import json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773'
if urlsplit(base).port!=18080:raise SystemExit('repair port guard failed')
def get(p):
 with op.open(Request(base+p,headers={'Authorization':'Bearer '+token}),timeout=15) as r:return json.load(r)
child=get('/api/nodes/'+CHILD);parent=get('/api/nodes/'+PARENT);tasks=get('/api/tasks');schedules=get('/api/schedules')
try:ex=get('/api/executions?node_id='+CHILD+'&limit=100')
except Exception:ex=[]
if isinstance(ex,dict):ex=ex.get('executions',ex.get('items',[]))
child_tasks=[{'id':x.get('id'),'name':x.get('name'),'type':x.get('type'),'script_id':x.get('script_id'),'application_id':x.get('application_id'),'target_node_ids':(x.get('target') or {}).get('node_ids',[]),'retry':x.get('retry'),'enabled':x.get('enabled')} for x in tasks if CHILD in ((x.get('target') or {}).get('node_ids') or [])]
child_task_ids={x['id'] for x in child_tasks};child_schedules=[{'id':s.get('id'),'task_id':s.get('task_id'),'enabled':s.get('enabled')} for s in schedules if s.get('task_id') in child_task_ids]
child_ex=[{'id':x.get('id'),'task_id':x.get('task_id'),'status':x.get('status'),'node_id':x.get('node_id')} for x in ex if x.get('node_id')==CHILD]
out={'captured_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint_port':18080,'child':{k:child.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision','last_seen','deployment_mode']},'parent':{k:parent.get(k) for k in ['id','status','sync_status','global_revision','last_seen']},'child_tasks':child_tasks,'child_schedules':child_schedules,'child_executions':child_ex,'running_pending_child_execution_count':sum(x.get('status','').upper() in ['RUNNING','PENDING'] for x in child_ex),'all_task_definitions':len(tasks),'all_schedule_definitions':len(schedules),'credentials_or_command_contents_emitted':False}
(H/'api-preflight.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
