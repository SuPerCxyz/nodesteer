#!/usr/bin/env python3
import os,json,pathlib,subprocess,shlex,urllib.request,urllib.error,ssl,time,tempfile,uuid,hashlib,base64,datetime
os.umask(0o077)
PRIVATE=pathlib.Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x'); OUT=pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/environment'); P=PRIVATE/'tls-full.json'; ISO=PRIVATE/'isolated.json'; PREFIX='qa-20260930-101945-env'; URL='https://192.168.100.209:18444'
SSH=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i','/home/superc/.ssh/id_ed25519','root@192.168.100.209']
c=json.loads(ISO.read_text()); r=c['restore']; state=json.loads(P.read_text()) if P.exists() else {'hub_url':URL,'username':r['username'],'password':r['password'],'registration_token':r['registration_token'],'nodes':[],'requests':[]}; ledger=state.get('ledger',{'apps':[],'artifacts':[],'tasks':[],'transfers':[]}); report={'status':'running','checks':{},'ledger':ledger}
context=ssl.create_default_context(cafile=str(OUT/'tls-ca.pem'))
def save():
 fd,name=tempfile.mkstemp(prefix='tls-full-',dir=PRIVATE)
 with os.fdopen(fd,'w') as f:json.dump(state,f,indent=2)
 os.chmod(name,0o600);os.replace(name,P)
def ssh(code,payload=None,timeout=90):
 result=subprocess.run(SSH+['python3 -c '+shlex.quote(code)],input=json.dumps(payload).encode() if payload is not None else None,capture_output=True,timeout=timeout)
 if result.returncode:
  (PRIVATE/'ssh-tls-full-error.log').write_bytes(result.stdout+result.stderr);raise RuntimeError('TLS full remote step failed; see private error log')
 return json.loads(result.stdout)
def api(method,path,data=None,ct='application/json',binary=False,allow=(200,)):
 headers={'Content-Type':ct}
 if state.get('token'):headers['Authorization']='Bearer '+state['token']
 body=data if isinstance(data,bytes) else json.dumps(data).encode() if data is not None else None
 try:
  with urllib.request.urlopen(urllib.request.Request(URL+path,data=body,headers=headers,method=method),context=context,timeout=20) as response:code=response.status;raw=response.read()
 except urllib.error.HTTPError as error:code=error.code;raw=error.read()
 state['requests'].append({'method':method,'path':path,'status':code})
 if code not in allow:raise RuntimeError('HTTP '+str(code)+' '+method+' '+path)
 return code,raw if binary else json.loads(raw or b'{}')
def node_wait(node_id,timeout=90):
 deadline=time.monotonic()+timeout
 while True:
  node=api('GET','/api/nodes/'+node_id)[1]
  if node['status']=='online' and node['sync_status']=='synced':return node
  if time.monotonic()>deadline:raise RuntimeError('TLS peer online/synced timeout')
  time.sleep(.5)
def run_task(name,command,nodes):
 task=api('POST','/api/tasks',{'name':PREFIX+'-tls-full-'+name,'type':'command','target':{'type':'node','node_ids':nodes},'command':command,'timeout':20,'retry':0,'offline_policy':'hub_online_required','enabled':True})[1];ledger['tasks'].append(task['id'])
 for node_id in nodes:node_wait(node_id)
 executions=api('POST','/api/tasks/'+task['id']+'/run',{})[1]
 for execution in executions:
  deadline=time.monotonic()+35
  while True:
   value=api('GET','/api/executions/'+execution['id'])[1]
   if value['status'] in ['SUCCESS','FAILED','TIMED_OUT','CANCELED','BLOCKED','SKIPPED']:break
   if time.monotonic()>deadline:raise RuntimeError('TLS fixture task timeout')
   time.sleep(.5)
  if value['status']!='SUCCESS':raise RuntimeError('TLS fixture task failed '+value['status'])
 return [x['id'] for x in executions]
save()
setup=state.get('setup') or ssh('''import os,json,pathlib,subprocess,shutil
os.umask(0o077);c=json.load(__import__('sys').stdin);r=c['restore'];root=pathlib.Path(c['remote_root']);cfg=root/'config/restore-hub.yaml';backup=root/'config/restore-hub.yaml.before-tls-full';assert not backup.exists();shutil.copy2(cfg,backup);value=json.loads(cfg.read_text());original={'base_url':value['base_url'],'gateway_base_url':value['gateway_base_url']};value['base_url']='https://192.168.100.209:18444';value['gateway_base_url']=value['base_url'];cfg.write_text(json.dumps(value));subprocess.run(['docker','restart',r['container']],check=True,capture_output=True)
nginx="events {}\\nhttp { log_format evidence '$scheme $request_method $uri $status'; access_log /dev/stdout evidence; error_log /dev/stderr warn; map $http_upgrade $connection_upgrade { default upgrade; '' close; } server { listen 443 ssl; ssl_certificate /etc/nginx/tls/tls.crt; ssl_certificate_key /etc/nginx/tls/tls.key; client_max_body_size 10g; proxy_buffering off; location / { proxy_pass http://"+r['container']+":8080; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection $connection_upgrade; proxy_set_header Host $host; proxy_set_header X-Forwarded-Proto https; proxy_read_timeout 3600s; } } }\\n"
p=root/'config/nginx-tls-full.conf';p.write_text(nginx);image=json.loads(subprocess.check_output(['docker','image','inspect','nginx:1.30.5-alpine']))[0]['RepoDigests'][0];name='qa-20260930-101945-env-tls-full'
a=['docker','run','-d','--name',name,'--label','nodesteer-e2e-run=20260930-101945','--memory','64m','--cpus','0.25','--network',c['network'],'-p','192.168.100.209:18444:443','--mount','type=bind,source='+str(p)+',target=/etc/nginx/nginx.conf,readonly','--mount','type=bind,source='+str(root/'config/tls.crt')+',target=/etc/nginx/tls/tls.crt,readonly','--mount','type=bind,source='+str(root/'config/tls.key')+',target=/etc/nginx/tls/tls.key,readonly',image]
cid=subprocess.check_output(a,text=True).strip();print(json.dumps({'proxy_name':name,'proxy_id':cid,'proxy_image':image,'original_config_backup':str(backup),'original_urls':original,'restore_container':r['container']}))
''',c)
state['setup']=setup;save();report['setup']=setup
try:
 for attempt in range(35):
  try:state['token']=api('POST','/api/login',{'username':state['username'],'password':state['password']})[1]['token'];break
  except Exception:
   if attempt==34:raise
   time.sleep(1)
 for suffix in ['source','peer1','peer2']:
  name=PREFIX+'-tls-full-'+suffix;existing=next((n for n in state['nodes'] if n['name']==name),None);enrollment=existing['enrollment'] if existing else api('POST','/api/nodes/enrollment',{'node_name':name,'node_ip':name+'.example.test','hub_address':URL})[1]
  assert enrollment['gateway_url']=='wss://192.168.100.209:18444' and URL+'/api/agent/binary?architecture=' in enrollment['native']
  item=existing or {'id':enrollment['node_id'],'name':name,'agent_id':enrollment['agent_id'],'ssh_target':'root@192.168.100.209','ssh_host':'192.168.100.209','ssh_port':22,'ssh_user':'root','ssh_identity_file':'/home/superc/.ssh/id_ed25519','docker_container':name,'data_dir':'/var/lib/nodesteer','mode':'docker','arch':'amd64','volume':name+'data','enrollment':enrollment}
  if not existing:state['nodes'].append(item)
  save()
  item['container_id']=item.get('container_id') or ssh('''import json,pathlib,subprocess,os
os.umask(0o077);x=json.load(__import__('sys').stdin);root=pathlib.Path('/root/qa-20260930-101945-env');n=x['node'];env={'NODESTEER_HUB_URL':x['gateway_url'],'NODESTEER_REGISTRATION_TOKEN':x['registration_token'],'NODESTEER_AGENT_ID':n['agent_id'],'NODESTEER_NODE_NAME':n['name'],'NODESTEER_NODE_IP':n['name']+'.example.test','NODESTEER_DEPLOYMENT_MODE':'docker','NODESTEER_TLS_CA_FILE':'/etc/nodesteer/ca.pem','NODESTEER_DATA_DIR':'/var/lib/nodesteer'};p=root/'config'/(n['name']+'.env');p.write_text(''.join(k+'='+v+chr(10) for k,v in env.items()));p.chmod(0o600);subprocess.run(['docker','volume','create','--label','nodesteer-e2e-run=20260930-101945',n['volume']],check=True,capture_output=True)
a=['docker','run','-d','--name',n['name'],'--label','nodesteer-e2e-run=20260930-101945','--memory','96m','--cpus','0.25','--network',x['network'],'--env-file',str(p),'--mount','type=volume,source='+n['volume']+',target=/var/lib/nodesteer','--mount','type=bind,source='+str(root/'config/tls.crt')+',target=/etc/nodesteer/ca.pem,readonly','qa-20260930-101945-env-agent:dddee4f0'];cid=subprocess.check_output(a,text=True).strip();print(json.dumps({'container_id':cid}))
''',{'node':item,'gateway_url':enrollment['gateway_url'],'registration_token':state['registration_token'],'network':c['network']})['container_id']
  node=node_wait(item['id']);assert node['agent_id']==item['agent_id'] and node['hostname']==name;item['executor_argv']=['python3',str(OUT/'guest-exec.py'),'--connection',str(P),'--node',item['id']];save()
 assert len({n['id'] for n in state['nodes']})==3 and len({n['agent_id'] for n in state['nodes']})==3
 report['checks']['enrollment_urls_and_distinct_identities']='passed'
 payload=b'qa-HTTPS-artifact-'+uuid.uuid4().hex.encode();boundary='qa'+uuid.uuid4().hex;parts=[]
 for k,v in {'name':PREFIX+'-tls-full-artifact','version':'1.0.0','architecture':'amd64'}.items():parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+k+'"\r\n\r\n'+v+'\r\n').encode())
 parts.extend([('--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="tls-artifact"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+payload+b'\r\n',('--'+boundary+'--\r\n').encode()]);artifact=api('POST','/api/artifacts',b''.join(parts),ct='multipart/form-data; boundary='+boundary)[1];ledger['artifacts'].append(artifact['id']);body=api('GET','/api/artifacts/'+artifact['id']+'/download',binary=True)[1];assert body==payload and artifact['sha256']==hashlib.sha256(body).hexdigest();report['checks']['https_artifact_upload_download']={'status':'passed','artifact_id':artifact['id'],'sha256':artifact['sha256']}
 app=api('POST','/api/applications',{'name':PREFIX+'-tls-full-prefetch','artifact_id':artifact['id'],'version':'1.0.0','binary_path':'/opt/'+PREFIX+'-tls-full/probe','unit_name':PREFIX+'-tls-full-prefetch.service','arguments':[],'environment':{},'health_check':{'type':'systemd','attempts':1,'timeout':2,'interval':1}})[1];ledger['apps'].append(app['id']);source=state['nodes'][0];targets=state['nodes'][1:];api('POST','/api/applications/'+app['id']+'/assign',{'node_ids':[source['id']]});node_wait(source['id']);api('POST','/api/applications/'+app['id']+'/deploy',{'node_ids':[source['id']],'operation':'deploy'},allow=(200,400))
 time.sleep(2)
 prefetch=ssh('''import json,subprocess,pathlib,sqlite3
x=json.load(__import__('sys').stdin);n=x['node'];lines=subprocess.run(['docker','logs',n['name']],capture_output=True,text=True);selected=[line for line in (lines.stdout+lines.stderr).splitlines() if x['artifact_id'] in line and ('prefetch' in line or 'download' in line)];mount=json.loads(subprocess.check_output(['docker','volume','inspect',n['volume']]))[0]['Mountpoint'];p=pathlib.Path(mount)/'artifacts'/x['sha'];print(json.dumps({'logs':selected[-10:],'cache_file_exists':p.exists(),'temporary_file_exists':pathlib.Path(str(p)+'.tmp').exists()}))
''',{'node':source,'artifact_id':artifact['id'],'sha':artifact['sha256']})
 report['checks']['agent_prefetch']=dict(prefetch,status='failed' if not prefetch['cache_file_exists'] else 'passed',source=['internal/agent/artifact.go:82','internal/agent/util.go:45'],expected='Agent tls_ca_file also permits HTTPS Artifact prefetch')
 data=(b'qa-TLS-relay-real-byte-'+uuid.uuid4().hex.encode()+b'\n')*512;data_sha=hashlib.sha256(data).hexdigest();directory='/var/lib/nodesteer/'+PREFIX+'-tls-full';src=directory+'/source.bin';encoded=base64.b64encode(data).decode();run_task('source-file','mkdir -p '+shlex.quote(directory)+'; printf %s '+shlex.quote(encoded)+' | base64 -d > '+shlex.quote(src)+'; sha256sum '+shlex.quote(src),[source['id']]);run_task('target-dirs','mkdir -p '+shlex.quote(directory),[n['id'] for n in targets])
 code,self_transfer=api('POST','/api/transfers',{'source_node_id':source['id'],'source_path':src,'targets':[{'node_id':source['id'],'destination_path':directory+'/self.bin'}]},allow=(200,400))
 report['checks']['self_target_api_boundary']={'http_status':code,'source_equals_target':True,'interpretation':'Observed API behavior; never used self as multi-target success evidence'}
 if code==200:
  ledger['transfers'].append(self_transfer['id']);time.sleep(1);current=api('GET','/api/transfers/'+self_transfer['id'])[1]
  if current['status'] not in ['SUCCESS','FAILED','CANCELED','PARTIAL']:api('POST','/api/transfers/'+self_transfer['id']+'/cancel',{})
  report['checks']['self_target_api_boundary']['transfer_id']=self_transfer['id']
 destinations=[{'node_id':n['id'],'destination_path':directory+'/target'+str(i)+'.bin'} for i,n in enumerate(targets,1)];transfer=api('POST','/api/transfers',{'source_node_id':source['id'],'source_path':src,'targets':destinations})[1];ledger['transfers'].append(transfer['id']);deadline=time.monotonic()+45
 while True:
  value=api('GET','/api/transfers/'+transfer['id'])[1]
  if value['status'] in ['SUCCESS','FAILED','CANCELED','PARTIAL']:break
  if time.monotonic()>deadline:raise RuntimeError('HTTPS relay terminal timeout')
  time.sleep(.5)
 hashes=[]
 for n,destination in zip(targets,destinations):
  command='sha256sum '+shlex.quote(destination['destination_path'])+'\n';result=subprocess.run(n['executor_argv'],input=command,text=True,capture_output=True,timeout=15);hashes.append({'node_id':n['id'],'ssh_exit_code':result.returncode,'sha256':result.stdout.split()[0] if result.returncode==0 else None})
 report['checks']['https_multi_target_relay']={'status':'passed' if value['status']=='SUCCESS' and all(h['sha256']==data_sha for h in hashes) else 'failed','transfer':value,'source_node_id':source['id'],'target_node_ids':[n['id'] for n in targets],'source_sha256':data_sha,'actual_target_hashes':hashes}
 log=ssh('''import subprocess,json
x=json.load(__import__('sys').stdin);p=subprocess.run(['docker','logs',x['proxy']],text=True,capture_output=True);lines=[line for line in (p.stdout+p.stderr).splitlines() if line.startswith('https ') and ('/agent/transfers/'+x['transfer'] in line or '/api/artifacts/'+x['artifact'] in line or '/api/artifacts ' in line)];print(json.dumps({'lines':lines}))
''',{'proxy':setup['proxy_name'],'transfer':transfer['id'],'artifact':artifact['id']});report['https_payload_access_log']=log['lines'];assert any('POST /agent/transfers/'+transfer['id']+'/upload' in line for line in log['lines']);assert sum('/agent/transfers/'+transfer['id']+'/download' in line for line in log['lines'])>=2
 report['requests']=state['requests'];report['nodes']=[{k:n[k] for k in ['id','name','agent_id','docker_container','volume','container_id']} for n in state['nodes']];report['status']='completed_with_prefetch_failure' if report['checks']['agent_prefetch']['status']=='failed' else 'passed'
except Exception as error:
 report['status']='failed';report['error']={'type':type(error).__name__,'message':str(error)}
finally:
 (OUT/'tls-full-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));state['report_status']=report['status'];state['ledger']=ledger;save();print(json.dumps({'phase':'tls_full','status':report['status'],'check_statuses':{k:v.get('status') if isinstance(v,dict) else v for k,v in report['checks'].items()},'error':report.get('error')}),flush=True)
