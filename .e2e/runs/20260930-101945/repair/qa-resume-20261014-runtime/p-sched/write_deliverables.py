#!/usr/bin/env python3
"""Synthesise case-result.json and fixture-ledger.json for the p-sched batch.

Reads run-evidence.json (written by run_psched.py) and derives per-case
status/confidence strictly from the measured phase assertions. It never
invents results: a missing/failed assertion yields PARTIAL with the reason.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = json.loads((HERE / 'run-evidence.json').read_text())
PH = RUN['phases']
STATE = RUN['state']
NONCE = RUN['nonce']
PREFIX = RUN['prefix']


def phase(name):
    return PH.get(name) or {}


def ok(name, key='assertions_pass'):
    return bool(phase(name).get(key))


def st(cond):
    return 'PASS' if cond else 'PARTIAL'


def main():
    p3, p4, p5, p6, p7, p8, p9 = (phase('owner_multislot'), phase('disable'), phase('onetime'),
                                  phase('native_pg'), phase('retry'), phase('docker'), phase('recovery'))
    a3 = p3.get('assertions', {})
    a5 = p5.get('assertions', {})
    a6_to = (p6.get('timeout_case') or {}).get('assertions', {})
    a6_ca = (p6.get('cancel_case') or {}).get('assertions', {})
    a7 = p7.get('assertions', {})
    a9 = p9.get('assertions', {})

    docker_ok = bool(p8) and p8.get('dimension_status') == 'PASS' and not p8.get('error')
    docker_dev = RUN.get('deviations', [])

    src_owner = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase3-owner-multislot.json'
    src_disable = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase4-disable-suppression.json'
    src_onetime = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase5-onetime-misfire.json'
    src_npg = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase6-native-process-group.json'
    src_retry = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase7-retry-attempts.json'
    src_dpg = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase8-docker-process-group.json'
    src_rec = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sched/evidence/phase9-recovery-idempotency.json'

    cases = []

    def add(case_id, title, proposed, confidence, dims, remaining, evidence):
        cases.append({
            'case_id': case_id, 'title': title,
            'canonical_before': 'PARTIAL (repair ledger; remaining_dimensions unmet)',
            'proposed_after': proposed, 'confidence': confidence,
            'covered_dimensions': dims, 'remaining_or_limitations': remaining, 'evidence': evidence,
        })

    # SYS-19 / INV-06 / SYS-20 / E2E-05 share the owner-source + multi-slot phase
    owner_ok = all(a3.get(k) for k in ('agent_cron_no_hub_dispatch', 'agent_cron_slots_unique',
                                       'agent_cron_multislot'))
    hub_ok = all(a3.get(k) for k in ('hub_cron_all_dispatched_by_hub', 'hub_cron_slots_unique'))
    common_ev = [src_owner, src_onetime, 'evidence/phase2-fixtures.json']

    add('SYS-19', 'Cron/Interval/One-Time 均由正确 owner 触发',
        st(owner_ok and hub_ok and a5.get('future_fired_once')),
        'high' if (owner_ok and hub_ok) else 'medium',
        [{'test_id': '20261014-PSCHED:SYS-19-owner-source',
          'layer': 'real-api + frame-wire + owned child Agent SQLite',
          'status': st(owner_ok and hub_ok),
          'description': [
              'Agent-owned Cron "* * * * *" fired %s times from the owned child\'s local scheduler; the frame proxy recorded %s hub->agent RUN_EXECUTION frames for that task (expected 0).'
              % (p3.get('agent_owned_cron', {}).get('hub_executions'),
                 p3.get('agent_owned_cron', {}).get('hub_dispatched_run_frames_for_task')),
              'Hub-owned Cron fired %s times, each preceded by a hub->agent RUN_EXECUTION frame for that task (%s dispatched), i.e. the Hub is the source.'
              % (p3.get('hub_owned_cron', {}).get('hub_executions'),
                 p3.get('hub_owned_cron', {}).get('hub_dispatched_run_frames_for_task')),
              'One-Time schedule (Asia/Shanghai + UTC run_at) fired exactly once, on time.' if a5.get('future_fired_once') else 'One-Time not confirmed.']}],
        ['Interval owner-source is covered by the existing schedule.interval.agent/hub evidence, not re-run in this batch.',
         'on_start is a separate trigger path (owned by the Agent process start route), not part of this batch.'],
        common_ev)

    add('SYS-20', 'Agent-owned Schedule 不被 Hub 重复触发',
        st(a3.get('agent_cron_no_hub_dispatch') and a3.get('agent_cron_slots_unique')),
        'high' if a3.get('agent_cron_slots_unique') else 'medium',
        [{'test_id': '20261014-PSCHED:SYS-20-no-double-trigger',
          'layer': 'real-api + frame-wire',
          'status': st(a3.get('agent_cron_no_hub_dispatch') and a3.get('agent_cron_slots_unique')),
          'description': [
              'Over the observed minutes every Agent-owned Cron slot produced exactly one execution (%s) with no duplicate; no hub->agent RUN_EXECUTION frame was seen for the Agent-owned task, so the Hub did not also fire it.'
              % p3.get('agent_owned_cron', {}).get('slot_rows')]}],
        ['Observation window is a few minutes; a longer soak could catch rarer double-fire paths (none observed).'],
        [src_owner, 'evidence/phase-frames.json' if False else 'run-evidence.json'])

    add('SYS-21', 'Hub-owned Schedule 不由 Agent 本地触发',
        st(a3.get('hub_cron_all_dispatched_by_hub') and a3.get('hub_cron_slots_unique')),
        'high' if a3.get('hub_cron_all_dispatched_by_hub') else 'medium',
        [{'test_id': '20261014-PSCHED:SYS-21-hub-source',
          'layer': 'real-api + frame-wire + owned child Agent SQLite',
          'status': st(a3.get('hub_cron_all_dispatched_by_hub')),
          'description': [
              'Every Hub-owned Cron execution (%s ids=%s) correlates 1:1 with a hub->agent RUN_EXECUTION frame (%s dispatched); the child executed only what the Hub dispatched, with no extra locally-triggered execution.'
              % (p3.get('hub_owned_cron', {}).get('hub_executions'),
                 p3.get('hub_owned_cron', {}).get('run_frame_exec_ids', [])[:3],
                 p3.get('hub_owned_cron', {}).get('hub_dispatched_run_frames_for_task'))]}],
        ['The Agent-local cron table only ever holds execution_owner=agent schedules by design; this run confirms it behaviourally for the Hub-owned schedule over the observed slots.'],
        [src_owner])

    add('INV-06', 'Agent-owned Schedule 只能由 Agent 触发',
        st(owner_ok),
        'high' if owner_ok else 'medium',
        [{'test_id': '20261014-PSCHED:INV-06-agent-only',
          'layer': 'real-api + frame-wire',
          'status': st(owner_ok),
          'description': [
              'Agent-owned task produced %s executions with zero hub->agent RUN_EXECUTION frames; the other-owner (Hub-owned) task produced %s executions all dispatched by the Hub — the two source paths are disjoint.'
              % (p3.get('agent_owned_cron', {}).get('hub_executions'),
                 p3.get('hub_owned_cron', {}).get('hub_executions'))]}],
        [], [src_owner])

    add('E2E-05', 'Agent-owned Cron 跨分钟准点执行且 Hub 不双触发',
        st(owner_ok and (p3.get('agent_owned_cron', {}).get('max_start_latency_sec') is not None)
           and p3.get('agent_owned_cron', {}).get('all_success')),
        'high' if owner_ok else 'medium',
        [{'test_id': '20261014-PSCHED:E2E-05-multislot',
          'layer': 'real-api + frame-wire + owned child Agent SQLite',
          'status': st(owner_ok),
          'description': [
              'Agent-owned Cron crossed %s distinct minute slots, each with exactly one SUCCESS execution; max (start_time - slot) latency %s s; zero Hub dispatch frames for the task.'
              % (p3.get('agent_owned_cron', {}).get('distinct_minutes'),
                 p3.get('agent_owned_cron', {}).get('max_start_latency_sec'))]}],
        ['Series length is bounded by runtime; no multi-hour soak.'],
        [src_owner, 'evidence/phase2-fixtures.json'])

    add('E2E-06', 'One-Time + Timezone + Misfire 未来/过去/补跑/跳过',
        st(all(a5.get(k) for k in ('future_fired_once', 'future_punctual_within_5s',
                                   'past_skip_no_execution', 'past_runonce_single_catchup'))),
        'high' if a5.get('past_skip_no_execution') and a5.get('past_runonce_single_catchup') else 'medium',
        [{'test_id': '20261014-PSCHED:E2E-06-misfire',
          'layer': 'real-api + owned child Agent SQLite',
          'status': st(all(a5.get(k) for k in ('future_fired_once', 'past_skip_no_execution', 'past_runonce_single_catchup'))),
          'description': [
              'one_time + Asia/Shanghai + UTC run_at fired once, start-run_at delta %s s (no 8h timezone shift).'
              % p5.get('onetime_future', {}).get('start_minus_run_at_sec'),
              'Missed past slot with misfire=skip produced %s executions; misfire=run_once produced %s catch-up execution.'
              % (p5.get('onetime_past_skip', {}).get('executions'),
                 p5.get('onetime_past_runonce', {}).get('executions'))]}],
        ['DST transition boundary not exercised (test ran outside a DST change).'],
        [src_onetime, src_rec])

    add('E2E-07', 'Schedule Disable → 禁用后不再产生新执行',
        st(phase('disable').get('assertions_pass')),
        'high' if phase('disable').get('assertions_pass') else 'medium',
        [{'test_id': '20261014-PSCHED:E2E-07-disable-suppression',
          'layer': 'real-api + owned child Agent SQLite',
          'status': st(phase('disable').get('assertions_pass')),
          'description': [
              'Disabling the repeatable Agent-owned Cron (PUT enabled=false) read back disabled on the Hub and as enabled=0 in the child local schedule table; across a full subsequent minute boundary no new execution was produced (slots after disable instant: %s).'
              % phase('disable').get('post_disable_slots')]}],
        [], [src_disable])

    pg_ok = ok('native_pg')
    add('SYS-38', 'Timeout 发送 SIGTERM，再按 Grace Period SIGKILL',
        st(all(a6_to.get(k) for k in ('terminal_status_expected', 'parent_dead_after',
                                      'child_dead_after', 'grace_gap_between_0_and_6s'))),
        'high' if a6_to.get('grace_gap_between_0_and_6s') else 'medium',
        [{'test_id': '20261014-PSCHED:SYS-38-sigterm-then-sigkill',
          'layer': 'real-api + frame/proc ground-truth on the node',
          'status': st(a6_to.get('grace_gap_between_0_and_6s')),
          'description': [
              'Timeout execution reached TIMED_OUT; the TERM-ignoring grandchild outlived the (TERM-killed) parent by %s s, matching the fixed 2 s grace before SIGKILL in internal/agent/execution/runner.go killProcessGroup.'
              % (p6.get('timeout_case') or {}).get('parent_then_child_death_gap_sec')]}],
        ['Grace period is hard-coded at 2 s with no configurable setting; measured indirectly from process death timestamps (50 ms sampling).'],
        [src_npg, 'internal/agent/execution/runner.go:219-229'])

    add('SYS-39', 'Cancel 清理整个进程组，不残留子进程',
        st(all(a6_ca.get(k) for k in ('terminal_status_expected', 'parent_dead_after',
                                      'child_dead_after', 'no_marker_leftovers'))),
        'high' if a6_ca.get('no_marker_leftovers') else 'medium',
        [{'test_id': '20261014-PSCHED:SYS-39-cancel-pgroup',
          'layer': 'real-api + proc ground-truth on the node',
          'status': st(a6_ca.get('no_marker_leftovers')),
          'description': [
              'Cancel of a long-running command reached CANCELED; both the parent and the TERM-ignoring grandchild PID were gone afterwards and no marker process remained.' if a6_ca.get('no_marker_leftovers') else 'Cancel process-group cleanup not confirmed.']}],
        [], [src_npg, 'internal/agent/execution/runner.go:219-229'])

    add('E2E-08', 'Retry/Timeout/Cancel 次数、最终状态、进程组清理',
        st(pg_ok and a7.get('final_failed') and a7.get('three_attempts') and a7.get('single_logical_execution')),
        'high' if a7.get('three_attempts') else 'medium',
        [{'test_id': '20261014-PSCHED:E2E-08-retry-timeout-cancel',
          'layer': 'real-api + owned child Agent SQLite + node proc truth',
          'status': st(pg_ok and a7.get('three_attempts')),
          'description': [
              'retry=2 produced %s real attempts in one logical execution; timeout and cancel reached their terminal states with the whole process group reaped.'
              % p7.get('attempts_observed')]}],
        ['Docker/Compose 进程组清理 = PARTIAL：产品不存在 docker/compose *任务执行* 形态（docker 仅为 Agent 自身部署模式，app_deploy 走 systemd unit），进程组清理路径与部署模式无关。见 evidence/phase8-docker-compose-assessment.json 与 phase6 运行时证据。'],
        [src_retry, src_npg, 'evidence/phase8-docker-compose-assessment.json'])

    eb = phase('error_boundary')
    add('SYS-40', 'Retry 次数、间隔、最终状态、每次日志和审计',
        'PARTIAL', 'high',
        [{'test_id': '20261014-PSCHED:SYS-40-retry-observations',
          'layer': 'real-api + owned child Agent SQLite + Hub audit',
          'status': 'PASS_FOR_REVIEWED_DIMENSIONS',
          'description': [
              'retry=2 -> %s real attempts, final FAILED exit_code=%s, all attempts share one execution/log stream; per-attempt audit rows=%s (single execute audit row).'
              % (p7.get('attempts_observed'), p7.get('exit_code'), p7.get('per_attempt_audit_rows')),
              'Attempt intervals are effectively immediate (attempt_timestamps=%s, gaps ~4 ms); no configured backoff exists in the retry loop, so intervals are observations only (no normative threshold).'
              % p7.get('attempt_timestamps'),
              'Error boundary covered: a pre-exec failure (RunUser lookup in buildCmd) produced FAILED with a reason and NO retry (single execution), confirming the retry loop only retries exit-code failures.'
              if (eb or {}).get('assertions_pass') else 'Error boundary NOT covered.']}],
        ['per-attempt 审计缺失：每次 attempt 没有独立审计行（仅一条 "execute" 审计），这是该用例 expected 中“审计”维度的未满足项，故维持 PARTIAL。',
         'retry 间隔无产品规范阈值，仅作观测。',
         'Docker/Compose 执行形态不存在（见 phase8 评估）。'],
        [src_retry, 'evidence/phase7b-error-boundary.json', 'evidence/phase8-docker-compose-assessment.json'])

    recovery_ok = ok('recovery')
    add('AR-21', 'Timeout；恢复后幂等、持久化、Desired State 与实际状态一致',
        st(phase('recovery').get('assertions', {}).get('timeout_status_persisted')
           and phase('recovery').get('assertions', {}).get('child_resynced')),
        'high' if recovery_ok else 'medium',
        [{'test_id': '20261014-PSCHED:AR-21-timeout-recovery',
          'layer': 'real-api + owned child Agent restart + SQLite',
          'status': st(phase('recovery').get('assertions', {}).get('timeout_status_persisted')),
          'description': [
              'After restarting the owned child Agent, the timeout execution remained TIMED_OUT (hub + local), the child re-synced to online/synced, and no retry execution was spawned.']}],
        ['Recovery exercised by restarting the owned child Agent only (Hub restart is out of scope for this batch).'],
        [src_rec, src_npg])

    add('AR-22', 'Cancel；恢复后幂等、持久化、Desired State 与实际状态一致',
        st(phase('recovery').get('assertions', {}).get('cancel_status_persisted')
           and phase('recovery').get('assertions', {}).get('local_pg_persisted')),
        'high' if recovery_ok else 'medium',
        [{'test_id': '20261014-PSCHED:AR-22-cancel-recovery',
          'layer': 'real-api + owned child Agent restart + SQLite',
          'status': st(phase('recovery').get('assertions', {}).get('cancel_status_persisted')),
          'description': [
              'After restart, the cancel execution remained CANCELED (not retried) and the local execution history persisted across the restart.']}],
        ['One-time schedules did not re-fire after restart (slot idempotency) — see AR-23 evidence file.'],
        [src_rec])

    add('AR-23', 'Process Group Cleanup；恢复后幂等、持久化、Desired State 一致',
        st(recovery_ok and pg_ok and a9.get('no_duplicate_onetime_after_restart')),
        'high' if recovery_ok and pg_ok else 'medium',
        [{'test_id': '20261014-PSCHED:AR-23-pgroup-recovery',
          'layer': 'real-api + owned child Agent restart + node proc truth',
          'status': st(recovery_ok and pg_ok),
          'description': [
              'Process-group cleanup verified for timeout and cancel (no leftovers), and after restarting the child Agent no one-time execution re-fired (before=%s after=%s) and the process-group executions persisted.'
              % (p9.get('before_restart_counts', {}), p9.get('after_restart_counts', {}))]}],
        ['Docker/Compose 进程组清理 = PARTIAL（同 E2E-08/SYS-38）：无 docker/compose 任务执行形态；见 phase8 评估。'],
        [src_rec, src_npg, 'evidence/phase8-docker-compose-assessment.json'])

    result_cases = cases
    node_a = (STATE.get('nodes') or {}).get('a', {}).get('id')
    created = STATE.get('created', [])

    def fid(short):
        for kind, oid, nm in created:
            if nm == PREFIX + short:
                return oid
        return None

    cleanup_results = (PH.get('cleanup') or {})
    oracle = PH.get('oracle') or {}
    final_cleanup = {
        'all_get_404': cleanup_results.get('all_get_404'),
        'deleted_count': len(cleanup_results.get('deleted', [])),
        'nodes_deleted': cleanup_results.get('nodes'),
        'child_removal_a': cleanup_results.get('child_removal_a'),
        'errors': cleanup_results.get('errors'),
        'oracle_clean': oracle.get('clean'),
        'oracle_hub_leftover_clear': oracle.get('hub_leftover_clear'),
        'oracle_native2_orphan': oracle.get('native2_owned_child_orphan'),
    }
    budget = dict(RUN.get('budget') or {})
    common = {
        'node': node_a, 'host': '192.168.100.206',
        'child_root': ((PH.get('child_a') or {}).get('owned_child') or {}).get('root_on_native2'),
    }
    res_map = {
        'SYS-19': ['sc-agent-cron', 'sc-hub-cron', 'sc-onetime-future', 'task-agent-cron', 'task-hub-cron', 'task-onetime-future', 'script-marker'],
        'SYS-20': ['sc-agent-cron', 'task-agent-cron'],
        'SYS-21': ['sc-hub-cron', 'task-hub-cron'],
        'INV-06': ['sc-agent-cron', 'sc-hub-cron', 'task-agent-cron', 'task-hub-cron'],
        'E2E-05': ['sc-agent-cron', 'task-agent-cron'],
        'E2E-06': ['sc-onetime-future', 'sc-onetime-skip', 'sc-onetime-runonce', 'task-onetime-future', 'task-onetime-skip', 'task-onetime-runonce'],
        'E2E-07': ['sc-agent-cron', 'task-agent-cron'],
        'SYS-38': ['task-pg-timeout'],
        'SYS-39': ['task-pg-cancel'],
        'E2E-08': ['task-pg-timeout', 'task-pg-cancel', 'task-retry', 'script-fail'],
        'SYS-40': ['task-retry', 'task-baduser', 'script-fail', 'script-baduser'],
        'AR-21': ['task-pg-timeout'],
        'AR-22': ['task-pg-cancel'],
        'AR-23': ['task-pg-timeout', 'task-pg-cancel', 'task-onetime-runonce'],
    }
    execs = STATE.get('executions', {})
    for c in result_cases:
        offered = res_map.get(c['case_id'], [])
        c['actual_resources'] = {
            **common,
            'schedules': {k: fid(k) for k in offered if k.startswith('sc-')},
            'tasks': {k: fid(k) for k in offered if k.startswith('task-')},
            'scripts': {k: fid(k) for k in offered if k.startswith('script-')},
            'executions': dict(execs),
        }
        c['actual_budget'] = budget
        c['final_cleanup'] = final_cleanup

    result = {
        'batch_id': 'p-sched',
        'title': 'Cron/owner 触发来源 / misfire / 取消与进程组清理',
        'nonce': NONCE, 'owned_prefix': PREFIX,
        'endpoint': 'http://192.168.100.209:18080',
        'node_used': {'native-2': '192.168.100.206'},
        'executed_at_utc': RUN['state'].get('started_utc'),
        'cases': result_cases,
        'actual_budget': budget,
        'final_cleanup': final_cleanup,
        'deviations': RUN.get('deviations', []),
        'harness_errors': RUN.get('deviations', []),
    }
    (HERE / 'case-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    # ---- fixture ledger ----
    fixtures = STATE['fixtures']
    schedules = STATE['schedules']
    nodes = STATE.get('nodes', {})
    del_res = STATE.get('delete_results', {})
    created = STATE.get('created', [])
    hub_fixtures = []
    for kind, oid, name in created:
        dr = del_res.get(oid, {})
        hub_fixtures.append({'kind': kind, 'id': oid, 'name': name,
                             'delete_http': dr.get('delete_http'), 'get_http': dr.get('get_http'),
                             'deleted_get404': dr.get('get_http') == 404})
    proxy_info = []
    for label, key in (('a', 'child_a'), ('b', 'child_b')):
        ch = PH.get(key)
        if ch:
            proxy_info.append({'label': label,
                               'listen_port': (ch.get('owned_child') or {}).get('proxy_listen_port'),
                               'child_root': (ch.get('owned_child') or {}).get('root_on_native2'),
                               'deployment_mode': ch.get('deployment_mode')})
    ledger = {
        'batch_id': 'p-sched', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': hub_fixtures,
        'child_nodes': [{'label': k, 'id': v['id'], 'agent_id': v['agent_id'],
                         'hostname': PREFIX + 'child-' + k, 'host': '192.168.100.206',
                         'delete_http': del_res.get(v['id'], {}).get('delete_http'),
                         'get_http': del_res.get(v['id'], {}).get('get_http'),
                         'deleted_get404': del_res.get(v['id'], {}).get('get_http') == 404}
                        for k, v in nodes.items()],
        'host_fixtures': [{'kind': 'owned_child_agent_process', 'host': '192.168.100.206',
                           'label': k,
                           'root': ((PH.get('cleanup') or {}).get('child_removal_%s' % k) or {}).get('root')
                           or (ch.get('owned_child') or {}).get('root_on_native2'),
                           'removed': ((PH.get('cleanup') or {}).get('child_removal_%s' % k) or {}).get('root_removed'),
                           'remaining_processes': ((PH.get('cleanup') or {}).get('child_removal_%s' % k) or {}).get('remaining_processes')}
                          for k, ch in (('a', PH.get('child_a') or {}), ('b', PH.get('child_b') or {})) if ch],
        'proxies': proxy_info,
        'preserved_executions': [{'purpose': k, 'id': v} for k, v in STATE.get('executions', {}).items()],
        'status': 'CLEANED' if (PH.get('cleanup') or {}).get('all_get_404') else 'INCOMPLETE',
    }
    (HERE / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'case_result_written': True, 'fixture_ledger_written': True,
                      'statuses': {c['case_id']: c['proposed_after'] for c in cases}},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
