import hashlib,json,os,pathlib,re,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';U='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';C=str(R/'agent.yaml');APPB=R/'app/bin/qa-app';APPC=R/'app/config/qa-app.env';APPU='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';APPUP=pathlib.Path('/etc/systemd/system')/APPU;V1B='35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c';V1C='5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34';V1U='6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa';APPID='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';DEPLOY='e3298145-23c7-4ee8-98b5-cf53b50e0345';ATTEMPT_VERSION='v2-attempt3';PU='nodesteer-agent-repair-20260930-101945-native-1.service';BU='nodesteer-agent.service'
def run(a,t=10):return subprocess.run(a,text=True,capture_output=True,timeout=t)
def props(u):
 p=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec']);return dict(z.split('=',1) for z in p.stdout.splitlines() if '=' in z)
def proc(pid):
 raw=open(f'/proc/{pid}/stat').read();q=raw[raw.rfind(')')+2:].split();s=open(f'/proc/{pid}/status').read();uid=int(re.search(r'^Uid:\s+(\d+)',s,re.M).group(1));a=open(f'/proc/{pid}/cmdline','rb').read();h=hashlib.sha256();f=open(f'/proc/{pid}/exe','rb')
 for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return {'pid':pid,'state':q[0],'startticks':int(q[19]),'uid':uid,'argv':[x.decode(errors='replace') for x in a.split(b'\0') if x],'argv_sha':hashlib.sha256(a).hexdigest(),'exe':os.readlink(f'/proc/{pid}/exe'),'exe_sha':h.hexdigest(),'cgroup':open(f'/proc/{pid}/cgroup').read().strip()}
def sha(p):
 h=hashlib.sha256();f=open(p,'rb')
 for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def die(s,**kw):print(json.dumps({'status':s,**kw},sort_keys=True));raise SystemExit(2)
o=json.loads((R/'owner.json').read_text());oe={'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':U,'binary_path':B,'binary_sha256':BS,'config_path':C,'data_dir':str(R/'state')}
if any(o.get(k)!=v for k,v in oe.items()):die('OWNER_MARKER_MISMATCH')
if not R.is_dir() or not pathlib.Path(C).is_file() or not (R/'state/state.db').is_file() or sha(B)!=BS:die('OWNED_ROOT_OR_BINARY_MISMATCH')
if (sha(APPB),sha(APPC),sha(APPUP))!=(V1B,V1C,V1U) or props(APPU).get('ActiveState')!='active':die('V1_APP_GUARD_FAILED')
import sqlite3
con=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True);con.row_factory=sqlite3.Row;rows=[dict(z) for z in con.execute('select id,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? and to_version=?',(APPID,ATTEMPT_VERSION))];con.close()
if len(rows)!=1 or rows[0].get('id')!=DEPLOY or rows[0].get('phase')!='PREPARING' or any(rows[0].get(k)!='' for k in ['backup_path','backup_config_path','backup_unit_path']):die('ATTEMPT02_JOURNAL_GUARD_FAILED',rows=rows)
parents={}
for u in [PU,BU]:
 up=props(u);pid=int(up.get('MainPID','0') or 0)
 if up.get('ActiveState')!='active' or pid<=0:die('PARENT_UNIT_NOT_ACTIVE',unit=u)
 pr=proc(pid);parents[u]={'props':up,'proc':pr}
 expected_sha=BS if u==PU else '43bf33d'
 if pr['uid']!=0 or not pr['exe_sha'].startswith(expected_sha):die('PARENT_FINGERPRINT_FAILED',unit=u)
cur=props(U);old=None;pid=int(cur.get('MainPID','0') or 0)
if cur.get('ActiveState')=='active' and pid:
 old=proc(pid)
 if old['uid']!=0 or old['argv']!=[B,'--config',C] or old['exe']!=B or old['exe_sha']!=BS or '/system.slice/'+U not in old['cgroup']:
  die('CURRENT_CHILD_OWNER_GUARD_FAILED',unit=cur,proc=old)
 st=run(['systemctl','stop',U],15)
 if st.returncode:die('OWNED_CHILD_STOP_FAILED',stderr=st.stderr[-500:])
for _ in range(50):
 cur=props(U);pid=int(cur.get('MainPID','0') or 0)
 if cur.get('ActiveState')!='active' and pid==0:break
 time.sleep(.1)
else:die('OWNED_CHILD_DID_NOT_STOP',unit=cur)
run(['systemctl','reset-failed',U],10)
for _ in range(30):
 cur=props(U)
 if cur.get('LoadState') in {'not-found',''}:break
 time.sleep(.1)
else:die('TRANSIENT_UNIT_NOT_UNLOADED_NO_RECREATE',unit=cur)
cmd=['systemd-run','--quiet','--no-block','--unit='+U,'--property=User=root','--property=Restart=no','--property=KillMode=control-group','--property=RuntimeMaxSec=3600s','--property=TimeoutStopSec=3s','--property=WorkingDirectory='+str(R),'--property=StandardOutput=append:'+str(R/'log/agent.log'),'--property=StandardError=append:'+str(R/'log/agent.log'),'--property=UnsetEnvironment=NODESTEER_PARAM_registration_token',B,'--config',C]
r=run(cmd,15)
if r.returncode:die('SYSTEMD_RUN_FAILED',returncode=r.returncode,stderr=r.stderr[-500:])
new=None;up={}
for _ in range(100):
 up=props(U);pid=int(up.get('MainPID','0') or 0)
 if pid:
  try:new=proc(pid)
  except Exception:new=None
  if new:break
 time.sleep(.1)
if not new:die('NEW_CHILD_PID_MISSING',unit=up)
checks={'different_pid':old is None or new['pid']!=old['pid'],'root_uid':new['uid']==0,'argv_exact':new['argv']==[B,'--config',C],'exe_path':new['exe']==B,'exe_sha':new['exe_sha']==BS,'cgroup':new['cgroup'].endswith('/system.slice/'+U),'unit_active':up.get('ActiveState')=='active','runtime_one_hour':up.get('RuntimeMaxUSec')=='1h'}
if not all(checks.values()):die('NEW_CHILD_GUARD_FAILED',checks=checks,unit=up,proc=new)
for u,saved in parents.items():
 nowp=props(u);p=proc(int(nowp.get('MainPID','0') or 0))
 if nowp.get('ActiveState')!='active' or p['pid']!=saved['proc']['pid'] or p['startticks']!=saved['proc']['startticks'] or not p['exe_sha'].startswith(BS if u==PU else '43bf33d'):die('PARENT_CHANGED',unit=u)
print(json.dumps({'status':'SAME_OWNED_CHILD_RECREATED','owner_nonce':N,'node_id':NODE,'unit':U,'root':str(R),'runtime_max':up.get('RuntimeMaxUSec'),'old_process':old,'new_process':new,'new_unit':up,'app_v1_sha256':{'binary':V1B,'config':V1C,'unit':V1U},'attempt02_deployment_id':DEPLOY,'attempt02_journal_before_restart':rows[0],'preserved_parent_units':[{'unit':u,'pid':v['proc']['pid'],'startticks':v['proc']['startticks']} for u,v in parents.items()]},sort_keys=True),flush=True)
