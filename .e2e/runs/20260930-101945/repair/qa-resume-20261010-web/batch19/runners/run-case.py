import json, re, subprocess, sys, time, select
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch19')
SESSION='qawebsecure20261010-b18-clean38181'; ORIGIN='http://127.0.0.1:38181'
CASES={
 'TABLE-NODES-ERROR':('/nodes','/api/nodes','web/src/features/catalog/index.tsx:88-92; 271-300; ListLayout 2320-2358'),
 'TABLE-APPLICATIONS-ERROR':('/applications','/api/applications','web/src/features/catalog/index.tsx:1513-1522; 1615-1645; ListLayout 2320-2358'),
 'TABLE-ARTIFACTS-ERROR':('/artifacts','/api/artifacts','web/src/features/catalog/index.tsx:1652-1667; 1810-1845; ListLayout 2320-2358'),
 'TABLE-EXECUTIONS-ERROR':('/executions','/api/executions','web/src/features/executions/index.tsx:81-102; 265-300'),
 'TABLE-TRANSFERS-ERROR':('/transfers','/api/transfers','web/src/features/transfers/index.tsx:40-52; 385-405'),
 'TABLE-AUDIT-ERROR':('/audit','/api/audit','web/src/features/catalog/index.tsx:1855-1870; 1925-1943; ListLayout 2320-2358'),
 'TABLE-USERS-ERROR':('/users','/api/users','web/src/features/catalog/index.tsx:1950-1970; 2050-2080; ListLayout 2320-2358'),
}
case_id=sys.argv[1]
if case_id not in CASES: raise SystemExit('case_not_allowlisted')
route,path,source=CASES[case_id]
progress_path=ROOT/'progress.json'; result_path=ROOT/'results.json'; evdir=ROOT/'evidence'; evdir.mkdir(parents=True,exist_ok=True)
def utc():return datetime.now(timezone.utc).isoformat()
def save_progress(stage, extra=None):
 d=json.loads(progress_path.read_text());d['updated_at_utc']=utc();d['phase']=f'{case_id}_{stage.upper()}';d['next']=f'{case_id}: {stage}'
 c=next(x for x in d['cases'] if x['case_id']==case_id);c['stage']=stage
 if extra:c.update(extra)
 progress_path.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
def cli(*args, timeout=15):
 return subprocess.run(['agent-browser','--session',SESSION,*args],capture_output=True,text=True,timeout=timeout,check=True)
def parse_cli_json(out):
 x=json.loads(out.strip());return json.loads(x) if isinstance(x,str) else x
def eval_json(js):
 cli('eval',js);cli('wait','450');return parse_cli_json(cli('eval','JSON.stringify(window.__qaB19Oracle)').stdout)
def api_dom_oracle():
 oracle_url=path+('?limit=10&offset=0' if path in ['/api/audit','/api/executions'] else '')
 js='''(()=>{window.__qaB19Oracle=null;const token=localStorage.getItem("nodesteer_token");fetch(%s,{headers:{Authorization:`Bearer ${token}`}}).then(async r=>{const x=await r.json().catch(()=>null);const a=Array.isArray(x)?x:(Array.isArray(x?.items)?x.items:Array.isArray(x?.data)?x.data:Array.isArray(x?.data?.items)?x.data.items:[]);const m=document.querySelector("main"),t=m?.innerText||"";window.__qaB19Oracle={apiStatus:r.status,apiCount:a.length,total:Number.isFinite(x?.total)?x.total:null,route:location.pathname,rows:m?.querySelectorAll("tbody tr").length||0,skeletons:m?.querySelectorAll("[data-slot=skeleton]").length||0,progressbars:m?.querySelectorAll("[role=progressbar]").length||0,alerts:m?.querySelectorAll("[role=alert]").length||0,emptyFeedback:!!m?.querySelector("svg.lucide-inbox")||/no results|no data|暂无|无数据|未找到|没有匹配/i.test(t)}}).catch(()=>window.__qaB19Oracle={apiStatus:0});return "started"})()'''%json.dumps(ORIGIN+oracle_url)
 return eval_json(js)
def start_runner(name):
 proc=subprocess.Popen(['node',str(ROOT/'runners'/name),path,route],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
 lines=[]; ready=None; deadline=time.time()+10
 while time.time()<deadline:
  ready_r,_,_=select.select([proc.stdout],[],[],0.2)
  if ready_r:
   line=proc.stdout.readline()
   if not line:break
   lines.append(line.strip())
   try:
    obj=json.loads(line)
    if obj.get('phase')=='READY':ready=obj;break
   except:pass
  if proc.poll() is not None:break
 if not ready:
  proc.kill();out,err=proc.communicate(timeout=3);return None,lines,{'harness_error':'runner_ready_timeout_or_failure','returncode':proc.returncode}
 return proc,lines,ready
def finish_runner(proc,lines,timeout=25):
 try: out,err=proc.communicate(timeout=timeout)
 except subprocess.TimeoutExpired:
  proc.terminate()
  try:out,err=proc.communicate(timeout=3)
  except subprocess.TimeoutExpired:proc.kill();out,err=proc.communicate(timeout=3)
  lines.append('runner_timeout')
 for line in out.splitlines():lines.append(line.strip())
 return lines,proc.returncode
# Canonical route and target precondition.
open_result=cli('open',ORIGIN+route);cli('wait','700')
actual_url=cli('get','url').stdout.strip();
if actual_url!=ORIGIN+route: raise SystemExit('route_guard_failed')
save_progress('route_ready')
# Loading phase: exact app GET is held while the same target is sampled.
proc,logs,ready=start_runner('loading-sampler.mjs')
if not proc: raise SystemExit('loading_runner_not_ready')
cli('reload');logs,rc=finish_runner(proc,logs)
loading=next((json.loads(x) for x in logs if x.startswith('{') and json.loads(x).get('phase')=='LOADING_SAMPLED'),{})
continued=next((json.loads(x) for x in logs if x.startswith('{') and json.loads(x).get('phase')=='APP_GET_200'),{})
load_result={'ready':ready,'sample':loading,'continued_get_200':continued,'runner_exit':rc,'runner_lines':logs}
load_oracle=api_dom_oracle()
loading_evidence={'case_id':case_id,'captured_at_utc':utc(),'route':route,'list_get_path':path,'phase':'hard reload exact GET paused then continued','loading_sampler':{k:v for k,v in loading.items() if k in ['phase','path','samples','loadingSamples','sample']},'actual_app_get_200':continued,'after_success_readonly_oracle':load_oracle,'target_guard':{k:ready.get(k) for k in ['path','route','ownedPages','externalPages','blankPages','cdpPort']},'cache_and_fetch_restored':any(json.loads(x).get('phase')=='FINISHED' and json.loads(x).get('cacheRestored') for x in logs if x.startswith('{')),'source_refs':source,'payload_headers_token_or_rows_saved':False,'server_writes':0,'runs':0}
(ROOT/'evidence'/f'{case_id}-loading.json').write_text(json.dumps(loading_evidence,ensure_ascii=False,indent=2)+'\n')
save_progress('loading_sampled_and_get_200',{'loading_evidence':f'evidence/{case_id}-loading.json'})
# Exact path failure phase on same route and target.
proc,logs,ready=start_runner('failure-observer.mjs')
if not proc: raise SystemExit('failure_runner_not_ready')
cli('reload');logs,rc=finish_runner(proc,logs,timeout=25)
err=next((json.loads(x) for x in logs if x.startswith('{') and json.loads(x).get('phase')=='ERROR_STATE_SAMPLE'),{})
finished=next((json.loads(x) for x in logs if x.startswith('{') and json.loads(x).get('phase')=='FINISHED'),{})
# Snapshot is consumed in memory; only the unique retry label/ref is retained.
snapshot=cli('snapshot',timeout=15).stdout
refs=[]
for line in snapshot.splitlines():
 if 'button ' in line and ('重试' in line or re.search(r'\bRetry\b',line)):
  m=re.search(r'\[ref=([^\]]+)\]',line)
  if m:refs.append({'label':'Retry' if 'Retry' in line else '重试','ref':m.group(1)})
fail_evidence={'case_id':case_id,'captured_at_utc':utc(),'route':route,'list_get_path':path,'failure_runner_ready':ready,'error_sample':err,'runner_final':finished,'runner_exit':rc,'retry_controls':[{k:v for k,v in x.items() if k=='label'} for x in refs],'source_refs':source,'response_body_or_snapshot_saved':False,'token_headers_or_raw_rows_saved':False,'server_writes':0,'runs':0}
(ROOT/'evidence'/f'{case_id}-failure.json').write_text(json.dumps(fail_evidence,ensure_ascii=False,indent=2)+'\n')
save_progress('failure_observed_fetch_disabled_cache_restored',{'failure_evidence':f'evidence/{case_id}-failure.json'})
retry_statuses=[];clicked=False;post=None
if len(refs)==1 and err.get('errorState') and err.get('retryVisible'):
 # clear old request history before user click; only exact retry click is evidence.
 cli('network','requests','--clear')
 click=cli('click','@'+refs[0]['ref']);clicked=True
 cli('wait','1500')
 logsout=cli('network','requests','--filter',path).stdout
 for line in logsout.splitlines():
  if ' GET ' in line and re.search(re.escape(path)+r'(?:\?|\s|\()',line):
   m=re.search(r'\s(\d{3})\s*$',line)
   if m:retry_statuses.append(int(m.group(1)))
 post=api_dom_oracle()
retry={'case_id':case_id,'captured_at_utc':utc(),'retry_button_clicked':clicked,'exact_get_statuses_after_click':retry_statuses,'after_retry_readonly_oracle':post,'empty_condition':{'api_count_zero':bool(post and post.get('apiStatus')==200 and post.get('apiCount')==0),'real_empty_feedback':bool(post and post.get('emptyFeedback')),'true_empty_confirmed':bool(post and post.get('apiStatus')==200 and post.get('apiCount')==0 and post.get('emptyFeedback'))},'response_body_or_request_log_saved':False,'server_writes':0,'runs':0}
(ROOT/'evidence'/f'{case_id}-retry.json').write_text(json.dumps(retry,ensure_ascii=False,indent=2)+'\n')
# Status follows all canonical dimensions, including a genuinely empty state.
loading_sample=loading.get('sample') or {}
loading_seen=bool(loading_sample.get('skeletons') or loading_sample.get('progressbars') or loading_sample.get('spinners') or loading_sample.get('ariaBusy') or loading_sample.get('loadingText'))
load_ok=loading_seen and continued.get('status')==200
error_ok=bool(err.get('errorState') and err.get('retryVisible') and (err.get('loadingFailedEvents') or 0)>0)
retry_ok=clicked and 200 in retry_statuses and post and post.get('apiStatus')==200 and post.get('alerts')==0 and ((post.get('apiCount',0)>0 and post.get('rows',0)>0) or retry['empty_condition']['true_empty_confirmed'])
all_ok=load_ok and error_ok and retry_ok and retry['empty_condition']['true_empty_confirmed']
status='PASS' if all_ok else 'PARTIAL'
remaining=[]
if not loading_seen:remaining.append('No loading feedback was observed while the exact GET was paused.')
if not continued or continued.get('status')!=200:remaining.append('The actual application GET did not return 200 after the loading sample.')
if not error_ok:remaining.append('Actual application ErrorState/Retry was not fully observed after exact request failure.')
if not retry_ok:remaining.append('Actual retry GET 200 and restored UI were not fully verified.')
if post and post.get('apiStatus')==200 and post.get('apiCount',0)>0:remaining.append(f"The live successful API response contained {post['apiCount']} items; true empty state cannot be safely manufactured under the zero-write scope.")
elif post and post.get('apiStatus')==200 and post.get('apiCount')==0 and not post.get('emptyFeedback'):remaining.append('API returned zero items but no real UI empty/no-results feedback was observed.')
actual={'loading_feedback_observed':loading_seen,'initial_get_200':continued.get('status')==200,'fetch_failures':err.get('loadingFailedEvents',0),'error_state_and_retry_observed':error_ok,'retry_clicked':clicked,'retry_200':200 in retry_statuses,'retry_restored_ui':bool(retry_ok),'api_count_after_retry':post.get('apiCount') if post else None,'ui_rows_after_retry':post.get('rows') if post else None,'true_empty_confirmed':retry['empty_condition']['true_empty_confirmed'],'evidence':[f'evidence/{case_id}-loading.json',f'evidence/{case_id}-failure.json',f'evidence/{case_id}-retry.json']}
case={'case_id':case_id,'title':case_id.replace('-',' '),'status':status,'evidence':actual['evidence'],'actual':json.dumps(actual,ensure_ascii=False),'remaining_dimensions':remaining,'server_writes':0,'runs':0}
r=json.loads(result_path.read_text());r['cases']=[c for c in r['cases'] if c['case_id']!=case_id];r['cases'].append(case);r['cases'].sort(key=lambda x:next(i for i,y in enumerate(CASES) if y==x['case_id']))
r['updated_at_utc']=utc();r['counts']={'PASS':sum(c['status']=='PASS' for c in r['cases']),'PARTIAL':sum(c['status']=='PARTIAL' for c in r['cases']),'FAIL':sum(c['status']=='FAIL' for c in r['cases']),'NOT_RUN':7-len(r['cases'])};result_path.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
q=json.loads(progress_path.read_text());q['updated_at_utc']=utc();q['counts']=r['counts'];c=next(x for x in q['cases'] if x['case_id']==case_id);c.update({'status':status,'stage':'complete','remaining_dimensions':remaining,'evidence':actual['evidence']});q['phase']=f'{case_id}_{status}';q['next']='Next canonical case in batch19: '+next((x['case_id'] for x in q['cases'] if x['status']=='NOT_RUN'),'batch complete');progress_path.write_text(json.dumps(q,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'case_id':case_id,'status':status,'loading_feedback':loading_seen,'loading_get200':load_ok,'error_retry':error_ok,'retry200':200 in retry_statuses,'api_count':post.get('apiCount') if post else None,'ui_rows':post.get('rows') if post else None,'true_empty':retry['empty_condition']['true_empty_confirmed'],'remaining_count':len(remaining),'counts':r['counts'],'evidence':actual['evidence']},ensure_ascii=False))
