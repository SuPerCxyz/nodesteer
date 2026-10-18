#!/usr/bin/env python3
"""Switch only the owned repair Hub unit to the latest CGO=0 login-guard build."""
import hashlib,json,os,shlex,subprocess,time,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945';ROOT=Path(__file__).resolve().parents[5];PRIV=Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
CONN=PRIV/'repair-connection.json';SOURCE=Path('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json')
MANIFEST=ROOT/'.e2e/runs'/RUN/'repair/build-manifest.json';BIN=Path('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/repair-build/nodesteer-hub-static-login-guard')
REPORT=ROOT/'.e2e/runs'/RUN/'repair/environment/hub-login-guard-cutover.json';HEALTH=ROOT/'.e2e/runs'/RUN/'repair/qa/hub-login-guard-health.json'
HUB_URL='http://192.168.100.209:18080';PRIMARY='http://192.168.100.209:8080';LABEL='dddee4f0-e2e-repair-20261004'
SHA='164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6';SIZE=54984207
OLD_SHA='e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472'
UNIT=f'nodesteer-hub-repair-{RUN}.service';OLD_BIN=f'/opt/nodesteer-repair-{RUN}/bin/nodesteer-hub';NEW_BIN=f'/opt/nodesteer-repair-{RUN}/bin/nodesteer-hub-static-login-guard'
UNIT_PATH=f'/etc/systemd/system/{UNIT}';UNIT_BACKUP=PRIV/'hub-unit-pre-login-guard.service';REMOTE_BACKUP=UNIT_PATH+'.pre-login-guard'
REMOTE_CONFIG=f'/etc/nodesteer/hub-repair-{RUN}.yaml';LOCAL_CONFIG=PRIV/'repair-hub.yaml'
AGENT_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'

def run(cmd,input_text=None,timeout=90):
 p=subprocess.run(cmd,input=input_text,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError(f'command failed ({p.returncode}): {(p.stderr or p.stdout)[-250:]}')
 return p.stdout.strip()
def ssh(hub,script,timeout=90):
 return run(['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',hub['ssh_identity_file'],'-p',str(hub['ssh_port']),hub['ssh_target'],'sh','-s'],script,timeout)
def scp(local,target,port,key,remote):
 run(['scp','-F','/dev/null','-B','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',key,'-P',str(port),str(local),target+':'+remote])
def request(url,method='GET',body=None,token=None):
 data=None if body is None else json.dumps(body).encode();headers={'Content-Type':'application/json'} if data is not None else {}
 if token:headers['Authorization']='Bearer '+token
 try:
  with urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers,method=method),timeout=10) as r:status,raw=r.status,r.read()
 except urllib.error.HTTPError as e:status,raw=e.code,e.read()
 except urllib.error.URLError:return 0,None
 try:return status,json.loads(raw) if raw else None
 except (ValueError,UnicodeDecodeError):return status,None
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def wait_health(timeout=40):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  a,_=request(HUB_URL+'/healthz');b,_=request(HUB_URL+'/readyz')
  if (a,b)==(200,200):return a,b
  time.sleep(1)
 return a,b
def inventory(token):
 paths=['nodes','scripts','tasks','artifacts','applications','users','executions?limit=500&offset=0']
 result={}
 for path in paths:
  code,data=request(HUB_URL+'/api/'+path,token=token)
  if code!=200:raise RuntimeError(f'pre/post resource inventory HTTP {code} for {path.split("?")[0]}')
  rows=data if isinstance(data,list) else data.get('items',[]) if isinstance(data,dict) else []
  key=path.split('?')[0]
  result[key]={'count':len(rows),'ids':sorted(str(x.get('id',x.get('ID',''))) for x in rows)}
 return result
def get_admin_token(c):
 code,x=request(HUB_URL+'/api/login','POST',{'username':c['admin_username'],'password':c['admin_password']})
 if code!=200 or not isinstance(x,dict) or not x.get('token'):raise RuntimeError(f'fresh admin login HTTP {code}')
 code,me=request(HUB_URL+'/api/me',token=x['token'])
 role=me.get('role',me.get('Role')) if isinstance(me,dict) else None
 if code!=200 or role!='administrator':raise RuntimeError(f'admin /api/me HTTP {code}')
 return x['token'],code,role

def main():
 if REPORT.exists() or HEALTH.exists():raise RuntimeError('refuse overwrite prior cutover report')
 if PRIV.stat().st_mode&0o777!=0o700 or CONN.stat().st_mode&0o777!=0o600:raise RuntimeError('private credential permissions mismatch')
 manifest=json.loads(MANIFEST.read_text());a=next(x for x in manifest.get('artifacts',[]) if x.get('name')=='nodesteer-hub-static-login-guard')
 if a.get('sha256')!=SHA or a.get('size')!=SIZE or a.get('cgo_enabled') is not False or a.get('architecture')!='amd64' or digest(BIN)!=SHA or BIN.stat().st_size!=SIZE:raise RuntimeError('latest static Hub artifact mismatch')
 source=json.loads(SOURCE.read_text());c=json.loads(CONN.read_text());hub=source['hub'];nodes=c['nodes']
 if c.get('hub_url')!=HUB_URL or len(nodes)!=4:raise RuntimeError('repair authority scope mismatch')
 config_sha=digest(LOCAL_CONFIG)
 if b'session_ttl: "24h"' not in LOCAL_CONFIG.read_bytes() or digest(LOCAL_CONFIG)!=digest(PRIV/'repair-hub-before-ttl.yaml'):raise RuntimeError('repair Hub config is not exact restored 24h version')
 old_token=c.get('token');precode,preme=request(HUB_URL+'/api/me',token=old_token)
 if precode!=200:old_token,_,_=get_admin_token(c)
 pre=inventory(old_token)
 code,old_nodes=request(HUB_URL+'/api/nodes',token=old_token)
 old_byname={x.get('hostname',x.get('name')):x for x in old_nodes} if code==200 and isinstance(old_nodes,list) else {}
 oldids={n['name']:old_byname.get(n['name'],{}).get('id') for n in nodes}
 if any(not v for v in oldids.values()) or len(set(oldids.values()))!=4:raise RuntimeError('repair Node identities missing/duplicated before cutover')
 for x in old_byname.values():
  if x.get('id') in oldids.values() and (x.get('status')!='online' or x.get('sync_status')!='synced' or x.get('agent_version')!=LABEL):raise RuntimeError('repair Native health gate not ready before Hub-only restart')
 # Ensure each Native PID and executable SHA before cutover; do not restart any Agent.
 source_nodes={n['ip']:n for n in source['nodes']};agent_before=[]
 for n in nodes:
  source_node=source_nodes[n['ssh_ip']];unit=n['service'];
  out=run(['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',source_node['ssh_identity_file'],'-p',str(n['ssh_port']),n['ssh_target'],'sh','-s'],f'set -eu; p=$(systemctl show {shlex.quote(unit)} -p MainPID --value); test "$p" -gt 1; test "$(sha256sum /proc/$p/exe | cut -d " " -f1)" = {AGENT_SHA}; echo "$p"',30)
  agent_before.append({'name':n['name'],'pid':out,'exe_sha256':AGENT_SHA})
 old_unit_text=ssh(hub, f'set -eu; test "$(systemctl show {shlex.quote(UNIT)} -p ActiveState --value)" = active; test "$(sha256sum {shlex.quote(OLD_BIN)} | cut -d " " -f1)" = {OLD_SHA}; test "$(sha256sum {shlex.quote(REMOTE_CONFIG)} | cut -d " " -f1)" = {config_sha}; test "$(stat -c %U:%G:%a {shlex.quote(REMOTE_CONFIG)})" = nodesteer:nodesteer:600; test ! -e {shlex.quote(NEW_BIN)}; test ! -e {shlex.quote(REMOTE_BACKUP)}; cat {shlex.quote(UNIT_PATH)}')
 if not old_unit_text or f'ExecStart={OLD_BIN} --config {REMOTE_CONFIG}' not in old_unit_text:raise RuntimeError('owned Hub unit contents changed unexpectedly')
 UNIT_BACKUP.write_text(old_unit_text);UNIT_BACKUP.chmod(0o600)
 new_unit=old_unit_text.replace(f'ExecStart={OLD_BIN} --config {REMOTE_CONFIG}',f'ExecStart={NEW_BIN} --config {REMOTE_CONFIG}',1)
 if new_unit==old_unit_text:raise RuntimeError('unit ExecStart replacement failed')
 unit_new=PRIV/'hub-unit-static-login-guard.service';unit_new.write_text(new_unit);unit_new.chmod(0o600)
 stage=NEW_BIN+'.stage';unit_stage=UNIT_PATH+'.login-guard-stage'
 scp(BIN,hub['ssh_target'],hub['ssh_port'],hub['ssh_identity_file'],stage)
 ssh(hub,'\n'.join(['set -eu',f'chmod 0755 {shlex.quote(stage)}',f'test "$(stat -c %s {shlex.quote(stage)})" = {SIZE}',f'test "$(sha256sum {shlex.quote(stage)} | cut -d " " -f1)" = {SHA}','']))
 scp(unit_new,hub['ssh_target'],hub['ssh_port'],hub['ssh_identity_file'],unit_stage)
 ssh(hub,'\n'.join(['set -eu',f'test "$(sha256sum {shlex.quote(unit_stage)} | cut -d " " -f1)" = {hashlib.sha256(new_unit.encode()).hexdigest()}',f'chmod 0644 {shlex.quote(unit_stage)}',f'chown root:root {shlex.quote(unit_stage)}',f'cp -p {shlex.quote(UNIT_PATH)} {shlex.quote(REMOTE_BACKUP)}',f'mv -f {shlex.quote(stage)} {shlex.quote(NEW_BIN)}',f'chown root:root {shlex.quote(NEW_BIN)}',f'chmod 0755 {shlex.quote(NEW_BIN)}',f'mv -f {shlex.quote(unit_stage)} {shlex.quote(UNIT_PATH)}','systemctl daemon-reload',f'systemctl restart {shlex.quote(UNIT)}','']),90)
 hz,rz=wait_health();
 if (hz,rz)!=(200,200):
  ssh(hub,'\n'.join(['set -eu',f'cp -p {shlex.quote(REMOTE_BACKUP)} {shlex.quote(UNIT_PATH)}','systemctl daemon-reload',f'systemctl restart {shlex.quote(UNIT)}','']),90)
  raise RuntimeError(f'new Hub health failed and exact prior unit restored; health={(hz,rz)}')
 token,me_code,role=get_admin_token(c)
 # Persist only the fresh isolated admin token in the private repair connection file.
 c['token']=token;tmp=PRIV/'.repair-connection-hub-guard.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:json.dump(c,f,ensure_ascii=False,indent=2);f.write('\n')
 os.replace(tmp,CONN);CONN.chmod(0o600)
 post=inventory(token);code,rows=request(HUB_URL+'/api/nodes',token=token)
 new={x.get('hostname',x.get('name')):x for x in rows} if code==200 and isinstance(rows,list) else {}
 agent_ids={n['name']:new.get(n['name'],{}).get('id') for n in nodes}
 deadline=time.monotonic()+75
 while time.monotonic()<deadline:
  if all(agent_ids[n['name']] and new[n['name']].get('status')=='online' and new[n['name']].get('sync_status')=='synced' and new[n['name']].get('agent_version')==LABEL for n in nodes):break
  time.sleep(1);code,rows=request(HUB_URL+'/api/nodes',token=token);new={x.get('hostname',x.get('name')):x for x in rows} if code==200 and isinstance(rows,list) else {};agent_ids={n['name']:new.get(n['name'],{}).get('id') for n in nodes}
 if agent_ids!=oldids or any(not new[n['name']] or new[n['name']].get('status')!='online' or new[n['name']].get('sync_status')!='synced' or new[n['name']].get('agent_version')!=LABEL for n in nodes):raise RuntimeError('Hub updated but Native identity/health gate failed; Agent processes were not restarted')
 agent_after=[]
 for n in nodes:
  srcnode=source_nodes[n['ssh_ip']];cmd=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',srcnode['ssh_identity_file'],'-p',str(n['ssh_port']),n['ssh_target'],'sh','-s']
  out=run(cmd,f'set -eu; p=$(systemctl show {shlex.quote(n["service"])} -p MainPID --value); test "$p" -gt 1; test "$(sha256sum /proc/$p/exe | cut -d " " -f1)" = {AGENT_SHA}; echo "$p"',30)
  agent_after.append({'name':n['name'],'pid':out,'exe_sha256':AGENT_SHA})
 primary_h,_=request(PRIMARY+'/healthz');primary_r,_=request(PRIMARY+'/readyz')
 health={'run_id':RUN,'status':'hub_only_login_guard_static_healthy','build_label':LABEL,'hub_sha256':SHA,'hub_healthz_http':hz,'hub_readyz_http':rz,'fresh_admin_login_http':200,'me_http':me_code,'me_role':role,'repair_node_ids_unchanged':agent_ids==oldids,'repair_agents':[{ 'name':n['name'],'node_id':agent_ids[n['name']],'status':new[n['name']]['status'],'sync_status':new[n['name']]['sync_status'],'agent_version':new[n['name']]['agent_version']} for n in nodes],'agent_processes_before':agent_before,'agent_processes_after':agent_after,'agent_process_pids_unchanged':{x['name']:x['pid'] for x in agent_before}=={x['name']:x['pid'] for x in agent_after},'hub_config_sha256':config_sha,'hub_config_restored_24h':True,'hub_config_owner_mode':'nodesteer:nodesteer:0600','resource_inventory_unchanged':pre==post,'resource_inventory':post,'primary_get_only':{'healthz_http':primary_h,'readyz_http':primary_r},'secrets_included':False}
 HEALTH.write_text(json.dumps(health,ensure_ascii=False,indent=2)+'\n')
 REPORT.write_text(json.dumps({'run_id':RUN,'status':'hub_only_cutover_healthy','new_hub_sha256':SHA,'previous_hub_sha256':OLD_SHA,'new_binary_path':NEW_BIN,'previous_unit_backup':REMOTE_BACKUP,'private_previous_unit_backup':str(UNIT_BACKUP),'hub_config_sha256':config_sha,'private_connection':str(CONN),'health_report':str(HEALTH.relative_to(ROOT)),'agent_services_restarted':False,'agent_ids_preserved':True,'primary_services_touched':False},ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'status':'hub_only_cutover_healthy','hub_sha256':SHA,'health':[hz,rz],'admin':[200,me_code,role],'node_ids_preserved':True,'agents_online_synced':True,'agent_pids_unchanged':health['agent_process_pids_unchanged'],'inventories_unchanged':pre==post,'primary_get':[primary_h,primary_r]}))
if __name__=='__main__':main()
