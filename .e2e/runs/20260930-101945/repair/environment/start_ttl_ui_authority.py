#!/usr/bin/env python3
"""Create one independent short-TTL loopback Hub for browser auth-expiry verification."""
import hashlib,json,os,secrets,shlex,socket,subprocess,time,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945';ROOT=Path(__file__).resolve().parents[5];PUB=Path(__file__).resolve().parent;QA=ROOT/'.e2e/runs'/RUN/'repair/qa'
PRIV=Path('/tmp/nodesteer-e2e-'+RUN+'-repair');SOURCE=Path('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json');MANIFEST=ROOT/'.e2e/runs'/RUN/'repair/build-manifest.json'
CONN=PRIV/'ttl-ui-connection.json';PRIVATE_CONFIG=PRIV/'ttl-ui-hub.yaml';UNIT_LOCAL=PRIV/'ttl-ui-hub.service';REPORT=QA/'ttl-ui-authority.json'
URL='http://127.0.0.1:18995';RUN_LABEL='nodesteer-hub-repair-'+RUN+'-ttl-ui';UNIT=RUN_LABEL+'.service'
CONFIG='/etc/nodesteer/'+RUN_LABEL+'.yaml';DATA='/var/lib/'+RUN_LABEL;BINARY='/opt/nodesteer-repair-'+RUN+'/bin/nodesteer-hub-static-login-guard'
UNIT_PATH='/etc/systemd/system/'+UNIT;PORT=18995;HUB_SHA='164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'

def run(cmd,input_text=None,timeout=90):
 p=subprocess.run(cmd,input=input_text,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(f'command failed ({p.returncode}): {(p.stderr or p.stdout)[-250:]}')
 return p.stdout.strip()
def ssh(h,script,timeout=90):return run(['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',h['ssh_identity_file'],'-p',str(h['ssh_port']),h['ssh_target'],'sh','-s'],script,timeout)
def scp(local,h,remote):run(['scp','-F','/dev/null','-B','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',h['ssh_identity_file'],'-P',str(h['ssh_port']),str(local),h['ssh_target']+':'+remote])
def http(path):
 try:
  with urllib.request.urlopen(URL+path,timeout=3) as r:return r.status
 except urllib.error.URLError:return 0

def main():
 if REPORT.exists():raise RuntimeError('refuse overwrite prior TTL UI report')
 if PRIV.stat().st_mode&0o777!=0o700:raise RuntimeError('private directory must be 0700')
 manifest=json.loads(MANIFEST.read_text());artifact=next(a for a in manifest['artifacts'] if a.get('name')=='nodesteer-hub-static-login-guard')
 if artifact.get('sha256')!=HUB_SHA or artifact.get('cgo_enabled') is not False:raise RuntimeError('latest static login-guard Hub manifest mismatch')
 src=json.loads(SOURCE.read_text());hub=src['hub'];port=hub['ssh_port'];key=hub['ssh_identity_file'];target=hub['ssh_target']
 local=socket.socket();local.bind(('127.0.0.1',PORT));local.close()
 # All remote paths and listener are new and exact; never inspect or reuse existing Hub state.
 username='qa-repair-'+RUN+'-ttl-ui-admin';password='';registration=''
 if CONN.exists():
  if CONN.stat().st_mode&0o077:raise RuntimeError('private TTL credentials have unsafe permissions')
  conn=json.loads(CONN.read_text());username=conn['admin_username'];password=conn['admin_password'];registration=conn['registration_token']
 else:
  password='QaTtl-'+secrets.token_urlsafe(28);registration=secrets.token_hex(32)
  conn={'run_id':RUN,'hub_url':URL,'admin_username':username,'admin_password':password,'registration_token':registration,'hub_sha256':HUB_SHA,'session_ttl':'3s','data_dir':DATA}
 cfg='\n'.join([f'web_addr: {json.dumps("127.0.0.1:18995")}',f'gateway_addr: {json.dumps("127.0.0.1:18995")}',f'base_url: {json.dumps(URL)}',f'registration_token: {json.dumps(registration)}',f'data_dir: {json.dumps(DATA)}',f'artifact_dir: {json.dumps(DATA+"/artifacts")}',f'admin_username: {json.dumps(username)}',f'admin_password: {json.dumps(password)}','session_ttl: "3s"',''])
 if not CONN.exists():
  fd=os.open(CONN,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump(conn,f,ensure_ascii=False,indent=2);f.write('\n')
 if PRIVATE_CONFIG.exists():
  if PRIVATE_CONFIG.read_text()!=cfg or PRIVATE_CONFIG.stat().st_mode&0o077:raise RuntimeError('private TTL config mismatch')
 else:PRIVATE_CONFIG.write_text(cfg);PRIVATE_CONFIG.chmod(0o600)
 unit='\n'.join(['[Unit]',f'Description=NodeSteer isolated TTL UI Hub {RUN}','After=network-online.target','Wants=network-online.target','', '[Service]','Type=simple','User=nodesteer','Group=nodesteer',f'ExecStart={BINARY} --config {CONFIG}','Restart=on-failure','RestartSec=3','NoNewPrivileges=true','ProtectSystem=strict','ProtectHome=true','PrivateTmp=true',f'ReadWritePaths={DATA}','', '[Install]','WantedBy=multi-user.target',''])
 if UNIT_LOCAL.exists():
  if UNIT_LOCAL.read_text()!=unit or UNIT_LOCAL.stat().st_mode&0o077:raise RuntimeError('private TTL unit mismatch')
 else:UNIT_LOCAL.write_text(unit);UNIT_LOCAL.chmod(0o600)
 config_sha=hashlib.sha256(cfg.encode()).hexdigest();unit_sha=hashlib.sha256(unit.encode()).hexdigest()
 ssh(hub,'\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(BINARY)} | cut -d " " -f1)" = {HUB_SHA}',"if ss -ltn '( sport = :18995 )' | tail -n +2 | grep -q .; then exit 51; fi",
  f'if test -e {shlex.quote(DATA)}; then test -d {shlex.quote(DATA)}; test "$(stat -c %U:%G:%a {shlex.quote(DATA)})" = nodesteer:nodesteer:700; else mkdir -m 0700 {shlex.quote(DATA)}; chown nodesteer:nodesteer {shlex.quote(DATA)}; fi',
  f'if test -e {shlex.quote(DATA+"/artifacts")}; then test -d {shlex.quote(DATA+"/artifacts")}; test "$(stat -c %U:%G:%a {shlex.quote(DATA+"/artifacts")})" = nodesteer:nodesteer:700; else mkdir -m 0700 {shlex.quote(DATA+"/artifacts")}; chown nodesteer:nodesteer {shlex.quote(DATA+"/artifacts")}; fi',
  f'if test -e {shlex.quote(CONFIG)}; then test "$(sha256sum {shlex.quote(CONFIG)} | cut -d " " -f1)" = {config_sha}; test "$(stat -c %U:%G:%a {shlex.quote(CONFIG)})" = nodesteer:nodesteer:600; else echo config-absent; fi',
  f'if test -e {shlex.quote(UNIT_PATH)}; then test "$(sha256sum {shlex.quote(UNIT_PATH)} | cut -d " " -f1)" = {unit_sha}; test "$(stat -c %U:%G:%a {shlex.quote(UNIT_PATH)})" = root:root:644; else echo unit-absent; fi','']))
 config_state=ssh(hub,f'if test -e {shlex.quote(CONFIG)}; then echo present; else echo absent; fi')
 unit_state=ssh(hub,f'if test -e {shlex.quote(UNIT_PATH)}; then echo present; else echo absent; fi')
 if config_state=='absent' or unit_state=='absent':
  cstage=CONFIG+'.stage';ustage=UNIT_PATH+'.stage'
  if config_state=='absent':
   scp(PRIVATE_CONFIG,hub,cstage)
   ssh(hub,'\n'.join(['set -eu',f'chown nodesteer:nodesteer {shlex.quote(cstage)}',f'chmod 0600 {shlex.quote(cstage)}',f'test "$(sha256sum {shlex.quote(cstage)} | cut -d " " -f1)" = {config_sha}',f'mv {shlex.quote(cstage)} {shlex.quote(CONFIG)}','']))
  if unit_state=='absent':
   scp(UNIT_LOCAL,hub,ustage)
   ssh(hub,'\n'.join(['set -eu',f'chown root:root {shlex.quote(ustage)}',f'chmod 0644 {shlex.quote(ustage)}',f'test "$(sha256sum {shlex.quote(ustage)} | cut -d " " -f1)" = {unit_sha}',f'mv {shlex.quote(ustage)} {shlex.quote(UNIT_PATH)}','']))
  ssh(hub,'\n'.join(['set -eu','systemctl daemon-reload',f'systemctl start {shlex.quote(UNIT)}','']))
 ssh(hub,f'systemctl start {shlex.quote(UNIT)}')
 deadline=time.monotonic()+40
 while time.monotonic()<deadline:
  status=ssh(hub,f'curl -s -o /dev/null -w "%{{http_code}}" http://127.0.0.1:{PORT}/healthz || true',20)
  if status=='200':break
  time.sleep(1)
 if status!='200':raise RuntimeError('TTL UI Hub loopback health gate timeout')
 tunnel_log=PRIV/'ttl-ui-tunnel.log'
 tunnel=subprocess.Popen(['ssh','-F','/dev/null','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',key,'-p',str(port),'-L',f'127.0.0.1:{PORT}:127.0.0.1:{PORT}',target],stdout=subprocess.DEVNULL,stderr=tunnel_log.open('ab'),start_new_session=True)
 deadline=time.monotonic()+15
 while time.monotonic()<deadline and tunnel.poll() is None:
  if http('/healthz')==200:break
  time.sleep(.3)
 if tunnel.poll() is not None or http('/healthz')!=200:raise RuntimeError('local SSH tunnel did not reach owned loopback Hub')
 # Record PID privately for exact shutdown; no credential values are printed.
 (PRIV/'ttl-ui-tunnel.pid').write_text(str(tunnel.pid)+'\n');(PRIV/'ttl-ui-tunnel.pid').chmod(0o600)
 report={'run_id':RUN,'status':'isolated_short_ttl_hub_healthy','url':URL,'remote_binding':'127.0.0.1:18995','session_ttl':'3s','hub_sha256':HUB_SHA,'unit':UNIT,'config':CONFIG,'data_dir':DATA,'admin_credentials_private':str(CONN),'tunnel_pid_private':str(PRIV/'ttl-ui-tunnel.pid'),'health_http':200,'fresh_authority':True,'secrets_included':False}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':report['status'],'url':URL,'health_http':200,'session_ttl':'3s','hub_sha256':HUB_SHA}))
if __name__=='__main__':main()
