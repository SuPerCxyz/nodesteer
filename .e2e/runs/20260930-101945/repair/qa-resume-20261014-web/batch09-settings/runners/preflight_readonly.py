#!/usr/bin/env python3
"""Read-only preflight for batch09-settings.

Captures, before anything is touched:
  * the authoritative repair endpoint guard (port 18080),
  * GET /api/settings (API view),
  * the Hub SQLite `settings` table (storage view, opened mode=ro),
  * /healthz + /readyz,
  * the 4 protected repair nodes + the primary/repair Hub unit MainPIDs,
  * the 4 repair nodes' online/sync state as seen by the API.

Never prints or persists a credential.
"""
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hubapi  # noqa: E402
import node_units  # noqa: E402

HUB_HOST = '192.168.100.209'
HUB_DB = '/var/lib/nodesteer-hub-repair-20260930-101945/hub.db'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')

DB_PROBE = r'''
import json, sqlite3
c = sqlite3.connect('file:%s?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
out = {}
out['quick_check'] = c.execute('PRAGMA quick_check').fetchone()[0]
out['settings_table_exists'] = bool(list(c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'")))
out['settings_columns'] = [r[1] for r in c.execute('PRAGMA table_info(settings)')]
out['settings_rows'] = [
    {'key': r[0], 'value': r[1]}
    for r in c.execute('SELECT key,value FROM settings ORDER BY key')] if out['settings_table_exists'] else []
out['nodes'] = [{'id': r[0], 'hostname': r[1], 'status': r[2], 'sync_status': r[3], 'global_revision': r[4]}
                for r in c.execute('SELECT id,hostname,status,sync_status,global_revision FROM nodes')]
print(json.dumps(out))
''' % HUB_DB


def hub_db_probe(timeout=60):
    ssh = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + HUB_HOST]
    cp = subprocess.run(ssh + ['python3', '-'], input=DB_PROBE, capture_output=True,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError('hub_db_probe_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    return json.loads(lines[-1])


def http_probe(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {'status': r.status, 'body': r.read(200).decode('utf-8', 'replace').strip()}
    except Exception as exc:
        return {'status': None, 'error': type(exc).__name__}


def main():
    hub = hubapi.Hub()
    out = {'at_utc': __import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime())}
    out['endpoint'] = hub.base
    out['endpoint_guard_ok'] = hub.base.endswith(':18080')
    hub.login()
    out['login_ok'] = True
    settings = hub.json('/api/settings')
    if isinstance(settings, list):
        settings = {s.get('key'): s.get('value') for s in settings}
    out['api_settings'] = settings
    nodes = hub.json('/api/nodes')
    if isinstance(nodes, dict):
        nodes = nodes.get('items') or nodes.get('nodes') or []
    out['api_nodes'] = [{'id': n.get('id'), 'name': n.get('name') or n.get('hostname'),
                         'status': n.get('status'), 'sync_status': n.get('sync_status')} for n in nodes]
    out['healthz'] = http_probe(hub.base + '/healthz')
    out['readyz'] = http_probe(hub.base + '/readyz')
    out['hub_db'] = hub_db_probe()
    out['units'] = {}
    for label, (ip, unit) in node_units.NODES.items():
        out['units'][label] = {'ip': ip, 'units': node_units.probe(ip, [unit, 'nodesteer-agent.service'])}
    out['units']['hub-vm'] = {'ip': node_units.HUB[0],
                              'units': node_units.probe(node_units.HUB[0], [node_units.HUB[1], node_units.HUB[2]])}
    out['api_calls'] = hub.calls
    name = sys.argv[1] if len(sys.argv) > 1 else 'phase0-preflight.json'
    hubapi.write_json('../evidence/' + name, out)
    print(json.dumps({'api_settings': out['api_settings'],
                      'db_settings': out['hub_db']['settings_rows'],
                      'nodes': len(out['api_nodes']),
                      'healthz': out['healthz']['status'], 'readyz': out['readyz']['status']},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
