#!/usr/bin/env python3
"""X20 补充 2：artifacts SHA 单元渲染 + 脚本详情页 480 溢出归因（参数区）。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

SID = "29822361-f6a0-425d-aa48-6316cf059dd1"
OUT = {"case_id": "X20", "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1200])
    return obj


def safe_js(code):
    try:
        return ui.js(code)
    except Exception as e:
        return {"eval_error": str(e)[:200]}


def vp(w, h=900):
    ui.run("set", "viewport", str(w), str(h))
    time.sleep(1.0)


vp(1280)
ui.open_("/artifacts", wait=2.2)
cells = safe_js(r'''(() => {
  const rows = [...document.querySelectorAll('tbody tr')];
  const own = rows.find(r => (r.innerText||'').includes('qa-webgap-6456ada2-art-amd64'));
  if (!own) return {found:false, n: rows.length};
  const tds = [...own.querySelectorAll('td')].map(td => {
    const span = td.querySelector('span');
    const node = span || td;
    const cs = getComputedStyle(node);
    return {text: (td.innerText||'').trim().slice(0,80), text_len: (td.innerText||'').trim().length,
            inner_title: node.getAttribute('title'), truncate: cs.textOverflow, overflow: cs.overflow,
            sw: node.scrollWidth, cw: node.clientWidth,
            mono: (node.className||'').toString().includes('font-mono')};
  });
  return {found:true, tds};
})()''')
step("x20-artifacts-row-cells", cells)

vp(480)
ui.open_("/scripts/" + SID, wait=2.5)
attr = safe_js(r'''(() => {
  const out = {innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth};
  const wide = [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().width > window.innerWidth + 2)
    .slice(0, 6).map(e => ({tag:e.tagName, cls:(e.className||'').toString().slice(0,80),
                            w: Math.round(e.getBoundingClientRect().width),
                            minW: getComputedStyle(e).minWidth,
                            text:(e.innerText||'').trim().slice(0,40)}));
  const tbl = [...document.querySelectorAll('table')].map(t => ({cls:(t.className||'').toString().slice(0,80),
      w: Math.round(t.getBoundingClientRect().width), minW: getComputedStyle(t).minWidth}));
  return {...out, wide, tables: tbl};
})()''')
step("x20-script-detail-480-attribution", attr)
ui.shot("X20-20-script-detail-480.png")

vp(760)
ui.open_("/scripts/" + SID, wait=2.5)
attr760 = safe_js(r'''(() => ({innerW: window.innerWidth, docScrollW: document.documentElement.scrollWidth,
  tables: [...document.querySelectorAll('table')].map(t=>({w:Math.round(t.getBoundingClientRect().width),
    minW:getComputedStyle(t).minWidth, cls:(t.className||'').toString().slice(0,70)}))}))()''')
step("x20-script-detail-760", attr760)

OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(ui.BATCH) / "evidence" / "X20-attribution2.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
