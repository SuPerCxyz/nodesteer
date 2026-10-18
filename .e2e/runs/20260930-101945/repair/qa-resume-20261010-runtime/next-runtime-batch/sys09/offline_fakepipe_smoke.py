#!/usr/bin/env python3
"""Local fake SQLite/command-pipe smoke only; never runs a NodeSteer Task."""
import base64
import hashlib
import importlib.util
import json
import re
import sqlite3
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE_PATH = HERE / "readonly_probe.py"
BUILDER_PATH = HERE / "command_builder.py"
SCRIPT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
SCHEDULE_ID = "33333333-3333-4333-8333-333333333333"
EXECUTION_ID = "44444444-4444-4444-8444-444444444444"
PROBE_TASK_NAME = "qa-sys09-native3-20261012-2709acec1825-readonly-probe"
PROBE_TASK_ID = "66666666-6666-4666-8666-666666666666"


def load_module(name, path):
    source = path.read_bytes()
    compile(source, str(path), "exec")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return source, module


def create_fake_agent_db(path):
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE identity (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE sync_state (id INTEGER PRIMARY KEY, global_revision INTEGER NOT NULL, last_sync TEXT);
        CREATE TABLE scripts (id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL, sha256 TEXT NOT NULL);
        CREATE TABLE tasks (id TEXT PRIMARY KEY, definition_json TEXT NOT NULL, revision INTEGER NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE schedules (id TEXT PRIMARY KEY, definition_json TEXT NOT NULL, revision INTEGER NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE executions (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_revision INTEGER NOT NULL,
            script_id TEXT NOT NULL, script_revision INTEGER NOT NULL, node_id TEXT NOT NULL,
            trigger_type TEXT NOT NULL, scheduled_time TEXT NOT NULL, status TEXT NOT NULL
        );
    """)
    con.executemany("INSERT INTO identity(key,value) VALUES (?,?)", [
        ("node_id", "37989297-3bc8-4c0c-b302-7c6efbdba630"),
        ("agent_id", "agent-bb005487-7d84-418c-b598-f93d8b0c86a0"),
    ])
    con.execute("INSERT INTO sync_state(id,global_revision,last_sync) VALUES(1,100,'fake')")
    probe_task = {"id": PROBE_TASK_ID, "name": PROBE_TASK_NAME, "type": "command"}
    con.execute("INSERT INTO tasks VALUES(?,?,1,1)", (PROBE_TASK_ID, json.dumps(probe_task)))
    con.commit()
    con.close()


def insert_fixture_and_history(path):
    con = sqlite3.connect(path)
    con.execute("INSERT INTO scripts(id,name,revision,sha256) VALUES(?,?,1,?)",
                (SCRIPT_ID, "qa-sys09-fake-script", "a" * 64))
    task = {"id": TASK_ID, "name": "qa-sys09-fake-task", "type": "script", "script_id": SCRIPT_ID}
    schedule = {"id": SCHEDULE_ID, "task_id": TASK_ID, "type": "interval",
                "interval_sec": 15, "execution_owner": "agent"}
    con.execute("INSERT INTO tasks VALUES(?,?,1,1)", (TASK_ID, json.dumps(task)))
    con.execute("INSERT INTO schedules VALUES(?,?,1,1)", (SCHEDULE_ID, json.dumps(schedule)))
    con.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?)",
                (EXECUTION_ID, TASK_ID, 1, SCRIPT_ID, 1,
                 "37989297-3bc8-4c0c-b302-7c6efbdba630", "schedule",
                 "2026-10-12T16:00:15Z", "SUCCESS"))
    con.commit()
    con.close()


def expect_probe_error(fn, code):
    try:
        fn()
    except Exception as exc:
        if getattr(exc, "code", None) != code:
            raise SystemExit("fakepipe_expected_error_mismatch")
        return True
    raise SystemExit("fakepipe_expected_error_not_raised")


def main():
    probe_source, probe = load_module("qa_sys09_probe", PROBE_PATH)
    builder_source, builder = load_module("qa_sys09_builder", BUILDER_PATH)
    command = builder.build_command(probe_source)
    match = re.search(r'base64\.b64decode\("([A-Za-z0-9+/=]+)"\)', command)
    if not match:
        raise SystemExit("embedded_readonly_probe_command_shape_invalid")
    embedded = zlib.decompress(base64.b64decode(match.group(1)))
    if embedded != probe_source:
        raise SystemExit("embedded_source_mismatch")
    compile(embedded, "sys09_embedded_probe.py", "exec")

    with tempfile.TemporaryDirectory(prefix="qa-sys09-fakepipe-") as root:
        db_path = Path(root) / "state.db"
        create_fake_agent_db(db_path)
        phase_pre = {"phase": "pre", "expected_hub_revision": "100"}
        before_pre = hashlib.sha256(db_path.read_bytes()).hexdigest()
        pre = probe.inspect_database(db_path, "pre", phase_pre)
        after_pre = hashlib.sha256(db_path.read_bytes()).hexdigest()
        if (pre.get("business_fixture_names_absent") is not True
                or pre.get("readonly_probe_task_present_once") is not True
                or pre.get("local_revision") != 100
                or before_pre != after_pre):
            raise SystemExit("fakepipe_preflight_readonly_or_absence_check_failed")

        insert_fixture_and_history(db_path)
        with sqlite3.connect(db_path) as con:
            con.execute("UPDATE sync_state SET global_revision=105 WHERE id=1")
        post_params = {"script_id": SCRIPT_ID, "task_id": TASK_ID, "schedule_id": SCHEDULE_ID,
                       "execution_id": EXECUTION_ID, "expected_scheduled_time": "2026-10-12T16:00:15Z",
                       "expected_hub_revision": "105"}
        before_not_deleted = hashlib.sha256(db_path.read_bytes()).hexdigest()
        expect_probe_error(lambda: probe.inspect_database(db_path, "post", post_params),
                           "exact_fixture_definition_rows_remain")
        if hashlib.sha256(db_path.read_bytes()).hexdigest() != before_not_deleted:
            raise SystemExit("fakepipe_readonly_probe_mutated_db_on_expected_failure")

        with sqlite3.connect(db_path) as con:
            con.execute("DELETE FROM schedules WHERE id=?", (SCHEDULE_ID,))
            con.execute("DELETE FROM tasks WHERE id=?", (TASK_ID,))
            con.execute("DELETE FROM scripts WHERE id=?", (SCRIPT_ID,))
            con.commit()
        after_tombstones = hashlib.sha256(db_path.read_bytes()).hexdigest()
        post = probe.inspect_database(db_path, "post", post_params)
        if (post.get("exact_definition_rows") != {"scripts": 0, "tasks": 0, "schedules": 0}
                or post.get("scheduled_execution_history", {}).get("status") != "SUCCESS"
                or post.get("scheduled_execution_history", {}).get("trigger_type") != "schedule"
                or post.get("local_revision_at_or_after_hub") is not True
                or hashlib.sha256(db_path.read_bytes()).hexdigest() != after_tombstones):
            raise SystemExit("fakepipe_post_tombstone_readback_failed")

        low_revision = dict(post_params, expected_hub_revision="106")
        expect_probe_error(lambda: probe.inspect_database(db_path, "post", low_revision),
                           "agent_local_revision_behind_hub_postdelete_revision")
        missing_execution = dict(post_params, execution_id="55555555-5555-4555-8555-555555555555")
        expect_probe_error(lambda: probe.inspect_database(db_path, "post", missing_execution),
                           "scheduled_execution_history_not_preserved")

    print(json.dumps({
        "status": "LOCAL_FAKEPIPE_SMOKE_PASS_NOT_E2E",
        "readonly_source_compile": True,
        "command_builder_source_compile": True,
        "embedded_probe_source_compile": True,
        "preflight_fixture_absence_checked": True,
        "post_tombstone_rows_absent_and_execution_preserved": True,
        "negative_revision_and_missing_history_guards_checked": True,
        "sqlite_db_mutations_by_probe": 0,
        "api_calls": 0,
        "api_mutations": 0,
        "task_creates": 0,
        "run_posts": 0,
        "remote_node_touched": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
