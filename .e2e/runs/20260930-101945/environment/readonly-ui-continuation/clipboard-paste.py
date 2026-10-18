#!/usr/bin/env python3
"""已知安全 Copy 后，可信 Ctrl+V 测量实际后果；辅助 DOM 随后移除。"""
from pathlib import Path

driver = Path(__file__).with_name('run.py')
prefix = driver.read_text().split("try:\n    browser(['set', 'media', 'light'])")[0]
prefix = prefix.replace("SESSION = 'rcon3'", "SESSION = 'rpaste3'").replace("'readonly-ui-profile'", "'readonly-ui-paste-profile'").replace("'readonly-ui-browser.json'", "'readonly-ui-paste-browser.json'").replace("'readonly-ui-secrets.json'", "'readonly-ui-paste-secrets.json'")
exec(compile(prefix, str(driver), 'exec'))
result = json.loads((OUT / 'summary.json').read_text()); serial = 700
proof = {'status':'running','method':'Real Copy Logs UI followed by native trusted Control+v into temporary test-only textarea','original_clipboard_read':False,'clipboard_api_mocked':False,'test_auxiliary_dom_only':True,'product_dom_or_source_permanently_changed':False}
result['clipboard_paste_recheck'] = proof

try:
    with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    h=CFG['hub'];args=['ssh','-F','/dev/null','-T','-N','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','ExitOnForwardFailure=yes','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-p',str(h['ssh_port']),'-i',h['ssh_identity_file'],'-L','127.0.0.1:'+str(port)+':127.0.0.1:8080',h['ssh_target']]
    tunnel_stderr=(PRIVATE/'readonly-ui-paste-tunnel.stderr').open('wb');tunnel=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=tunnel_stderr);local='http://localhost:'+str(port);proof.update({'owned_tunnel_pid':tunnel.pid,'local_port':port,'port_free_checked':True});save()
    for attempt in range(30):
        assert tunnel.poll() is None
        try:
            with urllib.request.urlopen(local+'/healthz',timeout=2) as q:assert q.status==200
            break
        except (urllib.error.URLError,TimeoutError,ConnectionError):time.sleep(.2)
    token=login(local);code,raw=request('/api/executions/'+EXECUTION,token,local);assert code==200;data=json.loads(raw);expected=data['stderr'];assert expected and not data['stdout'] and 'Permission denied' in expected
    browser(['open',local+'/executions/'+EXECUTION]);time.sleep(.4);evaluate(GUARD);browser(['select','select[aria-label="全部流"]','stderr']);cap=evaluate('({secure:window.isSecureContext,displayed:document.querySelector("main pre")?.textContent})');assert cap['secure'] and cap['displayed']==expected
    browser(['find','role','button','click','--name','复制日志']);time.sleep(.2);feedback=evaluate('({copied:document.body.innerText.includes("已复制"),failed:document.body.innerText.includes("复制失败"),manual:document.body.innerText.includes("请按 Ctrl+C 复制")})');assert feedback['copied'];browser(['screenshot',str(OUT/'localhost-copy-trusted-paste.png')])
    evaluate('(()=>{const e=document.createElement("textarea");e.id="qa-readonly-clipboard-measure";e.setAttribute("aria-label","Test-only clipboard measurement");e.style.cssText="position:fixed;right:16px;bottom:16px;width:420px;height:100px;z-index:2147483647;background:white;color:black";e.addEventListener("paste",v=>e.dataset.nativePasteTrusted=String(v.isTrusted));document.body.appendChild(e);return true})()')
    browser(['click','#qa-readonly-clipboard-measure']);browser(['press','Control+v']);time.sleep(.2)
    measured=evaluate('(async()=>{const e=document.querySelector("#qa-readonly-clipboard-measure");const text=e.value;const bytes=new TextEncoder().encode(text);const hash=[...new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))].map(x=>x.toString(16).padStart(2,"0")).join("");const trusted=e.dataset.nativePasteTrusted==="true";e.remove();return {length:text.length,sha256:hash,native_paste_event_trusted:trusted,auxiliary_dom_removed:!document.querySelector("#qa-readonly-clipboard-measure")}})()')
    expected_sha=hashlib.sha256(expected.encode()).hexdigest();proof.update({'status':'PASS' if measured['length']==len(expected) and measured['sha256']==expected_sha and measured['native_paste_event_trusted'] else 'FAIL','secure_context':cap['secure'],'feedback':feedback,'measured':measured,'expected_length':len(expected),'expected_sha256':expected_sha,'matches_exactly':measured['length']==len(expected) and measured['sha256']==expected_sha,'api_paths':[{'path':'/api/executions/'+EXECUTION,'http':code}],'business_guard':evaluate('window.__readonlyProof'),'evidence':['environment/readonly-ui-continuation/localhost-copy-trusted-paste.png','environment/readonly-ui-continuation/summary.json']})
    case=next(c for c in result['cases'] if c['case_id']=='P17P18-10');case['clipboard_subdimension']=proof['status'];case['trusted_paste_recheck']=proof;case['matches_exactly']=proof['matches_exactly'];case['readback_length']=measured['length'];case['readback_sha256']=measured['sha256'];case['readback_method']='Native trusted Ctrl+V measurement, not Clipboard API read';case['covered'].append('Actual post-copy clipboard consequence measured by trusted Ctrl+V into declared temporary test textarea; exact SHA/length match and helper removed');save()
except Exception as e:
    proof['status']='NOT_VERIFIED';proof['error']={'type':type(e).__name__,'message':'Trusted paste measurement failed; private CLI logs retained'}
finally:
    cleanup={'own_session_logout':[],'source_session_not_logged_out':True}
    browser(['--json','eval','--stdin'],'document.querySelector("#qa-readonly-clipboard-measure")?.remove();true',allow_error=True)
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
    source_http,_=request('/api/me',CFG['admin_token']);cleanup['source_session_me_http']=source_http;proof['cleanup']=cleanup;result['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save();print(json.dumps({'trusted_paste':proof['status'],'measured':proof.get('measured'),'matches':proof.get('matches_exactly'),'cleanup':cleanup,'error':proof.get('error')}),flush=True)
