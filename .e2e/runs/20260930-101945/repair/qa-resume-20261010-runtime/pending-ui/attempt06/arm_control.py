#!/usr/bin/env python3
"""Guarded owner-side arming helper. Source only until Root approves execution."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


NODE = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
AGENT = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
CHILD_EXE_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
PROXY_EXE_SHA = "078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531"
UNIT = "nodesteer-agent-repair-20260930-101945-native-4.service"
UNIT_MAIN_PID = 32487


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def process(pid: int) -> dict:
    p = Path("/proc") / str(pid)
    raw = (p / "stat").read_text()
    fields = raw[raw.rfind(")") + 2 :].split()
    uid_line = next(line for line in (p / "status").read_text().splitlines() if line.startswith("Uid:"))
    uids = tuple(map(int, uid_line.split()[1:5]))
    exe_path = os.readlink(p / "exe")
    argv = [part.decode("utf-8", "replace") for part in (p / "cmdline").read_bytes().split(b"\0") if part]
    return {
        "pid": pid,
        "startticks": int(fields[19]),
        "uid": uids[0],
        "all_uids": uids,
        "exe_deleted": exe_path.endswith(" (deleted)"),
        "exe_sha256": sha_file(p / "exe"),
        "argv": argv,
        "cgroup": (p / "cgroup").read_text().strip(),
    }


def verify_process(pid: int, ticks: int, uid: int, exe_sha: str, root: Path, cgroup: str, config_path: str | None = None) -> dict:
    p = process(pid)
    if (p["startticks"], p["uid"], p["exe_sha256"], p["cgroup"]) != (ticks, uid, exe_sha, cgroup):
        raise RuntimeError("process_fingerprint_or_cgroup_mismatch")
    if p["all_uids"] != (uid, uid, uid, uid) or p["exe_deleted"]:
        raise RuntimeError("process_uid_or_deleted_executable_guard_failed")
    if not any(arg == str(root) or arg.startswith(str(root) + os.sep) for arg in p["argv"]):
        raise RuntimeError("process_argv_not_owned_root")
    if config_path is not None and not any(p["argv"][i] == "--config" and p["argv"][i + 1] == config_path for i in range(len(p["argv"]) - 1)) and not any(arg == "--config=" + config_path for arg in p["argv"]):
        raise RuntimeError("child_process_config_argument_mismatch")
    return p


def unit_properties() -> dict:
    result = subprocess.run(
        ["systemctl", "show", UNIT, "--no-pager", "--property=Id,ActiveState,MainPID,ControlGroup"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("parent_unit_read_failed")
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if props.get("Id") != UNIT or props.get("ActiveState") != "active" or props.get("MainPID") != str(UNIT_MAIN_PID):
        raise RuntimeError("parent_unit_identity_or_health_mismatch")
    return props


def child_db_guard(db_path: Path, args) -> dict:
    db = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        quick = db.execute("PRAGMA quick_check").fetchone()[0]
        identity = {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')")}
        rev_row = db.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()
        task_rows = list(db.execute("SELECT id,definition_json,revision,enabled FROM tasks ORDER BY id"))
        schedule_rows = list(db.execute("SELECT id,definition_json,revision,enabled FROM schedules ORDER BY id"))
        active = list(db.execute("SELECT id,task_id,node_id,status FROM executions WHERE node_id=? AND status IN ('PENDING','RUNNING')", (NODE,)))
        if quick != "ok" or identity.get("node_id") != NODE or identity.get("agent_id") != AGENT or rev_row is None:
            raise RuntimeError("child_database_identity_or_integrity_guard_failed")
        if len(task_rows) != 1 or task_rows[0]["id"] != args.task_id or not bool(task_rows[0]["enabled"]):
            raise RuntimeError("child_database_owned_task_cardinality_mismatch")
        definition = json.loads(task_rows[0]["definition_json"])
        expected_command = "sleep 4; printf '%s\\n' '" + args.marker + "'"
        if definition.get("type") != "command" or definition.get("command") != expected_command or len(schedule_rows) != 0 or active:
            raise RuntimeError("child_database_task_schedule_or_inflight_guard_failed")
        return {
            "quick_check": quick,
            "node_id": identity["node_id"],
            "agent_id": identity["agent_id"],
            "local_global_revision": int(rev_row["global_revision"]),
            "task_ids": [row["id"] for row in task_rows],
            "owned_task_revision": int(task_rows[0]["revision"]),
            "schedule_count": len(schedule_rows),
            "pending_or_running_execution_count": len(active),
            "database_open_mode": "read-only",
            "sql_write_or_lock": False,
        }
    finally:
        db.close()


def atomic_write(path: Path, expected_before: bytes, content: bytes, uid: int, gid: int) -> None:
    if path.read_bytes() != expected_before:
        raise RuntimeError("controls_changed_before_write")
    tmp = path.with_name(path.name + ".qa-pending-ui-" + str(os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(tmp, uid, gid)
        os.chmod(tmp, 0o600)
        if path.read_bytes() != expected_before:
            raise RuntimeError("controls_changed_during_write")
        os.replace(tmp, path)
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_unique_json(path: Path, value: dict, uid: int, gid: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(path, uid, gid)
    os.chmod(path, 0o600)
    dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def ensure_watchdog_source(root: Path, source: bytes, source_sha256: str, uid: int, gid: int) -> tuple[Path, bool]:
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256) or sha_bytes(source) != source_sha256:
        raise RuntimeError("watchdog_source_sha_invalid")
    path = root / ("qa-pending-ui-watchdog-" + source_sha256 + ".py")
    if path.exists() or path.is_symlink():
        st = path.lstat()
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) != (uid, gid, 0o700) or sha_file(path) != source_sha256:
            raise RuntimeError("watchdog_destination_identity_or_sha_mismatch")
        return path, True
    with path.open("xb") as dst:
        dst.write(source)
        dst.flush()
        os.fsync(dst.fileno())
    os.chown(path, uid, gid)
    os.chmod(path, 0o700)
    return path, False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-reviewed-arm", action="store_true")
    parser.add_argument("--hold-nonce", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--root-dev", type=int, required=True)
    parser.add_argument("--root-inode", type=int, required=True)
    parser.add_argument("--root-uid", type=int, required=True)
    parser.add_argument("--root-gid", type=int, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--db-dev", type=int, required=True)
    parser.add_argument("--db-inode", type=int, required=True)
    parser.add_argument("--child-pid", type=int, required=True)
    parser.add_argument("--child-startticks", type=int, required=True)
    parser.add_argument("--child-uid", type=int, required=True)
    parser.add_argument("--child-cgroup", required=True)
    parser.add_argument("--proxy-pid", type=int, required=True)
    parser.add_argument("--proxy-startticks", type=int, required=True)
    parser.add_argument("--proxy-uid", type=int, required=True)
    parser.add_argument("--proxy-cgroup", required=True)
    parser.add_argument("--watchdog-source-b64", required=True)
    parser.add_argument("--watchdog-source-sha256", required=True)
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    if not args.execute_reviewed_arm:
        raise SystemExit("refusing_to_arm_without_explicit_execute_reviewed_arm")
    if not re.fullmatch(r"[0-9a-f]{12,32}", args.hold_nonce) or not re.fullmatch(r"[0-9a-f-]{36}", args.task_id):
        raise SystemExit("arm_nonce_or_task_id_format_invalid")
    if not re.fullmatch(r"QA_PENDING_UI_[0-9a-f]{12}", args.marker) or not args.task_name.endswith(args.marker[-12:]):
        raise SystemExit("arm_task_marker_binding_failed")
    root = Path(args.root)
    evidence_path = Path(args.evidence)
    if evidence_path.parent != root:
        raise SystemExit("arm_evidence_must_be_inside_exact_owned_root")
    try:
        if stat.S_ISLNK(root.lstat().st_mode):
            raise SystemExit("arm_root_symlink_rejected")
    except FileNotFoundError:
        raise SystemExit("arm_root_missing")
    evidence = {"status": "ARM_GUARD_STARTED", "node_id": NODE, "agent_id": AGENT, "task_id": args.task_id, "hold_nonce": args.hold_nonce}
    root_verified = False
    manifest_path = None
    lock_fd = None
    try:
        if os.geteuid() != 0:
            raise RuntimeError("owner_helper_must_run_as_root")
        if evidence_path.parent != root or evidence_path.exists() or evidence_path.is_symlink():
            raise RuntimeError("arm_evidence_path_not_unique_owned_root_file")
        rst = root.lstat()
        if stat.S_ISLNK(rst.st_mode) or root.resolve() != root or (rst.st_dev, rst.st_ino, rst.st_uid, rst.st_gid, stat.S_IMODE(rst.st_mode)) != (args.root_dev, args.root_inode, args.root_uid, args.root_gid, 0o700):
            raise RuntimeError("owned_root_identity_mismatch")
        root_verified = True
        manifest_path = root / "owner-manifest.json"
        manifest_stat = manifest_path.lstat()
        if stat.S_ISLNK(manifest_stat.st_mode) or (manifest_stat.st_uid, manifest_stat.st_gid, stat.S_IMODE(manifest_stat.st_mode)) != (args.root_uid, args.root_gid, 0o600) or sha_file(manifest_path) != args.manifest_sha256:
            raise RuntimeError("owner_manifest_sha_mismatch")
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get("node_id"), manifest.get("agent_id")) != (NODE, AGENT) or (manifest.get("directory_dev"), manifest.get("directory_ino"), manifest.get("directory_uid")) != (args.root_dev, args.root_inode, args.root_uid):
            raise RuntimeError("owner_manifest_exact_identity_mismatch")
        marker_path = root / "owner.marker"
        marker_stat = marker_path.lstat()
        owner_marker = manifest.get("owner_marker")
        if marker_path.parent != root or marker_path.is_symlink() or not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_uid != args.root_uid or not isinstance(owner_marker, str) or not owner_marker or marker_path.read_text(encoding="utf-8") != owner_marker:
            raise RuntimeError("owner_marker_identity_mismatch")
        config_path = Path(args.config_path)
        if config_path.is_symlink() or not config_path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError("config_path_outside_owned_root")
        cfgst = config_path.stat()
        if (cfgst.st_uid, cfgst.st_gid, stat.S_IMODE(cfgst.st_mode)) != (args.root_uid, args.root_gid, 0o600) or sha_file(config_path) != args.config_sha256:
            raise RuntimeError("config_owner_mode_or_sha_mismatch")
        db = root / "data" / "state.db"
        if (root / "data").is_symlink() or db.is_symlink():
            raise RuntimeError("owned_db_path_symlink_rejected")
        dbst = db.lstat()
        if (dbst.st_dev, dbst.st_ino, dbst.st_uid, dbst.st_gid, stat.S_IMODE(dbst.st_mode)) != (args.db_dev, args.db_inode, args.root_uid, args.root_gid, 0o644):
            raise RuntimeError("owned_db_identity_mismatch")
        unit = unit_properties()
        parent_cgroup = "0::" + unit.get("ControlGroup", "")
        if args.child_cgroup != parent_cgroup or args.proxy_cgroup != parent_cgroup:
            raise RuntimeError("processes_not_in_exact_parent_cgroup")
        child = verify_process(args.child_pid, args.child_startticks, args.child_uid, CHILD_EXE_SHA, root, parent_cgroup, args.config_path)
        proxy = verify_process(args.proxy_pid, args.proxy_startticks, args.proxy_uid, PROXY_EXE_SHA, root, parent_cgroup)
        controls = root / "controls.json"
        if controls.is_symlink():
            raise RuntimeError("controls_symlink_rejected")
        cst = controls.stat()
        if (cst.st_uid, cst.st_gid, stat.S_IMODE(cst.st_mode)) != (args.root_uid, args.root_gid, 0o600):
            raise RuntimeError("controls_owner_or_mode_mismatch")
        before = controls.read_bytes()
        if json.loads(before) != {}:
            raise RuntimeError("controls_not_empty")

        watchdog_source = base64.b64decode(args.watchdog_source_b64, validate=True)
        if sha_bytes(watchdog_source) != args.watchdog_source_sha256:
            raise RuntimeError("watchdog_source_sha_mismatch")
        watchdog_dst, watchdog_source_reused = ensure_watchdog_source(root, watchdog_source, args.watchdog_source_sha256, args.root_uid, args.root_gid)
        evidence["watchdog_source_reused"] = watchdog_source_reused

        ready = root / ("watchdog-ready-" + args.hold_nonce + ".json")
        lock_path = root / ("watchdog-lock-" + args.hold_nonce + ".lock")
        arm_state = root / ("watchdog-arm-" + args.hold_nonce + ".json")
        watchdog_evidence = root / ("watchdog-result-" + args.hold_nonce + ".json")
        if any(path.exists() or path.is_symlink() for path in (ready, lock_path, arm_state, watchdog_evidence)):
            raise RuntimeError("watchdog_evidence_path_already_exists")
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.chown(lock_path, args.root_uid, args.root_gid)
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        def drop_privileges():
            os.setgroups([])
            os.setgid(args.root_gid)
            os.setuid(args.root_uid)
        watchdog_argv = [
            sys.executable, str(watchdog_dst),
            "--root", str(root), "--root-dev", str(args.root_dev), "--root-inode", str(args.root_inode), "--root-uid", str(args.root_uid), "--root-gid", str(args.root_gid),
            "--node-id", NODE, "--agent-id", AGENT, "--manifest-sha256", args.manifest_sha256,
            "--task-id", args.task_id, "--hold-nonce", args.hold_nonce, "--lock-file", str(lock_path), "--arm-state", str(arm_state), "--ready-file", str(ready), "--evidence", str(watchdog_evidence),
        ]
        watcher = subprocess.Popen(watchdog_argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True, preexec_fn=drop_privileges)
        evidence["watchdog_pid"] = watcher.pid
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not ready.exists():
            if watcher.poll() is not None:
                raise RuntimeError("watchdog_exited_before_ready")
            time.sleep(0.025)
        if not ready.exists():
            raise RuntimeError("watchdog_ready_timeout")
        ready_data = json.loads(ready.read_text())
        if ready_data.get("status") != "WATCHDOG_READY" or ready_data.get("task_id") != args.task_id or ready_data.get("hold_nonce") != args.hold_nonce or ready_data.get("watchdog_pid") != watcher.pid or watcher.poll() is not None:
            raise RuntimeError("watchdog_ready_identity_mismatch")
        # Refresh every ownership and Hub-state guard immediately before the control write.
        rst2 = root.lstat()
        if (rst2.st_dev, rst2.st_ino, rst2.st_uid, rst2.st_gid, stat.S_IMODE(rst2.st_mode)) != (args.root_dev, args.root_inode, args.root_uid, args.root_gid, 0o700):
            raise RuntimeError("owned_root_changed_before_arm")
        if sha_file(manifest_path) != args.manifest_sha256 or sha_file(config_path) != args.config_sha256:
            raise RuntimeError("owner_or_config_sha_changed_before_arm")
        dbst2 = db.stat()
        if db.is_symlink() or (dbst2.st_dev, dbst2.st_ino, dbst2.st_uid, dbst2.st_gid, stat.S_IMODE(dbst2.st_mode)) != (args.db_dev, args.db_inode, args.root_uid, args.root_gid, 0o644):
            raise RuntimeError("owned_db_changed_before_arm")
        unit2 = unit_properties()
        if unit2.get("MainPID") != str(UNIT_MAIN_PID) or unit2.get("ControlGroup") != unit.get("ControlGroup"):
            raise RuntimeError("parent_unit_changed_before_arm")
        child = verify_process(args.child_pid, args.child_startticks, args.child_uid, CHILD_EXE_SHA, root, parent_cgroup, args.config_path)
        proxy = verify_process(args.proxy_pid, args.proxy_startticks, args.proxy_uid, PROXY_EXE_SHA, root, parent_cgroup)
        evidence["child_db_oracle"] = child_db_guard(db, args)
        if controls.read_bytes() != before:
            raise RuntimeError("controls_changed_before_arm")
        control_value = {"hold_types": ["RUN_EXECUTION"], "qa_hold_nonce": args.hold_nonce, "qa_hold_task_id": args.task_id}
        arm_start_monotonic = time.monotonic()
        deadline_monotonic = arm_start_monotonic + 15.0
        atomic_write(controls, before, json.dumps(control_value, separators=(",", ":")).encode() + b"\n", cst.st_uid, cst.st_gid)
        readback = json.loads(controls.read_text())
        if readback != control_value:
            raise RuntimeError("hold_control_readback_mismatch")
        armed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        write_unique_json(arm_state, {"task_id": args.task_id, "hold_nonce": args.hold_nonce, "armed_at_utc": armed_at_utc, "deadline_monotonic": deadline_monotonic, "duration_seconds": 15}, args.root_uid, args.root_gid)
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
        lock_fd = None
        evidence.update({
            "status": "HOLD_ARMED",
            "armed_at_utc": armed_at_utc,
            "control_keys": sorted(readback),
            "controls_sha256": sha_file(controls),
            "watchdog_started_before_controls_write": True,
            "watchdog_ready_before_controls_write": True,
            "watchdog_wait_seconds": 15,
            "watchdog_deadline_monotonic": deadline_monotonic,
            "child_process": {"pid": child["pid"], "startticks": child["startticks"], "uid": child["uid"], "exe_sha256": child["exe_sha256"]},
            "proxy_process": {"pid": proxy["pid"], "startticks": proxy["startticks"], "uid": proxy["uid"], "exe_sha256": proxy["exe_sha256"]},
            "parent_unit": {"id": unit.get("Id"), "main_pid": int(unit["MainPID"]), "control_group": unit.get("ControlGroup"), "active_state": unit.get("ActiveState")},
            "task_scope": "one exact child Task; no signal, proxy restart, Unit/cgroup operation, or other control changed",
        })
    except Exception as exc:
        evidence["status"] = "ARM_GUARD_FAILED"
        evidence["safe_error"] = str(exc)[:120]
    finally:
        try:
            if lock_fd is not None:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        except OSError:
            pass
    if root_verified and manifest_path is not None:
        try:
            final_root_stat = root.lstat()
            if (final_root_stat.st_dev, final_root_stat.st_ino, final_root_stat.st_uid, final_root_stat.st_gid, stat.S_IMODE(final_root_stat.st_mode)) != (args.root_dev, args.root_inode, args.root_uid, args.root_gid, 0o700) or sha_file(manifest_path) != args.manifest_sha256:
                raise RuntimeError("owned_root_changed_before_evidence_write")
            write_unique_json(evidence_path, evidence, args.root_uid, args.root_gid)
        except Exception:
            evidence["evidence_file_written"] = False
    print(json.dumps(evidence, ensure_ascii=False), flush=True)
    return 0 if evidence["status"] == "HOLD_ARMED" else 2


if __name__ == "__main__":
    sys.exit(main())
