"""X14 补充：批量请求 payload 与选择集合一致性（含"过滤后"选择语义）。

步骤：把所有自有 pending 节点恢复为在线 → 跨页勾选 3 个 → 设为维护(抓 payload) →
过滤到其中 1 个后再执行(抓 payload) → 恢复。
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
  return {rows: rows.map(tr => ({name: name(tr), checked: cb(tr)})),
          page_label: (document.body.innerText||'').match(/第 \d+ 页，共 \d+ 页/)?.[0] || null,
          bulk_buttons: [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
              .map(b => (b.innerText||'').trim()).filter(t => /批量|升级|维护|恢复|已选择/.test(t))};
})()"""


def js_click(who, pattern):
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
    time.sleep(0.9)
    return r


def confirm(who):
    """确认弹窗：优先对话框内主按钮。"""
    r = U.js(who, r"""
(async () => {
  const dlg = document.querySelector('[role=dialog], [role=alertdialog]');
  const scope = dlg || document;
  const btns = [...scope.querySelectorAll('button')];
  const target = btns.find(b => /^(确认执行|确认升级|确认|确定|删除)$/.test((b.innerText||'').trim()))
      || btns.filter(b => b.offsetParent !== null).slice(-1)[0];
  if (!target) return {confirmed:false, buttons: btns.map(b=>(b.innerText||'').trim())};
  target.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  target.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  target.click();
  await new Promise(r => setTimeout(r, 1500));
  return {confirmed:true, text:(target.innerText||'').trim()};
})()""")
    time.sleep(1.5)
    return r


def check(who, name_part, on=True):
    return U.js(who, r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const tr = rows.find(r => (r.innerText||'').includes(%s));
  if (!tr) return {found:false};
  const cb = tr.querySelector('button[role=checkbox]');
  if (!cb) return {found:true, checkbox:false};
  const now = cb.getAttribute('aria-checked') === 'true';
  if (now !== %s) { cb.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
    cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); cb.click(); }
  return {found:true, before: now, after: cb.getAttribute('aria-checked')};
})()""" % (json.dumps(name_part), "true" if on else "false"))


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    ev = {"case_id": "X14", "part": "bulk payload vs selection", "at_utc": C.utc(),
          "authority": cr["base_url"]}
    U.init(nonce)
    who = "x14b"

    # 全部自有 pending 节点恢复 online（保证"设为维护"可用）
    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])
    restored = []
    for n in nodes:
        if n.get("status") in ("maintenance", "disabled") and str(n.get("hostname", "")).startswith(f"qa-{nonce}"):
            r = hub.post(f"/api/nodes/{n['id']}", admin, body={"status": "online"})
            restored.append({"hostname": n["hostname"], "http": r["status"]})
    ev["pre_restore"] = restored
    time.sleep(1.5)

    U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    # 统一 pageSize=10
    U.js(who, r"""
(async () => {
  const t = [...document.querySelectorAll('button,[role=combobox]')].find(b => /^(10|20|50)$/.test((b.innerText||'').trim()));
  if (!t) return false; t.click(); await new Promise(r=>setTimeout(r,700));
  const o = [...document.querySelectorAll('[role=option]')].find(x => (x.innerText||'').trim() === '10');
  if (o) { o.click(); await new Promise(r=>setTimeout(r,1200)); }
  return true; })()""")
    time.sleep(1.0)
    p1 = U.js(who, ROWS_JS)
    ev["page1"] = p1
    n1, n2 = p1["rows"][0]["name"], p1["rows"][1]["name"]
    ev["sel_p1"] = [check(who, n1), check(who, n2)]
    js_click(who, "^下一页$")
    p2 = U.js(who, ROWS_JS)
    ev["page2"] = p2
    n3 = p2["rows"][0]["name"]
    ev["sel_p2"] = [check(who, n3)]
    ev["bar_after_cross_page_selection"] = U.js(who, ROWS_JS)["bulk_buttons"]
    js_click(who, "^上一页$")
    ev["back_on_page1"] = U.js(who, ROWS_JS)

    # --- A. 无过滤：设为维护 → 抓 payload ---
    U.clear_rec(who)
    ev["A_open"] = js_click(who, "^设为维护")
    ev["A_dialog"] = U.js(who, r"""(() => { const d = document.querySelector('[role=dialog],[role=alertdialog]');
      return {present: !!d, text: d ? (d.innerText||'').replace(/\n/g,' | ').slice(0,200) : null}; })()""")
    ev["A_confirm"] = confirm(who)
    time.sleep(2.5)
    ev["A_requests"] = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]
    ev["A_state_after"] = U.js(who, ROWS_JS)

    # 恢复
    U.clear_rec(who)
    ev["A_resume_open"] = js_click(who, "^恢复")
    ev["A_resume_confirm"] = confirm(who)
    time.sleep(2.5)
    ev["A_resume_requests"] = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]

    # --- B. 过滤到 1 个后执行 ---
    U.open_(who, "/nodes")
    U.install_request_recorder(who)
    p1b = U.js(who, ROWS_JS)
    ev["B_page1"] = p1b
    b1, b2 = p1b["rows"][0]["name"], p1b["rows"][1]["name"]
    ev["B_sel"] = [check(who, b1), check(who, b2)]
    key = b2[-6:]
    U.js(who, r"""
(async () => { const i = document.querySelector('input[placeholder]'); if (!i) return false;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i, %s);
  i.dispatchEvent(new Event('input',{bubbles:true})); await new Promise(r=>setTimeout(r,1200)); return true; })()""" % json.dumps(key))
    time.sleep(1.2)
    ev["B_filtered_view"] = U.js(who, ROWS_JS)
    U.clear_rec(who)
    ev["B_open"] = js_click(who, "^设为维护")
    ev["B_dialog"] = U.js(who, r"""(() => { const d = document.querySelector('[role=dialog],[role=alertdialog]');
      return {present: !!d, text: d ? (d.innerText||'').replace(/\n/g,' | ').slice(0,200) : null}; })()""")
    ev["B_confirm"] = confirm(who)
    time.sleep(2.5)
    ev["B_requests"] = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]
    ev["B_state_after"] = U.js(who, ROWS_JS)
    # 清理：清过滤 + 恢复
    U.js(who, r"""(() => { const i = document.querySelector('input[placeholder]');
      if (i) { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(i, '');
      i.dispatchEvent(new Event('input',{bubbles:true})); } return true; })()""")
    time.sleep(1.2)
    U.clear_rec(who)
    js_click(who, "^恢复")
    confirm(who)
    time.sleep(2.0)
    ev["cleanup_resume_requests"] = [r for r in U.rec(who) if r.get("method") in ("POST", "PUT") and "/api/nodes" in (r.get("url") or "")]

    by_name = {n["hostname"]: n["id"] for n in C.as_list(hub.get("/api/nodes", admin)["json"])}
    ev["node_ids"] = {k: by_name.get(k) for k in (n1, n2, n3, b1, b2)}
    ev["verdict"] = {
        "A_selection": [n1, n2, n3],
        "A_payload_ids": [r["url"].rsplit("/", 1)[-1] for r in ev["A_requests"]],
        "A_payload_matches_selection": sorted(r["url"].rsplit("/", 1)[-1] for r in ev["A_requests"])
                                     == sorted(by_name.get(k) for k in (n1, n2, n3)),
        "A_resume_payload_ids": [r["url"].rsplit("/", 1)[-1] for r in ev["A_resume_requests"]],
        "B_selection": [b1, b2],
        "B_payload_ids": [r["url"].rsplit("/", 1)[-1] for r in ev["B_requests"]],
        "B_payload_size": len(ev["B_requests"]),
    }
    C.write_evidence("X14-bulk-payload.json", ev)
    print(json.dumps({"A": {"dialog": ev["A_dialog"], "confirm": ev["A_confirm"],
                            "payload_ids": ev["verdict"]["A_payload_ids"],
                            "selection": ev["verdict"]["A_selection"],
                            "matches": ev["verdict"]["A_payload_matches_selection"]},
                      "B": {"filtered_view": [(r["name"][-8:], r["checked"]) for r in ev["B_filtered_view"]["rows"]],
                            "bar": ev["B_filtered_view"]["bulk_buttons"],
                            "dialog": ev["B_dialog"], "confirm": ev["B_confirm"],
                            "payload_ids": ev["verdict"]["B_payload_ids"],
                            "selection": ev["verdict"]["B_selection"]},
                      "node_ids": ev["node_ids"],
                      "restore_requests": len(ev["cleanup_resume_requests"])},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
