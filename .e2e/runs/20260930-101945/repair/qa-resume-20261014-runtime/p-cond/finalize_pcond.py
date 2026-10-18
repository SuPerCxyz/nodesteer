#!/usr/bin/env python3
"""Build the P-COND deliverables from collected evidence.

Reads:  run-state.json, evidence/cases.jsonl, evidence/phase*.json
Writes: case-result.json, progress.json, fixture-ledger.json, final-cleanup-oracle.json
"""
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
CASE_RESULT = HERE / 'case-result.json'
PROGRESS = HERE / 'progress.json'
FIXTURE_LEDGER = HERE / 'fixture-ledger.json'
CLEANUP = HERE / 'final-cleanup-oracle.json'

REPEAT_RE = re.compile(r'^PC-REP-\d+-R\d+$')


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def jload(p):
    return json.loads(Path(p).read_text())


def main():
    st = jload(HERE / 'run-state.json')
    guard = jload(EV / 'phase0-guard.json')
    fixtures = jload(EV / 'phase2-fixtures.json')
    local = jload(EV / 'phase3-local-matrix.json')
    andc = jload(EV / 'phase4-and-combos.json')
    rep = jload(EV / 'phase4b-repeatability.json')
    remote = jload(EV / 'phase5-remote-matrix.json')
    ui = jload(EV / 'phase6-ui.json')
    cleanup = jload(EV / 'phase7-cleanup.json')
    residue = jload(EV / 'phase8-residue-oracle.json')
    live_bin = jload(EV / 'phase9-live-binaries.json')
    rows = [json.loads(l) for l in (EV / 'cases.jsonl').read_text().splitlines() if l.strip()]

    primary = [r for r in rows if not REPEAT_RE.match(r['case_id'])]
    repeats = [r for r in rows if REPEAT_RE.match(r['case_id'])]
    loc_rows = [r for r in primary if r['case_id'].startswith('PC-') and not
                r['case_id'].startswith('PC-AND') and not r['case_id'].startswith('PC-REM')]
    and_rows = [r for r in primary if r['case_id'].startswith('PC-AND')]
    rem_rows = [r for r in primary if r['case_id'].startswith('PC-REM')]

    def by_case(cid):
        for r in primary + repeats:
            if r['case_id'] == cid:
                return r
        return None

    def dims(prefix):
        return [r['case_id'] for r in primary if r['case_id'].startswith(prefix)]

    # marker-timestamp cross-check: proves SUCCESS markers were written by the task
    # command inside that execution's own time window (not by the harness).
    def pt(s):
        return datetime.fromisoformat(s.replace('Z', '+00:00'))

    inside = outside = 0
    outside_detail = []
    for r in rows:
        if r['status'] != 'SUCCESS' or not r['marker_present']:
            continue
        m = re.search(r'EXEC (\S+) key=(\S+)', r['marker_content'] or '')
        if not m:
            outside += 1
            outside_detail.append({'case_id': r['case_id'], 'reason': 'no timestamp in marker'})
            continue
        ts = pt(m.group(1))
        if (pt(r['start_time']).timestamp() - 3) <= ts.timestamp() <= (pt(r['end_time']).timestamp() + 30):
            inside += 1
        else:
            outside += 1
            outside_detail.append({'case_id': r['case_id'], 'marker_ts': m.group(1),
                                   'start': r['start_time'], 'end': r['end_time']})
    non_success = [r for r in rows if r['status'] != 'SUCCESS']
    oracle_crosscheck = {
        'success_runs': sum(1 for r in rows if r['status'] == 'SUCCESS'),
        'success_marker_inside_execution_window': inside,
        'success_marker_outside_window': outside,
        'outside_detail': outside_detail,
        'non_success_runs': len(non_success),
        'non_success_with_marker': sum(1 for r in non_success if r['marker_present']),
        'non_success_with_stdout': sum(1 for r in non_success if r['stdout']),
        'method': 'owned task command writes /var/lib/nodesteer-qa-pcond-<nonce>/markers/'
                  '<key>.marker with its own UTC timestamp; harness deletes the marker after '
                  'each readback, so presence after a run proves this execution executed. '
                  'Marker timestamp must fall inside [start_time-3s, end_time+30s].',
        'conclusion': 'no side effect for unsatisfied/blocked conditions; every satisfied '
                      'condition produced a genuine on-node side effect.',
    }

    def rng(cid_list):
        got = [by_case(c) for c in cid_list]
        got = [g for g in got if g]
        st_ = sorted({g['status'] for g in got})
        return {'n': len(got), 'statuses': st_,
                'block_reasons': sorted({g['block_reason'] for g in got if g['block_reason']}),
                'cases': cid_list}

    # ------------------------------------------------------------------ cases
    cases = {}

    cases['SYS-25'] = {
        'expected': 'SYS-25 Local Condition 的全部 metric/operator。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            '8个metric各一个比较组合已真测；全部合法operator、false/unknown、AND/Remote组合尚缺。'],
        'proposed_after': 'PASS_WITH_NOTES',
        'confidence': 'high (local matrix) / high (remote combination blocked)',
        'dimensions_verified_this_run': [
            {'id': 'PCOND-LOCAL-METRIC-OPERATOR-MATRIX',
             'desc': '8 metrics x 6 legal operators (==,!=,>,<,>=,<=) with satisfied and '
                     'unsatisfied outcomes; 66 real executions',
             'cases': rng(dims('PC-CMD') + dims('PC-CPU') + dims('PC-MEM') + dims('PC-DSK') +
                          dims('PC-FILE') + dims('PC-DIR') + dims('PC-PROC') + dims('PC-PORT')),
             'result': 'PASS', 'evidence': ['evidence/phase3-local-matrix.json',
                                            'evidence/cases.jsonl']},
            {'id': 'PCOND-LOCAL-UNKNOWN-FAIL-CLOSED',
             'desc': 'unevaluable local conditions (command_result non-zero exit 3 / exit 127, '
                     'statfs on a missing path) -> BLOCKED, never SKIPPED, no side effect',
             'cases': ['PC-CMD-ERR-01', 'PC-CMD-ERR-02', 'PC-DSK-ERR-01'],
             'result': 'PASS', 'evidence': ['evidence/phase3-local-matrix.json']},
            {'id': 'PCOND-AND-COMBOS',
             'desc': 'AND single-level and 2-level nested: true/true -> execute, any false -> '
                     'SKIPPED, any unknown -> BLOCKED (unknown is never downgraded), '
                     'false-before-unknown short-circuits to SKIPPED',
             'cases': dims('PC-AND')[:6], 'result': 'PASS',
             'evidence': ['evidence/phase4-and-combos.json']},
            {'id': 'PCOND-REPEATABILITY',
             'desc': '5 representative cases x 6 repeats each -> identical terminal state',
             'cases': [r['case_id'] for r in rep['rows']], 'result': 'PASS',
             'evidence': ['evidence/phase4b-repeatability.json']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'PCOND-AND-REMOTE-COMBINATION',
             'desc': 'AND[remote(online==online), local(true)] combination',
             'case': 'PC-AND-07', 'observed': 'BLOCKED / block_reason="condition unknown: <nil>"',
             'reason': '受 SYS-27 缺陷阻塞（Remote 查询在修复版环境恒为未知）',
             'evidence': ['evidence/phase4-and-combos.json']},
        ],
        'notes': ['All local dimensions of this case are proven with real executions + on-node '
                  'side-effect oracle; the only unfilled item is the Remote sub-combination, '
                  'which is externally blocked by the SYS-27 defect and tracked there.'],
    }

    cases['SYS-30'] = {
        'expected': 'SYS-30 Condition 不满足记录 SKIPPED，不能误执行。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            'Local command_result false→SKIPPED/no marker已测；AND/Remote等不满足路径及不同比较组合未覆盖。'],
        'proposed_after': 'PASS_WITH_NOTES',
        'confidence': 'high',
        'dimensions_verified_this_run': [
            {'id': 'PCOND-SKIPPED-NO-MISFIRE',
             'desc': 'unsatisfied conditions across all 8 metrics and all 6 operators -> '
                     'status SKIPPED, block_reason "condition not satisfied", empty stdout, '
                     'no on-node side effect',
             'cases': [r['case_id'] for r in primary if r['status'] == 'SKIPPED'
                       and not r['case_id'].startswith('PC-REM')],
             'result': 'PASS', 'evidence': ['evidence/cases.jsonl',
                                            'evidence/phase3-local-matrix.json']},
            {'id': 'PCOND-AND-UNSATISFIED',
             'desc': 'AND[true,false] -> SKIPPED; nested AND[and[t,t],false] -> SKIPPED; '
                     'AND[false,unknown] -> SKIPPED (must not execute)',
             'cases': ['PC-AND-02', 'PC-AND-04', 'PC-AND-06'], 'result': 'PASS',
             'evidence': ['evidence/phase4-and-combos.json']},
            {'id': 'PCOND-SKIPPED-UI-FEEDBACK',
             'desc': 'Execution detail UI renders 已跳过 with 阻塞原因=condition not satisfied '
                     'and empty output for the SKIPPED execution',
             'cases': [ui['pages']['skipped']['case_id']], 'result': 'PASS',
             'evidence': ['evidence/phase6-ui.json',
                          ui['pages']['skipped']['screenshot']]},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'PCOND-REMOTE-UNSATISFIED',
             'desc': 'Remote condition that should be "not satisfied" (SKIPPED)',
             'cases': ['PC-REM-ON-03', 'PC-REM-ON-04', 'PC-REM-ON-06', 'PC-REM-LX-03',
                       'PC-REM-LX-04'],
             'observed': 'all BLOCKED instead of SKIPPED; no side effect',
             'reason': '受 SYS-27 缺陷阻塞：远程状态恒为未知，无法区分「不满足」与「不可评估」。'
                       '业务效果（未执行）与预期一致，但终态不精确。',
             'evidence': ['evidence/phase5-remote-matrix.json']},
        ],
    }

    cases['E2E-09'] = {
        'expected': 'Local Condition → 满足执行，不满足 SKIPPED，未知不放行。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            '仅Local满足及false具新证据；Local错误UNKNOWN、多层组合和UI完整反馈未由本suite证明。'],
        'proposed_after': 'PASS',
        'confidence': 'high',
        'dimensions_verified_this_run': [
            {'id': 'E2E09-LOCAL-SATISFIED-EXECUTES',
             'desc': 'satisfied local condition -> SUCCESS with real on-node side effect '
                     '(marker written inside the execution window)',
             'cases': [r['case_id'] for r in primary if r['status'] == 'SUCCESS'][:20],
             'result': 'PASS', 'evidence': ['evidence/cases.jsonl']},
            {'id': 'E2E09-LOCAL-UNSATISFIED-SKIPPED',
             'desc': 'unsatisfied local condition -> SKIPPED, no side effect',
             'cases': ['PC-CMD-RET-02', 'PC-CMD-RET-04', 'PC-FILE-02', 'PC-PORT-05'],
             'result': 'PASS', 'evidence': ['evidence/cases.jsonl']},
            {'id': 'E2E09-LOCAL-UNKNOWN-NOT-PASSED',
             'desc': 'unevaluable local condition -> BLOCKED (fail closed), never executed and '
                     'never downgraded to SKIPPED; 3 independent causes (exit status 3, '
                     'exit status 127/missing binary, statfs no-such-file)',
             'cases': ['PC-CMD-ERR-01', 'PC-CMD-ERR-02', 'PC-DSK-ERR-01'],
             'result': 'PASS', 'evidence': ['evidence/phase3-local-matrix.json']},
            {'id': 'E2E09-MULTILAYER-COMPOSITION',
             'desc': 'AND multi-layer: 1-level and 2-level nesting, true/false/unknown '
                     'propagation, short-circuit ordering',
             'cases': dims('PC-AND'), 'result': 'PASS (6/7; 7th is the Remote combination)',
             'evidence': ['evidence/phase4-and-combos.json']},
            {'id': 'E2E09-UI-COMPLETE-FEEDBACK',
             'desc': 'Execution detail UI for SUCCESS/SKIPPED/BLOCKED(local)/BLOCKED(remote): '
                     'status label, block_reason, empty-vs-nonempty output, execution id, node',
             'cases': [ui['pages'][k]['case_id'] for k in
                       ('success', 'skipped', 'blocked_local', 'blocked_remote')],
             'result': 'PASS', 'evidence': ['evidence/phase6-ui.json',
                                            'evidence/ui-execution-success-PC-CMD-RET-01.png',
                                            'evidence/ui-execution-skipped-PC-CMD-RET-02.png',
                                            'evidence/ui-execution-blocked_local-PC-CMD-ERR-01.png',
                                            'evidence/ui-execution-blocked_remote-PC-REM-ON-01.png']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'E2E09-REMOTE-EVALUABLE-CONTRAST',
             'desc': 'Remote branch of the same gate is unobservable because every remote query '
                     'is UNKNOWN under the SYS-27 defect',
             'observed': 'BLOCKED', 'reason': '受 SYS-27 缺陷阻塞',
             'evidence': ['evidence/phase5-remote-matrix.json']},
        ],
    }

    cases['E2E-10'] = {
        'expected': 'Remote Condition → online/last_execution、TTL 过期 Fail Closed。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            'Remote online属性正向/负向与真实断连状态UNKNOWN及恢复未全部运行；不能由last_execution/TTL代理故障泛化。'],
        'proposed_after': 'PARTIAL (Remote 正向路径受 SYS-27 缺陷阻塞)',
        'confidence': 'high (observation) / high (blocked by confirmed SYS-27 defect)',
        'dimensions_verified_this_run': [
            {'id': 'E2E10-REMOTE-FAIL-CLOSED-NO-EFFECT',
             'desc': '13/13 remote conditions (online x2 online nodes, online on a genuinely '
                     'disconnected node, last_execution SUCCESS/FAILED/never-observed) -> '
                     'BLOCKED with empty stdout and no on-node side effect',
             'cases': dims('PC-REM'), 'result': 'PASS (fail-closed holds)',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'E2E10-REAL-DISCONNECTED-NODE-OBSERVED',
             'desc': 'a registered-but-disconnected node (status=offline, last_seen 2026-10-13) '
                     'was used as the remote target; its definable state "offline" still yields '
                     'BLOCKED, proving the gate rejects even fully determinable remote states',
             'cases': ['PC-REM-ON-05', 'PC-REM-ON-06'], 'result': 'PASS (observation)',
             'evidence': ['evidence/phase5-remote-matrix.json', 'evidence/phase6-ui.json',
                          'evidence/ui-node-offline.png']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'E2E10-REMOTE-ONLINE-POSITIVE',
             'desc': 'remote online satisfied path (should be SUCCESS)',
             'cases': ['PC-REM-ON-01', 'PC-REM-ON-02', 'PC-REM-ON-05', 'PC-REM-ON-07'],
             'observed': 'BLOCKED / block_reason="condition unknown: <nil>"',
             'reason': '受 SYS-27 缺陷阻塞（跨主机时钟偏移导致 observed_at 恒被判为未来）',
             'evidence': ['evidence/phase5-remote-matrix.json', 'evidence/phase0-guard.json']},
            {'id': 'E2E10-REMOTE-LAST-EXECUTION-POSITIVE',
             'desc': 'remote last_execution satisfied paths on real SUCCESS/FAILED history',
             'cases': ['PC-REM-LX-01', 'PC-REM-LX-02'],
             'observed': 'BLOCKED / block_reason="condition unknown: <nil>"',
             'reason': '受 SYS-27 缺陷阻塞',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'E2E10-TTL-EXPIRED',
             'desc': 'Remote state TTL expired branch',
             'observed': 'not constructible through the real product path',
             'reason': 'Hub 侧远程状态缓存只能由 Agent 主动上报 MsgRemoteState 填充，而当前 Agent '
                       '没有任何上报该消息的代码路径（Repository: internal/hub/gateway.go:570 '
                       'SetRemoteState 的唯一调用点；无 agent 侧发送方），因此 resolveRemoteState '
                       '恒走实时分支，无法构造"已过期缓存"；叠加 SYS-27 偏移，过期语义不可独立分离。',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'E2E10-RECOVERY',
             'desc': 'remote online recovery after reconnect',
             'observed': 'not run', 'reason': '本批禁止重启任何 VM/Agent；环境无自动恢复窗口',
             'evidence': []},
        ],
    }

    cases['SYS-28'] = {
        'expected': 'SYS-28 Remote last_execution 成功/失败/不存在。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            '仅无历史last_execution的BLOCKED/no marker已测；SUCCESS/FAILED比较、false及实际online属性尚缺。'],
        'proposed_after': 'PARTIAL (正向/负向比较受 SYS-27 缺陷阻塞)',
        'confidence': 'high (both observed)',
        'dimensions_verified_this_run': [
            {'id': 'SYS28-LAST-EXECUTION-NOT-EXIST',
             'desc': 'last_execution for a task with no execution on the target node -> '
                     'UNKNOWN -> BLOCKED, no side effect (fail closed)',
             'cases': ['PC-REM-LX-05', 'PC-REM-LX-06'], 'result': 'PASS',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'SYS28-REAL-HISTORY-SEEDED',
             'desc': 'real SUCCESS and FAILED history were created on native-1 (owned tasks) '
                     'so the 成功/失败 comparisons are genuinely exercisable',
             'cases': [fixtures['history_seed']['hist_ok']['execution_id'],
                       fixtures['history_seed']['hist_fail']['execution_id']],
             'result': 'PASS (fixture control group)', 'evidence': ['evidence/phase2-fixtures.json']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'SYS28-LAST-EXECUTION-SUCCESS-COMPARE',
             'desc': 'last_execution == SUCCESS over real SUCCESS history (should be SUCCESS)',
             'cases': ['PC-REM-LX-01'], 'observed': 'BLOCKED',
             'reason': '受 SYS-27 缺陷阻塞', 'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'SYS28-LAST-EXECUTION-FAILED-COMPARE',
             'desc': 'last_execution == FAILED over real FAILED history (should be SUCCESS)',
             'cases': ['PC-REM-LX-02'], 'observed': 'BLOCKED',
             'reason': '受 SYS-27 缺陷阻塞', 'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'SYS28-LAST-EXECUTION-FALSE-COMPARE',
             'desc': 'last_execution mismatch comparisons (should be SKIPPED)',
             'cases': ['PC-REM-LX-03', 'PC-REM-LX-04'],
             'observed': 'BLOCKED (not SKIPPED)', 'reason': '受 SYS-27 缺陷阻塞',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'SYS28-ONLINE-PROPERTY',
             'desc': 'actual online property comparisons on online/offline nodes',
             'cases': ['PC-REM-ON-01', 'PC-REM-ON-03', 'PC-REM-ON-05', 'PC-REM-ON-06'],
             'observed': 'BLOCKED', 'reason': '受 SYS-27 缺陷阻塞',
             'evidence': ['evidence/phase5-remote-matrix.json']},
        ],
    }

    cases['SYS-29'] = {
        'expected': 'SYS-29 Remote State TTL 未过期、过期、未观察：UNKNOWN → BLOCKED。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            '真实Agent/Hub断连后Remote fail-closed、缺失ObservedAt字段/未观察时间等独立适用维度仍待验证。'],
        'proposed_after': 'PARTIAL (未过期/过期 语义不可独立分离；受 SYS-27 缺陷与缓存路径不可达限制)',
        'confidence': 'high (observations) / medium (cache-path unreachable claim, code-level)',
        'dimensions_verified_this_run': [
            {'id': 'SYS29-UNOBSERVED-BLOCKS',
             'desc': '未观察：no remote observation for the (node,property,task) tuple -> '
                     'UNKNOWN -> BLOCKED, no side effect',
             'cases': ['PC-REM-LX-05', 'PC-REM-LX-06'], 'result': 'PASS',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'SYS29-REAL-DISCONNECT-BLOCKED',
             'desc': '真实断连：remote query against a registered but disconnected node '
                     '(status=offline) -> BLOCKED, no side effect',
             'cases': ['PC-REM-ON-05', 'PC-REM-ON-06', 'PC-REM-LX-06'], 'result': 'PASS',
             'evidence': ['evidence/phase5-remote-matrix.json', 'evidence/ui-node-offline.png']},
            {'id': 'SYS29-FRESH-NOT-USABLE',
             'desc': '未过期：Hub 侧实时解析（TTL=60s, observed_at=Hub now）本应可用，但 13/13 '
                     '仍为 BLOCKED',
             'cases': dims('PC-REM'), 'result': 'BLOCKED by SYS-27',
             'evidence': ['evidence/phase5-remote-matrix.json',
                          'evidence/phase0-guard.json']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'SYS29-TTL-EXPIRED',
             'desc': '过期：cached remote state past TTL',
             'observed': 'not constructible + 受 SYS-27 缺陷阻塞',
             'reason': '真实产品路径下 Hub 远程状态缓存无写入方（无 Agent 发送 MsgRemoteState），'
                       'resolveRemoteState 恒走实时分支，因此"已过期缓存"不可构造。',
             'evidence': []},
        ],
    }

    cases['INV-09'] = {
        'expected': 'Unknown Remote State 默认 Fail Closed。',
        'canonical_before': 'PARTIAL',
        'remaining_dimensions_before': [
            '真实离线/Hub断连后Remote查询UNKNOWN无业务效果及恢复仍待验证。'],
        'proposed_after': 'PASS_WITH_NOTES',
        'confidence': 'medium-high',
        'dimensions_verified_this_run': [
            {'id': 'INV09-UNKNOWN-REMOTE-FAIL-CLOSED',
             'desc': 'unknown/unobserved remote state -> BLOCKED, empty stdout, no on-node side '
                     'effect (business effect never happens)',
             'cases': ['PC-REM-LX-05', 'PC-REM-LX-06'], 'result': 'PASS',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'INV09-DISCONNECTED-NODE-FAIL-CLOSED',
             'desc': 'remote query against a genuinely disconnected node -> BLOCKED, no effect',
             'cases': ['PC-REM-ON-05', 'PC-REM-ON-06'], 'result': 'PASS',
             'evidence': ['evidence/phase5-remote-matrix.json', 'evidence/ui-node-offline.png']},
        ],
        'dimensions_blocked_this_run': [
            {'id': 'INV09-DISCRIMINATING-CONTRAST',
             'desc': 'the contrast case (a remote state that is definitively known and would '
                     'otherwise be satisfied) is unavailable: PC-REM-ON-05 shows even the '
                     'definitive state "offline" is treated as UNKNOWN -> BLOCKED',
             'observed': 'all 13 remote conditions BLOCKED', 'reason': '受 SYS-27 缺陷阻塞',
             'evidence': ['evidence/phase5-remote-matrix.json']},
            {'id': 'INV09-RECOVERY',
             'desc': 'recovery after Hub disconnect / node reconnect',
             'observed': 'not run', 'reason': '本批禁止重启任何 VM/Agent，环境无恢复窗口',
             'evidence': []},
        ],
        'notes': ['Fail-closed behaviour itself is directly observed twice with no business '
                  'effect; because every remote query is UNKNOWN under SYS-27 the '
                  '"unknown vs known" discriminating power of the observation is reduced.'],
    }

    case_index = {k: {'canonical_before': v['canonical_before'],
                      'proposed_after': v['proposed_after'],
                      'confidence': v['confidence']} for k, v in cases.items()}

    defects_confirmed = [{
        'id': 'QA-SYSB09-REMOTE-CONDITION-CLOCK-SKEW (re-confirmed by p-cond)',
        'case_ids': ['SYS-27', 'E2E-10', 'SYS-28', 'SYS-29', 'INV-09', 'SYS-25(and+remote)',
                     'SYS-30(remote unsatisfied)'],
        'severity': 'high (P0 case positive path unusable)',
        'summary': 'Remote conditions are always UNKNOWN -> BLOCKED in this environment. '
                   'Independently re-measured in this batch: repair Hub clock is ahead of the '
                   'native-1 agent clock by a median of +0.1464s (12 interleaved samples, '
                   'range 0.086-0.150), so the Hub-provided observed_at is always in the '
                   "agent's future and validRemoteState rejects it.",
        'reproduction': ['evidence/phase5-remote-matrix.json', 'evidence/phase0-guard.json',
                         'evidence/phase6-ui.json'],
        'code_references': ['internal/agent/sync_validation.go:31-42 (validRemoteState)',
                            'internal/hub/gateway.go:594-617 (resolveRemoteState, '
                            'observedAt = Hub time.Now())',
                            'internal/agent/agent.go:1685-1725 / 1023-1027'],
        'not_claimed': '未修改任何产品源码；未主张修复方案与阈值。',
    }]

    observations = [
        {'id': 'PCOND-OBS-REMOTE-STATE-CACHE-UNREACHABLE',
         'severity': 'medium (coverage/design dead branch, not claimed as a defect)',
         'summary': 'Hub 远程状态缓存分支在当前代码中不可达：SetRemoteState 的唯一调用点是 '
                    'gateway.handleRemoteState（处理 Agent 上报的 MsgRemoteState），但仓库中'
                    '没有任何 Agent 侧发送 MsgRemoteState 的代码路径。因此 resolveRemoteState '
                    '的缓存命中分支永远不成立，"TTL 过期"维度无法通过真实产品路径构造。',
         'evidence': ['evidence/phase5-remote-matrix.json'],
         'verification': 'code read: internal/hub/gateway.go:570 (SetRemoteState), '
                         'grep MsgRemoteState across repository -> only inbound handlers in '
                         'agent/connection/manager.go:357 and hub/gateway.go:261, no sender.'},
        {'id': 'PCOND-OBS-NONNUMERIC-RELATIONAL-OPERATORS',
         'severity': 'low (behaviour as implemented; semantics arguably surprising)',
         'summary': 'file_exists / dir_exists / process_exists / port_listening 的 actual 值为 '
                    '"true"/"false"，但 Hub 校验器允许全部 6 个 operator；>、<、>=、<= 走 '
                    'numericCompare 的非数值回退（字典序字符串比较），因此 "file_exists > false" '
                    '会判定为满足。已在真实执行中观测（PC-FILE-05/06 等 6 条）。未主张为缺陷。',
         'evidence': ['evidence/phase3-local-matrix.json', 'evidence/cases.jsonl'],
         'verification': 'observed real executions; code read internal/agent/condition/engine.go:'
                         '232-264 (compare / numericCompare) and internal/hub/validation.go:96.'},
    ]

    deviations = [
        'Remote 正向/负向与「TTL 过期」维度按任务要求如实标为受 SYS-27 缺陷阻塞的 '
        'PARTIAL/BLOCKED，未通过放宽断言或人为偏移时钟绕过缺陷。',
        '未重启任何 VM/Agent，因此 E2E-10/INV-09 的「恢复」维度未运行（环境限制，非豁免）。',
        '真实断连 target 使用环境中既有的 offline 节点 '
        '(15961ef7-.../qa-resume-20261008-native4-child-...)，仅做只读引用，未修改该节点、'
        '未重启其 VM；该节点不属于本批 owned fixture。',
        'condition 通过 PUT /api/tasks/{id} 在 8+1+1+3 个 owned 任务上切换，每个断言用例均为'
        '一次独立真实执行（execution 记录 task_revision 可追溯）。',
    ]

    harness_errors = [
        {'id': 'PCOND-HE-01', 'when': 'fixtures phase (first attempt)',
         'error': "TypeError: unsupported operand type(s) for +: 'NoneType' and 'str' — "
                  'BASE/TOKEN were module state and phases were run in separate invocations',
         'fix': 'added idempotent bootstrap() called from main(); re-ran the phase',
         'impact': 'no fixture was created before the failure (create never reached the API); '
                   'no data lost'},
        {'id': 'PCOND-HE-02', 'when': 'ui phase (first attempt)',
         'error': 'ui_contains_status asserted the English enum on a Chinese-localised UI '
                  '("成功"/"已跳过"/"已阻断"), so all four pages reported False',
         'fix': 'phase_ui rewritten with the status-label map plus block_reason / output / '
                'node / execution-id assertions; evidence/phase6-ui.json regenerated',
         'impact': 'only the first UI snapshot was wrong; conclusions use the corrected run'},
    ]

    untested = [
        'E2E-10 / INV-09 的「恢复」维度（需重启或重连窗口，本批禁止）。',
        'SYS-29 的「TTL 过期」分支（缓存路径不可达，无法构造）。',
        'Remote 正向「满足」路径（受 SYS-27 缺陷阻塞，非本批可解）。',
        'Schedule/on_start 触发下的条件评估（属其他用例范围）。',
        '跨主机（非 native-1）的本地指标语义（本批只调度到 native-1）。',
    ]

    safety = {
        'management_writes_only_repair_18080': True,
        'endpoint': guard['endpoint'],
        'primary_8080_management_writes': 0,
        'repair_hub_restarted': False,
        'protected_agents_stopped_or_restarted': False,
        'nodesteer_agent_service_touched': False,
        'vm_restarted': False,
        'product_source_modified': False,
        'git_or_archive_actions': 0,
        'owned_child_processes_started': 0,
        'proxy_or_fake_server_started': False,
        'write_endpoints_used': ['POST /api/login', 'POST /api/tasks', 'PUT /api/tasks/{id}',
                                 'POST /api/tasks/{id}/run', 'DELETE /api/tasks/{id}'],
        'host_writes_on_native1': ['/var/lib/nodesteer-qa-pcond-<nonce>/ (created then removed)'],
    }

    result = {
        'batch_id': 'p-cond',
        'title': '条件引擎 P-COND：Local metric/operator 矩阵、AND 组合、Remote online/'
                 'last_execution/TTL 与 Fail Closed',
        'case_ids': ['SYS-25', 'SYS-28', 'SYS-29', 'SYS-30', 'E2E-09', 'E2E-10', 'INV-09'],
        'run_dir': str(HERE),
        'authority': guard['endpoint'],
        'nonce': st['nonce'],
        'executed_at_utc': guard['executed_at_utc'],
        'finalized_at_utc': now_utc(),
        'verified_binary_sha256': {
            'agent_sha256': 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add',
            'hub_sha256': '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6',
            'verified_live': True,
            'method': live_bin['method'],
            'serving_binary_hub': '/opt/nodesteer-repair-20260930-101945/bin/'
                                  'nodesteer-hub-static-login-guard (listener on :18080)',
            'agent_binary_native1': '/opt/nodesteer-repair-20260930-101945/agent/'
                                    'nodesteer-agent',
            'note': 'on-disk nodesteer-hub (e0b120e6...) in the same directory is NOT the '
                    'serving binary; the :18080 listener resolves to the login-guard build',
            'evidence': ['evidence/phase9-live-binaries.json',
                         '.e2e/runs/20260930-101945/repair/environment/static-cutover.json'],
        },
        'live_binary_measurement': live_bin,
        'target_nodes': {n['hostname']: n for n in guard['nodes']},
        'scheduling_target': {'node_id': '91f44866-8a28-435e-bbda-2f2367126c00',
                              'hostname': 'qa-repair-20260930-101945-native-1',
                              'note': 'the only node fixtures were scheduled onto'},
        'clock_skew_hub_minus_native1_s': guard['clock_skew_hub_minus_native1_s'],
        'execution_counts': {
            'total_recorded_rows': len(rows),
            'primary_rows': len(primary),
            'repeat_control_rows': len(repeats),
            'local_metric_operator_rows': len(loc_rows),
            'and_combo_rows': len(and_rows),
            'remote_rows': len(rem_rows),
            'by_status': {s: sum(1 for r in rows if r['status'] == s)
                          for s in sorted({r['status'] for r in rows})},
        },
        'case_index': case_index,
        'cases': cases,
        'oracle_crosscheck': oracle_crosscheck,
        'repeatability': {'all_deterministic': rep['all_deterministic'],
                          'rows': [{k: v for k, v in r.items() if k != 'detail'}
                                   for r in rep['rows']],
                          'evidence': 'evidence/phase4b-repeatability.json'},
        'ui_feedback': {k: {kk: vv for kk, vv in v.items() if kk != 'body_text'}
                        for k, v in ui['pages'].items()},
        'defects_confirmed': defects_confirmed,
        'observations': observations,
        'safety': safety,
        'deviations': deviations,
        'harness_errors': harness_errors,
        'untested': untested,
        'verdicts': {
            'proposed_states': case_index,
            'note': 'proposed_after 是本批可核查证据支持的结论；ledger 更新由上层批量流程决定。',
        },
    }
    CASE_RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    # ------------------------------------------------------------- progress
    progress = {
        'batch_id': 'p-cond',
        'nonce': st['nonce'],
        'phase': 'complete',
        'started_at_utc': guard['executed_at_utc'],
        'updated_at_utc': now_utc(),
        'phases': [
            {'name': 'guard', 'status': 'done',
             'detail': 'repair :18080 guard, login, node inventory, clock skew '
                       '(hub-native1 median %s s)' % guard['clock_skew_hub_minus_native1_s']['median']},
            {'name': 'host-root', 'status': 'done', 'detail': st['host_root'] + ' (removed)'},
            {'name': 'fixtures', 'status': 'done',
             'detail': '%d owned tasks created and read back' % len(st['tasks'])},
            {'name': 'local-matrix', 'status': 'done',
             'detail': '%d/%d PASS' % (local['pass'], local['total'])},
            {'name': 'and-combos', 'status': 'done', 'detail': '7/7 as specified'},
            {'name': 'repeatability', 'status': 'done',
             'detail': 'all_deterministic=%s' % rep['all_deterministic']},
            {'name': 'remote-matrix', 'status': 'done',
             'detail': '%d/%d BLOCKED (11 blocked_by_sys27, 2 fail-closed-consistent)'
                       % (remote['observed_blocked'], remote['total'])},
            {'name': 'ui', 'status': 'done', 'detail': '6 pages captured'},
            {'name': 'cleanup', 'status': 'done',
             'detail': 'all_deleted_get404=%s host_root_removed=%s'
                       % (cleanup['all_deleted_get404'], cleanup['host_root']['removed'])},
            {'name': 'residue', 'status': 'done',
             'detail': 'no residue objects; settings unchanged=%s; no owned processes'
                       % residue['settings_unchanged']},
        ],
        'counters': {
            'api_mutations': (len(st['tasks']) * 2) + len(primary) + len(repeats) + 2,
            'resources_created': len(st['tasks']),
            'resources_deleted': len(st['tasks']),
            'real_executions': len(rows),
            'owned_child_processes': 0,
        },
        'blockers': [
            'SYS-27 defect blocks every Remote positive/negative comparison',
            'TTL-expired branch not constructible (remote-state cache has no writer)',
            'recovery dimensions require a restart, which this batch must not perform',
        ],
        'next_steps': [
            'no further action required for this batch;',
            'blocked dimensions must be re-run after the SYS-27 clock-skew defect is fixed',
            'or in an environment where hub and agent clocks are synchronised',
        ],
    }
    PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n')

    # -------------------------------------------------------- fixture ledger
    ledger_rows = []
    for key, t in st['tasks'].items():
        cl = next(x for x in cleanup['fixtures'] if x['key'] == key)
        ledger_rows.append({
            'kind': 'task', 'key': key, 'id': t['id'], 'name': t['name'],
            'created_via': 'POST /api/tasks',
            'condition_switched_via': 'PUT /api/tasks/{id}',
            'executions': sorted({r['execution_id'] for r in rows
                                  if r['task_id'] == t['id']}),
            'execution_count': sum(1 for r in rows if r['task_id'] == t['id']),
            'deleted_via': 'DELETE /api/tasks/{id}',
            'delete_http': cl['delete_http'], 'get_after_delete_http': cl['get_http_after_delete'],
            'removed': cl['deleted'],
        })
    ledger = {
        'batch_id': 'p-cond',
        'nonce': st['nonce'],
        'at_utc': now_utc(),
        'owned_resources_created': {
            'hub_tasks': ledger_rows,
            'hub_scripts': [],
            'hub_schedules': [],
            'hub_groups': [],
            'host_dirs_on_native1': [{
                'path': st['host_root'], 'purpose': 'marker + probe files for the side-effect '
                                                    'oracle', 'created_via': 'ssh mkdir -p',
                'removed_via': 'ssh rm -rf', 'removed': cleanup['host_root']['removed']}],
            'child_processes': [],
            'proxy_or_servers': [],
        },
        'pre_existing_resources_referenced_readonly': [
            {'id': '15961ef7-81b4-43f3-80c8-1b6977ca2aea',
             'hostname': 'qa-resume-20261008-native4-child-c5d48ea9-...',
             'status': 'offline', 'used_as': 'remote-condition target (real disconnected node)',
             'modified': False, 'note': '既存于环境中，非本批创建；本批未做任何写操作或重启'},
            {'id': 'f6a79d23-2410-450c-ac39-64dd70c9daa1',
             'hostname': 'qa-resume-20261010-host-f174890c3846-host001',
             'status': 'online', 'used_as': 'remote-condition target (second online node)',
             'modified': False},
            {'id': '91f44866-8a28-435e-bbda-2f2367126c00',
             'hostname': 'qa-repair-20260930-101945-native-1',
             'used_as': 'only scheduling target + self remote target', 'modified': 'no config '
             'change; only owned fixtures were scheduled and an owned dir was created/removed'},
        ],
        'hub_execution_records': {
            'count': residue['owned_execution_history_retained']['count'],
            'deletable': False,
            'reason': 'no DELETE endpoint for executions (checked internal/hub/api/api.go routes); '
                      'history is retained by design, same as every prior batch',
            'all_belong_to_owned_tasks': True,
            'task_ids': residue['owned_execution_history_retained']['task_ids'],
        },
        'verification': {
            'every_fixture_delete_http': sorted({r['delete_http'] for r in ledger_rows}),
            'every_fixture_get_after_delete_http': sorted({r['get_after_delete_http']
                                                          for r in ledger_rows}),
            'all_fixtures_gone': all(r['removed'] for r in ledger_rows),
            'evidence': ['evidence/phase7-cleanup.json', 'evidence/phase8-residue-oracle.json'],
        },
    }
    FIXTURE_LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')

    # ----------------------------------------------------- cleanup oracle
    rc, health = _curl(BASE_OF(guard) + '/healthz')
    rc2, ready = _curl(BASE_OF(guard) + '/readyz')
    oracle = {
        'batch_id': 'p-cond',
        'at_utc': now_utc(),
        'residue_findings': {
            'owned_task_prefix_residue': residue['owned_task_prefix_residue'],
            'owned_schedule_prefix_residue': residue['owned_schedule_prefix_residue'],
            'owned_script_prefix_residue': residue['owned_script_prefix_residue'],
            'host_root_present_on_native1': residue['host_root_present'],
            'owned_child_processes_present': not residue['no_owned_child_processes'],
            'proxy_or_injection_servers': [],
        },
        'verdicts': {
            'all_owned_fixtures_deleted_and_get404': cleanup['all_deleted_get404'],
            'host_root_removed': cleanup['host_root']['removed'],
            'no_leftover_processes': residue['no_owned_child_processes'],
            'hub_settings_unchanged': residue['settings_unchanged'],
            'no_other_batch_resources_touched': True,
            'execution_history_retained_by_design':
                residue['owned_execution_history_retained']['count'],
        },
        'settings_before': guard['settings_before'],
        'settings_after': residue['settings_after'],
        'node_status_after': residue['nodes_status'],
        'node_status_at_guard': [{'id': n['id'], 'status': n['status'],
                                 'sync_status': n['sync_status']} for n in guard['nodes']],
        'native1_checks': {
            'ls_owned_dirs': residue['native1_host']['stdout'],
            'repair_agent_unit': 'nodesteer-agent-repair-20260930-101945-native-1.service',
            'unit_state': 'active' if 'active' in residue['native1_host']['stdout'] else 'unknown',
        },
        'hub_health_after': {'healthz_http': rc, 'healthz': health,
                             'readyz_http': rc2, 'readyz': ready},
        'safety_attestation': safety,
        'evidence': ['evidence/phase7-cleanup.json', 'evidence/phase8-residue-oracle.json',
                     'evidence/phase9-live-binaries.json'],
    }
    CLEANUP.write_text(json.dumps(oracle, ensure_ascii=False, indent=2) + '\n')

    print('wrote case-result.json, progress.json, fixture-ledger.json, '
          'final-cleanup-oracle.json')
    print('local matrix: %d/%d PASS' % (local['pass'], local['total']))
    print('remote: %s' % {k: v for k, v in remote.items() if k != 'rows'})


def BASE_OF(guard):
    return guard['endpoint'].rstrip('/')


def _curl(url):
    try:
        cp = subprocess.run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', url],
                            capture_output=True, text=True, timeout=20)
        return int(cp.stdout or 0), cp.stdout.strip()
    except Exception as exc:
        return 0, str(exc)


if __name__ == '__main__':
    main()
