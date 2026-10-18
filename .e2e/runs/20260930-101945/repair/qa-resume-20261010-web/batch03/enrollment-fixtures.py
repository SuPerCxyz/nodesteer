import json,subprocess,urllib.request,urllib.error,hashlib,time
from pathlib import Path
from datetime import datetime,timezone
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch03');plan=json.loads((B/'prepare-case-plan.json').read_text());fixtures=plan['fixtures'];priv=Path('/tmp/nodesteer-e2e-resume-20260909-Qv9AYO')
if not priv.exists():priv=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
conn=json.loads((priv/'repair-connection.json').read_text());credentials=json.loads((priv/'repair-credentials.json').read_text());REG=credentials.get('registration_token','')
O='http://192.168.100.209:18080';S='qawebsecure20261009';C=['agent-browser','--session',S,'--allowed-domains','192.168.100.209','--args','--unsafely-treat-insecure-origin-as-secure='+O]
created=[];clean_result=[];results=list(json.loads((B/'progress.json').read_text()).get('results',[]))
def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def cli(*a,timeout=20):
 r=subprocess.run(C+[str(x) for x in a],capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise RuntimeError((r.stderr or r.stdout)[:220])
 return r.stdout
def ev(js):return json.loads(cli('--json','eval',js))['data']['result']
def api(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode()
 req=urllib.request.Request(conn['hub_url'].rstrip('/')+path,data=data,headers={'Authorization':'Bearer '+conn['token'],**({'Content-Type':'application/json'} if data is not None else {})},method=method)
 try:
  with urllib.request.urlopen(req,timeout=12) as r:
   raw=r.read();return r.status,json.loads(raw) if raw else None
 except urllib.error.HTTPError as e:
  raw=e.read()
  try:d=json.loads(raw)
  except:d=None
  return e.code,d
def get_nodes():
 code,d=api('/api/nodes')
 if code!=200:raise RuntimeError('node list API status '+str(code))
 return d if isinstance(d,list) else d.get('items',d.get('nodes',[]))
def network_events():
 r=subprocess.run(C+['--json','network','requests'],capture_output=True,text=True,timeout=16)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except:return []
 out=[]
 def walk(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/' in u:out.append({'path':u.split(O,1)[-1].split('?',1)[0],'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return out
def posts_for_enrollment():return [x for x in network_events() if x.get('path','').endswith('/api/nodes/enrollment') and x.get('method')=='POST']
def write_json(name,obj):(B/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def progress(phase,gate):
 p=json.loads((B/'progress.json').read_text());p.update({'updated_at_utc':now(),'phase':phase,'current_gate':gate,'created_ids':[x['id'] for x in created],'deleted_ids':[x['id'] for x in clean_result if x.get('cleanup_verified')],'cleanup_verified':bool(clean_result) and all(x.get('cleanup_verified') for x in clean_result),'results':results});(B/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 d=json.loads((B/'resource-intents.json').read_text());d['created']=[{k:x[k] for k in ('id','agent_id','name','ip','purpose','post_http')} for x in created];d['deleted']=[{'id':x['id'],'delete_http':x['delete_http'],'get_after_http':x['get_after_http'],'cleanup_verified':x['cleanup_verified']} for x in clean_result];d['cleanup_verified']=bool(clean_result) and all(x.get('cleanup_verified') for x in clean_result);d['last_updated_utc']=now();write_json('resource-intents.json',d)
def record(cid,status,details,evidence=()):
 row={'captured_at_utc':now(),'canonical_id':cid,'status':status,'details':details,'evidence':list(evidence)};results[:]=[x for x in results if x['canonical_id']!=cid];results.append(row);write_json('case-'+cid.lower()+'.json',row);progress('FIXTURE_EXECUTION',cid+' evidence saved; owned ID list synchronized.')
def dialog_visible():return ev('(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return !!d&&r.width>0&&r.height>0&&getComputedStyle(d).visibility!=="hidden"})()')
def open_dialog():
 if dialog_visible():cli('press','Escape');cli('wait',700)
 cli('find','role','button','click','--name','添加节点');cli('wait',180)
def fill(label,value):cli('find','label',label,'fill',value)
def current_form():return ev('(()=>{const f=document.querySelector("[role=dialog] form");return {valid:f?.checkValidity(),values:[...f.querySelectorAll("input")].map(i=>i.value),required:[...f.querySelectorAll("input")].map(i=>i.required)}})()')
def safe_tab_metrics(text,name,ip,agent_id,token,tab):
 b=text.encode();
 if tab=='Native':
  checks={'hub_url':('hub_url: "' in text and ('ws://' in text or 'wss://' in text)),'registration_token':('registration_token:' in text and bool(token) and token in text),'node_name':f'node_name: "{name}"' in text,'node_ip':f'node_ip: "{ip}"' in text,'agent_id':f'agent_id: "{agent_id}"' in text,'native_installer':('curl --fail' in text and 'systemctl enable nodesteer-agent' in text)}
 elif tab=='docker run':
  checks={'hub_url':'NODESTEER_HUB_URL=' in text and ('ws://' in text or 'wss://' in text),'registration_token':('NODESTEER_REGISTRATION_TOKEN=' in text and bool(token) and token in text),'node_name':'NODESTEER_NODE_NAME=' in text and name in text,'node_ip':'NODESTEER_NODE_IP=' in text and ip in text,'agent_id':'NODESTEER_AGENT_ID=' in text and agent_id in text,'docker_mode':'NODESTEER_DEPLOYMENT_MODE=docker' in text,'image':'ghcr.io/supercxyz/nodesteer-agent:latest' in text}
 else:
  checks={'hub_url':'NODESTEER_HUB_URL:' in text and ('ws://' in text or 'wss://' in text),'registration_token':('NODESTEER_REGISTRATION_TOKEN:' in text and bool(token) and token in text),'node_name':'NODESTEER_NODE_NAME:' in text and name in text,'node_ip':'NODESTEER_NODE_IP:' in text and ip in text,'agent_id':'NODESTEER_AGENT_ID:' in text and agent_id in text,'docker_mode':'NODESTEER_DEPLOYMENT_MODE: docker' in text,'image':'ghcr.io/supercxyz/nodesteer-agent:latest' in text}
 return {'text_length_bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'field_checks':checks,'all_checks':all(checks.values())}
def make_fixture(f,purpose,hub):
 # Collision gate before every real mutation; ignore unrelated concurrent nodes.
 xs=get_nodes()
 if any(x.get('hostname')==f['name'] or x.get('ip')==f['ip'] for x in xs):raise RuntimeError('candidate name or IP collided immediately before submit')
 cli('open',O+'/agents');cli('wait',500);open_dialog();fill('节点名称',f['name']);fill('节点地址（IP 或域名）',f['ip']);fill('Hub 地址',hub)
 actual=current_form()
 if actual['values']!=[f['name'],f['ip'],hub] or actual['valid'] is not True:
  raise RuntimeError('controlled form values do not match intended fixture; refused submit')
 cli('network','requests','--clear')
 cli('find','role','button','click','--name','生成安装命令')
 # Capture loading before waiting for response; this is the actual live POST (not delayed/mocked).
 pending=ev('(()=>({disabled:document.querySelector("[role=dialog] button[type=submit]")?.disabled||false,skeleton:!!document.querySelector("[role=dialog] [data-slot=skeleton]"),dialogVisible:!!document.querySelector("[role=dialog]")}))()')
 cli('wait',900)
 post=posts_for_enrollment();nodes=get_nodes();matches=[x for x in nodes if x.get('hostname')==f['name'] and x.get('ip')==f['ip']]
 if len(matches)!=1:raise RuntimeError('exact UI-generated node could not be identified from independent list oracle')
 n=matches[0];http=post[0].get('status') if len(post)==1 else None
 item={'id':n.get('id'),'agent_id':n.get('agent_id'),'name':f['name'],'ip':f['ip'],'purpose':purpose,'status':n.get('status'),'sync_status':n.get('sync_status'),'post_count':len(post),'post_http':http,'pending_sample':pending}
 created.append(item);progress('OWNED_NODE_CREATED',purpose+' created by UI; exact Node/Agent IDs written to resource-intents immediately.')
 return item
# Independent preconditions: browser active; API baseline and all planned candidates absent.
start_nodes=get_nodes();start_ids={x.get('id') for x in start_nodes};cand_collision=[f['purpose'] for f in fixtures if any(n.get('hostname')==f['name'] or n.get('ip')==f['ip'] for n in start_nodes)]
if cand_collision:raise SystemExit('fixture collision; no writes made')
if not ev('navigator.onLine'):raise SystemExit('main browser is offline; no writes made')
progress('FIXTURE_EXECUTION','Live browser online and all six candidate Node names/IPs unique; beginning first real enrollment POST.')
try:
 # Primary: IPv4, exact 128-character hostname, valid HTTPS Hub address; actual output is kept only in memory.
 primary=make_fixture(fixtures[0],'ipv4-valid-length128-and-command-primary','https://hub.qa-web.example.test:8443')
 agent_id=primary['agent_id'];node_id=primary['id'];primary_checks=[];tab_metrics=[]
 # The rendered command is read directly in memory; never print, snapshot, or store plaintext.
 tab_names=ev('[...document.querySelectorAll("[role=tab]")].map(x=>x.innerText.trim())')
 expected_tabs=['Native','docker run','docker compose']
 for tab in expected_tabs:
  if tab not in tab_names:continue
  cli('find','role','tab','click','--name',tab,'--exact');cli('wait',100)
  actual_selected=ev('document.querySelector("[role=tab][aria-selected=true]")?.innerText.trim()||""')
  raw=ev('document.querySelector("[role=dialog] pre")?.textContent||""')
  metrics=safe_tab_metrics(raw,primary['name'],primary['ip'],agent_id,REG,tab);tab_metrics.append({'tab':tab,'selected':actual_selected==tab,'metrics':metrics})
  del raw
 # P05A-06 exactly one user click -> exactly one POST with a generated resource.
 p06={'one_real_click':True,'post_count':primary['post_count'],'post_http':primary['post_http'],'node_found_by_exact_name_and_ip':True,'node_id':node_id,'agent_id':agent_id,'status':primary['status'],'sync_status':primary['sync_status']}
 record('P05A-06','PASS' if p06['post_count']==1 and p06['post_http']==200 and p06['status']=='pending' else 'PARTIAL',p06)
 # P05A-07 is only PASS if actual pending state was captured before response.
 pend=primary['pending_sample'];record('P05A-07','PASS' if pend['disabled'] or pend['skeleton'] else 'PARTIAL',{'real_post_pending_sample':pend,'mock_or_route_delay_used':False,'limitation':'If the real Hub response completed before this observation, loading state was not captured in this live run.'})
 # P05A-08 and P05A-09: actual three response tabs, secret-safe semantic assertions only.
 all_tabs={x['tab']:x for x in tab_metrics};tabs_pass=set(all_tabs)==set(expected_tabs) and all(x['selected'] and x['metrics']['text_length_bytes']>0 for x in tab_metrics)
 record('P05A-08','PASS' if tabs_pass else 'PARTIAL',{'tab_labels_seen':tab_names,'tab_samples':tab_metrics,'plaintext_saved':False,'screenshots_taken_while_command_visible':False})
 fields_pass=set(all_tabs)==set(expected_tabs) and all(x['metrics']['all_checks'] for x in tab_metrics)
 record('P05A-09','PASS' if fields_pass else 'PARTIAL',{'all_three_command_field_checks':{k:v['metrics']['field_checks'] for k,v in all_tabs.items()},'all_required_fields_present':fields_pass,'registration_token_compared_in_memory':True,'plaintext_or_token_saved':False})
 # P05A-13: close generated state; reopen and verify form/command/error reset, with no additional POST.
 cli('network','requests','--clear');cli('press','Escape');cli('wait',750);closed=not dialog_visible();cli('find','role','button','click','--name','添加节点');cli('wait',250)
 reset=ev('(()=>{const d=document.querySelector("[role=dialog]"),f=d?.querySelector("form");return {visible:!!d&&getComputedStyle(d).visibility!=="hidden",inputs:[...f.querySelectorAll("input")].map(x=>({length:x.value.length,required:x.required})),commandVisible:!!d.querySelector("pre"),errorVisible:!!d.querySelector("[role=alert]"),hubDefaultLength:f.querySelectorAll("input")[2].value.length}})()')
 no_extra_post=len(posts_for_enrollment())==0
 record('P05A-13','PASS' if closed and reset['visible'] and reset['inputs'][0]['length']==0 and reset['inputs'][1]['length']==0 and not reset['commandVisible'] and not reset['errorVisible'] and no_extra_post else 'PARTIAL',{'closed_after_generated_response':closed,'reopened_state':reset,'extra_enrollment_post':not no_extra_post,'hub_default_is_restored':reset['hubDefaultLength']>0})
 cli('press','Escape');cli('wait',750)
 # Create remaining five owned rows through real UI, using distinct name/IP pairs.
 for f in fixtures[1:]:
  item=make_fixture(f,f['purpose'],O)
  if item['post_count']!=1 or item['post_http']!=200:
   record('P05A-04','PARTIAL',{'valid_format_submission':f['purpose'],'post_count':item['post_count'],'post_http':item['post_http'],'created_node_id':item['id']})
 # P05A-04: use actual GET oracle to verify 3 accepted address families and invalid address rejection from validation phase.
 by_purpose={x['purpose']:x for x in created};families=[('ipv4-valid-length128-and-command-primary','IPv4'),('ipv6-valid','IPv6'),('dns-valid','DNS')]
 family_ok=[]
 for purpose,label in families:
  x=by_purpose.get(purpose);status,api_node=api('/api/nodes/'+x['id']) if x else (0,{})
  family_ok.append({'family':label,'get_http':status,'same_node_id':bool(x and api_node.get('id')==x['id']),'status':api_node.get('status') if status==200 else None,'sync_status':api_node.get('sync_status') if status==200 else None})
 invalid_case=json.load(open(B/'case-p05a-04.json'))
 invalid_ok=invalid_case['details']['invalid_address_ui']['rejected_without_node']
 record('P05A-04','PASS' if all(x['get_http']==200 and x['same_node_id'] for x in family_ok) and invalid_ok else 'PARTIAL',{'valid_address_families':family_ok,'invalid_address_ui_rejected_without_node':invalid_ok,'unique_owned_fixture_count':len(created)})
 # P05A-05 valid HTTPS primary was accepted; invalid scheme and missing host evidence came from validation-only runner.
 old05=json.load(open(B/'case-p05a-05.json'));bad=old05['details'];https_ok=primary['post_http']==200
 record('P05A-05','PASS' if https_ok and bad['details']['unsupported_scheme']['rejected_without_node'] and bad['details']['missing_host_ui']['prevented_by_native_validation'] and bad['details']['missing_host_api']['http_status']==400 else 'PARTIAL',{'valid_https_enrollment_http':primary['post_http'],'unsupported_scheme':bad['details']['unsupported_scheme'],'missing_host_ui':bad['details']['missing_host_ui'],'missing_host_api':bad['details']['missing_host_api']})
 # P05A-03 prior invalid/over-128 cases + 128-char accepted as independent pending fixture.
 name128=primary['name'];old03=json.load(open(B/'case-p05a-03.json'));record('P05A-03','PASS' if len(name128)==128 and primary['post_http']==200 and all(x['rejected_without_node'] for x in old03['details']['invalid_ui_submissions']) else 'PARTIAL',{'invalid_ui_cases':old03['details']['invalid_ui_submissions'],'valid_128_char_name_length':len(name128),'valid_128_char_node_id':primary['id']})
 # P05A-12: Hub pending records and preallocated Agent IDs; no HELLO/start is permitted in this batch.
 pending_rows=[]
 for x in created:
  status,node=api('/api/nodes/'+x['id']);pending_rows.append({'id':x['id'],'agent_id':x['agent_id'],'get_http':status,'status':node.get('status') if status==200 else None,'sync_status':node.get('sync_status') if status==200 else None,'same_agent_id':node.get('agent_id')==x['agent_id'] if status==200 else False})
 p12_full=all(x['get_http']==200 and x['status']=='pending' and x['sync_status']=='pending' and x['same_agent_id'] for x in pending_rows)
 record('P05A-12','PARTIAL',{'all_owned_nodes_pending_with_preallocated_agent_identity':p12_full,'nodes':pending_rows,'agent_hello_online_synced_reuse':'NOT_RUN_BY_SCOPE; no Agent started','api_pending_dimension':'PASS' if p12_full else 'PARTIAL'})
 # P04-08: enough real rows for two pages; local search, reset, reload, page-size and prev/next are actual controls.
 cli('open',O+'/agents');cli('wait',1000);cli('network','requests','--clear')
 # Choose page size 10 through the actual combobox/option.
 cli('find','role','combobox','click');cli('find','role','option','click','--name','10','--exact');cli('wait',300)
 first=ev('(()=>({rows:document.querySelectorAll("main table tbody tr").length,text:(document.querySelector("main")?.innerText||"").slice(-180),url:location.pathname}))()');api_count=len(get_nodes());cli('screenshot',str((B/'p04-08-page1.png').resolve()))
 next_enabled=ev('[...document.querySelectorAll("button")].find(b=>/下一页|next page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")))?.disabled===false')
 if next_enabled:cli('find','role','button','click','--name','下一页');cli('wait',400)
 second=ev('(()=>({rows:document.querySelectorAll("main table tbody tr").length,text:(document.querySelector("main")?.innerText||"").slice(-180),url:location.pathname}))()');cli('screenshot',str((B/'p04-08-page2.png').resolve()))
 prev_enabled=ev('[...document.querySelectorAll("button")].find(b=>/上一页|previous page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")))?.disabled===false')
 if prev_enabled:cli('find','role','button','click','--name','上一页');cli('wait',350)
 prev=ev('(()=>({rows:document.querySelectorAll("main table tbody tr").length,text:(document.querySelector("main")?.innerText||"").slice(-180)}))()')
 cli('network','requests','--clear');cli('find','placeholder','搜索主机名、地址或 Agent ID','fill','qa-web-no-match-20261010');cli('wait',350);nores=ev('(()=>({message:(document.querySelector("main")?.innerText||"").includes("没有结果。"),rows:document.querySelectorAll("main table tbody tr").length,valueLength:document.querySelector("input[placeholder*=搜索]")?.value.length||0}))()')
 cli('find','placeholder','搜索主机名、地址或 Agent ID','click');cli('press','Control+a');cli('press','Backspace');cli('wait',250);cli('reload');cli('wait',900)
 after=ev('(()=>({rows:document.querySelectorAll("main table tbody tr").length,searchLength:document.querySelector("input[placeholder*=搜索]")?.value.length||0,pageText:(document.querySelector("main")?.innerText||"").slice(-180)}))()');search_events=[x for x in network_events() if x.get('path','').startswith('/api/')]
 p04ok=api_count>=11 and first['rows']==10 and next_enabled and second['rows']==api_count-10 and prev_enabled and prev['rows']==10 and nores['message'] and nores['valueLength']>0 and after['rows']==10 and after['searchLength']==0
 record('P04-08','PASS' if p04ok else 'PARTIAL',{'api_dataset_count':api_count,'page_size':10,'page1':first,'next_enabled':next_enabled,'page2':second,'previous_enabled':prev_enabled,'after_previous':prev,'no_match':nores,'after_reset_and_reload':after,'no_api_mutation_from_search':not any(x.get('method') not in ('GET',None) for x in search_events),'evidence':['p04-08-page1.png','p04-08-page2.png']},['p04-08-page1.png','p04-08-page2.png'])
except Exception as e:
 err={'captured_at_utc':now(),'exception_type':type(e).__name__,'message':str(e)[:300],'created_ids':[x['id'] for x in created]};write_json('fixture-runner-error.json',err);progress('FIXTURE_RUNNER_ERROR','An enrollment fixture step failed; cleanup finally will attempt only exact tracked fixture IDs.')
finally:
 # Discover exact plan-name fixtures in case a POST succeeded before the response/result was observed.
 try:
  nodes=get_nodes();known={x['id'] for x in created}
  for f in fixtures:
   for n in nodes:
    if n.get('hostname')==f['name'] and n.get('ip')==f['ip'] and n.get('id') not in known:
     item={'id':n.get('id'),'agent_id':n.get('agent_id'),'name':f['name'],'ip':f['ip'],'purpose':f['purpose'],'status':n.get('status'),'sync_status':n.get('sync_status'),'post_count':None,'post_http':None,'pending_sample':None}
     created.append(item);known.add(item['id']);progress('OWNED_NODE_DISCOVERED','Discovered exact fixture after a UI POST; exact IDs saved before cleanup.')
  for x in list(created):
   status,n=api('/api/nodes/'+x['id'])
   if status!=200 or n.get('hostname')!=x['name'] or n.get('ip')!=x['ip']:raise RuntimeError('refusing cleanup: exact owned node identity mismatch')
   delete_status,_=api('/api/nodes/'+x['id'],'DELETE');get_status,_=api('/api/nodes/'+x['id']);clean_result.append({'id':x['id'],'delete_http':delete_status,'get_after_http':get_status,'cleanup_verified':delete_status==200 and get_status==404})
   progress('FIXTURE_CLEANUP','Deleted only exact owned Node ID '+x['id']+'; verifying GET 404.')
 except Exception as cleanup_error:
  write_json('cleanup-error.json',{'captured_at_utc':now(),'type':type(cleanup_error).__name__,'message':str(cleanup_error)[:250],'ids_deleted':[x['id'] for x in clean_result]})
# Final status of original baseline IDs only; do not touch any other Node.
oldpre=json.load(open(Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch03/preflight.json')))
try:
 now_nodes=get_nodes();now_by={x.get('id'):x for x in now_nodes};baseline=[]
 for x in oldpre['baseline_node_snapshot']:
  n=now_by.get(x['id']);baseline.append({'id':x['id'],'classification':x['classification'],'present':bool(n),'status':n.get('status') if n else None,'sync_status':n.get('sync_status') if n else None,'same_as_preflight_status_sync':bool(n and n.get('status')==x['status'] and n.get('sync_status')==x['sync_status'])})
 final={'captured_at_utc':now(),'owned_created':[{'id':x['id'],'agent_id':x['agent_id'],'purpose':x['purpose'],'post_http':x['post_http']} for x in created],'owned_cleanup':clean_result,'all_owned_deleted_and_404':len(clean_result)==len(created) and all(x['cleanup_verified'] for x in clean_result),'final_node_count':len(now_nodes),'baseline_nodes':baseline,'baseline_original_ids_present':all(x['present'] for x in baseline),'main_browser_online':ev('navigator.onLine'),'main_browser_secure_context':ev('isSecureContext'),'main_session_route':cli('get','url').strip().replace(O,'')}
 write_json('fixture-final-verification.json',final)
 progress('FIXTURE_BATCH_FINISHED','Exact owned Node cleanup and final baseline check completed; details in fixture-final-verification.json.')
 batch={'captured_at_utc':now(),'batch_id':'web-20261010-03-resume','executor':'gpt-6-luna','canonical_cases_planned':12,'results':results,'owned_nodes_created_count':len(created),'owned_nodes_deleted_count':len(clean_result),'owned_cleanup_verified':final['all_owned_deleted_and_404'],'final_node_count':len(now_nodes),'baseline_node_ids_present':final['baseline_original_ids_present'],'baseline_health_by_class':{label:{'count':sum(1 for x in baseline if x['classification']==label),'online':sum(1 for x in baseline if x['classification']==label and x['status']=='online'),'synced':sum(1 for x in baseline if x['classification']==label and x['sync_status']=='synced')} for label in sorted({x['classification'] for x in baseline})},'unexpected_external_nodes_not_touched':sum(1 for x in now_nodes if x.get('id') not in start_ids and x.get('hostname','').startswith('qa-tls-')),'private_values_saved':False,'next_actions':['If the real loading sample was missed, retain P05A-07 PARTIAL.','P05A-12 HELLO/online/synced identity reuse remains untested by explicit no-Agent-start boundary.','Inspect root integration for all 12 current canonical outcomes.']}
 write_json('batch03-summary.json',batch)
except Exception as final_error:
 write_json('final-verification-error.json',{'captured_at_utc':now(),'type':type(final_error).__name__,'message':str(final_error)[:250],'ids_deleted':[x['id'] for x in clean_result]})
print(json.dumps({'created_count':len(created),'deleted_count':len(clean_result),'cleanup_verified':bool(clean_result) and all(x['cleanup_verified'] for x in clean_result),'case_statuses':{x['canonical_id']:x['status'] for x in results},'private_values_saved':False},ensure_ascii=False),flush=True)
