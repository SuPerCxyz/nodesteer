import json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');op=build_opener(ProxyHandler({}));CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def get(path):
 with op.open(Request(base+path,headers={'Authorization':'Bearer '+priv['token']}),timeout=15) as r:return json.load(r)
child=get('/api/nodes/'+CHILD);parent=get('/api/nodes/'+PARENT);nodes=get('/api/nodes');tasks=get('/api/tasks');schedules=get('/api/schedules');ex=get('/api/executions?node_id='+CHILD+'&limit=100')
if isinstance(nodes,dict):nodes=nodes.get('nodes',nodes.get('items',[]))
if isinstance(ex,dict):ex=ex.get('executions',ex.get('items',[]))
original=[]
for n in nodes:
 if n.get('hostname') in {f'qa-repair-20260930-101945-native-{i}' for i in range(1,5)}:original.append({k:n.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision']})
ctasks=[{'id':x.get('id'),'name':x.get('name'),'type':x.get('type'),'target_node_ids':(x.get('target') or {}).get('node_ids',[]),'retry':x.get('retry'),'enabled':x.get('enabled')} for x in tasks if CHILD in ((x.get('target') or {}).get('node_ids') or [])]
cids={x['id'] for x in ctasks};csched=[{'id':x.get('id'),'task_id':x.get('task_id')} for x in schedules if x.get('task_id') in cids]
ce=[{'id':x.get('id'),'task_id':x.get('task_id'),'status':x.get('status'),'node_id':x.get('node_id')} for x in ex if x.get('node_id')==CHILD]
out={'captured_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint':{'port':18080,'repair_only':True},'child':{k:child.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision','last_seen']},'parent':{k:parent.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision','last_seen']},'original_four':original,'child_tasks':ctasks,'child_schedules':csched,'child_executions':ce,'running_or_pending_execution_count':sum(str(x.get('status','')).upper() in ['RUNNING','PENDING'] for x in ce),'task_definition_count':len(tasks),'schedule_definition_count':len(schedules),'credentials_or_command_contents_emitted':False}
(H/'api-preflight.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'captured_at_utc':out['captured_at_utc'],'child':out['child'],'parent':out['parent'],'original_four':original,'child_task_count':len(ctasks),'child_schedule_count':len(csched),'running_or_pending_execution_count':out['running_or_pending_execution_count'],'schedule_definition_count':len(schedules)},ensure_ascii=False))
