import json,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';SCRIPT='20a79944-5581-494b-83d8-5b365738c1e4';V2SHA='2145f8d114e17ca06d69e640e328662ca3ee370f17922909b1dc4e8477b94387';deadline='2026-10-10T06:00:32Z'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def get(path):
 with op.open(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=10) as r:return json.load(r)
def utc():return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
def save_progress(patch):
 p=H/'progress.json';d=json.loads(p.read_text());d.update(patch,updated_at_utc=utc());p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
start_wall=utc();start_mono=time.monotonic();limit=datetime.fromisoformat(deadline.replace('Z','+00:00')).timestamp();timeline=[];recovered=False;last=None
while time.time()<limit:
 n=get('/api/nodes/'+NODE);s=get('/api/scripts/'+SCRIPT)
 last={'at_utc':utc(),'node_status':n.get('status'),'sync_status':n.get('sync_status'),'node_global_revision':n.get('global_revision'),'script_revision':s.get('revision'),'script_sha256':s.get('sha256'),'script_enabled':s.get('enabled'),'last_seen':n.get('last_seen')}
 if not timeline or any(last[k]!=timeline[-1][k] for k in ['node_status','sync_status','node_global_revision','script_revision','script_sha256']):timeline.append(last)
 recovered=n.get('status')=='online' and n.get('sync_status')=='synced' and s.get('revision')==2 and s.get('sha256')==V2SHA
 if recovered:break
 elapsed=round(time.monotonic()-start_mono,3)
 save_progress({'status':'WAITING_NORMAL_REVISION_RECOVERY','recovery_last_observation':last,'recovery_stage_elapsed_monotonic_seconds':elapsed})
 time.sleep(1)
end_wall=utc();elapsed=round(time.monotonic()-start_mono,3);out={'stage_started_at_utc':start_wall,'stage_ended_at_utc':end_wall,'stage_elapsed_monotonic_seconds':elapsed,'recovery_deadline_utc':deadline,'recovered':recovered,'timeline':timeline,'last_observation':last,'credentials_emitted':False}
p=H/'recovery-stage-01.json';p.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');save_progress({'status':'V2_HUB_RECOVERY_OBSERVED' if recovered else 'WAITING_NORMAL_REVISION_RECOVERY','recovery_evidence':p.name,'recovery_last_observation':last,'recovery_stage_elapsed_monotonic_seconds':elapsed,'recovered':recovered});print(json.dumps({'recovered':recovered,'stage_started_at_utc':start_wall,'stage_ended_at_utc':end_wall,'stage_elapsed_monotonic_seconds':elapsed,'timeline':timeline,'last_observation':last},ensure_ascii=False))
