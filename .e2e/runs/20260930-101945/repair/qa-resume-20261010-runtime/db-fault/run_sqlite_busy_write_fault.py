import hashlib,json,shlex,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';ROOT='/tmp/qa-resume-20261008-child-ciivjupc';SCRIPT='6ecb3dee-43c3-432a-95cc-8fa3b30a91e1';TASK='a30658b0-7090-4a1b-9701-3ab0b89dca3c';V1SHA='74a0113e91fa3c7091a309c4af857fd39f581b2c054e80058f0ee7fd022d313d';V2="#!/bin/sh\nset -eu\nprintf '%s\\n' 'DBLOCK_V2_870358CD92EE'\nprintf '%s\\n' 'DBLOCK_V2_870358CD92EE' > '/tmp/qa-resume-20261008-child-ciivjupc/db-fault-marker.txt'\n";V2SHA=hashlib.sha256(V2.encode()).hexdigest()
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return s,(json.loads(x) if x else None)
def get(p):return api('GET',p)[1]
def chunks(eid):
 x=get('/api/executions/'+eid+'/logs');return sorted(x,key=lambda z:(z.get('stream',''),z.get('seq',0))) if isinstance(x,list) else []
def joined_stdout(rows):return ''.join(x.get('chunk','') for x in rows if x.get('stream')=='stdout')
def save_progress(status,**kw):
 p=H/'progress.json';d=json.loads(p.read_text());d.update(status=status,updated_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**kw);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
# Exact current Hub identity and owned Script/Task guards before creating the fault Task.
node=get('/api/nodes/'+CHILD);parent=get('/api/nodes/'+PARENT);script=get('/api/scripts/'+SCRIPT);task=get('/api/tasks/'+TASK)
if node.get('id')!=CHILD or node.get('agent_id')!=AGENT or node.get('status')!='online' or node.get('sync_status') not in ('synced','outdated'):raise SystemExit('owned child Hub identity/health guard failed')
if parent.get('id')!=PARENT or parent.get('status')!='online':raise SystemExit('Native4 parent unavailable')
if script.get('id')!=SCRIPT or script.get('revision')!=1 or script.get('sha256')!=V1SHA:raise SystemExit('exact Script v1 Hub baseline mismatch')
if task.get('id')!=TASK or task.get('type')!='script' or task.get('script_id')!=SCRIPT or task.get('target',{}).get('node_ids')!=[CHILD] or task.get('retry')!=0 or task.get('timeout')!=60:raise SystemExit('exact child Script Task baseline mismatch')
# Parent-side lock Task verifies the latest owner root, manifest, processes and DB inode before lock acquisition.
src=r'''import hashlib,json,os,pathlib,sqlite3,stat,time
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';SCRIPT='6ecb3dee-43c3-432a-95cc-8fa3b30a91e1';TASK='a30658b0-7090-4a1b-9701-3ab0b89dca3c';V1SHA='74a0113e91fa3c7091a309c4af857fd39f581b2c054e80058f0ee7fd022d313d';BIN=D/'nodesteer-agent';BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP='/usr/bin/python3.13';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';EXPECTED_CHILD_PID=47015;EXPECTED_CHILD_TICK=130084225;EXPECTED_SUP_PID=44932;EXPECTED_SUP_TICK=123677922
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();st=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'startticks':int(st[19]),'uid':uid,'exe':os.readlink(q/'exe'),'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
st=D.lstat();M=D/'owner-manifest.json';m=json.loads(M.read_text())
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(33,135,65534,0o700):raise SystemExit('owner root inode/owner guard failed')
if m.get('node_id')!=NODE or m.get('agent_id')!=AGENT or m.get('directory')!=str(D):raise SystemExit('owner manifest exact identity failed')
if sha(M)!='fc7ad2e2dbba5c00c95ed08419cfc62b6601da1d13a0dc6ded9d8d5b4a7131f3':raise SystemExit('owner manifest SHA changed')
child=proc(EXPECTED_CHILD_PID);sup=proc(EXPECTED_SUP_PID)
if child['startticks']!=EXPECTED_CHILD_TICK or child['uid']!=65534 or child['exe']!=str(BIN) or child['exe_sha256']!=BIN_SHA or child['argv']!=[str(BIN),'--config',str(D/'child.yaml')] or child['cgroup']!='0::/system.slice/nodesteer-agent-repair-20260930-101945-native-4.service':raise SystemExit('exact child process fingerprint mismatch')
if sup['startticks']!=EXPECTED_SUP_TICK or sup['uid']!=65534 or sup['exe']!=SUP or sup['exe_sha256']!=SUP_SHA or not any(x==str(D/'supervisor.py') for x in sup['argv']) or sup['cgroup']!=child['cgroup']:raise SystemExit('exact supervisor fingerprint mismatch')
dbpath=D/'data/state.db';ds=dbpath.lstat()
if dbpath.is_symlink() or (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,155,65534):raise SystemExit('owned DB inode/owner mismatch')
# The lock connection itself captures the real baseline at lock acquisition; do not compare to an earlier Hub revision sample.
con=sqlite3.connect('file:'+str(dbpath)+'?mode=rw',uri=True,timeout=0.25,isolation_level=None);locked=False
try:
 con.execute('BEGIN IMMEDIATE');locked=True
 rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];s=con.execute('select revision,sha256 from scripts where id=?',(SCRIPT,)).fetchone();t=con.execute('select revision,definition_json,enabled from tasks where id=?',(TASK,)).fetchone();schedule_count=con.execute('select count(*) from schedules').fetchone()[0]
 if not s or s[0]!=1 or s[1]!=V1SHA or not t or schedule_count!=0:raise RuntimeError('local v1 Script/Task baseline mismatch')
 log_offsets=[]
 for p in list(D.glob('*.log'))+list(D.glob('log/*.log'))+list(D.glob('logs/*.log')):
  try:log_offsets.append({'path':str(p),'size':p.stat().st_size})
  except FileNotFoundError:pass
 eventfile=D/'proxy-events.jsonl';event_offset=eventfile.stat().st_size if eventfile.exists() else 0
 print(json.dumps({'event':'LOCK_ACQUIRED','node_id':NODE,'agent_id':AGENT,'child_pid':child['pid'],'child_startticks':child['startticks'],'supervisor_pid':sup['pid'],'supervisor_startticks':sup['startticks'],'db_dev':ds.st_dev,'db_inode':ds.st_ino,'lock_time_local_revision':rev,'old_script_id':SCRIPT,'old_script_revision':s[0],'old_script_sha256':s[1],'task_id':TASK,'task_revision':t[0],'schedule_count':schedule_count,'lock_hold_seconds':12},separators=(',',':')),flush=True)
 time.sleep(12)
 after_rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];after=con.execute('select revision,sha256 from scripts where id=?',(SCRIPT,)).fetchone()
 log_match=[]
 for x in log_offsets:
  try:
   with open(x['path'],'rb') as f:f.seek(x['size']);raw=f.read()
   for line in raw.decode(errors='replace').splitlines():
    low=line.lower()
    if 'apply sync failed' in low and ('database is locked' in low or 'database locked' in low):log_match.append(hashlib.sha256(line.encode()).hexdigest())
  except (FileNotFoundError,PermissionError):pass
 ack=[]
 if eventfile.exists():
  try:
   with open(eventfile,'rb') as f:f.seek(event_offset);lines=f.read().decode(errors='replace').splitlines()
   for line in lines:
    try:z=json.loads(line)
    except Exception:continue
    name=str(z.get('event','')).lower()
    if 'ack' in name:ack.append({'event':name,'ok':z.get('ok'),'applied_rev':z.get('applied_rev')})
  except (FileNotFoundError,PermissionError):pass
 print(json.dumps({'event':'LOCK_HELD_NEGATIVE_ORACLE','lock_time_local_revision':rev,'local_revision_while_held':after_rev,'local_revision_unchanged':after_rev==rev,'old_script_revision_while_held':after[0] if after else None,'old_script_sha256_while_held':after[1] if after else None,'old_script_unchanged':bool(after and after[0]==1 and after[1]==V1SHA),'agent_apply_sync_failed_database_locked_log_match':bool(log_match),'agent_error_line_sha256':log_match,'proxy_ack_events':ack,'proxy_negative_ack_exact_observed':any(x['ok'] is False and x['applied_rev']==0 for x in ack),'raw_log_or_frames_emitted':False},separators=(',',':')),flush=True)
finally:
 if locked:
  try:con.execute('ROLLBACK')
  except Exception:pass
 con.close()
 print(json.dumps({'event':'LOCK_RELEASED_BY_ROLLBACK','db_write_performed':False},separators=(',',':')),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-native4-db-write-lock-870358cd92ee';_,t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':30,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];e={'task_id':tid,'task_name':name,'target_parent_node_id':PARENT,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'timeout':30,'retry':0,'schedule_present':False,'v1_script_id':SCRIPT,'v1_script_sha256':V1SHA,'fault_put_requires_live_execution_log_marker':'LOCK_ACQUIRED','script_v2_put_attempted':False}
try:
 _,rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==e['command_sha256'] and rd.get('timeout')==30 and rd.get('retry')==0
 _,run=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=run[0]['id'];e['execution_id']=eid;save_progress('PARENT_LOCK_TASK_RUNNING_WAITING_FOR_REAL_LOCK_ACQUIRED_LOG',parent_lock_task_id=tid,lock_execution_id=eid,script_v2_put_attempted=False)
 deadline=time.monotonic()+8;marker=None;status=None;seen=[]
 while time.monotonic()<deadline:
  _,ex=api('GET','/api/executions/'+eid);status=ex.get('status');rows=get('/api/executions/'+eid+'/logs');out=''.join(x.get('chunk','') for x in sorted(rows,key=lambda z:z.get('seq',0)) if x.get('stream')=='stdout');seen.append({'status':status,'log_bytes':len(out.encode())})
  for line in out.splitlines():
   try:z=json.loads(line)
   except Exception:continue
   if z.get('event')=='LOCK_ACQUIRED':marker=z;break
  if marker or status in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.1)
 e['lock_marker_observation']=seen;e['execution_status_at_marker']=status;e['lock_acquired_marker']=marker
 if marker:
  if marker.get('node_id')!=CHILD or marker.get('agent_id')!=AGENT or marker.get('old_script_id')!=SCRIPT or marker.get('old_script_sha256')!=V1SHA or marker.get('old_script_revision')!=1 or marker.get('schedule_count')!=0 or marker.get('db_dev')!=33 or marker.get('db_inode')!=155 or marker.get('lock_hold_seconds')!=12:raise RuntimeError('LOCK_ACQUIRED oracle did not match exact approved baseline')
  if status in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:raise RuntimeError('lock Task already terminal; refusal to mutate after lock marker is no longer live')
  current=get('/api/scripts/'+SCRIPT)
  if current.get('revision')!=1 or current.get('sha256')!=V1SHA:raise RuntimeError('Script v1 changed before fault update')
  b=dict(current);b['content']=V2
  put_status,updated=api('PUT','/api/scripts/'+SCRIPT,b,(200,201));read=get('/api/scripts/'+SCRIPT)
  if read.get('revision')!=2 or read.get('sha256')!=V2SHA or read.get('content')!=V2:raise RuntimeError('Script v2 exact Hub readback failed')
  e.update({'script_v2_put_http_status':put_status,'script_v2_readback':{'id':read.get('id'),'revision':read.get('revision'),'sha256':read.get('sha256')},'script_v2_put_attempted':True,'script_v2_sha256':V2SHA,'script_v2_content_emitted':False})
  save_progress('LOCK_HELD_SCRIPT_V2_PUT_COMPLETE_WAITING_FOR_LOCK_RESULT',parent_lock_task_id=tid,lock_execution_id=eid,script_id=SCRIPT,script_v1_sha256=V1SHA,script_v2_sha256=V2SHA,script_v2_put_attempted=True,lock_time_child_revision=marker.get('lock_time_local_revision'),lock_time_old_script_sha256=marker.get('old_script_sha256'))
  transitions=[];deadline=time.monotonic()+25;terminal=None;last=None
  while time.monotonic()<deadline:
   _,ex=api('GET','/api/executions/'+eid);status=ex.get('status');rows=get('/api/executions/'+eid+'/logs');out=''.join(x.get('chunk','') for x in sorted(rows,key=lambda z:z.get('seq',0)) if x.get('stream')=='stdout')
   node=get('/api/nodes/'+CHILD);v={'execution_status':status,'child_status':node.get('status'),'sync_status':node.get('sync_status'),'global_revision':node.get('global_revision')}
   if not transitions or transitions[-1]!=v:transitions.append(v)
   for line in out.splitlines():
    try:z=json.loads(line)
    except Exception:continue
    if z.get('event')=='LOCK_HELD_NEGATIVE_ORACLE':last=z
   if status in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:terminal=ex;break
   time.sleep(.25)
  if terminal:
   rows=get('/api/executions/'+eid+'/logs');out=''.join(x.get('chunk','') for x in sorted(rows,key=lambda z:z.get('seq',0)) if x.get('stream')=='stdout')
   parsed=[]
   for line in out.splitlines():
    try:
     z=json.loads(line)
     if z.get('event') in {'LOCK_ACQUIRED','LOCK_HELD_NEGATIVE_ORACLE','LOCK_RELEASED_BY_ROLLBACK'}:parsed.append(z)
    except Exception:pass
   e.update({'execution_terminal_status':terminal.get('status'),'execution_terminal_node_id':terminal.get('node_id'),'terminal_protocol_events':parsed,'child_api_transitions_during_lock':transitions,'actual_negative_ack_event_seen':any(x.get('proxy_negative_ack_exact_observed') for x in parsed),'actual_busy_error_log_seen':any(x.get('agent_apply_sync_failed_database_locked_log_match') for x in parsed),'local_revision_unchanged_while_locked':next((x.get('local_revision_unchanged') for x in parsed if x.get('event')=='LOCK_HELD_NEGATIVE_ORACLE'),None),'old_script_preserved_while_locked':next((x.get('old_script_unchanged') for x in parsed if x.get('event')=='LOCK_HELD_NEGATIVE_ORACLE'),None)})
  else:e.update({'execution_terminal_status':None,'child_api_transitions_during_lock':transitions,'lock_result_timeout':True})
 else:
  e.update({'script_v2_put_attempted':False,'reason':'LOCK_ACQUIRED was not observed as a real live Execution log; no Script update was sent.'})
finally:
 e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_after_status']=404
 except Exception:e['task_get_after_status']='not-404'
(H/'sqlite-write-lock-fault-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':'LOCK_FAULT_INJECTED' if e.get('script_v2_put_attempted') else 'LOCK_GATE_NO_FAULT','lock_acquired':bool(marker),'v2_put_attempted':e.get('script_v2_put_attempted'),'task_deleted_404':e.get('task_get_after_status')==404,'lock_time_baseline':marker,'execution_terminal_status':e.get('execution_terminal_status'),'ack_captured':e.get('actual_negative_ack_event_seen'),'agent_busy_error_log_seen':e.get('actual_busy_error_log_seen'),'local_revision_unchanged':e.get('local_revision_unchanged_while_locked'),'old_script_preserved':e.get('old_script_preserved_while_locked')},ensure_ascii=False))
