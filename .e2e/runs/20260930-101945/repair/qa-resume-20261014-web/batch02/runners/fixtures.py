#!/usr/bin/env python3
"""Batch02 fixture manager for repair authority :18080.

Commands:
  snapshot <outfile>            read-only counts + ids
  create                        create owned fixtures, write fixture-created.json
  cleanup <fixture-created.json> delete every created object, GET404 verify, write cleanup-results.json

Never prints credentials. All objects are nonce-named and owned by this batch.
"""
import json, os, sys, time, uuid, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.path.dirname(HERE)
EV = os.path.join(BATCH, "evidence")


def call(method, path, token=None, body=None, raw=None, ctype=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if ctype:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read()
            try:
                return r.status, json.loads(txt or b"null")
            except Exception:
                return r.status, txt[:200]
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


def login():
    c = json.load(open(CRED))
    st, lg = call("POST", "/api/login", body={"username": c["admin_username"], "password": c["admin_password"]})
    if st != 200:
        raise SystemExit("login failed http=%s" % st)
    return lg["token"]


def multipart(fields, filename, content):
    boundary = "----b02" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode())
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n" % (boundary, filename)).encode())
    parts.append(content)
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


def counts(tok):
    out = {}
    for ep, key in (("/api/nodes", "nodes"), ("/api/applications", "applications"), ("/api/tasks", "tasks"),
                    ("/api/schedules", "schedules"), ("/api/artifacts", "artifacts"), ("/api/scripts", "scripts"),
                    ("/api/groups", "groups")):
        st, data = call("GET", ep, tok)
        out[key] = {"http": st, "count": len(data) if isinstance(data, list) else None}
    return out


def cmd_snapshot(outfile):
    tok = login()
    st, nodes = call("GET", "/api/nodes", tok)
    docs = {
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "base_url": BASE,
        "counts": counts(tok),
        "node_ids": [{"id": n["id"], "hostname": n["hostname"], "status": n["status"], "labels": n.get("labels")} for n in nodes],
    }
    for ep, key in (("/api/tasks", "task_ids"), ("/api/schedules", "schedule_ids"),
                    ("/api/applications", "application_ids"), ("/api/artifacts", "artifact_ids")):
        _, data = call("GET", ep, tok)
        docs[key] = [d["id"] for d in data]
    json.dump(docs, open(outfile, "w"), ensure_ascii=False, indent=1)
    print("snapshot ->", outfile, json.dumps(docs["counts"], ensure_ascii=False))


def cmd_create():
    tok = login()
    nonce = uuid.uuid4().hex[:6]
    prefix = "qa-w1410b02-%s" % nonce
    _, nodes = call("GET", "/api/nodes", tok)
    node_id = nodes[0]["id"]
    created = {"nonce": nonce, "prefix": prefix, "base_url": BASE,
               "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "nodes_target_id": node_id, "tasks": [], "schedules": [], "applications": [], "artifacts": [],
               "requests": []}
    log = created["requests"]

    def do(method, path, body=None, raw=None, ctype=None):
        st, resp = call(method, path, tok, body=body, raw=raw, ctype=ctype)
        log.append({"method": method, "path": path, "http": st,
                    "id": (resp or {}).get("id") if isinstance(resp, dict) else None})
        return st, resp

    # 11 tasks: 9 node-target command tasks + 2 label-target tasks
    task_ids = []
    for i in range(1, 10):
        body = {"name": "%s-task-%02d" % (prefix, i), "type": "command",
                "command": "/bin/echo %s-%02d" % (prefix, i),
                "target": {"type": "node", "node_ids": [node_id]},
                "parameters": [], "param_values": {}, "timeout": 300, "retry": 0,
                "run_user": "", "offline_policy": "hub_online_required", "enabled": i % 2 == 0}
        st, resp = do("POST", "/api/tasks", body=body)
        if st != 200:
            raise SystemExit("task create failed http=%s %s" % (st, resp))
        task_ids.append(resp["id"])
    label_tasks = {}
    for i, val in ((10, "alpha"), (11, "beta")):
        body = {"name": "%s-task-%02d" % (prefix, i), "type": "command",
                "command": "/bin/echo %s-%02d" % (prefix, i),
                "target": {"type": "label", "label_key": "%s-label" % prefix, "label_value": val},
                "parameters": [], "param_values": {}, "timeout": 120, "retry": 0,
                "run_user": "", "offline_policy": "allow_offline", "enabled": True}
        st, resp = do("POST", "/api/tasks", body=body)
        if st != 200:
            raise SystemExit("label task create failed http=%s %s" % (st, resp))
        task_ids.append(resp["id"])
        label_tasks[i] = {"id": resp["id"], "name": resp["name"], "label_key": body["target"]["label_key"], "label_value": val}
    created["tasks"] = task_ids
    created["label_tasks"] = label_tasks

    # 2 schedules on task-01 (varied 7-column values)
    sched_defs = [
        {"task_id": task_ids[0], "type": "cron", "expression": "0 3 * * *", "timezone": "Asia/Shanghai",
         "execution_owner": "hub", "offline_policy": "allow_offline", "misfire_policy": "run_once", "enabled": True},
        {"task_id": task_ids[0], "type": "interval", "interval_sec": 3600, "timezone": "UTC",
         "execution_owner": "agent", "offline_policy": "hub_online_required", "misfire_policy": "skip", "enabled": False},
    ]
    for sd in sched_defs:
        st, resp = do("POST", "/api/schedules", body=sd)
        if st != 200:
            raise SystemExit("schedule create failed http=%s %s" % (st, resp))
        created["schedules"].append(resp["id"])

    # 10 applications -> 11 rows total (pageSize min 10 => 2 pages)
    for i in range(1, 11):
        body = {"name": "%s-app-%02d" % (prefix, i), "description": "batch02 fixture",
                "version": "v%02d" % (11 - i), "binary_path": "/tmp/%s/app-%02d/binary" % (prefix, i),
                "arguments": [], "environment": {}, "config": "qa=1",
                "unit_name": "%s-app-%02d.service" % (prefix, i), "artifact_id": "",
                "health_check": None}
        st, resp = do("POST", "/api/applications", body=body)
        if st != 200:
            raise SystemExit("application create failed http=%s %s" % (st, resp))
        created["applications"].append(resp["id"])

    # 10 artifacts -> 11 rows total
    for i in range(1, 11):
        content = ("batch02 %s artifact %02d\n" % (prefix, i)).encode() * i
        raw, ct = multipart({"name": "%s-art-%02d" % (prefix, i), "version": "v1",
                             "architecture": "amd64" if i % 2 else "arm64"},
                            "%s-art-%02d.bin" % (prefix, i), content)
        st, resp = do("POST", "/api/artifacts", raw=raw, ctype=ct)
        if st != 200:
            raise SystemExit("artifact upload failed http=%s %s" % (st, resp))
        created["artifacts"].append(resp["id"])

    path = os.path.join(BATCH, "fixture-created.json")
    json.dump(created, open(path, "w"), ensure_ascii=False, indent=1)
    print("created nonce=%s tasks=%d schedules=%d applications=%d artifacts=%d" % (
        nonce, len(created["tasks"]), len(created["schedules"]), len(created["applications"]), len(created["artifacts"])))
    print("fixture file ->", path)


def cmd_cleanup(fixture_file):
    tok = login()
    fx = json.load(open(fixture_file))
    plan = ([("artifact", u, "/api/artifacts/%s") for u in fx["artifacts"]] +
            [("application", u, "/api/applications/%s") for u in fx["applications"]] +
            [("schedule", u, "/api/schedules/%s") for u in fx["schedules"]] +
            [("task", u, "/api/tasks/%s") for u in fx["tasks"]])
    results = []
    for kind, uid, tmpl in plan:
        st, resp = call("DELETE", tmpl % uid, tok)
        vst, _ = call("GET", tmpl % uid, tok)
        results.append({"kind": kind, "id": uid, "delete_http": st, "recheck_get_http": vst,
                        "recheck_expected": 404, "ok": st in (200, 204, 404) and vst == 404})
    out = {"cleaned_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "fixture_file": fixture_file, "nonce": fx["nonce"], "total": len(results),
           "all_404_verified": all(r["ok"] for r in results), "results": results,
           "counts_after_cleanup": counts(tok)}
    path = os.path.join(EV, "cleanup-get404-verification.json")
    json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
    print("cleanup total=%d all_404_verified=%s" % (len(results), out["all_404_verified"]))
    print("counts_after_cleanup", json.dumps(out["counts_after_cleanup"], ensure_ascii=False))
    print("->", path)
    bad = [r for r in results if not r["ok"]]
    if bad:
        print("FAILED", json.dumps(bad, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1] == "snapshot":
        cmd_snapshot(sys.argv[2])
    elif sys.argv[1] == "create":
        cmd_create()
    elif sys.argv[1] == "cleanup":
        cmd_cleanup(sys.argv[2])
    else:
        raise SystemExit("unknown command")
