import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';SCRIPTS=['fc6fbcc6-39ce-49f4-9368-265a5a42d174','616a9db6-a87f-4d79-a4de-7d1b016e2986'];TASKS=['d43ded1e-f073-461e-b06c-24005789d4c3','520d7d8d-ccf1-4456-a27f-4fd6b39bf4ce'];ARTS=['0c74fcee-7689-49a4-8206-3fa5f6eba746','5517ff25-65b1-4da3-b5d7-a3f86f0de73d','93ccd9eb-14b6-4a26-bceb-3f4c13de6061','6ce76a15-f3de-42b9-b7d4-63e4d02edfc4']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
def api(method,path=None,body=None,allowed=(200,)):
 if path is None:path=method;method='GET'
 headers={'Authorization':'Bearer '+token};data=None
 if body is not None:data=json.dumps(body).encode();headers['Content-Type']='application/json'
 try:
  with op.open(Request(base+path,data=data,headers=headers,method=method),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{method} {path} HTTP {s}')
 return json.loads(x) if x else None
# Exact final Hub absence precondition before requesting the bounded host-only removal task.
for path in ['/api/nodes/'+NODE,'/api/applications/'+APP]+['/api/scripts/'+x for x in SCRIPTS]+['/api/tasks/'+x for x in TASKS]+['/api/artifacts/'+x for x in ARTS]:api('GET',path,None,(404,))
src=r'''import hashlib,json,os,pathlib,shutil,sqlite3,subprocess
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';N='870358cd92ee';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e'
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=8);return dict(x.split('=',1) for x in q.stdout.strip().splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def unit(u):return run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup'])
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();ss=(q/'status').read_text();a=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in ss.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in a.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
def guardroot(path,dev,ino,uid,gid):
 s=path.lstat();return not path.is_symlink() and s.st_dev==dev and s.st_ino==ino and s.st_uid==uid and s.st_gid==gid and s.st_mode&0o7777==0o700
co=json.loads((R/'owner.json').read_text());po=json.loads((P/'owner.json').read_text())
if any(co.get(k)!=v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')}.items()):raise SystemExit('child owner manifest exact guard failed')
if any(po.get(k)!=v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'binary_sha256':PS,'source_sha256':'5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0'}.items()):raise SystemExit('proxy owner manifest exact guard failed')
if not guardroot(R,64769,10497077,0,0) or not guardroot(P,64769,13035507,65534,65534):raise SystemExit('root device/inode/owner/mode guard failed')
for u in (CHILD,PROXY):
 x=unit(u)
 if x.get('LoadState')!='not-found' or x.get('ActiveState') not in ('inactive','failed','not-found') or x.get('MainPID') not in ('0',''):raise SystemExit('owned Unit is not stopped/unloaded '+u)
if unit(APPUNIT).get('LoadState')!='not-found':raise SystemExit('App Unit unexpectedly present')
if (R/'app/bin/qa-app').exists() or (R/'app/config/qa-app.env').exists() or Path('/etc/systemd/system/'+APPUNIT).exists():raise SystemExit('App physical file unexpectedly present')
if (Path('/etc/systemd/system/multi-user.target.wants')/APPUNIT).exists():raise SystemExit('App enable symlink unexpectedly present')
db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);rows=[x for x in db.execute('select application_id,unit_name from unit_registry where application_id=?',('2e4fecee-afc8-43a8-9d93-dd36cfc10bf9',))];db.close()
if rows:raise SystemExit('App Unit Registry entry unexpectedly present')
parents={}
for u,pid,tick in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=unit(u);p=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=tick:raise SystemExit('original Native1 service guard failed')
 parents[u]={'pid':p['pid'],'startticks':p['startticks'],'active':q.get('ActiveState')}
meta={'child_root':{'path':str(R),'device':R.stat().st_dev,'inode':R.stat().st_ino,'uid':R.stat().st_uid,'gid':R.stat().st_gid,'mode':oct(R.stat().st_mode&0o7777)},'proxy_root':{'path':str(P),'device':P.stat().st_dev,'inode':P.stat().st_ino,'uid':P.stat().st_uid,'gid':P.stat().st_gid,'mode':oct(P.stat().st_mode&0o7777)},'unit_registry_rows':rows,'original_services':parents}
# Remove only two pre-guarded exact nonce-owned roots. No glob, parent removal or follow-symlink.
shutil.rmtree(R);shutil.rmtree(P)
if R.exists() or R.is_symlink() or P.exists() or P.is_symlink():raise SystemExit('owned roots remain after rmtree')
for u in (CHILD,PROXY):
 x=unit(u)
 if x.get('LoadState')!='not-found' or x.get('MainPID') not in ('0',''):raise SystemExit('Unit reappeared after root removal '+u)
for u,pid,tick in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=unit(u);p=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=tick:raise SystemExit('original service changed during exact root removal '+u)
print(json.dumps({'status':'EXACT_NONCE_OWNED_ROOTS_REMOVED','removed_paths':[str(R),str(P)],'both_paths_absent':not R.exists() and not P.exists(),'pre_removal_metadata':meta,'child_proxy_units_still_not_found':True,'original_native1_services_unchanged':True,'unit_registry_empty_before_root_removal':True},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-remove-owned-fixture-roots-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'hub_objects_prechecked_404':True,'scope':'remove only exact two owner-guarded roots after units stopped and App UnitRegistry/symlink/file already absent'}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==ev['command_sha256'];x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+65;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 ev['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['task_get_after_status']=404
 except Exception:ev['task_get_after_status']='not-404'
(H/'owned-roots-removal-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json')},ensure_ascii=False))
