#!/usr/bin/env python3
"""Read-only UI sweep part 2: audit pagination/search, users form, scripts list."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import ui  # noqa: E402

TABLE_JS = (Path(__file__).resolve().parent / "sweep1.py").read_text().split("TABLE_JS = r'''")[1].split("'''")[0]


def rec(name, obj):
    ui.write_ev(name, obj)
    print("  ->", name, json.dumps(obj, ensure_ascii=False)[:500])


def audit_extra():
    print("[P19-01] audit search/pagination/empty")
    ui.open_("/audit", 2.0)
    pag = ui.js(r'''(() => {
      const btns = Array.from(document.querySelectorAll('button')).map(b=>b.getAttribute('aria-label')||b.innerText.trim());
      return {buttons: btns.filter(Boolean).slice(0,20),
              total_hint: (document.body.innerText.match(/共\s*\d+\s*条/)||[])[0]||null};
    })()''')
    # real search interaction
    ui.fill("input[placeholder='搜索用户、操作或资源']", "delete")
    time.sleep(1.2)
    after = ui.js(r'''(() => {
      const t=document.querySelector('table');
      return {rows: t?Array.from(t.querySelectorAll('tbody tr')).map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim())):[], row_count: t?t.querySelectorAll('tbody tr').length:0};
    })()''')
    ui.shot("P19-01-audit-search-delete.png")
    # empty search
    ui.fill("input[placeholder='搜索用户、操作或资源']", "zzz-no-such-audit-qa-pweb")
    time.sleep(1.2)
    empty = ui.js(r'''(() => {
      const t=document.querySelector('table');
      const main=(document.querySelector('main')||document.body).innerText;
      return {row_count: t?t.querySelectorAll('tbody tr').length:0, empty_text: /暂无|没有|无数据|No data|empty/i.test(main)};
    })()''')
    ui.shot("P19-01-audit-empty.png")
    ui.open_("/audit", 1.5)
    rec("P19-01-audit-search-pagination.json", {"pagination_controls": pag, "search_delete_row_count": after["row_count"],
                                                "search_delete_rows": after["rows"][:4], "empty_state": empty})


def users_form():
    print("[P20-02/P20-03] new user form")
    ui.open_("/users", 2.0)
    before = ui.js(r'''(()=>({forms: document.querySelectorAll('form').length, inputs: document.querySelectorAll('input').length, url: location.pathname}))()''')
    opened = ui.find_click("button", "新建用户") or ui.find_click("button", "添加用户") or ui.find_click("button", "新增用户")
    time.sleep(1.0)
    form = ui.js(r'''(() => {
      const d = document.querySelector('[role=dialog]') || document.querySelector('form');
      if (!d) return {found:false};
      return {found:true,
        scope: d.getAttribute('role')||d.tagName,
        dialog_text: d.innerText.slice(0,600),
        inputs: Array.from(d.querySelectorAll('input')).map(e=>({name:e.name,type:e.type,placeholder:e.placeholder,required:e.required})),
        selects: Array.from(d.querySelectorAll('select')).map(s=>({options:Array.from(s.options).map(o=>o.text)})),
        buttons: Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)};
    })()''')
    ui.shot("P20-02-new-user-form.png", full=True)
    # required-field probe: submit empty
    ui.find_click("button", "创建") or ui.find_click("button", "保存") or ui.find_click("button", "确定")
    time.sleep(1.2)
    after_submit = ui.js(r'''(() => {
      const d = document.querySelector('[role=dialog]') || document.querySelector('form');
      return {dialog_still_open: !!d, text: d?d.innerText.slice(0,500):null,
              invalid_inputs: Array.from(document.querySelectorAll('[aria-invalid=true]')).length,
              body_has_required: /必填|required/i.test(document.body.innerText)};
    })()''')
    ui.shot("P20-03-required-validation.png", full=True)
    reqs = [l for l in ui.requests(30) if "/api/users" in l]
    # cancel
    cancel = ui.find_click("button", "取消")
    time.sleep(0.8)
    closed = ui.js(r'''(()=>({dialog_present: !!document.querySelector('[role=dialog]'), input_count: document.querySelectorAll('input').length, url: location.pathname}))()''')
    rec("P20-02-new-user-form.json", {"before": before, "opened_click": opened, "form": form,
                                      "after_empty_submit": after_submit, "cancel_clicked": cancel,
                                      "after_cancel": closed, "user_api_requests": reqs})
    rec("P20-03-user-form-roles.json", {"inputs": form.get("inputs"), "role_select": form.get("selects")})


def scripts_page():
    print("[P07_P08-01] scripts list")
    ui.open_("/scripts", 2.0)
    t = ui.js(TABLE_JS)
    ui.shot("P07_P08-01-scripts-list.png", full=True)
    ctrl = ui.js(r'''(() => ({
      search: Array.from(document.querySelectorAll('input')).map(i=>i.placeholder||i.getAttribute('aria-label')),
      view_button: !!Array.from(document.querySelectorAll('button')).find(b=>/视图/.test(b.innerText)),
      pagination: Array.from(document.querySelectorAll('button')).map(b=>b.getAttribute('aria-label')).filter(Boolean).slice(0,10),
      row_count: document.querySelectorAll('table tbody tr').length
    }))()''')
    st, sc = api.j("GET", "/api/scripts", api.login())
    rec("P07_P08-01-scripts-list.json", {"headers": t.get("headers"), "rows": t.get("rows", [])[:5],
                                         "controls": ctrl, "api_scripts": [{"id": s.get("id"), "name": s.get("name"),
                                         "interpreter": s.get("interpreter"), "revision": s.get("revision"),
                                         "enabled": s.get("enabled")} for s in api.as_list(sc)]})
    # empty state via search
    if ctrl.get("search"):
        ui.fill("input[placeholder='%s']" % ctrl["search"][0], "zzz-qa-pweb-none")
        time.sleep(1.2)
        empty = ui.js(r'''(() => {const t=document.querySelector('table');const m=(document.querySelector('main')||document.body).innerText;return {row_count:t?t.querySelectorAll('tbody tr').length:0, empty_text:(m.match(/(暂无|没有|无数据)[^\n]{0,40}/)||[])[0]||null};})()''')
        ui.shot("P07_P08-01-scripts-empty.png")
        ui.open_("/scripts", 1.5)
        rec("P07_P08-01-scripts-empty.json", empty)


def main():
    audit_extra()
    users_form()
    scripts_page()


main()
