"""Create/start only Host001 with approved exact mounts/caps and no seccomp override."""
import hashlib,json,os,pathlib,sys,time,subprocess
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());R=json.loads((B.parent/'qa-resume-20261010-root'/'host-plan-review.json').read_text());PRE=json.loads((B/'host-execution-preflight.json').read_text());IMG=json.loads((B/'host-image-import-verified.json').read_text());ST=json.loads((B/'host001-stage.json').read_text())
RUN=P['identifiers']['run'];NODE=P['target_native2_node_id'];ROOT=P['identifiers']['root'];TAG=P['identifiers']['image_tag'];IMAGE_ID=IMG['recovery_probe']['image']['id'];NET=P['identifiers']['bridge'];VOL=P['identifiers']['volumes'][1];NAME=P['identifiers']['containers'][1];UNIT=P['identifiers']['unit'];APP=P['identifiers']['app_root'];CONFIG=ST['remote']['config_path'];NODE_ID=ST['node_id'];EXPECTED=PRE['remote']['native2_agent'];BASE_TREE=PRE['systemd_tree_before_host_write'];TREE_SHA=hashlib.sha256(json.dumps(BASE_TREE,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if R.get('proposal_sha256')!='07b607093648a6ccdf2254d32cf7354e7b5f589b78cf1c5aa850edc9db5dfe18' or ST.get('status')!='PASS':raise RuntimeError('approved_stage_artifact_guard_failed')

def make_command():
    DIAG=json.loads((B/'host001-container-create-harness-diagnosis.json').read_text())
    c={'container_id':DIAG['container']['id'],'config_sha256':ST['remote']['config_sha256'],'owner_marker_sha256':ST['remote']['owner_marker_sha256'],'network_id':ST['remote']['bridge']['id'],'run':RUN,'root':ROOT,'root_dev':IMG['root']['device'],'root_ino':IMG['root']['inode'],'image_tag':TAG,'image_id':IMAGE_ID,'network':NET,'network_id':json.loads((B/'host001-stage.json').read_text())['remote']['bridge']['id'],'volume':VOL,'container':NAME,'node_id':NODE_ID,'unit':UNIT,'app_root':APP,'config':CONFIG,'agent':EXPECTED,'systemd_tree_sha':TREE_SHA}
    src=r'''import hashlib,json,os,stat,subprocess,time
from pathlib import Path
C=json.loads(%r)
def run(a,check=True):
 p=subprocess.run(a,text=True,capture_output=True,timeout=30)
 if check and p.returncode:raise RuntimeError('docker_or_systemctl_command_failed:'+a[1]+':'+str(p.returncode)+':'+p.stderr.strip()[:250])
 return p

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(exe),'argv':argv,'cgroup':(p/'cgroup').read_text().strip()}
def tree(root):
 out=[]
 for par,ds,fs in os.walk(root,followlinks=False):
  ds.sort();fs.sort()
  for n in ds+fs:
   p=Path(par)/n;s=p.lstat();r={'path':str(p.relative_to(root)),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'dev':s.st_dev,'ino':s.st_ino,'type':'dir' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other'}
   if stat.S_ISREG(s.st_mode):r.update({'size':s.st_size,'sha256':sha(p)})
   elif stat.S_ISLNK(s.st_mode):r['sha256']=hashlib.sha256(os.readlink(p).encode()).hexdigest()
   out.append(r)
 return out
root=Path(C['root']);rs=root.lstat();owner=json.loads((root/'.owner.json').read_text())
if root.is_symlink() or root.resolve()!=root or (rs.st_dev,rs.st_ino,rs.st_uid,stat.S_IMODE(rs.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or owner.get('run')!=C['run']:raise RuntimeError('root_owner_guard_failed')
agent=proc(C['agent']['pid'])
if any(agent[k]!=C['agent'][k] for k in ('pid','starttime','uid','argv','exe_sha256','cgroup')):raise RuntimeError('native2_agent_process_changed')
props=run(['systemctl','show','nodesteer-agent-repair-20260930-101945-native-2.service','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath']);kv=dict(x.split('=',1) for x in props.stdout.splitlines() if '=' in x)
if kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=agent['pid']:raise RuntimeError('native2_agent_unit_changed')
sd=tree('/etc/systemd/system');sdh=hashlib.sha256(json.dumps(sd,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if sdh!=C['systemd_tree_sha']:raise RuntimeError('systemd_tree_changed_before_container')
img=json.loads(run(['docker','image','inspect',C['image_tag']]).stdout)[0];cfg=img.get('Config') or {}
if img.get('Id')!=C['image_id'] or (cfg.get('Labels') or {}).get('nodesteer.e2e.owner')!=C['run']:raise RuntimeError('image_identity_changed')
net=json.loads(run(['docker','network','inspect',C['network']]).stdout)[0];vol=json.loads(run(['docker','volume','inspect',C['volume']]).stdout)[0]
if net.get('Id')!=C['network_id'] or net.get('Name')!=C['network'] or (net.get('Labels') or {}).get('nodesteer-e2e-run')!=C['run'] or vol.get('Name')!=C['volume'] or (vol.get('Labels') or {}).get('nodesteer-e2e-run')!=C['run']:raise RuntimeError('network_or_volume_owner_changed')
config=Path(C['config']);app=Path(C['app_root']);marker=Path(C['root'])/'host001'/'.owner.json'
if sha(config)!=C['config_sha256'] or sha(marker)!=C['owner_marker_sha256']:raise RuntimeError('config_or_host001_marker_sha_changed')
for p,mode in ((config,0o600),(app,0o700)):
 s=p.lstat()
 if p.is_symlink() or p.resolve()!=p or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=mode:raise RuntimeError('mount_source_owner_guard_failed')
if any(app.iterdir()):raise RuntimeError('app_root_not_empty_before_container')
old=run(['docker','container','inspect',C['container_id']],False)
if old.returncode:raise RuntimeError('exact_created_container_not_found')
inspect=json.loads(old.stdout)[0];h=inspect['HostConfig'];cfg=inspect['Config'];labels=cfg.get('Labels') or {};mounts=[{'type':m.get('Type'),'source':m.get('Source'),'name':m.get('Name'),'destination':m.get('Destination'),'rw':m.get('RW')} for m in inspect.get('Mounts',[])]
if inspect.get('Id')!=C['container_id'] or inspect.get('Name')!='/'+C['container'] or inspect.get('Image')!=C['image_id'] or labels.get('nodesteer-e2e-run')!=C['run'] or labels.get('nodesteer.e2e.role')!='host001' or labels.get('nodesteer.e2e.owner')!=C['run']:raise RuntimeError('container_id_name_label_image_guard_failed')
if cfg.get('User')!='0:0' or h.get('Privileged') is not False or h.get('CapDrop')!=['ALL'] or {x.removeprefix('CAP_') for x in (h.get('CapAdd') or [])}!={'SYS_ADMIN','SYS_CHROOT','SYS_PTRACE'} or h.get('PidMode')!='host' or h.get('ReadonlyRootfs') is not True or h.get('NetworkMode')!=C['network'] or h.get('PortBindings') not in (None,{}):raise RuntimeError('container_security_config_mismatch')
secopts=set(h.get('SecurityOpt') or [])
if 'no-new-privileges' not in secopts or not secopts.issubset({'no-new-privileges','label=disable'}) or any('seccomp' in x for x in secopts):raise RuntimeError('container_security_opt_mismatch')
want={( 'volume',C['volume'],C['volume'],'/var/lib/nodesteer',True),('bind',str(config),None,'/run/nodesteer/agent.yaml',False),('bind',str(app),None,'/host'+str(app),True),('bind','/etc/systemd/system',None,'/host/etc/systemd/system',True),('bind','/proc',None,'/host/proc',False),('bind','/sys',None,'/host/sys',False),('bind','/etc/os-release',None,'/host/etc/os-release',False)}
got={(x['type'],x['name'] if x['type']=='volume' else x['source'],x['name'] if x['type']=='volume' else None,x['destination'],x['rw']) for x in mounts}
if want!=got:raise RuntimeError('container_mount_set_mismatch')
state=inspect.get('State') or {}
if state.get('Running') is not False or int(state.get('Pid',0))!=0 or state.get('Status')!='created':raise RuntimeError('existing_container_not_exact_stopped_state')
started=run(['docker','container','start',C['container_id']]);post=json.loads(run(['docker','container','inspect',C['container_id']]).stdout)[0];state=post['State'];inspect=post;h=post['HostConfig'];cfg=post['Config'];labels=cfg.get('Labels') or {};mounts=[{'type':m.get('Type'),'source':m.get('Source'),'name':m.get('Name'),'destination':m.get('Destination'),'rw':m.get('RW')} for m in post.get('Mounts',[])]
if state.get('Running') is not True or int(state.get('Pid',0))<=0:raise RuntimeError('host001_container_not_running')
pid=int(state['Pid']);process=proc(pid)
if process['uid']!=0 or process['exe_sha256']!=C['agent']['exe_sha256'] or '/run/nodesteer/agent.yaml' not in process['argv'] or C['container_id'] not in process['cgroup']:raise RuntimeError('host001_agent_process_guard_failed')
network_state=post.get('NetworkSettings',{}).get('Networks') or {}
if set(network_state)!={C['network']} or network_state[C['network']].get('NetworkID')!=C['network_id']:raise RuntimeError('started_container_network_attachment_mismatch')
# Host systemd must remain byte/metadata-identical before the approved read-only smoke.
sd2=tree('/etc/systemd/system');sdh2=hashlib.sha256(json.dumps(sd2,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if sdh2!=C['systemd_tree_sha']:raise RuntimeError('systemd_changed_during_container_start')
unitpath=Path('/etc/systemd/system')/C['unit']
if unitpath.exists() or unitpath.is_symlink():raise RuntimeError('app_unit_created_before_smoke')
filtered={'id':inspect['Id'],'name':inspect['Name'],'image_id':inspect['Image'],'labels':labels,'user':cfg.get('User'),'privileged':h.get('Privileged'),'cap_drop':h.get('CapDrop'),'cap_add':h.get('CapAdd'),'pid_mode':h.get('PidMode'),'read_only_rootfs':h.get('ReadonlyRootfs'),'security_opt':h.get('SecurityOpt'),'network_mode':h.get('NetworkMode'),'port_bindings':h.get('PortBindings'),'mounts':mounts,'running':state.get('Running'),'pid':pid,'restart_policy':h.get('RestartPolicy')}
print(json.dumps({'status':'PASS','container':filtered,'process':process,'systemd_tree_sha256':sdh2,'systemd_tree_matches_baseline':True,'exact_app_unit_absent':True,'docker_create_argv_template':['docker','container','create','--name',C['container'],'--cap-drop=ALL','--cap-add=SYS_ADMIN','--cap-add=SYS_CHROOT','--cap-add=SYS_PTRACE','--security-opt=no-new-privileges','--network',C['network'],'--pid=host','<approved exact mounts>','--tmpfs','/tmp:size=64m,nosuid,nodev',C['image_tag']],'docker_start_argv':['docker','container','start',C['container_id']],'reused_existing_container_id':True,'seccomp_override_omitted':True},separators=(',',':')))
'''%json.dumps(c,separators=(',',':'))
    command='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(command,'host001-container-create-start')
    return command

def main():
    node=api.get_json('/api/nodes/'+NODE_ID)
    if node.get('status') not in ('pending','offline') or node.get('sync_status') not in ('pending','unsynced','synced'):raise RuntimeError('host001_prestart_node_state_unexpected')
    task=api.create_task('qa-resume-20261010-host-tls-host001-start-existing-f174890c3846',make_command(),NODE)
    try:result=api.run_task(task,timeout=120)
    finally:cleanup=api.cleanup_task(task['id'],NODE)
    stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    if result.get('status')!='SUCCESS':
        api.write_json('host001-container-start-failed.json',{'status':'FAILED','actual_at':stamp,'task_id':task['id'],'execution_id':result.get('id'),'exit_code':result.get('exit_code'),'block_reason':result.get('block_reason'),'task_cleanup':cleanup})
        raise RuntimeError('host001_container_start_task_failed')
    remote=json.loads(result.get('stdout') or '')
    if remote.get('status')!='PASS' or remote.get('systemd_tree_matches_baseline') is not True:raise RuntimeError('host001_container_start_result_invalid')
    end=time.monotonic()+35
    while time.monotonic()<end:
        node=api.get_json('/api/nodes/'+NODE_ID)
        if node.get('status')=='online' and node.get('sync_status')=='synced':break
        time.sleep(.5)
    if node.get('status')!='online' or node.get('sync_status')!='synced':
        api.write_json('host001-node-start-sync-timeout.json',{'status':'PARTIAL','actual_at':stamp,'node_id':NODE_ID,'status_api':node.get('status'),'sync_status':node.get('sync_status'),'container':remote['container'],'process':remote['process'],'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup})
        raise RuntimeError('host001_agent_not_online_synced')
    peers=[]
    for x in json.loads((B.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes']:
        q=api.get_json('/api/nodes/'+x['node_id']);peers.append({'node_id':x['node_id'],'status':q.get('status'),'sync_status':q.get('sync_status')})
    if any(x['status']!='online' or x['sync_status']!='synced' for x in peers):raise RuntimeError('original_nodes_health_changed')
    record={'status':'PASS','actual_at':stamp,'node_id':NODE_ID,'node':{k:node.get(k) for k in ('id','agent_id','hostname','ip','status','sync_status','agent_version','labels')},'remote':remote,'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup,'original_four_nodes':peers}
    api.write_json('host001-container-start.json',record)
    p=B/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'status':'HOST001_CONTAINER_ONLINE_SMOKE_PENDING','stage':'host001-container-running-owner-verified','current_operation':'Only the exact existing Host001 container ID was started after filtered owner/security/mount guards. Filtered Docker JSON verified immutable ID/image/labels/cap-drop/caps/PID host/no seccomp override/exact RW+RO mounts; live Agent PID/starttime/UID/argv/exeSHA/cgroup recorded. Host systemd tree unchanged and target Unit absent. Hub Node online/synced.','next_action':'Run read-only nsenter smoke through this Host001 Agent against exact app Unit (must remain not-found); verify host mount identity and systemd metadata unchanged before creating Artifact/App.','host001_node_id':NODE_ID,'host001_container_id':remote['container']['id'],'host001_container_pid':remote['process']['pid'],'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+[str((B/'host001-container-start.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',v)
    print(json.dumps({'status':'PASS','timestamp':stamp,'node_id':NODE_ID,'node_online_synced':True,'container_id':remote['container']['id'],'pid':remote['process']['pid'],'systemd_unchanged':True,'app_unit_absent':True,'docker_start_argv':['docker','container','start',C['container_id']],'reused_existing_container_id':True,'seccomp_override_omitted':True,'task_get404':cleanup['get_after_delete_http']==404}))

if __name__=='__main__':main()
