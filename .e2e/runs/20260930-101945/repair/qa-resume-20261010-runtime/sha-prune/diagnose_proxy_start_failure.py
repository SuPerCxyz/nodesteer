import hashlib,json,os,pathlib,subprocess
from pathlib import Path
ROOT=Path('/tmp/nodesteer-e2e-20260930-P0-002-native1-870358cd92ee-sha-prune-proxy-870358cd92ee');R=Path('/tmp/nodesteer-e2e-20261009-P0-002-native1-870358cd92ee');UNIT='nodesteer-qa-e2e-20261010-p0-002-sha-prune-proxy-870358cd92ee.service';BIN=ROOT/'ws-frame-proxy';CAND=ROOT/'ws-frame-proxy-candidate-v3'
def run(a):
 q=subprocess.run(a,text=True,capture_output=True,timeout=10);return {'rc':q.returncode,'out':q.stdout.strip(),'err_bytes':len(q.stderr.encode())}
props=['LoadState','ActiveState','SubState','Result','ExecMainCode','ExecMainStatus','MainPID','ControlGroup','User','Group','ExecStart','Restart','NoNewPrivileges','CapabilityBoundingSet','AmbientCapabilities','ProtectSystem','ReadWritePaths','IPAddressAllow','IPAddressDeny','RuntimeMaxUSec'];q=run(['systemctl','show',UNIT,*sum((['-p',x] for x in props),[])]);u=dict(x.split('=',1) for x in q['out'].splitlines() if '=' in x);j=run(['journalctl','-u',UNIT,'-n','20','--no-pager','-o','cat']);
for p in [BIN,CAND]:
 if p.exists():
  h=hashlib.sha256(p.read_bytes()).hexdigest();m={'path':str(p),'exists':True,'uid':p.stat().st_uid,'mode':oct(p.stat().st_mode&0o7777),'sha256':h}
 else:m={'path':str(p),'exists':False}
 if p==BIN:binm=m
 else:candm=m
owner=json.loads((ROOT/'owner.json').read_text());ctrl=json.loads((ROOT/'controls.json').read_text());print(json.dumps({'unit':u,'journal_tail':j['out'][-3000:],'journal_stderr_bytes':j['err_bytes'],'binary':binm,'candidate':candm,'owner':{k:owner.get(k) for k in ['owner_nonce','node_id','agent_id','proxy_unit','source_sha256','binary_sha256']},'fault_armed':ctrl.get('fault_armed'),'child_agent_unit':run(['systemctl','show','nodesteer-qa-e2e-20261009-p0-002-native1-870358cd92ee.service','-p','ActiveState','-p','MainPID','-p','ControlGroup'])['out']},sort_keys=True),flush=True)
