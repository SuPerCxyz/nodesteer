"""整理隔离实例内的自有对象：恢复夹具任务定义、删除探针遗留的临时对象。

仅隔离实例；所有删除按显式 id/前缀进行，并在 evidence 中登记。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    node = cr["node_id"]
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    tid = fx["fixtures"]["task"]["id"]
    out = {"at_utc": C.utc(), "restore": {}, "cleanup": []}

    # 1) 恢复夹具任务定义（earlier admin PUT 覆盖了 name/command 且 enabled 丢失）
    r = hub.put(f"/api/tasks/{tid}", admin, body={
        "id": tid, "name": f"qa-{nonce}-task", "type": "command", "command": "echo g1-fixture-ok",
        "target": {"type": "node", "node_ids": [node]}, "parameters": [], "param_values": {},
        "timeout": 60, "retry": 0, "offline_policy": "allow_offline", "enabled": True})
    got = hub.get(f"/api/tasks/{tid}", admin)["json"]
    out["restore"]["task"] = {"http": r["status"], "name": got.get("name"), "enabled": got.get("enabled"),
                              "command": got.get("command")}

    # 2) 删除探针遗留对象
    for t in C.as_list(hub.get("/api/tasks", admin)["json"]):
        if t["id"] != tid and str(t.get("name", "")).startswith(f"qa-{nonce}-") and "-pos-" not in str(t.get("name")):
            d = hub.delete(f"/api/tasks/{t['id']}", admin)
            out["cleanup"].append({"kind": "task", "name": t["name"], "id": t["id"], "http": d["status"]})
    for g in C.as_list(hub.get("/api/groups", admin)["json"]):
        if str(g.get("name", "")).startswith("qa-rbac-g"):
            d = hub.delete(f"/api/groups/{g['id']}", admin)
            out["cleanup"].append({"kind": "group", "name": g["name"], "id": g["id"], "http": d["status"]})
    for s in C.as_list(hub.get("/api/scripts", admin)["json"]):
        if str(s.get("name", "")) == "qa-rbac-x":
            d = hub.delete(f"/api/scripts/{s['id']}", admin)
            out["cleanup"].append({"kind": "script", "name": s["name"], "id": s["id"], "http": d["status"]})
    for a in C.as_list(hub.get("/api/applications", admin)["json"]):
        if str(a.get("name", "")) == "qa-rbac-app":
            d = hub.delete(f"/api/applications/{a['id']}", admin)
            out["cleanup"].append({"kind": "application", "name": a["name"], "id": a["id"], "http": d["status"]})
    for u in C.as_list(hub.get("/api/users", admin)["json"]):
        if str(u.get("username", "")) == "qa-rbac-u":
            out["cleanup"].append({"kind": "user", "username": u["username"], "note": "no delete API; kept"})

    # 3) 记账当前对象总数
    out["counts_after"] = {k: v["count"] for k, v in C.counts(hub, admin).items()}
    C.write_evidence("fixture-cleanup-and-restore.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
