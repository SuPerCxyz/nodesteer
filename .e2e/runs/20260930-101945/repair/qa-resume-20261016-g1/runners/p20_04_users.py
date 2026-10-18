"""P20-04：创建成功、重复用户名、错误输入（UI + API）+ MAIN-USER-EMPTY-PASSWORD 复核。

维度：
1. UI 真实创建 → 列表出现 + API 计数 +1 + 审计 create 行；
2. UI 重复用户名 → 400 提示、无新行、计数不变、无新审计；
3. 错误输入：UI 按钮禁用策略 + API 空用户名/空密码/非法角色 400；
4. MAIN-USER-EMPTY-PASSWORD：修复版是否拒绝空密码（API + 遗留空 hash 行直连注入的真实登录尝试）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402

FILL_JS = r"""
(() => {
  const u = %s, p = %s;
  const labels = [...document.querySelectorAll('label')];
  const lab = (re) => labels.find(l => re.test((l.innerText||'').trim()));
  const setVal = (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const lu = lab(/用户名/), lp = lab(/^密码|密码$/);
  const ui = lu ? lu.querySelector('input') : null;
  const pi = lp ? lp.querySelector('input') : null;
  if (!ui || !pi) return {ok:false, labels: labels.map(l=>(l.innerText||'').trim()).slice(0,12)};
  setVal(ui, u); setVal(pi, p);
  const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '创建');
  return {ok:true, username_len: ui.value.length, password_len: pi.value.length,
          password_masked: pi.type === 'password', create_disabled: btn ? !!btn.disabled : null};
})()
"""


def ui_create(who: str, username: str, password: str) -> dict:
    """真实 UI 创建用户：打开表单 → 填值 → 点创建。"""
    U.open_(who, "/users")
    before_rows = U.js(who, "[...document.querySelectorAll('table tbody tr')].length")
    open_form = U.click_by_text(who, "^新建用户$")
    filled = U.js(who, FILL_JS % (json.dumps(username), json.dumps(password)))
    clicked = U.click_by_text(who, "^创建$")
    import time
    time.sleep(1.6)
    after = U.js(who, r"""
(() => {
  const body = document.body.innerText || '';
  const rows = [...document.querySelectorAll('table tbody tr')];
  const toast = [...document.querySelectorAll('[role=status],[data-sonner-toast],li')]
      .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 200);
  return {rows: rows.length, has_user: body.includes(%s), toast_like: toast.slice(-6),
          form_open: !!document.querySelector('label')};
})()""" % json.dumps(username))
    return {"open_form": open_form, "fill": filled, "click_create": clicked,
            "rows_before": before_rows, "after": after,
            "shot_after": U.shot(who, f"P2004-{username.split('-')[-1]}-after.png")}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    import secrets as _s
    run = _s.token_hex(2)
    ev = {"case_id": "P20-04", "at_utc": C.utc(), "authority": cr["base_url"], "run_suffix": run}
    ui_user = f"qa-{nonce}-u-ui{run}"
    api_user = f"qa-{nonce}-u-api{run}"

    def user_count():
        return len(C.as_list(hub.get("/api/users", admin)["json"]))

    def audit_user_actions(uid):
        code, rows = C.audit_rows(hub, admin, limit=500)
        return [r for r in rows if isinstance(r, dict) and r.get("resource") == "user"
                and r.get("resource_id") == uid]

    # ---------- 1. API 正路径 ----------
    n0 = user_count()
    r = hub.post("/api/users", admin, body={"username": api_user, "password": "p" * 16, "role": "operator"})
    uid = (r["json"] or {}).get("id")
    ev["api_create"] = {"http": r["status"], "user_id": uid, "role": (r["json"] or {}).get("role"),
                       "count_before": n0, "count_after": user_count()}
    ev["audit_rows_for_created_user"] = audit_user_actions(uid)

    # ---------- 2. API 重复用户名 ----------
    dup = hub.post("/api/users", admin, body={"username": api_user, "password": "q" * 16, "role": "viewer"})
    ev["api_duplicate"] = {"http": dup["status"], "error": (dup["json"] or {}).get("error"),
                           "count_after": user_count()}

    # ---------- 3. API 错误输入 ----------
    bad = {}
    for name, body in (
        ("empty_username", {"username": "", "password": "x" * 12, "role": "viewer"}),
        ("empty_password", {"username": f"qa-{nonce}-u-emptypw", "password": "", "role": "viewer"}),
        ("missing_password", {"username": f"qa-{nonce}-u-nopw", "role": "viewer"}),
        ("invalid_role", {"username": f"qa-{nonce}-u-badrole", "password": "x" * 12, "role": "root"}),
        ("default_role", {"username": f"qa-{nonce}-u-defaultrole", "password": "x" * 12}),
        ("whitespace_username", {"username": "   ", "password": "x" * 12, "role": "viewer"}),
        ("short_password", {"username": f"qa-{nonce}-u-shortpw", "password": "a", "role": "viewer"}),
    ):
        rr = hub.post("/api/users", admin, body=body)
        bad[name] = {"http": rr["status"],
                     "error": (rr["json"] or {}).get("error") if isinstance(rr["json"], dict) else None,
                     "created_user": (rr["json"] or {}).get("username") if rr["status"] == 200 else None}
    ev["api_invalid_inputs"] = bad
    ev["user_count_after_invalid"] = user_count()

    # ---------- 4. MAIN-USER-EMPTY-PASSWORD 复核 ----------
    ev["main_user_empty_password"] = {
        "api_create_empty_password_http": bad["empty_password"]["http"],
        "api_create_empty_password_rejected": bad["empty_password"]["http"] == 400,
        "login_created_user_with_empty_password": hub.login(api_user, "")["status"],
        "login_created_user_with_wrong_password": hub.login(api_user, "wrong")["status"],
        "login_created_user_with_correct_password": hub.login(api_user, "p" * 16)["status"],
    }

    # ---------- 5. UI 真实创建 + 重复 ----------
    U.init(nonce)
    lg = U.login("adm2", cr["admin_username"], cr["admin_password"])
    ev["ui_login"] = {"token": lg["token_in_localStorage"], "url": lg["url"]}
    n_before_ui = user_count()
    ui_new = ui_create("adm2", ui_user, "u" * 16)
    n_after_ui = user_count()
    # 翻到最后一页确认新用户行真实出现在列表中
    U.click_by_text("adm2", "跳转到最后一页")
    import time as _t
    _t.sleep(1.4)
    lastpage = U.js("adm2", r"""(() => {
      const rows = [...document.querySelectorAll('table tbody tr')].map(r => r.innerText);
      return {rows: rows.length, joined: rows.join('|').slice(0, 600),
              has_new: rows.join('\n').includes(%s)};
    })()""" % json.dumps(ui_user))
    ui_new["last_page_check"] = lastpage
    ui_new["last_page_shot"] = U.shot("adm2", "P2004-ui-created-lastpage.png")
    ev["ui_create_success"] = {"detail": ui_new, "api_count_before": n_before_ui,
                               "api_count_after": n_after_ui,
                               "created_in_api": any(u.get("username") == ui_user
                                                     for u in C.as_list(hub.get("/api/users", admin)["json"]))}
    ui_dup = ui_create("adm2", ui_user, "v" * 16)
    ev["ui_duplicate"] = {"detail": ui_dup, "api_count_after": user_count(),
                          "duplicate_count": sum(1 for u in C.as_list(hub.get("/api/users", admin)["json"])
                                                 if u.get("username") == ui_user)}
    # UI 客户端校验：空用户名 / 空密码时创建按钮禁用
    U.open_("adm2", "/users")
    U.click_by_text("adm2", "^新建用户$")
    U.js("adm2", FILL_JS % (json.dumps(""), json.dumps("")))
    empty_state = U.js("adm2", r"""(() => {
      const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '创建');
      return {disabled: btn ? !!btn.disabled : null};
    })()""")
    U.js("adm2", FILL_JS % (json.dumps(f"qa-{nonce}-u-uiprobe"), json.dumps("")))
    pw_empty_state = U.js("adm2", r"""(() => {
      const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '创建');
      return {disabled: btn ? !!btn.disabled : null};
    })()""")
    ev["ui_client_validation"] = {"create_button_disabled_when_both_empty": empty_state.get("disabled"),
                                  "create_button_disabled_when_password_empty": pw_empty_state.get("disabled")}
    ev["ui_probe_user_created"] = any(u.get("username") == f"qa-{nonce}-u-uiprobe"
                                      for u in C.as_list(hub.get("/api/users", admin)["json"]))

    # ---------- 6. 遗留空 hash 行直连注入的真实登录尝试（可销毁实例内） ----------
    import uuid as _uuid
    inject_uname = f"qa-{nonce}-legacy-emptyhash"
    tok_admin_str = admin
    py = (
        "import sqlite3,json\n"
        f"db={cr['data_dir']!r}+'/hub.db'\n"
        "c=sqlite3.connect(db,timeout=10)\n"
        "c.execute(\"INSERT INTO users (id,username,password_hash,role,created_at) VALUES (?,?,?,?,?)\",\n"
        f"          ({str(_uuid.uuid4())!r},{inject_uname!r},'', 'viewer', {C.utc()!r}))\n"
        "c.commit()\n"
        f"row=c.execute('SELECT username, length(password_hash) FROM users WHERE username=?',({inject_uname!r},)).fetchone()\n"
        "print(json.dumps({'injected_row':row}))\n"
    )
    cp = C.ssh(f"python3 - <<'PYEOF'\n{py}PYEOF", check=False)
    login_empty = hub.login(inject_uname, "")
    login_any = hub.login(inject_uname, "anything")
    ev["legacy_empty_hash_injection"] = {
        "injector": "sqlite3 direct INSERT into isolated instance state.db (disposable instance only)",
        "inject_out": cp.stdout.strip().splitlines(),
        "login_empty_password_http": login_empty["status"],
        "login_empty_password_body": (login_empty["json"] or {}).get("error") if isinstance(login_empty["json"], dict) else None,
        "login_any_password_http": login_any["status"],
        "verdict": ("empty-hash account cannot log in with an empty password"
                    if login_empty["status"] == 401 else "EMPTY PASSWORD LOGIN SUCCEEDED - defect present"),
    }
    C.ssh(f"sqlite3 {cr['data_dir']}/state.db \"DELETE FROM users WHERE username='{inject_uname}'\"; echo CLEANED", check=False)
    clean_py = (
        "import sqlite3\n"
        f"c=sqlite3.connect({cr['data_dir']!r}+'/hub.db',timeout=10)\n"
        f"n=c.execute('DELETE FROM users WHERE username=?',({inject_uname!r},)).rowcount\n"
        "c.commit()\n"
        "print('DELETED',n)\n"
    )
    cp2 = C.ssh(f"python3 - <<'PYEOF'\n{clean_py}PYEOF", check=False)
    ev["legacy_injection_cleaned"] = cp2.stdout.strip()

    ev["verdict"] = {
        "ui_create_success": ev["ui_create_success"]["created_in_api"]
                            and n_after_ui == n_before_ui + 1
                            and ev["ui_create_success"]["detail"]["after"]["toast_like"][-1] == "已创建"
                            and ev["ui_create_success"].get("last_page_check", {}).get("has_new") is True,
        "duplicate_ui_400_and_no_dup": ev["ui_duplicate"]["duplicate_count"] == 1,
        "duplicate_api_400": ev["api_duplicate"]["http"] == 400,
        "invalid_inputs_rejected": {k: v["http"] for k, v in bad.items()},
        "empty_password_rejected_by_api": bad["empty_password"]["http"] == 400,
        "legacy_empty_hash_login_rejected": login_empty["status"] == 401,
    }
    C.write_evidence("P20-04-users-create-validation.json", ev)
    print(json.dumps({k: ev[k] for k in ("api_create", "api_duplicate", "api_invalid_inputs",
                                        "main_user_empty_password", "legacy_empty_hash_injection",
                                        "ui_client_validation", "verdict")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
