"""B 批：X13 v3（跨页缓存、两会话编辑、旧会话改角色后 API 权限、另一标签退出/替换 Token 后保护页面与收敛时间）。

实现要点（v3）：所有依赖"会话 token"的判定都在页面内用 fetch 完成（避免把 token 取出到进程外），
API 侧使用 API 签发 token 作为"旧会话"（服务端会话语义一致）。
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


def poll_api_role(hub, token, want, timeout=10.0):
    t0 = time.time()
    trace = []
    while time.time() - t0 < timeout:
        r = hub.get("/api/me", token)
        role = (r["json"] or {}).get("Role") if isinstance(r["json"], dict) else None
        trace.append({"ms": int((time.time() - t0) * 1000), "http": r["status"], "role": role})
        if role == want:
            return round(time.time() - t0, 3), role, trace
        time.sleep(0.2)
    return None, (trace[-1]["role"] if trace else None), trace


def page_state(who):
    return U.js(who, r"""
(() => { const b = document.body.innerText || '';
  return {path: location.pathname + location.search,
          has_token: !!localStorage.getItem('nodesteer_token'),
          signin_like: /登录|Sign in/.test(b.slice(0,300)),
          head: b.slice(0,80).replace(/\n/g,' ')}; })()""")


def poll_page(who, timeout=15.0, interval=1.0):
    t0 = time.time()
    trace = []
    while time.time() - t0 < timeout:
        st = page_state(who)
        trace.append({"ms": int((time.time() - t0) * 1000), "path": st.get("path"),
                      "has_token": st.get("has_token")})
        if str(st.get("path", "")).startswith("/sign-in"):
            return round(time.time() - t0, 3), trace
        time.sleep(interval)
    return None, trace


def tab_indices(who):
    out = U.run(who, "tab", "list")[0]
    lines = [l for l in out.strip().splitlines() if l.strip()]
    idxs = []
    active = None
    for l in lines:
        m = re.search(r"\[t(\d+)\]", l)
        if m:
            idxs.append(int(m.group(1)))
            if "→" in l:
                active = int(m.group(1))
    return {"tab_count": len(idxs), "indices": idxs, "active": active, "list": lines[:8]}


def switch_tab(who, idx):
    U.run(who, "tab", str(idx))
    time.sleep(0.9)
    return {"switched_to": idx, "state": page_state(who)}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    aid = fx["fixtures"]["application"]["id"]
    app_row = hub.get(f"/api/applications/{aid}", admin)["json"]
    app_name = app_row.get("name")
    ev = {"case_id": "X13", "at_utc": C.utc(), "authority": cr["base_url"],
          "fixture_app": {"id": aid, "name": app_name, "version": app_row.get("version")},
          "method": "in-page fetch for session-token checks; API-issued tokens for API-level checks"}
    U.init(nonce)

    op_user = next(u for u in C.as_list(hub.get("/api/users", admin)["json"])
                   if u["username"] == cr["operator_username"])
    op_token = hub.token(cr["operator_username"], cr["operator_password"])

    # 浏览器会话（UI）
    U.open_("x13op", "/sign-in", wait=1.8)
    U.js("x13op", "localStorage.clear()")
    U.reload("x13op", wait=1.8)
    op_login = U.login("x13op", cr["operator_username"], cr["operator_password"])
    U.open_("x13adm", "/sign-in", wait=1.8)
    U.js("x13adm", "localStorage.clear()")
    U.reload("x13adm", wait=1.8)
    adm_login = U.login("x13adm", cr["admin_username"], cr["admin_password"])
    ev["sessions"] = {
        "ui_operator_login_ok": op_login["token_in_localStorage"],
        "ui_admin_login_ok": adm_login["token_in_localStorage"],
        "api_token_operator_role": (hub.get("/api/me", op_token)["json"] or {}).get("Role"),
        "ui_operator_inpage_me_status": U.js("x13op", r"""
            (async () => { const t = localStorage.getItem('nodesteer_token');
              const r = await fetch('/api/me', {headers:{Authorization:'Bearer '+t}});
              return Number(r.status); })()"""),
        "ui_admin_inpage_me_status": U.js("x13adm", r"""
            (async () => { const t = localStorage.getItem('nodesteer_token');
              const r = await fetch('/api/me', {headers:{Authorization:'Bearer '+t}});
              return Number(r.status); })()"""),
    }

    # ---------- 1. 两会话编辑 + 跨页缓存 ----------
    names_js = r"""
(() => { const rows=[...document.querySelectorAll('table tbody tr')];
  return {path: location.pathname, names: rows.map(r => (r.innerText||'').split('\n')[0].trim())}; })()"""
    U.open_("x13op", "/applications")
    before = U.js("x13op", names_js)
    new_name = f"qa-{nonce}-app-v2"
    put = hub.put(f"/api/applications/{aid}", admin, body={"id": aid, "name": new_name, "version": "2.0.0"})
    time.sleep(3.0)
    still_open = U.js("x13op", names_js)
    U.open_("x13op", "/tasks")
    U.open_("x13op", "/applications")
    after_nav = U.js("x13op", names_js)
    ev["cross_page_cache"] = {
        "app_name_before": app_name, "written_by_other_session": new_name,
        "definition_put_http": put["status"],
        "open_page_after_3s_no_navigation": still_open,
        "after_leave_and_return": after_nav,
        "open_page_shows_new": new_name in (still_open.get("names") or []),
        "after_nav_shows_new": new_name in (after_nav.get("names") or []),
    }
    hub.put(f"/api/applications/{aid}", admin, body={"id": aid, "name": app_name, "version": "1.0.0"})

    # ---------- 2. 旧会话改角色（API 路径：权限收敛） ----------
    b_before = hub.get("/api/applications", op_token)["status"]
    t_api = time.time()
    api_change = hub.put(f"/api/users/{op_user['id']}", admin, body={"role": "viewer"})
    conv_api, role_api, trace_api = poll_api_role(hub, op_token, "viewer", timeout=8)
    restricted_op = hub.put(f"/api/applications/{aid}", op_token, body={"id": aid, "name": "x13-denied"})
    read_op = hub.get("/api/applications", op_token)
    write_denied_name = hub.get(f"/api/applications/{aid}", admin)["json"].get("name")
    ev["old_session_role_change_api_path"] = {
        "pre_change_read_http": b_before, "change_http": api_change["status"],
        "convergence_seconds_to_viewer": conv_api, "role_observed": role_api,
        "poll_trace": trace_api[:6],
        "restricted_write_http_after": restricted_op["status"],
        "read_http_after": read_op["status"],
        "definition_unchanged_after_denied_write": write_denied_name == app_name,
        "wall_clock_seconds": round(time.time() - t_api, 3),
    }

    # ---------- 3. 旧会话改角色（UI 路径：管理员在 Users 页改回操作员） ----------
    U.open_("x13adm", "/users")
    t_ui = time.time()
    ui_change = U.js("x13adm", r"""
(async () => {
  const rows = [...document.querySelectorAll('table tbody tr')];
  const row = rows.find(r => (r.innerText||'').includes(%s));
  if (!row) return {ok:false};
  row.querySelector('button').click();
  await new Promise(r => setTimeout(r, 600));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').trim() === '操作员');
  if (!opt) return {ok:false, options: [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim())};
  opt.click();
  return {ok:true, picked:'操作员'};
})()""" % json.dumps(cr["operator_username"]))
    conv_ui, role_ui, trace_ui = poll_api_role(hub, op_token, "operator", timeout=8)
    ev["old_session_role_change_ui_path"] = {
        "ui_change": ui_change, "convergence_seconds_back_to_operator": conv_ui,
        "role_observed": role_ui, "poll_trace": trace_ui[:6],
        "read_http_after_restore": hub.get("/api/applications", op_token)["status"],
        "wall_clock_seconds": round(time.time() - t_ui, 3),
    }

    # ---------- 4. 另一标签退出登录 ----------
    who = "x13op"
    U.open_(who, "/tasks")
    U.js(who, r"""(() => { window.__x13tok = localStorage.getItem('nodesteer_token');
      return {stashed: !!window.__x13tok}; })()""")
    U.run(who, "tab", "new", cr["base_url"] + "/tasks")
    ev["tabs_before"] = tab_indices(who)
    ev["tabs"] = switch_tab(who, ev["tabs_before"]["indices"][-1])
    # tab2：真实 UI 退出
    logout_flow = U.js(who, r"""
(async () => {
  const btns = [...document.querySelectorAll('button,[role=button]')];
  const trigger = btns.find(b => /admin|操作员|查看者|管理员/.test(b.innerText||''));
  if (!trigger) return {stage:'trigger'};
  trigger.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trigger.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trigger.click();
  await new Promise(r => setTimeout(r, 1000));
  const items = [...document.querySelectorAll('[role=menuitem]')];
  const logout = items.find(e => /退出登录|Logout/i.test(e.innerText||''));
  if (!logout) return {stage:'menu', items: items.map(e=>(e.innerText||'').trim())};
  logout.click();
  await new Promise(r => setTimeout(r, 900));
  const confirm = [...document.querySelectorAll('button')].find(b => /^退出登录$/.test((b.innerText||'').trim()));
  if (!confirm) return {stage:'dialog', buttons: [...document.querySelectorAll('button')].map(b=>(b.innerText||'').trim()).slice(0,20)};
  confirm.click();
  await new Promise(r => setTimeout(r, 1500));
  return {stage:'done', path: location.pathname, token_cleared: !localStorage.getItem('nodesteer_token')};
})()""")
    # tab2 内页面级判定
    tab2_after = U.js(who, r"""
(async () => { const t = localStorage.getItem('nodesteer_token');
  return {has_token: !!t, path: location.pathname, body: (document.body.innerText||'').slice(0,60).replace(/\n/g,' ')}; })()""")
    ev["tabs_after_logout_on_tab2"] = tab_indices(who)
    switch_tab(who, ev["tabs_before"]["indices"][0])
    time.sleep(0.6)
    # tab1：用自身内存/暂存 token 直连服务端（页面内 fetch）
    tab1_probe = U.js(who, r"""
(async () => {
  const t = window.__x13tok || localStorage.getItem('nodesteer_token');
  const r = await fetch('/api/me', {headers:{Authorization:'Bearer '+t}});
  return {stashed_token_len: t ? t.length : null, me_status: r.status,
          path: location.pathname, has_token_localStorage: !!localStorage.getItem('nodesteer_token'),
          head: (document.body.innerText||'').slice(0,60).replace(/\n/g,' ')};
})()""")
    conv_page, trace_page = poll_page(who, timeout=15.0, interval=1.0)
    ev["logout_in_other_tab"] = {
        "logout_flow": logout_flow, "tab2_after_logout": tab2_after,
        "tab1_probe_after_other_tab_logout": tab1_probe,
        "protected_page_convergence_seconds_passive": conv_page,
        "page_trace_passive": trace_page[:6],
    }
    t_reload = time.time()
    U.reload(who, wait=2.2)
    conv_reload, trace_reload = poll_page(who, timeout=10.0, interval=1.0)
    ev["logout_in_other_tab"]["after_self_reload"] = {
        "convergence_seconds": conv_reload, "state": page_state(who),
        "trace": trace_reload[:4], "wall_clock": round(time.time() - t_reload, 3)}
    # 对照：显式 API 登出会真实撤销会话
    api_login = hub.login(cr["operator_username"], cr["operator_password"])
    api_tok = (api_login["json"] or {}).get("token")
    api_logout = hub.post("/api/logout", api_tok, body={})
    ev["server_side_logout_contrast"] = {
        "api_login_http": api_login["status"], "api_logout_http": api_logout["status"],
        "old_api_token_after_explicit_logout": hub.get("/api/me", api_tok)["status"],
        "ui_logout_tab1_token_status": tab1_probe["me_status"],
    }

    # ---------- 5. 另一标签替换 Token ----------
    U.open_(who, "/sign-in", wait=1.8)
    U.js(who, "localStorage.clear()")
    U.reload(who, wait=1.8)
    U.login(who, cr["operator_username"], cr["operator_password"])
    U.open_(who, "/tasks")
    U.js(who, r"""(() => { window.__x13tok2 = localStorage.getItem('nodesteer_token');
      return {stashed: !!window.__x13tok2}; })()""")
    U.run(who, "tab", "new", cr["base_url"] + "/tasks")
    t_idx = tab_indices(who)
    switch_tab(who, t_idx["indices"][-1])
    tab2_replace = U.js(who, r"""
(() => { localStorage.setItem('nodesteer_token', 'qa-g1-forged-token-0000');
  return {set: localStorage.getItem('nodesteer_token') ? 'forged' : 'none'}; })()""")
    switch_tab(who, t_idx["indices"][0])
    time.sleep(0.6)
    conv_repl, trace_repl = poll_page(who, timeout=15.0, interval=1.0)
    t_refetch = time.time()
    tab1_probe2 = U.js(who, r"""
(async () => { const t = window.__x13tok2;
  const r = await fetch('/api/me', {headers:{Authorization:'Bearer '+t}});
  const rf = await fetch('/api/me', {headers:{Authorization:'Bearer qa-g1-forged-token-0000'}});
  return {stashed_token_me_status: r.status, forged_token_me_status: rf.status,
          path: location.pathname, has_token_localStorage: !!localStorage.getItem('nodesteer_token')}; })()""")
    ev["token_replaced_in_other_tab"] = {
        "tab2_set": tab2_replace,
        "protected_page_convergence_seconds_passive": conv_repl,
        "page_trace_passive": trace_repl[:6],
        "tab1_probe": tab1_probe2,
        "wall_clock_probe": round(time.time() - t_refetch, 3),
    }
    t_reload2 = time.time()
    U.reload(who, wait=2.2)
    conv_repl2, trace_repl2 = poll_page(who, timeout=10.0, interval=1.0)
    ev["token_replaced_in_other_tab"]["after_self_reload"] = {
        "convergence_seconds": conv_repl2, "state": page_state(who),
        "trace": trace_repl2[:4], "wall_clock": round(time.time() - t_reload2, 3)}
    U.run(who, "tab", "close", "1")

    ev["verdict"] = {
        "fresh_sessions_verified": int(ev["sessions"]["ui_operator_inpage_me_status"]) == 200
                                  and int(ev["sessions"]["ui_admin_inpage_me_status"]) == 200,
        "cross_page_cache_no_cross_session_push": not ev["cross_page_cache"]["open_page_shows_new"],
        "cross_page_cache_refetch_on_navigation": ev["cross_page_cache"]["after_nav_shows_new"],
        "old_token_downgrade_converges_seconds": conv_api,
        "old_token_restricted_403_after_downgrade": restricted_op["status"] == 403,
        "old_token_read_still_200": read_op["status"] == 200,
        "denied_write_left_definition_unchanged": ev["old_session_role_change_api_path"]["definition_unchanged_after_denied_write"],
        "old_token_recovers_after_ui_restore": role_ui == "operator",
        "ui_logout_clears_local_token_only":
            (logout_flow.get("token_cleared") is True or tab2_after.get("has_token") is False)
            and tab1_probe["me_status"] == 200,
        "explicit_api_logout_revokes_session": ev["server_side_logout_contrast"]["old_api_token_after_explicit_logout"] == 401,
        "other_tab_page_passive_redirect_after_logout": conv_page is not None,
        "converges_on_self_reload_after_logout": conv_reload is not None,
        "other_tab_page_passive_redirect_after_token_replace": conv_repl is not None,
        "converges_on_self_reload_after_token_replace": conv_repl2 is not None,
    }
    ev["observations"] = [
        {"id": "OBS-UI-LOGOUT-NO-SERVER-REVOKE", "severity": "low-medium", "case": "X13",
         "detail": ("Web UI 退出登录只做本地清理（auth.reset() + 跳 /sign-in），未调用 POST /api/logout；"
                    "因此服务端会话在 TTL 内仍然有效，另一标签页的受保护页面在%s秒被动窗口内不跳转。"
                    "对照：显式 /api/logout 后旧 token 立即 401。"
                    % (ev['logout_in_other_tab']['protected_page_convergence_seconds_passive'] or '15+')),
         "evidence": "logout_in_other_tab / server_side_logout_contrast"},
        {"id": "OBS-LOCALSTORAGE-TOKEN-NOT-CROSS-TAB", "severity": "info", "case": "X13",
         "detail": ("另一标签改写 localStorage 的 nodesteer_token 后，已打开页面的内存会话不受影响，"
                    "仍以原 token 正常访问 API；仅在自身刷新后才收敛到 /sign-in。"),
         "evidence": "token_replaced_in_other_tab"},
        {"id": "OBS-CROSS-SESSION-NO-PUSH", "severity": "info", "case": "X13",
         "detail": "另一会话修改数据后，已打开页面不会自动刷新（无跨会话推送）；离开再返回该页时重新拉取并显示新值。",
         "evidence": "cross_page_cache"},
    ]
    C.write_evidence("X13-multi-session-role-logout-convergence.json", ev)
    print(json.dumps({k: ev[k] for k in ("sessions", "cross_page_cache", "old_session_role_change_api_path",
                                        "old_session_role_change_ui_path", "tabs", "logout_in_other_tab",
                                        "server_side_logout_contrast", "token_replaced_in_other_tab",
                                        "verdict")}, ensure_ascii=False, indent=1)[:7000])


if __name__ == "__main__":
    main()
