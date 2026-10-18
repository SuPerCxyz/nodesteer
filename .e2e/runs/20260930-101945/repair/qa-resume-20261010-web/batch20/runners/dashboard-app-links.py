import json,re,subprocess
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch20');S='qawebsecure20261010-b18-clean38181';O='http://127.0.0.1:38181'
def utc():return datetime.now(timezone.utc).isoformat()
def cli(*a):return subprocess.run(['agent-browser','--session',S,*a],capture_output=True,text=True,timeout=15,check=True).stdout.strip()
def eval_json(js):
 x=json.loads(cli('eval',js));return json.loads(x) if isinstance(x,str) else x
def logs():
 text=cli('network','requests','--filter','/api/applications');out=[]
 for line in text.splitlines():
  m=re.search(r'\]\s+(GET|POST|PUT|DELETE)\s+(https?://\S+).*?\s(\d{3})\s*$',line)
  if not m:continue
  method,url,code=m.groups();u=urlparse(url)
  if u.netloc!='127.0.0.1:38181':continue
  parts=u.path.split('/');kind='/api/applications'
  if len(parts)>3:kind='/api/applications/<id>/state' if parts[-1]=='state' else '/api/applications/<id>'
  out.append({'method':method,'path_class':kind,'status':int(code)})
 return out
# Actual sidebar navigation to the Dashboard.
cli('network','requests','--clear');cli('click','a[data-sidebar="menu-button"][href="/"]');cli('wait','900')
# Identify only the Application Status card's View All anchor: inside main, exact path and unique.
view_count=eval_json('JSON.stringify(document.querySelectorAll("main a[href=\\"/applications\\"]").length)')
if view_count!=1:raise SystemExit('application_viewall_link_not_unique')
cli('network','requests','--clear');cli('click','main a[href="/applications"]');cli('wait','700');view_path=urlparse(cli('get','url')).path
cli('network','requests','--clear');cli('reload');cli('wait','900');view_reload_path=urlparse(cli('get','url')).path;view_dom=eval_json('JSON.stringify({main:!!document.querySelector("main"),alerts:document.querySelectorAll("main [role=alert]").length,rows:document.querySelectorAll("main tbody tr").length})');view_requests=logs()
view_ok=view_path=='/applications' and view_reload_path=='/applications' and view_dom['main'] and view_dom['alerts']==0 and any(x['path_class']=='/api/applications' and x['status']==200 for x in view_requests)
# Use actual dashboard card record link, then independently bind href id to the actual list and detail APIs in page memory.
cli('click','a[data-sidebar="menu-button"][href="/"]');cli('wait','900')
setup='''(()=>{window.__qaP0304=null;const t=localStorage.getItem("nodesteer_token");fetch("/api/applications",{headers:{Authorization:`Bearer ${t}`}}).then(async r=>{const x=await r.json();const a=Array.isArray(x)?x:(x.data||x.items||[]);const hs=[...document.querySelectorAll("main a[href^=\\"/applications/\\"]")].map(e=>e.getAttribute("href")).filter(h=>h!=="/applications/new");const id=hs.length===1?hs[0].split("/").pop():null;window.__qaP0304={listStatus:r.status,listCount:a.length,uiLinkCount:hs.length,uiLinkMatchesApi:!!id&&a.some(z=>z.id===id),linkId:id}}).catch(()=>window.__qaP0304={listStatus:0}) ;return "started"})()'''
cli('eval',setup);cli('wait','350');pre=eval_json('JSON.stringify(window.__qaP0304)')
if pre['listStatus']!=200 or pre['uiLinkCount']!=1 or not pre['uiLinkMatchesApi']:raise SystemExit('application_card_link_oracle_failed')
cli('network','requests','--clear');cli('click','main a[href^="/applications/"]:not([href="/applications/new"])');cli('wait','1200');route=urlparse(cli('get','url')).path
# This follow-up GET uses the actual route id without printing or saving it.
js='''(()=>{window.__qaP0304Detail=null;const id=location.pathname.split("/").pop(),t=localStorage.getItem("nodesteer_token");Promise.all([fetch("/api/applications",{headers:{Authorization:`Bearer ${t}`}}).then(async r=>({status:r.status,data:await r.json()})),fetch(`/api/applications/${id}`,{headers:{Authorization:`Bearer ${t}`}}).then(async r=>({status:r.status,data:await r.json()}))]).then(async ([l,d])=>{const a=Array.isArray(l.data)?l.data:(l.data.data||l.data.items||[]);const h=Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode(id)))).map(v=>v.toString(16).padStart(2,"0")).join("");window.__qaP0304Detail={listStatus:l.status,listCount:a.length,listHasRouteId:a.some(z=>z.id===id),detailStatus:d.status,responseIdMatchesRoute:d.data.id===id,routeIdSha256:h,main:!!document.querySelector("main"),alerts:document.querySelectorAll("main [role=alert]").length}}).catch(()=>window.__qaP0304Detail={listStatus:0}) ;return "started"})()'''
cli('eval',js);cli('wait','400');detail=eval_json('JSON.stringify(window.__qaP0304Detail)');app_requests=logs()
passed=view_ok and route.startswith('/applications/') and detail['listStatus']==200 and detail['listHasRouteId'] and detail['detailStatus']==200 and detail['responseIdMatchesRoute'] and detail['main'] and detail['alerts']==0
obj={'case_id':'P03-04','captured_at_utc':utc(),'source_refs':['web/src/features/overview/index.tsx:288-324','web/src/features/catalog/index.tsx:1513-1522; 1540-1560','web/src/features/editors/index.tsx:930-950'],'view_all':{'unique_main_link':view_count==1,'clicked':True,'route_after_click':view_path,'route_after_reload':view_reload_path,'main':view_dom['main'],'alerts':view_dom['alerts'],'rows':view_dom['rows'],'api_requests':view_requests},'dashboard_application_record':{'list_api_status':pre['listStatus'],'list_count':pre['listCount'],'ui_link_count':pre['uiLinkCount'],'link_id_matches_api_list':pre['uiLinkMatchesApi'],'clicked_actual_ui_link':True,'detail_route_id_sha256':detail['routeIdSha256'],'detail_route_matches_list_id':detail['listHasRouteId'],'detail_get_status':detail['detailStatus'],'detail_response_id_matches_route':detail['responseIdMatchesRoute'],'main':detail['main'],'alerts':detail['alerts'],'detail_api_requests':app_requests},'status':'PASS' if passed else 'PARTIAL','remaining_dimensions':[] if passed else ['Dashboard Application Status View All/link and exact application detail GET oracle incomplete.'],'server_writes':0,'runs':0,'raw_application_names_or_ids_saved':False,'response_bodies_or_tokens_saved':False}
(B/'evidence/P03-04.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
rp=B/'results.json';r=json.loads(rp.read_text());r['cases']=[x for x in r['cases'] if x['case_id']!='P03-04']+[{'case_id':'P03-04','status':obj['status'],'evidence':['evidence/P03-04.json'],'actual':'Clicked Dashboard Application Status View All and reloaded /applications with actual GET 200; clicked the real dashboard application link, whose route ID matched the API list ID and detail response ID.','remaining_dimensions':obj['remaining_dimensions']}];r['counts']={'PASS':sum(x['status']=='PASS' for x in r['cases']),'PARTIAL':sum(x['status']=='PARTIAL' for x in r['cases']),'FAIL':sum(x['status']=='FAIL' for x in r['cases']),'NOT_RUN':8-len(r['cases'])};r['updated_at_utc']=utc();rp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
pp=B/'progress.json';q=json.loads(pp.read_text());q.update({'updated_at_utc':utc(),'phase':'P03_04_'+obj['status'],'counts':r['counts'],'current_main_route':route});c=next(x for x in q['cases'] if x['case_id']=='P03-04');c.update({'status':obj['status'],'stage':'complete','evidence':['evidence/P03-04.json'],'remaining_dimensions':obj['remaining_dimensions']});q['next']='P03-06: Dashboard Recent Executions View All and actual Execution detail link; verify live Task link/404 separately.';pp.write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'case':'P03-04','status':obj['status'],'view_all_route':view_path,'app_count':pre['listCount'],'href_matches_api':pre['uiLinkMatchesApi'],'detail_get':detail['detailStatus'],'detail_id_matches':detail['responseIdMatchesRoute'],'alerts':detail['alerts']}))
