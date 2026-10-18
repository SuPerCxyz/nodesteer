import hashlib,json,os,pathlib,pwd,subprocess,time
R=pathlib.Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=pathlib.Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');N='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';BS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=8);return {'rc':p.returncode,'out':p.stdout.strip()}
def show(u):
 x=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','ExecStart']);return dict(q.split('=',1) for q in x['out'].splitlines() if '=' in q)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
o=json.loads((R/'owner.json').read_text()); owner_ok=all(o.get(k)==v for k,v in {'owner_nonce':N,'node_id':NODE,'agent_id':AGENT,'unit':'nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service','binary_path':B,'binary_sha256':BS,'config_path':str(R/'agent.yaml'),'data_dir':str(R/'state')}.items())
cu=show(o['unit']); pid=int(cu.get('MainPID','0') or 0); q=open('/proc/%d/stat'%pid).read().split(') ',1)[1].split() if pid else []; st=open('/proc/%d/status'%pid).read() if pid else ''; cfg=open('/proc/%d/cmdline'%pid,'rb').read() if pid else b''
proc={'pid':pid,'startticks':int(q[19]) if q else None,'uid':int([x.split()[1] for x in st.splitlines() if x.startswith('Uid:')][0]) if st else None,'argv_sha256':hashlib.sha256(cfg).hexdigest(),'exe_sha256':sha('/proc/%d/exe'%pid) if pid else None,'cgroup':open('/proc/%d/cgroup'%pid).read().strip() if pid else None}
au=show('nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service'); pu=show(UNIT)
root_meta={'path':str(P),'exists':P.exists(),'symlink':P.is_symlink(),'owner_uid':P.stat().st_uid if P.exists() else None,'mode':oct(P.stat().st_mode&0o7777) if P.exists() else None}
print(json.dumps({'probe':'read-only proxy setup preflight','owner_matches':owner_ok,'child_unit':cu,'child_proc':proc,'app_unit':au,'proxy_unit':pu,'proxy_root':root_meta,'nobody':{'uid':pwd.getpwnam('nobody').pw_uid,'gid':pwd.getpwnam('nobody').pw_gid},'tools':{x:run(['sh','-c','command -v '+x+' || true']) for x in ['systemd-run','base64','gzip','go']},'captured_unix':int(time.time())},sort_keys=True),flush=True)
