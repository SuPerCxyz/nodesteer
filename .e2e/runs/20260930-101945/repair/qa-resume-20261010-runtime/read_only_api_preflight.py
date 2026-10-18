import json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
B=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');tok=priv['token'];op=build_opener(ProxyHandler({}));HN='91f44866-8a28-435e-bbda-2f2367126c00';CN='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';ART=['0c74fcee-7689-49a4-8206-3fa5f6eba746','5517ff25-65b1-4da3-b5d7-a3f86f0de73d','93ccd9eb-14b6-4a26-bceb-3f4c13de6061']
if urlsplit(base).port!=18080:raise SystemExit('blocked: only repair :18080')
def get(p):
 with op.open(Request(base+p,headers={'Authorization':'Bearer '+tok}),timeout=15) as r:return json.load(r)
h=get('/api/nodes/'+HN);c=get('/api/nodes/'+CN);a=get('/api/applications/'+APP);states=get('/api/applications/'+APP+'/state');assigned=get('/api/applications/'+APP+'/nodes');tasks=get('/api/tasks');arts=get('/api/artifacts');settings=get('/api/settings')
out={'captured_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint':{'host':urlsplit(base).hostname,'port':urlsplit(base).port,'only_repair_18080':True},'host_node':{k:h.get(k) for k in ['id','hostname','status','sync_status','global_revision','agent_version']},'child_node':{k:c.get(k) for k in ['id','hostname','status','sync_status','global_revision','agent_version','deployment_mode']},'application':{k:a.get(k) for k in ['id','name','version','artifact_id','revision','unit_name','binary_path','config_path']},'application_state':[ {k:s.get(k) for k in ['node_id','version','operation','health','updated_at']} for s in states if s.get('node_id')==CN ],'assigned_node_ids':assigned,'owned_artifacts':[ {k:x.get(k) for k in ['id','name','version','size','sha256']} for x in arts if x.get('id') in ART ],'qa_tasks_for_host_or_child':[{'id':t.get('id'),'name':t.get('name'),'target':t.get('target'),'retry':t.get('retry'),'timeout':t.get('timeout')} for t in tasks if set((t.get('target') or {}).get('node_ids') or [])&{HN,CN}], 'changelog_window':settings.get('changelog_window')}
(B/'read-only-api-preflight.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
