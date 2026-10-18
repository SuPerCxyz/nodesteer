"""P-TABLE 批次公共工具：ssh 执行、本地凭据文件、隧道、agent-browser 调用。

约束：
- 不在 stdout / 聊天中输出凭据明文；
- 凭据只落盘到 0600 文件（/tmp/opencode/ptable-creds-*.json），收尾删除；
- 仅访问隔离实例（18082 隧道）与 repair authority 只读端点。
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

HOST = "192.168.100.209"
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_BIN = "/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard"
REMOTE_BIN_SHA = "164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6"
REPAIR_URL = "http://192.168.100.209:18080"
INSTANCE_PORT = 18082
TUNNEL_PORT = 38282
SESSION = "qaweb-ptable-20261015"
ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "evidence"
CRED_DIR = Path("/tmp/opencode")


def ssh(script: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        [
            "ssh", "-i", SSH_KEY, "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no", f"root@{HOST}", "bash -s",
        ],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"ssh_failed rc={cp.returncode} err={cp.stderr.strip()[:400]}")
    return cp


def local(cmd: list[str], timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError(f"local_failed {cmd[0]} rc={cp.returncode} err={cp.stderr.strip()[:400]}")
    return cp


def creds_path(nonce: str) -> Path:
    return CRED_DIR / f"ptable-creds-{nonce}.json"


def save_creds(nonce: str, data: dict) -> Path:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    p = creds_path(nonce)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data))
    return p


def load_creds(nonce: str) -> dict:
    return json.loads(creds_path(nonce).read_text())


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def gen_password() -> str:
    return secrets.token_urlsafe(24)


# ---------------- agent-browser helpers ----------------

def ab(*args: str, timeout: int = 30, check: bool = True) -> subprocess.CompletedProcess:
    return local(["agent-browser", "--session", SESSION, *args], timeout=timeout, check=check)


def ab_eval(js: str, timeout: int = 30) -> str:
    cp = local(["agent-browser", "--session", SESSION, "eval", "-b", b64(js)],
               timeout=timeout)
    return cp.stdout.strip()


def ab_json(js: str, timeout: int = 40, retries: int = 3):
    """执行 JS 并把 JSON.stringify 结果解析回 Python 对象（支持 await）。"""
    last = None
    for _ in range(retries):
        try:
            raw = ab_eval(f"(async()=>{{ {js} }})().then(v => JSON.stringify(v))",
                          timeout=timeout)
            if not raw:
                time.sleep(0.4)
                continue
            val = json.loads(raw)
            return json.loads(val) if isinstance(val, str) else val
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"ab_json_failed: {last}")


def origin() -> str:
    return f"http://127.0.0.1:{TUNNEL_PORT}"


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_evidence(name: str, payload: dict) -> str:
    EVID.mkdir(parents=True, exist_ok=True)
    p = EVID / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return f"evidence/{name}"
