#!/usr/bin/env python3
import json, os, urllib.request, urllib.error
from pathlib import Path
QA=Path(__file__).resolve().parent
PRIVATE=Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONN=json.loads((PRIVATE/"repair-connection.json").read_text())
users=PRIVATE/"playwright/resume-20261004-safe01_21/ui-users.jsonl"
resources=PRIVATE/"playwright/resume-20261004-safe01_21/resources.jsonl"
def api(base,path,method="GET",body=None,token=None):
 d=None if body is None else json.dumps(body).encode(); h={}
 if token: h["Authorization"]="Bearer "+token
 if d is not None: h["Content-Type"]="application/json"
 try:
  with urllib.request.urlopen(urllib.request.Request(base+path,data=d,headers=h,method=method),timeout=15) as r:
   raw=r.read(); return r.status, json.loads(raw) if raw else None
 except urllib.error.HTTPError as e: return e.code,None
rows=[json.loads(x) for x in users.read_text().splitlines() if x.strip()]
cred=next(x for x in reversed(rows) if x.get("final") is True)
uid=next(x["id"] for x in reversed([json.loads(y) for y in resources.read_text().splitlines() if y.strip()]) if x.get("path")=="/api/users" and x.get("status")=="retained_account")
base=CONN["hub_url"].rstrip("/")
ls,login=api(base,"/api/login","POST",{"username":cred["username"],"password":cred["password"]})
token=login.get("token") if login else None
out={"case":"owned Viewer session logout invalidates only that token","login_http":ls,"token_written":False,"user_id":uid}
if token:
 out["logout_http"]=api(base,"/api/logout","POST",token=token)[0]
 out["same_token_me_after_logout_http"]=api(base,"/api/me",token=token)[0]
else: out["logout_http"]=None; out["same_token_me_after_logout_http"]=None
out["repair_admin_users_http"]=api(base,"/api/users",token=CONN["token"])[0]
out["final_role"]=next((x.get("role") for x in (api(base,"/api/users",token=CONN["token"])[1] or []) if x.get("id")==uid),None)
out["status"]="passed" if ls==200 and out["logout_http"]==200 and out["same_token_me_after_logout_http"]==401 and out["repair_admin_users_http"]==200 and out["final_role"]=="viewer" else "failed"
path=QA/"user-role-session-cleanup.json"
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
with os.fdopen(fd,"w") as f: json.dump(out,f,indent=2);f.write("\n")
print(json.dumps(out))
