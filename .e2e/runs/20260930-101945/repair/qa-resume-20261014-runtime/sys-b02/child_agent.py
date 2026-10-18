"""Owned child Agent lifecycle on repair native-1 (192.168.100.204) for sys-b02.

Runs the exact repair Agent binary as a plain background process (no systemd
unit) under an owned 0700 root with an owned 0600 config. It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-*-native-1.service`.
"""
import json
import subprocess
import time
from pathlib import Path

HOST = '192.168.100.204'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HOST]
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
REPAIR_UNIT = 'nodesteer-agent-repair-20260930-101945-native-1.service'
OPERATOR_IP = '192.168.100.170'
ROOT_PREFIX = '/var/lib/nodesteer-qa-sysb02-'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip, token):
        self.root = ROOT_PREFIX + nonce
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = proxy_port
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=90):
        cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                            text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def _json(self, script, timeout=90):
        rc, out, err = self._ssh(script, timeout=timeout)
        if rc != 0:
            raise RuntimeError('remote_script_failed: %s' % (err[-400:] or out[-400:]))
        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError('remote_script_no_output')
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
            "q=subprocess.run(['pgrep','-af','nodesteer-qa-sysb02'],capture_output=True,text=True)\n"
            "out['any_sysb02_processes']=[l for l in q.stdout.splitlines() if l.strip()]\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.config, AGENT_BIN, REPAIR_UNIT)
        return self._json(script)

    # ---------- install / start / stop ----------
    def write_config_and_start(self):
        """Write the owned 0600 config (token inside the SSH program payload, never
        in argv and never on the operator disk) and start the child."""
        yaml_text = (
            'hub_url: "ws://%s:%d"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "native"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 5\n'
            'revision_check_sec: 5\n'
            'max_log_bytes: 262144\n'
        ) % (OPERATOR_IP, self.proxy_port, self._token, self.agent_id,
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
        rc, out, err = self._ssh(prep, timeout=60)
        if rc != 0 or 'CONFIG_WRITTEN' not in out:
            raise RuntimeError('child_config_write_failed: %s' % (err[-300:] or out[-300:]))
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
        return self._json(script, timeout=60)

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
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
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched():\n"
            "    time.sleep(0.5)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self._json(script, timeout=60)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    # ---------- read-only local state inspection ----------
    def db_query(self, sql, params=None, timeout=60):
        """Read-only query against the Agent local SQLite (query_only=ON)."""
        script = (
            "import json,sqlite3\n"
            "db=%r; sql=%r; params=%r\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "c.row_factory=sqlite3.Row\n"
            "rows=[dict(r) for r in c.execute(sql, params or ())]\n"
            "print(json.dumps({'sql':sql,'rows':rows}))\n"
        ) % (self.data + '/state.db', sql, params or [])
        return self._json(script, timeout=timeout)

    def local_state(self, script_id, task_id, schedule_id, phantom_script_id):
        ids = [i for i in (script_id, task_id, schedule_id, phantom_script_id) if i]
        ph = ','.join(['?'] * len(ids)) if ids else "''"
        return self.db_query(
            'SELECT id,name,revision,sha256,enabled,length(content) AS content_len FROM scripts '
            'WHERE id IN (%s)' % ph, ids) if ids else {'rows': []}

    def db_summary(self, ids):
        ids = [i for i in ids if i]
        script = (
            "import json,sqlite3\n"
            "db=" + repr(self.data + '/state.db') + "\n"
            "ids=" + repr(ids) + "\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "c.row_factory=sqlite3.Row\n"
            "ph=','.join(['?']*len(ids)) if ids else \"''\"\n"
            "out={'quick_check':c.execute('PRAGMA quick_check').fetchone()[0]}\n"
            "out['local_global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "out['last_sync']=c.execute('SELECT last_sync FROM sync_state WHERE id=1').fetchone()[0]\n"
            "out['identity']=dict(c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\"))\n"
            "out['scripts']=[dict(r) for r in c.execute('SELECT id,name,revision,sha256,enabled,length(content) AS content_len FROM scripts WHERE id IN (%s)' % ph, ids)]\n"
            "out['tasks']=[dict(r) for r in c.execute('SELECT id,revision,enabled,length(definition_json) AS def_len FROM tasks WHERE id IN (%s)' % ph, ids)]\n"
            "out['schedules']=[dict(r) for r in c.execute('SELECT id,revision,enabled FROM schedules WHERE id IN (%s)' % ph, ids)]\n"
            "out['counts']={'scripts':c.execute('SELECT count(*) FROM scripts').fetchone()[0],"
            "'tasks':c.execute('SELECT count(*) FROM tasks').fetchone()[0],"
            "'schedules':c.execute('SELECT count(*) FROM schedules').fetchone()[0]}\n"
            "out['executions']=[dict(r) for r in c.execute('SELECT id,task_id,status,exit_code,offline,synced FROM executions WHERE task_id IN (%s) ORDER BY start_time' % ph, ids)]\n"
            "print(json.dumps(out))\n"
        )
        return self._json(script)

    def full_script_ids(self):
        return self.db_query('SELECT id,name,revision,sha256 FROM scripts ORDER BY id')

    def log_tail(self, lines=60):
        script = (
            "import json,subprocess\n"
            "p=subprocess.run(['tail','-n',%r,%r],capture_output=True,text=True)\n"
            "print(json.dumps({'tail':p.stdout.splitlines()}))\n"
        ) % (str(lines), self.log)
        return self._json(script)

    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n"
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
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
        ) % (self.root, self.config, AGENT_BIN)
        return self._json(script, timeout=120)
