import asyncio
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
PRIVATE=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
SESSION='qawebsecure20261009'
HOST='192.168.100.209'
ORIGIN='http://192.168.100.209:18080'
EID='5c5d8c3e-945f-4ca2-ab91-fd30cb8b5b9b'
TASK='4006d075-8c15-4d14-9cbc-8b63f5823131'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
conn=json.loads((PRIVATE/'repair-connection.json').read_text())
HUB=conn['hub_url'].rstrip('/')
HEADERS={'Authorization':'Bearer '+conn['token']}


def now(): return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')
def save(name,data): (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def mark(phase,**fields):
    p=json.loads((OUT/'progress.json').read_text())
    p.update({'phase':phase,'updated_at':now(),'current_gate':phase})
    p.setdefault('runner',{}).update(fields)
    (OUT/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
    print(phase,flush=True)
def cli(*args,timeout=15):
    r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=timeout)
    if r.returncode: raise RuntimeError('agent-browser command failed: '+str(args[0])+' exit='+str(r.returncode))
    return r.stdout
def ev(js): return json.loads(cli('eval',js))
def api(path):
    with urlopen(Request(HUB+path,headers=HEADERS),timeout=8) as r: return r.status,json.loads(r.read())
def summary():
    return ev('({path:location.pathname,online:navigator.onLine,secure:window.isSecureContext,first:document.body.innerText.includes("QA_WEB_LIVE_FIRST"),second:document.body.innerText.includes("QA_WEB_LIVE_SECOND"),running:document.body.innerText.includes("运行中"),failed:document.body.innerText.includes("失败"),navCount:performance.getEntriesByType("navigation").length})')
def logs_summary():
    hd,e=api('/api/executions/'+EID)
    hl,logs=api('/api/executions/'+EID+'/logs')
    return {'http':hd,'status':e.get('status'),'exit_code':e.get('exit_code'),'task_revision':e.get('task_revision'),'offline':e.get('offline'),'synced':e.get('synced'),'stdout_length':len(e.get('stdout') or ''),'stderr_length':len(e.get('stderr') or ''),'logs_http':hl,'log_count':len(logs),'seq':[x.get('seq') for x in logs],'streams':[x.get('stream') for x in logs],'attempt_metadata':any(('attempt' in x or 'attempt_id' in x) for x in logs) or 'attempts' in e,'first':any('QA_WEB_LIVE_FIRST' in (x.get('chunk') or '') for x in logs),'second':any('QA_WEB_LIVE_SECOND' in (x.get('chunk') or '') for x in logs)}
def log_text(): return ev('(()=>{const e=[...document.querySelectorAll("pre,code")].find(x=>x.innerText.includes("QA_WEB_LIVE_FIRST"));return e?e.innerText:""})()')
def clip_text():
    d=json.loads(cli('--json','clipboard','read'))
    def find(x):
        if isinstance(x,str): return x
        if isinstance(x,dict):
            for k in ('text','clipboard','content','data'):
                if isinstance(x.get(k),str): return x[k]
            for v in x.values():
                z=find(v)
                if z is not None:return z
        if isinstance(x,list):
            for v in x:
                z=find(v)
                if z is not None:return z
        return None
    return find(d) or ''
def clipboard_feedback(): return ev('({failed:document.body.innerText.includes("复制失败")||document.body.innerText.toLowerCase().includes("copy failed"),success:document.body.innerText.includes("复制成功")||document.body.innerText.includes("已复制")||document.body.innerText.toLowerCase().includes("copied")})')
def click_copy(label):
    expected=log_text() or ''
    cli('find','role','button','click','--name','复制日志','--exact')
    time.sleep(.15)
    actual=clip_text()
    feedback=clipboard_feedback()
    return {'label':label,'expected_length':len(expected.encode()),'clipboard_length':len(actual.encode()),'expected_sha256':hashlib.sha256(expected.encode()).hexdigest(),'clipboard_sha256':hashlib.sha256(actual.encode()).hexdigest(),'exact_match':actual==expected,'ui_feedback':feedback}
async def set_permission(setting):
    import websockets
    cdp=cli('get','cdp-url').strip()
    async with websockets.connect(cdp,open_timeout=5,close_timeout=2) as ws:
        await ws.send(json.dumps({'id':1,'method':'Browser.setPermission','params':{'permission':{'name':'clipboard-write'},'setting':setting,'origin':ORIGIN}}))
        d=json.loads(await asyncio.wait_for(ws.recv(),5))
        return not bool(d.get('error'))


def main():
    offline_ui=summary(); api_terminal=logs_summary()
    if offline_ui['online'] or not offline_ui['running'] or offline_ui['second'] or api_terminal['status']!='FAILED': raise RuntimeError('precondition mismatch; no action performed')
    save('offline-recovery-start.json',{'captured_at':now(),'ui':offline_ui,'api':api_terminal,'claim':'browser was offline while API execution was already terminal; UI retained the last RUNNING/first-chunk view'})
    cli('scroll','down','600')
    cli('screenshot','main',str((OUT/'offline-stale-running.png').resolve()))
    mark('OFFLINE_UI_STALE_RUNNING_API_FAILED',browser_online=False,api_status=api_terminal['status'],ui_running=offline_ui['running'],first_visible=offline_ui['first'],second_visible=offline_ui['second'])

    # Real UI controls while the page is disconnected. Values are the rendered select options.
    cli('select','select','stdout'); stdout=summary()
    cli('select','select','stderr'); stderr=summary()
    cli('select','select','all')
    cli('fill','input[placeholder="搜索日志..."]','QA_WEB_LIVE_FIRST'); search=summary()
    cli('fill','input[placeholder="搜索日志..."]','')
    before_follow=ev('(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="跟随");return {found:!!b,pressed:b?b.getAttribute("aria-pressed"):null,className:b?b.className:null}})()')
    cli('find','role','button','click','--name','跟随','--exact')
    follow_off=ev('(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="跟随");return {pressed:b?b.getAttribute("aria-pressed"):null,className:b?b.className:null}})()')
    cli('find','role','button','click','--name','跟随','--exact')
    follow_on=ev('(()=>{const b=[...document.querySelectorAll("button")].find(x=>x.innerText.trim()==="跟随");return {pressed:b?b.getAttribute("aria-pressed"):null,className:b?b.className:null}})()')
    offline_copy=click_copy('offline_stale_view')
    offline_controls={'stdout_first':stdout['first'],'stderr_first':stderr['first'],'search_first':search['first'],'follow_before':before_follow,'follow_after_click1':follow_off,'follow_after_click2':follow_on,'copy':offline_copy,'nav_count':summary()['navCount']}
    save('offline-ui-controls.json',{'captured_at':now(),'controls':offline_controls,'browser_offline':True})
    mark('OFFLINE_STREAM_SEARCH_FOLLOW_COPY_CAPTURED',browser_online=False,offline_copy_exact=offline_copy['exact_match'])

    # Restore only this browser session's network and let the same detail page re-request.
    cli('set','offline','off')
    deadline=time.time()+20
    recovered=summary()
    while time.time()<deadline:
        recovered=summary()
        if recovered['online'] and recovered['failed'] and recovered['second']:
            break
        time.sleep(.5)
    api_recovered=logs_summary()
    cli('scroll','down','600')
    cli('screenshot','main',str((OUT/'reconnected-terminal.png').resolve()))
    mark('BROWSER_RECONNECTED_TERMINAL_UI_OBSERVED',browser_online=recovered['online'],ui_failed=recovered['failed'],ui_second_visible=recovered['second'],api_status=api_recovered['status'],nav_count=recovered['navCount'])

    # User Copy action writes the rendered logs to the actual system clipboard; read back in memory.
    terminal_copy=click_copy('reconnected_terminal')
    denied_supported=False; permission_state='unsupported'; denied_copy=None; permission_reset=False
    try:
        denied_supported=asyncio.run(set_permission('denied'))
        if denied_supported:
            q=ev('navigator.permissions.query({name:"clipboard-write"}).then(p=>p.state)')
            permission_state=q if isinstance(q,str) else str(q)
            denied_copy=click_copy('clipboard_permission_denied')
            permission_reset=asyncio.run(set_permission('granted'))
    except Exception as ex:
        permission_state='tool_error:'+type(ex).__name__
    final={
      'captured_at':now(),'execution_id':EID,'task_id':TASK,
      'offline_ui':offline_ui,'offline_api_terminal':api_terminal,'offline_controls':offline_controls,
      'reconnected_ui':recovered,'reconnected_api':api_recovered,'terminal_copy':terminal_copy,
      'clipboard_permission_denied_supported':denied_supported,'clipboard_permission_state':permission_state,
      'denied_copy':denied_copy,'permission_reset':permission_reset,
      'same_page_no_reload':recovered['navCount']==1,'attempt_metadata_exposed':api_recovered['attempt_metadata'],
      'evidence':['offline-stale-running.png','reconnected-terminal.png','offline-recovery-start.json','offline-ui-controls.json'],
      'clipboard_plaintext_saved':False,'raw_logs_saved':False,'credentials_saved':False
    }
    save('offline-recovery-results.json',final)
    progress=json.loads((OUT/'progress.json').read_text())
    progress.update({'phase':'OFFLINE_RECOVERY_AND_COPY_COMPLETE','updated_at':now(),'current_gate':'offline期间UI保留RUNNING/first chunk，API已FAILED；恢复online后同一页获取FAILED与terminal logs；Copy/权限证据见offline-recovery-results.json','offline_recovery':{'ui_was_stale_while_offline':offline_ui['running'] and not offline_ui['second'],'api_failed_while_offline':api_terminal['status']=='FAILED','ui_recovered_same_page':recovered['failed'] and recovered['second'] and recovered['navCount']==1,'copy_exact':terminal_copy['exact_match'],'permission_denied_supported':denied_supported}})
    (OUT/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'offline_ui':offline_controls,'api_while_offline':{'status':api_terminal['status'],'logs':api_terminal['log_count']},'reconnected_ui':recovered,'api_after_reconnect':{'status':api_recovered['status'],'logs':api_recovered['log_count']},'terminal_copy_exact':terminal_copy['exact_match'],'copy_feedback':terminal_copy['ui_feedback'],'permission_denied_supported':denied_supported,'permission_state':permission_state,'denied_copy_feedback':denied_copy['ui_feedback'] if denied_copy else None,'same_page_no_reload':recovered['navCount']==1},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
