#!/usr/bin/env python3
"""Task lifecycle via real UI: P09P11-02/09/13, E2E-02 (multi-node), X08 run-cancel, P03-02, P17P18-03."""
import json
import re
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pweb  # noqa: E402
import ui  # noqa: E402

PREFIX = pweb.PREFIX
S1, S2 = pweb.Child(1), pweb.Child(2)
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


def set_label(label, value):
    return ui.js(r'''(() => {
      const lb = Array.from(document.querySelectorAll('main label')).find(l => l.innerText.trim().replace(/\n/g,' ').startsWith(%s));
      if (!lb) return {ok:false, reason:'label'};
      const el = lb.querySelector('input,textarea') || (lb.parentElement && lb.parentElement.querySelector('input,textarea'));
      if (!el) return {ok:false, reason:'control'};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(el, %s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return {ok:true, len:el.value.length};
    })()''' % (json.dumps(label), json.dumps(value)))


def toggle_target(name):
    """Click the Radix checkbox whose label contains the node hostname."""
    return ui.js(r'''(() => {
      const lb = Array.from(document.querySelectorAll('main label')).find(l => l.innerText.includes(%s));
      if (!lb) return {ok:false};
      const cb = lb.querySelector('[role=checkbox],input[type=checkbox],button');
      if (!cb) return {ok:false, reason:'no checkbox'};
      cb.click();
      return {ok:true, checked: cb.getAttribute('aria-checked') || cb.checked};
    })()''' % json.dumps(name))


def save():
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.2)


def main():
    # ---- P09P11-02 / 09 / 13: create a command task targeting both owned children ----
    ui.open_("/tasks/new", 2.5)
    c = {"form_fields_before": ui.js(r'''(() => ({
        labels: Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(0,20),
        combos: Array.from(document.querySelectorAll('main [role=combobox]')).map(x=>x.innerText.trim().slice(0,30))}))()''')}
    c["set_name"] = set_label("名称", PREFIX + "-task-multinode")
    c["set_command"] = set_label("命令", "echo " + PREFIX + "-multi-ok")
    c["set_timeout"] = set_label("超时（秒）", "30")
    c["set_retry"] = set_label("重试次数", "1")
    c["set_runuser"] = set_label("运行用户", "root")
    c["pick_child1"] = toggle_target(S1.root.split("-")[-1] and (PREFIX + "-child1"))
    c["pick_child2"] = toggle_target(PREFIX + "-child2")
    c["selected_count"] = ui.js(r'''(() => {
      const t=(document.querySelector('main')||document.body).innerText;
      const m=t.match(/(\d+)\s*已选择/); return m?m[1]:null;})()''')
    c["checked_nodes"] = ui.js(r'''(() => Array.from(document.querySelectorAll('main [role=checkbox][aria-checked=true],main input[type=checkbox]:checked')).length)()''')
    ui.shot("P09P11-09-task-two-node-targets.png", full=True)
    save()
    c["toasts"] = toasts()
    c["url_after"] = ui.run("get", "url")[0].strip()
    c["write_requests"] = [l for l in ui.requests(200) if "/api/tasks" in l and "POST" in l]
    st, tasks = pweb.j("GET", "/api/tasks")
    mine = [t for t in pweb.lst(tasks) if t["name"].startswith(PREFIX)]
    c["created"] = [{k: t.get(k) for k in ("id", "name", "type", "target", "command", "timeout",
                                           "retry", "run_user", "enabled", "offline_policy", "revision")}
                    for t in mine]
    RES["P09P11-02_09_13"] = c
    if not mine:
        ui.write_ev("P09P11-task-multinode.json", RES)
        print(json.dumps(RES, ensure_ascii=False, indent=1)[:3000])
        return
    t0 = mine[0]

    # ---- P09P11-02: task detail page name/description/edit/run entries ----
    ui.open_("/tasks/" + t0["id"], 3.0)
    d = {"url": ui.run("get", "url")[0].strip(),
         "h1": ui.js("(()=>(document.querySelector('h1')||{}).innerText||null)()"),
         "entries": ui.js(r'''(() => ({
             links: Array.from(document.querySelectorAll('a[href]')).map(a=>({t:a.innerText.trim(),h:a.getAttribute('href')})).filter(x=>/tasks|executions|schedul/.test(x.h)).slice(0,12),
             buttons: Array.from(document.querySelectorAll('main button')).map(b=>b.innerText.trim()).filter(Boolean).slice(0,14)}))()''')}
    ui.shot("P09P11-02-task-detail.png", full=True)
    RES["P09P11-02_detail"] = d

    # ---- E2E-02 / P03-02 / run-cancel: 立即运行 ----
    run = {}
    ui.open_("/tasks/" + t0["id"], 2.5)
    # insert a node-side marker before the run
    run["marker_pre"] = S1.write_marker(PREFIX + "-pre-run\n")
    for label in ("立即运行", "运行", "手动运行"):
        if ref_of(label):
            ui.run("click", "@" + ref_of(label))
            break
    time.sleep(1.5)
    run["confirm_dialog"] = ui.js(r'''(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');
      return d?{role:d.getAttribute('role'),text:d.innerText.replace(/\n+/g,' | ').slice(0,300),
        buttons:Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)}:null})()''')
    ui.shot("X08-task-run-confirm.png", full=True)
    run["writes_before_confirm"] = [l for l in ui.requests(200) if "/run" in l]
    # cancel -> zero write
    ui.run("find", "role", "button", "click", "--name", "取消")
    time.sleep(1.5)
    run["after_cancel_writes"] = [l for l in ui.requests(220) if "/run" in l]
    run["after_cancel_dialog"] = ui.js("(()=>!!document.querySelector('[role=alertdialog],[role=dialog]'))()")
    st, ex = pweb.j("GET", "/api/executions?task_id=" + t0["id"] + "&limit=50")
    run["executions_after_cancel"] = len(pweb.lst(ex))
    ui.shot("X08-task-run-after-cancel.png", full=True)
    # confirm -> real run
    if ref_of("立即运行") or ref_of("运行"):
        pass
    ui.open_("/tasks/" + t0["id"], 2.2)
    for label in ("立即运行", "运行", "手动运行"):
        if ref_of(label):
            ui.run("click", "@" + ref_of(label))
            break
    time.sleep(1.2)
    ui.run("find", "role", "button", "click", "--name", "运行")
    time.sleep(4.0)
    run["toasts"] = toasts()
    run["run_requests"] = [l for l in ui.requests(260) if "/run" in l or "/executions" in l]
    # poll executions for this task
    seen = {}
    for _ in range(20):
        st, ex = pweb.j("GET", "/api/executions?task_id=" + t0["id"] + "&limit=50")
        rows = pweb.lst(ex)
        for r in rows:
            seen[r["id"]] = {"id": r["id"], "node_id": r.get("node_id"), "status": r.get("status"),
                             "exit_code": r.get("exit_code"), "trigger_type": r.get("trigger_type"),
                             "stdout": (r.get("stdout") or "")[:120]}
        if rows and all(r["status"] in ("SUCCESS", "FAILED", "TIMED_OUT", "CANCELED", "SKIPPED", "BLOCKED") for r in rows):
            break
        time.sleep(1.5)
    run["executions"] = list(seen.values())
    run["distinct_nodes"] = sorted({v["node_id"] for v in seen.values() if v["node_id"]})
    run["marker_post"] = S1.write_marker(PREFIX + "-post-run\n")
    ui.shot("E2E-02-multinode-executions.png", full=True)
    RES["E2E-02_run"] = run

    # ---- P03-02 running executions on the dashboard ----
    ui.open_("/", 3.0)
    RES["P03-02_dashboard_running"] = ui.js(r'''(() => {
      const cards=Array.from(document.querySelectorAll('main div[data-slot=card], main section'));
      const c=cards.find(x=>x.innerText.includes('正在执行'));
      return c?{text:c.innerText.slice(0,300), links:Array.from(c.querySelectorAll('a')).map(a=>({t:a.innerText.trim(),h:a.getAttribute('href')}))}:null})()''')
    RES["P03-02_view_all_href"] = ui.js(r'''(() => {const a=Array.from(document.querySelectorAll('a')).find(x=>/正在执行/.test((x.closest('div[data-slot=card],section')||{}).innerText||'')&&/查看全部/.test(x.innerText));return a?a.getAttribute('href'):null})()''')
    ui.shot("P03-02-dashboard.png", full=True)

    # ---- P17P18-03: executions list shows the new rows with status/exit code ----
    ui.open_("/executions", 2.5)
    RES["P17P18-03_list"] = ui.js(r'''(() => {
      const t=document.querySelector('table');
      return {headers:Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()),
        rows:Array.from(t.querySelectorAll('tbody tr')).slice(0,6).map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim()))}})()''')
    ui.shot("P17P18-03-executions-after-run.png", full=True)
    ui.write_ev("P09P11-E2E02-X08-P03-02-task-run.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:5000])


main()
