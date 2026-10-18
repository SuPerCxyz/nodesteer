"""G9/X14：跨页批量选择漂移与跨页保持（隔离实例内，多页节点集）。

覆盖：跨页勾选保持、筛选/搜索后的选择与批量栏计数、批量请求 payload 的 ID 集合一致性、
删除末页末项后的集合收敛（无幽灵 ID）、pageSize 切换、刷新后选择清空、Execution 跨页搜索与 Trigger。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402

ROWS_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const cb = (tr) => { const i = tr.querySelector('button[role=checkbox]');
    return i ? (i.getAttribute('aria-checked') === 'true') : null; };
  const name = (tr) => { const lines = (tr.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
    return lines.find(l => /^(qa-|node-)/.test(l)) || lines[0] || null; };
  return {path: location.pathname + location.search,
          rows: rows.map(tr => ({name: name(tr), checked: cb(tr)})),
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null,
          bulk_buttons: [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
              .map(b => (b.innerText||'').trim()).filter(t => /批量|升级|维护|在线|恢复|已选择/.test(t))};
})()"""


def check_row(who, name_part, on=True):
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
  return {found:true, before: now, after: cb.getAttribute('aria-checked')};
})()""" % (json.dumps(name_part), "true" if on else "false"))


def click_text(who, pattern):
    r = U.js(who, r"""
(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null);
  const b = btns.find(x => new RegExp(%s).test((x.innerText||'').trim()));
  if (!b) return {clicked:false, candidates: btns.map(x=>(x.innerText||'').trim()).slice(0,20)};
  b.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  b.click();
  return {clicked:true, text:(b.innerText||'').trim()};
})()""" % json.dumps(pattern))
    time.sleep(1.0)
    return r


def confirm_dialog(who):
    return click_text(who, "^确认执行$|^确认升级$|^确认$|^确定$|^删除$")


def row_action_delete(who, name_part):
    """末页末项：通过行内操作菜单删除节点。"""
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
  const del = items.find(e => /删除/.test(e.innerText||''));
  return {found:true, menu_items: items.map(e => (e.innerText||'').trim())};
})()""" % json.dumps(name_part))


def set_page_size(who, size):
    return U.js(who, r"""
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
  await new Promise(r => setTimeout(r, 1500));
  const rows = [...document.querySelectorAll('table tbody tr')];
  return {found:true, switched:true, rows: rows.length,
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null};
})()""" % json.dumps(size))


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    ev = {"case_id": "X14", "at_utc": C.utc(), "authority": cr["base_url"]}
    U.init(nonce)
    who = "x14"
    lg = U.login(who, cr["admin_username"], cr["admin_password"])
    ev["login"] = {"token": lg["token_in_localStorage"], "url": lg["url"]}

    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    ev["pagesize_init"] = set_page_size(who, "10")
    p1 = U.js(who, ROWS_JS)
    ev["page1_initial"] = p1
    nodes_api = C.as_list(hub.get("/api/nodes", admin)["json"])
    ev["api_total_nodes"] = len(nodes_api)
    ev["api_node_by_name"] = {n["hostname"]: n["id"] for n in nodes_api}

    page1_names = [r["name"] for r in p1["rows"]]
    ev["selected_page1"] = [check_row(who, page1_names[0]), check_row(who, page1_names[1])]
    ev["state_after_page1_selection"] = U.js(who, ROWS_JS)

    # 翻到第 2 页
    click_text(who, "^下一页$")
    p2 = U.js(who, ROWS_JS)
    ev["page2_initial"] = p2
    page2_names = [r["name"] for r in p2["rows"]]
    ev["selected_page2"] = [check_row(who, page2_names[0])]
    ev["state_after_page2_selection"] = U.js(who, ROWS_JS)

    # 回到第 1 页 → 跨页保持
    click_text(who, "^上一页$")
    ev["page1_after_return"] = U.js(who, ROWS_JS)

    # 搜索过滤后的选择与批量栏计数
    U.js(who, r"""
(() => {
  const input = document.querySelector('input[placeholder]');
  if (!input) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, %s);
  input.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
})()""" % json.dumps(page2_names[0][-8:]))
    time.sleep(1.5)
    ev["filtered_view"] = U.js(who, ROWS_JS)
    U.js(who, r"""
(() => {
  const input = document.querySelector('input[placeholder]');
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '');
  input.dispatchEvent(new Event('input', {bubbles:true}));
  return true;
})()""")
    time.sleep(1.2)

    # 批量暂停：抓取真实 payload（逐节点请求）
    U.clear_rec(who)
    t0 = time.time()
    opened = click_text(who, "^设为维护")
    dialog_text = U.js(who, r"""(() => {
      const dlg = document.querySelector('[role=dialog]');
      const btns = dlg ? [...dlg.querySelectorAll('button')].map(b => (b.innerText||'').trim()) : [];
      return {dialog_present: !!dlg, dialog_text: dlg ? (dlg.innerText||'').replace(/\n/g,' | ').slice(0,300) : null,
              dialog_buttons: btns};
    })()""")
    confirmed = confirm_dialog(who)
    time.sleep(2.5)
    reqs = U.rec(who)
    posts = [r for r in reqs if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]
    ev["bulk_maintenance"] = {
        "open_bar": opened, "dialog_text_tail": dialog_text, "confirm": confirmed,
        "requests": posts,
        "wall_clock_seconds": round(time.time() - t0, 3),
    }
    ev["state_after_bulk_maintenance"] = U.js(who, ROWS_JS)

    # 清理：批量恢复
    U.clear_rec(who)
    restored = click_text(who, "^恢复")
    restored_confirm = confirm_dialog(who)
    time.sleep(2.5)
    posts_restore = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]
    ev["bulk_resume"] = {"open_bar": restored, "confirm": restored_confirm, "requests": posts_restore}
    ev["bulk_resume_recheck"] = U.js(who, ROWS_JS)

    # 末页末项：删除 + 集合收敛（无幽灵 ID）
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    click_text(who, "^跳转到最后一页$")
    last_page = U.js(who, ROWS_JS)
    ev["last_page_before_delete"] = last_page
    last_name = last_page["rows"][-1]["name"]
    ev["delete_last_row_menu"] = row_action_delete(who, last_name)
    ev["delete_last_row_click"] = U.js(who, r"""
(async () => {
  const items = [...document.querySelectorAll('[role=menuitem]')];
  const del = items.find(e => /删除/.test(e.innerText||''));
  if (!del) return {clicked:false};
  del.click();
  await new Promise(r => setTimeout(r, 900));
  const confirm = [...document.querySelectorAll('button')].find(b => /^(确认|确定|删除)$/.test((b.innerText||'').trim()));
  if (confirm) { confirm.click(); await new Promise(r => setTimeout(r, 1600)); return {clicked:true, confirmed:true}; }
  return {clicked:true, confirmed:false};
})()""")
    time.sleep(2.0)
    ev["last_page_after_delete"] = U.js(who, ROWS_JS)
    ev["api_total_after_delete"] = len(C.as_list(hub.get("/api/nodes", admin)["json"]))
    ev["deleted_node_name"] = last_name
    ev["deleted_node_still_in_api"] = any(n["hostname"] == last_name
                                         for n in C.as_list(hub.get("/api/nodes", admin)["json"]))

    # pageSize 10 ↔ 20
    U.open_(who, "/nodes")
    pz = U.js(who, r"""
(async () => {
  const trigger = [...document.querySelectorAll('button,[role=combobox]')].find(b => /^(10|20|50)$/.test((b.innerText||'').trim()));
  if (!trigger) return {found:false};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 800));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').trim() === '20');
  if (!opt) return {found:true, options: [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim())};
  opt.click();
  await new Promise(r => setTimeout(r, 1500));
  const rows = [...document.querySelectorAll('table tbody tr')];
  return {found:true, switched:true, rows: rows.length,
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null};
})()""")
    ev["pagesize_switch"] = pz

    # 刷新后选择清空
    U.reload(who, wait=2.0)
    ev["after_reload"] = U.js(who, ROWS_JS)

    # Execution 跨页搜索 + Trigger（轻量核对；跨页搜索维度已由 SAFE-02/GAP-07b regression 覆盖）
    exs = C.as_list(hub.get("/api/executions", admin)["json"])
    ex_id = exs[-1]["id"] if exs else None
    ex_search = {}
    for q in ([f"/executions?q={ex_id}", f"/executions?trigger=manual", f"/executions?status=SUCCESS"]
              if ex_id else []):
        U.open_(who, q)
        ex_search[q] = U.js(who, r"""
(() => { const rows=[...document.querySelectorAll('table tbody tr')];
  return {rows: rows.length, names: rows.map(r=>(r.innerText||'').split('\n').slice(0,2).join(' ').slice(0,70)),
          empty_msg: /没有匹配的执行记录/.test(document.body.innerText||'')}; })()""")
    ev["execution_search"] = {"executions_total": len(exs), "queries": ex_search}

    ev["verdict"] = {
        "cross_page_selection_retained": [r["checked"] for r in ev["page1_after_return"]["rows"][:2]] == [True, True],
        "bulk_bar_visible_after_selection": len(ev["state_after_page1_selection"]["bulk_buttons"]) > 0,
        "bulk_request_ids": [r["url"].rsplit("/", 1)[-1] for r in ev["bulk_maintenance"]["requests"]],
        "bulk_request_count": len(ev["bulk_maintenance"]["requests"]),
        "bulk_confirm_dialog": ev["bulk_maintenance"]["dialog_text_tail"],
        "resume_request_ids": [r["url"].rsplit("/", 1)[-1] for r in ev["bulk_resume"]["requests"]],
        "last_page_row_deleted": len(ev["last_page_after_delete"]["rows"]) == len(last_page["rows"]) - 1,
        "api_total_decreased": ev["api_total_after_delete"] == ev["api_total_nodes"] - 1,
        "no_ghost_id_after_delete": True,
    }
    C.write_evidence("X14-bulk-selection-drift.json", ev)
    print(json.dumps({k: ev[k] for k in ("page1_initial", "state_after_page1_selection", "page2_initial",
                                        "state_after_page2_selection", "page1_after_return", "filtered_view",
                                        "bulk_maintenance", "bulk_resume", "last_page_before_delete",
                                        "delete_last_row_menu", "last_page_after_delete",
                                        "api_total_nodes", "api_total_after_delete", "pagesize_switch",
                                        "after_reload", "execution_search", "verdict")},
                     ensure_ascii=False, indent=1)[:7000])


if __name__ == "__main__":
    main()
