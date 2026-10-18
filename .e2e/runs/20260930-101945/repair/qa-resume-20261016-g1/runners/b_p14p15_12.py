"""B 批：P14P15-12（Admin/operator/viewer 的部署、定义修改权限）v2。

顺序修正：先做非破坏性角色（viewer→operator→admin），破坏性定义/删除测试一律用当次新建的
一次性应用，绝不删改已登记夹具。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402

OPS = ["deploy", "start", "stop", "restart", "upgrade"]


def api_part(hub, tok, cr, fx) -> dict:
    aid = fx["fixtures"]["application"]["id"]
    node = cr["node_id"]
    import secrets as _s
    run = _s.token_hex(2)
    out = {}
    for role in ("viewer", "operator", "admin"):
        t = tok[role]
        ops = {}
        for op in OPS:
            r = hub.post(f"/api/applications/{aid}/deploy", t, body={"node_ids": [node], "operation": op})
            ids = [e.get("id") for e in (r["json"] or [])] if isinstance(r["json"], list) else []
            ops[op] = {"http": r["status"], "execution_ids": ids,
                       "error": (r["json"] or {}).get("error") if isinstance(r["json"], dict) else None}
        # 定义修改：用当次一次性应用（不触碰夹具）
        tmp = hub.post("/api/applications", tok["admin"], body={
            "name": f"qa-{cr['nonce']}-tmp-{role}-{run}", "version": "1.0.0",
            "binary_path": f"/usr/local/bin/qa-{cr['nonce']}-tmp", "unit_name": f"nodesteer-qa-tmp-{run}-{role}.service"})
        tmp_id = (tmp["json"] or {}).get("id")
        out[role] = {
            "deploy_operations": ops,
            "definition_update_PUT": hub.put(f"/api/applications/{tmp_id}", t,
                                             body={"id": tmp_id, "name": f"qa-{cr['nonce']}-tmp-mod-{role}",
                                                   "version": "1.2.3"})["status"],
            "definition_create_POST": hub.post("/api/applications", t,
                                              body={"name": f"qa-{cr['nonce']}-{role}-{run}", "version": "1.0.0"})["status"],
            "definition_delete_DELETE": hub.delete(f"/api/applications/{tmp_id}", t)["status"],
            "assign_POST": hub.post(f"/api/applications/{tmp_id}/assign", t, body={"node_ids": [node]})["status"],
            "assign_after_delete": hub.post(f"/api/applications/{tmp_id}/assign", tok["admin"],
                                            body={"node_ids": [node]})["status"],
            "tmp_app_id": tmp_id,
        }
    # 夹具应用保持只读：定位当前状态
    cur = hub.get(f"/api/applications/{aid}", tok["admin"])["json"]
    out["application_fixture_after"] = {"name": cur.get("name"), "version": cur.get("version")}
    out["run_suffix"] = run
    return out


def ui_probe(cr, fx) -> dict:
    aid = fx["fixtures"]["application"]["id"]
    out = {}
    U.init(cr["nonce"])
    for role, who, un, pw in (("admin", "adm4", cr["admin_username"], cr["admin_password"]),
                              ("operator", "op4", cr["operator_username"], cr["operator_password"]),
                              ("viewer", "vw4", cr["viewer_username"], cr["viewer_password"])):
        U.login(who, un, pw)
        U.open_(who, "/applications")
        row_menu = U.js(who, r"""
(() => {
  const tr = document.querySelector('table tbody tr');
  const btn = tr ? [...tr.querySelectorAll('button')].slice(-1)[0] : null;
  if (!btn) return {no_menu: true};
  btn.click();
  return {opened: true};
})()""")
        time.sleep(1.0)
        menu = U.js(who, r"""
(() => {
  const items = [...document.querySelectorAll('[role=menuitem]')].map(e => (e.innerText||'').trim());
  return {items, count: items.length};
})()""")
        # 关闭菜单（Escape）后进入详情页
        U.run(who, "press", "Escape")
        U.open_(who, f"/applications/{aid}")
        detail = U.js(who, r"""
(() => {
  const btns = [...document.querySelectorAll('button')].filter(b => b.offsetParent !== null)
      .map(b => ({text:(b.innerText||'').trim(), disabled: !!b.disabled}));
  const labels = [...document.querySelectorAll('label')].filter(l => l.offsetParent !== null)
      .map(l => ({text:(l.innerText||'').trim().slice(0,20),
                  input_disabled: (() => { const i = l.querySelector('input'); return i ? !!i.disabled : null; })()}));
  return {url: location.pathname, buttons: btns.map(b => b.text + (b.disabled ? '(disabled)' : '')),
          save: btns.filter(b => /保存|Save/.test(b.text)).length,
          ops: btns.filter(b => /部署|启动|停止|重启|升级|移除|卸载|删除/.test(b.text)).map(b => b.text),
          labels};
})()""")
        out[role] = {"row_menu": row_menu, "row_menu_items": menu, "detail": detail,
                     "shot": U.shot(who, f"P14P15-12-appdetail-{role}.png")}
    return out


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    tok = {"admin": hub.token(cr["admin_username"], cr["admin_password"]),
           "operator": hub.token(cr["operator_username"], cr["operator_password"]),
           "viewer": hub.token(cr["viewer_username"], cr["viewer_password"])}
    ex_before = len(C.as_list(hub.get("/api/executions", tok["admin"])["json"]))
    api = api_part(hub, tok, cr, fx)
    time.sleep(8)
    ex_after = C.as_list(hub.get("/api/executions", tok["admin"])["json"])
    ui = ui_probe(cr, fx)

    ev = {"case_id": "P14P15-12", "at_utc": C.utc(), "authority": cr["base_url"],
          "api": api, "ui": ui,
          "executions_count_before": ex_before, "executions_count_after": len(ex_after),
          "executions_for_ops": [{k: e.get(k) for k in ("id", "task_id", "node_id", "status",
                                                       "trigger_type", "operation", "target_kind")}
                                 for e in ex_after[:16]],
          "verdict": {
              "admin_ops_all_accepted": {op: api["admin"]["deploy_operations"][op]["http"] for op in OPS},
              "operator_ops_all_accepted": {op: api["operator"]["deploy_operations"][op]["http"] for op in OPS},
              "viewer_ops_all_403": {op: api["viewer"]["deploy_operations"][op]["http"] for op in OPS},
              "definition_modify": {r: {k: api[r][k] for k in
                                        ("definition_update_PUT", "definition_create_POST",
                                         "definition_delete_DELETE", "assign_POST")}
                                    for r in ("admin", "operator", "viewer")},
              "assign_missing_app_http": {r: api[r]["assign_after_delete"] for r in ("admin", "operator", "viewer")},
              "real_executions_created": len(ex_after) - ex_before,
              "app_fixture_untouched": api["application_fixture_after"],
              "ui_row_menu_items": {r: ui[r]["row_menu_items"].get("items") for r in ui},
              "ui_detail_save_buttons": {r: ui[r]["detail"].get("save") for r in ui},
              "ui_detail_op_buttons": {r: ui[r]["detail"].get("ops") for r in ui},
          }}
    C.write_evidence("P14P15-12-app-deploy-permissions.json", ev)
    print(json.dumps({"api_ops": {r: {op: api[r]["deploy_operations"][op]["http"] for op in OPS}
                                  for r in ("admin", "operator", "viewer")},
                      "definition": {r: {k: api[r][k] for k in ("definition_update_PUT", "definition_create_POST",
                                                                "definition_delete_DELETE", "assign_POST",
                                                                "assign_after_delete")}
                                     for r in ("admin", "operator", "viewer")},
                      "exec_delta": len(ex_after) - ex_before,
                      "ui": {r: {"menu": ui[r]["row_menu_items"].get("items"),
                                 "save": ui[r]["detail"].get("save"), "ops": ui[r]["detail"].get("ops"),
                                 "url": ui[r]["detail"].get("url")} for r in ui}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
