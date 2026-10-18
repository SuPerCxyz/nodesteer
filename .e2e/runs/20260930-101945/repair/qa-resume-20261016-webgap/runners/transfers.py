#!/usr/bin/env python3
"""P22-07 / P22-09 / P22-10：传输开始按钮 loading 与禁用矩阵；多目标聚合与失败隔离。

- T-UI：UI 真实创建（source native-2 → target native-4 非法目标路径）→ 期望 FAILED
- T-ISO：API 创建 2 目标（native-2 合法 → SUCCESS；native-4 非法 → FAILED）→ 聚合 FAILED + 隔离
- T-SRC：API 创建源文件不存在的传输 → 源上传失败 → FAILED 且全部目标 FAILED
注意：Transfer 无 DELETE 端点，终态记录保留为历史（在报告中列出）。
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
DST_OK = BASE_DIR + "/深中文目录名称用于长值布局验证/目标副本-copy-of-the-very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789.bin"
DST_BAD = "/etc/hostname/qa-webgap-6456ada2-invalid-dst.bin"   # 父路径是文件 → MkdirAll ENOTDIR
SRC_MISSING = BASE_DIR + "/不存在的源文件-missing-source-for-failed-aggregate.bin"
tok = login()
OUT = {"case_ids": ["P22-07", "P22-09", "P22-10"], "steps": [], "session": ui.SESSION, "origin": ui.ORIGIN,
       "src": SRC, "dst_ok": DST_OK, "dst_bad": DST_BAD, "src_missing": SRC_MISSING}


def step(label, obj):
    OUT["steps"].append({"step": label, "value": obj})
    print("---", label, json.dumps(obj, ensure_ascii=False)[:1100])
    return obj


def safe_js(code):
    try:
        return ui.js(code)
    except Exception as e:
        return {"eval_error": str(e)[:200]}


SELECT = r'''
(async () => {
  const trigText = %s, optionText = %s;
  const trig = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === trigText);
  if (!trig) return {ok:false, reason:'trigger not found', trigText};
  trig.click();
  await new Promise(r => setTimeout(r, 350));
  const opts = [...document.querySelectorAll('[role=option]')].map(o => (o.innerText||'').trim());
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').includes(optionText));
  if (!opt) return {ok:false, reason:'option not found', opts};
  opt.click();
  await new Promise(r => setTimeout(r, 350));
  return {ok:true, options: opts.slice(0,12)};
})()'''

FILL_PATH = r'''(() => {
  const ph = %s;
  const inp = [...document.querySelectorAll('input')].find(i => i.placeholder === ph);
  if (!inp) return {ok:false};
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(inp, %s);
  inp.dispatchEvent(new Event('input', {bubbles:true}));
  inp.dispatchEvent(new Event('change', {bubbles:true}));
  return {ok:true, value: inp.value};
})()'''

BTN_STATE = r'''(() => {
  const b = [...document.querySelectorAll('button')].find(x => ['开始传输','创建中','添加目标'].includes((x.innerText||'').trim()));
  const start = [...document.querySelectorAll('button')].filter(x => (x.innerText||'').trim().startsWith('开始传输') || (x.innerText||'').trim().startsWith('创建中'))[0];
  const add = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '添加目标');
  return {start: start ? {text:start.innerText.trim(), disabled:start.disabled} : null,
          add: add ? {text:add.innerText.trim(), disabled:add.disabled} : null,
          chips: [...document.querySelectorAll('div')].filter(d=>d.innerText && d.innerText.includes(%s) && d.innerText.length<200).length};
})()'''


def start_click_and_sample():
    return safe_js(r'''(async () => {
      const start = [...document.querySelectorAll('button')].filter(x => (x.innerText||'').trim().startsWith('开始传输') || (x.innerText||'').trim().startsWith('创建中'))[0];
      if (!start) return {ok:false, reason:'start button missing'};
      const before = {text:start.innerText.trim(), disabled:start.disabled};
      start.click();
      const samples = [];
      for (let i=0;i<120;i++) {
        await new Promise(r=>setTimeout(r,25));
        const b = [...document.querySelectorAll('button')].filter(x => (x.innerText||'').trim().startsWith('开始传输') || (x.innerText||'').trim().startsWith('创建中'))[0];
        if (!b) { samples.push({t:i*25, gone:true}); break; }
        samples.push({t:i*25, text:b.innerText.trim(), disabled:b.disabled});
        if (b.disabled) { b.click(); }  // 重复点击尝试
        if (samples.length > 8 && b.innerText.trim()==='开始传输' && !b.disabled) break;
      }
      return {before, samples};
    })()''')


def wait_terminal(tid, timeout=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        st, d = j("GET", "/api/transfers/%s" % tid, tok)
        if st == 200:
            last = d
            if d.get("status") in ("SUCCESS", "FAILED", "CANCELED") and all(
                    t.get("status") in ("SUCCESS", "FAILED", "CANCELED") for t in d.get("targets", [])):
                return {"waited_s": round(time.time() - t0, 1), "final": d}
        time.sleep(3)
    return {"waited_s": round(time.time() - t0, 1), "final": last, "timeout": True}


def main():
    # ---------------- P22-07: UI ----------------
    ui.run("network", "requests", "--clear")
    ui.open_("/transfers", wait=2.2)
    m0 = safe_js(BTN_STATE % json.dumps("qa-webgap"))
    m1 = safe_js(SELECT % (json.dumps("选择源 Agent"), json.dumps("native-2")))
    time.sleep(0.4)
    m2 = safe_js(BTN_STATE % json.dumps("qa-webgap"))
    m3 = safe_js(FILL_PATH % (json.dumps("/var/lib/app/config.yaml"), json.dumps(SRC)))
    time.sleep(0.4)
    m4 = safe_js(BTN_STATE % json.dumps("qa-webgap"))
    m5 = safe_js(SELECT % (json.dumps("选择目标 Agent"), json.dumps("native-4")))
    time.sleep(0.4)
    m6 = safe_js(FILL_PATH % (json.dumps("/var/lib/app/config.yaml"), json.dumps(DST_BAD)))
    time.sleep(0.4)
    add = safe_js(r'''(async () => {
      const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '添加目标');
      if (!b) return {ok:false};
      const st = {disabled:b.disabled};
      b.click();
      await new Promise(r=>setTimeout(r,400));
      return {ok:true, before:st};
    })()''')
    time.sleep(0.5)
    m7 = safe_js(BTN_STATE % json.dumps(PFX))
    step("p22-07-disabled-matrix", {"initial": m0, "source_selected": m2, "source_path_filled": m4,
                                    "target_selected": m5, "after_add_target": add, "ready": m7,
                                    "select_source": m1, "select_target": m5})
    ui.shot("P22-07-01-ready.png")

    pre = j("GET", "/api/transfers", tok)
    n_before = len(as_list(pre[1]))
    sample = start_click_and_sample()
    step("p22-07-start-click-loading", sample)
    ui.shot("P22-07-02-loading.png")
    time.sleep(2.5)
    ui.shot("P22-07-03-after-start.png")
    st, lst = j("GET", "/api/transfers", tok)
    new = [t for t in as_list(lst) if t.get("source_path") == SRC and t.get("created_at", "") > time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 300))]
    t_ui = new[0] if new else None
    reqs = ui.requests(200)
    posts = [r for r in reqs if "/api/transfers" in r and "POST" in r.upper()]
    step("p22-07-created", {"n_before": n_before, "n_after": len(as_list(lst)),
                            "created": {"id": t_ui["id"], "status": t_ui["status"],
                                        "targets": [{"node": x["node_id"], "status": x["status"],
                                                     "dst": x["destination_path"]} for x in t_ui["targets"]]} if t_ui else None,
                            "post_transfers_requests": posts})
    if t_ui:
        OUT.setdefault("transfers", []).append({"id": t_ui["id"], "role": "P22-07-ui-start",
                                                "status_initial": t_ui["status"]})

    # ---------------- P22-09 / P22-10: API ----------------
    st, iso = j("POST", "/api/transfers", tok, {
        "source_node_id": NATIVE2, "source_path": SRC,
        "targets": [{"node_id": NATIVE2, "destination_path": DST_OK},
                    {"node_id": NATIVE4, "destination_path": DST_BAD}]})
    step("p22-09-10-created-iso", {"http": st, "id": iso.get("id") if isinstance(iso, dict) else iso,
                                   "status": iso.get("status") if isinstance(iso, dict) else None,
                                   "targets": [{"node": x["node_id"], "status": x["status"]} for x in (iso.get("targets") or [])] if isinstance(iso, dict) else None})
    if st == 200:
        OUT.setdefault("transfers", []).append({"id": iso["id"], "role": "P22-09/P22-10-iso",
                                                "status_initial": iso["status"]})

    st, srcf = j("POST", "/api/transfers", tok, {
        "source_node_id": NATIVE2, "source_path": SRC_MISSING,
        "targets": [{"node_id": NATIVE2, "destination_path": DST_OK + ".missing-source.bin"},
                    {"node_id": NATIVE4, "destination_path": DST_BAD + ".missing-source.bin"}]})
    step("p22-09-created-srcfail", {"http": st, "id": srcf.get("id") if isinstance(srcf, dict) else srcf,
                                    "status": srcf.get("status") if isinstance(srcf, dict) else None})
    if st == 200:
        OUT.setdefault("transfers", []).append({"id": srcf["id"], "role": "P22-09-srcfail",
                                                "status_initial": srcf["status"]})

    # ---------------- wait + UI evidence ----------------
    results = {}
    for t in OUT.get("transfers", []):
        results[t["id"]] = wait_terminal(t["id"], timeout=200)
        step("transfer-terminal-%s" % t["role"], {"id": t["id"], "waited_s": results[t["id"]]["waited_s"],
                                                  "status": (results[t["id"]]["final"] or {}).get("status"),
                                                  "error": (results[t["id"]]["final"] or {}).get("error"),
                                                  "targets": [{"node": x["node_id"], "status": x["status"],
                                                               "error": x.get("error"),
                                                               "dst": x["destination_path"]}
                                                              for x in ((results[t["id"]]["final"] or {}).get("targets") or [])]})

    ui.open_("/transfers", wait=2.5)
    ui.shot("P22-09-10-transfers-list.png")
    rows = safe_js(r'''(async () => {
      const inp = [...document.querySelectorAll('input')].find(i => (i.placeholder||'').includes('搜索传输'));
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
      setter.call(inp, 'qa-webgap-6456ada2');
      inp.dispatchEvent(new Event('input', {bubbles:true}));
      await new Promise(r=>setTimeout(r,900));
      const rows = [...document.querySelectorAll('tbody tr')].map(r => ({text: r.innerText.replace(/\n/g,' | ').slice(0,400),
        hrefs: [...r.querySelectorAll('a')].map(a=>a.getAttribute('href'))}));
      return {rows, count: rows.length};
    })()''')
    step("p22-09-10-list-search", rows)
    ui.shot("P22-09-11-list-search.png")

    OUT["terminal_summary"] = {tid: {"status": (r["final"] or {}).get("status"),
                                     "targets": [{"node": x["node_id"], "status": x["status"], "error": x.get("error")}
                                                 for x in ((r["final"] or {}).get("targets") or [])]}
                               for tid, r in results.items()}
    OUT["captured_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    p = Path(ui.BATCH) / "evidence" / "P22-transfers.json"
    p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)


main()
