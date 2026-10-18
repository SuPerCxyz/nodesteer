import asyncio, hashlib, json, subprocess, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
SESSION='qawebsecure20261009'; HOST='192.168.100.209'; ORIGIN='http://192.168.100.209:18080'; EID='5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def now(): return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=15)
 if r.returncode: raise RuntimeError('browser action failed: '+str(args[0]))
 return r.stdout
def ev(js): return json.loads(cli('eval',js))
async def permission(name,setting):
 import websockets
 cdp=cli('get','cdp-url').strip()
 async with websockets.connect(cdp,open_timeout=5,close_timeout=2) as ws:
  await ws.send(json.dumps({'id':1,'method':'Browser.setPermission','params':{'permission':{'name':name},'setting':setting,'origin':ORIGIN}}))
  d=json.loads(await asyncio.wait_for(ws.recv(),5))
  return not bool(d.get('error'))
def save(name,data): (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def mark(stage,**fields):
 p=json.loads((OUT/'progress.json').read_text());p.update({'phase':stage,'updated_at':now(),'current_gate':stage});p.setdefault('runner',{}).update(fields);(OUT/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(stage,flush=True)
def read_clipboard_via_page():
 js='''(async()=>{try{const p=[...document.querySelectorAll("pre,code")].find(x=>x.innerText.includes("QA_WEB_LIVE_FIRST"));const expected=p?p.innerText:"";const actual=await navigator.clipboard.readText();return {read:true,secure:window.isSecureContext,expected,actual}}catch(e){return {read:false,secure:window.isSecureContext,errorType:e&&e.name||"unknown"}}})()'''
 return ev(js)
def toast(): return ev('({failed:document.body.innerText.includes("复制失败")||document.body.innerText.toLowerCase().includes("copy failed"),success:document.body.innerText.includes("复制成功")||document.body.innerText.includes("已复制")||document.body.innerText.toLowerCase().includes("copied")})')
def main():
 before=ev('({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),failed:document.body.innerText.includes("失败"),running:document.body.innerText.includes("运行中"),navCount:performance.getEntriesByType("navigation").length,search:document.querySelector("input[placeholder=\\"搜索日志...\\"]")?.value||""})')
 if before['path']!='/executions/'+EID or not before['online'] or not before['failed'] or not before['first'] or not before['second'] or before['navCount']!=1: raise RuntimeError('post-recovery page precondition mismatch')
 # Re-assert All/stdout only with the real rendered select; retain empty search through user keyboard path.
 cli('select','select','all'); all_state=ev('({first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND")})')
 cli('select','select','stdout'); stdout_state=ev('({first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND")})')
 cli('select','select','all')
 # Save baseline clipboard permission state without exposing clipboard contents.
 base_perm=ev('Promise.all([navigator.permissions.query({name:"clipboard-read"}).then(p=>p.state).catch(e=>"unavailable"),navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state).catch(e=>"unavailable")]).then(x=>({read:x[0],write:x[1]}))')
 read_granted=asyncio.run(permission('clipboard-read','granted'))
 write_granted=asyncio.run(permission('clipboard-write','granted'))
 cli('find','role','button','click','--name','复制日志','--exact')
 time.sleep(.15)
 success_read=read_clipboard_via_page()
 success_match=bool(success_read.get('read') and success_read.get('expected')==success_read.get('actual'))
 success_lengths={'expected_bytes':len(success_read.get('expected','').encode()),'clipboard_bytes':len(success_read.get('actual','').encode()) if success_read.get('read') else None,'expected_sha256':hashlib.sha256(success_read.get('expected','').encode()).hexdigest(),'clipboard_sha256':hashlib.sha256(success_read.get('actual','').encode()).hexdigest() if success_read.get('read') else None,'exact_match':success_match,'read_error_type':success_read.get('errorType')}
 success_feedback=toast()
 mark('RECONNECTED_REAL_CLIPBOARD_WRITE_READ',secure=success_read.get('secure'),read_permission_granted=read_granted,write_permission_granted=write_granted,exact_match=success_match,feedback=success_feedback)

 denied_set=asyncio.run(permission('clipboard-write','denied'))
 denied_state=ev('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state).catch(e=>"unavailable")')
 cli('find','role','button','click','--name','复制日志','--exact')
 time.sleep(.15)
 denied_feedback=toast()
 denied_read=read_clipboard_via_page()
 denied_unchanged=bool(denied_read.get('read') and denied_read.get('expected')==denied_read.get('actual'))
 # Restore the session to the permission settings observed before this test.
 reset_read=asyncio.run(permission('clipboard-read',base_perm.get('read','prompt'))) if base_perm.get('read') in ('granted','prompt','denied') else False
 reset_write=asyncio.run(permission('clipboard-write',base_perm.get('write','prompt'))) if base_perm.get('write') in ('granted','prompt','denied') else False
 denial={'cdp_denied_set':denied_set,'permission_state':denied_state,'feedback':denied_feedback,'clipboard_unchanged':denied_unchanged,'read_permission_restored':reset_read,'write_permission_restored':reset_write}
 final={'captured_at':now(),'execution_id':EID,'url':before['path'],'secure_context':before['secure'],'initial_search_empty':before['search']=='','same_page_no_reload':before['navCount']==1,'all_stream_markers':all_state,'stdout_stream_markers':stdout_state,'clipboard_success_read_write':{'read_permission_granted':read_granted,'write_permission_granted':write_granted,'feedback':success_feedback,**success_lengths},'clipboard_denial':denial,'plaintext_or_credentials_saved':False}
 save('clipboard-results.json',final)
 cli('scroll','down','600');cli('screenshot','main',str((OUT/'clipboard-terminal.png').resolve()))
 p=json.loads((OUT/'progress.json').read_text());p.update({'phase':'OFFLINE_RECOVERY_AND_CLIPBOARD_COMPLETE','updated_at':now(),'current_gate':'同一5c5d详情页恢复online后status/logs复核及真实clipboard写入/读回、CDP权限拒绝反馈已执行，见clipboard-results.json与offline-recovery-results.json'});p['clipboard_results']={'exact_match':success_match,'permission_denial_supported':denied_set,'permission_state':denied_state,'denial_feedback':denied_feedback['failed'],'same_page_no_reload':before['navCount']==1};(OUT/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'ui_before':before,'all_stream':all_state,'stdout_stream':stdout_state,'clipboard_success':success_lengths|{'feedback':success_feedback},'permission_denial':denial},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
