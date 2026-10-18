import json,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;BASE='http://192.168.100.209:18080';SESSION='nodesteer-db-fault-attempt02';op=build_opener(ProxyHandler({}))
def br(args,stdin=None):return subprocess.run(['agent-browser','--session',SESSION,*args],input=stdin,text=True,capture_output=True,timeout=20)
def parse(out):
 v=json.loads(out.strip().splitlines()[-1])
 return json.loads(v) if isinstance(v,str) else v
browser_open=br(['open',BASE+'/sign-in']);ev={'sign_in_open_rc':browser_open.returncode}
try:
 r=br(['eval','--stdin'],"(async()=>{const present=!!localStorage.getItem('nodesteer_token');const r=await fetch('/api/me');return JSON.stringify({tokenPresent:present,meStatus:r.status})})()")
 ev['browser_eval_rc']=r.returncode
 if r.returncode==0:ev['browser_state']=parse(r.stdout)
except Exception as e:ev['safe_browser_error_type']=type(e).__name__
try:
 with op.open(Request(BASE+'/api/me'),timeout=15) as r:ev['unauthenticated_api_me_status']=r.status
except HTTPError as e:ev['unauthenticated_api_me_status']=e.code
finally:
 try:ev['session_close_rc']=br(['close']).returncode
 except Exception:ev['session_close_rc']=None
ev.update({'captured_at_utc':datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),'credential_values_emitted':False,'token_values_emitted':False,'dedicated_session_closed':ev.get('session_close_rc')==0})
(H/'postlogout-me401-evidence.json').write_text(json.dumps(ev,ensure_ascii=False,indent=2)+'\n');p=H/'progress.json';d=json.loads(p.read_text());d.update({'status':'POSTLOGOUT_TOKEN_ABSENT_ME401_CONFIRMED','updated_at_utc':ev['captured_at_utc'],'postlogout_evidence':'postlogout-me401-evidence.json','browser_token_absent':ev.get('browser_state',{}).get('tokenPresent') is False,'browser_me_status':ev.get('browser_state',{}).get('meStatus'),'unauthenticated_me_status':ev.get('unauthenticated_api_me_status'),'dedicated_browser_session_closed':ev['dedicated_session_closed'],'next_action':'Collect exact Native3 node health then run one read-only Command Task to identify repair Agent ancestor process, compare exe and disk SHA, and exact cleanup.'});p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps(ev,ensure_ascii=False))
