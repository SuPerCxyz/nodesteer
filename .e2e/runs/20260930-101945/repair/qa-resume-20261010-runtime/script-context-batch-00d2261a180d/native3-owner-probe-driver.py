import argparse, datetime, hashlib, json, pathlib, shlex, stat, time, urllib.error, urllib.request

BASE = 'http://192.168.100.209:18080'
NODE = '37989297-3bc8-4c0c-b302-7c6efbdba630'
AGENT = 'agent-bb005487-7d84-418c-b598-f93d8b0c86a0'
EXPECTED_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
EXPECTED_UNIT = 'nodesteer-agent-repair-20260930-101945-native-3.service'
EXPECTED_CONFIG = '/etc/nodesteer/repair-20260930-101945/native-3.yaml'

p = argparse.ArgumentParser()
p.add_argument('--token-file', required=True)
p.add_argument('--probe-source', required=True)
p.add_argument('--task-name', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
token_path = pathlib.Path(a.token_file)
st = token_path.stat()
if stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != __import__('os').getuid():
    raise SystemExit('private token file guard failed')
token = token_path.read_text()
source = pathlib.Path(a.probe_source).read_text()
source_sha = hashlib.sha256(source.encode()).hexdigest()
if source_sha != '63e6b82f9857433debab64ada4afa185474993c2b6c7e7dfd0e3826dac92d047':
    raise SystemExit('approved readonly source hash mismatch')
compile(source, a.probe_source, 'exec')
command = 'python3 -c ' + shlex.quote(source)
command_sha = hashlib.sha256(command.encode()).hexdigest()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def api(method, path, body=None, allowed=(200,)):
    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=20) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    if status not in allowed:
        raise RuntimeError(f'{method} {path} HTTP {status}')
    return status, (json.loads(raw) if raw else None)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')

result = {'started_at_utc': utc(), 'task_name': a.task_name, 'source_sha256': source_sha,
          'command_sha256': command_sha, 'node_id': NODE, 'agent_id_expected': AGENT,
          'retry': 0, 'timeout': 60, 'schedule_present': False,
          'token_value_emitted': False, 'source_code_emitted': False}
task_id = None
try:
    _, node = api('GET', '/api/nodes/' + NODE)
    result['fresh_node'] = {'id_match': node.get('id') == NODE,
        'agent_id_match': node.get('agent_id') == AGENT,
        'status': node.get('status'), 'sync_status': node.get('sync_status'),
        'global_revision': node.get('global_revision')}
    if not result['fresh_node']['id_match'] or not result['fresh_node']['agent_id_match'] or node.get('status') != 'online' or node.get('sync_status') != 'synced':
        raise RuntimeError('fresh Native3 API identity/health guard failed')
    _, current_tasks = api('GET', '/api/tasks')
    if any(x.get('name') == a.task_name for x in current_tasks):
        raise RuntimeError('readonly Task name collision')
    task = {'name': a.task_name, 'type': 'command', 'command': command,
        'target': {'type': 'node', 'node_ids': [NODE]}, 'parameters': [], 'param_values': {},
        'timeout': 60, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    _, created = api('POST', '/api/tasks', task, (200, 201))
    task_id = created.get('id')
    if not task_id:
        raise RuntimeError('readonly Task response did not include ID')
    result['task_id'] = task_id
    _, readback = api('GET', '/api/tasks/' + task_id)
    if (readback.get('id') != task_id or readback.get('type') != 'command' or
        readback.get('target', {}).get('node_ids') != [NODE] or readback.get('retry') != 0 or
        readback.get('timeout') != 60 or hashlib.sha256(readback.get('command', '').encode()).hexdigest() != command_sha):
        raise RuntimeError('exact readonly Task readback mismatch')
    result['task_readback_pass'] = True
    _, schedules = api('GET', '/api/schedules')
    result['schedule_reference_absent'] = not any(x.get('task_id') == task_id for x in schedules)
    if not result['schedule_reference_absent']:
        raise RuntimeError('unexpected Schedule reference')
    run_status, body = api('POST', '/api/tasks/' + task_id + '/run', {'params': {}}, (200, 201))
    rows = body if isinstance(body, list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body, dict) else []
    if len(rows) != 1 or not rows[0].get('id'):
        raise RuntimeError('readonly probe Run response cardinality mismatch')
    execution_id = rows[0]['id']
    result['execution_id'] = execution_id
    result['run_http_status'] = run_status
    deadline = time.monotonic() + 60
    final = None
    while time.monotonic() < deadline:
        _, final = api('GET', '/api/executions/' + execution_id)
        if final.get('status') in {'SUCCESS', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}:
            break
        time.sleep(.5)
    if not final or final.get('status') not in {'SUCCESS', 'FAILED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}:
        raise RuntimeError('readonly probe Execution did not reach terminal state')
    _, chunks = api('GET', '/api/executions/' + execution_id + '/logs')
    chunks = chunks if isinstance(chunks, list) else []
    stdout = ''.join(x.get('chunk', '') for x in sorted(chunks, key=lambda z: (z.get('stream', ''), z.get('seq', 0))) if x.get('stream') == 'stdout')
    owner = json.loads(stdout) if stdout else None
    result['execution'] = {'id': final.get('id'), 'task_id': final.get('task_id'),
        'node_id': final.get('node_id'), 'status': final.get('status'),
        'exit_code': final.get('exit_code'), 'trigger_type': final.get('trigger_type'),
        'offline': final.get('offline'), 'synced': final.get('synced')}
    result['stdout_sha256'] = hashlib.sha256(stdout.encode()).hexdigest()
    result['stdout_bytes'] = len(stdout.encode())
    result['owner_observation'] = owner
    result['completed_at_utc'] = utc()
except Exception as e:
    result['safe_error_type'] = type(e).__name__
    result['safe_error'] = str(e)[:200]
finally:
    result['task_delete_status'] = None
    result['task_get_404'] = False
    if task_id:
        try:
            result['task_delete_status'] = api('DELETE', '/api/tasks/' + task_id, None, (200, 204))[0]
        except Exception as e:
            result['task_delete_safe_error_type'] = type(e).__name__
        try:
            api('GET', '/api/tasks/' + task_id, None, (404,))
            result['task_get_404'] = True
        except Exception as e:
            result['task_get404_safe_error_type'] = type(e).__name__
    result['cleanup_completed_at_utc'] = utc()
    out = pathlib.Path(a.output)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'task_id': result.get('task_id'), 'execution_id': result.get('execution_id'),
    'execution_status': result.get('execution', {}).get('status'),
    'task_get_404': result.get('task_get_404'), 'safe_error_type': result.get('safe_error_type'),
    'owner_result_present': bool(result.get('owner_observation'))}, ensure_ascii=False))
