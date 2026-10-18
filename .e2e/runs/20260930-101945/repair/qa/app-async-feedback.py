#!/usr/bin/env python3
"""Prove first deploy/stop/start feedback converges without changing route or reload."""
import json,os,re,subprocess,time,urllib.error,urllib.parse,urllib.request
from pathlib import Path
RUN='20260930-101945';ROOT=Path(__file__).resolve().parents[5];QA=ROOT/'.e2e/runs'/RUN/'repair/qa';PRIV=Path('/tmp/nodesteer-e2e-'+RUN+'-repair');CONN=PRIV/'repair-connection.json';APPID='804e6b14-eb84-4dda-90de-8bcab9586c4c';UNIT='qa-20260930-101945-web-app-6a32f.service';DIR='/tmp/qa-repair-20260930-101945-app-web/app-6a32f';SESSION='node-repair-app-targeted';URL='http://192.168.100.209:18080'
REPORT=QA/'app-async-feedback.json';RES=QA/'app-async-feedback-results.json';BINARY_SHA='9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'
if REPORT.exists() or RES.exists():raise SystemExit('refuse overwrite app timing report')
conn=json.loads(CONN.read_text());token=conn['token'];node=json.loads((QA/'static-health.json').read_text())['agents'][0];
def request(method,path,body=None):
 global token
 def send():
  data=None if body is None else json.dumps(body).encode();headers={'Authorization':'Bearer '+token}
  if data is not None:headers['Content-Type']='application/json'
  try:
   with urllib.request.urlopen(urllib.request.Request(URL+path,data=data,headers=headers,method=method),timeout=12) as r:
    raw=r.read()
    try:value=json.loads(raw) if raw else None
    except (ValueError,UnicodeDecodeError):value=None
    return r.status,value
  except urllib.error.HTTPError as e:
   try:v=json.loads(e.read())
   except Exception:v=None
   return e.code,v
  except urllib.error.URLError:return 0,None
 code,data=send()
 if code==401:
  q=urllib.request.Request(URL+'/api/login',data=json.dumps({'username':conn['admin_username'],'password':conn['admin_password']}).encode(),headers={'Content-Type':'application/json'},method='POST')
  with urllib.request.urlopen(q,timeout=12) as r:token=json.loads(r.read())['token']
  conn['token']=token;tmp=PRIV/'.repair-connection-app-feedback.tmp';fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as f:json.dump(conn,f,ensure_ascii=False,indent=2);f.write('\n')
  os.replace(tmp,CONN);CONN.chmod(0o600);code,data=send()
 return code,data
def browser(args,timeout=15):
 p=subprocess.run(['agent-browser','--session',SESSION,'--allowed-domains','192.168.100.209']+args,text=True,capture_output=True,timeout=timeout)
 if p.returncode:raise RuntimeError('browser action failed: '+p.stdout[-160:]+p.stderr[-160:])
 return p.stdout.strip()
def screenshot(name):
 output=browser(['screenshot',str(QA/(name+'.png'))],20)
 m=re.search(r'Screenshot saved to (.+)',output)
 if not m:raise RuntimeError('screenshot path missing')
 import shutil
 shutil.copyfile(m.group(1),QA/(name+'.png'))
def inspector():
 p=subprocess.run(['python3',str(QA/'guest-inspect-repair.py'),UNIT,DIR,'inspect'],text=True,capture_output=True,timeout=50)
 if p.returncode:raise RuntimeError('guest inspect failed: '+p.stderr[-250:])
 return json.loads(p.stdout)
def ui_feedback(operation,timeout=8):
 js='''(async()=>{const end=Date.now()+8000;let last={};while(Date.now()<end){const cards=[...document.querySelectorAll('[data-slot="card"]')];const health=cards.find(c=>c.querySelector('[data-slot="card-title"]')?.innerText.includes('健康检查'));const history=cards.find(c=>c.querySelector('[data-slot="card-title"]')?.innerText.includes('执行历史'));const ht=health?.innerText||'';const xt=history?.innerText||'';last={health:ht,history:xt};const op=__OP__;const opOk=ht.toLowerCase().includes(op.toLowerCase())||ht.includes(({deploy:'部署',stop:'停止',start:'启动',restart:'重启'}[op]||op));const healthy=op==='stop'?(ht.includes('已停止')||ht.toLowerCase().includes('stopped')):(ht.includes('健康')||ht.toLowerCase().includes('healthy'));if(ht.includes(__NODE__)&&opOk&&healthy&&xt.includes('SUCCESS'))return JSON.stringify({ok:true,elapsed_ms:8000-(end-Date.now()),health:ht,history_contains_success:true,path:location.pathname});await new Promise(r=>setTimeout(r,150));}return JSON.stringify({ok:false,health:last.health,history:last.history,path:location.pathname});})()'''.replace('__OP__',json.dumps(operation)).replace('__NODE__',json.dumps(node['name']))
 return json.loads(browser(['eval',js],15))
def operate(operation):
 before_url=browser(['get','url']);assert before_url==URL+'/applications/'+APPID,before_url
 code,old=request('GET',f'/api/applications/{APPID}/executions');assert code==200 and isinstance(old,list),(code,old)
 prior={x.get('id') for x in old};snap=browser(['snapshot','-i'])
 if operation!='deploy':
  match=re.search(r'combobox[^\n]*: [^\n]*\[ref=(e\d+)\]',snap)
  # Select the operation dropdown, uniquely identified by current value in the tail of the editor.
  refs=re.findall(r'combobox[^\n]*\[ref=(e\d+)\]',snap)
  if not refs:raise RuntimeError('operation dropdown not found')
  browser(['click','@'+refs[-1]])
  browser(['find','role','option','click','--name',operation])
  snap=browser(['snapshot','-i'])
 # Click the operation submit button without navigating away from the application.
 refs=re.findall(r'button "(?:部署|deploy)"[^\n]*\[ref=(e\d+)\]',snap,re.I)
 if not refs:
  # Snapshot aliases; fall back to accessible role/name.
  browser(['find','role','button','click','--name','部署'])
 else:browser(['click','@'+refs[-1]])
 started=time.monotonic();terminal=None;execution=None
 deadline=started+60
 while time.monotonic()<deadline:
  code,rows=request('GET',f'/api/applications/{APPID}/executions')
  if code==200 and isinstance(rows,list):
   found=[x for x in rows if x.get('id') not in prior and x.get('operation')==operation]
   if found:
    execution=found[0]
    if execution.get('status') in ('SUCCESS','FAILED','BLOCKED','CANCELED','TIMED_OUT'):
     terminal=time.monotonic();break
  time.sleep(.25)
 if not execution or not terminal:raise RuntimeError(f'{operation}: application execution did not reach terminal within 60s')
 if execution['status']!='SUCCESS':raise RuntimeError(f'{operation}: terminal status {execution["status"]}')
 code,states=request('GET',f'/api/applications/{APPID}/state');assert code==200
 state=next((x for x in states if x.get('node_id')==node['id']),None);assert state and state.get('operation')==operation,state
 guest=inspector();props=guest.get('properties',{});files=guest.get('files',{})
 expected_active='inactive' if operation=='stop' else 'active';assert props.get('ActiveState')==expected_active,props
 assert files.get('binary',{}).get('sha256')==BINARY_SHA,files.get('binary')
 assert files.get('config.ini',{}).get('content')=='qa_version=async-v1\n',files.get('config.ini')
 # Start waiting only after API terminal and real guest health; keep same page and let UI's own query path converge.
 ui=ui_feedback(operation,8);end=time.monotonic();result={'operation':operation,'execution_id':execution['id'],'execution_status':execution['status'],'api_terminal_seconds':round(terminal-started,3),'guest':{'active_state':props.get('ActiveState'),'binary_sha256':files['binary']['sha256'],'config_content':files['config.ini']['content'],'unit_contains_app_path':DIR+'/binary' in guest.get('unit_content','')},'app_state':state,'ui_after_terminal_seconds':round(end-terminal,3),'ui_feedback':ui,'url_before':before_url,'url_after':browser(['get','url']),'no_reload_or_route_change':before_url==browser(['get','url'])}
 screenshot('app-async-'+operation+'-after')
 return result

# The app editor was loaded by a real browser session and saved through the UI. Keep route unchanged across operations.
results=[]
for op in ['deploy','stop','start']:
 results.append(operate(op))
summary={'case_id':'WEB-APP-LIVE-STATE','status':'passed' if all(x['ui_feedback']['ok'] and x['no_reload_or_route_change'] for x in results) else 'failed','hub_sha256':'164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6','agent_sha256':'d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add','app_artifact_sha256':BINARY_SHA,'application_id':APPID,'node_id':node['id'],'evidence':results,'secrets_included':False}
REPORT.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':summary['status'],'operations':[{'operation':x['operation'],'api_terminal_seconds':x['api_terminal_seconds'],'ui_after_terminal_seconds':x['ui_after_terminal_seconds'],'ui_ok':x['ui_feedback']['ok'],'guest':x['guest']['active_state']} for x in results]}))
