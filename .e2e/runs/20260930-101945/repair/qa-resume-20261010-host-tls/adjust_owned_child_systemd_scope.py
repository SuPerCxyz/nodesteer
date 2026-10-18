#!/usr/bin/env python3
"""Add the approved exact host systemd write path to only the owned TLS child."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent
LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text())
CHILD=json.loads((sorted(HERE.glob('child-ca-*-restart.json'),key=lambda p:p.stat().st_mtime)[-1]).read_text())['remote']['child']
PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text())
NODE='611c02a5-1859-4465-878b-d543b7f6fdec';NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];ROOT=LIVE['remote_owner']['root'];UNIT='nodesteer-qa-tls-1358f9a990bd-child.service'
CLAIMS={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'child':CHILD,'unit':UNIT,'node_id':NODE_ID,'app_unit':'nodesteer-qa-tls-1358f9a990bd-app.service'}
src=r'''import hashlib,json,os,socket,sqlite3,stat,subprocess,time,uuid
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';U=Path('/etc/systemd/system')/C['unit'];AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc',str(pid));raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip();return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(exe),'argv':argv,'cgroup':cg}
def props(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for line in r.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);d[k]=v
 return r,d
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha'] or m.get('nonce')!='1358f9a990bd':raise RuntimeError('owned_root_manifest_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_guard_failed')
P=C['parent'];p=proc(P['pid'])
if p['starttime']!=P['starttime'] or p['uid']!=P['uid'] or p['cgroup']!=P['cgroup'] or p['exe_sha256']!=C['agent_sha'] or p['exe']!=str(AGENT):raise RuntimeError('native2_agent_owner_changed')
S=C['server'];p=proc(S['pid']);SU=Path('/etc/systemd/system')/S['unit'];sr,sd=props(S['unit'])
if p['starttime']!=S['starttime'] or p['uid']!=S['uid'] or p['exe_sha256']!=S['exe_sha256'] or p['cgroup']!=S['cgroup'] or SU.is_symlink() or sha(SU)!=C['server_unit_sha'] or sr.returncode or sd.get('ActiveState')!='active' or sd.get('MainPID')!=str(S['pid']):raise RuntimeError('https_server_owner_changed')
iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{S['port']:04X}";socks=[]
for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
if socks!=S['listen_socket_inodes']:raise RuntimeError('https_server_listener_changed')
CH=C['child'];r,d=props(C['unit']);pid=int(d.get('MainPID','0') or 0)
if r.returncode or d.get('LoadState')!='loaded' or d.get('ActiveState')!='active' or pid!=CH['pid'] or d.get('FragmentPath')!=str(U) or U.is_symlink() or U.resolve()!=U or sha(U)!=CH['file_sha256'] or U.stat().st_uid!=0 or stat.S_IMODE(U.stat().st_mode)!=0o644:raise RuntimeError('child_unit_owner_changed_before_scope_adjustment')
p=proc(pid)
if p['starttime']!=CH['starttime'] or p['uid']!=CH['uid'] or p['exe_sha256']!=C['agent_sha'] or p['exe']!=str(AGENT) or CH['argv_config'] not in p['argv'] or p['cgroup']!=CH['cgroup']:raise RuntimeError('child_process_owner_changed_before_scope_adjustment')
old=U.read_text()
if 'ProtectSystem=strict\n' not in old or 'ReadWritePaths='+str(D)+'\n' not in old or '/etc/systemd/system' in old:raise RuntimeError('child_unit_policy_precondition_mismatch')
new=old.replace('ReadWritePaths='+str(D)+'\n','ReadWritePaths='+str(D)+' /etc/systemd/system\n')
tmp=U.with_name(U.name+'.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o644)
with os.fdopen(fd,'w') as f:f.write(new);f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,U)
if U.stat().st_uid!=0 or stat.S_IMODE(U.stat().st_mode)!=0o644 or sha(U)==sha(Path('/dev/null')):raise RuntimeError('child_unit_file_write_gate_failed')
if 'ProtectSystem=strict\n' not in U.read_text() or 'ReadWritePaths='+str(D)+' /etc/systemd/system\n' not in U.read_text():raise RuntimeError('child_unit_policy_write_readback_failed')
subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=20)
r,d=props(C['unit'])
if r.returncode or d.get('ActiveState')!='active' or d.get('MainPID')!=str(pid) or d.get('FragmentPath')!=str(U):raise RuntimeError('child_unit_pre_restart_state_changed')
subprocess.run(['systemctl','restart',C['unit']],check=True,capture_output=True,timeout=30)
end=time.monotonic()+30;identity=None;state={}
while time.monotonic()<end:
 r,state=props(C['unit']);npid=int(state.get('MainPID','0') or 0)
 if r.returncode==0 and state.get('ActiveState')=='active' and npid>0:
  try:
   identity=proc(npid)
   if str(U).replace(C['unit'],C['unit']) and C['unit'] in identity['cgroup'] and identity['exe_sha256']==C['agent_sha'] and CH['argv_config'] in identity['argv']:break
  except (FileNotFoundError,ProcessLookupError):pass
 time.sleep(.25)
if not identity or state.get('MainPID')!=str(identity['pid']) or state.get('FragmentPath')!=str(U):raise RuntimeError('child_process_after_scope_restart_identity_failed')
text=U.read_text()
if 'ProtectSystem=strict\n' not in text or 'ReadWritePaths='+str(D)+' /etc/systemd/system\n' not in text:raise RuntimeError('child_unit_policy_after_restart_failed')
# Preserve all previous unit metadata while refreshing only this owned child identity.
child={**CH,'file_sha256':sha(U),'pid':identity['pid'],'starttime':identity['starttime'],'uid':identity['uid'],'exe_path':identity['exe'],'exe_sha256':identity['exe_sha256'],'cgroup':identity['cgroup']}
m['units']['child']=child;tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'wb') as f:f.write(json.dumps(m,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,M);newraw=M.read_bytes()
# The new process should recover the exact prior ROLLING_BACK record using absent sentinels.
DB=D/'agent-data'/'state.db';end=time.monotonic()+20;phase=None
while time.monotonic()<end:
 con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2);row=con.execute('SELECT phase,backup_path,backup_config_path,backup_unit_path FROM deployments WHERE application_id=? ORDER BY started_at DESC LIMIT 1',(C['app_id'],)).fetchone();con.close()
 if row:phase=row; 
 if row and row[0]=='DONE':break
 time.sleep(.5)
if not phase or phase[0]!='DONE' or phase[1:4]!=('absent:','absent:','absent:'):raise RuntimeError('prior_deployment_journal_recovery_not_confirmed')
appunit=Path('/etc/systemd/system')/C['app_unit']
if appunit.exists() or appunit.is_symlink():raise RuntimeError('failed_app_unit_not_clean_before_retry')
print(json.dumps({'status':'PASS','unit':C['unit'],'child_unit_sha256':sha(U),'protect_system':'strict','read_write_paths':[str(D),'/etc/systemd/system'],'child':child,'manifest_sha256':hashlib.sha256(newraw).hexdigest(),'recovered_prior_deployment_phase':phase[0],'prior_backup_sentinels':list(phase[1:4]),'app_unit_absent':True,'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(CLAIMS,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'adjust-owned-child-systemd-scope')
# Check original four Nodes before the remote stop/restart.
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id'])
 if n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('original_repair_health_precheck_failed')
node=api.get_json('/api/nodes/'+NODE_ID)
if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('child_health_precheck_failed')
task=api.create_task('qa-resume-20261010-host-tls-adjust-child-scope-1358f9a990bd',command,NODE)
try:result=api.run_task(task,timeout=90)
finally:cleanup=api.cleanup_task(task['id'],NODE)
if result.get('status')!='SUCCESS':raise RuntimeError('approved_child_scope_adjust_task_failed')
out=json.loads(result.get('stdout') or '')
if out.get('status')!='PASS' or out.get('protect_system')!='strict' or out.get('read_write_paths')!=[ROOT,'/etc/systemd/system']:raise RuntimeError('child_scope_adjust_assertion_failed')
end=time.monotonic()+40;node=None
while time.monotonic()<end:
 node=api.get_json('/api/nodes/'+NODE_ID)
 if node.get('status')=='online' and node.get('sync_status')=='synced':break
 time.sleep(.5)
if not node or node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('child_health_after_scope_adjust_failed')
peers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
if not all(x['status']=='online' and x['sync_status']=='synced' for x in peers):raise RuntimeError('original_repair_health_postcheck_failed')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('child-systemd-scope-adjusted.json',{'status':'PASS','actual_at':stamp,'remote':out,'child_node':{k:node.get(k) for k in ('id','status','sync_status','agent_version')},'original_four_nodes':peers,'task_id':task['id'],'execution_id':result['id'],'cleanup':cleanup})
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'approved-child-systemd-scope-adjusted-and-recovered','current_operation':'Root-approved exact child service ReadWritePaths was updated; ProtectSystem=strict retained; exact Unit restarted with live PID guards and dynamic owner manifest refreshed. Agent startup recovered prior ROLLING_BACK row to DONE from absent sentinels; app Unit remains absent.','next_action':'Retry normal App deploy exactly once, then verify actual binary/config, unique app Unit, service health, API state/history and UI feedback; cache must remain from prior HTTPS prefetch and no new TLS server GET is expected for ArtifactID-derived Hub URL.','current_manifest_sha256':out['manifest_sha256'],'child_systemd_scope':{'unit':UNIT,'unit_sha256':out['child_unit_sha256'],'protect_system':'strict','read_write_paths':[ROOT,'/etc/systemd/system']},'prior_failed_deployment_recovery':{'phase':out['recovered_prior_deployment_phase'],'backup_sentinels':out['prior_backup_sentinels']},'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['child-systemd-scope-adjusted.json','task-cleanup-'+task['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'child_online_synced':True,'original_four_online_synced':True,'protect_system':'strict','prior_journal_recovered_to':'DONE','manifest_sha256':out['manifest_sha256'],'task_get404':cleanup['get_after_delete_http']==404}))
