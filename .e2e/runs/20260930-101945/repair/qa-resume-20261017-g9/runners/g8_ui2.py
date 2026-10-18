"""G8（X16）UI 补充：任务「立即运行」弹窗的 secret 输入遮蔽、执行详情日志遮蔽、参数默认值输入框类型。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    M = cr["markers"]
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    ev = {"case_id": "X16", "nonce": nonce, "at_utc": C.utc()}
    U.init()
    who = "g8ui2"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])

    # ---- 1. 任务详情 → 立即运行 弹窗 → secret 输入框 ----
    U.open_(who, f"/tasks/{cr['task_full']}")
    time.sleep(1.6)
    open_dlg = U.js(who, r"""
(async () => {
  const b = [...document.querySelectorAll('button')].find(x => /立即运行|运行|执行/.test((x.innerText||'').trim()));
  if (!b) return {found:false, buttons: [...document.querySelectorAll('button')].map(x=>(x.innerText||'').trim()).filter(Boolean).slice(0,30)};
  b.click();
  await new Promise(r => setTimeout(r, 1300));
  const d = document.querySelector('[role=dialog]');
  return {found:true, text: (b.innerText||'').trim(), dialog: !!d,
          inputs: d ? [...d.querySelectorAll('input')].map(i => ({type: i.type, ph: i.placeholder, name: i.name})) : [],
          dialog_text: d ? (d.innerText||'').replace(/\n/g,' | ').slice(0,300) : null};
})()""")
    ev["run_dialog_open"] = open_dlg
    typed = U.js(who, r"""
(async () => {
  const M = %s;
  const d = document.querySelector('[role=dialog]');
  if (!d) return {dialog:false};
  const inputs = [...d.querySelectorAll('input')].filter(i => i.type !== 'hidden');
  const target = inputs.find(i => i.type === 'password') || inputs[0];
  if (!target) return {dialog:true, input:false, inputs: inputs.length};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(target, M);
  target.dispatchEvent(new Event('input', {bubbles:true}));
  target.dispatchEvent(new Event('change', {bubbles:true}));
  await new Promise(r => setTimeout(r, 700));
  return {dialog:true, input:true, type: target.type, placeholder: target.placeholder || null,
          value_equals_marker: target.value === M,
          marker_in_dialog_visible_text: ((d.innerText||'').includes(M)),
          marker_in_dom_html_count: (document.documentElement.outerHTML.split(M).length - 1),
          dialog_text: (d.innerText||'').replace(/\n/g,' | ').slice(0,300)};
})()""" % json.dumps(M["ui_input"]))
    ev["secret_input_probe"] = typed
    U.shot(who, "g8", "G8-run-dialog-secret-input.png")
    ev["dialog_close"] = U.js(who, r"""
(() => {
  const b = [...document.querySelectorAll('button')].find(x => /^(取消|关闭)$/.test((x.innerText||'').trim()));
  if (b) b.click();
  return {closed: !!b};
})()""")
    time.sleep(0.8)

    # ---- 2. 执行详情页日志遮蔽 ----
    ex = None
    for r in C.as_list(hub.get("/api/executions?limit=50", admin)["json"]):
        if r.get("task_id") == cr["task_full"] and r.get("status") == "SUCCESS" and r.get("exit_code") is not None:
            ex = r
            break
    if ex is None:
        for r in C.as_list(hub.get("/api/executions?limit=50", admin)["json"]):
            if r.get("task_id") == cr["task_full"]:
                ex = r
                break
    if ex:
        U.open_(who, f"/executions/{ex['id']}")
        time.sleep(2.2)
        body = U.body_text(who)
        ev["execution_detail"] = {
            "execution_id": ex["id"], "status": ex.get("status"),
            "marker_hits": {k: body.count(v) for k, v in M.items() if v in body},
            "redacted_occurrences": body.count("[REDACTED]"),
            "log_area_head": body[body.find("日志"):][:400] if "日志" in body else body[-500:],
            "search_input_placeholder": U.js(who, r"""
(() => { const i = [...document.querySelectorAll('input')].find(x => /搜索/.test(x.placeholder||''));
  return i ? i.placeholder : null; })()"""),
        }
        U.shot(who, "g8", "G8-execution-detail-redacted.png")

    # ---- 3. 参数默认值输入框：type=secret 时是否为 password ----
    U.open_(who, f"/scripts/{cr['script_id']}")
    time.sleep(1.6)
    U.js(who, r"""
(() => {
  const b = [...document.querySelectorAll('button,a')].find(x => /编辑/.test((x.innerText||'').trim()));
  if (b) b.click();
  return true;
})()""")
    time.sleep(1.6)
    pick = U.js(who, r"""
(async () => {
  const trig = [...document.querySelectorAll('[role=combobox],button')]
      .find(b => /^(string|number|bool|secret)$/.test((b.innerText||'').trim()));
  if (!trig) return {found:false, cands: [...document.querySelectorAll('[role=combobox]')].map(b=>(b.innerText||'').trim())};
  const before = (trig.innerText||'').trim();
  trig.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  trig.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  trig.click();
  await new Promise(r => setTimeout(r, 700));
  const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').trim() === 'secret');
  if (!opt) return {found:true, before, options: [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim())};
  opt.click();
  await new Promise(r => setTimeout(r, 700));
  const dflt = [...document.querySelectorAll('input')].find(i => (i.placeholder||'') === '默认值');
  return {found:true, before, after: (trig.innerText||'').trim(),
          default_input_type: dflt ? dflt.type : null,
          default_input_value_len: dflt ? (dflt.value||'').length : null,
          all_input_types: [...document.querySelectorAll('[role=dialog] input, main input')]
              .map(i => ({type: i.type, ph: i.placeholder})).slice(0,12)};
})()""")
    ev["param_default_input_type"] = pick
    U.shot(who, "g8", "G8-param-type-secret-input.png")
    C.write_evidence("g8", "G8-masking-matrix-ui2.json", ev)
    print(json.dumps({k: ev[k] for k in ("login", "run_dialog_open", "secret_input_probe", "execution_detail",
                                        "param_default_input_type")}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
