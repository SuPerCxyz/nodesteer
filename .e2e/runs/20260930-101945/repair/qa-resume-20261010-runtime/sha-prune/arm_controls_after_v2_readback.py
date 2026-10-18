import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;ROOT='/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';GOOD='fc6fbcc6-39ce-49f4-9368-265a5a42d174';BAD='616a9db6-a87f-4d79-a4de-7d1b016e2986';GSHA='5bd147794c5df9113947cd8d98ffce8e6569188e6f0910862754e193c338a115';BSHA='ae33f3872073eb53d6c08656cd70c8809f7a4e04db8ba5f3f5d3c591736afd64';GREV=3;BREV=2;TASKS={'good':'d43ded1e-f073-461e-b06c-24005789d4c3','bad':'520d7d8d-ccf1-4456-a27f-4fd6b39bf4ce'}
priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=20) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
def coll(x):
 if x is None:return []
 if not isinstance(x,list):raise RuntimeError('unexpected collection shape')
 return x
node=api('GET','/api/nodes/'+NODE);app=api('GET','/api/applications/'+APP);assigned=api('GET','/api/applications/'+APP+'/nodes');scripts={r:api('GET','/api/scripts/'+sid) for r,sid in [('good',GOOD),('bad',BAD)]};tasks=coll(api('GET','/api/tasks'));schedules=coll(api('GET','/api/schedules'));settings=api('GET','/api/settings')
if node.get('status')!='offline' or node.get('id')!=NODE:raise SystemExit('child is not exact offline target')
if assigned not in (None,[]) or app.get('version')!='v1' or app.get('revision')!=7:raise SystemExit('owned App not exact v1/unassigned state')
if settings.get('changelog_window')!='5000':raise SystemExit('global changelog window changed')
for role,entry,rev,sha in [('good',scripts['good'],GREV,GSHA),('bad',scripts['bad'],BREV,BSHA)]:
 if entry.get('id')!=({'good':GOOD,'bad':BAD}[role]) or entry.get('revision')!=rev or entry.get('sha256')!=sha:raise SystemExit('exact Script revision/SHA readback mismatch '+role)
for role,tid in TASKS.items():
 t=next((x for x in tasks if x.get('id')==tid),None)
 if not t or t.get('type')!='script' or t.get('script_id')!=({'good':GOOD,'bad':BAD}[role]) or t.get('target',{}).get('node_ids')!=[NODE] or t.get('retry')!=0 or t.get('timeout')!=60:raise SystemExit('retained Task readback mismatch '+role)
 if any(sc.get('task_id')==tid for sc in schedules):raise SystemExit('retained Task unexpectedly has Schedule')
# Control source embeds only QA IDs/SHA, never credentials or raw Script content.
source=f'''import hashlib,json,os,pathlib,subprocess\nfrom pathlib import Path\nROOT=Path({ROOT!r});UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';APPUNIT='nodesteer-qa-e2e-20261009-p0-002-app-870358cd92ee.service';APP={APP!r};GOOD={GOOD!r};BAD={BAD!r};GSHA={GSHA!r};BSHA={BSHA!r};GREV={GREV};BREV={BREV};NONCE='870358cd92ee';NODE={NODE!r};AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';SRC='5c4b2f94b7353c081c9dac3917addd50a386a816878171b0067ee7ca02288ce0'\ndef run(a):\n p=subprocess.run(a,text=True,capture_output=True,timeout=8);return p.stdout.strip()\ndef show(u):\n s=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup']);return dict(x.split('=',1) for x in s.splitlines() if '=' in x)\ndef sha(p):\n h=hashlib.sha256()\n with open(p,'rb') as f:\n  for z in iter(lambda:f.read(1048576),b''):h.update(z)\n return h.hexdigest()\no=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())\nif o.get('owner_nonce')!=NONCE or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=UNIT or o.get('binary_sha256')!=PS or o.get('source_sha256')!=SRC:raise SystemExit('proxy owner manifest mismatch')\nif c.get('fault_armed') or c.get('release_good_snapshot') or c.get('app_id')!=APP:raise SystemExit('controls state mismatch')\nif show(CHILD).get('LoadState')!='not-found':raise SystemExit('child Unit must be offline/not-found before arming')\nu=show(UNIT);pid=int(u.get('MainPID','0'));q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();cmd=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);proc={{'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in cmd.split(b'\\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}}\nif u.get('ActiveState')!='active' or pid!=50214 or proc['startticks']!=136470092 or uid!=65534 or proc['exe_sha256']!=PS or proc['cgroup']!='0::/system.slice/'+UNIT:raise SystemExit('proxy process fingerprint mismatch')\ncontrols={{'app_id':APP,'good_script_id':GOOD,'good_revision':GREV,'good_sha256':GSHA,'bad_script_id':BAD,'bad_revision':BREV,'bad_sha256':BSHA,'fault_armed':True,'release_good_snapshot':False}}\ntmp=ROOT/'controls.json.tmp'\nwith open(tmp,'x') as f:f.write(json.dumps(controls,separators=(',',':'))+'\\n');f.flush();os.fsync(f.fileno())\nos.chown(tmp,65534,65534);os.chmod(tmp,0o644);os.replace(tmp,ROOT/'controls.json')\nr=json.loads((ROOT/'controls.json').read_text())\nif r!=controls:raise SystemExit('controls readback mismatch')\nprint(json.dumps({{'status':'EXACT_SHA_PRUNE_CONTROLS_ARMED_CHILD_OFFLINE','app_id':APP,'good_script':{{'id':GOOD,'revision':GREV,'sha256':GSHA}},'bad_script':{{'id':BAD,'revision':BREV,'sha256':BSHA}},'fault_armed':True,'release_good_snapshot':False,'child_unit_load_state':'not-found','proxy_pid':pid,'proxy_startticks':proc['startticks'],'controls_owner_uid_gid':f'{{(ROOT/\"controls.json\").stat().st_uid}}:{{(ROOT/\"controls.json\").stat().st_gid}}','controls_mode':oct((ROOT/'controls.json').stat().st_mode&0o7777)}},sort_keys=True),flush=True)'''
command='python3 -c '+shlex.quote(source);name='qa-resume-20261010-arm-sha-prune-controls-870358cd92ee';task=api('POST','/api/tasks',{'name':name,'type':'command','command':command,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=task['id'];e={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'command_sha256':hashlib.sha256(command.encode()).hexdigest(),'root_readback':{'child_status':node.get('status'),'app_version':app.get('version'),'app_revision':app.get('revision'),'assigned_node_ids':assigned,'scripts':{r:{'id':s.get('id'),'revision':s.get('revision'),'sha256':s.get('sha256')} for r,s in scripts.items()},'retained_tasks':[{'id':t.get('id'),'name':t.get('name'),'type':t.get('type'),'script_id':t.get('script_id'),'target':t.get('target'),'retry':t.get('retry'),'timeout':t.get('timeout')} for t in tasks if t.get('id') in TASKS.values()],'schedules_for_retained_tasks':[],'changelog_window':settings.get('changelog_window')},'fault_armed_requested':True}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==e['command_sha256'];xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=xs[0]['id'];end=time.monotonic()+65;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';e.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 e['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));e['task_get_after_status']=404
 except Exception:e['task_get_after_status']='not-404'
(H/'control-arm-evidence.json').write_text(json.dumps(e,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':e.get('status'),'task_deleted_404':e.get('task_get_after_status')==404,'control_result':e.get('stdout_json'),'fault_armed':bool((e.get('stdout_json') or {}).get('fault_armed'))},ensure_ascii=False))
