import hashlib
import json
import os
import pathlib
import sqlite3
import time

ROOT = pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee')
APP_ID = os.environ['NODESTEER_PARAM_app_id']
ARTIFACT_SHA = os.environ['NODESTEER_PARAM_artifact_sha']
APP_BIN = ROOT / 'app/bin/qa-app'
APP_CONFIG = ROOT / 'app/config/qa-app.env'
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
UNIT_PATH = pathlib.Path('/etc/systemd/system') / APP_UNIT
EXPECTED_V1_BINARY_SHA = '35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c'
EXPECTED_V1_CONFIG_SHA = '5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34'
EXPECTED_V1_UNIT_SHA = '6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def unit_info():
    import subprocess
    p = subprocess.run(['systemctl', 'show', APP_UNIT, '-p', 'ActiveState', '-p',
                        'MainPID', '-p', 'ControlGroup'], text=True,
                       capture_output=True, timeout=5)
    result = {'rc': p.returncode}
    for line in p.stdout.splitlines():
        if '=' in line:
            k, v = line.split('=', 1)
            result[k] = v
    return result


db = sqlite3.connect('file:' + str(ROOT / 'state/state.db') + '?mode=ro', uri=True,
                     timeout=5)
db.row_factory = sqlite3.Row
db.execute('PRAGMA busy_timeout=5000')
deadline = time.monotonic() + 90
cache_file = ROOT / 'state/artifacts' / ARTIFACT_SHA
artifact_row = None
while time.monotonic() < deadline:
    artifact_row = db.execute('SELECT sha256,path FROM artifacts WHERE sha256=?',
                              (ARTIFACT_SHA,)).fetchone()
    if artifact_row and cache_file.is_file() and sha(cache_file) == ARTIFACT_SHA:
        break
    time.sleep(.25)
deployment = db.execute('SELECT id,application_id,from_version,to_version,phase,'
                        'backup_path,backup_config_path,backup_unit_path '
                        'FROM deployments WHERE application_id=? ORDER BY started_at DESC LIMIT 1',
                        (APP_ID,)).fetchone()
v2_deployments = db.execute('SELECT COUNT(*) FROM deployments WHERE application_id=? AND to_version=?',
                            (APP_ID, 'v2-attempt2')).fetchone()[0]
db.close()

binary_sha = sha(APP_BIN)
config_sha = sha(APP_CONFIG)
unit_sha = sha(UNIT_PATH)
unit_state = unit_info()
if not artifact_row or not cache_file.is_file() or sha(cache_file) != ARTIFACT_SHA:
    raise SystemExit('v2 artifact prefetch did not reach verified cache before timeout')

print(json.dumps({
    'oracle': 'V2_PREFETCH_BEFORE_DEPLOY; V1_PHYSICAL_APP_UNCHANGED',
    'app_id': APP_ID,
    'v2_cache': {'sha256': ARTIFACT_SHA, 'path': str(cache_file),
                 'exists': cache_file.is_file(), 'size': cache_file.stat().st_size,
                 'registered_path': artifact_row['path'],
                 'actual_sha256': sha(cache_file)},
    'v1_physical_state': {
        'binary': {'path': str(APP_BIN), 'sha256': binary_sha,
                   'matches_v1': binary_sha == EXPECTED_V1_BINARY_SHA},
        'config': {'path': str(APP_CONFIG), 'sha256': config_sha,
                   'matches_v1': config_sha == EXPECTED_V1_CONFIG_SHA},
        'unit': {'path': str(UNIT_PATH), 'sha256': unit_sha,
                 'matches_v1': unit_sha == EXPECTED_V1_UNIT_SHA,
                 'active_state': unit_state.get('ActiveState'),
                 'main_pid': unit_state.get('MainPID')},
    },
    'latest_deployment': dict(deployment) if deployment else None,
    'v2_deployment_journal_count': v2_deployments,
}, sort_keys=True))
