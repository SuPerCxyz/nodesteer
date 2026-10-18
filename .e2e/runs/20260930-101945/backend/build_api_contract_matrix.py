#!/usr/bin/env python3
"""只读已有源码/运行证据；生成可审计HTTP契约矩阵，无网络请求。"""
import collections
import datetime as dt
import json
from pathlib import Path
import re
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parents[2]
API = 'internal/hub/api/api.go'
TEXT = (REPO / API).read_text().splitlines()


def source_line(needle):
    return next(i + 1 for i, line in enumerate(TEXT) if needle in line)


def function(name):
    start = source_line('func (s *Server) ' + name + '(')
    end = next((i + 1 for i in range(start, len(TEXT)) if TEXT[i].startswith('func ')), len(TEXT) + 1)
    return start, end


rows = []


def endpoint(path, category, method, handler, scope=None, notes=''):
    start, end = function(handler) if handler else (source_line('mux.HandleFunc("' + path + '"'), 0)
    if scope: start, end = scope
    registration = next((i + 1 for i, line in enumerate(TEXT) if 'mux.HandleFunc(' in line and ('s.' + handler) in line), None) if handler else start
    row = {'path_template': path, 'category': category, 'implemented_methods': method.split(','), 'method_notes': notes, 'source': [API + ':' + str(start)], 'registration_source': API + ':' + str(registration) if registration else API + ':116', 'handler': handler, '_range': (start, end), 'observations': []}
    rows.append(row)
    return row


for path, method, name, notes in [
    ('/api/login', 'ANY', 'handleLogin', '没有方法门禁；解析JSON后登录。不得机械要求非POST=405，也不得用有效凭据探针意外新建session。'),
    ('/api/oidc/state', 'GET', 'handleOIDCState', ''),
    ('/api/oidc/login', 'GET', 'handleOIDCLogin', '未启用404；启用正常302跳转，不是JSON成功响应。'),
    ('/api/oidc/callback', 'GET', 'handleOIDCCallback', '已启用失败/成功流程均可302重定向；没有要求所有错误均JSON。'),
    ('/api/agent/binary', 'GET,HEAD', 'handleAgentBinary', '公开下载；HEAD无body，GET二进制。'),
]: endpoint(path, 'public', method, name, notes=notes)
endpoint('/healthz', 'public', 'ANY', '', (183, 187), '未限制方法；成功plain text ok。')
endpoint('/readyz', 'public', 'ANY', '', (187, 197), '未限制方法；成功plain text ready，readyCheck失败503 JSON。')
for path, handler in [('/api/logout', 'handleLogout'), ('/api/me', 'handleMe')]: endpoint(path, 'session', 'ANY', handler, notes='当前没有方法门禁；withAuth仍执行。')
endpoint('/api/nodes/enrollment', 'management', 'GET,POST', 'handleNodeEnrollment', notes='管理员；GET query和POST JSON两套输入，输出含Native/Docker/Compose命令。')
endpoint('/api/nodes/upgrade', 'action', 'POST', 'handleNodesUpgrade', notes='Operator可运行；批内去重、在途复用；混合结果逐Node记录，不能用单一Execution shape代替。')
endpoint('/api/nodes/{id}/revoke-credential', 'action', 'POST', 'handleNodeByID', (841, 857), '错误方法或非管理员均403，方法/权限合并门禁。')
for kind, handler, item, methods in [
    ('nodes', 'handleNodes', 'handleNodeByID', 'GET,POST,PUT,DELETE'),
    ('groups', 'handleGroups', 'handleGroupByID', 'GET,PUT,DELETE'),
    ('scripts', 'handleScripts', 'handleScriptByID', 'GET,PUT,DELETE'),
    ('tasks', 'handleTasks', 'handleTaskByID', 'GET,PUT,DELETE'),
    ('schedules', 'handleSchedules', 'handleScheduleByID', 'GET,PUT,DELETE'),
    ('artifacts', 'handleArtifacts', 'handleArtifactByID', 'GET,DELETE'),
    ('applications', 'handleApplications', 'handleApplicationByID', 'GET,PUT,DELETE'),
    ('executions', 'handleExecutions', 'handleExecutionByID', 'GET'),
    ('transfers', 'handleTransfers', 'handleTransferByID', 'GET'),
    ('users', 'handleUsers', 'handleUserByID', 'PUT'),
]:
    endpoint('/api/' + kind, 'management', 'GET' if kind == 'executions' else 'GET,POST', handler)
    notes = ''
    if kind == 'nodes': notes = '先GetNode，缺资源404优先于方法判断；DELETE不存在404，更新支持POST/PUT。'
    if kind == 'transfers': notes = '资源GET以外若非POST或不含动作段均403，不能统一405。'
    if kind == 'users': notes = '管理员用户item仅PUT；GET item405，更新空JSON400；没有DELETE用户API。'
    endpoint('/api/' + kind + '/{id}', 'management', methods, item, notes=notes)
endpoint('/api/scripts/{id}/revisions', 'management', 'GET', 'handleScriptByID', (1173, 1186), '无资源历史集合可200 null/list，不统一404；PUT/DELETE子路径会落父handler。')
endpoint('/api/scripts/{id}/revisions/{revision}', 'management', 'GET', 'handleScriptByID', (1185, 1204), '非法revision400；缺revision404；PUT/DELETE子路径会落父handler。')
endpoint('/api/tasks/{id}/run', 'action', 'ANY', 'handleRunNow', notes='没有方法门禁；JSON空body EOF可接受；其他JSON错误400，缺Task404。非POST可能执行，Root探针仅对不存在Task/坏JSON。')
rows[-1]['registration_source'] = API + ':153'
rows[-1]['source'] = [API + ':1270', API + ':1325']
endpoint('/api/artifacts/{id}/download', 'data', 'ANY', 'handleArtifactDownload', notes='没有自定义方法门禁；ServeContent分支由标准库处理，当前允许User/注册token/Agent凭据；成功bytes、Range状态依赖ServeContent。')
for action, method, scope, notes in [
    ('state', 'GET', (1609, 1621), '不存在App的状态集合可200 null/list。'),
    ('executions', 'GET', (1621, 1633), '不存在App的执行集合可200 list。'),
    ('revisions', 'ANY', (1633, 1641), '无方法门禁，列修订记录。'),
    ('assign', 'ANY', (1641, 1660), '无方法门禁，管理员写权限；成功ok字符串；不要对现有App用非POST有效JSON探针。'),
    ('deploy', 'ANY', (1660, 1684), '无方法门禁，Operator可运行；Operation缺省deploy；不要对现有App用非POST有效JSON探针。'),
    ('nodes', 'ANY', (1684, 1694), '无方法门禁，列分配节点。'),
]: endpoint('/api/applications/{id}/' + action, 'action' if action in {'assign', 'deploy'} else 'management', method, 'handleApplicationByID', scope, notes)
endpoint('/api/executions/{id}/logs', 'management', 'GET', 'handleExecutionByID', (1800, 1814), '不存在Execution的日志集合可200 null/list。')
endpoint('/api/executions/{id}/cancel', 'action', 'POST', 'handleExecutionByID', (1814, 1840), 'Operator可运行；缺Execution使用400错误，而不是机械404。')
for action in ['retry', 'cancel']: endpoint('/api/transfers/{id}/' + action, 'action', 'POST', 'handleTransferByID', (1030, 1056), '管理员；不正确方法403；未知action404；manager错误400；retry可带target_id。')
endpoint('/api/settings', 'management', 'GET,PUT', 'handleSettings')
endpoint('/api/audit', 'management', 'ANY', 'handleAudit', notes='无方法门禁；读取分页集合与X-Total-Count。')
endpoint('/metrics', 'management', 'ANY', 'handleMetrics', notes='无方法门禁；成功Prometheus text，非JSON。')
for action, method, scope in [('upload', 'POST', (270, 389)), ('download', 'GET', (390, 426))]:
    rows.append({'path_template': '/agent/transfers/{id}/' + action, 'category': 'data', 'implemented_methods': [method], 'handler': 'HandleAgentHTTP/' + ('handleUpload' if action == 'upload' else 'handleDownload'), 'method_notes': '先解析path/验证精确Agent credential，再判断方法；无token/注册token不能通过。' + ('断点partial 409可只有status/next_offset，不必含error。' if action == 'upload' else '源或其他Agent403；stage前404；成功实际binary/SHA。'), 'source': ['internal/hub/file_transfer.go:233', 'internal/hub/file_transfer.go:' + str(scope[0])], 'registration_source': 'internal/hub/gateway.go:83', '_range': scope, 'observations': []})


def normalize(url):
    path = urlsplit(url).path
    if path.startswith('/agent/transfers/'):
        parts = path.split('/'); return '/agent/transfers/{id}/' + (parts[4] if len(parts) > 4 else '')
    parts = path.split('/')
    if len(parts) >= 4 and parts[1] == 'api' and parts[2] in {'nodes', 'groups', 'scripts', 'tasks', 'schedules', 'artifacts', 'applications', 'executions', 'transfers', 'users'}:
        if parts[2] == 'nodes' and parts[3] in {'enrollment', 'upgrade'}: return '/api/nodes/' + parts[3]
        parts[3] = '{id}'
        if len(parts) > 5 and parts[4] == 'revisions': parts[5] = '{revision}'
        return '/'.join(parts)
    return path


def shape(value, depth=0):
    if value is None: return {'kind': 'null'}
    if isinstance(value, bool): return {'kind': 'boolean'}
    if isinstance(value, str): return {'kind': 'string'}
    if isinstance(value, (int, float)): return {'kind': 'number'}
    if isinstance(value, dict): return {'kind': 'object', 'fields': {k: shape(v, depth+1) if depth < 3 else {'kind': type(v).__name__} for k, v in sorted(value.items())}}
    if isinstance(value, list):
        distinct = {}
        for item in value:
            s = shape(item, depth+1); distinct[json.dumps(s, sort_keys=True)] = s
        return {'kind': 'array', 'empty': not value, 'element_shapes': list(distinct.values())[:8]}
    return {'kind': type(value).__name__}


by_path = {row['path_template']: row for row in rows}
unmatched = []


def observe(method, path, status, evidence, case, response=None, captured=False, request=None, extra=None, headers=None):
    template = normalize(path)
    if template not in by_path:
        unmatched.append({'method': method, 'path_template': template, 'case_id': case, 'evidence': evidence}); return
    content_type = next((v for k, v in (headers or {}).items() if k.lower() == 'content-type'), '')
    body_shape = shape(response) if captured else {'kind': 'NOT_RECORDED'}
    if content_type and not content_type.lower().startswith('application/json'):
        body_shape = {'kind': 'binary' if 'octet-stream' in content_type else 'text', 'media_type': content_type}
    entry = {'method': method, 'http_status': status, 'response_shape': body_shape, 'request_shape': shape(request) if request is not None else {'kind': 'NOT_RECORDED_OR_NO_BODY'}, 'error_fields': list(response) if captured and status >= 400 and isinstance(response, dict) else [], 'error_message': response.get('error') if captured and status >= 400 and isinstance(response, dict) else None, 'response_headers_observed': sorted((headers or {}).keys()), 'query_keys': sorted(parse_qs(urlsplit(path).query)), 'case_id': case, 'evidence': evidence}
    if body_shape['kind'] in {'binary', 'text'}:
        entry.update(error_fields=[], error_message=None)
    if extra: entry.update(extra)
    by_path[template]['observations'].append(entry)


# 后端保存的真实response与caller效果，失败attempt仍可证明其已收到的HTTP契约。
for directory in [HERE / 'evidence', HERE / 'isolated/evidence']:
    for file in directory.glob('*.json'):
        d = json.loads(file.read_text()); result = d.get('result', {})
        for event in d.get('events', []):
            if not isinstance(event.get('api'), str) or ' ' not in event['api']: continue
            method, path = event['api'].split(' ', 1)
            if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}: continue
            response = event.get('response', event.get('terminal'))
            status = event.get('status')
            if not isinstance(status, int) and 'terminal' in event: status = 200
            if not isinstance(status, int): continue
            observe(method, path, status, str(file.relative_to(ROOT)), result.get('id', file.stem), response, True, event.get('request'), {'attempt_status': result.get('status'), 'status_source': 'explicit_http' if 'status' in event else 'runtime_helper_asserts_200_before_terminal_record'}, event.get('headers'))

for filename in ['primary-cleanup-result.json', 'primary-stage-cleanup-result.json', 'isolated/cleanup-result.json', 'isolated/cleanup-reconciled-definitions.json']:
    file = HERE / filename
    if not file.exists(): continue
    d = json.loads(file.read_text())
    for event in d.get('events', []) + d.get('cleanup_reconciliation_events', []):
        if isinstance(event.get('api'), str) and isinstance(event.get('status'), int):
            method, path = event['api'].split(' ', 1); observe(method, path, event['status'], str(file.relative_to(ROOT)), 'BACKEND-EXACT-CLEANUP', event.get('response'), True, event.get('request'), headers=event.get('headers'))

for row in json.loads((ROOT / 'main/api-results.json').read_text()):
    req = row.get('request', {})
    if req: observe(req['method'], req['url'], row['actual_http'], 'main/api-results.json', row['case_id'], extra={'scenario': row['title'], 'body_shape_note': '该结果仅保存HTTP和detail，没有响应JSON，不能据源码补成已实测shape。'})
for row in json.loads((ROOT / 'main/api-contract-results.json').read_text()):
    response = {'error': row['error']} if row.get('response_kind') == 'error' and isinstance(row.get('error'), str) else None
    extra = {'contract_shape_assertion': row.get('shape_matches'), 'response_kind_asserted': row.get('response_kind')}
    if row.get('response_kind') == 'empty-list': extra['shape_ambiguity'] = '原helper只断言null或list，未保存确切body类型。'
    observe(row['method'], row['path'], row['actual_http'], 'main/api-contract-results.json', row['case_id'], response, response is not None, extra=extra)

READ_PATHS = ['/api/me', '/api/nodes', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules', '/api/artifacts', '/api/applications', '/api/executions', '/api/transfers', '/api/audit', '/api/settings', '/api/users', '/metrics']
POST_PATHS = ['/api/nodes/enrollment', '/api/groups', '/api/scripts', '/api/tasks', '/api/schedules', '/api/applications', '/api/transfers', '/api/users']
DELETE_PATHS = ['/api/groups/{id}', '/api/scripts/{id}', '/api/tasks/{id}', '/api/schedules/{id}', '/api/applications/{id}']
for row in json.loads((ROOT / 'main/api-edge-results.json').read_text()):
    case = row['case_id']; method = 'GET'; path = None
    if case.startswith(('MAIN-INVALID-BEARER-', 'MAIN-REVOKED-BEARER-')): path = READ_PATHS[int(case.rsplit('-', 1)[1])]
    elif case.startswith('MAIN-MALFORMED-JSON-'): method, path = 'POST', POST_PATHS[int(case.rsplit('-', 1)[1])]
    elif case.startswith('MAIN-DELETE-MISSING-'): method, path = 'DELETE', DELETE_PATHS[int(case.rsplit('-', 1)[1])]
    elif case == 'MAIN-SESSION-VALID': path = '/api/me'
    elif case == 'MAIN-SESSION-LOGOUT': method, path = 'POST', '/api/logout'
    elif case == 'MAIN-ARTIFACT-EXACT-CLEANUP': method, path = 'DELETE', '/api/artifacts/{id}'
    elif case.startswith('MAIN-ARTIFACT-'): path = '/api/artifacts/{id}/download'
    elif case.startswith('MAIN-RELAY-'): path = '/agent/transfers/{id}/download'
    if path: observe(method, path, row['actual_http'], 'main/api-edge-results.json', case, extra={'association_source': 'main/api_edges.py', 'detail_fields': list(row.get('detail', {}))})

for row in json.loads((ROOT / 'main/relay-edge-results.json').read_text()):
    case, detail = row['case_id'], row.get('detail', {})
    code = detail.get('actual_http')
    if not isinstance(code, int): continue
    if 'DOWNLOAD' in case: method, path = 'GET', '/agent/transfers/{id}/download'
    elif case == 'MAIN-RELAY-CANCEL-EXACT': method, path = 'POST', '/api/transfers/{id}/cancel'
    else: method, path = 'POST', '/agent/transfers/{id}/upload'
    captured = 'response' in detail
    observe(method, path, code, 'main/relay-edge-results.json', case, detail.get('response'), captured, extra={'association_source': 'main/relay_edges.py', 'bytes_match_asserted': detail.get('bytes_match')})

for row in json.loads((ROOT / 'main/bulk-upgrade-results.json').read_text()):
    case, d = row['case_id'], row.get('detail', {})
    response = d.get('first', d.get('results', [d['result']] if 'result' in d else None))
    if case == 'MAIN-UPGRADE-INVALID-BATCH-ATOMIC': observe('POST', '/api/nodes/upgrade', d['http'], 'main/bulk-upgrade-results.json', case, extra={'new_execution_count': d['new_execution_count']})
    elif response is not None: observe('POST', '/api/nodes/upgrade', 200, 'main/bulk-upgrade-results.json', case, response, True, extra={'status_source': 'main/native_faults.py api helper raise_for_status + runtime result', 'duplicate_result_shape': shape(d['duplicate']) if 'duplicate' in d else None})

STATUS = {'OK': 200, 'Found': 302, 'BadRequest': 400, 'Unauthorized': 401, 'Forbidden': 403, 'NotFound': 404, 'MethodNotAllowed': 405, 'Conflict': 409, 'InternalServerError': 500, 'ServiceUnavailable': 503}
for row in rows:
    start, end = row.pop('_range')
    source_path = 'internal/hub/file_transfer.go' if row['path_template'].startswith('/agent/') else API
    lines = (REPO / source_path).read_text().splitlines()
    code = '\n'.join(lines[start - 1:end - 1]) if end else ''
    branches = []
    for i, line in enumerate(lines[start - 1:end - 1], start):
        m = re.search(r'(writeErr|writeTransferJSON)\(w, http.Status(\w+), (.+)', line)
        if m:
            literal = re.match(r'"([^"]*)"', m[3])
            branches.append({'http_status': STATUS.get(m[2], m[2]), 'source': source_path + ':' + str(i), 'source_condition_response': m[3].rstrip(')'), 'error_literal': literal[1] if literal else None, 'verification': 'SOURCE_ONLY until linked to observed response'})
    row['source_declared_error_branches'] = branches
    if not row['path_template'].startswith('/agent/') and row['category'] not in {'public'}: row['authentication_source'] = [API + ':219', API + ':243', API + ':249']
    signatures = {}
    for observation in row['observations']:
        signature = json.dumps({k: observation[k] for k in ['method', 'http_status', 'response_shape', 'error_message', 'query_keys']}, sort_keys=True)
        if signature not in signatures:
            sample = dict(observation); sample['evidence_refs'] = [{'file': observation['evidence'], 'case_id': observation['case_id']}]; sample['observation_count'] = 1; signatures[signature] = sample
        else:
            s = signatures[signature]; s['observation_count'] += 1
            ref = {'file': observation['evidence'], 'case_id': observation['case_id']}
            if ref not in s['evidence_refs']: s['evidence_refs'].append(ref)
    row['observed_contracts'] = list(signatures.values()); row.pop('observations')
    observed = row['observed_contracts']
    row['observed_methods'] = sorted({o['method'] for o in observed})
    row['observed_http_statuses'] = sorted({o['http_status'] for o in observed})
    row['observed_success_shapes'] = list({json.dumps(o['response_shape'], sort_keys=True): o['response_shape'] for o in observed if o['http_status'] < 400 and o['response_shape']['kind'] != 'NOT_RECORDED'}.values())
    row['observed_error_fields'] = sorted({field for o in observed for field in o['error_fields']})
    row['request_validation_audit'] = []
    for branch in branches:
        if branch['http_status'] != 400: continue
        matching = [o for o in observed if o['http_status'] == 400 and branch['error_literal'] and o.get('error_message') == branch['error_literal']]
        row['request_validation_audit'].append({'source': branch['source'], 'validator_or_error': branch['error_literal'] or branch['source_condition_response'], 'runtime_status': 'OBSERVED_SAME_ERROR_STRING' if matching else 'SOURCE_ONLY_NOT_RUN_OR_NO_ERROR_BODY_RECORDED', 'evidence': list(dict.fromkeys(o['evidence'] for o in matching)), 'evidence_case_ids': list(dict.fromkeys(o['case_id'] for o in matching))})
    row['input_fields_from_handler_source'] = sorted(set(re.findall(r'json:"([^",]+)', code)))
    if row['path_template'] == '/api/nodes/enrollment':
        row['input_fields_from_handler_source'] = ['node_name', 'node_ip', 'hub_address']
        row['linked_validation_sources'] = [API + ':751', API + ':768', API + ':778', API + ':783']
        row['mandatory_field_notes'] = 'node_name必填/≤128/字符集；node_ip有效IP或DNS必填；hub_address可选，但提供时必须http/https URL。GET/POST共用parseEnrollmentOptions。'
    if row['path_template'] == '/api/artifacts':
        row['input_fields_from_handler_source'] = ['name', 'version', 'architecture', 'file']
        row['mandatory_field_notes'] = 'Multipart name/version/file必填；architecture空默认amd64；JSON坏body先触发name/version必填，而不是泛化invalid request。'
    if row['path_template'] == '/api/users':
        row['linked_validation_sources'] = [API + ':2011', 'internal/store/sqlite_users_nodes.go:16']
        row['mandatory_field_notes'] = '当前handler/Store未显式要求username/password非空；空role默认viewer。没有运行空User创建，不能用{}“必填负向”探针擅自新增空User。'
    if row['path_template'] == '/api/users/{id}':
        row['linked_validation_sources'] = [API + ':2048', 'internal/store/sqlite_users_nodes.go:69', 'internal/store/sqlite_users_nodes.go:79']
        row['mandatory_field_notes'] = 'role或password至少一项非空，空JSON400；Store UPDATE不检查RowsAffected，不能机械期待不存在User PUT404。未执行该缺资源有效更新。'
    missing = []
    for method in row['implemented_methods']:
        if method != 'ANY' and not any(o['method'] == method and o['http_status'] < 400 for o in observed): missing.append({'dimension': 'success', 'method': method, 'status': 'NOT_RUN_OR_NO_RECORDED_SUCCESS', 'remaining': '本方法成功码与body/headers无完整已存实际证据；源实现不算PASS。'})
    if not row['observed_success_shapes']: missing.append({'dimension': 'success_shape', 'status': 'NOT_RECORDED', 'remaining': '尚无本端点成功body真实形状记录；HTTP200记录不能替代字段验证。'})
    if row['implemented_methods'] != ['ANY'] and not any(o['http_status'] in {403, 405} and ('CONTRACT' in o['case_id'] or 'METHOD' in o['case_id']) for o in observed): missing.append({'dimension': 'unsupported_method', 'status': 'NOT_RUN', 'remaining': '按该handler门禁和auth/资源查找顺序实测；不能统一405。'})
    if row['implemented_methods'] == ['ANY']:
        missing.append({'dimension': 'other_methods_without_explicit_guard', 'status': 'NOT_RUN', 'remaining': '无显式方法门禁只由源码确认；目前只观察已列方法，不把其他方法默认推为405或实测成功。运行/部署/分配入口仅用不存在对象或坏JSON补请求，避免执行业务。'})
    if any(method in row['implemented_methods'] for method in ['POST', 'PUT']) or row['path_template'] in {'/api/login', '/api/tasks/{id}/run', '/api/applications/{id}/assign', '/api/applications/{id}/deploy'}:
        json_endpoint = row['path_template'] != '/api/artifacts' and not row['path_template'].startswith('/agent/')
        if json_endpoint and not any(o['http_status'] == 400 and ('MALFORMED' in o['case_id'] or o.get('error_message') == 'invalid request') for o in observed): missing.append({'dimension': 'malformed_json', 'status': 'NOT_RUN_OR_BODY_NOT_RECORDED', 'remaining': '用坏JSON触发解析分支，确认实际status和JSON error:string；不创建定义。'})
        missing.append({'dimension': 'required_fields_and_specific_validators', 'status': 'PARTIAL', 'remaining': '按source_declared_error_branches逐条对照已有observed_contracts；当前仅已有输入组合实测，未自动把其他必填/校验分支推为PASS。'})
    for branch in branches:
        if branch['http_status'] not in row['observed_http_statuses']:
            missing.append({'dimension': 'source_error_branch', 'status': 'NOT_RUN', 'http_status': branch['http_status'], 'source': branch['source'], 'remaining': '该源分支无当前已存真实HTTP状态覆盖；数据库/部署/服务不可用等故障注入不属于本次只读操作。'})
    row['uncovered_combinations'] = missing
    row['coverage_status'] = 'NOT_RUN' if not observed else 'PARTIAL' if missing else 'AUDITED_OBSERVED_CONTRACTS'

data = {'run_id': ROOT.name, 'generated_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': 'read-only-evidence-audit; no network/state/schema/product mutation', 'endpoint_count': len(rows), 'status_scope': 'coverage status audits recorded HTTP contracts only; no source-only PASS, no whole-function assertion', 'cross_cutting': [
    {'dimension': 'CORS OPTIONS', 'source': API + ':204', 'implemented_status': 204, 'authentication_order': 'OPTIONS在withAuth前直接204；不要求401/JSON', 'observed_status': 'NOT_RUN'},
    {'dimension': 'JSON error helper', 'source': [API + ':292', API + ':299'], 'shape': {'error': 'string'}, 'verification': '已记录error:string的观察逐条列出；仅记录HTTP的case不推定error形状'},
    {'dimension': '非JSON例外', 'actual_contract': 'health/readiness成功plain text、metrics Prometheus、binary与Relay bytes、OIDC302、OPTIONS204；Relay partial409进度可无error。'},
    {'dimension': 'Agent control WebSocket', 'source': 'internal/hub/gateway.go:83', 'scope': '与REST矩阵分离；真实101/upstream WS、HELLO、控制通知等见backend88/proxy事件，不能把HTTP JSON断言套到WS帧。'},
    {'dimension': '认证身份', 'source': [API + ':219', API + ':249', 'internal/hub/file_transfer.go:233'], 'actual': '公开health/readiness/OIDC state/binary；管理API bearer session/RBAC；Artifact user或Agent/registration；Relay仅精确AgentID+credential，Source/Target逐传输限定。'}
], 'input_evidence': ['main/api-results.json', 'main/api-edge-results.json', 'main/api-contract-results.json', 'main/relay-edge-results.json', 'main/bulk-upgrade-results.json', 'backend/evidence/*.json', 'backend/isolated/evidence/*.json'], 'unmatched_runtime_paths': unmatched, 'endpoints': sorted(rows, key=lambda r:r['path_template'])}
data['suggested_followup_probes'] = [
    {'group': '公开/只读方法和错误body', 'requests': ['OPTIONS /api/tasks → 204/no body（无Bearer）', 'POST /healthz → 200/text', 'POST /readyz → 200/text', 'POST /api/oidc/state → 405/error:string', 'POST /api/oidc/login → 405/error:string', 'GET /api/oidc/callback → 当前primary未启用时404/error:string', 'POST /api/oidc/callback → 405/error:string', 'POST /metrics → 已认证200/Prometheus text', 'POST /api/me → 已认证200/Session JSON'], 'permission': '无业务定义或配置写入；metrics常规计数不计业务变更'},
    {'group': '读子资源成功/错误方法', 'requests': ['GET /api/applications/{absent}/nodes → 200/null或list（保存实际类型）', 'GET /api/applications/{absent}/revisions → 200/null或list', 'POST /api/applications/{absent}/nodes → 当前无方法门禁，记录实际200/shape', 'POST /api/applications/{absent}/state → 405/error:string', 'POST /api/applications/{absent}/executions → 405/error:string', 'POST /api/scripts/{absent}/revisions → 405/error:string（当前switch default）'], 'permission': '不存在对象上的只读子路由，不触发Task/App/服务'},
    {'group': 'Run/Assign/Deploy方法不能机械405', 'requests': ['GET /api/tasks/{absent}/run 空body → 404/error:string', 'PATCH /api/tasks/{absent}/run body "{" → 400/invalid request', 'GET或PATCH /api/applications/{absent}/assign body "{" → 400/invalid request', 'GET或PATCH /api/applications/{absent}/deploy body "{" → 400/invalid request'], 'permission': '只用不存在ID或无法解析JSON；不得对现有对象用有效JSON测试非POST'},
    {'group': '动作方法/错误资源', 'requests': ['GET /api/nodes/{absent}/revoke-credential → 403/permission denied', 'GET /api/transfers/{absent}/retry → 403/permission denied', 'GET /api/transfers/{absent}/cancel → 403/permission denied', 'PATCH /api/transfers/{absent} → 403/permission denied', 'POST /api/transfers/{absent}/retry → 400/实际manager错误', 'POST /api/executions/{absent}/cancel → 400/实际manager错误'], 'permission': '不存在ID动作，不改变已有数据；把错误body实际保留'},
    {'group': '必填/输入只读拒绝', 'requests': ['GET /api/nodes/enrollment 缺node_name/node_ip → 400/字段校验（无PrepareEnrollment）', 'GET /api/nodes/enrollment 有有效dummy DNS+name → 200/命令结构（GET不创建Node）', 'POST /api/nodes/upgrade {} 或node_ids=[] → 400/node_ids required', 'POST /api/artifacts 非multipart或缺name/version → 400/name and version required', 'POST /api/artifacts 有name/version无file → 400/file required', 'POST /api/login 坏JSON → 400/invalid request'], 'permission': '全部在写入前拒绝或只读GET；不能POST /api/users {}创建空User'},
    {'group': 'Agent数据方法/已清环境限制', 'requests': ['Relay wrong method：需要真实Primary Agent credential才会进入405方法门禁；无凭据先401', 'Artifact binary成功HEAD/Range需要已有合法Artifact并保存headers/实际byte SHA', 'OIDC启用时callback/login成功302仅有既有浏览器流程证据、未保存HTTP契约；SSO/Dex已清，不用源码代替HTTP PASS'], 'permission': 'core已撤除，不调用core，不新建Agent/SSO；由Root决定仅使用现存primary无写资源读取'},
]
data['summary'] = {'endpoint_coverage': dict(collections.Counter(r['coverage_status'] for r in rows)), 'observed_statuses': sorted({n for r in rows for n in r['observed_http_statuses']}), 'distinct_observed_contract_shapes': sum(len(r['observed_contracts']) for r in rows), 'runtime_observation_count': sum(o['observation_count'] for r in rows for o in r['observed_contracts'])}
(HERE / 'api-contract-matrix.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
print('API contract matrix',len(rows),'endpoints;',data['summary'])
