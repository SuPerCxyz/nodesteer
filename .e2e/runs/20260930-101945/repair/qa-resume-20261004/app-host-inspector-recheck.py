#!/usr/bin/env python3
"""Create a unique managed App and exact read-only Host Task, persist its definition before the first Task run, then verify FULL-08 and cleanup."""
import json,os,re,subprocess,time,urllib.request,urllib.error,hashlib,uuid
from pathlib import Path
QA=Path(__file__).resolve().parent; PRIVATE=Path(os.environ['NODESTEER_RESUME_PRIVATE_DIR']); CONN=json.loads((PRIVATE/'repair-connection.json').read_text()); BASE=CONN['hub_url'].rstrip('/'); AB=['agent-browser','--session','app-live-resume-3','--allowed-domains','192.168.100.209']
V1ID='7b8929ed-c880-46f1-90e6-a5e9c0dab096'; V1SHA='9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'; NODE='611c02a5-1859-4465-878b-d543b7f6fdec'; NODE_NAME='qa-repair-20260930-101945-native-2'; HUBSHA='164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'; AGENTSHA='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
NONCE='c8b417'; PREFIX='qa-resume-20261005-app-host-'+NONCE+'-'; APPNAME=PREFIX+'managed'; UNIT=PREFIX+'managed.service'; DIR='/tmp/qa-repair-20260930-101945-app-web/'+NONCE
REPORT=QA/'app-host-recheck-20261005-01.json'; DEFINITION=QA/'app-host-inspector-definition-20261005-01.json'
V2SHA=''; V2ID=''; APPID=''; TASKID=''; TASKNAME=PREFIX+'host-inspector'

def api(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode();h={'Authorization':'Bearer '+CONN['token']}
 if data is not None:h['Content-Type']='application/json'
 try:
  with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers=h,method=method),timeout=12) as r:
   raw=r.read()
   try:data=json.loads(raw) if raw else None
   except ValueError:data=raw.decode(errors='replace')
   return r.status,data
 except urllib.error.HTTPError as e:return e.code,None

def browser(args,timeout=25,stdin=None):
 p=subprocess.run(AB+args,input=stdin,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError('agent-browser '+args[0]+': '+(p.stdout+p.stderr)[-200:])
 return p.stdout.strip()
def snapshot():return browser(['snapshot','-i','-c','-d','5'])
def ref(kind,needle):
 for line in snapshot().splitlines():
  if kind in line and needle in line:
   m=re.search(r'ref=(e\d+)',line)
   if m:return '@'+m.group(1)
 raise RuntimeError('no UI ref '+kind+' '+needle)
def eval_js(js):
 raw=browser(['eval','--stdin'],stdin=js,timeout=20);x=json.loads(raw);return json.loads(x) if isinstance(x,str) else x
def write_json(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,'w') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.write('\n')
def update_json(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,path)
def make_v2():
 global V2ID,V2SHA
 source=(QA.parent/'qa/app-fixture/sample-app-v1').read_bytes()+b'\nqa-elf-version-v2\n';V2SHA=hashlib.sha256(source).hexdigest();boundary='----NodeSteerQA'+uuid.uuid4().hex
 fields=[('name',PREFIX+'artifact-v2'),('version','v2'),('architecture','amd64')];parts=[]
 for k,v in fields:parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
 parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="qa-app-v2.elf"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()+source+b'\r\n');body=b''.join(parts)+f'--{boundary}--\r\n'.encode();req=urllib.request.Request(BASE+'/api/artifacts',data=body,headers={'Authorization':'Bearer '+CONN['token'],'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
 with urllib.request.urlopen(req,timeout=30) as r:d=json.load(r);status=r.status
 V2ID=d['id']
 if status!=200 or d.get('sha256')!=V2SHA:raise RuntimeError('v2 API-seeded artifact hash mismatch')
 return {'artifact_id':V2ID,'sha256':V2SHA,'bytes':len(source),'setup':'API fixture seed; explicitly not counted as UI upload'}
def create_app():
 browser(['open',BASE+'/applications/new?artifact_id='+V1ID]);browser(['wait','--load','networkidle'])
 browser(['find','label','名称','fill',APPNAME]);browser(['find','label','二进制路径','fill',DIR+'/binary']);browser(['find','label','配置路径','fill',DIR+'/config.ini']);browser(['find','label','Unit','fill',UNIT]);browser(['find','label','配置内容','fill','qa_version=v1\n']);browser(['find','label','健康检查目标','fill',UNIT]);browser(['check',ref('checkbox',NODE_NAME)])
 browser(['network','requests','--clear']);browser(['click',ref('button','保存')]);browser(['wait','--load','networkidle'])
 url=browser(['get','url']).splitlines()[-1];m=re.search(r'/applications/([0-9a-f-]{36})$',url)
 if not m:raise RuntimeError('UI-created App did not navigate to detail')
 return m.group(1),[x for x in browser(['network','requests']).splitlines() if '/api/applications' in x and 'POST' in x]
def host_command():
 return f'''set +e
unit='{UNIT}'
dir='{DIR}'
bin="$dir/binary"
printf 'hostname=%s\\n' "$(hostname)"
printf 'unit=%s\\n' "$unit"
systemctl show "$unit" --no-pager -p LoadState -p ActiveState -p SubState -p MainPID -p Result -p FragmentPath
pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null)"
printf 'unit_main_pid=%s\\n' "$pid"
if [ -n "$pid" ] && [ "$pid" != 0 ] && [ -e "/proc/$pid/exe" ]; then printf 'unit_main_exe=%s\\n' "$(readlink "/proc/$pid/exe")"; printf 'unit_main_sha256=%s\\n' "$(sha256sum "/proc/$pid/exe" | cut -d' ' -f1)"; else printf 'unit_main_exe=none\\nunit_main_sha256=none\\n'; fi
if [ -e "$bin" ]; then printf 'binary_exists=yes\\n'; printf 'binary_sha256=%s\\n' "$(sha256sum "$bin" | cut -d' ' -f1)"; printf 'binary_size=%s\\n' "$(stat -c %s "$bin")"; else printf 'binary_exists=no\\n'; fi
if [ -e "$dir/config.ini" ]; then printf 'config_exists=yes\\nconfig_content='; cat "$dir/config.ini"; printf '\\n'; else printf 'config_exists=no\\n'; fi
unitfile="/etc/systemd/system/$unit"
if [ -e "$unitfile" ]; then printf 'unit_file_exists=yes\\n'; sed -n '1,40p' "$unitfile"; else printf 'unit_file_exists=no\\n'; fi
'''
def create_host_task(command):
 browser(['open',BASE+'/tasks/new']);browser(['wait','--load','networkidle']);browser(['find','label','名称','fill',TASKNAME]);browser(['find','label','超时（秒）','fill','30']);browser(['find','label','重试次数','fill','0'])
 node_ref=ref('checkbox',NODE_NAME);browser(['check',node_ref]);box=ref('textbox','命令');browser(['fill',box,command]);browser(['network','requests','--clear']);browser(['click',ref('button','保存')]);browser(['wait','--url','**/tasks'])
 _,tasks=api('/api/tasks');t=next((x for x in tasks or [] if x.get('name')==TASKNAME),None)
 if not t:raise RuntimeError('UI-created Host inspector Task missing from API')
 return t,[x for x in browser(['network','requests']).splitlines() if 'POST '+BASE+'/api/tasks' in x]
def task_definition(task,command):
 global TASKID
 TASKID=task['id'];sc,schedules=api('/api/schedules?task_id='+TASKID)
 target=(task.get('target') or {}).get('node_ids',[])
 result={'task_id':TASKID,'name':TASKNAME,'type':task.get('type'),'enabled':task.get('enabled'),'timeout':task.get('timeout'),'retry':task.get('retry'),'offline_policy':task.get('offline_policy'),'target_node_ids':target,'target_exact_node_only':target==[NODE],'schedule_count':len(schedules) if sc==200 and isinstance(schedules,list) else None,'schedule_list_http':sc,'created_via':'agent-browser Task UI','definition_source_http':200,'api_fields_confirmed':sorted(k for k in task.keys() if k in ('id','name','type','enabled','timeout','retry','offline_policy','target','command')),'command':task.get('command'),'command_sha256':hashlib.sha256((task.get('command') or '').encode()).hexdigest(),'expected_harness_command_sha256':hashlib.sha256(command.encode()).hexdigest(),'command_matches_harness':task.get('command')==command,'source_harness':'app-host-inspector-recheck-20261005-01.py','source_harness_file':str(Path(__file__).resolve().relative_to(Path.cwd())),'created_before_first_execution':True,'secrets_included':False}
 if result['schedule_count']!=0 or not result['target_exact_node_only'] or not result['command_matches_harness'] or task.get('enabled') is not True or task.get('timeout')!=30:raise RuntimeError('Host Task definition failed its strict pre-execution checks')
 write_json(DEFINITION,result)
 return result
def run_host(expected_sha,expect_active=True):
 browser(['open',BASE+f'/tasks/{TASKID}/run']);browser(['wait','--load','networkidle']);browser(['network','requests','--clear']);browser(['click',ref('button','立即运行')]);browser(['click',ref('button','立即运行')]);browser(['wait','--url','**/executions'])
 run_req=[x for x in browser(['network','requests']).splitlines() if f'/api/tasks/{TASKID}/run' in x]
 deadline=time.monotonic()+35;ex=None
 while time.monotonic()<deadline:
  status,rows=api(f'/api/executions?task_id={TASKID}&limit=10')
  if status==200 and rows:
   ex=rows[0]
   if ex.get('status') in ('SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'):break
  time.sleep(.15)
 if not ex or ex.get('status')!='SUCCESS':raise RuntimeError('Host Task execution did not reach SUCCESS')
 _,full=api('/api/executions/'+ex['id']);out=(full or {}).get('stdout','')
 browser(['open',BASE+'/executions/'+ex['id']]);browser(['wait','--load','networkidle']);browser(['click',ref('button','打开日志')]);ui_text=eval_js('JSON.stringify(document.body.innerText)')
 lines={}
 for ln in out.splitlines():
  if '=' in ln and not ln.startswith('['):k,v=ln.split('=',1);lines[k]=v
 cfg=out.split('config_content=',1)[1].split('\nunit_file_exists=',1)[0] if 'config_content=' in out and '\nunit_file_exists=' in out else None
 active=(lines.get('ActiveState')=='active' and lines.get('MainPID') not in (None,'0')) if expect_active else (lines.get('ActiveState')=='inactive' and lines.get('MainPID')=='0')
 exe=lines.get('unit_main_sha256')
 sha_ok=(exe==expected_sha and lines.get('binary_sha256')==expected_sha) if expect_active else (exe=='none' and lines.get('unit_main_sha256')=='none')
 return {'execution_id':ex['id'],'execution_status':ex.get('status'),'task_run_request':run_req,'host_output_visible_in_execution_ui':'unit_main_sha256=' in ui_text and 'systemctl show' not in ui_text,'host':{k:lines.get(k) for k in ('hostname','unit','LoadState','ActiveState','SubState','MainPID','FragmentPath','unit_main_pid','unit_main_exe','unit_main_sha256','binary_exists','binary_sha256','binary_size','config_exists','config_content','unit_file_exists')},'config_file_content':cfg,'expected_sha256':expected_sha,'active_state_matches':active,'running_exe_and_disk_hash_match':sha_ok,'physical_vm_identity_independently_verified':False,'os_hostname_observation':'Guest OS hostname is reported as observed; Hub Node hostname is Agent-configured node_name and may differ.'}
def set_version(v):
 boxes=[re.search(r'ref=(e\d+)',x).group(1) for x in snapshot().splitlines() if 'combobox' in x and re.search(r'ref=(e\d+)',x)]
 browser(['click','@'+boxes[0]]); option=(PREFIX+'artifact-v2 v2') if v=='v2' else 'qa-repair-20260930-101945-app-async-deploy-v1 v1';browser(['click',ref('option',option)]);browser(['find','label','版本','fill',v]);browser(['find','label','配置内容','fill','qa_version='+v+'\n']);browser(['click',ref('button','保存')]);browser(['wait','--load','networkidle'])
def run_app(op):
 route=BASE+'/applications/'+APPID
 if browser(['get','url']).splitlines()[-1]!=route:
  browser(['open',route]);browser(['wait','--load','networkidle'])
 if browser(['get','url']).splitlines()[-1]!=route:raise RuntimeError('failed to reach exact App detail route before operation')
 _,old=api(f'/api/applications/{APPID}/executions'); old_ids={x['id'] for x in old or []};boxes=[re.search(r'ref=(e\d+)',x).group(1) for x in snapshot().splitlines() if 'combobox' in x and re.search(r'ref=(e\d+)',x)]
 browser(['click','@'+boxes[-1]]);browser(['click',ref('option',op)]);browser(['network','requests','--clear']);before=browser(['get','url']).splitlines()[-1];t=time.monotonic();browser(['click',ref('button','部署')]);statuses=[];ex=None;deadline=t+35
 while time.monotonic()<deadline:
  _,rows=api(f'/api/applications/{APPID}/executions');matches=[x for x in rows or [] if x.get('id') not in old_ids]
  if matches:
   ex=matches[0]
   if not statuses or statuses[-1]['status']!=ex.get('status'):statuses.append({'status':ex.get('status'),'elapsed_ms':round((time.monotonic()-t)*1000)})
   if ex.get('status') in ('SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'):break
  time.sleep(.012)
 if not ex or ex.get('status')!='SUCCESS':raise RuntimeError(op+' App execution not SUCCESS')
 _,states=api(f'/api/applications/{APPID}/state');state=next((x for x in states or [] if x.get('node_id')==NODE),None);browser(['wait','3500'])
 ui=eval_js("JSON.stringify({path:location.pathname,health:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('健康检查'))?.innerText||'',history:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('执行历史'))?.innerText||''})")
 requests=[x for x in browser(['network','requests']).splitlines() if '/api/applications/'+APPID+'/deploy' in x]
 return {'operation':op,'execution_id':ex['id'],'status':ex['status'],'api_lifecycle':statuses,'state':state,'ui':ui,'deploy_request':requests,'same_route_no_reload':ui['path']=='/applications/'+APPID and before==route,'ui_health_history_ok':op in ui['health'] and ('已停止' in ui['health'] if op=='stop' else '健康' in ui['health']) and 'SUCCESS' in ui['history']}
def delete_app_ui():
 browser(['open',BASE+'/applications']);browser(['wait','--load','networkidle']);browser(['find','label','搜索托管应用、版本或 Unit','fill',APPNAME]);
 row=snapshot();m=re.search(r'cell "操作" \[ref=(e\d+)\]',row)
 if not m:raise RuntimeError('owned App action button missing from filtered row')
 # Resolve the action button in the row containing the exact owned App href.
 browser(['click','tr:has(a[href="/applications/'+APPID+'"]) button']);browser(['click',ref('menuitem','删除')]);browser(['click',ref('button','删除')]);browser(['wait','--load','networkidle']);return [x for x in browser(['network','requests']).splitlines() if 'DELETE '+BASE+'/api/applications/'+APPID in x]
def main():
 for p in (REPORT,DEFINITION):
  if p.exists():raise SystemExit('refuse overwrite '+p.name)
 health,_=api('/healthz');_,nodes=api('/api/nodes');node=next((x for x in nodes or [] if x.get('id')==NODE),None)
 if health!=200 or not node or node.get('status')!='online' or node.get('sync_status')!='synced':raise SystemExit('repair authority or owned native-2 unavailable')
 v1status,v1=api('/api/artifacts/'+V1ID)
 if v1status!=200 or v1.get('sha256')!=V1SHA:raise SystemExit('shared v1 fixture SHA mismatch')
 artifact_v2=make_v2();browser(['open',BASE+'/']);browser(['wait','--load','networkidle']);browser(['eval','--stdin'],stdin="localStorage.setItem('nodesteer_token',"+json.dumps(CONN['token'])+");");browser(['open',BASE+'/'])
 APPID,app_requests=create_app();app_code,app=api('/api/applications/'+APPID)
 if app_code!=200 or app.get('artifact_id')!=V1ID:raise RuntimeError('App UI fixture did not save correctly')
 command=host_command(); task,task_requests=create_host_task(command);definition=task_definition(task,command)
 # Definition is publicly persisted above before the first Host Task execution.
 report={'label':'app-host-recheck-20261005-01','authority':BASE,'hub_sha256':HUBSHA,'agent_sha256':AGENTSHA,'target_node_id':NODE,'target_node_name':NODE_NAME,'app_id':APPID,'app_name':APPNAME,'unit':UNIT,'app_dir':DIR,'v1_artifact_id':V1ID,'v1_artifact_sha256':V1SHA,'v2_artifact':artifact_v2,'app_create_ui_requests':app_requests,'host_task_create_ui_requests':task_requests,'host_task_definition_path':str(DEFINITION.relative_to(Path.cwd())),'host_task_definition_persisted_before_first_run':True,'host_task_definition':definition,'events':[],'cleanup':None,'secrets_included':False}
 write_json(REPORT,report)
 for op,sha in [('deploy',V1SHA),('stop',V1SHA),('start',V1SHA),('restart',V1SHA)]:
  ui=run_app(op);host=run_host(sha,op!='stop');report['events'].append({'type':'operation','ui':ui,'host':host});update_json(REPORT,report);browser(['open',BASE+'/applications/'+APPID]);browser(['wait','--load','networkidle'])
 set_version('v2');upgrade_ui=run_app('upgrade');upgrade_host=run_host(V2SHA,True);report['events'].append({'type':'operation','ui':upgrade_ui,'host':upgrade_host});update_json(REPORT,report)
 browser(['open',BASE+'/applications/'+APPID]);browser(['wait','--load','networkidle']);stop_ui=run_app('stop');stop_host=run_host(V2SHA,False);delete_req=delete_app_ui();app_get,_=api('/api/applications/'+APPID)
 cleanup_host=run_host(V2SHA,False);task_delete,_=api('/api/tasks/'+TASKID,'DELETE');task_get,_=api('/api/tasks/'+TASKID);task_hist,task_rows=api(f'/api/executions?task_id={TASKID}&limit=20');app_hist,app_rows=api(f'/api/applications/{APPID}/executions');artifact_del,_=api('/api/artifacts/'+V2ID,'DELETE');artifact_get,_=api('/api/artifacts/'+V2ID);v1get,_=api('/api/artifacts/'+V1ID)
 report['final_stop']={'ui':stop_ui,'host':stop_host};report['app_delete_ui_request']=delete_req;report['app_get_after_delete_http']=app_get;report['cleanup_host']=cleanup_host;report['unit_unregistered']=cleanup_host['host'].get('LoadState')=='not-found' and cleanup_host['host'].get('unit_file_exists')=='no';report['owned_files_removed']=cleanup_host['host'].get('binary_exists')=='no' and cleanup_host['host'].get('config_exists')=='no';report['cleanup_unit_mainpid_zero']=cleanup_host['host'].get('MainPID')=='0';report['task_cleanup']={'delete_http':task_delete,'get_after_http':task_get,'execution_history_http':task_hist,'execution_history_count':len(task_rows or [])};report['application_history_after_delete']={'http':app_hist,'count':len(app_rows or [])};report['artifact_cleanup']={'v2_delete_http':artifact_del,'v2_get_after_http':artifact_get,'shared_v1_get_http':v1get};report['status']='passed' if app_get==404 and report['unit_unregistered'] and report['owned_files_removed'] and report['cleanup_unit_mainpid_zero'] and task_delete in (200,204,404) and task_get==404 and len(task_rows or [])>0 and len(app_rows or [])>0 and artifact_del in (200,204,404) and artifact_get==404 and v1get==200 and len(report['events'])==5 and all(e['ui']['status']=='SUCCESS' and e['ui']['same_route_no_reload'] and e['ui']['ui_health_history_ok'] and e['host']['execution_status']=='SUCCESS' and e['host']['running_exe_and_disk_hash_match'] and e['host']['active_state_matches'] for e in report['events']) else 'partial'
 # rewrite the report with post-cleanup evidence without losing the pre-run definition artifact.
 update_json(REPORT,report)
 print(json.dumps({'status':report['status'],'events':[{'operation':x['ui']['operation'],'ui':x['ui']['status'],'same_page':x['ui']['same_route_no_reload'],'api_statuses':[y['status'] for y in x['ui']['api_lifecycle']],'host_status':x['host']['execution_status'],'active':x['host']['host'].get('ActiveState'),'sha_ok':x['host']['running_exe_and_disk_hash_match']} for x in report['events']],'cleanup':{'app_get':app_get,'unit_unregistered':report['unit_unregistered'],'owned_files_removed':report['owned_files_removed'],'task_history_count':len(task_rows or []),'app_history_count':len(app_rows or []),'v2_artifact_get':artifact_get}}))
if __name__=='__main__':main()
