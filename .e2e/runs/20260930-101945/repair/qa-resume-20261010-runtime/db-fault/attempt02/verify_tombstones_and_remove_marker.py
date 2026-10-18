import hashlib,json,shlex,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';SCRIPT='20a79944-5581-494b-83d8-5b365738c1e4';TASK='fdd19200-7fbd-42ad-935e-40fb80e57b0d';ROOT='/tmp/qa-resume-20261008-child-ciivjupc';MARKER='DBLOCK_V2_A2_870358CD92EE';TASK_NAME='qa-resume-20261010-native4-db-cleanup-attempt02-870358cd92ee'
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
src=r'''import hashlib,json,os,sqlite3,stat,time
from datetime import datetime,timezone
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';SCRIPT='20a79944-5581-494b-83d8-5b365738c1e4';TASK='fdd19200-7fbd-42ad-935e-40fb80e57b0d';ROOTDEV=33;ROOTINO=135;DBINO=155;UID=65534;BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';PID=47015;TICK=130084225;SUPPID=44932;SUPTICK=123677922;MARKER='DBLOCK_V2_A2_870358CD92EE'
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();st=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'startticks':int(st[19]),'uid':uid,'exe':os.readlink(q/'exe'),'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
def owner_guard():
 st=D.lstat()
 if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(ROOTDEV,ROOTINO,UID,0o700):raise RuntimeError('exact owned root guard failed')
 m=D/'owner-manifest.json'
 if m.is_symlink() or m.stat().st_uid!=UID:raise RuntimeError('owner manifest file guard failed')
 j=json.loads(m.read_text())
 if j.get('node_id')!=NODE or j.get('agent_id')!=AGENT or j.get('directory')!=str(D):raise RuntimeError('owner manifest identity mismatch')
 c=proc(PID);s=proc(SUPPID)
 if c['startticks']!=TICK or c['uid']!=UID or c['exe_sha256']!=BIN_SHA or c['argv']!=[str(D/'nodesteer-agent'),'--config',str(D/'child.yaml')] or c['cgroup']!='0::/system.slice/nodesteer-agent-repair-20260930-101945-native-4.service':raise RuntimeError('owned child fingerprint changed')
 if s['startticks']!=SUPTICK or s['uid']!=UID or s['exe_sha256']!=SUP_SHA or not any(str(D/'supervisor.py')==x for x in s['argv']) or s['cgroup']!=c['cgroup']:raise RuntimeError('owned supervisor fingerprint changed')
 return c,s
def read_db():
 dbp=D/'data/state.db';x=dbp.lstat()
 if dbp.is_symlink() or (x.st_dev,x.st_ino,x.st_uid)!=(ROOTDEV,DBINO,UID):raise RuntimeError('exact child database owner/inode guard failed')
 con=sqlite3.connect('file:'+str(dbp)+'?mode=ro',uri=True,timeout=2);con.row_factory=sqlite3.Row
 q=con.execute('PRAGMA quick_check').fetchone()[0];rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];s=con.execute('select id,revision,sha256 from scripts where id=?',(SCRIPT,)).fetchall();t=con.execute('select id from tasks where id=?',(TASK,)).fetchall();all_s=con.execute('select count(*) from scripts').fetchone()[0];all_t=con.execute('select count(*) from tasks').fetchone()[0];sched=con.execute('select count(*) from schedules').fetchone()[0];con.close();return {'quick_check':q,'local_revision':rev,'owned_script_rows':len(s),'owned_task_rows':len(t),'remaining_script_rows':all_s,'remaining_task_rows':all_t,'remaining_schedule_rows':sched,'owned_script_rows_detail':[{'id':z['id'],'revision':z['revision'],'sha256':z['sha256']} for z in s]}
owner_guard();started=utc();mono=time.monotonic();marker=D/'db-fault-marker.txt';removed=False;marker_state='absent'
if marker.exists() or marker.is_symlink():
 ms=marker.lstat()
 if marker.is_symlink() or not stat.S_ISREG(ms.st_mode) or ms.st_uid!=UID or marker.read_bytes()!=('DBLOCK_V2_A2_870358CD92EE\n').encode():raise RuntimeError('exact marker owner/type/content guard failed')
 marker.unlink();removed=True;marker_state='removed_exact_owned_marker'
owner_guard();deadline=time.monotonic()+52;history=[];last=None;cleared=False
while time.monotonic()<deadline:
 owner_guard();last=read_db();last.update({'at_utc':utc()});
 if not history or history[-1]!=last:history.append(last)
 if last['quick_check']=='ok' and last['owned_script_rows']==0 and last['owned_task_rows']==0 and last['remaining_script_rows']==0 and last['remaining_task_rows']==0 and last['remaining_schedule_rows']==0:cleared=True;break
 time.sleep(1)
ended=utc();elapsed=round(time.monotonic()-mono,3);owner_guard()
print(json.dumps({'event':'FIXTURE_CLEANUP_AND_TOMBSTONE_ORACLE','node_id':NODE,'agent_id':AGENT,'child_pid':PID,'child_startticks':TICK,'supervisor_pid':SUPPID,'supervisor_startticks':SUPTICK,'marker_path':str(marker),'marker_state':marker_state,'marker_removed':removed,'marker_exists_after':marker.exists(),'started_at_utc':started,'ended_at_utc':ended,'elapsed_monotonic_seconds':elapsed,'deadline_seconds':52,'tombstones_cleared':cleared,'last_db_observation':last,'observations':history,'execution_history_preserved':True,'no_service_or_process_operation':True},separators=(',',':')),flush=True)
if not cleared:raise SystemExit('fixture tombstone not observed before bounded deadline')'''
cmd='python3 -c '+shlex.quote(src);parent=api('GET','/api/nodes/'+PARENT)[1]
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise SystemExit('exact parent online+synced guard failed')
task=api('POST','/api/tasks',{'name':TASK_NAME,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('cleanup Command Task create response missing ID')
e={'task_id':tid,'task_name':TASK_NAME,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'retry':0,'timeout':60,'schedule':False,'marker_path':ROOT+'/db-fault-marker.txt'}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[PARENT] or rd.get('timeout')!=60 or rd.get('retry')!=0 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('exact cleanup task readback mismatch')
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('cleanup Task run response shape/cardinality mismatch')
 eid=rows[0]['id'];e.update({'execution_id':eid,'run_http_status':run_status});deadline=time.monotonic()+66;final=None;stdout=''
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout');z=json.loads(stdout) if stdout else {}
 e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'tombstone_oracle':z,'execution_history_preserved':True})
 if not final or final.get('status')!='SUCCESS' or z.get('event')!='FIXTURE_CLEANUP_AND_TOMBSTONE_ORACLE' or not z.get('tombstones_cleared') or not z.get('marker_removed'):raise RuntimeError('fixture marker/tombstone physical cleanup oracle failed')
finally:
 try:e['task_delete_http_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_http_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
(H/'cleanup-tombstone-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'FIXTURE_CLEANUP_COMPLETE' if e.get('task_get_404') and e.get('tombstone_oracle',{}).get('tombstones_cleared') else 'CLEANUP_INCOMPLETE','updated_at_utc':utc(),'cleanup_tombstone_evidence':'cleanup-tombstone-evidence.json','cleanup_task_id':tid,'cleanup_execution_id':e.get('execution_id'),'cleanup_task_get404':e.get('task_get_404'),'marker_removed':e.get('tombstone_oracle',{}).get('marker_removed'),'tombstones_cleared':e.get('tombstone_oracle',{}).get('tombstones_cleared'),'next_action':'Verify final API 404/child empty DB/owner unchanged; report UI auth gate and case scope.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'cleanup_task_id':tid,'execution_id':e.get('execution_id'),'execution_status':e.get('execution_status'),'marker_removed':e.get('tombstone_oracle',{}).get('marker_removed'),'tombstones_cleared':e.get('tombstone_oracle',{}).get('tombstones_cleared'),'task_get404':e.get('task_get_404')},ensure_ascii=False))
