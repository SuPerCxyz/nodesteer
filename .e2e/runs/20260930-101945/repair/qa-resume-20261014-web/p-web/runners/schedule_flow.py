#!/usr/bin/env python3
"""P12P13-02 owned schedule: list link -> detail, misfire display, delete confirm cancel/confirm."""
import json
import re
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pweb  # noqa: E402
import ui  # noqa: E402

PREFIX = pweb.PREFIX
RES = {}


def snap():
    return ui.run("snapshot", "-i")[0]


def row_idx(name):
    v = ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));return rows.findIndex(x=>x.innerText.includes(%s))})()" % json.dumps(name))
    try:
        return int(str(v).strip().strip('"'))
    except Exception:
        return -1


def main():
    st, tasks = pweb.j("GET", "/api/tasks")
    task = next((t for t in pweb.lst(tasks) if t["name"].startswith(PREFIX)), None)
    if not task:
        print("no owned task")
        return
    code, sch = pweb.j("POST", "/api/schedules", {
        "task_id": task["id"], "type": "interval", "interval_sec": 3600, "timezone": "Asia/Shanghai",
        "execution_owner": "agent", "offline_policy": "allow_offline", "misfire_policy": "run_once",
        "enabled": True})
    RES["schedule_created"] = {"http": code, "id": sch.get("id"), "revision": sch.get("revision"),
                               "type": sch.get("type"), "interval_sec": sch.get("interval_sec"),
                               "timezone": sch.get("timezone"), "execution_owner": sch.get("execution_owner"),
                               "offline_policy": sch.get("offline_policy"),
                               "misfire_policy": sch.get("misfire_policy"), "enabled": sch.get("enabled")}
    sid = sch["id"]

    # list -> name link -> detail
    ui.open_("/schedules", 2.5)
    RES["list"] = ui.js(r'''(() => {const t=document.querySelector('table');
      return {headers:t?Array.from(t.querySelectorAll('thead th')).map(x=>x.innerText.trim()):[],
        rows:t?Array.from(t.querySelectorAll('tbody tr')).slice(0,6).map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim())):[],
        first_link:(()=>{const a=document.querySelector('table tbody tr a[href^="/schedules/"]');return a?a.getAttribute('href'):null})()}})()''')
    ui.shot("P12P13-02-schedules-list.png", full=True)
    ui.run("click", "a[href='/schedules/%s']" % sid)
    time.sleep(2.5)
    RES["detail"] = ui.js(r'''(() => {const main=document.querySelector('main')||document.body;
      return {url:location.pathname, h1:(document.querySelector('h1')||{}).innerText||null,
        labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        combos:Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,30)),
        inputs:Array.from(main.querySelectorAll('input')).map(i=>({type:i.type,v:(i.value||'').slice(0,30)})),
        text:main.innerText.slice(0,700)}})()''')
    ui.shot("P12P13-02-schedule-detail.png", full=True)

    # delete: confirm dialog cancel (zero write) then confirm
    d = {}
    ui.open_("/schedules", 2.2)
    i = row_idx("qa-pweb")
    d["row_index"] = i
    if i >= 0:
        ui.run("click", "table tbody tr:nth-child(%d) button[aria-label='操作']" % (i + 1))
        time.sleep(1.2)
        d["menu_items"] = ui.js("(()=>Array.from(document.querySelectorAll('[role=menuitem]')).map(m=>m.innerText.trim()))()")
        ui.shot("G04-02-schedule-row-actions.png")
        ui.run("find", "role", "menuitem", "click", "--name", "删除")
        time.sleep(1.4)
        d["dialog"] = ui.js(r'''(() => {const x=document.querySelector('[role=alertdialog],[role=dialog]');
          return x?{role:x.getAttribute('role'),text:x.innerText.replace(/\n+/g,' | ').slice(0,300)}:null})()''')
        # cancel
        idx = ui.js(r'''(() => {const x=document.querySelector('[role=alertdialog],[role=dialog]');if(!x)return -1;
          return Array.from(x.querySelectorAll('button')).findIndex(b=>b.innerText.trim()==='取消')})()''')
        try:
            idx = int(str(idx).strip().strip('"'))
        except Exception:
            idx = -1
        if idx >= 0:
            ui.run("click", "[role=alertdialog] button:nth-of-type(%d), [role=dialog] button:nth-of-type(%d)" % (idx + 1, idx + 1))
            time.sleep(1.5)
        d["after_cancel_get"] = pweb._call("GET", "/api/schedules/" + sid)[0]
        d["cancel_writes"] = [l for l in ui.requests(300) if "DELETE" in l and "/api/schedules" in l]
        ui.shot("G04-02-schedule-after-cancel.png", full=True)
        # confirm
        ui.open_("/schedules", 2.0)
        i2 = row_idx("qa-pweb")
        ui.run("click", "table tbody tr:nth-child(%d) button[aria-label='操作']" % (i2 + 1))
        time.sleep(1.2)
        ui.run("find", "role", "menuitem", "click", "--name", "删除")
        time.sleep(1.4)
        ui.run("find", "role", "button", "click", "--name", "删除")
        time.sleep(2.0)
        d["after_confirm_get"] = pweb._call("GET", "/api/schedules/" + sid)[0]
        d["confirm_writes"] = [l for l in ui.requests(400) if "DELETE" in l and "/api/schedules" in l]
        d["toasts"] = ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast]'));return t.map(x=>x.innerText.trim())})()''')
        ui.shot("G04-02-schedule-after-delete.png", full=True)
    RES["P12P13-02_delete"] = d
    ui.write_ev("P12P13-02-G04-02-schedule.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:3500])


main()
