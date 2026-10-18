"""Read-only verification of the exact rootfs/image after inspect-harness failure."""
import hashlib
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import api

B = pathlib.Path(__file__).resolve().parent
PLAN = json.loads((B / 'host-fixture-plan-proposal.json').read_text())
PREF = json.loads((B / 'host-execution-preflight.json').read_text())
RUN = PLAN['identifiers']['run']
NODE = PLAN['target_native2_node_id']
ROOT = PLAN['identifiers']['root']
TAG = PLAN['identifiers']['image_tag']
EXPECTED = PREF['remote']['native2_agent']
TREE_EXPECT = hashlib.sha256(json.dumps(PREF['systemd_tree_before_host_write'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    source = r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
ROOT=%r;TAG=%r;RUN=%r;EXPECT=%s;TREE_EXPECT=%r

def run(a):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=20)
  return {'rc':p.returncode,'out':p.stdout.strip(),'err':p.stderr.strip()[:400]}
 except Exception as e:return {'rc':-1,'err_type':type(e).__name__}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);exe=os.readlink(p/'exe');argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'starttime':start,'uid':uid,'argv':argv,'exe':exe,'exe_sha256':sha(exe),'cgroup':(p/'cgroup').read_text().strip()}
def tree(root):
 out=[]
 for par,ds,fs in os.walk(root,followlinks=False):
  ds.sort();fs.sort()
  for n in ds+fs:
   p=Path(par)/n;s=p.lstat();r={'path':str(p.relative_to(root)),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'dev':s.st_dev,'ino':s.st_ino,'type':'dir' if stat.S_ISDIR(s.st_mode) else 'link' if stat.S_ISLNK(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other'}
   if stat.S_ISREG(s.st_mode):r.update({'size':s.st_size,'sha256':sha(p)})
   elif stat.S_ISLNK(s.st_mode):r['link_sha256']=hashlib.sha256(os.readlink(p).encode()).hexdigest()
   out.append(r)
 return out
agent=proc(EXPECT['pid']);props=run(['systemctl','show','nodesteer-agent-repair-20260930-101945-native-2.service','-p','LoadState','-p','ActiveState','-p','MainPID','-p','FragmentPath']);kv=dict(x.split('=',1) for x in props['out'].splitlines() if '=' in x)
if any(agent[k]!=EXPECT[k] for k in ('pid','starttime','uid','argv','exe_sha256','cgroup')) or kv.get('ActiveState')!='active' or int(kv.get('MainPID','0'))!=agent['pid']:raise RuntimeError('native2_owner_changed')
sd=[]
for par,ds,fs in os.walk('/etc/systemd/system',followlinks=False):
 ds.sort();fs.sort()
 for n in ds+fs:
  p=Path(par)/n;s=p.lstat();r={'path':str(p.relative_to('/etc/systemd/system')),'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'gid':s.st_gid,'dev':s.st_dev,'ino':s.st_ino,'type':'dir' if stat.S_ISDIR(s.st_mode) else 'symlink' if stat.S_ISLNK(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'other'}
  if stat.S_ISREG(s.st_mode):r.update({'size':s.st_size,'sha256':sha(p)})
  elif stat.S_ISLNK(s.st_mode):r['sha256']=hashlib.sha256(os.readlink(p).encode()).hexdigest()
  sd.append(r)
sdh=hashlib.sha256(json.dumps(sd,sort_keys=True,separators=(',',':')).encode()).hexdigest();root=Path(ROOT);owner=None
if (root/'.owner.json').is_file():owner=json.loads((root/'.owner.json').read_text())
rootinfo=None
if root.exists():
 s=root.lstat();rootinfo={'dev':s.st_dev,'ino':s.st_ino,'uid':s.st_uid,'mode':oct(stat.S_IMODE(s.st_mode)),'tree':tree(root),'owner':owner}
inspect=run(['docker','image','inspect',TAG]);image_info=None
if inspect['rc']==0:
 arr=json.loads(inspect['out']);x=arr[0];cfg=x.get('Config') or {};image_info={'id':x.get('Id'),'tags':x.get('RepoTags'),'size':x.get('Size'),'owner_label':(cfg.get('Labels') or {}).get('nodesteer.e2e.owner'),'entrypoint':cfg.get('Entrypoint'),'cmd':cfg.get('Cmd'),'layer_count':len((x.get('RootFS') or {}).get('Layers') or [])}
image_list=run(['docker','image','ls','--no-trunc','--format','{{json .}}']);tag_rows=[]
if image_list['rc']==0:
 for line in image_list['out'].splitlines():
  try:
   x=json.loads(line)
   if x.get('Repository')+':'+x.get('Tag')==TAG:tag_rows.append({'id':x.get('ID'),'repository':x.get('Repository'),'tag':x.get('Tag')})
  except Exception:pass
containers=run(['docker','ps','-a','--no-trunc','--format','{{json .}}']);container_rows=[]
if containers['rc']==0:
 for line in containers['out'].splitlines():
  try:
   x=json.loads(line)
   if RUN in (x.get('Names') or ''):container_rows.append({'id':x.get('ID'),'name':x.get('Names'),'labels':x.get('Labels')})
  except Exception:pass
net=run(['docker','network','inspect',RUN.replace('qa-resume-20261010-host-','nodesteer-qa-host-')]);network_info=None
if net['rc']==0:
 x=json.loads(net['out'])[0];network_info={'id':x.get('Id'),'name':x.get('Name'),'labels':x.get('Labels'),'driver':x.get('Driver'),'containers':list((x.get('Containers') or {}).keys())}
volume_results=[]
for i in range(6):
 name=RUN+'-data-'+str(i);r=run(['docker','volume','inspect',name]);
 if r['rc']==0:
  x=json.loads(r['out'])[0];volume_results.append({'name':x.get('Name'),'driver':x.get('Driver'),'labels':x.get('Labels')})
print(json.dumps({'status':'PASS','native2_agent':agent,'native2_unit':kv,'root':rootinfo,'image_inspect_argv':['docker','image','inspect',TAG],'image_inspect_rc':inspect['rc'],'image_inspect_stderr':inspect['err'],'image':image_info,'tag_rows':tag_rows,'container_rows':container_rows,'network':network_info,'volumes':volume_results,'systemd_tree_sha256':sdh,'systemd_matches_before':sdh==TREE_EXPECT,'task_code_read_only':True},separators=(',',':')))
'''
    source = source % (ROOT, TAG, RUN, json.dumps(EXPECTED, separators=(',', ':')), TREE_EXPECT)
    command = "python3 - <<'PY'\n" + source.rstrip() + "\nPY"
    api.validate_command(command, 'host-image-postfailure-readonly-probe')
    task = api.create_task('qa-resume-20261010-host-tls-image-postfailure-probe-f174890c3846', command, NODE)
    try:
        result = api.run_task(task)
    finally:
        cleanup = api.cleanup_task(task['id'], NODE)
    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if result.get('status') != 'SUCCESS':
        raise RuntimeError('readonly_postfailure_probe_task_failed')
    remote = json.loads(result.get('stdout') or '')
    api.write_json('host-image-postfailure-readonly-probe.json', {'status':'PASS','actual_at':stamp,'task_id':task['id'],'execution_id':result['id'],'cleanup':cleanup,'remote':remote})
    progress = json.loads((B/'progress.json').read_text())
    progress.update({'actual_at':stamp,'status':'HOST_IMAGE_POSTFAILURE_STATE_RECONCILED','stage':'image-import-side-effects-readonly-reconciled','current_operation':'Build Task stopped at exact image-inspect error after import; no rebuild. A separate read-only Native2 Task captured root/tar/image labels/ID/systemd metadata under dynamic owner guards and was deleted with GET404.','next_action':'Parse this read-only evidence; if exact owner image/root/tar and unchanged systemd metadata pass, create only Host001 and run the approved read-only nsenter smoke.','persisted_evidence':sorted(set(progress.get('persisted_evidence',[])+[str((B/'host-image-build-failed.json').resolve()),str((B/'host-image-postfailure-readonly-probe.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))})
    api.write_json('progress.json', progress)
    print(json.dumps({'status':'PASS','at':stamp,'root_exists':remote.get('root') is not None,'root_devino':None if not remote.get('root') else {k:remote['root'].get(k) for k in ('dev','ino','uid','mode')},'image_inspect_rc':remote['image_inspect_rc'],'image_inspect_stderr':remote['image_inspect_stderr'][:180],'image_id':None if not remote.get('image') else remote['image'].get('id'),'owner_label_matches':bool(remote.get('image') and remote['image'].get('owner_label')==RUN),'tag_rows':remote.get('tag_rows'),'systemd_unchanged':remote.get('systemd_matches_before'),'container_rows':remote.get('container_rows'),'network_exists':remote.get('network') is not None,'volumes':remote.get('volumes'),'task_get404':cleanup.get('get_after_delete_http')==404}))

if __name__ == '__main__':
    main()
