"""P03-09：真空权威（初始数据目录）上的空态 — 无运行执行、无应用、无调度、无失败执行。

必须在创建任何 fixture 之前运行（新实例的初始 data_dir 即真空态）。
只读 + UI 观察；不产生写请求（除登录）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402
import g1_ui as U  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    ev = {"case_id": "P03-09", "at_utc": C.utc(), "authority": cr["base_url"],
          "authority_kind": "isolated pristine instance (fresh data_dir, no fixtures yet)"}

    api = {}
    for ep in ("/api/executions", "/api/applications", "/api/schedules", "/api/nodes",
               "/api/tasks", "/api/scripts", "/api/artifacts", "/api/groups", "/api/transfers",
               "/api/executions?status=failed", "/api/executions?status=RUNNING"):
        r = hub.get(ep, admin)
        lst = C.as_list(r["json"])
        api[ep] = {"http": r["status"], "count": len(lst),
                   "statuses": sorted({x.get("status") for x in lst if isinstance(x, dict)})
                   if lst else []}
    ev["api_pristine"] = api
    ev["api_verdict"] = {
        "no_executions": api["/api/executions"]["count"] == 0,
        "no_failed_executions": api["/api/executions?status=failed"]["count"] == 0,
        "no_running_executions": api["/api/executions?status=RUNNING"]["count"] == 0,
        "no_applications": api["/api/applications"]["count"] == 0,
        "no_schedules": api["/api/schedules"]["count"] == 0,
        "no_nodes": api["/api/nodes"]["count"] == 0,
        "no_tasks": api["/api/tasks"]["count"] == 0,
    }

    # ---- UI ----
    U.init(nonce)
    lg = U.login("empty", cr["admin_username"], cr["admin_password"])
    ev["ui_login"] = {"token_in_localStorage": lg["token_in_localStorage"], "url": lg["url"]}

    EXPECT = {
        "/": ["当前没有正在执行的任务。", "暂无托管应用", "暂无调度"],
        "/applications": ["暂无数据"],
        "/schedules": ["暂无数据"],
        "/tasks": ["没有结果。"],
        "/nodes": ["暂无数据"],
        "/executions?status=failed": ["没有匹配的执行记录"],
    }
    ui = {}
    for path, needles in EXPECT.items():
        U.open_("empty", path)
        body = U.body_text("empty")
        rows = U.js("empty", """[...document.querySelectorAll('table tbody tr')].map(r=>r.innerText.slice(0,80))""")
        ui[path] = {
            "url": U.url("empty"),
            "empty_state_hits": {n: (n in body) for n in needles},
            "table_body_rows": rows,
            "data_rows": [r for r in rows if r.strip() not in ("没有结果。", "暂无数据",
                                                              "没有匹配的执行记录", "")],
        }
        name = "P0309-" + (path.strip("/").replace("/", "_").replace("?", "_").replace("=", "") or "dashboard")
        ui[path]["shot"] = U.shot("empty", name + ".png")
        if path == "/":
            ui[path]["dashboard_text"] = body[:1800]
    ev["ui"] = ui

    ui_ok = all(all(v["empty_state_hits"].values()) and not v["data_rows"] for v in ui.values())
    dash = ui["/"]["dashboard_text"]
    ev["verdict"] = {
        "api_all_empty": all(ev["api_verdict"].values()),
        "ui_all_empty_states": ui_ok,
        "dashboard_metrics": {
            "running_zero": "运行中\n0" in dash,
            "failed_zero": "执行失败\n0" in dash,
            "success_rate_dash": "成功率\n-\n\n0 次已完成执行" in dash,
        },
        "conclusion": ("pristine authority shows no running/failed executions, no applications, "
                       "no schedules, no nodes, no tasks in both API and UI"
                       if all(ev["api_verdict"].values()) and ui_ok else "see details"),
    }
    C.write_evidence("P03-09-pristine-empty-state.json", ev)
    print(json.dumps({"api_verdict": ev["api_verdict"], "ui_hits": {k: v["empty_state_hits"] for k, v in ui.items()},
                      "ui_data_rows": {k: v["data_rows"] for k, v in ui.items()},
                      "verdict": ev["verdict"]["conclusion"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
