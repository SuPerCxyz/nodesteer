import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';GOOD='fc6fbcc6-39ce-49f4-9368-265a5a42d174';BAD='616a9db6-a87f-4d79-a4de-7d1b016e2986';HUBNODE='91f44866-8a28-435e-bbda-2f2367126c00'
src=r'''import hashlib,json,os,pathlib,sqlite3,subprocess
from pathlib import Path
R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20260909-p0-002-app-870358cd92ee.service';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';B='/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';V1={'binary':'35d10e1ef6728c5b46555efac91c605c0f1eb7edfe217f3724ae25a0dffb9d1c','config':'5d24742791cf5f34c6b128ce01b7b4f5ee2516508e47731653abbf92c6190b34','unit':'6f0bb711bdeb0d88ad738824209c652928d28ada6cb104f46fe87114edcd7ffa'}
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=8);return dict(x.split('=',1) for x in q.stdout.strip().splitlines() if '=' in x)
def show(u):return run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup','-p','RuntimeMaxUSec'])
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();argv=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in argv.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
own=json.loads((R/'owner.json').read_text());pown=json.loads((P/'owner.json').read_text());controls=json.loads((P/'controls.json').read_text());expected={'owner_nonce':'870358cd92ee','node_id':NODE,'agent_id':AGENT,'unit':CHILD,'binary_sha256':AS,'config_path':str(R/'agent.yaml')}
if any(own.get(k)!=v for k,v in expected.items()):raise SystemExit('child owner manifest mismatch')
if pown.get('owner_nonce')!='870358cd92ee' or pown.get('node_id')!=NODE or pown.get('agent_id')!=AGENT or pown.get('binary_sha256')!=PS or controls.get('fault_armed') is not True or controls.get('release_good_snapshot') is not False:raise SystemExit('proxy or held controls guard mismatch')
cu=show(CHILD);cp=proc(int(cu.get('MainPID','0')));pu=show(PROXY);pp=proc(int(pu.get('MainPID','0')));au=show(APPUNIT);ap=proc(int(au.get('MainPID','0')))
if cp['pid']!=50291 or cp['startticks']!=136559152 or cp['uid']!=0 or cp['exe_sha256']!=AS or cp['argv']!=[B,'--config',str(R/'agent.yaml')] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child exact process guard mismatch')
if pu.get('ActiveState')!='active' or pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('proxy exact process guard mismatch')
files={'binary':sha(R/'app/bin/qa-app') if (R/'app/bin/qa-app').is_file() else None,'config':sha(R/'app/config/qa-app.env') if (R/'app/config/qa-app.env').is_file() else None,'unit':sha(pathlib.Path('/etc/systemd/system')/APPUNIT) if pathlib.Path('/etc/systemd/system',APPUNIT).is_file() else None}
if au.get('ActiveState')!='active' or ap['cgroup']!='0::/system.slice/'+APPUNIT or files!=V1:raise SystemExit('App v1 physical unit/hash oracle mismatch')
db=sqlite3.connect('file:'+str(R/'state/state.db')+'?mode=ro',uri=True,timeout=5);rev=db.execute('select global_revision from sync_state where id=1').fetchone()[0];apps=[x[0] for x in db.execute('select id from applications order by id')];scripts=[{'id':x[0],'revision':x[1],'sha256':x[2]} for x in db.execute('select id,revision,sha256 from scripts order by id')];journals=[dict(zip(['id','to_version','phase','backup_path','backup_config_path','backup_unit_path'],x)) for x in db.execute('select id,to_version,phase,backup_path,backup_config_path,backup_unit_path from deployments where application_id=? order by started_at',(APP,))];db.close()
events=[json.loads(x) for x in (P/'events.jsonl').read_text().splitlines() if x.strip()];selected=[x for x in events if x.get('event') in {'bad_sha_frame_forwarded','sync_ack','valid_snapshot_held_for_negative_oracle'}]
bad=next((x for x in selected if x.get('event')=='bad_sha_frame_forwarded'),{});ack=next((x for x in selected if x.get('event')=='sync_ack' and x.get('accepted_as_bad_sha_rejection')),{});held=next((x for x in selected if x.get('event')=='valid_snapshot_held_for_negative_oracle'),{})
if not bad or not ack or not held or ack.get('applied_rev')!=0 or ack.get('ok') is not False:raise SystemExit('proxy negative/frozen-frame evidence missing')
parents={}
for u,want,tick in [('nodesteer-agent.service',24684,59589521),('nodesteer-agent-repair-20260930-101945-native-1.service',33342,84203924)]:
 q=show(u);p=proc(int(q.get('MainPID','0')));parents[u]={'active':q.get('ActiveState')=='active','pid':p['pid'],'startticks':p['startticks']}
 if q.get('ActiveState')!='active' or p['pid']!=want or p['startticks']!=tick:raise SystemExit('original Native1 service changed')
print(json.dumps({'status':'NEGATIVE_ORACLE_OLD_APP_STATE_AND_FILES_PRESERVED_HELD_BEFORE_RELEASE','child_unit':cu,'child_process':cp,'proxy_unit':pu,'proxy_process':pp,'app_unit':au,'app_process':ap,'app_v1_file_sha256':files,'app_v1_file_hashes_match':files==V1,'child_local_global_revision':rev,'child_local_apps':apps,'owned_app_row_present':APP in apps,'child_local_script_rows':scripts,'note_scripts_baseline_was_empty':len(scripts)==0,'deployment_journals_for_app':journals,'negative_ack_event':ack,'bad_sha_forwarded_event':bad,'valid_snapshot_held_event':held,'original_native1_services':parents,'release_good_snapshot':False,'fault_armed':True},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-sha-prune-negative-oracle-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[HUBNODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'scope':'read-only exact child SQLite, process, App file/Unit and proxy event oracle'}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==ev['command_sha256'];x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+65;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())});node=api('GET','/api/nodes/'+NODE);app=api('GET','/api/applications/'+APP);states=api('GET','/api/applications/'+APP+'/state');assigned=api('GET','/api/applications/'+APP+'/nodes');ev['hub_readback']={'node':{k:node.get(k) for k in ['id','status','sync_status','global_revision']},'application':{k:app.get(k) for k in ['id','version','revision']},'application_state':[ {k:z.get(k) for k in ['node_id','version','operation','health']} for z in states if z.get('node_id')==NODE ],'assigned_node_ids':assigned}
finally:
 ev['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['task_get_after_status']=404
 except Exception:ev['task_get_after_status']='not-404'
(H/'negative-oracle-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json')},ensure_ascii=False))
