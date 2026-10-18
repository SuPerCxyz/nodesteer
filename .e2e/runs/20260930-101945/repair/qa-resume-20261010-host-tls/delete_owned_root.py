#!/usr/bin/env python3
"""Delete only the fully verified owned TLS fixture root after all Units/processes are gone."""
import hashlib,json,sys,time,urllib.parse
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text());STOP=json.loads((HERE/'owned-units-stopped.json').read_text())['remote'];EN=json.loads((HERE/'child-enrollment.json').read_text());APP=json.loads((HERE/'application-created.json').read_text());ART=json.loads((HERE/'artifact-upload-app-update.json').read_text())['artifact'];ROOT=LIVE['remote_owner']['root'];NODE='611c02a5-1859-4465-878b-d543b7f6fdec';CHILD=EN['response']['node_id'];SERVER=LIVE['remote_owner']['server'];CHILDUNIT='nodesteer-qa-tls-1358f9a990bd-child.service';APPUNIT=APP['unit_name'];app_id=APP['id']
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':SERVER,'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'child_pid':CHILD and json.loads((HERE/'approved-scope-recovery-verified.json').read_text())['remote']['child_process']['pid'],'child_starttime':json.loads((HERE/'approved-scope-recovery-verified.json').read_text())['remote']['child_process']['starttime'],'child_unit':CHILDUNIT,'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'app_unit':APPUNIT,'app_id':app_id,'artifact_id':ART['id'],'child_node_id':CHILD}
src=r'''import hashlib,json,os,shutil,socket,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def prop(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for l in r.stdout.splitlines():
  if '=' in l:k,v=l.split('=',1);d[k]=v
 return r,d
def proc_start(pid):
 try:
  p=Path('/proc',str(pid));s=(p/'stat').read_text();start=int(s[s.rfind(')')+2:].split()[19]);return start
 except (FileNotFoundError,ProcessLookupError):return None
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or D.parent.resolve()!=Path('/opt').resolve() or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha'] or m.get('nonce')!='1358f9a990bd' or m.get('root')!=str(D) or m.get('root_dev')!=C['root_dev'] or m.get('root_ino')!=C['root_ino']:raise RuntimeError('owned_root_manifest_identity_failed')
if MARK.is_symlink() or MARK.stat().st_uid!=0 or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_identity_failed')
P=C['parent'];p=Path('/proc',str(P['pid']));s=(p/'stat').read_text();pstart=int(s[s.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'))
if pstart!=P['starttime'] or uid!=P['uid'] or exe!=str(AGENT) or sha(exe)!=C['agent_sha'] or (p/'cgroup').read_text().strip()!=P['cgroup']:raise RuntimeError('native2_parent_agent_owner_changed')
for key,name in (('child',C['child_unit']),('https_server',C['server']['unit'])):
 U=Path('/etc/systemd/system')/name;link=Path('/etc/systemd/system/multi-user.target.wants')/name
 if U.exists() or U.is_symlink() or link.exists() or link.is_symlink():raise RuntimeError('owned_systemd_unit_still_present')
 r,d=prop(name)
 if d.get('MainPID')!='0' or d.get('ActiveState')!='inactive' or d.get('LoadState') not in ('not-found','loaded'):raise RuntimeError('owned_systemd_unit_not_stopped')
 if not m['units'][key].get('stopped') or not m['units'][key].get('unit_file_removed'):raise RuntimeError('owned_manifest_stop_record_missing')
for pid,start,name in ((C['child_pid'],C['child_starttime'],C['child_unit']),(C['server']['pid'],C['server']['starttime'],C['server']['unit'])):
 now=proc_start(pid)
 if now==start:raise RuntimeError('owned_process_generation_still_alive:'+name)
# No exact loopback listener or process remains in either owned systemd cgroup.
iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{C['server']['port']:04X}"
for l in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=l.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':raise RuntimeError('owned_loopback_listener_still_present')
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  cg=(p/'cgroup').read_text()
  if C['child_unit'] in cg or C['server']['unit'] in cg:raise RuntimeError('owned_unit_cgroup_process_remains')
 except (FileNotFoundError,ProcessLookupError,PermissionError):pass
# Exact App Unit/files and TLS payload cache are contained by this owned root only.
appU=Path('/etc/systemd/system')/C['app_unit'];appW=Path('/etc/systemd/system/multi-user.target.wants')/C['app_unit']
if appU.exists() or appU.is_symlink() or appW.exists() or appW.is_symlink() or (D/'app'/'bin'/'qa-app').exists() or (D/'app'/'config'/'qa.env').exists():raise RuntimeError('owned_app_resource_still_present')
# Full-tree deletion gate: every entry is non-symlink, owned by root, on this device, and realpaths under D.
files=dirs=total=0
for base,subdirs,names in os.walk(D,topdown=True,followlinks=False):
 bp=Path(base);bst=bp.lstat()
 if stat.S_ISLNK(bst.st_mode) or bst.st_uid!=0 or bst.st_dev!=C['root_dev'] or bp.resolve()!=bp or not bp.is_relative_to(D):raise RuntimeError('owned_tree_directory_identity_failed')
 dirs+=len(subdirs)
 for name in list(subdirs)+names:
  q=bp/name;qs=q.lstat()
  if stat.S_ISLNK(qs.st_mode) or qs.st_uid!=0 or qs.st_dev!=C['root_dev'] or q.resolve()!=q or not q.is_relative_to(D):raise RuntimeError('owned_tree_entry_identity_failed')
  if stat.S_ISREG(qs.st_mode):files+=1;total+=qs.st_size
  elif not stat.S_ISDIR(qs.st_mode):raise RuntimeError('owned_tree_unexpected_entry_type')
# Snapshot the owner markers/hashes before deleting, then remove only this exact path.
root_identity={'dev':st.st_dev,'ino':st.st_ino,'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode))};manifest_sha=hashlib.sha256(raw).hexdigest();marker_sha=sha(MARK);shutil.rmtree(D)
if D.exists() or D.is_symlink():raise RuntimeError('owned_root_delete_not_complete')
print(json.dumps({'status':'PASS','root':str(D),'root_identity':root_identity,'manifest_sha256_before_delete':manifest_sha,'owner_marker_sha256':marker_sha,'nonce':m['nonce'],'root_absent_after_delete':True,'tree_file_count':files,'tree_directory_count':dirs,'tree_total_bytes':total,'child_old_pid_same_generation_alive':False,'server_old_pid_same_generation_alive':False,'listener_absent':True,'both_owned_unit_files_absent':True,'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
cmd="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(cmd,'delete-owned-root')
# API and original-node gates immediately before this exact root deletion operation.
for path in ('/api/applications/'+app_id,'/api/artifacts/'+ART['id'],'/api/nodes/'+CHILD):
 if api.request('GET',path)[0]!=404:raise RuntimeError('owned_api_object_not_deleted_before_root_cleanup')
node2=api.get_json('/api/nodes/'+NODE)
if node2.get('id')!=NODE or node2.get('status')!='online' or node2.get('sync_status')!='synced':raise RuntimeError('native2_control_node_unhealthy_before_root_delete')
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());peers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id'])
 if n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('original_four_health_failed_before_root_delete')
 peers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
# App executions and audit records must still be readable before root deletion.
audit_app_before=api.get_json('/api/audit?q='+urllib.parse.quote(app_id)+'&limit=100');audit_child_before=api.get_json('/api/audit?q='+urllib.parse.quote(CHILD)+'&limit=100')
app_history=api.get_json('/api/applications/'+app_id+'/executions');successful=api.get_json('/api/executions/'+json.loads((HERE/'application-deploy-retry-api.json').read_text())['execution_id']);failed=api.get_json('/api/executions/'+json.loads((HERE/'application-deploy-api.json').read_text())['execution_id'])
if successful.get('status')!='SUCCESS' or failed.get('status')!='FAILED' or len(app_history)<2:raise RuntimeError('execution_audit_history_missing_before_root_delete')
artifacts=api.get_json('/api/artifacts')
if any(a.get('id')==ART['id'] for a in artifacts):raise RuntimeError('owned_artifact_still_in_hub')
t=api.create_task('qa-resume-20261010-host-tls-delete-owned-root-1358f9a990bd',cmd,NODE)
try:r=api.run_task(t,timeout=90)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('owned_root_delete_task_failed')
out=json.loads(r.get('stdout') or '')
if out.get('status')!='PASS' or not out.get('root_absent_after_delete'):raise RuntimeError('owned_root_delete_result_invalid')
# Post-delete API/history and original-four checks.
for path in ('/api/applications/'+app_id,'/api/artifacts/'+ART['id'],'/api/nodes/'+CHILD):
 if api.request('GET',path)[0]!=404:raise RuntimeError('owned_api_object_reappeared')
audit_app_after=api.get_json('/api/audit?q='+urllib.parse.quote(app_id)+'&limit=100');audit_child_after=api.get_json('/api/audit?q='+urllib.parse.quote(CHILD)+'&limit=100')
posthist=api.get_json('/api/applications/'+app_id+'/executions');postok=api.get_json('/api/executions/'+successful['id']);postfail=api.get_json('/api/executions/'+failed['id'])
if len(posthist)!=len(app_history) or postok.get('status')!='SUCCESS' or postfail.get('status')!='FAILED' or [x.get('id') for x in audit_app_after]!=[x.get('id') for x in audit_app_before] or [x.get('id') for x in audit_child_after]!=[x.get('id') for x in audit_child_before]:raise RuntimeError('historical_execution_or_audit_changed_after_cleanup')
postpeers=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id']);postpeers.append({'node_id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
if not all(x['status']=='online' and x['sync_status']=='synced' for x in postpeers):raise RuntimeError('original_four_health_failed_after_root_delete')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());record={'status':'PASS','actual_at':stamp,'remote':out,'app_id':app_id,'artifact_id':ART['id'],'child_node_id':CHILD,'app_get404':True,'artifact_get404':True,'child_node_get404':True,'app_execution_history_count_before_after':[len(app_history),len(posthist)],'application_audit_count_before_after':[len(audit_app_before),len(audit_app_after)],'child_audit_count_before_after':[len(audit_child_before),len(audit_child_after)],'successful_execution_status_after':postok.get('status'),'failed_environment_execution_status_after':postfail.get('status'),'original_four_nodes':postpeers,'root_delete_task_id':t['id'],'root_delete_execution_id':r['id'],'root_delete_task_cleanup':c};api.write_json('cleanup-root-deleted.json',record)
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'tls-owned-resource-cleanup-complete','status':'PASS','current_operation':'Exact owned root removed after live root/manifest/marker/parent Agent/full-tree owner-realpath-device checks and proofs that child/server/App Units, processes, and listener were gone. Exact Hub child/App/Artifact objects are 404; prior App executions and audit history are preserved; original four Nodes remain healthy.','next_action':'Prepare a precise proposed Host container image/capability/mount setup for Root review only; no container/image will be created in this TLS batch.','current_manifest_sha256':STOP['manifest_sha256'],'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['cleanup-root-deleted.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'remote_root_absent':True,'app_artifact_child_get404':True,'history_preserved':len(posthist)==len(app_history),'success_execution':'SUCCESS','prior_environment_execution':'FAILED','original_four_online_synced':True,'delete_task_get404':c['get_after_delete_http']==404,'tree_files_deleted':out['tree_file_count']}))
