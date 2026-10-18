"""G8/X16 收尾：错误路径遮蔽（携带标记值）+ /api/me 会话面 + 证据泄漏面复核。

只读为主（除一次性 enrollment 探针节点，用后即删；本轮不新建其它对象）。
全部动作在隔离实例内；证据只记录“是否回显标记值”，不回显凭据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402

MARK = "QAMARK-ERRPATH-8970c813-0123456789abcdef"  # 与既有 matrix 相同的非敏感标记


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    C.register_redaction(cr["admin_password"], "<ADMIN_PW>")
    C.register_redaction(cr["registration_token"], "<REG_TOKEN>")
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    ev = {"case_id": "X16", "probe": "error-masking-and-session-surface",
          "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"], "marker_label": "QAMARK-ERRPATH-*"}

    def probe(tag, method, path, body):
        r = hub.req(method, path, admin, body=body)
        text = (r["body_raw"] or b"").decode(errors="replace")
        return {"tag": tag, "method": method, "path": path, "http": r["status"],
                "error_head": text[:220].replace(MARK, "<MARKER>"),
                "echoes_marker": MARK in text}

    task_full = cr["task_full"]
    task_missing = cr["task_missing_required"]
    script_id = cr["script_id"]
    bogus = "00000000-0000-0000-0000-000000000000"

    ev["error_paths"] = [
        probe("run_with_supplied_secret_bogus_node", "POST", f"/api/tasks/{task_full}/run",
              {"node_ids": {bogus: True}, "params": {"marker_secret": MARK}}),
        probe("run_missing_required_secret", "POST", f"/api/tasks/{task_missing}/run",
              {"node_ids": {cr["node_id"]: True}, "params": {}}),
        probe("transfer_bogus_source_with_marker_path", "POST", "/api/transfers",
              {"source_node_id": bogus, "source_path": f"/tmp/{MARK}", "targets": []}),
        probe("script_put_bad_type_with_marker_default", "PUT", f"/api/scripts/{script_id}",
              {"id": script_id, "name": f"qa-{nonce}-script", "interpreter": "bash",
               "content": "echo x", "enabled": True,
               "parameters": [{"name": "marker_secret", "type": "not-a-type",
                               "required": False, "default": MARK}]}),
        probe("schedule_create_marker_name_bad_type", "POST", "/api/schedules",
              {"task_id": task_full, "type": "not-a-type", "name": f"qa-{nonce}-{MARK}",
               "interval_sec": 60, "execution_owner": "hub", "timezone": "UTC", "enabled": True}),
    ]
    # 恢复脚本合法定义（若上面 PUT 被接受会污染夹具）
    cur = hub.get(f"/api/scripts/{script_id}", admin)["json"]
    if isinstance(cur, dict):
        fixed = dict(cur)
        fixed["parameters"] = [{"name": "probe", "type": "string", "required": False, "default": "full"},
                               {"name": "marker_secret", "type": "secret", "required": False,
                                "default": cr["markers"]["script_default"]}]
        ev["script_restore"] = hub.put(f"/api/scripts/{script_id}", admin, body=fixed)["status"]
        after = hub.get(f"/api/scripts/{script_id}", admin)["body_raw"].decode(errors="replace")
        ev["script_restore_marker_state"] = {
            "script_default_present": cr["markers"]["script_default"] in after,
            "error_marker_present": MARK in after,
        }
    ev["error_verdict"] = {
        "paths_probed": len(ev["error_paths"]),
        "paths_with_marker_echo": [p["tag"] for p in ev["error_paths"] if p["echoes_marker"]],
        "status_codes": {p["tag"]: p["http"] for p in ev["error_paths"]},
    }

    # 会话面：/api/me 是否回显调用者自己的 token（字段名与相等性；不回显值）
    me = hub.get("/api/me", admin)
    mj = me["json"] if isinstance(me["json"], dict) else {}
    ev["session_surface"] = {
        "me_keys": sorted(mj.keys()),
        "token_field_equals_caller_token": mj.get("Token") == admin,
        "token_field_len": len(mj.get("Token") or ""),
        "note": ("/api/me 返回调用者本人 token（会话信息接口，调用者已持有该值）；"
                 "该 token 不出现在其它只读接口，且不进入证据。"),
    }
    # 其它只读接口是否出现 token 原文
    others = ["/api/nodes", "/api/users", "/api/settings", "/api/audit?limit=200",
              "/api/scripts", "/api/tasks", "/api/executions?limit=20"]
    ev["token_in_other_endpoints"] = {ep: (admin in (hub.get(ep, admin)["body_raw"] or b"").decode(errors="replace"))
                                      for ep in others}

    C.write_evidence("g8", "G8-error-masking-and-session-surface.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:3500])


if __name__ == "__main__":
    main()
