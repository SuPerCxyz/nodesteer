#!/usr/bin/env python3
"""OBS-20261014-OFFLINE-NODE-MANUAL-ONLINE-NO-SWEEP 第 3 步（能力补齐版）。

隔离实例内：手工置 online + 直接改库补 capabilities（无会话节点没有能力上报）
→ 派发任务，观察是「明确报错」还是「静默挂起」。
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
cred = json.loads(sorted(C.CRED_DIR.glob("tail-creds-*.json"))[-1].read_text())
BASE = f"http://{C.HOST}:{cred['port']}"
DATA = "/var/lib/nodesteer-qa-" + cred["instance"].replace("nodesteer-qa-", "")
OUT = EV / "manual-online-dispatch.json"


def req(method, path, token=None, body=None, timeout=60):
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

    st, nodes = req("GET", "/api/nodes", token)
    cand = [n["id"] for n in nodes if str(n.get("hostname", "")).startswith("qa-")]
    if not cand:
        print("no owned node left"); return
    nid = cand[0]
    res["node"] = nid

    st, _ = req("PUT", f"/api/nodes/{nid}", token, {"status": "online"})
    res["steps"].append({"step": "PUT /api/nodes/{id} {status:online}", "http": st})
    st, n = req("GET", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "status after manual online", "http": st, "status": n.get("status"),
                         "capabilities": n.get("capabilities")})

    # 直接改库补能力（无会话节点永远不会上报 capabilities）
    cp = C.ssh(f"""python3 - <<'PY'
import sqlite3
db = sqlite3.connect('{DATA}/hub.db')
db.execute("update nodes set capabilities_json='{{\\"script\\":true}}' where id=?", ('{nid}',))
db.commit()
print('rows', db.total_changes)
PY""", check=False)
    res["steps"].append({"step": "direct DB: set capabilities_json (isolated instance only)",
                         "rc": cp.returncode, "out": cp.stdout.strip()[:200], "err": cp.stderr.strip()[:200]})
    st, n = req("GET", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "status+capabilities after DB tweak", "http": st,
                         "status": n.get("status"), "capabilities": n.get("capabilities")})

    st, scr = req("POST", "/api/scripts", token,
                  {"name": "qa-tail-dispatch-probe", "content": "echo probe",
                   "interpreter": "shell", "enabled": True})
    sid = scr.get("id")
    st, task = req("POST", "/api/tasks", token,
                   {"name": "qa-tail-dispatch-task", "script_id": sid, "type": "script",
                    "enabled": True, "offline_policy": "hub_online_required",
                    "target": {"type": "node", "node_ids": [nid]}})
    tid = task.get("id") if isinstance(task, dict) else None
    res["steps"].append({"step": "create script+task", "script": sid, "task": tid})

    t0 = time.time()
    st, run = req("POST", f"/api/tasks/{tid}/run", token, {"node_ids": {nid: True}})
    res["steps"].append({"step": "POST /api/tasks/{id}/run → manual-online node without session",
                         "http": st, "body": run, "elapsed_s": round(time.time() - t0, 2)})

    time.sleep(3)
    st, execs = req("GET", f"/api/executions?task_id={tid}", token)
    res["steps"].append({"step": "GET executions (判定：FAILED+block_reason 明确报错 / PENDING 静默挂起)",
                         "http": st,
                         "executions": [{"id": e.get("id"), "status": e.get("status"),
                                         "block_reason": e.get("block_reason"),
                                         "node_id": e.get("node_id"), "offline": e.get("offline")}
                                        for e in (execs if isinstance(execs, list) else [])]})

    # 部署路径：应用 deploy 同门禁（ErrAgentOffline）
    st, apps = req("GET", "/api/applications", token)
    res["steps"].append({"step": "GET /api/applications", "http": st,
                         "count": len(apps) if isinstance(apps, list) else apps})

    for path, name in ((f"/api/tasks/{tid}", "task"), (f"/api/scripts/{sid}", "script"), (f"/api/nodes/{nid}", "node")):
        st, _ = req("DELETE", path, token)
        res["steps"].append({"step": "cleanup " + name, "http": st})
    st, _ = req("GET", f"/api/nodes/{nid}", token)
    res["steps"].append({"step": "GET node after cleanup (expect 404)", "http": st})

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
