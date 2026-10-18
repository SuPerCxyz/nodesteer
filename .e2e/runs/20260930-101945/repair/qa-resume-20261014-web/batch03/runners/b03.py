#!/usr/bin/env python3
"""Batch03 fixture/API manager for the repair authority :18080.

Scope (Root-granted): <=2 owned FileTransfers, <=12 owned Artifacts.
NEVER creates/deletes User/Node/Application/Script and never changes node state,
never triggers a real run/deploy.

Commands:
  snapshot <outfile>            read-only counts + owned-id inventories
  create-artifacts <n>          create n owned artifacts (nonce named)
  cleanup-artifacts <fixture>   delete owned artifacts, GET404 verify
  ledger                        print fixture-created summary

Never prints credentials.
"""
import json, os, sys, uuid, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.path.dirname(HERE)
EV = os.path.join(BATCH, "evidence")
NONCE = "69bdbd0f"
PREFIX = "qa-w1410b03-%s" % NONCE
FIXTURE = os.path.join(BATCH, "fixture-created.json")
# known repair nodes (read-only identifiers)
NODE_NATIVE1 = "91f44866-8a28-435e-bbda-2f2367126c00"   # 192.168.100.204  online
NODE_NATIVE3 = "37989297-3bc8-4c0c-b302-7c6efbdba630"   # 192.168.100.222  online
NODE_OFFLINE = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"   # offline
NODE_HOST001 = "f6a79d23-2410-450c-ac39-64dd70c9daa1"   # docker_host_integration on hub host 209, online


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
                return r.status, txt[:300]
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
    boundary = "----b03" + uuid.uuid4().hex
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
                    ("/api/groups", "groups"), ("/api/users", "users"), ("/api/transfers", "transfers")):
        st, data = call("GET", ep, tok)
        out[key] = {"http": st, "count": len(data) if isinstance(data, list) else None}
    return out


def cmd_snapshot(outfile):
    tok = login()
    docs = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "base_url": BASE, "nonce": NONCE, "prefix": PREFIX, "counts": counts(tok), "node_ids": []}
    _, nodes = call("GET", "/api/nodes", tok)
    docs["node_ids"] = [{"id": n["id"], "hostname": n["hostname"], "status": n["status"],
                         "mode": n.get("deployment_mode")} for n in nodes]
    for ep, key in (("/api/tasks", "task_ids"), ("/api/schedules", "schedule_ids"),
                    ("/api/applications", "application_ids"), ("/api/artifacts", "artifact_ids"),
                    ("/api/transfers", "transfer_ids"), ("/api/users", "user_ids")):
        _, data = call("GET", ep, tok)
        docs[key] = [d["id"] for d in data] if isinstance(data, list) else None
    json.dump(docs, open(outfile, "w"), ensure_ascii=False, indent=1)
    print("snapshot ->", outfile, json.dumps(docs["counts"], ensure_ascii=False))


def cmd_create_artifacts(n):
    tok = login()
    n = int(n)
    created = {"nonce": NONCE, "prefix": PREFIX, "created_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "artifacts": [], "requests": []}
    if os.path.exists(FIXTURE):
        created = json.load(open(FIXTURE))
        created.setdefault("artifacts", [])
        created.setdefault("requests", [])
    for i in range(1, n + 1):
        content = ("%s artifact %02d\n" % (PREFIX, i)).encode() * i
        name = "%s-art-%02d" % (PREFIX, i)
        raw, ct = multipart({"name": name, "version": "v%02d" % (n + 1 - i),
                             "architecture": "amd64" if i % 2 else "arm64"},
                            "%s-art-%02d.bin" % (PREFIX, i), content)
        st, resp = call("POST", "/api/artifacts", tok, raw=raw, ctype=ct)
        created["requests"].append({"method": "POST", "path": "/api/artifacts", "http": st,
                                    "id": (resp or {}).get("id") if isinstance(resp, dict) else None, "name": name})
        if st != 200:
            raise SystemExit("artifact upload failed http=%s %s" % (st, resp))
        created["artifacts"].append({"id": resp["id"], "name": name, "version": resp.get("version"),
                                     "architecture": resp.get("architecture"), "size": resp.get("size"),
                                     "sha256": resp.get("sha256")})
    json.dump(created, open(FIXTURE, "w"), ensure_ascii=False, indent=1)
    print("created artifacts=%d -> %s" % (len(created["artifacts"]), FIXTURE))


def cmd_cleanup_artifacts(fixture):
    tok = login()
    fx = json.load(open(fixture))
    results = []
    for a in fx.get("artifacts", []):
        st, _ = call("DELETE", "/api/artifacts/%s" % a["id"], tok)
        vst, _ = call("GET", "/api/artifacts/%s" % a["id"], tok)
        results.append({"kind": "artifact", "id": a["id"], "name": a.get("name"), "delete_http": st,
                        "recheck_get_http": vst, "ok": st in (200, 204, 404) and vst == 404})
    out = {"cleaned_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "total": len(results),
           "all_404_verified": all(r["ok"] for r in results), "results": results, "counts_after": counts(tok)}
    json.dump(out, open(os.path.join(EV, "cleanup-artifacts-get404.json"), "w"), ensure_ascii=False, indent=1)
    print("artifact cleanup total=%d all_404=%s" % (len(results), out["all_404_verified"]))
    print("counts_after", json.dumps(out["counts_after"], ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "snapshot":
        cmd_snapshot(sys.argv[2])
    elif cmd == "create-artifacts":
        cmd_create_artifacts(sys.argv[2])
    elif cmd == "cleanup-artifacts":
        cmd_cleanup_artifacts(sys.argv[2])
    elif cmd == "counts":
        print(json.dumps(counts(login()), ensure_ascii=False, indent=1))
    else:
        raise SystemExit("unknown command")
