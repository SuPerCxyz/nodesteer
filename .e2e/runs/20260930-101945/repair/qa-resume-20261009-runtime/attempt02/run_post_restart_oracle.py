import hashlib,json,shlex,time,uuid
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;P=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');o=json.loads((P/'repair-connection.json').read_text());base=o['hub_url'].rstrip('/');token=o['token'];NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';OUT=H/'post-restart-physical-oracle-evidence.json';op=build_opener(ProxyHandler({}))
if urlsplit(base).port!=18080:raise SystemExit('blocked: only repair :18080')
def api(m,p,b=None,allow=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,v=r.status,r.read()
 except HTTPError as e:s,v=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError(type(e).__name__)
 if s not in allow:raise RuntimeError(m+' HTTP '+str(s))
 return s,json.loads(v) if v else None
source=(H.parent/'app-v1-oracle-source.py').read_text();cmd='python3 -c '+shlex.quote(source);cs=hashlib.sha256(cmd.encode()).hexdigest();task={'name':'qa-resume-20261009-p0-002-v1-restore-oracle-870358cd92ee-'+uuid.uuid4().hex[:6],'type':'command','target':{'type':'node','node_ids':[NODE]},'parameters':[{'name':'app_id','type':'string','required':True}],'command':cmd,'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True};e={'case_id':'E2E-20260930-101945-P0-002','stage':'POST_RESTART_RECOVERY_PHYSICAL_AND_JOURNAL_ORACLE','source_sha256':hashlib.sha256(source.encode()).hexdigest(),'command_sha256':cs,'endpoint_guard':{'host':urlsplit(base).hostname,'port':urlsplit(base).port,'only_repair_18080':True},'target_node_id':NODE};tid=None
try:
 _,t=api('POST','/api/tasks',task,(200,201));tid=t['id'];e['task_id']=tid
 _,f=api('GET','/api/tasks/'+tid)
 if hashlib.sha256(f.get('command','').encode()).hexdigest()!=cs:raise RuntimeError('command SHA readback mismatch')
 _,xs=api('POST','/api/tasks/'+tid+'/run',{'params':{'app_id':APP}},(200,201))
 if len(xs)!=1:raise RuntimeError('expected one oracle execution')
 xid=xs[0]['id'];e['execution_id']=xid;end=time.monotonic()+70;z=None
 while time.monotonic()<end:
  _,z=api('GET','/api/executions/'+xid)
  if z.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'}:break
  time.sleep(.3)
 stdout=(z or {}).get('stdout') or '';e['execution']={k:z.get(k) for k in ['id','task_id','node_id','status','exit_code','trigger_type'] if z and k in z};e['execution']['stdout_json']=json.loads(stdout) if stdout else None;e['execution']['stdout_sha256']=hashlib.sha256(stdout.encode()).hexdigest();e['execution']['stderr_bytes']=len(((z or {}).get('stderr') or '').encode());e['status']='ORACLE_'+str((z or {}).get('status'))
finally:
 if tid:
  d,_=api('DELETE','/api/tasks/'+tid,(None), (200,204));g,_=api('GET','/api/tasks/'+tid,None,(404,));e['task_cleanup']={'delete_http_status':d,'verify_get_http_status':g,'verified_404':g==404}
 e['completed_unix']=int(time.time());OUT.write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'evidence':str(OUT.relative_to(Path.cwd())),'status':e.get('status'),'execution_id':e.get('execution_id'),'cleanup_404':e.get('task_cleanup',{}).get('verified_404')},ensure_ascii=False))
