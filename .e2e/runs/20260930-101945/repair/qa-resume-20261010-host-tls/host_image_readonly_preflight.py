#!/usr/bin/env python3
"""Read-only current Host-Integration image/capability/dependency preflight on Native2."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text());NODE='611c02a5-1859-4465-878b-d543b7f6fdec';EXPECTED=PRE['remote_agent']['binary_sha256'];EXE='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
src=r'''import hashlib,json,os,shutil,stat,subprocess
from pathlib import Path
EXPECTED=%r;AGENT=%r
CAPS={0:'CHOWN',1:'DAC_OVERRIDE',2:'DAC_READ_SEARCH',3:'FOWNER',4:'FSETID',5:'KILL',6:'SETGID',7:'SETUID',8:'SETPCAP',9:'LINUX_IMMUTABLE',10:'NET_BIND_SERVICE',11:'NET_BROADCAST',12:'NET_ADMIN',13:'NET_RAW',14:'IPC_LOCK',15:'IPC_OWNER',16:'SYS_MODULE',17:'SYS_RAWIO',18:'SYS_CHROOT',19:'SYS_PTRACE',20:'SYS_PACCT',21:'SYS_ADMIN',22:'SYS_BOOT',23:'SYS_NICE',24:'SYS_RESOURCE',25:'SYS_TIME',26:'SYS_TTY_CONFIG',27:'MKNOD',28:'LEASE',29:'AUDIT_WRITE',30:'AUDIT_CONTROL',31:'SETFCAP',32:'MAC_OVERRIDE',33:'MAC_ADMIN',34:'SYSLOG',35:'WAKE_ALARM',36:'BLOCK_SUSPEND',37:'AUDIT_READ',38:'PERFMON',39:'BPF',40:'CHECKPOINT_RESTORE'}
def run(a,timeout=15):
 try:
  p=subprocess.run(a,text=True,capture_output=True,timeout=timeout)
  return {'returncode':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[:300]}
 except Exception as e:return {'error_type':type(e).__name__}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
rows=[]
for d in Path('/proc').iterdir():
 if not d.name.isdigit():continue
 try:
  exe=os.readlink(d/'exe')
  if exe==AGENT and sha(exe)==EXPECTED:
   raw=(d/'stat').read_text();start=int(raw[raw.rfind(')')+2:].split()[19]);status=(d/'status').read_text().splitlines();uid=int(next(x for x in status if x.startswith('Uid:')).split()[1]);vals={x.split(':',1)[0]:x.split(':',1)[1].strip() for x in status if x.startswith(('CapEff:','CapBnd:'))};
   def decoded(key):
    n=int(vals.get(key,'0'),16);return [v for bit,v in CAPS.items() if n&(1<<bit)]
   rows.append({'pid':int(d.name),'starttime':start,'uid':uid,'exe_sha256':sha(exe),'cgroup':(d/'cgroup').read_text().strip(),'cap_eff_hex':vals.get('CapEff'),'cap_eff_names':decoded('CapEff'),'cap_bnd_names':decoded('CapBnd')})
 except (FileNotFoundError,ProcessLookupError,PermissionError):pass
if len(rows)!=1:raise RuntimeError('repair_agent_identity_count_unexpected')
docker=shutil.which('docker');nsenter=shutil.which('nsenter');agent=Path(AGENT)
def ldd(path):
 r=run(['ldd',path],10);return {'returncode':r.get('returncode'),'stdout_lines':(r.get('stdout') or '').splitlines(),'stderr':r.get('stderr','')}
image=run([docker,'image','ls','--no-trunc','--format','{{.ID}} {{.Repository}}:{{.Tag}}']) if docker else {'error_type':'docker_missing'}
net=run([docker,'network','ls','--format','{{.ID}} {{.Name}} {{.Driver}} {{.Scope}}']) if docker else {'error_type':'docker_missing'}
info=run([docker,'info','--format','{{.ServerVersion}}|{{.DockerRootDir}}|{{.Driver}}|{{json .SecurityOptions}}']) if docker else {'error_type':'docker_missing'}
sock=Path('/var/run/docker.sock');sockinfo=None
if sock.exists():
 s=sock.stat();sockinfo={'uid':s.st_uid,'gid':s.st_gid,'mode':oct(stat.S_IMODE(s.st_mode)),'is_socket':stat.S_ISSOCK(s.st_mode)}
roots=[]
for p in ('/tmp','/var/tmp','/opt'):
 q=Path(p);v=shutil.disk_usage(q);st=q.stat();roots.append({'path':p,'realpath':str(q.resolve()),'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode)),'free_bytes':v.free})
ns={}
for p in ('/proc/1/ns/pid','/proc/self/ns/pid','/proc/1/ns/mnt','/proc/self/ns/mnt'):
 try:ns[p]=os.readlink(p)
 except OSError as e:ns[p]=type(e).__name__
libs={p:ldd(p) for p in (AGENT,nsenter,'/bin/sh') if p and Path(p).exists()}
print(json.dumps({'status':'PASS','repair_agent':rows[0],'docker':{'binary':docker,'info':info,'image_list':{'returncode':image.get('returncode'),'rows':[x for x in (image.get('stdout') or '').splitlines() if x],'stderr':image.get('stderr','')},'networks':{'returncode':net.get('returncode'),'rows':[x for x in (net.get('stdout') or '').splitlines() if x],'stderr':net.get('stderr','')},'socket':sockinfo},'nsenter':{'binary':nsenter,'version':run([nsenter,'--version']) if nsenter else None,'root_option_supported':'--root' in ((run([nsenter,'--help']).get('stdout') or '') if nsenter else '')},'namespace_ids':ns,'required_runtime_binary_ldd':libs,'writable_roots':roots,'image_or_container_created':False},separators=(',',':')))
'''%(EXPECTED,EXE)
cmd="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(cmd,'host-image-readonly-preflight')
pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text());before=[]
for x in pre['original_repair_nodes']:
 n=api.get_json('/api/nodes/'+x['node_id'])
 if n.get('status')!='online' or n.get('sync_status')!='synced':raise RuntimeError('original_four_not_healthy_before_host_preflight')
 before.append(x['node_id'])
node=api.get_json('/api/nodes/'+NODE)
if node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('native2_not_healthy_before_host_preflight')
t=api.create_task('qa-resume-20261010-host-tls-host-image-readonly-preflight',cmd,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('host_image_readonly_preflight_failed')
out=json.loads(r.get('stdout') or '')
if out.get('status')!='PASS' or out.get('image_or_container_created') is not False:raise RuntimeError('host_image_preflight_output_invalid')
node_after=api.get_json('/api/nodes/'+NODE);peers=[]
for nid in before:
 n=api.get_json('/api/nodes/'+nid);peers.append({'node_id':nid,'status':n.get('status'),'sync_status':n.get('sync_status')})
if node_after.get('status')!='online' or node_after.get('sync_status')!='synced' or not all(x['status']=='online' and x['sync_status']=='synced' for x in peers):raise RuntimeError('host_preflight_health_after_failed')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime());api.write_json('host-image-readonly-preflight.json',{'status':'PASS','actual_at':stamp,'node_id':NODE,'remote':out,'native2_after':{k:node_after.get(k) for k in ('status','sync_status','agent_version')},'original_four_nodes':peers,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
v=json.loads((HERE/'progress.json').read_text());v.update({'actual_at':stamp,'stage':'host-image-readonly-preflight-complete','current_operation':'A current read-only Native2 Host preflight recorded Docker cache/network/daemon/socket, effective capabilities and namespace IDs, current repair Agent SHA, nsenter root support, minimal runtime binary dependencies and disk roots; no image, network, container, volume, Node or Host path was created.','next_action':'Send Root exact owned-rootfs/docker-import image proposal with minimum capability/mount plan for review; keep Host resource creation pending.','persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['host-image-readonly-preflight.json','task-cleanup-'+t['id']+'.json']))});api.write_json('progress.json',v)
print(json.dumps({'status':'PASS','actual_at':stamp,'image_rows':len(out['docker']['image_list']['rows']),'network_rows':len(out['docker']['networks']['rows']),'docker_server':out['docker']['info'].get('stdout'),'repair_agent_sha256':out['repair_agent']['exe_sha256'],'cap_eff':out['repair_agent']['cap_eff_names'],'nsenter_root_supported':out['nsenter']['root_option_supported'],'task_get404':c['get_after_delete_http']==404}))
