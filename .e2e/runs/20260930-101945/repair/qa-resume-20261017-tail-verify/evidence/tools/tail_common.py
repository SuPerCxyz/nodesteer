"""tail-verify 批次公共工具（2026-10-17）。

只读判定批：不改源码/配置/.e2e 状态文件；repair :18080 仅只读探测；
primary :8080 全程不触碰；隔离实例只在 :18088 上创建并整体销毁。
真实凭据只落 /tmp/opencode/tail-creds-<nonce>.json（0600），不进证据。
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from pathlib import Path

HOST = "192.168.100.209"
SSH_KEY = os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_BIN = "/opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard"
REPAIR_URL = "http://192.168.100.209:18080"
INSTANCE_PORT = 18088
CRED_DIR = Path("/tmp/opencode")

os.umask(0o077)


def ssh(script: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    cp = subprocess.run(
        ["ssh", "-i", SSH_KEY, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"root@{HOST}", "bash -s"],
        input=script, capture_output=True, text=True, timeout=timeout,
    )
    if check and cp.returncode != 0:
        raise RuntimeError(f"ssh_failed rc={cp.returncode} err={cp.stderr.strip()[:400]}")
    return cp


def gen_password() -> str:
    return secrets.token_urlsafe(24)


def gen_token() -> str:
    return secrets.token_urlsafe(32)
