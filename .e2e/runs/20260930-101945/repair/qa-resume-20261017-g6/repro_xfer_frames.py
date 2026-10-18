#!/usr/bin/env python3
"""Second, instrumented reproduction: both owned children connect through their
own harness frame proxy, so the websocket frame ledger shows exactly which
frames the Hub sends to the target node and whether the target answers.

Fault injection is NOT used here (pass-through only); the proxies exist purely as
observers.  Each child keeps its own proxy port, released at the end.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import child_agent  # noqa: E402
import hubapi  # noqa: E402
from frame_proxy import FrameProxy  # noqa: E402

NONCE = 'xferobs'
PREFIX = 'qa-g6-%s-' % NONCE
IPS = {'p1': '192.168.100.199', 'p2': '192.168.100.198'}
HUB = '192.168.100.209'


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'children': {}, 'steps': []}
    kids, proxies = {}, {}
    for label in ('p1', 'p2'):
        proxy = FrameProxy('ws://%s:18080' % HUB, label=label)
        port = proxy.start()
        proxies[label] = proxy
        code, en = hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + label, 'node_ip': IPS[label],
            'hub_address': 'http://%s:18080' % HUB})
        kid = child_agent.OwnedChild(NONCE, port, en['node_id'], en['agent_id'], PREFIX + label,
                                     IPS[label], hub.cred['registration_token'], label=label)
        kids[label] = kid
        out['children'][label] = {'node_id': en['node_id'], 'proxy_port': port,
                                  'start': kid.write_config_and_start()}
        for _ in range(60):
            st, n = hub.get('/api/nodes/' + hub.quote(en['node_id']))
            if isinstance(n, dict) and n.get('status') == 'online' and n.get('sync_status') == 'synced':
                break
            time.sleep(1)
        out['children'][label]['node_after'] = {k: n.get(k) for k in ('status', 'sync_status', 'deployment_mode')}
    src = kids['p1'].fixtures + '/xfer/obs-source.bin'
    blocker = kids['p1'].fixtures + '/xfer/obs-blocker'
    out['src'] = kids['p1'].make_file(src, 4096, seed=4)
    out['blocker'] = kids['p1'].write_small_file(blocker, 'blocker\n')
    dst_ok = kids['p2'].fixtures + '/xfer/obs-dst.bin'
    dst_bad = blocker + '/nope.bin'
    code, t = hub.post('/api/transfers', {
        'source_node_id': kids['p1'].node_id, 'source_path': src,
        'targets': [{'node_id': kids['p2'].node_id, 'destination_path': dst_ok},
                    {'node_id': kids['p1'].node_id, 'destination_path': dst_bad}]})
    tid = t['id']
    out['create'] = {'http': code, 'id': tid}
    for i in range(12):
        time.sleep(2)
        cur = hub.json('/api/transfers/' + hub.quote(tid))
        snap = {'t': i * 2, 'status': cur['status'],
                'targets': [{'node': x['node_id'][:8], 'status': x['status'], 'error': x.get('error')}
                            for x in cur['targets']]}
        out['steps'].append(snap)
        print(json.dumps(snap, ensure_ascii=False), flush=True)
        if all(x['status'] in ('SUCCESS', 'FAILED', 'CANCELED') for x in cur['targets']):
            break
    out['dst_ok_state'] = kids['p2'].file_state(dst_ok)
    for label in ('p1', 'p2'):
        p = proxies[label]
        st = p.state()
        out['children'][label]['frame_counts'] = st['counts_by_type']
        out['children'][label]['frames_total'] = st['frames_total']
        out['children'][label]['delivery_frames'] = [
            {'seq': f['seq'], 'direction': f['direction'], 'type': f['type'],
             'execution_id': f.get('execution_id'), 'at_utc': f['at_utc']} for f in p.frames
            if f['type'] in ('FILE_DELIVERY_REQUEST', 'FILE_DELIVERY_RESULT', 'FILE_UPLOAD_REQUEST',
                             'FILE_UPLOAD_RESULT', 'FILE_TRANSFER_CANCEL')]
        out['children'][label]['log_tail'] = kids[label].log_tail(12)
    out['cancel'] = hub.post('/api/transfers/%s/cancel' % hub.quote(tid), {})[0]
    for label, kid in kids.items():
        out['children'][label]['remove'] = kid.remove()
        proxies[label].stop()
    time.sleep(2)
    for label, kid in kids.items():
        out['children'][label]['node_delete_http'] = hub.delete('/api/nodes/' + hub.quote(kid.node_id))[0]
    hubapi.write_json(str(Path(__file__).resolve().parent / 'evidence' / 'repro-xfer-frame-ledger.json'), out)
    print('WROTE evidence/repro-xfer-frame-ledger.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
