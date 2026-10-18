import json,subprocess,urllib.request,urllib.error
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
SESSION='qaweblogout20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
creds=json.loads((PRIVATE/'repair-credentials.json').read_text());conn=json.loads((PRIVATE/'repair-connection.json').read_text())
def run(*args,stdin=None,timeout=30):
 r=subprocess.run(CLI+list(args),input=stdin,text=True,capture_output=True,timeout=timeout)
 if r.returncode:raise RuntimeError('browser action failed '+str(args[0])+' exit='+str(r.returncode))
 return r.stdout
def events():
 r=subprocess.run(CLI+['--json','network','requests','--filter','/api/logout'],text=True,capture_output=True,timeout=15)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except Exception:return []
 out=[]
 def walk(x):
  if isinstance(x,dict):
   if x.get('url','').endswith('/api/logout'):out.append({k:x.get(k) for k in ('method','status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return out
def main():
 # Session is isolated; its token is captured only in this process memory for one post-logout GET.
 run('open',ORIGIN+'/sign-in');run('wait','--fn','document.querySelector("input[name=\\"username\\"]")!==null')
 js='''(()=>{const set=(name,value)=>{const e=document.querySelector('input[name="'+name+'"]');if(!e)return false;const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(e,value);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return e.value.length===value.length};return {u:set('username',%s),p:set('password',%s)}})()'''%(json.dumps(creds['admin_username']),json.dumps(creds['admin_password']))
 fill=json.loads(run('eval','--stdin',stdin=js))
 if not (fill.get('u') and fill.get('p')):raise RuntimeError('isolated login fields not filled')
 run('network','requests','--clear');run('click','button[type="submit"]');run('wait','--text','仪表盘')
 me=json.loads(run('eval','fetch("/api/me",{headers:{Authorization:"Bearer "+localStorage.getItem("nodesteer_token")}}).then(async r=>({status:r.status,role:r.ok?(await r.json()).Role:null,tokenPresent:localStorage.getItem("nodesteer_token")!==null}))'))
 session_token=json.loads(run('eval','localStorage.getItem("nodesteer_token")'))
 if me.get('status')!=200 or me.get('role')!='administrator' or not session_token:raise RuntimeError('isolated session /api/me failed')
 # Profile menu, real logout menu, and confirmation.
 run('find','role','button','click','--name','个人资料','--exact')
 try:run('find','role','menuitem','click','--name','退出','--exact')
 except Exception:run('find','role','menuitem','click','--name','退出登录','--exact')
 run('wait','--text','退出登录')
 run('network','requests','--clear')
 run('find','role','button','click','--name','退出登录','--exact')
 run('wait','--fn','location.pathname==="/sign-in"')
 after=json.loads(run('eval','({url:location.pathname,tokenPresent:localStorage.getItem("nodesteer_token")!==null})'))
 req=urllib.request.Request(conn['hub_url'].rstrip('/')+'/api/me',headers={'Authorization':'Bearer '+session_token})
 try:
  with urllib.request.urlopen(req,timeout=8) as r: stale_me=r.status
 except urllib.error.HTTPError as e:stale_me=e.code
 rec={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'session':SESSION,'me_before_logout':{'http':me.get('status'),'role':me.get('role')},'logout_events':events(),'ui_after_logout':after,'same_isolated_session_token_me_http_after_logout':stale_me,'token_value_saved':False,'shared_private_connection_bearer_used_or_revoked':False,'request_bodies_saved':False}
 (OUT/'g01-06-results.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'me_before':rec['me_before_logout'],'logout_events':rec['logout_events'],'ui_after':after,'same_session_token_me_after_http':stale_me,'token_saved':False},ensure_ascii=False),flush=True)
 run('close')
if __name__=='__main__':main()
