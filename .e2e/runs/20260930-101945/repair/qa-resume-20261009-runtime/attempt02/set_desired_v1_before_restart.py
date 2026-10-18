import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;P=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');c=json.loads((P/'repair-connection.json').read_text());base=c['hub_url'].rstrip('/');tok=c['token'];op=build_opener(ProxyHandler({}));APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';EXPECTED='93ccd9eb-14b6-4a26-bceb-3f4c13de6061';V1ART='0c74fcee-7689-49a4-8206-3fa5f6eba746';NAME='qa-resume-20261009-native1-p0-002-app-870358cd92ee';BIN='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/bin/qa-app';CFG='/tmp/nodesteer-e2e-20260930-??';CFG='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/app/config/qa-app.env';UNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';N='870358cd92ee';OUT=H/'desired-v1-before-restart-evidence.json'
if urlsplit(base).port!=18080:raise SystemExit('blocked endpoint')
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+tok};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,v=r.status,r.read()
 except HTTPError as e:s,v=e.code,e.read()
 if s not in allow:raise RuntimeError(m+' unexpected HTTP '+str(s))
 return s,json.loads(v) if v else None
_,a=api('GET','/api/applications/'+APP)
if a.get('id')!=APP or a.get('name')!=NAME or a.get('version')!='v2-attempt2' or a.get('artifact_id')!=EXPECTED or a.get('binary_path')!=BIN or a.get('config_path')!=CFG or a.get('unit_name')!=UNIT:raise SystemExit('blocked: current App is not exact attempt02 owned definition')
config='QA_P002_VERSION=v1\nQA_P002_NONCE='+N+'\n';body={'name':NAME,'description':a.get('description',''),'artifact_id':V1ART,'version':'v1','binary_path':BIN,'config_path':CFG,'config':config,'unit_name':UNIT,'arguments':[],'environment':{'QA_P002_VERSION':'v1','QA_P002_NONCE':N},'health_check':a.get('health_check')}
_,u=api('PUT','/api/applications/'+APP,body,(200,201));_,r=api('GET','/api/applications/'+APP)
assert r.get('version')=='v1' and r.get('artifact_id')==V1ART
x={'updated_unix':int(time.time()),'endpoint_port':urlsplit(base).port,'application_id':APP,'before':{k:a.get(k) for k in ['version','artifact_id','revision']},'after':{k:r.get(k) for k in ['version','artifact_id','revision']},'config_sha256':hashlib.sha256(config.encode()).hexdigest(),'deployment_invoked':False,'status':'DESIRED_V1_RESTORED_NO_DEPLOY'};OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':x['status'],'revision':r.get('revision')}))
