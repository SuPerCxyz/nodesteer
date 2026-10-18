"""p-api 批次公共工具：ssh、凭据文件、Hub HTTP 客户端、evidence 写入。

约束：
- 凭据只落盘 0600（/tmp/opencode/papi-creds-*.json），收尾删除；stdout/evidence 不出现明文；
- 只访问隔离实例与 repair authority 只读端点；
- 不触碰 :8080、nodesteer-agent.service、其它批次进程/目录/端口。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

HOST = "192.168.100.209"
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_BIN = "/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard"
REMOTE_BIN_SHA = "164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6"
REMOTE_AGENT_STATIC = "/opt/nodesteer-repair-20260930-101945/agent-static/nodesteer-agent"
REPAIR_URL = "http://192.168.100.209:18080"
MAIN_PORT = 18083
MAIN_TUN = 38383
TTL_PORT = 18084
TTL_TUN = 38384
ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "evidence"
TERM = ROOT / "terminal"
CRED_DIR = Path("/tmp/opencode")


def ssh(script: str, timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"root@{HOST}", "bash -s"],
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


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def gen_password() -> str:
    return secrets.token_urlsafe(24)


def gen_token() -> str:
    return secrets.token_urlsafe(32)


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def creds_path(nonce: str) -> Path:
    return CRED_DIR / f"papi-creds-{nonce}.json"


def save_creds(nonce: str, data: dict) -> Path:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    p = creds_path(nonce)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data))
    return p


def load_creds(nonce: str) -> dict:
    return json.loads(creds_path(nonce).read_text())


def write_evidence(name: str, payload) -> str:
    EVID.mkdir(parents=True, exist_ok=True)
    p = EVID / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return f"evidence/{name}"


def write_terminal(name: str, text: str) -> str:
    TERM.mkdir(parents=True, exist_ok=True)
    p = TERM / name
    p.write_text(text)
    return f"terminal/{name}"


# ---------------- Hub HTTP 客户端 ----------------

class Hub:
    """极简 Hub API 客户端；不打印/不持久化凭据明文。"""

    def __init__(self, base: str, timeout: int = 20):
        self.base = base.rstrip("/")
        self.timeout = timeout

    def req(self, method: str, path: str, token: str | None = None, body=None,
            headers: dict | None = None, raw: bytes | None = None) -> dict:
        url = self.base + path
        data = None
        hdrs = dict(headers or {})
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        if token:
            hdrs["Authorization"] = "Bearer " + token
        r = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                payload = resp.read()
                return {"status": resp.status, "headers": dict(resp.headers),
                        "body_raw": payload, "json": _try_json(payload),
                        "content_length": len(payload)}
        except urllib.error.HTTPError as e:
            payload = e.read()
            return {"status": e.code, "headers": dict(e.headers),
                    "body_raw": payload, "json": _try_json(payload),
                    "content_length": len(payload)}
        except Exception as exc:  # noqa: BLE001
            return {"status": 0, "error": repr(exc), "json": None, "body_raw": b""}

    def login(self, username: str, password: str) -> dict:
        return self.req("POST", "/api/login", body={"username": username, "password": password})

    def token(self, username: str, password: str) -> str:
        r = self.login(username, password)
        if r["status"] != 200 or not isinstance(r["json"], dict) or not r["json"].get("token"):
            raise RuntimeError(f"login failed status={r['status']}")
        return r["json"]["token"]


def _try_json(payload: bytes):
    try:
        return json.loads(payload.decode())
    except Exception:  # noqa: BLE001
        return None


def shape(value) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null-or-nonjson"
    return type(value).__name__


def body_summary(r: dict) -> dict:
    j = r.get("json")
    out = {"status": r["status"], "body_shape": shape(j)}
    if isinstance(j, dict):
        out["body_keys"] = sorted(j.keys())
        if "error" in j:
            out["error"] = j["error"]
    elif isinstance(j, list):
        out["items"] = len(j)
    return out


def stub(payload) -> dict:
    """包装成 evidence 记录（补充 observed_at）。"""
    return {"observed_at": utc(), **payload}
