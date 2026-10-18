#!/usr/bin/env python3
"""Draft only: reviewed noHost SYS-12 container-A bootstrap; never run in this turn."""
import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import tarfile
from pathlib import Path

IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
IMAGE_OWNER = "qa-resume-20261010-host-f174890c3846"
ORIGINAL_ROOT = Path("/opt/qa-resume-20261010-host-f174890c3846")
ORIGINAL_CFG = ORIGINAL_ROOT / "private/host001-agent.yaml"
ARCHIVE = ORIGINAL_ROOT / "agent-rootfs.tar"
ARCHIVE_SHA = "d012a9301f242511460aa212608f8965879866ae6d64d8e2b8c40b7c0a0da490"
BASE_DIR = Path("/opt")
STATE = {"root": None, "network_created": False, "container_id": None}


class SafeStop(Exception):
    pass


def run(argv, timeout=15):
    p = subprocess.run(argv, text=True, capture_output=True, timeout=timeout, check=False)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def guarded_read(path, limit, uid, mode):
    p = Path(path)
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode) or p.is_symlink() or st.st_uid != uid or stat.S_IMODE(st.st_mode) != mode:
        raise SafeStop("config_file_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino, fs.st_uid, stat.S_IMODE(fs.st_mode)) != (
                st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode)):
            raise SafeStop("config_changed_during_open")
        chunks = bytearray()
        while len(chunks) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not block:
                break
            chunks.extend(block)
    finally:
        os.close(fd)
    if len(chunks) > limit:
        raise SafeStop("config_size_guard_failed")
    return st, bytes(chunks)


def original_root_guard():
    st = ORIGINAL_ROOT.lstat()
    if (not stat.S_ISDIR(st.st_mode) or ORIGINAL_ROOT.is_symlink() or ORIGINAL_ROOT.resolve() != ORIGINAL_ROOT
            or (st.st_dev, st.st_ino, st.st_uid, stat.S_IMODE(st.st_mode)) != (65025, 70286, 0, 0o700)):
        raise SafeStop("original_root_guard_failed")
    private = ORIGINAL_CFG.parent
    ps = private.lstat()
    if (not stat.S_ISDIR(ps.st_mode) or private.is_symlink() or private.resolve() != ORIGINAL_ROOT / "private"
            or ps.st_uid != 0 or stat.S_IMODE(ps.st_mode) != 0o700):
        raise SafeStop("original_private_directory_guard_failed")
    return st, ps


def original_registration_lines():
    original_root_guard()
    _, raw = guarded_read(ORIGINAL_CFG, 1024 * 1024, 0, 0o600)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise SafeStop("original_config_encoding_invalid")
    values = {}
    for line in lines:
        key, sep, value = line.partition(":")
        key = key.strip()
        if sep and key in {"hub_url", "registration_token"}:
            if key in values or not value.strip():
                raise SafeStop("required_config_key_invalid")
            values[key] = line
    if set(values) != {"hub_url", "registration_token"}:
        raise SafeStop("required_config_keys_missing")
    return [values["hub_url"], values["registration_token"]]


def check_shell_archive(expected_shell_sha):
    original_root_guard()
    st, _ = guarded_read(ARCHIVE, 64 * 1024 * 1024, 0, stat.S_IMODE(ARCHIVE.lstat().st_mode))
    fd = os.open(ARCHIVE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as f:
        fs = os.fstat(f.fileno())
        if (fs.st_dev, fs.st_ino, fs.st_uid) != (st.st_dev, st.st_ino, st.st_uid):
            raise SafeStop("archive_changed_during_open")
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
        if h.hexdigest() != ARCHIVE_SHA:
            raise SafeStop("archive_sha_guard_failed")
        f.seek(0)
        with tarfile.open(fileobj=f, mode="r:*") as tf:
            names = set(tf.getnames())
            entries = [name for name in ("rootfs/bin/sh", "bin/sh") if name in names]
            if len(entries) != 1:
                raise SafeStop("shell_member_cardinality_unknown")
            member = tf.getmember(entries[0])
            if not member.isfile() or member.mode & 0o111 == 0:
                raise SafeStop("shell_member_not_executable_regular")
            actual = sha(tf.extractfile(member).read())
            if actual != expected_shell_sha:
                raise SafeStop("shell_member_sha_guard_failed")


def docker_exact_absent(kind, name):
    rc, out, err = run(["docker", kind, "inspect", name])
    if rc == 0:
        raise SafeStop("candidate_name_collision")
    low = err.lower()
    if not any(x in low for x in ("no such", "not found", "does not exist")):
        raise SafeStop("candidate_inspect_unknown")


def image_guard():
    rc, out, _ = run(["docker", "image", "inspect", IMAGE])
    if rc:
        raise SafeStop("pinned_image_inspect_failed")
    rows = json.loads(out)
    if len(rows) != 1:
        raise SafeStop("pinned_image_inspect_cardinality")
    image = rows[0]
    labels = ((image.get("Config") or {}).get("Labels") or {})
    if image.get("Id") != IMAGE or labels.get("nodesteer.e2e.owner") != IMAGE_OWNER:
        raise SafeStop("pinned_image_identity_mismatch")


def write_owned_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(fd, 65534, 65534)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(data)
            f.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def write_root_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(data)
            f.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--shell-sha256", required=True)
    ap.add_argument("--launch", action="store_true", help="resource creation; requires separate Root approval")
    args = ap.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12}", args.nonce) or not re.fullmatch(r"[0-9a-f]{64}", args.shell_sha256):
        raise SafeStop("nonce_or_sha_shape_invalid")
    if not args.launch:
        print(json.dumps({"status": "DRAFT_NO_RESOURCE_ACTION", "resource_actions": False,
                          "nonce_shape_valid": True, "shell_sha_shape_valid": True,
                          "secret_values_emitted": False}, separators=(",", ":")))
        return
    raise SafeStop("superseded_by_lifecycle_source_proposal; never launch this bootstrap draft")
    if os.geteuid() != 0:
        raise SafeStop("native2_task_requires_existing_root_docker_access")
    # Precondition: the outer controller must Admin-GET /api/nodes and prove this NodeName unused.
    node_name = "qa-sys12-docker-" + args.nonce
    root = BASE_DIR / ("qa-resume-20261010-sys12-" + args.nonce)
    private = root / "private"
    data = root / "data"
    config = private / "agent.yaml"
    owner = "qa-resume-20261010-sys12-" + args.nonce
    container = owner + "-a"
    network = "nodesteer-qa-sys12-" + args.nonce
    if BASE_DIR.is_symlink() or not BASE_DIR.is_dir() or root.exists() or root.is_symlink():
        raise SafeStop("new_root_collision_or_parent_invalid")
    docker_exact_absent("container", container)
    docker_exact_absent("network", network)
    image_guard()
    config_lines = original_registration_lines()
    check_shell_archive(args.shell_sha256)

    root.mkdir(mode=0o700)
    os.chown(root, 0, 0)
    os.chmod(root, 0o700)
    STATE["root"] = str(root)
    rs = root.lstat()
    owner_marker = {"owner": owner, "nonce": args.nonce, "root": str(root),
                    "root_dev": rs.st_dev, "root_inode": rs.st_ino, "uid": rs.st_uid,
                    "mode": format(stat.S_IMODE(rs.st_mode), "04o"), "image_id": IMAGE,
                    "container_name": container, "network_name": network, "node_name": node_name}
    marker = root / "owner.json"
    write_root_file(marker, (json.dumps(owner_marker, sort_keys=True, separators=(",", ":")) + "\n").encode())
    private.mkdir(mode=0o700)
    data.mkdir(mode=0o700)
    for path in (private, data):
        os.chown(path, 65534, 65534)
        os.chmod(path, 0o700)
    cfg_text = "\n".join(config_lines + ["node_name: " + json.dumps(node_name)]) + "\n"
    write_owned_file(config, cfg_text.encode())
    config_st = config.lstat()
    if (config_st.st_uid, config_st.st_gid, stat.S_IMODE(config_st.st_mode)) != (65534, 65534, 0o600):
        raise SafeStop("derived_config_owner_mode_guard_failed")

    rc, _, _ = run(["docker", "network", "create", "--driver", "bridge",
                    "--label", "nodesteer.e2e.owner=" + owner, network])
    if rc:
        raise SafeStop("owned_network_create_failed")
    STATE["network_created"] = True
    argv = ["docker", "run", "--detach", "--name", container,
            "--label", "nodesteer.e2e.owner=" + owner,
            "--user", "65534:65534", "--cap-drop", "ALL", "--read-only",
            "--security-opt", "no-new-privileges", "--network", network,
            "--mount", "type=bind,src=" + str(config) + ",dst=/run/nodesteer/agent.yaml,readonly",
            "--mount", "type=bind,src=" + str(data) + ",dst=/var/lib/nodesteer",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m", IMAGE]
    rc, container_id, _ = run(argv, timeout=35)
    if rc or not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise SafeStop("owned_container_start_failed")
    STATE["container_id"] = container_id
    rc, inspect_out, _ = run(["docker", "container", "inspect", container_id])
    if rc:
        raise SafeStop("created_container_readback_failed_keep_for_review")
    row = json.loads(inspect_out)[0]
    hc = row.get("HostConfig") or {}
    mounts = row.get("Mounts") or []
    mount_sources = {m.get("Source"): m.get("Destination") for m in mounts if isinstance(m, dict)}
    expected_mounts = {str(config): "/run/nodesteer/agent.yaml", str(data): "/var/lib/nodesteer"}
    running_guard = (row.get("Id") == container_id and row.get("Image") == IMAGE
                     and (row.get("Config") or {}).get("User") == "65534:65534"
                     and hc.get("ReadonlyRootfs") is True and hc.get("Privileged") is False
                     and [x.upper() for x in hc.get("CapDrop", [])] == ["ALL"]
                     and hc.get("NetworkMode") == network and mount_sources == expected_mounts)
    if not running_guard:
        raise SafeStop("created_container_policy_or_mount_guard_failed_keep_for_review")
    netinfo = ((row.get("NetworkSettings") or {}).get("Networks") or {}).get(network) or {}
    print(json.dumps({"status": "OWNED_NONHOST_CONTAINER_A_STARTED", "owner": owner,
                      "node_name": node_name, "container_id": container_id,
                      "image_id": IMAGE, "network_name": network,
                      "assigned_ip": netinfo.get("IPAddress"),
                      "root_dev": rs.st_dev, "root_inode": rs.st_ino,
                      "config_uid": config_st.st_uid, "config_gid": config_st.st_gid,
                      "config_mode": format(stat.S_IMODE(config_st.st_mode), "04o"),
                      "secret_values_emitted": False, "admin_or_node_credential_in_source": False},
                     sort_keys=True, separators=(",", ":")))


try:
    main()
except SafeStop as exc:
    print(json.dumps({"status": "SAFE_STOP", "reason": str(exc), "owned_root": STATE["root"],
                      "owned_network_created": STATE["network_created"],
                      "owned_container_id": STATE["container_id"],
                      "secret_values_emitted": False}, separators=(",", ":")))
except Exception as exc:
    print(json.dumps({"status": "SAFE_STOP", "reason": "unexpected_" + type(exc).__name__,
                      "owned_root": STATE["root"], "owned_network_created": STATE["network_created"],
                      "owned_container_id": STATE["container_id"],
                      "secret_values_emitted": False}, separators=(",", ":")))
