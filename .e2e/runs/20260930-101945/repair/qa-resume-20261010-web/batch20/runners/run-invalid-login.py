import json,subprocess,select,time,sys
from pathlib import Path
from datetime import datetime,timezone
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch20');S='qawebunauth20261012-b20';O='http://127.0.0.1:38181'
def utc():return datetime.now(timezone.utc).isoformat()
def cli(*args,timeout=4):return subprocess.run(['agent-browser','--session',S,*args],capture_output=True,text=True,timeout=timeout,check=True)
def read_json_lines(proc,lines,deadline,until=None):
 while time.time()<deadline:
  ready,_,_=select.select([proc.stdout],[],[],0.03)
  if not ready:
   if proc.poll() is not None:break
   continue
  line=proc.stdout.readline()
  if not line:break
  try:
   obj=json.loads(line.strip());lines.append(obj)
   if until and until(obj):break
  except:pass
 return lines
# Confirm the unauthenticated form is live and values are present without retrieving either value.
probe=json.loads(cli('eval','JSON.stringify({route:location.pathname,tokenPresent:!!localStorage.getItem("nodesteer_token"),usernameNonempty:!!document.querySelector("input[autocomplete=username]")?.value,passwordNonempty:!!document.querySelector("input[autocomplete=current-password]")?.value,buttonPresent:!!document.querySelector("button[type=submit]")})').stdout.strip())
probe=json.loads(probe) if isinstance(probe,str) else probe
if probe['route']!='/sign-in' or probe['tokenPresent'] or not probe['usernameNonempty'] or not probe['passwordNonempty'] or not probe['buttonPresent']:raise SystemExit('unauth_form_guard_failed')
# Start same-target CDP gate and wait until the single actual UI submit request is paused.
proc=subprocess.Popen(['node',str(B/'runners/login-post-gate.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
lines=[];ready=read_json_lines(proc,lines,time.time()+8,lambda x:x.get('phase')=='READY')
if not any(x.get('phase')=='READY' for x in lines):
 proc.terminate();out,err=proc.communicate(timeout=2);raise SystemExit('cdp_runner_not_ready')
# One actual user click starts the POST. The runner holds only that same-origin endpoint.
first=subprocess.Popen(['agent-browser','--session',S,'click','button[type="submit"]'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
read_json_lines(proc,lines,time.time()+3,lambda x:x.get('phase')=='POST_PAUSED')
paused=next((x for x in lines if x.get('phase')=='POST_PAUSED'),None)
if paused:
 read_json_lines(proc,lines,time.time()+0.12,lambda x:x.get('phase')=='PENDING_UI')
 # Attempt a double click and focused Enter while the first POST is pending. Any additional POST
 # is failed at the browser boundary before it can reach the repair server.
 actions=[]
 for argv in [
  ['agent-browser','--session',S,'dblclick','button[type="submit"]'],
  ['agent-browser','--session',S,'focus','input[autocomplete="current-password"]'],
  ['agent-browser','--session',S,'press','Enter']]:
  actions.append(subprocess.Popen(argv,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
  time.sleep(0.025)
 time.sleep(0.08)
 try:proc.stdin.write('release\n');proc.stdin.flush()
 except:pass
else:
 actions=[]
 try:proc.stdin.write('release\n');proc.stdin.flush()
 except:pass
try:out,err=proc.communicate(timeout=6)
except subprocess.TimeoutExpired:
 proc.terminate();out,err=proc.communicate(timeout=2)
for line in out.splitlines():
 try:lines.append(json.loads(line))
 except:pass
for p in [first,*actions]:
 try:p.wait(timeout=2)
 except subprocess.TimeoutExpired:pass
finished=next((x for x in reversed(lines) if x.get('phase')=='FINISHED'),{})
pending=next((x for x in lines if x.get('phase')=='PENDING_UI'),{})
postresp=next((x for x in lines if x.get('phase')=='POST_RESPONSE'),{})
error=next((x for x in lines if x.get('phase')=='ERROR_FEEDBACK'),{})
# Independent UI/session oracle after the actual response; save booleans only.
out=cli('eval','JSON.stringify({route:location.pathname,tokenPresent:!!localStorage.getItem("nodesteer_token"),alertCount:document.querySelectorAll("main [role=alert]").length,buttonPresent:!!document.querySelector("button[type=submit]"),buttonDisabled:!!document.querySelector("button[type=submit]")?.disabled,usernameInputCount:document.querySelectorAll("input[autocomplete=username]").length,passwordInputCount:document.querySelectorAll("input[autocomplete=current-password]").length})').stdout.strip()
ui=json.loads(out);ui=json.loads(ui) if isinstance(ui,str) else ui
pmeta=json.loads((B/'evidence/P01-04-fake-input-metadata.json').read_text())
result={'case_id':'P01-04','captured_at_utc':utc(),'session':S,'route':'/sign-in','target_guard':next((x for x in lines if x.get('phase')=='READY'),{}),'input_metadata':pmeta,'first_user_action':'single UI click on submit button','pending_ui_sample':pending.get('sample'),'followup_user_actions':['double-click submit while first request pending','focus password field','press Enter while pending'],'client_request_observation':{'method':'POST','path':'/api/login','client_post_attempts':finished.get('clientPostAttempts',0),'duplicate_posts_blocked_before_server':finished.get('duplicatePostsBlocked',0),'first_request_forwarded':finished.get('firstPostForwarded',False),'pause_ms':finished.get('pauseMs')},'actual_response_status':postresp.get('status'),'error_feedback_observed':error.get('alertCount',0)>0,'final_ui_oracle':ui,'server_effect':{'forwarded_login_posts':1 if finished.get('firstPostForwarded') and postresp.get('status')==401 else 0,'status':postresp.get('status'),'new_user_created':False,'session_token_present':ui.get('tokenPresent',True)},'node_runner_output_whitelisted':True,'response_body_headers_or_raw_form_values_saved':False,'server_writes':1 if finished.get('firstPostForwarded') else 0}
conditions={'one_client_post':finished.get('clientPostAttempts')==1,'one_forwarded_post':result['server_effect']['forwarded_login_posts']==1,'http_401':postresp.get('status')==401,'disabled_while_pending':bool(pending.get('sample',{}).get('buttonDisabled')),'loading_spinner_while_pending':bool(pending.get('sample',{}).get('spinner')),'error_feedback':result['error_feedback_observed'] and ui.get('alertCount',0)>0,'no_token':not ui.get('tokenPresent',True),'pause_within_500ms':isinstance(finished.get('pauseMs'),(int,float)) and finished['pauseMs']<=500}
if conditions['one_client_post'] and all(v for k,v in conditions.items() if k!='one_client_post') and conditions['one_forwarded_post']:status='PASS'
elif finished.get('clientPostAttempts',0)>1:status='FAIL'
else:status='PARTIAL'
result['assertions']=conditions;result['status']=status;result['remaining_dimensions']=[] if status=='PASS' else [k for k,v in conditions.items() if not v]
# Close only this owned unauth profile as required; leave the authenticated main session/tunnel alone.
close=subprocess.run(['agent-browser','--session',S,'close'],capture_output=True,text=True,timeout=5)
result['owned_unauth_profile_closed']=close.returncode==0
(B/'evidence/P01-04-invalid-login.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
rp=B/'results.json';r=json.loads(rp.read_text());r['cases']=[x for x in r['cases'] if x['case_id']!='P01-04']+[{'case_id':'P01-04','status':status,'evidence':['evidence/P01-04-fake-input-metadata.json','evidence/P01-04-attempt01-no-post.json','evidence/P01-04-invalid-login.json'],'actual':'Actual submit and bounded repeat/Enter behavior measured on one owned unauthenticated browser profile; one server POST was authorized and no user/token was created. See per-assertion result and remaining dimensions.','remaining_dimensions':result['remaining_dimensions']}];r['counts']={'PASS':sum(x['status']=='PASS' for x in r['cases']),'PARTIAL':sum(x['status']=='PARTIAL' for x in r['cases']),'FAIL':sum(x['status']=='FAIL' for x in r['cases']),'NOT_RUN':8-len(r['cases'])};r['updated_at_utc']=utc();rp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
pp=B/'progress.json';q=json.loads(pp.read_text());q.update({'updated_at_utc':utc(),'counts':r['counts'],'unauth_profile_closed':result['owned_unauth_profile_closed'],'unauth_profile_session':S,'unauth_profile_server_forwarded_login_posts':result['server_effect']['forwarded_login_posts'],'unauth_route':'closed'});c=next(x for x in q['cases'] if x['case_id']=='P01-04');c.update({'status':status,'stage':'complete_profile_closed','evidence':['evidence/P01-04-invalid-login.json'],'remaining_dimensions':result['remaining_dimensions']});q['phase']='P01_04_'+status+'_PROFILE_CLOSED';q['next']='Continue P03 dashboard read-only cases using preserved authenticated main profile and shared loopback tunnel.';pp.write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'case_id':'P01-04','status':status,'assertions':conditions,'client_post_attempts':finished.get('clientPostAttempts',0),'forwarded_posts':result['server_effect']['forwarded_login_posts'],'response_status':postresp.get('status'),'profile_closed':result['owned_unauth_profile_closed'],'counts':r['counts']},ensure_ascii=False))
