import json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def get(path):
 with op.open(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=15) as r:return json.load(r)
child=get('/api/nodes/'+CHILD);parent=get('/api/nodes/'+PARENT);tasks=get('/api/tasks');schedules=get('/api/schedules')
child_tasks=[{'id':t.get('id'),'name':t.get('name'),'type':t.get('type'),'script_id':t.get('script_id'),'application_id':t.get('application_id'),'target':t.get('target'),'retry':t.get('retry'),'timeout':t.get('timeout')} for t in tasks if CHILD in ((t.get('target') or {}).get('node_ids') or [])]
child_task_ids={x['id'] for x in child_tasks};child_schedules=[{'id':s.get('id'),'name':s.get('name'),'task_id':s.get('task_id'),'enabled':s.get('enabled'),'schedule_type':s.get('type')} for s in schedules if s.get('task_id') in child_task_ids]
out={'captured_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint':{'host':urlsplit(base).hostname,'port':urlsplit(base).port,'repair_18080_only':True},'child':{k:child.get(k) for k in ['id','agent_id','hostname','ip','status','sync_status','global_revision','last_seen','deployment_mode','host_integration']},'parent':{k:parent.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision','last_seen','deployment_mode']},'child_targeted_tasks':child_tasks,'schedules_for_child_tasks':child_schedules,'task_definitions_total':len(tasks),'schedule_definitions_total':len(schedules)}
(H/'api-preflight.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
