#!/usr/bin/env python3
import argparse,json,os,re,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];RUN='20260930-101945';QA=ROOT/'.e2e/runs'/RUN/'repair/qa';PRIV=Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
parser=argparse.ArgumentParser();parser.add_argument('--label',required=True);parser.add_argument('--grep',default='');parser.add_argument('files',nargs='+');args=parser.parse_args()
conn=json.loads((PRIV/'repair-connection.json').read_text());roles=json.loads((PRIV/'main-accounts.json').read_text());health=json.loads((QA/'static-health.json').read_text());webpriv=PRIV/'web-suite-private';webpriv.mkdir(mode=0o700,exist_ok=True);webpriv.chmod(0o700)
output=QA/'web-evidence'/args.label;output.mkdir(parents=True,exist_ok=True)
raw=webpriv/(args.label+'.raw.json');result=QA/(args.label+'-results.json');runinfo=QA/(args.label+'-run.json');resources=QA/(args.label+'-resources.jsonl')
if result.exists() or runinfo.exists():raise SystemExit('refuse overwrite previous web suite result')
env=os.environ.copy();env.update({'BASE_URL':conn['hub_url'],'NODESTEER_E2E_USERNAME':conn['admin_username'],'NODESTEER_E2E_PASSWORD':conn['admin_password'],
'NODESTEER_E2E_PREFIX':'qa-repair-'+RUN+'-web-','NODESTEER_E2E_NODE_IDS':','.join(n['id'] for n in health['agents']),
'NODESTEER_E2E_LABEL_NODE_ID':health['agents'][3]['id'],'NODESTEER_E2E_OPERATOR_USERNAME':roles['operator']['username'],
'NODESTEER_E2E_VIEWER_USERNAME':roles['viewer']['username'],'NODESTEER_E2E_RBAC_PASSWORD':roles['operator']['password'],
'NODESTEER_E2E_OUTPUT_DIR':str(webpriv/'playwright-artifacts'),'NODESTEER_E2E_PRIVATE_DIR':str(webpriv),
'NODESTEER_E2E_RESOURCE_LOG':str(resources),'NODESTEER_E2E_EVIDENCE_DIR':str(output),
'NODESTEER_E2E_APP_ELF':str(QA/'app-fixture/sample-app-v1'),'NODESTEER_E2E_GUEST_INSPECT':str(QA/'guest-inspect-repair.py'),
'NODESTEER_E2E_APP_DIR':'/tmp/qa-repair-'+RUN+'-app-web','NODESTEER_E2E_ALLOW_SHARED_SETTINGS':'1'})
cmd=['node','node_modules/playwright/cli.js','test','--config','e2e/playwright.config.ts','--reporter=json']
if args.grep:cmd+=['--grep',args.grep]
cmd+=args.files;start=time.time();p=subprocess.run(cmd,cwd=ROOT/'web',env=env,text=True,capture_output=True,timeout=900)
raw.write_text(p.stdout);raw.chmod(0o600);secrets=[conn.get('admin_password',''),conn.get('token',''),conn.get('registration_token',''),roles['operator']['password']]
for v in secrets:
 if v:p.stdout=p.stdout.replace(v,'[REDACTED]')
try:data=json.loads(p.stdout)
except Exception:data={'parse_error':'Playwright JSON not parseable after redaction','output_tail':p.stdout[-1600:]}
def scrub(x):
 if isinstance(x,dict):return {k:('[REDACTED]' if any(z in k.lower() for z in ('password','token','authorization','cookie','credential')) else scrub(v)) for k,v in x.items()}
 if isinstance(x,list):return [scrub(v) for v in x]
 if isinstance(x,str):
  for v in secrets:
   if v:x=x.replace(v,'[REDACTED]')
  return x
 return x
result.write_text(json.dumps(scrub(data),ensure_ascii=False,indent=2)+'\n')
runinfo.write_text(json.dumps({'label':args.label,'command':cmd,'working_directory':'web','exit_code':p.returncode,'duration_seconds':round(time.time()-start,2),'hub_sha256':'164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6','agent_sha256':'d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add','agent_node_ids':[n['id'] for n in health['agents']],'stderr_tail':p.stderr[-1000:]},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'label':args.label,'exit_code':p.returncode,'duration_seconds':round(time.time()-start,2),'stats':data.get('stats') if isinstance(data,dict) else None,'result':str(result.relative_to(ROOT)),'raw_private':str(raw)}))
