"""G8/X16：运行页 secret 输入面补测 —— 对话框内确认 + 真实执行日志遮蔽。

前置：task_full 的目标节点可能因 earlier 数据集操作失效 → 先把目标更新为当前 owned child 节点
（本批次自有夹具，隔离实例内）。随后 UI 填标记值 → 运行 → 断言执行日志为 [REDACTED]。
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
    who = "g8q"
    ev = {"case_id": "X16", "probe": "run-page-secret-input-confirm", "nonce": nonce,
          "at_utc": C.utc(), "marker_label": "QAMARK-UIINPUT-*"}

    # 目标修复：指向当前 owned child 节点
    nodes = C.as_list(hub.get("/api/nodes?limit=500", admin)["json"])
    child = next((n for n in nodes if str(n.get("hostname", "")).endswith("-child")), None)
    task = hub.get(f"/api/tasks/{cr['task_full']}", admin)["json"]
    ev["target_fix"] = {"child_node": {"id": child["id"], "status": child["status"]} if child else None,
                        "target_before": (task or {}).get("target")}
    if child and isinstance(task, dict):
        task["target"] = {"type": "node", "node_ids": [child["id"]]}
        r = hub.put(f"/api/tasks/{cr['task_full']}", admin, body=task)
        ev["target_fix"]["put_http"] = r["status"]

    ev["login"] = U.login(who, cr["admin_username"], cr["admin_password"])
    before_ids = {e["id"] for e in C.as_list(hub.get("/api/executions?limit=50", admin)["json"])}
    U.open_(who, f"/tasks/{cr['task_full']}", wait=2.6)

    def click(pattern, scope="page"):
        r = U.js(who, r"""
(() => {
  const root = %s === 'dialog' ? (document.querySelector('[role=dialog],[role=alertdialog]') || document) : document;
  const els = [...root.querySelectorAll('button,a')].filter(b => b.offsetParent !== null);
  const b = els.find(x => new RegExp(%s).test((x.innerText||'').trim()));
  if (!b) return {found:false, candidates: els.map(x => (x.innerText||'').trim()).filter(Boolean).slice(0,20)};
  b.dispatchEvent(new MouseEvent('pointerdown',{bubbles:true}));
  b.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
  b.click();
  return {found:true, text:(b.innerText||'').trim()};
})()""" % (json.dumps(scope), json.dumps(pattern)))
        time.sleep(1.8)
        return r

    ev["open_run"] = click("^立即运行$")
    ev["url"] = U.url(who)
    ev["prefill"] = U.js(who, r"""
(() => {
  const pw = [...document.querySelectorAll('input')].find(i => i.type === 'password');
  if (!pw) return {found:false};
  const v = pw.value || '';
  return {found:true, len: v.length, dots_only: /^[•*·\s]+$/.test(v),
          equals_script_default: v === %s, equals_task_default: v === %s, equals_task_value: v === %s};
})()""" % (json.dumps(cr["markers"]["script_default"]), json.dumps(cr["markers"]["task_default"]),
           json.dumps(cr["markers"]["task_value"])))
    ev["fill"] = U.js(who, r"""
(async () => {
  const pw = [...document.querySelectorAll('input')].find(i => i.type === 'password');
  if (!pw) return {ok:false};
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  set.call(pw, ''); pw.dispatchEvent(new Event('input',{bubbles:true}));
  set.call(pw, %s); pw.dispatchEvent(new Event('input',{bubbles:true}));
  pw.dispatchEvent(new Event('change',{bubbles:true}));
  await new Promise(r => setTimeout(r, 300));
  return {ok:true, type: pw.type, visible_plaintext_in_dom: (document.body.innerText||'').includes(%s)};
})()""" % (json.dumps(MARK), json.dumps(MARK)))
    ev["submit"] = click("^立即运行$")
    ev["confirm"] = click("^立即运行$|^确认$|^确定$", scope="dialog")
    time.sleep(3.2)
    ev["url_after"] = U.url(who)
    ev["marker_in_dom_after"] = U.js(who, "(document.body.innerText||'').includes(%s)" % json.dumps(MARK))

    exs = C.as_list(hub.get("/api/executions?limit=50", admin)["json"])
    new = [e for e in exs if e["id"] not in before_ids]
    ev["new_executions"] = [{"id": e["id"], "task_id": e.get("task_id"), "status": e.get("status")} for e in new]
    target = next((e for e in new if e.get("task_id") == cr["task_full"]), None)
    if target:
        # 等待终态
        for _ in range(12):
            cur = hub.get(f"/api/executions/{target['id']}", admin)["json"] or {}
            if cur.get("status") in ("SUCCESS", "FAILED", "CANCELLED", "TIMEOUT"):
                break
            time.sleep(1.0)
        det = hub.get(f"/api/executions/{target['id']}/logs", admin)
        text = (det["body_raw"] or b"").decode(errors="replace")
        ev["ui_execution"] = {"execution_id": target["id"], "status": cur.get("status"),
                              "marker_in_logs": MARK in text,
                              "redacted_count": text.count("[REDACTED]"),
                              "log_head": text[:200].replace(MARK, "<MARKER>")}
    ev["shot"] = U.shot(who, "g8", "G8-runpage-confirm-submitted.png")
    C.write_evidence("g8", "G8-run-page-secret-input-confirm.json", ev)
    print(json.dumps(ev, ensure_ascii=False, indent=1)[:3000])


if __name__ == "__main__":
    main()
