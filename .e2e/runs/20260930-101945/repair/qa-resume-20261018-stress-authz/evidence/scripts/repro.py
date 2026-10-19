#!/usr/bin/env python3
"""Focused repro: concurrent schedule delete (SQLITE_BUSY + 409 + raw SQL)
and concurrent node delete (hang/timeout)."""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qalib import Client, fire, load_creds  # noqa: E402

creds = load_creds()
admin = Client(creds['host'], creds['port'], timeout=45)
admin.login(creds['admin_username'], creds['admin_password'])
out = {'schedules': [], 'nodes': []}


def jb(r):
    try:
        return json.loads(r.body)
    except Exception:
        return {}


# --- fixture task ---
r = admin.request('POST', '/api/tasks', {
    'name': 'qa-repro-sched-host-%s' % uuid.uuid4().hex[:6], 'type': 'command',
    'command': 'true', 'target': {'type': 'label', 'label_key': 'qa-repro'}, 'enabled': False})
task_id = jb(r)['id']

# --- schedules: create 48, delete 16 at a time x3 ---
sched_ids = []
for i in range(48):
    r = admin.request('POST', '/api/schedules', {
        'task_id': task_id, 'type': 'interval', 'interval_sec': 3600 + i,
        'timezone': 'UTC', 'execution_owner': 'hub', 'offline_policy': 'hub_online_required',
        'misfire_policy': 'skip', 'enabled': False})
    assert r.status == 200, r.snippet()
    sched_ids.append(jb(r)['id'])

for rnd in range(3):
    batch = sched_ids[rnd * 16:(rnd + 1) * 16]
    res = fire(16, lambda i: admin.request('DELETE', '/api/schedules/%s' % batch[i]))
    row = {'round': rnd, 'statuses': {}, 'ms': [], 'errors': []}
    for r in res:
        row['statuses'][str(r.status)] = row['statuses'].get(str(r.status), 0) + 1
        row['ms'].append(round(r.ms, 1))
        if r.status != 200:
            row['errors'].append({'status': r.status, 'body': r.snippet(200)})
    row['max_ms'] = max(row['ms'])
    out['schedules'].append(row)
    print('[sched-delete] r%d %s max=%.0fms' % (rnd, row['statuses'], row['max_ms']), flush=True)

# --- nodes: enroll 16 unique, delete concurrently ---
node_ids = []
for i in range(16):
    r = admin.request('POST', '/api/nodes/enrollment', {
        'node_name': 'qa-repro-node-%d-%s' % (i, uuid.uuid4().hex[:6]),
        'node_ip': '10.97.%d.%d' % (1, i + 1)})
    assert r.status == 200, r.snippet()
    node_ids.append(jb(r)['node_id'])

res = fire(16, lambda i: admin.request('DELETE', '/api/nodes/%s' % node_ids[i]))
row = {'statuses': {}, 'ms': [], 'errors': [], 'timeouts': 0}
for r in res:
    row['statuses'][str(r.status)] = row['statuses'].get(str(r.status), 0) + 1
    row['ms'].append(round(r.ms, 1))
    if r.status != 200:
        row['errors'].append({'status': r.status, 'body': r.snippet(200)})
row['max_ms'] = max(row['ms'])
out['nodes'].append(row)
print('[node-delete] %s max=%.0fms' % (row['statuses'], row['max_ms']), flush=True)

# leftovers
r = admin.request('GET', '/api/nodes')
nodes = jb(r) if isinstance(jb(r), list) else jb(r).get('nodes', [])
left = [n['hostname'] for n in nodes if n['hostname'].startswith('qa-repro-node')]
r2 = admin.request('GET', '/api/schedules')
scheds = jb(r2) if isinstance(jb(r2), list) else []
sleft = [s['id'] for s in scheds if s.get('task_id') == task_id]
out['leftover_nodes'] = left
out['leftover_schedules'] = sleft
print('leftover nodes=%d schedules=%d' % (len(left), len(sleft)))

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'repro-results.json'), 'w') as f:
    json.dump(out, f, indent=1)
print('DONE repro')
