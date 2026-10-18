"""Owned child Agent lifecycle on repair native-1 (192.168.100.204) for p-sysmisc.

Runs the exact repair Agent binary as a plain background process (no systemd unit)
under an owned 0700 root with an owned 0600 config. It never touches
`nodesteer-agent.service` or
`nodesteer-agent-repair-20260930-101945-native-1.service`.

Adapted from the p-sched batch child_agent.py, retargeted to native-1 and with
extra read-only instrumentation needed by p-sysmisc:

  * /proc/<pid> socket + listener inventory (INV-02),
  * a size-limited tmpfs data dir so a sync write can be made to fail with a
    real non-BUSY SQLite error (INV-05).
"""
import json
import subprocess
import time
from pathlib import Path

HOST = '192.168.100.204'
OPERATOR_IP = '192.168.100.170'
SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
       '-o', 'ControlMaster=auto',
       '-o', 'ControlPath=/tmp/opencode/psm-native1-ssh-%r@%h:%p',
       '-o', 'ControlPersist=600',
       '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HOST]
AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
REPAIR_UNIT = 'nodesteer-agent-repair-20260930-101945-native-1.service'
PRIMARY_UNIT = 'nodesteer-agent.service'
ROOT_PREFIX = '/var/lib/nodesteer-qa-psm-'
OWNED_TAG = 'nodesteer-qa-psm'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, proxy_port, node_id, agent_id, node_name, node_ip, token,
                 deployment_mode='native', label='a', tmpfs_mb=0):
        self.label = label
        self.root = ROOT_PREFIX + nonce + '-' + label
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.fixtures = self.root + '/fixtures'
        self.tmpfs_mb = tmpfs_mb
        self.deployment_mode = deployment_mode
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

    def _match_py(self):
        return (
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    out=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit():\n"
            "            continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%d/exe'%pid)\n"
            "            args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')\n"
            "        except Exception:\n"
            "            continue\n"
            "        if exe==agent and cfg.encode() in args:\n"
            "            out.append(pid)\n"
            "    return out\n"
        )

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
            "out['any_owned_processes']=[l for l in lines if 'nodesteer-qa-psm' in l]\n"
            "out['agent_binary_sha256']=sha(%r)\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],capture_output=True,text=True)\n"
            "out['primary_agent_unit']=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
            "r=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID',%r],capture_output=True,text=True)\n"
            "out['repair_agent_unit']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "print(json.dumps(out))\n"
        ) % (self.root, self.config, self.config, AGENT_BIN, PRIMARY_UNIT, REPAIR_UNIT)
        return self._json(script)

    # ---------- install / start ----------
    def prepare(self):
        """Create the owned root + (optional) size-limited tmpfs data dir."""
        mount_cmd = ''
        if self.tmpfs_mb:
            mount_cmd = ("if not os.path.ismount(data):\n"
                         "    subprocess.run(['mount','-t','tmpfs','-o',"
                         "'size=%dm,nodev,nosuid','tmpfs',data],check=True)\n" % self.tmpfs_mb)
        prep = (
            "import os,subprocess,json\n"
            "root=%r; data=%r; log=%r\n"
            "os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)\n"
            "os.makedirs(data, mode=0o700, exist_ok=True)\n"
            % (self.root, self.data, self.log)
            + mount_cmd +
            "open(log,'a').close()\n"
            "print(json.dumps({'root':root,'data':data,'data_is_tmpfs':os.path.ismount(data)}))\n"
        )
        return self._json(prep, timeout=60)

    def write_config_and_start(self, heartbeat_sec=5, revision_check_sec=5):
        yaml_text = (
            'hub_url: "ws://%s:%d"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: %d\n'
            'revision_check_sec: %d\n'
            'max_log_bytes: 262144\n'
        ) % (OPERATOR_IP, self.proxy_port, self._token, self.agent_id,
             self.node_name, self.node_ip, self.deployment_mode, self.data,
             heartbeat_sec, revision_check_sec)
        prep = (
            "import os\n"
            "cfg=%r; body=%r\n"
            "fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)\n"
            "with os.fdopen(fd,'w') as f: f.write(body)\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.config, yaml_text)
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

    def pids(self):
        script = (
            "import json,os,subprocess\n"
            "cfg=%r; agent=%r\n" % (self.config, AGENT_BIN)
        ) + self._match_py() + (
            "print(json.dumps({'pids':matched()}))\n"
        )
        return self._json(script, timeout=40)['pids']

    def stop(self):
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" % (self.config, AGENT_BIN)
        ) + self._match_py() + (
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched():\n"
            "    time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left:\n"
            "    os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(0.8)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n"
        )
        return self._json(script, timeout=60)

    def umount(self):
        if not self.tmpfs_mb:
            return {'umounted': False, 'reason': 'not_tmpfs'}
        script = (
            "import os,subprocess,json\n"
            "data=%r\n"
            "res={'was_mount':os.path.ismount(data),'umounted':False}\n"
            "for _ in range(3):\n"
            "    if not os.path.ismount(data):\n"
            "        res['umounted']=True; break\n"
            "    subprocess.run(['umount','-l',data],capture_output=True)\n"
            "    import time; time.sleep(1.0)\n"
            "res['still_mount']=os.path.ismount(data)\n"
            "print(json.dumps(res))\n"
        ) % self.data
        return self._json(script, timeout=60)

    def remove(self, umount_first=False):
        if umount_first or self.tmpfs_mb:
            try:
                self.umount()
            except Exception:
                pass
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%r; cfg=%r; agent=%r\n" % (self.root, self.config, AGENT_BIN)
        ) + self._match_py() + (
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
            "if os.path.ismount(%r):\n"
            "    subprocess.run(['umount','-l',%r],capture_output=True)\n"
            "    time.sleep(0.5)\n"
            "existed=os.path.isdir(root)\n"
            "if os.path.isdir(root):\n"
            "    shutil.rmtree(root, ignore_errors=True)\n"
            "print(json.dumps({'signalled':pids,'remaining_processes':left,'root_removed':existed,"
            "'root_exists_after':os.path.exists(root),'data_still_mount':os.path.ismount(%r)}))\n"
        ) % (self.data, self.data, self.data)
        return self._json(script, timeout=120)

    # ---------- read-only local state inspection ----------
    def db_query(self, sql, params=None, timeout=60):
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

    def db_meta(self):
        """WAL mode, files present, quick_check and identity keys (values flagged, not exported)."""
        script = (
            "import json,os,sqlite3\n"
            "data=%r\n"
            "out={}\n"
            "fs={}\n"
            "for suffix in ('','-wal','-shm'):\n"
            "    p=os.path.join(data,'state.db'+suffix)\n"
            "    fs['state.db'+suffix]={'exists':os.path.isfile(p),'size':(os.path.getsize(p) if os.path.isfile(p) else 0)}\n"
            "out['db_files']=fs\n"
            "db=os.path.join(data,'state.db')\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro', uri=True, timeout=5)\n"
            "c.execute('PRAGMA query_only=ON')\n"
            "out['journal_mode']=c.execute('PRAGMA journal_mode').fetchone()[0]\n"
            "out['quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]\n"
            "out['identity']=[{'key':r[0],'present':bool(r[1]),'chars':len(r[1] or '')} for r in c.execute('SELECT key,value FROM identity')]\n"
            "out['sync_state']=[{'global_revision':r[0],'last_sync':r[1]} for r in c.execute('SELECT global_revision,last_sync FROM sync_state')]\n"
            "print(json.dumps(out))\n"
        ) % self.data
        return self._json(script, timeout=60)

    def read_file(self, path, tail=0):
        script = (
            "import json,os\n"
            "p=%r\n"
            "out={'exists':os.path.isfile(p)}\n"
            "if out['exists']:\n"
            "    lines=open(p,errors='replace').read().splitlines()\n"
            "    out['line_count']=len(lines)\n"
            "    out['lines']=lines[-%d:] if %d else lines\n" % (path, tail, tail)
        ) + "print(json.dumps(out))\n"
        return self._json(script)

    # ---------- INV-02: /proc socket + listener inventory ----------
    def socket_inventory(self, pids=None):
        pids = pids or []
        script = (
            "import json,os,glob,subprocess\n"
            "targets=%r\n"
            "def read_net(p):\n"
            "    rows={}\n"
            "    if not os.path.isfile(p):\n"
            "        return rows\n"
            "    for ln in open(p).read().splitlines()[1:]:\n"
            "        f=ln.split()\n"
            "        if len(f)<10:\n"
            "            continue\n"
            "        rows[f[9]]={'local':f[1],'remote':f[2],'state':f[3]}\n"
            "    return rows\n"
            "tcp=read_net('/proc/net/tcp'); tcp6=read_net('/proc/net/tcp6')\n"
            "out=[]\n"
            "for pid in targets:\n"
            "    socks=[]\n"
            "    for fd in glob.glob('/proc/%%d/fd/*'%%pid):\n"
            "        try:\n"
            "            t=os.readlink(fd)\n"
            "        except Exception:\n"
            "            continue\n"
            "        if t.startswith('socket:['):\n"
            "            ino=t[8:-1]\n"
            "            m=tcp.get(ino) or tcp6.get(ino)\n"
            "            socks.append({'fd':os.path.basename(fd),'inode':ino,'net':m})\n"
            "    out.append({'pid':pid,'sockets':socks})\n"
            "ss=subprocess.run(['ss','-H','-tanp'],capture_output=True,text=True).stdout.splitlines()\n"
            "print(json.dumps({'procs':out,'ss_all_count':len(ss)}))\n"
        ) % (pids,)
        return self._json(script, timeout=40)

    def ss_for_pids(self, pids):
        script = (
            "import json,subprocess\n"
            "pids=%r\n"
            "out={}\n"
            "for pid in pids:\n"
            "    base=['ss','-H','-tanp']\n"
            "    rows=subprocess.run(base,capture_output=True,text=True).stdout.splitlines()\n"
            "    out[str(pid)]=[r for r in rows if ('pid=%%d,'%%pid) in r or r.endswith('pid=%%d'%%pid)]\n"
            "print(json.dumps(out))\n"
        ) % (list(pids),)
        return self._json(script, timeout=40)

    # ---------- INV-05: size-limited tmpfs space control ----------
    def tmpfs_stat(self):
        script = (
            "import json,os\n"
            "data=%r\n"
            "st=os.statvfs(data)\n"
            "junk=[]\n"
            "for name in os.listdir(data):\n"
            "    if name.startswith('junk-'):\n"
            "        junk.append({'name':name,'size':os.path.getsize(os.path.join(data,name))})\n"
            "print(json.dumps({'is_mount':os.path.ismount(data),'free_bytes':st.f_bavail*st.f_frsize,"
            "'total_bytes':st.f_blocks*st.f_frsize,'junk':junk}))\n"
        ) % self.data
        return self._json(script, timeout=40)

    def tmpfs_fill(self, keep_free_bytes):
        script = (
            "import json,os\n"
            "data=%r; keep=%d\n"
            "st=os.statvfs(data)\n"
            "free=st.f_bavail*st.f_frsize\n"
            "need=free-keep\n"
            "name=os.path.join(data,'junk-holder')\n"
            "written=0\n"
            "with open(name,'wb') as f:\n"
            "    chunk=b'\\0'*(1024*1024)\n"
            "    while written<need:\n"
            "        n=min(len(chunk),need-written)\n"
            "        f.write(chunk[:n]); written+=n\n"
            "    f.flush(); os.fsync(f.fileno())\n"
            "st2=os.statvfs(data)\n"
            "print(json.dumps({'wrote':written,'free_after':st2.f_bavail*st2.f_frsize,'junk':name}))\n"
        ) % (self.data, keep_free_bytes)
        return self._json(script, timeout=90)

    def tmpfs_clear_junk(self):
        script = (
            "import json,os\n"
            "data=%r\n"
            "removed=[]\n"
            "for name in os.listdir(data):\n"
            "    if name.startswith('junk-'):\n"
            "        os.remove(os.path.join(data,name)); removed.append(name)\n"
            "st=os.statvfs(data)\n"
            "print(json.dumps({'removed':removed,'free_after':st.f_bavail*st.f_frsize}))\n"
        ) % self.data
        return self._json(script, timeout=40)


def sweep_owned():
    """Defensive final sweep: SIGKILL any owned child Agent and remove all owned roots."""
    script = (
        "import json,os,signal,shutil,subprocess,time,glob\n"
        "pat='nodesteer-qa-psm'\n"
        "q=subprocess.run(['pgrep','-f',pat],capture_output=True,text=True)\n"
        "pids=[int(x) for x in q.stdout.split() if x.isdigit()]\n"
        "for pid in pids:\n"
        "    try:\n"
        "        os.kill(pid, signal.SIGKILL)\n"
        "    except Exception:\n"
        "        pass\n"
        "time.sleep(1)\n"
        "q2=subprocess.run(['pgrep','-af',pat],capture_output=True,text=True)\n"
        "left=[l for l in q2.stdout.splitlines() if l.strip() and 'pgrep' not in l]\n"
        "roots=glob.glob('/var/lib/nodesteer-qa-psm-*')\n"
        "for r in roots:\n"
        "    d=os.path.join(r,'data')\n"
        "    if os.path.ismount(d):\n"
        "        subprocess.run(['umount','-l',d],capture_output=True)\n"
        "for r in roots:\n"
        "    shutil.rmtree(r, ignore_errors=True)\n"
        "print(json.dumps({'killed':pids,'remaining':left,'roots_removed':roots,"
        "'roots_exists_after':glob.glob('/var/lib/nodesteer-qa-psm-*')}))\n"
    )
    cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True, text=True, timeout=120)
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {'error': cp.stderr[-300:], 'rc': cp.returncode}
