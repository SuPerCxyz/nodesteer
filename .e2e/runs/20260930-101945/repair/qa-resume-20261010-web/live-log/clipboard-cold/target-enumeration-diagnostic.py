import datetime, hashlib, json, os, re, signal, subprocess, time
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect
R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/live-log/clipboard-cold');ORIGIN='http://127.0.0.1:38181';EXEC='e3555a68-d70c-4c6a-9589-6ec2568fd669';SESSION='qawebsecure20261010-target-class-38181';CREDS=Path('/tmp/nodesteer-e2e-resume-20260909-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20260909-Qv9AYO/repair-connection.json');AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2'];tunnel=None;cdp=None;opened=False;start=None;argv_hash=None;login_meta=None
# Correct private connection location established by the approved live test.
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def cli(args,stdin=None,timeout=25):
 p=subprocess.run(AB+args,input=stdin,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('agent-browser command failed; output suppressed')
 return p.stdout
def cp(method,params=None):
 cp.i+=1;i=cp.i;cdp.send(json.dumps({'id':i,'method':method,'params':params or {}}));end=time.monotonic()+8
 while time.monotonic()<end:
  x=json.loads(cdp.recv(timeout=2))
  if x.get('id')==i:
   if 'error' in x:raise RuntimeError('CDP read failed: '+str(x['error'].get('code')))
   return x.get('result',{})
 raise RuntimeError('CDP read timeout')
cp.i=0
def proc_start(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def proc_argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);rows=[]
 for x in p.stdout.splitlines():
  a=re.search(r'\s([^\s]+):38181\s',x);ids=re.findall(r'pid=(\d+)',x);rows.append({'loopback_only':bool(a and a.group(1)=='127.0.0.1'),'owned_pid':bool(tunnel and str(tunnel.pid) in ids),'pids':ids})
 return {'count':len(rows),'listeners':rows}
try:
 g=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);vals={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for l in g.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in vals:vals[z[0]].append(z[1])
 assert not vals['localforward'] and not vals['remoteforward'] and all(x=='none' for x in vals['remotecommand']) and all(x!='yes' for x in vals['forwardagent']) and not vals['controlpath']
 s=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not s.stdout.strip()
 argv_hash=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();tunnel=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);start=proc_start(tunnel.pid);ready=False
 for _ in range(60):
  if tunnel.poll() is not None:raise RuntimeError('SSH forward exited')
  st=listeners()
  if st['count']==1 and st['listeners'][0]['loopback_only'] and st['listeners'][0]['owned_pid']:ready=True;break
  time.sleep(.2)
 if not ready:raise RuntimeError('exact owned loopback listener missing')
 cli(['open',ORIGIN+'/sign-in']);opened=True;cli(['wait','--load','networkidle'])
 creds=json.loads(CREDS.read_text());shared=json.loads(SHARED.read_text())['token'];js='''(async()=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:%s,password:%s})});let d={};try{d=await r.json()}catch{};const t=d.token||null,m=!!t&&t===%s;if(r.status===200&&t&&!m)localStorage.setItem('nodesteer_token',t);return {http:r.status,token_present:!!t,shared_match:m,stored:!!t&&!m&&localStorage.getItem('nodesteer_token')===t}})()'''%(json.dumps(creds['admin_username']),json.dumps(creds['admin_password']),json.dumps(shared));login_meta=json.loads(cli(['--json','eval','--stdin'],stdin=js))['data']['result']
 if login_meta.get('http')!=200 or not login_meta.get('token_present') or login_meta.get('shared_match') or not login_meta.get('stored'):raise RuntimeError('dedicated login isolation gate failed')
 cli(['network','requests','--clear']);cli(['open',ORIGIN+'/executions/'+EXEC]);cli(['wait','--load','networkidle'])
 auth=json.loads(cli(['--json','eval','--stdin'],stdin='''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {http:r.status,path:location.pathname,origin:location.origin,secure:isSecureContext,online:navigator.onLine}})()'''))['data']['result']
 if auth['http']!=200 or auth['path']!='/executions/'+EXEC or auth['origin']!=ORIGIN:raise RuntimeError('execution route/auth gate failed')
 info=json.loads(cli(['--json','session','info']))
 # Keep only selected scalar diagnostics; never persist the raw session-info object.
 def find_pid(o):
  if isinstance(o,dict):
   for k,v in o.items():
    if 'pid' in k.lower() and isinstance(v,int) and v>0:return (k,v)
    r=find_pid(v)
    if r:return r
  if isinstance(o,list):
   for v in o:
    r=find_pid(v)
    if r:return r
  return None
 piditem=find_pid(info);browser_process=None
 if piditem:
  piditem_name,bpid=piditem
  try:browser_process={'pid':bpid,'startticks_present':Path(f'/proc/{bpid}/stat').exists(),'comm':Path(f'/proc/{bpid}/comm').read_text().strip()[:80] if Path(f'/proc/{bpid}/comm').exists() else None}
  except Exception:browser_process={'pid':bpid,'startticks_present':False,'comm':None}
 wsurl=cli(['get','cdp-url']).strip();parsed=urlparse(wsurl)
 if parsed.hostname!='127.0.0.1':raise RuntimeError('CDP endpoint is not loopback')
 cdp=ws_connect(wsurl,open_timeout=10,close_timeout=3);version=cp('Browser.getVersion');targets=cp('Target.getTargets').get('targetInfos',[]);pages=[x for x in targets if x.get('type')=='page'];cats={'owned_execution':0,'same_origin_other_path':0,'about_blank_exact':0,'data_comma_exact':0,'browser_internal_empty_exact':0,'other_browser_internal':0,'other_web_origin':0,'other_scheme':0};schemes={};blanks=[];contexts=[]
 for x in pages:
  u=x.get('url','');q=urlparse(u);scheme=q.scheme or 'none';schemes[scheme]=schemes.get(scheme,0)+1
  contexts.append(x.get('browserContextId'))
  if u==ORIGIN+'/executions/'+EXEC:cats['owned_execution']+=1
  elif u.startswith(ORIGIN+'/'):cats['same_origin_other_path']+=1
  elif u=='about:blank':cats['about_blank_exact']+=1;blanks.append(True)
  elif u=='data:,':cats['data_comma_exact']+=1;blanks.append(True)
  elif u in ('chrome://newtab/','chrome://new-tab-page/','chrome://blank/'):cats['browser_internal_empty_exact']+=1;blanks.append(True)
  elif scheme in ('chrome','chrome-extension','devtools'):cats['other_browser_internal']+=1
  elif scheme in ('http','https'):cats['other_web_origin']+=1
  else:cats['other_scheme']+=1
 ctx=cp('Target.getBrowserContexts');default=ctx.get('defaultBrowserContextId');target=next((x for x in pages if x.get('url')==ORIGIN+'/executions/'+EXEC),None);context_match=bool(target and target.get('browserContextId')==default)
 save('target-enumeration-diagnostic.json',{'captured_at_utc':utc(),'session_name':SESSION,'session_info_json_valid':True,'session_info_pid_key':piditem[0] if piditem else None,'browser_process':browser_process,'browser_product':version.get('product'),'protocol_version':version.get('protocolVersion'),'cdp_websocket_host_loopback':True,'cdp_endpoint_kind':'browser' if '/devtools/browser/' in parsed.path else 'page' if '/devtools/page/' in parsed.path else 'other','page_target_count':len(pages),'page_target_categories':cats,'page_target_scheme_counts':schemes,'owned_execution_target_count':cats['owned_execution'],'same_origin_or_blank_only':cats['other_web_origin']==0 and cats['other_scheme']==0 and cats['other_browser_internal']==0,'second_page_is_exact_about_blank_or_data':bool(len(pages)==2 and sum((cats['about_blank_exact'],cats['data_comma_exact']))==1),'second_page_is_browser_internal_empty':bool(len(pages)==2 and cats['browser_internal_empty_exact']==1),'target_context_is_default':context_match,'nondefault_context_count':len(ctx.get('browserContextIds',[])),'default_context_present':bool(default),'target_context_ids_equal':context_match,'login':{k:login_meta.get(k) for k in ('http','token_present','shared_match','stored')},'page_secure':auth.get('secure'),'no_permission_mutations':True,'no_task_or_run_created':True,'raw_page_urls_saved':False,'page_titles_saved':False,'query_or_data_contents_saved':False,'private_values_saved':False})
except Exception as e:
 save('target-enumeration-diagnostic-error.json',{'captured_at_utc':utc(),'exception_type':type(e).__name__,'message':str(e)[:160],'private_values_saved':False})
finally:
 cleanup={'captured_at_utc':utc(),'network_cleared':False,'dedicated_logout':None,'browser_session_closed':False,'tunnel_guard_matched':False,'tunnel_stopped':False,'listener_after_stop':None,'listener_gone':False,'private_values_saved':False}
 if opened:
  try:cleanup['network_cleared']=subprocess.run(AB+['network','requests','--clear'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
  try:
   out=json.loads(cli(['--json','eval','--stdin'],stdin='''(async()=>{const t=localStorage.getItem('nodesteer_token');let post=0,old=0;if(t){post=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;old=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,logout_http:post,old_me_http:old,token_removed:!localStorage.getItem('nodesteer_token')}})()'''))['data']['result'];cleanup['dedicated_logout']=out
  except Exception:cleanup['dedicated_logout']={'completed':False}
  try:cleanup['browser_session_closed']=subprocess.run(AB+['close'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
 if cdp:
  try:cdp.close()
  except Exception:pass
 if tunnel:
  try:
   good=tunnel.poll() is None and proc_start(tunnel.pid)==start and hashlib.sha256(b'\0'.join(proc_argv(tunnel.pid))).hexdigest()==argv_hash;cleanup['tunnel_guard_matched']=good
   if good:os.kill(tunnel.pid,signal.SIGTERM);tunnel.wait(timeout=8);cleanup['tunnel_stopped']=tunnel.poll() is not None
   p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);cleanup['listener_after_stop_count']=sum(1 for x in p.stdout.splitlines() if x.strip());cleanup['listener_gone']=cleanup['listener_after_stop_count']==0
  except Exception:pass
 save('target-enumeration-cleanup.json',cleanup)
 print(json.dumps({'diagnostic_saved':(R/'target-enumeration-diagnostic.json').exists(),'network_cleared':cleanup['network_cleared'],'logout_old_me_401':(cleanup.get('dedicated_logout') or {}).get('old_me_http')==401,'browser_closed':cleanup['browser_session_closed'],'tunnel_stopped':cleanup['tunnel_stopped'],'listener_gone':cleanup['listener_gone']}),flush=True)
