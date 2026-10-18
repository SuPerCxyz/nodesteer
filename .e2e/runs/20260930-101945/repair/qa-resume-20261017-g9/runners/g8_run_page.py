"""G8/X16：运行页 secret 输入面（页面形态 + 确认对话框），含真实提交与执行侧遮蔽核对。

流程：任务详情 → 「立即运行」→ 运行页（secret 输入为 password 型、预填为掩码点）→
      填入标记值 → 确认运行 → 读取最新执行：日志应为 [REDACTED]，DOM/接口无标记原文。
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
    who = "g8p"
    ev = {"case_id": "X16", "probe": "run-page-secret-input", "nonce": nonce,
          "at_utc": C.utc(), "marker_label": "QAMARK-UIINPUT-*"}
    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    U.open_(who, f"/tasks/{cr['task_full']}", wait=2.6)
    before_ids = {e["id"] for e in C.as_list(hub.get("/api/executions?limit=50", admin)["json"])}

    def click_text(pattern):
        r = U.js(who, r"""
(() => {
  const els = [...document.querySelectorAll('button,a')].filter(b => b.offsetParent !== null);
  const b = els.find(x => new RegExp(%s).test((x.innerText||'').trim()));
  if (!b) return {found:false, candidates: els.map(x => (x.innerText||'').trim()).filter(Boolean).slice(0,30)};
  b.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  b.click();
  return {found:true, text:(b.innerText||'').trim()};
})()""" % json.dumps(pattern))
        time.sleep(1.8)
        return r

    ev["open_run"] = click_text("^立即运行$")
    ev["url_after_open"] = U.url(who)
    ev["run_page_scan"] = U.js(who, r"""
(() => {
  const inputs = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null)
      .map(i => ({type: i.type, ph: i.placeholder, val_len: (i.value||'').length,
                  val_is_marker: (i.value||'') === %s}));
  const dlgs = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')];
  return {inputs, dialog_count: dlgs.length,
          dialog_text: dlgs[0] ? (dlgs[0].innerText||'').replace(/\n/g,' | ').slice(0,220) : null,
          body_has_marker: (document.body.innerText||'').includes(%s)};
})()""" % (json.dumps(MARK), json.dumps(MARK)))
    ev["shot_run_page"] = U.shot(who, "g8", "G8-runpage-secret-input.png")

    # 填入标记值（password 型输入框；先取消预填掩码）
    ev["fill"] = U.js(who, r"""
(async () => {
  const pw = [...document.querySelectorAll('input')].filter(i => i.offsetParent !== null)
      .find(i => i.type === 'password');
  if (!pw) return {ok:false, reason:'no-password-input'};
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  set.call(pw, '');
  pw.dispatchEvent(new Event('input',{bubbles:true}));
  set.call(pw, %s);
  pw.dispatchEvent(new Event('input',{bubbles:true}));
  pw.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(r => setTimeout(r, 400));
  return {ok:true, type: pw.type, len_after: (pw.value||'').length,
          visible_plaintext_in_dom: (document.body.innerText||'').includes(%s)};
})()""" % (json.dumps(MARK), json.dumps(MARK)))
    ev["shot_filled"] = U.shot(who, "g8", "G8-runpage-secret-filled.png")

    # 点击运行 → 若出现确认框再确认
    ev["submit_click"] = click_text("^立即运行$")
    ev["confirm_dialog"] = U.js(who, """(() => {
  const d = [...document.querySelectorAll('[role=dialog],[role=alertdialog]')];
  return {count: d.length, text: d[0] ? (d[0].innerText||'').replace(/\\n/g,' | ').slice(0,220) : null}; })()""")
    ev["confirm_click"] = click_text("^立即运行$|^确认$|^确定$")
    time.sleep(3.0)
    ev["url_after_submit"] = U.url(who)
    ev["marker_in_dom_after_submit"] = U.js(who, "(document.body.innerText||'').includes(%s)" % json.dumps(MARK))
    ev["shot_after_submit"] = U.shot(who, "g8", "G8-runpage-after-submit.png")

    exs = C.as_list(hub.get("/api/executions?limit=50", admin)["json"])
    new = [e for e in exs if e["id"] not in before_ids]
    ev["new_executions"] = [{"id": e["id"], "task_id": e.get("task_id"), "status": e.get("status"),
                             "node_id": e.get("node_id")} for e in new]
    target = next((e for e in new if e.get("task_id") == cr["task_full"]), None)
    if target:
        det = hub.get(f"/api/executions/{target['id']}/logs", admin)
        text = (det["body_raw"] or b"").decode(errors="replace")
        ev["ui_execution"] = {"execution_id": target["id"], "status": target.get("status"),
                              "marker_in_logs": MARK in text,
                              "redacted_count": text.count("[REDACTED]"),
                              "log_head": text[:200].replace(MARK, "<MARKER>")}
    else:
        ev["ui_execution"] = {"created": False,
                              "note": "未观察到新执行（目标节点可能已失效）；UI 输入面已按 password 型记录"}
    C.write_evidence("g8", "G8-run-page-secret-input.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:3500])


if __name__ == "__main__":
    main()
