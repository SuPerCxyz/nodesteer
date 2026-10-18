import json, re, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
ROOT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch20')
S='qawebsecure20261010-b18-clean38181';O='http://127.0.0.1:38181'
ROUTES=[
 ('/tasks',['/api/tasks']),('/schedules',['/api/schedules']),('/executions',['/api/executions']),('/transfers',['/api/transfers']),('/agents',['/api/nodes','/api/groups']),('/groups',['/api/groups','/api/nodes']),('/scripts',['/api/scripts']),('/applications',['/api/applications']),('/audit',['/api/audit']),('/users',['/api/users']),('/settings',['/api/settings'])]
def utc():return datetime.now(timezone.utc).isoformat()
def cli(*a,timeout=20):return subprocess.run(['agent-browser','--session',S,*a],capture_output=True,text=True,timeout=timeout,check=True)
def network_whitelist():
 out=cli('network','requests','--filter','/api/').stdout;items=[]
 for line in out.splitlines():
  m=re.search(r'\]\s+(GET|POST|PUT|DELETE)\s+(https?://\S+).*?\s(\d{3})\s*$',line)
  if not m:continue
  method,url,code=m.groups();u=urlparse(url)
  if u.netloc!='127.0.0.1:38181' or not u.path.startswith('/api/'):continue
  parts=u.path.split('/');klass='/'.join(parts[:3]) if len(parts)>=3 else u.path
  if klass=='/api/applications' and len(parts)>3:klass='/api/applications/<id>/state' if parts[-1]=='state' else '/api/applications/<id>'
  if klass=='/api/executions' and len(parts)>3:klass='/api/executions/<id>'
  items.append({'method':method,'path_class':klass,'status':int(code)})
 return items
def dom():
 s=cli('eval','JSON.stringify({path:location.pathname,main:!!document.querySelector("main"),headingCount:document.querySelectorAll("h1").length,alerts:document.querySelectorAll("main [role=alert]").length,rows:document.querySelectorAll("main tbody tr").length,skeletons:document.querySelectorAll("main [data-slot=skeleton]").length})').stdout.strip()
 x=json.loads(s);return json.loads(x) if isinstance(x,str) else x
evidence_path=ROOT/'evidence/G02-08-sidebar-navigation.json';ev=json.loads(evidence_path.read_text());pfile=ROOT/'progress.json'
for route,expected in ROUTES:
 path=route
 selector=f'a[data-sidebar="menu-button"][href="{path}"]';js='JSON.stringify(document.querySelectorAll('+json.dumps(selector)+').length)';count_eval=cli('eval',js).stdout.strip();count=json.loads(count_eval);count=json.loads(count) if isinstance(count,str) else count
 if count!=1:
  result={'requested_path':path,'sidebar_locator_count':count,'status':'PARTIAL','reason':'Could not resolve unique actual sidebar menu link.'};ev['routes'].append(result);evidence_path.write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');continue
 cli('network','requests','--clear')
 click=cli('click',f'a[data-sidebar="menu-button"][href="{path}"]')
 cli('wait','700')
 actual=urlparse(cli('get','url').stdout.strip()).path
 click_dom=dom()
 cli('network','requests','--clear')
 cli('reload')
 cli('wait','900')
 actual_after=urlparse(cli('get','url').stdout.strip()).path
 state=dom();requests=network_whitelist()
 expected_observed=[x for x in requests if x['method']=='GET' and x['path_class'] in expected and x['status']==200]
 result={'requested_path':path,'actual_path_after_click':actual,'actual_path_after_reload':actual_after,'sidebar_locator_unique':True,'click_command_exit':click.returncode,'main_after_click':click_dom['main'],'alerts_after_click':click_dom['alerts'],'main_after_reload':state['main'],'heading_count_after_reload':state['headingCount'],'alerts_after_reload':state['alerts'],'api_requests_after_reload':requests,'expected_get_200_observed':[{'path_class':x['path_class'],'status':x['status']} for x in expected_observed],'status':'PASS' if actual==path and actual_after==path and state['main'] and state['alerts']==0 and expected_observed else 'PARTIAL'}
 if result['status']!='PASS':result['reason']='Route, main ready, zero alerts and an expected actual GET 200 are all required.'
 ev['routes'].append(result);ev['captured_at_utc']=utc();evidence_path.write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n')
 pr=json.loads(pfile.read_text());pr['updated_at_utc']=utc();pr['phase']='G02_08_SIDEBAR_MATRIX_RUNNING';pr['current_main_route']=actual_after;pr['g02_08_routes_completed']=len(ev['routes']);pr['g02_08_route_statuses']=[{'path':x.get('requested_path'),'status':x.get('status','CAPTURED')} for x in ev['routes']];pr['next']='Continue remaining actual Sidebar links and then Applications Manage Artifacts path.';pfile.write_text(json.dumps(pr,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'path':path,'click_path':actual,'reload_path':actual_after,'status':result['status'],'expected_get_200':len(expected_observed),'alerts':state['alerts']},ensure_ascii=False),flush=True)
