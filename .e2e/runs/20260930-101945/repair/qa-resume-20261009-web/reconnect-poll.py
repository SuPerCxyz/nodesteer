import json, subprocess, time, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
SESSION='qawebsecure20261009'; HOST='192.168.100.209'; ORIGIN='http://192.168.100.209:18080'; EID='5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
c=json.loads((PRIVATE/'repair-connection.json').read_text()); hub=c['hub_url'].rstrip('/'); headers={'Authorization':'Bearer '+c['token']}
def now(): return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=15)
 if r.returncode: raise RuntimeError('browser action failed '+str(args[0]))
 return r.stdout
def page():
 return json.loads(cli('eval','({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),running:document.body.innerText.includes("运行中"),failed:document.body.innerText.includes("失败"),navCount:performance.getEntriesByType("navigation").length})'))
def api():
 with urlopen(Request(hub+'/api/executions/'+EID,headers=headers),timeout=8) as r:d=json.loads(r.read()); hs=r.status
 with urlopen(Request(hub+'/api/executions/'+EID+'/logs',headers=headers),timeout=8) as r:logs=json.loads(r.read()); ls=r.status
 return {'http':hs,'status':d.get('status'),'exit_code':d.get('exit_code'),'stdout_length':len(d.get('stdout') or ''),'stderr_length':len(d.get('stderr') or ''),'logs_http':ls,'log_count':len(logs),'seq':[x.get('seq') for x in logs],'streams':[x.get('stream') for x in logs],'markers':{'first':any('QA_WEB_LIVE_FIRST' in (x.get('chunk') or '') for x in logs),'second':any('QA_WEB_LIVE_SECOND' in (x.get('chunk') or '') for x in logs)},'attempt_metadata':any(('attempt' in x or 'attempt_id' in x) for x in logs) or 'attempts' in d}
def request_summary():
 r=subprocess.run(CLI+['--json','network','requests'],text=True,capture_output=True,timeout=15)
 if r.returncode:return {'readable':False,'count':0,'offline_failure_count':0,'online_success_count':0}
 try:d=json.loads(r.stdout)
 except Exception:return {'readable':False,'count':0,'offline_failure_count':0,'online_success_count':0}
 items=[]
 def walk(x):
  if isinstance(x,dict):
   if isinstance(x.get('url'),str) and '/api/' in x['url']:
    url=x['url']; path=url.split(ORIGIN,1)[-1].split('?',1)[0]
    err=str(x.get('error') or x.get('failureText') or '')
    items.append({'path':path,'method':x.get('method'),'status':x.get('status'),'offline_error':'ERR_INTERNET_DISCONNECTED' in err or 'internet disconnected' in err.lower(),'failed':bool(err)})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d)
 return {'readable':True,'count':len(items),'offline_failure_count':sum(x['offline_error'] for x in items),'online_success_count':sum(x['status']==200 for x in items),'items':items}
network=request_summary()
start=page(); current=api(); print('online poll start',json.dumps({'ui':start,'api':current,'network':{k:v for k,v in network.items() if k!='items'}},ensure_ascii=False),flush=True)
deadline=time.time()+18
while time.time()<deadline:
 current=api(); state=page()
 if state['online'] and state['failed'] and state['second']:
  break
 time.sleep(.5)
result={'captured_at':now(),'network':network,'ui':state,'api':current,'same_page_no_reload':state['navCount']==1,'online_recovery_observed':state['online'] and state['failed'] and state['second']}
(OUT/'reconnected-poll.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
p=json.loads((OUT/'progress.json').read_text());p.update({'phase':'ONLINE_RECOVERY_POLLED','updated_at':now(),'current_gate':'已恢复online并从相同Execution页面轮询真实API与UI；检查OFFLINE失败请求和终态日志反馈见reconnected-poll.json','browser_recovery':{'same_page_no_reload':result['same_page_no_reload'],'online_recovery_observed':result['online_recovery_observed'],'offline_failure_count':network['offline_failure_count'],'online_success_count':network['online_success_count']}});(OUT/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
print('reconnect result',json.dumps({'ui':state,'api':current,'network':{k:v for k,v in network.items() if k!='items'},'recovery':result['online_recovery_observed']},ensure_ascii=False),flush=True)
