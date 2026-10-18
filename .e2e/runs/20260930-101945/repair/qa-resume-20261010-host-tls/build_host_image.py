"""Build the approved owned minimal rootfs and import its one exact Docker image."""
import hashlib, json, os, pathlib, stat, sys, time, subprocess
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import api

HERE=pathlib.Path(__file__).resolve().parent
PLAN=json.loads((HERE/'host-fixture-plan-proposal.json').read_text())
REVIEW=json.loads((HERE.parent/'qa-resume-20261010-root'/'host-plan-review.json').read_text())
PREF=json.loads((HERE/'host-execution-preflight.json').read_text())
RUN=PLAN['identifiers']['run'];ROOT=PLAN['identifiers']['root'];IMAGE=PLAN['identifiers']['image_tag'];NODE=PLAN['target_native2_node_id']
if REVIEW.get('status')!='APPROVED_WITH_SPECIFIC_CORRECTIONS' or REVIEW.get('proposal_sha256')!='07b607093648a6ccdf2254d32cf7354e7b5f589b78cf1c5aa850edc9db5dfe18': raise RuntimeError('approved_scope_hash_guard_failed')
if PLAN['identifiers']['nonce']!='f174890c3846' or PLAN['host_resource_creation_authorized'] is not False: raise RuntimeError('nonce_or_plan_guard_failed')
if PREF['remote']['native2_agent']['exe_sha256']!='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add': raise RuntimeError('preflight_agent_sha_invalid')
if len(PLAN['rootfs']['files'])!=7 or len(PLAN['identifiers']['containers'])!=6 or len(PLAN['identifiers']['volumes'])!=6: raise RuntimeError('approved_resource_count_invalid')

# All mount targets are precreated as empty targets only; real host sources are bind-mounted at runtime.
EXTRA_DIRS=['/host/etc','/host/etc/systemd','/host/etc/systemd/system','/host/proc','/host/sys',
            '/host/opt','/host/opt/'+RUN,'/host/opt/'+RUN+'/host001','/host/opt/'+RUN+'/host001/app',
            '/host/opt/'+RUN+'/hostroot']
EXTRA_FILES={'/host/etc/os-release':0o644,'/run/nodesteer/agent.yaml':0o600}
EXPECTED_AGENT=PREF['remote']['native2_agent']
SYSTEMD=PREF['systemd_tree_before_host_write']
SYSTEMD_HASH=hashlib.sha256(json.dumps(SYSTEMD,sort_keys=True,separators=(',',':')).encode()).hexdigest()
SOURCES=PLAN['rootfs']['files']; TARGET_DIRS=sorted(set(PLAN['rootfs']['empty_dirs']+EXTRA_DIRS))

def remote_source():
    source=r'''import hashlib,json,os,shutil,stat,subprocess,tarfile,io
from pathlib import Path
ROOT=%r;RUN=%r;IMAGE=%r;BRIDGE=%r;CONTAINERS=%r;VOLUMES=%r;SOURCES=json.loads(%r);DIRS=%r;EXTRA_FILES=%r
EXPECT_AGENT=%s;EXPECT_TREE_HASH=%r

def run(a,stdin=None):
 p=subprocess.run(a,text=True,capture_output=True,timeout=45,stdin=stdin)
 return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()[:250]}
def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def sha_file(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'argv':argv,'exe':exe,'exe_sha256':sha_file(exe),'cgroup':(p/'cgroup').read_text().strip()}
def tree(root):
 root=Path(root);out=[]
 for parent,dirs,files in os.walk(root,followlinks=False):
  dirs.sort();files.sort()
  for name in dirs+files:
   p=Path(parent)/name;st=p.lstat();row={'path':str(p.relative_to(root)),'mode':oct(stat.S_IMODE(st.st_mode)),'uid':st.st_uid,'gid':st.st_gid,'dev':st.st_dev,'ino':st.st_ino,'type':'dir' if stat.S_ISDIR(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'other'}
   if stat.S_ISREG(st.st_mode):row.update({'size':st.st_size,'sha256':sha_file(p)})
   elif stat.S_ISLNK(st.st_mode):row['sha256']=sha_bytes(os.readlink(p).encode())
   out.append(row)
 return out
def check_agent():
 p=Path('/proc')/str(EXPECT_AGENT['pid']);actual=proc(EXPECT_AGENT['pid'])
 if any(actual[k]!=EXPECT_AGENT[k] for k in ('pid','starttime','uid','argv','exe_sha256','cgroup')):raise RuntimeError('native2_agent_owner_guard_changed')
 props=run(['systemctl','show','nodesteer-agent-repair-20260930-101945-native-2.service','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath'])
 kv=dict(line.split('=',1) for line in props['out'].splitlines() if '=' in line)
 if props['rc'] or kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=actual['pid']:raise RuntimeError('native2_agent_unit_guard_changed')
 return actual,kv

def main():
 agent,unit=check_agent();sd_before=tree('/etc/systemd/system');sd_hash=sha_bytes(json.dumps(sd_before,sort_keys=True,separators=(',',':')).encode())
 if sd_hash!=EXPECT_TREE_HASH:raise RuntimeError('systemd_tree_changed_before_image_build')
 parent=Path('/opt');pst=parent.lstat()
 if not stat.S_ISDIR(pst.st_mode) or stat.S_ISLNK(pst.st_mode) or pst.st_uid!=0 or str(parent.resolve())!='/opt':raise RuntimeError('opt_parent_guard_failed')
 if os.path.lexists(ROOT):raise RuntimeError('owned_root_collision')
 docker=shutil.which('docker');info=run([docker,'info','--format','{{.ServerVersion}}|{{.DockerRootDir}}|{{.Driver}}|{{json .SecurityOptions}}'])
 if info['rc'] or not info['out'].startswith('29.7.2|/var/lib/docker|overlay2|'):raise RuntimeError('docker_server_guard_failed')
 def out(a):
  r=run([docker]+a)
  if r['rc']:raise RuntimeError('docker_read_guard_failed')
  return r['out']
 images=out(['image','ls','--no-trunc','--format','{{.Repository}}:{{.Tag}} {{.ID}}']);containers=out(['ps','-a','--no-trunc','--format','{{.Names}} {{.ID}}']);networks=out(['network','ls','--no-trunc','--format','{{.Name}}']);volumes=out(['volume','ls','--format','{{.Name}} {{.Driver}}'])
 if IMAGE in images or any(x in containers for x in CONTAINERS) or BRIDGE in networks or any(any(line.split() and line.split()[0]==v for line in volumes.splitlines()) for v in VOLUMES):raise RuntimeError('exact_docker_identifier_collision')
 root=Path(ROOT);root.mkdir(mode=0o700);rst=root.lstat();root_devino={'dev':rst.st_dev,'ino':rst.st_ino,'uid':rst.st_uid,'mode':oct(stat.S_IMODE(rst.st_mode))}
 if root_devino['uid']!=0 or root_devino['mode']!='0o700' or root.resolve()!=root:raise RuntimeError('owned_root_identity_failed')
 owner={'run':RUN,'nonce':RUN.rsplit('-',1)[1],'root':ROOT,'root_devino':root_devino,'authorized_image_tag':IMAGE,'source_agent_pid':agent['pid'],'source_agent_starttime':agent['starttime'],'source_agent_sha256':agent['exe_sha256']}
 ownerpath=root/'.owner.json';fd=os.open(ownerpath,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(owner,f,separators=(',',':'));f.write('\n')
 stage=root/'rootfs';stage.mkdir(mode=0o700)
 for rel in DIRS:
  q=stage/rel.lstrip('/');q.mkdir(mode=0o755,parents=True,exist_ok=True)
 for rel,mode in EXTRA_FILES.items():
  q=stage/rel.lstrip('/');q.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
  fd=os.open(q,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode);os.close(fd);os.chmod(q,mode)
 file_manifest=[]
 for f in SOURCES:
  src=Path(f['source_on_native2']);dst=stage/f['target_in_rootfs'].lstrip('/')
  if src.resolve()!=src or not src.is_file() or src.stat().st_size!=f['size_bytes'] or sha_file(src)!=f['sha256']:raise RuntimeError('approved_source_file_guard_failed')
  dst.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
  mode=int(f['mode'],8);data=src.read_bytes();fd=os.open(dst,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
  with os.fdopen(fd,'wb') as outp:outp.write(data)
  os.chmod(dst,mode);file_manifest.append({'source':str(src),'target':f['target_in_rootfs'],'size':len(data),'sha256':sha_bytes(data),'mode':oct(mode)})
 for q in stage.rglob('*'):
  if q.is_symlink() or (not q.is_dir() and not q.is_file()):raise RuntimeError('unexpected_rootfs_entry_type')
 tarpath=root/'agent-rootfs.tar';
 with tarfile.open(tarpath,'w',format=tarfile.USTAR_FORMAT) as tf:
  entries=sorted(stage.rglob('*'),key=lambda x:(0 if x.is_dir() else 1,str(x)))
  for q in entries:
   rel='.' if q==stage else str(q.relative_to(stage));ti=tarfile.TarInfo(rel);st=q.stat();ti.uid=0;ti.gid=0;ti.uname='';ti.gname='';ti.mtime=0
   if q.is_dir():ti.type=tarfile.DIRTYPE;ti.mode=0o755;ti.size=0;tf.addfile(ti)
   else:ti.type=tarfile.REGTYPE;ti.mode=stat.S_IMODE(st.st_mode);data=q.read_bytes();ti.size=len(data);tf.addfile(ti,io.BytesIO(data))
 os.chmod(tarpath,0o600);tar_sha=sha_file(tarpath);tar_size=tarpath.stat().st_size
 change_entry='ENTRYPOINT ["/opt/nodesteer/nodesteer-agent","--config","/run/nodesteer/agent.yaml"]';label='nodesteer.e2e.owner='+RUN
 with open(tarpath,'rb') as tf:
  imp=run([docker,'import','--change',change_entry,'--change','LABEL '+label,'-',IMAGE],stdin=tf)
 if imp['rc']:raise RuntimeError('docker_import_failed')
 image_id=imp['out'].splitlines()[-1].strip()
 inspect=run([docker,'image','inspect','--format','{{.Id}}|{{index .Config.Labels "nodesteer.e2e.owner"}}|{{json .Config.Entrypoint}}|{{json .Config.Cmd}}|{{.Size}}',IMAGE])
 if inspect['rc']:raise RuntimeError('image_inspect_failed')
 bits=inspect['out'].split('|',4)
 if len(bits)!=5 or bits[0]!=image_id or bits[1]!=RUN or bits[2]!=json.dumps(['/opt/nodesteer/nodesteer-agent','--config','/run/nodesteer/agent.yaml']):raise RuntimeError('image_identity_or_entrypoint_guard_failed')
 sd_after=tree('/etc/systemd/system');sd_after_hash=sha_bytes(json.dumps(sd_after,sort_keys=True,separators=(',',':')).encode())
 if sd_after_hash!=EXPECT_TREE_HASH:raise RuntimeError('systemd_tree_changed_during_image_build')
 manifest={'status':'PASS','run':RUN,'root':ROOT,'root_devino':root_devino,'owner_marker':owner,'source_agent':agent,'native2_unit':unit,'rootfs_files':file_manifest,'empty_mount_target_dirs':DIRS,'zero_byte_mount_target_files':list(EXTRA_FILES),'tar_path':str(tarpath),'tar_sha256':tar_sha,'tar_size_bytes':tar_size,'docker_import_stdout_image_id':image_id,'image_tag':IMAGE,'image_inspect':{'id':bits[0],'owner_label':bits[1],'entrypoint':json.loads(bits[2]),'cmd':json.loads(bits[3]),'size_bytes':int(bits[4])},'systemd_tree_sha_before':sd_hash,'systemd_tree_sha_after':sd_after_hash,'systemd_tree_unchanged':sd_before==sd_after,'docker_networks_created':False,'docker_containers_created':False,'docker_volumes_created':False,'hub_nodes_created':False}
 mp=root/'image-manifest.json';fd=os.open(mp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(manifest,f,separators=(',',':'));f.write('\n')
 print(json.dumps(manifest,separators=(',',':')))
main()
''' % (ROOT,RUN,IMAGE,PLAN['identifiers']['bridge'],PLAN['identifiers']['containers'],PLAN['identifiers']['volumes'],json.dumps(SOURCES,separators=(',',':')),TARGET_DIRS,EXTRA_FILES,json.dumps(EXPECTED_AGENT,separators=(',',':')),SYSTEMD_HASH)
    command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
    api.validate_command(command,'host-build-approved-image')
    return command

def main():
    # Read-only API/health guard immediately before the sole approved build stage.
    for path in ('/healthz','/readyz','/api/me'):
        status,_=api.request('GET',path)
        if status!=200:raise RuntimeError('repair_endpoint_not_ready_'+path)
    for n in json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes']:
        obj=api.get_json('/api/nodes/'+n['node_id'])
        if obj.get('status')!='online' or obj.get('sync_status')!='synced':raise RuntimeError('original_node_unhealthy_before_image_build')
    command=remote_source()
    task=api.create_task('qa-resume-20261010-host-tls-build-approved-image-f174890c3846',command,NODE)
    try:result=api.run_task(task,timeout=120)
    finally:cleanup=api.cleanup_task(task['id'],NODE)
    stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
    if result.get('status')!='SUCCESS':
        api.write_json('host-image-build-failed.json',{'status':'FAILED','actual_at':stamp,'task_id':task['id'],'execution_id':result.get('id'),'exit_code':result.get('exit_code'),'block_reason':result.get('block_reason'),'task_cleanup':cleanup})
        raise RuntimeError('approved_image_build_task_failed')
    manifest=json.loads(result.get('stdout') or '')
    if manifest.get('status')!='PASS' or manifest.get('systemd_tree_unchanged') is not True or manifest.get('docker_containers_created') is not False:raise RuntimeError('image_build_output_guard_failed')
    # Recheck original four and Native2 after the host-side image-only mutation.
    original=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())['original_repair_nodes'];nodes=[]
    for n in original:
        obj=api.get_json('/api/nodes/'+n['node_id']);nodes.append({'node_id':n['node_id'],'status':obj.get('status'),'sync_status':obj.get('sync_status')})
    if any(n['status']!='online' or n['sync_status']!='synced' for n in nodes):raise RuntimeError('original_nodes_unhealthy_after_image_build')
    api.write_json('host-image-build.json',{'status':'PASS','actual_at':stamp,'native2_node_id':NODE,'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup,'remote':manifest,'original_four_nodes':nodes,'approval_file':str((HERE.parent/'qa-resume-20261010-root'/'host-plan-review.json').resolve())})
    progress=json.loads((HERE/'progress.json').read_text());progress.update({'actual_at':stamp,'status':'HOST_IMAGE_BUILT_SMOKE_PENDING','stage':'approved-image-built-and-inspected','current_operation':'Created only the exact nonce-owned 0700 remote root, 7-source-SHA checked minimal rootfs tar and owner-labeled image; post-import tag/ID/label/entrypoint and systemd tree hash verified. Empty bind mount target directories/files were added; no containers/networks/volumes/Nodes or systemd changes.','next_action':'Create only Host001 container with reviewed capdropALL and no explicit seccomp selector; run exact read-only nsenter smoke and capture container identity/mount/systemd metadata before any ManagedApp write.','persisted_evidence':sorted(set(progress.get('persisted_evidence',[])+[str((HERE/'host-image-build.json').resolve()),str((HERE/'task-cleanup-'+task['id']+'.json').resolve())]))});api.write_json('progress.json',progress)
    print(json.dumps({'status':'PASS','timestamp':stamp,'image_id':manifest['image_inspect']['id'],'image_tag':manifest['image_tag'],'tar_sha256':manifest['tar_sha256'],'tar_size_bytes':manifest['tar_size_bytes'],'root_devino':manifest['root_devino'],'systemd_unchanged':manifest['systemd_tree_unchanged'],'task_get404':cleanup['get_after_delete_http']==404,'original_nodes_healthy':True}))

if __name__=='__main__':main()
