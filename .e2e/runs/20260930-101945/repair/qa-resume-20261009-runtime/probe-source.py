import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time


def run(args, timeout=5):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return {'rc': p.returncode, 'out': p.stdout.strip()}
    except Exception as exc:
        return {'rc': None, 'out': type(exc).__name__}


def fingerprint(pid):
    result = {'pid': pid}
    if pid <= 0:
        return result
    try:
        raw = open(f'/proc/{pid}/stat').read()
        rest = raw[raw.rfind(')') + 2:].split()
        result['starttime_ticks'] = int(rest[19])
        status = open(f'/proc/{pid}/status').read()
        m = re.search(r'^Uid:\s+(\d+)', status, re.M)
        result['uid'] = int(m.group(1)) if m else None
        result['exe_path'] = os.readlink(f'/proc/{pid}/exe')
        h = hashlib.sha256()
        with open(f'/proc/{pid}/exe', 'rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        result['exe_sha256'] = h.hexdigest()
        args = open(f'/proc/{pid}/cmdline', 'rb').read().split(b'\0')
        args = [a.decode(errors='replace') for a in args if a]
        safe = []
        redact_next = False
        for arg in args:
            if redact_next:
                safe.append('[redacted]')
                redact_next = False
                continue
            if re.search(r'(token|password|secret|credential|private.?key)', arg, re.I):
                if '=' in arg:
                    safe.append(arg.split('=', 1)[0] + '=[redacted]')
                else:
                    safe.append(arg)
                    redact_next = True
            elif arg.lower().startswith('bearer '):
                safe.append('Bearer [redacted]')
            else:
                safe.append(arg)
        result['argv'] = safe
    except Exception as exc:
        result['fingerprint_error'] = type(exc).__name__
    return result


service = run(['systemctl', 'show', 'nodesteer-agent.service',
               '-p', 'MainPID', '-p', 'User', '-p', 'FragmentPath',
               '-p', 'ActiveState', '-p', 'SubState', '-p', 'ExecStart'])
props = {}
for line in service['out'].splitlines():
    if '=' in line:
        k, v = line.split('=', 1)
        props[k] = v
main_pid = int(props.get('MainPID', '0') or 0)
start = props.get('ExecStart', '')
match = re.search(r'path=([^ ;]+)', start)
exec_path = match.group(1) if match else None

candidates = []
for path in ['/tmp', '/var/tmp']:
    try:
        st = os.stat(path)
        candidates.append({'path': path, 'exists': True, 'uid': st.st_uid,
                           'gid': st.st_gid, 'mode': oct(st.st_mode & 0o7777)})
    except Exception as exc:
        candidates.append({'path': path, 'exists': False,
                           'error': type(exc).__name__})
qa_root = '/tmp/nodesteer-e2e-20261009-P0-002-native1'
try:
    st = os.stat(qa_root)
    candidates.append({'path': qa_root, 'exists': True, 'uid': st.st_uid,
                       'gid': st.st_gid, 'mode': oct(st.st_mode & 0o7777)})
except FileNotFoundError:
    candidates.append({'path': qa_root, 'exists': False})
except Exception as exc:
    candidates.append({'path': qa_root, 'exists': None,
                       'error': type(exc).__name__})

tools = {name: shutil.which(name) for name in
         ['systemctl', 'systemd-run', 'nsenter', 'docker', 'python3', 'sha256sum']}
docker = run(['docker', 'version', '--format', '{{.Server.Version}}']) if tools['docker'] else {'rc': None, 'out': 'binary-unavailable'}
systemd = run(['systemctl', 'is-system-running']) if tools['systemctl'] else {'rc': None, 'out': 'binary-unavailable'}

print(json.dumps({
    'captured_unix_time': int(time.time()),
    'host': {'hostname': run(['hostname'])['out'], 'os': platform.system(),
             'release': platform.release(), 'architecture': platform.machine(),
             'uid': os.getuid(), 'gid': os.getgid()},
    'original_agent_service': {
        'unit': 'nodesteer-agent.service', 'systemctl_rc': service['rc'],
        'active_state': props.get('ActiveState'), 'sub_state': props.get('SubState'),
        'configured_user': props.get('User'), 'fragment_path': props.get('FragmentPath'),
        'exec_start_path': exec_path,
        'main_process': fingerprint(main_pid),
    },
    'host_capabilities': {
        'tools': tools, 'systemd_state': systemd,
        'docker_server_version': docker,
        'nsenter_version': run(['nsenter', '--version']) if tools['nsenter'] else {'rc': None, 'out': 'binary-unavailable'},
    },
    'qa_root_candidates': candidates,
    'proposed_unit_name_pattern': 'nodesteer-qa-e2e-20261009-p0-002-native1-<unique-id>.service',
}, sort_keys=True))
