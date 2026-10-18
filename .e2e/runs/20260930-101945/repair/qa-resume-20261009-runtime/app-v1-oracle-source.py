import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess

ROOT = pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee')
APP_ID = os.environ['NODESTEER_PARAM_app_id']
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
APP_BIN = ROOT / 'app/bin/qa-app'
APP_CONFIG = ROOT / 'app/config/qa-app.env'
UNIT_PATH = pathlib.Path('/etc/systemd/system') / APP_UNIT


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(args):
    p = subprocess.run(args, text=True, capture_output=True, timeout=8)
    return {'rc': p.returncode, 'stdout': p.stdout.strip()}


systemd_show = run(['systemctl', 'show', APP_UNIT, '-p', 'LoadState', '-p',
                    'ActiveState', '-p', 'SubState', '-p', 'MainPID', '-p',
                    'ControlGroup'])
unit_props = {}
for line in systemd_show['stdout'].splitlines():
    if '=' in line:
        k, v = line.split('=', 1)
        unit_props[k] = v
main_pid = int(unit_props.get('MainPID', '0') or 0)
unit_text = pathlib.Path(UNIT_PATH).read_text()
process = None
if main_pid:
    raw = open(f'/proc/{main_pid}/stat').read()
    rest = raw[raw.rfind(')') + 2:].split()
    status = open(f'/proc/{main_pid}/status').read()
    uid = re.search(r'^Uid:\s+(\d+)', status, re.M)
    process = {
        'pid': main_pid, 'starttime_ticks': int(rest[19]),
        'uid': int(uid.group(1)) if uid else None,
        'exe_path': os.readlink(f'/proc/{main_pid}/exe'),
        'exe_sha256': sha(f'/proc/{main_pid}/exe'),
        'argv': [x.decode(errors='replace') for x in
                 open(f'/proc/{main_pid}/cmdline', 'rb').read().split(b'\0') if x],
        'cgroup': open(f'/proc/{main_pid}/cgroup').read().strip().splitlines(),
    }

db = sqlite3.connect('file:' + str(ROOT / 'state/state.db') + '?mode=ro', uri=True,
                     timeout=5)
db.row_factory = sqlite3.Row
db.execute('PRAGMA busy_timeout=5000')
row = db.execute('SELECT id,application_id,from_version,to_version,phase,'
                 'backup_path,backup_config_path,backup_unit_path,started_at,updated_at '
                 'FROM deployments WHERE application_id=? ORDER BY started_at DESC LIMIT 1',
                 (APP_ID,)).fetchone()
deployment = dict(row) if row else None
db.close()

config_bytes = APP_CONFIG.read_bytes()
print(json.dumps({
    'oracle': 'APPLICATION_V1_PHYSICAL_STATE',
    'app_id': APP_ID,
    'binary': {'path': str(APP_BIN), 'exists': APP_BIN.is_file(),
               'executable': os.access(APP_BIN, os.X_OK),
               'size': APP_BIN.stat().st_size, 'sha256': sha(APP_BIN)},
    'config': {'path': str(APP_CONFIG), 'exists': APP_CONFIG.is_file(),
               'sha256': hashlib.sha256(config_bytes).hexdigest(),
               'contents': config_bytes.decode(errors='replace')},
    'unit': {'name': APP_UNIT, 'path': str(UNIT_PATH),
             'exists': UNIT_PATH.is_file(), 'sha256': sha(UNIT_PATH),
             'contains_binary_path': str(APP_BIN) in unit_text,
             'contains_app_id': APP_ID in unit_text,
             'service': unit_props, 'process': process},
    'deployment_journal': deployment,
}, sort_keys=True))
