"""仅Native2/3及唯一临时Docker节点：真实批量升级的混合/去重/暂停/离线。"""
import json
import shlex
import subprocess
import time
import uuid

import requests
import native_faults as f

root = f.ROOT
c = f.CFG
login = requests.post(f.BASE + '/api/login', json={'username': c['admin_username'], 'password': c['admin_password']}, timeout=20)
login.raise_for_status()
f.HEADERS = {'Authorization': 'Bearer ' + login.json()['token']}
node2 = 'd77e2440-919f-4431-a488-733817dcd809'
node3 = f.NODE_ID
prefix = 'qa-20260930-101945-main-upgrade-' + uuid.uuid4().hex[:8]
rows = []
docker_id = None
container_id = None
stopped_pid = None


def record(case, ok, **detail):
    rows.append({'case_id': case, 'classification': 'REAL_API_AGENT', 'status': 'PASS' if ok else 'FAIL', 'detail': detail})
    (root / 'bulk-upgrade-results.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
    print(case, rows[-1]['status'], flush=True)


def hub(code, payload):
    argv = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-i', f.NODE['ssh_identity_file'], c['hub']['ssh_user'] + '@' + c['hub']['ip'], 'python3 -c ' + shlex.quote(code)]
    result = subprocess.run(argv, input=json.dumps(payload), text=True, capture_output=True, timeout=40)
    if result.returncode:
        raise RuntimeError('owned upgrade Docker step failed, exit ' + str(result.returncode))
    return json.loads(result.stdout)


def online(node_id, timeout=50):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = f.api('GET', '/api/nodes/' + node_id)
        if value['status'] == 'online' and value['sync_status'] == 'synced':
            return value
        time.sleep(.4)
    raise RuntimeError('upgrade target online/synced timeout')


try:
    prepared = f.api('POST', '/api/nodes/enrollment', {'node_name': prefix, 'node_ip': prefix + '.example.test', 'hub_address': f.BASE})
    docker_id = prepared['node_id']
    reg = next(line.split(':', 1)[1].strip() for line in prepared['docker_compose'].splitlines() if 'NODESTEER_REGISTRATION_TOKEN:' in line)
    token = json.loads(reg)
    created = hub('''import json,pathlib,subprocess,os
os.umask(0o077);x=json.load(__import__('sys').stdin);name=x['name'];root=pathlib.Path('/root/'+name);root.mkdir();env=root/'agent.env';values={'NODESTEER_HUB_URL':x['gateway'],'NODESTEER_REGISTRATION_TOKEN':x['registration'],'NODESTEER_AGENT_ID':x['agent_id'],'NODESTEER_NODE_NAME':name,'NODESTEER_NODE_IP':name+'.example.test','NODESTEER_DEPLOYMENT_MODE':'docker','NODESTEER_DATA_DIR':'/var/lib/nodesteer'};env.write_text(''.join(k+'='+v+chr(10) for k,v in values.items()));vol=name+'data';subprocess.run(['docker','volume','create','--label','nodesteer-e2e-run=20260930-101945',vol],capture_output=True,check=True);args=['docker','run','-d','--name',name,'--label','nodesteer-e2e-run=20260930-101945','--memory','96m','--cpus','0.25','--network','host','--env-file',str(env),'-v',vol+':/var/lib/nodesteer','qa-20260930-101945-env-agent:dddee4f0'];cid=subprocess.check_output(args,text=True).strip();print(json.dumps({'container_id':cid,'volume':vol,'owned_dir':str(root)}))
''', {'name': prefix, 'gateway': prepared['gateway_url'], 'registration': token, 'agent_id': prepared['agent_id']})
    container_id = created['container_id']
    before = {nid: online(nid) for nid in [node2, node3, docker_id]}
    for status in ['maintenance', 'disabled']:
        f.api('PUT', '/api/nodes/' + node3, {'status': status})
        result = f.api('POST', '/api/nodes/upgrade', {'node_ids': [node3]})[0]
        record('MAIN-UPGRADE-' + status.upper(), result['status'] == 'SKIPPED' and result.get('block_reason') == 'node paused' and not result.get('dispatched'), result=result)
    f.api('PUT', '/api/nodes/' + node3, {'status': 'online'})
    online(node3)
    stopped_pid = int(f.remote('systemctl show nodesteer-agent.service -p MainPID --value\n'))
    f.remote("python3 - <<'PY'\nimport os,signal\npid=" + str(stopped_pid) + "\nassert 'nodesteer-agent' in os.readlink('/proc/'+str(pid)+'/exe');os.kill(pid,signal.SIGSTOP)\nPY\n")
    try:
        first = f.api('POST', '/api/nodes/upgrade', {'node_ids': [node2, node3, docker_id, node3]})
        second = f.api('POST', '/api/nodes/upgrade', {'node_ids': [node3]})[0]
        initial = next(row for row in first if row['node_id'] == node3)
        record('MAIN-UPGRADE-BATCH-AND-INFLIGHT-DEDUPE', len(first) == 3 and second.get('deduplicated') is True and second['execution_id'] == initial['execution_id'], first=first, duplicate=second)
    finally:
        f.remote("python3 - <<'PY'\nimport os,signal\npid=" + str(stopped_pid) + "\nassert 'nodesteer-agent' in os.readlink('/proc/'+str(pid)+'/exe');os.kill(pid,signal.SIGCONT)\nPY\n")
        stopped_pid = None
    terminal = [f.terminal(row['execution_id'], timeout=70) for row in first]
    record('MAIN-UPGRADE-MIXED-NATIVE-DOCKER', [x['status'] for x in terminal] == ['SUCCESS', 'SUCCESS', 'SKIPPED'], executions=[{k:x.get(k) for k in ['id', 'node_id', 'status', 'block_reason']} for x in terminal])
    after = {nid: online(nid, timeout=70) for nid in [node2, node3, docker_id]}
    record('MAIN-UPGRADE-IDENTITY-VERSION-SYNC', all(before[nid]['agent_id'] == after[nid]['agent_id'] and after[nid]['agent_version'] == 'dddee4f0' for nid in after), nodes=[{k:v.get(k) for k in ['id', 'agent_id', 'agent_version', 'status', 'sync_status']} for v in after.values()])
    f.remote('systemctl stop nodesteer-agent.service\n')
    try:
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if f.api('GET', '/api/nodes/' + node3)['status'] == 'offline':
                break
            time.sleep(.3)
        state = f.api('GET', '/api/nodes/' + node3)
        assert state['status'] == 'offline'
        mixed = f.api('POST', '/api/nodes/upgrade', {'node_ids': [node2, node3, docker_id]})
        finished = [f.terminal(x['execution_id'], timeout=70) for x in mixed]
        record('MAIN-UPGRADE-PARTIAL-OFFLINE-FAILURE', [x['status'] for x in finished] == ['SUCCESS', 'FAILED', 'SKIPPED'] and 'not online' in finished[1].get('block_reason', ''), results=mixed)
    finally:
        f.remote('systemctl start nodesteer-agent.service\n')
        online(node3, timeout=70)
    prior = f.api('GET', '/api/executions?task_id=agent_upgrade&node_id=' + node2 + '&limit=100')
    invalid = requests.post(f.BASE + '/api/nodes/upgrade', headers=f.HEADERS, json={'node_ids': [node2, 'qa-nonexistent']}, timeout=20)
    later = f.api('GET', '/api/executions?task_id=agent_upgrade&node_id=' + node2 + '&limit=100')
    record('MAIN-UPGRADE-INVALID-BATCH-ATOMIC', invalid.status_code == 400 and {x['id'] for x in prior} == {x['id'] for x in later}, http=invalid.status_code, new_execution_count=len({x['id'] for x in later} - {x['id'] for x in prior}))
finally:
    if stopped_pid is not None:
        f.remote('kill -CONT ' + str(stopped_pid) + '\n')
    f.remote('systemctl start nodesteer-agent.service\n')
    f.api('PUT', '/api/nodes/' + node3, {'status': 'online'})
    if container_id:
        result = hub('''import json,pathlib,subprocess
x=json.load(__import__('sys').stdin);name=x['name'];actual=json.loads(subprocess.check_output(['docker','inspect',name]))[0];assert actual['Id']==x['id'];subprocess.run(['docker','rm','-f',x['id']],capture_output=True,check=True);subprocess.run(['docker','volume','rm',name+'data'],capture_output=True,check=True);root=pathlib.Path('/root/'+name);(root/'agent.env').unlink();root.rmdir();print(json.dumps({'container_removed':True,'volume_removed':True,'private_config_removed':True}))
''', {'name': prefix, 'id': container_id})
    if docker_id:
        f.api('DELETE', '/api/nodes/' + docker_id)
    if container_id:
        record('MAIN-UPGRADE-EXACT-CLEANUP', all(result.values()) and online(node3)['agent_version'] == 'dddee4f0', cleanup=result, deleted_node_id=docker_id)
    requests.post(f.BASE + '/api/logout', headers=f.HEADERS, timeout=20)
