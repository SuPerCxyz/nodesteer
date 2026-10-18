#!/usr/bin/env python3
"""已加载 UI 的真实 SSH 转发中断、退避、Retry 恢复；原 Hub/Agent 持续运行。"""
from pathlib import Path

driver=Path(__file__).with_name('run.py')
prefix=driver.read_text().split("try:\n    browser(['set', 'media', 'light'])")[0]
prefix=prefix.replace("SESSION = 'rcon3'","SESSION = 'rtr6'").replace("'readonly-ui-profile'","'readonly-ui-transport-profile-fresh3'").replace("'readonly-ui-browser.json'","'readonly-ui-transport-browser-fresh3.json'").replace("'readonly-ui-secrets.json'","'readonly-ui-transport-secrets-fresh3.json'").replace("PROFILE.mkdir(mode=0o700, exist_ok=False)","PROFILE.mkdir(mode=0o700, exist_ok=True)")
exec(compile(prefix,str(driver),'exec'))
serial=800
result={'run_id':'20260930-101945','classification':'REAL_UI_TRANSPORT_FAILURE','status':'running','scenarios':[],'cases':[],'remote_hub_agent_changes':0,'network_responses_mocked':False,'business_definition_configuration_writes':0,'source':['web/src/main.tsx:27','web/src/components/layout/nodesteer-header.tsx:23','web/src/features/overview/index.tsx:100','web/src/features/catalog/index.tsx:2302']}

def save():
    SECRETS.write_text(json.dumps(secret_state,indent=2));SECRETS.chmod(0o600)
    (OUT/'transport-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))

GUARD='''(()=>{const original=window.fetch.bind(window);window.__readonlyProof={requests:[],failures:[],blocked:[]};window.fetch=async(input,init={})=>{const url=new URL(typeof input==='string'?input:input.url,location.href);const method=(init.method||(typeof input==='object'?input.method:'GET')||'GET').toUpperCase();if(url.origin===location.origin&&url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(method)&&!['/api/login','/api/logout'].includes(url.pathname)){window.__readonlyProof.blocked.push({path:url.pathname,method});throw new Error('Readonly verification prevents business mutation')}try{const r=await original(input,init);if(url.pathname.startsWith('/api/'))window.__readonlyProof.requests.push({path:url.pathname,method,http:r.status});return r}catch(e){if(url.pathname.startsWith('/api/'))window.__readonlyProof.failures.push({path:url.pathname,method,error:String(e)});throw e}}})()'''
UI='''(()=>{const root=document.querySelector('main')||document.body;const header=document.querySelector('header');const s=header?[...header.querySelectorAll('span')].find(e=>/^Hub (已连接|不可用|检查中)$/.test(e.textContent.trim())):null;const dot=s?.querySelector('span');return {path:location.pathname,heading:document.querySelector('h1')?.innerText||null,table_rows:root.querySelectorAll('tbody tr').length,has_retry:[...root.querySelectorAll('button')].some(e=>e.innerText.trim()==='重试'),hub_unavailable:document.body.innerText.includes('Hub 不可用'),hub_connected:document.body.innerText.includes('Hub 已连接'),hub_status_text:s?.textContent.trim()||null,dot_class:dot?.className||null,dot_color:dot?getComputedStyle(dot).backgroundColor:null,failed_fetch_feedback:root.innerText.includes('Failed to fetch')||root.innerText.includes('无法连接')||root.innerText.includes('网络'),visible:document.visibilityState,has_focus:document.hasFocus(),has_token:!!localStorage.getItem('nodesteer_token'),guard:window.__readonlyProof}})()'''

def start_tunnel(port):
    h=CFG['hub'];args=['ssh','-F','/dev/null','-T','-N','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ExitOnForwardFailure=yes','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-p',str(h['ssh_port']),'-i',h['ssh_identity_file'],'-L','127.0.0.1:'+str(port)+':127.0.0.1:8080',h['ssh_target']]
    p=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=tunnel_stderr)
    for attempt in range(40):
        assert p.poll() is None
        try:
            with urllib.request.urlopen(local+'/healthz',timeout=2) as r:
                if r.status==200:return p
        except (urllib.error.URLError,TimeoutError,ConnectionError):time.sleep(.2)
    raise RuntimeError('Owned localhost forwarding ready timeout')

def stop_tunnel(p):
    if p and p.poll() is None:
        p.terminate()
        try:p.wait(timeout=5)
        except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    return p is None or p.poll() is not None

def primary_snapshot():
    fields={}
    for path in ['/healthz','/readyz']:
        with urllib.request.urlopen(BASE_URL+path,timeout=8) as q:fields[path]=q.status
    http,raw=request('/api/nodes',CFG['admin_token']);allnodes=json.loads(raw);expected={n['id'] for n in CFG['nodes']};fields['nodes_http']=http;fields['native_nodes']=[{k:n[k] for k in ['id','status','sync_status','agent_version','deployment_mode']} for n in allnodes if n['id']in expected];fields['native_four_remain_online_synced']=len(fields['native_nodes'])==4 and all(n['status']=='online' and n['sync_status']=='synced' for n in fields['native_nodes']);return fields

def safe_network():
    obj=json.loads(browser(['--json','network','requests','--type','fetch,xhr']).stdout);data=obj.get('data');rows=data.get('requests',[]) if isinstance(data,dict) else data if isinstance(data,list) else [];out=[]
    for item in rows:
        url=item.get('url')or item.get('request',{}).get('url')or'';parsed=urllib.parse.urlsplit(url)
        if parsed.path.startswith('/api/')or parsed.path=='/healthz':
            err=item.get('error')or item.get('errorText')or item.get('failureText')or item.get('failure')
            text=json.dumps(item)
            out.append({'path':parsed.path,'method':item.get('method')or item.get('request',{}).get('method'),'http':item.get('status')or item.get('statusCode')or item.get('response',{}).get('status'),'connection_refused':'ERR_CONNECTION_REFUSED'in text,'failed':bool(err)or item.get('state')=='failed','transport_error':'ERR_CONNECTION_REFUSED' if 'ERR_CONNECTION_REFUSED'in text else None})
    return out

try:
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    local='http://localhost:'+str(port);tunnel_stderr=(PRIVATE/'readonly-ui-transport-tunnel.stderr').open('wb');tunnel=start_tunnel(port);result['local_forward']={'port':port,'initial_owned_pid':tunnel.pid,'port_free_checked':True,'remote_target':'authorized primaryHub loopback8080','same_port_reused_for_recovery':True};token=login(local);save()
    node_detail_path='/agents/'+CFG['nodes'][0]['id']
    for name,path in [('dashboard','/'),('nodes','/agents'),('node_detail',node_detail_path)]:
        if evaluate('location.pathname')!=path:browser(['open',local+path])
        time.sleep(.5);evaluate(GUARD)
        for attempt in range(25):
            before=evaluate(UI)
            if before['path']==path and before['hub_connected'] and not before['has_retry'] and (name!='nodes'or before['table_rows']>=4):break
            time.sleep(.3)
        if not (before['path']==path and before['hub_connected'] and not before['has_retry']):
            result['precondition_failure']={'page':name,'state':before,'network':safe_network()};browser(['screenshot',str(OUT/(name+'-transport-precondition-failed.png'))]);save();raise AssertionError('Fresh authenticated UI did not reach connected page')
        browser(['screenshot',str(OUT/(name+'-transport-loaded.png'))]);scenario={'page':name,'path':path,'loaded_before_cut':before,'before':primary_snapshot(),'observations':[],'source_hub_stopped':False,'classification':'REAL_UI_TRANSPORT_FAILURE'};result['scenarios'].append(scenario);save()
        time.sleep(11)
        tabs=json.loads(browser(['--json','tab','list']).stdout)['data'];tabs=tabs.get('tabs',[]) if isinstance(tabs,dict) else tabs;app_tab=next(x['tabId'] for x in tabs if urllib.parse.urlsplit(x.get('url','')).path==path and x.get('url','').startswith(local))
        browser(['network','requests','--clear']);scenario['owned_tunnel_cut_pid']=tunnel.pid;scenario['owned_tunnel_stopped']=stop_tunnel(tunnel)
        with socket.socket() as s:scenario['local_port_refused']=s.connect_ex(('127.0.0.1',port))!=0
        scenario['actual_primary_during_cut']=primary_snapshot();start=time.monotonic()
        # 页签切换是真实前台/后台交互，触发 PROD 的 stale query focus refetch，未注入 HTTP 数据。
        browser(['tab','new','--label','focus-trigger-'+name,'about:blank']);browser(['tab',app_tab]);scenario['refetch_trigger']='Real browser tab activation after source query staleTime10s; Dashboard also polls15s'
        while time.monotonic()-start<95:
            state=evaluate(UI);state['elapsed_sec']=round(time.monotonic()-start,2);scenario['observations'].append(state);save()
            if state['hub_unavailable'] and state['has_retry']:break
            print(json.dumps({'transport_page':name,'elapsed':state['elapsed_sec'],'hub_unavailable':state['hub_unavailable'],'has_retry':state['has_retry']}),flush=True);time.sleep(5)
        failed=evaluate(UI);scenario['failure_ui']=failed;scenario['waited_seconds']=round(time.monotonic()-start,2);scenario['failed_network']=safe_network();scenario['failed_fetches']=evaluate('window.__readonlyProof?.failures||[]');browser(['screenshot',str(OUT/(name+'-transport-refused.png'))]);scenario['failure_screenshot']='environment/readonly-ui-continuation/'+name+'-transport-refused.png';save()
        tunnel=start_tunnel(port);scenario['recovered_owned_tunnel_pid']=tunnel.pid
        if failed['has_retry']:
            browser(['find','role','button','click','--name','重试','--exact']);scenario['actual_retry_clicked']=True
        else:scenario['actual_retry_clicked']=False
        for attempt in range(45):
            recovered=evaluate(UI)
            if recovered['hub_connected'] and not recovered['has_retry'] and (name!='nodes'or recovered['table_rows']>=4):break
            time.sleep(.5)
        scenario['recovered_ui']=recovered;scenario['recovered_network']=safe_network();scenario['after']=primary_snapshot();browser(['screenshot',str(OUT/(name+'-transport-recovered.png'))]);scenario['recovery_screenshot']='environment/readonly-ui-continuation/'+name+'-transport-recovered.png';save()
        api_failure=any(x['path'].startswith('/api/') for x in scenario['failed_fetches'])
        recovered_ok=recovered['hub_connected'] and not recovered['has_retry'] and (name!='nodes'or recovered['table_rows']>=4)
        hubcase=next((x for x in result['cases'] if x['case_id']=='P02-05'),None)
        if hubcase is None:
            hubcase={'case_id':'P02-05','status':'PASS','classification':'REAL_UI_TRANSPORT_FAILURE','pages':[],'covered':['Loaded real UI before client transport cut','Actual Hub unavailable text/status dot','Same forwarding port restored; real header health recovered'],'remaining':['Actual backend Hub process restart is outside this transport scenario'],'evidence':['environment/readonly-ui-continuation/transport-summary.json']};result['cases'].append(hubcase)
        hub_page={'page':name,'status':'PASS' if failed['hub_unavailable'] and 'bg-destructive'in(failed['dot_class']or'') and recovered['hub_connected'] else 'PARTIAL','failure_screenshot':scenario['failure_screenshot'],'recovery_screenshot':scenario['recovery_screenshot']};hubcase['pages'].append(hub_page);hubcase['status']='PASS' if all(x['status']=='PASS' for x in hubcase['pages']) else 'PARTIAL'
        apicase_id='P03-10' if name=='dashboard' else 'P04-09'
        apicase=next((x for x in result['cases'] if x['case_id']==apicase_id),None)
        if apicase is None:
            apicase={'case_id':apicase_id,'status':'PASS','classification':'REAL_UI_TRANSPORT_FAILURE','pages':[],'covered':['Real browser fetch rejection captured without response mocking','Actual app Error/Retry UI with no fabricated data','Same-port restoration and native Retry click restored real data'],'remaining':[],'api_paths':[],'evidence':['environment/readonly-ui-continuation/transport-summary.json']};result['cases'].append(apicase)
        failed_paths={x['path'] for x in scenario['failed_fetches']}
        recovered_requests=(recovered.get('guard') or {}).get('requests',[])
        recovered_api=any(x.get('path') in failed_paths and x.get('http')==200 for x in recovered_requests)
        pagecase={'page':name,'status':'PASS' if api_failure and failed['has_retry'] and failed['table_rows']==0 and scenario['actual_retry_clicked'] and recovered_ok and recovered_api else 'PARTIAL','api_failures':scenario['failed_fetches'],'retry_visible':failed['has_retry'],'retry_clicked':scenario['actual_retry_clicked'],'recovered_api_200':recovered_api,'recovered_ui':recovered_ok,'failure_screenshot':scenario['failure_screenshot'],'recovery_screenshot':scenario['recovery_screenshot']}
        apicase['pages'].append(pagecase);apicase['api_paths']+=scenario['failed_fetches'];apicase['status']='PASS' if all(x['status']=='PASS' for x in apicase['pages']) else 'PARTIAL'
        if not all(x['status']=='PASS' for x in apicase['pages']):apicase['remaining']=['At least one page lacks a captured failed fetch, visible Retry, native recovery action, same endpoint HTTP 200, or complete UI recovery.']
        save();print(json.dumps({'transport_page':name,'hub_case':hubcase['status'],'api_case':apicase['status'],'waited':scenario['waited_seconds'],'actual_retry':scenario['actual_retry_clicked'],'failed_fetch_count':len(scenario['failed_fetches']),'recovered_api_200':recovered_api}),flush=True)
    result['status']='completed'
except Exception as e:
    import traceback
    result['status']='interrupted';result['error']={'type':type(e).__name__,'stage':traceback.extract_tb(e.__traceback__)[-1].name,'line':traceback.extract_tb(e.__traceback__)[-1].lineno,'message':'Real UI transport proof interrupted; private CLI/tunnel logs retained'}
finally:
    cleanup={'source_session_not_logged_out':True,'own_session_logout':[]}
    for token in own_tokens:
        assert token not in [CFG['token'],CFG['admin_token']];http,_=request('/api/logout',token,method='POST');cleanup['own_session_logout'].append({'http':http,'source_session':False})
    browser(['close'],allow_error=True);cleanup['browser_closed']=True;cleanup['owned_tunnel_stopped']=stop_tunnel(tunnel)
    if tunnel:cleanup['last_owned_tunnel_pid']=tunnel.pid
    if 'port'in globals():
        with socket.socket() as s:cleanup['local_forward_port_closed']=s.connect_ex(('127.0.0.1',port))!=0
    if tunnel_stderr:tunnel_stderr.close()
    source_http,_=request('/api/me',CFG['admin_token']);cleanup['source_session_me_http']=source_http;result['cleanup']=cleanup;result['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save();print(json.dumps({'transport_result':result['status'],'cases':[(x['case_id'],x['status'],x.get('page')) for x in result['cases']],'cleanup':cleanup,'error':result.get('error')}),flush=True)
