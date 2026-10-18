#!/usr/bin/env python3
"""sys-b15 harness library: repair-Hub control plane + owned child Agents.

Cases: X01 (native Agent self-upgrade, mixed/paused/offline/duplicate-in-flight/
partial-failure/confirm-cancel/version+reconnect) and X03 (on_start trigger
semantics).

Hard guards enforced here:
  * the authority is always the repair endpoint on :18080;
  * `nodesteer-hub.service` (:8080) and every `nodesteer-agent.service` /
    `nodesteer-agent-repair-*` unit is only ever READ (never stopped/started);
  * owned children are plain background processes started from an OWNED copy of
    the exact repair Agent binary (sha256 d55dc5cd...) under owned roots; the
    protected baseline binary `/opt/nodesteer-repair-20260930-101945/agent/`
    `nodesteer-agent` is only ever read and hashed;
  * the owned child runs with an owned `PATH` whose first entry is an owned
    `systemctl` shim.  Self-upgrade calls `systemctl restart nodesteer-agent`
    (hard-coded unit name, see internal/agent/upgrade.go agentUnitName); without
    the shim that call would RESTART THE PROTECTED PRIMARY UNIT.  The shim
    intercepts exactly `restart nodesteer-agent`, records it, and (per the mode
    chosen by the runner) either returns 0 / returns 1 after a delay; every
    other invocation is passed through to /usr/bin/systemctl untouched;
  * credentials are read from the private 0600 temp file, never printed and
    never written into evidence.
"""
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
REQUIRED_PORT = 18080

OPERATOR_IP = '192.168.100.170'
HUB_HOST = '192.168.100.209'
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
HUB_BIN = '/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard'
REPAIR_HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
PRIMARY_HUB_UNIT = 'nodesteer-hub.service'
PRIMARY_AGENT_UNIT = 'nodesteer-agent.service'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')

HOSTS = {
    'native-1': '192.168.100.204',
    'native-2': '192.168.100.206',
    'native-3': '192.168.100.222',
    'native-4': '192.168.100.247',
}
PROTECTED_UNITS = {
    'native-1': 'nodesteer-agent-repair-20260930-101945-native-1.service',
    'native-2': 'nodesteer-agent-repair-20260930-101945-native-2.service',
    'native-3': 'nodesteer-agent-repair-20260930-101945-native-3.service',
    'native-4': 'nodesteer-agent-repair-20260930-101945-native-4.service',
}
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'CANCELED', 'SKIPPED', 'BLOCKED'}
UPGRADE_TASK_ID = 'agent_upgrade'

os.umask(0o077)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


# --------------------------------------------------------------------------- #
# private credential gate + management API client
# --------------------------------------------------------------------------- #
def _gate(path):
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
        raise RuntimeError('private_file_gate_failed:' + path.name)


def load_authority():
    _gate(CONN)
    _gate(CRED)
    conn = json.loads(CONN.read_text())
    url = (conn.get('hub_url') or '').rstrip('/')
    if urllib.parse.urlsplit(url).port != REQUIRED_PORT:
        raise RuntimeError('endpoint_guard_failed_repair_18080_required')
    return url, json.loads(CRED.read_text())


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:400].decode('utf-8', 'replace')}


class Hub:
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.calls = 0

    def login(self):
        code, _, body = self.request('POST', '/api/login',
                                     json_body={'username': self.cred['admin_username'],
                                                'password': self.cred['admin_password']},
                                     auth=False)
        data = _decode(body)
        if code != 200 or not isinstance(data, dict) or not data.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = data['token']
        return self.token

    def request(self, method, path, json_body=None, auth=True, timeout=60, retries=2):
        data = None
        headers = {'Accept': 'application/json'}
        if json_body is not None:
            data = json.dumps(json_body, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        if auth and self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        last = None
        for _ in range(max(1, retries)):
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                return exc.code, dict(exc.headers), exc.read()
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def jrequest(self, method, path, json_body=None, **kw):
        code, headers, body = self.request(method, path, json_body=json_body, **kw)
        return code, _decode(body), headers

    def post(self, path, body=None, **kw):
        return self.jrequest('POST', path, json_body=body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.jrequest('PUT', path, json_body=body if body is not None else {}, **kw)

    def get(self, path, **kw):
        return self.jrequest('GET', path, **kw)

    def delete(self, path, **kw):
        return self.jrequest('DELETE', path, **kw)

    def json(self, path, **kw):
        code, body, _ = self.get(path, **kw)
        if code != 200:
            raise RuntimeError('get_%s_http_%s' % (path, code))
        return body

    def nodes(self):
        return self.json('/api/nodes')

    def node(self, node_id):
        code, body, _ = self.get('/api/nodes/' + urllib.parse.quote(node_id))
        return code, body

    def executions(self, task_id=None, node_id=None, limit=200):
        q = []
        if task_id:
            q.append('task_id=' + urllib.parse.quote(task_id))
        if node_id:
            q.append('node_id=' + urllib.parse.quote(node_id))
        q.append('limit=%d' % limit)
        code, body, headers = self.get('/api/executions?' + '&'.join(q))
        return code, body, headers

    def count_executions(self, task_id=None, node_id=None):
        code, body, headers = self.executions(task_id=task_id, node_id=node_id)
        if code != 200:
            raise RuntimeError('executions_http_%s' % code)
        total = headers.get('X-Total-Count')
        return (int(total) if total is not None else len(body)), body

    def upgrade(self, node_ids):
        return self.post('/api/nodes/upgrade', {'node_ids': list(node_ids)})

    def logins_left(self):
        return self.token is not None


def health():
    out = {}
    for p in ('/healthz', '/readyz'):
        try:
            with urllib.request.urlopen('http://%s:18080%s' % (HUB_HOST, p), timeout=6) as r:
                out[p] = {'http': r.status, 'body': r.read(40).decode('utf-8', 'replace').strip()}
        except urllib.error.HTTPError as exc:
            out[p] = {'http': exc.code, 'body': exc.read(80).decode('utf-8', 'replace').strip()}
        except Exception as exc:
            out[p] = {'error': type(exc).__name__}
    return out


def healthy():
    h = health()
    return h.get('/healthz', {}).get('http') == 200 and h.get('/readyz', {}).get('http') == 200


def agent_binary_head():
    """Read-only record of the artifact the repair Hub serves for self-upgrade."""
    try:
        req = urllib.request.Request('http://%s:18080/api/agent/binary?architecture=amd64' % HUB_HOST,
                                     method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            h = hashlib.sha256()
            n = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                n += len(chunk)
            return {'http': resp.status, 'content_length': n,
                    'header_sha256': resp.headers.get('X-Agent-Binary-SHA256'),
                    'body_sha256': h.hexdigest(),
                    'matches_baseline_agent_sha': h.hexdigest() == AGENT_SHA}
    except urllib.error.HTTPError as exc:
        return {'http': exc.code, 'body': exc.read(200).decode('utf-8', 'replace')}
    except Exception as exc:
        return {'error': type(exc).__name__}


# --------------------------------------------------------------------------- #
# SSH helpers (read-only everywhere except explicit owned paths)
# --------------------------------------------------------------------------- #
def ssh(host, script, timeout=180, check=True):
    cmd = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + host, 'python3', '-']
    cp = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed:%s:%s' % (host, (cp.stderr or cp.stdout)[-400:]))
    return cp.returncode, cp.stdout, cp.stderr


def ssh_json(host, script, timeout=180):
    _, out, _ = ssh(host, script, timeout=timeout)
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError('ssh_empty_output:%s' % host)
    return json.loads(lines[-1])


UNIT_PROPS = 'Id,LoadState,ActiveState,SubState,MainPID,ExecMainStartTimestamp,UnitFileState,FragmentPath'


def unit_probe():
    """Read-only MainPID/start-time probe of all protected units (never writes)."""
    out = {}
    for label, ip in HOSTS.items():
        units = [PROTECTED_UNITS[label], PRIMARY_AGENT_UNIT]
        out[label] = {'ip': ip, 'units': ssh_json(ip, (
            "import json,subprocess\n"
            "units=%r\n"
            "o={}\n"
            "for u in units:\n"
            "    r=subprocess.run(['systemctl','show','--no-pager','--property='+%r,u],"
            "capture_output=True,text=True)\n"
            "    o[u]=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(o))\n" % (units, UNIT_PROPS)), timeout=90)}
    out['hub-vm'] = {'ip': HUB_HOST, 'units': ssh_json(HUB_HOST, (
        "import json,subprocess\n"
        "units=%r\n"
        "o={}\n"
        "for u in units:\n"
        "    r=subprocess.run(['systemctl','show','--no-pager','--property='+%r,u],"
        "capture_output=True,text=True)\n"
        "    o[u]=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
        "print(json.dumps(o))\n" % ([PRIMARY_HUB_UNIT, REPAIR_HUB_UNIT], UNIT_PROPS)), timeout=90)}
    return out


def unit_fingerprint(probe):
    flat = {}
    for label, rec in probe.items():
        flat[label] = {}
        for unit, props in rec['units'].items():
            flat[label][unit] = {'ActiveState': props.get('ActiveState'),
                                 'SubState': props.get('SubState'),
                                 'MainPID': props.get('MainPID'),
                                 'ExecMainStartTimestamp': props.get('ExecMainStartTimestamp')}
    return flat


def protected_binary_sha(host):
    return ssh_json(host, (
        "import json,hashlib,os\n"
        "p=%r\n" % AGENT_BIN +
        "out={'path':p}\n"
        "try:\n"
        "    out['sha256']=hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
        "    out['size']=os.path.getsize(p)\n"
        "except Exception as e:\n"
        "    out['error']=type(e).__name__\n"
        "print(json.dumps(out))\n"), timeout=120)


def node_file_state(host, path):
    return ssh_json(host, (
        "import json,os,hashlib\n"
        "p=%r\n" % path +
        "out={'path':p}\n"
        "if not os.path.lexists(p):\n"
        "    print(json.dumps(dict(out,exists=False))); raise SystemExit\n"
        "st=os.lstat(p)\n"
        "out['exists']=True; out['size']=st.st_size; out['mode']=oct(st.st_mode & 0o777)[2:]\n"
        "out['is_symlink']=os.path.islink(p); out['is_dir']=os.path.isdir(p)\n"
        "out['uid']=st.st_uid\n"
        "if os.path.islink(p): out['symlink_target']=os.readlink(p)\n"
        "if os.path.isfile(p) and not os.path.islink(p):\n"
        "    h=hashlib.sha256()\n"
        "    with open(p,'rb') as f:\n"
        "        for c in iter(lambda: f.read(1<<20), b''): h.update(c)\n"
        "    out['sha256']=h.hexdigest()\n"
        "print(json.dumps(out))\n"))


# --------------------------------------------------------------------------- #
# owned child Agent
# --------------------------------------------------------------------------- #
SHIM_TEMPLATE = """#!/bin/sh
# sys-b15 owned PATH shim (harness artifact, not product code).
# The Agent's self-upgrade restarts the hard-coded unit name "nodesteer-agent"
# (internal/agent/upgrade.go agentUnitName).  Restarting the protected primary
# unit is forbidden for this batch, so the owned child runs with this shim
# FIRST on its own PATH: exactly "systemctl restart nodesteer-agent" is
# intercepted (logged, optional delay, chosen exit code); every other call is
# passed through to the real systemctl.
printf '%%s exit-pending args=%%s\\n' "$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" "$*" >> %(log)r
if [ "$1" = "restart" ] && [ "$2" = "nodesteer-agent" ]; then
  sleep %(sleep)d
  printf '%%s intercepted-restart exit=%(exit)d\\n' "$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ)" >> %(log)r
  exit %(exit)d
fi
exec /usr/bin/systemctl "$@"
"""


class OwnedChild:
    def __init__(self, batch, nonce, name, host_label, deployment_mode='native',
                 shim_sleep=0, shim_exit=0, mark_binary=True, via_proxy=False):
        self.batch = batch
        self.nonce = nonce
        self.name = name
        self.label = host_label
        self.host = HOSTS[host_label] if host_label in HOSTS else host_label
        self.deployment_mode = deployment_mode
        self.shim_sleep = shim_sleep
        self.shim_exit = shim_exit
        self.mark_binary = mark_binary
        self.via_proxy = via_proxy
        self.prefix = 'qa-%s-%s-' % (batch, nonce)
        self.root = '/var/lib/nodesteer-qa-%s-%s/%s' % (batch, nonce, name)
        self.opt = '/opt/qa-%s-%s/%s' % (batch, nonce, name)
        self.binary = self.opt + '/nodesteer-agent'
        self.shim_dir = self.opt + '/bin'
        self.shim_log = self.shim_dir + '/systemctl-shim.log'
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = None
        self.node_id = None
        self.agent_id = None
        self.agent_token = None

    # ---------- remote plumbing ----------
    def remote(self, script, timeout=180, check=True):
        return ssh(self.host, script, timeout=timeout, check=check)

    def py(self, script, timeout=180):
        return ssh_json(self.host, script, timeout=timeout)

    # ---------- prepare ----------
    def prepare(self, hub_url):
        """Create the owned root/opt tree, the owned binary copy (+marker) and
        the owned `systemctl` shim.  Returns the binary sha256 state."""
        marker = ('\n#QASYSD15-OLDVERSION-%s\n' % self.nonce).encode() if self.mark_binary else b''
        shim = SHIM_TEMPLATE % {'log': self.shim_log, 'sleep': self.shim_sleep, 'exit': self.shim_exit}
        yaml_text = (
            'hub_url: "%s"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 3\n'
            'revision_check_sec: 3\n'
            'max_log_bytes: 262144\n'
        ) % (hub_url, self.agent_token, self.agent_id, self.prefix + self.name,
             self.host, self.deployment_mode, self.data)
        script = (
            "import json,os,hashlib\n"
            "root=%(root)r; opt=%(opt)r; shim_dir=%(shimdir)r; src=%(src)r; dst=%(dst)r\n"
            "marker=%(marker)r; shim=%(shim)r; cfg=%(cfg)r; data=%(data)r; log=%(log)r\n"
            "body=%(body)r\n"
            "out={}\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "os.makedirs(shim_dir, mode=0o755, exist_ok=True)\n"
            "os.makedirs(os.path.dirname(opt), mode=0o755, exist_ok=True)\n"
            "os.makedirs(opt, mode=0o755, exist_ok=True)\n"
            "with open(src,'rb') as f: data_b=f.read()\n"
            "with open(dst,'wb') as f: f.write(data_b); f.write(marker)\n"
            "os.chmod(dst,0o755)\n"
            "sd=shim_dir+'/systemctl'\n"
            "fd=os.open(sd, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o755)\n"
            "os.write(fd, shim.encode()); os.close(fd)\n"
            "open(shim_dir+'/systemctl-shim.log','a').close()\n"
            "def sha(p):\n"
            "    h=hashlib.sha256()\n"
            "    with open(p,'rb') as f:\n"
            "        for c in iter(lambda: f.read(1<<20), b''): h.update(c)\n"
            "    return h.hexdigest()\n"
            "out['baseline_sha256']=sha(src)\n"
            "out['baseline_is_pinned']=out['baseline_sha256']==%(pinned)r\n"
            "out['owned_binary_sha256']=sha(dst)\n"
            "out['owned_binary_size']=os.path.getsize(dst)\n"
            "out['marker_appended']=bool(marker)\n"
            "out['shim_sha256']=sha(sd)\n"
            "out['shim_mode']=oct(os.stat(sd).st_mode & 0o777)\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "open(log,'a').close()\n"
            "out['root_mode']=oct(os.stat(root).st_mode & 0o777)\n"
            "out['config_mode']=oct(os.stat(cfg).st_mode & 0o777)\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'opt': self.opt, 'shimdir': self.shim_dir, 'src': AGENT_BIN,
             'dst': self.binary, 'marker': marker, 'shim': shim, 'cfg': self.config,
             'data': self.data, 'log': self.log, 'body': yaml_text, 'pinned': AGENT_SHA}
        return self.py(script, timeout=180)

    def rewrite_shim(self, shim_sleep, shim_exit):
        """Switch the shim behaviour between phases (owned path only)."""
        self.shim_sleep = shim_sleep
        self.shim_exit = shim_exit
        shim = SHIM_TEMPLATE % {'log': self.shim_log, 'sleep': shim_sleep, 'exit': shim_exit}
        return self.py(
            "import json,os,hashlib\n"
            "sd=%(sd)r; shim=%(shim)r\n"
            "fd=os.open(sd, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o755)\n"
            "os.write(fd, shim.encode()); os.close(fd)\n"
            "print(json.dumps({'shim':sd,'sleep':%(sleep)d,'exit':%(exit)d}))\n"
            % {'sd': self.shim_dir + '/systemctl', 'shim': shim, 'sleep': shim_sleep, 'exit': shim_exit})

    # ---------- lifecycle ----------
    def start(self):
        script = (
            "import json,os,subprocess,time\n"
            "cfg=%(cfg)r; log=%(log)r; agent=%(agent)r; shim=%(shim)r\n"
            "env=dict(os.environ)\n"
            "env['PATH']=shim+':'+env.get('PATH','/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')\n"
            "for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'):\n"
            "    env.pop(k, None)\n"
            "env['NO_PROXY']='*'; env['no_proxy']='*'\n"
            "p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],\n"
            "                   stdout=open(log,'a'), stderr=subprocess.STDOUT,\n"
            "                   stdin=subprocess.DEVNULL, start_new_session=True, env=env)\n"
            "time.sleep(1.5)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,"
            "'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % {'cfg': self.config, 'log': self.log, 'agent': self.binary, 'shim': self.shim_dir}
        return self.py(script, timeout=90)

    def _pids_guard(self):
        return (
            "import os,subprocess\n"
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit(): continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%d/exe' % pid)\n"
            "            args=open('/proc/%d/cmdline' % pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            # a self-replaced (renamed) binary keeps the old inode: the kernel
            # appends ' (deleted)' to /proc/PID/exe, so normalise it before the
            # identity comparison or the owned process would look foreign.
            "        if exe.endswith(' (deleted)'): exe=exe[:-len(' (deleted)')]\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n")

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" % (self.config, self.binary) + self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+15\n"
            "while time.time()<end and matched(): time.sleep(0.5)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n")
        return self.py(script, timeout=90)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.5)
        started = self.start()
        return {'stop': stopped, 'start': started}

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%(root)r; opt=%(opt)r; cfg=%(cfg)r; agent=%(agent)r\n"
            % {'root': self.root, 'opt': self.opt, 'cfg': self.config, 'agent': self.binary} +
            self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+15\n"
            "while time.time()<end and matched(): time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left: os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1.0)\n"
            "left=matched()\n"
            "root_existed=os.path.isdir(root); opt_existed=os.path.isdir(opt)\n"
            "for p in (root, opt):\n"
            "    if os.path.isdir(p): shutil.rmtree(p)\n"
            "print(json.dumps({'signalled':pids,'remaining_processes':left,"
            "'root_removed':root_existed,'opt_removed':opt_existed,"
            "'root_exists_after':os.path.exists(root),'opt_exists_after':os.path.exists(opt)}))\n")
        return self.py(script, timeout=180)

    def kill(self):
        """SIGKILL (identity-guarded) - simulates an abrupt process loss."""
        return self.py(
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" % (self.config, self.binary) + self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids: os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1.0)\n"
            "print(json.dumps({'killed':pids,'remaining':matched()}))\n", timeout=60)

    # ---------- evidence probes ----------
    def state(self, extra_paths=()):
        paths = [self.binary, self.binary + '.bak', self.binary + '.new',
                 self.binary + '.new.tmp', self.config, self.shim_dir + '/systemctl', self.shim_log]
        paths += list(extra_paths)
        script = (
            "import json,os,subprocess,hashlib,glob,time\n"
            "root=%(root)r; opt=%(opt)r; cfg=%(cfg)r; agent=%(agent)r; paths=%(paths)r; nonce=%(nonce)r\n"
            "def sha(p):\n"
            "    try:\n"
            "        h=hashlib.sha256()\n"
            "        with open(p,'rb') as f:\n"
            "            for c in iter(lambda: f.read(1<<20), b''): h.update(c)\n"
            "        return h.hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "out={}\n"
            "out['files']={}\n"
            "for p in paths:\n"
            "    e=os.path.lexists(p)\n"
            "    rec={'exists':e}\n"
            "    if e:\n"
            "        st=os.lstat(p)\n"
            "        rec.update({'is_dir':os.path.isdir(p),'is_link':os.path.islink(p),"
            "'size':st.st_size,'mode':oct(st.st_mode & 0o777)[2:],'mtime':st.st_mtime,'uid':st.st_uid})\n"
            "        if os.path.isfile(p) and not os.path.islink(p): rec['sha256']=sha(p)\n"
            "    out['files'][p]=rec\n"
            "def pids(pattern):\n"
            "    r=subprocess.run(['pgrep','-af',pattern],capture_output=True,text=True)\n"
            "    return [l for l in r.stdout.splitlines() if l.strip()]\n"
            "def guard():\n"
            "    res=[]\n"
            "    for pid in pids(agent):\n"
            "        p=pid.split(' ',1)[0]\n"
            "        try:\n"
            "            exe=os.readlink('/proc/'+p+'/exe')\n"
            "            args=open('/proc/'+p+'/cmdline','rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe.endswith(' (deleted)'): exe=exe[:-len(' (deleted)')]\n"
            "        if exe!=agent: continue\n"
            "        if cfg.encode() in args:\n"
            "            res.append({'pid':int(p),'exe':exe,"
            "'start_ticks':open('/proc/'+p+'/stat').read().split()[21]})\n"
            "    return res\n"
            "out['owned_pids']=guard()\n"
            "out['owned_processes']=pids(cfg)\n"
            "out['all_nodesteer_agent_processes']=pids('nodesteer-agent')\n"
            "out['owned_roots']=sorted(glob.glob('/var/lib/nodesteer-qa-%%s-*' %% %(batch)r))\n"
            "out['owned_opts']=sorted(glob.glob('/opt/qa-%%s-*' %% %(batch)r))\n"
            "def unit(u):\n"
            "    r=subprocess.run(['systemctl','show','--no-pager','--property=%%s' %% %(props)r,u],"
            "capture_output=True,text=True)\n"
            "    return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "out['primary_agent_unit']=unit(%(prim)r)\n"
            "out['protected_repair_unit']=unit(%(repair)r)\n"
            "out['etc_systemd_owned']=[n for n in sorted(os.listdir('/etc/systemd/system')) if %(prefix)r in n]\n"
            "out['shim_calls']=[l for l in (open(%(shimlog)r).read().splitlines() if os.path.isfile(%(shimlog)r) else [])][-20:]\n"
            "out['baseline_agent_sha256']=sha(%(baseline)r)\n"
            "out['baseline_agent_is_pinned']=out['baseline_agent_sha256']==%(pinned)r\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'opt': self.opt, 'cfg': self.config, 'agent': self.binary,
             'paths': paths, 'nonce': self.nonce, 'batch': self.batch, 'props': UNIT_PROPS,
             'prim': PRIMARY_AGENT_UNIT, 'repair': PROTECTED_UNITS[self.label],
             'prefix': self.prefix, 'shimlog': self.shim_log, 'baseline': AGENT_BIN,
             'pinned': AGENT_SHA}
        return self.py(script, timeout=180)

    def log_tail(self, lines=80, pattern=None):
        cmd = "tail -n %d %r" % (lines, self.log)
        if pattern:
            cmd = "grep -E %r %r | tail -n %d" % (pattern, self.log, lines)
        return self.py("import json,subprocess\n"
                       "p=subprocess.run(['bash','-lc',%r],capture_output=True,text=True)\n"
                       "print(json.dumps({'cmd':%r,'lines':p.stdout.splitlines()}))\n" % (cmd, cmd),
                       timeout=60)

    def db_read(self):
        """Read-only view of THIS child's own local store."""
        return self.py(
            "import json,os,sqlite3\n"
            "db=%(db)r; data=%(data)r\n"
            "out={'db':db,'exists':os.path.isfile(db)}\n"
            "try:\n"
            "    st=os.stat(db); out['size']=st.st_size; out['inode']=st.st_ino\n"
            "    c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "    c.execute('PRAGMA query_only=ON')\n"
            "    out['quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]\n"
            "    out['tables']=[r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\")]\n"
            "    out['identity']={k:{'present':True,'len':len(v)} for k,v in c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id','credential')\")}\n"
            "    out['sync_state']=[{'global_revision':r[0],'last_sync':r[1]} for r in c.execute('SELECT global_revision,last_sync FROM sync_state')]\n"
            "    try:\n"
            "        rows=c.execute('SELECT id,definition_json,revision,enabled FROM schedules').fetchall()\n"
            "        out['schedules']=[{'id':r[0],'type':(json.loads(r[1]) or {}).get('type'),"
            "'task_id':(json.loads(r[1]) or {}).get('task_id'),'revision':r[2],'enabled':r[3]} for r in rows]\n"
            "    except Exception as e:\n"
            "        out['schedules_error']=type(e).__name__+':'+str(e)\n"
            "    try:\n"
            "        rows=c.execute('SELECT id,task_id,status,trigger_type,scheduled_time,synced FROM executions ORDER BY start_time').fetchall()\n"
            "        out['executions']=[{'id':r[0],'task_id':r[1],'status':r[2],'trigger_type':r[3],"
            "'scheduled_time':r[4],'synced':r[5]} for r in rows]\n"
            "    except Exception as e:\n"
            "        out['executions_error']=type(e).__name__\n"
            "    try:\n"
            "        out['tasks']=[{'id':r[0],'revision':r[1],'enabled':r[2]} for r in c.execute('SELECT id,revision,enabled FROM tasks').fetchall()]\n"
            "    except Exception as e:\n"
            "        out['tasks_error']=type(e).__name__\n"
            "    c.close()\n"
            "except Exception as e:\n"
            "    out['error']=type(e).__name__+':'+str(e)\n"
            "print(json.dumps(out))\n" % {'db': self.data + '/state.db', 'data': self.data},
            timeout=60)


# --------------------------------------------------------------------------- #
# waiting
# --------------------------------------------------------------------------- #
def wait_pred(fn, predicate, timeout=120, interval=1.5):
    deadline = time.time() + timeout
    samples = []
    last = None
    while time.time() < deadline:
        try:
            last = fn()
        except Exception as exc:
            last = {'_error': type(exc).__name__}
            samples.append({'at_utc': now_utc(), 'value': last})
            time.sleep(interval)
            continue
        samples.append({'at_utc': now_utc(), 'value': last})
        if predicate(last):
            return {'complete': True, 'seconds': round(timeout - (deadline - time.time()), 1),
                    'last': last, 'samples': samples}
        time.sleep(interval)
    return {'complete': False, 'last': last, 'samples': samples}


def remove_batch_dirs(batch, nonce, hosts):
    """Remove the batch-level owned roots (/var/lib + /opt) on every host.

    Child-level removal only deletes <batch_root>/<name> and <batch_opt>/<name>;
    the batch root itself must go too so no owned residue is left behind.
    Refuses to run while any owned process of this batch still exists.
    """
    out = {}
    for host in hosts:
        out[host] = ssh_json(host, (
            "import json,os,shutil,subprocess\n"
            "root='/var/lib/nodesteer-qa-%s-%s'; opt='/opt/qa-%s-%s'\n"
            "p=subprocess.run(['pgrep','-af','qa-%s-%s'],capture_output=True,text=True)\n"
            "procs=[l for l in p.stdout.splitlines() if l.strip() and %r not in l]\n"
            "out={'host':%r,'root':root,'opt':opt,'owned_processes_before':procs}\n"
            "if procs:\n"
            "    out['refused']='owned_processes_present'\n"
            "    print(json.dumps(out)); raise SystemExit\n"
            "removed=[]\n"
            "for d in (root,opt):\n"
            "    if os.path.isdir(d):\n"
            "        shutil.rmtree(d); removed.append(d)\n"
            "out['removed']=removed\n"
            "out['root_exists_after']=os.path.exists(root)\n"
            "out['opt_exists_after']=os.path.exists(opt)\n"
            "print(json.dumps(out))\n" % (batch, nonce, batch, nonce, batch, nonce, 'python3', host)))
    return out


def update_json(path, value, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return path
