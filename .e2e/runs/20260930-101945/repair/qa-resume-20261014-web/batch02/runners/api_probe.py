#!/usr/bin/env python3
"""Read-only API probe for repair authority :18080. Never prints secrets."""
import json, sys, urllib.request, urllib.error

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
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def main():
    c = json.load(open(CRED))
    st, lg = call("POST", "/api/login", body={"username": c["admin_username"], "password": c["admin_password"]})
    print("login_http", st)
    tok = lg["token"]
    for ep in sys.argv[1:] or ["/api/nodes", "/api/applications", "/api/tasks", "/api/schedules",
                               "/api/artifacts", "/api/groups", "/api/scripts"]:
        s, data = call("GET", ep, tok)
        n = len(data) if isinstance(data, list) else None
        print("=== %s http=%s count=%s" % (ep, s, n))
        if ep == "/api/nodes" and isinstance(data, list):
            for x in data:
                print(json.dumps({k: x.get(k) for k in ("id", "hostname", "status", "labels")}, ensure_ascii=False))
        elif isinstance(data, list) and data:
            print(json.dumps(data[:2], ensure_ascii=False)[:1500])


main()
