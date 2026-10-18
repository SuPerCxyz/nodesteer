#!/usr/bin/env python3
"""Rescue cleanup for a partially executed sys-b12 attempt.

Removes every owned fixture by nonce prefix: Hub applications/artifacts/nodes, owned child
Agents and their host footprint (roots, /opt dirs, /tmp escape dir, symlinks, chattr flags,
owned tmpfs mounts). Idempotent; safe to run repeatedly.
"""
import json
import time
from pathlib import Path

import hostapi
import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb12-%s-' % NONCE
TAG = 'qa-sysb12-%s' % NONCE
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    out = {'at_utc': NOW(), 'nonce': NONCE, 'owned_prefix': PREFIX, 'steps': {}}
    hub = hubapi.Hub()
    hub.login()

    # 1) host-side: release seams, stop processes, remove owned dirs
    steps = {}
    for suffix, label in (('', 'child-a'), ('b', 'child-b')):
        child = hostapi.OwnedChild(NONCE, suffix)
        try:
            probe = child.probe(targets=[], dirs=[])
            steps[label + '_mounts_before'] = probe.get('mounts')
        except Exception as exc:
            steps[label + '_probe_error'] = str(exc)[:200]
        try:
            steps[label + '_chattr_minus_opt'] = child.seam('chattr_minus', child.opt_root).get('result')
            steps[label + '_umount'] = child.seam('umount', child.opt_root).get('result')
        except Exception as exc:
            steps[label + '_seam_error'] = str(exc)[:200]
        try:
            steps[label + '_remove'] = child.remove()
        except Exception as exc:
            steps[label + '_remove_error'] = str(exc)[:300]
    # owned dirs outside the child roots
    ca = hostapi.OwnedChild(NONCE, '')
    for d in ('/opt/%s' % TAG, '/opt/%s-b' % TAG, '/etc/%s' % TAG, '/tmp/%s-escape' % TAG):
        try:
            steps['rm_' + d] = ca.seam('rm_rf', d).get('result')
        except Exception as exc:
            steps['rm_' + d + '_error'] = str(exc)[:200]
    out['steps']['host'] = steps

    # 2) hub-side: owned applications, artifacts, nodes
    hub_steps = {}
    owned_apps = [a for a in hub.json('/api/applications') if (a.get('name') or '').startswith(PREFIX)]
    hub_steps['applications'] = []
    for a in owned_apps:
        code, body = hub.delete('/api/applications/' + hub.quote(a['id']))
        hub_steps['applications'].append({'id': a['id'], 'name': a['name'], 'delete_http': code})
    time.sleep(3)
    owned_arts = [a for a in hub.json('/api/artifacts') if (a.get('name') or '').startswith(PREFIX)]
    hub_steps['artifacts'] = []
    for a in owned_arts:
        code, body = hub.delete('/api/artifacts/' + hub.quote(a['id']))
        gcode, _ = hub.get('/api/artifacts/' + hub.quote(a['id']))
        hub_steps['artifacts'].append({'id': a['id'], 'delete_http': code, 'get_http': gcode})
    owned_nodes = [n for n in hub.json('/api/nodes') if (n.get('hostname') or '').startswith(PREFIX)]
    hub_steps['nodes'] = []
    for n in owned_nodes:
        code, body = hub.delete('/api/nodes/' + hub.quote(n['id']))
        gcode, _ = hub.get('/api/nodes/' + hub.quote(n['id']))
        hub_steps['nodes'].append({'id': n['id'], 'hostname': n.get('hostname'),
                                   'delete_http': code, 'delete_body': body, 'get_http': gcode})
    owned_tr = [t for t in hub.json('/api/transfers') if (t.get('source_path') or '').startswith('/opt/' + TAG)]
    hub_steps['transfers_canceled'] = []
    for t in owned_tr:
        if t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED'):
            code, _ = hub.post('/api/transfers/%s/cancel' % hub.quote(t['id']))
            hub_steps['transfers_canceled'].append({'id': t['id'], 'cancel_http': code})
    out['steps']['hub'] = hub_steps

    # 3) verification
    time.sleep(2)
    base = hostapi.OwnedChild(NONCE).host_baseline()
    arts = hub.json('/api/artifacts')
    apps = hub.json('/api/applications')
    nodes = hub.json('/api/nodes')
    out['verify'] = {
        'native2': base,
        'hub_leftovers': {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        },
    }
    out['clean'] = (not base['root_exists'] and not base['opt_root_exists']
                    and not base['owned_child_processes'] and not base['owned_units']
                    and not base['owned_mounts'] and not base['etc_systemd_owned_leftovers']
                    and not base['tmp_owned_entries'] and not base['etc_owned_entries']
                    and not any(out['verify']['hub_leftovers'].values()))
    hubapi.write_json('rescue-cleanup.json', out)
    print(json.dumps({'clean': out['clean'], 'hub_leftovers': out['verify']['hub_leftovers'],
                      'root_exists': base['root_exists'], 'opt_root_exists': base['opt_root_exists'],
                      'owned_child_processes': base['owned_child_processes'],
                      'owned_mounts': base['owned_mounts'],
                      'etc_owned_entries': base['etc_owned_entries'],
                      'tmp_owned_entries': base['tmp_owned_entries']}, indent=1))
    return 0 if out['clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
