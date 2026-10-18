import datetime,hashlib,json,os,re,signal,subprocess,time,urllib.request
from pathlib import Path
from urllib.parse import quote,urlparse
from websockets.sync.client import connect as ws_connect
R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04');ORIGIN='http://127.0.0.1:38181';SESSION='qawebsecure20261010-b04-p02resume-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
T=None;TICKS=None;AH=None;OPEN=False;C=None;TARGET_SESSION=None;TOKEN_SHARED=None;STAGE='START';ERRORS=[];CLI_FAILURE=None
class E(RuntimeError):pass
class CDP:
 def __init__(self,url):self.ws=ws_connect(url,open_timeout=10,close_timeout=3);self.i=0;self.events=[];self.sid=None
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
   if x.get('method'):
    z=x.get('params',{});u=z.get('url') or z.get('frame',{}).get('url');v=urlparse(u) if isinstance(u,str) else None
    self.events.append({'method':x['method'],'url_repair':bool(v and v.netloc=='127.0.0.1:38181'),'url_evil_test':bool(v and v.hostname=='evil.example.test'),'url_path_root':bool(v and v.path=='/'),'url_present':bool(v)})
  raise E('cdp timeout '+m)
 def attach(self):
  xs=self.call('Target.getTargets').get('targetInfos',[]);p=next((x for x in xs if x.get('type')=='page' and x.get('url','').startswith(ORIGIN)),None)
  if not p:raise E('repair page target absent')
  self.sid=self.call('Target.attachToTarget',{'targetId':p['targetId'],'flatten':True}).get('sessionId');self.call('Page.enable',s=self.sid);return p
 def insert(self,text):
  if not self.sid:self.attach()
  self.call('Input.insertText',{'text':text},s=self.sid)
 def close(self):self.ws.close()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def progress(ph,**x):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':ph,'updated_at_utc':utc(),'browser_open':OPEN,'tunnel_started':bool(T and T.poll() is None),'private_values_saved':False,'raw_auth_or_api_body_saved':False});p.update(x);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':ph,'stage':STAGE,'browser_open':OPEN},ensure_ascii=False),flush=True)
def cli(a,stdin=None,timeout=25,check=True):
 p=subprocess.run(AB+[str(z) for z in a],input=stdin,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:
  global CLI_FAILURE
  msg=(p.stdout+'\n'+p.stderr).lower();cls='other'
  for needle,label in [('not found','not_found'),('not visible','not_visible'),('not clickable','not_clickable'),('strict mode','strict_match'),('timeout','timeout'),('timed out','timeout'),('not connected','not_connected'),('invalid selector','invalid_selector'),('blocked','blocked')]:
   if needle in msg:cls=label;break
  CLI_FAILURE={'action':str(a[0]) if a else 'unknown','stage':STAGE,'returncode':p.returncode,'error_class':cls,'output_saved':False}
  raise E('agent-browser action failed; output suppressed')
 return p.returncode,p.stdout
def clear_network():
 rc,_=cli(['network','requests','--clear'],timeout=10,check=False)
 if rc: raise E('network request buffer could not be cleared')
def ev(js):
 _,o=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(o)['data']['result']
 except Exception:raise E('filtered eval result unavailable')
def procstart(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);a=[]
 for z in p.stdout.splitlines():
  m=re.search(r'\s([^\s]+):38181\s',z);ids=re.findall(r'pid=(\d+)',z);a.append({'loopback':bool(m and m.group(1)=='127.0.0.1'),'owned':bool(T and str(T.pid) in ids),'pids':ids})
 return {'count':len(a),'listeners':a}
def preflight():
 p=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);d={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for l in p.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in d:d[z[0]].append(z[1])
 assert not d['localforward'] and not d['remoteforward'] and all(x=='none' for x in d['remotecommand']) and all(x!='yes' for x in d['forwardagent']) and not d['controlpath']
 q=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not q.stdout.strip()
 assert CREDS.is_file() and SHARED.is_file() and not(CREDS.stat().st_mode&0o077)
 return {'ssh_g_exit':p.returncode,'extra_forward_count':0,'remotecommand':False,'forwardagent':False,'controlpath':False,'port_free':True,'credentials_private_file':True}
def netreq():
 rc,o=cli(['--json','network','requests'],timeout=15,check=False)
 if rc:return []
 try:a=json.loads(o).get('data',{}).get('requests',[])
 except Exception:return []
 out=[]
 for x in a:
  u=x.get('url') if isinstance(x,dict) else None
  if not isinstance(u,str):continue
  v=urlparse(u);out.append({'path':v.path,'repair_host':v.netloc=='127.0.0.1:38181','method':x.get('method'),'status':x.get('status') if isinstance(x.get('status'),int) else None,'failed':bool(x.get('failureText') or x.get('error'))})
 return out
def waitpath(p,secs=12):
 end=time.monotonic()+secs
 while time.monotonic()<end:
  try:
   x=ev('''({o:location.origin,p:location.pathname})''')
   if x['o']==ORIGIN and x['p']==p:return True
  except Exception:pass
  time.sleep(.15)
 return False
def loginmeta():
 sh=json.loads(SHARED.read_text())['token'];return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {me:r.status,token:!!t,shared:!!t&&t==='''+json.dumps(sh)+''',origin:location.origin,path:location.pathname}})()''')
def logout():
 d=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let p=0,m=0;if(t){p=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;m=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,post:p,old_me:m,removed:!localStorage.getItem('nodesteer_token')}})()''')
 if d['had_token'] and (d['post']!=200 or d['old_me']!=401 or not d['removed']):raise RuntimeError('dedicated token logout was not verified')
 return d
def ui_login(branch,value,keep=False):
 clear_network();cli(['open',ORIGIN+'/sign-in?redirect='+quote(value,safe='')],timeout=30);cli(['wait','--load','networkidle'])
 form=ev('''({form:!!document.querySelector('form'),u:!!document.querySelector('input[name="username"]'),p:!!document.querySelector('input[name="password"]')})''')
 if not form['form']:return {'case':'G-01-04','branch':branch,'status':'PARTIAL','reason':'local sign-in form unavailable'}
 cr=json.loads(CREDS.read_text());cli(['focus','input[name="username"]']);C.insert(cr['admin_username']);cli(['focus','input[name="password"]']);C.insert(cr['admin_password'])
 typed=ev('''(()=>{const u=document.querySelector('input[name="username"]'),p=document.querySelector('input[name="password"]');return {u:!!u?.value,p:!!p?.value}})()''')
 C.events=[]
 cli(['click','button[type="submit"]'],timeout=30);end=time.monotonic()+10;cur=None;auth=None
 while time.monotonic()<end:
  try:
   cur=ev('''({o:location.origin,p:location.pathname})''')
   if cur['o']==ORIGIN and cur['p']!='/sign-in':auth=loginmeta();
   if auth and auth['me']==200:break
  except Exception:pass
  time.sleep(.2)
 evlist=[x for x in C.events if x.get('url_evil_test')];req=netreq();posts=[x for x in req if x['path']=='/api/login' and x['method']=='POST'];external_req=sum(1 for x in req if not x['repair_host'])
 auth=auth or {}
 safe=bool(cur and cur.get('o')==ORIGIN and cur.get('p')=='/' and auth.get('me')==200 and auth.get('token') and not auth.get('shared'))
 blocked=bool(evlist or external_req)
 item={'case':'G-01-04','branch':branch,'status':'PASS' if safe and not blocked and len(posts)==1 and posts[0]['status']==200 else 'PARTIAL','form_visible':form['form'],'username_nonempty':typed['u'],'password_nonempty':typed['p'],'login_post_count':len(posts),'login_post_http':posts[-1]['status'] if posts else None,'final_origin_local':bool(cur and cur.get('o')==ORIGIN),'final_path_root':bool(cur and cur.get('p')=='/'),'token_present':bool(auth.get('token')),'token_shared':bool(auth.get('shared')),'external_navigation_event_count':len(evlist),'external_request_count':external_req,'cdp_navigation_events':C.events[-20:]}
 save('case-g01-04-'+branch+'.json',item)
 if auth.get('token') and not auth.get('shared') and not keep:item['logout']=logout();save('case-g01-04-'+branch+'.json',item)
 return item

def nodehash():
 return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[],s=JSON.stringify(xs.map(n=>({id:n.id,labels:n.labels||{},status:n.status,sync:n.sync_status})).sort((x,y)=>x.id.localeCompare(y.id))),b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return {http:r.status,count:xs.length,sha:[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}})()''')
try:
 STAGE='PREFLIGHT';pf=preflight();save('phase1-preflight.json',{'captured_at_utc':utc(),**pf});progress('P1_PREFLIGHT',preflight=pf)
 AH=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();T=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);TICKS=procstart(T.pid);ready=False
 for _ in range(60):
  if T.poll() is not None:raise RuntimeError('owned forward exited')
  z=listeners()
  if z['count']==1 and z['listeners'][0]['loopback'] and z['listeners'][0]['owned']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as r:ready=200<=r.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise RuntimeError('loopback forward not ready')
 progress('P1_TUNNEL_READY',ssh_pid=T.pid,ssh_startticks=TICKS,ssh_argv_sha256=AH,listener=listeners())
 STAGE='P1_BROWSER';cli(['open',ORIGIN+'/sign-in']);OPEN=True;cli(['wait','--load','networkidle']);u=cli(['get','cdp-url'])[1].strip();
 if not u.startswith('ws://127.0.0.1:'):raise RuntimeError('CDP endpoint is not loopback')
 C=CDP(u);C.attach();ver=C.call('Browser.getVersion');baseline_nodes=nodehash()
 progress('P1_BROWSER_READY',browser={'secure':ev('isSecureContext'),'origin':ev('location.origin'),'chrome':ver.get('product')})
 # Resume at P02-01 without repeating the already recorded G-01-04 branches.
 STAGE='P02_LOGIN';clear_network();cli(['open',ORIGIN+'/sign-in']);cli(['wait','--load','networkidle'])
 cr=json.loads(CREDS.read_text());cli(['focus','input[name="username"]']);C.insert(cr['admin_username']);cli(['focus','input[name="password"]']);C.insert(cr['admin_password']);cli(['click','button[type="submit"]'],timeout=30)
 if not waitpath('/'):raise E('resume login did not land on local dashboard')
 main=loginmeta()
 if main.get('me')!=200 or not main.get('token') or main.get('shared'):raise RuntimeError('final login token is not dedicated and valid')
 TOKEN_SHARED=main['shared'];
 # P02-01 actual navigation click through all 12 sidebar destinations.
 STAGE='P02-01';routes=['/settings'];nav=[]
 for r in routes:
  STAGE='P02-01:'+r
  sel=f'[data-slot="sidebar"] a[href="{r}"]';labels=ev('''(h=>[...document.querySelectorAll(`[data-slot="sidebar"] a[href="${h}"]`)].filter(x=>x.getClientRects().length&&!!(x.innerText||'').trim()).map(x=>(x.innerText||'').trim()))('''+json.dumps(r)+''')''')
  if not labels:nav.append({'route':r,'link':False,'reached':False,'active':False});save('case-p02-01-progress.json',{'case':'P02-01','status':'IN_PROGRESS','routes':nav,'route_count':len(nav)});continue
  label=labels[0];rc,_=cli(['click',sel],check=False);legacy_error=None
  if rc:
   raw=''
   # Error text is classified in process and never written or printed.
   try:
    q=subprocess.run(AB+['click',sel],capture_output=True,text=True,timeout=15,check=False);raw=(q.stdout+'\n'+q.stderr).lower()
   except Exception:raw=''
   legacy_error='strict_duplicate' if any(x in raw for x in ('strict mode','multiple elements','matched 2 elements','matched 3 elements','resolved to')) else 'not_found_or_not_actionable' if any(x in raw for x in ('not found','no element','not visible','not clickable')) else 'unclassified'
   cli(['find','role','link','click','--name',label,'--exact'])
  ok=waitpath(r);active=ev('''(s=>document.querySelector(s)?.getAttribute('data-active'))('''+json.dumps(sel)+''')''') if ok else None;nav.append({'route':r,'link':True,'visible_label_count':len(labels),'locator_recovery_used':bool(legacy_error),'original_locator_error_class':legacy_error,'reached':ok,'active':active=='true'});save('case-p02-01-progress.json',{'case':'P02-01','status':'IN_PROGRESS','routes':nav,'route_count':len(nav),'prior_11_routes_durable_evidence':False})
 # Indirect Artifacts entry from Application editor, no save.
 cli(['open',ORIGIN+'/applications/new']);waitpath('/applications/new');cli(['click','a[href="/artifacts"]']);art=waitpath('/artifacts')
 p201={'case':'P02-01','status':'PARTIAL','routes':nav,'artifacts_indirect':art,'write_requests':0,'limitation':'Only /settings and indirect Artifacts route have durable oracle in this resume. Prior 11 route clicks were attempted before the former CLI failure but no per-route oracle was saved; not repeated per Root instruction.'};save('case-p02-01.json',p201);progress('P02-01_COMPLETE',status=p201['status'])
 # P02-02 language roundtrip and Profile -> Settings, no writes.
 STAGE='P02-02';cli(['open',ORIGIN+'/']);waitpath('/');langbtn='button[aria-label="语言"]'
 if not ev('''(s=>document.querySelectorAll(s).length)('''+json.dumps(langbtn)+''')'''):langbtn='button[aria-label="Language"]'
 cli(['click',langbtn]);cli(['find','text','English','click','--exact']);lang1=ev('''document.documentElement.lang''');cli(['reload']);cli(['wait','--load','networkidle']);lang2=ev('''document.documentElement.lang''')
 prof='button[aria-label="Profile"]';cli(['click',prof]);menu=ev('''[...document.querySelectorAll('[role=menuitem]')].some(x=>(x.innerText||'').trim()==='Settings')''');cli(['find','role','menuitem','click','--name','Settings','--exact']);settingspath=waitpath('/settings')
 settings=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/settings',{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,keyCount:Object.keys(d||{}).length,inputCount:document.querySelectorAll('main input').length}})()''')
 cli(['click','button[aria-label="Language"]']);cli(['find','text','中文','click','--exact']);
 p202={'case':'P02-02','status':'PASS' if lang1.startswith('en') and lang2.startswith('en') and menu and settingspath and settings['http']==200 and settings['keyCount']==settings['inputCount'] else 'PARTIAL','language_en':lang1.startswith('en'),'reload_persisted':lang2.startswith('en'),'profile_settings_menu':menu,'settings_route':settingspath,'settings_get_http':settings['http'],'settings_fields_match':settings['keyCount']==settings['inputCount'],'write_requests':0};save('case-p02-02.json',p202);progress('P02-02_COMPLETE',status=p202['status'])
 # P03-04 dashboard applications card links; only existing records.
 STAGE='P03-04';cli(['open',ORIGIN+'/']);waitpath('/');appdata=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/applications',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[];return {http:r.status,items:xs.map(x=>({id:x.id,name:x.name}))}})()''')
 links=ev('''(()=>[...document.querySelectorAll('a[href^="/applications/"]')].filter(a=>a.getAttribute('href')!='/applications/new').map(a=>({href:a.getAttribute('href'),text:a.innerText.trim()})))()''');names={str(x['id']):x['name'] for x in appdata.get('items',[]) if x.get('id')};apps=[]
 for a in links:
  aid=a['href'].split('/')[-1];match=names.get(aid)==a['text'];cli(['click',f'a[href="/applications/{aid}"]']);ok=waitpath('/applications/'+aid);detail=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/applications/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id:d.id===id}})('''+json.dumps(aid)+''')''') if ok else {'http':0,'id':False};apps.append({'id':aid,'label_matches_api':match,'route':ok,'detail_http':detail['http'],'id_matches':detail['id']});cli(['open',ORIGIN+'/']);waitpath('/')
 cli(['click','a[href="/applications"]']);viewall=waitpath('/applications')
 p304={'case':'P03-04','status':'PASS' if appdata['http']==200 and len(apps)>0 and len(apps)==len(appdata['items']) and viewall and all(x['label_matches_api'] and x['route'] and x['detail_http']==200 and x['id_matches'] for x in apps) else 'PARTIAL','api_count':len(appdata.get('items',[])),'card_count':len(links),'cards':apps,'view_all_route':viewall};save('case-p03-04.json',p304);progress('P03-04_COMPLETE',status=p304['status'],app_count=len(apps))
 # P03-06 recent execution links; no new execution or task.
 STAGE='P03-06';cli(['open',ORIGIN+'/']);waitpath('/');exdata=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},[a,b]=await Promise.all([fetch('/api/executions?limit=50',{headers:h}),fetch('/api/tasks',{headers:h})]),e=await a.json(),ts=await b.json();return {eh:a.status,th:b.status,exec:(Array.isArray(e)?e:[]).slice(0,8).map(x=>({id:x.id,task:x.task_id})),tasks:(Array.isArray(ts)?ts:[]).map(x=>x.id)}})()''')
 domlinks=ev('''(()=>[...document.querySelectorAll('tbody a[href^="/executions/"]')].map(a=>({href:a.getAttribute('href'),nonempty:!!a.innerText.trim()})))()''');emap={str(x['id']):x for x in exdata['exec']};tset=set(str(x) for x in exdata['tasks']);rows=[];selected=None
 for a in domlinks:
  eid=a['href'].split('/')[-1];x=emap.get(eid);exists=bool(x and str(x['task']) in tset);rows.append({'id':eid,'api_record':bool(x),'task_exists':exists,'label_nonempty':a['nonempty']})
  if selected is None and x: selected=eid
 detail={'http':0,'id':False};detail_ok=False
 if selected:
  cli(['click',f'a[href="/executions/{selected}"]']);detail_ok=waitpath('/executions/'+selected)
  if detail_ok:detail=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,id:d.id===id}})('''+json.dumps(selected)+''')''')
 p306={'case':'P03-06','status':'PASS' if exdata['eh']==200 and exdata['th']==200 and rows and all(x['api_record'] and x['label_nonempty'] for x in rows) and any(x['task_exists'] for x in rows) and detail_ok and detail['http']==200 and detail['id'] else 'PARTIAL','dashboard_recent_count':len(rows),'rows':rows,'task_surviving_rows':sum(x['task_exists'] for x in rows),'execution_detail_route':detail_ok,'execution_detail_http':detail['http'],'id_matches':detail['id'],'new_run':False};save('case-p03-06.json',p306);progress('P03-06_COMPLETE',status=p306['status'],links=len(rows),surviving_task_links=p306['task_surviving_rows'])
 nodes_after=nodehash();if_changed=nodes_after['http']!=200 or nodes_after['count']!=baseline_nodes['count'] or nodes_after['sha']!=baseline_nodes['sha']
 if if_changed:raise RuntimeError('Node baseline changed in read-only phase')
 progress('PHASE1_COMPLETE',node_snapshot_unchanged=True)
except Exception as e:
 err={'captured_at_utc':utc(),'phase':'phase1-resume','stage':STAGE,'type':type(e).__name__,'message':str(e)[:180] if isinstance(e,E) else 'exception redacted','cli_failure':CLI_FAILURE,'private_values_saved':False,'raw_auth_body_saved':False};save('phase1-resume-error.json',err);progress('PHASE1_RESUME_ERROR',status='PARTIAL',error=err);print(json.dumps({'phase':'PHASE1_RESUME_ERROR','stage':STAGE,'type':type(e).__name__,'cli_failure':CLI_FAILURE}),flush=True)
finally:
 last={'completed':False}
 if OPEN:
  try:cli(['set','offline','off'],check=False);last=logout() if ev('''!!localStorage.getItem('nodesteer_token')''') else {'had_token':False,'old_me':None};clear_network=cli(['network','requests','--clear'],check=False)
  except Exception:last={'completed':False}
  try:cli(['close'],check=False)
  except Exception:pass
  OPEN=False
 if C:
  try:C.close()
  except Exception:pass
 if T:
  try:
   guarded=T.poll() is None and procstart(T.pid)==TICKS and hashlib.sha256(b'\0'.join(argv(T.pid))).hexdigest()==AH
   if guarded:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
   ls=listeners();cleanup={'ssh_pid':T.pid,'ssh_startticks':TICKS,'argv_sha256':AH,'guard_matched':guarded,'stopped':T.poll() is not None,'listener_after_stop':ls,'listener_gone':ls['count']==0}
  except Exception:cleanup={'guard_matched':False,'stopped':False,'listener_gone':False}
 else:cleanup={'guard_matched':False,'stopped':True,'listener_gone':True}
 cleanup['dedicated_logout']=last;cleanup['captured_at_utc']=utc();save('phase1-cleanup.json',cleanup)
 p=json.loads((R/'progress.json').read_text());p.update({'phase1_cleanup':cleanup,'updated_at_utc':utc(),'browser_open':False,'tunnel_started':False,'phase1_cleanup_complete':cleanup['stopped'] and cleanup['listener_gone'] and ((last.get('old_me')==401) if last.get('had_token') else True),'phase1_complete':p.get('phase')=='PHASE1_COMPLETE'});(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase1_tunnel_stopped':cleanup['stopped'],'listener_gone':cleanup['listener_gone'],'logout_old_me_401':last.get('old_me')==401 if last.get('had_token') else None}),flush=True)
