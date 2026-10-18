import json,subprocess,urllib.request,hashlib
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02');pvt=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text())
ORIGIN='http://192.168.100.209:18080';HOST='192.168.100.209';S='qawebsecure20261009';C=['agent-browser','--session',S,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def cli(session,*args):
 c=['agent-browser','--session',session,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]+[str(x) for x in args]
 r=subprocess.run(c,text=True,capture_output=True,timeout=20)
 if r.returncode:raise RuntimeError('agent-browser '+str(args[0])+' failed: '+r.stderr[:160])
 return r.stdout
def ev(session,js):return json.loads(cli(session,'--json','eval',js))['data']['result']
def api(path):
 rq=urllib.request.Request(pvt['hub_url'].rstrip('/')+path,headers={'Authorization':'Bearer '+pvt['token']})
 with urllib.request.urlopen(rq,timeout=10) as r:return r.status,json.loads(r.read())
def write(name,d): (OUT/name).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
# P01-07 actual disabled OIDC sign-in UI
code,oidc=api('/api/oidc/state');enabled=bool(oidc.get('enabled') or oidc.get('Enabled'))
login='qaweblogout20261009v';url=cli(login,'get','url').strip();snapshot=cli(login,'snapshot','-i');sso_visible=any(k.lower() in snapshot.lower() for k in ['sso','single sign','oidc','openid','saml'])
cli(login,'screenshot',str((OUT/'p01-07-oidc-disabled-sign-in.png').resolve()))
p01={'captured_at':now(),'canonical_id':'P01-07','oidc_state_http':code,'oidc_enabled':enabled,'sign_in_url':url.replace(ORIGIN,''),'sso_cta_visible':sso_visible,'local_login_visible':('用户名' in snapshot and '密码' in snapshot),'status':'PASS' if code==200 and not enabled and not sso_visible and '用户名' in snapshot and '密码' in snapshot else 'PARTIAL','evidence':['p01-07-oidc-disabled-sign-in.png'],'private_auth_saved':False}
write('case-p01-07-verified.json',p01)
# P04-08 live filter and refresh/read-only pagination.
cli(S,'open',ORIGIN+'/agents');cli(S,'wait',900);cli(S,'network','requests','--clear');cli(S,'find','placeholder','搜索主机名、地址或 Agent ID','fill','qa-no-such-host-20261009');cli(S,'wait',500)
search=ev(S,'(()=>{const i=document.querySelector("input[placeholder*=搜索]");const b=document.querySelector("main")?.innerText||"";return {value:i?.value||null,rowCount:document.querySelectorAll("table tbody tr").length,tbody:document.querySelector("table tbody")?.innerText||"",bodyTail:b.slice(-250)}})()')
cli(S,'screenshot',str((OUT/'p04-08-no-match.png').resolve()))
cli(S,'find','placeholder','搜索主机名、地址或 Agent ID','click');cli(S,'press','Control+a');cli(S,'press','Backspace');cli(S,'wait',400);cli(S,'reload');cli(S,'wait',900)
reset=ev(S,'({searchValue:document.querySelector("input[placeholder*=搜索]")?.value||"",rows:document.querySelectorAll("table tbody tr").length,footer:(document.querySelector("main")?.innerText||"").slice(-160)})')
cli(S,'find','role','combobox','click');opts=cli(S,'snapshot','-i');sizes=ev(S,'[...document.querySelectorAll("[role=option]")].map(e=>e.innerText.trim())');cli(S,'press','Escape');
pager=ev(S,'[...document.querySelectorAll("button")].filter(b=>/下一页|next page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||""))).map(b=>({disabled:b.disabled,text:b.innerText.trim()}))')
cli(S,'screenshot',str((OUT/'p04-08-after-reset-refresh.png').resolve()))
status='PASS' if search['value']=='qa-no-such-host-20261009' and search['rowCount']==0 and reset['searchValue']=='' and reset['rows']>0 else 'PARTIAL'
p04={'captured_at':now(),'canonical_id':'P04-08','search':{'entered_query_length':len(search['value'] or ''),'row_count':search['rowCount'],'empty_table_feedback_length':len(search['tbody']),'body_tail':search['bodyTail']},'reset_and_refresh':reset,'page_size_options':sizes,'next_page_controls':pager,'pagination_dimension':'PARTIAL; dataset 6 rows and minimum page size 10, so next page is disabled and second-page boundary cannot be triggered without changing data or limit.','status':'PARTIAL' if not any(not x['disabled'] for x in pager) else status,'evidence':['p04-08-no-match.png','p04-08-after-reset-refresh.png']}
write('case-p04-08-verified.json',p04)
# P05B detail fields and all sections; UI oracle is exact accessible/text content plus API response. Native-3 is this batch's permitted object.
node_id='37989297-3bc8-4c0c-b302-7c6efbdba630';cli(S,'set','viewport','1440','900');cli(S,'open',ORIGIN+'/agents/'+node_id);cli(S,'wait',1500)
ui=ev(S,'(()=>{const m=document.querySelector("main");return {path:location.pathname,text:m?.innerText||"",html:m?.innerHTML||"",noTabs:!m?.querySelector("[role=tablist]"),headings:[...m.querySelectorAll("h2,h3")].map(x=>x.innerText.trim())}})()')
code,node=api('/api/nodes/'+node_id)
# labels + displayed sub-tree presence; compare stable raw values only where rendered verbatim.
labels={'id':'节点 ID','agent_id':'Agent ID','ip':'IP','os':'系统','agent_version':'Agent 版本','arch':'架构','deployment_mode':'部署模式','global_revision':'全局修订号','sync_status':'同步状态','last_seen':'最后在线'}
flat=ui['text'];fields={}
for k,label in labels.items():
 val=node.get(k)
 label_ok=label.lower() in flat.lower()
 if k in {'sync_status','last_seen'}:value_ok=label_ok and bool(val) and label in flat
 elif val is None:value_ok=label_ok
 else:value_ok=str(val).lower() in flat.lower()
 fields[k]={'api_field_present':k in node,'label_visible':label_ok,'display_value_matches_or_is_rendered':value_ok,'value_sha256':hashlib.sha256(str(val).encode()).hexdigest() if val is not None else None}
section_labels=['概览','清单','能力','标签','任务','调度','应用','执行'];sections={x:x in flat for x in section_labels}
cli(S,'screenshot',str((OUT/'p05b-native3-detail.png').resolve()))
write('case-p05b-detail-oracle.json',{'captured_at':now(),'native3_only':True,'api_http':code,'sections':sections,'no_tabs':ui['noTabs'],'fields':fields,'private_auth_saved':False})
# Update existing per-case records with corrected scoped observations and summary statuses.
summary= json.loads((OUT/'batch02-summary.json').read_text())
for r in summary['results']:
 if r['canonical_id']=='P01-07':
  r.update({'status':p01['status'],'details':{'oidc_state_http':code,'enabled':enabled,'sso_cta_visible':sso_visible,'local_login_visible':p01['local_login_visible']},'evidence':p01['evidence']})
 if r['canonical_id']=='P04-08':
  r.update({'status':p04['status'],'details':{'no_match_row_count':search['rowCount'],'search_value_length':len(search['value'] or ''),'rows_after_reset_refresh':reset['rows'],'page_size_options':sizes,'next_page_enabled':any(not x['disabled'] for x in pager)},'evidence':p04['evidence']})
 if r['canonical_id']=='P05B-01':
  r.update({'status':'PASS' if all(sections.values()) and ui['noTabs'] else 'PARTIAL','details':{'actual_localized_sections':sections,'no_tabs':ui['noTabs'],'heading_labels':ui['headings']},'evidence':['p05b-native3-detail.png','case-p05b-detail-oracle.json']})
 if r['canonical_id']=='P05B-02':
  r.update({'status':'PASS' if code==200 and all(x['api_field_present'] and x['label_visible'] and x['display_value_matches_or_is_rendered'] for x in fields.values()) else 'PARTIAL','details':{'api_http':code,'fields':fields},'evidence':['p05b-native3-detail.png','case-p05b-detail-oracle.json']})
write('batch02-summary.json',summary)
progress=json.loads((OUT/'progress.json').read_text());progress.update({'phase':'BATCH02_PARTIAL_COMPLETE','updated_at':now(),'results':summary['results'],'current_gate':'Supplemental exact UI checks completed for disabled OIDC, search/no-result/reset/refresh/pagination bound, and native-3 detail fields/sections.','summary_file':'batch02-summary.json','next_actions':summary['next_batch_plan']});write('progress.json',progress)
print(json.dumps({'P01-07':p01,'P04-08':{'status':p04['status'],'search_rows':search['rowCount'],'restored_rows':reset['rows'],'sizes':sizes,'next_enabled':any(not x['disabled'] for x in pager)},'P05B-01':{'sections':sections,'no_tabs':ui['noTabs']},'P05B-02':{'status':next(r['status'] for r in summary['results'] if r['canonical_id']=='P05B-02'),'fields':fields}},ensure_ascii=False),flush=True)
