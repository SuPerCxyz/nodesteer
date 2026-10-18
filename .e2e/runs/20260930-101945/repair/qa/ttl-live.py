#!/usr/bin/env python3
"""Prove natural session expiry on the isolated static repair Hub; restore exact config."""
import hashlib,json,os,shlex,stat,subprocess,time,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945'; root=Path(__file__).resolve().parents[5]
priv=Path('/tmp/nodesteer-e2e-'+RUN+'-repair'); connp=priv/'repair-connection.json'; cfgp=priv/'repair-hub.yaml'
source=json.loads(Path('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json').read_text()); hub=source['hub']; conn=json.loads(connp.read_text())
url=conn['hub_url'].rstrip('/'); remote='/etc/nodesteer/hub-repair-'+RUN+'.yaml'; backup=priv/'repair-hub-before-ttl.yaml'; remote_backup=remote+'.pre-ttl-test'; unit='nodesteer-hub-repair-'+RUN+'.service'
out=Path(__file__).with_name('ttl-live.json')
if out.exists() or backup.exists(): raise SystemExit('refuse overwrite TTL evidence or backup')

def run(cmd,input_text=None,timeout=90):
 p=subprocess.run(cmd,input=input_text,text=True,capture_output=True,timeout=timeout)
 if p.returncode: raise RuntimeError(f'command failed ({p.returncode}): {(p.stderr or p.stdout)[-250:]}')
 return p.stdout.strip()
def ssh(script):
 return run(['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',hub['ssh_identity_file'],'-p',str(hub['ssh_port']),hub['ssh_target'],'sh','-s'],script)
def req(path,method='GET',body=None,token=None):
 data=None if body is None else json.dumps(body).encode(); headers={'Content-Type':'application/json'} if data is not None else {}
 if token: headers['Authorization']='Bearer '+token
 q=urllib.request.Request(url+path,data=data,headers=headers,method=method)
 try:
  with urllib.request.urlopen(q,timeout=5) as r: code=r.status; raw=r.read()
 except urllib.error.HTTPError as e: code=e.code; raw=e.read()
 try: obj=json.loads(raw) if raw else None
 except (ValueError,UnicodeDecodeError): obj=None
 return code,obj
def wait_health(timeout=35):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  a,_=req('/healthz'); b,_=req('/readyz')
  if a==200 and b==200:return a,b
  time.sleep(1)
 return a,b
original=cfgp.read_bytes(); mode=stat.S_IMODE(cfgp.stat().st_mode); original_sha=hashlib.sha256(original).hexdigest()
assert mode==0o600 and b'session_ttl: "24h"' in original and original.count(b'session_ttl:')==1
backup.write_bytes(original); backup.chmod(0o600)
ssh('\n'.join(['set -eu',f'test "$(systemctl show {shlex.quote(unit)} -p ActiveState --value)" = active',f'test "$(stat -c %a {shlex.quote(remote)})" = 600',f'test "$(sha256sum {shlex.quote(remote)} | cut -d " " -f1)" = {original_sha}',f'test ! -e {shlex.quote(remote_backup)}',f'cp -p {shlex.quote(remote)} {shlex.quote(remote_backup)}',f'test "$(sha256sum {shlex.quote(remote_backup)} | cut -d " " -f1)" = {original_sha}','']))
changed=original.replace(b'session_ttl: "24h"',b'session_ttl: "3s"',1)
tmp=priv/'.repair-hub-ttl-test.yaml'; tmp.write_bytes(changed);tmp.chmod(0o600)
stage=remote+'.ttl-test-stage'
run(['scp','-F','/dev/null','-B','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',hub['ssh_identity_file'],'-P',str(hub['ssh_port']),str(tmp),hub['ssh_target']+':'+stage])
ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(stage)} | cut -d " " -f1)" = {hashlib.sha256(changed).hexdigest()}',f'mv -f {shlex.quote(stage)} {shlex.quote(remote)}',f'systemctl restart {shlex.quote(unit)}','']))
hz,rz=wait_health(); assert (hz,rz)==(200,200),f'TTL Hub restart health failure: {(hz,rz)}'
login_code,login=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']}); assert login_code==200 and login.get('token')
token=login['token']; started=time.monotonic(); me_code,_=req('/api/me',token=token); assert me_code==200
expires_header=None
# Let the server expire this token naturally, then prove protected API rejection.
time.sleep(3.4); elapsed=round(time.monotonic()-started,3)
expired_me,_=req('/api/me',token=token); expired_nodes,_=req('/api/nodes',token=token)
assert expired_me==401 and expired_nodes==401,f'natural TTL expiry failed: {(expired_me,expired_nodes)}'
# Restore exact repair-only Hub config and relogin; no primary configuration is read or written.
restore=remote+'.restore-stage'
run(['scp','-F','/dev/null','-B','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',hub['ssh_identity_file'],'-P',str(hub['ssh_port']),str(backup),hub['ssh_target']+':'+restore])
ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(restore)} | cut -d " " -f1)" = {original_sha}',f'chmod 0600 {shlex.quote(restore)}',f'mv -f {shlex.quote(restore)} {shlex.quote(remote)}',f'test "$(sha256sum {shlex.quote(remote)} | cut -d " " -f1)" = {original_sha}',f'systemctl restart {shlex.quote(unit)}','']))
hz2,rz2=wait_health(); assert (hz2,rz2)==(200,200),f'restored TTL Hub health failure: {(hz2,rz2)}'
new_code,new_login=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']}); assert new_code==200 and new_login.get('token')
conn['token']=new_login['token']; tmpconn=priv/'.repair-connection-ttl.tmp'; fd=os.open(tmpconn,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump(conn,f,ensure_ascii=False,indent=2);f.write('\n')
os.replace(tmpconn,connp);connp.chmod(0o600)
me2,_=req('/api/me',token=conn['token']); assert me2==200
report={'case_id':'FL-14','failure_id':'E2E-20260930-101945-P1-001','status':'passed','layer':'live-isolated-static-hub','build_label':'dddee4f0-e2e-repair-20261004','hub_sha256':'e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472','session_ttl_test':'3s','login_http':login_code,'immediate_me_http':me_code,'elapsed_monotonic_seconds':elapsed,'expired_me_http':expired_me,'expired_protected_nodes_http':expired_nodes,'restored_health_ready_http':[hz2,rz2],'restored_admin_login_http':new_code,'restored_me_http':me2,'original_config_restored_sha256':original_sha,'private_config_backup':str(backup),'remote_config_backup':remote_backup,'secrets_included':False,'primary_touched':False}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','ttl_seconds':3,'elapsed':elapsed,'http':[login_code,me_code,expired_me,expired_nodes],'restored_health':[hz2,rz2],'primary_touched':False}))
