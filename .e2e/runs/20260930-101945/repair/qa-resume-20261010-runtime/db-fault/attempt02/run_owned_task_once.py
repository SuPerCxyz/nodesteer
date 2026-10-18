import hashlib,json,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';SCRIPT='20a79944-5581-494b-83d8-5b365738c1e4';TASK='fdd19200-7fbd-42ad-935e-40fb80e57b0d';V2SHA='2145f8d114e17ca06d69e640e328662ca3ee370f17922909b1dc4e8477b94387';MARKER='DBLOCK_V2_A2_870358CD92EE'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:status,raw=r.status,r.read()
 except HTTPError as e:status,raw=e.code,e.read()
 if status not in allowed:raise RuntimeError(f'{m} {p} HTTP {status}')
 return status,(json.loads(raw) if raw else None)
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
node=api('GET','/api/nodes/'+NODE)[1];script=api('GET','/api/scripts/'+SCRIPT)[1];task=api('GET','/api/tasks/'+TASK)[1]
if node.get('id')!=NODE or node.get('status')!='online' or node.get('sync_status')!='synced':raise SystemExit('owned child must be online+synced immediately before Run')
if script.get('id')!=SCRIPT or script.get('revision')!=2 or script.get('sha256')!=V2SHA or not script.get('enabled'):raise SystemExit('Hub Script v2 exact guard failed')
if task.get('id')!=TASK or task.get('type')!='script' or task.get('script_id')!=SCRIPT or task.get('target',{}).get('node_ids')!=[NODE] or task.get('retry')!=0 or task.get('timeout')!=60 or not task.get('enabled'):raise SystemExit('exact child Task guard failed')
start_utc=utc();start_mono=time.monotonic();run_status,body=api('POST','/api/tasks/'+TASK+'/run',{'params':{}},(200,201))
rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
if len(rows)!=1 or not isinstance(rows[0],dict) or not rows[0].get('id'):raise RuntimeError('unexpected Run response cardinality/shape; execution result not asserted')
eid=rows[0]['id'];deadline=time.monotonic()+70;timeline=[];final=None
while time.monotonic()<deadline:
 final=api('GET','/api/executions/'+eid)[1];state=final.get('status');v={'at_utc':utc(),'status':state,'node_id':final.get('node_id'),'task_id':final.get('task_id')}
 if not timeline or timeline[-1]['status']!=state:timeline.append(v)
 if state in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
 time.sleep(.5)
logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else []
logs=sorted(logs,key=lambda x:(x.get('stream',''),x.get('seq',0)));stdout=''.join(x.get('chunk','') for x in logs if x.get('stream')=='stdout');stderr=''.join(x.get('chunk','') for x in logs if x.get('stream')=='stderr')
marker_seen=MARKER in stdout and MARKER not in stderr
out={'task_id':TASK,'script_id':SCRIPT,'script_v2_sha256':V2SHA,'execution_id':eid,'run_http_status':run_status,'execution_start_at_utc':start_utc,'execution_end_at_utc':utc(),'execution_elapsed_monotonic_seconds':round(time.monotonic()-start_mono,3),'execution_status':final.get('status'),'execution_node_id':final.get('node_id'),'execution_task_id':final.get('task_id'),'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stderr_sha256':hashlib.sha256(stderr.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'stderr_bytes':len(stderr.encode()),'expected_marker':MARKER,'marker_seen_stdout':marker_seen,'log_chunk_count':len(logs),'timeline':timeline,'credentials_emitted':False}
(H/'run-execution-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'OWNED_TASK_RUN_SUCCESS' if final.get('status')=='SUCCESS' and marker_seen else 'OWNED_TASK_RUN_FAILED_OR_MARKER_MISSING','updated_at_utc':utc(),'execution_evidence':'run-execution-evidence.json','execution_id':eid,'execution_status':final.get('status'),'execution_elapsed_monotonic_seconds':out['execution_elapsed_monotonic_seconds'],'marker_seen_stdout':marker_seen,'next_action':'Inspect the actual Execution detail in the UI; then exact cleanup Script/Task/marker and verify tombstone.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'execution_id':eid,'status':final.get('status'),'execution_elapsed_monotonic_seconds':out['execution_elapsed_monotonic_seconds'],'marker_seen_stdout':marker_seen,'stdout_bytes':out['stdout_bytes'],'stderr_bytes':out['stderr_bytes']},ensure_ascii=False))
