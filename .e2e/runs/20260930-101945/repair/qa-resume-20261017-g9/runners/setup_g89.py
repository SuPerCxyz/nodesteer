"""G8+G9 前置夹具（全部在隔离实例内，共享同一实例）。

- owned child Agent（CR-1 构建）→ G8 的真实执行目标 + G9 中唯一 online 节点；
- G8 夹具：1 个 script（含 secret 默认值）+ 6 个 task（execution/错误路径探针）；
- G9 数据集：22 个 pending 纳管节点（自有 qa- 前缀）+ owned child = 23 行（pageSize=10 → 3 页）；
- 审计基线。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402

PROBE = {
    "full": 'printf "%s\\n" "$NODESTEER_PARAM_marker_secret"',
    "split": ('printf "%s" "${NODESTEER_PARAM_marker_secret%????????}"; '
              'sleep 1.5; printf "%s\\n" "${NODESTEER_PARAM_marker_secret#????????}"'),
    "stderr": 'printf "ERR-%s-END\\n" "$NODESTEER_PARAM_marker_secret" >&2',
    "fail": ('printf "%s" "${NODESTEER_PARAM_marker_secret#????????}"; '
             'printf "%s\\n" "${NODESTEER_PARAM_marker_secret%????????}" >&2; exit 7'),
}


def enroll(hub, admin, name, node_ip, base_url) -> dict:
    r = hub.post("/api/nodes/enrollment", admin,
                 body={"node_name": name, "node_ip": node_ip, "hub_address": base_url})
    j = r["json"] if isinstance(r["json"], dict) else {}
    return {"http": r["status"], "node_id": j.get("node_id"), "agent_id": j.get("agent_id")}


def start_child(nonce, agent_id, reg_token, port) -> dict:
    root = f"/var/lib/nodesteer-qa-{nonce}-child"
    cfg = f"{root}/child.yaml"
    data = f"{root}/data"
    log = f"{root}/child.log"
    yaml_text = (
        f'hub_url: "ws://127.0.0.1:{port}"\n'
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
install -d -m 0700 {data}
printf '%s' '{C.b64(yaml_text)}' | base64 -d > {cfg}
chmod 0600 {cfg}
setsid nohup {root}/bin/nodesteer-agent --config {cfg} >> {log} 2>&1 < /dev/null &
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
        n = hub.get(f"/api/nodes/{node_id}", admin)["json"]
        n = n if isinstance(n, dict) else {}
        last = {k: n.get(k) for k in ("id", "hostname", "status", "sync_status",
                                     "deployment_mode", "agent_version")}
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
    tag = nonce[-8:]
    out = {"nonce": nonce, "at_utc": C.utc(), "authority": base}

    # 非敏感标记值（按用例要求只用标记值；允许出现在 evidence 中，用于证明遮蔽行为）
    markers = {
        "script_default": f"QAMARK-SCRIPTDEFAULT-{tag}-0123456789abcdef",
        "task_default": f"QAMARK-TASKDEFAULT-{tag}-fedcba9876543210",
        "task_value": f"QAMARK-TASKVALUE-{tag}-abcdef0123456789",
        "revision": f"QAMARK-REVISION-{tag}-0123456789abcdef",
        "ui_input": f"QAMARK-UIINPUT-{tag}-0123456789abcdef",
        "error": f"QAMARK-ERRPATH-{tag}-0123456789abcdef",
    }
    out["markers"] = markers

    # ---- 1. owned child Agent（CR-1）----
    name = f"qa-{nonce}-child"
    en = enroll(hub, admin, name, "192.168.100.209", base)
    out["enrollment"] = en
    if not en["node_id"]:
        raise RuntimeError("enrollment failed: " + json.dumps(en))
    out["owned_child"] = start_child(nonce, en["agent_id"], cr["registration_token"], cr["host_port"])
    out["node_after_start"] = wait_online(hub, admin, en["node_id"])
    if out["node_after_start"].get("timeout"):
        raise RuntimeError("owned child not online: " + json.dumps(out["node_after_start"]))
    node_id = en["node_id"]

    # ---- 2. G8 夹具：script（含 secret 默认值）----
    script_content = (
        "#!/bin/sh\n"
        'S="${NODESTEER_PARAM_marker_secret}"\n'
        'case "${NODESTEER_PARAM_probe}" in\n'
        '  full) printf "%s\\n" "$S" ;;\n'
        '  split) printf "%s" "${S%????????}"; sleep 1.5; printf "%s\\n" "${S#????????}" ;;\n'
        '  stderr) printf "ERR-%s-END\\n" "$S" >&2 ;;\n'
        '  fail) printf "%s" "${S#????????}"; printf "%s\\n" "${S%????????}" >&2; exit 7 ;;\n'
        '  *) echo "probe=${NODESTEER_PARAM_probe}" ;;\n'
        "esac\n"
        f'echo "qa-{nonce}-script-done"\n'
    )
    sc = hub.post("/api/scripts", admin, body={
        "name": f"qa-{nonce}-script", "description": f"g89 fixture {tag}", "interpreter": "bash",
        "content": script_content,
        "parameters": [
            {"name": "probe", "type": "string", "required": False, "default": "full"},
            {"name": "marker_secret", "type": "secret", "required": False,
             "default": markers["script_default"]},
        ],
        "environment": {}, "working_dir": "/tmp", "timeout": 30, "enabled": True})
    script_id = (sc["json"] or {}).get("id") if isinstance(sc["json"], dict) else None
    out["script"] = {"http": sc["status"], "id": script_id,
                     "roundtrip_contains_script_default": markers["script_default"] in json.dumps(sc["json"] or {})}

    # ---- 3. G8 夹具：task 探针 ----
    tasks = {}
    mk = lambda nm, body: (lambda r: {"http": r["status"],
                                      "id": (r["json"] or {}).get("id") if isinstance(r["json"], dict) else None,
                                      "roundtrip_contains_task_default": markers["task_default"] in json.dumps(r["json"] or {}),
                                      "resp_err": (r["json"] or {}).get("error") if isinstance(r["json"], dict) else None})(body)

    def make_task(nm, cmd, params, param_values, ttype="command", script_id_val=None):
        body = {"name": nm, "type": ttype, "target": {"type": "node", "node_ids": [node_id]},
                "parameters": params, "param_values": param_values, "timeout": 60, "retry": 0,
                "offline_policy": "allow_offline", "enabled": True}
        if ttype == "command":
            body["command"] = cmd
        else:
            body["script_id"] = script_id_val
        r = hub.post("/api/tasks", admin, body=body)
        rec = {"http": r["status"],
               "id": (r["json"] or {}).get("id") if isinstance(r["json"], dict) else None,
               "resp_err": (r["json"] or {}).get("error") if isinstance(r["json"], dict) else None}
        rec["roundtrip_contains_task_default"] = markers["task_default"] in json.dumps(r["json"] or {})
        rec["roundtrip_contains_task_value"] = markers["task_value"] in json.dumps(r["json"] or {})
        return rec

    secret_param_default_task = [{"name": "marker_secret", "type": "secret", "required": False,
                                  "default": markers["task_default"]}]
    secret_param_value_task = [{"name": "marker_secret", "type": "secret", "required": False}]
    tasks["full"] = make_task(f"qa-{nonce}-task-full", PROBE["full"], secret_param_default_task,
                              {"marker_secret": markers["task_value"]})
    tasks["split"] = make_task(f"qa-{nonce}-task-split", PROBE["split"], secret_param_value_task,
                               {"marker_secret": markers["task_value"]})
    tasks["stderr"] = make_task(f"qa-{nonce}-task-stderr", PROBE["stderr"], secret_param_value_task,
                                {"marker_secret": markers["task_value"]})
    tasks["fail"] = make_task(f"qa-{nonce}-task-fail", PROBE["fail"], secret_param_value_task,
                              {"marker_secret": markers["task_value"]})
    tasks["script_default"] = make_task(f"qa-{nonce}-task-scriptdefault", "", [], {},
                                        ttype="script", script_id_val=script_id)
    tasks["missing_required"] = make_task(
        f"qa-{nonce}-task-required", "echo never",
        [{"name": "marker_secret", "type": "secret", "required": True,
          "default": markers["error"]}], {})
    out["tasks"] = tasks

    # ---- 4. G9 数据集：22 个 pending 纳管 + child = 23 行 ----
    pending = []
    for i in range(1, 23):
        pending.append(enroll(hub, admin, f"qa-{nonce}-n{i:02d}", f"10.42.0.{10 + i}", None))
    out["pending_nodes"] = pending
    nodes = C.as_list(hub.get("/api/nodes", admin)["json"])
    out["node_total"] = len(nodes)
    out["node_summary"] = sorted(
        ({"id": n.get("id"), "hostname": n.get("hostname"), "status": n.get("status")}
         for n in nodes), key=lambda x: x["hostname"] or "")

    # ---- 5. 审计基线 ----
    code, rows = C.audit_rows(hub, admin, limit=500)
    out["audit_baseline"] = {"http": code, "count": len(rows),
                             "actions": sorted({r.get("Action") for r in rows if isinstance(r, dict)})}

    (C.EVID_DIRS["g9"].parent / "fixture-ledger.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n")

    cr.update({"markers": markers, "node_id": node_id, "agent_id": en["agent_id"],
               "script_id": script_id, "child_root": out["owned_child"]["root"],
               "child_config": out["owned_child"]["config"],
               "child_log": out["owned_child"]["log"],
               "task_full": tasks["full"]["id"], "task_split": tasks["split"]["id"],
               "task_stderr": tasks["stderr"]["id"], "task_fail": tasks["fail"]["id"],
               "task_script_default": tasks["script_default"]["id"],
               "task_missing_required": tasks["missing_required"]["id"],
               "pending_node_ids": [p["node_id"] for p in pending]})
    C.save_creds(nonce, cr)

    safe = dict(out)
    safe.pop("pending_nodes", None)
    print(json.dumps(safe, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
