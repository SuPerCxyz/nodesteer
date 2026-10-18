#!/usr/bin/env python3
"""新登录的 operator/viewer 前端只读权限验收。"""
import datetime, json, os, pathlib, re, subprocess, time

os.umask(0o077)
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
OUT = pathlib.Path(__file__).parent
EVIDENCE = OUT / 'role-ui-evidence'
EVIDENCE.mkdir(exist_ok=True)
accounts = json.loads((PRIVATE / 'main-accounts.json').read_text())
URL = 'http://192.168.100.209:8080'
result = {'run_id': '20260930-101945', 'status': 'running', 'roles': {}, 'business_or_definition_write_requests': 0, 'source': ['web/src/lib/permissions.ts:3', 'web/src/features/catalog/index.tsx:1937', 'web/src/features/catalog/index.tsx:2113', 'web/src/features/tasks/index.tsx:1483', 'web/src/features/catalog/node-bulk-actions.tsx:113', 'web/src/features/editors/index.tsx:1568', 'internal/hub/auth/auth.go:230', 'internal/hub/api/api.go:221'], 'related_backend_evidence': 'main/api-results.json', 'boundary': 'New real UI login is allowed. All business operations and definition/configuration writes prohibited; actual dialog opening/cancel and authenticated GET only.'}
serial = 0
BASE = []
ENV = {}

def save():
    (OUT / 'role-ui-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))

def browser(args, script=None, allow_error=False):
    global serial
    serial += 1
    p = subprocess.run(BASE + args, env=ENV, input=script, capture_output=True, text=True, timeout=35)
    (PRIVATE / ('role-ui-browser-' + str(serial) + '.log')).write_text(p.stdout + p.stderr)
    if p.returncode and not allow_error: raise RuntimeError('Browser operation failed; private CLI log retained')
    return p

def evaluate(script):
    r = browser(['--json', 'eval', '--stdin'], script)
    x = json.loads(r.stdout)
    if not x.get('success'): raise RuntimeError('Browser evaluation failed; private CLI log retained')
    return x['data']['result']

GUARD = '''(()=>{window.__roleReadOnly={requests:[],blockedWrites:[]};const original=window.fetch.bind(window);window.fetch=async(input,init={})=>{const url=new URL(typeof input==='string'?input:input.url,location.href);const method=(init.method||(typeof input==='object'?input.method:'GET')||'GET').toUpperCase();if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(method)){window.__roleReadOnly.blockedWrites.push({method,path:url.pathname});throw new Error('Read-only role test prevents business mutation')}const response=await original(input,init);window.__roleReadOnly.requests.push({method,path:url.pathname,status:response.status});return response};return true})()'''
PAGE = '''(()=>{const visible=e=>!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);const root=document.querySelector('main')||document.body;const text=e=>(e.innerText||e.getAttribute('aria-label')||e.getAttribute('title')||'').trim();return {path:location.pathname,title:document.title,headings:[...document.querySelectorAll('h1,h2,h3')].filter(visible).map(text),buttons:[...root.querySelectorAll('button,[role=button],a')].filter(visible).map(e=>({text:text(e),aria:e.getAttribute('aria-label'),disabled:e.disabled===true||e.getAttribute('aria-disabled')==='true',href:e.tagName==='A'?new URL(e.href).pathname:null})),inputs:[...root.querySelectorAll('input,textarea,[role=checkbox]')].filter(visible).map(e=>({label:e.getAttribute('aria-label')||e.name||e.getAttribute('role')||e.type,disabled:e.disabled===true||e.getAttribute('aria-disabled')==='true'})),table_rows:root.querySelectorAll('tbody tr').length,feedback:[...document.querySelectorAll('[role=alert]')].filter(visible).map(text),users_admin_only:document.body.innerText.includes('只有管理员可以管理用户')||document.body.innerText.includes('Only administrators'),navigation:[...document.querySelectorAll('aside a,[data-slot=sidebar] a')].filter(visible).map(e=>({text:text(e),path:new URL(e.href).pathname})),safe_api_resources:performance.getEntriesByType('resource').filter(e=>new URL(e.name).pathname.startsWith('/api/')).map(e=>({path:new URL(e.name).pathname,http_status:e.responseStatus||null})),guard:window.__roleReadOnly}})()'''

def capture(role, key, path):
    browser(['open', URL + path])
    time.sleep(.45)
    evaluate(GUARD)
    for attempt in range(15):
        page = evaluate(PAGE)
        if page['path'] == path and page['headings'] and not any(x['http_status'] == 0 for x in page['safe_api_resources']): break
        time.sleep(.2)
    browser(['screenshot', str(EVIDENCE / (role + '-' + key + '.png'))])
    page['evidence'] = 'environment/role-ui-evidence/' + role + '-' + key + '.png'
    labels = [x['text'] for x in page['buttons']] + [x['aria'] or '' for x in page['buttons']]
    page['unexpected_cud_controls'] = [x for x in labels if re.search(r'新建|新增|编辑|删除|保存|克隆|纳管|上传|维护|停用|撤销凭证|\b(Create|New|Edit|Delete|Save|Clone|Enroll|Upload)\b', x, re.I)]
    page['status'] = 'PASS' if not page['unexpected_cud_controls'] else 'FAIL'
    result['roles'][role]['pages'][key] = page
    save(); print(json.dumps({'role': role, 'page': key, 'status': page['status'], 'rows': page['table_rows']}), flush=True)
    return page

def api_get(path, authenticated=True):
    script = '(async()=>{const response=await fetch(' + json.dumps(path) + ',{headers:' + ('{Authorization:"Bearer "+localStorage.getItem("nodesteer_token")}' if authenticated else '{}') + '});const value=await response.json();return {status:response.status,error:value.error||null,data_summary:Array.isArray(value)?{count:value.length,first_id:value[0]?.id||null}:null}})()'
    return evaluate(script)

try:
    for role, session in [('operator', 'rolop'), ('viewer', 'rolvi')]:
        assert accounts[role]['role'] == role
        profile = PRIVATE / ('browser-role-' + role)
        assert not profile.exists(), 'Fresh role profile already exists; avoid reusing old login token'
        profile.mkdir(mode=0o700)
        config = PRIVATE / ('browser-role-' + role + '-config.json'); config.write_text('{}'); config.chmod(0o600)
        ENV = dict(os.environ, AGENT_BROWSER_SOCKET_DIR=str(PRIVATE / 'browser-runtime'), AGENT_BROWSER_PROFILE=str(profile), AGENT_BROWSER_CONFIG=str(config), AGENT_BROWSER_ARGS='--no-sandbox,--disable-setuid-sandbox,--disable-crash-reporter,--disable-breakpad,--crash-dumps-dir=' + str(PRIVATE / 'browser-crash'), XDG_CACHE_HOME=str(PRIVATE / 'browser-cache'), XDG_CONFIG_HOME=str(PRIVATE / 'browser-xdg-config'), TMPDIR=str(PRIVATE))
        BASE = ['agent-browser', '--namespace', 'nse2e', '--session', session]
        result['roles'][role] = {'pages': {}, 'contracts': [], 'dialogs': [], 'source_expected': {'can_write': False, 'can_run': role == 'operator', 'users_admin_only': True, 'runtime_inputs_disabled': True}}
        browser(['open', URL + '/sign-in']); assert evaluate('!!localStorage.getItem("nodesteer_token")') is False
        browser(['snapshot', '-i'])
        evaluate('(()=>{const values=' + json.dumps({'username': accounts[role]['username'], 'password': accounts[role]['password']}) + ';for(const [name,value]of Object.entries(values)){const input=document.querySelector("input[name="+name+"]");if(!input)throw new Error("login input missing");Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,value);input.dispatchEvent(new Event("input",{bubbles:true}));input.dispatchEvent(new Event("change",{bubbles:true}))}return true})()')
        browser(['click', 'button[type=submit]'])
        for attempt in range(35):
            logged = evaluate('(()=>{return {path:location.pathname,has_token:!!localStorage.getItem("nodesteer_token")}})()')
            if logged['path'] == '/' and logged['has_token']: break
            time.sleep(.2)
        assert logged['path'] == '/' and logged['has_token']
        me = evaluate('(async()=>{const r=await fetch("/api/me",{headers:{Authorization:"Bearer "+localStorage.getItem("nodesteer_token")}});const d=await r.json();return {status:r.status,role:d.Role||d.role}})()')
        assert me == {'status': 200, 'role': role}
        result['roles'][role]['fresh_real_ui_login'] = me
        for key, path in [('dashboard','/'),('agents','/agents'),('groups','/groups'),('scripts','/scripts'),('tasks','/tasks'),('schedules','/schedules'),('applications','/applications'),('artifacts','/artifacts'),('executions','/executions'),('transfers','/transfers'),('audit','/audit'),('users','/users'),('settings','/settings')]:
            capture(role, key, path)
            if key == 'users':
                entry = result['roles'][role]['pages'][key]; entry['status'] = 'PASS' if entry['users_admin_only'] and not entry['unexpected_cud_controls'] else 'FAIL';save()
            if key == 'settings':
                entry = result['roles'][role]['pages'][key]; entry['runtime_inputs_all_disabled'] = bool(entry['inputs']) and all(x['disabled'] for x in entry['inputs']); entry['status'] = 'PASS' if entry['runtime_inputs_all_disabled'] and not entry['unexpected_cud_controls'] else 'FAIL';save()
        for path in ['/api/nodes','/api/groups','/api/scripts','/api/tasks','/api/schedules','/api/applications','/api/artifacts','/api/executions?limit=1','/api/transfers','/api/audit?limit=1','/api/settings','/api/users']:
            response = api_get(path); expected = 403 if path == '/api/users' else 200
            result['roles'][role]['contracts'].append({'method':'GET','path':path,'expected_http':expected,'actual_http':response['status'],'error':response['error'],'status':'PASS' if response['status']==expected else 'FAIL'})
        anonymous = api_get('/api/users',False); result['roles'][role]['contracts'].append({'method':'GET','path':'/api/users','authentication':'absent','expected_http':401,'actual_http':anonymous['status'],'status':'PASS' if anonymous['status']==401 else 'FAIL'})
        task = api_get('/api/tasks')['data_summary']
        if task and task['count']:
            page = capture(role,'task-run','/tasks/917a3edc-6de0-4e6a-a988-c81e51ee9e55/run'); visible = any(x['text']=='立即运行' or x['aria']=='立即运行' or x['text']=='Run now' for x in page['buttons']);page['run_control_matches_role']=visible==(role=='operator');page['status']='PASS' if page['run_control_matches_role'] and not page['unexpected_cud_controls'] else 'FAIL';save()
            if role=='operator' and visible:
                browser(['find','role','button','click','--name','立即运行'])
                dialog = evaluate('(()=>{const d=document.querySelector("[role=alertdialog],[role=dialog]");return {present:!!d,buttons:d?[...d.querySelectorAll("button")].map(e=>e.innerText):[],guard:window.__roleReadOnly}})()');browser(['screenshot',str(EVIDENCE/(role+'-task-run-dialog.png'))]);browser(['find','role','button','click','--name','取消']);time.sleep(.4);closed=evaluate('!document.querySelector("[role=alertdialog][data-state=open],[role=dialog][data-state=open]")');result['roles'][role]['dialogs'].append({'id':'task-run-open-cancel','status':'PASS' if dialog['present'] and closed and not dialog['guard']['blockedWrites'] else 'FAIL','opened':dialog['present'],'closed':closed,'business_requests':dialog['guard']['requests'],'blocked_writes':dialog['guard']['blockedWrites'],'evidence':'environment/role-ui-evidence/'+role+'-task-run-dialog.png'});save()
        capture(role,'agent-bulk','/agents')
        checkboxes=evaluate('document.querySelectorAll("tbody button[role=checkbox]").length')
        if role=='operator' and checkboxes:
            browser(['find','first','tbody button[role=checkbox]','check'])
            controls=evaluate(PAGE);bulk_visible=any('升级 Agent' in x['text'] for x in controls['buttons'])
            browser(['find','role','button','click','--name','升级 Agent'])
            dialog=evaluate('(()=>{const d=document.querySelector("[role=alertdialog],[role=dialog]");return {present:!!d,guard:window.__roleReadOnly}})()');browser(['screenshot',str(EVIDENCE/(role+'-upgrade-dialog.png'))]);browser(['find','role','button','click','--name','取消']);time.sleep(.4);closed=evaluate('!document.querySelector("[role=alertdialog][data-state=open],[role=dialog][data-state=open]")');result['roles'][role]['dialogs'].append({'id':'agent-upgrade-open-cancel','status':'PASS' if bulk_visible and dialog['present'] and closed and not dialog['guard']['blockedWrites'] else 'FAIL','opened':dialog['present'],'closed':closed,'business_requests':dialog['guard']['requests'],'blocked_writes':dialog['guard']['blockedWrites'],'evidence':'environment/role-ui-evidence/'+role+'-upgrade-dialog.png'});save()
        elif role=='viewer':
            result['roles'][role]['agent_selection_hidden']={'status':'PASS' if checkboxes==0 else 'FAIL','visible_selection_checkboxes':checkboxes};save()
        apps=api_get('/api/applications')['data_summary']
        if apps and apps['count']:
            page=capture(role,'application-detail','/applications/e35f4c8d-e88e-42b0-87c3-d395911222e6');deploy_visible=any(x['text'] in ['部署','Deploy'] for x in page['buttons']);assignboxes=[x for x in page['inputs'] if x['label']=='checkbox'];page['application_operation_control_matches_role']=deploy_visible==(role=='operator');page['assign_checkboxes_disabled']=bool(assignboxes) and all(x['disabled'] for x in assignboxes);page['status']='PASS' if page['application_operation_control_matches_role'] and page['assign_checkboxes_disabled'] and not page['unexpected_cud_controls'] else 'FAIL';save()
            if role=='operator' and deploy_visible:
                browser(['find','text','deploy','click','--exact']);options=evaluate('[...document.querySelectorAll("[role=option]")].map(e=>e.innerText.trim())');browser(['screenshot',str(EVIDENCE/(role+'-app-operation-options.png'))]);browser(['press','Escape']);guard=evaluate('window.__roleReadOnly');result['roles'][role]['application_operation_control']={'status':'PASS' if options==['deploy','start','stop','restart','upgrade'] and not guard['blockedWrites'] else 'FAIL','options':options,'menu_closed':evaluate('!document.querySelector("[role=listbox]")'),'actual_operation_dispatched':False,'reason':'Deployment button posts immediately; only operation menu opened/closed under zero-write authorization.','business_requests':guard['requests'],'blocked_writes':guard['blockedWrites'],'evidence':'environment/role-ui-evidence/'+role+'-app-operation-options.png'};save()
            else:result['roles'][role]['application_operation_control']={'status':'PASS' if not deploy_visible else 'FAIL','operation_controls_visible':deploy_visible}
        else:result['roles'][role]['application_operation_control']='NOT_VERIFIED_EMPTY_APPLICATION_LIST'
        browser(['close'],allow_error=True);save()
    result['status']='passed' if all(p['status']=='PASS' for role in result['roles'].values() for p in role['pages'].values()) and all(c['status']=='PASS' for role in result['roles'].values() for c in role['contracts']) else 'completed_with_failures'
except Exception as e:
    result['status']='interrupted';result['error']={'type':type(e).__name__,'message':'Role UI validation failed; private CLI logs retained'}
finally:
    if BASE:browser(['close'],allow_error=True)
    result['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save();print(json.dumps({'role_ui':result['status'],'page_counts':{k:len(v['pages']) for k,v in result['roles'].items()},'error':result.get('error')}),flush=True)
