import hashlib
import json
import shlex
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
CONNECTION_PATH = PRIVATE / 'repair-connection.json'
SOURCE_PATH = HERE / 'native2_readonly_preflight.py'
RESULT_PATH = HERE / 'native2-preflight-result.json'
LEDGER_PATH = HERE / 'native2-preflight-task-ledger.json'
PROGRESS_PATH = HERE / 'native2-preflight-progress.json'
NODE = '611c02a5-1859-4465-878b-d543b7f6fdec'
TASK_PREFIX = 'qa-resume-20261010-sys12-readonly-preflight-'
APPROVED_SOURCE_SHA = '53ecdaa900297673794a64b6091c95a87507826e89357def7ba604af5e1f5fc7'
APPROVED_COMMAND_SHA = 'b9187e9ac47e677d43aeec04932d753fed82e94b3b7debcb4d2dc102347b2209'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_connection():
    import os
    import stat
    st = CONNECTION_PATH.lstat()
    if not stat.S_ISREG(st.st_mode) or CONNECTION_PATH.is_symlink() or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_guard_failed')
    fd = os.open(CONNECTION_PATH, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino, fs.st_uid, stat.S_IMODE(fs.st_mode)) != (st.st_dev, st.st_ino, st.st_uid, 0o600):
            raise RuntimeError('private_connection_changed_during_open')
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise RuntimeError('private_connection_size_guard_failed')
    conn = json.loads(raw)
    base = conn.get('hub_url', '').rstrip('/')
    token = conn.get('token')
    if urlsplit(base).port != 18080 or not isinstance(token, str) or not token:
        raise RuntimeError('repair_connection_shape_guard_failed')
    return base, token


def request(method, path, token, body=None, timeout=12, limit=2 * 1024 * 1024):
    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
    req = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=timeout) as r:
            code, raw = r.status, r.read(limit + 1)
    except HTTPError as exc:
        code, raw = exc.code, exc.read(limit + 1)
    if len(raw) > limit:
        return code, None, {'bytes_gt_limit': True}
    try:
        obj = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        obj = None
    return code, obj, {'bytes': len(raw), 'json_type': type(obj).__name__ if obj is not None else 'empty_or_invalid'}


def rows(obj):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ('tasks', 'items'):
            if isinstance(obj.get(key), list):
                return obj[key]
    return None


def node_summary(obj):
    if not isinstance(obj, dict):
        return {'id_matches': False, 'shape': type(obj).__name__}
    return {'id_matches': obj.get('id') == NODE, 'status': obj.get('status'),
            'sync_status': obj.get('sync_status'),
            'agent_id_present': isinstance(obj.get('agent_id'), str) and bool(obj.get('agent_id'))}


BASE, ADMIN = load_connection()
OPENER = build_opener(ProxyHandler({}), NoRedirect())
source = SOURCE_PATH.read_text()
compile(source, str(SOURCE_PATH), 'exec')
source_sha = sha(source.encode())
remote_command = 'python3 -c ' + shlex.quote(source)
command_sha = sha(remote_command.encode())
if source_sha != APPROVED_SOURCE_SHA or command_sha != APPROVED_COMMAND_SHA:
    raise SystemExit('approved_source_or_command_sha_mismatch')

task_name = TASK_PREFIX + uuid.uuid4().hex[:12]
task_id = None
execution_id = None
ledger = {'started_at_utc': utc(), 'authority_port': 18080, 'target_node_id': NODE,
          'task_name': task_name, 'task_create_count': 0, 'run_count': 0,
          'retry': 0, 'timeout_seconds': 90, 'source_sha256': source_sha,
          'command_sha256': command_sha, 'source_bytes': len(source.encode()),
          'command_bytes': len(remote_command.encode()), 'credentials_persisted': False}
result = {'status': 'PREFLIGHT', 'captured_at_utc': utc(), 'authority_port': 18080,
          'target_node_id': NODE, 'source_sha256': source_sha,
          'command_sha256': command_sha, 'credentials_persisted': False, 'evidence': {}}
save(PROGRESS_PATH, {'updated_at_utc': utc(), 'status': 'PREFLIGHT', 'next_action': 'fresh Native2 and Task-name read guards'})
save(LEDGER_PATH, ledger)

try:
    code, node, meta = request('GET', '/api/nodes/' + NODE, ADMIN)
    summary = node_summary(node)
    result['evidence']['node_preflight'] = {'http_status': code, 'metadata': meta, 'node': summary}
    if code != 200 or not summary['id_matches'] or summary.get('status') != 'online' or summary.get('sync_status') != 'synced':
        raise RuntimeError('exact_native2_online_synced_preflight_failed')
    code, task_list, meta = request('GET', '/api/tasks', ADMIN)
    task_rows = rows(task_list)
    collision = any(isinstance(x, dict) and x.get('name') == task_name for x in (task_rows or []))
    result['evidence']['task_name_preflight'] = {'http_status': code, 'metadata': meta,
                                                  'collection_shape_ok': task_rows is not None,
                                                  'name_collision': collision}
    if code != 200 or task_rows is None or collision:
        raise RuntimeError('helper_task_name_preflight_failed')

    save(PROGRESS_PATH, {'updated_at_utc': utc(), 'status': 'CREATING_EXACT_READONLY_HELPER',
                         'next_action': 'run once against exact Native2 node'})
    code, created, meta = request('POST', '/api/tasks', ADMIN, {
        'name': task_name, 'type': 'command', 'command': remote_command,
        'target': {'type': 'node', 'node_ids': [NODE]}, 'parameters': [],
        'timeout': 90, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True,
    })
    ledger['task_create_count'] += 1
    result['evidence']['task_create'] = {'http_status': code, 'metadata': meta,
                                          'response_shape': type(created).__name__}
    if code not in (200, 201) or not isinstance(created, dict) or not isinstance(created.get('id'), str):
        raise RuntimeError('readonly_helper_task_create_failed_or_shape_invalid')
    task_id = created['id']
    ledger['task_id'] = task_id
    code, readback, meta = request('GET', '/api/tasks/' + task_id, ADMIN)
    target = readback.get('target', {}) if isinstance(readback, dict) else {}
    command_match = isinstance(readback, dict) and sha(str(readback.get('command', '')).encode()) == command_sha
    task_ok = (isinstance(readback, dict) and readback.get('id') == task_id
               and readback.get('name') == task_name and readback.get('type') == 'command'
               and command_match and isinstance(target, dict) and target.get('node_ids') == [NODE]
               and readback.get('retry') == 0 and readback.get('timeout') == 90)
    result['evidence']['task_readback'] = {'http_status': code, 'metadata': meta,
                                            'exact_task_match': task_ok,
                                            'command_sha256_matches': command_match,
                                            'target_node_ids': target.get('node_ids') if isinstance(target, dict) else None,
                                            'retry': readback.get('retry') if isinstance(readback, dict) else None,
                                            'timeout': readback.get('timeout') if isinstance(readback, dict) else None}
    if code != 200 or not task_ok:
        raise RuntimeError('exact_helper_task_readback_failed')
    ledger['task_readback_verified'] = True
    save(LEDGER_PATH, ledger)

    ledger['run_count'] = 1
    code, started, meta = request('POST', '/api/tasks/' + task_id + '/run', ADMIN, {'params': {}}, timeout=12)
    started_rows = started if isinstance(started, list) else (
        started.get('executions') if isinstance(started, dict) and isinstance(started.get('executions'), list)
        else ([started] if isinstance(started, dict) and started.get('id') else []))
    result['evidence']['run_start'] = {'http_status': code, 'metadata': meta,
                                       'response_shape': type(started).__name__,
                                       'execution_count': len(started_rows)}
    if (code not in (200, 201) or len(started_rows) != 1
            or not isinstance(started_rows[0], dict) or not isinstance(started_rows[0].get('id'), str)):
        raise RuntimeError('single_readonly_helper_run_failed_or_shape_invalid')
    execution_id = started_rows[0]['id']
    ledger['execution_id'] = execution_id
    save(LEDGER_PATH, ledger)
    save(PROGRESS_PATH, {'updated_at_utc': utc(), 'status': 'RUNNING_NATIVE2_READONLY_PREFLIGHT',
                         'task_id': task_id, 'execution_id': execution_id,
                         'next_action': 'poll this execution only, then exact Task cleanup'})
    deadline = time.monotonic() + 105
    terminal = None
    while time.monotonic() < deadline:
        code, current, meta = request('GET', '/api/executions/' + execution_id, ADMIN, timeout=12)
        if code != 200 or not isinstance(current, dict) or current.get('id') != execution_id:
            raise RuntimeError('exact_helper_execution_readback_failed')
        if current.get('status') in {'SUCCESS', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}:
            terminal = current
            break
        time.sleep(0.5)
    if terminal is None:
        raise RuntimeError('readonly_helper_execution_terminal_timeout')
    result['evidence']['execution'] = {'http_status': code, 'id_matches': terminal.get('id') == execution_id,
                                        'task_id_matches': terminal.get('task_id') == task_id,
                                        'node_id': terminal.get('node_id'), 'status': terminal.get('status')}
    code, logs_obj, meta = request('GET', '/api/executions/' + execution_id + '/logs', ADMIN, limit=512 * 1024)
    log_rows = logs_obj if isinstance(logs_obj, list) else (
        logs_obj.get('logs') if isinstance(logs_obj, dict) and isinstance(logs_obj.get('logs'), list) else None)
    stdout = ''
    if log_rows is not None:
        chunks = sorted((x for x in log_rows if isinstance(x, dict) and x.get('stream') == 'stdout'),
                        key=lambda x: (x.get('seq', 0), x.get('created_at', '')))
        stdout = ''.join(x.get('chunk', '') for x in chunks if isinstance(x.get('chunk'), str))
    try:
        remote = json.loads(stdout) if stdout else None
    except ValueError:
        remote = None
    result['evidence']['logs'] = {'http_status': code, 'metadata': meta,
                                   'collection_shape_ok': log_rows is not None}
    result['evidence']['native2_preflight'] = remote if isinstance(remote, dict) else {
        'status': 'stdout_missing_or_invalid_json', 'stdout_bytes': len(stdout.encode()),
        'stdout_sha256': sha(stdout.encode())}
    result['status'] = 'HELPER_TERMINAL_' + str(terminal.get('status'))
except Exception as exc:
    result['status'] = 'ABORTED_OR_PARTIAL'
    result['safe_error'] = type(exc).__name__ + ':' + str(exc)[:120]
finally:
    if task_id:
        try:
            code, _, meta = request('DELETE', '/api/tasks/' + task_id, ADMIN, timeout=12)
            result['evidence']['task_delete'] = {'http_status': code, 'metadata': meta}
        except Exception as exc:
            result['evidence']['task_delete'] = {'status': 'error', 'safe_error': type(exc).__name__}
        try:
            code, _, meta = request('GET', '/api/tasks/' + task_id, ADMIN, timeout=12)
            result['evidence']['task_get_after_delete'] = {'http_status': code, 'metadata': meta,
                                                            'is_404': code == 404}
        except Exception as exc:
            result['evidence']['task_get_after_delete'] = {'status': 'error', 'safe_error': type(exc).__name__}
    ledger.update({'finished_at_utc': utc(), 'task_delete': result['evidence'].get('task_delete'),
                   'task_get_after_delete': result['evidence'].get('task_get_after_delete'),
                   'execution_retained': execution_id is not None, 'credentials_persisted': False})
    save(LEDGER_PATH, ledger)
    result['captured_at_utc'] = utc()
    result['credentials_persisted'] = False
    save(RESULT_PATH, result)
    save(PROGRESS_PATH, {'updated_at_utc': utc(), 'status': result['status'],
                         'task_id': task_id, 'execution_id': execution_id,
                         'task_get404': result.get('evidence', {}).get('task_get_after_delete', {}).get('is_404'),
                         'next_action': 'review factual preflight; no Docker or enrollment action authorized'})
