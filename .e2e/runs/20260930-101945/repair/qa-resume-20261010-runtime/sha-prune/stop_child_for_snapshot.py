import hashlib,json,pathlib,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';APPBIN=R/'app/bin/qa-app';APPCFG=R/'app/config/qa-app.env';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'};AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e'
def run(a,t=12):
 p=subprocess.run(a,text=True,capture_output=True,timeout=t);return {'rc':p.returncode,'out':p.stdout.strip(),'stderr_bytes':len(p.stderr.encode())}
def show(u,props):
 a=['systemctl','show',u]
 for x in props:a+=['-p',x]
 q=run(a);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();ss=(q/'status').read_text();cmd=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in ss.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
o=json.loads((R/'owner.json').read_text());po=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text());
if not all(o.get(k)==v for k,v in {'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(CFG)}.items()) or not all(po.get(k)==v for k,v in {'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'binary_sha256':PS}.items()):raise SystemExit('owner manifest mismatch')
if c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('refuse stop: proxy fault controls not false')
conf=json.loads(CFG.read_text());u=urlsplit(conf.get('hub_url',''))
if u.scheme!='ws' or u.hostname!='127.0.0.1' or u.port!=41635 or CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('child config loopback/mode guard mismatch')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup']);pid=int(cu.get('MainPID','0'));old=proc(pid)
if cu.get('ActiveState')!='active' or old['pid']!=50158 or old['startticks']!=136353430 or old['uid']!=0 or old['exe_sha256']!=AS or old['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(CFG)] or old['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('exact owned child fingerprint mismatch')
au=show(APPUNIT,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(APPBIN),'config':sha(APPCFG),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=V1:raise SystemExit('v1 App physical guard mismatch')
pu=show(PROXY,['ActiveState','MainPID','ControlGroup']);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50125 or pp['startticks']!=136318003 or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('exact proxy process fingerprint mismatch')
stop=run(['systemctl','stop',CHILD],20)
if stop['rc']!=0:raise SystemExit('exact child systemctl stop failed')
end=time.monotonic()+8;after={}
while time.monotonic()<end:
 after=show(CHILD,['ActiveState','MainPID','ControlGroup'])
 if after.get('ActiveState')=='inactive' and after.get('MainPID')=='0':break
 time.sleep(.25)
if after.get('ActiveState')!='inactive' or after.get('MainPID')!='0':raise SystemExit('owned child Unit did not stop cleanly')
try:still=Path('/proc/%d'%pid).exists()
except Exception:still=False
if still:raise SystemExit('old owned child PID still exists after stop')
print(json.dumps({'status':'OWNED_CHILD_STOPPED_FOR_SNAPSHOT_PREP','stopped_unit':CHILD,'previous_process':old,'unit_after':after,'old_pid_absent':not still,'proxy_process':pp,'proxy_fault_armed':False,'app_unit_active':au.get('ActiveState'),'app_pid_untouched':ap['pid'],'v1_sha256':actual,'child_config':{'path':str(CFG),'sha256':sha(CFG),'mode':oct(CFG.stat().st_mode&0o7777),'hub_url_value_emitted':False}},sort_keys=True),flush=True)
