import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));HN='91f44866-8a28-435e-bbda-2f2367126c00';NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';IDS={'node':[NODE],'app':[APP],'scripts':['fc6fbcc6-39ce-49f4-9368-265a5a42d174','616a9db6-a87f-4d79-a4de-7d1b016e2986'],'tasks':['d43ded1e-f073-461e-b06c-24005789d4c3','520d7d8d-ccf1-4456-a27f-4fd6b39bf4ce'],'artifacts':['0c74fcee-7689-49a4-8206-3fa5f6eba746','5517ff25-65b1-4da3-b5d7-a3f86f0de73d','93ccd9eb-14b6-4a26-bceb-3f4c13de6061','6ce76a15-f3de-42b9-b7d4-63e4d02edfc4']}
if urlsplit(base).port!=18080:raise SystemExit('endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
for kind,ids in IDS.items():
 for i in ids:api('GET','/api/'+({'node':'nodes','app':'applications','scripts':'scripts','tasks':'tasks','artifacts':'artifacts'}[kind])+'/'+i,None,(404,))
nodes=api('GET','/api/nodes');parents=[{'id':x.get('id'),'hostname':x.get('hostname'),'status':x.get('status'),'sync_status':x.get('sync_status'),'global_revision':x.get('global_revision')} for x in nodes if x.get('hostname','').startswith('qa-repair-20260930-101945-native-')];expected={'qa-repair-20260930-101945-native-1','qa-repair-20260930-101945-native-2','qa-repair-20260930-101945-native-3','qa-repair-20260930-101945-native-4'}
if {x['hostname'] for x in parents}!=expected or any(x['status']!='online' or x['sync_status']!='synced' for x in parents):raise SystemExit('original four repair nodes not all online/synced')
tasks=api('GET','/api/tasks');arts=api('GET','/api/artifacts');settings=api('GET','/api/settings')
if tasks:raise SystemExit('Task definitions remain after exact cleanup')
qaarts=[x for x in arts if '870358cd92ee' in (x.get('name') or '') or 'qa-resume-202610' in (x.get('name') or '')]
if qaarts:raise SystemExit('QA-named Artifact remains after cleanup')
if settings.get('changelog_window')!='5000':raise SystemExit('global changelog_window changed')
# Preserve and verify one SHA-case Execution history record after Node deletion.
exe=api('GET','/api/executions/c6729f66-12fd-4492-bb99-1dfbd68cc3f4');history={'sha_good_execution_id':exe.get('id'),'status':exe.get('status'),'node_id':exe.get('node_id')}
if history['status']!='SUCCESS':raise SystemExit('historical good Script Execution was not preserved')
src=r'''import hashlib,json,os,pathlib,subprocess
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'
def show(u):
 q=subprocess.run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup'],text=True,capture_output=True,timeout=8);return dict(x.split('=',1) for x in q.stdout.strip().splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);s=(q/'stat').read_text().split(') ',1)[1].split();t=(q/'status').read_text();uid=int([x.split()[1] for x in t.splitlines() if x.startswith('Uid:')][0]);a=(q/'cmdline').read_bytes();return {'pid':pid,'startticks':int(s[19]),'uid':uid,'argv':[x.decode() for x in a.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
for p in (R,P):
 if p.exists() or p.is_symlink():raise SystemExit('owned root remains '+str(p))
for u in (CHILD,PROXY,APPUNIT):
 x=show(u)
 if x.get('LoadState')!='not-found' or x.get('MainPID') not in ('0',''):raise SystemExit('owned Unit remains '+u)
for p in (Path('/etc/systemd/system')/APPUNIT,Path('/etc/systemd/system/multi-user.target.wants')/APPUNIT):
 if p.exists() or p.is_symlink():raise SystemExit('App Unit file/enable symlink remains')
parents={}
for u,pid,ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 x=show(u);p=proc(int(x.get('MainPID','0')))
 if x.get('ActiveState')!='active' or p['pid']!=pid or p['startticks']!=ticks:raise SystemExit('original Native1 service changed '+u)
 parents[u]={'pid':p['pid'],'startticks':p['startticks'],'active':x.get('ActiveState')}
print(json.dumps({'status':'FINAL_HOST_CLEANUP_ORACLE_PASS','owned_roots_absent':True,'owned_units_not_found':True,'App_Unit_file_and_enable_symlink_absent':True,'original_Native1_services_unchanged':parents},sort_keys=True),flush=True)'''
cmd='python3 -c '+__import__('shlex').quote(src);name='qa-resume-20261010-final-cleanup-oracle-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[HN]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'scope':'read-only final exact roots, Units, App file/symlink, original Unit guards'}
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
host_result=json.loads(json.dumps(ev))
ev['final_api_readback']={'removed_object_ids_all_get404':True,'remaining_tasks':len(tasks),'remaining_qa_named_artifacts':qaarts,'remaining_artifact_count':len(arts),'original_repair_nodes':parents,'changelog_window':settings.get('changelog_window'),'preserved_execution_history':history,'previously_deleted_artifact_get404':True}
ev['final_host_oracle_task']=host_result
(H/'final-cleanup-oracle-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':host_result['status'],'task_deleted_404':host_result['task_get_after_status']==404,'result':host_result['stdout_json'],'api_objects_get404':True,'tasks_remaining':len(tasks),'qa_artifacts_remaining':len(qaarts),'original_nodes_online_synced':len(parents)==4,'preserved_execution':history},ensure_ascii=False))
