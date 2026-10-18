"""A 批：P04-06（operator/viewer 受限操作 UI 可见性 + API 403 + 状态不变）
     + P21-08（Operator/Viewer 读取、保存按钮策略 + API 403）。

设计要点：
- 受限角色尝试（operator/viewer）单独包在 state fingerprint 窗口内 → 证明 0 副作用；
- admin 正向对照使用**当次创建的专用一次性对象**（并在结束前删除），不污染已登记夹具；
- 全部动作在隔离实例内。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402

PAGES = {
    "/nodes": ["添加节点"],
    "/applications": ["新建托管应用"],
    "/tasks": ["新建任务"],
    "/schedules": ["新建调度"],
    "/scripts": ["新建脚本"],
    "/users": ["新建用户"],
}


def tokens(hub, cr):
    return {"admin": hub.token(cr["admin_username"], cr["admin_password"]),
            "operator": hub.token(cr["operator_username"], cr["operator_password"]),
            "viewer": hub.token(cr["viewer_username"], cr["viewer_password"])}


def restricted_attempts(hub, tok, cr, fx) -> dict:
    """operator/viewer 对 admin-only 写操作的尝试（期望全部 403）。"""
    tid = fx["fixtures"]["task"]["id"]
    sid = fx["fixtures"]["script"]["id"]
    aid = fx["fixtures"]["application"]["id"]
    scid = fx["fixtures"]["schedule"]["id"]
    node = cr["node_id"]
    pending = next(n["id"] for n in C.as_list(hub.get("/api/nodes", tok["admin"])["json"])
                   if n.get("status") == "pending")
    ghost = "00000000-0000-0000-0000-000000000000"
    users = C.as_list(hub.get("/api/users", tok["admin"])["json"])
    probe_uid = next((u["id"] for u in users if u.get("username") == fx.get("probe_user")), ghost)
    spec = [
        ("script_create", "POST", "/api/scripts", {"name": "qa-rbac-x", "interpreter": "bash", "content": "echo x"}),
        ("script_update", "PUT", f"/api/scripts/{sid}", {"id": sid, "name": "qa-rbac-x", "interpreter": "bash", "content": "echo y"}),
        ("script_delete", "DELETE", f"/api/scripts/{sid}", None),
        ("task_create", "POST", "/api/tasks", {"name": "qa-rbac-t", "type": "command", "command": "echo x", "target": {"type": "node", "node_ids": [node]}}),
        ("task_update", "PUT", f"/api/tasks/{tid}", {"id": tid, "name": "qa-rbac-t-mod", "type": "command", "command": "echo y", "target": {"type": "node", "node_ids": [node]}}),
        ("task_delete", "DELETE", f"/api/tasks/{tid}", None),
        ("schedule_create", "POST", "/api/schedules", {"task_id": tid, "type": "interval", "interval_sec": 3600, "execution_owner": "hub", "timezone": "UTC", "enabled": True}),
        ("schedule_update", "PUT", f"/api/schedules/{scid}", {"id": scid, "task_id": tid, "type": "interval", "interval_sec": 7200, "execution_owner": "hub", "timezone": "UTC", "enabled": True}),
        ("schedule_delete", "DELETE", f"/api/schedules/{scid}", None),
        ("application_create", "POST", "/api/applications", {"name": "qa-rbac-app", "version": "1.0.0"}),
        ("application_update", "PUT", f"/api/applications/{aid}", {"id": aid, "name": "qa-rbac-app-mod", "version": "1.0.0"}),
        ("application_delete", "DELETE", f"/api/applications/{aid}", None),
        ("application_assign", "POST", f"/api/applications/{aid}/assign", {"node_ids": [node]}),
        ("group_create", "POST", "/api/groups", {"name": "qa-rbac-g", "type": "static", "members": []}),
        ("transfer_create", "POST", "/api/transfers", {"source_node_id": pending, "source_path": "/tmp/x", "targets": [{"node_id": node, "destination_path": "/tmp/y"}]}),
        ("node_status_update", "POST", f"/api/nodes/{node}", {"status": "maintenance"}),
        ("node_delete", "DELETE", f"/api/nodes/{pending}", None),
        ("node_revoke_credential", "POST", f"/api/nodes/{node}/revoke-credential", {}),
        ("settings_put", "PUT", "/api/settings", {"heartbeat_interval_sec": "60", "changelog_window": "999999"}),
        ("users_get", "GET", "/api/users", None),
        ("users_create", "POST", "/api/users", {"username": "qa-rbac-u", "password": "x" * 16, "role": "viewer"}),
        ("users_update_role", "PUT", f"/api/users/{probe_uid}", {"role": "administrator"}),
        ("users_update_password", "PUT", f"/api/users/{probe_uid}", {"password": "y" * 16}),
    ]
    out = {}
    for role in ("operator", "viewer"):
        t = tok[role]
        rows = {}
        for name, method, path, body in spec:
            if body is None and method in ("PUT", "POST", "DELETE"):
                body = {}
            r = hub.req(method, path, t, body=body)
            rows[name] = {"method": method, "path": path, "http": r["status"],
                          "error": (r["json"] or {}).get("error") if isinstance(r["json"], dict) else None}
        out[role] = rows
    return out


def boundary_probe(hub, tok, cr, fx) -> dict:
    """角色边界（非 403）：operator 允许 run/deploy，viewer 403。真实执行在 owned child 上。"""
    tid = fx["fixtures"]["task"]["id"]
    aid = fx["fixtures"]["application"]["id"]
    node = cr["node_id"]
    out = {}
    for role in ("operator", "viewer"):
        t = tok[role]
        run = hub.post(f"/api/tasks/{tid}/run", t, body={"node_ids": {node: True}})
        dep = hub.post(f"/api/applications/{aid}/deploy", t, body={"node_ids": [node], "operation": "deploy"})
        out[role] = {
            "task_run": {"http": run["status"],
                         "execution_ids": [e.get("id") for e in (run["json"] or [])]
                         if isinstance(run["json"], list) else None,
                         "error": (run["json"] or {}).get("error") if isinstance(run["json"], dict) else None},
            "application_deploy": {"http": dep["status"],
                                   "execution_ids": [e.get("id") for e in (dep["json"] or [])]
                                   if isinstance(dep["json"], list) else None,
                                   "error": (dep["json"] or {}).get("error") if isinstance(dep["json"], dict) else None},
        }
    return out


def admin_positive(hub, tok, cr, fx) -> dict:
    """admin 正向对照：同一批操作中 admin 必须被允许（使用一次性专用对象）。"""
    admin = tok["admin"]
    node = cr["node_id"]
    import secrets as _s
    run = _s.token_hex(2)
    pending = next(n["id"] for n in C.as_list(hub.get("/api/nodes", admin)["json"])
                   if n.get("status") == "pending")
    out = {}
    # 专用脚本 / 任务 / 应用 / 分组 / 用户
    sc = hub.post("/api/scripts", admin, body={"name": f"qa-{cr['nonce']}-pos-{run}-script", "interpreter": "bash",
                                              "content": "echo pos", "timeout": 30, "enabled": True})
    scid = (sc["json"] or {}).get("id")
    tk = hub.post("/api/tasks", admin, body={"name": f"qa-{cr['nonce']}-pos-{run}-task", "type": "command",
                                            "command": "echo pos", "target": {"type": "node", "node_ids": [node]},
                                            "timeout": 60, "offline_policy": "allow_offline", "enabled": True})
    tid = (tk["json"] or {}).get("id")
    ap = hub.post("/api/applications", admin, body={"name": f"qa-{cr['nonce']}-pos-{run}-app", "version": "1.0.0",
                                                   "binary_path": f"/usr/local/bin/qa-{cr['nonce']}-pos",
                                                   "unit_name": f"nodesteer-qa-{cr['nonce']}-pos.service"})
    aid = (ap["json"] or {}).get("id")
    gp = hub.post("/api/groups", admin, body={"name": f"qa-{cr['nonce']}-pos-{run}-g", "type": "static", "members": []})
    gid = (gp["json"] or {}).get("id")
    up = hub.post("/api/users", admin, body={"username": f"qa-{cr['nonce']}-pos-{run}-u", "password": "z" * 16, "role": "viewer"})
    uid = (up["json"] or {}).get("id")
    tr = hub.post("/api/transfers", admin, body={"source_node_id": pending, "source_path": f"/tmp/qa-{cr['nonce']}-pos-{run}",
                                                "targets": [{"node_id": node, "destination_path": f"/tmp/qa-{cr['nonce']}-pos-dst"}]})
    trid = (tr["json"] or {}).get("id")
    sch = hub.post("/api/schedules", admin, body={"task_id": tid, "type": "interval", "interval_sec": 7200,
                                                 "execution_owner": "hub", "timezone": "UTC", "enabled": True})
    schid = (sch["json"] or {}).get("id")
    out.update({
        "script_create": sc["status"], "task_create": tk["status"], "application_create": ap["status"],
        "group_create": gp["status"], "users_create": up["status"], "transfer_create": tr["status"],
        "schedule_create": sch["status"],
        "application_update": hub.put(f"/api/applications/{aid}", admin, body={"id": aid, "name": f"qa-{cr['nonce']}-pos-app2", "version": "1.1.0"})["status"],
        "application_assign": hub.post(f"/api/applications/{aid}/assign", admin, body={"node_ids": [node]})["status"],
        "application_assign_unknown_app": hub.post("/api/applications/00000000-0000-0000-0000-000000000000/assign",
                                                  admin, body={"node_ids": [node]})["status"],
        "application_assign_repeat": hub.post(f"/api/applications/{aid}/assign", admin, body={"node_ids": [node]})["status"],
        "users_update_role": hub.put(f"/api/users/{uid}", admin, body={"role": "operator"})["status"],
        "users_update_invalid_role": hub.put(f"/api/users/{uid}", admin, body={"role": "root"})["status"],
        "settings_put": hub.put("/api/settings", admin, body={"changelog_window": "1000000"})["status"],
        "node_status_update": hub.post(f"/api/nodes/{pending}", admin, body={"status": "maintenance"})["status"],
        "node_status_restore": hub.post(f"/api/nodes/{pending}", admin, body={"status": "pending"})["status"],
        "schedule_delete": hub.delete(f"/api/schedules/{schid}", admin)["status"],
        "task_delete": hub.delete(f"/api/tasks/{tid}", admin)["status"],
        "script_delete": hub.delete(f"/api/scripts/{scid}", admin)["status"],
        "application_delete": hub.delete(f"/api/applications/{aid}", admin)["status"],
        "group_delete": hub.delete(f"/api/groups/{gid}", admin)["status"],
        "transfer_delete": hub.delete(f"/api/transfers/{trid}", admin)["status"],
    })
    return out


def allowed_probe(hub, tok) -> dict:
    ghost = "00000000-0000-0000-0000-000000000000"
    out = {}
    for role in ("operator", "viewer"):
        t = tok[role]
        out[role] = {
            "me_role": (hub.get("/api/me", t)["json"] or {}).get("Role"),
            "tasks_get": hub.get("/api/tasks", t)["status"],
            "scripts_get": hub.get("/api/scripts", t)["status"],
            "applications_get": hub.get("/api/applications", t)["status"],
            "schedules_get": hub.get("/api/schedules", t)["status"],
            "nodes_get": hub.get("/api/nodes", t)["status"],
            "nodes_enrollment_get": hub.get("/api/nodes/enrollment?node_name=x&node_ip=1.2.3.4", t)["status"],
            "executions_get": hub.get("/api/executions", t)["status"],
            "audit_get": hub.get("/api/audit?limit=5", t)["status"],
            "settings_get": hub.get("/api/settings", t)["status"],
            "transfers_get": hub.get("/api/transfers", t)["status"],
            "metrics_get": hub.get("/api/metrics", t)["status"],
            "cancel_missing_execution": hub.post(f"/api/executions/{ghost}/cancel", t, body={})["status"],
            "upgrade_missing_node": hub.post("/api/nodes/upgrade", t, body={"node_ids": [ghost]})["status"],
        }
    return out


def settings_policy(hub, tok) -> dict:
    out = {}
    for role in ("operator", "viewer"):
        t = tok[role]
        before = hub.get("/api/settings", tok["admin"])["json"]
        put = hub.put("/api/settings", t, body={"heartbeat_interval_sec": "77", "changelog_window": "12345"})
        after = hub.get("/api/settings", tok["admin"])["json"]
        out[role] = {
            "get_http": hub.get("/api/settings", t)["status"],
            "get_body_keys": sorted((hub.get("/api/settings", t)["json"] or {}).keys()),
            "put_http": put["status"],
            "put_error": (put["json"] or {}).get("error") if isinstance(put["json"], dict) else None,
            "settings_unchanged": before == after,
            "before": before, "after": after,
        }
    return out


def ui_matrix(cr) -> dict:
    out = {}
    U.init(cr["nonce"])
    for role, who, un, pw in (("operator", "op", cr["operator_username"], cr["operator_password"]),
                              ("viewer", "vw", cr["viewer_username"], cr["viewer_password"]),
                              ("admin", "adm", cr["admin_username"], cr["admin_password"])):
        lg = U.login(who, un, pw)
        rec = {"login": {"token": lg["token_in_localStorage"], "url": lg["url"]}, "pages": {}}
        for path, forbidden in PAGES.items():
            U.open_(who, path)
            state = U.js(who, r"""
(() => {
  const vis = [...document.querySelectorAll('button,a')].filter(b => b.offsetParent !== null);
  const texts = vis.map(b => (b.innerText||'').trim());
  return {buttons: texts, rows: [...document.querySelectorAll('table tbody tr')].length,
          body_tail: (document.body.innerText||'').slice(-160)};
})()""")
            rec["pages"][path] = {"forbidden_controls_found": {f: state["buttons"].count(f) for f in forbidden},
                                  "rows": state["rows"], "url": U.url(who), "body_tail": state["body_tail"]}
            if path == "/users":
                rec["pages"][path]["admin_only_notice"] = "只有管理员可以管理用户。" in U.body_text(who)
            rec["pages"][path]["shot"] = U.shot(who, f"P0406-{role}-{path.strip('/')}.png")
        U.open_(who, "/settings")
        st = U.js(who, r"""
(() => {
  const inputs = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null);
  const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
      .map(b => (b.innerText||'').trim());
  return {input_count: inputs.length, inputs_disabled: inputs.map(i => !!i.disabled),
          save_button_count: btns.filter(t => /保存|Save/.test(t)).length,
          aria_labels: inputs.map(i => i.getAttribute('aria-label')), values: inputs.map(i => i.value)};
})()""")
        rec["settings"] = st
        rec["settings_shot"] = U.shot(who, f"P2108-settings-{role}.png")
        out[role] = rec
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    tok = tokens(hub, cr)

    # 受限角色尝试：状态不变窗口
    fp_before = C.state_fingerprint(hub, tok["admin"])
    api = restricted_attempts(hub, tok, cr, fx)
    fp_after = C.state_fingerprint(hub, tok["admin"])
    state_unchanged = fp_before == fp_after

    boundary = boundary_probe(hub, tok, cr, fx)
    allowed = allowed_probe(hub, tok)
    settings = settings_policy(hub, tok)
    positive = admin_positive(hub, tok, cr, fx)
    # 把正向对照创建的专用用户登记（便于 X13/审计引用）
    fx2 = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    fx2["probe_positive"] = {"note": "admin 正向对照专用一次性对象（已删除，用户无删除 API 除外）",
                             "positive_results": positive}
    (C.ROOT / "fixture-ledger.json").write_text(json.dumps(fx2, ensure_ascii=False, indent=1) + "\n")
    ui = ui_matrix(cr)

    ev = {
        "case_ids": ["P04-06", "P21-08"], "at_utc": C.utc(), "authority": cr["base_url"],
        "restricted_attempts": api,
        "role_boundary_real_execution": boundary,
        "api_allowed_reads": allowed,
        "settings_policy": settings,
        "admin_positive_control": positive,
        "state_fingerprint_before": fp_before,
        "state_fingerprint_after": fp_after,
        "state_unchanged_after_restricted_attempts": state_unchanged,
        "ui": ui,
    }
    ev["verdict"] = {
        "operator_viewer_all_403": all(v["http"] == 403 for role in ("operator", "viewer")
                                       for v in api[role].values()),
        "restricted_ui_controls_absent": all(
            all(c == 0 for c in ui[role]["pages"][p]["forbidden_controls_found"].values())
            for role in ("operator", "viewer") for p in PAGES),
        "admin_positive_controls_ok": {k: v for k, v in positive.items()},
        "settings_policy_ok": {role: {"get": settings[role]["get_http"], "put": settings[role]["put_http"],
                                      "save_button": ui[role]["settings"]["save_button_count"],
                                      "inputs_disabled": all(ui[role]["settings"]["inputs_disabled"])}
                               for role in ("operator", "viewer")},
        "state_unchanged": state_unchanged,
        "boundary": {r: {k: v["http"] for k, v in boundary[r].items()} for r in boundary},
    }
    C.write_evidence("P04-06-P21-08-rbac-matrix.json", ev)
    print(json.dumps({
        "operator": {k: v["http"] for k, v in api["operator"].items()},
        "viewer_non403": {k: v["http"] for k, v in api["viewer"].items() if v["http"] != 403},
        "admin_positive": positive,
        "allowed": allowed,
        "settings": {r: {"get": settings[r]["get_http"], "put": settings[r]["put_http"],
                         "unchanged": settings[r]["settings_unchanged"]} for r in settings},
        "state_unchanged": state_unchanged,
        "boundary": {r: {k: v["http"] for k, v in boundary[r].items()} for r in boundary},
        "ui_save_buttons": {r: ui[r]["settings"]["save_button_count"] for r in ui},
        "ui_inputs_disabled": {r: all(ui[r]["settings"]["inputs_disabled"]) for r in ui},
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
