import hashlib,json,os,pathlib,pwd,subprocess,time
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BIN='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';AGENT_PID=49688;AGENT_TICKS=136179722;APP_SHA={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'};PROXYSHA='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e';SOURCE_SHA='a4fba037729524358e92f0aed881aec655c9b58f4691d7b4639aeb9b1c503db7'
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=10);return q.returncode,q.stdout.strip()
def show(unit):
 rc,out=run(['systemctl','show',unit,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup']);return dict(x.split('=',1) for x in out.splitlines() if '=' in x)
def proc(pid):
 st=(Path('/proc')/str(pid)/'stat').read_text().split(') ',1)[1].split();status=(Path('/proc')/str(pid)/'status').read_text();cmd=(Path('/proc')/str(pid)/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);exe=Path('/proc')/str(pid)/'exe';h=hashlib.sha256(exe.read_bytes()).hexdigest();return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':h,'cgroup':(Path('/proc')/str(pid)/'cgroup').read_text().strip()}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
o=json.loads((R/'owner.json').read_text());want={'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_path':BIN,'binary_sha256':BIN_SHA,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')}
if not all(o.get(k)==v for k,v in want.items()):raise SystemExit('blocked: child owner manifest mismatch')
cu=show(CHILD);cp=proc(int(cu.get('MainPID','0'))); ap=show(APPUNIT); appid=int(ap.get('MainPID','0')); appproc=proc(appid);pu=show(PROXY)
if cu.get('ActiveState')!='active' or cp['pid']!=AGENT_PID or cp['startticks']!=AGENT_TICKS or cp['uid']!=0 or cp['exe_sha256']!=BIN_SHA or cp['argv']!=[BIN,'--config',str(R/'agent.yaml')] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('blocked: child process guard mismatch')
if ap.get('ActiveState')!='active' or not appid or appproc['uid']!=0 or appproc['cgroup']!='0::/system.slice/'+APPUNIT:raise SystemExit('blocked: v1 App Unit inactive or owner guard mismatch')
actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if actual!=APP_SHA:raise SystemExit('blocked: v1 physical SHA mismatch')
for unit,expected_pid,expected_ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 u=show(unit);p=int(u.get('MainPID','0'));q=proc(p) if p else {}
 if u.get('ActiveState')!='active' or p!=expected_pid or q.get('startticks')!=expected_ticks:raise SystemExit('blocked: original Native-1 service changed: '+unit)
if pu.get('LoadState')!='not-found':raise SystemExit('blocked: proxy Unit already exists')
if P.exists() or P.is_symlink():raise SystemExit('blocked: proxy root already exists')
uid=pwd.getpwnam('nobody').pw_uid;gid=pwd.getpwnam('nobody').pw_gid;os.mkdir(P,0o700);os.chown(P,uid,gid);os.chmod(P,0o700)
owner={'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'child_agent_unit':CHILD,'source_sha256':SOURCE_SHA,'binary_sha256':PROXYSHA}
control={'app_id':'2e4fecee-afc8-43a8-9d93-dd36cfc10bf9','good_script_id':'fc6fbcc6-39ce-49f4-9368-265a5a42d174','good_revision':2,'good_sha256':'728dafe9a3392b084f7f3c050a018a3f958aa75049e06ba56081f075dcd9f17d','bad_script_id':'616a9db6-a87f-4d79-a4de-7d1b016e2986','bad_revision':1,'bad_sha256':'7d6df17e0117eb02de2b0263062ea68bf68e45dea5c1959816d7bdc719466c54','fault_armed':False,'release_good_snapshot':False}
# Fill expected Script SHA from actual owned Hub definitions is performed in the follow-up guarded setup task; controls remain unarmed here.
for name,obj in [('owner.json',owner),('controls.json',control)]:
 tmp=P/(name+'.tmp');tmp.write_text(json.dumps(obj,separators=(',',':'))+'\n');os.chown(tmp,uid,gid);os.chmod(tmp,0o644);os.replace(tmp,P/name)
print(json.dumps({'status':'ROOT_CREATED','proxy_root':str(P),'uid':uid,'gid':gid,'mode':oct(P.stat().st_mode&0o7777),'owner_nonce':NONCE,'node_id':NODE,'agent_id':AGENT,'child_pid':cp['pid'],'child_startticks':cp['startticks'],'app_pid':apppid if False else appid,'app_unit_active':ap['ActiveState'],'v1_hashes_match':actual==APP_SHA,'proxy_unit_load_state':pu.get('LoadState'),'fault_armed':False},sort_keys=True),flush=True)
