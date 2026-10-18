"""Owned child Agent + owned host fixtures on repair native-2 (192.168.100.206) for sys-b11.

Runs the exact repair Agent binary as a plain background process (no systemd unit) under an
owned 0700 root with an owned 0600 config.  It never touches `nodesteer-agent.service` or
`nodesteer-agent-repair-20260930-101945-native-2.service`.

Owned host side effects created by the Managed Application lifecycle (systemd unit, binary,
config, backup dir) are always placed under owned, nonce-prefixed paths and are removed again.

All probes are read-only except the explicitly named owned-path helpers
(prepare/restore backup-dir seams, mount/umount own tmpfs, chattr on own dir).
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
PRIMARY_UNIT = 'nodesteer-agent.service'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class OwnedChild:
    def __init__(self, nonce, node_id='', agent_id='', node_name='', node_ip='', token='',
                 deployment_mode='native'):
        self.nonce = nonce
        self.prefix = 'qa-sysb11-%s-' % nonce
        self.root = '/var/lib/nodesteer-qa-sysb11-%s' % nonce
        self.opt_root = '/opt/qa-sysb11-%s' % nonce
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.proxy_port = 0
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self._token = token
        self.deployment_mode = deployment_mode

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

    def remote_raw(self, script, timeout=90):
        return self._ssh(script, timeout=timeout)

    # ---------- guards / inventory ----------
    def inventory(self, unit=''):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "out={'root_exists':os.path.isdir(%(root)r),'opt_root_exists':os.path.isdir(%(opt)r),"
            "'config_exists':os.path.isfile(%(cfg)r)}\n"
            "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
            "lines=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "out['agent_process_table']=lines\n"
            "out['owned_child_processes']=[l for l in lines if %(cfg)r in l]\n"
            "out['agent_binary_sha256']=sha(%(bin)r)\n"
            "def unit_state(u):\n"
            "    r=subprocess.run(['systemctl','show','--no-pager',"
            "'--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath',u],"
            "capture_output=True,text=True)\n"
            "    return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "out['primary_agent_unit']=unit_state(%(prim)r)\n"
            "out['repair_agent_unit']=unit_state(%(prot)r)\n"
            "u=%(unit)r\n"
            "out['owned_unit_state']=unit_state(u) if u else None\n"
            "out['owned_unit_file_exists']=os.path.exists('/etc/systemd/system/'+u) if u else None\n"
            "sd=sorted(os.listdir('/etc/systemd/system'))\n"
            "out['etc_systemd_owned_leftovers']=[n for n in sd if %(prefix)r in n]\n"
            "out['etc_systemd_owned_wants']=[]\n"
            "w='/etc/systemd/system/multi-user.target.wants'\n"
            "if os.path.isdir(w):\n"
            "    out['etc_systemd_owned_wants']=[n for n in sorted(os.listdir(w)) if %(prefix)r in n]\n"
            "m=[l for l in open('/proc/mounts') if %(root)r in l or %(opt)r in l]\n"
            "out['owned_mounts']=m\n"
            "sy=subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],"
            "capture_output=True,text=True).stdout.splitlines()\n"
            "out['owned_systemd_units']=[l.strip() for l in sy if %(prefix)r in l]\n"
            "out['opt_entries']=sorted(os.listdir(%(opt)r)) if os.path.isdir(%(opt)r) else []\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'opt': self.opt_root, 'cfg': self.config, 'bin': AGENT_BIN,
             'prim': PRIMARY_UNIT, 'prot': PROTECTED_UNIT, 'unit': unit, 'prefix': 'qa-sysb11-%s' % self.nonce}
        return self.remote_py(script)

    # ---------- install / start / stop ----------
    def write_config_and_start(self, hub_url):
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
        ) % (hub_url, self._token, self.agent_id, self.node_name, self.node_ip,
             self.deployment_mode, self.data)
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

    def kill_child(self):
        """SIGKILL the owned child agent (identity-guarded); used for crash-recovery scenarios."""
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n"
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
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1.0)\n"
            "print(json.dumps({'killed':pids,'remaining':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    def file_shas(self, paths):
        script = (
            "import json,os,hashlib\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "out={}\n"
            "for p in %r:\n"
            "    out[p]={'exists':os.path.exists(p),'sha256':sha(p) if os.path.exists(p) else None,"
            "'size':(os.path.getsize(p) if os.path.exists(p) else None)}\n"
            "print(json.dumps(out))\n"
        ) % (list(paths),)
        return self.remote_py(script)

    # ---------- managed application / deployment journal readback ----------
    def app_state(self, app_id, unit, binary_path, config_path, shas=(), deployments_limit=30):
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
            "'mode':oct(st.st_mode & 0o7777),'size':st.st_size,'mtime':st.st_mtime,'uid':st.st_uid}\n"
            "    if st.st_size<10485760 and not os.path.islink(p):\n"
            "        d['sha256']=sha(p)\n"
            "    if os.path.islink(p):\n"
            "        d['target']=os.readlink(p)\n"
            "    return d\n"
            "out={'at_utc':%(now)r}\n"
            "out['binary']=finfo(bp)\n"
            "out['config']=finfo(cp)\n"
            "up='/etc/systemd/system/'+unit\n"
            "out['unit_file_path']=up\n"
            "out['unit_file']=finfo(up)\n"
            "wants='/etc/systemd/system/multi-user.target.wants/'+unit\n"
            "out['enable_symlink']=finfo(wants)\n"
            "r=subprocess.run(['systemctl','show','--no-pager',"
            "'--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath,NRestarts',unit],"
            "capture_output=True,text=True)\n"
            "out['unit_state']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
            "pid=out['unit_state'].get('MainPID')\n"
            "if pid and pid!='0':\n"
            "    try:\n"
            "        out['main_process']={'exe':os.readlink('/proc/'+pid+'/exe'),"
            "'argv':open('/proc/'+pid+'/cmdline','rb').read().decode('utf-8','replace').split('\\0')}\n"
            "    except Exception as e:\n"
            "        out['main_process']={'error':type(e).__name__}\n"
            "adir=os.path.join(data,'applications')\n"
            "out['backup_dir']={'path':adir,'lexists':os.path.lexists(adir),'is_dir':os.path.isdir(adir),"
            "'is_symlink':os.path.islink(adir)}\n"
            "if os.path.islink(adir):\n"
            "    out['backup_dir']['target']=os.readlink(adir)\n"
            "if os.path.isdir(adir):\n"
            "    ent=[]\n"
            "    for n in sorted(os.listdir(adir)):\n"
            "        p=os.path.join(adir,n)\n"
            "        ent.append({'name':n,'info':finfo(p)})\n"
            "    out['backup_entries']=ent\n"
            "    out['backup_dir_lsattr']=subprocess.run(['lsattr','-d',adir],capture_output=True,text=True).stdout.strip()\n"
            "    out['backup_dir_statvfs']=dict(zip(('bsize','frsize','blocks','bfree','bavail'),os.statvfs(adir)[:5])) if hasattr(os,'statvfs') else None\n"
            "else:\n"
            "    out['backup_entries']=[]\n"
            "out['owned_mounts']=[l for l in open('/proc/mounts') if adir in l]\n"
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
            "    out['local_apps']=[{'id':r[0],'revision':r[1],'definition':json.loads(r[2]) if r[2] else None} "
            "for r in c.execute('SELECT id,revision,definition_json FROM applications')]\n"
            "    out['artifact_rows']=[{'sha256':r[0],'path':r[1],'downloaded_at':r[2]} for r in "
            "c.execute('SELECT sha256,path,downloaded_at FROM artifacts')]\n"
            "    out['global_revision']=c.execute('SELECT global_revision FROM sync_state WHERE id=1').fetchone()[0]\n"
            "    out['identity']={k:v for k,v in c.execute(\"SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')\")}\n"
            "    out['watched_sha_files']={}\n"
            "    for s in shas:\n"
            "        fp=os.path.join(data,'artifacts',s)\n"
            "        out['watched_sha_files'][s]=finfo(fp)\n"
            "except Exception as e:\n"
            "    out['db_error']=type(e).__name__+':'+str(e)[:200]\n"
            "print(json.dumps(out))\n"
        ) % {'data': self.data, 'app': app_id, 'unit': unit, 'bp': binary_path, 'cp': config_path,
             'shas': list(shas), 'now': now_utc(), 'limit': deployments_limit}
        return self.remote_py(script)

    def wait_deployment_phase(self, phase, timeout=60, interval=0.4, unit='', binary_path='/', config_path='/'):
        """Poll the owned deployments journal until a row reaches `phase`; returns sampled phases."""
        deadline = time.time() + timeout
        samples = []
        while time.time() < deadline:
            st = self.app_state('', unit, binary_path, config_path)
            if 'db_error' in st:
                raise RuntimeError('agent_db_probe_error: %s' % st['db_error'])
            rows = st.get('deployments') or []
            if rows:
                cur = rows[0]['phase']
                if not samples or samples[-1] != cur:
                    samples.append(cur)
                if cur == phase:
                    return {'reached': True, 'samples': samples, 'row': rows[0]}
            time.sleep(interval)
        return {'reached': False, 'samples': samples}

    def log_tail(self, max_bytes=40000):
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

    # ---------- owned backup-dir seams (host mutation, owned path only) ----------
    def backup_seam(self, action, extra=None):
        """action in: status | rm_dir | mk_dangling_symlink | mk_regular_file | mkdir
                    | chattr_plus_i | chattr_minus_i | mount_tmpfs | umount | lsattr."""
        script = (
            "import json,os,subprocess,shutil\n"
            "adir=%(adir)r; action=%(action)r; extra=json.loads(%(extra)r) or {}\n"
            "out={'action':action,'dir':adir}\n"
            "def run(cmd):\n"
            "    r=subprocess.run(cmd,capture_output=True,text=True)\n"
            "    return {'rc':r.returncode,'out':r.stdout.strip()[:300],'err':r.stderr.strip()[:300]}\n"
            "if action in ('rm_dir','mk_dangling_symlink','mk_regular_file','mkdir'):\n"
            "    if os.path.islink(adir): os.unlink(adir)\n"
            "    elif os.path.isdir(adir): shutil.rmtree(adir)\n"
            "    elif os.path.lexists(adir): os.unlink(adir)\n"
            "if action=='rm_dir':\n"
            "    pass\n"
            "elif action=='mk_dangling_symlink':\n"
            "    out['result']=run(['ln','-s','/nonexistent-sysb11-target',adir])\n"
            "elif action=='mk_regular_file':\n"
            "    open(adir,'w').write('sysb11 backup dir seam\\n')\n"
            "elif action=='mkdir':\n"
            "    os.makedirs(adir, mode=0o755, exist_ok=True)\n"
            "elif action=='chattr_plus_i':\n"
            "    out['result']=run(['chattr','+i',adir])\n"
            "elif action=='chattr_minus_i':\n"
            "    out['result']=run(['chattr','-i',adir])\n"
            "elif action=='mount_tmpfs':\n"
            "    os.makedirs(adir, mode=0o755, exist_ok=True)\n"
            "    out['result']=run(['mount','-t','tmpfs','-o',extra.get('opts','size=128k,mode=0755'),'tmpfs',adir])\n"
            "elif action=='umount':\n"
            "    out['result']=run(['umount',adir])\n"
            "elif action=='lsattr':\n"
            "    out['result']=run(['lsattr','-d',adir])\n"
            "out['after']={'lexists':os.path.lexists(adir),'is_dir':os.path.isdir(adir),"
            "'is_symlink':os.path.islink(adir),'target':os.readlink(adir) if os.path.islink(adir) else None,\n"
            "    'mode':(oct(os.lstat(adir).st_mode & 0o7777) if os.path.lexists(adir) else None)}\n"
            "out['mounts']=[l.strip() for l in open('/proc/mounts') if adir in l]\n"
            "out['lsattr']=run(['lsattr','-d',adir])\n"
            "print(json.dumps(out))\n"
        ) % {'adir': self.data + '/applications', 'action': action, 'extra': json.dumps(extra or {})}
        return self.remote_py(script)

    # ---------- owned managed-unit cleanup (host side) ----------
    def unit_cleanup(self, unit, binary_path='', config_path='', opt_root=''):
        script = (
            "import json,os,subprocess\n"
            "unit=%(unit)r; bp=%(bp)r; cp=%(cp)r; opt=%(opt)r\n"
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
            "for p in (bp,cp):\n"
            "    if p and os.path.lexists(p):\n"
            "        os.remove(p)\n"
            "if opt and os.path.isdir(opt):\n"
            "    import shutil; shutil.rmtree(opt)\n"
            "out['remaining_unit_exists']=os.path.lexists('/etc/systemd/system/'+unit) if unit else None\n"
            "print(json.dumps(out))\n"
        ) % {'unit': unit, 'bp': binary_path, 'cp': config_path, 'opt': opt_root}
        return self.remote_py(script)

    # ---------- host baseline / final oracle ----------
    def host_baseline(self):
        script = (
            "import json,os,subprocess,hashlib\n"
            "def sha(p):\n"
            "    try:\n"
            "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
            "    except Exception as e:\n"
            "        return 'ERR:'+type(e).__name__\n"
            "prefix=%(prefix)r; root=%(root)r; opt=%(opt)r\n"
            "out={}\n"
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
            "out['nodesteer_unit_count']=len([l for l in sy if 'nodesteer' in l])\n"
            "out['etc_systemd_total']=len(os.listdir('/etc/systemd/system'))\n"
            "print(json.dumps(out))\n"
        ) % {'prefix': 'qa-sysb11-%s' % self.nonce, 'root': self.root, 'opt': self.opt_root,
             'bin': AGENT_BIN, 'prot': PROTECTED_UNIT}
        return self.remote_py(script)

    # ---------- teardown ----------
    def remove(self):
        script = (
            "import json,os,signal,shutil,subprocess,time\n"
            "root=%(root)r; cfg=%(cfg)r; agent=%(bin)r; opt=%(opt)r; adir=%(adir)r\n"
            "def run(cmd):\n"
            "    r=subprocess.run(cmd,capture_output=True,text=True)\n"
            "    return {'rc':r.returncode,'out':r.stdout.strip()[:200],'err':r.stderr.strip()[:200]}\n"
            "out={}\n"
            "if os.path.isdir(adir):\n"
            "    run(['chattr','-i',adir])\n"
            "mnt=[l.split()[1] for l in open('/proc/mounts') if adir in l]\n"
            "if mnt:\n"
            "    out['umount']=run(['umount',adir])\n"
            "def matched(pattern):\n"
            "    p=subprocess.run(['pgrep','-f',pattern],capture_output=True,text=True)\n"
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
            "pids=matched(cfg)\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGTERM)\n"
            "end=time.time()+12\n"
            "while time.time()<end and matched(cfg):\n"
            "    time.sleep(0.5)\n"
            "left=matched(cfg)\n"
            "for pid in left:\n"
            "    try: os.kill(pid, signal.SIGKILL)\n"
            "    except Exception: pass\n"
            "time.sleep(0.5)\n"
            "out['agent_signalled']=pids; out['agent_remaining']=matched(cfg)\n"
            "for p in (root,opt):\n"
            "    if os.path.isdir(p):\n"
            "        shutil.rmtree(p, ignore_errors=True)\n"
            "out['root_removed']=not os.path.exists(root)\n"
            "out['opt_removed']=not os.path.exists(opt)\n"
            "out['owned_mounts_after']=[l.strip() for l in open('/proc/mounts') if root in l or opt in l]\n"
            "out['owned_units_after']=[l.strip() for l in subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],capture_output=True,text=True).stdout.splitlines() if 'qa-sysb11' in l]\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'cfg': self.config, 'bin': AGENT_BIN, 'opt': self.opt_root,
             'adir': self.data + '/applications'}
        return self.remote_py(script, timeout=150)
