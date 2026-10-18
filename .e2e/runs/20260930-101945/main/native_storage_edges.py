"""仅新建Rocky Agent3自有目录/tmpfs与定义：权限、空间和路径负向。"""
import base64
import hashlib
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
NODE_ID = '811d72ef-75f8-47ba-84de-8cb1e6974315'
NODE = next(n for n in CFG['nodes'] if n['id'] == NODE_ID)
BASE = CFG['base_url']
login = requests.post(BASE + '/api/login', json={'username': CFG['admin_username'], 'password': CFG['admin_password']}, timeout=20)
login.raise_for_status()
HEADERS = {'Authorization': 'Bearer ' + login.json()['token']}
PREFIX = 'qa-20260930-101945-main-storage-' + uuid.uuid4().hex[:8]
DIRECTORY = '/tmp/' + PREFIX
ROWS, LEDGER = [], []


def save():
    for name, data in [('native-storage-edge-results.json', ROWS), ('native-storage-edge-ledger.json', LEDGER)]:
        (ROOT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def record(case, ok, **detail):
    ROWS.append({'case_id': case, 'classification': 'REAL_API_AGENT', 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
    save()
    print(case, ROWS[-1]['status'], flush=True)


def guest(script):
    p = subprocess.run(NODE['executor_argv'], input=script, text=True, capture_output=True, timeout=30)
    if p.returncode:
        raise RuntimeError('owned guest storage step failed ' + str(p.returncode) + ': ' + p.stderr[-500:])
    return p.stdout.strip()


def api(method, path, body=None):
    r = requests.request(method, BASE + path, headers=HEADERS, json=body, timeout=25)
    r.raise_for_status()
    return r.json()


def create(kind, body):
    item = api('POST', '/api/' + kind, body)
    LEDGER.append({'kind': kind, 'id': item['id'], 'status': 'created'})
    save()
    return item


def terminal(eid):
    end = time.monotonic() + 35
    while time.monotonic() < end:
        value = api('GET', '/api/executions/' + eid)
        if value['status'] not in ['PENDING', 'RUNNING']:
            return value
        time.sleep(.4)
    raise RuntimeError('owned storage execution terminal timeout')


def synced():
    end = time.monotonic() + 20
    while time.monotonic() < end:
        value = api('GET', '/api/nodes/' + NODE_ID)
        if value['status'] == 'online' and value['sync_status'] == 'synced':
            return value
        time.sleep(.4)
    raise RuntimeError('owned Agent sync timeout')


def file_state(path, unit=None):
    code = "import pathlib,json,subprocess,hashlib,os; p=pathlib.Path(" + repr(path) + "); out={'entries':sorted(x.name for x in p.iterdir()),'agent':subprocess.run(['systemctl','is-active','nodesteer-agent.service'],capture_output=True,text=True).stdout.strip()}"
    if unit:
        code += "; out['unit_exists']=pathlib.Path('/etc/systemd/system/' + " + repr(unit) + ").exists()"
    code += "; print(json.dumps(out))"
    return json.loads(guest('python3 -c ' + shlex.quote(code) + '\n'))


guest('set -eu\nmkdir -m 700 ' + shlex.quote(DIRECTORY) + '\nmkdir -m 700 ' + shlex.quote(DIRECTORY + '/private') + '\n')
try:
    for label, user in [('permission', 'nobody'), ('unknown-user', PREFIX)]:
        task = create('tasks', {'name': PREFIX + '-' + label, 'type': 'command', 'target': {'type': 'node', 'node_ids': [NODE_ID]},
            'command': 'printf must-not-write > ' + shlex.quote(DIRECTORY + '/private/' + label), 'run_user': user, 'timeout': 5, 'retry': 0, 'enabled': True})
        synced()
        eid = api('POST', '/api/tasks/' + task['id'] + '/run', {})[0]['id']
        result = terminal(eid)
        record('MAIN-NATIVE-' + label.upper(), result['status'] == 'FAILED' and guest('test ! -e ' + shlex.quote(DIRECTORY + '/private/' + label) + ' && echo absent\n') == 'absent', execution=result)

    fixture = base64.b64decode(guest('base64 /bin/sh\n'))
    response = requests.post(BASE + '/api/artifacts', headers=HEADERS, data={'name': PREFIX + '-fixture', 'version': 'v1', 'architecture': 'amd64'}, files={'file': ('fixture.bin', fixture)}, timeout=25)
    response.raise_for_status()
    artifact = response.json()
    LEDGER.append({'kind': 'artifacts', 'id': artifact['id'], 'status': 'created'})
    save()
    for label, mount_options in [('disk-full', 'size=4096,mode=0755'), ('read-only', 'size=1m,mode=0755,ro')]:
        path, unit = DIRECTORY + '/' + label, PREFIX + '-' + label + '.service'
        guest('set -eu\nmkdir ' + shlex.quote(path) + '\nmount -t tmpfs -o ' + shlex.quote(mount_options) + ' tmpfs ' + shlex.quote(path) + '\n')
        try:
            app = create('applications', {'name': PREFIX + '-' + label, 'version': 'v1', 'artifact_id': artifact['id'], 'binary_path': path + '/app-bin', 'config_path': path + '/app.conf', 'config': 'owned-fixture', 'unit_name': unit,
                'arguments': ['-c', 'exec sleep 30'], 'health_check': {'type': 'systemd', 'timeout': 1, 'attempts': 1, 'interval': 1}})
            api('POST', '/api/applications/' + app['id'] + '/assign', {'node_ids': [NODE_ID]})
            synced()
            result = terminal(api('POST', '/api/applications/' + app['id'] + '/deploy', {'node_ids': [NODE_ID], 'operation': 'deploy'})[0]['id'])
            files = file_state(path, unit)
            states = api('GET', '/api/applications/' + app['id'] + '/state')
            text = ' '.join([result.get('stderr', ''), result.get('block_reason', '')] + [x.get('error', '') for x in states])
            expected = 'no space left' if label == 'disk-full' else 'read-only file system'
            record('MAIN-NATIVE-STORAGE-' + label.upper(), result['status'] == 'FAILED' and expected in text.lower() and files['entries'] == [] and not files['unit_exists'] and files['agent'] == 'active', execution_id=result['id'], execution_status=result['status'], application_state=states, guest=files, artifact_sha256=hashlib.sha256(fixture).hexdigest())
            api('DELETE', '/api/applications/' + app['id'])
            next(x for x in LEDGER if x['id'] == app['id'])['status'] = 'deleted'
            save()
        finally:
            guest('set -eu\numount ' + shlex.quote(path) + '\nrmdir ' + shlex.quote(path) + '\n')
    path = DIRECTORY + '/invalid/../escape-bin'
    unit = PREFIX + '-invalid.service'
    result = requests.post(BASE + '/api/applications', headers=HEADERS, json={'name': PREFIX + '-path', 'version': 'v1', 'artifact_id': artifact['id'], 'binary_path': path, 'config_path': DIRECTORY + '/invalid.conf', 'config': 'owned', 'unit_name': unit, 'arguments': ['-c', 'exec sleep 30'], 'health_check': {'type': 'systemd', 'timeout': 1, 'attempts': 1, 'interval': 1}}, timeout=25)
    actual = file_state(DIRECTORY, unit)
    record('MAIN-NATIVE-PATH-TRAVERSAL', result.status_code == 400 and actual['entries'] == ['private'] and not actual['unit_exists'], http=result.status_code, error=result.json().get('error'), rejected_before_dispatch=True, guest=actual)
finally:
    for item in reversed(LEDGER):
        if item['status'] != 'created':
            continue
        try:
            api('DELETE', '/api/' + item['kind'] + '/' + item['id'])
            item['status'] = 'deleted'
        except Exception as error:
            item['status'] = 'cleanup_failed'
            item['error_type'] = type(error).__name__
        save()
    guest('set -eu\nrmdir ' + shlex.quote(DIRECTORY + '/private') + '\nrmdir ' + shlex.quote(DIRECTORY) + '\n')
    requests.post(BASE + '/api/logout', headers=HEADERS, timeout=20)
    record('MAIN-NATIVE-STORAGE-CLEANUP', all(x['status'] == 'deleted' for x in LEDGER), resources=LEDGER, native_status='service checked active in each case')
