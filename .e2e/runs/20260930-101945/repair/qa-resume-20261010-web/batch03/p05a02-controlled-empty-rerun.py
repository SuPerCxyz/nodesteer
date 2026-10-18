import json,subprocess,urllib.request,urllib.error
from pathlib import Path
from datetime import datetime,timezone
B=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch03');priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());O='http://192.168.100.209:18080';C=['agent-browser','--session','qawebsecure20261009','--allowed-domains','192.168.100.209','--args','--unsafely-treat-insecure-origin-as-secure='+O]
def cli(*a):
 r=subprocess.run(C+[str(x) for x in a],capture_output=True,text=True,timeout=20)
 if r.returncode:raise RuntimeError((r.stderr or r.stdout)[:180])
 return r.stdout
def ev(js):return json.loads(cli('--json','eval',js))['data']['result']
def clear(label):
 cli('find','label',label,'click');cli('press','Control+a');cli('press','Backspace')
def fill(label,val):cli('find','label',label,'fill',val)
def open_d():
 if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
 cli('find','role','button','click','--name','添加节点');cli('wait',150)
def form():return ev('(()=>{const f=document.querySelector("[role=dialog] form");return {valid:f.checkValidity(),values:[...f.querySelectorAll("input")].map(x=>({length:x.value.length,required:x.required,valid:x.checkValidity(),type:x.type}))}})()')
def net_posts():
 r=subprocess.run(C+['--json','network','requests'],capture_output=True,text=True,timeout=15)
 d=json.loads(r.stdout) if r.returncode==0 else {};out=[]
 def rec(x):
  if isinstance(x,dict):
   u=x.get('url','')
   if isinstance(u,str) and '/api/nodes/enrollment' in u and x.get('method')=='POST':out.append({'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):rec(v)
  elif isinstance(x,list):
   for v in x:rec(v)
 rec(d);return out
def node_count():
 q=urllib.request.Request(priv['hub_url'].rstrip('/')+'/api/nodes',headers={'Authorization':'Bearer '+priv['token']})
 with urllib.request.urlopen(q,timeout=10) as r:
  d=json.loads(r.read());return len(d if isinstance(d,list) else d.get('items',[]))
branches=[]
for name in ['all_empty','missing_name','missing_address','missing_hub']:
 cli('open',O+'/agents');cli('wait',500);open_d()
 # use real fills for values intended nonempty and real key events to clear intended empty fields
 if name=='all_empty':
  clear('节点名称');clear('节点地址（IP 或域名）');clear('Hub 地址')
 elif name=='missing_name':
  clear('节点名称');fill('节点地址（IP 或域名）','127.70.10.91');fill('Hub 地址',O)
 elif name=='missing_address':
  fill('节点名称','qa-web-required-check-2');clear('节点地址（IP 或域名）');fill('Hub 地址',O)
 else:
  fill('节点名称','qa-web-required-check-3');fill('节点地址（IP 或域名）','127.70.10.93');clear('Hub 地址')
 state=form();cli('network','requests','--clear');cli('find','role','button','click','--name','生成安装命令');cli('wait',120);posts=net_posts()
 branches.append({'branch':name,'pre_submit_form_valid':state['valid'],'inputs':state['values'],'post_count':len(posts),'post_statuses':[x.get('status') for x in posts],'required_empty_observed':any(x['required'] and x['length']==0 and not x['valid'] for x in state['values']),'blocked_no_post':not state['valid'] and len(posts)==0})
 if ev('!!document.querySelector("[role=dialog]")'):cli('press','Escape');cli('wait',600)
# API empty/missing name/IP should return 400 without any node count change.
before=node_count();api=[]
for label,body in [('all_empty',{}),('missing_name',{'node_ip':'127.70.10.91','hub_address':O}),('missing_ip',{'node_name':'qa-web-api-missing-ip','hub_address':O})]:
 req=urllib.request.Request(priv['hub_url'].rstrip('/')+'/api/nodes/enrollment',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+priv['token'],'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=8) as r:status=r.status
 except urllib.error.HTTPError as e:status=e.code
 after=node_count();api.append({'case':label,'http_status':status,'omitted_fields':[k for k in ('node_name','node_ip','hub_address') if not body.get(k)],'node_count_unchanged':after==before})
passed=all(x['blocked_no_post'] for x in branches) and all(x['http_status']==400 and x['node_count_unchanged'] for x in api)
result={'captured_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),'canonical_id':'P05A-02','status':'PASS' if passed else 'PARTIAL','ui_branches':branches,'direct_api_negative_cases':api,'node_count_before_api':before,'node_count_after_api':node_count(),'fill_empty_method':'real keyboard Control+A then Backspace; DOM validity and lengths checked before submit','missing_hub_api_note':'hub_address is optional in handler; omitted Hub was tested only on UI required control; API fallback contract was not asserted as a negative.','private_values_saved':False}
(B/'case-p05a-02.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
p=json.loads((B/'progress.json').read_text());p['results']=[x for x in p['results'] if x['canonical_id']!='P05A-02'];p['results'].append({'canonical_id':'P05A-02','status':result['status'],'evidence':['case-p05a-02.json'],'details':{'ui_branches':branches,'api_negative':api}});p.update({'updated_at_utc':result['captured_at_utc'],'phase':'VALIDATION_ONLY_COMPLETE','current_gate':'Corrected P05A-02 control: typed fields were cleared through real keyboard events, verified empty/invalid before click. Accidental node has already been deleted; valid fixture creation remains stopped until this proves 0 UI POST.'});(B/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':result['status'],'ui':branches,'api':api,'node_count_after':result['node_count_after_api']},ensure_ascii=False),flush=True)
