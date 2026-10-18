"""Enroll only the approved Host001 fixture and stage its secret config/resources."""
import hashlib,json,os,pathlib,re,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());R=json.loads((B.parent/'qa-resume-20261010-root'/'host-plan-review.json').read_text());PRE=json.loads((B/'host-execution-preflight.json').read_text());IMG=json.loads((B/'host-image-import-verified.json').read_text())
RUN=P['identifiers']['run'];NODE=P['target_native2_node_id'];ROOT=P['identifiers']['root'];TAG=P['identifiers']['image_tag'];BR=P['identifiers']['bridge'];VOL=P['identifiers']['volumes'][1];NAME=P['identifiers']['containers'][1];APPROOT=P['identifiers']['app_root'];UNIT=P['identifiers']['unit'];IMAGE_ID=IMG['recovery_probe']['image']['id'];CHILD_NAME=NAME
if R.get('status')!='APPROVED_WITH_SPECIFIC_CORRECTIONS' or R.get('proposal_sha256')!='07b607093648a6ccdf2254d32cf7354e7b5f589b78cf1c5aa850edc9db5dfe18':raise RuntimeError('root_approval_hash_guard_failed')
if IMG.get('status')!='PASS_IMAGE_IMPORT_VERIFIED_AFTER_INSPECT_HARNESS_FAILURE' or IMAGE_ID!='sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44':raise RuntimeError('verified_image_identity_guard_failed')

def remote_command(node, gateway, agentid, config_path):
    agent=PRE['remote']['native2_agent']; treehash=hashlib.sha256(json.dumps(PRE['systemd_tree_before_host_write'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    safe={'run':RUN,'node_id':node,'agent_id':agentid,'node_name':CHILD_NAME,'node_ip':CHILD_NAME+'.invalid','gateway':gateway,'root':ROOT,'root_dev':IMG['root']['device'],'root_ino':IMG['root']['inode'],'image_tag':TAG,'image_id':IMAGE_ID,'bridge':BR,'volume':VOL,'container':NAME,'app_root':APPROOT,'unit':UNIT,'config_path':config_path,'agent_sha':agent['exe_sha256'],'agent_process':agent,'systemd_tree_sha':treehash}
    src=r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
C=json.loads(%r);TOKEN=os.environ.get('NODESTEER_PARAM_registration_token','')
if not TOKEN:raise RuntimeError('registration_secret_parameter_missing')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=25)
 if p.returncode:raise RuntimeError('command_failed:'+a[1]+':'+str(p.returncode)+':'+p.stderr.strip()[:200])
 return p.stdout.strip()
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
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'exe':exe,'exe_sha256':sha(exe),'argv':argv,'cgroup':(p/'cgroup').read_text().strip()}
root=Path(C['root']);rs=root.lstat();owner=json.loads((root/'.owner.json').read_text())
if root.is_symlink() or root.resolve()!=root or (rs.st_dev,rs.st_ino,rs.st_uid,stat.S_IMODE(rs.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or owner.get('run')!=C['run']:raise RuntimeError('owned_root_guard_failed')
if (root/'image-rootfs.tar').exists() or not (root/'agent-rootfs.tar').is_file() or sha(root/'agent-rootfs.tar')!=%r:raise RuntimeError('rootfs_tar_identity_failed')
agent=proc(C['agent_process']['pid'])
if any(agent[k]!=C['agent_process'][k] for k in ('pid','starttime','uid','exe_sha256','argv','cgroup')):raise RuntimeError('native2_process_owner_changed')
unit='nodesteer-agent-repair-20260930-101945-native-2.service';props=run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath']);kv=dict(x.split('=',1) for x in props.splitlines() if '=' in x)
if kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=agent['pid']:raise RuntimeError('native2_unit_owner_changed')
systemd=tree('/etc/systemd/system');treehash=hashlib.sha256(json.dumps(systemd,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if treehash!=C['systemd_tree_sha']:raise RuntimeError('systemd_tree_changed_before_host001_stage')
image=json.loads(run(['docker','image','inspect',C['image_tag']]))[0];cfg=image.get('Config') or {}
if image.get('Id')!=C['image_id'] or (cfg.get('Labels') or {}).get('nodesteer.e2e.owner')!=C['run'] or cfg.get('Entrypoint')!=['/opt/nodesteer/nodesteer-agent','--config','/run/nodesteer/agent.yaml']:raise RuntimeError('verified_image_changed')
for cmd in (['docker','network','inspect',C['bridge']],['docker','volume','inspect',C['volume']],['docker','container','inspect',C['container']]):
 p=subprocess.run(cmd,text=True,capture_output=True,timeout=10)
 if p.returncode==0:raise RuntimeError('approved_identifier_already_exists')
# Create exact private directories, root-owned marker, and app fixture root.
priv=root/'private';priv.mkdir(mode=0o700,exist_ok=True)
if priv.is_symlink() or priv.resolve()!=priv or priv.stat().st_uid!=0 or stat.S_IMODE(priv.stat().st_mode)!=0o700:raise RuntimeError('private_dir_guard_failed')
host001=root/'host001';host001.mkdir(mode=0o700,exist_ok=False);app=Path(C['app_root']);app.mkdir(mode=0o700,parents=True,exist_ok=False)
for p in (host001,app):
 s=p.lstat()
 if p.is_symlink() or p.resolve()!=p or s.st_uid!=0 or stat.S_IMODE(s.st_mode)!=0o700:raise RuntimeError('app_root_identity_failed')
marker={'run':C['run'],'nonce':C['run'].rsplit('-',1)[1],'node_id':C['node_id'],'agent_id':C['agent_id'],'app_root':C['app_root'],'host001_dir_dev':host001.stat().st_dev,'host001_dir_ino':host001.stat().st_ino,'app_root_dev':app.stat().st_dev,'app_root_ino':app.stat().st_ino}
fd=os.open(host001/'.owner.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump(marker,f,separators=(',',':'));f.write('\n')
# Token remains only in this 0600 file, fed to the Task as a secret parameter.
config='hub_url: '+json.dumps(C['gateway'])+'\nregistration_token: '+json.dumps(TOKEN)+'\nagent_id: '+json.dumps(C['agent_id'])+'\nnode_name: '+json.dumps(C['node_name'])+'\nnode_ip: '+json.dumps(C['node_ip'])+'\ndeployment_mode: "docker_host_integration"\nhost_integration: true\nhost_root: "/host"\nhost_path_allowlist:\n  - '+json.dumps(C['app_root'])+'\n  - '+json.dumps('/etc/systemd/system/'+C['unit'])+'\ndata_dir: "/var/lib/nodesteer"\nheartbeat_sec: 3\nrevision_check_sec: 3\n'
config_path=Path(C['config_path']);fd=os.open(config_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'w') as f:f.write(config);f.flush();os.fsync(f.fileno())
os.chown(config_path,0,0);os.chmod(config_path,0o600)
# Create only the one bridge and Host001 volume, each with the approved run label.
label='nodesteer-e2e-run='+C['run'];network_id=run(['docker','network','create','--driver','bridge','--label',label,C['bridge']]);volume_name=run(['docker','volume','create','--label',label,C['volume']])
net=json.loads(run(['docker','network','inspect',C['bridge']]))[0];vol=json.loads(run(['docker','volume','inspect',C['volume']]))[0]
if net.get('Id')!=network_id or net.get('Name')!=C['bridge'] or (net.get('Labels') or {}).get('nodesteer-e2e-run')!=C['run'] or volume_name!=C['volume'] or vol.get('Name')!=C['volume'] or (vol.get('Labels') or {}).get('nodesteer-e2e-run')!=C['run']:raise RuntimeError('owned_bridge_volume_identity_failed')
systemd2=tree('/etc/systemd/system');treehash2=hashlib.sha256(json.dumps(systemd2,sort_keys=True,separators=(',',':')).encode()).hexdigest()
if treehash2!=C['systemd_tree_sha']:raise RuntimeError('systemd_tree_changed_during_stage')
print(json.dumps({'status':'PASS','node_id':C['node_id'],'agent_id':C['agent_id'],'node_name':C['node_name'],'node_ip':C['node_ip'],'gateway':C['gateway'],'config_path':str(config_path),'config_mode':oct(stat.S_IMODE(config_path.stat().st_mode)),'config_sha256':sha(config_path),'secret_emitted':False,'private_dir':{'path':str(priv),'dev':priv.stat().st_dev,'ino':priv.stat().st_ino,'uid':priv.stat().st_uid,'mode':oct(stat.S_IMODE(priv.stat().st_mode))},'app_root':{'path':str(app),'dev':app.stat().st_dev,'ino':app.stat().st_ino,'uid':app.stat().st_uid,'mode':oct(stat.S_IMODE(app.stat().st_mode))},'owner_marker_sha256':sha(host001/'.owner.json'),'bridge':{'id':net['Id'],'name':net['Name'],'driver':net['Driver'],'labels':net.get('Labels'),'containers':list((net.get('Containers') or {}).keys())},'volume':{'name':vol['Name'],'driver':vol['Driver'],'labels':vol.get('Labels'),'scope':vol.get('Scope')},'systemd_tree_sha_before_after':treehash==treehash2},separators=(',',':')))
''' % (json.dumps(safe,separators=(',',':')), IMG['root']['tar_sha256'])
    command='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(command,'host001-stage-config-bridge-volume')
    return command

def main():
    nodes=api.get_json('/api/nodes')
    if not isinstance(nodes,list):raise RuntimeError('node_list_shape_changed')
    node_name='qa-host-f174890c3846-host001';node_ip=node_name+'.invalid'
    if any(x.get('hostname')==node_name or x.get('ip')==node_ip for x in nodes):raise RuntimeError('host001_node_identity_collision')
    originals=json.loads((B.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes'];before=[]
    for x in originals:
        n=api.get_json('/api/nodes/'+x['node_id']);before.append({'id':x['node_id'],'status':n.get('status'),'sync_status':n.get('sync_status')})
    if len(before)!=4 or any(n['status']!='online' or n['sync_status']!='synced' for n in before):raise RuntimeError('original_nodes_unhealthy')
    status,raw=api.request('POST','/api/nodes/enrollment',{'node_name':node_name,'node_ip':node_ip,'hub_address':'http://192.168.100.209:18080'})
    if status!=200:raise RuntimeError('host001_enrollment_http_'+str(status))
    response=json.loads(raw);token_match=re.search(r'^registration_token:\s*("(?:[^"\\]|\\.)*")\s*$',response.get('native',''),re.M)
    if not token_match:raise RuntimeError('enrollment_secret_field_missing')
    token=json.loads(token_match.group(1));node_id=response.get('node_id');agent_id=response.get('agent_id');gateway=response.get('gateway_url')
    if not token or not node_id or not agent_id or gateway!='ws://192.168.100.209:18080':raise RuntimeError('host001_enrollment_response_gate_failed')
    # Persist only nonsecret identity fields; never persist the response or token.
    record={'status':'PASS','actual_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'request':{'node_name':node_name,'node_ip':node_ip,'hub_address':'http://192.168.100.209:18080'},'http':status,'node_id':node_id,'agent_id':agent_id,'gateway_url':gateway,'registration_secret_present_not_persisted':True,'target':'repair :18080','original_four_nodes':before}
    api.write_json('host001-enrollment.json',record)
    config_path=ROOT+'/private/host001-agent.yaml';cmd=remote_command(node_id,gateway,agent_id,config_path)
    task=api.create_secret_task('qa-resume-20261010-host-tls-host001-stage-f174890c3846',cmd,NODE,'registration_token')
    try:result=api.run_task(task,timeout=120,params={'registration_token':token},secret_values=(token,))
    finally:cleanup=api.cleanup_task(task['id'],NODE)
    stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    if result.get('status')!='SUCCESS':
        api.write_json('host001-stage-failed.json',{'status':'FAILED','actual_at':stamp,'node_id':node_id,'agent_id':agent_id,'task_id':task['id'],'execution_id':result.get('id'),'block_reason':result.get('block_reason'),'task_cleanup':cleanup,'secret_value_persisted':False})
        raise RuntimeError('host001_stage_task_failed')
    remote=json.loads(result.get('stdout') or '')
    if remote.get('status')!='PASS' or remote.get('secret_emitted') is not False or remote.get('config_mode')!='0o600' or not remote.get('systemd_tree_sha_before_after'):raise RuntimeError('host001_stage_result_gate_failed')
    api.write_json('host001-stage.json',{'status':'PASS','actual_at':stamp,'node_id':node_id,'agent_id':agent_id,'remote':remote,'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup,'task_secret_value_persisted':False})
    p=B/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'status':'HOST001_FIXTURE_STAGED_CONTAINER_PENDING','stage':'host001-node-config-bridge-volume-staged','host001_node_id':node_id,'host001_agent_id':agent_id,'current_operation':'Only the new Host001 Node was enrolled via repair :18080. Native2 secret Task wrote one private 0600 Agent config, owned app root/marker, unique bridge and Host001 volume. No container started and no host systemd write occurred.','next_action':'Create/start only exact Host001 container, parse/filter inspect JSON and dynamic PID owner; run readonly nsenter smoke against exact absent Unit. Do not create Managed App/Artifact before smoke PASS.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+[str((B/'host001-enrollment.json').resolve()),str((B/'host001-stage.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',v)
    print(json.dumps({'status':'PASS','timestamp':stamp,'node_id':node_id,'agent_id':agent_id,'config_sha256':remote['config_sha256'],'bridge_id':remote['bridge']['id'],'volume':remote['volume']['name'],'secret_emitted':False,'task_get404':cleanup['get_after_delete_http']==404}))

if __name__=='__main__':main()
