"""G8+G9 批次公共工具（2026-10-17）。

不修改任何既有批次目录（g1 / webgap / appui 仅作写法参考）。

约束（authorization-checklist-20261016.md G8/G9 + 0.1 红线）：
- G8 遮蔽矩阵只在**隔离可销毁实例**上构造敏感值（标记值）；真实凭据不落盘/不落证据；
- G9 数据集只在同一隔离实例内构造；不得在共享 repair 权威 :18080 造数据；
- repair :18080 只读；primary :8080 全程不触碰；
- 凭据文件 0600（/tmp/opencode/g89-creds-<nonce>.json），收尾删除。
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
REMOTE_BIN_SHA_EXPECTED = "fdfa667bf51ce1a7e9a0b1aeeeef7660764b605360613d14fb518b67015e36d1"
# 当前 repair native Agent 实际运行的 CR-1 构建（internal/agent/redact.go 新增流式遮蔽器，
# 旧构建 d55dc5cd 的 strings.ReplaceAll 会被跨块拆分绕过，故必须用 CR-1 Agent 才有代表性）
LOCAL_AGENT_CR1 = "/tmp/opencode/nodesteer-build-20261016-cr1/nodesteer-agent-static"
AGENT_CR1_SHA = "b15f6df540a63a8eb530ec837c57d4628ff7925822153a02d624f021a525ac4f"
REPAIR_URL = "http://192.168.100.209:18080"
INSTANCE_PORT = 18086
TUNNEL_PORT = 38286

BATCH_G8 = "qa-resume-20261017-g8"
BATCH_G9 = "qa-resume-20261017-g9"
ROOT = Path(__file__).resolve().parents[2]  # repair/
TERM_DIRS = {"g8": ROOT / BATCH_G8 / "terminal", "g9": ROOT / BATCH_G9 / "terminal"}
EVID_DIRS = {"g8": ROOT / BATCH_G8 / "evidence", "g9": ROOT / BATCH_G9 / "evidence"}
NONCE_FILE = Path(__file__).resolve().parent / ".nonce"
CRED_DIR = Path("/tmp/opencode")

# 证据防泄漏：任何写入 evidence 的内容都会做一次明文替换（真实凭据 -> 占位符）
REDACTIONS: list[tuple[str, str]] = []


def ssh(script: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"root@{HOST}", "bash -s"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"ssh_failed rc={cp.returncode} err={cp.stderr.strip()[:400]}")
    return cp


def local(cmd: list[str], timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess:
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


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


# ---------------------------------------------------------------- 凭据文件
def creds_path(nonce: str) -> Path:
    return CRED_DIR / f"g89-creds-{nonce}.json"


def save_creds(nonce: str, data: dict) -> Path:
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    p = creds_path(nonce)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data))
    os.chmod(p, 0o600)
    return p


def load_creds(nonce: str | None = None) -> dict:
    if nonce is None:
        nonce = get_nonce()
    return json.loads(creds_path(nonce).read_text())


def set_nonce(nonce: str) -> None:
    NONCE_FILE.write_text(nonce + "\n")


def get_nonce() -> str:
    return NONCE_FILE.read_text().strip()


def register_redaction(secret: str, placeholder: str) -> None:
    """把真实凭据登记为证据写入前的强制替换项。"""
    if secret and len(secret) >= 6:
        REDACTIONS.append((secret, placeholder))


def _scrub_obj(value):
    text = json.dumps(value, ensure_ascii=False, indent=1)
    for secret, placeholder in REDACTIONS:
        if secret in text:
            text = text.replace(secret, placeholder)
    return text


def write_evidence(batch: str, name: str, payload) -> str:
    d = EVID_DIRS[batch]
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(_scrub_obj(payload) + "\n")
    return f"{BATCH_G8 if batch == 'g8' else BATCH_G9}/evidence/{name}"


def write_terminal(batch: str, name: str, text: str) -> str:
    d = TERM_DIRS[batch]
    d.mkdir(parents=True, exist_ok=True)
    for secret, placeholder in REDACTIONS:
        if secret in text:
            text = text.replace(secret, placeholder)
    (d / name).write_text(text)
    return f"terminal/{name}"


# ---------------------------------------------------------------- HTTP
def _try_json(payload: bytes):
    try:
        return json.loads(payload.decode())
    except Exception:  # noqa: BLE001
        return None


class Hub:
    """极简 Hub API 客户端；不打印凭据明文。"""

    def __init__(self, base: str, timeout: int = 25):
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
                return {"status": resp.status, "json": _try_json(payload), "body_raw": payload,
                        "headers": dict(resp.headers), "ms": int((time.time() - t0) * 1000)}
        except urllib.error.HTTPError as e:
            payload = e.read()
            return {"status": e.code, "json": _try_json(payload), "body_raw": payload,
                    "headers": dict(e.headers or {}), "ms": int((time.time() - t0) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"status": 0, "error": repr(exc), "json": None, "body_raw": b"",
                    "headers": {}, "ms": int((time.time() - t0) * 1000)}

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


def as_list(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("items", "data", "results", "nodes", "users", "applications", "tasks",
                  "schedules", "scripts", "artifacts", "executions", "audit", "groups",
                  "revisions", "chunks", "logs"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def audit_rows(hub: Hub, token: str, limit: int = 500) -> tuple[int, list]:
    r = hub.get(f"/api/audit?limit={limit}", token)
    j = r["json"]
    return r["status"], (j if isinstance(j, list) else as_list(j))


def counts(hub: Hub, token: str) -> dict:
    out = {}
    for ep in ("/api/nodes", "/api/applications", "/api/tasks", "/api/schedules",
               "/api/scripts", "/api/artifacts", "/api/executions", "/api/users",
               "/api/groups", "/api/transfers"):
        r = hub.get(ep, token)
        out[ep] = {"http": r["status"], "count": len(as_list(r["json"]))}
    return out


def state_fingerprint(hub: Hub, token: str) -> dict:
    c = counts(hub, token)
    users = as_list(hub.get("/api/users", token)["json"])
    ident = sorted((u.get("id"), u.get("username"), u.get("role")) for u in users
                   if isinstance(u, dict))
    return {"counts": {k: v["count"] for k, v in c.items()},
            "users_identity_sha256": sha256(json.dumps(ident, sort_keys=True).encode())}


# ---------------------------------------------------------------- 证据泄漏扫描
def scan_text(text: str, needles: dict[str, str]) -> dict:
    """返回 {label: 命中次数}；不在结果中回显被扫字符串本身。"""
    hits = {}
    for label, needle in needles.items():
        if not needle:
            continue
        n = text.count(needle)
        if n:
            hits[label] = n
    return hits


def scan_paths(paths: list[Path], needles: dict[str, str], exts: tuple[str, ...] | None = None) -> dict:
    """在给定文件集合内扫描凭据原文。只返回文件名与命中标签/次数，不回显值。"""
    out = {"files_scanned": 0, "hits": [], "skipped_binary": 0}
    for p in paths:
        if not p.is_file():
            continue
        if exts and p.suffix not in exts:
            continue
        try:
            raw = p.read_bytes()
        except Exception:  # noqa: BLE001
            continue
        if b"\0" in raw[:4096]:
            out["skipped_binary"] += 1
            continue
        out["files_scanned"] += 1
        text = raw.decode(errors="replace")
        h = scan_text(text, needles)
        if h:
            out["hits"].append({"file": str(p), "labels": sorted(h.keys()), "counts": h})
    return out


def walk_evidence(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            out.append(p)
    return out
