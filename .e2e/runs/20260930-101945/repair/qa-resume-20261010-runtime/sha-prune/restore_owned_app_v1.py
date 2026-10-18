import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];OUT=H/'restore-owned-app-v1-evidence.json';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';ART='0c74fcee-7689-49a4-8206-3fa5f6eba746';NODE='c77b8a84-8085-4ea8-8532-de171761854b';NAME='qa-resume-20261009-native1-p0-002-app-870358cd92ee';BIN='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/bin/qa-app';CFG='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/config/qa-app.env';UNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';NONCE='870358cd92ee';op=build_opener(ProxyHandler({}))
if urlsplit(base).port!=18080:raise SystemExit('blocked: not repair :18080')
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,v=r.status,r.read()
 except HTTPError as e:s,v=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError('API '+type(e).__name__)
 if s not in allow:raise RuntimeError(m+' unexpected HTTP '+str(s))
 return s,json.loads(v) if v else None
def wait_exec(eid,timeout=60):
 end=time.monotonic()+timeout;last=None
 while time.monotonic()<end:
  _,last=api('GET','/api/executions/'+eid)
  if last.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:return last
  time.sleep(.3)
 return last
e={'case_id':'E2E-20260930-101945-P0-002','purpose':'restore only the owned QA Application to physical v1 baseline for BE-SCRIPT-SHA-002','endpoint_guard':{'host':urlsplit(base).hostname,'port':urlsplit(base).port,'only_repair_18080':True},'app_id':APP,'child_node_id':NODE,'v1_artifact_id':ART,'status':'STARTED'}
_,cur=api('GET','/api/applications/'+APP)
if cur.get('id')!=APP or cur.get('name')!=NAME or cur.get('version')!='v2-attempt3' or cur.get('binary_path')!=BIN or cur.get('config_path')!=CFG or cur.get('unit_name')!=UNIT:raise SystemExit('blocked: exact owned Application is not at expected v2 definition')
e['before']={k:cur.get(k) for k in ['id','name','version','artifact_id','revision','binary_path','config_path','unit_name']}
config='QA_P002_VERSION=v1\nQA_P002_NONCE='+NONCE+'\n'
update={'name':NAME,'description':cur.get('description',''),'artifact_id':ART,'version':'v1','binary_path':BIN,'config_path':CFG,'config':config,'unit_name':UNIT,'arguments':[],'environment':{'QA_P002_VERSION':'v1','QA_P002_NONCE':NONCE},'health_check':cur.get('health_check') or {'type':'systemd','timeout':2,'attempts':5,'interval':1}}
_,updated=api('PUT','/api/applications/'+APP,update,(200,201));e['updated_definition']={k:updated.get(k) for k in ['id','name','version','artifact_id','revision','binary_path','config_path','unit_name']}
if updated.get('version')!='v1' or updated.get('artifact_id')!=ART:raise RuntimeError('v1 definition readback mismatch')
_,node=api('GET','/api/nodes/'+NODE);end=time.monotonic()+60
while (node.get('status')!='online' or node.get('sync_status')!='synced') and time.monotonic()<end:
 time.sleep(.5);_,node=api('GET','/api/nodes/'+NODE)
e['child_before_deploy']={k:node.get(k) for k in ['id','status','sync_status','agent_version']}
if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('owned child not ready')
_,ex=api('POST','/api/applications/'+APP+'/deploy',{'node_ids':[NODE],'operation':'upgrade'},(200,201))
if len(ex)!=1:raise RuntimeError('expected exactly one v1 restoration Execution')
eid=ex[0]['id'];e['restore_execution_id']=eid;fin=wait_exec(eid);e['restore_execution']={k:fin.get(k) for k in ['id','task_id','node_id','status','exit_code','trigger_type','started_at','ended_at']}
e['application_state']=api('GET','/api/applications/'+APP+'/state')[1];e['child_after']=api('GET','/api/nodes/'+NODE)[1]
e['status']='V1_RESTORE_'+str(fin.get('status'))
OUT.write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':e['status'],'execution_id':eid,'child_status':e['child_after'].get('status'),'app_state':e['application_state']},ensure_ascii=False))
