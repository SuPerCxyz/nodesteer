#!/usr/bin/env python3
"""Corrected mixed-version check + per-case records for the P0-1 remote phase.

The first run's MIXED rows actually dispatched executions on native-1 and only used
native-4 as the remote *state* target. Here the echo task itself targets native-4 so
the OLD agent evaluates the remote condition.
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import regression_run as rr  # noqa: E402

hub = rr.hub
hub.login()
P = rr.P
N4 = rr.N4
n1 = rr.ST['node_ids']['native-1']
n4 = rr.ST['node_ids']['native-4']
out = {'at_utc': rr.now_utc(), 'note': 'corrected mixed-version check: task executed BY old agent on native-4'}

# clock probes
rc, hub_epoch, _ = rr.ssh(rr.HUB_HOST, 'date -u +%s.%N')
rc2, n4_epoch, _ = rr.ssh(N4, 'date -u +%s.%N')
rc3, n1_epoch, _ = rr.ssh(rr.N1, 'date -u +%s.%N')
out['clock_probe'] = {'hub': float(hub_epoch.strip()), 'native-4': float(n4_epoch.strip()),
                      'native-1': float(n1_epoch.strip())}
out['clock_delta'] = {'hub_minus_native4': float(hub_epoch) - float(n4_epoch),
                      'hub_minus_native1': float(hub_epoch) - float(n1_epoch)}

root4 = '/var/lib/%smk4' % P
rr.ssh(N4, 'mkdir -p %s && chmod 0755 %s' % (root4, root4))
rr.ST.setdefault('host_roots', []).append({'host': N4, 'path': root4, 'role': 'P0-1 mixed-version markers'})
rr.save_state(rr.ST)


def mk_task(key, node_id, root):
    name = P + 'rem-' + key
    cmd = ("printf 'EXEC %%s\\n' \"$(date -u +%%FT%%T.%%NZ)\" > %s/%s.marker && echo RAN_%s"
           % (root, key, key))
    code, body = hub.post('/api/tasks', rr._task_body(name, node_id, command=cmd))
    if code not in (200, 201):
        raise RuntimeError('task create failed %s %s' % (key, body))
    rr.ST['fixtures'].append(('task', body['id'], name))
    rr.save_state(rr.ST)
    return body['id']


def marker(root, key):
    p = '%s/%s.marker' % (root, key)
    rc, so, _ = rr.ssh(N4, 'if [ -f %s ]; then echo PRESENT; cat %s; rm -f %s; else echo ABSENT; fi' % (p, p, p))
    lines = so.strip().split('\n')
    present = bool(lines) and lines[0] == 'PRESENT'
    return {'present': present}


def run_case(case_id, key, condition, note, node_id):
    tid = mk_task(key, node_id, root4)
    code, resp = rr._put_task_condition(tid, condition)
    if code != 200:
        return {'case_id': case_id, 'task_id': tid, 'put_http': code, 'put_body': resp, 'note': note}
    marker(root4, key)
    code, run = hub.post('/api/tasks/%s/run' % tid, {'node_ids': {node_id: True}})
    eid = run[0]['id'] if code == 200 and run else None
    ex = rr.wait_exec(eid, timeout=90) if eid else {}
    ms = marker(root4, key)
    return {'case_id': case_id, 'task_id': tid, 'execution_id': eid,
            'status': ex.get('status'), 'block_reason': ex.get('block_reason'),
            'exit_code': ex.get('exit_code'), 'marker_present': ms.get('present'),
            'note': note, 'at_utc': rr.now_utc()}


def rem(node, prop, op, value, task=None):
    c = {'type': 'remote', 'remote': {'node_id': node, 'property': prop, 'operator': op, 'value': value}}
    if task:
        c['remote']['task_id'] = task
    return c


# seed last_execution on native-4 (old agent must run it)
hist4 = mk_task('hist4-ok', n4, root4)
code, run = hub.post('/api/tasks/%s/run' % hist4, {'node_ids': {n4: True}})
hist_ex = rr.wait_exec(run[0]['id']) if code == 200 and run else {}
marker(root4, 'hist4-ok')
out['native4_history_seed'] = {'task_id': hist4, 'status': hist_ex.get('status')}

cases = []
cases.append(run_case('MIXED4-ON01', 'm4on01', rem(n1, 'online', '==', 'online'),
                      '旧 Agent 执行任务；remote 条件查 native-1（新 Hub 返回旧字段+新字段）', n4))
cases.append(run_case('MIXED4-LX01', 'm4lx01', rem(n4, 'last_execution', '==', 'SUCCESS', task=hist4),
                      '旧 Agent 执行任务；remote last_execution 有真实历史', n4))
cases.append(run_case('MIXED4-ON03', 'm4on03', rem(n1, 'online', '==', 'offline'),
                      '旧 Agent 执行任务；remote 不满足路径', n4))
out['cases'] = cases
rr.ev_write('remote-mixed-v4.json', out)
for c in cases:
    print('%-14s status=%-9s marker=%s block=%s' % (c.get('case_id'), c.get('status'),
                                                    c.get('marker_present'), (c.get('block_reason') or '')[:60]))

# -------- merge into remote-cases.json and re-annotate old MIXED rows --------
ev_path = rr.EV / 'remote-cases.json'
ev = json.loads(ev_path.read_text())
old_on01 = ev['cases'].get('MIXED-v4-ON01', {})
old_lx01 = ev['cases'].get('MIXED-v4-LX01', {})
ev['cases']['MIXED-v4-ON01']['note'] = ('（修正前口径：任务实际在 native-1 新 Agent 执行，条件是 native-4 的远程状态）'
                                        '新 Agent 判定 native-4 online 状态 → %s' % old_on01.get('status'))
ev['cases']['MIXED-v4-ON01']['verdict'] = 'INFO'
ev['cases']['MIXED-v4-LX01']['note'] = ('（修正前口径）新 Agent 判定 native-4 未观察 last_execution → BLOCKED fail-closed，'
                                        '与混合版本无关')
ev['cases']['MIXED-v4-LX01']['verdict'] = 'INFO'
ev['cases']['MIXED4-ON01'] = cases[0]
ev['cases']['MIXED4-LX01'] = cases[1]
ev['cases']['MIXED4-ON03'] = cases[2]
ev['mixed_version'] = {
    'clock_probe': out['clock_probe'],
    'clock_delta': out['clock_delta'],
    'native4_old_agent_remote_online': cases[0].get('status'),
    'native4_old_agent_remote_last_execution': cases[1].get('status'),
    'native4_old_agent_remote_unsatisfied': cases[2].get('status'),
    'interpretation': ('旧 Agent 仍走 observed_at 绝对时间回退路径；其结果取决于该主机与 Hub 的相对时钟。'
                       'BLOCKED 即旧 Agent 仍受偏移门禁影响（预期、文档已注明）；SUCCESS/SKIPPED 表示该主机时钟关系未触发门禁。'),
}
ev_path.write_text(json.dumps(ev, ensure_ascii=False, indent=1) + '\n')

# -------- per-case records (原状态来自 repair feature-inventory / p-cond 复核) --------
rr.record('SYS-27', 'remote', 'PASS',
          '原 FAIL（remote online 满足/不满足均 BLOCKED）→ 满足 SUCCESS 且真实执行、不满足 SKIPPED 且无副作用',
          ['evidence/remote-cases.json'])
rr.record('SYS-28', 'remote', 'PASS',
          '原 PARTIAL（成功/失败/不存在受 BLOCKED 阻塞）→ SUCCESS 历史==SUCCESS 满足执行、==FAILED 不满足 SKIPPED、'
          '未观察→BLOCKED fail-closed',
          ['evidence/remote-cases.json'])
rr.record('SYS-29', 'remote', 'PASS_WITH_NOTES',
          '原 PARTIAL；TTL 未过期路径可用（所有判定均在其新鲜窗口内完成），未观察仍 fail-closed；'
          '"TTL 过期"子用例仍不可经产品路径构造（缓存分支无写入方，沿用 p-cond OBS 结论）',
          ['evidence/remote-cases.json'])
rr.record('SYS-30', 'remote', 'PASS',
          '原 PARTIAL（Remote「不满足」被 SYS-27 阻塞）→ remote 不满足真实 SKIPPED、无 marker/副作用',
          ['evidence/remote-cases.json'])
rr.record('E2E-10', 'remote', 'PASS_WITH_NOTES',
          '原 PARTIAL（13/13 Remote BLOCKED）→ 满足/不满足/确定性 offline/未观察 fail-closed 全部可判定；'
          'TTL 过期维度仍不可构造',
          ['evidence/remote-cases.json'])
rr.record('INV-09', 'remote', 'PASS',
          '原 PASS_WITH_NOTES（确定性状态也被判未知）→ 未观察远程状态 BLOCKED 无业务效果，且与可判定状态区分',
          ['evidence/remote-cases.json'])
rr.record('SYS-26', 'remote', 'PASS',
          'AND(remote online, local true) → SUCCESS（此前受 SYS-27 阻塞为 BLOCKED）；本地矩阵/SKIPPED/BLOCKED 语义未回退',
          ['evidence/remote-cases.json'])
rr.record('MIXED-VERSION-newHub-oldAgent', 'remote', 'INFO',
          '新 Hub + 旧 Agent（native-4）：连接与同步 online/synced；remote 条件由旧 Agent 执行时走回退路径，'
          '实测结果见 evidence/remote-mixed-v4.json（受宿主相对时钟影响）',
          ['evidence/remote-mixed-v4.json'])
rr.dump_results()
print('records written')
