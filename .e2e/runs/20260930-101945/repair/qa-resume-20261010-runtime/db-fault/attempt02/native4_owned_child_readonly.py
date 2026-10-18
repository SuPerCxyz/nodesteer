import hashlib,json,shlex,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';ROOT='/tmp/qa-resume-20261008-child-ciivjupc';AGENTSHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUPSHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';CHILDSHA=AGENTSHA
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
# Owner-protected read-only source. It suppresses manifest marker/token fingerprints and credential identity values.
src=r'''import hashlib,json,os,pathlib,re,sqlite3,stat,struct
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';BIN=D/'nodesteer-agent';BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP='/usr/bin/python3.13';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531';DEV=33;INO=135;UID=65534
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();tail=raw[raw.rfind(')')+2:].split();st=(q/'status').read_text().splitlines();uid=int(next(x for x in st if x.startswith('Uid:')).split()[1]);a=(q/'cmdline').read_bytes();argv=[x.decode(errors='replace') for x in a.split(b'\0') if x];return {'pid':pid,'startticks':int(tail[19]),'uid':uid,'exe_path':os.readlink(q/'exe'),'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
def scan():
 child=[];sup=[]
 for ent in Path('/proc').iterdir():
  if not ent.name.isdigit():continue
  try:p=proc(int(ent.name))
  except (FileNotFoundError,ProcessLookupError,PermissionError,ValueError):continue
  if p['uid']!=UID:continue
  if p['exe_path']==str(BIN) and p['exe_sha256']==BIN_SHA and any(x==str(D/'child.yaml') for x in p['argv']):child.append(p)
  if p['exe_path']==SUP and p['exe_sha256']==SUP_SHA and any(x==str(D) or x.startswith(str(D)+os.sep) for x in p['argv']):sup.append(p)
 if len(child)!=1 or len(sup)!=1:raise RuntimeError('owned child/supervisor process cardinality mismatch')
 return child[0],sup[0]
def filemeta(p):
 s=p.lstat();return {'path':str(p),'dev':s.st_dev,'inode':s.st_ino,'uid':s.st_uid,'gid':s.st_gid,'mode':oct(s.st_mode&0o7777),'size':s.st_size,'sha256':sha(p) if p.is_file() else None,'symlink':p.is_symlink()}
if D.is_symlink() or D.resolve()!=D:raise SystemExit('owned root symlink/path guard failed')
ds=D.lstat()
if (ds.st_dev,ds.st_ino,ds.st_uid,stat.S_IMODE(ds.st_mode))!=(DEV,INO,UID,0o700):raise SystemExit('owned root inode/owner/mode guard failed')
M=D/'owner-manifest.json'
if M.is_symlink() or M.stat().st_uid!=UID or stat.S_IMODE(M.stat().st_mode)!=0o600:raise SystemExit('owner manifest file guard failed')
manifest=json.loads(M.read_text())
if manifest.get('node_id')!=NODE or manifest.get('agent_id')!=AGENT or manifest.get('directory')!=str(D):raise SystemExit('owner manifest IDs/path mismatch')
endpoint=manifest.get('hub_endpoint') or {}
if endpoint.get('host')!='192.168.100.209' or endpoint.get('port')!=18080 or endpoint.get('scheme')!='ws':raise SystemExit('owner manifest hub endpoint mismatch')
binary=manifest.get('binary') or {};childcfg=manifest.get('config') or {};proxy=manifest.get('proxy') or {}
if binary.get('copy_path')!=str(BIN) or binary.get('copy_sha256')!=BIN_SHA or childcfg.get('path')!=str(D/'child.yaml'):raise SystemExit('manifest binary/config identity mismatch')
child,supervisor=scan()
if child['exe_sha256']!=BIN_SHA or supervisor['exe_sha256']!=SUP_SHA or child['uid']!=UID or supervisor['uid']!=UID:raise SystemExit('owned process executable/uid mismatch')
config=filemeta(D/'child.yaml');manifestmeta=filemeta(M);dbpath=D/'data'/'state.db';dbmeta=filemeta(dbpath)
if config['uid']!=UID or config['mode']!='0o600' or dbmeta['uid']!=UID:raise SystemExit('owned config/database ownership mismatch')
# Read-only SQLite. Report schema/IDs/revisions/SHA only; never select credential, script content, or config values.
db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
quick=db.execute('PRAGMA quick_check').fetchone()[0];user_version=db.execute('PRAGMA user_version').fetchone()[0];tables=[x[0] for x in db.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")];localrev=db.execute('select global_revision from sync_state where id=1').fetchone()[0];identity={x['key']:x['value'] for x in db.execute("select key,value from identity where key in ('node_id','agent_id')")}
if identity.get('node_id')!=NODE or identity.get('agent_id')!=AGENT:raise RuntimeError('local DB identity mismatch')
scripts=[{'id':x['id'],'revision':x['revision'],'sha256':x['sha256']} for x in db.execute('select id,revision,sha256 from scripts order by id')]
tasks=[]
for x in db.execute('select id,definition_json,revision,enabled from tasks order by id'):
 j=json.loads(x['definition_json']);tasks.append({'id':x['id'],'revision':x['revision'],'enabled':bool(x['enabled']),'type':j.get('type'),'script_id':j.get('script_id'),'application_id':j.get('application_id')})
schedules=[]
for x in db.execute('select id,definition_json,revision,enabled from schedules order by id'):
 j=json.loads(x['definition_json']);schedules.append({'id':x['id'],'revision':x['revision'],'enabled':bool(x['enabled']),'task_id':j.get('task_id')})
apps=[]
for x in db.execute('select id,definition_json,revision from applications order by id'):
 j=json.loads(x['definition_json']);apps.append({'id':x['id'],'revision':x['revision'],'unit_name':j.get('unit_name')})
journals=[{'id':x['id'],'application_id':x['application_id'],'from_version':x['from_version'],'to_version':x['to_version'],'phase':x['phase'],'backup_binary_present':bool(x['backup_path']),'backup_config_present':bool(x['backup_config_path']),'backup_unit_present':bool(x['backup_unit_path'])} for x in db.execute('select id,application_id,from_version,to_version,phase,backup_path,backup_config_path,backup_unit_path from deployments order by started_at')]
unit_registry=[{'application_id':x['application_id'],'unit_name':x['unit_name']} for x in db.execute('select application_id,unit_name from unit_registry order by application_id')]
cols={t:[x['name'] for x in db.execute('pragma table_info('+t+')')] for t in tables};db.close()
# Validate the existing loopback proxy listener is still attached to the exact owned supervisor PID; no connect/start/stop.
port=int(proxy.get('port') or 0);listen_inodes=[]
for name in ('tcp','tcp6'):
 f=Path('/proc/net/'+name)
 if not f.exists():continue
 for line in f.read_text().splitlines()[1:]:
  a=line.split()
  if len(a)>9 and a[1].rsplit(':',1)[1].lower()==format(port,'04x').lower() and a[3]=='0A':listen_inodes.append(a[9])
fd_inodes=[]
for fd in Path('/proc/%d/fd'%supervisor['pid']).iterdir():
 try:
  link=os.readlink(fd)
  if link.startswith('socket:['):fd_inodes.append(link[8:-1])
 except (FileNotFoundError,PermissionError):pass
proxy_owned_listening=bool(list(set(listen_inodes)&set(fd_inodes)))
if port and not proxy_owned_listening:raise RuntimeError('manifested proxy listener not owned by current supervisor')
wal=[]
for p in (Path(str(dbpath)+'-wal'),Path(str(dbpath)+'-shm')):
 if p.exists():wal.append(filemeta(p))
# Suppress any accidental sensitive field from output by only constructing explicit selected values.
out={'status':'NATIVE4_CHILD_READONLY_OWNER_PROCESS_DATABASE_INVENTORY','owner_root':{'path':str(D),'dev':ds.st_dev,'inode':ds.st_ino,'uid':ds.st_uid,'gid':ds.st_gid,'mode':oct(ds.st_mode&0o7777),'owner_manifest_sha256':sha(M)},'manifest_identity':{'node_id':manifest.get('node_id'),'agent_id':manifest.get('agent_id'),'binary_sha256':binary.get('copy_sha256'),'config_path':childcfg.get('path'),'hub_endpoint':{'scheme':endpoint.get('scheme'),'host':endpoint.get('host'),'port':endpoint.get('port')},'proxy':{'bind_ip':proxy.get('bind_ip'),'port':port,'upstream_host':proxy.get('upstream_host'),'upstream_port':proxy.get('upstream_port'),'proxy_listening_owned_by_supervisor':proxy_owned_listening}},'child_process':child,'supervisor_process':supervisor,'config_metadata':{k:v for k,v in config.items() if k!='path'},'db_metadata':{k:v for k,v in dbmeta.items() if k!='path'},'db_wal_shm_metadata':wal,'db_integrity':quick,'schema':{'user_version':user_version,'tables':tables,'columns':cols},'local_identity':identity,'local_global_revision':localrev,'scripts':scripts,'tasks':tasks,'schedules':schedules,'applications':apps,'deployment_journal':journals,'unit_registry':unit_registry,'read_only':True,'credentials_or_contents_emitted':False}
print(json.dumps(out,separators=(',',':'),sort_keys=True))'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-native4-db-fault-readonly-inventory-attempt02-15961ef7-870358cd92ee';task=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=task['id'];e={'task_id':tid,'task_name':name,'target_parent_node_id':PARENT,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'retry':0,'schedule_present':False,'timeout':60,'scope':'read-only current Native4 child owner/process/database/object inventory; no DB write/lock or process/network action'}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==e['command_sha256'];x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+55;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';e.update({'execution_id':eid,'execution_status':fin.get('status'),'execution_node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_after_status']=404
 except Exception:e['task_get_after_status']='not-404'
(H/'child-runtime-readonly-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':e.get('execution_status'),'task_deleted_404':e.get('task_get_after_status')==404,'summary':{k:(v if k not in ['owner_root','manifest_identity'] else v) for k,v in (e.get('stdout_json') or {}).items() if k in ['status','owner_root','manifest_identity','child_process','supervisor_process','db_metadata','db_integrity','local_identity','local_global_revision','scripts','tasks','schedules','applications','deployment_journal','unit_registry','read_only','credentials_or_contents_emitted']}},ensure_ascii=False))
