#!/usr/bin/env python3
"""Run existing FULL-08 UI app workflow against the isolated repair authority."""
import json,os,secrets,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];RUN='20260930-101945';QA=ROOT/'.e2e/runs'/RUN/'repair/qa';PRIV=Path('/tmp/nodesteer-e2e-'+RUN+'-repair');CONN=json.loads((PRIV/'repair-connection.json').read_text())
HEALTH=json.loads((QA/'static-health.json').read_text());node_id=HEALTH['agents'][0]['id'];output=QA/'app-evidence';output.mkdir(exist_ok=True)
appdir='/tmp/qa-repair-'+RUN+'-app-web';appelf=QA/'app-fixture/sample-app-v1';inspector=QA/'guest-inspect-repair.py'
base=PRIV/'app-ui-private';base.mkdir(mode=0o700,exist_ok=True);base.chmod(0o700)
raw=base/'full08.raw.json';result=QA/'full08-results.json';runinfo=QA/'full08-run.json'
if result.exists() or runinfo.exists():raise SystemExit('refuse overwrite previous App result')
env=os.environ.copy();env.update({'BASE_URL':CONN['hub_url'],'NODESTEER_E2E_USERNAME':CONN['admin_username'],'NODESTEER_E2E_PASSWORD':CONN['admin_password'],
 'NODESTEER_E2E_PREFIX':'qa-repair-'+RUN+'-app-','NODESTEER_E2E_NODE_IDS':node_id,'NODESTEER_E2E_APP_ELF':str(appelf),
 'NODESTEER_E2E_GUEST_INSPECT':str(inspector),'NODESTEER_E2E_APP_DIR':appdir,'NODESTEER_E2E_EVIDENCE_DIR':str(output),
 'NODESTEER_E2E_OUTPUT_DIR':str(base/'playwright-artifacts'),'NODESTEER_E2E_RESOURCE_LOG':str(QA/'full08-resources.jsonl'),
 'NODESTEER_E2E_PRIVATE_DIR':str(base)})
cmd=['node','node_modules/playwright/cli.js','test','--config','e2e/playwright.config.ts','--reporter=json','e2e/tests/full-feature-ui.spec.ts','--grep','FULL-08']
start=time.time();p=subprocess.run(cmd,cwd=ROOT/'web',env=env,text=True,capture_output=True,timeout=360)
# Raw output is private; public evidence contains redacted JSON and summary only.
raw.write_text(p.stdout);raw.chmod(0o600)
text=p.stdout
for secret in [CONN.get('admin_password',''),CONN.get('token',''),CONN.get('registration_token','')]:
 if secret:text=text.replace(secret,'[REDACTED]')
try:
 data=json.loads(text)
except Exception:
 data={'parse_error':'Playwright JSON output could not be parsed','stdout_excerpt':text[-1500:]}
result.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
runinfo.write_text(json.dumps({'command':'node node_modules/playwright/cli.js test --config e2e/playwright.config.ts --reporter=json e2e/tests/full-feature-ui.spec.ts --grep FULL-08','working_directory':'web','exit_code':p.returncode,'duration_seconds':round(time.time()-start,2),'node_id':node_id,'hub_sha256':'164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6','agent_sha256':'d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add','binary_sha256':__import__('hashlib').sha256(appelf.read_bytes()).hexdigest(),'stderr_tail':p.stderr[-1200:].replace(CONN.get('admin_password',''),'[REDACTED')},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'exit_code':p.returncode,'duration_seconds':round(time.time()-start,2),'stats':data.get('stats') if isinstance(data,dict) else None,'result':str(result.relative_to(ROOT)),'private_raw':str(raw)}))
