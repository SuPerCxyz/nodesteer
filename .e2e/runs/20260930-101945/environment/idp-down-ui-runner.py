#!/usr/bin/env python3
import os,pathlib,json,subprocess,urllib.request,urllib.error,time,re
PRIVATE=pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x'); OUT=pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/environment'); c=json.loads((PRIVATE/'isolated.json').read_text()); s=c['sso']; user=next(x for x in s['users'] if x['role']=='viewer')
env=dict(os.environ,AGENT_BROWSER_SOCKET_DIR=str(PRIVATE/'browser-runtime'),AGENT_BROWSER_PROFILE=str(PRIVATE/'browser-profile-idp-final'),AGENT_BROWSER_CONFIG=str(PRIVATE/'browser-config-idp.json'),AGENT_BROWSER_ARGS='--no-sandbox,--disable-setuid-sandbox,--disable-crash-reporter,--disable-breakpad,--crash-dumps-dir='+str(PRIVATE/'browser-crash'),XDG_CACHE_HOME=str(PRIVATE/'browser-cache'),XDG_CONFIG_HOME=str(PRIVATE/'browser-xdg-config'),TMPDIR=str(PRIVATE))
BASE=['agent-browser','--namespace','nse2e','--session','idp2']; SSH=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i','/home/superc/.ssh/id_ed25519','root@192.168.100.209']; result={'status':'running','steps':[]};serial=0;stopped=False

def safe(text):return re.sub(r'https?://[^\s"<>]+',lambda m:m.group(0).split('?')[0].split('#')[0],text)
def browser(args,js=None,allow_error=False):
 global serial
 serial+=1;r=subprocess.run(BASE+args,env=env,input=js,text=True,capture_output=True,timeout=25);(PRIVATE/('idp-browser-'+str(serial)+'.log')).write_text(r.stdout+r.stderr)
 if r.returncode and not allow_error:raise RuntimeError(safe(r.stderr or r.stdout)[:300])
 return r

def evaluate(js):
 r=browser(['--json','eval','--stdin'],js);value=json.loads(r.stdout)
 if not value.get('success'):raise RuntimeError(safe(value.get('error','browser evaluation failed')))
 return value['data']['result']
def request(path,body=None):
 req=urllib.request.Request(s['hub_url']+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=6) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:return e.code,json.loads(e.read() or b'{}')
try:
 browser(['open',s['hub_url']+'/sign-in']);snapshot=browser(['snapshot','-i']).stdout;assert 'SSO' in snapshot;result['initial_oidc_state']=request('/api/oidc/state')[1];assert result['initial_oidc_state']=={'enabled':True,'local_fallback':False}
 subprocess.run(SSH+['docker','stop',s['idp_container']],capture_output=True,check=True,timeout=30);stopped=True
 try:urllib.request.urlopen(s['issuer']+'/.well-known/openid-configuration',timeout=3);raise AssertionError('issuer still available after stop')
 except urllib.error.URLError as error:result['issuer_down_observed']={'error_type':type(error.reason).__name__,'connection_refused':isinstance(error.reason,ConnectionRefusedError)}
 click=browser(['find','role','button','click','--name','使用 SSO 登录'],allow_error=True);result['sso_click_while_down']={'exit_code':click.returncode,'error':safe(click.stderr or click.stdout)[:350] if click.returncode else None}
 for i in range(15):
  page=evaluate('(()=>{let token=false;try{token=!!localStorage.getItem("nodesteer_token")}catch{}return {origin:location.origin,path:location.pathname,title:document.title,body:document.body?.innerText||"",hasToken:token}})()');page['body']=safe(page['body'])
  if 'ERR_CONNECTION_REFUSED' in page['body'] or '拒绝了我们的连接' in page['body']:break
  time.sleep(1)
 result['browser_failure_while_idp_remained_stopped']=page;assert not page['hasToken'];assert 'ERR_CONNECTION_REFUSED' in page['body'] or '拒绝了我们的连接' in page['body'];browser(['screenshot',str(OUT/'idp-down-browser.png')])
 status,body=request('/api/login',{'username':s['bootstrap_username'],'password':s['bootstrap_password']});assert status==403;result['local_password_while_idp_down']={'status':status,'message':body.get('error')};result['oidc_state_while_idp_down']=request('/api/oidc/state')[1]
 subprocess.run(SSH+['docker','start',s['idp_container']],capture_output=True,check=True,timeout=20);stopped=False
 for i in range(30):
  try:
   with urllib.request.urlopen(s['issuer']+'/.well-known/openid-configuration',timeout=3) as response:result['discovery_recovered']=response.status
   break
  except urllib.error.URLError:time.sleep(1)
 assert result.get('discovery_recovered')==200
 browser(['open',s['hub_url']+'/sign-in']);browser(['find','role','button','click','--name','使用 SSO 登录']);snapshot=browser(['snapshot','-i']).stdout;result['idp_login_form_seen']=('Password' in snapshot or '密码' in snapshot);assert result['idp_login_form_seen']
 js='(()=>{const login=document.querySelector("input[name=login],input[type=email],input[type=text]");const password=document.querySelector("input[type=password]");if(!login||!password)throw new Error("IdP login fields missing");login.value='+json.dumps(user['email'])+';password.value='+json.dumps(user['password'])+';login.dispatchEvent(new Event("input",{bubbles:true}));password.dispatchEvent(new Event("input",{bubbles:true}));return true})()'
 evaluate(js);browser(['click','button[type=submit]'])
 for i in range(30):
  value=evaluate('(()=>{let token=false;try{token=!!localStorage.getItem("nodesteer_token")}catch{}return {origin:location.origin,path:location.pathname,hasToken:token}})()')
  if value['origin']==s['hub_url'] and value['hasToken']:break
  time.sleep(1)
 assert value['origin']==s['hub_url'] and value['hasToken'];me=evaluate('(async()=>{const token=localStorage.getItem("nodesteer_token");const response=await fetch("/api/me",{headers:{Authorization:"Bearer "+token}});const value=await response.json();return {status:response.status,username:value.Username||value.username,role:value.Role||value.role}})()');assert me['status']==200 and me['role']=='viewer';result['recovered_real_ui_login']=me;browser(['screenshot',str(OUT/'idp-recovered-browser.png')]);result['status']='passed'
except Exception as error:result['status']='failed';result['error']={'type':type(error).__name__,'message':safe(str(error))[:450]}
finally:
 if stopped:subprocess.run(SSH+['docker','start',s['idp_container']],capture_output=True,timeout=20)
 browser(['close'],allow_error=True);(OUT/'idp-down-ui-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({'idp_ui':result['status'],'issuer_down':result.get('issuer_down_observed'),'password_status':result.get('local_password_while_idp_down'),'recovered_me':result.get('recovered_real_ui_login'),'error':result.get('error')},ensure_ascii=False),flush=True)
