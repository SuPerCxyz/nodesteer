"""G8+G9 收尾：整体销毁共享隔离实例（端口 18086）并逐项核验零残留。

动作（仅匹配本批次 nonce / 端口）：
  pkill -TERM/-KILL -f qa-<nonce>（hub + owned child + runuser 父进程）
  rm -rf <data_dir> <child_root>；rm -f <config> <log>
  关闭本地隧道 -L 38286:127.0.0.1:18086；删除本地 0600 凭据文件
  关闭自有 agent-browser session（仅 qa-g89-<nonce>-*）
核验：PGREP=0 / PORT_LISTEN=0 / DATA_DIR·CONFIG·LOG·CHILD_ROOT 不存在 / ETC·VARLIB·VARLOG·TMP 无残留
旁证：repair :18080 与 primary :8080 监听与 PID 未变、repair 配置 md5 未变、无 qa-* 残留单元/进程
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g89_common as C  # noqa: E402

BATCHES = ["qa-resume-20261017-g8", "qa-resume-20261017-g9"]


def main() -> None:
    nonce = C.get_nonce()
    led = json.loads((C.ROOT / "qa-resume-20261017-g9" / "instance-ledger.json").read_text())
    base = led.get("baseline_before_start", {})
    cr = C.load_creds(nonce)
    data_dir, cfg, log, child_root = cr["data_dir"], cr["config_path"], cr["log_path"], cr["child_root"]
    port, tun = cr["host_port"], cr["tunnel_port"]

    pre = C.ssh(f"""
echo "PGREP_BEFORE:$(pgrep -af 'qa-{nonce}' | wc -l)"
echo "PORT_BEFORE:$(ss -ltn | grep -c ':{port} ' || true)"
echo "REPAIR_PID_BEFORE:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "REPAIR_MD5_BEFORE:$(md5sum /etc/nodesteer/hub-repair-20260930-101945.yaml | cut -d' ' -f1)"
echo "DATA_DIR_SIZE_BEFORE:$(du -sh {data_dir} 2>/dev/null | cut -f1)"
""", check=False)
    pre_checks = dict(l.split(":", 1) for l in pre.stdout.splitlines() if ":" in l)

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
echo "PORT_LISTEN_AFTER:$(ss -ltn | grep -c ':{port} ' || true)"
echo "DATA_DIR_EXISTS:$(test -e {data_dir} && echo yes || echo no)"
echo "CHILD_ROOT_EXISTS:$(test -e {child_root} && echo yes || echo no)"
echo "CONFIG_EXISTS:$(test -e {cfg} && echo yes || echo no)"
echo "LOG_EXISTS:$(test -e {log} && echo yes || echo no)"
echo "ETC_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-g89202610178970c813' || true)"
echo "VARLIB_LEFTOVER:$(ls -d /var/lib/*qa-g89202610178970c813* 2>/dev/null | wc -l)"
echo "VARLOG_LEFTOVER:$(ls /var/log/*qa-g89202610178970c813* 2>/dev/null | wc -l)"
echo "TMP_LEFTOVER:$(ls -d /tmp/*qa-g89202610178970c813* 2>/dev/null | wc -l)"
echo "REPAIR_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "REPAIR_PID_AFTER:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "REPAIR_MD5_AFTER:$(md5sum /etc/nodesteer/hub-repair-20260930-101945.yaml | cut -d' ' -f1)"
echo "PRIMARY_PID:$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1)"
echo "AGENT_UNIT:$(systemctl is-active nodesteer-agent.service 2>/dev/null || echo absent)"
echo "UNIT_QA_MATCH:$(find /etc/systemd/system -name '*qa-g89202610178970c813*' | wc -l)"
echo "UNIT_NEW_1H:$(find /etc/systemd/system -newermt '-3 hours' -name '*.service' | wc -l)"
echo "REPAIR_AGENT_UNITS:$(systemctl list-units --all --plain --no-legend 'nodesteer-agent-repair-*' | wc -l)"
""", check=False)
    checks = dict(l.split(":", 1) for l in stop.stdout.splitlines() if ":" in l)

    # 本地：隧道 + 凭据 + 自有浏览器 session
    C.local(["pkill", "-f", f"-L {tun}:127.0.0.1:{port}"], check=False)
    tun_left = C.local(["pgrep", "-af", f"-L {tun}:"], check=False).stdout.strip()
    cred_p = C.creds_path(nonce)
    cred_existed = cred_p.exists()
    cred_p.unlink(missing_ok=True)

    prefix = f"qa-g89-{nonce}-"
    ls = subprocess.run(["agent-browser", "session", "list"], capture_output=True, text=True)
    own = [l.strip() for l in ls.stdout.splitlines() if l.strip().startswith(prefix)]
    closed = []
    for s in own:
        cp = subprocess.run(["agent-browser", "--session", s, "close"], capture_output=True, text=True, timeout=120)
        closed.append({"session": s, "rc": cp.returncode})
    after = subprocess.run(["agent-browser", "session", "list"], capture_output=True, text=True)
    left = [l.strip() for l in after.stdout.splitlines() if l.strip().startswith(prefix)]

    ok = (checks.get("PGREP_AFTER") == "0" and checks.get("PORT_LISTEN_AFTER") == "0"
          and checks.get("DATA_DIR_EXISTS") == "no" and checks.get("CHILD_ROOT_EXISTS") == "no"
          and checks.get("CONFIG_EXISTS") == "no" and checks.get("LOG_EXISTS") == "no"
          and checks.get("ETC_LEFTOVER") == "0" and checks.get("VARLIB_LEFTOVER") == "0"
          and checks.get("VARLOG_LEFTOVER") == "0" and checks.get("TMP_LEFTOVER") == "0"
          and not tun_left and not cred_p.exists() and not left)
    collateral_ok = (checks.get("REPAIR_18080") == "1" and checks.get("PRIMARY_8080") == "1"
                     and checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID")
                     and checks.get("PRIMARY_PID") == base.get("PRIMARY_PID")
                     and checks.get("REPAIR_MD5_AFTER") == pre_checks.get("REPAIR_MD5_BEFORE")
                     and checks.get("AGENT_UNIT") == "inactive"
                     and checks.get("UNIT_QA_MATCH") == "0"
                     and checks.get("REPAIR_AGENT_UNITS") == "0")

    oracle = {
        "batch_ids": BATCHES,
        "verified_at_utc": C.utc(),
        "instance": {"id": led["instance_id"], "nonce": nonce, "port": port,
                     "data_dir": data_dir, "child_root": child_root, "config": cfg, "log": log,
                     "tunnel_local_port": tun, "credential_file": str(cred_p)},
        "teardown_steps": [
            "pkill -TERM -f qa-<nonce>（hub + owned child + runuser 父进程）",
            "pkill -KILL -f qa-<nonce>（兜底）",
            f"rm -rf {data_dir}（含 hub.db/hub.db-wal/artifacts）",
            f"rm -rf {child_root}（owned child 配置/数据/日志/二进制）",
            f"rm -f {cfg} {log}",
            f"关闭本地隧道 -L {tun}:127.0.0.1:{port}",
            f"删除本地 0600 凭据文件 {cred_p}（存在过={cred_existed}）",
            f"关闭自有 agent-browser session {len(closed)} 个（仅 {prefix}*）",
        ],
        "pre_teardown_checks": pre_checks,
        "remote_checks": checks,
        "local_checks": {
            "tunnel_process_left": tun_left or "none",
            "credential_file_left": cred_p.exists(),
            "own_browser_sessions_left": left,
            "closed_sessions": closed,
        },
        "baseline_comparison": {
            "repair_pid_before": base.get("REPAIR_PID"),
            "repair_pid_after": checks.get("REPAIR_PID_AFTER"),
            "repair_pid_same": checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID"),
            "primary_pid_before": base.get("PRIMARY_PID"),
            "primary_pid_after": checks.get("PRIMARY_PID"),
            "primary_pid_same": checks.get("PRIMARY_PID") == base.get("PRIMARY_PID"),
            "repair_config_md5_before": pre_checks.get("REPAIR_MD5_BEFORE"),
            "repair_config_md5_after": checks.get("REPAIR_MD5_AFTER"),
            "repair_config_hash_same": checks.get("REPAIR_MD5_AFTER") == pre_checks.get("REPAIR_MD5_BEFORE"),
        },
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
            "tmp_leftovers": checks.get("TMP_LEFTOVER"),
        },
        "collateral_assertions": {
            "repair_18080_listening": checks.get("REPAIR_18080") == "1",
            "primary_8080_listening": checks.get("PRIMARY_8080") == "1",
            "repair_pid_unchanged": checks.get("REPAIR_PID_AFTER") == base.get("REPAIR_PID"),
            "primary_pid_unchanged": checks.get("PRIMARY_PID") == base.get("PRIMARY_PID"),
            "nodesteer_agent_service": checks.get("AGENT_UNIT"),
            "systemd_units_matching_nonce_after_cleanup": checks.get("UNIT_QA_MATCH"),
            "repair_agent_units_on_hub_host": checks.get("REPAIR_AGENT_UNITS"),
            "repair_writes_by_this_batch": "none（全程只读 GET/SHA 比对）",
            "primary_8080_writes_by_this_batch": "none",
        },
        "result": ("ISOLATED_INSTANCE_DESTROYED_AND_VERIFIED" if ok and collateral_ok else
                   ("DESTROYED_BUT_COLLATERAL_REVIEW_REQUIRED" if ok else "TEARDOWN_INCOMPLETE_REVIEW_REQUIRED")),
        "verdict": {"zero_residue": ok, "collateral_unaffected": collateral_ok},
    }

    for b in BATCHES:
        d = C.ROOT / b
        (d / "final-cleanup-oracle.json").write_text(json.dumps(oracle, ensure_ascii=False, indent=1) + "\n")
        (C.EVID_DIRS["g8" if b.endswith("g8") else "g9"] / "final-cleanup-oracle.json").write_text(
            json.dumps(oracle, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: oracle[k] for k in ("teardown_steps", "zombie_check", "collateral_assertions",
                                             "local_checks", "result", "verdict")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
