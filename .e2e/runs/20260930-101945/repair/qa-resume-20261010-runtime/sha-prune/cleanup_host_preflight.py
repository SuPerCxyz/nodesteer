import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token'];op=build_opener(ProxyHandler({}));HN='91f44866-8a28-435e-bbda-2f2367126c00'
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
src=r'''import hashlib,json,os,pathlib,sqlite3,subprocess
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';N='870358cd92ee';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e'
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=8);return dict(x.split('=',1) for x in q.stdout.strip().splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();ss=(q/'status').read_text();a=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in ss.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in a.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
def unit(u):return run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup'])
def rootmeta(p):
 s=p.lstat();return {'path':str(p),'exists':True,'symlink':p.is_symlink(),'uid':s.st_uid,'gid':s.st_gid,'mode':oct(s.st_mode&0o7777),'device':s.st_dev,'inode':s.st_ino}
co=json.loads((R/'owner.json').read_text());po=json.loads((P/'owner.json').read_text())
if any(co.get(k)!=v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(R/'agent.yaml')}.items()):raise SystemExit('child owner manifest guard failed')
if any(po.get(k)!=v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'binary_sha256':PS}.items()):raise SystemExit('proxy owner manifest guard failed')
cu=unit(CHILD);cp=proc(int(cu.get('MainPID','0')));pu=unit(PROXY);pp=proc(int(pu.get('MainPID','0')));au=unit(APPUNIT);app_pid=int(au.get('MainPID','0') or 0)
if cp['pid']!=50291 or cp['startticks']!=136559152 or cp['uid']!=0 or cp['exe_sha256']!=AS or cp['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(R/'agent.yaml')] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child exact PID fingerprint changed')
if pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('proxy exact PID fingerprint changed')
if app_pid!=0 or au.get('LoadState')!='not-found':raise SystemExit('owned App unit unexpectedly present')
if (R/'app/bin/qa-app').exists() or (R/'app/config/qa-app.env').exists() or Path('/etc/systemd/system/'+APPUNIT).exists():raise SystemExit('owned App runtime paths unexpectedly present')
db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);registry=[dict(zip(['application_id','unit_name','registered_at'],x)) for x in db.execute('select application_id,unit_name,registered_at from unit_registry where application_id=?',('2e4fecee-afc8-43a8-9d93-dd36cfc10bf9',))];db.close()
link=Path('/etc/systemd/system/multi-user.target.wants')/APPUNIT;linkmeta={'path':str(link),'exists':link.exists() or link.is_symlink(),'is_symlink':link.is_symlink(),'target':os.readlink(link) if link.is_symlink() else None};enabled=run(['systemctl','is-enabled',APPUNIT])
parents={}
for u,pid,ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=unit(u);p=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=ticks:raise SystemExit('original Native1 service fingerprint changed')
 parents[u]={'active':q.get('ActiveState'),'pid':p['pid'],'startticks':p['startticks']}
controls=json.loads((P/'controls.json').read_text())
if controls.get('fault_armed') is not True or controls.get('release_good_snapshot') is not True:raise SystemExit('fixture not in completed released state')
print(json.dumps({'status':'EXACT_COMBINED_FIXTURE_CLEANUP_PREFLIGHT_READY','child_unit':cu,'child_process':cp,'proxy_unit':pu,'proxy_process':pp,'app_unit':au,'child_root':rootmeta(R),'proxy_root':rootmeta(P),'child_root_owner_matches':R.stat().st_uid==0 and (R.stat().st_mode&0o7777)==0o700,'proxy_root_owner_matches':P.stat().st_uid==65534 and (P.stat().st_mode&0o7777)==0o700,'proxy_controls_released':True,'original_native1_services':parents,'root_paths_are_exact_nonce_owned':True,'app_unit_and_physical_files_already_pruned':True,'unit_registry_rows_for_owned_app':registry,'enabled_symlink_probe':linkmeta,'systemctl_is_enabled':enabled},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-cleanup-host-registry-preflight-870358cd92ee';task=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[HN]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=task['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'scope':'read-only exact own process/unit/root and original service guards'}
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
(H/'cleanup-host-registry-preflight-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json')},ensure_ascii=False))
