"""修复/补齐隔离实例内的自有夹具（前次探针误删时使用）。仅隔离实例。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    cr = C.load_creds(nonce)
    hub = C.Hub(cr["local_base"])
    admin = hub.token(cr["admin_username"], cr["admin_password"])
    fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    node = cr["node_id"]
    ids = {k: v.get("id") for k, v in fx["fixtures"].items() if isinstance(v, dict)}
    report = {}

    def exists(path, rid):
        return isinstance(rid, str) and hub.get(f"{path}/{rid}", admin)["status"] == 200

    if not exists("/api/scripts", ids.get("script")):
        r = hub.post("/api/scripts", admin, body={"name": f"qa-{nonce}-script", "interpreter": "bash",
                                                 "content": "echo g1-fixture-ok", "parameter": [],
                                                 "timeout": 30, "enabled": True})
        fx["fixtures"]["script"] = {"id": (r["json"] or {}).get("id"), "recreated": True}
        report["script"] = "recreated"
    if not exists("/api/tasks", ids.get("task")):
        r = hub.post("/api/tasks", admin, body={"name": f"qa-{nonce}-task", "type": "command",
                                               "command": "echo g1-fixture-ok",
                                               "target": {"type": "node", "node_ids": [node]},
                                               "timeout": 60, "offline_policy": "allow_offline",
                                               "enabled": True})
        fx["fixtures"]["task"] = {"id": (r["json"] or {}).get("id"), "recreated": True}
        report["task"] = "recreated"
    if not exists("/api/applications", ids.get("application")):
        r = hub.post("/api/applications", admin, body={
            "name": f"qa-{nonce}-app", "description": "g1 fixture", "artifact_id": "",
            "version": "1.0.0", "binary_path": f"/usr/local/bin/qa-{nonce}", "arguments": [],
            "environment": {}, "config": "", "config_path": f"/etc/qa-{nonce}.conf",
            "unit_name": f"nodesteer-qa-{nonce}.service"})
        aid = (r["json"] or {}).get("id")
        hub.post(f"/api/applications/{aid}/assign", admin, body={"node_ids": [node]})
        fx["fixtures"]["application"] = {"id": aid, "recreated": True,
                                        "assigned_nodes": hub.get(f"/api/applications/{aid}/nodes", admin)["json"]}
        report["application"] = "recreated+assigned"
    if not exists("/api/schedules", ids.get("schedule")):
        r = hub.post("/api/schedules", admin, body={"task_id": fx["fixtures"]["task"]["id"],
                                                   "type": "interval", "interval_sec": 3600,
                                                   "execution_owner": "hub", "timezone": "UTC",
                                                   "offline_policy": "allow_offline",
                                                   "misfire_policy": "run_once", "enabled": True})
        fx["fixtures"]["schedule"] = {"id": (r["json"] or {}).get("id"), "recreated": True}
        report["schedule"] = "recreated"
    if "probe_user" not in fx:
        r = hub.post("/api/users", admin, body={"username": f"qa-{nonce}-probe-u",
                                               "password": C.gen_password(), "role": "viewer"})
        fx["probe_user"] = (r["json"] or {}).get("username")
        report["probe_user"] = "created"
    fx["fixtures_fixed_at_utc"] = C.utc()
    (C.ROOT / "fixture-ledger.json").write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")

    creds = C.load_creds(nonce)
    creds.update({"task_id": fx["fixtures"]["task"]["id"], "application_id": fx["fixtures"]["application"]["id"],
                  "script_id": fx["fixtures"]["script"]["id"], "schedule_id": fx["fixtures"]["schedule"]["id"]})
    C.save_creds(nonce, creds)
    print(json.dumps({"report": report, "fixtures": fx["fixtures"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
