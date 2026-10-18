#!/usr/bin/env python3
"""Third reproduction: after the remote-target delivery hangs, probe whether the
Hub's websocket session for that node is still usable at all.

Adds to repro_xfer_frames.py:
  * the full proxy event log (connection_open/closed, relay_error) per child;
  * every hub->agent frame for the target node, with timestamps;
  * a liveness probe after the hang: a workspace-wide CHANGE_NOTIFICATION
    (node label write) and a node-status notification must still reach the
    target node if its session is alive.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import child_agent  # noqa: E402
import hubapi  # noqa: E402
from frame_proxy import FrameProxy  # noqa: E402

NONCE = 'xferlive'
PREFIX = 'qa-g6-%s-' % NONCE
IPS = {'q1': '192.168.100.197', 'q2': '192.168.100.196'}
HUB = '192.168.100.209'


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'children': {}, 'steps': []}
    kids, proxies = {}, {}
    for label in ('q1', 'q2'):
        proxy = FrameProxy('ws://%s:18080' % HUB, label=label)
        port = proxy.start()
        proxies[label] = proxy
        code, en = hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + label, 'node_ip': IPS[label], 'hub_address': 'http://%s:18080' % HUB})
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
        out['children'][label]['node_after'] = {k: n.get(k) for k in ('status', 'sync_status')}
    src = kids['q1'].fixtures + '/xfer/live-source.bin'
    blocker = kids['q1'].fixtures + '/xfer/live-blocker'
    out['src'] = kids['q1'].make_file(src, 4096, seed=2)
    out['blocker'] = kids['q1'].write_small_file(blocker, 'blocker\n')
    dst_ok = kids['q2'].fixtures + '/xfer/live-dst.bin'
    code, t = hub.post('/api/transfers', {
        'source_node_id': kids['q1'].node_id, 'source_path': src,
        'targets': [{'node_id': kids['q2'].node_id, 'destination_path': dst_ok},
                    {'node_id': kids['q1'].node_id, 'destination_path': blocker + '/nope.bin'}]})
    tid = t['id']
    out['create'] = {'http': code, 'id': tid}
    for i in range(8):
        time.sleep(2)
        cur = hub.json('/api/transfers/' + hub.quote(tid))
        snap = {'t': i * 2, 'status': cur['status'],
                'targets': [{'node': x['node_id'][:8], 'status': x['status']} for x in cur['targets']]}
        out['steps'].append(snap)
        print(json.dumps(snap, ensure_ascii=False), flush=True)
        if all(x['status'] in ('SUCCESS', 'FAILED', 'CANCELED') for x in cur['targets']):
            break
    # ---- liveness probes against the hung target node
    p2 = proxies['q2']
    before = len([f for f in p2.frames if f['direction'] == 'hub_to_agent'])
    out['liveness'] = {'hub_to_agent_frames_before_probe': before}
    out['liveness']['label_write'] = hub.post('/api/nodes/' + hub.quote(kids['q2'].node_id),
                                              {'labels': {PREFIX + 'probe': '1'}})[0]
    time.sleep(4)
    after = [f for f in p2.frames if f['direction'] == 'hub_to_agent']
    out['liveness']['hub_to_agent_frames_after_label_probe'] = len(after)
    out['liveness']['frames_after_probe'] = [{'seq': f['seq'], 'type': f['type'], 'at_utc': f['at_utc']}
                                             for f in after[before:]]
    out['liveness']['status_write'] = hub.post('/api/nodes/' + hub.quote(kids['q2'].node_id),
                                               {'status': 'maintenance'})[0]
    time.sleep(3)
    after2 = [f for f in p2.frames if f['direction'] == 'hub_to_agent']
    out['liveness']['hub_to_agent_frames_after_status_probe'] = len(after2)
    out['liveness']['status_frames_after_probe'] = [{'seq': f['seq'], 'type': f['type'], 'at_utc': f['at_utc']}
                                                    for f in after2[len(after):]]
    out['liveness']['node_after_probes'] = {k: (hub.get('/api/nodes/' + hub.quote(kids['q2'].node_id))[1] or {}).get(k)
                                            for k in ('status', 'sync_status', 'last_seen')}
    hub.post('/api/nodes/' + hub.quote(kids['q2'].node_id), {'status': 'online'})
    # ---- per-child evidence
    for label in ('q1', 'q2'):
        p = proxies[label]
        out['children'][label]['all_frames'] = [
            {'seq': f['seq'], 'direction': f['direction'], 'type': f['type'], 'at_utc': f['at_utc']}
            for f in p.frames]
        out['children'][label]['events'] = p.events
        out['children'][label]['log_tail'] = kids[label].log_tail(8)
    out['dst_ok_state'] = kids['q2'].file_state(dst_ok)
    out['cancel'] = hub.post('/api/transfers/%s/cancel' % hub.quote(tid), {})[0]
    for label, kid in kids.items():
        out['children'][label]['remove'] = kid.remove()
        proxies[label].stop()
    time.sleep(2)
    for label, kid in kids.items():
        out['children'][label]['node_delete_http'] = hub.delete('/api/nodes/' + hub.quote(kid.node_id))[0]
    hubapi.write_json(str(Path(__file__).resolve().parent / 'evidence' / 'repro-xfer-liveness.json'), out)
    print('WROTE evidence/repro-xfer-liveness.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
