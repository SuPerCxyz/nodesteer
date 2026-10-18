#!/usr/bin/env python3
"""Enroll and start only the parent-approved isolated Native-1 child Agent."""
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
OUT = HERE / 'child-setup-evidence.json'
PARENT_NODE = '91f44866-8a28-435e-bbda-2f2367126c00'
NODE_NAME = 'qa-resume-20261009-native1-p0-002-child-870358cd92ee'
NODE_IP = 'qa-resume-20261009-native1-p0-002-870358cd92ee.example.test'
ROOT = '/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee'
CHILD_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service'

conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
creds = json.loads((PRIVATE / 'repair-credentials.json').read_text())
base, admin_token = conn['hub_url'].rstrip('/'), conn['token']
registration_token = creds['registration_token']
if urlsplit(base).port != 18080:
    raise SystemExit('blocked: repair endpoint is not :18080')


def api(method, path, body=None, allowed=(200,)):
    headers = {'Authorization': 'Bearer ' + admin_token}
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


def node_summary(node):
    return {k: node.get(k) for k in ['id', 'hostname', 'ip', 'status', 'sync_status',
                                     'agent_version', 'deployment_mode', 'host_integration']}


def get_node(node_id):
    _, node = api('GET', '/api/nodes/' + node_id)
    return node_summary(node)


def wait_child(node_id, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = get_node(node_id)
        except RuntimeError:
            time.sleep(1)
            continue
        if last.get('status') == 'online' and last.get('sync_status') == 'synced':
            return last
        time.sleep(1)
    return last


_, nodes = api('GET', '/api/nodes')
if any(n.get('hostname') == NODE_NAME or n.get('ip') == NODE_IP for n in nodes):
    raise SystemExit('blocked: exact child node name/IP already exists')
evidence = {
    'run_id': '20260930-101945',
    'case_id': 'E2E-20260930-101945-P0-002',
    'stage': 'CHILD_SETUP_AND_PREFLIGHT_ONLY',
    'fault_injection_authorized': False,
    'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                       'only_repair_18080': True},
    'parent_node_id': PARENT_NODE,
    'node_name': NODE_NAME,
    'node_ip': NODE_IP,
    'owner_nonce': '870358cd92ee',
    'qa_root': ROOT,
    'child_unit': CHILD_UNIT,
    'agent_binary': '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent',
    'agent_binary_sha256_required': 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add',
    'node_name_ip_collision_check': 'clear',
}
node_id = agent_id = task_id = execution_id = None
registration_token = None
setup_run_submitted = False
try:
    enrollment_status, enrollment = api('POST', '/api/nodes/enrollment', {
        'node_name': NODE_NAME,
        'node_ip': NODE_IP,
        'hub_address': base,
    }, allowed=(200, 201))
    node_id = enrollment['node_id']
    agent_id = enrollment['agent_id']
    # The enrollment response contains only node identity and gateway URL. Use the
    # already authorized private registration credential in memory; never persist it.
    registration_token = creds['registration_token']
    gateway_url = enrollment['gateway_url']
    gateway = urlsplit(gateway_url)
    if gateway.scheme not in {'ws', 'wss'} or gateway.hostname != urlsplit(base).hostname:
        raise RuntimeError('enrollment returned an unexpected repair gateway origin')
    evidence['node_id'] = node_id
    evidence['agent_id'] = agent_id
    evidence['gateway_origin'] = {'scheme': gateway.scheme, 'host': gateway.hostname,
                                  'port': gateway.port, 'source': 'this enrollment response'}
    evidence['enrollment_http_status'] = enrollment_status
    evidence['registration_token_persisted_in_evidence'] = False

    source = (HERE / 'child-setup-source.py').read_text()
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    command = 'python3 -c ' + shlex.quote(source)
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    task = {
        'name': 'qa-resume-20261009-native1-child-setup-870358cd92ee',
        'type': 'command',
        'target': {'type': 'node', 'node_ids': [PARENT_NODE]},
        'parameters': [
            {'name': 'agent_id', 'type': 'string', 'required': True},
            {'name': 'node_id', 'type': 'string', 'required': True},
            {'name': 'node_name', 'type': 'string', 'required': True},
            {'name': 'node_ip', 'type': 'string', 'required': True},
            {'name': 'hub_url', 'type': 'string', 'required': True},
            {'name': 'registration_token', 'type': 'secret', 'required': True},
        ],
        'command': command,
        'timeout': 60,
        'retry': 0,
        'offline_policy': 'hub_online_required',
        'enabled': True,
    }
    evidence['setup_source_file'] = 'child-setup-source.py'
    evidence['setup_source_sha256'] = source_sha
    evidence['task_command_sha256'] = command_sha
    evidence['task_definition'] = {k: v for k, v in task.items() if k != 'command'}
    evidence['task_definition']['command_sha256'] = command_sha
    _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
    task_id = created['id']
    evidence['task_id'] = task_id
    evidence['task_definition']['id'] = task_id
    _, fetched = api('GET', '/api/tasks/' + task_id)
    readback_sha = hashlib.sha256(fetched.get('command', '').encode()).hexdigest()
    evidence['task_readback'] = {
        'id': fetched.get('id'), 'name': fetched.get('name'), 'type': fetched.get('type'),
        'target': fetched.get('target'), 'timeout': fetched.get('timeout'),
        'retry': fetched.get('retry'), 'offline_policy': fetched.get('offline_policy'),
        'parameters': [{'name': p.get('name'), 'type': p.get('type'),
                        'required': p.get('required')} for p in fetched.get('parameters', [])],
        'command_sha256': readback_sha,
    }
    if readback_sha != command_sha:
        raise RuntimeError('child setup Task command readback SHA mismatch')
    run_params = {
        'agent_id': agent_id,
        'node_id': node_id,
        'node_name': NODE_NAME,
        'node_ip': NODE_IP,
        'hub_url': gateway_url,
        'registration_token': registration_token,
    }
    setup_run_submitted = True
    _, execs = api('POST', '/api/tasks/' + task_id + '/run', {'params': run_params},
                   allowed=(200, 201))
    if not execs:
        raise RuntimeError('child setup Task returned no Execution')
    execution_id = execs[0]['id']
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
        evidence['execution'] = {
            k: final.get(k) for k in ['id', 'task_id', 'node_id', 'status',
                                       'exit_code', 'trigger_type', 'started_at', 'ended_at']
            if k in final
        }
        evidence['execution']['stdout_json'] = json.loads(stdout) if stdout else None
        evidence['execution']['stdout_sha256'] = hashlib.sha256(stdout.encode()).hexdigest()
        evidence['execution']['stderr_bytes'] = len((final.get('stderr') or '').encode())
    evidence['child_api_state'] = wait_child(node_id)
    evidence['child_online_synced'] = bool(
        evidence['child_api_state'] and evidence['child_api_state'].get('status') == 'online'
        and evidence['child_api_state'].get('sync_status') == 'synced')
    evidence['stage_status'] = ('CHILD_PREFLIGHT_READY'
                                if evidence.get('execution', {}).get('status') == 'SUCCESS'
                                and evidence['child_online_synced'] else 'CHILD_PREFLIGHT_INCOMPLETE')
finally:
    registration_token = None
    if task_id:
        try:
            status, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
            evidence['task_cleanup'] = {'delete_http_status': status}
            status, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
            evidence['task_cleanup']['verify_get_http_status'] = status
            evidence['task_cleanup']['verified_404'] = status == 404
        except Exception as exc:
            evidence['task_cleanup'] = {'error': type(exc).__name__}
    if node_id and not setup_run_submitted:
        # No child setup command was dispatched, so this exact pending enrollment is disposable.
        try:
            status, _ = api('DELETE', '/api/nodes/' + node_id, allowed=(200, 204))
            verify, _ = api('GET', '/api/nodes/' + node_id, allowed=(404,))
            evidence['unused_enrollment_cleanup'] = {
                'delete_http_status': status, 'verify_get_http_status': verify,
                'verified_404': verify == 404,
            }
        except Exception as exc:
            evidence['unused_enrollment_cleanup'] = {'error': type(exc).__name__}
    evidence['completed_unix'] = int(time.time())
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

print(json.dumps({'evidence': str(OUT.relative_to(Path.cwd())),
                  'stage_status': evidence.get('stage_status'),
                  'node_id': evidence.get('node_id'), 'agent_id': evidence.get('agent_id'),
                  'task_id': evidence.get('task_id'), 'execution_id': evidence.get('execution_id'),
                  'child_online_synced': evidence.get('child_online_synced'),
                  'task_deleted_and_404': evidence.get('task_cleanup', {}).get('verified_404'),
                  'credential_in_evidence': False}, ensure_ascii=False))
