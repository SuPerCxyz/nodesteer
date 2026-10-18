#!/usr/bin/env python3
"""Shared FileTransfer harness library for sys-b14 (system) and
batch10-hub-restart (web).
Evidence chain, no product source changes, no protected Agent stopped, no VM
restarted:

  operator/API act -> real Transfer row -> Hub dispatch over WS -> real Agent
  (owned child, exact repair binary) does the real HTTP upload/download ->
  Hub staged blob -> API / SQLite / physical readback.

Guards enforced here:
  * the authority is always the repair endpoint on :18080;
  * the ONLY unit this library can stop/start is the repair Hub unit
    (`nodesteer-hub-repair-20260930-101945.service`), via hubctl;
  * agent credentials are read only from the harness's OWN child Agent store
    (identity table) and are never printed or written into evidence;
  * the primary `nodesteer-hub.service` (:8080), `nodesteer-agent.service` and
    every protected repair unit are only ever read.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from frame_proxy import FrameProxy  # re-exported for the batch runners

CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
REQUIRED_PORT = 18080

OPERATOR_IP = '192.168.100.170'
HUB_HOST = '192.168.100.209'
HUB_DATA = '/var/lib/nodesteer-hub-repair-20260930-101945'
HUB_XFER_DIR = HUB_DATA + '/file-transfers'
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
NODE_IP = {
    'native-1': '192.168.100.204',
    'native-2': '192.168.100.206',
    'native-3': '192.168.100.222',
    'native-4': '192.168.100.247',
}
TERMINAL = {'SUCCESS', 'FAILED', 'CANCELED'}

os.umask(0o077)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


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


# --------------------------------------------------------------------------- #
# management API client
# --------------------------------------------------------------------------- #
class Hub:
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.relogins = 0

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

    def request(self, method, path, json_body=None, raw=None, ctype=None,
                agent_id=None, token=None, auth=True, timeout=60, retries=2):
        data = raw
        headers = {'Accept': 'application/json'}
        if json_body is not None:
            data = json.dumps(json_body, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        if ctype:
            headers['Content-Type'] = ctype
        if agent_id is not None:
            headers['X-NodeSteer-Agent-ID'] = agent_id
        if token is not None:
            headers['X-NodeSteer-Agent-Token'] = token
        if auth and self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        reauthed = False
        last = None
        for _ in range(max(1, retries)):
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as exc:
                body = exc.read()
                # The repair Hub keeps management sessions in memory: a restart
                # invalidates them, so re-authenticate once and retry.
                if exc.code == 401 and auth and self.token and not reauthed and path != '/api/login':
                    reauthed = True
                    self.relogins += 1
                    self.login()
                    headers['Authorization'] = 'Bearer ' + self.token
                    continue
                return exc.code, dict(exc.headers), body
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def jrequest(self, method, path, json_body=None, **kw):
        code, headers, body = self.request(method, path, json_body=json_body, **kw)
        return code, _decode(body), headers

    def post(self, path, body=None, **kw):
        return self.jrequest('POST', path, json_body=body if body is not None else {}, **kw)

    def get(self, path, **kw):
        return self.jrequest('GET', path, **kw)

    def delete(self, path, **kw):
        return self.jrequest('DELETE', path, **kw)

    def json(self, path, **kw):
        code, body, _ = self.get(path, **kw)
        if code != 200:
            raise RuntimeError('get_%s_http_%s' % (path, code))
        return body

    @staticmethod
    def quote(v):
        return urllib.parse.quote(str(v), safe='')

    # ---- FileTransfer management ----
    def create_transfer(self, source_node_id, source_path, targets):
        return self.post('/api/transfers', {'source_node_id': source_node_id,
                                            'source_path': source_path, 'targets': targets})

    def transfer(self, tid):
        return self.json('/api/transfers/' + self.quote(tid))

    def transfers(self):
        return self.json('/api/transfers')

    # ---- Agent data plane (real HTTP used by the Agent) ----
    def agent_upload(self, tid, agent_id, token, content,
                     declared_size=None, content_range=None, mode=0o644, timeout=120):
        headers = {'Content-Type': 'application/octet-stream'}
        if declared_size is not None:
            headers['X-NodeSteer-File-Size'] = str(declared_size)
        if mode is not None:
            headers['X-NodeSteer-File-Mode'] = ('%o' % mode)
        if content_range is not None:
            headers['Content-Range'] = content_range
        return self._agent_request('POST', '/agent/transfers/%s/upload' % self.quote(tid),
                                   agent_id, token, headers, content, timeout)

    def _agent_request(self, method, path, agent_id, token, headers, body, timeout):
        h = dict(headers)
        if agent_id is not None:
            h['X-NodeSteer-Agent-ID'] = agent_id
        if token is not None:
            h['X-NodeSteer-Agent-Token'] = token
        req = urllib.request.Request(self.base + path, data=body, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def agent_download(self, tid, agent_id, token, timeout=120):
        return self._agent_request('GET', '/agent/transfers/%s/download' % self.quote(tid),
                                   agent_id, token, {}, None, timeout)


# --------------------------------------------------------------------------- #
# SSH helpers (nodes + hub host, read/write on OWNED paths only)
# --------------------------------------------------------------------------- #
def ssh(host, script, timeout=120, check=True):
    cmd = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
           '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + host, 'python3', '-']
    cp = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed:%s:%s' % (host, (cp.stderr or cp.stdout)[-300:]))
    return cp.returncode, cp.stdout, cp.stderr


def ssh_json(host, script, timeout=120):
    _, out, _ = ssh(host, script, timeout=timeout)
    lines = [l for l in out.splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def node_file_state(host, path):
    script = (
        "import json,os,hashlib\n"
        "p=%r\n" % path +
        "out={'path':p}\n"
        "if not os.path.lexists(p):\n"
        "    print(json.dumps(dict(out,exists=False))); raise SystemExit\n"
        "st=os.lstat(p)\n"
        "out['exists']=True; out['size']=st.st_size; out['mode']=oct(st.st_mode & 0o777)[2:]\n"
        "out['is_symlink']=os.path.islink(p); out['is_dir']=os.path.isdir(p)\n"
        "if os.path.islink(p): out['symlink_target']=os.readlink(p)\n"
        "if os.path.isfile(p) and not os.path.islink(p):\n"
        "    h=hashlib.sha256()\n"
        "    with open(p,'rb') as f:\n"
        "        for c in iter(lambda: f.read(1<<20), b''): h.update(c)\n"
        "    out['sha256']=h.hexdigest()\n"
        "print(json.dumps(out))\n")
    return ssh_json(host, script)


def node_list_dir(host, path):
    return ssh_json(host, (
        "import json,os\n"
        "p=%r\n" % path +
        "out={'path':p}\n"
        "if not os.path.isdir(p):\n"
        "    print(json.dumps(dict(out,exists=False))); raise SystemExit\n"
        "items=[]\n"
        "for n in sorted(os.listdir(p)):\n"
        "    fp=os.path.join(p,n)\n"
        "    st=os.lstat(fp)\n"
        "    items.append({'name':n,'size':st.st_size,'is_dir':os.path.isdir(fp),'is_link':os.path.islink(fp)})\n"
        "print(json.dumps(dict(out,exists=True,items=items)))\n"))


def node_make_file(host, path, size, seed=7, timeout=180):
    script = (
        "import json,os,hashlib\n"
        "p=%r; size=%d; seed=%d\n" % (path, size, seed) +
        "os.makedirs(os.path.dirname(p), exist_ok=True)\n"
        "blk=bytes(((i*131+seed*7)&0xFF) for i in range(1<<16))\n"
        "h=hashlib.sha256(); left=size; pos=0\n"
        "with open(p,'wb') as f:\n"
        "    while left>0:\n"
        "        n=min(left,len(blk)); chunk=blk[:n]; f.write(chunk); h.update(chunk); left-=n; pos+=n\n"
        "os.chmod(p,0o644)\n"
        "print(json.dumps({'path':p,'size':size,'sha256':h.hexdigest()}))\n")
    return ssh_json(host, script, timeout=timeout)


def node_symlink(host, link, target):
    return ssh_json(host, (
        "import json,os\n"
        "link=%r; target=%r\n" % (link, target) +
        "os.makedirs(os.path.dirname(link), exist_ok=True)\n"
        "if os.path.lexists(link): os.remove(link)\n"
        "os.symlink(target, link)\n"
        "print(json.dumps({'link':link,'target':target,'created':os.path.islink(link)}))\n"))


def node_rm(host, path):
    return ssh_json(host, (
        "import json,os,shutil\n"
        "p=%r\n" % path +
        "existed=os.path.lexists(p)\n"
        "if os.path.islink(p) or os.path.isfile(p): os.remove(p)\n"
        "elif os.path.isdir(p): shutil.rmtree(p)\n"
        "print(json.dumps({'path':p,'existed':existed,'exists_after':os.path.lexists(p)}))\n"))


def hub_file_state(path):
    return ssh_json(HUB_HOST, (
        "import json,os,hashlib,pwd,grp\n"
        "p=%r\n" % path +
        "out={'path':p}\n"
        "if not os.path.lexists(p):\n"
        "    print(json.dumps(dict(out,exists=False))); raise SystemExit\n"
        "st=os.lstat(p)\n"
        "out['exists']=True; out['size']=st.st_size; out['mode']=oct(st.st_mode & 0o777)[2:]\n"
        "out['uid']=st.st_uid; out['gid']=st.st_gid\n"
        "try:\n"
        "    out['owner']=pwd.getpwuid(st.st_uid).pw_name\n"
        "except Exception:\n"
        "    out['owner']=st.st_uid\n"
        "if os.path.isfile(p):\n"
        "    h=hashlib.sha256()\n"
        "    with open(p,'rb') as f:\n"
        "        for c in iter(lambda: f.read(1<<20), b''): h.update(c)\n"
        "    out['sha256']=h.hexdigest()\n"
        "print(json.dumps(out))\n"))


def hub_xfer_listing():
    return ssh_json(HUB_HOST, (
        "import json,os\n"
        "p=%r\n" % HUB_XFER_DIR +
        "items=[]\n"
        "if os.path.isdir(p):\n"
        "    for n in sorted(os.listdir(p)):\n"
        "        st=os.lstat(os.path.join(p,n))\n"
        "        items.append({'name':n,'size':st.st_size})\n"
        "print(json.dumps({'dir':p,'exists':os.path.isdir(p),'items':items}))\n"))


def hub_blob_backup(tid):
    blob = '%s/%s.blob' % (HUB_XFER_DIR, tid)
    return ssh_json(HUB_HOST, (
        "import json,os,shutil\n"
        "blob=%r; bak=blob+'.qabak'\n" % blob +
        "out={'blob':blob,'exists':os.path.isfile(blob)}\n"
        "if os.path.isfile(blob):\n"
        "    st=os.stat(blob)\n"
        "    out['orig_uid']=st.st_uid; out['orig_gid']=st.st_gid; out['orig_mode']=oct(st.st_mode & 0o777)\n"
        "    if os.path.exists(bak): os.remove(bak)\n"
        "    with open(blob,'rb') as s, open(bak,'wb') as d: shutil.copyfileobj(s,d)\n"
        "    out['backup']=bak; out['backup_size']=os.path.getsize(bak)\n"
        "print(json.dumps(out))\n"))


def hub_blob_corrupt(tid, nbytes=32):
    blob = '%s/%s.blob' % (HUB_XFER_DIR, tid)
    return ssh_json(HUB_HOST, (
        "import json,os\n"
        "blob=%r; n=%d\n" % (blob, nbytes) +
        "out={'blob':blob}\n"
        "if not os.path.isfile(blob):\n"
        "    print(json.dumps(dict(out,corrupted=False))); raise SystemExit\n"
        "with open(blob,'r+b') as f:\n"
        "    f.seek(0); f.write(b'Q'*n); f.flush(); os.fsync(f.fileno())\n"
        "out['corrupted']=True; out['size']=os.path.getsize(blob)\n"
        "print(json.dumps(out))\n"))


def hub_blob_restore(tid):
    """Restore the staged blob IN PLACE so ownership/mode are preserved.

    A copy+os.replace would hand the blob to uid 0 and the Hub service
    (a different uid) would then fail to open it (harness bug, not product).
    """
    blob = '%s/%s.blob' % (HUB_XFER_DIR, tid)
    return ssh_json(HUB_HOST, (
        "import json,os,stat as _st\n"
        "blob=%r; bak=blob+'.qabak'\n" % blob +
        "out={'blob':blob,'had_backup':os.path.isfile(bak)}\n"
        "if os.path.isfile(bak):\n"
        "    st=os.stat(blob)\n"
        "    with open(bak,'rb') as s: data=s.read()\n"
        "    with open(blob,'r+b') as f:\n"
        "        f.seek(0); f.truncate(0); f.write(data); f.flush(); os.fsync(f.fileno())\n"
        "    os.chown(blob, st.st_uid, st.st_gid)\n"
        "    os.chmod(blob, _st.S_IMODE(st.st_mode))\n"
        "    os.remove(bak)\n"
        "    out['restored']=True; out['size']=os.path.getsize(blob)\n"
        "    out['uid']=os.stat(blob).st_uid\n"
        "print(json.dumps(out))\n"))


def unit_probe():
    """Read-only MainPID/start-time probe for the protected units (hub + 4 nodes)."""
    import node_units
    out = {}
    for label, (ip, unit) in node_units.NODES.items():
        out[label] = {'ip': ip, 'units': node_units.probe(ip, [unit, 'nodesteer-agent.service'])}
    out['hub-vm'] = {'ip': node_units.HUB[0],
                     'units': node_units.probe(node_units.HUB[0], [node_units.HUB[1], node_units.HUB[2]])}
    return out


def protected_pids(probe):
    """Flatten a unit_probe() result into {label: {unit: MainPID}}."""
    flat = {}
    for label, rec in probe.items():
        flat[label] = {}
        for unit, props in rec['units'].items():
            flat[label][unit] = {'ActiveState': props.get('ActiveState'),
                                 'MainPID': props.get('MainPID'),
                                 'ExecMainStartTimestamp': props.get('ExecMainStartTimestamp')}
    return flat


# --------------------------------------------------------------------------- #
# owned child Agent (runs the exact repair Agent binary, no systemd unit)
# --------------------------------------------------------------------------- #
class OwnedChild:
    def __init__(self, batch, host, name, nonce):
        self.batch = batch
        self.host = host
        self.name = name
        self.nonce = nonce
        self.root = '/var/lib/nodesteer-qa-%s-%s' % (batch, nonce)
        self.config = '%s/child-%s.yaml' % (self.root, name)
        self.data = '%s/data-%s' % (self.root, name)
        self.log = '%s/child-%s.log' % (self.root, name)
        self.proxy_port = None
        self.node_id = None
        self.agent_id = None

    def inventory(self):
        script = (
            "import json,os,subprocess,hashlib,glob\n"
            "def sha(p):\n"
            "    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['owned_child_processes']=[l for l in lines if %r in l]\n"
            "out['other_qa_child_processes']=[l for l in lines if '/var/lib/nodesteer-qa-' in l and %r not in l]\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "out['agent_binary_sha_matches']=out['agent_binary_sha256']==%r\n"
            "out['owned_root_glob']=sorted(glob.glob('/var/lib/nodesteer-qa-%s-*'))\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',"
            "'nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            % (self.root, self.config, self.config, self.config, AGENT_BIN, AGENT_SHA, self.batch) +
            "print(json.dumps(out))\n")
        return ssh_json(self.host, script)

    def write_config_and_start(self, proxy_port, node_id, agent_id, token, node_name, node_ip):
        self.proxy_port = proxy_port
        self.node_id = node_id
        self.agent_id = agent_id
        yaml_text = (
            'hub_url: "ws://%s:%d"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "native"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 3\n'
            'revision_check_sec: 5\n'
            'max_log_bytes: 262144\n'
        ) % (OPERATOR_IP, proxy_port, token, agent_id, node_name, node_ip, self.data)
        prep = (
            "import os\n"
            "root=%r; cfg=%r; data=%r; log=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "open(log,'a').close()\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.root, self.config, self.data, self.log, yaml_text)
        rc, out, err = ssh(self.host, prep, timeout=60)
        if 'CONFIG_WRITTEN' not in out:
            raise RuntimeError('child_config_write_failed:%s' % ((err or out)[-300:]))
        return self.start()

    def start(self):
        script = (
            "import json,os,subprocess,time\n"
            "cfg=%r; log=%r; agent=%r\n"
            "p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],\n"
            "                   stdout=open(log,'a'), stderr=subprocess.STDOUT,\n"
            "                   stdin=subprocess.DEVNULL, start_new_session=True)\n"
            "time.sleep(1.5)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN)
        return ssh_json(self.host, script, timeout=60)

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
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n")

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" % (self.config, AGENT_BIN) + self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched(): time.sleep(0.5)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n")
        return ssh_json(self.host, script, timeout=60)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n" % (self.root, self.config, AGENT_BIN) + self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched(): time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left: os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1)\n"
            "left=matched()\n"
            "existed=os.path.isdir(root)\n"
            "if os.path.isdir(root): shutil.rmtree(root)\n"
            "print(json.dumps({'signalled':pids,'remaining_processes':left,'root_removed':existed,"
            "'root_exists_after':os.path.exists(root)}))\n")
        return ssh_json(self.host, script, timeout=120)

    def read_cred(self):
        """Read THIS child's own agent credential from its own store (read-only)."""
        script = (
            "import json,sqlite3\n"
            "db=%r\n" % (self.data + '/state.db') +
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "rows=dict(c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id','credential')\"))\n"
            "out={'has_credential':bool(rows.get('credential')),'credential_len':len(rows.get('credential') or ''),"
            "'node_id':rows.get('node_id'),'agent_id':rows.get('agent_id'),"
            "'quick_check':c.execute('PRAGMA quick_check').fetchone()[0]}\n"
            "print(json.dumps(out))\n")
        state = ssh_json(self.host, script, timeout=60)
        if state and state.get('has_credential'):
            # fetch the value separately but never persist/print it
            val = ssh_json(self.host, (
                "import json,sqlite3\n"
                "db=%r\n" % (self.data + '/state.db') +
                "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
                "c.execute('PRAGMA query_only=ON')\n"
                "print(json.dumps({'cred':c.execute(\"SELECT value FROM identity WHERE key='credential'\").fetchone()[0]}))\n"))
            return state, (val or {}).get('cred')
        return state, None

    def log_tail(self, lines=60, grep=None):
        cmd = "tail -n %d %r" % (lines, self.log)
        if grep:
            cmd = "grep -Ei %r %r | tail -n %d" % (grep, self.log, lines)
        script = ("import json,subprocess\n"
                  "p=subprocess.run(['bash','-lc',%r],capture_output=True,text=True)\n"
                  "print(json.dumps({'tail':p.stdout.splitlines()}))\n" % cmd)
        return ssh_json(self.host, script, timeout=60)


# --------------------------------------------------------------------------- #
# small waiting helpers
# --------------------------------------------------------------------------- #
def wait_pred(fn, predicate, timeout=120, interval=1.0):
    deadline = time.time() + timeout
    samples = []
    last = None
    while time.time() < deadline:
        try:
            last = fn()
        except Exception as exc:  # transient API/transport
            last = {'_error': str(exc)}
            continue
        samples.append({'at_utc': now_utc(), 'value': last})
        if predicate(last):
            return {'complete': True, 'seconds': round(timeout - (deadline - time.time()), 1),
                    'last': last, 'samples': samples}
        time.sleep(interval)
    return {'complete': False, 'last': last, 'samples': samples}


def update_json(path, value, mode=0o644):
    """Atomically write a JSON evidence file (bytes-safe, parent dirs created)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return path


decode = _decode


def sha256_file_local(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def b64(data):
    return base64.b64encode(data).decode()
