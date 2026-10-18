import hashlib,json,shlex,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));PARENT='03cb50cc-eab6-4afd-bbe0-f73c4e326773';CHILD='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';ROOT='/tmp/qa-resume-20261008-child-ciivjupc';SCRIPT='6ecb3dee-43c3-432a-95cc-8fa3b30a91e1';TASK='a30658b0-7090-4a1b-9701-3ab0b89dca3c';SCRIPTSHA='74a0113e91fa3c7091a309c4af857fd39f581b2c054e80058f0ee7fd022d313d';BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};d=None
 if b is not None:d=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=d,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
src=r'''import hashlib,json,os,pathlib,sqlite3,stat
from pathlib import Path
D=Path('/tmp/qa-resume-20261008-child-ciivjupc');NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';AGENT='agent-ab15a133-73cd-4025-8c97-f2ff37e000f5';SCRIPT='6ecb3dee-43c3-432a-95cc-8fa3b30a91e1';TASK='a30658b0-7090-4a1b-9701-3ab0b89dca3c';SH='74a0113e91fa3c7091a309c4af857fd39f581b2c054e80058f0ee7fd022d313d';BIN=D/'nodesteer-agent';BIN_SHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';SUP='/usr/bin/python3.13';SUP_SHA='078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531'
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def getp(pid):
 q=Path('/proc/%d'%pid);raw=(q/'stat').read_text();st=raw[raw.rfind(')')+2:].split();ss=(q/'status').read_text().splitlines();uid=int(next(x for x in ss if x.startswith('Uid:')).split()[1]);argv=[x.decode(errors='replace') for x in (q/'cmdline').read_bytes().split(b'\0') if x];return {'pid':pid,'startticks':int(st[19]),'uid':uid,'exe':os.readlink(q/'exe'),'exe_sha256':sha(q/'exe'),'argv':argv,'cgroup':(q/'cgroup').read_text().strip()}
st=D.lstat();M=D/'owner-manifest.json';m=json.loads(M.read_text())
if D.is_symlink() or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(33,135,65534,0o700):raise SystemExit('owner root guard failed')
if m.get('node_id')!=NODE or m.get('agent_id')!=AGENT or m.get('directory')!=str(D):raise SystemExit('owner manifest IDs/root mismatch')
child=[];sup=[]
for x in Path('/proc').iterdir():
 if not x.name.isdigit():continue
 try:p=getp(int(x.name))
 except (FileNotFoundError,ProcessLookupError,PermissionError):continue
 if p['uid']!=65534:continue
 if p['exe']==str(BIN) and p['exe_sha256']==BIN_SHA and str(D/'child.yaml') in p['argv']:child.append(p)
 if p['exe']==SUP and p['exe_sha256']==SUP_SHA and any(a==str(D/'supervisor.py') for a in p['argv']):sup.append(p)
if len(child)!=1 or len(sup)!=1:raise SystemExit('owner child/supervisor cardinality mismatch')
dbpath=D/'data/state.db';ds=dbpath.lstat()
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,155,65534) or dbpath.is_symlink():raise SystemExit('DB inode/owner guard mismatch')
db=sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True,timeout=3);db.row_factory=sqlite3.Row
rev=db.execute('select global_revision from sync_state where id=1').fetchone()[0];s=[dict(x) for x in db.execute('select id,revision,sha256 from scripts where id=?',(SCRIPT,))];trows=[json.loads(x['definition_json'])|{'id':x['id'],'revision':x['revision'],'enabled':bool(x['enabled'])} for x in db.execute('select id,definition_json,revision,enabled from tasks where id=?',(TASK,))];schedules=[x[0] for x in db.execute('select id from schedules')];db.close()
if s!=[{'id':SCRIPT,'revision':1,'sha256':SH}]:raise SystemExit('child Script v1 baseline mismatch')
if len(trows)!=1 or trows[0].get('type')!='script' or trows[0].get('script_id')!=SCRIPT or trows[0].get('target',{}).get('node_ids')!=[NODE] or trows[0].get('retry')!=0 or trows[0].get('timeout')!=60:raise SystemExit('child Task v1 baseline mismatch')
if schedules:raise SystemExit('unexpected child Schedule objects')
print(json.dumps({'status':'SCRIPT_V1_TASK_BASELINE_PERSISTED','owner_root':{'dev':st.st_dev,'inode':st.st_ino,'uid':st.st_uid,'mode':oct(stat.S_IMODE(st.st_mode)),'manifest_sha256':sha(M)},'child_process':child[0],'supervisor_process':sup[0],'db':{'dev':ds.st_dev,'inode':ds.st_ino,'uid':ds.st_uid,'mode':oct(stat.S_IMODE(ds.st_mode)),'local_revision':rev},'scripts':s,'tasks':[{'id':x['id'],'revision':x['revision'],'type':x['type'],'script_id':x['script_id'],'target_node_ids':x['target']['node_ids'],'retry':x['retry'],'timeout':x['timeout']} for x in trows],'schedules':schedules,'read_only':True,'credential_or_contents_emitted':False},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-native4-db-fault-v1-baseline-readonly-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[PARENT]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];e={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'target_parent_node_id':PARENT,'timeout':60,'retry':0,'schedule_present':False,'purpose':'read-only exact child v1 Script/Task database baseline before write-lock fault'}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==e['command_sha256'];x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+55;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';e.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_after_status']=404
 except Exception:e['task_get_after_status']='not-404'
(H/'v1-child-baseline-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':e.get('status'),'task_deleted_404':e.get('task_get_after_status')==404,'result':e.get('stdout_json')},ensure_ascii=False))
