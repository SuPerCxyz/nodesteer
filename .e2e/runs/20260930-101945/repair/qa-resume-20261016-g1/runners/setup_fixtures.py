"""G1 A 批/前置：在隔离实例内创建受限账号、owned child Agent、自有夹具与多页节点集。

只在隔离实例（含其 data_dir 与 owned child 目录）内写入；不触碰 repair :18080 / primary :8080。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def create_user(hub, admin, username, password, role) -> dict:
    r = hub.post("/api/users", admin, body={"username": username, "password": password, "role": role})
    return {"http": r["status"], "id": (r["json"] or {}).get("id") if isinstance(r["json"], dict) else None,
            "role": (r["json"] or {}).get("role") if isinstance(r["json"], dict) else None}


def enroll(hub, admin, name, node_ip, base_url) -> dict:
    r = hub.post("/api/nodes/enrollment", admin,
                 body={"node_name": name, "node_ip": node_ip, "hub_address": base_url})
    j = r["json"] if isinstance(r["json"], dict) else {}
    return {"http": r["status"], "node_id": j.get("node_id"), "agent_id": j.get("agent_id")}


def start_child(nonce, node_id, agent_id, reg_token) -> dict:
    root = f"/var/lib/nodesteer-qa-{nonce}-child"
    cfg = f"{root}/child.yaml"
    data = f"{root}/data"
    log = f"{root}/child.log"
    yaml_text = (
        f'hub_url: "ws://127.0.0.1:18085"\n'
        f'registration_token: "{reg_token}"\n'
        f'agent_id: "{agent_id}"\n'
        f'node_name: "qa-{nonce}-child"\n'
        f'node_ip: "192.168.100.209"\n'
        f'deployment_mode: "native"\n'
        f'data_dir: "{data}"\n'
        f'heartbeat_sec: 5\n'
        f'revision_check_sec: 5\n'
        f'max_log_bytes: 262144\n'
    )
    script = f"""
set -eu
umask 077
install -d -m 0700 {root}
install -d -m 0700 {data}
printf '%s' '{C.b64(yaml_text)}' | base64 -d > {cfg}
chmod 0600 {cfg}
setsid nohup {C.REMOTE_AGENT} --config {cfg} >> {log} 2>&1 < /dev/null &
sleep 2
pgrep -af 'qa-{nonce}-child' | sed 's/^/CHILD_PID: /' || true
echo "CFG_MODE:$(stat -c '%a' {cfg})"
"""
    cp = C.ssh(script, timeout=90)
    return {"root": root, "config": cfg, "data_dir": data, "log": log,
            "out": cp.stdout.strip().splitlines()}


def wait_online(hub, admin, node_id, timeout=90):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = hub.get(f"/api/nodes/{node_id}", admin)
        n = r["json"] if isinstance(r["json"], dict) else {}
        last = {k: n.get(k) for k in ("id", "hostname", "status", "sync_status",
                                     "deployment_mode", "agent_version", "global_revision")}
        if n.get("status") == "online" and n.get("sync_status") == "synced":
            return last
        time.sleep(3)
    return {"timeout": True, "last": last}


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    base = cr["base_url"]
    out = {"nonce": nonce, "at_utc": C.utc(), "authority": base}

    # ---- 1. 受限账号（operator / viewer）----
    op_user, vw_user = f"qa-{nonce}-op1", f"qa-{nonce}-vw1"
    op_pw, vw_pw = C.gen_password(), C.gen_password()
    out["users"] = {
        "operator": {**create_user(hub, admin, op_user, op_pw, "operator"), "username": op_user},
        "viewer": {**create_user(hub, admin, vw_user, vw_pw, "viewer"), "username": vw_user},
    }
    # 受限账号登录自检（凭据仅在本地内存/凭据文件中）
    for who, un, pw in (("operator", op_user, op_pw), ("viewer", vw_user, vw_pw)):
        lg = hub.login(un, pw)
        me = hub.get("/api/me", (lg["json"] or {}).get("token")) if lg["status"] == 200 else {"status": 0}
        out["users"][who]["login_http"] = lg["status"]
        out["users"][who]["me_http"] = me["status"]
        out["users"][who]["me_role"] = (me["json"] or {}).get("Role") or (me["json"] or {}).get("role") \
            if isinstance(me["json"], dict) else None

    # ---- 2. owned child Agent（native，指向隔离实例）----
    name = f"qa-{nonce}-child"
    en = enroll(hub, admin, name, "192.168.100.209", base)
    out["enrollment"] = en
    if not en["node_id"]:
        raise RuntimeError("enrollment failed: " + json.dumps(en))
    out["owned_child"] = start_child(nonce, en["node_id"], en["agent_id"], cr["registration_token"])
    out["node_after_start"] = wait_online(hub, admin, en["node_id"])
    if out["node_after_start"].get("timeout"):
        raise RuntimeError("owned child did not reach online/synced: " + json.dumps(out["node_after_start"]))

    node_id = en["node_id"]

    # ---- 3. 自有夹具：script / task / schedule / application + assign ----
    sc = hub.post("/api/scripts", admin, body={
        "name": f"qa-{nonce}-script", "description": "g1 fixture", "interpreter": "bash",
        "content": "echo g1-fixture-ok", "parameters": [], "environment": {},
        "working_dir": "/tmp", "timeout": 30, "enabled": True})
    task = hub.post("/api/tasks", admin, body={
        "name": f"qa-{nonce}-task", "type": "command", "command": "echo g1-fixture-ok",
        "target": {"type": "node", "node_ids": [node_id]}, "parameters": [], "param_values": {},
        "timeout": 60, "retry": 0, "offline_policy": "allow_offline", "enabled": True})
    task_id = (task["json"] or {}).get("id") if isinstance(task["json"], dict) else None
    sched = hub.post("/api/schedules", admin, body={
        "task_id": task_id, "type": "interval", "interval_sec": 3600, "execution_owner": "hub",
        "timezone": "UTC", "offline_policy": "allow_offline", "misfire_policy": "run_once",
        "enabled": True})
    app = hub.post("/api/applications", admin, body={
        "name": f"qa-{nonce}-app", "description": "g1 fixture", "artifact_id": "",
        "version": "1.0.0", "binary_path": f"/usr/local/bin/qa-{nonce}",
        "arguments": [], "environment": {}, "config": "", "config_path": f"/etc/qa-{nonce}.conf",
        "unit_name": f"nodesteer-qa-{nonce}.service"})
    app_id = (app["json"] or {}).get("id") if isinstance(app["json"], dict) else None
    assign = hub.post(f"/api/applications/{app_id}/assign", admin, body={"node_ids": [node_id]})
    out["fixtures"] = {
        "script": {"http": sc["status"], "id": (sc["json"] or {}).get("id") if isinstance(sc["json"], dict) else None},
        "task": {"http": task["status"], "id": task_id},
        "schedule": {"http": sched["status"], "id": (sched["json"] or {}).get("id") if isinstance(sched["json"], dict) else None},
        "application": {"http": app["status"], "id": app_id},
        "app_assign": {"http": assign["status"], "body": assign["json"]},
    }
    out["assignment_after"] = hub.get(f"/api/applications/{app_id}/nodes", admin)["json"]

    # ---- 4. X14：多页节点集（12 个 pending + owned child = 13）----
    pending = []
    for i in range(1, 13):
        r = enroll(hub, admin, f"qa-{nonce}-n{i:02d}", f"10.42.0.{10 + i}", None)
        pending.append(r)
    out["x14_pending_nodes"] = pending
    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])
    out["node_total"] = len(nodes)
    out["node_summary"] = sorted(({"id": n.get("id"), "hostname": n.get("hostname"),
                                   "status": n.get("status"), "address": n.get("address")}
                                  for n in nodes), key=lambda x: x["hostname"] or "")

    # ---- 5. 审计基线 ----
    code, rows = C.audit_rows(hub, admin, limit=500)
    out["audit_baseline"] = {"http": code, "count": len(rows),
                             "actions": sorted({r.get("Action") for r in rows if isinstance(r, dict)})}

    (C.ROOT / "fixture-ledger.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")

    creds = C.load_creds(nonce)
    creds.update({"operator_username": op_user, "operator_password": op_pw,
                  "viewer_username": vw_user, "viewer_password": vw_pw,
                  "node_id": node_id, "agent_id": en["agent_id"],
                  "task_id": task_id, "application_id": app_id,
                  "script_id": out["fixtures"]["script"]["id"],
                  "schedule_id": out["fixtures"]["schedule"]["id"],
                  "child_root": out["owned_child"]["root"],
                  "child_config": out["owned_child"]["config"],
                  "child_data_dir": out["owned_child"]["data_dir"],
                  "child_log": out["owned_child"]["log"]})
    C.save_creds(nonce, creds)

    safe = dict(out)
    safe.pop("x14_pending_nodes", None)
    print(json.dumps(safe, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
