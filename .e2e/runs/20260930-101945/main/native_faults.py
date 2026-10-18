"""仅本轮新安装的 Rocky Agent3：真实重启和托管应用阶段崩溃。"""
import base64
import concurrent.futures
import hashlib
import json
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads(Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json').read_text())
NODE_ID = '811d72ef-75f8-47ba-84de-8cb1e6974315'
NODE = next(x for x in CFG['nodes'] if x['id'] == NODE_ID)
BASE = CFG['base_url']
HEADERS = {'Authorization': 'Bearer ' + CFG['admin_token']}
PREFIX = 'qa-20260930-101945-main'
DIRECTORY = '/tmp/' + PREFIX + '/native'
LEDGER = json.loads((ROOT / 'native-ledger.json').read_text()) if (ROOT / 'native-ledger.json').exists() else []
RESULTS = json.loads((ROOT / 'native-results.json').read_text()) if (ROOT / 'native-results.json').exists() else []


def api(method, path, body=None, **kwargs):
    r = requests.request(method, BASE + path, headers=HEADERS, json=body, timeout=30, **kwargs)
    if r.status_code not in (200, 201, 204):
        raise RuntimeError(f'{method} {path}: {r.status_code} {r.text[:300]}')
    return r.json() if r.content else None


def remote(script, timeout=120):
    r = subprocess.run(NODE['executor_argv'], input=script, text=True, capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f'guest exit {r.returncode}: {r.stderr[-500:]}')
    return r.stdout.strip()


def save():
    (ROOT / 'native-results.json').write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + '\n')
    (ROOT / 'native-ledger.json').write_text(json.dumps(LEDGER, ensure_ascii=False, indent=2) + '\n')


def create(kind, payload):
    x = api('POST', '/api/' + kind, payload)
    LEDGER.append({'kind': kind, 'id': x['id'], 'name': x.get('name'), 'status': 'created'})
    save()
    return x


def ready(timeout=70):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        n = api('GET', '/api/nodes/' + NODE_ID)
        if n['status'] == 'online' and n['sync_status'] == 'synced':
            return n
        time.sleep(1)
    raise RuntimeError('本轮Agent3没有恢复online/synced')


def terminal(eid, timeout=90):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        x = api('GET', '/api/executions/' + eid)
        if x['status'] not in ['PENDING', 'RUNNING']:
            return x
        time.sleep(.5)
    raise RuntimeError('执行未终结 ' + eid)


def deploy(app, operation='deploy'):
    xs = api('POST', '/api/applications/' + app['id'] + '/deploy',
             {'node_ids': [NODE_ID], 'operation': operation})
    assert len(xs) == 1
    return xs[0]['id']


def state(app, directory, unit):
    script = f'''import json,os,hashlib,sqlite3,subprocess
p={json.dumps(directory)}; app={json.dumps(app['id'])}; unit={json.dumps(unit)}
c=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True)
rows=c.execute('select id,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? order by started_at desc',(app,)).fetchall()
out={{'journal':rows,'binary_exists':os.path.exists(p+'/app-bin'),'config_exists':os.path.exists(p+'/app.conf'),'unit_exists':os.path.exists('/etc/systemd/system/'+unit),'unit_state':subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True).stdout.strip()}}
if out['binary_exists']:out['binary_sha256']=hashlib.sha256(open(p+'/app-bin','rb').read()).hexdigest()
if out['config_exists']:out['config']=open(p+'/app.conf').read()
print(json.dumps(out))
'''
    return json.loads(remote('python3 - <<\'PY\'\n' + script + '\nPY\n'))


def restart_case():
    marker = DIRECTORY + '/on-start'
    task = create('tasks', {'name': PREFIX + '-native-start-' + uuid.uuid4().hex[:6], 'type': 'command',
        'target': {'type': 'node', 'node_ids': [NODE_ID]},
        'command': 'mkdir -p ' + shlex.quote(DIRECTORY) + '; printf "boot\\n" >> ' + shlex.quote(marker),
        'timeout': 10, 'enabled': True, 'offline_policy': 'allow_offline'})
    schedule = create('schedules', {'task_id': task['id'], 'type': 'on_start', 'execution_owner': 'agent',
        'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'enabled': True})
    time.sleep(2)
    before = api('GET', '/api/nodes/' + NODE_ID)
    before_count = int(remote('test ! -f ' + shlex.quote(marker) + ' || wc -l < ' + shlex.quote(marker)) or '0')
    remote('systemctl restart nodesteer-agent.service\nsystemctl is-active nodesteer-agent.service\n')
    after = ready()
    until = time.monotonic() + 40
    count = before_count
    while time.monotonic() < until:
        count = int(remote('test ! -f ' + shlex.quote(marker) + ' || wc -l < ' + shlex.quote(marker)) or '0')
        if count > before_count:
            break
        time.sleep(1)
    row = {'case_id': 'MAIN-NATIVE-RESTART-ONSTART', 'classification': 'REAL_API_AGENT',
           'before': {'agent_id': before['agent_id'], 'counter': before_count},
           'after': {'agent_id': after['agent_id'], 'counter': count, 'status': after['status'], 'sync': after['sync_status']},
           'status': 'PASS' if after['agent_id'] == before['agent_id'] and count == before_count + 1 else 'FAIL'}
    RESULTS.append(row)
    save()
    api('DELETE', '/api/schedules/' + schedule['id'])
    api('DELETE', '/api/tasks/' + task['id'])
    for x in LEDGER:
        if x['id'] in [schedule['id'], task['id']]:
            x['status'] = 'deleted'
    save()
    print(row['case_id'], row['status'], flush=True)


def phase_case(phase):
    suffix = uuid.uuid4().hex[:8]
    directory = DIRECTORY + '/phase-' + suffix
    unit = PREFIX + '-phase-' + suffix + '.service'
    fixture = base64.b64decode(remote('base64 /bin/sh\n'))
    sha = hashlib.sha256(fixture).hexdigest()
    r = requests.post(BASE + '/api/artifacts', headers=HEADERS,
        data={'name': PREFIX + '-phase-artifact-' + suffix, 'version': 'v1', 'architecture': 'amd64'},
        files={'file': ('fixture.bin', fixture, 'application/octet-stream')}, timeout=30)
    r.raise_for_status()
    artifact = r.json()
    LEDGER.append({'kind': 'artifacts', 'id': artifact['id'], 'status': 'created'})
    arguments = ['-c', 'trap "" TERM; while :; do sleep 1; done'] if phase == 'PREPARING' else ['-c', 'exec sleep 600']
    app = create('applications', {'name': PREFIX + '-phase-' + suffix, 'version': 'v1',
        'artifact_id': artifact['id'], 'binary_path': directory + '/app-bin',
        'config_path': directory + '/app.conf', 'config': 'config-v1\n', 'unit_name': unit,
        'arguments': arguments, 'health_check': {'type': 'systemd', 'timeout': 2, 'attempts': 5, 'interval': 1}})
    api('POST', '/api/applications/' + app['id'] + '/assign', {'node_ids': [NODE_ID]})
    row = {'case_id': 'MAIN-PHASE-' + phase, 'classification': 'REAL_API_AGENT', 'application_id': app['id'],
           'unit': unit, 'path': directory, 'status': 'RUNNING'}
    try:
        initial = terminal(deploy(app))
        assert initial['status'] == 'SUCCESS', 'v1基线部署失败'
        row['baseline'] = state(app, directory, unit)
        assert row['baseline']['config'] == 'config-v1\n' and row['baseline']['binary_sha256'] == sha
        app.update(version='v2', config='config-v2\n', arguments=['-c', 'exec sleep 600'])
        if phase == 'VERIFYING':
            app['health_check'] = {'type': 'command', 'target': 'sleep 3; false', 'timeout': 4, 'attempts': 8, 'interval': 1}
        if phase == 'ROLLING_BACK':
            app['arguments'] = ['-c', 'exit 17']
            app['health_check'] = {'type': 'systemd', 'timeout': 1, 'attempts': 2, 'interval': 1}
        app = api('PUT', '/api/applications/' + app['id'], app)
        watch = directory + '/watch-ready'
        monitor = f'''import os,time,json,signal,sqlite3,subprocess
app={json.dumps(app['id'])}; target={json.dumps(phase)}; unit={json.dumps(unit)}
pid=int(subprocess.check_output(['systemctl','show','nodesteer-agent.service','-p','MainPID','--value']))
assert 'nodesteer-agent' in os.readlink('/proc/'+str(pid)+'/exe')
c=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True,timeout=.2)
open({json.dumps(watch)},'w').write(str(pid))
until=time.monotonic()+18; out={{'observed':False,'target':target}}
while time.monotonic()<until:
 try:r=c.execute('select id,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? and phase=? order by started_at desc limit 1',(app,target)).fetchone()
 except sqlite3.OperationalError:r=None
 if r:
  os.kill(pid,signal.SIGSTOP)
  current=c.execute('select phase,backup_path,backup_config_path,backup_unit_path from deployments where id=?',(r[0],)).fetchone()
  if current[0]!=target:
   os.kill(pid,signal.SIGCONT);out={{'observed':False,'target':target,'raced_to':current[0]}};break
  out={{'observed':True,'target':target,'stopped_phase':current[0],'backup_present':[bool(x) for x in current[1:]],'deployment_id':r[0],'agent_pid':pid}}
  os.kill(pid,signal.SIGKILL)
  subprocess.run(['systemctl','kill','--signal=KILL',unit],capture_output=True)
  break
 time.sleep(.001)
print(json.dumps(out))
'''
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(remote, "python3 - <<'PY'\n" + monitor + '\nPY\n', 35)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if remote('test -f ' + shlex.quote(watch) + ' && echo ready || true\n') == 'ready':
                    break
                time.sleep(.2)
            eid = deploy(app, 'upgrade')
            row['execution_id'] = eid
            row['injection'] = json.loads(future.result())
        if not row['injection']['observed']:
            row.update(status='BLOCKED', reason='未能在目标阶段精确暂停进程；不把其他阶段算该阶段通过')
            terminal(eid)
        else:
            remote('systemctl start nodesteer-agent.service\n')
            ready(timeout=150)
            deadline = time.monotonic() + 30
            actual = state(app, directory, unit)
            while actual['journal'][0][1] != 'DONE' and time.monotonic() < deadline:
                time.sleep(1)
                actual = state(app, directory, unit)
            row['recovered'] = actual
            row['execution_terminal'] = terminal(eid)
            ok = actual.get('config') == 'config-v1\n' and actual.get('binary_sha256') == sha and actual['unit_state'] == 'active'
            row.update(status='PASS' if ok else 'FAIL', reason='崩溃后恢复v1 Binary/Config/Unit和真实active服务' if ok else '崩溃后旧版本文件或服务未恢复')
    except Exception as exc:
        row.update(status='FAIL', reason=str(exc))
    finally:
        remote('systemctl start nodesteer-agent.service\n')
        ready()
        remote('systemctl kill --signal=KILL ' + shlex.quote(unit) + ' 2>/dev/null || true\n')
        api('DELETE', '/api/applications/' + app['id'])
        api('DELETE', '/api/artifacts/' + artifact['id'])
        for x in LEDGER:
            if x['id'] in [app['id'], artifact['id']]:
                x['status'] = 'deleted'
        RESULTS.append(row)
        save()
        print(row['case_id'], row['status'], row.get('reason', ''), flush=True)


if __name__ == '__main__':
    remote('mkdir -p ' + shlex.quote(DIRECTORY) + '\n')
    if not sys.argv[1:]:
        restart_case()
    for target in (sys.argv[1:] or ['PREPARING', 'REPLACED', 'STARTED', 'VERIFYING', 'ROLLING_BACK']):
        assert target in ['PREPARING', 'REPLACED', 'STARTED', 'VERIFYING', 'ROLLING_BACK']
        phase_case(target)
    if not sys.argv[1:]:
        RESULTS.append({'case_id': 'MAIN-PHASE-STOPPED', 'status': 'BLOCKED', 'classification': 'STATIC_LIMITATION',
                        'reason': '当前源码PhaseStopped只有声明没有写入；停止服务处于PREPARING，未伪造STOPPED阶段测试'})
    save()
