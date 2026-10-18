#!/usr/bin/env python3
"""Read-only Native3 owner/SQLite probe for the proposed SYS-09 schedule case."""
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
from pathlib import Path
from urllib.parse import quote

NODE_ID = "37989297-3bc8-4c0c-b302-7c6efbdba630"
AGENT_ID = "agent-bb005487-7d84-418c-b598-f93d8b0c86a0"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
UNIT = "nodesteer-agent-repair-20260930-101945-native-3.service"
CGROUP = "/system.slice/" + UNIT
CONFIG_PATH = "/etc/nodesteer/repair-20260930-101945/native-3.yaml"
PREFIX = "qa-sys09-native3-20261012-2709acec1825"
SCRIPT_NAME = PREFIX + "-script"
TASK_NAME = PREFIX + "-script-task"
PROBE_NAME = PREFIX + "-readonly-probe"


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
    tail = raw[close + 2 :].split()
    uid_line = next(
        (line for line in (proc / "status").read_text(encoding="ascii").splitlines()
         if line.startswith("Uid:")),
        None,
    )
    if len(tail) < 20 or uid_line is None:
        raise ProbeError("proc_identity_missing")
    argv = [part.decode("utf-8", "replace") for part in
            (proc / "cmdline").read_bytes().split(b"\0") if part]
    exe = os.readlink(proc / "exe")
    return {
        "pid": int(pid),
        "ppid": int(tail[1]),
        "startticks": int(tail[19]),
        "uid": int(uid_line.split()[1]),
        "exe_path": exe,
        "exe_name": Path(exe.removesuffix(" (deleted)")).name,
        "exe_sha": file_sha256(proc / "exe"),
        "argv": argv,
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
        raise ProbeError("unique_exact_native3_agent_ancestor_not_found")
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
        capture_output=True, text=True, timeout=6, check=False,
    )
    props = {}
    for line in cp.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    if (cp.returncode != 0 or props.get("Id") != UNIT
            or props.get("ActiveState") != "active"
            or props.get("MainPID") != str(agent["pid"])
            or props.get("ControlGroup") != CGROUP):
        raise ProbeError("native3_unit_mainpid_cgroup_guard_failed")
    # argv is used only to check the config path; it is never returned.
    return {
        "pid": agent["pid"],
        "startticks": agent["startticks"],
        "uid": agent["uid"],
        "proc_exe_sha": agent["exe_sha"],
        "disk_exe_sha": AGENT_SHA,
        "unit_mainpid_match": True,
        "unit_cgroup_match": True,
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


def identity_read(connection):
    return {row["key"]: row["value"] for row in connection.execute(
        "SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')"
    )}


def global_revision_read(connection):
    row = connection.execute(
        "SELECT global_revision FROM sync_state WHERE id=1"
    ).fetchone()
    if row is None:
        raise ProbeError("agent_sync_state_row_missing")
    return int(row[0])


def local_fixture_name_counts(connection):
    script_count = connection.execute(
        "SELECT count(*) FROM scripts WHERE name=?", (SCRIPT_NAME,)
    ).fetchone()[0]
    task_counts = {TASK_NAME: 0, PROBE_NAME: 0}
    for row in connection.execute("SELECT definition_json FROM tasks"):
        try:
            definition = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            raise ProbeError("local_task_definition_json_invalid")
        name = definition.get("name")
        if name in task_counts:
            task_counts[name] += 1
    return int(script_count), task_counts[TASK_NAME], task_counts[PROBE_NAME]


def fixture_rows(connection, script_id, task_id, schedule_id):
    values = {}
    for table, fixture_id in (("scripts", script_id), ("tasks", task_id),
                              ("schedules", schedule_id)):
        values[table] = int(connection.execute(
            f"SELECT count(*) FROM {table} WHERE id=?", (fixture_id,)
        ).fetchone()[0])
    return values


def scheduled_execution(connection, execution_id, task_id, script_id):
    row = connection.execute(
        "SELECT id,task_id,task_revision,script_id,script_revision,node_id,status,trigger_type,scheduled_time "
        "FROM executions WHERE id=?", (execution_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id_matches": row["id"] == execution_id,
        "task_id_matches": row["task_id"] == task_id,
        "task_revision": int(row["task_revision"]),
        "script_id_matches": row["script_id"] == script_id,
        "script_revision": int(row["script_revision"]),
        "node_id_matches": row["node_id"] == NODE_ID,
        "status": row["status"],
        "trigger_type": row["trigger_type"],
        "scheduled_time": row["scheduled_time"],
    }


def inspect_database(path, phase, params):
    before = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(before.st_mode):
        raise ProbeError("agent_state_db_path_changed_before_read")
    connection = open_readonly_db(path)
    try:
        identity = identity_read(connection)
        if identity != {"node_id": NODE_ID, "agent_id": AGENT_ID}:
            raise ProbeError("agent_state_db_identity_mismatch")
        revision = global_revision_read(connection)
        expected_revision = int(params["expected_hub_revision"])
        if phase == "pre":
            script_count, business_task_count, probe_task_count = local_fixture_name_counts(connection)
            if script_count or business_task_count or probe_task_count != 1:
                raise ProbeError("business_fixture_or_probe_task_local_presence_mismatch")
            if revision < expected_revision:
                raise ProbeError("agent_local_revision_behind_hub_preflight_revision")
            result = {
                "phase": "pre",
                "node_id": NODE_ID,
                "agent_id": AGENT_ID,
                "local_revision": revision,
                "expected_hub_revision": expected_revision,
                "local_revision_at_or_after_hub": True,
                "business_fixture_names_absent": True,
                "readonly_probe_task_present_once": True,
            }
        elif phase == "post":
            script_id = params["script_id"]
            task_id = params["task_id"]
            schedule_id = params["schedule_id"]
            execution_id = params["execution_id"]
            expected_scheduled_time = params["expected_scheduled_time"]
            rows = fixture_rows(connection, script_id, task_id, schedule_id)
            history = scheduled_execution(connection, execution_id, task_id, script_id)
            if revision < expected_revision:
                raise ProbeError("agent_local_revision_behind_hub_postdelete_revision")
            if rows != {"scripts": 0, "tasks": 0, "schedules": 0}:
                raise ProbeError("exact_fixture_definition_rows_remain")
            if (history is None or not history["id_matches"]
                    or not history["task_id_matches"] or not history["script_id_matches"]
                    or history["task_revision"] != 1 or history["script_revision"] != 1
                    or not history["node_id_matches"]
                    or history["status"] != "SUCCESS"
                    or history["trigger_type"] != "schedule"
                    or not history["scheduled_time"]
                    or history["scheduled_time"] != expected_scheduled_time):
                raise ProbeError("scheduled_execution_history_not_preserved")
            result = {
                "phase": "post",
                "node_id": NODE_ID,
                "agent_id": AGENT_ID,
                "local_revision": revision,
                "expected_hub_revision": expected_revision,
                "local_revision_at_or_after_hub": True,
                "exact_definition_rows": rows,
                "scheduled_execution_history": history,
            }
        else:
            raise ProbeError("probe_phase_invalid")
    finally:
        connection.close()
    after = path.lstat()
    if path.is_symlink() or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise ProbeError("agent_state_db_identity_changed_during_read")
    result.update({
        "database_identity": {
            "device": before.st_dev,
            "inode": before.st_ino,
            "uid": before.st_uid,
            "gid": before.st_gid,
            "mode": stat.S_IMODE(before.st_mode),
            "size": before.st_size,
            "path_sha256": sha256_bytes(str(path).encode()),
        },
        "quick_check": "ok",
        "query_only": True,
        "database_path_contents_emitted": False,
    })
    return result


def parameters():
    return {
        key: os.environ.get("NODESTEER_PARAM_" + key, "")
        for key in ("phase", "script_id", "task_id", "schedule_id",
                    "execution_id", "expected_scheduled_time", "expected_hub_revision")
    }


def run():
    params = parameters()
    if params["phase"] not in ("pre", "post") or not params["expected_hub_revision"]:
        raise ProbeError("probe_phase_missing_or_invalid")
    agent = guard_agent_ancestor()
    db_path, db_meta = state_db_path(agent["pid"])
    state = inspect_database(db_path, params["phase"], params)
    after = guard_agent_ancestor()
    if (agent["pid"], agent["startticks"], agent["proc_exe_sha"], agent["uid"]) != (
            after["pid"], after["startticks"], after["proc_exe_sha"], after["uid"]):
        raise ProbeError("agent_identity_changed_during_probe")
    state["agent_owner"] = agent
    state["database_file_identity_stable"] = True
    state["no_config_contents_or_full_environment_read"] = True
    return state


def main():
    try:
        out = {"status": "READONLY_PROBE_PASS", **run()}
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
