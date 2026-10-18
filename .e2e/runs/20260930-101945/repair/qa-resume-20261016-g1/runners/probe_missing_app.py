"""缺陷/契约探针：不存在的 application id 在应用子端点上的实际响应（隔离实例内）。"""
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
    ghost = "00000000-0000-0000-0000-000000000000"
    ev = {"at_utc": C.utc(), "authority": cr["base_url"], "ghost_app_id": ghost, "probes": {}}

    # 干净复现：新建 → 删除 → 对已删除 id 调用各子端点
    tmp = hub.post("/api/applications", admin, body={
        "name": f"qa-{nonce}-def-probe", "version": "1.0.0",
        "binary_path": f"/usr/local/bin/qa-{nonce}-def", "unit_name": f"nodesteer-qa-def-{nonce}.service"})
    tmp_id = (tmp["json"] or {}).get("id")
    ev["created"] = {"http": tmp["status"], "id": tmp_id}
    ev["created_assign_http"] = hub.post(f"/api/applications/{tmp_id}/assign", admin,
                                         body={"node_ids": [node]})["status"]
    d = hub.delete(f"/api/applications/{tmp_id}", admin)
    ev["deleted"] = {"http": d["status"]}
    ev["after_delete_get"] = hub.get(f"/api/applications/{tmp_id}", admin)["status"]

    probes = [
        ("GET", f"/api/applications/{tmp_id}", None),
        ("PUT", f"/api/applications/{tmp_id}", {"id": tmp_id, "name": "x", "version": "9"}),
        ("DELETE", f"/api/applications/{tmp_id}", None),
        ("POST", f"/api/applications/{tmp_id}/assign", {"node_ids": [node]}),
        ("POST", f"/api/applications/{tmp_id}/assign", {"node_ids": [node], "remove": True}),
        ("POST", f"/api/applications/{tmp_id}/deploy", {"node_ids": [node], "operation": "deploy"}),
        ("GET", f"/api/applications/{tmp_id}/state", None),
        ("GET", f"/api/applications/{tmp_id}/executions", None),
        ("GET", f"/api/applications/{tmp_id}/nodes", None),
        ("GET", f"/api/applications/{tmp_id}/revisions", None),
    ]
    for method, path, body in probes:
        r = hub.req(method, path, admin, body=body) if body is not None else hub.req(method, path, admin)
        ev["probes"][f"{method} {path.split('/')[-1] if path.split('/')[-1] != tmp_id else 'application'}"] = {
            "method": method, "path": path, "http": r["status"],
            "body": (r["body_raw"] or b"")[:200].decode(errors="replace")}

    ev["finding"] = {
        "id": "DEF-ASSIGN-MISSING-APP-500",
        "severity": "medium",
        "title": "对不存在的 application 调用 assign 返回 500 并回显内部 SQL 错误（应 404/400）",
        "repro": ["POST /api/applications/{id}/assign 使用已删除（或从未存在）的 id",
                  "→ HTTP 500 {\"error\":\"sql: no rows in result set\"}"],
        "impact": "错误语义与状态码错误；向调用方回显持久层内部错误文本；对 UI 的分配流程表现为“服务器错误”而非“应用不存在”。",
        "note": "同 id 下 deploy → 400 application not found、GET → 404、PUT → 404，说明仅 assign 路径缺少存在性前置检查。",
    }
    C.write_evidence("DEF-missing-application-matrix.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    main()
