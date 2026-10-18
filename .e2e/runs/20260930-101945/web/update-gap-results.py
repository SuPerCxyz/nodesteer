"""将本轮已执行的逐维证据合并到Web子清单；不写全局记录。"""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[3]


def read(name):
    return json.loads((ROOT / name).read_text())


def write(name, data):
    target = ROOT / name
    temporary = target.with_suffix(target.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(target)


inventory = read('case-inventory.json')
cases = {case['case_id']: case for case in inventory['cases']}


def result(ids, status, name, covered, remaining, actual, classification='REAL_UI'):
    evidence = '.e2e/runs/20260930-101945/web/evidence/' + name + '.json'
    assert (REPO / evidence).exists(), evidence
    for case_id in ids.split():
        case = cases[case_id]
        old = case.get('covered_dimensions') or []
        old = [old] if isinstance(old, str) else old
        case.update(status=status, classification=classification, actual=actual,
                    covered_dimensions=list(dict.fromkeys(old + covered)), remaining_dimensions=remaining)
        case['evidence'] = list(dict.fromkeys(case.get('evidence', []) + [evidence]))


result('G04-01 X08', 'FAIL', 'task-pending-secret',
       ['Task real PUT pending Save remains enabled and two clicks issue two requests'],
       ['Task pending Save defect; final revision=2 does not prove two server mutations', 'X08 all other dialog branches remain individually documented'],
       '独占Task真实PUT延迟期间Save仍启用，点击2次发2PUT，最终revision2；参数移除实际保存为空。', 'REAL_UI_DELAY')
result('P09P11-14', 'FAIL', 'task-pending-secret',
       ['Task secret default summary exposed', 'parameter remove and saved empty array'],
       ['secret default visibility defect'],
       'Task secret摘要同样明文，随机测试secret仅private保存/公开结果脱敏；扩展WEB-SECRET-DEFAULT影响范围。')
result('P05A-03 P05A-07 P05A-11', 'PASS', 'enrollment-retry-pending',
       ['128-char prefixed name accepted', 'real bad-name 400 Retry repeats exact payload', 'pending disabled/skeleton/one actual create'], [],
       '实际400→Retry相同原参数400，编辑合法128名与HTTPS Hub后真实POST一次/pending禁用、Skeleton1、节点pending；精确清理。', 'REAL_UI_DELAY')
result('P05A-05', 'PARTIAL', 'enrollment-retry-pending', ['HTTPS Hub accepted'], ['missing Host native validity UI edge'],
       '历史http/ftp错误及本轮https正确路径有真证据；缺Host单项未断言。')
result('P09P11-12 X09', 'PASS', 'task-four-types-roundtrip',
       ['same Task four persisted type roundtrips', 'effective operation normalized', 'command/script actual run ignores unrelated retained fields', 'group/direct label final persisted actual run'], [],
       '同Task script/app_deploy/app_operation/command四次保存回显；仅app_operation裁剪，script/application隐藏ID可保留但按type忽略；script/command实跑，应用5实际操作引用既有FULL09，不新增裁全部字段契约。')
result('P09P11-10 P09P11-11 P06-08', 'PASS', 'multi-group-direct-label',
       ['overlapping two Groups deduplicate to one execution', 'deselect Group persisted', 'direct label matches Agent4 and unmatched produces 400/no new execution', 'Node Detail Task link updates for Group/Label'], [],
       '真实2重叠Group→Node1仅1Execution、去选保存；直接Label Task→Agent4成功，标签不匹配400且无额外执行；Task在Node Detail出现。')
result('P09P11-22', 'PARTIAL', 'task-missing-fields',
       ['UI empty name client reject', 'command/script/application/operation/target missing real 400 feedback'],
       ['invalid ID injection API evidence belongs to Root; UI selectors do not accept nonexistent IDs'],
       '空名称0请求，其余5缺字段经真实UI提交Hub400且不创建；未冒充任意ID输入可通过UI完成。')
result('P09P11-13', 'PASS', 'task-runuser-policy-uuid',
       ['Task RunUser root actually consumed', 'Enabled=false run400 no execution', 'both OfflinePolicy variants saved'], [],
       'Task run_user=root实际id-un输出root；关闭启用运行400无记录；允许离线与需Hub在线保存，重新启用后两次成功。')
result('P11-08', 'PARTIAL', 'task-runuser-policy-uuid',
       ['two real Manual Run requests generate distinct fresh UUIDs'],
       ['same Execution ID delivery/upload idempotence is backend/system evidence'],
       '两次ManualRun不同UUID与实际root输出已验证；不将新意图测试冒充同ID重复投递幂等。')
result('P16-05 P16-06', 'PASS', 'artifact-boundaries-reference-delete',
       ['actual payload exact download', 'local size/SHA equality', 'Content-Length', 'X-Artifact-SHA256'], [],
       '真实上传/下载字节一致，Hub size/SHA与本地相等、Content-Length与X-Artifact-SHA256正确。')
result('P16-04', 'PARTIAL', 'artifact-large-pending',
       ['actual 8MiB multipart upload', 'pending upload disabled and one request', 'size/SHA correct'],
       ['current API has no maximum size contract; unbounded enormous-file stress not executed'],
       '同名同版本不同ID与空文件当前允许，8MiB真实上传/pending禁用/SHA与列表大小通过；无配置最大值，不虚构超限拒绝契约。', 'REAL_UI_DELAY')
result('P16-08', 'PARTIAL', 'artifact-boundaries-reference-delete',
       ['referenced Artifact cancel0DELETE', 'confirm1DELETE409 concrete reference error and retained object'],
       ['unreferenced Artifact UI delete-success branch (API cleanup already completed)'],
       '应用引用制品取消0DELETE、确认1DELETE409，制品及应用保留且UI有行；无引用清理由exact API执行，未冒充UI分支。')
result('P19-04', 'FAIL', 'audit-cross-page-search',
       ['actual offset10 row hit', 'first/previous/next page boundaries', 'Reset restores full current page', 'known other-page ID misses offset0 search'],
       ['server-page-local audit search defect'],
       'Audit同资源ID在服务端第2页可搜，第1页全局搜索显示无结果；扩展同DataTable的WEB-EXEC-SEARCH-PAGE，不增独立bug。')
result('G03-07', 'PASS', 'audit-cross-page-search', ['next/previous and first-page boundary real Audit server pagination'], [],
       '历史11表首末/单多页边界+本轮Audit上一页/下一页/首页禁用边界实际验证。')
result('X20', 'PARTIAL', 'all-tables-all-columns',
       ['all eleven tables sixty-six hideable columns all hidden, reload-persisted, baseline restored'],
       ['long Chinese/path/ID/command/parameters/log cases and narrow form/menu/dialog layout'],
       '11真实表共66可隐藏列逐项全隐藏、刷新保持、恢复baseline并再次刷新；长文本及窄屏完整矩阵继续保留。')
result('X06', 'PARTIAL', 'inline-artifact-upload',
       ['inline cancel zero upload', 'native required invalid zero upload preserves v1', 'actual successful inline upload auto-picks new artifact/v2 and App saved'],
       ['server-side upload failure does not overwrite draft; native required is not that branch'],
       '真实内嵌上传取消/必填零写，成功自动选newArtifact/v2，App实际保存正确；server-error分支仍区分。')
result('P19-02', 'PASS', 'crud-login-audit',
       ['real login Audit', 'Script/Task/Schedule/Application create/update/delete Audit', 'Artifact upload/delete Audit', 'each exact object displayed via real Audit UI'], [],
       '真实Login+5类对象CUD产生审计，按exact ID核对每事件与UI行数；Artifact无元数据编辑接口，update不适用。', 'REAL_API_CUD_AND_REAL_UI')
result('FL-06 P12P13-11', 'PASS', 'task-schedule-continuity',
       ['Schedule Task preset', 'Task Detail/Schedule List/Detail consistent exact ID', 'delete cancel0 confirm1', 'deleted Schedule absent from both pages'], [],
       '真实Task→Schedule创建、独立列表与Task详情回链/表达式一致；取消0DELETE、确认1DELETE后两页均消失，GET404。')
result('P11-04', 'PARTIAL', 'offline-node-condition-blocked',
       ['own enrolled node Hub status manually offline produces real UI400/no executions/button reenabled'],
       ['actual physical Agent offline UI Run window is queued with Root; Hub fixture status is not physical daemon offline'],
       '独占pending身份经Hub设offline后Run即400且无执行；没有把此fixture冒充物理offline。')
result('P17P18-13', 'FAIL', 'offline-node-condition-blocked',
       ['actual never-run remote condition UNKNOWN produces Agent BLOCKED', 'API actual block_reason recorded', 'UI BLOCKED lacks actual reason'],
       ['BLOCKED reason UI missing defect'],
       '真实Node1 remote last_execution查无历史→BLOCKED；API reason condition unknown: <nil>，UI仅已阻断而无reason。')
result('X11', 'PARTIAL', 'offline-node-condition-blocked',
       ['actual local false SKIPPED no command', 'actual remote UNKNOWN BLOCKED no command'],
       ['AND unchecked/unset/clear-last full execution matrix still open', 'BLOCKED UI reason missing'],
       '本轮Agent实际Local false→SKIPPED、Remote UNKNOWN→BLOCKED，均无命令marker；不把Local false错误期待为BLOCKED。')
result('P11-04', 'PASS', 'physical-offline-task-run',
       ['Root verified real Node2 service inactive/PID0/statusoffline', 'actual UI Run400 immediately no Execution', 'button reenabled', 'Root restored online/synced/version'], [],
       'Root独立控制Node2真实物理offline窗口，Web实际Run400、无Execution、按钮恢复；finallyNode2online/synced/dddee4f0。首次flag未到门禁超时仅harness，不计产品失败。', 'REAL_UI_PHYSICAL_OFFLINE')
result('FL-01 FL-02 P03-01 P03-04 P03-06 P03-07 P03-08', 'PASS', 'dashboard-continuous-metrics-links',
       ['actual UI Login→Dashboard→Agents ViewAll→NodeDetail→TaskDetail', 'all four actual response metrics including success rate', 'App health aggregate/sync counts', 'all displayed actual App/Execution/Schedule detail links'], [],
       '真实UI登录→Dashboard指标/API一致→Agents ViewAll→Node1详情→Task目标一致；实际limit50成功率、0/1App健康、同步counts与10详情链接均验证。')

for case in inventory['cases']:
    if case['case_id'].startswith('TABLE-') and case['case_id'].endswith('-COLUMNS'):
        result(case['case_id'], 'PASS', 'all-tables-all-columns', ['all hideable columns hidden/restored with reload'], [], '真实11表全66列隐藏/恢复，保留原搜索/分页证据。')

summary = collections.Counter(case['status'] for case in inventory['cases'])
inventory['summary'].update(statuses=dict(summary), passed=summary['PASS'], failed=summary['FAIL'], partial=summary['PARTIAL'], not_run=summary['NOT_RUN'])
write('case-inventory.json', inventory)
remaining = collections.defaultdict(list)
for case in inventory['cases']:
    if case['owner'] == 'web' and case['status'] in ('PARTIAL', 'NOT_RUN', 'BLOCKED'):
        module = case['case_id'].split('-')[0]
        remaining[module].append({key: case.get(key) for key in ['case_id', 'status', 'title', 'actual', 'covered_dimensions', 'remaining_dimensions', 'evidence']})
write('remaining-web.json', {'run_id': inventory['run_id'], 'remaining_by_module': dict(remaining)})

failures = read('failures.json')
for failure in failures['failures']:
    if failure['id'] == 'WEB-SECRET-DEFAULT':
        failure.update(title='Script/Task Editor secret参数默认值摘要明文展示', task_extension='Task独立摘要直接渲染item.default；同类保密问题扩展影响，不重复计bug')
        failure['evidence'] = list(dict.fromkeys(failure['evidence'] + ['evidence/task-pending-secret.json', 'gap-core-results.json']))
    if failure['id'] == 'WEB-EXEC-SEARCH-PAGE':
        failure.update(title='Execution/Audit搜索仅过滤当前服务端页', audit_extension='同DataTable本地搜索根因；Audit已用实际offset0/10独立验证')
        failure['evidence'] = list(dict.fromkeys(failure['evidence'] + ['evidence/audit-cross-page-search.json', 'gap-audit-tables-verified-results.json']))
    if failure['id'] == 'WEB-ROLE-LIVE-STATE':
        failure['severity_priority_explanation'] = 'severity=P3描述短暂UI状态滞后（服务端即时403保护有效）；priority=P2反映容易误导有权限变化的用户，应优先修复。'
if not any(failure['id'] == 'WEB-TASK-PENDING-SAVE' for failure in failures['failures']):
    failures['failures'].append({'id': 'WEB-TASK-PENDING-SAVE', 'severity': 'P2', 'priority': 'P2', 'status': 'OPEN',
        'title': 'Task保存pending期间按钮未禁用，重复点击发重复PUT',
        'steps': ['编辑本轮独占Task', '延迟真实PUT发送但不修改请求/响应', 'Save点击一次仍enabled，再点一次', '释放后核对Hub实际定义'],
        'expected': 'pending保存禁用，重复点击不生成第二请求',
        'actual': 'Save enabled，2次点击产生2个PUT；最终Revision2，只证明重复请求，不能证明两次服务端mutation',
        'source': 'web/src/features/tasks/index.tsx:631', 'case_ids': ['G04-01', 'X08'],
        'evidence': ['evidence/task-pending-secret.json', 'gap-core-results.json']})
if not any(failure['id'] == 'WEB-BLOCK-REASON' for failure in failures['failures']):
    failures['failures'].append({'id': 'WEB-BLOCK-REASON', 'severity': 'P2', 'priority': 'P2', 'status': 'OPEN',
        'title': '真实BLOCKED执行详情未显示Hub保存的block_reason',
        'steps': ['新建Node1 neverRun Task使last_execution无历史', '另一个本轮Task引用该真实remote condition', '真实Run等待Agent返回BLOCKED', '打开Execution详情并刷新'],
        'expected': 'BLOCKED详情显示实际block_reason，用户可理解为何没有执行',
        'actual': 'Hub记录statusBLOCKED/block_reason=condition unknown: <nil>；UI仅已阻断、stdout/stderr空，没有原因',
        'source': 'web/src/features/executions/index.tsx:511', 'case_ids': ['P17P18-13'],
        'evidence': ['evidence/offline-node-condition-blocked.json', 'gap-block-reason-real-results.json']})
write('failures.json', failures)

data = read('test-data.json')
resources = {item.get('resource_path') or item['path'] + ('/' + item['id'] if item.get('id') and not item['path'].endswith(item['id']) else ''): item for item in data['resources']}
for line in (ROOT / 'playwright-resources.jsonl').read_text().splitlines():
    item = json.loads(line)
    key = item['path'] + ('/' + item['id'] if item.get('id') and not item['path'].endswith(item['id']) else '')
    if item['status'] == 'deleted':
        if key in resources: resources[key]['status'] = 'deleted'
    elif key not in resources:
        resources[key] = {**item, 'resource_path': key}
for obj in read('evidence/crud-login-audit.json')['objects']:
    for entry in obj['entries']:
        key = '/api/audit/' + entry['id']
        resources[key] = {'resource_path': key, 'path': '/api/audit', 'id': entry['id'], 'status': 'retained_history', 'resource': obj['resource'], 'resource_id': obj['id'], 'action': entry['action'], 'retention': '不可删除审计API；仅记录实际exact own-object事件，不宣称全部DB历史'}
data['resources'] = list(resources.values())
data['summary'] = dict(collections.Counter(item['status'] for item in data['resources']))
write('test-data.json', data)

index = read('safe-run-index.json')
existing = {item['label'] for item in index['latest_real_rounds']}
for label in ['gap-core', 'gap-core-verified', 'gap-roundtrip-tables', 'gap-audit-tables-verified', 'gap-upload-policy', 'gap-upload-policy-verified', 'gap-task-policy-final', 'gap-blocked-schedule-audit', 'gap-blocked-schedule-verified', 'gap-condition-blocked-final', 'gap-block-reason-real', 'gap-physical-offline', 'gap-physical-offline-verified', 'gap-mock-dashboard-viewer']:
    if label not in existing:
        index['latest_real_rounds'].append({'label': label, 'result': '参照真实report；harness失败已由后续同项纠正，产品失败保留', 'report': '.e2e/runs/20260930-101945/web/' + label + '-results.json'})
write('safe-run-index.json', index)
print(json.dumps({'web_statuses': dict(collections.Counter(c['status'] for c in inventory['cases'] if c['owner'] == 'web')), 'data_statuses': data['summary'], 'web_defects': len(failures['failures'])}, ensure_ascii=False))
