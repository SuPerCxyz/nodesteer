import hashlib,json,uuid,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from email.generator import _make_boundary
H=Path(__file__).resolve().parent;B=H/'ws-frame-proxy/ws-frame-proxy-stripped';NAME='qa-resume-20261010-sha-prune-proxy-binary-870358cd92ee';VERSION='guarded-v3';FILENAME='nodesteer-qa-sha-prune-proxy-v2.gz';EXPECTED_BINARY='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e'
from gzip import compress
binary=B.read_bytes();
if hashlib.sha256(binary).hexdigest()!=EXPECTED_BINARY:raise SystemExit('local binary SHA mismatch')
payload=compress(binary,compresslevel=9,mtime=0);artifact_sha=hashlib.sha256(payload).hexdigest()
priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token']
if urlsplit(base).port!=18080:raise SystemExit('blocked: endpoint is not repair :18080')
op=build_opener(ProxyHandler({}))
def api(m,path,raw=None,headers=None,allow=(200,)):
 h={'Authorization':'Bearer '+token};h.update(headers or {})
 try:
  with op.open(Request(base+path,data=raw,headers=h,method=m),timeout=60) as r:status,data=r.status,r.read()
 except HTTPError as e:status,data=e.code,b''
 except (URLError,TimeoutError) as e:raise RuntimeError('repair API transport '+type(e).__name__)
 if status not in allow:raise RuntimeError(f'{m} {path} HTTP {status}')
 return json.loads(data) if data else None
existing=api('GET','/api/artifacts');
old_id='1285f7a6-6b65-4c76-9d97-e62b9c6ee849';old=api('GET','/api/artifacts/'+old_id)
if old.get('name')!=NAME or old.get('version')!='guarded-v2' or old.get('sha256')!='767f956847c21311b732611a5743cd859d00d0ff27ce6abf30ab300426108801':raise SystemExit('blocked: existing owned Artifact fingerprint mismatch')
api('DELETE','/api/artifacts/'+old_id,None,None,(200,));
try:api('GET','/api/artifacts/'+old_id,None,None,(404,));old_404=True
except Exception:old_404=False
if not old_404:raise SystemExit('old owned Artifact did not verify GET404')
existing=api('GET','/api/artifacts')
if any(x.get('name')==NAME for x in existing):raise SystemExit('blocked: duplicate Artifact name remains after exact delete')
boundary='qa'+uuid.uuid4().hex;parts=[]
for k,v in [('name',NAME),('version',VERSION),('architecture','amd64')]:parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode())
parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{FILENAME}"\r\nContent-Type: application/gzip\r\n\r\n').encode());parts.append(payload);parts.append((f'\r\n--{boundary}--\r\n').encode());body=b''.join(parts)
created=api('POST','/api/artifacts',body,{'Content-Type':'multipart/form-data; boundary='+boundary},(200,201));aid=created.get('id')
if not aid:raise RuntimeError('Artifact create response missing ID')
read=api('GET','/api/artifacts/'+aid)
e={'case_id':'BE-SCRIPT-SHA-002','artifact_id':aid,'name':read.get('name'),'version':read.get('version'),'filename':read.get('filename'),'architecture':read.get('architecture'),'size':read.get('size'),'sha256':read.get('sha256'),'expected_size':len(payload),'expected_sha256':artifact_sha,'deployment_binary_sha256':EXPECTED_BINARY,'source_sha256':'5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0','endpoint_port':18080,'created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'verify':read.get('sha256')==artifact_sha and int(read.get('size',-1))==len(payload),'previous_owned_artifact_deleted_and_404':old_404}
(H/'proxy-binary-artifact-final-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
if not e['verify']:raise SystemExit('Artifact readback SHA/size mismatch')
print(json.dumps({'status':'ARTIFACT_UPLOAD_VERIFIED','artifact_id':aid,'name':NAME,'version':VERSION,'size':len(payload),'sha256':artifact_sha,'binary_sha256':EXPECTED_BINARY,'endpoint_port':18080}))
