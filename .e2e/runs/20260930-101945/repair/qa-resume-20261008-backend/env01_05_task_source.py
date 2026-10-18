import hashlib
import importlib
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

SERVICE = 'nodesteer-agent-repair-20260930-101945-native-4.service'
EXPECTED_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
EXPECTED_BINARY_SHA256 = '__EXPECTED_BINARY_SHA256__'


class GateFailure(Exception):
    def __init__(self, code):
        self.code = code


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def proc_starttime(pid):
    raw = Path('/proc', str(pid), 'stat').read_text()
    tail = raw[raw.rfind(')') + 2:].split()
    if len(tail) < 20:
        raise GateFailure('proc_stat_unreadable')
    return int(tail[19])


def proc_uid(pid):
    for line in Path('/proc', str(pid), 'status').read_text().splitlines():
        if line.startswith('Uid:'):
            fields = line.split()[1:]
            if len(fields) >= 2:
                return int(fields[0]), int(fields[1])
    raise GateFailure('proc_uid_unreadable')


def proc_argv_contains(pid, exact_value):
    raw = Path('/proc', str(pid), 'cmdline').read_bytes()
    argv = [part.decode(errors='replace') for part in raw.split(b'\0') if part]
    return any(arg == exact_value or arg.startswith(exact_value + os.sep) for arg in argv)


def systemd_value(prop):
    result = subprocess.run(
        ['systemctl', 'show', SERVICE, '-p', prop, '--value'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise GateFailure('systemctl_read_failed')
    return result.stdout.strip()


def parse_execstart(raw):
    match = re.search(r'argv\[\]=(.*?)(?=\s*;\s*[a-z_]+=|}\s*$)', raw, re.S)
    if match:
        try:
            argv = shlex.split(match.group(1))
        except ValueError:
            raise GateFailure('execstart_argv_parse_failed')
    else:
        path_match = re.search(r'\bpath=([^ ;}]+)', raw)
        argv = [path_match.group(1)] if path_match else []
        if '--config' in raw:
            config_match = re.search(r'--config(?:=|\s+)(?:"([^"]+)"|\'([^\']+)\'|([^ ;}]+))', raw)
            if config_match:
                argv.extend(['--config', next(v for v in config_match.groups() if v is not None)])
    if not argv or os.path.realpath(argv[0]) != os.path.realpath(EXPECTED_BINARY):
        raise GateFailure('execstart_binary_mismatch')
    config_path = None
    for index, arg in enumerate(argv):
        if arg == '--config' and index + 1 < len(argv):
            config_path = argv[index + 1]
            break
        if arg.startswith('--config='):
            config_path = arg.split('=', 1)[1]
            break
    if not config_path or not os.path.isabs(config_path):
        raise GateFailure('execstart_config_path_missing')
    return config_path


def parse_yaml_scalar(lines, key, required=True):
    rows = [line for line in lines if re.match(r'^\s*' + re.escape(key) + r'\s*:', line)]
    if not rows:
        if required:
            raise GateFailure('config_' + key + '_missing')
        return None
    if len(rows) != 1:
        raise GateFailure('config_' + key + '_duplicate')
    scalar = rows[0].split(':', 1)[1].strip()
    if key == 'registration_token':
        if not (scalar.startswith('"') and scalar.endswith('"')):
            raise GateFailure('registration_token_not_json_quoted')
        try:
            value = json.loads(scalar)
        except (TypeError, ValueError):
            raise GateFailure('registration_token_json_parse_failed')
        if not isinstance(value, str) or not value:
            raise GateFailure('registration_token_empty_or_non_string')
        return value
    if scalar.startswith('"'):
        try:
            value = json.loads(scalar)
        except (TypeError, ValueError):
            raise GateFailure('config_' + key + '_parse_failed')
    elif scalar.startswith("'") and scalar.endswith("'") and len(scalar) >= 2:
        value = scalar[1:-1].replace("''", "'")
    else:
        value = scalar.split('#', 1)[0].strip()
    if not value:
        if required:
            raise GateFailure('config_' + key + '_empty')
        return None
    return value


def safe_remove_tree(path, expected_dev, expected_ino):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        return False
    if info.st_dev != expected_dev or info.st_ino != expected_ino:
        return False
    shutil.rmtree(path)
    return not path.exists()


def process_matches(pid, starttime, uid, exe_sha, owned_dir):
    try:
        if proc_starttime(pid) != starttime:
            return False
        real_uid, effective_uid = proc_uid(pid)
        if real_uid != uid or effective_uid != uid:
            return False
        exe = os.readlink('/proc/' + str(pid) + '/exe')
        if exe.endswith(' (deleted)') or sha256_file(exe) != exe_sha:
            return False
        if not proc_argv_contains(pid, owned_dir):
            return False
        return True
    except (OSError, GateFailure, ValueError):
        return False


def terminate_owned_child(pid, starttime, uid, exe_sha, owned_dir):
    if not process_matches(pid, starttime, uid, exe_sha, owned_dir):
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        if not process_matches(pid, starttime, uid, exe_sha, owned_dir):
            return True
        time.sleep(0.1)
    if process_matches(pid, starttime, uid, exe_sha, owned_dir):
        os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if not process_matches(pid, starttime, uid, exe_sha, owned_dir):
            return True
        time.sleep(0.1)
    return not process_matches(pid, starttime, uid, exe_sha, owned_dir)


def emit(value):
    sys.stdout.write(json.dumps(value, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def main():
    probe_dir = None
    probe_identity = None
    daemon_dir = None
    daemon_identity = None
    daemon = None
    result = {
        'phase': 'ENV-01-05',
        'status': 'BLOCKED',
        'checks': {},
        'credentials_printed': False,
        'agent_started': False,
        'existing_service_mutated': False,
    }
    try:
        required_modules = ['asyncio', 'json', 'pathlib', 'struct', 'time', 'socket', 'sqlite3', 'subprocess', 'os', 'pwd', 'hashlib']
        missing = []
        for name in required_modules:
            try:
                importlib.import_module(name)
            except ImportError:
                missing.append(name)
        result['checks']['python_version'] = list(sys.version_info[:3])
        result['checks']['python_executable'] = os.path.realpath(sys.executable)
        result['checks']['stdlib_modules_ok'] = not missing
        result['checks']['missing_modules'] = missing
        if sys.version_info < (3, 8) or missing:
            raise GateFailure('python_or_stdlib_gate_failed')

        result['checks']['launcher_euid'] = os.geteuid()
        if os.geteuid() != 0:
            raise GateFailure('launcher_task_not_root_for_safe_uid_probe')
        service_exec = systemd_value('ExecStart')
        config_path = parse_execstart(service_exec)
        del service_exec
        active_state = systemd_value('ActiveState')
        if active_state != 'active':
            raise GateFailure('repair_service_not_active')
        main_pid_raw = systemd_value('MainPID')
        try:
            main_pid = int(main_pid_raw)
        except ValueError:
            raise GateFailure('repair_mainpid_invalid')
        del main_pid_raw
        if main_pid <= 1 or not Path('/proc', str(main_pid)).exists():
            raise GateFailure('repair_mainpid_not_running')
        result['checks']['service_main_pid'] = main_pid
        running_exe = os.readlink('/proc/' + str(main_pid) + '/exe')
        if running_exe.endswith(' (deleted)') or os.path.realpath(running_exe) != os.path.realpath(EXPECTED_BINARY):
            raise GateFailure('repair_running_exe_mismatch')
        running_sha = sha256_file(running_exe)
        disk_sha = sha256_file(EXPECTED_BINARY)
        result['checks']['service_exact'] = True
        result['checks']['execstart_config_path'] = config_path
        result['checks']['running_exe_path'] = os.path.realpath(running_exe)
        result['checks']['expected_binary_path'] = EXPECTED_BINARY
        result['checks']['expected_binary_sha256'] = EXPECTED_BINARY_SHA256
        result['checks']['expected_binary_sha256_length'] = len(EXPECTED_BINARY_SHA256)
        result['checks']['sha256_method'] = 'SHA-256 streamed in 1 MiB reads'
        result['checks']['running_binary_sha256'] = running_sha
        result['checks']['disk_binary_sha256'] = disk_sha
        result['checks']['running_binary_sha_matches'] = running_sha == EXPECTED_BINARY_SHA256
        result['checks']['disk_binary_sha_matches'] = disk_sha == EXPECTED_BINARY_SHA256
        if running_sha != EXPECTED_BINARY_SHA256 or disk_sha != EXPECTED_BINARY_SHA256:
            raise GateFailure('repair_binary_sha_mismatch')

        cfg = Path(config_path)
        cfg_stat = cfg.lstat()
        if stat.S_ISLNK(cfg_stat.st_mode) or not stat.S_ISREG(cfg_stat.st_mode):
            raise GateFailure('repair_config_not_regular_or_is_symlink')
        config_mode = stat.S_IMODE(cfg_stat.st_mode)
        result['checks']['config_mode'] = oct(config_mode)
        if config_mode != 0o600:
            raise GateFailure('repair_config_mode_not_0600')
        config_text = cfg.read_text()
        lines = config_text.splitlines()
        data_dir = parse_yaml_scalar(lines, 'data_dir', required=False) or '/var/lib/nodesteer'
        token_value = parse_yaml_scalar(lines, 'registration_token', required=True)
        token_fingerprint = hashlib.sha256(token_value.encode()).hexdigest()[:16]
        token_value = None
        del token_value
        db_path = Path(data_dir) / 'state.db'
        if not Path(data_dir).is_absolute() or db_path.is_symlink() or not db_path.is_file():
            raise GateFailure('agent_state_database_unavailable')
        db_uri = 'file:' + urllib.parse.quote(str(db_path), safe='/') + '?mode=ro'
        connection = sqlite3.connect(db_uri, uri=True, timeout=2)
        try:
            table_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            connection.close()
        if 'identity' not in table_names:
            raise GateFailure('agent_state_database_schema_missing_identity')
        del config_text, lines
        result['checks']['config_path_is_exact_and_read_only'] = True
        result['checks']['config_mode'] = oct(config_mode)
        result['checks']['config_owner_uid'] = cfg_stat.st_uid
        result['checks']['registration_token_format'] = 'valid_json_double_quoted_string'
        result['checks']['registration_token_fingerprint'] = token_fingerprint
        result['checks']['sqlite_read_only'] = True
        result['checks']['sqlite_identity_table_present'] = True

        nobody = pwd.getpwnam('nobody')
        if nobody.pw_uid == 0:
            raise GateFailure('nonroot_probe_account_is_root')
        result['checks']['nonroot_user_exists'] = True
        result['checks']['nonroot_uid'] = nobody.pw_uid
        result['checks']['nonroot_gid'] = nobody.pw_gid

        probe_dir = tempfile.mkdtemp(prefix='qa-resume-20261008-probe-', dir='/tmp')
        os.chmod(probe_dir, 0o700)
        probe_info = os.lstat(probe_dir)
        probe_identity = (probe_info.st_dev, probe_info.st_ino)
        probe = Path(probe_dir)
        binary_copy = probe / 'repair-agent-copy'
        shutil.copyfile(EXPECTED_BINARY, binary_copy)
        os.chmod(binary_copy, 0o700)
        dummy_config = probe / 'probe-config.yaml'
        dummy_config.write_text('hub_url: "http://127.0.0.1:1"\ndata_dir: "' + str(probe / 'data') + '"\n')
        os.chmod(dummy_config, 0o600)
        data_probe = probe / 'data'
        data_probe.mkdir(mode=0o700)
        for path in (probe, binary_copy, dummy_config, data_probe):
            os.chown(path, nobody.pw_uid, nobody.pw_gid)
        result_file = probe / 'write-probe'
        probe_code = (
            'import os,sys,pathlib;'
            'exe,cfg,data,uid=sys.argv[1:];'
            'assert os.geteuid()==int(uid);'
            'assert os.access(exe,os.R_OK|os.X_OK);'
            'assert "hub_url" in pathlib.Path(cfg).read_text();'
            'pathlib.Path(data,"write-probe").write_text("ok");'
            'assert pathlib.Path(data,"write-probe").read_text()=="ok";'
            'print("PROBE_OK")'
        )
        def drop_to_nobody():
            os.setgroups([nobody.pw_gid])
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)
        probe_run = subprocess.run(
            [sys.executable, '-c', probe_code, str(binary_copy), str(dummy_config), str(data_probe), str(nobody.pw_uid)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, check=False, text=True, timeout=10, preexec_fn=drop_to_nobody,
        )
        result['checks']['nonroot_can_read_copied_binary_config_and_write_owned_data'] = probe_run.returncode == 0 and probe_run.stdout.strip() == 'PROBE_OK'
        if not result['checks']['nonroot_can_read_copied_binary_config_and_write_owned_data']:
            raise GateFailure('nonroot_copy_permission_probe_failed')
        if not safe_remove_tree(probe_dir, *probe_identity):
            raise GateFailure('probe_directory_cleanup_guard_failed')
        probe_dir = None
        probe_identity = None

        daemon_dir = tempfile.mkdtemp(prefix='qa-resume-20261008-child-', dir='/tmp')
        os.chmod(daemon_dir, 0o700)
        daemon_info = os.lstat(daemon_dir)
        daemon_identity = (daemon_info.st_dev, daemon_info.st_ino)
        owner_nonce = os.urandom(16).hex()
        owner_marker = Path(daemon_dir) / 'owner.marker'
        owner_marker.write_text(owner_nonce)
        os.chmod(owner_marker, 0o600)
        heartbeat = Path(daemon_dir) / 'heartbeat'
        daemon_source = Path(daemon_dir) / 'daemon.py'
        daemon_source.write_text(
            'import os,sys,time,pathlib\n'
            'os.umask(0o077)\n'
            'p=pathlib.Path(sys.argv[1])\n'
            'while True:\n'
            ' p.write_text(str(time.time()))\n'
            ' time.sleep(0.2)\n'
        )
        os.chmod(daemon_source, 0o700)
        daemon_log = Path(daemon_dir) / 'daemon.log'
        daemon_log.touch(mode=0o600, exist_ok=True)
        os.chmod(daemon_log, 0o600)
        for path in (daemon_dir, owner_marker, daemon_source, daemon_log):
            os.chown(path, nobody.pw_uid, nobody.pw_gid)
        def drop_daemon():
            os.setgroups([nobody.pw_gid])
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)
        with daemon_log.open('ab', buffering=0) as logfile:
            daemon = subprocess.Popen(
                [sys.executable, str(daemon_source), str(heartbeat)],
                cwd=daemon_dir, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
                close_fds=True, start_new_session=True, preexec_fn=drop_daemon,
            )
        time.sleep(0.5)
        daemon_pid = daemon.pid
        daemon_start = proc_starttime(daemon_pid)
        daemon_real_uid, daemon_effective_uid = proc_uid(daemon_pid)
        daemon_exe = os.readlink('/proc/' + str(daemon_pid) + '/exe')
        daemon_sha = sha256_file(daemon_exe)
        daemon_owned_argv = proc_argv_contains(daemon_pid, daemon_dir)
        heartbeat_stat = heartbeat.stat()
        if daemon_real_uid != nobody.pw_uid or daemon_effective_uid != nobody.pw_uid or not daemon_owned_argv:
            raise GateFailure('owned_daemon_identity_gate_failed')
        if time.time() - heartbeat_stat.st_mtime > 2:
            raise GateFailure('owned_daemon_heartbeat_missing')
        owner_digest = hashlib.sha256(owner_nonce.encode()).hexdigest()[:16]
        manifest = {
            'owner_nonce': owner_nonce,
            'owner_marker_sha256_prefix': owner_digest,
            'directory': daemon_dir,
            'directory_dev': daemon_identity[0],
            'directory_ino': daemon_identity[1],
            'uid': nobody.pw_uid,
            'gid': nobody.pw_gid,
            'pid': daemon_pid,
            'starttime': daemon_start,
            'exe_sha256': daemon_sha,
        }
        manifest_path = Path(daemon_dir) / 'owner-manifest.json'
        manifest_path.write_text(json.dumps(manifest, separators=(',', ':')))
        os.chmod(manifest_path, 0o600)
        os.chown(manifest_path, nobody.pw_uid, nobody.pw_gid)
        manifest_sha256 = sha256_file(manifest_path)
        result['daemon'] = {
            'survives_initial_task_exit_test_pending': True,
            'directory': daemon_dir,
            'directory_dev': daemon_identity[0],
            'directory_ino': daemon_identity[1],
            'directory_mode': oct(stat.S_IMODE(os.stat(daemon_dir).st_mode)),
            'owner_marker_sha256_prefix': owner_digest,
            'manifest_sha256': manifest_sha256,
            'pid': daemon_pid,
            'starttime': daemon_start,
            'uid': daemon_effective_uid,
            'exe_path': os.path.realpath(daemon_exe),
            'exe_sha256': daemon_sha,
            'argv_owns_exact_directory': daemon_owned_argv,
            'heartbeat_present': heartbeat_stat.st_size > 0,
            'stdin_devnull': True,
            'stdio_private_file': True,
            'close_fds': True,
            'start_new_session': os.getsid(daemon_pid) == daemon_pid,
        }
        result['status'] = 'PASS'
    except GateFailure as failure:
        result['status'] = 'BLOCKED'
        result['block_code'] = failure.code
    except BaseException:
        result['status'] = 'BLOCKED'
        result['block_code'] = 'internal_preflight_exception'
    finally:
        if probe_dir is not None and probe_identity is not None:
            safe_remove_tree(probe_dir, *probe_identity)
        if daemon is not None and result['status'] != 'PASS' and daemon_dir is not None:
            if process_matches(daemon.pid, proc_starttime(daemon.pid), nobody.pw_uid, sha256_file(os.readlink('/proc/' + str(daemon.pid) + '/exe')), daemon_dir):
                terminate_owned_child(daemon.pid, proc_starttime(daemon.pid), nobody.pw_uid, sha256_file(os.readlink('/proc/' + str(daemon.pid) + '/exe')), daemon_dir)
            if daemon_identity is not None:
                safe_remove_tree(daemon_dir, *daemon_identity)
    emit(result)


if __name__ == '__main__':
    main()
