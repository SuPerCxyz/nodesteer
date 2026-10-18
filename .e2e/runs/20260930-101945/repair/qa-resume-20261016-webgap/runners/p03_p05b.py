#!/usr/bin/env python3
"""P03-02 / P05B-08 / P05B-10：运行中执行链接、节点详情调度链接、执行表任务链接。

依赖 owned fixture（native-2 上的长跑任务 + schedule），在 RUNNING 窗口内捕获。
"""
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
SCHED_ID = "bea1c6eb-4d79-4712-a0cd-9876d4892ae9"
tok = login()
OUT = {"case_ids": ["P03-02", "P05B-08", "P05B-10"], "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1200])
    return obj


# ---- API view of the running execution
st, ex = j("GET", "/api/executions", tok)
mine = [e for e in as_list(ex) if e.get("task_id") == TASK_ID]
mine.sort(key=lambda e: e.get("start_time") or "", reverse=True)
step("owned_executions", [{"id": e["id"], "status": e["status"], "node_id": e["node_id"],
                           "start": e.get("start_time"), "end": e.get("end_time"),
                           "stdout_len": len(e.get("stdout") or "")} for e in mine[:5]])

# ---- P03-02: dashboard running executions
ui.run("network", "requests", "--clear")
ui.open_("/", wait=2.5)
dash = ui.js(r'''(() => {
  const cards = [...document.querySelectorAll('div')].filter(d => (d.innerText||'').includes('正在执行的任务') && d.querySelectorAll('a').length > 0).slice(0,3);
  const links = [...document.querySelectorAll('a')].map(a => ({href: a.getAttribute('href'), text: (a.innerText||'').trim().slice(0,60)}));
  const runningCard = [...document.querySelectorAll('div')].map(d=>d.innerText).filter(t => t && t.includes('运行中') && t.length < 120).slice(0,4);
  return {
    url: location.pathname,
    running_card_texts: runningCard,
    exec_links: links.filter(l => (l.href||'').includes('/executions')),
    view_all_links: links.filter(l => (l.href||'').includes('/executions') && (l.text||'').includes('查看全部')),
    dotask_texts: [...document.querySelectorAll('*')].filter(e=>e.children.length===0 && (e.textContent||'').includes('正在执行的任务')).map(e=>e.textContent.trim()).slice(0,3),
  };
})()''')
step("p03-02-dashboard", dash)
ui.shot("P03-02-01-dashboard-running.png")

# ---- P05B-10 + P05B-08: node detail
ui.open_("/nodes/" + NATIVE2, wait=2.5)
ui.shot("P05B-10-01-node-detail.png")
node = ui.js(r'''(async () => {
  const h1 = (document.querySelector('h1')||{}).innerText || null;
  const links = [...document.querySelectorAll('a')].map(a => ({href: a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,80)}));
  const execRows = [...document.querySelectorAll('table')].map(tb => ({
    head: [...tb.querySelectorAll('thead th')].map(th=>th.innerText.trim()),
    rows: [...tb.querySelectorAll('tbody tr')].slice(0,6).map(tr => ({
      text: tr.innerText.replace(/\n/g,' | ').slice(0,300),
      links: [...tr.querySelectorAll('a')].map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,80)}))
    }))
  }));
  const schedSection = [...document.querySelectorAll('section,div')].filter(d=>{const t=d.innerText||''; return t.includes('调度') && d.querySelectorAll('a').length>0;}).slice(0,2).map(d=>d.innerText.slice(0,400));
  return {h1, url: location.pathname, execRows, schedSection,
          task_links: links.filter(l => (l.href||'').includes('/tasks/'))};
})()''')
step("p05b-10-node-detail-tables", node)

# click the owned task name link in the execution table -> task detail
clk = ui.js(r'''(async () => {
  const want = %s;
  const cands = [...document.querySelectorAll('table a')].filter(a => ((a.innerText||'').includes('qa-webgap-6456ada2') || (a.getAttribute('href')||'').includes(%s)));
  if (!cands.length) return {clicked:false, reason:'no owned task link in tables'};
  const a = cands[0];
  a.scrollIntoView({block:'center'});
  const href = a.getAttribute('href'); const text = (a.innerText||'').trim();
  a.click();
  await new Promise(r=>setTimeout(r,1500));
  return {clicked:true, href, text, url_after: location.pathname + location.search};
})()''' % (json.dumps(PFX), json.dumps(TASK_ID)))
step("p05b-10-click-task-link", clk)
ui.shot("P05B-10-02-after-task-link-click.png")
time.sleep(0.5)
td = ui.js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  body: document.body.innerText.slice(0,600)}))()''')
step("p05b-10-task-detail", td)

# back to node detail, then click the schedule row link (P05B-08)
ui.open_("/nodes/" + NATIVE2, wait=2.5)
sched_href = ui.js(r'''(async () => {
  const cands = [...document.querySelectorAll('a')].filter(a => (a.getAttribute('href')||'').includes('/schedules/'));
  return {count: cands.length, links: cands.slice(0,10).map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,80)}))};
})()''')
step("p05b-08-node-detail-schedule-links", sched_href)
ui.shot("P05B-08-01-node-detail-schedules.png")
clk2 = ui.js(r'''(async () => {
  const a = [...document.querySelectorAll('a')].find(a => (a.getAttribute('href')||'').includes(%s));
  if (!a) return {clicked:false, reason:'owned schedule link not found'};
  a.scrollIntoView({block:'center'});
  const href = a.getAttribute('href'); const text=(a.innerText||'').trim();
  a.click();
  await new Promise(r=>setTimeout(r,1500));
  return {clicked:true, href, text, url_after: location.pathname + location.search};
})()''' % json.dumps(SCHED_ID))
step("p05b-08-click-schedule-link", clk2)
ui.shot("P05B-08-02-schedule-detail.png")
time.sleep(0.4)
sd = ui.js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  body: document.body.innerText.slice(0,700)}))()''')
step("p05b-08-schedule-detail", sd)

OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(ui.BATCH) / "evidence" / "P03-P05B-node-links.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
