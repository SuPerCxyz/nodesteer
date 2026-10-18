import datetime, hashlib, json, os, re, secrets, signal, subprocess, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect
R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/live-log/clipboard-denial');EV=R/'evidence'
ORIGIN='http://127.0.0.1:38181';EXEC='e3555a68-d70c-4c6a-9589-6ec2568fd669';SESSION='qawebsecure20261010-deny-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20260930-101945/repair-connection.json')
# Keep credential locations established by the currently mounted private repair fixture.
SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1']
SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
tunnel=None;startticks=None;argv_hash=None;opened=False;cdp=None;context_id=None;stage='INIT';token_shared=None;permission_changes=[];guard_history=[];result={}
ALLOW_TEXTS={
 '已复制':'success','复制失败，请手动复制。':'error','当前页面无法自动写入剪切板，请按 Ctrl+C 复制。':'manual_fallback',
 'Copied':'success','Copy failed. Copy the command manually.':'error','This page cannot write to the clipboard automatically. Press Ctrl+C to copy.':'manual_fallback'
}
class CdpError(RuntimeError):
 def __init__(self,method,code,message,params):self.method=method;self.code=code;self.message=message;self.param_names=params;super().__init__(method)
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(name,obj): (R/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def progress(phase,**kw):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':phase,'updated_at_utc':utc(),'created_tasks':0,'ui_runs':0,'private_values_saved':False,'raw_log_or_network_or_command_content_saved':False});p.update(kw);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'browser_open':opened,'context_known':bool(context_id),'new_tasks':0,'new_runs':0}),flush=True)
def cli(args,stdin=None,timeout=25):
 p=subprocess.run(AB+[str(x) for x in args],input=stdin,capture_output=True,text=True,timeout=timeout)
 if p.returncode:raise RuntimeError('agent-browser command failed; output suppressed')
 return p.stdout
def ev(js):
 out=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(out)['data']['result']
 except Exception:raise RuntimeError('filtered browser evaluation returned invalid JSON')
def proc_start(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def proc_argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listener_state():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);rows=[]
 for x in p.stdout.splitlines():
  a=re.search(r'\s([^\s]+):38181\s',x);ids=re.findall(r'pid=(\d+)',x);rows.append({'loopback_only':bool(a and a.group(1)=='127.0.0.1'),'owned_pid':bool(tunnel and str(tunnel.pid) in ids),'pids':ids})
 return {'count':len(rows),'listeners':rows}
def ssh_preflight():
 p=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);d={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for l in p.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in d:d[z[0]].append(z[1])
 assert not d['localforward'] and not d['remoteforward'] and all(x=='none' for x in d['remotecommand']) and all(x!='yes' for x in d['forwardagent']) and not d['controlpath']
 q=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not q.stdout.strip()
 return {'ssh_g_exit':p.returncode,'localforward_count':0,'remoteforward_count':0,'remotecommand_nonempty':False,'forwardagent_enabled':False,'controlpath_set':False,'port_listener_count':0,'guard_passed':True}
def cdp_call(method,params=None):
 cdp_call.i+=1;i=cdp_call.i;cdp.send(json.dumps({'id':i,'method':method,'params':params or {}}));end=time.monotonic()+10
 while time.monotonic()<end:
  m=json.loads(cdp.recv(timeout=2))
  if m.get('id')==i:
   if 'error' in m:
    e=m['error'];msg=re.sub(r'Bearer\s+[^\s,;]+','Bearer [REDACTED]',str(e.get('message',''))[:240],flags=re.I)
    raise CdpError(method,e.get('code'),msg,sorted((params or {}).keys()))
   return m.get('result',{})
 raise CdpError(method,None,'response timeout',sorted((params or {}).keys()))
cdp_call.i=0
def err_meta(e,permission=None):return {'method':getattr(e,'method',None),'code':getattr(e,'code',None),'message':getattr(e,'message',str(e)[:220]),'parameter_names':getattr(e,'param_names',[]),'permission_name':permission,'origin':ORIGIN}
def perm_state():return ev('''(async()=>{const q=async n=>{try{return (await navigator.permissions.query({name:n})).state}catch{return 'unsupported'}};return {read:await q('clipboard-read'),write:await q('clipboard-write')}})()''')
def target_guard():
 infos=cdp_call('Target.getTargets').get('targetInfos',[]);pages=[x for x in infos if x.get('type')=='page'];urls=[x.get('url','') for x in pages];ctx=cdp_call('Target.getBrowserContexts');default_id=ctx.get('defaultBrowserContextId')
 counts={'owned_execution':0,'same_origin_other_path':0,'about_blank':0,'data_comma':0,'exact_chrome_newtab_blank':0,'other_internal':0,'external_web_origin':0,'other_scheme':0}
 for u in urls:
  if u==ORIGIN+'/executions/'+EXEC:counts['owned_execution']+=1
  elif u.startswith(ORIGIN+'/'):counts['same_origin_other_path']+=1
  elif u=='about:blank':counts['about_blank']+=1
  elif u=='data:,':counts['data_comma']+=1
  elif u=='chrome://newtab/':counts['exact_chrome_newtab_blank']+=1
  else:
   q=urlparse(u)
   if q.scheme in ('chrome','chrome-extension','devtools'):counts['other_internal']+=1
   elif q.scheme in ('http','https'):counts['external_web_origin']+=1
   else:counts['other_scheme']+=1
 out={'page_target_count':len(pages),'page_categories':counts,'exact_internal_blank_match':counts['exact_chrome_newtab_blank']==1,'target_is_default_context':context_id==default_id,'nondefault_context_count':len(ctx.get('browserContextIds',[]))}
 out['passed']=counts['owned_execution']==1 and counts['same_origin_other_path']==0 and counts['about_blank']==0 and counts['data_comma']==0 and counts['exact_chrome_newtab_blank']==1 and counts['other_internal']==0 and counts['external_web_origin']==0 and counts['other_scheme']==0 and out['target_is_default_context'] and out['nondefault_context_count']==0
 guard_history.append(out);save('fresh-target-guard.json',{'captured_at_utc':utc(),'checks':guard_history,'raw_urls_saved':False,'query_or_title_saved':False})
 if not out['passed']:raise RuntimeError('fresh owned default-context target guard failed')
 return out
def set_permission(name,setting,allow_without_sanitization=False):
 params={'permission':{'name':name,'allowWithoutSanitization':allow_without_sanitization},'setting':setting,'origin':ORIGIN}
 cdp_call('Browser.setPermission',params);permission_changes.append({'permission_descriptor_name':name,'setting':setting,'exact_origin':ORIGIN,'browserContextId_omitted_for_verified_default':True})
def sha(txt):return hashlib.sha256(txt.encode()).hexdigest()
def observer_start():
 # Product copy strings are allowlisted; all unknown text is hash/length only.
 known=json.dumps(list(ALLOW_TEXTS.keys()),ensure_ascii=False)
 return ev('''(()=>{const known='''+known+''',state={baseline_utc:new Date().toISOString(),baseline_perf:performance.now(),events:[],seen:new Set()};const cls=t=>t.includes('手动复制')||t.includes('Ctrl+C')||t.includes('manually')?'manual_fallback':t.includes('复制失败')||t.includes('Copy failed')?'error':t==='已复制'||t==='Copied'?'success':t?'other':'empty';const collect=()=>{for(const n of document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]')){const t=(n.innerText||n.textContent||'').trim();if(!t||state.seen.has(t))continue;state.seen.add(t);const kind=cls(t),allow=known.includes(JSON.stringify(t));crypto.subtle.digest('SHA-256',new TextEncoder().encode(t)).then(b=>state.events.push({observed_utc:new Date().toISOString(),observed_perf_ms:performance.now(),kind,known_product_text:allow?t:null,text_bytes:new TextEncoder().encode(t).length,text_sha256:[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}))}};state.observer=new MutationObserver(collect);state.observer.observe(document.body,{subtree:true,childList:true,characterData:true});state.collect=collect;window.__qaCopyFeedback=state;collect();return {baseline_utc:state.baseline_utc,baseline_perf_ms:state.baseline_perf,existing_toast_nodes:document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]').length,observer_installed:true}})()''')
def observer_events():return ev('''(()=>{window.__qaCopyFeedback?.collect();return {events:window.__qaCopyFeedback?.events||[],toast_nodes:document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]').length}})()''')
def dom_oracle():
 return ev('''(async()=>{const id='''+json.dumps(EXEC)+''',tok=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+tok}}),d=await r.json(),s=document.querySelector('#execution-logs pre')?.textContent||'',out=d.stdout||'',err=d.stderr||'',hash=async x=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(x));return [...new Uint8Array(b)].map(v=>v.toString(16).padStart(2,'0')).join('')};return {http:r.status,id_matches:d.id===id,status:d.status,exit_code:d.exit_code,api_stdout_bytes:new TextEncoder().encode(out).length,api_stderr_bytes:new TextEncoder().encode(err).length,api_stdout_sha256:await hash(out),api_stderr_sha256:await hash(err),dom_bytes:new TextEncoder().encode(s).length,dom_sha256:await hash(s),dom_matches_api_stdout:s===out,dom_contains_any_error_marker:/-err-\\d+/.test(s),dom_contains_out_marker:/-out-\\d+/.test(s),search_value:document.querySelector('#execution-logs input')?.value??null,stream_value:document.querySelector('#execution-logs select')?.value??null}})()''')

try:
 stage='PREFLIGHT';pre=ssh_preflight();save('tunnel-preflight.json',{'captured_at_utc':utc(),**pre});progress('PREFLIGHT_PASSED',ssh=pre)
 argv_hash=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();tunnel=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);startticks=proc_start(tunnel.pid);ready=False
 for _ in range(60):
  if tunnel.poll() is not None:raise RuntimeError('owned SSH forward exited before ready')
  ss=listener_state()
  if ss['count']==1 and ss['listeners'][0]['loopback_only'] and ss['listeners'][0]['owned_pid']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as r:ready=200<=r.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise RuntimeError('loopback-only repair forward not ready')
 progress('TUNNEL_READY',ssh_pid=tunnel.pid,ssh_startticks=startticks,ssh_argv_sha256=argv_hash,listener=listener_state())
 stage='LOGIN';cli(['open',ORIGIN+'/sign-in']);opened=True;cli(['wait','--load','networkidle'])
 creds=json.loads(CREDS.read_text());shared=json.loads(SHARED.read_text())['token'];login_js='''(async()=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:%s,password:%s})});let d={};try{d=await r.json()}catch{};const t=d.token||null,m=!!t&&t===%s;if(r.status===200&&t&&!m)localStorage.setItem('nodesteer_token',t);return {http:r.status,token_present:!!t,shared_match:m,stored:!!t&&!m&&localStorage.getItem('nodesteer_token')===t}})()'''%(json.dumps(creds['admin_username']),json.dumps(creds['admin_password']),json.dumps(shared));login=ev(login_js);token_shared=login.get('shared_match')
 if login.get('http')!=200 or not login.get('token_present') or token_shared or not login.get('stored'):raise RuntimeError('dedicated token isolation gate failed')
 cli(['network','requests','--clear']);cli(['open',ORIGIN+'/executions/'+EXEC]);cli(['wait','--load','networkidle'])
 auth=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {http:r.status,origin:location.origin,path:location.pathname,secure:isSecureContext,online:navigator.onLine}})()''')
 if auth['http']!=200 or auth['origin']!=ORIGIN or auth['path']!='/executions/'+EXEC or not auth['secure'] or not auth['online']:raise RuntimeError('fresh execution detail auth/security gate failed')
 progress('COLD_EXECUTION_READY',browser={k:auth[k] for k in ('http','origin','path','secure','online')},token_shared_match=token_shared)
 stage='API_DOM';cli(['focus','#execution-logs input']);cli(['press','Control+a']);cli(['press','Backspace']);time.sleep(.2);cli(['select','#execution-logs select','stdout']);time.sleep(.2);api_dom=dom_oracle()
 old=json.loads((R.parent/'clipboard-cold'/'stream-filter-oracle.json').read_text())['api_execution'];prior=json.loads((R.parent/'attempt05'/'execution-final-oracle.json').read_text())['execution']
 api_match=api_dom['http']==200 and api_dom['id_matches'] and api_dom['status']=='FAILED' and api_dom['exit_code']==23 and api_dom['api_stdout_sha256']==prior['stdout_sha256'] and api_dom['api_stderr_sha256']==prior['stderr_sha256'] and api_dom['api_stdout_bytes']==old['stdout_bytes'] and api_dom['api_stderr_bytes']==old['stderr_bytes']
 save('execution-dom-oracle.json',{'captured_at_utc':utc(),'api_execution':{k:api_dom[k] for k in ('http','id_matches','status','exit_code','api_stdout_bytes','api_stderr_bytes','api_stdout_sha256','api_stderr_sha256')},'selected_ui':{k:api_dom[k] for k in ('dom_bytes','dom_sha256','dom_matches_api_stdout','dom_contains_any_error_marker','dom_contains_out_marker','search_value','stream_value')},'stdout_hash_matches_prior_copy_display':api_dom['api_stdout_sha256']==json.loads((R.parent/'clipboard-cold'/'clipboard-copy-oracle.json').read_text())['expected_sha256'],'private_values_saved':False,'raw_logs_saved':False})
 if not api_match or api_dom['search_value']!='' or api_dom['stream_value']!='stdout' or not api_dom['dom_matches_api_stdout']:raise RuntimeError('retained execution/filtered DOM oracle mismatch')
 # Ensure a fresh blank-toast baseline before installing observer.
 deadline=time.monotonic()+5;stable=0
 while time.monotonic()<deadline:
  n=ev('''document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]').length''')
  if n==0:stable+=1
  else:stable=0
  if stable>=5:break
  time.sleep(.15)
 if stable<5:raise RuntimeError('fresh page did not reach stable no-toast baseline')
 baseline=observer_start()
 if baseline['existing_toast_nodes']!=0:raise RuntimeError('toast appeared before observer baseline')
 save('toast-observer-baseline.json',{'captured_at_utc':utc(),**baseline,'session_is_fresh':True,'previous_toasts_present':False,'private_values_saved':False})
 stage='TARGET_GUARD';cdpurl=cli(['get','cdp-url']).strip()
 if not cdpurl.startswith('ws://127.0.0.1:'):raise RuntimeError('CDP endpoint not loopback')
 cdp=ws_connect(cdpurl,open_timeout=10,close_timeout=3);version=cdp_call('Browser.getVersion');targets=cdp_call('Target.getTargets').get('targetInfos',[]);target=next((x for x in targets if x.get('type')=='page' and x.get('url')==ORIGIN+'/executions/'+EXEC),None)
 if not target or not target.get('browserContextId'):raise RuntimeError('owned Execution target/context unavailable')
 context_id=target['browserContextId'];info=cdp_call('Target.getTargetInfo',{'targetId':target['targetId']}).get('targetInfo',{})
 if info.get('browserContextId')!=context_id or info.get('url')!=ORIGIN+'/executions/'+EXEC:raise RuntimeError('CDP target identity changed')
 initial_guard=target_guard()
 save('permission-scope-oracle.json',{'captured_at_utc':utc(),'browser_product':version.get('product'),'protocol_version':version.get('protocolVersion'),'cdp_endpoint_kind':'browser','browser_context_id':context_id,'target_context_is_default':initial_guard['target_is_default_context'],'target_guard':initial_guard,'exact_origin':ORIGIN,'context_id_omitted_for_verified_default':True,'private_values_saved':False})
 stage='PERMISSION_GRANT';grant_guard=target_guard();set_permission('clipboard-read','granted',False);set_permission('clipboard-write','granted',False);granted=perm_state()
 if granted!={'read':'granted','write':'granted'}:raise RuntimeError('Clipboard Permissions API did not confirm both grants')
 sentinel='qa-clipboard-denial-'+secrets.token_hex(16);seed=ev('''(async()=>{const s='''+json.dumps(sentinel)+''';await navigator.clipboard.writeText(s);const x=await navigator.clipboard.readText(),h=async t=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(t));return [...new Uint8Array(b)].map(v=>v.toString(16).padStart(2,'0')).join('')};return {write_read_equal:x===s,bytes:new TextEncoder().encode(x).length,sha256:await h(x)}})()''')
 seed_sha=hashlib.sha256(sentinel.encode()).hexdigest()
 save('sentinel-seed-oracle.json',{'captured_at_utc':utc(),'bytes':len(sentinel.encode()),'expected_sha256':seed_sha,'read_bytes':seed['bytes'],'read_sha256':seed['sha256'],'write_read_equal':seed['write_read_equal'],'permissions':granted,'plaintext_saved':False,'private_values_saved':False})
 if not seed['write_read_equal'] or seed['sha256']!=seed_sha:raise RuntimeError('real clipboard sentinel seed/readback mismatch')
 stage='PERMISSION_DENY';deny_guard=target_guard();set_permission('clipboard-read','denied',False);set_permission('clipboard-write','denied',False);denied=perm_state()
 save('permission-deny-oracle.json',{'captured_at_utc':utc(),'deny_method':'Browser.setPermission PermissionDescriptor for clipboard-read and clipboard-write, exact origin, verified owned default context','target_guard':deny_guard,'actual_permissions_api_state':denied,'both_denied':denied=={'read':'denied','write':'denied'},'permission_changes':permission_changes,'private_values_saved':False})
 if denied!={'read':'denied','write':'denied'}:raise RuntimeError('actual Permissions API did not confirm both clipboard permissions denied')
 # Capture the single real application Copy click after the clean observer baseline.
 copy_labels=ev('''(()=>[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(x=>x==='复制日志'||x==='Copy logs'))()''')
 if len(copy_labels)!=1:raise RuntimeError('unique real Copy logs button unavailable')
 click_label=copy_labels[0];click_mark=ev('''({utc:new Date().toISOString(),perf:performance.now()})''');cli(['find','role','button','click','--name',click_label,'--exact']);time.sleep(.55)
 observer=observer_events();events=[x for x in observer.get('events',[]) if x.get('observed_perf_ms',0)>=click_mark['perf']]
 toast_nodes=ev('''(()=>({count:document.querySelectorAll('[data-sonner-toast]').length,statusCount:document.querySelectorAll('[role=status],[role=alert]').length}))()''')
 feedback={'copy_button_label':click_label,'click_started_utc':click_mark['utc'],'click_started_perf_ms':click_mark['perf'],'observer_baseline_utc':baseline['baseline_utc'],'new_feedback_event_count':len(events),'events':events,'toast_nodes_after_click':toast_nodes,'copy_button_click_count':1,'known_product_copy_strings':list(ALLOW_TEXTS.keys())}
 save('deny-copy-feedback-oracle.json',{'captured_at_utc':utc(),**feedback,'raw_execution_logs_saved':False,'private_values_saved':False})
 # Screenshot only the toast element; never capture the log pane.
 screenshot={'available':False,'path':None}
 if toast_nodes['count']:
  dest=EV/'deny-copy-toast.png';p=subprocess.run(AB+['screenshot','[data-sonner-toast]',str(dest)],capture_output=True,text=True,timeout=20)
  screenshot={'available':p.returncode==0 and dest.exists(),'path':'evidence/deny-copy-toast.png' if p.returncode==0 and dest.exists() else None}
 save('deny-copy-feedback-oracle.json',{'captured_at_utc':utc(),**feedback,'toast_only_screenshot':screenshot,'raw_execution_logs_saved':False,'private_values_saved':False})
 stage='PERMISSION_REGRANT_AND_READBACK';regrant_guard=target_guard();set_permission('clipboard-read','granted',False);set_permission('clipboard-write','granted',False);regranted=perm_state()
 if regranted!={'read':'granted','write':'granted'}:raise RuntimeError('Clipboard Permissions API did not confirm regrant for readback')
 readback=ev('''(async()=>{let x=null,state='ok';try{x=await navigator.clipboard.readText()}catch{state='rejected'};const d=document.querySelector('#execution-logs pre')?.textContent||'',h=async t=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(t));return [...new Uint8Array(b)].map(v=>v.toString(16).padStart(2,'0')).join('')};return {read_state:state,clipboard_bytes:x===null?null:new TextEncoder().encode(x).length,clipboard_sha256:x===null?null:await h(x),matches_sentinel:x!==null&&await h(x)==='''+json.dumps(seed_sha)+''',matches_displayed_dom:x!==null&&x===d,dom_bytes:new TextEncoder().encode(d).length,dom_sha256:await h(d)}})()''')
 save('clipboard-denied-effect-oracle.json',{'captured_at_utc':utc(),'read_after_regrant':readback,'sentinel_bytes':len(sentinel.encode()),'sentinel_sha256':seed_sha,'regrant_permission_state':regranted,'target_guard':regrant_guard,'clipboard_plaintext_saved':False,'private_values_saved':False})
 result={'execution_dom_matches_api':api_dom['dom_matches_api_stdout'],'fresh_toast_baseline_empty':baseline['existing_toast_nodes']==0,'both_clipboard_permissions_denied':denied=={'read':'denied','write':'denied'},'real_copy_clicked_once':True,'new_feedback_event_count':len(events),'feedback_events':events,'clipboard_after_denied_click':{'matches_sentinel':readback['matches_sentinel'],'matches_displayed_dom':readback['matches_displayed_dom'],'bytes':readback['clipboard_bytes'],'sha256':readback['clipboard_sha256']},'toast_screenshot':screenshot,'permissions_reset_in_finally':False}
 progress('COPY_DENY_ORACLE_CAPTURED',outcome=result)
except Exception as e:
 meta={'stage':stage,'exception_type':type(e).__name__,'message':str(e)[:220] if not isinstance(e,CdpError) else 'CDP call rejected; filtered metadata saved'}
 if isinstance(e,CdpError):meta['cdp_error']=err_meta(e,None)
 save('runner-error.json',{'captured_at_utc':utc(),**meta,'private_values_saved':False,'raw_network_or_command_content_saved':False})
 result={'failed_stage':stage,'exception_type':type(e).__name__,'message':meta['message']}
 print(json.dumps({'phase':'STEP_FAILED','stage':stage,'exception_type':type(e).__name__}),flush=True)
finally:
 cleanup={'captured_at_utc':utc(),'permissions_reset_for_owned_default_context':False,'permission_state_after_reset':None,'browser_closed':False,'network_cleared':False,'dedicated_logout':None,'ssh_guard_matched':False,'ssh_stopped':False,'listener_after_stop':None,'listener_gone':False,'private_values_saved':False}
 if cdp is not None and context_id:
  try:
   cleanup['target_guard_before_reset']=target_guard();cdp_call('Browser.resetPermissions');cleanup['permission_reset_command_acknowledged']=True;cleanup['permission_state_after_reset']=perm_state();cleanup['permissions_reset_for_owned_default_context']=True
  except Exception as e:cleanup['permission_reset_error']=err_meta(e,None) if isinstance(e,CdpError) else {'type':type(e).__name__,'message':str(e)[:180]}
 if opened:
  try:cleanup['network_cleared']=subprocess.run(AB+['network','requests','--clear'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
  try:
   cleanup['dedicated_logout']=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let post=0,old=0;if(t){post=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;old=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,logout_http:post,old_me_http:old,token_removed:!localStorage.getItem('nodesteer_token'),online:navigator.onLine,secure:isSecureContext}})()''')
  except Exception:cleanup['dedicated_logout']={'completed':False}
  try:cleanup['browser_closed']=subprocess.run(AB+['close'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:pass
 if cdp:
  try:cdp.close()
  except Exception:pass
 if tunnel:
  try:
   alive=tunnel.poll() is None and proc_start(tunnel.pid)==startticks and hashlib.sha256(b'\0'.join(proc_argv(tunnel.pid))).hexdigest()==argv_hash;cleanup['ssh_guard_matched']=bool(alive)
   if alive:os.kill(tunnel.pid,signal.SIGTERM);tunnel.wait(timeout=8);cleanup['ssh_stopped']=tunnel.poll() is not None
   cleanup['listener_after_stop']=listener_state();cleanup['listener_gone']=cleanup['listener_after_stop']['count']==0
  except Exception:pass
 save('cleanup-verification.json',cleanup)
 feedback_path=R/'deny-copy-feedback-oracle.json';feedback_data=json.loads(feedback_path.read_text()) if feedback_path.exists() else {}
 f_events=feedback_data.get('events',[]);fresh=bool(f_events)
 deny=json.loads((R/'permission-deny-oracle.json').read_text()) if (R/'permission-deny-oracle.json').exists() else {}
 effect=json.loads((R/'clipboard-denied-effect-oracle.json').read_text()) if (R/'clipboard-denied-effect-oracle.json').exists() else {}
 overall='PASS' if deny.get('both_denied') and fresh and cleanup.get('permissions_reset_for_owned_default_context') and cleanup.get('browser_closed') and cleanup.get('network_cleared') and (cleanup.get('dedicated_logout') or {}).get('old_me_http')==401 and cleanup.get('ssh_stopped') and cleanup.get('listener_gone') else 'PARTIAL'
 save('clipboard-denial-result.json',{'captured_at_utc':utc(),'status':overall,'retained_execution_id':EXEC,'new_tasks':0,'new_runs':0,'execution_dom_oracle':'execution-dom-oracle.json','feedback_oracle':'deny-copy-feedback-oracle.json','permission_oracle':'permission-deny-oracle.json','clipboard_effect_oracle':'clipboard-denied-effect-oracle.json' if effect else None,'cleanup':cleanup,'outcome':result,'private_values_saved':False,'raw_execution_logs_or_network_content_saved':False})
 progress('FINALIZED_'+overall,status=overall,cleanup=cleanup,outcome=result,next_action='No Task/Run. Hand off denial feedback and actual clipboard effect to Root.')
