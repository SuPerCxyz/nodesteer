import gzip,hashlib,json,os,pathlib,pwd,stat,subprocess,time,urllib.request
from pathlib import Path
ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';ART='6ce76a15-f3de-42b9-b7d4-63e4d02edfc4';ART_SHA='9b5f1e9708a440fdaadd97836e64f4156b281bb17e7bfb73c42e6a3bb38b50ef';BIN_SHA='394d0eab7ed45218a07b796c4e49519bfb1ea81af8a65cffd6a2da318e48a52e';CAND=ROOT/'ws-frame-proxy-candidate-v3';PART=ROOT/'proxy-artifact-download.gz.part'
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=8);return {'rc':p.returncode,'out':p.stdout.strip()}
def show(u):
 p=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup']);return dict(x.split('=',1) for x in p['out'].splitlines() if '=' in x)
def sha_bytes(b):return hashlib.sha256(b).hexdigest()
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
o=json.loads((ROOT/'owner.json').read_text());ctrl=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!=NONCE or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=UNIT:raise SystemExit('proxy owner manifest mismatch')
if ctrl.get('fault_armed') or ctrl.get('release_good_snapshot'):raise SystemExit('proxy controls are not safely unarmed')
u=show(UNIT);pid=int(u.get('MainPID','0'))
if u.get('ActiveState')!='active' or pid!=50125 or u.get('ControlGroup')!='/system.slice/'+UNIT:raise SystemExit('current proxy Unit fingerprint mismatch')
if CAND.exists() or CAND.is_symlink() or PART.exists() or PART.is_symlink():raise SystemExit('candidate transfer target already exists')
token=os.environ.get('NODESTEER_PARAM_qa_auth','')
if not token:raise SystemExit('secret parameter missing')
url='http://192.168.100.209:18080/api/artifacts/'+ART+'/download';req=urllib.request.Request(url,headers={'Authorization':'Bearer '+token})
try:
 with urllib.request.urlopen(req,timeout=45) as r:
  if r.status!=200:raise SystemExit('artifact download HTTP mismatch')
  got_header=r.headers.get('X-Artifact-SHA256','');compressed=r.read()
except Exception as e:raise SystemExit('artifact download failed type='+type(e).__name__)
if got_header!=ART_SHA or sha_bytes(compressed)!=ART_SHA:raise SystemExit('artifact download SHA mismatch')
fd=os.open(PART,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
try:os.write(fd,compressed);os.fsync(fd)
finally:os.close(fd)
try:
 with gzip.open(PART,'rb') as f:binary=f.read()
 if sha_bytes(binary)!=BIN_SHA:raise SystemExit('decompressed proxy binary SHA mismatch')
 fd=os.open(CAND,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o755)
 try:os.write(fd,binary);os.fsync(fd)
 finally:os.close(fd)
 uid=pwd.getpwnam('nobody').pw_uid;gid=pwd.getpwnam('nobody').pw_gid;os.chown(CAND,uid,gid);os.chmod(CAND,0o755)
 if sha(CAND)!=BIN_SHA or CAND.stat().st_uid!=uid or CAND.stat().st_gid!=gid or CAND.stat().st_mode&0o7777!=0o755:raise SystemExit('candidate file metadata mismatch')
finally:
 try:PART.unlink()
 except FileNotFoundError:pass
print(json.dumps({'status':'CANDIDATE_DOWNLOADED_AND_VERIFIED','artifact_id':ART,'artifact_sha256':ART_SHA,'download_header_sha256':got_header,'candidate_path':str(CAND),'candidate_sha256':sha(CAND),'candidate_owner_uid_gid':f'{CAND.stat().st_uid}:{CAND.stat().st_gid}','candidate_mode':oct(CAND.stat().st_mode&0o7777),'proxy_unit':UNIT,'proxy_pid_unchanged':pid,'artifact_temp_removed':not PART.exists(),'secret_value_logged':False},sort_keys=True),flush=True)
