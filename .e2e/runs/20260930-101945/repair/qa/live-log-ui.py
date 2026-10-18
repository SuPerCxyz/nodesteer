#!/usr/bin/env python3
import json,secrets,subprocess,time,urllib.error,urllib.request
from pathlib import Path
p=Path('/tmp/nodesteer-e2e-20260930-101945-repair/repair-connection.json');c=json.loads(p.read_text());base=c['hub_url'].rstrip('/');token=c['token'];node=json.loads(Path('.e2e/runs/20260930-101945/repair/qa/static-health.json').read_text())['agents'][0]['id'];prefix='qa-repair-20260930-101945-live-log-'+secrets.token_hex(3);markers=('QA_FIRST_'+secrets.token_hex(4),'QA_SECOND_'+secrets.token_hex(4))
def api(method,path,body=None):
 d=None if body is None else json.dumps(body).encode();h={'Authorization':'Bearer '+token};
 if d is not None:h['Content-Type']='application/json'
 q=urllib.request.Request(base+path,data=d,headers=h,method=method)
 try:
  with urllib.request.urlopen(q,timeout=15) as r:return r.status,json.loads(r.read())
 except urllib.error.HTTPError as e:return e.code,json.loads(e.read() or b'null')
def logs(exid):return api('GET','/api/executions/'+exid+'/logs')
create={'name':prefix,'type':'command','target':{'type':'node','node_ids':[node]},'command':"printf '%s\\n'; sleep 7; printf '%s\\n'"%markers,'timeout':20,'retry':0,'offline_policy':'hub_online_required','enabled':True}
assert not Path(__file__).with_name('live-log-ui.json').exists()
status,task=api('POST','/api/tasks',create);assert status in (200,201) and isinstance(task,dict),f'task create HTTP {status}'
status,execs=api('POST','/api/tasks/'+task['id']+'/run',{'params':{}});assert status==200 and execs,f'task run HTTP {status}'
ex=execs[0];exid=ex.get('id') or ex.get('ID');assert exid
first_seen=None;start=time.monotonic()
while time.monotonic()-start<20:
 st,rows=logs(exid)
 if st==200 and markers[0] in json.dumps(rows):first_seen=round(time.monotonic()-start,3);break
 time.sleep(.2)
assert first_seen is not None,'first streamed log did not arrive'
url=base+'/executions/'+exid
popen=subprocess.run(['agent-browser','--session','node-repair-qa','--allowed-domains','192.168.100.209','open',url],text=True,capture_output=True,timeout=25)
assert popen.returncode==0,'browser could not open execution detail route'
state=None
while time.monotonic()-start<40:
 code,state=api('GET','/api/executions/'+exid)
 if code==200 and state.get('status') not in ('PENDING','RUNNING'):break
 time.sleep(.25)
assert code==200 and state.get('status')=='SUCCESS',f'Execution terminal state was {state.get("status") if state else code}'
time.sleep(4.0)
read=subprocess.run(['agent-browser','--session','node-repair-qa','read'],text=True,capture_output=True,timeout=25)
assert read.returncode==0,'browser read failed'
visible=read.stdout
assert markers[0] in visible and markers[1] in visible,'current page did not render both streamed chunks after terminal without manual reload'
shot=Path(__file__).with_name('live-log-detail.png')
ss=subprocess.run(['agent-browser','--session','node-repair-qa','screenshot',str(shot)],text=True,capture_output=True,timeout=20)
# screenshot CLI may choose its managed path; the rendered text and API are the decisive evidence.
st,all_chunks=logs(exid);assert st==200 and markers[0] in json.dumps(all_chunks) and markers[1] in json.dumps(all_chunks)
# Delete this exact QA task; retain execution history.
delcode,_=api('DELETE','/api/tasks/'+task['id']);assert delcode in (200,204)
report={'failure_id':'WEB-LIVE-LOG','case_id':'EXECUTION-DETAIL-LIVE-LOG','status':'passed','hub_sha256':'e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472','task_id':task['id'],'execution_id':exid,'first_chunk_seen_seconds':first_seen,'terminal_status':state['status'],'final_api_logs_http':st,'api_contains_both_chunks':True,'browser_detail_rendered_both_chunks_without_reload':True,'browser_text_excerpt':'execution detail page showed both first and second QA markers','screenshot_written':shot.exists(),'cleanup':{'task_http':delcode,'execution_history_retained':True},'secrets_included':False}
Path(__file__).with_name('live-log-ui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','first_chunk_seconds':first_seen,'terminal':state['status'],'ui_both_chunks':True,'task_deleted':delcode}))
