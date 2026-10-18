#!/usr/bin/env python3
"""Poll /api/transfers and record status transitions for every batch-owned transfer
(source_path contains the batch nonce). argv: <outfile> <seconds>"""
import json, os, sys, time, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
NONCE = open(os.path.join(HERE, ".nonce")).read().strip()


def call(m, p, tok=None, body=None):
    d = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + p, data=d, method=m)
    if d is not None:
        r.add_header("Content-Type", "application/json")
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    try:
        with urllib.request.urlopen(r, timeout=15) as x:
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
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 30
t0 = time.time()
seen = {}          # transfer_id -> last signature
timeline = {}      # transfer_id -> [ {t, status, targets, sha} ]
while time.time() - t0 < dur:
    st, items = call("GET", "/api/transfers", tok)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if isinstance(items, list):
        for t in items:
            if NONCE not in (t.get("source_path") or ""):
                continue
            tid = t["id"]
            sig = json.dumps({"s": t["status"], "sha": bool(t.get("sha256")),
                              "tg": [[x["node_id"][:8], x["status"]] for x in t.get("targets", [])]}, sort_keys=True)
            if seen.get(tid) != sig:
                seen[tid] = sig
                timeline.setdefault(tid, []).append({
                    "t": now, "elapsed_s": round(time.time() - t0, 3), "status": t["status"],
                    "sha256_present": bool(t.get("sha256")), "size": t.get("size"),
                    "targets": [{"node_id": x["node_id"], "status": x["status"], "error": x.get("error")}
                                for x in t.get("targets", [])],
                })
    time.sleep(0.02)
json.dump({"poll_seconds": dur, "nonce": NONCE, "timeline": timeline}, open(out, "w"), ensure_ascii=False, indent=1)
for tid, tl in timeline.items():
    print(tid, [x["status"] for x in tl])
print("->", out)
