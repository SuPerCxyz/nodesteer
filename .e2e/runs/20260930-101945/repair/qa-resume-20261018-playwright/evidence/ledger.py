#!/usr/bin/env python3
"""Fixture ledger + exact-ID cleanup + GET-404 oracle for the repair Playwright run."""
import json, pathlib, subprocess, sys, time

PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')
LOG = OUT / 'playwright-resources.jsonl'
cred = json.loads((PRIVATE / 'repair-credentials.json').read_text())
BASE = 'http://192.168.100.209:18080'
PREFIX = 'qa-pw-20261018-'

ledger = []
for line in LOG.read_text().splitlines():
    line = line.strip()
    if line:
        try:
            ledger.append(json.loads(line))
        except ValueError:
            pass

# fixtures.ts logs created entries as {path: <collection>, id: <object id>}
# and deleted entries as {path: <collection>/<id>}.
created, deleted, retained = {}, set(), []
for e in ledger:
    st, p = e.get('status'), e.get('path', '')
    if st == 'created' and e.get('id'):
        created[f"{p}/{e['id']}"] = e
    elif st == 'deleted':
        deleted.add(p)
    else:
        retained.append(e)

residual = sorted(p for p in created if p not in deleted)

login = subprocess.run(['curl', '-s', '--max-time', '15', '-X', 'POST', f'{BASE}/api/login',
                        '-H', 'Content-Type: application/json',
                        '-d', json.dumps({'username': cred['admin_username'], 'password': cred['admin_password']})],
                       capture_output=True, text=True)
token = json.loads(login.stdout).get('token') or json.loads(login.stdout).get('Token')


def http(method, path):
    r = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '15',
                        '-X', method, BASE + path, '-H', f'Authorization: Bearer {token}'],
                       capture_output=True, text=True)
    return r.stdout.strip()


cleanup = []
for path in residual:
    if path.startswith('/api/executions'):
        cleanup.append({'path': path, 'action': 'retained_history_no_delete_api'})
        continue
    code = http('DELETE', path)
    cleanup.append({'path': path, 'delete_http': code})
    time.sleep(0.03)

# Users have no DELETE endpoint (PRODUCT P20-05); probe one own-prefix user to document it.
user_probe = None
own_users = [e['id'] for e in ledger if e.get('status') == 'retained_account']
if own_users:
    u = own_users[0]
    user_probe = {'path': f'/api/users/{u}', 'delete_http': http('DELETE', f'/api/users/{u}'),
                  'get_after_delete_http': http('GET', f'/api/users/{u}')}

oracle = []
for path in residual:
    if path.startswith('/api/executions'):
        continue
    code = http('GET', path)
    oracle.append({'path': path, 'get_http': code, 'gone': code in ('404', '410')})

residue = {}
for name, path, key in [
    ('nodes', '/api/nodes', 'hostname'), ('scripts', '/api/scripts', 'name'),
    ('tasks', '/api/tasks', 'name'), ('groups', '/api/groups', 'name'),
    ('schedules', '/api/schedules', 'name'), ('applications', '/api/applications', 'name'),
    ('artifacts', '/api/artifacts', 'name'), ('users', '/api/users', 'username'),
    ('transfers', '/api/transfers', 'id'),
]:
    r = subprocess.run(['curl', '-s', '--max-time', '20', BASE + path, '-H', f'Authorization: Bearer {token}'],
                       capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
        items = data if isinstance(data, list) else data.get('items', data.get('users', []))
        hits = [{'id': i.get('id'), key: i.get(key)} for i in items if str(i.get(key, '')).startswith(PREFIX)]
        residue[name] = {'total': len(items), 'prefix_hits': hits}
    except ValueError:
        residue[name] = {'error': r.stdout[:200]}

# Node label residue check (tests set labels on repair QA nodes)
labels = []
r = subprocess.run(['curl', '-s', '--max-time', '20', BASE + '/api/nodes', '-H', f'Authorization: Bearer {token}'],
                   capture_output=True, text=True)
for n in json.loads(r.stdout):
    for k, v in (n.get('labels') or {}).items():
        if str(k).startswith(PREFIX) or PREFIX in str(v) or str(k).startswith('qa_web_') or str(k).startswith('qa-'):
            labels.append({'node': n.get('hostname'), 'key': k, 'value': v})

payload = {
    'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
    'base_url': BASE, 'prefix': PREFIX,
    'ledger_counts': {'created': len(created), 'deleted_logged': len(deleted),
                      'residual_after_run': len(residual), 'retained_entries': len(retained)},
    'owned_residual': residual,
    'cleanup': cleanup,
    'user_delete_probe': user_probe,
    'oracle': oracle,
    'all_gone': all(o['gone'] for o in oracle) if oracle else None,
    'list_residue': residue,
    'node_label_residue': labels,
    'retained_by_design': [e for e in retained],
}
(OUT / 'final-cleanup-oracle.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2))
print(json.dumps({'residual_targets': len(residual), 'all_gone': payload['all_gone'],
                  'user_probe': user_probe,
                  'residue_hits': {k: len(v.get('prefix_hits', [])) for k, v in residue.items()},
                  'node_label_residue': labels,
                  'retained_history_executions': sum(1 for e in retained if e.get('path') == '/api/executions'),
                  }, ensure_ascii=False, indent=2))
