import json,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web');SESSION='qawebsecure20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
ROUTES=[('仪表盘','/'),('任务','/tasks'),('调度','/schedules'),('执行','/executions'),('文件传输','/transfers'),('节点','/agents'),('分组','/groups'),('脚本','/scripts'),('应用','/applications'),('审计','/audit'),('用户','/users'),('设置','/settings')]
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=20)
 if r.returncode:raise RuntimeError('browser action failed '+str(args[0]))
 return r.stdout
def ev(js):return json.loads(cli('eval',js))
def state(name,path):
 q=json.dumps(name,ensure_ascii=False);h=json.dumps(path)
 js=f'''(()=>{{const as=[...document.querySelectorAll("a[href]")];const a=as.find(x=>x.getAttribute("href")=== {h} && x.innerText.trim()=== {q});const active=as.filter(x=>x.getAttribute("aria-current")==="page");return {{url:location.pathname,title:document.querySelector("h1")?.innerText||"",activeFound:!!a,ariaCurrent:a?.getAttribute("aria-current")||null,activeClass:a?String(a.className).split(/\\s+/).includes("active"):false,activeCount:active.length}}}})()'''
 return ev(js)
def main():
 cli('open',ORIGIN+'/')
 cli('wait','--text','仪表盘')
 results=[]
 for name,path in ROUTES:
  if path!='/':
   cli('find','role','link','click','--name',name,'--exact')
   cli('wait','--url','**'+path)
  results.append({'label':name,'expected_path':path,**state(name,path)})
 # Browser history is exercised by route links, then actual back/forward actions.
 cli('find','role','link','click','--name','设置','--exact');cli('wait','--url','**/settings')
 cli('find','role','link','click','--name','任务','--exact');cli('wait','--url','**/tasks')
 cli('back');cli('wait','--url','**/settings');back=state('设置','/settings')
 cli('forward');cli('wait','--url','**/tasks');forward=state('任务','/tasks')
 # Direct deep link and refresh use an existing owned Task; no mutation.
 cli('open',ORIGIN+'/tasks/4006d075-8c15-4d14-9cbc-8b63f5823131')
 cli('wait','--text','qa-web-20261009-live-failed-7956f386')
 deep_before=ev('({url:location.pathname,title:document.querySelector("h1")?.innerText||"",taskName:document.body.innerText.includes("qa-web-20261009-live-failed-7956f386")})')
 cli('reload')
 cli('wait','--text','qa-web-20261009-live-failed-7956f386')
 deep_after=ev('({url:location.pathname,title:document.querySelector("h1")?.innerText||"",taskName:document.body.innerText.includes("qa-web-20261009-live-failed-7956f386")})')
 # Direct Artifacts URL and the Application-page indirect link are read-only checks.
 cli('open',ORIGIN+'/applications');cli('wait','--text','应用')
 app_link=ev('(()=>{const a=[...document.querySelectorAll("a[href]")].find(x=>/管理制品|Artifacts/i.test(x.innerText));return a?{text:a.innerText.trim(),href:a.getAttribute("href")}:null})()')
 if app_link and app_link.get('href'):
  cli('click','a[href='+json.dumps(app_link['href'])+']')
  cli('wait','--url','**/artifacts')
  indirect=ev('({url:location.pathname,title:document.querySelector("h1")?.innerText||""})')
 else: indirect={'url':None,'title':'','link_found':False}
 cli('open',ORIGIN+'/artifacts')
 cli('wait','--url','**/artifacts')
 direct=ev('({url:location.pathname,title:document.querySelector("h1")?.innerText||"",not_found:document.body.innerText.includes("Not Found")})')
 cli('open',ORIGIN+'/')
 cli('wait','--text','仪表盘')
 cli('screenshot','main',str((OUT/'navigation-dashboard.png').resolve()))
 record={'captured_at':now(),'canonical_case':'G02-09','routes':results,'active_all_routes':all(x['url']==x['expected_path'] and x['activeFound'] and x['ariaCurrent']=='page' and x['activeClass'] for x in results),'back':back,'forward':forward,'back_forward_ok':back['url']=='/settings' and forward['url']=='/tasks' and back['ariaCurrent']=='page' and forward['ariaCurrent']=='page','direct_detail_before_refresh':deep_before,'direct_detail_after_refresh':deep_after,'detail_deep_link_ok':deep_before['taskName'] and deep_after['taskName'] and deep_before['url']==deep_after['url'],'artifacts_indirect_link':app_link,'artifacts_indirect_result':indirect,'artifacts_direct_result':direct,'evidence':['navigation-dashboard.png'],'resources_written':False}
 (OUT/'g02-09-results.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'active_all_routes':record['active_all_routes'],'route_results':[{'label':x['label'],'url':x['url'],'ariaCurrent':x['ariaCurrent'],'activeClass':x['activeClass']} for x in results],'back_forward_ok':record['back_forward_ok'],'deep_link_ok':record['detail_deep_link_ok'],'artifacts_link_found':bool(app_link),'artifacts_indirect_url':indirect.get('url'),'artifacts_direct':direct},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
