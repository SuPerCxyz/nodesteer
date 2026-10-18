import hashlib,json,os,pathlib,re,sqlite3,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CU='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';AU='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';CFG=str(R/'agent.yaml');APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';OLDPID=48271;OLDSTART=130265882
V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a,t=10):return subprocess.run(a,text=True,capture_output=True,timeout=t)
def props(u):
 p=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec'],8);return dict(z.split('=',1) for z in p.stdout.splitlines() if '=' in z)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 if pid<=0:return None
 raw=open(f'/proc/{pid}/stat').read();q=raw[raw.rfind(')')+2:].split();st=open(f'/proc/{pid}/status').read();uid=int(re.search(r'^Uid:\s+(\d+)',st,re.M).group(1));ar=open(f'/proc/{pid}/cmdline','rb').read()
 return {'pid':pid,'startticks':int(q[19]),'state':q[0],'uid':uid,'argv':[x.decode(errors='replace') for x in ar.split(b'\0') if x],'argv_sha256':hashlib.sha256(ar).hexdigest(),'exe_path':os.readlink(f'/proc/{pid}/exe'),'exe_sha256':sha(f'/proc/{pid}/exe'),'cgroup':open(f'/proc/{pid}/cgroup').read().strip()}
def die(s,**kw):print(json.dumps({'status':s,**kw},sort_keys=True));raise SystemExit(2)
owner=json.loads((R/'owner.json').read_text());expect={'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':CU,'binary_path':B,'binary_sha256':BS,'config_path':CFG,'data_dir':str(R/'state')}
if not R.is_dir() or R.is_symlink() or R.stat().st_uid!=0 or (R.stat().st_mode&0o7777)!=0o700 or any(owner.get(k)!=v for k,v in expect.items()):die('OWNED_ROOT_OR_MARKER_GUARD_FAILED')
cfg=pathlib.Path(CFG);cs=cfg.stat()
if cfg.is_symlink() or cs.st_uid!=0 or (cs.st_mode&0o7777)!=0o600 or not cfg.is_file() or sha(B)!=BS:die('CHILD_CONFIG_METADATA_OR_BINARY_GUARD_FAILED')
cu=props(CU);pid=int(cu.get('MainPID','0') or 0)
if cu.get('LoadState')!='loaded' or cu.get('ActiveState') not in {'failed','inactive'} or pid!=0 or cu.get('RuntimeMaxUSec') not in {'1h','3600000000'}:die('CHILD_UNIT_NOT_EXPIRED_OR_AMBIGUOUS',unit=cu)
old=None
try:old=proc(OLDPID)
except FileNotFoundError:pass
if old and old.get('startticks')==OLDSTART and old.get('exe_sha256')==BS and old.get('argv')==[B,'--config',CFG]:die('OLD_OWNED_PROCESS_STILL_EXISTS_NO_SIGNAL',process=old)
appunit=props(AU);appunitpath=pathlib.Path('/etc/systemd/system')/AU;files={'binary':sha(R/'app/bin/qa-app') if (R/'app/bin/qa-app').is_file() else None,'config':sha(R/'app/config/qa-app.env') if (R/'app/config/qa-app.env').is_file() else None,'unit':sha(appunitpath) if appunitpath.is_file() else None}
if files!=V1 or appunit.get('LoadState')!='loaded':die('V1_APP_FILES_OR_UNIT_GUARD_FAILED',unit=appunit,files=files)
parents={}
for u,expected_pid,expected_ticks,expected_sha in [('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924,BS),('nodesteer-agent.service',24684,59589521,'43bf33d06e775bd6581f120a002d6d904de36de6c6d4e18eaef90f6862fb6998')]:
 up=props(u);p=int(up.get('MainPID','0') or 0);q=proc(p) if p else None
 if up.get('ActiveState')!='active' or not q or q.get('pid')!=expected_pid or q.get('startticks')!=expected_ticks or q.get('uid')!=0 or q.get('exe_sha256')!=expected_sha:die('ORIGINAL_PARENT_SERVICE_GUARD_FAILED',unit=u,properties=up,process=q)
 parents[u]={'properties':up,'pid':p,'startticks':q['startticks'],'exe_sha256':q['exe_sha256']}
db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row;prejournals=[dict(x) for x in db.execute('select id,to_version,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? and phase=?',(APP,'PREPARING'))];db.close()
reset=run(['systemctl','reset-failed',CU],10)
end=time.monotonic()+5
while time.monotonic()<end:
 cu=props(CU)
 if cu.get('LoadState') in {'not-found',''}:break
 time.sleep(.1)
else:die('OLD_TRANSIENT_UNIT_STILL_LOADED_NO_RECREATE',unit=cu,reset_rc=reset.returncode)
cmd=['systemd-run','--quiet','--no-block','--unit='+CU,'--property=User=root','--property=Restart=no','--property=KillMode=control-group','--property=RuntimeMaxSec=3600s','--property=TimeoutStopSec=3s','--property=WorkingDirectory='+str(R),'--property=StandardOutput=append:'+str(R/'log/agent.log'),'--property=StandardError=append:'+str(R/'log/agent.log'),B,'--config',CFG]
z=run(cmd,15)
if z.returncode:die('SYSTEMD_RUN_FAILED',returncode=z.returncode,stderr=z.stderr[-500:])
up={};new=None;end=time.monotonic()+15
while time.monotonic()<end:
 up=props(CU);p=int(up.get('MainPID','0') or 0)
 if p:
  try:new=proc(p)
  except (FileNotFoundError,ProcessLookupError):new=None
  if new:break
 time.sleep(.1)
if not new:die('NEW_CHILD_PROCESS_NOT_FOUND',unit=up)
checks={'different_pid':new['pid']!=OLDPID,'root_uid':new['uid']==0,'argv_exact':new['argv']==[B,'--config',CFG],'exe_path_exact':new['exe_path']==B,'exe_sha_expected':new['exe_sha256']==BS,'cgroup_exact':new['cgroup'].endswith('/system.slice/'+CU),'unit_active':up.get('ActiveState')=='active','runtime_one_hour':up.get('RuntimeMaxUSec')=='1h'}
if not all(checks.values()):die('NEW_CHILD_IDENTITY_GUARD_FAILED',checks=checks,unit=up,process=new)
for u,saved in parents.items():
 nowp=props(u);q=proc(int(nowp.get('MainPID','0') or 0))
 if nowp.get('ActiveState')!='active' or q['pid']!=saved['pid'] or q['startticks']!=saved['startticks'] or q['exe_sha256']!=saved['exe_sha256']:die('PARENT_UNIT_CHANGED_DURING_RECREATE',unit=u)
print(json.dumps({'status':'EXACT_OWNED_CHILD_UNIT_RECREATED','owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'root':str(R),'config_metadata':{'path':CFG,'uid':cs.st_uid,'mode':'0600','contents_read':False},'binary_sha256':BS,'new_child_process':new,'new_child_unit':up,'app_v1_hashes_before_restart':files,'app_unit_before_restart':appunit,'pre_restart_preparing_journals':prejournals,'original_parent_units_preserved':parents,'captured_unix':int(time.time())},sort_keys=True),flush=True)
