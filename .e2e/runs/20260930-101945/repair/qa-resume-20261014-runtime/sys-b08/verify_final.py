#!/usr/bin/env python3
"""Independent post-run verification for sys-b08 (read-only, no writes to the
environment).  Re-derives the final state from scratch instead of trusting the
batch's own bookkeeping:

  * every protected node online/synced
  * every protected service MainPID (and the repair Hub's) identical to the baseline
  * repair Hub unit active + /healthz + /readyz == 200
  * no owned fixture (script / 2 tasks / 2 schedules / node by nonce prefix) remains
  * every recorded fixture id returns HTTP 404
  * no owned child process / root remains on native-3
  * Hub revision sequence did not go backwards and the DB is consistent
  * global settings unchanged
"""
import json
import sys
from pathlib import Path

import child_agent
import hubapi
import hubctl
import hubdb
import node_units

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb08-%s-' % NONCE
PROTECTED = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
UNIT_HOSTS = {
    'native-1': '192.168.100.204', 'native-2': '192.168.100.206',
    'native-3': '192.168.100.222', 'native-4': '192.168.100.247',
}
ALL_UNITS = {
    'native-1': ['nodesteer-agent.service'],
    'native-2': ['nodesteer-agent.service'],
    'native-3': ['nodesteer-agent.service'],
    'native-4': ['nodesteer-agent.service'],
    'hub-vm': ['nodesteer-hub.service', 'nodesteer-hub-repair-20260930-101945.service'],
}


def main():
    run_ev = json.loads((HERE / 'run-evidence.json').read_text())
    guard = run_ev['phases']['guard']
    out = {'batch_id': 'sys-b08', 'nonce': NONCE, 'at_utc': child_agent.now_utc(),
           'independent_of_batch_bookkeeping': True}
    hub = hubapi.Hub()
    hub.login()
    nodes = {n['id']: n for n in hub.json('/api/nodes')}
    out['protected_nodes'] = {label: {'status': (nodes.get(nid) or {}).get('status'),
                                      'sync_status': (nodes.get(nid) or {}).get('sync_status')}
                              for nid, label in PROTECTED.items()}
    out['protected_all_online_synced'] = all(
        v['status'] == 'online' and v['sync_status'] == 'synced' for v in out['protected_nodes'].values())
    scripts = hub.json('/api/scripts')
    tasks = hub.json('/api/tasks')
    schedules = hub.json('/api/schedules')
    owned_task_ids = {run_ev['state']['task_allow_id'], run_ev['state']['task_required_id']}
    out['leftover'] = {
        'scripts_by_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks_by_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'nodes_by_prefix': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        'schedules_by_owned_task': [s['id'] for s in schedules if s.get('task_id') in owned_task_ids]}
    out['leftover_clear'] = not any(out['leftover'].values())
    out['scripts_total'] = len(scripts)
    out['tasks_total'] = len(tasks)
    out['schedules_total'] = len(schedules)
    out['node_total'] = len(nodes)
    deleted = []
    for kind, obj_id in [('schedules', run_ev['state']['schedule_allow_id']),
                         ('schedules', run_ev['state']['schedule_required_id']),
                         ('tasks', run_ev['state']['task_allow_id']),
                         ('tasks', run_ev['state']['task_required_id']),
                         ('scripts', run_ev['state']['script_id']),
                         ('nodes', run_ev['state']['child_node_id'])]:
        if not obj_id:
            continue
        code, _ = hub.get('/api/%s/%s' % (kind, hub.quote(obj_id)))
        deleted.append({'kind': kind, 'id': obj_id, 'get_http': code, 'is_404': code == 404})
    out['deleted_fixture_get_http'] = deleted
    out['all_fixtures_404'] = all(d['is_404'] for d in deleted)
    inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
    out['native3_inventory'] = {k: inv[k] for k in
                                ('root_exists', 'config_exists', 'owned_child_processes',
                                 'owned_root_glob', 'primary_agent_unit', 'repair_agent_unit',
                                 'agent_binary_sha256')}
    out['native3_clean'] = not (inv['root_exists'] or inv['config_exists']
                                or inv['owned_child_processes'] or inv['owned_root_glob'])
    out['hub_unit'] = hubctl.unit_state()
    out['hub_health'] = hubctl.health()
    out['hub_healthy'] = (out['hub_health']['healthz'].get('http') == 200
                          and out['hub_health']['readyz'].get('http') == 200)
    units_now = {}
    for label, units in ALL_UNITS.items():
        units_now[label] = node_units.probe(UNIT_HOSTS.get(label, '192.168.100.209'), units)
    out['units_now'] = units_now
    pairs = {}
    for label, v in units_now.items():
        for unit, props in v.items():
            base = guard['units_before'][label]['units'].get(unit, {}).get('MainPID')
            pairs['%s/%s' % (label, unit)] = {'baseline': base, 'now': props.get('MainPID'),
                                              'identical': base == props.get('MainPID')}
    out['all_mainpids_identical_to_baseline'] = pairs
    out['all_identical'] = all(v['identical'] for v in pairs.values())
    settings = hub.json('/api/settings')
    if isinstance(settings, list):
        settings = {s.get('key'): s.get('value') for s in settings}
    out['settings_now'] = settings
    out['settings_unchanged'] = settings == guard['settings_readonly']
    db_now = hubdb.snapshot()
    out['hub_db'] = {k: db_now[k] for k in ('revision_sequence', 'revision_changes_total', 'quick_check')}
    out['hub_db_before'] = {k: guard['hub_db_before'][k] for k in
                            ('revision_sequence', 'revision_changes_total', 'quick_check')}
    out['hub_revision_not_decreased'] = (db_now['revision_sequence']
                                        >= guard['hub_db_before']['revision_sequence'])
    out['hub_db_consistent'] = db_now['quick_check'] == 'ok'
    out['passed'] = bool(out['protected_all_online_synced'] and out['leftover_clear']
                         and out['all_fixtures_404'] and out['native3_clean']
                         and out['hub_healthy'] and out['all_identical']
                         and out['settings_unchanged'] and out['hub_revision_not_decreased']
                         and out['hub_db_consistent'])
    hubapi.write_json('evidence/independent-verification.json', out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
