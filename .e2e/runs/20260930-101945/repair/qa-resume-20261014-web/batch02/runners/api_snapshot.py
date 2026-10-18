#!/usr/bin/env python3
"""Read-only API snapshot writer. argv: <endpoint> <outfile>. Never prints secrets."""
import json, sys, urllib.request, urllib.error, datetime
BASE="http://192.168.100.209:18080"
CRED="/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
def call(m,p,tok=None,body=None):
    d=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+p,data=d,method=m)
    if d is not None: r.add_header("Content-Type","application/json")
    if tok: r.add_header("Authorization","Bearer "+tok)
    try:
        with urllib.request.urlopen(r,timeout=20) as x: return x.status, json.loads(x.read() or b'null')
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b'null')
        except Exception: return e.code, None
c=json.load(open(CRED))
st,lg=call("POST","/api/login",body={"username":c["admin_username"],"password":c["admin_password"]})
tok=lg["token"]
ep=sys.argv[1]; out=sys.argv[2]
st,data=call("GET",ep,tok)
doc={"captured_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"endpoint":ep,"http":st,"count":len(data) if isinstance(data,list) else None,"data":data}
json.dump(doc,open(out,"w"),ensure_ascii=False,indent=1)
print(out, "http", st, "count", doc["count"])
