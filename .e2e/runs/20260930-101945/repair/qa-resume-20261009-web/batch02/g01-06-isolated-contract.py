import json, subprocess, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web/batch02')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
conn=json.loads((PRIVATE/'repair-connection.json').read_text())
SESSION='qaweblogout20261009v'; HOST='192.168.100.209'; ORIGIN='http://192.168.100.209:18080'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def run(*args,timeout=20):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=timeout)
 if r.returncode: raise RuntimeError('agent-browser failed at '+str(args[0])+': '+r.stderr[:200])
 return r.stdout
def status(path,token,method='GET'):
 req=urllib.request.Request(conn['hub_url'].rstrip('/')+path,headers={'Authorization':'Bearer '+token},data=b'' if method=='POST' else None,method=method)
 try:
  with urllib.request.urlopen(req,timeout=8) as r:return r.status
 except urllib.error.HTTPError as e:return e.code
def events():
 r=subprocess.run(CLI+['--json','network','requests'],text=True,capture_output=True,timeout=15)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except Exception:return []
 arr=[]
 def walk(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/' in u:
    arr.append({'path':u.split(ORIGIN,1)[-1].split('?',1)[0],'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return arr
def main():
 result={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'canonical_id':'G-01-06','session_isolated':True,'shared_private_bearer_used':False,'stage':'start'}
 try:
  token_raw=json.loads(run('--json','eval','localStorage.getItem("nodesteer_token")'))
  token=token_raw['data']['result']
  if not token: raise RuntimeError('isolated local token absent')
  me_before=status('/api/me',token);result['me_before_http']=me_before
  if me_before!=200: raise RuntimeError('isolated token /me expected 200')
  run('network','requests','--clear')
  run('find','role','button','click','--name','个人资料','--exact')
  snap=run('snapshot','-i')
  result['profile_menu_logout_visible']=('退出' in snap)
  # Use the actual menu option, then inspect confirmation dialog before confirming.
  try:run('find','role','menuitem','click','--name','退出','--exact')
  except Exception:run('find','role','menuitem','click','--name','退出登录','--exact')
  snap=run('snapshot','-i');result['confirmation_visible']=('退出登录' in snap)
  run('find','role','button','click','--name','退出登录','--exact')
  run('wait','--fn','location.pathname==="/sign-in"')
  after=json.loads(run('--json','eval','({path:location.pathname,tokenPresent:!!localStorage.getItem("nodesteer_token")})'))['data']['result']
  result['ui_after_logout']=after
  result['api_events_after_ui_logout']=events()
  result['old_token_me_after_ui_logout_http']=status('/api/me',token)
  result['stage']='ui_logout_observed'
  # G-01-06 canonical API contract: invoke POST logout with only this isolated UI session token.
  result['direct_logout_http']=status('/api/logout',token,'POST')
  result['old_token_me_after_direct_logout_http']=status('/api/me',token)
  result['stage']='complete'
 except Exception as e:
  result['stage']='incomplete';result['error_type']=type(e).__name__;result['error']=str(e)[:260]
 finally:
  result['private_values_saved']=False
  (OUT/'g01-06-isolated-contract-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
  print(json.dumps({k:v for k,v in result.items() if k not in ('error',)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
