#!/usr/bin/env python3
"""Read-only native-4 (192.168.100.247) owner + agent state.db probe.

Runs as a real NodeSteer Script task on native-4. Fail-closed. It never emits
config contents, the full environment, or credential material; only counts and
the exact fixture/execution rows requested by the caller.
"""
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path
from urllib.parse import quote

NODE_ID = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
AGENT_ID = "agent-bbd350f8-69f9-4406-923c-4f9c335e6962"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
UNIT = "nodesteer-agent-repair-20260930-101945-native-4.service"
CGROUP = "/system.slice/" + UNIT
CONFIG_PATH = "/etc/nodesteer/repair-20260930-101945/native-4.yaml"


class ProbeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def proc_record(pid):
    proc = Path("/proc") / str(pid)
    raw = (proc / "stat").read_text(encoding="ascii")
    close = raw.rfind(")")
    if close < 0:
        raise ProbeError("proc_stat_shape_invalid")
    tail = raw[close + 2:].split()
    uid_line = next(
        (line for line in (proc / "status").read_text(encoding="ascii").splitlines()
         if line.startswith("Uid:")), None)
    if len(tail) < 20 or uid_line is None:
        raise ProbeError("proc_identity_missing")
    argv = [part.decode("utf-8", "replace") for part in
            (proc / "cmdline").read_bytes().split(b"\0") if part]
    exe = os.readlink(proc / "exe")
    return {
        "pid": int(pid), "ppid": int(tail[1]), "startticks": int(tail[19]),
        "uid": int(uid_line.split()[1]), "exe_path": exe,
        "exe_name": Path(exe.removesuffix(" (deleted)")).name,
        "exe_sha": file_sha256(proc / "exe"), "argv": argv,
        "cgroup_lines": (proc / "cgroup").read_text(encoding="ascii").splitlines(),
    }


def exact_config_argv(argv):
    paths = []
    for index, value in enumerate(argv):
        if value in ("--config", "-config", "--config-file") and index + 1 < len(argv):
            paths.append(argv[index + 1])
        elif value.startswith("--config="):
            paths.append(value.split("=", 1)[1])
    return paths == [CONFIG_PATH]


def guard_agent_ancestor():
    current = os.getppid()
    matches = []
    for _ in range(32):
        if current <= 1:
            break
        try:
            process = proc_record(current)
        except (OSError, ValueError, ProbeError, ProcessLookupError, PermissionError):
            break
        if (process["exe_name"] == "nodesteer-agent" and process["uid"] == 0
                and process["exe_sha"] == AGENT_SHA
                and f"0::{CGROUP}" in process["cgroup_lines"]
                and exact_config_argv(process["argv"])):
            matches.append(process)
        current = process["ppid"]
    if len(matches) != 1:
        raise ProbeError("unique_exact_native4_agent_ancestor_not_found")
    agent = matches[0]
    if " (deleted)" in agent["exe_path"]:
        raise ProbeError("agent_binary_deleted_path")
    disk = Path(agent["exe_path"])
    if not disk.is_file() or file_sha256(disk) != AGENT_SHA:
        raise ProbeError("agent_disk_sha_mismatch")
    cp = subprocess.run(
        ["systemctl", "show", "--no-pager",
         "--property=Id,ActiveState,MainPID,ControlGroup", UNIT],
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        capture_output=True, text=True, timeout=6, check=False)
    props = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    if (cp.returncode != 0 or props.get("Id") != UNIT
            or props.get("ActiveState") != "active"
            or props.get("MainPID") != str(agent["pid"])
            or props.get("ControlGroup") != CGROUP):
        raise ProbeError("native4_unit_mainpid_cgroup_guard_failed")
    return {
        "pid": agent["pid"], "startticks": agent["startticks"], "uid": agent["uid"],
        "proc_exe_sha": agent["exe_sha"], "disk_exe_sha": AGENT_SHA,
        "unit_mainpid_match": True, "unit_cgroup_match": True,
        "config_path_exact_match": True,
        "raw_argv_or_config_contents_emitted": False,
    }


def state_db_path(agent_pid):
    fd_dir = Path("/proc") / str(agent_pid) / "fd"
    found = {}
    for entry in fd_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            target = os.readlink(entry)
            opened = os.stat(entry)
        except OSError:
            continue
        if target.endswith("/state.db") and stat.S_ISREG(opened.st_mode):
            found[(target, opened.st_dev, opened.st_ino)] = opened
    if len(found) != 1:
        raise ProbeError("agent_state_db_fd_missing_or_ambiguous")
    (target, device, inode), opened = next(iter(found.items()))
    db_path = Path(target)
    before = db_path.lstat()
    if db_path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise ProbeError("agent_state_db_path_type_guard_failed")
    if (before.st_dev, before.st_ino) != (device, inode):
        raise ProbeError("agent_state_db_fd_path_identity_mismatch")
    return db_path, before


def open_readonly_db(path):
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=3)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise ProbeError("sqlite_query_only_not_enabled")
    if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        connection.close()
        raise ProbeError("agent_state_db_quick_check_failed")
    return connection


def inspect(connection, phase, params):
    identity = {row["key"]: row["value"] for row in connection.execute(
        "SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')")}
    if identity != {"node_id": NODE_ID, "agent_id": AGENT_ID}:
        raise ProbeError("agent_state_db_identity_mismatch")
    revision = int(connection.execute(
        "SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0])
    expected = int(params["expected_hub_revision"])
    if revision < expected:
        raise ProbeError("agent_local_revision_behind_hub_revision")
    script_id, task_id, execution_id = params["script_id"], params["task_id"], params["execution_id"]
    rows = {t: int(connection.execute(
        "SELECT count(*) FROM %s WHERE id=?" % t, (i,)).fetchone()[0])
        for t, i in (("scripts", script_id), ("tasks", task_id))}
    history = None
    row = connection.execute(
        "SELECT id,task_id,script_id,node_id,status,exit_code,trigger_type,start_time,end_time "
        "FROM executions WHERE id=?", (execution_id,)).fetchone() if execution_id else None
    if row is not None:
        history = {"id_matches": row["id"] == execution_id,
                   "task_id": row["task_id"], "task_id_matches": row["task_id"] == task_id,
                   "script_id": row["script_id"], "script_id_matches": row["script_id"] == script_id,
                   "node_id_matches": row["node_id"] == NODE_ID,
                   "status": row["status"], "exit_code": row["exit_code"],
                   "trigger_type": row["trigger_type"],
                   "start_time": row["start_time"], "end_time": row["end_time"]}
    if phase == "pre":
        if rows != {"scripts": 1, "tasks": 1}:
            raise ProbeError("fixture_definition_rows_not_local_before_delete")
        if history is None or not history["id_matches"] or not history["task_id_matches"] \
                or not history["script_id_matches"] or not history["node_id_matches"]:
            raise ProbeError("execution_history_missing_before_delete")
        if history["status"] != "SUCCESS":
            raise ProbeError("execution_not_success_before_delete")
        return {"phase": phase, "local_revision": revision, "hypothesis": "fixture present locally",
                "exact_definition_rows": rows, "execution_history": history,
                "fixture_definition_present_locally": True,
                "history_present": True}
    if phase == "post":
        if rows != {"scripts": 0, "tasks": 0}:
            raise ProbeError("exact_fixture_definition_rows_remain_after_delete")
        if history is None or not history["id_matches"] or not history["task_id_matches"] \
                or not history["script_id_matches"] or not history["node_id_matches"]:
            raise ProbeError("execution_history_not_preserved_after_delete")
        if history["status"] != "SUCCESS":
            raise ProbeError("execution_status_changed_after_delete")
        return {"phase": phase, "local_revision": revision,
                "hypothesis": "fixture definitions removed locally, history retained",
                "exact_definition_rows": rows, "execution_history": history,
                "fixture_definition_present_locally": False,
                "history_present": True}
    raise ProbeError("probe_phase_invalid")


def parameters():
    return {key: os.environ.get("NODESTEER_PARAM_" + key, "") for key in
            ("phase", "script_id", "task_id", "execution_id", "expected_hub_revision")}


def run():
    params = parameters()
    if params["phase"] not in ("pre", "post") or not params["expected_hub_revision"] \
            or not params["script_id"] or not params["task_id"] or not params["execution_id"]:
        raise ProbeError("probe_parameters_missing_or_invalid")
    agent = guard_agent_ancestor()
    db_path, db_meta = state_db_path(agent["pid"])
    before = db_path.lstat()
    connection = open_readonly_db(db_path)
    try:
        state = inspect(connection, params["phase"], params)
    finally:
        connection.close()
    after = db_path.lstat()
    if db_path.is_symlink() or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise ProbeError("agent_state_db_identity_changed_during_read")
    after_agent = guard_agent_ancestor()
    if (agent["pid"], agent["startticks"], agent["proc_exe_sha"], agent["uid"]) != (
            after_agent["pid"], after_agent["startticks"], after_agent["proc_exe_sha"],
            after_agent["uid"]):
        raise ProbeError("agent_identity_changed_during_probe")
    state.update({
        "status": "READONLY_PROBE_PASS", "node_id": NODE_ID, "agent_id": AGENT_ID,
        "expected_hub_revision": int(params["expected_hub_revision"]),
        "local_revision_at_or_after_hub": True,
        "agent_owner": agent,
        "database_identity": {"device": before.st_dev, "inode": before.st_ino,
                              "uid": before.st_uid, "gid": before.st_gid,
                              "mode": stat.S_IMODE(before.st_mode), "size": before.st_size,
                              "path_sha256": sha256_bytes(str(db_path).encode())},
        "quick_check": "ok", "query_only": True,
        "database_path_contents_emitted": False,
        "no_config_contents_or_full_environment_read": True,
    })
    return state


def main():
    try:
        out = run()
    except ProbeError as exc:
        out = {"status": "READONLY_PROBE_FAIL_CLOSED", "reason_code": exc.code,
               "node_id": NODE_ID, "agent_id": AGENT_ID,
               "no_config_contents_or_full_environment_read": True}
    except Exception:
        out = {"status": "READONLY_PROBE_FAIL_CLOSED",
               "reason_code": "unexpected_readonly_probe_error",
               "node_id": NODE_ID, "agent_id": AGENT_ID,
               "no_config_contents_or_full_environment_read": True}
    print(json.dumps(out, sort_keys=True, separators=(",", ":")), flush=True)
    return 0 if out["status"] == "READONLY_PROBE_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
