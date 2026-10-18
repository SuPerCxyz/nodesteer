#!/usr/bin/env python3
"""Create the approved real v1 artifact/application, deploy only to the QA child, and verify physical state."""
import hashlib
import json
import shlex
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = HERE / 'app-v1-baseline-evidence.json'
NODE_ID = 'c77b8a84-8085-4ea8-8532-de171761854b'
NODE_NAME = 'qa-resume-20261009-native1-p0-002-child-870358cd92ee'
PARENT_NODE = '91f44866-8a28-435e-bbda-2f2367126c00'
NONCE = '870358cd92ee'
ROOT = '/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee'
APP_NAME = 'qa-resume-20261009-native1-p0-002-app-870358cd92ee'
ARTIFACT_NAME = 'qa-resume-20261009-native1-p0-002-artifact-870358cd92ee'
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
APP_BIN = ROOT + '/app/bin/qa-app'
APP_CONFIG = ROOT + '/app/config/qa-app.env'

conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
base, token = conn['hub_url'].rstrip('/'), conn['token']
if urlsplit(base).port != 18080:
    raise SystemExit('blocked: repair endpoint is not :18080')
opener = build_opener(ProxyHandler({}))


def api(method, path, body=None, headers=None, raw=None, allowed=(200,)):
    request_headers = {'Authorization': 'Bearer ' + token}
    request_headers.update(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        request_headers['Content-Type'] = 'application/json'
    req = Request(base + path, data=data, headers=request_headers, method=method)
    try:
        with opener.open(req, timeout=20) as response:
            status, payload = response.status, response.read()
    except HTTPError as exc:
        status, payload = exc.code, exc.read()
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('repair API transport failed: ' + type(exc).__name__)
    if status not in allowed:
        raise RuntimeError(f'{method} {path}: unexpected HTTP {status}')
    if not payload:
        return status, None
    try:
        return status, json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return status, {'payload_bytes': len(payload),
                        'payload_sha256': hashlib.sha256(payload).hexdigest()}


def node_state(node_id):
    _, node = api('GET', '/api/nodes/' + node_id)
    return {k: node.get(k) for k in ['id', 'hostname', 'ip', 'status',
                                     'sync_status', 'agent_version', 'deployment_mode']}


def wait_node_sync(timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = node_state(NODE_ID)
        if last.get('status') == 'online' and last.get('sync_status') == 'synced':
            return last
        time.sleep(1)
    return last


def run_task_once(name, source_file, target_id, parameters=None, source_values=None):
    source = (HERE / source_file).read_text()
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    command = 'python3 -c ' + shlex.quote(source)
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    task = {
        'name': name,
        'type': 'command',
        'target': {'type': 'node', 'node_ids': [target_id]},
        'parameters': parameters or [],
        'command': command,
        'timeout': 60,
        'retry': 0,
        'offline_policy': 'hub_online_required',
        'enabled': True,
    }
    _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
    task_id = created['id']
    _, fetched = api('GET', '/api/tasks/' + task_id)
    readback_sha = hashlib.sha256(fetched.get('command', '').encode()).hexdigest()
    if readback_sha != command_sha:
        raise RuntimeError('Task command readback SHA mismatch')
    result = {'task_id': task_id, 'source_file': source_file,
              'source_sha256': source_sha, 'command_sha256': command_sha,
              'definition': {k: fetched.get(k) for k in
                             ['id', 'name', 'type', 'target', 'timeout', 'retry',
                              'offline_policy', 'parameters', 'enabled']}}
    try:
        _, executions = api('POST', '/api/tasks/' + task_id + '/run',
                            {'params': source_values or {}}, allowed=(200, 201))
        if not executions:
            raise RuntimeError('Task returned no Execution')
        execution_id = executions[0]['id']
        result['execution_id'] = execution_id
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
            result['execution'] = {'status': 'TIMEOUT_WAITING'}
        else:
            stdout = final.get('stdout') or ''
            result['execution'] = {k: final.get(k) for k in
                                   ['id', 'task_id', 'node_id', 'status',
                                    'exit_code', 'trigger_type', 'started_at', 'ended_at']
                                   if k in final}
            result['execution']['stdout_json'] = json.loads(stdout) if stdout else None
            result['execution']['stdout_sha256'] = hashlib.sha256(stdout.encode()).hexdigest()
            result['execution']['stderr_bytes'] = len((final.get('stderr') or '').encode())
    finally:
        status, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
        status2, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
        result['cleanup'] = {'delete_http_status': status,
                             'verify_get_http_status': status2,
                             'verified_404': status2 == 404}
    return result


def upload_artifact(name, version, filename, data):
    boundary = 'qa' + uuid.uuid4().hex
    fields = {'name': name, 'version': version, 'architecture': 'amd64'}
    body = b''
    for key, value in fields.items():
        body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key +
                 '"\r\n\r\n' + value + '\r\n').encode()
    body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' +
             filename + '"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += data + ('\r\n--' + boundary + '--\r\n').encode()
    _, value = api('POST', '/api/artifacts', raw=body,
                   headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
                   allowed=(200, 201))
    return value


def wait_execution(execution_id, timeout=90):
    deadline = time.monotonic() + timeout
    final = None
    while time.monotonic() < deadline:
        _, final = api('GET', '/api/executions/' + execution_id)
        if final.get('status') in {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED',
                                   'CANCELED', 'TIMED_OUT'}:
            return final
        time.sleep(1)
    raise RuntimeError('Application Deployment Execution did not reach terminal status')


before_node = node_state(NODE_ID)
if before_node.get('status') != 'online' or before_node.get('sync_status') != 'synced':
    raise SystemExit('blocked: owned child is not online/synced')

evidence = {
    'run_id': '20260930-101945',
    'case_id': 'E2E-20260930-101945-P0-002',
    'stage': 'REAL_V1_APP_ARTIFACT_DEPLOY_AND_PHYSICAL_BASELINE',
    'fault_injection_authorized': False,
    'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                       'only_repair_18080': True},
    'child_node_id': NODE_ID,
    'child_before': before_node,
    'owner_nonce': NONCE,
    'qa_root': ROOT,
    'app_name': APP_NAME,
    'artifact_name': ARTIFACT_NAME,
    'app_unit_name': APP_UNIT,
    'app_binary_path': APP_BIN,
    'app_config_path': APP_CONFIG,
    'stage_status': 'STARTED',
}

# Product-targeted read-only preflight: verify owner, current process, and all app paths/Unit absent.
preflight = run_task_once('qa-resume-20261009-p0-002-app-path-preflight-' + NONCE,
                          'app-preflight-source.py', NODE_ID)
evidence['target_preflight_task'] = preflight
preflight_result = preflight.get('execution', {}).get('stdout_json')
if preflight.get('execution', {}).get('status') != 'SUCCESS' or not preflight_result or preflight_result.get('preflight') != 'PASS':
    evidence['stage_status'] = 'BLOCKED_TARGET_PREFLIGHT'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('blocked: exact owned Application paths/unit did not pass preflight')

_, existing_apps = api('GET', '/api/applications')
_, existing_artifacts = api('GET', '/api/artifacts')
if any(a.get('name') == APP_NAME for a in existing_apps):
    raise SystemExit('blocked: unique Application name already exists')
if any(a.get('name') == ARTIFACT_NAME for a in existing_artifacts):
    raise SystemExit('blocked: unique Artifact name already exists')

v1_path = HERE / 'qa-app-v1.sh'
v1_data = v1_path.read_bytes()
v1_sha = hashlib.sha256(v1_data).hexdigest()
config_v1 = f'QA_P002_VERSION=v1\nQA_P002_NONCE={NONCE}\n'
config_sha = hashlib.sha256(config_v1.encode()).hexdigest()
artifact = upload_artifact(ARTIFACT_NAME, 'v1', v1_path.name, v1_data)
evidence['v1_artifact'] = {k: artifact.get(k) for k in
                           ['id', 'name', 'version', 'architecture', 'filename', 'size', 'sha256']}
evidence['v1_artifact']['source_file'] = v1_path.name
evidence['v1_artifact']['source_sha256'] = v1_sha
if artifact.get('sha256') != v1_sha or int(artifact.get('size', -1)) != len(v1_data):
    evidence['stage_status'] = 'FAIL_ARTIFACT_METADATA_MISMATCH'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('Artifact SHA/size does not match exact local v1 payload')

app_body = {
    'name': APP_NAME,
    'description': 'Isolated runtime fixture for P0-002 PREPARING crash recovery.',
    'artifact_id': artifact['id'],
    'version': 'v1',
    'binary_path': APP_BIN,
    'config_path': APP_CONFIG,
    'config': config_v1,
    'unit_name': APP_UNIT,
    'arguments': [],
    'environment': {'QA_P002_VERSION': 'v1', 'QA_P002_NONCE': NONCE},
    'health_check': {'type': 'systemd', 'timeout': 2, 'attempts': 5, 'interval': 1},
}
_, app = api('POST', '/api/applications', app_body, allowed=(200, 201))
app_id = app['id']
evidence['application'] = {k: app.get(k) for k in
                           ['id', 'name', 'artifact_id', 'version', 'binary_path',
                            'config_path', 'unit_name', 'revision', 'created_at']}
evidence['application']['config_sha256'] = config_sha
_, assignment = api('POST', '/api/applications/' + app_id + '/assign',
                    {'node_ids': [NODE_ID]}, allowed=(200, 201))
evidence['assignment'] = {'node_id': NODE_ID, 'http_status': 200}

assigned_node = wait_node_sync()
evidence['child_after_app_sync'] = assigned_node
if not assigned_node or assigned_node.get('status') != 'online' or assigned_node.get('sync_status') != 'synced':
    evidence['stage_status'] = 'BLOCKED_CHILD_SYNC_AFTER_ASSIGN'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('child did not reconcile assigned Application')

_, executions = api('POST', '/api/applications/' + app_id + '/deploy',
                    {'node_ids': [NODE_ID], 'operation': 'deploy'}, allowed=(200, 201))
if len(executions) != 1:
    raise RuntimeError('expected exactly one v1 deployment Execution for owned child')
execution_id = executions[0]['id']
evidence['v1_deploy_execution_id'] = execution_id
terminal = wait_execution(execution_id)
evidence['v1_deploy_execution'] = {k: terminal.get(k) for k in
                                   ['id', 'task_id', 'node_id', 'status', 'exit_code',
                                    'trigger_type', 'started_at', 'ended_at'] if k in terminal}
if terminal.get('status') != 'SUCCESS':
    evidence['stage_status'] = 'FAIL_V1_DEPLOY_EXECUTION'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('real v1 deploy did not succeed')

evidence['application_state'] = api('GET', '/api/applications/' + app_id + '/state')[1]
evidence['application_executions'] = api('GET', '/api/applications/' + app_id + '/executions')[1]
oracle = run_task_once('qa-resume-20261009-p0-002-v1-physical-oracle-' + NONCE,
                       'app-v1-oracle-source.py', NODE_ID,
                       parameters=[{'name': 'app_id', 'type': 'string', 'required': True}],
                       source_values={'app_id': app_id})
evidence['physical_oracle_task'] = oracle
oracle_value = oracle.get('execution', {}).get('stdout_json')
evidence['physical_oracle'] = oracle_value
evidence['child_after_v1'] = node_state(NODE_ID)

expected_unit_sha = None
if oracle_value:
    unit_info = oracle_value.get('unit', {})
    expected_unit_sha = unit_info.get('sha256')
checks = {
    'physical_binary_sha_matches_artifact': bool(oracle_value and oracle_value.get('binary', {}).get('sha256') == v1_sha),
    'physical_config_sha_matches_definition': bool(oracle_value and oracle_value.get('config', {}).get('sha256') == config_sha),
    'physical_unit_exists_and_references_exact_binary': bool(oracle_value and oracle_value.get('unit', {}).get('exists') and oracle_value.get('unit', {}).get('contains_binary_path')),
    'managed_unit_active': bool(oracle_value and oracle_value.get('unit', {}).get('service', {}).get('ActiveState') == 'active'),
    'app_health_healthy': bool(evidence['application_state'] and any(s.get('health') == 'healthy' and s.get('node_id') == NODE_ID for s in evidence['application_state'])),
    'deployment_journal_done': bool(oracle_value and oracle_value.get('deployment_journal', {}).get('phase') == 'DONE'),
    'oracle_execution_success': oracle.get('execution', {}).get('status') == 'SUCCESS',
    'child_remains_online_synced': evidence['child_after_v1'].get('status') == 'online' and evidence['child_after_v1'].get('sync_status') == 'synced',
}
evidence['assertions'] = checks
evidence['v1_physical_unit_sha256'] = expected_unit_sha
evidence['stage_status'] = 'V1_BASELINE_READY' if all(checks.values()) else 'V1_BASELINE_PARTIAL_OR_FAILED'
evidence['v2_payload'] = {
    'source_file': 'qa-app-v2.sh',
    'sha256': hashlib.sha256((HERE / 'qa-app-v2.sh').read_bytes()).hexdigest(),
    'application_unit_sha_will_change_via_environment_and_definition': True,
    'upload_or_upgrade_performed': False,
}
OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'evidence': str(OUT.relative_to(Path.cwd())),
                  'stage_status': evidence['stage_status'],
                  'node_id': NODE_ID, 'application_id': app_id,
                  'artifact_id': artifact['id'],
                  'deployment_execution_id': execution_id,
                  'v1_sha256': v1_sha,
                  'v1_unit_sha256': expected_unit_sha,
                  'assertions': checks,
                  'fault_injected': False}, ensure_ascii=False))
