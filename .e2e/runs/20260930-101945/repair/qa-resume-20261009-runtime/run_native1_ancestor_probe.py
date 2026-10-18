#!/usr/bin/env python3
"""Run/delete one approved read-only Native-1 parent-chain probe Task."""
import hashlib
import json
import shlex
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = HERE / 'native1-ancestor-probe-evidence.json'
NODE_ID = '91f44866-8a28-435e-bbda-2f2367126c00'

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
    node = next((n for n in nodes if n.get('id') == NODE_ID), None)
    if not node:
        raise RuntimeError('approved Native-1 node disappeared')
    return {k: node.get(k) for k in ['id', 'hostname', 'status', 'sync_status',
                                     'agent_version', 'deployment_mode']}


before = node_state()
if before.get('status') != 'online' or before.get('sync_status') != 'synced':
    raise SystemExit('blocked: Native-1 is not online/synced')
source = (HERE / 'probe-ancestor-source.py').read_text()
source_sha = hashlib.sha256(source.encode()).hexdigest()
command = 'python3 -c ' + shlex.quote(source)
command_sha = hashlib.sha256(command.encode()).hexdigest()
task = {
    'name': 'qa-resume-20261009-native1-ancestor-' + uuid.uuid4().hex[:10],
    'type': 'command',
    'target': {'type': 'node', 'node_ids': [NODE_ID]},
    'command': command,
    'timeout': 60,
    'retry': 0,
    'offline_policy': 'hub_online_required',
    'enabled': True,
}
evidence = {
    'run_id': '20260930-101945', 'case_ids': [],
    'scope_note': 'Read-only process ancestry for the current product Command Task; no process signals or filesystem writes on the Agent host.',
    'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                       'only_repair_18080': True},
    'node_before': before,
    'source_file': 'probe-ancestor-source.py', 'source_sha256': source_sha,
    'task_definition': {k: v for k, v in task.items() if k != 'command'},
    'task_command_sha256': command_sha,
}
task_id = None
try:
    _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
    task_id = created['id']
    evidence['task_id'] = task_id
    evidence['task_definition']['id'] = task_id
    evidence['task_definition']['command_sha256'] = command_sha
    _, fetched = api('GET', '/api/tasks/' + task_id)
    readback_sha = hashlib.sha256(fetched.get('command', '').encode()).hexdigest()
    evidence['task_readback'] = {'id': fetched.get('id'), 'type': fetched.get('type'),
                                 'target': fetched.get('target'), 'timeout': fetched.get('timeout'),
                                 'retry': fetched.get('retry'), 'command_sha256': readback_sha}
    if readback_sha != command_sha:
        raise RuntimeError('task command readback SHA mismatch')
    _, created_executions = api('POST', '/api/tasks/' + task_id + '/run', {'params': {}},
                                allowed=(200, 201))
    if not created_executions:
        raise RuntimeError('task produced no Execution')
    execution_id = created_executions[0]['id']
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
        stdout = final.get('stdout') or ''
        safe = {k: final.get(k) for k in ['id', 'task_id', 'node_id', 'status',
                                            'exit_code', 'trigger_type', 'started_at', 'ended_at']
                if k in final}
        safe['stdout_json'] = json.loads(stdout) if stdout else None
        safe['stdout_sha256'] = hashlib.sha256(stdout.encode()).hexdigest()
        safe['stderr_bytes'] = len((final.get('stderr') or '').encode())
        evidence['execution'] = safe
finally:
    if task_id:
        status, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
        evidence['task_cleanup'] = {'delete_http_status': status}
        status, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
        evidence['task_cleanup']['verify_get_http_status'] = status
        evidence['task_cleanup']['verified_404'] = status == 404
    evidence['node_after'] = node_state()
    evidence['node_state_unchanged'] = evidence['node_after'] == before
    evidence['completed_unix'] = int(time.time())
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

print(json.dumps({'evidence': str(OUT.relative_to(Path.cwd())),
                  'task_id': evidence.get('task_id'),
                  'execution_id': evidence.get('execution_id'),
                  'execution_status': evidence.get('execution', {}).get('status'),
                  'task_deleted_and_404': evidence.get('task_cleanup', {}).get('verified_404'),
                  'node_state_unchanged': evidence.get('node_state_unchanged')}, ensure_ascii=False))
