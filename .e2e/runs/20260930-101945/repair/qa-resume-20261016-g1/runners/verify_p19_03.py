"""P19-03 审计校验（按审计行 id 去重，修正同名动作行的比对）。

六类动作逐类：动作前取审计行 id 集合 → 执行真实动作 → 动作后取集合 →
断言新增行中存在 (resource, action, resource_id) 匹配的行。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def ids(hub, admin):
    code, rows = C.audit_rows(hub, admin, limit=2000)
    return {r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}, code


def new_rows(before, after, resource, action, resource_id=None):
    out = []
    for rid, r in after.items():
        if rid in before:
            continue
        if r.get("resource") == resource and r.get("action") == action and (
                resource_id is None or r.get("resource_id") == resource_id):
            out.append({"id": rid, "username": r.get("username"), "detail": r.get("detail"),
                        "created_at": r.get("created_at"), "resource_id": r.get("resource_id")})
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    node = cr["node_id"]
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    tid = fx["fixtures"]["task"]["id"]
    aid = fx["fixtures"]["application"]["id"]
    ev = {"case_id": "P19-03", "at_utc": C.utc(), "authority": cr["base_url"],
          "method": "audit row-id diff per action class", "classes": {}}

    # 1) 执行
    b, _ = ids(hub, admin)
    r = hub.post(f"/api/tasks/{tid}/run", admin, body={"node_ids": {node: True}})
    exec_ids = [e.get("id") for e in (r["json"] or [])] if isinstance(r["json"], list) else []
    time.sleep(1.5)
    a, _ = ids(hub, admin)
    ev["classes"]["execute"] = {"http": r["status"], "execution_ids": exec_ids,
                                "new_rows": new_rows(b, a, "task", "execute", tid)}

    # 3) 部署
    b, _ = ids(hub, admin)
    r = hub.post(f"/api/applications/{aid}/deploy", admin, body={"node_ids": [node], "operation": "deploy"})
    time.sleep(1.5)
    a, _ = ids(hub, admin)
    ev["classes"]["deploy"] = {"http": r["status"], "execution_ids":
                               [e.get("id") for e in (r["json"] or [])] if isinstance(r["json"], list) else [],
                               "new_rows": new_rows(b, a, "application", "deploy", aid)}

    # 5) 节点状态（maintenance → online 各一条 update）
    b, _ = ids(hub, admin)
    s1 = hub.post(f"/api/nodes/{node}", admin, body={"status": "maintenance"})
    time.sleep(1.0)
    mid = hub.get(f"/api/nodes/{node}", admin)["json"]
    s2 = hub.post(f"/api/nodes/{node}", admin, body={"status": "online"})
    time.sleep(1.0)
    a, _ = ids(hub, admin)
    end = hub.get(f"/api/nodes/{node}", admin)["json"]
    ev["classes"]["node_status"] = {"maintenance_http": s1["status"], "resume_http": s2["status"],
                                    "status_mid": mid.get("status"), "status_end": end.get("status"),
                                    "sync_end": end.get("sync_status"),
                                    "new_rows": new_rows(b, a, "node", "update", node)}

    # 6) 用户角色 + 密码（两条 update）
    users = C.as_list(hub.get("/api/users", admin)["json"])
    probe = next(u for u in users if u["username"].startswith(f"qa-{nonce}-probe-u"))
    b, _ = ids(hub, admin)
    r1 = hub.put(f"/api/users/{probe['id']}", admin, body={"role": "viewer"})
    r2 = hub.put(f"/api/users/{probe['id']}", admin, body={"password": "P" * 18})
    lg = hub.login(probe["username"], "P" * 18)
    time.sleep(1.0)
    a, _ = ids(hub, admin)
    rows = new_rows(b, a, "user", "update", probe["id"])
    ev["classes"]["user_role_password"] = {"role_http": r1["status"], "password_http": r2["status"],
                                          "relogin_after_password_change": lg["status"],
                                          "new_rows": rows, "new_rows_count": len(rows)}

    # 2) 取消 + 4) 升级：复用首次运行的证据（此处复核其行仍存在）
    code, rows_all = C.audit_rows(hub, admin, limit=2000)
    prev = json.loads((C.EVID / "P19-03-audit-six-action-classes.json").read_text())
    cancel_id = prev["actions"]["2_cancel"]["execution_id"][0] if prev["actions"]["2_cancel"]["execution_id"] else None
    upgrade_node = node
    ev["classes"]["cancel_recheck"] = {
        "row_exists": any(r.get("resource") == "execution" and r.get("action") == "cancel"
                          and r.get("resource_id") == cancel_id for r in rows_all),
        "execution_id": cancel_id,
        "audit_total_rows": len(rows_all)}
    ev["classes"]["upgrade_recheck"] = {
        "row_exists": any(r.get("resource") == "node" and r.get("action") == "upgrade"
                          and r.get("resource_id") == upgrade_node for r in rows_all),
        "rows": [{"id": r.get("id"), "created_at": r.get("created_at"), "username": r.get("username")}
                 for r in rows_all if r.get("resource") == "node" and r.get("action") == "upgrade"
                 and r.get("resource_id") == upgrade_node][:3]}

    ev["verdict"] = {k: (len(v.get("new_rows", [])) > 0 if k != "user_role_password"
                         else v.get("new_rows_count", 0) >= 2)
                     for k, v in ev["classes"].items()}
    ev["verdict"]["cancel_row_exists"] = ev["classes"]["cancel_recheck"]["row_exists"]
    ev["verdict"]["upgrade_row_exists"] = ev["classes"]["upgrade_recheck"]["row_exists"]
    C.write_evidence("P19-03-audit-six-classes-verified.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
