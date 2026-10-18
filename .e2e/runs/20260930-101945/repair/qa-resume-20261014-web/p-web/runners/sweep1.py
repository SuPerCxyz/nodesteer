#!/usr/bin/env python3
"""Read-only UI sweep for the p-web batch. Real browser interactions only.

Writes evidence files under evidence/ (prefixed by case id) and prints a
compact per-case summary.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import ui  # noqa: E402

OUT = {}


def rec(name, obj):
    ui.write_ev(name, obj)
    OUT[name] = obj
    print("  ->", name, json.dumps(obj, ensure_ascii=False)[:400])


TABLE_JS = r'''
(() => {
  const t = document.querySelector('table');
  if (!t) return {has_table:false};
  return {
    headers: Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),
    rows: Array.from(t.querySelectorAll('tbody tr')).map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim())),
    row_count: t.querySelectorAll('tbody tr').length
  };
})()'''

FORMS_JS = r'''
(() => {
  const scope = document.querySelector('form') || document.querySelector('main') || document.body;
  const field = (e) => ({
    tag: e.tagName, type: e.type||null, name: e.name||null, id: e.id||null,
    placeholder: e.placeholder||null, aria: e.getAttribute('aria-label'),
    label: (() => { try { return e.labels && e.labels[0] ? e.labels[0].innerText.trim() : null; } catch(x){ return null; } })(),
    required: !!e.required, value: (e.type==='password'?'<masked>':(e.value||'')) , options: e.tagName==='SELECT'?Array.from(e.options).map(o=>o.text):null
  });
  return {
    url: location.pathname,
    headings: Array.from(document.querySelectorAll('h1,h2,h3')).map(h=>h.innerText.trim()).slice(0,12),
    inputs: Array.from(scope.querySelectorAll('input,textarea,select')).map(field),
    buttons: Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean).slice(0,25),
    tabs: Array.from(document.querySelectorAll('[role=tab],button[data-state]')).map(b=>b.innerText.trim()).filter(Boolean).slice(0,12),
    body_text_head: (document.querySelector('main')||document.body).innerText.slice(0,900)
  };
})()'''


def case_executions():
    print("[P17P18-03/04] executions")
    ui.open_("/executions", 2.0)
    base = ui.js(TABLE_JS)
    filters = ui.js(r'''
(() => {
  const sels = Array.from(document.querySelectorAll('select')).map(s=>({aria:s.getAttribute('aria-label'),options:Array.from(s.options).map(o=>o.text)}));
  return {selects: sels};
})()''')
    st, ex = api.j("GET", "/api/executions?limit=5000", api.login())
    lst = api.as_list(ex)
    import collections
    dist = dict(collections.Counter(e.get("status") for e in lst))
    trig = dict(collections.Counter(e.get("trigger_type") for e in lst))
    seen = {}
    for e in lst:
        seen.setdefault(e["status"], {"id": e["id"], "task_id": e["task_id"],
                                      "trigger_type": e["trigger_type"],
                                      "exit_code": e.get("exit_code")})
    rec("P17P18-03-executions-status.json", {
        "ui_headers": base.get("headers"),
        "status_filter_options": filters["selects"][0]["options"] if filters["selects"] else None,
        "trigger_filter_options": filters["selects"][1]["options"] if len(filters["selects"]) > 1 else None,
        "api_status_distribution": dist,
        "api_one_sample_per_status": seen,
        "api_total": len(lst)})
    rec("P17P18-04-executions-trigger.json", {
        "ui_header_trigger": "触发方式" in (base.get("headers") or []),
        "trigger_filter_options": filters["selects"][1]["options"] if len(filters["selects"]) > 1 else None,
        "api_trigger_type_distribution": trig,
        "ui_sample_rows": base.get("rows", [])[:6]})
    # real filter interaction: pick 已阻断
    ui.js(r'''(() => {
      const s = Array.from(document.querySelectorAll('select')).find(x=>x.getAttribute('aria-label')==='状态');
      const o = Array.from(s.options).find(o=>o.text==='已阻断');
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
      setter.call(s, o.value);
      s.dispatchEvent(new Event('change',{bubbles:true}));
      return o.value;
    })()''')
    import time
    time.sleep(1.2)
    filt = ui.js(TABLE_JS)
    ui.shot("P17P18-03-filter-blocked.png")
    rec("P17P18-03-filter-blocked.json", {
        "selected_filter": "已阻断",
        "row_statuses": [r[0] for r in filt.get("rows", [])],
        "all_blocked": all(r[0] == "已阻断" for r in filt.get("rows", [])) and bool(filt.get("rows")),
        "row_count": filt.get("row_count")})
    ui.open_("/executions", 1.5)


def case_audit():
    print("[P19-01/P19-04] audit")
    ui.open_("/audit", 2.0)
    t = ui.js(TABLE_JS)
    ui.shot("P19-01-audit-list.png")
    ui.shot("P19-04-audit-fields.png")
    # row detail modal / expandable
    ui.click("table tbody tr:first-child")
    import time
    time.sleep(1.0)
    detail = ui.js(r'''
(() => {
  const d = document.querySelector('[role=dialog]');
  return d ? {dialog_text: d.innerText.slice(0,1200)} : {dialog_text:null, body_head: document.body.innerText.slice(0,600)};
})()''')
    st, au = api.j("GET", "/api/audit?limit=5", api.login())
    row = api.as_list(au)[0] if api.as_list(au) else None
    rec("P19-01-audit-list.json", {
        "headers": t.get("headers"), "row_count": t.get("row_count"),
        "first_rows": t.get("rows", [])[:5],
        "search_input": ui.js(r'''(()=>Array.from(document.querySelectorAll('input')).map(i=>i.placeholder||i.getAttribute('aria-label')))()'''),
        "pagination_present": ui.js(r'''(()=>!!document.querySelector('[aria-label*="页"],[aria-label*="pagination"],nav button'))()''')})
    rec("P19-04-audit-row-fields.json", {
        "api_row": row, "detail_dialog": detail, "ui_headers": t.get("headers")})


def case_users():
    print("[P20-01] users")
    ui.open_("/users", 2.0)
    t = ui.js(TABLE_JS)
    ui.shot("P20-01-users-list.png")
    st, us = api.j("GET", "/api/users", api.login())
    rec("P20-01-users-list.json", {
        "headers": t.get("headers"), "row_count": t.get("row_count"),
        "first_rows": t.get("rows", [])[:6],
        "api_user_count": len(us),
        "role_select_present": ui.js(r'''(()=>{const s=Array.from(document.querySelectorAll('select'));return s.map(x=>Array.from(x.options).map(o=>o.text))})()''')})


def main():
    case_executions()
    case_audit()
    case_users()
    print(json.dumps(sorted(OUT.keys()), ensure_ascii=False))


main()
