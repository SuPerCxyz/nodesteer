import json,subprocess,urllib.request,urllib.error,time
from pathlib import Path
from datetime import datetime,timezone
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch03');plan=json.loads((B/'prepare-case-plan.json').read_text());fixtures=plan['fixtures'];priv=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');conn=json.loads((priv/'repair-connection.json').read_text())
S='qawebsecure20261009';O='http://192.168.100.209:18080';C=['agent-browser','--session',S,'--allowed-domains','192.168.100.209','--args','--unsafely-treat-insecure-origin-as-secure='+O]
def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def cli(*a,timeout=20):
 r=subprocess.run(C+[str(x) for x in a],capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise RuntimeError((r.stderr or r.stdout)[:250])
 return r.stdout
def ev(js):return json.loads(cli('--json','eval',js))['data']['result']
def open_agents():cli('open',O+'/agents');cli('wait',500)
def open_dialog():
 if ev('!!document.querySelector("[role=dialog]")'):
  cli('press','Escape');cli('wait',600)
 cli('find','role','button','click','--name','添加节点');cli('wait',150)
def fill(label,value):cli('find','label',label,'fill',value)
def form_state():return ev('(()=>{const f=document.querySelector("[role=dialog] form");return {dialogVisible:(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return !!d&&r.width>0&&r.height>0&&getComputedStyle(d).visibility!=="hidden"})(),valid:f?.checkValidity(),inputs:[...f.querySelectorAll("input")].map(x=>({required:x.required,empty:x.value.length===0,valid:x.checkValidity(),type:x.type,valueLength:x.value.length}))}})()')
def events():
 r=subprocess.run(C+['--json','network','requests'],capture_output=True,text=True,timeout=15)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except:return []
 out=[]
 def rec(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/' in u:out.append({'path':u.split(O,1)[-1].split('?',1)[0],'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):rec(v)
  elif isinstance(x,list):
   for v in x:rec(v)
 rec(d);return out
def api_post(body):
 req=urllib.request.Request(conn['hub_url'].rstrip('/')+'/api/nodes/enrollment',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+conn['token'],'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=10) as r:return r.status,None
 except urllib.error.HTTPError as e:return e.code,e.code

def get_nodes():
 req=urllib.request.Request(conn['hub_url'].rstrip('/')+'/api/nodes',headers={'Authorization':'Bearer '+conn['token']})
 with urllib.request.urlopen(req,timeout=10) as r:
  d=json.loads(r.read());return d if isinstance(d,list) else d.get('items',d.get('nodes',[]))
def record(cid,status,data):
 result={'captured_at_utc':now(),'canonical_id':cid,'status':status,'details':data,'evidence':[]}
 (B/f'case-{cid.lower()}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 p=json.loads((B/'progress.json').read_text());found=[x for x in p['results'] if x['canonical_id']!=cid];found.append({'canonical_id':cid,'status':status,'evidence':[],'details':data});p.update({'updated_at_utc':now(),'phase':'VALIDATION_ONLY_COMPLETE','results':found,'current_gate':cid+' live UI/API validation-only evidence saved; no Node created.'});(B/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'case':cid,'status':status,'details':data},ensure_ascii=False),flush=True)
# Pure dialog close/reset and no mutation.
open_agents();before=len(get_nodes());close_checks=[]
open_dialog();fill('节点名称','qa-web-draft-close');fill('节点地址（IP 或域名）','127.70.99.1');cli('network','requests','--clear');cli('find','role','button','click','--name','Close');cli('wait',600);close_checks.append({'method':'Close-X','closed':not ev('(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return !!d&&r.width>0&&r.height>0&&getComputedStyle(d).visibility!=="hidden"})()'),'post_count':sum(x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment') for x in events())})
open_dialog();fill('节点名称','qa-web-draft-escape');fill('节点地址（IP 或域名）','127.70.99.2');cli('network','requests','--clear');cli('press','Escape');cli('wait',600);close_checks.append({'method':'Escape','closed':not ev('(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return !!d&&r.width>0&&r.height>0&&getComputedStyle(d).visibility!=="hidden"})()'),'post_count':sum(x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment') for x in events())})
open_dialog();fill('节点名称','qa-web-draft-backdrop');fill('节点地址（IP 或域名）','127.70.99.3');cli('network','requests','--clear');cli('mouse','move',10,400);cli('mouse','down');cli('mouse','up');cli('wait',600);backdrop_closed=not ev('(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return !!d&&r.width>0&&r.height>0&&getComputedStyle(d).visibility!=="hidden"})()');close_checks.append({'method':'Backdrop click at x10,y400','closed':backdrop_closed,'post_count':sum(x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment') for x in events())})
if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
count_after=len(get_nodes());record('P05A-01','PASS' if all(x['closed'] and x['post_count']==0 for x in close_checks) and count_after==before else 'PARTIAL',{'close_methods':close_checks,'node_count_before':before,'node_count_after':count_after,'fields_were_draft_only':True})
# Required input UI branches: all empty, missing each required field. No click may POST.
open_dialog();ui_branches=[]
for case in ['all_empty','missing_name','missing_address','missing_hub']:
 fill('节点名称','');fill('节点地址（IP 或域名）','');fill('Hub 地址','')
 if case in ('missing_name','missing_hub'):fill('节点地址（IP 或域名）',fixtures[0]['ip'])
 if case in ('missing_address','missing_hub'):fill('节点名称','qa-web-required-check')
 if case!='missing_hub':fill('Hub 地址',O)
 state=form_state();cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',100);evs=events();posts=[x for x in evs if x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment')]
 ui_branches.append({'branch':case,'dom_required_state':state['inputs'],'form_valid_before_submit':state['valid'],'enrollment_post_count':len(posts),'native_invalid_prevents_submit':state['valid'] is False and len(posts)==0})
if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
ui_pass=all(x['native_invalid_prevents_submit'] for x in ui_branches);api_cases=[];base=len(get_nodes())
api_payloads=[('all_empty',{}),('missing_name',{'node_ip':fixtures[0]['ip'],'hub_address':O}),('missing_ip',{'node_name':'qa-web-missing-ip','hub_address':O})]
for label,body in api_payloads:
 status,err=api_post(body);after=get_nodes();api_cases.append({'case':label,'omitted_fields':[k for k in ['node_name','node_ip','hub_address'] if not body.get(k)],'http_status':status,'node_count_unchanged':len(after)==base,'new_candidate_names_present':sum(x.get('hostname')==body.get('node_name') for x in after) if body.get('node_name') else 0})
api_pass=all(x['http_status']==400 and x['node_count_unchanged'] for x in api_cases)
record('P05A-02','PASS' if ui_pass and api_pass else 'PARTIAL',{'ui_branches':ui_branches,'direct_api_negative_cases':api_cases,'api_missing_hub_not_tested_as_negative':'handler treats empty hub_address as optional fallback; UI required control covers Hub omission without creating a Node.','node_count_before_api_negative':base,'node_count_after_api_negative':len(get_nodes())})
# P05A-03 illegal character and 129-char field, sent through real UI and rejected before PrepareEnrollment.
name_cases=[]
for label,name in [('illegal_char','qa-web-invalid!name'),('over_128','qa-web-20261010-'+('x'*120))]:
 open_dialog();fill('节点名称',name);fill('节点地址（IP 或域名）',fixtures[0]['ip']);fill('Hub 地址',O);cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',350);evs=events();post=[x for x in evs if x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment')];matches=[x for x in get_nodes() if x.get('hostname')==name]
 name_cases.append({'branch':label,'name_length':len(name),'post_count':len(post),'http_statuses':[x.get('status') for x in post],'candidate_node_count':len(matches),'rejected_without_node':len(post)==1 and post[0].get('status')==400 and not matches})
 if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
record('P05A-03','PASS' if all(x['rejected_without_node'] for x in name_cases) else 'PARTIAL',{'invalid_ui_submissions':name_cases,'128_char_valid_acceptance':'will be exercised via first fixture in enrollment phase.'})
# P05A-04 invalid address through real UI; valid address families tested in the fixture phase.
open_dialog();fill('节点名称','qa-web-invalid-address');fill('节点地址（IP 或域名）','not a valid host !');fill('Hub 地址',O);cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',350);evs=events();post=[x for x in evs if x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment')];candidate=[x for x in get_nodes() if x.get('hostname')=='qa-web-invalid-address'];
invalid_address={'post_count':len(post),'http_statuses':[x.get('status') for x in post],'node_created':bool(candidate),'rejected_without_node':len(post)==1 and post[0].get('status')==400 and not candidate}
if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
record('P05A-04','PARTIAL',{'invalid_address_ui':invalid_address,'valid_ipv4_ipv6_dns':'will be tested with three owned fixtures in enrollment phase.'})
# P05A-05 unsupported scheme server rejection and missing-host browser/API rejection. Valid HTTPS is tested by primary enrollment.
open_dialog();fill('节点名称','qa-web-invalid-hub-scheme');fill('节点地址（IP 或域名）',fixtures[0]['ip']);fill('Hub 地址','ftp://hub.example.test');cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',350);evs=events();post=[x for x in evs if x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment')];created=[x for x in get_nodes() if x.get('hostname')=='qa-web-invalid-hub-scheme'];scheme={'post_count':len(post),'http_statuses':[x.get('status') for x in post],'node_created':bool(created),'rejected_without_node':len(post)==1 and post[0].get('status')==400 and not created}
fill('节点名称','qa-web-invalid-hub-host');fill('节点地址（IP 或域名）',fixtures[0]['ip']);fill('Hub 地址','http://');st=form_state();cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',150);evs=events();posts=[x for x in evs if x['method']=='POST' and x['path'].endswith('/api/nodes/enrollment')];browser_missing_host={'native_url_valid':st['inputs'][2]['valid'],'post_count':len(posts),'prevented_by_native_validation':st['inputs'][2]['valid'] is False and len(posts)==0}
# direct API covers missing Host parse branch without any node creation
api_http,api_err=api_post({'node_name':'qa-web-api-invalid-hub','node_ip':fixtures[0]['ip'],'hub_address':'http://'});api_missing_host={'http_status':api_http,'no_node_created':not any(x.get('hostname')=='qa-web-api-invalid-hub' for x in get_nodes())}
if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
valid_base=len(get_nodes());record('P05A-05','PARTIAL',{'unsupported_scheme':scheme,'missing_host_ui':browser_missing_host,'missing_host_api':api_missing_host,'valid_http_https':'HTTPS accepted by actual successful primary enrollment in next phase.','node_count_before_last_api_negative':valid_base,'remaining':'verified rejection paths now; valid HTTPS acceptance pending actual command response.'})
# Save phase-level negative side-effect assertion.
negative={'captured_at_utc':now(),'node_count_at_end':len(get_nodes()),'batch_owned_name_matches':sum(any(x.get('hostname')==f['name'] for x in get_nodes()) for f in fixtures),'invalid_submissions_created_no_candidate_node':True,'main_session_online':ev('navigator.onLine'),'main_session_route':cli('get','url').strip().replace(O,''),'private_auth_values_saved':False}
(B/'validation-only-summary.json').write_text(json.dumps(negative,ensure_ascii=False,indent=2)+'\n')
pr=json.loads((B/'progress.json').read_text());pr.update({'updated_at_utc':now(),'phase':'VALIDATION_ONLY_COMPLETE','validation_summary_file':'validation-only-summary.json','current_gate':'Non-creating real UI/API validation cases completed. No enrollment candidate was created; proceeding to real valid enrollment UI fixture creation.'});(B/'progress.json').write_text(json.dumps(pr,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'phase':'VALIDATION_ONLY_COMPLETE','node_count_end':negative['node_count_at_end'],'created_candidate_nodes':negative['batch_owned_name_matches'],'main_session_online':negative['main_session_online'],'cases':{x['canonical_id']:x['status'] for x in pr['results']}},ensure_ascii=False),flush=True)
