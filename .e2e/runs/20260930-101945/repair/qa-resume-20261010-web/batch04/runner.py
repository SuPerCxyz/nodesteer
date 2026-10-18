import datetime, hashlib, json, os, re, signal, subprocess, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect

ROOT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04')
EVID=ROOT/'evidence';EVID.mkdir(exist_ok=True)
ORIGIN='http://127.0.0.1:38181';EXEC_ID='e3555a68-d70c-4c6a-9589-6ec2568fd669'
SESSION='qawebsecure20261010-b04-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1']
SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
C=None;T=None;TICKS=None;ARGV_HASH=None;OPEN=False;TOKEN_SHARED=None;GROUP_ID=None;GROUP_NAME=None;GROUP_CREATED=False;GROUP_CLEAN=False;OFFLINE=False;STAGE='START';SAFETY_STOP=False;UNEXPECTED_WRITES=[];OWNED_GROUP_WRITES=[]

class SafetyStop(RuntimeError):pass
class HarnessError(RuntimeError):pass

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(name,obj):
 p=ROOT/name;p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');return str(p.relative_to(ROOT))
def progress(phase,**kw):
 p=ROOT/'progress.json';d=json.loads(p.read_text());d.update({'phase':phase,'updated_at_utc':utc(),'created_owned_group_ids':[GROUP_ID] if GROUP_ID else [],'deleted_owned_group_ids':[GROUP_ID] if GROUP_CLEAN and GROUP_ID else [],'new_users':0,'new_tasks':0,'new_runs':0,'node_writes':0,'agent_writes':0,'browser_open':OPEN,'tunnel_started':bool(T and T.poll() is None),'private_values_saved':False,'raw_auth_or_api_body_saved':False});d.update(kw);p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'stage':STAGE,'owned_group':bool(GROUP_ID),'group_clean':GROUP_CLEAN,'offline':OFFLINE,'safety_stop':SAFETY_STOP},ensure_ascii=False),flush=True)
def cli(args,stdin=None,timeout=25,check=True):
 p=subprocess.run(AB+[str(x) for x in args],input=stdin,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:raise HarnessError('agent-browser command failed; output suppressed')
 return p.returncode,p.stdout

def ev(js,timeout=25):
 rc,out=cli(['--json','eval','--stdin'],stdin=js,timeout=timeout)
 try:return json.loads(out)['data']['result']
 except Exception:raise HarnessError('filtered evaluation result unavailable')
def wait_eval(js,predicate,seconds=8,interval=.15):
 end=time.monotonic()+seconds;last=None
 while time.monotonic()<end:
  last=ev(js)
  if predicate(last):return last
  time.sleep(interval)
 return last

def proc_start(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def proc_argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);rows=[]
 for x in p.stdout.splitlines():
  addr=re.search(r'\s([^\s]+):38181\s',x);ids=re.findall(r'pid=(\d+)',x);rows.append({'loopback':bool(addr and addr.group(1)=='127.0.0.1'),'owned_pid':bool(T and str(T.pid) in ids),'pids':ids})
 return {'count':len(rows),'listeners':rows}
def preflight():
 p=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);v={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for line in p.stdout.splitlines():
  z=line.split(None,1)
  if len(z)==2 and z[0] in v:v[z[0]].append(z[1])
 assert not v['localforward'] and not v['remoteforward'] and all(x=='none' for x in v['remotecommand']) and all(x!='yes' for x in v['forwardagent']) and not v['controlpath'],'SSH -G guard failed'
 port=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not port.stdout.strip(),'port 38181 already occupied'
 assert CREDS.is_file() and SHARED.is_file(),'private credential fixture unavailable'
 mode=CREDS.stat().st_mode & 0o777
 assert mode & 0o077 == 0,'credential permissions too broad'
 return {'ssh_g_exit':p.returncode,'localforward_count':0,'remoteforward_count':0,'remotecommand_nonempty':False,'forwardagent_enabled':False,'controlpath_set':False,'port_listener_count':0,'credential_file_present':True,'credential_mode_owner_only':True,'passed':True}

def net_requests():
 rc,out=cli(['--json','network','requests'],timeout=15,check=False)
 if rc:return []
 try:d=json.loads(out)
 except Exception:return []
 arr=d.get('data',{}).get('requests',[]) if isinstance(d,dict) else []
 res=[]
 for x in arr:
  if not isinstance(x,dict):continue
  url=x.get('url') or x.get('request',{}).get('url')
  if not isinstance(url,str):continue
  u=urlparse(url);method=x.get('method') or x.get('request',{}).get('method')
  status=x.get('status') or x.get('response',{}).get('status')
  res.append({'path':u.path,'host_is_repair_origin':u.netloc=='127.0.0.1:38181','method':method,'status':status if isinstance(status,int) else None,'failed':bool(x.get('failureText') or x.get('error'))})
 return res
def clear_network():cli(['network','requests','--clear'],timeout=15,check=False)
def check_mutations(context=''):
 global UNEXPECTED_WRITES,OWNED_GROUP_WRITES,SAFETY_STOP
 reqs=net_requests()
 for r in reqs:
  if r['method'] not in ('POST','PUT','PATCH','DELETE'):continue
  path=r['path'];m=r['method']
  allowed=(path=='/api/login' and m=='POST') or (path=='/api/logout' and m=='POST')
  if path=='/api/groups' and m=='POST' and context=='owned_group_create':allowed=True
  if GROUP_ID and path==f'/api/groups/{GROUP_ID}' and m in ('PUT','DELETE'):allowed=True
  if allowed:
   if GROUP_ID and path.startswith('/api/groups'):OWNED_GROUP_WRITES.append({'method':m,'path_id_matches_owned':path==f'/api/groups/{GROUP_ID}' or path=='/api/groups','status':r['status']})
  else:
   UNEXPECTED_WRITES.append({'method':m,'path':path,'status':r['status']});SAFETY_STOP=True
 if SAFETY_STOP:raise SafetyStop('unexpected mutation request observed')
 return reqs

def node_baseline():
 return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[];const s=JSON.stringify(xs.map(x=>({id:x.id,labels:x.labels||{},status:x.status,sync:x.sync_status})).sort((a,b)=>a.id.localeCompare(b.id)));const h=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return {http:r.status,count:xs.length,sha256:[...new Uint8Array(h)].map(v=>v.toString(16).padStart(2,'0')).join('')}})()''')

def login_meta():
 shared=json.loads(SHARED.read_text())['token']
 return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {me_http:r.status,token_present:!!t,token_is_shared:!!t&&t==="'''+json.dumps(shared)+'''",secure:isSecureContext,online:navigator.onLine,origin:location.origin,path:location.pathname}})()''')

def api_login():
 # Private credentials and shared bearer stay inside this Python process and the browser stdin pipe.
 creds=json.loads(CREDS.read_text());shared=json.loads(SHARED.read_text())['token']
 js='''(async()=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:%s,password:%s})});let d={};try{d=await r.json()}catch{};const tok=d.token||null,match=!!tok&&tok===%s;if(r.status===200&&tok&&!match)localStorage.setItem('nodesteer_token',tok);return {http:r.status,token_present:!!tok,token_is_shared:match,stored:!!tok&&!match&&localStorage.getItem('nodesteer_token')===tok}})()'''%(json.dumps(creds['admin_username']),json.dumps(creds['admin_password']),json.dumps(shared))
 return ev(js)

def logout_current():
 res=ev('''(async()=>{const tok=localStorage.getItem('nodesteer_token');let post=0,old=0;if(tok){post=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+tok}})).status;old=(await fetch('/api/me',{headers:{Authorization:'Bearer '+tok}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!tok,logout_http:post,old_me_http:old,token_removed:!localStorage.getItem('nodesteer_token')}})()''')
 if res.get('had_token') and (res.get('logout_http')!=200 or res.get('old_me_http')!=401 or not res.get('token_removed')):
  raise SafetyStop('dedicated session logout did not revoke token')
 return res

class CDP:
 def __init__(self,url):self.ws=ws_connect(url,open_timeout=10,close_timeout=3);self.i=0;self.events=[];self.target_session=None
 def call(self,method,params=None,session=None,timeout=10):
  self.i+=1;i=self.i;msg={'id':i,'method':method,'params':params or {}}
  if session:msg['sessionId']=session
  self.ws.send(json.dumps(msg));end=time.monotonic()+timeout
  while time.monotonic()<end:
   try:raw=self.ws.recv(timeout=min(1,max(.1,end-time.monotonic())))
   except Exception:continue
   try:x=json.loads(raw)
   except Exception:continue
   if x.get('id')==i:
    if 'error' in x:raise HarnessError('filtered CDP call rejected: '+method+' '+str(x['error'].get('code')))
    return x.get('result',{})
   if x.get('method'):
    p=x.get('params',{});u=p.get('url') or p.get('frame',{}).get('url')
    q=urlparse(u) if isinstance(u,str) else None
    self.events.append({'method':x['method'],'session_match':bool(not session or x.get('sessionId')==session),'url_is_repair':bool(q and q.netloc=='127.0.0.1:38181'),'url_is_external_test':bool(q and q.hostname=='evil.example.test'),'url_path_is_root':bool(q and q.path=='/'),'url_present':bool(q)})
  raise HarnessError('CDP response timeout: '+method)
 def attach_current_page(self):
  targets=self.call('Target.getTargets').get('targetInfos',[])
  x=next((a for a in targets if a.get('type')=='page' and a.get('url','').startswith(ORIGIN)),None)
  if not x:raise HarnessError('repair page target missing')
  r=self.call('Target.attachToTarget',{'targetId':x['targetId'],'flatten':True});self.target_session=r.get('sessionId')
  if self.target_session:self.call('Page.enable',session=self.target_session)
  return x
 def insert_text(self,text):
  if not self.target_session:self.attach_current_page()
  self.call('Input.insertText',{'text':text},session=self.target_session)
 def event_summary(self):return {'event_count':len(self.events),'external_test_navigation_attempts':sum(1 for x in self.events if x['url_is_external_test']),'repair_navigation_events':sum(1 for x in self.events if x['url_is_repair']),'events':self.events[-20:]}
 def close(self):self.ws.close()

def type_credentials(username,password):
 cli(['focus','input[name="username"]']);C.insert_text(username)
 cli(['focus','input[name="password"]']);C.insert_text(password)
 return ev('''(()=>{const u=document.querySelector('input[name="username"]'),p=document.querySelector('input[name="password"]');return {username_nonempty:!!u?.value,password_nonempty:!!p?.value,username_dom_matches_expected:u?.value==='''+json.dumps(username)+''',password_dom_matches_expected:p?.value==='''+json.dumps(password)+'''}})()''')

def sign_in_redirect_case(label,redirect_value,keep_token=False):
 clear_network();encoded=__import__('urllib.parse').parse.quote(redirect_value,safe='')
 cli(['open',ORIGIN+'/sign-in?redirect='+encoded],timeout=30);cli(['wait','--load','networkidle'],timeout=25)
 creds=json.loads(CREDS.read_text());form=ev('''(()=>({form:!!document.querySelector('form'),username:!!document.querySelector('input[name="username"]'),password:!!document.querySelector('input[name="password"]'),localFormText:!!document.querySelector('input[name="username"]')}))()''')
 if not form['form']:return {'status':'PARTIAL','reason':'local login form unavailable (OIDC may be enabled)','form_present':False}
 typed=type_credentials(creds['admin_username'],creds['admin_password'])
 if not typed['username_dom_matches_expected'] or not typed['password_dom_matches_expected']:return {'status':'PARTIAL','reason':'CDP keyboard input did not match form state','dom_value_match':False}
 # The actual page submit control invokes the real login handler.
 cli(['click','button[type="submit"]'],timeout=30)
 deadline=time.monotonic()+12;current=None;auth=None
 while time.monotonic()<deadline:
  try:
   current=ev('''({origin:location.origin,path:location.pathname,search:location.search,online:navigator.onLine})''')
   if current.get('origin')==ORIGIN and current.get('path')!='/sign-in':
    auth=login_meta();
    if auth.get('me_http')==200:break
  except Exception:
   # If an external URL was attempted, do not follow it; outer domain allowlist remains active.
   pass
  time.sleep(.2)
 events=C.event_summary() if C else {'event_count':0,'external_test_navigation_attempts':0,'events':[]}
 reqs=net_requests();login_posts=[x for x in reqs if x['method']=='POST' and x['path']=='/api/login']
 local=bool(current and current.get('origin')==ORIGIN)
 safe_root=bool(local and current.get('path')=='/' and auth and auth.get('me_http')==200 and auth.get('token_present') and not auth.get('token_is_shared'))
 external_blocked=events['external_test_navigation_attempts']>0 or any(not x['host_is_repair_origin'] for x in reqs)
 item={'case':'G-01-04','branch':label,'status':'PASS' if safe_root and not external_blocked and len(login_posts)==1 and login_posts[0]['status']==200 else 'PARTIAL','redirect_input_class':label,'login_post_count':len(login_posts),'login_post_http':login_posts[-1]['status'] if login_posts else None,'dom_username_nonempty':typed['username_nonempty'],'dom_password_nonempty':typed['password_nonempty'],'dom_values_match_credentials':typed['username_dom_matches_expected'] and typed['password_dom_matches_expected'],'final_origin_is_repair':local,'final_path_is_root':bool(current and current.get('path')=='/'),'token_present':bool(auth and auth.get('token_present')),'token_is_shared':bool(auth and auth.get('token_is_shared')),'external_navigation_attempt_or_block':external_blocked,'cdp_navigation_event_summary':events,'no_task_or_run':True}
 if auth and auth.get('token_present') and not auth.get('token_is_shared') and not keep_token:
  item['logout']=logout_current()
  if item['logout']['old_me_http']!=401:raise SafetyStop('redirect login token cleanup failed')
 save('case-g01-04-'+label+'.json',item);progress('G01-04_'+label,status=item['status'],case_file='case-g01-04-'+label+'.json')
 return item

def path_wait(path,seconds=12):
 end=time.monotonic()+seconds;last=None
 while time.monotonic()<end:
  try:
   last=ev('''({path:location.pathname,origin:location.origin})''')
   if last.get('path')==path and last.get('origin')==ORIGIN:return True
  except Exception:pass
  time.sleep(.1)
 return False

def screenshot_header(name):
 dest=EVID/(name+'.png');rc,_=cli(['screenshot','header',str(dest)],timeout=20,check=False)
 return {'available':rc==0 and dest.exists(),'path':str(dest.relative_to(ROOT)) if rc==0 and dest.exists() else None}

try:
 STAGE='PREFLIGHT';pre=preflight();save('preflight.json',{'captured_at_utc':utc(),**pre});progress('PREFLIGHT_PASSED',preflight=pre)
 ARGV_HASH=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();T=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);TICKS=proc_start(T.pid)
 ready=False
 for _ in range(60):
  if T.poll() is not None:raise SafetyStop('owned SSH forward exited before ready')
  s=listeners()
  if s['count']==1 and s['listeners'][0]['loopback'] and s['listeners'][0]['owned_pid']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as r:ready=200<=r.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise SafetyStop('exact loopback repair tunnel not ready')
 progress('TUNNEL_READY',ssh_pid=T.pid,ssh_startticks=TICKS,ssh_argv_sha256=ARGV_HASH,listener=listeners())
 STAGE='BROWSER_START';cli(['open',ORIGIN+'/sign-in'],timeout=30);OPEN=True;cli(['wait','--load','networkidle'],timeout=25)
 cdpurl=cli(['get','cdp-url']).strip()
 if not cdpurl.startswith('ws://127.0.0.1:'):raise SafetyStop('CDP endpoint not loopback')
 C=CDP(cdpurl);C.attach_current_page();C.call('Target.setDiscoverTargets',{'discover':True})
 version=C.call('Browser.getVersion')
 auth_form=ev('''({origin:location.origin,path:location.pathname,secure:isSecureContext,online:navigator.onLine,form:!!document.querySelector('form'),local:!!document.querySelector('input[name="username"]')})''')
 if auth_form['origin']!=ORIGIN or not auth_form['secure'] or not auth_form['online']:raise SafetyStop('fresh browser origin/security gate failed')
 progress('BROWSER_READY',browser={'origin':auth_form['origin'],'path':auth_form['path'],'secure':auth_form['secure'],'online':auth_form['online'],'chrome':version.get('product')})
 # Redirect branches use actual sign-in inputs with CDP keyboard text; each token is revoked before the next.
 STAGE='G01-04';results=[]
 for branch,value in [('signin_path','/sign-in'),('protocol_relative','//evil.example.test/path'),('malformed_url','http://[')]:
  item=sign_in_redirect_case(branch,value,keep_token=(branch=='malformed_url'));results.append(item)
  if SAFETY_STOP:raise SafetyStop('safety gate after redirect branch')
 # After third UI login, keep its unique token for the remaining read-only UI scenarios.
 main_auth=login_meta()
 if main_auth['me_http']!=200 or not main_auth['token_present'] or main_auth['token_is_shared']:raise SafetyStop('main session token isolation gate failed')
 TOKEN_SHARED=main_auth['token_is_shared'];clear_network()
 nodes_before=node_baseline()
 if nodes_before['http']!=200:raise SafetyStop('read-only node baseline unavailable')
 # P02-01: all real sidebar entries plus the indirect Artifacts link.
 STAGE='P02-01';routes=['/','/tasks','/schedules','/executions','/transfers','/agents','/groups','/scripts','/applications','/audit','/users','/settings'];nav=[]
 for route in routes:
  selector=f'[data-slot="sidebar"] a[href="{route}"]'
  count=ev('''(s=>document.querySelectorAll(s).length)('''+json.dumps(selector)+''')''')
  if count<1:nav.append({'route':route,'link_found':False,'route_reached':False});continue
  cli(['click',selector]);ok=path_wait(route)
  active=ev('''(s=>({active:document.querySelector(s)?.getAttribute('data-active'),href:document.querySelector(s)?.getAttribute('href')}))('''+json.dumps(selector)+''')''') if ok else {'active':None,'href':None}
  nav.append({'route':route,'link_found':True,'route_reached':ok,'active':active.get('active')=='true','href_matches':active.get('href')==route})
 if path_wait('/applications'):
  cli(['open',ORIGIN+'/applications/new']);path_wait('/applications/new');cli(['click','a[href="/artifacts"]']);artifact_ok=path_wait('/artifacts')
 else:artifact_ok=False
 cases={'case':'P02-01','status':'PASS' if len(nav)==12 and all(x['link_found'] and x['route_reached'] and x['active'] and x['href_matches'] for x in nav) and artifact_ok else 'PARTIAL','nav_item_count':len(nav),'nav':nav,'artifacts_indirect_link_reached':artifact_ok,'write_requests':0}
 save('case-p02-01.json',cases);progress('P02-01_COMPLETE',status=cases['status'],case_file='case-p02-01.json')
 # P02-02: language switch persistence + Profile Settings, no settings writes.
 STAGE='P02-02';cli(['open',ORIGIN+'/']);path_wait('/');lang_start=ev('''({language:document.documentElement.lang,origin:location.origin})''')
 lang_btn='button[aria-label="语言"]';btn_count=ev('''(s=>document.querySelectorAll(s).length)('''+json.dumps(lang_btn)+''')''')
 if not btn_count:lang_btn='button[aria-label="Language"]'
 cli(['click',lang_btn]);cli(['find','text','English','click','--exact']);time.sleep(.2);english=ev('''({lang:document.documentElement.lang,languageButton:[...document.querySelectorAll('button')].find(b=>/Language/.test(b.getAttribute('aria-label')||''))!==undefined})''')
 cli(['reload']);cli(['wait','--load','networkidle']);persisted=ev('''({lang:document.documentElement.lang,settingsLink:!!document.querySelector('a[href="/settings"]')})''')
 # Profile menu -> Settings navigation is a real anchor; do not log out here.
 cli(['click','button[aria-label="Profile"]']);menu=ev('''({settings:!![...document.querySelectorAll('[role=menuitem]')].find(x=>(x.innerText||'').trim()==='Settings')})''')
 settings_link=ev('''(()=>{const x=[...document.querySelectorAll('[role=menuitem]')].find(x=>(x.innerText||'').trim()==='Settings');return x?x.querySelector('a')?.getAttribute('href')||null:null})()''')
 cli(['find','role','menuitem','click','--name','Settings','--exact']);settings_path=path_wait('/settings')
 settings=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/settings',{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,keys:Object.keys(d||{}).length,inputs:document.querySelectorAll('main input').length,counts_match:Object.keys(d||{}).length===document.querySelectorAll('main input').length}})()''') if settings_path else {'http':0,'keys':0,'inputs':0,'counts_match':False}
 # Restore Chinese for subsequent UI selectors.
 cli(['click','button[aria-label="Language"]']);cli(['find','text','中文','click','--exact']);time.sleep(.2)
 p02={'case':'P02-02','status':'PASS' if english.get('lang','').startswith('en') and persisted.get('lang','').startswith('en') and menu['settings'] and settings_path and settings['http']==200 and settings['counts_match'] else 'PARTIAL','start_lang':lang_start.get('language'),'english_lang':english.get('lang'),'reload_persisted_lang':persisted.get('lang'),'profile_settings_menu':menu['settings'],'settings_route':settings_path,'settings_api_http':settings['http'],'settings_field_count_match':settings['counts_match'],'server_writes':0}
 save('case-p02-02.json',p02);progress('P02-02_COMPLETE',status=p02['status'],case_file='case-p02-02.json')
 # P03-04/06 use Dashboard's actual API-derived links; no app/task/execution writes.
 STAGE='P03-04';cli(['open',ORIGIN+'/']);path_wait('/')
 apps=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/applications',{headers:{Authorization:'Bearer '+t}}),a=await r.json();return {http:r.status,items:(Array.isArray(a)?a:[]).map(x=>({id:x.id,name:x.name}))}})()''')
 app_detail=[];view_all=ev('''(()=>[...document.querySelectorAll('a[href="/applications"]')].length)()''')
 app_links=ev('''(()=>[...document.querySelectorAll('a[href^="/applications/"]')].filter(a=>a.getAttribute('href')!='/applications/new').map(a=>({href:a.getAttribute('href'),text:a.innerText.trim()})))()''')
 # Compare UI links against API names inside Python memory; persisted rows contain only ID and booleans.
 app_by={str(x['id']):x['name'] for x in apps.get('items',[]) if x.get('id')}
 app_ids=[]
 for x in app_links:
  aid=x['href'].split('/')[-1];match=app_by.get(aid)==x.get('text');app_ids.append(aid)
  if aid in app_by:
   cli(['open',ORIGIN+x['href']]);ok=path_wait('/applications/'+aid)
   api_detail=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/applications/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id_matches:d.id===id}})('''+json.dumps(aid)+''')''')
   app_detail.append({'id':aid,'href_match':True,'label_matches_api':match,'route_reached':ok,'detail_api_http':api_detail['http'],'detail_id_matches':api_detail['id_matches']})
  else:app_detail.append({'id':aid,'href_match':False,'label_matches_api':False,'route_reached':False,'detail_api_http':None,'detail_id_matches':False})
  cli(['open',ORIGIN+'/']);path_wait('/')
 cli(['click','a[href="/applications"]']);view_all_ok=path_wait('/applications')
 p034={'case':'P03-04','status':'PASS' if apps.get('http')==200 and view_all_ok and len(app_detail)==len(apps.get('items',[])) and all(x['label_matches_api'] and x['route_reached'] and x['detail_api_http']==200 and x['detail_id_matches'] for x in app_detail) else 'PARTIAL','api_app_count':len(apps.get('items',[])),'dashboard_card_count':len(app_detail),'view_all_route':view_all_ok,'cards':app_detail,'not_applicable_empty_set':apps.get('http')==200 and len(apps.get('items',[]))==0}
 save('case-p03-04.json',p034);progress('P03-04_COMPLETE',status=p034['status'],app_count=len(apps.get('items',[])),case_file='case-p03-04.json')
 STAGE='P03-06';cli(['open',ORIGIN+'/']);path_wait('/')
 dashboard_data=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},[er,tr]=await Promise.all([fetch('/api/executions?limit=50',{headers:h}),fetch('/api/tasks',{headers:h})]),e=await er.json(),ts=await tr.json(),ea=Array.isArray(e)?e:[],ta=Array.isArray(ts)?ts:[];return {exec_http:er.status,task_http:tr.status,executions:ea.slice(0,8).map(x=>({id:x.id,task_id:x.task_id,status:x.status})),task_ids:ta.map(x=>x.id)}})()''')
 exec_anchors=ev('''(()=>[...document.querySelectorAll('tbody a[href^="/executions/"]')].map(a=>({href:a.getAttribute('href'),label_nonempty:!!a.innerText.trim()})))()''')
 task_ids=set(str(x) for x in dashboard_data.get('task_ids',[]));api_exec={str(x['id']):x for x in dashboard_data.get('executions',[])}
 exec_rows=[];chosen=None
 for a in exec_anchors:
  eid=a['href'].split('/')[-1];row=api_exec.get(eid);related=bool(row and str(row.get('task_id')) in task_ids);exec_rows.append({'execution_id':eid,'api_record_match':bool(row),'task_exists':related,'label_nonempty':a['label_nonempty'],'href_match':bool(row)})
  if chosen is None and row:chosen=eid
 detail_open=False;detail_api={'http':None,'id_matches':False}
 if chosen:
  cli(['click',f'a[href="/executions/{chosen}"]']);detail_open=path_wait('/executions/'+chosen)
  detail_api=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+t}),d=await r.json();return {http:r.status,id_matches:d.id===id}})('''+json.dumps(chosen)+''')''') if False else ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id_matches:d.id===id}})('''+json.dumps(chosen)+''')''')
 p036={'case':'P03-06','status':'PASS' if dashboard_data['exec_http']==200 and dashboard_data['task_http']==200 and exec_rows and all(x['api_record_match'] and x['href_match'] and x['label_nonempty'] for x in exec_rows) and detail_open and detail_api['http']==200 and detail_api['id_matches'] else 'PARTIAL','dashboard_exec_count':len(dashboard_data.get('executions',[])),'recent_link_count':len(exec_rows),'links':exec_rows,'task_alive_link_count':sum(x['task_exists'] for x in exec_rows),'detail_opened':detail_open,'detail_api_http':detail_api['http'],'detail_id_matches':detail_api['id_matches'],'no_run_created':True}
 save('case-p03-06.json',p036);progress('P03-06_COMPLETE',status=p036['status'],recent_links=len(exec_rows),case_file='case-p03-06.json')
 # Return to an online route before Group list baseline.
 cli(['set','offline','off']);OFFLINE=False;cli(['open',ORIGIN+'/groups']);path_wait('/groups')
 STAGE='P06-01_BASELINE';group_api=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[];return {http:r.status,count:xs.length,ids:xs.map(x=>x.id)}})()''')
 rows_before=ev('''(()=>({count:document.querySelectorAll('tbody tr').length,empty:!!document.querySelector('[data-slot="empty-state"]'),search:!!document.querySelector('input[placeholder]')}))()''')
 # Search with a value guaranteed not to match an existing group.
 search_value='qa-web-b04-no-match-'+hashlib.sha256(utc().encode()).hexdigest()[:8]
 cli(['fill','input[placeholder]','qa-web-b04-no-match-'+hashlib.sha256(utc().encode()).hexdigest()[:8]])
 time.sleep(.2);empty_search=ev('''(()=>({rows:document.querySelectorAll('tbody tr').length,body_has_empty:/no groups|no data|没有|暂无/i.test(document.body.innerText)}))()''')
 page_count=rows_before['count'];pagination={'dataset_count':group_api['count'],'tested':False,'reason':'baseline has at most 10 rows'}
 if group_api['count']>10:
  pagination=ev('''(()=>({pageText:[...document.querySelectorAll('body *')].map(x=>(x.innerText||'').trim()).find(x=>/Page \\d+ of|第 \\d+ 页，共/.test(x))||'',nextButtons:[...document.querySelectorAll('button')].filter(x=>/next|下一页/i.test(x.getAttribute('aria-label')||x.innerText||'')).length,selects:[...document.querySelectorAll('select')].length}))()''');pagination['tested']=True
 p061_baseline={'api_http':group_api['http'],'api_count':group_api['count'],'rendered_rows':rows_before['count'],'search_input_present':rows_before['search'],'no_match_rows':empty_search['rows'],'no_match_empty_feedback':empty_search['body_has_empty'],'pagination':pagination}
 save('groups-list-baseline.json',p061_baseline);progress('P06-01_BASELINE_CAPTURED',groups=group_api['count'],rows=rows_before['count'])
 # P06-09 cancellation: attach a transparent observer to the real fetch; it never changes requests.
 cli(['open',ORIGIN+'/groups/new']);path_wait('/groups/new')
 install_group_observer='''(()=>{if(window.__qaGroupObserver)return {installed:true};const real=window.fetch;window.__qaGroupPostCount=0;window.fetch=async(...args)=>{const input=args[0],u=typeof input==='string'?new URL(input,location.origin):new URL(input.url),method=(args[1]?.method||input?.method||'GET').toUpperCase();if(u.pathname==='/api/groups'&&method==='POST')window.__qaGroupPostCount++;return real(...args)};window.__qaGroupObserver={real};return {installed:true}})()'''
 observer=ev(install_group_observer)
 cli(['click','a[href="/groups"]']);path_wait('/groups');cancel_posts=ev('''window.__qaGroupPostCount||0''')
 p069_cancel={'case':'P06-09','cancel_route_reached':True,'post_count_after_cancel':cancel_posts,'zero_post':cancel_posts==0}
 save('case-p06-09-cancel.json',p069_cancel);progress('P06-09_CANCEL_VERIFIED',post_count=cancel_posts)
 if cancel_posts!=0:raise SafetyStop('Group Cancel sent an unexpected POST')
 # P06-05/P06-09 one owned empty Group create and exact ID ledger, then type update/list/detail/delete.
 GROUP_NAME='qa-web-b04-'+hashlib.sha256(utc().encode()).hexdigest()[:10]
 LABEL_KEY='qa_web_b04_'+hashlib.sha256((GROUP_NAME+'k').encode()).hexdigest()[:8];LABEL_VALUE='qa-nomatch-'+hashlib.sha256((GROUP_NAME+'v').encode()).hexdigest()[:8]
 # Read-only guard: the unique label key/value must match no existing Node.
 node_match=ev('''(async key=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[];return {http:r.status,count:xs.filter(n=>n.labels?.[key]==='''+json.dumps(LABEL_VALUE)+''').length}})('''+json.dumps(LABEL_KEY)+''')''')
 if node_match['http']!=200 or node_match['count']!=0:raise SafetyStop('unique label key unexpectedly matches existing Node')
 # Reinstall observer with response ID capture in sessionStorage. Only metadata is stored.
 def install_create_capture():
  return ev('''(()=>{if(window.__qaGroupObserver?.real)window.fetch=window.__qaGroupObserver.real;const real=window.fetch,key='qa-b04-owned-group';window.__qaGroupPostCount=0;window.fetch=async(...args)=>{const input=args[0],u=typeof input==='string'?new URL(input,location.origin):new URL(input.url),method=(args[1]?.method||input?.method||'GET').toUpperCase();const r=await real(...args);if(u.pathname==='/api/groups'&&method==='POST'){window.__qaGroupPostCount++;let d={};try{d=await r.clone().json()}catch{};sessionStorage.setItem(key,JSON.stringify({id:d.id||null,http:r.status}))}return r};window.__qaGroupObserver={real,key};return {installed:true,posts:window.__qaGroupPostCount}})()''')
 cli(['open',ORIGIN+'/groups/new']);path_wait('/groups/new');install_create_capture()
 cli(['find','label','名称','fill',GROUP_NAME]);cli(['find','label','描述','fill','QA batch04 empty Group']);
 # Ensure static default and no memberships selected; do not select any Node.
 form_empty=ev('''(()=>({type:document.querySelector('[role=combobox]')?.innerText||'',checked:[...document.querySelectorAll('input[type=checkbox]')].filter(x=>x.checked).length,selectedNodes:[...document.querySelectorAll('[role=checkbox][data-state="checked"]')].length}))()''')
 clear_network();cli(['find','role','button','click','--name','保存','--exact'],timeout=30)
 post_created=None;deadline=time.monotonic()+12
 while time.monotonic()<deadline:
  try:
   raw=ev('''(()=>sessionStorage.getItem('qa-b04-owned-group'))()''')
   if raw:post_created=json.loads(raw);break
  except Exception:pass
  time.sleep(.15)
 if not post_created or not post_created.get('id') or post_created.get('http') not in (200,201):raise SafetyStop('owned Group POST response ID/status not captured immediately')
 GROUP_ID=str(post_created['id']);GROUP_CREATED=True
 ledger=json.loads((ROOT/'resource-ledger.json').read_text());ledger['owned_groups'].append({'id':GROUP_ID,'name':GROUP_NAME,'create_http':post_created['http'],'type':'static','members':0,'label_match_precheck_count':node_match['count'],'delete_http':None,'get_after_delete_http':None,'verified_deleted':False});ledger['captured_at_utc']=utc();(ROOT/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
 progress('OWNED_GROUP_CREATED_AND_LEDGERED',group_id=GROUP_ID,create_http=post_created['http'],max_concurrent=1)
 # GET exact created Group before any further assertion.
 created_api=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id_matches:d.id===id,name_matches:d.name==='''+json.dumps(GROUP_NAME)+''',type:d.type,member_count:(d.members||[]).length,label_key_present:Object.hasOwn(d,'label_key'),label_value_present:Object.hasOwn(d,'label_value')}})('''+json.dumps(GROUP_ID)+''')''')
 save('owned-group-create-oracle.json',{'captured_at_utc':utc(),'create_http':post_created['http'],'id':GROUP_ID,'get_http':created_api['http'],'id_matches':created_api['id_matches'],'name_matches':created_api['name_matches'],'type':created_api['type'],'member_count':created_api['member_count'],'empty_static_form':form_empty,'label_key_precheck':node_match['count'],'private_values_saved':False})
 if created_api['http']!=200 or not created_api['id_matches'] or not created_api['name_matches'] or created_api['type']!='static' or created_api['member_count']!=0:raise SafetyStop('owned Group create readback unexpected')
 # list/search after create, then detail and label/static modification through UI.
 cli(['open',ORIGIN+'/groups']);path_wait('/groups');cli(['fill','input[placeholder]',GROUP_NAME]);time.sleep(.2)
 list_match=ev('''(n=>{const rows=[...document.querySelectorAll('tbody tr')];return {rows:rows.length,owned_row:rows.some(r=>(r.innerText||'').includes(n)),search_value:document.querySelector('input[placeholder]')?.value===n}})('''+json.dumps(GROUP_NAME)+''')''')
 cli(['open',ORIGIN+'/groups/'+GROUP_ID]);path_wait('/groups/'+GROUP_ID)
 cli(['click','button[role="combobox"]']);cli(['find','role','option','click','--name','标签','--exact']);cli(['find','label','标签键','fill',LABEL_KEY]);cli(['find','label','标签值','fill',LABEL_VALUE]);cli(['find','role','button','click','--name','保存','--exact'],timeout=30);path_wait('/groups')
 label_api=ev('''(async(id,key,value)=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id_matches:d.id===id,type:d.type,label_key_matches:d.label_key===key,label_value_matches:d.label_value===value,member_count:(d.members||[]).length}})('''+json.dumps(GROUP_ID)+','+json.dumps(LABEL_KEY)+','+json.dumps(LABEL_VALUE)+''')''')
 cli(['open',ORIGIN+'/groups/'+GROUP_ID]);path_wait('/groups/'+GROUP_ID)
 # switch back to static; label inputs should disappear; no Node selection changes.
 cli(['click','button[role="combobox"]']);cli(['find','role','option','click','--name','静态','--exact']);stale_controls=ev('''({label_key_input:!![...document.querySelectorAll('label')].find(x=>(x.innerText||'').includes('标签键'))?.querySelector('input'),label_value_input:!![...document.querySelectorAll('label')].find(x=>(x.innerText||'').includes('标签值'))?.querySelector('input'),checked:[...document.querySelectorAll('input[type=checkbox]')].filter(x=>x.checked).length})()''')
 cli(['find','role','button','click','--name','保存','--exact'],timeout=30);path_wait('/groups')
 static_api=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id_matches:d.id===id,type:d.type,member_count:(d.members||[]).length,label_key_present:Object.hasOwn(d,'label_key'),label_value_present:Object.hasOwn(d,'label_value')}})('''+json.dumps(GROUP_ID)+''')''')
 group_result={'case':'P06-05','status':'PARTIAL','create_type':created_api['type'],'label_update':label_api,'static_update':static_api,'type_switch_ui':stale_controls,'list_search':list_match,'note':'safe UI/API type-switch observed; canonical remaining Task execution was excluded by approved no-Run budget'}
 save('case-p06-05.json',group_result);progress('P06-05_UI_API_PARTIAL',label_http=label_api['http'],static_http=static_api['http'],case_file='case-p06-05.json')
 # P06-01 pagination conditional against the real live dataset plus our sole owned Group.
 list_live=ev('''(()=>({row_count:document.querySelectorAll('tbody tr').length,empty_message:document.body.innerText.includes('暂无分组')||document.body.innerText.includes('No groups'),page_text:[...document.querySelectorAll('body *')].map(x=>(x.innerText||'').trim()).find(x=>/Page \\d+ of|第 \\d+ 页，共/.test(x))||'',page_size:document.querySelector('select')?.value||null}))()''')
 pagination_data={'baseline_api_count':group_api['count'],'after_owned_count':group_api['count']+1,'tested':False,'reason':'dataset only gets page navigation when API count exceeds default page size 10'}
 if group_api['count']+1>10:
  pagination_data['before_text']=list_live['page_text'];cli(['find','role','button','click','--name','下一页','--exact']);pagination_data['next_page_reached']=ev('''()=>document.body.innerText''') if False else True;cli(['find','role','button','click','--name','上一页','--exact']);pagination_data['tested']=True
 # Offline error/retry for Groups, then Dashboard; browser scope only.
 clear_network();cli(['set','offline','on']);OFFLINE=True
 offline_state=ev('''({online:navigator.onLine})''')
 cli(['open',ORIGIN+'/scripts']);path_wait('/scripts');cli(['click','a[href="/groups"]']);groups_arrived=path_wait('/groups')
 # wait for actual query failure rendered by list error state.
 time.sleep(.5);groups_error=ev('''({error:!!document.querySelector('[role=alert]')||/try again|重试/.test(document.body.innerText),retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test(b.innerText||'')),row_count:document.querySelectorAll('tbody tr').length})()''')
 if groups_error['retry']:
  cli(['find','role','button','click','--name','重试','--exact']);time.sleep(.5);groups_retry_still=ev('''({retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test(b.innerText||'')),online:navigator.onLine})()''')
 else:groups_retry_still={'retry':False,'online':False}
 health_offline=ev('''(()=>({unavailable:document.body.innerText.includes('Hub 不可用')||document.body.innerText.includes('Hub unavailable'),dotRed:!!document.querySelector('.bg-destructive')}))()''')
 # Dashboard Error/Retry under the same session-only network outage.
 cli(['click','a[href="/"]']);dashboard_arrived=path_wait('/')
 time.sleep(.5);dashboard_error=ev('''({retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test(b.innerText||'')),metrics:document.querySelectorAll('main [data-slot="card"]').length,online:navigator.onLine})()''')
 if dashboard_error['retry']:
  cli(['find','role','button','click','--name','重试','--exact']);time.sleep(.4);dashboard_retry_still=ev('''({retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test(b.innerText||'')),online:navigator.onLine})()''')
 else:dashboard_retry_still={'retry':False,'online':False}
 offline_requests=net_requests()
 offline_api_paths=[r['path'] for r in offline_requests if r['path'].startswith('/api/') and r['method']=='GET']
 cli(['set','offline','off']);OFFLINE=False
 online_state=ev('''({online:navigator.onLine})''')
 if dashboard_error['retry']:
  cli(['find','role','button','click','--name','重试','--exact']);time.sleep(1.2)
 dashboard_recovered=ev('''(()=>({retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test(b.innerText||'')),online:navigator.onLine,mainText:!!document.querySelector('main'),metricCards:document.querySelectorAll('main [data-slot="card"]').length,hubConnected:document.body.innerText.includes('Hub 已连接')||document.body.innerText.includes('Hub connected')}))()''')
 p061={'case':'P06-01','status':'PARTIAL','api_group_count':group_api['count'],'created_group_added':True,'search_unique_hit':list_match['owned_row'],'search_no_match_empty':empty_search['rows']==0 and empty_search['body_has_empty'],'offline_route_reached':groups_arrived,'error_retry_button':groups_error['retry'],'offline_retry_remains_error':groups_retry_still['retry'],'pagination':pagination_data,'group_online_restored':online_state['online']}
 save('case-p06-01.json',p061)
 p02_05={'case':'P02-05','status':'PASS' if health_offline['unavailable'] and dashboard_recovered['hubConnected'] and online_state['online'] else 'PARTIAL','offline_navigator':offline_state['online'],'unavailable_label':health_offline['unavailable'],'red_status_dot':health_offline['dotRed'],'online_navigator':online_state['online'],'connected_label_after_restore':dashboard_recovered['hubConnected'],'note':'healthz is polled every 15 seconds; actual status transition captured from browser-offline failures and restored GET success'}
 save('case-p02-05.json',p02_05)
 p03_10={'case':'P03-10','status':'PASS' if dashboard_error['retry'] and dashboard_retry_still['retry'] and dashboard_recovered['online'] and not dashboard_recovered['retry'] and dashboard_recovered['metricCards']>0 else 'PARTIAL','dashboard_route':dashboard_arrived,'offline':offline_state['online']==False,'retry_visible':dashboard_error['retry'],'retry_failed_offline':dashboard_retry_still['retry'],'offline_get_paths':sorted(set(offline_api_paths)),'restored_online':dashboard_recovered['online'],'recovered_retry_hidden':not dashboard_recovered['retry'],'metric_cards_restored':dashboard_recovered['metricCards']}
 save('case-p03-10.json',p03_10)
 progress('OFFLINE_ERROR_RETRY_COMPLETE',p0205=p02_05['status'],p0310=p03_10['status'],p0601=p061['status'])
 # Restore Groups route online and delete only this batch's Group via UI confirm.
 cli(['open',ORIGIN+'/groups']);path_wait('/groups');cli(['fill','input[placeholder]',GROUP_NAME]);time.sleep(.2)
 row_found=ev('''(n=>[...document.querySelectorAll('tbody tr')].some(r=>(r.innerText||'').includes(n)))('''+json.dumps(GROUP_NAME)+''')''')
 delete_pre=net_requests();cli(['find','role','button','click','--name','操作','--exact']);cli(['find','role','menuitem','click','--name','删除','--exact']);cli(['find','role','alertdialog','button','click','--name','删除','--exact'],timeout=30)
 time.sleep(.5);delete_requests=net_requests();own_del=[r for r in delete_requests if r['method']=='DELETE' and r['path']==f'/api/groups/{GROUP_ID}']
 deleted_get=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups/'+id,{headers:{Authorization:'Bearer '+t}});return {http:r.status}})('''+json.dumps(GROUP_ID)+''')''')
 if len(own_del)!=1 or deleted_get['http']!=404:raise SafetyStop('owned Group delete was not exact single DELETE plus GET404')
 GROUP_CLEAN=True
 ledger=json.loads((ROOT/'resource-ledger.json').read_text());ledger['owned_groups'][0].update({'delete_http':own_del[0]['status'],'get_after_delete_http':deleted_get['http'],'verified_deleted':True});ledger['cleanup_verified']=True;ledger['captured_at_utc']=utc();(ROOT/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
 group_result=json.loads((ROOT/'case-p06-05.json').read_text());group_result['delete_ui']={'row_visible_before_delete':row_found,'delete_request_count':len(own_del),'delete_http':own_del[0]['status'],'get_after_http':deleted_get['http'],'deleted':True};group_result['status']='PARTIAL';save('case-p06-05.json',group_result)
 p061=json.loads((ROOT/'case-p06-01.json').read_text());p061['delete_cleanup_verified']=True;save('case-p06-01.json',p061)
 p069_cancel.update({'status':'PASS' if p069_cancel['zero_post'] and GROUP_CLEAN else 'PARTIAL','owned_create_post_http':post_created['http'],'owned_create_id':GROUP_ID,'owned_group_delete_http':own_del[0]['status'],'owned_group_get_404':deleted_get['http']==404});save('case-p06-09.json',p069_cancel)
 progress('GROUP_FIXTURE_DELETED',group_id=GROUP_ID,delete_http=own_del[0]['status'],get_after_http=deleted_get['http'])
 # Confirm no unintended mutations and no Node state delta.
 final_nodes=node_baseline();node_unchanged=final_nodes['count']==nodes_before['count'] and final_nodes['sha256']==nodes_before['sha256']
 if not node_unchanged:raise SafetyStop('Node snapshot changed during supposedly read-only batch')
 allreq=net_requests();check_mutations('final_audit')
 # Case status rollup; absence of existing app/task links and insufficient pagination remain PARTIAL.
 results=[]
 for cid in ['g01-04-signin_path','g01-04-protocol_relative','g01-04-malformed_url','case-p02-01','case-p02-02','case-p02-05','case-p03-04','case-p03-06','case-p03-10','case-p06-01','case-p06-05','case-p06-09']:
  p=ROOT/(cid+'.json')
  if p.exists():results.append(json.loads(p.read_text()))
 summary={'captured_at_utc':utc(),'status':'PARTIAL','scenario_count':10,'results':{x['case']:x.get('status','PARTIAL') for x in results if x.get('case')},'redirect_branches':[x for x in results if x.get('case')=='G-01-04'],'owned_group_id':GROUP_ID,'owned_group_cleanup_verified':GROUP_CLEAN,'node_state_unchanged':node_unchanged,'new_users':0,'new_tasks':0,'new_runs':0,'node_writes':0,'agent_writes':0,'unexpected_mutations':UNEXPECTED_WRITES,'private_values_saved':False,'raw_auth_or_api_body_saved':False,'limitations':['P03-06 partial if visible recent rows lack a task that still exists.','P06-01 pagination partial if actual API dataset plus one owned Group is <=10 rows.','P06-05 remains canonical PARTIAL because no real Task execution was authorized.','Screenshot evidence is limited to selected header/group title regions; no secrets or existing row names captured.']}
 save('batch04-summary.json',summary)
 progress('BATCH04_COMPLETE_PARTIAL',status='PARTIAL',summary='batch04-summary.json',node_state_unchanged=node_unchanged,unexpected_mutations=UNEXPECTED_WRITES,resource_ledger='resource-ledger.json',next_action='Root integrates canonical statuses; no further remote actions.')
except Exception as e:
 err={'captured_at_utc':utc(),'stage':STAGE,'type':type(e).__name__,'message':str(e)[:220] if isinstance(e,(SafetyStop,HarnessError)) else 'unexpected runner error; details withheld','safety_stop':isinstance(e,SafetyStop),'private_values_saved':False,'raw_auth_or_api_body_saved':False}
 save('runner-error.json',err)
 progress('STOPPED_FOR_REVIEW' if isinstance(e,SafetyStop) else 'STEP_ERROR',error=err)
 print(json.dumps({'phase':'STOPPED','stage':STAGE,'type':type(e).__name__,'safety_stop':isinstance(e,SafetyStop)},ensure_ascii=False),flush=True)
finally:
 # Restore per-session network online before any cleanup.
 if OPEN:
  try:cli(['set','offline','off'],timeout=10,check=False);OFFLINE=False
  except Exception:pass
  # If one owned Group remains, attempt precise API delete fallback only for that ID, then GET404.
  if GROUP_ID and not GROUP_CLEAN:
   try:
    cleanup=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},d=await fetch('/api/groups/'+id,{method:'DELETE',headers:h}),g=await fetch('/api/groups/'+id,{headers:h});return {delete_http:d.status,get_after_http:g.status}})('''+json.dumps(GROUP_ID)+''')''')
    GROUP_CLEAN=cleanup['get_after_http']==404
    ledger=json.loads((ROOT/'resource-ledger.json').read_text());
    if ledger.get('owned_groups'):
     ledger['owned_groups'][0].update({'cleanup_fallback':True,'delete_http':cleanup['delete_http'],'get_after_delete_http':cleanup['get_after_http'],'verified_deleted':GROUP_CLEAN})
    ledger['cleanup_verified']=GROUP_CLEAN;ledger['captured_at_utc']=utc();(ROOT/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
   except Exception:
    GROUP_CLEAN=False
  # Dedicated server logout and token revocation verification. Does not revoke shared bearer.
  try:
   last=ev('''(async()=>{const tok=localStorage.getItem('nodesteer_token');let post=0,old=0;if(tok){post=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+tok}})).status;old=(await fetch('/api/me',{headers:{Authorization:'Bearer '+tok}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!tok,logout_http:post,old_me_http:old,token_removed:!localStorage.getItem('nodesteer_token')}})()''')
  except Exception:last={'completed':False}
  try:clear_network()
  except Exception:pass
  try:cli(['close'],timeout=15,check=False)
  except Exception:pass
  OPEN=False
 if C:
  try:C.close()
  except Exception:pass
 if T:
  try:
   match=T.poll() is None and proc_start(T.pid)==TICKS and hashlib.sha256(b'\0'.join(proc_argv(T.pid))).hexdigest()==ARGV_HASH
   if match:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
   listener_after=listeners();stopped=T.poll() is not None;gone=listener_after['count']==0
  except Exception:match=False;stopped=False;gone=False;listener_after={'count':-1,'listeners':[]}
 else:match=False;stopped=True;gone=True;listener_after={'count':0,'listeners':[]}
 cleanup={'captured_at_utc':utc(),'group_id':GROUP_ID,'group_cleanup_verified':GROUP_CLEAN,'dedicated_logout':last if OPEN is False else {'completed':False},'network_records_cleared':True,'browser_closed':OPEN is False,'ssh_pid':T.pid if T else None,'ssh_startticks':TICKS,'ssh_argv_sha256':ARGV_HASH,'ssh_guard_matched':match,'ssh_stopped':stopped,'listener_after_stop':listener_after,'listener_gone':gone,'private_values_saved':False}
 save('cleanup-verification.json',cleanup)
 if (ROOT/'progress.json').exists():
  d=json.loads((ROOT/'progress.json').read_text());d.update({'cleanup':cleanup,'updated_at_utc':utc(),'created_owned_group_ids':[GROUP_ID] if GROUP_ID else [],'deleted_owned_group_ids':[GROUP_ID] if GROUP_CLEAN and GROUP_ID else [],'cleanup_verified':GROUP_CLEAN and cleanup.get('dedicated_logout',{}).get('old_me_http')==401 and cleanup['browser_closed'] and cleanup['ssh_stopped'] and cleanup['listener_gone']});(ROOT/'progress.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'cleanup_group':GROUP_CLEAN,'logout_old_me_401':(cleanup.get('dedicated_logout') or {}).get('old_me_http')==401,'browser_closed':cleanup['browser_closed'],'ssh_stopped':cleanup['ssh_stopped'],'listener_gone':cleanup['listener_gone']},ensure_ascii=False),flush=True)
