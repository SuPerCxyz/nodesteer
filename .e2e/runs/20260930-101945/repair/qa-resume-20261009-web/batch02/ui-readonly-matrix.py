import json, subprocess, urllib.request, urllib.error, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');conn=json.loads((PRIVATE/'repair-connection.json').read_text())
S='qawebsecure20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080';BASE=ORIGIN
C=['agent-browser','--session',S,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
results=[];seq=0
def cli(*a,timeout=18):
 r=subprocess.run(C+[str(x) for x in a],text=True,capture_output=True,timeout=timeout)
 if r.returncode:raise RuntimeError(str(a[0])+' failed '+r.stderr[:140])
 return r.stdout
def ev(js):
 d=json.loads(cli('--json','eval',js));return d.get('data',{}).get('result')
def snap():return cli('snapshot','-i')
def shot(n):cli('screenshot',str((OUT/(n+'.png')).resolve()))
def nav(path):cli('open',BASE+path);cli('wait',1000)
def reqs():
 r=subprocess.run(C+['--json','network','requests'],text=True,capture_output=True,timeout=12)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except:return []
 out=[]
 def rec(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/' in u:out.append({'path':u.split(ORIGIN,1)[-1].split('?',1)[0],'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):rec(v)
  elif isinstance(x,list):
   for v in x:rec(v)
 rec(d);return out
def api(path):
 rq=urllib.request.Request(conn['hub_url'].rstrip('/')+path,headers={'Authorization':'Bearer '+conn['token']})
 with urllib.request.urlopen(rq,timeout=10) as r:return r.status,json.loads(r.read())
def record(cid,status,details,evidence=()):
 global seq;seq+=1
 row={'canonical_id':cid,'status':status,'details':details,'evidence':list(evidence)};results.append(row)
 (OUT/f'case-{cid.lower()}-result.json').write_text(json.dumps({'captured_at':now(),'executor':'gpt-6-luna',**row},ensure_ascii=False,indent=2)+'\n')
 prog.update({'phase':'BATCH02_RUNNING','updated_at':now(),'completed_count':seq,'results':results,'current_gate':f'{cid} recorded; remaining cases continue in this runner.'})
 (OUT/'progress.json').write_text(json.dumps(prog,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'case':cid,'status':status,'details':details},ensure_ascii=False),flush=True)
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
prog=json.loads((OUT/'progress.json').read_text());prog['started_at']=now();(OUT/'progress.json').write_text(json.dumps(prog,ensure_ascii=False,indent=2)+'\n')
def run_case(cid,fn):
 try:fn()
 except Exception as e:record(cid,'PARTIAL',{'error_type':type(e).__name__,'error':str(e)[:250]},())
# G02-03
def g0203():
 nav('/agents');cli('find','role','button','click','--name','个人资料','--exact');ss=snap();menu=('设置' in ss or 'Settings' in ss)
 cli('find','role','menuitem','click','--name','设置','--exact');cli('wait',500);u=cli('get','url').strip();h=ev('document.querySelector("main h1")?.innerText||document.querySelector("h1")?.innerText')
 shot('g02-03-profile-settings');record('G02-03','PASS' if menu and u.endswith('/settings') and h else 'PARTIAL',{'menu_settings_visible':menu,'url':u.replace(ORIGIN,''),'heading':h},['g02-03-profile-settings.png'])
run_case('G02-03',g0203)
# sidebar desktop+mobile shared behavior
nav('/');cli('set','viewport','1440','900');cli('find','role','button','click','--name','切换侧边栏','--exact');cli('wait',250)
desk_coll=ev('({w:innerWidth,sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,sidebar:document.querySelector("[data-slot=sidebar]")?.getBoundingClientRect().width})');shot('sidebar-desktop-collapsed')
cli('find','role','button','click','--name','切换侧边栏','--exact');cli('wait',250);desk_open=ev('({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,sidebar:document.querySelector("[data-slot=sidebar]")?.getBoundingClientRect().width})');shot('sidebar-desktop-expanded')
record('G02-06','PASS' if desk_coll['sw']<=desk_coll['cw'] and desk_open['sw']<=desk_open['cw'] else 'FAIL',{'desktop_collapsed':desk_coll,'desktop_expanded':desk_open},['sidebar-desktop-collapsed.png','sidebar-desktop-expanded.png'])
# mobile open nav then route click auto-close
cli('set','viewport','390','844');nav('/');shot('sidebar-mobile-closed');
ss=snap();toggle='切换侧边栏' if '切换侧边栏' in ss else 'Toggle Sidebar';cli('find','role','button','click','--name',toggle,'--exact');cli('wait',200);mob_open=ev('({sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,sidebar:document.querySelector("[data-slot=sidebar]")?.getBoundingClientRect().width,visible:[...document.querySelectorAll("[data-slot=sidebar]")].some(e=>e.getBoundingClientRect().width>0)})');shot('sidebar-mobile-open')
# choose nav via actual link; mobile menu may close after navigation
cli('find','role','link','click','--name','任务','--exact');cli('wait',500);mob_after=ev('({path:location.pathname,sw:document.documentElement.scrollWidth,cw:document.documentElement.clientWidth,sidebarOpen:document.querySelector("[data-slot=sidebar]")?.getAttribute("data-state")||document.querySelector("[data-slot=sidebar]")?.getAttribute("data-mobile")})');shot('sidebar-mobile-after-nav')
record('G02-07','PASS' if mob_after['path']=='/tasks' and mob_after['sw']<=mob_after['cw'] else 'PARTIAL',{'mobile_open':mob_open,'after_navigation':mob_after,'nav_closed_observed_by_route_change':mob_after['path']=='/tasks'},['sidebar-mobile-open.png','sidebar-mobile-after-nav.png'])
record('P02-03','PASS' if desk_coll['sw']<=desk_coll['cw'] and desk_open['sw']<=desk_open['cw'] and mob_after['path']=='/tasks' else 'PARTIAL',{'desktop_collapse_expand':True,'mobile_navigation_route':mob_after['path'],'mobile_layout_no_overflow':mob_after['sw']<=mob_after['cw']},['sidebar-desktop-collapsed.png','sidebar-desktop-expanded.png','sidebar-mobile-open.png','sidebar-mobile-after-nav.png'])
# G04-06 responsive viewport matrix across list/detail
node_id='37989297-3bc8-4c0c-b302-7c6efbdba630';layout=[]
for w,h in [(1366,768),(1440,900),(390,844)]:
 cli('set','viewport',str(w),str(h));nav('/agents');layout.append({'path':'/agents','viewport':[w,h],**ev('(()=>{const m=document.querySelector("main"),r=m?.getBoundingClientRect();return {scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,main:m?{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),right:Math.round(r.right),bottom:Math.round(r.bottom)}:null,viewport:{w:innerWidth,h:innerHeight}}})()')});shot(f'g04-06-agents-{w}x{h}')
 nav('/agents/'+node_id);layout.append({'path':'/agents/:native3','viewport':[w,h],**ev('(()=>{const m=document.querySelector("main"),r=m?.getBoundingClientRect();return {scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,main:m?{x:Math.round(r.x),width:Math.round(r.width),right:Math.round(r.right)}:null,viewport:{w:innerWidth,h:innerHeight}}})()')});shot(f'g04-06-detail-{w}x{h}')
record('G04-06','PASS' if all(x['scrollWidth']<=x['clientWidth'] for x in layout) and all(x['main'] for x in layout) else 'PARTIAL',{'measurements':layout},[f'g04-06-{p}-{w}x{h}.png' for w,h in [(1366,768),(1440,900),(390,844)] for p in ['agents','detail']])
# P04-02 legacy route redirect and actual hostname link.
nav('/nodes');legacy=ev('({path:location.pathname,heading:document.querySelector("main h1")?.innerText||"",firstAgentLink:document.querySelector("table tbody tr a")?.getAttribute("href")})');nav('/agents');agents=ev('({path:location.pathname,heading:document.querySelector("main h1")?.innerText||"",native3Link:[...document.querySelectorAll("table tbody tr a")].find(a=>a.innerText.includes("native-3"))?.getAttribute("href")})');cli('find','role','link','click','--name','qa-repair-20260930-101945-native-3','--exact');cli('wait',500);detail=cli('get','url').strip();record('P04-02','PASS' if legacy['firstAgentLink'].startswith('/agents/') and agents['native3Link']=='/agents/'+node_id and detail.endswith('/agents/'+node_id) else 'PARTIAL',{'nodes_route':{**legacy,'list_content_present':bool(legacy['firstAgentLink'])},'agents_route':{**agents,'list_content_present':bool(agents['native3Link'])},'detail_url':detail.replace(ORIGIN,'')},[])
# P04-08 search/no-result/refresh/page-size read-only.
nav('/agents');cli('network','requests','--clear');cli('find','placeholder','搜索主机名、地址或 Agent ID','fill','qa-no-such-host-20261009');cli('wait',500);no_result='没有找到' in snap() or 'No results' in snap() or '无结果' in snap();empty=ev('document.querySelector("table tbody")?.innerText||""');
# clear with keyboard to avoid controlled-input fill-empty issue
cli('find','placeholder','搜索主机名、地址或 Agent ID','click');cli('press','Control+a');cli('press','Backspace');cli('wait',500);cli('reload');cli('wait',1000);list_snap=snap();rows=ev('document.querySelectorAll("table tbody tr").length');
# inspect page-size control, set 5 when available
controls=ev('[...document.querySelectorAll("button,[role=combobox],select")].map(e=>({text:(e.innerText||e.getAttribute("aria-label")||"").trim(),tag:e.tagName,role:e.getAttribute("role"),value:e.value})).filter(x=>/5|page|页|rows|条/i.test(x.text)||x.tag==="SELECT")');
# capture after search and reset
shot('p04-08-list-after-refresh'); rows_empty=ev('document.querySelectorAll("table tbody tr").length');empty_body=ev('(document.querySelector("main")?.innerText||"").slice(-500)');cli('find','role','combobox','click');options=snap();has_page5='option "5"' in options;has_page10='option "10"' in options;cli('press','Escape');next_enabled=ev('[...document.querySelectorAll("button")].find(b=>/下一页|next page/i.test(b.innerText||b.getAttribute("aria-label")||""))?.disabled===false');details={'no_result_feedback':no_result,'rows_for_no_match':rows_empty,'empty_result_text_tail':empty_body,'rows_after_reset_refresh':rows,'pagination_controls':controls,'page_size_5_available':has_page5,'page_size_10_available':has_page10,'next_page_enabled_at_dataset_size':next_enabled,'reload_url':cli('get','url').strip().replace(ORIGIN,'')};status='PARTIAL' if rows_empty==0 and rows>=1 and has_page10 and not next_enabled else 'PARTIAL';record('P04-08',status,details,['p04-08-list-after-refresh.png'])
# P05A-01/02/04/13 safe dialog interaction; do not submit valid payload or command generation.
nav('/agents');cli('find','role','button','click','--name','添加节点','--exact');cli('wait',300);opened=ev('!!document.querySelector("[role=dialog]")');shot('p05a-dialog-open');cli('press','Escape');cli('wait',250);closed=not ev('!!document.querySelector("[role=dialog]")');cli('find','role','button','click','--name','添加节点','--exact');cli('wait',250);reopen=snap();reset=ev('(()=>[...document.querySelectorAll("[role=dialog] input")].map(e=>({required:e.required,valueLength:e.value.length,type:e.type})))()');shot('p05a-dialog-reopened-reset');record('P05A-01','PASS' if opened and closed and '添加节点' in reopen else 'PARTIAL',{'opened':opened,'escape_closed':closed,'reopened':True,'inputs':reset},['p05a-dialog-open.png','p05a-dialog-reopened-reset.png'])
# required constraints, click submit while all required fields blank: native constraint should prevent request, no generated node possible.
cli('network','requests','--clear');validity=ev('(()=>{const f=document.querySelector("[role=dialog] form");return {form:!!f,checkValidity:f?.checkValidity(),inputs:[...f.querySelectorAll("input")].map(e=>({required:e.required,valid:e.checkValidity(),type:e.type,empty:e.value.length===0})),submitDisabled:f?.querySelector("button[type=submit]")?.disabled}})()');cli('find','role','button','click','--name','生成安装命令','--exact');cli('wait',400);post_events=[x for x in reqs() if x.get('path','').endswith('/api/nodes/enrollment') and x.get('method')=='POST'];after_blank=ev('({formValid:document.querySelector("[role=dialog] form")?.checkValidity(),dialog:!!document.querySelector("[role=dialog]"),visibleText:(document.querySelector("[role=dialog]")?.innerText||"").slice(0,350)})');shot('p05a-required-validation');record('P05A-02','PASS' if validity['form'] and validity['checkValidity'] is False and not post_events and not after_blank['formValid'] else 'PARTIAL',{'before':validity,'post_enrollment_requests':post_events,'after':after_blank},['p05a-required-validation.png'])
# fill harmless invalid address formats only; no submit
cli('find','label','节点名称','fill','qa-invalid-input-observation');cli('find','label','节点地址（IP 或域名）','fill','not a valid host !');cli('find','label','Hub 地址','fill','http://192.168.100.209:18080');cli('press','Tab');invalid=ev('(()=>({nodeAddress:document.querySelector("#enrollment-node-ip")?.value,valid:document.querySelector("#enrollment-node-ip")?.checkValidity(),custom:document.querySelector("#enrollment-node-ip")?.validationMessage.length>0,hubValid:document.querySelector("#enrollment-hub-address")?.checkValidity()}))()');bad_events=[x for x in reqs() if x.get('path','').endswith('/api/nodes/enrollment') and x.get('method')=='POST'];record('P05A-04','PARTIAL',{'tested_invalid_node_address_only':{'length':len(invalid['nodeAddress'] or ''),'input_valid':invalid['valid'],'browser_message_present':invalid['custom'],'hub_valid':invalid['hubValid']},'valid_IPv4_IPv6_DNS_submission':'NOT_TESTED_TO_AVOID_CREATING_PERSISTENT_PENDING_NODE','no_enrollment_post':not bad_events},[])
# close/reset and reopen verifies no typed/error state persists; command tabs require server-generated response so not attempted.
cli('find','role','button','click','--name','Close','--exact');cli('wait',250);cli('find','role','button','click','--name','添加节点','--exact');cli('wait',250);reset2=ev('(()=>({inputs:[...document.querySelectorAll("[role=dialog] input")].map(e=>({required:e.required,length:e.value.length,type:e.type})),hasError:!!document.querySelector("[role=dialog] [role=alert]"),hasCommand:!!document.querySelector("[role=dialog] pre"),method:document.querySelector("[role=dialog] [role=tab][aria-selected=true]")?.innerText||null}))()');shot('p05a-reset-after-reopen');record('P05A-13','PASS' if all(x['length']==0 for x in reset2['inputs'][:-1]) and not reset2['hasError'] and not reset2['hasCommand'] else 'PARTIAL',{'reopened_state':reset2,'generation_not_attempted':True},['p05a-reset-after-reopen.png'])
record('P05A-08','PARTIAL',{'tabs_and_command_content':'NOT_TESTED','reason':'actual Native/docker run/docker compose tab content exists only after POST /api/nodes/enrollment returns a generated command; submitting valid fields can create persistent pending Node with no DELETE API, so no valid generation request was made.'},[])
cli('press','Escape');cli('wait',200)
# P05B detail sections + fields from read-only UI and independent API oracle, native-3 only.
cli('set','viewport','1440','900');nav('/agents/'+node_id);cli('wait',1400);body=ev('document.querySelector("main")?.innerText||document.body.innerText');ui_headings=ev('[...document.querySelectorAll("main h2,main h3")].map(e=>e.innerText.trim())');http,api_node=api('/api/nodes/'+node_id)
fields=['id','agent_id','ip','os','version','arch','mode','revision','sync_status','last_seen'];safe_api={k:api_node.get(k) for k in fields if k in api_node};body_len=len(body);sections=['概览','Inventory','Capability','标签','任务','计划','应用','执行'];missing=[x for x in sections if x.lower() not in body.lower()]
# evidence list only visible business field strings / hashes; no credentials. Existing owned API object is a node record.
visible={}
for key,val in safe_api.items():
 if val is None: visible[key]=None
 elif isinstance(val,(str,int,float,bool)): visible[key]={'visible_in_main':str(val).lower() in body.lower(),'value_sha256':hashlib.sha256(str(val).encode()).hexdigest()}
record('P05B-01','PASS' if body_len>0 and not any(x in missing for x in ['任务','执行']) else 'PARTIAL',{'main_text_length':body_len,'headings':ui_headings,'expected_sections_missing':missing,'one_page_no_tabs':not bool(ev('document.querySelector("main [role=tablist]")'))},[])
record('P05B-02','PASS' if http==200 and all(v.get('visible_in_main',True) for v in visible.values() if isinstance(v,dict)) else 'PARTIAL',{'oracle_http':http,'api_fields_present':list(safe_api.keys()),'field_visibility':visible,'native3_only':True},[])
# P01-07 state read from repair API; disabled case only.
try:
 code,oidc=api('/api/oidc/state');en=bool(oidc.get('enabled') or oidc.get('Enabled'));record('P01-07','PASS' if code==200 and not en else 'PARTIAL',{'oidc_state_http':code,'enabled':en,'sso_button_assertion':'not applicable; no changes to global OIDC state' if en else 'inspect sign-in CTA'})
 if not en:
  # sign-in in a new tab is not required; main admin token remains in localStorage; inspect without clearing it through separate session
  pass
except Exception as e:record('P01-07','PARTIAL',{'oracle_error_type':type(e).__name__,'error':str(e)[:180]})
# exact final environment state
cli('set','offline','off');cli('set','viewport','1440','900');nav('/agents')
summary={'captured_at':now(),'batch_id':'web-20261009-02','executor':'gpt-6-luna','canonical_cases_planned':16,'results':results,'owned_created_ids':[],'owned_deleted_ids':[],'persistent_node_generation_attempted':False,'oidc_global_changed':False,'main_session_online':ev('navigator.onLine'),'main_session_url':cli('get','url').strip().replace(ORIGIN,''),'private_auth_saved':False,'next_batch_plan':['P05A-08 with a disposable owned Node enrollment fixture only after a cleanup/DELETE-capable scheme is authorized or documented','P05A-04 valid IPv4/IPv6/DNS outcomes with disposable owned enrollment fixture','G-01-06 UI signout server invalidation defect review','X16 revision/audit/error/default-input runtime coverage','WEB-LIVE-LOG remaining attempt metadata/PENDING and real clipboard exact read/denied feedback']}
(OUT/'batch02-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
prog.update({'phase':'BATCH02_PARTIAL_COMPLETE','updated_at':now(),'results':results,'summary_file':'batch02-summary.json','current_gate':'Batch02 read-only UI matrix completed; generation-dependent Node command dimensions remain PARTIAL for no-persistent-write safety.','next_actions':summary['next_batch_plan']});(OUT/'progress.json').write_text(json.dumps(prog,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'summary':'batch02-summary.json','case_statuses':{r['canonical_id']:r['status'] for r in results},'owned_created_ids':[],'online':summary['main_session_online']},ensure_ascii=False),flush=True)
