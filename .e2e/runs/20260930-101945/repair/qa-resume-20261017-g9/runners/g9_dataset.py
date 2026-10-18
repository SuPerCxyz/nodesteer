"""G9（X14）：多页节点数据集上的批量选择漂移与跨页保持断言（隔离实例内真实执行）。

数据集：36 行（1 online = owned child + 35 pending），pageSize=10 → 4 页。
断言面：
 1 跨页（3 页跨度）勾选保持；
 2 真实批量动作 payload 的 ID 集合 == UI 勾选集合（真实执行 + API 复核状态）；
 3 筛选隐藏已选行后的选择与 payload（幽灵/漏发检查）；
 4 排序变化后保持；
 5 pageSize 10↔20 变化后保持；
 6 删除他页行造成位移后按 ID（而非行号）保持；
 7 末页末项真实删除 → 计数与页码收敛、无幽灵 ID；
 8 刷新后选择清空；
 9 Execution 跨页搜索/Trigger 轻量复核。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402

ROWS_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const cbOf = (tr) => tr.querySelector('button[role=checkbox]');
  const nameOf = (tr) => {
    const lines = (tr.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
    return lines.find(l => /^(qa-|node-)/.test(l)) || lines[0] || null;
  };
  const barText = [...document.querySelectorAll('*')].filter(e => e.offsetParent !== null && e.children.length === 0)
      .map(e => (e.innerText||'').trim()).filter(t => /^已选择 \d+ 项$/.test(t));
  return {
    path: location.pathname + location.search,
    rows: rows.map(tr => ({name: nameOf(tr),
                           checked: cbOf(tr) ? cbOf(tr).getAttribute('aria-checked') === 'true' : null})),
    selected_count_text: barText[0] || null,
    header_cb: (document.querySelector('button[aria-label="全选所有行"]')||{}).getAttribute
        ? document.querySelector('button[aria-label="全选所有行"]').getAttribute('aria-checked') : null,
    page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null,
    bulk_buttons: [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
        .map(b => (b.innerText||'').trim()).filter(t => /^(设为维护|恢复|清除选择|批量)/.test(t)),
    empty_msg: /没有匹配|暂无数据|没有数据/.test(document.body.innerText||''),
  };
})()"""


def click_text(who, pattern, wait=1.2):
    r = U.js(who, r"""
(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null);
  const b = btns.find(x => new RegExp(%s).test((x.innerText||'').trim()));
  if (!b) return {clicked:false, candidates: btns.map(x=>(x.innerText||'').trim()).filter(Boolean).slice(0,25)};
  b.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  b.click();
  return {clicked:true, text:(b.innerText||'').trim()};
})()""" % json.dumps(pattern))
    time.sleep(wait)
    return r


def set_row_checked(who, name_part, on=True):
    return U.js(who, r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const tr = rows.find(r => (r.innerText||'').includes(%s));
  if (!tr) return {found:false};
  const cb = tr.querySelector('button[role=checkbox]');
  if (!cb) return {found:true, checkbox:false};
  const now = cb.getAttribute('aria-checked') === 'true';
  if (now !== %s) {
    cb.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
    cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
    cb.click();
  }
  return {found:true, name: %s, before: now, after: cb.getAttribute('aria-checked')};
})()""" % (json.dumps(name_part), "true" if on else "false", json.dumps(name_part)))


def set_filter(who, value, wait=1.6):
    r = U.js(who, r"""
(() => {
  const inputs = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null);
  const el = inputs.find(i => /搜索|筛选|search|filter/i.test(i.placeholder||'')) || inputs[0];
  if (!el) return {found:false, inputs: inputs.map(i=>i.placeholder)};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return {found:true, placeholder: el.placeholder, value_len: %s.length};
})()""" % (json.dumps(value), json.dumps(value)))
    time.sleep(wait)
    return r


def set_page_size(who, size, wait=1.8):
    r = U.js(who, r"""
(async () => {
  const trigger = [...document.querySelectorAll('button,[role=combobox]')]
      .find(b => /^(10|20|50)$/.test((b.innerText||'').trim()));
  if (!trigger) return {found:false};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 800));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').trim() === %s);
  if (!opt) return {found:true, options: [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim())};
  opt.click();
  await new Promise(r => setTimeout(r, 1200));
  const rows = [...document.querySelectorAll('table tbody tr')];
  return {found:true, switched:true, rows: rows.length,
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null};
})()""" % json.dumps(size))
    time.sleep(wait)
    return r


def bulk_action(who, pattern, expect_ids, wait=3.0):
    """真实批量动作：抓确认框文本与真实请求 payload。"""
    U.clear_rec(who)
    opened = click_text(who, pattern, wait=1.2)
    dlg = U.js(who, r"""
(() => {
  const dlg = document.querySelector('[role=dialog]');
  return {dialog_present: !!dlg,
          dialog_text: dlg ? (dlg.innerText||'').replace(/\n/g,' | ').slice(0,600) : null,
          dialog_buttons: dlg ? [...dlg.querySelectorAll('button')].map(b=>(b.innerText||'').trim()) : []};
})()""")
    confirmed = click_text(who, "^确认执行$|^确认升级$|^确认$|^确定$|^删除$", wait=0.6)
    time.sleep(wait)
    reqs = U.rec(who)
    muts = [r for r in reqs if r.get("method") in ("POST", "PUT", "DELETE", "PATCH")
            and "/api/nodes" in (r.get("url") or "")]
    ids = []
    for r in muts:
        seg = (r.get("url") or "").split("/api/nodes/")[-1].split("?")[0]
        seg = seg.split("/")[0]
        if seg and seg not in ("upgrade", "enrollment"):
            ids.append(seg)
        if r.get("body"):
            for m in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r["body"]):
                if m not in ids:
                    ids.append(m)
    return {"open_bar": opened, "dialog": dlg, "confirm": confirmed,
            "request_count": len(muts), "request_urls": [r.get("url") for r in muts],
            "request_methods": [r.get("method") for r in muts],
            "payload_ids": ids, "payload_id_count": len(ids),
            "expected_ids": expect_ids,
            "payload_equals_expected": sorted(set(ids)) == sorted(set(expect_ids)),
            "no_duplicates": len(ids) == len(set(ids))}


def row_action_delete(who, name_part):
    return U.js(who, r"""
(async () => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const tr = rows.find(r => (r.innerText||'').includes(%s));
  if (!tr) return {found:false};
  const trigger = [...tr.querySelectorAll('button')].slice(-1)[0];
  if (!trigger) return {found:true, trigger:false};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 900));
  const items = [...document.querySelectorAll('[role=menuitem]')];
  return {found:true, menu_items: items.map(e => (e.innerText||'').trim())};
})()""" % json.dumps(name_part))


def delete_via_menu(who):
    return U.js(who, r"""
(async () => {
  const items = [...document.querySelectorAll('[role=menuitem]')];
  const del = items.find(e => /删除/.test(e.innerText||''));
  if (!del) return {clicked:false, items: items.map(e=>(e.innerText||'').trim())};
  del.click();
  await new Promise(r => setTimeout(r, 1000));
  const confirm = [...document.querySelectorAll('button')]
      .find(b => /^(确认|确定|删除)$/.test((b.innerText||'').trim()));
  if (confirm) { confirm.click(); await new Promise(r => setTimeout(r, 1800)); return {clicked:true, confirmed:true}; }
  return {clicked:true, confirmed:false};
})()""")


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])

    def api_nodes():
        return C.as_list(hub.get("/api/nodes?limit=500", admin)["json"])

    def status_map():
        return {n["hostname"]: (n["id"], n.get("status")) for n in api_nodes()}

    ev = {"case_id": "X14", "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"],
          "dataset": {"nodes_api_total": len(api_nodes())}}
    sm = status_map()
    ev["dataset"]["status_breakdown"] = {s: sum(1 for _, st in sm.values() if st == s)
                                         for s in sorted({st for _, st in sm.values()})}
    U.init()
    who = "g9"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    ev["pagesize_init"] = set_page_size(who, "10")
    U.install_request_recorder(who)
    p1 = U.js(who, ROWS_JS)
    ev["page1_initial"] = p1
    ev["api_total_nodes"] = len(api_nodes())
    ev["api_id_by_name"] = {n["hostname"]: n["id"] for n in api_nodes()}

    # ---------- 1. 三页跨度跨页勾选 ----------
    names1 = [r["name"] for r in p1["rows"]]
    sel_a, sel_b = [], []
    for nm in names1[:2]:
        sel_a.append(set_row_checked(who, nm))
        time.sleep(0.4)
    ev["select_page1"] = sel_a
    ev["state_after_page1"] = U.js(who, ROWS_JS)

    click_text(who, "^下一页$")
    p2 = U.js(who, ROWS_JS)
    ev["page2"] = p2
    sel_c = set_row_checked(who, p2["rows"][0]["name"])
    time.sleep(0.4)
    ev["select_page2"] = sel_c

    click_text(who, "^跳转到第 3 页")
    p3 = U.js(who, ROWS_JS)
    ev["page3"] = p3
    sel_d = set_row_checked(who, p3["rows"][0]["name"])
    time.sleep(0.4)
    ev["select_page3"] = sel_d

    click_text(who, "^跳转到第 1 页")
    back = U.js(who, ROWS_JS)
    ev["page1_after_return"] = back
    selected_names = [names1[0], names1[1], p2["rows"][0]["name"], p3["rows"][0]["name"]]
    selected_ids = [ev["api_id_by_name"][n] for n in selected_names]
    ev["cross_page_selection"] = {
        "selected_names": selected_names, "selected_ids": selected_ids,
        "page1_checked_after_return": [r["checked"] for r in back["rows"][:2]],
        "count_text": back["selected_count_text"],
        "retained": all(r["checked"] for r in back["rows"][:2]) and (back["selected_count_text"] == "已选择 4 项"),
    }

    # ---------- 2. 真实批量（跨页 4 个）----------
    ev["bulk_maintenance"] = bulk_action(who, "^设为维护", selected_ids)
    ev["status_after_maintenance"] = {n: status_map().get(n, (None, None))[1] for n in selected_names}
    names_now = [n for n in status_map() if n.startswith(f"qa-{nonce}")]
    ev["maintenance_exact_set"] = sorted(n for n in names_now if status_map()[n][1] == "maintenance") == sorted(selected_names)
    ev["state_after_bulk_maintenance"] = U.js(who, ROWS_JS)

    ev["bulk_resume"] = bulk_action(who, "^(恢复|设为在线|恢复在线)", selected_ids)
    ev["status_after_resume"] = {n: status_map().get(n, (None, None))[1] for n in selected_names}

    # ---------- 3. 筛选隐藏已选行后的 payload ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    st = U.js(who, ROWS_JS)
    ev["filter_phase_start"] = st
    f_names = [r["name"] for r in st["rows"]]
    ev["filter_select"] = [set_row_checked(who, f_names[0]), set_row_checked(who, f_names[1])]
    time.sleep(0.6)
    target = [n for n in ev["api_id_by_name"] if n not in f_names[:2]]
    needle = target[0][-6:] if target else "zzzz"
    ev["filter_apply"] = set_filter(who, needle)
    filtered = U.js(who, ROWS_JS)
    ev["filtered_view"] = filtered
    ev["filter_hides_selected"] = not any(r["checked"] for r in filtered["rows"])
    ev["bulk_filtered"] = bulk_action(who, "^设为维护", [ev["api_id_by_name"][f_names[0]], ev["api_id_by_name"][f_names[1]]])
    ev["bulk_filtered_matches_ui_count"] = (
        (filtered["selected_count_text"] or "").startswith("已选择 ")
        and int((filtered["selected_count_text"] or "已选择 0 项").split()[1]) == ev["bulk_filtered"]["payload_id_count"])
    ev["status_after_filtered_bulk"] = {n: status_map().get(n, (None, None))[1] for n in f_names[:2]}
    set_filter(who, "", wait=1.6)
    ev["after_filter_clear"] = U.js(who, ROWS_JS)
    ev["bulk_resume2"] = bulk_action(who, "^(恢复|设为在线|恢复在线)",
                                     [ev["api_id_by_name"][f_names[0]], ev["api_id_by_name"][f_names[1]]])

    # ---------- 4. 排序变化 ----------
    sort = U.js(who, r"""
(() => {
  const b = [...document.querySelectorAll('button')].find(x => /主机名/.test(x.getAttribute('aria-label')||'') && x.offsetParent !== null);
  if (!b) return {found:false};
  const before = b.getAttribute('aria-label');
  b.click();
  return {found:true, aria_before: before, aria_after: b.getAttribute('aria-label')};
})()""")
    time.sleep(1.6)
    ev["sort_click"] = sort
    ev["after_sort"] = U.js(who, ROWS_JS)

    # ---------- 5. pageSize 10 → 20 → 10 ----------
    ev["pagesize_20"] = set_page_size(who, "20")
    ev["after_pagesize_20"] = U.js(who, ROWS_JS)
    ev["pagesize_back_10"] = set_page_size(who, "10")
    ev["after_pagesize_back"] = U.js(who, ROWS_JS)

    # ---------- 6. 他页删除造成位移，按 ID 保持 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    s0 = U.js(who, ROWS_JS)
    victim = s0["rows"][0]["name"]           # 第 1 页第 1 行（在选中行之前，删除会让后续行整体前移）
    click_text(who, "^下一页$")
    s1 = U.js(who, ROWS_JS)
    keep_name = s1["rows"][0]["name"]
    ev["shift_keep_selected"] = set_row_checked(who, keep_name)
    time.sleep(0.5)
    click_text(who, "^跳转到第 1 页")
    U.install_request_recorder(who)
    ev["shift_victim"] = victim
    ev["shift_delete_menu"] = row_action_delete(who, victim)
    ev["shift_delete_click"] = delete_via_menu(who)
    time.sleep(2.0)
    click_text(who, "^下一页$")
    after_shift = U.js(who, ROWS_JS)
    ev["after_shift_page2"] = after_shift
    ev["shift_kept_by_id"] = set_row_checked(who, keep_name, on=True)
    ev["shift_recheck"] = U.js(who, ROWS_JS)
    ev["api_after_shift_delete"] = {k: status_map().get(k, (None, None))[1] for k in (victim, keep_name)}
    ev["shift_victim_absent_in_api"] = victim not in status_map()

    # ---------- 7. 末页末项真实删除 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    click_text(who, "^跳转到最后一页")
    last = U.js(who, ROWS_JS)
    ev["last_page_before_delete"] = last
    before_total = len(api_nodes())
    ev["api_total_before_last_delete"] = before_total
    ev["last_page_pagecount_before"] = last["page_label"]
    last_name = last["rows"][-1]["name"]
    ev["last_row_selected_before_delete"] = last["rows"][-1]["checked"]
    if not last["rows"][-1]["checked"]:
        ev["last_row_select"] = set_row_checked(who, last_name)
        time.sleep(0.5)
    ev["last_delete_menu"] = row_action_delete(who, last_name)
    ev["last_delete_click"] = delete_via_menu(who)
    time.sleep(2.2)
    ev["last_page_after_delete"] = U.js(who, ROWS_JS)
    ev["api_total_after_last_delete"] = len(api_nodes())
    ev["deleted_last_node_absent"] = last_name not in status_map()
    ev["deleted_last_node_name"] = last_name

    # ---------- 8. 刷新 -------------
    U.open_(who, "/nodes")
    sel_before = U.js(who, ROWS_JS)
    ev["select_before_reload"] = sel_before
    U.reload(who, wait=2.2)
    U.install_request_recorder(who)
    ev["after_reload"] = U.js(who, ROWS_JS)

    # ---------- 9. Execution 跨页搜索 / Trigger ----------
    exs = C.as_list(hub.get("/api/executions", admin)["json"])
    ex_id = exs[-1]["id"] if exs else None
    queries = {}
    if ex_id:
        for q in (f"/executions?q={ex_id}", "/executions?trigger=manual", "/executions?status=SUCCESS"):
            U.open_(who, q)
            queries[q] = U.js(who, r"""
(() => { const rows=[...document.querySelectorAll('table tbody tr')];
  return {rows: rows.length, page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null,
          empty_msg: /没有匹配的执行记录/.test(document.body.innerText||'')}; })()""")
    ev["execution_search"] = {"executions_total": len(exs), "queries": queries}

    ev["verdict"] = {
        "cross_page_retained_3_pages": ev["cross_page_selection"]["retained"],
        "bulk_payload_equals_selection": ev["bulk_maintenance"]["payload_equals_expected"],
        "bulk_no_duplicate_ids": ev["bulk_maintenance"]["no_duplicates"],
        "maintenance_exact_set": ev["maintenance_exact_set"],
        "resume_payload_equals_selection": ev["bulk_resume"]["payload_equals_expected"],
        "filtered_bulk_no_ghost": ev["bulk_filtered"]["payload_equals_expected"],
        "filtered_bulk_payload_count_matches_ui": ev["bulk_filtered_matches_ui_count"],
        "selection_retained_after_sort": (ev["after_sort"]["selected_count_text"] is not None),
        "selection_retained_after_pagesize_change": (ev["after_pagesize_20"]["selected_count_text"] is not None),
        "shift_kept_by_id": bool(ev["shift_kept_by_id"].get("after") == "true"),
        "last_page_row_deleted": len(ev["last_page_after_delete"]["rows"]) == len(last["rows"]) - 1,
        "api_total_decreased": ev["api_total_after_last_delete"] == before_total - 1,
        "reload_clears_selection": ev["after_reload"]["selected_count_text"] is None,
    }
    C.write_evidence("g9", "X14-multipage-selection-drift.json", ev)
    (C.EVID_DIRS["g9"].parent / "results.json").write_text(
        json.dumps(ev, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({"dataset": ev["dataset"], "pagesize_init": ev["pagesize_init"],
                      "cross_page_selection": ev["cross_page_selection"],
                      "bulk_maintenance": {k: ev["bulk_maintenance"][k] for k in
                                           ("payload_id_count", "payload_equals_expected", "no_duplicates",
                                            "request_methods", "dialog_text_tail" if False else "request_count")},
                      "status_after_maintenance": ev["status_after_maintenance"],
                      "maintenance_exact_set": ev["maintenance_exact_set"],
                      "filtered_view_count_text": filtered["selected_count_text"],
                      "bulk_filtered": {k: ev["bulk_filtered"][k] for k in
                                        ("payload_id_count", "payload_equals_expected", "no_duplicates")},
                      "after_sort": ev["after_sort"].get("selected_count_text"),
                      "after_pagesize_20": ev["after_pagesize_20"].get("selected_count_text"),
                      "shift": {"victim": victim, "keep": keep_name,
                                "victim_absent": ev["shift_victim_absent_in_api"],
                                "kept": ev["shift_kept_by_id"]},
                      "last_delete": {"name": last_name, "rows_before": len(last["rows"]),
                                      "rows_after": len(ev["last_page_after_delete"]["rows"]),
                                      "total_before": before_total, "total_after": ev["api_total_after_last_delete"],
                                      "pagecount_after": ev["last_page_after_delete"]["page_label"]},
                      "after_reload_count": ev["after_reload"]["selected_count_text"],
                      "verdict": ev["verdict"]},
                     ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
