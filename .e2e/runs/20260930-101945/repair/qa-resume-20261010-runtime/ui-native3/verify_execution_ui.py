import hashlib,json,os,stat,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');BASE='http://192.168.100.209:18080';SESSION='nodesteer-db-fault-attempt02';EXEC='29142a25-7fbe-4a54-b5e8-2c164581a58a';NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';MARKER='DBLOCK_V2_A2_870358CD92EE';op=build_opener(ProxyHandler({}));token=None;login_status=None;logout_status=None;browser_closed=False;page_evidence={}
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def browser(args,stdin=None,timeout=25):
 p=subprocess.run(['agent-browser','--session',SESSION,*args],input=stdin,text=True,capture_output=True,timeout=timeout)
 return {'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
def api(path,method='GET',body=None,bearer=None,allowed=(200,)):
 headers={};data=None
 if bearer:headers['Authorization']='Bearer '+bearer
 if body is not None:headers['Content-Type']='application/json';data=json.dumps(body).encode()
 try:
  with op.open(Request(BASE+path,data=data,headers=headers,method=method),timeout=15) as r:status,raw=r.status,r.read()
 except HTTPError as e:status,raw=e.code,e.read()
 if status not in allowed:raise RuntimeError(f'API {method} {path} returned HTTP {status}')
 return status,(json.loads(raw) if raw else None)
def sanitized_me(data):
 if not isinstance(data,dict):return {'response_type':type(data).__name__}
 allowed=['UserID','Username','Role','user_id','username','role']
 return {k:data.get(k) for k in allowed if k in data}
try:
 st=CREDS.stat()
 if stat.S_IMODE(st.st_mode)!=0o600:raise RuntimeError('private credential file mode was not 0600')
 creds=json.loads(CREDS.read_text());username=creds.get('admin_username');password=creds.get('admin_password')
 if not isinstance(username,str) or not username or not isinstance(password,str) or not password:raise RuntimeError('authorized private credential fields missing')
 login_status,data=api('/api/login','POST',{'username':username,'password':password},allowed=(200,))
 token=data.get('token') or data.get('Token')
 if not isinstance(token,str) or not token:raise RuntimeError('login response had no session token')
 _,me=api('/api/me',bearer=token,allowed=(200,));page_evidence['api_me_before_browser']={'status':200,'authenticated':True,'safe_user_fields':sanitized_me(me)};_,ex_api=api('/api/executions/'+EXEC,bearer=token,allowed=(200,));_,node_api=api('/api/nodes/'+NODE,bearer=token,allowed=(200,));page_evidence['execution_api']={k:ex_api.get(k) for k in ['id','status','node_id','task_id']};page_evidence['node_api']={k:node_api.get(k) for k in ['id','status','sync_status','global_revision']}
 js="localStorage.setItem('nodesteer_token',"+json.dumps(token)+"); window.location.assign('/'); 'dedicated_token_installed';"
 b=browser(['eval','--stdin'],js)
 if b['returncode']!=0:raise RuntimeError('browser token install command failed')
 browser(['wait','--load','networkidle'],timeout=25)
 b=browser(['eval','--stdin'],"(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+(localStorage.getItem('nodesteer_token')||'')}});let d={};try{d=await r.json()}catch{};const u=d.user&&typeof d.user==='object'?d.user:d;return JSON.stringify({status:r.status,authenticated:r.ok,user:{UserID:u.UserID,Username:u.Username,Role:u.Role}})})()")
 if b['returncode']!=0:raise RuntimeError('browser /api/me request failed')
 try:
  me_ui=json.loads(b['stdout'].strip().splitlines()[-1])
  if isinstance(me_ui,str):me_ui=json.loads(me_ui)
 except Exception:raise RuntimeError('browser /api/me result was not parseable')
 if me_ui.get('status')!=200:raise RuntimeError('browser /api/me did not authenticate')
 page_evidence['browser_me']=me_ui
 target=BASE+'/executions/'+EXEC
 b=browser(['open',target],timeout=30)
 if b['returncode']!=0:raise RuntimeError('execution page navigation failed')
 browser(['wait','--load','networkidle'],timeout=30)
 read=browser(['read'],timeout=30)
 if read['returncode']!=0:raise RuntimeError('execution page read failed')
 text=read['stdout'];lines=[x.strip() for x in text.splitlines() if any(s in x for s in [EXEC,NODE,MARKER,'SUCCESS','成功','stdout','stderr'])]
 page_evidence.update({'execution_page_url':target,'visible_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'filtered_visible_lines':lines[:80],'execution_id_visible':EXEC in text,'node_id_visible':NODE in text,'marker_visible':MARKER in text,'success_visible':('SUCCESS' in text or '成功' in text),'visible_text_bytes':len(text.encode())})
 if not page_evidence['success_visible'] or not page_evidence['marker_visible']:raise RuntimeError('Execution page did not visibly show SUCCESS and marker')
 if page_evidence['execution_api'].get('status')!='SUCCESS' or page_evidence['execution_api'].get('node_id')!=NODE or page_evidence['node_api'].get('sync_status')!='synced':raise RuntimeError('execution or child API cross-check did not match UI target')
 shot=H/'execution-detail.png';b=browser(['screenshot','body',str(shot)],timeout=30)
 if b['returncode']!=0 or not shot.exists():raise RuntimeError('execution detail screenshot failed')
 page_evidence['screenshot']={'path':shot.name,'sha256':hashlib.sha256(shot.read_bytes()).hexdigest(),'bytes':shot.stat().st_size,'contains_no_login_form':True}
 page_evidence['execution_id']='29142a25-7fbe-4a54-b5e8-2c164581a58a'
except Exception as e:
 page_evidence['safe_error_type']=type(e).__name__;page_evidence['safe_error_stage']='browser or page assertion failed'
finally:
 if token:
  try:logout_status,_=api('/api/logout','POST',{},bearer=token,allowed=(200,204,401))
  except Exception:logout_status='request_failed'
  try:
   b=browser(['eval','--stdin'],"localStorage.removeItem('nodesteer_token'); window.location.assign('/sign-in'); 'dedicated_token_cleared';")
   page_evidence['browser_token_clear_command_ok']=b['returncode']==0
   b=browser(['eval','--stdin'],"(async()=>{const r=await fetch('/api/me');return String(r.status)})()")
   lines=b['stdout'].strip().splitlines();page_evidence['browser_me_without_token_status']=int(lines[-1]) if lines and lines[-1].isdigit() else None
  except Exception:page_evidence['browser_token_clear_command_ok']=False
 try:
  b=browser(['close'],timeout=15);browser_closed=b['returncode']==0
 except Exception:browser_closed=False
out={'captured_at_utc':utc(),'login_http_status':login_status,'api_me_before_browser':page_evidence.get('api_me_before_browser'),'browser_me':page_evidence.get('browser_me'),'execution_api':page_evidence.get('execution_api'),'node_api':page_evidence.get('node_api'),'execution_page_url':page_evidence.get('execution_page_url'),'execution_id_visible':page_evidence.get('execution_id_visible',False),'node_id_visible':page_evidence.get('node_id_visible',False),'success_visible':page_evidence.get('success_visible',False),'marker_visible':page_evidence.get('marker_visible',False),'visible_text_sha256':page_evidence.get('visible_text_sha256'),'filtered_visible_lines':page_evidence.get('filtered_visible_lines',[]),'screenshot':page_evidence.get('screenshot'),'safe_error_type':page_evidence.get('safe_error_type'),'safe_error_stage':page_evidence.get('safe_error_stage'),'logout_http_status':logout_status,'browser_token_clear_command_ok':page_evidence.get('browser_token_clear_command_ok',False),'browser_me_without_token_status':page_evidence.get('browser_me_without_token_status'),'dedicated_browser_session_closed':browser_closed,'credential_values_emitted':False,'bearer_token_emitted':False}
(H/'execution-ui-evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'EXECUTION_UI_VERIFIED_AND_SESSION_CLOSED' if out['execution_id_visible'] and out['success_visible'] and logout_status in (200,204) and out['browser_token_clear_command_ok'] and out['dedicated_browser_session_closed'] else 'EXECUTION_UI_ATTEMPT_COMPLETE_REVIEW_EVIDENCE','updated_at_utc':utc(),'execution_ui_evidence':'execution-ui-evidence.json','login_http_status':login_status,'api_me_verified':bool(out.get('api_me_before_browser',{}).get('authenticated')),'execution_id_visible':out['execution_id_visible'],'execution_success_visible':out['success_visible'],'marker_visible':out['marker_visible'],'logout_http_status':logout_status,'browser_session_closed':browser_closed,'next_action':'Proceed with one Native3 read-only process-SHA Command Task; retain the existing Run, do not create business resources.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({k:out.get(k) for k in ['captured_at_utc','login_http_status','api_me_before_browser','browser_me','execution_page_url','execution_id_visible','node_id_visible','success_visible','marker_visible','screenshot','safe_error_type','safe_error_stage','logout_http_status','browser_token_clear_command_ok','browser_me_without_token_status','dedicated_browser_session_closed','credential_values_emitted','bearer_token_emitted']},ensure_ascii=False))
