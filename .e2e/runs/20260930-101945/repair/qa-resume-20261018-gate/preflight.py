#!/usr/bin/env python3
"""qa-resume-20261018-gate preflight: baseline snapshot before the gate run.

Reads admin credentials from the 0600 file (never persisted here). Records
hub health, node inventory and mutable collection baselines so the final
cleanup oracle can diff against them.
"""
import json
import pathlib
import urllib.request
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
BASE = 'http://192.168.100.209:18080'
CRED = json.loads(pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json').read_text())


def req(path, method='GET', data=None, token=None, timeout=20):
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


out = {'authority': BASE, 'checked_at': __import__('datetime').datetime.now(
    __import__('datetime').timezone.utc).isoformat()}

for p in ('/healthz', '/readyz'):
    st, body = req(p)
    out[p] = {'status': st, 'body': body if isinstance(body, str) else str(body)[:100]}

st, login = req('/api/login', 'POST', {'username': CRED['admin_username'], 'password': CRED['admin_password']})
out['login_status'] = st
tok = login.get('token') if isinstance(login, dict) else None
out['login_ok'] = bool(tok)

collections = {}
if tok:
    nodes = req('/api/nodes', token=tok)[1]
    collections['nodes'] = {
        'count': len(nodes),
        'items': [{'id': n['id'], 'name': n.get('hostname') or n.get('name'), 'status': n.get('status')} for n in nodes],
    }
    for path, key in (
        ('/api/users', 'users'), ('/api/applications', 'applications'), ('/api/tasks', 'tasks'),
        ('/api/scripts', 'scripts'), ('/api/groups', 'groups'), ('/api/schedules', 'schedules'),
        ('/api/artifacts', 'artifacts'), ('/api/transfers', 'transfers'),
    ):
        st, items = req(path, token=tok)
        collections[key] = {
            'http': st,
            'count': len(items) if isinstance(items, list) else None,
            'names': [i.get('name') or i.get('id') for i in items] if isinstance(items, list) else None,
        }
    st, execs = req('/api/executions?limit=1000', token=tok)
    collections['executions'] = {'http': st, 'count': len(execs) if isinstance(execs, list) else None}
out['collections'] = collections

(HERE / 'preflight.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in out.items() if k != 'collections'}, ensure_ascii=False))
print({k: v['count'] for k, v in collections.items()})
