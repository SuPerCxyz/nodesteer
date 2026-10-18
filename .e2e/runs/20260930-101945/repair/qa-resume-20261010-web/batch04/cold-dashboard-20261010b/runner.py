import datetime,hashlib,json,os,re,signal,subprocess,time,urllib.request
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect

R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04/cold-dashboard-20261010b')
ORIGIN='http://127.0.0.1:38181';SESSION='qawebsecure20261010-b04-colddash-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1']
SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
T=None;TICKS=None;AH=None;OPEN=False;C=None;STAGE='START';LAST_CLI=None;LAST_EVAL=None;TOKEN_OK=False

class E(RuntimeError):pass
class CDP:
 def __init__(self,url):self.ws=ws_connect(url,open_timeout=10,close_timeout=3);self.i=0;self.sid=None
 def call(self,m,p=None,s=None,timeout=8):
  self.i+=1;i=self.i;q={'id':i,'method':m,'params':p or {}}
  if s:q['sessionId']=s
  self.ws.send(json.dumps(q));end=time.monotonic()+timeout
  while time.monotonic()<end:
   try:x=json.loads(self.ws.recv(timeout=min(1,max(.1,end-time.monotonic()))) or '{}')
   except Exception:continue
   if x.get('id')==i:
    if 'error' in x:raise E('cdp '+m+' rejected '+str(x['error'].get('code')))
    return x.get('result',{})
  raise E('cdp timeout '+m)
 def attach(self):
  xs=self.call('Target.getTargets').get('targetInfos',[]);pages=[x for x in xs if x.get('type')=='page'];ok=lambda u:u.startswith(ORIGIN) or u in ('about:blank','data:,','chrome://newtab/')
  if not pages or any(not ok(x.get('url','')) for x in pages):raise E('foreign page target guard')
  p=next((x for x in pages if x.get('url','').startswith(ORIGIN)),None)
  if not p:raise E('repair target missing')
  self.sid=self.call('Target.attachToTarget',{'targetId':p['targetId'],'flatten':True}).get('sessionId');return {'page_count':len(pages),'all_owned_or_blank':True}
 def close(self):self.ws.close()

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(name,data):(R/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def progress(phase,**kw):
 d=json.loads((R/'progress.json').read_text());d.update({'phase':phase,'updated_at_utc':now(),'executor':'gpt-6-luna','browser_open':OPEN,'tunnel_started':bool(T and T.poll() is None),'private_values_saved':False,'raw_auth_or_api_body_saved':False});d.update(kw);(R/'progress.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'stage':STAGE,'browser_open':OPEN}),flush=True)
def cli(args,stdin=None,timeout=25,check=True):
 global LAST_CLI
 p=subprocess.run(AB+[str(x) for x in args],input=stdin,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:
  raw=p.stdout+'\n'+p.stderr
  try:
   for f in (CREDS,SHARED):
    if f.is_file():
     for v in json.loads(f.read_text()).values():
      if isinstance(v,str) and v:raw=raw.replace(v,'[FILTERED]')
  except Exception:pass
  low=raw.lower();kind='other'
  for q,k in [('syntaxerror','javascript_syntax'),('strict mode','strict_match'),('multiple elements','strict_match'),('not found','not_found'),('not visible','not_visible'),('timeout','timeout'),('unsupported','unsupported')]:
   if q in low:kind=k;break
  action=(str(args[1]) if len(args)>1 and args[0]=='--json' else str(args[0])) if args else 'unknown';LAST_CLI={'action':action,'stage':STAGE,'returncode':p.returncode,'error_class':kind,'raw_saved':False};raise E('agent-browser action failed; output suppressed')
 return p.returncode,p.stdout
def ev(js):
 global LAST_EVAL
 LAST_EVAL={'stage':STAGE,'length':len(js),'sha256':hashlib.sha256(js.encode()).hexdigest()};_,out=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(out)['data']['result']
 except Exception:raise E('filtered eval result unavailable')
def clear_network():
 rc,_=cli(['network','requests','--clear'],check=False)
 if rc:raise E('network buffer clear failed')
def safe_network():
 rc,out=cli(['--json','network','requests'],check=False);keep=[];ignored=0
 if rc:return {'requests':keep,'ignored_count':0,'query_exit':rc}
 try:xs=json.loads(out).get('data',{}).get('requests',[])
 except Exception:return {'requests':keep,'ignored_count':0,'parse_ok':False}
 for x in xs:
  u=x.get('url') if isinstance(x,dict) else None
  if not isinstance(u,str):ignored+=1;continue
  try:v=urlparse(u)
  except Exception:ignored+=1;continue
  if v.scheme not in ('http','https') or v.netloc!='127.0.0.1:38181':ignored+=1;continue
  keep.append({'scheme':v.scheme,'host_local':True,'path':v.path,'method':x.get('method') if isinstance(x.get('method'),str) else None,'status':x.get('status') if isinstance(x.get('status'),int) else None,'failed':bool(x.get('failureText') or x.get('error'))})
 return {'requests':keep,'ignored_count':ignored}
def snapshots():
 rc,raw=cli(['snapshot','-i','--urls'],check=False,timeout=20)
 if rc:raise E('accessibility snapshot failed')
 arr=[]
 for line in raw.splitlines():
  m=re.search(r'\b(link|button)\s+"([^"]*)"',line,re.I);ref=re.search(r'\[ref=(e\d+)\]|(@e\d+)',line);href=re.search(r'href=["\']?([^\s"\']+)',line)
  if m and ref:
   refid=ref.group(1) or ref.group(2);path=href.group(1) if href else None
   if path and path.startswith(ORIGIN):path=urlparse(path).path
   arr.append({'role':m.group(1).lower(),'label':m.group(2),'ref':refid if refid.startswith('@') else '@'+refid,'href':path})
 return arr
def find_entry(entries,role=None,href=None,labels=()):
 for x in entries:
  if role and x['role']!=role:continue
  if href and x.get('href')!=href:continue
  if labels and not any(re.fullmatch(p,x['label'],re.I) for p in labels):continue
  return x
 return None
def dom_route_links(href,main_only=False):
 scope='main a[href]' if main_only else 'a[href]'
 return ev('''(h=>[...document.querySelectorAll('''+json.dumps(scope)+''')].filter(a=>a.getAttribute('href')===h&&a.getClientRects().length).map((a,i)=>({index:i,label:(a.innerText||'').trim(),href:a.getAttribute('href'),role:a.getAttribute('role')||'link',slot:a.getAttribute('data-slot')||'',in_sidebar:!!a.closest('[data-slot="sidebar"]')})))('''+json.dumps(href)+''')''')
def ref_for_label(entries,label):
 norm=lambda x:re.sub(r'\s+',' ',x or '').strip().casefold()
 return next((x for x in entries if x['role']=='link' and norm(x['label'])==norm(label)),None)
def waitpath(path,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   q=ev('''({origin:location.origin,path:location.pathname})''')
   if q=={'origin':ORIGIN,'path':path}:return True
  except Exception:pass
  time.sleep(.15)
 return False
def procstart(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);out=[]
 for line in p.stdout.splitlines():
  m=re.search(r'\s([^\s]+):38181\s',line);ids=re.findall(r'pid=(\d+)',line);out.append({'loopback':bool(m and m.group(1)=='127.0.0.1'),'owned':bool(T and str(T.pid) in ids)})
 return out
def preflight():
 g=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);v={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for line in g.stdout.splitlines():
  q=line.split(None,1)
  if len(q)==2 and q[0] in v:v[q[0]].append(q[1])
 assert not v['localforward'] and not v['remoteforward'] and all(x=='none' for x in v['remotecommand']) and all(x!='yes' for x in v['forwardagent']) and not v['controlpath']
 assert not subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
 assert CREDS.is_file() and SHARED.is_file() and not(CREDS.stat().st_mode&0o077)
 return {'extra_forwards':0,'port_free':True,'credentials_mode_0600':True}
def api_login():
 cli(['open',ORIGIN+'/sign-in']);cli(['wait','--load','networkidle']);cr=json.loads(CREDS.read_text());shared=json.loads(SHARED.read_text())['token'];cred=json.dumps({'username':cr['admin_username'],'password':cr['admin_password']})
 js="(async(c,s)=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});let d={};try{d=await r.json()}catch{};const t=d.token??d.Token;if(typeof t==='string'&&t)localStorage.setItem('nodesteer_token',t);return {status:r.status,tokenPresent:typeof t==='string'&&!!t,shared:typeof t==='string'&&t===s,origin:location.origin}})("+cred+","+json.dumps(shared)+")"
 r=ev(js)
 if r['status']!=200 or not r['tokenPresent'] or r['shared'] or r['origin']!=ORIGIN:raise E('dedicated API login guard failed')
 clear_network();cli(['open',ORIGIN+'/groups'])
 path_ok=waitpath('/groups');deadline=time.monotonic()+12;ui={'path':None,'origin':None,'table':False,'alert':False,'skeleton':False,'empty':False}
 while time.monotonic()<deadline:
  ui=ev('''(()=>{const m=document.querySelector('main'),txt=m?.innerText||'';return {path:location.pathname,origin:location.origin,table:!!m?.querySelector('table'),alert:!!m?.querySelector('[role="alert"]'),skeleton:!!m?.querySelector('[aria-busy="true"]'),empty:/no data|暂无数据/i.test(txt)}})()''')
  if ui['empty'] or ui['table'] or ui['alert']:break
  time.sleep(.2)
 me=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}}),g=await fetch('/api/groups',{headers:{Authorization:'Bearer '+t}});let d=[];try{d=await g.json()}catch{};return {me:r.status,groups:g.status,count:Array.isArray(d)?d.length:null,path:location.pathname,origin:location.origin}})()''')
 first={'captured_at_utc':now(),'user_action':'API login in isolated /sign-in then hard navigation to first protected /groups','login':{'http':r['status'],'token_present':r['tokenPresent'],'token_shared':r['shared']},'location_wait_succeeded':path_ok,'ui_state':ui,'api_oracle':me,'network':safe_network()};save('first-protected-groups.json',first);progress('FIRST_PROTECTED_GROUPS_ORACLE',first_protected='first-protected-groups.json',groups_http=me['groups'],groups_count=me['count'],empty_state=ui['empty'],table=ui['table'])
 if not path_ok or me['me']!=200 or me['groups']!=200 or me['count']!=0 or me['path']!='/groups' or me['origin']!=ORIGIN or not (ui['empty'] or ui['table'] or ui['alert']):raise E('first protected Groups state did not become ready within bound')
 return {'login_http':r['status'],'token_nonshared':not r['shared'],'me_http':me['me'],'groups_http':me['groups'],'groups_count':me['count'],'groups_empty_state':ui['empty'],'first_protected_path':me['path']}
def logout():
 return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let p=0,m=0;if(t){p=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;m=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had:!!t,post:p,old_me:m,removed:!localStorage.getItem('nodesteer_token')}})()''')

try:
 STAGE='PREFLIGHT';pf=preflight();save('preflight.json',{'captured_at_utc':now(),**pf});progress('PREFLIGHT',preflight=pf)
 AH=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();T=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);TICKS=procstart(T.pid);ready=False
 for _ in range(60):
  if T.poll() is not None:raise E('owned SSH exited')
  ls=listeners()
  if len(ls)==1 and ls[0]['loopback'] and ls[0]['owned']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as x:ready=200<=x.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise E('exact loopback SSH forward not ready')
 progress('TUNNEL_READY',ssh_pid=T.pid,ssh_startticks=TICKS,ssh_argv_sha256=AH,listener_owner=True)
 STAGE='BROWSER_START';cli(['open',ORIGIN+'/sign-in']);OPEN=True;cli(['wait','--load','networkidle']);cdpurl=cli(['get','cdp-url'])[1].strip()
 if not cdpurl.startswith('ws://127.0.0.1:'):raise E('CDP not loopback')
 C=CDP(cdpurl);target=C.attach();STAGE='FIRST_PROTECTED_GROUPS';auth=api_login();clear_network();progress('GROUPS_COLD_BASELINE',auth=auth,browser_target_guard=target)

 # P03-10: intent-hover the actual sidebar Dashboard link. This loads code only; no dashboard component/query may mount.
 STAGE='P03-10_HOVER_PRELOAD';base_path=ev('''location.pathname''');snap0=snapshots();dash_dom=dom_route_links('/');dash_entry=next((x for x in dash_dom if x['slot']=='sidebar-menu-button' or x['in_sidebar']),None);dash0=ref_for_label(snap0,dash_entry['label']) if dash_entry else None
 save('p03-10-dashboard-link-snapshot.json',{'captured_at_utc':now(),'current_path':base_path,'dom_exact_href_candidates':dash_dom,'snapshot_link_count':sum(x['role']=='link' for x in snap0),'dashboard_snapshot_label':dash0['label'] if dash0 else None,'dashboard_snapshot_ref':dash0['ref'] if dash0 else None})
 if not dash_entry or not dash0:raise E('Dashboard exact sidebar href and snapshot ref could not be paired')
 cli(['hover',dash0['ref']]);time.sleep(.6);hover_path=ev('''location.pathname''');hover_net=safe_network();dash_api=('/api/nodes','/api/executions','/api/applications','/api/schedules','/api/tasks');hover_data=[x for x in hover_net['requests'] if x['path'] in dash_api];chunks=[x for x in hover_net['requests'] if x['path'].startswith('/assets/') and x['path'].endswith('.js') and x['status']==200]
 hover={'dashboard_ref':dash0['ref'],'visible_label':dash0['label'],'href':dash_entry['href'],'location_before':base_path,'location_after':hover_path,'stayed_on_groups':hover_path=='/groups','preloaded_successful_js_chunk_count':len(chunks),'preloaded_js_paths':[x['path'] for x in chunks],'dashboard_data_api_request_count':len(hover_data),'network_requests':hover_net['requests'],'ignored_non_http_or_foreign_url_count':hover_net['ignored_count']}
 save('p03-10-hover-preload.json',{'captured_at_utc':now(),**hover});progress('P03-10_PRELOAD_ORACLE',**{k:v for k,v in hover.items() if k not in ('network_requests',)})
 if hover_path!='/groups' or hover_data or not chunks:raise E('Dashboard hover did not prove an online route-chunk preload without data queries')

 # Re-snapshot because refs are invalidated by page activity. Click the exact same real sidebar href.
 STAGE='P03-10_OFFLINE_CLICK';clear_network();cli(['set','offline','on']);snap1=snapshots();dash_dom1=dom_route_links('/');dash1=ref_for_label(snap1,dash_entry['label'])
 save('p03-10-dashboard-offline-link.json',{'captured_at_utc':now(),'current_path':ev('''location.pathname'''),'dom_exact_href_candidates':dash_dom1,'snapshot_ref':dash1['ref'] if dash1 else None})
 if not dash1:raise E('Dashboard snapshot ref absent after offline toggle')
 cli(['click',dash1['ref']]);dashboard_route=waitpath('/');deadline=time.monotonic()+18;has_alert=False
 while time.monotonic()<deadline:
  has_alert=ev('''!!document.querySelector('[role="alert"]')''')
  if has_alert:break
  time.sleep(.2)
 failed_net=safe_network();expected=('/api/nodes','/api/executions','/api/applications','/api/schedules','/api/tasks');fails={p:any(x['path']==p and (x['status'] is None or x['failed']) for x in failed_net['requests']) for p in expected};retry_snap=snapshots();retry=find_entry(retry_snap,'button',labels=(r'retry',r'retry.*',r'retry',r'重试'))
 if not retry:retry=next((x for x in retry_snap if x['role']=='button' and re.search(r'retry|重试',x['label'],re.I)),None)
 p03={'case':'P03-10','status':'IN_PROGRESS','preload':hover,'dashboard_route':dashboard_route,'offline_error_alert':has_alert,'offline_failed_requests':failed_net['requests'],'ignored_non_http_or_foreign_url_count':failed_net['ignored_count'],'expected_api_failure_observed':fails,'retry_button':{'ref':retry['ref'],'label':retry['label']} if retry else None,'retry_clicked_offline':False};save('case-p03-10-progress.json',p03);progress('P03-10_ERROR_ORACLE',status='IN_PROGRESS',dashboard_route=dashboard_route,error_alert=has_alert,api_failure_count=sum(fails.values()))
 if not dashboard_route or not has_alert or not retry or not any(fails.values()):raise E('real Dashboard error state or data API failure was not observed')
 cli(['click',retry['ref']]);time.sleep(.4);p03['retry_clicked_offline']=True;p03['offline_retry_network']=safe_network()['requests'];
 # Restore online, take fresh snapshot and click a real Retry ref again.
 STAGE='P03-10_ONLINE_RETRY';cli(['set','offline','off']);end=time.monotonic()+18;retry_online=None;restore_snap=[]
 while time.monotonic()<end:
  restore_snap=snapshots();retry_online=next((x for x in restore_snap if x['role']=='button' and re.search(r'retry|重试',x['label'],re.I)),None)
  if retry_online:break
  time.sleep(.2)
 if retry_online:cli(['click',retry_online['ref']])
 end=time.monotonic()+20;restored=False
 while time.monotonic()<end:
  restored=not ev('''!!document.querySelector('[role="alert"]')''')
  if restored:break
  time.sleep(.25)
 online_net=safe_network();success={p:any(x['path']==p and x['status']==200 for x in online_net['requests']) for p in expected};metrics=ev('''(()=>({main:!!document.querySelector('main'),heading:(document.querySelector('header h1')?.innerText||'').trim(),alert:!!document.querySelector('[role="alert"]')}))()''')
 p03.update({'status':'PASS' if dashboard_route and has_alert and retry and all(fails.values()) and p03['retry_clicked_offline'] and retry_online and restored and all(success.values()) else 'PARTIAL','retry_online_button':{'ref':retry_online['ref'],'label':retry_online['label']} if retry_online else None,'online_retry_cleared_alert':restored,'online_get_200_by_api':success,'online_requests':online_net['requests'],'online_ignored_non_http_or_foreign_url_count':online_net['ignored_count'],'dashboard_ui_after_recovery':metrics});save('case-p03-10.json',p03);progress('P03-10_COMPLETE',status=p03['status'],api_failure_count=sum(fails.values()),api_success_count=sum(success.values()))
 if p03['status']!='PASS':raise E('Dashboard outage/recovery acceptance remained partial')

 # P06-09: actual new Group editor footer Cancel. This branch must send zero POST /api/groups.
 STAGE='P06-09_EDITOR';cli(['open',ORIGIN+'/groups']);
 if not waitpath('/groups'):raise E('online Groups route did not return')
 # EmptyState is expected with count zero; open form through the real visible create control.
 snapg=snapshots();create_dom=dom_route_links('/groups/new');create_item=next((x for x in create_dom if re.search(r'new group|create group|新建分组',x['label'],re.I)),None);create=ref_for_label(snapg,create_item['label']) if create_item else None
 save('p06-09-new-group-entry.json',{'captured_at_utc':now(),'dom_exact_href_candidates':create_dom,'snapshot_ref':create['ref'] if create else None})
 if not create:raise E('Groups new-group exact href/snapshot ref absent')
 cli(['click',create['ref']]);
 if not waitpath('/groups/new'):raise E('new Group editor route did not load')
 form=ev('''(()=>({path:location.pathname,input_count:document.querySelectorAll('main input').length}))()''');form_snap=snapshots();cancel_dom=dom_route_links('/groups',True);cancel_item=next((x for x in cancel_dom if re.fullmatch(r'cancel|取消',x['label'],re.I)),None);cancel=ref_for_label(form_snap,cancel_item['label']) if cancel_item else None
 save('p06-09-cancel-snapshot.json',{'captured_at_utc':now(),'editor':form,'main_exact_href_candidates':cancel_dom,'snapshot_ref':cancel['ref'] if cancel else None,'cancel_label':cancel_item['label'] if cancel_item else None})
 if not cancel:raise E('exact form-footer Cancel href=/groups absent from accessibility snapshot')
 clear_network();cli(['click',cancel['ref']]);cancel_back=waitpath('/groups');after=safe_network();posts=[x for x in after['requests'] if x['path']=='/api/groups' and x['method']=='POST']
 p609={'case':'P06-09','status':'PASS' if form['path']=='/groups/new' and form['input_count']>=2 and cancel_back and not posts else 'PARTIAL','user_action':'snapshot ref click on exact visible form-footer Cancel link to /groups','new_editor_input_count':form['input_count'],'cancel_ref':cancel['ref'],'cancel_label':cancel_item['label'],'cancel_href':cancel_item['href'],'returned_to_groups':cancel_back,'post_group_count':len(posts),'same_origin_http_requests':after['requests'],'ignored_non_http_or_foreign_url_count':after['ignored_count'],'group_objects_created':0};save('case-p06-09.json',p609);progress('P06-09_COMPLETE',status=p609['status'],group_posts=len(posts))

except Exception as e:
 msg=str(e)[:170] if isinstance(e,E) else str(e.args[0])[:80] if isinstance(e,KeyError) and e.args and re.fullmatch(r'[a-zA-Z0-9_ -]+',str(e.args[0])) else 'exception redacted';diag={'captured_at_utc':now(),'stage':STAGE,'type':type(e).__name__,'message':msg,'cli_failure':LAST_CLI,'eval_fingerprint':LAST_EVAL,'private_values_saved':False,'raw_cli_output_saved':False};save('error.json',diag);progress('ERROR',status='PARTIAL',error=diag);print(json.dumps({'phase':'ERROR','stage':STAGE,'type':type(e).__name__}),flush=True)
finally:
 try:cli(['set','offline','off'],check=False)
 except Exception:pass
 logout_result={'had':False,'post':None,'old_me':None,'removed':False}
 if OPEN:
  try:
   origin=ev('''location.origin''')
   if origin!=ORIGIN:cli(['open',ORIGIN+'/sign-in']);cli(['wait','--load','networkidle'])
   logout_result=logout();clear_network();close_rc=cli(['close'],check=False)[0]
  except Exception:close_rc=-1
  OPEN=False
 if C:
  try:C.close()
  except Exception:pass
 if T:
  try:
   guarded=T.poll() is None and procstart(T.pid)==TICKS and hashlib.sha256(b'\0'.join(argv(T.pid))).hexdigest()==AH
   if guarded:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
   remain=listeners();tunnel={'guard_matched':guarded,'stopped':T.poll() is not None,'listener_count':len(remain),'listener_gone':len(remain)==0}
  except Exception:tunnel={'guard_matched':False,'stopped':False,'listener_count':-1,'listener_gone':False}
 else:tunnel={'guard_matched':False,'stopped':True,'listener_count':0,'listener_gone':True}
 cleanup={'captured_at_utc':now(),'dedicated_logout':{'had_token':bool(logout_result.get('had')),'post_http':logout_result.get('post'),'old_me_http':logout_result.get('old_me'),'storage_removed':bool(logout_result.get('removed')),'verified':bool(logout_result.get('had')) and logout_result.get('post')==200 and logout_result.get('old_me')==401 and bool(logout_result.get('removed'))},'browser_close_exit':locals().get('close_rc'),'ssh':tunnel,'new_fixture_count':0};save('cleanup.json',cleanup)
 d=json.loads((R/'progress.json').read_text());d.update({'updated_at_utc':now(),'browser_open':False,'tunnel_started':False,'cleanup':cleanup});(R/'progress.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'logout_verified':cleanup['dedicated_logout']['verified'],'browser_close_exit':cleanup['browser_close_exit'],'tunnel_stopped':tunnel['stopped'],'listener_gone':tunnel['listener_gone']},ensure_ascii=False),flush=True)
