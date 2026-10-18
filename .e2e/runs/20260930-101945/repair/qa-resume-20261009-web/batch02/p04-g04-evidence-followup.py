import json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02');S='qawebsecure20261009';H='192.168.100.209';O='http://192.168.100.209:18080';N='qa-repair-20260930-101945-native-3';ID='37989297-3bc8-4c0c-b302-7c6efbdba630';C=['agent-browser','--session',S,'--allowed-domains',H,'--args','--unsafely-treat-insecure-origin-as-secure='+O]
def cli(*a):
 r=subprocess.run(C+[str(x) for x in a],text=True,capture_output=True,timeout=20)
 if r.returncode:raise RuntimeError(str(a[0])+':'+(r.stderr or r.stdout)[:280])
 return r.stdout
def ev(js):
 for attempt in range(3):
  try:return json.loads(cli('--json','eval',js))['data']['result']
  except Exception:
   if attempt==2:raise
   time.sleep(.25)
def shot(name):cli('screenshot',str((OUT/name).resolve()))
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
# P04-02 exact live route/list/detail link oracle.
routes=[]
for path in ['/nodes','/agents']:
 cli('open',O+path);cli('wait',900)
 d=ev('(()=>({path:location.pathname,tableVisible:!!document.querySelector("main table"),rowCount:document.querySelectorAll("main table tbody tr").length,firstHref:document.querySelector("main table tbody tr a")?.getAttribute("href")||null,heading:[...document.querySelectorAll("h1")].map(x=>x.innerText).join(" ")}))()')
 routes.append(d);shot('p04-02-'+path.strip('/')+'-live.png')
cli('open',O+'/agents');cli('wait',700);cli('find','role','link','click','--name',N,'--exact');cli('wait',900)
detail=ev("""({path:location.pathname,heading:document.querySelector('h1')?.innerText||'',backHref:[...document.querySelectorAll('main a')].find(a=>a.getAttribute('href')==='/agents')?.getAttribute('href')||null,mainVisible:!!document.querySelector('main'),nodeIdInPath:location.pathname==='/agents/37989297-3bc8-4c0c-b302-7c6efbdba630'})""")
shot('p04-02-native3-detail-live.png')
p04={'captured_at':now(),'canonical_id':'P04-02','routes':routes,'detail_after_real_hostname_click':detail,'assertions':{'both_list_routes_show_rows':all(x['tableVisible'] and x['rowCount']>0 for x in routes),'all_first_row_detail_links_use_agents_canonical':all(isinstance(x['firstHref'],str) and x['firstHref'].startswith('/agents/') for x in routes),'native3_hostname_click_opens_expected_detail':detail['nodeIdInPath'],'detail_back_link_canonical_agents':detail['backHref']=='/agents'},'status':'PASS' if all(x['tableVisible'] and x['rowCount']>0 and isinstance(x['firstHref'],str) and x['firstHref'].startswith('/agents/') for x in routes) and detail['nodeIdInPath'] and detail['backHref']=='/agents' else 'PARTIAL','evidence':['p04-02-nodes-live.png','p04-02-agents-live.png','p04-02-native3-detail-live.png']}
(OUT/'p04-02-live-route-oracle.json').write_text(json.dumps(p04,ensure_ascii=False,indent=2)+'\n')
# G04-06 precise responsive interaction sample; only tested Node list and native-3 detail.
layout=[]
for w,h in [(1366,768),(1440,900),(390,844)]:
 cli('set','viewport',w,h);cli('open',O+'/agents');cli('wait',850)
 dims=ev('(()=>{const r=e=>{const b=e?.getBoundingClientRect();return b?{x:Math.round(b.x),y:Math.round(b.y),right:Math.round(b.right),bottom:Math.round(b.bottom),w:Math.round(b.width),h:Math.round(b.height)}:null};const m=document.querySelector("main"),a=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="添加节点");const ar=a?.getBoundingClientRect();return {path:location.pathname,viewport:{w:innerWidth,h:innerHeight},scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,main:r(m),addNodeVisible:!!a&&ar.width>0&&ar.height>0,addNodeHit:!!(ar&&document.elementFromPoint(ar.x+ar.width/2,ar.y+ar.height/2)?.closest("button")===a),rowCount:document.querySelectorAll("main table tbody tr").length}})()')
 cli('find','role','button','click','--name','添加节点','--exact');cli('wait',250)
 modal=ev('(()=>{const d=document.querySelector("[role=dialog]"),r=d?.getBoundingClientRect();return {visible:!!d,rect:r?{x:Math.round(r.x),y:Math.round(r.y),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}:null,viewport:{w:innerWidth,h:innerHeight},documentScrollWidth:document.documentElement.scrollWidth,closeButtonVisible:[...document.querySelectorAll("[role=dialog] button")].some(b=>b.getAttribute("aria-label")==="Close")}})()')
 shot(f'g04-06-{w}x{h}-add-dialog.png');cli('press','Escape');cli('wait',200);closed=not ev('!!document.querySelector("[role=dialog]")')
 cli('find','placeholder','搜索主机名、地址或 Agent ID','fill','qa-web-layout-no-match');cli('wait',200);searched=ev('({text:(document.querySelector("main")?.innerText||"").includes("没有结果。"),value:document.querySelector("input[placeholder*=搜索]")?.value||""})');cli('find','placeholder','搜索主机名、地址或 Agent ID','click');cli('press','Control+a');cli('press','Backspace');cli('wait',200)
 cli('find','role','link','click','--name',N,'--exact');cli('wait',600)
 back=ev('(()=>{const a=[...document.querySelectorAll("main a")].find(a=>a.getAttribute("href")==="/agents"),r=a?.getBoundingClientRect();return {path:location.pathname,visible:!!a&&r.width>0&&r.height>0,hit:!!(r&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest("a")===a),scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth}})()')
 cli('find','role','link','click','--name','← 返回','--exact');cli('wait',400)
 layout.append({'viewport':dims['viewport'],'list':dims,'add_dialog':modal,'escape_closed_modal':closed,'search_no_result_feedback':searched,'detail_back':back,'back_navigation_route':cli('get','url').strip().replace(O,'')})
 shot(f'g04-06-{w}x{h}-detail.png')
g04={'captured_at':now(),'canonical_id':'G04-06','scope':'actual responsive checks limited to /agents list and owned native-3 detail. This is not a claim for every route.','measurements':layout,'assertions':{'no_horizontal_overflow':all(x['list']['scrollWidth']<=x['list']['clientWidth'] and x['add_dialog']['documentScrollWidth']<=x['add_dialog']['viewport']['w'] and x['detail_back']['scrollWidth']<=x['detail_back']['clientWidth'] for x in layout),'list_add_button_visible_and_hit_tested':all(x['list']['addNodeVisible'] and x['list']['addNodeHit'] for x in layout),'dialog_within_viewport':all(x['add_dialog']['visible'] and x['add_dialog']['rect']['x']>=0 and x['add_dialog']['rect']['right']<=x['add_dialog']['viewport']['w'] for x in layout),'escape_close_worked':all(x['escape_closed_modal'] for x in layout),'search_feedback':all(x['search_no_result_feedback']['text'] for x in layout),'detail_back_visible_and_hit_tested':all(x['detail_back']['visible'] and x['detail_back']['hit'] and x['back_navigation_route']=='/agents' for x in layout)},'evidence':[f'g04-06-{w}x{h}-{q}.png' for w,h in [(1366,768),(1440,900),(390,844)] for q in ['add-dialog','detail']]}
g04['status']='PASS' if all(g04['assertions'].values()) else 'PARTIAL'
(OUT/'g04-06-responsive-controls-oracle.json').write_text(json.dumps(g04,ensure_ascii=False,indent=2)+'\n')
summary=json.loads((OUT/'batch02-summary.json').read_text())
for row in summary['results']:
 if row['canonical_id']=='P04-02':row.update({'status':p04['status'],'details':p04['assertions'],'evidence':p04['evidence']+['p04-02-live-route-oracle.json']})
 if row['canonical_id']=='G04-06':row.update({'status':g04['status'],'details':{'scope':g04['scope'],'assertions':g04['assertions'],'measurements':layout},'evidence':g04['evidence']+['g04-06-responsive-controls-oracle.json']})
summary['main_session_url']='/agents';summary['main_session_online']=True
(OUT/'batch02-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
for cid,obj in [('p04-02-result',p04),('g04-06-result',g04)]:
 (OUT/f'case-{cid}.json').write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
progress=json.loads((OUT/'progress.json').read_text());progress.update({'updated_at':now(),'results':summary['results'],'current_gate':'Added missing P04-02 route/table/detail-href live oracle and screenshots. G04-06 now covers measured clickability/modal/search/back controls at three viewports on two Node routes only.'});(OUT/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'P04-02':p04['status'],'P04-02_assertions':p04['assertions'],'G04-06':g04['status'],'G04-06_assertions':g04['assertions']},ensure_ascii=False),flush=True)
