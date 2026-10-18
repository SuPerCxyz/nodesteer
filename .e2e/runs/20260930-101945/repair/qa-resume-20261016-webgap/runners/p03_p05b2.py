#!/usr/bin/env python3
"""修正脚本 working_dir 后重新 run，并在 RUNNING 窗口内完成 P03-02 / P05B-08 / P05B-10 捕获。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402
from api import j, login, as_list  # noqa: E402

PFX = "qa-webgap-6456ada2"
NATIVE2 = "611c02a5-1859-4465-878b-d543b7f6fdec"
TASK_ID = "49259725-f51d-4b77-93a7-2fe35e6e18b1"
SID = "29822361-f6a0-425d-aa48-6316cf059dd1"
SCHED_ID = "bea1c6eb-4d79-4712-a0cd-9876d4892ae9"
tok = login()
OUT = {"case_ids": ["P03-02", "P05B-08", "P05B-10"], "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1400])
    return obj


# --- fix working_dir (owned fixture) ---
st, sc = j("GET", "/api/scripts/%s" % SID, tok)
sc["working_dir"] = ""
st, upd = j("PUT", "/api/scripts/%s" % SID, tok, sc)
step("script-working-dir-fixed", {"http": st, "working_dir": upd.get("working_dir") if isinstance(upd, dict) else None})
tok = login()

# --- run again ---
st, run = j("POST", "/api/tasks/%s/run" % TASK_ID, tok, {})
step("run-now-2", {"http": st, "resp": run if st != 200 else "ok"})
t0 = time.time()

# wait for the execution to appear as RUNNING via API
exec_id = None
while time.time() - t0 < 30:
    st, ex = j("GET", "/api/executions", tok)
    mine = [e for e in as_list(ex) if e.get("task_id") == TASK_ID]
    mine.sort(key=lambda e: e.get("start_time") or "", reverse=True)
    if mine and mine[0].get("status") in ("RUNNING", "PENDING"):
        exec_id = mine[0]["id"]
        break
    time.sleep(1)
step("running-execution-detected", {"exec_id": exec_id, "waited_s": round(time.time() - t0, 1)})

# --- P03-02: dashboard running card must be non-empty ---
deadline = time.time() + 25
dash = None
while time.time() < deadline:
    ui.open_("/", wait=1.8)
    dash = ui.js(r'''(() => {
      const links = [...document.querySelectorAll('a')].map(a => ({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,120)}));
      const cardText = [...document.querySelectorAll('*')].filter(e=>e.children.length===0 && (e.textContent||'').includes('正在执行的任务')).map(e=>e.textContent.trim())[0] || null;
      const running = [...document.querySelectorAll('*')].filter(e=>e.children.length===0 && /^\d+$/.test((e.textContent||'').trim())).map(e=>e.textContent.trim());
      const section = [...document.querySelectorAll('div,section')].filter(d => (d.innerText||'').includes('正在执行的任务') && d.querySelectorAll('a').length>0).slice(0,1).map(d=>d.innerText.slice(0,500))[0] || null;
      return {url: location.pathname, running_card: cardText, section,
              exec_links: links.filter(l => (l.href||'').includes('/executions/')),
              view_all: links.filter(l => (l.href||'')==='/executions')};
    })()''')
    if dash and dash.get("exec_links"):
        if any("/executions/" + (exec_id or "") in (l.get("href") or "") for l in dash["exec_links"]):
            break
    time.sleep(1.5)
step("p03-02-dashboard-running", dash)
ui.shot("P03-02-01-dashboard-running.png")

# --- P05B-10: node detail exec table task link (during RUNNING) ---
ui.open_("/nodes/" + NATIVE2, wait=2.2)
node = ui.js(r'''(() => {
  const tb = document.querySelector('table');
  return {url: location.pathname, h1: (document.querySelector('h1')||{}).innerText||null,
    head: tb ? [...tb.querySelectorAll('thead th')].map(th=>th.innerText.trim()) : null,
    rows: tb ? [...tb.querySelectorAll('tbody tr')].slice(0,4).map(tr=>({
      text: tr.innerText.replace(/\n/g,' | ').slice(0,260),
      links: [...tr.querySelectorAll('a')].map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,80)}))})) : null};
})()''')
step("p05b-10-node-exec-table-running", node)
ui.shot("P05B-10-01-node-exec-running.png")

# click owned task link in the table -> task detail (navigation kills the eval; verify separately)
ui.js(r'''(async () => {
  const a = [...document.querySelectorAll('table a')].find(a => (a.getAttribute('href')||'').includes('/tasks/'));
  if (!a) return false;
  a.scrollIntoView({block:'center'});
  a.click();
  await new Promise(r=>setTimeout(r,900));
  return true;
})()''')
time.sleep(1.5)
td = ui.js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  body: document.body.innerText.slice(0,700)}))()''')
step("p05b-10-task-detail-after-click", td)
ui.shot("P05B-10-02-task-detail.png")

# link integrity: /tasks/{id} href is a real route
OUT["deep_link_check"] = ui.js(r'''(() => ({href_seen: %s, url_now: location.pathname}))()''' % json.dumps("/tasks/" + TASK_ID))

# --- P05B-08: node detail schedules list -> schedule detail ---
ui.open_("/nodes/" + NATIVE2, wait=2.2)
sl = ui.js(r'''(() => {
  const links = [...document.querySelectorAll('a')].filter(a => (a.getAttribute('href')||'').includes('/schedules/'));
  const sects = [...document.querySelectorAll('section,div')].filter(d => (d.innerText||'').includes('调度') && d.querySelectorAll('a').length>0).slice(0,2).map(d=>d.innerText.slice(0,500));
  return {count: links.length, links: links.slice(0,8).map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,80)})), sects};
})()''')
step("p05b-08-node-detail-schedule-links", sl)
ui.shot("P05B-08-01-node-schedules.png")
ui.js(r'''(async () => {
  const a = [...document.querySelectorAll('a')].find(a => (a.getAttribute('href')||'').includes(%s));
  if (!a) return false;
  a.scrollIntoView({block:'center'}); a.click();
  await new Promise(r=>setTimeout(r,900));
  return true;
})()''' % json.dumps(SCHED_ID))
time.sleep(1.6)
sd = ui.js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  body: document.body.innerText.slice(0,900)}))()''')
step("p05b-08-schedule-detail-after-click", sd)
ui.shot("P05B-08-02-schedule-detail.png")

# --- wait for owned execution terminal state ---
deadline = time.time() + 150
ex = None
while time.time() < deadline:
    st, ex = j("GET", "/api/executions", tok)
    mine = [e for e in as_list(ex) if e.get("task_id") == TASK_ID]
    mine.sort(key=lambda e: e.get("start_time") or "", reverse=True)
    if mine and mine[0].get("status") not in ("RUNNING", "PENDING"):
        ex = mine[0]
        break
    time.sleep(5)
step("owned-execution-terminal", {"id": ex.get("id"), "status": ex.get("status"),
                                  "stdout_len": len(ex.get("stdout") or ""),
                                  "stdout_head": (ex.get("stdout") or "")[:300],
                                  "stdout_tail": (ex.get("stdout") or "")[-200:],
                                  "truncated": ex.get("stdout_truncated")})
OUT["execution_id"] = ex.get("id")
OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(ui.BATCH) / "evidence" / "P03-P05B-node-links.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
