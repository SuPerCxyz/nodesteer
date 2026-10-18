import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');tok=priv['token'];op=build_opener(ProxyHandler({}));APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';ART='0c74fcee-7689-49a4-8206-3fa5f6eba746';NAME='qa-resume-20261009-native1-p0-002-app-870358cd92ee';OUT=H/'v1-baseline-deploy-evidence.json'
if urlsplit(base).port!=18080:raise SystemExit('blocked endpoint; only repair :18080')
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+tok};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,v=r.status,r.read()
 except HTTPError as e:s,v=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError(type(e).__name__)
 if s not in allow:raise RuntimeError(m+' HTTP '+str(s))
 return s,json.loads(v) if v else None
def final_exec(eid,timeout=60):
 end=time.monotonic()+timeout;z=None
 while time.monotonic()<end:
  _,z=api('GET','/api/executions/'+eid)
  if z.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:return z
  time.sleep(.25)
 return z
_,node=api('GET','/api/nodes/'+NODE);_,app=api('GET','/api/applications/'+APP);_,states=api('GET','/api/applications/'+APP+'/state');_,assigned=api('GET','/api/applications/'+APP+'/nodes')
if node.get('status')!='online' or node.get('sync_status')!='synced' or app.get('name')!=NAME or app.get('version')!='v1' or app.get('artifact_id')!=ART or assigned!=[NODE]:raise SystemExit('blocked: exact owned child/App is not ready for v1 baseline')
e={'case_id':'E2E-20260930-101945-P0-002','purpose':'real v1 deploy to re-establish healthy state after attempt02, before attempt03','captured_before':{'node':{k:node.get(k) for k in ['id','status','sync_status']},'application':{k:app.get(k) for k in ['id','version','artifact_id','revision']},'application_state':[ {k:s.get(k) for k in ['version','operation','health','updated_at']} for s in states if s.get('node_id')==NODE]},'assigned_node_ids':assigned}
_,runs=api('POST','/api/applications/'+APP+'/deploy',{'node_ids':[NODE],'operation':'upgrade'},(200,201))
if len(runs)!=1:raise RuntimeError('expected one exact child v1 baseline execution')
eid=runs[0]['id'];z=final_exec(eid);e['execution_id']=eid;e['execution']={k:z.get(k) for k in ['id','task_id','node_id','status','exit_code','trigger_type','start_time','end_time','block_reason']};e['child_after']=api('GET','/api/nodes/'+NODE)[1];e['application_state_after']=api('GET','/api/applications/'+APP+'/state')[1];e['status']='V1_BASELINE_'+str(z.get('status'));OUT.write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':e['status'],'execution_id':eid,'child_status':e['child_after'].get('status'),'app_state':e['application_state_after']},ensure_ascii=False))
