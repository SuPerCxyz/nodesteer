import hashlib
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
ROOT_PREFLIGHT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/preflight.json')
ROOT_REVIEW = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/backend-isolation-review.json')
ROOT_DISK = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-root/disk-binaries.json')
OUT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend')
HUB_EXPECTED = 'http://192.168.100.209:18080'
TARGET_ID = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
TARGET_NAME = 'qa-repair-20260930-101945-native-4'
OTHER_REPAIR_IDS = {
    'qa-repair-20260930-101945-native-1': '91f44866-8a28-435e-bbda-2f2367126c00',
    'qa-repair-20260930-101945-native-2': '611c02a5-1859-4465-878b-d543b7f6fdec',
    'qa-repair-20260930-101945-native-3': '37989297-3bc8-4c0c-b302-7c6efbdba630',
}


def read_private():
    info = PRIVATE.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('private_connection_file_gate_failed')
    data = json.loads(PRIVATE.read_text())
    if data.get('hub_url') != HUB_EXPECTED:
        raise RuntimeError('private_connection_authority_gate_failed')
    token = data.get('token')
    if not isinstance(token, str) or not token:
        raise RuntimeError('private_connection_token_missing')
    return token


def request(token, method, path, body=None):
    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(',', ':')).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(HUB_EXPECTED + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except Exception:
        raise RuntimeError('api_transport_failure')


def get_json(token, path):
    status, raw = request(token, 'GET', path)
    if status != 200:
        raise RuntimeError('api_get_http_' + str(status))
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        raise RuntimeError('api_json_decode_failure')


def write_json(path, value, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, mode)


def route_status(token, path):
    status, _ = request(token, 'GET', path)
    return status


def create_and_run(token, name, command, target_id, timeout=180):
    command_hash = hashlib.sha256(command.encode()).hexdigest()
    payload = {
        'name': name,
        'type': 'command',
        'target': {'type': 'node', 'node_ids': [target_id]},
        'parameters': [],
        'param_values': {},
        'command': command,
        'timeout': timeout,
        'retry': 0,
        'offline_policy': 'hub_online_required',
        'enabled': True,
    }
    definition_path = OUT / (name + '-definition.json')
    write_json(definition_path, {'task': payload, 'command_sha256': command_hash, 'source_contains_connection_token': False})
    token = token
    if token in command:
        raise RuntimeError('command_literal_auth_value_gate_failed')
    status, raw = request(token, 'POST', '/api/tasks', payload)
    if status != 200:
        raise RuntimeError('task_create_http_' + str(status))
    try:
        task = json.loads(raw)
    except (ValueError, TypeError):
        raise RuntimeError('task_create_response_invalid')
    task_id = task.get('id')
    if not isinstance(task_id, str) or not task_id:
        raise RuntimeError('task_id_missing')
    readback = get_json(token, '/api/tasks/' + urllib.parse.quote(task_id, safe=''))
    if readback.get('command') != command or readback.get('target', {}).get('node_ids') != [target_id] or readback.get('retry') != 0:
        raise RuntimeError('task_definition_readback_mismatch')
    write_json(OUT / (name + '-created.json'), {
        'task_id': task_id,
        'name': name,
        'create_http': status,
        'revision': task.get('revision'),
        'command_sha256': command_hash,
        'readback_http': 200,
        'target_node_id': target_id,
        'retry': readback.get('retry'),
        'schedule_present': bool(readback.get('schedule')),
    })
    run_status, run_raw = request(token, 'POST', '/api/tasks/' + task_id + '/run', {'params': {}})
    if run_status != 200:
        raise RuntimeError('task_run_http_' + str(run_status))
    try:
        executions = json.loads(run_raw)
    except (ValueError, TypeError):
        raise RuntimeError('task_run_response_invalid')
    if not isinstance(executions, list) or len(executions) != 1:
        raise RuntimeError('task_run_execution_count_not_one')
    execution_id = executions[0].get('id')
    if executions[0].get('node_id') != target_id or not execution_id:
        raise RuntimeError('task_execution_target_mismatch')
    deadline = time.monotonic() + timeout + 30
    execution = None
    while time.monotonic() < deadline:
        execution = get_json(token, '/api/executions/' + urllib.parse.quote(execution_id, safe=''))
        if execution.get('status') not in ('PENDING', 'RUNNING'):
            break
        time.sleep(1)
    if not execution or execution.get('status') in ('PENDING', 'RUNNING'):
        raise RuntimeError('task_execution_timeout')
    stdout = execution.get('stdout') or ''
    stderr = execution.get('stderr') or ''
    if token in stdout or token in stderr:
        stdout = ''
        stderr = ''
        output_sensitive = True
    else:
        output_sensitive = False
    try:
        output = json.loads(stdout) if stdout else None
    except (ValueError, TypeError):
        output = None
    write_json(OUT / (name + '-execution.json'), {
        'execution_id': execution_id,
        'task_id': task_id,
        'node_id': execution.get('node_id'),
        'status': execution.get('status'),
        'exit_code': execution.get('exit_code'),
        'stdout_chars': len(stdout),
        'stderr_chars': len(stderr),
        'stdout_json': output,
        'sensitive_output_suppressed': output_sensitive,
        'execution_get_http': 200,
    })
    return {'task_id': task_id, 'execution_id': execution_id, 'status': execution.get('status'), 'output': output, 'command_sha256': command_hash}


def make_python_command(source):
    return "python3 - <<'PY'\n" + source.rstrip() + "\nPY"


def main():
    os.umask(0o077)
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = read_private()
    me = get_json(token, '/api/me')
    if me.get('Role') != 'administrator':
        raise RuntimeError('authority_role_gate_failed')
    root_preflight = json.loads(ROOT_PREFLIGHT.read_text())
    baseline = root_preflight.get('nodes')
    if not isinstance(baseline, list):
        raise RuntimeError('root_preflight_node_map_missing')
    map_by_name = {row.get('hostname', row.get('name')): row for row in baseline}
    expected = map_by_name.get(TARGET_NAME)
    if not expected or expected.get('id') != TARGET_ID:
        raise RuntimeError('root_preflight_native4_mapping_mismatch')
    for name, node_id in OTHER_REPAIR_IDS.items():
        row = map_by_name.get(name)
        if not row or row.get('id') != node_id:
            raise RuntimeError('root_preflight_other_repair_mapping_mismatch')

    api_nodes = get_json(token, '/api/nodes')
    if not isinstance(api_nodes, list):
        raise RuntimeError('nodes_response_not_array')
    by_id = {row.get('id'): row for row in api_nodes}
    target = by_id.get(TARGET_ID)
    if not target or target.get('hostname', target.get('name')) != TARGET_NAME:
        raise RuntimeError('live_native4_mapping_mismatch')
    required_ids = {TARGET_ID, *OTHER_REPAIR_IDS.values()}
    if not required_ids.issubset(by_id):
        raise RuntimeError('live_repair_node_set_mismatch')
    pre_nodes = []
    for node_id in sorted(required_ids):
        row = by_id[node_id]
        pre_nodes.append({
            'id': row.get('id'),
            'name': row.get('hostname', row.get('name')),
            'status': row.get('status'),
            'sync_status': row.get('sync_status'),
            'agent_version': row.get('agent_version'),
            'deployment_mode': row.get('deployment_mode'),
        })
    node_map = {
        'http': 200,
        'target_id': TARGET_ID,
        'target_name': TARGET_NAME,
        'target_status': target.get('status'),
        'target_sync_status': target.get('sync_status'),
        'mapping_matches_root_preflight': True,
        'mapping_matches_live_api': True,
        'repair_nodes': pre_nodes,
    }
    write_json(OUT / 'native4-node-map-preflight-final.json', node_map)
    if any(row.get('status') != 'online' or row.get('sync_status') != 'synced' for row in pre_nodes):
        raise RuntimeError('repair_nodes_not_online_and_synced_before_task')

    review = json.loads(ROOT_REVIEW.read_text())
    required_sha = review.get('target_host', {}).get('required_binary_sha256')
    disk = json.loads(ROOT_DISK.read_text())
    disk_rows = [row for row in disk.get('results', []) if row.get('domain') == 'nodesteer-agent-4']
    if len(disk_rows) != 1:
        raise RuntimeError('native4_disk_manifest_entry_missing_or_ambiguous')
    disk_row = disk_rows[0]
    if not isinstance(required_sha, str) or len(required_sha) != 64 or not re.fullmatch(r'[0-9a-f]{64}', required_sha):
        raise RuntimeError('authoritative_expected_sha_invalid')
    if disk_row.get('path') != '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent' or disk_row.get('sha256') != required_sha or disk_row.get('expected_sha256') != required_sha:
        raise RuntimeError('root_disk_manifest_does_not_match_review')
    prior = json.loads((OUT / 'env01_05-sha-diagnostic-summary.json').read_text())
    prior_checks = prior.get('preflight_output', {}).get('checks', {})
    wrong_expected = prior_checks.get('expected_binary_sha256')
    actual_disk = prior_checks.get('disk_binary_sha256')
    write_json(OUT / 'binary-sha-reconciliation.json', {
        'authoritative_review_expected_sha256': required_sha,
        'root_disk_manifest_sha256': disk_row.get('sha256'),
        'root_disk_manifest_expected_sha256': disk_row.get('expected_sha256'),
        'prior_task_source_expected_sha256': wrong_expected,
        'prior_task_source_expected_length': len(wrong_expected) if isinstance(wrong_expected, str) else None,
        'prior_target_disk_sha256': actual_disk,
        'prior_target_running_sha256': prior_checks.get('running_binary_sha256'),
        'first_gate_failure_cause': 'test_harness_expected_hash_transcription_was_62_hex_characters',
        'corrected_expected_sha_source': 'backend-isolation-review.json target_host.required_binary_sha256 and disk-binaries.json native4 entry',
        'corrected_expected_sha_length': len(required_sha),
    })
    suffix = uuid.uuid4().hex[:10]
    task_name = 'qa-resume-20261008-native4-env01-05-' + suffix
    task_source = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/env01_05_task_source.py').read_text()
    task_source = task_source.replace('__EXPECTED_BINARY_SHA256__', required_sha)
    if '__EXPECTED_BINARY_SHA256__' in task_source:
        raise RuntimeError('authoritative_sha_injection_failed')
    command = make_python_command(task_source)
    if token in command:
        raise RuntimeError('task_source_contains_auth_token')
    main_result = create_and_run(token, task_name, command, TARGET_ID, timeout=180)
    if main_result['status'] != 'SUCCESS' or not isinstance(main_result['output'], dict):
        raise RuntimeError('preflight_task_execution_failed')
    output = main_result['output']
    if output.get('status') != 'PASS':
        blocked_task_cleanup = {'task_id': main_result['task_id'], 'delete_http': None, 'get_after_delete_http': None}
        blocked_task_cleanup['delete_http'], _ = request(token, 'DELETE', '/api/tasks/' + urllib.parse.quote(main_result['task_id'], safe=''))
        blocked_task_cleanup['get_after_delete_http'] = route_status(token, '/api/tasks/' + urllib.parse.quote(main_result['task_id'], safe=''))
        write_json(OUT / 'env01_05-corrected-preflight-summary.json', {
            'status': 'BLOCKED',
            'block_code': output.get('block_code', 'preflight_gate_blocked'),
            'target_id': TARGET_ID,
            'target_name': TARGET_NAME,
            'mapping_source': 'Root preflight node map plus live API exact ID/name match',
            'execution_id': main_result['execution_id'],
            'task_id': main_result['task_id'],
            'command_sha256': main_result['command_sha256'],
            'preflight_output': output,
            'task_cleanup': blocked_task_cleanup,
            'credentials_recorded': False,
            'business_source_changed': False,
        })
        raise SystemExit(0)

    daemon_info = output.get('daemon')
    if not isinstance(daemon_info, dict):
        raise RuntimeError('preflight_daemon_manifest_missing')
    owned_dir = daemon_info.get('directory')
    if not isinstance(owned_dir, str) or not re.fullmatch(r'/tmp/qa-resume-20261008-child-[A-Za-z0-9_-]+', owned_dir):
        raise RuntimeError('preflight_owned_directory_name_invalid')
    preflight_json = {
        'task_id': main_result['task_id'],
        'execution_id': main_result['execution_id'],
        'command_sha256': main_result['command_sha256'],
        'output': output,
    }
    write_json(OUT / 'env01_05-primary-preflight-corrected.json', preflight_json)

    # A second, node-scoped Product Task verifies survival after the first execution ended.
    observer_source = OBSERVER_SOURCE.replace('__OWNED_DIR_JSON__', json.dumps(owned_dir))
    observer_source = observer_source.replace('__EXPECTED_MANIFEST_SHA256__', json.dumps(daemon_info.get('manifest_sha256', '')))
    observer_name = 'qa-resume-20261008-native4-daemon-observe-' + suffix
    observer = create_and_run(token, observer_name, make_python_command(observer_source), TARGET_ID, timeout=45)
    observer_output = observer.get('output') if isinstance(observer.get('output'), dict) else {}
    write_json(OUT / 'env01_05-daemon-observer.json', {
        'task_id': observer['task_id'],
        'execution_id': observer['execution_id'],
        'status': observer['status'],
        'command_sha256': observer['command_sha256'],
        'output': observer_output,
    })

    cleanup_source = CLEANUP_SOURCE.replace('__OWNED_DIR_JSON__', json.dumps(owned_dir))
    cleanup_source = cleanup_source.replace('__EXPECTED_MANIFEST_SHA256__', json.dumps(daemon_info.get('manifest_sha256', '')))
    cleanup_name = 'qa-resume-20261008-native4-daemon-cleanup-' + suffix
    cleanup = create_and_run(token, cleanup_name, make_python_command(cleanup_source), TARGET_ID, timeout=45)
    cleanup_output = cleanup.get('output') if isinstance(cleanup.get('output'), dict) else {}
    write_json(OUT / 'env01_05-daemon-cleanup.json', {
        'task_id': cleanup['task_id'],
        'execution_id': cleanup['execution_id'],
        'status': cleanup['status'],
        'command_sha256': cleanup['command_sha256'],
        'output': cleanup_output,
    })

    task_rows = []
    tasks_to_delete = [main_result['task_id'], observer['task_id'], cleanup['task_id']]
    if cleanup_output.get('status') != 'PASS':
        tasks_to_delete = tasks_to_delete[:-1]
    for task_id in tasks_to_delete:
        get_status = route_status(token, '/api/tasks/' + urllib.parse.quote(task_id, safe=''))
        delete_status, _ = request(token, 'DELETE', '/api/tasks/' + urllib.parse.quote(task_id, safe=''))
        after_status = route_status(token, '/api/tasks/' + urllib.parse.quote(task_id, safe=''))
        task_rows.append({'task_id': task_id, 'get_before_delete_http': get_status, 'delete_http': delete_status, 'get_after_delete_http': after_status})
    write_json(OUT / 'env01_05-task-cleanup.json', {'tasks': task_rows})
    final_nodes = get_json(token, '/api/nodes')
    final_by_id = {row.get('id'): row for row in final_nodes}
    final_health = []
    for node_id in sorted(required_ids):
        row = final_by_id.get(node_id, {})
        final_health.append({'id': node_id, 'name': row.get('hostname', row.get('name')), 'status': row.get('status'), 'sync_status': row.get('sync_status'), 'agent_version': row.get('agent_version')})
    healthz = route_status(token, '/healthz')
    readyz = route_status(token, '/readyz')
    baseline_by_id = {row['id']: row for row in pre_nodes}
    health_preserved = all(
        final_by_id.get(node_id, {}).get('status') == 'online'
        and final_by_id.get(node_id, {}).get('sync_status') == 'synced'
        and final_by_id.get(node_id, {}).get('agent_version') == baseline_by_id[node_id].get('agent_version')
        for node_id in required_ids
    )
    summary = {
        'status': 'PASS' if observer_output.get('status') == 'PASS' and cleanup_output.get('status') == 'PASS' and all(x['delete_http'] == 200 and x['get_after_delete_http'] == 404 for x in task_rows) and healthz == 200 and readyz == 200 and health_preserved else 'PARTIAL',
        'preflight_output': output,
        'observer_output': observer_output,
        'cleanup_output': cleanup_output,
        'task_cleanup': task_rows,
        'final_healthz_http': healthz,
        'final_readyz_http': readyz,
        'repair_nodes_after_cleanup': final_health,
        'four_repair_nodes_health_and_version_preserved': health_preserved,
        'credentials_recorded': False,
        'agent_started': False,
        'existing_service_or_config_mutated': False,
        'enrollment_or_proxy_started': False,
    }
    write_json(OUT / 'env01_05-summary.json', summary)
    print(json.dumps({
        'status': summary['status'],
        'target_id': TARGET_ID,
        'preflight_status': output.get('status'),
        'observer_status': observer_output.get('status'),
        'cleanup_status': cleanup_output.get('status'),
        'task_cleanup_http': [[x['delete_http'], x['get_after_delete_http']] for x in task_rows],
        'healthz_http': healthz,
        'readyz_http': readyz,
        'primary_access': 'none',
        'credentials_recorded': False,
    }, separators=(',', ':')))


OBSERVER_SOURCE = r'''import hashlib,json,os,stat,time
from pathlib import Path
D=Path(__OWNED_DIR_JSON__)
EXPECTED_MANIFEST_SHA=__EXPECTED_MANIFEST_SHA256__
def emit(v): print(json.dumps(v,separators=(',',':')),flush=True)
def fail(code): emit({'status':'BLOCKED','code':code}); raise SystemExit(0)
def start(pid):
 raw=Path('/proc',str(pid),'stat').read_text(); tail=raw[raw.rfind(')')+2:].split(); return int(tail[19])
def uid(pid):
 for line in Path('/proc',str(pid),'status').read_text().splitlines():
  if line.startswith('Uid:'): return tuple(map(int,line.split()[1:3]))
 raise ValueError('uid')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
if not str(D).startswith('/tmp/qa-resume-20261008-child-') or D.is_symlink(): fail('owned_directory_gate')
try:
 st=D.lstat(); mpath=D/'owner-manifest.json'; raw=mpath.read_bytes()
 if hashlib.sha256(raw).hexdigest()!=EXPECTED_MANIFEST_SHA: fail('manifest_hash_mismatch')
 m=json.loads(raw); nonce=m['owner_nonce']; marker=(D/'owner.marker').read_text()
 if marker!=nonce or hashlib.sha256(marker.encode()).hexdigest()[:16]!=m.get('owner_marker_sha256_prefix'): fail('owner_marker_mismatch')
 if (st.st_dev,st.st_ino)!=(m['directory_dev'],m['directory_ino']) or stat.S_ISLNK(st.st_mode): fail('directory_identity_mismatch')
 pid=int(m['pid']); expected_start=int(m['starttime']); expected_uid=int(m['uid']); expected_exe=m['exe_sha256']
 if start(pid)!=expected_start: fail('daemon_starttime_mismatch')
 if uid(pid)!=(expected_uid,expected_uid): fail('daemon_uid_mismatch')
 exe=os.readlink('/proc/'+str(pid)+'/exe')
 if exe.endswith(' (deleted)') or sha(exe)!=expected_exe: fail('daemon_exe_hash_mismatch')
 argv=[x.decode(errors='replace') for x in Path('/proc',str(pid),'cmdline').read_bytes().split(b'\0') if x]
 if not any(arg==str(D) or arg.startswith(str(D)+os.sep) for arg in argv): fail('daemon_argv_owner_path_mismatch')
 hb=D/'heartbeat'; first=hb.stat().st_mtime_ns; time.sleep(0.7); second=hb.stat().st_mtime_ns
 if second<=first: fail('daemon_heartbeat_not_advancing')
 emit({'status':'PASS','owned_directory_matches':True,'manifest_hash_matches':True,'owner_marker_matches':True,'pid_alive_after_initial_task_exit':True,'starttime_matches':True,'uid_matches':True,'exe_sha256_matches':True,'argv_contains_owned_directory':True,'heartbeat_advanced':True,'directory_mode':oct(stat.S_IMODE(st.st_mode))})
except SystemExit: raise
except BaseException: fail('observer_internal_failure')
'''

CLEANUP_SOURCE = r'''import hashlib,json,os,shutil,signal,stat,time
from pathlib import Path
D=Path(__OWNED_DIR_JSON__)
EXPECTED_MANIFEST_SHA=__EXPECTED_MANIFEST_SHA256__
def emit(v): print(json.dumps(v,separators=(',',':')),flush=True)
def start(pid):
 raw=Path('/proc',str(pid),'stat').read_text(); tail=raw[raw.rfind(')')+2:].split(); return int(tail[19])
def uid(pid):
 for line in Path('/proc',str(pid),'status').read_text().splitlines():
  if line.startswith('Uid:'): return tuple(map(int,line.split()[1:3]))
 raise ValueError('uid')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''): h.update(b)
 return h.hexdigest()
def same(pid,since,want_uid,want_exe,directory):
 try:
  if start(pid)!=since or uid(pid)!=(want_uid,want_uid): return False
  exe=os.readlink('/proc/'+str(pid)+'/exe')
  if exe.endswith(' (deleted)') or sha(exe)!=want_exe: return False
  argv=[x.decode(errors='replace') for x in Path('/proc',str(pid),'cmdline').read_bytes().split(b'\0') if x]
  return any(arg==str(directory) or arg.startswith(str(directory)+os.sep) for arg in argv)
 except BaseException: return False
if not str(D).startswith('/tmp/qa-resume-20261008-child-') or D.is_symlink(): emit({'status':'BLOCKED','code':'owned_directory_gate'}); raise SystemExit(0)
try:
 st=D.lstat(); raw=(D/'owner-manifest.json').read_bytes()
 if hashlib.sha256(raw).hexdigest()!=EXPECTED_MANIFEST_SHA: emit({'status':'BLOCKED','code':'manifest_hash_mismatch'}); raise SystemExit(0)
 m=json.loads(raw); pid=int(m['pid']); since=int(m['starttime']); expected_uid=int(m['uid']); expected_exe=m['exe_sha256']; nonce=m['owner_nonce']
 if (st.st_dev,st.st_ino)!=(m['directory_dev'],m['directory_ino']) or stat.S_ISLNK(st.st_mode): emit({'status':'BLOCKED','code':'directory_identity_mismatch'}); raise SystemExit(0)
 if (D/'owner.marker').read_text()!=nonce: emit({'status':'BLOCKED','code':'owner_marker_mismatch'}); raise SystemExit(0)
 matched=same(pid,since,expected_uid,expected_exe,D)
 if matched:
  os.kill(pid,signal.SIGTERM)
  deadline=time.monotonic()+5
  while time.monotonic()<deadline and same(pid,since,expected_uid,expected_exe,D): time.sleep(0.1)
  if same(pid,since,expected_uid,expected_exe,D):
   os.kill(pid,signal.SIGKILL)
   deadline=time.monotonic()+2
   while time.monotonic()<deadline and same(pid,since,expected_uid,expected_exe,D): time.sleep(0.1)
  if same(pid,since,expected_uid,expected_exe,D): emit({'status':'BLOCKED','code':'owned_pid_did_not_exit'}); raise SystemExit(0)
 elif Path('/proc',str(pid)).exists():
  try:
   if start(pid)==since: emit({'status':'BLOCKED','code':'pid_exists_identity_mismatch'}); raise SystemExit(0)
  except FileNotFoundError: pass
 if D.exists():
  now=D.lstat()
  if stat.S_ISLNK(now.st_mode) or (now.st_dev,now.st_ino)!=(m['directory_dev'],m['directory_ino']) or (D/'owner.marker').read_text()!=nonce:
   emit({'status':'BLOCKED','code':'cleanup_directory_owner_guard_failed'}); raise SystemExit(0)
  shutil.rmtree(D)
 emit({'status':'PASS','owned_pid_stopped_or_gone':True,'process_identity_verified_before_signal':matched,'owned_directory_removed':not D.exists(),'directory_dev_inode_verified':True,'owner_marker_verified':True})
except SystemExit: raise
except BaseException: emit({'status':'BLOCKED','code':'cleanup_internal_failure'})
'''


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # The message intentionally carries only a fixed failure category, never response bodies or credentials.
        print(json.dumps({'status': 'BLOCKED', 'code': str(error)}, separators=(',', ':')))
        raise SystemExit(2)
