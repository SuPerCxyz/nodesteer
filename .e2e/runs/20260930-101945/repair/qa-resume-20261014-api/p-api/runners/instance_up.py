"""p-api：创建可整体销毁的隔离 Hub 实例（main:18083 24h TTL；ttl:18084 3s TTL）。

仅本批次自身进程/目录/端口；不触碰 repair Hub、:8080、nodesteer-agent.service、其它批次。
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import papi_common as C  # noqa: E402


def pick_port(preferred: int) -> int:
    for port in [preferred, preferred + 1, preferred + 2, preferred + 3]:
        cp = C.ssh(f"ss -ltn | grep -c ':{port} ' || true")
        if cp.stdout.strip() in ("0", ""):
            return port
    raise RuntimeError(f"no free port near {preferred}")


def create(tag: str, port: int, ttl: str, tun: int) -> dict:
    nonce = f"{tag}{secrets.token_hex(4)}"
    instance = f"nodesteer-qa-papi-{nonce}"
    admin_user = f"qa-papi-{nonce}-admin"
    admin_pw = C.gen_password()
    reg_token = C.gen_token()
    data_dir = f"/var/lib/nodesteer-qa-papi-{nonce}"
    cfg_path = f"/etc/nodesteer/qa-papi-{nonce}.yaml"
    log_path = f"/var/log/nodesteer-qa-papi-{nonce}.log"
    cfg = (
        f'web_addr: ":{port}"\n'
        f'gateway_addr: ":{port}"\n'
        f'base_url: "http://{C.HOST}:{port}"\n'
        f'registration_token: "{reg_token}"\n'
        f'data_dir: "{data_dir}"\n'
        f'artifact_dir: "{data_dir}/artifacts"\n'
        f'admin_username: "{admin_user}"\n'
        f'admin_password: "{admin_pw}"\n'
        f'session_ttl: "{ttl}"\n'
    )
    script = f"""
set -eu
umask 077
install -d -o nodesteer -g nodesteer -m 0750 {data_dir}
printf '%s' '{C.b64(cfg)}' | base64 -d > {cfg_path}
chown root:nodesteer {cfg_path}
chmod 0640 {cfg_path}
runuser -u nodesteer -- setsid nohup {C.REMOTE_BIN} --config {cfg_path} >> {log_path} 2>&1 < /dev/null &
sleep 3
pgrep -af 'qa-papi-{nonce}' | sed 's/^/PID: /' || true
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1:{port}/healthz; then echo "HEALTHZ_OK"; break; fi
  sleep 1
done
curl -s -o /dev/null -w 'ROOT_STATUS:%{{http_code}}\\n' http://127.0.0.1:{port}/
ss -ltn | grep ':{port} ' | sed 's/^/LISTEN: /' || true
curl -s http://127.0.0.1:{port}/ | sha256sum | awk '{{print "INDEX_SHA:"$1}}'
echo "CONFIG_MODE:$(stat -c '%a %U:%G' {cfg_path})"
echo "DATA_DIR_MODE:$(stat -c '%a %U:%G' {data_dir})"
sha256sum {C.REMOTE_BIN} | awk '{{print "BIN_SHA:"$1}}'
"""
    cp = C.ssh(script, timeout=180)
    led = {
        "instance_id": instance, "nonce": nonce, "purpose_ttl": ttl,
        "purpose": ("主隔离实例：认证/越权/契约/Relay 真实复测；产品无删除用户 API，"
                    "角色与用户类维度必须在此可整体销毁实例上进行"),
        "host": C.HOST, "port": port, "tunnel_local_port": tun,
        "binary": {"path": C.REMOTE_BIN, "sha256_expected": C.REMOTE_BIN_SHA},
        "config": {"path": cfg_path, "mode_requested": "0640 root:nodesteer"},
        "data_dir": {"path": data_dir, "mode_requested": "0750 nodesteer:nodesteer"},
        "log": log_path,
        "launch": {"method": "transient: runuser -u nodesteer -- setsid nohup <binary> --config <cfg>",
                   "systemd": "none"},
        "created_at_utc": C.utc(),
    }
    for line in cp.stdout.splitlines():
        if line.startswith("PID: "):
            led.setdefault("pids_at_start", []).append(line[5:].strip())
        elif line.startswith("INDEX_SHA:"):
            led["index_html_sha256"] = line.split(":", 1)[1].strip()
        elif line.startswith("LISTEN: "):
            led.setdefault("listeners", []).append(line.split(" ", 1)[1].strip())
        elif line.startswith("CONFIG_MODE:"):
            led["config_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("DATA_DIR_MODE:"):
            led["data_dir_mode_actual"] = line.split(":", 1)[1].strip()
        elif line.startswith("BIN_SHA:"):
            led["sha256_verified"] = line.split(":", 1)[1].strip()
        elif line.startswith("HEALTHZ_OK"):
            led["healthz_ok"] = True
        elif line.startswith("ROOT_STATUS:"):
            led["root_status"] = line.split(":", 1)[1].strip()
    rep = C.ssh(f"sha256sum {C.REMOTE_BIN} | awk '{{print $1}}'; "
                f"curl -s http://127.0.0.1:18080/ | sha256sum | awk '{{print $1}}'; "
                f"pgrep -f 'hub-repair-20260930-101945.yaml' | head -1")
    rl = [x.strip() for x in rep.stdout.splitlines() if x.strip()]
    led["repair_binary_sha256"] = rl[0] if rl else None
    led["repair_index_html_sha256"] = rl[1] if len(rl) > 1 else None
    led["repair_hub_pid"] = rl[2] if len(rl) > 2 else None
    led["binary_match"] = led.get("sha256_verified") == C.REMOTE_BIN_SHA
    led["index_html_identical_to_repair"] = (
        led.get("index_html_sha256") == led.get("repair_index_html_sha256"))

    C.local(["ssh", "-i", C.SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
             "-o", "ExitOnForwardFailure=yes", "-f", "-N",
             "-L", f"{tun}:127.0.0.1:{port}", f"root@{C.HOST}"], timeout=25, check=False)
    time.sleep(1.2)
    hc = C.local(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                  f"http://127.0.0.1:{tun}/healthz"], check=False)
    led["tunnel"] = {"local_port": tun, "healthz_status": hc.stdout.strip()}

    C.save_creds(nonce, {
        "nonce": nonce, "instance_id": instance, "host_port": port, "tunnel_port": tun,
        "admin_username": admin_user, "admin_password": admin_pw,
        "registration_token": reg_token, "config_path": cfg_path,
        "data_dir": data_dir, "log_path": log_path, "session_ttl": ttl,
    })
    return led


def main() -> None:
    t0 = time.time()
    main_port = pick_port(C.MAIN_PORT)
    ttl_port = pick_port(C.TTL_PORT if C.TTL_PORT != main_port else C.TTL_PORT + 1)
    main_tun = C.MAIN_TUN if main_port == C.MAIN_PORT else main_port - 14000 + 38000
    ttl_tun = C.TTL_TUN if ttl_port == C.TTL_PORT else ttl_port - 14000 + 38000
    ledgers = {
        "batch_id": "qa-resume-20261014-api-p-api",
        "owner": "api",
        "authorization_basis": "Root 指定：角色/用户维度必须在可整体销毁的隔离 Hub 实例上做"
                               "（产品无删除用户 API），同一 repair 二进制，端口 18083（顺延），"
                               "独立 data_dir/配置，临时进程，收尾必须销毁核验。",
        "created_at_utc": C.utc(),
        "instances": [
            create("papim", main_port, "24h", main_tun),
            create("papitt", ttl_port, "3s", ttl_tun),
        ],
    }
    ledgers["elapsed_sec"] = round(time.time() - t0, 1)
    (C.ROOT / "instance-ledger.json").write_text(
        json.dumps(ledgers, ensure_ascii=False, indent=2) + "\n")
    safe = json.loads(json.dumps(ledgers))
    print(json.dumps(safe, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
