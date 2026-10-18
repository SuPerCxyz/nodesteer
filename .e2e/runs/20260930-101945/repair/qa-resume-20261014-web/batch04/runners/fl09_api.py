#!/usr/bin/env python3
"""Read-only FL-09 API consistency snapshot for a given owned application id.
argv: <outfile> [app_id]
"""
import json, os, sys, urllib.request, urllib.error, datetime

BASE = "http://192.168.100.209:18080"
CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = os.path.dirname(HERE)
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
out = sys.argv[1]
target_app = sys.argv[2] if len(sys.argv) > 2 else None

_, apps = call("GET", "/api/applications", tok)
_, nodes = call("GET", "/api/nodes", tok)
app_to_nodes = {}
for a in apps:
    st, ids = call("GET", "/api/applications/%s/nodes" % a["id"], tok)
    app_to_nodes[a["id"]] = {"http": st, "node_ids": ids}
node_names = {n["id"]: n["hostname"] for n in nodes}
node_to_apps = {n["id"]: [a["id"] for a in apps if n["id"] in (app_to_nodes[a["id"]]["node_ids"] or [])]
                 for n in nodes}
mismatch = []
for aid, rec in app_to_nodes.items():
    for nid in rec["node_ids"] or []:
        if nid not in node_names:
            mismatch.append({"app": aid, "unknown_node": nid})
for nid, alist in node_to_apps.items():
    for aid in alist:
        if nid not in (app_to_nodes[aid]["node_ids"] or []):
            mismatch.append({"node": nid, "app_not_assigned": aid})

app_name_by_id = {a["id"]: a["name"] for a in apps}
doc = {
    "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "owned_app_id": target_app,
    "application_count": len(apps),
    "node_count": len(nodes),
    "app_names": {a["id"]: a["name"] for a in apps},
    "app_to_nodes": {aid: {"http": r["http"], "node_ids": r["node_ids"],
                           "node_names": [node_names.get(n, n) for n in (r["node_ids"] or [])]}
                     for aid, r in app_to_nodes.items()},
    "node_to_apps": {node_names[n]: [doc_name for doc_name in a_list] for n, a_list in node_to_apps.items()},
    "node_to_app_names": {node_names[n]: [app_name_by_id.get(x) or x for x in a_list]
                          for n, a_list in node_to_apps.items()},
    "bidirectional_mismatches": mismatch,
    "consistent": not mismatch,
    "non_owned_apps_untouched": {a["id"]: app_to_nodes[a["id"]]["node_ids"] for a in apps if PREFIX not in a["name"]},
}
json.dump(doc, open(out, "w"), ensure_ascii=False, indent=1)
print(json.dumps({"owned_app_id": target_app,
                  "owned_app_nodes": doc["app_to_nodes"].get(target_app, {}),
                  "node_to_app_names": doc["node_to_app_names"],
                  "consistent": doc["consistent"], "mismatches": mismatch,
                  "non_owned_apps_untouched": doc["non_owned_apps_untouched"]}, ensure_ascii=False, indent=1))
