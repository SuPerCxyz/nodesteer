import hashlib,json,os,pathlib,re,sqlite3,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';V='v2-attempt3';U='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';APPB=R/'app/bin/qa-app';APPC=R/'app/config/qa-app.env';APPU='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';APPUP=pathlib.Path('/etc/systemd/system')/APPU
V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def sh(p):
 h=hashlib.sha256();f=open(p,'rb')
 for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def show(u):
 p=subprocess.run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec'],text=True,capture_output=True,timeout=6);return dict((z.split('=',1) for z in p.stdout.splitlines() if '=' in z))
def proc(p):
 if p<=0:return None
 raw=open(f'/proc/{p}/stat').read();q=raw[raw.rfind(')')+2:].split();s=open(f'/proc/{p}/status').read();m=re.search(r'^Uid:\s+(\d+)',s,re.M);cmd=open(f'/proc/{p}/cmdline','rb').read();
 return {'pid':p,'state':q[0],'startticks':int(q[19]),'uid':int(m.group(1)) if m else None,'argv':[v.decode(errors='replace') for v in cmd.split(b'\0') if v],'argv_sha256':hashlib.sha256(cmd).hexdigest(),'exe_path':os.readlink(f'/proc/{p}/exe'),'exe_sha256':sh(f'/proc/{p}/exe'),'cgroup':open(f'/proc/{p}/cgroup').read().strip()}
owner=json.loads((R/'owner.json').read_text());expected={'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':U,'binary_path':B,'binary_sha256':BS,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')};owner_ok=all(owner.get(k)==v for k,v in expected.items())
con=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);con.row_factory=sqlite3.Row;rows=[dict(x) for x in con.execute('SELECT id,application_id,from_version,to_version,phase,backup_path,backup_config_path,backup_unit_path,started_at,updated_at FROM deployments WHERE application_id=? AND to_version=?',(APP,V))];con.close()
child=show(U);app=show(APPU);apphash={'binary':sh(APPB),'config':sh(APPC),'unit':sh(APPUP)};parents={}
for u in ['nodesteer-agent-repair-20260930-101945-native-1.service','nodesteer-agent.service']:
 p=show(u);pid=int(p.get('MainPID','0') or 0);parents[u]={'unit':p,'process':proc(pid)}
print(json.dumps({'oracle':'POST_SIGKILL_PRE_RESTART_READ_ONLY','owner_marker_valid':owner_ok,'owner_identity':{k:owner.get(k) for k in ['owner_nonce','node_id','agent_id','unit','binary_sha256']},'child_unit':child,'child_process':proc(int(child.get('MainPID','0') or 0)),'attempt02_deployment_rows':rows,'app_v1_sha256':apphash,'app_unit':app,'v1_hashes_match':apphash==V1,'v1_unit_active':app.get('ActiveState')=='active','parents':parents,'captured_unix':int(time.time())},sort_keys=True),flush=True)
