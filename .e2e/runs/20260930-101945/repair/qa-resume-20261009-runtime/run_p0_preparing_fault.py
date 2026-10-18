#!/usr/bin/env python3
"""Gate, launch the reviewed observer, then dispatch only the owned v2 Application upgrade."""
import argparse
import hashlib
import json
import shlex
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
OUT = HERE / 'p0-preparing-fault-evidence.json'
BASELINE = json.loads((HERE / 'app-v1-baseline-evidence.json').read_text())
V2 = json.loads((HERE / 'app-v2-preparation-evidence.json').read_text())
PARENT_NODE = '91f44866-8a28-435e-bbda-2f2367126c00'
CHILD_NODE = 'c77b8a84-8085-4ea8-8532-de171761854b'
APP_ID = '2e4fecee-afc8-43a8-9d93-dd36cfc10bf9'
V2_ARTIFACT_ID = '5517ff25-65b1-4da3-b5d7-a3f86f0de73d'
V2_ARTIFACT_SHA = 'e978740d1f6e40e831013a1ccd60ecd6d27e2c67ba1389d4df23bcbd4801cd46'
NONCE = '870358cd92ee'

base = token = opener = None


def api(method, path, body=None, headers=None, raw=None, allowed=(200,)):
    request_headers = {'Authorization': 'Bearer ' + token}
    request_headers.update(headers or {})
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        request_headers['Content-Type'] = 'application/json'
    req = Request(base + path, data=data, headers=request_headers, method=method)
    try:
        with opener.open(req, timeout=15) as response:
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
    return {k: node.get(k) for k in ['id', 'hostname', 'status', 'sync_status',
                                     'agent_version', 'deployment_mode']}


def wait_execution(execution_id, terminal, timeout):
    deadline = time.monotonic() + timeout
    final = None
    while time.monotonic() < deadline:
        _, final = api('GET', '/api/executions/' + execution_id)
        if final.get('status') in terminal:
            return final
        time.sleep(.25)
    return final


def wait_observer_ready(execution_id, timeout=12):
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        _, chunks = api('GET', '/api/executions/' + execution_id + '/logs')
        stdout = ''.join(c.get('chunk', '') for c in chunks if c.get('stream') == 'stdout')
        marker = 'READY_ARMED_WAITING_FOR_EXACT_PREPARING'
        if marker in stdout:
            lines = [line for line in stdout.splitlines() if marker in line]
            try:
                value = json.loads(lines[-1])
            except (ValueError, IndexError):
                value = None
            if (value and value.get('observer_status') == 'READY_ARMED_WAITING_FOR_EXACT_PREPARING' and
                    value.get('owner_nonce') == NONCE and value.get('app_id') == APP_ID and
                    value.get('child_node_id') == CHILD_NODE and
                    value.get('child_unit') == 'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service' and
                    value.get('v1_physical_state_matches') is True and
                    value.get('v2_cache_size') == 16777216):
                return {'ready': True, 'marker': value, 'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest()}
        _, status = api('GET', '/api/executions/' + execution_id)
        last_status = status.get('status')
        if last_status in {'FAILED', 'BLOCKED', 'CANCELED', 'TIMED_OUT', 'SUCCESS'}:
            return {'ready': False, 'execution_status': last_status,
                    'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest()}
        time.sleep(.15)
    return {'ready': False, 'execution_status': last_status, 'reason': 'ready marker timeout'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute-fault', action='store_true')
    parser.add_argument('--owner-nonce', default='')
    args = parser.parse_args()
    if not args.execute_fault or args.owner_nonce != NONCE:
        print(json.dumps({'status': 'PREPARE_ONLY_NO_REMOTE_CALLS',
                          'execute_fault_flag': args.execute_fault,
                          'owner_nonce_matched': args.owner_nonce == NONCE,
                          'remote_calls_made': False}, ensure_ascii=False))
        return

    global base, token, opener
    conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
    base, token = conn['hub_url'].rstrip('/'), conn['token']
    if urlsplit(base).port != 18080:
        raise SystemExit('blocked: repair endpoint is not :18080')
    opener = build_opener(ProxyHandler({}))

    if BASELINE.get('stage_status') != 'V1_BASELINE_READY':
        raise SystemExit('blocked: v1 physical baseline is not ready')
    if V2.get('stage_status') != 'V2_READY_FOR_PARENT_FAULT_REVIEW':
        raise SystemExit('blocked: v2 cache prefetch is not verified')
    if V2.get('deploy_v2_invoked') is not False or V2.get('fault_injected') is not False:
        raise SystemExit('blocked: v2 deployment/fault already attempted')
    child_before = node_state(CHILD_NODE)
    app, = [api('GET', '/api/applications/' + APP_ID)[1]]
    if (child_before.get('status') != 'online' or child_before.get('sync_status') != 'synced' or
            app.get('version') != 'v2' or app.get('artifact_id') != V2_ARTIFACT_ID):
        raise SystemExit('blocked: child/App desired state does not match approved fixture')
    states = api('GET', '/api/applications/' + APP_ID + '/state')[1]
    if not any(s.get('node_id') == CHILD_NODE and s.get('version') == 'v1' and
               s.get('health') == 'healthy' for s in states):
        raise SystemExit('blocked: v1 is not the currently healthy physical App state')
    if V2_ARTIFACT_SHA != V2.get('v2_artifact', {}).get('sha256'):
        raise SystemExit('blocked: v2 Artifact SHA differs from reviewed evidence')

    source = (HERE / 'preparing_fault_observer.py').read_text()
    source_sha = hashlib.sha256(source.encode()).hexdigest()
    command = ('python3 -c ' + shlex.quote(source) +
               ' --execute-fault --owner-nonce ' + shlex.quote(NONCE))
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    task = {
        'name': 'qa-resume-20261009-p0-002-preparing-observer-' + NONCE,
        'type': 'command',
        'target': {'type': 'node', 'node_ids': [PARENT_NODE]},
        'parameters': [],
        'command': command,
        'timeout': 60,
        'retry': 0,
        'offline_policy': 'hub_online_required',
        'enabled': True,
    }
    evidence = {
        'run_id': '20260930-101945',
        'case_id': 'E2E-20260930-101945-P0-002',
        'stage': 'PREPARING_FAULT_OBSERVER_AND_V2_DEPLOY',
        'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port,
                           'only_repair_18080': True},
        'child_node_id': CHILD_NODE,
        'parent_observer_node_id': PARENT_NODE,
        'application_id': APP_ID,
        'v2_artifact_id': V2_ARTIFACT_ID,
        'v2_artifact_sha256': V2_ARTIFACT_SHA,
        'observer_source_file': 'preparing_fault_observer.py',
        'observer_source_sha256': source_sha,
        'observer_task_command_sha256': command_sha,
        'observer_task_definition': {k: v for k, v in task.items() if k != 'command'},
        'observer_execution': None,
        'v2_deployment_execution': None,
        'signals_sent_by_harness': False,
        'fault_injection_authorized_by_this_harness': False,
    }
    task_id = None
    try:
        _, created = api('POST', '/api/tasks', task, allowed=(200, 201))
        task_id = created['id']
        evidence['observer_task_id'] = task_id
        evidence['observer_task_definition']['id'] = task_id
        evidence['observer_task_definition']['command_sha256'] = command_sha
        _, fetched = api('GET', '/api/tasks/' + task_id)
        readback_sha = hashlib.sha256(fetched.get('command', '').encode()).hexdigest()
        evidence['observer_task_readback'] = {
            'id': fetched.get('id'), 'name': fetched.get('name'),
            'type': fetched.get('type'), 'target': fetched.get('target'),
            'retry': fetched.get('retry'), 'timeout': fetched.get('timeout'),
            'command_sha256': readback_sha,
        }
        if readback_sha != command_sha:
            raise RuntimeError('observer Task command readback SHA mismatch')
        _, observer_execs = api('POST', '/api/tasks/' + task_id + '/run',
                                {'params': {}}, allowed=(200, 201))
        if len(observer_execs) != 1:
            raise RuntimeError('observer Task did not produce exactly one Execution')
        observer_execution_id = observer_execs[0]['id']
        evidence['observer_execution_id'] = observer_execution_id
        ready = wait_observer_ready(observer_execution_id)
        evidence['observer_ready'] = ready
        if not ready.get('ready'):
            try:
                api('POST', '/api/executions/' + observer_execution_id + '/cancel', {}, allowed=(200, 204))
                evidence['observer_readiness_cancel_requested'] = True
            except Exception as exc:
                evidence['observer_readiness_cancel_error'] = type(exc).__name__
            raise RuntimeError('observer did not emit exact READY marker; no v2 deployment was sent')
        child_now = node_state(CHILD_NODE)
        evidence['child_before_v2_deploy'] = child_now
        if child_now.get('status') != 'online' or child_now.get('sync_status') != 'synced':
            raise RuntimeError('owned child went offline before v2 deployment; no v2 deploy sent')

        # This is the only mutating deployment request in this harness; observer source controls signals.
        _, deploys = api('POST', '/api/applications/' + APP_ID + '/deploy',
                         {'node_ids': [CHILD_NODE], 'operation': 'upgrade'},
                         allowed=(200, 201))
        if len(deploys) != 1:
            raise RuntimeError('expected one v2 Application Deployment Execution')
        deploy_id = deploys[0]['id']
        evidence['v2_deployment_execution_id'] = deploy_id
        evidence['deploy_request_sent'] = True

        observer_final = wait_execution(observer_execution_id,
                                        {'SUCCESS', 'FAILED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'},
                                        55)
        if observer_final:
            stdout = observer_final.get('stdout') or ''
            records = []
            malformed_line_sha256s = []
            for line in stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    malformed_line_sha256s.append(hashlib.sha256(line.encode()).hexdigest())
                    continue
                if isinstance(record, dict):
                    records.append(record)
            final_record = next((record for record in reversed(records)
                                 if record.get('observer_status') and
                                 record.get('observer_status') != 'READY_ARMED_WAITING_FOR_EXACT_PREPARING'),
                                None)
            evidence['observer_execution'] = {
                'id': observer_final.get('id'),
                'status': observer_final.get('status'),
                'node_id': observer_final.get('node_id'),
                'stdout_json': final_record,
                'stdout_json_record_count': len(records),
                'stdout_ready_record_seen': any(
                    record.get('observer_status') == 'READY_ARMED_WAITING_FOR_EXACT_PREPARING'
                    for record in records),
                'stdout_malformed_line_sha256s': malformed_line_sha256s,
                'stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest(),
                'stderr_bytes': len((observer_final.get('stderr') or '').encode()),
                'stdout_parse_status': 'FINAL_OBSERVER_RECORD_SELECTED' if final_record else 'NO_FINAL_OBSERVER_RECORD',
            }
            evidence['signals_sent_by_harness'] = bool(
                final_record and final_record.get('sigkill_sent'))
        deploy_final = wait_execution(deploy_id,
                                      {'SUCCESS', 'FAILED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'},
                                      10)
        if deploy_final:
            evidence['v2_deployment_execution'] = {
                k: deploy_final.get(k) for k in
                ['id', 'task_id', 'node_id', 'status', 'exit_code',
                 'trigger_type', 'started_at', 'ended_at', 'block_reason']
                if k in deploy_final
            }
        evidence['child_after_observer'] = node_state(CHILD_NODE)
        evidence['application_state_after_observer'] = api(
            'GET', '/api/applications/' + APP_ID + '/state')[1]
        evidence['harness_status'] = (
            'SIGKILL_SENT' if evidence['signals_sent_by_harness'] else
            (evidence['observer_execution']['stdout_json'].get('observer_status')
             if evidence.get('observer_execution', {}).get('stdout_json') else 'OBSERVER_NO_RESULT'))
    finally:
        if task_id:
            try:
                delete_status, _ = api('DELETE', '/api/tasks/' + task_id, allowed=(200, 204))
                verify_status, _ = api('GET', '/api/tasks/' + task_id, allowed=(404,))
                evidence['observer_task_cleanup'] = {
                    'delete_http_status': delete_status,
                    'verify_get_http_status': verify_status,
                    'verified_404': verify_status == 404,
                }
            except Exception as exc:
                evidence['observer_task_cleanup'] = {'error_type': type(exc).__name__}
        evidence['completed_unix'] = int(time.time())
        OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')

    print(json.dumps({'evidence': str(OUT.relative_to(Path.cwd())),
                      'harness_status': evidence.get('harness_status'),
                      'observer_task_id': evidence.get('observer_task_id'),
                      'observer_execution_id': evidence.get('observer_execution_id'),
                      'v2_deployment_execution_id': evidence.get('v2_deployment_execution_id'),
                      'signals_sent': evidence.get('signals_sent_by_harness'),
                      'child_status': evidence.get('child_after_observer', {}).get('status'),
                      'fault_injected': evidence.get('signals_sent_by_harness')},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
