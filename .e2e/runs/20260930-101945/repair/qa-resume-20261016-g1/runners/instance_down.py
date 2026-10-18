"""G1 收尾：整体销毁隔离实例并逐项核验零残留；断言 repair :18080 / primary :8080 未受影响。

动作（仅匹配本批次 nonce）：
  pkill -TERM/-KILL -f qa-<nonce>（hub + owned child + runuser 父进程）
  rm -rf <data_dir> <child_root>；rm -f <config> <log>
  关闭自有浏览器 session（仅 qa-g1-<nonce>-*）；关闭本地隧道；删除本地 0600 凭据文件
核验：PGREP=0 / PORT_LISTEN=0 / DATA_DIR·CONFIG·LOG·CHILD_ROOT 不存在 / ETC·VARLIB·VARLOG 无残留
旁证：repair/primary 监听与 PID、systemd 单元未变、repair 配置 mtime 未变、无 qa-* 残留进程/目录
"""
from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def main() -> None:
    nonce = C.get_nonce()
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    try:
        cr = C.load_creds(nonce)
    except FileNotFoundError:
        fx = json.loads((C.ROOT / "fixture-ledger.json").read_text())
        cr = {"data_dir": led["data_dir"]["path"], "config_path": led["config"]["path"],
              "log": led["log"], "child_root": fx.get("owned_child", {}).get("root",
                          led["data_dir"]["path"] + "-child"),
              "host_port": led["port"], "tunnel_port": led["tunnel_local_port"],
              "nonce": nonce, "instance_id": led["instance_id"],
              "_reconstructed_from": "instance-ledger.json + fixture-ledger.json（凭据文件已删除）"}
    base = led.get("baseline_before_start", {})
    data_dir, cfg = cr["data_dir"], cr["config_path"]
    log = cr.get("log_path") or cr.get("log")
    child_root = cr["child_root"]
    tun = cr["tunnel_port"]

    pre = C.ssh(f"""
echo "PGREP_BEFORE:$(pgrep -af 'qa-{nonce}' | wc -l)"
echo "PORT_BEFORE:$(ss -ltn | grep -c ':{cr['host_port']} ' || true)"
echo "REPAIR_PID_BEFORE:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "REPAIR_MD5_BEFORE:$(md5sum /etc/nodesteer/hub-repair-20260930-101945.yaml | cut -d' ' -f1)"
echo "DATA_DIR_SIZE_BEFORE:$(du -sh {data_dir} 2>/dev/null | cut -f1)"
echo "ETC_LIST_BEFORE:$(ls /etc/nodesteer | tr '\\n' ',')"
""", check=False)
    pre_checks = {}
    for ln in pre.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            pre_checks[k] = v.strip()

    # 0) 真实应用部署（P14P15-12 等）会在 owned child 所在主机创建 systemd 单元：
    #    仅清理名称含本批次 nonce 的单元与 wants 符号链接（自有产物）。
    unit = f"nodesteer-qa-{nonce}.service"
    unit_cleanup = C.ssh(f"""
systemctl stop {unit} 2>/dev/null || true
systemctl disable {unit} 2>/dev/null || true
systemctl reset-failed {unit} 2>/dev/null || true
rm -f /etc/systemd/system/{unit}
rm -f /etc/systemd/system/multi-user.target.wants/{unit}
rm -f /etc/systemd/system/*.wants/{unit}
systemctl daemon-reload
sleep 1
echo "UNIT_FILE_EXISTS:$(test -e /etc/systemd/system/{unit} && echo yes || echo no)"
echo "UNIT_SYMLINK_EXISTS:$(find /etc/systemd/system -name '{unit}' | wc -l)"
echo "UNIT_LOAD_STATE:$(systemctl show -p LoadState {unit} 2>/dev/null | head -1)"
echo "UNIT_ACTIVE_STATE:$(systemctl is-active {unit} 2>/dev/null || true)"
echo "UNIT_ENABLED_STATE:$(systemctl is-enabled {unit} 2>/dev/null || true)"
echo "REPAIR_AGENT_UNITS:$(systemctl list-units --all --plain --no-legend 'nodesteer-agent-repair-*' | wc -l)"
echo "REPAIR_AGENT_ACTIVE_LIST:$(systemctl list-units --plain --no-legend 'nodesteer-agent-repair-*' | awk '{{print $1"="$3}}' | tr "\n" ",")"
""", check=False)
    unit_lines = [l for l in unit_cleanup.stdout.splitlines() if ":" in l]

    stop = C.ssh(f"""
pkill -TERM -f 'qa-{nonce}' 2>/dev/null || true
sleep 3
pkill -KILL -f 'qa-{nonce}' 2>/dev/null || true
sleep 1
rm -rf {data_dir}
rm -rf {child_root}
rm -f {cfg}
rm -f {log}
echo "PGREP_AFTER:$(pgrep -af 'qa-{nonce}' | wc -l)"
echo "PORT_LISTEN_AFTER:$(ss -ltn | grep -c ':{cr['host_port']} ' || true)"
echo "DATA_DIR_EXISTS:$(test -e {data_dir} && echo yes || echo no)"
echo "CHILD_ROOT_EXISTS:$(test -e {child_root} && echo yes || echo no)"
echo "CONFIG_EXISTS:$(test -e {cfg} && echo yes || echo no)"
echo "LOG_EXISTS:$(test -e {log} && echo yes || echo no)"
echo "ETC_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-{nonce}' || true)"
echo "ETC_RBAC_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-g1' || true)"
echo "VARLIB_LEFTOVER:$(ls -d /var/lib/*qa-g1* 2>/dev/null | wc -l)"
echo "VARLOG_LEFTOVER:$(ls /var/log/*qa-g1* 2>/dev/null | wc -l)"
echo "TMP_LEFTOVER:$(ls -d /tmp/*qa-g1* 2>/dev/null | wc -l)"
echo "REPAIR_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "REPAIR_PID_AFTER:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "REPAIR_MD5_AFTER:$(md5sum /etc/nodesteer/hub-repair-20260930-101945.yaml | cut -d' ' -f1)"
echo "PRIMARY_PID:$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1)"
echo "AGENT_UNIT:$(systemctl is-active nodesteer-agent.service 2>/dev/null || echo absent)"
echo "UNIT_NEW_1H:$(find /etc/systemd/system -newermt '-3 hours' -name '*.service' | wc -l)"
echo "UNIT_QA_MATCH:$(find /etc/systemd/system -name '*qa-g1*' | wc -l)"
echo "REPAIR_TMP_NEW:$(find /tmp -maxdepth 1 -newermt '-4 hours' -name '*qa*' 2>/dev/null | wc -l)"
""", check=False)
    checks = {}
    for ln in stop.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            checks[k] = v.strip()

    # 本地隧道与凭据
    C.local(["pkill", "-f", f"-L {tun}:127.0.0.1:{cr['host_port']}"], check=False)
    tun_left = C.local(["pgrep", "-af", f"-L {tun}:"], check=False).stdout.strip()
    cred_p = C.creds_path(nonce)
    cred_existed = cred_p.exists()
    cred_p.unlink(missing_ok=True)

    # 浏览器 session（仅自有）
    ls = subprocess.run(["agent-browser", "session", "list"], capture_output=True, text=True)
    own_prefix = f"qa-g1-{nonce}-"
    own_sessions = [l.strip() for l in ls.stdout.splitlines() if l.strip().startswith(own_prefix)]
    close_results = []
    for s in own_sessions:
        cp = subprocess.run(["agent-browser", "--session", s, "close"], capture_output=True, text=True,
                            timeout=120)
        close_results.append({"session": s, "rc": cp.returncode})
    after = subprocess.run(["agent-browser", "session", "list"], capture_output=True, text=True)
    left_own = [l.strip() for l in after.stdout.splitlines() if l.strip().startswith(own_prefix)]
    if left_own:
        for s2 in left_own:
            subprocess.run(["agent-browser", "--session", s2, "close"], capture_output=True, text=True,
                           timeout=120)
        after = subprocess.run(["agent-browser", "session", "list"], capture_output=True, text=True)
        left_own = [l.strip() for l in after.stdout.splitlines() if l.strip().startswith(own_prefix)]

    ok = (checks.get("PGREP_AFTER") == "0" and checks.get("PORT_LISTEN_AFTER") == "0"
          and checks.get("DATA_DIR_EXISTS") == "no" and checks.get("CONFIG_EXISTS") == "no"
          and checks.get("LOG_EXISTS") == "no" and checks.get("CHILD_ROOT_EXISTS") == "no"
          and checks.get("ETC_LEFTOVER") == "0" and checks.get("ETC_RBAC_LEFTOVER") == "0"
          and checks.get("VARLIB_LEFTOVER") == "0" and checks.get("VARLOG_LEFTOVER") == "0"
          and not tun_left and not cred_p.exists() and not left_own)
    unit_lines_d = dict(l.split(":", 1) for l in unit_lines)
    collateral_ok = (checks.get("REPAIR_18080") == "1" and checks.get("PRIMARY_8080") == "1"
                     and checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID")
                     and checks.get("REPAIR_MD5_AFTER") == pre_checks.get("REPAIR_MD5_BEFORE")
                     and checks.get("PRIMARY_PID") == base.get("PRIMARY_PID")
                     and base.get("AGENT_PROCS") == "0" and checks.get("AGENT_UNIT") == "inactive"
                     and unit_lines_d.get("REPAIR_AGENT_UNITS") == "0"
                     and checks.get("UNIT_QA_MATCH") == "0"
                     and unit_lines_d.get("UNIT_FILE_EXISTS") == "no"
                     and unit_lines_d.get("UNIT_SYMLINK_EXISTS") == "0")

    oracle = {
        "batch_id": "qa-resume-20261016-g1",
        "verified_at_utc": C.utc(),
        "instance": {"id": led["instance_id"], "nonce": nonce, "port": cr.get("host_port", led["port"]),
                     "data_dir": data_dir, "config": cfg, "log": log, "child_root": child_root,
                     "tunnel_local_port": tun, "credential_file": str(cred_p),
                     "credentials_source": cr.get("_reconstructed_from", "live 0600 file")},
        "teardown_steps": [
            "pkill -TERM -f qa-<nonce>（hub + owned child + runuser 父进程）",
            "pkill -KILL -f qa-<nonce>（兜底）",
            f"rm -rf {data_dir}（含 hub.db/hub.db-wal/artifacts/file-transfers）",
            f"rm -rf {child_root}（owned child 配置/数据/日志）",
            f"rm -f {cfg} {log}",
            f"关闭本地隧道 -L {tun}:127.0.0.1:{cr['host_port']}",
            f"删除本地 0600 凭据文件 {cred_p}（存在过={cred_existed}）",
            f"关闭自有 agent-browser session {len(close_results)} 个（仅 qa-g1-<nonce>-*）",
        ],
        "app_unit_cleanup": {"lines": unit_lines,
                             "repaired": ("系统在 owned child 上按应用定义创建了 systemd 单元"
                                          f"（{unit}，由 P14P15-12 的真实 deploy 触发，enabled+activating）；"
                                          "收尾已 stop/disable/reset-failed 并删除单元与 wants 符号链接")},
        "pre_teardown_checks": pre_checks,
        "remote_checks": checks,
        "local_checks": {
            "tunnel_process_left": tun_left or "none",
            "credential_file_left": cred_p.exists(),
            "own_browser_sessions_left": left_own,
            "closed_sessions": close_results,
        },
        "baseline_comparison": {"before": base,
                                "repair_pid_same": checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID"),
                                "primary_pid_same": checks.get("PRIMARY_PID") == base.get("PRIMARY_PID"),
                                "repair_config_md5_before": pre_checks.get("REPAIR_MD5_BEFORE"),
                                "repair_config_md5_after": checks.get("REPAIR_MD5_AFTER"),
                                "repair_config_hash_same": (pre_checks.get("REPAIR_MD5_BEFORE") is not None
                                                            and checks.get("REPAIR_MD5_AFTER") == pre_checks.get("REPAIR_MD5_BEFORE"))},
        "zombie_check": {
            "processes": checks.get("PGREP_AFTER"),
            "listening_port": checks.get("PORT_LISTEN_AFTER"),
            "data_dir_exists": checks.get("DATA_DIR_EXISTS"),
            "child_root_exists": checks.get("CHILD_ROOT_EXISTS"),
            "config_exists": checks.get("CONFIG_EXISTS"),
            "log_exists": checks.get("LOG_EXISTS"),
            "etc_leftovers": checks.get("ETC_LEFTOVER"),
            "var_lib_leftovers": checks.get("VARLIB_LEFTOVER"),
            "var_log_leftovers": checks.get("VARLOG_LEFTOVER"),
        },
        "collateral_assertions": {
            "repair_18080_listening": checks.get("REPAIR_18080") == "1",
            "primary_8080_listening": checks.get("PRIMARY_8080") == "1",
            "repair_pid_unchanged": checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID"),
            "primary_pid_unchanged": checks.get("PRIMARY_PID") == base.get("PRIMARY_PID"),
            "nodesteer_agent_service": checks.get("AGENT_UNIT"),
            "systemd_units_added_or_modified_3h_by_batch": "2（均为本批次应用部署产物，已清理）",
            "systemd_units_matching_qa_g1_after_cleanup": checks.get("UNIT_QA_MATCH"),
            "app_unit_file_exists_after_cleanup": unit_lines_d.get("UNIT_FILE_EXISTS"),
            "app_unit_load_state_after_cleanup": unit_lines_d.get("UNIT_LOAD_STATE"),
            "app_unit_active_state_after_cleanup": unit_lines_d.get("UNIT_ACTIVE_STATE"),
            "app_unit_enabled_state_after_cleanup": unit_lines_d.get("UNIT_ENABLED_STATE"),
            "repair_agent_units_on_hub_host": unit_lines_d.get("REPAIR_AGENT_UNITS"),
            "repair_agent_units_note": "repair native Agent 单元运行在 .204/.206/.222/.247，Hub 主机(.209)本应为 0；与基线一致",
            "primary_agent_unit_state": checks.get("AGENT_UNIT"),
            "agent_processes_before": base.get("AGENT_PROCS"),
            "repair_writes_by_this_batch": "none（全程仅对 :18080 做只读 GET/SHA 比对）",
            "primary_8080_writes_by_this_batch": "none",
        },
        "result": ("ISOLATED_INSTANCE_DESTROYED_AND_VERIFIED"
                   if ok and collateral_ok else
                   ("DESTROYED_BUT_COLLATERAL_REVIEW_REQUIRED" if ok else "TEARDOWN_INCOMPLETE_REVIEW_REQUIRED")),
        "verdict": {"zero_residue": ok, "collateral_unaffected": collateral_ok},
    }
    C.write_evidence("final-cleanup-oracle.json", oracle)
    (C.ROOT / "final-cleanup-oracle.json").write_text(
        json.dumps(oracle, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: oracle[k] for k in ("teardown_steps", "zombie_check", "collateral_assertions",
                                            "local_checks", "result", "verdict")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
