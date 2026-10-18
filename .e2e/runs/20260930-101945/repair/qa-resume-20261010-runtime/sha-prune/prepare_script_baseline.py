import hashlib,json,shlex,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
HERE=Path(__file__).resolve().parent; R='/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee'; NODE='c77b8a84-8085-4ea8-8532-de171761854b'; APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9'; NONCE='870358cd92ee'
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text()); base=c['hub_url'].rstrip('/'); token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('blocked: endpoint is not repair :18080')
op=build_opener(ProxyHandler({}))
def api(m,path,body=None,allow=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if body is not None:data=json.dumps(body).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+path,data=data,headers=h,method=m),timeout=20) as r:status,payload=r.status,r.read()
 except HTTPError as e:status,payload=e.code,e.read()
 except (URLError,TimeoutError) as e:raise RuntimeError('API '+type(e).__name__)
 if status not in allow:raise RuntimeError(f'{m} {path} HTTP {status}')
 return status,json.loads(payload) if payload else None
def wait(eid):
 end=time.monotonic()+65;last=None
 while time.monotonic()<end:
  _,last=api('GET','/api/executions/'+eid)
  if last.get('status') in {'SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT','SKIPPED'}:return last
  time.sleep(.5)
 return last
def one_task(name,body):
 _,t=api('POST','/api/tasks',body,(200,201));tid=t['id'];_,rd=api('GET','/api/tasks/'+tid); result={'task_id':tid,'name':name,'definition_sha256':hashlib.sha256(json.dumps(rd,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'retry':rd.get('retry'),'timeout':rd.get('timeout'),'target':rd.get('target')}
 try:
  _,xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=xs[0]['id'];fin=wait(eid);stdout=fin.get('stdout') or '';result['execution']={k:fin.get(k) for k in ['id','task_id','node_id','status','exit_code','trigger_type','started_at','ended_at']};result['execution']['stdout']=stdout;result['execution']['stdout_sha256']=hashlib.sha256(stdout.encode()).hexdigest();result['execution']['stderr_bytes']=len((fin.get('stderr') or '').encode())
 finally:
  ds,_=api('DELETE','/api/tasks/'+tid,None,(200,204));gs,_=api('GET','/api/tasks/'+tid,None,(404,));result['cleanup']={'delete':ds,'get_after':gs}
 return result
_,node=api('GET','/api/nodes/'+NODE);_,app=api('GET','/api/applications/'+APP);_,states=api('GET','/api/applications/'+APP+'/state');_,assigned=api('GET','/api/applications/'+APP+'/nodes')
if node.get('status')!='online' or node.get('sync_status')!='synced' or app.get('version')!='v1' or NODE not in assigned or not any(s.get('node_id')==NODE and s.get('version')=='v1' and s.get('health')=='healthy' for s in states):raise SystemExit('blocked: child or real v1 App baseline not healthy/assigned')
mk=R+'/sha-prune/markers'; results={'captured_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'case_id':'BE-SCRIPT-SHA-002','endpoint_port':18080,'child_node_id':NODE,'application_id':APP,'application_version':'v1','app_health':'healthy','marker_directory':mk,'scripts':[],'task_executions':[],'marker_oracles':[],'known_initial_marker_format_issue':'good v1 script initially wrote trailing n due shell printf format escaping; corrected Script revision follows, first failed oracle preserved in marker-read-evidence.json'}
(HERE/'script-baseline-evidence.json').write_text(json.dumps(results,indent=2)+'\n')
all_scripts=api('GET','/api/scripts')[1]
for role in ['good','bad']:
 name='qa-resume-20261010-sha-prune-'+role+'-'+NONCE; marker='sha-prune-'+role+'-baseline-'+NONCE; path=mk+'/'+role+'-v1.marker'
 content='#!/bin/sh\nset -eu\nprintf \'%s\\n\' '+shlex.quote(marker)+' > '+shlex.quote(path)+'\n'
 payload={'name':name,'description':'owned BE-SCRIPT-SHA-002 real execution baseline','interpreter':'shell','content':content,'parameters':[],'environment':{},'working_dir':R,'run_user':'root','timeout':30,'enabled':True}
 prev=next((x for x in all_scripts if x.get('name')==name),None)
 if prev: _,sc=api('PUT','/api/scripts/'+prev['id'],payload,(200,201)); sid=prev['id']; action='updated'
 else: _,sc=api('POST','/api/scripts',payload,(200,201));sid=sc['id'];action='created'
 _,rd=api('GET','/api/scripts/'+sid)
 if rd.get('sha256')!=hashlib.sha256(content.encode()).hexdigest():raise RuntimeError('Script SHA readback mismatch')
 results['scripts'].append({'id':sid,'name':name,'action':action,'revision':rd.get('revision'),'sha256':rd.get('sha256'),'content_bytes':len(content.encode()),'marker_path':path,'marker_sha256':hashlib.sha256((marker+'\n').encode()).hexdigest()})
 body={'name':'qa-resume-20261010-sha-prune-run-'+role+'-'+NONCE,'type':'script','script_id':sid,'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True}
 task=one_task(body['name'],body);results['task_executions'].append(task);(HERE/'script-baseline-evidence.json').write_text(json.dumps(results,indent=2)+'\n')
 verify='test "$(cat '+shlex.quote(path)+')" = '+shlex.quote(marker)+' && sha256sum '+shlex.quote(path)
 v=one_task('qa-resume-20261010-sha-prune-verify-'+role+'-'+NONCE,{'name':'qa-resume-20261010-sha-prune-verify-'+role+'-'+NONCE,'type':'command','command':verify,'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True});results['marker_oracles'].append({'role':role,'task':v});(HERE/'script-baseline-evidence.json').write_text(json.dumps(results,indent=2)+'\n')
 if task['execution'].get('status')!='SUCCESS' or v['execution'].get('status')!='SUCCESS':raise RuntimeError('Script execution or physical marker oracle failed')
results['status']='BASELINE_SUCCESS';(HERE/'script-baseline-evidence.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps({'evidence':'script-baseline-evidence.json','status':'BASELINE_SUCCESS','script_ids':[s['id'] for s in results['scripts']],'revisions':[s['revision'] for s in results['scripts']],'execution_statuses':[x['execution']['status'] for x in results['task_executions']],'marker_statuses':[x['task']['execution']['status'] for x in results['marker_oracles']]},ensure_ascii=False))
