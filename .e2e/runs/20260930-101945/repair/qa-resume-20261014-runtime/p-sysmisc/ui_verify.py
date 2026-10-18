#!/usr/bin/env python3
"""E2E-16 UI feedback sub-run: observe a freshly enrolled node in the repair Web UI
across the pending -> online transition (label 'u').

Drives the real NodeSteer SPA at http://192.168.100.209:18080 through agent-browser
during the same enrollment/HELLO lifecycle used elsewhere in this batch. The node is
owned (prefix qa-psm-<nonce>-) and removed at the end.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import child as childmod  # noqa: E402
import frame_proxy  # noqa: E402
import hubapi  # noqa: E402

NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-psm-%s-' % NONCE
UI = 'http://192.168.100.209:18080'
HOSTNAME = PREFIX + 'child-u'
NODE_IP = PREFIX + 'u.invalid'
os.environ['PATH'] = os.environ.get('PATH', '') + ':/home/superc/.npm-global/bin'
SEARCH_SEL = "input[placeholder*='搜索主机名']"


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ab(*args, timeout=150):
    r = subprocess.run(['agent-browser'] + list(args), capture_output=True, text=True, timeout=timeout)
    out = (r.stdout or '').strip()
    if r.stderr and r.stderr.strip():
        out += ' | ERR:' + r.stderr.strip()[:200]
    return out


def row_status():
    js = ("(() => {const rows=[...document.querySelectorAll('tbody tr')];"
          "const r=rows.find(t=>t.innerText.includes(%s));"
          "return r? r.innerText.replace(/\\s+/g,' ') : 'NOT_FOUND rows='+rows.length;})()"
          % json.dumps(HOSTNAME))
    return ab('eval', js)


def search_and_read():
    ab('fill', SEARCH_SEL, HOSTNAME)
    time.sleep(3)
    return row_status()


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': now_utc(), 'ui': UI, 'hostname': HOSTNAME, 'steps': {}}

    # login to the SPA (credentials read from the private file, never persisted)
    base, cred = hubapi.load_authority()
    started_at = now_utc()
    try:
        code, enr = hub.post('/api/nodes/enrollment',
                             {'node_name': HOSTNAME, 'node_ip': NODE_IP, 'hub_address': UI})
        assert code == 200 and enr.get('node_id'), 'enrollment failed %s %s' % (code, enr)
        node_id = enr['node_id']
        node_pending = (hub.get('/api/nodes/' + hub.quote(node_id))[1] or {})
        out['steps']['pending'] = {'enrolled_at': started_at, 'node_api_status': node_pending.get('status')}

        # --- UI: pending ---
        ab('open', UI + '/sign-in')
        ab('find', 'label', '用户名', 'fill', cred['admin_username'])
        ab('find', 'label', '密码', 'fill', cred['admin_password'])
        ab('find', 'role', 'button', 'click', '登录')
        time.sleep(4)
        ab('open', UI + '/agents')
        time.sleep(3)
        ui_pending = search_and_read()
        ab('screenshot', str(HERE / 'evidence' / 'ui-node-pending.png'))
        out['steps']['pending']['ui_row_text'] = ui_pending
        out['steps']['pending']['ui_pending_observed'] = '等待中' in ui_pending

        # --- start the owned child; observe HELLO -> online ---
        proxy = frame_proxy.FrameProxy('ws://192.168.100.209:18080', label='u')
        proxy.start()
        ch = childmod.OwnedChild(NONCE, proxy.port, node_id, enr['agent_id'], HOSTNAME, NODE_IP,
                                 hub.cred['registration_token'], label='u')
        ch.prepare()
        ch.write_config_and_start()
        deadline = time.time() + 90
        online = None
        while time.time() < deadline:
            n = (hub.get('/api/nodes/' + hub.quote(node_id))[1] or {})
            if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                online = n
                break
            time.sleep(1.0)
        out['steps']['online'] = {'node_api_status': (online or {}).get('status'),
                                  'node_api_sync_status': (online or {}).get('sync_status'),
                                  'hello_frames': len(proxy.frames_of('HELLO')),
                                  'hello_ack_accepted': [f.get('accepted') for f in proxy.frames_of('HELLO_ACK')]}

        # --- UI: online ---
        ab('open', UI + '/agents')
        time.sleep(3)
        ui_online = search_and_read()
        ab('screenshot', str(HERE / 'evidence' / 'ui-node-online.png'))
        out['steps']['online']['ui_row_text'] = ui_online
        out['steps']['online']['ui_online_observed'] = '在线' in ui_online

        out['assertions'] = {
            'ui_shows_pending_before_hello': out['steps']['pending']['ui_pending_observed'],
            'ui_shows_online_after_hello': out['steps']['online']['ui_online_observed'],
            'api_online_synced': out['steps']['online']['node_api_status'] == 'online'
                                 and out['steps']['online']['node_api_sync_status'] == 'synced',
        }
        out['assertions_pass'] = all(out['assertions'].values())
        out['ui_screenshots'] = ['evidence/ui-node-pending.png', 'evidence/ui-node-online.png']
    finally:
        # cleanup: stop child + proxy, delete node
        try:
            if 'ch' in dir() and ch is not None:
                out['cleanup'] = {'child_remove': ch.remove()}
        except Exception as exc:
            out.setdefault('cleanup', {})['child_remove_error'] = str(exc)
        try:
            if 'proxy' in dir() and proxy is not None:
                proxy.stop()
        except Exception as exc:
            out.setdefault('cleanup', {})['proxy_stop_error'] = str(exc)
        if 'node_id' in dir() and node_id:
            code, _ = hub.delete('/api/nodes/' + hub.quote(node_id))
            gcode, _ = hub.get('/api/nodes/' + hub.quote(node_id))
            out.setdefault('cleanup', {})['node_delete'] = {'id': node_id, 'delete_http': code, 'get_http': gcode}
    hubapi.write_json('evidence/phase10-ui-feedback-u.json', out)
    print(json.dumps({'assertions': out.get('assertions'), 'pending_row': out['steps']['pending'].get('ui_row_text'),
                      'online_row': out['steps']['online'].get('ui_row_text'),
                      'cleanup': out.get('cleanup')}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
