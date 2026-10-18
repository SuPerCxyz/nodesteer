#!/usr/bin/env python3
"""Owned child Agent lifecycle for batch09-settings / idempotent-manual.

Runs the exact repair Agent binary as a plain background process (no systemd
unit) under an owned 0700 root, with an owned 0600 config. It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-*-native-*.service`.

The child's *local* config values are deliberately set to small fixed values so
that any observed runtime change can only have come from the Hub (the Hub pushes
settings on HELLO_ACK and on every HEARTBEAT_ACK, plus the explicit SETTINGS
broadcast), never from the local file.
"""
import json
import subprocess
import time
from pathlib import Path

SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
DEFAULT_HOST = '192.168.100.247'          # native-4
NODE_HOSTS = {
    'native-1': '192.168.100.204',
    'native-2': '192.168.100.206',
    'native-3': '192.168.100.222',
    'native-4': '192.168.100.247',
}


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip,
                 token, host=DEFAULT_HOST, tag='qa-b09',
                 heartbeat_sec=2, revision_check_sec=2, max_log_bytes=262144):
        self.host = host
        self.ssh_base = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + host]
        self.root = '/var/lib/nodesteer-%s-%s' % (tag, nonce)
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = proxy_port
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token
        self.heartbeat_sec = heartbeat_sec
        self.revision_check_sec = revision_check_sec
        self.max_log_bytes = max_log_bytes
        self.operator_ip = '192.168.100.170'

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=60):
        cp = subprocess.run(self.ssh_base + ['python3', '-'], input=script,
                            capture_output=True, text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def _last_json(self, out):
        lines = [l for l in out.splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else None

    # ---------- guards ----------
    def inventory(self):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "out={'host':%r,'root':%r,'root_exists':os.path.isdir(%r),'config_exists':os.path.isfile(%r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['agent_process_table']=lines\n"
            "out['owned_child_processes']=[l for l in lines if %r in l]\n"
            "out['agent_binary_sha256']=sha(%r) if os.path.isfile(%r) else None\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,SubState,MainPID',"
            "'nodesteer-agent.service'],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "import glob\n"
            "out['repair_agent_units']={}\n"
            "for path in sorted(glob.glob('/etc/systemd/system/nodesteer-agent-repair-*.service')):\n"
            "    name=os.path.basename(path)\n"
            "    r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,SubState,MainPID',name],capture_output=True,text=True)\n"
            "    out['repair_agent_units'][name]=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.host, self.root, self.root, self.config, self.config, AGENT_BIN, AGENT_BIN)
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
            'heartbeat_sec: %d\n'
            'revision_check_sec: %d\n'
            'max_log_bytes: %d\n'
        ) % (self.operator_ip, self.proxy_port, self._token, self.agent_id,
             self.node_name, self.node_ip, self.data,
             self.heartbeat_sec, self.revision_check_sec, self.max_log_bytes)
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
        cp = subprocess.run(self.ssh_base + ['python3', '-'], input=prep,
                            capture_output=True, text=True, timeout=60)
        if cp.returncode != 0 or 'CONFIG_WRITTEN' not in cp.stdout:
            raise RuntimeError('child_config_write_failed: %s' % (cp.stderr[-300:] or cp.stdout[-300:]))
        return self.start()

    def local_config_echo(self):
        """Return the local config keys WITHOUT the registration token."""
        script = (
            "import json\n"
            "out={'exists':False}\n"
            "try:\n"
            "    t=open(%r).read()\n"
            "    out['exists']=True\n"
            "    for line in t.splitlines():\n"
            "        if ':' in line and 'token' not in line:\n"
            "            k,v=line.split(':',1); out[k.strip()]=v.strip().strip('\\\"')\n"
            "except Exception as e:\n"
            "    out['error']=type(e).__name__\n"
            "print(json.dumps(out))\n"
        ) % self.config
        rc, out, err = self._ssh(script)
        if rc != 0:
            raise RuntimeError('child_config_echo_failed: %s' % err[-300:])
        return self._last_json(out)

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
            "time.sleep(2)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_stop_failed: %s' % err[-300:])
        return self._last_json(out)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    # ---------- read-only local journal inspection ----------
    def db_read_executions(self, exec_ids):
        db = self.data + '/state.db'
        script = (
            "import json,sqlite3\n"
            "db=%r\n"
            "ids=%r\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "out={'quick_check':c.execute('PRAGMA quick_check').fetchone()[0]}\n"
            "out['identity']=dict(c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\"))\n"
            "out['executions_total']=c.execute('SELECT count(*) FROM executions').fetchone()[0]\n"
            "rows=[]\n"
            "for eid in ids:\n"
            "    r=c.execute('SELECT id,task_id,node_id,status,trigger_type,offline,synced,exit_code,"
            "length(coalesce(stdout,\\'\\')),length(coalesce(stderr,\\'\\')) FROM executions WHERE id=?',(eid,)).fetchone()\n"
            "    rows.append(None if r is None else {'id':r[0],'task_id':r[1],'node_id':r[2],'status':r[3],"
            "'trigger_type':r[4],'offline':bool(r[5]),'synced':bool(r[6]),'exit_code':r[7],"
            "'stdout_len':r[8],'stderr_len':r[9]})\n"
            "out['requested_executions']=rows\n"
            "print(json.dumps(out))\n"
        ) % (db, exec_ids)
        rc, out, err = self._ssh(script, timeout=60)
        if rc != 0:
            raise RuntimeError('child_db_read_failed: %s' % err[-300:])
        return self._last_json(out)

    def read_local_file(self, path, max_bytes=4096):
        script = (
            "import json\n"
            "try:\n"
            "    with open(%r,'rb') as f: b=f.read(%d)\n"
            "    print(json.dumps({'exists':True,'len':len(b),'head':b[:600].decode('utf-8','replace')}))\n"
            "except Exception as e:\n"
            "    print(json.dumps({'exists':False,'error':type(e).__name__}))\n"
        ) % (path, max_bytes)
        rc, out, err = self._ssh(script)
        if rc != 0:
            raise RuntimeError('child_file_read_failed: %s' % err[-300:])
        return self._last_json(out)

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
        rc, out, err = self._ssh(script, timeout=90)
        if rc != 0:
            raise RuntimeError('child_remove_failed: %s' % err[-300:])
        return self._last_json(out)
