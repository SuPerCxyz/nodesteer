import hashlib,json,shlex,time
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong endpoint')
op=build_opener(ProxyHandler({}))
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=15) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
src=r'''import hashlib,json,os,pathlib,subprocess,time
from pathlib import Path
P=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';CHILD='nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service';PS='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';AS='d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';APP='2e4fecee-afc8-43a8-9d93-dd36cfc10bf9';GOOD='fc6fbcc6-39ce-49f4-9368-265a5a42d174';BAD='616a9db6-a87f-4d79-a4de-7d1b016e2986';GSHA='5bd147794c5df9113947cd8d98ffce8e6569188e6f0910862754e193c338a115';BSHA='ae33f3872073eb53d6c08656cd70c8809f7a4e04db8ba5f3f5d3c591736afd64'
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=8);return dict(x.split('=',1) for x in q.stdout.strip().splitlines() if '=' in x)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def proc(pid):
 q=Path('/proc/%d'%pid);st=(q/'stat').read_text().split(') ',1)[1].split();status=(q/'status').read_text();argv=(q/'cmdline').read_bytes();uid=int([x.split()[1] for x in status.splitlines() if x.startswith('Uid:')][0]);return {'pid':pid,'startticks':int(st[19]),'uid':uid,'argv':[x.decode() for x in argv.split(b'\0') if x],'exe_sha256':sha(q/'exe'),'cgroup':(q/'cgroup').read_text().strip()}
def show(u):return run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup'])
o=json.loads((P/'owner.json').read_text());co=json.loads((P/'controls.json').read_text());expected={'owner_nonce':'870358cd92ee','node_id':NODE,'agent_id':AGENT,'proxy_unit':PROXY,'binary_sha256':PS}
if any(o.get(k)!=v for k,v in expected.items()):raise SystemExit('proxy owner manifest mismatch')
if co!={'app_id':APP,'good_script_id':GOOD,'good_revision':3,'good_sha256':GSHA,'bad_script_id':BAD,'bad_revision':2,'bad_sha256':BSHA,'fault_armed':True,'release_good_snapshot':False}:raise SystemExit('exact held control state mismatch')
pu=show(PROXY);pp=proc(int(pu.get('MainPID','0')));cu=show(CHILD);cp=proc(int(cu.get('MainPID','0')))
if pu.get('ActiveState')!='active' or pp['pid']!=50214 or pp['startticks']!=136470092 or pp['uid']!=65534 or pp['exe_sha256']!=PS or pp['cgroup']!='0::/system.slice/'+PROXY:raise SystemExit('proxy fingerprint mismatch')
if cp['pid']!=50291 or cp['startticks']!=136559152 or cp['uid']!=0 or cp['exe_sha256']!=AS or cp['argv']!=['/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent','--config',str(R/'agent.yaml')] or cp['cgroup']!='0::/system.slice/'+CHILD:raise SystemExit('child fingerprint mismatch')
evfile=P/'events.jsonl';records=[json.loads(x) for x in evfile.read_text().splitlines() if x.strip()];held=[x for x in records if x.get('event')=='valid_snapshot_held_for_negative_oracle'];negative=[x for x in records if x.get('event')=='sync_ack' and x.get('accepted_as_bad_sha_rejection') and x.get('ok') is False and x.get('applied_rev')==0]
if not held or not negative:raise SystemExit('negative oracle/held frame missing')
tmp=P/'controls.json.tmp'
with open(tmp,'x') as f:f.write(json.dumps({**co,'release_good_snapshot':True},separators=(',',':'))+'\n');f.flush();os.fsync(f.fileno())
os.chown(tmp,65534,65534);os.chmod(tmp,0o644);os.replace(tmp,P/'controls.json');after=json.loads((P/'controls.json').read_text())
if after!={**co,'release_good_snapshot':True}:raise SystemExit('control release readback mismatch')
offset=evfile.stat().st_size;end=time.monotonic()+35;released=None;acks=[]
while time.monotonic()<end:
 raw=evfile.read_bytes();fresh=[json.loads(x) for x in raw[offset:].splitlines() if x.strip()];released=next((x for x in fresh if x.get('event')=='valid_snapshot_released_after_negative_oracle'),None);acks.extend(x for x in fresh if x.get('event')=='sync_ack')
 if released and any(x.get('ok') is True for x in acks):break
 time.sleep(.25)
if not released:raise SystemExit('legal held Snapshot release event not observed')
print(json.dumps({'status':'LEGAL_SNAPSHOT_RELEASED_AFTER_NEGATIVE_ORACLE','proxy_pid':pp['pid'],'proxy_startticks':pp['startticks'],'child_pid':cp['pid'],'child_startticks':cp['startticks'],'fault_armed':after['fault_armed'],'release_good_snapshot':after['release_good_snapshot'],'release_event':released,'new_sync_ack_events':acks,'successful_ack_seen':any(x.get('ok') is True for x in acks),'negative_ack_remains_proven':True,'raw_frames_emitted':False},sort_keys=True),flush=True)'''
cmd='python3 -c '+shlex.quote(src);name='qa-resume-20261010-release-valid-sha-prune-snapshot-870358cd92ee';t=api('POST','/api/tasks',{'name':name,'type':'command','command':cmd,'target':{'type':'node','node_ids':['91f44866-8a28-435e-bbda-2f2367126c00']},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=t['id'];ev={'task_id':tid,'task_name':name,'source_sha256':hashlib.sha256(src.encode()).hexdigest(),'command_sha256':hashlib.sha256(cmd.encode()).hexdigest(),'release_precondition':'exact negative ACK and valid held Snapshot already recorded; owner process guards pass'}
try:
 rd=api('GET','/api/tasks/'+tid);assert hashlib.sha256(rd.get('command','').encode()).hexdigest()==ev['command_sha256'];x=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=x[0]['id'];end=time.monotonic()+65;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:break
  time.sleep(.5)
 out=fin.get('stdout') or '';ev.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stdout_sha256':hashlib.sha256(out.encode()).hexdigest(),'stdout_json':json.loads(out) if out else None,'stderr_bytes':len((fin.get('stderr') or '').encode())})
finally:
 ev['task_delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
 try:api('GET','/api/tasks/'+tid,None,(404,));ev['task_get_after_status']=404
 except Exception:ev['task_get_after_status']='not-404'
(H/'release-good-snapshot-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':ev.get('status'),'task_deleted_404':ev.get('task_get_after_status')==404,'result':ev.get('stdout_json')},ensure_ascii=False))
