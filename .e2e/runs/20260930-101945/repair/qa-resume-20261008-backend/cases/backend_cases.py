#!/usr/bin/env python3
"""真实隔离后端用例执行器；所有证据仅写入当前 cases 目录。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import uuid

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import run_env01_05 as api

HUB = 'http://192.168.100.209:18080'
CHILD = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
NATIVE4 = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
OLD_IDS = {'91f44866-8a28-435e-bbda-2f2367126c00', '611c02a5-1859-4465-878b-d543b7f6fdec', '37989297-3bc8-4c0c-b302-7c6efbdba630', NATIVE4}
OWNED_DIR = '/tmp/qa-resume-20261008-child-ciivjupc'
DIR_DEV, DIR_INO = 33, 135
MANIFEST_SHA = '475c97667c8475ddb6dc4cc8d24a1f225af47e8af949de17fa8354dafc76b940'
CHILD_PID, CHILD_START = 44027, 121232566
CHILD_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
SUP_PID, SUP_START = 44025, 121232540
SUP_SHA = '078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531'
api.OUT = HERE
TOKEN = api.read_private()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def checkpoint(case_id, step, **data):
    path = HERE / (case_id + '-checkpoint.json')
    value = {'case_id': case_id, 'step': step, 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **data}
    atomic(path, value)


def url(path):
    return urllib.parse.quote(str(path), safe='')


def api_get(path):
    return api.get_json(TOKEN, path)


def create_resource(kind, payload, label):
    name = payload.get('name', label)
    def_path = HERE / (label + '-definition.json')
    atomic(def_path, {'method': 'POST', 'path': '/api/' + kind + 's', 'payload': payload, 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    status, raw = api.request(TOKEN, 'POST', '/api/' + kind + 's', payload)
    if status != 200:
        atomic(HERE / (label + '-create-error.json'), {'status': status, 'response_bytes': len(raw), 'response_sha256': hashlib.sha256(raw).hexdigest()})
        raise RuntimeError(label + '_create_http_' + str(status))
    value = json.loads(raw)
    rid = value.get('id')
    if not rid:
        raise RuntimeError(label + '_create_id_missing')
    readback = api_get('/api/' + kind + 's/' + url(rid))
    if readback.get('name') != name:
        raise RuntimeError(label + '_definition_readback_mismatch')
    atomic(HERE / (label + '-created.json'), {'id': rid, 'name': name, 'create_http': status, 'revision': value.get('revision'), 'readback_http': 200, 'readback_revision': readback.get('revision'), 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return readback


def update_resource(kind, rid, changes, label):
    path = '/api/' + kind + 's/' + url(rid)
    current = api_get(path)
    payload = {**current, **changes}
    atomic(HERE / (label + '-definition.json'), {'method': 'PUT', 'path': path, 'payload': payload, 'before_revision': current.get('revision'), 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    status, raw = api.request(TOKEN, 'PUT', path, payload)
    if status != 200:
        atomic(HERE / (label + '-error.json'), {'status': status, 'response_bytes': len(raw), 'response_sha256': hashlib.sha256(raw).hexdigest()})
        raise RuntimeError(label + '_update_http_' + str(status))
    value = api_get(path)
    atomic(HERE / (label + '-updated.json'), {'id': rid, 'update_http': status, 'before_revision': current.get('revision'), 'revision': value.get('revision'), 'sha256': value.get('sha256'), 'content_sha256': hashlib.sha256(str(value.get('content', '')).encode()).hexdigest(), 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    return value


def delete_resource(kind, rid, label):
    status, raw = api.request(TOKEN, 'DELETE', '/api/' + kind + 's/' + url(rid))
    get_status = api.route_status(TOKEN, '/api/' + kind + 's/' + url(rid))
    atomic(HERE / (label + '-cleanup.json'), {'kind': kind, 'id': rid, 'delete_http': status, 'get_after_delete_http': get_status, 'delete_response_bytes': len(raw)})
    return status == 200 and get_status == 404


def create_task(label, command, node_id=CHILD, task_type='command', script_id=None, condition=None, timeout=30):
    payload = {'name': label, 'type': task_type, 'target': {'type': 'node', 'node_ids': [node_id]}, 'parameters': [], 'param_values': {}, 'command': command, 'timeout': timeout, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    if script_id:
        payload['script_id'] = script_id
    if condition:
        payload['condition'] = condition
    return create_resource('task', payload, label)


def run_task(task, label, expected_status='SUCCESS', timeout=90):
    path = '/api/tasks/' + url(task['id']) + '/run'
    atomic(HERE / (label + '-run-definition.json'), {'method': 'POST', 'path': path, 'payload': {'params': {}}, 'task_id': task['id'], 'target_node_id': task.get('target', {}).get('node_ids'), 'retry': task.get('retry'), 'schedule_present': bool(task.get('schedule')), 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    status, raw = api.request(TOKEN, 'POST', path, {'params': {}})
    if status != 200:
        raise RuntimeError(label + '_run_http_' + str(status))
    rows = json.loads(raw)
    if len(rows) != 1 or rows[0].get('node_id') != task.get('target', {}).get('node_ids', [None])[0]:
        raise RuntimeError(label + '_run_target_or_count_mismatch')
    execution_id = rows[0]['id']
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = api_get('/api/executions/' + url(execution_id))
        if result.get('status') not in {'PENDING', 'RUNNING'}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(label + '_execution_timeout')
    logs = api_get('/api/executions/' + url(execution_id) + '/logs')
    evidence = {'task_id': task['id'], 'execution_id': execution_id, 'node_id': result.get('node_id'), 'status': result.get('status'), 'exit_code': result.get('exit_code'), 'stdout': result.get('stdout', ''), 'stderr': result.get('stderr', ''), 'block_reason': result.get('block_reason', ''), 'revision': result.get('revision'), 'task_revision': result.get('task_revision'), 'script_revision': result.get('script_revision'), 'logs': logs}
    atomic(HERE / (label + '-execution.json'), evidence)
    if result.get('status') != expected_status:
        raise RuntimeError(label + '_terminal_' + str(result.get('status')) + '_expected_' + expected_status)
    return result


def create_native4_command(label, source, timeout=60):
    command = api.make_python_command(source)
    name = label + '-' + uuid.uuid4().hex[:10]
    result = api.create_and_run(TOKEN, name, command, NATIVE4, timeout=timeout)
    atomic(HERE / (label + '-task-result.json'), {'task_name': name, 'task_id': result.get('task_id'), 'execution_id': result.get('execution_id'), 'command_sha256': result.get('command_sha256'), 'target_node_id': NATIVE4, 'retry': 0, 'schedule_present': False, 'status': result.get('status'), 'output': result.get('output')})
    status = api.request(TOKEN, 'DELETE', '/api/tasks/' + url(result['task_id']))[0]
    get_status = api.route_status(TOKEN, '/api/tasks/' + url(result['task_id']))
    atomic(HERE / (label + '-task-cleanup.json'), {'task_id': result['task_id'], 'delete_http': status, 'get_after_delete_http': get_status})
    if status != 200 or get_status != 404:
        raise RuntimeError(label + '_task_cleanup_failed')
    return result


def child_db_probe(label, query, params=()):
    # Database opens read-only in the guest. Query must be a literal SQL string supplied by this harness.
    source = '''import json,sqlite3,pathlib\nroot=pathlib.Path("/tmp/qa-resume-20261008-child-ciivjupc/data")\ndb=root/"state.db"\nc=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3)\nc.row_factory=sqlite3.Row\nrows=[dict(r) for r in c.execute(%s,%s).fetchall()]\nprint(json.dumps(rows,separators=(",",":")))\n''' % (repr(query), repr(tuple(params)))
    return create_native4_command(label, source)


def child_db_wait(label, query, params=(), expected_count=1, seconds=25):
    source = '''import json,sqlite3,pathlib,time\nroot=pathlib.Path("/tmp/qa-resume-20261008-child-ciivjupc/data")\ndb=root/"state.db"\nend=time.monotonic()+%s\nrows=[]\nwhile time.monotonic()<end:\n c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3); c.row_factory=sqlite3.Row\n try: rows=[dict(r) for r in c.execute(%s,%s).fetchall()]\n finally: c.close()\n if len(rows)>=%s: break\n time.sleep(.25)\nprint(json.dumps({"rows":rows,"expected_count":%s,"ready":len(rows)>=%s},separators=(",",":")))\n''' % (seconds, repr(query), repr(tuple(params)), expected_count, expected_count, expected_count)
    return create_native4_command(label, source, timeout=seconds + 10)


def child_file_probe(label, expressions):
    source = 'import json,os\nresults={}'
    for key, path in expressions.items():
        source += '\np=' + repr(path) + '\nresults[' + repr(key) + ']=open(p, "rb").read().decode() if os.path.isfile(p) else None'
    source += '\nprint(json.dumps(results,separators=(",",":")))\n'
    return create_native4_command(label, source)


if __name__ == '__main__':
    raise SystemExit('Import helpers and run an explicitly named case function; no implicit mutations.')
