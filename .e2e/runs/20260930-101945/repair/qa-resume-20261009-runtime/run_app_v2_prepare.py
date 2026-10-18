#!/usr/bin/env python3
"""Upload a real v2 payload and update only the owned App definition; do not deploy or inject a fault."""
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
OUT = HERE / 'app-v2-preparation-evidence.json'
NODE_ID = 'c77b8a84-8085-4ea8-8532-de171761854b'
APP_ID = '2e4fecee-afc8-43a8-9d93-dd36cfc10bf9'
APP_NAME = 'qa-resume-20261009-native1-p0-002-app-870358cd92ee'
ARTIFACT_NAME = 'qa-resume-20261009-native1-p0-002-artifact-870358cd92ee-v2'
APP_UNIT = 'nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
APP_BIN = '/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/bin/qa-app'
APP_CONFIG = '/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/config/qa-app.env'
NONCE = '870358cd92ee'
V2_SIZE = 16 * 1024 * 1024
V1_BINARY_SHA = '35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c'
V1_CONFIG_SHA = '5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34'
V1_UNIT_SHA = '6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'

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
        with opener.open(req, timeout=30) as response:
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


def node_state():
    _, n = api('GET', '/api/nodes/' + NODE_ID)
    return {k: n.get(k) for k in ['id', 'hostname', 'status', 'sync_status',
                                  'agent_version', 'deployment_mode']}


def wait_synced(timeout=60):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = node_state()
        if last.get('status') == 'online' and last.get('sync_status') == 'synced':
            return last
        time.sleep(.5)
    return last


def run_task(source_name, task_name, params):
    source = (HERE / source_name).read_text()
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    command = 'python3 -c ' + shlex.quote(source)
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    definitions = [{'name': k, 'type': 'string', 'required': True} for k in params]
    task = {'name': task_name, 'type': 'command',
            'target': {'type': 'node', 'node_ids': [NODE_ID]},
            'parameters': definitions, 'command': command, 'timeout': 60,
            'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
    task_id = created['id']
    _, fetched = api('GET', '/api/tasks/' + task_id)
    if hashlib.sha256(fetched.get('command', '').encode()).hexdigest() != command_sha:
        raise RuntimeError('read-only cache oracle Task command SHA mismatch')
    row = {'task_id': task_id, 'source_file': source_name,
           'source_sha256': source_sha, 'command_sha256': command_sha}
    try:
        _, created_execs = api('POST', '/api/tasks/' + task_id + '/run',
                               {'params': params}, allowed=(200, 201))
        if not created_execs:
            raise RuntimeError('cache oracle Task returned no Execution')
        exec_id = created_execs[0]['id']
        row['execution_id'] = exec_id
        deadline = time.monotonic() + 100
        final = None
        while time.monotonic() < deadline:
            _, final = api('GET', '/api/executions/' + exec_id)
            if final.get('status') in {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED',
                                       'CANCELED', 'TIMED_OUT'}:
                break
            time.sleep(.5)
        if not final or final.get('status') not in {'SUCCESS', 'FAILED', 'SKIPPED',
                                                    'BLOCKED', 'CANCELED', 'TIMED_OUT'}:
            row['execution'] = {'status': 'TIMEOUT_WAITING'}
        else:
            stdout = final.get('stdout') or ''
            row['execution'] = {'id': final.get('id'), 'status': final.get('status'),
                                'node_id': final.get('node_id'),
                                'stdout_json': json.loads(stdout) if stdout else None,
                                'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
                                'stderr_bytes': len((final.get('stderr') or '').encode())}
    finally:
        delete, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
        verify, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
        row['cleanup'] = {'delete_http_status': delete,
                          'verify_get_http_status': verify, 'verified_404': verify == 404}
    return row


def upload_v2(data):
    boundary = 'qa' + uuid.uuid4().hex
    fields = {'name': ARTIFACT_NAME, 'version': 'v2', 'architecture': 'amd64'}
    body = b''
    for key, value in fields.items():
        body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key +
                 '"\r\n\r\n' + value + '\r\n').encode()
    body += ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="qa-app-v2.sh"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
    body += data + ('\r\n--' + boundary + '--\r\n').encode()
    _, value = api('POST', '/api/artifacts', raw=body,
                   headers={'Content-Type': 'multipart/form-data; boundary=' + boundary},
                   allowed=(200, 201))
    return value


before_node = node_state()
if before_node.get('status') != 'online' or before_node.get('sync_status') != 'synced':
    raise SystemExit('blocked: owned child is not online/synced')
_, current = api('GET', '/api/applications/' + APP_ID)
if current.get('name') != APP_NAME or current.get('version') != 'v1':
    raise SystemExit('blocked: owned application is not still the v1 definition')

prefix = (b'#!/bin/sh\n# NodeSteer isolated P0-002 fixture v2; no side effects.\n'
          b'exec sleep 3600\n')
padding_size = V2_SIZE - len(prefix)
if padding_size < 2:
    raise SystemExit('blocked: v2 payload size is invalid')
v2_data = prefix + b'#' + (b'x' * (padding_size - 1))
v2_sha = hashlib.sha256(v2_data).hexdigest()
if len(v2_data) != V2_SIZE or v2_sha == V1_BINARY_SHA:
    raise SystemExit('blocked: v2 payload size/hash is invalid')

evidence = {
    'run_id': '20260930-101945', 'case_id': 'E2E-20260930-101945-P0-002',
    'stage': 'V2_ARTIFACT_AND_DEFINITION_PREPARE_ONLY',
    'fault_injection_authorized': False,
    'deploy_v2_invoked': False,
    'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                       'only_repair_18080': True},
    'node_id': NODE_ID, 'application_id': APP_ID,
    'v1_baseline': {'binary_sha256': V1_BINARY_SHA,
                    'config_sha256': V1_CONFIG_SHA, 'unit_sha256': V1_UNIT_SHA},
    'v2_payload_generation': {'size': len(v2_data), 'sha256': v2_sha,
                              'padding_bytes': padding_size - 1,
                              'script_prefix_sha256': hashlib.sha256(prefix).hexdigest(),
                              'local_payload_file': 'not_written; generated in-memory'},
    'stage_status': 'STARTED',
}
artifact = upload_v2(v2_data)
evidence['v2_artifact'] = {k: artifact.get(k) for k in
                           ['id', 'name', 'version', 'architecture', 'filename', 'size', 'sha256']}
if artifact.get('sha256') != v2_sha or int(artifact.get('size', -1)) != V2_SIZE:
    evidence['stage_status'] = 'FAIL_V2_ARTIFACT_METADATA_MISMATCH'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('v2 Artifact SHA/size mismatch')

config_v2 = f'QA_P002_VERSION=v2\nQA_P002_NONCE={NONCE}\n'
update = {
    'name': APP_NAME,
    'description': current.get('description', ''),
    'artifact_id': artifact['id'],
    'version': 'v2',
    'binary_path': APP_BIN,
    'config_path': APP_CONFIG,
    'config': config_v2,
    'unit_name': APP_UNIT,
    'arguments': [],
    'environment': {'QA_P002_VERSION': 'v2', 'QA_P002_NONCE': NONCE},
    'health_check': current.get('health_check') or
                    {'type': 'systemd', 'timeout': 2, 'attempts': 5, 'interval': 1},
}
_, updated = api('PUT', '/api/applications/' + APP_ID, update, allowed=(200, 201))
evidence['v2_application_definition'] = {k: updated.get(k) for k in
                                         ['id', 'name', 'artifact_id', 'version',
                                          'binary_path', 'config_path', 'unit_name', 'revision']}
evidence['v2_application_definition']['config_sha256'] = hashlib.sha256(config_v2.encode()).hexdigest()
if updated.get('version') != 'v2' or updated.get('artifact_id') != artifact['id']:
    raise SystemExit('v2 Application definition readback mismatch')

node_after_sync = wait_synced()
evidence['node_after_definition_sync'] = node_after_sync
if not node_after_sync or node_after_sync.get('status') != 'online' or node_after_sync.get('sync_status') != 'synced':
    evidence['stage_status'] = 'BLOCKED_CHILD_SYNC_AFTER_V2_UPDATE'
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    raise SystemExit('child did not sync v2 Application definition')

cache_task = run_task(
    'app-v2-cache-oracle-source.py', 'qa-resume-20261009-p0-002-v2-cache-oracle-' + uuid.uuid4().hex[:8],
    {'app_id': APP_ID, 'artifact_sha': v2_sha})
evidence['v2_cache_oracle_task'] = cache_task
cache_oracle = cache_task.get('execution', {}).get('stdout_json')
evidence['v2_cache_oracle'] = cache_oracle
evidence['node_after_cache_oracle'] = node_state()
evidence['application_state_after_prepare'] = api('GET', '/api/applications/' + APP_ID + '/state')[1]
evidence['v1_application_executions'] = api('GET', '/api/applications/' + APP_ID + '/executions')[1]
checks = {
    'v2_cache_verified_from_real_agent_store': bool(cache_oracle and cache_oracle.get('v2_cache', {}).get('actual_sha256') == v2_sha and cache_oracle.get('v2_cache', {}).get('size') == V2_SIZE),
    'v1_binary_remains_physical': bool(cache_oracle and cache_oracle.get('v1_physical_state', {}).get('binary', {}).get('matches_v1')),
    'v1_config_remains_physical': bool(cache_oracle and cache_oracle.get('v1_physical_state', {}).get('config', {}).get('matches_v1')),
    'v1_unit_remains_active_and_unchanged': bool(cache_oracle and cache_oracle.get('v1_physical_state', {}).get('unit', {}).get('matches_v1') and cache_oracle.get('v1_physical_state', {}).get('unit', {}).get('active_state') == 'active'),
    'app_desired_definition_is_v2': updated.get('version') == 'v2',
    'v2_deploy_journal_absent': bool(cache_oracle and cache_oracle.get('v2_deployment_journal_count') == 0 and cache_oracle.get('latest_deployment', {}).get('to_version') == 'v1'),
    'application_state_still_v1_healthy': bool(evidence['application_state_after_prepare'] and any(s.get('version') == 'v1' and s.get('health') == 'healthy' and s.get('node_id') == NODE_ID for s in evidence['application_state_after_prepare'])),
    'child_online_synced': evidence['node_after_cache_oracle'].get('status') == 'online' and evidence['node_after_cache_oracle'].get('sync_status') == 'synced',
}
evidence['assertions'] = checks
evidence['prefetch_before_deploy_observed'] = bool(checks['v2_cache_verified_from_real_agent_store'])
evidence['stage_status'] = 'V2_READY_FOR_PARENT_FAULT_REVIEW' if all(checks.values()) else 'V2_PREPARE_PARTIAL_OR_FAILED'
OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'evidence': str(OUT.relative_to(Path.cwd())),
                  'stage_status': evidence['stage_status'],
                  'artifact_id': artifact['id'], 'app_id': APP_ID,
                  'v2_sha256': v2_sha, 'v2_size': V2_SIZE,
                  'prefetch_before_deploy_observed': evidence['prefetch_before_deploy_observed'],
                  'v1_physical_unchanged': checks['v1_binary_remains_physical'] and checks['v1_config_remains_physical'] and checks['v1_unit_remains_active_and_unchanged'],
                  'v2_deploy_invoked': False, 'fault_injected': False}, ensure_ascii=False))
