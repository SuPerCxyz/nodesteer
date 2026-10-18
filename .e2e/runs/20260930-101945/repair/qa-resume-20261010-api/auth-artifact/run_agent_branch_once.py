import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
CONNECTION = json.loads((PRIVATE / 'repair-connection.json').read_text())
BASE = CONNECTION['hub_url'].rstrip('/')
TOKEN = CONNECTION['token']
if urlsplit(BASE).port != 18080:
    raise SystemExit('repair_endpoint_guard_failed')
OPENER = build_opener(ProxyHandler({}))
PARENT = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
CHILD = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
AGENT = 'agent-ab15a133-73cd-4025-8c97-f2ff37e000f5'
ARTIFACT = '7b8929ed-c880-46f1-90e6-a5e9c0dab096'
EXPECTED_SHA = '9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'
EXPECTED_SIZE = 1601698
ORIGINAL = {
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
    '37989297-3bc8-4c0c-b302-7c6efbdba630',
    '611c02a5-1859-4465-878b-d543b7f6fdec',
    '91f44866-8a28-435e-bbda-2f2367126c00',
}

def now():
    return datetime.now(timezone.utc).isoformat()

def api(method, path, body=None, timeout=15):
    headers = {'Authorization': 'Bearer ' + TOKEN}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
    request = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except HTTPError as exc:
        status = exc.code
        raw = exc.read(2048)
    try:
        value = json.loads(raw) if raw else None
    except Exception:
        value = None
    return status, value

def require(status, expected, label):
    if status not in expected:
        raise RuntimeError(label + '_http_' + str(status))

def collection(value, key):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        rows = value.get(key, value.get('items', []))
        if isinstance(rows, list):
            return rows
    raise RuntimeError('unexpected_' + key + '_shape')

preflight = json.loads((HERE / 'agent-branch-preflight.json').read_text())
if not preflight.get('guards_passed'):
    raise SystemExit('saved_preflight_guard_failed')
command_path = HERE / 'helper' / 'embedded-command.sh'
command = command_path.read_text()
command_sha = hashlib.sha256(command.encode()).hexdigest()
compile_record = json.loads((HERE / 'helper' / 'compile-check.json').read_text())
if command_sha != compile_record.get('embedded_command_sha256'):
    raise SystemExit('embedded_command_sha_changed')
name = preflight['proposed_task_name']
ledger = {
    'started_at_utc': now(), 'status': 'PREFLIGHT_RECHECK', 'task_name': name,
    'task_id': None, 'execution_id': None, 'task_created': False,
    'execution_run_count': 0, 'task_cleanup_status': 'NOT_ATTEMPTED',
    'task_get_after_delete_status': None,
}
(HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
progress_path = HERE / 'progress.json'
progress = json.loads(progress_path.read_text())
progress.update({'updated_at': now(), 'status': 'AGENT_BRANCH_PREFLIGHT_RECHECK', 'next_action': 'One exact parent Command Task, one Run, then exact Task delete/GET404.'})
progress_path.write_text(json.dumps(progress, indent=2) + '\n')

task_id = None
execution_id = None
try:
    me_status, me = api('GET', '/api/me')
    require(me_status, {200}, 'me')
    role = me.get('Role', me.get('role')) if isinstance(me, dict) else None
    if role not in ('admin', 'administrator'):
        raise RuntimeError('admin_role_guard_failed')
    nodes_status, nodes_raw = api('GET', '/api/nodes')
    require(nodes_status, {200}, 'nodes')
    nodes = collection(nodes_raw, 'nodes')
    by_id = {str(row.get('id')): row for row in nodes if isinstance(row, dict)}
    parent = by_id.get(PARENT)
    child = by_id.get(CHILD)
    if not parent or parent.get('status') != 'online' or parent.get('sync_status') != 'synced':
        raise RuntimeError('parent_health_guard_failed')
    if not child or child.get('agent_id') != AGENT or child.get('status') != 'online' or child.get('sync_status') != 'synced':
        raise RuntimeError('child_identity_health_guard_failed')
    for node_id in ORIGINAL:
        row = by_id.get(node_id)
        if not row or row.get('status') != 'online' or row.get('sync_status') != 'synced':
            raise RuntimeError('original_four_health_guard_failed')
    artifact_status, artifact = api('GET', '/api/artifacts/' + ARTIFACT)
    require(artifact_status, {200}, 'artifact')
    if isinstance(artifact, dict) and isinstance(artifact.get('artifact'), dict):
        artifact = artifact['artifact']
    if not isinstance(artifact, dict) or (artifact.get('id'), artifact.get('size'), artifact.get('sha256')) != (ARTIFACT, EXPECTED_SIZE, EXPECTED_SHA):
        raise RuntimeError('artifact_metadata_guard_failed')
    tasks_status, tasks_raw = api('GET', '/api/tasks')
    require(tasks_status, {200}, 'tasks')
    tasks = collection(tasks_raw, 'tasks')
    if any(isinstance(row, dict) and row.get('name') == name for row in tasks):
        raise RuntimeError('task_name_collision_before_create')
    schedules_status, schedules_raw = api('GET', '/api/schedules')
    require(schedules_status, {200}, 'schedules')
    schedules = collection(schedules_raw, 'schedules')
    ledger.update({'preflight_recheck_at_utc': now(), 'parent_status': parent.get('status'), 'child_status': child.get('status'), 'child_agent_id_matches': True, 'original_four_healthy': True, 'artifact_metadata_matches': True, 'task_name_unique': True, 'schedules_count_before': len(schedules)})
    ledger['status'] = 'PREFLIGHT_RECHECK_PASS'
    (HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    task_body = {
        'name': name,
        'type': 'command',
        'command': command,
        'target': {'type': 'node', 'node_ids': [PARENT]},
        'parameters': [],
        'timeout': 75,
        'retry': 0,
        'offline_policy': 'hub_online_required',
        'enabled': True,
    }
    create_status, task = api('POST', '/api/tasks', task_body, timeout=20)
    require(create_status, {200, 201}, 'task_create')
    if not isinstance(task, dict) or not task.get('id'):
        raise RuntimeError('task_create_response_missing_id')
    task_id = str(task['id'])
    ledger.update({'task_id': task_id, 'task_created': True, 'task_create_http_status': create_status, 'task_command_sha256': command_sha, 'target_node_id': PARENT, 'task_retry': 0, 'task_timeout': 75, 'schedule_present': False})
    ledger['status'] = 'TASK_CREATED_READBACK_PENDING'
    (HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    read_status, readback = api('GET', '/api/tasks/' + task_id)
    require(read_status, {200}, 'task_readback')
    if not isinstance(readback, dict) or readback.get('id') != task_id or readback.get('type') != 'command' or readback.get('target', {}).get('node_ids') != [PARENT] or readback.get('retry') != 0 or readback.get('timeout') != 75 or hashlib.sha256(readback.get('command', '').encode()).hexdigest() != command_sha:
        raise RuntimeError('exact_task_readback_guard_failed')
    sched_status, sched_raw = api('GET', '/api/schedules')
    require(sched_status, {200}, 'schedules_after_create')
    sched_rows = collection(sched_raw, 'schedules')
    if any(isinstance(row, dict) and row.get('task_id') == task_id for row in sched_rows):
        raise RuntimeError('unexpected_task_schedule')
    run_status, run_response = api('POST', '/api/tasks/' + task_id + '/run', {'params': {}}, timeout=20)
    require(run_status, {200, 201}, 'task_run')
    if isinstance(run_response, list):
        run_rows = run_response
    elif isinstance(run_response, dict):
        run_rows = run_response.get('executions') or ([run_response] if run_response.get('id') else [])
    else:
        run_rows = []
    ledger.update({'run_http_status': run_status, 'execution_run_count': 1})
    if len(run_rows) != 1 or not isinstance(run_rows[0], dict) or not run_rows[0].get('id'):
        ledger['status'] = 'RUN_RESPONSE_UNEXPECTED_SINGLE_RUN_NOT_RETRIED'
        raise RuntimeError('run_response_cardinality_or_id_mismatch')
    execution_id = str(run_rows[0]['id'])
    ledger['execution_id'] = execution_id
    ledger['status'] = 'EXECUTION_POLLING'
    (HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    deadline = time.monotonic() + 78
    final = None
    timeline = []
    while time.monotonic() < deadline:
        execution_status, final = api('GET', '/api/executions/' + execution_id, timeout=12)
        require(execution_status, {200}, 'execution_readback')
        state = final.get('status') if isinstance(final, dict) else None
        if not timeline or timeline[-1]['status'] != state:
            timeline.append({'at_utc': now(), 'status': state})
        if state in {'SUCCESS', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}:
            break
        time.sleep(0.5)
    if not isinstance(final, dict) or final.get('status') not in {'SUCCESS', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}:
        ledger['status'] = 'EXECUTION_TIMEOUT_RESULT_UNKNOWN_NO_RERUN'
        raise RuntimeError('execution_poll_timeout')
    log_status, log_value = api('GET', '/api/executions/' + execution_id + '/logs', timeout=12)
    require(log_status, {200}, 'execution_logs')
    log_rows = log_value if isinstance(log_value, list) else (log_value.get('logs', log_value.get('items', [])) if isinstance(log_value, dict) else [])
    if not isinstance(log_rows, list):
        raise RuntimeError('execution_logs_shape_invalid')
    stdout_chunks = [str(row.get('chunk', '')) for row in log_rows if isinstance(row, dict) and row.get('stream') == 'stdout']
    stderr_chunks = [str(row.get('chunk', '')) for row in log_rows if isinstance(row, dict) and row.get('stream') == 'stderr']
    stdout = ''.join(stdout_chunks)
    stderr = ''.join(stderr_chunks)
    parsed = None
    try:
        parsed = json.loads(stdout.strip())
    except Exception:
        pass
    allowed_fields = {'status', 'owner_guard_passed', 'correct_agent', 'wrong_agent_id'}
    safe_result = None
    if isinstance(parsed, dict) and set(parsed).issubset(allowed_fields):
        safe_result = parsed
    evidence = {
        'captured_at_utc': now(), 'task_id': task_id, 'execution_id': execution_id,
        'execution_status': final.get('status'), 'execution_node_id': final.get('node_id'),
        'execution_task_id': final.get('task_id'), 'timeline': timeline,
        'log_chunk_count': len(log_rows), 'stdout_bytes': len(stdout.encode()), 'stderr_bytes': len(stderr.encode()),
        'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(), 'stderr_sha256': hashlib.sha256(stderr.encode()).hexdigest(),
        'safe_helper_result': safe_result,
        'raw_logs_persisted': False, 'credentials_or_response_headers_persisted': False,
    }
    (HERE / 'agent-branch-execution-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    ledger.update({'execution_status': final.get('status'), 'helper_result_status': safe_result.get('status') if safe_result else None, 'execution_evidence': 'agent-branch-execution-evidence.json', 'status': 'EXECUTION_RESULT_CAPTURED'})
    (HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
finally:
    if task_id is not None:
        ledger['cleanup_attempted_at_utc'] = now()
        try:
            delete_status, _ = api('DELETE', '/api/tasks/' + task_id, timeout=15)
            ledger['task_cleanup_http_status'] = delete_status
            ledger['task_cleanup_status'] = 'DELETE_ACCEPTED' if delete_status in {200, 202, 204} else 'DELETE_UNEXPECTED_STATUS'
            gone_status, gone = api('GET', '/api/tasks/' + task_id, timeout=15)
            ledger['task_get_after_delete_status'] = gone_status
            if gone_status == 404:
                ledger['task_cleanup_status'] = 'DELETE_GET404_CONFIRMED'
            else:
                ledger['task_cleanup_status'] = 'TASK_STILL_PRESENT_OR_GET_UNEXPECTED'
                ledger['task_get_after_delete_shape'] = type(gone).__name__
        except Exception as exc:
            ledger['task_cleanup_status'] = 'CLEANUP_ERROR_' + type(exc).__name__
        ledger['finished_at_utc'] = now()
        (HERE / 'agent-branch-task-ledger.json').write_text(json.dumps(ledger, indent=2) + '\n')
    progress = json.loads(progress_path.read_text())
    progress.update({
        'updated_at': now(),
        'status': ledger.get('status', 'AGENT_BRANCH_STOPPED'),
        'stage': 'agent-credential-branch-single-run-attempted',
        'next_action': 'Review safe execution evidence and exact Task cleanup tombstone; no rerun.' if task_id else 'Review preflight guard evidence; no Task was created.',
        'agent_branch_task_ledger': 'agent-branch-task-ledger.json',
    })
    progress_path.write_text(json.dumps(progress, indent=2) + '\n')

print(json.dumps({
    'status': ledger.get('status'), 'task_id': task_id, 'execution_id': execution_id,
    'execution_status': ledger.get('execution_status'), 'helper_result_status': ledger.get('helper_result_status'),
    'task_cleanup_status': ledger.get('task_cleanup_status'), 'task_get_after_delete_status': ledger.get('task_get_after_delete_status'),
}, indent=2))
