import hashlib
import json
import os
import stat
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_env01_05 as api

BASE = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/setup')
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
ROOT_REVIEW = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/backend-isolation-review.json')
ROOT_DISK = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/disk-binaries.json')
PROXY_PATH = Path('.e2e/runs/20260930-101945/backend/ws_proxy.py')
HUB = 'http://192.168.100.209:18080'
TARGET_ID = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
TARGET_NAME = 'qa-repair-20260930-101945-native-4'
OLD_IDS = {
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
    '91f44866-8a28-435e-bbda-2f2367126c00',
    '611c02a5-1859-4465-878b-d543b7f6fdec',
    '37989297-3bc8-4c0c-b302-7c6efbdba630',
}
api.OUT = BASE


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)


def contains(value, needle):
    if isinstance(value, dict):
        return any(contains(k, needle) or contains(v, needle) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains(v, needle) for v in value)
    return value == needle


def load_connection():
    st = PRIVATE.lstat()
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_gate_failed')
    data = json.loads(PRIVATE.read_text())
    if data.get('hub_url') != HUB or not isinstance(data.get('token'), str) or not data.get('token'):
        raise RuntimeError('private_connection_authority_gate_failed')
    return data['token']


def validate_review_sha():
    review = json.loads(ROOT_REVIEW.read_text())
    disk = json.loads(ROOT_DISK.read_text())
    expected = review.get('target_host', {}).get('required_binary_sha256')
    rows = [r for r in disk.get('results', []) if r.get('domain') == 'nodesteer-agent-4']
    if len(rows) != 1 or not isinstance(expected, str) or len(expected) != 64 or any(ch not in '0123456789abcdef' for ch in expected):
        raise RuntimeError('authoritative_binary_hash_gate_failed')
    row = rows[0]
    binary = review.get('target_host', {}).get('existing_repair_binary')
    if row.get('path') != binary or row.get('sha256') != expected or row.get('expected_sha256') != expected:
        raise RuntimeError('review_disk_manifest_hash_disagreement')
    return expected


def assert_peer_health(token):
    rows = api.get_json(token, '/api/nodes')
    by = {r.get('id'): r for r in rows if isinstance(r, dict)}
    if not OLD_IDS.issubset(by):
        raise RuntimeError('original_repair_peer_set_changed')
    expected_snapshot = json.loads((BASE.parent / 'native4-node-map-preflight-final.json').read_text())['repair_nodes']
    before = {r['id']: r for r in expected_snapshot}
    facts = []
    for node_id in sorted(OLD_IDS):
        node = by[node_id]
        old = before.get(node_id)
        if not old or node.get('status') != 'online' or node.get('sync_status') != 'synced':
            raise RuntimeError('original_repair_peer_health_changed')
        if node.get('agent_version') != old.get('agent_version'):
            raise RuntimeError('original_repair_peer_version_changed')
        facts.append({'id': node_id, 'name': node.get('hostname'), 'status': node.get('status'), 'sync_status': node.get('sync_status'), 'agent_version': node.get('agent_version')})
    return facts, by


def assert_only_owned_node_matches(token, identity):
    nodes = api.get_json(token, '/api/nodes')
    matches = [r for r in nodes if isinstance(r, dict) and (str(r.get('hostname', '')).casefold() == identity['node_name'].casefold() or str(r.get('ip', '')).strip() == identity['node_ip'])]
    if len(matches) != 1 or matches[0].get('id') != identity['node_id'] or matches[0].get('agent_id') != identity['agent_id']:
        raise RuntimeError('new_identity_collision_or_mapping_mismatch')
    return matches[0]


def create_setup_task(token, identity, expected_sha, proxy_source):
    template = (BASE / 'native4_child_setup_runtime.py').read_text()
    values = {
        '__EXPECTED_BINARY_SHA256__': json.dumps(expected_sha),
        '__EXPECTED_NODE_ID__': json.dumps(identity['node_id']),
        '__EXPECTED_AGENT_ID__': json.dumps(identity['agent_id']),
        '__NODE_NAME__': json.dumps(identity['node_name']),
        '__NODE_IP__': json.dumps(identity['node_ip']),
        '__PROXY_SOURCE_JSON__': json.dumps(proxy_source, ensure_ascii=True),
    }
    source = template
    for key, value in values.items():
        source = source.replace(key, value)
    if any(key in source for key in values):
        raise RuntimeError('source_injection_gate_failed')
    command = api.make_python_command(source)
    if token in command or token in source:
        raise RuntimeError('task_source_contains_auth_token')
    suffix = hashlib.sha256((identity['node_id'] + str(time.time_ns())).encode()).hexdigest()[:10]
    name = 'qa-resume-20261008-native4-child-setup-' + suffix
    command_sha = hashlib.sha256(command.encode()).hexdigest()
    # api.create_and_run writes the complete immutable Task definition and command SHA before POST/run.
    result = api.create_and_run(token, name, command, TARGET_ID, timeout=180)
    if result.get('command_sha256') != command_sha:
        raise RuntimeError('task_command_hash_readback_mismatch')
    result['task_name'] = name
    return result


def delete_task(token, task_id, evidence_name):
    encoded = urllib.parse.quote(task_id, safe='')
    delete_http, _ = api.request(token, 'DELETE', '/api/tasks/' + encoded)
    get404 = api.route_status(token, '/api/tasks/' + encoded)
    row = {'task_id': task_id, 'delete_http': delete_http, 'get_after_delete_http': get404, 'evidence': evidence_name}
    if delete_http != 200 or get404 != 404:
        raise RuntimeError('owned_task_cleanup_failed')
    return row


def cleanup_child(token, identity):
    template = (BASE / 'cleanup_owned_child_runtime.py').read_text()
    owned_dir = identity.get('owned_directory')
    manifest_sha = identity.get('owner_manifest_sha256')
    if not isinstance(owned_dir, str) or not owned_dir.startswith('/tmp/qa-resume-20261008-child-') or not isinstance(manifest_sha, str) or len(manifest_sha) != 64:
        raise RuntimeError('owned_cleanup_inputs_invalid')
    source = template.replace('__OWNED_DIR_JSON__', json.dumps(owned_dir)).replace('__EXPECTED_MANIFEST_SHA256__', json.dumps(manifest_sha))
    command = api.make_python_command(source)
    if token in command:
        raise RuntimeError('cleanup_task_source_contains_auth_token')
    name = 'qa-resume-20261008-native4-child-cleanup-' + hashlib.sha256((identity['node_id'] + str(time.time_ns())).encode()).hexdigest()[:10]
    result = api.create_and_run(token, name, command, TARGET_ID, timeout=45)
    output = result.get('output')
    task_cleanup = delete_task(token, result['task_id'], 'native4_child_cleanup_task')
    record = {'task_id':result['task_id'],'execution_id':result['execution_id'],'command_sha256':result['command_sha256'],'execution_status':result['status'],'output':output,'task_cleanup':task_cleanup,'definition_evidence':name+'-definition.json','execution_evidence':name+'-execution.json'}
    write(BASE / 'owned-child-cleanup.json', record)
    if result.get('status') != 'SUCCESS' or not isinstance(output, dict) or output.get('status') != 'PASS' or output.get('directory_removed') is not True:
        raise RuntimeError('owned_child_cleanup_not_proven')
    return record


def delete_enrolled_node(token, identity):
    node_id = identity.get('node_id')
    if not isinstance(node_id, str) or node_id in OLD_IDS:
        raise RuntimeError('new_node_delete_id_gate_failed')
    encoded = urllib.parse.quote(node_id, safe='')
    node = api.get_json(token, '/api/nodes/' + encoded)
    if node.get('id') != node_id or node.get('agent_id') != identity.get('agent_id') or node.get('hostname') != identity.get('node_name') or node.get('ip') != identity.get('node_ip'):
        raise RuntimeError('new_node_delete_identity_guard_failed')
    delete_http, _ = api.request(token, 'DELETE', '/api/nodes/' + encoded)
    get404 = api.route_status(token, '/api/nodes/' + encoded)
    evidence = {'node_id': node_id, 'agent_id': identity['agent_id'], 'node_name': identity['node_name'], 'node_ip': identity['node_ip'], 'delete_http': delete_http, 'get_after_delete_http': get404}
    write(BASE / 'new-node-cleanup.json', evidence)
    if delete_http != 200 or get404 != 404:
        raise RuntimeError('new_node_delete_cleanup_failed')
    return evidence


def poll_online(token, node_id, seconds=180):
    deadline = time.monotonic() + seconds
    last = None
    samples = 0
    while time.monotonic() < deadline:
        last = api.get_json(token, '/api/nodes/' + urllib.parse.quote(node_id, safe=''))
        samples += 1
        if last.get('status') == 'online' and last.get('sync_status') == 'synced':
            return last, samples
        time.sleep(3)
    return last, samples


def main():
    os.umask(0o077)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BASE, 0o700)
    token = load_connection()
    if api.get_json(token, '/api/me').get('Role') != 'administrator':
        raise RuntimeError('administrator_role_gate_failed')
    identity = json.loads((BASE / 'stage2-enrollment.json').read_text())
    if identity.get('stage') != 'NEW_IDENTITY_ENROLLED_PENDING' or identity.get('node_id') in OLD_IDS:
        raise RuntimeError('new_identity_checkpoint_gate_failed')
    peers_before, current = assert_peer_health(token)
    current_node = assert_only_owned_node_matches(token, identity)
    if current_node.get('status') != 'pending' or current_node.get('sync_status') != 'pending':
        raise RuntimeError('new_identity_not_pending_before_child_start')
    expected_sha = validate_review_sha()
    proxy_raw = PROXY_PATH.read_bytes()
    proxy_source_sha = hashlib.sha256(proxy_raw).hexdigest()
    review = json.loads(ROOT_REVIEW.read_text())
    if proxy_source_sha != review.get('source_hashes', {}).get(str(PROXY_PATH)):
        raise RuntimeError('approved_proxy_source_hash_mismatch')
    proxy_source = proxy_raw.decode('utf-8')
    result = create_setup_task(token, identity, expected_sha, proxy_source)
    output = result.get('output')
    task_id = result.get('task_id')
    task_cleanup = delete_task(token, task_id, 'native4_child_setup_task') if task_id else None
    write(BASE / 'native4-child-setup-task-result.json', {
        'task_id': task_id,
        'execution_id': result.get('execution_id'),
        'execution_status': result.get('status'),
        'command_sha256': result.get('command_sha256'),
        'target_node_id': TARGET_ID,
        'retry': 0,
        'schedule_present': False,
        'task_cleanup': task_cleanup,
        'task_output': output,
        'task_definition_evidence': result.get('task_name', '') + '-definition.json',
    })
    if result.get('status') != 'SUCCESS' or not isinstance(output, dict) or output.get('status') != 'PASS':
        cleanup = None
        if isinstance(output, dict) and isinstance(output.get('failure_cleanup'), dict) and output['failure_cleanup'].get('owned_processes_stopped') is True and output['failure_cleanup'].get('owned_directory_removed') is True:
            cleanup = delete_enrolled_node(token, identity)
        write(BASE / 'checkpoint.json', {'stage': 'CHILD_SETUP_BLOCKED', 'task_id': task_id, 'execution_id': result.get('execution_id'), 'task_output': output, 'child_started': False, 'new_node_cleanup': cleanup, 'updated_at_epoch': time.time()})
        raise SystemExit(2)
    if output.get('node_id') != identity['node_id'] or output.get('agent_id') != identity['agent_id'] or output.get('env03_nobody_can_read_and_parse_child_config') is not True:
        raise RuntimeError('child_setup_identity_or_env03_proof_mismatch')
    identity['setup_task_id'] = task_id
    identity['setup_execution_id'] = result.get('execution_id')
    identity['setup_command_sha256'] = result.get('command_sha256')
    identity['owned_directory'] = output.get('owned_directory')
    identity['owner_manifest_sha256'] = output.get('owner_manifest_sha256')
    identity['child_pid'] = output.get('child_pid')
    identity['child_starttime'] = output.get('child_starttime')
    identity['child_exe_sha256'] = output.get('child_exe_sha256')
    identity['supervisor_pid'] = output.get('supervisor_pid')
    identity['supervisor_starttime'] = output.get('supervisor_starttime')
    identity['supervisor_exe_sha256'] = output.get('supervisor_exe_sha256')
    identity['proxy_bind_ip'] = output.get('proxy_bind_ip')
    identity['proxy_ephemeral_port'] = output.get('proxy_ephemeral_port')
    identity['proxy_upstream'] = output.get('proxy_upstream')
    identity['proxy_source_sha256'] = output.get('proxy_source_sha256')
    identity['registration_token_fingerprint'] = output.get('registration_token_fingerprint')
    identity['env03'] = {'status': 'PASS', 'readable_and_parseable_by_uid': 65534, 'config_mode': output.get('child_config_mode'), 'config_uid': output.get('child_config_uid'), 'token_fingerprint': output.get('registration_token_fingerprint')}
    write(BASE / 'stage3-child-start.json', identity)
    owner_ledger = json.loads((BASE / 'owner-ledger.json').read_text())
    owner_ledger.update({'stage': 'CHILD_STARTED_WAITING_ONLINE', 'child_processes_started': True, 'owned_directory': output.get('owned_directory'), 'directory_dev': output.get('directory_dev'), 'directory_ino': output.get('directory_ino'), 'directory_uid': output.get('directory_uid'), 'owner_manifest_sha256': output.get('owner_manifest_sha256'), 'child_pid': output.get('child_pid'), 'child_starttime': output.get('child_starttime'), 'child_uid': output.get('child_uid'), 'child_exe_sha256': output.get('child_exe_sha256'), 'supervisor_pid': output.get('supervisor_pid'), 'supervisor_starttime': output.get('supervisor_starttime'), 'supervisor_uid': output.get('supervisor_uid'), 'supervisor_exe_sha256': output.get('supervisor_exe_sha256')})
    write(BASE / 'owner-ledger.json', owner_ledger)
    node, samples = poll_online(token, identity['node_id'])
    if not node or node.get('status') != 'online' or node.get('sync_status') != 'synced' or node.get('agent_id') != identity['agent_id']:
        cleanup = cleanup_child(token, identity)
        node_cleanup = delete_enrolled_node(token, identity)
        write(BASE / 'checkpoint.json', {'stage': 'CHILD_ONLINE_BLOCKED_CLEANED', 'node_id': identity['node_id'], 'last_status': None if node is None else node.get('status'), 'last_sync_status': None if node is None else node.get('sync_status'), 'samples': samples, 'child_cleanup': cleanup, 'new_node_cleanup': node_cleanup, 'updated_at_epoch': time.time()})
        raise SystemExit(3)
    # No pre-existing Task, Schedule, or Application assignment may target this new identity.
    tasks = api.get_json(token, '/api/tasks')
    schedules = api.get_json(token, '/api/schedules')
    applications = api.get_json(token, '/api/applications')
    task_refs = [r.get('id') for r in tasks if isinstance(r, dict) and contains(r.get('target', {}), identity['node_id'])]
    schedule_refs = [r.get('id') for r in schedules if isinstance(r, dict) and contains(r.get('target', {}), identity['node_id'])]
    app_refs = [r.get('id') for r in applications if isinstance(r, dict) and contains(r, identity['node_id'])]
    if task_refs or schedule_refs or app_refs:
        cleanup = cleanup_child(token, identity)
        node_cleanup = delete_enrolled_node(token, identity)
        write(BASE / 'checkpoint.json', {'stage': 'CHILD_ASSIGNMENT_GATE_BLOCKED_CLEANED', 'node_id': identity['node_id'], 'task_refs': task_refs, 'schedule_refs': schedule_refs, 'application_refs': app_refs, 'child_cleanup': cleanup, 'new_node_cleanup': node_cleanup, 'updated_at_epoch': time.time()})
        raise SystemExit(4)
    peers_after, _ = assert_peer_health(token)
    write(BASE / 'pre_identity_assignment-check.json', {'node_id': identity['node_id'], 'status': node.get('status'), 'sync_status': node.get('sync_status'), 'agent_id_matches_enrollment': node.get('agent_id') == identity['agent_id'], 'existing_task_assignment_ids': task_refs, 'existing_schedule_assignment_ids': schedule_refs, 'existing_application_assignment_ids': app_refs, 'old_repair_peers_before': peers_before, 'old_repair_peers_after': peers_after, 'samples_to_online_synced': samples})
    owner_ledger['stage'] = 'CHILD_ONLINE_SYNCED_NO_PREEXISTING_ASSIGNMENTS'
    owner_ledger['new_node_status'] = node.get('status')
    owner_ledger['new_node_sync_status'] = node.get('sync_status')
    write(BASE / 'owner-ledger.json', owner_ledger)
    write(BASE / 'checkpoint.json', {'stage': 'CHILD_ONLINE_SYNCED_ENV03_PASS', 'node_id': identity['node_id'], 'agent_id': identity['agent_id'], 'child_started': True, 'setup_task_cleaned_get404': True, 'preexisting_assignments': 0, 'updated_at_epoch': time.time()})
    write(BASE / 'resume-hint.json', {'next_step': 'Create one exact child-targeted retry=0 no-schedule identity/proc/readonly SQLite proof Task; save full definition+SHA before run; then stop and await Root review. Do not inject faults or revoke/re-enroll.'})
    print(json.dumps({'status': 'PASS', 'node_id': identity['node_id'], 'agent_id': identity['agent_id'], 'node_status': node.get('status'), 'sync_status': node.get('sync_status'), 'env03': 'PASS', 'existing_assignments': 0, 'task_deleted_get404': True}, separators=(',', ':')))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'code': str(error)}, separators=(',', ':')))
        raise SystemExit(2)
