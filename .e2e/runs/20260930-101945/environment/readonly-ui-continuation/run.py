#!/usr/bin/env python3
"""仅独立会话的主题、刷新失效 Token 和 localhost Clipboard 真验收。"""
import datetime, hashlib, json, os, pathlib, secrets, shlex, socket, subprocess, time, urllib.error, urllib.parse, urllib.request

os.umask(0o077)
OUT = pathlib.Path(__file__).parent
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
CFG = json.loads((PRIVATE / 'connection.json').read_text())
BASE_URL = CFG['base_url']
EXECUTION = 'd70ecb9a-08e1-474c-9414-da3fce0140fb'
SESSION = 'rcon3'
PROFILE = PRIVATE / 'readonly-ui-profile'
CONFIG = PRIVATE / 'readonly-ui-browser.json'
SECRETS = PRIVATE / 'readonly-ui-secrets.json'
PROFILE.mkdir(mode=0o700, exist_ok=False)
CONFIG.write_text('{}'); CONFIG.chmod(0o600)
(PRIVATE / 'browser-runtime').mkdir(mode=0o700, exist_ok=True)
ENV = dict(os.environ, AGENT_BROWSER_SOCKET_DIR=str(PRIVATE / 'browser-runtime'), AGENT_BROWSER_PROFILE=str(PROFILE), AGENT_BROWSER_CONFIG=str(CONFIG), AGENT_BROWSER_ARGS='--no-sandbox,--disable-setuid-sandbox,--disable-crash-reporter,--disable-breakpad,--crash-dumps-dir=' + str(PRIVATE / 'browser-crash'), XDG_CACHE_HOME=str(PRIVATE / 'browser-cache'), XDG_CONFIG_HOME=str(PRIVATE / 'browser-xdg-config'), TMPDIR=str(PRIVATE))
CLI = ['agent-browser', '--namespace', 'nse2e', '--session', SESSION]
serial = 0
own_tokens = []
tunnel = None
tunnel_stderr = None
secret_state = {'tokens': own_tokens}
result = {'run_id': '20260930-101945', 'status': 'running', 'cases': [], 'business_definition_configuration_writes': 0, 'source_session_logged_out': False, 'source': ['web/src/components/theme-switch.tsx:20', 'web/src/context/theme-provider.tsx:74', 'web/src/components/layout/authenticated-layout.tsx:64', 'web/src/lib/api.ts:48', 'web/src/lib/clipboard.ts:28', 'web/src/features/executions/index.tsx:617']}

def save():
    SECRETS.write_text(json.dumps(secret_state, indent=2)); SECRETS.chmod(0o600)
    (OUT / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))

def browser(args, js=None, allow_error=False):
    global serial
    serial += 1
    p = subprocess.run(CLI + args, env=ENV, input=js, capture_output=True, text=True, timeout=35)
    (PRIVATE / ('readonly-ui-cli-' + str(serial) + '.log')).write_text(p.stdout + p.stderr)
    if p.returncode and not allow_error: raise RuntimeError('Browser step failed; private CLI evidence retained')
    return p

def evaluate(js):
    value = json.loads(browser(['--json', 'eval', '--stdin'], js).stdout)
    if not value.get('success'): raise RuntimeError('Browser evaluation failed; private CLI evidence retained')
    return value['data']['result']

def request(path, token, base=BASE_URL, method='GET'):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers={'Authorization': 'Bearer ' + token}, method=method), timeout=12) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()

GUARD = '''(()=>{const original=window.fetch.bind(window);window.__readonlyProof={requests:[],blocked:[]};window.fetch=async(input,init={})=>{const url=new URL(typeof input==='string'?input:input.url,location.href);const method=(init.method||(typeof input==='object'?input.method:'GET')||'GET').toUpperCase();if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(method)&&!['/api/login','/api/logout'].includes(url.pathname)){window.__readonlyProof.blocked.push({path:url.pathname,method});throw new Error('Readonly verification prevents business mutation')}const r=await original(input,init);if(url.pathname.startsWith('/api/'))window.__readonlyProof.requests.push({path:url.pathname,method,http:r.status});return r};return true})()'''

def login(base):
    browser(['open', base + '/sign-in']); evaluate(GUARD)
    assert not evaluate('!!localStorage.getItem("nodesteer_token")')
    evaluate('(()=>{const values=' + json.dumps({'username': CFG['username'], 'password': CFG['password']}) + ';for(const [name,value]of Object.entries(values)){const field=document.querySelector("input[name="+name+"]");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(field,value);field.dispatchEvent(new Event("input",{bubbles:true}));field.dispatchEvent(new Event("change",{bubbles:true}))}return true})()')
    browser(['click', 'button[type=submit]'])
    for attempt in range(35):
        page = evaluate('({path:location.pathname,has_token:!!localStorage.getItem("nodesteer_token")})')
        if page['path'] == '/' and page['has_token']: break
        time.sleep(.2)
    assert page['path'] == '/' and page['has_token']
    token = evaluate('localStorage.getItem("nodesteer_token")')
    assert token != CFG['token'] and token != CFG['admin_token']
    own_tokens.append(token); save()
    http, raw = request('/api/me', token, base)
    assert http == 200 and json.loads(raw)['Role'] == 'administrator'
    return token

THEME = '''(()=>{const root=document.documentElement;const meta=document.querySelector('meta[name="theme-color"]');const color=meta?.content||null;function rgb(value){const c=document.createElement('canvas');c.width=c.height=1;const x=c.getContext('2d');x.fillStyle=value;x.fillRect(0,0,1,1);return [...x.getImageData(0,0,1,1).data]}const cookie=document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('vite-ui-theme='));const cls=root.classList.contains('dark')?'dark':root.classList.contains('light')?'light':null;return {path:location.pathname,theme_cookie:cookie?decodeURIComponent(cookie.slice(cookie.indexOf('=')+1)):null,resolved_class:cls,media_dark:matchMedia('(prefers-color-scheme: dark)').matches,meta_content:color,meta_rgb:color?rgb(color):null,body_css_color:getComputedStyle(document.body).backgroundColor,body_rgb:rgb(getComputedStyle(document.body).backgroundColor)}})()'''

def theme_observe(case, step, chosen, resolved, screenshot=False):
    time.sleep(.25)
    observed = evaluate(THEME)
    expected = [2,8,23,255] if resolved == 'dark' else [255,255,255,255]
    observed.update({'step': step, 'selected_theme_expected': chosen, 'resolved_theme_expected': resolved, 'expected_meta_rgb': expected, 'cookie_matches': observed['theme_cookie'] == chosen, 'class_matches': observed['resolved_class'] == resolved, 'meta_matches_resolved_theme': observed['meta_rgb'] == expected})
    if screenshot:
        name = 'theme-' + step + '.png'; browser(['screenshot', str(OUT / name)]); observed['screenshot'] = 'environment/readonly-ui-continuation/' + name
    case['observations'].append(observed); save()
    return observed

def clipboard_read():
    p = browser(['--json', 'clipboard', 'read'], allow_error=True)
    if p.returncode: return None, 'native_clipboard_read_failed'
    value = json.loads(p.stdout)
    if not value.get('success'): return None, 'native_clipboard_read_unsuccessful'
    data = value.get('data')
    if isinstance(data, str): return data, None
    if isinstance(data, dict):
        for k in ['text','content','result','value']:
            if isinstance(data.get(k), str): return data[k], None
    return None, 'native_clipboard_response_shape_unrecognized'

try:
    browser(['set', 'media', 'light'])
    login(BASE_URL)
    browser(['open', BASE_URL + '/settings']); evaluate(GUARD)
    theme = {'case_id':'G02-02','status':'running','covered':[],'remaining':[],'observations':[],'api_paths':[{'path':'/api/me','http':200},{'path':'/api/settings','http':200}],'contract':'Meta uses the configured light/dark palette according to resolved theme; actual CSS/body RGB and cookie are also recorded.'}
    result['cases'].append(theme); save()
    for chosen, label in [('light','浅色'),('dark','深色'),('system','系统')]:
        browser(['find','role','button','click','--name','切换主题']); browser(['find','role','menuitem','click','--name',label,'--exact'])
        theme_observe(theme,chosen+'-selected',chosen,'dark' if chosen=='dark' else 'light',True)
        browser(['reload']); theme_observe(theme,chosen+'-reloaded',chosen,'dark' if chosen=='dark' else 'light',True)
        browser(['set','media','dark']); theme_observe(theme,chosen+'-media-dark',chosen,'light' if chosen=='light' else 'dark',True)
        if chosen=='system':
            browser(['reload']); theme_observe(theme,'system-dark-reloaded',chosen,'dark',True)
        browser(['set','media','light']); theme_observe(theme,chosen+'-media-light',chosen,'dark' if chosen=='dark' else 'light')
    theme['covered']=['Real light/dark/system menu selection','Reload persistence for all three modes','Actual light/dark media preference switching','Meta content decoded to RGBA and compared with resolved-theme palette','Actual body CSS color/RGBA and root class captured']
    theme['status']='PASS' if all(v['cookie_matches'] and v['class_matches'] and v['meta_matches_resolved_theme'] for v in theme['observations']) else 'FAIL'
    if theme['status']=='FAIL':theme['failure_id']='FAIL-ENV-UI-THEME-001'
    save(); print(json.dumps({'case_id':'G02-02','status':theme['status'],'observation_count':len(theme['observations'])}),flush=True)
    auth = {'case_id':'G-01-05','status':'running','covered':[],'remaining':[],'precondition':'Fresh independent UI login /api/me200; protected settings page loaded. Test is invalid localStorage token followed by real reload, distinct from loaded-page expiry behavior.'}
    result['cases'].append(auth); save()
    browser(['network','requests','--clear'])
    secret_state['invalid_local_token']='qa-readonly-invalid-'+secrets.token_urlsafe(24);save()
    evaluate('(()=>{localStorage.setItem("nodesteer_token",'+json.dumps(secret_state['invalid_local_token'])+');return {invalid_token_stored:true}})()')
    browser(['reload'])
    for attempt in range(35):
        state=evaluate('({path:location.pathname,has_token:!!localStorage.getItem("nodesteer_token"),login_form_present:!!document.querySelector("input[name=username]")})')
        if state['path']=='/sign-in' and not state['has_token'] and state['login_form_present']:break
        time.sleep(.2)
    timing=evaluate('performance.getEntriesByType("resource").filter(e=>new URL(e.name).pathname.startsWith("/api/")).map(e=>({path:new URL(e.name).pathname,http:e.responseStatus||null}))')
    network=json.loads(browser(['--json','network','requests','--filter','/api/me','--status','401']).stdout)
    data=network.get('data');rows=data.get('requests',[]) if isinstance(data,dict) else data if isinstance(data,list) else []
    safe=[]
    for item in rows:
        url=item.get('url') or item.get('request',{}).get('url') or ''
        status=item.get('status') or item.get('statusCode') or item.get('response',{}).get('status')
        safe.append({'path':urllib.parse.urlsplit(url).path,'method':item.get('method') or item.get('request',{}).get('method'),'http':status})
    browser(['screenshot',str(OUT/'invalid-token-sign-in.png')])
    auth.update({'final_ui':state,'browser_resource_timing':timing,'browser_native_network':safe,'covered':['Invalid localStorage credential then browser reload','Actual protected /api/me401','Local token removed','Actual /sign-in navigation and local login form'], 'evidence':['environment/readonly-ui-continuation/invalid-token-sign-in.png','environment/readonly-ui-continuation/summary.json']})
    http401=any(x['path']=='/api/me' and x['http']==401 for x in timing+safe)
    auth['status']='PASS' if http401 and state['path']=='/sign-in' and not state['has_token'] and state['login_form_present'] else 'FAIL';save();print(json.dumps({'case_id':'G-01-05','status':auth['status'],'actual_401':http401,'token_cleared':not state['has_token'],'path':state['path']}),flush=True)
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    hub=CFG['hub'];args=['ssh','-F','/dev/null','-T','-N','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=2','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-p',str(hub['ssh_port']),'-i',hub['ssh_identity_file'],'-L','127.0.0.1:'+str(port)+':127.0.0.1:8080',hub['ssh_target']]
    tunnel_stderr=(PRIVATE/'readonly-ui-tunnel.stderr').open('wb');tunnel=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=tunnel_stderr);local='http://localhost:'+str(port)
    result['tunnel']={'pid':tunnel.pid,'local_address':'127.0.0.1','local_port':port,'target':'authorized Hub loopback8080','owned_by_this_task':True,'port_free_checked_before_launch':True,'remote_configuration_modified':False};save()
    for attempt in range(35):
        assert tunnel.poll() is None,'Owned SSH tunnel exited'
        try:
            with urllib.request.urlopen(local+'/healthz',timeout=2) as response:assert response.status==200
            break
        except (urllib.error.URLError,ConnectionError,TimeoutError):time.sleep(.2)
    token=login(local)
    code,raw=request('/api/executions/'+EXECUTION,token,local);assert code==200;execution=json.loads(raw);code_logs,raw_logs=request('/api/executions/'+EXECUTION+'/logs',token,local);assert code_logs==200;assert execution['stdout']=='' and execution['stderr'] and 'Permission denied' in execution['stderr'];expected=execution['stderr'];secret_state['expected_safe_clipboard_text']=expected;save()
    browser(['open',local+'/executions/'+EXECUTION]);time.sleep(.4);evaluate(GUARD);browser(['select','select[aria-label="全部流"]','stderr'])
    capability=evaluate('({isSecureContext:window.isSecureContext,clipboard_write_api:!!navigator.clipboard?.writeText,clipboard_read_api:!!navigator.clipboard?.readText,origin:location.origin,visible_log:document.querySelector("main pre")?.textContent||null})')
    displayed=capability.pop('visible_log');assert displayed==expected
    before,before_error=clipboard_read()
    browser(['find','role','button','click','--name','复制日志']);time.sleep(.2)
    feedback=evaluate('({copied:document.body.innerText.includes("已复制"),manual:document.body.innerText.includes("请按 Ctrl+C 复制"),failed:document.body.innerText.includes("复制失败")})')
    browser(['screenshot',str(OUT/'localhost-real-copy.png')])
    copied,read_error=clipboard_read()
    clip={'case_id':'P17P18-10','status':'PARTIAL','clipboard_subdimension':'PASS' if capability['isSecureContext'] and feedback['copied'] and copied==expected else 'NOT_VERIFIED' if copied is None else 'FAIL','covered':['Same real primary Hub and execution log UI via task-owned SSH-L localhost trustworthy origin','Real stderr stream selection and Copy Logs click','Actual Copied feedback and native browser clipboard-read equality/SHA'],'remaining':['Overall Wrap/Follow case is not fully executed here','HTTPS origin itself not exercised; this is real secure-context localhost proof','Other Copy surfaces including enrollment commands and clipboard-denial/failure not generalized'],'execution_id':EXECUTION,'capability':capability,'feedback':feedback,'expected_length':len(expected),'expected_sha256':hashlib.sha256(expected.encode()).hexdigest(),'readback_length':len(copied) if copied is not None else None,'readback_sha256':hashlib.sha256(copied.encode()).hexdigest() if copied is not None else None,'matches_exactly':copied==expected,'before_read_error':before_error,'after_read_error':read_error,'before_length':len(before) if before is not None else None,'api_paths':[{'path':'/api/executions/'+EXECUTION,'http':code},{'path':'/api/executions/'+EXECUTION+'/logs','http':code_logs}],'business_mutations':evaluate('window.__readonlyProof'),'evidence':['environment/readonly-ui-continuation/localhost-real-copy.png','environment/readonly-ui-continuation/summary.json']}
    result['cases'].append(clip);save();print(json.dumps({'case_id':'P17P18-10','clipboard_dimension':clip['clipboard_subdimension'],'secure_context':capability['isSecureContext'],'copied_feedback':feedback['copied'],'matches':clip['matches_exactly'],'read_error':read_error}),flush=True)
    result['status']='completed_with_theme_failure' if theme['status']=='FAIL' else 'completed'
except Exception as e:
    result['status']='interrupted';result['error']={'type':type(e).__name__,'message':'Readonly continuation step failed; private logs retained'}
finally:
    cleanup={'own_session_logout':[],'source_session_not_logged_out':True,'browser_closed':False,'owned_tunnel_stopped':tunnel is None}
    for token in own_tokens:
        assert token not in [CFG['token'],CFG['admin_token']]
        http,_=request('/api/logout',token,method='POST');cleanup['own_session_logout'].append({'http':http,'source_session':False})
    browser(['close'],allow_error=True);cleanup['browser_closed']=True
    if tunnel is not None:
        cleanup['owned_tunnel_pid']=tunnel.pid
        if tunnel.poll() is None:
            tunnel.terminate()
            try:tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:tunnel.kill();tunnel.wait(timeout=5)
        cleanup['owned_tunnel_stopped']=tunnel.poll() is not None
        with socket.socket() as s:cleanup['local_forward_port_closed']=s.connect_ex(('127.0.0.1',result['tunnel']['local_port']))!=0
    if tunnel_stderr:tunnel_stderr.close()
    source_http,_=request('/api/me',CFG['admin_token']);cleanup['source_session_me_http']=source_http
    result['cleanup']=cleanup;result['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save();print(json.dumps({'continuation':result['status'],'case_statuses':{x['case_id']:x['status'] for x in result['cases']},'cleanup':cleanup,'error':result.get('error')}),flush=True)
