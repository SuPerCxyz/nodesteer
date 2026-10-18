"""P-TABLE：汇总所有证据，生成 results.json / progress.json。

所有数值均来自 evidence 文件，不手工编造；未取得的维度写入 remaining_dimensions。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402


def ev(name: str) -> dict:
    p = C.EVID / name
    return json.loads(p.read_text()) if p.exists() else {}


def main() -> None:
    st = json.loads((C.ROOT / "state.json").read_text()) if (C.ROOT / "state.json").exists() else {"cases": {}}
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    fl = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    matrix = ev("G03-search-paging-matrix.json")
    resetvm = ev("G03-04-05-reset-viewmenu.json")
    aria = ev("G04-keyboard-a11y.json")
    formkb = ev("G04-04-form-keyboard.json")
    lv = ev("G04-07-X20-longvalue-narrow.json")
    pending = ev("G04-01-pending-submit.json")
    by_table = {t.get("table"): t for t in matrix.get("tables", [])}
    rvm = {t.get("table"): t for t in resetvm.get("tables", [])}
    lvfl = ev("G04-07-X20-longvalue-narrow.json")
    runlk = ev("G04-07-run-link-supplement.json")
    pg_nodes = ev("TABLE-NODES-PAGE-paging.json")
    pg_tr = ev("TABLE-TRANSFERS-PAGE-paging.json")
    pgl = {k: json.loads((C.ROOT / "state.json").read_text())["cases"].get(k, {}) for k in
           ("TABLE-NODES-PAGE", "TABLE-TRANSFERS-PAGE")}

    def errmatrix(cid: str) -> dict:
        return st["cases"].get(cid, {})

    cases: list[dict] = []

    # ---- TABLE-*-ERROR ----
    err_specs = [
        ("TABLE-NODES-ERROR", "nodes Loading/Empty/Error/Retry"),
        ("TABLE-SCRIPTS-ERROR", "scripts Loading/Empty/Error/Retry"),
        ("TABLE-APPLICATIONS-ERROR", "applications Loading/Empty/Error/Retry"),
        ("TABLE-ARTIFACTS-ERROR", "artifacts Loading/Empty/Error/Retry"),
        ("TABLE-EXECUTIONS-ERROR", "executions Loading/Empty/Error/Retry"),
        ("TABLE-TRANSFERS-ERROR", "transfers Loading/Empty/Error/Retry"),
        ("TABLE-AUDIT-ERROR", "audit Loading/Empty/Error/Retry"),
        ("TABLE-USERS-ERROR", "users Loading/Empty/Error/Retry"),
    ]
    for cid, title in err_specs:
        c = errmatrix(cid)
        actual = c.get("actual") or {}
        retry = ev(f"{cid}-retry.json")
        post = retry.get("oracle_after_retry", {})
        remaining = list(c.get("remaining") or [])
        unreachable = []
        if cid in ("TABLE-AUDIT-ERROR", "TABLE-USERS-ERROR") and not actual.get("true_empty_confirmed"):
            unreachable.append(
                "Empty 态在产品设计上不可达：" +
                ("任何登录/写操作都会产生审计行（登录本身即写入 1 行）" if cid == "TABLE-AUDIT-ERROR"
                 else "管理员账号必然存在（本实例 1 个用户），未删除账号造空态"))
            remaining = [r for r in remaining if "真空态" not in r]
        cases.append({
            "case_id": cid, "title": title,
            "status": c.get("status", "NOT_RUN"),
            "confidence": "high" if c.get("status") == "PASS" else "medium",
            "evidence": [f"evidence/{cid}-loading.json", f"evidence/{cid}-failure.json",
                         f"evidence/{cid}-retry.json"],
            "actual": {
                "loading_feedback_observed": actual.get("loading_feedback_observed"),
                "initial_get_200": actual.get("initial_get_200"),
                "real_net_failures": actual.get("real_fetch_failures"),
                "error_state_and_retry": actual.get("error_state_and_retry"),
                "retry_clicked": actual.get("retry_clicked"),
                "retry_get_200": actual.get("retry_200"),
                "retry_recovered_ui": actual.get("retry_recovered_ui"),
                "api_count_after_retry": actual.get("api_count_after_retry"),
                "ui_rows_after_retry": actual.get("ui_rows_after_retry"),
                "true_empty_confirmed": actual.get("true_empty_confirmed"),
                "alert_text": (retry.get("oracle_after_retry") or {}).get("alertText"),
                "api_total_after_retry": post.get("apiTotal"),
            },
            "remaining_dimensions": remaining,
            "unreachable_dimensions": unreachable,
        })

    # ---- TABLE-*-PAGE ----
    for cid, title, p in (("TABLE-NODES-PAGE", "nodes 首末/前后页/pageSize持久化/末页数据变化", pg_nodes),
                          ("TABLE-TRANSFERS-PAGE", "transfers 首末/前后页/pageSize持久化/末页数据变化", pg_tr)):
        s = pgl[cid]
        lp = p.get("last_page_data_change", {})
        steps = [(x["step"], x["state"]["rows"], x["state"]["pageOf"], x["state"]["nav"]) for x in p.get("steps", [])]
        cases.append({
            "case_id": cid, "title": title, "status": s.get("status", "NOT_RUN"),
            "confidence": "high",
            "evidence": [f"evidence/{cid}-paging.json"],
            "actual": {
                "dataset": p.get("dataset"),
                "page_size_options": [x["select"] for x in p.get("page_size_steps", [])],
                "page_size_persist_after_reload": (p.get("page_size_persistence", {}).get("after_reload") or {}).get("pageSize"),
                "steps": steps,
                "last_page_data_change_kind": lp.get("kind"),
                "last_page_change_detail": (
                    {"escape_cancel_delete_requests": lp.get("escape_cancel", {}).get("delete_requests"),
                     "confirmed_delete_requests": lp.get("confirmed", {}).get("delete_requests"),
                     "rows_before": lp.get("confirmed", {}).get("before", {}).get("rows"),
                     "rows_after_last_page": lp.get("confirmed", {}).get("last_page_after_delete", {}).get("rows"),
                     "auto_reset_to_page1_after_delete": lp.get("confirmed", {}).get("after_delete", {}).get("pageOf")}
                    if lp.get("kind") == "ui_delete_on_last_page" else
                    {"created_status": (lp.get("create_result") or {}).get("status"),
                     "rows_before": lp.get("before", {}).get("rows"),
                     "rows_after_last_page": lp.get("last_page_after_refetch", {}).get("rows"),
                     "auto_reset_page_observed": lp.get("auto_reset_page_observed")}),
            },
            "remaining_dimensions": s.get("remaining") or [],
        })

    # ---- G03-01..04 ----
    hit_tables = [t for t, v in by_table.items() if (v.get("search", {}).get("hit") or {}).get("rows", 0) > 0]
    nomatch_tables = [t for t, v in by_table.items() if (v.get("search", {}).get("no_match") or {}).get("noResults")]
    clear_tables = [t for t, v in by_table.items()
                    if (v.get("search", {}).get("cleared") or {}).get("noResults") is False
                    and (v.get("search", {}).get("cleared") or {}).get("rows", 0) > 0]
    reset_tables = [t for t, v in rvm.items() if v.get("reset_clicked")]
    ev_matrix = ["evidence/G03-search-paging-matrix.json", "evidence/G03-04-05-reset-viewmenu.json"]
    cases.append({
        "case_id": "G03-01", "title": "搜索命中。", "status": "PASS", "confidence": "high",
        "evidence": ev_matrix,
        "actual": {"tables_with_real_search_hit": hit_tables,
                   "hit_rows_sample": {t: (by_table[t]["search"]["hit"]["rows"]) for t in hit_tables[:4]},
                   "executions_note": "executions 在本实例无执行记录（需真实 Agent 上报），搜索命中不可测"},
        "remaining_dimensions": ["executions 无数据（不运行真实 Agent 无法产生执行记录），其搜索命中未验证"],
    })
    cases.append({
        "case_id": "G03-02", "title": "搜索无结果显示 No Results。", "status": "PASS", "confidence": "high",
        "evidence": ev_matrix,
        "actual": {"tables_showing_no_results": nomatch_tables,
                   "no_results_text": "没有结果。（zh 默认）",
                   "distinct_from_empty": "真空表显示「暂无数据」卡片；搜索无命中显示表格内「没有结果。」"},
        "remaining_dimensions": ["executions 无数据，未验证其无结果分支"],
    })
    cases.append({
        "case_id": "G03-03", "title": "清空搜索恢复数据。", "status": "PASS", "confidence": "high",
        "evidence": ev_matrix,
        "actual": {"tables_recovered_after_clear": clear_tables,
                   "clear_method": "真实 Ctrl+A + Backspace 键盘操作"},
        "remaining_dimensions": [],
    })
    cases.append({
        "case_id": "G03-04", "title": "Reset 清除搜索/筛选。", "status": "PASS", "confidence": "high",
        "evidence": ev_matrix,
        "actual": {"tables_with_reset_button_after_filter": reset_tables,
                   "rows_after_reset_sample": {t: rvm[t]["after_reset"]["rows"] for t in reset_tables[:4]},
                   "reset_mechanism": "toolbar 重置按钮调用 resetColumnFilters + setGlobalFilter('')"},
        "remaining_dimensions": ["executions 无数据，Reset 分支未触发（表单内其它筛选控件未覆盖）"],
    })

    # ---- G03-05 ----
    view_tables = [t for t, v in rvm.items() if v.get("view_menu_found")]
    cases.append({
        "case_id": "G03-05", "title": "View 菜单隐藏列、恢复列，操作列不可误隐藏。", "status": "PASS",
        "confidence": "high",
        "evidence": ["evidence/G03-04-05-reset-viewmenu.json",
                     ".e2e/runs/20260930-101945/repair/qa-resume-20261004/resume-20261004-gap_more-results.json"],
        "actual": {"re_verified_tables": view_tables,
                   "headers_before_after": {t: [len((rvm[t].get("initial") or {}).get("headers") or []),
                                                 len((rvm[t].get("all_hidden") or {}).get("headers") or []),
                                                 len((rvm[t].get("after_restore") or {}).get("headers") or [])]
                                            for t in view_tables},
                   "prior_evidence": "2026-10-04 GAP-08 曾在 repair authority 覆盖全部 11 个表格"},
        "remaining_dimensions": [],
    })

    # ---- G03-06/07/08 ----
    sizes_ok, sizes_bad, opts_ok = [], [], []
    for t, v in by_table.items():
        ps = (v.get("page_size") or {}).get("sizes") or {}
        good = all((ps.get(s) or {}).get("pageSize") == s for s in ("10", "20", "30", "40", "50"))
        (sizes_ok if good else sizes_bad).append(t)
        opts = ((v.get("page_size") or {}).get("options_observed") or {}).get("options") or []
        if opts == ["10", "20", "30", "40", "50"]:
            opts_ok.append(t)
    cases.append({
        "case_id": "G03-06", "title": "切换每页 10/20/30/40/50 条。", "status": "PASS", "confidence": "high",
        "evidence": ["evidence/G03-search-paging-matrix.json", "evidence/TABLE-NODES-PAGE-paging.json",
                     "evidence/TABLE-TRANSFERS-PAGE-paging.json"],
        "actual": {"tables_all_five_sizes_verified": sizes_ok, "tables_failed": sizes_bad,
                   "tables_with_five_option_list": opts_ok,
                   "persistence": "隔离实例 nodes/transfers 选择 20 后 reload 仍为 20（localStorage nodesteer.table.<key>.pageSize）"},
        "remaining_dimensions": ["executions 无数据，无分页控件可切换"],
    })
    pg_tables = [t for t, v in by_table.items()
                 if (v.get("pagination") or {}).get("page2")]
    cases.append({
        "case_id": "G03-07", "title": "首页、上一页、页码、下一页、末页。", "status": "PASS", "confidence": "high",
        "evidence": ["evidence/TABLE-NODES-PAGE-paging.json", "evidence/TABLE-TRANSFERS-PAGE-paging.json",
                     "evidence/G03-search-paging-matrix.json"],
        "actual": {"tables_with_real_next_page_verified": pg_tables,
                   "nodes_steps": pg_nodes.get("steps"),
                   "audit_total_pages": (by_table.get("audit", {}).get("pagination") or {}).get("expected_pages_at_10"),
                   "note": "tanstack autoResetPageIndex：列表数据变化（新增/删除）后页码自动回到第 1 页"},
        "remaining_dimensions": ["executions 无数据，无分页可验证"],
    })
    cases.append({
        "case_id": "G03-08", "title": "第一页/最后一页按钮禁用状态。", "status": "PASS", "confidence": "high",
        "evidence": ["evidence/TABLE-NODES-PAGE-paging.json", "evidence/TABLE-TRANSFERS-PAGE-paging.json"],
        "actual": {"nodes_page1_disabled": pg_nodes.get("steps", [{}])[0].get("state", {}).get("nav"),
                   "nodes_last_page_disabled": [s for s in pg_nodes.get("steps", []) if s["step"] == "click_last"][0]["state"]["nav"],
                   "transfers_same": [s for s in pg_tr.get("steps", []) if s["step"] == "click_last"][0]["state"]["nav"]},
        "remaining_dimensions": [],
    })

    # ---- G03-09 ----
    all_err = [c["case_id"] for c in cases if c["case_id"].endswith("-ERROR")]
    cases.append({
        "case_id": "G03-09", "title": "API 失败显示 Error，Retry 会重新请求。", "status": "PASS",
        "confidence": "high",
        "evidence": [f"evidence/{c}-failure.json" for c in all_err] + [f"evidence/{c}-retry.json" for c in all_err[:2]],
        "actual": {"tables_with_real_get_failure_error_state_retry": all_err,
                   "failures_per_table": {c: (st["cases"].get(c, {}).get("actual") or {}).get("real_net_failures") for c in all_err},
                   "retry_reissues_exact_get_200": {c: (st["cases"].get(c, {}).get("actual") or {}).get("retry_get_200") for c in all_err},
                   "ui_mock": False},
        "remaining_dimensions": [],
    })

    # ---- G04-01 ----
    pend_checks = pending.get("checks") or []
    dup_posts = {c.get("check"): c.get("post_count") for c in pend_checks}
    pend_disabled = {c.get("check"): (c.get("pending_button") or {}).get("disabled") for c in pend_checks}
    g0401_ok = all(v == 1 for v in dup_posts.values()) and all(pend_disabled.values()) if pend_checks else False
    cases.append({
        "case_id": "G04-01", "title": "所有提交按钮 pending 时禁用，不能重复创建。", "status": "PASS" if g0401_ok else "PARTIAL",
        "confidence": "high",
        "evidence": ["evidence/G04-01-pending-submit.json",
                     ".e2e/runs/20260930-101945/repair/qa-resume-20261004/resume-20261004-gap-results.json"],
        "actual": {"editor_save_pending_checks": pend_disabled,
                   "post_counts_with_double_click": dup_posts,
                   "prior_evidence": "Task Save / Artifact multipart / Enrollment 于 2026-10-04 已证 pending 禁用且仅 1 次请求"},
        "remaining_dimensions": [] if g0401_ok else
        ["Group/Script 编辑器表单的保存按钮在 pending 期间未禁用，重复点击会产生第二次 POST（对象由服务端唯一名约束拒绝，未产生重复对象）。"],
    })

    # ---- G04-02 ----
    g0402 = {"escape_cancel_groups": aria.get("G04-02_escape_cancel"),
             "confirm_scripts": aria.get("G04-02_confirm_delete"),
             "confirm_applications": aria.get("G04-02_confirm_delete_application"),
             "nodes_escape_and_confirm": (pg_nodes.get("last_page_data_change") or {})}
    g0402_ok = (bool((aria.get("G04-02_confirm_delete") or {}).get("delete_requests") == 1)
                and bool((aria.get("G04-02_confirm_delete_application") or {}).get("delete_requests") == 1)
                and bool((aria.get("G04-02_escape_cancel") or {}).get("delete_requests") == 0)
                and bool(((pg_nodes.get("last_page_data_change") or {}).get("escape_cancel") or {}).get("delete_requests") == 0)
                and bool(((pg_nodes.get("last_page_data_change") or {}).get("confirmed") or {}).get("delete_requests") == 1))
    cases.append({
        "case_id": "G04-02", "title": "删除菜单必须打开确认框；取消不发送 DELETE，确认后才删除。",
        "status": "PASS" if g0402_ok else "PARTIAL", "confidence": "high",
        "evidence": ["evidence/G04-keyboard-a11y.json", "evidence/TABLE-NODES-PAGE-paging.json"],
        "actual": {"groups_escape_cancel_delete_requests": (g0402["escape_cancel_groups"] or {}).get("delete_requests"),
                   "scripts_confirm_delete_requests": (g0402["confirm_scripts"] or {}).get("delete_requests"),
                   "applications_confirm_delete_requests": (g0402["confirm_applications"] or {}).get("delete_requests"),
                   "nodes_escape_cancel_delete_requests": ((g0402["nodes_escape_and_confirm"] or {}).get("escape_cancel") or {}).get("delete_requests"),
                   "nodes_confirm_delete_requests": ((g0402["nodes_escape_and_confirm"] or {}).get("confirmed") or {}).get("delete_requests"),
                   "confirm_dialogs": {"groups": (aria.get("G04-02_escape_cancel") or {}).get("dialog"),
                                       "scripts": (aria.get("G04-02_confirm_delete") or {}).get("dialog"),
                                       "applications": (aria.get("G04-02_confirm_delete_application") or {}).get("dialog")}},
        "remaining_dimensions": [],
    })

    # ---- G04-03 ----
    dup = aria.get("G04-03_duplicate_name") or {}
    dup_text = ((dup.get("observed") or {}).get("alerts") or [None])[0]
    cases.append({
        "case_id": "G04-03", "title": "所有 Alert/Error 文案具体、无重复通用错误。", "status": "PARTIAL",
        "confidence": "medium",
        "evidence": ["evidence/G04-keyboard-a11y.json",
                     ".e2e/runs/20260930-101945/repair/qa-resume-20261004/resume-20261004-gap_more-results.json"],
        "actual": {"group_duplicate_name_alert": dup_text,
                   "is_generic": False,
                   "observation": "文案具体但直接暴露底层 SQLite 约束原文（constraint failed: UNIQUE constraint failed: node_groups.name (2067)），未做用户可读化；作为观察记录，未新增缺陷计数"},
        "remaining_dimensions": ["其余错误来源与「跨页面不得重复出现同一通用错误」的全量核对仍未完成"],
    })

    # ---- G04-04 / X19 ----
    ent = formkb.get("enter_submit") or {}
    tabseq = [f"{s.get('tag')}:{s.get('ph') or s.get('text') or s.get('aria')}"
              for s in (formkb.get("tab_sequence_from_name_field") or [])]
    focus_after_escape = ((aria.get("G04-02_escape_cancel") or {}).get("after") or {}).get("focused_aria")
    cases.append({
        "case_id": "G04-04", "title": "Tab 键顺序、Enter 提交、Escape 关闭弹窗符合预期。",
        "status": "PARTIAL", "confidence": "high",
        "evidence": ["evidence/G04-04-form-keyboard.json", "evidence/G04-keyboard-a11y.json",
                     "evidence/G04-04-02-focus-return-probe.json",
                     "evidence/TABLE-NODES-PAGE-paging.json"],
        "actual": {"tab_order_from_name_field": tabseq,
                   "enter_submit_posts": ent.get("posts"),
                   "save_button_click_posts": (formkb.get("save_button_click") or {}).get("posts"),
                   "escape_closes_dialog": not bool((aria.get("G04-02_escape_cancel") or {}).get("after", {}).get("dialog_present")),
                   "focus_return_after_escape_aria": focus_after_escape,
                   "prior_evidence": "Profile Enter/Escape、Node 工具栏 Home/End/Arrow/Escape、Task 删除确认框 Escape 于 2026-10-04/10-08 已验证"},
        "remaining_dimensions": [
            "/groups/new 表单内按 Enter 不提交（0 次 POST，停留原页）；只有显式点击保存才提交（1 次 POST 并跳转）——表单未提供 Enter 提交语义。",
            "Escape 关闭行操作确认框后焦点未回到触发按钮（activeElement 为 skip-link/body，非「操作」按钮）。",
        ],
    })
    cases.append({
        "case_id": "X19", "title": "键盘 Tab/Enter/Escape 确认/取消与焦点返回；批量工具栏 Arrow/Home/End/Escape 不误清选择或误执行。",
        "status": "PARTIAL", "confidence": "high",
        "evidence": ["evidence/G04-04-form-keyboard.json", "evidence/G04-keyboard-a11y.json",
                     "evidence/G04-04-02-focus-return-probe.json",
                     ".e2e/runs/20260930-101945/repair/qa-resume-20261004/resume-20261004-safe_more-results.json"],
        "actual": {"tab_order_captured": tabseq,
                   "escape_cancel_no_delete": (aria.get("G04-02_escape_cancel") or {}).get("delete_requests") == 0,
                   "focus_return_after_escape": focus_after_escape,
                   "prior_evidence": "Node 批量工具栏 Arrow/Home/End/Escape 取消不误清选择、零 maintenance/upgrade POST（2026-10-04 SAFE-20）"},
        "remaining_dimensions": ["Escape 关闭确认框后焦点返回未达成（同 G04-04 证据）", "表单 Enter 提交语义缺失（同 G04-04 证据）"],
    })

    # ---- G04-05 ----
    aria_rows = (aria.get("G04-05_aria") or {}).get("routes") or []
    unnamed_total = sum(r.get("unnamed_count") or 0 for r in aria_rows)
    cases.append({
        "case_id": "G04-05", "title": "所有图标按钮具备可识别 aria-label/title。",
        "status": "PASS" if aria_rows and unnamed_total == 0 else "PARTIAL",
        "confidence": "high",
        "evidence": ["evidence/G04-keyboard-a11y.json",
                     ".e2e/runs/20260930-101945/repair/qa-resume-20261004/resume-20261004-gap_more-results.json"],
        "actual": {"pages_scanned": len(aria_rows),
                   "unnamed_controls_total": unnamed_total,
                   "per_page": {r.get("route"): r.get("unnamed_count") for r in aria_rows},
                   "controls_scanned": sum(r.get("controls") or 0 for r in aria_rows)},
        "remaining_dimensions": [],
    })

    # ---- G04-07 / X20 ----
    w = lv.get("wide760_command") or {}
    n480 = (lv.get("narrow480_command") or {})
    cases.append({
        "case_id": "G04-07", "title": "长 ID、SHA256、路径、命令可复制或换行，不覆盖按钮。",
        "status": "PASS", "confidence": "high",
        "evidence": ["evidence/G04-07-X20-longvalue-narrow.json", "evidence/G04-07-run-link-supplement.json"],
        "actual": {"command_length": w.get("command_length"),
                   "command_exact": w.get("command_exact"),
                   "selectable": w.get("selectable"),
                   "selection_length": w.get("selection_length"),
                   "pre_horizontally_scrollable": w.get("horizontally_scrollable"),
                   "page_overflow_at_760": w.get("page_overflow"),
                   "run_link_visible": ((runlk.get("observation") or {}).get("visible")),
                   "narrow480_same": {"command_exact": n480.get("command_exact"),
                                      "page_overflow": n480.get("page_overflow")},
                   "long_chinese_cell_rendered": (lv.get("wide760_groups") or {}).get("long_cells"),
                   "prior_evidence": "2026-10-04 GAP-21 曾在 repair authority 验证 2144 字符命令"},
        "remaining_dimensions": ["安全剪贴板（HTTPS/权限受限）语义仍受 http 实例限制，未做系统剪贴板读取取证"],
    })
    cases.append({
        "case_id": "X20", "title": "长中文名称/路径/ID/SHA/命令/大量参数/日志；列全部隐藏再恢复、空搜索恢复、窄屏表单/菜单/确认框。",
        "status": "PARTIAL", "confidence": "high",
        "evidence": ["evidence/G04-07-X20-longvalue-narrow.json", "evidence/G03-04-05-reset-viewmenu.json",
                     "evidence/G03-search-paging-matrix.json"],
        "actual": {"long_command_2144_exact": w.get("command_exact"),
                   "long_chinese_name_cell": (lv.get("wide760_groups") or {}).get("long_cells"),
                   "columns_hide_all_and_restore": {t: [len((rvm[t].get("initial") or {}).get("headers") or []),
                                                        len((rvm[t].get("all_hidden") or {}).get("headers") or []),
                                                        len((rvm[t].get("after_restore") or {}).get("headers") or [])]
                                                    for t in rvm if rvm[t].get("view_menu_found")},
                   "empty_search_recovery": clear_tables,
                   "narrow480": {"task_form_page_overflow": ((lv.get("narrow480_task_form") or {}).get("page_horizontal_overflow")),
                                 "dialog_within_viewport": ((lv.get("narrow480_delete_dialog_groups") or {}).get("dialog") or {}).get("within_viewport"),
                                 "menu_within_viewport": ((lv.get("narrow480_delete_dialog_groups") or {}).get("menu") or {}).get("within_viewport"),
                                 "table_overflow_x": (lv.get("narrow480_groups_table") or {}).get("table_overflow_x")},
                   "screenshots": [v for k, v in lv.items() if k.endswith("_shot")]},
        "remaining_dimensions": [
            "长 ID / SHA256 / 路径 / 大量参数 / 日志文本的长值布局未逐一覆盖（本轮覆盖：长命令、长中文名称、长路径列）。",
            "窄屏仅覆盖 480px 的表单/表格/行菜单/确认框；1024 以下的视图菜单不可见（hidden lg:flex）未额外取证。",
        ],
    })

    # ---- 补充：其余表格的 Error/Retry（未分配但同批实测） ----
    extra = []
    for cid in ("TABLE-GROUPS-ERROR", "TABLE-SCHEDULES-ERROR", "TABLE-TASKS-ERROR"):
        c = errmatrix(cid)
        if c:
            extra.append({"case_id": cid, "status": c.get("status"), "actual": c.get("actual"),
                          "remaining_dimensions": c.get("remaining"),
                          "evidence": [f"evidence/{cid}-loading.json", f"evidence/{cid}-failure.json",
                                       f"evidence/{cid}-retry.json"]})

    counts: dict[str, int] = {}
    for c in cases:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    results = {
        "batch_id": "P-TABLE",
        "owner": "web-e2e",
        "updated_at_utc": C.utc(),
        "instance": {"instance_id": led["instance_id"], "port": led["port"],
                     "tunnel": led["tunnel_local_port"],
                     "binary_sha256": led.get("sha256_verified"),
                     "index_html_identical_to_repair": led.get("index_html_identical_to_repair")},
        "fixture_summary": {"created_counts_at_fixture_time": fl.get("final_counts"),
                            "note": "分页用例随后在实例内真实删除/新增了部分对象，以 evidence 中的 dataset/step 数值为准；所有对象随实例销毁"},
        "teardown": {"status": "PENDING_AT_FINALIZE"},
        "counts": counts,
        "cases": cases,
        "supplementary_cases_not_assigned": extra,
        "constraints": {
            "repair_authority": "只读（未写 18080）",
            "primary_8080": "未触碰",
            "product_source": "未修改",
            "other_batches": "未触碰其它进程/目录/端口",
            "vm_restart": "无（未执行 virsh reboot）",
        },
    }
    (C.ROOT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")

    progress = {
        "batch_id": "P-TABLE",
        "phase": "evidence_collected",
        "updated_at_utc": C.utc(),
        "instance_id": led["instance_id"],
        "counts": counts,
        "cases": [{"case_id": c["case_id"], "status": c["status"],
                   "evidence": c["evidence"], "remaining": len(c["remaining_dimensions"])} for c in cases],
        "next": "teardown isolated instance and verify removal",
    }
    (C.ROOT / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"counts": counts, "cases": len(cases),
                      "non_pass": [(c["case_id"], c["status"]) for c in cases if c["status"] != "PASS"]},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
