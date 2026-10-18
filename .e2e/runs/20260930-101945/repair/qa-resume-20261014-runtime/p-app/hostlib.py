"""Owned child Agents + owned host fixtures on repair native-4 (192.168.100.247).

The exact repair Agent binary is started as a plain background process (no systemd
unit) under an owned 0700 root with an owned 0600 config.  It never touches
`nodesteer-agent.service` or `nodesteer-agent-repair-20260930-101945-native-4.service`.

Owned host side effects created by the product (systemd unit, binary, config,
backup dir, artifact cache, transfer destinations) always live under owned,
nonce-prefixed paths and are removed again.

Probes are read-only except the explicitly named owned-path helpers (chattr /
mount seams on owned directories, starting/stopping the owned child).
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
PRIMARY_UNIT = 'nodesteer-agent.service'


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# --------------------------------------------------------------------------
# remote helper scripts uploaded into each owned child root
# --------------------------------------------------------------------------
PHASEWATCH = r'''
import hashlib, json, os, signal, sqlite3, subprocess, sys, time
name, mode, target, row_id, after_id, timeout = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], float(sys.argv[6])
db, out, unit, binpath, cfg, agent = sys.argv[7], sys.argv[8], sys.argv[9], sys.argv[10], sys.argv[11], sys.argv[12]
row_id = row_id or None
after_id = after_id or None
start = time.monotonic()
f = open(out, 'a')

def pids():
    p = subprocess.run(['pgrep', '-f', cfg], capture_output=True, text=True)
    res = []
    for tok in p.stdout.split():
        if not tok.isdigit():
            continue
        pid = int(tok)
        try:
            exe = os.readlink('/proc/%d/exe' % pid)
            args = open('/proc/%d/cmdline' % pid, 'rb').read().split(b'\0')
        except Exception:
            continue
        if exe == agent and cfg.encode() in args:
            res.append(pid)
    return res

def sha(p):
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as fh:
            for chunk in iter(lambda: fh.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception as exc:
        return 'ERR:' + type(exc).__name__

def active():
    r = subprocess.run(['systemctl', 'is-active', unit], capture_output=True, text=True)
    return r.stdout.strip()

def snap(phase, rid):
    d = os.path.dirname(binpath)
    tmp = sorted([n for n in os.listdir(d) if n.startswith('.tmp-')]) if os.path.isdir(d) else None
    return {'t': round(time.monotonic() - start, 3), 'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'phase': phase, 'row': rid, 'binary_sha256': sha(binpath), 'unit_active': active(), 'tmp_entries': tmp}

def emit(obj):
    f.write(json.dumps(obj) + '\n')
    f.flush()
    os.fsync(f.fileno())

last = None
lastrow = None
con = sqlite3.connect('file:' + db + '?mode=ro', uri=True, timeout=2)
con.execute('PRAGMA busy_timeout=2000')
con.execute('PRAGMA query_only=ON')
deadline = time.time() + timeout
while time.time() < deadline:
    row = None
    try:
        row = con.execute('SELECT id, phase FROM deployments ORDER BY started_at DESC LIMIT 1').fetchone()
        if row_id:
            r2 = con.execute('SELECT id, phase FROM deployments WHERE id = ?', (row_id,)).fetchone()
            if r2:
                row = r2
    except Exception as exc:
        row = None
    if row:
        rid, ph = row[0], row[1]
        if row_id or after_id is None or rid != after_id:
            if rid != lastrow:
                lastrow = rid
                last = None
                emit({'event': 'tracking_row', 'row': rid, 't': round(time.monotonic() - start, 3)})
            if ph != last:
                last = ph
                emit(snap(ph, rid))
            if mode == 'kill' and target and ph == target:
                k = pids()
                for pid in k:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except Exception:
                        pass
                emit({'event': 'killed', 'mode': mode, 'target_phase': target, 'phase_at_kill': ph,
                      'killed_pids': k, 'remaining': pids(), 'row': rid})
                break
            if ph == 'DONE':
                emit({'event': 'reached_done', 'row': rid})
                break
    time.sleep(0.001)
emit({'event': 'watcher_exit', 'last_phase': last, 'row': lastrow})
f.close()
try:
    con.close()
except Exception:
    pass
print(json.dumps({'ok': True, 'last_phase': last, 'row': lastrow}))
'''

NETSAMPLE = r'''
import json, re, subprocess, sys, time
out, secs, iv = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
f = open(out, 'a')
start = time.monotonic()
end = start + secs
while time.monotonic() < end:
    r = subprocess.run(['ss', '-tinp', 'state', 'established'], capture_output=True, text=True)
    recs = []
    cur = None
    for line in r.stdout.splitlines():
        if line[:1] in (' ', '\t'):
            if cur is not None:
                cur['info'] += line.strip()
        else:
            if cur:
                recs.append(cur)
            cur = {'head': line.strip(), 'info': ''}
    if cur:
        recs.append(cur)
    socks = []
    for rec in recs:
        if ':18080' not in rec['head']:
            continue
        addrs = re.findall(r'\d+\.\d+\.\d+\.\d+:\d+', rec['head'])
        peer = next((a for a in addrs if a.endswith(':18080')), None)
        local = next((a for a in addrs if a != peer), None)
        m = re.search(r'pid=(\d+)', rec['head'])
        br = re.search(r'bytes_received:(\d+)', rec['info'])
        bs = re.search(r'bytes_sent:(\d+)', rec['info'])
        socks.append({'local': local, 'peer': peer,
                      'pid': m.group(1) if m else None,
                      'bytes_received': int(br.group(1)) if br else None,
                      'bytes_sent': int(bs.group(1)) if bs else None})
    f.write(json.dumps({'t': round(time.monotonic() - start, 3),
                        'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'sockets': socks}) + '\n')
    f.flush()
    time.sleep(iv)
f.close()
print(json.dumps({'ok': True}))
'''

DIRWATCH = r'''
import json, os, sys, time
out, secs, iv, path = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
f = open(out, 'a')
start = time.monotonic()
end = start + secs
first = True
last = None
while time.monotonic() < end:
    ent = []
    try:
        for n in sorted(os.listdir(path)):
            p = os.path.join(path, n)
            try:
                st = os.lstat(p)
                ent.append({'name': n, 'size': st.st_size, 'mode': oct(st.st_mode & 0o7777),
                            'is_symlink': os.path.islink(p)})
            except Exception as exc:
                ent.append({'name': n, 'error': type(exc).__name__})
    except Exception as exc:
        ent = [{'ls_error': type(exc).__name__}]
    key = json.dumps(ent, sort_keys=True)
    if key != last or first:
        f.write(json.dumps({'t': round(time.monotonic() - start, 3),
                            'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                            'entries': ent}) + '\n')
        f.flush()
        last = key
        first = False
    time.sleep(iv)
f.close()
print(json.dumps({'ok': True}))
'''


class OwnedChild:
    def __init__(self, nonce, tag='', node_id='', agent_id='', node_name='', node_ip='',
                 token='', deployment_mode='native'):
        self.nonce = nonce
        self.tag = tag
        self.prefix = 'qa-papp-%s%s-' % (nonce, tag)
        self.root = '/var/lib/nodesteer-qa-papp-%s%s' % (nonce, tag)
        self.opt_root = '/opt/qa-papp-%s%s' % (nonce, tag)
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
    def _ssh(self, script, timeout=60):
        cp = subprocess.run(SSH + ['python3', '-'], input=script, capture_output=True,
                            text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def remote_py(self, script, timeout=90, retries=2):
        last = None
        for _ in range(retries):
            rc, out, err = self._ssh(script, timeout=timeout)
            if rc != 0:
                last = 'remote_py_failed: %s' % (err[-400:] or out[-400:])
                time.sleep(1.0)
                continue
            lines = [l for l in out.splitlines() if l.strip()]
            if not lines:
                last = 'remote_py_empty_output'
                time.sleep(1.0)
                continue
            return json.loads(lines[-1])
        raise RuntimeError(last)

    def remote_raw(self, script, timeout=90):
        return self._ssh(script, timeout=timeout)

    def sh(self, cmd, timeout=90):
        """Run a plain shell command on the owned host (never fed through python3)."""
        return self._ssh_shell(cmd, timeout=timeout)

    def _ssh_shell(self, cmd, timeout=90):
        cp = subprocess.run(SSH + [cmd], capture_output=True, text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

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
            "w='/etc/systemd/system/multi-user.target.wants'\n"
            "out['etc_systemd_owned_wants']=[n for n in sorted(os.listdir(w)) if %(prefix)r in n] if os.path.isdir(w) else []\n"
            "out['owned_mounts']=[l.strip() for l in open('/proc/mounts') if %(root)r in l or %(opt)r in l]\n"
            "sy=subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],"
            "capture_output=True,text=True).stdout.splitlines()\n"
            "out['owned_systemd_units']=[l.strip() for l in sy if %(prefix)r in l]\n"
            "out['opt_entries']=sorted(os.listdir(%(opt)r)) if os.path.isdir(%(opt)r) else []\n"
            "print(json.dumps(out))\n"
        ) % {'root': self.root, 'opt': self.opt_root, 'cfg': self.config, 'bin': AGENT_BIN,
             'prim': PRIMARY_UNIT, 'prot': PROTECTED_UNIT, 'unit': unit,
             'prefix': 'qa-papp-%s%s' % (self.nonce, self.tag)}
        return self.remote_py(script)

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
            "for n,b in (('phasewatch.py',PW),('netsample.py',NS),('dirwatch.py',DW)):\n"
            "    p=os.path.join(root,n)\n"
            "    with open(p,'w') as f: f.write(b)\n"
            "    os.chmod(p,0o600)\n"
            "print('CONFIG_WRITTEN')\n"
        ) % (self.root, self.config, self.data, self.log, yaml_text)
        # embed helper scripts through a second, non-interpolated channel
        prep = prep.replace("PW", json.dumps(PHASEWATCH)).replace("NS", json.dumps(NETSAMPLE)).replace("DW", json.dumps(DIRWATCH))
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

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    def kill_child(self):
        """SIGKILL the owned child agent (identity-guarded) - crash-recovery seam."""
        script = (
            "import json,os,signal,subprocess,time\n"
            "cfg=%r; agent=%r\n" + self._matched_script() +
            "pids=matched()\n"
            "for pid in pids:\n"
            "    os.kill(pid, signal.SIGKILL)\n"
            "time.sleep(1.0)\n"
            "print(json.dumps({'killed':pids,'remaining':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    def process_alive(self):
        script = (
            "import json,os,subprocess\n"
            "cfg=%r; agent=%r\n" + self._matched_script() +
            "print(json.dumps({'pids':matched()}))\n"
        ) % (self.config, AGENT_BIN)
        return self.remote_py(script)

    # ---------- deployment journal / host state readback ----------
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
            "out={'at_utc':%(now)r}\n"
            "out['binary']=finfo(bp)\n"
            "out['config']=finfo(cp)\n"
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
            "pid=out['unit_state'].get('MainPID')\n"
            "if pid and pid!='0':\n"
            "    try:\n"
            "        out['main_process']={'exe':os.readlink('/proc/'+pid+'/exe'),"
            "'argv':open('/proc/'+pid+'/cmdline','rb').read().decode('utf-8','replace').split('\\0')[:8]}\n"
            "    except Exception as e:\n"
            "        out['main_process']={'error':type(e).__name__}\n"
            "out['unit_file_content']=None\n"
            "out['unit_file_text']=None\n"
            "try:\n"
            "    t=open(up).read()\n"
            "    out['unit_file_text']=t\n"
            "    out['unit_file_content']={'len':len(t),'has_v1':'--profile=v1' in t,'has_v2':'--profile=v2' in t}\n"
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
            "        defn={k:(d or {}).get(k) for k in ('version','artifact_id','artifact_url','artifact_sha256',"
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
             'shas': list(shas), 'now': now_utc(), 'limit': deployments_limit}
        return self.remote_py(script)

    def deployments(self):
        script = (
            "import json,sqlite3,os\n"
            "db=os.path.join(%r,'state.db')\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5)\n"
            "print(json.dumps([{'id':r[0],'application_id':r[1],'from_version':r[2],'to_version':r[3],'phase':r[4],"
            "'backup_path':r[5],'backup_config_path':r[6],'backup_unit_path':r[7],'started_at':r[8],'updated_at':r[9]} "
            "for r in c.execute('SELECT id,application_id,from_version,to_version,phase,backup_path,backup_config_path,"
            "backup_unit_path,started_at,updated_at FROM deployments ORDER BY started_at DESC LIMIT 40')]))\n"
        ) % (self.data,)
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

    # ---------- owned-path seams (host mutation on owned paths only) ----------
    def seam(self, path, action, extra=None):
        """action: status | rm | mkdir | symlink | regular_file | chattr_plus_i
                   | chattr_minus_i | mount_tmpfs | umount | lsattr | chown | chmod | write_file"""
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
            "elif action=='symlink':\n"
            "    out['result']=run(['ln','-s',extra.get('target','/nonexistent-papp-target'),p])\n"
            "elif action=='regular_file':\n"
            "    open(p,'w').write(extra.get('content','papp seam\\n'))\n"
            "elif action=='write_file':\n"
            "    os.makedirs(os.path.dirname(p),mode=0o755,exist_ok=True)\n"
            "    open(p,'wb').write(bytes.fromhex(extra['hex'])); os.chmod(p,int(extra.get('mode',0o644)))\n"
            "elif action=='chattr_plus_i':\n"
            "    out['result']=run(['chattr','+i',p])\n"
            "elif action=='chattr_minus_i':\n"
            "    out['result']=run(['chattr','-i',p])\n"
            "elif action=='lsattr':\n"
            "    out['result']=run(['lsattr','-d',p])\n"
            "elif action=='chown':\n"
            "    out['result']=run(['chown',extra['spec'],p])\n"
            "elif action=='chmod':\n"
            "    out['result']=run(['chmod',extra['spec'],p])\n"
            "elif action=='mount_tmpfs':\n"
            "    os.makedirs(p, mode=0o755, exist_ok=True)\n"
            "    out['result']=run(['mount','-t','tmpfs','-o',extra.get('opts','size=64k,mode=0755'),'tmpfs',p])\n"
            "elif action=='umount':\n"
            "    out['result']=run(['umount',p])\n"
            "if os.path.lexists(p):\n"
            "    st=os.lstat(p)\n"
            "    out['after']={'lexists':True,'is_dir':os.path.isdir(p),'is_symlink':os.path.islink(p),"
            "'mode':oct(st.st_mode & 0o7777),'uid':st.st_uid,'gid':st.st_gid,"
            "'target':os.readlink(p) if os.path.islink(p) else None,'size':st.st_size}\n"
            "else:\n"
            "    out['after']={'lexists':False}\n"
            "out['mounts']=[l.strip() for l in open('/proc/mounts') if p in l]\n"
            "out['lsattr']=run(['lsattr','-d',p])['out']\n"
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
        ) % {'unit': unit, 'bp': binary_path, 'cp': config_path, 'opt': opt_root, 'extra': list(extra_files)}
        return self.remote_py(script)

    # ---------- async remote watchers ----------
    def _start_async(self, script_name, args, out_name):
        # the whole background group must be isolated inside a subshell, otherwise ssh keeps the
        # channel open until the detached process exits.
        cmd = ('cd %s && (setsid nohup python3 %s %s >/dev/null 2>&1 </dev/null &) ; echo started'
               % (self.root, script_name, ' '.join(_shq(a) for a in args)))
        rc, out, err = self._ssh_shell(cmd, timeout=30)
        if rc != 0 or 'started' not in out:
            raise RuntimeError('async_start_failed:%s' % (err[-200:] or out[-200:]))
        return {'out': out_name, 'stdout': out.strip()}

    def start_watcher(self, name, unit, binary_path, row_id='', after_id='', mode='observe',
                      target='', timeout=120):
        out_name = '%s/watch-%s.jsonl' % (self.root, name)
        self._ssh_shell('rm -f %s' % _shq(out_name), timeout=20)
        args = [name, mode, target, row_id, after_id, str(timeout), self.data + '/state.db',
                out_name, unit, binary_path, self.config, AGENT_BIN]
        res = self._start_async('phasewatch.py', args, out_name)
        res['name'] = name
        res['mode'] = mode
        res['target'] = target
        return res

    def read_watcher(self, name, wait_exit=True, timeout=200):
        out_name = '%s/watch-%s.jsonl' % (self.root, name)
        deadline = time.time() + timeout
        rows = []
        while True:
            rc, out, err = self._ssh_shell('cat %s 2>/dev/null || true' % _shq(out_name), timeout=30)
            rows = []
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    rows.append({'_unparsed': line[:200]})
            done = any(r.get('event') == 'watcher_exit' for r in rows)
            if done or not wait_exit or time.time() > deadline:
                break
            time.sleep(0.5)
        phases = [r.get('phase') for r in rows if r.get('phase')]
        return {'out': out_name, 'rows': rows, 'phase_sequence': phases,
                'killed': [r for r in rows if r.get('event') == 'killed'],
                'exited': any(r.get('event') == 'watcher_exit' for r in rows)}

    def sample_net(self, name, seconds, interval=0.05):
        out_name = '%s/net-%s.jsonl' % (self.root, name)
        self._ssh_shell('rm -f %s' % _shq(out_name), timeout=20)
        return self._start_async('netsample.py', [out_name, str(seconds), str(interval)], out_name)

    def read_net(self, name):
        out_name = '%s/net-%s.jsonl' % (self.root, name)
        rc, out, err = self._ssh_shell('cat %s 2>/dev/null || true' % _shq(out_name), timeout=30)
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        return rows

    def sample_dir(self, name, path, seconds, interval=0.01):
        out_name = '%s/dir-%s.jsonl' % (self.root, name)
        self._ssh_shell('rm -f %s' % _shq(out_name), timeout=20)
        return self._start_async('dirwatch.py', [out_name, str(seconds), str(interval), path], out_name)

    def read_dir(self, name):
        out_name = '%s/dir-%s.jsonl' % (self.root, name)
        rc, out, err = self._ssh_shell('cat %s 2>/dev/null || true' % _shq(out_name), timeout=30)
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
        return rows

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
            "out['global_owned_prefix_scan']=[n for n in sorted(os.listdir('/etc/systemd/system')) if 'qa-papp' in n]\n"
            "print(json.dumps(out))\n"
        ) % {'prefix': 'qa-papp-%s%s' % (self.nonce, self.tag), 'root': self.root, 'opt': self.opt_root,
             'bin': AGENT_BIN, 'prot': PROTECTED_UNIT}
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
             'adir': self.data + '/applications', 'prefix': 'qa-papp-%s%s' % (self.nonce, self.tag)}
        return self.remote_py(script, timeout=180)


def _shq(value):
    import shlex
    return shlex.quote(str(value))
