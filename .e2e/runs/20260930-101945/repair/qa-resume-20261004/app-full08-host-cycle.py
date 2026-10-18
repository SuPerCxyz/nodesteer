#!/usr/bin/env python3
"""Repeat the owned FULL-08 UI cycle with a product Task inspection after each operation."""
import json,os,re,subprocess,time,urllib.request,urllib.error
from pathlib import Path
QA=Path(__file__).resolve().parent; PRIVATE=Path(os.environ['NODESTEER_RESUME_PRIVATE_DIR']); CONN=json.loads((PRIVATE/'repair-connection.json').read_text())
BASE=CONN['hub_url'].rstrip('/'); APP='5ad4d86f-403a-4a7b-b164-c658044f62df'; TID='20d22690-106c-4f01-978b-c2dc4faa343a'; EVID=QA/'app-full08-host-cycle-retry.json'; AB=['agent-browser','--session','app-live-resume-2','--allowed-domains','192.168.100.209']
NODE='611c02a5-1859-4465-878b-d543b7f6fdec'; NODE_NAME='qa-repair-20260930-101945-native-2'; V1='9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'; V2='9987115cb76d62078cda7dcdb96679b71bfc875d65e42500d0bab98aab9ce637'; UNIT='qa-resume-20261005-managed-71b2.service'; DIR='/tmp/qa-repair-20260930-101945-app-web/app-71b2'
def api(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode();h={'Authorization':'Bearer '+CONN['token']}
 if data is not None:h['Content-Type']='application/json'
 try:
  with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers=h,method=method),timeout=8) as r:
   b=r.read();return r.status,json.loads(b) if b else None
 except urllib.error.HTTPError as e:return e.code,None
def br(args,timeout=25):
 p=subprocess.run(AB+args,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('agent-browser '+args[0]+': '+(p.stdout+p.stderr)[-180:])
 return p.stdout.strip()
def snap():return br(['snapshot','-i','-c','-d','5'])
def ref(kind,needle):
 for line in snap().splitlines():
  if kind in line and needle in line:
   m=re.search(r'ref=(e\d+)',line)
   if m:return '@'+m.group(1)
 raise RuntimeError('UI ref missing '+kind+' '+needle)
def js(expr):
 raw=br(['eval','--stdin'],timeout=20) if False else subprocess.run(AB+['eval','--stdin'],input=expr,text=True,capture_output=True,timeout=20)
 if raw.returncode:raise RuntimeError('agent-browser eval failed')
 x=json.loads(raw.stdout.strip());return json.loads(x) if isinstance(x,str) else x
def save(d):
 tmp=EVID.with_suffix('.tmp');tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,EVID)
def enter_app():br(['open',BASE+'/applications/'+APP]);br(['wait','--load','networkidle'])
def set_version(v):
 artifact_name='qa-resume-20261005-appv2-71b2 v2' if v=='v2' else 'qa-repair-20260930-101945-app-async-deploy-v1 v1'
 boxes=[re.search(r'ref=(e\d+)',x).group(1) for x in snap().splitlines() if 'combobox' in x and re.search(r'ref=(e\d+)',x)]
 br(['click','@'+boxes[0]])
 br(['click',ref('option',artifact_name)])
 br(['find','label','版本','fill',v]);br(['find','label','配置内容','fill','qa_version='+v+'\\n'])
 br(['network','requests','--clear']);br(['click',ref('button','保存')]);br(['wait','--load','networkidle'])
def run_app(op):
 before=br(['get','url']).splitlines()[-1]; assert before==BASE+'/applications/'+APP
 _,oldrows=api(f'/api/applications/{APP}/executions'); old={x['id'] for x in oldrows or []}
 combo=[re.search(r'ref=(e\d+)',x).group(1) for x in snap().splitlines() if 'combobox' in x and re.search(r'ref=(e\d+)',x)]
 br(['click','@'+combo[-1]]);br(['click',ref('option',op)]);br(['network','requests','--clear'])
 t=time.monotonic();br(['click',ref('button','部署')]); statuses=[];ex=None;deadline=t+25
 while time.monotonic()<deadline:
  _,rows=api(f'/api/applications/{APP}/executions'); matches=[x for x in rows or [] if x['id'] not in old]
  if matches:
   ex=matches[0]
   if not statuses or statuses[-1]['status']!=ex['status']:statuses.append({'status':ex['status'],'elapsed_ms':round((time.monotonic()-t)*1000)})
   if ex['status'] in ('SUCCESS','FAILED','CANCELED','TIMED_OUT','BLOCKED'):break
  time.sleep(.01)
 if not ex or ex['status']!='SUCCESS':raise RuntimeError(op+' app execution not SUCCESS')
 code,states=api(f'/api/applications/{APP}/state'); st=next((x for x in states or [] if x.get('node_id')==NODE),None)
 br(['wait','3500']);ui=js("JSON.stringify({path:location.pathname,health:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('健康检查'))?.innerText||'',history:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('执行历史'))?.innerText||''})")
 req=[x for x in br(['network','requests']).splitlines() if '/api/applications/'+APP+'/deploy' in x]
 return {'operation':op,'execution_id':ex['id'],'execution_status':ex['status'],'api_lifecycle':statuses,'state':st,'state_http':code,'ui':ui,'same_page':ui['path']=='/applications/'+APP,'health_history_ok':op in ui['health'] and ('已停止' in ui['health'] if op=='stop' else '健康' in ui['health']) and 'SUCCESS' in ui['history'],'deploy_request':req,'url_before':before,'url_after':br(['get','url']).splitlines()[-1]}
def host_run(expected_sha):
 br(['open',BASE+f'/tasks/{TID}/run']);br(['wait','--load','networkidle']);br(['click',ref('button','立即运行')]);br(['click',ref('button','立即运行')]);br(['wait','--url','**/executions'])
 deadline=time.monotonic()+35;ex=None
 while time.monotonic()<deadline:
  code,rows=api(f'/api/executions?task_id={TID}&limit=10')
  if code==200 and rows:
   ex=rows[0]
   if ex.get('status') in ('SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'):break
  time.sleep(.2)
 if not ex:raise RuntimeError('host bridge produced no Execution')
 _,full=api('/api/executions/'+ex['id']);out=(full or {}).get('stdout','')
 br(['open',BASE+'/executions/'+ex['id']]);br(['wait','--load','networkidle']);br(['click',ref('button','打开日志')]);body=js('JSON.stringify(document.body.innerText)')
 visible='hostname=' in body and 'unit_main_sha256=' in body
 vals={}
 for ln in out.splitlines():
  if '=' in ln and not ln.startswith('['):
   k,v=ln.split('=',1);vals[k]=v
 cfg=out.split('config_content=',1)[1].split('unit_file_exists=',1)[0] if 'config_content=' in out and 'unit_file_exists=' in out else None
 expected=expected_sha
 return {'task_id':TID,'execution_id':ex['id'],'execution_status':ex.get('status'),'output_visible_in_ui':visible,'host':{k:vals.get(k) for k in ('hostname','unit','LoadState','ActiveState','SubState','MainPID','unit_main_pid','unit_main_exe','unit_main_sha256','binary_sha256','binary_size','config_content','unit_file_exists')},'config_file_content':cfg,'expected_artifact_sha':expected,'disk_sha_matches_expected':vals.get('binary_sha256')==expected,'running_exe_sha_matches_expected':vals.get('unit_main_sha256')==expected if vals.get('unit_main_sha256')!='none' else vals.get('ActiveState')=='inactive','host_name_matches_node':vals.get('hostname')==NODE_NAME}

def main():
 if EVID.exists():raise SystemExit('refuse overwrite evidence')
 d={'case':'FULL-08 repeat lifecycle with exact product Host bridge inspection','application_id':APP,'target_node_id':NODE,'target_node_name':NODE_NAME,'unit':UNIT,'app_dir':DIR,'events':[],'host_identity_limit':'native VM hostname differs from Hub Node hostname; all Task commands restricted to exact owned Unit/App paths'};save(d)
 enter_app();set_version('v1')
 for op in ('deploy','stop','start','restart'):
  ui=run_app(op);host=host_run(V1);d['events'].append({'type':'operation','ui':ui,'host':host});save(d);enter_app()
 set_version('v2');ui=run_app('upgrade');host=host_run(V2);d['events'].append({'type':'operation','ui':ui,'host':host});save(d)
 print(json.dumps({'events':[{'op':x['ui']['operation'],'api_statuses':[e['status'] for e in x['ui']['api_lifecycle']],'ui_same_page':x['ui']['same_page'],'ui_feedback':x['ui']['health_history_ok'],'host_status':x['host']['execution_status'],'host_active':x['host']['host'].get('ActiveState'),'running_sha_ok':x['host']['running_exe_sha_matches_expected'],'disk_sha_ok':x['host']['disk_sha_matches_expected']} for x in d['events']]}))
if __name__=='__main__':main()
