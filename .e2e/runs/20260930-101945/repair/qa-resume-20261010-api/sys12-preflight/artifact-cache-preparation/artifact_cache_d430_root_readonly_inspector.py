#!/usr/bin/env python3
"""Read-only bounded inventory for the exact d430 owned root; never removes or reads payloads."""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
NONCE = "d430d8074305"
OWNER = "qa-resume-20261012-artcache-d430d8074305"
ROOT = Path("/opt") / OWNER
ROOT_DEV = 65025
ROOT_INODE = 13447875
NODE_ID = "668cbe00-e1ea-4d51-9282-324fa2fc7a97"
CONTAINER_A_ID = "12428f37b52c808188c89094d06142a878ebb12c879ce8b48a9b36077451ebe1"
CONTAINER_A_NAME = OWNER + "-a"
NETWORK_ID = "33e6b0b0d8aefd7f37c7adf44547f09fe18c79979079d2721a097e7eee3b9b2a"
NETWORK_NAME = "nodesteer-qa-artcache-d430d8074305"
SHELL_SHA = "a6f559e00b69a4aa4d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
MAX_JSON = 128 * 1024
MAX_ENTRIES_PER_DIR = 256


class SafeStop(Exception):
    pass


def digest_name(name):
    return hashlib.sha256(name.encode("utf-8", "surrogateescape")).hexdigest()


def safe_name(name):
    return name if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name) else "sha256:" + digest_name(name)


def read_owned_json(path, limit, uid, gid, mode):
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or path.is_symlink()
            or (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) != (uid, gid, mode)
            or before.st_size > limit):
        raise SafeStop("owner_metadata_file_guard_failed")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid,
                stat.S_IMODE(opened.st_mode)) != (before.st_dev, before.st_ino, uid, gid, mode):
            raise SafeStop("owner_metadata_open_identity_changed")
        raw = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    if len(raw) > limit:
        raise SafeStop("owner_metadata_size_limit")
    return json.loads(raw)


def docker_inspect(kind, ident):
    proc = subprocess.run(["docker", kind, "inspect", ident], capture_output=True,
                          text=True, timeout=8, check=False)
    if proc.returncode != 0:
        low = proc.stderr.lower()
        exact = (f"no such {kind}: {ident.lower()}" in low
                 or f"no such object: {ident.lower()}" in low
                 or f"{kind} {ident.lower()} not found" in low
                 or f"{kind} {ident.lower()}: not found" in low)
        return {"exists": False if exact else None,
                "classification": "EXACT_MISSING" if exact else "UNKNOWN_ERROR"}
    try:
        rows = json.loads(proc.stdout)
    except ValueError:
        raise SafeStop("docker_inspect_json_invalid")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise SafeStop("docker_inspect_shape_invalid")
    row = rows[0]
    if kind == "container":
        conf = row.get("Config") or {}
        labels = conf.get("Labels") or {}
        return {"exists": True, "id_matches": row.get("Id") == CONTAINER_A_ID,
                "name_matches": row.get("Name") == "/" + CONTAINER_A_NAME,
                "owner_label_matches": labels.get("nodesteer.e2e.owner") == OWNER}
    labels = row.get("Labels") or {}
    return {"exists": True, "id_matches": row.get("Id") == NETWORK_ID,
            "name_matches": row.get("Name") == NETWORK_NAME,
            "owner_label_matches": labels.get("nodesteer.e2e.owner") == OWNER}


def stat_record(path, depth):
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode): kind = "symlink"
    elif stat.S_ISDIR(st.st_mode): kind = "directory"
    elif stat.S_ISREG(st.st_mode): kind = "regular"
    else: kind = "other"
    return {"name": safe_name(path.name), "depth": depth, "type": kind,
            "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode),
            "dev": st.st_dev, "inode": st.st_ino, "size": st.st_size}


def bounded_entries(directory, depth):
    out = []
    with os.scandir(directory) as it:
        for i, entry in enumerate(it):
            if i >= MAX_ENTRIES_PER_DIR:
                raise SafeStop("data_directory_entry_limit")
            path = Path(entry.path)
            row = stat_record(path, depth)
            out.append(row)
            if depth == 1 and row["type"] == "directory":
                child = path.lstat()
                if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
                    out.extend(bounded_entries(path, 2))
    return out


def inspect():
    root_st = ROOT.lstat()
    if (not stat.S_ISDIR(root_st.st_mode) or ROOT.is_symlink() or ROOT.resolve() != ROOT
            or (root_st.st_dev, root_st.st_ino, root_st.st_uid,
                stat.S_IMODE(root_st.st_mode)) != (ROOT_DEV, ROOT_INODE, 0, 0o700)):
        raise SafeStop("exact_owned_root_pin_mismatch")
    marker = read_owned_json(ROOT / "owner.json", 65536, 0, 0, 0o600)
    ledger = read_owned_json(ROOT / "resource-ledger.json", 131072, 0, 0, 0o600)
    for obj in (marker, ledger):
        if (obj.get("owner") != OWNER or obj.get("nonce") != NONCE
                or obj.get("root") != str(ROOT) or obj.get("root_dev") != ROOT_DEV
                or obj.get("root_inode") != ROOT_INODE or obj.get("image_id") != IMAGE
                or obj.get("network_name") != NETWORK_NAME):
            raise SafeStop("owner_nonce_root_binding_mismatch")
    if (marker.get("container_a_id") is not None or marker.get("container_b_id") is not None
            or marker.get("network_id") is not None):
        raise SafeStop("runtime_removal_not_recorded_in_marker")
    data = ROOT / "data"
    ds = data.lstat()
    if (data.is_symlink() or not stat.S_ISDIR(ds.st_mode)
            or (ds.st_uid, ds.st_gid, stat.S_IMODE(ds.st_mode)) != (65534, 65534, 0o700)
            or data.resolve() != ROOT.resolve() / "data"):
        raise SafeStop("owned_data_directory_guard_failed")
    entries = bounded_entries(data, 1)
    a_by_id = docker_inspect("container", CONTAINER_A_ID)
    a_by_name = docker_inspect("container", CONTAINER_A_NAME)
    n_by_id = docker_inspect("network", NETWORK_ID)
    n_by_name = docker_inspect("network", NETWORK_NAME)
    return {"status": "READ_ONLY_EXACT_ROOT_INSPECTION",
            "credentials_emitted": False,
            "owner_marker_matches": True,
            "resource_ledger_matches": True,
            "root": {"dev": root_st.st_dev, "inode": root_st.st_ino,
                     "uid": root_st.st_uid, "gid": root_st.st_gid,
                     "mode": stat.S_IMODE(root_st.st_mode)},
            "api_node_id_external_readback_required": NODE_ID,
            "container_a_by_id": a_by_id, "container_a_by_name": a_by_name,
            "network_by_id": n_by_id, "network_by_name": n_by_name,
            "data_entry_count_depth_1_and_2": len(entries),
            "data_entries_metadata_only": entries,
            "contents_read": False, "mutations": 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("inspect",), required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--shell-sha256", required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    if not args.execute or args.nonce != NONCE or args.shell_sha256 != SHELL_SHA:
        raise SafeStop("exact_readonly_probe_invocation_required")
    print(json.dumps(inspect(), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except SafeStop as exc:
        print(json.dumps({"status": "SAFE_STOP", "credentials_emitted": False,
                          "safe_error": str(exc), "mutations": 0}, sort_keys=True))
        raise SystemExit(0)
    except Exception as exc:
        print(json.dumps({"status": "SAFE_STOP", "credentials_emitted": False,
                          "safe_error": "unexpected_" + type(exc).__name__, "mutations": 0}, sort_keys=True))
        raise SystemExit(0)
