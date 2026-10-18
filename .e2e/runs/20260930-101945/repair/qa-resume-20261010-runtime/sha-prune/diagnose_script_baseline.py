import json,hashlib
from pathlib import Path
from urllib.request import *
from urllib.parse import urlsplit
from urllib.error import HTTPError
c=json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text());base=c['hub_url'].rstrip('/');tok=c['token']
if urlsplit(base).port!=18080:raise SystemExit('wrong port')
op=build_opener(ProxyHandler({}))
def g(path):
 with op.open(Request(base+path,headers={'Authorization':'Bearer '+tok}),timeout=15) as r:return json.load(r)
ss=g('/api/scripts');ts=g('/api/tasks');
ss=[{'id':x['id'],'name':x['name'],'rev':x.get('revision'),'sha256':x.get('sha256'),'content_bytes':len(x.get('content','').encode())} for x in ss if 'qa-resume-20261010-sha-prune-' in x.get('name','')]
ts=[{'id':x['id'],'name':x['name'],'type':x.get('type'),'script_id':x.get('script_id')} for x in ts if 'qa-resume-20261010-sha-prune-' in x.get('name','')]
try:
 es=g('/api/executions'); es=[{'id':e.get('id'),'task_id':e.get('task_id'),'node_id':e.get('node_id'),'status':e.get('status'),'stdout_sha256':hashlib.sha256((e.get('stdout') or '').encode()).hexdigest(),'stderr':(e.get('stderr') or '')[:400]} for e in es if e.get('node_id')=='c77b8a84-8085-4ea8-8532-de171761854b']
except Exception as e: es=[{'list_error':type(e).__name__}]
print(json.dumps({'scripts':ss,'tasks':ts,'execution_recent':es[-10:]},indent=2))
