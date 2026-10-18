import hashlib,json,os,pathlib,subprocess,time
from pathlib import Path
ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';CFG=R/'agent.yaml';BIN=ROOT/'ws-frame-proxy';CAND=ROOT/'ws-frame-proxy-candidate-v3';OLD_BIN='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e';NEW_BIN='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';OLD_SRC='a4fba037729524358e92f0aed881aec655c9b58f4691d7b4639aeb9b1c503db7';NEW_SRC='5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';NONCE='870358cd92ee'
def run(a,t=12):
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
def file_sha(path):return sha(path)
o=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!=NONCE or o.get('node_id')!='c77b8a84-8085-4ea8-8532-de171761854b' or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=PROXY or o.get('binary_sha256')!=OLD_BIN or o.get('source_sha256')!=OLD_SRC:raise SystemExit('old proxy owner fingerprint mismatch')
if c.get('fault_armed') or c.get('release_good_snapshot'):raise SystemExit('refuse redeploy while fault armed/released')
if ROOT.stat().st_uid!=65534 or ROOT.stat().st_mode&0o7777!=0o700 or CAND.is_symlink() or sha(CAND)!=NEW_BIN or CAND.stat().st_uid!=65534 or CAND.stat().st_mode&0o7777!=0o755:raise SystemExit('new candidate exact owner/SHA mismatch')
cu=show(CHILD,['ActiveState','MainPID','ControlGroup']);cp=proc(int(cu.get('MainPID','0')))
if cu.get('ActiveState')!='active' or cp['pid']!=50158 or cp['startticks']!=136353430 or cp['uid']!=0 or cp['exe_sha256']!='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add' or cp['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(CFG)] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child process guard mismatch')
pu=show(PROXY,['LoadState','ActiveState','MainPID','ControlGroup']);pp=proc(int(pu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50125 or pp['startticks']!=136318003 or pp['uid']!=65534 or pp['exe_sha256']!=OLD_BIN or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('old proxy process guard mismatch')
au=show(APPUNIT,['ActiveState','MainPID','ControlGroup']);ap=proc(int(au.get('MainPID','0')))
if au.get('ActiveState')!='active' or ap['uid']!=0 or ap['cgroup']!='0::/system.slice/'+APPUNIT:raise SystemExit('v1 App Unit must remain active')
oldready=json.loads((ROOT/'ready.json').read_text())
if oldready.get('unit_pid')!=pp['pid']:raise SystemExit('old ready metadata PID mismatch')
stop=run(['systemctl','stop',PROXY],20)
if stop['rc']!=0:raise SystemExit('exact owned proxy stop failed')
if show(PROXY,['ActiveState','MainPID']).get('ActiveState')!='inactive':raise SystemExit('proxy Unit did not stop')
if Path('/proc/%d'%pp['pid']).exists():raise SystemExit('old proxy PID still exists after stop')
# Remove only the exact prior READY record after confirming its Unit PID; preserve events.log.
(ROOT/'ready.json').unlink()
os.replace(CAND,BIN)
if sha(BIN)!=NEW_BIN or BIN.stat().st_uid!=65534 or BIN.stat().st_mode&0o7777!=0o755:raise SystemExit('new active binary path metadata mismatch')
o['source_sha256']=NEW_SRC;o['binary_sha256']=NEW_BIN;tmp=ROOT/'owner.json.tmp'
with open(tmp,'x') as f:f.write(json.dumps(o,separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
os.chown(tmp,65534,65534);os.chmod(tmp,0o644);os.replace(tmp,ROOT/'owner.json')
start=run(['systemctl','start',PROXY],20)
if start['rc']!=0:raise SystemExit('exact owned proxy start failed')
end=time.monotonic()+10;unit={};np=None;ready=None
while time.monotonic()<end:
 unit=show(PROXY,['LoadState','ActiveState','SubState','MainPID','ControlGroup','User','Group','Restart','RuntimeMaxUSec','NoNewPrivileges','CapabilityBoundingSet','AmbientCapabilities','ProtectSystem','ReadWritePaths','IPAddressAllow','IPAddressDeny']);pid=int(unit.get('MainPID','0') or 0)
 if unit.get('ActiveState')=='active' and pid and (ROOT/'ready.json').is_file():
  np=proc(pid);ready=json.loads((ROOT/'ready.json').read_text());break
 time.sleep(.25)
if not ready or not np:raise SystemExit('v3 proxy not ready')
if np['pid']==pp['pid'] or np['uid']!=65534 or np['exe_sha256']!=NEW_BIN or np['argv']!=[str(BIN),str(ROOT)] or np['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('new proxy process fingerprint mismatch')
if ready.get('agent_id')!=AGENT or ready.get('upstream_host')!='192.168.100.209' or ready.get('upstream_port')!=18080 or ready.get('listen')!='127.0.0.1' or ready.get('unit_pid')!=np['pid']:raise SystemExit('new READY identity/upstream mismatch')
if json.loads((ROOT/'controls.json').read_text()).get('fault_armed'):raise SystemExit('fault control unexpectedly armed')
print(json.dumps({'status':'PROXY_V3_REDEPLOYED_UNARMED','unit':PROXY,'old_process':pp,'new_process':np,'ready':ready,'unit_properties':unit,'binary_path':str(BIN),'binary_sha256':sha(BIN),'source_sha256':NEW_SRC,'child_process_untouched':cp,'child_config_untouched':True,'app_pid_untouched':ap['pid'],'controls_fault_armed':False,'events_preserved':(ROOT/'events.jsonl').exists()},sort_keys=True),flush=True)
