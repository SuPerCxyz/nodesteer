"""p-api：API-CONTRACT 真实契约矩阵（隔离实例 main）。

覆盖：公共端点、未认证方法矩阵、admin 方法矩阵、必填字段错误 JSON、
成功 CRUD 全链路（创建→读取→更新→删除→再读 404）与副作用核验。

产出 evidence：API-CONTRACT-*.json
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papi_common as C  # noqa: E402

MAIN_TAG = "papim"
METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]

COLLECTIONS = ["/api/nodes", "/api/groups", "/api/scripts", "/api/tasks", "/api/schedules",
               "/api/artifacts", "/api/applications", "/api/users", "/api/transfers",
               "/api/executions", "/api/audit", "/api/settings"]
ITEMS = ["/api/nodes/{id}", "/api/groups/{id}", "/api/scripts/{id}", "/api/tasks/{id}",
         "/api/schedules/{id}", "/api/artifacts/{id}", "/api/applications/{id}",
         "/api/users/{id}", "/api/transfers/{id}", "/api/executions/{id}"]
SINGLETONS = ["/api/logout", "/api/me", "/api/nodes/enrollment", "/api/nodes/upgrade", "/metrics"]
# admin 方法矩阵排除 /api/logout：任何方法都会注销当前会话，破坏后续断言（单独用一次性会话验证）
SINGLETONS_ADMIN = ["/api/me", "/api/nodes/enrollment", "/api/nodes/upgrade", "/metrics"]


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    inst = next(i for i in led["instances"] if i["nonce"].startswith(MAIN_TAG))
    cr = C.load_creds(inst["nonce"])
    base = f"http://127.0.0.1:{cr['tunnel_port']}"
    hub = C.Hub(base)
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    out = {"hub": base, "instance_nonce": inst["nonce"]}

    # ---------- 1. 公共端点（无凭据） ----------
    pub = []
    for method, path in [("GET", "/healthz"), ("GET", "/readyz"), ("GET", "/api/oidc/state"),
                         ("GET", "/api/oidc/login"), ("GET", "/api/oidc/callback"),
                         ("POST", "/api/login"), ("GET", "/api/agent/binary?architecture=unknowarch")]:
        r = hub.req(method, path)
        rec = {"method": method, "path": path, **C.body_summary(r)}
        if path.startswith("/api/agent/binary"):
            rec["content_length"] = r.get("content_length")
            rec["body_raw_head"] = r["body_raw"][:80].decode(errors="replace")
        pub.append(rec)
    binary_head = hub.req("GET", "/api/agent/binary?architecture=amd64", headers={"Range": "bytes=0-31"})
    pub.append({"method": "GET", "path": "/api/agent/binary?architecture=amd64 (Range 0-31)",
                "status": binary_head["status"],
                "content_length": binary_head.get("content_length"),
                "x_agent_binary_sha256": binary_head["headers"].get("X-Agent-Binary-SHA256"),
                "content_range": binary_head["headers"].get("Content-Range")})
    C.write_evidence("API-CONTRACT-public-endpoints.json",
                     C.stub({"hub": base, "records": pub}))
    out["public_endpoints"] = pub

    # ---------- 2. 未认证方法矩阵 ----------
    unauth = []
    paths = COLLECTIONS + ITEMS + SINGLETONS
    for tpl in paths:
        p = tpl.replace("{id}", str(uuid.uuid4()))
        row = {"path": p, "statuses": {}}
        for m in METHODS:
            r = hub.req(m, p)
            row["statuses"][m] = r["status"]
            if m == "OPTIONS":
                row["options_cors"] = {
                    "allow_origin": r["headers"].get("Access-Control-Allow-Origin"),
                    "allow_methods": r["headers"].get("Access-Control-Allow-Methods"),
                    "allow_headers": r["headers"].get("Access-Control-Allow-Headers"),
                    "content_length": r.get("content_length"),
                }
        unauth.append(row)
    non_options = [(r["path"], m, s) for r in unauth for m, s in r["statuses"].items() if m != "OPTIONS"]
    C.write_evidence("API-CONTRACT-unauth-method-matrix.json",
                     C.stub({"hub": base, "matrix": unauth,
                             "all_non_options_401": all(s == 401 for _, _, s in non_options),
                             "options_all_204": all(r["statuses"]["OPTIONS"] == 204 for r in unauth)}))
    out["unauth_method_matrix"] = {
        "endpoints": len(unauth), "all_non_options_401": all(s == 401 for _, _, s in non_options),
        "options_all_204": all(r["statuses"]["OPTIONS"] == 204 for r in unauth),
        "non_401": [{"path": p, "method": m, "status": s} for p, m, s in non_options if s != 401],
    }

    # ---------- 3. admin 方法矩阵（空体/不存在 id） ----------
    # body 覆盖：仅 groups 的 POST/PUT 在空体下会以空 name 落库（并触发后续 UNIQUE），
    # 因此对 groups 使用唯一合法 name，矩阵记录真实状态后再删除，保持实例状态可预期。
    name_mm = "qa-papi-mm-" + inst["nonce"][-8:]
    body_overrides = {
        ("POST", "/api/groups"): {"name": name_mm + "-post", "type": "static"},
        ("PUT", "/api/groups/{id}"): {"name": name_mm + "-put", "type": "static"},
    }
    created_for_cleanup = []
    adm = []
    for tpl in COLLECTIONS + ITEMS + SINGLETONS_ADMIN:
        p = tpl.replace("{id}", str(uuid.uuid4()))
        row = {"path": p, "probes": []}
        for m in METHODS:
            body = body_overrides.get((m, tpl))
            if body is None and m in ("POST", "PUT"):
                body = {}
            r = hub.req(m, p, token=admin, body=body)
            row["probes"].append({"method": m, **C.body_summary(r),
                                  "content_type": r["headers"].get("Content-Type")})
            if m in ("POST", "PUT") and isinstance(r["json"], dict) and r["json"].get("id") \
                    and tpl in ("/api/groups", "/api/scripts", "/api/tasks", "/api/applications") \
                    and r["status"] == 200:
                created_for_cleanup.append((tpl if "{" not in tpl
                                            else tpl.replace("/{id}", ""), r["json"]["id"]))
        adm.append(row)
    # 清理矩阵过程中真实创建的资源（隔离实例，仍保持可预期状态）
    cleanup_records = []
    for coll, rid in created_for_cleanup:
        d = hub.req("DELETE", f"{coll}/{rid}", token=admin)
        cleanup_records.append({"collection": coll, "id": rid, "delete_http": d["status"]})
    C.write_evidence("API-CONTRACT-admin-method-matrix.json",
                     C.stub({"hub": base, "matrix": adm, "matrix_cleanup": cleanup_records}))
    out["admin_method_matrix"] = [
        {"path": r["path"], **{p["method"]: p["status"] for p in r["probes"]}} for r in adm]

    # 一次性会话验证 /api/logout 契约（任何方法都注销，且注销后 token 失效）
    tmp_token = hub.token(cr["admin_username"], cr["admin_password"])
    lo_before = hub.req("GET", "/api/me", token=tmp_token)
    lo = hub.req("GET", "/api/logout", token=tmp_token)
    lo_after = hub.req("GET", "/api/me", token=tmp_token)
    logout_contract = {
        "get_logout_http": lo["status"], "get_logout_body": lo["json"],
        "me_before": lo_before["status"], "me_after": lo_after["status"],
        "note": "handleLogout 无方法开关：GET/POST 均注销并返回 200 {ok:true}；注销后旧 token 401",
    }
    C.write_evidence("API-CONTRACT-logout.json", C.stub({"hub": base, "logout": logout_contract}))
    out["logout_contract"] = logout_contract

    # ---------- 4. 必填字段错误 JSON ----------
    reqs = []
    cleanup = []
    for p in COLLECTIONS:
        r = hub.req("POST", p, token=admin, body={})
        rec = {"method": "POST", "path": p, **C.body_summary(r)}
        if r["status"] == 200 and isinstance(r["json"], dict) and r["json"].get("id"):
            rec["created_id"] = r["json"]["id"]
            d = hub.req("DELETE", f"{p}/{r['json']['id']}", token=admin)
            cleanup.append({"path": p, "id": r["json"]["id"], "delete_http": d["status"]})
        reqs.append(rec)
    for p in ["/api/groups/{id}", "/api/scripts/{id}", "/api/tasks/{id}", "/api/schedules/{id}",
              "/api/applications/{id}", "/api/users/{id}"]:
        path = p.replace("{id}", str(uuid.uuid4()))
        r = hub.req("PUT", path, token=admin, body={})
        reqs.append({"method": "PUT", "path": path, **C.body_summary(r)})
    r = hub.req("POST", "/api/login", body={})
    reqs.append({"method": "POST", "path": "/api/login (empty)", **C.body_summary(r)})
    r = hub.req("POST", "/api/nodes/enrollment", token=admin, body={})
    reqs.append({"method": "POST", "path": "/api/nodes/enrollment (empty)", **C.body_summary(r)})
    r = hub.req("POST", "/api/nodes/enrollment", token=admin, body={"node_name": "qa!bad", "node_ip": "1.1.1.1"})
    reqs.append({"method": "POST", "path": "/api/nodes/enrollment (bad node_name)", **C.body_summary(r)})
    r = hub.req("POST", "/api/nodes/enrollment", token=admin, body={"node_name": "qa-papi-x", "node_ip": "not-an-ip!"})
    reqs.append({"method": "POST", "path": "/api/nodes/enrollment (bad node_ip)", **C.body_summary(r)})
    r = hub.req("POST", "/api/login", body={"username": "nobody", "password": "bad"})
    reqs.append({"method": "POST", "path": "/api/login (bad creds)", **C.body_summary(r)})
    r = hub.req("GET", "/api/transfers/" + str(uuid.uuid4()), token=admin)
    reqs.append({"method": "GET", "path": "/api/transfers/{unknown}", **C.body_summary(r)})
    C.write_evidence("API-CONTRACT-required-fields.json",
                     C.stub({"hub": base, "records": reqs, "cleanup_of_created": cleanup}))
    out["required_fields"] = reqs
    out["required_fields_cleanup"] = cleanup

    # ---------- 5. 成功 CRUD 全链路（隔离实例） ----------
    crud = []

    def cycle(kind: str, collection: str, item_tpl: str, body: dict, update: dict):
        rec = {"resource": kind, "steps": []}
        r = hub.req("POST", collection, token=admin, body=body)
        rec["steps"].append({"step": "create", **C.body_summary(r),
                             "returned_keys": sorted((r["json"] or {}).keys())
                             if isinstance(r["json"], dict) else None})
        rid = (r["json"] or {}).get("id") if isinstance(r["json"], dict) else None
        if not rid:
            rec["result"] = "CREATE_FAILED"
            return rec
        item = item_tpl.replace("{id}", rid)
        g = hub.req("GET", item, token=admin)
        rec["steps"].append({"step": "get_after_create", **C.body_summary(g)})
        u = hub.req("PUT", item, token=admin, body={**body, **update})
        rec["steps"].append({"step": "update", **C.body_summary(u)})
        g2 = hub.req("GET", item, token=admin)
        rec["steps"].append({"step": "get_after_update", **C.body_summary(g2)})
        d = hub.req("DELETE", item, token=admin)
        rec["steps"].append({"step": "delete", **C.body_summary(d)})
        g3 = hub.req("GET", item, token=admin)
        rec["steps"].append({"step": "get_after_delete", **C.body_summary(g3)})
        rec["resource_id"] = rid
        rec["result"] = "OK"
        return rec

    name = "qa-papi-" + inst["nonce"][-8:]
    crud.append(cycle("group", "/api/groups", "/api/groups/{id}",
                      {"name": name + "-grp", "type": "static"},
                      {"name": name + "-grp2"}))
    crud.append(cycle("script", "/api/scripts", "/api/scripts/{id}",
                      {"name": name + "-scr", "content": "echo hi", "interpreter": "shell"},
                      {"content": "echo bye"}))
    crud.append(cycle("task", "/api/tasks", "/api/tasks/{id}",
                      {"name": name + "-task", "type": "command", "command": "true",
                       "target": {"type": "label", "label_key": "qa-papi"}},
                      {"command": "false"}))
    crud.append(cycle("application", "/api/applications", "/api/applications/{id}",
                      {"name": name + "-app", "command": "true"}, {"description": "x"}))
    C.write_evidence("API-CONTRACT-crud-selective.json",
                     C.stub({"hub": base, "cycles": crud,
                             "note": "schedule 需真实 task_id、artifact 需 multipart，单独验证"}))
    out["crud_selective"] = [{"resource": c["resource"], "result": c["result"],
                              "statuses": [(s["step"], s["status"]) for s in c["steps"]]}
                             for c in crud]

    # schedule：需要真实 task_id
    t = hub.req("POST", "/api/tasks", token=admin,
                body={"name": name + "-sched-task", "type": "command", "command": "true",
                      "target": {"type": "label", "label_key": "qa-papi"}})
    task_id = (t["json"] or {}).get("id")
    sched = {"create_http": None, "steps": []}
    if task_id:
        s = hub.req("POST", "/api/schedules", token=admin,
                    body={"task_id": task_id, "type": "cron", "expression": "0 3 * * *",
                          "timezone": "UTC", "execution_owner": "hub",
                          "offline_policy": "hub_online_required", "misfire_policy": "skip",
                          "enabled": True})
        sched["create_http"] = s["status"]
        sid = (s["json"] or {}).get("id")
        sched["steps"].append({"step": "create", **C.body_summary(s)})
        if sid:
            sched["steps"].append({"step": "get", **C.body_summary(hub.req("GET", f"/api/schedules/{sid}", token=admin))})
            sched["steps"].append({"step": "delete", **C.body_summary(hub.req("DELETE", f"/api/schedules/{sid}", token=admin))})
            sched["steps"].append({"step": "get_after_delete", **C.body_summary(hub.req("GET", f"/api/schedules/{sid}", token=admin))})
        hub.req("DELETE", f"/api/tasks/{task_id}", token=admin)
    C.write_evidence("API-CONTRACT-schedule-crud.json", C.stub({"hub": base, "schedule": sched}))
    out["schedule_crud"] = sched

    # artifact：真实 multipart 上传 + 下载
    artifact = {}
    token = None
    boundary = "----papi" + uuid.uuid4().hex
    content = ("qa-papi-artifact-" + inst["nonce"]).encode() * 200
    parts = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"name\"\r\n\r\n{name}-art\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"version\"\r\n\r\n1.0.0\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"architecture\"\r\n\r\namd64\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"papi.bin\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    up = hub.req("POST", "/api/artifacts", token=admin, raw=parts,
                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    artifact["upload"] = {**C.body_summary(up), "size_expected": len(content)}
    aid = (up["json"] or {}).get("id") if isinstance(up["json"], dict) else None
    if aid:
        dl = hub.req("GET", f"/api/artifacts/{aid}/download", token=admin)
        artifact["download"] = {**C.body_summary(dl), "content_length": dl.get("content_length"),
                                "sha256_expected": C.sha256_bytes(content),
                                "sha256_actual": C.sha256_bytes(dl["body_raw"]),
                                "match": C.sha256_bytes(content) == C.sha256_bytes(dl["body_raw"])}
        del_r = hub.req("DELETE", f"/api/artifacts/{aid}", token=admin)
        artifact["delete"] = C.body_summary(del_r)
    C.write_evidence("API-CONTRACT-artifact-roundtrip.json", C.stub({"hub": base, "artifact": artifact}))
    out["artifact_roundtrip"] = artifact

    # ---------- 6. 契约一致性观察 ----------
    obs = {}
    # 错误 JSON 形状统一性
    shapes = set()
    for path in ["/api/users", "/api/groups", "/api/tasks"]:
        r = hub.req("GET", path)
        shapes.add((C.shape(r["json"]), tuple(sorted((r["json"] or {}).keys())) if isinstance(r["json"], dict) else None))
    obs["error_envelope_samples"] = sorted(str(s) for s in shapes)
    # 成功列表形状 + X-Total-Count
    total_counts = {}
    for path in ["/api/nodes", "/api/executions", "/api/audit", "/api/users", "/api/groups",
                 "/api/scripts", "/api/tasks", "/api/schedules", "/api/artifacts",
                 "/api/applications", "/api/transfers"]:
        r = hub.req("GET", path, token=admin)
        total_counts[path] = {"status": r["status"], "shape": C.shape(r["json"]),
                              "x_total_count": r["headers"].get("X-Total-Count")}
    obs["list_endpoints"] = total_counts
    # /api/me 与 /api/login 返回键约定
    me = hub.req("GET", "/api/me", token=admin)
    lg = hub.login(cr["admin_username"], cr["admin_password"])
    obs["me_body_keys"] = sorted((me["json"] or {}).keys())
    obs["login_body_keys"] = sorted((lg["json"] or {}).keys())
    obs["login_user_keys"] = sorted((lg["json"].get("user") or {}).keys()) if isinstance(lg["json"], dict) else None
    # CORS 响应头
    obs["cors_headers"] = {"allow_origin": me["headers"].get("Access-Control-Allow-Origin"),
                           "allow_methods": me["headers"].get("Access-Control-Allow-Methods"),
                           "expose_headers": me["headers"].get("Access-Control-Expose-Headers")}
    C.write_evidence("API-CONTRACT-observations.json", C.stub({"hub": base, "observations": obs}))
    out["observations"] = obs

    C.write_evidence("API-CONTRACT-summary.json", C.stub(out))
    (C.ROOT / "contract-probe-summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
