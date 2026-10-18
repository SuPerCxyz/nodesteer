#!/usr/bin/env python3
"""Fetch every batch-owned FileTransfer (source_path carries the batch nonce).
argv: <outfile>"""
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
st, items = call("GET", "/api/transfers", tok)
items = items or []
owned = [t for t in items if PREFIX in (t.get("source_path") or "")]
doc = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "http": st, "total_transfers": len(items), "owned_count": len(owned),
       "delete_endpoint_probe": call("DELETE", "/api/transfers/00000000-0000-0000-0000-000000000000", tok),
       "owned": owned}
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
for t in owned:
    print(json.dumps({"id": t["id"], "status": t["status"], "source_node_id": t["source_node_id"],
                      "source_path": t["source_path"], "size": t.get("size"), "sha256": t.get("sha256"),
                      "error": t.get("error"),
                      "targets": [{"node_id": x["node_id"], "status": x["status"], "path": x["destination_path"],
                                   "error": (x.get("error") or "")[:80]} for x in t["targets"]]},
                     ensure_ascii=False))
print("total_transfers", len(items), "owned", len(owned))
print("DELETE /api/transfers/{id} probe ->", doc["delete_endpoint_probe"])
print("->", out)
