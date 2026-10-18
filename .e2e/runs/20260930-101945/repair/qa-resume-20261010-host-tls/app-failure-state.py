#!/usr/bin/env python3
"""Read-only audit of the exact failed owned App deployment and rollback result."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;DEP=json.loads((HERE/'application-deploy-api.json').read_text());LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());CUR=json.loads((sorted(HERE.glob('child-ca-*-restart.json'),key=lambda p:p.stat().st_mtime)[-1]) .read_text())['remote']['child'];A=DEP['application_id'];ROOT=LIVE['remote_owner']['root'];UNIT='nodesteer-qa-tls-1358f9a990bd-app.service';NODE='611c02a5-1859-4465-878b-d543b7f6fdec';NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];EXEC=DEP['execution_id']
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'child':CUR,'unit':'nodesteer-qa-tls-1358f9a990bd-child.service','app_id':A,'app_unit':UNIT,'app_binary':ROOT+'/app/bin/qa-app','app_config':ROOT+'/app/config/qa.env'}
src=r'''import hashlib,json,os,sqlite3,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';DB=D/'agent-data'/'state.db';U=Path('/etc/systemd/system')/C['unit'];APPU=Path('/etc/systemd/system')/C['app_unit']
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
st=D.lstat();raw=M.read_bytes()
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('owned_root_manifest_guard_failed')
r,d=props(C['unit']);pid=int(d.get('MainPID','0') or 0);CH=C['child']
if r.returncode or d.get('ActiveState')!='active' or pid!=CH['pid'] or d.get('FragmentPath')!=str(U):raise RuntimeError('child_unit_owner_changed')
files=[]
for key in ('app_binary','app_config'):
 p=Path(C[key]);x={'path':str(p),'exists':p.exists(),'symlink':p.is_symlink()}
 if p.exists() and not p.is_symlink():x.update({'uid':p.stat().st_uid,'mode':oct(stat.S_IMODE(p.stat().st_mode)),'sha256':sha(p),'realpath':str(p.resolve())})
 files.append(x)
r,ad=props(C['app_unit']);appunit={'state':ad,'path_exists':APPU.exists(),'symlink':APPU.is_symlink()}
if APPU.exists() and not APPU.is_symlink():appunit.update({'uid':APPU.stat().st_uid,'mode':oct(stat.S_IMODE(APPU.stat().st_mode)),'sha256':sha(APPU)})
con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2)
rows=con.execute('SELECT id,application_id,from_version,to_version,phase,backup_path,backup_config_path,backup_unit_path,started_at,updated_at FROM deployments WHERE application_id=?',(C['app_id'],)).fetchall()
registry=con.execute('SELECT unit_name FROM unit_registry WHERE application_id=?',(C['app_id'],)).fetchall();con.close()
print(json.dumps({'status':'PASS','child_unit':{'active':d.get('ActiveState'),'pid':pid},'app_files':files,'app_unit':appunit,'deployment_journal':[{'id':x[0],'application_id':x[1],'from_version':x[2],'to_version':x[3],'phase':x[4],'backup_path':x[5],'backup_config_path':x[6],'backup_unit_path':x[7],'started_at':x[8],'updated_at':x[9]} for x in rows],'unit_registry':[x[0] for x in registry],'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'app-failure-state')
t=api.create_task('qa-resume-20261010-host-tls-app-failure-state-1358f9a990bd',command,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('app_failure_state_probe_failed')
out=json.loads(r.get('stdout') or '')
node=api.get_json('/api/nodes/'+NODE_ID);pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 q=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('app-failure-state.json',{'status':'PASS','actual_at':stamp,'remote':out,'child':{k:node.get(k) for k in ('id','status','sync_status')},'original_four_nodes':peers,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'app-deploy-environment-blocked-reviewed','current_operation':'Real App deploy failed because the owned child service sandbox makes /etc/systemd/system read-only; API recorded FAILED/unhealthy and rollback failed. Read-only probe captured exact Unit/file/Journal state, while child and original four Agent health remain unchanged. No deploy retry or other-unit operation.','next_action':'Await Root review of the exact test-child sandbox exception required for host-managed unit creation; then choose a narrowly guarded recovery path.','deploy_failure_block_reason':DEP['execution']['block_reason'],'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['app-failure-state.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'child_online_synced':node.get('status')=='online' and node.get('sync_status')=='synced','original_four_online_synced':all(x['status']=='online' and x['sync_status']=='synced' for x in peers),'app_unit_exists':out['app_unit']['path_exists'],'deployment_journal_rows':len(out['deployment_journal']),'task_get404':c['get_after_delete_http']==404}))
