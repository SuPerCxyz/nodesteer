#!/usr/bin/env python3
"""Read-only verification of the real owned Managed App deployment/cache hit."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent
LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());CH=json.loads((HERE/'approved-scope-recovery-verified.json').read_text())['remote']['child_process'];APP=json.loads((HERE/'application-created.json').read_text());ART=json.loads((HERE/'artifact-upload-app-update.json').read_text())['artifact'];DEP=json.loads((HERE/'application-deploy-retry-api.json').read_text());APP_ID=APP['id'];ART_ID=ART['id'];NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];NODE='611c02a5-1859-4465-878b-d543b7f6fdec';ROOT=LIVE['remote_owner']['root'];UNIT=APP['unit_name'];payload=(HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'application-payload.sh').read_bytes();PAYLOAD_SHA=hashlib.sha256(payload).hexdigest();appobj=api.get_json('/api/applications/'+APP_ID);config_sha=hashlib.sha256(appobj.get('config','').encode()).hexdigest();expected_hub_url=api.HUB+'/api/artifacts/'+ART_ID+'/download'
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'agent_sha':json.loads((HERE/'pre-child-owner-probe.json').read_text())['remote_agent']['binary_sha256'],'child':CH,'app_id':APP_ID,'artifact_id':ART_ID,'artifact_sha':PAYLOAD_SHA,'config_sha':config_sha,'unit':UNIT,'binary':ROOT+'/app/bin/qa-app','config':ROOT+'/app/config/qa.env','data':ROOT+'/agent-data','expected_hub_url':expected_hub_url,'app_exec_id':DEP['execution_id']}
src=r'''import hashlib,json,os,sqlite3,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';DB=D/'agent-data'/'state.db';CACHE=D/'agent-data'/'artifacts'/C['artifact_sha'];BIN=Path(C['binary']);CFG=Path(C['config']);UNIT=Path('/etc/systemd/system')/C['unit'];WANT=Path('/etc/systemd/system/multi-user.target.wants')/C['unit'];CHUNIT='nodesteer-qa-tls-1358f9a990bd-child.service';AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
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
st=D.lstat();raw=M.read_bytes();manifest=json.loads(raw);mh=hashlib.sha256(raw).hexdigest()
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or mh!=C['manifest_sha'] or manifest.get('nonce')!='1358f9a990bd':raise RuntimeError('owned_root_manifest_guard_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_guard_failed')
CH=manifest['units']['child'];r,cs=props(CHUNIT);cpid=int(cs.get('MainPID','0') or 0)
if r.returncode or cs.get('ActiveState')!='active' or cpid!=CH['pid'] or cpid!=C['child']['pid'] or cs.get('FragmentPath')!='/etc/systemd/system/'+CHUNIT:raise RuntimeError('child_unit_owner_changed')
p=Path('/proc',str(cpid));rawstat=(p/'stat').read_text();cstart=int(rawstat[rawstat.rfind(')')+2:].split()[19]);cexe=os.readlink(str(p/'exe'));cuid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);ccg=(p/'cgroup').read_text().strip()
if cstart!=CH['starttime'] or cuid!=0 or sha(cexe)!=C['agent_sha'] or ccg!=CH['cgroup'] or CHUNIT not in ccg:raise RuntimeError('child_process_dynamic_owner_failed')
if 'ProtectSystem=strict\n' not in (Path('/etc/systemd/system')/CHUNIT).read_text() or 'ReadWritePaths='+str(D)+' /etc/systemd/system\n' not in (Path('/etc/systemd/system')/CHUNIT).read_text():raise RuntimeError('child_sandbox_policy_changed')
for key,path,wantmode in (('app_binary',BIN,0o755),('app_config',CFG,0o644)):
 if path.is_symlink() or not path.is_file() or path.resolve().parent!=D/'app'/( 'bin' if key=='app_binary' else 'config') or path.stat().st_uid!=0 or stat.S_IMODE(path.stat().st_mode)!=wantmode:raise RuntimeError(key+'_owner_mode_realpath_failed')
if sha(BIN)!=C['artifact_sha'] or sha(CFG)!=C['config_sha']:raise RuntimeError('deployed_app_file_sha_mismatch')
r,us=props(C['unit'])
if r.returncode or us.get('LoadState')!='loaded' or us.get('ActiveState')!='active' or us.get('FragmentPath')!=str(UNIT) or int(us.get('MainPID','0') or 0)<=0:raise RuntimeError('managed_app_unit_not_active')
if UNIT.is_symlink() or UNIT.stat().st_uid!=0 or stat.S_IMODE(UNIT.stat().st_mode)!=0o644:raise RuntimeError('managed_app_unit_owner_mode_failed')
unittext=UNIT.read_text()
if str(BIN) not in unittext or str(CFG) not in unittext:raise RuntimeError('managed_app_unit_definition_mismatch')
if not WANT.is_symlink() or WANT.resolve()!=UNIT.resolve():raise RuntimeError('managed_app_enable_symlink_mismatch')
enabled=subprocess.run(['systemctl','is-enabled',C['unit']],text=True,capture_output=True,timeout=10)
if enabled.returncode or enabled.stdout.strip()!='enabled':raise RuntimeError('managed_app_enable_state_failed')
apid=int(us['MainPID']);ap=Path('/proc',str(apid));asr=(ap/'stat').read_text();astart=int(asr[asr.rfind(')')+2:].split()[19]);aexe=os.readlink(str(ap/'exe'));aargv=[x.decode(errors='replace') for x in (ap/'cmdline').read_bytes().split(b'\0') if x];acg=(ap/'cgroup').read_text().strip()
if C['unit'] not in acg or not any(x==str(BIN) for x in aargv):raise RuntimeError('managed_app_process_unit_argv_failed')
con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2)
art_count=con.execute('SELECT COUNT(*) FROM artifacts WHERE sha256=?',(C['artifact_sha'],)).fetchone()[0]
app_row=con.execute('SELECT definition_json FROM applications WHERE id=?',(C['app_id'],)).fetchone()
journals=con.execute('SELECT phase,backup_path,backup_config_path,backup_unit_path FROM deployments WHERE application_id=? ORDER BY started_at',(C['app_id'],)).fetchall()
registry=con.execute('SELECT unit_name FROM unit_registry WHERE application_id=?',(C['app_id'],)).fetchall();con.close()
if art_count!=1 or not app_row or not journals or journals[-1][0]!='DONE' or journals[-1][1:4]!=('absent:','absent:','absent:') or [x[0] for x in registry]!=[C['unit']]:raise RuntimeError('managed_app_local_state_journal_registry_failed')
localapp=json.loads(app_row[0])
if localapp.get('artifact_id')!=C['artifact_id'] or localapp.get('artifact_sha256')!=C['artifact_sha'] or localapp.get('artifact_url')!=C['expected_hub_url']:raise RuntimeError('agent_synced_app_definition_mismatch')
if CACHE.is_symlink() or not CACHE.is_file() or sha(CACHE)!=C['artifact_sha']:raise RuntimeError('positive_cache_sha_failed')
tmp=Path(str(CACHE)+'.tmp')
statusp=D/'private'/'server-status.json';status=json.loads(statusp.read_text())
logp=D/'private'/'server-access.jsonl';rows=[json.loads(line) for line in logp.read_text().splitlines() if line.strip()]
if statusp.is_symlink() or status.get('pid')!=C['server']['pid'] or status.get('port')!=C['server']['port']:raise RuntimeError('owned_tls_server_identity_failed')
print(json.dumps({'status':'PASS','root_dev':st.st_dev,'root_ino':st.st_ino,'manifest_sha256':mh,'child':{'pid':cpid,'starttime':cstart,'unit_sha256':sha(Path('/etc/systemd/system')/CHUNIT),'protect_system':'strict','read_write_paths':[str(D),'/etc/systemd/system']},'app':{'unit':C['unit'],'unit_sha256':sha(UNIT),'state':us.get('ActiveState'),'main_pid':apid,'process_starttime':astart,'process_exe':aexe,'process_argv':aargv,'process_cgroup':acg,'enabled':enabled.stdout.strip(),'enable_symlink':str(WANT),'unit_owner_uid':UNIT.stat().st_uid,'unit_mode':oct(stat.S_IMODE(UNIT.stat().st_mode)),'binary_path':str(BIN),'binary_mode':oct(stat.S_IMODE(BIN.stat().st_mode)),'binary_sha256':sha(BIN),'config_path':str(CFG),'config_mode':oct(stat.S_IMODE(CFG.stat().st_mode)),'config_sha256':sha(CFG),'local_synced_artifact_url':localapp['artifact_url'],'local_synced_artifact_id':localapp['artifact_id'],'local_synced_artifact_sha256':localapp['artifact_sha256'],'registry_units':[x[0] for x in registry],'deployment_journals':[{'phase':x[0],'backup_sentinels':list(x[1:4])} for x in journals]},'cache':{'path':str(CACHE),'sha256':sha(CACHE),'sqlite_rows':art_count,'tmp_exists':tmp.exists()},'tls_server':{k:status.get(k) for k in ('http_requests','tls_handshake_failures','port','bind')},'tls_server_access_rows':rows,'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'verify-managed-app')
# API assertions against independent Hub state and original repair Agent health before remote read-only verification.
node=api.get_json('/api/nodes/'+NODE_ID);apps=api.get_json('/api/applications/'+APP_ID);assigned=api.get_json('/api/applications/'+APP_ID+'/nodes')
if node.get('status')!='online' or node.get('sync_status')!='synced' or assigned!=[NODE_ID] or apps.get('artifact_id')!=ART_ID:raise RuntimeError('hub_child_app_state_before_verify_failed')
t=api.create_task('qa-resume-20261010-host-tls-verify-managed-app-1358f9a990bd',command,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('managed_app_verify_task_failed')
out=json.loads(r.get('stdout') or '')
state=api.get_json('/api/applications/'+APP_ID+'/state');history=api.get_json('/api/applications/'+APP_ID+'/executions');execution=api.get_json('/api/executions/'+DEP['execution_id'])
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
record={'status':'PASS','actual_at':stamp,'remote':out,'hub_child':{k:node.get(k) for k in ('id','status','sync_status','agent_version')},'hub_app':{k:apps.get(k) for k in ('id','name','artifact_id','artifact_url','artifact_sha256','revision','unit_name')},'assigned_node_ids':assigned,'app_state':state,'execution':{k:execution.get(k) for k in ('id','status','block_reason','start_time','end_time')},'application_execution_history':history,'original_four_nodes':peers,'task_id':t['id'],'execution_id':r['id'],'task_cleanup':c}
api.write_json('managed-app-verified.json',record)
if out.get('status')!='PASS' or not state or state[0].get('health')!='healthy' or execution.get('status')!='SUCCESS' or not all(x['status']=='online' and x['sync_status']=='synced' for x in peers):raise RuntimeError('managed_app_end_to_end_assertion_failed')
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'managed-app-real-deploy-healthy-verified','current_operation':'One authorized retry deployed the App successfully from the exact prepopulated same-SHA cache; target files, config, unique enabled service Unit/process/health, local synced derived Hub URL, SQLite artifact row, deployment journal, unit registry, API state/history and original four Node health independently verified. No direct HTTPS deploy-fetch is claimed; the TLS HTTPS GET occurred in prior custom-URL prefetch and later deployment URL was Hub-derived HTTP/cache hit.','next_action':'Use agent-browser on the existing authorized Web UI session to verify App execution/state and real backend feedback; then exact cleanup of only owned app/artifact/child/server/root and final original four health.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['managed-app-verified.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'execution_status':execution.get('status'),'app_health':state[0].get('health'),'app_unit':out['app']['unit'],'app_unit_state':out['app']['state'],'cache_preserved':out['cache']['sqlite_rows']==1 and not out['cache']['tmp_exists'],'https_get_count':out['tls_server']['http_requests'],'original_four_online_synced':True,'task_get404':c['get_after_delete_http']==404}))
