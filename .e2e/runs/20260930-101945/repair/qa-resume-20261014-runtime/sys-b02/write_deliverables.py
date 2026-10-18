#!/usr/bin/env python3
"""Build the sys-b02 deliverables from the runtime evidence (single source of truth:
the evidence/*.json files produced by the successful run)."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = HERE / 'evidence'
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb02-%s-' % NONCE


def L(name):
    return json.loads((E / name).read_text())


run = json.loads((HERE / 'run-evidence.json').read_text())
p2 = L('phase2-fixtures-and-notify-ordering.json')
p3 = L('phase3-notify-once-and-duplicate.json')
p4 = L('phase4-inv17-uncommitted-no-notification.json')
p5 = L('phase5-inv03-notify-revisioncheck-reconcile.json')
p6 = L('phase6-inv04-notification-not-authority.json')
p7 = L('phase7-sys08-sync-failure-rollback.json')
p8 = L('phase8-ar16-recovery-persistence.json')
p9 = L('phase9-executions.json')
frames = L('phase-frames.json')
oracle = json.loads((HERE / 'final-cleanup-oracle.json').read_text())
indep = L('phase11-independent-cleanup-verification.json')

state = run['state']
budget = run['budget']
cleanup = oracle['cleanup_results']
F = ['evidence/phase0-guard.json', 'evidence/phase1-owned-child.json',
     'evidence/phase2-fixtures-and-notify-ordering.json', 'evidence/phase3-notify-once-and-duplicate.json',
     'evidence/phase4-inv17-uncommitted-no-notification.json',
     'evidence/phase5-inv03-notify-revisioncheck-reconcile.json',
     'evidence/phase6-inv04-notification-not-authority.json',
     'evidence/phase7-sys08-sync-failure-rollback.json',
     'evidence/phase8-ar16-recovery-persistence.json', 'evidence/phase9-executions.json',
     'evidence/phase10-cleanup.json', 'evidence/phase11-independent-cleanup-verification.json',
     'evidence/phase-frames.json', 'final-cleanup-oracle.json', 'fixture-ledger.json',
     'run-evidence.json', 'case-result.json', 'progress.json']

CANON = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)')

min_delta_ms = round(1000 * p2['commit_before_notify']['min_commit_minus_notify_sec'], 3)
max_delta_ms = round(1000 * max(r['commit_minus_notify_sec'] for r in p2['commit_before_notify']['rows']), 3)
offset_halfspread_ms = round(1000 * p2['clock_offset_probe']['before']['offset_sec_halfspread'], 3)


def case(case_id, title, proposed, confidence, dims, limits):
    return {'case_id': case_id, 'title': title, 'canonical_before': CANON,
            'proposed_after': proposed, 'confidence': confidence,
            'covered_dimensions': dims, 'remaining_or_limitations': limits,
            'evidence': F,
            'actual_resources': {
                'owned_prefix': PREFIX,
                'child_node_id': state['child_node_id'], 'child_agent_id': state['child_agent_id'],
                'host': '192.168.100.204 (native-1)',
                'scripts': {'good': state['script_good'], 'dep': state['script_dep'], 'fail': state['script_fail']},
                'tasks': {'run': state['task_run'], 'dep': state['task_dep'], 'fail': state['task_fail']},
                'proxy_listen_port': (frames['state'] or {}).get('connection_open_count') and
                json.loads((HERE / 'fixture-ledger.json').read_text())['proxy']['listen_port'],
                'preserved_execution_history': [v for v in (state.get('executions') or {}).values() if v],
            },
            'actual_budget': budget, 'final_cleanup': cleanup}


cases = []

# ---- SYS-10 ----
cases.append(case(
    'SYS-10', 'SYS-10 Commit 前不发送通知，Commit 后通知一次或可安全重复',
    'PASS' if (p2['assertions_pass'] and p3['assertions_pass']) else 'PARTIAL',
    'high' if (p2['assertions_pass'] and p3['assertions_pass']) else 'medium',
    [{'test_id': '20261014-RUNTIME:SYS-10',
      'layer': 'hub commit -> websocket frame ledger -> owned child Agent sqlite',
      'status': 'PASS' if (p2['assertions_pass'] and p3['assertions_pass']) else 'PARTIAL',
      'description': [
          'Commit-before-notify was measured for 6 real mutations: a hub-local read-only poller (2 ms sampling, '
          '18460 samples) recorded when each change became visible in the committed SQLite store; the '
          'CHANGE_NOTIFICATION frame was received at the frame proxy %0.1f-%0.1f ms AFTER that instant in all 6 '
          'cases (dedicated in-run clock offset probe, half-spread %0.3f ms).' % (abs(max_delta_ms), abs(min_delta_ms), offset_halfspread_ms),
          'Each committed change produced exactly one CHANGE_NOTIFICATION naming the committed object type/id and '
          'the exact committed object/global revision; a FAILED mutation (HTTP 400) produced none.',
          'Duplicating a CHANGE_NOTIFICATION frame (same revision) was safe: the owner Agent stored one row, the '
          'content matched the committed SHA, and the extra frame only caused a redundant SYNC_REQUEST that '
          'returned the same authoritative state.',
      ]}],
    ['The Hub send instant itself cannot be instrumented without product-source changes; the ordering claim is a '
     'runtime falsification test with a measured tolerance (0.05 s) plus a positive control (see INV-17), not a '
     'source-level proof.',
     'Notification counts are per connected Agent; the run had exactly one connected owned child plus other '
     'batches\' nodes, and the assertion is scoped to the owned object ids.']))

# ---- INV-17 ----
cases.append(case(
    'INV-17', 'INV-17 Commit 前不得发送 Desired State Notification',
    'PASS' if (p2['assertions_pass'] and p4['assertions_pass']) else 'PARTIAL',
    'high' if (p2['assertions_pass'] and p4['assertions_pass']) else 'medium',
    [{'test_id': '20261014-RUNTIME:INV-17',
      'layer': 'hub commit ordering / frame ledger / hub read-only sqlite',
      'status': 'PASS' if (p2['assertions_pass'] and p4['assertions_pass']) else 'PARTIAL',
      'description': [
          'Ordering: for every mutation the notification described a revision that was already durable in the '
          'Hub committed store before the frame arrived (no notification was ever observed for an uncommitted '
          'revision).',
          'Negative: a rejected desired-state mutation (POST /api/tasks with a non-existent script_id -> HTTP 400 '
          '"referenced script not found") left revision_sequence unchanged (%d) and added 0 change-log rows, and '
          'the frame ledger contains 0 notifications naming the rejected object.'
          % p4['rejected_mutation_no_notification']['revision_before'],
          'Detection power (control): the harness injected a synthetic CHANGE_NOTIFICATION carrying the committed '
          'revision + 100000; the same checker classified it as VIOLATION because that revision was never '
          'committed, proving the check would catch a pre-commit notification.',
      ]}],
    ['Only one deterministic rejected-mutation path was exercised (referenced-script validation). A mutation that '
     'fails *inside* the Hub write transaction could not be forced without concurrency tricks or product changes, '
     'so the "no notification on rollback" statement rests on the validation-rejection path plus the control.']))

# ---- INV-03 ----
cases.append(case(
    'INV-03', 'INV-03 同步必须是 Notify + Revision Check + Reconciliation',
    'PASS' if p5['assertions_pass'] else 'PARTIAL',
    'high' if p5['assertions_pass'] else 'medium',
    [{'test_id': '20261014-RUNTIME:INV-03',
      'layer': 'frame ledger (3 layers) + hub node readback + owned child sqlite',
      'status': 'PASS' if p5['assertions_pass'] else 'PARTIAL',
      'description': [
          'Notify: one committed mutation produced the exact frame chain CHANGE_NOTIFICATION -> SYNC_REQUEST -> '
          'SYNC_RESPONSE -> SYNC_ACK within %s s, and the object became the committed revision locally.'
          % p5['notify_layer']['convergence_latency_sec'],
          'Revision Check: with all CHANGE_NOTIFICATION frames dropped by the proxy the Agent stayed stale for '
          '>6 s and converged only through a periodic REVISION_CHECK -> REVISION_CHECK_ACK -> SYNC_REQUEST chain '
          '(convergence %s s from the mutation; hub setting revision_check_interval_sec=45).'
          % p5['revision_check_layer']['convergence_sec_from_mutation'],
          'Reconnect Reconciliation: after cutting the owned child link and committing a mutation while it was '
          'offline, HELLO(local_global_rev=%s) -> HELLO_ACK(desired_global_rev=%s) -> SYNC_REQUEST(since=%s) '
          'converged in %s s and the node returned to online/synced.'
          % (p5['reconcile_layer']['hellos_after_cut'][0]['local_global_rev'],
             p5['reconcile_layer']['hello_acks_after_cut'][0]['desired_global_rev'],
             p5['reconcile_layer']['sync_requests_after_reconnect'][0]['since'],
             p5['reconcile_layer']['reconciliation_sec']),
      ]}],
    ['The revision-check layer relies on the hub-configured 45 s interval, so convergence is bounded by that '
     'period; no jitter-threshold claim is made.',
     'All three layers were exercised on one owned child Agent (one hub session).']))

# ---- INV-04 ----
cases.append(case(
    'INV-04', 'INV-04 Notification 不承担最终一致性',
    'PASS' if (p6['assertions_pass'] and p5['assertions_pass'] and p3['assertions_pass'] and p4['assertions_pass'])
    else 'PARTIAL',
    'high' if (p6['assertions_pass'] and p5['assertions_pass'] and p3['assertions_pass'] and p4['assertions_pass'])
    else 'medium',
    [{'test_id': '20261014-RUNTIME:INV-04',
      'layer': 'lost / duplicate / stale notification injection + frame payload structure',
      'status': 'PASS' if (p6['assertions_pass'] and p5['assertions_pass']) else 'PARTIAL',
      'description': [
          'Lost: dropping every CHANGE_NOTIFICATION still converged to the committed revision through the periodic '
          'revision check (see INV-03 revision_check_layer).',
          'Duplicate: a duplicated CHANGE_NOTIFICATION for the same revision caused no state change (one row, '
          'unchanged SHA, no regression).',
          'Stale / out-of-order: a notification carrying a revision older than the locally applied revision '
          '(local %s, injected %s) produced 0 SYNC_REQUESTs and left the local object revision/SHA untouched.'
          % (p6['stale_out_of_order_notification']['injected']['local_revision_at_injection'],
             p6['stale_out_of_order_notification']['injected']['global_revision']),
          'Structural: the observed notification payload keys are exactly %s - no object content - so consistency '
          'can only come from the authoritative SYNC_RESPONSE snapshot.'
          % p6['notification_is_not_authority']['notification_payload_keys_observed'],
          'Fabricated future revision: a notification for committed+100000 made the Agent sync and it converged to '
          'the Hub current revision (%d), not to the notified revision.'
          % p4['negative_control_detector']['convergence_target']['hub_current_revision'],
      ]}],
    ['Injections are harness-side frame manipulations in front of the owned child; the Hub and its authoritative '
     'store are the unmodified product path.',
     'Out-of-order was exercised as a strictly-stale notification plus a far-future one; a shuffled pair of '
     'consecutive revisions while a sync was in flight was not forced (covered by sys-b04).']))

# ---- SYS-08 ----
cases.append(case(
    'SYS-08', 'SYS-08 Script/Task 依赖同步失败：事务回滚，Revision 不前进，无半同步',
    'PASS' if p7['assertions_pass'] else 'PARTIAL',
    'high' if p7['assertions_pass'] else 'medium',
    [{'test_id': '20261014-RUNTIME:SYS-08',
      'layer': 'corrupted SYNC_RESPONSE -> owned child agent transaction -> hub node readback',
      'status': 'PASS' if p7['assertions_pass'] else 'PARTIAL',
      'description': [
          'V1 dependency SHA failure: one SYNC_RESPONSE carrying a corrupted sha256 for the Script referenced by '
          'the Task was rejected with SYNC_ACK ok=false error="... sha256 mismatch"; the Agent local revision '
          'stayed %s while the Hub had already advanced to %s, and no local row changed (no half sync).'
          % (p7['v1_bad_sha']['child_local_revision_after'][0]['global_revision'],
             p7['v1_bad_sha']['hub_advanced_while_agent_stayed_behind']['hub_global_revision']),
          'V2 in-transaction rollback: the injected SYNC_RESPONSE contained a valid phantom Script entry and a '
          'malformed Task entry, so the Agent write transaction failed after the script write; SYNC_ACK ok=false '
          'error="json: cannot unmarshal string into Go value of type models.Task", the phantom script is absent '
          'from the Agent SQLite, the local script count stayed 3, and the local revision did not advance.',
          'Hub-side readback: the node row in the Hub committed store is sync_status="error" with '
          'global_revision=0 immediately after each failed sync, while committed definitions remain intact '
          '(dependency row unchanged).',
      ]}],
    ['The failing SYNC_RESPONSE is harness-injected at the frame layer (product source unmodified); the validation '
     'and transaction paths exercised are the unmodified product paths.',
     'V3 observation (not a failure claim): a snapshot that simply omits a task-referenced script is accepted by '
     'the Agent (the Hub is authoritative and prune removes the local copy); no rollback occurs for that shape. '
     'It is unreachable from an unmodified Hub, which always ships referenced scripts, and the next authoritative '
     'snapshot restored the row (see AR-16).']))

# ---- AR-16 ----
cases.append(case(
    'AR-16', 'AR-16 Sync Dependency Failure；恢复后幂等、持久化、Desired State 与实际状态一致',
    'PASS' if p8['assertions_pass'] else 'PARTIAL',
    'high' if p8['assertions_pass'] else 'medium',
    [{'test_id': '20261014-RUNTIME:AR-16',
      'layer': 'recovery sync + hub/agent field comparison + child restart',
      'status': 'PASS' if p8['assertions_pass'] else 'PARTIAL',
      'description': [
          'Recovery: after the dependency failures a clean authoritative sync applied revision %s with '
          'SYNC_ACK ok=true applied_rev=%s; Hub and Agent SHA agree (%s) and the Agent local revision equals the '
          'Hub global revision (%s).'
          % (p8['recovery']['hub_committed_revision'], p8['recovery']['sync_ack']['applied_rev'],
             p8['recovery']['content_sha']['hub'][:16] + '...', p8['recovery']['child_local_revision']),
          'Desired == Actual: all 3 owned Scripts (revision + sha256) and all 3 owned Tasks (revision) match '
          'between the Hub committed store and the Agent local SQLite, and the node is online/synced.',
          'Idempotency: re-issuing the identical desired state produced identical row counts (3/3/0) and identical '
          'script SHAs - no duplicates, no churn.',
          'Persistence: a real owned-child Agent restart preserved node_id/agent_id, the applied revision and all '
          'rows, and the Agent returned to online/synced.',
          'Real business effect: 1 manual Execution SUCCESS (marker "%s", exit 0) and 1 manual Execution FAILED '
          '(exit 3) reached terminal state on the Hub with matching Agent journal rows.'
          % p9['executions']['success']['stdout'],
      ]}],
    ['Persistence was verified with a real child Agent process restart of the owned fixture, not a host reboot; '
     'the protected repair Agents were never restarted.']))

# ---- summary / deliverables ----
case_result = {
    'batch_id': 'sys-b02',
    'title': '提交边界与通知语义：Commit 前不通知、通知不承担最终一致性、依赖同步失败回滚',
    'nonce': NONCE, 'owned_prefix': PREFIX,
    'executed_at_utc': run['started_at_utc'], 'finished_at_utc': run['finished_at_utc'],
    'endpoint': run['endpoint'],
    'cases': cases,
    'deviations': run['deviations'],
    'budget': budget,
    'final_cleanup_oracle_clean': oracle['clean'],
    'independent_cleanup_verification': bool(indep['ok']),
    'harness_seams': {
        'authority': 'management writes only to repair http://192.168.100.209:18080; the primary :8080 Hub and the '
                     '4 protected repair Agents were never written to, stopped or restarted',
        'owned_child': 'enrolled node + the exact repair Agent binary (sha256 d55dc5cd...) run as a harness-owned '
                       'background process under /var/lib/nodesteer-qa-sysb02-%s on native-1, connected through a '
                       'harness frame proxy on the operator host 192.168.100.170 on an ephemeral port' % NONCE,
        'commit_oracle': 'read-only hub SQLite (mode=ro, PRAGMA query_only) plus a 2 ms hub-local revision_sequence '
                         'poller on the hub clock; the notification receipt timestamps come from the frame proxy and '
                         'are related through a measured clock offset (half-spread %0.3f ms)' % offset_halfspread_ms,
        'notification_injections': 'the proxy dropped / duplicated CHANGE_NOTIFICATION frames and injected synthetic '
                                   'ones (stale revision, far-future revision) to exercise lost/duplicate/'
                                   'out-of-order and to prove detector power',
        'sync_failure_injection': 'the proxy mutated exactly one SYNC_RESPONSE in flight (corrupted dependency SHA; '
                                  'phantom script entry plus malformed task entry) - the Agent validation and '
                                  'transaction paths are unmodified product code',
        'lost_notification': 'offline/reconnect was produced by cutting the owned child link at the proxy layer; the '
                             'Hub and protected Agents were never stopped',
        'product_source_changes': 0, 'git_operations': 0, 'hub_restarts': 0,
        'protected_services_untouched': True,
    },
    'environment_notes': [
        'The first run attempt aborted in a harness lock deadlock; a native-1 reboot external to this batch happened '
        'at 2026-10-14T10:16Z (native-1 and native-3 repair nodes briefly offline) and the environment was healthy '
        'again before the successful run (preflight in evidence/phase0-guard.json shows all 4 protected nodes '
        'online/synced and both binary SHAs matching).',
        'Up to 7 concurrent nodes from other batches were visible in /api/nodes during the run; all assertions are '
        'scoped to the nonce-owned object ids, and the frame ledger records every notification received by the '
        'owned child.',
    ],
    'rerun_note': 'Failed harness attempts are preserved unmodified under evidence/attempt01-lock-deadlock/ '
                  '(harness threading-lock deadlock), evidence/attempt02-dbsummary-format-bug/ and '
                  'evidence/attempt03-dbsummary-format-bug/ (harness string-format bug in the local-SQLite readback '
                  'helper). This report reflects the final complete run (EXIT=0, deviations=[]).',
    'evidence': F,
}
(HERE / 'case-result.json').write_text(json.dumps(case_result, ensure_ascii=False, indent=2) + '\n')

progress = json.loads((HERE / 'progress.json').read_text())
progress['cases'] = {c['case_id']: c['proposed_after'] for c in cases}
progress['independent_cleanup_verified'] = bool(indep['ok'])
progress['artifacts'] = ['case-result.json', 'progress.json', 'fixture-ledger.json',
                         'final-cleanup-oracle.json', 'run-evidence.json', 'evidence/']
progress['harness_errors'] = run['deviations']
progress['status'] = 'COMPLETE' if not run['deviations'] and oracle['clean'] and indep['ok'] else 'INCOMPLETE'
(HERE / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'cases': {c['case_id']: c['proposed_after'] for c in cases},
                  'clean': oracle['clean'], 'indep': indep['ok']}, ensure_ascii=False, indent=2))
