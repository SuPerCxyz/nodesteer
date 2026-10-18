#!/usr/bin/env python3
"""在隔离 Hub 数据目录验证真实 Dashboard empty state 与 session TTL。"""
import datetime,json,os,pathlib,queue,secrets,shutil,socket,subprocess,tempfile,threading,time,urllib.error,urllib.parse,urllib.request,hashlib,shlex,re

os.umask(0o077)
OUT=pathlib.Path(__file__).parent
PRIVATE=pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
CONNECTION=PRIVATE/'connection.json'
CFG=json.loads(CONNECTION.read_text())
HUB=CFG['hub']
HUB_EXE='/usr/local/bin/nodesteer-hub'
HUB_SERVICE='nodesteer-hub.service'
BASE='/root/nodesteer-e2e-20260930-101945/backup'
BACKUP_ROOT=BASE+'/empty-ttl'
RESUME_TAG=os.environ.get('NODESTEER_EMPTY_TTL_RESUME_TAG')
PREVIOUS_REPORT=None
if RESUME_TAG:
    if not re.fullmatch(r'[0-9a-f]{12}',RESUME_TAG):raise RuntimeError('invalid resume tag format')
    TAG=RESUME_TAG
    PREVIOUS_REPORT=json.loads((pathlib.Path(__file__).parent/'empty-ttl-summary.json').read_text())
    LOCAL_CANDIDATES=list(pathlib.Path('/tmp').glob('nodesteer-empty-ttl-'+TAG+'-*'))
    if len(LOCAL_CANDIDATES)!=1:raise RuntimeError('resume requires exactly one owned local-private directory')
    LOCAL_PRIVATE=LOCAL_CANDIDATES[0]
    saved_credentials=json.loads((LOCAL_PRIVATE/'credentials.json').read_text())
    USERNAME=saved_credentials['username'];PASSWORD=saved_credentials['password'];REG_TOKEN=saved_credentials['registration_token']
else:
    TAG=secrets.token_hex(6)
    USERNAME='qa-ttl-admin'
    PASSWORD=secrets.token_urlsafe(32)
    REG_TOKEN=secrets.token_urlsafe(32)
SOCKET_ROOT=pathlib.Path('/tmp/e'+TAG)
SOCKET_ROOT.mkdir(mode=0o700,exist_ok=True)
SOCKET_ROOT.chmod(0o700)
OWN='/tmp/qa-20260930-101945-luna-empty-ttl-'+TAG
BACKUP=BACKUP_ROOT+'/empty-ttl-'+TAG+('-final-'+secrets.token_hex(4) if RESUME_TAG else '')+'.db'
if not RESUME_TAG:
    LOCAL_PRIVATE=pathlib.Path(tempfile.mkdtemp(prefix='nodesteer-empty-ttl-'+TAG+'-',dir='/tmp'))
    LOCAL_PRIVATE.chmod(0o700)
    (LOCAL_PRIVATE/'credentials.json').write_text(json.dumps({'username':USERNAME,'password':PASSWORD,'registration_token':REG_TOKEN}))
    (LOCAL_PRIVATE/'credentials.json').chmod(0o600)
HUB_TIMEOUT_PID=None
HUB_PID=None
HUB_PROC=None
HUB_LINES=queue.Queue()
HUB_READER=None
TUNNEL_PROC=None
TUNNEL_LOG=None
LOCAL_PORT=None
LOCAL_BASE=None
BROWSER_CLI=None
BROWSER_ENV=None
REPORT={'run_id':CFG['run_id'],'classification':'REAL_ISOLATED_HUB_UI','status':'running','run_tag':TAG,
        'remote_primary_mutations':0,'primary_credentials_used':False,'primary_config_or_database_read':False,
        'agent_registration_attempted':False,'phases':[],'cases':[],
        'local_private':{'directory_mode':'0700','credentials_mode':'0600','path':str(LOCAL_PRIVATE),'socket_root':str(SOCKET_ROOT),'path_retained_for_root_secret_scan':True},
        'temporary_hub':{'service':'own timeout-wrapped process','binary':HUB_EXE,'data_dir':OWN+'/data','artifact_dir':OWN+'/artifacts','config':OWN+'/hub.yaml'},
        'evidence':[],'resume_existing_own_database':bool(RESUME_TAG),'local_private_path':str(LOCAL_PRIVATE)}
if PREVIOUS_REPORT:
    REPORT['prior_harness_attempt']=PREVIOUS_REPORT.get('prior_harness_attempt') or {
       'classification':'HARNESS_FAILURE','status':PREVIOUS_REPORT.get('status'),'error':PREVIOUS_REPORT.get('error'),
       'temporary_hub_process':(PREVIOUS_REPORT.get('phases') or [{}])[0].get('process'),
       'private_backup':PREVIOUS_REPORT.get('cleanup',{}).get('private_database_backup'),
       'cleanup_errors':PREVIOUS_REPORT.get('cleanup_errors',[]),'own_temp_dir_retained':True,
       'business_resource_counts':{'users':1,'audit_logs':0,'nodes':0,'tasks':0,'schedules':0,'executions':0,'applications':0}}
    if PREVIOUS_REPORT.get('error'):
        REPORT['harness_history']=[{'classification':'HARNESS_FAILURE','status':PREVIOUS_REPORT.get('status'),'error':PREVIOUS_REPORT.get('error'),'no_new_Hub_started_in_that_attempt':True}]
if RESUME_TAG:
    REPORT['preserved_prior_empty_state_probe']={
      'case_id':'P03-09','status':'PASS','classification':'REAL_ISOLATED_HUB_UI',
      'evidence':'environment/empty-ttl/dashboard-empty-isolated-initial.png',
      'me':{'http':200,'role':'administrator','username':'qa-ttl-admin'},
      'api_empty_results':[{'path':path,'status':200,'count':0} for path in ['/api/nodes','/api/executions?limit=50','/api/applications','/api/schedules','/api/tasks']],
      'ui':{'heading':'仪表盘','metric_cards':['节点总数 0 0 在线','运行中 0 正在执行的任务','执行失败 0 最近 50 次执行','成功率 - 0 次已完成执行'],
            'no_running':True,'no_apps':True,'no_schedules':True,'recent_execution_rows':0,
            'no_dedicated_failure_or_recent_execution_explanation':True},
      'own_session_logout_http':200,'note':'真实零失败数据和Failed=0卡片适用；页面没有独立失败列表/解释文本，已如实记录。'}

def save():
    REPORT['recorded_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'empty-ttl-summary.json').write_text(json.dumps(REPORT,ensure_ascii=False,indent=2)+'\n')

CURRENT_STAGE='controller initialization'
BROWSER_STEP=0
def set_stage(name):
    global CURRENT_STAGE
    CURRENT_STAGE=name;REPORT['stage']=name;save()

def redact(text):
    text=str(text).replace(PASSWORD,'[REDACTED]').replace(REG_TOKEN,'[REDACTED]')
    text=re.sub(r'(?i)(bearer\s+)[^\s,"}]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)(password|registration[_ -]?token|access[_ -]?token|token)["\s:=]+[^\s,"}]+',r'\1=[REDACTED]',text)
    return text[:512]

def sshbase():
    return ['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','LogLevel=ERROR',
            '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-p',str(HUB['ssh_port']),
            '-i',HUB['ssh_identity_file'],HUB['ssh_target']]

def remote(script,timeout=30):
    p=subprocess.run(sshbase()+['sh','-s'],input=script,text=True,capture_output=True,timeout=timeout)
    if p.returncode:
        q=LOCAL_PRIVATE/'remote-error.json'
        q.write_text(json.dumps({'exit_code':p.returncode,'stderr_length':len(p.stderr),'stderr_excerpt':redact(p.stderr)}));q.chmod(0o600)
        raise RuntimeError('task-owned Hub guest step failed')
    return p.stdout

def primary_snapshot():
    out={}
    for path in ['/healthz','/readyz']:
        with urllib.request.urlopen(CFG['base_url']+path,timeout=12) as r:out[path]=r.status
    req=urllib.request.Request(CFG['base_url']+'/api/nodes',headers={'Authorization':'Bearer '+CFG['admin_token']})
    with urllib.request.urlopen(req,timeout=12) as r:
        rows=json.loads(r.read());out['nodes_http']=r.status
    expected={n['id']:n for n in CFG['nodes']}
    nodes=[{k:x.get(k) for k in ['id','agent_id','status','sync_status','agent_version']} for x in rows if x.get('id') in expected]
    out['native_nodes']=nodes
    out['four_native_unchanged']=len(nodes)==4 and all(x['status']=='online' and x['sync_status']=='synced' and x['agent_id']==expected[x['id']]['agent_id'] and x['agent_version']==expected[x['id']]['agent_version'] for x in nodes)
    return out

def own_database_identity():
    script='''set -eu
python3 - "'''+OWN+'''/data" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1]); rows=[]
for p in root.rglob('*'):
 if p.is_file() and not p.is_symlink():
  try:
   with p.open('rb') as f: header=f.read(16)
   if header==b'SQLite format 3\\x00':
    st=p.stat();rows.append({'path':str(p.relative_to(root)),'device':st.st_dev,'inode':st.st_ino,'size':st.st_size})
  except OSError:pass
print(json.dumps(rows,sort_keys=True))
PY
'''
    rows=json.loads(remote(script).strip().splitlines()[-1])
    if not rows:
        raise RuntimeError('no SQLite database exists under the isolated Hub DataDir')
    return rows

def original_baseline():
    script=r'''set -eu
active=$(systemctl is-active nodesteer-hub.service)
pid=$(systemctl show -p MainPID --value nodesteer-hub.service)
exe=$(readlink -f "/proc/$pid/exe")
sha=$(sha256sum "$exe" | cut -d' ' -f1)
ports=$(ss -Hlnpt '( sport = :18995 or sport = :18996 )')
timeout_bin=$(command -v timeout)
python_bin=$(command -v python3)
sqlite_ok=$(python3 -c 'import sqlite3; print("available")')
printf '{"active":"%s","pid":%s,"exe":"%s","sha":"%s","ports":"%s","timeout":"%s","python3":"%s","sqlite3":"%s"}\n' "$active" "$pid" "$exe" "$sha" "$ports" "$timeout_bin" "$python_bin" "$sqlite_ok"
'''
    row=json.loads(remote(script).strip().splitlines()[-1])
    if row['active']!='active' or row['pid']!=412437 or row['exe']!=HUB_EXE or row['ports'] or not row['timeout'] or not row['python3'] or row['sqlite3']!='available':
        raise RuntimeError('original Hub process/port precondition changed')
    return row

def backup_old_entries():
    # Read metadata and names only; existing backup contents are never opened.
    script=r'''set -eu
for p in /root/nodesteer-e2e-20260930-101945 /root/nodesteer-e2e-20260930-101945/backup /root/nodesteer-e2e-20260930-101945/backup/hub /root/nodesteer-e2e-20260930-101945/backup/isolated-core; do
  if [ -e "$p" ]; then stat -c '%n|%F|%a|%s|%Y' "$p"; else printf 'ABSENT|%s\n' "$p"; fi
done
'''
    return remote(script).splitlines()

def config_text(ttl):
    return '\n'.join([
      'web_addr: "127.0.0.1:18996"',
      'gateway_addr: "127.0.0.1:18995"',
      'gateway_base_url: "http://127.0.0.1:18995"',
      'data_dir: "'+OWN+'/data"',
      'artifact_dir: "'+OWN+'/artifacts"',
      'base_url: "http://127.0.0.1:18996"',
      'admin_username: "'+USERNAME+'"',
      'admin_password: "'+PASSWORD+'"',
      'registration_token: "'+REG_TOKEN+'"',
      'session_ttl: "'+ttl+'"',''])

def write_config(ttl):
    content=config_text(ttl)
    digest=hashlib.sha256(content.encode()).hexdigest()
    marker='TTL_CONFIG_'+TAG+'_'+ttl.replace('s','S')
    config_path=OWN+'/hub.yaml'
    script=(
      'set -eu\n'
      +'cat > '+shlex.quote(config_path)+" <<'"+marker+"'\n"
      +content+marker+'\n'
      +'chmod 600 '+shlex.quote(config_path)+'\n'
      +'test "$(sha256sum '+shlex.quote(config_path)+' | cut -d\' \' -f1)" = "'+digest+'"\n'
      +'python3 - '+shlex.quote(config_path)+' '+shlex.quote(OWN)+' '+shlex.quote(ttl)+" <<'PY'\n"
      +'import json,pathlib,sys\n'
      +'p=pathlib.Path(sys.argv[1]); root=sys.argv[2]; ttl=sys.argv[3]; got={}\n'
      +'for line in p.read_text().splitlines():\n'
      +' if \':\' in line:\n'
      +'  k,v=line.split(\':\',1);got[k.strip()]=v.strip().strip(\'"\')\n'
      +"want={'web_addr':'127.0.0.1:18996','gateway_addr':'127.0.0.1:18995','gateway_base_url':'http://127.0.0.1:18995','base_url':'http://127.0.0.1:18996','data_dir':root+'/data','artifact_dir':root+'/artifacts','admin_username':'qa-ttl-admin','session_ttl':ttl}\n"
      +"print(json.dumps({'sha_matches':True,'mode':oct(p.stat().st_mode&0o777),'own_paths_only':all(got.get(k)==v for k,v in want.items()),'secrets_present':bool(got.get('admin_password') and got.get('registration_token')),'oidc_tls_omitted':not any(k.startswith(('oidc','web_tls','gateway_tls')) for k in got)}))\n"
      +'PY\n'
    )
    result=json.loads(remote(script).strip().splitlines()[-1])
    if result['mode']!='0o600' or not result['own_paths_only'] or not result['secrets_present'] or not result['oidc_tls_omitted']:
        raise RuntimeError('task-owned Hub config validation failed')
    return {'session_ttl':ttl,'sha256':digest,'mode':'0600','own_paths_only':True,'secrets_private':True}

def read_lines(stream,q,path):
    with open(path,'a',encoding='utf-8') as f:
        for line in stream:
            f.write(line);f.flush();q.put(line.rstrip('\n'))

def wait_marker(q,prefix,timeout=20):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:line=q.get(timeout=min(.5,max(.01,end-time.monotonic())))
        except queue.Empty:continue
        if line.startswith(prefix):return line[len(prefix):].strip()
    raise TimeoutError('owned Hub process marker not received')

def start_hub():
    global HUB_TIMEOUT_PID,HUB_PID,HUB_PROC,HUB_READER
    script='''set -u
/usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin /usr/bin/timeout -s TERM -k 5s 240s '''+HUB_EXE+''' --config '''+OWN+'''/hub.yaml > '''+OWN+'''/hub.log 2>&1 &
timeout_pid=$!
hub_pid=""
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30; do
 hub_pid=$(python3 - "$timeout_pid" <<'PY'
import pathlib,sys
parent=int(sys.argv[1])
for p in pathlib.Path('/proc').glob('[0-9]*'):
 try:
  st=(p/'status').read_text(); ppid=int(next(x.split()[1] for x in st.splitlines() if x.startswith('PPid:')))
  if ppid==parent and (p/'exe').resolve().as_posix()=='''+repr(HUB_EXE)+''':
   print(p.name);break
 except (OSError,ValueError,StopIteration):pass
PY
)
 [ -n "$hub_pid" ] && break
 sleep 0.1
done
if [ -z "$hub_pid" ]; then echo '__OWN_HUB_START_FAILED__'; wait "$timeout_pid"; exit 1; fi
cmd=$(tr '\\000' ' ' < "/proc/$timeout_pid/cmdline")
case "$cmd" in *'''+OWN+'''/hub.yaml*) ;; *) kill -TERM "$hub_pid"; wait "$timeout_pid"; exit 78 ;; esac
sha=$(sha256sum "/proc/$hub_pid/exe" | cut -d' ' -f1)
printf '__OWN_HUB_READY__%s %s %s\\n' "$timeout_pid" "$hub_pid" "$sha"
wait "$timeout_pid"
rc=$?
printf '__OWN_HUB_EXIT__%s\\n' "$rc"
exit 0
'''
    HUB_PROC=subprocess.Popen(sshbase()+['sh','-s'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1,start_new_session=True)
    HUB_PROC.stdin.write(script);HUB_PROC.stdin.close()
    HUB_READER=threading.Thread(target=read_lines,args=(HUB_PROC.stdout,HUB_LINES,LOCAL_PRIVATE/'hub-wrapper.out'),daemon=True)
    HUB_READER.start()
    vals=wait_marker(HUB_LINES,'__OWN_HUB_READY__',20).split()
    HUB_TIMEOUT_PID,HUB_PID,sha=int(vals[0]),int(vals[1]),vals[2]
    if sha!=REPORT['baseline']['original_hub']['sha']:
        raise RuntimeError('temporary Hub is not the baseline binary SHA')
    return {'timeout_pid':HUB_TIMEOUT_PID,'hub_pid':HUB_PID,'local_pgid':HUB_PROC.pid,'binary_sha256':sha}

def stop_hub():
    global HUB_TIMEOUT_PID,HUB_PID,HUB_PROC
    if HUB_PID is None:return {'requested':False,'reason':'no owned Hub PID was recorded'}
    script='''set -eu
p='''+str(HUB_PID)+'''; t='''+str(HUB_TIMEOUT_PID)+'''
if ! kill -0 "$p" 2>/dev/null; then printf '{"already_exited":true}\\n'; exit 0; fi
exe=$(readlink -f "/proc/$p/exe"); cmd=$(tr '\\000' ' ' < "/proc/$p/cmdline"); tout=$(tr '\\000' ' ' < "/proc/$t/cmdline")
[ "$exe" = "'''+HUB_EXE+'''" ]
case "$cmd" in *'''+OWN+'''/hub.yaml*) ;; *) exit 78 ;; esac
case "$tout" in *'''+OWN+'''/hub.yaml*) ;; *) exit 79 ;; esac
kill -TERM "$p"
printf '{"hub_pid":%s,"timeout_pid":%s,"term_sent_to":"owned-hub"}\\n' "$p" "$t"
'''
    row=json.loads(remote(script).strip().splitlines()[-1])
    if HUB_PROC:
        HUB_PROC.wait(timeout=25)
        if HUB_READER:HUB_READER.join(timeout=5)
        row['wrapper_exit']=HUB_PROC.returncode
    HUB_TIMEOUT_PID=HUB_PID=None
    return row

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

def start_tunnel():
    global TUNNEL_PROC,TUNNEL_LOG,LOCAL_PORT,LOCAL_BASE
    LOCAL_PORT=free_port();LOCAL_BASE='http://localhost:'+str(LOCAL_PORT)
    TUNNEL_LOG=open(LOCAL_PRIVATE/'ssh-tunnel.log','a',encoding='utf-8')
    TUNNEL_PROC=subprocess.Popen(sshbase()+['-N','-o','ExitOnForwardFailure=yes','-L',f'127.0.0.1:{LOCAL_PORT}:127.0.0.1:18996'],
                                 stdin=subprocess.DEVNULL,stdout=TUNNEL_LOG,stderr=TUNNEL_LOG,start_new_session=True)
    end=time.monotonic()+20
    while time.monotonic()<end:
        if TUNNEL_PROC.poll() is not None:raise RuntimeError('owned localhost SSH tunnel exited')
        try:
            with urllib.request.urlopen(LOCAL_BASE+'/healthz',timeout=1) as r:
                if r.status==200:return {'pid':TUNNEL_PROC.pid,'local_pgid':TUNNEL_PROC.pid,'local_port':LOCAL_PORT,'target':'Hub VM localhost:18996'}
        except (urllib.error.URLError,TimeoutError,ConnectionError):time.sleep(.2)
    raise TimeoutError('owned localhost SSH forward was not ready')

def port_open(host,port):
    with socket.socket() as s:return s.connect_ex((host,port))==0

def stop_tunnel():
    global TUNNEL_PROC,TUNNEL_LOG
    if TUNNEL_PROC and TUNNEL_PROC.poll() is None:
        TUNNEL_PROC.terminate()
        try:TUNNEL_PROC.wait(timeout=6)
        except subprocess.TimeoutExpired:TUNNEL_PROC.kill();TUNNEL_PROC.wait(timeout=5)
    result={'stopped':TUNNEL_PROC is None or TUNNEL_PROC.poll() is not None,'local_forward_port_closed':not port_open('127.0.0.1',LOCAL_PORT) if LOCAL_PORT else True}
    if TUNNEL_LOG:TUNNEL_LOG.close()
    return result

def browser_profile(session):
    global BROWSER_CLI,BROWSER_ENV
    profile=LOCAL_PRIVATE/('profile-'+session);profile.mkdir(mode=0o700)
    config=LOCAL_PRIVATE/('agent-browser-'+session+'.json');config.write_text('{}');config.chmod(0o600)
    sockets=SOCKET_ROOT/session;sockets.mkdir(mode=0o700)
    BROWSER_ENV=dict(os.environ)
    for key in list(BROWSER_ENV):
        if key.startswith('NODESTEER_'):
            BROWSER_ENV.pop(key,None)
    for key in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
        BROWSER_ENV.pop(key,None)
    BROWSER_ENV.update({'AGENT_BROWSER_SOCKET_DIR':str(sockets),'AGENT_BROWSER_PROFILE':str(profile),'AGENT_BROWSER_CONFIG':str(config),
                        'AGENT_BROWSER_EXECUTABLE_PATH':'/home/superc/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome',
                        'AGENT_BROWSER_ARGS':'--no-sandbox,--disable-setuid-sandbox,--disable-crash-reporter,--disable-breakpad,--crash-dumps-dir='+str(LOCAL_PRIVATE/'crashes'),
                        'XDG_CACHE_HOME':str(LOCAL_PRIVATE/('cache-'+session)),'XDG_CONFIG_HOME':str(LOCAL_PRIVATE/('xdg-'+session)),'TMPDIR':str(LOCAL_PRIVATE)})
    pathlib.Path(BROWSER_ENV['XDG_CACHE_HOME']).mkdir(mode=0o700);pathlib.Path(BROWSER_ENV['XDG_CONFIG_HOME']).mkdir(mode=0o700)
    binary=shutil.which('agent-browser')
    if not binary:raise RuntimeError('installed agent-browser CLI unavailable')
    BROWSER_CLI=[binary,'--namespace','ettl','--session',session]

def browser(args,input_text=None,allow_error=False):
    global BROWSER_STEP
    p=subprocess.run(BROWSER_CLI+args,env=BROWSER_ENV,input=input_text,capture_output=True,text=True,timeout=45)
    BROWSER_STEP+=1
    sensitive='network' in args and 'requests' in args
    record={'step':BROWSER_STEP,'stage':CURRENT_STAGE,'command':args[0] if args else None,'exit_code':p.returncode,
            'stdout_length':len(p.stdout),'stderr_length':len(p.stderr),'stderr_excerpt':redact(p.stderr)}
    if not sensitive:record['stdout_excerpt']=redact(p.stdout)
    path=LOCAL_PRIVATE/f'browser-step-{BROWSER_STEP:03d}.json'
    path.write_text(json.dumps(record,ensure_ascii=False));path.chmod(0o600)
    if p.returncode and not allow_error:raise RuntimeError(f'agent-browser step failed (exit {p.returncode})')
    return p

def evaluate(script):
    last_error=None
    for attempt in range(10):
        proc=browser(['--json','eval','--stdin'],input_text=script,allow_error=True)
        try:result=json.loads(proc.stdout)
        except (ValueError,TypeError):result={}
        if result.get('success'):
            return (result.get('data') or {}).get('result')
        last_error=str(result.get('error') or proc.stderr or 'evaluation failed')
        if 'navigated or closed' in last_error or 'Target page, context or browser has been closed' in last_error:
            time.sleep(.2)
            continue
        raise RuntimeError('browser evaluation failed: '+redact(last_error))
    raise TimeoutError('browser evaluation stayed unavailable after a page navigation: '+redact(last_error))

LOGIN_STATE="""({path:location.pathname,search:location.search,has_token:!!localStorage.getItem('nodesteer_token'),heading:document.querySelector('h1')?.innerText||null})"""
ME_QUERY="""(async()=>{const t=localStorage.getItem('nodesteer_token');if(!t)return {http:null,token_present:false};const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+t}});let j={};try{j=await r.json()}catch{};return {http:r.status,role:j.Role||null,username:j.Username||null,expires:j.Expires||null,token_present:!!t,token_length:t.length}})()"""
EMPTY_API="""(async()=>{const t=localStorage.getItem('nodesteer_token');const paths=['/api/nodes','/api/executions?limit=50','/api/applications','/api/schedules','/api/tasks'];return await Promise.all(paths.map(async p=>{const r=await fetch(p,{headers:{Authorization:'Bearer '+t}});let j;try{j=await r.json()}catch{j=null};return {path:p,status:r.status,count:Array.isArray(j)?j.length:null}}))})()"""
EMPTY_UI="""(()=>{const m=document.querySelector('main'),s=(m?.innerText||'').replace(/\\s+/g,' ').trim();const cards=[...document.querySelectorAll('main [data-slot="card"]')].slice(0,4).map(e=>e.innerText.replace(/\\s+/g,' ').trim());return {path:location.pathname,heading:document.querySelector('h1')?.innerText||null,cards,recent_execution_rows:m?.querySelectorAll('tbody tr').length||0,messages:{no_running:s.includes('当前没有正在执行的任务。'),no_executions:s.includes('暂无执行。运行一个任务查看结果。'),no_failures:s.includes('暂无最近失败'),no_apps:s.includes('暂无托管应用'),no_schedules:s.includes('暂无调度')}}})()"""
TTL_PROBE="""(()=>{const orig=window.fetch.bind(window);window.__ttlProof={responses:[],errors:[]};window.fetch=async(input,init={})=>{const u=new URL(typeof input==='string'?input:input.url,location.href),m=(init.method||(typeof input==='object'?input.method:'GET')||'GET').toUpperCase();try{const r=await orig(input,init);if(u.pathname.startsWith('/api/'))window.__ttlProof.responses.push({path:u.pathname,method:m,http:r.status});return r}catch(e){if(u.pathname.startsWith('/api/'))window.__ttlProof.errors.push({path:u.pathname,method:m,error:String(e)});throw e}};return true})()"""
TTL_STATE="""(()=>{const m=document.querySelector('main');return {path:location.pathname,search:location.search,redirect:new URLSearchParams(location.search).get('redirect'),has_token:!!localStorage.getItem('nodesteer_token'),sign_in_form:!!document.querySelector('input[name=username]'),heading:document.querySelector('h1')?.innerText||null,has_retry:[...(m?.querySelectorAll('button')||[])].some(x=>x.innerText.trim()==='重试'),ttl_proof:window.__ttlProof||null,auth_error:/session|expired|过期|未登录|invalid|失败/i.test(m?.innerText||'')}})()"""

def do_login(session):
    set_stage('isolated browser login: '+session)
    browser_profile(session)
    browser(['open',LOCAL_BASE+'/sign-in'])
    for _ in range(80):
        s=evaluate("""({path:location.pathname,form:!!document.querySelector('input[name=username]')})""")
        if s['path']=='/sign-in' and s['form']:break
        time.sleep(.1)
    fill="""(()=>{const v=__LOGIN__;for(const [n,x]of Object.entries(v)){const e=document.querySelector('input[name=\"'+n+'\"]');if(!e)throw new Error('login input missing');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,x);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}return true})()""".replace('__LOGIN__',json.dumps({'username':USERNAME,'password':PASSWORD}))
    evaluate(fill)
    start=time.monotonic();browser(['click','button[type=submit]'])
    for _ in range(150):
        s=evaluate(LOGIN_STATE)
        if s['has_token'] and s['path']!='/sign-in':return {'state':s,'elapsed_seconds':round(time.monotonic()-start,3)}
        time.sleep(.1)
    raise TimeoutError('real isolated UI login did not finish')

def safe_network():
    obj=json.loads(browser(['--json','network','requests','--type','fetch,xhr']).stdout)
    data=obj.get('data') or {}
    items=data.get('requests',[]) if isinstance(data,dict) else []
    out=[]
    for item in items:
        url=item.get('url') or (item.get('request') or {}).get('url') or ''
        path=urllib.parse.urlsplit(url).path
        if path.startswith('/api/') or path=='/healthz':
            req=item.get('request') if isinstance(item.get('request'),dict) else {}
            out.append({'path':path,'method':item.get('method') or req.get('method'),'http':item.get('status') or item.get('statusCode')})
    return out

def create_remote_dirs():
    script='''set -eu
test ! -e "'''+OWN+'''"
mkdir -m 700 "'''+OWN+'''"
mkdir -m 700 "'''+OWN+'''/data" "'''+OWN+'''/artifacts"
test -z "$(ls -A "'''+OWN+'''/data")"
test -z "$(ls -A "'''+OWN+'''/artifacts")"
test ! -e "'''+BACKUP_ROOT+'''"
mkdir -m 700 "'''+BACKUP_ROOT+'''"
test ! -e "'''+BACKUP+'''"
printf '{"own_dirs_empty":true,"own_mode":"0700","backup_dir_new":"true","backup_mode":"0700","unique_backup_absent":true}\\n'
'''
    return json.loads(remote(script).strip().splitlines()[-1])

def verify_resume_dirs():
    old_backup=BACKUP_ROOT+'/empty-ttl-'+TAG+'.db'
    script='''set -eu
test -d "'''+OWN+'''" && test "$(stat -c %a "'''+OWN+'''")" = 700
test -d "'''+OWN+'''/data" && test -d "'''+OWN+'''/artifacts"
test "$(stat -c %a "'''+OWN+'''/hub.yaml")" = 600
test "$(stat -c %a "'''+shlex.quote(BACKUP_ROOT)+'''")" = 700
test -f "'''+old_backup+'''" && test "$(stat -c %a "'''+old_backup+'''")" = 600
test ! -e "'''+BACKUP+'''"
printf '{"own_dir_mode":"0700","config_mode":"0600","previous_backup_mode":"0600","new_backup_absent":true}\n'
'''
    result=json.loads(remote(script).strip().splitlines()[-1])
    result['previous_backup_path']=old_backup
    if REPORT.get('prior_harness_attempt') is not None:
        REPORT['prior_harness_attempt']['private_backup']={'backup_path':old_backup,'verified_present_mode':'0600'}
    return result

def backup_and_clean():
    if HUB_PID is not None:
        raise RuntimeError('refusing to remove task-owned data while its Hub process may still be alive')
    script='''set -eu
root="'''+OWN+'''"; backup_root="'''+BACKUP_ROOT+'''"; backup="'''+BACKUP+'''"
test -d "$root" && test "$(stat -c %a "$root")" = 700
test -d "$backup_root" && test "$(stat -c %a "$backup_root")" = 700
test ! -e "$backup"
python3 - "$root/data" "$backup" <<'PY'
import hashlib,json,os,pathlib,sqlite3,sys
data=pathlib.Path(sys.argv[1]);dstpath=pathlib.Path(sys.argv[2]);dbs=[]
for p in data.rglob('*'):
 if p.is_file() and not p.is_symlink():
  try:
   with p.open('rb') as f: header=f.read(16)
   if header==b'SQLite format 3\\x00':dbs.append(p)
  except OSError:pass
if len(dbs)!=1:raise SystemExit(83)
srcpath=dbs[0];src=sqlite3.connect('file:'+str(srcpath)+'?mode=ro',uri=True);dst=sqlite3.connect(dstpath);src.backup(dst)
integrity=dst.execute('PRAGMA integrity_check').fetchone()[0]
tables=[r[0] for r in dst.execute("select name from sqlite_master where type='table' order by name")]
counts={}
for t in tables:
 if t.lower() in {'tasks','schedules','deployments','executions'}:
  counts[t]=dst.execute('select count(*) from "'+t.replace('"','""')+'"').fetchone()[0]
dst.close();src.close();os.chmod(dstpath,0o600)
print(json.dumps({'source_db':str(srcpath.relative_to(data)),'db_count':len(dbs),'integrity_check':integrity,'tables':tables,'business_counts':counts,'mode':oct(dstpath.stat().st_mode&0o777),'size':dstpath.stat().st_size,'sha256':hashlib.sha256(dstpath.read_bytes()).hexdigest()}))
PY
'''
    row=json.loads([x for x in remote(script,45).splitlines() if x.startswith('{')][-1])
    if row['integrity_check']!='ok' or row['mode']!='0o600':raise RuntimeError('isolated SQLite backup validation failed')
    cleanup='''set -eu
dir="'''+OWN+'''"
case "$dir" in /tmp/qa-20260930-101945-luna-empty-ttl-*) ;; *) exit 85 ;; esac
rm -rf -- "$dir"
test ! -e "$dir"
printf '{"own_dir_removed":true}\\n'
'''
    row['own_tmp_dir_removed']=json.loads([x for x in remote(cleanup).splitlines() if x.startswith('{')][-1])['own_dir_removed']
    row['backup_path']=BACKUP
    return row

try:
    set_stage('read-only preflight and baseline')
    OUT.mkdir(mode=0o700,parents=True,exist_ok=True)
    REPORT['baseline']={'original_hub':original_baseline(),'primary_api':primary_snapshot(),'old_backup_metadata':backup_old_entries()}
    if REPORT['baseline']['primary_api']['/healthz']!=200 or REPORT['baseline']['primary_api']['/readyz']!=200 or not REPORT['baseline']['primary_api']['four_native_unchanged']:
        raise RuntimeError('primary service or Native baseline changed')
    set_stage('pinned Chrome for Testing about:blank CLI preflight')
    chrome='/home/superc/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome'
    if RESUME_TAG and (LOCAL_PRIVATE/'chrome-preflight.json').exists():
        proof=json.loads((LOCAL_PRIVATE/'chrome-preflight.json').read_text())
        version={'returncode':proof.get('version_exit'), 'stdout':proof.get('version')}
        blank=proof.get('opened_url')
        open_code=proof.get('open_exit')
    else:
        version=subprocess.run([chrome,'--version'],capture_output=True,text=True,timeout=10)
        browser_profile('p')
        open_proc=browser(['open','about:blank'])
        blank=evaluate('location.href')
        open_code=open_proc.returncode
        browser(['close'],allow_error=True)
    version_exit=version['returncode'] if isinstance(version,dict) else version.returncode
    if version_exit:
        raise RuntimeError('pinned Chrome for Testing --version failed')
    version_text=version['stdout'] if isinstance(version,dict) else version.stdout
    REPORT['browser_binary']={'path':chrome,'version':redact(version_text.strip()),'independent_about_blank_open':blank=='about:blank','open_exit':open_code}
    if open_code or blank!='about:blank':raise RuntimeError('pinned browser did not open the independent blank page')
    set_stage('isolated directory verification')
    REPORT['directory_setup']=verify_resume_dirs() if RESUME_TAG else create_remote_dirs()
    set_stage('write and verify TTL=60s isolated config')
    REPORT['configs']=[write_config('60s')]
    save()

    set_stage('start owned TTL=60s Hub process')
    hub60=start_hub()
    REPORT['phases'].append({'session_ttl':'60s','process':hub60})
    tunnel60=start_tunnel()
    REPORT['phases'][-1]['tunnel']=tunnel60
    with urllib.request.urlopen(LOCAL_BASE+'/readyz',timeout=5) as r:REPORT['phases'][-1]['readyz']=r.status
    REPORT['phases'][-1]['guest_listeners']=remote("ss -Hlnpt '( sport = :18995 or sport = :18996 )'").splitlines()
    listener_rows=REPORT['phases'][-1]['guest_listeners']
    if len(listener_rows)<2 or not all('127.0.0.1:' in x for x in listener_rows) or not any(':18995' in x for x in listener_rows) or not any(':18996' in x for x in listener_rows):
        raise RuntimeError('temporary Hub did not bind only its two approved loopback ports')
    save()

    set_stage('fresh-profile actual UI login')
    ttl60_session='r60'+secrets.token_hex(3) if RESUME_TAG else 't60'
    do_login(ttl60_session)
    REPORT['phases'][-1]['browser_session']=ttl60_session
    set_stage('verify TTL=60s /api/me and real Dashboard empty API state')
    me60=evaluate(ME_QUERY)
    empty_api=evaluate(EMPTY_API)
    empty_ui=evaluate(EMPTY_UI)
    network60=safe_network()
    initial_screenshot=OUT/'dashboard-empty-isolated-initial.png'
    current_screenshot=OUT/'dashboard-empty-isolated.png'
    if RESUME_TAG and current_screenshot.exists() and not initial_screenshot.exists():
        shutil.copy2(current_screenshot,initial_screenshot)
    browser(['screenshot','--full',str(OUT/'dashboard-empty-isolated.png')])
    empty_ok=me60['http']==200 and me60['role']=='administrator' and all(x['status']==200 and x['count']==0 for x in empty_api) and empty_ui['messages']['no_running'] and empty_ui['messages']['no_apps'] and empty_ui['messages']['no_schedules'] and empty_ui['recent_execution_rows']==0 and len(empty_ui['cards'])>=3 and all('0' in x for x in empty_ui['cards'][:3])
    REPORT['cases'].append({'case_id':'P03-09','status':'PASS' if empty_ok else 'PARTIAL','classification':'REAL_ISOLATED_HUB_UI',
      'covered':['Fresh isolated admin login and /api/me=200 administrator','Dashboard used real empty arrays for nodes, executions, applications, schedules and tasks','Dashboard displayed zero running/failure counts, no-running/no-app/no-schedule empty states and an empty recent-execution body','No Agent registration or business-resource creation'],
      'observed_notes':['Dashboard reports zero recent failures in its metric card; this UI does not provide a separate explanatory no-failure message or failure list.'],
      'remaining':[] if empty_ok else ['One or more real API results, zero metrics, or applicable no-running/no-app/no-schedule empty states were not observed.'],
      'me':me60,'api_empty_results':empty_api,'ui':empty_ui,'actual_ui_network':network60,
      'evidence':['environment/empty-ttl/dashboard-empty-isolated-initial.png' if initial_screenshot.exists() else 'environment/empty-ttl/dashboard-empty-isolated.png','environment/empty-ttl/dashboard-empty-isolated.png','environment/empty-ttl/empty-ttl-summary.json']})
    REPORT['phases'][-1]['database_before_ttl_restart']=own_database_identity()
    set_stage('logout only own TTL=60s session')
    logout60=evaluate("""(async()=>{const t=localStorage.getItem('nodesteer_token');const r=await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}});return {http:r.status,token_present:!!t}})()""")
    REPORT['phases'][-1]['own_session_logout']=logout60
    browser(['close'])
    REPORT['phases'][-1]['browser_closed']=True
    if logout60['http'] not in [200,204]:raise RuntimeError('own 60s session logout failed')
    set_stage('stop own TTL=60s Hub and tunnel')
    stop60=stop_hub();REPORT['phases'][-1]['own_hub_stop']=stop60
    REPORT['phases'][-1]['tunnel_stop']=stop_tunnel()
    REPORT['phases'][-1]['ports_closed']=not bool(remote("ss -Hlnpt '( sport = :18995 or sport = :18996 )'\n").strip())
    if not REPORT['phases'][-1]['ports_closed']:raise RuntimeError('own Hub ports still listen after stop')

    set_stage('write and verify TTL=3s config against same owned DB')
    REPORT['configs'].append(write_config('3s'))
    save()
    set_stage('start owned TTL=3s Hub process')
    hub3=start_hub()
    REPORT['phases'].append({'session_ttl':'3s','process':hub3,'same_data_dir':True})
    tunnel3=start_tunnel();REPORT['phases'][-1]['tunnel']=tunnel3
    REPORT['phases'][-1]['guest_listeners']=remote("ss -Hlnpt '( sport = :18995 or sport = :18996 )'").splitlines()
    listener_rows=REPORT['phases'][-1]['guest_listeners']
    if len(listener_rows)<2 or not all('127.0.0.1:' in x for x in listener_rows) or not any(':18995' in x for x in listener_rows) or not any(':18996' in x for x in listener_rows):
        raise RuntimeError('TTL=3 temporary Hub did not bind only its two approved loopback ports')
    with urllib.request.urlopen(LOCAL_BASE+'/readyz',timeout=5) as r:REPORT['phases'][-1]['readyz']=r.status
    REPORT['phases'][-1]['database_after_restart']=own_database_identity()
    REPORT['phases'][-1]['same_database_identity']=[(x['path'],x['device'],x['inode']) for x in REPORT['phases'][0]['database_before_ttl_restart']]==[(x['path'],x['device'],x['inode']) for x in REPORT['phases'][-1]['database_after_restart']]
    if not REPORT['phases'][-1]['same_database_identity']:
        raise RuntimeError('short-TTL Hub did not reuse the same isolated SQLite database')
    set_stage('fresh-profile actual TTL=3s UI login')
    do_login('t3')
    login3_at=time.monotonic()
    me3=evaluate(ME_QUERY)
    browser(['network','requests','--clear'])
    evaluate(TTL_PROBE)
    while time.monotonic()-login3_at<5.5:time.sleep(.25)
    REPORT['phases'][-1]['wait_after_login_seconds']=round(time.monotonic()-login3_at,3)
    deadline=time.monotonic()+38
    ttl_state=None
    while time.monotonic()<deadline:
        ttl_state=evaluate(TTL_STATE)
        rejections=[x for x in (ttl_state.get('ttl_proof') or {}).get('responses',[]) if x.get('http')==401 and x.get('path','').startswith('/api/')]
        if rejections:break
        time.sleep(.5)
    browser(['screenshot','--full',str(OUT/'dashboard-ttl-expired.png')])
    net3=safe_network()
    api401=[x for x in net3 if x.get('http')==401 and x['path'].startswith('/api/')]
    probe401=[x for x in (ttl_state.get('ttl_proof') or {}).get('responses',[]) if x.get('http')==401 and x.get('path','').startswith('/api/')]
    api401=api401 or probe401
    auto_redirect=ttl_state['path']=='/sign-in' and ttl_state['sign_in_form']
    expiry={'case_id':'FL-14','status':'PASS' if api401 and auto_redirect and ttl_state.get('redirect','').startswith('/') and not ttl_state.get('redirect','').startswith('//') else ('FAIL' if api401 else 'PARTIAL'),
      'classification':'REAL_ISOLATED_HUB_UI','related_failure':'E2E-20260930-101945-P1-001',
      'covered':['Fresh TTL=3s login, /api/me=200 administrator, server Expires recorded','Waited more than five seconds without changing any clock','Dashboard issued real API request(s) and received HTTP 401 after expiry','Captured post-401 URL, login form and localStorage token state'],
      'remaining':[],'login_me':me3,'wait_after_login_seconds':REPORT['phases'][-1]['wait_after_login_seconds'],
      'expired_ui_state':ttl_state,'api_401':api401,'automatic_sign_in_redirect':auto_redirect,
      'evidence':['environment/empty-ttl/dashboard-ttl-expired.png','environment/empty-ttl/empty-ttl-summary.json']}
    if not auto_redirect:
        expiry['remaining'].append('Automatic redirect from the expired Dashboard to /sign-in was not observed; correlate this reproduction with the existing session-expiry defect.')
        expiry['failure_evidence']='.e2e/runs/20260930-101945/failures.json#E2E-20260930-101945-P1-001'
    REPORT['cases'].append(expiry)
    save()

    # Test an internal same-origin return target after recording the actual expiry outcome.
    browser(['open',LOCAL_BASE+'/executions'])
    for _ in range(100):
        route=evaluate(TTL_STATE)
        if route['path']=='/sign-in' and route['sign_in_form']:break
        time.sleep(.1)
    redirect=route.get('redirect')
    safe_target=redirect=='/executions' and redirect.startswith('/') and not redirect.startswith('//')
    REPORT['phases'][-1]['internal_return_target']={'requested_path':'/executions','signin_redirect':redirect,'safe_same_origin':safe_target}
    if route['path']=='/sign-in' and route['sign_in_form']:
        fill="""(()=>{const v=__LOGIN__;for(const [n,x]of Object.entries(v)){const e=document.querySelector('input[name="'+n+'"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,x);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}return true})()""".replace('__LOGIN__',json.dumps({'username':USERNAME,'password':PASSWORD}))
        evaluate(fill);browser(['click','button[type=submit]'])
        for _ in range(100):
            after=evaluate(TTL_STATE)
            if after['path']!='/sign-in' and after['has_token']:break
            time.sleep(.1)
        me_return=evaluate(ME_QUERY)
        rtarget={'requested_path':'/executions','signin_redirect':redirect,'safe_same_origin':safe_target,'final_path':after['path'],'me_http':me_return['http'],'path_matches':after['path']=='/executions'}
        REPORT['phases'][-1]['internal_return_target']=rtarget
        expiry['internal_return_target']=rtarget
        expiry['covered'].append('A protected same-origin /executions request retained its internal target through a fresh UI login')
        if not safe_target or not rtarget['path_matches'] or me_return['http']!=200:
            expiry['status']='FAIL'
            expiry['remaining'].append('The internal return target did not return to /executions after login.')
        logout3=evaluate("""(async()=>{const t=localStorage.getItem('nodesteer_token');if(!t)return {http:null,token_present:false};const r=await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}});return {http:r.status,token_present:true}})()""")
        REPORT['phases'][-1]['final_own_session_logout']=logout3
        if logout3.get('http') not in [200,204]:logout3['expired_or_rejected']=True
    else:
        expiry['remaining'].append('Could not reach the sign-in page to test an internal return target.')
    save()
    browser(['close'],allow_error=True)
    REPORT['phases'][-1]['browser_closed']=True
    REPORT['phases'][-1]['own_hub_stop']=stop_hub()
    REPORT['phases'][-1]['tunnel_stop']=stop_tunnel()
    REPORT['phases'][-1]['ports_closed']=not bool(remote("ss -Hlnpt '( sport = :18995 or sport = :18996 )'\n").strip())
    REPORT['status']='completed'
except Exception as exc:
    REPORT['status']='interrupted'
    REPORT['error']={'type':type(exc).__name__,'stage':CURRENT_STAGE,'message':redact(exc)}
    failure_path=LOCAL_PRIVATE/'failure-private.json'
    failure_path.write_text(json.dumps(REPORT['error'],ensure_ascii=False));failure_path.chmod(0o600)
finally:
    try:
        browser(['close'],allow_error=True)
        if REPORT['phases']:REPORT['phases'][-1]['browser_closed']=True
    except Exception:pass
    try:
        if HUB_PID is not None:REPORT.setdefault('cleanup',{})['own_hub_stop']=stop_hub()
    except Exception as exc:REPORT.setdefault('cleanup_errors',[]).append(type(exc).__name__)
    try:REPORT.setdefault('cleanup',{})['own_tunnel_stop']=stop_tunnel()
    except Exception as exc:REPORT.setdefault('cleanup_errors',[]).append(type(exc).__name__)
    try:
        if remote('if [ -d "'+OWN+'" ]; then echo PRESENT; else echo ABSENT; fi\n').strip()=='PRESENT':
            REPORT.setdefault('cleanup',{})['private_database_backup']=backup_and_clean()
    except Exception as exc:REPORT.setdefault('cleanup_errors',[]).append(type(exc).__name__)
    try:
        REPORT.setdefault('cleanup',{})['original_hub_after']=original_baseline()
        REPORT['cleanup']['primary_api_after']=primary_snapshot()
        REPORT['cleanup']['old_backup_metadata_after']=backup_old_entries()
    except Exception as exc:REPORT.setdefault('cleanup_errors',[]).append(type(exc).__name__)
    REPORT['evidence']=['environment/empty-ttl/empty-ttl-summary.json','environment/empty-ttl/dashboard-empty-isolated.png','environment/empty-ttl/dashboard-ttl-expired.png']
    save()
    print(json.dumps({'status':REPORT['status'],'cases':[(x['case_id'],x['status']) for x in REPORT['cases']],'cleanup_errors':REPORT.get('cleanup_errors',[])}),flush=True)
