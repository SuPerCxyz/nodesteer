#!/usr/bin/env python3
"""X20 补充判定：SHA 单元渲染、脚本编辑器/调度编辑器 480 溢出归因、复制失败后的文本选择回退。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

PFX = "qa-webgap-6456ada2"
TASK_ID = "49259725-f51d-4b77-93a7-2fe35e6e18b1"
SID = "29822361-f6a0-425d-aa48-6316cf059dd1"
SCHED_ID = "bea1c6eb-4d79-4712-a0cd-9876d4892ae9"
EXEC_ID = "c5cd0511-e20e-4dfc-a4f6-3fe02b511175"
OUT = {"case_id": "X20", "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1000])
    return obj


def safe_js(code):
    try:
        return ui.js(code)
    except Exception as e:
        return {"eval_error": str(e)[:200]}


def vp(w, h=900):
    ui.run("set", "viewport", str(w), str(h))
    time.sleep(1.0)


# 1) artifacts SHA 单元
vp(1280)
ui.open_("/artifacts", wait=2.2)
sha = safe_js(r'''(() => {
  const out = [];
  document.querySelectorAll('td,span,div').forEach(e => {
    const t = (e.textContent||'').trim();
    if (/^[0-9a-f]{40,}$/.test(t)) {
      const cs = getComputedStyle(e);
      out.push({tag:e.tagName, len:t.length, cls:(e.className||'').toString().slice(0,60),
                title: e.getAttribute('title'), truncate: cs.textOverflow, overflow: cs.overflow,
                sw:e.scrollWidth, cw:e.clientWidth,
                parentCls:(e.parentElement.className||'').toString().slice(0,60),
                parentBtns: e.parentElement.querySelectorAll('button').length});
    }
  });
  return {sha_cells: out.slice(-4), copy_buttons: [...document.querySelectorAll('button[aria-label]')].map(b=>b.getAttribute('aria-label'))};
})()''')
step("x20-artifacts-sha-cell", sha)
ui.shot("X20-16-artifacts-sha.png")

# 2) scripts/new @480 归因
vp(480)
ui.open_("/scripts/new", wait=2.2)
sn = safe_js(r'''(() => {
  const chain = [];
  let el = document.querySelector('input');
  while (el && el !== document.body) {
    const cs = getComputedStyle(el);
    chain.push({tag: el.tagName, cls:(el.className||'').toString().slice(0,70), w: Math.round(el.getBoundingClientRect().width),
                minW: cs.minWidth, maxW: cs.maxWidth, overflowX: cs.overflowX, display: cs.display});
    el = el.parentElement;
  }
  return {innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth, chain: chain.slice(0,9)};
})()''')
step("x20-scripts-new-480-attribution", sn)
ui.shot("X20-17-scripts-new-480.png")

# 3) schedules/new @480：选择长名任务前后
ui.open_("/schedules/new", wait=2.2)
before = safe_js(r'''(() => ({innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth}))()''')
sel = safe_js(r'''(async () => {
  const trig = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '选择任务...');
  if (!trig) return {ok:false, opts:[...document.querySelectorAll('button')].map(b=>b.innerText.trim().slice(0,20))};
  trig.click();
  await new Promise(r=>setTimeout(r,450));
  const opts = [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim());
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').includes('qa-webgap-6456ada2'));
  if (!opt) return {ok:false, opts: opts.slice(0,8)};
  opt.click();
  await new Promise(r=>setTimeout(r,700));
  return {ok:true, opt_text: (opt.innerText||'').trim()};
})()''')
time.sleep(0.6)
after = safe_js(r'''(() => {
  const chain = [];
  let el = [...document.querySelectorAll('button')].find(b => (b.innerText||'').includes('qa-webgap'));
  const triggerRect = el ? Math.round(el.getBoundingClientRect().width) : null;
  while (el && el !== document.body) {
    const cs = getComputedStyle(el);
    chain.push({tag: el.tagName, cls:(el.className||'').toString().slice(0,70), w: Math.round(el.getBoundingClientRect().width),
                minW: cs.minWidth, maxW: cs.maxWidth, ws: cs.whiteSpace, overflow: cs.overflow});
    el = el.parentElement;
  }
  return {innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth, trigger_w: triggerRect, chain: chain.slice(0,7)};
})()''')
step("x20-schedules-new-480-long-name-attribution", {"before_select": before, "select": sel, "after_select": after})
ui.shot("X20-18-schedules-new-480-long-name.png")

# 4) 复制失败后的文本选择回退
vp(1280)
ui.open_("/executions/" + EXEC_ID, wait=2.5)
sel_fallback = safe_js(r'''(async () => {
  const before_len = (window.getSelection()||{toString:()=>''}).toString().length;
  const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').includes('复制日志'));
  if (!b) return {ok:false};
  b.scrollIntoView({block:'center'}); b.click();
  await new Promise(r=>setTimeout(r,1000));
  const toasts = [...document.querySelectorAll('[data-sonner-toast]')].map(e=>(e.innerText||'').trim());
  const sel = (window.getSelection()||{toString:()=>''}).toString();
  return {ok:true, isSecureContext: window.isSecureContext, toasts, selection_before: before_len,
          selection_after_len: sel.length, selection_head: sel.slice(0,60)};
})()''')
step("x20-copy-logs-selection-fallback", sel_fallback)
ui.shot("X20-19-copy-logs-selection.png")

OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(ui.BATCH) / "evidence" / "X20-attribution.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
