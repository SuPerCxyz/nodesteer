#!/usr/bin/env python3
"""Fetch executions for the batch-owned task(s) and record them to evidence.
argv: <outfile> [task_id...]   (defaults to all qa-w1410b04-* tasks)
"""
import json, os, sys, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
NONCE = open(os.path.join(HERE, ".nonce")).read().strip()
PREFIX = "qa-w1410b04-%s" % NONCE


def call(m, p, tok=None, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + p, data=d, method=m)
    if d is not None:
        r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r, timeout=25) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


c = json.load(open(CRED))
_, lg = call("POST", "/api/login", body={"username": c["admin_username"], "password": c["admin_password"]})
tok = lg["token"]
out = sys.argv[1]
task_ids = sys.argv[2:]
if not task_ids:
    _, tasks = call("GET", "/api/tasks", tok)
    task_ids = [t["id"] for t in (tasks or []) if PREFIX in t["name"]]
_, nodes = call("GET", "/api/nodes", tok)
node_names = {n["id"]: n["hostname"] for n in (nodes or [])}
_, tasks = call("GET", "/api/tasks", tok)
task_names = {t["id"]: t["name"] for t in (tasks or [])}

doc = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "owned_task_ids": task_ids, "per_task": {}}
for tid in task_ids:
    st, exs = call("GET", "/api/executions?task_id=%s&limit=100" % tid, tok)
    exs = exs or []
    doc["per_task"][tid] = {
        "http": st,
        "task_name": task_names.get(tid),
        "count": len(exs),
        "distinct_ids": len({e["id"] for e in exs}),
        "rows": [{"id": e["id"], "node_id": e.get("node_id"), "node_name": node_names.get(e.get("node_id")),
                  "status": e.get("status"), "exit_code": e.get("exit_code"),
                  "trigger_type": e.get("trigger_type"), "start_time": e.get("start_time"),
                  "end_time": e.get("end_time"), "offline": e.get("offline"),
                  "stdout": (e.get("stdout") or "").strip()[:200], "stderr": (e.get("stderr") or "").strip()[:200],
                  "block_reason": e.get("block_reason") or "", "synced": e.get("synced"),
                  "scheduled_time": e.get("scheduled_time")}
                 for e in exs],
    }
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
for tid, rec in doc["per_task"].items():
    print("task", tid, rec["task_name"], "count", rec["count"], "distinct", rec["distinct_ids"])
    for r in rec["rows"]:
        print("  ", r["id"], r["node_name"], r["status"], "exit", r["exit_code"], "|", r["stdout"][:70])
print("->", out)
