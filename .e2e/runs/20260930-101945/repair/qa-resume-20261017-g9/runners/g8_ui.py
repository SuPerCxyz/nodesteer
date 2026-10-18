"""G8（X16）UI 面：默认值摘要遮蔽 / secret 输入遮蔽 / 执行日志遮蔽 / 审计页 / 浏览器存储。

只在隔离实例；secret 只使用非敏感标记值；真实凭据不进入 evidence。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402
import g89_ui as U  # noqa: E402

DOM_SCAN = r"""
(() => {
  const M = %s;
  const txt = document.body.innerText || '';
  const inputs = [...document.querySelectorAll('input,textarea')].map(i => ({
      type: i.type, name: i.name || null, ph: i.placeholder || null,
      value_len: (i.value || '').length,
      value_equals_marker: (i.value || '') === M }));
  const html_hits = (document.documentElement.outerHTML || '').split(M).length - 1;
  const text_hits = txt.split(M).length - 1;
  const masked_tokens = (txt.match(/•+/g) || []).slice(0, 6);
  return {url: location.pathname + location.search, marker_len: M.length,
          marker_in_visible_text: text_hits, marker_in_html: html_hits,
          masked_tokens, inputs: inputs,
          password_inputs: inputs.filter(i => i.type === 'password').length,
          text_tail: txt.slice(0, 400)};
})()"""


def ui_scan(who, marker):
    return U.js(who, DOM_SCAN % json.dumps(marker))


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    M = cr["markers"]
    sid = cr["script_id"]
    ev = {"case_id": "X16", "nonce": nonce, "at_utc": C.utc(), "authority": cr["base_url"],
          "markers": M}
    U.init()
    who = "g8ui"
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])

    # ---- 1. 浏览器存储（只记录键名/长度，不记录值）----
    U.open_(who, "/nodes")
    ev["storage"] = U.cookies_and_storage(who)
    ev["storage"]["note"] = ("localStorage 承载会话 token（Web 端设计）；document.cookie 是否非空与键名如下；"
                             "不记录任何 token 值。")

    # ---- 2. Script 编辑器：secret 默认值摘要 ----
    U.open_(who, f"/scripts/{sid}")
    time.sleep(1.5)
    ev["script_editor"] = ui_scan(who, M["revision"])
    ev["script_editor_default_marker"] = M["revision"]
    U.shot(who, "g8", "G8-script-editor-masked.png")
    # 编辑态（点击编辑后参数行的输入框类型）
    edit = U.js(who, r"""
(() => {
  const b = [...document.querySelectorAll('button,a')].find(x => /编辑/.test((x.innerText||'').trim()));
  if (!b) return {found:false, buttons: [...document.querySelectorAll('button')].map(x=>(x.innerText||'').trim()).slice(0,25)};
  b.click();
  return {found:true};
})()""")
    time.sleep(1.6)
    ev["script_edit_click"] = edit
    ev["script_editor_edit_mode"] = ui_scan(who, M["revision"])
    U.shot(who, "g8", "G8-script-editor-edit-mode.png")

    # ---- 3. Task 编辑器：secret 默认值 + 执行弹窗输入框 ----
    U.open_(who, f"/tasks/{cr['task_full']}")
    time.sleep(1.6)
    ev["task_detail"] = ui_scan(who, M["task_value"])
    U.shot(who, "g8", "G8-task-detail-masked.png")
    run_dlg = U.js(who, r"""
(async () => {
  const b = [...document.querySelectorAll('button')].find(x => /^(执行|立即执行|运行)/.test((x.innerText||'').trim()));
  if (!b) return {found:false, buttons: [...document.querySelectorAll('button')].map(x=>(x.innerText||'').trim()).slice(0,25)};
  b.click();
  await new Promise(r => setTimeout(r, 1200));
  const d = document.querySelector('[role=dialog]');
  return {found:true, dialog: !!d,
          inputs: d ? [...d.querySelectorAll('input')].map(i => ({type:i.type, ph:i.placeholder, name:i.name})) : []};
})()""")
    ev["task_run_dialog"] = run_dlg
    # 在 secret 输入框里填入标记值，检查是否为 password 且不泄漏到 DOM 文本
    typed = U.js(who, r"""
(async () => {
  const M = %s;
  const d = document.querySelector('[role=dialog]');
  if (!d) return {dialog:false};
  const inputs = [...d.querySelectorAll('input')].filter(i => i.type !== 'hidden');
  const target = inputs.find(i => i.type === 'password') || inputs[0];
  if (!target) return {dialog:true, input:false};
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(target, M);
  target.dispatchEvent(new Event('input', {bubbles:true}));
  target.dispatchEvent(new Event('change', {bubbles:true}));
  await new Promise(r => setTimeout(r, 600));
  return {dialog:true, input:true, type: target.type, name: target.name || null,
          value_equals_marker: target.value === M,
          marker_in_dialog_text: ((d.innerText||'').includes(M)),
          marker_in_dom_html: (document.documentElement.outerHTML.split(M).length - 1)};
})()""" % json.dumps(M["ui_input"]))
    ev["secret_input_probe"] = typed
    U.shot(who, "g8", "G8-task-run-dialog-secret-input.png")
    # 关闭弹窗（不执行，避免依赖已被探针删除的 child 节点）
    U.js(who, r"""
(() => {
  const d = document.querySelector('[role=dialog]');
  const b = [...document.querySelectorAll('button')].find(x => /^(取消|关闭)$/.test((x.innerText||'').trim()));
  if (b) b.click();
  return {closed: !!b, dialog_present_after: !!document.querySelector('[role=dialog]')};
})()""")
    time.sleep(0.8)
    ev["run_dialog_closed"] = U.js(who, "(() => ({dialog_present: !!document.querySelector('[role=dialog]')}))()")

    # ---- 4. 执行详情页：日志遮蔽 ----
    ex = None
    for r in C.as_list(C.Hub(cr["local_base"]).get("/api/executions?limit=50",
                                                   C.Hub(cr["local_base"]).token(cr["admin_username"],
                                                                                 cr["admin_password"]))["json"]):
        if r.get("task_id") == cr["task_full"] and r.get("status") == "SUCCESS":
            ex = r
            break
    if ex:
        U.open_(who, f"/executions/{ex['id']}")
        time.sleep(2.0)
        ev["execution_detail"] = {"execution_id": ex["id"], "scan": ui_scan(who, M["task_value"]),
                                  "redacted_present": "[REDACTED]" in U.body_text(who)}
        U.shot(who, "g8", "G8-execution-detail-redacted.png")

    # ---- 5. 审计页 ----
    U.open_(who, "/audit")
    time.sleep(2.0)
    at = U.body_text(who)
    ev["audit_page"] = {"url": U.url(who),
                        "marker_hits": {k: at.count(v) for k, v in M.items() if v in at},
                        "rows_visible": U.js(who, "document.querySelectorAll('table tbody tr').length")}
    U.shot(who, "g8", "G8-audit-page.png")

    # ---- 6. 脚本列表页（摘要列）----
    U.open_(who, "/scripts")
    time.sleep(1.8)
    st = U.body_text(who)
    ev["scripts_list"] = {"marker_hits": {k: st.count(v) for k, v in M.items() if v in st}}
    U.shot(who, "g8", "G8-scripts-list.png")
    # ---- 7. 任务列表页 ----
    U.open_(who, "/tasks")
    time.sleep(1.8)
    tt = U.body_text(who)
    ev["tasks_list"] = {"marker_hits": {k: tt.count(v) for k, v in M.items() if v in tt}}
    U.shot(who, "g8", "G8-tasks-list.png")

    C.write_evidence("g8", "G8-masking-matrix-ui.json", ev)
    print(json.dumps({k: ev[k] for k in ("login", "storage", "script_editor", "script_editor_edit_mode",
                                        "task_detail", "task_run_dialog", "secret_input_probe",
                                        "execution_detail", "audit_page", "scripts_list", "tasks_list")},
                     ensure_ascii=False, indent=1)[:4500])


if __name__ == "__main__":
    main()
