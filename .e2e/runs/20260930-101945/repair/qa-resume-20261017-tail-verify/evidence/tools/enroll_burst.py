#!/usr/bin/env python3
"""OBS-20261016-G1 / OBS-20261017-ENROLL-INTERMITTENT-500-AGAIN 判定探针。

在隔离实例（:18088，repair 二进制 1d8cc17e…）上快速连发 POST /api/nodes/enrollment：
  A 组：12 次同参数（同 name/ip）
  B 组：12 次不同参数（各自 name）
  C 组：12 次并发（混合）
任何非 200 都记录完整响应体；同时抓取 Hub 日志中的错误原文。
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tail_common as C  # noqa: E402

EV = HERE.parent
NONCE = "tail" + time.strftime("%Y%m%d") + secrets.token_hex(4)
INSTANCE = f"nodesteer-qa-{NONCE}"
PORT = C.INSTANCE_PORT
DATA_DIR = f"/var/lib/nodesteer-qa-{NONCE}"
CFG = f"/etc/nodesteer/qa-{NONCE}.yaml"
LOG = f"/var/log/nodesteer-qa-{NONCE}.log"
ADMIN = f"qa-{NONCE}-admin"
PW = C.gen_password()
REG = C.gen_token()

LEDGER = EV / "instance-ledger.json"
OUT = EV / "enroll-burst.json"
CREDS = C.CRED_DIR / f"tail-creds-{NONCE}.json"


def sh(cmd, check=True, timeout=120):
    return C.ssh(cmd, check=check, timeout=timeout)


def baseline() -> dict:
    cp = sh("""
echo "REPAIR_18080:$(ss -ltn | grep -c ':18080 ' || true)"
echo "PRIMARY_8080:$(ss -ltn | grep -c ':8080 ' || true)"
echo "INSTANCE_18088:$(ss -ltn | grep -c ':18088 ' || true)"
RP=$(pgrep -f 'hub-repair-20260930-101945.yaml' | head -1); echo "REPAIR_PID:$RP"
echo "REPAIR_EXE_SHA:$(sha256sum /proc/$RP/exe 2>/dev/null | awk '{print $1}')"
PP=$(pgrep -f 'nodesteer-hub --config /etc/nodesteer/hub.yaml' | head -1); echo "PRIMARY_PID:$PP"
echo "AGENT_UNITS:$(systemctl list-units --type=service --all 2>/dev/null | grep -c 'nodesteer-agent')"
echo "QA_UNITS:$(find /etc/systemd/system -name 'nodesteer-qa-*.service' | wc -l)"
echo "EXISTING_QA:$(ls -d /var/lib/nodesteer-qa-* /etc/nodesteer/qa-*.yaml 2>/dev/null | wc -l)"
""", check=False)
    d = {}
    for ln in cp.stdout.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k] = v.strip()
    return d


def start_instance() -> None:
    cfg = (
        f'web_addr: ":{PORT}"\n'
        f'gateway_addr: ":{PORT}"\n'
        f'base_url: "http://{C.HOST}:{PORT}"\n'
        f'registration_token: "{REG}"\n'
        f'data_dir: "{DATA_DIR}"\n'
        f'artifact_dir: "{DATA_DIR}/artifacts"\n'
        f'admin_username: "{ADMIN}"\n'
        f'admin_password: "{PW}"\n'
        f'session_ttl: "24h"\n'
    )
    cp = sh(f"""
set -eu
install -d -m 0750 {DATA_DIR}
umask 077
cat > {CFG} <<'YAMLEOF'
{cfg}YAMLEOF
chmod 0640 {CFG}
setsid nohup {C.REMOTE_BIN} --config {CFG} > {LOG} 2>&1 < /dev/null &
echo "PID:$!"
""")
    return cp.stdout


def wait_up(timeout=45) -> bool:
    base = f"http://{C.HOST}:{PORT}"
    for _ in range(timeout * 2):
        try:
            req = urllib.request.Request(base + "/api/login", data=b"{}",
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=4)
            return True
        except urllib.error.HTTPError:
            return True
        except Exception:
            time.sleep(0.5)
    return False


def login(base: str) -> str:
    body = json.dumps({"username": ADMIN, "password": PW}).encode()
    req = urllib.request.Request(base + "/api/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]


def enroll(base, token, name, ip=""):
    payload = {"node_name": name, "deployment_mode": "native"}
    if ip:
        payload["node_ip"] = ip
    body = json.dumps(payload).encode()
    req = urllib.request.Request(base + "/api/nodes/enrollment", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + token}, method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return {"http": r.status, "body": r.read().decode("utf-8", "replace")[:400],
                    "ms": round((time.time() - t0) * 1000, 1)}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:600],
                "ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"http": 0, "body": repr(e)[:300], "ms": round((time.time() - t0) * 1000, 1)}


def main() -> None:
    base_before = baseline()
    start_instance()
    up = wait_up()
    if not up:
        print(json.dumps({"error": "instance_not_up", "log": sh(f"tail -40 {LOG}", check=False).stdout}))
        sys.exit(1)
    token = login(f"http://{C.HOST}:{PORT}")
    CREDS.write_text(json.dumps({"admin": ADMIN, "password": PW, "registration_token": REG,
                                 "instance": INSTANCE, "port": PORT}, indent=1) + "\n")
    os.chmod(CREDS, 0o600)

    res = {"nonce": NONCE, "instance": INSTANCE, "port": PORT,
           "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "baseline_before": base_before,
           "binary_sha256": sh(f"sha256sum {C.REMOTE_BIN} | awk '{{print $1}}'").stdout.strip(),
           "groups": {}}

    # A: 12x same params, rapid sequential
    a = [enroll(f"http://{C.HOST}:{PORT}", token, f"qa-{NONCE}-same", "10.99.0.1") for _ in range(12)]
    res["groups"]["A_same_params_sequential"] = {"results": a,
                                                 "http_counts": counts(a)}

    # B: 12x different params, rapid sequential
    b = [enroll(f"http://{C.HOST}:{PORT}", token, f"qa-{NONCE}-diff-{i:02d}", f"10.99.1.{i}")
         for i in range(12)]
    res["groups"]["B_diff_params_sequential"] = {"results": b, "http_counts": counts(b)}

    # C: 12x concurrent
    with ThreadPoolExecutor(max_workers=12) as ex:
        c = list(ex.map(lambda i: enroll(f"http://{C.HOST}:{PORT}", token,
                                         f"qa-{NONCE}-conc-{i:02d}", f"10.99.2.{i}"), range(12)))
    res["groups"]["C_concurrent"] = {"results": c, "http_counts": counts(c)}

    # Hub log error lines
    lg = sh(f"grep -n -iE 'error|busy|locked|constraint|panic' {LOG} | tail -60", check=False).stdout
    res["hub_log_error_lines"] = lg.splitlines()
    res["hub_log_sha"] = sh(f"sha256sum {LOG} | awk '{{print $1}}'").stdout.strip()
    res["nodes_after"] = sh(f"sqlite3 {DATA_DIR}/nodesteer.db 'select count(*) from nodes' 2>/dev/null || echo n/a",
                            check=False).stdout.strip()

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "groups"}, ensure_ascii=False, indent=1))
    for g, v in res["groups"].items():
        print(g, v["http_counts"])
        for r in v["results"]:
            if r["http"] != 200:
                print("   NON200:", r)


def counts(rs):
    c = {}
    for r in rs:
        c[str(r["http"])] = c.get(str(r["http"]), 0) + 1
    return c


if __name__ == "__main__":
    main()
