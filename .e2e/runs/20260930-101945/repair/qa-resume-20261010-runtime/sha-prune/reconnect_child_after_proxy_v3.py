import hashlib,json,os,pathlib,re,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BIN=ROOT/'ws-frame-proxy';BS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
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
o=json.loads((R/'owner.json').read_text());po=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if not all(o.get(k)==v for k,v in {'owner_nonce':'870358cd92ee','node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(CFG)}.items()):raise SystemExit('child owner manifest mismatch')
if po.get('owner_nonce')!='870358cd92ee' or po.get('node_id')!=NODE or po.get('agent_id')!=AGENT or po.get('proxy_unit')!=PROXY or po.get('binary_sha256')!=BS or po.get('source_sha256')!='5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0' or c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('proxy owner/control mismatch')
ready=json.loads((ROOT/'ready.json').read_text());newport=ready.get('port')
if not isinstance(newport,int) or ready.get('unit_pid')!=50214 or ready.get('agent_id')!=AGENT or ready.get('upstream_port')!=18080:raise SystemExit('proxy v3 READY mismatch')
pu=show(PROXY,['ActiveState','MainPID','ControlGroup']);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=BS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('exact v3 proxy process guard mismatch')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);oldpid=int(cu.get('MainPID','0'));old=proc(oldpid)
if cu.get('ActiveState')!='active' or oldpid!=50158 or old['startticks']!=136353430 or old['uid']!=0 or old['exe_sha256']!=AS or old['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(CFG)] or old['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('owned child process guard mismatch')
au=show(APPUNIT,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=V1:raise SystemExit('v1 App physical oracle mismatch')
if CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('child config owner/mode mismatch')
oldbytes=CFG.read_bytes();matches=list(re.finditer(rb'("hub_url"[ \t]*:[ \t]*)("(?:\\.|[^"\\])*")',oldbytes))
if len(matches)!=1:raise SystemExit('exact JSON hub_url field missing/duplicate')
m=matches[0];oldurl=json.loads(m.group(2).decode());u=urlsplit(oldurl)
if u.scheme!='ws' or u.hostname!='127.0.0.1' or u.port!=41635 or u.path!='/' or u.username or u.password or u.query or u.fragment:raise SystemExit('old loopback URL did not match expected proxy port')
newurl=f'ws://127.0.0.1:{newport}/';newtoken=json.dumps(newurl,separators=(',',':')).encode();newbytes=oldbytes[:m.start(2)]+newtoken+oldbytes[m.end(2):]
if newbytes.replace(newtoken,m.group(2),1)!=oldbytes:raise SystemExit('config changed beyond only hub_url token')
events=ROOT/'events.jsonl';before_events=events.read_bytes() if events.exists() else b'';before_event_len=len(before_events);before_event_sha=hashlib.sha256(before_events).hexdigest()
# Extend only this owned transient Agent Unit; do not signal here. The following exact restart renews the bounded lifetime.
ext=run(['systemctl','set-property','--runtime',CHILD,'RuntimeMaxSec=3600'],15)
if ext['rc']!=0:raise SystemExit('owned child RuntimeMax extension failed')
cu_ext=show(CHILD,['ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);same=proc(int(cu_ext.get('MainPID','0')))
if cu_ext.get('ActiveState')!='active' or same['pid']!=old['pid'] or same['startticks']!=old['startticks'] or same['exe_sha256']!=AS or cu_ext.get('RuntimeMaxUSec')!='1h':raise SystemExit('owned child runtime extension/process verification failed')
tmp=CFG.with_name('agent.yaml.sha-prune.tmp');fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:os.write(fd,newbytes);os.fsync(fd)
finally:os.close(fd)
os.chown(tmp,CFG.stat().st_uid,CFG.stat().st_gid);os.chmod(tmp,0o600);os.replace(tmp,CFG)
if CFG.read_bytes()!=newbytes or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('atomic JSON hub_url config edit failed')
cu2=show(CHILD,['ActiveState','MainPID','ControlGroup']);now=proc(int(cu2.get('MainPID','0')))
if cu2.get('ActiveState')!='active' or now['pid']!=old['pid'] or now['startticks']!=old['startticks'] or now['uid']!=old['uid'] or now['argv']!=old['argv'] or now['exe_sha256']!=AS or now['cgroup']!=old['cgroup']:raise SystemExit('child fingerprint changed before exact restart')
r=run(['systemctl','restart',CHILD],25)
if r['rc']!=0:raise SystemExit('exact owned child restart failed')
end=time.monotonic()+35;new=None;u2={}
while time.monotonic()<end:
 u2=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);pid=int(u2.get('MainPID','0') or 0)
 if u2.get('ActiveState')=='active' and pid and pid!=old['pid']:
  try:new=proc(pid)
  except FileNotFoundError:new=None
  if new and new['exe_sha256']==AS and new['argv']==old['argv'] and new['cgroup']=='0::/system.slice/'+CHILD:break
 time.sleep(.25)
if not new or u2.get('RuntimeMaxUSec')!='1h':raise SystemExit('owned child restart/runtime verification failed')
end=time.monotonic()+35;hello=None
while time.monotonic()<end:
 try:
  raw=events.read_bytes();tail=raw[before_event_len:];records=[json.loads(line) for line in tail.splitlines() if line.strip()];hello=next((x for x in records if x.get('event')=='hello_verified' and x.get('agent_id')==AGENT),None)
 except (FileNotFoundError,PermissionError,ValueError):hello=None
 if hello:break
 time.sleep(.25)
if not hello:raise SystemExit('proxy did not record new HELLO after reconnect')
if json.loads((ROOT/'controls.json').read_text()).get('fault_armed'):raise SystemExit('fault unexpectedly armed')
print(json.dumps({'status':'CHILD_RECONNECTED_TO_PROXY_V3_UNARMED','child_old_process':old,'child_new_process':new,'child_runtime_max_before':cu.get('RuntimeMaxUSec'),'child_runtime_max_after':u2.get('RuntimeMaxUSec'),'runtime_extension_command_rc':ext['rc'],'proxy_old_process':None,'proxy_new_process':pp,'proxy_ready':ready,'new_hello_event':hello,'child_config':{'path':str(CFG),'uid':CFG.stat().st_uid,'mode':oct(CFG.stat().st_mode&0o7777),'old_hub_url_shape':{'scheme':u.scheme,'host':u.hostname,'port':u.port,'path':u.path},'new_hub_url':newurl,'old_value_sha256':hashlib.sha256(oldurl.encode()).hexdigest(),'config_sha256_before':hashlib.sha256(oldbytes).hexdigest(),'config_sha256_after':hashlib.sha256(newbytes).hexdigest(),'only_hub_url_value_changed':True,'before_events_bytes':before_event_len,'before_events_sha256':before_event_sha,'previous_url_value_emitted':False},'app_unit_active':au.get('ActiveState'),'v1_physical_sha256':actual,'fault_armed':False},sort_keys=True),flush=True)
