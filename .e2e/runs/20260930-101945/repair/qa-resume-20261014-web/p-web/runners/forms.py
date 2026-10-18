#!/usr/bin/env python3
"""Remaining read-only form probes: task validation, schedule form, app form, artifact form."""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import ui  # noqa: E402

RES = {}


def snap():
    return ui.run("snapshot", "-i")[0]


def ref_of(text, role="button"):
    for l in snap().splitlines():
        if '%s "%s"' % (role, text) in l and "ref=" in l:
            return re.search(r"ref=(e\d+)", l).group(1)


def toasts():
    return ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast],[data-sonner-toaster] li'));
      return {n:t.length,texts:t.map(x=>x.innerText.trim())}})()''')


def set_first_text(value, idx=0):
    return ui.js("(()=>{const els=Array.from(document.querySelectorAll('main input')).filter(i=>i.type==='text'&&!(i.placeholder||'').length);const el=els[%d];if(!el)return{ok:false};const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return{ok:true,len:el.value.length}})()" % (idx, json.dumps(value)))


def select_combo(label, option_text):
    s = snap()
    m = re.search(r'combobox "%s" \[expanded=false, ref=(e\d+)\]' % re.escape(label), s)
    if not m:
        return {"ok": False, "reason": "combo missing"}
    ui.run("click", "@" + m.group(1))
    time.sleep(0.8)
    s2 = snap()
    tgt = [l for l in s2.splitlines() if "- option" in l and option_text in l]
    if not tgt:
        ui.run("press", "Escape")
        return {"ok": False, "options": [l.strip() for l in s2.splitlines() if "- option" in l]}
    ui.run("click", "@" + re.search(r"ref=(e\d+)", tgt[0]).group(1))
    time.sleep(0.9)
    return {"ok": True}


def task_validation():
    print("[P09P11-22] task validation")
    out = {}
    # no script
    ui.open_("/tasks/new", 2.2)
    set_first_text("qa-pweb-d6dbf7af-probe-noscript")
    select_combo("类型", "脚本")
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(1.6)
    out["script_no_script"] = toasts()
    ui.shot("P09P11-22-script-no-script.png", full=True)
    # no application
    ui.open_("/tasks/new", 2.0)
    set_first_text("qa-pweb-d6dbf7af-probe-noapp")
    select_combo("类型", "托管应用部署")
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(1.6)
    out["appdeploy_no_app"] = toasts()
    ui.shot("P09P11-22-appdeploy-no-app.png", full=True)
    # command but empty command (already done) -> also check with a target selected
    ui.open_("/tasks/new", 2.0)
    set_first_text("qa-pweb-d6dbf7af-probe-emptytarget")
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(1.6)
    out["name_only_no_command"] = toasts()
    # invalid target node id via API-side check (UI cannot pick invalid)
    st, body = api.j("POST", "/api/tasks", api.login(),
                     {"name": "qa-pweb-d6dbf7af-invalid-target",
                      "type": "command", "command": "true",
                      "target": {"type": "node", "node_ids": ["not-a-uuid"]}, "enabled": True})
    out["invalid_target_node_id_api"] = {"http": st, "body": body if isinstance(body, str) else json.dumps(body)[:200]}
    RES["P09P11-22"] = out


def schedule_form():
    print("[P12P13-04/05/X04/X10] schedule form")
    ui.open_("/tasks/new", 2.2)
    # create form is on task detail; schedules are created from task detail page.
    # Use an existing task (owned by a parallel batch) only for READ-ONLY form inspection.
    st, tasks = api.j("GET", "/api/tasks", api.login())
    tl = api.as_list(tasks)
    out = {"readonly_note": "form inspected from a real task detail page (read-only; no save)"}
    if tl:
        t = tl[0]
        ui.open_("/tasks/" + t["id"], 2.5)
        out["task_used"] = {"id": t["id"], "name": t["name"], "owned_by_us": False}
        found = ui.js(r'''(() => {
          const links=Array.from(document.querySelectorAll('a[href^="/schedules"]')).map(a=>({t:a.innerText.trim(),h:a.getAttribute('href')}));
          const btn=Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(t=>/新建调度|添加调度/.test(t));
          return {schedule_links:links, new_schedule_buttons:btn};
        })()''')
        out["task_detail_schedule_entries"] = found
        ui.shot("X05-task-detail-schedule-entry.png", full=True)
        if found.get("new_schedule_buttons"):
            ui.run("click", "@" + ref_of(found["new_schedule_buttons"][0]))
            time.sleep(1.5)
            out["new_schedule_form"] = ui.js(r'''(() => {
              const main=document.querySelector('main')||document.body;
              return {url:location.pathname, h1:(document.querySelector('h1')||{}).innerText||null,
                labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(0,25),
                combos:Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,40)),
                inputs:Array.from(main.querySelectorAll('input')).map(i=>({ph:i.placeholder,type:i.type,v:i.value})),
                text:main.innerText.slice(0,700)};
            })()''')
            ui.shot("X05-new-schedule-from-task.png", full=True)
    RES["P12P13-X04-X05-X10"] = out


def app_form():
    print("[P14P15-02/04/X06] application form")
    ui.open_("/applications/new", 2.5)
    out = {"new_form": ui.js(r'''(() => {
      const main=document.querySelector('main')||document.body;
      return {url:location.pathname, h1:(document.querySelector('h1')||{}).innerText||null,
        labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        combos:Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,40)),
        inputs:Array.from(main.querySelectorAll('input,textarea')).map(i=>({ph:i.placeholder,type:i.type})),
        buttons:Array.from(main.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean),
        text:main.innerText.slice(0,1200)};
    })()''')}
    ui.shot("P14P15-02-application-new-form.png", full=True)
    # health check types
    hc = ui.js(r'''(() => {
      const btns=Array.from(document.querySelectorAll('main button')).map(b=>b.innerText.trim());
      return {health_buttons: btns.filter(b=>/health|健康|systemd|tcp|http|command/i.test(b)), all_buttons: btns.filter(Boolean).slice(0,30)};
    })()''')
    out["health_controls"] = hc
    RES["P14P15-02-04"] = out
    # try opening health check editor if present
    for label in ["添加健康检查", "添加检查", "健康检查"]:
        if label in (hc.get("all_buttons") or []):
            ui.run("click", "@" + ref_of(label))
            time.sleep(1.2)
            out["health_editor_%s" % label] = ui.js(r'''(() => {
              const main=document.querySelector('main')||document.body;
              return {labels:Array.from(document.querySelectorAll('label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(-12),
                combos:Array.from(document.querySelectorAll('[role=combobox]')).map(c=>c.innerText.trim().slice(0,30)).slice(-6),
                inputs:Array.from(main.querySelectorAll('input')).map(i=>i.placeholder||i.getAttribute('aria-label')).slice(-10)};
            })()''')
            ui.shot("P14P15-04-health-check-editor.png", full=True)
            break


def artifact_form():
    print("[P16-02/P16-04] artifact upload form")
    ui.open_("/artifacts", 2.2)
    btns = ui.js("(()=>Array.from(document.querySelectorAll('main button')).map(b=>b.innerText.trim()).filter(Boolean))()")
    out = {"upload_buttons": [b for b in btns if "上传" in b or "新建" in b or "添加" in b]}
    if out["upload_buttons"]:
        ui.run("click", "@" + ref_of(out["upload_buttons"][0]))
        time.sleep(1.2)
        out["form"] = ui.js(r'''(() => {
          const d=document.querySelector('[role=dialog]')||document.querySelector('main');
          return {role:d.getAttribute('role')||d.tagName,
            labels:Array.from(d.querySelectorAll('label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
            combos:Array.from(d.querySelectorAll('[role=combobox]')).map(c=>c.innerText.trim().slice(0,30)),
            inputs:Array.from(d.querySelectorAll('input')).map(i=>({type:i.type,ph:i.placeholder,accept:i.accept})),
            buttons:Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean),
            text:d.innerText.slice(0,700)};
        })()''')
        ui.shot("P16-02-artifact-upload-form.png", full=True)
        # arch options
        s = snap()
        m = re.search(r'combobox "架构" \[expanded=false, ref=(e\d+)\]', s)
        if m:
            ui.run("click", "@" + m.group(1))
            time.sleep(0.8)
            out["arch_options"] = [l.strip() for l in snap().splitlines() if "- option" in l]
            ui.run("press", "Escape")
    RES["P16-02-04"] = out


def main():
    task_validation()
    schedule_form()
    app_form()
    artifact_form()
    ui.write_ev("P09P11-22-P12P13-P14P15-P16-forms.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:5000])


main()
