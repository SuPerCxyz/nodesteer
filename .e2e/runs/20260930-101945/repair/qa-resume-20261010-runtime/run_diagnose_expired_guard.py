#!/usr/bin/env python3
"""Create/run/delete one approved read-only Native-1 Command Task."""
import hashlib
import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = HERE / 'diagnose-expired-guard-evidence.json'
NODE_ID = '91f44866-8a28-435e-bbda-2f2367126c00'
NODE_NAME = 'qa-repair-20260930-101945-native-1'

conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
base, token = conn['hub_url'].rstrip('/'), conn['token']
if urlsplit(base).port != 18080:
    raise SystemExit('blocked: repair endpoint is not :18080')


def api(method, path, body=None, allowed=(200,)):
    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=15) as response:
            status, payload = response.status, response.read()
    except HTTPError as exc:
        status, payload = exc.code, exc.read()
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('API transport failed: ' + type(exc).__name__)
    if status not in allowed:
        raise RuntimeError(f'{method} {path}: unexpected HTTP {status}')
    if not payload:
        return status, None
    try:
        return status, json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return status, {'payload_bytes': len(payload),
                        'payload_sha256': hashlib.sha256(payload).hexdigest()}


def node_state():
    _, nodes = api('GET', '/api/nodes')
    n = next((n for n in nodes if n.get('id') == NODE_ID), None)
    if not n:
        raise RuntimeError('approved Native-1 node disappeared')
    return {k: n.get(k) for k in ['id', 'hostname', 'status', 'sync_status',
                                   'deployment_mode', 'host_integration',
                                   'agent_version', 'version']}


before_node = node_state()
if before_node.get('status') != 'online' or before_node.get('sync_status') != 'synced':
    raise SystemExit('blocked: Native-1 is not online/synced')
source = (HERE / 'diagnose_expired_guard_source.py').read_text()
source_sha = hashlib.sha256(source.encode()).hexdigest()
command = 'python3 -c ' + __import__('shlex').quote(source)
command_sha = hashlib.sha256(command.encode()).hexdigest()
task_name = 'qa-resume-20261010-native1-owned-child-preflight-' + uuid.uuid4().hex[:6]
task = {
    'name': task_name,
    'type': 'command',
    'target': {'type': 'node', 'node_ids': [NODE_ID]},
    'command': command,
    'timeout': 60,
    'retry': 0,
    'offline_policy': 'hub_online_required',
    'enabled': True,
}

evidence = {
    'run_id': '20260930-101945',
    'case_ids': ['BE-SCRIPT-SHA-002'],
    'scope_note': 'Read-only exact branch diagnostic for the previously failed expired-Unit recreate owner guard; no service/process/config/database mutation.',
    'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                       'only_repair_18080': True},
    'node_before': before_node,
    'source_file': 'probe-source.py',
    'source_sha256': source_sha,
    'task_definition': {k: v for k, v in task.items() if k != 'command'},
    'task_command_sha256': command_sha,
    'task_command_source_sha256': source_sha,
    'status': 'CREATING_TASK',
}
task_id = None
try:
    _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
    task_id = created['id']
    evidence['task_id'] = task_id
    evidence['task_definition']['id'] = task_id
    evidence['task_definition']['command_sha256'] = command_sha
    _, fetched = api('GET', '/api/tasks/' + task_id)
    evidence['task_readback'] = {
        'id': fetched.get('id'), 'name': fetched.get('name'),
        'type': fetched.get('type'), 'target': fetched.get('target'),
        'timeout': fetched.get('timeout'), 'retry': fetched.get('retry'),
        'offline_policy': fetched.get('offline_policy'),
        'enabled': fetched.get('enabled'),
        'command_sha256': hashlib.sha256(fetched.get('command', '').encode()).hexdigest(),
    }
    if evidence['task_readback']['command_sha256'] != command_sha:
        raise RuntimeError('task command readback SHA mismatch')
    evidence['status'] = 'RUNNING_ONCE'
    _, executions = api('POST', '/api/tasks/' + task_id + '/run', {'params': {}},
                        allowed=(200, 201))
    if not executions:
        raise RuntimeError('task produced no Execution')
    execution_id = executions[0]['id']
    evidence['execution_id'] = execution_id
    deadline = time.monotonic() + 70
    final = None
    while time.monotonic() < deadline:
        _, final = api('GET', '/api/executions/' + execution_id)
        if final.get('status') in {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED',
                                   'CANCELED', 'TIMED_OUT'}:
            break
        time.sleep(1)
    if not final or final.get('status') not in {'SUCCESS', 'FAILED', 'SKIPPED',
                                                'BLOCKED', 'CANCELED', 'TIMED_OUT'}:
        evidence['execution'] = {'status': 'TIMEOUT_WAITING'}
    else:
        safe_fields = ['id', 'task_id', 'node_id', 'status', 'exit_code',
                       'trigger_type', 'started_at', 'ended_at']
        evidence['execution'] = {k: final.get(k) for k in safe_fields if k in final}
        stdout = final.get('stdout') or ''
        evidence['execution']['stdout_json'] = json.loads(stdout) if stdout else None
        evidence['execution']['stdout_sha256'] = hashlib.sha256(
            stdout.encode()).hexdigest()
        evidence['execution']['stderr_bytes'] = len(final.get('stderr', '').encode())
        evidence['status'] = 'EXECUTION_' + str(final.get('status'))
finally:
    if task_id:
        status, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
        evidence['task_cleanup'] = {'delete_http_status': status}
        try:
            status, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
            evidence['task_cleanup']['verify_get_http_status'] = status
            evidence['task_cleanup']['verified_404'] = status == 404
        except Exception as exc:
            evidence['task_cleanup']['verify_error'] = type(exc).__name__
    evidence['node_after'] = node_state()
    evidence['node_state_unchanged'] = evidence['node_after'] == before_node
    evidence['completed_at_unix'] = int(time.time())
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

print(json.dumps({
    'evidence': str(OUT.relative_to(Path.cwd())),
    'status': evidence.get('status'),
    'node_id': NODE_ID,
    'task_id': evidence.get('task_id'),
    'execution_id': evidence.get('execution_id'),
    'node_state_unchanged': evidence.get('node_state_unchanged'),
    'task_deleted_and_404': evidence.get('task_cleanup', {}).get('verified_404'),
}, ensure_ascii=False))
