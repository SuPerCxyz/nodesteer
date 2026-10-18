"""Owned child Agent lifecycle on repair native-1 (192.168.100.204) for sys-b01.

Runs the exact repair Agent binary as a plain background process (no systemd
unit) under an owned 0700 root with an owned 0600 config.  It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-*-native-1.service`, and
the agent's local store is only ever read (`mode=ro`, `PRAGMA query_only=ON`).
"""
import json
import subprocess
import time
from pathlib import Path

BATCH = 'sysb01'
HOST = '192.168.100.204'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HOST]
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
REPAIR_UNIT = 'nodesteer-agent-repair-20260930-101945-native-1.service'
OPERATOR_IP = '192.168.100.170'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip, token):
        self.root = '/var/lib/nodesteer-qa-%s-%s' % (BATCH, nonce)
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = proxy_port
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token
        self.operator_ip = OPERATOR_IP

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=60):
        cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                            text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def _last_json(self, out):
        lines = [l for l in out.splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None

    # ---------- guards / inventory ----------
    def inventory(self):
        script = (
            "import json,os,subprocess,hashlib,glob\n"
            "def sha(p):\n"
            "    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['agent_process_table']=lines\n"
            "out['owned_child_processes']=[l for l in lines if %r in l]\n"
            "out['other_qa_child_processes']=[l for l in lines if '/var/lib/nodesteer-qa-' in l and %r not in l]\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "out['owned_root_glob']=sorted(glob.glob('/var/lib/nodesteer-qa-%s-*'))\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',"
            "'nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],capture_output=True,text=True)\n"
            "out['repair_agent_unit']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.config, self.config, AGENT_BIN, BATCH, REPAIR_UNIT)
        rc, out, err = self._ssh(script)
        if rc != 0:
            raise RuntimeError('child_inventory_failed: %s' % err[-300:])
        return self._last_json(out)

    # ---------- install / start / stop ----------
    def write_config_and_start(self):
        """Write the owned 0600 config (token embedded in the SSH program payload,
        never in argv / never on the operator disk) and start the child."""
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
        ) % (self.operator_ip, self.proxy_port, self._token, self.agent_id,
             self.node_name, self.node_ip, self.data)
        prep = (
            "import os\n"
            "root=%r; cfg=%r; data=%r; log=%r; body=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            "if os.path.exists(log):\n"
            "    os.remove(log)\n"
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
            "p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],\n"
            "                   stdout=open(log,'a'), stderr=subprocess.STDOUT,\n"
            "                   stdin=subprocess.DEVNULL, start_new_session=True)\n"
            "time.sleep(1.5)\n"
            "q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)\n"
            "print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))\n"
        ) % (self.config, self.log, AGENT_BIN)
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_start_failed: %s' % err[-300:])
        return self._last_json(out)

    def _pids_guard(self):
        return (
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit():\n"
            "            continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%d/exe' % pid)\n"
            "            args=open('/proc/%d/cmdline' % pid,'rb').read().split(b'\\0')\n"
            "        except Exception:\n"
            "            continue\n"
            "        if exe==agent and cfg.encode() in args:\n"
            "            out.append(pid)\n"
            "    return out\n"
        )

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" % (self.config, AGENT_BIN) +
            self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched():\n"
            "    time.sleep(0.5)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n"
        )
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_stop_failed: %s' % err[-300:])
        return self._last_json(out)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    # ---------- read-only local store inspection ----------
    def db_read(self, task_id, exec_ids, script_id='', task_ids=None):
        """Read-only snapshot of the owned child Agent store."""
        db = self.data + '/state.db'
        ids = list(task_ids) if task_ids else ([task_id] if task_id else [])
        script = (
            "import json,sqlite3\n"
            "db=%r; ids=%r; task_id=%r; script_id=%r; task_ids=%r\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "out={'quick_check':c.execute('PRAGMA quick_check').fetchone()[0]}\n"
            "out['db_opened_readonly']=True\n"
            "out['identity']={k:v for k,v in c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\")}\n"
            "out['local_global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "out['last_sync']=c.execute('SELECT last_sync FROM sync_state WHERE id=1').fetchone()[0]\n"
            "out['scripts_total']=c.execute('SELECT count(*) FROM scripts').fetchone()[0]\n"
            "out['tasks_total']=c.execute('SELECT count(*) FROM tasks').fetchone()[0]\n"
            "out['schedules_total']=c.execute('SELECT count(*) FROM schedules').fetchone()[0]\n"
            "out['executions_total']=c.execute('SELECT count(*) FROM executions').fetchone()[0]\n"
            "if script_id:\n"
            "    r=c.execute('SELECT id,name,revision,sha256,length(content),enabled FROM scripts WHERE id=?',(script_id,)).fetchone()\n"
            "    out['owned_script']=None if r is None else {'id':r[0],'name':r[1],'revision':r[2],'sha256':r[3],'content_len':r[4],'enabled':bool(r[5])}\n"
            "else:\n"
            "    out['owned_script']=None\n"
            "out['owned_tasks']=[]\n"
            "for tid in task_ids:\n"
            "    r=c.execute('SELECT id,revision,enabled,length(definition_json) FROM tasks WHERE id=?',(tid,)).fetchone()\n"
            "    out['owned_tasks'].append(None if r is None else {'id':r[0],'revision':r[1],'enabled':bool(r[2]),'definition_len':r[3]})\n"
            "out['owned_task']=out['owned_tasks'][0] if out['owned_tasks'] else None\n"
            "rows=[]\n"
            "for eid in ids:\n"
            "    r=c.execute('SELECT id,task_id,node_id,status,trigger_type,scheduled_time,offline,synced,exit_code FROM executions WHERE id=?',(eid,)).fetchone()\n"
            "    rows.append(None if r is None else {'id':r[0],'task_id':r[1],'node_id':r[2],'status':r[3],'trigger_type':r[4],'scheduled_time':r[5],'offline':bool(r[6]),'synced':bool(r[7]),'exit_code':r[8]})\n"
            "out['requested_executions']=rows\n"
            "if task_id:\n"
            "    out['task_executions']=[{'id':r[0],'status':r[1],'trigger_type':r[2],'scheduled_time':r[3],"
            "'offline':bool(r[4]),'synced':bool(r[5]),'exit_code':r[6]} for r in c.execute(\n"
            "       \"SELECT id,status,trigger_type,scheduled_time,offline,synced,exit_code FROM executions WHERE task_id=? ORDER BY start_time\",(task_id,))]\n"
            "else:\n"
            "    out['task_executions']=[]\n"
            "print(json.dumps(out))\n"
        ) % (db, exec_ids, task_id, script_id, ids)
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_db_read_failed: %s' % err[-300:])
        return self._last_json(out)

    def log_tail(self, lines=80):
        script = (
            "import json,subprocess\n"
            "p=subprocess.run(['tail','-n',%r,%r],capture_output=True,text=True)\n"
            "print(json.dumps({'tail':p.stdout.splitlines()}))\n"
        ) % (str(lines), self.log)
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_log_tail_failed: %s' % err[-300:])
        return self._last_json(out)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n" % (self.root, self.config, AGENT_BIN) +
            self._pids_guard() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched():\n"
            "    time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left:\n"
            "    os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1)\n"
            "left=matched()\n"
            "existed=os.path.isdir(root)\n"
            "if os.path.isdir(root):\n"
            "    shutil.rmtree(root)\n"
            "print(json.dumps({'signalled':pids,'remaining_processes':left,'root_removed':existed,"
            "'root_exists_after':os.path.exists(root)}))\n"
        )
        rc, out, err = self._ssh(script, timeout=120)
        if rc != 0:
            raise RuntimeError('child_remove_failed: %s' % err[-300:])
        return self._last_json(out)
