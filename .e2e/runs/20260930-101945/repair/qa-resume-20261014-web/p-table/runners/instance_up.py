"""P-TABLE：创建可整体销毁的隔离 Hub 实例（18082 优先），并建立本地隧道。

仅本批次自身进程/目录/端口；不触碰 repair Hub、:8080、其它批次。
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402


def pick_port() -> int:
    for port in [18082, 18083, 18084, 18085, 18086]:
        cp = C.ssh(f"ss -ltn | grep -c ':{port} ' || true")
        if cp.stdout.strip() in ("0", ""):
            return port
    raise RuntimeError("no free instance port")


def main() -> None:
    nonce = "20261015ptable" + secrets.token_hex(4)
    instance = f"nodesteer-qa-ptable-{nonce}"
    inst_port = pick_port()
    tun = C.TUNNEL_PORT if inst_port == C.INSTANCE_PORT else inst_port - 14000 + 38000
    admin_user = f"qa-ptable-{nonce}-admin"
    admin_pw = C.gen_password()
    reg_token = secrets.token_urlsafe(32)
    data_dir = f"/var/lib/nodesteer-qa-ptable-{nonce}"
    cfg_path = f"/etc/nodesteer/qa-ptable-{nonce}.yaml"
    log_path = f"/var/log/nodesteer-qa-ptable-{nonce}.log"
    cfg = (
        f'web_addr: ":{inst_port}"\n'
        f'gateway_addr: ":{inst_port}"\n'
        f'base_url: "http://{C.HOST}:{inst_port}"\n'
        f'registration_token: "{reg_token}"\n'
        f'data_dir: "{data_dir}"\n'
        f'artifact_dir: "{data_dir}/artifacts"\n'
        f'admin_username: "{admin_user}"\n'
        f'admin_password: "{admin_pw}"\n'
        f'session_ttl: "24h"\n'
    )
    ledger = {
        "instance_id": instance,
        "nonce": nonce,
        "purpose": "可整体销毁的隔离 Hub：在真空态验证表格 Loading/Empty/Error/Retry 与真实分页/排序/边界",
        "host": C.HOST,
        "port": inst_port,
        "tunnel_local_port": tun,
        "binary": {"path": C.REMOTE_BIN, "sha256_expected": C.REMOTE_BIN_SHA},
        "config": {"path": cfg_path, "mode": "0640 root:nodesteer"},
        "data_dir": {"path": data_dir, "mode": "0750 nodesteer:nodesteer"},
        "log": log_path,
        "launch": {"method": "transient: runuser -u nodesteer -- setsid nohup <binary> --config <cfg>",
                   "systemd": "none"},
        "created_at_utc": C.utc(),
    }
    script = f"""
set -eu
umask 077
install -d -o nodesteer -g nodesteer -m 0750 {data_dir}
printf '%s' '{C.b64(cfg)}' | base64 -d > {cfg_path}
chown root:nodesteer {cfg_path}
chmod 0640 {cfg_path}
sha256sum {C.REMOTE_BIN} | awk '{{print $1}}'
runuser -u nodesteer -- setsid nohup {C.REMOTE_BIN} --config {cfg_path} >> {log_path} 2>&1 < /dev/null &
sleep 3
pgrep -af 'qa-ptable-{nonce}' | sed 's/^/PID: /' || true
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:{inst_port}/healthz; then echo "HEALTHZ_OK"; break; fi
  sleep 1
done
curl -s -o /dev/null -w 'ROOT_STATUS:%{{http_code}}\\n' http://127.0.0.1:{inst_port}/
ss -ltn | grep ':{inst_port} ' | sed 's/^/LISTEN: /' || true
curl -s http://127.0.0.1:{inst_port}/ | sha256sum | awk '{{print "INDEX_SHA:"$1}}'
echo "CONFIG_MODE:$(stat -c '%a %U:%G' {cfg_path})"
echo "DATA_DIR_MODE:$(stat -c '%a %U:%G' {data_dir})"
"""
    cp = C.ssh(script, timeout=180)
    out = cp.stdout
    ledger["sha256_verified"] = None
    for line in out.splitlines():
        if line.startswith("PID: "):
            ledger.setdefault("pids_at_start", []).append(line[5:].strip())
        elif line.startswith("INDEX_SHA:"):
            ledger["index_html_sha256"] = line.split(":", 1)[1].strip()
        elif line.startswith("LISTEN: "):
            ledger.setdefault("listeners", []).append(line.split(" ", 1)[1].strip())
        elif line.startswith("CONFIG_MODE:"):
            ledger["config_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("DATA_DIR_MODE:"):
            ledger["data_dir_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("HEALTHZ_OK"):
            ledger["healthz_ok"] = True
        elif line.startswith("ROOT_STATUS:"):
            ledger["root_status"] = line.split(":", 1)[1].strip()
        elif len(line.strip()) == 64:
            ledger["sha256_verified"] = line.strip()
    rep = C.ssh(f"sha256sum {C.REMOTE_BIN} | awk '{{print $1}}'; "
                f"curl -s http://127.0.0.1:18080/ | sha256sum | awk '{{print $1}}'")
    rlines = [x.strip() for x in rep.stdout.splitlines() if x.strip()]
    ledger["repair_binary_sha256"] = rlines[0] if rlines else None
    ledger["repair_index_html_sha256"] = rlines[1] if len(rlines) > 1 else None
    ledger["binary_match"] = ledger.get("sha256_verified") == C.REMOTE_BIN_SHA
    ledger["index_html_identical_to_repair"] = (
        ledger.get("index_html_sha256") == ledger.get("repair_index_html_sha256"))

    # 本地隧道（ssh -f 后台驻留；收尾按 -L <port>:127.0.0.1 模式精确清理）
    C.local([
        "ssh", "-i", C.SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
        "-o", "ExitOnForwardFailure=yes", "-f", "-N",
        "-L", f"{tun}:127.0.0.1:{inst_port}", f"root@{C.HOST}",
    ], timeout=25, check=False)
    time.sleep(1.5)
    http = C.local(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                    f"http://127.0.0.1:{tun}/healthz"], check=False)
    pg = C.local(["pgrep", "-af", f"-L {tun}:127.0.0.1"], check=False)
    ledger["tunnel"] = {"local_port": tun, "healthz_status": http.stdout.strip(),
                        "tunnel_pgrep": pg.stdout.strip()}

    creds = {
        "nonce": nonce, "instance_id": instance, "instance_port": inst_port,
        "tunnel_port": tun, "admin_username": admin_user, "admin_password": admin_pw,
        "registration_token": reg_token, "config_path": cfg_path,
        "data_dir": data_dir, "log_path": log_path,
    }
    creds_file = C.save_creds(nonce, creds)
    ledger["local_credential_file"] = f"{creds_file} (0600)"
    (C.ROOT / "instance-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    safe = {k: v for k, v in ledger.items() if k not in ("registration_token",)}
    print(json.dumps(safe, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
