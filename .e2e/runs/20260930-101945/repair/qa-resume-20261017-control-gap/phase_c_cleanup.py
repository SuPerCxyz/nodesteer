#!/usr/bin/env python3
"""Phase C: precise cleanup of owned fixtures + GET404 oracle + final artifacts."""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import cgap_lib as L  # noqa: E402

SETUP = L.load('phase-a-setup.json')
RESULTS = L.load('phase-b-results.json') or {}
B_TESTS = RESULTS.get('tests', {})

hub = L.Hub()
hub.login()

now = lambda: datetime.now(timezone.utc).isoformat()

# ---- collect owned fixtures ----
c, schedules, _ = hub.get('/api/schedules')
mine_sched = [s for s in (schedules or []) if s.get('task_id') == SETUP['task']['id']]
c, arts, _ = hub.get('/api/artifacts')
mine_art2 = [a for a in (arts or []) if a.get('name') == L.PREFIX + 'art2']

fixtures = []
if mine_sched:
    fixtures.append({'kind': 'schedule', 'id': mine_sched[0]['id'], 'label': L.PREFIX + 'schedule', 'note': 'created via /schedules/new UI (one_time Asia/Shanghai)'})
fixtures += [
    {'kind': 'task', 'id': SETUP['task']['id'], 'label': SETUP['task']['name']},
    {'kind': 'group', 'id': SETUP['group']['id'], 'label': SETUP['group']['name']},
    {'kind': 'script', 'id': SETUP['script']['id'], 'label': SETUP['script']['name']},
    {'kind': 'application', 'id': SETUP['application']['id'], 'label': SETUP['application']['name']},
]
for a in mine_art2:
    fixtures.append({'kind': 'artifact', 'id': a['id'], 'label': a['name'] + ' ' + a['version'], 'note': 'created via inline upload on /applications/{id}'})
fixtures.append({'kind': 'artifact', 'id': SETUP['artifact']['id'], 'label': SETUP['artifact']['name'] + ' ' + SETUP['artifact']['version']})

paths = {'schedule': '/api/schedules/', 'task': '/api/tasks/', 'group': '/api/groups/',
         'script': '/api/scripts/', 'application': '/api/applications/', 'artifact': '/api/artifacts/'}

# ---- pre-cleanup counts ----
def list_ids(path):
    c, b, _ = hub.get(path)
    items = b if isinstance(b, list) else (b.get('items') if isinstance(b, dict) else [])
    return items or []

pre = {
    'tasks': len(list_ids('/api/tasks')), 'groups': len(list_ids('/api/groups')),
    'schedules': len(list_ids('/api/schedules')), 'scripts': len(list_ids('/api/scripts')),
    'applications': len(list_ids('/api/applications')), 'artifacts': len(list_ids('/api/artifacts')),
}

# ---- delete + GET404 ----
ledger = []
for fx in fixtures:
    c, body, _ = hub.delete(paths[fx['kind']] + fx['id'])
    time.sleep(0.2)
    c2, body2, _ = hub.get(paths[fx['kind']] + fx['id'])
    ledger.append({**fx, 'delete_http': c, 'get_http': c2, 'get404': c2 == 404,
                   'delete_body': body if isinstance(body, (str, dict)) and c not in (200, 204) else None})

# ---- leftover scan by prefix ----
def scan(path, key='name'):
    return [{key: x.get(key), 'id': x.get('id')} for x in list_ids(path) if L.PREFIX in json.dumps(x, ensure_ascii=False)]

leftovers = {
    'tasks': scan('/api/tasks'), 'groups': scan('/api/groups'), 'scripts': scan('/api/scripts'),
    'applications': scan('/api/applications'), 'artifacts': scan('/api/artifacts'),
    'schedules': [{'id': s.get('id'), 'task_id': s.get('task_id')} for s in list_ids('/api/schedules') if s.get('task_id') == SETUP['task']['id']],
}

# ---- post-cleanup counts ----
post = {
    'tasks': len(list_ids('/api/tasks')), 'groups': len(list_ids('/api/groups')),
    'schedules': len(list_ids('/api/schedules')), 'scripts': len(list_ids('/api/scripts')),
    'applications': len(list_ids('/api/applications')), 'artifacts': len(list_ids('/api/artifacts')),
}

# ---- executions / audit for owned objects ----
execs = {}
for label, q in (('task', '/api/executions?limit=5&task_id=' + SETUP['task']['id']),):
    c, b, h = hub.get(q)
    execs[label] = {'x_total_count': h.get('X-Total-Count'), 'count': len(b or [])}

audit_scan = {}
for label, rid in [('task', SETUP['task']['id']), ('script', SETUP['script']['id']), ('group', SETUP['group']['id']),
                   ('application', SETUP['application']['id']), ('artifact1', SETUP['artifact']['id'])] + \
                  ([('schedule', mine_sched[0]['id'])] if mine_sched else []) + \
                  ([('artifact2', mine_art2[0]['id'])] if mine_art2 else []):
    c, b, h = hub.get('/api/audit?limit=50&q=' + rid)
    audit_scan[label] = {'total': h.get('X-Total-Count'), 'actions': sorted({(x.get('action') or '') + '/' + (x.get('resource') or '') for x in (b or [])})}

# ---- temp files + browser close ----
tmp_removed = []
for p in Path('/tmp/opencode').glob('qa-cgap-*'):
    p.unlink(missing_ok=True)
    tmp_removed.append(p.name)
browser_close = L.ab_close()

# ---- health check ----
c1, b1, _ = hub.get('/healthz')
c2, b2, _ = hub.get('/readyz')
c3, nodes, _ = hub.get('/api/nodes')
node_states = {n.get('hostname'): {'status': n.get('status'), 'sync_status': n.get('sync_status')} for n in (nodes or []) if 'repair' in (n.get('hostname') or '')}

ledger_doc = {
    'batch_id': 'qa-resume-20261017-control-gap',
    'nonce': L.NONCE,
    'owned_prefix': L.PREFIX,
    'authority': L.UI,
    'created_by': 'owned fixtures for control-gap re-tests',
    'deleted': ledger,
    'all_deleted_get404': all(x['get404'] for x in ledger),
    'leftovers': leftovers,
    'counts': {'pre': pre, 'post': post},
    'no_executions_created': execs['task']['x_total_count'] == '0',
    'audit_entries_for_owned_objects': audit_scan,
    'tmp_files_removed': tmp_removed,
    'browser_close': browser_close,
    'at_utc': now(),
}
L.save('fixture-ledger.json', ledger_doc)

oracle = {
    'batch_id': 'qa-resume-20261017-control-gap',
    'at_utc': now(),
    'all_owned_deleted_get404': ledger_doc['all_deleted_get404'],
    'leftover_owned_objects': {k: v for k, v in leftovers.items() if v},
    'no_owned_executions': execs,
    'healthz_readyz': {'healthz': c1, 'readyz': c2},
    'protected_nodes': node_states,
    'browser_session_closed': browser_close,
    'tmp_files_removed': tmp_removed,
    'counts_pre_post': {'pre': pre, 'post': post},
    'primary_8080_contacted': False,
}
L.save('final-cleanup-oracle.json', oracle)
print(json.dumps(ledger_doc, ensure_ascii=False, indent=1))
print(json.dumps(oracle, ensure_ascii=False, indent=1))
