import datetime, hashlib, json, os, re, signal, subprocess, time
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect
R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/live-log/clipboard-cold');ORIGIN='http://127.0.0.1:38181';SESSION='qawebsecure20261010-clipcold-diag-38181';AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2'];tunnel=None;cdp=None;opened=False;start=None;argv_hash=None
class CdpError(RuntimeError):pass
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def cli(a,timeout=25):
 p=subprocess.run(AB+a,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('browser command failed; output suppressed')
 return p.stdout
def cp(m,p=None):
 cp.i+=1;i=cp.i;cdp.send(json.dumps({'id':i,'method':m,'params':p or {}}));end=time.monotonic()+8
 while time.monotonic()<end:
  x=json.loads(cdp.recv(timeout=2))
  if x.get('id')==i:
   if 'error' in x:raise CdpError(str(x['error'].get('code')))
   return x.get('result',{})
 raise CdpError('timeout')
cp.i=0
def stat(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);rows=[]
 for x in p.stdout.splitlines():
  a=re.search(r'\s([^\s]+):38181\s',x);ids=re.findall(r'pid=(\d+)',x);rows.append({'loopback_only':bool(a and a.group(1)=='127.0.0.1'),'owned_pid':bool(tunnel and str(tunnel.pid) in ids),'pids':ids})
 return {'count':len(rows),'listeners':rows}
try:
 g=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);d={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for l in g.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in d:d[z[0]].append(z[1])
 assert not d['localforward'] and not d['remoteforward'] and all(x=='none' for x in d['remotecommand']) and all(x!='yes' for x in d['forwardagent']) and not d['controlpath']
 s=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not s.stdout.strip()
 argv_hash=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();tunnel=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);start=stat(tunnel.pid)
 ready=False
 for _ in range(60):
  if tunnel.poll() is not None:raise RuntimeError('SSH forward exited')
  st=listeners()
  if st['count']==1 and st['listeners'][0]['loopback_only'] and st['listeners'][0]['owned_pid']:
   ready=True;break
  time.sleep(.2)
 if not ready:raise RuntimeError('exact owned loopback listener missing')
 cli(['open',ORIGIN+'/sign-in']);opened=True;cli(['wait','--load','networkidle'])
 wsurl=cli(['get','cdp-url']).strip()
 if not wsurl.startswith('ws://127.0.0.1:'):raise RuntimeError('CDP endpoint not loopback')
 parsed=urlparse(wsurl);cdp=ws_connect(wsurl,open_timeout=10,close_timeout=3)
 ver=cp('Browser.getVersion');targets=cp('Target.getTargets').get('targetInfos',[]);t=next(x for x in targets if x.get('type')=='page' and x.get('url')==ORIGIN+'/sign-in');info=cp('Target.getTargetInfo',{'targetId':t['targetId']}).get('targetInfo',{});ctxresp=cp('Target.getBrowserContexts');contexts=ctxresp.get('browserContextIds',[]);default_id=ctxresp.get('defaultBrowserContextId')
 context=t.get('browserContextId');info_context=info.get('browserContextId');
 save('cdp-context-diagnostic.json',{'captured_at_utc':utc(),'browser_product':ver.get('product'),'protocol_version':ver.get('protocolVersion'),'cdp_ws_path_kind':'browser' if '/devtools/browser/' in parsed.path else 'page' if '/devtools/page/' in parsed.path else 'other','cdp_ws_host_loopback':parsed.hostname=='127.0.0.1','target_type':info.get('type'),'target_url_origin_matches':info.get('url','').startswith(ORIGIN+'/'),'target_context_id_present':bool(context),'target_and_info_context_equal':context==info_context,'target_context_in_getBrowserContexts':context in contexts if context else False,'target_context_is_default':bool(context and context==default_id),'listed_browser_context_count':len(contexts),'default_context_id_returned':bool(default_id),'browser_context_ids_recorded':False,'no_permission_mutations_attempted':True,'no_task_or_run_created':True,'private_values_saved':False})
except Exception as e:
 save('cdp-context-diagnostic-error.json',{'captured_at_utc':utc(),'exception_type':type(e).__name__,'message':str(e)[:200],'private_values_saved':False})
finally:
 cleanup={'captured_at_utc':utc(),'browser_session_closed':False,'network_log_cleared':False,'tunnel_guard_matched':False,'tunnel_stopped':False,'listener_after_stop':None,'listener_gone':False,'private_values_saved':False}
 if opened:
  try:cleanup['network_log_cleared']=subprocess.run(AB+['network','requests','--clear'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
  try:cleanup['browser_session_closed']=subprocess.run(AB+['close'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
 if cdp:
  try:cdp.close()
  except Exception:pass
 if tunnel:
  try:
   alive=tunnel.poll() is None and stat(tunnel.pid)==start and hashlib.sha256(b'\0'.join(argv(tunnel.pid))).hexdigest()==argv_hash;cleanup['tunnel_guard_matched']=alive
   if alive:os.kill(tunnel.pid,signal.SIGTERM);tunnel.wait(timeout=8);cleanup['tunnel_stopped']=tunnel.poll() is not None
   cleanup['listener_after_stop']=listeners();cleanup['listener_gone']=cleanup['listener_after_stop']['count']==0
  except Exception:pass
 save('cdp-context-diagnostic-cleanup.json',cleanup)
 print(json.dumps({'diagnostic_saved':(R/'cdp-context-diagnostic.json').exists(),'browser_closed':cleanup['browser_session_closed'],'tunnel_stopped':cleanup['tunnel_stopped'],'listener_gone':cleanup['listener_gone']}),flush=True)
