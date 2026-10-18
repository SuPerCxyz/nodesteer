"""p-api：隔离实例收尾并核验销毁（进程、端口、目录、配置、日志、隧道、凭据文件）。

- 仅匹配本批次自身 nonce（qa-papi-*）；
- 附带旁证：repair 18080 / :8080 / nodesteer-agent.service / repair 进程未被触碰。
- 记录写入 instance-ledger.json 的 teardown_history（保留每次收尾结果）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papi_common as C  # noqa: E402


def teardown_one(nonce: str, port: int, tun: int, creds: dict) -> dict:
    data_dir = creds["data_dir"]
    cfg = creds["config_path"]
    log = creds["log_path"]
    stop = C.ssh(f"""
pkill -TERM -f 'qa-papi-{nonce}' 2>/dev/null || true
sleep 2
pkill -KILL -f 'qa-papi-{nonce}' 2>/dev/null || true
sleep 1
rm -rf {data_dir}
rm -f {cfg}
rm -f {log}
echo "PGREP_AFTER:$(pgrep -af 'qa-papi-{nonce}' | wc -l)"
echo "PORT_LISTEN_AFTER:$(ss -ltn | grep -c ':{port} ' || true)"
echo "DATA_DIR_EXISTS:$(test -e {data_dir} && echo yes || echo no)"
echo "CONFIG_EXISTS:$(test -e {cfg} && echo yes || echo no)"
echo "LOG_EXISTS:$(test -e {log} && echo yes || echo no)"
echo "ETC_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-papi-{nonce}' || true)"
echo "VARLIB_LEFTOVER:$(ls -d /var/lib/*qa-papi* 2>/dev/null | grep -c 'qa-papi-{nonce}' || true)"
echo "REPAIR_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "REPAIR_PID_ALIVE:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "AGENT_UNIT:$(systemctl is-active nodesteer-agent.service 2>/dev/null || echo absent)"
echo "UNIT_FILES_CHANGED:$(find /etc/systemd/system -newermt '-180 minutes' -name '*.service' | wc -l)"
echo "AGENT_PROCS_LEFT:$(pgrep -af 'qa-papi-{nonce}' | grep -c nodesteer-agent || true)"
""", check=False)
    out = stop.stdout
    C.local(["pkill", "-f", f"-L {tun}:127.0.0.1"], check=False)
    tun_left = C.local(["pgrep", "-af", f"-L {tun}:127.0.0.1"], check=False).stdout.strip()
    cp = C.creds_path(nonce)
    cp.unlink(missing_ok=True)
    checks = {k: v for k, v in (ln.split(":", 1) for ln in out.splitlines() if ":" in ln)}
    ok = (checks.get("PGREP_AFTER") == "0" and checks.get("PORT_LISTEN_AFTER") == "0"
          and checks.get("DATA_DIR_EXISTS") == "no" and checks.get("CONFIG_EXISTS") == "no"
          and checks.get("LOG_EXISTS") == "no" and checks.get("ETC_LEFTOVER") == "0"
          and checks.get("VARLIB_LEFTOVER") == "0" and not tun_left and not cp.exists())
    return {
        "nonce": nonce, "port": port, "tunnel_local_port": tun,
        "steps": [f"pkill -TERM/-KILL -f qa-papi-{nonce}",
                  f"rm -rf {data_dir}", f"rm -f {cfg} {log}",
                  f"pkill -f '-L {tun}:127.0.0.1'", f"删除本地 0600 凭据 {cp}"],
        "remote_checks": checks,
        "local": {"tunnel_left": tun_left or "none", "credential_file_left": cp.exists()},
        "verification": {
            "processes_after": checks.get("PGREP_AFTER"),
            "port_listening_after": checks.get("PORT_LISTEN_AFTER"),
            "data_dir_exists_after": checks.get("DATA_DIR_EXISTS"),
            "config_exists_after": checks.get("CONFIG_EXISTS"),
            "log_exists_after": checks.get("LOG_EXISTS"),
            "etc_leftovers": checks.get("ETC_LEFTOVER"),
            "var_lib_leftovers": checks.get("VARLIB_LEFTOVER"),
        },
        "collateral_check": {
            "repair_18080_listening": checks.get("REPAIR_18080"),
            "primary_8080_listening": checks.get("PRIMARY_8080"),
            "repair_hub_pid_alive": checks.get("REPAIR_PID_ALIVE"),
            "nodesteer_agent_service": checks.get("AGENT_UNIT"),
            "systemd_unit_files_modified": checks.get("UNIT_FILES_CHANGED"),
        },
        "result": "DESTROYED_AND_VERIFIED" if ok else "TEARDOWN_INCOMPLETE_REVIEW_REQUIRED",
    }


def teardown_agents_and_fixtures(main_nonce: str) -> dict:
    """停止本批次自建 Agent 并删除 relay/fixture 临时文件（仅匹配本批次 nonce）。"""
    fx_path = C.ROOT / "fixture-ledger.json"
    fixtures = json.loads(fx_path.read_text()) if fx_path.exists() else {}
    paths = [f["path"] for f in fixtures.get("temp_files", [])]
    rm_list = " ".join(paths) if paths else ""
    kill = C.ssh(f"""
pkill -TERM -f 'qa-papi-{main_nonce}-agent' 2>/dev/null || true
sleep 2
pkill -KILL -f 'qa-papi-{main_nonce}-agent' 2>/dev/null || true
sleep 1
echo "AGENT_PGREP_AFTER:$(pgrep -af 'qa-papi-{main_nonce}-agent' | wc -l)"
{('rm -rf ' + rm_list) if rm_list else 'true'}
for p in {rm_list if rm_list else ''}; do
  echo "FIXTURE_EXISTS:$p:$(test -e "$p" && echo yes || echo no)"
done
echo "ETC_AGENT_LEFTOVER:$(ls /etc/nodesteer | grep -c 'qa-papi-{main_nonce}-agent' || true)"
echo "VARLIB_AGENT_LEFTOVER:$(ls -d /var/lib/*qa-papi-{main_nonce}-agent* 2>/dev/null | wc -l)"
echo "VARLOG_AGENT_LEFTOVER:$(ls /var/log/*qa-papi-{main_nonce}-agent* 2>/dev/null | wc -l)"
echo "RELAY_TMP_LEFTOVER:$(ls -d /tmp/qa-papi-{main_nonce}* 2>/dev/null | wc -l)"
""", check=False)
    checks = {k: v for k, v in (ln.split(":", 1) for ln in kill.stdout.splitlines() if ":" in ln)}
    return {"nonce": main_nonce, "steps": [
        f"pkill -TERM/-KILL -f qa-papi-{main_nonce}-agent",
        f"rm -rf {rm_list}"],
        "remote_checks": checks,
        "verification": {
            "agent_processes_after": checks.get("AGENT_PGREP_AFTER"),
            "etc_agent_leftovers": checks.get("ETC_AGENT_LEFTOVER"),
            "var_lib_agent_leftovers": checks.get("VARLIB_AGENT_LEFTOVER"),
            "var_log_agent_leftovers": checks.get("VARLOG_AGENT_LEFTOVER"),
            "relay_tmp_leftovers": checks.get("RELAY_TMP_LEFTOVER"),
        },
        "result": ("AGENTS_AND_FIXTURES_REMOVED"
                   if checks.get("AGENT_PGREP_AFTER") == "0"
                   and checks.get("ETC_AGENT_LEFTOVER") == "0"
                   and checks.get("VARLIB_AGENT_LEFTOVER") == "0"
                   and checks.get("VARLOG_AGENT_LEFTOVER") == "0"
                   and checks.get("RELAY_TMP_LEFTOVER") == "0"
                   else "FIXTURE_TEARDOWN_INCOMPLETE")}


def main() -> None:
    path = C.ROOT / "instance-ledger.json"
    led = json.loads(path.read_text())
    main_nonce = next(i["nonce"] for i in led["instances"] if i["nonce"].startswith("papim"))
    agent_teardown = teardown_agents_and_fixtures(main_nonce)
    results = []
    for inst in led["instances"]:
        creds = C.load_creds(inst["nonce"])
        results.append(teardown_one(inst["nonce"], inst["port"],
                                    creds["tunnel_port"], creds))
    led.setdefault("teardown_history", []).append(
        {"at_utc": C.utc(), "agent_teardown": agent_teardown, "results": results})
    led["teardown"] = {
        "agent_teardown": agent_teardown,
        "instances": results,
        "result": ("ALL_INSTANCES_DESTROYED_AND_FIXTURES_REMOVED"
                   if agent_teardown["result"] == "AGENTS_AND_FIXTURES_REMOVED"
                   and all(r["result"] == "DESTROYED_AND_VERIFIED" for r in results)
                   else "TEARDOWN_INCOMPLETE_REVIEW_REQUIRED"),
    }
    path.write_text(json.dumps(led, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(led["teardown"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
