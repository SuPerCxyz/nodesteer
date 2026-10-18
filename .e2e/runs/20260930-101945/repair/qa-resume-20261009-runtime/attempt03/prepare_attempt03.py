import hashlib,json,shlex,time,uuid
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;P=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');c=json.loads((P/'repair-connection.json').read_text());base=c['hub_url'].rstrip('/');tok=c['token'];op=build_opener(ProxyHandler({}));APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';ART='93ccd9eb-14b6-4a26-bceb-3f4c13de6061';SHA='145333b2830f512362ca1ef187d92599cb022b07666e8a53e6f8f0dd5bd16317';SIZE=67108864;VER='v2-attempt3';NAME='qa-resume-20261009-native1-p0-002-app-870358cd92ee';BIN='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/bin/qa-app';CFG='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/config/qa-app.env';UNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';OUT=H/'prepare-evidence.json'
if urlsplit(base).port!=18080:raise SystemExit('blocked endpoint; only repair :18080')
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+tok};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,v=r.status,r.read()
 except HTTPError as e:s,v=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError(type(e).__name__)
 if s not in allow:raise RuntimeError(m+' HTTP '+str(s))
 return s,json.loads(v) if v else None
def node():return api('GET','/api/nodes/'+NODE)[1]
def run_cache_task(sha):
 source=(H/'cache_oracle_attempt03.py').read_text();cmd='python3 -c '+shlex.quote(source);cmdsha=hashlib.sha256(cmd.encode()).hexdigest();task={'name':'qa-resume-20261009-p0-002-v2-attempt3-cache-oracle-'+uuid.uuid4().hex[:7],'type':'command','target':{'type':'node','node_ids':[NODE]},'parameters':[{'name':'app_id','type':'string','required':True},{'name':'artifact_sha','type':'string','required':True}],'command':cmd,'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True};tid=None;e={'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'command_sha256':cmdsha}
 try:
  _,t=api('POST','/api/tasks',task,(200,201));tid=t['id'];e['task_id']=tid;_,f=api('GET','/api/tasks/'+tid)
  if hashlib.sha256(f.get('command','').encode()).hexdigest()!=cmdsha:raise RuntimeError('Task readback SHA mismatch')
  _,xs=api('POST','/api/tasks/'+tid+'/run',{'params':{'app_id':APP,'artifact_sha':sha}},(200,201));
  if len(xs)!=1:raise RuntimeError('expected one cache oracle execution')
  xid=xs[0]['id'];e['execution_id']=xid;end=time.monotonic()+100;z=None
  while time.monotonic()<end:
   _,z=api('GET','/api/executions/'+xid)
   if z.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:break
   time.sleep(.25)
  out=(z or {}).get('stdout') or '';e['execution']={'id':xid,'status':(z or {}).get('status'),'node_id':(z or {}).get('node_id'),'stdout_json':json.loads(out) if out else None,'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stderr_bytes':len(((z or {}).get('stderr') or '').encode())}
 finally:
  if tid:
   d,_=api('DELETE','/api/tasks/'+tid,None,(200,204));g,_=api('GET','/api/tasks/'+tid,None,(404,));e['cleanup']={'delete_http_status':d,'verify_get_http_status':g,'verified_404':g==404}
 return e
_,node_before=api('GET','/api/nodes/'+NODE);_,app_before=api('GET','/api/applications/'+APP);_,assign=api('GET','/api/applications/'+APP+'/nodes');_,artifact=api('GET','/api/artifacts/'+ART)
if node_before.get('status')!='online' or node_before.get('sync_status')!='synced' or app_before.get('version')!='v1' or app_before.get('artifact_id')!='0c74fcee-7689-49a4-8206-3fa5f6eba746' or assign!=[NODE] or artifact.get('sha256')!=SHA or int(artifact.get('size',0))!=SIZE:raise SystemExit('blocked: precondition mismatch; preserve current fixture')
e={'case_id':'E2E-20260930-101945-P0-002','stage':'ATTEMPT03_REUSE_APPROVED_64M_ARTIFACT_AND_SYNC_UNIQUE_VERSION','updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'reuse_existing_artifact':True,'artifact':{k:artifact.get(k) for k in ['id','name','version','size','sha256']},'v2_artifact':{k:artifact.get(k) for k in ['id','name','version','size','sha256']},'app_before':{k:app_before.get(k) for k in ['id','name','version','artifact_id','revision']},'node_before':{k:node_before.get(k) for k in ['id','status','sync_status']},'assigned_node_ids':assign,'v1_baseline_execution_id':'93283230-b8e3-4530-906e-e51a212f76ec','v1_baseline_execution_success':True}
config='QA_P002_VERSION='+VER+'\nQA_P002_NONCE=870358cd92ee\n';body={'name':NAME,'description':app_before.get('description',''),'artifact_id':ART,'version':VER,'binary_path':BIN,'config_path':CFG,'config':config,'unit_name':UNIT,'arguments':[],'environment':{'QA_P002_VERSION':VER,'QA_P002_NONCE':'870358cd92ee'},'health_check':app_before.get('health_check')};_,up=api('PUT','/api/applications/'+APP,body,(200,201));e['app_definition_after_sync']={k:up.get(k) for k in ['id','name','version','artifact_id','revision','unit_name']}
end=time.monotonic()+60;n=node()
while (n.get('status')!='online' or n.get('sync_status')!='synced') and time.monotonic()<end:time.sleep(.5);n=node()
e['node_after_definition_sync']={k:n.get(k) for k in ['id','status','sync_status','agent_version']}
if n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('child failed to sync attempt03 definition')
e['cache_oracle']=run_cache_task(SHA);o=e['cache_oracle'].get('execution',{}).get('stdout_json') or {};e['application_state_after_prepare']=[{k:s.get(k) for k in ['version','operation','health','updated_at']} for s in api('GET','/api/applications/'+APP+'/state')[1] if s.get('node_id')==NODE];latest=o.get('latest_deployment') or {};e['assertions']={'app_version_attempt3':e['app_definition_after_sync'].get('version')==VER,'reused_64m_artifact_sha_size':artifact.get('sha256')==SHA and int(artifact.get('size',0))==SIZE,'real_agent_cache_sha_size_path':o.get('v2_cache',{}).get('actual_sha256')==SHA and o.get('v2_cache',{}).get('size')==SIZE and o.get('v2_cache',{}).get('registered_path')==o.get('v2_cache',{}).get('path'),'physical_v1_files_unit_active':all([o.get('v1_physical_state',{}).get('binary',{}).get('matches_v1'),o.get('v1_physical_state',{}).get('config',{}).get('matches_v1'),o.get('v1_physical_state',{}).get('unit',{}).get('matches_v1'),o.get('v1_physical_state',{}).get('unit',{}).get('active_state')=='active']),'no_attempt03_journal':o.get('v2_deployment_journal_count')==0,'latest_finished_v1_journal':latest.get('to_version')=='v1' and latest.get('phase')=='DONE','child_online_synced':n.get('status')=='online' and n.get('sync_status')=='synced','no_fault_deploy_invoked':True}
e['status']='V2_ATTEMPT03_READY_FOR_GUARDED_FAULT' if all(e['assertions'].values()) and e['cache_oracle'].get('execution',{}).get('status')=='SUCCESS' else 'ATTEMPT03_PREPARE_PARTIAL';e['stage_status']=e['status'];e['deploy_attempt03_invoked']=False;e['deploy_v2_invoked']=False;e['fault_injected']=False;OUT.write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':e['status'],'artifact_id':ART,'artifact_sha256':SHA,'artifact_size':SIZE,'app_version':VER,'fault_injected':False},ensure_ascii=False))
