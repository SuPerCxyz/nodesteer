#!/usr/bin/env python3
"""Read-only probes: schedule editor (/schedules/new), application health types, artifact upload form."""
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


def combo_options(label, prefix=""):
    s = snap()
    m = re.search(r'combobox "%s" \[expanded=false, ref=(e\d+)\]' % re.escape(label), s)
    if not m:
        return None
    ui.run("click", "@" + m.group(1))
    time.sleep(0.9)
    opts = [re.sub(r'^-\s*', '', l.strip()) for l in snap().splitlines() if "- option" in l]
    ui.run("press", "Escape")
    time.sleep(0.4)
    return opts


def editor_state():
    return ui.js(r'''(() => {
      const main = document.querySelector('main') || document.body;
      return {
        url: location.pathname + location.search,
        h1: (document.querySelector('h1')||{}).innerText || null,
        labels: Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        combos: Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,40)),
        inputs: Array.from(main.querySelectorAll('input,textarea')).map(i=>({ph:i.placeholder,aria:i.getAttribute('aria-label'),type:i.type,v:(i.value||'').slice(0,40)})),
        buttons: Array.from(main.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean),
        text: main.innerText.slice(0, 900)
      };
    })()''')


def schedules():
    print("[X05/P12P13/X04/X10] schedule editor")
    out = {}
    # no task_id
    ui.open_("/schedules/new", 2.5)
    out["empty"] = editor_state()
    ui.shot("X05-schedule-new-no-taskid.png", full=True)
    out["type_options"] = combo_options("类型")
    out["timezone_present"] = ui.js(r'''(() => {
      const m=(document.querySelector('main')||document.body).innerText;
      return {has_timezone:/时区/.test(m), has_next_runs:/接下来 5 次触发/.test(m)};
    })()''')
    # valid task_id (read-only use of a real task created by a parallel batch; no save)
    st, tl = api.j("GET", "/api/tasks", api.login())
    tasks = api.as_list(tl)
    if tasks:
        t = tasks[0]
        ui.open_("/schedules/new?task_id=" + t["id"], 2.5)
        out["with_task_id"] = editor_state()
        out["with_task_id_task"] = {"id": t["id"], "name": t["name"], "owned_by_us": False}
        ui.shot("X05-schedule-new-with-taskid.png", full=True)
        out["task_locked"] = ui.js(r'''(() => {
          const main=document.querySelector('main')||document.body;
          const combo=Array.from(document.querySelectorAll('main [role=combobox]')).find(c=>c.innerText.includes('qa-'));
          return {combo_text: combo?combo.innerText.trim():null,
                  disabled_controls: Array.from(document.querySelectorAll('main [disabled]')).map(e=>e.tagName+':'+(e.innerText||'').trim().slice(0,20)).slice(0,8)};
        })()''')
        # interval type
        out["interval"] = {}
        s = snap()
        m = re.search(r'combobox "类型" \[expanded=false, ref=(e\d+)\]', s)
        if m:
            ui.run("click", "@" + m.group(1)); time.sleep(0.8)
            s2 = snap()
            it = [l for l in s2.splitlines() if "- option" in l and "Interval" in l]
            if it:
                ui.run("click", "@" + re.search(r"ref=(e\d+)", it[0]).group(1)); time.sleep(0.9)
                out["interval"] = editor_state()
                ui.shot("P12P13-04-schedule-interval.png", full=True)
    # invalid task_id
    ui.open_("/schedules/new?task_id=00000000-0000-0000-0000-000000000000", 2.5)
    out["invalid_task_id"] = ui.js(r'''(() => {
      const main=document.querySelector('main')||document.body;
      return {url:location.pathname+location.search, text:main.innerText.slice(0,350),
        has_error:/不存在|无效|invalid|not found/.test(main.innerText)};
    })()''')
    ui.shot("X05-schedule-invalid-taskid.png", full=True)
    RES["P12P13-X04-X05-X10"] = out


def app_health():
    print("[P14P15-04/X06] app health types + artifact upload")
    out = {}
    ui.open_("/applications/new", 2.5)
    s = snap()
    m = re.search(r'combobox "健康检查类型" \[expanded=false, ref=(e\d+)\]', s)
    if not m:
        m = re.search(r'combobox[^\]]*ref=(e\d+)\]: systemd', s)
    if m:
        ui.run("click", "@" + m.group(1)); time.sleep(0.9)
        out["health_type_options"] = [l.strip() for l in snap().splitlines() if "- option" in l]
        # pick tcp to see its fields
        s2 = snap()
        tcp = [l for l in s2.splitlines() if "- option" in l and "tcp" in l.lower()]
        if tcp:
            ui.run("click", "@" + re.search(r"ref=(e\d+)", tcp[0]).group(1)); time.sleep(1.0)
            out["tcp"] = ui.js(r'''(() => {
              const main=document.querySelector('main')||document.body;
              return {labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(0,20),
                      inputs:Array.from(main.querySelectorAll('input')).map(i=>({ph:i.placeholder,type:i.type}))};
            })()''')
            ui.shot("P14P15-04-health-tcp.png", full=True)
    # artifact indirect entry: 上传新发布包
    btns = ui.js("(()=>Array.from(document.querySelectorAll('main button')).map(b=>b.innerText.trim()).filter(Boolean))()")
    if "上传新发布包" in (btns or []):
        ui.run("click", "@" + ref_of("上传新发布包")); time.sleep(1.2)
    elif "选择发布包..." in (btns or []):
        ui.run("click", "@" + ref_of("选择发布包...")); time.sleep(1.2)
    out["artifact_dialog"] = ui.js(r'''(() => {
      const d=document.querySelector('[role=dialog]');
      return d ? {role:'dialog', text:d.innerText.slice(0,600),
        labels:Array.from(d.querySelectorAll('label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        combos:Array.from(d.querySelectorAll('[role=combobox]')).map(c=>c.innerText.trim().slice(0,30)),
        inputs:Array.from(d.querySelectorAll('input')).map(i=>({type:i.type,ph:i.placeholder,accept:i.accept})),
        buttons:Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)} : {role:null};
    })()''')
    ui.shot("P16-02-artifact-upload-from-app.png", full=True)
    # list existing artifacts in the picker
    out["artifact_picker"] = ui.js(r'''(() => {const m=(document.querySelector('main')||document.body).innerText;
      return {text:(m.match(/发布包[^\n]{0,60}/g)||[]).slice(0,6)};})()''')
    RES["P02-01-P14P15-04-X06-P16-02"] = out


def main():
    schedules()
    app_health()
    ui.write_ev("P12P13-X04-X05-X10-P14P15-04-X06-P16-02.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:6000])


main()
