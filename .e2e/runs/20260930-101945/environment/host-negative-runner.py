#!/usr/bin/env python3
"""本轮独占 Host 负向验证；凭据只留任务私密目录。"""
import base64, hashlib, json, os, pathlib, shlex, subprocess, time, urllib.error, urllib.request, uuid

os.umask(0o077)
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
OUT = pathlib.Path(__file__).parent
PREFIX = 'qa-20260930-101945-env'
ROOT = '/opt/' + PREFIX + '-hostneg'
WRONG = ROOT + '-wrong'
UNIT = PREFIX + '-hostneg.service'
STATE = PRIVATE / 'host-negative.json'
iso = json.loads((PRIVATE / 'isolated.json').read_text())
restore = iso['restore']
URL = restore['hub_url']
state = json.loads(STATE.read_text()) if STATE.exists() else {'nodes': [], 'requests': [], 'cases': [], 'containers': [], 'tasks': [], 'apps': [], 'artifacts': []}
report = {'run_id': '20260930-101945', 'status': 'running', 'authority': URL, 'cases': state['cases'], 'protected_services': {}, 'source': ['internal/agent/host/container.go:49', 'internal/agent/host/container.go:191', 'internal/agent/host/container.go:239', 'internal/agent/agent.go:1290']}
SSH = ['ssh', '-F', '/dev/null', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'LogLevel=ERROR', '-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-i', '/home/superc/.ssh/id_ed25519']

def save():
    STATE.write_text(json.dumps(state, indent=2)); STATE.chmod(0o600)
    (OUT / 'host-negative-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))

def remote(ip, code, data=None):
    p = subprocess.run(SSH + ['root@' + ip, 'python3 -c ' + shlex.quote(code)], input=json.dumps(data).encode() if data is not None else None, capture_output=True, timeout=90)
    if p.returncode:
        (PRIVATE / 'host-negative-remote-error.log').write_bytes(p.stdout + p.stderr)
        raise RuntimeError('owned remote step failed; private error log retained')
    return json.loads(p.stdout)

def api(method, path, data=None, ct='application/json', allowed=(200,)):
    body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
    headers = {'Content-Type': ct}
    if state.get('token'): headers['Authorization'] = 'Bearer ' + state['token']
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, body, headers, method=method), timeout=20) as r: code, raw = r.status, r.read()
    except urllib.error.HTTPError as e: code, raw = e.code, e.read()
    state['requests'].append({'method': method, 'path': path, 'status': code})
    if code not in allowed: raise RuntimeError('HTTP ' + str(code) + ' ' + method + ' ' + path)
    return code, json.loads(raw or b'{}')

def wait_node(n):
    deadline = time.monotonic() + 100
    while True:
        x = api('GET', '/api/nodes/' + n['id'])[1]
        if x['status'] == 'online' and x['sync_status'] == 'synced':
            assert x['agent_id'] == n['agent_id'] and x['hostname'] == n['name']
            return {k: x[k] for k in ['id', 'agent_id', 'hostname', 'status', 'sync_status', 'deployment_mode', 'capabilities']}
        if time.monotonic() > deadline: raise RuntimeError('owned Host negative node online/synced timeout')
        time.sleep(.6)

def enroll(suffix, host):
    name = PREFIX + '-hostneg-' + suffix
    n = next((n for n in state['nodes'] if n['name'] == name), None)
    if n: return n
    e = api('POST', '/api/nodes/enrollment', {'node_name': name, 'node_ip': name + '.example.test', 'hub_address': URL})[1]
    n = {'id': e['node_id'], 'agent_id': e['agent_id'], 'name': name, 'volume': name + 'data', 'host': host, 'enrollment': e}
    state['nodes'].append(n); save(); return n

def start(n, variant):
    result = remote(n['host'], '''import json,pathlib,subprocess,os
os.umask(0o077);x=json.load(__import__('sys').stdin);n=x['node'];root=pathlib.Path('/root/qa-20260930-101945-env');cfg=root/'config'/(n['name']+'.yaml');cfg.parent.mkdir(parents=True,exist_ok=True)
value={'hub_url':n['enrollment']['gateway_url'],'registration_token':x['registration_token'],'agent_id':n['agent_id'],'node_name':n['name'],'node_ip':n['name']+'.example.test','data_dir':'/var/lib/nodesteer','deployment_mode':'docker' if x['variant']=='source' else 'docker_host_integration','host_root':'/host','host_path_allowlist':[] if x['variant']=='source' else [x['logical_root'],'/etc/systemd/system/'+x['unit']]};cfg.write_text(json.dumps(value));cfg.chmod(0o600)
old=subprocess.run(['docker','inspect',n['name']],capture_output=True,text=True)
if old.returncode==0:
 d=json.loads(old.stdout)[0];assert d['Config']['Labels'].get('nodesteer-e2e-run')=='20260930-101945';subprocess.run(['docker','rm','-f',d['Id']],check=True,capture_output=True)
subprocess.run(['docker','volume','create','--label','nodesteer-e2e-run=20260930-101945',n['volume']],check=True,capture_output=True)
image=json.loads(subprocess.check_output(['docker','image','inspect','qa-20260930-101945-env-agent:dddee4f0']))[0]['Id'];a=['docker','run','-d','--name',n['name'],'--label','nodesteer-e2e-run=20260930-101945','--memory','96m','--cpus','0.25','--cap-drop','ALL','--mount','type=volume,source='+n['volume']+',target=/var/lib/nodesteer','--mount','type=bind,source='+str(cfg)+',target=/etc/nodesteer/agent.yaml,readonly']
if x['variant']=='source':a+=['--network',x['network']]
else:
 a+=['--network','host','--pid','host','--mount','type=bind,source=/proc,target=/host/proc,readonly','--mount','type=bind,source=/sys,target=/host/sys,readonly','--mount','type=bind,source=/etc/os-release,target=/host/etc/os-release,readonly']
 if x['variant']!='no-cap':
  for cap in ['SYS_ADMIN','SYS_CHROOT','SYS_PTRACE','DAC_OVERRIDE']:a+=['--cap-add',cap]
 if x['variant']!='missing':
  src=x['wrong_root'] if x['variant']=='wrong' else x['logical_root'];mount='type=bind,source='+src+',target=/host'+x['logical_root'];a+=['--mount',mount+(',readonly' if x['variant']=='readonly' else '')]
 if x['variant']=='no-cap':a+=['--mount','type=bind,source=/etc/systemd/system,target=/host/etc/systemd/system']
a += [image,'--config','/etc/nodesteer/agent.yaml'];cid=subprocess.check_output(a,text=True).strip();d=json.loads(subprocess.check_output(['docker','inspect',cid]))[0];print(json.dumps({'id':cid,'name':n['name'],'variant':x['variant'],'image':image,'privileged':d['HostConfig']['Privileged'],'cap_add':d['HostConfig']['CapAdd'],'cap_drop':d['HostConfig']['CapDrop'],'mounts':[{'source':m['Source'],'destination':m['Destination'],'rw':m['RW']} for m in d['Mounts']]}))
''', {'node': n, 'variant': variant, 'registration_token': restore['registration_token'], 'logical_root': ROOT, 'wrong_root': WRONG, 'unit': UNIT, 'network': iso['network']})
    state['containers'].append(result); save(); time.sleep(.7); return wait_node(n)

def snapshot(destination, n):
    return remote('192.168.100.247', '''import json,pathlib,hashlib,subprocess
x=json.load(__import__('sys').stdin);paths=x['paths'];out={}
for label,p in paths.items():
 q=pathlib.Path(p);out[label]={'exists':q.exists(),'sha256':hashlib.sha256(q.read_bytes()).hexdigest() if q.is_file() else None}
inside=subprocess.run(['docker','exec',x['container'],'sha256sum','/host'+x['destination']],capture_output=True,text=True);out['container_mapped']={'exit_code':inside.returncode,'sha256':inside.stdout.split()[0] if inside.returncode==0 else None};out['native_agent']={'active':subprocess.run(['systemctl','is-active','nodesteer-agent.service'],capture_output=True,text=True).stdout.strip(),'pid':subprocess.run(['systemctl','show','-p','MainPID','--value','nodesteer-agent.service'],capture_output=True,text=True).stdout.strip()};print(json.dumps(out))
''', {'paths': {'host_destination': destination, 'host_sentinel': ROOT + '/sentinel', 'wrong_sentinel': WRONG + '/sentinel', 'unit': '/etc/systemd/system/' + UNIT}, 'destination': destination, 'container': n['name']})

def transfer(case, source, target, destination, expected):
    before = snapshot(destination, target)
    code, value = api('POST', '/api/transfers', {'source_node_id': source['id'], 'source_path': '/var/lib/nodesteer/host-negative-source.bin', 'targets': [{'node_id': target['id'], 'destination_path': destination}]}, allowed=(200,400))
    if code == 200:
        deadline = time.monotonic() + 40
        while True:
            value = api('GET', '/api/transfers/' + value['id'])[1]
            if value['status'] in ['SUCCESS','FAILED','CANCELED','PARTIAL']: break
            if time.monotonic() > deadline: raise RuntimeError('Host negative transfer terminal timeout')
            time.sleep(.5)
    item={'id':case,'expected':expected,'http_status':code,'transfer':value,'before':before,'after':snapshot(destination,target)}
    state['cases'].append(item); save(); print(json.dumps({'case':case,'http_status':code,'status':value.get('status'),'error':value.get('error'),'host_exists':item['after']['host_destination']['exists']}),flush=True)
    return item

try:
    state['token'] = api('POST','/api/login',{'username':restore['username'],'password':restore['password']})[1]['token'];save()
    if not state.get('baseline'):
        state['baseline']=remote('192.168.100.247','''import pathlib,subprocess,json,hashlib
x=json.load(__import__('sys').stdin)
for p in [x['root'],x['wrong'],'/etc/systemd/system/'+x['unit']]:assert not pathlib.Path(p).exists(),p
for p in [x['root'],x['wrong']]:pathlib.Path(p).mkdir(mode=0o700);(pathlib.Path(p)/'sentinel').write_bytes(b'host-negative-original-sentinel\\n')
print(json.dumps({'created_paths':[x['root'],x['wrong']],'unit_initially_absent':True,'native_active':subprocess.run(['systemctl','is-active','nodesteer-agent.service'],capture_output=True,text=True).stdout.strip(),'native_pid':subprocess.run(['systemctl','show','-p','MainPID','--value','nodesteer-agent.service'],capture_output=True,text=True).stdout.strip()}))''',{'root':ROOT,'wrong':WRONG,'unit':UNIT});save()
    report['baseline']=state['baseline'];source=enroll('source','192.168.100.209');target=enroll('target','192.168.100.247')
    if not state.get('source_online'):
        state['source_online']=start(source,'source');data=b'actual-host-negative-relay-'+uuid.uuid4().hex.encode();state['payload_sha256']=hashlib.sha256(data).hexdigest();remote(source['host'],'''import json,subprocess,base64
x=json.load(__import__('sys').stdin);subprocess.run(['docker','exec','-i',x['container'],'sh','-c','cat > /var/lib/nodesteer/host-negative-source.bin'],input=base64.b64decode(x['data']),check=True,capture_output=True);print(json.dumps({'fixture_created':True}))''',{'container':source['name'],'data':base64.b64encode(data).decode()});save()
    for variant in ['missing','readonly','wrong','valid']:
        if any(c['id']=='ENV-HOSTNEG-'+variant.upper() for c in state['cases']):continue
        node=start(target,variant);report.setdefault('node_variants',[]).append({'variant':variant,'node':node})
        destination=ROOT+'/sentinel' if variant=='readonly' else ROOT+'/'+variant+'.bin'
        transfer('ENV-HOSTNEG-'+variant.upper(),source,target,destination,'Reject unsafe configuration or write to real owned host path; readonly must reject')
        if variant=='valid':
            transfer('ENV-HOSTNEG-PATH-TRAVERSAL',source,target,ROOT+'/../'+pathlib.Path(WRONG).name+'/traversal.bin','Reject path traversal')
            transfer('ENV-HOSTNEG-ALLOWLIST',source,target,WRONG+'/allowlist.bin','Reject outside allowlist')
            remote(target['host'],'''import json,subprocess
x=json.load(__import__('sys').stdin);subprocess.run(['docker','exec',x['container'],'sh','-c','mkdir -p /tmp/qa-hostneg-outside; printf unchanged > /tmp/qa-hostneg-outside/sentinel; ln -s /tmp/qa-hostneg-outside '+__import__('shlex').quote('/host'+x['root']+'/escape-link')],check=True,capture_output=True);print(json.dumps({'owned_symlink_created':True}))''',{'container':target['name'],'root':ROOT})
            transfer('ENV-HOSTNEG-SYMLINK',source,target,ROOT+'/escape-link/sentinel','Reject resolved path outside hostRoot')
            report['symlink_container_sentinel']=remote(target['host'],'''import json,subprocess
x=json.load(__import__('sys').stdin);p=subprocess.run(['docker','exec',x['container'],'cat','/tmp/qa-hostneg-outside/sentinel'],capture_output=True,text=True);print(json.dumps({'exit_code':p.returncode,'unchanged':p.stdout=='unchanged'}))''',{'container':target['name']})
    if not any(c['id']=='ENV-HOSTNEG-CAPABILITY' for c in state['cases']):
        node=start(target,'no-cap');report.setdefault('node_variants',[]).append({'variant':'no-cap','node':node})
        actual=remote(target['host'],'''import json,subprocess,base64
x=json.load(__import__('sys').stdin);p=subprocess.run(['docker','exec',x['container'],'nsenter','-t','1','-m','-u','-i','-n','-p','--root=/proc/1/root','systemctl','is-active','nodesteer-agent.service'],capture_output=True,text=True);print(json.dumps({'exit_code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip(),'artifact':base64.b64encode(open('/bin/sh','rb').read()).decode()}))''',{'container':target['name']})
        binary=base64.b64decode(actual.pop('artifact'));boundary='qa'+uuid.uuid4().hex;fields={'name':PREFIX+'-hostneg-bin','version':'1.0.0','architecture':'amd64'};parts=[('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+v+'\r\n').encode() for k,v in fields.items()];parts.extend([('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="hostneg-bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+binary+b'\r\n',('--'+boundary+'--\r\n').encode()]);artifact=api('POST','/api/artifacts',b''.join(parts),ct='multipart/form-data; boundary='+boundary)[1];state['artifacts'].append(artifact['id']);save()
        app=api('POST','/api/applications',{'name':PREFIX+'-hostneg-nocap','artifact_id':artifact['id'],'version':'1.0.0','binary_path':ROOT+'/nocap-bin','config_path':ROOT+'/nocap.conf','unit_name':UNIT,'arguments':[],'environment':{},'health_check':{'type':'systemd','attempts':1,'timeout':2,'interval':1}})[1];state['apps'].append(app['id']);save();api('POST','/api/applications/'+app['id']+'/assign',{'node_ids':[target['id']]});wait_node(target);code,value=api('POST','/api/applications/'+app['id']+'/deploy',{'node_ids':[target['id']],'operation':'deploy'},allowed=(200,400));executions=value if isinstance(value,list) else []
        for e in executions:
            deadline=time.monotonic()+35
            while True:
                e=api('GET','/api/executions/'+e['id'])[1]
                if e['status'] in ['SUCCESS','FAILED','TIMED_OUT','BLOCKED','CANCELED']:break
                if time.monotonic()>deadline:raise RuntimeError('nocap application terminal timeout')
                time.sleep(.5)
            state['cases'].append({'id':'ENV-HOSTNEG-CAPABILITY','expected':'Insufficient kernel capabilities prevent host systemctl; no privileged fallback','nsenter_readonly_actual':actual,'http_status':code,'execution':e,'node_advertised_capabilities':node['capabilities'],'after':snapshot(ROOT+'/nocap-bin',target),'application_id':app['id'],'artifact_id':artifact['id']});save()
    report['nodes']=[{k:n[k] for k in ['id','agent_id','name','volume','host']} for n in state['nodes']];report['payload_sha256']=state['payload_sha256'];report['containers']=state['containers'];report['status']='completed_pending_cleanup';save();print(json.dumps({'phase':'host-negative','status':report['status'],'cases':len(state['cases'])}),flush=True)
except Exception as e:
    report['status']='interrupted';report['error']={'type':type(e).__name__,'message':str(e)};save();print(json.dumps({'phase':'host-negative','status':report['status'],'error':report['error']}),flush=True)
