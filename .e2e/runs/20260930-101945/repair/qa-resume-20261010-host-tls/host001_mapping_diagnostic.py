"""Read-only Host-side stat/mountinfo diagnosis after shell samefile smoke failed."""
import hashlib,json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());PRE=json.loads((B/'host-execution-preflight.json').read_text());LIVE=json.loads((B/'host001-container-running-recovered.json').read_text());ROOT=P['identifiers']['root'];CID=LIVE['container']['id'];PID=LIVE['process']['pid'];NODE=P['target_native2_node_id'];RUN=P['identifiers']['run'];UNIT=P['identifiers']['unit'];APP=P['identifiers']['app_root'];TREE=hashlib.sha256(json.dumps(PRE['systemd_tree_before_host_write'],sort_keys=True,separators=(',',':')).encode()).hexdigest();EXPECTED=PRE['remote']['native2_agent']

def main():
 src=r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
CID=%r;PID=%d;RUN=%r;UNIT=%r;APP=%r;EXPECT=%s;TREE_EXPECT=%r

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'argv':argv,'exe':exe,'exe_sha256':sha(p/'exe'),'cgroup':(p/'cgroup').read_text().strip()}
def statpath(p):
 try:
  s=os.stat(p);return {'ok':True,'dev':s.st_dev,'ino':s.st_ino,'uid':s.st_uid,'mode':oct(stat.S_IMODE(s.st_mode)),'realpath':os.path.realpath(p)}
 except OSError as e:return {'ok':False,'error':type(e).__name__,'errno':e.errno}
p=proc(PID)
if p['uid']!=0 or p['exe_sha256']!=EXPECT['exe_sha256'] or CID not in p['cgroup']:raise RuntimeError('host001_process_owner_changed')
docker=subprocess.run(['docker','container','inspect',CID],text=True,capture_output=True,timeout=15)
if docker.returncode:raise RuntimeError('exact_container_inspect_failed')
x=json.loads(docker.stdout)[0];cfg=x.get('Config') or {};state=x.get('State') or {};labels=cfg.get('Labels') or {}
if x.get('Id')!=CID or labels.get('nodesteer-e2e-run')!=RUN or labels.get('nodesteer.e2e.role')!='host001' or state.get('Running') is not True or int(state.get('Pid',0))!=PID:raise RuntimeError('container_identity_or_state_changed')
cr=f'/proc/{PID}/root';pairs={
 'systemd_container':cr+'/host/etc/systemd/system',
 'systemd_hostview':cr+'/proc/1/root/etc/systemd/system',
 'app_container':cr+'/host'+APP,
 'app_hostview':cr+'/proc/1/root'+APP,
 'hostview_proc1_root':cr+'/proc/1/root',
 'hostview_proc1_systemd':cr+'/proc/1/root/etc/systemd/system',
}
paths={k:statpath(v) for k,v in pairs.items()}
links={}
for k,v in {'container_mntns':'/proc/'+str(PID)+'/ns/mnt','host_pid1_mntns':'/proc/1/ns/mnt','container_proc1_mntns':cr+'/proc/1/ns/mnt','container_self_mntns':cr+'/proc/self/ns/mnt','container_proc1_root':cr+'/proc/1/root'}.items():
 try:links[k]=os.readlink(v)
 except OSError as e:links[k]=type(e).__name__+':'+str(e.errno)
mountinfo=[]
for line in Path('/proc',str(PID),'mountinfo').read_text().splitlines():
 f=line.split()
 if len(f)>5 and f[4] in ['/host/etc/systemd/system','/host/etc','/host','/host'+APP,'/host/proc','/host/sys','/host/etc/os-release']:
  mountinfo.append({'mount_id':f[0],'parent_id':f[1],'major_minor':f[2],'root':f[3],'mountpoint':f[4],'options':f[5]})
sd=[]
for par,ds,fs in os.walk('/etc/systemd/system',followlinks=False):
 ds.sort();fs.sort()
 for n in ds+fs:
  q=Path(par)/n;s=q.lstat();r={'path':str(q.relative_to('/etc/systemd/system')),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'dev':s.st_dev,'ino':s.st_ino,'type':'dir' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other'}
  if stat.S_ISREG(s.st_mode):r.update({'size':s.st_size,'sha256':sha(q)})
  elif stat.S_ISLNK(s.st_mode):r['sha256']=hashlib.sha256(os.readlink(q).encode()).hexdigest()
  sd.append(r)
sdh=hashlib.sha256(json.dumps(sd,sort_keys=True,separators=(',',':')).encode()).hexdigest();unitpath='/etc/systemd/system/'+UNIT
print(json.dumps({'status':'PASS','native2_agent':proc(EXPECT['pid']),'container':{'id':x.get('Id'),'name':x.get('Name'),'image':x.get('Image'),'labels':labels,'state':{k:state.get(k) for k in ('Status','Running','Pid')},'mounts':[{'source':m.get('Source'),'destination':m.get('Destination'),'rw':m.get('RW'),'type':m.get('Type')} for m in x.get('Mounts',[])]},'host001_process':p,'paths':paths,'mount_namespace_links':links,'mountinfo':mountinfo,'samefile_systemd':paths['systemd_container'].get('dev')==paths['systemd_hostview'].get('dev') and paths['systemd_container'].get('ino')==paths['systemd_hostview'].get('ino'),'samefile_app':paths['app_container'].get('dev')==paths['app_hostview'].get('dev') and paths['app_container'].get('ino')==paths['app_hostview'].get('ino'),'systemd_tree_sha256':sdh,'systemd_tree_matches_before':sdh==TREE_EXPECT,'app_unit_exists':os.path.lexists(unitpath),'task_code_read_only':True},separators=(',',':')))
'''%(CID,PID,RUN,UNIT,APP,json.dumps(EXPECTED,separators=(',',':')),TREE)
 cmd='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(cmd,'host001-mapping-diagnostic-readonly');task=api.create_task('qa-resume-20261010-host-tls-host001-mapping-diagnostic-f174890c3846',cmd,NODE)
 try:r=api.run_task(task,timeout=60)
 finally:cl=api.cleanup_task(task['id'],NODE)
 if r.get('status')!='SUCCESS':raise RuntimeError('host001_mapping_diagnostic_task_failed')
 remote=json.loads(r.get('stdout') or '');stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());node=api.get_json('/api/nodes/'+LIVE['host001_node_id'])
 api.write_json('host001-mapping-diagnostic.json',{'status':'PASS','actual_at':stamp,'task_id':task['id'],'execution_id':r['id'],'task_cleanup':cl,'remote':remote,'hub_node':{k:node.get(k) for k in ('id','status','sync_status')}})
 pr=json.loads((B/'progress.json').read_text());pr.update({'actual_at':stamp,'status':'HOST001_MAPPING_DIAGNOSTIC','stage':'host001-samefile-smoke-discrepancy-investigated','current_operation':'Initial shell readonly smoke proved private mount namespace but shell test reported systemd bind mismatch before any nsenter or writes. A separate Native2 read-only task now compares actual container mount paths versus `/proc/1/root` using host-side os.stat, container mountinfo and live IDs/PID/process; no App/Artifact/Unit changes.','next_action':'Use the actual dev/inode and mount namespace evidence to classify shell-test false negative versus real mount mapping defect. Do not create Managed App until exact samefile/mount and nsenter read-only smoke both pass.','persisted_evidence':sorted(set(pr.get('persisted_evidence',[])+[str((B/'host001-readonly-nsenter-smoke-failed.json').resolve()),str((B/'host001-mapping-diagnostic.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',pr)
 print(json.dumps({'status':'PASS','actual_at':stamp,'samefile_systemd':remote.get('samefile_systemd'),'samefile_app':remote.get('samefile_app'),'path_stats':remote.get('paths'),'mntns':remote.get('mount_namespace_links'),'mountinfo':remote.get('mountinfo'),'systemd_unchanged':remote.get('systemd_tree_matches_before'),'app_unit_exists':remote.get('app_unit_exists'),'node_status':node.get('status'),'node_sync':node.get('sync_status'),'task_get404':cl.get('get_after_delete_http')==404},ensure_ascii=False))
if __name__=='__main__':main()
