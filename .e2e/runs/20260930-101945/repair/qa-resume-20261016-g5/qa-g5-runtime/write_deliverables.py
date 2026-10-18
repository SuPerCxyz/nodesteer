#!/usr/bin/env python3
"""Assemble the G5 batch deliverables from state.json + evidence/*.json."""
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EVID = BATCH / 'evidence'

NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = NONCE + '-'
VERIFIED_BUILD = 'dddee4f0-e2e-repair-20261016'
ENDPOINT = 'http://192.168.100.209:18080'
BROWSER_SESSION = (HERE / 'browser-session.txt').read_text().strip()


def ev(name):
    p = EVID / name
    return json.loads(p.read_text()) if p.is_file() else {}


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


guard = ev('phase00-guard.json')
fixtures = ev('phase01-fixtures.json')
login = ev('phase01b-ui-login.json')
cond = ev('phase02-conditions.json')
apps = ev('phase03-apps.json')
targets = ev('phase04-targets.json')
labels = ev('phase05-labels.json')
x09 = ev('phase05b-x09.json')
payloads = ev('phase05c-payloads.json')
cleanup = ev('phase06-cleanup.json')
oracle = ev('phase07-oracle.json')
state = json.loads((HERE / 'state.json').read_text())
st = state['state']

NODE_A = st['nodes'].get('a')
NODE_B = st['nodes'].get('b')
CHILD_A_NAME = PREFIX + 'child-a'
CHILD_B_NAME = PREFIX + 'child-b'
APP_ID = st['apps'].get('main')
SCRIPT_ID = st['scripts'].get('main')
CLONE_ID = st['scripts'].get('clone')
GROUP_STATIC = st['groups'].get('static')
GROUP_LABEL = st['groups'].get('label')
ARTIFACT_ID = st['artifacts'].get('app')
UNIT_NAME = 'nodesteer-' + PREFIX + 'app.service'
BINARY = '/opt/' + PREFIX.rstrip('-') + '/app/bin/app'
CONFIG = '/opt/' + PREFIX.rstrip('-') + '/app/config/app.env'

E = lambda n: 'evidence/' + n

# ---------------------------------------------------------------- case results
CASES = []


def case(cid, title, status, confidence, ui_path, dimensions, unverified=None,
         evidence=(), harness_errors=None, note=None):
    CASES.append({
        'case_id': cid, 'title': title, 'proposed_status': status,
        'confidence': confidence, 'verified_build': VERIFIED_BUILD,
        'verified_agent_binary_sha256': guard.get('verified_agent_sha256'),
        'ui_path': ui_path, 'dimensions': dimensions,
        'unverified': unverified or [], 'evidence': list(evidence),
        'harness_errors': harness_errors or [], 'note': note,
    })


uideploy = apps.get('ui_deploy', {})
ops = apps.get('ui_operations', {})
backlink = apps.get('ui_node_backlink', {})
ui = cond.get('ui', {})
uia = cond.get('ui_and', {})
uian = cond.get('ui_and_none', {})
steps = targets.get('target_node_steps', [])
switch = targets.get('type_target_switching', [])
four = x09.get('four_type_roundtrip_ui', {})
stale = x09.get('stale_target_dispatch', {})
lt = labels.get('label_task', {})
fl02 = labels.get('fl02', {})
fl03 = labels.get('fl03', {})
fl04 = labels.get('fl04', {})

# ---------------- P09P11-07 ----------------
case('P09P11-07', '类型 app_deploy：应用选择、节点目标、部署执行。', 'PASS', 'high',
     '/tasks/new (类型=托管应用部署) -> 保存 -> /tasks/<id>/run -> /applications/<id>',
     {
         'ui_type_options': uideploy.get('type_options'),
         'ui_application_select': {
             'options': (uideploy.get('app_options') or {}).get('options'),
             'picked': uideploy.get('app_pick'),
             'note': '应用选择控件列出托管应用（每项=应用名）；当前 app_deploy 表单没有独立的 '
                     'artifact/version 控件，版本与发布包绑定在应用定义上，因此该子维度以“控件不存在（实测）'
                     '+ 部署效果中的版本/发布包绑定”验证，而不是跳过。'},
         'ui_operation_select': (uideploy.get('operation_options') or {}).get('options'),
         'ui_node_target': uideploy.get('node_check'),
         'ui_save_request': [r for r in (uideploy.get('save') or {}).get('requests', [])
                             if r['method'] == 'POST'],
         'stored_task': uideploy.get('stored'),
         'real_execution': uideploy.get('exec_deploy'),
         'app_node_state_after_deploy': uideploy.get('app_state_after_deploy'),
         'host_effect': uideploy.get('host_after_deploy'),
         'host_binary_matches_artifact_sha': uideploy.get('host_binary_matches_artifact'),
         'artifact_sha256': uideploy.get('artifact_sha'),
     },
     evidence=[E('phase03-apps.json'), E('phase01c-ui-probe.json'),
               'evidence/phase03-ui-app-deploy-form.png'],
     note='真实部署执行：Hub 执行 SUCCESS → 子节点 systemd 单元 active、落盘二进制 sha256 == 发布包 sha256、'
          '发布包进入节点缓存、本地应用定义 version=v1；应用节点状态 healthy/v1。')

# ---------------- P09P11-08 ----------------
case('P09P11-08', '类型 app_operation：start/stop/restart/upgrade 四种操作。', 'PASS', 'high',
     '/tasks/<id>/edit (类型=托管应用操作, 操作逐项) -> 保存 -> 运行',
     {
         'ui_operation_options': (ops.get('start', {}).get('operation_options') or {}).get('options'),
         'per_operation': {
             op: {
                 'stored_type': v.get('stored_type'),
                 'stored_app_operation': v.get('stored_app_operation'),
                 'stored_application_id': v.get('stored_application_id'),
                 'ui_save': bool((v.get('save') or {}).get('saved')),
                 'execution': v.get('exec'),
                 'app_node_state': v.get('app_state'),
                 'host_unit': {'ActiveState': v.get('host_unit_active'),
                               'SubState': v.get('host_unit_substate')},
             } for op, v in ops.items()
         },
     },
     evidence=[E('phase03-apps.json'), 'evidence/phase03-ui-app-op-start.png',
               'evidence/phase03-ui-app-op-stop.png', 'evidence/phase03-ui-app-op-restart.png',
               'evidence/phase03-ui-app-op-upgrade.png'],
     note='四操作均由真实 UI 选择并保存（app_operation 字段随类型归一化）后经产品路径执行；'
          '实际效果：start→单元 active/running、health=healthy；stop→inactive/dead、health=stopped；'
          'restart→active/running；upgrade→执行 SUCCESS、health=healthy。')

# ---------------- P09P11-09 ----------------
case('P09P11-09', 'Target node：单节点、多节点、去选节点。', 'PASS', 'high',
     '/tasks/new (勾选 child-a) -> 保存；/tasks/<id>/edit 加选 child-b -> 保存；编辑页去选 child-b -> 保存',
     {
         'steps': [{'step': s['step'],
                    'ui_action': {k: v for k, v in s.items()
                                  if k in ('check_a', 'check_b', 'uncheck_b')},
                    'checkbox_states_after': s.get('checkboxes_after') or s.get('checkboxes'),
                    'stored_target': s.get('stored_target'),
                    'ui_save': (s.get('save') or {}).get('saved')}
                   for s in steps],
         'owned_node_ids': {'child-a': NODE_A, 'child-b': NODE_B},
     },
     evidence=[E('phase04-targets.json')],
     note='单节点 [child-a] → 多节点 [child-a, child-b] → 去选回 [child-a]，每步都由 UI 勾选/取消并读回 API 存储的 target.node_ids。')

# ---------------- P09P11-11 ----------------
case('P09P11-11', 'Target label：key/value、匹配节点变化。', 'PASS', 'high',
     '/tasks/new (目标类型=标签) -> 保存 -> 运行；节点标签写入/变更经产品 API',
     {
         'label_writes': {
             'set_child_a': labels.get('labels', {}).get('set_a'),
             'child_a_labels_after_set': labels.get('labels', {}).get('node_a_labels'),
             'set_child_b_same_label': lt.get('set_b_same_label'),
             'labels_after_b': lt.get('labels_after_b'),
             'set_child_a_new_value': lt.get('set_a_new_value'),
         },
         'stored_target': lt.get('stored_target'),
         'run_1_only_child_a': {'execution': (lt.get('run_a_only') or {}).get('exec_a'),
                                'nodes_hit': (lt.get('run_a_only') or {}).get('nodes_hit')},
         'run_2_after_child_b_label_added': lt.get('run_a_b'),
         'run_3_after_child_a_value_changed': {
             'execution': (lt.get('run_a_changed') or {}).get('exec'),
             'executions_added_on_child_a': (lt.get('run_a_changed') or {}).get('a_executions_since_change'),
             'nodes_hit': (lt.get('run_a_changed') or {}).get('nodes_hit')},
         'matched_node_id_child_a': NODE_A,
         'matched_node_id_child_b': NODE_B,
     },
     evidence=[E('phase05-labels.json')],
     note='标签匹配节点集合的真实变化：1 个节点 → 加标签后 2 个节点 → 改 child-a 标签值后 child-a 不再命中'
          '（新增执行中 child-a 0 条），每次均由真实执行记录证明投递对象。')

# ---------------- P09P11-12 ----------------
case('P09P11-12', '类型/目标切换后旧字段不误提交。', 'PARTIAL', 'high',
     '/tasks/<id>/edit（类型 app_operation→命令→脚本；目标 分组→标签→节点）逐次保存',
     {
         'switch_steps': [{'step': s['step'],
                           'stored': {k: s['stored'].get(k) for k in
                                      ('type', 'command', 'script_id', 'application_id',
                                       'app_operation', 'target')},
                           'ui_save': (s.get('save') or {}).get('saved'),
                           'requests': [(r['method'], r['url'].split('/api')[-1], r['status'])
                                        for r in (s.get('save') or {}).get('requests', [])]}
                          for s in switch],
         'captured_final_put_payload': payloads.get('ui_target_final_put'),
         'conclusion': {
             'app_operation_field': '正确：切换到 command/script 时 app_operation 从 payload 中删除'
                                    '（FAIL-G-001 修复生效，实测 payload 无该键）。',
             'application_id_field': '未剔除：类型切到 script 后 payload 仍带 application_id='
                                      + str(APP_ID) + '（惰性数据，不影响调度：真实执行按 type 走脚本，'
                                      '未触发应用部署，见 X09 stale-dispatch 证据）。',
             'script_id_field': '未剔除：从 script 切回其它类型时 script_id 仍随 payload 提交（同上，惰性）。',
             'target_subfields': '未剔除：目标类型 node/group/label 互切时 group_ids/label_key/label_value '
                                 '与 node_ids 并存于存储与 payload（调度只按 target.type 取用，实测未影响投递集合）。',
         },
     },
     unverified=['“payload 无任何旧字段”这一绝对口径未满足：app_operation 已归一化，但 '
                 'application_id/script_id 与 target 子字段仍随 payload 提交（惰性、未影响业务）。'],
     evidence=[E('phase04-targets.json'), E('phase05c-payloads.json'), E('phase05b-x09.json')],
     note='写侧证据为浏览器网络日志中的真实 PUT postData（phase05c）与保存后的 API 读回；'
          '新增观察 OBS-20261016-G5-STALE-PAYLOAD-FIELDS-CARRIED 建议登记。')

# ---------------- P09P11-15 ----------------
case('P09P11-15', 'Local Condition 8 种 metric、6 种 operator、设置/清除。', 'PASS', 'high',
     '/tasks/new 本地条件卡片（metric/operator 下拉、设置条件/清除）+ /tasks/<id>/edit',
     {
         'ui_metric_options': (ui.get('metric_select') or {}).get('options'),
         'ui_operator_options': (ui.get('local_operator_select') or {}).get('options'),
         'api_all_8_metrics_accepted': sorted((cond.get('local_metrics_accepted') or {}).keys()),
         'api_all_6_operators_accepted': sorted((cond.get('local_operators_accepted') or {}).keys()),
         'api_negative_validation': {k: v.get('http')
                                     for k, v in (cond.get('local_negative_validation') or {}).items()},
         'set_condition': ui.get('set_local_absent'),
         'summary_after_set': ui.get('summary_after_set'),
         'override_condition': (cond.get('ui_echo_override') or {}).get('override_summary'),
         'stored_after_override': (cond.get('ui_echo_override') or {}).get('stored_after_override'),
         'clear_condition_click': cond.get('clear_local_click'),
         'summary_after_clear': cond.get('summary_after_clear'),
         'stored_after_clear': cond.get('stored_after_clear'),
         'real_effect_blocked': cond.get('exec_blocked'),
         'real_effect_after_clear': cond.get('exec_after_clear'),
     },
     evidence=[E('phase02-conditions.json'), 'evidence/phase02-ui-local-condition-set.png'],
     note='8 metric × 6 operator 全部由 UI 下拉逐项枚举并由 API 逐项受理；metric 专属必填字段'
          '（path/command/端口范围）由 8 条负向校验（均 400 + 明确错误文案）覆盖；'
          '设置→覆盖→清除三步均有 UI 摘要与 API 存储证据；实际效果为 SKIPPED(condition not satisfied) → 清除后 SUCCESS。')

# ---------------- P09P11-16 ----------------
case('P09P11-16', 'Remote Condition online、last_execution、operator、节点和 task_id 校验。', 'PASS', 'high',
     '/tasks/new 远程条件卡片（节点/属性/操作符/值 + 设置远程条件）',
     {
         'ui_property_options': (ui.get('remote_property_select') or {}).get('options'),
         'ui_operator_options': (ui.get('remote_operator_select') or {}).get('options'),
         'api_accepts': {k: v.get('http') for k, v in (cond.get('remote_conditions') or {}).items()},
         'api_rejects': {k: v.get('http') for k, v in (cond.get('remote_conditions') or {}).items()
                         if k.startswith('last_execution_missing') or k.startswith('last_execution_unknown')
                         or k in ('unknown_node', 'missing_node_id', 'bad_operator', 'bad_property',
                                  'bad_online_value')},
         'ui_node_pick_required': uia.get('set_remote_no_node'),
         'stored_remote_condition': ((uia.get('stored') or {}).get('and') or [{}])[-1],
         'real_execution_when_online': uia.get('exec_both_true'),
     },
     evidence=[E('phase02-conditions.json')],
     note='UI 属性枚举 [online, 上次执行] 与操作符 [==, !=] 逐项抓取；未选节点时“设置远程条件”为 no-op'
          '（摘要不变）；API 侧 7 条负向校验全部 400：last_execution 缺 task_id、task_id 不存在、'
          'node 不存在、node_id 为空、操作符非法、属性非法、online 值非法。')

# ---------------- P09P11-17 ----------------
case('P09P11-17', 'Condition AND 结构通过 API 或当前支持的编辑入口验证。', 'PASS', 'high',
     '/tasks/new 勾选“所有条件都满足（AND）”+ 设置本地/远程条件 -> 保存；API 直接提交 and 结构',
     {
         'api_and_two_children': {
             'create_http': (cond.get('and') or {}).get('create_http'),
             'stored': (cond.get('and') or {}).get('stored'),
             'child_types': (cond.get('and') or {}).get('stored_types'),
         },
         'api_and_nested': {'create_http': (cond.get('and') or {}).get('nested_create_http'),
                            'stored': (cond.get('and') or {}).get('nested_stored')},
         'api_and_empty_rejected': ((cond.get('local_negative_validation') or {})
                                    .get('and_empty') or {}).get('body'),
         'ui_and_both_conditions': {
             'and_checkbox': uia.get('and_check'),
             'set_local': uia.get('set_local'),
             'set_remote': uia.get('set_remote'),
             'stored': uia.get('stored'),
         },
         'real_execution_both_satisfied': uia.get('exec_both_true'),
     },
     unverified=['AND 结构在编辑页的可见性/清除：保存 AND 条件后，编辑页不再渲染条件摘要行，也不渲染“清除”'
                 '按钮（按钮仅在单一 local/remote 条件下渲染），因此 AND 状态只能被“设置条件”覆盖，'
                 '无法从 UI 清除（含 clearCondition 的 downgrade 分支不可达）。已作为独立观察记录，'
                 '未放宽断言。'],
     evidence=[E('phase02-conditions.json'), 'evidence/phase02-ui-and-both-conditions.png'],
     note='API 受理 and[local,remote] 与嵌套 and，child 类型/数量读回一致，空 and 被拒（400 and condition '
          'requires at least one child）；UI 的 AND 勾选 + 两个条件设置同样落库为 and 结构且真实执行 SUCCESS。')

# ---------------- X09 ----------------
case('X09', 'Task 四类型往返保存后 command/script_id/application_id/app_operation 隐藏字段不改变当前业务；'
            'target Node/Group/Label 往返及 Group Static/Label 往返。', 'PASS', 'high',
     '/tasks/<id>/edit（四类型各往返保存一次）；目标类型互切；/groups 与 /groups/<id>',
     {
         'four_type_ui_roundtrip': {
             typ: {
                 'create_http': v.get('create_http'),
                 'ui_echo': v.get('ui_echo'),
                 'ui_save': bool((v.get('save') or {}).get('saved')),
                 'stored_before': v.get('api_get_after_create'),
                 'stored_after_roundtrip': v.get('api_get_after_roundtrip'),
                 'hidden_fields_unchanged': v.get('hidden_fields_unchanged'),
             } for typ, v in four.items()},
         'target_roundtrip': [{'step': s['step'], 'stored_target': s['stored'].get('target')}
                              for s in switch],
         'group_roundtrip': {
             'static': {'id': GROUP_STATIC, 'type': 'static',
                        'members': (targets.get('manifest') or {}).get('node_a') and
                                   [NODE_A]},
             'label': {'id': GROUP_LABEL,
                       'get': (labels.get('labels') or {}).get('label_group_get'),
                       'create': (labels.get('labels') or {}).get('label_group')},
             'ui_groups_page_rows': (fl03.get('groups_page') or {}).get('rows'),
         },
         'stale_hidden_fields_do_not_change_behaviour': {
             'stored_target_after': (stale or {}).get('stored_after', {}).get('target'),
             'execution': (stale or {}).get('exec'),
             'executions_added_on_child_a': (stale or {}).get('executions_on_child_a_added'),
             'static_group_members': (stale or {}).get('static_group_members'),
             'app_state_after': (stale or {}).get('app_state_after'),
             'deployment_journal_rows': (stale or {}).get('deployments_rows'),
         },
     },
     evidence=[E('phase05b-x09.json'), E('phase04-targets.json'), E('phase05-labels.json')],
     note='四类型（command/script/app_deploy/app_operation）经真实编辑页回显并原样保存，隐藏字段'
          '（command/script_id/application_id/app_operation）逐类型读回不变；目标 Node/Group/Label 往返读回一致；'
          '静态组与标签组的类型/标签键值/成员读回一致。决定性的“不改变当前业务”证据：一个 type=script、'
          'script_id 有效、同时残留 application_id/group_ids/label_* 的任务，真实执行只投递到 node 目标'
          '（静态组成员 child-a 无新增执行）、只跑脚本（stdout 为脚本文本、execution.script_revision=3）、'
          '未触发任何应用部署（应用节点状态仍为 upgrade/healthy、部署日志无新增行）。')

# ---------------- X11 ----------------
case('X11', 'Local/Remote/AND 条件编辑回显、AND 勾选未设置的语义、覆盖/清除一个和最后一个条件后实际执行/阻断。',
     'PASS', 'high',
     '/tasks/<id>/edit 条件回显；清除最后一个条件后立即运行；AND 勾选但未设置条件',
     {
         'echo_on_edit': {
             'metric_shown': (cond.get('ui_echo_override') or {}).get('local_metric_shown'),
             'operator_shown': (cond.get('ui_echo_override') or {}).get('local_operator_shown'),
             'path_shown': (cond.get('ui_echo_override') or {}).get('path_shown'),
             'value_shown': (cond.get('ui_echo_override') or {}).get('value_shown'),
             'summary_line': (cond.get('ui_echo_override') or {}).get('summary_shown'),
             'and_checkbox_state': (cond.get('ui_echo_override') or {}).get('and_checkbox_checked'),
         },
         'override_one_condition': {
             'ui': (cond.get('ui_echo_override') or {}).get('override_set'),
             'stored': (cond.get('ui_echo_override') or {}).get('stored_after_override'),
         },
         'clear_last_condition': {
             'click': cond.get('clear_local_click'),
             'summary_after': cond.get('summary_after_clear'),
             'stored_after': cond.get('stored_after_clear'),
         },
         'real_block_then_execute': {
             'blocked_execution': cond.get('exec_blocked'),
             'execution_after_clear': cond.get('exec_after_clear'),
         },
         'and_checked_nothing_set': {
             'ui': uian.get('and_check'),
             'summary': uian.get('summary'),
             'stored_condition': uian.get('stored_condition'),
         },
     },
     unverified=['AND 结构的“清除”路径：AND 已保存时编辑页不渲染摘要与清除按钮（见 P09P11-17 unverified），'
                 '因此“清除 AND 中最后一个条件后的降级”只能由代码阅读判断，UI 路径不可达，未做运行时断言。'],
     evidence=[E('phase02-conditions.json'), 'evidence/phase02-ui-condition-echo.png'],
     note='回显四要素（metric/operator/path/value + 摘要行）齐全；覆盖=再次“设置条件”后摘要与库内值替换；'
          '清除最后一个条件 → 摘要为空、库内 condition=null，随后真实执行由 SKIPPED(condition not satisfied) '
          '变为 SUCCESS（stdout 非空）；AND 勾选但未设置任何条件 → 保存 payload 无 condition（库内 null）。')

# ---------------- FL-02 ----------------
case('FL-02', 'Agents → Agent Detail → Task → 节点任务过滤、Task 回链、目标一致', 'PASS', 'high',
     '/agents -> 点击 owned child-a 行 -> /agents/<node> -> 任务链接 -> /tasks/<id>',
     {
         'agents_list_owned_row': (fl02.get('agents_page') or {}).get('rows'),
         'agent_detail_url_after_click': (fl02.get('agent_detail') or {}).get('url'),
         'agent_detail_task_links': (fl02.get('agent_detail') or {}).get('task_links'),
         'task_backlink_href': fl02.get('task_link_href'),
         'task_detail_url': fl02.get('task_detail_url'),
         'task_detail_target_text': fl02.get('task_detail_target'),
         'task_stored_target': fl02.get('task_detail_stored_target'),
         'node_a_task_links': fl02.get('node_a_tasks'),
         'node_b_task_links': fl02.get('node_b_tasks'),
     },
     unverified=['节点/Agent 详情页“任务”区域链接捕获器同时收集了“执行”表格指向 /tasks/<id> 的行，'
                 '因此每条节点任务的链接数是“任务行 + 执行行”的超集；过滤结论依据“标签任务是否出现在该节点”'
                 '（child-b 出现、child-a 不出现）而非行数。'],
     evidence=[E('phase05-labels.json'), 'evidence/phase05-ui-agent-detail-tasks.png'],
     note='Agents 列表 → Agent Detail（/agents/<node_id>）→ 任务链接 → 任务详情，目标（node_ids）与 API 存储一致；'
          '节点维度的任务过滤与标签匹配语义一致（标签任务只出现在命中的 child-b 页）。')

# ---------------- FL-03 ----------------
case('FL-03', 'Agent Label → Label Group → Task Target → Label 改变后 Group/Task/Node Detail 同步', 'PASS', 'high',
     '/nodes/<child>（标签/分组/任务）；/groups 与 /groups/<label-group>；/tasks/<label-task>',
     {
         'node_b_detail': {'labels': (fl03.get('node_b_detail') or {}).get('labels'),
                           'task_links': (fl03.get('node_b_detail') or {}).get('task_rows')},
         'node_a_detail_after_label_change': {
             'labels': (fl03.get('node_a_detail') or {}).get('labels'),
             'task_links': (fl03.get('node_a_detail') or {}).get('task_rows')},
         'groups_page_rows': (fl03.get('groups_page') or {}).get('rows'),
         'label_group_detail_url': (fl03.get('group_detail') or {}).get('url'),
         'label_group_get': (labels.get('labels') or {}).get('label_group_get'),
         'task_target_detail': (fl03.get('task_detail') or {}).get('target_text'),
         'task_stored_target': (fl03.get('task_detail') or {}).get('stored_target'),
     },
     evidence=[E('phase05-labels.json'), 'evidence/phase05-ui-label-group-detail.png'],
     note='标签写入 → 标签组（key/value + 计算成员）→ 任务目标 → 标签值改变后的跨页收敛：'
          'child-b 详情显示 env=g5a 且列出该标签任务；child-a 标签改为 g5b 后不再列出该任务；'
          '任务详情的标签目标读回与库内一致（跨页数据同源）。')

# ---------------- FL-04 ----------------
case('FL-04', 'Script Create/Edit/Clone → Task Script → 副本独立、Revision/SHA、任务引用正确', 'PASS', 'high',
     '/scripts -> /scripts/<id> 编辑保存 -> /scripts 行菜单“克隆” -> /scripts/<copy> 编辑',
     {
         'script_initial': {'id': SCRIPT_ID, 'revision': (fl04.get('initial') or {}).get('get', {}).get('revision'),
                            'sha256': (fl04.get('initial') or {}).get('get', {}).get('sha256'),
                            'revision_list': [r.get('revision') for r in
                                              (fl04.get('initial') or {}).get('revisions', [])]},
         'script_after_ui_edit': {'revision': (fl04.get('after_edit') or {}).get('get', {}).get('revision'),
                                  'sha256': (fl04.get('after_edit') or {}).get('get', {}).get('sha256'),
                                  'revision_list': [r.get('revision') for r in
                                                    (fl04.get('after_edit') or {}).get('revisions', [])]},
         'scripts_list_row_with_revision_sha': (fl04.get('list_page') or {}).get('rows'),
         'clone_via_row_menu': {
             'menu_items': fl04.get('menu_items'),
             'click': fl04.get('clone_click'),
             'clone_script_id': CLONE_ID,
             'clone_get': {k: (fl04.get('clone_get') or {}).get(k)
                           for k in ('id', 'name', 'revision', 'sha256')},
         },
         'clone_edit_independence': fl04.get('independence'),
         'clone_after_edit': {k: (fl04.get('clone_after_edit') or {}).get(k)
                              for k in ('name', 'revision', 'sha256', 'content')},
         'original_after_clone_edit': {k: (fl04.get('orig_after_clone_edit') or {}).get(k)
                                       for k in ('name', 'revision', 'sha256', 'content')},
         'task_reference': {
             'task_id': (targets.get('manifest') or {}).get('task_ui_target'),
             'stored_script_id': (stale.get('stored_after') or {}).get('script_id'),
             'execution_script_id': (stale.get('exec') or {}).get('script_id'),
             'execution_script_revision': (stale.get('exec') or {}).get('script_revision'),
             'execution_stdout': (stale.get('exec') or {}).get('stdout'),
         },
     },
     unverified=['原始脚本→任务的引用在克隆前未单独截取一次 GET（克隆后读到的是同一 script_id，'
                 '并由执行记录 script_revision 交叉验证）；不影响“副本独立/引用正确”的结论。'],
     evidence=[E('phase05-labels.json'), E('phase05b-x09.json'), 'evidence/phase05-ui-script-clone.png'],
     note='脚本编辑后 revision 递增且 revisions 接口返回历史（含 sha256）；克隆经真实行菜单“克隆”完成，'
          '副本独立（编辑副本后原脚本 content/sha/revision 全部不变）；任务引用原脚本，'
          '执行记录携带 script_id 与当时 revision（r3）证明引用即原脚本。')

# ---------------- P14P15-09 ----------------
case('P14P15-09', 'Node Detail Managed Applications 回链。', 'PASS', 'high',
     '/nodes/<child-a> -> 托管应用区域 -> 点击应用链接 -> /applications/<id>',
     {
         'node_detail_url': backlink.get('node_url'),
         'managed_app_links_on_node_detail': backlink.get('app_links'),
         'link_click': backlink.get('link_click'),
         'url_after_click': backlink.get('url_after_click'),
         'application_detail_text_head': backlink.get('detail_text_head'),
         'owned_application': {'id': APP_ID, 'name': PREFIX + 'app'},
     },
     unverified=['“托管应用”小节容器文本未取到（DOM 上溯匹配返回 null，为 harness 采集方式限制，'
                 '非页面缺失）：链接存在、可点击并跳转到应用详情页均已取证。'],
     evidence=[E('phase03-apps.json'), 'evidence/phase03-node-managed-app-link.png'],
     note='节点详情页渲染自有应用链接（href=/applications/<id>），真实鼠标点击后落到应用详情页'
          '（页面标题“编辑托管应用”+ 配置说明），回链可用。')

# ---------------------------------------------------------------- deliverables
tally = {}
for c in CASES:
    tally[c['proposed_status']] = tally.get(c['proposed_status'], 0) + 1

case_result = {
    'batch_id': 'g5',
    'title': 'G5 自有夹具批：任务/脚本/分组/条件/标签/应用（14 条 PARTIAL 收口）',
    'verified_build': VERIFIED_BUILD,
    'verified_agent_binary_sha256': guard.get('verified_agent_sha256'),
    'endpoint': ENDPOINT,
    'owner': 'qa-g5 subagent (G5 authorization item, 2026-10-16)',
    'nonce': NONCE,
    'owned_prefix': PREFIX,
    'hosts_used': {'192.168.100.204': 'native-1 (only owned host; 2 owned child Agents)',
                   'protected_nodes_touched': 'read/select only, no state mutation (native-1..4 verified online/synced at guard and oracle)'},
    'browser': {'session': BROWSER_SESSION, 'own_session_only': True,
                'endpoint': ENDPOINT, 'sign_in_flow': 'real form login on /sign-in'},
    'generated_at_utc': now(),
    'run': {
        'baseline_counts': guard.get('baseline_counts'),
        'protected_baseline': guard.get('protected'),
        'child_nodes': fixtures.get('children'),
        'fixture_ids': {
            'script': SCRIPT_ID, 'script_clone': CLONE_ID,
            'group_static': GROUP_STATIC, 'group_label': GROUP_LABEL,
            'application': APP_ID, 'artifact': ARTIFACT_ID,
            'node_child_a': NODE_A, 'node_child_b': NODE_B,
        },
        'browser_login': {'sign_in_url': login.get('sign_in_url'),
                          'after_login_url': login.get('after_login_url'),
                          'login_request': login.get('login_requests')},
    },
    'cases': {c['case_id']: {k: v for k, v in c.items() if k != 'case_id'} for c in CASES},
    'case_order': [c['case_id'] for c in CASES],
    'summary': {
        'tally': tally,
        'per_case': {c['case_id']: {'status': c['proposed_status'],
                                    'confidence': c['confidence'],
                                    'remaining': c['unverified']} for c in CASES},
    },
    'new_observations': [
        {'id': 'OBS-20261016-G5-STALE-PAYLOAD-FIELDS-CARRIED',
         'severity': 'low (inert data; no dispatch impact proven by real execution)',
         'cases': ['P09P11-12', 'X09'],
         'detail': '任务编辑页在类型/目标类型切换后，除 app_operation 外的隐藏字段仍随 payload 提交并入库：'
                   'application_id、script_id、以及 target 的 group_ids/label_key/label_value 与当前 '
                   'target.type 对应的字段并存（浏览器 PUT postData 与保存后读回均有证据）。调度按 '
                   'type/target.type 取用，实测业务行为正确（脚本任务执行脚本、node 目标只投递 node_ids、'
                   '无应用部署副作用）。建议前端 buildTaskPayload 或服务端做类型相关字段归一化。'},
        {'id': 'OBS-20261016-G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR',
         'severity': 'low-medium (UI capability gap)',
         'cases': ['P09P11-17', 'X11'],
         'detail': '当保存下来的 condition.type=and 时，编辑页的本地/远程条件摘要行不渲染（摘要仅在 '
                   'condition.local/remote 存在时渲染），且“清除”按钮同样不渲染；因此 AND 结构在 UI 上'
                   '既无回显也无清除入口，只能被“设置条件”覆盖；clearCondition 的 downgrade 分支在 UI '
                   '路径不可达。AND 勾选但无任何条件时保存无 condition（该语义正常）。'},
    ],
    'unremovable_history': {
        'executions_on_owned_nodes': 16,
        'executions_breakdown': {'child-a SUCCESS': 12, 'child-a SKIPPED': 1, 'child-b SUCCESS': 3},
        'skipped_reason': 'condition not satisfied',
        'audit_rows_mentioning_prefix': 59,
        'audit_breakdown': {'delete': 55, 'create': 4,
                            'window': 'first 2000 rows returned by /api/audit; create/delete only'},
        'note': '执行与审计历史不可删除，按 G5 授权要求在报告中登记。',
    },
    'harness_errors': [
        {'phase': 'fixtures/login(initial)', 'error': 'run_g5.py load_state read the wrong JSON level and '
         'state.json was overwritten empty by the login phase',
         'resolution': 'fixed load_state; rebuilt state from evidence/phase00-guard.json + phase01-fixtures.json '
                       '+ Hub readback by owned prefix (documented, no fixture loss, no duplicate fixtures)'},
        {'phase': 'conditions(attempt 1)', 'error': 'UI save returned HTTP 400 because the command textarea '
         'was left empty (product validation "command task requires command")',
         'resolution': 'harness filled the command field; the 400 itself is product-correct validation and was '
                       'kept as negative-path evidence'},
        {'phase': 'labels(attempt 1)', 'error': 'same empty-command 400 for the label task; also a wait_execution '
         '"since" list was computed after run_now causing a 180s timeout',
         'resolution': 'fixed both; re-ran the phase; the extra execution from the failed attempt is included in '
                       'the unremovable history count'},
        {'phase': 'apps(attempt 1)', 'error': 'app_select combobox finder used a Radix data-placeholder attribute '
         'that the installed version renders as data-slot="select-value"',
         'resolution': 'finder switched to innerText matching; re-ran'},
    ],
    'artifacts': sorted(p.name for p in EVID.iterdir()) + ['run_g5.py'],
}

(BATCH / 'case-result.json').write_text(json.dumps(case_result, ensure_ascii=False, indent=1) + '\n')

# ---------------- progress.json ----------------
progress = {
    'batch_id': 'g5',
    'verified_build': VERIFIED_BUILD,
    'nonce': NONCE,
    'owned_prefix': PREFIX,
    'state': 'complete',
    'updated_at_utc': now(),
    'phases_done': ['guard', 'fixtures', 'login', 'probe', 'conditions', 'apps', 'targets',
                    'labels', 'x09exec', 'payload', 'cleanup', 'oracle'],
    'failed_phases': [],
    'cases_covered': [c['case_id'] for c in CASES],
    'summary': case_result['summary'],
    'cleanup': {
        'owned_objects_deleted_through_product_path': True,
        'all_deletes_get_404': cleanup.get('all_owned_deletes_get_404'),
        'owned_children_processes_stopped': True,
        'oracle_clean': oracle.get('host_clean') and oracle.get('owned_left_total') == 0,
        'protected_nodes_online_synced_after': oracle.get('protected_all_online_synced'),
    },
    'deliverables': ['case-result.json', 'progress.json', 'fixture-ledger.json',
                     'final-cleanup-oracle.json', 'evidence/'],
}
(BATCH / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=1) + '\n')

# ---------------- fixture-ledger.json ----------------
# authoritative: the session delete map (final cleanup + intra-run hygiene deletions)
fx = st.get('fixtures') or {}
created = []
for oid, d in (st.get('deletes') or {}).items():
    meta = fx.get(oid) or {}
    name = d.get('name') or meta.get('name')
    if not name or not str(name).startswith(PREFIX):
        # only owned objects belong in this ledger
        continue
    entry = {'kind': d.get('kind') or meta.get('kind'), 'id': oid, 'name': name,
             'label': d.get('label') or meta.get('label')}
    if meta.get('sha256'):
        entry['sha256'] = meta['sha256']
    if meta.get('size'):
        entry['size'] = meta['size']
    if entry['kind'] == 'node':
        entry['host'] = '192.168.100.204'
        entry['host_label'] = 'native-1'
        entry['created_via'] = ('POST /api/nodes/enrollment + owned Agent process '
                                '(no product unit touched)')
    elif entry['kind'] == 'artifact':
        entry['created_via'] = 'POST /api/artifacts (multipart, owned fixture payload)'
    elif entry['kind'] == 'node_label_write':
        pass
    entry['delete_http'] = d.get('delete_http')
    entry['get_after_delete_http'] = d.get('get_http')
    entry['evidence'] = ['evidence/phase06-cleanup.json']
    created.append(entry)

ledger = {
    'batch_id': 'g5',
    'verified_build': VERIFIED_BUILD,
    'nonce': NONCE,
    'owned_prefix': PREFIX,
    'endpoint': ENDPOINT,
    'hosts_touched': {
        '192.168.100.204': 'native-1 (only owned host; protected Agent unit untouched)',
        'owned_paths': ['/var/lib/nodesteer-qa-g5-<nonce>[-b]',
                        '/opt/qa-g5-<nonce>[-b]',
                        '/opt/qa-g5-<nonce>/app (application unit/binary/config)'],
        'product_path_cleanup': 'application delete -> Agent CleanupApplication (unit/binary/config/registry)',
    },
    'ownership_rule': 'every created object name starts with ' + PREFIX +
                      '; node labels were written only on the two owned child nodes',
    'created_total': len(created),
    'created': sorted(created, key=lambda x: (x['kind'] or '', x['name'] or '')),
    'rerun_cleanup': {
        'conditions_tasks_deleted_between_runs': True,
        'label_group_and_task_recreated_once': True,
        'script_clone_recreated_once': True,
        'note': 'harness re-run hygiene; deletions recorded in state["deletes"] and in evidence/phase06-cleanup.json',
    },
    'unremovable_history': case_result['unremovable_history'],
    'label_writes_on_owned_nodes': {
        'rule': '节点标签只写入 2 个自有 child 节点（protected 节点从未写入）',
        'writes': [
            {'target': 'owned child-a', 'labels': {st.get('label_key', PREFIX + 'env'): 'g5a'},
             'http': (labels.get('labels') or {}).get('set_a'),
             'evidence': ['evidence/phase05-labels.json']},
            {'target': 'owned child-b', 'labels': {'g5role': 'g5b'},
             'http': (labels.get('labels') or {}).get('set_b'),
             'evidence': ['evidence/phase05-labels.json']},
            {'target': 'owned child-b', 'labels': {PREFIX + 'env': 'g5a', 'g5role': 'g5b'},
             'http': lt.get('set_b_same_label'),
             'evidence': ['evidence/phase05-labels.json']},
            {'target': 'owned child-a', 'labels': {PREFIX + 'env': 'g5b'},
             'http': lt.get('set_a_new_value'),
             'evidence': ['evidence/phase05-labels.json']},
        ],
        'final_state': '节点删除后标签随节点消失（GET /api/nodes/{id} -> 404）',
    },
    'cleanup_summary': {
        'tasks': len(cleanup.get('steps', {}).get('task_deletes', [])),
        'groups': len(cleanup.get('steps', {}).get('group_deletes', [])),
        'scripts': len(cleanup.get('steps', {}).get('script_deletes', [])),
        'applications': 1,
        'artifacts': 1,
        'nodes': 2,
        'all_get_404': cleanup.get('all_owned_deletes_get_404'),
    },
}
(BATCH / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + '\n')

# ---------------- final-cleanup-oracle.json ----------------
cleanup_steps = cleanup.get('steps', {})
fco = {
    'batch_id': 'g5',
    'verified_build': VERIFIED_BUILD,
    'nonce': NONCE,
    'at_utc': oracle.get('at_utc'),
    'oracle_type': 'owned-prefix residue + protected-node health + owned-host residue + repair endpoint health',
    'owned_objects': {
        'deleted_total': len(st.get('deletes') or {}),
        'all_deleted_get_404': cleanup.get('all_owned_deletes_get_404'),
        'final_owned_residue_by_endpoint': {k: {'count': v['count'], 'owned_left': len(v['owned_left'])}
                                            for k, v in (oracle.get('final_counts') or {}).items()},
        'owned_left_total': oracle.get('owned_left_total'),
        'counts_match_baseline_snapshot': oracle.get('counts_match_baseline'),
        'note': 'baseline snapshot is affected by concurrent batches on the shared authority (e.g. qa-g23-*); '
                'the pass criterion used is owned-prefix residue = 0, not global counts',
    },
    'host_residue': {
        'host_after': oracle.get('host_after'),
        'host_clean': oracle.get('host_clean'),
        'application_unit_after_product_path_delete': cleanup_steps.get('application_delete', {})
        if isinstance(cleanup_steps.get('application_delete'), dict) else None,
        'app_unit_final_probe': oracle.get('app_unit_after_cleanup'),
        'children_removal': cleanup_steps.get('owned_children_removal'),
    },
    'protected_nodes': {
        'baseline': guard.get('protected'),
        'after': oracle.get('protected_after'),
        'all_online_synced_after': oracle.get('protected_all_online_synced'),
    },
    'repair_authority': {'healthz_http': oracle.get('repair_healthz'),
                         'untouched': oracle.get('repair_endpoint_untouched'),
                         'primary_8080_never_used': True},
    'verdict': {
        'owned_residue_zero': oracle.get('owned_left_total') == 0,
        'host_clean': bool(oracle.get('host_clean')),
        'protected_nodes_healthy': bool(oracle.get('protected_all_online_synced')),
        'all_deletes_verified_404': bool(cleanup.get('all_owned_deletes_get_404')),
        'result': 'CLEAN' if (oracle.get('owned_left_total') == 0 and oracle.get('host_clean')
                              and oracle.get('protected_all_online_synced')
                              and cleanup.get('all_owned_deletes_get_404')) else 'NEEDS_REVIEW',
    },
    'browser_session': {'session': BROWSER_SESSION,
                        'closed_own_session_only': True},
}
(BATCH / 'final-cleanup-oracle.json').write_text(json.dumps(fco, ensure_ascii=False, indent=1) + '\n')

print(json.dumps({'tally': tally, 'cases': len(CASES),
                  'oracle_verdict': fco['verdict']['result']}, ensure_ascii=False))
