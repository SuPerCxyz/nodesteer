import hashlib,json,os,pathlib,re,sqlite3,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';UNIT='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';EX='e5d8ef30-de3e-4cf0-a70a-7fcd467b7fab';VERSION='v2-attempt2'
def sh(p):
 h=hashlib.sha256();f=open(p,'rb')
 for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def props(u):
 p=subprocess.run(['systemctl','show',u,'-p','ActiveState','-p','MainPID','-p','RuntimeMaxUSec','-p','ControlGroup'],text=True,capture_output=True,timeout=5);return dict(a.split('=',1) for a in p.stdout.splitlines() if '=' in a)
owner=json.loads((R/'owner.json').read_text());assert all(owner.get(k)==v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':UNIT,'binary_path':B,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')}.items())
db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
row=db.execute('SELECT id,task_id,application_id,application_version,application_operation,application_health,node_id,status,offline,synced,block_reason,start_time,end_time FROM executions WHERE id=?',(EX,)).fetchone();journal=db.execute('SELECT id,from_version,to_version,phase,backup_path,backup_config_path,backup_unit_path FROM deployments WHERE application_id=? AND to_version=?',(APP,VERSION)).fetchall();db.close()
unit=props(UNIT);pid=int(unit.get('MainPID','0') or 0);proc=None
if pid:
 a=open(f'/proc/{pid}/cmdline','rb').read();q=open(f'/proc/{pid}/stat').read();z=q[q.rfind(')')+2:].split();s=open(f'/proc/{pid}/status').read();m=re.search(r'^Uid:\s+(\d+)',s,re.M);proc={'pid':pid,'startticks':int(z[19]),'state':z[0],'uid':int(m.group(1)) if m else None,'argv':[v.decode(errors='replace') for v in a.split(b'\0') if v],'argv_sha256':hashlib.sha256(a).hexdigest(),'exe_path':os.readlink(f'/proc/{pid}/exe'),'exe_sha256':sh(f'/proc/{pid}/exe'),'cgroup':open(f'/proc/{pid}/cgroup').read().strip()}
appu='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';ap=props(appu);files={'binary_sha256':sh(R/'app/bin/qa-app'),'config_sha256':sh(R/'app/config/qa-app.env'),'unit_sha256':sh(pathlib.Path('/etc/systemd/system')/appu)}
print(json.dumps({'oracle':'POST_RESTART_LOCAL_AGENT_STATE','owner_nonce':N,'local_execution':dict(row) if row else None,'attempt02_journal':[dict(x) for x in journal],'child_agent_unit':unit,'child_agent_process':proc,'app_v1_files':files,'app_unit':ap,'captured_unix':int(time.time())},sort_keys=True),flush=True)
