#!/usr/bin/env python3
"""Read-only UI sweep part 3: users form, scripts search, transfers, artifacts entry."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402


def rec(name, obj):
    ui.write_ev(name, obj)
    print("  ->", name, json.dumps(obj, ensure_ascii=False)[:600])


def users_form():
    print("[P20-02/P20-03] new user form (open/validate/cancel)")
    ui.open_("/users", 2.0)
    ui.click_by_text("button", "新建用户")
    time.sleep(1.0)
    form = ui.js(r'''(() => {
      const d = document.querySelector('[role=dialog]') || document.querySelector('form');
      if (!d) return {found:false};
      return {found:true, role: d.getAttribute('role')||d.tagName,
        url: location.pathname,
        text: d.innerText.slice(0,500),
        inputs: Array.from(d.querySelectorAll('input')).map(e=>({name:e.name,type:e.type,placeholder:e.placeholder,required:e.required,id:e.id})),
        selects: Array.from(d.querySelectorAll('select')).map(s=>({aria:s.getAttribute('aria-label'),options:Array.from(s.options).map(o=>o.text),value:s.value})),
        buttons: Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)};
    })()''')
    ui.shot("P20-02-new-user-form.png", full=True)
    # empty submit -> required feedback, no write
    sub = ui.click_by_text("button", "保存") if not ui.click_by_text("button", "创建") else None
    time.sleep(1.2)
    after = ui.js(r'''(() => {
      const d = document.querySelector('[role=dialog]') || document.querySelector('form');
      return {dialog_still_open: !!d, dialog_text: d?d.innerText.slice(0,400):null,
        aria_invalid: Array.from(document.querySelectorAll('[aria-invalid=true]')).length,
        required_msgs: (document.body.innerText.match(/(必填|不能为空|required)/g)||[]).slice(0,5)};
    })()''')
    ui.shot("P20-03-required-validation.png", full=True)
    reqs = [l for l in ui.requests(40) if "/api/users" in l]
    ui.click_by_text("button", "取消")
    time.sleep(0.9)
    closed = ui.js(r'''(()=>({dialog_present: !!document.querySelector('[role=dialog]'), input_count: document.querySelectorAll('input').length, table_present: !!document.querySelector('table')}))()''')
    ui.shot("P20-02-after-cancel.png")
    rec("P20-02-new-user-form.json", {"form": form, "after_empty_submit": after, "after_cancel": closed,
                                      "api_user_requests": reqs,
                                      "write_requests_during_probe": [l for l in reqs if " POST " in l]})
    rec("P20-03-user-form-fields.json", {"inputs": form.get("inputs"), "role_select": form.get("selects")})


def scripts_search():
    print("[P07_P08-01] scripts search/pagination/view")
    ui.open_("/scripts", 2.0)
    PH = "input[placeholder='搜索脚本名称、解释器或 SHA256']"
    ui.fill(PH, "qa-web-b05")
    time.sleep(1.2)
    hit = ui.js(r'''(() => {const t=document.querySelector('table');return {rows:t?Array.from(t.querySelectorAll('tbody tr')).map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim())):[], count:t?t.querySelectorAll('tbody tr').length:0};})()''')
    ui.shot("P07_P08-01-scripts-search-hit.png")
    ui.fill(PH, "zzz-qa-pweb-no-script")
    time.sleep(1.2)
    miss = ui.js(r'''(() => {const t=document.querySelector('table');const m=(document.querySelector('main')||document.body).innerText;return {count:t?t.querySelectorAll('tbody tr').length:0, empty:(m.match(/(没有结果|暂无)[^\n]{0,40}/)||[])[0]||null};})()''')
    ui.shot("P07_P08-01-scripts-empty.png")
    ui.open_("/scripts", 1.5)
    view = ui.js(r'''(() => {
      const btns = Array.from(document.querySelectorAll('button')).map(b=>(b.innerText.trim()||b.getAttribute('aria-label')||'')).filter(Boolean);
      const pag = btns.filter(b=>/页|上一页|下一页/.test(b));
      return {view_button: btns.includes('视图'), pagination_buttons: pag, page_size_options: (()=>{const s=Array.from(document.querySelectorAll('select')).find(x=>Array.from(x.options).some(o=>/10|20|50/.test(o.text)));return s?Array.from(s.options).map(o=>o.text):null})()};
    })()''')
    ui.click_by_text("button", "视图")
    time.sleep(0.8)
    viewmenu = ui.js(r'''(() => {const m=Array.from(document.querySelectorAll('[role=menu],[data-radix-popper-content-wrapper],[role=menuitemcheckbox],[role=menuitem]'));return {items:m.map(x=>x.innerText.trim()).slice(0,15), has_column_controls: m.length>0};})()''')
    ui.shot("P07_P08-01-scripts-view-control.png")
    rec("P07_P08-01-scripts-search.json", {"positive_search": hit, "negative_search": miss, "view_controls": view, "view_menu": viewmenu})


def transfers_page():
    print("[P22-05/P22-07] transfers add targets / start disable")
    ui.open_("/transfers", 2.0)
    base = ui.js(r'''(() => {
      const btns = Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean);
      return {buttons: btns, start_disabled: (()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>x.innerText.trim()==='开始传输');return b?{disabled:b.disabled,aria:b.getAttribute('aria-disabled')}:null})(),
        target_inputs: Array.from(document.querySelectorAll('input')).filter(i=>(i.placeholder||'').includes('config.yaml')).length};
    })()''')
    ui.shot("P22-07-transfers-initial.png", full=True)
    # add target
    add = ui.click_by_text("button", "添加目标")
    time.sleep(0.8)
    after_add = ui.js(r'''(() => ({
      target_inputs: Array.from(document.querySelectorAll('input')).filter(i=>(i.placeholder||'').includes('config.yaml')).length,
      target_rows: Array.from(Array.from(document.querySelectorAll('input')).filter(i=>(i.placeholder||'').includes('config.yaml'))).length,
      start_disabled: (()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>x.innerText.trim()==='开始传输');return b?b.disabled:null})(),
      target_selects: Array.from(document.querySelectorAll('select,[role=combobox],button')).filter(b=>/选择目标 Agent/.test(b.innerText)).length
    }))()''')
    ui.shot("P22-05-transfers-target-added.png", full=True)
    reqs = ui.requests(40)
    rec("P22-05-transfers-add-target.json", {"initial": base, "add_clicked": add, "after_add": after_add})
    rec("P22-07-start-disabled.json", {"initial_start_disabled": base.get("start_disabled"),
                                        "after_add_start_disabled": after_add.get("start_disabled"),
                                        "requests": [l for l in reqs if "/api/transfers" in l][-5:]})


def artifacts_entry():
    print("[P02-01] artifacts indirect entry")
    ui.open_("/applications", 2.0)
    app_links = ui.js(r'''(() => ({
      url: location.pathname,
      links: Array.from(document.querySelectorAll('a[href]')).map(a=>a.innerText.trim()+' -> '+a.getAttribute('href')).filter(t=>/发布包|artifact|应用/.test(t)).slice(0,12),
      buttons: Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean).slice(0,20)
    }))()''')
    ui.shot("P02-01-applications-page.png", full=True)
    ui.open_("/artifacts", 1.8)
    direct = ui.js(r'''(() => {const t=document.querySelector('table');return {url: location.pathname, h1:(document.querySelector('h1')||{}).innerText, headers: t?Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()):[], row_count: t?t.querySelectorAll('tbody tr').length:0};})()''')
    ui.shot("P02-01-artifacts-direct-url.png")
    rec("P02-01-artifacts-entry.json", {"applications_page": app_links, "artifacts_direct_url": direct})


def main():
    users_form()
    scripts_search()
    transfers_page()
    artifacts_entry()


main()
