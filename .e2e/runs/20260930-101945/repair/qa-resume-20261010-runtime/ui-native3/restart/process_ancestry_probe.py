import hashlib,json,shlex,stat,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';TASK_NAME='qa-resume-20261010-native4-child-ppid-unit-mainpid-readonly-870358cd92ee'
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
src=r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');UNIT='nodesteer-agent-repair-20260930-101945-native-4.service';CHILD_PID=47015;CHILD_TICK=130084225;SUP_PID=44932;SUP_TICK=123677922;EXPECTED_CHILD_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);exe=os.readlink(q/'exe');argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];cfg=None
 for i,x in enumerate(argv):
  if x in ('--config','-config','--config-file') and i+1<len(argv):cfg=argv[i+1]
  if x.startswith('--config='):cfg=x.split('=',1)[1]
 return {'pid':pid,'ppid':int(tail[1]),'startticks':int(tail[19]),'uid':uid,'exe_path':exe,'exe_sha256':sha(q/'exe'),'config_path_only':cfg,'cgroup':(q/'cgroup').read_text().strip()}
root=D.lstat();db=D/'data/state.db';ds=db.lstat()
if D.is_symlink() or D.resolve()!=D or (root.st_dev,root.st_ino,root.st_uid,stat.S_IMODE(root.st_mode))!=(33,135,65534,0o700) or db.is_symlink() or (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,155,65534):raise SystemExit('owned root/DB exact guard failed')
child=proc(CHILD_PID);sup=proc(SUP_PID)
if child['startticks']!=CHILD_TICK or child['uid']!=65534 or child['exe_sha256']!=EXPECTED_CHILD_SHA or sup['startticks']!=SUP_TICK or sup['uid']!=65534:raise SystemExit('exact process fingerprints changed')
try:
 cp=subprocess.run(['systemctl','show',UNIT,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup,Restart,RestartUSec,KillMode'],capture_output=True,text=True,timeout=5,check=False);props={}
 for line in cp.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);props[k]=v
 main=int(props['MainPID']) if props.get('MainPID','').isdigit() and int(props['MainPID'])>0 else None;unit={'available':cp.returncode==0,'id':props.get('Id'),'active_state':props.get('ActiveState'),'main_pid':main,'control_group':props.get('ControlGroup'),'restart':props.get('Restart'),'restart_usec':props.get('RestartUSec'),'kill_mode':props.get('KillMode')};main_proc=proc(main) if main else None
except (FileNotFoundError,subprocess.TimeoutExpired):unit={'available':False};main_proc=None
out={'captured_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'child_process':child,'proxy_supervisor_process':sup,'unit':unit,'unit_main_process':({k:main_proc.get(k) for k in ['pid','ppid','startticks','uid','exe_path','exe_sha256','config_path_only','cgroup']} if main_proc else None),'child_is_unit_main_pid':bool(main_proc and child['pid']==main_proc['pid']),'child_ppid_is_proxy_supervisor':child['ppid']==sup['pid'],'proxy_supervisor_source_has_no_process_spawn_loop':True,'no_signal_or_service_operation':True,'argv_env_secrets_emitted':False}
print(json.dumps(out,separators=(',',':')),flush=True)'''
cmd='python3 -c '+shlex.quote(src);parent=api('GET','/api/nodes/'+PARENT)[1]
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise SystemExit('exact parent online/synced guard failed')
task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('Task create response missing ID')
e={'task_id':tid,'task_name':TASK_NAME,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'timeout':60,'retry':0,'schedule':False}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[PARENT] or rd.get('timeout')!=60 or rd.get('retry')!=0 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('exact readonly task readback mismatch')
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('Run response shape/cardinality mismatch')
 eid=rows[0]['id'];e['execution_id']=eid;e['run_http_status']=run_status;deadline=time.monotonic()+60;final=None
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout');result=json.loads(stdout) if stdout else None;e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'result':result})
finally:
 try:e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
(H/'process-ancestry-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'PROCESS_ANCESTRY_READONLY_COMPLETE' if e.get('task_get_404') else 'PROCESS_ANCESTRY_CLEANUP_INCOMPLETE','updated_at_utc':utc(),'process_ancestry_evidence':'process-ancestry-evidence.json','process_probe_task_id':tid,'process_probe_execution_id':e.get('execution_id'),'process_probe_task_get404':e.get('task_get_404'),'next_action':'Write accurate no-signal restart proposal based on actual child PPID, supervisor, Unit MainPID and recovery ownership evidence; await Root approval.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'task_id':tid,'execution_id':e.get('execution_id'),'status':e.get('execution_status'),'task_get404':e.get('task_get_404'),'child_ppid':(e.get('result') or {}).get('child_process',{}).get('ppid'),'supervisor_pid':(e.get('result') or {}).get('proxy_supervisor_process',{}).get('pid'),'unit_main_pid':(e.get('result') or {}).get('unit',{}).get('main_pid'),'child_is_unit_main_pid':(e.get('result') or {}).get('child_is_unit_main_pid')},ensure_ascii=False))
