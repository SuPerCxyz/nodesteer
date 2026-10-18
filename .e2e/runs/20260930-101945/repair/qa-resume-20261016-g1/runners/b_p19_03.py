"""B 批：P19-03（执行、取消、部署、升级、节点状态、用户角色/密码修改 六类动作的真实审计）。

方法：每类动作前后各取一次 /api/audit 快照，按 (resource, resource_id, action) 精确比对新增行；
并做 1 次 UI 审计页搜索核对（取消类）。全部动作在隔离实例 + owned child 上。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402


def snap(hub, admin):
    code, rows = C.audit_rows(hub, admin, limit=1000)
    return {(r.get("resource"), r.get("resource_id"), r.get("action")): r for r in rows if isinstance(r, dict)}


def diff(before, after):
    return [{"resource": k[0], "resource_id": k[1], "action": k[2],
             "username": after[k].get("username"), "detail": after[k].get("detail"),
             "created_at": after[k].get("created_at")}
            for k in after if k not in before]


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    node = cr["node_id"]
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    tid = fx["fixtures"]["task"]["id"]
    aid = fx["fixtures"]["application"]["id"]
    ev = {"case_id": "P19-03", "at_utc": C.utc(), "authority": cr["base_url"], "actions": {}}

    # ---------- 1. 执行 ----------
    b = snap(hub, admin)
    run = hub.post(f"/api/tasks/{tid}/run", admin, body={"node_ids": {node: True}})
    run_ids = [e.get("id") for e in (run["json"] or [])] if isinstance(run["json"], list) else []
    time.sleep(1.2)
    a = snap(hub, admin)
    ev["actions"]["1_execute"] = {"http": run["status"], "execution_ids": run_ids,
                                  "new_audit_rows": diff(b, a)}

    # ---------- 2. 取消（需要 RUNNING 样本：新建 slow 任务） ----------
    slow = hub.post("/api/tasks", admin, body={
        "name": f"qa-{nonce}-task-slow", "type": "command", "command": "sleep 60",
        "target": {"type": "node", "node_ids": [node]}, "timeout": 120, "retry": 0,
        "offline_policy": "allow_offline", "enabled": True})
    slow_id = (slow["json"] or {}).get("id")
    b = snap(hub, admin)
    run2 = hub.post(f"/api/tasks/{slow_id}/run", admin, body={"node_ids": {node: True}})
    slow_exec = [e.get("id") for e in (run2["json"] or [])] if isinstance(run2["json"], list) else []
    status = None
    for _ in range(30):
        if slow_exec:
            st = hub.get(f"/api/executions/{slow_exec[0]}", admin)
            status = (st["json"] or {}).get("status") if isinstance(st["json"], dict) else None
            if status == "RUNNING":
                break
        time.sleep(1)
    cancel = hub.post(f"/api/executions/{slow_exec[0]}/cancel", admin, body={}) if slow_exec else {"status": None, "json": None}
    time.sleep(1.0)
    st_after = hub.get(f"/api/executions/{slow_exec[0]}", admin) if slow_exec else {"json": None}
    a = snap(hub, admin)
    ev["actions"]["2_cancel"] = {"http": cancel["status"], "execution_id": slow_exec,
                                 "status_before_cancel": status,
                                 "status_after_cancel": (st_after["json"] or {}).get("status") if isinstance(st_after["json"], dict) else None,
                                 "new_audit_rows": diff(b, a)}

    # ---------- 3. 部署 ----------
    b = snap(hub, admin)
    dep = hub.post(f"/api/applications/{aid}/deploy", admin, body={"node_ids": [node], "operation": "deploy"})
    dep_ids = [e.get("id") for e in (dep["json"] or [])] if isinstance(dep["json"], list) else []
    time.sleep(1.2)
    a = snap(hub, admin)
    ev["actions"]["3_deploy"] = {"http": dep["status"], "execution_ids": dep_ids,
                                 "new_audit_rows": diff(b, a)}

    # ---------- 4. 升级 ----------
    b = snap(hub, admin)
    up = hub.post("/api/nodes/upgrade", admin, body={"node_ids": [node]})
    time.sleep(1.2)
    a = snap(hub, admin)
    ev["actions"]["4_upgrade"] = {"http": up["status"],
                                 "results": up["json"] if not isinstance(up["json"], dict) or "error" in up["json"] else up["json"],
                                 "new_audit_rows": diff(b, a)}
    time.sleep(5)

    # ---------- 5. 节点状态 ----------
    b = snap(hub, admin)
    st1 = hub.post(f"/api/nodes/{node}", admin, body={"status": "maintenance"})
    time.sleep(0.8)
    mid = hub.get(f"/api/nodes/{node}", admin)["json"]
    st2 = hub.post(f"/api/nodes/{node}", admin, body={"status": "online"})
    time.sleep(0.8)
    end = hub.get(f"/api/nodes/{node}", admin)["json"]
    a = snap(hub, admin)
    ev["actions"]["5_node_status"] = {"maintenance_http": st1["status"], "resume_http": st2["status"],
                                      "status_mid": mid.get("status"), "status_end": end.get("status"),
                                      "sync_end": end.get("sync_status"),
                                      "new_audit_rows": diff(b, a)}

    # ---------- 6. 用户角色 / 密码修改 ----------
    users = C.as_list(hub.get("/api/users", admin)["json"])
    probe = next(u for u in users if u["username"].startswith(f"qa-{nonce}-probe-u"))
    b = snap(hub, admin)
    r1 = hub.put(f"/api/users/{probe['id']}", admin, body={"role": "operator"})
    r2 = hub.put(f"/api/users/{probe['id']}", admin, body={"password": "N" * 18})
    r3 = hub.login(probe["username"], "N" * 18)
    a = snap(hub, admin)
    ev["actions"]["6_user_role_password"] = {"role_http": r1["status"], "password_http": r2["status"],
                                            "relogin_after_password_change": r3["status"],
                                            "new_audit_rows": diff(b, a)}

    # ---------- UI：审计页搜索（取消类） ----------
    U.init(nonce)
    lg = U.login("audit", cr["admin_username"], cr["admin_password"])
    U.open_("audit", "/audit")
    exec_id = slow_exec[0] if slow_exec else ""
    search = U.js("audit", r"""
(() => {
  const input = document.querySelector('input[placeholder]');
  if (!input) return {no_input: true, placeholders: [...document.querySelectorAll('input')].map(i=>i.placeholder)};
  return {placeholder: input.placeholder};
})()""")
    U.js("audit", r"""
(() => {
  const input = document.querySelector('input[placeholder]');
  if (!input) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, %s);
  input.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
})()""" % json.dumps(exec_id))
    time.sleep(1.6)
    ui_audit = U.js("audit", r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')].map(r => r.innerText.replace(/\\n/g,' | ').slice(0,180));
  return {rows, count: rows.length, body_has_exec: (document.body.innerText||'').includes(%s)};
})()""" % json.dumps(exec_id))
    ev["ui_audit_search"] = {"search_input": search, "result": ui_audit,
                            "shot": U.shot("audit", "P1903-audit-search-cancel.png"),
                            "login_token": lg["token_in_localStorage"]}

    ev["verdict"] = {
        "execute_audited": any(r["action"] == "execute" for r in ev["actions"]["1_execute"]["new_audit_rows"]),
        "cancel_audited": any(r["action"] == "cancel" and r["resource"] == "execution"
                              for r in ev["actions"]["2_cancel"]["new_audit_rows"]),
        "deploy_audited": any(r["action"] == "deploy" for r in ev["actions"]["3_deploy"]["new_audit_rows"]),
        "upgrade_audited": any(r["action"] == "upgrade" for r in ev["actions"]["4_upgrade"]["new_audit_rows"]),
        "node_status_audited": sum(1 for r in ev["actions"]["5_node_status"]["new_audit_rows"]
                                   if r["action"] == "update" and r["resource"] == "node"),
        "user_role_password_audited": sum(1 for r in ev["actions"]["6_user_role_password"]["new_audit_rows"]
                                         if r["action"] == "update" and r["resource"] == "user"),
        "ui_search_finds_cancel": bool(ui_audit.get("body_has_exec")) and ui_audit.get("count", 0) >= 1,
    }
    C.write_evidence("P19-03-audit-six-action-classes.json", ev)
    print(json.dumps({"actions": {k: {"http": v.get("http"), "rows": v["new_audit_rows"]}
                                 for k, v in ev["actions"].items()},
                      "ui": ev["ui_audit_search"]["result"], "verdict": ev["verdict"]},
                     ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
