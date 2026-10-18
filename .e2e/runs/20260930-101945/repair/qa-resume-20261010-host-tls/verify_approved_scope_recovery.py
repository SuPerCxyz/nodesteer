#!/usr/bin/env python3
"""Read-only verify the authorized child scope update after a post-restart harness error."""
import json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text());NODE='611c02a5-1859-4465-878b-d543b7f6fdec';NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];APP_ID=json.loads((HERE/'application-created.json').read_text())['id'];ROOT=LIVE['remote_owner']['root'];UNIT='nodesteer-qa-tls-1358f9a990bd-child.service';APPUNIT='nodesteer-qa-tls-1358f9a990bd-app.service'
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'unit':UNIT,'app_unit':APPUNIT,'app_id':APP_ID}
src=r'''import hashlib,json,os,sqlite3,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';U=Path('/etc/systemd/system')/C['unit'];AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
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
st=D.lstat();raw=M.read_bytes();m=json.loads(raw);mh=hashlib.sha256(raw).hexdigest()
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or m.get('nonce')!='1358f9a990bd' or m.get('root_dev')!=C['root_dev'] or m.get('root_ino')!=C['root_ino']:raise RuntimeError('owned_root_identity_changed')
if MARK.is_symlink() or MARK.stat().st_uid!=0 or sha(MARK)!=C['marker_sha']:raise RuntimeError('owned_marker_identity_changed')
P=C['parent'];p=Path('/proc',str(P['pid']));s=(p/'stat').read_text();start=int(s[s.rfind(')')+2:].split()[19]);exe=os.readlink(str(p/'exe'))
if start!=P['starttime'] or sha(exe)!=C['agent_sha'] or exe!=str(AGENT):raise RuntimeError('native2_agent_changed')
S=C['server'];p=Path('/proc',str(S['pid']));s=(p/'stat').read_text();ss=int(s[s.rfind(')')+2:].split()[19]);exe=os.readlink(str(p/'exe'));SU=Path('/etc/systemd/system')/S['unit'];sr,sd=prop(S['unit'])
if ss!=S['starttime'] or sha(exe)!=S['exe_sha256'] or SU.is_symlink() or sha(SU)!=C['server_unit_sha'] or sr.returncode or sd.get('ActiveState')!='active' or sd.get('MainPID')!=str(S['pid']):raise RuntimeError('https_server_changed')
CH=m.get('units',{}).get('child',{});r,d=prop(C['unit']);pid=int(d.get('MainPID','0') or 0)
if r.returncode or d.get('LoadState')!='loaded' or d.get('ActiveState')!='active' or d.get('FragmentPath')!=str(U) or pid<=0:raise RuntimeError('child_unit_not_active')
text=U.read_text()
if U.is_symlink() or sha(U)!=CH.get('file_sha256') or U.stat().st_uid!=0 or stat.S_IMODE(U.stat().st_mode)!=0o644 or 'ProtectSystem=strict\n' not in text or 'ReadWritePaths='+str(D)+' /etc/systemd/system\n' not in text:raise RuntimeError('approved_child_unit_policy_not_present')
p=Path('/proc',str(pid));s=(p/'stat').read_text();ps=int(s[s.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip()
if pid!=CH.get('pid') or ps!=CH.get('starttime') or uid!=CH.get('uid') or sha(exe)!=C['agent_sha'] or not any(x==str(D/'private'/'child.yaml') for x in argv) or cg!=CH.get('cgroup'):raise RuntimeError('child_process_not_manifest_owned')
appU=Path('/etc/systemd/system')/C['app_unit'];ar,ad=prop(C['app_unit']);DB=D/'agent-data'/'state.db';con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2);rows=con.execute('SELECT phase,backup_path,backup_config_path,backup_unit_path FROM deployments WHERE application_id=? ORDER BY started_at',(C['app_id'],)).fetchall();cache=D/'agent-data'/'artifacts'/'472512a4e39ac47d64123faae1ae9de0df0fdc646de687b88fc1912eb86594f4';cache_rows=con.execute('SELECT COUNT(*) FROM artifacts WHERE sha256=?',('472512a4e39ac47d64123faae1ae9de0df0fdc646de687b88fc1912eb86594f4',)).fetchone()[0];con.close()
if not rows or rows[-1][0]!='DONE' or rows[-1][1:4]!=('absent:','absent:','absent:'):raise RuntimeError('old_rollback_journal_not_done')
print(json.dumps({'status':'PASS','manifest_sha256':mh,'root_dev':st.st_dev,'root_ino':st.st_ino,'child_unit_sha256':sha(U),'child_process':{'pid':pid,'starttime':ps,'uid':uid,'exe_sha256':sha(exe),'argv_config':str(D/'private'/'child.yaml'),'cgroup':cg},'protect_system':'strict','read_write_paths':[str(D),'/etc/systemd/system'],'old_deployment_rows':rows,'app_unit':{'path_exists':appU.exists(),'systemd':ad},'positive_cache':{'file_exists':cache.exists(),'sha256':sha(cache) if cache.exists() else None,'sqlite_rows':cache_rows},'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'verify-approved-scope-recovery')
t=api.create_task('qa-resume-20261010-host-tls-verify-scope-recovery-1358f9a990bd',command,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('scope_recovery_probe_failed')
out=json.loads(r.get('stdout') or '')
node=api.get_json('/api/nodes/'+NODE_ID);pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('approved-scope-recovery-verified.json',{'status':'PASS','actual_at':stamp,'remote':out,'child_node':{k:node.get(k) for k in ('id','status','sync_status','agent_version')},'original_four_nodes':peers,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
if node.get('status')!='online' or node.get('sync_status')!='synced' or not all(x['status']=='online' and x['sync_status']=='synced' for x in peers):raise RuntimeError('post_restart_node_health_failed')
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'approved-child-scope-updated-recovery-verified','current_operation':'Read-only probe confirms approved strict sandbox policy, dynamic child process/manifest ownership and old exact ROLLING_BACK row recovered to DONE with absent sentinels; positive HTTPS cache remains intact; App Unit absent.','next_action':'Retry exact App deployment once through API; the Hub artifact sync URL uses HTTP and same-SHA cache hit, with no new TLS server GET expected.','current_manifest_sha256':out['manifest_sha256'],'child_systemd_scope':{'unit':UNIT,'unit_sha256':out['child_unit_sha256'],'protect_system':'strict','read_write_paths':[ROOT,'/etc/systemd/system']},'prior_failed_deployment_recovery':{'phase':out['old_deployment_rows'][-1][0],'backup_sentinels':out['old_deployment_rows'][-1][1:4]},'pending_scope_review':None,'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['approved-scope-recovery-verified.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'child_online_synced':True,'original_four_online_synced':True,'manifest_sha256':out['manifest_sha256'],'previous_journal_phase':out['old_deployment_rows'][-1][0],'app_unit_absent':not out['app_unit']['path_exists'],'cache_preserved':out['positive_cache']['file_exists'],'task_get404':c['get_after_delete_http']==404}))
