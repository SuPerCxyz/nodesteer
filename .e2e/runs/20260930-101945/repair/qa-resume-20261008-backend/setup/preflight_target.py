import hashlib
import json
import os
import socket
import stat
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_env01_05 as api

BASE = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/setup')
PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
ROOT_PREFLIGHT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/preflight.json')
TARGET_ID = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
TARGET_NAME = 'qa-repair-20260930-101945-native-4'
HUB = 'http://192.168.100.209:18080'
api.OUT = BASE


def write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)


def main():
    os.umask(0o077)
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BASE, 0o700)
    info = PRIVATE.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_gate_failed')
    connection = json.loads(PRIVATE.read_text())
    if connection.get('hub_url') != HUB or not isinstance(connection.get('token'), str) or not connection['token']:
        raise RuntimeError('private_connection_gate_failed')
    token = connection['token']
    me = api.get_json(token, '/api/me')
    if me.get('Role') != 'administrator':
        raise RuntimeError('administrator_role_gate_failed')
    root = json.loads(ROOT_PREFLIGHT.read_text())
    mapping = {r.get('hostname', r.get('name')): r.get('id') for r in root.get('nodes', [])}
    if mapping.get(TARGET_NAME) != TARGET_ID:
        raise RuntimeError('root_native4_mapping_gate_failed')
    nodes = api.get_json(token, '/api/nodes')
    by_id = {r.get('id'): r for r in nodes if isinstance(r, dict)}
    target = by_id.get(TARGET_ID)
    if not target or target.get('hostname') != TARGET_NAME:
        raise RuntimeError('live_native4_mapping_gate_failed')
    expected_old = {
        '03cb50cc-eab6-4afd-bbe0-f73c4e326773',
        '91f44866-8a28-435e-bbda-2f2367126c00',
        '611c02a5-1859-4465-878b-d543b7f6fdec',
        '37989297-3bc8-4c0c-b302-7c6efbdba630',
    }
    if not expected_old.issubset(by_id):
        raise RuntimeError('repair_peer_set_changed')
    nonce = str(uuid.uuid4())
    node_name = 'qa-resume-20261008-native4-child-' + nonce
    occupied_names = {str(r.get('hostname', '')).casefold() for r in nodes}
    occupied_ips = {str(r.get('ip', '')).strip() for r in nodes}
    if node_name.casefold() in occupied_names:
        raise RuntimeError('generated_node_name_collision')
    # Generate an RFC1918-looking loopback alias in 127/8; NodeSteer stores this as its true proxy bind IP.
    parts = [127, 64 + int(nonce[0:2], 16) % 64, int(nonce[2:4], 16), 1 + int(nonce[4:6], 16) % 253]
    node_ip = '.'.join(map(str, parts))
    if node_ip in occupied_ips:
        raise RuntimeError('generated_node_ip_collision')
    task_source = """import json,socket\nip=__NODE_IP__\ns=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\ntry:\n s.bind((ip,0)); print(json.dumps({'status':'PASS','bindable':True,'ip':ip,'port_ephemeral':True},separators=(',',':')))\nfinally:\n s.close()\n""".replace('__NODE_IP__', json.dumps(node_ip))
    task_name = 'qa-resume-20261008-native4-child-bind-' + nonce[:10]
    command = api.make_python_command(task_source)
    if token in command:
        raise RuntimeError('task_source_contains_auth_token')
    result = api.create_and_run(token, task_name, command, TARGET_ID, timeout=45)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('loopback_bind_task_failed')
    task_id = result['task_id']
    encoded = __import__('urllib.parse', fromlist=['quote']).quote(task_id, safe='')
    delete_http, _ = api.request(token, 'DELETE', '/api/tasks/' + encoded)
    get404 = api.route_status(token, '/api/tasks/' + encoded)
    if delete_http != 200 or get404 != 404:
        raise RuntimeError('bind_task_cleanup_failed')
    state = {
        'stage': 'TARGET_BIND_PREFLIGHT_PASS',
        'authority': HUB,
        'target_node_id': TARGET_ID,
        'target_node_name': TARGET_NAME,
        'original_repair_node_ids_preserved': sorted(expected_old),
        'generated_nonce': nonce,
        'new_node_name': node_name,
        'new_node_ip': node_ip,
        'name_collision_check': 'no case-insensitive collision across live /api/nodes',
        'ip_collision_check': 'no exact IP collision across live /api/nodes',
        'target_bind': {'task_id': task_id, 'execution_id': result['execution_id'], 'command_sha256': result['command_sha256'], 'status': result['status'], 'output': result['output'], 'delete_http': delete_http, 'get_after_delete_http': get404},
        'enrollment_performed': False,
        'child_started': False,
        'updated_at_epoch': time.time(),
    }
    write(BASE / 'stage1-target-preflight.json', state)
    write(BASE / 'owner-ledger.json', {'resources': [], 'stage': state['stage'], 'new_node_created': False, 'child_processes_started': False})
    write(BASE / 'resume-hint.json', {'next_step': 'Use admin enrollment POST with exact unique node_name/node_ip; keep generated config response in memory, persist only node_id/agent_id metadata; then run child setup Task on exact native4.'})
    print(json.dumps({'status': 'PASS', 'node_name': node_name, 'node_ip': node_ip, 'task_deleted_get404': True}, separators=(',', ':')))


if __name__ == '__main__':
    main()
