import asyncio,hashlib,json,subprocess
from datetime import datetime,timezone,timedelta
from pathlib import Path
import websockets
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web');SESSION='qawebsecure20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080';EID='5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=15)
 if r.returncode:raise RuntimeError('agent-browser error '+str(args[0]))
 return r.stdout
def ev(js):return json.loads(cli('eval',js))
async def setperm(name,setting):
 url=cli('get','cdp-url').strip()
 async with websockets.connect(url,open_timeout=5,close_timeout=2) as ws:
  await ws.send(json.dumps({'id':1,'method':'Browser.setPermission','params':{'permission':{'name':name},'setting':setting,'origin':ORIGIN}}))
  d=json.loads(await asyncio.wait_for(ws.recv(),5));return not bool(d.get('error'))
def main():
 page=ev('({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,nav:performance.getEntriesByType("navigation").length,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),search:document.querySelector("input[placeholder=\\"搜索日志...\\"]")?.value||""})')
 state=ev('Promise.all([navigator.permissions.query({name:"clipboard-read"}).then(p=>p.state).catch(e=>"unavailable"),navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state).catch(e=>"unavailable")]).then(x=>({read:x[0],write:x[1]}))')
 read_grant=asyncio.run(setperm('clipboard-read','granted'));write_grant=asyncio.run(setperm('clipboard-write','granted'))
 # Real user button click writes the currently rendered log text. The subsequent read runs through the page Clipboard API.
 cli('find','role','button','click','--name','复制日志','--exact')
 result=ev('navigator.clipboard.readText().then(actual=>{const p=[...document.querySelectorAll("pre,code")].find(x=>x.innerText.includes("QA_WEB_LIVE_FIRST"));const expected=p?p.innerText:"";return {ok:true,actual,expected}}).catch(e=>({ok:false,errorType:e.name}))')
 matched=bool(result.get('ok') and result.get('actual')==result.get('expected'))
 feedback=ev('({failed:document.body.innerText.includes("复制失败")||document.body.innerText.toLowerCase().includes("copy failed"),success:document.body.innerText.includes("复制成功")||document.body.innerText.includes("已复制")||document.body.innerText.toLowerCase().includes("copied")})')
 metrics={'read_permission_before':state.get('read'),'write_permission_before':state.get('write'),'read_permission_grant_command':read_grant,'write_permission_grant_command':write_grant,'read_result_ok':result.get('ok'),'read_error_type':result.get('errorType'),'expected_bytes':len(result.get('expected','').encode()),'clipboard_bytes':len(result.get('actual','').encode()) if result.get('ok') else None,'expected_sha256':hashlib.sha256(result.get('expected','').encode()).hexdigest(),'clipboard_sha256':hashlib.sha256(result.get('actual','').encode()).hexdigest() if result.get('ok') else None,'exact_match':matched,'ui_feedback':feedback}
 # Restore this session's pre-test permission state. Values and clipboard data are never written to disk.
 rr=asyncio.run(setperm('clipboard-read',state['read'])) if state.get('read') in ('granted','prompt','denied') else False
 rw=asyncio.run(setperm('clipboard-write',state['write'])) if state.get('write') in ('granted','prompt','denied') else False
 record={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'execution_id':EID,'ui':page,'clipboard':metrics,'permission_restored':{'read':rr,'write':rw},'clipboard_plaintext_saved':False,'credentials_saved':False}
 (OUT/'clipboard-actual-read.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'ui':page,'clipboard':metrics,'permission_restored':{'read':rr,'write':rw}},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
