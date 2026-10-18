#!/usr/bin/env python3
"""Focused reproduction for the stuck target delivery observed in phase4.

Scenario: two fresh owned children on native-1 (distinct node_ip, both direct
websocket), one 4 KiB source file, one transfer with two targets:
  * child d2 -> valid destination (expected SUCCESS)
  * child d1 -> ENOTDIR destination (expected FAILED)
The script polls the transfer and dumps both agents' logs so the delivery path
can be observed live.  Nothing here is a product-code change.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import child_agent  # noqa: E402
import hubapi  # noqa: E402

NONCE = 'xferrepro'
PREFIX = 'qa-g6-%s-' % NONCE
IPS = {'d1': '192.168.100.201', 'd2': '192.168.100.200'}


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'children': {}, 'steps': []}
    kids = {}
    for label in ('d1', 'd2'):
        code, en = hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + label, 'node_ip': IPS[label],
            'hub_address': 'http://192.168.100.209:18080'})
        kid = child_agent.OwnedChild(NONCE, 0, en['node_id'], en['agent_id'], PREFIX + label,
                                     IPS[label], hub.cred['registration_token'], label=label)
        kids[label] = kid
        out['children'][label] = {'node_id': en['node_id'], 'start': kid.write_config_and_start()}
        for _ in range(60):
            st, n = hub.get('/api/nodes/' + hub.quote(en['node_id']))
            if isinstance(n, dict) and n.get('status') == 'online' and n.get('sync_status') == 'synced':
                break
            time.sleep(1)
        out['children'][label]['node_after'] = {k: n.get(k) for k in ('status', 'sync_status', 'deployment_mode')}
    src = kids['d1'].fixtures + '/xfer/repro-source.bin'
    blocker = kids['d1'].fixtures + '/xfer/repro-blocker'
    out['src'] = kids['d1'].make_file(src, 4096, seed=9)
    out['blocker'] = kids['d1'].write_small_file(blocker, 'blocker\n')
    dst_ok = kids['d2'].fixtures + '/xfer/repro-dst.bin'
    dst_bad = blocker + '/nope.bin'
    code, t = hub.post('/api/transfers', {
        'source_node_id': kids['d1'].node_id, 'source_path': src,
        'targets': [{'node_id': kids['d2'].node_id, 'destination_path': dst_ok},
                    {'node_id': kids['d1'].node_id, 'destination_path': dst_bad}]})
    out['create'] = {'http': code, 'id': t.get('id') if isinstance(t, dict) else t}
    tid = t['id']
    for i in range(30):
        time.sleep(2)
        cur = hub.json('/api/transfers/' + hub.quote(tid))
        snap = {'t': i * 2, 'status': cur['status'],
                'targets': [{'node': x['node_id'][:8], 'status': x['status'], 'error': x.get('error')}
                            for x in cur['targets']]}
        out['steps'].append(snap)
        print(json.dumps(snap, ensure_ascii=False), flush=True)
        if all(x['status'] in ('SUCCESS', 'FAILED', 'CANCELED') for x in cur['targets']):
            break
    out['dst_ok_state'] = kids['d2'].file_state(dst_ok)
    out['d1_log_tail'] = kids['d1'].log_tail(15)
    out['d2_log_tail'] = kids['d2'].log_tail(15)
    out['d2_local_db'] = kids['d2'].db_query(
        "SELECT name FROM sqlite_master WHERE type='table'")['rows']
    # cleanup: cancel the transfer (no DELETE endpoint), remove children, delete nodes
    out['cancel'] = hub.post('/api/transfers/%s/cancel' % hub.quote(tid), {})[0]
    for label, kid in kids.items():
        out['children'][label]['remove'] = kid.remove()
    time.sleep(2)
    for label, kid in kids.items():
        out['children'][label]['node_delete_http'] = hub.delete('/api/nodes/' + hub.quote(kid.node_id))[0]
    hubapi.write_json(str(Path(__file__).resolve().parent / 'evidence' / 'repro-xfer-second-node.json'), out)
    print('WROTE evidence/repro-xfer-second-node.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
