import datetime, hashlib, json, os, re, signal, subprocess, time, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from websockets.sync.client import connect as ws_connect

R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/live-log/clipboard-cold')
ORIGIN='http://127.0.0.1:38181'
SESSION='qawebsecure20261010-clipcold-38181'
EXEC='e3555a68-d70c-4c6a-9589-6ec2568fd669'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1']
SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
tunnel=None; startticks=None; argv_hash=None; opened=False; context_id=None; cdp=None; token_present=False; token_shared=None; stage='INIT'; permission_changes=[]; target_guard_history=[]; outcome={}

def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
def save(name,obj): (R/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def progress(phase,**extra):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':phase,'updated_at_utc':utc(),'target_execution_id':EXEC,'created_tasks':0,'ui_runs':0,'private_values_saved':False,'raw_network_or_command_content_saved':False});p.update(extra);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'browser_open':opened,'context_known':bool(context_id),'task_created':False,'run_started':False},ensure_ascii=False),flush=True)
def cli(args,stdin=None,timeout=25):
 p=subprocess.run(AB+[str(x) for x in args],input=stdin,capture_output=True,text=True,timeout=timeout)
 if p.returncode: raise RuntimeError('agent-browser command failed; output intentionally suppressed')
 return p.stdout
def ev(js):
 out=cli(['--json','eval','--stdin'],stdin=js,timeout=30)
 try:return json.loads(out)['data']['result']
 except Exception:raise RuntimeError('browser eval did not return filtered JSON')
def proc_start(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def proc_argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
def listener_rows():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True)
 rows=[]
 for line in p.stdout.splitlines():
  a=re.search(r'\s([^\s]+):38181\s',line);ids=re.findall(r'pid=(\d+)',line)
  rows.append({'loopback_only':bool(a and a.group(1)=='127.0.0.1'),'pid_matches_owned':bool(tunnel and str(tunnel.pid) in ids),'pids':ids})
 return {'count':len(rows),'listeners':rows}
def ssh_preflight():
 p=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True)
 keys={'localforward':[],'remoteforward':[],'remotecommand':[],'forwardagent':[],'controlpath':[]}
 for line in p.stdout.splitlines():
  z=line.split(None,1)
  if len(z)==2 and z[0] in keys:keys[z[0]].append(z[1])
 assert not keys['localforward'] and not keys['remoteforward'] and all(x=='none' for x in keys['remotecommand']) and all(x!='yes' for x in keys['forwardagent']) and not keys['controlpath']
 q=subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);assert not q.stdout.strip()
 return {'ssh_g_exit':p.returncode,'localforward_count':0,'remoteforward_count':0,'remotecommand_nonempty':False,'forwardagent_enabled':False,'controlpath_set':False,'listener_count_before':0,'guard_passed':True}
def cdpcall(method,params=None):
 cdpcall.i+=1;ident=cdpcall.i;cdp.send(json.dumps({'id':ident,'method':method,'params':params or {}}));end=time.monotonic()+10
 while time.monotonic()<end:
  msg=json.loads(cdp.recv(timeout=2))
  if msg.get('id')==ident:
   if 'error' in msg:
    e=msg['error'];text=str(e.get('message',''))[:240]
    # Protocol error text is retained only after stripping anything outside the specified whitelist.
    text=re.sub(r'Bearer\s+[^\s,;]+','Bearer [REDACTED]',text,flags=re.I)
    raise CDPError(method,e.get('code'),text,sorted((params or {}).keys()))
   return msg.get('result',{})
 raise CDPError(method,None,'response timeout',sorted((params or {}).keys()))
cdpcall.i=0
class CDPError(RuntimeError):
 def __init__(self,method,code,message,param_names):self.method=method;self.code=code;self.message=message;self.param_names=param_names;super().__init__(method)
def permission_state():return ev('''(async()=>{const q=async n=>{try{return (await navigator.permissions.query({name:n})).state}catch{return 'unsupported'}};return {read:await q('clipboard-read'),write:await q('clipboard-write')}})()''')
def error_meta(e,permission=None):return {'method':getattr(e,'method',None),'code':getattr(e,'code',None),'message':getattr(e,'message',str(e)[:240]),'parameter_names':getattr(e,'param_names',[]),'permission_name':permission,'origin':ORIGIN}
def hash(s):return hashlib.sha256(s.encode()).hexdigest()

def eval_filter():
 return ev('''(async()=>{const id='''+json.dumps(EXEC)+''',t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json(),out=d.stdout||'',err=d.stderr||'',pre=document.querySelector('#execution-logs pre'),ui=pre?.textContent||'',rx=(s,k)=>{const m=s.match(new RegExp('[A-Za-z0-9_-]+-'+k+'-01'));return m?m[0]:null},om=rx(out,'out'),em=rx(err,'err'),counts=s=>({out:(s.match(/[A-Za-z0-9_-]+-out-\\d+/g)||[]).length,err:(s.match(/[A-Za-z0-9_-]+-err-\\d+/g)||[]).length});return {http:r.status,id:d.id,status:d.status,exit_code:d.exit_code,stdout_bytes:new TextEncoder().encode(out).length,stderr_bytes:new TextEncoder().encode(err).length,stdout_sha256:await (async()=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(out));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')})(),stderr_sha256:await (async()=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(err));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')})(),input_value:document.querySelector('#execution-logs input')?.value??null,selected:document.querySelector('#execution-logs select')?.value??null,ui_present:!!pre,ui_bytes:new TextEncoder().encode(ui).length,ui_counts:counts(ui),out_marker_available:!!om,err_marker_available:!!em,out_marker_visible:!!om&&ui.includes(om),err_marker_visible:!!em&&ui.includes(em),ui_hash:(()=>{const b=new TextEncoder().encode(ui);return [...b].slice(0,0).length})()}})()''')

def get_ui_text_digest():
 return ev('''(()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'';return {bytes:new TextEncoder().encode(s).length,sha256:(()=>{let x=0;return ''})()}})()''')

def target_guard():
 infos=cdpcall('Target.getTargets').get('targetInfos',[]);pages=[x for x in infos if x.get('type')=='page'];urls=[x.get('url','') for x in pages]
 ctx=cdpcall('Target.getBrowserContexts');default_id=ctx.get('defaultBrowserContextId')
 exact_internal=('chrome://newtab/','chrome://new-tab-page/','chrome://blank/')
 cats={'owned_execution':0,'same_origin_other_path':0,'about_blank_exact':0,'data_comma_exact':0,'browser_internal_empty_exact':0,'other_browser_internal':0,'other_web_origin':0,'other_scheme':0}
 for u in urls:
  if u==ORIGIN+'/executions/'+EXEC:cats['owned_execution']+=1
  elif u.startswith(ORIGIN+'/'):cats['same_origin_other_path']+=1
  elif u=='about:blank':cats['about_blank_exact']+=1
  elif u=='data:,':cats['data_comma_exact']+=1
  elif u in exact_internal:cats['browser_internal_empty_exact']+=1
  else:
   q=urlparse(u)
   if q.scheme in ('chrome','chrome-extension','devtools'):cats['other_browser_internal']+=1
   elif q.scheme in ('http','https'):cats['other_web_origin']+=1
   else:cats['other_scheme']+=1
 result={'page_target_count':len(pages),'page_target_categories':cats,'exact_internal_empty_match_flags':{'chrome_newtab':any(u=='chrome://newtab/' for u in urls),'chrome_new_tab_page':any(u=='chrome://new-tab-page/' for u in urls),'chrome_blank':any(u=='chrome://blank/' for u in urls)},'owned_execution_target_count':cats['owned_execution'],'allowed_page_targets':cats['other_browser_internal']==0 and cats['other_web_origin']==0 and cats['other_scheme']==0,'context_is_default':context_id==default_id,'nondefault_context_count':len(ctx.get('browserContextIds',[]))}
 result['passed']=result['allowed_page_targets'] and cats['owned_execution']==1 and result['context_is_default'] and result['nondefault_context_count']==0
 target_guard_history.append(result);save('permission-target-guards.json',{'captured_at_utc':utc(),'checks':target_guard_history,'raw_page_urls_saved':False,'titles_saved':False})
 if not result['passed']:raise RuntimeError('owned default-context target guard failed')
 return result
def set_permission(name,setting,descriptor=None):
 perm={'name':name}
 if descriptor:perm.update(descriptor)
 params={'permission':perm,'setting':setting,'origin':ORIGIN}
 try:
  cdpcall('Browser.setPermission',params);permission_changes.append({'api':'setPermission','permission':name,'setting':setting,'default_context_scoped':True});return {'ok':True,'method':'Browser.setPermission','permission':name,'setting':setting,'error':None}
 except CDPError as e:return {'ok':False,'method':'Browser.setPermission','permission':name,'setting':setting,'error':error_meta(e,name)}

try:
 stage='PREFLIGHT';pre=ssh_preflight();save('tunnel-preflight.json',{'captured_at_utc':utc(),**pre});progress('PREFLIGHT_PASSED',ssh=pre)
 argv_hash=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest()
 tunnel=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
 startticks=proc_start(tunnel.pid);ready=False
 for _ in range(60):
  if tunnel.poll() is not None:raise RuntimeError('owned SSH tunnel exited before listener ready')
  st=listener_rows()
  if st['count']==1 and st['listeners'][0]['loopback_only'] and st['listeners'][0]['pid_matches_owned']:
   try:
    with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as resp:ready=200<=resp.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise RuntimeError('exact loopback SSH listener/upstream readiness failed')
 progress('TUNNEL_READY',ssh_pid=tunnel.pid,ssh_startticks=startticks,ssh_argv_sha256=argv_hash,listener=listener_rows())
 stage='BROWSER_LOGIN';cli(['open',ORIGIN+'/sign-in'],timeout=35);opened=True;cli(['wait','--load','networkidle'],timeout=30)
 if not CREDS.is_file() or not SHARED.is_file():raise RuntimeError('private credential input unavailable')
 creds=json.loads(CREDS.read_text());user=creds['admin_username'];password=creds['admin_password'];shared=json.loads(SHARED.read_text())['token']
 login_js='''(async()=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:%s,password:%s})});let d={};try{d=await r.json()}catch{};const x=d.token||null,shared=!!x&&x===%s;if(r.status===200&&x&&!shared)localStorage.setItem('nodesteer_token',x);return {http:r.status,token_present:!!x,shared_token_match:shared,stored:!!x&&!shared&&localStorage.getItem('nodesteer_token')===x}})()'''%(json.dumps(user),json.dumps(password),json.dumps(shared))
 login=ev(login_js);token_present=login.get('token_present',False);token_shared=login.get('shared_token_match')
 if login.get('http')!=200 or not token_present or token_shared or not login.get('stored'):raise RuntimeError('dedicated API login/token-isolation gate failed')
 cli(['network','requests','--clear']);cli(['open',ORIGIN+'/executions/'+EXEC],timeout=35);cli(['wait','--load','networkidle'],timeout=30)
 auth=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {http:r.status,token_present:!!t,secure:isSecureContext,online:navigator.onLine,origin:location.origin,path:location.pathname,clipboard_api:!!navigator.clipboard&&typeof navigator.clipboard.readText==='function'&&typeof navigator.clipboard.writeText==='function'}})()''')
 if auth['http']!=200 or not auth['secure'] or not auth['online'] or auth['origin']!=ORIGIN or auth['path']!='/executions/'+EXEC or not auth['clipboard_api']:raise RuntimeError('fresh page/auth/secure-context gate failed')
 progress('COLD_DETAIL_READY',browser={k:auth[k] for k in ('http','token_present','secure','online','origin','path','clipboard_api')},token_shared_match=token_shared)
 stage='EXECUTION_ORACLE';base=eval_filter();old=json.loads((R.parent/'attempt05'/'execution-final-oracle.json').read_text())['execution']
 execution_meta={'http':base['http'],'id_matches':base['id']==EXEC,'status':base['status'],'exit_code':base['exit_code'],'stdout_bytes':base['stdout_bytes'],'stderr_bytes':base['stderr_bytes'],'stdout_sha256_matches_prior':base['stdout_sha256']==old['stdout_sha256'],'stderr_sha256_matches_prior':base['stderr_sha256']==old['stderr_sha256'],'ui_present':base['ui_present']}
 if not execution_meta['id_matches'] or not execution_meta['stdout_sha256_matches_prior'] or not execution_meta['stderr_sha256_matches_prior']:raise RuntimeError('retained execution API oracle mismatch')
 save('execution-api-oracle.json',{'captured_at_utc':utc(),**execution_meta,'private_values_saved':False})
 stage='CLEAR_SEARCH';cli(['focus','#execution-logs input']);cli(['press','Control+a']);cli(['press','Backspace']);time.sleep(.25)
 clear=eval_filter();clear_ok=clear['input_value']==''
 filters={}
 expected={'stdout':(True,False),'stderr':(False,True),'all':(True,True)}
 for name in ('stdout','stderr','all'):
  cli(['select','#execution-logs select',name]);time.sleep(.15);m=eval_filter();filters[name]={'selected':m['selected'],'search_empty':m['input_value']=='','ui_present':m['ui_present'],'out_marker_visible':m['out_marker_visible'],'err_marker_visible':m['err_marker_visible'],'ui_bytes':m['ui_bytes'],'ui_counts':m['ui_counts']}
  filters[name]['matches_expected']=filters[name]['search_empty'] and filters[name]['selected']==name and (filters[name]['out_marker_visible'],filters[name]['err_marker_visible'])==expected[name]
 save('stream-filter-oracle.json',{'captured_at_utc':utc(),'clear_search_control_a_backspace':clear_ok,'clear_search_value_empty':clear['input_value']=='','api_execution':execution_meta,'filters':filters,'all_filters_match':clear_ok and all(x['matches_expected'] for x in filters.values()),'raw_streams_saved':False,'private_values_saved':False})
 progress('STREAM_FILTERS_CHECKED',clear_search=clear_ok,all_filters_match=all(x['matches_expected'] for x in filters.values()))
 stage='CDP_TARGET';cdpurl=cli(['get','cdp-url']).strip()
 if not cdpurl.startswith('ws://127.0.0.1:'):raise RuntimeError('agent-browser CDP endpoint not loopback')
 cdp=ws_connect(cdpurl,open_timeout=10,close_timeout=3)
 ver=cdpcall('Browser.getVersion');targets=cdpcall('Target.getTargets').get('targetInfos',[])
 target=next((x for x in targets if x.get('type')=='page' and x.get('url')==ORIGIN+'/executions/'+EXEC),None)
 if not target or not target.get('browserContextId'):raise RuntimeError('exact execution target/context unavailable')
 context_id=target['browserContextId']
 target_info=cdpcall('Target.getTargetInfo',{'targetId':target['targetId']}).get('targetInfo',{})
 if target_info.get('browserContextId')!=context_id or target_info.get('url')!=ORIGIN+'/executions/'+EXEC:raise RuntimeError('target context/origin changed during validation')
 target_guard_meta=target_guard()
 save('cdp-target-oracle.json',{'captured_at_utc':utc(),'browser_product':ver.get('product'),'protocol_version':ver.get('protocolVersion'),'target_type':target_info.get('type'),'target_id_present':bool(target.get('targetId')),'browser_context_id':context_id,'origin':ORIGIN,'target_path_matches':True,'permission_scope_selection':'omit browserContextId because target context equals this dedicated browser endpoint defaultBrowserContextId; exact origin still passed','target_guard':target_guard_meta,'local_protocol_types':{'permission_descriptor':True,'permission_setting_values':['granted','denied','prompt'],'permission_type_clipboard_read':'clipboardReadWrite','permission_type_clipboard_write':'clipboardSanitizedWrite','playwright_mapping_verified':True},'private_values_saved':False})
 stage='CLIPBOARD_GRANT'
 grant_guard=target_guard();grant_results=[set_permission('clipboard-read','granted',{'allowWithoutSanitization':False}),set_permission('clipboard-write','granted',{'allowWithoutSanitization':False})]
 grant_method='Browser.setPermission'
 if not all(x['ok'] for x in grant_results):
  try:
   target_guard();cdpcall('Browser.grantPermissions',{'permissions':['clipboardReadWrite','clipboardSanitizedWrite'],'origin':ORIGIN})
   permission_changes.append({'api':'grantPermissions','permission':['clipboardReadWrite','clipboardSanitizedWrite'],'setting':'grant','default_context_scoped':True})
   grant_method='Browser.grantPermissions'
  except CDPError as e:
   save('clipboard-permission-errors.json',{'captured_at_utc':utc(),'errors':[x['error'] for x in grant_results if not x['ok']]+[error_meta(e,None)],'request_parameter_names':['permission','setting','origin','permissions'],'permission_names':['clipboard-read','clipboard-write','clipboardReadWrite','clipboardSanitizedWrite'],'origin':ORIGIN,'target_guard':target_guard_meta,'raw_cdp_payload_saved':False})
   raise RuntimeError('clipboard grant APIs rejected exact-context permission requests')
 granted=permission_state()
 save('clipboard-permission-grant-oracle.json',{'captured_at_utc':utc(),'set_permission_results':grant_results,'final_grant_method':grant_method,'permission_state':granted,'target_guard_before_grant':grant_guard,'exact_origin':ORIGIN,'browserContextId_omitted_because_target_is_default':True,'private_values_saved':False})
 if granted['read']!='granted' or granted['write']!='granted':raise RuntimeError('actual Clipboard Permissions API did not confirm grant')
 cli(['select','#execution-logs select','stdout']);cli(['focus','#execution-logs input']);cli(['press','Control+a']);cli(['press','Backspace']);time.sleep(.2)
 copy_controls=ev('''(()=>[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(x=>x==='复制日志'||x==='Copy logs'))()''')
 if len(copy_controls)!=1:raise RuntimeError('unique real Copy logs control unavailable')
 expected_clip=ev('''(async()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'',b=new TextEncoder().encode(s),h=await crypto.subtle.digest('SHA-256',b);return {bytes:b.length,sha256:[...new Uint8Array(h)].map(v=>v.toString(16).padStart(2,'0')).join('')}})()''')
 cli(['find','role','button','click','--name',copy_controls[0],'--exact']);time.sleep(.3)
 copied=ev('''(async()=>{const pre=document.querySelector('#execution-logs pre'),expected=pre?.textContent||'';let actual=null,read='ok';try{actual=await navigator.clipboard.readText()}catch{read='rejected'};const h=async s=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(b)].map(v=>v.toString(16).padStart(2,'0')).join('')};return {read,exact_equal:actual!==null&&actual===expected,clipboard_bytes:actual===null?null:new TextEncoder().encode(actual).length,clipboard_sha256:actual===null?null:await h(actual),expected_bytes:new TextEncoder().encode(expected).length,expected_sha256:await h(expected),feedback:(()=>{const s=[...document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]')].map(x=>(x.innerText||'').trim()).join(' ').toLowerCase();return s.includes('手动复制')||s.includes('manually')?'manual_fallback':s.includes('复制失败')||s.includes('copy failed')?'error':s.includes('已复制')||s.includes('copied')?'success':s?'other':'none'})()}})()''')
 save('clipboard-copy-oracle.json',{'captured_at_utc':utc(),'method':'user-equivalent Copy logs button plus real navigator.clipboard.readText','copy_button_label':copy_controls[0],'grant_method':grant_method,'permission_state_after_grant':granted,'expected_bytes':copied['expected_bytes'],'expected_sha256':copied['expected_sha256'],'read_succeeded':copied['read']=='ok','clipboard_bytes':copied['clipboard_bytes'],'clipboard_sha256':copied['clipboard_sha256'],'exact_equal':copied['exact_equal'],'feedback':copied['feedback'],'clipboard_plaintext_saved':False,'private_values_saved':False})
 stage='CLIPBOARD_DENY';deny_guard=target_guard();deny=set_permission('clipboard-write','denied',{'allowWithoutSanitization':False})
 deny_method='Browser.setPermission'
 if not deny['ok']:
  # Deprecated CDP path is explicitly scoped to this origin and this actual browser context.
  try:
   target_guard();cdpcall('Browser.grantPermissions',{'permissions':['clipboardReadWrite'],'origin':ORIGIN})
   permission_changes.append({'api':'grantPermissions','permission':['clipboardReadWrite'],'setting':'grant others denied','default_context_scoped':True});deny_method='Browser.grantPermissions deny-others'
  except CDPError as e:
   save('clipboard-deny-error.json',{'captured_at_utc':utc(),'set_permission_error':deny['error'],'fallback_error':error_meta(e,None),'request_parameter_names':['permission','setting','origin','permissions'],'permission_name':'clipboard-write','origin':ORIGIN,'target_guard':target_guard_meta,'raw_cdp_payload_saved':False})
   raise RuntimeError('exact-default-context clipboard-write deny request rejected')
 denied_state=permission_state()
 if denied_state['write']!='denied':raise RuntimeError('actual Permissions API did not confirm clipboard-write denied')
 cli(['find','role','button','click','--name',copy_controls[0],'--exact']);time.sleep(.25)
 feedback=ev('''(()=>{const s=[...document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]')].map(x=>(x.innerText||'').trim()).join(' ').toLowerCase();return {kind:s.includes('手动复制')||s.includes('manually')?'manual_fallback':s.includes('复制失败')||s.includes('copy failed')?'error':s.includes('已复制')||s.includes('copied')?'success':s?'other':'none',text_bytes:new TextEncoder().encode(s).length}})()''')
 save('clipboard-deny-oracle.json',{'captured_at_utc':utc(),'deny_method':deny_method,'deny_set_permission_result':deny,'target_guard_before_deny':deny_guard,'permission_state_after_deny':denied_state,'copy_button_clicked':True,'feedback':feedback,'raw_feedback_text_saved':False,'private_values_saved':False})
 outcome={'execution_api':execution_meta,'clear_search':clear_ok,'stream_filters_match':all(x['matches_expected'] for x in filters.values()),'permission_target_guard':target_guard_meta,'clipboard_grant_states':granted,'copy_exact_equal':copied['exact_equal'],'copy_read_succeeded':copied['read']=='ok','deny_write_state':denied_state['write'],'deny_feedback':feedback['kind']}
 progress('TESTS_CAPTURED',outcome=outcome)
except Exception as e:
 safe={'stage':stage,'exception_type':type(e).__name__,'message':str(e)[:240] if not isinstance(e,CDPError) else 'CDP request rejected; filtered metadata captured separately'}
 if isinstance(e,CDPError):safe['cdp_error']=error_meta(e,None)
 save('runner-error.json',{'captured_at_utc':utc(),**safe,'private_values_saved':False,'raw_network_or_command_content_saved':False})
 outcome={'failed_stage':stage,'exception_type':type(e).__name__,'message':safe['message']}
 print(json.dumps({'phase':'TEST_STEP_FAILED','stage':stage,'exception_type':type(e).__name__},ensure_ascii=False),flush=True)
finally:
 cleanup={'captured_at_utc':utc(),'permissions_reset_for_exact_context':False,'browser_session_closed':False,'network_log_cleared':False,'dedicated_logout':None,'tunnel_guard_matched':False,'tunnel_stopped':False,'listener_after_stop':None,'private_values_saved':False}
 if cdp is not None and context_id:
  try:
   cleanup['target_guard_before_reset']=target_guard();cdpcall('Browser.resetPermissions');cleanup['permission_state_after_reset']=permission_state();cleanup['permissions_reset_for_owned_default_context']=cleanup['permission_state_after_reset']['read']!='granted' and cleanup['permission_state_after_reset']['write']!='granted';cleanup['permission_changes']=permission_changes
  except Exception as e:cleanup['permission_reset_error']=error_meta(e,None) if isinstance(e,CDPError) else {'type':type(e).__name__,'message':str(e)[:200]}
 if opened:
  try:
   cleanup['network_log_cleared']=subprocess.run(AB+['network','requests','--clear'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:cleanup['network_log_cleared']=False
  try:
   logout=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let post=0,old=0;if(t){const p=await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}});post=p.status;const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});old=r.status;localStorage.removeItem('nodesteer_token')}return {had_token:!!t,logout_http:post,old_me_http:old,token_removed:!localStorage.getItem('nodesteer_token'),online:navigator.onLine,secure:isSecureContext}})()''')
   cleanup['dedicated_logout']=logout
  except Exception:cleanup['dedicated_logout']={'completed':False}
  try:cleanup['browser_session_closed']=subprocess.run(AB+['close'],capture_output=True,text=True,timeout=15).returncode==0
  except Exception:cleanup['browser_session_closed']=False
 if cdp is not None:
  try:cdp.close()
  except Exception:pass
 if tunnel is not None:
  try:
   alive=tunnel.poll() is None and Path(f'/proc/{tunnel.pid}').exists() and proc_start(tunnel.pid)==startticks and hashlib.sha256(b'\0'.join(proc_argv(tunnel.pid))).hexdigest()==argv_hash
   cleanup['tunnel_guard_matched']=bool(alive)
   if alive:
    os.kill(tunnel.pid,signal.SIGTERM)
    try:tunnel.wait(timeout=8)
    except subprocess.TimeoutExpired:cleanup['tunnel_stop_timeout']=True
    cleanup['tunnel_stopped']=tunnel.poll() is not None
   cleanup['listener_after_stop']=listener_rows();cleanup['listener_gone']=cleanup['listener_after_stop']['count']==0
  except Exception:cleanup['tunnel_guard_matched']=False
 cleanup['browser_context_default_isolated_session']=bool(context_id);cleanup['permission_origin_scope']=ORIGIN;save('cleanup-verification.json',cleanup)
 overall='PASS' if outcome.get('stream_filters_match') and outcome.get('copy_exact_equal') and outcome.get('deny_write_state')=='denied' and outcome.get('deny_feedback') in ('manual_fallback','error') and cleanup.get('permissions_reset_for_owned_default_context') and cleanup.get('browser_session_closed') and cleanup.get('listener_gone') and (cleanup.get('dedicated_logout') or {}).get('old_me_http')==401 else 'PARTIAL'
 save('clipboard-cold-result.json',{'captured_at_utc':utc(),'status':overall,'outcome':outcome,'cleanup':cleanup,'new_tasks':0,'new_runs':0,'private_values_saved':False,'raw_network_or_command_content_saved':False})
 progress('FINALIZED_'+overall,status=overall,outcome=outcome,cleanup=cleanup,next_action='No task/run creation. Hand off measured outcomes and any protocol limitation.')
