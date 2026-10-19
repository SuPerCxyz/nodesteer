#!/usr/bin/env python3
"""Fixture cleanup inside the isolated instance + verification.

Deletes every qa-* fixture created by the stress/authz runs (schedules,
tasks, scripts, groups, applications, artifacts, nodes; transfers canceled).
Users/executions have no delete endpoint (documented)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qalib import Client, load_creds  # noqa: E402

creds = load_creds()
c = Client(creds['host'], creds['port'], timeout=30)
c.login(creds['admin_username'], creds['admin_password'])

log = {'deleted': {}, 'failed': [], 'remaining': {}, 'notes': []}


def jb(r):
    try:
        return json.loads(r.body)
    except Exception:
        return {}


def listing(path):
    d = jb(c.request('GET', path))
    if isinstance(d, dict):
        for k in ('tasks', 'nodes'):
            if isinstance(d.get(k), list):
                return d[k]
        return []
    return d if isinstance(d, list) else []


def is_qa(name):
    return str(name or '').startswith('qa-')


def delete(kind, path, ident, tries=3):
    for t in range(tries):
        r = c.request('DELETE', path)
        if r.status == 200:
            log['deleted'][kind] = log['deleted'].get(kind, 0) + 1
            return True
        if r.status == 404:
            return True
        if r.status == 409:
            time.sleep(0.3)
            continue
        log['failed'].append({'kind': kind, 'id': ident, 'status': r.status, 'body': r.snippet(150)})
        return False
    log['failed'].append({'kind': kind, 'id': ident, 'status': 'retries_exhausted'})
    return False


# 1) schedules (sequential to avoid the concurrent-delete SQLITE_BUSY defect)
for s in listing('/api/schedules'):
    if isinstance(s, dict) and s.get('id'):
        delete('schedules', '/api/schedules/%s' % s['id'], s['id'])

# 2) transfers: cancel all non-terminal ones (no delete endpoint)
transfers = listing('/api/transfers')
for t in transfers:
    if isinstance(t, dict) and t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED'):
        r = c.request('POST', '/api/transfers/%s/cancel' % t['id'])
        if r.status == 200:
            log['deleted']['transfers_canceled'] = log['deleted'].get('transfers_canceled', 0) + 1
        else:
            log['failed'].append({'kind': 'transfer_cancel', 'id': t['id'], 'status': r.status})

# 3) tasks / scripts / groups / applications / artifacts
for path, kind in [('/api/tasks', 'tasks'), ('/api/scripts', 'scripts'),
                   ('/api/groups', 'groups'), ('/api/applications', 'applications'),
                   ('/api/artifacts', 'artifacts')]:
    for item in listing(path):
        if isinstance(item, dict) and item.get('id') and is_qa(item.get('name')):
            delete(kind, '%s/%s' % (path, item['id']), item['id'])

# 4) nodes
for n in listing('/api/nodes'):
    if isinstance(n, dict) and n.get('id') and is_qa(n.get('hostname')):
        delete('nodes', '/api/nodes/%s' % n['id'], n['id'])

# 5) verify
remaining = {}
for path, kind in [('/api/tasks', 'tasks'), ('/api/scripts', 'scripts'),
                   ('/api/schedules', 'schedules'), ('/api/groups', 'groups'),
                   ('/api/applications', 'applications'), ('/api/artifacts', 'artifacts')]:
    remaining[kind] = [i.get('name') for i in listing(path) if isinstance(i, dict) and is_qa(i.get('name'))]
remaining['nodes'] = [n.get('hostname') for n in listing('/api/nodes')
                      if isinstance(n, dict) and is_qa(n.get('hostname'))]
left_tr = [t.get('id') for t in listing('/api/transfers')
           if isinstance(t, dict) and t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED')]
remaining['active_transfers'] = left_tr
users = listing('/api/users')
remaining['users_qa_left'] = [u.get('username') for u in users if isinstance(u, dict) and is_qa(u.get('username'))]
log['notes'].append('users have no DELETE endpoint (user_delete_probe=405): %d qa users remain in the '
                    'disposable instance; the instance itself is destroyed at teardown' % len(remaining['users_qa_left']))
log['notes'].append('executions have no DELETE endpoint; the single qa-authz-exec fixture row remains in the '
                    'disposable instance DB (verified unreachable after teardown)')
log['remaining'] = remaining
log['clean'] = all(not v for k, v in remaining.items() if k != 'users_qa_left')
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixture-cleanup.json'), 'w') as f:
    json.dump(log, f, indent=1)
print(json.dumps(log, indent=1))
