"""G1 批次公共工具：ssh、凭据文件、Hub HTTP 客户端、evidence 写入。

约束（源自 authorization-checklist-20261016.md G1 / 0.1 红线）：
- 凭据只落盘 0600（/tmp/opencode/g1-creds-*.json），收尾删除；stdout/evidence 不出现明文；
- 只写信隔离实例；repair :18080 仅只读 GET；绝不触碰 primary :8080；
- 不触碰其它批次进程/目录/端口。
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
REMOTE_BIN_SHA = "cd8b7f9d7e40841bbba982c6838b2f74ccfdfac785674030c5bf7d6f1f69120d"
REMOTE_AGENT = "/opt/nodesteer-repair-20260930-101945/agent-static/nodesteer-agent"
REPAIR_URL = "http://192.168.100.209:18080"
INSTANCE_PORT = 18085
TUNNEL_PORT = 38285
ROOT = Path(__file__).resolve().parent.parent
EVID = ROOT / "evidence"
TERM = ROOT / "terminal"
CRED_DIR = Path("/tmp/opencode")
NONCE_FILE = ROOT / "runners" / ".nonce"


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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def creds_path(nonce: str) -> Path:
    return CRED_DIR / f"g1-creds-{nonce}.json"


def save_creds(nonce: str, data: dict) -> Path:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    p = creds_path(nonce)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data))
    return p


def load_creds(nonce: str | None = None) -> dict:
    if nonce is None:
        nonce = NONCE_FILE.read_text().strip()
    return json.loads(creds_path(nonce).read_text())


def set_nonce(nonce: str) -> None:
    NONCE_FILE.write_text(nonce + "\n")


def get_nonce() -> str:
    return NONCE_FILE.read_text().strip()


def write_evidence(name: str, payload) -> str:
    EVID.mkdir(parents=True, exist_ok=True)
    p = EVID / name
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n")
    return f"evidence/{name}"


def write_terminal(name: str, text: str) -> str:
    TERM.mkdir(parents=True, exist_ok=True)
    p = TERM / name
    p.write_text(text)
    return f"terminal/{name}"


def _try_json(payload: bytes):
    try:
        return json.loads(payload.decode())
    except Exception:  # noqa: BLE001
        return None


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
        t0 = time.time()
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as resp:
                payload = resp.read()
                return {"status": resp.status, "json": _try_json(payload),
                        "body_raw": payload, "ms": int((time.time() - t0) * 1000)}
        except urllib.error.HTTPError as e:
            payload = e.read()
            return {"status": e.code, "json": _try_json(payload),
                    "body_raw": payload, "ms": int((time.time() - t0) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"status": 0, "error": repr(exc), "json": None, "body_raw": b""}

    get = lambda self, p, t=None, **kw: self.req("GET", p, t, **kw)  # noqa: E731
    post = lambda self, p, t=None, **kw: self.req("POST", p, t, **kw)  # noqa: E731
    put = lambda self, p, t=None, **kw: self.req("PUT", p, t, **kw)  # noqa: E731
    delete = lambda self, p, t=None, **kw: self.req("DELETE", p, t, **kw)  # noqa: E731

    def login(self, username: str, password: str) -> dict:
        return self.post("/api/login", body={"username": username, "password": password})

    def token(self, username: str, password: str) -> str:
        r = self.login(username, password)
        if r["status"] != 200 or not isinstance(r["json"], dict) or not r["json"].get("token"):
            raise RuntimeError(f"login failed status={r['status']}")
        return r["json"]["token"]


def shape(value) -> str:
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null-or-nonjson"
    return type(value).__name__


def brief(r: dict, keys=("status", "json")) -> dict:
    out = {"http": r.get("status")}
    j = r.get("json")
    if isinstance(j, dict):
        out["body_keys"] = sorted(j.keys())[:12]
        for k in ("error", "id", "name", "username", "role", "token"):
            if k in j and k != "token":
                out[k] = j[k]
        if "token" in j:
            out["token_present"] = bool(j["token"])
    elif isinstance(j, list):
        out["items"] = len(j)
    elif j is None:
        out["body"] = r.get("body_raw", b"")[:120].decode(errors="replace")
    return out


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results", "nodes", "users", "applications", "tasks",
                  "schedules", "scripts", "artifacts", "executions", "audit", "groups"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def audit_rows(hub: Hub, token: str, limit: int = 500) -> tuple[int, list]:
    """读取审计（只读）。返回 (http, rows)。"""
    r = hub.get(f"/api/audit?limit={limit}", token)
    j = r["json"]
    rows = j if isinstance(j, list) else as_list(j)
    return r["status"], rows


def counts(hub: Hub, token: str) -> dict:
    out = {}
    for ep in ("/api/nodes", "/api/applications", "/api/tasks", "/api/schedules",
               "/api/scripts", "/api/artifacts", "/api/executions", "/api/users",
               "/api/groups", "/api/transfers"):
        r = hub.get(ep, token)
        lst = as_list(r["json"])
        out[ep] = {"http": r["status"], "count": len(lst)}
    return out


def state_fingerprint(hub: Hub, token: str) -> dict:
    c = counts(hub, token)
    users = as_list(hub.get("/api/users", token)["json"])
    ident = sorted((u.get("id"), u.get("username"), u.get("role")) for u in users
                   if isinstance(u, dict))
    return {"counts": {k: v["count"] for k, v in c.items()},
            "users_identity_sha256": sha256(json.dumps(ident, sort_keys=True).encode())}
