#!/usr/bin/env python3
"""Batch04 fixture/API manager for the repair authority :18080.

Root-granted scope for this batch:
  <=1 owned Application (FL-09 write path), <=4 owned Artifacts,
  <=4 owned FileTransfers, <=2 owned Scripts, <=2 owned Tasks.
NEVER creates/deletes User/Node/Group, never changes node state,
never triggers a real deploy to a production shape.

All fixture names/paths carry the batch nonce prefix qa-w1410b04-<nonce>.

Never prints credentials.
"""
import json, os, sys, uuid, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.path.dirname(HERE)
EV = os.path.join(BATCH, "evidence")
NONCE = open(os.path.join(HERE, ".nonce")).read().strip()
PREFIX = "qa-w1410b04-%s" % NONCE
FIXTURE = os.path.join(BATCH, "fixture-created.json")

NODES = {
    "native-1": "91f44866-8a28-435e-bbda-2f2367126c00",   # 192.168.100.204
    "native-2": "611c02a5-1859-4465-878b-d543b7f6fdec",   # 192.168.100.206
    "native-3": "37989297-3bc8-4c0c-b302-7c6efbdba630",   # 192.168.100.222
    "native-4": "03cb50cc-eab6-4afd-bbe0-f73c4e326773",   # 192.168.100.247
}


def call(method, path, token=None, body=None, raw=None, ctype=None, timeout=30):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if ctype:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read()
            try:
                return r.status, json.loads(txt or b"null")
            except Exception:
                return r.status, txt[:400].decode("utf-8", "replace")
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
    boundary = "----b04" + uuid.uuid4().hex
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
                    ("/api/groups", "groups"), ("/api/users", "users"), ("/api/transfers", "transfers"),
                    ("/api/executions?limit=1", "executions_head")):
        st, data = call("GET", ep, tok)
        if isinstance(data, dict) and "items" in data:
            out[key] = {"http": st, "count": data.get("total"), "item_count": len(data["items"])}
        else:
            out[key] = {"http": st, "count": len(data) if isinstance(data, list) else None}
    return out


def owned(tok):
    """Owned-id inventories: only objects whose name/path/id carries this batch nonce."""
    res = {}
    for ep, key, needle in (("/api/artifacts", "artifacts", "name"), ("/api/applications", "applications", "name"),
                            ("/api/tasks", "tasks", "name"), ("/api/scripts", "scripts", "name"),
                            ("/api/transfers", "transfers", "source_path")):
        st, data = call("GET", ep, tok)
        items = data if isinstance(data, list) else []
        res[key] = [{"id": d["id"], "label": d.get(needle) or d.get("name")} for d in items
                    if PREFIX in json.dumps(d)]
    return res


def cmd_snapshot(outfile):
    tok = login()
    docs = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "base_url": BASE, "nonce": NONCE, "prefix": PREFIX, "counts": counts(tok), "nodes": []}
    _, nodes = call("GET", "/api/nodes", tok)
    if isinstance(nodes, list):
        docs["nodes"] = [{"id": n["id"], "hostname": n["hostname"], "status": n["status"],
                          "mode": n.get("deployment_mode"), "agent_id": n.get("agent_id")} for n in nodes]
    docs["owned_baseline"] = owned(tok)
    for ep, key in (("/api/users", "user_ids"), ("/api/groups", "group_ids"), ("/api/schedules", "schedule_ids")):
        _, data = call("GET", ep, tok)
        docs[key] = sorted(d["id"] for d in data) if isinstance(data, list) else None
    for ep, key in (("/api/artifacts", "artifact_ids"), ("/api/applications", "application_ids"),
                    ("/api/tasks", "task_ids"), ("/api/transfer_ids", "transfer_ids")):
        pass
    _, data = call("GET", "/api/artifacts", tok)
    docs["artifact_ids"] = sorted(d["id"] for d in data) if isinstance(data, list) else None
    _, data = call("GET", "/api/transfers", tok)
    docs["transfer_ids"] = sorted(d["id"] for d in data) if isinstance(data, list) else None
    _, data = call("GET", "/api/applications", tok)
    docs["application_ids"] = sorted(d["id"] for d in data) if isinstance(data, list) else None
    _, data = call("GET", "/api/tasks", tok)
    docs["task_ids"] = sorted(d["id"] for d in data) if isinstance(data, list) else None
    _, data = call("GET", "/api/executions?limit=1", tok)
    docs["executions_total"] = data.get("total") if isinstance(data, dict) else None
    json.dump(docs, open(outfile, "w"), ensure_ascii=False, indent=1)
    print(json.dumps(docs["counts"], ensure_ascii=False))
    print("nodes:", json.dumps(docs["nodes"], ensure_ascii=False))
    print("owned_baseline:", json.dumps(docs["owned_baseline"], ensure_ascii=False))
    print("executions_total:", docs["executions_total"])
    print("snapshot ->", outfile)


def load_fixture():
    if os.path.exists(FIXTURE):
        return json.load(open(FIXTURE))
    return {"nonce": NONCE, "prefix": PREFIX, "artifacts": [], "applications": [], "tasks": [],
            "scripts": [], "transfers": [], "executions": [], "notes": []}


def save_fixture(doc):
    json.dump(doc, open(FIXTURE, "w"), ensure_ascii=False, indent=1)


def cmd_artifact(name, content_len=4096):
    tok = login()
    doc = load_fixture()
    body, ctype = multipart({"name": name, "version": "1.0.0", "arch": "amd64"},
                            "%s.bin" % name, (b"B04" * ((content_len // 3) + 1))[:content_len])
    st, data = call("POST", "/api/artifacts", tok, raw=body, ctype=ctype)
    rec = {"name": name, "http": st, "artifact": data}
    doc["artifacts"].append(rec)
    save_fixture(doc)
    print(json.dumps({"http": st, "id": (data or {}).get("id") if isinstance(data, dict) else None,
                      "name": name, "size": (data or {}).get("size") if isinstance(data, dict) else None},
                     ensure_ascii=False))
    return rec


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "snapshot":
        cmd_snapshot(sys.argv[2])
    elif cmd == "artifact":
        cmd_artifact(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4096)
    elif cmd == "whoami":
        tok = login()
        print(json.dumps(counts(tok), ensure_ascii=False))
        print(json.dumps(owned(tok), ensure_ascii=False))
    else:
        raise SystemExit("unknown cmd %s" % cmd)
