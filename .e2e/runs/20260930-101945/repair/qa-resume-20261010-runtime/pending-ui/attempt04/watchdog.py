#!/usr/bin/env python3
"""Bounded fail-safe release for the owned Native4 RUN_EXECUTION hold."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def root_guard(args) -> tuple[Path, Path]:
    root = Path(args.root)
    st = root.lstat()
    expected = (args.root_dev, args.root_inode, args.root_uid, args.root_gid, 0o700)
    actual = (st.st_dev, st.st_ino, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode))
    if stat.S_ISLNK(st.st_mode) or root.resolve() != root or actual != expected:
        raise RuntimeError("owned_root_fingerprint_mismatch")
    manifest_path = root / "owner-manifest.json"
    mst = manifest_path.lstat()
    if stat.S_ISLNK(mst.st_mode) or (mst.st_uid, mst.st_gid, stat.S_IMODE(mst.st_mode)) != (args.root_uid, args.root_gid, 0o600):
        raise RuntimeError("owner_manifest_guard_failed")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("node_id"), manifest.get("agent_id")) != (args.node_id, args.agent_id) or (manifest.get("directory_dev"), manifest.get("directory_ino"), manifest.get("directory_uid")) != (args.root_dev, args.root_inode, args.root_uid):
        raise RuntimeError("owner_manifest_identity_mismatch")
    marker_path = root / "owner.marker"
    marker_stat = marker_path.lstat()
    owner_marker = manifest.get("owner_marker")
    if marker_path.parent != root or marker_path.is_symlink() or not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_uid != args.root_uid or not isinstance(owner_marker, str) or not owner_marker or marker_path.read_text(encoding="utf-8") != owner_marker:
        raise RuntimeError("owner_marker_identity_mismatch")
    if args.manifest_sha256 and sha_file(manifest_path) != args.manifest_sha256:
        raise RuntimeError("owner_manifest_sha_mismatch")
    # Do not depend on the driver or either Agent process surviving. The release authority
    # is the exact nonce/task marker in this private controls file; owner/process identity
    # was verified by arm_control.py immediately before it wrote the hold.
    controls = root / "controls.json"
    if controls.is_symlink():
        raise RuntimeError("controls_symlink_rejected")
    cst = controls.stat()
    if (cst.st_uid, cst.st_gid, stat.S_IMODE(cst.st_mode)) != (args.root_uid, args.root_gid, 0o600):
        raise RuntimeError("controls_owner_mode_mismatch")
    return root, controls


def atomic_replace(controls: Path, before: bytes, value: dict, uid: int, gid: int) -> None:
    # Compare-and-swap prevents a stale watchdog from overwriting a newer actor's controls.
    if controls.read_bytes() != before:
        raise RuntimeError("controls_changed_before_release")
    temp = controls.with_name("controls.json.qa-watchdog-" + str(os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(value, separators=(",", ":")).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp, uid, gid)
        os.chmod(temp, 0o600)
        if controls.read_bytes() != before:
            temp.unlink(missing_ok=True)
            raise RuntimeError("controls_changed_during_release")
        os.replace(temp, controls)
        dfd = os.open(controls.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def write_private(path: Path, value: dict, uid: int, gid: int) -> None:
    temp = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp, uid, gid)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--root-dev", type=int, required=True)
    parser.add_argument("--root-inode", type=int, required=True)
    parser.add_argument("--root-uid", type=int, required=True)
    parser.add_argument("--root-gid", type=int, required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--hold-nonce", required=True)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--arm-state", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{12,32}", args.hold_nonce) or not re.fullmatch(r"[0-9a-f-]{36}", args.task_id):
        raise SystemExit("watchdog_nonce_or_task_id_format_invalid")
    root_arg = Path(args.root)
    lock_path = Path(args.lock_file)
    arm_state_path = Path(args.arm_state)
    if any(path.parent != root_arg for path in (Path(args.ready_file), Path(args.evidence), lock_path, arm_state_path)):
        raise SystemExit("watchdog_evidence_must_be_inside_exact_owned_root")
    try:
        if stat.S_ISLNK(root_arg.lstat().st_mode):
            raise SystemExit("watchdog_root_symlink_rejected")
    except FileNotFoundError:
        raise SystemExit("watchdog_root_missing")
    evidence = {"status": "WATCHDOG_STARTED", "task_id": args.task_id, "hold_nonce": args.hold_nonce}
    root_verified = False
    evidence_gid = None
    lock_fd = None
    try:
        root, controls = root_guard(args)
        root_verified = True
        evidence_gid = root.stat().st_gid
        ready = Path(args.ready_file)
        evidence_path = Path(args.evidence)
        if ready.exists() or ready.is_symlink() or evidence_path.exists() or evidence_path.is_symlink() or arm_state_path.is_symlink() or lock_path.is_symlink():
            raise RuntimeError("watchdog_evidence_path_exists")
        cst = controls.stat()
        lst = lock_path.lstat()
        if not stat.S_ISREG(lst.st_mode) or (lst.st_uid, lst.st_gid, stat.S_IMODE(lst.st_mode)) != (args.root_uid, args.root_gid, 0o600):
            raise RuntimeError("watchdog_lock_owner_mode_mismatch")
        write_private(ready, {"status": "WATCHDOG_READY", "task_id": args.task_id, "hold_nonce": args.hold_nonce, "watchdog_pid": os.getpid()}, cst.st_uid, cst.st_gid)
        lock_fd = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
        opened_lock = os.fstat(lock_fd)
        if (opened_lock.st_dev, opened_lock.st_ino, opened_lock.st_uid, stat.S_IMODE(opened_lock.st_mode)) != (lst.st_dev, lst.st_ino, args.root_uid, 0o600):
            raise RuntimeError("watchdog_lock_changed_during_open")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        root, controls = root_guard(args)
        before = controls.read_bytes()
        value = json.loads(before)
        if not isinstance(value, dict):
            raise RuntimeError("controls_not_object")
        if value.get("qa_hold_nonce") != args.hold_nonce or value.get("qa_hold_task_id") != args.task_id:
            evidence["status"] = "NOOP_CONTROL_OWNER_MISMATCH"
        else:
            hold_types = value.get("hold_types", [])
            if not isinstance(hold_types, list) or hold_types.count("RUN_EXECUTION") != 1:
                raise RuntimeError("owned_hold_missing_or_ambiguous")
            try:
                armed = json.loads(arm_state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                armed = {}
            valid_arm = armed.get("hold_nonce") == args.hold_nonce and armed.get("task_id") == args.task_id and isinstance(armed.get("deadline_monotonic"), (int, float))
            if valid_arm:
                remaining = float(armed["deadline_monotonic"]) - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
            else:
                evidence["safe_release_reason"] = "matching_hold_without_valid_arm_deadline"
            # The armer cannot write after watchdog expiry: both sides held the same flock.
            before = controls.read_bytes()
            value = json.loads(before)
            if value.get("qa_hold_nonce") != args.hold_nonce or value.get("qa_hold_task_id") != args.task_id:
                evidence["status"] = "NOOP_CONTROL_OWNER_MISMATCH"
            else:
                hold_types = value.get("hold_types", [])
                if not isinstance(hold_types, list) or hold_types.count("RUN_EXECUTION") != 1:
                    raise RuntimeError("owned_hold_changed_before_release")
                updated = dict(value)
                updated["hold_types"] = [kind for kind in hold_types if kind != "RUN_EXECUTION"]
                if not updated["hold_types"]:
                    updated.pop("hold_types")
                updated.pop("qa_hold_nonce", None)
                updated.pop("qa_hold_task_id", None)
                cst = controls.stat()
                atomic_replace(controls, before, updated, cst.st_uid, cst.st_gid)
                evidence["status"] = "OWNED_HOLD_RELEASED"
                evidence["other_control_keys_preserved"] = sorted(set(value) - {"hold_types", "qa_hold_nonce", "qa_hold_task_id"})
                evidence["controls_after_sha256"] = sha_file(controls)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        lock_fd = None
    except Exception as exc:
        # Keep the diagnostic bounded and non-sensitive; the Root fallback can inspect exact state.
        evidence["status"] = "WATCHDOG_GUARD_FAILED"
        evidence["safe_error"] = str(exc)[:120]
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
    evidence["completed_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if root_verified:
        try:
            _, controls = root_guard(args)
            cst = controls.stat()
            write_private(Path(args.evidence), evidence, cst.st_uid, cst.st_gid)
        except Exception:
            evidence["evidence_file_written"] = False
    print(json.dumps({k: evidence[k] for k in evidence if k not in {"hold_nonce", "task_id"}}, separators=(",", ":")), flush=True)
    return 0 if evidence["status"] in {"OWNED_HOLD_RELEASED", "NOOP_CONTROL_OWNER_MISMATCH"} else 2


if __name__ == "__main__":
    sys.exit(main())
