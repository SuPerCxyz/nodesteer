#!/usr/bin/env python3
"""P22-07 续：注入 2s 客户端延迟以在真实 DOM 观察「创建中...」loading 文案，并验证慢响应下重复点击不产生第二次请求。

方法说明：仅在页面内包装 window.fetch，对 POST /api/transfers 延迟 2s 后**照常转发**到真实 Hub；
不改后端、不 mock 响应。该次传输仍会真实创建（Transfer 无 DELETE 端点，保留为历史）。
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
NATIVE4 = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
BASE_DIR = "/var/lib/" + PFX
SRC = BASE_DIR + "/深中文目录名称用于长值布局验证/very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789-abcdefghijklmnopqrstuvwxyz.bin"
DST_BAD2 = "/etc/hostname/qa-webgap-6456ada2-ui-slow-dst.bin"
tok = login()
OUT = {"case_ids": ["P22-07"], "method_note": "in-page fetch wrapper delays POST /api/transfers by 2s; request is still forwarded to the real Hub",
       "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1200])
    return obj


def safe_js(code):
    try:
        return ui.js(code)
    except Exception as e:
        return {"eval_error": str(e)[:200]}


SELECT = r'''(async () => {
  const trigText = %s, optionText = %s;
  const trig = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === trigText);
  if (!trig) return {ok:false, reason:'trigger not found'};
  trig.click();
  await new Promise(r => setTimeout(r, 350));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').includes(optionText));
  if (!opt) return {ok:false, reason:'option not found'};
  opt.click();
  await new Promise(r => setTimeout(r, 400));
  return {ok:true};
})()'''

SET_PATH = r'''(() => {
  const idx = %d;
  const inp = [...document.querySelectorAll('input')].filter(i => (i.placeholder||'').includes('/var/lib/app/config.yaml'))[idx];
  if (!inp) return {ok:false};
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(inp, %s);
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  inp.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true, value: inp.value};
})()'''

PATCH_FETCH = r'''(() => {
  if (!window.__webgapOrigFetch) {
    window.__webgapOrigFetch = window.fetch;
    window.__webgapDelayHits = 0;
    window.fetch = async (...args) => {
      const u = args[0];
      const url = typeof u === 'string' ? u : (u && u.url) || '';
      const opt = args[1] || {};
      const method = (opt.method || 'GET').toUpperCase();
      if (url.includes('/api/transfers') && method === 'POST') {
        window.__webgapDelayHits++;
        await new Promise(r => setTimeout(r, 2000));
      }
      return window.__webgapOrigFetch(...args);
    };
  }
  return {patched: true, base: typeof window.__webgapOrigFetch};
})()'''

RESTORE_FETCH = r'''(() => {
  if (window.__webgapOrigFetch) { window.fetch = window.__webgapOrigFetch; delete window.__webgapOrigFetch; }
  return {restored: true, delay_hits: window.__webgapDelayHits || 0};
})()'''


def main():
    ui.run("network", "requests", "--clear")
    ui.open_("/transfers", wait=2.2)
    patch = safe_js(PATCH_FETCH)
    step("patch-fetch", patch)
    safe_js(SELECT % (json.dumps("选择源 Agent"), json.dumps("native-2")))
    safe_js(SET_PATH % (0, json.dumps(SRC)))
    safe_js(SELECT % (json.dumps("选择目标 Agent"), json.dumps("native-4")))
    safe_js(SET_PATH % (1, json.dumps(DST_BAD2)))
    time.sleep(0.4)
    safe_js(r'''(async () => { const b=[...document.querySelectorAll('button')].find(x=>(x.innerText||'').trim()==='添加目标'); if(b&&!b.disabled)b.click(); await new Promise(r=>setTimeout(r,450)); return true; })()''')
    time.sleep(0.4)
    ready = safe_js(r'''(() => { const s=[...document.querySelectorAll('button')].filter(x=>/^(开始传输|创建中)/.test((x.innerText||'').trim()))[0];
      return {text:s?s.innerText.trim():null, disabled:s?s.disabled:null}; })()''')
    step("ready", ready)
    ui.shot("P22-07-06-slow-ready.png")

    n_before = len(as_list(j("GET", "/api/transfers", tok)[1]))
    sample = safe_js(r'''(async () => {
      const pick = () => [...document.querySelectorAll('button')].filter(x => /^(开始传输|创建中)/.test((x.innerText||'').trim()))[0];
      const start = pick();
      const before = {text:start.innerText.trim(), disabled:start.disabled};
      start.click();
      const samples = []; let secondClick = null; let sawLoading = false;
      for (let i=0;i<80;i++) {
        await new Promise(r=>setTimeout(r,50));
        const b = pick();
        if (!b) { samples.push({t:(i+1)*50, gone:true}); break; }
        samples.push({t:(i+1)*50, text:b.innerText.trim(), disabled:b.disabled});
        if (b.innerText.trim().startsWith('创建中')) sawLoading = true;
        if (sawLoading && !secondClick) { b.click(); secondClick = {t:(i+1)*50, at_loading_text:b.innerText.trim(), was_disabled:b.disabled}; }
        if (sawLoading && i > 8 && !b.innerText.trim().startsWith('创建中')) break;
      }
      return {before, sawLoading, secondClick, samples};
    })()''')
    step("p22-07-slow-loading", {"before": sample.get("before"), "sawLoading": sample.get("sawLoading"),
                                 "secondClick": sample.get("secondClick"),
                                 "distinct_texts": sorted({x.get("text") for x in (sample.get("samples") or [])}),
                                 "first_samples": (sample.get("samples") or [])[:6],
                                 "loading_samples": [x for x in (sample.get("samples") or []) if str(x.get("text") or "").startswith("创建中")][:4]})
    try:
        ui.shot("P22-07-07-loading-slow.png")
    except Exception as e:
        print("shot err", e)
    time.sleep(3)
    restore = safe_js(RESTORE_FETCH)
    step("restore-fetch", restore)
    try:
        ui.shot("P22-07-08-after-slow-create.png")
    except Exception as e:
        print("shot err", e)

    st, lst = j("GET", "/api/transfers", tok)
    allt = as_list(lst)
    mine = [t for t in allt if t.get("source_path") == SRC]
    mine.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    reqs = ui.requests(300)
    posts = [r for r in reqs if "/api/transfers" in r and "POST" in r.upper()]
    step("p22-07-slow-created", {"n_before": n_before, "n_after": len(allt), "post_count": len(posts), "posts": posts,
                                 "created": [{"id": t["id"], "status": t["status"],
                                              "dst": [x["destination_path"] for x in t.get("targets") or []]} for t in mine[:2]]})
    tid = None
    for t in mine:
        if any((x.get("destination_path") or "") == DST_BAD2 for x in t.get("targets") or []):
            tid = t["id"]; break
    if tid:
        t0 = time.time(); final = None
        while time.time() - t0 < 120:
            st, d = j("GET", "/api/transfers/%s" % tid, tok)
            if st == 200 and d.get("status") in ("SUCCESS", "FAILED", "CANCELED"):
                final = d; break
            time.sleep(3)
        step("p22-07-slow-terminal", {"id": tid, "status": (final or {}).get("status"),
                                      "targets": [{"node": x["node_id"], "status": x["status"], "error": x.get("error")}
                                                  for x in ((final or {}).get("targets") or [])]})
        OUT["slow_transfer_id"] = tid
    OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = Path(ui.BATCH) / "evidence" / "P22-07-ui-slow-loading.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)


main()
