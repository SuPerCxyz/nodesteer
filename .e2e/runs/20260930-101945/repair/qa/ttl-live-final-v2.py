#!/usr/bin/env python3
"""Run isolated real TTL=3s and restore exact owned config, preserving service ownership."""
import hashlib,json,os,shlex,subprocess,time,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945';root=Path(__file__).resolve().parents[5];priv=Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
connp=priv/'repair-connection.json';original=priv/'repair-hub-before-ttl.yaml';source=json.load(open('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json'));hub=source['hub'];conn=json.loads(connp.read_text());url=conn['hub_url'].rstrip('/')
remote='/etc/nodesteer/hub-repair-'+RUN+'.yaml';remote_backup=remote+'.pre-ttl-test';unit='nodesteer-hub-repair-'+RUN+'.service';orig_sha=hashlib.sha256(original.read_bytes()).hexdigest();out=Path(__file__).with_name('ttl-live.json')
if out.exists() or not original.is_file() or original.stat().st_mode & 0o077:raise SystemExit('TTL evidence/private backup guard failed')
def run(cmd,input_text=None,timeout=90):
 p=subprocess.run(cmd,input=input_text,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(f'command failed ({p.returncode}): {(p.stderr or p.stdout)[-200:]}')
 return p.stdout.strip()
def ssh(script):
 return run(['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',hub['ssh_identity_file'],'-p',str(hub['ssh_port']),hub['ssh_target'],'sh','-s'],script)
def req(path,method='GET',body=None,token=None):
 data=None if body is None else json.dumps(body).encode();headers={'Content-Type':'application/json'} if data is not None else {}
 if token:headers['Authorization']='Bearer '+token
 try:
  with urllib.request.urlopen(urllib.request.Request(url+path,data=data,headers=headers,method=method),timeout=5) as r:code,raw=r.status,r.read()
 except urllib.error.HTTPError as e:code,raw=e.code,e.read()
 except urllib.error.URLError:return 0,None
 try:return code,json.loads(raw) if raw else None
 except (ValueError,UnicodeDecodeError):return code,None
def wait_health(timeout=45):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  a,_=req('/healthz');b,_=req('/readyz')
  if (a,b)==(200,200):return a,b
  time.sleep(1)
 return a,b
# Verify exact original backup and preserve node-owned config metadata through in-place content edit.
ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(remote_backup)} | cut -d " " -f1)" = {orig_sha}',f'test "$(sha256sum {shlex.quote(remote)} | cut -d " " -f1)" = {orig_sha}',f'test "$(stat -c %U:%G:%a {shlex.quote(remote)})" = nodesteer:nodesteer:600',f'cp -p {shlex.quote(remote_backup)} {shlex.quote(remote)}',f'python3 - {shlex.quote(remote)} <<\'PY2\'', 'import pathlib,sys', 'p=pathlib.Path(sys.argv[1]);b=p.read_bytes();assert b.count(b\'session_ttl: "24h"\')==1;p.write_bytes(b.replace(b\'session_ttl: "24h"\',b\'session_ttl: "3s"\',1))', 'PY2',f'test "$(stat -c %U:%G:%a {shlex.quote(remote)})" = nodesteer:nodesteer:600',f'grep -qx \'session_ttl: "3s"\' {shlex.quote(remote)}',f'systemctl restart {shlex.quote(unit)}','']))
hz,rz=wait_health();assert (hz,rz)==(200,200),f'TTL Hub health gate failed {(hz,rz)}'
status={'hub_health_with_ttl_config':[hz,rz],'config_owner_mode':'nodesteer:nodesteer:0600'}
try:
 code,login=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']});assert code==200 and isinstance(login,dict) and login.get('token'),f'login HTTP {code}'
 t0=time.monotonic();me,_=req('/api/me',token=login['token']);assert me==200,f'immediate /api/me HTTP {me}'
 time.sleep(3.5);elapsed=round(time.monotonic()-t0,3)
 expired_me,_=req('/api/me',token=login['token']);expired_nodes,_=req('/api/nodes',token=login['token'])
 assert expired_me==401 and expired_nodes==401,f'actual expiry failed /me={expired_me} /nodes={expired_nodes}'
 status.update({'login_http':code,'immediate_me_http':me,'elapsed_monotonic_seconds':elapsed,'expired_me_http':expired_me,'expired_nodes_http':expired_nodes})
finally:
 # Always restore exact file bytes, mode, and owner from the verified isolated backup.
 ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(remote_backup)} | cut -d " " -f1)" = {orig_sha}',f'cp -p {shlex.quote(remote_backup)} {shlex.quote(remote)}',f'test "$(sha256sum {shlex.quote(remote)} | cut -d " " -f1)" = {orig_sha}',f'test "$(stat -c %U:%G:%a {shlex.quote(remote)})" = nodesteer:nodesteer:600',f'systemctl restart {shlex.quote(unit)}','']))
 hz2,rz2=wait_health();assert (hz2,rz2)==(200,200),f'restored Hub health failed {(hz2,rz2)}'
 status.update({'restored_health_ready_http':[hz2,rz2],'original_config_sha256_restored':orig_sha})
 code2,login2=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']});assert code2==200 and login2.get('token'),f'restored admin login HTTP {code2}'
 conn['token']=login2['token'];tmp=priv/'.repair-connection-ttl3.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(conn,f,ensure_ascii=False,indent=2);f.write('\n')
 os.replace(tmp,connp);connp.chmod(0o600)
 me2,_=req('/api/me',token=conn['token']);assert me2==200,f'restored /api/me HTTP {me2}'
 status.update({'restored_login_http':code2,'restored_me_http':me2})
report={'case_id':'FL-14','failure_id':'E2E-20260930-101945-P1-001','status':'passed','layer':'live-isolated-static-hub','build_label':'dddee4f0-e2e-repair-20261004','hub_sha256':'e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472','tested_ttl':'3s','checks':status,'remote_config_backup':remote_backup,'private_config_backup':str(original),'configuration_owner_preserved':True,'primary_touched':False,'secrets_included':False}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','elapsed_seconds':status['elapsed_monotonic_seconds'],'expired_http':[status['expired_me_http'],status['expired_nodes_http']],'restored_http':[status['restored_health_ready_http'],status['restored_login_http'],status['restored_me_http']]}))
