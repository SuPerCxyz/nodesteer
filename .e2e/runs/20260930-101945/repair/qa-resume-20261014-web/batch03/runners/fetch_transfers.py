#!/usr/bin/env python3
"""Fetch owned transfers (by nonce source_path) and write full records to evidence."""
import json, os, sys, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
NONCE = "69bdbd0f"
HERE = os.path.dirname(os.path.abspath(__file__))
EV = os.path.normpath(os.path.join(HERE, "..", "evidence"))


def call(m, p, tok=None, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + p, data=d, method=m)
    if d is not None:
        r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, None


c = json.load(open(CRED))
_, lg = call("POST", "/api/login", body={"username": c["admin_username"], "password": c["admin_password"]})
tok = lg["token"]
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(EV, "owned-transfers.json")
st, items = call("GET", "/api/transfers", tok)
owned = [t for t in items if NONCE in (t.get("source_path") or "")]
doc = {"captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "http": st, "total_transfers": len(items), "owned_count": len(owned), "owned": owned}
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
for t in owned:
    print(json.dumps({"id": t["id"], "status": t["status"], "source_node_id": t["source_node_id"],
                      "source_path": t["source_path"], "size": t.get("size"), "sha256": t.get("sha256"),
                      "targets": [{"node_id": x["node_id"], "status": x["status"], "path": x["destination_path"],
                                   "error": x.get("error")} for x in t["targets"]]}, ensure_ascii=False))
print("->", out)
