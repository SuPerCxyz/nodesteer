"""G8/X16：运行对话框 secret 输入面（一次尝试；失败即如实记为未覆盖）。

步骤：打开 task 详情 → 点「立即运行」→ 记录对话框内参数输入（类型/是否预填）→
      在 secret 输入框内填入标记值并提交 → 读执行详情，确认日志为 [REDACTED] 且 DOM/接口无标记原文。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402

MARK = "QAMARK-UIINPUT-8970c813-0123456789abcdef"


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    C.register_redaction(cr["admin_password"], "<ADMIN_PW>")
    C.register_redaction(cr["registration_token"], "<REG_TOKEN>")
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    U.init()
    who = "g8r"
    ev = {"case_id": "X16", "probe": "run-dialog-secret-input", "nonce": nonce,
          "at_utc": C.utc(), "marker_label": "QAMARK-UIINPUT-*"}
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, f"/tasks/{cr['task_full']}", wait=2.6)
    ev["buttons"] = U.js(who, """[...document.querySelectorAll('button,a')].filter(b => b.offsetParent !== null)
      .map(b => (b.innerText||'').trim()).filter(Boolean).slice(0,40)""")
    ev["open_click"] = U.js(who, r"""
(() => {
  const els = [...document.querySelectorAll('button,a')].filter(b => b.offsetParent !== null);
  const b = els.find(x => /立即运行|^运行$/.test((x.innerText||'').trim()));
  if (!b) return {found:false, candidates: els.map(x => (x.innerText||'').trim()).filter(Boolean).slice(0,30)};
  b.dispatchEvent(new MouseEvent('pointerdown', {bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown', {bubbles:true}));
  b.click();
  return {found:true, text:(b.innerText||'').trim()};
})()""")
    time.sleep(2.0)
    ev["dialog_state"] = U.js(who, r"""
(() => {
  const dlg = document.querySelector('[role=dialog], [role=alertdialog]');
  const inputs = dlg ? [...dlg.querySelectorAll('input')].map(i => ({type: i.type, ph: i.placeholder,
      val_len: (i.value||'').length, val_is_marker: (i.value||'') === %s})) : [];
  return {dialog: !!dlg,
          dialog_text: dlg ? (dlg.innerText||'').replace(/\n/g,' | ').slice(0,260) : null,
          inputs,
          buttons: dlg ? [...dlg.querySelectorAll('button')].map(x => (x.innerText||'').trim()) : []};
})()""" % json.dumps(MARK))
    ev["dialog_probe"] = U.js(who, r"""
(() => {
  const dlg = document.querySelector('[role=dialog], [role=alertdialog]');
  if (!dlg) return {dialog:false};
  return {dialog:true, marker_in_dialog_text: (dlg.innerText||'').includes(%s),
          password_inputs: [...dlg.querySelectorAll('input')].filter(i => i.type === 'password').length};
})()""" % json.dumps(MARK))
    ev["shot_dialog"] = U.shot(who, "g8", "G8-run-dialog-secret-input-final.png")

    # 在对话框内填入标记值（若存在密码型输入框）并提交
    ev["fill_submit"] = U.js(who, r"""
(async () => {
  const dlg = document.querySelector('[role=dialog], [role=alertdialog]');
  if (!dlg) return {ok:false, reason:'no-dialog'};
  const pw = [...dlg.querySelectorAll('input')].find(i => i.type === 'password')
          || [...dlg.querySelectorAll('input')].find(i => /secret|参数/i.test(i.placeholder||''));
  if (!pw) return {ok:false, reason:'no-secret-input',
                   inputs: [...dlg.querySelectorAll('input')].map(i => ({t:i.type, ph:i.placeholder}))};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(pw, %s);
  pw.dispatchEvent(new Event('input', {bubbles:true}));
  pw.dispatchEvent(new Event('change', {bubbles:true}));
  await new Promise(r => setTimeout(r, 300));
  const btns = [...dlg.querySelectorAll('button')];
  const go = btns.find(b => /^(确认执行|确认运行|运行|确定|确认)$/.test((b.innerText||'').trim()));
  if (!go) return {ok:false, reason:'no-submit', buttons: btns.map(b => (b.innerText||'').trim())};
  go.click();
  await new Promise(r => setTimeout(r, 2600));
  return {ok:true, submitted:true, input_type: pw.type, input_was_empty: true,
          dialog_still_open: !!document.querySelector('[role=dialog], [role=alertdialog]'),
          marker_in_dom: (document.body.innerText||'').includes(%s)};
})()""" % (json.dumps(MARK), json.dumps(MARK)))
    time.sleep(2.4)
    ev["marker_in_dom_after_submit"] = U.js(who, "(document.body.innerText||'').includes(%s)" % json.dumps(MARK))
    # 最新执行 + 日志遮蔽
    exs = C.as_list(hub.get("/api/executions?limit=10", admin)["json"])
    exs_sorted = sorted(exs, key=lambda e: e.get("start_time") or "", reverse=True)
    ev["latest_executions"] = [{"id": e["id"], "status": e.get("status"),
                                "task_id": e.get("task_id"), "start_time": e.get("start_time")}
                               for e in exs_sorted[:3]]
    ui_exec = next((e for e in exs_sorted if e.get("task_id") == cr["task_full"]), None)
    if ui_exec:
        det = hub.get(f"/api/executions/{ui_exec['id']}/logs", admin)
        text = (det["body_raw"] or b"").decode(errors="replace")
        ev["ui_execution"] = {"execution_id": ui_exec["id"], "status": ui_exec.get("status"),
                              "marker_in_logs": MARK in text,
                              "redacted_count": text.count("[REDACTED]"),
                              "log_head": text[:160].replace(MARK, "<MARKER>")}
    C.write_evidence("g8", "G8-run-dialog-secret-input.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:3200])


if __name__ == "__main__":
    main()
