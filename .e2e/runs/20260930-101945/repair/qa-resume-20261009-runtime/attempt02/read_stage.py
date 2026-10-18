import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');tok=priv['token'];op=build_opener(ProxyHandler({}));APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';ARTNAME='qa-resume-20261009-native1-p0-002-artifact-870358cd92ee-v2-attempt2'
if urlsplit(base).port!=18080:raise SystemExit('blocked endpoint')
def get(p):
 r=Request(base+p,headers={'Authorization':'Bearer '+tok})
 try:
  with op.open(r,timeout=15) as x:return json.load(x)
 except HTTPError as e:raise RuntimeError('API GET '+p+' HTTP '+str(e.code))
node=get('/api/nodes/'+NODE);app=get('/api/applications/'+APP);states=get('/api/applications/'+APP+'/state');arts=get('/api/artifacts')
relevant=[{k:a.get(k) for k in ['id','name','version','architecture','size','sha256']} for a in arts if a.get('name') in {ARTNAME,'qa-resume-20261009-native1-p0-002-artifact-870358cd92ee-v2'}]
out={'updated_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint_guard':{'host':urlsplit(base).hostname,'port':urlsplit(base).port,'only_repair_18080':True},'node':{k:node.get(k) for k in ['id','hostname','status','sync_status','agent_version','deployment_mode']},'app':{k:app.get(k) for k in ['id','name','artifact_id','version','revision','binary_path','config_path','unit_name']},'app_state':[ {k:s.get(k) for k in ['application_id','node_id','version','operation','health','updated_at']} for s in states if s.get('node_id')==NODE], 'attempt2_artifacts_existing':relevant,'attempt2_artifact_name_absent':not any(a.get('name')==ARTNAME for a in arts),'last_definition_revision':app.get('revision'),'status':'PREFLIGHT_READ_ONLY'}
assert node.get('status')=='online' and node.get('sync_status')=='synced' and app.get('id')==APP and app.get('version')=='v1' and app.get('artifact_id')=='0c74fcee-7689-49a4-8206-3fa5f6eba746' and out['attempt2_artifact_name_absent'] and any(s.get('version')=='v1' and s.get('health')=='healthy' for s in states if s.get('node_id')==NODE)
(H/'read-stage-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
