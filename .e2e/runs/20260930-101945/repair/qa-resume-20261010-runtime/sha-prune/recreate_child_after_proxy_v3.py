import hashlib,json,os,pathlib,re,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';NODE='c77b8a84-8085-4ea8-8532-de171761854b';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';BS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a,t=12):
 p=subprocess.run(a,text=True,capture_output=True,timeout=t);return {'rc':p.returncode,'out':p.stdout.strip(),'stderr_bytes':len(p.stderr.encode())}
def show(u,props):
 a=['systemctl','show',u]
 for x in props:a+=['-p',x]
 q=run(a);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();cmd=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
def owner_guard():
 o=json.loads((R/'owner.json').read_text());return all(o.get(k)==v for k,v in {'owner_nonce':'870358cd92ee','node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(CFG),'data_dir':str(R/'state')}.items())
if not owner_guard():raise SystemExit('child owner manifest mismatch')
o=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!='870358cd92ee' or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=PROXY or o.get('binary_sha256')!=BS or c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('proxy owner/control mismatch')
ready=json.loads((ROOT/'ready.json').read_text());port=ready.get('port')
if not isinstance(port,int) or ready.get('unit_pid')!=50214 or ready.get('upstream_port')!=18080 or ready.get('agent_id')!=AGENT:raise SystemExit('proxy v3 READY mismatch')
pu=show(PROXY,['ActiveState','MainPID','ControlGroup']);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=BS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('exact proxy process guard mismatch')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);oldpid=int(cu.get('MainPID','0'));old=proc(oldpid)
if cu.get('ActiveState')!='active' or oldpid!=50158 or old['startticks']!=136353430 or old['uid']!=0 or old['exe_sha256']!=AS or old['argv']!=[B,'--config',str(CFG)] or old['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('exact child process fingerprint mismatch')
au=show(APPUNIT,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=V1:raise SystemExit('v1 App physical oracle mismatch')
for u,pid,ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=show(u,['ActiveState','MainPID']);p=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=ticks:raise SystemExit('original Native1 service changed: '+u)
if CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('child config owner/mode mismatch')
oldbytes=CFG.read_bytes();matches=list(re.finditer(rb'("hub_url"[ \t]*:[ \t]*)("(?:\\.|[^"\\])*")',oldbytes))
if len(matches)!=1:raise SystemExit('exact JSON hub_url field missing/duplicate')
m=matches[0];oldurl=json.loads(m.group(2).decode());u=urlsplit(oldurl)
if u.scheme!='ws' or u.hostname!='127.0.0.1' or u.port!=41635 or u.path!='/' or u.username or u.password or u.query or u.fragment:raise SystemExit('old loopback URL mismatch')
newurl=f'ws://127.0.0.1:{port}/';token=json.dumps(newurl,separators=(',',':')).encode();newbytes=oldbytes[:m.start(2)]+token+oldbytes[m.end(2):]
if newbytes.replace(token,m.group(2),1)!=oldbytes:raise SystemExit('config changed beyond hub_url token')
events=ROOT/'events.jsonl';ev_before=events.read_bytes() if events.exists() else b'';ev_len=len(ev_before);ev_sha=hashlib.sha256(ev_before).hexdigest()
tmp=CFG.with_name('agent.yaml.sha-prune.tmp');fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:os.write(fd,newbytes);os.fsync(fd)
finally:os.close(fd)
os.chown(tmp,CFG.stat().st_uid,CFG.stat().st_gid);os.chmod(tmp,0o600);os.replace(tmp,CFG)
if CFG.read_bytes()!=newbytes or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('atomic config update verification failed')
# Refresh the exact owned transient Unit with a bounded 1h RuntimeMax, retaining identity/root/config/binary.
cu2=show(CHILD,['ActiveState','MainPID','ControlGroup']);now=proc(int(cu2.get('MainPID','0')))
if cu2.get('ActiveState')!='active' or now['pid']!=old['pid'] or now['startticks']!=old['startticks'] or now['exe_sha256']!=AS or now['argv']!=old['argv'] or now['cgroup']!=old['cgroup']:raise SystemExit('child changed before restart')
st=run(['systemctl','stop',CHILD],20)
if st['rc']!=0:raise SystemExit('exact owned child stop failed')
if Path('/proc/%d'%old['pid']).exists():raise SystemExit('old child PID still present after stop')
u0=show(CHILD,['LoadState','ActiveState','MainPID'])
if u0.get('ActiveState')!='inactive' or u0.get('MainPID')!='0':raise SystemExit('child Unit did not become inactive')
if u0.get('LoadState')!='not-found':
 rr=run(['systemctl','reset-failed',CHILD],10)
 if rr['rc']!=0:raise SystemExit('exact transient child reset-failed failed')
# Reconfirm all immutable ownership fields immediately before creating the same unit.
if not owner_guard() or CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600 or json.loads(CFG.read_text()).get('hub_url')!=newurl:raise SystemExit('child owner/config changed before same-identity recreate')
if show(CHILD,['LoadState']).get('LoadState')!='not-found':raise SystemExit('same child transient Unit did not unload')
cmd=['systemd-run','--quiet','--no-block','--unit='+CHILD,'--property=User=root','--property=Restart=no','--property=KillMode=control-group','--property=RuntimeMaxSec=3600s','--property=TimeoutStopSec=3s','--property=WorkingDirectory='+str(R),'--property=StandardOutput=append:'+str(R/'log/agent.log'),'--property=StandardError=append:'+str(R/'log/agent.log'),B,'--config',str(CFG)]
start=run(cmd,20)
if start['rc']!=0:raise SystemExit('same-identity systemd-run failed rc='+str(start['rc']))
end=time.monotonic()+35;new=None;u2={}
while time.monotonic()<end:
 u2=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);pid=int(u2.get('MainPID','0') or 0)
 if u2.get('ActiveState')=='active' and pid and pid!=old['pid']:
  try:new=proc(pid)
  except FileNotFoundError:new=None
  if new and new['exe_sha256']==AS and new['argv']==old['argv'] and new['cgroup']=='0::/system.slice/'+CHILD:break
 time.sleep(.25)
if not new or u2.get('RuntimeMaxUSec')!='1h':raise SystemExit('same-identity child Unit/process RuntimeMax verification failed')
end=time.monotonic()+35;hello=None
while time.monotonic()<end:
 try:
  raw=events.read_bytes();records=[json.loads(line) for line in raw[ev_len:].splitlines() if line.strip()];hello=next((x for x in records if x.get('event')=='hello_verified' and x.get('agent_id')==AGENT),None)
 except (FileNotFoundError,PermissionError,ValueError):hello=None
 if hello:break
 time.sleep(.25)
if not hello:raise SystemExit('proxy did not log new exact Agent HELLO')
if json.loads((ROOT/'controls.json').read_text()).get('fault_armed'):raise SystemExit('fault unexpectedly armed')
print(json.dumps({'status':'CHILD_RECREATED_SAME_IDENTITY_CONNECTED_PROXY_V3_UNARMED','child_old_process':old,'child_new_process':new,'child_unit_before':cu,'child_unit_after':u2,'proxy_process':pp,'proxy_ready':ready,'hello_event_after_restart':hello,'child_config':{'path':str(CFG),'uid':CFG.stat().st_uid,'mode':oct(CFG.stat().st_mode&0o7777),'old_hub_url_shape':{'scheme':u.scheme,'host':u.hostname,'port':u.port,'path':u.path},'new_hub_url':newurl,'old_value_sha256':hashlib.sha256(oldurl.encode()).hexdigest(),'config_sha256_before':hashlib.sha256(oldbytes).hexdigest(),'config_sha256_after':hashlib.sha256(newbytes).hexdigest(),'only_hub_url_value_changed':True,'previous_proxy_events_bytes':ev_len,'previous_proxy_events_sha256':ev_sha,'old_url_value_emitted':False},'app_unit_active':au.get('ActiveState'),'v1_physical_sha256':actual,'fault_armed':False},sort_keys=True),flush=True)
