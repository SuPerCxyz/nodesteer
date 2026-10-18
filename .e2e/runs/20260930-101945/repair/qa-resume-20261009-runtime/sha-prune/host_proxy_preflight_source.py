import hashlib,importlib.util,json,os,pathlib,pwd,socket,sqlite3,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CU='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';AU='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9'
def cmd(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=8);return {'rc':p.returncode,'out':p.stdout.strip()}
def show(u):
 p=cmd(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec']);return dict(x.split('=',1) for x in p['out'].splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for x in iter(lambda:f.read(1048576),b''):h.update(x)
 return h.hexdigest()
def proc(pid):
 raw=open('/proc/%s/stat'%pid).read();q=raw[raw.rfind(')')+2:].split();st=open('/proc/%s/status'%pid).read();uid=int([x.split()[1] for x in st.splitlines() if x.startswith('Uid:')][0]);ar=open('/proc/%s/cmdline'%pid,'rb').read()
 return {'pid':pid,'startticks':int(q[19]),'state':q[0],'uid':uid,'argv':[x.decode(errors='replace') for x in ar.split(b'\0') if x],'argv_sha256':hashlib.sha256(ar).hexdigest(),'exe_path':os.readlink('/proc/%s/exe'%pid),'exe_sha256':sha('/proc/%s/exe'%pid),'cgroup':open('/proc/%s/cgroup'%pid).read().strip()}
o=json.loads((R/'owner.json').read_text());expected={'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':CU,'binary_path':B,'binary_sha256':BS,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')};assert all(o.get(k)==v for k,v in expected.items())
cu=show(CU);cp=proc(int(cu.get('MainPID','0')));au=show(AU);db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);rev=db.execute('select global_revision from sync_state where id=1').fetchone()[0];scripts=[dict(id=r[0],revision=r[1],sha256=r[2]) for r in db.execute('select id,revision,sha256 from scripts order by id')];apps=[r[0] for r in db.execute('select id from applications order by id')];db.close()
assert cu.get('ActiveState')=='active' and cp['uid']==0 and cp['argv']==[B,'--config',str(R/'agent.yaml')] and cp['exe_sha256']==BS and cp['cgroup'].endswith('/system.slice/'+CU)
mods={x:bool(importlib.util.find_spec(x)) for x in ['websockets','websocket','aiohttp']};s=socket.socket();s.bind(('127.0.0.1',0));available=True;s.close();
stat=os.statvfs('/tmp');free=stat.f_bavail*stat.f_frsize
route=cmd(['ip','route','get','192.168.100.209']) if pathlib.Path('/usr/sbin/ip').exists() or pathlib.Path('/usr/bin/ip').exists() else {'rc':127,'out':'ip tool absent'}
print(json.dumps({'probe':'Native1 host read-only proxy/runtime preflight','target_child_identity':{'node_id':NODE,'agent_id':AGENT,'owner_nonce':N,'root':str(R),'agent_unit':CU,'agent_process':cp},'child_unit':cu,'app_unit':au,'child_local_global_revision':rev,'scripts_local':[{'id':x['id'],'revision':x['revision'],'sha256':x['sha256']} for x in scripts],'local_application_ids':apps,'python3_version':cmd(['python3','--version']),'python_websocket_modules':mods,'stdlib_asyncio_socket_modules':{x:bool(importlib.util.find_spec(x)) for x in ['asyncio','socket','ssl','struct','hashlib','json']},'nobody_uid':pwd.getpwnam('nobody').pw_uid,'loopback_ephemeral_bind_available':available,'tmp_free_bytes':free,'route_to_repair_hub':route,'systemd_version':cmd(['systemd-run','--version']),'no_child_config_read_or_write':True},sort_keys=True),flush=True)
