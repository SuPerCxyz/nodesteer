#!/usr/bin/env python3
"""Independent verification of batch09-settings / idempotent-manual.

Re-reads the live environment and the on-disk snapshots without using any of the
batch's in-memory accounting, then writes evidence/independent-verification.json.

Checks
  1. endpoint is the repair authority on :18080
  2. GET /api/settings == evidence/settings-after.json == evidence/settings-before.json
  3. the Hub `settings` table read directly (mode=ro) matches the same values and
     its key set equals the pre-run key set recorded in settings-before.json
  4. the Hub config file sha256 is unchanged
  5. no owned fixture (prefix from fixture-ledger.json) survives in scripts/tasks/nodes
  6. the owned child root/process is gone from the child host
  7. the 4 protected repair nodes are online/synced
  8. every protected unit's MainPID equals the baseline recorded in phase0-guard.json
  9. /healthz and /readyz answer 200
 10. the recorded case verdicts are all PASS
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import childlib  # noqa: E402
import hubapi  # noqa: E402
import node_units  # noqa: E402
import settings_snapshot as ss  # noqa: E402

BATCH = HERE.parent
EV = BATCH / 'evidence'
API_DIR = BATCH.parent.parent / 'qa-resume-20261014-api' / 'idempotent-manual'


def load(p):
    return json.loads(Path(p).read_text())


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'endpoint': hub.base, 'checks': {}, 'details': {}}

    def check(name, ok, detail=None):
        out['checks'][name] = bool(ok)
        if detail is not None:
            out['details'][name] = detail

    before = load(EV / 'settings-before.json')
    after = load(EV / 'settings-after.json')
    ledger = load(BATCH / 'fixture-ledger.json')
    guard = load(EV / 'phase0-guard.json')
    web_result = load(BATCH / 'case-result.json')
    api_result = load(API_DIR / 'case-result.json')
    prefix = ledger['prefix']

    check('endpoint_is_repair_18080', hub.base.endswith(':18080'), hub.base)

    api_now = hub.json('/api/settings')
    check('api_settings_equals_after', api_now == after['api_settings'], api_now)
    check('api_settings_equals_before', api_now == before['api_settings'])
    check('before_equals_after', before['api_settings'] == after['api_settings'])

    db = ss._ssh(ss.PROBE)
    managed = {k: db['settings_rows'].get(k) for k in ss.API_KEYS}
    check('hub_db_managed_equals_api', managed == {k: api_now.get(k) for k in ss.API_KEYS}, managed)
    check('hub_db_key_set_equals_before_run',
          sorted(db['settings_rows'].keys()) == sorted(before['hub_db_settings_all_keys']),
          {'now': sorted(db['settings_rows'].keys()),
           'before': before['hub_db_settings_all_keys']})
    check('hub_config_file_sha256_unchanged',
          db['config_file']['sha256'] == before['hub_config_file']['sha256'],
          db['config_file']['sha256'])
    check('primary_hub_MainPID_unchanged',
          db['primary_hub_unit'].get('MainPID') == before['primary_hub_unit'].get('MainPID'),
          db['primary_hub_unit'].get('MainPID'))

    nodes = hub.json('/api/nodes')
    left = {
        'scripts': [s['id'] for s in hub.json('/api/scripts')
                    if (s.get('name') or '').startswith(prefix)],
        'tasks': [t['id'] for t in hub.json('/api/tasks')
                  if (t.get('name') or '').startswith(prefix)],
        'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(prefix)],
    }
    check('no_owned_fixture_left', not any(left.values()), left)

    child_tag = (ledger.get('owned_child') or {}).get('tag', 'qa-b09-settings')
    child_nonce = ledger['nonce']
    inv = childlib.OwnedChild(child_nonce, 1, '', '', '', '', '', tag=child_tag).inventory()
    check('owned_child_root_removed', not inv['root_exists'] and not inv['config_exists'],
          {'root': inv['root'], 'root_exists': inv['root_exists'],
           'config_exists': inv['config_exists']})
    check('owned_child_process_gone', not inv['owned_child_processes'],
          inv['owned_child_processes'])
    check('owned_child_agent_binary_sha_is_repair_build',
          inv['agent_binary_sha256'] == childlib.AGENT_SHA)

    by_id = {n['id']: n for n in nodes}
    protected = {}
    for nid, label in {'91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
                       '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
                       '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
                       '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4'}.items():
        n = by_id.get(nid) or {}
        protected[label] = {'status': n.get('status'), 'sync_status': n.get('sync_status')}
    check('protected_nodes_online_synced',
          all(v['status'] == 'online' and v['sync_status'] == 'synced'
              for v in protected.values()), protected)

    baseline = guard['unit_pid_baseline']
    pid_report = {}
    all_same = True
    for label, (ip, unit) in node_units.NODES.items():
        now_units = node_units.probe(ip, [unit, 'nodesteer-agent.service'])
        pid_report[label] = {}
        for uname, base in baseline[label]['units'].items():
            now_pid = now_units.get(uname, {}).get('MainPID')
            same = now_pid == base.get('MainPID')
            pid_report[label][uname] = {'baseline': base.get('MainPID'), 'now': now_pid,
                                        'same': same}
            all_same = all_same and same
    check('protected_units_pid_unchanged_vs_preflight_baseline', all_same, pid_report)

    health = {}
    for suffix in ('/healthz', '/readyz'):
        try:
            code, _ = hub.get(suffix)
            health[suffix] = code
        except Exception as exc:  # noqa: BLE001
            health[suffix] = type(exc).__name__
    check('hub_healthz_readyz_200', all(v == 200 for v in health.values()), health)

    web_verdicts = {c['case_id']: c['proposed_after'] for c in web_result['cases']}
    api_verdicts = {c['case_id']: c['proposed_after'] for c in api_result['cases']}
    check('web_case_verdicts_all_pass', all(v == 'PASS' for v in web_verdicts.values()),
          web_verdicts)
    check('api_case_verdicts_all_pass', all(v == 'PASS' for v in api_verdicts.values()),
          api_verdicts)
    check('web_result_settings_restored', web_result['settings_snapshot']['restored'])
    check('web_result_cleanup_oracle_clean', web_result['final_cleanup_oracle_clean'])
    check('web_result_no_deviations_only_harness_attempts',
          web_result['deviations'] == [], web_result['deviations'])

    out['passed'] = all(out['checks'].values())
    hubapi.write_json('../evidence/independent-verification.json', out)
    print(json.dumps({k: v for k, v in out['checks'].items()}, indent=2))
    print('PASSED' if out['passed'] else 'FAILED')
    return 0 if out['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
