#!/usr/bin/env python3
"""Read-only snapshot of owned TLS server, child cache/SQLite, and targeted journal rows."""
import hashlib,json,sys,time
from pathlib import Path
import api
HERE=Path(__file__).resolve().parent
MODE=sys.argv[1] if len(sys.argv)>1 else 'baseline'
PAYLOAD=Path(HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'application-payload.sh').read_bytes()
SHA=sys.argv[2] if len(sys.argv)>2 else hashlib.sha256(PAYLOAD).hexdigest();LIVE=json.loads((HERE/'tls-live-owner-recheck.json').read_text());child_files=sorted(HERE.glob('child-ca-*-restart.json'),key=lambda p:p.stat().st_mtime)
CUR=json.loads((child_files[-1] if child_files else HERE/'child-owner-recorded.json').read_text())['remote']['child'];ROOT=LIVE['remote_owner']['root'];PORT=LIVE['remote_owner']['server']['port'];NODE='611c02a5-1859-4465-878b-d543b7f6fdec';NODE_ID=json.loads((HERE/'child-enrollment.json').read_text())['response']['node_id'];UNIT='nodesteer-qa-tls-1358f9a990bd-child.service';APP_ID=json.loads((HERE/'application-created.json').read_text())['id'] if (HERE/'application-created.json').exists() else ''
C={'root':ROOT,'root_dev':LIVE['remote_owner']['root_dev'],'root_ino':LIVE['remote_owner']['root_ino'],'manifest_sha':json.loads((HERE/'progress.json').read_text())['current_manifest_sha256'],'marker_sha':json.loads((HERE.parent/'qa-resume-20261009-host-tls'/'tls'/'owner-root-created.json').read_text())['result']['owner_marker_sha256'],'server':LIVE['remote_owner']['server'],'child':CUR,'agent_sha':json.loads((HERE/'pre-child-owner-probe.json').read_text())['remote_agent']['binary_sha256'],'unit':UNIT,'sha':SHA,'mode':MODE,'app_id':json.loads((HERE/'application-created.json').read_text())['id'] if (HERE/'application-created.json').exists() else ''}
src=r'''import hashlib,json,os,sqlite3,stat,subprocess
from pathlib import Path
C=json.loads(%r);D=Path(C['root']);M=D/'owner-manifest.json';MARK=D/'owner.marker';DATA=D/'agent-data';CACHE=DATA/'artifacts';DB=DATA/'state.db';SHA=C['sha'];UNIT=C['unit'];CFG=D/'private'/'child.yaml'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def prop(name):
 r=subprocess.run(['systemctl','show','--no-pager','--property=LoadState,ActiveState,MainPID,FragmentPath',name],text=True,capture_output=True,timeout=10);d={}
 for line in r.stdout.splitlines():
  if '=' in line:k,v=line.split('=',1);d[k]=v
 return r,d
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C['root_dev'],C['root_ino'],0,0o700) or hashlib.sha256(raw).hexdigest()!=C['manifest_sha']:raise RuntimeError('owned_root_manifest_guard_failed')
CH=C['child'];r,d=prop(UNIT);pid=int(d.get('MainPID','0') or 0)
if r.returncode or d.get('ActiveState')!='active' or pid!=CH['pid'] or d.get('FragmentPath')!='/etc/systemd/system/'+UNIT:raise RuntimeError('child_unit_owner_changed')
p=Path('/proc',str(pid));ps=(p/'stat').read_text();start=int(ps[ps.rfind(')')+2:].split()[19]);exe=os.readlink(str(p/'exe'))
if start!=CH['starttime'] or sha(exe)!=C['agent_sha'] or C['unit'] not in (p/'cgroup').read_text():raise RuntimeError('child_process_owner_changed')
if CFG.is_symlink() or CFG.stat().st_uid!=0 or stat.S_IMODE(CFG.stat().st_mode)!=0o600:raise RuntimeError('child_config_owner_mode_failed')
config=CFG.read_text();if_ca='tls_ca_file:' in config
if C['mode']=='baseline' and if_ca:raise RuntimeError('baseline_child_config_has_ca')
if DATA.is_symlink() or CACHE.is_symlink() or DB.is_symlink() or DATA.resolve()!=DATA or CACHE.resolve()!=CACHE:raise RuntimeError('cache_db_path_identity_failed')
exists=(CACHE/SHA).exists();fsha=sha(CACHE/SHA) if exists and (CACHE/SHA).is_file() else None
con=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=2);count=con.execute('SELECT COUNT(*) FROM artifacts WHERE sha256=?',(SHA,)).fetchone()[0];con.close()
tmps=list(CACHE.glob(SHA+'.tmp')) if CACHE.exists() else []
statusp=D/'private'/'server-status.json';status=json.loads(statusp.read_text())
if statusp.is_symlink() or statusp.stat().st_uid!=0 or stat.S_IMODE(statusp.stat().st_mode)!=0o600 or status.get('pid')!=C['server']['pid'] or status.get('port')!=C['server']['port']:raise RuntimeError('server_status_identity_failed')
journal=subprocess.run(['journalctl','--no-pager','-o','cat','-n','250','-u',UNIT],text=True,capture_output=True,timeout=12)
rawcfg=CFG.read_text();secret=''
for line in rawcfg.splitlines():
 if line.startswith('registration_token:'):secret=json.loads(line.split(':',1)[1].strip());break
matching=[line.replace(secret,'[REDACTED]')[-700:] for line in journal.stdout.splitlines() if 'application artifact prefetch failed' in line and C['app_id'] and C['app_id'] in line]
logs=[];logp=D/'private'/'server-access.jsonl'
if logp.exists():
 for line in logp.read_text().splitlines():
  try:
   row=json.loads(line)
   if row.get('path','').endswith(SHA) or row.get('path','').startswith('/artifact/') or row.get('path','').startswith('/sha-mismatch/'):logs.append(row)
  except ValueError:raise RuntimeError('server_access_log_parse_failed')
print(json.dumps({'status':'PASS','mode':C['mode'],'cache':{'path':str(CACHE/SHA),'exists':exists,'file_sha256':fsha,'sqlite_artifact_rows':count,'exact_tmp_exists':bool(tmps)},'cache_dir_owner':CACHE.stat().st_uid if CACHE.exists() else None,'state_db':{'path':str(DB),'exists':DB.exists(),'uid':DB.stat().st_uid if DB.exists() else None},'server':{k:status.get(k) for k in ('pid','port','bind','http_requests','tls_handshake_failures')},'matching_server_access_rows':logs,'agent_prefetch_warnings':matching,'journalctl_returncode':journal.returncode,'child':{'pid':pid,'starttime':start,'tls_ca_configured':if_ca,'config_sha256':sha(CFG)},'private_values_emitted':False},separators=(',',':')))
'''%json.dumps(C,separators=(',',':'))
command="python3 - <<'PY'\n"+src.rstrip()+"\nPY";api.validate_command(command,'tls-state-'+MODE)
t=api.create_task('qa-resume-20261010-host-tls-state-'+MODE+'-1358f9a990bd',command,NODE)
try:r=api.run_task(t)
finally:c=api.cleanup_task(t['id'],NODE)
if r.get('status')!='SUCCESS':raise RuntimeError('tls_state_probe_failed')
out=json.loads(r.get('stdout') or '')
if out.get('status')!='PASS':raise RuntimeError('tls_state_probe_output_invalid')
stamp=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
api.write_json('tls-state-'+MODE+'.json',{'status':'PASS','actual_at':stamp,'remote':out,'task_id':t['id'],'execution_id':r['id'],'cleanup':c})
print(json.dumps({'status':'PASS','actual_at':stamp,'mode':MODE,'cache_exists':out['cache']['exists'],'sqlite_artifact_rows':out['cache']['sqlite_artifact_rows'],'tmp_exists':out['cache']['exact_tmp_exists'],'server_metrics':out['server'],'task_get404':c['get_after_delete_http']==404}))
