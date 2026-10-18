#!/usr/bin/env python3
"""P05B-04 capabilities sample, X05 schedule-from-task lock, P12P13-04/05 interval & one-time validation."""
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


def ref_of(text, role="button"):
    for l in snap().splitlines():
        if '%s "%s"' % (role, text) in l and "ref=" in l:
            return re.search(r"ref=(e\d+)", l).group(1)


def combo(label, option=None):
    s = snap()
    m = re.search(r'combobox "%s" \[expanded=false, ref=(e\d+)\]' % re.escape(label), s)
    if not m:
        return {"ok": False, "reason": "combo missing"}
    ui.run("click", "@" + m.group(1))
    time.sleep(0.9)
    opts = [re.sub(r"^-\s*option\s*", "", l.strip()).strip() for l in snap().splitlines() if "- option" in l]
    if option is None:
        ui.run("press", "Escape")
        time.sleep(0.4)
        return {"ok": True, "options": opts}
    tgt = [l for l in snap().splitlines() if "- option" in l and option in l]
    if not tgt:
        ui.run("press", "Escape")
        return {"ok": False, "options": opts}
    ui.run("click", "@" + re.search(r"ref=(e\d+)", tgt[0]).group(1))
    time.sleep(1.1)
    return {"ok": True}


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
      return {ok:true, tag:el.tagName, len:el.value.length};
    })()''' % (json.dumps(label), json.dumps(value)))


def editor():
    return ui.js(r'''(() => {
      const main=document.querySelector('main')||document.body;
      return {url:location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
        labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        combos:Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,40)),
        inputs:Array.from(main.querySelectorAll('input')).map(i=>({ph:i.placeholder,type:i.type,v:(i.value||'').slice(0,30)})),
        text:main.innerText.slice(0,700)};})()''')


def toasts():
    return ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast],[data-sonner-toaster] li'));
      return {n:t.length,texts:t.map(x=>x.innerText.trim())}})()''')


def capabilities():
    print("[P05B-04] capabilities enabled/disabled samples")
    st, nodes = pweb.j("GET", "/api/nodes")
    ours = [n for n in pweb.lst(nodes) if n["hostname"].startswith(PREFIX)]
    api = {n["hostname"]: {"mode": n["deployment_mode"], "status": n["status"], "capabilities": n["capabilities"]}
           for n in ours}
    ui_samples = {}
    for n in ours:
        ui.open_("/agents/" + n["id"], 2.5)
        ui_samples[n["hostname"]] = ui.js(r'''(() => {
          const main=document.querySelector('main')||document.body;
          const rows=Array.from(document.querySelectorAll('main [role=switch],main [role=checkbox]')).map(e=>({role:e.getAttribute('role'),checked:e.getAttribute('aria-checked'),label:(e.closest('div,label')||{}).innerText}));
          const cand=Array.from(document.querySelectorAll('main *')).filter(e=>e.children.length===0 && /^(true|false|启用|禁用|已启用|未启用)$/.test((e.textContent||'').trim())).length;
          const section=(main.innerText.match(/能力[\s\S]{0,400}/)||[])[0];
          return {switch_like:rows.slice(0,12), true_false_nodes:cand, capability_section:section};})()''')
        ui.shot("P05B-04-%s-capabilities.png" % n["hostname"].split("-")[-1], full=True)
    RES["P05B-04"] = {"api": api, "ui": ui_samples}


def x05_schedule_lock(task):
    print("[X05] schedule from task detail: task_id preselected + locked, cancel = zero write")
    out = {}
    ui.open_("/tasks/" + task["id"], 2.5)
    link = ui.js(r'''(() => {const a=Array.from(document.querySelectorAll('a[href^="/schedules/new"]'));return a.length?a[0].getAttribute('href'):null})()''')
    out["entry_href"] = (str(link).strip().strip('"') if link else None)
    if out["entry_href"]:
        ui.run("click", "a[href='%s']" % out["entry_href"])
        time.sleep(2.2)
        out["editor"] = editor()
        out["locked_banner"] = ui.js(r'''(() => {const t=(document.querySelector('main')||document.body).innerText;
          return {banner:(t.match(/由任务详情创建[^\n]{0,30}/)||[])[0]||null,
                  task_control_disabled: Array.from(document.querySelectorAll('main button[disabled],[role=combobox][aria-disabled=true]')).length};})()''')
        ui.shot("X05-schedule-from-task-locked.png", full=True)
        # cancel -> zero write
        for label in ("取消", "返回"):
            if ref_of(label, "link") or ref_of(label, "button"):
                break
        ui.run("find", "role", "button", "click", "--name", "取消")
        time.sleep(1.6)
        out["after_cancel_url"] = ui.run("get", "url")[0].strip()
        out["schedule_writes"] = [l for l in ui.requests(200) if "/api/schedules" in l and ("POST" in l or "PUT" in l or "DELETE" in l)]
    RES["X05"] = out


def schedule_types(task):
    print("[P12P13-04/05/X04/X10] interval & one-time validation")
    out = {}
    # interval
    ui.open_("/schedules/new?task_id=" + task["id"], 2.5)
    out["type_options"] = combo("类型")
    combo("类型", "间隔")
    out["interval_form"] = editor()
    out["interval_field_probe"] = []
    for v in ("0", "-1", "1.5", "999999999"):
        set_label("间隔", v)
        time.sleep(0.4)
        out["interval_field_probe"].append({"value": v, "state": ui.js(r'''(() => {const main=document.querySelector('main')||document.body;
          return {text:(main.innerText.match(/[^\n]{0,60}(不能|必须|无效|至少|大于)[^\n]{0,30}/g)||[])[0]||null,
                  aria_invalid:Array.from(document.querySelectorAll('[aria-invalid=true]')).length,
                  save_disabled:(()=>{const b=Array.from(document.querySelectorAll('main button')).find(x=>x.innerText.trim()==='保存');return b?b.disabled:null})()}})()''')})
    ui.shot("P12P13-04-schedule-interval.png", full=True)
    # one-time
    combo("类型", "一次性")
    out["onetime_form"] = editor()
    r = set_label("运行时间", "2020-01-01T00:00")
    out["onetime_past_set"] = r
    time.sleep(0.5)
    out["onetime_past"] = ui.js(r'''(() => {const main=document.querySelector('main')||document.body;
      return {text:(main.innerText.match(/[^\n]{0,60}(过去|不能|必须|无效|未来)[^\n]{0,30}/g)||[]).slice(0,3),
              aria_invalid:Array.from(document.querySelectorAll('[aria-invalid=true]')).length,
              save_disabled:(()=>{const b=Array.from(document.querySelectorAll('main button')).find(x=>x.innerText.trim()==='保存');return b?b.disabled:null})()}})()''')
    ui.shot("P12P13-05-schedule-onetime-past.png", full=True)
    r2 = set_label("运行时间", "2030-01-01T00:00")
    out["onetime_future_set"] = r2
    time.sleep(0.5)
    out["onetime_future"] = ui.js(r'''(() => {const main=document.querySelector('main')||document.body;
      return {aria_invalid:Array.from(document.querySelectorAll('[aria-invalid=true]')).length,
              save_disabled:(()=>{const b=Array.from(document.querySelectorAll('main button')).find(x=>x.innerText.trim()==='保存');return b?b.disabled:null})(),
              timezone_combo:Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim()).slice(0,6)}})()''')
    # owner / offline / misfire combos
    out["owner_options"] = combo("执行方")
    out["offline_options"] = combo("离线策略")
    out["misfire_options"] = combo("漏触发策略")
    ui.shot("P12P13-06-07-08-schedule-combos.png", full=True)
    # cancel without saving
    ui.run("find", "role", "button", "click", "--name", "取消")
    time.sleep(1.5)
    out["writes"] = [l for l in ui.requests(250) if "/api/schedules" in l and ("POST" in l or "PUT" in l)]
    RES["P12P13-04-05-06-07-08-X04-X10"] = out


def main():
    capabilities()
    st, tasks = pweb.j("GET", "/api/tasks")
    mine = [t for t in pweb.lst(tasks) if t["name"].startswith(PREFIX)]
    if mine:
        x05_schedule_lock(mine[0])
        schedule_types(mine[0])
    ui.write_ev("P05B-04-X05-P12P13-schedule.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:6000])


main()
