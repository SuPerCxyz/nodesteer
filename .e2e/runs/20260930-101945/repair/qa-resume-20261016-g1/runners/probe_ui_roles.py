"""只读探针：以 operator/viewer/admin 会话逐页抓取可见动作控件与页面文本（不点击任何写控件）。

输出 evidence/UI-probe-<role>.json，供 A 批断言设计使用。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402

PAGES = ["/", "/nodes", "/applications", "/tasks", "/schedules", "/scripts", "/settings", "/users"]

BUTTON_JS = r"""
(() => {
  const btns = [...document.querySelectorAll('button')];
  const visible = btns.filter(b => b.offsetParent !== null);
  const inputs = [...document.querySelectorAll('input,textarea,select')].filter(i => i.offsetParent !== null);
  return {
    buttons: visible.map(b => ({text:(b.innerText||'').trim().slice(0,30), disabled: !!b.disabled,
                               aria:(b.getAttribute('aria-label')||'').slice(0,30)})),
    inputs: inputs.map(i => ({tag:i.tagName, type:i.type, disabled: !!i.disabled,
                              aria:(i.getAttribute('aria-label')||'').slice(0,40)})),
    rows: [...document.querySelectorAll('table tbody tr')].length,
  };
})()
"""


def probe(who: str, username: str, password: str) -> dict:
    out = {}
    lg = U.login(who, username, password)
    out["login"] = {"token": lg["token_in_localStorage"], "url": lg["url"]}
    for p in PAGES:
        U.open_(who, p)
        d = U.js(who, BUTTON_JS)
        body = U.body_text(who)
        d["url"] = U.url(who)
        d["admin_only_notice"] = "只有管理员可以管理用户。" in body
        d["text_head"] = body[-260:] if p != "/" else body[:400]
        out[p] = d
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    U.init(nonce)
    res = {
        "at_utc": C.utc(), "authority": cr["base_url"],
        "operator": probe("op", cr["operator_username"], cr["operator_password"]),
        "viewer": probe("vw", cr["viewer_username"], cr["viewer_password"]),
    }
    U.shot("op", "PROBE-operator-settings.png")
    C.write_evidence("UI-probe-roles.json", res)
    for role in ("operator", "viewer"):
        print("===", role, "===")
        for p, d in res[role].items():
            if p == "login":
                continue
            names = [b["text"] for b in d["buttons"] if b["text"]]
            print(f"  {p}: buttons={names}")
            print(f"     inputs={[(i['tag'], i['type'], i['disabled']) for i in d['inputs']]} rows={d['rows']}")


if __name__ == "__main__":
    main()
