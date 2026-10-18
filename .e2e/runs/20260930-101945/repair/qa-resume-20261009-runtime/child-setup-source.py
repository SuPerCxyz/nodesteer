import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import time
from urllib.parse import urlsplit

ROOT = '/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee'
NONCE = '870358cd92ee'
CHILD_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service'
PARENT_UNIT = 'nodesteer-agent-repair-20260930-101945-native-1.service'
BASELINE_UNIT = 'nodesteer-agent.service'
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
NODE_NAME = 'qa-resume-20261009-native1-p0-002-child-870358cd92ee'
NODE_IP = 'qa-resume-20261009-native1-p0-002-870358cd92ee.example.test'


def run(args, env=None):
    return subprocess.run(args, text=True, capture_output=True, timeout=12, env=env)


def unit_props(unit):
    p = run(['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState',
             '-p', 'SubState', '-p', 'MainPID', '-p', 'ControlGroup'])
    result = {'rc': p.returncode}
    for line in p.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            result[k] = v
    return result


def process(pid):
    if pid <= 0:
        return None
    raw = open(f'/proc/{pid}/stat').read()
    rest = raw[raw.rfind(')') + 2:].split()
    status = open(f'/proc/{pid}/status').read()
    uid = re.search(r'^Uid:\s+(\d+)', status, re.M)
    argv_raw = open(f'/proc/{pid}/cmdline', 'rb').read()
    argv = [p.decode(errors='replace') for p in argv_raw.split(b'\0') if p]
    h = hashlib.sha256()
    with open(f'/proc/{pid}/exe', 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {
        'pid': pid,
        'starttime_ticks': int(rest[19]),
        'uid': int(uid.group(1)) if uid else None,
        'argv': argv,
        'argv_sha256': hashlib.sha256(argv_raw).hexdigest(),
        'exe_path': os.readlink(f'/proc/{pid}/exe'),
        'exe_sha256': h.hexdigest(),
        'cgroup': open(f'/proc/{pid}/cgroup').read().strip().splitlines(),
    }


def mount_options(path):
    best = None
    best_len = -1
    for line in open('/proc/mounts'):
        fields = line.split()
        if len(fields) >= 4:
            mountpoint = fields[1].replace('\\040', ' ')
            if (path == mountpoint or path.startswith(mountpoint.rstrip('/') + '/')) and len(mountpoint) > best_len:
                best = {'mountpoint': mountpoint, 'options': fields[3].split(',')}
                best_len = len(mountpoint)
    return best


env = os.environ.copy()
params = {}
for key in ['agent_id', 'node_id', 'node_name', 'node_ip', 'hub_url', 'registration_token']:
    env_key = 'NODESTEER_PARAM_' + key
    if not env.get(env_key):
        raise SystemExit('blocked: required setup parameter missing')
    params[key] = env[env_key]
gateway = urlsplit(params['hub_url'])
if gateway.scheme not in {'ws', 'wss'} or gateway.hostname != '192.168.100.209':
    raise SystemExit('blocked: child gateway is not the exact enrollment-returned repair gateway')
if params['node_name'] != NODE_NAME or params['node_ip'] != NODE_IP:
    raise SystemExit('blocked: unique enrollment identity mismatch')

root = pathlib.Path(ROOT)
if root.exists():
    raise SystemExit('blocked: unique QA root already exists')
parent = root.parent.stat()
if parent.st_uid != 0 or (parent.st_mode & stat.S_ISVTX) == 0:
    raise SystemExit('blocked: /tmp ownership/sticky-bit precondition changed')
if not pathlib.Path(AGENT_BIN).is_file():
    raise SystemExit('blocked: repair Agent binary path missing')
h = hashlib.sha256(pathlib.Path(AGENT_BIN).read_bytes()).hexdigest()
if h != AGENT_SHA:
    raise SystemExit('blocked: repair Agent binary hash does not match approved manifest')

for unit in [CHILD_UNIT]:
    props = unit_props(unit)
    if props.get('LoadState') not in {None, '', 'not-found'}:
        raise SystemExit('blocked: unique transient child unit already exists')

parent_props = unit_props(PARENT_UNIT)
parent_pid = int(parent_props.get('MainPID', '0') or 0)
parent_process = process(parent_pid)
if parent_props.get('ActiveState') != 'active' or not parent_process or parent_process['exe_sha256'] != AGENT_SHA:
    raise SystemExit('blocked: approved Native-1 repair parent is not active at expected repair SHA')
baseline_props = unit_props(BASELINE_UNIT)
if baseline_props.get('ActiveState') != 'active' or int(baseline_props.get('MainPID', '0') or 0) <= 0:
    raise SystemExit('blocked: preserved baseline Agent service is not active')

root_mount = mount_options(str(root))
if not root_mount or 'noexec' in root_mount['options']:
    raise SystemExit('blocked: owned root is not on an executable filesystem; do not create child')

root.mkdir(mode=0o700)
os.chmod(root, 0o700)
for subdir in ['state', 'log']:
    (root / subdir).mkdir(mode=0o700)
    os.chmod(root / subdir, 0o700)
config_path = root / 'agent.yaml'
config = {
    'hub_url': params['hub_url'],
    'registration_token': params['registration_token'],
    'agent_id': params['agent_id'],
    'node_name': params['node_name'],
    'node_ip': params['node_ip'],
    'deployment_mode': 'native',
    'data_dir': str(root / 'state'),
    'heartbeat_sec': 3,
    'revision_check_sec': 3,
    'max_log_bytes': 1048576,
}
tmp_config = root / 'agent.yaml.tmp'
with open(tmp_config, 'x') as f:
    json.dump(config, f, separators=(',', ':'))
    f.flush()
    os.fsync(f.fileno())
os.chmod(tmp_config, 0o600)
os.replace(tmp_config, config_path)
owner = {
    'owner_nonce': NONCE,
    'node_id': params['node_id'],
    'agent_id': params['agent_id'],
    'node_name': NODE_NAME,
    'unit': CHILD_UNIT,
    'binary_path': AGENT_BIN,
    'binary_sha256': AGENT_SHA,
    'config_path': str(config_path),
    'data_dir': str(root / 'state'),
    'log_path': str(root / 'log/agent.log'),
    'created_unix': int(time.time()),
}
owner_file = root / 'owner.json'
owner_file.write_text(json.dumps(owner, indent=2) + '\n')
os.chmod(owner_file, 0o600)

# Do not pass a registration credential to systemd-run or to the child Agent environment.
env.pop('NODESTEER_PARAM_registration_token', None)
args = [
    'systemd-run', '--quiet', '--no-block', '--unit=' + CHILD_UNIT,
    '--property=User=root', '--property=Restart=no',
    '--property=KillMode=control-group', '--property=RuntimeMaxSec=1800s',
    '--property=TimeoutStopSec=3s',
    '--property=WorkingDirectory=' + str(root),
    '--property=StandardOutput=append:' + str(root / 'log/agent.log'),
    '--property=StandardError=append:' + str(root / 'log/agent.log'),
    '--property=UnsetEnvironment=NODESTEER_PARAM_registration_token',
    AGENT_BIN, '--config', str(config_path),
]
started = run(args, env=env)
if started.returncode != 0:
    print(json.dumps({'setup_status': 'SYSTEMD_RUN_FAILED', 'return_code': started.returncode,
                      'stderr': started.stderr[-500:], 'owner': owner,
                      'root_mode': oct(root.stat().st_mode & 0o7777),
                      'root_uid': root.stat().st_uid,
                      'config_mode': oct(config_path.stat().st_mode & 0o7777),
                      'config_uid': config_path.stat().st_uid,
                      'root_mount': root_mount,
                      'repair_parent': {'unit': PARENT_UNIT, 'properties': parent_props,
                                        'process': parent_process},
                      'baseline_parent': {'unit': BASELINE_UNIT, 'properties': baseline_props}}))
    raise SystemExit(1)

deadline = time.monotonic() + 12
child_props = {}
child_process = None
while time.monotonic() < deadline:
    child_props = unit_props(CHILD_UNIT)
    child_pid = int(child_props.get('MainPID', '0') or 0)
    if child_pid:
        child_process = process(child_pid)
        if child_process:
            break
    time.sleep(.2)
if not child_process:
    raise SystemExit('blocked: child process did not appear in the exact transient unit')
expected_argv = [AGENT_BIN, '--config', str(config_path)]
if child_process['exe_sha256'] != AGENT_SHA or child_process['uid'] != 0 or child_process['argv'] != expected_argv:
    raise SystemExit('blocked: child process identity fingerprint mismatch')
if child_props.get('ControlGroup') not in (child_process['cgroup'][0].split(':', 2)[-1],):
    raise SystemExit('blocked: child process is outside exact transient unit cgroup')
parent_after = unit_props(PARENT_UNIT)
parent_pid_after = int(parent_after.get('MainPID', '0') or 0)
parent_process_after = process(parent_pid_after)
baseline_after = unit_props(BASELINE_UNIT)
if (parent_after.get('ActiveState') != 'active' or not parent_process_after or
        parent_process_after['pid'] != parent_process['pid'] or
        parent_process_after['starttime_ticks'] != parent_process['starttime_ticks'] or
        parent_process_after['exe_sha256'] != AGENT_SHA or
        baseline_after.get('ActiveState') != 'active' or
        baseline_after.get('MainPID') != baseline_props.get('MainPID')):
    raise SystemExit('blocked: preserved original Native-1 service fingerprint changed during child start')

print(json.dumps({
    'setup_status': 'CHILD_STARTED',
    'owner': owner,
    'root_mode': oct(root.stat().st_mode & 0o7777), 'root_uid': root.stat().st_uid,
    'config_mode': oct(config_path.stat().st_mode & 0o7777), 'config_uid': config_path.stat().st_uid,
    'state_dir_mode': oct((root / 'state').stat().st_mode & 0o7777),
    'root_mount': root_mount,
    'child_unit': child_props, 'child_process': child_process,
    'repair_parent': {'unit': PARENT_UNIT, 'properties': parent_props,
                      'process': parent_process,
                      'properties_after': parent_after,
                      'process_after': parent_process_after},
    'baseline_parent': {'unit': BASELINE_UNIT, 'properties': baseline_props,
                        'properties_after': baseline_after},
    'registration_token_written_to_private_config': True,
    'registration_token_in_systemd_or_child_environment': False,
}, sort_keys=True))
