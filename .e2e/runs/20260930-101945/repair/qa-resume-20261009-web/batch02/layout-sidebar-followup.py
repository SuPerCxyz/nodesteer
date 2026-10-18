import json,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02');S='qawebsecure20261009';H='192.168.100.209';O='http://192.168.100.209:18080';C=['agent-browser','--session',S,'--allowed-domains',H,'--args','--unsafely-treat-insecure-origin-as-secure='+O]
def cli(*a):
 r=subprocess.run(C+[str(x) for x in a],text=True,capture_output=True,timeout=18)
 if r.returncode:raise RuntimeError(str(a[0])+':'+r.stderr[:180])
 return r.stdout
def ev(js):return json.loads(cli('--json','eval',js))['data']['result']
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def snap(name):cli('screenshot',str((OUT/name).resolve()))
# Verify desktop sidebar toggles both real widths, independent of starting state label.
cli('set','viewport',1440,900);cli('open',O+'/');cli('wait',800)
def state():return ev('(()=>{const s=document.querySelector("[data-slot=sidebar]");const r=s?.getBoundingClientRect();const b=[...document.querySelectorAll("button")].find(b=>/切换侧边栏|toggle sidebar/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")));return {path:location.pathname,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,sidebarWidth:r?Math.round(r.width):null,sidebarState:s?.getAttribute("data-state"),sidebarCollapsible:s?.getAttribute("data-collapsible"),toggleExpanded:b?.getAttribute("aria-expanded")}})()')
start=state();snap('sidebar-desktop-start-followup.png');cli('find','role','button','click','--name','切换侧边栏','--exact');cli('wait',200);one=state();snap('sidebar-desktop-toggle-1.png');cli('find','role','button','click','--name','切换侧边栏','--exact');cli('wait',200);two=state();snap('sidebar-desktop-toggle-2.png')
# Mobile actual overlay/drawer state before and after nav click.
cli('set','viewport',390,844);cli('open',O+'/');cli('wait',700)
def mstate():return ev('(()=>{const s=document.querySelector("[data-slot=sidebar]");const r=s?.getBoundingClientRect();const b=[...document.querySelectorAll("button")].find(b=>/切换侧边栏|toggle sidebar/i.test((b.innerText||"")+(b.getAttribute("aria-label")||"")));return {path:location.pathname,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,sidebarRect:r?{x:Math.round(r.x),width:Math.round(r.width),right:Math.round(r.right)}:null,sidebarState:s?.getAttribute("data-state"),sidebarMobile:s?.getAttribute("data-mobile"),sidebarHidden:s?.getAttribute("aria-hidden"),toggleExpanded:b?.getAttribute("aria-expanded")}})()')
mbefore=mstate();
try:cli('find','role','button','click','--name','切换侧边栏','--exact')
except RuntimeError:cli('find','role','button','click','--name','Toggle Sidebar','--exact')
cli('wait',200);mopen=mstate();snap('sidebar-mobile-open-verified.png');cli('find','role','link','click','--name','任务','--exact');cli('wait',500);mafter=mstate();snap('sidebar-mobile-after-nav-verified.png')
# Viewport geometry incl header/main/sidebar to check obvious overlay.
layout=[]
for w,h in [(1366,768),(1440,900),(390,844)]:
 cli('set','viewport',w,h)
 for path in ['/agents','/agents/37989297-3bc8-4c0c-b302-7c6efbdba630']:
  cli('open',O+path);cli('wait',800)
  d=ev('(()=>{const rect=e=>{const r=e?.getBoundingClientRect();return r?{x:Math.round(r.x),y:Math.round(r.y),right:Math.round(r.right),bottom:Math.round(r.bottom),width:Math.round(r.width),height:Math.round(r.height)}:null};const m=document.querySelector("main"),s=document.querySelector("[data-slot=sidebar]"),h=document.querySelector("header");const mr=m?.getBoundingClientRect(),hr=h?.getBoundingClientRect(),sr=s?.getBoundingClientRect();return {path:location.pathname,viewport:{w:innerWidth,h:innerHeight},scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,main:rect(m),header:rect(h),sidebar:rect(s),headerMainOverlap:!!(mr&&hr&&mr.x<hr.right&&hr.x<mr.right&&mr.y<hr.bottom&&hr.y<mr.bottom),sidebarMainOverlap:!!(mr&&sr&&mr.x<sr.right&&sr.x<mr.right&&mr.y<sr.bottom&&sr.y<mr.bottom)}})()')
  layout.append(d);snap(f'layout-followup-{w}x{h}-'+('list' if path=='/agents' else 'detail')+'.png')
report={'captured_at':now(),'desktop_sidebar':{'start':start,'after_toggle_1':one,'after_toggle_2':two,'both_widths_seen':len({one['sidebarWidth'],two['sidebarWidth']})==2,'no_overflow':all(x['scrollWidth']<=x['clientWidth'] for x in [start,one,two])},'mobile_sidebar':{'before':mbefore,'open':mopen,'after_navigation':mafter,'route_is_tasks':mafter['path']=='/tasks','sidebar_closed_after_nav':mafter.get('sidebarState')=='collapsed' or mafter.get('toggleExpanded')=='false' or (mafter.get('sidebarRect') and mafter['sidebarRect']['x']<=-mafter['sidebarRect']['width'])},'layout':layout,'private_auth_saved':False}
(OUT/'sidebar-layout-followup.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
summary=json.loads((OUT/'batch02-summary.json').read_text())
for row in summary['results']:
 if row['canonical_id']=='G02-06':row.update({'status':'PASS' if report['desktop_sidebar']['both_widths_seen'] and report['desktop_sidebar']['no_overflow'] else 'PARTIAL','details':report['desktop_sidebar'],'evidence':['sidebar-desktop-start-followup.png','sidebar-desktop-toggle-1.png','sidebar-desktop-toggle-2.png']})
 if row['canonical_id'] in ('G02-07','P02-03'):
  ok=report['mobile_sidebar']['route_is_tasks'] and report['mobile_sidebar']['sidebar_closed_after_nav'] and mafter['scrollWidth']<=mafter['clientWidth']
  row.update({'status':'PASS' if ok else 'PARTIAL','details':report['mobile_sidebar'],'evidence':['sidebar-mobile-open-verified.png','sidebar-mobile-after-nav-verified.png']})
 if row['canonical_id']=='G04-06':
  ok=all(x['scrollWidth']<=x['clientWidth'] and not x['headerMainOverlap'] and not x['sidebarMainOverlap'] for x in layout)
  row.update({'status':'PASS' if ok else 'PARTIAL','details':{'measurements':layout,'horizontal_overflow_absent':all(x['scrollWidth']<=x['clientWidth'] for x in layout),'header_sidebar_main_overlap_absent':all(not x['headerMainOverlap'] and not x['sidebarMainOverlap'] for x in layout)},'evidence':[f'layout-followup-{w}x{h}-{p}.png' for w,h in [(1366,768),(1440,900),(390,844)] for p in ['list','detail']]})
summary['followup_evidence']='sidebar-layout-followup.json';(OUT/'batch02-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
progress=json.loads((OUT/'progress.json').read_text());progress.update({'updated_at':now(),'results':summary['results'],'current_gate':'All 16 requested canonical IDs have per-case outcomes; verified mobile Sidebar collapse and layout overlap geometry in supplemental pass.','phase':'BATCH02_PARTIAL_COMPLETE'});(OUT/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'desktop':report['desktop_sidebar'],'mobile':report['mobile_sidebar'],'geometry_checks':len(layout),'case_statuses':{x['canonical_id']:x['status'] for x in summary['results']}},ensure_ascii=False),flush=True)
