import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';NONCE='870358cd92ee';SCRIPT_NAME='qa-resume-20261010-db-write-lock-script-attempt02-870358cd92ee';TASK_NAME='qa-resume-20261010-db-write-lock-task-attempt02-870358cd92ee';MARKER='/tmp/qa-resume-20261008-child-ciivjupc/db-fault-marker.txt';CONTENT_V1="#!/bin/sh\nset -eu\nprintf '%s\\n' 'DBLOCK_V1_A2_870358CD92EE' > '/tmp/qa-resume-20261008-child-ciivjupc/db-fault-marker.txt'\n"
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return s,(json.loads(x) if x else None)
def get(p):return api('GET',p)[1]
child=get('/api/nodes/'+CHILD);parent=get('/api/nodes/'+PARENT)
if child.get('id')!=CHILD or child.get('agent_id')!='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5' or child.get('status')!='online' or child.get('sync_status')!='synced':raise SystemExit('owned child identity/health guard failed')
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise SystemExit('exact Native4 parent unavailable')
scripts=get('/api/scripts');tasks=get('/api/tasks');schedules=get('/api/schedules')
if any(x.get('name')==SCRIPT_NAME for x in scripts) or any(x.get('name')==TASK_NAME for x in tasks):raise SystemExit('QA fixture names already exist')
if any(CHILD in ((x.get('target') or {}).get('node_ids') or []) for x in tasks) or any(x.get('task_id') in {t.get('id') for t in tasks if CHILD in ((t.get('target') or {}).get('node_ids') or [])} for x in schedules):raise SystemExit('unexpected existing child Task/Schedule objects')
created_script=None;created_task=None;ev={'endpoint_port':18080,'child_before':{k:child.get(k) for k in ['id','agent_id','status','sync_status','global_revision']},'parent_before':{k:parent.get(k) for k in ['id','status','sync_status','global_revision']},'baseline_child_task_count':sum(CHILD in ((x.get('target') or {}).get('node_ids') or []) for x in tasks),'baseline_child_schedule_count':0,'script_name':SCRIPT_NAME,'task_name':TASK_NAME,'marker_path':MARKER,'script_v1_sha256':hashlib.sha256(CONTENT_V1.encode()).hexdigest()}
try:
 code,created_script=api('POST','/api/scripts',{'name':SCRIPT_NAME,'description':'Bounded isolated INV-05 SQLite write-lock marker fixture.','interpreter':'shell','content':CONTENT_V1,'parameters':[],'environment':{},'working_dir':'','timeout':10,'enabled':True},(200,201));sid=created_script['id'];script=get('/api/scripts/'+sid)
 if script.get('id')!=sid or script.get('name')!=SCRIPT_NAME or script.get('revision')!=1 or script.get('sha256')!=ev['script_v1_sha256']:raise RuntimeError('Script v1 exact readback mismatch')
 code,created_task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'script','script_id':sid,'target':{'type':'node','node_ids':[CHILD]},'parameters':[],'param_values':{},'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=created_task['id'];task=get('/api/tasks/'+tid)
 if task.get('id')!=tid or task.get('type')!='script' or task.get('script_id')!=sid or task.get('target',{}).get('node_ids')!=[CHILD] or task.get('retry')!=0 or task.get('timeout')!=60:raise RuntimeError('Script Task exact target/readback mismatch')
 schedules=get('/api/schedules');ev.update({'script_id':sid,'script_revision':script.get('revision'),'script_sha256':script.get('sha256'),'task_id':tid,'task_revision':task.get('revision'),'task_readback':{k:task.get(k) for k in ['id','type','script_id','target','retry','timeout','enabled']},'schedule_for_owned_task':any(x.get('task_id')==tid for x in schedules),'fixture_created':True,'completed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())})
 if ev['schedule_for_owned_task']:raise RuntimeError('fixture Task unexpectedly scheduled')
except Exception as err:
 ev['fixture_created']=False;ev['safe_error_type']=type(err).__name__
 if created_task:
  tid=created_task.get('id');api('DELETE','/api/tasks/'+tid,None,(200,204))
  try:api('GET','/api/tasks/'+tid,None,(404,));ev['partial_task_get404']=True
  except Exception:ev['partial_task_get404']=False
 if created_script:
  sid=created_script.get('id');api('DELETE','/api/scripts/'+sid,None,(200,204))
  try:api('GET','/api/scripts/'+sid,None,(404,));ev['partial_script_get404']=True
  except Exception:ev['partial_script_get404']=False
 raise
(H/'fixture-v1-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');(H/'fixture-ledger.json').write_text(json.dumps({'child_node_id':CHILD,'parent_node_id':PARENT,'script_id':ev['script_id'],'script_name':SCRIPT_NAME,'task_id':ev['task_id'],'task_name':TASK_NAME,'marker_path':MARKER,'script_v1_sha256':ev['script_v1_sha256'],'task_retry':0,'task_timeout':60,'task_schedule':False,'status':'ACTIVE_OWNED_QA_FIXTURE'},ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':'SCRIPT_V1_AND_CHILD_ONLY_TASK_CREATED','script_id':ev['script_id'],'task_id':ev['task_id'],'script_revision':ev['script_revision'],'script_sha256':ev['script_sha256'],'task_target':CHILD,'retry':0,'timeout':60,'schedule':False},ensure_ascii=False))
