import json,subprocess,time
from datetime import datetime,timezone,timedelta
from pathlib import Path
OUT=Path('.e2e/runs/20260930-101945/repair/qa-resume-20261009-web')
SESSION='qaweblogin20261009';HOST='192.168.100.209';ORIGIN='http://192.168.100.209:18080'
CLI=['agent-browser','--session',SESSION,'--allowed-domains',HOST,'--args','--unsafely-treat-insecure-origin-as-secure='+ORIGIN]
URL=ORIGIN+'/sign-in'
def cli(*args):
 r=subprocess.run(CLI+list(args),text=True,capture_output=True,timeout=20)
 if r.returncode:raise RuntimeError('browser action failed '+str(args[0]))
 return r.stdout
def ev(js):return json.loads(cli('eval',js))
def requests():
 r=subprocess.run(CLI+['--json','network','requests','--filter','/api/login'],text=True,capture_output=True,timeout=15)
 if r.returncode:return []
 try:d=json.loads(r.stdout)
 except Exception:return []
 out=[]
 def walk(x):
  if isinstance(x,dict):
   if x.get('url','').endswith('/api/login'):out.append({'method':x.get('method'),'status':x.get('status')})
   for v in x.values():
    if isinstance(v,(dict,list)):walk(v)
  elif isinstance(x,list):
   for v in x:walk(v)
 walk(d);return out
def run_case(label,username=None,password=None):
 cli('open',URL);cli('wait','--fn','document.querySelector("input[name=\\"username\\"]")!==null')
 cli('network','requests','--clear')
 if username is not None: cli('fill','input[name="username"]',username)
 if password is not None: cli('fill','input[name="password"]',password)
 before=ev('({url:location.pathname,userLength:document.querySelector("input[name=\\"username\\"]")?.value.length??null,passwordLength:document.querySelector("input[name=\\"password\\"]")?.value.length??null,userRequired:document.querySelector("input[name=\\"username\\"]")?.required,passwordRequired:document.querySelector("input[name=\\"password\\"]")?.required,tokenPresent:localStorage.getItem("nodesteer_token")!==null})')
 cli('click','button[type="submit"]')
 time.sleep(.15)
 after=ev('({url:location.pathname,tokenPresent:localStorage.getItem("nodesteer_token")!==null,invalidInput:document.body.innerText.includes("Invalid input"),invalidCredentials:document.body.innerText.toLowerCase().includes("invalid credentials")})')
 evts=requests()
 return {'branch':label,'before':before,'after':after,'login_events':evts,'post_count':len(evts),'raw_request_body_saved':False,'secrets_saved':False}

def main():
 cases=[run_case('both_empty'),run_case('username_empty_password_nonempty',username=None,password='synthetic-web-case-20261009'),run_case('username_nonempty_password_empty',username='qa-web-empty-login-20261009',password=None)]
 rec={'captured_at':datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),'session':SESSION,'origin':ORIGIN,'cases':cases,'method':'fresh sign-in page for each case; agent-browser actual input fills/click; only value lengths and request method/status retained','raw_credentials_or_bodies_saved':False}
 (OUT/'p01-03-fresh-results.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2)+'\n')
 cli('close')
 p=json.loads((OUT/'progress.json').read_text());p.update({'phase':'P01_03_FRESH_FORM_RECHECK_COMPLETE','updated_at':rec['captured_at'],'current_gate':'P01-03三个fresh sign-in分支已用agent-browser真实控件输入/点击复核；详情见p01-03-fresh-results.json，未保存输入原文或request body。'});(OUT/'progress.json').write_text(json.dumps(p,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps([{'branch':x['branch'],'post_count':x['post_count'],'statuses':[e['status'] for e in x['login_events']],'invalid_input':x['after']['invalidInput'],'invalid_credentials':x['after']['invalidCredentials'],'token_present':x['after']['tokenPresent'],'dom_value_lengths':[x['before']['userLength'],x['before']['passwordLength']]} for x in cases],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
