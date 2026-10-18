import hashlib,json,shlex,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;R='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NONCE='870358cd92ee';IDS={'good':'fc6fbcc6-39ce-49f4-9368-265a5a42d174','bad':'616a9db6-a87f-4d79-a4de-7d1b016e2986'};BASE={'good':{'revision':2,'sha256':'728dafe9a3392b084f7f3c050a018a3f958aa75049e06ba56081f075dcd9f17d'},'bad':{'revision':1,'sha256':'7d6df17e0117eb02de2b0263062ea68bf68e45dea5c1959816d7bdc719466c54'}}
priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token']
if urlsplit(base).port!=18080:raise SystemExit('blocked: not repair :18080')
op=build_opener(ProxyHandler({}))
def api(m,path,body=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if body is not None:data=json.dumps(body).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+path,data=data,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError('API transport '+type(e).__name__)
 if s not in allowed:raise RuntimeError(f'{m} {path} HTTP {s}')
 return json.loads(x) if x else None
def norm_list(x):
 if x is None:return []
 if isinstance(x,list):return x
 raise RuntimeError('unexpected collection response type')
def summary(s):return {k:s.get(k) for k in ['id','name','revision','sha256','interpreter','enabled','run_user','timeout']}
# Desired v2 content is the same harmless marker writer as in initial setup; execution occurs only after valid snapshot.
updates={}
for role in ['good','bad']:
 marker='sha-prune-'+role+'-v2-'+NONCE;path=R+'/sha-prune/markers/'+role+'-v2.marker';content='#!/bin/sh\nset -eu\nprintf \'%s\\n\' '+shlex.quote(marker)+' > '+shlex.quote(path)+'\n'
 updates[role]={'name':'qa-resume-20261010-sha-prune-'+role+'-'+NONCE,'content':content,'sha256':hashlib.sha256(content.encode()).hexdigest(),'payload':{'name':'qa-resume-20261010-sha-prune-'+role+'-'+NONCE,'description':'owned BE-SCRIPT-SHA-002 v2 Snapshot validation','interpreter':'shell','content':content,'parameters':[],'environment':{},'working_dir':R,'run_user':'root','timeout':30,'enabled':True},'marker_path':path,'marker_value_sha256':hashlib.sha256((marker+'\n').encode()).hexdigest()}
node=api('GET','/api/nodes/'+NODE);app=api('GET','/api/applications/'+APP);states=norm_list(api('GET','/api/applications/'+APP+'/state'));assigned=norm_list(api('GET','/api/applications/'+APP+'/nodes'));settings=api('GET','/api/settings')
if node.get('status')!='offline' or node.get('id')!=NODE:raise SystemExit('blocked: exact child must remain offline')
if app.get('version')!='v1' or app.get('unit_name')!='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service':raise SystemExit('blocked: exact owned App is not healthy v1 definition')
if assigned not in ([NODE],[]):raise SystemExit('blocked: owned App assignment contains another node')
if assigned and not any(s.get('node_id')==NODE and s.get('version')=='v1' and s.get('health')=='healthy' for s in states):raise SystemExit('blocked: assigned v1 App state not healthy')
if settings.get('changelog_window')!='5000':raise SystemExit('blocked: changelog_window changed')
old={role:api('GET','/api/scripts/'+sid) for role,sid in IDS.items()};e={'case_id':'BE-SCRIPT-SHA-002','stage':'STAGE_V2_FULL_SNAPSHOT_WHILE_CHILD_OFFLINE','captured_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint_port':18080,'child_node_id':NODE,'application_id':APP,'child_status_before':node.get('status'),'app_before':{k:app.get(k) for k in ['id','version','revision','unit_name','artifact_id']},'assignment_before':assigned,'app_state_before':[{k:s.get(k) for k in ['node_id','version','health']} for s in states if s.get('node_id')==NODE],'changelog_window':settings.get('changelog_window'),'scripts_before':{r:summary(s) for r,s in old.items()},'initial_attempt_note':'attempt01 changed both Scripts and removed this child App assignment but helper misinterpreted API null empty assignment as []; offline setup resumed idempotently, no Task had been created.','status':'STARTED'}
(H/'v2-snapshot-prepare-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
for role in ['good','bad']:
 s=old[role];baseinfo=BASE[role];update=updates[role]
 if s.get('id')!=IDS[role] or s.get('name')!=update['name']:raise SystemExit('blocked: Script identity mismatch '+role)
 if s.get('revision')==baseinfo['revision'] and s.get('sha256')==baseinfo['sha256']:
  api('PUT','/api/scripts/'+IDS[role],update['payload'],(200,201));action='updated_from_v1'
 elif s.get('revision')==baseinfo['revision']+1 and s.get('sha256')==update['sha256']:
  action='already_v2_from_attempt01'
 else:raise SystemExit('blocked: unexpected current Script revision/SHA '+role)
 rd=api('GET','/api/scripts/'+IDS[role])
 if rd.get('revision')!=baseinfo['revision']+1 or rd.get('sha256')!=update['sha256']:raise SystemExit('Script v2 readback mismatch '+role)
 e.setdefault('scripts_after',{})[role]={'id':IDS[role],'name':rd.get('name'),'action':action,'revision':rd.get('revision'),'sha256':rd.get('sha256'),'content_bytes':len(update['content'].encode()),'marker_path':update['marker_path'],'marker_value_sha256':update['marker_value_sha256']};(H/'v2-snapshot-prepare-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
if assigned:
 api('POST','/api/applications/'+APP+'/assign',{'node_ids':[NODE],'remove':True},(200,));e['app_assignment_remove_called']=True
else:e['app_assignment_remove_called']=False
after=api('GET','/api/applications/'+APP+'/nodes');e['app_assignment_after_raw']=after;e['app_assignment_after_normalized']=norm_list(after)
if e['app_assignment_after_normalized']!=[]:raise SystemExit('App remains assigned after removal')
existing=norm_list(api('GET','/api/tasks'));tasks=[];created_ids=[]
for role in ['good','bad']:
 name='qa-resume-20261010-sha-prune-run-'+role+'-v2-'+NONCE;matches=[x for x in existing if x.get('name')==name]
 if matches:
  rd=matches[0]
  if rd.get('type')!='script' or rd.get('script_id')!=IDS[role] or rd.get('target',{}).get('node_ids')!=[NODE] or rd.get('retry')!=0 or rd.get('timeout')!=60:raise SystemExit('existing retained Task does not match exact target '+role)
  action='reused_exact'
 else:
  body={'name':name,'type':'script','script_id':IDS[role],'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True}
  created=api('POST','/api/tasks',body,(200,201));created_ids.append(created['id']);rd=api('GET','/api/tasks/'+created['id']);action='created'
 if rd.get('type')!='script' or rd.get('script_id')!=IDS[role] or rd.get('target',{}).get('node_ids')!=[NODE] or rd.get('retry')!=0 or rd.get('timeout')!=60:raise RuntimeError('retained Script Task readback mismatch '+role)
 tasks.append({'role':role,'action':action,'id':rd.get('id'),'name':rd.get('name'),'type':rd.get('type'),'script_id':rd.get('script_id'),'target':rd.get('target'),'retry':rd.get('retry'),'timeout':rd.get('timeout'),'enabled':rd.get('enabled')})
 e['execution_tasks']=tasks;(H/'v2-snapshot-prepare-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
schedules=norm_list(api('GET','/api/schedules'));scheduled=[s for s in schedules if s.get('task_id') in {t['id'] for t in tasks}]
if scheduled:raise SystemExit('unexpected Schedule references retained QA Script Tasks')
app_after=api('GET','/api/applications/'+APP);assigned_raw=api('GET','/api/applications/'+APP+'/nodes');final_scripts={role:api('GET','/api/scripts/'+sid) for role,sid in IDS.items()};child_after=api('GET','/api/nodes/'+NODE)
if child_after.get('status')!='offline' or norm_list(assigned_raw)!=[] or app_after.get('version')!='v1':raise SystemExit('final offline/app omission readback mismatch')
for role,s in final_scripts.items():
 if s.get('revision')!=BASE[role]['revision']+1 or s.get('sha256')!=updates[role]['sha256']:raise SystemExit('final Script readback mismatch '+role)
e.update({'app_assignment_after_raw':assigned_raw,'app_assignment_after_normalized':[],'app_after':{k:app_after.get(k) for k in ['id','version','revision','unit_name','artifact_id']},'child_after':{k:child_after.get(k) for k in ['id','status','sync_status','global_revision']},'schedules_for_retained_tasks':[],'retained_task_ids':[t['id'] for t in tasks],'status':'V2_DESIRED_AND_BOTH_SCRIPT_TASKS_READBACK_WHILE_CHILD_OFFLINE','fault_armed':False});(H/'v2-snapshot-prepare-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':e['status'],'app_assignment':e['app_assignment_after_raw'],'scripts_after':{r:{'id':s['id'],'revision':s['revision'],'sha256':s['sha256']} for r,s in final_scripts.items()},'retained_tasks':tasks,'schedules':[],'child_status':child_after.get('status'),'changelog_window':settings.get('changelog_window'),'fault_armed':False},ensure_ascii=False))
