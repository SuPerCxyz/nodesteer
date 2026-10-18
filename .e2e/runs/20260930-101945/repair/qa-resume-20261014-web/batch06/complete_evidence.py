#!/usr/bin/env python3
"""Post-run evidence completion for web batch06.

  * records the node audit rows with the API's real field names
    (resource / resource_id / action) — the in-run filter used object_id/target_id
    and therefore returned an empty list in phase4;
  * records the preserved immutable Execution history readback;
  * records the environment observations that belong to this batch.

No environment state is changed here.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'runners'))
import hubapi  # noqa: E402

EV = HERE / 'evidence'
NODE_ID = 'bf80d9a9-2adc-4aa9-a58e-8d56d254590a'

hub = hubapi.Hub()
hub.login()

rows = hub.json('/api/audit?limit=500')
rows = rows if isinstance(rows, list) else rows.get('items', [])
node_rows = [r for r in rows if r.get('resource') == 'node' and r.get('resource_id') == NODE_ID]

# --- phase4 audit readback (correct field names) ---
p4_path = EV / 'phase4-p04-05-offline-set-online.json'
p4 = json.loads(p4_path.read_text())
p4['node_audit_readback'] = {
    'at_utc': '2026-10-14T10:26:00Z',
    'note': 'the in-run filter used object_id/target_id and returned []; re-read with the real '
            'audit fields (resource/resource_id/action) while the fixture still existed',
    'rows': [{'action': r.get('action'), 'resource': r.get('resource'),
              'resource_id': r.get('resource_id'), 'created_at': r.get('created_at')}
             for r in sorted(node_rows, key=lambda r: r.get('created_at') or '')],
    'row_count': len(node_rows),
    'status_updates_recorded': len([r for r in node_rows if r.get('action') == 'update']),
}
p4_path.write_text(json.dumps(p4, ensure_ascii=False, indent=2) + '\n')

# --- preserved execution history readback ---
ids = {
    'p0404_paused_skip': 'bf285560-87c3-45c1-8e03-7278f86968bf',
    'p0404_after_resume': 'b580f4df-aab1-41cd-8bed-b0e39b77ff3e',
    'p1104_agent_offline': '482f1ae0-ddf1-4e9a-803c-c3fd2fb27ab6',
    'p0608_group_dispatch': '616bd019-c902-4f62-b0d5-daf9ebdd2522',
    'p17p18_offline': 'dd12456f-0c38-4fdf-afe1-e80f73d9db38',
}
readback = {}
for key, eid in ids.items():
    code, body = hub.get('/api/executions/' + eid)
    readback[key] = {'id': eid, 'http': code, 'status': (body or {}).get('status'),
                     'block_reason': (body or {}).get('block_reason'),
                     'offline': (body or {}).get('offline'),
                     'trigger_type': (body or {}).get('trigger_type')}
(EV / 'preserved-executions-readback.json').write_text(
    json.dumps({'at_utc': '2026-10-14T10:26:00Z',
                'note': 'immutable Execution history produced by this batch; no delete path exists',
                'executions': readback}, ensure_ascii=False, indent=2) + '\n')

# --- observations ---
nodes = hub.json('/api/nodes')
native3 = [n for n in nodes if 'native-3' in (n.get('hostname') or '')]
observations = [
    {'id': 'OBS-B06-01', 'severity': 'low',
     'title': 'maintenance/disabled 状态徽标无中文文案',
     'detail': 'StatusBadge 的标签表中没有 maintenance/disabled 的 zh 文案，节点列表在中文界面下'
               '直接显示原始英文状态串 "maintenance"（本批 UI 回读与截图可见）。',
     'evidence': ['evidence/P0404-02-node-maintenance-row.png',
                  'evidence/phase3-p04-04-operation-menu.json'],
     'source': 'web/src/features/shared/ui.tsx StatusBadge labels'},
    {'id': 'OBS-B06-02', 'severity': 'medium',
     'title': 'offline 节点手工设为在线后无会话仍显示在线',
     'detail': '节点已 offline（无 Agent 会话）时手工「设为在线」会持久化为 online；该记录不会自动'
               '回落到 offline（无断开/心跳事件），直到 Hub 重启复位孤儿 online 或 Agent 重新 HELLO。'
               '此时对该节点手动执行会失败并留 FAILED 记录（block_reason=agent offline）。'
               '是否符合预期需产品确认，本批仅记录现象。',
     'evidence': ['evidence/phase4-p04-05-offline-set-online.json',
                  'evidence/phase5-p11-04-offline-node-dispatch.json'],
     'source': 'hub/node.go MarkOffline 仅由 gateway 断开回调触发；无周期清扫'},
    {'id': 'OBS-B06-03', 'severity': 'low',
     'title': 'Node Detail 任务列表用整页文本测量会被 执行 区污染',
     'detail': 'Node Detail 的 执行 区按 execution 行渲染 task.name（features/tasks/index.tsx '
               'ExecutionRows），因此整页文本里会残留与节点无目标关系的任务名；按 SectionCard 的 '
               '任务 区做作用域读取才准确。本批已按作用域读取并说明。',
     'evidence': ['evidence/phase6-p06-08-group-change-sync.json',
                  'evidence/assertion-reevaluation.json']},
    {'id': 'OBS-B06-04', 'severity': 'low',
     'title': '菜单点击后立即读取行状态徽标可能读到旧值',
     'detail': '点击菜单项后 mutation 成功 → invalidate ["nodes"] → 重新拉取存在短暂延迟；'
               '本次在 agent-pause 步骤未加 settle 延迟即读取，得到旧徽标 在线（Hub 已是 '
               'maintenance）；同一状态在 P04-04 步骤 2（延迟 1.5s）读取正确。属 Harness 时序问题，'
               '非产品缺陷。',
     'evidence': ['evidence/phase3-p04-04-operation-menu.json']},
    {'id': 'OBS-B06-05', 'severity': 'info',
     'title': '并行批次干扰：native-3 在本次批次结束后离线且 SSH 握手超时',
     'detail': '本批 final guard（18:06）时 native-3 为 online/synced；18:09:47 起 Hub 记为 offline，'
               '192.168.100.222:22 端口可连但 SSH banner 握手超时。本批全程未对 native-3 做任何操作。',
     'evidence': ['final-cleanup-guard.json'],
     'native3_now': [{'hostname': n.get('hostname'), 'status': n.get('status'),
                      'sync_status': n.get('sync_status'), 'last_seen': n.get('last_seen')}
                     for n in native3]},
    {'id': 'OBS-B06-06', 'severity': 'info',
     'title': 'Harness 修复记录（3 处）',
     'detail': '(1) runners/child_agent.py inventory() 的格式化参数不足导致 2 次启动前置失败，'
               '修复后重跑；(2) CLI 的 wait --text 未文档化 --timeout，改为 eval 轮询实现；'
               '(3) 登录表单需在 hydration 完成后再填充，改为先轮询输入框存在再填充并校验长度。',
     'evidence': ['run-stdout.log']},
]
(EV / 'observations.json').write_text(
    json.dumps({'batch_id': 'qa-resume-20261014-web-batch06', 'at_utc': '2026-10-14T10:26:00Z',
                'observations': observations}, ensure_ascii=False, indent=2) + '\n')

# --- merge observation ids into results.json ---
res_path = HERE / 'results.json'
res = json.loads(res_path.read_text())
res['observations'] = {'evidence': 'evidence/observations.json',
                       'ids': [o['id'] for o in observations]}
res['preserved_execution_history_readback'] = 'evidence/preserved-executions-readback.json'
res_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + '\n')

print(json.dumps({'node_audit_rows': len(node_rows),
                  'executions_readback': {k: v['status'] for k, v in readback.items()},
                  'observations': [o['id'] for o in observations]}, ensure_ascii=False, indent=2))
