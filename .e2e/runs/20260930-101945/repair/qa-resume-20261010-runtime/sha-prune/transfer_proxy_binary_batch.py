import hashlib,json,sys,time,datetime
from pathlib import Path
from urllib.request import *
from urllib.error import HTTPError
from urllib.parse import urlsplit
H=Path(__file__).resolve().parent;DATA=(H/'transfer-data/proxy.bin.gz.b64').read_text();WIDTH=70000; chunks=[DATA[i:i+WIDTH] for i in range(0,len(DATA),WIDTH)]
start=int(sys.argv[1]);count=int(sys.argv[2]);assert 0<=start<len(chunks) and 1<=count<=10
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');token=c['token'];assert urlsplit(base).port==18080
op=build_opener(ProxyHandler({}));NODE='91f44866-8a28-435e-bbda-2f2367126c00';ROOT='/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee';PART=ROOT+'/proxy.bin.gz.part'
def api(m,p,b=None,allowed=(200,)):
 h={'Authorization':'Bearer '+token};data=None
 if b is not None:data=json.dumps(b).encode();h['Content-Type']='application/json'
 try:
  with op.open(Request(base+p,data=data,headers=h,method=m),timeout=25) as r:s,x=r.status,r.read()
 except HTTPError as e:s,x=e.code,e.read()
 if s not in allowed:raise RuntimeError(f'{m} {p} HTTP {s}')
 return json.loads(x) if x else None
def wait(eid):
 end=time.monotonic()+35;fin=None
 while time.monotonic()<end:
  fin=api('GET','/api/executions/'+eid)
  if fin.get('status') in {'SUCCESS','FAILED','BLOCKED','TIMED_OUT','CANCELED'}:return fin
  time.sleep(.25)
 return fin
lp=H/'transfer-ledger.json';ledger=json.loads(lp.read_text()) if lp.exists() else {'case_id':'BE-SCRIPT-SHA-002','remote_part_path':PART,'chunk_width_base64_chars':WIDTH,'chunk_count':len(chunks),'completed':[],'task_cleanup_failures':[]}
for ix in range(start,min(start+count,len(chunks))):
 if any(x['index']==ix for x in ledger['completed']):continue
 chunk=chunks[ix];name=f'qa-resume-20261010-proxy-bin-chunk-{ix:03d}-870358cd92ee';q="'"+chunk+"'";init=('test ! -e '+PART+' && install -o 65534 -g 65534 -m 0600 /dev/null '+PART+'; ' if ix==0 else 'test -f '+PART+' && test "$(stat -c %u:%g:%a '+PART+')" = "65534:65534:600"; ');command=init+"printf '%s' "+q+' | base64 -d >> '+PART
 task=api('POST','/api/tasks',{'name':name,'type':'command','command':command,'target':{'type':'node','node_ids':[NODE]},'parameters':[],'timeout':60,'retry':0,'offline_policy':'hub_online_required','enabled':True},(200,201));tid=task['id'];cmdsha=hashlib.sha256(command.encode()).hexdigest();entry={'index':ix,'task_id':tid,'task_name':name,'command_sha256':cmdsha,'chunk_base64_sha256':hashlib.sha256(chunk.encode()).hexdigest(),'chunk_chars':len(chunk)}
 try:
  read=api('GET','/api/tasks/'+tid);assert hashlib.sha256(read.get('command','').encode()).hexdigest()==cmdsha
  xs=api('POST','/api/tasks/'+tid+'/run',{'params':{}},(200,201));eid=xs[0]['id'];fin=wait(eid);entry.update({'execution_id':eid,'status':fin.get('status'),'node_id':fin.get('node_id'),'stderr_bytes':len((fin.get('stderr') or '').encode())})
 finally:
  entry['delete_status']=api('DELETE','/api/tasks/'+tid,None,(200,204))
  try:api('GET','/api/tasks/'+tid,None,(404,));entry['get_after_status']=404
  except Exception:entry['get_after_status']='not-404';ledger['task_cleanup_failures'].append(tid)
 ledger['completed'].append(entry);lp.write_text(json.dumps(ledger,indent=2)+'\n')
 if entry.get('status')!='SUCCESS' or entry['get_after_status']!=404:raise RuntimeError('chunk transfer task or cleanup failed: '+str(ix))
pp=H.parent/'progress.json';d=json.loads(pp.read_text());d['updated_at']=datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ');d['proxy_transfer']={'completed_chunks':len(ledger['completed']),'chunk_count':len(chunks),'remote_root':ROOT,'part_file':PART,'transfer_complete':len(ledger['completed'])==len(chunks)};d['status']='PROXY_BINARY_TRANSFER_IN_PROGRESS';pp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'batch_start':start,'batch_count':count,'chunks_completed_total':len(ledger['completed']),'chunk_count':len(chunks),'all_tasks_deleted_404':all(x['get_after_status']==404 for x in ledger['completed'])}))
