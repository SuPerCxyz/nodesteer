#!/usr/bin/env python3
"""sys-b03 read-only preflight: authority guard, protected-node health, settings,
fixture absence and owned-child absence on native-3. No writes."""
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
OWNED_PREFIX = 'qa-sysb03-%s-' % NONCE
PROTECTED_UNITS = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'nonce': NONCE, 'owned_prefix': OWNED_PREFIX, 'endpoint': hub.base, 'port': 18080}

    nodes = hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    out['protected_nodes'] = {}
    bad = []
    for nid, label in PROTECTED_UNITS.items():
        n = by_id.get(nid)
        rec = None if not n else {k: n.get(k) for k in ('id', 'agent_id', 'hostname', 'status', 'sync_status', 'global_revision')}
        out['protected_nodes'][label] = rec
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            bad.append(label)
    out['protected_all_online_synced'] = not bad
    out['node_total'] = len(nodes)

    settings = hub.json('/api/settings')
    if isinstance(settings, list):
        settings = {s.get('key'): s.get('value') for s in settings}
    out['settings'] = settings

    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    schedules = hub.json('/api/schedules')
    out['fixture_absence'] = {
        'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(OWNED_PREFIX)],
        'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(OWNED_PREFIX)],
        'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(OWNED_PREFIX)],
        'scripts_total': len(scripts), 'tasks_total': len(tasks), 'schedules_total': len(schedules),
    }

    # owned child absence on native-3 (process + dir) using a marker only (no secret read)
    import child_agent
    ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
    out['native3_pre'] = {
        'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
        'owned_child_processes': ci['owned_child_processes'],
        'agent_binary_sha256': ci['agent_binary_sha256'],
        'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
        'primary_agent_unit': ci['primary_agent_unit'],
        'repair_agent_unit': ci['repair_agent_unit'],
    }

    ok = True
    try:
        s = socket.socket()
        s.bind(('0.0.0.0', 0))
        s.close()
    except OSError:
        ok = False
    out['workspace_tcp_ok'] = ok
    out['preflight_at_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    hubapi.write_json('evidence/phase0-preflight.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    healthy = (out['protected_all_online_synced'] and out['native3_pre']['agent_binary_sha_matches']
               and not out['native3_pre']['root_exists'] and not out['native3_pre']['owned_child_processes'])
    return 0 if healthy else 1


if __name__ == '__main__':
    sys.exit(main())
