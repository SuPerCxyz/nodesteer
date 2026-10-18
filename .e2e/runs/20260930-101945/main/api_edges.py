"""真实API负向边界及Artifact三种身份；不记录认证输入。"""
import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
ACCOUNTS = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/main-accounts.json').read_text())
BASE = CFG['base_url']
ROWS = []


def result(case, response, expected, **detail):
    row = {'case_id': case, 'classification': 'REAL_API', 'status': 'PASS' if response.status_code == expected else 'FAIL',
           'actual_http': response.status_code, 'expected_http': expected, 'detail': detail}
    ROWS.append(row)
    (ROOT / 'api-edge-results.json').write_text(json.dumps(ROWS, ensure_ascii=False, indent=2) + '\n')
    print(case, row['status'], response.status_code, flush=True)


def call(method, path, token=None, **kwargs):
    h = kwargs.pop('headers', {})
    if token:
        h['Authorization'] = 'Bearer ' + token
    return requests.request(method, BASE + path, headers=h, timeout=20, **kwargs)


admin = CFG['admin_token']
paths = ['/api/me', '/api/nodes', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules',
         '/api/artifacts', '/api/applications', '/api/executions', '/api/transfers', '/api/audit', '/api/settings', '/api/users', '/metrics']
for i, path in enumerate(paths):
    result(f'MAIN-INVALID-BEARER-{i:02}', call('GET', path, 'invalid-' + uuid.uuid4().hex), 401)

u = ACCOUNTS['viewer']
login = call('POST', '/api/login', json={'username': u['username'], 'password': u['password']})
login.raise_for_status()
revoked = login.json()['token']
result('MAIN-SESSION-VALID', call('GET', '/api/me', revoked), 200)
result('MAIN-SESSION-LOGOUT', call('POST', '/api/logout', revoked), 200)
for i, path in enumerate(paths):
    result(f'MAIN-REVOKED-BEARER-{i:02}', call('GET', path, revoked), 401)

for i, path in enumerate(['/api/nodes/enrollment', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules', '/api/applications', '/api/transfers', '/api/users']):
    result(f'MAIN-MALFORMED-JSON-{i:02}', call('POST', path, admin, data='{', headers={'Content-Type': 'application/json'}), 400)
for i, path in enumerate(['/api/groups/qa-nonexistent', '/api/scripts/qa-nonexistent', '/api/tasks/qa-nonexistent', '/api/schedules/qa-nonexistent', '/api/applications/qa-nonexistent']):
    result(f'MAIN-DELETE-MISSING-{i:02}', call('DELETE', path, admin), 200)

payload = b'qa-20260930-101945-artifact-auth-real-bytes'
r = call('POST', '/api/artifacts', admin, data={'name': 'qa-20260930-101945-main-auth-' + uuid.uuid4().hex[:8], 'version': 'v1', 'architecture': 'amd64'},
         files={'file': ('fixture.bin', payload, 'application/octet-stream')})
r.raise_for_status()
artifact = r.json()
try:
    path = '/api/artifacts/' + artifact['id'] + '/download'
    result('MAIN-ARTIFACT-NOAUTH', call('GET', path), 401)
    for role, user in ACCOUNTS.items():
        rr = call('POST', '/api/login', json={'username': user['username'], 'password': user['password']})
        rr.raise_for_status()
        x = call('GET', path, rr.json()['token'])
        result('MAIN-ARTIFACT-USER-' + role, x, 200 if x.content == payload else -1, sha256=hashlib.sha256(x.content).hexdigest())
    node = next(n for n in CFG['nodes'] if n['id'] == '811d72ef-75f8-47ba-84de-8cb1e6974315')
    script = '''python3 - <<'PY'
import sqlite3,json
c=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True)
d=dict(c.execute("select key,value from identity where key in ('agent_id','credential')"))
for line in open('/etc/nodesteer/agent.yaml'):
 if line.strip().startswith('registration_token:'):
  value=line.split(':',1)[1].strip()
  d['registration_token']=json.loads(value) if value.startswith('"') else value
print(json.dumps(d))
PY
'''
    proc = subprocess.run(node['executor_argv'], input=script, text=True, capture_output=True, timeout=20)
    if proc.returncode:
        raise RuntimeError('owned Agent identity read failed')
    identity = json.loads(proc.stdout)
    for label, headers in [
        ('REGISTRATION', {'X-NodeSteer-Agent-Token': identity['registration_token']}),
        ('CREDENTIAL', {'X-NodeSteer-Agent-ID': identity['agent_id'], 'X-NodeSteer-Agent-Token': identity['credential']}),
    ]:
        x = call('GET', path, headers=headers)
        result('MAIN-ARTIFACT-' + label, x, 200 if x.content == payload else -1, content_matches=x.content == payload)
    result('MAIN-ARTIFACT-INVALID-AGENT', call('GET', path, headers={'X-NodeSteer-Agent-ID': identity['agent_id'], 'X-NodeSteer-Agent-Token': 'invalid'}), 401)
    result('MAIN-RELAY-USER-REJECTED', call('GET', '/agent/transfers/qa-nonexistent/download', admin), 401)
    result('MAIN-RELAY-REGISTRATION-REJECTED', call('GET', '/agent/transfers/qa-nonexistent/download', headers={'X-NodeSteer-Agent-ID': identity['agent_id'], 'X-NodeSteer-Agent-Token': identity['registration_token']}), 401)
    result('MAIN-RELAY-VALID-AGENT-MISSING', call('GET', '/agent/transfers/qa-nonexistent/download', headers={'X-NodeSteer-Agent-ID': identity['agent_id'], 'X-NodeSteer-Agent-Token': identity['credential']}), 404)
finally:
    x = call('DELETE', '/api/artifacts/' + artifact['id'], admin)
    result('MAIN-ARTIFACT-EXACT-CLEANUP', x, 200, artifact_id=artifact['id'])
