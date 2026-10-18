#!/usr/bin/env python3
"""p-web batch driver: repair authority :18080 fixtures + owned child agent(s) on native-3.

Hard guard: endpoint is the repair authority only. Credentials come from the
0600 file and are never printed or persisted.

Subcommands:
  preflight              baseline counts + protected-node state + native-3 agent state
  child-up <mode> [n]    enroll + start owned child agent(s) on native-3 (native|docker)
  child-inv              node-side inventory (owned process/root/config presence)
  child-stop             stop owned child agent process(es) (keeps node record)
  child-set <status>     set owned child node record status (UI-equivalent, API)
  child-rm               remove owned node record(s) + node-side root/processes
  guard                  final counts + node-side cleanup verification
  api-create <kind> ...  create a fixture via API
  api-delete <kind> <id> delete a fixture via API
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EV = BATCH / "evidence"
NONCE = (BATCH / ".nonce").read_text().strip()
PREFIX = "qa-pweb-%s" % NONCE
BASE = "http://192.168.100.209:18080"
CRED = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json")
STATE_PATH = BATCH / "state.json"

NATIVE3_IP = "192.168.100.222"
NODE_NATIVE3 = "37989297-3bc8-4c0c-b302-7c6efbdba630"
PROTECTED = {
    "91f44866-8a28-435e-bbda-2f2367126c00": "native-1",
    "611c02a5-1859-4465-878b-d543b7f6fdec": "native-2",
    "37989297-3bc8-4c0c-b302-7c6efbdba630": "native-3",
    "03cb50cc-eab6-4afd-bbe0-f73c4e326773": "native-4",
}
AGENT_BIN = "/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
NATIVE3_UNIT = "nodesteer-agent-repair-20260930-101945-native-3.service"
SSH = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes",
       "-o", "ConnectTimeout=10", "root@" + NATIVE3_IP]

os.umask(0o077)


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ api
def token():
    c = json.load(open(CRED))
    st, raw = _call("POST", "/api/login", body={"username": c["admin_username"],
                                                "password": c["admin_password"]})
    if st != 200:
        raise RuntimeError("login failed %s" % st)
    return json.loads(raw)["token"]


TOK = None


def tok():
    global TOK
    if TOK is None:
        TOK = token()
    return TOK


def _call(method, path, body=None, raw_body=None, headers=None):
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None and raw_body is None:
        req.add_header("Content-Type", "application/json")
    if path != "/api/login":
        req.add_header("Authorization", "Bearer " + tok())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def j(method, path, body=None):
    st, raw = _call(method, path, body=body)
    try:
        return st, json.loads(raw)
    except Exception:
        return st, raw.decode(errors="replace")


def lst(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def counts():
    out = {}
    for ep, key in [("/api/nodes", "nodes"), ("/api/applications", "applications"),
                    ("/api/tasks", "tasks"), ("/api/schedules", "schedules"),
                    ("/api/scripts", "scripts"), ("/api/artifacts", "artifacts"),
                    ("/api/transfers", "transfers"), ("/api/users", "users")]:
        st, p = j("GET", ep)
        out[key] = len(lst(p))
    st, ex = j("GET", "/api/executions?limit=1")
    out["executions_total"] = ex.get("total") if isinstance(ex, dict) else None
    return out


def mine(kind_ep):
    st, p = j("GET", kind_ep)
    return [x for x in lst(p) if str(x.get("name", x.get("hostname", ""))).startswith(PREFIX)]


# ------------------------------------------------------------------ node side
def ssh_py(script, timeout=120):
    cp = subprocess.run(SSH + ["python3", "-"], input=script, capture_output=True,
                        text=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError("ssh_py_failed: %s" % (cp.stderr[-400:] or cp.stdout[-400:]))
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    if not lines:
        raise RuntimeError("ssh_py_empty")
    return json.loads(lines[-1])


class Child:
    def __init__(self, idx=1):
        self.idx = idx
        self.root = "/var/lib/nodesteer-%s-child%d" % (PREFIX, idx)
        self.config = self.root + "/child.yaml"
        self.data = self.root + "/data"
        self.log = self.root + "/child.log"
        self.marker = "/tmp/%s-marker-%d.txt" % (PREFIX, idx)

    def inventory(self):
        return ssh_py((
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r),'marker_exists':os.path.exists(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "out['owned_processes']=[l.split('--config')[0].strip() for l in p.stdout.splitlines() if %r in l]\n"
            "out['owned_process_count']=len(out['owned_processes'])\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],capture_output=True,text=True)\n"
            "out['protected_native3_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "b=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID','nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in b.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.marker, self.config, AGENT_BIN, NATIVE3_UNIT))

    def write_and_start(self, node_id, agent_id, node_name, reg_token, mode, node_ip):
        yaml_text = (
            'hub_url: "ws://192.168.100.209:18080"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 2\n'
            'revision_check_sec: 2\n'
            'max_log_bytes: 262144\n'
        ) % (reg_token, agent_id, node_name, node_ip, mode, self.data)
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
        cp = subprocess.run(SSH + ["python3", "-"], input=prep, capture_output=True, text=True, timeout=60)
        if cp.returncode != 0 or "CONFIG_WRITTEN" not in cp.stdout:
            raise RuntimeError("child_config_write_failed: %s" % (cp.stderr[-300:] or cp.stdout[-300:]))
        return self.start()

    def start(self):
        return ssh_py((
            "import json,os,subprocess,time\n"
            "cfg=%r; log=%r; agent=%r\n"
            "env=dict(os.environ)\n"
            "for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'): env.pop(k,None)\n"
            "env['NO_PROXY']='*'; env['no_proxy']='*'\n"
            "p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],stdout=open(log,'a'),stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True,env=env)\n"
            "time.sleep(2.0)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l.split('--config')[0].strip() for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN))

    def stop(self):
        return ssh_py((
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
            "def m():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for t in p.stdout.split():\n"
            "        if not t.isdigit(): continue\n"
            "        pid=int(t)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid); args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "pids=m()\n"
            "for pid in pids: os.kill(pid,signal.SIGTERM)\n"
            "end=time.time()+10\n"
            "while time.time()<end and m(): time.sleep(0.4)\n"
            "left=m()\n"
            "for pid in left:\n"
            "    try: os.kill(pid,signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.8)\n"
            "print(json.dumps({'signalled':pids,'remaining':m()}))\n"
        ) % (self.config, AGENT_BIN))

    def remove(self):
        return ssh_py((
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r; mk=%r\n"
            "def m():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for t in p.stdout.split():\n"
            "        if not t.isdigit(): continue\n"
            "        pid=int(t)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid); args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "pids=m()\n"
            "for pid in pids:\n"
            "    try: os.kill(pid,signal.SIGTERM)\n"
            "    except Exception: pass\n"
            "end=time.time()+10\n"
            "while time.time()<end and m(): time.sleep(0.4)\n"
            "left=m()\n"
            "for pid in left:\n"
            "    try: os.kill(pid,signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.6)\n"
            "existed=os.path.isdir(root)\n"
            "if existed: shutil.rmtree(root)\n"
            "mkex=os.path.exists(mk)\n"
            "if mkex: os.unlink(mk)\n"
            "print(json.dumps({'signalled':pids,'remaining':m(),'root_removed':existed,'root_exists_after':os.path.exists(root),'marker_exists_after':os.path.exists(mk)}))\n"
        ) % (self.root, self.config, AGENT_BIN, self.marker), timeout=120)

    def log_tail(self, max_bytes=3000):
        return ssh_py((
            "import json,os\n"
            "p=%r\nout={'size':0,'tail':''}\n"
            "if os.path.isfile(p):\n"
            "    sz=os.path.getsize(p); out['size']=sz\n"
            "    with open(p,'rb') as f:\n"
            "        if sz>%r: f.seek(sz-%r)\n"
            "        out['tail']=f.read().decode('utf-8','replace')\n"
            "print(json.dumps(out))\n"
        ) % (self.log, max_bytes, max_bytes))

    def write_marker(self, text):
        return ssh_py((
            "import json\n"
            "open(%r,'w').write(%r)\n"
            "print(json.dumps({'ok':True}))\n"
        ) % (self.marker, text))


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def save_state(st):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE_PATH)


def rec(name, obj):
    EV.mkdir(parents=True, exist_ok=True)
    (EV / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    return "evidence/" + name


# ------------------------------------------------------------------ commands
def preflight():
    out = {"at": now(), "authority": BASE, "nonce": NONCE, "prefix": PREFIX}
    out["health"] = {p: _raw_get(p) for p in ("/healthz", "/readyz")}
    out["counts"] = counts()
    st, nodes = j("GET", "/api/nodes")
    out["protected_nodes"] = {n["hostname"]: n["status"] for n in lst(nodes)
                              if n["id"] in PROTECTED}
    out["other_actors_nodes"] = [{"hostname": n["hostname"], "status": n["status"]}
                                 for n in lst(nodes) if n["id"] not in PROTECTED]
    out["owned_leftovers"] = {k: len(mine(ep)) for k, ep in
                              [("nodes", "/api/nodes"), ("scripts", "/api/scripts"),
                               ("tasks", "/api/tasks"), ("schedules", "/api/schedules"),
                               ("applications", "/api/applications"), ("artifacts", "/api/artifacts")]}
    out["node_side"] = Child(1).inventory()
    out["node_side"]["expected_agent_sha"] = AGENT_SHA
    out["node_side_sha_ok"] = out["node_side"]["agent_binary_sha256"] == AGENT_SHA
    print(json.dumps(out, ensure_ascii=False, indent=1))
    rec("preflight.json", out)


def _raw_get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status
    except Exception as e:
        return "ERR:%s" % e


def _enroll_one(i, mode):
    c = json.load(open(CRED))
    st = load_state()
    st.setdefault("children", [])
    name = "%s-child%d" % (PREFIX, i)
    node_ip = "%s%d" % (NATIVE3_IP.rsplit(".", 1)[0] + ".", 220 + i)
    Child(i).remove()
    code, enr = j("POST", "/api/nodes/enrollment",
                  {"node_name": name, "node_ip": node_ip, "hub_address": BASE})
    if code != 200 or not isinstance(enr, dict):
        raise RuntimeError("enrollment_failed %s %s" % (code, enr))
    child = Child(i)
    started = child.write_and_start(enr["node_id"], enr["agent_id"], name,
                                    c["registration_token"], mode, node_ip)
    online = None
    for _ in range(30):
        code, nd = j("GET", "/api/nodes/" + enr["node_id"])
        if isinstance(nd, dict):
            online = nd.get("status")
            if online == "online":
                break
        time.sleep(1)
    rec("child%d-up-%s.json" % (i, mode), {"node_id": enr["node_id"], "agent_id": enr["agent_id"],
                                           "name": name, "mode": mode, "node_ip": node_ip,
                                           "status": online, "root": child.root,
                                           "started": started, "log_tail": child.log_tail(1200)})
    entry = {"idx": i, "node_id": enr["node_id"], "agent_id": enr["agent_id"],
             "name": name, "mode": mode, "node_ip": node_ip, "status": online}
    st["children"] = [x for x in st["children"] if x["idx"] != i] + [entry]
    st["children"] = sorted(st["children"], key=lambda x: x["idx"])
    save_state(st)
    print(name, enr["node_id"], mode, node_ip, online)
    return entry


def child_up(mode="native", n=1):
    for i in range(1, int(n) + 1):
        _enroll_one(i, mode)


def child_add(idx, mode="native"):
    _enroll_one(int(idx), mode)


def child_inv():
    st = load_state()
    out = {}
    for c in st.get("children", []):
        out[c["name"]] = Child(c["idx"]).inventory()
    out["_state"] = st.get("children")
    print(json.dumps(out, ensure_ascii=False, indent=1))


def child_stop():
    st = load_state()
    out = {}
    for c in st.get("children", []):
        out[c["name"]] = Child(c["idx"]).stop()
    out["_recorded_child_count"] = len(st.get("children", []))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    rec("children-stopped.json", out)
    return out


def child_set(status):
    st = load_state()
    out = {}
    for c in st.get("children", []):
        # status is normally derived from the live session; only the API record is writable
        code, body = _call("POST", "/api/nodes/" + c["node_id"] + "/status",
                           body={"status": status})
        out[c["name"]] = {"http": code, "body": body[:120].decode(errors="replace")}
    print(json.dumps(out, ensure_ascii=False, indent=1))


def child_rm():
    st = load_state()
    out = {}
    for c in st.get("children", []):
        node_side = Child(c["idx"]).remove()
        code, body = _call("DELETE", "/api/nodes/" + c["node_id"])
        chk, _ = _call("GET", "/api/nodes/" + c["node_id"])
        out[c["name"]] = {"node_side": node_side, "delete_http": code,
                          "delete_body": body[:120].decode(errors="replace"),
                          "get_after": chk}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    rec("children-removed.json", out)
    return out


def guard():
    out = {"at": now(), "counts": counts()}
    st, nodes = j("GET", "/api/nodes")
    out["protected_nodes"] = {n["hostname"]: n["status"] for n in lst(nodes) if n["id"] in PROTECTED}
    out["owned_leftovers"] = {k: [x.get("name", x.get("hostname")) for x in mine(ep)]
                              for k, ep in [("nodes", "/api/nodes"), ("scripts", "/api/scripts"),
                                            ("tasks", "/api/tasks"), ("schedules", "/api/schedules"),
                                            ("applications", "/api/applications"),
                                            ("artifacts", "/api/artifacts")]}
    stt = load_state()
    out["node_side"] = {c["name"]: Child(c["idx"]).inventory() for c in stt.get("children", [])}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    rec("cleanup-guard.json", out)


def main():
    cmd = sys.argv[1]
    if cmd == "preflight":
        preflight()
    elif cmd == "child-up":
        child_up(sys.argv[2] if len(sys.argv) > 2 else "native",
                 sys.argv[3] if len(sys.argv) > 3 else 1)
    elif cmd == "child-add":
        child_add(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "native")
    elif cmd == "child-inv":
        child_inv()
    elif cmd == "child-stop":
        child_stop()
    elif cmd == "child-set":
        child_set(sys.argv[2])
    elif cmd == "child-rm":
        child_rm()
    elif cmd == "guard":
        guard()
    else:
        raise SystemExit("unknown command " + cmd)


if __name__ == "__main__":
    main()
