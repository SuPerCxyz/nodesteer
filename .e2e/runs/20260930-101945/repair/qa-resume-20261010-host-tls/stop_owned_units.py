#!/usr/bin/env python3
"""Stop and remove only the exact child/server Units after all app/API resources are gone."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());APP=json.loads((HERE/'application-created.json').read_text());CH=json.loads((HERE/'approved-scope-recovery-verified.json').read_text())['remote']['child_process'];PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text());NODE='611c02a5-1859-4465-878b-d543b7f6fdec';CHILD_NODE=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];ROOT=LIVE['remote_owner']['root'];CHUNIT='nodesteer-qa-tls-1358f9a990bd-child.service';SERVER=LIVE['remote_owner']['server'];SERVER_UNIT=SERVER['unit'];
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'server':SERVER,'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'child':CH,'child_unit':CHUNIT,'app_unit':APP['unit_name'],'child_node_id':CHILD_NODE}
src=r'''import hashlib,json,os,socket,stat,subprocess,time,uuid
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc',str(pid));s=(p/'stat').read_text();start=int(s[s.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip();return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(exe),'argv':argv,'cgroup':cg}
def props(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for l in r.stdout.splitlines():
  if '=' in l:k,v=l.split('=',1);d[k]=v
 return r,d
def wait_stopped(name):
 end=time.monotonic()+18;state={}
 while time.monotonic()<end:
  r,state=props(name)
  if r.returncode==0 and state.get('ActiveState')=='inactive' and state.get('MainPID')=='0':return state
  time.sleep(.25)
 raise RuntimeError('unit_stop_timeout')
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha'] or m.get('nonce')!='1358f9a990bd':raise RuntimeError('owned_root_manifest_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_guard_failed')
# Keep the original Native2 repair Agent available for this exact command.
P=C['parent'];p=proc(P['pid'])
if p['starttime']!=P['starttime'] or p['uid']!=P['uid'] or p['cgroup']!=P['cgroup'] or p['exe_sha256']!=C['agent_sha'] or p['exe']!=str(AGENT):raise RuntimeError('native2_parent_agent_changed')
# Check exact current server/child generations from the live manifest before stop.
CH=m['units']['child'];S=m['units']['https_server'];expected_child=C['child'];expected_server=C['server']
if CH.get('pid')!=expected_child['pid'] or CH.get('starttime')!=expected_child['starttime'] or CH.get('file_sha256')!=sha(Path('/etc/systemd/system')/C['child_unit']):raise RuntimeError('child_manifest_owner_changed')
if S.get('pid')!=expected_server['pid'] or S.get('starttime')!=expected_server['starttime'] or S.get('exe_sha256')!=expected_server['exe_sha256']:raise RuntimeError('server_manifest_owner_changed')
child_unit=Path('/etc/systemd/system')/C['child_unit'];server_unit=Path('/etc/systemd/system')/expected_server['unit'];app_unit=Path('/etc/systemd/system')/C['app_unit'];app_link=Path('/etc/systemd/system/multi-user.target.wants')/C['app_unit']
if app_unit.exists() or app_unit.is_symlink() or app_link.exists() or app_link.is_symlink() or (D/'app'/'bin'/'qa-app').exists() or (D/'app'/'config'/'qa.env').exists():raise RuntimeError('owned_app_resource_still_present')
for path,unit,sha_expected in ((child_unit,C['child_unit'],CH['file_sha256']),(server_unit,expected_server['unit'],C['server_unit_sha'])):
 if path.is_symlink() or not path.is_file() or path.resolve()!=path or path.stat().st_uid!=0 or stat.S_IMODE(path.stat().st_mode)!=0o644 or sha(path)!=sha_expected:raise RuntimeError('owned_service_unit_file_identity_failed')
 # A wants link can be absent (these Units were not enabled). If present, it must point to this exact Unit.
 link=Path('/etc/systemd/system/multi-user.target.wants')/unit
 if link.exists() or link.is_symlink():
  if not link.is_symlink() or link.resolve()!=path:raise RuntimeError('owned_unit_enable_link_identity_failed')
 r,d=props(unit)
 if r.returncode or d.get('ActiveState')!='active' or d.get('FragmentPath')!=str(path):raise RuntimeError('owned_unit_active_state_guard_failed')
# Dynamic exact process and listener identity before stopping either owner.
cp=proc(CH['pid']);sp=proc(S['pid'])
if cp['starttime']!=CH['starttime'] or cp['uid']!=CH['uid'] or cp['exe_sha256']!=C['agent_sha'] or cp['exe']!=str(AGENT) or CH['argv_config'] not in cp['argv'] or cp['cgroup']!=CH['cgroup']:raise RuntimeError('child_dynamic_process_owner_failed')
if sp['starttime']!=S['starttime'] or sp['uid']!=S['uid'] or sp['exe_sha256']!=S['exe_sha256'] or sp['cgroup']!=S['cgroup'] or not any(x==str(D/'private'/'https_artifact_server.py') for x in sp['argv']):raise RuntimeError('server_dynamic_process_owner_failed')
iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{S['port']:04X}";socks=[]
for l in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=l.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
if socks!=S['socket_inodes']:raise RuntimeError('server_loopback_listener_owner_failed')
# Stop only the two exact approved owned Units and prove MainPID=0.
subprocess.run(['systemctl','stop',C['child_unit']],check=True,capture_output=True,timeout=25);child_stopped=wait_stopped(C['child_unit'])
subprocess.run(['systemctl','stop',expected_server['unit']],check=True,capture_output=True,timeout=25);server_stopped=wait_stopped(expected_server['unit'])
# Remove only files/symlinks whose owners, identities, and contents were just proven.
removed=[]
for path,unit,sha_expected in ((child_unit,C['child_unit'],CH['file_sha256']),(server_unit,expected_server['unit'],C['server_unit_sha'])):
 link=Path('/etc/systemd/system/multi-user.target.wants')/unit
 if link.is_symlink():
  if link.resolve()!=path:raise RuntimeError('unit_enable_link_target_changed_during_cleanup')
  link.unlink();removed.append(str(link))
 if path.is_symlink() or path.stat().st_uid!=0 or sha(path)!=sha_expected:raise RuntimeError('unit_file_changed_during_cleanup')
 path.unlink();removed.append(str(path))
subprocess.run(['systemctl','daemon-reload'],check=True,capture_output=True,timeout=20)
for u in (C['child_unit'],expected_server['unit']):
 r,d=props(u)
 if d.get('MainPID')!='0' or d.get('ActiveState')!='inactive' or d.get('LoadState') not in ('not-found','loaded'):raise RuntimeError('stopped_unit_postcleanup_state_invalid')
# Confirm no listener remains at the exact port.
left=[]
for l in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=l.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':left.append(f[9])
if left:raise RuntimeError('server_listener_remains_after_stop')
# Persist final stopped/removed metadata under the exact owned manifest.
m['units']['child'].update({'stopped':True,'unit_file_removed':True,'stopped_pid':cp['pid'],'stopped_starttime':cp['starttime'],'stopped_at':time.time()})
m['units']['https_server'].update({'stopped':True,'unit_file_removed':True,'stopped_pid':sp['pid'],'stopped_starttime':sp['starttime'],'stopped_at':time.time()})
tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'wb') as f:f.write(json.dumps(m,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,M);newraw=M.read_bytes()
print(json.dumps({'status':'PASS','root_dev':st.st_dev,'root_ino':st.st_ino,'manifest_sha256':hashlib.sha256(newraw).hexdigest(),'stopped_units':[{'name':C['child_unit'],'mainpid_before':cp['pid'],'starttime_before':cp['starttime'],'post_stop':child_stopped},{'name':expected_server['unit'],'mainpid_before':sp['pid'],'starttime_before':sp['starttime'],'post_stop':server_stopped}],'removed_paths':removed,'app_unit_absent':True,'listener_absent':True,'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
cmd="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(cmd,'stop-owned-units')
# External API authority and health checks are read-only before exact stop.
if api.request('GET','/api/applications/'+APP['id'])[0]!=404 or api.request('GET','/api/artifacts/'+json.loads((HERE/'artifact-upload-app-update.json').read_text())['artifact']['id'])[0]!=404:raise RuntimeError('owned_app_or_artifact_not_deleted')
child=api.get_json('/api/nodes/'+CHILD_NODE)
if child.get('id')!=CHILD_NODE or child.get('hostname')!=json.loads((HERE/'child-enrollment.json').read_text())['node']['hostname'] or child.get('ip')!=json.loads((HERE/'child-enrollment.json').read_text())['node']['ip'] or child.get('status')!='online' or child.get('sync_status')!='synced':raise RuntimeError('owned_child_api_identity_health_failed_before_stop')
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id']);
 if n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('original_four_health_failed_before_cleanup')
 peers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
t=api.create_task('qa-resume-20261010-host-tls-stop-owned-units-1358f9a990bd',cmd,NODE)
try:r=api.run_task(t,timeout=100)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('owned_units_stop_task_failed')
out=json.loads(r.get('stdout') or '')
if out.get('status')!='PASS' or not out.get('listener_absent') or not out.get('app_unit_absent'):raise RuntimeError('owned_units_stop_output_invalid')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('owned-units-stopped.json',{'status':'PASS','actual_at':stamp,'remote':out,'original_four_nodes':peers,'child_id':CHILD_NODE,'child_state_before_stop':{k:child.get(k) for k in ('status','sync_status')},'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'owned-child-server-stopped-units-removed','current_operation':'After App/Artifact deletion and live owner guards, stopped and removed only the owned child and HTTPS server Units; exact loopback listener gone; manifest records stop generations; child Node is now expected offline. Original four remained healthy.','next_action':'Delete only the enrolled child Hub Node after checking no app assignment/static group/active transfer, then exact root deletion using updated manifest SHA/dev/ino/marker/all-tree ownership checks.','current_manifest_sha256':out['manifest_sha256'],'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['owned-units-stopped.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'stopped_units':[x['name'] for x in out['stopped_units']],'listener_absent':True,'child_was_online_synced':True,'original_four_online_synced':True,'task_get404':c['get_after_delete_http']==404,'manifest_sha256':out['manifest_sha256']}))
