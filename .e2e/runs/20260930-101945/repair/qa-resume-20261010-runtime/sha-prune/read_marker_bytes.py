import json,hashlib,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');tok=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+tok};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError('HTTP '+str(s))
 return json.loads(x) if x else None
cmd="p='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/sha-prune/markers/good-v1.marker'; test -f \"$p\" && od -An -tx1 \"$p\""
name='qa-resume-20261010-sha-prune-inspect-marker-870358cd92ee'
name=name+'-03'
t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':['c77b8a84-8085-4ea8-8532-de171761854b']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201)); tid=t['id']
try:
 xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201)); eid=xs[0]['id']; end=time.monotonic()+60
 while time.monotonic()<end:
  ex=api('GET','/api/executions/'+eid)
  if ex.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:break
  time.sleep(.5)
 stdout=ex.get('stdout') or ''; (Path(__file__).parent/'marker-read-evidence.json').write_text(json.dumps({'task_id':tid,'execution_id':eid,'status':ex.get('status'),'stdout_bytes':len(stdout.encode()),'stdout':stdout,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest()},indent=2)+'\n'); print(json.dumps({'status':ex.get('status'),'stdout':stdout,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest()}))
finally:
 api('DELETE','/api/tasks/'+tid,None,(200,204));api('GET','/api/tasks/'+tid,None,(404,))
