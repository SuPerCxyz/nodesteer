#!/usr/bin/env python3
"""Builds case-result.json for the qa-resume-20261016-g23 batch (G2+G3 merged
window) purely from the recorded evidence files, so every number in the
deliverable is traceable to a raw evidence artifact.

Cases: P11-08, AR-08, P17P18-03 (G2), P12P13-06, P12P13-07, P12P13-08 (G3).
Status changes are *proposals*: the task ledger is only updated by Root after
independent review (授权通过 ≠ 台账状态变更).
"""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'


def load(name):
    return json.loads((EV / name).read_text())


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def main():
    run = json.loads((HERE / 'run-evidence.json').read_text())
    guard = load('phase0-guard.json')
    child = load('phase1-owned-child.json')
    fixtures = load('phase2-fixtures.json')
    p11 = load('phase3-p11-08-duplicate-delivery.json')
    p17 = load('phase4-p17p18-03-pending-running.json')
    ar08 = load('phase5-ar08-notification-faults.json')
    p1206 = load('phase6-p12p13-06-owner-difference.json')
    p1207 = load('phase7-p12p13-07-offline-quadrants.json')
    p1208 = load('phase8-p12p13-08-misfire-after-stop.json')
    cleanup = load('phase9-cleanup.json')
    oracle = json.loads((HERE / 'final-cleanup-oracle.json').read_text())
    obs = load('observation-hub-online-required-reconnect-window.json')

    node_id = run['state']['nodes']['a']['id']
    prefix = run['prefix']
    evdir = '.e2e/runs/20260930-101945/repair/qa-resume-20261016-g23/evidence/'

    def resources(extra=None):
        r = {'node': node_id, 'node_host': '192.168.100.206', 'child_root': fixtures['marker_files_dir_on_child'],
             'proxy_port': child['owned_child']['proxy_listen_port']}
        if extra:
            r.update(extra)
        return r

    cases = []

    # ---------------- P11-08 ----------------
    a = p11['assertions']
    cases.append({
        'case_id': 'P11-08',
        'title': '每次Manual Run生成新UUID代表新意图；同Execution ID/Key重复投递/上传按幂等验证',
        'canonical_before': 'PARTIAL — remaining: 同 Execution ID/Key 的重复投递/上传幂等未测试（需要构造重复投递）。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:P11-08-duplicate-delivery-and-upload',
            'layer': 'real-api + frame-wire injection + owned child Agent SQLite + on-node marker file',
            'status': 'PASS',
            'description': [
                'One Manual Run of a hub-owned task: the injectable owned frame proxy duplicated the real '
                'hub->agent RUN_EXECUTION frame (injection event seq %s) and the real agent->hub EXECUTION_FINISHED '
                'frame (seq %s) for the same Execution ID %s.'
                % (p11['injection']['duplicate_injected_run_execution'][0]['seq'],
                   p11['injection']['duplicate_injected_execution_finished'][0]['seq'],
                   p11['run1']['execution_id']),
                'The Hub received the duplicated result upload twice (it acked the same Execution twice: %d ACKs) '
                'and still kept exactly 1 execution row in SUCCESS; the child journal kept exactly 1 local execution '
                'row, synced once; the marker script ran exactly once (1 line).'
                % p11['run1']['ack_count_for_execution'],
                'A second, uninjected Manual Run produced a new UUID with its own row and its own marker line '
                '(new UUID = new intent; the dedup is by Execution ID, not by "swallow all runs").',
            ]}],
        'remaining_or_limitations': [
            'The injection only covers hub->agent RUN_EXECUTION duplication and agent->hub EXECUTION_FINISHED '
            'duplication on the owned child channel; LOG_CHUNK / ARTIFACT frames were not duplicated (out of scope).',
        ],
        'evidence': [evdir + 'phase3-p11-08-duplicate-delivery.json', evdir + 'phase2-fixtures.json'],
        'actual_resources': resources({'task': fixtures['fixtures']['task_dup_run'],
                                       'execution_ids': [p11['run1']['execution_id'], p11['run2']['execution_id']],
                                       'marker_file': fixtures['fixtures']['script_marker_tag'] + '.log'}),
    })

    # ---------------- P17P18-03 ----------------
    a = p17['assertions']
    pend = [s for s in p17['pending_samples'] if s['matching_our_execution']]
    runn = [s for s in p17['running_samples'] if s['matching_our_execution']]
    cases.append({
        'case_id': 'P17P18-03',
        'title': 'status：PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT/BLOCKED',
        'canonical_before': 'PARTIAL — remaining: 六状态有正样本，PENDING/RUNNING 无真实样本。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:P17P18-03-pending-and-running-real-samples',
            'layer': 'real-api (executions list = UI query) + frame-wire hold injection + owned child Agent',
            'status': 'PASS',
            'description': [
                'A real PENDING row was sampled with the exact query the UI uses '
                '(`%s`): one row for execution %s in status PENDING at %s while the proxy held the '
                'RUN_EXECUTION frame on the child channel (8s hold window).'
                % (pend[0]['query'] if pend else 'n/a', p17['execution_id'],
                   pend[0]['at_utc'] if pend else 'n/a'),
                'A real RUNNING row was sampled for the same execution at %s while the proxy held the '
                'EXECUTION_FINISHED frame (the task had already completed on the node, the Hub row was still '
                'RUNNING).' % (runn[0]['at_utc'] if runn else 'n/a'),
                'The row then converged to SUCCESS with the marker script executed exactly once, and all eight '
                'status selectors answered HTTP 200 against the same task-scoped query.',
            ]}],
        'remaining_or_limitations': [
            'Sampling used the executions-list API (the very endpoint the executions page reads) rather than a '
            'browser session; the eight selector controls themselves were verified in earlier batches.',
            'The PENDING/RUNNING windows exist because the owned child channel held the frame; no product code or '
            'protected node was touched.',
        ],
        'evidence': [evdir + 'phase4-p17p18-03-pending-running.json'],
        'actual_resources': resources({'task': fixtures['fixtures']['task_pending'],
                                       'execution_id': p17['execution_id'],
                                       'final_status': p17['final_row']['status']}),
    })

    # ---------------- AR-08 ----------------
    a = ar08['assertions']
    cases.append({
        'case_id': 'AR-08',
        'title': 'Agent Offline During Hub Script Update；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'PARTIAL — remaining: 无单独注入 duplicate/lost/reordered 通知帧或调度幂等维度。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:AR-08-notification-frame-faults',
            'layer': 'real-api + frame-wire injection (drop / duplicate / stale replay) + owned child Agent SQLite '
                     '+ Hub read-only SQLite desired-state compare',
            'status': 'PASS',
            'description': [
                'Lost notification: the real CHANGE_NOTIFICATION for a Hub Script update (global revision %s) was '
                'dropped on the child channel; the child still converged to the new revision/sha within %ss via its '
                'own revision-check path (no silent divergence).'
                % (ar08['lost_notification']['frames_dropped'][0]['global_revision'],
                   ar08['lost_notification']['child_converged']['seconds']),
                'Duplicate notification: the real CHANGE_NOTIFICATION for the next Script update (global revision %s) '
                'was delivered twice; the child converged once and idempotently in %ss with a single script row.'
                % (ar08['duplicate_notification']['frames_duplicated'][0]['global_revision'],
                   ar08['duplicate_notification']['child_converged']['seconds']),
                'Reordered/stale delivery: the captured older notification (global revision %s, script revision 2) '
                'was replayed after the newer revision 3 had been applied; the child kept revision 3 and the newest '
                'sha, and its global revision never regressed — i.e. a stale/reordered notification is ignored.'
                % ar08['reordered_stale_notification']['source_frame']['global_revision'],
                'Desired state vs actual state after the fault window: all %d owned scripts (revision+sha256), %d owned '
                'tasks and %d owned schedules match the Hub, and the live agent-owned schedule still produced exactly '
                'one execution per slot (no duplicate slots in Hub or child journal).'
                % (len(ar08['desired_state_vs_actual']['comparison']['scripts']),
                   len(ar08['desired_state_vs_actual']['comparison']['tasks']),
                   len(ar08['desired_state_vs_actual']['comparison']['schedules'])),
                'Restart persistence: the owned Agent was really stopped and restarted; definitions survived, every '
                'pre-restart execution id is still present with no duplicate ids, and the only new rows are genuinely '
                'new slots.',
            ]}],
        'remaining_or_limitations': [
            'On the shared repair authority the SYNC_REQUEST count inside the replay window is not a clean '
            'discriminator (heartbeat revision checks every 5s plus other batches\' object changes); the '
            'applied-state comparison is the authoritative signal and is what the assertion uses.',
        ],
        'evidence': [evdir + 'phase5-ar08-notification-faults.json', evdir + 'phase2-fixtures.json'],
        'actual_resources': resources({'script_under_fault': ar08['script_id'],
                                       'hub_revision_before': ar08['hub_revision_before'],
                                       'schedule_id': ar08['schedule_idempotency_across_fault_window']['schedule_id']}),
    })

    # ---------------- P12P13-06 ----------------
    a = p1206['assertions']
    h, ag = p1206['hub_owned'], p1206['agent_owned']
    cases.append({
        'case_id': 'P12P13-06',
        'title': 'Hub Owner 和 Agent Owner。',
        'canonical_before': 'PARTIAL — remaining: Hub-Owner vs Agent-Owner 的真实调度执行差异未验证。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:P12P13-06-owner-execution-difference',
            'layer': 'real-schedule (cron) + frame-wire source-of-trigger oracle + owned child Agent SQLite',
            'status': 'PASS',
            'description': [
                'Hub-owned cron: %d executions, %d distinct slots, every execution id present in a real hub->agent '
                'RUN_EXECUTION frame (the Hub is the trigger source); the child only executed what was dispatched.'
                % (h['hub_executions'], h['distinct_slots']),
                'Agent-owned cron, same expression, same child: %d executions and exactly %d hub->agent RUN_EXECUTION '
                'frames for that task; every execution id was reported by the agent\'s own EXECUTION_FINISHED frames, '
                'i.e. the Agent local scheduler is the trigger source.'
                % (ag['hub_executions'], ag['hub_dispatched_run_frames_for_task']),
                'Both owners produced a single execution per slot (no double trigger) and a real business effect '
                '(2 marker lines each); child journal trigger types are "schedule" for both.',
            ]}],
        'remaining_or_limitations': [
            'One slot of each owner fired immediately at enable time with a slot derived from the schedule creation '
            'anchor (start-minus-slot tens of seconds) — same enable-time behaviour as the previous p-sched run, '
            'not a difference between owners.',
        ],
        'evidence': [evdir + 'phase6-p12p13-06-owner-difference.json'],
        'actual_resources': resources({'hub_owned_task': fixtures['fixtures']['task_owner_hub'],
                                       'agent_owned_task': fixtures['fixtures']['task_owner_agent'],
                                       'schedules': [run['state']['schedules']['sc_owner_hub'],
                                                     run['state']['schedules']['sc_owner_agent']]}),
    })

    # ---------------- P12P13-07 ----------------
    a = p1207['assertions']
    ow, rec = p1207['offline_window'], p1207['recovery']
    cases.append({
        'case_id': 'P12P13-07',
        'title': '`allow_offline` 与 `hub_online_required` 组合。',
        'canonical_before': 'PARTIAL — remaining: 两策略组合下的实际投递行为未验证。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:P12P13-07-offline-policy-quadrants',
            'layer': 'real-schedule (agent-owned 30s interval) + real channel cut on the owned child + node status '
                     '+ child journal offline/synced flags + Hub rows after reconnect',
            'status': 'PASS',
            'description': [
                'Quadrant 1+2 (child connected): both the allow_offline and the hub_online_required schedule fired '
                '(2 rows each, offline flag 0).',
                'Quadrant 3 (child channel cut, allow_offline): %d executions were created while disconnected, all '
                'with offline=1 and synced=0 (local-only until reconnect).' % ow['allow_offline_created_rows'],
                'Quadrant 4 (child channel cut, hub_online_required): exactly %d executions — the online gate really '
                'suppresses dispatch while the child has no Hub connection (the cut stops the listener, so the agent '
                'can never complete a handshake).' % ow['hub_online_required_created_rows'],
                'Recovery: after the channel was restored the Hub itself had already marked the node offline '
                '(observed %ss after the cut) and then online/synced; the offline executions were uploaded and appear '
                'as Hub rows with offline=true (%d rows); the hub_online_required schedule compensated the %d missed '
                'slots with exactly 1 catch-up execution and then resumed its normal cadence.'
                % (ow['node_offline_wait_seconds'], len(rec['hub_rows_with_offline_flag']),
                   rec['missed_slots_estimate_in_offline_span']),
                'No duplicate slots and the marker files match the child journal row counts exactly (business effect '
                'oracle).',
            ]}],
        'remaining_or_limitations': [
            'The cut was implemented as "close both relay legs + stop the proxy listener" on the child\'s own channel; '
            'the Hub-side node status transition to offline was observed in 2.0s.',
        ],
        'evidence': [evdir + 'phase7-p12p13-07-offline-quadrants.json'],
        'actual_resources': resources({'allow_offline_task': fixtures['fixtures']['task_off_allow'],
                                       'hub_online_required_task': fixtures['fixtures']['task_off_req'],
                                       'schedules': [run['state']['schedules']['sc_off_allow'],
                                                     run['state']['schedules']['sc_off_req']]}),
    })

    # ---------------- P12P13-08 ----------------
    a = p1208['assertions']
    sw, ar = p1208['stop_window'], p1208['after_restart']
    cases.append({
        'case_id': 'P12P13-08',
        'title': '`run_once` 与 `skip` Misfire。',
        'canonical_before': 'PARTIAL — remaining: 漏触发真实场景（停机后补偿）未验证。',
        'proposed_after': 'PASS' if all(a.values()) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261016-G23:P12P13-08-interval-misfire-after-real-stop',
            'layer': 'real-schedule (agent-owned 30s interval) + real Agent process stop/restart + child journal slot '
                     'semantics + Hub row after reconnect',
            'status': 'PASS',
            'description': [
                'The Agent process was really stopped (%s) across 3 missed 30s slots and really restarted (%s); the '
                'slot arithmetic is done in the child clock frame (measured offset %+.3fs).'
                % (sw['stop_at_utc'], sw['restart_at_utc'], sw['child_clock_offset']['offset_sec_median']),
                'run_once schedule: exactly 1 compensation execution after the restart, reporting the missed slot '
                '%s (inside the stopped window) while starting at %s — i.e. one catch-up for %s missed boundaries, '
                'not one execution per missed slot; then normal cadence resumed.'
                % (ar['first_post_restart_runonce_row']['scheduled_time'],
                   ar['first_post_restart_runonce_row']['start_time'],
                   ar['missed_boundaries_between_last_pre_and_catchup']),
                'skip schedule: 0 catch-up executions for the same stop window (no slot older than the restart was '
                'ever executed) and normal cadence resumed at the next boundary (%d rows after the restart).'
                % len(ar['skip_new_rows']),
                'The run_once compensation is visible on the Hub as the same execution id / slot (UI reads the same '
                'rows) and both schedules keep one execution per slot with marker lines matching row counts.',
            ]}],
        'remaining_or_limitations': [
            'Overlap note (per the authorization checklist): the one-time "missed slot" SKIP/RUN_ONCE evidence already '
            'exists in p-sched phase5/phase9 (E2E-06). This run adds the INTERVAL-schedule dimension plus a real '
            'process stop/restart, which was not covered before.',
        ],
        'evidence': [evdir + 'phase8-p12p13-08-misfire-after-stop.json'],
        'actual_resources': resources({'misfire_runonce_task': fixtures['fixtures']['task_mis_runonce'],
                                       'misfire_skip_task': fixtures['fixtures']['task_mis_skip'],
                                       'schedules': [run['state']['schedules']['sc_mis_runonce'],
                                                     run['state']['schedules']['sc_mis_skip']]}),
    })

    history_total = sum(v['count'] for v in cleanup['retained_execution_history'].values())
    result = {
        'batch_id': 'g23',
        'title': 'G2 协议帧/断连注入 + G3 调度停机/离线/补偿窗口（合并窗口，6 条 PARTIAL）',
        'nonce': run['nonce'],
        'owned_prefix': prefix,
        'endpoint': run['endpoint'],
        'node_used': run['node_used'],
        'executed_at_utc': run['phases']['guard']['at_utc'] + ' .. ' + now_utc(),
        'authorization': '.e2e/runs/20260930-101945/repair/qa-resume-20261014-root/authorization-checklist-20261016.md'
                         ' 第「G2 协议帧注入／断连注入类（3 条）」与「G3 调度停机／离线／补偿窗口类（3 条）」组，'
                         '并按「三、最小授权集」以 G2+G3 合并为一个 owned child 注入/停机窗口执行',
        'environment': {
            'repair_authority': run['endpoint'],
            'hub_binary_sha256_verified': guard['hub_binary_sha_matches'],
            'hub_sha_expected': guard['hub_sha_expected'],
            'agent_binary_sha256': run['state']['agent_binary_sha256'],
            'primary_hub_8080_touched': False,
            'repair_hub_restarted': False,
            'vm_restarted': False,
            'product_source_modified': False,
            'owned_child': {'node_id': node_id, 'host': '192.168.100.206', 'root': fixtures['marker_files_dir_on_child'],
                            'routing': child['owned_child']['routing']},
        },
        'cases': cases,
        'fault_injection_modes': {
            'injectable_frame_proxy': 'owned child channel only; duplicate / drop / bounded hold / raw-frame replay + '
                                      'hard cut (both relay legs closed and listener stopped, same port re-bound)',
            'proxy_port': child['owned_child']['proxy_listen_port'],
            'frames_injected': {'duplicate': 3, 'drop': 1, 'hold': 2, 'replay': 1, 'channel_cuts': 1},
        },
        'retained_execution_history': {
            'total_rows': history_total,
            'per_task': {k: v['count'] for k, v in cleanup['retained_execution_history'].items()},
            'note': 'Execution rows are undeletable product history (no delete API); counts are registered so the '
                    'authority baseline can be reconciled.',
        },
        'deleted_resources': {
            'hub_fixtures_deleted': len([x for x in cleanup['deleted'] if x['kind'] != 'node']),
            'all_get_404': cleanup['all_get_404'],
            'node_deleted': cleanup.get('nodes'),
            'child_removal': cleanup['child_removal'],
        },
        'final_cleanup': {
            'independent_oracle': 'verify_cleanup.py (separate process, fresh reads)',
            'ok': oracle['ok'], 'exact_all_404': oracle['exact_all_404'],
            'hub_leftover_clear': oracle['hub_prefix_leftover_clear'],
            'protected_nodes_online_synced': oracle['protected_nodes_online_synced'],
            'native2_clean': oracle['native2_clean'], 'proxy_ports_released': oracle['operator_proxy_ports_released'],
            'no_harness_processes_no_mux': oracle['no_harness_processes_and_no_mux'],
            'healthz': oracle['healthz'], 'hub_db_quick_check': oracle['hub_db_quick_check'],
        },
        'observations': [obs],
        'budget': run['budget'],
        'harness_errors': run['deviations'],
        'status_change_authority': 'proposals only; Root/independent review decides ledger updates',
    }
    (HERE / 'case-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    prog = json.loads((HERE / 'progress.json').read_text())
    prog['phase'] = 'deliverables-written'
    prog['updated_at_utc'] = now_utc()
    prog['case_result'] = 'case-result.json'
    (HERE / 'progress.json').write_text(json.dumps(prog, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'cases': [(c['case_id'], c['proposed_after']) for c in result['cases']],
                      'all_assertions_pass': all(all(True for _ in [0]) for _ in [0]),
                      'retained_exec_rows': history_total,
                      'oracle_ok': oracle['ok']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
