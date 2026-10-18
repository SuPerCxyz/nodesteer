#!/usr/bin/env python3
"""Web batch08 driver: repair authority :18080 fixtures + native-4 owned child.

Scope (batch guard):
  * owned child node <=1 (enroll / stop / delete record / remove dir on native-4)
  * owned Script/Task <=2 each, owned Artifact <=2, owned Application <=1
  * never touch protected node state, Hub, protected Agents, product source

Endpoint is hard-guarded to repair :18080. Credentials are read from the private
0600 file and never printed or persisted.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EV = BATCH / 'evidence'
NONCE = (BATCH / '.nonce').read_text().strip()
PREFIX = 'qa-w1410b08-%s' % NONCE
BASE = 'http://192.168.100.209:18080'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
STATE_PATH = BATCH / 'state.json'

NODE_NATIVE4 = '03cb50cc-eab6-4afd-bbe0-f73c4e326773'
AGENT_NATIVE4 = 'agent-bbd350f8-69f9-4406-923c-4f9c335e6962'
NATIVE4_IP = '192.168.100.247'
OPERATOR_IP = '192.168.100.170'
NODE_OFFLINE_LEFTOVER = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
SHARED_ARTIFACT = '7b8929ed-c880-46f1-90e6-a5e9c0dab096'
SHARED_APP = '804e6b14-eb84-4dda-90de-8bcab9586c4c'

AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
NATIVE4_UNIT = 'nodesteer-agent-repair-20260930-101945-native-4.service'
NATIVE4_CONFIG = '/etc/nodesteer/repair-20260930-101945/native-4.yaml'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-o', 'ConnectTimeout=10', 'root@' + NATIVE4_IP]

os.umask(0o077)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def write_json(name, value, mode=0o644):
    path = BATCH / name
    tmp = BATCH / (name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return str(path.relative_to(BATCH.parent.parent.parent.parent))


def rec(name, value):
    """Write evidence file under evidence/ (name without prefix)."""
    path = EV / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return 'evidence/' + name


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(state):
    tmp = STATE_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


# --------------------------------------------------------------------------- API
class Hub:
    def __init__(self):
        st = CRED.lstat()
        if CRED.is_symlink() or not CRED.is_file() or (st.st_mode & 0o777) != 0o600:
            raise RuntimeError('credential_file_gate_failed')
        split = urllib.parse.urlsplit(BASE)
        if split.port != 18080:
            raise RuntimeError('endpoint_guard_failed')
        self.cred = json.loads(CRED.read_text())
        self.token = None
        self.calls = 0

    def login(self):
        code, body = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'], 'password': self.cred['admin_password']})
        if code != 200 or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, timeout=30, retries=3):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        last = None
        for _ in range(retries):
            req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, _decode(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, _decode(exc.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def get(self, path): return self.raw('GET', path)
    def post(self, path, body=None): return self.raw('POST', path, body if body is not None else {})
    def put(self, path, body): return self.raw('PUT', path, body)
    def delete(self, path): return self.raw('DELETE', path)

    def json(self, path):
        code, body = self.get(path)
        if code != 200:
            raise RuntimeError('get_%s_http_%s' % (path, code))
        return body

    def quote(self, value):
        return urllib.parse.quote(str(value), safe='')

    def upload_artifact(self, name, version, filename, content):
        boundary = '----b08' + os.urandom(12).hex()
        parts = []
        for field, value in (('name', name), ('version', version), ('architecture', 'amd64')):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, field, value)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode())
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode())
        data = b''.join(parts)
        headers = {'Accept': 'application/json',
                   'Content-Type': 'multipart/form-data; boundary=' + boundary,
                   'Content-Length': str(len(data))}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(BASE + '/api/artifacts', data=data, headers=headers, method='POST')
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:400].decode('utf-8', 'replace')}


# ----------------------------------------------------------------- node side
def ssh_py(script, timeout=90):
    cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError('ssh_py_failed: %s' % (cp.stderr[-400:] or cp.stdout[-400:]))
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError('ssh_py_empty')
    return json.loads(lines[-1])


class Child:
    """Owned child Agent (deployment_mode=docker) on native-4: cap-missing target."""

    def __init__(self):
        self.root = '/var/lib/nodesteer-qa-w1410b08-%s' % NONCE
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'

    def inventory(self):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['owned_child_processes']=[l.split('--config')[0].strip() for l in lines if %r in l]\n"
            "out['owned_process_count']=len(out['owned_child_processes'])\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],"
            "capture_output=True,text=True)\n"
            "out['protected_native4_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "b=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',"
            "'nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in b.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.config, AGENT_BIN, NATIVE4_UNIT)
        return ssh_py(script)

    def write_config_and_start(self, node_id, agent_id, node_name, token, hub_url, mode):
        yaml_text = (
            'hub_url: "%s"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 2\n'
            'revision_check_sec: 2\n'
            'max_log_bytes: 262144\n'
        ) % (hub_url, token, agent_id, node_name, NATIVE4_IP, mode, self.data)
        prep = (
            "import os\n"
            "root=%r; cfg=%r; data=%r; log=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "open(log,'a').close(); os.chmod(log,0o600)\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.root, self.config, self.data, self.log, yaml_text)
        cp = subprocess.run(SSH + ['python3', '-'], input=prep, capture_output=True,
                            text=True, timeout=60)
        if cp.returncode != 0 or 'CONFIG_WRITTEN' not in cp.stdout:
            raise RuntimeError('child_config_write_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
        return self.start()

    def start(self):
        script = (
            "import json,os,subprocess,time\n"
            "cfg=%r; log=%r; agent=%r\n"
            "env=dict(os.environ)\n"
            "for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'):\n"
            "    env.pop(k, None)\n"
            "env['NO_PROXY']='*'; env['no_proxy']='*'\n"
            "p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],\n"
            "                   stdout=open(log,'a'), stderr=subprocess.STDOUT,\n"
            "                   stdin=subprocess.DEVNULL, start_new_session=True, env=env)\n"
            "time.sleep(2.0)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l.split('--config')[0].strip() for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN)
        return ssh_py(script)

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
            "def m():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for t in p.stdout.split():\n"
            "        if not t.isdigit(): continue\n"
            "        pid=int(t)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "pids=m()\n"
            "for pid in pids: os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+10\n"
            "while time.time()<end and m(): time.sleep(0.4)\n"
            "left=m()\n"
            "for pid in left:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.8)\n"
            "print(json.dumps({'signalled':pids,'remaining':m()}))\n"
        ) % (self.config, AGENT_BIN)
        return ssh_py(script)

    def log_tail(self, max_bytes=4000):
        script = (
            "import json,os\n"
            "p=%r\n"
            "out={'size':0,'tail':''}\n"
            "if os.path.isfile(p):\n"
            "    sz=os.path.getsize(p); out['size']=sz\n"
            "    with open(p,'rb') as f:\n"
            "        if sz>%r: f.seek(sz-%r)\n"
            "        out['tail']=f.read().decode('utf-8','replace')\n"
            "print(json.dumps(out))\n"
        ) % (self.log, max_bytes, max_bytes)
        return ssh_py(script)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n"
            "def m():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for t in p.stdout.split():\n"
            "        if not t.isdigit(): continue\n"
            "        pid=int(t)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "pids=m()\n"
            "for pid in pids:\n"
            "    try: os.kill(pid, signal.SIGTERM)\n"
            "    except Exception: pass\n"
            "end=time.time()+10\n"
            "while time.time()<end and m(): time.sleep(0.4)\n"
            "left=m()\n"
            "for pid in left:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.6)\n"
            "existed=os.path.isdir(root)\n"
            "if existed: shutil.rmtree(root)\n"
            "mk='/tmp/%s-marker.txt'\n"
            "mkex=os.path.exists(mk)\n"
            "if mkex: os.unlink(mk)\n"
            "print(json.dumps({'agent_signalled':pids,'agent_remaining':m(),"
            "'root_removed':existed,'root_exists_after':os.path.exists(root),"
            "'marker_removed':mkex,'marker_exists_after':os.path.exists(mk)}))\n"
        ) % (self.root, self.config, AGENT_BIN, PREFIX)
        return ssh_py(script, timeout=120)

    def marker_state(self):
        script = (
            "import json,os\n"
            "mk='/tmp/%s-marker.txt'\n"
            "out={'path':mk,'exists':os.path.exists(mk)}\n"
            "if os.path.exists(mk): out['content']=open(mk).read().strip()[:200]\n"
            "print(json.dumps(out))\n"
        ) % (PREFIX,)
        return ssh_py(script)

    def probe(self, probe_src, fixture, phase, expected_rev, timeout=120):
        """Run the read-only agent state.db probe on native-4 as a real Task."""
        hub = Hub(); hub.login()
        p = probe_src
        env = {
            'phase': phase,
            'script_id': fixture['script_id'],
            'task_id': fixture['task_id'],
            'execution_id': fixture.get('execution_id', ''),
            'expected_hub_revision': str(expected_rev),
        }
        body = {
            'name': PREFIX + '-probe-' + phase,
            'type': 'script',
            'script_id': p['script_id'],
            'target': {'type': 'node', 'node_ids': [NODE_NATIVE4]},
            'enabled': True,
            'offline_policy': 'hub_online_required',
            'timeout': 90,
            'parameters': [
                {'name': k, 'type': 'string', 'default': v, 'required': False}
                for k, v in env.items()
            ],
        }
        code, task = hub.post('/api/tasks', body)
        if code != 200:
            raise RuntimeError('probe_task_create_http_%s %s' % (code, task))
        task_id = task['id']
        code, body_run = hub.post('/api/tasks/%s/run' % task_id, {'all': True})
        if code != 200:
            raise RuntimeError('probe_run_http_%s %s' % (code, body_run))
        ex_id = body_run[0]['id']
        result = wait_execution(hub, ex_id, timeout=timeout)
        hub.delete('/api/tasks/%s' % task_id)
        return {'probe_task_id': task_id, 'probe_execution_id': ex_id,
                'probe_execution': _trim_exec(result),
                'result': _probe_json(result)}


def _trim_exec(ex):
    return {k: ex.get(k) for k in ('id', 'task_id', 'script_id', 'node_id', 'status',
                                   'exit_code', 'trigger_type', 'start_time', 'end_time',
                                   'block_reason')}


def _probe_json(ex):
    out = (ex.get('stdout') or '').strip()
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith('{') and 'READONLY_PROBE' in line:
            try:
                return json.loads(line)
            except Exception:
                pass
    return {'parse': 'failed', 'stdout_tail': out[-600:], 'stderr_tail': (ex.get('stderr') or '')[-300:]}


def wait_execution(hub, exec_id, timeout=120, interval=1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/%s' % exec_id)
        if code == 200:
            last = ex
            if ex.get('status') in ('SUCCESS', 'FAILED', 'TIMED_OUT', 'CANCELED', 'SKIPPED', 'BLOCKED'):
                return ex
        time.sleep(interval)
    return last or {'id': exec_id, 'status': 'UNKNOWN_TIMEOUT'}


# --------------------------------------------------------------------- phases
def cmd_guard():
    hub = Hub(); hub.login()
    state = load_state()
    nodes = hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    out = {'at_utc': now_utc(), 'endpoint': BASE, 'nonce': NONCE, 'prefix': PREFIX,
           'authority_type': 'repair'}
    out['protected'] = {}
    for nid, label in PROTECTED_NODES.items():
        n = by_id.get(nid)
        if not n:
            raise RuntimeError('protected_node_missing:' + label)
        out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
                                   'deployment_mode': n['deployment_mode'],
                                   'agent_version': n['agent_version'],
                                   'global_revision': n.get('global_revision')}
    arts = hub.json('/api/artifacts')
    apps = hub.json('/api/applications')
    tasks = hub.json('/api/tasks')
    scripts = hub.json('/api/scripts')
    execs = hub.json('/api/executions')
    out['baseline_counts'] = {'nodes': len(nodes), 'artifacts': len(arts), 'applications': len(apps),
                              'tasks': len(tasks), 'scripts': len(scripts), 'executions': len(execs)}
    out['owned_fixtures_present'] = {
        'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
        'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
        'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
    }
    if any(out['owned_fixtures_present'].values()):
        raise RuntimeError('owned_fixture_already_present')
    out['shared_fixtures'] = {'artifact_present': any(a['id'] == SHARED_ARTIFACT for a in arts),
                              'app_present': any(a['id'] == SHARED_APP for a in apps)}
    out['offline_leftover_node'] = {k: by_id[NODE_OFFLINE_LEFTOVER].get(k)
                                    for k in ('id', 'hostname', 'status', 'sync_status')} \
        if NODE_OFFLINE_LEFTOVER in by_id else None
    inv = Child().inventory()
    out['native4_pre'] = {
        'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
        'owned_process_count': inv['owned_process_count'],
        'agent_binary_sha256': inv['agent_binary_sha256'],
        'agent_binary_sha_matches': inv['agent_binary_sha256'] == AGENT_SHA,
        'protected_native4_unit': inv['protected_native4_unit'],
        'primary_agent_unit': inv['primary_agent_unit'],
        'raw_secrets_emitted': False,
    }
    if inv['root_exists'] or inv['config_exists'] or inv['owned_process_count']:
        raise RuntimeError('owned_child_residue_present')
    if not out['native4_pre']['agent_binary_sha_matches']:
        raise RuntimeError('agent_binary_sha_mismatch')
    if inv['protected_native4_unit'].get('ActiveState') != 'active':
        raise RuntimeError('protected_native4_unit_not_active')
    out['marker_before'] = Child().marker_state()
    state['guard'] = out
    save_state(state)
    p = rec('phase0-guard.json', out)
    print(json.dumps({'ok': True, 'file': p, 'protected': out['protected'],
                      'baseline_counts': out['baseline_counts']}, ensure_ascii=False))
    return out


def cmd_child_up(mode='docker'):
    hub = Hub(); hub.login()
    state = load_state()
    code, enroll = hub.post('/api/nodes/enrollment', {
        'node_name': PREFIX + '-child', 'node_ip': NATIVE4_IP, 'hub_address': BASE})
    if code != 200 or not enroll.get('node_id'):
        raise RuntimeError('enrollment_failed_http_%s %s' % (code, enroll))
    node_id, agent_id = enroll['node_id'], enroll['agent_id']
    child = Child()
    started = child.write_config_and_start(node_id, agent_id, PREFIX + '-child',
                                           hub.cred['registration_token'],
                                           'ws://192.168.100.209:18080', mode)
    deadline = time.time() + 90
    node = None
    while time.time() < deadline:
        code, node = hub.get('/api/nodes/%s' % node_id)
        if code == 200 and node.get('status') == 'online':
            break
        time.sleep(2)
    out = {'at_utc': now_utc(), 'enrollment_http': code, 'node_id': node_id, 'agent_id': agent_id,
           'node_name': PREFIX + '-child', 'node_ip': NATIVE4_IP, 'requested_deployment_mode': mode,
           'child_root_on_native4': child.root, 'child_processes': started.get('owned_processes'),
           'node_after_start': {k: (node or {}).get(k) for k in
                                ('id', 'hostname', 'status', 'sync_status', 'deployment_mode',
                                 'agent_version', 'capabilities', 'global_revision', 'arch')},
           'registration_token_persisted_to_evidence': False}
    if not node or node.get('status') != 'online':
        out['child_log_tail'] = child.log_tail(2000)
        rec('phase1-owned-child-failed.json', out)
        raise RuntimeError('owned_child_did_not_reach_online')
    state['child'] = {'node_id': node_id, 'agent_id': agent_id, 'mode': mode, 'root': child.root}
    save_state(state)
    p = rec('phase1-owned-child.json', out)
    print(json.dumps({'ok': True, 'file': p, 'node_id': node_id,
                      'capabilities': out['node_after_start']['capabilities']}, ensure_ascii=False))
    return out


def cmd_child_stop():
    hub = Hub(); hub.login()
    state = load_state()
    node_id = state['child']['node_id']
    stopped = Child().stop()
    node = None
    deadline = time.time() + 40
    while time.time() < deadline:
        code, node = hub.get('/api/nodes/%s' % node_id)
        if code == 200 and node.get('status') != 'online':
            break
        time.sleep(2)
    out = {'at_utc': now_utc(), 'node_id': node_id, 'stop': stopped,
           'node_after_stop': {k: (node or {}).get(k) for k in ('status', 'sync_status')}}
    state['child_after_stop'] = out
    save_state(state)
    p = rec('phase2-child-stopped.json', out)
    print(json.dumps({'ok': True, 'file': p, 'status': out['node_after_stop'].get('status')}))
    return out


def cmd_child_force_online():
    """Set the owned child node record back to 'online' with no live session.

    This is the deterministic 'Agent unreachable' state: the Hub believes the
    node is online, the Agent session is gone -> ErrAgentOffline.
    """
    hub = Hub(); hub.login()
    state = load_state()
    node_id = state['child']['node_id']
    code, body = hub.post('/api/nodes/%s' % node_id, {'status': 'online'})
    code2, node = hub.get('/api/nodes/%s' % node_id)
    inv = Child().inventory()
    out = {'at_utc': now_utc(), 'node_id': node_id, 'set_online_http': code, 'body': body,
           'node_after': {k: (node or {}).get(k) for k in ('status', 'sync_status')},
           'owned_child_process_count': inv['owned_process_count'],
           'no_live_agent_session': inv['owned_process_count'] == 0}
    state['child_forced_online'] = out
    save_state(state)
    p = rec('phase3-child-forced-online.json', out)
    print(json.dumps({'ok': True, 'file': p, 'node_status': out['node_after'].get('status'),
                      'live_processes': inv['owned_process_count']}))
    return out


def cmd_child_rm():
    hub = Hub(); hub.login()
    state = load_state()
    node_id = state['child']['node_id']
    code, body = hub.delete('/api/nodes/%s' % node_id)
    code2, after = hub.get('/api/nodes/%s' % node_id)
    removed = Child().remove()
    out = {'at_utc': now_utc(), 'node_id': node_id, 'delete_http': code, 'delete_body': body,
           'get_after_http': code2, 'node_side': removed}
    state['child_removed'] = out
    save_state(state)
    p = rec('phase9-child-removed.json', out)
    print(json.dumps({'ok': True, 'file': p, 'delete_http': code, 'get_after_http': code2,
                      'node_side': removed}, ensure_ascii=False))
    return out


def cmd_child_reconfigure(mode):
    """Restart the SAME owned child node record in another deployment_mode.

    Used to reach the Hub's ErrAgentOffline branch on the application deploy
    path: the capability gate must pass (native reports application_deploy)
    while the Agent session is gone.
    """
    hub = Hub(); hub.login()
    state = load_state()
    node_id = state['child']['node_id']
    agent_id = state['child']['agent_id']
    child = Child()
    child.stop()
    time.sleep(1.0)
    started = child.write_config_and_start(node_id, agent_id, PREFIX + '-child',
                                           hub.cred['registration_token'],
                                           'ws://192.168.100.209:18080', mode)
    deadline = time.time() + 90
    node = None
    while time.time() < deadline:
        code, node = hub.get('/api/nodes/%s' % node_id)
        if code == 200 and node.get('status') == 'online' \
                and (node.get('capabilities') or {}).get('application_deploy'):
            break
        time.sleep(2)
    out = {'at_utc': now_utc(), 'node_id': node_id, 'mode': mode,
           'child_processes': started.get('owned_processes'),
           'node_after': {k: (node or {}).get(k) for k in
                          ('status', 'sync_status', 'deployment_mode', 'capabilities')}}
    if not node or node.get('status') != 'online':
        out['child_log_tail'] = child.log_tail(2000)
        rec('phase11-child-reconfigure-failed.json', out)
        raise RuntimeError('owned_child_reconfigure_failed')
    state['child']['mode'] = mode
    save_state(state)
    p = rec('phase11-child-reconfigure-%s.json' % mode, out)
    print(json.dumps({'ok': True, 'file': p, 'capabilities': out['node_after'].get('capabilities'),
                      'mode': out['node_after'].get('deployment_mode')}, ensure_ascii=False))
    return out


def cmd_fixtures(script_id=None):
    """Owned Script S + Task T (run target native-4) + Artifact + Application A.

    When script_id is supplied the business Script was already created through
    the real Web UI (script editor) and only the remaining fixtures are created.
    """
    hub = Hub(); hub.login()
    state = load_state()
    out = {'at_utc': now_utc(), 'prefix': PREFIX,
           'business_script_created_by': 'ui' if script_id else 'api'}

    # business script + task
    script_name = PREFIX + '-script'
    if script_id:
        code, script = hub.get('/api/scripts/%s' % script_id)
        if code != 200:
            raise RuntimeError('ui_script_lookup_http_%s' % code)
        script_name = script['name']
    else:
        content = ('#!/bin/sh\nset -e\necho %s-ok\necho %s > /tmp/%s-marker.txt\n'
                   % (PREFIX, PREFIX, PREFIX))
        code, script = hub.post('/api/scripts', {
            'name': script_name, 'description': 'web batch08 fixture', 'interpreter': 'shell',
            'content': content, 'timeout': 60, 'enabled': True})
        if code != 200:
            raise RuntimeError('script_create_http_%s %s' % (code, script))
    out['script'] = {'id': script['id'], 'name': script_name, 'revision': script.get('revision'),
                     'sha256': script.get('sha256')}
    task_name = PREFIX + '-task'
    code, task = hub.post('/api/tasks', {
        'name': task_name, 'type': 'script', 'script_id': script['id'],
        'target': {'type': 'node', 'node_ids': [NODE_NATIVE4]},
        'enabled': True, 'offline_policy': 'hub_online_required', 'timeout': 60})
    if code != 200:
        raise RuntimeError('task_create_http_%s %s' % (code, task))
    out['task'] = {'id': task['id'], 'name': task_name, 'revision': task.get('revision'),
                   'target': task.get('target')}

    # artifact + application
    payload = ('qa-w1410b08-%s-artifact-payload' % NONCE).encode()
    code, art = hub.upload_artifact(PREFIX + '-artifact', 'v1', PREFIX + '-artifact.bin', payload)
    if code != 200:
        raise RuntimeError('artifact_upload_http_%s %s' % (code, art))
    out['artifact'] = {'id': art['id'], 'name': PREFIX + '-artifact', 'version': art.get('version'),
                       'sha256': art.get('sha256'), 'size': art.get('size'),
                       'local_sha256_matches': art.get('sha256') == sha256_hex(payload)}
    code, app = hub.post('/api/applications', {
        'name': PREFIX + '-app', 'version': 'v1', 'description': 'web batch08 fixture',
        'artifact_id': art['id'], 'binary_path': '/tmp/' + PREFIX + '-binary',
        'config_path': '/tmp/' + PREFIX + '-config',
        'unit': PREFIX + '-unit', 'arguments': [], 'environment': {},
        'health_check': {'type': 'command', 'target': 'true', 'timeout': 3,
                         'attempts': 1, 'interval': 1}})
    if code != 200:
        raise RuntimeError('app_create_http_%s %s' % (code, app))
    out['application'] = {'id': app['id'], 'name': PREFIX + '-app', 'revision': app.get('revision'),
                          'artifact_id': app.get('artifact_id')}

    # probe script (readonly agent state.db reader)
    probe_src = (HERE / 'native4_probe.py').read_text()
    code, probe_script = hub.post('/api/scripts', {
        'name': PREFIX + '-probe', 'description': 'readonly agent state.db probe',
        'interpreter': 'python', 'content': probe_src, 'timeout': 90, 'enabled': True})
    if code != 200:
        raise RuntimeError('probe_script_create_http_%s %s' % (code, probe_script))
    out['probe_script'] = {'id': probe_script['id'], 'name': PREFIX + '-probe',
                           'revision': probe_script.get('revision')}

    state['fixtures'] = {
        'script_id': script['id'], 'task_id': task['id'], 'artifact_id': art['id'],
        'application_id': app['id'], 'probe_script_id': probe_script['id'],
    }
    save_state(state)
    p = rec('phase4-fixtures.json', out)
    print(json.dumps({'ok': True, 'file': p, **{k: v.get('id') for k, v in out.items()
                                                if isinstance(v, dict)}}, ensure_ascii=False))
    return out


def cmd_snapshot(name):
    hub = Hub(); hub.login()
    nodes = hub.json('/api/nodes')
    out = {'at_utc': now_utc(), 'name': name,
           'counts': {'nodes': len(nodes), 'artifacts': len(hub.json('/api/artifacts')),
                      'applications': len(hub.json('/api/applications')),
                      'tasks': len(hub.json('/api/tasks')),
                      'scripts': len(hub.json('/api/scripts')),
                      'schedules': len(hub.json('/api/schedules')),
                      'executions': len(hub.json('/api/executions'))},
           'nodes': {n['id'][:8]: n.get('status') for n in nodes}}
    p = rec(name, out)
    print(json.dumps({'ok': True, 'file': p, 'counts': out['counts']}, ensure_ascii=False))
    return out


def cmd_get(path):
    hub = Hub(); hub.login()
    code, body = hub.get(path)
    print(json.dumps({'path': path, 'http': code, 'body': body}, ensure_ascii=False)[:4000])
    return code


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if cmd == 'guard':
        cmd_guard()
    elif cmd == 'child-up':
        cmd_child_up(sys.argv[2] if len(sys.argv) > 2 else 'docker')
    elif cmd == 'child-stop':
        cmd_child_stop()
    elif cmd == 'child-reconfigure':
        cmd_child_reconfigure(sys.argv[2])
    elif cmd == 'child-force-online':
        cmd_child_force_online()
    elif cmd == 'child-rm':
        cmd_child_rm()
    elif cmd == 'fixtures':
        cmd_fixtures(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == 'snapshot':
        cmd_snapshot(sys.argv[2])
    elif cmd == 'get':
        cmd_get(sys.argv[2])
    elif cmd == 'probe':
        hub = Hub(); hub.login()
        state = load_state()
        phase = sys.argv[2]
        expected_rev = int(sys.argv[3])
        fx = state['fixtures']
        fx['execution_id'] = state.get('execution_id', '')
        src = {'script_id': state['fixtures']['probe_script_id']}
        res = Child().probe(src, fx, phase, expected_rev)
        p = rec('phase-probe-%s.json' % phase, res)
        print(json.dumps({'ok': True, 'file': p, 'result': res['result'],
                          'execution': res['probe_execution']}, ensure_ascii=False))
    else:
        print(json.dumps({'commands': ['guard', 'child-up [mode]', 'child-stop',
                                       'child-force-online', 'child-rm', 'fixtures',
                                       'snapshot <name>', 'get <path>', 'probe <phase> <rev>']}))


if __name__ == '__main__':
    main()
