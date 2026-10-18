import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent; c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allow:raise RuntimeError(f'{m} {p}: HTTP {s}')
 return json.loads(x) if x else None
src=(H/'host_proxy_setup_probe.py').read_text(); cmd='python3 -c '+shlex.quote(src); name='qa-resume-20261010-sha-prune-proxy-preflight-870358cd92ee'
t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id']; evidence={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest()}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd['command'].encode()).hexdigest()==evidence['command_sha256'];xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=xs[0]['id'];end=time.monotonic()+70;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 stdout=fin.get('stdout') or ''; evidence.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_json':json.loads(stdout) if stdout else None})
finally:
 ds=api('DELETE','/api/tasks/'+tid,None,(200,204));
 try:api('GET','/api/tasks/'+tid,None,(404,));gs=404
 except Exception:gs='not-404'
 evidence['cleanup']={'delete':ds,'get_after':gs}
(H/'host-proxy-setup-probe-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':evidence.get('status'),'task_deleted_404':evidence['cleanup']['get_after']==404,'owner_matches':(evidence.get('stdout_json') or {}).get('owner_matches'),'proxy_root_exists':(evidence.get('stdout_json') or {}).get('proxy_root',{}).get('exists'),'go_available':((evidence.get('stdout_json') or {}).get('tools',{}).get('go',{}).get('out')!='')},ensure_ascii=False))
