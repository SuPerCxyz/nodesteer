"""Owned child Agent + owned host fixtures on repair native-2 (192.168.100.206) for sys-b12.

Runs the exact repair Agent binary as a plain background process (no systemd unit) under an
owned 0700 root with an owned 0600 config.  It never touches `nodesteer-agent.service` or
`nodesteer-agent-repair-20260930-101945-native-2.service`.

sys-b12 fixtures are always nonce-prefixed (`qa-sysb12-<nonce>-*`) and every host side effect
(owned roots, owned tmpfs mount, owned symlinks, chattr flags) is released again.

Remote probes are read-only except the explicitly named owned-path helpers.
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


def render(template, **tokens):
    out = template
    for key, value in tokens.items():
        out = out.replace('__%s__' % key, repr(value))
    return out


class OwnedChild:
    """One owned Agent process + its owned host footprint."""

    def __init__(self, nonce, suffix='', node_id='', agent_id='', node_name='', node_ip='',
                 token='', deployment_mode='native', allowlist=()):
        self.nonce = nonce
        self.suffix = suffix
        tag = 'qa-sysb12-%s' % nonce + (('-') + suffix if suffix else '')
        self.tag = tag
        self.root = '/var/lib/nodesteer-%s' % tag
        self.opt_root = '/opt/%s' % tag
        self.config = self.root + '/child.yaml'
        self.data = self.root + '/data'
        self.log = self.root + '/child.log'
        self.node_id = node_id
        self.agent_id = agent_id
        self.node_name = node_name
        self.node_ip = node_ip
        self.deployment_mode = deployment_mode
        self.allowlist = list(allowlist)
        self._token = token
        self.hub_url = ''

    # ---------- remote plumbing ----------
    def _ssh(self, script, timeout=90):
        cp = subprocess.run(SSH + ['python3', '-'], input=script,
                            capture_output=True, text=True, timeout=timeout)
        return cp.returncode, cp.stdout, cp.stderr

    def remote_py(self, script, timeout=90):
        rc, out, err = self._ssh(script, timeout=timeout)
        if rc != 0:
            raise RuntimeError('remote_py_failed: %s' % (err[-500:] or out[-500:]))
        lines = [l for l in out.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError('remote_py_empty_output')
        return json.loads(lines[-1])

    def remote_raw(self, script, timeout=90):
        return self._ssh(script, timeout=timeout)

    # ---------- guards / inventory ----------
    def host_baseline(self):
        script = render(HOST_BASELINE, tag=self.tag, root=self.root, opt=self.opt_root,
                        bin=AGENT_BIN, prot=PROTECTED_UNIT, prim=PRIMARY_UNIT)
        return self.remote_py(script)

    # ---------- install / start / stop ----------
    def write_config_and_start(self, hub_ws_url):
        self.hub_url = hub_ws_url
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
        ) % (hub_ws_url, self._token, self.agent_id, self.node_name, self.node_ip,
             self.deployment_mode, self.data)
        if self.allowlist:
            yaml_text += 'host_path_allowlist: [%s]\n' % ', '.join('"%s"' % p for p in self.allowlist)
        prep = render(PREP_CONFIG, root=self.root, cfg=self.config, data=self.data,
                      log=self.log, body=yaml_text)
        rc, out, err = self._ssh(prep, timeout=60)
        if rc != 0 or 'CONFIG_WRITTEN' not in out:
            raise RuntimeError('child_config_write_failed: %s' % (err[-300:] or out[-300:]))
        return self.start()

    def start(self):
        script = render(START_CHILD, cfg=self.config, log=self.log, agent=AGENT_BIN)
        return self.remote_py(script)

    def stop(self):
        script = render(STOP_CHILD, cfg=self.config, agent=AGENT_BIN)
        return self.remote_py(script)

    def restart(self):
        stopped = self.stop()
        time.sleep(1.0)
        started = self.start()
        return {'stop': stopped, 'start': started}

    def kill_child(self):
        script = render(KILL_CHILD, cfg=self.config, agent=AGENT_BIN)
        return self.remote_py(script)

    def child_pid(self):
        script = render(CHILD_PID, cfg=self.config, agent=AGENT_BIN)
        return self.remote_py(script)

    def log_tail(self, max_bytes=40000):
        script = render(LOG_TAIL, log=self.log, n=max_bytes)
        return self.remote_py(script)

    def tcp_check(self, host, port, timeout=5):
        """Verify the child can open a TCP connection to a harness listener."""
        script = ('import json,socket\n'
                  's=socket.socket(); s.settimeout(%d)\n'
                  'try:\n'
                  '    s.connect((%r, %d)); out={"ok": True}\n'
                  'except Exception as e:\n'
                  '    out={"ok": False, "error": type(e).__name__+":"+str(e)[:120]}\n'
                  'finally:\n'
                  '    s.close()\n'
                  'print(json.dumps(out))\n') % (timeout, host, int(port))
        return self.remote_py(script)

    # ---------- filesystem / owned seam helpers ----------
    def probe(self, targets=(), dirs=(), freeform=''):
        """Read-only inventory of owned paths: info, devices, dir listings."""
        script = 'import json,os,hashlib,subprocess\n'
        script += 'def finfo(p):\n'
        script += '    if not os.path.lexists(p):\n'
        script += '        return {"lexists": False, "exists": False, "is_symlink": False}\n'
        script += '    st = os.lstat(p)\n'
        script += '    d = {"lexists": True, "exists": os.path.exists(p), "is_symlink": os.path.islink(p),'
        script += ' "is_dir": os.path.isdir(p), "mode": oct(st.st_mode & 0o7777), "size": st.st_size,'
        script += ' "uid": st.st_uid, "dev": st.st_dev, "mtime": st.st_mtime}\n'
        script += '    if os.path.islink(p):\n'
        script += '        d["target"] = os.readlink(p)\n'
        script += '        d["target_info"] = finfo(os.path.realpath(p))\n'
        script += '    elif os.path.isdir(p):\n'
        script += '        d["entries"] = sorted(os.listdir(p))[:200]\n'
        script += '    elif os.path.isfile(p) and st.st_size < 268435456:\n'
        script += '        d["sha256"] = hashlib.sha256(open(p, "rb").read()).hexdigest()\n'
        script += '    return d\n'
        script += 'def dinfo(p):\n'
        script += '    if not os.path.isdir(p):\n'
        script += '        return {"lexists": os.path.lexists(p), "is_dir": False}\n'
        script += '    st = os.stat(p)\n'
        script += '    out = {"lexists": True, "is_dir": True, "mode": oct(st.st_mode & 0o7777), "dev": st.st_dev}\n'
        script += '    try:\n'
        script += '        out["statvfs"] = dict(zip(("bsize","frsize","blocks","bfree","bavail"), os.statvfs(p)[:5]))\n'
        script += '    except Exception as e:\n'
        script += '        out["statvfs_error"] = type(e).__name__\n'
        script += '    out["lsattr"] = subprocess.run(["lsattr","-d",p],capture_output=True,text=True).stdout.strip()\n'
        script += '    out["entries"] = sorted(os.listdir(p))[:400]\n'
        script += '    return out\n'
        script += 'out = {"at_utc": __NOW__, "files": {}, "dirs": {}, "tmp_dev": os.stat("/tmp").st_dev,'
        script += ' "root_dev": os.stat("/").st_dev}\n'
        script += 'for p in __TARGETS__:\n'
        script += '    out["files"][p] = finfo(p)\n'
        script += 'for p in __DIRS__:\n'
        script += '    out["dirs"][p] = dinfo(p)\n'
        script += 'out["mounts"] = [l.strip() for l in open("/proc/mounts") if __TAG__ in l]\n'
        if freeform:
            script += freeform + '\n'
        script += 'print(json.dumps(out))\n'
        script = script.replace('__NOW__', repr(now_utc()))
        script = script.replace('__TARGETS__', repr(list(targets)))
        script = script.replace('__DIRS__', repr(list(dirs)))
        script = script.replace('__TAG__', repr(self.tag))
        return self.remote_py(script)

    def agent_state(self, app_id='', unit='', binary_path='/', config_path='/'):
        """Read the owned Agent SQLite state (executions, deployments, applications, identity)."""
        script = render(AGENT_STATE, data=self.data, app=app_id, unit=unit,
                        bp=binary_path, cp=config_path, now=now_utc())
        return self.remote_py(script)

    def seam(self, action, path=None, extra=None):
        """Owned-path seam control: mkdir/rmdir/chattr/symlink/mount/umount/mkfile/hostop."""
        script = render(SEAM, action=action, path=path or '', extra=json.dumps(extra or {}),
                        tag=self.tag, now=now_utc())
        return self.remote_py(script, timeout=120)

    def sampler_start(self, watch_dirs, out_path, seconds=20):
        script_path = out_path + '.py'
        log_path = out_path + '.sampler.log'
        prog = render(SAMPLER_PROGRAM, dirs=list(watch_dirs), out=out_path, secs=seconds,
                      cfg=self.config, agent=AGENT_BIN)
        script = render(SAMPLER_START, prog=prog, out=out_path, script=script_path, log=log_path)
        rc, out, err = self._ssh(script, timeout=40)
        if rc != 0:
            return {'rc': rc, 'err': err[-300:]}
        body = [l for l in out.splitlines() if l.strip()]
        return {'rc': rc, 'result': json.loads(body[-1]) if body else None}

    def sampler_stop(self, out_path):
        script = 'import json,os,signal,subprocess,time\n'
        script += 'sp=%r\n' % (out_path + '.py')
        script += ('r=subprocess.run(["pgrep","-f",sp],capture_output=True,text=True)\n'
                   'pids=[int(x) for x in r.stdout.split() if x.isdigit()]\n'
                   'for pid in pids:\n'
                   '    try: os.kill(pid, signal.SIGTERM)\n'
                   '    except Exception: pass\n'
                   'time.sleep(0.5)\n'
                   'r2=subprocess.run(["pgrep","-f",sp],capture_output=True,text=True)\n'
                   'left=[int(x) for x in r2.stdout.split() if x.isdigit()]\n'
                   'for pid in left:\n'
                   '    try: os.kill(pid, signal.SIGKILL)\n'
                   '    except Exception: pass\n'
                   'time.sleep(0.3)\n'
                   'r3=subprocess.run(["pgrep","-f",sp],capture_output=True,text=True)\n'
                   'print(json.dumps({"signalled":pids,"remaining":[int(x) for x in r3.stdout.split() if x.isdigit()]}))\n')
        return self.remote_py(script)

    def sampler_fetch(self, out_path):
        script = render(SAMPLER_FETCH, out=out_path)
        return self.remote_py(script)

    def make_file(self, path, size, fill='zeros'):
        script = render(MAKE_FILE, path=path, size=size, fill=fill)
        return self.remote_py(script, timeout=180)

    def read_jsonl_tail(self, path, lines=40):
        script = render(JSONL_TAIL, path=path, n=lines)
        return self.remote_py(script)

    # ---------- teardown ----------
    def remove(self):
        script = render(REMOVE_CHILD, root=self.root, cfg=self.config, agent=AGENT_BIN,
                        opt=self.opt_root, tag=self.tag, adir=self.data + '/applications')
        return self.remote_py(script, timeout=180)


# ---------------------------------------------------------------- remote scripts
PREP_CONFIG = """import os
root=__root__; cfg=__cfg__; data=__data__; log=__log__; body=__body__
os.makedirs(root, mode=0o700, exist_ok=True); os.chmod(root,0o700)
os.makedirs(data, mode=0o700, exist_ok=True)
open(log,'a').close()
fd=os.open(cfg, os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0), 0o600)
with os.fdopen(fd,'w') as f: f.write(body)
print('CONFIG_WRITTEN')
"""

START_CHILD = """import json,os,subprocess,time
cfg=__cfg__; log=__log__; agent=__agent__
env=dict(os.environ)
for k in ('http_proxy','https_proxy','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','all_proxy'):
    env.pop(k, None)
env['NO_PROXY']='*'; env['no_proxy']='*'
p=subprocess.Popen(['setsid','nohup',agent,'--config',cfg],
                   stdout=open(log,'a'), stderr=subprocess.STDOUT,
                   stdin=subprocess.DEVNULL, start_new_session=True, env=env)
time.sleep(1.5)
q=subprocess.run(['pgrep','-af',cfg],capture_output=True,text=True)
print(json.dumps({'launcher_pid':p.pid,'owned_processes':[l for l in q.stdout.splitlines() if l.strip()]}))
"""

STOP_CHILD = """import json,os,signal,subprocess,time
cfg=__cfg__; agent=__agent__
def matched():
    r=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)
    res=[]
    for tok in r.stdout.split():
        if not tok.isdigit(): continue
        pid=int(tok)
        try:
            exe=os.readlink('/proc/%d/exe'%pid)
            args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')
        except Exception:
            continue
        if exe==agent and cfg.encode() in args: res.append(pid)
    return res
pids=matched()
for pid in pids: os.kill(pid, signal.SIGTERM)
time.sleep(2)
print(json.dumps({'signalled':pids,'remaining':matched()}))
"""

KILL_CHILD = """import json,os,signal,subprocess,time
cfg=__cfg__; agent=__agent__
def matched():
    r=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)
    res=[]
    for tok in r.stdout.split():
        if not tok.isdigit(): continue
        pid=int(tok)
        try:
            exe=os.readlink('/proc/%d/exe'%pid)
            args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')
        except Exception:
            continue
        if exe==agent and cfg.encode() in args: res.append(pid)
    return res
pids=matched()
for pid in pids: os.kill(pid, signal.SIGKILL)
time.sleep(1.0)
print(json.dumps({'killed':pids,'remaining':matched()}))
"""

CHILD_PID = """import json,os,subprocess
cfg=__cfg__; agent=__agent__
r=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)
out=[]
for tok in r.stdout.split():
    if not tok.isdigit(): continue
    pid=int(tok)
    try:
        exe=os.readlink('/proc/%d/exe'%pid)
        args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')
    except Exception:
        continue
    if exe==agent and cfg.encode() in args:
        out.append({'pid':pid,'argv':[a.decode('utf-8','replace') for a in args if a]})
print(json.dumps({'pids':out}))
"""

LOG_TAIL = """import json,os
p=__log__; n=__n__
data=b''
if os.path.isfile(p):
    sz=os.path.getsize(p)
    with open(p,'rb') as f:
        if sz>n: f.seek(sz-n)
        data=f.read()
print(json.dumps({'size':sz if os.path.isfile(p) else 0,'tail':data.decode('utf-8','replace')}))
"""

HOST_BASELINE = """import json,os,subprocess,hashlib
def sha(p):
    try: return hashlib.sha256(open(p,'rb').read()).hexdigest()
    except Exception as e: return 'ERR:'+type(e).__name__
def unit_state(u):
    r=subprocess.run(['systemctl','show','--no-pager',
        '--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath',u],
        capture_output=True,text=True)
    return dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
tag=__tag__; root=__root__; opt=__opt__
out={}
sy=subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],capture_output=True,text=True).stdout.splitlines()
out['owned_units']=[l.strip() for l in sy if 'qa-sysb12-' in l]
out['etc_systemd_owned_leftovers']=[n for n in sorted(os.listdir('/etc/systemd/system')) if 'qa-sysb12-' in n]
w='/etc/systemd/system/multi-user.target.wants'
out['etc_systemd_owned_wants']=[n for n in sorted(os.listdir(w)) if 'qa-sysb12-' in n] if os.path.isdir(w) else []
out['owned_mounts']=[l.strip() for l in open('/proc/mounts') if tag in l]
out['root_exists']=os.path.isdir(root)
out['opt_root_exists']=os.path.isdir(opt)
out['child_config_exists']=os.path.isfile(root+'/child.yaml')
p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)
lines=[l for l in p.stdout.splitlines() if l.strip()]
out['agent_process_table']=lines
out['owned_child_processes']=[l for l in lines if 'qa-sysb12-' in l]
out['agent_binary_sha256']=sha(__bin__)
out['primary_agent_unit']=unit_state(__prim__)
out['repair_agent_unit']=unit_state(__prot__)
out['etc_systemd_total']=len(os.listdir('/etc/systemd/system'))
out['opt_entries']=sorted(os.listdir('/opt'))
out['tmp_owned_entries']=[n for n in sorted(os.listdir('/tmp')) if 'qa-sysb12-' in n][:50]
out['etc_owned_entries']=[n for n in sorted(os.listdir('/etc')) if 'qa-sysb12-' in n][:50]
out['filesystem_devs']={'root':os.stat('/').st_dev,'tmp':os.stat('/tmp').st_dev,'opt':os.stat('/opt').st_dev}
mem={}
for line in open('/proc/meminfo'):
    parts=line.split()
    if parts[0] in ('MemTotal:','MemAvailable:'):
        mem[parts[0].rstrip(':')]=int(parts[1])
out['mem_kib']=mem
print(json.dumps(out))
"""

AGENT_STATE = """import json,os,sqlite3,subprocess,hashlib
data=__data__; app=__app__; unit=__unit__; bp=__bp__; cp=__cp__
def finfo(p):
    if not os.path.lexists(p): return {'lexists':False,'exists':False}
    st=os.lstat(p)
    d={'lexists':True,'exists':os.path.exists(p),'is_symlink':os.path.islink(p),'is_dir':os.path.isdir(p),'mode':oct(st.st_mode & 0o7777),'size':st.st_size,'dev':st.st_dev}
    if os.path.islink(p): d['target']=os.readlink(p)
    elif os.path.isdir(p): d['entries']=sorted(os.listdir(p))[:100]
    elif st.st_size<10485760: d['sha256']=hashlib.sha256(open(p,'rb').read()).hexdigest()
    return d
out={'at_utc':__now__}
out['binary']=finfo(bp); out['config']=finfo(cp)
up='/etc/systemd/system/'+unit if unit else ''
out['unit_file']=finfo(up) if unit else None
out['enable_symlink']=finfo('/etc/systemd/system/multi-user.target.wants/'+unit) if unit else None
if unit:
    r=subprocess.run(['systemctl','show','--no-pager',
        '--property=Id,LoadState,ActiveState,SubState,MainPID,UnitFileState,FragmentPath',unit],capture_output=True,text=True)
    out['unit_state']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)
else:
    out['unit_state']=None
db=os.path.join(data,'state.db')
try:
    c=sqlite3.connect('file:'+db+'?mode=ro',uri=True,timeout=5); c.execute('PRAGMA query_only=ON')
    out['db_quick_check']=c.execute('PRAGMA quick_check').fetchone()[0]
    out['executions']=[{'id':r[0],'task_id':r[1],'status':r[2],'application_id':r[3],'application_operation':r[4],
        'start_time':r[5],'end_time':r[6],'block_reason':r[7]}
        for r in c.execute('SELECT id,task_id,status,COALESCE(application_id,\\'\\'),COALESCE(application_operation,\\'\\'),'
                           'COALESCE(start_time,\\'\\'),COALESCE(end_time,\\'\\'),COALESCE(block_reason,\\'\\') '
                           'FROM executions ORDER BY start_time DESC LIMIT 40')]
    out['execution_count']=c.execute('SELECT COUNT(*) FROM executions').fetchone()[0]
    out['deployments']=[{'id':r[0],'application_id':r[1],'from_version':r[2],'to_version':r[3],'phase':r[4],
        'backup_path':r[5],'started_at':r[6],'updated_at':r[7]}
        for r in c.execute('SELECT id,application_id,from_version,to_version,phase,backup_path,started_at,updated_at '
                           'FROM deployments ORDER BY started_at DESC LIMIT 30')]
    out['unit_registry']=[{'application_id':r[0],'unit_name':r[1]} for r in
        c.execute('SELECT application_id,unit_name FROM unit_registry')]
    out['local_apps']=[{'id':r[0],'revision':r[1],'definition':json.loads(r[2]) if r[2] else None} for r in
        c.execute('SELECT id,revision,definition_json FROM applications')]
    out['artifact_rows']=[{'sha256':r[0],'path':r[1]} for r in c.execute('SELECT sha256,path FROM artifacts')]
    out['identity']={k:v for k,v in c.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')")}
except Exception as e:
    out['db_error']=type(e).__name__+':'+str(e)[:200]
print(json.dumps(out))
"""

SEAM = """import json,os,shutil,subprocess,time
action=__action__; path=__path__; extra=json.loads(__extra__)
out={'action':action,'path':path,'extra':extra,'at_utc':__now__}
def run(cmd):
    r=subprocess.run(cmd,capture_output=True,text=True)
    return {'rc':r.returncode,'out':r.stdout.strip()[:400],'err':r.stderr.strip()[:400]}
if action=='mkdir':
    os.makedirs(path, mode=int(extra.get('mode','0755'),8), exist_ok=True); out['result']={'ok':True}
elif action=='rm_rf':
    if os.path.islink(path): os.unlink(path)
    elif os.path.isdir(path): shutil.rmtree(path)
    elif os.path.lexists(path): os.unlink(path)
    out['result']={'ok':not os.path.lexists(path)}
elif action=='chattr_plus':
    out['result']=run(['chattr','+i',path])
elif action=='chattr_minus':
    out['result']=run(['chattr','-i',path])
elif action=='symlink':
    if os.path.lexists(path): os.unlink(path)
    os.symlink(extra['target'], path); out['result']={'target':extra['target']}
elif action=='mkfile':
    os.makedirs(os.path.dirname(path) or '/', exist_ok=True)
    size=int(extra.get('size',0)); content=extra.get('content')
    with open(path,'wb') as f:
        if content is not None: f.write(content.encode())
        elif extra.get('fill')=='urandom':
            left=size
            while left>0:
                n=min(left, 1048576); f.write(os.urandom(n)); left-=n
        elif size:
            f.truncate(size)
    out['result']={'size':os.path.getsize(path)}
elif action=='mount_tmpfs':
    os.makedirs(path, mode=0o755, exist_ok=True)
    out['result']=run(['mount','-t','tmpfs','-o',extra.get('opts','size=64m,mode=0755'),'tmpfs',path])
elif action=='umount':
    out['result']=run(['umount',path])
elif action=='df':
    out['result']=run(['df','-h',path])
    out['mounts']=[l.strip() for l in open('/proc/mounts') if path in l]
elif action=='lsattr':
    out['result']=run(['lsattr','-d',path])
elif action=='cmd':
    out['result']=run(extra['argv'])
else:
    out['error']='unknown action'
out['after']={'lexists':os.path.lexists(path),'is_dir':os.path.isdir(path),'is_symlink':os.path.islink(path),
              'target':os.readlink(path) if os.path.islink(path) else None}
print(json.dumps(out))
"""

SAMPLER_PROGRAM = """import json,os,subprocess,time,sys
dirs=__dirs__; out_path=__out__; secs=__secs__; cfg=__cfg__; agent=__agent__
def pids():
    try:
        r=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)
    except Exception:
        return []
    res=[]
    for tok in r.stdout.split():
        if not tok.isdigit(): continue
        pid=int(tok)
        try:
            exe=os.readlink('/proc/%d/exe'%pid)
            args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')
        except Exception:
            continue
        if exe==agent and cfg.encode() in args: res.append(pid)
    return res
def snap():
    s={}
    for d in dirs:
        try:
            entries={}
            for n in os.listdir(d):
                p=os.path.join(d,n)
                try: entries[n]=os.lstat(p).st_size
                except Exception: entries[n]='ERR'
            s[d]=entries
        except Exception as e:
            s[d]='ERR:'+type(e).__name__
    fds={}
    pl=pids()
    for pid in pl:
        try:
            for fd in os.listdir('/proc/%d/fd'%pid):
                try: t=os.readlink('/proc/%d/fd/%s'%(pid,fd))
                except Exception: continue
                if any(t.startswith(d) for d in dirs): fds['%s:%s'%(pid,fd)]=t
        except Exception: pass
    return {'t':round(time.time(),4),'dirs':s,'fds':fds,'pids':pl}
def key(s):
    return json.dumps([s['dirs'],s['fds']],sort_keys=True)
f=open(out_path,'a',buffering=1)
f.write(json.dumps({'event':'sampler_start','t':time.time(),'dirs':dirs})+'\\n')
end=time.time()+secs
last=None
n=0
while time.time()<end:
    s=snap(); n+=1
    k=key(s)
    if k!=last:
        f.write(json.dumps(s)+'\\n')
        last=k
    time.sleep(0.0005)
f.write(json.dumps({'event':'sampler_stop','t':time.time(),'iterations':n})+'\\n')
f.close()
"""

SAMPLER_START = """import json,os,subprocess,time
prog=__prog__; out_path=__out__; script_path=__script__; log=__log__
open(script_path,'w').write(prog)
p=subprocess.Popen(['setsid','nohup','python3',script_path],stdout=open(log,'a'),
                   stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
time.sleep(0.6)
alive=p.poll() is None
print(json.dumps({'sampler_pid':p.pid,'alive':alive,'script_path':script_path,'out_path':out_path}))
"""


SAMPLER_FETCH = """import json,os
p=__out__
rows=[]
bad=0
if os.path.isfile(p):
    for line in open(p):
        line=line.strip()
        if not line: continue
        try:
            rows.append(json.loads(line))
        except Exception:
            bad+=1
print(json.dumps({'path':p,'exists':os.path.isfile(p),'row_count':len(rows),'unparsable_lines':bad,'rows':rows}))
"""

MAKE_FILE = """import json,os,hashlib
path=__path__; size=__size__; fill=__fill__
os.makedirs(os.path.dirname(path) or '/', exist_ok=True)
if fill=='urandom':
    left=size
    with open(path,'wb') as f:
        while left>0:
            n=min(left,1048576); f.write(os.urandom(n)); left-=n
else:
    with open(path,'wb') as f:
        f.truncate(size)
sha=hashlib.sha256(open(path,'rb').read()).hexdigest()
print(json.dumps({'path':path,'size':os.path.getsize(path),'sha256':sha}))
"""

JSONL_TAIL = """import json,os
p=__path__; n=__n__
rows=[]
if os.path.isfile(p):
    lines=open(p).read().splitlines()[-n:]
    for line in lines:
        line=line.strip()
        if line: rows.append(json.loads(line))
print(json.dumps({'path':p,'tail':rows}))
"""

REMOVE_CHILD = """import json,os,signal,shutil,subprocess,time
root=__root__; cfg=__cfg__; agent=__agent__; opt=__opt__; tag=__tag__; adir=__adir__
def run(cmd):
    r=subprocess.run(cmd,capture_output=True,text=True)
    return {'rc':r.returncode,'out':r.stdout.strip()[:200],'err':r.stderr.strip()[:200]}
out={}
for d in (opt, adir, root):
    if os.path.isdir(d):
        run(['chattr','-i',d])
    for n in list(os.listdir(d) if os.path.isdir(d) else []):
        p=os.path.join(d,n)
        if os.path.isdir(p):
            run(['chattr','-i',p])
mnt=[l.split()[1] for l in open('/proc/mounts') if tag in l]
out['mount_before']=mnt
for m in mnt:
    out.setdefault('umount',[]).append(run(['umount',m]))
def matched():
    p=subprocess.run(['pgrep','-f',cfg],capture_output=True,text=True)
    res=[]
    for tok in p.stdout.split():
        if not tok.isdigit(): continue
        pid=int(tok)
        try:
            exe=os.readlink('/proc/%d/exe'%pid)
            args=open('/proc/%d/cmdline'%pid,'rb').read().split(b'\\0')
        except Exception:
            continue
        if exe==agent and cfg.encode() in args: res.append(pid)
    return res
pids=matched()
for pid in pids: os.kill(pid, signal.SIGTERM)
end=time.time()+12
while time.time()<end and matched(): time.sleep(0.5)
left=matched()
for pid in left:
    try: os.kill(pid, signal.SIGKILL)
    except Exception: pass
time.sleep(0.5)
out['agent_signalled']=pids; out['agent_remaining']=matched()
for p in (root, opt):
    if os.path.isdir(p): shutil.rmtree(p, ignore_errors=True)
out['root_removed']=not os.path.exists(root)
out['opt_removed']=not os.path.exists(opt)
out['mounts_after']=[l.strip() for l in open('/proc/mounts') if tag in l]
out['units_after']=[l.strip() for l in subprocess.run(['systemctl','list-units','--all','--no-pager','--no-legend'],
    capture_output=True,text=True).stdout.splitlines() if 'qa-sysb12-' in l]
print(json.dumps(out))
"""
