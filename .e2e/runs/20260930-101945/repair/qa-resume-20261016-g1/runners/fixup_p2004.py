"""重算 P20-04 verdict（路径层级修正）+ 补录用户名/密码边界输入观察。仅隔离实例。"""
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
    p = C.EVID / "P20-04-users-create-validation.json"
    ev = json.loads(p.read_text())
    import secrets as _s
    run = _s.token_hex(2)

    # 边界输入补录（用当次唯一用户名，避免 UNIQUE 干扰）
    edge = {}
    ws = f"   {run}   "
    for name, body in (
        ("whitespace_only_username", {"username": ws, "password": "x" * 12, "role": "viewer"}),
        ("one_char_password", {"username": f"qa-{nonce}-edge1char{run}", "password": "a", "role": "viewer"}),
        ("very_long_username", {"username": "L" * 300, "password": "x" * 12, "role": "viewer"}),
        ("unicode_username", {"username": f"qa-{nonce}-用户名{run}", "password": "x" * 12, "role": "viewer"}),
    ):
        r = hub.post("/api/users", admin, body=body)
        row = {"http": r["status"],
               "error": (r["json"] or {}).get("error") if isinstance(r["json"], dict) else None,
               "created": (r["json"] or {}).get("username") if r["status"] == 200 else None}
        if name == "whitespace_only_username":
            row["login_with_whitespace_username"] = hub.login(ws, "x" * 12)["status"]
        edge[name] = row
    ev["edge_inputs"] = edge

    u = ev["ui_create_success"]
    last = u["detail"].get("last_page_check", {})
    ev["verdict"] = {
        "ui_create_success": bool(u["created_in_api"] and u["api_count_after"] == u["api_count_before"] + 1
                                 and u["detail"]["after"]["toast_like"][-1] == "已创建"
                                 and last.get("has_new") is True),
        "ui_create_evidence": {"api_count": [u["api_count_before"], u["api_count_after"]],
                               "toast": u["detail"]["after"]["toast_like"][-1],
                               "row_on_last_page": last.get("has_new"),
                               "screenshot": u["detail"]["shot_after"]},
        "duplicate_ui_400_no_new_row": ev["ui_duplicate"]["duplicate_count"] == 1
                                       and ev["ui_duplicate"]["detail"]["after"]["toast_like"][-1].startswith("constraint failed"),
        "duplicate_api_400": ev["api_duplicate"]["http"] == 400,
        "invalid_inputs": {k: v["http"] for k, v in ev["api_invalid_inputs"].items()},
        "empty_password_rejected": ev["main_user_empty_password"]["api_create_empty_password_rejected"],
        "legacy_empty_hash_login_rejected": ev["legacy_empty_hash_injection"]["login_empty_password_http"] == 401,
        "ui_client_validation": ev["ui_client_validation"],
    }
    ev["observations"] = [
        {"id": "OBS-USER-DUP-SQL-MESSAGE", "severity": "low",
         "detail": "重复用户名/唯一约束冲突时，API 与 UI 直接暴露 SQLite 内部错误文本 "
                   "（示例：constraint failed: UNIQUE constraint failed: users.username (2067)），"
                   "而不是领域化提示；状态码 400 正确。",
         "evidence": "api_duplicate.error / ui_duplicate.detail.after.toast_like"},
        {"id": "OBS-USER-INPUT-NOT-TRIMMED", "severity": "low",
         "detail": "用户名未做 trim/空白校验：首次探测以纯空白用户名可创建成功（P20-04 run1）；"
                   "密码无最小长度限制（1 字符可创建）。均为 admin-only 路径。",
         "evidence": "edge_inputs"},
    ]
    p.write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"verdict": ev["verdict"], "edge_inputs": edge}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
