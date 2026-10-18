#!/usr/bin/env python3
"""Run one exact-owner-guarded RuntimeMax extension Task via repair gateway only."""
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
OUT = HERE / 'child-runtime-extension-evidence.json'
NODE_ID = '91f44866-8a28-435e-bbda-2f2367126c00'
NONCE = '870358cd92ee'
conn = json.loads((PRIVATE / 'repair-connection.json').read_text())
base, token = conn['hub_url'].rstrip('/'), conn['token']
if urlsplit(base).port != 18080:
    raise SystemExit('blocked: only the repair :18080 gateway is allowed')
opener = build_opener(ProxyHandler({}))


def api(method, path, body=None, allowed=(200,)):
    headers = {'Authorization': 'Bearer ' + token}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=15) as response:
            status, payload = response.status, response.read()
    except HTTPError as exc:
        status, payload = exc.code, exc.read()
    except (URLError, TimeoutError) as exc:
        raise RuntimeError('repair API transport failed: ' + type(exc).__name__)
    if status not in allowed:
        raise RuntimeError(f'{method} {path}: unexpected HTTP {status}')
    return (status, json.loads(payload) if payload else None)


source = (HERE / 'extend_owned_child_runtime.py').read_text()
command = 'python3 -c ' + shlex.quote(source)
command_sha = hashlib.sha256(command.encode()).hexdigest()
task = {'name': 'qa-resume-20261009-native1-runtime-extension-' + NONCE,
        'type': 'command', 'target': {'type': 'node', 'node_ids': [NODE_ID]},
        'parameters': [], 'command': command, 'timeout': 60, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True}
evidence = {'case_id': 'E2E-20260930-101945-P0-002', 'action': 'extend only the approved owned transient Agent Unit RuntimeMaxSec to 3600 after exact owner guard', 'endpoint_guard': {'host': urlsplit(base).hostname, 'port': urlsplit(base).port, 'only_repair_18080': True}, 'target_node_id': NODE_ID, 'source_file': 'extend_owned_child_runtime.py', 'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'command_sha256': command_sha, 'task_definition': {k:v for k,v in task.items() if k!='command'}, 'started_unix': int(time.time())}
task_id = None
try:
    _, created = api('POST', '/api/tasks', task, (200,201)); task_id = created['id']; evidence['task_id'] = task_id
    _, fetched = api('GET', '/api/tasks/' + task_id)
    if hashlib.sha256(fetched.get('command','').encode()).hexdigest() != command_sha:
        raise RuntimeError('Task command readback SHA mismatch')
    _, runs = api('POST', '/api/tasks/' + task_id + '/run', {'params': {}}, (200,201))
    if len(runs) != 1: raise RuntimeError('expected one extension Execution')
    eid = runs[0]['id']; evidence['execution_id'] = eid
    deadline = time.monotonic()+70; final=None
    while time.monotonic()<deadline:
        _, final=api('GET','/api/executions/'+eid)
        if final.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}: break
        time.sleep(.25)
    stdout=(final or {}).get('stdout') or ''
    evidence['execution']={k:final.get(k) for k in ['id','task_id','node_id','status','exit_code','trigger_type','started_at','ended_at'] if final and k in final}
    evidence['execution']['stdout_json']=json.loads(stdout) if stdout else None
    evidence['execution']['stdout_sha256']=hashlib.sha256(stdout.encode()).hexdigest()
    evidence['execution']['stderr_bytes']=len(((final or {}).get('stderr') or '').encode())
    evidence['status']='EXTENSION_'+str((final or {}).get('status'))
finally:
    if task_id:
        d,_=api('DELETE','/api/tasks/'+task_id,allowed=(200,204))
        g,_=api('GET','/api/tasks/'+task_id,allowed=(404,))
        evidence['task_cleanup']={'delete_http_status':d,'verify_get_http_status':g,'verified_404':g==404}
    evidence['completed_unix']=int(time.time())
    OUT.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':evidence.get('status'),'task_id':task_id,'execution_id':evidence.get('execution_id'),'cleanup_verified_404':evidence.get('task_cleanup',{}).get('verified_404')},ensure_ascii=False))
