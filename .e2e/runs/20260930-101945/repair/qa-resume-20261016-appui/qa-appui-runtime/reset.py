#!/usr/bin/env python3
"""Owned-fixture reset for the appui batch. Deletes ONLY `qa-appui-<nonce>-*`
fixtures (owned Applications through the product path, then artifacts, then
nodes) and removes ONLY the owned host roots/units. Never touches other
batches' fixtures, the protected nodes, or the protected Agent units."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hostlib  # noqa: E402
import hubapi  # noqa: E402

HERE = Path(__file__).resolve().parent
NONCE = (HERE.parent / 'qa-appui-runtime' / '.nonce').read_text().strip() \
    if (HERE.parent / 'qa-appui-runtime' / '.nonce').is_file() else (HERE / '.nonce').read_text().strip()
PREFIX = NONCE + '-'
UNIT = 'nodesteer-' + PREFIX + 'app.service'
OPT = '/opt/' + PREFIX.rstrip('-')
BIN = OPT + '/app/bin/app'
CFG = OPT + '/app/config/app.env'


def main():
    h = hubapi.Hub()
    h.login()
    report = {'nonce': NONCE, 'prefix': PREFIX, 'apps': [], 'artifacts': [], 'nodes': [], 'hosts': {}}
    apps = h.json('/api/applications')
    for a in apps:
        if (a.get('name') or '').startswith(PREFIX):
            d = h.delete('/api/applications/' + h.quote(a['id']))
            g = h.get('/api/applications/' + h.quote(a['id']))[0]
            report['apps'].append({'id': a['id'], 'name': a['name'], 'delete': d[0], 'get': g})
    time.sleep(3)
    arts = h.json('/api/artifacts')
    for a in arts:
        if (a.get('name') or '').startswith(PREFIX):
            d = h.delete('/api/artifacts/' + h.quote(a['id']))
            g = h.get('/api/artifacts/' + h.quote(a['id']))[0]
            report['artifacts'].append({'id': a['id'], 'name': a['name'], 'delete': d[0], 'get': g})
    nodes = h.json('/api/nodes')
    for n in nodes:
        if (n.get('hostname') or '').startswith(PREFIX):
            code, body = 0, None
            for _ in range(4):
                code, body = h.delete('/api/nodes/' + h.quote(n['id']))
                if h.get('/api/nodes/' + h.quote(n['id']))[0] == 404:
                    break
                time.sleep(2)
            report['nodes'].append({'id': n['id'], 'hostname': n['hostname'], 'delete': code,
                                    'get': h.get('/api/nodes/' + h.quote(n['id']))[0]})
    for key, hk, tag in (('a', 'n1', ''), ('b', 'n3', '-b')):
        ch = hostlib.OwnedChild(NONCE, hk, tag)
        report['hosts'][hk] = {'unit_cleanup': ch.unit_cleanup(UNIT, BIN, CFG, ch.opt_root),
                               'remove': ch.remove()}
        b = ch.host_baseline()
        report['hosts'][hk]['baseline_clean'] = (
            not b['owned_units'] and not b['etc_systemd_owned'] and not b['root_exists']
            and not b['opt_root_exists'] and not b['owned_child_processes']
            and not b['global_owned_prefix_scan'])
    left = {'apps': [a['id'] for a in h.json('/api/applications') if (a.get('name') or '').startswith(PREFIX)],
            'artifacts': [a['id'] for a in h.json('/api/artifacts') if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in h.json('/api/nodes') if (n.get('hostname') or '').startswith(PREFIX)]}
    report['leftover'] = left
    report['clean'] = not any(left.values()) and all(v.get('baseline_clean') for v in report['hosts'].values())
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report['clean'] else 1


if __name__ == '__main__':
    sys.exit(main())
