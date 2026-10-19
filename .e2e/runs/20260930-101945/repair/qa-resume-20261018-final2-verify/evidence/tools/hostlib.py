"""Owned child Agents + owned host fixtures for the APP-UI-20261016 batch.

Verified build: `dddee4f0-e2e-repair-20261016` (hub cd8b7f9d…, agent b04383bf…).

Targets: native-1 (192.168.100.204) and, only when a second node is needed,
native-3 (192.168.100.222).  The exact repair Agent binary is started as a plain
background process (no systemd unit) under an owned 0700 root with an owned 0600
config.  It never touches `nodesteer-agent.service` or the protected
`nodesteer-agent-repair-20260930-101945-native-N.service` unit of its host.

Every host side effect created by the product (systemd unit, binary, config,
backup dir, artifact cache) lives under an owned, nonce-prefixed path and is
removed again.  Probes are read-only except the explicitly named owned-path
helpers (chattr seams on owned directories, starting/stopping the owned child).
"""
import json
import subprocess
import time
from pathlib import Path

KEY = str(Path.home() / '.ssh' / 'id_ed25519')

HOSTS = {
    'n1': {'ip': '192.168.100.204', 'node': 'native-1',
           'unit': 'nodesteer-agent-repair-20260930-101945-native-1.service'},
    'n2': {'ip': '192.168.100.206', 'node': 'native-2',
           'unit': 'nodesteer-agent-repair-20260930-101945-native-2.service'},
    'n3': {'ip': '192.168.100.222', 'node': 'native-3',
           'unit': 'nodesteer-agent-repair-20260930-101945-native-3.service'},
    'n4': {'ip': '192.168.100.247', 'node': 'native-4',
           'unit': 'nodesteer-agent-repair-20260930-101945-native-4.service'},
}

AGENT_BIN = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
AGENT_BUILD = 'dddee4f0-e2e-repair-20261018-final2'
AGENT_SHA = '89f2a3bb4b6bcb8b208230b0c9bffa48d7fb9ddda577b491dd6f4cf317f90008'
PRIMARY_UNIT = 'nodesteer-agent.service'


def ssh_prefix(host_key):
    return ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + HOSTS[host_key]['ip']]


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    """One owned child Agent process on one owned host."""

    def __init__(self, nonce, host_key, tag='', node_id='', agent_id='', node_name='',
                 node_ip='', token='', deployment_mode='native'):
        self.nonce = nonce
        self.host_key = host_key
        self.host = HOSTS[host_key]
        self.tag = tag
        self.prefix = 'qa-final2-%s%s-' % (nonce, tag)
        self.root = '/var/lib/nodesteer-qa-final2-%s%s' % (nonce, tag)
        self.opt_root = '/opt/qa-final2-%s%s' % (nonce, tag)
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token
        self.deployment_mode = deployment_mode

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=90):
        return subprocess.run(ssh_prefix(self.host_key) + ['python3', '-'], input=script,
                              capture_output=True, text=True, timeout=timeout)

    def remote_py(self, script, timeout=90, retries=2):
        last = None
        for _ in range(retries):
            try:
                cp = self._ssh(script, timeout=timeout)
            except subprocess.TimeoutExpired:
                last = 'remote_py_timeout'
                continue
            if cp.returncode != 0:
                last = 'remote_py_failed: %s' % (cp.stderr[-400:] or cp.stdout[-400:])
                time.sleep(1.0)
                continue
            lines = [l for l in cp.stdout.splitlines() if l.strip()]
            if not lines:
                last = 'remote_py_empty_output'
                time.sleep(1.0)
                continue
            return json.loads(lines[-1])
        raise RuntimeError(last)

    def sh(self, cmd, timeout=90):
        cp = subprocess.run(ssh_prefix(self.host_key) + [cmd], capture_output=True,
                            text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def _matched_script(self):
        return (
            "def matched():\n"
            "    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)\n"
            "    res=[]\n"
            "    for tok in p.stdout.split():\n"
            "        if not tok.isdigit(): continue\n"
            "        pid=int(tok)\n"
            "        try:\n"
            "            exe=os.readlink('/proc/%%d/exe'%%pid)\n"
            "            args=open('/proc/%%d/cmdline'%%pid,'rb').read().split(b'\\0')\n"
            "        except Exception:\n"
            "            continue\n"
            "        if exe==agent and cfg.encode() in args:\n"
            "            res.append(pid)\n"
            "    return res\n"
        )

    # ---------- install / start / stop ----------
    def write_config_and_start(self, hub_url, extra_yaml=''):
        yaml_text = (
            'hub_url: "%s"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 2\n'
            'revision_check_sec: 2\n'
            'max_log_bytes: 262144\n'
            '%s'
        ) % (hub_url, self._token, self.agent_id, self.node_name, self.node_ip,
             self.deployment_mode, self.data, extra_yaml)
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
        cp = subprocess.run(ssh_prefix(self.host_key) + ['python3', '-'], input=prep,
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
            "cfg=%r; agent=%r\n" + self._matched_script() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "time.sleep(2)\n"
            "print(json.dumps({'signalled':pids,'remaining':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    def process_alive(self):
        script = (
            "import json,os,subprocess\n"
            "cfg=%r; agent=%r\n" + self._matched_script() +
            "print(json.dumps({'pids':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    # ---------- host state readback ----------
    def app_state(self, app_id, unit, binary_path, config_path, shas=(), deployments_limit=40):
        script = (
            "import json,os,sqlite3,subprocess,hashlib\n"
            "data=%(data)r; app=%(app)r; unit=%(unit)r; bp=%(bp)r; cp=%(cp)r; shas=%(shas)r\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "def finfo(p):\n"
            "    if not os.path.lexists(p):\n"
            "        return {'exists':False,'lexists':False}\n"
            "    st=os.lstat(p)\n"
            "    d={'exists':os.path.exists(p),'lexists':True,'is_symlink':os.path.islink(p),"
            "'mode':oct(st.st_mode & 0o7777),'size':st.st_size,'mtime':st.st_mtime,'uid':st.st_uid,'gid':st.st_gid}\n"
            "    if st.st_size<10485760 and not os.path.islink(p):\n"
            "        d['sha256']=sha(p)\n"
            "    if os.path.islink(p):\n"
            "        d['target']=os.readlink(p)\n"
            "    return d\n"
            "out={'at_utc':%(now)r,'host_ip':%(ip)r}\n"
            "out['binary']=finfo(bp)\n"
            "out['config']=finfo(cp)\n"
            "out['config_text']=None\n"
            "try:\n"
            "    out['config_text']=open(cp).read()[:2000]\n"
            "except Exception: pass\n"
            "out['binary_dir']=finfo(os.path.dirname(bp))\n"
            "out['binary_dir_entries']=sorted(os.listdir(os.path.dirname(bp))) if os.path.isdir(os.path.dirname(bp)) else None\n"
            "up='/etc/systemd/system/'+unit\n"
            "out['unit_file_path']=up\n"
            "out['unit_file']=finfo(up)\n"
            "out['enable_symlink']=finfo('/etc/systemd/system/multi-user.target.wants/'+unit)\n"
            "r=subprocess.run(['systemctl','show','--no-pager',"
            "'--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath,NRestarts,ExecMainStatus',"
            "unit],capture_output=True,text=True)\n"
            "out['unit_state']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "jid=subprocess.run(['systemctl','show','--no-pager','--property=InvocationID',unit],capture_output=True,text=True).stdout.strip()\n"
            "out['journal_invocation']=jid\n"
            "pid=out['unit_state'].get('MainPID')\n"
            "if pid and pid!='0':\n"
            "    try:\n"
            "        out['main_process']={'exe':os.readlink('/proc/'+pid+'/exe'),"
            "'argv':open('/proc/'+pid+'/cmdline','rb').read().decode('utf-8','replace').split('\\0')[:8]}\n"
            "    except Exception as e:\n"
            "        out['main_process']={'error':type(e).__name__}\n"
            "out['unit_file_text']=None\n"
            "out['unit_file_content']=None\n"
            "try:\n"
            "    t=open(up).read()\n"
            "    out['unit_file_text']=t\n"
            "    out['unit_file_content']={'len':len(t),'has_v1':'--profile=v1' in t,'has_v2':'--profile=v2' in t,"
            "'has_v3':'--profile=v3' in t,'has_v15':'--profile=v1.5' in t}\n"
            "except Exception: pass\n"
            "adir=os.path.join(data,'applications')\n"
            "out['backup_dir']={'path':adir,'lexists':os.path.lexists(adir),'is_dir':os.path.isdir(adir),"
            "'is_symlink':os.path.islink(adir),'mode':(oct(os.lstat(adir).st_mode & 0o7777) if os.path.lexists(adir) else None)}\n"
            "if os.path.isdir(adir):\n"
            "    ent=[]\n"
            "    for n in sorted(os.listdir(adir)):\n"
            "        p=os.path.join(adir,n)\n"
            "        ent.append({'name':n,'info':finfo(p)})\n"
            "    out['backup_entries']=ent\n"
            "else:\n"
            "    out['backup_entries']=[]\n"
            "out['owned_mounts']=[l.strip() for l in open('/proc/mounts') if adir in l]\n"
            "db=os.path.join(data,'state.db')\n"
            "try:\n"
            "    c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5); c.execute('PRAGMA query_only=ON')\n"
            "    out['db_quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]\n"
            "    out['deployments']=[{'id':r[0],'application_id':r[1],'from_version':r[2],'to_version':r[3],"
            "'phase':r[4],'backup_path':r[5],'backup_config_path':r[6],'backup_unit_path':r[7],"
            "'started_at':r[8],'updated_at':r[9]} for r in c.execute("
            "'SELECT id,application_id,from_version,to_version,phase,backup_path,backup_config_path,"
            "backup_unit_path,started_at,updated_at FROM deployments ORDER BY started_at DESC LIMIT ?',(%(limit)d,))]\n"
            "    out['unit_registry']=[{'application_id':r[0],'unit_name':r[1],'registered_at':r[2]} for r in "
            "c.execute('SELECT application_id,unit_name,registered_at FROM unit_registry')]\n"
            "    out['local_apps']=[]\n"
            "    for r in c.execute('SELECT id,revision,definition_json FROM applications'):\n"
            "        d=json.loads(r[2]) if r[2] else None\n"
            "        defn={k:(d or {}).get(k) for k in ('name','version','artifact_id','artifact_url','artifact_sha256',"
            "'binary_path','unit_name','config_path','health_check','arguments','environment','config')} if d else None\n"
            "        out['local_apps'].append({'id':r[0],'revision':r[1],'definition':defn})\n"
            "    out['artifact_rows']=[{'sha256':r[0],'path':r[1],'downloaded_at':r[2]} for r in "
            "c.execute('SELECT sha256,path,downloaded_at FROM artifacts')]\n"
            "    out['global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "    out['identity']={k:v for k,v in c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\")}\n"
            "    out['watched_sha_files']={}\n"
            "    for s in shas:\n"
            "        out['watched_sha_files'][s]=finfo(os.path.join(data,'artifacts',s))\n"
            "    out['artifact_cache_dir']=finfo(os.path.join(data,'artifacts'))\n"
            "    out['artifact_cache_entries']=sorted(os.listdir(os.path.join(data,'artifacts'))) if os.path.isdir(os.path.join(data,'artifacts')) else None\n"
            "except Exception as e:\n"
            "    out['db_error']=type(e).__name__+':'+str(e)[:200]\n"
            "print(json.dumps(out))\n"
        ) % {'data': self.data, 'app': app_id, 'unit': unit, 'bp': binary_path, 'cp': config_path,
             'shas': list(shas), 'now': now_utc(), 'limit': deployments_limit,
             'ip': self.host['ip']}
        return self.remote_py(script)

    def journal(self, unit, lines=200):
        """systemd journal for the owned product unit (real host effect)."""
        script = (
            "import json,subprocess\n"
            "u=%r; n=%d\n"
            "r=subprocess.run(['journalctl','-u',u,'-n',str(n),'--no-pager','-o','short-iso',"
            "'--output-fields=MESSAGE,__REALTIME_TIMESTAMP'],capture_output=True,text=True)\n"
            "print(json.dumps({'rc':r.returncode,'journal':r.stdout[-12000:],'err':r.stderr[-400:],'unit':u}))\n"
        ) % (unit, lines)
        return self.remote_py(script)

    def log_tail(self, max_bytes=60000):
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

    # ---------- owned-path seams ----------
    def seam(self, path, action, extra=None):
        script = (
            "import json,os,subprocess,shutil\n"
            "p=%(p)r; action=%(action)r; extra=json.loads(%(extra)r) or {}\n"
            "out={'action':action,'path':p}\n"
            "def run(cmd):\n"
            "    r=subprocess.run(cmd,capture_output=True,text=True)\n"
            "    return {'rc':r.returncode,'out':r.stdout.strip()[:400],'err':r.stderr.strip()[:400]}\n"
            "if action in ('rm','mkdir','symlink','regular_file','mount_tmpfs'):\n"
            "    if os.path.islink(p): os.unlink(p)\n"
            "    elif os.path.isdir(p) and action in ('rm','symlink','regular_file'): shutil.rmtree(p)\n"
            "    elif os.path.lexists(p) and action in ('rm','mkdir','symlink','regular_file'): os.unlink(p)\n"
            "if action=='rm':\n"
            "    pass\n"
            "elif action=='mkdir':\n"
            "    os.makedirs(p, mode=int(extra.get('mode',0o755)), exist_ok=True)\n"
            "elif action=='chattr_plus_i':\n"
            "    out['result']=run(['chattr','+i',p])\n"
            "elif action=='chattr_minus_i':\n"
            "    out['result']=run(['chattr','-i',p])\n"
            "elif action=='lsattr':\n"
            "    out['result']=run(['lsattr','-d',p])\n"
            "if os.path.lexists(p):\n"
            "    st=os.lstat(p)\n"
            "    out['after']={'lexists':True,'is_dir':os.path.isdir(p),'is_symlink':os.path.islink(p),"
            "'mode':oct(st.st_mode & 0o7777),'size':st.st_size}\n"
            "else:\n"
            "    out['after']={'lexists':False}\n"
            "out['mounts']=[l.strip() for l in open('/proc/mounts') if p in l]\n"
            "print(json.dumps(out))\n"
        ) % {'p': path, 'action': action, 'extra': json.dumps(extra or {})}
        return self.remote_py(script)

    def unit_cleanup(self, unit, binary_path='', config_path='', opt_root='', extra_files=()):
        script = (
            "import json,os,shutil,subprocess\n"
            "unit=%(unit)r; bp=%(bp)r; cp=%(cp)r; opt=%(opt)r; extra=%(extra)r\n"
            "out={}\n"
            "def run(cmd):\n"
            "    r=subprocess.run(cmd,capture_output=True,text=True)\n"
            "    return {'rc':r.returncode,'out':r.stdout.strip()[:200],'err':r.stderr.strip()[:200]}\n"
            "if unit:\n"
            "    out['stop']=run(['systemctl','stop',unit])\n"
            "    out['disable']=run(['systemctl','disable',unit])\n"
            "    up='/etc/systemd/system/'+unit\n"
            "    out['unit_file_existed']=os.path.lexists(up)\n"
            "    if os.path.lexists(up): os.remove(up)\n"
            "    out['daemon_reload']=run(['systemctl','daemon-reload'])\n"
            "    out['reset_failed']=run(['systemctl','reset-failed',unit])\n"
            "for p in [bp,cp]+list(extra):\n"
            "    if p and os.path.lexists(p):\n"
            "        try: os.remove(p)\n"
            "        except Exception: pass\n"
            "if opt and os.path.isdir(opt):\n"
            "    shutil.rmtree(opt, ignore_errors=True)\n"
            "out['remaining_unit_exists']=os.path.lexists('/etc/systemd/system/'+unit) if unit else None\n"
            "print(json.dumps(out))\n"
        ) % {'unit': unit, 'bp': binary_path, 'cp': config_path, 'opt': opt_root,
             'extra': list(extra_files)}
        return self.remote_py(script)

    # ---------- host baseline / oracle ----------
    def host_baseline(self):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "prefix=%(prefix)r; root=%(root)r; opt=%(opt)r\n"
            "out={'host_ip':%(ip)r,'host_key':%(hk)r}\n"
            "sy=subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],"
            "capture_output=True,text=True).stdout.splitlines()\n"
            "out['owned_units']=[l.strip() for l in sy if prefix in l]\n"
            "out['etc_systemd_owned']=[n for n in sorted(os.listdir('/etc/systemd/system')) if prefix in n]\n"
            "w='/etc/systemd/system/multi-user.target.wants'\n"
            "out['wants_owned']=[n for n in sorted(os.listdir(w)) if prefix in n] if os.path.isdir(w) else []\n"
            "out['owned_mounts']=[l.strip() for l in open('/proc/mounts') if root in l or opt in l]\n"
            "out['root_exists']=os.path.isdir(root); out['opt_root_exists']=os.path.isdir(opt)\n"
            "out['child_config_exists']=os.path.isfile(root+'/child.yaml')\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "out['owned_child_processes']=[l for l in p.stdout.splitlines() if root in l]\n"
            "out['agent_binary_sha256']=sha(%(bin)r)\n"
            "def unit_state(u):\n"
            "    r=subprocess.run(['systemctl','show','--no-pager',"
            "'--property=Id,ActiveState,SubState,MainPID,UnitFileState',u],capture_output=True,text=True)\n"
            "    return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "out['primary_agent_unit']=unit_state('nodesteer-agent.service')\n"
            "out['repair_agent_unit']=unit_state(%(prot)r)\n"
            "out['all_nodesteer_units']=[l.strip() for l in sy if 'nodesteer' in l]\n"
            "out['etc_systemd_total']=len(os.listdir('/etc/systemd/system'))\n"
            "out['global_owned_prefix_scan']=[n for n in sorted(os.listdir('/etc/systemd/system')) if 'qa-' in n]\n"
            "print(json.dumps(out))\n"
        ) % {'prefix': self.prefix, 'root': self.root, 'opt': self.opt_root,
             'bin': AGENT_BIN, 'prot': self.host['unit'], 'ip': self.host['ip'],
             'hk': self.host_key}
        return self.remote_py(script)

    # ---------- teardown ----------
    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%(root)r; cfg=%(cfg)r; opt=%(opt)r; adir=%(adir)r; agent=%(bin)r\n"
            "def run(cmd):\n"
            "    r=subprocess.run(cmd,capture_output=True,text=True)\n"
            "    return {'rc':r.returncode,'out':r.stdout.strip()[:200],'err':r.stderr.strip()[:200]}\n"
            "out={}\n"
            "for d in (adir, os.path.join(root,'data','artifacts')):\n"
            "    if os.path.isdir(d): run(['chattr','-i',d])\n"
            "mnt=[l.split()[1] for l in open('/proc/mounts') if adir in l or root in l or opt in l]\n"
            "for m in sorted(set(mnt), key=len, reverse=True):\n"
            "    out.setdefault('umount',[]).append(run(['umount',m]))\n" + self._matched_script() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched():\n"
            "    time.sleep(0.5)\n"
            "left=matched()\n"
            "for pid in left:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.5)\n"
            "out['agent_signalled']=pids; out['agent_remaining']=matched()\n"
            "for p in (root,opt):\n"
            "    if os.path.isdir(p):\n"
            "        shutil.rmtree(p, ignore_errors=True)\n"
            "out['root_removed']=not os.path.exists(root)\n"
            "out['opt_removed']=not os.path.exists(opt)\n"
            "out['owned_mounts_after']=[l.strip() for l in open('/proc/mounts') if root in l or opt in l]\n"
            "out['owned_units_after']=[l.strip() for l in subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],capture_output=True,text=True).stdout.splitlines() if %(prefix)r in l]\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'cfg': self.config, 'opt': self.opt_root, 'bin': AGENT_BIN,
             'adir': self.data + '/applications', 'prefix': self.prefix}
        return self.remote_py(script, timeout=180)
