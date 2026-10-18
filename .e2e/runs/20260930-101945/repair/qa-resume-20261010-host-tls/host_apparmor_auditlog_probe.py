"""Read-only check for independent auditd records and installed Docker AppArmor source paths."""
import json,pathlib,sys,time
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent));import api
B=pathlib.Path(__file__).resolve().parent;NODE='611c02a5-1859-4465-878b-d543b7f6fdec';PID=json.loads((B/'host001-container-running-recovered.json').read_text())['process']['pid'];RUN=json.loads((B/'host-fixture-plan-proposal.json').read_text())['identifiers']['run']

def main():
 src=r'''import datetime,hashlib,json,os,re,shutil,stat,subprocess
from pathlib import Path
PID=%d;RUN=%r

def run(a,timeout=20):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=timeout);return {'rc':p.returncode,'out':p.stdout,'stderr_chars':len(p.stderr)}
 except Exception as e:return {'error':type(e).__name__}
def safe_fields(line):
 out={}
 for key in ('apparmor','operation','profile','name','pid','comm','requested_mask','denied_mask','type','syscall','success','exit'):
  m=re.search(r'(?:^|\\s)'+re.escape(key)+r'="([^"]*)"',line)
  if not m:m=re.search(r'(?:^|\\s)'+re.escape(key)+r'=([^\\s]+)',line)
  if m:out[key]=m.group(1)
 return out
files=[];total=0
adir=Path('/var/log/audit')
if adir.is_dir():
 for p in sorted(adir.glob('audit.log*')):
  if p.is_file():
   s=p.stat();files.append({'name':p.name,'size':s.st_size,'mode':oct(stat.S_IMODE(s.st_mode)),'uid':s.st_uid});total+=s.st_size
matches=[];scanned=False
start=datetime.datetime.fromisoformat('2026-10-10T02:50:00+00:00').timestamp();end=datetime.datetime.fromisoformat('2026-10-10T03:05:00+00:00').timestamp()
if total<=50000000:
 for f in files:
  p=adir/f['name']
  try:
   with p.open('r',errors='replace') as h:
    for line in h:
     tm=re.search(r'msg=audit\\((\\d+(?:\\.\\d+)?):',line)
     if not tm:continue
     ts=float(tm.group(1))
     if not (start<=ts<=end):continue
     fields=safe_fields(line)
     if fields.get('pid')==str(PID) or (fields.get('profile')=='docker-default' and fields.get('operation') and ((fields.get('name') or '').startswith('/proc/1/root') or fields.get('comm')=='nsenter')):
      matches.append({'timestamp_utc':datetime.datetime.fromtimestamp(ts,datetime.timezone.utc).isoformat(),'fields':fields})
  except OSError:pass
 scanned=True
# Inventory only matching installed file names from package manifests; never dump package content.
pkgs={}
for pkg in ('docker-ce','apparmor','apparmor-utils'):
 r=run(['dpkg-query','-L',pkg]);pkgs[pkg]=[line for line in r.get('out','').splitlines() if re.search(r'apparmor|docker-default',line,re.I)][:80] if r.get('rc')==0 else []
src=[]
for root in ('/usr/lib/apparmor.d','/usr/share/docker','/usr/libexec/docker','/etc/apparmor.d'):
 d=Path(root)
 if d.is_dir():
  try:
   for e in os.scandir(d):
    if re.search(r'docker|container|nodesteer',e.name,re.I):
     st=e.stat(follow_symlinks=False);src.append({'path':e.path,'type':'symlink' if e.is_symlink() else 'directory' if e.is_dir(follow_symlinks=False) else 'file','size':st.st_size if stat.S_ISREG(st.st_mode) else None,'sha256':hashlib.sha256(Path(e.path).read_bytes()).hexdigest() if stat.S_ISREG(st.st_mode) else None})
  except OSError:pass
print(json.dumps({'status':'PASS','audit_tools':{x:shutil.which(x) for x in ('auditctl','ausearch','auditd')},'audit_log_files':files,'audit_log_total_bytes':total,'audit_logs_scanned':scanned,'filtered_relevant_denials':matches,'package_filelists_filtered':pkgs,'candidate_source_metadata':src,'pid':PID,'time_window_utc':['2026-10-10T02:50:00Z','2026-10-10T03:05:00Z'],'no_full_log_or_policy_content_emitted':True},separators=(',',':')))
'''%(PID,RUN)
 cmd='python3 - <<\'PY\'\n'+src.rstrip()+'\nPY';api.validate_command(cmd,'apparmor-audit-log-readonly');task=api.create_task('qa-resume-20261010-host-tls-apparmor-auditlog-readonly-f174890c3846',cmd,NODE)
 try:r=api.run_task(task,timeout=75)
 finally:c=api.cleanup_task(task['id'],NODE)
 if r.get('status')!='SUCCESS':raise RuntimeError('auditlog_probe_task_failed')
 remote=json.loads(r.get('stdout') or '');stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('host-apparmor-auditlog-probe.json',{'status':'PASS','actual_at':stamp,'task_id':task['id'],'execution_id':r['id'],'task_cleanup':c,'remote':remote})
 p=B/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'status':'APPARMOR_SOURCE_AND_AUDIT_SEARCH_COMPLETE','stage':'apparmor-auditlog-and-package-source-check-complete','current_operation':'Checked exact auditd log file metadata/content-filtered events and filtered dpkg file manifests for an installed Docker default-profile source. No policy file content was dumped and no policy was loaded.','next_action':'If no authentic docker-default source is present, report no safe local policy patch can be prepared; keep container and mark runtime partial pending Root boundary decision.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+[str((B/'host-apparmor-auditlog-probe.json').resolve()),str((B/('task-cleanup-'+task['id']+'.json')).resolve())]))});api.write_json('progress.json',v)
 print(json.dumps({'status':'PASS','at':stamp,'audit_tools':remote['audit_tools'],'audit_files':remote['audit_log_files'],'audit_matches':remote['filtered_relevant_denials'],'package_policy_paths':remote['package_filelists_filtered'],'candidate_sources':remote['candidate_source_metadata'],'task_get404':c.get('get_after_delete_http')==404},ensure_ascii=False))
if __name__=='__main__':main()
