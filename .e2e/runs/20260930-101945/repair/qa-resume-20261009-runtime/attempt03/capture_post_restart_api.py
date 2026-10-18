import json,time
from pathlib import Path
from urllib.request import Request,ProxyHandler,build_opener
from urllib.parse import urlsplit
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');tok=c['token'];op=build_opener(ProxyHandler({}));APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';EXEC='2bb4e175-af11-471a-9c03-c01bd391a51a'
if urlsplit(base).port!=18080:raise SystemExit('blocked endpoint')
def get(p):
 with op.open(Request(base+p,headers={'Authorization':'Bearer '+tok}),timeout=10) as r:return json.load(r)
end=time.monotonic()+60;node=app=states=ex=None
while time.monotonic()<end:
 node=get('/api/nodes/'+NODE);app=get('/api/applications/'+APP);states=get('/api/applications/'+APP+'/state');ex=get('/api/executions/'+EXEC)
 if node.get('status')=='online' and node.get('sync_status')=='synced':break
 time.sleep(.4)
out={'captured_unix':int(time.time()),'endpoint_port':urlsplit(base).port,'node':{k:node.get(k) for k in ['id','status','sync_status','agent_version']},'app_definition':{k:app.get(k) for k in ['id','version','artifact_id','revision']},'application_state':[ {k:s.get(k) for k in ['version','operation','health','updated_at']} for s in states if s.get('node_id')==NODE],'deployment_execution':{k:ex.get(k) for k in ['id','status','node_id','task_id','block_reason','start_time','end_time']},'attempt03_desired_version_preserved':app.get('version')=='v2-attempt3','cancel_invoked':False,'manual_put_after_attempt03_fault':False}
Path(__file__).with_name('post-restart-api-state-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
