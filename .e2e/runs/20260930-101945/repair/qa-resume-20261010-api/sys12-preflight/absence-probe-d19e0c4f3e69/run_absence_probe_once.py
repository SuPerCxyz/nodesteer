#!/usr/bin/env python3
import hashlib
import json
import shlex
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "absence_probe.py"
ATTEMPT = HERE.parent / "sys12-d19e0c4f3e69-ledger.json"
CONNECTION = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
TASK_NAME = "qa-sys12-absence-probe-d19e0c4f3e69"
TARGET_NODE = "611c02a5-1859-4465-878b-d543b7f6fdec"
OWNER = "qa-resume-20261012-sys12-d19e0c4f3e69"
TERMINAL = {"SUCCESS", "FAILED", "SKIPPED", "BLOCKED", "TIMED_OUT", "CANCELED"}
LEDGER_PATH = HERE / "probe-ledger.json"
RESULT_PATH = HERE / "probe-result.json"
PROGRESS_PATH = HERE / "probe-progress.json"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def persist(codex, ledger, result, status, next_action):
    ledger["status"] = status
    ledger["updated_at_utc"] = codex.utc()
    codex.save(LEDGER_PATH, ledger)
    codex.save(RESULT_PATH, result)
    codex.save(PROGRESS_PATH, {"updated_at_utc": codex.utc(), "status": status,
                               "next_action": next_action, "task_id": ledger.get("task_id"),
                               "execution_id": ledger.get("execution_id")})


def exact_rows(api, task_id):
    query = urlencode({"task_id": task_id, "limit": 10})
    code, obj, _ = api.request("GET", "/api/executions?" + query)
    rows = codex.list_rows(obj, ("executions", "items"))
    if code != 200 or rows is None:
        raise RuntimeError("EXECUTION_LIST_UNREADABLE")
    return [row for row in rows if isinstance(row, dict) and row.get("task_id") == task_id]


def verify_task(api, task_id, command_sha):
    code, task, _ = api.request("GET", "/api/tasks/" + task_id)
    if (code != 200 or not isinstance(task, dict) or task.get("id") != task_id
            or task.get("name") != TASK_NAME or task.get("type") != "command"
            or sha(str(task.get("command", "")).encode()) != command_sha
            or task.get("target", {}).get("node_ids") != [TARGET_NODE]
            or task.get("timeout") != 15 or task.get("retry") != 0):
        raise RuntimeError("EXACT_PROBE_TASK_GUARD_FAILED")
    return task


def main():
    global codex
    sys.path.insert(0, str(HERE.parent))
    import sys12_outer_controller as codex_module
    codex = codex_module

    raw_source = SOURCE.read_bytes()
    source = raw_source.decode()
    compile(source, str(SOURCE), "exec")
    command = "python3 -c " + shlex.quote(source)
    command_sha = sha(command.encode())
    source_sha = sha(raw_source)
    attempt = json.loads(ATTEMPT.read_text())
    if (attempt.get("nonce") != "d19e0c4f3e69" or len(attempt.get("helper_calls", [])) != 5
            or any(not x.get("terminal_proven") or x.get("execution_status") not in TERMINAL
                   or not x.get("task_deleted_and_404") for x in attempt.get("helper_calls", []))):
        raise RuntimeError("SOURCE_ATTEMPT_LEDGER_TERMINAL_GUARD_FAILED")
    if LEDGER_PATH.exists() or RESULT_PATH.exists() or PROGRESS_PATH.exists():
        raise RuntimeError("LOCAL_PROBE_LEDGER_COLLISION")

    base, token = codex.read_private(CONNECTION)
    api = codex.API(base, token)
    ledger = {"created_at_utc": codex.utc(), "attempt_nonce": "d19e0c4f3e69",
              "owner": OWNER, "task_name": TASK_NAME, "target_node_id": TARGET_NODE,
              "source_sha256": source_sha, "command_sha256": command_sha,
              "task_id": None, "execution_id": None, "run_requested": False,
              "terminal_proven": False, "task_deleted_and_404": False,
              "business_runs": 0, "docker_mutations": 0, "normal_helper_call_slot": 4,
              "global_helper_budget_before": {"normal": 3, "recovery": 2, "total": 5}}
    result = {"status": "PREPARED", "source_sha256": source_sha,
              "command_sha256": command_sha, "probe": None, "api_evidence": []}
    api.on_response = lambda item: (result["api_evidence"].append(item), codex.save(RESULT_PATH, result))
    persist(codex, ledger, result, "PREFLIGHT", "recheck target and exact task name immediately before create")

    code, node, _ = api.request("GET", "/api/nodes/" + TARGET_NODE)
    if (code != 200 or not isinstance(node, dict) or node.get("id") != TARGET_NODE
            or node.get("status") != "online" or node.get("sync_status") != "synced"):
        raise RuntimeError("NATIVE2_NOT_ONLINE_SYNCED")
    code, tasks, _ = api.request("GET", "/api/tasks")
    rows = codex.list_rows(tasks, ("tasks", "items"))
    if code != 200 or rows is None or any(isinstance(x, dict) and x.get("name") == TASK_NAME for x in rows):
        raise RuntimeError("EXACT_PROBE_TASK_NAME_COLLISION_OR_LIST_ERROR")

    persist(codex, ledger, result, "TASK_CREATE_REQUESTED", "create one exact readonly probe Task")
    code, created, _ = api.request("POST", "/api/tasks", {
        "name": TASK_NAME, "type": "command", "command": command,
        "target": {"type": "node", "node_ids": [TARGET_NODE]}, "parameters": [],
        "timeout": 15, "retry": 0, "offline_policy": "hub_online_required", "enabled": True,
    })
    if code not in (200, 201) or not isinstance(created, dict) or not isinstance(created.get("id"), str):
        ledger["task_create_http"] = code
        persist(codex, ledger, result, "CREATE_RESPONSE_AMBIGUOUS", "do not retry create; preserve for exact owner reconciliation")
        raise RuntimeError("PROBE_TASK_CREATE_RESPONSE_AMBIGUOUS")
    task_id = created["id"]
    ledger["task_id"] = task_id
    persist(codex, ledger, result, "TASK_CREATED", "GET exact Task and verify name/command/target/budget")
    verify_task(api, task_id, command_sha)

    ledger["run_requested"] = True
    persist(codex, ledger, result, "RUN_REQUESTED", "run the single authorized diagnostic Task once")
    code, started, _ = api.request("POST", "/api/tasks/" + task_id + "/run", {"params": {}})
    executions = codex.list_rows(started, ("executions",))
    if executions is None and isinstance(started, dict) and isinstance(started.get("id"), str):
        executions = [started]
    if code in (200, 201) and isinstance(executions, list) and len(executions) == 1 and isinstance(executions[0], dict):
        execution_id = executions[0].get("id")
    else:
        matches = exact_rows(api, task_id)
        execution_id = matches[0].get("id") if len(matches) == 1 else None
    if not isinstance(execution_id, str) or not execution_id:
        ledger["run_response_ambiguous"] = True
        persist(codex, ledger, result, "OWNED_ACTIVE_HELPER_BLOCKED", "no unique Execution bound; stop all remote mutations")
        raise RuntimeError("OWNED_ACTIVE_HELPER_BLOCKED")
    ledger["execution_id"] = execution_id
    persist(codex, ledger, result, "WAITING_FOR_TERMINAL", "poll only the exact Task-bound Execution")

    def read_execution():
        ecode, execution, _ = api.request("GET", "/api/executions/" + execution_id)
        if (ecode != 200 or not isinstance(execution, dict) or execution.get("id") != execution_id
                or execution.get("task_id") != task_id):
            raise RuntimeError("OWNED_ACTIVE_HELPER_BLOCKED")
        return execution

    deadline = time.monotonic() + 35
    execution = None
    while time.monotonic() < deadline:
        current = read_execution()
        if current.get("status") in TERMINAL:
            execution = current
            break
        time.sleep(0.4)
    if execution is None:
        code, obj, _ = api.request("POST", "/api/executions/" + execution_id + "/cancel")
        ledger["cancel_http"] = code
        persist(codex, ledger, result, "CANCEL_REQUESTED", "poll exact Execution for terminal after cancel request")
        cancel_deadline = time.monotonic() + 20
        while time.monotonic() < cancel_deadline:
            current = read_execution()
            if current.get("status") in TERMINAL:
                execution = current
                break
            time.sleep(0.4)
        if execution is None:
            ledger["status"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            persist(codex, ledger, result, "OWNED_ACTIVE_HELPER_BLOCKED", "preserve Task; no further remote mutations")
            raise RuntimeError("OWNED_ACTIVE_HELPER_BLOCKED")
    ledger["terminal_proven"] = True
    ledger["execution_status"] = execution.get("status")
    persist(codex, ledger, result, "EXECUTION_TERMINAL", "read bounded logs; then delete exact Task")
    lcode, logs, _ = api.request("GET", "/api/executions/" + execution_id + "/logs", limit=1024 * 1024)
    chunks = codex.list_rows(logs, ("logs",))
    if lcode != 200 or chunks is None:
        raise RuntimeError("PROBE_LOG_READBACK_FAILED")
    stdout = "".join(x.get("chunk", "") for x in sorted(
        (x for x in chunks if isinstance(x, dict) and x.get("stream") == "stdout"),
        key=lambda x: (x.get("seq", 0), x.get("created_at", ""))) if isinstance(x.get("chunk"), str))
    try:
        probe = json.loads(stdout) if stdout else None
    except ValueError:
        probe = None
    if execution.get("status") != "SUCCESS" or not isinstance(probe, dict):
        result["safe_error"] = "PROBE_EXECUTION_OR_JSON_OUTPUT_NOT_SUCCESS"
    else:
        result["probe"] = probe
    persist(codex, ledger, result, "TERMINAL_RESULT_RECORDED", "cleanup only the exact terminal probe Task")

    verify_task(api, task_id, command_sha)
    dcode, _, _ = api.request("DELETE", "/api/tasks/" + task_id)
    gcode, _, _ = api.request("GET", "/api/tasks/" + task_id)
    ledger["task_delete_http"] = dcode
    ledger["task_get_after_delete_http"] = gcode
    ledger["task_deleted_and_404"] = dcode in (200, 404) and gcode == 404
    if not ledger["task_deleted_and_404"]:
        persist(codex, ledger, result, "EXACT_TASK_CLEANUP_FAILED", "preserve ledger; Root review")
        raise RuntimeError("EXACT_PROBE_TASK_CLEANUP_FAILED")
    persist(codex, ledger, result, "COMPLETE", "Root review diagnostic only; do not alter product or rerun business lifecycle")
    print(json.dumps({"status": result["status"], "task_id": task_id,
                      "execution_id": execution_id, "execution_status": execution.get("status"),
                      "task_delete_http": dcode, "task_get_after_delete_http": gcode,
                      "probe": result.get("probe"), "safe_error": result.get("safe_error")},
                     separators=(",", ":")))


if __name__ == "__main__":
    codex = None
    main()
