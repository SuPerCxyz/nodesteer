import hashlib,json,os,pathlib,pwd,subprocess,time
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BIN=ROOT/'ws-frame-proxy';BS='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e';CHILDSHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';APPSHA={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a,timeout=10):
 p=subprocess.run(a,text=True,capture_output=True,timeout=timeout);return {'rc':p.returncode,'out':p.stdout.strip(),'err_bytes':len(p.stderr.encode())}
def show(u,props):
 args=['systemctl','show',u]
 for x in props:args+=['-p',x]
 q=run(args);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 st=Path('/proc/%d/stat'%pid).read_text().split(') ',1)[1].split();status=Path('/proc/%d/status'%pid).read_text();cmd=Path('/proc/%d/cmdline'%pid).read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'state':st[0],'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha('/proc/%d/exe'%pid),'cgroup':Path('/proc/%d/cgroup'%pid).read_text().strip()}
o=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!=NONCE or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=UNIT or o.get('binary_sha256')!=BS:raise SystemExit('proxy owner manifest mismatch')
if c.get('fault_armed') or c.get('release_good_snapshot') or c.get('good_script_id')!='fc6fbcc6-39ce-49f4-9368-265a5a42d174' or c.get('bad_script_id')!='616a9db6-a87f-4d79-a4de-7d1b016e2986':raise SystemExit('proxy controls mismatch or fault already armed')
if ROOT.stat().st_uid!=65534 or ROOT.stat().st_mode&0o7777!=0o700 or BIN.is_symlink() or sha(BIN)!=BS:raise SystemExit('proxy root/binary guard mismatch')
cu=show(CHILD,['LoadState','ActiveState','MainPID','ControlGroup']);cp=proc(int(cu.get('MainPID','0')))
if cu.get('ActiveState')!='active' or cp['pid']!=49688 or cp['startticks']!=136179722 or cp['uid']!=0 or cp['exe_sha256']!=CHILDSHA or cp['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(R/'agent.yaml')] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child owner/process guard mismatch')
au=show(APPUNIT,['LoadState','ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')))
actual={'binary':sha(R/'app/bin/qa-app'),'config':sha(R/'app/config/qa-app.env'),'unit':sha('/etc/systemd/system/'+APPUNIT)}
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT or actual!=APPSHA:raise SystemExit('v1 App physical baseline guard mismatch')
for u,pid,ticks in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=show(u,['LoadState','ActiveState','MainPID']);pr=proc(int(q.get('MainPID','0')))
 if q.get('ActiveState')!='active' or pr['pid']!=pid or pr['startticks']!=ticks:raise SystemExit('original parent service guard mismatch: '+u)
if show(UNIT,['LoadState']).get('LoadState')!='not-found':raise SystemExit('exact proxy Unit already exists')
args=['systemd-run','--unit='+UNIT,'--property=User=65534','--property=Group=65534','--property=Restart=no','--property=RuntimeMaxSec=3600','--property=KillMode=control-group','--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=','--property=AmbientCapabilities=','--property=ProtectSystem=strict','--property=ReadWritePaths='+str(ROOT),'--property=ProtectHome=yes','--property=PrivateDevices=yes','--property=ProtectKernelTunables=yes','--property=ProtectKernelModules=yes','--property=ProtectControlGroups=yes','--property=RestrictSUIDSGID=yes','--property=LockPersonality=yes','--property=UMask=0077','--property=RestrictAddressFamilies=AF_UNIX AF_INET','--property=IPAddressAllow=127.0.0.1/8','--property=IPAddressAllow=192.168.100.209/32','--property=IPAddressDeny=any','--',str(BIN),str(ROOT)]
started=run(args,15)
if started['rc']!=0:raise SystemExit('systemd-run failed rc='+str(started['rc']))
end=time.monotonic()+10;u={};pr={};ready=None
while time.monotonic()<end:
 u=show(UNIT,['LoadState','ActiveState','SubState','MainPID','ControlGroup','User','Group','Restart','RuntimeMaxUSec','NoNewPrivileges','CapabilityBoundingSet','AmbientCapabilities','ProtectSystem','ReadWritePaths','IPAddressAllow','IPAddressDeny','ExecStart']);pid=int(u.get('MainPID','0') or 0)
 if u.get('ActiveState')=='active' and pid and (ROOT/'ready.json').is_file():pr=proc(pid);ready=json.loads((ROOT/'ready.json').read_text());break
 time.sleep(.25)
if not ready or not pr:raise SystemExit('proxy Unit did not become ready')
if pr['uid']!=65534 or pr['exe_sha256']!=BS or pr['argv']!=[str(BIN),str(ROOT)] or pr['cgroup']!='0::/system.slice/'+UNIT:raise SystemExit('proxy process owner/exe/argv/cgroup mismatch')
if ready.get('listen')!='127.0.0.1' or ready.get('upstream_host')!='192.168.100.209' or ready.get('upstream_port')!=18080 or ready.get('agent_id')!=AGENT or ready.get('unit_pid')!=pid:raise SystemExit('proxy READY metadata mismatch')
if u.get('User') not in {'65534','nobody'} or u.get('Group') not in {'65534','nogroup'} or u.get('Restart')!='no' or u.get('NoNewPrivileges')!='yes':raise SystemExit('proxy Unit sandbox property mismatch')
print(json.dumps({'status':'PROXY_READY','unit':UNIT,'unit_properties':u,'process':pr,'ready':ready,'owner_nonce':NONCE,'fault_armed':c.get('fault_armed'),'child_pid_untouched':cp['pid'],'app_pid_untouched':ap['pid'],'v1_hashes_match':actual==APPSHA,'endpoint_policy':'loopback listen and fixed 192.168.100.209:18080 upstream'},sort_keys=True),flush=True)
