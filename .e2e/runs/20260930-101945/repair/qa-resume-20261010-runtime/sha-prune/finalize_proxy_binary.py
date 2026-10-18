import gzip,hashlib,json,os,pathlib,pwd,shutil,subprocess,time
from pathlib import Path
ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');R=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee');NONCE='870358cd92ee';NODE='c77b8a84-8085-4ea8-8532-de171761854b';AGENT='agent-426aa507-af4c-4005-858f-ce497d2fc67d';PROXY='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BINARY='ws-frame-proxy';BS='6937afa1ba697c4b91a7baafe3b15e1fc99c1b481fba328d24c479fd4467c70e';GS='3209f42a3b1160c8631ac7b7b867bae6e3371e4794cc9ba91efb59078e12c0cc'
def run(a):
 p=subprocess.run(a,text=True,capture_output=True,timeout=12);return {'rc':p.returncode,'out':p.stdout.strip()}
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for z in iter(lambda:f.read(1048576),b''):h.update(z)
 return h.hexdigest()
def show(u):
 q=run(['systemctl','show',u,'-p','LoadState','-p','ActiveState','-p','MainPID','-p','ControlGroup']);return dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x)
o=json.loads((ROOT/'owner.json').read_text());c=json.loads((ROOT/'controls.json').read_text())
if o.get('owner_nonce')!=NONCE or o.get('node_id')!=NODE or o.get('agent_id')!=AGENT or o.get('proxy_unit')!=PROXY or o.get('binary_sha256')!=BS:raise SystemExit('proxy owner manifest mismatch')
if c.get('fault_armed') or c.get('release_good_snapshot') or c.get('good_sha256')!='728dafe9a3392b084f7f3c050a018a3f958aa75049e06ba56081f075dcd9f17d' or c.get('bad_sha256')!='7d6df17e0117eb02de2b0263062ea68bf68e45dea5c1959816d7bdc719466c54':raise SystemExit('proxy controls are not exact/unarmed')
u=show(PROXY)
if u.get('LoadState')!='not-found':raise SystemExit('proxy Unit appeared unexpectedly')
part=ROOT/'proxy.bin.gz.part';dest=ROOT/BINARY;tmp=ROOT/(BINARY+'.tmp')
if not part.is_file() or part.is_symlink() or dest.exists() or dest.is_symlink() or tmp.exists():raise SystemExit('binary transfer paths fail exact guard')
if part.stat().st_uid!=65534 or part.stat().st_mode&0o7777!=0o600 or sha(part)!=GS:raise SystemExit('compressed transfer owner/mode/hash mismatch')
with gzip.open(part,'rb') as src,open(tmp,'xb') as dst:shutil.copyfileobj(src,dst,1024*1024)
if sha(tmp)!=BS:tmp.unlink();raise SystemExit('decompressed binary SHA mismatch')
uid=pwd.getpwnam('nobody').pw_uid;gid=pwd.getpwnam('nobody').pw_gid;os.chown(tmp,uid,gid);os.chmod(tmp,0o755);os.replace(tmp,dest);part.unlink()
if sha(dest)!=BS or dest.stat().st_uid!=uid or dest.stat().st_gid!=gid or dest.stat().st_mode&0o7777!=0o755:raise SystemExit('final binary metadata mismatch')
print(json.dumps({'status':'PROXY_BINARY_READY','binary_path':str(dest),'binary_sha256':sha(dest),'owner_uid_gid':f'{dest.stat().st_uid}:{dest.stat().st_gid}','mode':oct(dest.stat().st_mode&0o7777),'gzip_sha256':GS,'transfer_part_removed':not part.exists(),'proxy_unit_load_state':u.get('LoadState'),'fault_armed':c.get('fault_armed')},sort_keys=True),flush=True)
