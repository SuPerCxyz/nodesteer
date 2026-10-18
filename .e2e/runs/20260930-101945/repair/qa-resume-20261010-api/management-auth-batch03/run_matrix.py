import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
CONNECTION = json.loads((PRIVATE / 'repair-connection.json').read_text())
BASE = CONNECTION['hub_url'].rstrip('/')
ADMIN = CONNECTION['token']
if urlsplit(BASE).port != 18080:
    raise SystemExit('repair_endpoint_guard_failed')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

OPENER = build_opener(ProxyHandler({}), NoRedirect())
PLAN_PATH = HERE / 'plan.json'
PROGRESS_PATH = HERE / 'progress.json'
RESULTS_PATH = HERE / 'results.json'
PLAN = json.loads(PLAN_PATH.read_text())


def utc():
    return datetime.now(timezone.utc).isoformat()


def save_progress(status, stage, note):
    value = {
        'updated_at_utc': utc(),
        'case_id': 'AUTH-MANAGEMENT',
        'status': status,
        'stage': stage,
        'scope': 'repair:18080 only; no login or valid-credential mutation; no Task/fixture creation.',
        'next_action': note,
        'results': 'results.json',
        'plan': 'plan.json',
    }
    PROGRESS_PATH.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def request(method, path, headers=None, body=None, timeout=12):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body, separators=(',', ':')).encode()
        h['Content-Type'] = 'application/json'
    req = Request(BASE + path, data=data, headers=h, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as response:
            status = response.status
            content_type = response.headers.get('Content-Type', '')
            total_count = response.headers.get('X-Total-Count')
            raw = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as exc:
        status = exc.code
        content_type = exc.headers.get('Content-Type', '') if exc.headers else ''
        total_count = exc.headers.get('X-Total-Count') if exc.headers else None
        raw = exc.read(2 * 1024 * 1024 + 1)
    except Exception as exc:
        return {'method': method, 'path': path, 'transport_error': type(exc).__name__}
    if len(raw) > 2 * 1024 * 1024:
        return {'method': method, 'path': path, 'status': status, 'content_type': content_type, 'body_too_large': True}
    try:
        obj = json.loads(raw) if raw else None
    except Exception:
        obj = None
    result = {
        'method': method,
        'path': path,
        'status': status,
        'content_type': content_type,
        'response_bytes': len(raw),
        'json_object': isinstance(obj, dict),
        'body_keys': sorted(obj.keys()) if isinstance(obj, dict) else [],
    }
    if isinstance(obj, dict):
        if isinstance(obj.get('error'), str):
            result['error'] = obj['error']
        # Only surface an unexpected returned ID so Root can identify an accidental creation.
        if status in (200, 201) and isinstance(obj.get('id'), str):
            result['unexpected_returned_id'] = obj['id']
    return result


def get_admin(path):
    result = request('GET', path, {'Authorization': 'Bearer ' + ADMIN})
    if 'transport_error' in result or result.get('status') != 200:
        raise RuntimeError('admin_read_failed:' + path + ':' + str(result.get('status', result.get('transport_error'))))
    return result


def parse_admin_json(path):
    req = Request(BASE + path, headers={'Authorization': 'Bearer ' + ADMIN}, method='GET')
    try:
        with OPENER.open(req, timeout=12) as response:
            status = response.status
            content_type = response.headers.get('Content-Type', '')
            total_count = response.headers.get('X-Total-Count')
            raw = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as exc:
        raise RuntimeError('admin_read_http_' + str(exc.code) + ':' + path)
    if status != 200 or len(raw) > 2 * 1024 * 1024:
        raise RuntimeError('admin_read_status_or_size:' + path)
    try:
        value = json.loads(raw) if raw else None
    except Exception:
        raise RuntimeError('admin_read_invalid_json:' + path)
    return value, content_type, total_count


def rows_from(value, keys):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                return value[key]
    raise RuntimeError('unexpected_collection_shape')


def field(row, name):
    if not isinstance(row, dict):
        return None
    if name in row:
        return row[name]
    upper = name[:1].upper() + name[1:]
    return row.get(upper)


def safe_rows(rows, kind):
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if kind == 'nodes':
            item = {k: field(row, k) for k in ('id', 'agent_id', 'hostname', 'labels')}
        elif kind == 'transfers':
            targets = field(row, 'targets') or []
            item = {k: field(row, k) for k in ('id', 'source_node_id', 'status', 'size', 'sha256')}
            item['targets'] = [
                {'node_id': field(t, 'node_id'), 'status': field(t, 'status')}
                for t in targets if isinstance(t, dict)
            ]
        elif kind == 'scripts':
            item = {k: field(row, k) for k in ('id', 'name', 'sha256', 'revision', 'enabled')}
        elif kind == 'tasks':
            target = field(row, 'target') or {}
            item = {k: field(row, k) for k in ('id', 'name', 'type', 'enabled', 'retry', 'timeout')}
            item['target_node_ids'] = field(target, 'node_ids') if isinstance(target, dict) else None
        elif kind == 'executions':
            item = {k: field(row, k) for k in ('id', 'task_id', 'node_id', 'status', 'start_time', 'end_time')}
        elif kind == 'applications':
            item = {k: field(row, k) for k in ('id', 'name', 'version', 'enabled', 'artifact_id')}
        elif kind == 'artifacts':
            item = {k: field(row, k) for k in ('id', 'name', 'version', 'architecture', 'arch', 'size', 'sha256')}
        elif kind == 'users':
            item = {k: field(row, k) for k in ('id', 'username', 'role', 'enabled')}
        elif kind == 'audit':
            item = {k: field(row, k) for k in ('id', 'action', 'resource', 'resource_id', 'created_at')}
        else:
            item = {'id': field(row, 'id')}
        out.append(item)
    out.sort(key=lambda x: str(x.get('id', '')))
    return out


def snapshot_one(name, path, keys, kind):
    value, _, total_count = parse_admin_json(path)
    if kind == 'settings':
        if not isinstance(value, dict):
            raise RuntimeError('settings_shape_invalid')
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        return {'count': len(value), 'keys': sorted(value.keys()), 'safe_value_sha256': hashlib.sha256(encoded).hexdigest()}
    rows = rows_from(value, keys)
    safe = safe_rows(rows, kind)
    encoded = json.dumps(safe, sort_keys=True, separators=(',', ':')).encode()
    snapshot = {'count': len(safe), 'safe_metadata_sha256': hashlib.sha256(encoded).hexdigest()}
    if kind in {'nodes', 'transfers', 'applications', 'artifacts', 'users'}:
        snapshot['safe_rows'] = safe
    if kind == 'tasks':
        snapshot['attempt02_tasks'] = [
            {'id': x.get('id'), 'name': x.get('name'), 'type': x.get('type'), 'target_node_ids': x.get('target_node_ids')}
            for x in safe if 'attempt02' in str(x.get('name', '')).lower()
        ]
    if kind == 'executions':
        active = [x for x in safe if str(x.get('status', '')).upper() in {'PENDING', 'QUEUED', 'RUNNING'}]
        snapshot['active_count'] = len(active)
        snapshot['active_executions'] = active
    if kind == 'audit':
        snapshot['rows_count'] = len(safe)
        snapshot['total_count'] = int(total_count) if total_count and total_count.isdigit() else None
    return snapshot


def take_snapshot():
    specs = [
        ('nodes', '/api/nodes', ('nodes', 'items'), 'nodes'),
        ('transfers', '/api/transfers', ('transfers', 'items'), 'transfers'),
        ('tasks', '/api/tasks', ('tasks', 'items'), 'tasks'),
        ('executions', '/api/executions?limit=100', ('executions', 'items'), 'executions'),
        ('applications', '/api/applications', ('applications', 'items'), 'applications'),
        ('artifacts', '/api/artifacts', ('artifacts', 'items'), 'artifacts'),
        ('users', '/api/users', ('users', 'items'), 'users'),
        ('audit', '/api/audit?limit=100', (), 'audit'),
    ]
    return {name: snapshot_one(name, path, keys, kind) for name, path, keys, kind in specs}


def fresh_ids():
    return {k: str(uuid.uuid4()) for k in ('node', 'transfer', 'group', 'script', 'task', 'schedule', 'artifact', 'application', 'execution', 'user')}


def instantiate_paths(ids):
    repl = {
        '<fresh-random-node-uuid>': ids['node'],
        '<fresh-random-transfer-uuid>': ids['transfer'],
        '<fresh-random-group-uuid>': ids['group'],
        '<fresh-random-script-uuid>': ids['script'],
        '<fresh-random-task-uuid>': ids['task'],
        '<fresh-random-schedule-uuid>': ids['schedule'],
        '<fresh-random-artifact-uuid>': ids['artifact'],
        '<fresh-random-application-uuid>': ids['application'],
        '<fresh-random-execution-uuid>': ids['execution'],
        '<fresh-random-user-uuid>': ids['user'],
    }
    gets = []
    for entry in PLAN['protected_get_paths']:
        path = entry['path']
        for old, new in repl.items():
            path = path.replace(old, new)
        gets.append({'method': 'GET', 'path': path, 'route': entry['route']})
    writes = []
    for entry in PLAN['representative_write_paths']:
        path = entry['path']
        for old, new in repl.items():
            path = path.replace(old, new)
        writes.append({'method': entry['method'], 'path': path, 'body': entry.get('body')})
    return gets, writes


def auth_probe(method, path, mode, body):
    headers = {}
    if mode == 'invalid_bearer':
        headers['Authorization'] = 'Bearer invalid-management-batch03-' + str(uuid.uuid4())
    result = request(method, path, headers, body)
    expected_error = 'unauthorized' if mode == 'anonymous' else 'invalid or expired session'
    result.update({
        'auth_mode': mode,
        'expected_status': 401,
        'expected_error': expected_error,
        'pass': result.get('status') == 401 and result.get('content_type', '').split(';', 1)[0] == 'application/json'
                and result.get('json_object') is True and result.get('body_keys') == ['error']
                and result.get('error') == expected_error,
    })
    return result


def write_results(result):
    RESULTS_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


def main():
    session_evidence_path = '.e2e/runs/20260930-101945/repair/qa-resume-20261010-root/management-session-refresh.json'
    session_evidence = json.loads(Path(session_evidence_path).read_text())
    if session_evidence.get('new_me_status') != 200 or session_evidence.get('role') != 'administrator':
        raise SystemExit('refreshed_admin_session_evidence_guard_failed')
    result = {
        'case_id': 'AUTH-MANAGEMENT',
        'started_at_utc': utc(),
        'hub_port': 18080,
        'redirects_followed': False,
        'admin_role': session_evidence['role'],
        'admin_me_status': session_evidence['new_me_status'],
        'admin_session_refresh_evidence': session_evidence_path,
        'private_admin_used_for': ['bounded safe metadata snapshots', 'fresh item ID GET 404 preflight only'],
        'earlier_harness_attempt': {'pre_snapshot_path':'/api/executions?limit=1000','failure':'admin_read_status_or_size','matrix_requests_sent':0,'read_gets_already_counted':6},
        'credential_values_or_full_bodies_persisted': False,
        'matrix': [],
        'write_id_preflight': [],
        'status': 'STARTED',
    }
    write_results(result)
    save_progress('RUNNING', 'admin-session-precheck', 'Use Root recorded fresh /me 200 administrator evidence; read current private session only into memory for header use.')
    ids = fresh_ids()
    result['fresh_item_ids'] = ids
    before = {}
    snapshot_specs = [
        ('nodes', '/api/nodes', ('nodes', 'items'), 'nodes'),
        ('transfers', '/api/transfers', ('transfers', 'items'), 'transfers'),
        ('tasks', '/api/tasks', ('tasks', 'items'), 'tasks'),
        ('executions', '/api/executions?limit=100', ('executions', 'items'), 'executions'),
        ('applications', '/api/applications', ('applications', 'items'), 'applications'),
        ('artifacts', '/api/artifacts', ('artifacts', 'items'), 'artifacts'),
        ('users', '/api/users', ('users', 'items'), 'users'),
        ('audit', '/api/audit?limit=100', (), 'audit'),
    ]
    try:
        for snap_name, snap_path, snap_keys, snap_kind in snapshot_specs:
            before[snap_name] = snapshot_one(snap_name, snap_path, snap_keys, snap_kind)
            result['before'] = before
            write_results(result)
    except Exception as exc:
        result['status'] = 'BLOCKED_BEFORE_SNAPSHOT_' + type(exc).__name__
        result['before_snapshot_error'] = str(exc)[:200]
        write_results(result)
        save_progress(result['status'], 'before-snapshot', 'Stop before matrix; no unauthenticated requests sent.')
        return 2
    result['before'] = before
    result['attempt02_tasks_present'] = before['tasks'].get('attempt02_tasks', [])
    result['active_executions_before'] = before['executions'].get('active_executions', [])
    result['preflight_captured_at_utc'] = utc()
    if result['active_executions_before']:
        result['status'] = 'BLOCKED_ACTIVE_EXECUTION_PRESENT'
        result['after'] = before
        write_results(result)
        save_progress(result['status'], 'preflight', 'Active execution present; stop before sending matrix requests and report exact safe IDs/statuses.')
        return 2
    write_results(result)
    save_progress('RUNNING', 'fresh-item-id-preflight', 'Verify all item write IDs are absent with Admin GET 404.')
    for path in ('/api/scripts/' + ids['script'], '/api/tasks/' + ids['task'], '/api/executions/' + ids['execution']):
        check = request('GET', path, {'Authorization': 'Bearer ' + ADMIN}, None)
        record = {'path': path, 'status': check.get('status'), 'body_keys': check.get('body_keys', []), 'error': check.get('error')}
        record['pass'] = check.get('status') == 404 and check.get('json_object') is True and check.get('body_keys') == ['error']
        result['write_id_preflight'].append(record)
        write_results(result)
        if not record['pass']:
            result['status'] = 'BLOCKED_FRESH_ITEM_ID_NOT_404'
            write_results(result)
            save_progress(result['status'], 'fresh-item-id-preflight', 'Stop; no unauthenticated matrix was sent.')
            return 2
    gets, writes = instantiate_paths(ids)
    save_progress('RUNNING', 'unauthenticated-get-matrix', 'Run two bounded auth modes on each protected GET path.')
    for spec in gets:
        for mode in ('anonymous', 'invalid_bearer'):
            item = auth_probe('GET', spec['path'], mode, None)
            item['route'] = spec['route']
            result['matrix'].append(item)
            write_results(result)
            if not item['pass']:
                result['status'] = 'PARTIAL_GET_MATRIX_FAILED_STOP_WRITES'
                result['unexpected_case'] = {'method': item['method'], 'path': item['path'], 'auth_mode': mode, 'status': item.get('status'), 'error': item.get('error')}
                result['after'] = take_snapshot()
                write_results(result)
                save_progress(result['status'], 'unauthenticated-get-matrix', 'Unexpected GET auth result; representative write requests were not sent.')
                return 1
    result['get_matrix_passed'] = True
    write_results(result)
    save_progress('RUNNING', 'unauthenticated-write-guard-matrix', 'Run representative POST/PUT/DELETE using no auth and synthetic invalid Bearer only.')
    for spec in writes:
        for mode in ('anonymous', 'invalid_bearer'):
            item = auth_probe(spec['method'], spec['path'], mode, spec.get('body'))
            result['matrix'].append(item)
            write_results(result)
            if not item['pass']:
                result['status'] = 'PARTIAL_WRITE_MATRIX_FAILED_STOP'
                result['unexpected_case'] = {'method': item['method'], 'path': item['path'], 'auth_mode': mode, 'status': item.get('status'), 'error': item.get('error'), 'unexpected_returned_id': item.get('unexpected_returned_id')}
                result['after'] = take_snapshot()
                write_results(result)
                save_progress(result['status'], 'unauthenticated-write-guard-matrix', 'Unexpected write response; stopped additional writes and captured bounded state.')
                return 1
    result['write_matrix_passed'] = True
    save_progress('RUNNING', 'post-matrix-snapshot', 'Capture final bounded stable state and audit summary.')
    after = take_snapshot()
    result['after'] = after
    result['matrix_request_count'] = len(result['matrix'])
    result['matrix_request_count_expected'] = 76
    result['matrix_all_pass'] = len(result['matrix']) == 76 and all(x['pass'] for x in result['matrix'])
    result['stable_state_unchanged'] = before == after
    result['active_executions_after'] = after['executions'].get('active_executions', [])
    result['audit_unchanged'] = before['audit'] == after['audit']
    result['state_change_attribution'] = 'No matrix request carried valid credentials; any observed state/audit delta is unattributed unless an exact owned execution proves its source.'
    result['status'] = 'PASS_MATRIX_STATE_STABLE' if result['matrix_all_pass'] and result['stable_state_unchanged'] else 'PASS_HTTP_MATRIX_STATE_CHANGED_OR_CONCURRENT'
    result['finished_at_utc'] = utc()
    write_results(result)
    save_progress(result['status'], 'complete', 'Report results to Root; canonical AUTH-MANAGEMENT remains PARTIAL for expired session and role dimensions.')
    return 0 if result['matrix_all_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
