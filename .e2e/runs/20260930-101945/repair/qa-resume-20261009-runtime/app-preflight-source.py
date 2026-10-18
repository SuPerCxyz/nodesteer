import hashlib
import json
import os
import pathlib
import re
import subprocess

ROOT = pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee')
NODE_ID = 'c77b8a84-8085-4ea8-8532-de171761854b'
NONCE = '870358cd92ee'
CHILD_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service'
CHILD_PID = 47582
CHILD_START = 129963662
CHILD_EXE_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
APP_BIN = ROOT / 'app/bin/qa-app'
APP_CONFIG = ROOT / 'app/config/qa-app.env'
APP_UNIT_PATH = pathlib.Path('/etc/systemd/system') / APP_UNIT


def props(unit):
    p = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '-p',
                        'ActiveState', '-p', 'MainPID', '-p', 'ControlGroup'],
                       text=True, capture_output=True, timeout=5)
    out = {'rc': p.returncode}
    for line in p.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            out[k] = v
    return out


def fingerprint(pid):
    raw = open(f'/proc/{pid}/stat').read()
    rest = raw[raw.rfind(')') + 2:].split()
    status = open(f'/proc/{pid}/status').read()
    uid = re.search(r'^Uid:\s+(\d+)', status, re.M)
    args_raw = open(f'/proc/{pid}/cmdline', 'rb').read()
    args = [x.decode(errors='replace') for x in args_raw.split(b'\0') if x]
    h = hashlib.sha256()
    with open(f'/proc/{pid}/exe', 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return {'pid': pid, 'starttime_ticks': int(rest[19]),
            'uid': int(uid.group(1)) if uid else None, 'argv': args,
            'argv_sha256': hashlib.sha256(args_raw).hexdigest(),
            'exe_path': os.readlink(f'/proc/{pid}/exe'),
            'exe_sha256': h.hexdigest(),
            'cgroup': open(f'/proc/{pid}/cgroup').read().strip().splitlines()}


owner = json.loads((ROOT / 'owner.json').read_text())
if owner.get('owner_nonce') != NONCE or owner.get('node_id') != NODE_ID:
    raise SystemExit('blocked: child owner marker mismatch')
child_unit = props(CHILD_UNIT)
child_pid = int(child_unit.get('MainPID', '0') or 0)
child_process = fingerprint(child_pid) if child_pid else None
if child_unit.get('ActiveState') != 'active' or child_pid != CHILD_PID or not child_process:
    raise SystemExit('blocked: child Agent unit/PID changed since approved preflight')
if (child_process['starttime_ticks'] != CHILD_START or child_process['uid'] != 0 or
        child_process['exe_sha256'] != CHILD_EXE_SHA or
        child_process['argv'] != [owner['binary_path'], '--config', owner['config_path']] or
        child_unit.get('ControlGroup') != child_process['cgroup'][0].split(':', 2)[-1]):
    raise SystemExit('blocked: child Agent owner fingerprint mismatch')

app_dir = ROOT / 'app'
paths = {str(p): os.path.lexists(p) for p in
         [app_dir, APP_BIN, APP_CONFIG, APP_UNIT_PATH]}
app_unit = props(APP_UNIT)
if any(paths.values()) or app_unit.get('LoadState') not in {None, '', 'not-found'}:
    raise SystemExit('blocked: reserved Application path or systemd Unit already exists')

print(json.dumps({
    'preflight': 'PASS', 'owner_nonce': NONCE, 'node_id': NODE_ID,
    'child_unit': child_unit, 'child_process': child_process,
    'application_paths_absent': paths,
    'application_unit': app_unit,
    'application_unit_loadstate_not_found': app_unit.get('LoadState') in {None, '', 'not-found'},
}, sort_keys=True))
