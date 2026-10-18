import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent
# Reload the private management connection immediately before this API operation; never emit its values.
priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token']
if urlsplit(base).port!=18080:raise SystemExit('blocked: management endpoint is not repair :18080')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=25) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
src=(H/'download_proxy_candidate_secret_task.py').read_text();command='python3 -c '+shlex.quote(src);name='qa-resume-20261010-download-proxy-candidate-v3-870358cd92ee';param={'name':'qa_auth','type':'secret','required':True}
task=api('POST','/api/tasks',{'name':name,'type':'command','command':command,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[param],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=task['id'];ev={'case_id':'BE-SCRIPT-SHA-002','task_id':tid,'task_name':name,'task_type':'command','task_source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'parameter_definition':{'name':'qa_auth','type':'secret','required':True},'retry':0,'timeout':60,'target_node_id':'91f44866-8a28-435e-bbda-2f2367126c00','secret_parameter_value_written_to_evidence':False}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==ev['command_sha256'];params=rd.get('parameters') or []
 if len(params)!=1 or params[0].get('name')!='qa_auth' or params[0].get('type')!='secret':raise RuntimeError('Task secret parameter definition readback mismatch')
 xs=api('POST','/api/tasks/'+tid+'/run',{'params':{'qa_auth':token}},(200,201));eid=xs[0]['id'];end=time.monotonic()+70;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 ev['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['task_get_after_status']=404
 except Exception:ev['task_get_after_status']='not-404'
(H/'proxy-candidate-download-final-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json'),'secret_value_printed':False},ensure_ascii=False))
