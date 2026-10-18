#!/usr/bin/env python3
"""重新执行 owned 脚本（长日志 RUNNING 窗口）并在窗口内捕获 P03-02 / P05B-10 / P05B-08。"""
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
BASE_DIR = "/var/lib/" + PFX
SRC = BASE_DIR + "/深中文目录名称用于长值布局验证/very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789-abcdefghijklmnopqrstuvwxyz.bin"
tok = login()
OUT = {"case_ids": ["P03-02", "P05B-08", "P05B-10", "X20"], "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1000])
    return obj


def safe_js(code):
    try:
        return ui.js(code)
    except Exception as e:
        return {"eval_error": str(e)[:200]}


SCRIPT = """#!/bin/sh
BASE="__BASE__"
SRC="__SRC__"
mkdir -p "$(dirname "$SRC")"
if [ ! -f "$SRC" ]; then head -c 1048576 /dev/urandom > "$SRC"; fi
chmod 644 "$SRC"
echo "SRC=$SRC"
ls -l "$SRC"
sha256sum "$SRC"
echo "param01=${NODESTEER_PARAM_qa_webgap_long_parameter_name_01:-<not-injected>}"
i=1
while [ $i -le 3000 ]; do
  printf 'qa-webgap-6456ada2 超长日志行 %05d 填充=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\n' "$i"
  i=$((i+1))
done
echo "log-line-count=3000"
sleep 70
echo "qa-webgap-6456ada2 done"
""".replace("__BASE__", BASE_DIR).replace("__SRC__", SRC)

st, sc = j("GET", "/api/scripts/%s" % SID, tok)
sc["content"] = SCRIPT
st, upd = j("PUT", "/api/scripts/%s" % SID, tok, sc)
step("script-updated", {"http": st, "revision": upd.get("revision") if isinstance(upd, dict) else None})
tok = login()

params = {"qa_webgap_long_parameter_name_01": "参数值-非常长的中文与字母数字混排内容-0123456789-abcdefghijklmnopqrstuvwxyz-超长结束"}
st, run = j("POST", "/api/tasks/%s/run" % TASK_ID, tok, {"params": params})
step("run-now-3", {"http": st, "resp": run if st != 200 else "ok"})
t0 = time.time()
exec_id = None
last_status = None
while time.time() - t0 < 40:
    st, ex = j("GET", "/api/executions", tok)
    mine = [e for e in as_list(ex) if e.get("task_id") == TASK_ID]
    mine.sort(key=lambda e: e.get("start_time") or "", reverse=True)
    if mine:
        last_status = mine[0]["status"]
        if mine[0]["status"] in ("RUNNING", "PENDING"):
            exec_id = mine[0]["id"]
            break
    time.sleep(1.5)
step("running-execution", {"exec_id": exec_id, "last_status": last_status, "waited_s": round(time.time() - t0, 1)})

# --- P03-02 dashboard running row (non-empty) ---
dash = None
deadline = time.time() + 25
while time.time() < deadline:
    ui.open_("/", wait=1.6)
    dash = safe_js(r'''(() => {
      const links = [...document.querySelectorAll('a')].map(a => ({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,130)}));
      const text = document.body.innerText;
      const m = text.match(/(\d+)\s*\n?\s*正在执行的任务/);
      const card = [...document.querySelectorAll('*')].filter(e=>e.children.length===0 && (e.textContent||'').includes('正在执行的任务')).length;
      return {url: location.pathname, running_count_guess: m ? m[1] : null, card_present: card>0,
              empty_msg: text.includes('当前没有正在执行的任务'),
              running_section: (text.split('正在执行的任务')[1]||'').slice(0,300),
              exec_links: links.filter(l => (l.href||'').includes('/executions/')).slice(0,6)};
    })()''')
    if dash and isinstance(dash, dict) and exec_id and any(exec_id in (l.get("href") or "") for l in dash.get("exec_links", [])):
        break
    time.sleep(1.5)
step("p03-02-dashboard-running", dash)
ui.shot("P03-02-01-dashboard-running.png")

# --- P05B-10 node detail exec table during RUNNING ---
ui.open_("/nodes/" + NATIVE2, wait=2.0)
node = safe_js(r'''(() => {
  const tb = document.querySelector('table');
  return {url: location.pathname, h1:(document.querySelector('h1')||{}).innerText||null,
    head: tb ? [...tb.querySelectorAll('thead th')].map(th=>th.innerText.trim()) : null,
    rows: tb ? [...tb.querySelectorAll('tbody tr')].slice(0,3).map(tr=>({text: tr.innerText.replace(/\n/g,' | ').slice(0,240),
      links: [...tr.querySelectorAll('a')].map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,70)}))})) : null};
})()''')
step("p05b-10-node-exec-table-running", node)
ui.shot("P05B-10-01-node-exec-running.png")

# click owned task link -> task detail
click1 = safe_js(r'''(async () => {
  const a = [...document.querySelectorAll('table a')].find(a => (a.getAttribute('href')||'').includes('/tasks/'));
  if (!a) return {clicked:false};
  const href = a.getAttribute('href');
  a.scrollIntoView({block:'center'}); a.click();
  await new Promise(r=>setTimeout(r,600));
  return {clicked:true, href};
})()''')
time.sleep(1.8)
td = safe_js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  has_task_name: document.body.innerText.includes('qa-webgap-6456ada2-任务长名称'),
  body: document.body.innerText.slice(0,500)}))()''')
step("p05b-10-task-link-click", {"click": click1, "after": td})
ui.shot("P05B-10-02-task-detail.png")

# --- P05B-08 node detail schedules -> schedule detail ---
ui.open_("/nodes/" + NATIVE2, wait=2.0)
sl = safe_js(r'''(() => {
  const links = [...document.querySelectorAll('a')].filter(a => (a.getAttribute('href')||'').includes('/schedules/'));
  return {count: links.length, links: links.slice(0,8).map(a=>({href:a.getAttribute('href'), text:(a.innerText||'').trim().slice(0,70)})),
          sched_section: (document.body.innerText.split('调度')[1]||'').slice(0,200)};
})()''')
step("p05b-08-node-detail-schedule-links", sl)
ui.shot("P05B-08-01-node-schedules.png")
click2 = safe_js(r'''(async () => {
  const a = [...document.querySelectorAll('a')].find(a => (a.getAttribute('href')||'').includes(%s));
  if (!a) return {clicked:false, reason:'owned schedule link not found'};
  const href = a.getAttribute('href');
  a.scrollIntoView({block:'center'}); a.click();
  await new Promise(r=>setTimeout(r,600));
  return {clicked:true, href};
})()''' % json.dumps(SCHED_ID))
time.sleep(1.8)
sd = safe_js(r'''(() => ({url: location.pathname+location.search, h1:(document.querySelector('h1')||{}).innerText||null,
  has_cron: document.body.innerText.includes('0 3 1 1 *'),
  body: document.body.innerText.slice(0,800)}))()''')
step("p05b-08-schedule-detail", {"click": click2, "after": sd})
ui.shot("P05B-08-02-schedule-detail.png")

# --- wait for terminal, then capture execution detail long log (X20) ---
deadline = time.time() + 200
ex = None
while time.time() < deadline:
    st, exl = j("GET", "/api/executions", tok)
    mine = [e for e in as_list(exl) if e.get("task_id") == TASK_ID]
    mine.sort(key=lambda e: e.get("start_time") or "", reverse=True)
    if mine and mine[0].get("status") not in ("RUNNING", "PENDING"):
        ex = mine[0]
        break
    time.sleep(5)
step("owned-execution-terminal", {"id": ex.get("id"), "status": ex.get("status"), "exit_code": ex.get("exit_code"),
                                  "stdout_len": len(ex.get("stdout") or ""),
                                  "stdout_head": (ex.get("stdout") or "")[:200],
                                  "stdout_tail": (ex.get("stdout") or "")[-120:],
                                  "truncated": ex.get("stdout_truncated")})
OUT["execution_id"] = ex.get("id")
OUT["execution_status"] = ex.get("status")

if ex.get("id"):
    ui.open_("/executions/" + ex["id"], wait=2.5)
    ui.shot("X20-01-exec-detail-long-log.png")
    det = safe_js(r'''(() => {
      const text = document.body.innerText;
      const pre = [...document.querySelectorAll('pre,code,textarea')].map(e=>({tag:e.tagName,
        scrollH:e.scrollHeight, clientH:e.clientHeight, scrollW:e.scrollWidth, clientW:e.clientWidth,
        lines:(e.innerText||'').split('\n').length, head:(e.innerText||'').slice(0,80)}));
      const overflow = [...document.querySelectorAll('*')].filter(e => e.scrollWidth > e.clientWidth + 8).slice(0,6).map(e=>({tag:e.tagName, cls:(e.className||'').toString().slice(0,60), sw:e.scrollWidth, cw:e.clientWidth}));
      return {url: location.pathname, bodyDocScrollW: document.body.scrollWidth, innerW: window.innerWidth,
              pre, horizontal_overflow_nodes: overflow,
              copy_logs_btn: [...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(t=>t.includes('复制'))};
    })()''')
    step("x20-exec-detail-layout", det)

OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(ui.BATCH) / "evidence" / "P03-P05B-X20-node-links.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
