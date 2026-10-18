import json,subprocess,urllib.request,urllib.error
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO');cred=json.loads((PRIVATE/'repair-credentials.json').read_text());conn=json.loads((PRIVATE/'repair-connection.json').read_text())
SESSION='qaweblogout20261009v';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080';CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def run(*a,stdin=None,timeout=30):
 r=subprocess.run(CLI+list(a),input=stdin,text=True,capture_output=True,timeout=timeout)
 if r.returncode:raise RuntimeError('browser action failed '+str(a[0]))
 return r.stdout
def api_me(token):
 r=urllib.request.Request(conn['hub_url'].rstrip('/')+'/api/me',headers={'Authorization':'Bearer '+token})
 try:
  with urllib.request.urlopen(r,timeout=8) as q:return q.status
 except urllib.error.HTTPError as e:return e.code
def api_logout(token):
 r=urllib.request.Request(conn['hub_url'].rstrip('/')+'/api/logout',data=b'',headers={'Authorization':'Bearer '+token},method='POST')
 try:
  with urllib.request.urlopen(r,timeout=8) as q:return q.status
 except urllib.error.HTTPError as e:return e.code
def network_api():
 r=subprocess.run(CLI+['--json','network','requests'],text=True,capture_output=True,timeout=15)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except Exception:return []
 out=[]
 def walk(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/' in u:
    path=u.split(ORIGIN,1)[-1].split('?',1)[0]
    out.append({'path':path,'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return out
def main():
 run('open',ORIGIN+'/sign-in');run('wait','--fn','document.querySelector("input[name=\\"username\\"]")!==null')
 js='''(()=>{const set=(name,value)=>{const e=document.querySelector('input[name="'+name+'"]');if(!e)return false;const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return e.value.length===value.length};return {u:set('username',%s),p:set('password',%s)}})()'''%(json.dumps(cred['admin_username']),json.dumps(cred['admin_password']))
 fill=json.loads(run('eval','--stdin',stdin=js))
 if not (fill.get('u') and fill.get('p')):raise RuntimeError('login fill failed')
 run('network','requests','--clear');run('click','button[type="submit"]');run('wait','--text','仪表盘')
 token=json.loads(run('eval','localStorage.getItem("nodesteer_token")'))
 if not isinstance(token,str) or not token:raise RuntimeError('login token absent')
 before=api_me(token)
 if before!=200:raise RuntimeError('isolated session /me precondition failed')
 run('find','role','button','click','--name','个人资料','--exact')
 try:run('find','role','menuitem','click','--name','退出','--exact')
 except Exception:run('find','role','menuitem','click','--name','退出登录','--exact')
 run('wait','--text','退出登录');run('network','requests','--clear');run('find','role','button','click','--name','退出登录','--exact');run('wait','--fn','location.pathname==="/sign-in"')
 after=json.loads(run('eval','({url:location.pathname,tokenPresent:localStorage.getItem("nodesteer_token")!==null})'))
 ui_events=network_api(); old_token_after_ui=api_me(token)
 direct_logout=None; old_token_after_api=None
 if old_token_after_ui==200:
  direct_logout=api_logout(token);old_token_after_api=api_me(token)
 rec={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'isolated_session':SESSION,'me_before':{'http':before},'ui_logout_api_events':ui_events,'ui_after_logout':after,'old_token_me_after_ui_logout_http':old_token_after_ui,'direct_logout_with_only_isolated_token_http':direct_logout,'old_token_me_after_direct_logout_http':old_token_after_api,'shared_private_bearer_revoked':False,'user_password_token_or_body_saved':False}
 (OUT/'g01-06-logout-session-verify.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'me_before':rec['me_before'],'ui_logout_api_events':ui_events,'ui_after':after,'old_token_after_ui_logout_http':old_token_after_ui,'direct_logout_http':direct_logout,'old_token_after_direct_logout_http':old_token_after_api,'token_saved':False},ensure_ascii=False),flush=True)
 run('close')
if __name__=='__main__':main()
