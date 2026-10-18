"""p-api：AUTH-RELAY 真实 Relay 归属验证（隔离实例 + 两个自建 owned Agent）。

真实链路：admin 创建 Transfer（源/目标均为本实例真实在线 Agent）→ 源 Agent 通过
/agent/transfers/{id}/upload 上传 → Hub 落盘并计算 SHA256 → 目标 Agent 通过
/agent/transfers/{id}/download 下载并校验 → 物理源/目标 size/SHA 一致 → 无 .part 残留。

同时验证冒充矩阵：匿名 / 用户会话 / 其它 Agent / 错配 id+token / 方法边界。

产出 evidence：AUTH-RELAY-*.json；fixture-ledger.json 记录 Agent 与临时文件归属。
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papi_common as C  # noqa: E402

MAIN_TAG = "papim"
AGENT_BIN = "/opt/nodesteer-repair-20260930-101945/agent-static/nodesteer-agent"


def host_sha_size(path: str) -> dict:
    cp = C.ssh(f"if [ -f {path} ]; then sha256sum {path} | awk '{{print $1}}'; "
               f"stat -c '%s' {path}; else echo MISSING; fi")
    lines = [x.strip() for x in cp.stdout.splitlines() if x.strip()]
    if not lines or lines[0] == "MISSING":
        return {"exists": False}
    return {"exists": True, "sha256": lines[0], "size": int(lines[1])}


def read_setting(db: str, key: str) -> str | None:
    script = f"""python3 - <<'PY'
import sqlite3, json
db = sqlite3.connect("file:{db}?mode=ro", uri=True, timeout=5)
row = db.execute("SELECT value FROM settings WHERE key=?", ({json.dumps(key)},)).fetchone()
db.close()
print(row[0] if row else "")
PY"""
    return C.ssh(script).stdout.strip() or None


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    inst = next(i for i in led["instances"] if i["nonce"].startswith(MAIN_TAG))
    cr = C.load_creds(inst["nonce"])
    nonce = inst["nonce"]
    port = inst["port"]
    reg = cr["registration_token"]
    data_dir = cr["data_dir"]
    base = f"http://127.0.0.1:{cr['tunnel_port']}"
    hub = C.Hub(base)
    admin = hub.token(cr["admin_username"], cr["admin_password"])

    work = f"/tmp/qa-papi-{nonce}-relay"
    src_path = f"{work}/src.bin"
    dst_path = f"{work}/dst.bin"
    agents = {}
    fixtures = {
        "captured_at_utc": C.utc(), "instance_nonce": nonce, "instance_port": port,
        "work_dir": work, "agents": [], "temp_files": [], "note": None,
    }

    # ---------- 1. 准备源文件 + 两个 owned Agent ----------
    prep = f"""
set -eu
mkdir -p {work}
head -c 524288 /dev/urandom > {src_path}
chmod 0644 {src_path}
rm -f {dst_path}
sha256sum {src_path} | awk '{{print "SRC_SHA:"$1}}'
stat -c 'SRC_SIZE:%s' {src_path}
"""
    print(C.ssh(prep).stdout.strip())
    src_meta = host_sha_size(src_path)

    for role, label in (("src", "source"), ("dst", "target")):
        node_name = f"qa-papi-{nonce}-{role}"
        a_data = f"/var/lib/nodesteer-qa-papi-{nonce}-agent-{role}"
        a_cfg = f"/etc/nodesteer/qa-papi-{nonce}-agent-{role}.yaml"
        a_log = f"/var/log/nodesteer-qa-papi-{nonce}-agent-{role}.log"
        cfg = (
            f'hub_url: "ws://{C.HOST}:{port}"\n'
            f'registration_token: "{reg}"\n'
            f'node_name: "{node_name}"\n'
            f'node_ip: "{C.HOST}"\n'
            f'deployment_mode: "native"\n'
            f'host_integration: false\n'
            f'data_dir: "{a_data}"\n'
            f'heartbeat_sec: 2\n'
            f'revision_check_sec: 2\n'
        )
        script = f"""
set -eu
umask 077
install -d -m 0750 {a_data}
printf '%s' '{C.b64(cfg)}' | base64 -d > {a_cfg}
chmod 0600 {a_cfg}
setsid nohup {AGENT_BIN} --config {a_cfg} >> {a_log} 2>&1 < /dev/null &
sleep 2
echo "PGREP_{role}:$(pgrep -fc 'qa-papi-{nonce}-agent-{role}')"
"""
        r = C.ssh(script)
        agents[role] = {"role": label, "node_name": node_name, "agent_role": role,
                        "data_dir": a_data, "config_path": a_cfg, "log_path": a_log,
                        "start": r.stdout.strip()}
        fixtures["agents"].append({k: agents[role][k] for k in
                                   ("role", "node_name", "data_dir", "config_path", "log_path")})
        fixtures["temp_files"].append({"path": a_data, "kind": "agent_data_dir"})
        fixtures["temp_files"].append({"path": a_cfg, "kind": "agent_config"})
        fixtures["temp_files"].append({"path": a_log, "kind": "agent_log"})

    # 等待两个节点 online
    def find_node(name):
        lst = hub.req("GET", "/api/nodes", token=admin)
        for n in (lst["json"] or []):
            if n.get("hostname") == name:
                return n
        return None

    deadline = time.time() + 60
    resolved = {}
    while time.time() < deadline and len(resolved) < 2:
        for role in ("src", "dst"):
            if role in resolved:
                continue
            n = find_node(agents[role]["node_name"])
            if n and n.get("status") == "online":
                resolved[role] = n
        time.sleep(2)
    for role, n in resolved.items():
        agents[role]["node_id"] = n["id"]
        agents[role]["agent_id"] = n["agent_id"]
        agents[role]["status"] = n["status"]
    if len(resolved) < 2:
        C.write_evidence("AUTH-RELAY-blocked.json",
                         C.stub({"hub": base, "reason": "agents_not_online",
                                 "resolved": {k: v.get("status") for k, v in resolved.items()},
                                 "fixtures": fixtures}))
        (C.ROOT / "fixture-ledger.json").write_text(
            json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"status": "BLOCKED", "resolved": list(resolved)}, indent=1))
        return

    src_id, dst_id = agents["src"]["node_id"], agents["dst"]["node_id"]
    src_agent_id, dst_agent_id = agents["src"]["agent_id"], agents["dst"]["agent_id"]
    # Agent 凭据仅从 Hub settings 只读提取，不出现在 stdout/evidence
    src_cred = read_setting(f"{data_dir}/hub.db", f"cred:{src_id}")
    dst_cred = read_setting(f"{data_dir}/hub.db", f"cred:{dst_id}")
    creds_ok = bool(src_cred) and bool(dst_cred)
    fixtures["credential_handling"] = "read-only from hub.db settings; never printed or persisted"

    # ---------- 2. 真实 Transfer 全链路 ----------
    tr = hub.req("POST", "/api/transfers", token=admin, body={
        "source_node_id": src_id, "source_path": src_path,
        "targets": [{"node_id": dst_id, "destination_path": dst_path, "mode": 420}],
    })
    transfer = {"create_http": tr["status"], "create_body_keys":
                sorted((tr["json"] or {}).keys()) if isinstance(tr["json"], dict) else None}
    tid = (tr["json"] or {}).get("id") if isinstance(tr["json"], dict) else None
    audit_before = hub.req("GET", "/api/transfers", token=admin)
    deadline = time.time() + 90
    final = None
    while tid and time.time() < deadline:
        g = hub.req("GET", f"/api/transfers/{tid}", token=admin)
        final = g["json"] if isinstance(g["json"], dict) else None
        if final and final.get("status") in ("SUCCESS", "FAILED", "CANCELED"):
            break
        time.sleep(2)
    transfer["transfer_id"] = tid
    transfer["final_status"] = (final or {}).get("status")
    transfer["final_size"] = (final or {}).get("size")
    transfer["final_sha256"] = (final or {}).get("sha256")
    transfer["targets"] = (final or {}).get("targets")
    transfer["source_size"] = src_meta.get("size")
    transfer["source_sha256"] = src_meta.get("sha256")

    # 物理核验：Hub blob + 目标文件 + .part 残留
    blob = host_sha_size(f"{data_dir}/file-transfers/{tid}.blob")
    part = C.ssh(f"ls {data_dir}/file-transfers/ | sed 's/^/F:/'").stdout.strip().splitlines()
    dst_meta = host_sha_size(dst_path)
    transfer["hub_blob"] = blob
    transfer["hub_transfer_dir_listing"] = part
    transfer["destination_file"] = dst_meta
    transfer["source_dst_sha_match"] = (src_meta.get("sha256") == dst_meta.get("sha256")
                                        and src_meta.get("sha256") is not None)
    transfer["hub_blob_sha_match"] = (src_meta.get("sha256") == blob.get("sha256"))
    transfer["no_part_leftover"] = not any(x.endswith(".part") for x in part)

    C.write_evidence("AUTH-RELAY-transfer-roundtrip.json",
                     C.stub({"hub": base, "source": src_meta, "transfer": transfer,
                             "agents": {k: {"node_id": v.get("node_id"), "agent_id": v.get("agent_id"),
                                            "node_name": v.get("node_name"), "status": v.get("status")}
                                        for k, v in agents.items()}}))

    # ---------- 2b. Hub 暂存物理文件 size/SHA + cancel 清理核验 ----------
    # 目标为未连接的 pending 节点：源 Agent 真实上传后 blob 保持暂存，可核验物理文件；
    # 然后 cancel 触发 cleanupFiles，核验 .part/.blob 均被清理。
    stall = {"note": "目标为 pending 节点，不会自动交付，便于核验 Hub 暂存物理文件与 cancel 清理"}
    ctrl = hub.req("POST", "/api/nodes/enrollment", token=admin,
                   body={"node_name": f"qa-papi-{nonce}-stall", "node_ip": C.HOST})
    stall["control_node_http"] = ctrl["status"]
    stall["control_node_id_present"] = bool((ctrl["json"] or {}).get("node_id"))
    ctrl_id = (ctrl["json"] or {}).get("node_id")
    if ctrl_id:
        st = hub.req("POST", "/api/transfers", token=admin, body={
            "source_node_id": src_id, "source_path": src_path,
            "targets": [{"node_id": ctrl_id, "destination_path": f"{work}/never.bin", "mode": 420}],
        })
        stall["create_http"] = st["status"]
        sid = (st["json"] or {}).get("id") if isinstance(st["json"], dict) else None
        stall["transfer_id"] = sid
        deadline = time.time() + 60
        cur = {}
        while sid and time.time() < deadline:
            g = hub.req("GET", f"/api/transfers/{sid}", token=admin)
            cur = g["json"] if isinstance(g["json"], dict) else {}
            if cur.get("sha256"):
                break
            time.sleep(2)
        stall["status_after_source_upload"] = cur.get("status")
        stall["sha256_after_source_upload"] = cur.get("sha256")
        stall["size_after_source_upload"] = cur.get("size")
        listing = C.ssh(f"ls -l {data_dir}/file-transfers/ | sed 's/^/L:/'").stdout.strip().splitlines()
        stall["transfer_dir_listing_during_stall"] = listing
        stall["blob_size_sha"] = host_sha_size(f"{data_dir}/file-transfers/{sid}.blob")
        stall["blob_matches_source"] = stall["blob_size_sha"].get("sha256") == src_meta.get("sha256")
        cx = hub.req("POST", f"/api/transfers/{sid}/cancel", token=admin)
        stall["cancel_http"] = cx["status"]
        stall["cancel_status"] = (cx["json"] or {}).get("status") if isinstance(cx["json"], dict) else None
        stall["blob_after_cancel"] = host_sha_size(f"{data_dir}/file-transfers/{sid}.blob")
        listing_after = C.ssh(f"ls -l {data_dir}/file-transfers/ | sed 's/^/L:/'").stdout.strip().splitlines()
        stall["transfer_dir_listing_after_cancel"] = listing_after
        stall["cleanup_verified"] = (not stall["blob_after_cancel"].get("exists")
                                     and not any(f"{sid}." in x for x in listing_after))
    C.write_evidence("AUTH-RELAY-hub-staging-and-cleanup.json",
                     C.stub({"hub": base, "source": src_meta, "staging": stall}))

    # ---------- 3. 冒充 / 越权矩阵 ----------
    tpath = f"/agent/transfers/{tid}"
    def hdr(aid, tok):
        return {"X-NodeSteer-Agent-ID": aid, "X-NodeSteer-Agent-Token": tok}

    probes = [
        {"id": "anon_download", "method": "GET", "path": tpath + "/download", "headers": {}},
        {"id": "user_bearer_download", "method": "GET", "path": tpath + "/download",
         "headers": {"Authorization": "Bearer " + admin}},
        {"id": "user_bearer_as_agent_token", "method": "GET", "path": tpath + "/download",
         "headers": hdr(src_agent_id, admin)},
        {"id": "source_agent_download_not_target", "method": "GET", "path": tpath + "/download",
         "headers": hdr(src_agent_id, src_cred)},
        {"id": "target_agent_upload_not_source", "method": "POST", "path": tpath + "/upload",
         "headers": hdr(dst_agent_id, dst_cred)},
        {"id": "mismatched_id_token", "method": "GET", "path": tpath + "/download",
         "headers": hdr(src_agent_id, dst_cred)},
        {"id": "wrong_method_get_upload", "method": "GET", "path": tpath + "/upload",
         "headers": hdr(src_agent_id, src_cred)},
        {"id": "wrong_method_post_download", "method": "POST", "path": tpath + "/download",
         "headers": hdr(dst_agent_id, dst_cred)},
        {"id": "unknown_transfer_id", "method": "GET",
         "path": f"/agent/transfers/{uuid.uuid4()}/download", "headers": hdr(dst_agent_id, dst_cred)},
        {"id": "missing_action_segment", "method": "GET", "path": tpath,
         "headers": hdr(src_agent_id, src_cred)},
        {"id": "unknown_action", "method": "GET", "path": tpath + "/nope",
         "headers": hdr(src_agent_id, src_cred)},
        {"id": "agent_token_query_param_only", "method": "GET", "path": tpath + "/download",
         "headers": {"X-NodeSteer-Agent-ID": src_agent_id}},
    ]
    rows = []
    for p in probes:
        r = hub.req(p["method"], p["path"], headers=p["headers"])
        rows.append({"probe": p["id"], "method": p["method"], "path": p["path"],
                     **C.body_summary(r)})
    # transfer 状态在冒充尝试后未变
    after = hub.req("GET", f"/api/transfers/{tid}", token=admin)
    after_j = after["json"] if isinstance(after["json"], dict) else {}
    state_unchanged = (after_j.get("sha256") == transfer["final_sha256"]
                       and after_j.get("status") == transfer["final_status"])
    C.write_evidence("AUTH-RELAY-impersonation-matrix.json",
                     C.stub({"hub": base, "transfer_id": tid, "credentials_present": creds_ok,
                             "matrix": rows,
                             "transfer_state_after_probes": {
                                 "status": after_j.get("status"), "sha256": after_j.get("sha256"),
                                 "unchanged": state_unchanged}}))

    summary = {
        "agents_online": {role: agents[role].get("status") for role in ("src", "dst")},
        "credentials_read_only_from_hub_db": creds_ok,
        "roundtrip": {
            "create_http": transfer["create_http"], "final_status": transfer["final_status"],
            "final_size": transfer["final_size"], "final_sha256": transfer["final_sha256"],
            "source_size": transfer["source_size"], "source_sha256": transfer["source_sha256"],
            "hub_blob": blob, "destination_file": dst_meta,
            "source_dst_sha_match": transfer["source_dst_sha_match"],
            "hub_blob_sha_match": transfer["hub_blob_sha_match"],
            "no_part_leftover": transfer["no_part_leftover"],
            "targets_statuses": [t.get("status") for t in (transfer["targets"] or [])],
        },
        "impersonation": {r["probe"]: r["status"] for r in rows},
        "impersonation_state_unchanged": state_unchanged,
        "hub_transfers_total_after": len(audit_before["json"] or []) if isinstance(audit_before["json"], list) else None,
        "hub_staging_and_cleanup": {
            "status_after_source_upload": stall.get("status_after_source_upload"),
            "hub_blob_size": stall.get("blob_size_sha", {}).get("size"),
            "hub_blob_sha256": stall.get("blob_size_sha", {}).get("sha256"),
            "blob_matches_source": stall.get("blob_matches_source"),
            "cancel_http": stall.get("cancel_http"),
            "cancel_status": stall.get("cancel_status"),
            "cleanup_verified": stall.get("cleanup_verified"),
        },
    }
    C.write_evidence("AUTH-RELAY-summary.json", C.stub(summary))
    (C.ROOT / "relay-probe-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    fixtures["transfer"] = {"transfer_id": tid, "source_path": src_path, "destination_path": dst_path,
                            "hub_blob": f"{data_dir}/file-transfers/{tid}.blob"}
    fixtures["temp_files"].append({"path": work, "kind": "relay_work_dir"})
    (C.ROOT / "fixture-ledger.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
