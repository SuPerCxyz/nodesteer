import hashlib,json,shlex,time,datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import *
from urllib.error import HTTPError,URLError
H=Path(__file__).resolve().parent;ROOT='/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee';R='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';NONCE='870358cd92ee';IDS={'good':'fc6fbcc6-39ce-49f4-9368-265a5a42d174','bad':'616a9db6-a87f-4d79-a4de-7d1b016e2986'}
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('blocked: not repair :18080')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError('API '+type(e).__name__)
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
def summ(s):return {k:s.get(k) for k in ['id','name','revision','sha256','interpreter','enabled','run_user','timeout']}
node=api('GET','/api/nodes/'+NODE);app=api('GET','/api/applications/'+APP);states=api('GET','/api/applications/'+APP+'/state');assigned=api('GET','/api/applications/'+APP+'/nodes');settings=api('GET','/api/settings')
if node.get('status')!='offline' or app.get('version')!='v1' or node.get('id')!=NODE:raise SystemExit('blocked: exact child must be stopped and v1 app definition must remain')
if assigned!=[NODE]:raise SystemExit('blocked: owned App assignment set is not exactly this child')
if settings.get('changelog_window')!='5000':raise SystemExit('blocked: global changelog window changed')
if not any(s.get('node_id')==NODE and s.get('version')=='v1' and s.get('health')=='healthy' for s in states):raise SystemExit('blocked: v1 App state not healthy')
old={role:api('GET','/api/scripts/'+sid) for role,sid in IDS.items()}
if old['good'].get('revision')!=2 or old['bad'].get('revision')!=1:raise SystemExit('blocked: unexpected v1 Script revisions')
script_updates={}
for role in ['good','bad']:
 name=old[role]['name'];marker='sha-prune-'+role+'-v2-'+NONCE;path=R+'/sha-prune/markers/'+role+'-v2.marker';content='#!/bin/sh\nset -eu\nprintf \'%s\\n\' '+shlex.quote(marker)+' > '+shlex.quote(path)+'\n'
 payload={'name':name,'description':'owned BE-SCRIPT-SHA-002 v2 Snapshot validation','interpreter':'shell','content':content,'parameters':[],'environment':{},'working_dir':R,'run_user':'root','timeout':30,'enabled':True}
 script_updates[role]={'payload':payload,'content':content,'content_sha256':hashlib.sha256(content.encode()).hexdigest(),'marker_path':path,'marker_value_sha256':hashlib.sha256((marker+'\n').encode()).hexdigest()}
evidence={'case_id':'BE-SCRIPT-SHA-002','stage':'PREPARE_V2_FULL_SNAPSHOT_DESIRED_WHILE_CHILD_STOPPED','captured_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'endpoint_port':18080,'child_node_id':NODE,'application_id':APP,'child_status_before':node.get('status'),'app_before':{k:app.get(k) for k in ['id','version','revision','unit_name','artifact_id']},'assignment_before':assigned,'changelog_window':settings.get('changelog_window'),'scripts_before':{r:summ(s) for r,s in old.items()},'status':'STARTED'}
def write(): (H/'v2-snapshot-prepare-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
write(); changed=[];task_id=None;created_task_ids=[]
try:
 for role in ['good','bad']:
  sid=IDS[role];u=api('PUT','/api/scripts/'+sid,script_updates[role]['payload'],(200,201));changed.append(role);rd=api('GET','/api/scripts/'+sid)
  if rd.get('sha256')!=script_updates[role]['content_sha256'] or rd.get('revision')!=old[role]['revision']+1:raise RuntimeError('Script update readback mismatch for '+role)
  evidence.setdefault('scripts_after',{})[role]={'id':sid,'name':rd.get('name'),'revision':rd.get('revision'),'sha256':rd.get('sha256'),'content_bytes':len(script_updates[role]['content'].encode()),'marker_path':script_updates[role]['marker_path'],'marker_value_sha256':script_updates[role]['marker_value_sha256']};write()
 assigned_result=api('POST','/api/applications/'+APP+'/assign',{'node_ids':[NODE],'remove':True},(200,));assigned_after=api('GET','/api/applications/'+APP+'/nodes')
 if assigned_after!=[]:raise RuntimeError('App still assigned to child after remove')
 evidence['app_assignment_after']=assigned_after;evidence['app_assignment_remove_result']=assigned_result;write()
 tasks_after=[];created_task_ids=[]
 for role in ['good','bad']:
  task_name='qa-resume-20261010-sha-prune-run-'+role+'-v2-'+NONCE
  task={'name':task_name,'type':'script','script_id':IDS[role],'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True}
  created=api('POST','/api/tasks',task,(200,201));task_id=created['id'];created_task_ids.append(task_id);rd=api('GET','/api/tasks/'+task_id)
  if rd.get('target',{}).get('node_ids')!=[NODE] or rd.get('retry')!=0 or rd.get('timeout')!=60 or rd.get('script_id')!=IDS[role] or rd.get('type')!='script':raise RuntimeError('v2 Script Task readback mismatch for '+role)
  tasks_after.append({k:rd.get(k) for k in ['id','name','type','script_id','target','retry','timeout','enabled']})
 _,schedules=api('GET','/api/schedules')
 if any(x.get('task_id') in created_task_ids for x in schedules):raise RuntimeError('unexpected Schedule attached to retained QA Script Tasks')
 evidence['schedules_for_retained_tasks']=[]
 evidence['execution_tasks']=tasks_after;evidence['status']='DESIRED_V2_STAGED_CHILD_OFFLINE_UNARMED';evidence['fault_armed']=False;write()
except Exception as e:
 evidence['error_type']=type(e).__name__;evidence['error_message']=str(e);evidence['status']='SETUP_FAILED_ROLLBACK_ATTEMPTED'
 try:
  for created_id in created_task_ids:
   api('DELETE','/api/tasks/'+created_id,None,(200,204));api('GET','/api/tasks/'+created_id,None,(404,))
  cur=api('GET','/api/applications/'+APP+'/nodes')
  if NODE not in cur:api('POST','/api/applications/'+APP+'/assign',{'node_ids':[NODE],'remove':False},(200,))
  for role in changed:
   s=old[role];payload={k:s.get(k) for k in ['name','description','interpreter','content','parameters','environment','working_dir','run_user','timeout','enabled']};api('PUT','/api/scripts/'+IDS[role],payload,(200,201))
  evidence['rollback']='attempted'
 except Exception as re:evidence['rollback_error_type']=type(re).__name__
 write();raise
print(json.dumps({'status':evidence['status'],'script_revisions':{k:v.get('revision') for k,v in evidence.get('scripts_after',{}).items()},'script_sha256':{k:v.get('sha256') for k,v in evidence.get('scripts_after',{}).items()},'app_assignment_after':evidence.get('app_assignment_after'),'tasks':evidence.get('execution_tasks'),'fault_armed':False},ensure_ascii=False))
