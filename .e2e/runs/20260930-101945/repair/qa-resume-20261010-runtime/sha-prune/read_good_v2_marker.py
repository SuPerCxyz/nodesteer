import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));node='c77b8a84-8085-4ea8-8532-de171761854b';cmd="p='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/sha-prune/markers/good-v2.marker'; test -f \"$p\" && od -An -tx1 \"$p\"";name='qa-resume-20261010-read-good-v2-marker-870358cd92ee'
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[node]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_node':node,'read_only_marker_path':'/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee/sha-prune/markers/good-v2.marker'}
try:
 t=api('GET','/api/tasks/'+tid);assert t.get('command')==cmd;x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+65;ex=None
 while time.monotonic()<end:
  ex=api('GET','/api/executions/'+eid)
  if ex.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:break
  time.sleep(.5)
 out=ex.get('stdout') or '';ev.update({'execution_id':eid,'status':ex.get('status'),'node_id':ex.get('node_id'),'stdout':out,'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stderr_bytes':len((ex.get('stderr') or '').encode())})
finally:
 ev['delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['get_after_status']=404
 except Exception:ev['get_after_status']='not-404'
(H/'good-v2-marker-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'stdout':ev.get('stdout'),'task_deleted_404':ev.get('get_after_status')==404},ensure_ascii=False))
