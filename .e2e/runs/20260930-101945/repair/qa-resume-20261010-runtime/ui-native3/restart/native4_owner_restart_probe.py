import hashlib,json,shlex,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';TASK_NAME='qa-resume-20261010-native4-restart-plan-readonly-retry1-15961ef7-870358cd92ee'
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
src=r'''import hashlib,json,os,re,sqlite3,stat,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';ROOT=(33,135,65534,0o700);DBMETA=(33,155,65534);BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';CHILD_PID=47015;CHILD_TICK=130084225;SUP_PID=44932;SUP_TICK=123677922;UNIT='nodesteer-agent-repair-20260930-101945-native-4.service'
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];exe=os.readlink(q/'exe');return {'pid':pid,'ppid':int(tail[1]),'startticks':int(tail[19]),'uid':uid,'exe_path':exe,'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
st=D.lstat()
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=ROOT:raise SystemExit('owned root exact inode/owner guard failed')
M=D/'owner-manifest.json'
if M.is_symlink() or M.stat().st_uid!=65534:raise SystemExit('owner manifest guard failed')
manifest=json.loads(M.read_text())
if manifest.get('node_id')!=NODE or manifest.get('agent_id')!=AGENT or manifest.get('directory')!=str(D):raise SystemExit('owner identity mismatch')
if sha(M)!='fc7ad2e2dbba5c00c95ed08419cfc62b6601da1d13a0dc6ded9d8d5b4a7131f3':raise SystemExit('owner manifest SHA mismatch')
child=proc(CHILD_PID);sup=proc(SUP_PID)
if child['startticks']!=CHILD_TICK or child['uid']!=65534 or child['exe_sha256']!=BIN_SHA or child['argv']!=[str(D/'nodesteer-agent'),'--config',str(D/'child.yaml')]:raise SystemExit('owned child PID fingerprint mismatch')
if child['cgroup']!='0::/system.slice/'+UNIT or sup['startticks']!=SUP_TICK or sup['uid']!=65534 or sup['exe_sha256']!=SUP_SHA or not any(str(D/'supervisor.py')==x for x in sup['argv']) or sup['cgroup']!=child['cgroup']:raise SystemExit('owned supervisor/cgroup fingerprint mismatch')
# Report only the --config path, never complete argv or environment.
config_path=child['argv'][child['argv'].index('--config')+1];dbp=D/'data/state.db';dbst=dbp.lstat()
if dbp.is_symlink() or (dbst.st_dev,dbst.st_ino,dbst.st_uid)!=DBMETA:raise SystemExit('owned DB exact inode/UID mismatch')
con=sqlite3.connect('file:'+str(dbp)+'?mode=ro',uri=True,timeout=3);con.row_factory=sqlite3.Row;quick=con.execute('PRAGMA quick_check').fetchone()[0];tables=[x[0] for x in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")];rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];ident={x['key']:x['value'] for x in con.execute("select key,value from identity where key in ('node_id','agent_id')")};counts={}
for table in ['scripts','tasks','schedules','applications','deployments','unit_registry']:
 counts[table]=con.execute('select count(*) from '+table).fetchone()[0]
con.close()
# Existing Supervisor policy: hash and keyword/line index only, with no source line bodies or secrets.
sp=D/'supervisor.py';source=sp.read_text().splitlines();tags=[]
for i,line in enumerate(source,1):
 low=line.lower();matched=[]
 for term,label in [('while true','outer_loop'),('while ','loop'),('popen','spawn_child'),('.wait(','wait_child'),('.poll(','poll_child'),('time.sleep','backoff_or_poll'),('signal.signal','signal_handler'),('terminate(','terminate_child'),('restart','restart_policy')]:
  if term in low:matched.append(label)
 if matched:tags.append({'line':i,'tags':sorted(set(matched))})
ordered={'outer_loop':any('outer_loop' in x['tags'] for x in tags),'spawn_child':any('spawn_child' in x['tags'] for x in tags),'wait_child':any('wait_child' in x['tags'] for x in tags),'sleep_or_backoff':any('backoff_or_poll' in x['tags'] for x in tags)}
# Read-only systemd properties for the existing shared parent cgroup unit.
systemd={}
try:
 cp=subprocess.run(['systemctl','show',UNIT,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup,Restart,RestartUSec'],capture_output=True,text=True,timeout=5,check=False);props={}
 for line in cp.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);props[k]=v
 systemd={'available':cp.returncode==0,'returncode':cp.returncode,'id':props.get('Id'),'active_state':props.get('ActiveState'),'main_pid':int(props['MainPID']) if props.get('MainPID','').isdigit() else None,'control_group':props.get('ControlGroup'),'restart':props.get('Restart'),'restart_usec':props.get('RestartUSec')}
except (FileNotFoundError,subprocess.TimeoutExpired):systemd={'available':False}
# Read proxy event metadata only; never include raw frames or payloads.
eventfile=D/'proxy-events.jsonl';evcounts={};last_events=[]
if eventfile.exists():
 with eventfile.open('r',encoding='utf-8',errors='replace') as f:
  for line in f:
   try:z=json.loads(line)
   except Exception:continue
   ev=str(z.get('event','unknown'));evcounts[ev]=evcounts.get(ev,0)+1;last_events.append({'event':ev,'at_utc':z.get('at_utc') or z.get('timestamp')})
last_events=last_events[-40:]
# Only report names/sizes and SHA digests of candidate logs; do not expose log contents.
logs=[]
for p in list(D.glob('*.log'))+list((D/'log').glob('*.log') if (D/'log').exists() else [])+list((D/'logs').glob('*.log') if (D/'logs').exists() else []):
 try:logs.append({'name':p.relative_to(D).as_posix(),'bytes':p.stat().st_size,'sha256':sha(p)})
 except (FileNotFoundError,PermissionError):pass
out={'captured_at_utc':utc(),'status':'NATIVE4_RESTART_READONLY_OWNER_AND_DB_GUARDS','node_id':NODE,'agent_id':AGENT,'owner_root':{'path':str(D),'dev':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':oct(st.st_mode&0o7777),'manifest_sha256':sha(M)},'child_process':{'pid':child['pid'],'startticks':child['startticks'],'uid':child['uid'],'exe_path':child['exe_path'],'exe_sha256':child['exe_sha256'],'config_path_only':config_path,'cgroup':child['cgroup']},'supervisor_process':{'pid':sup['pid'],'startticks':sup['startticks'],'uid':sup['uid'],'exe_path':sup['exe_path'],'exe_sha256':sup['exe_sha256'],'cgroup':sup['cgroup']},'db':{'dev':dbst.st_dev,'inode':dbst.st_ino,'uid':dbst.st_uid,'mode':oct(dbst.st_mode&0o7777),'quick_check':quick,'local_revision':rev,'identity':ident,'tables':tables,'fixture_table_counts':counts},'unit':systemd,'supervisor_source':{'path':str(sp),'sha256':sha(sp),'size':sp.stat().st_size,'restart_related_line_tags':tags,'loop_spawn_wait_features':ordered,'source_bodies_emitted':False},'proxy_events':{'path':str(eventfile),'exists':eventfile.exists(),'event_counts':evcounts,'last_40_event_metadata':last_events,'raw_frames_emitted':False,'heartbeat_event_count':sum(v for k,v in evcounts.items() if 'heartbeat' in k.lower() or k.lower() in ('ping','pong'))},'candidate_agent_logs_metadata':logs,'process_signal_sent':False,'service_or_cgroup_changed':False,'credentials_or_env_emitted':False,'full_argv_emitted':False}
print(json.dumps(out,separators=(',',':')),flush=True)'''
cmd='python3 -c '+shlex.quote(src);parent=api('GET','/api/nodes/'+PARENT)[1]
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise SystemExit('exact Native4 parent online+synced guard failed')
task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('Task create response missing ID')
e={'task_id':tid,'task_name':TASK_NAME,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'retry':0,'timeout':60,'schedule':False,'scope':'read-only exact owned child/supervisor/process/DB/unit/proxy metadata; no mutation or signal'}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[PARENT] or rd.get('retry')!=0 or rd.get('timeout')!=60 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('exact read-only Task readback mismatch')
 schedules=api('GET','/api/schedules')[1]
 if any(x.get('task_id')==tid for x in schedules):raise RuntimeError('unexpected Task schedule')
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('Run response shape/cardinality mismatch')
 eid=rows[0]['id'];e['execution_id']=eid;e['run_http_status']=run_status;deadline=time.monotonic()+60;final=None
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout');result=json.loads(stdout) if stdout else None
 e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'result':result,'credentials_or_sensitive_payload_emitted':False})
finally:
 try:e['task_delete_http_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_http_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
(H/'owner-probe-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'READONLY_OWNER_PROBE_COMPLETE' if e.get('task_get_404') else 'READONLY_PROBE_CLEANUP_INCOMPLETE','updated_at_utc':utc(),'owner_probe_evidence':'owner-probe-evidence.json','parent_helper_task_id':tid,'parent_helper_execution_id':e.get('execution_id'),'parent_helper_task_get404':e.get('task_get_404'),'next_action':'Draft exact restart plan from fresh owner/API/source/event evidence; do not signal or restart until Root approves.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'task_id':tid,'execution_id':e.get('execution_id'),'execution_status':e.get('execution_status'),'task_get404':e.get('task_get_404'),'child_pid':(e.get('result') or {}).get('child_process',{}).get('pid'),'db_integrity':(e.get('result') or {}).get('db',{}).get('quick_check'),'fixture_counts':(e.get('result') or {}).get('db',{}).get('fixture_table_counts'),'supervisor_restart_loop_features':(e.get('result') or {}).get('supervisor_source',{}).get('loop_spawn_wait_features'),'heartbeat_event_count':(e.get('result') or {}).get('proxy_events',{}).get('heartbeat_event_count')},ensure_ascii=False))
