#!/usr/bin/env python3
"""Wait read-only for the owned App tombstone to prune its exact Unit/files locally."""
import json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());CH=json.loads((HERE/'approved-scope-recovery-verified.json').read_text())['remote']['child_process'];child=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];app=json.loads((HERE/'application-created.json').read_text());ROOT=LIVE['remote_owner']['root'];NODE='611c02a5-1859-4465-878b-d543b7f6fdec';UNIT='nodesteer-qa-tls-1358f9a990bd-child.service';APPUNIT=app['unit_name'];C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'child':CH,'child_unit':UNIT,'app_id':app['id'],'app_unit':APPUNIT,'app_binary':ROOT+'/app/bin/qa-app','app_config':ROOT+'/app/config/qa.env'}
src=r'''import hashlib,json,os,sqlite3,stat,subprocess,time
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';CHU=Path('/etc/systemd/system')/C['child_unit'];APU=Path('/etc/systemd/system')/C['app_unit'];WANT=Path('/etc/systemd/system/multi-user.target.wants')/C['app_unit'];BIN=Path(C['app_binary']);CFG=Path(C['app_config']);DB=D/'agent-data'/'state.db'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def props(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for l in r.stdout.splitlines():
  if '=' in l:k,v=l.split('=',1);d[k]=v
 return r,d
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('root_manifest_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_guard_failed')
CH=C['child'];r,d=props(C['child_unit']);pid=int(d.get('MainPID','0') or 0)
if r.returncode or d.get('ActiveState')!='active' or pid!=CH['pid'] or d.get('FragmentPath')!=str(CHU):raise RuntimeError('child_unit_owner_changed')
p=Path('/proc',str(pid));s=(p/'stat').read_text();start=int(s[s.rfind(')')+2:].split()[19]);exe=os.readlink(str(p/'exe'))
if start!=CH['starttime'] or sha(exe)!=CH['exe_sha256'] or (p/'cgroup').read_text().strip()!=CH['cgroup']:raise RuntimeError('child_process_owner_changed')
end=time.monotonic()+30;rows=None;status={}
while time.monotonic()<end:
 r,status=props(C['app_unit']);
 con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2);rows=con.execute('SELECT COUNT(*) FROM applications WHERE id=?',(C['app_id'],)).fetchone()[0];registry=con.execute('SELECT COUNT(*) FROM unit_registry WHERE application_id=?',(C['app_id'],)).fetchone()[0];con.close()
 if not APU.exists() and not APU.is_symlink() and not BIN.exists() and not CFG.exists() and rows==0 and registry==0:break
 time.sleep(.5)
else:raise RuntimeError('owned_app_prune_not_completed')
if status.get('LoadState') not in ('not-found','loaded'):raise RuntimeError('app_unit_loadstate_invalid')
print(json.dumps({'status':'PASS','app_unit_file_absent':not APU.exists(),'app_unit_symlink_absent':not APU.is_symlink(),'enable_symlink_absent':not WANT.exists() and not WANT.is_symlink(),'app_unit_loadstate':status.get('LoadState'),'app_binary_absent':not BIN.exists(),'app_config_absent':not CFG.exists(),'local_application_rows':rows,'local_unit_registry_rows':registry,'child_pid':pid,'child_starttime':start,'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
cmd="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(cmd,'verify-app-cleanup')
t=api.create_task('qa-resume-20261010-host-tls-verify-app-cleanup-1358f9a990bd',cmd,NODE)
try:r=api.run_task(t,timeout=75)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('owned_app_cleanup_probe_failed')
out=json.loads(r.get('stdout') or '');n=api.get_json('/api/nodes/'+child);st,timebase=api.request('GET','/api/applications/'+C['app_id']);stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('app-prune-verified.json',{'status':'PASS','actual_at':stamp,'remote':out,'child':{k:n.get(k) for k in ('id','status','sync_status')},'api_app_get_http':st,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
if st!=404 or n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('owned_app_prune_hub_assertion_failed')
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'owned-app-tombstone-cleanup-verified','current_operation':'App DELETE tombstone was applied on the owned child: exact managed Unit, enable link, binary and config are absent; local App definition and unit registry rows are gone; child stays online/synced.','next_action':'Delete only the now-unreferenced exact Hub Artifact, then guarded stop/remove the owned child and HTTPS server Units, delete only the enrolled child Node, remove exact remote owned root, and verify original four nodes.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['app-prune-verified.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'app_unit_absent':out['app_unit_file_absent'] and out['app_unit_symlink_absent'] and out['enable_symlink_absent'],'binary_config_absent':out['app_binary_absent'] and out['app_config_absent'],'local_app_registry_absent':out['local_application_rows']==0 and out['local_unit_registry_rows']==0,'api_app_get404':st==404,'child_online_synced':True,'task_get404':c['get_after_delete_http']==404}))
