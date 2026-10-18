import json,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler,Request,build_opener
H=Path(__file__).resolve().parent;priv=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=priv['hub_url'].rstrip('/');token=priv['token'];op=build_opener(ProxyHandler({}));NODE='15961ef7-81b4-43f3-80c8-1b6977ca2aea';SCRIPT='6ecb3dee-43c3-432a-95cc-8fa3b30a91e1';V2SHA='f9a6eeffbcb3da671d1c333c75f5af6bef580526956091341d08bd7ad8fc8e8b'
if urlsplit(base).port!=18080:raise SystemExit('repair endpoint guard failed')
def get(path):
 with op.open(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=10) as r:return json.load(r)
start=time.monotonic();deadline=start+20;timeline=[];recovered=False
while time.monotonic()<deadline:
 n=get('/api/nodes/'+NODE);s=get('/api/scripts/'+SCRIPT);z={'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'node_status':n.get('status'),'sync_status':n.get('sync_status'),'node_global_revision':n.get('global_revision'),'script_revision':s.get('revision'),'script_sha256':s.get('sha256'),'last_seen':n.get('last_seen')}
 if not timeline or {k:z[k] for k in ['node_status','sync_status','node_global_revision','script_revision','script_sha256']}!={k:timeline[-1][k] for k in ['node_status','sync_status','node_global_revision','script_revision','script_sha256']}:timeline.append(z)
 recovered=n.get('status')=='online' and n.get('sync_status')=='synced' and s.get('revision')==2 and s.get('sha256')==V2SHA and (n.get('global_revision') or 0)>=1362
 if recovered:break
 time.sleep(1)
out={'started_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(start)),'ended_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'window_seconds':round(time.monotonic()-start,2),'recovered':recovered,'timeline':timeline,'last_observation':z,'credentials_emitted':False}
p=H/'recovery-api-timeline.json';old=json.loads(p.read_text()) if p.exists() else {'observations':[]};old['observations'].append(out);p.write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'recovered':recovered,'window_seconds':out['window_seconds'],'timeline':timeline,'last_observation':z},ensure_ascii=False))
