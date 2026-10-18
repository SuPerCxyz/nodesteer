import hashlib,json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));TASK='d43ded1e-f073-461e-b06c-24005789d4c3';NODE='c77b8a84-8085-4ea8-8532-de171761854b';SCRIPT='fc6fbcc6-39ce-49f4-9368-265a5a42d174'
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
t=api('GET','/api/tasks/'+TASK);ev={'task_id':TASK,'task_name':t.get('name'),'type':t.get('type'),'script_id':t.get('script_id'),'target':t.get('target'),'retry':t.get('retry'),'timeout':t.get('timeout'),'enabled':t.get('enabled')}
if t.get('type')!='script' or t.get('script_id')!=SCRIPT or t.get('target',{}).get('node_ids')!=[NODE] or t.get('retry')!=0 or t.get('timeout')!=60:raise SystemExit('retained Good Script Task exact-target guard failed')
sc=api('GET','/api/scripts/'+SCRIPT);ev['script_readback']={k:sc.get(k) for k in ['id','revision','sha256','enabled']}
if sc.get('revision')!=3 or sc.get('sha256')!='5bd147794c5df9113947cd8d98ffce8e6569188e6f0910862754e193c338a115':raise SystemExit('Good Script current revision/SHA mismatch')
x=api('POST','/api/tasks/'+TASK+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+65;fin=None
while time.monotonic()<end:
 fin=api('GET','/api/executions/'+eid)
 if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
 time.sleep(.5)
out=fin.get('stdout') or '';err=fin.get('stderr') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'started_at':fin.get('started_at'),'finished_at':fin.get('finished_at'),'stdout':out,'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stderr_bytes':len(err.encode()),'stderr_sha256':hashlib.sha256(err.encode()).hexdigest(),'error':fin.get('error')})
(H/'good-script-execution-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'execution_id':eid,'status':ev['status'],'task_id':TASK,'script_id':SCRIPT,'target_node':ev.get('node_id'),'stdout_sha256':ev['stdout_sha256'],'stdout_bytes':len(out.encode()),'stderr_bytes':ev['stderr_bytes']},ensure_ascii=False))
