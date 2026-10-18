"""合并本轮各执行单元的证据，不把历史结果或未测场景计为通过。"""
import collections
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent
PROJECT = RUN.parents[2]
GLOBAL = PROJECT / '.e2e'
RUN_ID = RUN.name


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, data):
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def canonical(ref, owner=None):
    p = Path(ref)
    if p.is_absolute():
        return str(p)
    if str(p).startswith('.e2e/'):
        return str(PROJECT / p)
    if str(p).startswith(('main/', 'web/', 'backend/', 'environment/')):
        return str(RUN / p)
    return str((RUN / owner / p).resolve()) if owner else str(RUN / p)


def map_case(cases, key, status, evidence, actual, remaining=''):
    row = cases[key]
    row.update(status=status, evidence=[canonical(x) for x in evidence], actual=actual, remaining_dimensions=remaining)
    row['classification'] = 'REAL_API_AGENT' if row['owner'] == 'system' else 'REAL_API'


inventory = copy.deepcopy(read(RUN / 'web/case-inventory.json'))
cases = {c['case_id']: c for c in inventory['cases']}
mapping = read(RUN / 'backend/web-system-mapping.json', {'cases': []})
for row in mapping['cases']:
    # 未执行本身不是环境阻塞。保留真实 DST 环境限制，其余未执行不伪造阻塞。
    status = row['status']
    if status == 'BLOCKED' and row['case_id'] != 'SYS-24':
        status = 'NOT_RUN'
    map_case(cases, row['case_id'], status, row.get('evidence', []), row.get('actual', ''), row.get('remaining', ''))

system_gaps = read(ROOT / 'remaining-system-dimensions.json', {'cases': []})
for audited in system_gaps['cases']:
    row = cases[audited['case_id']]
    if row['status'] == 'PARTIAL' and audited.get('additional_covered'):
        row['evidence'] += [canonical(x) for x in audited['evidence'] if canonical(x) not in row['evidence']]
        row['actual'] += '；补充现证核对：' + audited['additional_covered']
        row['remaining_dimensions'] = audited['remaining_dimensions']

map_case(cases, 'AUTH-PUBLIC', 'PASS', ['main/api-results.json', 'main/sso-results.json', 'environment/arm64-binary-endpoint.json'],
         'healthz/readyz/OIDC state与登录/无效state回调、Agent GET/HEAD下载按端点真实公开契约检查；不是统一401。')
expiry = read(RUN / 'environment/token-time-expiry.json')
expired_ok = bool(expiry and str(expiry.get('status', '')).lower() in ('passed', 'pass'))
map_case(cases, 'AUTH-MANAGEMENT', 'PASS' if expired_ok else 'PARTIAL',
         ['main/api-results.json', 'main/api-edge-results.json'] + (['environment/token-time-expiry.json'] if expiry else []),
         '无凭据/非法bearer/真实登出撤销的会话逐端点401；三角色读取/定义写/Users等403；旧token升降权实时生效。',
         '' if expired_ok else '真实时间TTL到期401由环境单元补测；登出撤销不能替代时间到期。')
map_case(cases, 'AUTH-USERS', 'PASS', ['main/api-results.json'], 'Users GET/创建/更新仅administrator；operator/viewer真实403；角色变更作用于既有会话。')
map_case(cases, 'AUTH-ARTIFACT', 'PASS', ['main/api-edge-results.json'], 'user两角色、registration token和归属Agent credential下载真实bytes/SHA；无认证/非法Agent凭据401。')
map_case(cases, 'AUTH-RELAY', 'PASS', ['main/api-edge-results.json', 'main/relay-edge-results.json'],
         '用户/registration token无权访问Relay；合法Agent缺失传输404；错源上传403；已暂存文件仅目标下载200，源与第三Agent403；未暂存全部404。')
map_case(cases, 'API-NOT-FOUND', 'PASS', ['main/api-contract-results.json', 'main/api-edge-results.json'],
         '九类对象GET缺失404；Node缺失DELETE404，Group/Script/Task/Schedule/Application缺失DELETE200幂等；User单对象GET405；Revision/Logs/State子资源集合空200。')
user_validation = read(ROOT / 'user-validation-results.json', [])
user_validation_failed = any(row.get('status') == 'FAIL' for row in user_validation)
map_case(cases, 'API-CONTRACT', 'FAIL' if user_validation_failed else 'PARTIAL', ['main/api-contract-results.json', 'main/api-edge-results.json', 'main/api-results.json', 'backend/api-contract-matrix.json'] + (['main/user-validation-results.json'] if user_validation else []),
         '实际覆盖50条读取/方法/错误JSON/空子集合契约，加8类POST坏JSON、缺节点ID、空用户更新和业务成功/角色契约；这些坏JSON请求未写入定义。',
         '尚未穷举每个端点的所有必填字段组合及各成功响应字段；按场景关联业务证据继续补。')
if user_validation_failed:
    cases['API-CONTRACT']['actual'] += '；新增真实User边界：空密码创建/login/me200、非法role200且持久化，2独立缺陷；仅专属QA账号已恢复安全Viewer。'
duplicate_id = sorted((RUN / 'backend/isolated/evidence').glob('feature.execution.duplicate-id-*.json'))
map_case(cases, 'IDEMPOTENT-MANUAL', 'PASS' if duplicate_id else 'PARTIAL', ['web/dynamic-targets-results.json', 'main/relay-edge-results.json'] + [str(x) for x in duplicate_id[-1:]],
         '独立Manual意图真实新UUID；Relay同Transfer重复上传原SHA不变。相同RUN_EXECUTION投递/结果ACK证据由Backend关联。',
         '' if duplicate_id else '需要关联Backend同Execution ID重复投递的实测结果，Relay幂等不替代Execution幂等。')
map_case(cases, 'SYS-59', 'PASS', ['main/api-edge-results.json', 'main/relay-edge-results.json'], '源Agent专属credential认证，错源上传403；用户/registration拒绝；源不能冒充目标下载。')
map_case(cases, 'SYS-60', 'PASS', ['main/relay-edge-results.json'], '完整暂存后指定目标200真实bytes；同一源和第三真实Agent403；目标不能冒充源上传。')
if cases['SYS-18']['status'] == 'PASS':
    cases['SYS-18'].update(status='PARTIAL', remaining_dimensions='心跳配置与Revision收敛已实测；重连退避与Jitter完整分布尚未独立采样。')
jitter = read(RUN / 'environment/jitter/sys18-jitter.json', {})
if jitter.get('sampling_status') == 'PASS':
    cases['SYS-18']['evidence'] += [canonical('environment/jitter/sys18-jitter.json'), canonical('environment/jitter/reconnect-syn.txt'), canonical('main/jitter-independent-check.json')]
    cases['SYS-18']['actual'] += '；Luna在Native2另起独占空数据/dummy进程，仅连接无监听localhost18997，57唯一SYN/56间隔，2.085～3.992秒、p50=3.351/p95=3.973、19个0.1秒分箱、capture零丢包；Root独立重算一致。原Native2 PID/SHA/身份与在线同步均未变，自己的进程/目录已清。'
    cases['SYS-18']['remaining_dimensions'] = '重连delay/jitter分布已实测；心跳热更新与丢通知后Revision收敛有既有证据，但Revision周期jitter/改值周期消费的独立采样仍缺。本次dummy未连接Hub，不冒称本采样完成HELLO。'
bulk = read(ROOT / 'bulk-upgrade-results.json', [])
if bulk and all(row['status'] == 'PASS' for row in bulk):
    cases['X01']['evidence'].append(canonical('main/bulk-upgrade-results.json'))
    cases['X01']['actual'] += '；Root真实API补齐混合Native/Docker、maintenance/disabled、重复在途、offline部分失败、输入缺失整体拒绝、Version/ID/sync与精确清理8/8。UI单点确认/取消已有证据，批量UI完整确认反馈仍按实际子维度保留。'
    cases['X01']['remaining_dimensions'] = 'API组合已完成；批量UI完整确认/结果反馈等未实测组合不以API代替。'
outbound = read(ROOT / 'agent-outbound-sockets.json', {})
if outbound.get('status') == 'PASS':
    cases['INV-02']['evidence'].append(canonical('main/agent-outbound-sockets.json'))
    cases['INV-02']['actual'] += '；4Native实际Agent进程Socket均主动连接Hub8080，Agent无TCP listener。'
role_ui = read(RUN / 'environment/role-ui-results.json', {})
if role_ui.get('status') == 'passed':
    for key, actual in [
        ('P04-07', 'Administrator真实纳管UI已验证；Operator/Viewer实际Agents页面无添加节点控件，直接写入口403，后端admin-only策略一致。'),
        ('P20-07', '两个独立角色真实UI登录后访问Users显示管理员限制、无创建/角色修改控件；真实GET Users403，未认证401。'),
        ('P21-08', '两个角色真实读取Runtime，输入disabled且Save隐藏；main真实PUT Settings403，读取/API拒绝与UI权限策略一致。'),
    ]:
        map_case(cases, key, 'PASS', ['environment/role-ui-results.json', 'main/api-results.json'], actual)
        cases[key]['classification'] = 'REAL_UI_API'
password_scope = read(ROOT / 'password-session-scope-result.json', {})
if password_scope.get('status') == 'PASS':
    map_case(cases, 'P20-08', 'PASS', ['main/password-session-scope-result.json'], '专属QA Viewer密码修改后旧密码401、新密码200；旧签发会话me仍200为当前实际策略。最终恢复原随机密码Viewer、临时密码401、相关会话登出。')
oidc_negative = read(RUN / 'environment/oidc-negative-results.json', {})
if oidc_negative.get('status') == 'passed':
    map_case(cases, 'P01-09', 'PASS', ['environment/oidc-negative-results.json', 'main/sso-results.json'], 'SSO-only enabled=true/local_fallback=false实际正确本地密码403；allow_local_login=true互斥本地模式enabled=false/local_fallback=true、bootstrap login/me200，OIDC login/callback404；配置SHA精确恢复。')
    map_case(cases, 'P01-10', 'PASS', ['environment/oidc-negative-results.json'], '缺state/code、错误state、fresh valid state错误code和已消费state重复回调均302同源/sign-in失败，无Cookie/Token script、匿名me401。重放是失败兑换已消费state，不泛指成功授权码重放。')
settings_ui = read(ROOT / 'settings-ui-results.json', [])
settings_persistence = read(ROOT / 'settings-persistence-result.json', {})
if settings_ui and all(row['status'] == 'PASS' for row in settings_ui):
    map_case(cases, 'P21-03', 'PASS', ['main/settings-ui-results.json','main/settings-pending-retest.json','main/settings-runtime-evidence.json'], '真实UI保存显示已保存；16坏数字均具体Error，原值不变；重复保存正确；1.5s真实请求延迟时按钮disabled、第二物理点击不再PUT。首次dblclick未证在途重入的观察已澄清，未列产品缺陷。')
    map_case(cases, 'P21-04', 'PASS', ['main/settings-ui-results.json','main/api-results.json','main/api_rbac.py'], '当前四Runtime字段text/0/-1/超大数全部UI提交400并显示具体反馈，全部原值不变；Gateway没有存储键因而当前无UI输入，真实API file URL400且未新增键，unknown key亦400。')
    for key in ['P21-03','P21-04']:cases[key]['classification']='REAL_UI_API'
if settings_persistence.get('status') == 'PASS':
    map_case(cases, 'P21-05', 'PASS', ['main/settings-persistence-result.json'], 'UI将心跳30改31保存200/成功提示，页面reload与API都31；再用UI恢复30，reload/API均原4字段，finally基线保护，不新增Gateway。')
    cases['P21-05']['classification']='REAL_UI_API'
if read(ROOT / 'settings-runtime-evidence.json', {}).get('status') == 'PASS':
    map_case(cases, 'P21-06', 'PASS', ['main/settings-runtime-evidence.json'], '真实UI心跳30→3，请求200；4Agent实际心跳间隔3～4秒，与1秒loop+jitter相符；共享设置已恢复原值。')
    cases['P21-06']['classification']='REAL_UI_API_AGENT'
buttons = read(ROOT / 'button-accessibility-results.json', {})
if buttons.get('status') == 'PASS' and cases['G04-05']['status'] in ['NOT_RUN','PARTIAL']:
    source = copy.deepcopy(cases['G04-05'])
    map_case(cases, 'G04-05', 'PARTIAL', source.get('evidence', []) + ['main/button-accessibility-results.json'],
             '19基础列表/新建页Chromium真实可访问树全部button有非空名称；只读GUI，未造业务对象。；Web新增实证：' + (source.get('actual') or ''),
             source.get('remaining_dimensions') or '详情、动态弹窗及hover菜单按钮还需按实际打开状态补验。')
    cases['G04-05']['classification']='REAL_UI_READONLY'
detail_buttons = read(RUN / 'backend/detail-accessibility.json', {})
if detail_buttons and cases['G04-05']['status'] == 'PARTIAL':
    cases['G04-05']['evidence'].append(canonical('backend/detail-accessibility.json'))
    cases['G04-05']['actual'] += '；追加4Native Node Detail、既有Task Detail/Run确认开取消、Script Detail、真实历史Execution详情、Profile菜单等10状态共62可访问按钮名称均存在，零业务写。'
    cases['G04-05']['actual'] += '旧只读检查当时没有App/Group/Schedule与参数行，不覆盖Web之后新增fixture的实证；剩余按Web最新维度保留。'
self_role = read(RUN / 'backend/admin-self-role-ui.json', {})
if self_role and cases['P20-06']['status'] in ['NOT_RUN','PARTIAL']:
    map_case(cases, 'P20-06', 'PARTIAL', ['backend/admin-self-role-ui.json'], '真实Admin me/Users身份匹配，自身Row Role combobox显示管理员但disabled=false、无锁定提示；未点击/提交/降权原真实Admin，零账号写。', '由独占新QA Admin实际UI自降权及旧会话权限范围仍待测试；不会操作真实原Admin。')
    cases['P20-06']['classification']='REAL_UI_READONLY'
login_pending = read(ROOT / 'login-pending-result.json', {})
if login_pending.get('status') == 'PASS':
    map_case(cases, 'P01-04', 'PASS', ['main/login-pending-result.json'], '独立真实UI匿名登录，在实际POST网络延迟1.5s时button pending disabled、100ms后第二物理点击零重复请求；login200→Dashboard/me200，相关会话登出。')
    cases['P01-04']['classification']='REAL_UI_API'

readonly_ui = read(RUN / 'environment/readonly-ui-continuation/summary.json', {'cases': []})
for tested in readonly_ui['cases']:
    key = tested['case_id']
    proof = ['environment/readonly-ui-continuation/summary.json'] + tested.get('evidence', [])
    if key == 'G02-02':
        proof += [item['screenshot'] for item in tested.get('observations', []) if not item.get('meta_matches_resolved_theme', True) and item.get('screenshot')]
        map_case(cases, key, tested['status'], proof,
                 '真实light/dark/system菜单、刷新持久化、媒体切换与meta颜色比对：显式dark正常，system随媒体变dark及其reload时root/body已dark，但meta仍#fff。')
        cases[key]['classification'] = 'REAL_UI'
        cases[key]['covered_dimensions'] = tested.get('covered', [])
    elif key == 'G-01-05' and tested['status'] == 'PASS':
        map_case(cases, key, 'PASS', proof, '真实客户端localStorage换非法Token后刷新，原生network与Performance记录/api/me401、Token清除且跳到/sign-in；区别于已加载页面会话失效后不跳转的既有缺陷。')
        cases[key]['classification'] = 'REAL_UI_API'
        cases[key]['covered_dimensions'] = tested.get('covered', [])
    elif key == 'P17P18-10' and cases[key]['status'] == 'PARTIAL':
        cases[key]['evidence'] += [canonical(x) for x in proof]
        if tested.get('matches_exactly') is True:
            cases[key]['actual'] += '；Env在自有SSH localhost可信origin点击真实Copy，随后在测试测量textarea用可信Ctrl+V原生粘贴，101字符及SHA与实际API安全stderr完全一致；测量DOM、自己的session和tunnel已清理。clipboard-read API权限仍受限，以真实Paste验证复制后果，不冒称HTTPS已测。'
            cases[key].setdefault('covered_dimensions', []).append('secure-origin Clipboard exact contents through trusted native paste')
            cases[key]['remaining_dimensions'] = 'Follow新分片滚动仍缺专门实证，并受既有实时日志不更新缺陷影响；不泛称其他Copy入口已通过。'
        else:
            cases[key]['actual'] += '；Env通过任务自有SSH localhost转发真实Hub页面补Clipboard，可信origin与已复制toast有证据，但原生回读被权限拒绝，不以toast推定内容正确。'

artifact_crossmap = read(RUN / 'backend/continuation/artifact-web-crossmap.json', {'cases': []})
for tested in artifact_crossmap['cases']:
    key = tested['case_id']
    if cases[key]['status'] in ['NOT_RUN', 'PARTIAL']:
        covered = [item['dimension'] for item in tested['covered']]
        remaining = [item['dimension'] + '：' + item.get('actual_missing', '') for item in tested['remaining']]
        map_case(cases, key, tested['status'], ['backend/continuation/artifact-web-crossmap.json'] + tested['evidence'],
                 '已有实际API+Agent证据跨关联：' + '；'.join(covered) + '。Basic Docker Prefetch证据不替代Native持续坏SHA部署；恢复PUT触发不冒称无需操作的定时重试。',
                 '；'.join(remaining))
        cases[key]['classification'] = 'REAL_API_AGENT'
        cases[key]['covered_dimensions'] = covered

web_system_crossmap = read(RUN / 'backend/continuation/remaining-web-system-crossmap.json', {'cases': []})
for tested in web_system_crossmap['cases']:
    key = tested['case_id']
    row = cases[key]
    if row['status'] not in ['NOT_RUN', 'PARTIAL']:
        continue
    original_proof = row.get('evidence', [])
    proof = ['backend/continuation/remaining-web-system-crossmap.json'] + [item['path'] for item in tested['evidence']]
    covered = [item['dimension'] for item in tested['covered']]
    remaining = [item['dimension'] for item in tested['remaining']]
    map_case(cases, key, tested['status'], list(dict.fromkeys(original_proof + proof)), tested['actual'], '；'.join(remaining))
    row['classification'] = tested['classification']
    row['covered_dimensions'] = covered
    row['missing_ui_dimensions'] = tested.get('gui_missing', [])
    if tested.get('confirmed_failure_ids'):
        row['confirmed_failure_ids'] = tested['confirmed_failure_ids']
    if tested.get('narrower_subcase_status'):
        row['narrower_subcase_status'] = tested['narrower_subcase_status']

transport = read(RUN / 'environment/readonly-ui-continuation/transport-summary.json', {'cases': []})
for tested in transport['cases']:
    key = tested['case_id']
    if key not in ['P02-05', 'P03-10', 'P04-09']:
        continue
    status = tested['status']
    remaining = '；'.join(tested.get('remaining', []))
    actual = '在完整加载的真实Hub页面只断开本任务localhost SSH转发，浏览器原生fetch失败/端口拒绝；同一端口恢复并实际操作Retry/状态恢复。原Hub和4Agent未停止、没有mock响应或业务修改。'
    if key == 'P04-09':
        # TEST_PLAN P04是节点列表；已有详情页中断是额外观察，不是该条的门禁。
        status = 'PASS'
        remaining = ''
        actual += '按docs/TEST_PLAN.md P04边界，仅Nodes列表：10次失败fetch、Retry可见、实际点击后API200/4行数据恢复。已加载NodeDetail95秒没有Retry/恢复fetch另外记录，不混入列表通过结论。'
    map_case(cases, key, status, ['environment/readonly-ui-continuation/transport-summary.json'] + tested.get('evidence', []), actual, remaining)
    cases[key]['classification'] = 'REAL_UI_TRANSPORT_FAILURE'
    cases[key]['covered_dimensions'] = tested.get('covered', [])
oidc_review = transport.get('oidc_review', {})
if oidc_review.get('status') == 'PARTIAL' and cases['P01-08']['status'] in ['NOT_RUN', 'PARTIAL']:
    map_case(cases, 'P01-08', 'PARTIAL', ['environment/readonly-ui-continuation/transport-summary.json'] + oidc_review['evidence'],
             '既有真实SSO-only模式、IdP恢复后Viewer登录和state/code负向回调证据存在；旧隔离端点已撤且没有保留AuthLocation查询/PKCE verifier兑换实证，未重建环境或把源码当运行证明。',
             '；'.join(oidc_review['remaining_dimensions']))
    cases['P01-08']['classification'] = 'EXISTING_REAL_OIDC_EVIDENCE_REVIEW'
    cases['P01-08']['covered_dimensions'] = oidc_review['covered_dimensions']

empty_ttl = read(RUN / 'environment/empty-ttl/empty-ttl-summary.json', {})
repair_matrix = read(RUN / 'repair/defect-regression-matrix.json', {})
if empty_ttl.get('status') == 'completed':
    for tested in empty_ttl.get('cases', []):
        if tested['case_id'] not in ['P03-09', 'FL-14']:
            continue
        key = tested['case_id']
        actual = ('Luna使用与原Hub相同二进制启动独占数据、3秒TTL的真实隔离Hub，原Hub与4Native Agent保持不变。'
                  + ('真实登录及五类空集合API均200，Dashboard空态、指标和最近执行表已截图验证。'
                     if key == 'P03-09' else
                     '登录me200后自然等待5.512秒，五个API实际401且Token清空，但仍在Dashboard、没有登录表单；内部/executions回跳经重新登录成功。'))
        map_case(cases, key, tested['status'], ['environment/empty-ttl/empty-ttl-summary.json'] + tested['evidence'],
                 actual, '；'.join(tested.get('remaining', [])))
        cases[key]['classification'] = tested['classification']
        cases[key]['covered_dimensions'] = tested.get('covered', [])
        if tested.get('related_failure'):
            cases[key]['confirmed_failure_ids'] = [tested['related_failure']]

missing = []
for row in cases.values():
    row['evidence'] = [canonical(x, row['owner'] if row['owner'] in ['main', 'web', 'backend', 'environment'] else None) for x in row.get('evidence', [])]
    absent = [x for x in row['evidence'] if not Path(x).exists()]
    if absent:
        missing.append({'case_id': row['case_id'], 'missing': absent})
    # 没有证据的映射不能因为文字说明获得PASS。
    if row['status'] == 'PASS' and (not row['evidence'] or absent):
        row.update(status='PARTIAL', remaining_dimensions='证据路径需要核验：' + ', '.join(absent))
inventory['status'] = 'running'
inventory['summary'] = dict(collections.Counter(x['status'] for x in cases.values()))
inventory['merged_at'] = datetime.now(timezone.utc).isoformat()
inventory['merge_sources'] = ['web/case-inventory.json', 'backend/web-system-mapping.json', 'main实际API/Agent证据']
write(GLOBAL / 'feature-inventory.json', inventory)

arch = read(ROOT / 'architecture-inventory.json')
for group, prefix in [('scenarios', 'AR'), ('invariants', 'INV')]:
    for row in arch[group]:
        key = prefix + '-' + row['id'].split('-')[-1]
        c = cases[key]
        row.update(status=c['status'], evidence=c['evidence'], actual=c['actual'], remaining=c.get('remaining_dimensions', ''), linked_cases=[key])
write(ROOT / 'architecture-inventory.json', arch)

failures = {}
for owner in ['main', 'backend', 'web', 'environment']:
    data = read(RUN / owner / 'failures.json', [])
    rows = data.get('failures', []) if isinstance(data, dict) else data
    for row in rows:
        if str(row.get('status', '')).lower() in ['linked', 'superseded', 'harness_error']:
            continue
        row = copy.deepcopy(row)
        severity = row.get('severity', row.get('priority', 'P2'))
        row['severity'] = {'critical': 'P0', 'high': 'P1', 'medium': 'P2', 'low': 'P3'}.get(severity.lower(), severity.upper())
        row['origin'] = owner + '/failures.json'
        row['evidence'] = [canonical(x, owner) for x in row.get('evidence', [])]
        failures[row['id']] = row
if empty_ttl.get('status') == 'completed':
    expiry_failure = failures.get('E2E-20260930-101945-P1-001')
    if expiry_failure:
        expiry_failure['case_ids'] = list(dict.fromkeys(expiry_failure['case_ids'] + ['FL-14']))
        expiry_failure['evidence'] = list(dict.fromkeys(expiry_failure['evidence'] + [
            canonical('environment/empty-ttl/empty-ttl-summary.json'),
            canonical('environment/empty-ttl/dashboard-ttl-expired.png')]))
        expiry_failure['additional_reproduction'] = '独占真实Hub TTL=3秒，自然等待5.512秒后五API401、Token清空，但Dashboard没有自动跳登录。'
for repaired in repair_matrix.get('failures', []):
    failure = failures.get(repaired['failure_id'])
    if failure is not None:
        failure['repair_status'] = repaired['regression_status']
        failure['repair_evidence'] = [canonical(ref) for ref in repaired.get('runtime_evidence', [])]
        failure['repair_remaining_dimensions'] = repaired.get('remaining_dimensions', [])
        if repaired.get('verified_hub_sha256'):
            failure['verified_repair_hub_sha256'] = repaired['verified_hub_sha256']
write(GLOBAL / 'failures.json', {'run_id': RUN_ID, 'status': 'running', 'failures': list(failures.values())})

coverage = {
    'schema_version': 1, 'run_id': RUN_ID, 'status': 'running', 'updated_at': inventory['merged_at'],
    'definition': '504条业务/系统场景为唯一功能分母；API细断言和各suite数量为补充，不累计充当新增业务覆盖。PASS要求该case全部适用维度与证据；PARTIAL保留已测子维度。',
    'summary': {'total_cases': len(cases), 'statuses': inventory['summary'], 'confirmed_product_failures': len(failures),
                'by_owner': {owner: dict(collections.Counter(c['status'] for c in cases.values() if c['owner'] == owner)) for owner in sorted(set(c['owner'] for c in cases.values()))},
                'architecture_scenarios': dict(collections.Counter(c['status'] for c in arch['scenarios'])),
                'architecture_invariants': dict(collections.Counter(c['status'] for c in arch['invariants']))},
    'gaps': [{k: c.get(k) for k in ['case_id', 'title', 'owner', 'status', 'covered_dimensions', 'remaining_dimensions', 'evidence']} for c in cases.values() if c['status'] != 'PASS'],
    'missing_evidence_paths': missing,
    'not_final': '仍在补测；本文件不表示全部功能完成或产品可交付。',
}
if repair_matrix:
    coverage['repair_summary'] = repair_matrix.get('summary', {})
    coverage['repair_feature_inventory'] = canonical('repair/feature-inventory.json')
    coverage['baseline_counts_are_not_repaired_version_results'] = True
write(GLOBAL / 'coverage.json', coverage)

main_rows = {}
for name in ['api-results.json', 'api-edge-results.json', 'api-contract-results.json', 'relay-edge-results.json',
             'native-results.json', 'sso-results.json', 'settings-runtime-evidence.json', 'upgrade-ui-result.json',
             'cross-fs-result.json', 'nonmanaged-unit-result.json', 'cache-recovery-result.json', 'idp-recovery-result.json',
             'native-storage-edge-results.json', 'backup-missing-result.json', 'bulk-upgrade-results.json', 'agent-outbound-sockets.json', 'user-validation-results.json', 'password-session-scope-result.json', 'settings-ui-results.json','settings-persistence-result.json','button-accessibility-results.json','login-pending-result.json','remote-peer-offline-result.json']:
    data = read(ROOT / name, [])
    rows = data if isinstance(data, list) else [data]
    for row in rows:
        if row.get('case_id'):
            main_rows[row['case_id']] = copy.deepcopy(row)
idp = read(RUN / 'environment/idp-down-ui-results.json', {})
if idp.get('status') == 'passed':
    main_rows['MAIN-OIDC-IDP-DOWN-RECOVERY'].update(status='PASS', evidence=[canonical('main/idp-recovery-result.json'), canonical('environment/idp-down-ui-results.json')],
        supersedes='Root首次导航未等故障即恢复的PARTIAL记录；环境单元后续在Dex仍停时实际ERR_CONNECTION_REFUSED、无Token、local403，恢复后真实viewer UI登录me200。')
main_counts = dict(collections.Counter(row['status'] for row in main_rows.values()))
write(ROOT / 'current-summary.json', {'run_id': RUN_ID, 'results': list(main_rows.values()), 'counts': main_counts})

discovery = read(RUN / 'web/page-discovery.json', {})
write(GLOBAL / 'discovery.json', {'schema_version': 1, 'run_id': RUN_ID, 'status': 'current_runtime_discovery',
    'base_url': inventory['base_url'], 'revision': 'dddee4f0', 'pages': discovery.get('pages', []),
    'sources': [canonical('web/page-discovery.json'), canonical('web/responsive-results.json'), canonical('main/current-health.json')],
    'api_contract_evidence': [canonical('main/api-results.json'), canonical('main/api-edge-results.json'), canonical('main/api-contract-results.json')],
    'environment': {'primary_hub': 'Ubuntu native', 'native_agents': ['Debian', 'Fedora', 'Rocky', 'SUSE'], 'isolated': ['Docker', 'Compose', 'Host Integration', 'OIDC/Dex', 'TLS', 'Restore'],
                    'actual_arm_execution': 'BLOCKED: 没有ARM测试VM；arm64真实构建与HTTP/SHA/ELF不替代执行。'}})

workflow_specs = [
    ('WF-SCRIPT', '脚本→任务→节点运行→日志→审计', ['E2E-01', 'E2E-03']),
    ('WF-MULTI-TARGET', '命令任务多节点，每节点独立执行', ['E2E-02']),
    ('WF-SCHEDULE', '创建→正确owner触发→离线→补跑→禁用', ['E2E-04', 'E2E-05', 'E2E-06', 'E2E-07']),
    ('WF-EXECUTION-CONTROL', 'Retry/Timeout/Cancel→进程组与Journal', ['E2E-08']),
    ('WF-CONDITIONS', '本地/远端条件→真实效果或阻止', ['E2E-09', 'E2E-10']),
    ('WF-APPLICATION', '制品→应用→部署/操作→升级/回滚/恢复', ['E2E-11', 'E2E-12', 'E2E-13']),
    ('WF-RELAY', '源文件→Hub暂存→多目标→重试/取消/重启', ['E2E-14']),
    ('WF-USERS', '创建→登录→角色变更→调用方实时权限', ['E2E-15']),
    ('WF-ENROLLMENT', '纳管→HELLO→同步→Docker重建持久化', ['E2E-16', 'E2E-17', 'E2E-18']),
]
workflows = []
for key, name, ids in workflow_specs:
    linked = [cases[x] for x in ids]
    statuses = [c['status'] for c in linked]
    status = 'FAIL' if 'FAIL' in statuses else 'PASS' if all(s == 'PASS' for s in statuses) else 'PARTIAL'
    workflows.append({'id': key, 'name': name, 'case_ids': ids, 'status': status,
        'evidence': sorted({e for c in linked for e in c['evidence']}),
        'remaining': [c.get('remaining_dimensions') for c in linked if c.get('remaining_dimensions')]})
write(GLOBAL / 'workflows.json', {'schema_version': 1, 'run_id': RUN_ID, 'status': 'running', 'workflows': workflows})
transitions = [
    ('authentication', 'anonymous', 'authenticated', 'P01-01'),
    ('authentication', 'authenticated', 'signed_out', 'G02-05'),
    ('authentication', 'authenticated', 'expired_then_sign_in', 'G-01-05'),
    ('node', 'unregistered', 'online_synced', 'E2E-16'),
    ('script', 'revision_1', 'revision_2', 'E2E-01'),
    ('execution', 'running', 'timed_out_or_canceled', 'E2E-08'),
    ('execution', 'condition_evaluation', 'success_skipped_blocked', 'E2E-09'),
    ('application', 'defined', 'installed_healthy', 'E2E-11'),
    ('application', 'v1', 'v2_or_rollback', 'E2E-12'),
    ('application', 'interrupted_upgrade', 'recovered', 'E2E-13'),
    ('file_transfer', 'source_file', 'staged_delivered_or_retried', 'E2E-14'),
]
write(GLOBAL / 'state-graph.json', {'schema_version': 1, 'run_id': RUN_ID, 'status': 'running',
    'definition': '业务状态迁移的证据索引；状态名称是当前行为摘要，不替代逐项场景和断言。',
    'transitions': [{'entity': entity, 'from': before, 'to': after, 'case_id': key, 'status': cases[key]['status'], 'evidence': cases[key]['evidence']} for entity, before, after, key in transitions]})
resources = {}
for owner, rel in [('web', 'web/test-data.json'), ('backend', 'backend/ledger.json'), ('backend', 'backend/continuation/ledger.json'), ('backend-isolated', 'backend/isolated/ledger.json'),
                   ('main', 'main/test-data.json'), ('main', 'main/native-ledger.json'), ('main', 'main/native-second-ledger.json'), ('main', 'main/native-storage-edge-ledger.json')]:
    data = read(RUN / rel, [])
    rows = data.get('resources', []) if isinstance(data, dict) else data
    for row in rows:
        item = copy.deepcopy(row)
        item.update(owner=owner, source=canonical(rel))
        resources[(owner, str(row.get('type', row.get('kind', row.get('path', '')))), row.get('id'))] = item
write(GLOBAL / 'test-data.json', {'schema_version': 1, 'run_id': RUN_ID, 'status': 'running', 'resources': list(resources.values()),
    'environment_inventory': canonical('environment/owned-environment-inventory.json'),
    'environment_cleanup': canonical('environment/final-cleanup-phase2.json'),
    'empty_ttl_cleanup': canonical('environment/empty-ttl/empty-ttl-summary.json'),
    'root_role_fixture_cleanup': canonical('main/role-ui-fixture-ledger.json'),
    'retention_policy': '保留5VM本轮原生部署与备份、primary API不能删除的User/Execution/Audit/Transfer历史；隔离authority已整体撤除，其数据库历史仅存VM私密一致性备份。新Web fixture逐例精确清理，主环境最终验收尚待执行。'})
write(GLOBAL / 'observations.json', {'schema_version': 1, 'run_id': RUN_ID, 'status': 'running', 'observations': [
    {'id': 'OBS-ARM-RUNTIME', 'status': 'BLOCKED', 'actual': '当前环境没有ARM运行目标；arm64构建/ELF/SHA/HTTP可验证，不能代替ARM执行。', 'evidence': canonical('environment/arm64-binary-endpoint.json')},
    {'id': 'OBS-PHASE-STOPPED', 'status': 'BLOCKED', 'actual': '当前部署状态机没有实际写入STOPPED，不能伪造该checkpoint崩溃验证；实际stop阶段PREPARING已测试并确认P0。', 'evidence': canonical('main/native-results.json')},
    {'id': 'OBS-HARNESS-IDENTITY-POLLUTION', 'status': 'private_backup_retained', 'actual': '早期隔离实例同IP身份复用造成Hub RUNNING与原卷无Journal的不一致；精确取消仍RUNNING、相关定义已可删除。隔离authority整体撤除前已私密一致性备份，不直接改数据库掩盖。', 'evidence': [canonical('backend/isolated/pollution-cancel-result.json'),canonical('environment/final-cleanup-phase2.json')]},
    {'id': 'OBS-SECRET-HISTORY', 'status': 'historical_material', 'actual': '初始只读检索曾回显旧TEST_ENV认证行；未复制到本轮环境证据，最终扫描针对新增交付内容。不得声称整个会话从未有过回显。'},
]})

counts = inventory['summary']
lines = ['# 当前全功能 E2E 进度（尚未完成）', '',
         f'运行 `{RUN_ID}`，版本 `dddee4f0`，kvm2 Hub 与 4 个 Native Agent。更新时间：{inventory["merged_at"]}。', '',
         f'504 条功能场景：{counts.get("PASS",0)} 通过、{counts.get("FAIL",0)} 失败、{counts.get("PARTIAL",0)} 部分验证、{counts.get("NOT_RUN",0)} 未执行、{counts.get("BLOCKED",0)} 环境阻塞。部分验证保留已测维度，不能计为全通过。', '',
         f'已确认 {len(failures)} 个独立产品缺陷；失败场景数量与缺陷数量分别统计。同一缺陷可以影响多个场景。', '',
         f'主控细断言：{main_counts}。这些断言用于支持功能场景，不加入504的功能分母。', '',
         '## 已确认缺陷', '', '| 优先级 | 缺陷 | 实际现象 |', '|---|---|---|']
for row in sorted(failures.values(), key=lambda x: (x['severity'], x['id'])):
    lines.append('| ' + row['severity'] + ' | ' + row['id'] + ' | ' + row.get('title', '').replace('|', '/') + ' |')
remote_cleanup = read(RUN / 'environment/final-cleanup-phase2.json', {}).get('status') == 'passed'
connectivity = read(ROOT / 'resume-connectivity.json', {})
network_available = all(connectivity.get(path, {}).get('http') == 200 for path in ['/healthz', '/readyz']) if connectivity else None
recovery = read(RUN / 'review-resume/recovery.json', {})
repair_state = read(GLOBAL / 'run-state.json').get('repair', {})
if recovery.get('status') == 'restored':
    network_available = recovery.get('healthz_http') == 200 and recovery.get('readyz_http') == 200
connection_available = Path(read(GLOBAL / 'run-state.json').get('private_dir', '/nonexistent'), 'connection.json').exists()
if repair_state.get('confirmed') and repair_matrix:
    lines += ['', '## 修复版真实验收', '',
              f'22项缺陷独立台账：{repair_matrix.get("summary", {})}。缺陷子用例、开发回归与504功能场景分别统计，不累计扩大分母；关联复合场景仍需全部适用维度。', '',
              '当前原版覆盖数字保留为dddee4f0基线，修复版全部功能状态见 [修复版清单](runs/20260930-101945/repair/feature-inventory.json)，缺陷逐项证据见 [修复回归](runs/20260930-101945/repair/defect-regression-matrix.json)。静态CGO=0 Hub/4Native已真实上线，各自独立数据/凭证/服务，原primary保留。']
if connectivity:
    lines += ['', '## 最近续跑检查', '',
              f'检查时间：{connectivity.get("time", "未知")}；healthz/readyz：{"均为200" if network_available else "连接未通过"}；私密连接资料：{"存在" if connection_available else "恢复中"}。', '',
              f'系统尚有{len(system_gaps["cases"])}条部分验证已逐维列出现证与续测步骤，见 [系统缺口](runs/20260930-101945/main/remaining-system-dimensions.json)。记录一致性已核验；这些离线检查不计为新增实时测试。']
lines += ['', '## 继续执行与限制', '',
          '页面控件与跨页联动、系统异常和Host负向仍在补测。完整逐项状态与证据见 [功能清单](feature-inventory.json)、[覆盖缺口](coverage.json)、[结构化缺陷](failures.json) 和 [检查提示词](full-feature-prompts.md)。', '',
          ('全部Env隔离容器/卷/网络/VM临时配置已精确撤除，18080—18444临时端口无监听；隔离数据库已保存在VM私密备份。原生Hub/4 Agent部署、原始备份与primary不可删除历史保留。Root只读权限fixture也已清理；Web新增fixture继续逐例处理，主环境最终健康验收仍待全部补测后执行。' if remote_cleanup else '临时对象按所有者和精确ID清理；隔离环境仍待交接。原生部署、备份与不可删除历史保留。主环境最终健康验收尚未完成。'), '',
          ('用户已确认22项缺陷修复及隔离版本复测，产品代码正在修改；本报告数字是dddee4f0原测试基线，不是修复版通过结果。未执行commit/push。' if repair_state.get('confirmed') else '仅测试脚本、提示词与证据有修改；没有修改产品实现，没有执行commit/push。') + 'Mock与静态检查不计为真实Hub-Agent全链路通过。', '',
          'Luna已恢复私密测试连接，真实login/me/healthz/readyz/nodes均200，四节点身份与记录一致；空态/TTL隔离Hub已撤并保留私密一致性备份。' + ('修复与隔离部署已确认，执行计划由OpenSpec repair-e2e-confirmed-defects管理；具体用例仍用Luna，Root整合验收。' if repair_state.get('confirmed') else '用户新增产品修复要求，22项只读根因审查已完成，完整修复与隔离部署范围等待Requirement Review确认；此前测试授权继续有效。') + '未完成项没有沿用历史通过结果。']
(GLOBAL / 'final-report.md').write_text('\n'.join(lines) + '\n')
state = read(GLOBAL / 'run-state.json')
state.update(updated_at=inventory['merged_at'], phase='frontend-gap-completion' if connection_available and network_available is not False else 'environment-recovery', coverage_counts=inventory['summary'], confirmed_failure_count=len(failures), remote_environment_cleanup='passed' if remote_cleanup else 'pending',
             resume_hint='具体用例执行已按用户要求切换gpt-6-luna。旧Web/Env高能力会话安全收尾并退休；luna_web_execution读取web/luna-handoff.md和remaining-web.json，luna_environment_execution继续自有localhost转发的真实UI连通故障与恢复。Backend高能力会话仅证据/方案审阅；4新增SYS真PASS和10项crossmap已保留，私密连接恢复/Clip可信Paste/invalidLS已验证。Root只负责范围、必要串行控制、证据与最终验收，所有已知缺陷保留。继续未测/部分维度、最终健康/记录一致性/秘密扫描；不改产品/Git。')
if recovery.get('status') == 'restored' and not repair_state.get('confirmed'):
    state['phase'] = 'repair-requirement-review'
    state['requested_scope'] = '修复22项已确认缺陷并补齐全部功能测试；产品实现与隔离修复版本部署尚待本轮Requirement Review确认。'
    state['resume_hint'] = 'Luna已恢复私密连接并完成真实登录/健康/节点只读验证。空态P03-09 PASS、自然TTL FL-14 FAIL已合并，关联现有会话缺陷。后端/前端22项根因审查已完成；本轮新修复与隔离部署范围等待确认，未修改产品。具体用例继续用gpt-6-luna；Root负责范围、共享接口与最终验收，不执行Git交付。'
    state['current_tasks'] = {
        'backend_review': 'backend_fix_review已完成10项后端/环境根因与必要API配套只读审查，未实施。',
        'frontend_review': 'frontend_fix_review已完成12项UI根因与实际使用路径只读审查，未实施。',
        'test_executor': 'luna_result_audit复用；私密连接恢复完成，核对empty/TTL及Operator App部署证据与精确清理。',
        'completion_gate': f'当前{counts.get("PASS",0)}PASS/{counts.get("FAIL",0)}FAIL/{counts.get("PARTIAL",0)}PARTIAL/{counts.get("NOT_RUN",0)}NOT_RUN/{counts.get("BLOCKED",0)}BLOCKED；22项缺陷仍OPEN，不能宣称全部通过。'
    }
if repair_state.get('confirmed'):
    state['phase'] = repair_state.get('phase', 'defect-repair-implementation')
    state['repair']['defect_regression_counts'] = repair_matrix.get('summary', {})
    state['resume_hint'] = '用户已确认修复22项缺陷及kvm2隔离部署复测。遵循OpenSpec repair-e2e-confirmed-defects；backend_fix_review独占internal/agent、frontend_fix_review独占web/src，Root控制Hub/Store/共享Filter/全局记录；luna_result_audit用gpt-6-luna执行具体用例。保留原Hub/Agent及备份，不操作Git。全局覆盖数字仍是dddee4f0基线，修复版必须另附实际版本/SHA和真实证据，不能凭代码修改关缺陷。'
state['execution_models'] = {'concrete_case_steps': 'gpt-6-luna', 'test_executor': 'luna_result_audit', 'backend_review': 'backend_fix_review', 'frontend_review': 'frontend_fix_review', 'reviewer': 'root'}
state['resume_environment'] = {'connectivity_evidence': canonical('main/resume-connectivity.json'), 'network_available': network_available, 'private_connection_available': connection_available}
if recovery.get('status') == 'restored':
    state['resume_environment']['recovery_evidence'] = canonical('review-resume/recovery.json')
if not connection_available:
    state['resume_hint'] = '当前health/ready已恢复200，但/tmp私密连接目录消失。Env只读恢复连接凭据和节点inventory，不改部署/User；成功后通知Web和Backend续测。Root已完成系统20条部分验证逐维审计及504条记录一致性。' + state['resume_hint']
write(GLOBAL / 'run-state.json', state)
print(json.dumps({'total': len(cases), 'counts': inventory['summary'], 'failures': len(failures), 'missing_evidence_count': len(missing), 'missing_evidence_samples': missing[:4]}, ensure_ascii=False))
