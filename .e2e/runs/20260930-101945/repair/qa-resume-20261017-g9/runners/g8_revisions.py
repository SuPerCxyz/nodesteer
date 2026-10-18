"""G8/X16：Revision 历史遮蔽复核 —— API 明细结构 + UI 版本历史逐项点击。

断言面：revisions API 返回体是否含 secret 默认值明文；UI 点击每个 rN 后页面是否出现标记原文。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    C.register_redaction(cr["admin_password"], "<ADMIN_PW>")
    C.register_redaction(cr["registration_token"], "<REG_TOKEN>")
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    sid = cr["script_id"]
    marks = cr["markers"]
    ev = {"case_id": "X16", "probe": "revision-history-masking", "nonce": nonce,
          "at_utc": C.utc(), "script_id": sid}

    r = hub.get(f"/api/scripts/{sid}/revisions", admin)
    body = (r["body_raw"] or b"").decode(errors="replace")
    revs = C.as_list(r["json"])
    ev["api"] = {
        "http": r["status"], "bytes": len(body), "count": len(revs),
        "row_keys": sorted(revs[0].keys()) if revs and isinstance(revs[0], dict) else None,
        "contains_script_default": marks["script_default"] in body,
        "contains_revision_marker": marks["revision"] in body,
        "contains_error_marker": marks["error"] in body,
        "has_parameters_field": "parameters" in body,
    }
    if revs and isinstance(revs[0], dict):
        ev["api"]["first_row_preview"] = {k: (str(v)[:40] if not isinstance(v, (dict, list)) else type(v).__name__)
                                          for k, v in revs[0].items()}
        # 若有单 revision 明细接口
        rid = revs[0].get("id") or revs[0].get("revision")
        if rid is not None:
            d = hub.get(f"/api/scripts/{sid}/revisions/{rid}", admin)
            dbody = (d["body_raw"] or b"").decode(errors="replace")
            ev["api"]["detail_http"] = d["status"]
            ev["api"]["detail_bytes"] = len(dbody)
            ev["api"]["detail_contains_script_default"] = marks["script_default"] in dbody
            ev["api"]["detail_contains_revision_marker"] = marks["revision"] in dbody

    # UI：逐项点击版本历史
    U.init()
    who = "g8v"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, f"/scripts/{sid}", wait=2.6)
    ev["ui_rows"] = U.js(who, r"""
(() => {
  const els = [...document.querySelectorAll('*')].filter(e => e.offsetParent !== null);
  const hist = els.filter(e => /^r\d+[0-9a-f]{6,}/.test((e.innerText||'').trim()))
      .map(e => ({tag: e.tagName, text: (e.innerText||'').trim().slice(0, 30)}));
  return hist.slice(0, 8);
})()""")
    clicks = []
    for i in range(min(4, len(ev["ui_rows"]))):
        res = U.js(who, r"""
(async () => {
  const els = [...document.querySelectorAll('*')].filter(e => e.offsetParent !== null);
  const items = els.filter(e => /^r\d+[0-9a-f]{6,}/.test((e.innerText||'').trim()));
  const t = items[%d];
  if (!t) return {found:false};
  const clickable = t.closest('button,a,[role=button]') || t;
  clickable.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  clickable.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  clickable.click();
  await new Promise(r => setTimeout(r, 1400));
  const body = document.body.innerText || '';
  return {found:true, label: (t.innerText||'').trim().slice(0,24),
          marker_in_text: body.includes(%s) || body.includes(%s),
          text_len: body.length};
})()""" % (i, json.dumps(marks["script_default"]), json.dumps(marks["revision"])))
        clicks.append(res)
        time.sleep(0.6)
    ev["ui_clicks"] = clicks
    ev["ui_marker_anywhere"] = U.js(who, r"""(() => { const t = document.body.innerText || '';
      return {script_default: t.includes(%s), revision: t.includes(%s)}; })()"""
                                     % (json.dumps(marks["script_default"]), json.dumps(marks["revision"])))
    ev["shot"] = U.shot(who, "g8", "G8-script-revision-history.png")
    C.write_evidence("g8", "G8-revision-history-masking.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:2600])


if __name__ == "__main__":
    main()
