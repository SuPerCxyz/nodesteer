#!/usr/bin/env python3
"""Install and start only the authorized owned TLS fixture child."""
import hashlib, json, os, runpy, sys, time
from pathlib import Path
import api

HERE=Path(__file__).resolve().parent
TOKEN=runpy.run_path(str(HERE/'private'/'registration-secret.py'))['TOKEN']
NODE='611c02a5-1859-4465-878b-d543b7f6fdec'
ENROLL=json.loads((HERE/'child-enrollment.json').read_text())
LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text())
PARENT=json.loads((HERE/'pre-child-owner-probe.json').read_text())
ROOT=LIVE['remote_owner']['root']; NONCE='1358f9a990bd'
UNIT='nodesteer-qa-tls-'+NONCE+'-child.service'
AGENT='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'
CONFIG=ROOT+'/private/child.yaml'; DATA=ROOT+'/agent-data'; CA=ROOT+'/private/ca.crt'
NODE_ID=ENROLL['response']['node_id']; AGENT_ID=ENROLL['response']['agent_id']
NODE_NAME=ENROLL['request']['node_name']; NODE_IP=ENROLL['request']['node_ip']
WS=ENROLL['response']['gateway_url']
CLAIMS={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':LIVE['remote_owner']['manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'parent':PARENT['remote_agent']['process'],'agent_sha':PARENT['remote_agent']['binary_sha256'],'unit':UNIT,'config':CONFIG,'data':DATA,'ca':CA,'node_id':NODE_ID,'agent_id':AGENT_ID,'node_name':NODE_NAME,'node_ip':NODE_IP,'ws':WS,'token':TOKEN}

source=r'''import hashlib,json,os,stat,subprocess,time,uuid
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';U=Path('/etc/systemd/system')/C['unit'];CFG=Path(C['config']);DATA=Path(C['data']);CA=Path(C['ca']);AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def run(a,check=True):
 r=subprocess.run(a,text=True,capture_output=True,timeout=20)
 if check and r.returncode:raise RuntimeError('command_failed:'+a[0]+':'+str(r.returncode))
 return r
def props():
 r=run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',C['unit']],False);d={}
 for line in r.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);d[k]=v
 return r,d
st=D.lstat();raw=M.read_bytes();manifest=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700):raise RuntimeError('owned_root_changed')
if hashlib.sha256(raw).hexdigest()!=C['manifest_sha'] or manifest.get('nonce')!='1358f9a990bd':raise RuntimeError('owned_manifest_changed')
if MARK.is_symlink() or hashlib.sha256(MARK.read_bytes()).hexdigest()!=C['marker_sha']:raise RuntimeError('owner_marker_changed')
# Recheck the live HTTPS server exact process and listener before any child mutation.
S=C['server'];sp=Path('/proc',str(S['pid']));ss=(sp/'stat').read_text();start=int(ss[ss.rfind(')')+2:].split()[19]);uid=int(next(x for x in (sp/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(sp/'exe'));cg=(sp/'cgroup').read_text().strip();svunit=Path('/etc/systemd/system')/S['unit']
if start!=S['starttime'] or uid!=S['uid'] or sha(exe)!=S['exe_sha256'] or cg!=S['cgroup'] or svunit.is_symlink() or sha(svunit)!=C['server_unit_sha']:raise RuntimeError('https_server_owner_changed')
sr=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',S['unit']],text=True,capture_output=True,timeout=10);sd={}
for line in sr.stdout.splitlines():
 if '=' in line:k,v=line.split('=',1);sd[k]=v
if sr.returncode or sd.get('LoadState')!='loaded' or sd.get('ActiveState')!='active' or sd.get('MainPID')!=str(S['pid']) or sd.get('FragmentPath')!=str(svunit):raise RuntimeError('https_server_unit_identity_changed')
iphex=__import__('socket').inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{S['port']:04X}";socks=[]
for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
if socks!=S['listen_socket_inodes']:raise RuntimeError('https_server_loopback_socket_owner_changed')
r,d=props()
if U.exists() or U.is_symlink() or d.get('LoadState')!='not-found':raise RuntimeError('child_unit_not_absent')
# Recheck the original Native2 repair Agent process and executable.
P=C['parent'];pp=Path('/proc',str(P['pid']));ps=(pp/'stat').read_text();pstart=int(ps[ps.rfind(')')+2:].split()[19]);pexe=os.readlink(str(pp/'exe'));puid=int(next(x for x in (pp/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);pcg=(pp/'cgroup').read_text().strip()
if pstart!=P['starttime'] or puid!=P['uid'] or pcg!=P['cgroup'] or sha(pexe)!=C['agent_sha'] or pexe!=str(AGENT):raise RuntimeError('native2_repair_agent_owner_changed')
if sha(AGENT)!=C['agent_sha']:raise RuntimeError('repair_agent_binary_changed')
for x in (D/'private',DATA):
 st=x.lstat()
 if x.is_symlink() or x.resolve().parent!=D and x!=D/'private' or st.st_uid!=0 or stat.S_IMODE(st.st_mode)!=0o700:raise RuntimeError('owned_child_directory_guard_failed')
if list(DATA.iterdir()):raise RuntimeError('owned_child_data_dir_not_empty')
if CFG.exists() or CFG.is_symlink() or CA.is_symlink() or not CA.is_file():raise RuntimeError('child_config_or_ca_path_gate_failed')
config='hub_url: '+json.dumps(C['ws'])+'\nregistration_token: '+json.dumps(C['token'])+'\nagent_id: '+json.dumps(C['agent_id'])+'\nnode_name: '+json.dumps(C['node_name'])+'\nnode_ip: '+json.dumps(C['node_ip'])+'\ndeployment_mode: "native"\nhost_integration: false\ndata_dir: '+json.dumps(str(DATA))+'\ntls_ca_file: '+json.dumps(str(CA))+'\nheartbeat_sec: 3\nrevision_check_sec: 3\n'
fd=os.open(CFG,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'w') as f:f.write(config);f.flush();os.fsync(f.fileno())
os.chown(CFG,0,0)
unit='[Unit]\nDescription=NodeSteer isolated TLS QA child '+C['node_id']+'\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nType=simple\nUser=root\nGroup=root\nExecStart='+str(AGENT)+' --config '+str(CFG)+'\nRestart=on-failure\nRestartSec=1\nNoNewPrivileges=yes\nProtectSystem=strict\nProtectHome=yes\nReadWritePaths='+str(D)+'\n\n[Install]\nWantedBy=multi-user.target\n'
fd=os.open(U,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o644)
with os.fdopen(fd,'w') as f:f.write(unit);f.flush();os.fsync(f.fileno())
os.chown(U,0,0)
run(['systemctl','daemon-reload'])
r,d=props()
if r.returncode or d.get('LoadState')!='loaded' or d.get('ActiveState')!='inactive' or d.get('MainPID')!='0' or d.get('FragmentPath')!=str(U):raise RuntimeError('child_unit_prestart_identity_failed')
run(['systemctl','start',C['unit']])
end=time.monotonic()+25;pid=0;d={}
while time.monotonic()<end:
 r,d=props()
 if d.get('ActiveState')=='active' and int(d.get('MainPID','0') or 0)>0:pid=int(d['MainPID']);break
 time.sleep(.25)
if not pid:raise RuntimeError('child_unit_did_not_start')
cp=Path('/proc',str(pid));rawstat=(cp/'stat').read_text();cstart=int(rawstat[rawstat.rfind(')')+2:].split()[19]);cuid=int(next(x for x in (cp/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);cexe=os.readlink(str(cp/'exe'));args=[x.decode(errors='replace') for x in (cp/'cmdline').read_bytes().split(b'\0') if x];ccg=(cp/'cgroup').read_text().strip()
if cuid!=0 or sha(cexe)!=C['agent_sha'] or not any(x==str(CFG) for x in args) or C['unit'] not in ccg or d.get('MainPID')!=str(pid):raise RuntimeError('child_process_owner_guard_failed')
if CFG.is_symlink() or CFG.stat().st_uid!=0 or stat.S_IMODE(CFG.stat().st_mode)!=0o600 or sha(CFG)=='' or str(CA) not in CFG.read_text() or str(DATA) not in CFG.read_text() or C['token'] not in CFG.read_text():raise RuntimeError('child_config_content_or_mode_gate_failed')
# Atomically publish exact current child owner after successful start.
manifest['units']['child']={'name':C['unit'],'path':str(U),'file_sha256':sha(U),'pid':pid,'starttime':cstart,'uid':cuid,'exe_path':cexe,'exe_sha256':sha(cexe),'argv_config':str(CFG),'cgroup':ccg,'data_dir':str(DATA),'data_dir_dev':DATA.stat().st_dev,'data_dir_ino':DATA.stat().st_ino,'config_sha256':sha(CFG),'tls_ca_file':str(CA)}
manifest['server_process_rechecked_at']=time.time();tmp=D/('owner-manifest.json.tmp-'+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
with os.fdopen(fd,'wb') as f:f.write(json.dumps(manifest,separators=(',',':')).encode());f.flush();os.fsync(f.fileno())
os.chown(tmp,0,0);os.replace(tmp,M);newraw=M.read_bytes()
print(json.dumps({'status':'PASS','node_id':C['node_id'],'agent_id':C['agent_id'],'unit':C['unit'],'unit_sha256':sha(U),'config_sha256':sha(CFG),'config_mode':oct(stat.S_IMODE(CFG.stat().st_mode)),'tls_ca_file':str(CA),'ca_sha256':sha(CA),'data_dir':str(DATA),'data_dir_dev':DATA.stat().st_dev,'data_dir_ino':DATA.stat().st_ino,'child':{'pid':pid,'starttime':cstart,'uid':cuid,'exe_sha256':sha(cexe),'argv_config':str(CFG),'cgroup':ccg},'manifest_sha256':hashlib.sha256(newraw).hexdigest(),'secret_value_emitted':False},separators=(',',':')))
''' % json.dumps({**CLAIMS,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':LIVE['remote_owner']['manifest_sha256'],'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256']},separators=(',',':'))
command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
api.validate_command(command,'start-owned-child')
task=api.create_secret_task('qa-resume-20261010-host-tls-start-child-1358f9a990bd',command,NODE,'registration_token')
params={'registration_token':TOKEN}
try:
 result=api.run_task(task,timeout=75,params=params,secret_values=(TOKEN,))
finally:
 cleanup=api.cleanup_task(task['id'],NODE)
if result.get('status')!='SUCCESS':raise RuntimeError('owned_child_start_task_failed')
out=json.loads(result.get('stdout') or '')
if out.get('status')!='PASS' or out.get('node_id')!=NODE_ID or out.get('secret_value_emitted') is not False:raise RuntimeError('owned_child_start_result_gate_failed')
# Confirm HELLO registration and sync via the repair API, without exposing registration credentials.
end=time.monotonic()+40;node=None
while time.monotonic()<end:
 node=api.get_json('/api/nodes/'+NODE_ID)
 if node.get('status')=='online' and node.get('sync_status')=='synced':break
 time.sleep(.5)
if not node or node.get('status')!='online' or node.get('sync_status')!='synced':raise RuntimeError('owned_child_hello_or_sync_timeout')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
record={'status':'PASS','actual_at':stamp,'node':{k:node.get(k) for k in ('id','agent_id','hostname','ip','status','sync_status','agent_version','labels')},'remote':out,'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup,'registration_secret_persisted_to_evidence':False}
api.write_json('child-started.json',record)
p=HERE/'progress.json';v=json.loads(p.read_text());v.update({'actual_at':stamp,'stage':'owned-child-online-synced','current_operation':'Unique native child installed from exact repair Agent binary with owned 0600 config and tls_ca_file, started under exact owner Unit, and HELLO reached online/synced. Owner manifest now includes dynamic child identity.','next_action':'Test no-CA/wrong-CA failures using unique uncached payload with actual backend error/cache/temp evidence; then restore CA on same payload and prove successful GET/cache/database before app deploy.','child_runtime':out['child'],'current_manifest_sha256':out['manifest_sha256'],'persisted_evidence':sorted(set(v.get('persisted_evidence',[])+['child-started.json','task-cleanup-'+task['id']+'.json']))});api.write_json('progress.json',v)
(HERE/'private'/'registration-secret.py').unlink()
print(json.dumps({'status':'PASS','actual_at':stamp,'child_node_id':NODE_ID,'child_status':'online/synced','child_pid':out['child']['pid'],'child_unit':out['unit'],'manifest_sha256':out['manifest_sha256'],'task_get404':cleanup['get_after_delete_http']==404,'registration_secret_local_file_removed':True}))
