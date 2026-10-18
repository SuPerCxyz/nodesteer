#!/usr/bin/env python3
"""Run owned app operations through agent-browser and capture same-page feedback/API lifecycle."""
import json, os, re, subprocess, time, urllib.request, urllib.error, hashlib
from pathlib import Path
QA=Path(__file__).resolve().parent
PRIVATE=Path(os.environ['NODESTEER_RESUME_PRIVATE_DIR'])
CONN=json.loads((PRIVATE/'repair-connection.json').read_text())
BASE=CONN['hub_url'].rstrip('/'); APP='5ad4d86f-403a-4a7b-b164-c658044f62df'
AB=['agent-browser','--session','app-live-resume-2','--allowed-domains','192.168.100.209']
REPORT=QA/'app-live-state-full08.json'
V2ID='0ff9672c-99d0-486a-a9fb-5598f0620aec'
V2SHA='9987115cb76d62078cda7dcdb96679b71bfc875d65e42500d0bab98aab9ce637'
V1SHA='9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'
UNIT='qa-resume-20261005-managed-71b2.service'; DIR='/tmp/qa-repair-20260930-101945-app-web/app-71b2'
NODE='611c02a5-1859-4465-878b-d543b7f6fdec'; NODE_NAME='qa-repair-20260930-101945-native-2'

def api(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode(); h={'Authorization':'Bearer '+CONN['token']}
 if data is not None:h['Content-Type']='application/json'
 try:
  with urllib.request.urlopen(urllib.request.Request(BASE+path,data=data,headers=h,method=method),timeout=8) as r:
   raw=r.read(); return r.status,json.loads(raw) if raw else None
 except urllib.error.HTTPError as e: return e.code,None

def browser(args,timeout=25,stdin=None):
 p=subprocess.run(AB+args,input=stdin,text=True,capture_output=True,timeout=timeout)
 if p.returncode: raise RuntimeError('agent-browser '+args[0]+' failed: '+(p.stdout+p.stderr)[-200:])
 return p.stdout.strip()

def snap():return browser(['snapshot','-i','-c','-d','5'])
def ref_for(pattern, text=None):
 for line in snap().splitlines():
  if pattern in line and (text is None or text in line):
   m=re.search(r'ref=(e\d+)',line)
   if m:return '@'+m.group(1)
 raise RuntimeError('no UI ref for '+str(pattern)+' '+str(text))
def eval_js(js):
 raw=browser(['eval','--stdin'],stdin=js)
 x=json.loads(raw)
 return json.loads(x) if isinstance(x,str) else x

def set_upgrade():
 # Select the API-seeded v2 Artifact in the real Application editor, save through UI, then operate.
 browser(['click',ref_for('combobox','qa-repair-20260930-101945-app-async-deploy-v1')])
 option=ref_for('option','qa-resume-20261005-appv2-71b2 v2'); browser(['click',option])
 browser(['find','label','版本','fill','v2']); browser(['find','label','配置内容','fill','qa_version=v2\\n'])
 before=browser(['get','url']).splitlines()[-1]
 browser(['click',ref_for('button','保存')]); browser(['wait','--load','networkidle'])
 return {'save_navigation_from':before,'save_navigation_to':browser(['get','url']).splitlines()[-1],'save_requests':[x for x in browser(['network','requests']).splitlines() if 'PUT '+BASE+'/api/applications/'+APP in x]}

def choose(op):
 # The last combobox is the operation selector; refresh refs after each menu transition.
 comboboxes=[re.search(r'ref=(e\d+)',line).group(1) for line in snap().splitlines() if 'combobox' in line and re.search(r'ref=(e\d+)',line)]
 browser(['click','@'+comboboxes[-1]])
 browser(['click',ref_for('option',op)])

def run(op):
 if op=='upgrade': prep=set_upgrade()
 else: prep=None
 before_url=browser(['get','url']).splitlines()[-1]
 if before_url!=BASE+'/applications/'+APP: raise RuntimeError('app route changed before '+op)
 old={x.get('id') for x in (api(f'/api/applications/{APP}/executions')[1] or [])}
 choose(op)
 browser(['network','requests','--clear'])
 t=time.monotonic(); browser(['click',ref_for('button','部署')])
 observations=[]; new=None; deadline=t+25
 while time.monotonic()<deadline:
  _,rows=api(f'/api/applications/{APP}/executions')
  match=[x for x in rows or [] if x.get('id') not in old]
  if match:
   new=match[0]
   signature=(new.get('id'),new.get('status'))
   if not observations or observations[-1]['signature']!=signature:
    observations.append({'signature':signature,'elapsed_ms':round((time.monotonic()-t)*1000),'status':new.get('status')})
   if new.get('status') in ('SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'):break
  time.sleep(.01)
 if not new or new.get('status')!='SUCCESS':raise RuntimeError(op+' did not reach SUCCESS')
 state_code,states=api(f'/api/applications/{APP}/state'); state=next((x for x in states or [] if x.get('node_id')==NODE),None)
 browser(['wait','3500'])
 ui=eval_js("JSON.stringify({url:location.pathname,body:document.body.innerText,health:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('健康检查'))?.innerText||'',history:[...document.querySelectorAll('[data-slot=card]')].find(c=>c.querySelector('[data-slot=card-title]')?.innerText.includes('执行历史'))?.innerText||''})")
 reqs=browser(['network','requests']).splitlines()
 result={'operation':op,'execution_id':new['id'],'execution_status':new['status'],'api_status_observations':observations,'app_state_http':state_code,'app_state':state,'ui':ui,'deploy_request':[x for x in reqs if '/api/applications/'+APP+'/deploy' in x],'same_route_without_operation_reload':ui['url']=='/applications/'+APP,'version_sha_expected':V2SHA if op=='upgrade' else V1SHA,'prep_save':prep}
 result['ui_feedback_ok']=result['same_route_without_operation_reload'] and op in ui['health'] and ('已停止' in ui['health'] if op=='stop' else '健康' in ui['health']) and 'SUCCESS' in ui['history']
 return result

if __name__=='__main__':
 report={'case':'WEB-APP-LIVE-STATE + FULL-08 owned Application operations','authority':BASE,'hub_sha256':'164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6','agent_sha256':'d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add','application_id':APP,'unit':UNIT,'app_directory':DIR,'target_node_id':NODE,'target_node_hostname':NODE_NAME,'v1_artifact_sha256':V1SHA,'v2_artifact_id':V2ID,'v2_artifact_sha256':V2SHA,'v2_artifact_seed':'API multipart fixture setup; UI upload attempt stalled before any POST and was not counted as UI upload PASS','tool':'agent-browser','operations':[],'host_inspection':'pending'}
 if REPORT.exists(): raise SystemExit('refuse overwrite report')
 browser(['open',BASE+'/applications/'+APP]);browser(['wait','--load','networkidle'])
 for op in ['stop','start','restart','upgrade']:
  row=run(op); report['operations'].append(row)
  fd=os.open(REPORT,os.O_CREAT|os.O_WRONLY|os.O_EXCL if len(report['operations'])==1 else os.O_WRONLY,0o644) if len(report['operations'])==1 else None
  if fd is None:
   tmp=REPORT.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');os.replace(tmp,REPORT)
  else:
   with os.fdopen(fd,'w') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n')
 print(json.dumps({'operations':[{'operation':x['operation'],'status':x['execution_status'],'pending_seen':any(y['status']=='PENDING' for y in x['api_status_observations']),'ui_feedback_ok':x['ui_feedback_ok'],'same_route':x['same_route_without_operation_reload']} for x in report['operations']]}))
