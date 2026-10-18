#!/usr/bin/env python3
"""sys-b01 read-only preflight: authority guard, protected-node health, reset of a
fresh runtime baseline (hub unit / binary sha / health / primary unit / settings),
fixture absence and owned-child absence.  No writes."""
import json
import sys
import time
from pathlib import Path

import child_agent
import hubapi
import hubctl
import hubdb

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb01-%s-' % NONCE
PROTECTED_UNITS = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': hub.base,
           'preflight_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    nodes = hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    out['protected_nodes'] = {}
    bad = []
    for nid, label in PROTECTED_UNITS.items():
        n = by_id.get(nid)
        out['protected_nodes'][label] = None if not n else {
            k: n.get(k) for k in ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'agent_version')}
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            bad.append(label)
    out['protected_all_online_synced'] = not bad
    out['node_total'] = len(nodes)
    settings = hub.json('/api/settings')
    out['settings'] = ({s.get('key'): s.get('value') for s in settings}
                       if isinstance(settings, list) else settings)
    out['hub_unit'] = hubctl.unit_state()
    pid = out['hub_unit'].get('MainPID')
    out['hub_binary'] = hubctl.binary_sha(pid) if pid and pid != '0' else None
    out['hub_binary_sha_matches_pinned'] = (out['hub_binary'] or {}).get('sha256') == hubctl.HUB_BIN_SHA
    out['health'] = hubctl.health()
    out['primary_hub_unit_untouched_readonly'] = hubctl.primary_hub_state()
    snap = hubdb.snapshot()
    out['hub_db'] = {k: snap[k] for k in ('revision_sequence', 'revision_changes_total', 'quick_check',
                                          'agent_sync_state')}
    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    out['fixture_absence'] = {
        'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        'scripts_total': len(scripts), 'tasks_total': len(tasks)}
    ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
    out['native1'] = {k: ci[k] for k in ('root_exists', 'config_exists', 'owned_child_processes',
                                         'agent_binary_sha256', 'primary_agent_unit',
                                         'repair_agent_unit', 'owned_root_glob')}
    out['native1']['agent_binary_sha_matches'] = ci['agent_binary_sha256'] == child_agent.AGENT_SHA
    hubapi.write_json('evidence/phase0-preflight.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = (out['protected_all_online_synced'] and out['hub_binary_sha_matches_pinned']
          and out['health']['healthz'].get('http') == 200 and out['health']['readyz'].get('http') == 200
          and not any(out['fixture_absence'][k] for k in
                      ('scripts_matching_prefix', 'tasks_matching_prefix', 'child_nodes_matching_prefix'))
          and not (ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
