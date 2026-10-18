import hashlib,json,pathlib,subprocess,time
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APP='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';SRC='5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
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
o=json.loads((R/'owner.json').read_text());po=json.loads((ROOT/'owner.json').read_text());ctrl=json.loads((ROOT/'controls.json').read_text())
if not all(o.get(k)==v for k,v in {'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(CFG)}.items()) or not all(po.get(k)==v for k,v in {'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'binary_sha256':PS,'source_sha256':SRC}.items()):raise SystemExit('owner manifest mismatch')
if ctrl.get('fault_armed') or ctrl.get('release_good_snapshot'):raise SystemExit('refuse child stop: controls not false')
conf=json.loads(CFG.read_text())
if conf.get('hub_url')!='ws://127.0.0.1:45237/' or CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('config loopback/mode guard mismatch')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup','RuntimeMaxUSec']);pid=int(cu.get('MainPID','0'));old=proc(pid)
if cu.get('ActiveState')!='active' or pid!=50238 or old['startticks']!=136480608 or old['uid']!=0 or old['exe_sha256']!=AS or old['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(CFG)] or old['cgroup']!='0::/system.slice/'+CHILD or cu.get('RuntimeMaxUSec')!='1h':raise SystemExit('child process fingerprint mismatch')
au=show(APP,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APP)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APP or actual!=V1:raise SystemExit('v1 App physical baseline mismatch')
pu=show(PROXY,['ActiveState','MainPID','ControlGroup']);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('proxy process fingerprint mismatch')
parents=[]
for unit,pid0,tick0 in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 u=show(unit,['ActiveState','MainPID']);p=proc(int(u.get('MainPID','0')))
 if u.get('ActiveState')!='active' or p['pid']!=pid0 or p['startticks']!=tick0:raise SystemExit('original parent Native1 Unit changed')
 parents.append({'unit':unit,'pid':p['pid'],'startticks':p['startticks']})
st=run(['systemctl','stop',CHILD],20)
if st['rc']!=0:raise SystemExit('exact owned child stop failed')
end=time.monotonic()+8;after={}
while time.monotonic()<end:
 after=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup'])
 if after.get('ActiveState')=='inactive' and after.get('MainPID')=='0':break
 time.sleep(.25)
if after.get('ActiveState')!='inactive' or after.get('MainPID')!='0' or Path('/proc/%d'%pid).exists():raise SystemExit('owned child did not stop cleanly')
print(json.dumps({'status':'OWNED_CHILD_STOPPED_FOR_V2_STAGE','unit':CHILD,'old_process':old,'unit_after':after,'old_pid_absent':True,'proxy_process_untouched':pp,'v1_app_process_untouched':ap['pid'],'v1_physical_sha256':actual,'parent_services_untouched':parents,'child_config_sha256':sha(CFG),'child_config_mode':oct(CFG.stat().st_mode&0o7777),'fault_armed':False},sort_keys=True),flush=True)
