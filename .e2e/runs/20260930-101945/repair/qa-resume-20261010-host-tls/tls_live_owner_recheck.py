#!/usr/bin/env python3
"""Dynamically revalidate old TLS owner root/server before touching its PID."""
import hashlib, json, socket, stat, sys, time
from pathlib import Path
import api

HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'qa-resume-20261009-host-tls'/'tls'
PLAN=json.loads((OLD/'plan.json').read_text())
ROOT_RESULT=json.loads((OLD/'owner-root-created.json').read_text())['result']
START=json.loads((OLD/'https-server-started.json').read_text())['result']
NODE='611c02a5-1859-4465-878b-d543b7f6fdec'

def main():
    before=api.get_json('/api/nodes/'+NODE)
    if before.get('status')!='online' or before.get('sync_status')!='synced':raise RuntimeError('native2_health_gate_failed')
    old_pre=json.loads((HERE.parent/'qa-resume-20261009-backend'/'preflight.json').read_text())
    peers=[x['node_id'] for x in old_pre['original_repair_nodes']]
    server=START['server']
    claims={'root':ROOT_RESULT['root'],'root_dev':ROOT_RESULT['root_identity']['dev'],'root_ino':ROOT_RESULT['root_identity']['ino'],'manifest_sha':START['manifest_sha256'],'marker_sha':ROOT_RESULT['owner_marker_sha256'],'pid':server['pid'],'starttime':server['starttime'],'uid':server['uid'],'exe_sha':server['exe_sha256'],'cgroup':server['cgroup'],'port':server['port'],'unit':PLAN['units']['https_server'],'unit_sha':ROOT_RESULT['server_unit']['file_sha256']}
    source=r'''import hashlib,json,os,socket,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';U=Path('/etc/systemd/system')/C['unit']
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700):raise RuntimeError('owned_root_changed')
if hashlib.sha256(raw).hexdigest()!=C['manifest_sha'] or m.get('nonce')!=C['nonce']:raise RuntimeError('owned_manifest_changed')
marker=D/'owner.marker'
if marker.is_symlink() or hashlib.sha256(marker.read_bytes()).hexdigest()!=C['marker_sha']:raise RuntimeError('owner_marker_changed')
if U.is_symlink() or not U.is_file() or sha(U)!=C['unit_sha'] or U.stat().st_uid!=0:raise RuntimeError('server_unit_file_changed')
P=m['units']['https_server'];pid=P['pid'];pr=Path('/proc',str(pid));rawstat=(pr/'stat').read_text();start=int(rawstat[rawstat.rfind(')')+2:].split()[19]);uid=tuple(map(int,next(x for x in (pr/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1:3]));exe=os.readlink(str(pr/'exe'));args=[x.decode(errors='replace') for x in (pr/'cmdline').read_bytes().split(b'\0') if x];cg=(pr/'cgroup').read_text().strip()
if pid!=C['pid'] or start!=C['starttime'] or uid!=(C['uid'],C['uid']) or sha(exe)!=C['exe_sha'] or cg!=C['cgroup'] or U.name not in cg or not any(x==str(D/'private'/'https_artifact_server.py') for x in args):raise RuntimeError('server_process_owner_guard_failed')
u=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',U.name],text=True,capture_output=True,timeout=10);props={}
for line in u.stdout.splitlines():
 if '=' in line:k,v=line.split('=',1);props[k]=v
if u.returncode or props.get('LoadState')!='loaded' or props.get('ActiveState')!='active' or props.get('MainPID')!=str(pid) or props.get('FragmentPath')!=str(U):raise RuntimeError('server_unit_runtime_identity_failed')
iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{C['port']:04X}";socks=[]
for line in Path('/proc/net/tcp').read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
if not socks or socks!=P.get('socket_inodes'):raise RuntimeError('loopback_listener_inode_changed')
sp=D/'private'/'server-status.json';ss=json.loads(sp.read_text())
if sp.is_symlink() or sp.stat().st_uid!=0 or stat.S_IMODE(sp.stat().st_mode)!=0o600 or ss.get('pid')!=pid or ss.get('port')!=C['port'] or ss.get('bind')!='127.0.0.1':raise RuntimeError('server_status_owner_or_port_changed')
print(json.dumps({'status':'PASS','root':str(D),'root_dev':st.st_dev,'root_ino':st.st_ino,'manifest_sha256':hashlib.sha256(raw).hexdigest(),'server':{'pid':pid,'starttime':start,'uid':uid[0],'exe_sha256':sha(exe),'cgroup':cg,'unit':U.name,'port':C['port'],'bind':'127.0.0.1','listen_socket_inodes':socks},'metrics':{k:ss.get(k,0) for k in ('http_requests','tls_handshake_failures')},'private_key_values_emitted':False},separators=(',',':')))
''' % json.dumps({**claims,'nonce':PLAN['nonce']},separators=(',',':'))
    task=api.create_task('qa-resume-20261010-host-tls-live-owner-recheck-'+PLAN['nonce'],"python3 - <<'PY'\n"+source.rstrip()+"\nPY",NODE)
    try:result=api.run_task(task)
    finally:cleanup=api.cleanup_task(task['id'],NODE)
    if result.get('status')!='SUCCESS':raise RuntimeError('live_owner_recheck_task_failed')
    value=json.loads(result.get('stdout') or '')
    after=api.get_json('/api/nodes/'+NODE)
    peers_after=[]
    for peer in peers:
        n=api.get_json('/api/nodes/'+peer);peers_after.append({'node_id':peer,'status':n.get('status'),'sync_status':n.get('sync_status')})
    health=all(x['status']=='online' and x['sync_status']=='synced' for x in peers_after+[{'status':after.get('status'),'sync_status':after.get('sync_status')}])
    if value.get('status')!='PASS' or not health:raise RuntimeError('live_owner_or_original_health_assertion_failed')
    api.write_json('tls-live-owner-recheck.json',{'status':'PASS','actual_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'node2':{k:after.get(k) for k in ('id','hostname','ip','status','sync_status')},'original_four_nodes':peers_after,'remote_owner':value,'task_id':task['id'],'execution_id':result['id'],'task_cleanup':cleanup})
    print(json.dumps({'status':'PASS','server_pid':value['server']['pid'],'server_starttime':value['server']['starttime'],'port':value['server']['port'],'manifest_sha256':value['manifest_sha256'],'original_four_online_synced':True,'task_get404':cleanup.get('get_after_delete_http')==404}))

if __name__=='__main__':main()
