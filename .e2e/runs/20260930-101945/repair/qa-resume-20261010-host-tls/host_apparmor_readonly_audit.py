"""Read-only Native2 AppArmor tooling/source/profile/audit probe for exact Host001."""
import hashlib,json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;P=json.loads((B/'host-fixture-plan-proposal.json').read_text());REC=json.loads((B/'host001-container-running-recovered.json').read_text());NODE=P['target_native2_node_id'];CID=REC['container']['id'];PID=REC['process']['pid'];START=REC['process']['starttime'];RUN=P['identifiers']['run'];

def main():
 src=r'''import hashlib,json,os,re,shutil,stat,subprocess,time
from pathlib import Path
PID=%d;START=%d;CID=%r;RUN=%r

def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def run(a,timeout=15):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=timeout);return {'rc':p.returncode,'first_line':(p.stdout.strip().splitlines() or [''])[0][:240],'stderr_chars':len(p.stderr)}
 except Exception as e:return {'error':type(e).__name__}
def read(p):
 try:return Path(p).read_text().strip()
 except Exception as e:return type(e).__name__
def proc(pid):
 p=Path('/proc')/str(pid);raw=(p/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);st=(p/'status').read_text().splitlines();values={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in st if ':' in x};return {'pid':pid,'starttime':start,'uid':int(values.get('Uid','0').split()[0]),'cap_eff':values.get('CapEff'),'cap_bnd':values.get('CapBnd'),'no_new_privs':values.get('NoNewPrivs'),'seccomp':values.get('Seccomp'),'apparmor_current':read(p/'attr/current')}
def field(msg,key):
 m=re.search(r'(?:^|\\s)'+re.escape(key)+r'="([^"]*)"',msg)
 if m:return m.group(1)
 m=re.search(r'(?:^|\\s)'+re.escape(key)+r'=([^\\s]+)',msg)
 return m.group(1) if m else None
p=proc(PID)
if p['starttime']!=START or CID not in (Path('/proc')/str(PID)/'cgroup').read_text():raise RuntimeError('host001_pid_identity_changed')
info=run(['docker','info','--format','{{json .SecurityOptions}}']);insp=subprocess.run(['docker','container','inspect',CID],text=True,capture_output=True,timeout=15)
if insp.returncode:raise RuntimeError('container_inspect_failed')
x=json.loads(insp.stdout)[0];cfg=x.get('Config') or {};container_profile=read(Path('/proc')/str(PID)/'attr/current')
parser=shutil.which('apparmor_parser');aastatus=shutil.which('aa-status')
tools={'apparmor_parser':None if not parser else {'path':parser,'version':run([parser,'--version'])},'aa_status':None if not aastatus else {'path':aastatus,'enabled':run([aastatus,'--enabled'])},'docker_security_options':info}
loaded=[]
q=Path('/sys/kernel/security/apparmor/profiles')
if q.is_file():
 for line in q.read_text().splitlines():
  if 'docker-default' in line or 'nodesteer-qa-host-'+RUN.rsplit('-',1)[1] in line:loaded.append(line.split(' ',1)[0:2])
source_dirs=['/etc/apparmor.d','/etc/apparmor.d/abstractions','/usr/share/apparmor','/usr/share/docker','/usr/libexec/docker']
candidate_names=[]
for root in source_dirs:
 d=Path(root)
 if d.is_dir():
  try:
   for e in os.scandir(d):
    if re.search(r'docker|container|nodesteer',e.name,re.I):
     s=e.stat(follow_symlinks=False);candidate_names.append({'path':e.path,'type':'symlink' if e.is_symlink() else 'dir' if e.is_dir(follow_symlinks=False) else 'file','mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid,'size':s.st_size if stat.S_ISREG(s.st_mode) else None,'sha256':sha(e.path) if stat.S_ISREG(s.st_mode) else None})
  except PermissionError:pass
exact_sources=[]
for name in ['/etc/apparmor.d/docker','/etc/apparmor.d/containers/docker-default','/etc/apparmor.d/local/docker','/usr/share/docker.io/contrib/apparmor/docker-default','/usr/share/docker/engine/apparmor/docker-default']:
 f=Path(name)
 if f.exists() and f.is_file():exact_sources.append({'path':name,'mode':oct(stat.S_IMODE(f.stat().st_mode)),'uid':f.stat().st_uid,'size':f.stat().st_size,'sha256':sha(name)})
profiles=run(['dpkg-query','-W','-f=${binary:Package} ${Version}','apparmor','apparmor-utils','docker.io','docker-ce'],20)
# Search only kernel journal within the smoke interval; emit metadata fields, never the original line.
j=run(['journalctl','-k','--since','2026-10-10 02:50:00 UTC','--until','2026-10-10 03:05:00 UTC','--no-pager','-o','json'],25);matches=[];denied=0
if j['rc']==0:
 raw=subprocess.run(['journalctl','-k','--since','2026-10-10 02:50:00 UTC','--until','2026-10-10 03:05:00 UTC','--no-pager','-o','json'],text=True,capture_output=True,timeout=25).stdout
 for line in raw.splitlines():
  try:row=json.loads(line);msg=row.get('MESSAGE','')
  except Exception:continue
  if 'apparmor="DENIED"' not in msg:continue
  prof=field(msg,'profile');op=field(msg,'operation');name=field(msg,'name');pid=field(msg,'pid');comm=field(msg,'comm')
  is_target=(pid==str(PID)) or (prof=='docker-default' and (name or '').startswith('/proc/1/root')) or (prof=='docker-default' and comm=='nsenter')
  if not is_target:continue
  denied+=1
  matches.append({'timestamp_usec':row.get('__REALTIME_TIMESTAMP'),'profile':prof,'operation':op,'name':name,'pid':pid,'comm':comm,'requested_mask':field(msg,'requested_mask'),'denied_mask':field(msg,'denied_mask'),'target_exact_pid':pid==str(PID)})
print(json.dumps({'status':'PASS','native2_tools':tools,'host_pid1_apparmor_current':read('/proc/1/attr/current'),'container_pid':p,'container_profile':container_profile,'docker_container_apparmor_profile':x.get('AppArmorProfile'),'loaded_matching_profiles':loaded,'source_directories_checked':source_dirs,'filtered_candidate_source_metadata':candidate_names,'exact_docker_default_source_candidates':exact_sources,'package_versions':profiles,'yama_ptrace_scope':read('/proc/sys/kernel/yama/ptrace_scope'),'journal_query':{'rc':j['rc'],'stderr_chars':j.get('stderr_chars'),'time_range_utc':['2026-10-10T02:50:00Z','2026-10-10T03:05:00Z'],'denied_records_relevant_count':denied,'filtered_records':matches},'no_policy_change':True,'no_container_action':True},separators=(',',':')))
'''%(PID,START,CID,RUN)
 cmd='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(cmd,'apparmor-source-audit-readonly');task=api.create_task('qa-resume-20261010-host-tls-apparmor-source-audit-f174890c3846',cmd,NODE)
 try:r=api.run_task(task,timeout=90)
 finally:c=api.cleanup_task(task['id'],NODE)
 if r.get('status')!='SUCCESS':
  e=api.get_json('/api/executions/'+r['id']);api.write_json('host-apparmor-readonly-audit-failed.json',{'status':'FAILED','task_id':task['id'],'execution_id':r['id'],'stdout':e.get('stdout',''),'stderr':e.get('stderr',''),'cleanup':c});raise RuntimeError('apparmor_readonly_audit_task_failed')
 remote=json.loads(r.get('stdout') or '');stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('host-apparmor-readonly-audit.json',{'status':'PASS','actual_at':stamp,'task_id':task['id'],'execution_id':r['id'],'task_cleanup':c,'remote':remote})
 p=B/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'status':'APPARMOR_READONLY_AUDIT_COMPLETE','stage':'apparmor-tool-policy-source-and-denial-audit-complete','current_operation':'Read-only Native2 audit captured installed AppArmor tools/versions, loaded Host/container profile, filtered candidate source metadata, Yama setting and exact container/time-window AppArmor DENIED records with only selected fields. No parser load, policy write, container operation, or profile change.','next_action':'If docker-default source is unavailable or not derivable, report no safe profile patch can be prepared and leave runtime blocked; otherwise build only a local evidence preview for Root review, never apply it.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+[str((B/'host-apparmor-readonly-audit.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',v)
 print(json.dumps({'status':'PASS','at':stamp,'parser':remote['native2_tools']['apparmor_parser'],'aa_status':remote['native2_tools']['aa_status'],'host_pid1_profile':remote['host_pid1_apparmor_current'],'container_profile':remote['container_profile'],'docker_profile':remote['docker_container_apparmor_profile'],'source_candidates':remote['exact_docker_default_source_candidates'],'candidate_files':remote['filtered_candidate_source_metadata'],'yama':remote['yama_ptrace_scope'],'audit_count':remote['journal_query']['denied_records_relevant_count'],'audit_records':remote['journal_query']['filtered_records'],'task_get404':c.get('get_after_delete_http')==404},ensure_ascii=False))
if __name__=='__main__':main()
