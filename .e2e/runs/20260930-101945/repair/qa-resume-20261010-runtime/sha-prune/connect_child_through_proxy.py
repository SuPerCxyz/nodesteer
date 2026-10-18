import hashlib,json,os,pathlib,re,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BIN=ROOT/'ws-frame-proxy';BS='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a,timeout=12):
 p=subprocess.run(a,text=True,capture_output=True,timeout=timeout);return {'rc':p.returncode,'out':p.stdout.strip(),'stderr_bytes':len(p.stderr.encode())}
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
 st=Path('/proc/%d/stat'%pid).read_text().split(') ',1)[1].split();status=Path('/proc/%d/status'%pid).read_text();cmd=Path('/proc/%d/cmdline'%pid).read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha('/proc/%d/exe'%pid),'cgroup':Path('/proc/%d/cgroup'%pid).read_text().strip()}
o=json.loads((R/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text());po=json.loads((ROOT/'owner.json').read_text())
if not all(o.get(k)==v for k,v in {'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(CFG)}.items()):raise SystemExit('child owner manifest mismatch')
if po.get('owner_nonce')!=NONCE or po.get('node_id')!=NODE or po.get('agent_id')!=AGENT or c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('proxy owner/control guard mismatch')
if ROOT.stat().st_uid!=65534 or ROOT.stat().st_mode&0o7777!=0o700 or sha(BIN)!=BS:raise SystemExit('proxy root/binary guard mismatch')
ready=json.loads((ROOT/'ready.json').read_text());port=ready.get('port')
if not isinstance(port,int) or ready.get('agent_id')!=AGENT:raise SystemExit('proxy READY guard mismatch')
cu=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup']);oldpid=int(cu.get('MainPID','0'));old=proc(oldpid)
if cu.get('ActiveState')!='active' or oldpid!=49688 or old['startticks']!=136179722 or old['uid']!=0 or old['exe_sha256']!=AS or old['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(CFG)] or old['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child process guard mismatch before config edit')
au=show(APPUNIT,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=V1:raise SystemExit('v1 App physical oracle mismatch')
if show(PROXY,['ActiveState','MainPID']).get('ActiveState')!='active':raise SystemExit('proxy not active')
if CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('child config owner/mode mismatch')
oldbytes=CFG.read_bytes();matches=list(re.finditer(rb'("hub_url"[ \t]*:[ \t]*)("(?:\\.|[^"\\])*")',oldbytes))
if len(matches)!=1:raise SystemExit('expected exactly one JSON hub_url string token')
m=matches[0];oldurl=json.loads(m.group(2).decode('utf-8'));u=urlsplit(oldurl)
if u.scheme!='ws' or u.hostname!='192.168.100.209' or u.port!=18080 or u.path not in ('','/') or u.username or u.password or u.query or u.fragment:raise SystemExit('current hub_url is not the expected direct repair endpoint')
newurl=f'ws://127.0.0.1:{port}/';newtoken=json.dumps(newurl,separators=(',',':')).encode();newbytes=oldbytes[:m.start(2)]+newtoken+oldbytes[m.end(2):]
if newbytes.replace(newtoken,m.group(2),1)!=oldbytes:raise SystemExit('config non-hub_url bytes changed')
tmp=CFG.with_name('agent.yaml.sha-prune.tmp')
fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:os.write(fd,newbytes);os.fsync(fd)
finally:os.close(fd)
os.chown(tmp,CFG.stat().st_uid,CFG.stat().st_gid);os.chmod(tmp,0o600);os.replace(tmp,CFG)
if CFG.read_bytes()!=newbytes or CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('atomic config update verify failed')
# Revalidate the original child fingerprint immediately before restarting only this exact owned transient Unit.
cu2=show(CHILD,['ActiveState','MainPID','ControlGroup']);now=proc(int(cu2.get('MainPID','0')))
if cu2.get('ActiveState')!='active' or now['pid']!=old['pid'] or now['startticks']!=old['startticks'] or now['uid']!=old['uid'] or now['argv']!=old['argv'] or now['exe_sha256']!=AS or now['cgroup']!=old['cgroup']:raise SystemExit('child fingerprint changed before restart')
r=run(['systemctl','restart',CHILD],25)
if r['rc']!=0:raise SystemExit('exact child restart command failed rc='+str(r['rc']))
end=time.monotonic()+35;new=None;u2={}
while time.monotonic()<end:
 u2=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup']);pid=int(u2.get('MainPID','0') or 0)
 if u2.get('ActiveState')=='active' and pid and pid!=old['pid']:
  try:new=proc(pid)
  except FileNotFoundError:new=None
  if new and new['exe_sha256']==AS and new['argv']==old['argv'] and new['cgroup']=='0::/system.slice/'+CHILD:break
 time.sleep(.25)
if not new:raise SystemExit('owned child did not restart with same binary/config')
# Wait until the exact Agent HELLO has reached the reviewed proxy. No bad-frame match is possible while App remains assigned.
end=time.monotonic()+35;hello=False
while time.monotonic()<end:
 try:
  events=(ROOT/'events.jsonl').read_text().splitlines();hello=any(json.loads(e).get('event')=='hello_verified' and json.loads(e).get('agent_id')==AGENT for e in events)
 except (FileNotFoundError,PermissionError):hello=False
 if hello:break
 time.sleep(.25)
if not hello:raise SystemExit('proxy did not observe exact Agent HELLO')
if json.loads((ROOT/'controls.json').read_text()).get('fault_armed'):raise SystemExit('fault unexpectedly armed')
print(json.dumps({'status':'CHILD_CONNECTED_THROUGH_PROXY_UNARMED','child_old_process':old,'child_new_process':new,'proxy_unit':PROXY,'proxy_ready':ready,'proxy_hello_seen':hello,'fault_armed':False,'child_config':{'path':str(CFG),'uid':CFG.stat().st_uid,'mode':oct(CFG.stat().st_mode&0o7777),'old_hub_url_shape':{'scheme':u.scheme,'host':u.hostname,'port':u.port,'path':u.path},'new_hub_url':newurl,'old_value_sha256':hashlib.sha256(oldurl.encode()).hexdigest(),'config_sha256_before':hashlib.sha256(oldbytes).hexdigest(),'config_sha256_after':hashlib.sha256(newbytes).hexdigest(),'only_hub_url_value_changed':True},'app_unit_active':au.get('ActiveState'),'v1_physical_sha256':actual},sort_keys=True),flush=True)
