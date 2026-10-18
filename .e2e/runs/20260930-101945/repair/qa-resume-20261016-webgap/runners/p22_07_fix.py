#!/usr/bin/env python3
"""P22-07 修正版：UI 新建传输（源 native-2 → 目标 native-4 非法目标路径）并捕获「创建中」loading。"""
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
DST_BAD = "/etc/hostname/qa-webgap-6456ada2-ui-invalid-dst.bin"
tok = login()
OUT = {"case_ids": ["P22-07"], "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN, "src": SRC, "dst_bad": DST_BAD}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1100])
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
  if (!opt) return {ok:false, reason:'option not found', opts:[...document.querySelectorAll('[role=option]')].map(o=>o.innerText.trim())};
  opt.click();
  await new Promise(r => setTimeout(r, 400));
  return {ok:true, chosen: optionText};
})()'''

SET_PATH = r'''(() => {
  const idx = %d;
  const inp = [...document.querySelectorAll('input')].filter(i => (i.placeholder||'').includes('/var/lib/app/config.yaml'))[idx];
  if (!inp) return {ok:false, count: [...document.querySelectorAll('input')].length};
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(inp, %s);
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  inp.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true, value: inp.value};
})()'''

STATE = r'''(() => {
  const start = [...document.querySelectorAll('button')].filter(x => /^(开始传输|创建中)/.test((x.innerText||'').trim()))[0];
  const add = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '添加目标');
  const inputs = [...document.querySelectorAll('input')].filter(i => (i.placeholder||'').includes('/var/lib/app/config.yaml')).map(i => i.value);
  const chips = [...document.querySelectorAll('span,div')].filter(e => (e.innerText||'').includes('qa-webgap') && (e.innerText||'').length < 260).map(e => e.innerText.trim().slice(0,120));
  return {start: start ? {text:start.innerText.trim(), disabled:start.disabled} : null,
          add: add ? {disabled:add.disabled} : null, path_inputs: inputs, chips: chips.slice(0,3)};
})()'''


def main():
    ui.run("network", "requests", "--clear")
    ui.open_("/transfers", wait=2.2)
    s = {}
    s["initial"] = safe_js(STATE)
    s["select_source"] = safe_js(SELECT % (json.dumps("选择源 Agent"), json.dumps("native-2")))
    s["paths_after_source"] = safe_js(SET_PATH % (0, json.dumps(SRC)))
    time.sleep(0.3)
    s["after_source_path"] = safe_js(STATE)
    s["select_target"] = safe_js(SELECT % (json.dumps("选择目标 Agent"), json.dumps("native-4")))
    s["paths_after_target"] = safe_js(SET_PATH % (1, json.dumps(DST_BAD)))
    time.sleep(0.4)
    s["ready_with_target_path"] = safe_js(STATE)
    s["add_target_click"] = safe_js(r'''(async () => {
      const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '添加目标');
      const before = b ? b.disabled : null;
      if (b && !b.disabled) b.click();
      await new Promise(r=>setTimeout(r,450));
      return {before_disabled: before};
    })()''')
    time.sleep(0.4)
    s["after_add"] = safe_js(STATE)
    ui.shot("P22-07-01-start-enabled.png")
    step("p22-07-ui-disabled-matrix", s)

    # 反向：清空源路径 → Start 应重新禁用；再恢复
    s["clear_source_path"] = safe_js(SET_PATH % (0, json.dumps("")))
    time.sleep(0.4)
    s["after_clear_source"] = safe_js(STATE)
    s["restore_source_path"] = safe_js(SET_PATH % (0, json.dumps(SRC)))
    time.sleep(0.4)
    s["restored"] = safe_js(STATE)
    step("p22-07-reverse-path", {"clear": s["after_clear_source"], "restored": s["restored"]})
    ui.shot("P22-07-02-restored.png")

    n_before = len(as_list(j("GET", "/api/transfers", tok)[1]))
    sample = safe_js(r'''(async () => {
      const pick = () => [...document.querySelectorAll('button')].filter(x => /^(开始传输|创建中)/.test((x.innerText||'').trim()))[0];
      const start = pick();
      if (!start) return {ok:false, reason:'start button missing'};
      const before = {text:start.innerText.trim(), disabled:start.disabled};
      start.click();
      const samples = []; let secondClick = null;
      for (let i=0;i<160;i++) {
        await new Promise(r=>setTimeout(r,25));
        const b = pick();
        if (!b) { samples.push({t:i*25, gone:true}); break; }
        samples.push({t:i*25, text:b.innerText.trim(), disabled:b.disabled});
        if (b.disabled && !secondClick) { b.click(); secondClick = {t:i*25, text:b.innerText.trim(), was_disabled:b.disabled}; }
        if (i>6 && !b.disabled && b.innerText.trim()==='开始传输') break;
      }
      return {before, secondClick, samples};
    })()''')
    step("p22-07-start-loading", sample)
    ui.shot("P22-07-03-loading.png")
    time.sleep(2.5)
    ui.shot("P22-07-04-after-create.png")

    st, lst = j("GET", "/api/transfers", tok)
    allt = as_list(lst)
    mine = [t for t in allt if t.get("source_path") == SRC or (t.get("targets") or [{}])[0].get("destination_path") == DST_BAD]
    mine.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    reqs = ui.requests(200)
    posts = [r for r in reqs if "/api/transfers" in r and "POST" in r.upper()]
    step("p22-07-created", {"n_before": n_before, "n_after": len(allt), "post_count": len(posts), "posts": posts,
                            "created": [{"id": t["id"], "status": t["status"], "source_path": t["source_path"],
                                         "targets": [{"node": x["node_id"], "status": x["status"], "dst": x["destination_path"]}
                                                     for x in t.get("targets") or []]} for t in mine[:3]]})

    tid = None
    for t in mine:
        if t.get("source_path") == SRC and any(x.get("destination_path") == DST_BAD for x in t.get("targets") or []):
            tid = t["id"]
            break
    if tid:
        t0 = time.time()
        final = None
        while time.time() - t0 < 180:
            st, d = j("GET", "/api/transfers/%s" % tid, tok)
            if st == 200 and d.get("status") in ("SUCCESS", "FAILED", "CANCELED"):
                final = d
                break
            time.sleep(3)
        step("p22-07-ui-transfer-terminal", {"id": tid, "waited_s": round(time.time() - t0, 1),
                                             "status": (final or {}).get("status"),
                                             "targets": [{"node": x["node_id"], "status": x["status"], "error": x.get("error")}
                                                         for x in ((final or {}).get("targets") or [])]})
        OUT["ui_transfer_id"] = tid
        OUT["ui_transfer_final"] = {"status": (final or {}).get("status"),
                                    "targets": [{"node": x["node_id"], "status": x["status"], "error": x.get("error")}
                                                for x in ((final or {}).get("targets") or [])]}
        ui.open_("/transfers", wait=2.0)
        ui.shot("P22-07-05-list-final.png")

    OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = Path(ui.BATCH) / "evidence" / "P22-07-ui-start.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)


main()
