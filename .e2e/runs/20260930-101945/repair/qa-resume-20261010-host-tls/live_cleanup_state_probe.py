#!/usr/bin/env python3
"""Read-only dynamic inventory before stopping an offline child."""
import json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent;LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());PRE=json.loads((HERE/'pre-child-owner-probe.json').read_text());NODE='611c02a5-1859-4465-878b-d543b7f6fdec';CHILD_NODE=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];ROOT=LIVE['remote_owner']['root'];C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'server_unit_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['server_unit']['file_sha256'],'parent':PRE['remote_agent']['process'],'agent_sha':PRE['remote_agent']['binary_sha256'],'child_node_id':CHILD_NODE,'child_unit':'nodesteer-qa-tls-1358f9a990bd-child.service','server_unit':LIVE['remote_owner']['server']['unit'],'app_unit':'nodesteer-qa-tls-1358f9a990bd-app.service'}
src=r'''import hashlib,json,os,stat,subprocess,socket
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';AGENT=Path('/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def prop(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for l in r.stdout.splitlines():
  if '=' in l:k,v=l.split('=',1);d[k]=v
 return r,d
def proc(pid):
 p=Path('/proc',str(pid));s=(p/'stat').read_text();start=int(s[s.rfind(')')+2:].split()[19]);uid=int(next(x for x in (p/'status').read_text().splitlines() if x.startswith('Uid:')).split()[1]);exe=os.readlink(str(p/'exe'));argv=[x.decode(errors='replace') for x in (p/'cmdline').read_bytes().split(b'\0') if x];cg=(p/'cgroup').read_text().strip();return {'pid':pid,'starttime':start,'uid':uid,'exe_sha256':sha(exe),'argv':argv,'cgroup':cg}
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('root_manifest_identity_failed')
if MARK.is_symlink() or sha(MARK)!=C['marker_sha']:raise RuntimeError('owner_marker_identity_failed')
P=C['parent'];pp=proc(P['pid'])
if pp['starttime']!=P['starttime'] or pp['exe_sha256']!=C['agent_sha'] or pp['cgroup']!=P['cgroup']:raise RuntimeError('native2_parent_owner_changed')
rows={}
for key,name,pathsha in (('child',C['child_unit'],m['units']['child']['file_sha256']),('server',C['server_unit'],C['server_unit_sha'])):
 U=Path('/etc/systemd/system')/name;r,d=prop(name);item={'unit_file_exists':U.exists(),'unit_symlink':U.is_symlink(),'unit_file_sha':sha(U) if U.exists() and not U.is_symlink() else None,'expected_unit_sha':pathsha,'systemd':d}
 if item['unit_file_sha']!=pathsha or U.is_symlink():raise RuntimeError(key+'_unit_file_identity_changed')
 pid=int(d.get('MainPID','0') or 0)
 if pid:
  item['process']=proc(pid)
 else:item['process']=None
 rows[key]=item
S=rows['server']['process']
if S:
 expected=C['server'];
 if S['pid']!=expected['pid'] or S['starttime']!=expected['starttime'] or S['uid']!=expected['uid'] or S['exe_sha256']!=expected['exe_sha256'] or S['cgroup']!=expected['cgroup']:raise RuntimeError('server_process_not_manifest_owned')
 iphex=socket.inet_aton('127.0.0.1')[::-1].hex().upper();porthex=f"{C['server']['port']:04X}";socks=[]
 for l in Path('/proc/net/tcp').read_text().splitlines()[1:]:
  f=l.split()
  if f[1]==iphex+':'+porthex and f[3]=='0A':socks.append(f[9])
 rows['server']['socket_inodes']=socks
CH=m['units']['child'];Q=rows['child']['process']
if Q:
 if Q['pid']!=CH['pid'] or Q['starttime']!=CH['starttime'] or Q['uid']!=CH['uid'] or Q['exe_sha256']!=CH['exe_sha256'] or Q['cgroup']!=CH['cgroup']:raise RuntimeError('child_process_not_manifest_owned')
app=Path('/etc/systemd/system')/C['app_unit'];link=Path('/etc/systemd/system/multi-user.target.wants')/C['app_unit'];files=[D/'app'/'bin'/'qa-app',D/'app'/'config'/'qa.env']
print(json.dumps({'status':'PASS','root_dev':st.st_dev,'root_ino':st.st_ino,'manifest_sha256':hashlib.sha256(raw).hexdigest(),'parent_pid':P['pid'],'child_unit':rows['child'],'server_unit':rows['server'],'app_resources_absent':not app.exists() and not app.is_symlink() and not link.exists() and not link.is_symlink() and all(not p.exists() for p in files),'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
cmd="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(cmd,'live-cleanup-state-probe')
t=api.create_task('qa-resume-20261010-host-tls-live-cleanup-state-1358f9a990bd',cmd,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('live_cleanup_probe_task_failed')
out=json.loads(r.get('stdout') or '')
api.write_json('live-cleanup-state.json',{'status':'PASS','actual_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'child_hub_node':{k:api.get_json('/api/nodes/'+CHILD_NODE).get(k) for k in ('id','hostname','ip','status','sync_status','last_seen')},'remote':out,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
print(json.dumps({'status':'PASS','child_node_status':api.get_json('/api/nodes/'+CHILD_NODE).get('status'),'child_systemd':out['child_unit']['systemd'],'child_pid':(out['child_unit']['process'] or {}).get('pid'),'server_active':out['server_unit']['systemd'].get('ActiveState'),'server_pid':(out['server_unit']['process'] or {}).get('pid'),'listener_sockets':out['server_unit'].get('socket_inodes'),'manifest_sha256':out['manifest_sha256'],'task_get404':c['get_after_delete_http']==404}))
