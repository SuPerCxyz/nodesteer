"""G9 侦察 2：选中行后的批量栏/操作按钮全量 dump（只读选择，不触发任何批量动作）。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402

DUMP = r"""
(() => {
  const vis = el => el && el.offsetParent !== null;
  const btns = [...document.querySelectorAll('button')].filter(vis)
      .map((b, i) => ({i, t: (b.innerText||'').trim().slice(0,30), aria: b.getAttribute('aria-label'),
                       role: b.getAttribute('role'), checked: b.getAttribute('aria-checked'),
                       cls: (b.className||'').slice(0,50)}));
  const sel_text = [...document.querySelectorAll('*')].filter(e => vis(e) && e.children.length === 0)
      .map(e => (e.innerText||'').trim()).filter(t => /已选择|批量|维护|恢复|删除|升级|在线/.test(t)).slice(0, 20);
  const rows_checked = [...document.querySelectorAll('table tbody tr')]
      .map(tr => ({cb: (tr.querySelector('button[role=checkbox]')||{}).getAttribute
                      ? tr.querySelector('button[role=checkbox]').getAttribute('aria-checked') : null}));
  return {buttons: btns, sel_text, rows_checked,
          header_cb: (document.querySelector('button[aria-label="全选所有行"]')||{}).getAttribute
              ? document.querySelector('button[aria-label="全选所有行"]').getAttribute('aria-checked') : null};
})()
"""


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    U.init()
    who = "probe2"
    U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, "/nodes")
    out = {"nonce": nonce, "at_utc": C.utc()}
    # 勾选前 2 行
    out["select"] = U.js(who, r"""
(() => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const res = [];
  for (const i of [0, 1]) {
    const cb = rows[i] && rows[i].querySelector('button[role=checkbox]');
    if (!cb) { res.push({i, found:false}); continue; }
    cb.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
    cb.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
    cb.click();
    res.push({i, found:true, after: cb.getAttribute('aria-checked')});
  }
  return res;
})()
""")
    time.sleep(1.5)
    out["dump"] = U.js(who, DUMP)
    out["body_text_head"] = U.body_text(who)[:900]
    C.write_evidence("g9", "G9-ui-probe2.json", out)
    print("SEL_TEXT:", json.dumps(out["dump"]["sel_text"], ensure_ascii=False))
    print("HEADER_CB:", out["dump"]["header_cb"], "ROWS_CHECKED:", json.dumps(out["dump"]["rows_checked"]))
    print("BUTTONS with bulk-ish text:")
    for b in out["dump"]["buttons"]:
        if any(k in (b["t"] or "") + (b["aria"] or "") for k in ("维护", "恢复", "删除", "升级", "选择", "批量", "在线")):
            print("  ", json.dumps(b, ensure_ascii=False))


if __name__ == "__main__":
    main()
