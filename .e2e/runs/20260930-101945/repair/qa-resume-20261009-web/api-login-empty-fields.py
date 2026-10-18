import json,urllib.error,urllib.request
from datetime import datetime,timezone,timedelta
from pathlib import Path
BASE='http://192.168.100.209:18080'
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
DUMMY='synthetic-nonexistent-e2e-user-20261009'
CASES=[('both_empty',{'username':'','password':''}),('username_empty_password_nonempty',{'username':'','password':'synthetic-not-a-password'}),('username_nonempty_password_empty',{'username':DUMMY,'password':''})]
results=[]
for name,payload in CASES:
 req=urllib.request.Request(BASE+'/api/login',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=10) as r:
   status=r.status; raw=r.read(65536)
 except urllib.error.HTTPError as e:
  status=e.code; raw=e.read(65536)
 except Exception as e:
  results.append({'case':name,'transport_error':type(e).__name__,'http_status':None,'token_or_session_returned':None});continue
 try: data=json.loads(raw)
 except Exception: data={}
 errs=[str(data.get(k,'')) for k in ('error','message','detail') if data.get(k)]
 results.append({'case':name,'http_status':status,'feedback_category':'validation_error' if any('required' in x.lower() or 'empty' in x.lower() or 'invalid input' in x.lower() for x in errs) else ('credential_rejection' if any('invalid' in x.lower() or 'credential' in x.lower() for x in errs) or status in (401,403) else 'other'),'error_fields_present':bool(errs),'token_or_session_returned':bool(data.get('token') or data.get('Token') or data.get('session')),'raw_response_saved':False,'request_body_saved':False})
record={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'endpoint':'POST /api/login','cases':results,'input_policy':'only empty fields and synthetic nonexistent dummy; no real QA username/password; body never saved'}
(OUT/'p01-03-server-fields.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(results,ensure_ascii=False))
