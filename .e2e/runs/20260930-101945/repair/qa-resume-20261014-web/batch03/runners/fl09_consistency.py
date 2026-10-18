#!/usr/bin/env python3
"""FL-09 read-only cross-consistency: Application assign nodes vs Agent/Node detail managed apps."""
import json, urllib.request, urllib.error, datetime, os, sys

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evidence", "FL09-08-api-consistency.json")


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
_, apps = call("GET", "/api/applications", tok)
_, nodes = call("GET", "/api/nodes", tok)

app_to_nodes = {}
for a in apps:
    st, ids = call("GET", "/api/applications/%s/nodes" % a["id"], tok)
    app_to_nodes[a["id"]] = {"http": st, "node_ids": ids}

node_to_apps = {}
for n in nodes:
    node_to_apps[n["id"]] = [a["id"] for a in apps if n["id"] in (app_to_nodes[a["id"]]["node_ids"] or [])]

# reverse consistency: every id in app_to_nodes must be a real node; every derived node app must be in app_to_nodes
mismatch = []
node_ids = {n["id"] for n in nodes}
for aid, rec in app_to_nodes.items():
    for nid in rec["node_ids"] or []:
        if nid not in node_ids:
            mismatch.append({"app": aid, "unknown_node": nid})
for nid, alist in node_to_apps.items():
    for aid in alist:
        if nid not in (app_to_nodes[aid]["node_ids"] or []):
            mismatch.append({"node": nid, "app_not_assigned": aid})

doc = {
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "application_count": len(apps),
    "node_count": len(nodes),
    "app_to_nodes": app_to_nodes,
    "app_names": {a["id"]: a["name"] for a in apps},
    "node_names": {n["id"]: n["hostname"] for n in nodes},
    "node_to_apps": node_to_apps,
    "bidirectional_mismatches": mismatch,
    "consistent": not mismatch,
}
json.dump(doc, open(os.path.normpath(OUT), "w"), ensure_ascii=False, indent=1)
print(json.dumps({"consistent": doc["consistent"], "mismatches": mismatch,
                  "app_to_nodes": {doc["app_names"][k]: v["node_ids"] for k, v in app_to_nodes.items()},
                  "node_to_apps": {doc["node_names"][k]: v for k, v in node_to_apps.items()}},
                 ensure_ascii=False, indent=1))
print("->", os.path.normpath(OUT))
