"""Read-only inspect exact Host001 container after guarded-create harness failure."""
import hashlib,json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());PRE=json.loads((B/'host-execution-preflight.json').read_text());ST=json.loads((B/'host001-stage.json').read_text());IMG=json.loads((B/'host-image-import-verified.json').read_text())
RUN=P['identifiers']['run'];NODE=P['target_native2_node_id'];NAME=P['identifiers']['containers'][1];ROOT=P['identifiers']['root'];TAG=P['identifiers']['image_tag'];EXPECTED=PRE['remote']['native2_agent'];TREE_SHA=hashlib.sha256(json.dumps(PRE['systemd_tree_before_host_write'],sort_keys=True,separators=(',',':')).encode()).hexdigest()

def main():
 src=r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
RUN=%r;NAME=%r;ROOT=%r;TAG=%r;EXPECT=%s;TREE_EXPECT=%r;NET=%r;VOL=%r

def run(a):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=20);return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()[:250]}
 except Exception as e:return {'rc':-1,'error':type(e).__name__}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'argv':argv,'exe':exe,'exe_sha256':sha(p/'exe'),'cgroup':(p/'cgroup').read_text().strip()}
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
agent=proc(EXPECT['pid'])
if any(agent[k]!=EXPECT[k] for k in ('pid','starttime','uid','argv','exe_sha256','cgroup')):raise RuntimeError('native2_owner_changed')
props=run(['systemctl','show','nodesteer-agent-repair-20260930-101945-native-2.service','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath']);kv=dict(x.split('=',1) for x in props['out'].splitlines() if '=' in x)
if kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=agent['pid']:raise RuntimeError('native2_unit_changed')
sd=tree('/etc/systemd/system');sdh=hashlib.sha256(json.dumps(sd,sort_keys=True,separators=(',',':')).encode()).hexdigest()
cp=run(['docker','container','inspect',NAME]);container=None
if cp['rc']==0:
 x=json.loads(cp['out'])[0];h=x.get('HostConfig') or {};cfg=x.get('Config') or {};state=x.get('State') or {}
 container={'id':x.get('Id'),'name':x.get('Name'),'image_id':x.get('Image'),'labels':cfg.get('Labels') or {},'env_names':sorted([v.split('=',1)[0] for v in cfg.get('Env') or []]),'state':{k:state.get(k) for k in ('Status','Running','Pid','ExitCode','Error')},'hostconfig':{'privileged':h.get('Privileged'),'cap_drop':h.get('CapDrop'),'cap_add':h.get('CapAdd'),'pid_mode':h.get('PidMode'),'read_only_rootfs':h.get('ReadonlyRootfs'),'security_opt':h.get('SecurityOpt'),'network_mode':h.get('NetworkMode'),'port_bindings':h.get('PortBindings'),'restart_policy':h.get('RestartPolicy'),'devices':h.get('Devices')},'mounts':[{'type':m.get('Type'),'source':m.get('Source'),'destination':m.get('Destination'),'rw':m.get('RW')} for m in x.get('Mounts',[])],'networks':{k:{'network_id':v.get('NetworkID')} for k,v in (x.get('NetworkSettings',{}).get('Networks') or {}).items()}}
process=proc(container['state']['Pid']) if container and container['state']['Running'] and container['state']['Pid'] else None
img=run(['docker','image','inspect',TAG]);image=None
if img['rc']==0:
 x=json.loads(img['out'])[0];cfg=x.get('Config') or {};image={'id':x.get('Id'),'tags':x.get('RepoTags'),'owner_label':(cfg.get('Labels') or {}).get('nodesteer.e2e.owner'),'entrypoint':cfg.get('Entrypoint')}
net=run(['docker','network','inspect',NET]);network=None
if net['rc']==0:
 x=json.loads(net['out'])[0];network={'id':x.get('Id'),'name':x.get('Name'),'labels':x.get('Labels'),'container_ids':list((x.get('Containers') or {}).keys())}
vol=run(['docker','volume','inspect',VOL]);volume=None
if vol['rc']==0:
 x=json.loads(vol['out'])[0];volume={'name':x.get('Name'),'labels':x.get('Labels'),'driver':x.get('Driver')}
root=Path(ROOT);rs=root.lstat();owner=json.loads((root/'.owner.json').read_text());app=Path(ROOT+'/host001/app');config=Path(ROOT+'/private/host001-agent.yaml')
print(json.dumps({'status':'PASS','native2_agent':agent,'native2_unit':kv,'container_inspect_rc':cp['rc'],'container_inspect_stderr':cp['err'],'container':container,'process':process,'image':image,'network':network,'volume':volume,'root':{'dev':rs.st_dev,'ino':rs.st_ino,'uid':rs.st_uid,'mode':oct(stat.S_IMODE(rs.st_mode)),'owner':owner},'app_root':{'dev':app.stat().st_dev,'ino':app.stat().st_ino,'uid':app.stat().st_uid,'mode':oct(stat.S_IMODE(app.stat().st_mode))},'config':{'dev':config.stat().st_dev,'ino':config.stat().st_ino,'uid':config.stat().st_uid,'mode':oct(stat.S_IMODE(config.stat().st_mode)),'sha256':sha(config)},'systemd_tree_sha256':sdh,'systemd_matches_before':sdh==TREE_EXPECT,'task_probe_read_only':True},separators=(',',':')))
'''%(RUN,NAME,ROOT,TAG,json.dumps(EXPECTED,separators=(',',':')),TREE_SHA,P['identifiers']['bridge'],P['identifiers']['volumes'][1])
 command='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(command,'host001-container-postfailure-readonly-probe');task=api.create_task('qa-resume-20261010-host-tls-host001-container-postfailure-probe-f174890c3846',command,NODE)
 try:r=api.run_task(task)
 finally:c=api.cleanup_task(task['id'],NODE)
 if r.get('status')!='SUCCESS':raise RuntimeError('readonly_container_probe_failed')
 remote=json.loads(r.get('stdout') or '');stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
 api.write_json('host001-container-postfailure-probe.json',{'status':'PASS','actual_at':stamp,'task_id':task['id'],'execution_id':r['id'],'task_cleanup':c,'remote':remote})
 p=B/'progress.json';d=json.loads(p.read_text());d.update({'actual_at':stamp,'status':'HOST001_CONTAINER_POSTFAILURE_RECONCILED','stage':'container-create-partial-state-readonly-reconciled','current_operation':'Container-start Task failed at post-create security assertion; no retry. Exact container, image/network/volume/root/config, dynamic process owner and systemd metadata were re-read via read-only Native2 Task and filtered Docker inspect JSON; probe Task DELETE/GET404.','next_action':'Compare actual container caps/security/mounts with Root approval; if exact safe config, start existing container by immutable ID after fresh owner guard; never create another. If invalid, do not start; exact-remove only this approved owned container then correct harness before one recreate.','persisted_evidence':sorted(set(d.get('persisted_evidence',[])+[str((B/'host001-container-start-failed.json').resolve()),str((B/'host001-container-postfailure-probe.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',d)
 print(json.dumps({'status':'PASS','at':stamp,'container':None if remote.get('container') is None else {'id':remote['container']['id'],'state':remote['container']['state'],'pid_mode':remote['container']['hostconfig']['pid_mode'],'caps_add':remote['container']['hostconfig']['cap_add'],'caps_drop':remote['container']['hostconfig']['cap_drop'],'security_opt':remote['container']['hostconfig']['security_opt'],'mount_count':len(remote['container']['mounts'])},'process':remote.get('process'),'container_inspect_rc':remote['container_inspect_rc'],'systemd_unchanged':remote.get('systemd_matches_before'),'task_get404':c.get('get_after_delete_http')==404}))
if __name__=='__main__':main()
