import datetime,hashlib,json,os,re,signal,subprocess,time,urllib.request,uuid
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect

R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04');ORIGIN='http://127.0.0.1:38181';SESSION='qawebsecure20261010-b04-group-retry-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
T=None;TICKS=None;AH=None;OPEN=False;C=None;NODE_BASE=None;GROUP_ID=None;GROUP_NAME=None;GROUP_DELETED=False;STAGE='START';LAST_CLI=None;LAST_EVAL=None;WRITES=[];SESSION_CLEAN=False

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
  xs=self.call('Target.getTargets').get('targetInfos',[]);ps=[x for x in xs if x.get('type')=='page'];
  allowed=lambda u: u.startswith(ORIGIN) or u in ('about:blank','data:,','chrome://newtab/')
  if not ps or any(not allowed(x.get('url','')) for x in ps):raise E('foreign page target guard failed')
  p=next((x for x in ps if x.get('url','').startswith(ORIGIN)),None)
  if not p:raise E('repair page target absent')
  self.sid=self.call('Target.attachToTarget',{'targetId':p['targetId'],'flatten':True}).get('sessionId');return {'page_count':len(ps),'all_pages_owned_or_blank':True}
 def insert(self,s):self.call('Input.insertText',{'text':s},s=self.sid)
 def close(self):self.ws.close()

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def prog(ph,**kw):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':ph,'updated_at_utc':utc(),'executor':'gpt-6-luna','browser_open':OPEN,'tunnel_started':bool(T and T.poll() is None),'private_values_saved':False,'raw_auth_or_api_body_saved':False});p.update(kw);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':ph,'stage':STAGE,'browser_open':OPEN}),flush=True)
def cli(a,stdin=None,timeout=25,check=True):
 global LAST_CLI
 p=subprocess.run(AB+[str(z) for z in a],input=stdin,capture_output=True,text=True,timeout=timeout)
 if check and p.returncode:
  raw=p.stdout+'\n'+p.stderr
  try:
   for fp in (CREDS,SHARED):
    if fp.is_file():
     for val in json.loads(fp.read_text()).values():
      if isinstance(val,str) and val:raw=raw.replace(val,'[FILTERED]')
  except Exception:pass
  m=raw.lower();ec='other'
  for q,n in [('syntaxerror','javascript_syntax'),('unexpected token','javascript_syntax'),('json parse','json_parse'),('strict mode','strict_duplicate'),('multiple elements','strict_duplicate'),('not found','not_found'),('not visible','not_visible'),('not clickable','not_actionable'),('timeout','timeout'),('timed out','timeout'),('blocked','blocked'),('unsupported','unsupported')]:
   if q in m:ec=n;break
  line=re.search(r'line\s+(\d+)',m);act=(str(a[1]) if len(a)>1 and a[0]=='--json' else str(a[0])) if a else 'unknown';LAST_CLI={'action':act,'stage':STAGE,'returncode':p.returncode,'error_class':ec,'error_line':int(line.group(1)) if line else None,'output_saved':False};raise E('agent-browser action failed; output suppressed')
 return p.returncode,p.stdout
def ev(js):
 global LAST_EVAL
 LAST_EVAL={'stage':STAGE,'javascript_length':len(js),'javascript_sha256':hashlib.sha256(js.encode()).hexdigest()}
 _,o=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(o)['data']['result']
 except Exception:raise E('filtered eval result unavailable')
def clear_network():
 rc,_=cli(['network','requests','--clear'],check=False)
 if rc:raise E('network buffer clear failed')
def network():
 rc,o=cli(['--json','network','requests'],check=False);a=[]
 if rc:return a
 try:raw=json.loads(o).get('data',{}).get('requests',[])
 except Exception:return a
 for x in raw:
  u=x.get('url') if isinstance(x,dict) else None
  if not isinstance(u,str):continue
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
  z=l.split(None,1)
  if len(z)==2 and z[0] in d:d[z[0]].append(z[1])
 assert not d['localforward'] and not d['remoteforward'] and all(x=='none' for x in d['remotecommand']) and all(x!='yes' for x in d['forwardagent']) and not d['controlpath']
 q=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not q.stdout.strip()
 assert CREDS.is_file() and SHARED.is_file() and not(CREDS.stat().st_mode&0o077)
 return {'extra_forward_count':0,'port_free':True,'credentials_private_file':True}
def stable_nodes():
 return ev('''(async()=>{try{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}});let d;try{d=await r.json()}catch(e){return {http:r.status,parse_error:e?.name||'error',nodes:[]}};const a=Array.isArray(d)?d:[];return {http:r.status,nodes:a.map(n=>({id:n.id||null,agent_id:n.agent_id||n.agentId||null,hostname:n.hostname||null,labels:n.labels||{}})).sort((x,y)=>String(x.id).localeCompare(String(y.id)))}}catch(e){return {http:0,fetch_error:e?.name||'error',nodes:[]}}})()''')
def waitpath(path,seconds=12):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   x=ev('''({o:location.origin,p:location.pathname})''')
   if x=={'o':ORIGIN,'p':path}:return True
  except Exception:pass
  time.sleep(.15)
 return False
def label_for_href(href):
 return ev('''(h=>[...document.querySelectorAll('a[href]')].filter(x=>x.getAttribute('href')===h&&x.getClientRects().length&&!!(x.innerText||'').trim()).map(x=>(x.innerText||'').trim()))('''+json.dumps(href)+''')''')
def click_link(href):
 return ev('''(h=>{const sel=`a[data-slot="sidebar-menu-button"][href="${h}"]`,all=[...document.querySelectorAll(sel)],vis=all.map((a,i)=>({i,text:(a.innerText||'').trim(),visible:!!a.getClientRects().length,href:a.getAttribute('href')})).filter(x=>x.visible&&x.text);return {selector:sel,count:all.length,visible:vis}})('''+json.dumps(href)+''')''')
 nav=click_link(href)
 if nav['visible']:
  cli(['find','nth',str(nav['visible'][0]['i']),nav['selector'],'click']);return nav['visible'][0]['text']
 labels=label_for_href(href)
 if not labels:raise E('visible route link absent '+href)
 candidates=ev('''(h=>{const sel=`a[href="${h}"]`,all=[...document.querySelectorAll(sel)];return all.map((a,i)=>({i,text:(a.innerText||'').trim(),visible:!!a.getClientRects().length})).filter(x=>x.visible&&x.text)})('''+json.dumps(href)+''')''')
 if not candidates:raise E('visible exact route anchor absent '+href)
 cli(['find','nth',str(candidates[0]['i']),f'a[href="{href}"]','click']);return candidates[0]['text']
def login():
 cli(['open',ORIGIN+'/sign-in']);cli(['wait','--load','networkidle']);cr=json.loads(CREDS.read_text());cli(['focus','input[name="username"]']);C.insert(cr['admin_username']);cli(['focus','input[name="password"]']);C.insert(cr['admin_password']);clear_network();cli(['click','button[type="submit"]'],timeout=30)
 if not waitpath('/'):raise E('fresh UI login did not reach dashboard')
 sh=json.loads(SHARED.read_text())['token'];m=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {http:r.status,token:!!t,shared:!!t&&t==='''+json.dumps(sh)+''',origin:location.origin}})()''')
 if m['http']!=200 or not m['token'] or m['shared'] or m['origin']!=ORIGIN:raise E('fresh dedicated login guard failed')
 return {'http':m['http'],'token_present':m['token'],'token_shared':m['shared'],'origin_local':m['origin']==ORIGIN}
def install_write_observer():
 ev('''(()=>{if(window.__qaB04FetchObserver)return true;window.__qaB04FetchObserver=true;if(!sessionStorage.getItem('qa_b04_writes'))sessionStorage.setItem('qa_b04_writes','[]');const base=window.fetch.bind(window);window.fetch=async(input,init={})=>{const method=String(init.method||input?.method||'GET').toUpperCase(),url=typeof input==='string'?input:input.url;const response=await base(input,init);if(!['GET','HEAD','OPTIONS'].includes(method)){const u=new URL(url,location.href);const item={method,path:u.pathname,status:response.status};if(method==='POST'&&u.pathname==='/api/groups'){try{const d=await response.clone().json();item.id=typeof d.id==='string'?d.id:null}catch{item.id=null}}const xs=JSON.parse(sessionStorage.getItem('qa_b04_writes')||'[]');xs.push(item);sessionStorage.setItem('qa_b04_writes',JSON.stringify(xs))}return response};return true})()''')
def writes():return ev('''JSON.parse(sessionStorage.getItem('qa_b04_writes')||'[]')''')
def group_api(gid=None):
 return ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},u=id?'/api/groups/'+encodeURIComponent(id):'/api/groups',r=await fetch(u,{headers:h});let d;try{d=await r.json()}catch{};if(!id){const a=Array.isArray(d)?d:[];return {http:r.status,groups:a.map(x=>({id:x.id,name:x.name,type:x.type,members:Array.isArray(x.members)?x.members.length:0}))}}return {http:r.status,id:d?.id||null,name:d?.name||null,type:d?.type||null,members:Array.isArray(d?.members)?d.members.length:0,label_key:typeof d?.label_key==='string'?d.label_key:null,label_value:typeof d?.label_value==='string'?d.label_value:null}})('''+json.dumps(gid)+''')''')
def cleanup_group():
 global GROUP_DELETED
 if not GROUP_ID:return {'id_known':False,'deleted':False,'not_found':None}
 try:
  cli(['set','offline','off'],check=False)
  before=group_api(GROUP_ID)
  if before['http']==404:GROUP_DELETED=True;return {'id_known':True,'delete_http':None,'deleted':True,'verify_http':404,'not_found':True}
  if not GROUP_DELETED:
   d=ev('''(async id=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/groups/'+encodeURIComponent(id),{method:'DELETE',headers:{Authorization:'Bearer '+t}});return {http:r.status}})('''+json.dumps(GROUP_ID)+''')''')
   GROUP_DELETED=d['http'] in (200,204)
  g=group_api(GROUP_ID);return {'id_known':True,'delete_http':d.get('http') if 'd' in locals() else None,'deleted':GROUP_DELETED,'verify_http':g['http'],'not_found':g['http']==404}
 except Exception:return {'id_known':True,'deleted':False,'not_found':False}

try:
 STAGE='P2_PREFLIGHT';pf=preflight();save('phase2-preflight.json',{'captured_at_utc':utc(),**pf});prog('P2_PREFLIGHT',preflight=pf)
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
 prog('P2_TUNNEL_READY',ssh_pid=T.pid,ssh_startticks=TICKS,ssh_argv_sha256=AH,listener=listeners())
 STAGE='P2_BROWSER';cli(['open',ORIGIN+'/sign-in']);OPEN=True;cli(['wait','--load','networkidle']);u=cli(['get','cdp-url'])[1].strip()
 if not u.startswith('ws://127.0.0.1:'):raise E('CDP endpoint not loopback')
 C=CDP(u);target=C.attach();STAGE='P2_UI_LOGIN';login_meta=login();STAGE='P2_NODE_BASELINE';NODE_BASE=stable_nodes()
 if NODE_BASE['http']!=200:raise E('Node stable baseline GET failed')
 save('phase2-node-stable-before.json',{'captured_at_utc':utc(),'http':NODE_BASE['http'],'node_count':len(NODE_BASE['nodes']),'nodes':NODE_BASE['nodes']})
 install_write_observer();clear_network();STAGE='P2_READY_METADATA';prog('P2_READY',login=login_meta,browser={'origin_local':login_meta['origin_local'],'page_guard':target},node_count=len(NODE_BASE['nodes']),node_stable_baseline='phase2-node-stable-before.json')

 # P02-01: capture the real visible sidebar href/labels and roles without repeating unrecorded route clicks.
 STAGE='P02-01_SIDEBAR_SNAPSHOT';sidebar=ev('''(()=>{const rs=new Set(['/','/tasks','/schedules','/executions','/transfers','/agents','/groups','/scripts','/applications','/audit','/users','/settings']);return [...document.querySelectorAll('a[href]')].filter(a=>rs.has(a.getAttribute('href'))&&a.getClientRects().length).map(a=>({href:a.getAttribute('href'),label:(a.innerText||'').trim(),role:a.getAttribute('role'),sidebarButton:a.getAttribute('data-slot')==='sidebar-menu-button',withinSidebar:!!a.closest('[data-slot="sidebar"]')}))})()''')
 unique={x['href'] for x in sidebar if x['sidebarButton'] or x['withinSidebar']};p201={'case':'P02-01','status':'PARTIAL','visible_sidebar_route_count':len(unique),'visible_sidebar_routes':sorted(unique),'entries':sidebar,'previous_11_click_oracles_missing':True,'settings_route_rechecked_in_P02_02':True,'artifacts_indirect_route_reached_previous_phase':True,'write_requests':0};save('case-p02-01-snapshot.json',p201);prog('P02-01_SNAPSHOT',status='PARTIAL',visible_sidebar_route_count=len(unique))

 # P02-05 and P03-10: true session-scoped offline transition, header health indicator, dashboard error/retry.
 STAGE='P02-05_ONLINE_BASELINE';click_link('/');waitpath('/');
 hb=ev('''(async()=>{const r=await fetch('/healthz',{cache:'no-store'});return {status:r.status,ok:r.ok}})()''');time.sleep(.2)
 header=lambda:ev('''(()=>{const h=document.querySelector('header');return {text:(h?.innerText||'').trim(),destructiveCount:h?.querySelectorAll('.bg-destructive').length||0,onlineCount:h?.querySelectorAll('.bg-emerald-500').length||0}})()''')
 online0=header();prog('P02-05_ONLINE_BASELINE',healthz=hb,header=online0)
 STAGE='P02-05_OFFLINE';clear_network();cli(['set','offline','on']);deadline=time.monotonic()+22;offline_header=header()
 while time.monotonic()<deadline:
  offline_header=header()
  if '不可用' in offline_header['text'] or 'unavailable' in offline_header['text'].lower():break
  time.sleep(.5)
 direct_health=ev('''(async()=>{try{const r=await fetch('/healthz',{cache:'no-store'});return {ok:r.ok,status:r.status,failure:null}}catch(e){return {ok:false,status:null,failure:e?.name||'error'}}})()''')
 p205={'case':'P02-05','status':'PASS' if hb.get('status')==200 and ('不可用' in offline_header['text'] or 'unavailable' in offline_header['text'].lower()) and offline_header['destructiveCount']>=1 and direct_health['failure'] else 'PARTIAL','baseline_healthz':hb,'offline_health':direct_health,'offline_header':offline_header,'offline_request_meta':network()};save('case-p02-05.json',p205);prog('P02-05_OFFLINE_OBSERVED',status=p205['status'],header=offline_header)
 STAGE='P03-10_DASHBOARD_ERROR';click_link('/tasks');waitpath('/tasks');dashlabel=click_link('/');dashroute=waitpath('/');deadline=time.monotonic()+10;dash_error=False
 while time.monotonic()<deadline:
  dash_error=ev('''!!document.querySelector('[role="alert"]')''')
  if dash_error:break
  time.sleep(.2)
 alerts=ev('''(()=>({alert:!!document.querySelector('[role="alert"]'),retry:[...document.querySelectorAll('button')].some(b=>/retry|重试/i.test((b.innerText||'').trim()))}))()''')
 dash_off_reqs=network();p310={'case':'P03-10','status':'PARTIAL','dashboard_link_label':dashlabel,'dashboard_route':dashroute,'error_alert':alerts['alert'],'retry_button':alerts['retry'],'offline_request_meta':dash_off_reqs,'note':'Offline view is real session network loss; retry recovery measured after online restore below.'};save('case-p03-10-progress.json',p310);prog('P03-10_ERROR_CAPTURED',status='IN_PROGRESS',error_alert=alerts['alert'])
 if alerts['retry']:
  retry_labels=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');cli(['find','role','button','click','--name',retry_labels[0],'--exact']);time.sleep(.3)
 STAGE='P02-05_RESTORE';cli(['set','offline','off']);online_direct=ev('''(async()=>{try{const r=await fetch('/healthz',{cache:'no-store'});return {ok:r.ok,status:r.status}}catch(e){return {ok:false,failure:e?.name||'error'}}})()''');deadline=time.monotonic()+22;restored=header()
 while time.monotonic()<deadline:
  restored=header()
  if '已连接' in restored['text'] or 'connected' in restored['text'].lower():break
  time.sleep(.5)
 p205.update({'restored_health':online_direct,'restored_header':restored,'status':'PASS' if p205['status']=='PASS' and online_direct.get('status')==200 and ('已连接' in restored['text'] or 'connected' in restored['text'].lower()) else 'PARTIAL'});save('case-p02-05.json',p205);prog('P02-05_RESTORED',status=p205['status'],header=restored)
 STAGE='P03-10_DASHBOARD_RETRY';dashretry=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');
 if dashretry:cli(['find','role','button','click','--name',dashretry[0],'--exact']);deadline=time.monotonic()+12;retry_effect={'alert':True,'heading':''}
 while time.monotonic()<deadline:
  retry_effect=ev('''(()=>({alert:!!document.querySelector('[role="alert"]'),heading:(document.querySelector('main h1')?.innerText||document.querySelector('h1')?.innerText||'').trim()}))()''')
  if not retry_effect['alert']:break
  time.sleep(.2)
 dash_ok=not retry_effect['alert'];dash_online_reqs=network()
 p310={'case':'P03-10','status':'PASS' if p310['error_alert'] and p310['retry_button'] and dashroute and dash_ok and online_direct.get('status')==200 else 'PARTIAL','error_alert_offline':p310['error_alert'],'retry_button':p310['retry_button'],'dashboard_route':dashroute,'retry_cleared_error_online':dash_ok,'dashboard_heading_present':bool(retry_effect['heading']),'offline_requests':p310['offline_request_meta'],'online_requests':dash_online_reqs,'new_task_or_run':False};save('case-p03-10.json',p310);prog('P03-10_COMPLETE',status=p310['status'])

 # Return to Groups online; exact one owned empty Group fixture.
 STAGE='P06_GROUP_BASELINE';click_link('/groups');waitpath('/groups');gbase=group_api();
 if gbase['http']!=200:raise E('groups baseline GET failed')
 nbase={x['name'] for x in gbase['groups']};nonce=uuid.uuid4().hex[:10];GROUP_NAME='qa-web-b04-'+nonce
 while GROUP_NAME in nbase:nonce=uuid.uuid4().hex[:10];GROUP_NAME='qa-web-b04-'+nonce
 nodes_labels=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),a=await r.json(),xs=Array.isArray(a)?a:[];return {http:r.status,anyLabelCollision:xs.some(n=>Object.prototype.hasOwnProperty.call(n.labels||{},'qa_web_b04_'+'''+json.dumps(nonce)+'''))}})()''')
 if nodes_labels['http']!=200 or nodes_labels['anyLabelCollision']:raise E('unique label-key preflight failed')
 # P06-09 cancel branch: real New Group -> Cancel, capture zero POST.
 STAGE='P06-09_CANCEL';clear_network();cli(['open',ORIGIN+'/groups/new']);waitpath('/groups/new');install_write_observer();cancel_links=label_for_href('/groups');cancel=cancel_links[0] if cancel_links else None
 before_cancel=writes();
 if cancel:cli(['find','role','link','click','--name',cancel,'--exact']);cancel_back=waitpath('/groups')
 else:cancel_back=False
 after_cancel=writes();cancel_posts=[x for x in after_cancel[len(before_cancel):] if x['path']=='/api/groups' and x['method']=='POST'];p609={'case':'P06-09','cancel_back_to_list':cancel_back,'cancel_post_count':len(cancel_posts),'cancel_http_writes':after_cancel[len(before_cancel):],'status':'PASS' if cancel_back and len(cancel_posts)==0 else 'PARTIAL'};save('case-p06-09-cancel.json',p609);prog('P06-09_CANCEL_COMPLETE',status=p609['status'])
 # P06-09/P06-05 create exactly one group through the actual UI; POST response ID enters ledger immediately.
 STAGE='P06_CREATE_GROUP';cli(['open',ORIGIN+'/groups/new']);waitpath('/groups/new');install_write_observer();cli(['find','first','main input','fill',GROUP_NAME]);
 desc='Owned temporary empty group for batch04 UI verification '+nonce
 cli(['find','nth','1','main input','fill',desc]);
 # The editor defaults to Static. Read selected type and member-list count before saving.
 form0=ev('''(()=>({inputs:document.querySelectorAll('main input').length,combo:document.querySelector('[role="combobox"]')?.innerText?.trim()||'',checkboxes:document.querySelectorAll('main input[type="checkbox"]').length}))()''')
 if form0['inputs']<2:raise E('Group form input controls unavailable')
 cli(['find','role','button','click','--name','Save','--exact']) if ev('''[...document.querySelectorAll('button')].some(b=>(b.innerText||'').trim()==='Save')''') else cli(['find','role','button','click','--name','保存','--exact'])
 deadline=time.monotonic()+8;wr=writes()
 while time.monotonic()<deadline:
  wr=writes();created=[x for x in wr if x['method']=='POST' and x['path']=='/api/groups']
  if created:break
  time.sleep(.15)
 created=[x for x in wr if x['method']=='POST' and x['path']=='/api/groups']
 if len(created)!=1 or created[0].get('status') not in (200,201) or not created[0].get('id'):raise E('Group POST ID/status was not captured exactly')
 GROUP_ID=created[0]['id'];ledger=json.loads((R/'resource-ledger.json').read_text());ledger.setdefault('owned_groups',[]).append({'id':GROUP_ID,'name':GROUP_NAME,'created_at_utc':utc(),'create_http':created[0]['status'],'deleted_at_utc':None,'delete_http':None,'verify_http':None});(R/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
 p=json.loads((R/'progress.json').read_text());p.update({'created_owned_group_ids':[GROUP_ID],'updated_at_utc':utc(),'current_owned_group_count':1});(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');prog('P06_OWNED_GROUP_CREATED',group_id=GROUP_ID,create_http=created[0]['status'])
 if not waitpath('/groups'):raise E('Group create did not return to list')
 install_write_observer()
 gcreate=group_api(GROUP_ID)
 if gcreate['http']!=200 or gcreate['id']!=GROUP_ID or gcreate['name']!=GROUP_NAME:raise E('owned Group GET did not match create')
 save('case-p06-09-create.json',{'case':'P06-09','status':'IN_PROGRESS','user_action':'submitted real GroupEditor form','request':created[0],'api_state':gcreate,'form':form0,'group_id':GROUP_ID,'name':GROUP_NAME})
 save('case-p06-05-progress.json',{'case':'P06-05','status':'IN_PROGRESS','static_create':gcreate,'note':'Task execution residual intentionally remains PARTIAL (no Run permitted).'})

 # P06-01 list/search with the unique owned row; no pagination fixture expansion.
 STAGE='P06-01_SEARCH';cli(['open',ORIGIN+'/groups']);waitpath('/groups');install_write_observer();search=ev('''[...document.querySelectorAll('input')].map(x=>({placeholder:x.placeholder||'',type:x.type||''})).filter(x=>x.placeholder)''');ph=next((x['placeholder'] for x in search if 'search' in x['placeholder'].lower() or '搜索' in x['placeholder']),None)
 if not ph:raise E('Groups search input not found')
 sel='input[placeholder='+json.dumps(ph)+']';cli(['fill',sel,GROUP_NAME]);time.sleep(.4);owned_visible=ev('''(n=>[...document.querySelectorAll('tbody tr')].some(r=>(r.innerText||'').includes(n)))('''+json.dumps(GROUP_NAME)+''')''')
 cli(['fill',sel,'qa-web-b04-no-match-'+nonce]);time.sleep(.3);no_match=ev('''(()=>({rows:document.querySelectorAll('tbody tr').length,empty:!!document.querySelector('[data-slot="empty-state"]')||/no results|未找到|暂无数据/i.test(document.querySelector('main')?.innerText||'')}))()''')
 cli(['fill',sel,'']);time.sleep(.3);listrows=ev('''(()=>({rowCount:document.querySelectorAll('tbody tr').length,own: [...document.querySelectorAll('tbody tr')].some(r=>(r.innerText||'').includes('''+json.dumps(GROUP_NAME)+'''))}))()''');
 pag=ev('''(()=>({buttons:[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(Boolean).slice(-8),rowCount:document.querySelectorAll('tbody tr').length}))()''');pagination_applicable=len(gbase['groups'])+1>10
 # Groups error/retry on this same live list, using actual offline browser state.
 STAGE='P06-01_OFFLINE_RETRY';cli(['set','offline','on']);click_link('/tasks');waitpath('/tasks');click_link('/groups');groupsroute=waitpath('/groups');deadline=time.monotonic()+10;group_alert=False
 while time.monotonic()<deadline:
  group_alert=ev('''!!document.querySelector('[role="alert"]')''')
  if group_alert:break
  time.sleep(.2)
 group_retry=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');offline_group_requests=network();failed_retry=False
 if group_retry:cli(['find','role','button','click','--name',group_retry[0],'--exact']);failed_retry=True;time.sleep(.25)
 cli(['set','offline','off']);online_group_retry=ev('''[...document.querySelectorAll('button')].filter(b=>/retry|重试/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''')
 if online_group_retry:cli(['find','role','button','click','--name',online_group_retry[0],'--exact'])
 deadline=time.monotonic()+12;group_recovered=False
 while time.monotonic()<deadline:
  group_recovered=not ev('''!!document.querySelector('[role="alert"]')''')
  if group_recovered:break
  time.sleep(.2)
 p601={'case':'P06-01','status':'PASS' if not pagination_applicable and owned_visible and no_match['empty'] and listrows['own'] and groupsroute and group_alert and failed_retry and online_group_retry and group_recovered else 'PARTIAL','api_baseline_count':len(gbase['groups']),'api_count_with_owned_group':len(group_api()['groups']),'search_placeholder':ph,'owned_search_hit':owned_visible,'no_match_rows':no_match['rows'],'no_match_empty_feedback':no_match['empty'],'reset_rows':listrows['rowCount'],'owned_visible_after_reset':listrows['own'],'pagination_applicable':pagination_applicable,'pagination_controls':pag['buttons'] if pagination_applicable else [],'pagination_reason':'Dataset <=10; pagination not applicable.' if not pagination_applicable else 'Pagination applicable; next/prev not verified in this phase.','offline_route':groupsroute,'offline_error_alert':group_alert,'offline_failed_requests':offline_group_requests,'offline_retry_clicked':failed_retry,'online_retry_visible':bool(online_group_retry),'online_retry_recovered':group_recovered,'online_group_get_http':group_api()['http']};save('case-p06-01-progress.json',p601);save('case-p06-01.json',p601);prog('P06-01_SEARCH_AND_RETRY',status=p601['status'],group_error=group_alert,recovered=group_recovered)

 # P06-05 Static -> Label -> Static using only this Group; API confirms unchanged Node whitelist later.
 STAGE='P06-05_TO_LABEL';cli(['open',ORIGIN+'/groups/'+GROUP_ID]);waitpath('/groups/'+GROUP_ID);install_write_observer();cli(['click','button[role="combobox"]']);opts=ev('''[...document.querySelectorAll('[role="option"]')].map(x=>(x.innerText||'').trim())''');labelopt=next((x for x in opts if 'label' in x.lower() or '标签' in x),None)
 if not labelopt:raise E('Label group option unavailable')
 cli(['find','role','option','click','--name',labelopt,'--exact']);
 label_input_labels=ev('''[...document.querySelectorAll('label')].map(x=>(x.innerText||'').trim()).filter(x=>x)''');
 # Use the two inputs rendered inside labels-key/value labels and never persist typed values outside their purpose.
 inpinfo=ev('''[...document.querySelectorAll('label')].map(l=>({text:(l.innerText||'').trim(),hasInput:!!l.querySelector('input')})).filter(x=>x.hasInput)''');
 keylab=next((x['text'] for x in inpinfo if 'key' in x['text'].lower() or '键' in x['text']),None);vallab=next((x['text'] for x in inpinfo if 'value' in x['text'].lower() or '值' in x['text']),None)
 if not keylab or not vallab:raise E('label key/value controls not identified')
 lk='qa_web_b04_'+nonce;lv='no_node_match_'+nonce;cli(['find','label',keylab,'fill',lk]);cli(['find','label',vallab,'fill',lv]);
 savebtn=ev('''[...document.querySelectorAll('button')].filter(b=>/save|保存/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');cli(['find','role','button','click','--name',savebtn[0],'--exact']);time.sleep(.3);wr=writes();put1=[x for x in wr if x['path']=='/api/groups/'+GROUP_ID and x['method']=='PUT'];glabel=group_api(GROUP_ID);save('case-p06-05-label-state.json',{'case':'P06-05','operation':'Static to Label','put':put1[-1] if put1 else None,'api_state':glabel,'control_labels_identified':bool(keylab and vallab),'status':'IN_PROGRESS'});prog('P06-05_LABEL_SAVED',put_count=len(put1),api_type=glabel['type'])
 # Reopen owned editor, switch back to Static, observe which controls remain, and save.
 STAGE='P06-05_TO_STATIC';cli(['open',ORIGIN+'/groups/'+GROUP_ID]);waitpath('/groups/'+GROUP_ID);install_write_observer();cli(['click','button[role="combobox"]']);opts2=ev('''[...document.querySelectorAll('[role="option"]')].map(x=>(x.innerText||'').trim())''');staticopt=next((x for x in opts2 if 'static' in x.lower() or '静态' in x),None)
 if not staticopt:raise E('Static group option unavailable')
 cli(['find','role','option','click','--name',staticopt,'--exact']);old_inputs=ev('''[...document.querySelectorAll('label')].map(l=>(l.innerText||'').trim()).filter(x=>/label key|label value|标签键|标签值/i.test(x))''');savebtn=ev('''[...document.querySelectorAll('button')].filter(b=>/save|保存/i.test((b.innerText||'').trim())).map(b=>(b.innerText||'').trim())''');cli(['find','role','button','click','--name',savebtn[0],'--exact']);time.sleep(.3);wr=writes();put2=[x for x in wr if x['path']=='/api/groups/'+GROUP_ID and x['method']=='PUT'];gstatic=group_api(GROUP_ID)
 save('case-p06-05.json',{'case':'P06-05','status':'PARTIAL','group_id':GROUP_ID,'static_to_label_put':put1[-1] if put1 else None,'label_state':glabel,'label_to_static_put':put2[-1] if put2 else None,'static_state':gstatic,'old_label_controls_visible_after_switch_back':old_inputs,'node_stable_oracle_pending':True,'remaining':'Next Task execution/business targeting residual not tested because this batch prohibits Task/Run.'});prog('P06-05_COMPLETE',status='PARTIAL',api_type=gstatic['type'])

 # Final Group UI delete: row menu -> exact confirmation -> DELETE only our captured ID.
 STAGE='P06_GROUP_UI_DELETE';cli(['open',ORIGIN+'/groups']);waitpath('/groups');install_write_observer();rows=ev('''(n=>[...document.querySelectorAll('tbody tr')].map((r,i)=>({i,text:(r.innerText||'').trim()})).filter(x=>x.text.includes(n)))('''+json.dumps(GROUP_NAME)+''')''')
 if len(rows)!=1:raise E('owned Group row was not unique for UI cleanup')
 row_sel=f'tbody tr:nth-child({rows[0]["i"]+1}) button';cli(['click',row_sel]);menus=ev('''[...document.querySelectorAll('[role="menuitem"]')].map(x=>(x.innerText||'').trim())''');delmenu=next((x for x in menus if 'delete' in x.lower() or '删除' in x),None)
 if not delmenu:raise E('owned row delete menu item unavailable')
 cli(['find','role','menuitem','click','--name',delmenu,'--exact']);alert=ev('''!!document.querySelector('[role="alertdialog"]')''')
 if not alert:raise E('owned Group delete confirmation absent')
 confirm=ev('''[...document.querySelector('[role="alertdialog"]').querySelectorAll('button')].map(b=>(b.innerText||'').trim()).find(x=>/delete|删除/i.test(x))||'' ''')
 if not confirm:raise E('Group delete confirmation button missing')
 cli(['find','role','button','click','--name',confirm,'--exact']);deadline=time.monotonic()+10;wr=writes()
 while time.monotonic()<deadline:
  wr=writes();deletes=[x for x in wr if x['method']=='DELETE' and x['path']=='/api/groups/'+GROUP_ID]
  if deletes:break
  time.sleep(.15)
 deletes=[x for x in wr if x['method']=='DELETE' and x['path']=='/api/groups/'+GROUP_ID];v=group_api(GROUP_ID);GROUP_DELETED=bool(deletes and deletes[-1]['status'] in (200,204) and v['http']==404)
 ledger=json.loads((R/'resource-ledger.json').read_text());entry=next(x for x in ledger['owned_groups'] if x['id']==GROUP_ID);entry.update({'deleted_at_utc':utc(),'delete_http':deletes[-1]['status'] if deletes else None,'verify_http':v['http']});ledger['cleanup_verified']=GROUP_DELETED;(R/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
 gfinal=group_api();writes_final=writes();foreign=[x for x in writes_final if not (x['path']=='/api/groups' and x['method']=='POST') and not (x['path']=='/api/groups/'+GROUP_ID and x['method'] in ('PUT','DELETE'))]
 save('case-p06-09.json',{'case':'P06-09','status':'PASS' if p609['status']=='PASS' and gcreate['http']==200 and len(put1)==1 and len(put2)==1 and len(deletes)==1 and GROUP_DELETED and not foreign and len(gfinal['groups'])==len(gbase['groups']) else 'PARTIAL','cancel':p609,'create':created[0],'update_put_count':len(put1)+len(put2),'delete':deletes[-1] if deletes else None,'exact_get_after_delete':v,'baseline_group_count':len(gbase['groups']),'final_group_count':len(gfinal['groups']),'unexpected_write_count':len(foreign),'group_id':GROUP_ID});prog('P06-09_UI_DELETE_COMPLETE',status='PASS' if GROUP_DELETED else 'PARTIAL',group_id=GROUP_ID)
 if foreign:raise E('non-owned-group write request observed')
 if not GROUP_DELETED:raise E('owned Group UI cleanup not verified')

 # Stable Node identity/label whitelist must be byte-for-byte unchanged; ignore heartbeat fields.
 STAGE='P2_NODE_STABLE_VERIFY';NODE_AFTER=stable_nodes();save('phase2-node-stable-after.json',{'captured_at_utc':utc(),'http':NODE_AFTER['http'],'node_count':len(NODE_AFTER['nodes']),'nodes':NODE_AFTER['nodes']})
 stable_same=NODE_AFTER['http']==NODE_BASE['http']==200 and NODE_AFTER['nodes']==NODE_BASE['nodes']
 if not stable_same:raise E('stable Node identity/labels whitelist changed')
 case=json.loads((R/'case-p06-05.json').read_text());case.update({'node_stable_oracle_pending':False,'nodes_stable_identity_and_labels_unchanged':True,'node_before_evidence':'phase2-node-stable-before.json','node_after_evidence':'phase2-node-stable-after.json'});save('case-p06-05.json',case)
 progress_data=json.loads((R/'progress.json').read_text());progress_data.update({'stable_node_snapshot_before':'phase2-node-stable-before.json','stable_node_snapshot_after':'phase2-node-stable-after.json','stable_node_whitelist_unchanged':True,'all_app_writes_owned_group_only':not foreign,'phase2_business_complete':True});(R/'progress.json').write_text(json.dumps(progress_data,ensure_ascii=False,indent=2)+'\n')
 prog('PHASE2_COMPLETE',status='PARTIAL',node_stable_unchanged=True,owned_group_deleted=True,unexpected_write_count=0)
except Exception as e:
 diag={'captured_at_utc':utc(),'phase':'phase2','stage':STAGE,'type':type(e).__name__,'message':str(e)[:180] if isinstance(e,E) else 'exception redacted','cli_failure':LAST_CLI,'eval_fingerprint':LAST_EVAL,'group_id_known':bool(GROUP_ID),'private_values_saved':False,'raw_auth_or_api_body_saved':False};save('phase2-error.json',diag);prog('PHASE2_ERROR',status='PARTIAL',error=diag);print(json.dumps({'phase':'PHASE2_ERROR','stage':STAGE,'type':type(e).__name__,'group_id_known':bool(GROUP_ID)}),flush=True)
finally:
 cleanup=cleanup_group() if OPEN else {'id_known':bool(GROUP_ID),'deleted':GROUP_DELETED,'not_found':GROUP_DELETED}
 appwrites=[]
 if OPEN:
  try:appwrites=writes()
  except Exception:appwrites=[]
 allowedwrites=all((x.get('path')=='/api/groups' and x.get('method')=='POST') or (GROUP_ID and x.get('path')=='/api/groups/'+GROUP_ID and x.get('method') in ('PUT','DELETE')) for x in appwrites)
 save('phase2-write-ledger.json',{'captured_at_utc':utc(),'application_writes':appwrites,'only_owned_group_paths':allowedwrites,'raw_bodies_saved':False})
 if GROUP_ID and cleanup.get('not_found') is True:
  try:
   ledger=json.loads((R/'resource-ledger.json').read_text());entry=next((x for x in ledger.setdefault('owned_groups',[]) if x.get('id')==GROUP_ID),None)
   if entry:entry.update({'deleted_at_utc':utc(),'delete_http':cleanup.get('delete_http'),'verify_http':404,'cleanup_fallback':True})
   ledger['cleanup_verified']=True
   (R/'resource-ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2)+'\n')
  except Exception:pass
 logout={'had_token':False,'post':None,'old_me':None,'removed':True}
 if OPEN:
  try:
   cli(['set','offline','off'],check=False)
   logout=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let p=0,m=0;if(t){p=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;m=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,post:p,old_me:m,removed:!localStorage.getItem('nodesteer_token')}})()''')
   clear_network();cli(['close'],check=False)
  except Exception:logout={'had_token':False,'post':None,'old_me':None,'removed':False}
  OPEN=False
 if C:
  try:C.close()
  except Exception:pass
 if T:
  try:
   guarded=T.poll() is None and procstart(T.pid)==TICKS and hashlib.sha256(b'\0'.join(argv(T.pid))).hexdigest()==AH
   if guarded:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
   ls=listeners();tunnel={'pid':T.pid,'startticks':TICKS,'argv_sha256':AH,'guard_matched':guarded,'stopped':T.poll() is not None,'listener_after_stop':ls,'listener_gone':ls['count']==0}
  except Exception:tunnel={'guard_matched':False,'stopped':False,'listener_gone':False}
 else:tunnel={'guard_matched':False,'stopped':True,'listener_gone':True}
 cleanup.update({'captured_at_utc':utc(),'dedicated_logout':logout,'browser_closed':not OPEN,'tunnel':tunnel,'secret_values_saved':False});save('phase2-cleanup.json',cleanup)
 try:
  p=json.loads((R/'progress.json').read_text());p.update({'updated_at_utc':utc(),'browser_open':False,'tunnel_started':False,'cleanup':cleanup,'owned_group_deleted_get404':cleanup.get('not_found') is True,'dedicated_logout_old_me_401':logout.get('old_me')==401,'listener_gone':tunnel.get('listener_gone'),'application_writes_only_owned_group':allowedwrites});(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 except Exception:pass
 print(json.dumps({'group_deleted_get404':cleanup.get('not_found'),'logout_old_me_401':logout.get('old_me')==401,'tunnel_stopped':tunnel.get('stopped'),'listener_gone':tunnel.get('listener_gone')},ensure_ascii=False),flush=True)
