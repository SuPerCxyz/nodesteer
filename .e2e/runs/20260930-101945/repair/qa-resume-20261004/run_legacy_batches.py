#!/usr/bin/env python3
"""Run the parent-approved legacy UI batches against the isolated repair Hub."""
import json,os,re,subprocess,sys,urllib.request
from pathlib import Path
QA=Path(__file__).resolve().parent; ROOT=QA.parents[4]; WEB=ROOT/'web'; PRIVATE=Path(os.environ['NODESTEER_RESUME_PRIVATE_DIR']); CONN=json.loads((PRIVATE/'repair-connection.json').read_text())
SPECS={
 'a': {'grep':r'P01-0[1-6]|P02-(01|04|05)|P03-(01|03)|P04-0[12]|P05-01|P06-(01|02|06)', 'files':['e2e/tests/p01-login.spec.ts','e2e/tests/p02-layout.spec.ts','e2e/tests/p03-dashboard.spec.ts','e2e/tests/p04-nodes.spec.ts','e2e/tests/p06-groups.spec.ts']},
 'b': {'grep':r'P07-0[124]|P08-(01|08)|P10-01|P12-(01|04)|P13-01|P14-01|P15-01|P16-01|P17-(01|03)|P18-01|P19-01|P20-(01|05)|P21-01|P24-0[123]', 'files':['e2e/tests/p07-scripts.spec.ts','e2e/tests/p09-tasks.spec.ts','e2e/tests/p12-schedules.spec.ts','e2e/tests/p24-task-schedules.spec.ts','e2e/tests/p14-applications.spec.ts','e2e/tests/p16-artifacts.spec.ts','e2e/tests/p17-executions.spec.ts','e2e/tests/p19-misc.spec.ts']},
 'c': {'grep':r'E2E-01|P23-0[1-4]|RBAC-(02|03|04|06|07|08)', 'files':['e2e/tests/e2e-flows.spec.ts','e2e/tests/p23-detail-layout.spec.ts','e2e/tests/rbac.spec.ts']},
 'cr': {'grep':r'RBAC-(02|03|04|06|07|08)', 'files':['e2e/tests/rbac.spec.ts']},
}
def put_private(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(data)
def put_public(path,data):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
 with os.fdopen(fd,'w') as f:f.write(data)
def scrub(x,secrets):
 if isinstance(x,dict):return {k:('[REDACTED]' if any(s in k.lower() for s in ('password','token','secret','cookie','authorization','credential')) else scrub(v,secrets)) for k,v in x.items()}
 if isinstance(x,list):return [scrub(v,secrets) for v in x]
 if isinstance(x,str):
  for s in secrets:
   if s:x=x.replace(s,'[REDACTED]')
  return x
 return x
def run(group):
 g=group.lower()
 if g not in SPECS:raise SystemExit('group must be a, b, or c')
 label='resume-20261005-'+g+'-01'; spec=SPECS[g]; base=CONN['hub_url'].rstrip('/')
 rq=urllib.request.Request(base+'/api/nodes',headers={'Authorization':'Bearer '+CONN['token']})
 with urllib.request.urlopen(rq,timeout=12) as r:nodes=json.loads(r.read())
 if len(nodes)!=4 or any(n.get('status')!='online' or n.get('sync_status')!='synced' for n in nodes):raise SystemExit('repair API must report exactly four online/synced nodes')
 run_dir=PRIVATE/'playwright'/label;run_dir.mkdir(mode=0o700,parents=True,exist_ok=False); evidence=run_dir/'evidence'; evidence.mkdir(mode=0o700); artifacts=run_dir/'artifacts';artifacts.mkdir(mode=0o700);resource=run_dir/'resources.jsonl';put_private(resource,'')
 public_evidence=QA/'evidence'/label;public_evidence.mkdir(mode=0o755,parents=True,exist_ok=False)
 env=os.environ.copy();prefix='qa-resume-20261005-'+g+'-'
 c_rbac=None
 if g in ('c','cr'):
  c_rbac=json.loads((PRIVATE/'playwright'/'resume-20261005-c-setup'/'rbac-users.json').read_text())
 env.update({'BASE_URL':base,'NODESTEER_E2E_USERNAME':CONN['admin_username'],'NODESTEER_E2E_PASSWORD':CONN['admin_password'],'NODESTEER_E2E_PREFIX':prefix,'NODESTEER_E2E_NODE_IDS':','.join(n['id'] for n in nodes),'NODESTEER_E2E_LABEL_NODE_ID':nodes[-1]['id'],'NODESTEER_E2E_OPERATOR_USERNAME':c_rbac['operator']['username'] if c_rbac else '','NODESTEER_E2E_VIEWER_USERNAME':c_rbac['viewer']['username'] if c_rbac else '','NODESTEER_E2E_RBAC_PASSWORD':c_rbac['rbac_password'] if c_rbac else '','NODESTEER_E2E_ALLOW_SHARED_SETTINGS':'0','NODESTEER_E2E_ALLOW_USER_CREATE':'0','NODESTEER_E2E_OUTPUT_DIR':str(artifacts),'NODESTEER_E2E_PRIVATE_DIR':str(run_dir),'NODESTEER_E2E_RESOURCE_LOG':str(resource),'NODESTEER_E2E_EVIDENCE_DIR':str(evidence)})
 cmd=['node','node_modules/playwright/cli.js','test','--config','e2e/playwright.config.ts','--reporter=json','--workers=1','--grep',spec['grep'],*spec['files']]
 try:p=subprocess.run(cmd,cwd=WEB,env=env,text=True,capture_output=True,timeout=900)
 except subprocess.TimeoutExpired as e:p=type('Result',(),{'returncode':124,'stdout':e.stdout or '', 'stderr':(e.stderr or '')+'\nRUN_TIMEOUT'})()
 put_private(run_dir/'reporter.raw.json',p.stdout if isinstance(p.stdout,str) else p.stdout.decode(errors='replace'))
 secrets=[CONN.get('admin_password',''),CONN.get('token','')]
 if c_rbac:secrets.extend([c_rbac['rbac_password'],c_rbac['operator']['username'],c_rbac['viewer']['username']])
 users=run_dir/'ui-users.jsonl'
 if users.exists():
  for line in users.read_text().splitlines():
   try:secrets.extend(str(v) for k,v in json.loads(line).items() if any(s in k.lower() for s in ('password','token','secret')))
   except ValueError:pass
 try:safe=scrub(json.loads(p.stdout),secrets)
 except Exception:safe={'parse_error':True}
 results=QA/(label+'-results.json');put_public(results,json.dumps(safe,ensure_ascii=False,indent=2)+'\n')
 for f in evidence.glob('*.json'):
  try:put_public(public_evidence/f.name,json.dumps(scrub(json.loads(f.read_text()),secrets),ensure_ascii=False,indent=2)+'\n')
  except Exception:continue
 stats=safe.get('stats',{}) if isinstance(safe,dict) else {}; res=[]
 for line in resource.read_text().splitlines():
  try:res.append(json.loads(line))
  except ValueError:pass
 counts={}
 for x in res:counts[x.get('status','unknown')]=counts.get(x.get('status','unknown'),0)+1
 summary={'label':label,'group':g.upper(),'base_url':base,'prefix':prefix,'grep':spec['grep'],'specs':spec['files'],'workers':1,'exit_code':p.returncode,'status':'passed' if p.returncode==0 and stats.get('failed',0)==0 else 'failed','stats':stats,'resource_log_records':len(res),'resource_cleanup_counts':counts,'node_ids':[n['id'] for n in nodes],'report':str(results.relative_to(ROOT)),'evidence_directory':str(public_evidence.relative_to(ROOT)),'raw_report_private_path':str(run_dir/'reporter.raw.json'),'resource_log_private_path':str(resource),'stderr_redacted':scrub(p.stderr[-1800:],secrets)}
 put_public(QA/(label+'-run.json'),json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'label':label,'exit_code':p.returncode,'status':summary['status'],'stats':stats,'resource_cleanup_counts':counts,'report':str(results.relative_to(ROOT))},ensure_ascii=False))
 return p.returncode
if __name__=='__main__':raise SystemExit(run(sys.argv[1]))
