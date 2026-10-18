import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';GOOD='fc6fbcc6-39ce-49f4-9368-265a5a42d174';BAD='616a9db6-a87f-4d79-a4de-7d1b016e2986';TASKS={'d43ded1e-f073-461e-b06c-24005789d4c3':('qa-resume-20261010-sha-prune-run-good-v2-870358cd92ee',GOOD),'520d7d8d-ccf1-4456-a27f-4fd6b39bf4ce':('qa-resume-20261010-sha-prune-run-bad-v2-870358cd92ee',BAD)};ARTS={'0c74fcee-7689-49a4-8206-3fa5f6eba746':('qa-resume-20261009-native1-p0-002-artifact-870358cd92ee','v1','35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c'),'5517ff25-65b1-4da3-b5d7-a3f86f0de73d':('qa-resume-20261009-native1-p0-002-artifact-870358cd92ee-v2','v2','e978740d1f6e40e831013a1ccd60ecd6d27e2c67ba1389d4df23bcbd4801cd46'),'93ccd9eb-14b6-4a26-bceb-3f4c13de6061':('qa-resume-20261009-native1-p0-002-artifact-870358cd92ee-v2-attempt2','v2-attempt2','145333b2830f512362ca1ef187d92599cb022b07666e8a53e6f8f0dd5bd16317'),'6ce76a15-f3de-42b9-b7d4-63e4d02edfc4':('qa-resume-20261010-sha-prune-proxy-binary-870358cd92ee','guarded-v3','9b5f1e9708a440fdaadd97836e64f4156b281bb17e7bfb73c42e6a3bb38b50ef')}
if urlsplit(base).port!=18080:raise SystemExit('endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return s,(json.loads(x) if x else None)
def get(p):return api('GET',p)[1]
# Verify child is truly offline before destructive definitions are removed.
node=None;end=time.monotonic()+20
while time.monotonic()<end:
 node=get('/api/nodes/'+NODE)
 if node.get('status')=='offline':break
 time.sleep(.5)
if not node or node.get('id')!=NODE or node.get('agent_id')!='agent-426aa507-af4c-4005-858f-ce497d2fc67d' or node.get('hostname')!='qa-resume-20261009-native1-p0-002-child-870358cd92ee' or node.get('status')!='offline':raise SystemExit('exact QA child must be offline before API cleanup')
a=get('/api/applications/'+APP)
if a.get('id')!=APP or a.get('name')!='qa-resume-20261009-native1-p0-002-app-870358cd92ee' or a.get('version')!='v1' or a.get('revision')!=7:raise SystemExit('exact QA App readback mismatch')
assigned=get('/api/applications/'+APP+'/nodes');state=get('/api/applications/'+APP+'/state')
if assigned not in (None,[]) or any(x.get('node_id')==NODE for x in state):raise SystemExit('App still has assignment/state')
tasks=get('/api/tasks');schedules=get('/api/schedules');taskmap={x.get('id'):x for x in tasks}
if set(taskmap)!=set(TASKS):raise SystemExit('unexpected task definitions present; refuse broad cleanup')
for tid,(name,sid) in TASKS.items():
 t=taskmap[tid]
 if t.get('name')!=name or t.get('type')!='script' or t.get('script_id')!=sid or t.get('target',{}).get('node_ids')!=[NODE] or t.get('retry')!=0 or t.get('timeout')!=60 or any(x.get('task_id')==tid for x in schedules):raise SystemExit('exact Task/Schedule readback mismatch '+tid)
script_expected={GOOD:('qa-resume-20261010-sha-prune-good-870358cd92ee',3,'5bd147794c5df9113947cd8d98ffce8e6569188e6f0910862754e193c338a115'),BAD:('qa-resume-20261010-sha-prune-bad-870358cd92ee',2,'ae33f3872073eb53d6c08656cd70c8809f7a4e04db8ba5f3f5d3c591736afd64')}
for sid,(name,rev,sha) in script_expected.items():
 x=get('/api/scripts/'+sid)
 if x.get('id')!=sid or x.get('name')!=name or x.get('revision')!=rev or x.get('sha256')!=sha:raise SystemExit('exact Script readback mismatch '+sid)
allarts=get('/api/artifacts');artmap={x.get('id'):x for x in allarts};qaids=set(ARTS)
if {x['id'] for x in allarts if '870358cd92ee' in (x.get('name') or '') or 'qa-resume-202610' in (x.get('name') or '')}!=qaids:raise SystemExit('unexpected or missing QA Artifact; refuse broad cleanup')
for aid,(name,version,sha) in ARTS.items():
 x=artmap.get(aid)
 if not x or x.get('name')!=name or x.get('version')!=version or x.get('sha256')!=sha:raise SystemExit('exact Artifact readback mismatch '+aid)
e={'endpoint_port':18080,'node_before':{k:node.get(k) for k in ['id','hostname','agent_id','status','global_revision']},'app_before':{k:a.get(k) for k in ['id','name','version','revision']},'app_assignment_before':assigned,'app_state_for_child_before':[x for x in state if x.get('node_id')==NODE],'task_ids_before':sorted(taskmap),'script_ids_before':sorted(script_expected),'artifact_ids_before':sorted(ARTS),'delete_results':{'tasks':{},'app':None,'scripts':{},'artifacts':{},'node':None}}
# Remove only exact current definitions. Execution and Audit endpoints are never called with DELETE.
for tid in TASKS:
 s,_=api('DELETE','/api/tasks/'+tid,None,(200,204));
 try:api('GET','/api/tasks/'+tid,None,(404,));after=404
 except Exception:after='not-404'
 if after!=404:raise SystemExit('Task did not reach GET404 '+tid)
 e['delete_results']['tasks'][tid]={'delete_status':s,'get_after':404}
s,_=api('DELETE','/api/applications/'+APP,None,(200,204));
try:api('GET','/api/applications/'+APP,None,(404,));after=404
except Exception:after='not-404'
if after!=404:raise SystemExit('App did not reach GET404')
e['delete_results']['app']={'delete_status':s,'get_after':404}
for sid in script_expected:
 s,_=api('DELETE','/api/scripts/'+sid,None,(200,204))
 try:api('GET','/api/scripts/'+sid,None,(404,));after=404
 except Exception:after='not-404'
 if after!=404:raise SystemExit('Script did not reach GET404 '+sid)
 e['delete_results']['scripts'][sid]={'delete_status':s,'get_after':404}
for aid in ARTS:
 s,_=api('DELETE','/api/artifacts/'+aid,None,(200,204))
 try:api('GET','/api/artifacts/'+aid,None,(404,));after=404
 except Exception:after='not-404'
 if after!=404:raise SystemExit('Artifact did not reach GET404 '+aid)
 e['delete_results']['artifacts'][aid]={'delete_status':s,'get_after':404}
# Node remains offline; delete only the exact child. Node deletion implementation preserves execution history.
node=get('/api/nodes/'+NODE)
if node.get('id')!=NODE or node.get('status')!='offline' or node.get('agent_id')!='agent-426aa507-af4c-4005-858f-ce497d2fc67d':raise SystemExit('exact offline Node guard changed before delete')
s,_=api('DELETE','/api/nodes/'+NODE,None,(200,204));
try:api('GET','/api/nodes/'+NODE,None,(404,));after=404
except Exception:after='not-404'
if after!=404:raise SystemExit('Node did not reach GET404')
e['delete_results']['node']={'delete_status':s,'get_after':404}
# One known, previously deleted artifact is verified 404; don't turn that historical event into another deletion.
try:api('GET','/api/artifacts/1285f7a6-6b65-4c76-9d97-e62b9c6ee849',None,(404,));e['already_absent_proxy_artifact_get']=404
except Exception:e['already_absent_proxy_artifact_get']='unexpectedly-present'
(H/'hub-fixture-cleanup-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':'EXACT_HUB_FIXTURES_DELETED_GET404','counts':{'tasks':len(e['delete_results']['tasks']),'scripts':len(e['delete_results']['scripts']),'artifacts':len(e['delete_results']['artifacts'])},'app':e['delete_results']['app'],'node':e['delete_results']['node'],'already_absent_proxy_artifact_get':e.get('already_absent_proxy_artifact_get')},ensure_ascii=False))
