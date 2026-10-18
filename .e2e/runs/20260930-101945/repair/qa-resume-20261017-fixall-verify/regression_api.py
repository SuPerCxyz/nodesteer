#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: Go-side regression on the deployed fixall build.

Build: dddee4f0-e2e-repair-20261017-fixall (hub 1d8cc17e…, agent 9702d2c7…).
Authority: repair hub http://192.168.100.209:18080 only.

Covers: ENVFILE runtime effect, rollback false-failure, assign/deploy 404,
exit_code 0, parameter type, whitespace username, file-transfer failure path +
cancel, and creates the owned fixtures the web phase consumes.

Every fixture is nonce-prefixed and owned; nothing protected is touched.
"""
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import hubclient  # noqa: E402
import hostlib  # noqa: E402

EV = HERE / 'evidence'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LABEL = 'dddee4f0-e2e-repair-20261017-fixall'
HUB_SHA = '1d8cc17e3aeeb5df59026bc3881a6ff8b5a3e308617c931ac1f13d70a9cc230b'
AGENT_SHA = '9702d2c7bd2174f1499ef8019b186544afbf4948cdd3bb08945f8b2b63f12adf'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}

os.umask(0o077)
hub = hubclient.Hub()


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {'nonce': secrets.token_hex(4), 'fixtures': [], 'phases': {}, 'cases': [],
            'children': {}, 'ids': {}}


def save_state(st):
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, STATE_F)


ST = load_state()
NONCE = ST['nonce']
P = 'qa-fixall-%s-' % NONCE


def record(case, phase, status, conclusion, evidence=None, extra=None):
    row = {'case_id': case, 'phase': phase, 'at_utc': now_utc(), 'status': status,
           'conclusion': conclusion, 'evidence': evidence or [], 'extra': extra or {}}
    ST['cases'] = [c for c in ST['cases'] if c.get('case_id') != case]
    ST['cases'].append(row)
    save_state(ST)
    print('[%s] %s -> %s' % (phase, case, status), flush=True)
    return row


def dump_results():
    payload = {'batch': 'qa-resume-20261017-fixall-verify', 'authority': 'http://192.168.100.209:18080',
               'build_label': LABEL, 'hub_sha256': HUB_SHA, 'agent_sha256': AGENT_SHA,
               'updated_at_utc': now_utc(), 'nonce': NONCE, 'cases': ST['cases']}
    tmp = RESULTS_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, RESULTS_F)


def ev_write(name, value):
    path = EV / name
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
    return 'evidence/' + name


def add_fixture(kind, fid, name):
    ST['fixtures'].append([kind, fid, name])
    save_state(ST)


def wait_exec(exec_id, timeout=300):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        if code == 200 and isinstance(ex, dict):
            last = ex
            if ex.get('status') in TERMINAL:
                return ex
        time.sleep(2)
    return last


def sha_text(s):
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------- children
def enroll_child(key, tag, host_key):
    name = P + key
    ip = P + key + '.invalid'
    code, enroll = hub.post('/api/nodes/enrollment',
                            {'node_name': name, 'node_ip': ip,
                             'hub_address': 'http://192.168.100.209:18080'})
    if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
        raise RuntimeError('enrollment_failed_http_%s_%s' % (code, enroll))
    ch = hostlib.OwnedChild(NONCE, host_key, tag, enroll['node_id'], enroll['agent_id'],
                            name, ip, hub.cred['registration_token'])
    started = ch.write_config_and_start('ws://192.168.100.209:18080')
    ST['children'][key] = {'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'host_key': host_key, 'tag': tag, 'name': name, 'ip': ip,
                           'root': ch.root, 'opt_root': ch.opt_root,
                           'processes': started.get('owned_processes')}
    save_state(ST)
    add_fixture('node', enroll['node_id'], name)
    return ch


def wait_node_online(node_id, timeout=150):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, n = hub.get('/api/nodes/' + node_id)
        if code == 200 and isinstance(n, dict):
            last = n
            if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                return n
        time.sleep(2)
    return last


def child_of(key):
    meta = ST['children'][key]
    return hostlib.OwnedChild(NONCE, meta['host_key'], meta['tag'], meta['node_id'],
                              meta['agent_id'], meta['name'], meta['ip'],
                              hub.cred['registration_token'])


def phase_children():
    out = {'at_utc': now_utc(), 'children': {}}
    plan = [('a', '-a', 'n1'), ('b', '-b', 'n1'), ('c', '-c', 'n3'), ('d', '-d', 'n1')]
    for key, tag, host_key in plan:
        if key in ST['children']:
            ch = child_of(key)
        else:
            ch = enroll_child(key, tag, host_key)
        node = wait_node_online(ST['children'][key]['node_id'])
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:%s:%s' % (key, node))
        if node.get('agent_version') != LABEL:
            raise RuntimeError('owned_child_wrong_build:%s:%s' % (key, node.get('agent_version')))
        out['children'][key] = {'node_id': ST['children'][key]['node_id'],
                                'host': ch.host['ip'], 'root': ch.root, 'opt_root': ch.opt_root,
                                'node': {k: node.get(k) for k in
                                         ('hostname', 'status', 'sync_status', 'agent_version',
                                          'deployment_mode', 'capabilities')}}
    ev_write('phase01-children.json', out)
    return out


# ---------------------------------------------------------------- app fixtures
UNIT_NAME = 'nodesteer-' + P + 'envapp.service'
OPT_ROOT = '/opt/' + P + 'envapp'
BINARY = OPT_ROOT + '/bin/app'
CONFIG = OPT_ROOT + '/config/app.env'
APP_NAME = P + 'envapp'


def app_script(version):
    body = ('#!/bin/sh\n'
            '# fixall fixture %s\n' % version +
            'if [ -n "$APP_ENV_OUT" ]; then\n'
            '  echo "mark=${APP_FIXALL_MARK}" > "$APP_ENV_OUT"\n'
            'fi\n'
            'if [ -n "$APP_READY_FLAG" ]; then echo ready > "$APP_READY_FLAG"; fi\n'
            'exec sleep 1000000\n')
    return body.encode()


def phase_artifacts():
    out = {'at_utc': now_utc(), 'artifacts': {}}
    contents = {'A1': app_script('v1'), 'A2': app_script('v2')}
    for label, content in contents.items():
        name = P + 'payload-' + label.lower()
        code, body = hub.upload_artifact(name, 'v1', name + '.bin', content)
        if code != 200:
            raise RuntimeError('artifact_upload_failed_%s_http_%s' % (label, code))
        local_sha = hashlib.sha256(content).hexdigest()
        if body.get('sha256') != local_sha:
            raise RuntimeError('artifact_sha_mismatch_%s_hub_%s_local_%s'
                               % (label, body.get('sha256'), local_sha))
        ST['ids']['artifact_' + label] = body['id']
        ST.setdefault('artifact_sha', {})[label] = local_sha
        add_fixture('artifact', body['id'], name)
        out['artifacts'][label] = {'id': body['id'], 'name': name,
                                   'local_sha256': hashlib.sha256(content).hexdigest(),
                                   'hub_sha256': body.get('sha256'), 'size': body.get('size')}
    save_state(ST)
    ev_write('phase02-artifacts.json', out)
    return out


def app_payload(version, artifact_id, config_text, health):
    return {'name': APP_NAME, 'description': 'fixall owned application',
            'artifact_id': artifact_id, 'version': version,
            'binary_path': BINARY, 'arguments': [], 'environment': {},
            'config': config_text, 'config_path': CONFIG, 'unit_name': UNIT_NAME,
            'health_check': health}


def phase_app_deploy():
    """Create the owned app, assign a+b, deploy v1, verify ENVFILE effect on the real agent."""
    out = {'at_utc': now_utc(), 'case': ['APPUI-20261016-ENVFILE-QUOTED-DASH']}
    child_a = child_of('a')
    node_a = ST['children']['a']['node_id']
    node_b = ST['children']['b']['node_id']
    mark = 'fixall-env-%s' % NONCE
    c1 = ('APP_FIXALL_MARK=%s\nAPP_ENV_OUT=%s\nAPP_READY_FLAG=%s\n'
          % (mark, child_a.root + '/env-out.txt', child_a.root + '/ready.flag'))
    out['config_v1'] = c1
    out['config_v1_sha256'] = hashlib.sha256(c1.encode()).hexdigest()
    payload = app_payload('1.0', ST['ids']['artifact_A1'], c1,
                          {'type': 'systemd', 'target': UNIT_NAME, 'timeout': 5,
                           'attempts': 3, 'interval': 2})
    app_id = ST['ids'].get('app')
    if app_id:
        code, existing = hub.get('/api/applications/' + app_id)
        if code != 200:
            app_id = None
    if not app_id:
        code, app = hub.post('/api/applications', payload)
        if code not in (200, 201):
            raise RuntimeError('app_create_failed_http_%s_%s' % (code, app))
        app_id = app['id']
        ST['ids']['app'] = app_id
        add_fixture('application', app_id, APP_NAME)
        out['app'] = {'id': app_id, 'http': code, 'version': app.get('version'),
                      'config_path': app.get('config_path'), 'unit_name': app.get('unit_name')}
    else:
        out['app'] = {'id': app_id, 'resumed': True}
    # assign a + b (7: assignment removal later)
    code, r = hub.post('/api/applications/%s/assign' % app_id, {'node_ids': [node_a, node_b]})
    out['assign'] = {'http': code, 'body': r}
    code, nodes = hub.get('/api/applications/%s/nodes' % app_id)
    out['assigned_after'] = nodes
    # deploy on node a
    code, execs = hub.post('/api/applications/%s/deploy' % app_id,
                           {'node_ids': [node_a], 'operation': 'deploy'})
    out['deploy_http'] = code
    if code != 200 or not execs:
        raise RuntimeError('deploy_failed_http_%s_%s' % (code, execs))
    ex = wait_exec(execs[0]['id'], timeout=300)
    out['deploy_execution'] = ex
    # runtime probe on the real agent host
    probe = child_a.remote_py(
        "import json,os,subprocess\n"
        "unit=%(unit)r; cfg=%(cfg)r; out={}\n"
        "up='/etc/systemd/system/'+unit\n"
        "out['unit_exists']=os.path.lexists(up)\n"
        "txt=open(up).read() if os.path.lexists(up) else ''\n"
        "out['unit_environmentfile_line']=[l for l in txt.splitlines() if l.startswith('EnvironmentFile=')]\n"
        "out['config_file_text']=open(cfg).read() if os.path.isfile(cfg) else None\n"
        "r=subprocess.run(['systemctl','show','-p','Environment','--value',unit],capture_output=True,text=True)\n"
        "out['systemctl_environment']=r.stdout.strip()\n"
        "mp=subprocess.run(['systemctl','show','-p','MainPID','--value',unit],capture_output=True,text=True).stdout.strip()\n"
        "out['main_pid']=mp\n"
        "proc={}\n"
        "if mp and mp!='0':\n"
        "    try:\n"
        "        for kv in open('/proc/'+mp+'/environ','rb').read().split(b'\\0'):\n"
        "            if b'=' in kv:\n"
        "                k,v=kv.split(b'=',1)\n"
        "                k=k.decode('utf-8','replace')\n"
        "                if k.startswith('APP_'):\n"
        "                    proc[k]=v.decode('utf-8','replace')\n"
        "    except Exception as e:\n"
        "        proc['_error']=type(e).__name__\n"
        "out['process_app_env']=proc\n"
        "env_out=%(env_out)r\n"
        "out['env_out_text']=open(env_out).read().strip() if os.path.isfile(env_out) else None\n"
        "print(json.dumps(out))\n"
        % {'unit': UNIT_NAME, 'cfg': CONFIG, 'env_out': child_a.root + '/env-out.txt'})
    out['runtime_probe'] = probe
    ef = (probe.get('unit_environmentfile_line') or [''])[0]
    out['systemd_semantics_note'] = (
        'systemd 的 `systemctl show -p Environment` 只暴露 Environment= 指令（unit 属性），'
        '不含 EnvironmentFile 加载的键；EnvironmentFile 键只出现在服务进程环境。'
        '本机 systemd 实测复现（2026-10-17，user manager）：Environment= 键出现在 show 输出，'
        'EnvironmentFile 键只出现在 /proc/PID/environ。故运行期生效以 /proc/<pid>/environ + '
        '进程实际行为（写出 env-out）为准。')
    out['verdict'] = {
        'unit_line_unquoted': ef == 'EnvironmentFile=-' + CONFIG,
        'proc_environ_has_mark': (probe.get('process_app_env') or {}).get('APP_FIXALL_MARK') == mark,
        'env_out_written_by_process': (probe.get('env_out_text') or '') == 'mark=' + mark,
        'config_file_matches': (probe.get('config_file_text') or '') == c1,
    }
    ok = (ex or {}).get('status') == 'SUCCESS' and all(out['verdict'].values())
    record('APPUI-20261016-ENVFILE-QUOTED-DASH', 'app', 'FIXED' if ok else 'STILL_BROKEN',
           'owned Application 配置内容运行期生效（unit 行/systemctl/进程 environ/进程实际写出）' if ok
           else '配置内容未完全生效：%s' % out['verdict'],
           ['evidence/phase03-envfile.json'], out)
    ev_write('phase03-envfile.json', out)
    dump_results()
    return out


def phase_envfile_extra():
    """ENVFILE 补充：Environment= 指令键（systemctl show Environment 可见）与
    EnvironmentFile 键（进程环境可见）在同一 Unit 内同时生效。"""
    out = {'at_utc': now_utc(), 'case': ['APPUI-20261016-ENVFILE-QUOTED-DASH-EXTRA']}
    app_id = ST['ids']['app']
    child_a = child_of('a')
    node_a = ST['children']['a']['node_id']
    mark = 'fixall-env-%s' % NONCE
    def_mark = 'fixall-def-%s' % NONCE
    c1 = ('APP_FIXALL_MARK=%s\nAPP_ENV_OUT=%s\nAPP_READY_FLAG=%s\n'
          % (mark, child_a.root + '/env-out.txt', child_a.root + '/ready.flag'))
    payload = app_payload('1.0', ST['ids']['artifact_A1'], c1,
                          {'type': 'systemd', 'target': UNIT_NAME, 'timeout': 5,
                           'attempts': 3, 'interval': 2})
    payload['environment'] = {'APP_DEF_MARK': def_mark}
    code, app = hub.put('/api/applications/%s' % app_id, payload)
    out['update_http'] = code
    code, execs = hub.post('/api/applications/%s/deploy' % app_id,
                           {'node_ids': [node_a], 'operation': 'deploy'})
    out['deploy_http'] = code
    ex = wait_exec(execs[0]['id'], timeout=300) if code == 200 and execs else None
    out['deploy_execution'] = {'status': (ex or {}).get('status')}
    time.sleep(2)
    probe = child_a.remote_py(
        "import json,os,subprocess\n"
        "unit=%(unit)r; out={}\n"
        "txt=open('/etc/systemd/system/'+unit).read() if os.path.lexists('/etc/systemd/system/'+unit) else ''\n"
        "out['unit_lines']=[l for l in txt.splitlines() if l.startswith(('Environment=','EnvironmentFile='))]\n"
        "r=subprocess.run(['systemctl','show','-p','Environment','--value',unit],capture_output=True,text=True)\n"
        "out['systemctl_environment']=r.stdout.strip()\n"
        "mp=subprocess.run(['systemctl','show','-p','MainPID','--value',unit],capture_output=True,text=True).stdout.strip()\n"
        "proc={}\n"
        "if mp and mp!='0':\n"
        "    for kv in open('/proc/'+mp+'/environ','rb').read().split(b'\\0'):\n"
        "        if b'=' in kv:\n"
        "            k,v=kv.split(b'=',1); k=k.decode('utf-8','replace')\n"
        "            if k.startswith('APP_'): proc[k]=v.decode('utf-8','replace')\n"
        "out['process_app_env']=proc\n"
        "print(json.dumps(out))\n" % {'unit': UNIT_NAME})
    out['probe'] = probe
    out['verdict'] = {
        'systemctl_shows_definition_env': ('APP_DEF_MARK=' + def_mark) in (probe.get('systemctl_environment') or ''),
        'proc_has_definition_env': (probe.get('process_app_env') or {}).get('APP_DEF_MARK') == def_mark,
        'proc_has_file_env': (probe.get('process_app_env') or {}).get('APP_FIXALL_MARK') == mark,
        'unit_has_both_lines': any(l.startswith('Environment=') and 'APP_DEF_MARK=' in l
                                   for l in probe.get('unit_lines') or [])
                               and any(l.startswith('EnvironmentFile=-') for l in probe.get('unit_lines') or []),
    }
    ok = all(out['verdict'].values())
    record('APPUI-20261016-ENVFILE-QUOTED-DASH-EXTRA', 'app', 'FIXED' if ok else 'STILL_BROKEN',
           '同一 Unit：Environment= 键出现在 systemctl show Environment；EnvironmentFile 键出现在进程环境'
           if ok else '偏差：%s' % out['verdict'],
           ['evidence/phase03b-envfile-extra.json'], out)
    ev_write('phase03b-envfile-extra.json', out)
    dump_results()
    return out


def phase_remove_assignment():
    """7: remove one node assignment through the product path."""
    out = {'at_utc': now_utc(), 'case': ['DESTRUCTIVE-REMOVE-ASSIGNMENT']}
    app_id = ST['ids']['app']
    node_b = ST['children']['b']['node_id']
    code, before = hub.get('/api/applications/%s/nodes' % app_id)
    out['assigned_before'] = before
    code, r = hub.post('/api/applications/%s/assign' % app_id,
                       {'node_ids': [node_b], 'remove': True})
    out['remove_http'] = code
    out['remove_body'] = r
    code, after = hub.get('/api/applications/%s/nodes' % app_id)
    out['assigned_after'] = after
    ok = code == 200 and node_b in (before or []) and node_b not in (after or [])
    record('DESTRUCTIVE-REMOVE-ASSIGNMENT', 'destructive', 'PASS' if ok else 'FAIL',
           '应用节点分配移除生效：%s -> %s' % (before, after), ['evidence/phase04-remove-assign.json'], out)
    ev_write('phase04-remove-assign.json', out)
    dump_results()
    return out


def phase_rollback():
    """APPUI-20261016-ROLLBACK-FALSE-FAILURE: negative tcp health on v2 -> rollback succeeds."""
    out = {'at_utc': now_utc(), 'case': ['APPUI-20261016-ROLLBACK-FALSE-FAILURE']}
    app_id = ST['ids']['app']
    child_a = child_of('a')
    node_a = ST['children']['a']['node_id']
    # a really-closed port on the agent host
    closed = child_a.remote_py(
        "import json,socket\n"
        "s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close()\n"
        "print(json.dumps({'port':p}))\n")['port']
    c2 = ('APP_FIXALL_MARK=fixall-v2-%s\nAPP_ENV_OUT=%s\nAPP_READY_FLAG=%s\n'
          % (NONCE, child_a.root + '/env-out.txt', child_a.root + '/ready.flag'))
    out['closed_port'] = closed
    out['config_v2'] = c2
    payload = app_payload('2.0', ST['ids']['artifact_A2'], c2,
                          {'type': 'tcp', 'target': '127.0.0.1:%d' % closed, 'timeout': 2,
                           'attempts': 2, 'interval': 1})
    code, app = hub.put('/api/applications/%s' % app_id, payload)
    out['update_http'] = code
    out['updated_version'] = (app or {}).get('version')
    code, execs = hub.post('/api/applications/%s/deploy' % app_id,
                           {'node_ids': [node_a], 'operation': 'deploy'})
    out['deploy_http'] = code
    if code != 200 or not execs:
        raise RuntimeError('rollback_deploy_failed_http_%s_%s' % (code, execs))
    ex = wait_exec(execs[0]['id'], timeout=300)
    out['deploy_execution'] = ex
    time.sleep(3)
    probe = child_a.remote_py(
        "import json,os,subprocess,sqlite3,hashlib\n"
        "unit=%(unit)r; cfg=%(cfg)r; bp=%(bp)r; data=%(data)r\n"
        "out={}\n"
        "out['binary_sha256']=hashlib.sha256(open(bp,'rb').read()).hexdigest() if os.path.isfile(bp) else None\n"
        "out['config_text']=open(cfg).read() if os.path.isfile(cfg) else None\n"
        "r=subprocess.run(['systemctl','show','--no-pager','--property=ActiveState,SubState,MainPID,UnitFileState',unit],capture_output=True,text=True)\n"
        "out['unit_state']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
        "txt=open('/etc/systemd/system/'+unit).read() if os.path.lexists('/etc/systemd/system/'+unit) else ''\n"
        "out['unit_environmentfile_line']=[l for l in txt.splitlines() if l.startswith('EnvironmentFile=')]\n"
        "c=sqlite3.connect('file:'+os.path.join(data,'state.db')+'?mode=ro',uri=True)\n"
        "c.execute('PRAGMA query_only=ON')\n"
        "out['deployments']=[{'id':r0[0],'to_version':r0[1],'phase':r0[2],'completed_kind':r0[3],'updated_at':r0[4]} for r0 in c.execute('SELECT id,to_version,phase,completed_kind,updated_at FROM deployments ORDER BY started_at DESC LIMIT 8')]\n"
        "print(json.dumps(out))\n"
        % {'unit': UNIT_NAME, 'cfg': CONFIG, 'bp': BINARY, 'data': child_a.data})
    out['host_after_rollback'] = probe
    deps = probe.get('deployments') or []
    kinds = [d.get('completed_kind') for d in deps]
    failure_text = ' '.join(str((ex or {}).get(k) or '') for k in ('error', 'block_reason', 'stderr'))
    out['failure_text'] = failure_text
    out['verdict'] = {
        'execution_failed': (ex or {}).get('status') == 'FAILED',
        'failure_reason_is_health_check': 'health check failed' in failure_text,
        'failure_text_no_rollback_failed': 'rollback failed' not in failure_text,
        'binary_restored_v1': probe.get('binary_sha256') == ST['artifact_sha']['A1'],
        'config_restored_v1': 'fixall-v2' not in (probe.get('config_text') or '')
                              and 'fixall-env-%s' % NONCE in (probe.get('config_text') or ''),
        'unit_active': (probe.get('unit_state') or {}).get('ActiveState') == 'active',
        'no_rollback_failed_rows': 'ROLLBACK_FAILED' not in [d.get('phase') for d in deps],
        'rolled_back_kind_recorded': 'rolled_back' in kinds,
    }
    ok = all(out['verdict'].values())
    record('APPUI-20261016-ROLLBACK-FALSE-FAILURE', 'app', 'FIXED' if ok else 'STILL_BROKEN',
           'v2 负向健康检查失败后回滚：文件还原 v1、服务存活、journal DONE/rolled_back、无假 ROLLBACK_FAILED'
           if ok else '仍有偏差：%s' % out['verdict'],
           ['evidence/phase05-rollback.json'], out)
    ev_write('phase05-rollback.json', out)
    dump_results()
    return out


def phase_misc_api():
    out = {'at_utc': now_utc(), 'cases': {}}
    # --- assign/deploy 404 ---
    missing = '00000000-0000-4000-8000-0000000000ff'
    c1, h1, b1 = hub.raw_text('POST', '/api/applications/%s/assign' % missing, {'node_ids': []})
    c2, h2, b2 = hub.raw_text('POST', '/api/applications/%s/deploy' % missing,
                              {'node_ids': [ST['children']['a']['node_id']], 'operation': 'deploy'})
    leak = any(tok in (b1 + b2) for tok in ('sql:', 'no rows', 'sqlite', 'SELECT '))
    ok404 = c1 == 404 and c2 == 404 and '"application not found"' in b1 and '"application not found"' in b2 and not leak
    out['cases']['ASSIGN404'] = {'assign': {'http': c1, 'body': b1[:300]},
                                 'deploy': {'http': c2, 'body': b2[:300]}, 'sql_leak': leak}
    record('E2E-20261016-ASSIGN-MISSING-APP-500-SQL-LEAK', 'api', 'FIXED' if ok404 else 'STILL_BROKEN',
           '不存在应用 assign/deploy -> 404 application not found 且无 SQL 原文' if ok404
           else '仍复现：%s' % out['cases']['ASSIGN404'],
           ['evidence/phase06-misc-api.json'])
    # deploy with existing app but missing node -> 400 business text
    app_id = ST['ids']['app']
    c3, h3, b3 = hub.raw_text('POST', '/api/applications/%s/deploy' % app_id,
                              {'node_ids': ['missing-node-xyz'], 'operation': 'deploy'})
    out['cases']['DEPLOY_MISSING_NODE'] = {'http': c3, 'body': b3[:300]}
    # --- parameter type ---
    sc = {'name': P + 'script', 'description': 'fixall owned script',
          'interpreter': 'shell', 'content': 'echo fixall\n' * 30,
          'parameters': [{'name': 'p1', 'type': 'string', 'required': False, 'default': ''}],
          'environment': {}, 'working_dir': ''}
    code, script = hub.post('/api/scripts', sc)
    if code not in (200, 201):
        raise RuntimeError('script_create_failed_%s_%s' % (code, script))
    sid = script['id']
    ST['ids']['script'] = sid
    add_fixture('script', sid, sc['name'])
    bad = dict(sc)
    bad['parameters'] = [{'name': 'p1', 'type': 'not-a-type', 'required': False, 'default': ''}]
    c4, h4, b4 = hub.raw_text('PUT', '/api/scripts/%s' % sid, bad)
    c5, h5, b5 = hub.raw_text('POST', '/api/scripts', dict(bad, name=P + 'script-bad'))
    code2, after = hub.get('/api/scripts/%s' % sid)
    unchanged = (after.get('parameters') or [{}])[0].get('type') == 'string'
    okparam = c4 == 400 and c5 == 400 and '"invalid parameter type' in b4 and unchanged
    out['cases']['PARAM_TYPE'] = {'put_http': c4, 'put_body': b4[:200], 'post_http': c5,
                                  'post_body': b5[:200], 'definition_unchanged': unchanged}
    record('OBS-20261017-SCRIPT-PUT-TYPE-UNVALIDATED', 'api', 'FIXED' if okparam else 'STILL_BROKEN',
           'PUT/POST 非法参数类型 -> 400 invalid parameter type，定义未被改写' if okparam
           else '仍复现：%s' % out['cases']['PARAM_TYPE'],
           ['evidence/phase06-misc-api.json'])
    # --- whitespace username ---
    c6, h6, b6 = hub.raw_text('POST', '/api/users',
                              {'username': '   ', 'password': 'fixall-pass-123', 'role': 'viewer'})
    c7, h7, b7 = hub.raw_text('POST', '/api/login',
                              {'username': '   ', 'password': 'fixall-pass-123'})
    okuser = c6 == 400 and c7 == 401
    out['cases']['USER_WS'] = {'create_http': c6, 'create_body': b6[:200],
                               'login_http': c7, 'login_body': b7[:200]}
    record('OBS-20261016-G1-USER-INPUT-NOT-TRIMMED', 'api', 'FIXED' if okuser else 'STILL_BROKEN',
           '纯空白用户名创建 400 / 登录 401' if okuser else '仍复现：%s' % out['cases']['USER_WS'],
           ['evidence/phase06-misc-api.json'])
    ev_write('phase06-misc-api.json', out)
    dump_results()
    return out


def phase_exit_code():
    out = {'at_utc': now_utc(), 'case': ['OBS-20261008-EXECUTION-ZERO-EXIT-CODE']}
    node_b = ST['children']['b']['node_id']
    long_name = P + 'exit0-' + ('超长任务名称-' * 12) + 'long-task-name-tail'
    task = {'name': long_name, 'type': 'command', 'command': 'echo fixall-exit-0',
            'target': {'type': 'node', 'node_ids': [node_b]},
            'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, t = hub.post('/api/tasks', task)
    if code not in (200, 201):
        raise RuntimeError('task_create_failed_%s_%s' % (code, t))
    ST['ids']['task'] = t['id']
    add_fixture('task', t['id'], long_name)
    out['task'] = {'id': t['id'], 'name': long_name}
    code, run = hub.post('/api/tasks/%s/run' % t['id'], {})
    out['run_http'] = code
    out['run_body'] = run if not isinstance(run, dict) or 'log' not in run else {k: v for k, v in run.items() if k != 'log'}
    exec_id = None
    if isinstance(run, list) and run:
        exec_id = run[0].get('id')
    elif isinstance(run, dict):
        exec_id = run.get('id') or run.get('execution_id')
        if not exec_id and isinstance(run.get('execution'), dict):
            exec_id = run['execution'].get('id')
    if not exec_id:
        # fall back: list executions for the task
        code, lst = hub.get('/api/executions?task_id=' + t['id'])
        if code == 200 and isinstance(lst, list) and lst:
            exec_id = lst[0].get('id')
    out['execution_id'] = exec_id
    ex = wait_exec(exec_id, timeout=180) if exec_id else None
    out['execution'] = ex
    code, listed = hub.get('/api/executions')
    listed_row = None
    if code == 200 and isinstance(listed, list):
        for row in listed:
            if row.get('id') == exec_id:
                listed_row = row
                break
    out['list_row'] = listed_row
    present = isinstance(ex, dict) and 'exit_code' in ex
    value = (ex or {}).get('exit_code') if present else None
    ok = (ex or {}).get('status') == 'SUCCESS' and present and value == 0
    record('OBS-20261008-EXECUTION-ZERO-EXIT-CODE', 'api', 'FIXED' if ok else 'STILL_BROKEN',
           'SUCCESS 执行 exit_code=0 且字段存在（列表同规则）' if ok
           else '仍复现：status=%s present=%s value=%s' % ((ex or {}).get('status'), present, value),
           ['evidence/phase07-exitcode.json'], out)
    ev_write('phase07-exitcode.json', out)
    dump_results()
    return out


def phase_transfers():
    run = ST.get('transfers_run', 1)
    ST['transfers_run'] = run + 1
    tag = '' if run == 1 else '-run%d' % run
    out = {'at_utc': now_utc(), 'run': run, 'case': ['E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING', 'DESTRUCTIVE-CANCEL-TRANSFER']}
    child_b, child_c = child_of('b'), child_of('c')
    node_b, node_c = ST['children']['b']['node_id'], ST['children']['c']['node_id']
    # owned source file on b's host
    src = child_b.root + '/share/transfer-src.txt'
    content = 'fixall-transfer-%s\n' % NONCE
    child_b.remote_py(
        "import json,os\n"
        "p=%(p)r\n"
        "os.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)\n"
        "open(p,'w').write(%(c)r)\n"
        "print(json.dumps({'written':p}))\n" % {'p': src, 'c': content})
    dst = child_c.root + '/incoming/transfer-dst.txt'
    # --- happy path b -> c
    code, tr = hub.post('/api/transfers', {'source_node_id': node_b, 'source_path': src,
                                           'targets': [{'node_id': node_c, 'destination_path': dst}]})
    out['create_happy_http'] = code
    if code != 200:
        raise RuntimeError('transfer_create_failed_%s_%s' % (code, tr))
    tr_id = tr['id']
    ST['ids']['transfer_happy' + tag] = tr_id
    add_fixture('transfer', tr_id, 'happy b->c' + tag)
    deadline = time.time() + 180
    final = None
    while time.time() < deadline:
        c, final = hub.get('/api/transfers/' + tr_id)
        if final.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
            break
        time.sleep(2)
    out['happy_final'] = {k: final.get(k) for k in ('id', 'status', 'error', 'sha256', 'size')}
    out['happy_targets'] = final.get('targets')
    got = child_c.remote_py(
        "import json,os\n"
        "p=%(p)r\n"
        "print(json.dumps({'exists':os.path.isfile(p),'text':open(p).read() if os.path.isfile(p) else None}))\n"
        % {'p': dst})
    out['dest_file'] = got
    happy_ok = final.get('status') == 'SUCCESS' and got.get('text') == content
    # --- failure path: kill c, create transfer, expect PENDING (no silent DELIVERING), 409, then recovery
    kill = child_c.remote_py(
        "import json,os,signal,subprocess,time\n"
        "cfg=%(cfg)r\n"
        "p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
        "pids=[int(x) for x in p.stdout.split() if x.isdigit()]\n"
        "for pid in pids:\n"
        "    os.kill(pid, signal.SIGKILL)\n"
        "time.sleep(1)\n"
        "q=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
        "print(json.dumps({'killed':pids,'remaining':q.stdout.split()}))\n"
        % {'cfg': child_c.config})
    out['kill_c'] = kill
    deadline = time.time() + 60
    while time.time() < deadline:
        c, n = hub.get('/api/nodes/' + node_c)
        if n.get('status') != 'online':
            break
        time.sleep(2)
    out['c_status_after_kill'] = n.get('status')
    src2 = child_b.root + '/share/transfer-fail.txt'
    child_b.remote_py(
        "import json,os\n"
        "p=%(p)r\n"
        "open(p,'w').write('fail-path-%(n)s\\n')\n"
        "print(json.dumps({'written':p}))\n" % {'p': src2, 'n': NONCE})
    dst2 = child_c.root + '/incoming/fail-dst.txt'
    code, tr2 = hub.post('/api/transfers', {'source_node_id': node_b, 'source_path': src2,
                                            'targets': [{'node_id': node_c, 'destination_path': dst2}]})
    out['create_fail_http'] = code
    tr2_id = tr2['id']
    ST['ids']['transfer_fail' + tag] = tr2_id
    add_fixture('transfer', tr2_id, 'fail path b->c (c down)' + tag)
    # wait for source upload to finish and dispatch to find c unavailable
    deadline = time.time() + 120
    final2 = None
    while time.time() < deadline:
        c, final2 = hub.get('/api/transfers/' + tr2_id)
        tgt = (final2.get('targets') or [{}])[0]
        if final2.get('sha256') and tgt.get('status') in ('PENDING', 'DELIVERING', 'FAILED', 'SUCCESS'):
            break
        time.sleep(2)
    tgt2 = (final2.get('targets') or [{}])[0]
    out['fail_state'] = {'status': final2.get('status'), 'target': {k: tgt2.get(k) for k in
                         ('node_id', 'status', 'error')}, 'sha256_present': bool(final2.get('sha256'))}
    # node delete must be blocked while non-terminal (expected 409)
    c409, h409, b409 = hub.raw_text('DELETE', '/api/nodes/' + node_c)
    out['delete_c_while_active'] = {'http': c409, 'body': b409[:200]}
    # --- cancel a second transfer while c is down (destructive 6)
    code, tr3 = hub.post('/api/transfers', {'source_node_id': node_b, 'source_path': src2,
                                            'targets': [{'node_id': node_c, 'destination_path': dst2 + '.2'}]})
    out['create_cancel_http'] = code
    tr3_id = tr3['id']
    ST['ids']['transfer_cancel' + tag] = tr3_id
    add_fixture('transfer', tr3_id, 'cancel b->c (c down)' + tag)
    ccan, can = hub.post('/api/transfers/%s/cancel' % tr3_id, {})
    out['cancel_http'] = ccan
    out['cancel_body'] = {k: can.get(k) for k in ('id', 'status', 'error')} if isinstance(can, dict) else can
    out['cancel_targets'] = can.get('targets') if isinstance(can, dict) else None
    # --- restart c -> recovery via ResumeNode
    restart = child_c.start()
    out['restart_c'] = restart
    node_c_after = wait_node_online(node_c, timeout=150)
    out['c_status_after_restart'] = node_c_after.get('status') if node_c_after else None
    deadline = time.time() + 180
    final2b = None
    while time.time() < deadline:
        c, final2b = hub.get('/api/transfers/' + tr2_id)
        if final2b.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
            break
        time.sleep(2)
    out['fail_final'] = {k: final2b.get(k) for k in ('id', 'status', 'error')}
    out['fail_targets_final'] = final2b.get('targets')
    got2 = child_c.remote_py(
        "import json,os\n"
        "p=%(p)r\n"
        "print(json.dumps({'exists':os.path.isfile(p),'text':open(p).read() if os.path.isfile(p) else None}))\n"
        % {'p': dst2})
    out['dest2_file'] = got2
    fail_ok = (out['fail_state']['target']['status'] in ('PENDING',)
               and out['delete_c_while_active']['http'] == 409
               and final2b.get('status') == 'SUCCESS' and got2.get('exists'))
    out['verdict'] = {
        'happy_path': happy_ok,
        'channel_down_target_not_silently_delivering': out['fail_state']['target']['status'] == 'PENDING',
        'non_terminal_blocks_delete_409': out['delete_c_while_active']['http'] == 409,
        'reconnect_resume_completes': final2b.get('status') == 'SUCCESS' and bool(got2.get('exists')),
        'cancel_terminal': (out['cancel_body'] or {}).get('status') == 'CANCELED',
    }
    ok = all(out['verdict'].values())
    record('E2E-20261017-XFER-REMOTE-TARGET-STUCK-DELIVERING', 'xfer',
           'FIXED' if ok else 'PARTIAL/STILL_BROKEN',
           '通道不可用时目标保持可恢复 PENDING（不再静默 DELIVERING 悬挂）；重连后 ResumeNode 完成；'
           '非终态 409、终态释放；取消传输进入 CANCELED' if ok else '偏差：%s' % out['verdict'],
           ['evidence/phase08-transfers%s.json' % tag], out)
    ev_write('phase08-transfers%s.json' % tag, out)
    dump_results()
    return out


def phase_schedule_group():
    out = {'at_utc': now_utc()}
    task_id = ST['ids']['task']
    sid = ST['ids'].get('schedule')
    if sid:
        code, _ = hub.get('/api/schedules/' + sid)
        if code != 200:
            sid = None
    if not sid:
        sched = {'name': P + 'sched', 'task_id': task_id, 'type': 'cron',
                 'expression': '0 3 * * *', 'timezone': 'Asia/Shanghai', 'enabled': True,
                 'execution_owner': 'agent', 'offline_policy': 'hub_online_required',
                 'misfire_policy': 'skip'}
        code, s = hub.post('/api/schedules', sched)
        if code not in (200, 201):
            raise RuntimeError('schedule_create_failed_%s_%s' % (code, s))
        sid = s['id']
        ST['ids']['schedule'] = sid
        add_fixture('schedule', sid, sched['name'])
        out['schedule'] = {'id': sid, 'http': code}
    else:
        out['schedule'] = {'id': sid, 'resumed': True}
    gid = ST['ids'].get('group')
    if gid:
        code, _ = hub.get('/api/groups/' + gid)
        if code != 200:
            gid = None
    if not gid:
        grp = {'name': P + 'group', 'description': 'fixall owned group', 'type': 'static',
               'members': [ST['children']['a']['node_id']]}
        code, g = hub.post('/api/groups', grp)
        if code not in (200, 201):
            raise RuntimeError('group_create_failed_%s_%s' % (code, g))
        gid = g['id']
        ST['ids']['group'] = gid
        add_fixture('group', gid, grp['name'])
        out['group'] = {'id': gid, 'http': code}
    else:
        out['group'] = {'id': gid, 'resumed': True}
    ev_write('phase09-schedule-group.json', out)
    save_state(ST)
    return out


def main():
    hub.login()
    phase = sys.argv[1] if len(sys.argv) > 1 else 'all'
    phases = {'children': phase_children, 'artifacts': phase_artifacts,
              'app': phase_app_deploy, 'envfile_extra': phase_envfile_extra,
              'remove': phase_remove_assignment,
              'rollback': phase_rollback, 'misc': phase_misc_api,
              'exitcode': phase_exit_code, 'transfers': phase_transfers,
              'schedgroup': phase_schedule_group}
    order = ['children', 'artifacts', 'app', 'envfile_extra', 'remove', 'rollback', 'misc',
             'exitcode', 'transfers', 'schedgroup']
    if phase == 'all':
        for name in order:
            print('==== phase', name, flush=True)
            phases[name]()
    else:
        phases[phase]()
    dump_results()


if __name__ == '__main__':
    main()
