#!/usr/bin/env python3
"""Finish natural TTL test and restore the exact isolated Hub config."""
import hashlib,json,os,shlex,subprocess,time,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945';root=Path(__file__).resolve().parents[5];priv=Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
connp=priv/'repair-connection.json';backup=priv/'repair-hub-before-ttl.yaml';source=json.load(open('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json'));hub=source['hub'];conn=json.loads(connp.read_text());url=conn['hub_url'].rstrip('/')
remote='/etc/nodesteer/hub-repair-'+RUN+'.yaml';remote_backup=remote+'.pre-ttl-test';unit='nodesteer-hub-repair-'+RUN+'.service';sha=hashlib.sha256(backup.read_bytes()).hexdigest();out=Path(__file__).with_name('ttl-live.json')
if out.exists() or not backup.is_file() or backup.stat().st_mode & 0o077: raise SystemExit('TTL recovery backup/evidence guard failed')
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
  with urllib.request.urlopen(urllib.request.Request(url+path,data=data,headers=headers,method=method),timeout=5) as r:return r.status,json.loads(r.read()) if r.headers.get('Content-Length')!='0' else None
 except urllib.error.HTTPError as e:return e.code,None
 except urllib.error.URLError:return 0,None
def health(timeout=40):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  a,_=req('/healthz');b,_=req('/readyz')
  if (a,b)==(200,200):return a,b
  time.sleep(1)
 return a,b
# Confirm the preserved original backup and the active TTL=3s config have the expected owner/mode and service.
remote_state=ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(remote_backup)} | cut -d " " -f1)" = {sha}',f'test "$(stat -c %U:%G:%a {shlex.quote(remote)})" = nodesteer:nodesteer:600',f'grep -qx \'session_ttl: "3s"\' {shlex.quote(remote)}',f'test "$(systemctl show {shlex.quote(unit)} -p ActiveState --value)" = active','']))
status={'backup_sha256':sha,'active_config_restored':False}
try:
 code,login=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']})
 assert code==200 and isinstance(login,dict) and login.get('token'),f'login HTTP {code}'
 t0=time.monotonic();immediate,_=req('/api/me',token=login['token']);assert immediate==200,f'immediate /api/me HTTP {immediate}'
 time.sleep(3.4);elapsed=round(time.monotonic()-t0,3)
 expired,_=req('/api/me',token=login['token']);nodes,_=req('/api/nodes',token=login['token'])
 assert expired==401 and nodes==401,f'expired token accepted: /me={expired}, /nodes={nodes}'
 status.update({'login_http':code,'immediate_me_http':immediate,'expired_me_http':expired,'expired_nodes_http':nodes,'elapsed_monotonic_seconds':elapsed})
finally:
 # Restore from the verified ownership-preserving server backup even if assertions fail.
 ssh('\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(remote_backup)} | cut -d " " -f1)" = {sha}',f'cp -p {shlex.quote(remote_backup)} {shlex.quote(remote)}',f'test "$(sha256sum {shlex.quote(remote)} | cut -d " " -f1)" = {sha}',f'test "$(stat -c %U:%G:%a {shlex.quote(remote)})" = nodesteer:nodesteer:600',f'systemctl restart {shlex.quote(unit)}','']))
 hz,rz=health();assert (hz,rz)==(200,200),f'restored Hub health/ready failed: {(hz,rz)}'
 status.update({'restored_health_ready_http':[hz,rz],'active_config_restored':True})
 login_code,again=req('/api/login','POST',{'username':conn['admin_username'],'password':conn['admin_password']})
 assert login_code==200 and again.get('token'),f'restored admin login HTTP {login_code}'
 conn['token']=again['token'];tmp=priv/'.repair-connection-ttl2.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(conn,f,ensure_ascii=False,indent=2);f.write('\n')
 os.replace(tmp,connp);connp.chmod(0o600)
 me,_=req('/api/me',token=conn['token']);assert me==200,f'restored /api/me HTTP {me}'
 status.update({'restored_admin_login_http':login_code,'restored_me_http':me})
report={'case_id':'FL-14','failure_id':'E2E-20260930-101945-P1-001','status':'passed','layer':'live-isolated-static-hub','build_label':'dddee4f0-e2e-repair-20261004','hub_sha256':'e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472','test_ttl':'3s','checks':status,'remote_config_backup':remote_backup,'private_config_backup':str(backup),'primary_touched':False,'secrets_included':False,'harness_note':'初次重启探测遇到Type=simple启动窗口；stage文件owner导致权限拒绝已修复为从owned备份cp -p原owner/mode后原位改TTL。本轮记录仅通过后写。'}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','elapsed_seconds':status['elapsed_monotonic_seconds'],'expired_http':[status['expired_me_http'],status['expired_nodes_http']],'restored_http':[status['restored_health_ready_http'],status['restored_admin_login_http'],status['restored_me_http']]}))
