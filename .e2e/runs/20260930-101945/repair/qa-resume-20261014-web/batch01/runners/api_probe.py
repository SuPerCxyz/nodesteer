#!/usr/bin/env python3
"""Read-only API probe for repair authority :18080. Never prints secrets."""
import json, os, sys, urllib.request, urllib.error

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"

def call(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def main():
    c = json.load(open(CRED))
    st, raw = call("POST", "/api/login", body={"username": c["admin_username"], "password": c["admin_password"]})
    out = {"login_http": st}
    if st != 200:
        print(json.dumps(out)); return
    tok = json.loads(raw)["token"]
    out["login_token_acquired"] = True
    endpoints = ["/api/me", "/api/nodes", "/api/applications", "/api/tasks", "/api/schedules",
                 "/api/groups", "/api/scripts", "/api/artifacts", "/api/transfers", "/api/executions",
                 "/api/users", "/api/audit"]
    for ep in endpoints:
        s, r = call("GET", ep, tok)
        info = {"http": s}
        try:
            j = json.loads(r)
            if isinstance(j, list):
                info["count"] = len(j)
            elif isinstance(j, dict):
                for k in ("items", "nodes", "applications", "tasks", "schedules", "executions", "total"):
                    if k in j and isinstance(j[k], list):
                        info["count"] = len(j[k]); break
                info["keys"] = sorted(j.keys())[:12]
        except Exception:
            info["body_len"] = len(r)
        out[ep] = info
    print(json.dumps(out, ensure_ascii=False, indent=1))

main()
