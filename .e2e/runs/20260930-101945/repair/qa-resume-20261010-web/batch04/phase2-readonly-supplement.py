import datetime,hashlib,json,os,re,signal,subprocess,time,urllib.request
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect

R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04');ORIGIN='http://127.0.0.1:38181';SESSION='qawebsecure20261010-b04-readonly-final-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
T=None;TICKS=None;AH=None;OPEN=False;C=None;STAGE='START';LAST_CLI=None;LAST_EVAL=None;NODE_BEFORE=None
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
  xs=self.call('Target.getTargets').get('targetInfos',[]);ps=[x for x in xs if x.get('type')=='page'];allowed=lambda u:u.startswith(ORIGIN) or u in ('about:blank','data:,','chrome://newtab/')
  if not ps or any(not allowed(x.get('url','')) for x in ps):raise E('foreign target guard failed')
  p=next((x for x in ps if x.get('url','').startswith(ORIGIN)),None)
  if not p:raise E('repair target absent')
  self.sid=self.call('Target.attachToTarget',{'targetId':p['targetId'],'flatten':True}).get('sessionId');return {'page_count':len(ps),'all_owned_or_blank':True}
 def insert(self,s):self.call('Input.insertText',{'text':s},s=self.sid)
 def close(self):self.ws.close()
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def progress(ph,**kw):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':ph,'updated_at_utc':utc(),'browser_open':OPEN,'tunnel_started':bool(T and T.poll() is None),'private_values_saved':False,'raw_auth_or_api_body_saved':False});p.update(kw);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':ph,'stage':STAGE,'browser_open':OPEN}),flush=True)
def cli(a,stdin=None,timeout=25,check=True):
 global LAST_CLI
 p=subprocess.run(AB+[str(x) for x in a],input=stdin,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:
  raw=p.stdout+'\n'+p.stderr
  try:
   for fp in (CREDS,SHARED):
    if fp.is_file():
     for v in json.loads(fp.read_text()).values():
      if isinstance(v,str) and v:raw=raw.replace(v,'[FILTERED]')
  except Exception:pass
  m=raw.lower();ec='other'
  for q,n in [('syntaxerror','javascript_syntax'),('strict mode','strict_duplicate'),('multiple elements','strict_duplicate'),('not found','not_found'),('not visible','not_visible'),('timeout','timeout'),('unsupported','unsupported')]:
   if q in m:ec=n;break
  action=(str(a[1]) if len(a)>1 and a[0]=='--json' else str(a[0])) if a else 'unknown';LAST_CLI={'action':action,'stage':STAGE,'returncode':p.returncode,'error_class':ec,'output_saved':False};raise E('agent-browser action failed; output suppressed')
 return p.returncode,p.stdout
def ev(js):
 global LAST_EVAL
 LAST_EVAL={'stage':STAGE,'length':len(js),'sha256':hashlib.sha256(js.encode()).hexdigest()};_,o=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(o)['data']['result']
 except Exception:raise E('filtered eval result unavailable')
def clear_network():
 rc,_=cli(['network','requests','--clear'],check=False)
 if rc:raise E('network clear failed')
def network():
 rc,o=cli(['--json','network','requests'],check=False);a=[]
 if rc:return a
 try:raw=json.loads(o).get('data',{}).get('requests',[])
 except Exception:return a
 for x in raw:
  u=x.get('url') if isinstance(x,dict) else None
  if isinstance(u,str):
   v=urlparse(u);a.append({'path':v.path,'local':v.netloc=='127.0.0.1:38181','method':x.get('method'),'status':x.get('status') if isinstance(x.get('status'),int) else None,'failed':bool(x.get('failureText') or x.get('error'))})
 return a
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
  q=l.split(None,1)
  if len(q)==2 and q[0] in d:d[q[0]].append(q[1])
 assert not d['localforward'] and not d['remoteforward'] and all(x=='none' for x in d['remotecommand']) and all(x!='yes' for x in d['forwardagent']) and not d['controlpath']
 q=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not q.stdout.strip()
 assert CREDS.is_file() and SHARED.is_file() and not(CREDS.stat().st_mode&0o077)
 return {'extra_forward_count':0,'port_free':True,'credentials_private_file':True}
def login():
 cli(['open',ORIGIN+'/sign-in']);cli(['wait','--load','networkidle']);cr=json.loads(CREDS.read_text());sh=json.loads(SHARED.read_text())['token'];payload=json.dumps({'username':cr['admin_username'],'password':cr['admin_password']})
 # API login response token is kept in browser localStorage only; eval returns booleans/status, never token/body.
 js="(async(c,s)=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});let d={};try{d=await r.json()}catch{};const t=d.token??d.Token;if(typeof t==='string'&&t)localStorage.setItem('nodesteer_token',t);return {http:r.status,token:typeof t==='string'&&!!t,shared:typeof t==='string'&&t===s,local:location.origin==="+json.dumps(ORIGIN)+"}})("+payload+","+json.dumps(sh)+")"
 m=ev(js)
 if m['http']!=200 or not m['token'] or m['shared'] or not m['local']:raise E('private API login isolation guard failed')
 return {'http':m['http'],'token_present':m['token'],'shared':m['shared'],'local':m['local']}
def nodes():
 return ev('''(async()=>{try{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),d=await r.json(),a=Array.isArray(d)?d:[];return {http:r.status,nodes:a.map(n=>({id:n.id||null,agent_id:n.agent_id||n.agentId||null,hostname:n.hostname||null,labels:n.labels||{}})).sort((x,y)=>String(x.id).localeCompare(String(y.id)))}}catch(e){return {http:0,error:e?.name||'error',nodes:[]}}})()''')
def waitpath(path,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   if ev('''({o:location.origin,p:location.pathname})''')=={'o':ORIGIN,'p':path}:return True
  except Exception:pass
  time.sleep(.15)
 return False
def click_href(href):
 return ev('''(h=>{const sel=`a[data-slot="sidebar-menu-button"][href="${h}"]`,a=[...document.querySelectorAll(sel)];const xs=a.map((e,i)=>({i,label:(e.innerText||'').trim(),visible:!!e.getClientRects().length})).filter(x=>x.visible&&x.label);return {sel,items:xs}})('''+json.dumps(href)+''')''')
def actual_click_href(href):
 d=click_href(href)
 if d['items']:cli(['find','nth',str(d['items'][0]['i']),d['sel'],'click']);return d['items'][0]['label']
 # Exact route fallback, using DOM-indexed locator, never global text matching.
 d=ev('''(h=>{const sel=`a[href="${h}"]`,a=[...document.querySelectorAll(sel)];return {sel,items:a.map((e,i)=>({i,label:(e.innerText||'').trim(),visible:!!e.getClientRects().length})).filter(x=>x.visible&&x.label)}})('''+json.dumps(href)+''')''')
 if not d['items']:raise E('visible exact route anchor absent '+href)
 cli(['find','nth',str(d['items'][0]['i']),d['sel'],'click']);return d['items'][0]['label']
def wait_groups_ready(secs=12):
 end=time.monotonic()+secs
 while time.monotonic()<end:
  try:
   x=ev('''(()=>{const m=document.querySelector('main'),txt=m?.innerText||'';return {path:location.pathname,heading:!!m?.querySelector('h1'),table:!!m?.querySelector('table'),search:[...document.querySelectorAll('main input[placeholder]')].map(i=>i.placeholder),alert:!!m?.querySelector('[role="alert"]'),skeleton:!!m?.querySelector('[aria-busy="true"]'),emptyState:/no data|暂无数据/i.test(txt)}})()''')
   if x['path']=='/groups' and x['heading'] and (x['table'] or x['alert'] or x['skeleton'] or x['emptyState']):return x
  except Exception:pass
  time.sleep(.2)
 return ev('''(()=>{const m=document.querySelector('main'),txt=m?.innerText||'';return {path:location.pathname,heading:!!m?.querySelector('h1'),table:!!m?.querySelector('table'),search:[...document.querySelectorAll('main input[placeholder]')].map(i=>i.placeholder),alert:!!m?.querySelector('[role="alert"]'),skeleton:!!m?.querySelector('[aria-busy="true"]'),emptyState:/no data|暂无数据/i.test(txt)}})()''')
def write_observer():
 ev('''(()=>{if(window.__qaB04FetchObserver)return true;window.__qaB04FetchObserver=true;if(!sessionStorage.getItem('qa_b04_writes'))sessionStorage.setItem('qa_b04_writes','[]');const base=window.fetch.bind(window);window.fetch=async(input,init={})=>{const method=String(init.method||input?.method||'GET').toUpperCase(),url=typeof input==='string'?input:input.url,r=await base(input,init);if(!['GET','HEAD','OPTIONS'].includes(method)){const x=new URL(url,location.href);const a=JSON.parse(sessionStorage.getItem('qa_b04_writes')||'[]');a.push({method,path:x.pathname,status:r.status});sessionStorage.setItem('qa_b04_writes',JSON.stringify(a))}return r};return true})()''')
def writes():return ev('''JSON.parse(sessionStorage.getItem('qa_b04_writes')||'[]')''')

try:
 STAGE='RO_PREFLIGHT';pf=preflight();save('phase2-readonly-preflight.json',{'captured_at_utc':utc(),**pf});progress('P2_READONLY_PREFLIGHT',preflight=pf)
 AH=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();T=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);TICKS=procstart(T.pid);ready=False
 for _ in range(60):
  if T.poll() is not None:raise E('owned forward exited')
  z=listeners()
  if z['count']==1 and z['listeners'][0]['loopback'] and z['listeners'][0]['owned']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as r:ready=200<=r.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise E('loopback forward not ready')
 progress('P2_READONLY_TUNNEL_READY',ssh_pid=T.pid,ssh_startticks=TICKS,listener=listeners())
 STAGE='RO_BROWSER';cli(['open',ORIGIN+'/sign-in']);OPEN=True;cli(['wait','--load','networkidle']);u=cli(['get','cdp-url'])[1].strip()
 if not u.startswith('ws://127.0.0.1:'):raise E('CDP endpoint not loopback')
 C=CDP(u);guard=C.attach();STAGE='RO_LOGIN';loginmeta=login();cli(['open',ORIGIN+'/groups']);gready=wait_groups_ready();write_observer();clear_network()
 route_state=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},[m,g]=await Promise.all([fetch('/api/me',{headers:h}),fetch('/api/groups',{headers:h})]);let d=[];try{d=await g.json()}catch{};const main=document.querySelector('main'),txt=main?.innerText||'';return {path:location.pathname,origin:location.origin,me_http:m.status,groups_http:g.status,groups_count:Array.isArray(d)?d.length:null,table:!!main?.querySelector('table'),main_inputs:main?.querySelectorAll('input').length||0,alert:!!main?.querySelector('[role="alert"]'),skeleton:!!main?.querySelector('[aria-busy="true"]'),empty_state:/no data|暂无数据/i.test(txt)}})()''');save('phase2-readonly-first-protected-route.json',{'captured_at_utc':utc(),'login':loginmeta,'browser_target_guard':guard,'groups_ready':gready,'route_state':route_state,'network_meta':network()})
 if gready['path']!='/groups' or not (gready['table'] or gready['alert'] or gready['skeleton'] or gready['emptyState']):raise E('first protected Groups route did not hydrate')
 original=json.loads((R/'phase2-node-stable-before.json').read_text());NODE_BEFORE=nodes();before_same=NODE_BEFORE['http']==200 and NODE_BEFORE['nodes']==original['nodes'];save('phase2-node-stable-current-before.json',{'captured_at_utc':utc(),'http':NODE_BEFORE['http'],'node_count':len(NODE_BEFORE['nodes']),'matches_prior_phase_baseline':before_same,'nodes':NODE_BEFORE['nodes']});progress('P2_READONLY_READY',login=loginmeta,target_guard=guard,node_stable_matches_prior=before_same)

 # P03-10: Groups was the first protected route, so Dashboard query cache is cold.
 STAGE='P03-10_RECHECK';clear_network();cli(['set','offline','on']);actual_click_href('/');dashboard=waitpath('/');deadline=time.monotonic()+18;derr=False
 while time.monotonic()<deadline:
  derr=ev('''!!document.querySelector('[role="alert"]')''')
  if derr:break
  time.sleep(.25)
 dretry=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');dnet=network();dclick=False
 if dretry:cli(['find','role','button','click','--name',dretry[0],'--exact']);dclick=True;time.sleep(.5)
 cli(['set','offline','off']);dretry2=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');drestore=False
 if dretry2:
  cli(['find','role','button','click','--name',dretry2[0],'--exact']);end=time.monotonic()+15
  while time.monotonic()<end:
   if not ev('''!!document.querySelector('[role="alert"]')'''):drestore=True;break
   time.sleep(.2)
 direct_offline=ev('''(async()=>{try{const r=await fetch('/api/nodes');return {ok:r.ok,status:r.status,error:null}}catch(e){return {ok:false,status:null,error:e?.name||'error'}}})()''');p310={'case':'P03-10','status':'PASS' if dashboard and derr and dretry and dclick and drestore and direct_offline['error'] else 'PARTIAL','dashboard_route':dashboard,'offline_error_alert':derr,'retry_button_present':bool(dretry),'retry_clicked_while_offline':dclick,'retry_recovered_online':drestore,'direct_api_offline':direct_offline,'offline_requests':dnet,'online_retry_requests':network(),'cold_dashboard_cache':True};save('case-p03-10.json',p310);progress('P03-10_READONLY_COMPLETE',status=p310['status'])

 # P06-01: existing dataset only, actual Groups table/search with conditional pagination; no fixture creation.
 STAGE='P06-01_GROUPS_LOAD';cli(['set','offline','off']);actual_click_href('/groups');gready=wait_groups_ready();gapi=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups',{headers:{Authorization:'Bearer '+t}}),d=await r.json();return {http:r.status,count:Array.isArray(d)?d.length:0,names:Array.isArray(d)?d.map(g=>g.name):[]}})()''')
 if not gready['table'] and not gready['emptyState']:raise E('Groups list is neither table nor explicit empty state')
 placeholder=gready['search'][0] if gready['search'] else None;sel='main input[placeholder='+json.dumps(placeholder)+']' if placeholder else None;rowbase=ev('''document.querySelectorAll('main tbody tr').length''');target_name=gapi['names'][0] if gapi['names'] else None;searchhit=None;empty={'rows':rowbase,'empty':False};rows_reset=rowbase
 if sel:
  if target_name:
   cli(['fill',sel,target_name]);time.sleep(.4);searchhit=ev('''(n=>[...document.querySelectorAll('main tbody tr')].some(r=>(r.innerText||'').includes(n)))('''+json.dumps(target_name)+''')''')
  no_query='qa-b04-no-match-'+str(int(time.time()));cli(['fill',sel,no_query]);time.sleep(.3);empty=ev('''(()=>({rows:document.querySelectorAll('main tbody tr').length,empty:/no results|未找到|暂无数据/i.test(document.querySelector('main')?.innerText||'')}))()''');cli(['fill',sel,'']);time.sleep(.3);rows_reset=ev('''document.querySelectorAll('main tbody tr').length''')
 pages={'applicable':gapi['count']>10,'next_clicked':False,'next_page_rows':None,'previous_clicked':False,'restored_page_rows':None,'page_size_changed':False,'page_size_rows':None}
 if pages['applicable']:
  labels=ev('''[...document.querySelectorAll('main button')].map((b,i)=>({i,text:(b.innerText||'').trim(),disabled:b.disabled})).filter(x=>/next|下一页/i.test(x.text))''')
  if labels and not labels[0]['disabled']:
   cli(['find','nth',str(labels[0]['i']),'main button','click']);pages['next_clicked']=True;pages['next_page_rows']=ev('''document.querySelectorAll('main tbody tr').length''');prev=ev('''[...document.querySelectorAll('main button')].map((b,i)=>({i,text:(b.innerText||'').trim(),disabled:b.disabled})).filter(x=>/previous|上一页/i.test(x.text))''')
   if prev and not prev[0]['disabled']:
    cli(['find','nth',str(prev[0]['i']),'main button','click']);pages['previous_clicked']=True;pages['restored_page_rows']=ev('''document.querySelectorAll('main tbody tr').length''')
  combos=ev('''[...document.querySelectorAll('main [role="combobox"]')].length''')
  if combos==1:
   cli(['click','main [role="combobox"]']);ops=ev('''[...document.querySelectorAll('[role="option"]')].map(x=>(x.innerText||'').trim())''')
   if '20' in ops:
    cli(['find','role','option','click','--name','20','--exact']);time.sleep(.25);pages['page_size_changed']=True;pages['page_size_rows']=ev('''document.querySelectorAll('main tbody tr').length''');cli(['click','main [role="combobox"]']);cli(['find','role','option','click','--name','10','--exact'])
 p601={'case':'P06-01','status':'PARTIAL','groups_get_http':gapi['http'],'api_group_count':gapi['count'],'visible_table_rows':rowbase,'search_input_count':len(gready['search']),'search_placeholder':placeholder,'existing_group_search_hit':searchhit,'empty_search_rows':empty['rows'],'empty_search_feedback':empty['empty'],'restored_rows':rows_reset,'pagination':pages,'pagination_condition':'Applicable because API count >10.' if pages['applicable'] else 'Not applicable because API count <=10.'};save('case-p06-01-search.json',p601);progress('P06-01_SEARCH_COMPLETE',status='IN_PROGRESS',group_count=gapi['count'])

 # P06-01 actual Group GET failure/retry after staleness; this uses only browser network isolation.
 STAGE='P06-01_OFFLINE_RETRY';time.sleep(11);clear_network();cli(['set','offline','on']);actual_click_href('/tasks');waitpath('/tasks');actual_click_href('/groups');groute=wait_groups_ready();deadline=time.monotonic()+15;gerr=False
 while time.monotonic()<deadline:
  gerr=ev('''!!document.querySelector('[role="alert"]')''')
  if gerr:break
  time.sleep(.25)
 gretry=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');gnet=network();grfail=False
 if gretry:cli(['find','role','button','click','--name',gretry[0],'--exact']);grfail=True;time.sleep(.4)
 cli(['set','offline','off']);gretry2=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');grrecovered=False
 if gretry2:
  cli(['find','role','button','click','--name',gretry2[0],'--exact']);end=time.monotonic()+15
  while time.monotonic()<end:
   if not ev('''!!document.querySelector('[role="alert"]')'''):grrecovered=True;break
   time.sleep(.2)
 gget=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups',{headers:{Authorization:'Bearer '+t}});return {http:r.status}})()''');p601.update({'status':'PASS' if placeholder and not pages['applicable'] and (searchhit is None or searchhit) and empty['empty'] and gerr and gretry and grfail and grrecovered and gget['http']==200 else 'PARTIAL','offline_route':groute['path']=='/groups' and (groute['table'] or groute['alert'] or groute['skeleton'] or groute['emptyState']),'offline_ui_state':{'table':groute['table'],'alert':groute['alert'],'skeleton':groute['skeleton'],'empty_state':groute['emptyState']},'offline_error_alert':gerr,'offline_retry_button':bool(gretry),'offline_retry_clicked':grfail,'online_retry_recovered':grrecovered,'online_groups_get_http':gget['http'],'offline_request_meta':gnet});save('case-p06-01.json',p601);progress('P06-01_COMPLETE',status=p601['status'])

 # P06-09 Cancel only: open real editor, click its exact main Cancel link, assert zero POST.
 STAGE='P06-09_CANCEL';cli(['set','offline','off']);clear_network();before=writes();cli(['open',ORIGIN+'/groups/new'])
 def wait_cancel_ready():
  end=time.monotonic()+12;x={}
  while time.monotonic()<end:
   x=ev('''(()=>({p:location.pathname,inputs:document.querySelectorAll('main input').length,links:[...document.querySelectorAll('main a[href="/groups"]')].map((a,i)=>({i,text:(a.innerText||'').trim(),visible:!!a.getClientRects().length}))}))()''')
   if x['p']=='/groups/new' and x['inputs']>=2 and any(a['visible'] for a in x['links']):return x
   time.sleep(.2)
  return x
 cancel_dom=wait_cancel_ready();can=[a for a in cancel_dom.get('links',[]) if a['visible'] and re.fullmatch(r'cancel|取消',a['text'],re.I)]
 if len(can)==1:
  write_observer();cli(['find','nth',str(can[0]['i']),'main a[href="/groups"]','click']);cancel_back=waitpath('/groups')
 else:cancel_back=False
 after=writes();cancelposts=[x for x in after[len(before):] if x.get('method')=='POST' and x.get('path')=='/api/groups'];p609={'case':'P06-09','status':'PASS' if cancel_back and len(cancelposts)==0 else 'PARTIAL','new_group_editor_visible':cancel_dom.get('p')=='/groups/new' and cancel_dom.get('inputs',0)>=2,'cancel_anchor_count':len(can),'cancel_returned_to_groups':cancel_back,'group_post_count':len(cancelposts),'group_write_records':after[len(before):],'cancel_network_requests':[x for x in network() if x.get('path')=='/api/groups']};save('case-p06-09-cancel.json',p609);progress('P06-09_CANCEL_COMPLETE',status=p609['status'])

 after_nodes=nodes();same=after_nodes['http']==200 and NODE_BEFORE['http']==200 and after_nodes['nodes']==original['nodes'];save('phase2-node-stable-current-after.json',{'captured_at_utc':utc(),'http':after_nodes['http'],'node_count':len(after_nodes['nodes']),'matches_prior_phase_baseline':same,'nodes':after_nodes['nodes']})
 appwrites=writes();unexpected=[x for x in appwrites if x.get('method') not in ('GET','HEAD','OPTIONS')];save('phase2-readonly-write-ledger.json',{'captured_at_utc':utc(),'writes':appwrites,'unexpected_write_count':len(unexpected),'write_bodies_saved':False})
 p601['node_stable_identity_labels_unchanged']=same;p601['node_before']='phase2-node-stable-before.json';p601['node_after']='phase2-node-stable-current-after.json';save('case-p06-01.json',p601)
 progress('READONLY_SUPPLEMENT_COMPLETE',status='PARTIAL',node_stable_unchanged=same,write_count=len(unexpected));
except Exception as e:
 diag={'captured_at_utc':utc(),'stage':STAGE,'type':type(e).__name__,'message':str(e)[:160] if isinstance(e,E) else 'exception redacted','cli_failure':LAST_CLI,'eval_fingerprint':LAST_EVAL,'raw_output_saved':False,'private_values_saved':False};save('phase2-readonly-error.json',diag);progress('READONLY_SUPPLEMENT_ERROR',status='PARTIAL',error=diag);print(json.dumps({'phase':'READONLY_SUPPLEMENT_ERROR','stage':STAGE,'type':type(e).__name__}),flush=True)
finally:
 logout={'had_token':False,'post':None,'old_me':None,'removed':False}
 if OPEN:
  try:
   cli(['set','offline','off'],check=False);logout=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let p=0,m=0;if(t){p=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;m=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,post:p,old_me:m,removed:!localStorage.getItem('nodesteer_token')}})()''');clear_network();cli(['close'],check=False)
  except Exception:pass
  OPEN=False
 if C:
  try:C.close()
  except Exception:pass
 if T:
  try:
   guarded=T.poll() is None and procstart(T.pid)==TICKS and hashlib.sha256(b'\0'.join(argv(T.pid))).hexdigest()==AH
   if guarded:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
   ls=listeners();tunnel={'guard_matched':guarded,'stopped':T.poll() is not None,'listener_gone':ls['count']==0,'listener_after_stop':ls}
  except Exception:tunnel={'guard_matched':False,'stopped':False,'listener_gone':False}
 else:tunnel={'guard_matched':False,'stopped':True,'listener_gone':True}
 clean={'captured_at_utc':utc(),'dedicated_logout':logout,'browser_closed':not OPEN,'tunnel':tunnel};save('phase2-readonly-cleanup.json',clean)
 try:
  p=json.loads((R/'progress.json').read_text());p.update({'updated_at_utc':utc(),'browser_open':False,'tunnel_started':False,'readonly_cleanup':clean,'readonly_logout_old_me_401':logout.get('old_me')==401,'readonly_listener_gone':tunnel.get('listener_gone')});(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 except Exception:pass
 print(json.dumps({'logout_old_me_401':logout.get('old_me')==401,'tunnel_stopped':tunnel.get('stopped'),'listener_gone':tunnel.get('listener_gone')},ensure_ascii=False),flush=True)
