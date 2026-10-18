"""P-TABLE：隔离实例收尾并核验销毁（进程、端口、目录、配置、隧道、浏览器会话、凭据文件）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    nonce = led["nonce"]
    inst = led["instance_id"]
    creds = C.load_creds(nonce)
    tun = creds["tunnel_port"]
    data_dir = creds["data_dir"]
    cfg = creds["config_path"]
    log = creds["log_path"]

    # 1) 停进程（含子进程），仅匹配本批次 nonce
    stop = C.ssh(f"""
pkill -TERM -f 'qa-ptable-{nonce}' 2>/dev/null || true
sleep 2
pkill -KILL -f 'qa-ptable-{nonce}' 2>/dev/null || true
sleep 1
echo "PGREP_AFTER:$(pgrep -af 'qa-ptable-{nonce}' | wc -l)"
echo "PORT_LISTEN:$(ss -ltn | grep -c ':{led["port"]} ' || true)"
""", check=False)
    out = stop.stdout

    # 2) 删除目录与配置
    rm = C.ssh(f"""
rm -rf {data_dir}
rm -f {cfg}
rm -f {log}
echo "DATA_DIR_EXISTS:$(test -e {data_dir} && echo yes || echo no)"
echo "CONFIG_EXISTS:$(test -e {cfg} && echo yes || echo no)"
echo "LOG_EXISTS:$(test -e {log} && echo yes || echo no)"
echo "ETC_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-ptable-{nonce}' || true)"
echo "VARLIB_LEFTOVER:$(ls -d /var/lib/*qa-ptable* 2>/dev/null | grep -c 'qa-ptable-{nonce}' || true)"
echo "PORT_LISTEN_AFTER:$(ss -ltn | grep -c ':{led["port"]} ' || true)"
echo "REPAIR_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "REPAIR_PID_ALIVE:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "AGENT_UNIT:$(systemctl is-active nodesteer-agent.service 2>/dev/null || echo absent)"
echo "UNIT_FILES_CHANGED:$(find /etc/systemd/system -newermt '-40 minutes' -name '*.service' | wc -l)"
""", check=False)
    out += rm.stdout

    # 3) 本地隧道与浏览器会话
    tun_kill = C.local(["pkill", "-f", f"-L {tun}:127.0.0.1"], check=False)
    tun_left = C.local(["pgrep", "-af", f"-L {tun}:127.0.0.1"], check=False).stdout.strip()
    ab_close = C.ab("close", check=False)
    ab_profile = C.ab("auth", "delete", f"ptable-{nonce}", check=False)

    teardown = {
        "steps": [
            f"pkill -TERM/-KILL -f qa-ptable-{nonce}（含 runuser 父进程与 hub 子进程）",
            f"rm -rf {data_dir}",
            f"rm -f {cfg} {log}",
            f"本地隧道进程 pkill -f '-L {tun}:127.0.0.1'",
            f"agent-browser --session {C.SESSION} close（未使用 --all）",
            f"agent-browser auth delete ptable-{nonce}",
            f"删除本地 0600 凭据文件 {C.creds_path(nonce)}",
        ],
        "remote_checks": {k: v for k, v in
                          (line.split(":", 1) for line in out.splitlines() if ":" in line)},
        "local": {
            "tunnel_process_after": tun_left or "none",
            "tunnel_pkill_rc": tun_kill.returncode,
            "browser_close_rc": ab_close.returncode,
            "auth_delete_rc": ab_profile.returncode,
        },
    }
    # 4) 本地凭据文件删除
    cp = C.creds_path(nonce)
    cp.unlink(missing_ok=True)
    teardown["local"]["credential_file_exists_after"] = cp.exists()

    checks = teardown["remote_checks"]
    ok = (checks.get("PGREP_AFTER") == "0" and checks.get("PORT_LISTEN") == "0"
          and checks.get("DATA_DIR_EXISTS") == "no" and checks.get("CONFIG_EXISTS") == "no"
          and checks.get("LOG_EXISTS") == "no" and checks.get("ETC_LEFTOVER") == "0"
          and checks.get("VARLIB_LEFTOVER") == "0" and checks.get("PORT_LISTEN_AFTER") == "0"
          and not tun_left and not cp.exists())
    teardown["verification"] = {
        "processes_after": checks.get("PGREP_AFTER"),
        "port_listening_after": checks.get("PORT_LISTEN_AFTER"),
        "data_dir_exists_after": checks.get("DATA_DIR_EXISTS"),
        "config_exists_after": checks.get("CONFIG_EXISTS"),
        "log_exists_after": checks.get("LOG_EXISTS"),
        "etc_leftovers": checks.get("ETC_LEFTOVER"),
        "var_lib_leftovers": checks.get("VARLIB_LEFTOVER"),
        "local_tunnel_left": tun_left or "none",
        "credential_file_left": cp.exists(),
    }
    teardown["collateral_check"] = {
        "repair_18080_listening": checks.get("REPAIR_18080"),
        "primary_8080_listening": checks.get("PRIMARY_8080"),
        "repair_pid_alive_pid": checks.get("REPAIR_PID_ALIVE"),
        "nodesteer_agent_service": checks.get("AGENT_UNIT"),
        "systemd_unit_files_modified_recently": checks.get("UNIT_FILES_CHANGED"),
    }
    teardown["result"] = ("ISOLATED_INSTANCE_DESTROYED_AND_VERIFIED" if ok
                          else "TEARDOWN_INCOMPLETE_REVIEW_REQUIRED")
    led["teardown"] = teardown
    (C.ROOT / "instance-ledger.json").write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n")

    res_path = C.ROOT / "results.json"
    if res_path.exists():
        res = json.loads(res_path.read_text())
        res["teardown"] = {"status": teardown["result"], "verification": teardown["verification"],
                           "collateral_check": teardown["collateral_check"]}
        res_path.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(teardown, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
