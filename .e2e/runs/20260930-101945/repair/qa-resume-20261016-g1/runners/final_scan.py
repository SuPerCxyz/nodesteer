"""收尾核验：远端 nonce 残留全盘扫描 + 本地证据凭据泄漏扫描 + 更新交付 JSON。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())

    # 1) 远端全盘残留扫描（排除系统路径噪声）
    scan = C.ssh(f"""
find / -xdev -name '*{nonce}*' -not -path '/proc/*' -not -path '/sys/*' 2>/dev/null | head -20
echo "SCAN_DONE"
echo "PGREP:$(pgrep -af 'qa-g1|{nonce}' | grep -v 'pgrep\\|bash -c' | wc -l)"
echo "PORT_18085:$(ss -ltn | grep -c ':18085 ' || true)"
echo "UNIT_MATCH:$(find /etc/systemd/system -name '*{nonce}*' | wc -l)"
echo "CRON_MATCH:$(grep -rl '{nonce}' /etc/cron* 2>/dev/null | wc -l)"
echo "TMP_MATCH:$(ls -d /tmp/*{nonce}* 2>/dev/null | wc -l)"
""", check=False)
    lines = scan.stdout.splitlines()
    scan_paths = [l for l in lines if l.startswith("/")]
    scan_checks = {}
    for l in lines:
        if ":" in l:
            k, v = l.split(":", 1)
            scan_checks[k] = v.strip()

    # 2) 本地证据/终端目录凭据泄漏扫描（仅结构判定，不输出命中内容）
    pat = re.compile(r"(eyJ[A-Za-z0-9_\-]{10,}|Bearer\s+[A-Za-z0-9_\-]{20,}|"
                     r"registration_token:\s*\S|admin_password\s*[:=]\s*\S|"
                     r"nodesteer_token'\s*,\s*'[A-Za-z0-9])")
    hits = []
    secret_values = []
    for f in list(C.EVID.rglob("*")) + list(C.TERM.rglob("*")):
        if not f.is_file():
            continue
        try:
            txt = f.read_text(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for m in pat.finditer(txt):
            hits.append({"file": str(f.relative_to(C.ROOT)), "pattern": m.group(0)[:6] + "…"})
    cred_scan = {"files_scanned": len([p for p in list(C.EVID.rglob("*")) + list(C.TERM.rglob("*"))
                                      if p.is_file()]),
                 "suspicious_hits": hits, "result": "CLEAN" if not hits else "REVIEW"}

    # 3) 更新 case-result.json / progress.json
    cr = json.loads((C.ROOT / "case-result.json").read_text())
    oracle = json.loads((C.ROOT / "final-cleanup-oracle.json").read_text())
    cr["teardown"] = {
        "oracle": "final-cleanup-oracle.json",
        "result": oracle["result"],
        "zero_residue": oracle["verdict"]["zero_residue"],
        "collateral_unaffected": oracle["verdict"]["collateral_unaffected"],
        "app_deployment_unit_side_effect": {
            "unit": "nodesteer-qa-<nonce>.service",
            "cause": "P14P15-12 的真实应用部署（owned child 在本机执行 systemd 单元安装/启用）",
            "cleaned": True,
            "detail": oracle.get("app_unit_cleanup", {}).get("repaired"),
        },
        "remote_full_scan": {"paths": scan_paths, "checks": scan_checks},
        "credential_scan": cred_scan,
    }
    cr["observations_and_findings"] = [
        {"id": "DEF-ASSIGN-MISSING-APP-500", "severity": "medium",
         "title": "对不存在的 application 调用 assign 返回 500 且回显内部 SQL 错误；deploy 亦回显同一内部错误（400）",
         "case_impact": "P14P15-12（admin 正向对照路径）；不影响 P04-06/P21-08 的 403 结论",
         "evidence": "evidence/DEF-missing-application-matrix.json",
         "observed": {"assign": {"http": 500, "body": "sql: no rows in result set"},
                      "deploy": {"http": 400, "body": "sql: no rows in result set"},
                      "get": {"http": 404, "body": "application not found"},
                      "put": {"http": 404, "body": "application not found"},
                      "delete": {"http": 200, "body": "{\"ok\":\"true\"}"}}},
        {"id": "OBS-UI-LOGOUT-NO-SERVER-REVOKE", "severity": "low-medium",
         "title": "Web UI 退出登录不调用 POST /api/logout（仅本地清 token + 跳转），服务端会话在 TTL 内仍有效",
         "case_impact": "X13（另一标签退出后的保护页面行为）",
         "evidence": "evidence/X13-other-tab-logout-token-replacement.json"},
        {"id": "OBS-USER-DUP-SQL-MESSAGE", "severity": "low",
         "title": "重复用户名时 API 与 UI toast 直接显示 SQLite 唯一约束原文（状态码 400 正确）",
         "case_impact": "P20-04", "evidence": "evidence/P20-04-users-create-validation.json"},
        {"id": "OBS-USER-INPUT-NOT-TRIMMED", "severity": "low",
         "title": "用户名不做 trim/空白校验：纯空白用户名可创建且可登录；密码无最小长度；用户名无长度上限",
         "case_impact": "P20-04（admin-only 路径）", "evidence": "evidence/P20-04-users-create-validation.json"},
        {"id": "OBS-ENROLL-TRANSIENT-500", "severity": "low",
         "title": "快速连续创建节点时曾出现 3/12 次 POST /api/nodes/enrollment = HTTP 500；同参数立即重试成功，"
                  "后续 6 次连发与 3 次重试均成功（未复现）",
         "case_impact": "X14 夹具准备（无断言依赖）", "evidence": "evidence/OBS-enrollment-rapid-burst.json"},
        {"id": "OBS-AUDIT-METHOD-200", "severity": "low",
         "title": "既有观测（batch05）复核仍成立：/api/audit 无方法分发，非 GET 方法返回 200 与审计列表（无写入）",
         "case_impact": "P19-03（无影响）", "evidence": "evidence/fixture-ledger.json / 本批未重复探测"},
        {"id": "OBS-APP-DEPLOY-UNIT-SIDE-EFFECT", "severity": "info",
         "title": "真实应用部署在 Hub 主机创建 systemd 单元 nodesteer-qa-<nonce>.service（enabled，因二进制不存在进入 activating 重试）；"
                  "收尾已 stop/disable/删除并 daemon-reload",
         "case_impact": "P14P15-12（真实执行副产物）；收尾零残留核验通过",
         "evidence": "final-cleanup-oracle.json#app_unit_cleanup"},
    ]
    (C.ROOT / "case-result.json").write_text(json.dumps(cr, ensure_ascii=False, indent=1) + "\n")

    pr = json.loads((C.ROOT / "progress.json").read_text())
    pr["phase"] = "COMPLETE_PENDING_ROOT_REVIEW"
    pr["updated_at_utc"] = C.utc()
    pr["steps"] = [s if s["name"] != "收尾：销毁实例 + 零残留核验 + 凭据清理"
                   else {"name": s["name"], "status": "done", "result": oracle["result"]} for s in pr["steps"]]
    pr["teardown"] = {"result": oracle["result"], "zero_residue": oracle["verdict"]["zero_residue"],
                      "collateral_unaffected": oracle["verdict"]["collateral_unaffected"],
                      "credential_scan": cred_scan["result"],
                      "remote_full_scan_paths": scan_paths}
    pr["harness_anomalies"] = [
        "执行期间客户端（OpenCode 服务端）发生一次重启，中断了 X13 的一次运行；未重复已完成步骤，"
        "受影响脚本已重跑并复核实例/夹具状态后继续。",
        "agent-browser 的 tab 引用必须是稳定 id（t1/t2），数字下标不会切换上下文；"
        "已改用 tN 并对每个 tab 打 window 标记核对身份。",
        "agent-browser eval 返回纯字符串（如 location.pathname）时带引号且长值可能被换行，"
        "涉及 token/路径的判定改为页面内完成（避免把 token 取到进程外）。",
        "首次 teardown 后发现真实应用部署遗留 systemd 单元，已在收尾脚本中加入 stop/disable/删除并重跑 oracle。",
    ]
    (C.ROOT / "progress.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1) + "\n")

    print(json.dumps({"remote_scan_paths": scan_paths, "remote_checks": scan_checks,
                      "credential_scan": cred_scan, "teardown": cr["teardown"]["result"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
