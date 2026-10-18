"""p-api：AUTH-MANAGEMENT / AUTH-USERS 真实认证与越权矩阵（隔离实例）。

产出 evidence：
- AUTH-MANAGEMENT-anon-invalid-revoked-matrix.json
- AUTH-MANAGEMENT-expired-ttl-matrix.json
- AUTH-MANAGEMENT-role-write-matrix.json
- AUTH-USERS-admin-write.json
- AUTH-USERS-role-matrix.json
- AUTH-MANAGEMENT-state-invariance.json
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papi_common as C  # noqa: E402

MAIN_NONCE_TAG = "papim"
TTL_NONCE_TAG = "papitt"

# 受保护端点（read probe 用 GET；{id} 用随机不存在 UUID，中间件在 handler 前拦截）
READ_ENDPOINTS = [
    "/api/logout", "/api/me", "/api/nodes/enrollment", "/api/nodes", "/api/nodes/upgrade",
    "/api/nodes/{id}", "/api/transfers", "/api/transfers/{id}", "/api/groups",
    "/api/groups/{id}", "/api/scripts", "/api/scripts/{id}", "/api/tasks",
    "/api/tasks/{id}", "/api/schedules", "/api/schedules/{id}", "/api/artifacts",
    "/api/artifacts/{id}", "/api/artifacts/{id}/download", "/api/applications",
    "/api/applications/{id}", "/api/executions", "/api/executions/{id}", "/api/audit",
    "/api/settings", "/api/users", "/api/users/{id}", "/metrics",
]

# 写/管理端点：admin-only → operator/viewer 403；canRun → operator 200 路径（viewer 403）
WRITE_ENDPOINTS = [
    ("POST", "/api/users", {"username": "x", "password": "y", "role": "viewer"}, "admin"),
    ("POST", "/api/nodes", {"id": "x", "status": "active", "labels": {}}, "admin"),
    ("DELETE", "/api/nodes/{node_id}", None, "admin"),
    ("POST", "/api/nodes/{node_id}/revoke-credential", None, "admin"),
    ("DELETE", "/api/nodes/{id}", None, "admin-nonexistent-node"),
    ("POST", "/api/transfers", {"source_node_id": "x", "source_path": "/tmp/x", "targets": []}, "admin"),
    ("POST", "/api/transfers/{id}/cancel", None, "admin"),
    ("POST", "/api/groups", {"name": "x", "type": "static"}, "admin"),
    ("PUT", "/api/groups/{id}", {"name": "x", "type": "static"}, "admin"),
    ("DELETE", "/api/groups/{id}", None, "admin"),
    ("POST", "/api/scripts", {"name": "x", "content": "echo"}, "admin"),
    ("PUT", "/api/scripts/{id}", {"name": "x", "content": "echo"}, "admin"),
    ("DELETE", "/api/scripts/{id}", None, "admin"),
    ("POST", "/api/tasks", {"name": "x"}, "admin"),
    ("PUT", "/api/tasks/{id}", {"name": "x"}, "admin"),
    ("DELETE", "/api/tasks/{id}", None, "admin"),
    ("POST", "/api/tasks/{id}/run", None, "run"),
    ("POST", "/api/schedules", {"task_id": "x", "cron": "* * * * *"}, "admin"),
    ("PUT", "/api/schedules/{id}", {"task_id": "x", "cron": "* * * * *"}, "admin"),
    ("DELETE", "/api/schedules/{id}", None, "admin"),
    ("POST", "/api/applications", {"name": "x"}, "admin"),
    ("PUT", "/api/applications/{id}", {"name": "x"}, "admin"),
    ("DELETE", "/api/applications/{id}", None, "admin"),
    ("PUT", "/api/settings", {"key": "value"}, "admin"),
    ("POST", "/api/nodes/upgrade", {"node_ids": []}, "run"),
    ("POST", "/api/audit", None, "read-only-handler"),
]


def inst_creds(tag: str) -> dict:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    for i in led["instances"]:
        if i["nonce"].startswith(tag):
            return C.load_creds(i["nonce"])
    raise RuntimeError(f"instance {tag} not found")


def base_url(creds: dict) -> str:
    return f"http://127.0.0.1:{creds['tunnel_port']}"


def sess_role(obj) -> str | None:
    """/api/me、/api/login.user 返回 auth.Session（无 json tag，键首字母大写）。"""
    if not isinstance(obj, dict):
        return None
    return obj.get("role") or obj.get("Role")


def user_digest(hub: C.Hub, token: str) -> dict:
    r = hub.req("GET", "/api/users", token=token)
    users = r["json"] if isinstance(r["json"], list) else []
    digest = C.sha256_bytes(
        json.dumps(sorted((u.get("id"), u.get("username"), u.get("role")) for u in users)).encode())
    return {"status": r["status"], "count": len(users), "identity_role_sha256": digest}


def main() -> None:
    out = {}
    mc = inst_creds(MAIN_NONCE_TAG)
    tc = inst_creds(TTL_NONCE_TAG)
    hub = C.Hub(base_url(mc))
    ttlhub = C.Hub(base_url(tc))
    out["main_instance"] = {"nonce": mc["nonce"], "port": mc["host_port"],
                            "tunnel": mc["tunnel_port"], "session_ttl": mc["session_ttl"]}
    out["ttl_instance"] = {"nonce": tc["nonce"], "port": tc["host_port"],
                           "tunnel": tc["tunnel_port"], "session_ttl": tc["session_ttl"]}

    admin_user, admin_pw = mc["admin_username"], mc["admin_password"]
    admin_token = hub.token(admin_user, admin_pw)
    me = hub.req("GET", "/api/me", token=admin_token)
    me_json = me["json"] or {}
    out["admin_login"] = {"login_http": 200, "me_http": me["status"],
                          "me_role": me_json.get("role") or me_json.get("Role"),
                          "me_body_keys": sorted(me_json.keys())}

    # ---- 创建 operator / viewer（隔离实例，随实例销毁；产品无删除用户 API）----
    suffix = mc["nonce"][-8:]
    inst_suffix = suffix
    create_records = []
    creds_by_role = {}
    existing = hub.req("GET", "/api/users", token=admin_token)
    by_name = {u.get("username"): u for u in (existing["json"] or [])}
    out["auth_users_count_before_create"] = len(by_name)
    admin_id = me_json.get("UserID") or me_json.get("id")
    for role in ("operator", "viewer"):
        username = f"qa-papi-{suffix}-{role}"
        password = C.gen_password()
        r = hub.req("POST", "/api/users", token=admin_token,
                    body={"username": username, "password": password, "role": role})
        uid = (r["json"] or {}).get("id")
        recovery = None
        if r["status"] != 200 and username in by_name:
            # 同名已存在（重跑或早期部分运行）：改用 admin PUT 重置密码并复用，不新建重复账号
            uid = by_name[username]["id"]
            rr = hub.req("PUT", f"/api/users/{uid}", token=admin_token,
                         body={"password": password, "role": role})
            recovery = {"put_http": rr["status"]}
        create_records.append({"role": role, "http": r["status"],
                               "returned_role": (r["json"] or {}).get("role"),
                               "id_present": bool(uid), "reused_existing": recovery})
        if uid:
            creds_by_role[role] = {"user_id": uid, "username": username, "password": password,
                                   "token": hub.token(username, password)}
    out["users_created"] = create_records

    # ---- AUTH-USERS: role matrix ----
    if "operator" in creds_by_role and "viewer" in creds_by_role:
        op = creds_by_role["operator"]
        vw = creds_by_role["viewer"]
        matrix = []
        probes = [
            ("GET", "/api/users", None),
            ("POST", "/api/users", {"username": "z", "password": "y", "role": "viewer"}),
            ("PUT", f"/api/users/{op['user_id']}", {"role": "viewer"}),
            ("PUT", f"/api/users/{vw['user_id']}", {"password": "zz"}),
            ("DELETE", f"/api/users/{op['user_id']}", None),
        ]
        for role, tok in (("operator", op["token"]), ("viewer", vw["token"])):
            for method, path, body in probes:
                r = hub.req(method, path, token=tok, body=body)
                matrix.append({"role": role, "method": method, "path": path,
                               **C.body_summary(r)})
        C.write_evidence("AUTH-USERS-role-matrix.json",
                         C.stub({"hub": base_url(mc), "session_ttl": mc["session_ttl"],
                                 "note": "operator/viewer 对 /api/users 的 GET/写/DELETE 实际状态",
                                 "matrix": matrix}))
        out["auth_users_role_matrix"] = {"records": len(matrix),
                                         "statuses": sorted({(m["role"], m["method"], m["status"])
                                                             for m in matrix})}

    # ---- AUTH-USERS: admin 成功写入 + 可回收（随实例销毁）----
    if "viewer" in creds_by_role:
        vw = creds_by_role["viewer"]
        listing = hub.req("GET", "/api/users", token=admin_token)
        users_after_create = len(listing["json"] or []) if isinstance(listing["json"], list) else None
        before_put = hub.req("PUT", f"/api/users/{vw['user_id']}", token=admin_token,
                             body={"role": "operator"})
        vw_me = hub.req("GET", "/api/me", token=vw["token"])
        pw_new = C.gen_password()
        before_pw = hub.req("PUT", f"/api/users/{vw['user_id']}", token=admin_token,
                            body={"password": pw_new})
        relogin = hub.login(vw["username"], pw_new)
        relogin_user = (relogin["json"] or {}).get("user") if isinstance(relogin["json"], dict) else None
        role_upgrade = {
            "users_count_before_create": out["auth_users_count_before_create"],
            "users_count_after_create": users_after_create,
            "put_role_http": before_put["status"], "put_role_body": before_put["json"],
            "me_after_role_change_http": vw_me["status"],
            "me_after_role_change_role": sess_role(vw_me["json"]),
            "put_password_http": before_pw["status"],
            "relogin_with_new_password_http": relogin["status"],
            "relogin_user_role": sess_role(relogin_user),
        }
        # 复原为 viewer 便于后续矩阵复核（仍在隔实例内）
        hub.req("PUT", f"/api/users/{vw['user_id']}", token=admin_token, body={"role": "viewer"})
        C.write_evidence("AUTH-USERS-admin-write.json",
                         C.stub({"hub": base_url(mc),
                                 "created_users": create_records,
                                 "role_upgrade": role_upgrade,
                                 "note": "产品无删除用户 API；创建/改写均只发生在隔离实例，随实例销毁"}))
        out["auth_users_admin_write"] = role_upgrade

    # ---- AUTH-MANAGEMENT: 无凭据 / 非法 / 已撤销 ----
    list_before = user_digest(hub, admin_token)
    revoked_token = hub.token(admin_user, admin_pw)
    revoked_me_before = hub.req("GET", "/api/me", token=revoked_token)["status"]
    hub.req("POST", "/api/logout", token=revoked_token)
    revoked_me_after = hub.req("GET", "/api/me", token=revoked_token)["status"]

    rows = []
    for tpl in READ_ENDPOINTS:
        path = tpl.replace("{id}", str(uuid.uuid4()))
        r_anon = hub.req("GET", path)
        r_bad = hub.req("GET", path, token="invalid-" + uuid.uuid4().hex)
        r_rev = hub.req("GET", path, token=revoked_token)
        rows.append({
            "path": path,
            "anon": C.body_summary(r_anon),
            "invalid_bearer": C.body_summary(r_bad),
            "revoked_session": C.body_summary(r_rev),
        })
    C.write_evidence("AUTH-MANAGEMENT-anon-invalid-revoked-matrix.json",
                     C.stub({"hub": base_url(mc),
                             "revoked_token_live_before_logout_http": revoked_me_before,
                             "revoked_token_after_logout_http": revoked_me_after,
                             "endpoints": rows}))
    out["auth_management_401_matrix"] = {
        "endpoints": len(rows),
        "anon_all_401": all(r["anon"]["status"] == 401 for r in rows),
        "invalid_all_401": all(r["invalid_bearer"]["status"] == 401 for r in rows),
        "revoked_all_401": all(r["revoked_session"]["status"] == 401 for r in rows),
        "anon_error_strings": sorted({r["anon"].get("error") for r in rows}),
        "invalid_error_strings": sorted({r["invalid_bearer"].get("error") for r in rows}),
        "revoked_error_strings": sorted({r["revoked_session"].get("error") for r in rows}),
    }

    # ---- AUTH-MANAGEMENT: 越权写矩阵 ----
    if "operator" in creds_by_role and "viewer" in creds_by_role:
        op = creds_by_role["operator"]
        vw = creds_by_role["viewer"]
        # 真实存在的 Node：用于 /api/nodes/{id} 的 DELETE / revoke-credential 越权判定
        enr = hub.req("POST", "/api/nodes/enrollment", token=admin_token,
                      body={"node_name": f"qa-papi-{inst_suffix}-ctrl",
                            "node_ip": C.HOST})
        real_node_id = (enr["json"] or {}).get("node_id")
        out["control_node"] = {"enrollment_http": enr["status"],
                               "enrollment_error": (enr["json"] or {}).get("error"),
                               "node_id_present": bool(real_node_id),
                               "note": "仅用于隔离实例上的越权 DELETE 404/403 对照；随实例销毁"}
        wrows = []
        for method, tpl, body, kind in WRITE_ENDPOINTS:
            path = tpl.replace("{id}", str(uuid.uuid4()))
            if "{node_id}" in path:
                path = path.replace("{node_id}", real_node_id or str(uuid.uuid4()))
            for role, tok in (("operator", op["token"]), ("viewer", vw["token"])):
                r = hub.req(method, path, token=tok, body=body)
                wrows.append({"role": role, "method": method, "path": path, "policy": kind,
                              **C.body_summary(r)})
        C.write_evidence("AUTH-MANAGEMENT-role-write-matrix.json",
                         C.stub({"hub": base_url(mc),
                                 "real_node_id": real_node_id,
                                 "note": "operator/viewer 尝试写/管理端点；403 在 mutation 之前返回",
                                 "matrix": wrows}))
        admin_only = [w for w in wrows if w["policy"] == "admin"]
        nonexistent_node = [w for w in wrows if w["policy"] == "admin-nonexistent-node"]
        run_allowed = [w for w in wrows if w["policy"] == "run"]
        audit_rows = [w for w in wrows if w["policy"] == "read-only-handler"]
        out["auth_management_write_matrix"] = {
            "admin_only_records": len(admin_only),
            "admin_only_all_403": all(w["status"] == 403 for w in admin_only),
            "admin_only_non403": [{"role": w["role"], "method": w["method"], "path": w["path"],
                                   "status": w["status"], "error": w.get("error")}
                                  for w in admin_only if w["status"] != 403],
            "nonexistent_node_delete": [{"role": w["role"], "status": w["status"],
                                         "error": w.get("error")} for w in nonexistent_node],
            "operator_run_statuses": sorted({(w["method"], w["status"]) for w in run_allowed
                                             if w["role"] == "operator"}),
            "viewer_run_statuses": sorted({(w["method"], w["status"]) for w in run_allowed
                                           if w["role"] == "viewer"}),
            "audit_post_statuses": sorted({(w["role"], w["status"]) for w in audit_rows}),
        }
    list_after = user_digest(hub, admin_token)
    C.write_evidence("AUTH-MANAGEMENT-state-invariance.json",
                     C.stub({"hub": base_url(mc),
                             "users_before_negative_probes": list_before,
                             "users_after_negative_probes": list_after,
                             "unchanged": list_before == list_after}))
    out["auth_management_state_invariance"] = {"unchanged": list_before == list_after,
                                                "before": list_before, "after": list_after}

    # ---- AUTH-MANAGEMENT: natural TTL（短 TTL 隔离实例）----
    t_admin = ttlhub.token(tc["admin_username"], tc["admin_password"])
    ttl_before = ttlhub.req("GET", "/api/me", token=t_admin)
    time.sleep(6)
    ttl_rows = []
    for tpl in READ_ENDPOINTS:
        path = tpl.replace("{id}", str(uuid.uuid4()))
        r = ttlhub.req("GET", path, token=t_admin)
        ttl_rows.append({"path": path, **C.body_summary(r)})
    C.write_evidence("AUTH-MANAGEMENT-expired-ttl-matrix.json",
                     C.stub({"hub": base_url(tc), "session_ttl": tc["session_ttl"],
                             "me_before_expiry_http": ttl_before["status"],
                             "waited_sec": 6,
                             "endpoints": ttl_rows}))
    out["auth_management_natural_ttl"] = {
        "session_ttl": tc["session_ttl"],
        "me_before_expiry_http": ttl_before["status"],
        "expired_all_401": all(r["status"] == 401 for r in ttl_rows),
        "endpoints": len(ttl_rows),
        "error_strings": sorted({r.get("error") for r in ttl_rows}),
    }

    C.write_evidence("AUTH-MANAGEMENT-summary.json", C.stub(out))
    (C.ROOT / "auth-probe-summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
