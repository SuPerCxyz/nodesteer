"""G8（X16）泄漏面扫描：Registration Token / Authorization / Cookie 是否进入响应、页面或交付证据。

真实凭据只在本进程内存中用于比对；结果只输出“命中次数 + 文件名”，绝不回显被扫字符串。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402

REPAIR_CRED_DIR = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO")


def load_repair_secrets() -> dict:
    out = {}
    try:
        d = json.loads((REPAIR_CRED_DIR / "repair-credentials.json").read_text())
        out["repair_admin_password"] = d.get("admin_password", "")
        out["repair_registration_token"] = d.get("registration_token", "")
    except Exception as exc:  # noqa: BLE001
        out["repair_credentials_read_error"] = repr(exc)[:120]
    try:
        d = json.loads((REPAIR_CRED_DIR / "repair-connection.json").read_text())
        out["repair_connection_token"] = d.get("token", "")
    except Exception as exc:  # noqa: BLE001
        out["repair_connection_read_error"] = repr(exc)[:120]
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    needles = {
        "instance_admin_password": cr["admin_password"],
        "instance_registration_token": cr["registration_token"],
        "instance_bearer_token": admin,
        **load_repair_secrets(),
    }
    ev = {"case_id": "X16", "nonce": nonce, "at_utc": C.utc(),
          "needles_tracked": [k for k, v in needles.items() if v],
          "note": "被扫字符串不落证据；仅记录命中位置与次数。"}

    # ---------------- 1. 只读 API 响应扫描 ----------------
    endpoints = ["/api/nodes", "/api/scripts", "/api/tasks", "/api/schedules", "/api/executions",
                 "/api/audit?limit=500", "/api/users", "/api/me", "/api/settings",
                 "/api/transfers", "/api/groups", "/api/applications", "/api/artifacts"]
    if cr.get("script_id"):
        endpoints += [f"/api/scripts/{cr['script_id']}", f"/api/scripts/{cr['script_id']}/revisions"]
    for tid in (cr.get("task_full"), cr.get("task_script_default")):
        if tid:
            endpoints.append(f"/api/tasks/{tid}")
    exs = C.as_list(hub.get("/api/executions?limit=50", admin)["json"])
    for e in exs[:3]:
        endpoints += [f"/api/executions/{e['id']}", f"/api/executions/{e['id']}/logs"]
    api_scan = []
    for ep in endpoints:
        r = hub.get(ep, admin)
        body = (r["body_raw"] or b"").decode(errors="replace")
        hits = {k: body.count(v) for k, v in needles.items() if v and v in body}
        api_scan.append({"endpoint": ep, "http": r["status"], "bytes": len(body), "hits": hits})
    ev["api_readonly_scan"] = {
        "endpoints_scanned": len(api_scan),
        "endpoints_with_hits": [x for x in api_scan if x["hits"]],
        "all": api_scan,
        "verdict": "CLEAN" if not any(x["hits"] for x in api_scan) else "HITS",
    }

    # ---------------- 2. enrollment 响应（设计用途）----------------
    en = hub.post("/api/nodes/enrollment", admin,
                  body={"node_name": f"qa-{nonce}-leakscan", "node_ip": "10.46.0.9",
                        "hub_address": cr["base_url"]})
    en_body = (en["body_raw"] or b"").decode(errors="replace")
    ev["enrollment_response"] = {
        "http": en["status"], "bytes": len(en_body),
        "registration_token_present": cr["registration_token"] in en_body,
        "admin_password_present": cr["admin_password"] in en_body,
        "bearer_present": admin in en_body,
        "keys": sorted((en["json"] or {}).keys()) if isinstance(en["json"], dict) else None,
        "interpretation": ("接入命令（docker_compose/命令片段）内嵌 registration token 属设计用途"
                           "（新节点需凭它注册）；本项只确认未同时泄漏密码/bearer，且该 token 不出现在其它只读接口。"),
    }
    # 该探针节点随后删除（只读优先；这也是本次唯一的 enrollment 副作用）
    nid = (en["json"] or {}).get("node_id") if isinstance(en["json"], dict) else None
    if nid:
        ev["enrollment_probe_node_deleted"] = hub.delete(f"/api/nodes/{nid}", admin)["status"]

    # ---------------- 3. 登录响应头 / Cookie ----------------
    lg = hub.req("POST", "/api/login", body={"username": cr["admin_username"], "password": cr["admin_password"]})
    hdrs = lg.get("headers") or {}
    lg_body = (lg["body_raw"] or b"").decode(errors="replace")
    ev["login_response"] = {
        "http": lg["status"],
        "header_names": sorted(hdrs.keys()),
        "set_cookie_present": any(k.lower() == "set-cookie" for k in hdrs),
        "body_contains_password": cr["admin_password"] in lg_body,
        "body_keys": sorted((lg["json"] or {}).keys()) if isinstance(lg["json"], dict) else None,
        "token_key_present_in_body": bool(isinstance(lg["json"], dict) and lg["json"].get("token")),
    }
    ev["login_token_surface"] = ("登录响应体返回 token（浏览器存 localStorage）；无 Set-Cookie，"
                                 "因此不存在 Cookie 承载会话值的泄漏面（页面侧 document.cookie 为空，见 G8-masking-matrix-ui.json）。")

    # ---------------- 4. 交付证据目录扫描 ----------------
    root = C.ROOT  # repair/
    files = [p for p in C.walk_evidence(root) if p.is_file()]
    text_files = []
    for p in files:
        rel = str(p)
        if any(seg in rel for seg in ("/runners/", "/terminal/", "__pycache__", "/.git/")):
            continue
        try:
            if p.stat().st_size > 8 * 1024 * 1024:
                continue
            raw = p.read_bytes()
        except Exception:  # noqa: BLE001
            continue
        if b"\0" in raw[:4096]:
            continue
        text_files.append((p, raw.decode(errors="replace")))
    hits = []
    for p, text in text_files:
        h = {k: text.count(v) for k, v in needles.items() if v and v in text}
        if h:
            hits.append({"file": str(p), "labels": sorted(h.keys()), "counts": h})
    # 通用模式：真实形态的 Bearer/Authorization 与 Cookie 头
    generic = {"bearer_like": 0, "authorization_header_like": 0, "cookie_header_like": 0}
    generic_files = []
    re_bearer = re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{24,}")
    re_auth = re.compile(r"Authorization[\"'\s:=]+Bearer", re.I)
    re_cookie = re.compile(r"Cookie[\"'\s:=]+[A-Za-z0-9_\-]+=[^\"'\s;]{8,}", re.I)
    for p, text in text_files:
        c = {"file": str(p), "bearer": len(re_bearer.findall(text)),
             "auth_header": len(re_auth.findall(text)), "cookie": len(re_cookie.findall(text))}
        if c["bearer"] or c["auth_header"] or c["cookie"]:
            generic["bearer_like"] += c["bearer"]
            generic["authorization_header_like"] += c["auth_header"]
            generic["cookie_header_like"] += c["cookie"]
            generic_files.append(c)
    # 本次两批自有 evidence 单独统计
    own = []
    for p, text in text_files:
        if "/qa-resume-20261017-g8/" in str(p) or "/qa-resume-20261017-g9/" in str(p):
            h = {k: text.count(v) for k, v in needles.items() if v and v in text}
            own.append({"file": str(p), "hits": h})
    ev["evidence_tree_scan"] = {
        "root": str(root),
        "files_considered": len(files), "text_files_scanned": len(text_files),
        "needle_hits": hits,
        "own_batch_files_with_hits": [x for x in own if x["hits"]],
        "generic_pattern_hits": generic,
        "generic_files": generic_files[:20],
        "verdict": "CLEAN" if (not hits and not generic_files) else "REVIEW",
    }
    C.write_evidence("g8", "G8-credential-leak-scan.json", ev)
    print(json.dumps({
        "api_readonly_scan": {"endpoints_scanned": ev["api_readonly_scan"]["endpoints_scanned"],
                              "with_hits": ev["api_readonly_scan"]["endpoints_with_hits"],
                              "verdict": ev["api_readonly_scan"]["verdict"]},
        "enrollment_response": ev["enrollment_response"],
        "login_response": ev["login_response"],
        "evidence_tree_scan": {k: v for k, v in ev["evidence_tree_scan"].items() if k != "generic_files"},
        "generic_files_sample": generic_files[:5],
    }, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
