import json,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));SCRIPT='20a79944-5581-494b-83d8-5b365738c1e4';TASK='fdd19200-7fbd-42ad-935e-40fb80e57b0d';SCRIPT_NAME='qa-resume-20261010-db-write-lock-script-attempt02-870358cd92ee';TASK_NAME='qa-resume-20261010-db-write-lock-task-attempt02-870358cd92ee';V2SHA='2145f8d114e17ca06d69e640e328662ca3ee370f17922909b1dc4e8477b94387'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:status,raw=r.status,r.read()
 except HTTPError as e:status,raw=e.code,e.read()
 if status not in allowed:raise RuntimeError(f'{m} {p} HTTP {status}')
 return status,(json.loads(raw) if raw else None)
def absent(path):
 try:api('GET',path,None,(404,));return True
 except Exception:return False
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
script=api('GET','/api/scripts/'+SCRIPT)[1];task=api('GET','/api/tasks/'+TASK)[1];schedules=api('GET','/api/schedules')[1]
if script.get('id')!=SCRIPT or script.get('name')!=SCRIPT_NAME or script.get('revision')!=2 or script.get('sha256')!=V2SHA:raise SystemExit('exact owned Script cleanup guard failed')
if task.get('id')!=TASK or task.get('name')!=TASK_NAME or task.get('type')!='script' or task.get('script_id')!=SCRIPT or task.get('target',{}).get('node_ids')!=['15961ef7-81b4-43f3-80c8-1b6977ca2aea'] or task.get('retry')!=0:raise SystemExit('exact owned Task cleanup guard failed')
if any(x.get('task_id')==TASK for x in schedules):raise SystemExit('unexpected Schedule reference; abort cleanup')
_,delete_task=api('DELETE','/api/tasks/'+TASK,None,(200,204));task_404=absent('/api/tasks/'+TASK)
if not task_404:raise SystemExit('Task did not return GET 404')
_,delete_script=api('DELETE','/api/scripts/'+SCRIPT,None,(200,204));script_404=absent('/api/scripts/'+SCRIPT)
if not script_404:raise SystemExit('Script did not return GET 404')
out={'cleaned_at_utc':utc(),'task_id':TASK,'task_delete_http_status':delete_task,'task_get_404':task_404,'script_id':SCRIPT,'script_delete_http_status':delete_script,'script_get_404':script_404,'execution_id_preserved':'29142a25-7fbe-4a54-b5e8-2c164581a58a','schedule_reference_count_before_delete':sum(x.get('task_id')==TASK for x in schedules),'credentials_emitted':False}
(H/'cleanup-api-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'HUB_SCRIPT_TASK_DELETED_GET404_WAITING_LOCAL_TOMBSTONES_AND_MARKER_CLEANUP','updated_at_utc':utc(),'cleanup_api_evidence':'cleanup-api-evidence.json','script_get404':script_404,'task_get404':task_404,'next_action':'Run one guarded exact parent Command Task to verify/remove marker, then read child DB rows/tombstones; preserve Execution history.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
