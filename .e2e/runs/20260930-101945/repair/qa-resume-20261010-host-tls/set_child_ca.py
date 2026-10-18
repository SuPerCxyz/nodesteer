#!/usr/bin/env python3
"""Guarded config change and restart of only the unique TLS QA child."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent
MODE=sys.argv[1] if len(sys.argv)>1 else 'none'
if MODE not in ('none','wrong','correct'):raise RuntimeError('invalid_ca_mode')
LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text())
current_files=sorted(HERE.glob('child-ca-*-restart.json'),key=lambda p:p.stat().st_mtime)
CURRENT=json.loads((current_files[-1] if current_files else HERE/'child-owner-recorded.json').read_text())['remote']['child']
NODE='611c02a5-1859-4465-878b-d543b7f6fdec';NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id']
ROOT=LIVE['remote_owner']['root'];NONCE='1358f9a990bd';UNIT='nodesteer-qa-tls-'+NONCE+'-child.service'
CA={'none':'','wrong':ROOT+'/private/wrong-ca.crt','correct':ROOT+'/private/ca.crt'}[MODE]
CLAIMS={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'parent':json.loads((HERE/'pre-child-owner-probe.json').read_text())['remote_agent']['process'],'agent_sha':json.loads((HERE/'pre-child-owner-probe.json').read_text())['remote_agent']['binary_sha256'],'child':CURRENT,'unit':UNIT,'node_id':NODE_ID,'config':ROOT+'/private/child.yaml','data':ROOT+'/agent-data','new_ca':CA,'mode':MODE}

source=r'''import hashlib,json,os,stat,subprocess,time,uuid
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';CFG=Path(C['config']);DATA=Path(C['data']);U=Path('/etc/systemd/system')/C['unit'];AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def unitprop(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for line in r.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);d[k]=v
 return r,d
def proc(pid):
 p=Path('/proc',str(pid));raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip();return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(exe),'argv':argv,'cgroup':cg}
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('owned_root_manifest_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_guard_failed')
# Native2 original repair binary process.
P=C['parent'];p=proc(P['pid'])
if p['starttime']!=P['starttime'] or p['uid']!=P['uid'] or p['cgroup']!=P['cgroup'] or p['exe_sha256']!=C['agent_sha'] or p['exe']!='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent':raise RuntimeError('native2_agent_owner_changed')
# Existing loopback TLS server exact owner remains up.
S=C['server'];p=proc(S['pid']);SU=Path('/etc/systemd/system')/S['unit'];sr,sd=unitprop(S['unit'])
if p['starttime']!=S['starttime'] or p['uid']!=S['uid'] or p['exe_sha256']!=S['exe_sha256'] or p['cgroup']!=S['cgroup'] or SU.is_symlink() or sha(SU)!=C['server_unit_sha'] or sr.returncode or sd.get('ActiveState')!='active' or sd.get('MainPID')!=str(S['pid']):raise RuntimeError('https_server_owner_changed')
import socket
iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{S['port']:04X}";socks=[]
for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
if socks!=S['listen_socket_inodes']:raise RuntimeError('https_server_loopback_socket_changed')
# Child unit/PID/config/data are unique and correspond to current manifest.
CH=C['child'];cr,cd=unitprop(C['unit']);
if cr.returncode or cd.get('LoadState')!='loaded' or cd.get('ActiveState')!='active' or cd.get('MainPID')!=str(CH['pid']) or cd.get('FragmentPath')!=str(U) or U.is_symlink() or sha(U)!=CH['file_sha256']:raise RuntimeError('child_unit_owner_changed')
p=proc(CH['pid'])
if p['starttime']!=CH['starttime'] or p['uid']!=CH['uid'] or p['exe_sha256']!=C['agent_sha'] or p['exe']!='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent' or str(CFG) not in p['argv'] or p['cgroup']!=CH['cgroup']:raise RuntimeError('child_process_owner_changed')
if CFG.is_symlink() or CFG.resolve()!=CFG or CFG.stat().st_uid!=0 or stat.S_IMODE(CFG.stat().st_mode)!=0o600 or sha(CFG)!=CH['config_sha256']:raise RuntimeError('child_config_owner_or_sha_changed')
if DATA.is_symlink() or DATA.resolve()!=DATA or (DATA.stat().st_dev,DATA.stat().st_ino)!=(CH['data_dir_dev'],CH['data_dir_ino']):raise RuntimeError('child_data_dir_identity_changed')
text=CFG.read_text()
if 'registration_token:' not in text:raise RuntimeError('child_registration_config_missing')
text='\n'.join(line for line in text.splitlines() if not line.startswith('tls_ca_file:'))+'\n'
if C['new_ca']:text+='tls_ca_file: '+json.dumps(C['new_ca'])+'\n'
tmp=CFG.with_name('child.yaml.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,CFG)
if CFG.stat().st_uid!=0 or stat.S_IMODE(CFG.stat().st_mode)!=0o600:raise RuntimeError('child_config_mode_after_update_failed')
r=subprocess.run(['systemctl','restart',C['unit']],text=True,capture_output=True,timeout=25)
if r.returncode:raise RuntimeError('owned_child_restart_failed:'+str(r.returncode))
end=time.monotonic()+25;identity=None;state={}
while time.monotonic()<end:
 x,state=unitprop(C['unit']);pid=int(state.get('MainPID','0') or 0)
 if x.returncode==0 and state.get('ActiveState')=='active' and pid>0:
  try:
   identity=proc(pid)
   if str(CFG) in identity['argv'] and identity['exe_sha256']==C['agent_sha']:break
  except (FileNotFoundError,ProcessLookupError):pass
 time.sleep(.25)
if not identity or state.get('MainPID')!=str(identity['pid']) or state.get('FragmentPath')!=str(U):raise RuntimeError('owned_child_after_restart_identity_failed')
if 'tls_ca_file:' in CFG.read_text():
  if C['new_ca'] not in CFG.read_text():raise RuntimeError('child_tls_ca_config_mismatch')
elif C['new_ca']:raise RuntimeError('child_tls_ca_config_missing')
# Refresh manifest with current generation identity, never the stale PID.
dst=DATA.stat();child={'name':C['unit'],'path':str(U),'file_sha256':sha(U),'pid':identity['pid'],'starttime':identity['starttime'],'uid':identity['uid'],'exe_path':identity['exe'],'exe_sha256':identity['exe_sha256'],'argv_config':str(CFG),'cgroup':identity['cgroup'],'config_sha256':sha(CFG),'tls_ca_file':C['new_ca'],'data_dir':str(DATA),'data_dir_dev':dst.st_dev,'data_dir_ino':dst.st_ino}
m['units']['child']=child;tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'wb') as f:f.write(json.dumps(m,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,M);new=M.read_bytes()
print(json.dumps({'status':'PASS','mode':C['mode'],'unit_state':state,'child':child,'manifest_sha256':hashlib.sha256(new).hexdigest(),'config_mode':oct(stat.S_IMODE(CFG.stat().st_mode)),'tls_ca_file':C['new_ca'],'data_dir_entries':len(list(DATA.iterdir())),'secret_value_emitted':False},separators=(',',':')))
'''%json.dumps(CLAIMS,separators=(',',':'))
command="python3 - <<'PY'\n"+source.rstrip()+"\nPY";api.validate_command(command,'set-child-ca-'+MODE)
task=api.create_task('qa-resume-20261010-host-tls-set-ca-'+MODE+'-1358f9a990bd',command,NODE)
try:result=api.run_task(task,timeout=75)
finally:cleanup=api.cleanup_task(task['id'],NODE)
if result.get('status')!='SUCCESS':raise RuntimeError('set_child_ca_task_failed')
out=json.loads(result.get('stdout') or '')
if out.get('status')!='PASS' or out.get('mode')!=MODE:raise RuntimeError('set_child_ca_result_invalid')
# Confirm only this child returned to online/synced before writing stage result.
end=time.monotonic()+40;node=None
while time.monotonic()<end:
 node=api.get_json('/api/nodes/'+NODE_ID)
 if node.get('status')=='online' and node.get('sync_status')=='synced':break
 time.sleep(.5)
if not node or node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('child_node_health_after_restart_failed')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
api.write_json('child-ca-'+MODE+'-restart.json',{'status':'PASS','actual_at':stamp,'mode':MODE,'remote':out,'node':{k:node.get(k) for k in ('id','status','sync_status','agent_version')},'task_id':task['id'],'execution_id':result['id'],'cleanup':cleanup})
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'child-ca-'+MODE+'-restarted-online-synced','current_operation':'Child CA configuration switched to '+MODE+' and only the exact owned child Unit restarted under live owner guard; manifest refreshed with new PID/start/config SHA and API reports online/synced.','current_manifest_sha256':out['manifest_sha256'],'next_action':'Create/assign the unique custom-HTTPS Application and verify the requested CA outcome with live server metrics, backend journal error, exact cache file, SQLite artifact row, and temp-file absence.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['child-ca-'+MODE+'-restart.json','task-cleanup-'+task['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'mode':MODE,'child_status':'online/synced','child_pid':out['child']['pid'],'child_starttime':out['child']['starttime'],'manifest_sha256':out['manifest_sha256'],'task_get404':cleanup['get_after_delete_http']==404}))
