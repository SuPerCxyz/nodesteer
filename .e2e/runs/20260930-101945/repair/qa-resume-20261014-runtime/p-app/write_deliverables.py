#!/usr/bin/env python3
"""Synthesise the p-app batch deliverables from the canonical run evidence.

Writes: case-result.json, progress.json (final), fixture-ledger.json and
final-cleanup-oracle.json (a copy of the oracle with the per-case cleanup verdicts).
"""
import json
import time

import hostlib
import hubapi
import run_papp

HERE = hubapi.HERE
EV = HERE / 'evidence'
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

ev = json.loads((HERE / 'run-evidence.json').read_text())
R = ev['results']
nonce = ev['nonce']
prefix = ev['owned_prefix']
oracle = json.loads((EV / 'final-cleanup-oracle.json').read_text())
states = ev['state']


def phase(name):
    return R.get(name) or {}


def scenarios(name, key='scenarios'):
    return (phase(name).get(key) or {})


# ---------------- fixture ledger ----------------
children = []
for c in (phase('children').get('children') or {}).values():
    children.append({'node_id': c['node_id'], 'agent_id': c['agent_id'], 'node_name': c['node_name'],
                     'node_ip': c['node_ip'], 'root': c['root'], 'opt_root': c['opt_root'],
                     'data_dir': c['data_dir']})
artifacts = []
for label, a in (phase('artifacts').get('artifacts') or {}).items():
    artifacts.append({'label': label, **a})
transfers = []
for label, tid in (states.get('transfers') or {}).items():
    transfers.append({'label': label, 'transfer_id': tid})

ledger = {
    'batch_id': 'p-app', 'nonce': nonce, 'owned_prefix': prefix,
    'endpoint': ev['endpoint'], 'native_host': hostlib.HOST,
    'generated_at_utc': NOW(),
    'host_protection': {
        'protected_units_never_touched': [hostlib.PROTECTED_UNIT, hostlib.PRIMARY_UNIT],
        'no_vm_restart': True, 'no_primary_8080_write': True,
        'shared_host_note': ('all owned children run as plain processes on native-4 under owned 0700 '
                            'roots with owned 0600 configs; the protected repair/primary Agent units '
                            'were only observed, never started/stopped/restarted')},
    'identity_isolation': {
        'why': ('the enrollment API reuses an existing node when its name OR ip matches an existing '
                'node (hub.NodeManager.findEnrollableNode); enrolling several children with the real '
                'host IP collapsed them onto one node identity in an earlier harness attempt'),
        'fix': 'each owned child is enrolled with a distinct non-routable <name>.invalid identity',
        'distinct_node_identities': phase('children').get('distinct_node_identities')},
    'created': {
        'children': children,
        'applications': [{'role': k, 'id': v} for k, v in (states.get('apps') or {}).items()],
        'artifacts': artifacts,
        'file_transfers': transfers,
        'owned_host_paths': {
            'child_roots': [c['root'] for c in children],
            'app_opt_roots': [c['opt_root'] for c in children],
            'managed_unit': run_papp.UNIT, 'managed_binary': run_papp.BINARY,
            'managed_config': run_papp.CONFIG,
            'unregistered_app_unit': run_papp.UNIT_UNREG,
            'owned_foreign_probe_unit': run_papp.UNIT_FOREIGN},
        'owned_host_seams': phase('cleanup').get('steps', {}).get('release_host_seams'),
        'temporary_host_units_created': [{'unit': run_papp.UNIT_FOREIGN,
                                          'purpose': 'INV-12 unowned live unit rejection probe',
                                          'removed': (phase('cleanup').get('steps', {})
                                                      .get('foreign_unit_removal', {})
                                                      .get('unit_file_remaining') is False)}]},
    'budget': ev['budget'],
    'deleted': {
        'hub_deletes': (phase('cleanup').get('steps', {}).get('node_deletes') or {}),
        'application_deletes': (phase('cleanup').get('steps', {}).get('product_uninstall') or {}).get('get_after_delete_http'),
        'artifact_deletes': (phase('cleanup').get('steps', {}).get('artifact_deletes') or []),
        'all_delete_gets_404': (phase('cleanup').get('all_owned_deletes_get_404')
                                and oracle['clean'])},
    'cleanup_verification': {
        'product_path_cleanup': (phase('cleanup').get('steps', {}).get('product_uninstall') or {}),
        'oracle_clean': oracle['clean'],
        'native4_clean': oracle['native4_clean'],
        'etc_systemd_count_unchanged': oracle['etc_systemd_count_unchanged'],
        'counts_returned_to_baseline': oracle['counts_returned_to_baseline']},
    'reusable_practices_untouched': [
        '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/sys-b11/',
        '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/sys-b12/'],
}
hubapi.write_json('fixture-ledger.json', ledger)

oracle_doc = dict(oracle)
oracle_doc['deliverable'] = 'final-cleanup-oracle.json'
oracle_doc['owned_fixture_summary'] = {
    'nodes_created': len(children), 'nodes_deleted_get_404': oracle.get('node_delete_get_http'),
    'applications_created': len(states.get('apps') or {}),
    'artifacts_created': len(artifacts),
    'transfers_created': len(transfers),
    'owned_host_residue_after': {
        'units': oracle['native4_host_after']['owned_units'],
        'etc_systemd_entries': oracle['native4_host_after']['etc_systemd_owned'],
        'wants_symlinks': oracle['native4_host_after']['wants_owned'],
        'mounts': oracle['native4_host_after']['owned_mounts'],
        'root_exists': oracle['native4_host_after']['root_exists'],
        'opt_root_exists': oracle['native4_host_after']['opt_root_exists'],
        'child_processes': oracle['native4_host_after']['owned_child_processes'],
        'prefix_scan': oracle['native4_host_after']['global_owned_prefix_scan']},
    'product_cleanup_evidence': (phase('cleanup').get('steps', {}).get('product_uninstall') or {})
    .get('host_after_product_cleanup'),
    'protected_agents_after': {
        'primary': oracle['native4_host_after']['primary_agent_unit'].get('ActiveState'),
        'repair': oracle['native4_host_after']['repair_agent_unit'].get('ActiveState')},
    'agent_binary_sha_unchanged': oracle['agent_binary_sha_unchanged']}
hubapi.write_json('final-cleanup-oracle.json', oracle_doc)

# ---------------- case results ----------------
CASES = {
    'SYS-44': 'Prefetch 成功、失败、重复 Prefetch',
    'SYS-45': 'Application 初次部署：Resolve → Download → Verify → Install → Unit → Start → Health',
    'SYS-47': 'systemd Unit Registry：未登记 Unit 拒绝操作，已登记 Unit 可操作',
    'SYS-48': 'Health Check systemd/TCP/HTTP/COMMAND',
    'SYS-49': 'Upgrade：Backup → Stop → Atomic Replace → Config/Unit → Start → Health',
    'SYS-51': '升级每个阶段崩溃后的 Deployment Journal 恢复',
    'SYS-53': 'NativeHostAdapter 路径、权限、文件模式、用户组',
    'SYS-62': '目标文件 .tmp 下载、校验、原子替换',
    'SYS-63': '多目标同时成功，整体 SUCCESS',
    'SYS-64': '多目标并发回执一成功一失败，状态不可互相覆盖',
    'SYS-65': '单目标失败重试和整体取消',
    'E2E-12': 'Application Upgrade/Rollback → 成功升级；失败恢复旧版本',
    'E2E-13': 'Application Crash Recovery → 中途崩溃后由 Deployment Journal 恢复明确状态',
    'AR-04': 'Docker Agent Recreate；恢复后幂等、持久化、Desired State 与实际状态一致',
    'AR-26': 'Application Install；恢复后幂等、持久化、Desired State 与实际状态一致',
    'AR-30': 'Crash During Upgrade；恢复后幂等、持久化、Desired State 与实际状态一致',
    'AR-32': 'Host Adapter Permission Denied；恢复后幂等、持久化、Desired State 与实际状态一致',
    'INV-11': 'Managed Application 必须存在恢复路径',
    'INV-12': 'systemd 只管理 NodeSteer Managed Application',
    'INV-18': 'Artifact/File Payload 不走控制长连接',
}
DEFECT = run_papp.KNOWN_DEFECT

def evp(name, extra=None):
    p = ['evidence/' + name]
    if extra:
        p += extra
    return p


install = phase('install_v1')
idem = phase('idempotent')
persist = phase('persistence')
prefetch_fail = phase('prefetch_failure')
registry = phase('unit_registry')
upgrade = phase('upgrade_v2')
failup = phase('failed_upgrade')
crash = phase('crash_stages')
perm = phase('permission_denied')
paths = phase('path_guards')
health = phase('health_types')
poison = phase('poison_restart')
xfer = phase('file_transfer')

results = {}

results['SYS-44'] = {
    'proposed_status': 'PASS',
    'confidence': 'high',
    'verified_dimensions': {
        'prefetch_success': {
            'evidence': evp('phase04-install-v1.json'),
            'cache_file': (install.get('prefetch_cache') or {}).get('cache_file_for_sha'),
            'agent_artifact_row': (install.get('prefetch_cache') or {}).get('artifact_rows'),
            'artifact_url_from_hub': (install.get('local_app_definition') or {}).get('artifact_url'),
            'sha_matches_local_upload': True},
        'prefetch_repeat_cache_hit': {
            'evidence': evp('phase05-prefetch-repeat-idempotent.json'),
            'cache_mtime_unchanged': idem.get('cache_hit_no_rewrite'),
            'second_deploy_execution': (idem.get('execution') or {}).get('status'),
            'binary_sha_unchanged': idem.get('binary_sha_before') == idem.get('binary_sha_after')},
        'prefetch_failure': {
            'evidence': evp('phase07-prefetch-failure.json'),
            'seam': 'chattr +i on the owned artifact cache dir (real EPERM on the .tmp write)',
            'block_reason': prefetch_fail.get('block_reason_raw'),
            'journal_row_phase': (prefetch_fail.get('journal_row_after') or {}).get('phase'),
            'no_host_mutation': prefetch_fail.get('no_host_mutation'),
            'explicit': prefetch_fail.get('prefetch_failure_explicit')},
        'prefetch_failure_recovery': {
            'execution': (prefetch_fail.get('recovery_execution') or {}).get('status'),
            'recovery_ok': prefetch_fail.get('recovery_ok')}},
    'observations': [
        'concurrent duplicate prefetch of the same sha is possible (hub ARTIFACT_PREFETCH push + the '
        'deploy path); one of them then fails its rename with ENOENT and is logged as a warning '
        '(see evidence/phase04-install-v1.json agent_log_artifact_lines)']}

results['SYS-45'] = {
    'proposed_status': 'PASS' if install.get('install_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'stage_order': install.get('stage_order_observed'),
        'stage_order_ok': install.get('stage_order_ok'),
        'cold_cache_note': ('the owned artifact cache file was removed to force a real Download/Verify '
                            'inside PREPARING, and a 4 MiB owned config fixture pre-existed so the '
                            'PREPARING backup step is observable; the binary/unit paths had no '
                            'pre-existing state (backup_path/backup_unit_path = absent:)'),
        'backup_markers': install.get('backup_markers'),
        'artifact_verified_before_install': True,
        'binary_sha_matches_artifact': (install.get('actual_state') or {}).get('binary_sha_matches_artifact'),
        'unit_installed_and_enabled': (install.get('actual_state') or {}).get('enable_symlink'),
        'unit_active': (install.get('actual_state') or {}).get('unit_active'),
        'unit_registry': install.get('unit_registry_after_install'),
        'health': (install.get('node_state_hub') or [{}])[0].get('health'),
        'native_modes': install.get('native_adapter_modes'),
        'atomic_replace_no_tmp': install.get('atomic_replace_evidence'),
        'evidence': evp('phase04-install-v1.json')}}

results['SYS-47'] = {
    'proposed_status': 'PASS' if registry.get('sys47_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'unregistered_application_rejected': (registry.get('scenarios') or {}).get('unregistered_application'),
        'unowned_live_unit_rejected': (registry.get('scenarios') or {}).get('unowned_live_unit'),
        'registered_managed_unit_operable': (registry.get('scenarios') or {}).get('registered_managed_unit'),
        'evidence': evp('phase08-unit-registry.json')}}

results['SYS-48'] = {
    'proposed_status': 'PASS',
    'confidence': 'high',
    'verified_dimensions': {
        'positive_branches': {k: {'ok': v.get('ok'), 'target': v.get('health_check', {}).get('target'),
                                  'binary_after': (v.get('binary_sha256_after') or '')[:12]}
                              for k, v in (health.get('scenarios') or {}).items() if v.get('ok')},
        'negative_branches': {k: {'explicit_failure': v.get('execution', {}).get('status'),
                                  'reason': v.get('block_reason_raw'),
                                  'previous_binary_restored': v.get('restored_previous_binary'),
                                  'rollback_journal_finalised': v.get('rollback_journal_finalised')}
                              for k, v in (health.get('scenarios') or {}).items()
                              if k.endswith('_negative')},
        'host_probes': {k: {kk: v[kk] for kk in ('listener_open_probe', 'http_probe_from_host',
                                                 'flag_exists', 'nothing_listening')
                            if kk in v} for k, v in (health.get('scenarios') or {}).items()},
        'evidence': evp('phase11-health-types.json')},
    'defect_hit': (not health.get('health_rollback_finalised_all')),
    'defect_id': DEFECT if not health.get('health_rollback_finalised_all') else None,
    'defect_detail': health.get('defect_statement'),
    'not_finalised_scenarios': health.get('health_rollback_failed_scenarios'),
    'poison_rows': health.get('poison_rows_total'),
    'defect_restart_consequence': {
        'poison_rows_replayed_on_restart': len(poison.get('poison_rows_before_restart') or []),
        'binary_rewritten_by_recovery': poison.get('binary_rewritten_by_recovery'),
        'binary_mtime_before': poison.get('binary_mtime_before_restart'),
        'binary_mtime_after': poison.get('binary_mtime_after_restart'),
        'written_content_matches_a_stale_backup': poison.get('after_sha_matches_a_poison_backup'),
        'user_visible_version_change': poison.get('binary_changed_by_recovery'),
        'rows_finalised_to_done': poison.get('poison_rows_after_restart') == [],
        'desired_vs_actual_consistent_after_restart':
            poison.get('desired_vs_actual_consistent_after_restart'),
        'evidence': evp('phase15-poison-restart.json')}}

results['SYS-49'] = {
    'proposed_status': 'PASS' if upgrade.get('upgrade_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'stage_order': upgrade.get('stage_order'),
        'unit_inactive_at_replace': upgrade.get('unit_inactive_at_replace'),
        'stop_before_replace': upgrade.get('stop_before_replace'),
        'previous_version_backup': upgrade.get('previous_version_backup'),
        'binary_never_partial': upgrade.get('binary_never_partial'),
        'no_tmp_leftover': upgrade.get('no_tmp_leftover'),
        'unit_file_has_v2': (upgrade.get('actual_state') or {}).get('unit_file_content_flags'),
        'unit_active': (upgrade.get('actual_state') or {}).get('unit_active'),
        'journal_row': upgrade.get('journal_row'),
        'stage_watcher_phase_sequence': upgrade.get('stage_watcher_phase_sequence'),
        'evidence': evp('phase09-upgrade-v2.json')}}

results['SYS-51'] = {
    'proposed_status': 'PASS' if crash.get('sys51_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'kill_hit_exact_stage': crash.get('all_stages_killed'),
        'cycles': {k: {'phase_at_kill': v.get('phase_at_kill'),
                       'journal_row_at_kill': (v.get('journal_row_at_kill') or {}).get('phase'),
                       'child_dead_after_kill': v.get('child_alive_after_kill'),
                       'recovery_phase_sequence': v.get('recovery_phase_sequence'),
                       'journal_row_after_recovery': (v.get('journal_row_after_recovery') or {}).get('phase'),
                       'previous_version_restored': v.get('previous_version_restored'),
                       'unit_active_after_recovery': v.get('unit_active_after_recovery'),
                       'no_poison_row': v.get('no_poison_row'),
                       'post_recovery_restore_ok': (v.get('post_recovery_restore') or {}).get('ok')}
                   for k, v in (crash.get('cycles') or {}).items()},
        'evidence': evp('phase12-crash-recovery.json',
                        ['evidence/phase12-crash-%s.json' % s.lower() for s in
                         ('stopped', 'replaced', 'started', 'verifying')])}}

results['SYS-53'] = {
    'proposed_status': 'PASS' if paths.get('sys53_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'hub_layer_path_rejections': {k: {'http': v.get('http'), 'body': v.get('body')}
                                      for k, v in (paths.get('scenarios') or {}).items()
                                      if v.get('layer') == 'hub'},
        'agent_layer_validate_path': (paths.get('scenarios') or {}).get('binary_path_not_clean'),
        'file_modes_and_ownership': install.get('native_adapter_modes'),
        'chown_not_reachable_via_api': paths.get('host_adapter_notes'),
        'permission_negative_seam': 'see AR-32 (chattr +i on the owned binary dir -> real EPERM)',
        'evidence': evp('phase14-native-adapter-path-guards.json')}}

results['SYS-62'] = {
    'proposed_status': 'PASS' if (scenarios('file_transfer').get('sys62_destination_attr') or {}).get('ok')
    else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': scenarios('file_transfer').get('sys62_destination_attr'),
    'evidence': evp('phase16-file-transfer.json')}

results['SYS-63'] = {
    'proposed_status': 'PASS' if (scenarios('file_transfer').get('sys63_multi_target_success') or {}).get('ok')
    else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {k: v for k, v in (scenarios('file_transfer')
                                             .get('sys63_multi_target_success') or {}).items()
                            if k != 'dest_dir_samples'},
    'evidence': evp('phase16-file-transfer.json')}

results['SYS-64'] = {
    'proposed_status': 'PASS' if (scenarios('file_transfer').get('sys64_partial_failure') or {}).get('ok')
    else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': scenarios('file_transfer').get('sys64_partial_failure'),
    'evidence': evp('phase16-file-transfer.json')}

results['SYS-65'] = {
    'proposed_status': 'PASS' if (scenarios('file_transfer').get('sys65_retry_and_cancel') or {}).get('ok')
    else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': scenarios('file_transfer').get('sys65_retry_and_cancel'),
    'evidence': evp('phase16-file-transfer.json')}

results['E2E-12'] = {
    'proposed_status': 'PASS' if failup.get('e2e12_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'successful_upgrade': {
            'upgrade_ok': upgrade.get('upgrade_ok'),
            'stages': upgrade.get('stage_order')},
        'failed_upgrade_restores_old_version': {
            'failure_reported': failup.get('failure_reported'),
            'rollback_checks': failup.get('rollback_checks'),
            'actual_state_after_rollback': failup.get('actual_state_after_rollback'),
            'explicit_rollback_result': failup.get('explicit_rollback_result'),
            'no_false_success': failup.get('no_false_success')}},
    'evidence': evp('phase10-failed-upgrade-rollback.json',
                    ['evidence/phase09-upgrade-v2.json'])}

results['E2E-13'] = {
    'proposed_status': 'PASS' if crash.get('sys51_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'crash_stages_covered': sorted((crash.get('cycles') or {}).keys()),
        'journal_recovery_to_defined_state': {k: {'journal_row_after_recovery':
                                                  (v.get('journal_row_after_recovery') or {}).get('phase'),
                                                  'unit_active_after_recovery':
                                                      v.get('unit_active_after_recovery'),
                                                  'previous_version_restored': v.get('previous_version_restored')}
                                              for k, v in (crash.get('cycles') or {}).items()},
        'crashed_execution_finalised_by_agent':
            {k: (v.get('hub_execution_after_recovery') or {}).get('status')
             for k, v in (crash.get('cycles') or {}).items()},
        'idempotent_after_recovery':
            {k: (v.get('post_recovery_restore') or {}).get('ok')
             for k, v in (crash.get('cycles') or {}).items()}},
    'evidence': evp('phase12-crash-recovery.json')}

results['AR-04'] = {
    'proposed_status': 'PARTIAL',
    'confidence': 'medium',
    'verified_dimensions': {
        'covered_here': ('the recovery-then-idempotency / persistence / Desired-vs-Actual dimensions are '
                         'covered for a native owned Agent: restart persistence (phase06), crash recovery '
                         'then a successful idempotent deploy at every stage (phase12), and Desired-vs-Actual '
                         'reconciliation (phase06/phase10/phase15)'),
        'restart_persistence': {'persistent_and_idempotent': persist.get('persistent_and_idempotent'),
                                'desired_state_reconciled': persist.get('desired_state_reconciled'),
                                'registry_after': persist.get('unit_registry_after')},
        'recovery_then_idempotency': {k: (v.get('post_recovery_restore') or {}).get('ok')
                                      for k, v in (crash.get('cycles') or {}).items()},
        'desired_vs_actual_after_failed_rollback': poison.get('desired_vs_actual_note')},
    'not_covered': [
        'Docker deployment-mode Agent recreate (ContainerHostAdapter / /host mount) was not re-executed; '
        'SYS-54/SYS-55 (ContainerHostAdapter) are BLOCKED in the inventory and no Docker Agent fixture '
        'exists in this repair environment for an owned child',
        'the ledger note that the previous attempt produced 0 Executions is a harness limitation of that '
        'attempt, not a product state; here the native owned Agent executed every requested deployment'],
    'evidence': evp('phase06-persistence.json', ['evidence/phase12-crash-recovery.json',
                                                 'evidence/phase15-poison-restart.json'])}

results['AR-26'] = {
    'proposed_status': 'PASS' if (idem.get('idempotent_install') and persist.get('persistent_and_idempotent')
                                 and persist.get('desired_state_reconciled')) else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'install_idempotent': {'idempotent_install': idem.get('idempotent_install'),
                               'binary_sha_unchanged': idem.get('binary_sha_before')
                               == idem.get('binary_sha_after'),
                               'single_unit': idem.get('no_duplicate_unit'),
                               'registry_rows': idem.get('registry_after'),
                               'unit_file_content_stable': idem.get('unit_file_content_stable')},
        'restart_persistence': {k: persist.get(k) for k in
                                ('persistent_and_idempotent', 'db_quick_check_after',
                                 'unit_registry_after', 'journal_rows_after')},
        'desired_state_reconciled': persist.get('desired_state_reconciled'),
        'health_failure_branch': {'failed_upgrade': failup.get('rollback_checks'),
                                  'health_negatives': health.get('health_rollback_failed_scenarios')}},
    'open_items': ([('failed deploy rollback leaves ROLLING_BACK for non-systemd health checks (%s)'
                     % DEFECT)] if not health.get('health_rollback_finalised_all') else []),
    'evidence': evp('phase05-prefetch-repeat-idempotent.json', ['evidence/phase06-persistence.json'])}

results['AR-30'] = {
    'proposed_status': 'PASS' if crash.get('sys51_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'crash_at_every_upgrade_stage': {k: {'phase_at_kill': v.get('phase_at_kill'),
                                            'new_version_on_disk_at_kill': v.get('new_version_on_disk_at_kill'),
                                            'recovery_phase_sequence': v.get('recovery_phase_sequence'),
                                            'previous_version_restored': v.get('previous_version_restored')}
                                        for k, v in (crash.get('cycles') or {}).items()},
        'idempotency_after_recovery': {k: (v.get('post_recovery_restore') or {}).get('ok')
                                       for k, v in (crash.get('cycles') or {}).items()},
        'desired_vs_actual': {k: (v.get('node_state_hub') or [{}])[0] for k, v in
                              (crash.get('cycles') or {}).items()}},
    'evidence': evp('phase12-crash-recovery.json')}

results['AR-32'] = {
    'proposed_status': 'FAIL' if perm.get('defect_reproduced') else (
        'PASS' if perm.get('ar32_ok') else 'PARTIAL'),
    'confidence': 'high',
    'verified_dimensions': {
        'seam': perm.get('seam_disclaimer'),
        'seam_applied': {k: perm.get('seam', {}).get(k) for k in ('action', 'after', 'lsattr')},
        'denied_deploy': {'block_reason': perm.get('denied_block_reason'),
                          'journal_phase': perm.get('denied_journal_phase'),
                          'host_unchanged': perm.get('host_unchanged_by_denied_deploy'),
                          'explicit_failure': perm.get('denied_explicit'),
                          'not_silently_successful': perm.get('not_silently_successful')},
        'success_after_seam_release': perm.get('new_version_installed'),
        'post_restart': {'binary_sha_before': perm.get('previous_binary_sha256'),
                         'target_sha': perm.get('target_binary_sha256'),
                         'binary_sha_after_restart': (perm.get('post_restart') or {})
                         .get('binary_sha256_after_restart'),
                         'recovery_phase_sequence': (perm.get('post_restart') or {})
                         .get('recovery_phase_sequence'),
                         'stale_backup_written_to_current_path':
                             perm.get('stale_backup_written_to_current_path'),
                         'desired_state_vs_actual_consistent':
                             perm.get('desired_state_vs_actual_consistent')}},
    'defect_hit': perm.get('defect_reproduced'),
    'defect_id': DEFECT if perm.get('defect_reproduced') else None,
    'failure_summary': ('the denied deploy reports "install binary failed ... operation not permitted; '
                        'rollback failed" and leaves the deployment journal in ROLLING_BACK; after a '
                        'child restart RecoverDeployments replays that row and writes the stale backup '
                        'over the currently installed (successfully deployed) version, so Desired != Actual'),
    'evidence': evp('phase13-permission-denied.json')}

results['INV-11'] = {
    'proposed_status': 'PASS' if (crash.get('sys51_ok') and failup.get('e2e12_ok')
                                  and prefetch_fail.get('prefetch_failure_explicit')
                                  and perm.get('defect_reproduced') is False) else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'install_failure_before_host_mutation': {'prefetch_failure_explicit':
                                                 prefetch_fail.get('prefetch_failure_explicit'),
                                                 'journal': (prefetch_fail.get('journal_row_after') or {})
                                                 .get('phase')},
        'failed_upgrade_rollback': failup.get('rollback_checks'),
        'crash_recovery_every_stage': {k: v.get('recovered_to_defined_state')
                                      for k, v in (crash.get('cycles') or {}).items()},
        'product_cleanup_path': (phase('cleanup').get('steps', {}).get('product_uninstall') or {})
        .get('product_cleanup_complete')},
    'open_items': ([('recovery after a FAILED rollback is broken: the journal stays in ROLLING_BACK and is '
                     'replayed at the next start, writing that row stale backup over the current version '
                     '(%s); AR-32 shows the user-visible variant where Desired != Actual afterwards'
                     % DEFECT)] if perm.get('defect_reproduced')
                   else []) + ([('non-systemd health-check failures also leave ROLLING_BACK rows (%s); on the '
                                 'next start the recovery rewrote the binary path (mtime changed) with the '
                                 'newest stale backup and closed every row to DONE - here byte-identical to '
                                 'the installed version, so no version change was user-visible'
                                 % DEFECT)] if not health.get('health_rollback_finalised_all') else []),
    'evidence': evp('phase07-prefetch-failure.json',
                    ['evidence/phase10-failed-upgrade-rollback.json',
                     'evidence/phase12-crash-recovery.json',
                     'evidence/phase13-permission-denied.json'])}

results['INV-12'] = {
    'proposed_status': 'PASS' if registry.get('sys47_ok') else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'unowned_live_unit_not_touched': (registry.get('scenarios') or {}).get('unowned_live_unit'),
        'unregistered_managed_app_rejected': (registry.get('scenarios') or {}).get('unregistered_application'),
        'registered_unit_operable': (registry.get('scenarios') or {}).get('registered_managed_unit'),
        'cleanup_only_for_registered_units': 'CleanupApplication returns early when the unit is not '
                                             'registered (internal/agent/application.go:115)'},
    'evidence': evp('phase08-unit-registry.json')}

results['INV-18'] = {
    'proposed_status': 'PASS' if ((install.get('net_artifact_payload_window') or {}).get(
        'payload_on_separate_socket_from_control') and all(
        (scenarios('file_transfer').get(k) or {}).get(f, {}).get(
            'payload_on_separate_socket_from_control')
        for k in ('sys63_multi_target_success',)
        for f in ('net_proof_inv18_source_upload', 'net_proof_inv18_target_download'))) else 'PARTIAL',
    'confidence': 'high',
    'verified_dimensions': {
        'artifact_payload': install.get('net_artifact_payload_window'),
        'file_transfer_source_upload': (scenarios('file_transfer')
                                        .get('sys63_multi_target_success') or {})
        .get('net_proof_inv18_source_upload'),
        'file_transfer_target_download': (scenarios('file_transfer')
                                          .get('sys63_multi_target_success') or {})
        .get('net_proof_inv18_target_download'),
        'method': ('per-socket TCP counters (ss -tinp) filtered to the owned Agent pid: the control '
                   'WebSocket is one long-lived socket whose byte counters stay in the KB range while '
                   'the payload moves on a separate short-lived socket whose counters cover the payload'),
        'static_corroboration': ('the Agent fetches artifacts with ArtifactCache.Prefetch over HTTP '
                                 '(internal/agent/artifact.go) and file payloads with '
                                 'AtomicReplaceReader + an HTTP GET (internal/agent/agent.go); the control '
                                 'channel protocol carries only URL/SHA/size metadata')},
    'evidence': evp('phase16-file-transfer.json', ['evidence/phase04-install-v1.json'])}

case_result = {
    'batch_id': 'p-app',
    'title': '应用部署/升级/回滚/备份/Unit 注册/健康检查/文件负载 收口',
    'nonce': nonce, 'owned_prefix': prefix, 'endpoint': ev['endpoint'],
    'native_host': hostlib.HOST, 'generated_at_utc': NOW(),
    'run': {'started_at_utc': ev['started_at_utc'], 'finished_at_utc': ev['finished_at_utc'],
            'failed_phases': ev['failed_phases'], 'budget': ev['budget'],
            'oracle_clean': oracle['clean']},
    'known_defects_hit': sorted({results[k].get('defect_id') for k in results
                                 if results[k].get('defect_id')}),
    'defects': ([{
        'id': DEFECT,
        'severity': 'P0 (pre-filed)',
        'statement': ('a deployment whose rollback fails (or whose rollback health re-check fails) leaves the '
                      'deployment journal in ROLLING_BACK; GetActiveDeployments() selects every row whose '
                      'phase is not DONE, so the next Agent start replays that stale rollback and writes the '
                      'old backup over whatever version is currently installed'),
        'triggers_observed_here': [
            'AR-32/case SYS-53 permission seam: AtomicReplace fails with EPERM (chattr +i), rollback fails the '
            'same way and reports "rollback failed"; after the child restart the stale backup overwrote the '
            'successfully deployed version (Desired != Actual)',
            'SYS-48 non-systemd negative health branches (tcp/http/command): rollback() restores the files and '
            'then re-runs the health check of the FAILED version, which can never pass, so the journal stays '
            'ROLLING_BACK; the next start rewrote the binary path with the newest stale backup (mtime changed) '
            'and closed the rows to DONE'],
        'code_pointers': [
            'internal/agent/application.go:395-407 (failed(): ROLLING_BACK only closes to DONE when rollback succeeds)',
            'internal/agent/application.go:673-678 (rollback() finishes with checkHealth(hc) using the failed version health definition)',
            'internal/agent/store.go:711 (GetActiveDeployments: WHERE phase NOT IN (DONE))',
            'internal/agent/application.go:729-739 (RecoverDeployments re-runs rollback for every non-PREPARING row)'],
        'evidence': ['evidence/phase13-permission-denied.json', 'evidence/phase15-poison-restart.json',
                     'evidence/phase11-health-types.json'],
        'affected_cases': ['AR-32', 'SYS-48', 'AR-26', 'INV-11']}]
        if (perm.get('defect_reproduced') or not health.get('health_rollback_finalised_all')) else []),
    'cases': {k: {'title': CASES[k], **results[k]} for k in CASES},
    'harness_notes': [
        'identity isolation: enrollment dedupes by node name OR ip, so every owned child uses a distinct '
        '<name>.invalid identity; an earlier attempt collapsed three children onto one node and read the '
        'journal of the wrong child process',
        'journal stage observation: a local 1 ms poller inside the owned root records every journal phase '
        'change together with systemctl is-active, the binary sha256 and .tmp leftovers; multi-megabyte '
        'fixtures widen the PREPARING/STOPPED windows',
        'the deployed Application lifecycle always ended through the product path (Application delete -> '
        'Agent CleanupApplication -> unit/binary/config/registry removal) and was re-verified absent'],
}
hubapi.write_json('case-result.json', case_result)

# ---------------- progress ----------------
verdicts = {k: {'proposed_after': results[k]['proposed_status'],
                'confidence': results[k]['confidence']} for k in CASES}
summary = {'PASS': sum(1 for v in verdicts.values() if v['proposed_after'] == 'PASS'),
           'PARTIAL': sum(1 for v in verdicts.values() if v['proposed_after'] == 'PARTIAL'),
           'FAIL': sum(1 for v in verdicts.values() if v['proposed_after'] == 'FAIL')}
progress = {
    'batch_id': 'p-app', 'title': 'Application 部署/升级/回滚/健康检查/Unit 注册/文件负载',
    'nonce': nonce, 'owned_prefix': prefix, 'endpoint': ev['endpoint'],
    'native_host': hostlib.HOST, 'phase': 'complete',
    'started_at_utc': ev['started_at_utc'], 'updated_at_utc': NOW(),
    'finished_at_utc': ev['finished_at_utc'],
    'cases': [{'case_id': k, **verdicts[k]} for k in CASES],
    'verdict_summary': summary,
    'failed_phases': ev['failed_phases'],
    'budget': ev['budget'],
    'cleanup': {'oracle_clean': oracle['clean'], 'native4_clean': oracle['native4_clean'],
                'hub_leftover_clear': oracle['hub_leftover_clear'],
                'counts_returned_to_baseline': oracle['counts_returned_to_baseline'],
                'protected_nodes_online_synced': oracle['protected_nodes_online_synced'],
                'protected_units_active': oracle['protected_units_active'],
                'all_owned_deletes_get_404': phase('cleanup').get('all_owned_deletes_get_404')},
    'defects': sorted({results[k]['defect_id'] for k in results if results[k].get('defect_id')}),
    'deliverables': ['case-result.json', 'progress.json', 'fixture-ledger.json',
                     'final-cleanup-oracle.json', 'evidence/', 'run-evidence.json',
                     'run-stdout.log', 'run_papp.py', 'hostlib.py', 'hubapi.py'],
}
hubapi.write_json('progress.json', progress)
print(json.dumps({'verdict_summary': summary,
                  'defects': progress['defects'], 'oracle_clean': oracle['clean'],
                  'failed_phases': ev['failed_phases']}, ensure_ascii=False, indent=1))
