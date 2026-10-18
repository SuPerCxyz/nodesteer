import asyncio,hashlib,json,subprocess,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
import websockets
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
SESSION='qawebsecure20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080';EID='5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def now():return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=15)
 if r.returncode:raise RuntimeError('browser action failed '+str(args[0]))
 return r.stdout
def ev(js):return json.loads(cli('eval',js))
async def cdp_info():
 import websockets
 url=cli('get','cdp-url').strip()
 async with websockets.connect(url,open_timeout=5,close_timeout=2) as ws:
  await ws.send(json.dumps({'id':1,'method':'Target.getTargets'}));d=json.loads(await asyncio.wait_for(ws.recv(),5))
  targets=d.get('result',{}).get('targetInfos',[])
  t=next(x for x in targets if x.get('type')=='page' and '/executions/'+EID in x.get('url',''))
  return url,t.get('targetId'),t.get('browserContextId')
async def set_permission(url,context_id,name,setting):
 async with websockets.connect(url,open_timeout=5,close_timeout=2) as ws:
  await ws.send(json.dumps({'id':1,'method':'Browser.setPermission','params':{'permission':{'name':name},'setting':setting,'origin':ORIGIN,'browserContextId':context_id}}))
  d=json.loads(await asyncio.wait_for(ws.recv(),5));return not bool(d.get('error'))
def page_permissions():
 return ev('Promise.all([navigator.permissions.query({name:"clipboard-read"}).then(p=>p.state).catch(e=>"unavailable"),navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state).catch(e=>"unavailable")]).then(x=>({read:x[0],write:x[1],secure:window.isSecureContext}))')
def page_clipboard():
 return ev('''(async()=>{try{const t=document.body.innerText;const i=t.lastIndexOf("复制日志");const expected=i>=0?t.slice(i+"复制日志".length).trimEnd():"";const actual=await navigator.clipboard.readText();return {ok:true,expected,actual}}catch(e){return {ok:false,errorType:e.name}}})()''')
def feedback(): return ev('({failed:document.body.innerText.includes("复制失败")||document.body.innerText.toLowerCase().includes("copy failed"),success:document.body.innerText.includes("复制成功")||document.body.innerText.includes("已复制")||document.body.innerText.toLowerCase().includes("copied")})')

def main():
 ui=ev('({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,nav:performance.getEntriesByType("navigation").length,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),search:document.querySelector("input[placeholder=\\"搜索日志...\\"]")?.value||""})')
 if ui['path']!='/executions/'+EID or not ui['online'] or not ui['secure'] or not ui['first'] or not ui['second'] or ui['nav']!=1 or ui['search']!='':raise RuntimeError('page precondition mismatch')
 url,target_id,context_id=asyncio.run(cdp_info())
 if not context_id:raise RuntimeError('execution page browserContextId missing')
 baseline=page_permissions()
 base_read,base_write=baseline['read'],baseline['write']
 grant_read=asyncio.run(set_permission(url,context_id,'clipboard-read','granted'))
 grant_write=asyncio.run(set_permission(url,context_id,'clipboard-write','granted'))
 after_grant=page_permissions()
 cli('find','role','button','click','--name','复制日志','--exact')
 time.sleep(.1)
 success=page_clipboard();success_feedback=feedback()
 expected=success.get('expected','');actual=success.get('actual','')
 success_metrics={'read_ok':success.get('ok'),'read_error_type':success.get('errorType'),'exact_match':bool(success.get('ok') and expected==actual),'expected_bytes':len(expected.encode()),'clipboard_bytes':len(actual.encode()) if success.get('ok') else None,'expected_sha256':hashlib.sha256(expected.encode()).hexdigest(),'clipboard_sha256':hashlib.sha256(actual.encode()).hexdigest() if success.get('ok') else None,'ui_feedback':success_feedback}
 # Deny clipboard write on the actual page's BrowserContext, then exercise the real Copy button.
 deny_command=asyncio.run(set_permission(url,context_id,'clipboard-write','denied'))
 denied_state=page_permissions()
 denied_copy=None;denied_feedback=None;unchanged=None
 if deny_command and denied_state['write']=='denied':
  cli('find','role','button','click','--name','复制日志','--exact');time.sleep(.1)
  denied_copy=page_clipboard();denied_feedback=feedback()
  unchanged=bool(denied_copy.get('ok') and denied_copy.get('actual')==actual)
 # Always restore this page context to its observed pre-test permission states.
 reset_read=asyncio.run(set_permission(url,context_id,'clipboard-read',base_read)) if base_read in ('granted','prompt','denied') else False
 reset_write=asyncio.run(set_permission(url,context_id,'clipboard-write',base_write)) if base_write in ('granted','prompt','denied') else False
 after_restore=page_permissions()
 result={'captured_at':now(),'page':ui,'browser_context_id_recorded':bool(context_id),'target_id_recorded':bool(target_id),'permission_baseline':baseline,'cdp_grants':{'read':grant_read,'write':grant_write,'after':after_grant},'real_copy':success_metrics,'permission_denial':{'cdp_command_accepted':deny_command,'permission_state_after_deny':denied_state,'button_clicked_only_if_denied':denied_copy is not None,'feedback':denied_feedback,'clipboard_unchanged':unchanged},'restoration':{'read_command':reset_read,'write_command':reset_write,'state':after_restore},'plaintext_saved':False,'raw_token_or_cookie_saved':False}
 (OUT/'cdp-context-clipboard-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'page':ui,'permission_baseline':baseline,'context_grant':after_grant,'real_copy':success_metrics,'denied_state':denied_state,'denied_feedback':denied_feedback,'clipboard_unchanged':unchanged,'restored':after_restore},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
