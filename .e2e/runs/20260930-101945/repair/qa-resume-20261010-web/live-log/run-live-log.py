import hashlib, json, re, secrets, subprocess, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from websockets.sync.client import connect as ws_connect

R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/live-log')
ORIGIN='http://127.0.0.1:38181'; SESSION='qawebsecure20261010-live-log-38181'
CREDS=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
AB=['agent-browser','--session',SESSION,'--allowed-domains','127.0.0.1']
SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
task=None; task_name=None; execution=None; tunnel=None; startticks=None; arg_hash=None; page_open=False; run_action_attempted=False; cdp=None; context_id=None; permissions_reset=False; error=None; result={}

def utc(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def save(n,d): (R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def progress(phase,gate,**extra):
 p=json.loads((R/'progress.json').read_text());p.update({'phase':phase,'updated_at_utc':utc(),'current_gate':gate,'owned_task_id':task.get('id') if task else None,'execution_id':execution,'private_values_saved':False,'raw_network_or_command_content_saved':False,'tunnel_started':bool(tunnel and tunnel.poll() is None)});p.update(extra);(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'phase':phase,'gate':gate,'task_created':bool(task),'execution_seen':bool(execution)},ensure_ascii=False),flush=True)
def cli(a,stdin=None,timeout=22):
 p=subprocess.run(AB+[str(x) for x in a],input=stdin,capture_output=True,text=True,timeout=timeout)
 if p.returncode: raise RuntimeError('agent-browser action failed: '+str(a[0]))
 return p.stdout
def ev(js): return json.loads(cli(['--json','eval',js],timeout=25))['data']['result']
def starttime(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def argvraw(pid): return Path(f'/proc/{pid}/cmdline').read_bytes()
def ssh_cfg():
 p=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15);v={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlmaster','controlpath')}
 for l in p.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in v:v[z[0]].append(z[1])
 ok=p.returncode==0 and not v['localforward'] and not v['remoteforward'] and not any(x!='none' for x in v['remotecommand']) and not any(x=='yes' for x in v['forwardagent']) and not v['controlpath']
 return ok,{'exit':p.returncode,'localforward_count':len(v['localforward']),'remoteforward_count':len(v['remoteforward']),'remotecommand_nonempty':any(x!='none' for x in v['remotecommand']),'forwardagent_enabled':any(x=='yes' for x in v['forwardagent']),'controlpath_set':bool(v['controlpath'])}
def ss_state():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10);rows=[x.strip() for x in p.stdout.splitlines() if x.strip()];out=[]
 for x in rows:
  addr=re.search(r'\s([^\s]+):38181\s',x);pids=re.findall(r'pid=(\d+)',x);out.append({'loopback_only':bool(addr and addr.group(1)=='127.0.0.1'),'pid_matches_owned':bool(tunnel and str(tunnel.pid) in pids),'pids':pids})
 return {'exit':p.returncode,'count':len(rows),'listeners':out}
def netreq(suffix):
 p=subprocess.run(AB+['--json','network','requests'],capture_output=True,text=True,timeout=18)
 if p.returncode:return []
 try:xs=json.loads(p.stdout).get('data',{}).get('requests',[])
 except Exception:return []
 return [{'request_id':x.get('requestId'),'status':x.get('status')} for x in xs if isinstance(x.get('url'),str) and x['url'].split('?',1)[0].endswith(suffix) and x.get('method')=='POST']
def respmeta(rid):
 p=subprocess.run(AB+['--json','network','request',str(rid)],capture_output=True,text=True,timeout=18)
 if p.returncode:return None
 try:
  d=json.loads(p.stdout).get('data',{});b=d.get('responseBody');b=json.loads(b) if isinstance(b,str) else b
  if not isinstance(b,dict):return {'http':d.get('status'),'id':None}
  return {'http':d.get('status'),'id':b.get('id') or b.get('task_id') or b.get('execution_id')}
 except Exception:return None
def api_nodes():return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/nodes',{headers:{Authorization:'Bearer '+t}}),d=await r.json(),a=Array.isArray(d)?d:(d.items||d.nodes||[]);return {http:r.status,nodes:a.map(x=>({id:x.id,hostname:x.hostname,status:x.status,sync_status:x.sync_status}))}})()''')
def api_tasks():return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/tasks',{headers:{Authorization:'Bearer '+t}}),d=await r.json(),a=Array.isArray(d)?d:(d.items||d.tasks||[]);return {http:r.status,tasks:a.map(x=>({id:x.id,name:x.name,retry:x.retry,target_type:x.target?.type,node_ids:x.target?.node_ids||[]}))}})()''')
def api_execs(tid):return ev('''(async()=>{const id=%s,t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions?task_id='+id+'&limit=10',{headers:{Authorization:'Bearer '+t}}),d=await r.json(),a=Array.isArray(d)?d:(d.items||d.executions||[]);return {http:r.status,items:a.filter(x=>x.task_id===id).map(x=>({id:x.id,status:x.status,exit_code:x.exit_code,created_at:x.created_at}))}})()'''%json.dumps(tid))
def exec_detail(eid):return ev('''(async()=>{const id=%s,t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/executions/'+id,{headers:{Authorization:'Bearer '+t}}),d=await r.json(),hash=async s=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s||''));return [...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')};return {http:r.status,id:d.id,status:d.status,exit_code:d.exit_code,task_id:d.task_id,stdout_bytes:new TextEncoder().encode(d.stdout||'').length,stderr_bytes:new TextEncoder().encode(d.stderr||'').length,stdout_sha256:await hash(d.stdout),stderr_sha256:await hash(d.stderr),chunk_count:Array.isArray(d.chunks)?d.chunks.length:null,streams:Array.isArray(d.chunks)?[...new Set(d.chunks.map(x=>x.stream))].sort():[]}})()'''%json.dumps(eid))
def markers(marker):return ev('''(()=>{const p=document.querySelector('#execution-logs pre'),s=p?.textContent||'';return {present:!!p,out:s.includes(%s),err:s.includes(%s),out_count:(s.match(new RegExp(%s,'g'))||[]).length,err_count:(s.match(new RegExp(%s,'g'))||[]).length,scroll_top:p?.scrollTop||0,scroll_height:p?.scrollHeight||0,client_height:p?.clientHeight||0}})()'''%tuple(json.dumps(x) for x in (marker+'-out-',marker+'-err-',marker+'-out-',marker+'-err-')))
def permission_states():return ev('''(async()=>{const q=async n=>{try{return (await navigator.permissions.query({name:n})).state}catch{return 'unsupported'}};return {read:await q('clipboard-read'),write:await q('clipboard-write')}})()''')
def toast_kind():return ev('''(()=>{const s=[...document.querySelectorAll('[data-sonner-toast],[role=status],[role=alert]')].map(x=>x.innerText||'').join(' ').toLowerCase();return s.includes('手动复制')||s.includes('manually')?'manual_fallback':s.includes('已复制')||s.includes('copied')?'success':s.includes('复制失败')||s.includes('copy failed')?'error':s?'other':'none'})()''')
def clip_oracle():return ev('''(async()=>{const p=document.querySelector('#execution-logs pre'),e=p?.textContent||'';let x=null,read='ok';try{x=await navigator.clipboard.readText()}catch{read='rejected'};const h=async s=>{const b=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(s));return [...new Uint8Array(b)].map(v=>v.toString(16).padStart(2,'0')).join('')};return {read,exact_equal:x!==null&&x===e,clipboard_bytes:x===null?null:new TextEncoder().encode(x).length,clipboard_sha256:x===null?null:await h(x),expected_bytes:new TextEncoder().encode(e).length,expected_sha256:await h(e)}})()''')
def row_pending(eid,name):return ev('''(()=>{const a=document.querySelector('a[href="/executions/'+%s+'"]'),r=a?.closest('tr'),s=r?.innerText||'';return {route:location.pathname,row_present:!!r,task_name_present:s.includes(%s),pending:/等待中|pending/i.test(s)}})()'''%(json.dumps(eid),json.dumps(name)))
def delete_task(tid):return ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},r=await fetch('/api/tasks/'+%s,{method:'DELETE',headers:h}),g=await fetch('/api/tasks/'+%s,{headers:h});return {delete_http:r.status,get_after_http:g.status}})()'''%(json.dumps(tid),json.dumps(tid)))
def cdp_call(method,params=None):
 cdp_call.i+=1;i=cdp_call.i;cdp.send(json.dumps({'id':i,'method':method,'params':params or {}}));end=time.monotonic()+8
 while time.monotonic()<end:
  m=json.loads(cdp.recv(timeout=2))
  if m.get('id')==i:
   if 'error' in m:raise RuntimeError('CDP operation rejected: '+method)
   return m.get('result',{})
 raise RuntimeError('CDP response timeout: '+method)
cdp_call.i=0

try:
    config_ok, config_meta = ssh_cfg()
    port = subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10)
    if not config_ok or port.returncode or port.stdout.strip():
        raise RuntimeError('SSH config/port guard failed; forward not launched')
    progress('SSH_PREFLIGHT_PASS','filtered ssh -G has no extra forwarding/remote command; port 38181 unbound',ssh_g=config_meta)
    arg_hash=hashlib.sha256('\0'.join(SSH).encode()).hexdigest()
    tunnel=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
    startticks=starttime(tunnel.pid)
    ready=False
    for _ in range(50):
        if tunnel.poll() is not None: raise RuntimeError('owned SSH forward exited before ready')
        ss=ss_state()
        if ss['count']==1 and ss['listeners'][0]['loopback_only'] and ss['listeners'][0]['pid_matches_owned']:
            try:
                with urllib.request.urlopen(ORIGIN+'/sign-in',timeout=2) as r: ready=200<=r.status<500
            except Exception: ready=False
        if ready: break
        time.sleep(.2)
    if not ready: raise RuntimeError('loopback-only SSH listener/upstream readiness check failed')
    progress('SSH_FORWARD_READY','only loopback 38181 bound by exact owned SSH child',ssh_pid=tunnel.pid,ssh_startticks=startticks,ssh_argv_sha256=arg_hash,listener=ss_state())

    cli(['open',ORIGIN+'/sign-in'],timeout=30);page_open=True
    cli(['wait','--load','networkidle'],timeout=25)
    creds=json.loads(CREDS.read_text()); user=creds['admin_username']; password=creds['admin_password']; shared=json.loads(SHARED.read_text())['token']
    # Real API login in this exclusive loopback context. Token stays in page memory/storage only.
    login_js='''(async()=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:%s,password:%s})});let d={};try{d=await r.json()}catch{};const tok=d.token||null,match=!!tok&&tok===%s;if(r.status===200&&tok&&!match)localStorage.setItem('nodesteer_token',tok);return {http:r.status,token_present:!!tok,shared_token_match:match,stored:!!tok&&!match&&localStorage.getItem('nodesteer_token')===tok}})()'''%(json.dumps(user),json.dumps(password),json.dumps(shared))
    login=json.loads(cli(['--json','eval','--stdin'],stdin=login_js,timeout=25))['data']['result']
    cli(['network','requests','--clear'])
    if login['http']!=200 or not login['token_present'] or login['shared_token_match'] or not login['stored']:raise RuntimeError('dedicated API login/token isolation failed')
    cli(['open',ORIGIN+'/agents']);cli(['wait','--load','networkidle'],timeout=25)
    auth={'http':0,'token_present':False}
    for _ in range(40):
        auth=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});return {http:r.status,token_present:!!t,secure:isSecureContext,online:navigator.onLine}})()''')
        if auth['http']==200:break
        time.sleep(.25)
    page_meta=ev('''({origin:location.origin,path:location.pathname,secure:isSecureContext,online:navigator.onLine,clipboard_api:!!navigator.clipboard&&typeof navigator.clipboard.readText==='function'&&typeof navigator.clipboard.writeText==='function'})''')
    if auth['http']!=200 or not auth['token_present'] or not page_meta['secure'] or not page_meta['clipboard_api'] or not page_meta['online']:
        raise RuntimeError('fresh loopback auth/secure-context/clipboard/shared-token gate failed')
    progress('EXCLUSIVE_BROWSER_READY','fresh loopback origin, unique token, Secure Context and Clipboard API confirmed',browser=page_meta)
    cli(['network','requests','--clear'])

    nodes=api_nodes();node=next((x for x in nodes['nodes'] if x['hostname']=='qa-repair-20260930-101945-native-3'),None)
    if nodes['http']!=200 or not node or node['status']!='online' or node['sync_status']!='synced':raise RuntimeError('native-3 read-only target gate failed')
    task_name='qa-live-log-20261010-'+secrets.token_hex(3);mark='qa-live-'+secrets.token_hex(4)
    if any(x['name']==task_name for x in api_tasks()['tasks']):raise RuntimeError('unique Task name collision')
    # The synthetic command contains no credentials. It is passed only to the approved Task create API call and never logged/saved.
    cmd=('#!/bin/sh\nset -eu\nmarker='+mark+'\n'
         'emit(){ i="$1"; end="$2"; while [ "$i" -le "$end" ]; do printf "%s-out-%02d\\n" "$marker" "$i"; printf "%s-err-%02d\\n" "$marker" "$i" >&2; i=$((i+1)); done; }\n'
         'emit 1 20\nsleep 6\nemit 21 40\nsleep 6\nemit 41 80\nexit 23\n')
    payload={'name':task_name,'type':'command','command':cmd,'target':{'type':'node','node_ids':[node['id']]},
             'parameters':[],'param_values':{},'timeout':300,'retry':0,'run_user':'',
             'offline_policy':'hub_online_required','enabled':True}
    payload_js='''(async()=>{const b=%s,t=localStorage.getItem('nodesteer_token'),r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+t},body:JSON.stringify(b)});let d={};try{d=await r.json()}catch{};return {http:r.status,id:d.id||d.task_id||null,name:d.name||d.task?.name||null,retry:d.retry??d.task?.retry??null,target_node_ids:d.target?.node_ids||d.task?.target?.node_ids||[],error_present:!!d.error}})()'''%json.dumps(payload)
    created=json.loads(cli(['--json','eval','--stdin'],stdin=payload_js,timeout=25))['data']['result']
    if created['http'] not in (200,201):raise RuntimeError('approved Task create API helper returned non-success status')
    if created.get('id'):
        task={'id':created['id'],'name':task_name,'post_http':created['http'],'id_from':'filtered_browser_API_helper_response'}
    else:
        matches=[x for x in api_tasks()['tasks'] if x['name']==task_name]
        if len(matches)!=1:raise RuntimeError('created Task response omitted ID and exact-name readback not unique')
        task={'id':matches[0]['id'],'name':task_name,'post_http':created['http'],'id_from':'unique_created_Task_API_readback'}
    save('resource-intents.json',{'captured_at_utc':utc(),'planned_tasks':1,'created_tasks':[task],'deleted_tasks':[],
        'execution_id':None,'execution_history_retained':True,'max_ui_runs':1,'retry_count':0,'target_node_id':node['id'],
        'original_node_write_count':0,'private_values_saved':False,'raw_task_command_saved':False})
    progress('TASK_ID_LEDGERED','approved safe Task API helper response ID persisted before post-create assertions',task_post_http=task['post_http'],task_id_source=task['id_from'],target_node_id=node['id'],retry_zero=True,marker_sha256=hashlib.sha256(mark.encode()).hexdigest(),command_plaintext_saved=False)
    tcheck=next((x for x in api_tasks()['tasks'] if x['id']==task['id']),None)
    if not tcheck or tcheck['name']!=task_name or tcheck['retry']!=0 or node['id'] not in tcheck['node_ids']:raise RuntimeError('Task ID/target/retry API oracle mismatch')

    cli(['open',ORIGIN+'/tasks/'+task['id']+'/run']);cli(['wait','--load','networkidle'],timeout=25)
    run_buttons=ev('''(()=>[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(Boolean))()''')
    run_label=next((x for x in ('立即运行','Run now') if x in run_buttons),None)
    if not run_label:raise RuntimeError('Task Run UI action label not found')
    cli(['find','role','button','click','--name',run_label])
    dialog=None;end=time.monotonic()+7
    while time.monotonic()<end:
        dialog=ev('''(()=>{const d=document.querySelector('[role="alertdialog"]');return {visible:!!d,buttons:d?[...d.querySelectorAll('button')].map(b=>({text:(b.innerText||'').trim(),disabled:b.disabled})):[]}})()''')
        if dialog['visible']:break
        time.sleep(.1)
    if not dialog or not dialog['visible']:raise RuntimeError('Run alertdialog did not appear')
    matches=[i for i,b in enumerate(dialog['buttons']) if b['text']==run_label]
    if len(matches)!=1 or dialog['buttons'][matches[0]]['disabled']:raise RuntimeError('enabled Run button not unique inside alertdialog')
    selector='[role="alertdialog"] button:nth-of-type('+str(matches[0]+1)+')'
    exact=ev('''(()=>{const b=document.querySelector(%s);return {exists:!!b,label:(b?.innerText||'').trim(),disabled:!!b?.disabled}})()'''%json.dumps(selector))
    if exact['label']!=run_label or exact['disabled']:raise RuntimeError('alertdialog selector did not resolve exact enabled Run button')
    save('run-confirmation-oracle.json',{'captured_at_utc':utc(),'dialog_role':'alertdialog','buttons':[{'text':b['text'],'disabled':b['disabled']} for b in dialog['buttons']],'confirm_button_index':matches[0]+1,'confirm_enabled':True,'private_values_saved':False})
    cli(['network','requests','--clear']);run_action_attempted=True;progress('RUN_CONFIRM_ENABLED','exact enabled Run button inside visible alertdialog; clicking once',task_id=task['id'],confirm_button_index=matches[0]+1)
    cli(['click',selector])
    run_posts=[];pending=None;first=None;deadline=time.monotonic()+18
    while time.monotonic()<deadline:
        run_posts=netreq('/api/tasks/'+task['id']+'/run');q=api_execs(task['id'])
        if q.get('http')==200 and q.get('items'):
            first=q['items'][-1];is_new=execution is None;execution=first['id']
            if is_new:progress('EXECUTION_ID_CAPTURED','single real UI Run execution found by owned Task ID',run_post_count=len(run_posts),run_post_http=next((x['status'] for x in reversed(run_posts) if x['status'] is not None),None),execution_status=first['status'])
            if first['status']=='PENDING':
                p=row_pending(execution,task_name)
                if p['row_present'] and p['task_name_present'] and p['pending']:pending={'captured_at_utc':utc(),'execution_id':execution,'task_id':task['id'],'api_status':'PENDING','ui_row_present':True,'ui_pending_text':True}
            if first['status'] in ('RUNNING','SUCCESS','FAILED','CANCELED','TIMED_OUT'):break
        time.sleep(.12)
    if not execution:raise RuntimeError('UI Run did not yield an execution ID within bounded polling')
    if len(run_posts)>1:raise RuntimeError('more than one Run POST observed after one user click')
    if pending:save('pending-ui-observation.json',pending);result['pending_ui_observed']=True
    else:save('pending-ui-observation.json',{'captured_at_utc':utc(),'task_id':task['id'],'execution_id':execution,'api_status_first_observed':first.get('status') if first else None,'ui_pending_text_visible':False,'limitation':'bounded polling did not catch task-correlated PENDING rendered in UI; no PASS claimed'});result['pending_ui_observed']=False
    cli(['open',ORIGIN+'/executions/'+execution]);cli(['wait','--load','networkidle'],timeout=25)
    if ev('location.pathname')!='/executions/'+execution:raise RuntimeError('Execution detail route mismatch')
    progress('DETAIL_OPEN_NO_RELOAD','Execution detail opened once; live log/scroll sampling begins')

    first=None;end=time.monotonic()+18
    while time.monotonic()<end:
        x=markers(mark)
        if x['out_count']>=20 and x['err_count']>=20 and x['scroll_height']>x['client_height']:
            first=x;break
        time.sleep(.2)
    if not first:raise RuntimeError('first real dual-stream wave did not overflow rendered log container')
    # Follow starts off. Move the real output element to top and measure it across the next incoming wave.
    start_off=ev('''(()=>{const p=document.querySelector('#execution-logs pre');if(p)p.scrollTop=0;return {top:p?.scrollTop||0,height:p?.scrollHeight||0,client:p?.clientHeight||0}})()''')
    end=time.monotonic()+9
    while time.monotonic()<end:
        x=markers(mark)
        if x['out_count']>=40 and x['err_count']>=40:break
        time.sleep(.18)
    off_after=markers(mark)
    off_stayed_away=off_after['scroll_top']<off_after['scroll_height']-off_after['client_height']-5
    follow_labels=ev('''(()=>[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(x=>x==='跟随'||x==='Follow'))()''')
    follow_label=follow_labels[0] if follow_labels else None
    if not follow_label:raise RuntimeError('Follow control not found in live log UI')
    cli(['find','role','button','click','--name',follow_label])
    toggle=ev('''(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='跟随');return {found:!!b,aria_pressed:b?.getAttribute('aria-pressed'),active_style:!!b&&/secondary/.test(b.className)}})()''')
    end=time.monotonic()+10
    while time.monotonic()<end:
        x=markers(mark)
        if x['out_count']>=80 and x['err_count']>=80:break
        time.sleep(.18)
    on_after=markers(mark)
    on_at_bottom=on_after['scroll_top']+on_after['client_height']>=on_after['scroll_height']-5
    follow={'first_wave':first,'follow_off_start':start_off,'follow_off_after_wave':off_after,
            'follow_off_stayed_away_from_bottom':off_stayed_away,'follow_toggle':toggle,
            'follow_on_after_wave':on_after,'follow_on_at_bottom':on_at_bottom}
    save('follow-scroll-oracle.json',{'captured_at_utc':utc(),'execution_id':execution,'measurements':follow,'private_values_saved':False})

    search='#execution-logs input'
    cli(['fill',search,mark+'-out-01'])
    search_out=ev('''(()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'';return {input_matches:document.querySelector('#execution-logs input')?.value===%s,expected_marker:s.includes(%s),stderr_hidden:!s.includes(%s),visible_bytes:new TextEncoder().encode(s).length}})()'''%(json.dumps(mark+'-out-01'),json.dumps(mark+'-out-01'),json.dumps(mark+'-err-01')))
    cli(['fill',search,''])
    cli(['select','#execution-logs select','stdout'])
    stdout_filter=ev('''(()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'';return {selected:document.querySelector('#execution-logs select')?.value,out:s.includes(%s),err:s.includes(%s),visible_bytes:new TextEncoder().encode(s).length}})()'''%(json.dumps(mark+'-out-01'),json.dumps(mark+'-err-01')))
    cli(['select','#execution-logs select','stderr'])
    stderr_filter=ev('''(()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'';return {selected:document.querySelector('#execution-logs select')?.value,out:s.includes(%s),err:s.includes(%s),visible_bytes:new TextEncoder().encode(s).length}})()'''%(json.dumps(mark+'-out-01'),json.dumps(mark+'-err-01')))
    cli(['select','#execution-logs select','all'])
    cli(['fill',search,mark])
    search_both=ev('''(()=>{const s=document.querySelector('#execution-logs pre')?.textContent||'';return {selected:document.querySelector('#execution-logs select')?.value,out:s.includes(%s),err:s.includes(%s),visible_bytes:new TextEncoder().encode(s).length}})()'''%(json.dumps(mark+'-out-01'),json.dumps(mark+'-err-01')))
    cli(['fill',search,''])
    cli(['select','#execution-logs select','stdout'])

    clipboard={'available':False,'limitation':None}
    try:
        wsurl=cli(['get','cdp-url']).strip()
        if not wsurl.startswith('ws://127.0.0.1:'):raise RuntimeError('CDP endpoint not loopback')
        cdp=ws_connect(wsurl,open_timeout=10,close_timeout=3)
        targets=cdp_call('Target.getTargets').get('targetInfos',[])
        target=next((x for x in targets if x.get('type')=='page' and x.get('url','').startswith(ORIGIN)),None)
        if not target or not target.get('browserContextId'):raise RuntimeError('active origin browserContextId unavailable')
        context_id=target['browserContextId']
        def setperm(name,state):cdp_call('Browser.setPermission',{'permission':{'name':name},'setting':state,'origin':ORIGIN,'browserContextId':context_id})
        setperm('clipboard-read','granted');setperm('clipboard-write','granted')
        granted=permission_states()
        if granted!={'read':'granted','write':'granted'}:raise RuntimeError('Permissions API did not confirm exact-context clipboard grants')
        cli(['fill',search,mark+'-out-01'])
        copy_labels=ev('''(()=>[...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).filter(x=>x==='复制日志'||x==='Copy logs'))()''')
        copy_label=copy_labels[0] if copy_labels else None
        if not copy_label:raise RuntimeError('Copy logs control not found in live log UI')
        cli(['find','role','button','click','--name',copy_label]);time.sleep(.15)
        success_read=clip_oracle();success_toast=toast_kind()
        setperm('clipboard-read','denied');setperm('clipboard-write','denied')
        denied=permission_states()
        cli(['find','role','button','click','--name',copy_label]);time.sleep(.15)
        denied_kind=toast_kind();denied_read=clip_oracle()
        clipboard={'available':True,'context_id':context_id,'origin':ORIGIN,'grant_states':granted,
                   'success_toast':success_toast,'success_read':success_read,'denied_states':denied,
                   'denied_toast_kind':denied_kind,'denied_read':denied_read,
                   'denied_feedback_observed':denied=={'read':'denied','write':'denied'} and denied_kind in ('manual_fallback','error')}
    except Exception as exc:
        clipboard={'available':False,'limitation':type(exc).__name__+': '+str(exc)[:100]}
    finally:
        if context_id and cdp:
            try:cdp_call('Browser.resetPermissions',{'browserContextId':context_id});permissions_reset=True
            except Exception:permissions_reset=False
    save('clipboard-oracle.json',{'captured_at_utc':utc(),'method':'real UI Copy click; real page navigator.clipboard.readText; CDP permission scope exact origin+browserContextId','observation':clipboard,'clipboard_plaintext_saved':False,'private_values_saved':False})

    # Restore unfiltered All view for final independent API/UI oracle.
    cli(['fill',search,'']);cli(['select','#execution-logs select','all'])
    api=exec_detail(execution)
    page=ev('''(()=>({path:location.pathname,secure:isSecureContext,online:navigator.onLine,navEntries:performance.getEntriesByType('navigation').length}))()''')
    visible=markers(mark)
    api['same_detail_route_no_reload']=page['path']=='/executions/'+execution and page['navEntries']==1
    api['browser_online']=page['online']
    save('execution-final-oracle.json',{'captured_at_utc':utc(),'execution':api,'visible_log':visible,'search':search_out,
        'stdout_filter':stdout_filter,'stderr_filter':stderr_filter,'search_both_streams':search_both,
        'run_post_count':len(run_posts),'pending_ui_observed':bool(pending),'route_state':page,'private_values_saved':False})
    exec_ok=api['http']==200 and api['status']=='FAILED' and api['exit_code']==23 and api['stdout_bytes']>0 and api['stderr_bytes']>0
    filter_ok=search_out['expected_marker'] and search_out['stderr_hidden'] and stdout_filter['selected']=='stdout' and stdout_filter['out'] and not stdout_filter['err'] and stderr_filter['selected']=='stderr' and stderr_filter['err'] and not stderr_filter['out'] and search_both['out'] and search_both['err']
    copy_ok=clipboard.get('available') and clipboard.get('success_read',{}).get('read')=='ok' and clipboard.get('success_read',{}).get('exact_equal') and clipboard.get('success_toast')=='success' and clipboard.get('denied_feedback_observed')
    follow_ok=off_stayed_away and on_at_bottom
    result={'captured_at_utc':utc(),'canonical_cases':['WEB-LIVE-LOG','X15'],'task_id':task['id'],'execution_id':execution,
      'status':'PASS_FOR_REMAINING_DIMENSIONS' if exec_ok and filter_ok and copy_ok and follow_ok and api['same_detail_route_no_reload'] and pending else 'PARTIAL',
      'checks':{'real_final_failed_two_streams':exec_ok,'search_and_both_stream_filters':filter_ok,'actual_clipboard_copy_exact_read_and_denied_feedback':bool(copy_ok),'actual_follow_scroll_positions':follow_ok,'pending_ui':bool(pending),'same_detail_no_reload':api['same_detail_route_no_reload']},
      'known_limits':['No attempt number/grouping was asserted or inferred.','If PENDING was not observed in rendered UI, it remains PARTIAL.','Original X15 dimensions outside the approved remainder retain their existing statuses.'],
      'private_values_saved':False,'raw_command_or_network_content_saved':False}
    save('live-log-result.json',result)
    progress('RESULTS_SAVED_CLEANUP_REQUIRED','Real UI/API/clipboard/scroll results persisted without raw secret or command text')

except Exception as exc:
    error={'captured_at_utc':utc(),'type':type(exc).__name__,'safe_message':str(exc)[:160],
           'task_id':task.get('id') if task else None,'execution_id':execution}
    save('runner-error.json',error)
    progress('ERROR_CLEANUP_REQUIRED','Bounded error recorded; finally will clean only response-ledgered Task/browser/tunnel')

finally:
    # Emergency late-arrival recovery for an unledgered but uniquely named Task created by this exact session.
    if not task and task_name and page_open:
        try:
            late=[x for x in api_tasks().get('tasks',[]) if x['name']==task_name]
            if len(late)==1:
                task={'id':late[0]['id'],'name':task_name,'post_http':None,'request_id':None,'id_from':'unique_late_API_readback'}
                if not (R/'resource-intents.json').exists():save('resource-intents.json',{'captured_at_utc':utc(),'planned_tasks':1,'created_tasks':[task],'deleted_tasks':[],'execution_history_retained':True,'max_ui_runs':1,'retry_count':0,'private_values_saved':False,'raw_task_command_saved':False})
                else:
                    res=json.loads((R/'resource-intents.json').read_text());res['created_tasks']=[task];(R/'resource-intents.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
        except Exception:pass
    if context_id and cdp:
        try:cdp_call('Browser.resetPermissions',{'browserContextId':context_id});permissions_reset=True
        except Exception:permissions_reset=False
    cleanup_task=None
    if task and page_open:
        try:
            cleanup_task=delete_task(task['id'])
            res=json.loads((R/'resource-intents.json').read_text());res['deleted_tasks']=[{'id':task['id'],**cleanup_task,'verified':cleanup_task['delete_http'] in (200,204) and cleanup_task['get_after_http']==404}];res['cleanup_verified']=res['deleted_tasks'][0]['verified'];res['execution_id']=execution;(R/'resource-intents.json').write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
        except Exception as exc:save('task-cleanup-error.json',{'captured_at_utc':utc(),'task_id':task['id'],'type':type(exc).__name__})
    logout=None
    if page_open:
        try:
            logout=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');if(!t)return {had_token:false};const r=await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}}),m=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});localStorage.removeItem('nodesteer_token');return {had_token:true,logout_http:r.status,old_me_http:m.status,token_removed:!localStorage.getItem('nodesteer_token'),online:navigator.onLine,secure:isSecureContext}})()''')
            cli(['network','requests','--clear'])
        except Exception as exc:logout={'error_type':type(exc).__name__}
        try:cli(['close'],timeout=12)
        except Exception:pass
        page_open=False
    if cdp:
        try:cdp.close()
        except Exception:pass
    stopped=False;guard=False;after=None
    if tunnel:
        try:
            if tunnel.poll() is None:
                same=Path(f'/proc/{tunnel.pid}/stat').exists() and starttime(tunnel.pid)==startticks
                actual=[x.decode(errors='replace') for x in argvraw(tunnel.pid).split(b'\0') if x] if same else []
                exact=bool(actual and actual[1:]==SSH[1:] and actual[0].endswith('ssh'))
                guard=bool(same and exact and tunnel.pid>1)
                if guard:tunnel.terminate();tunnel.wait(timeout=8);stopped=True
            else:stopped=True
        except Exception:guard=False
        try:after=ss_state()
        except Exception:after=None
    resource=json.loads((R/'resource-intents.json').read_text()) if (R/'resource-intents.json').exists() else {}
    clean={'captured_at_utc':utc(),'task_delete':cleanup_task,'task_cleanup_verified':resource.get('cleanup_verified',False),
           'execution_history_retained':bool(execution),'logout':logout,'permissions_reset_for_exact_context':permissions_reset,
           'browser_session_closed':not page_open,'network_log_cleared':bool(logout and logout.get('had_token')),
           'ssh_pid':tunnel.pid if tunnel else None,'ssh_startticks':startticks,'ssh_argv_sha256':arg_hash,
           'ssh_guard_matched':guard,'ssh_stopped':stopped,'listener_after_stop':after,'listener_gone':bool(after and after['count']==0),'private_values_saved':False}
    save('cleanup-verification.json',clean)
    complete=resource.get('cleanup_verified',False) and not page_open and bool(after and after['count']==0) and bool(logout and logout.get('logout_http')==200 and logout.get('old_me_http')==401) and permissions_reset
    try:progress('CLEANUP_COMPLETE' if complete else 'CLEANUP_INCOMPLETE','Task/browser/CDP/SSH cleanup verification persisted',cleanup_verified=complete,cleanup=clean)
    except Exception:pass

print(json.dumps({'result_status':result.get('status','PARTIAL'),'checks':result.get('checks',{}),'runner_error':error is not None,
                  'cleanup_verified':json.loads((R/'resource-intents.json').read_text()).get('cleanup_verified',False) if (R/'resource-intents.json').exists() else False,
                  'task_created':bool(task),'execution_recorded':bool(execution)},ensure_ascii=False),flush=True)
