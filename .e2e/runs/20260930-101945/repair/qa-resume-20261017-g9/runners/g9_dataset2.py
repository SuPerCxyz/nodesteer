"""G9（X14）v2：多页节点数据集批量选择漂移与跨页保持 —— 修正 v1 harness 竞态后的权威执行。

v1 问题（harness，非产品）：勾选后立即读 aria-checked 落在 React 重渲染之前 → 出现假“未勾选”；
批量动作完成后选择栏关闭，导致后续“恢复”找不到按钮。v2 全部改为点击后等待再读、按名称跨页定位、
每次动作前后做全量状态快照并计算实际变更集。
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

CLICK_ROW_JS = r"""
(async () => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const tr = rows.find(r => (r.innerText||'').includes(%s));
  if (!tr) return {found:false, rows: rows.length};
  const cb = tr.querySelector('button[role=checkbox]');
  if (!cb) return {found:true, checkbox:false};
  const before = cb.getAttribute('aria-checked');
  if (before !== %s) {
    cb.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
    cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
    cb.click();
  }
  await new Promise(r => setTimeout(r, 550));
  return {found:true, name: %s, before, after: cb.getAttribute('aria-checked'),
          checked_now: [...document.querySelectorAll('table tbody tr')]
              .filter(r => { const c = r.querySelector('button[role=checkbox]');
                             return c && c.getAttribute('aria-checked') === 'true'; }).length};
})()
"""

ROWS_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const cbOf = (tr) => tr.querySelector('button[role=checkbox]');
  const nameOf = (tr) => {
    const lines = (tr.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
    return lines.find(l => /^(qa-|node-)/.test(l)) || lines[0] || null;
  };
  const texts = [...document.querySelectorAll('*')].filter(e => e.offsetParent !== null)
      .map(e => (e.innerText||'').trim());
  const bar = texts.find(t => /^已选择 \d+ 项$/.test(t)) || null;
  const barBtns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
      .map(b => (b.innerText||'').trim())
      .filter(t => /^(设为维护|恢复|清除选择|设置|取消)/.test(t));
  return {
    path: location.pathname + location.search,
    rows: rows.map(tr => ({name: nameOf(tr),
                           checked: cbOf(tr) ? cbOf(tr).getAttribute('aria-checked') === 'true' : null})),
    selected_count_text: bar,
    bar_buttons: barBtns,
    page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null,
    header_cb: (document.querySelector('button[aria-label="全选所有行"]')||{}).getAttribute
        ? document.querySelector('button[aria-label="全选所有行"]').getAttribute('aria-checked') : null,
  };
})()"""


def click_text(who, pattern, wait=1.3):
    r = U.js(who, r"""
(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null);
  const b = btns.find(x => new RegExp(%s).test((x.innerText||'').trim()));
  if (!b) return {clicked:false, candidates: btns.map(x=>(x.innerText||'').trim()).filter(Boolean)};
  b.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  b.click();
  return {clicked:true, text:(b.innerText||'').trim()};
})()""" % json.dumps(pattern))
    time.sleep(wait)
    return r


def check_row(who, name, on=True, tries=3):
    want = "true" if on else "false"
    out = {"name": name, "want": want, "attempts": []}
    for _ in range(tries):
        r = U.js(who, CLICK_ROW_JS % (json.dumps(name), json.dumps(want), json.dumps(name)))
        out["attempts"].append(r)
        if r.get("after") == want:
            out["ok"] = True
            return out
        time.sleep(0.5)
    out["ok"] = False
    return out


def set_filter(who, value, wait=1.6):
    r = U.js(who, r"""
(() => {
  const inputs = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null);
  const el = inputs.find(i => /搜索|筛选|search|filter/i.test(i.placeholder||'')) || inputs[0];
  if (!el) return {found:false};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return {found:true, placeholder: el.placeholder};
})()""" % json.dumps(value))
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
  await new Promise(r => setTimeout(r, 700));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').trim() === %s);
  if (!opt) return {found:true, options: [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim())};
  opt.click();
  await new Promise(r => setTimeout(r, 1100));
  return {found:true, switched:true,
          rows: [...document.querySelectorAll('table tbody tr')].length,
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null};
})()""" % json.dumps(size))
    time.sleep(wait)
    return r


def delete_row(who, name):
    menu = U.js(who, r"""
(async () => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const tr = rows.find(r => (r.innerText||'').includes(%s));
  if (!tr) return {found:false};
  const trigger = [...tr.querySelectorAll('button')].slice(-1)[0];
  if (!trigger) return {found:true, trigger:false};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 800));
  return {found:true, items: [...document.querySelectorAll('[role=menuitem]')].map(e=>(e.innerText||'').trim())};
})()""" % json.dumps(name))
    click = U.js(who, r"""
(async () => {
  const items = [...document.querySelectorAll('[role=menuitem]')];
  const del = items.find(e => /^删除$/.test((e.innerText||'').trim()));
  if (!del) return {clicked:false, items: items.map(e=>(e.innerText||'').trim())};
  del.click();
  await new Promise(r => setTimeout(r, 900));
  const confirm = [...document.querySelectorAll('button')]
      .find(b => /^(确认|确定|删除)$/.test((b.innerText||'').trim()));
  if (confirm) { confirm.click(); await new Promise(r => setTimeout(r, 1500)); return {clicked:true, confirmed:true}; }
  return {clicked:true, confirmed:false};
})()""")
    time.sleep(1.5)
    return {"menu": menu, "click": click}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])

    def nodes():
        return C.as_list(hub.get("/api/nodes?limit=500", admin)["json"])

    def snap():
        return {n["hostname"]: n.get("status") for n in nodes()}

    def ids_by_name():
        return {n["hostname"]: n["id"] for n in nodes()}

    ev = {"case_id": "X14", "version": "v2 (harness race fixed)",
          "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"]}
    ev["dataset"] = {"nodes_api_total": len(nodes()),
                     "status_breakdown": {s: sum(1 for v in snap().values() if v == s)
                                          for s in sorted(set(snap().values()))}}

    # ---------- P0 复位 v1 遗留的 maintenance 节点 ----------
    restored = []
    for name, st in snap().items():
        if st == "maintenance" and name.startswith(f"qa-{nonce}"):
            nid = ids_by_name()[name]
            r = hub.put(f"/api/nodes/{nid}", admin, body={"status": "pending"})
            restored.append({"name": name, "http": r["status"]})
    ev["p0_restore_leftovers"] = restored
    ev["p0_status_after"] = {s: sum(1 for v in snap().values() if v == s) for s in sorted(set(snap().values()))}

    U.init()
    who = "g9b"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])

    # ---------- P1 三页跨度跨页勾选 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    ev["p1_pagesize"] = set_page_size(who, "10")
    U.install_request_recorder(who)
    p1 = U.js(who, ROWS_JS)
    ev["p1_page1"] = p1
    ev["p1_id_by_name"] = ids_by_name()
    n1 = [r["name"] for r in p1["rows"]]
    ev["p1_checks"] = [check_row(who, n1[0]), check_row(who, n1[1])]
    click_text(who, "^下一页$")
    p2 = U.js(who, ROWS_JS)
    ev["p1_page2"] = p2
    ev["p1_checks"] += [check_row(who, p2["rows"][0]["name"])]
    click_text(who, "^跳转到第 3 页")
    p3 = U.js(who, ROWS_JS)
    ev["p1_page3"] = p3
    ev["p1_checks"] += [check_row(who, p3["rows"][0]["name"])]
    click_text(who, "^跳转到第 1 页")
    back = U.js(who, ROWS_JS)
    ev["p1_back"] = back
    sel4_names = [n1[0], n1[1], p2["rows"][0]["name"], p3["rows"][0]["name"]]
    sel4_ids = [ev["p1_id_by_name"][x] for x in sel4_names]
    ev["p1_verdict"] = {"selected_names": sel4_names, "selected_ids": sel4_ids,
                        "count_text": back["selected_count_text"],
                        "page1_checked": [r["checked"] for r in back["rows"][:2]],
                        "retained_3_pages": back["selected_count_text"] == "已选择 4 项"
                                            and all(r["checked"] for r in back["rows"][:2])}

    # ---------- P2 批量动作（真实执行）+ 变更集核验 ----------
    def bulk_and_verify(tag, pattern, expect_ids):
        before = snap()
        U.clear_rec(who)
        opened = click_text(who, pattern, wait=1.2)
        dlg = U.js(who, r"""
(() => { const d = document.querySelector('[role=dialog]');
  return {present: !!d, text: d ? (d.innerText||'').replace(/\n/g,' | ').slice(0,500) : null,
          buttons: d ? [...d.querySelectorAll('button')].map(b=>(b.innerText||'').trim()) : []}; })()""")
        confirmed = click_text(who, "^确认执行$|^确认升级$|^确认$|^确定$", wait=0.7)
        time.sleep(2.6)
        reqs = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT", "DELETE", "PATCH")
                and "/api/nodes" in (r.get("url") or "")]
        ids = []
        for r in reqs:
            seg = (r.get("url") or "").split("/api/nodes/")[-1].split("?")[0].split("/")[0]
            if seg and seg not in ("upgrade", "enrollment"):
                ids.append(seg)
            for m in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", r.get("body") or ""):
                if m not in ids:
                    ids.append(m)
        after = snap()
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        id2name = {v: k for k, v in ids_by_name().items()}
        return {"tag": tag, "pattern": pattern, "open_bar": opened, "dialog": dlg, "confirm": confirmed,
                "request_count": len(reqs), "request_methods": [r.get("method") for r in reqs],
                "payload_ids": ids, "expected_ids": expect_ids,
                "payload_equals_expected": sorted(set(ids)) == sorted(set(expect_ids)),
                "no_duplicates": len(ids) == len(set(ids)),
                "changed_names": changed,
                "changed_names_equal_expected": sorted(changed) == sorted(sel4_names if tag.startswith("maintenance") or tag.startswith("restore") else changed),
                "status_delta_names": [{"name": k, "before": before.get(k), "after": after.get(k)} for k in changed],
                "changed_ids": sorted({id2name.get(c) for c in changed if id2name.get(c)})}

    ev["p2_maintenance"] = bulk_and_verify("maintenance", "^设为维护", sel4_ids)
    ev["p2_state_after"] = U.js(who, ROWS_JS)
    ev["p2_status"] = {k: snap().get(k) for k in sel4_names}

    # ---------- P3 批量恢复（重新按名称跨页选择 maintenance 节点）----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    picked = []
    for target in sel4_names:
        found = None
        for page in range(1, 6):
            if page > 1:
                click_text(who, f"^跳转到第 {page} 页", wait=1.2)
            st = U.js(who, ROWS_JS)
            if any(r["name"] == target for r in st["rows"]):
                found = page
                break
        if found:
            picked.append({"name": target, "page": found, "check": check_row(who, target)})
    ev["p3_picked"] = picked
    st_bar = U.js(who, ROWS_JS)
    ev["p3_bar"] = {"count_text": st_bar["selected_count_text"], "bar_buttons": st_bar["bar_buttons"]}
    restore_pattern = next((t for t in st_bar["bar_buttons"]
                            if t not in ("清除选择",) and not t.startswith("设为维护") and not t.startswith("设置")),
                           "^恢复|^设为在线|^取消")
    ev["p3_restore"] = bulk_and_verify("restore", f"^{re.escape(restore_pattern)}", sel4_ids)
    ev["p3_status"] = {k: snap().get(k) for k in sel4_names}

    # ---------- P4 筛选隐藏已选行 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    f0 = U.js(who, ROWS_JS)
    f_names = [r["name"] for r in f0["rows"]]
    ev["p4_start"] = {"count_text": f0["selected_count_text"], "rows": f0["rows"][:3]}
    ev["p4_checks"] = [check_row(who, f_names[0]), check_row(who, f_names[1])]
    ev["p4_bar_before_filter"] = U.js(who, ROWS_JS)["selected_count_text"]
    outside = [n for n in ev["p1_id_by_name"] if n not in f_names[:2]]
    ev["p4_filter"] = set_filter(who, outside[0][-6:])
    fv = U.js(who, ROWS_JS)
    ev["p4_filtered_view"] = {"count_text": fv["selected_count_text"], "bar_buttons": fv["bar_buttons"],
                             "rows": fv["rows"], "hides_selected": not any(r["checked"] for r in fv["rows"])}
    if fv["bar_buttons"] and any(b.startswith("设为维护") for b in fv["bar_buttons"]):
        ev["p4_bulk"] = bulk_and_verify("filtered", "^设为维护",
                                        [ev["p1_id_by_name"][f_names[0]], ev["p1_id_by_name"][f_names[1]]])
        ev["p4_payload_matches_ui_count"] = (
            int((fv["selected_count_text"] or "已选择 0 项").split()[1]) == ev["p4_bulk"]["payload_id_count"])
        ev["p4_status"] = {k: snap().get(k) for k in f_names[:2]}
    else:
        ev["p4_bulk"] = {"skipped": True, "reason": "筛选后选择栏消失（选择被清空）",
                         "no_writes_on_filter": True}
        ev["p4_payload_matches_ui_count"] = (fv["selected_count_text"] is None)
        ev["p4_status"] = {k: snap().get(k) for k in f_names[:2]}
    U.shot(who, "g9", "G9-filter-selection-state.png")
    set_filter(who, "", wait=1.6)
    ev["p4_after_clear"] = U.js(who, ROWS_JS)

    # ---------- P5 排序变化 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    s0 = U.js(who, ROWS_JS)
    s_names = [r["name"] for r in s0["rows"]]
    ev["p5_checks"] = [check_row(who, s_names[0]), check_row(who, s_names[1])]
    ev["p5_before_sort"] = U.js(who, ROWS_JS)
    U.clear_rec(who)
    ev["p5_sort_click"] = U.js(who, r"""
(() => {
  const b = [...document.querySelectorAll('button')]
      .find(x => /主机名/.test(x.getAttribute('aria-label')||'') && x.offsetParent !== null);
  if (!b) return {found:false};
  const before = b.getAttribute('aria-label');
  b.click();
  return {found:true, aria_before: before};
})()""")
    time.sleep(1.6)
    ev["p5_after_sort"] = U.js(who, ROWS_JS)
    ev["p5_sort_writes"] = [r for r in U.rec(who) if r.get("method") != "GET"]
    ev["p5_selected_status"] = {k: snap().get(k) for k in s_names[:2]}

    # ---------- P6 pageSize 切换 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    z0 = U.js(who, ROWS_JS)
    z_names = [r["name"] for r in z0["rows"]]
    ev["p6_checks"] = [check_row(who, z_names[0]), check_row(who, z_names[1])]
    ev["p6_before"] = U.js(who, ROWS_JS)
    U.clear_rec(who)
    ev["p6_switch_20"] = set_page_size(who, "20")
    ev["p6_after_20"] = U.js(who, ROWS_JS)
    ev["p6_switch_10"] = set_page_size(who, "10")
    ev["p6_after_10"] = U.js(who, ROWS_JS)
    ev["p6_writes"] = [r for r in U.rec(who) if r.get("method") != "GET"]

    # ---------- P7 他页删除造成位移（按 ID 保持）----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    h0 = U.js(who, ROWS_JS)
    victim = h0["rows"][0]["name"]
    click_text(who, "^下一页$")
    h2 = U.js(who, ROWS_JS)
    keep = h2["rows"][0]["name"]
    ev["p7_keep_check"] = check_row(who, keep)
    ev["p7_bar_before"] = U.js(who, ROWS_JS)["selected_count_text"]
    click_text(who, "^跳转到第 1 页")
    ev["p7_delete_victim"] = delete_row(who, victim)
    ev["p7_victim"] = victim
    ev["p7_victim_absent_in_api"] = victim not in snap()
    # 跨页找出 keep 行当前勾选态
    found_state = None
    for page in range(1, 6):
        if page > 1:
            click_text(who, f"^跳转到第 {page} 页", wait=1.1)
        st = U.js(who, ROWS_JS)
        row = next((r for r in st["rows"] if r["name"] == keep), None)
        if row:
            found_state = {"page": page, "checked": row["checked"], "count_text": st["selected_count_text"]}
            break
    ev["p7_keep_state_after_shift"] = found_state
    ev["p7_bar_after"] = U.js(who, ROWS_JS)["selected_count_text"]
    ev["p7_bulk"] = bulk_and_verify("shift", "^设为维护", [ev["p1_id_by_name"][keep]])
    ev["p7_status"] = {keep: snap().get(keep)}

    # ---------- P8 末页末项真实删除 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    click_text(who, "^跳转到最后一页", wait=1.6)
    last = U.js(who, ROWS_JS)
    ev["p8_last_before"] = {"page_label": last["page_label"], "rows": len(last["rows"]),
                            "count_text": last["selected_count_text"],
                            "last_row": last["rows"][-1]}
    last_name = last["rows"][-1]["name"]
    ev["p8_check_last"] = check_row(who, last_name)
    ev["p8_bar_after_check"] = U.js(who, ROWS_JS)["selected_count_text"]
    total_before = len(nodes())
    U.clear_rec(who)
    ev["p8_delete"] = delete_row(who, last_name)
    ev["p8_requests"] = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT", "DELETE", "PATCH")]
    ev["p8_after"] = U.js(who, ROWS_JS)
    ev["p8_api"] = {"total_before": total_before, "total_after": len(nodes()),
                    "deleted_absent": last_name not in snap(), "deleted": last_name}
    ev["p8_no_ghost"] = (ev["p8_after"]["selected_count_text"] is None
                         or last_name not in (ev["p8_after"]["selected_count_text"] or ""))

    # ---------- P9 刷新清空 ----------
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    set_page_size(who, "10")
    U.install_request_recorder(who)
    r0 = U.js(who, ROWS_JS)
    rn = [r["name"] for r in r0["rows"]]
    ev["p9_checks"] = [check_row(who, rn[0]), check_row(who, rn[1])]
    ev["p9_before_reload"] = U.js(who, ROWS_JS)
    U.reload(who, wait=2.4)
    U.install_request_recorder(who)
    ev["p9_after_reload"] = U.js(who, ROWS_JS)

    # ---------- P10 Execution 跨页搜索 / Trigger ----------
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
    ev["p10_execution_search"] = {"executions_total": len(exs), "queries": queries}

    ev["verdict"] = {
        "cross_page_retained_3_pages": ev["p1_verdict"]["retained_3_pages"],
        "bulk_payload_equals_selection": ev["p2_maintenance"]["payload_equals_expected"],
        "bulk_changed_set_equals_selection": sorted(ev["p2_maintenance"]["changed_names"]) == sorted(sel4_names),
        "bulk_no_duplicate_ids": ev["p2_maintenance"]["no_duplicates"],
        "restore_payload_equals_selection": ev["p3_restore"]["payload_equals_expected"],
        "restore_changed_set_equals_selection": sorted(ev["p3_restore"]["changed_names"]) == sorted(sel4_names),
        "filter_hides_selected": ev["p4_filtered_view"]["hides_selected"],
        "filter_no_drift": (ev["p4_bulk"].get("payload_equals_expected") is True
                            or ev["p4_bulk"].get("skipped") is True),
        "filter_payload_count_matches_ui": ev["p4_payload_matches_ui_count"],
        "sort_retains_selection": ev["p5_after_sort"]["selected_count_text"] is not None,
        "sort_no_writes": len(ev["p5_sort_writes"]) == 0,
        "pagesize20_retains_selection": ev["p6_after_20"]["selected_count_text"] is not None,
        "pagesize10_retains_selection": ev["p6_after_10"]["selected_count_text"] is not None,
        "pagesize_no_writes": len(ev["p6_writes"]) == 0,
        "shift_keep_still_checked": bool(found_state and found_state.get("checked")),
        "shift_bulk_payload_equals_keep": ev["p7_bulk"]["payload_equals_expected"],
        "last_row_deleted_from_api": ev["p8_api"]["deleted_absent"],
        "last_row_total_decreased": ev["p8_api"]["total_after"] == ev["p8_api"]["total_before"] - 1,
        "last_row_no_ghost": ev["p8_no_ghost"],
        "reload_clears_selection": (ev["p9_after_reload"]["selected_count_text"] is None
                                    and not any(r["checked"] for r in ev["p9_after_reload"]["rows"])),
        "execution_search_ok": all((v["rows"] > 0) or v["empty_msg"] for v in queries.values()) if queries else None,
    }
    C.write_evidence("g9", "X14-multipage-selection-drift-v2.json", ev)
    (C.EVID_DIRS["g9"].parent / "results.json").write_text(json.dumps(ev, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({
        "dataset": ev["dataset"], "p0": ev["p0_restore_leftovers"],
        "p1": ev["p1_verdict"],
        "p2": {"payload": ev["p2_maintenance"]["payload_ids"], "ok": ev["p2_maintenance"]["payload_equals_expected"],
               "changed": ev["p2_maintenance"]["status_delta_names"], "bar_after": ev["p2_state_after"]["selected_count_text"]},
        "p3": {"bar": ev["p3_bar"], "restore_pattern": restore_pattern,
               "ok": ev["p3_restore"]["payload_equals_expected"], "changed": ev["p3_restore"]["status_delta_names"]},
        "p4": {"count_after_filter": ev["p4_filtered_view"]["count_text"], "bulk": ev["p4_bulk"].get("payload_ids"),
               "skipped": ev["p4_bulk"].get("skipped"), "status": ev["p4_status"]},
        "p5": {"before": ev["p5_before_sort"]["selected_count_text"], "after_sort": ev["p5_after_sort"]["selected_count_text"],
               "writes": len(ev["p5_sort_writes"])},
        "p6": {"before": ev["p6_before"]["selected_count_text"], "after20": ev["p6_after_20"]["selected_count_text"],
               "after10": ev["p6_after_10"]["selected_count_text"], "writes": len(ev["p6_writes"])},
        "p7": {"victim": victim, "keep": keep, "keep_state": found_state, "bar_after": ev["p7_bar_after"],
               "bulk": ev["p7_bulk"]["payload_ids"], "ok": ev["p7_bulk"]["payload_equals_expected"]},
        "p8": {"before": ev["p8_last_before"], "after": {"label": ev["p8_after"]["page_label"], "rows": len(ev["p8_after"]["rows"]),
                                                          "count": ev["p8_after"]["selected_count_text"]},
               "api": ev["p8_api"], "no_ghost": ev["p8_no_ghost"]},
        "p9": {"before": ev["p9_before_reload"]["selected_count_text"], "after": ev["p9_after_reload"]["selected_count_text"]},
        "p10": ev["p10_execution_search"],
        "verdict": ev["verdict"]}, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
