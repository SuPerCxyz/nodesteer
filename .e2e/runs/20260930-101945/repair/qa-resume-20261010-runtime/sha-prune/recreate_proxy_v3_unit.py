import hashlib,json,pathlib,subprocess,time
from pathlib import Path
ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APP='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';BIN=ROOT/'ws-frame-proxy';NONCE='870358cd92ee';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';BS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';OLDCHILD='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
def run(a,t=15):
 p=subprocess.run(a,text=True,capture_output=True,timeout=t);return {'rc':p.returncode,'out':p.stdout.strip(),'stderr_bytes':len(p.stderr.encode())}
def show(u,props):
 a=['systemctl','show',u]
 for x in props:a+=['-p',x]
 q=run(a);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();cmd=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
o=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!=NONCE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=UNIT or o.get('source_sha256')!='5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0' or o.get('binary_sha256')!=BS:raise SystemExit('proxy owner manifest mismatch')
if c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('proxy controls not unarmed')
if show(UNIT,['LoadState']).get('LoadState')!='not-found':raise SystemExit('proxy Unit must be not-found before transient recreate')
if sha(BIN)!=BS or BIN.stat().st_uid!=65534 or BIN.stat().st_mode&0o7777!=0o755:raise SystemExit('new proxy binary guard mismatch')
if (ROOT/'ready.json').exists():raise SystemExit('old ready file must be absent before start')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup']);cp=proc(int(cu.get('MainPID','0')))
if cu.get('ActiveState')!='active' or cp['pid']!=50158 or cp['startticks']!=136353430 or cp['uid']!=0 or cp['exe_sha256']!=OLDCHILD or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('same owned child process fingerprint mismatch')
au=show(APP,['ActiveState','MainPID','ControlGroup']);
if au.get('ActiveState')!='active':raise SystemExit('v1 App Unit not active')
args=['systemd-run','--unit='+UNIT,'--property=User=65534','--property=Group=65534','--property=Restart=no','--property=RuntimeMaxSec=3600','--property=KillMode=control-group','--property=NoNewPrivileges=yes','--property=CapabilityBoundingSet=','--property=AmbientCapabilities=','--property=ProtectSystem=strict','--property=ReadWritePaths='+str(ROOT),'--property=ProtectHome=yes','--property=PrivateDevices=yes','--property=ProtectKernelTunables=yes','--property=ProtectKernelModules=yes','--property=ProtectControlGroups=yes','--property=RestrictSUIDSGID=yes','--property=LockPersonality=yes','--property=UMask=0077','--property=RestrictAddressFamilies=AF_UNIX AF_INET','--property=IPAddressAllow=127.0.0.1/8','--property=IPAddressAllow=192.168.100.209/32','--property=IPAddressDeny=any','--',str(BIN),str(ROOT)]
r=run(args,20)
if r['rc']!=0:raise SystemExit('systemd-run transient recreate failed rc='+str(r['rc']))
end=time.monotonic()+12;u={};p=None;ready=None
while time.monotonic()<end:
 u=show(UNIT,['LoadState','ActiveState','SubState','MainPID','ControlGroup','User','Group','Restart','RuntimeMaxUSec','NoNewPrivileges','CapabilityBoundingSet','AmbientCapabilities','ProtectSystem','ReadWritePaths','IPAddressAllow','IPAddressDeny']);pid=int(u.get('MainPID','0') or 0)
 if u.get('ActiveState')=='active' and pid and (ROOT/'ready.json').is_file():p=proc(pid);ready=json.loads((ROOT/'ready.json').read_text());break
 time.sleep(.25)
if not p or not ready:raise SystemExit('new proxy transient unit not ready')
if p['uid']!=65534 or p['exe_sha256']!=BS or p['argv']!=[str(BIN),str(ROOT)] or p['cgroup']!='0::/system.slice/'+UNIT:raise SystemExit('recreated proxy process fingerprint mismatch')
if ready.get('agent_id')!=AGENT or ready.get('upstream_host')!='192.168.100.209' or ready.get('upstream_port')!=18080 or ready.get('listen')!='127.0.0.1' or ready.get('unit_pid')!=p['pid']:raise SystemExit('recreated proxy READY metadata mismatch')
print(json.dumps({'status':'PROXY_V3_TRANSIENT_RECREATED_UNARMED','unit':UNIT,'process':p,'ready':ready,'unit_properties':u,'child_process_untouched':cp,'app_unit_active':au.get('ActiveState'),'binary_sha256':sha(BIN),'source_sha256':o['source_sha256'],'fault_armed':c.get('fault_armed'),'old_events_preserved':(ROOT/'events.jsonl').exists()},sort_keys=True),flush=True)
