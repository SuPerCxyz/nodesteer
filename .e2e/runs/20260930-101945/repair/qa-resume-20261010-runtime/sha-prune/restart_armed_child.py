import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
R=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee')
src=r'''import hashlib,json,os,pathlib,subprocess,time
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20260909-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a,t=10):
 q=subprocess.run(a,text=True,capture_output=True,timeout=t);return {'rc':q.returncode,'out':q.stdout.strip(),'stderr_bytes':len(q.stderr.encode())}
def show(u):
 q=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec']);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();cmd=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CFG=R/'agent.yaml';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
o=json.loads((R/'owner.json').read_text());po=json.loads((P/'owner.json').read_text());c=json.loads((P/'controls.json').read_text());ready=json.loads((P/'ready.json').read_text())
if o.get('owner_nonce')!='870358cd92ee' or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('unit')!=CHILD or o.get('binary_sha256')!=AS or o.get('config_path')!=str(CFG):raise SystemExit('child owner manifest mismatch')
if po.get('owner_nonce')!='870358cd92ee' or po.get('node_id')!=NODE or po.get('agent_id')!=AGENT or po.get('proxy_unit')!=PROXY or po.get('binary_sha256')!=PS or c.get('fault_armed') is not True or c.get('release_good_snapshot') is not False:raise SystemExit('proxy/control exact target mismatch')
if c.get('app_id')!='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9' or c.get('good_script_id')!='fc6fbcc6-39ce-49f4-9368-265a5a42d174' or c.get('bad_script_id')!='616a9db6-a87f-4d79-a4de-7d1b016e2986' or c.get('good_revision')!=3 or c.get('bad_revision')!=2:raise SystemExit('control IDs/revisions mismatch')
if show(CHILD).get('LoadState')!='not-found':raise SystemExit('child unit not offline')
pu=show(PROXY);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('proxy process guard mismatch')
if ready.get('unit_pid')!=pp['pid'] or ready.get('agent_id')!=AGENT or not isinstance(ready.get('port'),int):raise SystemExit('proxy READY mismatch')
if sha(B)!=AS or CFG.stat().st_uid!=0 or CFG.stat().st_mode&0o7777!=0o600:raise SystemExit('child binary/config guard mismatch')
cfg=json.loads(CFG.read_text());url=cfg.get('hub_url','');import urllib.parse
u=urllib.parse.urlsplit(url)
if u.scheme!='ws' or u.hostname!='127.0.0.1' or u.port!=ready['port'] or u.path!='/' or u.username or u.password or u.query or u.fragment:raise SystemExit('child config proxy URL mismatch')
au=show(APPUNIT);ap=proc(int(au.get('MainPID','0')));actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=V1:raise SystemExit('App v1 physical baseline mismatch')
for un,pid,ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=show(un);p=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=ticks:raise SystemExit('original Native1 service guard failed')
events=P/'events.jsonl';offset=events.stat().st_size if events.exists() else 0
cmd=['systemd-run','--quiet','--no-block','--unit='+CHILD,'--property=User=root','--property=Restart=no','--property=KillMode=control-group','--property=RuntimeMaxSec=3600s','--property=TimeoutStopSec=3s','--property=WorkingDirectory='+str(R),'--property=StandardOutput=append:'+str(R/'log/agent.log'),'--property=StandardError=append:'+str(R/'log/agent.log'),B,'--config',str(CFG)]
st=run(cmd,15)
if st['rc']!=0:raise SystemExit('same-owned child systemd-run failed')
end=time.monotonic()+12;child=None;cu={}
while time.monotonic()<end:
 cu=show(CHILD);pid=int(cu.get('MainPID','0') or 0)
 if cu.get('ActiveState')=='active' and pid:
  try:child=proc(pid)
  except FileNotFoundError:child=None
  if child and child['exe_sha256']==AS and child['argv']==[B,'--config',str(CFG)] and child['cgroup']=='0::/system.slice/'+CHILD:break
 time.sleep(.25)
if not child or cu.get('RuntimeMaxUSec')!='1h':raise SystemExit('restarted child owner/RuntimeMax verification failed')
end=time.monotonic()+35;records=[];wanted=('hello_verified','bad_sha_frame_forwarded','sync_ack','valid_snapshot_held_for_negative_oracle')
while time.monotonic()<end:
 raw=events.read_bytes() if events.exists() else b''
 records=[json.loads(z) for z in raw[offset:].splitlines() if z.strip()]
 names=[x.get('event') for x in records]
 if all(x in names for x in wanted):break
 time.sleep(.25)
selected=[x for x in records if x.get('event') in wanted or x.get('event')=='target_snapshot_input_mismatch_blocked']
if not all(any(x.get('event')==n for x in selected) for n in wanted):raise SystemExit('expected exact HELLO/fault/negative ACK/held valid Snapshot not observed; no release performed')
bad=next(x for x in selected if x.get('event')=='bad_sha_frame_forwarded');ack=next(x for x in selected if x.get('event')=='sync_ack');held=next(x for x in selected if x.get('event')=='valid_snapshot_held_for_negative_oracle')
if bad.get('bad_script_id')!=c['bad_script_id'] or bad.get('original_content_sha256')!=c['bad_sha256'] or bad.get('only_content_field_changed') is not True:raise SystemExit('bad-SHA frame oracle mismatch')
if ack.get('ok') is not False or ack.get('applied_rev')!=0 or ack.get('accepted_as_bad_sha_rejection') is not True:raise SystemExit('negative ACK oracle mismatch')
details=held.get('frame') or {}
if details.get('snapshot') is not True or details.get('full_resync') is not False or details.get('target_app_present') is not False:raise SystemExit('valid full Snapshot App omission mismatch')
print(json.dumps({'status':'EXACT_BAD_SHA_REJECTED_VALID_SNAPSHOT_HELD','child_process':child,'child_unit':cu,'proxy_process':pp,'proxy_port':ready['port'],'proxy_events_after_restart':selected,'bad_sha_exact_rejected':True,'negative_ack_applied_rev':0,'valid_snapshot_held':True,'release_good_snapshot':False,'app_v1_physical_sha256':actual,'app_unit_active':au.get('ActiveState'),'original_services_unchanged':True,'credential_emitted':False,'raw_frames_emitted':False},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-restart-armed-sha-prune-child-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'fault_armed_before_execution':True,'release_before_execution':False}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==ev['command_sha256'];xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=xs[0]['id'];end=time.monotonic()+65;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 ev['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['task_get_after_status']=404
 except Exception:ev['task_get_after_status']='not-404'
(H/'armed-child-restart-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json')},ensure_ascii=False))
