#!/usr/bin/env python3
"""OBS-20261014-OFFLINE-NODE-MANUAL-ONLINE-NO-SWEEP 判定探针（隔离实例 :18088）。

判定：
 1. 产品里手工置在线的入口（UI/API 哪个）；
 2. 无 agent 会话时是否长期保持 online（含 >心跳超时 观察窗）；
 3. 此状态下派发任务/部署是「明确报错」还是「静默挂起」。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tail_common as C  # noqa: E402

EV = HERE.parent
CREDS = sorted(C.CRED_DIR.glob("tail-creds-*.json"))
cred = json.loads(CREDS[-1].read_text())
BASE = f"http://{C.HOST}:{cred['port']}"
OUT = EV / "manual-online-no-sweep.json"


def req(method, path, token=None, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Accept": "application/json"}
    if body is not None:
        hdrs["Content-Type"] = "application/json"
    if token:
        hdrs["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw[:400]
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:400]


def main() -> None:
    st, body = req("POST", "/api/login", body={"username": cred["admin"], "password": cred["password"]})
    token = body["token"]
    res = {"instance": cred["instance"], "port": cred["port"],
           "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "steps": []}

    # 找一个 owned 节点（enrollment 造的 pending 节点）
    st, nodes = req("GET", "/api/nodes", token)
    owned = [n for n in nodes if str(n.get("hostname", "")).startswith("qa-")] if isinstance(nodes, list) else []
    res["owned_nodes"] = [{"id": n["id"], "hostname": n.get("hostname"), "status": n.get("status")} for n in owned]
    if not owned:
        res["error"] = "no owned node"
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    nid = owned[0]["id"]

    # 1) 手工置在线（API 入口 PUT /api/nodes/{id}）
    st, body = req("PUT", f"/api/nodes/{nid}", token, {"status": "online"})
    res["steps"].append({"step": "PUT /api/nodes/{id} status=online", "http": st, "body": body})
    st, n = req("GET", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "GET node after manual online", "http": st,
                         "status": n.get("status") if isinstance(n, dict) else n})

    # 2) 观察窗：> 心跳超时（默认 30s 检查间隔 + timeout）
    obs = []
    for wait in (20, 40, 70):
        time.sleep(20 if not obs else 20)
        st, n = req("GET", f"/api/nodes/{nid}", token)
        obs.append({"t_plus_s": wait, "status": n.get("status") if isinstance(n, dict) else n,
                    "http": st})
    res["steps"].append({"step": "status over observation window (no agent session)", "samples": obs})

    # 3) 派发：建 script + task，指向该节点手动运行
    st, scr = req("POST", "/api/scripts", token,
                  {"name": "qa-tail-manualonline-probe", "content": "echo probe", "interpreter": "/bin/sh"})
    sid = scr.get("id") if isinstance(scr, dict) else None
    res["steps"].append({"step": "create script", "http": st, "id": sid})
    st, task = req("POST", "/api/tasks", token,
                   {"name": "qa-tail-manualonline-task", "script_id": sid, "type": "script"})
    tid = task.get("id") if isinstance(task, dict) else None
    res["steps"].append({"step": "create task", "http": st, "id": tid, "body": task if st != 200 else None})

    st, run = req("POST", f"/api/tasks/{tid}/run", token, {"node_ids": [nid]})
    res["steps"].append({"step": "POST /api/tasks/{id}/run (manual dispatch)", "http": st, "body": run})

    # 执行记录是否被标记失败（而非悬挂）
    time.sleep(3)
    st, execs = req("GET", f"/api/executions?task_id={tid}", token)
    res["steps"].append({"step": "GET executions for task", "http": st,
                         "executions": [{"id": e.get("id"), "status": e.get("status"),
                                         "block_reason": e.get("block_reason"),
                                         "node_id": e.get("node_id")}
                                        for e in (execs if isinstance(execs, list) else [])]})

    # 4) 部署路径（application deploy）是否同样明确报错
    st, apps = req("GET", "/api/applications", token)
    res["steps"].append({"step": "list applications (deploy path uses same online gate)", "http": st,
                         "count": len(apps) if isinstance(apps, list) else apps})

    # 5) 清理自有 fixture
    if tid:
        st, _ = req("DELETE", f"/api/tasks/{tid}", token)
        res["steps"].append({"step": "cleanup task", "http": st})
    if sid:
        st, _ = req("DELETE", f"/api/scripts/{sid}", token)
        res["steps"].append({"step": "cleanup script", "http": st})
    st, _ = req("DELETE", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "cleanup node", "http": st})
    st, _ = req("GET", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "GET node after cleanup (expect 404)", "http": st})

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
