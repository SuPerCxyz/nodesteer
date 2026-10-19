#!/usr/bin/env python3
"""qa-resume-20261018-gate: fixture cleanup + final oracle.

Only touches fixtures created by this gate run (prefix `qa-gate-` and ids
recorded in playwright-resources.jsonl). Users have no DELETE API (retained,
password rotated); transfers/executions are history-only by product design.
"""
import json
import pathlib
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
BASE = 'http://192.168.100.209:18080'
PREFIX = 'qa-gate-'
CRED = json.loads(pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json').read_text())
PREFLIGHT = json.loads((HERE / 'preflight.json').read_text())


def req(path, method='GET', data=None, token=None, timeout=25):
    r = urllib.request.Request(BASE + path, method=method)
    r.add_header('Content-Type', 'application/json')
    if token:
        r.add_header('Authorization', 'Bearer ' + token)
    body = json.dumps(data).encode() if data is not None else None
    try:
        with urllib.request.urlopen(r, body, timeout=timeout) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw or 'null')
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


def now():
    return datetime.now(timezone.utc).isoformat()


st, login = req('/api/login', 'POST', {'username': CRED['admin_username'], 'password': CRED['admin_password']})
tok = login['token']
out = {'at_utc': now(), 'prefix': PREFIX, 'steps': {}}

# ---------------- 1. read resource log
log_created = {}
log_deleted = set()
log_path = HERE / 'playwright-resources.jsonl'
if log_path.exists():
    for line in log_path.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get('status') == 'created':
            log_created[f"{item.get('path')}/{item.get('id')}"] = item
        elif item.get('status') == 'deleted':
            log_deleted.add(item.get('path'))
        else:
            log_created.setdefault(f"{item.get('path')}/{item.get('id')}", item)  # retained_history etc.
pending = {k: v for k, v in log_created.items() if k not in log_deleted and '/api/executions' not in k and '/api/transfers' not in k}
out['steps']['resource_log'] = {
    'created_entries': len(log_created), 'deleted_by_suite': len(log_deleted),
    'pending_after_suite': len(pending),
    'pending_by_path': {},
}
for k, v in pending.items():
    p = v.get('path')
    out['steps']['resource_log']['pending_by_path'].setdefault(p, []).append(v.get('id'))

# ---------------- 2. delete by logged ids (reference order)
ORDER = ['/api/schedules', '/api/tasks', '/api/applications', '/api/artifacts', '/api/scripts', '/api/groups', '/api/nodes']
out['steps']['delete_by_log'] = {}
for path in ORDER:
    items = [v for v in pending.values() if v.get('path') == path]
    for item in items:
        code, body = req(f"{path}/{item['id']}", method='DELETE', token=tok)
        out['steps']['delete_by_log'].setdefault(path, []).append(
            {'id': item['id'], 'name': item.get('name'), 'http': code,
             'body': body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:160]})
        time.sleep(0.25)

# ---------------- 3. prefix sweep
def fetch(path):
    code, body = req(path, token=tok)
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for key in ('items', 'nodes', 'users'):
            if isinstance(body.get(key), list):
                return body[key]
    return []

owned_task_ids = set()
out['steps']['prefix_sweep'] = {}
for kind, path in (('tasks', '/api/tasks'), ('scripts', '/api/scripts'), ('groups', '/api/groups'),
                   ('applications', '/api/applications'), ('artifacts', '/api/artifacts')):
    rows = fetch(path)
    found = [x for x in rows if str(x.get('name') or '').startswith(PREFIX)]
    deleted = []
    for x in found:
        if kind == 'tasks':
            owned_task_ids.add(x['id'])
        code, body = req(f"{path}/{x['id']}", method='DELETE', token=tok)
        deleted.append({'id': x['id'], 'name': x.get('name'), 'http': code})
        time.sleep(0.25)
    out['steps']['prefix_sweep'][kind] = {'found': len(found), 'deleted': deleted}
# schedules: ownership via task_id, or ids logged
rows = fetch('/api/schedules')
found = [x for x in rows if x.get('task_id') in owned_task_ids]
deleted = []
for x in found:
    code, body = req(f"/api/schedules/{x['id']}", method='DELETE', token=tok)
    deleted.append({'id': x['id'], 'task_id': x.get('task_id'), 'http': code})
    time.sleep(0.25)
out['steps']['prefix_sweep']['schedules'] = {'found': len(found), 'deleted': deleted}
# nodes
rows = fetch('/api/nodes')
owned_nodes = [n for n in rows if str(n.get('hostname') or n.get('name') or '').startswith(PREFIX)]
out['steps']['prefix_sweep']['nodes'] = {'found': len(owned_nodes), 'deleted': []}
for n in owned_nodes:
    code, body = req('/api/nodes/' + n['id'], method='DELETE', token=tok)
    out['steps']['prefix_sweep']['nodes']['deleted'].append(
        {'id': n['id'], 'hostname': n.get('hostname'), 'http': code})
    time.sleep(0.4)

# ---------------- 4. users: no delete API -> rotate password
out['steps']['users'] = {'created_by_run': [], 'retained': [], 'note': '产品无用户删除接口；保留账号并轮换密码为随机值'}
rows = fetch('/api/users')
for u in rows:
    if str(u.get('username') or '').startswith(PREFIX):
        new_pw = secrets.token_urlsafe(18)
        code, _ = req(f"/api/users/{u['id']}", method='PUT', data={'password': new_pw}, token=tok)
        out['steps']['users']['retained'].append(
            {'id': u['id'], 'username': u['username'], 'role': u.get('role'),
             'password_rotated_http': code})

# ---------------- 5. verify leftovers
time.sleep(1)
out['steps']['leftover_scan'] = {}
for kind, path in (('tasks', '/api/tasks'), ('scripts', '/api/scripts'), ('groups', '/api/groups'),
                   ('applications', '/api/applications'), ('artifacts', '/api/artifacts'),
                   ('schedules', '/api/schedules'), ('nodes', '/api/nodes')):
    rows = fetch(path)
    if kind == 'schedules':
        left = [x for x in rows if x.get('task_id') in owned_task_ids]
    else:
        left = [x for x in rows if str(x.get('hostname') or x.get('name') or '').startswith(PREFIX)]
    out['steps']['leftover_scan'][kind] = {'leftover': left}
rows = fetch('/api/users')
out['steps']['leftover_scan']['users_retained'] = [
    u['username'] for u in rows if str(u.get('username') or '').startswith(PREFIX)]

# ---------------- 6. final oracle vs baseline
base = PREFLIGHT['collections']
final = {}
for kind, path in (('nodes', '/api/nodes'), ('users', '/api/users'), ('applications', '/api/applications'),
                   ('tasks', '/api/tasks'), ('scripts', '/api/scripts'), ('groups', '/api/groups'),
                   ('schedules', '/api/schedules'), ('artifacts', '/api/artifacts'), ('transfers', '/api/transfers')):
    final[kind] = len(fetch(path))
code_h, body_h = req('/healthz')
code_r, body_r = req('/readyz')
oracle = {
    'at_utc': now(),
    'healthz': {'http': code_h, 'body': body_h},
    'readyz': {'http': code_r, 'body': body_r},
    'baseline': {k: base[k]['count'] for k in base},
    'final': final,
    'checks': {},
}
oracle['checks']['applications_back_to_baseline'] = final['applications'] == base['applications']['count']
oracle['checks']['tasks_back_to_baseline'] = final['tasks'] == base['tasks']['count']
oracle['checks']['groups_back_to_baseline'] = final['groups'] == base['groups']['count']
oracle['checks']['schedules_back_to_baseline'] = final['schedules'] == base['schedules']['count']
oracle['checks']['scripts_back_to_baseline'] = final['scripts'] == base['scripts']['count']
oracle['checks']['artifacts_back_to_baseline'] = final['artifacts'] == base['artifacts']['count']
oracle['checks']['nodes_back_to_baseline'] = final['nodes'] == base['nodes']['count']
oracle['checks']['transfers_only_grew'] = final['transfers'] >= base['transfers']['count']
oracle['checks']['users_only_grew_by_owned'] = final['users'] >= base['users']['count']
oracle['checks']['no_qa_gate_leftover'] = all(
    len(out['steps']['leftover_scan'][k]['leftover']) == 0
    for k in ('tasks', 'scripts', 'groups', 'applications', 'artifacts', 'schedules', 'nodes'))
oracle['checks']['baseline_scripts_intact'] = set(base['scripts']['names']).issubset(
    {i.get('name') for i in fetch('/api/scripts')})
oracle['checks']['baseline_artifact_intact'] = base['artifacts']['names'][0] in [
    i.get('name') for i in fetch('/api/artifacts')]
oracle['checks']['baseline_nodes_intact'] = set(
    n['id'] for n in base['nodes']['items']) == set(n['id'] for n in fetch('/api/nodes'))
oracle['checks']['hub_healthy'] = code_h == 200 and code_r == 200
oracle['oracle_pass'] = all(oracle['checks'].values())

ledger = {
    'at_utc': now(), 'prefix': PREFIX, 'authority': BASE,
    'baseline_counts': {k: base[k]['count'] for k in base},
    'final_counts': final,
    'created_logged': {k: v.get('name') for k, v in log_created.items()},
    'deleted_by_suite': sorted(log_deleted),
    'cleanup': out['steps'],
    'retained_history': {
        'users': [u['username'] for u in out['steps']['users']['retained']],
        'transfers_new': final['transfers'] - base['transfers']['count'],
        'note': '用户无删除 API（密码已轮换）；传输/执行仅历史记录，无删除 API',
    },
}
(HERE / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2))
(HERE / 'final-cleanup-oracle.json').write_text(json.dumps(oracle, ensure_ascii=False, indent=2))
print(json.dumps({'leftover': {k: len(out['steps']['leftover_scan'][k]['leftover']) for k in out['steps']['leftover_scan'] if k != 'users_retained'},
                  'users_retained': out['steps']['leftover_scan']['users_retained'],
                  'baseline': oracle['baseline'], 'final': oracle['final'],
                  'checks': oracle['checks'], 'oracle_pass': oracle['oracle_pass']}, ensure_ascii=False, indent=1))
