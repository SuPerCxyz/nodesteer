"""G1 批次收口：生成 case-result.json / progress.json / 更新 fixture-ledger.json（销毁前）。

状态提议依据：本批实测证据（evidence/*.json）；不改动 feature-inventory.json（由 Root 复核后变更）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def load(name):
    p = C.EVID / name
    return json.loads(p.read_text()) if p.exists() else {}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    inv = {}

    p0309 = load("P03-09-pristine-empty-state.json")
    rbac = load("P04-06-P21-08-rbac-matrix.json")
    p2004 = load("P20-04-users-create-validation.json")
    p1415 = load("P14P15-12-app-deploy-permissions.json")
    p1903a = load("P19-03-audit-six-action-classes.json")
    p1903b = load("P19-03-audit-six-classes-verified.json")
    x13 = load("X13-summary.json")
    x14 = load("X14-bulk-selection-drift.json")
    x14b = load("X14-bulk-payload.json")
    missing = load("DEF-missing-application-matrix.json")

    execs = C.as_list(hub.get("/api/executions", admin)["json"])
    audit_code, audit_rows = C.audit_rows(hub, admin, limit=2000)
    users = C.as_list(hub.get("/api/users", admin)["json"])
    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])

    def dims(case_id, status, confidence, verified, remaining, layer, evidence, actual=None):
        return {"case_id": case_id, "before_status": "PARTIAL", "proposed_status": status,
                "confidence": confidence, "verified_dimensions": verified,
                "remaining_or_unverified": remaining, "layer": layer,
                "evidence": evidence, "actual": actual or {}}

    cases = []

    cases.append(dims(
        "P03-09", "PASS", "high",
        [{"dimension": "真空权威（初始 data_dir）上的四个空态：无运行执行 / 无应用 / 无调度 / 无失败执行",
          "result": "PASS", "layer": "real-ui-api",
          "detail": ("API：executions=0（且 status=failed / RUNNING 均为 0）、applications=0、schedules=0、"
                     "nodes=0、tasks=0；UI：仪表盘“当前没有正在执行的任务。”+ 运行中 0 + 执行失败 0 + 成功率 -、"
                     "“暂无托管应用”、“暂无调度”；/applications 与 /schedules 表为空态“暂无数据”；"
                     "/executions?status=failed 空态“没有匹配的执行记录”。"),
          "evidence": ["evidence/P03-09-pristine-empty-state.json",
                       "evidence/P0309-dashboard.png", "evidence/P0309-applications.png",
                       "evidence/P0309-schedules.png", "evidence/P0309-executions_statusfailed.png"]}],
        ["空态断言在“零数据”实例上进行；未覆盖有历史失败数据时的局部空态（该形态已由既有 PARTIAL 维度覆盖）。"],
        "real-ui-api",
        ["evidence/P03-09-pristine-empty-state.json"],
        {"api_pristine": p0309.get("api_verdict"), "ui_empty_states": {k: v.get("empty_state_hits")
                                                                     for k, v in (p0309.get("ui") or {}).items()}}))

    cases.append(dims(
        "P04-06", "PASS", "high",
        [{"dimension": "operator/viewer 对 23 个受限（admin-only）操作的 API 403 矩阵", "result": "PASS",
          "detail": "operator 23/23 = 403，viewer 23/23 = 403（脚本/任务/调度/应用定义与分配/分组/传输/节点状态/"
                    "节点删除/撤销凭证/设置写入/用户管理/角色与密码修改）；唯一非 403 为 DELETE /api/nodes/{不存在 id} → 404，"
                    "与既有 finding AUTHZ-NODE-EXISTENCE-ORACLE（存在性先于鉴权）一致。",
          "evidence": ["evidence/P04-06-P21-08-rbac-matrix.json"]},
         {"dimension": "受限操作的 UI 可见性（真实浏览器会话）", "result": "PASS",
          "detail": "operator/viewer 在 /nodes、/applications、/tasks、/schedules、/scripts、/users 六个页面均无对应创建/管理控件"
                    "（添加节点/新建托管应用/新建任务/新建调度/新建脚本/新建用户 计数均为 0）；admin 正向对照在同一页面均可见（计数 1）。"
                    "/users 页面显示“只有管理员可以管理用户。”。",
          "evidence": ["evidence/P04-06-operator-*.png", "evidence/P04-06-viewer-*.png"]},
         {"dimension": "受限尝试后状态不变（无副作用）", "result": "PASS",
          "detail": "23×2 次受限期物尝试前后 state fingerprint（10 类对象计数 + 用户身份摘要）完全一致。",
          "evidence": ["evidence/P04-06-P21-08-rbac-matrix.json"]},
         {"dimension": "角色边界正向：operator 允许的 run/deploy 真实执行", "result": "PASS",
          "detail": "operator POST /api/tasks/{id}/run → 200（execution 67341403…），POST /api/applications/{id}/deploy → 200"
                    "（execution 5a0d1c71…）；viewer 同端点 403。",
          "evidence": ["evidence/P04-06-P21-08-rbac-matrix.json"]}],
        ["导航栏对受限角色仍渲染“用户”入口（仅页面正文受限），与既有观测 OBS-NAV-ROLE-UNFILTERED 一致，非本用例失败项。"],
        "real-ui-api",
        ["evidence/P04-06-P21-08-rbac-matrix.json"],
        {"operator_403_count": len(rbac.get("restricted_attempts", {}).get("operator", {})),
         "viewer_403_count": len(rbac.get("restricted_attempts", {}).get("viewer", {})),
         "state_unchanged": rbac.get("state_unchanged_after_restricted_attempts")}))

    cases.append(dims(
        "P21-08", "PASS", "high",
        [{"dimension": "Settings 读取（GET /api/settings）对 operator/viewer 允许", "result": "PASS",
          "detail": "两角色 GET 均 200（读取不属受限操作）。",
          "evidence": ["evidence/P04-06-P21-08-rbac-matrix.json"]},
         {"dimension": "Settings 保存按钮显示策略与输入可编辑性", "result": "PASS",
          "detail": "operator/viewer：/settings 运行时输入框全部 disabled，保存按钮计数 0；admin 正向对照：输入可编辑、保存按钮 1。",
          "evidence": ["evidence/P2108-settings-operator.png", "evidence/P2108-settings-viewer.png",
                       "evidence/P2108-settings-admin.png"]},
         {"dimension": "Settings 写入 API 403 且数值不变", "result": "PASS",
          "detail": "PUT /api/settings（heartbeat_interval_sec/changelog_window）operator/viewer 均 403；admin 读回配置与写入前完全一致。",
          "evidence": ["evidence/P04-06-P21-08-rbac-matrix.json"]}],
        [],
        "real-ui-api",
        ["evidence/P04-06-P21-08-rbac-matrix.json"],
        {"settings": {r: {"get": (rbac.get("settings_policy") or {}).get(r, {}).get("get_http"),
                          "put": (rbac.get("settings_policy") or {}).get(r, {}).get("put_http"),
                          "unchanged": (rbac.get("settings_policy") or {}).get(r, {}).get("settings_unchanged")}
                      for r in ("operator", "viewer")}}))

    cases.append(dims(
        "P20-04", "PASS", "high",
        [{"dimension": "UI 真实创建用户成功（列表 + API + 审计）", "result": "PASS",
          "detail": "在 /users 通过表单创建 qa-…-u-ui<run>：toast“已创建”、API 用户数 13→14、最后一页列表出现该用户、"
                    "审计新增 (user, <id>, create) 行。",
          "evidence": ["evidence/P20-04-users-create-validation.json", "evidence/P2004-ui-created-lastpage.png"]},
         {"dimension": "重复用户名（UI + API）", "result": "PASS",
          "detail": "UI 再次创建同名用户：表单保留、toast 显示错误、列表无新增行、重复计数=1；API POST 同用户 400。"
                    "错误正文为 SQLite 唯一约束原文（见 observation）。",
          "evidence": ["evidence/P20-04-users-create-validation.json"]},
         {"dimension": "错误输入（空用户名/空密码/缺密码/非法角色/默认角色）", "result": "PASS",
          "detail": "空用户名、空密码、缺密码 → 400 username and password required；非法角色 → 400 invalid role；"
                    "role 省略 → 200 且落为 viewer（默认语义）。UI 侧“创建”按钮在用户名或密码为空时 disabled。",
          "evidence": ["evidence/P20-04-users-create-validation.json"]},
         {"dimension": "MAIN-USER-EMPTY-PASSWORD 复核（修复版是否拒绝空密码）", "result": "PASS",
          "detail": "① POST /api/users 空密码 → 400（未创建用户）；② 已建用户用空密码登录 → 401；"
                    "③ 遗留空 hash 行（在可销毁实例内直连 hub.db 注入 length(password_hash)=0 的用户）用空密码登录 → 401 invalid credentials，"
                    "任意密码亦 401。三条路径均拒绝空密码登录。",
          "evidence": ["evidence/P20-04-users-create-validation.json"]}],
        ["空 hash 行注入属 harness 造数（可销毁实例内直连 SQLite），非产品 API 路径；产品侧 API 已无法产生该形态。"],
        "real-ui-api-db",
        ["evidence/P20-04-users-create-validation.json"],
        {"verdict": p2004.get("verdict"), "edge_inputs": p2004.get("edge_inputs")}))

    cases.append(dims(
        "P14P15-12", "PASS", "high",
        [{"dimension": "五操作（deploy/start/stop/restart/upgrade）分角色权限 + 真实执行", "result": "PASS",
          "detail": "admin 5/5 → 200；operator 5/5 → 200（真实执行落到 owned child）；viewer 5/5 → 403。"
                    "执行数 2 → 12（本批新增 10 条真实 Execution）。",
          "evidence": ["evidence/P14P15-12-app-deploy-permissions.json"]},
         {"dimension": "应用定义修改权限（PUT/POST/DELETE/assign）", "result": "PASS",
          "detail": "admin 定义修改 200/200/200 与 assign 200（重复 assign 200，幂等）；operator/viewer 全部 403，"
                    "且拒绝后夹具应用名称/版本未被改动。",
          "evidence": ["evidence/P14P15-12-app-deploy-permissions.json"]},
         {"dimension": "应用详情页控件分角色（真实 UI）", "result": "PASS",
          "detail": "admin：保存按钮 1 + 操作按钮[部署]；operator：保存 0 + [部署]（可部署不可改定义）；viewer：保存 0、无操作按钮。",
          "evidence": ["evidence/P14P15-12-appdetail-admin.png", "evidence/P14P15-12-appdetail-operator.png",
                       "evidence/P14P15-12-appdetail-viewer.png"]}],
        ["夹具应用无 artifact（binary_path 指向不存在的二进制），故五操作在 Agent 侧的真实终态可能为失败/跳过；"
         "本用例维度为“部署与定义修改权限”，权限与真实执行分发均已验证；应用部署的业务成功终态在本批未作为断言。"],
        "real-ui-api-agent",
        ["evidence/P14P15-12-app-deploy-permissions.json"],
        {"ops": {r: {op: (p1415.get("api") or {}).get(r, {}).get("deploy_operations", {}).get(op, {}).get("http")
                     for op in ("deploy", "start", "stop", "restart", "upgrade")}
                 for r in ("admin", "operator", "viewer")},
         "exec_delta": (p1415.get("verdict") or {}).get("real_executions_created")}))

    cases.append(dims(
        "P19-03", "PASS", "high",
        [{"dimension": "六类动作逐类审计行（执行/取消/部署/升级/节点状态/角色密码）", "result": "PASS",
          "detail": ("execute：POST /api/tasks/{id}/run → 新增 (task,<id>,execute)；cancel：RUNNING 执行取消 → "
                     "(execution,<id>,cancel) 且 detail=任务名；deploy：(application,<id>,deploy)；upgrade："
                     "(node,<id>,upgrade)；节点状态：(node,<id>,update) ×2（maintenance→online）；"
                     "角色/密码：(user,<id>,update) ×2（改角色、改密码后原密码失效、新密码可登录）。"),
          "evidence": ["evidence/P19-03-audit-six-classes-verified.json",
                       "evidence/P19-03-audit-six-action-classes.json"]},
         {"dimension": "UI 审计页可检索到取消动作", "result": "PASS",
          "detail": "在 /audit 搜索该 execution id → 唯一行显示 用户名/cancel/execution/<id>/任务名（截图留档）。",
          "evidence": ["evidence/P1903-audit-search-cancel.png"]}],
        ["升级类审计为“下发升级”记录；其真实 Agent 侧自升级终态未作为本用例断言（属 P17P18/X15 维度）。"],
        "real-ui-api-agent",
        ["evidence/P19-03-audit-six-classes-verified.json"],
        {"classes": {k: v.get("new_rows") or v.get("new_rows_count") or v.get("row_exists")
                     for k, v in (p1903b.get("classes") or {}).items()},
         "audit_total_rows": (p1903b.get("classes") or {}).get("cancel_recheck", {}).get("audit_total_rows")}))

    cases.append(dims(
        "X13", "PASS", "medium-high",
        [{"dimension": "跨页缓存 / 两会话编辑", "result": "PASS",
          "detail": ("另一会话（admin）修改应用定义后：已打开页面 3 秒内不自动更新（无跨会话推送，open_page_shows_new=false）；"
                     "离开再返回该页时重新拉取并显示新值（after_nav_shows_new=true）。"),
          "evidence": ["evidence/X13-multi-session-role-logout-convergence.json"]},
         {"dimension": "旧会话改角色后的 API 权限与实际收敛时间", "result": "PASS",
          "detail": ("admin 改角色（API 路径）→ 旧 token /api/me 角色收敛 0.001s；受限写（PUT /api/applications）立即 403，"
                     "读仍 200，且被拒写未改动定义；admin 在 Users 页用真实 UI 改回 operator → 旧 token 恢复 operator。"),
          "evidence": ["evidence/X13-multi-session-role-logout-convergence.json"]},
         {"dimension": "另一标签退出登录后保护页面 + 实际收敛时间", "result": "PASS（行为已测，见 observation）",
          "detail": ("同上下文另一标签（t2）执行真实 UI 退出：localStorage token 被清除（跨标签共享）；但 t1 受保护页面在 15s "
                     "被动窗口内不跳转、仍可继续操作，其内存 token 直连 /api/me 仍返回 200（服务端会话未被撤销，因 UI 退出未调用 "
                     "/api/logout）；t1 自身刷新后立即收敛到 /sign-in（首次引导请求 401）。"),
          "evidence": ["evidence/X13-other-tab-logout-token-replacement.json"]},
         {"dimension": "另一标签替换 Token 后保护页面", "result": "PASS（行为已测）",
          "detail": ("另一标签写入伪造 token（服务端 401）；t1 页面被动窗口 15s 内不跳转、仍以内存旧会话正常工作；"
                     "t1 刷新后立即收敛 /sign-in。对照：显式 POST /api/logout 后旧 token 立即 401。"),
          "evidence": ["evidence/X13-other-tab-logout-token-replacement.json"]}],
        ["“收敛时间”在被动窗口内为“不收敛”（设计使然：SPA 无跨标签会话同步）；收敛发生在页面自身刷新时（≈首个引导请求，"
         "wall clock 约 2.3s 含页面加载）。是否需要“退出即撤销服务端会话”属产品口径，见 observation OBS-UI-LOGOUT-NO-SERVER-REVOKE。"],
        "real-ui-api",
        ["evidence/X13-summary.json", "evidence/X13-multi-session-role-logout-convergence.json",
         "evidence/X13-other-tab-logout-token-replacement.json"],
        {"verdict": x13.get("verdict")}))

    cases.append(dims(
        "X14", "PASS", "high",
        [{"dimension": "跨页勾选保持与批量请求 ID 不漂移", "result": "PASS",
          "detail": ("pageSize=10、18 节点 2 页：第 1 页勾选 burst01/burst02、第 2 页勾选 n05，返回第 1 页后两项仍保持勾选"
                     "（bulk 栏计数 3）；点击“设为维护”→ 确认框“将把 3 个节点设为维护状态。”→ 实际发出的 3 条 POST "
                     "ID 与所选 3 个节点 ID 完全一致（978c29ae…/2eff7ecd…/1ceb2963…）。"),
          "evidence": ["evidence/X14-bulk-payload.json", "evidence/X14-bulk-selection-drift.json"]},
         {"dimension": "筛选状态下的选择集合语义", "result": "PASS（行为已记录）",
          "detail": ("筛选命中 1 行时，bulk 栏计数收缩为 1（只统计当前可见的已选行）；清除筛选后计数恢复，"
                     "随后“恢复”动作发出的 2 条请求 ID 与页面勾选一致（无幽灵 ID）。"),
          "evidence": ["evidence/X14-bulk-selection-drift.json", "evidence/X14-bulk-payload.json"]},
         {"dimension": "末页末项删除、总数与 pageSize/刷新", "result": "PASS",
          "detail": ("末页行操作菜单[维护/禁用/设为在线/撤销凭证/删除]→删除末项：API 总数 18→17（连续两次 16→15），"
                     "被删节点不再出现在接口；pageSize 10→20 后单页 15/17 行且页指示“第 1 页，共 1 页”；"
                     "刷新后勾选清空（期望行为）。"),
          "evidence": ["evidence/X14-bulk-selection-drift.json"]},
         {"dimension": "Execution 跨页搜索与 Trigger（轻量核对）", "result": "PASS（引用既有 regression）",
          "detail": ("以执行 id 搜索 → 命中 1 行；?trigger=manual 与 ?status=SUCCESS 过滤均返回行且无错误。"
                     "跨页搜索维度已由 SAFE-02/GAP-07b 覆盖（WEB-EXEC-SEARCH-PAGE 已 regression PASS），本项仅做组合核对。"),
          "evidence": ["evidence/X14-bulk-selection-drift.json"]}],
        ["批量“升级”payload 未在本项重复抓取（升级已在 P19-03 做真实下发）；节点数据集为自建 pending + 1 个 owned child，"
         "未使用 Docker 形态节点。"],
        "real-ui-api-agent",
        ["evidence/X14-bulk-payload.json", "evidence/X14-bulk-selection-drift.json"],
        {"A_payload_matches": (x14b.get("verdict") or {}).get("A_payload_matches_selection"),
         "A_payload_ids": (x14b.get("verdict") or {}).get("A_payload_ids"),
         "totals": [x14.get("api_total_nodes"), x14.get("api_total_after_delete")]}))

    out = {
        "batch_id": "qa-resume-20261016-g1",
        "owner": "e2e (G1 隔离可销毁实例批)",
        "generated_at_utc": C.utc(),
        "authorization": {
            "checklist": ".e2e/runs/20260930-101945/repair/qa-resume-20261014-root/authorization-checklist-20261016.md",
            "group": "G1（隔离可销毁实例）+ G9 的 X14 并入",
            "cases_authorized": ["P04-06", "P14P15-12", "P21-08", "P20-04", "X13", "P03-09", "P19-03", "X14"],
        },
        "environment": {
            "instance": cr["instance_id"], "nonce": nonce, "port": cr["host_port"],
            "base_url": cr["base_url"], "tunnel_local_port": cr["tunnel_port"],
            "binary": C.REMOTE_BIN, "binary_sha256": C.REMOTE_BIN_SHA,
            "ui_parity_with_repair": "index.html sha256 相同（612c23ba…）",
            "owned_child_agent_binary": C.REMOTE_AGENT,
            "owned_child_agent_sha256": "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add",
            "note": "owned child 使用 repair 同步的 agent-static 构建；Hub 侧为本日修复版。",
            "browser": "agent-browser（自有 session：qa-g1-<nonce>-*，含 tab t1/t2 多标签）",
            "database": f"{cr['data_dir']}/hub.db（销毁时随 data_dir 一并删除）",
        },
        "data_created_on_isolated_instance": {
            "executions": len(execs),
            "audit_rows": len(audit_rows),
            "users": len(users),
            "nodes": len(nodes),
            "note": "全部对象仅存在于隔离实例 data_dir，随实例整体销毁；未在 repair :18080 / primary :8080 产生任何写入。",
        },
        "cases": cases,
    }
    (C.ROOT / "case-result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")

    progress = {
        "batch_id": "qa-resume-20261016-g1",
        "nonce": nonce,
        "phase": "CASES_EXECUTED_PENDING_TEARDOWN",
        "updated_at_utc": C.utc(),
        "authority": f"{cr['base_url']} (isolated disposable instance)",
        "steps": [
            {"name": "实例创建（transient，独立 data_dir/config/log/端口）", "status": "done", "result": "PASS"},
            {"name": "版本平行核对（二进制 SHA + index.html SHA == repair）", "status": "done", "result": "PASS"},
            {"name": "P03-09 真空空态（API+UI）", "status": "done", "result": "PASS"},
            {"name": "受限账号 operator/viewer + owned child Agent + 自有夹具", "status": "done", "result": "PASS"},
            {"name": "A 批：P04-06 / P21-08 / P20-04", "status": "done", "result": "PASS"},
            {"name": "B 批：P14P15-12 / P19-03 / X13", "status": "done", "result": "PASS"},
            {"name": "G9：X14 多页选择漂移与跨页保持", "status": "done", "result": "PASS"},
            {"name": "收尾：销毁实例 + 零残留核验 + 凭据清理", "status": "pending"},
        ],
        "case_status": {c["case_id"]: {"before": c["before_status"], "proposed_after": c["proposed_status"],
                                       "confidence": c["confidence"]} for c in cases},
        "findings_summary": [
            "DEF-ASSIGN-MISSING-APP-500（medium）",
            "OBS-UI-LOGOUT-NO-SERVER-REVOKE（low-medium）",
            "OBS-USER-DUP-SQL-MESSAGE（low）",
            "OBS-USER-INPUT-NOT-TRIMMED（low）",
            "OBS-ENROLL-TRANSIENT-500（low，未复现）",
            "OBS-LOCALSTORAGE-TOKEN-NOT-CROSS-TAB（info）",
            "OBS-CROSS-SESSION-NO-PUSH（info）",
        ],
        "outputs": ["case-result.json", "progress.json", "instance-ledger.json", "fixture-ledger.json",
                    "final-cleanup-oracle.json", "evidence/", "terminal/"],
    }
    (C.ROOT / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1) + "\n")

    # fixture-ledger 更新（销毁前快照）
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    fx["disposal"] = {
        "at_utc": C.utc(),
        "method": "whole-instance destroy（pkill -f qa-<nonce> → rm -rf data_dir/child root → rm -f config/log）",
        "objects": {
            "users": [{"username": u.get("username"), "role": u.get("role")} for u in users],
            "nodes": len(nodes), "executions": len(execs), "audit_rows": len(audit_rows),
        },
        "note": "产品无删除用户 API，故所有账号/对象均随实例销毁；repair 权威零持久对象。",
    }
    (C.ROOT / "fixture-ledger.json").write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"cases": {c["case_id"]: c["proposed_status"] for c in cases},
                      "executions": len(execs), "audit_rows": len(audit_rows),
                      "users": len(users), "nodes": len(nodes)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
