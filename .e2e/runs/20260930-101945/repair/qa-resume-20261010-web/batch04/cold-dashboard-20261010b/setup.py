import datetime,hashlib,json,os,re,subprocess,time,urllib.request
from pathlib import Path
R=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch04/cold-dashboard-20261010b');O='http://127.0.0.1:38181';S='qawebsecure20261010-b04-colddash-38181';CRED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json');SHARED=Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json');AB=['agent-browser','--session',S,'--allowed-domains','127.0.0.1'];SSH=['ssh','-N','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ControlMaster=no','-o','ControlPath=none','-L','127.0.0.1:38181:192.168.100.209:18080','kvm2']
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds')
def save(n,d):(R/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def cli(a,stdin=None,timeout=30):
 p=subprocess.run(AB+[str(x) for x in a],input=stdin,capture_output=True,text=True,timeout=timeout,check=False)
 if p.returncode:raise RuntimeError('agent-browser '+str(a[0])+' failed rc='+str(p.returncode))
 return p.stdout
def ev(js):
 out=cli(['--json','eval','--stdin'],stdin=js);return json.loads(out)['data']['result']
def listeners():
 p=subprocess.run(['ss','-ltnpH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True);a=[]
 for l in p.stdout.splitlines():
  m=re.search(r'\s([^\s]+):38181\s',l);ids=re.findall(r'pid=(\d+)',l);a.append({'loopback':bool(m and m.group(1)=='127.0.0.1'),'owned':bool(T and str(T.pid) in ids)})
 return a
def startticks(pid):
 s=Path(f'/proc/{pid}/stat').read_text();return s[s.rfind(')')+2:].split()[19]
def argv(pid):return Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')[:-1]
T=None;TICKS=None;AH=None;BROWSER=False;DONE=False
try:
 STAGE='preflight';q=subprocess.run(['ssh','-G','kvm2'],capture_output=True,text=True,timeout=15,check=True);cfg={k:[] for k in ('localforward','remoteforward','remotecommand','forwardagent','controlpath')}
 for l in q.stdout.splitlines():
  z=l.split(None,1)
  if len(z)==2 and z[0] in cfg:cfg[z[0]].append(z[1])
 assert not cfg['localforward'] and not cfg['remoteforward'] and all(x=='none' for x in cfg['remotecommand']) and all(x!='yes' for x in cfg['forwardagent']) and not cfg['controlpath']
 assert not subprocess.run(['ss','-ltnH','( sport = :38181 )'],capture_output=True,text=True,timeout=10,check=True).stdout.strip()
 assert CRED.is_file() and SHARED.is_file() and not(CRED.stat().st_mode&0o077)
 h=hashlib.sha256(b'\0'.join(x.encode() for x in SSH)).hexdigest();AH=h;T=subprocess.Popen(SSH,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True);ticks=startticks(T.pid);TICKS=ticks;ready=False
 for _ in range(60):
  if T.poll() is not None:raise RuntimeError('owned SSH exited')
  ls=listeners()
  if len(ls)==1 and ls[0]['loopback'] and ls[0]['owned']:
   try:
    with urllib.request.urlopen(O+'/sign-in',timeout=2) as r:ready=200<=r.status<500
   except Exception:ready=False
  if ready:break
  time.sleep(.2)
 if not ready:raise RuntimeError('owned loopback tunnel readiness failed')
 cli(['open',O+'/sign-in']);BROWSER=True;cli(['wait','--load','networkidle'])
 cr=json.loads(CRED.read_text());sh=json.loads(SHARED.read_text())['token'];payload=json.dumps({'username':cr['admin_username'],'password':cr['admin_password']})
 js="(async(c,s)=>{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});let d={};try{d=await r.json()}catch{};const t=d.token??d.Token;if(typeof t==='string'&&t)localStorage.setItem('nodesteer_token',t);return {http:r.status,token:typeof t==='string'&&!!t,shared:typeof t==='string'&&t===s,origin:location.origin}})("+payload+","+json.dumps(sh)+")"
 login=ev(js)
 if login['http']!=200 or not login['token'] or login['shared'] or login['origin']!=O:raise RuntimeError('dedicated API login guard failed')
 cli(['network','requests','--clear']);cli(['open',O+'/groups'])
 end=time.monotonic()+12;ui={}
 while time.monotonic()<end:
  ui=ev('''(()=>{const m=document.querySelector('main'),txt=m?.innerText||'';return {path:location.pathname,origin:location.origin,empty:/no data|暂无数据/i.test(txt),table:!!m?.querySelector('table'),error:!!m?.querySelector('[role="alert"]'),skeleton:!!m?.querySelector('[aria-busy="true"]')}})()''')
  if ui['path']=='/groups' and (ui['empty'] or ui['table'] or ui['error']):break
  time.sleep(.2)
 api=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token'),h={Authorization:'Bearer '+t},[m,g]=await Promise.all([fetch('/api/me',{headers:h}),fetch('/api/groups',{headers:h})]);let d=[];try{d=await g.json()}catch{};return {me:m.status,groups:g.status,count:Array.isArray(d)?d.length:null}})()''')
 result={'captured_at_utc':now(),'stage':'GROUPS_FIRST_PROTECTED_READY','origin':'http://127.0.0.1:38181','repair_target':'192.168.100.209:18080','session_name':S,'new_browser_session_created':True,'token_values_saved':False,'login_http':login['http'],'token_nonshared':not login['shared'],'groups_ui_state':ui,'api':api,'ssh_pid':T.pid,'ssh_startticks':ticks,'ssh_argv_sha256':h,'listener_owner_verified':True,'listener_count':1,'browser_open':True,'tunnel_started':True}
 save('setup.json',result);p=json.loads((R/'progress.json').read_text());p.update({'phase':'GROUPS_COLD_BASELINE_READY','status':'IN_PROGRESS','updated_at_utc':now(),'setup_evidence':'setup.json','browser_open':True,'tunnel_started':True,'ssh_pid':T.pid,'ssh_startticks':ticks,'ssh_argv_sha256':h});(R/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n');DONE=True;print(json.dumps({'stage':result['stage'],'login_http':login['http'],'groups_http':api['groups'],'groups_count':api['count'],'empty_state':ui['empty'],'browser_open':True,'tunnel_started':True}))
except Exception as e:
 report={'captured_at_utc':now(),'stage':locals().get('STAGE','unknown'),'type':type(e).__name__,'message':str(e)[:120],'private_values_saved':False,'raw_cli_output_saved':False};save('setup-error.json',report);print(json.dumps({'stage':'SETUP_ERROR','type':type(e).__name__,'private_values_saved':False}))
finally:
 if not DONE:
  logout={'had':False,'post':None,'old_me':None,'removed':False}
  if BROWSER:
   try:
    cli(['set','offline','off']);logout=ev('''(async()=>{const t=localStorage.getItem('nodesteer_token');let p=0,m=0;if(t){p=(await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}})).status;m=(await fetch('/api/me',{headers:{Authorization:'Bearer '+t}})).status;localStorage.removeItem('nodesteer_token')}return {had:!!t,post:p,old_me:m,removed:!localStorage.getItem('nodesteer_token')}})()''');cli(['close'])
   except Exception:pass
  if T:
   try:
    guard=T.poll() is None and startticks(T.pid)==TICKS and hashlib.sha256(b'\0'.join(argv(T.pid))).hexdigest()==AH
    if guard:os.kill(T.pid,signal.SIGTERM);T.wait(timeout=8)
    cleanup={'guard_matched':guard,'stopped':T.poll() is not None,'listener_gone':len(listeners())==0}
   except Exception:cleanup={'guard_matched':False,'stopped':False,'listener_gone':False}
  else:cleanup={'guard_matched':False,'stopped':True,'listener_gone':True}
  save('setup-failure-cleanup.json',{'captured_at_utc':now(),'logout_verified':logout.get('had') and logout.get('post')==200 and logout.get('old_me')==401 and logout.get('removed'),'tunnel':cleanup})
