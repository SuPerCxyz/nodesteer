import asyncio
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

SERVICE = 'nodesteer-agent-repair-20260930-101945-native-4.service'
EXPECTED_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
EXPECTED_BINARY_SHA256 = __EXPECTED_BINARY_SHA256__
EXPECTED_NODE_ID = __EXPECTED_NODE_ID__
EXPECTED_AGENT_ID = __EXPECTED_AGENT_ID__
NODE_NAME = __NODE_NAME__
NODE_IP = __NODE_IP__
GATEWAY_HOST = '192.168.100.209'
GATEWAY_PORT = 18080
PROXY_SOURCE = __PROXY_SOURCE_JSON__
NONROOT_USER = 'nobody'
OWNED_DIR = None
OWNED_DIR_IDENTITY = None
OWNED_MARKER = None
OWNED_PROCESS_PROOFS = []
SETUP_SUCCESS = False


class Blocked(Exception):
    def __init__(self, code):
        self.code = code


def emit(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            digest.update(chunk)
    return digest.hexdigest()


def proc_starttime(pid):
    raw = Path('/proc', str(pid), 'stat').read_text()
    fields = raw[raw.rfind(')') + 2:].split()
    return int(fields[19])


def proc_uid(pid):
    for line in Path('/proc', str(pid), 'status').read_text().splitlines():
        if line.startswith('Uid:'):
            return tuple(map(int, line.split()[1:5]))
    raise ValueError('proc_uid_missing')


def proc_argv(pid):
    return [value.decode(errors='replace') for value in Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0') if value]


def systemd_value(prop):
    result = subprocess.run(
        ['systemctl', 'show', SERVICE, '-p', prop, '--value'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, text=True, timeout=10,
    )
    if result.returncode != 0:
        raise Blocked('systemd_read_failed')
    return result.stdout.strip()


def parse_execstart(raw):
    match = re.search(r'argv\[\]=(.*?)(?=\s*;\s*[a-z_]+=|}\s*$)', raw, re.S)
    if match:
        try:
            argv = shlex.split(match.group(1))
        except ValueError:
            raise Blocked('execstart_argv_parse_failed')
    else:
        path_match = re.search(r'\bpath=([^ ;}]+)', raw)
        argv = [path_match.group(1)] if path_match else []
        config_match = re.search(r'--config(?:=|\s+)(?:"([^"]+)"|\'([^\']+)\'|([^ ;}]+))', raw)
        if config_match:
            argv.extend(['--config', next(value for value in config_match.groups() if value is not None)])
    if not argv or os.path.realpath(argv[0]) != os.path.realpath(EXPECTED_BINARY):
        raise Blocked('execstart_binary_mismatch')
    for index, arg in enumerate(argv):
        if arg == '--config' and index + 1 < len(argv):
            config_path = argv[index + 1]
            break
        if arg.startswith('--config='):
            config_path = arg.split('=', 1)[1]
            break
    else:
        raise Blocked('execstart_config_path_missing')
    if not os.path.isabs(config_path):
        raise Blocked('execstart_config_path_not_absolute')
    return config_path


def yaml_scalar(lines, key, required=True):
    rows = [line for line in lines if re.match(r'^\s*' + re.escape(key) + r'\s*:', line)]
    if len(rows) > 1 or (required and len(rows) != 1):
        raise Blocked('config_' + key + '_missing_or_duplicate')
    if not rows:
        return None
    scalar = rows[0].split(':', 1)[1].strip()
    if key == 'registration_token':
        if not (scalar.startswith('"') and scalar.endswith('"')):
            raise Blocked('registration_token_not_json_quoted')
        try:
            value = json.loads(scalar)
        except (TypeError, ValueError):
            raise Blocked('registration_token_json_parse_failed')
        if not isinstance(value, str) or not value:
            raise Blocked('registration_token_empty_or_non_string')
        return value
    if scalar.startswith('"'):
        try:
            value = json.loads(scalar)
        except (TypeError, ValueError):
            raise Blocked('config_' + key + '_parse_failed')
    elif scalar.startswith("'") and scalar.endswith("'") and len(scalar) >= 2:
        value = scalar[1:-1].replace("''", "'")
    else:
        value = scalar.split('#', 1)[0].strip()
    if not value and required:
        raise Blocked('config_' + key + '_empty')
    return value


def drop_to(uid, gid):
    def apply():
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    return apply


def verify_live_process(pid, expected_start, expected_uid, expected_exe_sha, owned_dir):
    try:
        if proc_starttime(pid) != expected_start:
            return False
        uids = proc_uid(pid)
        if uids[0] != expected_uid or uids[1] != expected_uid:
            return False
        exe = os.readlink('/proc/' + str(pid) + '/exe')
        if exe.endswith(' (deleted)') or sha256_file(exe) != expected_exe_sha:
            return False
        argv = proc_argv(pid)
        if not any(arg == str(owned_dir) or arg.startswith(str(owned_dir) + os.sep) for arg in argv):
            return False
        return True
    except (OSError, ValueError, IndexError):
        return False


def stop_exact(pid, start, uid, exe_sha, owned_dir, popen=None):
    if not verify_live_process(pid, start, uid, exe_sha, owned_dir):
        return not Path('/proc', str(pid)).exists()
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if not verify_live_process(pid, start, uid, exe_sha, owned_dir):
            if popen is not None:
                popen.poll()
            return True
        time.sleep(0.1)
    if not verify_live_process(pid, start, uid, exe_sha, owned_dir):
        return True
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and verify_live_process(pid, start, uid, exe_sha, owned_dir):
        time.sleep(0.1)
    return not verify_live_process(pid, start, uid, exe_sha, owned_dir)


def supervisor_source():
    return '''import asyncio,json,os,sys\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom ws_proxy import Proxy\nnode_ip=sys.argv[2]; upstream_host=sys.argv[3]; upstream_port=int(sys.argv[4]); controls=Path(sys.argv[5]); events=Path(sys.argv[6]); ready=Path(sys.argv[7])\nproxy=Proxy(controls,events,upstream_host,upstream_port)\nactive=False\nasync def handler(reader,writer):\n global active\n peer=writer.get_extra_info("peername")\n if not peer or not peer[0].startswith("127.") or active:\n  writer.close()\n  try: await writer.wait_closed()\n  except Exception: pass\n  return\n active=True\n try: await proxy.connection(reader,writer)\n finally: active=False\nasync def run():\n server=await asyncio.start_server(handler,node_ip,0)\n port=server.sockets[0].getsockname()[1]\n temp=ready.with_suffix(".tmp")\n temp.write_text(json.dumps({"port":port},separators=(",",":")))\n os.chmod(temp,0o600)\n temp.replace(ready)\n async with server: await server.serve_forever()\nasyncio.run(run())\n'''


def run_lowpriv_config_probe(config_path, uid, gid):
    source = (
        'import hashlib,json,re,stat,sys;from pathlib import Path;'
        'p=Path(sys.argv[1]);s=p.lstat();assert stat.S_ISREG(s.st_mode) and not p.is_symlink();'
        'assert stat.S_IMODE(s.st_mode)==0o600;lines=p.read_text().splitlines();'
        'defv=[x for x in lines if re.match(r"^\\s*registration_token\\s*:",x)];'
        'assert len(defv)==1 and defv[0].split(":",1)[1].strip().startswith(chr(34));'
        'v=json.loads(defv[0].split(":",1)[1].strip());assert isinstance(v,str) and v;'
        'assert any(re.match(r"^\\s*agent_id\\s*:",x) for x in lines);'
        'assert any(re.match(r"^\\s*node_name\\s*:",x) for x in lines);'
        'assert any(re.match(r"^\\s*node_ip\\s*:",x) for x in lines);'
        'print(json.dumps({"readable":True,"parseable":True,"mode":oct(stat.S_IMODE(s.st_mode)),"owner_uid":s.st_uid,"token_fingerprint":hashlib.sha256(v.encode()).hexdigest()[:16]},separators=(",",":")))'
    )
    result = subprocess.run(
        [sys.executable, '-c', source, config_path], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10,
        close_fds=True, preexec_fn=drop_to(uid, gid), env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent'},
    )
    if result.returncode != 0:
        raise Blocked('nonroot_child_config_read_parse_failed')
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise Blocked('nonroot_probe_output_invalid')
    if value.get('readable') is not True or value.get('parseable') is not True or value.get('owner_uid') != uid:
        raise Blocked('nonroot_child_config_access_assertion_failed')
    return value


def main():
    global OWNED_DIR, OWNED_DIR_IDENTITY, OWNED_MARKER, SETUP_SUCCESS
    if not re.fullmatch(r'[0-9a-f]{64}', EXPECTED_BINARY_SHA256):
        raise Blocked('expected_binary_sha_invalid')
    account = pwd.getpwnam(NONROOT_USER)
    uid, gid = account.pw_uid, account.pw_gid
    if uid == 0:
        raise Blocked('nonroot_uid_invalid')
    if os.geteuid() != 0:
        raise Blocked('launcher_not_root_for_owned_uid_setup')
    if not (NODE_NAME.startswith('qa-resume-20261008-native4-child-') and NODE_IP.startswith('127.') and EXPECTED_NODE_ID and EXPECTED_AGENT_ID):
        raise Blocked('new_identity_input_gate_failed')
    if Path(EXPECTED_BINARY).is_symlink() or not Path(EXPECTED_BINARY).is_file() or sha256_file(EXPECTED_BINARY) != EXPECTED_BINARY_SHA256:
        raise Blocked('native4_disk_binary_hash_gate_failed')
    service_active = systemd_value('ActiveState')
    main_pid_text = systemd_value('MainPID')
    execstart = systemd_value('ExecStart')
    config_path = parse_execstart(execstart)
    del execstart
    if service_active != 'active' or not main_pid_text.isdigit() or int(main_pid_text) <= 1:
        raise Blocked('native4_service_active_mainpid_gate_failed')
    native_pid = int(main_pid_text)
    native_exe = os.readlink('/proc/' + str(native_pid) + '/exe')
    if native_exe.endswith(' (deleted)') or os.path.realpath(native_exe) != os.path.realpath(EXPECTED_BINARY) or sha256_file(native_exe) != EXPECTED_BINARY_SHA256:
        raise Blocked('native4_running_binary_hash_gate_failed')
    cfg = Path(config_path)
    cfg_stat = cfg.lstat()
    if stat.S_ISLNK(cfg_stat.st_mode) or not stat.S_ISREG(cfg_stat.st_mode) or stat.S_IMODE(cfg_stat.st_mode) != 0o600:
        raise Blocked('native4_config_path_mode_gate_failed')
    cfg_lines = cfg.read_text().splitlines()
    registration_token = yaml_scalar(cfg_lines, 'registration_token')
    existing_hub_url = yaml_scalar(cfg_lines, 'hub_url')
    from urllib.parse import urlsplit
    parsed_hub = urlsplit(existing_hub_url)
    if parsed_hub.scheme != 'ws' or parsed_hub.hostname != GATEWAY_HOST or parsed_hub.port != GATEWAY_PORT or parsed_hub.username or parsed_hub.password or parsed_hub.query or parsed_hub.fragment:
        raise Blocked('native4_gateway_endpoint_drift')
    token_fingerprint = hashlib.sha256(registration_token.encode()).hexdigest()[:16]

    owned_dir = Path(tempfile.mkdtemp(prefix='qa-resume-20261008-child-'))
    owned_dir = owned_dir.resolve()
    dir_stat = owned_dir.lstat()
    marker = str(uuid.uuid4())
    OWNED_DIR = owned_dir
    OWNED_DIR_IDENTITY = (dir_stat.st_dev, dir_stat.st_ino)
    OWNED_MARKER = marker
    marker_path = owned_dir / 'owner.marker'
    marker_path.write_text(marker)
    os.chmod(marker_path, 0o600)
    binary_copy = owned_dir / 'nodesteer-agent'
    shutil.copyfile(EXPECTED_BINARY, binary_copy)
    if sha256_file(binary_copy) != EXPECTED_BINARY_SHA256:
        raise Blocked('owned_binary_copy_hash_mismatch')
    os.chmod(binary_copy, 0o500)
    proxy_path = owned_dir / 'ws_proxy.py'
    proxy_path.write_text(PROXY_SOURCE)
    os.chmod(proxy_path, 0o400)
    supervisor_path = owned_dir / 'supervisor.py'
    supervisor_path.write_text(supervisor_source())
    os.chmod(supervisor_path, 0o500)
    controls_path = owned_dir / 'controls.json'
    controls_path.write_text('{}')
    os.chmod(controls_path, 0o600)
    events_path = owned_dir / 'proxy-events.jsonl'
    events_path.write_text('')
    os.chmod(events_path, 0o600)
    data_dir = owned_dir / 'data'
    data_dir.mkdir(mode=0o700)
    os.chown(data_dir, uid, gid)
    os.chown(marker_path, uid, gid)
    os.chown(binary_copy, uid, gid)
    os.chown(proxy_path, uid, gid)
    os.chown(supervisor_path, uid, gid)
    os.chown(controls_path, uid, gid)
    os.chown(events_path, uid, gid)
    os.chown(owned_dir, uid, gid)
    os.chmod(owned_dir, 0o700)
    dir_stat = owned_dir.lstat()
    if dir_stat.st_uid != uid or stat.S_IMODE(dir_stat.st_mode) != 0o700:
        raise Blocked('owned_directory_owner_mode_gate_failed')

    ready_path = owned_dir / 'supervisor.ready.json'
    supervisor_log = owned_dir / 'supervisor.log'
    with supervisor_log.open('ab') as log_stream:
        supervisor = subprocess.Popen(
            [sys.executable, str(supervisor_path), str(owned_dir), NODE_IP, GATEWAY_HOST, str(GATEWAY_PORT), str(controls_path), str(events_path), str(ready_path)],
            cwd=str(owned_dir), stdin=subprocess.DEVNULL, stdout=log_stream, stderr=log_stream,
            close_fds=True, start_new_session=True,
            preexec_fn=drop_to(uid, gid), env={'PATH': '/usr/bin:/bin', 'HOME': str(owned_dir)},
        )
    supervisor_start = proc_starttime(supervisor.pid)
    supervisor_exe = os.readlink('/proc/' + str(supervisor.pid) + '/exe')
    supervisor_sha = sha256_file(supervisor_exe)
    OWNED_PROCESS_PROOFS.append((supervisor.pid, supervisor_start, uid, supervisor_sha, owned_dir))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready_path.exists():
        if supervisor.poll() is not None:
            raise Blocked('supervisor_exited_before_ready')
        time.sleep(0.1)
    if not ready_path.is_file() or ready_path.is_symlink():
        raise Blocked('supervisor_ready_timeout')
    ready = json.loads(ready_path.read_text())
    port = int(ready.get('port', 0))
    if not 1 <= port <= 65535:
        raise Blocked('proxy_ephemeral_port_invalid')
    child_hub_url = 'ws://' + NODE_IP + ':' + str(port) + '/'
    child_config = owned_dir / 'child.yaml'
    child_lines = [
        'hub_url: ' + json.dumps(child_hub_url),
        'registration_token: ' + json.dumps(registration_token),
        'agent_id: ' + json.dumps(EXPECTED_AGENT_ID),
        'node_name: ' + json.dumps(NODE_NAME),
        'node_ip: ' + json.dumps(NODE_IP),
        'deployment_mode: "native"',
        'host_integration: false',
        'data_dir: ' + json.dumps(str(data_dir)),
    ]
    child_config.write_text('\n'.join(child_lines) + '\n')
    os.chmod(child_config, 0o600)
    os.chown(child_config, uid, gid)
    lowpriv = run_lowpriv_config_probe(str(child_config), uid, gid)
    if lowpriv.get('token_fingerprint') != token_fingerprint:
        raise Blocked('nonroot_child_config_fingerprint_mismatch')
    del registration_token, cfg_lines, child_lines, existing_hub_url

    child_log = owned_dir / 'agent.log'
    with child_log.open('ab') as log_stream:
        child = subprocess.Popen(
            [str(binary_copy), '--config', str(child_config)], cwd=str(owned_dir),
            stdin=subprocess.DEVNULL, stdout=log_stream, stderr=log_stream,
            close_fds=True, start_new_session=True,
            preexec_fn=drop_to(uid, gid), env={'PATH': '/usr/bin:/bin', 'HOME': str(owned_dir)},
        )
    child_start = proc_starttime(child.pid)
    child_exe = os.readlink('/proc/' + str(child.pid) + '/exe')
    child_sha = sha256_file(child_exe)
    OWNED_PROCESS_PROOFS.append((child.pid, child_start, uid, child_sha, owned_dir))
    time.sleep(1.0)
    if child.poll() is not None or child_sha != EXPECTED_BINARY_SHA256:
        raise Blocked('child_agent_did_not_remain_running')
    if proc_uid(child.pid)[1] != uid or proc_uid(supervisor.pid)[1] != uid:
        raise Blocked('child_or_supervisor_not_nonroot')
    child_argv = proc_argv(child.pid)
    supervisor_argv = proc_argv(supervisor.pid)
    if not any(arg == str(owned_dir) or arg.startswith(str(owned_dir) + os.sep) for arg in child_argv):
        raise Blocked('child_argv_owner_path_mismatch')
    if not any(arg == str(owned_dir) or arg.startswith(str(owned_dir) + os.sep) for arg in supervisor_argv):
        raise Blocked('supervisor_argv_owner_path_mismatch')

    owner = {
        'schema_version': 1,
        'owner_marker': marker,
        'directory': str(owned_dir),
        'directory_dev': dir_stat.st_dev,
        'directory_ino': dir_stat.st_ino,
        'directory_uid': uid,
        'directory_mode': oct(stat.S_IMODE(dir_stat.st_mode)),
        'node_id': EXPECTED_NODE_ID,
        'agent_id': EXPECTED_AGENT_ID,
        'registration_token_fingerprint': token_fingerprint,
        'hub_endpoint': {'scheme': parsed_hub.scheme, 'host': parsed_hub.hostname, 'port': parsed_hub.port, 'path': parsed_hub.path or '/'},
        'proxy': {'bind_ip': NODE_IP, 'port': port, 'upstream_host': GATEWAY_HOST, 'upstream_port': GATEWAY_PORT, 'source_sha256': hashlib.sha256(PROXY_SOURCE.encode()).hexdigest()},
        'child': {'pid': child.pid, 'starttime': child_start, 'uid': uid, 'exe_path': str(binary_copy), 'exe_sha256': child_sha, 'argv_owner_path_match': True},
        'supervisor': {'pid': supervisor.pid, 'starttime': supervisor_start, 'uid': uid, 'exe_path': supervisor_exe, 'exe_sha256': supervisor_sha, 'argv_owner_path_match': True},
        'config': {'path': str(child_config), 'mode': oct(stat.S_IMODE(child_config.stat().st_mode)), 'uid': child_config.stat().st_uid, 'readable_parseable_as_nobody': True, 'token_fingerprint': token_fingerprint},
        'binary': {'source_path': EXPECTED_BINARY, 'source_sha256': EXPECTED_BINARY_SHA256, 'copy_path': str(binary_copy), 'copy_sha256': sha256_file(binary_copy)},
        'created_at_epoch': time.time(),
    }
    manifest_path = owned_dir / 'owner-manifest.json'
    manifest_path.write_text(json.dumps(owner, separators=(',', ':')))
    os.chmod(manifest_path, 0o600)
    os.chown(manifest_path, uid, gid)
    manifest_sha = sha256_file(manifest_path)
    write_ok = {
        'status': 'PASS',
        'node_id': EXPECTED_NODE_ID,
        'agent_id': EXPECTED_AGENT_ID,
        'node_name': NODE_NAME,
        'node_ip': NODE_IP,
        'owned_directory': str(owned_dir),
        'directory_dev': dir_stat.st_dev,
        'directory_ino': dir_stat.st_ino,
        'directory_uid': uid,
        'directory_mode': oct(stat.S_IMODE(dir_stat.st_mode)),
        'child_pid': child.pid,
        'child_starttime': child_start,
        'child_uid': uid,
        'child_exe_sha256': child_sha,
        'supervisor_pid': supervisor.pid,
        'supervisor_starttime': supervisor_start,
        'supervisor_uid': uid,
        'supervisor_exe_sha256': supervisor_sha,
        'proxy_bind_ip': NODE_IP,
        'proxy_ephemeral_port': port,
        'proxy_upstream': {'host': GATEWAY_HOST, 'port': GATEWAY_PORT},
        'child_config_mode': '0o600',
        'child_config_uid': uid,
        'env03_nobody_can_read_and_parse_child_config': True,
        'registration_token_fingerprint': token_fingerprint,
        'owner_manifest_sha256': manifest_sha,
        'proxy_source_sha256': hashlib.sha256(PROXY_SOURCE.encode()).hexdigest(),
        'child_binary_sha256': child_sha,
        'supervisor_independent_session': True,
        'child_independent_session': True,
        'secret_values_emitted': False,
        'primary_8080_accessed': False,
    }
    SETUP_SUCCESS = True
    emit(write_ok)


def safe_abort():
    if OWNED_DIR is None:
        return {'owned_processes_stopped': True, 'owned_directory_removed': True}
    stopped = True
    for pid, start, uid, exe_sha, directory in reversed(OWNED_PROCESS_PROOFS):
        if not stop_exact(pid, start, uid, exe_sha, directory):
            stopped = False
    removed = False
    try:
        current = OWNED_DIR.lstat()
        marker_path = OWNED_DIR / 'owner.marker'
        marker_ok = marker_path.is_file() and not marker_path.is_symlink() and marker_path.read_text() == OWNED_MARKER
        identity_ok = (current.st_dev, current.st_ino) == OWNED_DIR_IDENTITY and not stat.S_ISLNK(current.st_mode)
        processes_gone = all(not Path('/proc', str(pid)).exists() for pid, *_ in OWNED_PROCESS_PROOFS)
        if stopped and processes_gone and identity_ok and marker_ok:
            shutil.rmtree(OWNED_DIR)
            removed = not OWNED_DIR.exists()
    except OSError:
        removed = False
    return {'owned_processes_stopped': stopped, 'owned_directory_removed': removed, 'owned_directory': str(OWNED_DIR)}


try:
    main()
except Blocked as error:
    cleanup = safe_abort()
    emit({'status': 'BLOCKED', 'code': error.code, 'secret_values_emitted': False, 'failure_cleanup': cleanup})
    raise SystemExit(0)
except BaseException:
    cleanup = safe_abort()
    emit({'status': 'BLOCKED', 'code': 'setup_internal_failure', 'secret_values_emitted': False, 'failure_cleanup': cleanup})
    raise SystemExit(0)
