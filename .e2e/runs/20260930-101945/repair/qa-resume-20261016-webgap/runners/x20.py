#!/usr/bin/env python3
"""X20 收口：长 ID/SHA/路径/参数 渲染与安全剪贴板；760/480 窄屏横向溢出测量。"""
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
EXEC_ID = "c5cd0511-e20e-4dfc-a4f6-3fe02b511175"
tok = login()
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


OVERFLOW = r'''(() => {
  const iw = window.innerWidth;
  const bad = [...document.querySelectorAll('body *')].filter(e => {
    const r = e.getBoundingClientRect();
    return r.width > 4 && (r.right > iw + 2 || r.left < -2) && getComputedStyle(e).position !== 'fixed';
  }).slice(0, 8).map(e => ({tag: e.tagName, cls: (e.className||'').toString().slice(0,70),
                            right: Math.round(e.getBoundingClientRect().right), w: Math.round(e.getBoundingClientRect().width),
                            text: (e.innerText||'').trim().slice(0,50)}));
  return {innerW: iw, docScrollW: document.documentElement.scrollWidth, bodyScrollW: document.body.scrollWidth,
          h_scroll: document.documentElement.scrollWidth > iw + 2, offending: bad};
})()'''

LONGVALS = r'''(() => {
  const out = {};
  const short = [];
  document.querySelectorAll('span,td,pre').forEach(e => {
    const t = (e.textContent||'').trim();
    if (t.length >= 24) {
      const title = e.getAttribute('title');
      const cs = getComputedStyle(e);
      short.push({tag:e.tagName, len:t.length, head:t.slice(0,28),
                  title_len: title ? title.length : null, title_matches: title ? (title === t || t === title.slice(0, t.length - 1) || t.endsWith('…')) : null,
                  truncate: cs.textOverflow, overflow: cs.overflow, ws: cs.whiteSpace,
                  scrollW: e.scrollWidth, clientW: e.clientWidth, textAlign: cs.textAlign});
    }
  });
  out.long_elements = short.slice(0, 14);
  out.count = short.length;
  return out;
})()'''

TOAST = r'''(() => [...document.querySelectorAll('[data-sonner-toast], [role=status], li[data-sonner-toast]')]
  .map(e => (e.innerText||'').trim()).slice(0,4))()'''


def viewport(w, h):
    ui.run("set", "viewport", str(w), str(h))
    time.sleep(1.0)


def audit(label, path, shot=None):
    ui.open_(path, wait=2.2)
    if shot:
        ui.shot(shot)
    ov = safe_js(OVERFLOW)
    lv = safe_js(LONGVALS)
    step(label, {"overflow": ov, "long_values": {"count": lv.get("count"), "sample": (lv.get("long_elements") or [])[:6]}})
    return {"overflow": ov, "long": lv}


def main():
    # 1) desktop 1280：长值渲染抽查
    viewport(1280, 900)
    audit("x20-artifacts-1280", "/artifacts", "X20-02-artifacts-1280.png")
    audit("x20-transfers-1280", "/transfers", "X20-03-transfers-1280.png")
    audit("x20-exec-detail-1280", "/executions/" + EXEC_ID, "X20-01-exec-detail-1280.png")
    audit("x20-script-detail-1280", "/scripts/" + SID, "X20-04-script-1280.png")
    audit("x20-task-detail-1280", "/tasks/" + TASK_ID, "X20-05-task-1280.png")

    # 2) 安全剪贴板：HTTP 非安全上下文下点击复制 → 应为「手动复制」提示，而不是「已复制」
    ui.open_("/artifacts", wait=2.2)
    ui.run("console", "--clear")
    copy_probe = safe_js(r'''(async () => {
      const btns = [...document.querySelectorAll('button[aria-label]')].filter(b => (b.getAttribute('aria-label')||'').includes('复制'));
      if (!btns.length) return {ok:false, reason:'no copy button'};
      const b = btns[0];
      const label = b.getAttribute('aria-label');
      b.scrollIntoView({block:'center'});
      b.click();
      await new Promise(r=>setTimeout(r,900));
      const toasts = [...document.querySelectorAll('[data-sonner-toast]')].map(e=>(e.innerText||'').trim());
      return {ok:true, label, isSecureContext: window.isSecureContext, toasts,
              execCommand: typeof document.execCommand};
    })()''')
    step("x20-copy-artifact-sha", copy_probe)
    ui.shot("X20-06-copy-manual-toast.png")

    # 长 ID 复制（执行详情页 ID 旁的复制按钮）
    ui.open_("/executions/" + EXEC_ID, wait=2.2)
    copy_probe2 = safe_js(r'''(async () => {
      const btns = [...document.querySelectorAll('button[aria-label]')].filter(b => (b.getAttribute('aria-label')||'').includes('复制'));
      const labels = btns.map(b => b.getAttribute('aria-label'));
      let toasts = [];
      if (btns[0]) { btns[0].scrollIntoView({block:'center'}); btns[0].click(); await new Promise(r=>setTimeout(r,900));
        toasts = [...document.querySelectorAll('[data-sonner-toast]')].map(e=>(e.innerText||'').trim()); }
      return {labels, clicked: !!btns[0], toasts, isSecureContext: window.isSecureContext};
    })()''')
    step("x20-copy-exec-id", copy_probe2)

    # 复制日志按钮
    logs = safe_js(r'''(async () => {
      const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').includes('复制日志'));
      if (!b) return {ok:false};
      b.scrollIntoView({block:'center'}); b.click();
      await new Promise(r=>setTimeout(r,1000));
      return {ok:true, toasts: [...document.querySelectorAll('[data-sonner-toast]')].map(e=>(e.innerText||'').trim()),
              log_len_inner: (document.querySelector('pre')||{}).innerText ? document.querySelector('pre').innerText.length : null};
    })()''')
    step("x20-copy-logs", logs)
    ui.shot("X20-07-copy-logs-toast.png")

    # 3) 760 / 480 窄屏：长值页面横向溢出
    for w in (760, 480):
        viewport(w, 900)
        for label, path, shot in [("artifacts", "/artifacts", "X20-08-artifacts-%d.png" % w),
                                  ("transfers", "/transfers", "X20-09-transfers-%d.png" % w),
                                  ("exec-detail", "/executions/" + EXEC_ID, "X20-10-exec-%d.png" % w),
                                  ("script", "/scripts/" + SID, "X20-11-script-%d.png" % w),
                                  ("task", "/tasks/" + TASK_ID, "X20-12-task-%d.png" % w),
                                  ("schedules", "/schedules/" + SCHED_ID, "X20-13-sched-%d.png" % w)]:
            audit("x20-%s-%d" % (label, w), path, shot)

    # 4) 480 窄屏确认框（删除制品确认，仅打开不确认），检查不溢出；随后关闭
    viewport(480, 900)
    ui.open_("/artifacts", wait=2.2)
    dlg = safe_js(r'''(async () => {
      const rows = [...document.querySelectorAll('tbody tr')];
      const row = rows.find(r => (r.innerText||'').includes('qa-webgap-6456ada2-art-empty'));
      if (!row) return {ok:false, reason:'owned row not found', rows: rows.map(r=>r.innerText.slice(0,60))};
      const btn = [...row.querySelectorAll('button')].pop();
      btn.scrollIntoView({block:'center'}); btn.click();
      await new Promise(r=>setTimeout(r,700));
      const items = [...document.querySelectorAll('[role=menuitem]')].map(e=>(e.innerText||'').trim());
      const del = [...document.querySelectorAll('[role=menuitem]')].find(e => (e.innerText||'').includes('删除'));
      if (del) { del.click(); await new Promise(r=>setTimeout(r,800)); }
      const d = document.querySelector('[role=dialog]');
      const r2 = d ? d.getBoundingClientRect() : null;
      return {ok:true, items, dialog: d ? {text: (d.innerText||'').slice(0,200), left:Math.round(r2.left), right:Math.round(r2.right), w:Math.round(r2.width)} : null,
              innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth};
    })()''')
    step("x20-480-delete-confirm", dlg)
    ui.shot("X20-14-confirm-480.png")
    # 关闭确认框（按 Escape），不执行删除
    safe_js(r'''(async () => { document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
      await new Promise(r=>setTimeout(r,400)); return {dialog_open: !!document.querySelector('[role=dialog]')}; })()''')
    safe_js(r'''(async () => { const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='取消' || (x.innerText||'').trim()==='Cancel');
      if (b) { b.click(); await new Promise(r=>setTimeout(r,400)); } return {ok:true}; })()''')
    closed = safe_js(r'''(() => ({dialog_open: !!document.querySelector('[role=dialog]')}))()''')
    step("x20-480-confirm-closed", closed)
    ui.shot("X20-15-after-close-480.png")

    OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = Path(ui.BATCH) / "evidence" / "X20-long-values.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)


main()
