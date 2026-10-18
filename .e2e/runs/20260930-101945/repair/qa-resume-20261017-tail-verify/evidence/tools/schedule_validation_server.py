#!/usr/bin/env python3
"""OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED / OBS-20261014-PAST-ONETIME-SAVE-ENABLED
服务端行为判定（repair :18080，只创建自有 fixture 并清理）。

判定问题：UI 允许点击保存时，服务端是否拒绝 interval<=0 / 过去时间 one_time。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402

EV = HERE.parent
OUT = EV / "schedule-validation-server.json"
h = hubclient.Hub()
h.login()
NONCE = "tail" + time.strftime("%Y%m%d%H%M%S")
res = {"nonce": NONCE, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
       "authority": h.base, "steps": []}
created = []


def step(name, code, body):
    res["steps"].append({"step": name, "http": code,
                         "body": body if not isinstance(body, dict) or len(json.dumps(body)) < 500 else "…"})
    return body


def main():
    # owned node（pending，不会真有 Agent 接入）作为 task 目标
    code, enr = h.raw("POST", "/api/nodes/enrollment",
                      {"node_name": f"qa-{NONCE}-schedval-node", "node_ip": "10.98.0.1"})
    nid = None
    if isinstance(enr, dict) and enr.get("node_id"):
        nid = enr["node_id"]
    elif isinstance(enr, dict) and enr.get("id"):
        nid = enr["id"]
    if not nid:
        code2, nodes = h.raw("GET", "/api/nodes")
        for n in (nodes if isinstance(nodes, list) else []):
            if n.get("hostname") == f"qa-{NONCE}-schedval-node":
                nid = n["id"]
    created.append(("node", nid))
    step("create owned node (enrollment)", code, {"id": nid})

    # owned script + task
    code, scr = h.raw("POST", "/api/scripts",
                      {"name": f"qa-{NONCE}-schedval", "content": "echo x",
                       "interpreter": "shell", "enabled": True})
    sid = scr["id"] if isinstance(scr, dict) else None
    created.append(("script", sid))
    step("create script", code, {"id": sid})

    code, task = h.raw("POST", "/api/tasks",
                       {"name": f"qa-{NONCE}-schedval-task", "script_id": sid, "type": "script",
                        "enabled": True, "offline_policy": "allow_offline",
                        "target": {"type": "node", "node_ids": [nid]}})
    tid = task.get("id") if isinstance(task, dict) else None
    created.append(("task", tid))
    step("create task", code, {"id": tid, "err": task if code != 200 else None})

    common = {"task_id": tid, "timezone": "Asia/Shanghai", "execution_owner": "agent",
              "offline_policy": "allow_offline", "misfire_policy": "run_once", "enabled": True}

    # A. interval = 0
    code, b = h.raw("POST", "/api/schedules", {**common, "type": "interval", "interval_sec": 0})
    step("POST /api/schedules type=interval interval_sec=0", code, b)
    if code == 200 and isinstance(b, dict):
        created.append(("schedule", b.get("id")))

    # B. interval = -1
    code, b = h.raw("POST", "/api/schedules", {**common, "type": "interval", "interval_sec": -1})
    step("POST /api/schedules type=interval interval_sec=-1", code, b)
    if code == 200 and isinstance(b, dict):
        created.append(("schedule", b.get("id")))

    # C. one_time with a PAST run_at
    past = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 86400))
    code, b = h.raw("POST", "/api/schedules", {**common, "type": "one_time", "run_at": past})
    step(f"POST /api/schedules type=one_time run_at={past} (24h in the past)", code, b)
    if code == 200 and isinstance(b, dict):
        created.append(("schedule", b.get("id")))
        res["past_one_time_accepted"] = True
        res["past_one_time_record"] = {"id": b.get("id"), "run_at": b.get("run_at"),
                                       "enabled": b.get("enabled")}

    # C2. one_time far past (2020)
    code, b = h.raw("POST", "/api/schedules", {**common, "type": "one_time",
                                               "run_at": "2020-01-01T00:00:00Z"})
    step("POST /api/schedules type=one_time run_at=2020-01-01T00:00:00Z", code, b)
    if code == 200 and isinstance(b, dict):
        created.append(("schedule", b.get("id")))

    # D. 参考：one_time 无 run_at（应 400）
    code, b = h.raw("POST", "/api/schedules", {**common, "type": "one_time"})
    step("POST /api/schedules type=one_time without run_at (expect 400)", code, b)

    # cleanup
    for kind, fid in reversed(created):
        if not fid:
            continue
        path = {"script": "/api/scripts/", "task": "/api/tasks/", "schedule": "/api/schedules/", "node": "/api/nodes/"}[kind]
        code, _ = h.raw("DELETE", path + fid)
        step(f"cleanup {kind} {fid}", code, {"deleted": code == 200})
    for kind, fid in created:
        if not fid:
            continue
        path = {"script": "/api/scripts/", "task": "/api/tasks/", "schedule": "/api/schedules/", "node": "/api/nodes/"}[kind]
        code, _ = h.raw("GET", path + fid)
        step(f"verify 404 {kind} {fid}", code, {"expect": 404})

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
