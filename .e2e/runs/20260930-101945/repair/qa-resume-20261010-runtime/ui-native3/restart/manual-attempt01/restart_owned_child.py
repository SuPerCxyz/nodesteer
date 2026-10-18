import ast,hashlib,json,shlex,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';TASK_NAME='qa-resume-20261010-native4-child-restart-manual-attempt01-870358cd92ee';BASELINE_AGENT_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';OLD_PID=47015;OLD_TICK=130084225;SUP_PID=44932;SUP_TICK=123677922;UNIT='nodesteer-agent-repair-20260930-101945-native-4.service';ROOT='/tmp/qa-resume-20261008-child-ciivjupc';CONFIG_SHA='2d36aeb7f541573683c5458f35e8322ea7b84024f33d01541f720b144dbbab1c';MANIFEST_SHA='fc7ad2e2dbba5c00c95ed08419cfc62b6601da1d13a0dc6ded9d8d5b4a7131f3';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';UNIT_PID=32487;UNIT_TICK=84202343
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
def node_summary(n):return {k:n.get(k) for k in ['id','agent_id','hostname','status','sync_status','global_revision','last_seen']}
def preflight():
 c=api('GET','/api/nodes/'+CHILD)[1];p=api('GET','/api/nodes/'+PARENT)[1];alln=api('GET','/api/nodes')[1];tasks=api('GET','/api/tasks')[1];schedules=api('GET','/api/schedules')[1];ex=api('GET','/api/executions?node_id='+CHILD+'&limit=100')[1]
 if isinstance(alln,dict):alln=alln.get('nodes',alln.get('items',[]))
 if isinstance(ex,dict):ex=ex.get('executions',ex.get('items',[]))
 four=[node_summary(n) for n in alln if n.get('hostname') in {f'qa-repair-20260930-101945-native-{i}' for i in range(1,5)}]
 if c.get('id')!=CHILD or c.get('agent_id')!=AGENT or c.get('status')!='online' or c.get('sync_status')!='synced':raise RuntimeError('child API exact identity/health guard failed')
 if p.get('id')!=PARENT or p.get('status')!='online' or p.get('sync_status')!='synced':raise RuntimeError('parent API exact identity/health guard failed')
 if len(four)!=4 or any(x.get('status')!='online' or x.get('sync_status')!='synced' for x in four):raise RuntimeError('original four Agent health guard failed')
 child_tasks=[x for x in tasks if CHILD in ((x.get('target') or {}).get('node_ids') or [])]
 if child_tasks or any(x.get('task_id') in {t.get('id') for t in child_tasks} for x in schedules):raise RuntimeError('child Task/Schedule guard failed')
 pending=[x for x in ex if x.get('node_id')==CHILD and str(x.get('status','')).upper() in ('RUNNING','PENDING')]
 if pending:raise RuntimeError('child running/pending Execution guard failed')
 return {'captured_at_utc':utc(),'child':node_summary(c),'parent':node_summary(p),'original_four':four,'child_task_count':len(child_tasks),'schedule_definition_count':len(schedules),'running_or_pending_execution_count':len(pending)}
pre=preflight()
src=r'''import hashlib,json,os,re,signal,sqlite3,stat,subprocess,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';PARENT_UNIT='nodesteer-agent-repair-20260930-101945-native-4.service';OLD_PID=47015;OLD_TICK=130084225;SUP_PID=44932;SUP_TICK=123677922;UNIT_PID=32487;UNIT_TICK=84202343;BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';CONFIG_SHA='2d36aeb7f541573683c5458f35e8322ea7b84024f33d01541f720b144dbbab1c';MANIFEST_SHA='fc7ad2e2dbba5c00c95ed08419cfc62b6601da1d13a0dc6ded9d8d5b4a7131f3';BASELINE_NODE_NAME='qa-resume-20261008-native4-child-c5d48ea9-da08-4123-94fb-69b11abd59a7'
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);gid=int(next(x for x in ss if x.startswith('Gid:')).split()[2]);argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];exe=os.readlink(q/'exe');return {'pid':pid,'ppid':int(tail[1]),'startticks':int(tail[19]),'uid':uid,'gid':gid,'exe_path':exe,'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
def guard_root_manifest_config():
 st=D.lstat();m=D/'owner-manifest.json';cfg=D/'child.yaml';binp=D/'nodesteer-agent'
 if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(33,135,65534,0o700):raise RuntimeError('owned_root_exact_guard_failed')
 if m.is_symlink() or m.stat().st_uid!=65534 or stat.S_IMODE(m.stat().st_mode)!=0o600 or sha(m)!=MANIFEST_SHA:raise RuntimeError('owner_manifest_exact_guard_failed')
 manifest=json.loads(m.read_text())
 if manifest.get('node_id')!=NODE or manifest.get('agent_id')!=AGENT or manifest.get('directory')!=str(D):raise RuntimeError('owner_manifest_identity_guard_failed')
 childmeta=manifest.get('child') or {};mt=childmeta.get('starttime',childmeta.get('startticks'))
 if childmeta.get('pid')!=OLD_PID or mt!=OLD_TICK or childmeta.get('uid')!=65534 or childmeta.get('exe_sha256')!=BIN_SHA:raise RuntimeError('owner_manifest_child_row_guard_failed')
 if cfg.is_symlink() or cfg.stat().st_uid!=65534 or stat.S_IMODE(cfg.stat().st_mode)!=0o600 or sha(cfg)!=CONFIG_SHA:raise RuntimeError('config_owner_mode_sha_guard_failed')
 if binp.is_symlink() or binp.stat().st_uid!=65534 or sha(binp)!=BIN_SHA or not os.access(binp,os.X_OK):raise RuntimeError('owned_binary_guard_failed')
 text=cfg.read_text(errors='strict')
 def scalar(key):
  vals=[]
  for line in text.splitlines():
   match=re.match(r'^'+re.escape(key)+r':\s*(.*?)\s*$',line)
   if match:
    v=match.group(1).split(' #',1)[0].strip()
    if len(v)>=2 and v[0]==v[-1] and v[0] in ('"',"'"):v=v[1:-1]
    vals.append(v)
  if len(vals)!=1:raise RuntimeError('config_expected_single_'+key+'_scalar')
  return vals[0]
 data_dir=scalar('data_dir');cfg_agent=scalar('agent_id');hub_url=scalar('hub_url');node_name=scalar('node_name')
 if data_dir!=str(D/'data') or cfg_agent!=AGENT or node_name!=BASELINE_NODE_NAME:raise RuntimeError('config_owned_data_or_registration_identity_guard_failed')
 proxy=manifest.get('proxy') or {};u=urlsplit(hub_url)
 if u.scheme not in ('ws','wss') or u.hostname!=proxy.get('bind_ip') or u.port!=proxy.get('port'):raise RuntimeError('config_hub_endpoint_not_owned_proxy')
 binary=manifest.get('binary') or {};config_meta=manifest.get('config') or {}
 if binary.get('copy_path')!=str(binp) or binary.get('copy_sha256')!=BIN_SHA or config_meta.get('path')!=str(cfg):raise RuntimeError('owner_manifest_binary_config_guard_failed')
 return manifest,{'path':str(cfg),'sha256':sha(cfg),'data_dir':data_dir,'agent_id':cfg_agent,'node_name':node_name,'hub_scheme':u.scheme,'hub_host':u.hostname,'hub_port':u.port},binp,cfg,m,proxy
manifest,config,binp,cfg,manifest_path,proxy=guard_root_manifest_config()
if os.geteuid()!=0:raise RuntimeError('parent_command_must_be_root_for_exact_privilege_drop')
child=proc(OLD_PID);sup=proc(SUP_PID)
if child['startticks']!=OLD_TICK or child['uid']!=65534 or child['exe_sha256']!=BIN_SHA or child['exe_path']!=str(binp) or child['argv']!=[str(binp),'--config',str(cfg)] or child['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('exact_old_child_PID_ticks_UID_argv_exe_cgroup_guard_failed')
if child['ppid']!=1:raise RuntimeError('old_child_ppid_changed')
if sup['startticks']!=SUP_TICK or sup['uid']!=65534 or sup['exe_sha256']!=SUP_SHA or sup['exe_path']!='/usr/bin/python3.13' or str(cfg.parent) not in sup['argv'] or sup['cgroup']!=child['cgroup']:raise RuntimeError('exact_proxy_supervisor_guard_failed')
# Verify existing child and proxy identities, DB state, controls, and cgroup Unit without changing them.
db=D/'data/state.db';ds=db.lstat()
if db.is_symlink() or (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,155,65534):raise RuntimeError('exact_DB_inode_owner_guard_failed')
con=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=3);con.row_factory=sqlite3.Row
quick=con.execute('PRAGMA quick_check').fetchone()[0];identity={x['key']:x['value'] for x in con.execute("select key,value from identity where key in ('node_id','agent_id')")};base_rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];tables={x[0] for x in con.execute("select name from sqlite_master where type='table'")};counts={}
for table in ['scripts','tasks','schedules','applications','deployments','unit_registry']:
 if table not in tables:raise RuntimeError('required_DB_table_missing')
 counts[table]=con.execute('select count(*) from '+table).fetchone()[0]
run_pending=0
if 'executions' in tables:
 cols={x['name'] for x in con.execute('pragma table_info(executions)')}
 if 'status' in cols:run_pending=con.execute("select count(*) from executions where upper(status) in ('RUNNING','PENDING')").fetchone()[0]
con.close()
if quick!='ok' or identity.get('node_id')!=NODE or identity.get('agent_id')!=AGENT or run_pending:raise RuntimeError('DB_integrity_identity_or_running_execution_guard_failed')
if any(counts.values()):raise RuntimeError('child_fixture_rows_not_empty')
controls=D/'controls.json';events=D/'proxy-events.jsonl';log=D/'agent.log';
if controls.is_symlink() or json.loads(controls.read_text())!={} or events.is_symlink() or log.is_symlink() or not log.resolve().is_relative_to(D.resolve()):raise RuntimeError('proxy_controls_or_owned_log_guard_failed')
event_offset=events.stat().st_size;log_offset=log.stat().st_size
# Verify listener socket remains owned by the exact existing proxy supervisor.
port=int(proxy.get('port') or 0);listen=[]
for name in ('tcp','tcp6'):
 f=Path('/proc/net/'+name)
 if f.exists():
  for line in f.read_text().splitlines()[1:]:
   a=line.split()
   if len(a)>9 and a[1].rsplit(':',1)[1].lower()==format(port,'04x').lower() and a[3]=='0A':listen.append(a[9])
owned=[]
for fd in Path('/proc/%d/fd'%SUP_PID).iterdir():
 try:
  q=os.readlink(fd)
  if q.startswith('socket:['):owned.append(q[8:-1])
 except (FileNotFoundError,PermissionError):pass
if not set(listen)&set(owned):raise RuntimeError('owned_proxy_listener_guard_failed')
# The existing systemd unit is a different MainPID in the same cgroup; verify but never restart/stop it.
cp=subprocess.run(['systemctl','show',PARENT_UNIT,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup,Restart,KillMode'],capture_output=True,text=True,timeout=5,check=False);props={}
for line in cp.stdout.splitlines():
 if '=' in line:k,v=line.split('=',1);props[k]=v
if cp.returncode or props.get('Id')!=PARENT_UNIT or props.get('ActiveState')!='active' or props.get('ControlGroup')!='/system.slice/'+PARENT_UNIT or props.get('KillMode')!='control-group' or int(props.get('MainPID','0'))!=UNIT_PID:raise RuntimeError('parent_unit_identity_guard_failed')
unit_proc=proc(UNIT_PID)
if unit_proc['startticks']!=UNIT_TICK or unit_proc['uid']!=0 or unit_proc['exe_sha256']!=BIN_SHA or unit_proc['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('parent_unit_main_process_guard_failed')
# Last identity check immediately before the only authorized signal.
child=proc(OLD_PID)
if child['startticks']!=OLD_TICK or child['uid']!=65534 or child['exe_sha256']!=BIN_SHA or child['argv']!=[str(binp),'--config',str(cfg)] or child['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('pre_signal_child_guard_changed')
if sha(cfg)!=CONFIG_SHA or sha(manifest_path)!=MANIFEST_SHA:raise RuntimeError('pre_signal_config_or_manifest_sha_changed')
signal_time=utc();signal_mono=time.monotonic();os.kill(OLD_PID,signal.SIGTERM);old_exited=False;exit_deadline=time.monotonic()+8
while time.monotonic()<exit_deadline:
 try:
  old_now=proc(OLD_PID)
  if old_now['startticks']!=OLD_TICK:old_exited=True;break
 except (FileNotFoundError,ProcessLookupError):old_exited=True;break
 time.sleep(.1)
if not old_exited:raise RuntimeError('owned_child_did_not_exit_within_8s_after_sigterm')
# Use only the reviewed minimal environment; do not inherit parent NODESTEER variables or set HOME.
ss=(Path('/proc/%d/status'%SUP_PID).read_text().splitlines());gid=int(next(x for x in ss if x.startswith('Gid:')).split()[2]);uid=65534

def drop():
 os.setgroups([]);os.setgid(gid);os.setuid(uid)
with open(log,'ab') as stream:
 new=subprocess.Popen([str(binp),'--config',str(cfg)],cwd=str(D),stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,close_fds=True,start_new_session=True,preexec_fn=drop,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
new_pid=new.pid;launch_mono=time.monotonic();ready_deadline=launch_mono+30;newproc=None;db_after=None;event_samples=[];local_ready=False;conn_after=0;hello_after=0;ack_after=[];heartbeat_after=0
new_identity_deadline=time.monotonic()+5;newproc=None
while time.monotonic()<new_identity_deadline:
 try:newproc=proc(new_pid)
 except (FileNotFoundError,ProcessLookupError):
  if new.poll() is not None:break
  time.sleep(.1);continue
 if newproc['pid']==OLD_PID or newproc['startticks']==OLD_TICK or newproc['uid']!=65534 or newproc['gid']!=65534 or newproc['exe_path']!=str(binp) or newproc['exe_sha256']!=BIN_SHA or newproc['argv']!=[str(binp),'--config',str(cfg)] or newproc['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('new_child_PID_UID_exe_config_or_cgroup_mismatch_before_manifest_update')
 if cfg.stat().st_uid!=65534 or cfg.stat().st_mode&0o777!=0o600 or sha(cfg)!=CONFIG_SHA or sha(manifest_path)!=MANIFEST_SHA:raise RuntimeError('config_or_manifest_changed_before_child_row_update')
 newrow={'pid':new_pid,'starttime':newproc['startticks'],'uid':uid,'exe_path':newproc['exe_path'],'exe_sha256':newproc['exe_sha256'],'argv_owner_path_match':True};manifest['child']=newrow;tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
 with os.fdopen(fd,'wb') as f:f.write(json.dumps(manifest,separators=(',',':')).encode())
 os.chown(tmp,uid,gid);os.chmod(tmp,0o600);tmp.replace(manifest_path)
 if manifest_path.is_symlink() or manifest_path.stat().st_uid!=uid or stat.S_IMODE(manifest_path.stat().st_mode)!=0o600:raise RuntimeError('owner_manifest_atomic_update_owner_mode_failed')
 manifest_sha_after=sha(manifest_path);break
if newproc is None:raise RuntimeError('new_child_process_not_visible_within_5s_after_Popen')

def event_state():
 rows=[]
 if not events.exists() or events.is_symlink():return rows
 with events.open('rb') as f:f.seek(event_offset);data=f.read()
 for line in data.decode('utf-8','replace').splitlines():
  try:z=json.loads(line)
  except Exception:continue
  safe={k:z.get(k) for k in ['event','type','message_type','control_type','direction','ok','applied_rev','node_id','agent_id','at_utc','timestamp'] if k in z};rows.append(safe)
 return rows
while time.monotonic()<ready_deadline:
 try:newproc=proc(new_pid)
 except (FileNotFoundError,ProcessLookupError):
  if new.poll() is not None:break
  time.sleep(.1);continue
 if newproc['pid']==OLD_PID or newproc['startticks']==OLD_TICK or newproc['uid']!=65534 or newproc['gid']!=65534 or newproc['exe_path']!=str(binp) or newproc['exe_sha256']!=BIN_SHA or newproc['argv']!=[str(binp),'--config',str(cfg)] or newproc['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('new_child_PID_UID_exe_config_or_cgroup_mismatch')
 if cfg.stat().st_uid!=65534 or cfg.stat().st_mode&0o777!=0o600 or sha(cfg)!=CONFIG_SHA:raise RuntimeError('config_changed_during_restart')
 con=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True,timeout=2);con.row_factory=sqlite3.Row;quick_after=con.execute('PRAGMA quick_check').fetchone()[0];id_after={x['key']:x['value'] for x in con.execute("select key,value from identity where key in ('node_id','agent_id')")};db_after_rev=con.execute('select global_revision from sync_state where id=1').fetchone()[0];counts_after={t:con.execute('select count(*) from '+t).fetchone()[0] for t in ['scripts','tasks','schedules','applications','deployments','unit_registry']};con.close();db_after={'quick_check':quick_after,'identity':id_after,'local_revision':db_after_rev,'fixture_counts':counts_after}
 if quick_after!='ok' or id_after.get('node_id')!=NODE or id_after.get('agent_id')!=AGENT or db_after_rev<base_rev or any(counts_after.values()):raise RuntimeError('post_restart_DB_identity_revision_or_empty_fixture_guard_failed')
 ev=event_state();conns=[x for x in ev if str(x.get('event','')).lower()=='connection'];hellos=[x for x in ev if any('hello' in str(x.get(k,'')).lower() for k in ['event','type','message_type','control_type'])];acks=[x for x in ev if str(x.get('event','')).lower()=='sync_ack' and x.get('ok') is True];heartbeats=[x for x in ev if any('heartbeat' in str(x.get(k,'')).lower() for k in ['event','type','message_type','control_type'])];conn_after=len(conns);hello_after=len(hellos);ack_after=[{'applied_rev':x.get('applied_rev'),'ok':x.get('ok')} for x in acks if isinstance(x.get('applied_rev'),int) and x.get('applied_rev')>=base_rev];heartbeat_after=len(heartbeats);event_samples=ev[-30:]
 local_ready=True
 if conn_after>0 and ack_after:break
 time.sleep(.25)
if not local_ready:raise RuntimeError('new_child_failed_local_identity_DB_ready_guard_within_30s')
# Recheck proxy supervisor, parent Unit MainPID and root/config after launch.
sup_after=proc(SUP_PID);unit_after=subprocess.run(['systemctl','show',PARENT_UNIT,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup,Restart,KillMode'],capture_output=True,text=True,timeout=5,check=False);props2={}
for line in unit_after.stdout.splitlines():
 if '=' in line:k,v=line.split('=',1);props2[k]=v
if sup_after['startticks']!=SUP_TICK or sup_after['uid']!=65534 or sup_after['exe_sha256']!=SUP_SHA or sup_after['cgroup']!='0::/system.slice/'+PARENT_UNIT:raise RuntimeError('proxy_supervisor_changed_during_restart')
if unit_after.returncode or props2.get('ActiveState')!='active' or props2.get('MainPID')!=str(UNIT_PID) or props2.get('KillMode')!='control-group':raise RuntimeError('parent_Unit_changed_during_restart')
if D.lstat().st_ino!=135 or D.lstat().st_dev!=33 or cfg.stat().st_ino!=197 or sha(cfg)!=CONFIG_SHA:raise RuntimeError('owned_root_or_config_identity_changed')
manifest_sha_after=sha(manifest_path);log_after=log.stat().st_size;result={'status':'MANUAL_CHILD_RESTART_LOCAL_GUARDS_PASS','node_id':NODE,'agent_id':AGENT,'old_child':{'pid':OLD_PID,'startticks':OLD_TICK,'uid':65534,'exe_sha256':BIN_SHA,'config_path':str(cfg),'config_sha256_before':CONFIG_SHA,'config_sha256_after':sha(cfg),'signal':'SIGTERM','signal_at_utc':signal_time,'old_pid_exit_observed':old_exited,'exit_wait_monotonic_seconds':round(time.monotonic()-signal_mono,3)},'new_child':{'pid':new_pid,'startticks':newproc['startticks'],'uid':uid,'exe_path':newproc['exe_path'],'exe_sha256':newproc['exe_sha256'],'config_path':str(cfg),'argv_owner_path_match':True,'cgroup':newproc['cgroup'],'dropped_groups':True,'gid':gid,'env_keys_only':['PATH','LANG'],'home_env_set':False,'inherited_parent_nodesteer_env':False},'owner_root':{'path':str(D),'dev':33,'inode':135,'uid':65534,'mode':'0700'},'database':{'dev':33,'inode':155,'uid':65534,'quick_check_before':quick,'quick_check_after':db_after['quick_check'],'identity_before':identity,'identity_after':db_after['identity'],'local_revision_before':base_rev,'local_revision_after':db_after['local_revision'],'revision_not_retracted':db_after['local_revision']>=base_rev,'fixture_counts_before':counts,'fixture_counts_after':counts_after},'proxy_supervisor':{'pid':SUP_PID,'startticks':SUP_TICK,'uid':65534,'exe_sha256':SUP_SHA,'unchanged':True,'listener_owned':True},'parent_unit':{'id':PARENT_UNIT,'active_state':props2.get('ActiveState'),'main_pid':int(props2['MainPID']),'kill_mode':props2.get('KillMode'),'unchanged':True},'proxy_events_after_offset':{'connection_event_count':conn_after,'hello_event_count':hello_after,'successful_sync_ack_at_or_after_base_revision':ack_after,'heartbeat_event_count':heartbeat_after,'safe_metadata_last_30':event_samples},'agent_log':{'path':str(log),'bytes_before':log_offset,'bytes_after':log_after,'new_bytes':max(0,log_after-log_offset)},'owner_manifest_sha256_before':MANIFEST_SHA,'owner_manifest_sha256_after':manifest_sha_after,'owner_manifest_only_child_row_updated':True,'config_or_proxy_or_schema_changed':False,'no_raw_frames_credentials_or_environment_emitted':True,'elapsed_monotonic_seconds_since_launch':round(time.monotonic()-launch_mono,3)}
print(json.dumps(result,separators=(',',':')),flush=True)'''
# Compile the exact embedded remote program locally before creating any remote Task or signaling.
tree=ast.parse(Path(__file__).read_text());remote=None
for n in ast.walk(tree):
 if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='src' for t in n.targets):remote=ast.literal_eval(n.value);break
if not remote:raise RuntimeError('embedded remote source missing')
compile(remote,'<manual-owned-child-restart>','exec')
cmd='python3 -c '+shlex.quote(remote);parent=api('GET','/api/nodes/'+PARENT)[1]
if parent.get('id')!=PARENT or parent.get('status')!='online' or parent.get('sync_status')!='synced':raise RuntimeError('exact parent is not online+synced')
name=TASK_NAME;task=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201))[1];tid=task.get('id')
if not tid:raise RuntimeError('parent helper Task create response missing ID')
e={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(remote.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'timeout':60,'retry':0,'schedule':False,'preflight':pre,'authorization_review':'.e2e/runs/20260930-101945/repair/qa-resume-20261010-root/manual-child-restart-review.json','remote_code_compiled_locally':True,'signal_authorized_by_root':True}
try:
 rd=api('GET','/api/tasks/'+tid)[1]
 if rd.get('id')!=tid or rd.get('type')!='command' or rd.get('target',{}).get('node_ids')!=[PARENT] or rd.get('timeout')!=60 or rd.get('retry')!=0 or hashlib.sha256(rd.get('command','').encode()).hexdigest()!=e['command_sha256']:raise RuntimeError('exact parent helper Task readback mismatch')
 if any(x.get('task_id')==tid for x in api('GET','/api/schedules')[1]):raise RuntimeError('parent helper unexpectedly scheduled')
 # One final current API check immediately before Run; requires zero child work/schedules and all four original Nodes healthy.
 preflight_now=preflight();e['preflight_immediately_before_run']=preflight_now
 run_status,body=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));rows=body if isinstance(body,list) else (body.get('executions') or ([body] if body.get('id') else [])) if isinstance(body,dict) else []
 if len(rows)!=1 or not rows[0].get('id'):raise RuntimeError('parent helper Run response shape/cardinality mismatch')
 eid=rows[0]['id'];e['execution_id']=eid;e['run_http_status']=run_status;start_mono=time.monotonic();api_timeline=[];final=None;deadline=start_mono+60;last_seen_before=preflight_now['child'].get('last_seen')
 while time.monotonic()<deadline:
  final=api('GET','/api/executions/'+eid)[1]
  child_now=api('GET','/api/nodes/'+CHILD)[1]
  state={'at_utc':utc(),'execution_status':final.get('status'),'child_status':child_now.get('status'),'sync_status':child_now.get('sync_status'),'global_revision':child_now.get('global_revision'),'last_seen':child_now.get('last_seen'),'agent_id':child_now.get('agent_id')}
  if not api_timeline or any(state[k]!=api_timeline[-1][k] for k in ['execution_status','child_status','sync_status','global_revision','last_seen']):api_timeline.append(state)
  if final.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 logs=api('GET','/api/executions/'+eid+'/logs')[1];logs=logs if isinstance(logs,list) else [];stdout=''.join(x.get('chunk','') for x in sorted(logs,key=lambda z:(z.get('stream',''),z.get('seq',0))) if x.get('stream')=='stdout');stderr=''.join(x.get('chunk','') for x in logs if x.get('stream')=='stderr');result=json.loads(stdout) if stdout else None
 e.update({'execution_status':final.get('status') if final else None,'execution_node_id':final.get('node_id') if final else None,'stdout_sha256':hashlib.sha256(stdout.encode()).hexdigest(),'stdout_bytes':len(stdout.encode()),'stderr_sha256':hashlib.sha256(stderr.encode()).hexdigest(),'stderr_bytes':len(stderr.encode()),'result':result,'api_timeline':api_timeline,'api_child_last_seen_advanced':bool(api_timeline and api_timeline[-1].get('last_seen') and api_timeline[-1]['last_seen']!=last_seen_before),'credentials_emitted':False,'raw_frames_emitted':False})
finally:
 try:e['task_delete_http_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))[0]
 except Exception:e['task_delete_http_status']='delete_failed'
 e['task_get_404']=False
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_404']=True
 except Exception:pass
# Final API health and original-four guard after parent helper cleanup.
try:
 cafter=api('GET','/api/nodes/'+CHILD)[1];pafter=api('GET','/api/nodes/'+PARENT)[1];alln=api('GET','/api/nodes')[1]
 if isinstance(alln,dict):alln=alln.get('nodes',alln.get('items',[]))
 four_after=[node_summary(n) for n in alln if n.get('hostname') in {f'qa-repair-20260930-101945-native-{i}' for i in range(1,5)}]
 e['final_api']={'child':node_summary(cafter),'parent':node_summary(pafter),'original_four':four_after,'original_four_online_synced':len(four_after)==4 and all(x.get('status')=='online' and x.get('sync_status')=='synced' for x in four_after)}
except Exception:e['final_api']={'readback_failed':True}
e['overall_restart_local_and_api_success']=bool(e.get('execution_status')=='SUCCESS' and e.get('task_get_404') and e.get('result',{}).get('status')=='MANUAL_CHILD_RESTART_LOCAL_GUARDS_PASS' and e.get('result',{}).get('new_child',{}).get('pid')!=OLD_PID and e.get('final_api',{}).get('child',{}).get('status')=='online' and e.get('final_api',{}).get('child',{}).get('sync_status')=='synced' and e.get('final_api',{}).get('child',{}).get('agent_id')==AGENT and e.get('final_api',{}).get('original_four_online_synced'))
(H/'restart-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'MANUAL_CHILD_RESTART_COMPLETE' if e['overall_restart_local_and_api_success'] else 'MANUAL_CHILD_RESTART_PARTIAL_OR_FAILED','updated_at_utc':utc(),'restart_evidence':'restart-evidence.json','helper_task_id':tid,'helper_execution_id':e.get('execution_id'),'helper_task_get404':e.get('task_get_404'),'old_pid':OLD_PID,'new_pid':(e.get('result') or {}).get('new_child',{}).get('pid'),'api_child_sync_status':(e.get('final_api') or {}).get('child',{}).get('sync_status'),'original_four_online_synced':(e.get('final_api') or {}).get('original_four_online_synced'),'heartbeat_packet_verified':bool((e.get('result') or {}).get('proxy_events_after_offset',{}).get('heartbeat_event_count')),'next_action':'Review restart, process identity, durable DB revision, API HELLO/sync and heartbeat limits. Preserve exact child; no further restart.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'helper_task_id':tid,'execution_id':e.get('execution_id'),'execution_status':e.get('execution_status'),'task_get404':e.get('task_get_404'),'old_pid':OLD_PID,'new_pid':(e.get('result') or {}).get('new_child',{}).get('pid'),'new_revision':(e.get('result') or {}).get('database',{}).get('local_revision_after'),'connection_events':(e.get('result') or {}).get('proxy_events_after_offset',{}).get('connection_event_count'),'hello_events':(e.get('result') or {}).get('proxy_events_after_offset',{}).get('hello_event_count'),'sync_ack_events':(e.get('result') or {}).get('proxy_events_after_offset',{}).get('successful_sync_ack_at_or_after_base_revision'),'heartbeat_events':(e.get('result') or {}).get('proxy_events_after_offset',{}).get('heartbeat_event_count'),'api_child':(e.get('final_api') or {}).get('child'),'original_four_online_synced':(e.get('final_api') or {}).get('original_four_online_synced'),'overall':e['overall_restart_local_and_api_success']},ensure_ascii=False))
