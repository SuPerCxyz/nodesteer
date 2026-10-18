#!/usr/bin/env python3
"""清理 owned fixture + 生成 fixture-ledger.json / final-cleanup-guard.json + 终态 GET404 复核。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import j, login, as_list  # noqa: E402

PFX = "qa-webgap-6456ada2"
NATIVE2_IP = "192.168.100.206"
NODE_DIR = "/var/lib/" + PFX
BATCH = Path(__file__).resolve().parent.parent
EV = BATCH / "evidence"
tok = login()

# --- owned ids ---
setup = json.load(open(EV / "fixtures-setup.json"))
ids = setup["ids"]
p16a = json.load(open(EV / "P16-artifact-upload.json")) if (EV / "P16-artifact-upload.json").exists() else {}
p16b = json.load(open(EV / "P16-artifact-upload-2.json"))
artifacts = []
for src in (p16a, p16b):
    for a in src.get("created", []):
        if a["kind"] == "artifact" and a["id"] not in [x["id"] for x in artifacts]:
            artifacts.append(a)

transfers = []
for f in ("P22-transfers.json", "P22-07-ui-start.json", "P22-07-ui-slow-loading.json"):
    p = EV / f
    if not p.exists():
        continue
    d = json.load(open(p))
    for t in d.get("transfers", []):
        transfers.append(t)
    if d.get("ui_transfer_id"):
        transfers.append({"id": d["ui_transfer_id"], "role": "P22-07-ui-start"})
    if d.get("slow_transfer_id"):
        transfers.append({"id": d["slow_transfer_id"], "role": "P22-07-slow-loading"})
seen = set()
transfers = [t for t in transfers if not (t["id"] in seen or seen.add(t["id"]))]

EXEC_IDS = ["f04c7f2d-da35-4f39-a01b-ea1e166dd133", "ed7b64db-e4da-45da-98b9-d75dca86f653",
            "c5cd0511-e20e-4dfc-a4f6-3fe02b511175"]

counts_baseline = json.load(open(EV / "recon.json"))["counts_baseline"]
for k, v in list(counts_baseline.items()):
    if v.get("count") is None:
        counts_baseline[k]["count"] = 0


def c(tok_):
    out = {}
    for ep in ["/api/nodes", "/api/applications", "/api/tasks", "/api/schedules", "/api/scripts",
               "/api/artifacts", "/api/transfers", "/api/executions", "/api/users"]:
        st, payload = j("GET", ep, tok_)
        lst = as_list(payload)
        out[ep] = {"http": st, "count": len(lst)}
    return out


ledger = {"batch_id": "qa-resume-20261016-webgap", "owner": "web (WEB-GAP-20261016)", "nonce": "6456ada2",
          "prefix": PFX, "authority": "http://192.168.100.209:18080 (repair)",
          "authority_type": "repair", "primary_8080_touched": False,
          "verified_build": "dddee4f0-e2e-repair-20261016",
          "node_scope": "native-2 (192.168.100.206) only",
          "fixtures": [], "retained_history": [], "cleanup_started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

# --- delete order: schedule -> task -> script -> artifacts ---
results = {}


def get404(kind, id_, path):
    st, raw = j("GET", path, tok)
    results[kind + ":" + id_] = st
    return st


# schedule
sid = ids.get("schedule_id")
if sid:
    st, raw = j("DELETE", "/api/schedules/%s" % sid, tok)
    g = get404("schedule", sid, "/api/schedules/%s" % sid)
    ledger["fixtures"].append({"kind": "schedule", "id": sid, "delete_http": st, "get_after": g,
                               "purpose": "P05B-08 node-detail schedule link", "deleted": g == 404, "delete_http": st})
tok = login()

# task
tid = ids.get("task_id")
if tid:
    st, raw = j("DELETE", "/api/tasks/%s" % tid, tok)
    g = get404("task", tid, "/api/tasks/%s" % tid)
    ledger["fixtures"].append({"kind": "task", "id": tid, "delete_http": st, "get_after": g,
                               "purpose": "P03-02 dashboard running row / P05B-10 execution table task link / X20 long log",
                               "deleted": g == 404})
tok = login()

# script
scid = ids.get("script_id")
if scid:
    st, raw = j("DELETE", "/api/scripts/%s" % scid, tok)
    g = get404("script", scid, "/api/scripts/%s" % scid)
    ledger["fixtures"].append({"kind": "script", "id": scid, "delete_http": st, "get_after": g,
                               "purpose": "X20 long command/params + long log producer", "deleted": g == 404})
tok = login()

# artifacts
for a in artifacts:
    st, raw = j("DELETE", "/api/artifacts/%s" % a["id"], tok)
    g = get404("artifact", a["id"], "/api/artifacts/%s" % a["id"])
    rec = dict(a)
    rec.update({"delete_http": st, "get_after": g, "deleted": g == 404})
    ledger["fixtures"].append(rec)
tok = login()

# --- transfers / executions: retained history (no DELETE endpoint) ---
for t in transfers:
    st, d = j("GET", "/api/transfers/%s" % t["id"], tok)
    ledger["retained_history"].append({
        "kind": "transfer", "id": t["id"], "role": t.get("role"), "get_http": st,
        "status": d.get("status") if isinstance(d, dict) else None,
        "source_path": d.get("source_path") if isinstance(d, dict) else None,
        "targets": [{"node_id": x.get("node_id"), "status": x.get("status"), "error": x.get("error"),
                     "destination_path": x.get("destination_path")} for x in ((d or {}).get("targets") or [])] if isinstance(d, dict) else None,
        "delete_supported": False,
        "reason": "Hub 无 DELETE /api/transfers 端点（DELETE 返回 403），终态记录保留为历史"})
for eid in EXEC_IDS:
    st, d = j("GET", "/api/executions/%s" % eid, tok)
    ledger["retained_history"].append({"kind": "execution", "id": eid, "get_http": st,
                                       "status": d.get("status") if isinstance(d, dict) else None,
                                       "stdout_len": len((d or {}).get("stdout") or "") if isinstance(d, dict) else None,
                                       "delete_supported": False,
                                       "reason": "DELETE /api/executions/{id} 返回 405 method not allowed"})

# --- node-side cleanup on native-2 ---
ssh = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                      "root@" + NATIVE2_IP,
                      "rm -rf %s && (ls -d %s 2>&1 || true) && (ls -d /var/lib/nodesteer-qa-webgap-* 2>&1 || true)" % (NODE_DIR, NODE_DIR)],
                     capture_output=True, text=True, timeout=60)
ledger["node_side"] = {"host": NATIVE2_IP, "dir": NODE_DIR, "removed": ssh.returncode == 0,
                        "stdout": ssh.stdout.strip()[:300], "stderr": ssh.stderr.strip()[:200]}

# --- counts after ---
counts_after = c(tok)
owned_leftovers = {
    "artifacts": [{"id": a["id"], "name": a.get("name")} for a in as_list(j("GET", "/api/artifacts", tok)[1])
                  if PFX in json.dumps(a, ensure_ascii=False)],
    "tasks": [t for t in as_list(j("GET", "/api/tasks", tok)[1]) if PFX in json.dumps(t, ensure_ascii=False)],
    "schedules": [s for s in as_list(j("GET", "/api/schedules", tok)[1]) if PFX in json.dumps(s, ensure_ascii=False)],
    "scripts": [s for s in as_list(j("GET", "/api/scripts", tok)[1]) if PFX in json.dumps(s, ensure_ascii=False)],
    "nodes": [n for n in as_list(j("GET", "/api/nodes", tok)[1]) if PFX in json.dumps(n, ensure_ascii=False)],
}
transfers_all = as_list(j("GET", "/api/transfers", tok)[1])
owned_transfers = [t for t in transfers_all if PFX in json.dumps(t, ensure_ascii=False)]

guard = {
    "batch_id": "qa-resume-20261016-webgap",
    "authority": "http://192.168.100.209:18080 (repair)",
    "primary_8080_touched": False,
    "prefix": PFX,
    "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "counts_baseline": counts_baseline,
    "counts_after": counts_after,
    "deltas": {k: (counts_after.get(k, {}).get("count", 0) - counts_baseline[k]["count"]) for k in counts_baseline},
    "owned_leftovers": {
        "artifacts": owned_leftovers["artifacts"], "tasks": owned_leftovers["tasks"],
        "schedules": owned_leftovers["schedules"], "scripts": owned_leftovers["scripts"],
        "nodes": owned_leftovers["nodes"],
        "transfers": [{"id": t["id"], "status": t["status"]} for t in owned_transfers],
        "executions_retained": EXEC_IDS,
    },
    "owned_get404": results,
    "retained_transfer_count": len(owned_transfers),
    "retained_transfers": [{"id": t["id"], "status": t["status"], "source_path": t["source_path"],
                            "targets": [{"node_id": x["node_id"], "status": x["status"]} for x in t.get("targets") or []]}
                           for t in owned_transfers],
    "node_side": ledger["node_side"],
    "notes": [
        "本批新增 4 条 Transfer 无法删除（Hub 无 DELETE 端点），全部为终态 FAILED，作为历史保留并在此列出。",
        "本批产生 3 条 Execution（2 条为构造脚本时的失败尝试，1 条 SUCCESS）；DELETE /api/executions/{id} 返回 405，无法删除，作为历史保留。",
    ],
}

(EV / "fixture-ledger.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
(EV / "final-cleanup-guard.json").write_text(json.dumps(guard, ensure_ascii=False, indent=1) + "\n")
print(json.dumps({"deltas": guard["deltas"], "owned_get404": results,
                  "retained_transfer_count": guard["retained_transfer_count"],
                  "node_side": ledger["node_side"]}, ensure_ascii=False, indent=1))
print("owned leftovers non-transfer:", {k: len(v) if isinstance(v, list) else v for k, v in owned_leftovers.items()})
