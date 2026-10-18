python3 - 7b8929ed-c880-46f1-90e6-a5e9c0dab096 9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed 1601698 c8142503fc467066265b67a1db641ec3274119961f65cdadcd92f2ba26a2e2ce <<'QA_AUTH_ARTIFACT'
"""Read the owned Agent credential in place and verify Artifact access."""
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

OWNED_DIR = Path("/tmp/qa-resume-20261008-child-ciivjupc")
CHILD_CONFIG = OWNED_DIR / "child.yaml"
EXPECTED_NODE_ID = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
EXPECTED_AGENT_ID = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
EXPECTED_CHILD_PID = 49873
EXPECTED_CHILD_STARTTICKS = 137980573
EXPECTED_CHILD_UID = 65534
EXPECTED_CHILD_EXE_SHA256 = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
EXPECTED_PARENT_MAIN_PID = 32487
EXPECTED_ROOT_DEV = 33
EXPECTED_ROOT_INO = 135
EXPECTED_ROOT_UID = 65534
EXPECTED_ROOT_MODE = 0o700
HUB = "http://192.168.100.209:18080"
TIMEOUT = 25


def blocked(code):
    print(json.dumps({"status": "BLOCKED", "reason": code}, separators=(",", ":")))
    raise SystemExit(0)


def file_sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def process_startticks(pid):
    raw = Path("/proc", str(pid), "stat").read_text()
    return int(raw[raw.rfind(")") + 2 :].split()[19])


def process_uid(pid):
    for line in Path("/proc", str(pid), "status").read_text().splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise ValueError("uid_missing")


def process_argv(pid):
    return [x.decode(errors="replace") for x in Path("/proc", str(pid), "cmdline").read_bytes().split(b"\0") if x]


def parent_unit_is_current():
    cgroup_path = None
    for line in Path("/proc", str(EXPECTED_PARENT_MAIN_PID), "cgroup").read_text().splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3:
            cgroup_path = parts[2]
            if cgroup_path:
                break
    if not cgroup_path:
        return False
    units = [part for part in cgroup_path.split("/") if part.endswith(".service")]
    if len(units) != 1 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.@-" for ch in units[0]):
        return False
    try:
        result = subprocess.run(
            ["systemctl", "show", units[0], "-p", "LoadState", "-p", "ActiveState", "-p", "MainPID"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    values = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return (
        values.get("LoadState") == "loaded"
        and values.get("ActiveState") == "active"
        and values.get("MainPID") == str(EXPECTED_PARENT_MAIN_PID)
    )


def config_string(key):
    matches = []
    with CHILD_CONFIG.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.startswith(key + ": "):
                matches.append(line.split(": ", 1)[1].strip())
    if len(matches) != 1:
        raise ValueError("config_key_missing_or_duplicate")
    value = json.loads(matches[0])
    if not isinstance(value, str):
        raise ValueError("config_key_not_string")
    return value


def read_owner_credential(expected_manifest_sha):
    root_lstat = OWNED_DIR.lstat()
    if OWNED_DIR.is_symlink() or not OWNED_DIR.is_dir():
        raise ValueError("owned_root_not_real_directory")
    if (root_lstat.st_dev, root_lstat.st_ino, root_lstat.st_uid, stat.S_IMODE(root_lstat.st_mode)) != (
        EXPECTED_ROOT_DEV,
        EXPECTED_ROOT_INO,
        EXPECTED_ROOT_UID,
        EXPECTED_ROOT_MODE,
    ):
        raise ValueError("owned_root_identity_mismatch")
    if not parent_unit_is_current():
        raise ValueError("parent_unit_not_active_or_mainpid_changed")

    manifest = OWNED_DIR / "owner-manifest.json"
    manifest_lstat = manifest.lstat()
    if manifest.is_symlink() or not stat.S_ISREG(manifest_lstat.st_mode):
        raise ValueError("owner_manifest_not_regular")
    manifest_raw = manifest.read_bytes()
    if file_sha256(manifest) != expected_manifest_sha:
        raise ValueError("owner_manifest_sha_mismatch")
    owner = json.loads(manifest_raw)
    if (owner.get("directory_dev"), owner.get("directory_ino"), owner.get("directory_uid")) != (
        EXPECTED_ROOT_DEV,
        EXPECTED_ROOT_INO,
        EXPECTED_ROOT_UID,
    ):
        raise ValueError("owner_manifest_root_mismatch")
    if (owner.get("node_id"), owner.get("agent_id")) != (EXPECTED_NODE_ID, EXPECTED_AGENT_ID):
        raise ValueError("owner_manifest_node_identity_mismatch")
    marker = OWNED_DIR / "owner.marker"
    if marker.is_symlink() or marker.read_text(encoding="utf-8") != owner.get("owner_marker"):
        raise ValueError("owner_marker_mismatch")

    child = owner.get("child", {})
    if (child.get("pid"), child.get("starttime"), child.get("uid"), child.get("exe_sha256")) != (
        EXPECTED_CHILD_PID,
        EXPECTED_CHILD_STARTTICKS,
        EXPECTED_CHILD_UID,
        EXPECTED_CHILD_EXE_SHA256,
    ):
        raise ValueError("owner_manifest_child_mismatch")
    pid = EXPECTED_CHILD_PID
    if process_startticks(pid) != EXPECTED_CHILD_STARTTICKS or process_uid(pid) != EXPECTED_CHILD_UID:
        raise ValueError("child_process_identity_mismatch")
    argv = process_argv(pid)
    if str(CHILD_CONFIG) not in argv:
        raise ValueError("child_config_not_in_process_argv")
    if file_sha256(Path("/proc", str(pid), "exe")) != EXPECTED_CHILD_EXE_SHA256:
        raise ValueError("child_process_exe_sha_mismatch")

    cfg = owner.get("config", {})
    if Path(cfg.get("path", "")) != CHILD_CONFIG:
        raise ValueError("owner_config_path_mismatch")
    config_stat = CHILD_CONFIG.lstat()
    if CHILD_CONFIG.is_symlink() or not stat.S_ISREG(config_stat.st_mode):
        raise ValueError("child_config_not_regular")
    if stat.S_IMODE(config_stat.st_mode) != 0o600 or config_stat.st_uid != EXPECTED_ROOT_UID:
        raise ValueError("child_config_owner_or_mode_mismatch")
    if config_string("agent_id") != EXPECTED_AGENT_ID:
        raise ValueError("child_config_agent_id_mismatch")

    db_path = OWNED_DIR / "data" / "state.db"
    db_stat = db_path.lstat()
    if (
        db_path.is_symlink()
        or not stat.S_ISREG(db_stat.st_mode)
        or db_stat.st_uid != EXPECTED_ROOT_UID
        or (db_stat.st_dev, db_stat.st_ino) != (33, 155)
    ):
        raise ValueError("identity_database_owner_or_type_mismatch")
    uri = "file:" + urllib.parse.quote(str(db_path), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=2)
    try:
        db.execute("PRAGMA query_only=ON")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "identity" not in tables:
            raise ValueError("identity_table_missing")
        values = dict(db.execute("SELECT key, value FROM identity WHERE key IN (?, ?, ?)", ("node_id", "agent_id", "credential")))
    finally:
        db.close()
    if values.get("node_id") != EXPECTED_NODE_ID or values.get("agent_id") != EXPECTED_AGENT_ID:
        raise ValueError("database_identity_mismatch")
    credential = values.get("credential")
    if not isinstance(credential, str) or not credential:
        raise ValueError("agent_credential_missing")
    return credential


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_artifact(artifact_id, agent_id, token, expected_sha, expected_size):
    url = HUB + "/api/artifacts/" + urllib.parse.quote(artifact_id, safe="") + "/download"
    req = urllib.request.Request(
        url,
        headers={"X-NodeSteer-Agent-ID": agent_id, "X-NodeSteer-Agent-Token": token},
        method="GET",
    )
    opener = urllib.request.build_opener(NoRedirect)
    try:
        response = opener.open(req, timeout=TIMEOUT)
        status, headers, stream = response.status, response.headers, response
    except urllib.error.HTTPError as exc:
        status, headers, stream = exc.code, exc.headers, exc

    if status != 200:
        raw = stream.read(4096)
        try:
            body = json.loads(raw)
            json_error = isinstance(body, dict) and "error" in body
        except Exception:
            json_error = False
        return {"status": status, "json_error": json_error}

    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        digest.update(chunk)
    sha = digest.hexdigest()
    return {
        "status": status,
        "size": size,
        "sha256": sha,
        "size_match": size == expected_size and headers.get("Content-Length") == str(expected_size),
        "sha256_match": sha == expected_sha and headers.get("X-Artifact-SHA256") == expected_sha,
    }


def main():
    if len(sys.argv) != 5:
        blocked("expected_safe_metadata_arguments")
    artifact_id, expected_sha, expected_size_raw, expected_manifest_sha = sys.argv[1:]
    if len(expected_sha) != 64 or len(expected_manifest_sha) != 64 or not expected_size_raw.isdigit():
        blocked("safe_metadata_argument_validation_failed")
    expected_size = int(expected_size_raw)
    try:
        credential = read_owner_credential(expected_manifest_sha)
        allowed = request_artifact(artifact_id, EXPECTED_AGENT_ID, credential, expected_sha, expected_size)
        denied_id = "agent-qa-auth-deny-" + str(uuid.uuid4())
        denied = request_artifact(artifact_id, denied_id, credential, expected_sha, expected_size)
        del credential
    except Exception:
        blocked("owner_guard_or_request_failed")
    result = {
        "status": "PASS"
        if allowed.get("status") == 200
        and allowed.get("size_match")
        and allowed.get("sha256_match")
        and denied.get("status") == 401
        and denied.get("json_error")
        else "PARTIAL",
        "owner_guard_passed": True,
        "correct_agent": allowed,
        "wrong_agent_id": denied,
    }
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
QA_AUTH_ARTIFACT
