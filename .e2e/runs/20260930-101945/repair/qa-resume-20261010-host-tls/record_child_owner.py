#!/usr/bin/env python3
"""Adopt the dynamically verified process left by the first authorized start Task."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent
PART=json.loads((HERE/'child-partial-state-probe.json').read_text())
LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text())
OLD=HERE.parent/'qa-resume-20261009-host-tls'/'tls'
PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text())
NODE='611c02a5-1859-4465-878b-d543b7f6fdec'
node_record=json.loads((HERE/'child-enrollment.json').read_text())['response']
node_id=node_record['node_id']
claims={'root':LIVE['remote_owner']['root'],'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':LIVE['remote_owner']['manifest_sha256'],'marker_sha':json.loads((OLD/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'server_unit_sha':json.loads((OLD/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'child':PART['remote']['child_process'],'child_unit':PART['remote']['child_unit'],'node_id':node_id,'unit':'nodesteer-qa-tls-1358f9a990bd-child.service','config':LIVE['remote_owner']['root']+'/private/child.yaml','data':LIVE['remote_owner']['root']+'/agent-data'}
src=r'''import hashlib,json,os,stat,subprocess,uuid
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';U=Path('/etc/systemd/system')/C['unit'];CFG=Path(C['config']);DATA=Path(C['data']);AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def prop(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for line in r.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);d[k]=v
 return r,d
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('root_manifest_owner_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('root_marker_guard_failed')
P=C['parent'];p=Path('/proc',str(P['pid']));ps=(p/'stat').read_text();pst=int(ps[ps.rfind(')')+2:].split()[19]);pexe=os.readlink(str(p/'exe'))
if pst!=P['starttime'] or sha(pexe)!=C['agent_sha'] or pexe!=str(AGENT):raise RuntimeError('native2_agent_owner_changed')
S=C['server'];p=Path('/proc',str(S['pid']));ss=(p/'stat').read_text();sst=int(ss[ss.rfind(')')+2:].split()[19]);sexe=os.readlink(str(p/'exe'));scg=(p/'cgroup').read_text().strip();su=Path('/etc/systemd/system')/S['unit'];sr,sd=prop(S['unit'])
if sst!=S['starttime'] or sha(sexe)!=S['exe_sha256'] or scg!=S['cgroup'] or sha(su)!=C['server_unit_sha'] or sr.returncode or sd.get('ActiveState')!='active' or sd.get('MainPID')!=str(S['pid']):raise RuntimeError('server_owner_changed')
CH=C['child'];r,d=prop(C['unit']);Utext=U.read_text();pid=int(d.get('MainPID','0') or 0)
if r.returncode or d.get('LoadState')!='loaded' or d.get('ActiveState')!='active' or pid!=CH['main_pid'] or d.get('FragmentPath')!=str(U) or U.is_symlink() or sha(U)!=C['child_unit']['file_sha256'] or Utext.count(str(CFG))!=1:raise RuntimeError('child_unit_identity_changed')
p=Path('/proc',str(pid));rawstat=(p/'stat').read_text();start=int(rawstat[rawstat.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip()
if start!=CH['starttime'] or uid!=CH['uid'] or sha(exe)!=C['agent_sha'] or exe!=str(AGENT) or not any(x==str(CFG) for x in argv) or cg!=CH['cgroup'] or C['unit'] not in cg:raise RuntimeError('child_pid_start_uid_argv_exe_cgroup_guard_failed')
cst=CFG.lstat();text=CFG.read_text();dst=DATA.lstat()
if CFG.is_symlink() or CFG.resolve()!=CFG or (cst.st_uid,stat.S_IMODE(cst.st_mode))!=(0,0o600) or sha(CFG)!=CH['config_sha256'] or 'registration_token:' not in text or ('tls_ca_file: '+json.dumps(str(D/'private'/'ca.crt'))) not in text:raise RuntimeError('child_config_owner_or_ca_config_guard_failed')
if DATA.is_symlink() or DATA.resolve()!=DATA or (dst.st_dev,dst.st_ino)!=(CH['data_dir_dev'],CH['data_dir_ino']) or dst.st_uid!=0 or stat.S_IMODE(dst.st_mode)!=0o700:raise RuntimeError('child_data_dir_identity_changed')
m['units']['child']={'name':C['unit'],'path':str(U),'file_sha256':sha(U),'pid':pid,'starttime':start,'uid':uid,'exe_path':exe,'exe_sha256':sha(exe),'argv_config':str(CFG),'cgroup':cg,'config_sha256':sha(CFG),'tls_ca_file':str(D/'private'/'ca.crt'),'data_dir':str(DATA),'data_dir_dev':dst.st_dev,'data_dir_ino':dst.st_ino}
tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'wb') as f:f.write(json.dumps(m,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,M);new=M.read_bytes()
print(json.dumps({'status':'PASS','manifest_sha256':hashlib.sha256(new).hexdigest(),'root_dev':st.st_dev,'root_ino':st.st_ino,'child':m['units']['child'],'data_entries_count':len(list(DATA.iterdir())),'secret_values_emitted':False},separators=(',',':')))
'''%json.dumps(claims,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'record-child-owner')
t=api.create_task('qa-resume-20261010-host-tls-record-child-owner-1358f9a990bd',command,NODE)
try:result=api.run_task(t)
finally:cleanup=api.cleanup_task(t['id'],NODE)
if result.get('status')!='SUCCESS':raise RuntimeError('record_child_owner_task_failed')
out=json.loads(result.get('stdout') or '')
if out.get('status')!='PASS':raise RuntimeError('record_child_owner_result_invalid')
node=api.get_json('/api/nodes/'+node_id)
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 q=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
if node.get('status')!='online' or node.get('sync_status')!='synced' or not all(x['status']=='online' and x['sync_status']=='synced' for x in peers):raise RuntimeError('child_or_original_repair_health_failed')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
api.write_json('child-owner-recorded.json',{'status':'PASS','actual_at':stamp,'child_node':{k:node.get(k) for k in ('id','agent_id','hostname','ip','status','sync_status','agent_version','labels')},'original_four_nodes':peers,'remote':out,'task_id':t['id'],'execution_id':result['id'],'cleanup':cleanup})
progress=HERE/'progress.json';v=json.loads(progress.read_text());failed=json.loads((HERE/'execution-9a3b20f5-2d0c-436e-854d-f50001513408.json').read_text());v.update({'actual_at':stamp,'stage':'owned-child-online-synced-manifest-recorded','current_operation':'Started child service is dynamically verified online/synced, and its current PID/start/UID/argv/exe/cgroup/config/data identity is now in owner manifest. Initial Task FAILED at a transient process-owner check after start; subsequent live probe proved the child owner and no product defect is inferred.','next_action':'Run fresh uncached TLS prefetch failure with no configured CA, gather backend reason, cache/tmp/server handshake and Hub state; then wrong CA and correct recovery on same payload.','current_manifest_sha256':out['manifest_sha256'],'initial_start_task_failure':{'task_id':'9a3b20f5-2d0c-436e-854d-f50001513408','execution_id':'148c5663-db2e-413a-9628-553866ee0bd8','execution_status':failed['status'],'stderr_chars':failed['stderr_chars'],'classification':'harness transient child_process_owner_guard_failed; not a product failure'},'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['child-owner-recorded.json','execution-9a3b20f5-2d0c-436e-854d-f50001513408.json','task-cleanup-9a3b20f5-2d0c-436e-854d-f50001513408.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
secret=HERE/'private'/'registration-secret.py'
if secret.exists():secret.unlink()
print(json.dumps({'status':'PASS','actual_at':stamp,'child_status':'online/synced','manifest_sha256':out['manifest_sha256'],'original_four_online_synced':True,'task_get404':cleanup['get_after_delete_http']==404,'local_secret_file_removed':not secret.exists()}))
