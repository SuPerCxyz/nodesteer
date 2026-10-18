import hashlib
import json
import os
import time
import urllib.parse
from pathlib import Path

import run_env01_05 as api

OUT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend')
TARGET_ID = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
TARGET_NAME = 'qa-repair-20260930-101945-native-4'


def main():
    os.umask(0o077)
    token = api.read_private()
    mapping = json.loads((OUT / 'native4-node-map-preflight-final.json').read_text())
    if mapping.get('target_id') != TARGET_ID or mapping.get('target_name') != TARGET_NAME:
        raise RuntimeError('native4_mapping_evidence_mismatch')
    source = (OUT / 'native4_gateway_config_probe.py').read_text()
    command = api.make_python_command(source)
    if token in command:
        raise RuntimeError('task_source_contains_auth_token')
    suffix = hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:10]
    name = 'qa-resume-20261008-native4-gateway-read-' + suffix
    result = api.create_and_run(token, name, command, TARGET_ID, timeout=60)
    output = result.get('output')
    if result.get('status') != 'SUCCESS' or not isinstance(output, dict):
        raise RuntimeError('gateway_probe_task_execution_failed')

    task_id = result['task_id']
    encoded_id = urllib.parse.quote(task_id, safe='')
    delete_http, _ = api.request(token, 'DELETE', '/api/tasks/' + encoded_id)
    get_after_delete_http = api.route_status(token, '/api/tasks/' + encoded_id)
    cleanup = {
        'task_id': task_id,
        'delete_http': delete_http,
        'get_after_delete_http': get_after_delete_http,
    }
    api.write_json(OUT / (name + '-cleanup.json'), cleanup)
    if delete_http != 200 or get_after_delete_http != 404:
        raise RuntimeError('gateway_probe_task_cleanup_failed')

    evidence = {
        'status': output.get('status'),
        'task_id': task_id,
        'execution_id': result['execution_id'],
        'target_id': TARGET_ID,
        'target_name': TARGET_NAME,
        'command_sha256': result['command_sha256'],
        'execution_status': result['status'],
        'execution_output': output,
        'task_cleanup': cleanup,
        'no_gateway_connection_attempted': output.get('connection_attempted') is False,
        'primary_8080_accessed': False,
        'existing_agent_or_config_modified': False,
    }
    api.write_json(OUT / (name + '-evidence.json'), evidence)
    summary = {
        'status': 'PASS' if output.get('status') == 'PASS' and output.get('connection_attempted') is False else 'BLOCKED',
        'result': output,
        'task_id': task_id,
        'execution_id': result['execution_id'],
        'command_sha256': result['command_sha256'],
        'definition_evidence': name + '-definition.json',
        'created_evidence': name + '-created.json',
        'execution_evidence': name + '-execution.json',
        'cleanup_evidence': name + '-cleanup.json',
        'full_evidence': name + '-evidence.json',
    }
    api.write_json(OUT / 'native4-gateway-config-read.json', summary)
    print(json.dumps({'status': summary['status'], 'evidence': 'native4-gateway-config-read.json', 'task_cleanup': cleanup}, separators=(',', ':')))


if __name__ == '__main__':
    main()
