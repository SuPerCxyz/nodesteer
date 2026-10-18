import hashlib,json,os,stat,subprocess,time
from pathlib import Path
EXPECTED_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';NODE='37989297-3bc8-4c0c-b302-7c6efbdba630';AGENT='agent-bb005487-7d84-418c-b598-f93d8b0c86a0'
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();status=(q/'status').read_text().splitlines();uid=int(next(x for x in status if x.startswith('Uid:')).split()[1]);ppid=int(tail[1]);exe=os.readlink(q/'exe');return {'pid':pid,'ppid':ppid,'startticks':int(tail[19]),'uid':uid,'exe_path':exe,'exe_basename':Path(exe.removesuffix(' (deleted)')).name,'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip(),'argv':[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x]}
selfpid=os.getpid();chain=[];cur=os.getppid();agent=None
for _ in range(32):
 if cur<=1:break
 try:p=proc(cur)
 except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError):break
 chain.append({k:p[k] for k in ['pid','ppid','startticks','uid','exe_basename','exe_sha256','cgroup']})
 if p['exe_sha256']==EXPECTED_SHA and 'nodesteer-agent' in p['exe_basename']:
  agent=p;break
 cur=p['ppid']
if agent is None:
 print(json.dumps({'status':'AGENT_SHA_NOT_FOUND_IN_SELF_ANCESTORS','node_id':NODE,'agent_id':AGENT,'task_pid':selfpid,'ancestor_chain':chain,'expected_sha256':EXPECTED_SHA,'argv_env_emitted':False},separators=(',',':')),flush=True);raise SystemExit(2)
# Expose only recognized config path; never output arbitrary argv or any environment values.
config=None
for i,arg in enumerate(agent['argv']):
 if arg in ('--config','-config','--config-file') and i+1<len(agent['argv']):config=agent['argv'][i+1]
 if arg.startswith('--config='):config=arg.split('=',1)[1]
exe_disk=agent['exe_path'].removesuffix(' (deleted)');disk_sha=sha(exe_disk) if os.path.isfile(exe_disk) else None;disk_stat=None
if disk_sha is not None:
 st=os.stat(exe_disk);disk_stat={'dev':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode))}
parts=[x for x in agent['cgroup'].split('/') if x];unit=next((x for x in reversed(parts) if x.endswith(('.service','.scope'))),None);systemd={'available':False}
if unit:
 try:
  cp=subprocess.run(['systemctl','show',unit,'--no-pager','--property=Id,ActiveState,MainPID,ControlGroup'],capture_output=True,text=True,timeout=5,check=False);props={}
  for line in cp.stdout.splitlines():
   if '=' in line:
    k,v=line.split('=',1);props[k]=v
  systemd={'available':cp.returncode==0,'returncode':cp.returncode,'id':props.get('Id'),'active_state':props.get('ActiveState'),'main_pid':int(props['MainPID']) if props.get('MainPID','').isdigit() else None,'control_group':props.get('ControlGroup')}
 except (FileNotFoundError,subprocess.TimeoutExpired):systemd={'available':False,'reason':'systemctl unavailable or timed out'}
out={'status':'NATIVE3_REPAIR_AGENT_ANCESTOR_SHA_CHECK','node_id':NODE,'agent_id':AGENT,'task_pid':selfpid,'ancestor_depth':len(chain),'ancestor_summary':chain,'agent_process':{'pid':agent['pid'],'ppid':agent['ppid'],'startticks':agent['startticks'],'uid':agent['uid'],'exe_path':agent['exe_path'],'proc_exe_sha256':agent['exe_sha256'],'disk_path':exe_disk,'disk_sha256':disk_sha,'disk_stat':disk_stat,'config_path_only':config,'cgroup':agent['cgroup'],'unit_from_cgroup':unit,'systemd_readonly':systemd,'proc_and_disk_sha_match_expected':agent['exe_sha256']==EXPECTED_SHA and disk_sha==EXPECTED_SHA},'expected_manifest_sha256':EXPECTED_SHA,'manifest_source':'.e2e/runs/20260930-101945/repair/build-manifest.json artifacts[nodesteer-agent-static]','full_nonagent_argv_emitted':False,'environment_read_or_emitted':False}
print(json.dumps(out,separators=(',',':')),flush=True)