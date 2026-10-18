"""Owned child Agent lifecycle on repair native-2 (192.168.100.206) for sys-b10.

Runs the exact repair Agent binary as a plain background process (no systemd
unit) under an owned 0700 root, with an owned 0600 config. It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-20260930-101945-native-2.service`.

native-2 is only used for this owned process; it is NOT shared with a protected
unit start/stop.
"""
import json
import subprocess
import time
from pathlib import Path

HOST = '192.168.100.206'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HOST]
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
PROTECTED_UNIT = 'nodesteer-agent-repair-20260930-101945-native-2.service'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip, token):
        self.root = '/var/lib/nodesteer-qa-sysb10-%s' % nonce
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = proxy_port
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token
        self.operator_ip = '192.168.100.170'

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=60):
        cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                            text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def remote_py(self, script, timeout=90):
        rc, out, err = self._ssh(script, timeout=timeout)
        if rc != 0:
            raise RuntimeError('remote_py_failed: %s' % (err[-400:] or out[-400:]))
        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError('remote_py_empty_output')
        return json.loads(lines[-1])

    # ---------- guards ----------
    def inventory(self):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['agent_process_table']=lines\n"
            "out['owned_child_processes']=[l for l in lines if %r in l]\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',"
            "'nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],capture_output=True,text=True)\n"
            "out['repair_agent_unit']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.config, AGENT_BIN, PROTECTED_UNIT)
        return self.remote_py(script)

    # ---------- install / start / stop ----------
    def write_config_and_start(self):
        yaml_text = (
            'hub_url: "ws://%s:%d"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "native"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 2\n'
            'revision_check_sec: 2\n'
            'max_log_bytes: 262144\n'
        ) % (self.operator_ip, self.proxy_port, self._token, self.agent_id,
             self.node_name, self.node_ip, self.data)
        prep = (
            "import os\n"
            "root=%r; cfg=%r; data=%r; log=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "open(log,'a').close()\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.root, self.config, self.data, self.log, yaml_text)
        cp = subprocess.run(SSH + ['python3', '-'], input=prep,
                            capture_output=True, text=True, timeout=60)
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
            "time.sleep(1.5)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN)
        return self.remote_py(script)

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
            "p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "pids=[int(x) for x in p.stdout.split() if x.strip().isdigit()]\n"
            "for pid in pids:\n"
            "    try:\n"
            "        exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "        args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "    except Exception:\n"
            "        continue\n"
            "    if exe!=agent or cfg.encode() not in args:\n"
            "        raise SystemExit('child_stop_identity_guard_failed')\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "time.sleep(2)\n"
            "r=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'signalled':pids,'remaining':[l for l in r.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    # ---------- read-only artifact / cache inspection ----------
    def artifact_state(self, shas, timeout=60):
        """Physical + DB state of the owned agent artifact cache for given SHAs."""
        script = (
            "import json,sqlite3,os,hashlib\n"
            "data=%r; shas=%r\n"
            "adir=os.path.join(data,'artifacts')\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "out={'artifacts_dir':adir,'dir_exists':os.path.isdir(adir)}\n"
            "entries=[]\n"
            "if os.path.isdir(adir):\n"
            "    for n in sorted(os.listdir(adir)):\n"
            "        p=os.path.join(adir,n)\n"
            "        try: st=os.stat(p)\n"
            "        except Exception: continue\n"
            "        entries.append({'name':n,'size':st.st_size,'sha256':sha(p) if os.path.isfile(p) else None,'mtime':st.st_mtime})\n"
            "out['all_dir_entries']=entries\n"
            "out['tmp_entries']=[e for e in entries if e['name'].endswith('.tmp')]\n"
            "out['watched']={}\n"
            "for s in shas:\n"
            "    hits=[e for e in entries if e['name']==s]\n"
            "    out['watched'][s]={'file_present':bool(hits),'file_sha_matches':bool(hits and hits[0]['sha256']==s),"
            "        'file_sha':hits[0]['sha256'] if hits else None,'file_size':hits[0]['size'] if hits else None,"
            "        'file_mtime':hits[0]['mtime'] if hits else None,\n"
            "        'tmp_present':any(e['name']==s+'.tmp' for e in entries)}\n"
            "db=os.path.join(data,'state.db')\n"
            "try:\n"
            "    c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5); c.execute('PRAGMA query_only=ON')\n"
            "    out['db_quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]\n"
            "    out['db_rows']={s:bool(c.execute('SELECT count(*) FROM artifacts WHERE sha256=?',(s,)).fetchone()[0]) for s in shas}\n"
            "    out['db_all_artifact_rows']=[{'sha256':r[0],'path':r[1],'downloaded_at':r[2]} for r in c.execute('SELECT sha256,path,downloaded_at FROM artifacts')]\n"
            "    out['db_artifact_count']=c.execute('SELECT count(*) FROM artifacts').fetchone()[0]\n"
            "    out['local_apps']=[{'id':r[0],'name':(json.loads(r[1]).get('name') if r[1] else None),'revision':r[2]} for r in c.execute('SELECT id,definition_json,revision FROM applications')]\n"
            "    out['identity']={k:v for k,v in c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\")}\n"
            "    out['global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "except Exception as e:\n"
            "    out['db_error']=type(e).__name__+':'+str(e)[:200]\n"
            "print(json.dumps(out))\n"
        ) % (self.data, list(shas))
        return self.remote_py(script, timeout=timeout)

    def poll_for_file(self, name, present=True, timeout=20, interval=0.3):
        """Poll the owned artifacts dir until `name` appears (or disappears)."""
        script = (
            "import json,os,time\n"
            "adir=os.path.join(%r,'artifacts'); name=%r; want=%r; deadline=time.time()+%r; start=time.time()\n"
            "while time.time()<deadline:\n"
            "    p=os.path.join(adir,name)\n"
            "    exists=os.path.exists(p)\n"
            "    if exists==want:\n"
            "        print(json.dumps({'reached':True,'present':exists,'waited_s':round(time.time()-start,2),"
            "'size':os.path.getsize(p) if exists else None})); raise SystemExit\n"
            "    time.sleep(%r)\n"
            "print(json.dumps({'reached':False,'present':os.path.exists(os.path.join(adir,name))}))\n"
        ) % (self.data, name, present, timeout, interval)
        return self.remote_py(script, timeout=int(timeout) + 30)

    def log_tail(self, max_bytes=20000):
        script = (
            "import json,os\n"
            "p=%r\n"
            "data=b''\n"
            "if os.path.isfile(p):\n"
            "    sz=os.path.getsize(p)\n"
            "    with open(p,'rb') as f:\n"
            "        if sz>%r: f.seek(sz-%r)\n"
            "        data=f.read()\n"
            "print(json.dumps({'size':os.path.getsize(p) if os.path.isfile(p) else 0,'tail':data.decode('utf-8','replace')}))\n"
        ) % (self.log, max_bytes, max_bytes)
        return self.remote_py(script)

    # ---------- fault-injection HTTP server (runs on native-2) ----------
    def write_remote_text(self, remote_path, text, mode=0o600, timeout=60):
        prep = (
            "import os\n"
            "root=%r; path=%r; body=%r; mode=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True)\n"
            "fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, mode)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('REMOTE_TEXT_WRITTEN')\n"
        ) % (self.root, remote_path, text, mode)
        cp = subprocess.run(SSH + ['python3', '-'], input=prep, capture_output=True, text=True, timeout=timeout)
        if cp.returncode != 0 or 'REMOTE_TEXT_WRITTEN' not in cp.stdout:
            raise RuntimeError('remote_text_write_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
        return True

    def start_artifact_server(self, server_src, port, timeout=60):
        remote_path = self.root + '/artifact_server.py'
        prep = (
            "import os\n"
            "root=%r; path=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True)\n"
            "fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('SERVER_WRITTEN')\n"
        ) % (self.root, remote_path, server_src)
        cp = subprocess.run(SSH + ['python3', '-'], input=prep, capture_output=True, text=True, timeout=timeout)
        if cp.returncode != 0 or 'SERVER_WRITTEN' not in cp.stdout:
            raise RuntimeError('artifact_server_write_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
        script = (
            "import json,os,subprocess,time\n"
            "path=%r; log=%r; port=%r\n"
            "p=subprocess.Popen(['setsid','nohup','python3',path,'--port',str(port)],\n"
            "                   stdout=open(log,'a'), stderr=subprocess.STDOUT,\n"
            "                   stdin=subprocess.DEVNULL, start_new_session=True)\n"
            "time.sleep(1.2)\n"
            "q=subprocess.run(['pgrep','-af',path],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (remote_path, self.root + '/artifact_server.log', port)
        return self.remote_py(script, timeout=timeout)

    def stop_artifact_server(self, timeout=60):
        remote_path = self.root + '/artifact_server.py'
        script = (
            "import json,os,signal,subprocess,time\n"
            "path=%r\n"
            "p=subprocess.run(['pgrep','-f',path],capture_output=True,text=True)\n"
            "pids=[int(x) for x in p.stdout.split() if x.strip().isdigit()]\n"
            "for pid in pids:\n"
            "    try: os.kill(pid, signal.SIGTERM)\n"
            "    except Exception: pass\n"
            "time.sleep(1.5)\n"
            "p2=subprocess.run(['pgrep','-f',path],capture_output=True,text=True)\n"
            "left=[int(x) for x in p2.stdout.split() if x.strip().isdigit()]\n"
            "for pid in left:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.5)\n"
            "p3=subprocess.run(['pgrep','-f',path],capture_output=True,text=True)\n"
            "print(json.dumps({'signalled':pids,'remaining':[l for l in p3.stdout.splitlines() if l.strip()]}))\n"
        ) % (remote_path,)
        return self.remote_py(script, timeout=timeout)

    # ---------- protected-node / real cache read-only baseline ----------
    def real_cache_baseline(self, probe_paths):
        script = (
            "import json,os,hashlib\n"
            "paths=%r\n"
            "out={}\n"
            "for p in paths:\n"
            "    e={}\n"
            "    if os.path.isdir(p):\n"
            "        for n in sorted(os.listdir(p)):\n"
            "            fp=os.path.join(p,n)\n"
            "            if os.path.isfile(fp):\n"
            "                e[n]={'size':os.path.getsize(fp),'sha256':hashlib.sha256(open(fp,'rb').read()).hexdigest()}\n"
            "    out[p]=e\n"
            "print(json.dumps(out))\n"
        ) % (list(probe_paths),)
        return self.remote_py(script)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r; srv=%r\n"
            "def matched(pattern):\n"
            "    p=subprocess.run(['pgrep','-f',pattern],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit():\n"
            "            continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception:\n"
            "            continue\n"
            "        if exe==agent and cfg.encode() in args:\n"
            "            out.append(pid)\n"
            "    return out\n"
            "pids=matched(cfg)\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched(cfg):\n"
            "    time.sleep(0.5)\n"
            "left=matched(cfg)\n"
            "for pid in left:\n"
            "    os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(0.5)\n"
            "left=matched(cfg)\n"
            "sp=subprocess.run(['pgrep','-f',srv],capture_output=True,text=True)\n"
            "spids=[int(x) for x in sp.stdout.split() if x.strip().isdigit()]\n"
            "for pid in spids:\n"
            "    try: os.kill(pid, signal.SIGTERM)\n"
            "    except Exception: pass\n"
            "time.sleep(1)\n"
            "sp=subprocess.run(['pgrep','-f',srv],capture_output=True,text=True)\n"
            "sleft=[int(x) for x in sp.stdout.split() if x.strip().isdigit()]\n"
            "for pid in sleft:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "existed=os.path.isdir(root)\n"
            "if os.path.isdir(root):\n"
            "    shutil.rmtree(root)\n"
            "print(json.dumps({'agent_signalled':pids,'agent_remaining':left,'server_signalled':spids,"
            "'root_removed':existed,'root_exists_after':os.path.exists(root),"
            "'server_processes_after':[l for l in subprocess.run(['pgrep','-af',srv],capture_output=True,text=True).stdout.splitlines() if l.strip()]}))\n"
        ) % (self.root, self.config, AGENT_BIN, self.root + '/artifact_server.py')
        return self.remote_py(script, timeout=120)
