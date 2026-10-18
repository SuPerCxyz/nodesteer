import hashlib,json,shlex,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='37989297-3bc8-4c0c-b302-7c6efbdba630';EXPECTED_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';TASK_NAME='qa-resume-20261010-native3-agent-proc-sha-37989297-7d84-418c-b598-f93d8b0c86a0'
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
src=r'''import hashlib,json,os,stat,subprocess,time
from pathlib import Path
EXPECTED_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';NODE='37989297-3bc8-4c0c-b302-7c6efbdba630';AGENT='agent-bb005487-7d84-418c-b598-f93d8b0c86a0'
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();status=(q/'status').read_text().splitlines();uid=int(next(x for x in status if x.startswith('Uid:')).split()[1]);ppid=int(tail[1]);exe=os.readlink(q/'exe');return {'pid':pid,'ppid':ppid,'startticks':int(tail[19]),'uid':uid,'exe_path':exe,'exe_basename':Path(exe.removesuffix(' (deleted)')).name,'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip(),'argv':[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x]}
selfpid=os.getpid();chain=[];cur=os.getppid();agent=None
for _ in range(32):
 if cur<=1:break
 try:p=proc(cur)
 except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError):break
 chain.append({k:p[k] for k in ['pid','ppid','startticks','uid','exe_basename','exe_sha256','cgroup']})
 if p['exe_sha256']==EXPECTED_SHA and 'nodesteer-agent' in p['exe_basename']:
  agent=p;break
 cur=p['ppid']
if agent is None:
 print(json.dumps({'status':'AGENT_SHA_NOT_FOUND_IN_SELF_ANCESTORS','node_id':NODE,'agent_id':AGENT,'task_pid':selfpid,'ancestor_chain':chain,'expected_sha256':EXPECTED_SHA,'argv_env_emitted':False},separators=(',',':')),flush=True);raise SystemExit(2)
# Expose only recognized config path; never output arbitrary argv or any environment values.
config=None
for i,arg in enumerate(agent['argv']):
 if arg in ('--config','-config','--config-file') and i+1<len(agent['argv']):config=agent['argv'][i+1]
 if arg.startswith('--config='):config=arg.split('=',1)[1]
exe_disk=agent['exe_path'].removesuffix(' (deleted)');disk_sha=sha(exe_disk) if os.path.isfile(exe_disk) else None;disk_stat=None
if disk_sha is not None:
 st=os.stat(exe_disk);disk_stat={'dev':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode))}
parts=[x for x in agent['cgroup'].split('/') if x];unit=next((x for x in reversed(parts) if x.endswith(('.service','.scope'))),None);systemd={'available':False}
if unit:
 try:
  cp=subprocess.run(['systemctl','show',unit,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup'],capture_output=True,text=True,timeout=5,check=False);props={}
  for line in cp.stdout.splitlines():
   if '=' in line:
    k,v=line.split('=',1);props[k]=v
  systemd={'available':cp.returncode==0,'returncode':cp.returncode,'id':props.get('Id'),'active_state':props.get('ActiveState'),'main_pid':int(props['MainPID']) if props.get('MainPID','').isdigit() else None,'control_group':props.get('ControlGroup')}
 except (FileNotFoundError,subprocess.TimeoutExpired):systemd={'available':False,'reason':'systemctl unavailable or timed out'}
out={'status':'NATIVE3_REPAIR_AGENT_ANCESTOR_SHA_CHECK','node_id':NODE,'agent_id':AGENT,'task_pid':selfpid,'ancestor_depth':len(chain),'ancestor_summary':chain,'agent_process':{'pid':agent['pid'],'ppid':agent['ppid'],'startticks':agent['startticks'],'uid':agent['uid'],'exe_path':agent['exe_path'],'proc_exe_sha256':agent['exe_sha256'],'disk_path':exe_disk,'disk_sha256':disk_sha,'disk_stat':disk_stat,'config_path_only':config,'cgroup':agent['cgroup'],'unit_from_cgroup':unit,'systemd_readonly':systemd,'proc_and_disk_sha_match_expected':agent['exe_sha256']==EXPECTED_SHA and disk_sha==EXPECTED_SHA},'expected_manifest_sha256':EXPECTED_SHA,'manifest_source':'.e2e/runs/20260930-101945/repair/build-manifest.json artifacts[nodesteer-agent-static]','full_nonagent_argv_emitted':False,'environment_read_or_emitted':False}
print(json.dumps(out,separators=(',',':')),flush=True)'''
cmd='python3 -c '+shlex.quote(src);node=api('GET','/api/nodes/'+NODE)[1]
if node.get('id')!=NODE or node.get('status')!='online' or node.get('sync_status')!='synced':raise SystemExit('exact Native3 identity/health guard failed')
task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'command','command':cmd,'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('Native3 Task create response lacked ID')
e={'task_id':tid,'task_name':TASK_NAME,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'node_id':NODE,'target_node_ids':[NODE],'retry':0,'timeout':60,'schedule_present':False}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[NODE] or rd.get('retry')!=0 or rd.get('timeout')!=60 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('Native3 Task exact readback mismatch')
 schedules=api('GET','/api/schedules')[1]
 if any(x.get('task_id')==tid for x in schedules):raise RuntimeError('unexpected scheduled Task reference')
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('Native3 Task Run response shape/cardinality mismatch')
 eid=rows[0]['id'];e['execution_id']=eid;e['run_http_status']=run_status;deadline=time.monotonic()+60;final=None
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout')
 result=json.loads(stdout) if stdout else None
 e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'result':result,'credentials_or_token_emitted':False})
finally:
 try:e['task_delete_http_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_http_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
(H/'native3-process-sha-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'NATIVE3_READONLY_TASK_COMPLETE' if e.get('task_get_404') else 'NATIVE3_READONLY_TASK_CLEANUP_INCOMPLETE','updated_at_utc':utc(),'native3_task_evidence':'native3-process-sha-evidence.json','native3_task_id':tid,'native3_execution_id':e.get('execution_id'),'native3_execution_status':e.get('execution_status'),'native3_task_get404':e.get('task_get_404'),'next_action':'Review actual ancestor/disk SHA evidence, keep execution audit history, report no process/service changes.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'task_id':tid,'execution_id':e.get('execution_id'),'execution_status':e.get('execution_status'),'task_get404':e.get('task_get_404'),'agent_found':bool((e.get('result') or {}).get('agent_process')),'proc_sha_matches_disk_sha':(e.get('result') or {}).get('agent_process',{}).get('proc_and_disk_sha_match_expected')},ensure_ascii=False))
