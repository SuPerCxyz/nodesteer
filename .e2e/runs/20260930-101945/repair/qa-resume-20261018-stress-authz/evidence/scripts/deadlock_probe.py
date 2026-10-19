#!/usr/bin/env python3
"""Deadlock evidence probe: connection-pool exhaustion via nested queries in
ListFileTransfers. Saves machine-checkable JSON evidence.

Boundary test: N concurrent GET /api/transfers with barrier start.
- 7 concurrent -> all 200
- 8 concurrent -> all hang (client timeout), and during the hang a plain
  GET /api/tasks also stalls (hub-wide DB starvation).
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qalib import Client, fire, load_creds  # noqa: E402

creds = load_creds()
out = {'base_url': creds['base_url'], 'probes': []}
admin = Client(creds['host'], creds['port'], timeout=10)
admin.login(creds['admin_username'], creds['admin_password'])


def jb(r):
    try:
        return json.loads(r.body)
    except Exception:
        return {}


# fixtures: two nodes + one transfer (list requires at least one row)
ids = {}
for nm, ip in [('qa-dl-src', '10.95.0.1'), ('qa-dl-dst', '10.95.0.2')]:
    r = admin.request('POST', '/api/nodes/enrollment', {'node_name': nm, 'node_ip': ip})
    assert r.status == 200, r.snippet()
    ids[nm] = jb(r)['node_id']
r = admin.request('POST', '/api/transfers', {
    'source_node_id': ids['qa-dl-src'], 'source_path': '/tmp/qa-dl-src',
    'targets': [{'node_id': ids['qa-dl-dst'], 'destination_path': '/tmp/qa-dl-dst'}]})
assert r.status == 200, r.snippet()
out['fixtures'] = {'nodes': ids, 'transfer': jb(r)['id']}

for n, tmo in ((7, 10), (8, 8)):
    c = Client(creds['host'], creds['port'], timeout=tmo)
    c.token = admin.token
    results = {}

    def run():
        try:
            res = fire(n, lambda i: c.request('GET', '/api/transfers'))
            results['rows'] = [{'status': x.status, 'ms': round(x.ms, 1)} for x in res]
        except Exception as exc:
            results['rows'] = [{'exception': type(exc).__name__}] * n

    th = threading.Thread(target=run)
    th.start()
    collateral = []
    if n == 8:
        time.sleep(1.5)
        for path in ('/api/me', '/api/tasks', '/api/nodes'):
            pc = Client(creds['host'], creds['port'], timeout=5)
            pc.token = admin.token
            try:
                r2 = pc.request('GET', path)
                collateral.append({'path': path, 'status': r2.status, 'ms': round(r2.ms, 1)})
            except Exception as exc:
                collateral.append({'path': path, 'exception': type(exc).__name__})
    th.join()
    out['probes'].append({'concurrent': n, 'client_timeout_s': tmo,
                          'results': results.get('rows'), 'collateral_during_hang': collateral})
    print('concurrent=%d -> %s' % (n, json.dumps(results.get('rows'))[:200]), flush=True)
    time.sleep(1)

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'deadlock-probe.json'), 'w') as f:
    json.dump(out, f, indent=1)
print('DONE deadlock probe')
