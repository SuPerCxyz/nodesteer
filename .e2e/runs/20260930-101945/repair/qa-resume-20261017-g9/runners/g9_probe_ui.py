"""G9 侦察：/nodes 列表页的分页/排序/批量栏/pageSize 控件结构（只读，本地不写）。

输出用于确定 G9 断言的稳定选择器；不修改任何数据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402

PROBE = r"""
(() => {
  const vis = el => el && el.offsetParent !== null;
  const btns = [...document.querySelectorAll('button')].filter(vis)
      .map(b => ({t: (b.innerText||'').trim().slice(0,24), aria: b.getAttribute('aria-label'),
                  title: b.title, role: b.getAttribute('role'),
                  checked: b.getAttribute('aria-checked')}));
  const headers = [...document.querySelectorAll('th')].map(t => ({t:(t.innerText||'').trim().slice(0,20),
      cls: t.className.slice(0,60), aria_sort: t.getAttribute('aria-sort')}));
  const rows = [...document.querySelectorAll('table tbody tr')];
  const sels = [...document.querySelectorAll('select')].map(s => ({options:[...s.options].map(o=>o.textContent)}));
  const inputs = [...document.querySelectorAll('input')].filter(vis).map(i => ({type:i.type, ph:i.placeholder, name:i.name}));
  const page_label = ((document.body.innerText||'').match(/第\s*\d+\s*页[^\n]{0,20}/)||[])[0] || null;
  const total_like = ((document.body.innerText||'').match(/共\s*\d+\s*[条个项]/g)||[]).slice(0,4);
  return {url: location.pathname+location.search, rows: rows.length, headers, buttons: btns.slice(0,40),
          selects: sels, inputs: inputs.slice(0,12), page_label, total_like,
          text_tail: (document.body.innerText||'').slice(-500)};
})()
"""


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    U.init()
    who = "probe"
    lg = U.login(who, cr["admin_username"], cr["admin_password"])
    out = {"nonce": nonce, "at_utc": C.utc(), "login": lg}
    U.open_(who, "/nodes")
    out["nodes_page"] = U.js(who, PROBE)
    U.shot(who, "g9", "G9-probe-nodes-page.png")

    # 勾选第一行，观察批量栏
    sel = U.js(who, r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  if (!rows.length) return {rows:0};
  const cb = rows[0].querySelector('button[role=checkbox]');
  if (!cb) return {rows:rows.length, checkbox:false};
  cb.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  cb.click();
  return {rows:rows.length, checkbox:true, after:cb.getAttribute('aria-checked')};
})()
""")
    out["select_first_row"] = sel
    import time as _t
    _t.sleep(1.2)
    out["after_select_probe"] = U.js(who, PROBE)
    out["after_select_text"] = U.body_text(who)[:1200]
    U.shot(who, "g9", "G9-probe-after-select.png")

    # 分页控件文本
    out["pagination_zone"] = U.js(who, r"""
(() => {
  const cands = [...document.querySelectorAll('button,a,[role=button]')].filter(b=>b.offsetParent!==null);
  const pag = cands.filter(b => /下一页|上一页|最后一页|第一页|页/.test((b.innerText||'')+(b.getAttribute('aria-label')||'')+(b.title||'')));
  return {count: pag.length,
          items: pag.map(b=>({t:(b.innerText||'').trim(), aria:b.getAttribute('aria-label'), title:b.title, dis:!!b.disabled})).slice(0,15),
          label: ((document.body.innerText||'').match(/第\s*\d+\s*页[^\n]{0,24}/)||[])[0] || null};
})()
""")
    C.write_evidence("g9", "G9-ui-probe.json", out)
    print(json.dumps({k: out[k] for k in ("login", "select_first_row", "pagination_zone")},
                     ensure_ascii=False, indent=1)[:2500])
    print("PAGE_LABEL:", json.dumps(out["nodes_page"].get("page_label"), ensure_ascii=False),
          "ROWS:", out["nodes_page"].get("rows"),
          "SELECTS:", json.dumps(out["nodes_page"].get("selects"), ensure_ascii=False)[:200],
          "TOTAL:", json.dumps(out["nodes_page"].get("total_like"), ensure_ascii=False)[:200])
    print("BUTTONS:", json.dumps(out["nodes_page"].get("buttons"), ensure_ascii=False)[:1200])
    print("HEADERS:", json.dumps(out["nodes_page"].get("headers"), ensure_ascii=False)[:600])
    print("AFTER_SELECT_BUTTONS:", json.dumps(out["after_select_probe"].get("buttons"), ensure_ascii=False)[:1200])
    print("AFTER_SELECT_TEXT:", out["after_select_text"][:500].replace("\n", " | "))


if __name__ == "__main__":
    main()
