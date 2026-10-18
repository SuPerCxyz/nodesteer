#!/usr/bin/env python3
"""One-shot exact cleanup for d430 only; preflights every entry before children-first removal."""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

NONCE = "d430d8074305"
OWNER = "qa-resume-20261012-artcache-d430d8074305"
IMAGE = "sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44"
ROOT = Path("/opt") / OWNER
ROOT_DEV, ROOT_INODE = 65025, 13447875
NODE_ID = "668cbe00-e1ea-4d51-9282-324fa2fc7a97"
CONTAINER_A_ID = "12428f37b52c808188c89094d06142a878ebb12c879ce8b48a9b36077451ebe1"
CONTAINER_A_NAME = OWNER + "-a"
NETWORK_ID = "33e6b0b0d8aefd7f37c7adf44547f09fe18c79979079d2721a097e7eee3b9b2a"
NETWORK_NAME = "nodesteer-qa-artcache-d430d8074305"
ARTIFACT_SHA = "7be5a1b24bfc2ac5ca4dc35368985b07deb8345ba8dc7109110431e5a399634d"
SHELL_SHA = "a6f559e00b69a4aa4d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
APPROVED_EMPTY_DIRS = {
    "scripts": 13447899,
    "artifacts": 56340,
    "applications": 4732427,
    "executions": 8703458,
    "logs": 13447900,
    "tmp": 56341,
}
STATE_DB_INODE, STATE_DB_SIZE = 4732428, 86016
MAX_JSON = 128 * 1024


class SafeStop(Exception):
    pass


def safe_name(name):
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
        return name
    return "sha256:" + hashlib.sha256(name.encode("utf-8", "surrogateescape")).hexdigest()


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


def exact_docker_absent(kind, ident):
    proc = subprocess.run(["docker", kind, "inspect", ident], capture_output=True,
                          text=True, timeout=8, check=False)
    if proc.returncode == 0:
        raise SafeStop("owned_docker_resource_still_present")
    low = proc.stderr.lower()
    if not (f"no such {kind}: {ident.lower()}" in low
            or f"no such object: {ident.lower()}" in low
            or f"{kind} {ident.lower()} not found" in low
            or f"{kind} {ident.lower()}: not found" in low):
        raise SafeStop("owned_docker_absence_unclassified")


def validate_empty_directory(path, expected_dev, expected_inode, expected_uid, expected_gid, expected_mode):
    st = path.lstat()
    if (path.is_symlink() or not stat.S_ISDIR(st.st_mode)
            or (st.st_dev, st.st_ino, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode))
            != (expected_dev, expected_inode, expected_uid, expected_gid, expected_mode)):
        raise SafeStop("approved_empty_directory_pin_mismatch")
    with os.scandir(path) as entries:
        if next(entries, None) is not None:
            raise SafeStop("approved_directory_not_empty")
    return st


def exact_owner_bindings(marker, ledger):
    for obj in (marker, ledger):
        if (obj.get("owner") != OWNER or obj.get("nonce") != NONCE
                or obj.get("root") != str(ROOT) or obj.get("root_dev") != ROOT_DEV
                or obj.get("root_inode") != ROOT_INODE or obj.get("image_id") != IMAGE
                or obj.get("network_name") != NETWORK_NAME):
            raise SafeStop("root_owner_nonce_binding_mismatch")
    if marker.get("artifact_sha256") != ARTIFACT_SHA:
        raise SafeStop("root_artifact_binding_mismatch")
    if (marker.get("container_a_id") is not None or marker.get("container_b_id") is not None
            or marker.get("network_id") is not None
            or marker.get("container_a_removed_id") != CONTAINER_A_ID
            or marker.get("network_removed_id") != NETWORK_ID):
        raise SafeStop("runtime_absence_owner_marker_mismatch")
    created = marker.get("created_container_ids")
    removed = set(ledger.get("removed_container_ids", []))
    if not isinstance(created, list) or created != [CONTAINER_A_ID] or CONTAINER_A_ID not in removed:
        raise SafeStop("created_container_removal_ledger_mismatch")
    if ledger.get("phase") not in {"RUNTIME_REMOVED_ROOT_RETAINED", "ROOT_CHILDREN_CLEANUP", "ROOT_CHILDREN_REMOVED"}:
        raise SafeStop("runtime_removal_phase_mismatch")


def bounded_names(directory, maximum, label):
    names=set()
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= maximum:
                raise SafeStop(label + "_entry_limit")
            names.add(entry.name)
    return names


def entry_metadata(path):
    st = path.lstat()
    if stat.S_ISLNK(st.st_mode): kind = "symlink"
    elif stat.S_ISDIR(st.st_mode): kind = "directory"
    elif stat.S_ISREG(st.st_mode): kind = "regular"
    else: kind = "other"
    return {"name": safe_name(path.name), "type": kind, "uid": st.st_uid,
            "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode), "dev": st.st_dev,
            "inode": st.st_ino, "size": st.st_size}


def atomic_json(path, obj, uid, gid, mode):
    temp = path.with_name(path.name + ".tmp-" + os.urandom(5).hex())
    raw = (json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)
    dirfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(dirfd)
    finally: os.close(dirfd)


def inspect_and_preflight(node_http_code):
    try: root_st = ROOT.lstat()
    except FileNotFoundError: raise SafeStop("owned_root_missing_before_cleanup")
    if (not stat.S_ISDIR(root_st.st_mode) or ROOT.is_symlink() or ROOT.resolve() != ROOT
            or (root_st.st_dev, root_st.st_ino, root_st.st_uid, stat.S_IMODE(root_st.st_mode))
            != (ROOT_DEV, ROOT_INODE, 0, 0o700)):
        raise SafeStop("exact_root_dev_inode_owner_mode_mismatch")
    if node_http_code != 404:
        raise SafeStop("owned_node_get404_external_guard_missing")
    owner_path, ledger_path = ROOT / "owner.json", ROOT / "resource-ledger.json"
    marker = read_owned_json(owner_path, 65536, 0, 0, 0o600)
    ledger = read_owned_json(ledger_path, MAX_JSON, 0, 0, 0o600)
    exact_owner_bindings(marker, ledger)
    exact_docker_absent("container", CONTAINER_A_ID)
    exact_docker_absent("container", CONTAINER_A_NAME)
    exact_docker_absent("network", NETWORK_ID)
    exact_docker_absent("network", NETWORK_NAME)
    root_names = bounded_names(ROOT, 4, "root")
    if root_names != {"owner.json", "resource-ledger.json", "private", "data"}:
        raise SafeStop("root_entries_do_not_match_exact_plan")
    private = ROOT / "private"
    private_st = private.lstat()
    if (private.is_symlink() or not stat.S_ISDIR(private_st.st_mode)
            or (private_st.st_uid, private_st.st_gid, stat.S_IMODE(private_st.st_mode)) != (65534, 65534, 0o700)
            or private.resolve() != ROOT.resolve() / "private"):
        raise SafeStop("private_directory_identity_mismatch")
    if bounded_names(private, 1, "private") != {"agent.yaml"}:
        raise SafeStop("private_directory_entries_mismatch")
    config = private / "agent.yaml"
    config_st = config.lstat()
    if (config.is_symlink() or not stat.S_ISREG(config_st.st_mode)
            or (config_st.st_uid, config_st.st_gid, stat.S_IMODE(config_st.st_mode)) != (65534, 65534, 0o600)
            or config.resolve() != private.resolve() / "agent.yaml"):
        raise SafeStop("private_config_metadata_mismatch")
    config_fd = os.open(config, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        config_open = os.fstat(config_fd)
        if (config_open.st_dev, config_open.st_ino, config_open.st_uid, config_open.st_gid,
                stat.S_IMODE(config_open.st_mode)) != (config_st.st_dev, config_st.st_ino, 65534, 65534, 0o600):
            raise SafeStop("private_config_open_identity_changed")
    finally: os.close(config_fd)
    data = ROOT / "data"
    data_st = data.lstat()
    if (data.is_symlink() or not stat.S_ISDIR(data_st.st_mode)
            or (data_st.st_uid, data_st.st_gid, stat.S_IMODE(data_st.st_mode)) != (65534, 65534, 0o700)
            or data_st.st_dev != ROOT_DEV or data.resolve() != ROOT.resolve() / "data"):
        raise SafeStop("data_directory_identity_mismatch")
    expected_names = set(APPROVED_EMPTY_DIRS) | {"state.db"}
    data_names = bounded_names(data, len(expected_names), "data")
    if data_names != expected_names:
        raise SafeStop("data_entries_changed_since_readonly_inventory")
    dirs = {}
    metadata = []
    for name, inode in APPROVED_EMPTY_DIRS.items():
        path = data / name
        st = validate_empty_directory(path, ROOT_DEV, inode, 65534, 65534, 0o755)
        dirs[name] = (path, st.st_dev, st.st_ino)
        metadata.append(entry_metadata(path))
    db = data / "state.db"
    db_st = db.lstat()
    if (db.is_symlink() or not stat.S_ISREG(db_st.st_mode)
            or (db_st.st_dev, db_st.st_ino, db_st.st_uid, db_st.st_gid,
                stat.S_IMODE(db_st.st_mode), db_st.st_size)
            != (ROOT_DEV, STATE_DB_INODE, 65534, 65534, 0o644, STATE_DB_SIZE)):
        raise SafeStop("state_db_metadata_pin_mismatch")
    db_fd = os.open(db, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        db_open = os.fstat(db_fd)
        if ((db_open.st_dev, db_open.st_ino, db_open.st_uid, db_open.st_gid,
             stat.S_IMODE(db_open.st_mode), db_open.st_size)
                != (ROOT_DEV, STATE_DB_INODE, 65534, 65534, 0o644, STATE_DB_SIZE)):
            raise SafeStop("state_db_open_identity_mismatch")
    finally: os.close(db_fd)
    metadata.append(entry_metadata(db))
    return marker, ledger, root_st, dirs, (data, data_st.st_dev, data_st.st_ino), (db, db_st.st_dev, db_st.st_ino), (config, private, private_st.st_dev, private_st.st_ino), metadata


def cleanup(node_http_code):
    marker, ledger, root_st, dirs, data_info, db_info, config_info, metadata = inspect_and_preflight(node_http_code)
    removed_dirs = []
    removed_files = []
    try:
        for name in ("scripts", "artifacts", "applications", "executions", "logs", "tmp"):
            path, dev, ino = dirs[name]
            current = path.lstat()
            if (path.is_symlink() or not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino, current.st_uid, current.st_gid,
                        stat.S_IMODE(current.st_mode)) != (dev, ino, 65534, 65534, 0o755)):
                raise SafeStop("empty_directory_changed_before_rmdir")
            with os.scandir(path) as entries:
                if next(entries, None) is not None:
                    raise SafeStop("empty_directory_gained_entry_before_rmdir")
            path.rmdir()
            removed_dirs.append(name)
        db, dbdev, dbino = db_info
        current = db.lstat()
        if (db.is_symlink() or not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino, current.st_uid, current.st_gid,
                    stat.S_IMODE(current.st_mode), current.st_size)
                != (dbdev, dbino, 65534, 65534, 0o644, STATE_DB_SIZE)):
            raise SafeStop("state_db_changed_before_unlink")
        db.unlink()
        removed_files.append("state.db")
        data, datadev, dataino = data_info
        dst = data.lstat()
        if (data.is_symlink() or not stat.S_ISDIR(dst.st_mode)
                or (dst.st_dev, dst.st_ino, dst.st_uid, dst.st_gid, stat.S_IMODE(dst.st_mode))
                != (datadev, dataino, 65534, 65534, 0o700)):
            raise SafeStop("data_directory_changed_before_rmdir")
        if bounded_names(data, 1, "data_after_cleanup"): raise SafeStop("data_directory_nonempty_after_exact_cleanup")
        data.rmdir()
        removed_dirs.append("data")
        config, private, privdev, privino = config_info
        cst = config.lstat()
        private_now = private.lstat()
        if (private.is_symlink() or not stat.S_ISDIR(private_now.st_mode)
                or (private_now.st_dev, private_now.st_ino, private_now.st_uid, private_now.st_gid,
                    stat.S_IMODE(private_now.st_mode)) != (privdev, privino, 65534, 65534, 0o700)
                or config.resolve() != private.resolve() / "agent.yaml"
                or config.is_symlink() or not stat.S_ISREG(cst.st_mode)
                or (cst.st_uid, cst.st_gid, stat.S_IMODE(cst.st_mode)) != (65534, 65534, 0o600)):
            raise SafeStop("private_config_changed_before_unlink")
        config.unlink()
        removed_files.append("private/agent.yaml")
        pst = private.lstat()
        if (private.is_symlink() or not stat.S_ISDIR(pst.st_mode)
                or (pst.st_dev, pst.st_ino, pst.st_uid, pst.st_gid, stat.S_IMODE(pst.st_mode))
                != (privdev, privino, 65534, 65534, 0o700)):
            raise SafeStop("private_directory_changed_before_rmdir")
        if bounded_names(private, 1, "private_after_config_unlink"): raise SafeStop("private_directory_nonempty_after_config_unlink")
        private.rmdir()
        removed_dirs.append("private")
        # Persist restartable final state only after all private/data children are gone.
        marker["children_removed"] = True
        ledger["phase"] = "ROOT_CHILDREN_REMOVED"
        atomic_json(ROOT / "owner.json", marker, 0, 0, 0o600)
        atomic_json(ROOT / "resource-ledger.json", ledger, 0, 0, 0o600)
        owner_path = ROOT / "owner.json"
        ledger_path = ROOT / "resource-ledger.json"
        read_owned_json(owner_path, 65536, 0, 0, 0o600)
        read_owned_json(ledger_path, MAX_JSON, 0, 0, 0o600)
        ledger_path.unlink()
        removed_files.append("resource-ledger.json")
        read_owned_json(owner_path, 65536, 0, 0, 0o600)
        owner_path.unlink()
        removed_files.append("owner.json")
        if bounded_names(ROOT, 1, "root_after_metadata_cleanup"): raise SafeStop("root_not_empty_after_exact_metadata_cleanup")
        ROOT.rmdir()
        if ROOT.exists() or ROOT.is_symlink(): raise SafeStop("exact_root_still_present")
        return {"status": "EXACT_OWNED_ROOT_REMOVED", "credentials_emitted": False,
                "node_id": NODE_ID, "node_get_http_external": 404,
                "root_dev": ROOT_DEV, "root_inode": ROOT_INODE,
                "data_entries_preflight": metadata, "removed_empty_directories": removed_dirs,
                "removed_exact_files": removed_files, "mutations": "exact-owned-empty-path-cleanup"}
    except SafeStop as exc:
        return {"status": "SAFE_STOP", "credentials_emitted": False,
                "safe_error": str(exc), "root_dev": ROOT_DEV, "root_inode": ROOT_INODE,
                "removed_empty_directories": removed_dirs, "removed_exact_files": removed_files,
                "unknown_entries_removed": False}
    except Exception as exc:
        return {"status": "SAFE_STOP", "credentials_emitted": False,
                "safe_error": "cleanup_exception_" + type(exc).__name__, "root_dev": ROOT_DEV,
                "root_inode": ROOT_INODE, "removed_empty_directories": removed_dirs,
                "removed_exact_files": removed_files, "unknown_entries_removed": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("final-cleanup",), required=True)
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--shell-sha256", required=True)
    ap.add_argument("--node-id", required=True)
    ap.add_argument("--node-http-code", type=int, required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()
    if (not args.execute or args.nonce != NONCE or args.shell_sha256 != SHELL_SHA
            or args.node_id != NODE_ID):
        raise SafeStop("exact_final_cleanup_invocation_required")
    print(json.dumps(cleanup(args.node_http_code), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except SafeStop as exc:
        print(json.dumps({"status": "SAFE_STOP", "credentials_emitted": False,
                          "safe_error": str(exc), "removed_unknown_entries": False}, sort_keys=True))
        raise SystemExit(0)
    except Exception as exc:
        print(json.dumps({"status": "SAFE_STOP", "credentials_emitted": False,
                          "safe_error": "unexpected_" + type(exc).__name__,
                          "removed_unknown_entries": False}, sort_keys=True))
        raise SystemExit(0)
