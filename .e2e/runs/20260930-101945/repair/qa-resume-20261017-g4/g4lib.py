"""G4 batch helpers (qa-resume-20261017-g4).

Scope guards:
- Hub endpoint is hard-guarded to the repair authority port 18080 (never :8080).
- Docker work happens only on repair native-4 (192.168.100.247); native-2
  (192.168.100.206) is used read-only (`docker save` stream only).
- Credentials live in the private 0600 temp file; they are never printed,
  written into evidence, or passed through argv.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
STATE_PATH = HERE / 'state.json'

HOST = '192.168.100.209'
NATIVE4 = '192.168.100.247'
NATIVE2 = '192.168.100.206'
KEY = str(Path.home() / '.ssh' / 'id_ed25519')

CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
REQUIRED_PORT = 18080

# ------------------------------------------------------------------ constants
NONCE = '74acdeb973df'
OWNER = 'qa-g4-' + NONCE
ROOT = '/opt/' + OWNER
NET = OWNER + '-net'
VOL = OWNER + '-data'
CONT = OWNER + '-compose'
PROJ = OWNER
IMAGE = 'sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44'
IMAGE_TAG = 'nodesteer-qa/agent-host:f174890c3846'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'

SCRIPT = OWNER + '-script'
SCRIPT2 = OWNER + '-script-2'
TASK = OWNER + '-task'
TASK2 = OWNER + '-task-2'
ARTIFACT = OWNER + '-artifact'
APP = OWNER + '-app'
MARKER = OWNER + '-marker'
MARKER2 = OWNER + '-marker-2'

HUB_SCRIPT = OWNER + '-hub-script'
HUB_UNIT = OWNER + '-hub-unit'
HUB_BIN = '/var/lib/nodesteer/' + OWNER + '-bin'
HUB_CFG = '/var/lib/nodesteer/' + OWNER + '-cfg.env'

VOL_MP = '/var/lib/docker/volumes/' + VOL + '/_data'
DB = VOL_MP + '/state.db'
COMPOSE_PATH = ROOT + '/compose.yaml'
CONFIG_PATH = ROOT + '/private/agent.yaml'

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'TIMED_OUT', 'CANCELED'}

os.umask(0o077)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ev(name, value, mode=0o644):
    EV.mkdir(parents=True, exist_ok=True)
    path = EV / (name + '.json')
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return value


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {'batch_id': 'qa-resume-20261017-g4', 'nonce': NONCE, 'owner': OWNER,
            'created_at_utc': now_utc(), 'hub_objects': {}, 'nodes': {}, 'containers': {},
            'network': NET, 'volume': VOL, 'root': ROOT, 'image': IMAGE}


def save_state(state):
    tmp = STATE_PATH.with_name(STATE_PATH.name + '.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    os.replace(tmp, STATE_PATH)


# ------------------------------------------------------------------ ssh / docker
def ssh(host, script, timeout=180, check=True):
    cp = subprocess.run(
        ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
         '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR', '-i', KEY, 'root@' + host,
         'bash', '-s'],
        input=script, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed host=%s rc=%s err=%s' % (host, cp.returncode, (cp.stderr[-500:] or cp.stdout[-500:])))
    return cp.returncode, cp.stdout, cp.stderr


def rpy(host, code, timeout=180, check=True):
    """Run python3 on the remote host; return the JSON object printed last."""
    script = "python3 - <<'G4PYEOF'\n" + code + "\nG4PYEOF\n"
    rc, out, err = ssh(host, script, timeout=timeout, check=check)
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError('remote_py_empty_output host=%s: %s' % (host, err[-400:]))
    return json.loads(lines[-1])


def n4(script, timeout=180, check=True):
    return ssh(NATIVE4, script, timeout=timeout, check=check)


def dargs(args):
    return 'docker ' + ' '.join(shlex.quote(a) for a in args)


def dk(args, timeout=180, check=True):
    return n4(dargs(args), timeout=timeout, check=check)


def dk_inspect(kind, name, timeout=90):
    """Return parsed json of `docker <kind> inspect <name>`, or None if absent."""
    rc, out, _ = dk([kind, 'inspect', name], timeout=timeout, check=False)
    if rc != 0 or not out.strip():
        return None
    try:
        rows = json.loads(out)
    except Exception:
        return None
    return rows[0] if isinstance(rows, list) and rows else None


def dk_exists(kind, name):
    return dk_inspect(kind, name) is not None


def container_state(cid):
    insp = dk_inspect('container', cid)
    if not insp:
        return None
    st = insp.get('State', {})
    return {'id': insp.get('Id'), 'running': st.get('Running'), 'pid': st.get('Pid'),
            'started_at': st.get('StartedAt'), 'exit_code': st.get('ExitCode'),
            'status': st.get('Status'), 'restart_count': insp.get('RestartCount'),
            'image': insp.get('Image'), 'user': insp.get('Config', {}).get('User'),
            'readonly': insp.get('HostConfig', {}).get('ReadonlyRootfs'),
            'labels': insp.get('Config', {}).get('Labels')}


# ------------------------------------------------------------------ hub client
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


class Hub:
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.calls = 0

    def login(self):
        code, body = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'], 'password': self.cred['admin_password']})
        if code != 200 or not isinstance(body, dict) or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, timeout=25, retries=3, headers=None):
        data = None
        hdrs = {'Accept': 'application/json'}
        if headers:
            hdrs.update(headers)
        if self.token:
            hdrs['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            data = json.dumps(body, separators=(',', ':')).encode()
            hdrs['Content-Type'] = 'application/json'
        last = None
        for _ in range(retries):
            req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
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

    def get(self, path, timeout=25):
        return self.raw('GET', path, None, timeout)

    def post(self, path, body=None, timeout=25):
        return self.raw('POST', path, body if body is not None else {}, timeout)

    def put(self, path, body, timeout=25):
        return self.raw('PUT', path, body, timeout)

    def delete(self, path, timeout=25):
        return self.raw('DELETE', path, None, timeout)

    def upload_artifact(self, name, version, filename, content, arch='amd64', timeout=60):
        boundary = '----g4' + os.urandom(12).hex()
        parts = []
        for field, value in (('name', name), ('version', version), ('architecture', arch)):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, field, value)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode())
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode())
        data = b''.join(parts)
        hdrs = {'Accept': 'application/json',
                'Content-Type': 'multipart/form-data; boundary=' + boundary,
                'Content-Length': str(len(data))}
        if self.token:
            hdrs['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(self.base + '/api/artifacts', data=data, headers=hdrs, method='POST')
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:300].decode('utf-8', 'replace')}


def hub():
    h = Hub()
    h.login()
    return h


def rows_of(body, key):
    if isinstance(body, dict):
        return body.get(key) or []
    return body or []


def node_by_name(h, name):
    code, rows = h.get('/api/nodes')
    if code != 200 or not isinstance(rows, list):
        raise RuntimeError('nodes_list_failed_http_%s' % code)
    matches = [r for r in rows if isinstance(r, dict) and r.get('hostname') == name]
    if len(matches) > 1:
        raise RuntimeError('node_name_not_unique:' + name)
    return matches[0] if matches else None


def wait_node_by_name(h, name, timeout=150):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = node_by_name(h, name)
        if last and last.get('status') == 'online' and last.get('sync_status') == 'synced':
            return last
        time.sleep(1.0)
    raise RuntimeError('node_not_online_synced:%s last=%s' % (name, json.dumps(last)[:300]))


def node_row(h, node_id, fields):
    code, row = h.get('/api/nodes/' + node_id)
    if code != 200:
        return None
    return {k: row.get(k) for k in fields}


def watermark_from_protected(h):
    """Hub-side applied-revision watermark read from the 4 idle protected nodes."""
    code, rows = h.get('/api/nodes')
    out = {}
    for r in rows or []:
        if isinstance(r, dict) and r.get('id') in PROTECTED_NODES:
            out[PROTECTED_NODES[r['id']]] = {'status': r.get('status'), 'sync_status': r.get('sync_status'),
                                             'global_revision': r.get('global_revision')}
    return out


HUB_DB = '/var/lib/nodesteer-hub-repair-20260930-101945/hub.db'

HUB_WM_CODE = r'''
import json, os, sqlite3, datetime
p = __HUBDB__
res = {"db_path": p, "sampled_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
con = sqlite3.connect("file:" + p + "?mode=ro", uri=True)
con.execute("PRAGMA query_only=1")
res["revision_sequence"] = con.execute("SELECT value FROM revision_sequence").fetchone()[0]
if __DETAIL__:
    res["recent_changes"] = [{"global_revision": r[0], "object_type": r[1], "object_id": r[2],
                              "object_revision": r[3], "operation": r[4], "created_at": r[5]}
                             for r in con.execute(
        "SELECT global_revision,object_type,object_id,object_revision,operation,created_at "
        "FROM revision_changes ORDER BY global_revision DESC LIMIT __LIMIT__")]
    res["changes_since"] = [{"global_revision": r[0], "object_type": r[1], "object_id": r[2],
                             "object_revision": r[3], "operation": r[4], "created_at": r[5]}
                            for r in con.execute(
        "SELECT global_revision,object_type,object_id,object_revision,operation,created_at "
        "FROM revision_changes WHERE global_revision > ? ORDER BY global_revision", (__SINCE__,))]
con.close()
print(json.dumps(res))
'''


def hub_watermark(detail=False, since=0, limit=8):
    """Authoritative hub watermark, read-only (mode=ro + query_only) from the repair hub DB."""
    code = (HUB_WM_CODE.replace('__HUBDB__', json.dumps(HUB_DB))
            .replace('__DETAIL__', 'True' if detail else 'False')
            .replace('__LIMIT__', str(limit)).replace('__SINCE__', str(since)))
    return rpy(HOST, code.strip())


def wait_node_report(h, node_id, min_rev, timeout=55):
    """Wait until the hub node row reports at least min_rev (heartbeat cadence ~30s)."""
    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 < timeout:
        last = node_row(h, node_id, ['status', 'sync_status', 'global_revision', 'last_seen'])
        if last and (last.get('global_revision') or 0) >= min_rev:
            return {'waited_s': round(time.monotonic() - t0, 1), 'row': last, 'converged': True}
        time.sleep(1.5)
    return {'waited_s': round(time.monotonic() - t0, 1), 'row': last, 'converged': False}


def run_task_to_terminal(h, task_id, timeout=150):
    code, started = h.post('/api/tasks/' + task_id + '/run', {'params': {}})
    if isinstance(started, list):
        execs = started
    elif isinstance(started, dict):
        execs = started.get('executions') or ([started] if started.get('id') else None)
    else:
        execs = None
    if code not in (200, 201) or not execs or len(execs) != 1:
        raise RuntimeError('task_run_shape_invalid http=%s body=%s' % (code, json.dumps(started)[:300]))
    eid = execs[0]['id']
    deadline = time.monotonic() + timeout
    term = None
    while time.monotonic() < deadline:
        code, cur = h.get('/api/executions/' + eid)
        if code != 200:
            raise RuntimeError('execution_readback_http_%s' % code)
        if cur.get('status') in TERMINAL:
            term = cur
            break
        time.sleep(0.4)
    if term is None:
        raise RuntimeError('execution_not_terminal:' + eid)
    code, logs = h.get('/api/executions/' + eid + '/logs')
    chunks = logs.get('logs') if isinstance(logs, dict) else logs
    stdout = ''
    if isinstance(chunks, list):
        for x in sorted((c for c in chunks if isinstance(c, dict) and c.get('stream') == 'stdout'),
                        key=lambda c: (c.get('seq', 0), c.get('created_at', ''))):
            stdout += x.get('chunk', '')
    return {'execution_id': eid, 'terminal': term, 'stdout': stdout}


# ------------------------------------------------------------------ agent DB
QUERIES = [
    ("SELECT key,value FROM identity ORDER BY key", "identity"),
    ("SELECT global_revision,last_sync FROM sync_state", "sync_state"),
    ("SELECT id,name,revision,sha256,length(content) FROM scripts ORDER BY id", "scripts"),
    ("SELECT id,revision,enabled,definition_json FROM tasks ORDER BY id", "tasks"),
    ("SELECT id,revision,definition_json FROM applications ORDER BY id", "applications"),
    ("SELECT sha256,path,downloaded_at FROM artifacts ORDER BY sha256", "artifacts"),
    ("SELECT id,status,exit_code,stdout,script_id,script_revision,node_id,trigger_type,synced,offline,"
     "start_time,end_time FROM executions ORDER BY start_time DESC LIMIT 30", "executions"),
    ("SELECT id,revision,enabled,definition_json FROM schedules ORDER BY id", "schedules"),
    ("SELECT id,application_id,phase,completed_kind,to_version FROM deployments ORDER BY started_at DESC LIMIT 10",
     "deployments"),
]

DB_CODE = r'''
import json,os,sqlite3,shutil,tempfile,hashlib,glob
p = __DBPATH__
res = {"db_path": p}
try:
    st = os.stat(p)
    res.update({"exists": True, "dev": st.st_dev, "inode": st.st_ino, "size": st.st_size,
                "uid": st.st_uid, "gid": st.st_gid, "mode": oct(st.st_mode & 0o777)})
except FileNotFoundError:
    res["exists"] = False
    print(json.dumps(res)); raise SystemExit
def open_con(path):
    con = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=1")
    return con
try:
    con = open_con(p)
    res["opened_mode"] = "ro"
except Exception as e:
    tmp = tempfile.mkdtemp()
    for suf in ("", "-wal", "-shm"):
        if os.path.exists(p + suf):
            shutil.copy2(p + suf, os.path.join(tmp, os.path.basename(p) + suf))
    con = sqlite3.connect(os.path.join(tmp, os.path.basename(p)))
    res["opened_mode"] = "copied:" + type(e).__name__
try:
    res["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
    res["tables"] = sorted(r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    for q, key in __QUERIES__:
        try:
            cur = con.execute(q)
            cols = [d[0] for d in cur.description] if cur.description else []
            res[key] = {"columns": cols, "rows": [list(r) for r in cur.fetchall()]}
        except Exception as e3:
            res[key] = {"error": type(e3).__name__}
finally:
    con.close()
files = []
for pat in (res["db_path"] + "*", os.path.join(os.path.dirname(res["db_path"]), "artifacts", "*")):
    for f in sorted(glob.glob(pat)):
        try:
            st2 = os.stat(f)
            files.append({"path": f, "dev": st2.st_dev, "inode": st2.st_ino, "size": st2.st_size,
                          "uid": st2.st_uid, "gid": st2.st_gid, "mode": oct(st2.st_mode & 0o777),
                          "sha256": hashlib.sha256(open(f, "rb").read()).hexdigest() if st2.st_size < 1048576 else None})
        except Exception as e4:
            files.append({"path": f, "error": type(e4).__name__})
res["files"] = files
print(json.dumps(res))
'''


def agent_db(dbpath=DB):
    code = DB_CODE.replace('__DBPATH__', json.dumps(dbpath)).replace('__QUERIES__', json.dumps(QUERIES))
    return rpy(NATIVE4, code.strip())


def db_view(summary):
    """Compact, evidence-friendly view of an agent DB summary."""
    if not summary or not summary.get('exists'):
        return {'exists': False}
    def r(k):
        v = summary.get(k)
        if not isinstance(v, dict):
            return None
        return v.get('rows')
    return {
        'exists': True, 'dev': summary.get('dev'), 'inode': summary.get('inode'),
        'quick_check': summary.get('quick_check'), 'opened_mode': summary.get('opened_mode'),
        'identity': r('identity'), 'sync_state': r('sync_state'), 'scripts': r('scripts'),
        'tasks': r('tasks'), 'applications': r('applications'), 'artifacts': r('artifacts'),
        'executions': r('executions'), 'schedules': r('schedules'), 'deployments': r('deployments'),
        'files': summary.get('files'),
    }


CONTAINER_PROOF_CODE = r'''
import json, os, stat, hashlib, glob
cid = __CID__
def sh(cmd):
    return os.popen(cmd).read().strip()
pid_raw = sh("docker inspect -f '{{.State.Pid}}' " + cid)
out = {"container_id": cid, "host_pid": pid_raw,
       "method": "host-side /proc/<pid> inspection on native-4 (image has no python3/coreutils)"}
try:
    pid = int(pid_raw)
except Exception:
    print(json.dumps({"error": "no_pid", "raw": pid_raw})); raise SystemExit
base = "/proc/%d" % pid
try:
    out["exe"] = os.readlink(base + "/exe")
    st = os.stat(base + "/exe")
    out["exe_sha256"] = hashlib.sha256(open(base + "/exe", "rb").read()).hexdigest()
    out["exe_dev_inode"] = [st.st_dev, st.st_ino]
except Exception as e:
    out["exe_error"] = type(e).__name__
try:
    for line in open(base + "/status"):
        if line.startswith("Uid:"):
            out["uid"] = line.split()[1]
            break
except Exception as e:
    out["status_error"] = type(e).__name__
try:
    out["cgroup"] = open(base + "/cgroup").read().strip()[:200]
except Exception as e:
    out["cgroup_error"] = type(e).__name__
try:
    out["cmdline"] = open(base + "/cmdline", "rb").read().decode("utf-8", "replace").replace("\x00", " ").strip()
except Exception:
    pass
fds = []
for fd in sorted(glob.glob(base + "/fd/*"), key=lambda p: int(p.rsplit("/", 1)[1])):
    try:
        tgt = os.readlink(fd)
    except Exception:
        continue
    item = {"fd": fd.rsplit("/", 1)[1], "target": tgt}
    try:
        st = os.stat(fd)
        item.update({"dev": st.st_dev, "inode": st.st_ino})
    except Exception:
        pass
    if tgt.endswith("state.db") or "/artifacts/" in tgt:
        fds.append(item)
out["db_and_cache_fds"] = fds
try:
    st = os.stat(base + "/root/var/lib/nodesteer/state.db")
    out["container_view_db"] = {"dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid,
                                "gid": st.st_gid, "mode": oct(st.st_mode & 0o777)}
except Exception as e:
    out["container_view_db_error"] = type(e).__name__
try:
    out["container_view_artifacts"] = sorted(os.listdir(base + "/root/var/lib/nodesteer/artifacts"))
except Exception as e:
    out["container_view_artifacts_error"] = type(e).__name__
out["dockerenv_present"] = os.path.exists(base + "/root/.dockerenv")
print(json.dumps(out))
'''


def container_process_proof(cid):
    """Host-side proof that the container's live agent process really owns the volume DB/cache."""
    code = CONTAINER_PROOF_CODE.replace('__CID__', json.dumps(cid))
    return rpy(NATIVE4, code.strip())


def sha256_file(path, timeout=90):
    rc, out, _ = n4('sha256sum ' + shlex.quote(path), timeout=timeout, check=False)
    return out.split()[0] if rc == 0 else None


# ------------------------------------------------------------------ browser
BROWSER_SESSION = OWNER + '-ui'


def ab(*args, stdin=None, timeout=180):
    cmd = ['agent-browser', '--session', BROWSER_SESSION] + [str(a) for a in args]
    cp = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def ab_value(out):
    i = out.find('{')
    j = out.find('[')
    if i < 0 or (0 <= j < i):
        i = j
    if i < 0:
        return out.strip()
    try:
        return json.loads(out[i:])
    except Exception:
        return out[i:]


def ab_js(code):
    out, err, rc = ab('eval', '--stdin', stdin=code)
    if rc != 0:
        raise RuntimeError('ab_eval_failed: %s%s' % ((err or '')[-300:], (out or '')[-300:]))
    return ab_value(out)


def ab_open(path, wait=2.0):
    ab('open', 'http://%s:%d%s' % (HOST, REQUIRED_PORT, path))
    time.sleep(wait)
    return ab('get', 'url')[0].strip()


def ab_login():
    """Submit the sign-in form with credentials injected through stdin JS."""
    url, cred = load_authority()
    if urllib.parse.urlsplit(url).port != REQUIRED_PORT:
        raise RuntimeError('endpoint_guard_failed_repair_18080_required')
    ab_open('/sign-in', wait=2.4)
    code = r"""
(() => {
  const u = %s, p = %s;
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const un = document.querySelector('input[name=username], input#username');
  const pw = document.querySelector('input[name=password], input#password');
  if (!un || !pw) return {ok:false, reason:'inputs-not-found'};
  setVal(un, u); setVal(pw, p);
  const btn = [...document.querySelectorAll('button')].find(b => /登录|Sign in|Log in|登陆/.test(b.innerText||''));
  if (!btn) return {ok:false, reason:'submit-not-found'};
  btn.click();
  return {ok:true};
})()
""" % (json.dumps(cred['admin_username']), json.dumps(cred['admin_password']))
    r = ab_js(code)
    for _ in range(40):
        time.sleep(0.4)
        if ab_js("localStorage.getItem('nodesteer_token') ? 'T' : 'N'") == 'T':
            break
    return {'submit': r, 'token_in_localStorage': bool(ab_js("!!localStorage.getItem('nodesteer_token')")),
            'url': ab('get', 'url')[0].strip()}


def ab_text():
    return ab_js('document.body.innerText') or ''


def ab_shot(name, full=True):
    EV.mkdir(parents=True, exist_ok=True)
    p = EV / name
    args = ['screenshot', str(p)]
    if full:
        args.append('--full')
    ab(*args)
    return 'evidence/' + name if p.exists() else ''
