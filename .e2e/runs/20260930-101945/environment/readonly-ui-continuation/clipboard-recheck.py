#!/usr/bin/env python3
"""仅自有浏览器/localhost 标准读写权限，已知安全 UI Copy 后才回读。"""
from pathlib import Path
import asyncio
import websockets

driver = Path(__file__).with_name('run.py')
prefix = driver.read_text().split("try:\n    browser(['set', 'media', 'light'])")[0]
prefix = prefix.replace("SESSION = 'rcon3'", "SESSION = 'rclip7'").replace("'readonly-ui-profile'", "'readonly-ui-clipboard-profile5'").replace("'readonly-ui-browser.json'", "'readonly-ui-clipboard-browser5.json'").replace("'readonly-ui-secrets.json'", "'readonly-ui-clipboard-secrets5.json'")
exec(compile(prefix, str(driver), 'exec'))
result = json.loads((OUT / 'summary.json').read_text())
own_tokens.clear()
serial = 600
cdp_url = None
context_id = None
permission_set = False

async def cdp_operation(url, method, params):
    async with websockets.connect(url, max_size=4*1024*1024) as ws:
        await ws.send(json.dumps({'id':1,'method':method,'params':params}))
        while True:
            msg = json.loads(await ws.recv())
            if msg.get('id') == 1:
                if 'error' in msg:
                    (PRIVATE/'readonly-ui-cdp-error.json').write_text(json.dumps({'method':method,'error':msg['error']},indent=2))
                    raise RuntimeError('Owned CDP permission step failed')
                return msg.get('result', {})

def ws_url(value):
    if isinstance(value,str) and value.startswith('ws://'):return value
    if isinstance(value,dict):
        for child in value.values():
            found=ws_url(child)
            if found:return found
    if isinstance(value,list):
        for child in value:
            found=ws_url(child)
            if found:return found
    return None

try:
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    h=CFG['hub'];args=['ssh','-F','/dev/null','-T','-N','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ExitOnForwardFailure=yes','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-p',str(h['ssh_port']),'-i',h['ssh_identity_file'],'-L','127.0.0.1:'+str(port)+':127.0.0.1:8080',h['ssh_target']]
    tunnel_stderr=(PRIVATE/'readonly-ui-clipboard-tunnel.stderr').open('wb');tunnel=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=tunnel_stderr);local='http://localhost:'+str(port)
    recheck={'status':'running','owned_tunnel_pid':tunnel.pid,'local_port':port,'port_free_checked':True,'other_clipboard_contents_read':False,'insecure_secure_flags_added':False}
    result['clipboard_permission_recheck']=recheck;save()
    for attempt in range(30):
        assert tunnel.poll() is None
        try:
            with urllib.request.urlopen(local+'/healthz',timeout=2) as q:assert q.status==200
            break
        except (urllib.error.URLError,TimeoutError,ConnectionError):time.sleep(.2)
    token=login(local)
    code,raw=request('/api/executions/'+EXECUTION,token,local);assert code==200;execution=json.loads(raw);expected=execution['stderr'];assert expected and not execution['stdout'] and 'Permission denied' in expected
    browser(['open',local+'/executions/'+EXECUTION]);time.sleep(.4);evaluate(GUARD);browser(['select','select[aria-label="全部流"]','stderr'])
    capability=evaluate('({isSecureContext:window.isSecureContext,origin:location.origin,clipboard_api:!!navigator.clipboard?.readText,visible_log:document.querySelector("main pre")?.textContent||null})');displayed=capability.pop('visible_log');assert displayed==expected
    cdp_url=ws_url(json.loads(browser(['--json','get','cdp-url']).stdout));assert cdp_url and urllib.parse.urlsplit(cdp_url).hostname in ['127.0.0.1','localhost']
    targets=asyncio.run(cdp_operation(cdp_url,'Target.getTargets',{}))['targetInfos'];page=next(t for t in targets if t.get('type')=='page' and t.get('url')==local+'/executions/'+EXECUTION)
    contexts=asyncio.run(cdp_operation(cdp_url,'Target.getBrowserContexts',{})).get('browserContextIds',[])
    reported_context=page.get('browserContextId');context_id=reported_context if reported_context in contexts else None
    recheck['context_resolution']={'override_context_enumerated':context_id is not None,'default_profile_context':context_id is None,'target_default_id_is_not_override_id':bool(reported_context) and reported_context not in contexts}
    # 浏览器端点也枚举启动页；默认 Context 须以本任务 profile 的活动端口证明归属。
    if not context_id:
        active_port=(PROFILE/'DevToolsActivePort').read_text().splitlines();endpoint=urllib.parse.urlsplit(cdp_url)
        assert int(active_port[0])==endpoint.port and active_port[1]==endpoint.path, 'Default context must match exclusive fresh profile DevToolsActivePort'
        recheck['default_context_ownership_verified']='Fresh private profile DevToolsActivePort exactly matches session CDP endpoint'
    version=asyncio.run(cdp_operation(cdp_url,'Browser.getVersion',{})).get('product');params={'origin':local,'permissions':['clipboardReadWrite','clipboardSanitizedWrite']}
    if context_id:params['browserContextId']=context_id
    asyncio.run(cdp_operation(cdp_url,'Browser.grantPermissions',params));permission_set=True
    states=evaluate('(async()=>({read:(await navigator.permissions.query({name:"clipboard-read"})).state,write:(await navigator.permissions.query({name:"clipboard-write"})).state}))()')
    browser(['find','role','button','click','--name','复制日志']);time.sleep(.2)
    feedback=evaluate('({copied:document.body.innerText.includes("已复制"),manual:document.body.innerText.includes("请按 Ctrl+C 复制"),failed:document.body.innerText.includes("复制失败")})');browser(['screenshot',str(OUT/'localhost-clipboard-readback.png')])
    copied,error=clipboard_read();navigator_read=evaluate('(async()=>{try{return {ok:true,value:await navigator.clipboard.readText()}}catch(e){return {ok:false,error_name:e.name}}})()')
    recheck.update({'status':'PASS' if capability['isSecureContext'] and feedback['copied'] and copied==expected and navigator_read.get('value')==expected else 'NOT_VERIFIED' if copied is None else 'FAIL','capability':capability,'normal_permission_override':{'method':'Browser.grantPermissions','permissions':['clipboardReadWrite','clipboardSanitizedWrite'],'origin':local,'exclusive_fresh_profile':True,'scope':'Own BrowserContext' if context_id else 'Exclusive default BrowserContext of own fresh Chromium profile','states':states,'browser':version,'official_contract':'https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json'},'feedback':feedback,'actual_length':len(copied) if copied is not None else None,'expected_length':len(expected),'sha256':hashlib.sha256(copied.encode()).hexdigest() if copied is not None else None,'expected_sha256':hashlib.sha256(expected.encode()).hexdigest(),'native_clipboard_matches':copied==expected,'navigator_clipboard_matches':navigator_read.get('value')==expected,'read_error':error,'api_paths':[{'path':'/api/executions/'+EXECUTION,'http':code}],'business_guard':evaluate('window.__readonlyProof'),'evidence':['environment/readonly-ui-continuation/localhost-clipboard-readback.png','environment/readonly-ui-continuation/summary.json']})
    case=next(c for c in result['cases'] if c['case_id']=='P17P18-10');case['clipboard_subdimension']=recheck['status'];case['normal_permission_recheck']=recheck;case['readback_length']=recheck['actual_length'];case['readback_sha256']=recheck['sha256'];case['matches_exactly']=recheck['native_clipboard_matches'];case['after_read_error']=recheck['read_error'];case['covered'].append('Real native clipboard-read and navigator.clipboard.readText after known safe UI Copy under own origin/context standard read/write permissions');save()
except Exception as e:
    result['clipboard_permission_recheck']['status']='NOT_VERIFIED';result['clipboard_permission_recheck']['error']={'type':type(e).__name__,'message':'Own scoped standard permission recheck failed; private evidence retained'}
finally:
    cleanup={'own_session_logout':[],'source_session_not_logged_out':True,'permission_override_reset':not permission_set}
    if permission_set and cdp_url:
        params={'browserContextId':context_id} if context_id else {}
        asyncio.run(cdp_operation(cdp_url,'Browser.resetPermissions',params));cleanup['permission_override_reset']=True
    for token in own_tokens:
        assert token not in [CFG['token'],CFG['admin_token']];http,_=request('/api/logout',token,method='POST');cleanup['own_session_logout'].append({'http':http,'source_session':False})
    browser(['close'],allow_error=True);cleanup['browser_closed']=True
    if tunnel is not None:
        if tunnel.poll() is None:
            tunnel.terminate()
            try:tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait(timeout=5)
        cleanup['owned_tunnel_stopped']=tunnel.poll() is not None;cleanup['owned_tunnel_pid']=tunnel.pid
        with socket.socket() as s:cleanup['local_forward_port_closed']=s.connect_ex(('127.0.0.1',port))!=0
    if tunnel_stderr:tunnel_stderr.close()
    source_http,_=request('/api/me',CFG['admin_token']);cleanup['source_session_me_http']=source_http
    result['clipboard_permission_recheck']['cleanup']=cleanup;result['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save();print(json.dumps({'clipboard_recheck':result['clipboard_permission_recheck']['status'],'matches':result['clipboard_permission_recheck'].get('native_clipboard_matches'),'navigator_matches':result['clipboard_permission_recheck'].get('navigator_clipboard_matches'),'length':result['clipboard_permission_recheck'].get('actual_length'),'cleanup':cleanup,'error':result['clipboard_permission_recheck'].get('error')}),flush=True)
