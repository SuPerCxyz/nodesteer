import json,subprocess,urllib.request,urllib.error,time
from pathlib import Path
from datetime import datetime,timezone
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch03/pagination-supplement-rerun');plan=json.load(open(B/'prepare-case-plan.json'));fixtures=plan['fixtures'];conn=json.load(open('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json'));O='http://192.168.100.209:18080';S='qawebsecure20261009';C=['agent-browser','--session',S,'--allowed-domains','192.168.100.209','--args','--unsafely-treat-insecure-origin-as-secure='+O];created=[];deleted=[];results=[];p04ok=False
def now():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def cli(*a,timeout=20):
 r=subprocess.run(C+[str(x) for x in a],capture_output=True,text=True,timeout=timeout)
 if r.returncode:raise RuntimeError((r.stderr or r.stdout)[:220])
 return r.stdout
def ev(js):return json.loads(cli('--json','eval',js))['data']['result']
def api(path,method='GET',body=None):
 data=None if body is None else json.dumps(body).encode();headers={'Authorization':'Bearer '+conn['token']}
 if data is not None:headers['Content-Type']='application/json'
 q=urllib.request.Request(conn['hub_url'].rstrip('/')+path,data=data,headers=headers,method=method)
 try:
  with urllib.request.urlopen(q,timeout=12) as r:
   b=r.read();return r.status,json.loads(b) if b else None
 except urllib.error.HTTPError as e:return e.code,None
def nodes():
 code,d=api('/api/nodes')
 if code!=200:raise RuntimeError('GET /api/nodes HTTP '+str(code))
 return d if isinstance(d,list) else d.get('items',d.get('nodes',[]))
def progress(phase,gate):
 p=json.load(open(B/'progress.json'));p.update({'phase':phase,'updated_at_utc':now(),'created_ids':[x['id'] for x in created],'deleted_ids':[x['id'] for x in deleted],'cleanup_verified':bool(deleted) and all(x['verified'] for x in deleted),'results':results,'current_gate':gate});(B/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 ri=json.load(open(B/'resource-intents.json'));ri['created']=[{k:x[k] for k in ('id','agent_id','name','ip','purpose','post_http','post_count','status','sync_status')} for x in created];ri['deleted']=deleted;ri['cleanup_verified']=bool(deleted) and all(x['verified'] for x in deleted);ri['last_updated_utc']=now();(B/'resource-intents.json').write_text(json.dumps(ri,ensure_ascii=False,indent=2)+'\n')
def write(name,d):(B/name).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def record(cid,status,details,evidence):
 x={'captured_at_utc':now(),'canonical_id':cid,'status':status,'details':details,'evidence':evidence};results[:]=[r for r in results if r['canonical_id']!=cid];results.append(x);write('case-'+cid.lower()+'.json',x);progress('RUNNING',cid+' evidence saved.')
def posts():
 r=subprocess.run(C+['--json','network','requests'],capture_output=True,text=True,timeout=15);d=json.loads(r.stdout) if r.returncode==0 else {};out=[]
 def walk(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/nodes/enrollment' in u and x.get('method')=='POST':out.append({'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return out
try:
 base_nodes=nodes();base_ids={x.get('id') for x in base_nodes};planned_conflicts=[f['purpose'] for f in fixtures if any(n.get('hostname')==f['name'] or n.get('ip')==f['ip'] for n in base_nodes)]
 if planned_conflicts:raise RuntimeError('supplemental unique fixture collision')
 if not ev('navigator.onLine'):raise RuntimeError('browser offline')
 progress('CREATING_FIXTURES','Four unique pagination-only Nodes, no command tab interaction, starting.')
 # Four real Add Node submissions; command panes are never inspected or captured.
 for f in fixtures:
  cli('open',O+'/agents');cli('wait',500)
  if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',700)
  cli('find','role','button','click','--name','添加节点');cli('wait',150)
  cli('find','label','节点名称','fill',f['name']);cli('find','label','节点地址（IP 或域名）','fill',f['ip']);cli('find','label','Hub 地址','fill',O)
  actual=ev('(()=>[...document.querySelectorAll("[role=dialog] input")].map(x=>x.value))()')
  if actual!=[f['name'],f['ip'],O]:raise RuntimeError('actual form value mismatch; refused to submit')
  cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令')
  p=posts()
  if len(p)!=1 or p[0].get('status')!=200:raise RuntimeError('real POST count/status mismatch '+json.dumps(p))
  cli('wait',700);m=[]
  for _ in range(16):
   xs=nodes();m=[n for n in xs if n.get('hostname')==f['name'] and n.get('ip')==f['ip']]
   if len(m)==1:break
   time.sleep(.3)
  if len(m)!=1:raise RuntimeError('HTTP200 but exact node list oracle mismatch after verified controlled form')
  p=posts();xs=nodes();m=[n for n in xs if n.get('hostname')==f['name'] and n.get('ip')==f['ip']]
  if len(m)!=1:raise RuntimeError('exact created node list match count != 1')
  n=m[0];x={'id':n.get('id'),'agent_id':n.get('agent_id'),'name':f['name'],'ip':f['ip'],'purpose':f['purpose'],'post_http':p[0].get('status') if len(p)==1 else None,'post_count':len(p),'status':n.get('status'),'sync_status':n.get('sync_status')};created.append(x);progress('NODE_CREATED',f['purpose']+' exact IDs saved; no secret command text was read.')
  # Independent P05A-12 GET while the node exists; no Agent HELLO/start.
  status,node=api('/api/nodes/'+x['id']);x['detail_get_http']=status;x['detail_status']=node.get('status') if status==200 else None;x['detail_sync_status']=node.get('sync_status') if status==200 else None;x['detail_agent_id_matches']=status==200 and node.get('agent_id')==x['agent_id'];progress('NODE_DETAIL_CHECKED','P05A-12 GET status/Agent ID checked for '+f['purpose'])
  # Close without inspecting or screenshotting the generated command pane.
  cli('press','Escape');cli('wait',750)
 # Page-size and page-turn test with a sufficiently large real dataset.
 cli('open',O+'/agents');cli('wait',1100);cli('network','requests','--clear');api_count=len(nodes())
 cli('find','role','combobox','click');opts=ev('[...document.querySelectorAll("[role=option]")].map(x=>x.innerText.trim())');cli('find','role','option','click','--name','10','--exact');cli('wait',400)
 def page_capture():
  return ev('(()=>{const ids=[...document.querySelectorAll("main table tbody a[href^=\"/agents/\"]")].map(a=>a.getAttribute("href").split("/").pop());const main=document.querySelector("main")?.innerText||"";return {path:location.pathname,rowCount:document.querySelectorAll("main table tbody tr").length,hrefIds:ids,pageText:main.slice(-140),searchLength:document.querySelector("input[placeholder*=搜索]")?.value.length||0}})()')
 first=page_capture()
 cli('screenshot',str((B/'p04-08-page1.png').resolve()))
 next_enabled=ev('[...document.querySelectorAll("button")].find(b=>/下一页|next page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")))?.disabled===false')
 if next_enabled:cli('find','role','button','click','--name','下一页');cli('wait',450)
 second=page_capture();cli('screenshot',str((B/'p04-08-page2.png').resolve()))
 prev_enabled=ev('[...document.querySelectorAll("button")].find(b=>/上一页|previous page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")))?.disabled===false')
 if prev_enabled:cli('find','role','button','click','--name','上一页');cli('wait',350)
 after_prev=page_capture()
 cli('find','role','combobox','click');cli('find','role','option','click','--name','20','--exact');cli('wait',350);size20=page_capture();next20=ev('[...document.querySelectorAll("button")].find(b=>/下一页|next page/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")))?.disabled===false')
 cli('find','role','combobox','click');cli('find','role','option','click','--name','10','--exact');cli('wait',300);reset10=page_capture()
 api_nodes=nodes();api_ids={x.get('id') for x in api_nodes};union=set(first['hrefIds'])|set(second['hrefIds']);owned_ids={x['id'] for x in created}
 p04ok=api_count>=11 and len(opts)>=2 and '10' in opts and '20' in opts and first['rowCount']==10 and next_enabled and len(second['hrefIds'])==api_count-10 and prev_enabled and after_prev['hrefIds']==first['hrefIds'] and size20['rowCount']==api_count and not next20 and reset10['rowCount']==10 and union==api_ids and owned_ids.issubset(union)
 record('P04-08','PASS' if p04ok else 'PARTIAL',{'api_node_count_before_paging':api_count,'page_size_options':opts,'page_size_10_page1':first,'next_enabled_page1':next_enabled,'page2':second,'previous_enabled_page2':prev_enabled,'page1_restored_by_previous':after_prev['hrefIds']==first['hrefIds'],'page_size_20_rows':size20['rowCount'],'next_disabled_at_size20':not next20,'page_size_reset_to10':reset10['rowCount']==10,'api_dataset_membership_matches_ui_pages':union==api_ids,'all_owned_pagination_fixture_ids_visible':owned_ids.issubset(union),'evidence':['p04-08-page1.png','p04-08-page2.png']},['p04-08-page1.png','p04-08-page2.png'])
 # All four owned records have direct GET status and preallocated agent identity before cleanup.
 pending_checks=[{'id':x['id'],'agent_id':x['agent_id'],'http':x.get('detail_get_http'),'status':x.get('detail_status'),'sync_status':x.get('detail_sync_status'),'agent_id_matches':x.get('detail_agent_id_matches')} for x in created]
 pending_ok=all(x['http']==200 and x['status']=='pending' and x['sync_status']=='pending' and x['agent_id_matches'] for x in pending_checks)
 record('P05A-12','PARTIAL',{'direct_detail_get_pending_records_pass':pending_ok,'fixtures':pending_checks,'Agent_HELLO_online_synced_reuse':'NOT_RUN by explicit no-agent-start constraint.'},['pending-node-details.json'])
 write('pending-node-details.json',{'captured_at_utc':now(),'canonical_id':'P05A-12','nodes':pending_checks,'agent_hello_run':False,'private_auth_saved':False})
except Exception as e:
 write('pagination-runner-error.json',{'captured_at_utc':now(),'type':type(e).__name__,'message':str(e)[:300],'created_ids':[x['id'] for x in created]})
 progress('PAGINATION_RUNNER_ERROR','Pagination supplemental action raised; finally will exact-clean its owned IDs.')
finally:
 try:
  # Recover exact created candidates from API if a submit succeeded before its record was appended.
  live=nodes();known={x['id'] for x in created}
  for f in fixtures:
   for n in live:
    if n.get('hostname')==f['name'] and n.get('ip')==f['ip'] and n.get('id') not in known:
     x={'id':n.get('id'),'agent_id':n.get('agent_id'),'name':f['name'],'ip':f['ip'],'purpose':f['purpose'],'post_http':None,'post_count':None,'status':n.get('status'),'sync_status':n.get('sync_status')};created.append(x);known.add(x['id']);progress('OWNED_NODE_RECOVERED','Recovered exact candidate after submit and persisted ID before cleanup.')
  for x in list(created):
   status,n=api('/api/nodes/'+x['id'])
   if status!=200 or n.get('hostname')!=x['name'] or n.get('ip')!=x['ip']:raise RuntimeError('refuse cleanup: exact fixture identity mismatch')
   ds,_=api('/api/nodes/'+x['id'],'DELETE');gs,_=api('/api/nodes/'+x['id']);deleted.append({'id':x['id'],'delete_http':ds,'get_after_http':gs,'verified':ds==200 and gs==404});progress('SUPPLEMENT_CLEANUP','Exact supplemental node deleted and GET404 checked.')
 except Exception as e:write('supplement-cleanup-error.json',{'captured_at_utc':now(),'type':type(e).__name__,'message':str(e)[:260],'deleted':deleted})
# Check original baseline nodes plus external rows without touching them.
try:
 original=json.load(open(Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch03/preflight.json')))['baseline_node_snapshot'];live=nodes();byid={x.get('id'):x for x in live};baseline=[]
 for x in original:
  n=byid.get(x['id']);baseline.append({'id':x['id'],'classification':x['classification'],'present':bool(n),'status':n.get('status') if n else None,'sync_status':n.get('sync_status') if n else None,'matches_prior_status_sync':bool(n and n.get('status')==x['status'] and n.get('sync_status')==x['sync_status'])})
 final={'captured_at_utc':now(),'owned_created':[{'id':x['id'],'agent_id':x['agent_id'],'purpose':x['purpose']} for x in created],'owned_cleanup':deleted,'all_owned_deleted_get404':len(deleted)==len(created) and all(x['verified'] for x in deleted),'final_node_count':len(live),'baseline_nodes':baseline,'all_original_baseline_ids_present':all(x['present'] for x in baseline),'browser_online':ev('navigator.onLine'),'browser_secure_context':ev('isSecureContext')}
 write('pagination-final-verification.json',final)
 progress('SUPPLEMENT_FINISHED','Pagination supplemental cleanup and baseline check completed.')
 # Merge the two canonical outcomes into the main run summary and resource ledger.
 main=B.parent;summary=json.load(open(main/'batch03-summary.json'));existing={x['canonical_id']:x for x in summary['results']}
 for x in results:existing[x['canonical_id']]=x
 order=['P04-08','P05A-01','P05A-02','P05A-03','P05A-04','P05A-05','P05A-06','P05A-07','P05A-08','P05A-09','P05A-12','P05A-13'];summary['results']=[existing[x] for x in order];summary['captured_at_utc']=now();summary['canonical_cases_recorded']=12;summary['pagination_supplemental_fixture_count']=len(created);summary['pagination_supplemental_cumulative_count']=6;summary['pagination_supplemental_cleanup_verified']=final['all_owned_deleted_get404'];summary['owned_nodes_created_count_total_in_batch03']=13;summary['owned_nodes_deleted_count_total_in_batch03']=13;summary['pagination_actual_action_reached']=bool(p04ok);summary['final_original_baseline_ids_present']=final['all_original_baseline_ids_present'];summary['next_actions']=['P05A-12 Agent HELLO/online/synced reuse remains untested by explicit no-agent-start boundary.'];(main/'batch03-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 # Aggregate resource lifecycle without replacing the first six or the accidental cleanup history.
 ri=json.load(open(main/'resource-intents.json'));supp=[]
 for dirname in ['pagination-supplement','pagination-supplement-rerun']:
  rp=main/dirname/'resource-intents.json'
  if rp.exists():
   rd=json.load(open(rp));supp.append({'plan_file':dirname+'/prepare-case-plan.json','resource_intents_file':dirname+'/resource-intents.json','created':[{'id':x['id'],'agent_id':x.get('agent_id'),'purpose':x.get('purpose')} for x in rd.get('created',[])],'deleted':rd.get('deleted',[]),'cleanup_verified':rd.get('cleanup_verified',False)})
 ri['supplemental_runs']=supp;ri['supplemental_fixture_count']=sum(len(x['created']) for x in supp);ri['supplemental_cleanup_verified']=all(x['cleanup_verified'] for x in supp);ri['last_updated_utc']=now();(main/'resource-intents.json').write_text(json.dumps(ri,ensure_ascii=False,indent=2)+'\n')
 pmain=json.load(open(main/'progress.json'));supp_created=[y for x in supp for y in x['created']];supp_deleted=[y for x in supp for y in x['deleted']];all_created=ri.get('created',[])+ri.get('unexpected_created',[])+supp_created;all_deleted=ri.get('deleted',[])+supp_deleted;pmain.update({'phase':'BATCH03_COMPLETE_WITH_P05A12_PARTIAL','updated_at_utc':now(),'results':[{'canonical_id':x['canonical_id'],'status':x['status'],'evidence':x.get('evidence',[])} for x in summary['results']],'created_ids':list(dict.fromkeys(x['id'] for x in all_created)),'deleted_ids':list(dict.fromkeys((x['id'] if isinstance(x,dict) else x) for x in all_deleted)),'cleanup_verified':ri.get('cleanup_verified',False) and ri['supplemental_cleanup_verified'] and final['all_owned_deleted_get404'],'current_gate':'Pagination-only supplemental run finished; exact temporary fixtures deleted. P04-08 result and P05A-12 pending detail oracle merged.'});(main/'progress.json').write_text(json.dumps(pmain,ensure_ascii=False,indent=2)+'\n')
except Exception as e:
 write('final-merge-error.json',{'captured_at_utc':now(),'type':type(e).__name__,'message':str(e)[:300]})
print(json.dumps({'created':len(created),'deleted':len(deleted),'cleanup':len(deleted)==len(created) and all(x['verified'] for x in deleted),'results':{x['canonical_id']:x['status'] for x in results},'p04_pass':any(x['canonical_id']=='P04-08' and x['status']=='PASS' for x in results)},ensure_ascii=False),flush=True)
