"""G1：创建 1 个可整体销毁的隔离 Hub 实例（默认 18085），并建立本地隧道。

- transient 进程（setsid nohup），不写 systemd 单元 / autostart；
- 独立 data_dir / 配置（0640 root:nodesteer）/ 日志 / 注册令牌 / admin 凭据；
- 启动前记录 repair :18080 与 primary :8080 的基线（PID + 监听 + 目录清单）；
- 生产凭据只落 /tmp/opencode/g1-creds-<nonce>.json（0600）。
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import g1_common as C  # noqa: E402


def pick_port() -> int:
    for port in [C.INSTANCE_PORT, C.INSTANCE_PORT + 1, C.INSTANCE_PORT + 2, C.INSTANCE_PORT + 3]:
        cp = C.ssh(f"ss -ltn | grep -c ':{port} ' || true")
        if cp.stdout.strip() in ("0", ""):
            return port
    raise RuntimeError("no free instance port")


def baseline() -> dict:
    cp = C.ssh("""
echo "REPAIR_18080_LISTEN:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080_LISTEN:$(ss -ltn | grep -c ':8080 ' || true)"
echo "REPAIR_PID:$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)"
echo "PRIMARY_PID:$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1)"
echo "REPAIR_PID_CMDLINE:$(tr '\\0' ' ' < /proc/$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1)/cmdline 2>/dev/null || true)"
echo "ETC_FILES:$(ls /etc/nodesteer | tr '\\n' ',')"
echo "VARLIB_DIRS:$(ls -d /var/lib/nodesteer* 2>/dev/null | tr '\\n' ',')"
echo "AGENT_PROCS:$(pgrep -af nodesteer-agent | wc -l)"
echo "UNIT_NEW:$(find /etc/systemd/system -newermt '-1 hour' -name '*.service' | wc -l)"
""", check=False)
    d = {}
    for ln in cp.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k] = v.strip()
    return d


def main() -> None:
    nonce = "g1" + time.strftime("%Y%m%d") + secrets.token_hex(4)
    instance = f"nodesteer-qa-{nonce}"
    port = pick_port()
    tun = C.TUNNEL_PORT if port == C.INSTANCE_PORT else port - 14000 + 38000
    admin_user = f"qa-{nonce}-admin"
    admin_pw = C.gen_password()
    reg_token = C.gen_token()
    data_dir = f"/var/lib/nodesteer-qa-{nonce}"
    cfg_path = f"/etc/nodesteer/qa-{nonce}.yaml"
    log_path = f"/var/log/nodesteer-qa-{nonce}.log"
    cfg = (
        f'web_addr: ":{port}"\n'
        f'gateway_addr: ":{port}"\n'
        f'base_url: "http://{C.HOST}:{port}"\n'
        f'registration_token: "{reg_token}"\n'
        f'data_dir: "{data_dir}"\n'
        f'artifact_dir: "{data_dir}/artifacts"\n'
        f'admin_username: "{admin_user}"\n'
        f'admin_password: "{admin_pw}"\n'
        f'session_ttl: "24h"\n'
    )
    base = baseline()
    ledger = {
        "batch_id": "qa-resume-20261016-g1",
        "instance_id": instance,
        "nonce": nonce,
        "purpose": ("G1 授权：临时可销毁隔离 Hub，在其内创建 admin/operator/viewer 账号与 owned child "
                    "Agent，用于 P04-06/P14P15-12/P21-08/P20-04/X13/P03-09/P19-03/X14；"
                    "产品无删除用户 API，账号动作必须隔离；用完整体销毁。"),
        "authorization_basis": ("authorization-checklist-20261016.md G1（含 G9 的 X14 并入）"
                               "“批准：同一模板，零持久化残留”"),
        "host": C.HOST,
        "port": port,
        "tunnel_local_port": tun,
        "binary": {"path": C.REMOTE_BIN, "sha256_expected": C.REMOTE_BIN_SHA,
                   "note": "2026-10-16 修复版（今日新构建），要求与 repair :18080 同版本"},
        "config": {"path": cfg_path, "mode_requested": "0640 root:nodesteer",
                   "fields": ["web_addr", "gateway_addr", "base_url", "registration_token",
                              "data_dir", "artifact_dir", "admin_username", "admin_password",
                              "session_ttl=24h"]},
        "data_dir": {"path": data_dir, "mode_requested": "0750 nodesteer:nodesteer"},
        "log": log_path,
        "launch": {"method": "transient: runuser -u nodesteer -- setsid nohup <binary> --config <cfg>",
                   "systemd": "none"},
        "baseline_before_start": base,
        "created_at_utc": C.utc(),
    }
    script = f"""
set -eu
umask 077
install -d -o nodesteer -g nodesteer -m 0750 {data_dir}
printf '%s' '{C.b64(cfg)}' | base64 -d > {cfg_path}
chown root:nodesteer {cfg_path}
chmod 0640 {cfg_path}
runuser -u nodesteer -- setsid nohup {C.REMOTE_BIN} --config {cfg_path} >> {log_path} 2>&1 < /dev/null &
sleep 3
pgrep -af 'qa-{nonce}' | sed 's/^/PID: /' || true
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:{port}/healthz; then echo "HEALTHZ_OK"; break; fi
  sleep 1
done
curl -s -o /dev/null -w 'ROOT_STATUS:%{{http_code}}\\n' http://127.0.0.1:{port}/
ss -ltn | grep ':{port} ' | sed 's/^/LISTEN: /' || true
curl -s http://127.0.0.1:{port}/ | sha256sum | awk '{{print "INDEX_SHA:"$1}}'
curl -s http://127.0.0.1:18080/ | sha256sum | awk '{{print "REPAIR_INDEX_SHA:"$1}}'
sha256sum {C.REMOTE_BIN} | awk '{{print "BIN_SHA:"$1}}'
sha256sum {C.REMOTE_AGENT} | awk '{{print "AGENT_SHA:"$1}}'
echo "CONFIG_MODE:$(stat -c '%a %U:%G' {cfg_path})"
echo "DATA_DIR_MODE:$(stat -c '%a %U:%G' {data_dir})"
echo "REPAIR_STILL:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_STILL:$(ss -ltn | grep -c ':8080 ' || true)"
"""
    cp = C.ssh(script, timeout=180)
    C.write_terminal("instance-up.log", cp.stdout + "\n--- stderr ---\n" + cp.stderr)
    for line in cp.stdout.splitlines():
        if line.startswith("PID: "):
            ledger.setdefault("pids_at_start", []).append(line[5:].strip())
        elif line.startswith("INDEX_SHA:"):
            ledger["index_html_sha256"] = line.split(":", 1)[1].strip()
        elif line.startswith("REPAIR_INDEX_SHA:"):
            ledger["repair_index_html_sha256"] = line.split(":", 1)[1].strip()
        elif line.startswith("LISTEN: "):
            ledger.setdefault("listeners", []).append(line.split(" ", 1)[1].strip())
        elif line.startswith("BIN_SHA:"):
            ledger["sha256_verified"] = line.split(":", 1)[1].strip()
        elif line.startswith("AGENT_SHA:"):
            ledger["owned_child_agent_sha256"] = line.split(":", 1)[1].strip()
        elif line.startswith("CONFIG_MODE:"):
            ledger["config_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("DATA_DIR_MODE:"):
            ledger["data_dir_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("HEALTHZ_OK"):
            ledger["healthz_ok"] = True
        elif line.startswith("ROOT_STATUS:"):
            ledger["root_status"] = line.split(":", 1)[1].strip()
    ledger["binary_match_expected_sha"] = ledger.get("sha256_verified") == C.REMOTE_BIN_SHA
    ledger["index_html_identical_to_repair"] = (
        ledger.get("index_html_sha256") == ledger.get("repair_index_html_sha256")
        and bool(ledger.get("index_html_sha256")))

    # 本地隧道（仅自有端口）
    C.local(["ssh", "-i", C.SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ExitOnForwardFailure=yes", "-f", "-N",
             "-L", f"{tun}:127.0.0.1:{port}", f"root@{C.HOST}"], timeout=25, check=False)
    time.sleep(1.2)
    hc = C.local(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                  f"http://127.0.0.1:{tun}/healthz"], check=False)
    ledger["tunnel"] = {"local_port": tun, "healthz_status": hc.stdout.strip()}

    hub = C.Hub(f"http://127.0.0.1:{tun}")
    lg = hub.login(admin_user, admin_pw)
    ledger["admin_login"] = {"http": lg["status"], "role": (lg["json"] or {}).get("role")
                             if isinstance(lg["json"], dict) else None}
    if lg["status"] != 200:
        raise RuntimeError("admin login failed on fresh instance")

    (C.ROOT / "instance-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=1) + "\n")
    C.set_nonce(nonce)
    C.save_creds(nonce, {
        "nonce": nonce, "instance_id": instance, "host_port": port, "tunnel_port": tun,
        "base_url": f"http://{C.HOST}:{port}", "local_base": f"http://127.0.0.1:{tun}",
        "admin_username": admin_user, "admin_password": admin_pw,
        "registration_token": reg_token, "config_path": cfg_path,
        "data_dir": data_dir, "log_path": log_path, "session_ttl": "24h",
    })
    safe = json.loads(json.dumps(ledger))
    safe.pop("baseline_before_start", None)
    print(json.dumps(safe, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
