"""X13 专项：另一标签"退出登录"/"替换 Token"后受保护页面的行为与实际收敛时间。

用 agent-browser 稳定 tab id（tN）在**同一浏览器上下文**内真实模拟多标签，
覆盖：共享 localStorage、内存会话与 localStorage 的差异、被动收敛时间、刷新收敛时间。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402


def tab_list(who):
    out = U.run(who, "tab", "list")[0]
    ids = re.findall(r"\[t(\d+)\]", out)
    return {"lines": [l.strip() for l in out.strip().splitlines() if l.strip()],
            "ids": [f"t{i}" for i in ids]}


def switch(who, tab):
    U.run(who, "tab", tab)
    time.sleep(0.7)
    return {"tab": tab, "mark": U.js(who, "window.__mark || null"),
            "path": U.js(who, "location.pathname")}


def probe_tab(who, mark):
    return U.js(who, r"""
(async () => {
  window.__mark = %s;
  const stash = window.__tokA || null;
  const ls = localStorage.getItem('nodesteer_token');
  let stash_status = null, ls_status = null;
  if (stash) { const r = await fetch('/api/me', {headers:{Authorization:'Bearer '+stash}}); stash_status = Number(r.status); }
  if (ls) { const r2 = await fetch('/api/me', {headers:{Authorization:'Bearer '+ls}}); ls_status = Number(r2.status); }
  return {mark: window.__mark, path: location.pathname, has_ls_token: !!ls,
          stash_present: !!stash, stash_len: stash ? stash.length : null,
          stash_token_me: stash_status, ls_token_me: ls_status,
          head: (document.body.innerText||'').slice(0,60).replace(/\n/g,' ')};
})()""" % json.dumps(mark))


def poll_passive(who, timeout=15.0, interval=1.0):
    t0 = time.time()
    trace = []
    while time.time() - t0 < timeout:
        st = U.js(who, r"""(() => ({path: location.pathname + location.search,
            has_ls: !!localStorage.getItem('nodesteer_token')}))()""")
        trace.append({"ms": int((time.time() - t0) * 1000), "path": st.get("path"),
                      "has_ls": st.get("has_ls")})
        if str(st.get("path", "")).startswith("/sign-in"):
            return round(time.time() - t0, 3), trace
        time.sleep(interval)
    return None, trace


def ui_logout(who):
    return U.js(who, r"""
(async () => {
  const btns = [...document.querySelectorAll('button,[role=button]')];
  const trigger = btns.find(b => /操作员|查看者|管理员|admin/.test(b.innerText||''));
  if (!trigger) return {stage:'trigger'};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 900));
  const items = [...document.querySelectorAll('[role=menuitem]')];
  const logout = items.find(e => /退出登录|Logout/i.test(e.innerText||''));
  if (!logout) return {stage:'menu', items: items.map(e=>(e.innerText||'').trim())};
  logout.click();
  await new Promise(r => setTimeout(r, 900));
  const confirm = [...document.querySelectorAll('button')].find(b => /^退出登录$/.test((b.innerText||'').trim()));
  if (!confirm) return {stage:'dialog'};
  confirm.click();
  await new Promise(r => setTimeout(r, 1500));
  return {stage:'done', path: location.pathname, token_cleared: !localStorage.getItem('nodesteer_token')};
})()""")


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    ev = {"case_id": "X13", "part": "other-tab logout / token replacement",
          "at_utc": C.utc(), "authority": cr["base_url"]}
    U.init(nonce)
    who = "x13t"

    # 清理：只保留一个 tab
    U.run(who, "open", cr["base_url"] + "/sign-in")
    time.sleep(1.5)
    tl = tab_list(who)
    for t in tl["ids"][1:]:
        U.run(who, "tab", t)
        U.run(who, "tab", "close")
    first = tab_list(who)["ids"]
    tab1 = first[0] if first else "t1"
    switch(who, tab1)
    U.js(who, "localStorage.clear()")
    U.reload(who, wait=2.0)
    lg = U.login(who, cr["operator_username"], cr["operator_password"])
    U.open_(who, "/tasks")
    U.js(who, "(() => { window.__tokA = localStorage.getItem('nodesteer_token'); window.__mark='T1'; return true; })()")
    ev["tab1_initial"] = probe_tab(who, "T1")
    ev["tab1_login"] = {"token_in_localStorage": lg["token_in_localStorage"], "url": lg["url"]}

    # 新标签（同一上下文，共享 localStorage）
    U.run(who, "tab", "new", cr["base_url"] + "/tasks")
    time.sleep(1.8)
    ev["tabs_after_new"] = tab_list(who)
    tab2 = ev["tabs_after_new"]["ids"][-1]
    ev["tab2_initial"] = probe_tab(who, "T2")

    # ---------- A. 另一标签退出登录 ----------
    t0 = time.time()
    ev["tab2_logout"] = ui_logout(who)
    ev["tab2_after_logout"] = probe_tab(who, "T2")
    ev["tabs_after_logout"] = tab_list(who)
    switch(who, tab1)
    ev["tab1_probe_after_logout"] = probe_tab(who, "T1")
    conv_logout, trace_logout = poll_passive(who, timeout=15.0)
    ev["logout_effect"] = {
        "other_tab_passive_redirect_seconds": conv_logout,
        "passive_trace": trace_logout[:5],
        "wall_clock_seconds": round(time.time() - t0, 3),
    }
    # 该标签自身的 SPA 内导航是否触发收敛
    nav = U.js(who, r"""
(async () => {
  const link = [...document.querySelectorAll('a')].find(a => /节点|Nodes/.test(a.innerText||''));
  if (link) { link.click(); await new Promise(r => setTimeout(r, 1500)); }
  return {path: location.pathname, head: (document.body.innerText||'').slice(0,60).replace(/\n/g,' ')};
})()""")
    ev["logout_effect"]["after_in_app_navigation"] = nav
    t_reload = time.time()
    U.reload(who, wait=2.2)
    ev["logout_effect"]["after_self_reload"] = {
        "seconds": round(time.time() - t_reload, 3),
        "path": U.js(who, "location.pathname"),
        "has_ls_token": U.js(who, "!!localStorage.getItem('nodesteer_token')")}

    # ---------- B. 另一标签替换 Token ----------
    switch(who, tab1)
    U.js(who, "localStorage.clear()")
    U.reload(who, wait=2.0)
    U.login(who, cr["operator_username"], cr["operator_password"])
    U.open_(who, "/tasks")
    U.js(who, "(() => { window.__tokA = localStorage.getItem('nodesteer_token'); window.__mark='T1b'; return true; })()")
    ev["tab1_before_replace"] = probe_tab(who, "T1b")
    switch(who, tab2)
    U.open_(who, "/tasks")
    U.js(who, "(() => { window.__mark='T2b'; return true; })()")
    t_repl = time.time()
    ev["tab2_replace"] = U.js(who, r"""
(() => { localStorage.setItem('nodesteer_token','qa-g1-forged-token-0000');
  return {set: 'forged'}; })()""")
    ev["tab2_after_replace"] = probe_tab(who, "T2b")
    switch(who, tab1)
    ev["tab1_probe_after_replace"] = probe_tab(who, "T1b")
    conv_repl, trace_repl = poll_passive(who, timeout=15.0)
    ev["replace_effect"] = {
        "other_tab_passive_redirect_seconds": conv_repl,
        "passive_trace": trace_repl[:5],
        "wall_clock_seconds": round(time.time() - t_repl, 3),
    }
    t_reload2 = time.time()
    U.reload(who, wait=2.2)
    ev["replace_effect"]["after_self_reload"] = {
        "seconds": round(time.time() - t_reload2, 3),
        "path": U.js(who, "location.pathname"),
        "has_ls_token": U.js(who, "!!localStorage.getItem('nodesteer_token')")}

    # 对照：显式 API 登出会撤销服务端会话
    api_login = hub.login(cr["operator_username"], cr["operator_password"])
    api_tok = (api_login["json"] or {}).get("token")
    api_logout = hub.post("/api/logout", api_tok, body={})
    ev["server_side_contrast"] = {"api_login": api_login["status"], "api_logout": api_logout["status"],
                                 "old_token_after_explicit_logout": hub.get("/api/me", api_tok)["status"]}

    ev["verdict"] = {
        "tabs_share_localstorage": ev["tab2_initial"].get("stash_present") is False
                                 and ev["tab2_initial"].get("has_ls_token") is True,
        "ui_logout_clears_localstorage_in_other_tab": ev["tab2_after_logout"].get("has_ls_token") is False,
        "ui_logout_does_not_revoke_server_session":
            ev["tab1_probe_after_logout"].get("stash_token_me") == 200,
        "other_tab_passive_redirect_after_logout_seconds": conv_logout,
        "other_tab_still_usable_after_logout": ev["tab1_probe_after_logout"].get("head", "").find("登录") < 0,
        "converges_on_self_reload_after_logout": str(ev["logout_effect"]["after_self_reload"]["path"]).startswith("/sign-in"),
        "forged_token_rejected_by_server": ev["tab2_after_replace"].get("ls_token_me") == 401,
        "other_tab_keeps_old_session_after_replace": ev["tab1_probe_after_replace"].get("stash_token_me") == 200,
        "other_tab_passive_redirect_after_replace_seconds": conv_repl,
        "converges_on_self_reload_after_replace": str(ev["replace_effect"]["after_self_reload"]["path"]).startswith("/sign-in"),
        "explicit_api_logout_revokes": ev["server_side_contrast"]["old_token_after_explicit_logout"] == 401,
    }
    C.write_evidence("X13-other-tab-logout-token-replacement.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
