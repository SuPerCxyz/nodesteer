"""Owned child Agent lifecycle on repair native-4 (192.168.100.247) for sys-b07.

Runs the exact repair Agent binary as a plain background process (no systemd
unit) under an owned 0700 root, with an owned 0600 config. It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-20260930-101945-native-4.service`.

The "Agent restart / disconnect" semantics required by sys-b07 are produced by
stopping / SIGKILLing / restarting this owned process (and its task process
group) only, never a protected Agent.
"""
import json
import subprocess
import time
from pathlib import Path

HOST = '192.168.100.247'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HOST]
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
PROTECTED_UNIT = 'nodesteer-agent-repair-20260930-101945-native-4.service'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip, token):
        self.root = '/var/lib/nodesteer-qa-sysb07-%s' % nonce
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.marks = self.root + '/marks'
        self.pidfile = self.marks + '/running.pid'
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
            "root=%r; cfg=%r; data=%r; log=%r; marks=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "os.makedirs(marks, mode=0o700, exist_ok=True)\n"
            "open(log,'a').close()\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.root, self.config, self.data, self.log, self.marks, yaml_text)
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
            "pids=[]\n"
            "for tok in q.stdout.split():\n"
            "    if tok.isdigit():\n"
            "        try:\n"
            "            if os.readlink('/proc/%%d/exe'%%int(tok))==agent: pids.append(int(tok))\n"
            "        except Exception: pass\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_pids':sorted(set(pids)),"
            "'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN)
        return self.remote_py(script)

    def stop(self):
        """Graceful stop of the owned child Agent (SIGTERM then SIGKILL)."""
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
            "def pids_of():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit(): continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "signalled=pids_of()\n"
            "for pid in signalled:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and pids_of(): time.sleep(0.5)\n"
            "left=pids_of()\n"
            "for pid in left: os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(0.8)\n"
            "print(json.dumps({'signalled':signalled,'forced_killed':left,'remaining':pids_of()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    def crash(self):
        """Simulate an uncontrolled Agent stop.

        SIGKILLs the owned child Agent main process AND kills the in-flight task
        process group recorded by the fixture script (so the old RUNNING
        Execution has no live process), leaving a RUNNING journal behind.
        """
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r; root=%r; pidfile=%r\n"
            "def agent_pids():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit(): continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "def alive(pid):\n"
            "    try:\n"
            "        os.kill(pid,0); return True\n"
            "    except Exception:\n"
            "        return False\n"
            "task_pids=[]\n"
            "if os.path.isfile(pidfile):\n"
            "    for line in open(pidfile):\n"
            "        line=line.strip()\n"
            "        if line.isdigit(): task_pids.append(int(line))\n"
            "task_alive_before=[p for p in task_pids if alive(p)]\n"
            "# SIGKILL the owned Agent FIRST so its process runner cannot observe the\n"
            "# task death and finalize the RUNNING row; the row must stay RUNNING.\n"
            "apids=agent_pids()\n"
            "for pid in apids: os.kill(pid, signal.SIGKILL)\n"
            "wait_end=time.time()+10\n"
            "while time.time()<wait_end and agent_pids(): time.sleep(0.2)\n"
            "# then reap the orphaned task process group (no live process left)\n"
            "killed=[]\n"
            "for pid in task_alive_before:\n"
            "    pgid=None\n"
            "    try: pgid=os.getpgid(pid)\n"
            "    except Exception: pass\n"
            "    if pgid is not None:\n"
            "        try: os.killpg(pgid, signal.SIGKILL)\n"
            "        except Exception: pass\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "    killed.append({'pid':pid,'pgid':pgid})\n"
            "time.sleep(1.0)\n"
            "p=subprocess.run(['pgrep','-af',root],capture_output=True,text=True)\n"
            "left=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "print(json.dumps({'agent_killed':apids,'agent_remaining':agent_pids(),"
            "'task_pids_recorded':task_pids,'task_alive_before':task_alive_before,"
            "'task_groups_killed':killed,'task_alive_after':[p for p in task_pids if alive(p)],"
            "'processes_under_owned_root_after':left}))\n"
        ) % (self.config, AGENT_BIN, self.root, self.pidfile)
        return self.remote_py(script)

    def restart(self, graceful=True):
        stopped = self.stop() if graceful else self.crash()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    # ---------- read-only local state ----------
    def journal_state(self, exec_ids, timeout=60):
        """Read the owned child Agent journal rows for the given Execution IDs."""
        script = (
            "import json,sqlite3,os\n"
            "data=%r; ids=%r\n"
            "db=os.path.join(data,'state.db')\n"
            "out={'db_path':db,'db_exists':os.path.isfile(db)}\n"
            "def row(c,eid):\n"
            "    r=c.execute('SELECT id,task_id,task_revision,script_id,script_revision,node_id,trigger_type,"
            "scheduled_time,status,exit_code,stdout,stderr,start_time,end_time,offline,synced,block_reason "
            "FROM executions WHERE id=?',(eid,)).fetchone()\n"
            "    if not r: return None\n"
            "    keys=['id','task_id','task_revision','script_id','script_revision','node_id','trigger_type',"
            "'scheduled_time','status','exit_code','stdout','stderr','start_time','end_time','offline','synced','block_reason']\n"
            "    d=dict(zip(keys,r))\n"
            "    d['stdout_tail']=(d.pop('stdout') or '')[-4000:]\n"
            "    d['stderr_tail']=(d.pop('stderr') or '')[-4000:]\n"
            "    return d\n"
            "try:\n"
            "    c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5); c.execute('PRAGMA query_only=ON')\n"
            "    out['db_quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]\n"
            "    out['rows']={eid:row(c,eid) for eid in ids}\n"
            "    out['total_executions']=c.execute('SELECT count(*) FROM executions').fetchone()[0]\n"
            "    out['running_count']=c.execute(\"SELECT count(*) FROM executions WHERE status='RUNNING'\").fetchone()[0]\n"
            "except Exception as e:\n"
            "    out['db_error']=type(e).__name__+':'+str(e)[:200]\n"
            "print(json.dumps(out))\n"
        ) % (self.data, list(exec_ids))
        return self.remote_py(script, timeout=timeout)

    def local_state(self, script_id, task_id, timeout=60):
        """Identity / revision / desired-definition readback from the owned child DB."""
        script = (
            "import json,sqlite3,os,hashlib\n"
            "data=%r; sid=%r; tid=%r\n"
            "db=os.path.join(data,'state.db')\n"
            "out={}\n"
            "def dbstat(p):\n"
            "    st=os.stat(p); return {'size':st.st_size,'dev':st.st_dev,'inode':st.st_ino}\n"
            "for n in ('state.db','state.db-wal','state.db-shm'):\n"
            "    p=os.path.join(data,n)\n"
            "    out[n]=dbstat(p) if os.path.isfile(p) else None\n"
            "try:\n"
            "    c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5); c.execute('PRAGMA query_only=ON')\n"
            "    ident={k:v for k,v in c.execute(\"SELECT key,value FROM identity\")}\n"
            "    if 'credential' in ident:\n"
            "        ident['credential_sha256']=hashlib.sha256((ident.pop('credential') or '').encode()).hexdigest()\n"
            "    out['identity']=ident\n"
            "    out['global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "    out['last_sync']=c.execute('SELECT last_sync FROM sync_state WHERE id=1').fetchone()[0]\n"
            "    s=c.execute('SELECT id,name,content,revision,enabled,sha256 FROM scripts WHERE id=?',(sid,)).fetchone()\n"
            "    out['script']=None if not s else {'id':s[0],'name':s[1],'content_sha256':hashlib.sha256((s[2] or '').encode()).hexdigest(),"
            "'content':(s[2] or ''),'revision':s[3],'enabled':s[4],'sha256_field':s[5]}\n"
            "    t=c.execute('SELECT id,definition_json,revision,enabled FROM tasks WHERE id=?',(tid,)).fetchone()\n"
            "    out['task']=None if not t else {'id':t[0],'definition':json.loads(t[1]),'revision':t[2],'enabled':t[3]}\n"
            "    out['scripts_total']=c.execute('SELECT count(*) FROM scripts').fetchone()[0]\n"
            "    out['tasks_total']=c.execute('SELECT count(*) FROM tasks').fetchone()[0]\n"
            "    out['exec_count']=c.execute('SELECT count(*) FROM executions').fetchone()[0]\n"
            "except Exception as e:\n"
            "    out['db_error']=type(e).__name__+':'+str(e)[:200]\n"
            "print(json.dumps(out))\n"
        ) % (self.data, script_id, task_id)
        return self.remote_py(script, timeout=timeout)

    def marks_state(self, timeout=40):
        """Owned mark directory contents (real script side effects), no secrets."""
        script = (
            "import json,os\n"
            "marks=%r\n"
            "out={'dir':marks,'exists':os.path.isdir(marks),'files':{}}\n"
            "if os.path.isdir(marks):\n"
            "    for n in sorted(os.listdir(marks)):\n"
            "        p=os.path.join(marks,n)\n"
            "        if os.path.isfile(p):\n"
            "            out['files'][n]=open(p,'r',errors='replace').read()[-4000:]\n"
            "print(json.dumps(out))\n"
        ) % (self.marks,)
        return self.remote_py(script, timeout=timeout)

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
            "print(json.dumps({'size':os.path.getsize(p) if os.path.isfile(p) else 0,"
            "'tail':data.decode('utf-8','replace')}))\n"
        ) % (self.log, max_bytes, max_bytes)
        return self.remote_py(script)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n"
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit(): continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception: continue\n"
            "        if exe==agent and cfg.encode() in args: out.append(pid)\n"
            "    return out\n"
            "pids=matched()\n"
            "for pid in pids: os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched(): time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left: os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(0.8)\n"
            "left=matched()\n"
            "p=subprocess.run(['pgrep','-af',root],capture_output=True,text=True)\n"
            "under=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "for line in under:\n"
            "    tok=line.split(None,1)[0]\n"
            "    if tok.isdigit():\n"
            "        try: os.kill(int(tok), signal.SIGKILL)\n"
            "        except Exception: pass\n"
            "time.sleep(0.5)\n"
            "existed=os.path.isdir(root)\n"
            "if os.path.isdir(root): shutil.rmtree(root)\n"
            "print(json.dumps({'agent_signalled':pids,'agent_remaining':left,"
            "'processes_under_root_before_rmtree':under,'root_removed':existed,"
            "'root_exists_after':os.path.exists(root)}))\n"
        ) % (self.root, self.config, AGENT_BIN)
        return self.remote_py(script, timeout=120)
