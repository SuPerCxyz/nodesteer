import hashlib,json,re,shlex,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';TASK_NAME='qa-resume-20261010-native4-supervisor-source-restart-readonly-870358cd92ee'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:status,raw=r.status,r.read()
 except HTTPError as e:status,raw=e.code,e.read()
 if status not in allowed:raise RuntimeError(f'{m} {p} HTTP {status}')
 return status,(json.loads(raw) if raw else None)
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
src=r'''import hashlib,json,re
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');sp=D/'supervisor.py'
if D.is_symlink() or not D.is_dir() or sp.is_symlink() or not sp.is_file():raise SystemExit('owned supervisor source path guard failed')
raw=sp.read_bytes();text=raw.decode('utf-8');lines=text.splitlines();terms={'while ':'loop','Popen':'spawn','wait':'wait','poll':'poll','sleep':'sleep/backoff','restart':'restart','terminate':'terminate','kill':'kill','signal':'signal','child':'child'};out=[]
for n,line in enumerate(lines,1):
 tags=sorted({label for term,label in terms.items() if term.lower() in line.lower()})
 safe=re.sub(r'(?i)(token|password|secret|authorization|bearer)([^,;)]*)',r'\1=<redacted>',line)
 out.append({'line':n,'tags':tags,'source':safe[:300]})
logs=[]
for p in [D/'supervisor.log',D/'agent.log']:
 if not p.is_file() or p.is_symlink():continue
 rows=[]
 for n,line in enumerate(p.read_text(errors='replace').splitlines(),1):
  low=line.lower();tags=[x for x in ['restart','spawn','child','exit','signal','started','starting','hello','heartbeat'] if x in low]
  if tags:rows.append({'line':n,'tags':tags,'line_sha256':hashlib.sha256(line.encode()).hexdigest()})
 logs.append({'name':p.name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'keyword_lines':rows[-30:]})
print(json.dumps({'status':'OWNED_SUPERVISOR_SOURCE_AND_SAFE_LOG_TAGS','source_path':str(sp),'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),'source_line_count':len(lines),'source_lines_redacted':out,'logs':logs,'credentials_or_env_emitted':False},separators=(',',':')),flush=True)'''
cmd='python3 -c '+shlex.quote(src);parent=api('GET','/api/nodes/'+PARENT)[1]
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise SystemExit('exact Native4 parent online+synced guard failed')
task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('source probe Task create response missing ID')
e={'task_id':tid,'task_name':TASK_NAME,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'timeout':60,'retry':0,'schedule':False,'scope':'read-only existing owned supervisor source and sanitized log keyword metadata'}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[PARENT] or rd.get('timeout')!=60 or rd.get('retry')!=0 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('source probe exact Task readback failed')
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('source probe Run shape mismatch')
 eid=rows[0]['id'];e['execution_id']=eid;e['run_http_status']=run_status;deadline=time.monotonic()+60;final=None
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout');result=json.loads(stdout) if stdout else None
 e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'result':result,'credentials_or_env_emitted':False})
finally:
 try:e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
(H/'supervisor-source-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'SUPERVISOR_SOURCE_READONLY_CHECK_COMPLETE' if e.get('task_get_404') else 'SUPERVISOR_SOURCE_CHECK_CLEANUP_INCOMPLETE','updated_at_utc':utc(),'supervisor_source_evidence':'supervisor-source-evidence.json','source_probe_task_id':tid,'source_probe_execution_id':e.get('execution_id'),'source_probe_task_get404':e.get('task_get_404'),'next_action':'Review source and log evidence, then write precise restart proposal for Root approval; no signal until approval.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'task_id':tid,'execution_id':e.get('execution_id'),'execution_status':e.get('execution_status'),'task_get404':e.get('task_get_404'),'source_sha256':(e.get('result') or {}).get('source_sha256'),'source_line_count':(e.get('result') or {}).get('source_line_count')},ensure_ascii=False))
