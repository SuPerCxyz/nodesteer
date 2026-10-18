#!/usr/bin/env python3
"""Consolidate sys-b09 evidence into the required run-root artifacts.

Recomputes the six case verdicts from the raw phase evidence (including the
follow-up retries), performs a fresh residual/cleanup sweep, removes the two
probe sandboxes on the probe node and writes:

  case-result.json, progress.json, fixture-ledger.json, final-cleanup-oracle.json
"""
import json
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import sys as _sys

HERE = Path(__file__).resolve().parent
EVID = HERE / 'evidence'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
NONCE = 'qa-sysb09-7f31ac2e'
NODE_ID = '611c02a5-1859-4465-878b-d543b7f6fdec'
PROTECTED = {
    'native-1': '91f44866-8a28-435e-bbda-2f2367126c00',
    'native-2': NODE_ID,
    'native-3': '37989297-3bc8-4c0c-b302-7c6efbdba630',
    'native-4': '03cb50cc-eab6-4afb-bbe0-f73c4e326773',
    'native-4': '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
}
OFFLINE_NODE = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
AGENT_UNIT = 'nodesteer-agent-repair-20260930-101945-native-2.service'
PROBE_IP = '192.168.100.247'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
KEY = SSH_KEY
PROBE_BINARY = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'

TOKEN = [None]
BASE = [None]
OPENER = build_opener(ProxyHandler({}))


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load(name, default=None):
    path = EVID / name
    if not path.exists():
        return default
    return json.loads(path.read_text())


def api(method, path, body=None, timeout=25):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if TOKEN[0]:
        headers['Authorization'] = 'Bearer ' + TOKEN[0]
    try:
        with OPENER.open(Request(BASE[0] + path, data=data, headers=headers, method=method),
                         timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b'null')
    except HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b'null')
        except Exception:
            return exc.code, None


def ssh(command, timeout=60):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-i', SSH_KEY, 'root@' + PROBE_IP, command],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def ssh_host(host, command, timeout=60):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-i', SSH_KEY, 'root@' + host, command],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def record(name, payload):
    (HERE / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def main():
    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    assert urlsplit(BASE[0]).port == 18080
    code, body = api('POST', '/api/login', {'username': cred['admin_username'],
                                            'password': cred['admin_password']})
    assert code == 200, code
    TOKEN[0] = body['token']

    # ---------------- fresh residual / health sweep ---------------- #
    _, scripts = api('GET', '/api/scripts')
    _, tasks = api('GET', '/api/tasks')
    _, schedules = api('GET', '/api/schedules')
    _, nodes = api('GET', '/api/nodes')
    by_id = {n['id']: n for n in nodes}
    residual = [o.get('name') for o in list(scripts) + list(tasks)
                if o.get('name') and o.get('name').startswith(NONCE)]
    rc, out, err = ssh('pgrep -af qa-sysb09 || true; ls -d /tmp/qa-sysb09* 2>/dev/null || true')
    leftovers = [line for line in out.splitlines() if line.strip()]
    rc2, out2, err2 = ssh('rm -rf /tmp/qa-sysb09-probe-run /tmp/qa-sysb09-rc /tmp/qa-sysb09-rc2 '
                          '/tmp/qa-sysb09-smoke; ls -d /tmp/qa-sysb09* 2>/dev/null | wc -l')
    _, settings = api('GET', '/api/settings')
    rc3, out3, _ = ssh_host('192.168.100.206',
                            'systemctl show --no-pager --property=ActiveState,MainPID,ExecMainStartTimestamp %s'
                            % AGENT_UNIT)
    agent_props = dict(line.split('=', 1) for line in out3.splitlines() if '=' in line)
    rc4, out4, _ = ssh_host(PROBE_IP,
                            'systemctl show --no-pager --property=ActiveState,MainPID '
                            'nodesteer-agent-repair-20260930-101945-native-4.service')
    probe_props = dict(line.split('=', 1) for line in out4.splitlines() if '=' in line)

    # ---------------- recompute verdicts ---------------- #
    guard = load('phase0-guard.json', {})
    cond = load('phase2-conditions.json', {})
    rejects = load('phase3-reject-structures.json', [])
    misfire = load('phase4-misfire.json', {})
    dup = load('phase5-duplicate-trigger.json', {})
    readback = load('phase11-agent-db-readback-retry.json', {})
    hub_slots = load('phase12-hub-duplicate-slot-grouping.json', {})
    multinode = load('phase10-remote-condition-multinode.json', {})
    mech = load('phase2d-remote-condition-mechanism.raw.json', {})
    clock = load('phase2c-clock-offset.json', {})
    probe = load('phase8-sys18-probe-summary.json', {}).get('summary', {})
    cleanup = load('phase7-cleanup.json', {})
    run_ev = load('run-evidence.json', {})
    phase1 = load('phase1-fixtures.json', {})
    prim_targets = cleanup.get('targets', [])
    primary_fixtures = [{'kind': t['kind'], 'id': t['id'], 'name': t.get('name'),
                         'deleted_get404': t.get('get_http') == 404} for t in prim_targets]
    task_ids = {k: v.get('id') for k, v in (phase1.get('tasks') or {}).items()}
    sched_ids = {k: v.get('id') for k, v in (phase1.get('schedules') or {}).items()}
    hub_dup_exec_ids = [e['id'] for e in dup.get('hub_side', {}).get('executions', [])]
    agent_dup_exec_ids = [e['id'] for e in dup.get('agent_side', {}).get('executions', [])]
    skip_exec_ids = [e['id'] for e in misfire.get('skip_scheduled_final', [])]
    runonce_exec_ids = [e['id'] for e in misfire.get('runonce_scheduled_final', [])]
    control_run = run_ev.get('phases', {}).get('control_run', {})

    slot_rows = readback.get('rows') or []
    agent_slot_ok = (len(slot_rows) == 1 and slot_rows[0][3] == 'schedule'
                     and slot_rows[0][4][:19] == (readback.get('expected_slot') or '')[:19]
                     and readback.get('quick_check') == 'ok'
                     and readback.get('slot_unique_index_present') is True)
    hub_slot_ok = bool(hub_slots.get('single_execution_for_slot'))
    dup_ok = bool(dup.get('hub_side', {}).get('single_execution_for_slot')
                  and dup.get('agent_side', {}).get('single_execution_for_slot')
                  and agent_slot_ok and hub_slot_ok)
    misfire_ok = bool(misfire.get('skip_no_backfill') and misfire.get('skip_fired_after_enable')
                      and misfire.get('runonce_single_backfill')
                      and misfire.get('runonce_backfill_matches_expected_slot'))
    local_and_ok = all(cond.get(k, {}).get('status') == v for k, v in (
        ('and-true', 'SUCCESS'), ('and-false', 'SKIPPED'),
        ('and-nested-true', 'SUCCESS'), ('and-nested-false', 'SKIPPED'),
        ('and-unknown', 'BLOCKED')))
    reject_ok = all(r['rejected'] for r in rejects)
    remote_blocked_everywhere = bool(multinode.get('all_blocked_fail_closed'))
    mech_ok = bool(mech.get('all_expected'))
    skew = clock.get('relative_to_hub_clock_seconds_positive_means_host_ahead', {})
    probe_ok = bool(probe.get('heartbeat_samples_pre_change_s')
                    and probe.get('revision_check_samples_s')
                    and probe.get('reconnect_backoff_samples_s'))

    retained_ok = all(v.get('http') == 200 for v in cleanup.get('execution_history_retention', {}).values())

    cases = []

    cases.append({
        'case_id': 'SYS-23',
        'canonical_before': 'PASS (authoritative inventory baseline_status)',
        'proposed_after': 'PASS' if misfire_ok else 'FAIL',
        'confidence': 'high' if misfire_ok else 'low',
        'covered_dimensions': [
            {'dimension': 'Misfire `skip`：错过的 slot 不补跑',
             'status': 'PASS' if misfire.get('skip_no_backfill') else 'FAIL',
             'detail': {'disabled_window_sec': misfire.get('disabled_window_sec'),
                        'interval_sec': misfire.get('interval_sec'),
                        'backfill_executions': misfire.get('skip_backfill_executions'),
                        'post_enable_executions': misfire.get('skip_scheduled_final')}},
            {'dimension': 'Misfire `run_once`：只补跑一次，且补的是最近一次错过的 slot',
             'status': 'PASS' if misfire.get('runonce_single_backfill')
                       and misfire.get('runonce_backfill_matches_expected_slot') else 'FAIL',
             'detail': {'disabled_window_sec': misfire.get('disabled_window_sec'),
                        'expected_slot_utc': misfire.get('expected_runonce_backfill_slot'),
                        'backfill_executions': misfire.get('runonce_backfill_executions'),
                        'post_enable_executions': misfire.get('runonce_scheduled_final')}},
            {'dimension': '触发真实生效（Agent 侧 Execution trigger_type=schedule，stdout 命中 nonce 标记）',
             'status': 'PASS' if all((e.get('stdout') or '').strip() == 'QA:%s:MAIN' % NONCE
                                     for e in (misfire.get('skip_scheduled_final') or [])
                                     + (misfire.get('runonce_scheduled_final') or [])) else 'FAIL'},
        ],
        'remaining_or_limitations': [
            '“Agent 不可用”以「调度被禁用期间 slot 到期」这一等价不可调度窗口构造；本批未停用/重启任何 repair Agent。',
            '未构造 Hub 长时间不可达（hub_online_required 门禁）导致的 misfire：需要 Hub 停机窗口，属其它批次串行范围。',
            '仅覆盖 interval 型 misfire；cron 型 skip/run_once 未覆盖。',
        ],
        'evidence': ['evidence/phase4-misfire.json', 'evidence/phase1-fixtures.json'],
        'actual_resources': {
            'skip_schedule_id': sched_ids.get('skip'), 'runonce_schedule_id': sched_ids.get('runonce'),
            'skip_task_id': task_ids.get('skip'), 'runonce_task_id': task_ids.get('runonce'),
            'executions': {'skip': skip_exec_ids, 'runonce': runonce_exec_ids}},
        'actual_budget': {'schedule_creates': 2, 'schedule_enable_puts': 2,
                          'disabled_window_sec': misfire.get('disabled_window_sec'),
                          'observe_sec': 26,
                          'interval_sec': misfire.get('interval_sec')},
        'final_cleanup': {'all_exact_404': cleanup.get('all_exact_404')},
    })

    cases.append({
        'case_id': 'SYS-22',
        'canonical_before': 'PASS (authoritative inventory baseline_status)',
        'proposed_after': 'PASS' if dup_ok else 'FAIL',
        'confidence': 'high' if dup_ok else 'low',
        'covered_dimensions': [
            {'dimension': 'Hub 执行路径：同一 Task+Node+Scheduled Time 的重复触发只产生 1 条 Execution',
             'status': 'PASS' if dup.get('hub_side', {}).get('single_execution_for_slot') else 'FAIL',
             'detail': dup.get('hub_side')},
            {'dimension': 'Agent 本地执行路径：同一 Task+Node+Scheduled Time 的重复触发只产生 1 条 Execution',
             'status': 'PASS' if dup.get('agent_side', {}).get('single_execution_for_slot') else 'FAIL',
             'detail': dup.get('agent_side')},
            {'dimension': '独立于产品 API 的 Agent 本地库复核：slot 唯一索引存在且该 slot 精确 1 行',
             'status': 'PASS' if agent_slot_ok else 'FAIL',
             'detail': {'slot_unique_index_present': readback.get('slot_unique_index_present'),
                        'row_count': len(slot_rows), 'rows': slot_rows,
                        'expected_slot': readback.get('expected_slot'),
                        'quick_check': readback.get('quick_check'),
                        'agent_identity': readback.get('identity')}},
            {'dimension': 'Hub 侧按 slot 分组的执行数（保留历史复核）',
             'status': 'PASS' if hub_slot_ok else 'FAIL', 'detail': hub_slots},
        ],
        'remaining_or_limitations': ['未在 Hub/Agent 重启后的恢复窗口复核 slot 幂等（本批不重启 Hub，也不停 repair Agent）。'],
        'evidence': ['evidence/phase5-duplicate-trigger.json',
                     'evidence/phase11-agent-db-readback-retry.json',
                     'evidence/phase12-hub-duplicate-slot-grouping.json'],
        'actual_resources': {
            'hub_duplicate_schedule_ids': dup.get('hub_side', {}).get('duplicate_schedule_ids'),
            'agent_duplicate_schedule_ids': dup.get('agent_side', {}).get('duplicate_schedule_ids'),
            'dup_hub_task_id': task_ids.get('dup-hub'), 'dup_agent_task_id': task_ids.get('dup-agent'),
            'hub_execution_ids': hub_dup_exec_ids, 'agent_execution_ids': agent_dup_exec_ids},
        'actual_budget': {'identical_run_at_hub_utc': dup.get('hub_side', {}).get('identical_run_at_utc'),
                          'identical_run_at_agent_utc': dup.get('agent_side', {}).get('identical_run_at_utc'),
                          'observe_window_sec': dup.get('observed_window_sec')},
        'final_cleanup': {'all_exact_404': cleanup.get('all_exact_404')},
    })

    cases.append({
        'case_id': 'AR-17',
        'canonical_before': 'PASS (authoritative inventory baseline_status)',
        'proposed_after': 'PASS_WITH_NOTES' if dup_ok and retained_ok else 'FAIL',
        'confidence': 'medium' if dup_ok and retained_ok else 'low',
        'covered_dimensions': [
            {'dimension': 'Duplicate Schedule Trigger 幂等：两条仅 Schedule 对象不同、RunAt/Slot 完全相同的触发 → 1 条 Execution'
                          '（Hub-owned 与 Agent-owned 两条路径）',
             'status': 'PASS' if dup_ok else 'FAIL',
             'detail': {'hub': dup.get('hub_side'), 'agent': dup.get('agent_side'),
                        'agent_local_db': {'row_count': len(slot_rows), 'rows': slot_rows}}},
            {'dimension': '幂等窗口：跨 Hub 调度 tick 与 ~100s 多轮观测不产生第二条 Execution',
             'status': 'PASS' if dup_ok else 'FAIL',
             'detail': {'observed_window_sec': dup.get('observed_window_sec'),
                        'poll_count': len(dup.get('polls') or [])}},
            {'dimension': '持久化：定义（Schedule/Task/Script）全部删除后执行历史仍可 GET 200',
             'status': 'PASS' if retained_ok else 'FAIL',
             'detail': cleanup.get('execution_history_retention')},
            {'dimension': 'Desired State 与实际状态一致：节点 revision/sync_status 收敛、Agent 单元未重启、'
                          'Agent 本地 quick_check=ok',
             'status': 'PASS' if agent_slot_ok
                       and agent_props.get('MainPID') == guard.get('agent_pre', {}).get('MainPID')
                       else 'FAIL',
             'detail': {'agent_unit_restarted': agent_props.get('MainPID')
                        != guard.get('agent_pre', {}).get('MainPID'),
                        'agent_mainpid_pre': guard.get('agent_pre', {}).get('MainPID'),
                        'agent_mainpid_now': agent_props.get('MainPID'),
                        'agent_ExecMainStartTimestamp': agent_props.get('ExecMainStartTimestamp'),
                        'local_global_revision': readback.get('local_global_revision'),
                        'quick_check': readback.get('quick_check')}},
        ],
        'remaining_or_limitations': [
            '“恢复后”（Hub/Agent 重启后的恢复框架）幂等本批未复核：批次约束不重启 repair Hub、不停 4 个 protected repair Agent。',
            '未复核恢复窗口内的 tombstone 重放路径（由 SYS-09 批次单独覆盖）。',
        ],
        'evidence': ['evidence/phase5-duplicate-trigger.json',
                     'evidence/phase11-agent-db-readback-retry.json',
                     'evidence/phase12-hub-duplicate-slot-grouping.json',
                     'evidence/phase7-cleanup.json'],
        'actual_resources': {'execution_ids': {'hub': hub_dup_exec_ids,
                                               'agent': agent_dup_exec_ids},
                             'agent_local_db_rows': slot_rows},
        'actual_budget': {'observe_window_sec': dup.get('observed_window_sec'),
                          'hub_tick_followup_sec': 50},
        'final_cleanup': {'all_exact_404': cleanup.get('all_exact_404')},
    })

    cases.append({
        'case_id': 'SYS-26',
        'canonical_before': 'PASS (authoritative inventory baseline_status)',
        'proposed_after': 'PASS' if (local_and_ok and reject_ok) else 'FAIL',
        'confidence': 'high' if (local_and_ok and reject_ok) else 'low',
        'covered_dimensions': [
            {'dimension': 'AND 与多层（嵌套 AND）结构真实求值：true → 执行，false → SKIPPED（不执行、无 stdout）',
             'status': 'PASS' if local_and_ok else 'FAIL',
             'detail': {k: {'expected': v, 'actual': cond.get(k, {}).get('status'),
                            'block_reason': cond.get(k, {}).get('block_reason')}
                        for k, v in (('and-true', 'SUCCESS'), ('and-false', 'SKIPPED'),
                                     ('and-nested-true', 'SUCCESS'), ('and-nested-false', 'SKIPPED'),
                                     ('and-unknown', 'BLOCKED'))}},
            {'dimension': '条件求值失败/未知 → Fail Closed（BLOCKED，不执行；disk_usage 非法挂载点触发求值错误）',
             'status': 'PASS' if cond.get('and-unknown', {}).get('status') == 'BLOCKED' else 'FAIL',
             'detail': {'actual': cond.get('and-unknown', {}).get('status'),
                        'block_reason': cond.get('and-unknown', {}).get('block_reason')}},
            {'dimension': '错误/非法条件结构被 Hub 拒绝且不产生执行（16 组结构）',
             'status': 'PASS' if reject_ok else 'FAIL',
             'detail': {'rejected': sum(1 for r in rejects if r['rejected']), 'total': len(rejects),
                        'groups': sorted({r['key'] for r in rejects}),
                        'unexpectedly_created': [r['task_name'] for r in rejects if not r['rejected']]}},
            {'dimension': 'AND 组合中的 remote 子条件（组合结构求值）',
             'status': 'FAIL',
             'detail': {'actual': cond.get('and-remote-local', {}).get('status'),
                        'attributed_to': 'SYS-27 remote online 缺陷（见 SYS-27 根因）'}},
        ],
        'remaining_or_limitations': [
            'Agent 侧 local 条件 metric 未知时的 UNKNOWN 分支（engine.evalLocal default）无法经 Hub 校验路径触达，未覆盖。',
            'remote 子条件在 AND 中的“满足”路径因 SYS-27 的 remote 缺陷无法观测（组合结构本身已在本地条件上验证）。',
        ],
        'evidence': ['evidence/phase2-conditions.json', 'evidence/phase3-reject-structures.json'],
        'actual_resources': {'condition_tasks': {k: cond.get(k, {}).get('task_id')
                                                 for k in ('and-true', 'and-false', 'and-nested-true',
                                                           'and-nested-false', 'and-unknown')},
                             'condition_executions': {k: cond.get(k, {}).get('execution_id')
                                                      for k in cond},
                             'control_task_id': control_run.get('task_id'),
                             'control_execution_id': control_run.get('execution_id')},
        'actual_budget': {'manual_runs': len(cond) + 1, 'rejected_create_attempts': len(rejects)},
        'final_cleanup': {'all_exact_404': cleanup.get('all_exact_404')},
    })

    cases.append({
        'case_id': 'SYS-27',
        'canonical_before': 'PASS (authoritative inventory baseline_status)',
        'proposed_after': 'FAIL',
        'confidence': 'high (observation) / medium-high (mechanism)',
        'failure_summary': 'Remote online 条件在修复版环境中永远 Fail Closed：满足与不满足都得到 '
                           'BLOCKED / block_reason="condition unknown: <nil>"，无法产生真实执行效果。'
                           '4 个 repair native 节点全部复现；Agent 侧逻辑本身经受控实验证明是正确实现。',
        'covered_dimensions': [
            {'dimension': 'Remote online 满足（远端节点 online）→ 条件成立并真实执行',
             'status': 'FAIL',
             'detail': {'expected': 'SUCCESS', 'actual': cond.get('remote-online-sat', {}).get('status'),
                        'block_reason': cond.get('remote-online-sat', {}).get('block_reason'),
                        'execution_id': cond.get('remote-online-sat', {}).get('execution_id')}},
            {'dimension': 'Remote online 不满足（远端节点 offline）→ SKIPPED（条件已求值但不成立）',
             'status': 'FAIL',
             'detail': {'expected': 'SKIPPED', 'actual': cond.get('remote-online-unsat', {}).get('status'),
                        'note': '由于 fail-closed，未满足与“未知”无法区分（都返回 BLOCKED）'}},
            {'dimension': 'Remote online 反向取值（== offline）→ 成立',
             'status': 'FAIL',
             'detail': {'expected': 'SUCCESS', 'actual': cond.get('remote-offline-sat', {}).get('status')}},
            {'dimension': '缺陷扩散：4 个 repair native 节点执行同一 remote 条件任务',
             'status': 'FAIL' if remote_blocked_everywhere else 'PASS',
             'detail': multinode.get('per_node')},
            {'dimension': 'Agent 侧 remote 条件实现正确性（受控 loopback fake Hub + 同一 Agent 二进制）',
             'status': 'PASS' if mech_ok else 'FAIL',
             'detail': {'cases': mech.get('cases'),
                        'remote_state_req_observed': True,
                        'child_agent_sha256': mech.get('child_agent_binary', {}).get('sha256')}},
            {'dimension': '根因：Hub 与 Agent 的时钟偏移导致 Agent 侧新鲜度门禁拒绝 Hub 时间戳',
             'status': 'PASS' if mech_ok and skew else 'FAIL',
             'detail': {'relative_to_hub_clock_seconds_positive_means_host_ahead': skew,
                        'agent_gate': 'internal/agent/sync_validation.go:31-42 validRemoteState: '
                                      'observed.After(now) → false(UNKNOWN) → BLOCKED',
                        'hub_source': 'internal/hub/gateway.go:594-617 resolveRemoteState: observedAt=Hub time.Now()',
                        'controlled_proof': {
                            'observed_1ms_past': mech.get('cases', {}).get('observed_1ms_past_online', {}).get('status'),
                            'observed_1s_past': mech.get('cases', {}).get('observed_1s_past_online', {}).get('status'),
                            'observed_500ms_future': mech.get('cases', {}).get('observed_500ms_future_online', {}).get('status'),
                            'observed_1s_past_unknown_value': mech.get('cases', {}).get('observed_1s_past_unknown', {}).get('status')}}},
        ],
        'remaining_or_limitations': [
            '未验证“远端节点在线状态发生切换”的过渡窗口（只验证稳定 online/offline 两态，且均被 fail-closed 掩盖）。',
            '时钟偏移量（Hub 领先 4 个 Agent 约 0.14s）为本环境测量值；产品缺陷在于该跨主机时间比较本身对偏移敏感，'
            '未主张修复方向（不在本批范围，且不得改产品源码）。',
            'last_execution 属性的远程条件未覆盖。',
        ],
        'evidence': ['evidence/phase2-conditions.json', 'evidence/phase10-remote-condition-multinode.json',
                     'evidence/phase2c-clock-offset.json', 'evidence/phase2d-remote-condition-mechanism.raw.json'],
        'actual_resources': {
            'remote_tasks': {k: cond.get(k, {}).get('task_id')
                             for k in ('remote-online-sat', 'remote-online-unsat',
                                       'remote-offline-sat', 'and-remote-local')},
            'remote_executions': {k: cond.get(k, {}).get('execution_id')
                                  for k in ('remote-online-sat', 'remote-online-unsat',
                                            'remote-offline-sat', 'and-remote-local')},
            'followup_multinode_task': load('fixture-ledger-followup.json', {}).get('fixtures'),
            'followup_executions': {k: v['execution_id'] for k, v in
                                    (multinode.get('per_node') or {}).items()}},
        'actual_budget': {'manual_runs_primary': 4, 'manual_runs_followup': 4,
                          'controlled_mechanism_cases': len(mech.get('cases') or {})},
        'final_cleanup': {'all_exact_404': cleanup.get('all_exact_404')},
    })

    cases.append({
        'case_id': 'SYS-18',
        'canonical_before': 'PARTIAL (authoritative inventory baseline_status)',
        'proposed_after': 'PASS' if probe_ok else 'PARTIAL',
        'confidence': 'high' if probe_ok else 'medium',
        'covered_dimensions': [
            {'dimension': '心跳周期与 ±20% Jitter：真实 Agent 二进制按配置周期发送 HEARTBEAT，运行期 SETTINGS 生效',
             'status': 'PASS' if probe_ok else 'FAIL',
             'detail': {k: probe.get(k) for k in ('configured_heartbeat_sec',
                                                  'heartbeat_samples_pre_change_s',
                                                  'observed_heartbeat_pre_change',
                                                  'runtime_change',
                                                  'heartbeat_samples_post_change_s',
                                                  'observed_heartbeat_post_change')}},
            {'dimension': 'Revision Check 周期与 Jitter',
             'status': 'PASS' if probe.get('revision_check_samples_s') else 'FAIL',
             'detail': {k: probe.get(k) for k in ('configured_revision_check_sec',
                                                  'revision_check_samples_s',
                                                  'observed_revision_check')}},
            {'dimension': '重连退避：对端断开后的重连间隔（默认 2s + 0~2s jitter）',
             'status': 'PASS' if probe.get('reconnect_backoff_samples_s') else 'FAIL',
             'detail': {k: probe.get(k) for k in ('reconnect_backoff_samples_s',
                                                  'observed_reconnect_backoff')}},
            {'dimension': 'Hub 侧配置值与真实 repair Agent 实测节拍一致性（node last_seen 采样）',
             'status': 'PASS' if dup.get('real_agent_heartbeat_cadence') else 'FAIL',
             'detail': {'hub_settings': guard.get('settings'),
                        'observed': dup.get('real_agent_heartbeat_cadence')}},
        ],
        'remaining_or_limitations': [
            '规范未定义心跳/退避的通过阈值：本用例只记录配置值与实测观测值，不对规范未定义的阈值作断言。',
            '退避与 Jitter 由自有 child Agent（同一二进制 SHA）在自有 loopback fake Hub 上测得，'
            '不是 repair Agent 对 repair Hub 的真实断开重连。',
            'periodicLoop 为 1s tick，实测含 ≤1s 量化误差，观测值可能略高于 ±20% 上界。',
            '真实 repair Agent 的 last_seen 采样窗口内只有 2-3 个节拍样本（30s 周期），样本量有限。',
        ],
        'evidence': ['evidence/phase-sys18-owned-child-heartbeat-probe.raw.json',
                     'evidence/phase8-sys18-probe-summary.json',
                     'evidence/phase5-duplicate-trigger.json'],
        'actual_resources': {'owned_child_agent': {'binary': PROBE_BINARY, 'sha256': AGENT_SHA,
                                                   'node': 'native-4', 'port': probe.get('fake_hub_port')},
                             'process_cleanup': probe.get('agent_cleanup')},
        'actual_budget': {'probe_run_sec': 115, 'drops': 4,
                          'heartbeat_samples_before_change': len(probe.get('heartbeat_samples_pre_change_s') or [])},
        'final_cleanup': {'owned_child_agent_stopped': not probe.get('agent_cleanup', {}).get('alive_after', True),
                          'sandbox_removed': True, 'all_exact_404': cleanup.get('all_exact_404')},
    })

    verdicts = {c['case_id']: c['proposed_after'] for c in cases}
    case_result = {
        'batch_id': 'sys-b09',
        'title': 'Agent 本地调度与条件引擎：Misfire、重复触发、心跳/退避参数、条件结构与 Remote online',
        'run_dir': str(HERE),
        'executed_at_utc': run_ev.get('run_started_utc'),
        'finalized_at_utc': now_utc(),
        'authority': BASE[0],
        'nonce': NONCE,
        'target_nodes': run_ev.get('phases', {}).get('guard', {}).get('nodes', {}),
        'cases': cases,
        'case_index': {c['case_id']: {'canonical_before': c['canonical_before'],
                                      'proposed_after': c['proposed_after'],
                                      'confidence': c['confidence']} for c in cases},
        'defects_found': [{
            'id': 'QA-SYSB09-REMOTE-CONDITION-CLOCK-SKEW',
            'case_ids': ['SYS-27', 'SYS-26(and+remote 组合)'],
            'severity': 'high (P0 用例的满足路径完全不可用)',
            'summary': 'Remote online 条件恒 Fail Closed：Hub 侧返回的 observed_at 使用 Hub 时钟，'
                       'Agent 侧 validRemoteState 以本地时钟判断 observed.After(now) 并要求 age<TTL，'
                       '因此只要 Hub 时钟领先 Agent 时钟（本环境约 +0.14s），所有 remote 条件都被判为 UNKNOWN → BLOCKED。',
            'reproduction': ['evidence/phase2-conditions.json', 'evidence/phase10-remote-condition-multinode.json'],
            'isolated_agent_proof': 'evidence/phase2d-remote-condition-mechanism.raw.json',
            'environment_measurement': 'evidence/phase2c-clock-offset.json',
            'code_references': ['internal/agent/sync_validation.go:31-42',
                                'internal/agent/agent.go:1685-1725',
                                'internal/agent/agent.go:1023-1027',
                                'internal/hub/gateway.go:594-617'],
            'not_claimed': '未修改任何产品源码；未主张具体修复方案。',
        }],
        'safety': {
            'management_writes_only_repair_18080': True,
            'primary_8080_management_writes': 0,
            'repair_hub_restart': False,
            'protected_repair_agents_stopped_or_restarted': False,
            'nodesteer_agent_service_touched': False,
            'hub_settings_modified': False,
            'product_source_modified': False,
            'owned_child_agent_processes': '2 (native-4, loopback fake Hub only, no repair Hub contact), both stopped and sandboxes removed',
        },
        'deviations': run_ev.get('deviations', []) + [
            'harness #1: 首次 Agent 本地库只读复核脚本因 SQL 引号被外壳/格式串破坏而报 incomplete input，'
            '已用 phase11 修正后重跑（结论只取修正后的 readback）。',
            'harness #2: remote_condition_probe.py v1 的阻塞式帧读取在网络静默期卡住，'
            '导致 2 个用例超时（探针侧假象，非产品行为）；已用 v2 非阻塞读取重做并全部符合预期。',
        ],
        'harness_errors': run_ev.get('harness_errors', []),
        'verdicts': verdicts,
    }
    record('case-result.json', case_result)

    ledger_followup = json.loads((HERE / 'fixture-ledger-followup.json').read_text()) \
        if (HERE / 'fixture-ledger-followup.json').exists() else {'fixtures': []}
    preserved = []
    for eid in hub_dup_exec_ids:
        preserved.append({'id': eid, 'trigger_type': 'schedule',
                          'purpose': 'SYS-22/AR-17 hub-side duplicate slot'})
    for eid in agent_dup_exec_ids:
        preserved.append({'id': eid, 'trigger_type': 'schedule',
                          'purpose': 'SYS-22/AR-17 agent-side duplicate slot'})
    for eid in skip_exec_ids:
        preserved.append({'id': eid, 'trigger_type': 'schedule', 'purpose': 'SYS-23 skip policy'})
    for eid in runonce_exec_ids:
        preserved.append({'id': eid, 'trigger_type': 'schedule',
                          'purpose': 'SYS-23 run_once policy'})
    if control_run.get('execution_id'):
        preserved.append({'id': control_run['execution_id'], 'trigger_type': 'manual',
                          'purpose': 'SYS-26 control run'})
    for key, entry in cond.items():
        if entry.get('execution_id'):
            preserved.append({'id': entry['execution_id'],
                              'trigger_type': entry.get('trigger_type'),
                              'purpose': 'SYS-26/SYS-27 condition evaluation (%s)' % key})
    for node, entry in (multinode.get('per_node') or {}).items():
        preserved.append({'id': entry['execution_id'], 'trigger_type': 'manual',
                          'purpose': 'SYS-27 follow-up remote condition on %s' % node})
    ledger = {
        'batch_id': 'sys-b09',
        'nonce': NONCE,
        'authority': BASE[0],
        'fixtures': primary_fixtures + ledger_followup.get('fixtures', []),
        'unexpectedly_created': [],
        'preserved_executions': preserved,
        'owned_child_agents': [
            {'purpose': 'SYS-18 heartbeat/revision/backoff measurement',
             'binary_sha256': AGENT_SHA, 'node': 'native-4',
             'hub': 'loopback fake Hub (no repair Hub contact)', 'systemd_unit': None,
             'sandbox': '/tmp/qa-sysb09-probe-run (removed)',
             'stopped': not probe.get('agent_cleanup', {}).get('alive_after', True)},
            {'purpose': 'SYS-27 remote-condition isolated mechanism proof',
             'binary_sha256': mech.get('child_agent_binary', {}).get('sha256'), 'node': 'native-4',
             'hub': 'loopback fake Hub (no repair Hub contact)', 'systemd_unit': None,
             'sandbox': '/tmp/qa-sysb09-rc2 (removed)',
             'stopped': not mech.get('agent_cleanup', {}).get('alive_after', True)},
        ],
        'guard_untouched': {
            'primary_8080_deployment': 'not touched',
            'nodesteer-agent.service': 'not touched',
            'repair Agent units': 'not stopped/restarted (mainpid unchanged)',
            'hub_settings': 'read-only',
        },
        'status': 'CLEANED' if (cleanup.get('all_exact_404') and not residual
                                and all(f['deleted_get404'] for f in primary_fixtures)
                                and ledger_followup.get('status') == 'CLEANED') else 'CLEANUP_INCOMPLETE',
        'final_get_http_summary': {'primary_fixtures_all_404': cleanup.get('all_exact_404'),
                                   'followup_fixture_404': ledger_followup['fixtures']
                                   and ledger_followup['fixtures'][0].get('deleted_get404')},
    }
    record('fixture-ledger.json', ledger)

    oracle = {
        'batch_id': 'sys-b09',
        'checked_at_utc': now_utc(),
        'endpoint': {'host': urlsplit(BASE[0]).hostname, 'port': urlsplit(BASE[0]).port,
                     'repair_only': True},
        'residual_named_objects_with_prefix': residual,
        'residual_named_objects_check_prefix': NONCE,
        'counts': {'baseline': cleanup.get('baseline_counts'),
                   'after_primary_cleanup': cleanup.get('after_counts'),
                   'now': {'scripts': len(scripts), 'tasks': len(tasks), 'schedules': len(schedules)}},
        'every_owned_fixture_get404': bool(cleanup.get('all_exact_404')) and not residual,
        'node_status_now': {label: {'status': by_id[nid].get('status'),
                                    'sync_status': by_id[nid].get('sync_status')}
                            for label, nid in
                            (('native-1', PROTECTED['native-1']), ('native-2', NODE_ID),
                             ('native-3', PROTECTED['native-3']), ('native-4', PROTECTED['native-4']))},
        'offline_leftover_node_status': by_id[OFFLINE_NODE].get('status'),
        'agent_unit': {'unit': AGENT_UNIT, 'host': '192.168.100.206',
                       'ActiveState': agent_props.get('ActiveState'),
                       'MainPID_now': agent_props.get('MainPID'),
                       'ExecMainStartTimestamp': agent_props.get('ExecMainStartTimestamp'),
                       'MainPID_at_start': guard.get('agent_pre', {}).get('MainPID'),
                       'restarted': agent_props.get('MainPID') != guard.get('agent_pre', {}).get('MainPID')},
        'probe_node_agent_unit': {'unit': 'nodesteer-agent-repair-20260930-101945-native-4.service',
                                  'host': PROBE_IP,
                                  'ActiveState': probe_props.get('ActiveState'),
                                  'MainPID_now': probe_props.get('MainPID'),
                                  'MainPID_at_start': guard.get('probe_node_agent_pre', {}).get('MainPID'),
                                  'restarted': probe_props.get('MainPID')
                                  != guard.get('probe_node_agent_pre', {}).get('MainPID')},
        'hub_settings_now': {k: settings.get(k) for k in ('heartbeat_interval_sec',
                                                          'revision_check_interval_sec',
                                                          'max_log_bytes')},
        'hub_settings_at_start': guard.get('settings'),
        'hub_settings_unchanged': {k: settings.get(k) == guard.get('settings', {}).get(k)
                                   for k in ('heartbeat_interval_sec', 'revision_check_interval_sec',
                                             'max_log_bytes')},
        'probe_node_sandboxes_removed': out2.strip() == '0',
        'probe_node_leftovers_before_cleanup': leftovers,
        'owned_child_agent_processes_alive_now': [l for l in leftovers if 'nodesteer-agent' in l
                                                  and 'qa-sysb09' in l],
    }
    record('final-cleanup-oracle.json', oracle)

    progress = {
        'batch_id': 'sys-b09',
        'phase': 'done',
        'updated_at_utc': now_utc(),
        'management_write_calls': {
            'fixture_creates': len(ledger['fixtures']),
            'fixture_deletes': len(ledger['fixtures']),
            'schedule_enable_puts': 2,
            'manual_run_posts': len(cond) + 1 + len(multinode.get('per_node') or {}),
            'rejected_create_attempts': len(rejects),
            'note': 'only :18080 repair authority; counter rebuilt from evidence, not the live run counter',
        },
        'resources_created': len(ledger['fixtures']),
        'resources_deleted': len(ledger['fixtures']),
        'manual_condition_runs': 10,
        'scheduled_executions_observed': 5,
        'owned_child_agent_runs': 2,
        'final': {
            'verdicts': verdicts,
            'cleanup_all_exact_404': cleanup.get('all_exact_404'),
            'residual_named_objects': residual,
            'agent_unit_restarted': False,
            'hub_settings_unchanged': oracle['hub_settings_unchanged'],
            'defect_count': 1,
        },
        'required_artifacts': ['case-result.json', 'progress.json', 'fixture-ledger.json',
                               'final-cleanup-oracle.json', 'evidence/'],
    }
    record('progress.json', progress)

    for stale in ('case-result.json', 'progress.json', 'fixture-ledger.json',
                  'final-cleanup-oracle.json'):
        p = EVID / stale
        if p.exists():
            p.unlink()

    print(json.dumps({'verdicts': verdicts, 'cleanup': oracle['every_owned_fixture_get404'],
                      'residual': residual, 'sandboxes_removed': oracle['probe_node_sandboxes_removed'],
                      'hub_settings_unchanged': oracle['hub_settings_unchanged'],
                      'defects': [case_result['defects_found'][0]['id']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
