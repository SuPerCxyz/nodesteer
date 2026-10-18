"""Narrow repair-authority helpers for 20261009 owned E2E cases."""
import hashlib
import json
import os
import sys
import subprocess
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_BACKEND = HERE.parent / "qa-resume-20261008-backend"
sys.path.insert(0, str(OLD_BACKEND))
import run_env01_05 as api

api.PRIVATE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
api.OUT = HERE
TOKEN = api.read_private()
HUB = "http://192.168.100.209:18080"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def validate_command_source(command, label):
    if not command.startswith("python3 - <<'PY'\n") or not command.endswith("\nPY"):
        return
    source = command[len("python3 - <<'PY'\n"):-len("\nPY")]
    compile(source, label, "exec")
    result = subprocess.run(["bash", "-n"], input=command, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError(label + "_bash_syntax_failed")
    write_json(HERE / (label + "-syntax-check.json"), {"python_compile": "PASS", "bash_n": "PASS", "command_sha256": hashlib.sha256(command.encode()).hexdigest()})


def request(method, path, body=None):
    return api.request(TOKEN, method, path, body)


def get(path):
    return api.get_json(TOKEN, path)


def create_task(label, command, node_id, condition=None, timeout=45):
    validate_command_source(command, label)
    payload = {"name": label, "type": "command", "target": {"type": "node", "node_ids": [node_id]}, "parameters": [], "param_values": {}, "command": command, "timeout": timeout, "retry": 0, "offline_policy": "hub_online_required", "enabled": True}
    if condition is not None:
        payload["condition"] = condition
    path = "/api/tasks"
    write_json(HERE / (label + "-definition.json"), {"method": "POST", "path": path, "payload": payload, "command_sha256": hashlib.sha256(command.encode()).hexdigest()})
    status, raw = request("POST", path, payload)
    if status != 200:
        raise RuntimeError(label + "_create_http_" + str(status))
    task_id = json.loads(raw).get("id")
    if not task_id:
        raise RuntimeError(label + "_task_id_missing")
    readback = get(path + "/" + urllib.parse.quote(task_id, safe=""))
    if readback.get("command") != command or readback.get("target", {}).get("node_ids") != [node_id] or readback.get("retry") != 0 or readback.get("schedule"):
        raise RuntimeError(label + "_definition_readback_mismatch")
    if condition is not None and readback.get("condition") != condition:
        raise RuntimeError(label + "_condition_readback_mismatch")
    write_json(HERE / (label + "-created.json"), {"id": task_id, "name": label, "create_http": status, "target_node_id": node_id, "retry": 0, "schedule_present": False, "condition": condition, "command_sha256": hashlib.sha256(command.encode()).hexdigest()})
    return readback


def run_task(task, label, timeout=90):
    task_id = task["id"]
    path = "/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/run"
    write_json(HERE / (label + "-run-definition.json"), {"method": "POST", "path": path, "task_id": task_id, "target_node_ids": task.get("target", {}).get("node_ids"), "retry": task.get("retry"), "schedule_present": bool(task.get("schedule"))})
    status, raw = request("POST", path, {"params": {}})
    if status != 200:
        raise RuntimeError(label + "_run_http_" + str(status))
    executions = json.loads(raw)
    if len(executions) != 1 or executions[0].get("node_id") != task.get("target", {}).get("node_ids", [None])[0]:
        raise RuntimeError(label + "_execution_target_or_count_mismatch")
    execution_id = executions[0]["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get("/api/executions/" + urllib.parse.quote(execution_id, safe=""))
        if result.get("status") not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(label + "_execution_timeout")
    logs = get("/api/executions/" + urllib.parse.quote(execution_id, safe="") + "/logs")
    stdout, stderr = result.get("stdout", ""), result.get("stderr", "")
    sensitive = TOKEN in stdout or TOKEN in stderr
    evidence = {"task_id": task_id, "execution_id": execution_id, "node_id": result.get("node_id"), "status": result.get("status"), "exit_code": result.get("exit_code"), "stdout": "" if sensitive else stdout, "stderr": "" if sensitive else stderr, "stdout_chars": len(stdout), "stderr_chars": len(stderr), "sensitive_output_suppressed": sensitive, "block_reason": result.get("block_reason", ""), "revision": result.get("revision"), "task_revision": result.get("task_revision"), "script_revision": result.get("script_revision"), "logs": logs}
    write_json(HERE / (label + "-execution.json"), evidence)
    result["id"] = execution_id
    return result


def delete_task(task_id, label, expected_target, expected_name_prefix="qa-resume-20261009-"):
    path = "/api/tasks/" + urllib.parse.quote(task_id, safe="")
    task = get(path)
    if task.get("target", {}).get("node_ids") != [expected_target] or not task.get("name", "").startswith(expected_name_prefix):
        raise RuntimeError(label + "_cleanup_owner_guard_failed")
    status, raw = request("DELETE", path)
    gone = api.route_status(TOKEN, path)
    value = {"task_id": task_id, "name": task.get("name"), "target_node_id": expected_target, "delete_http": status, "get_after_delete_http": gone, "response_bytes": len(raw)}
    write_json(HERE / (label + "-cleanup.json"), value)
    if status != 200 or gone != 404:
        raise RuntimeError(label + "_cleanup_failed")
    return value


def native4_command(label, source, native4_id, timeout=60):
    command = api.make_python_command(source)
    validate_command_source(command, label)
    if TOKEN in command:
        raise RuntimeError("native4_command_contains_bearer")
    task = create_task("qa-resume-20261009-" + label + "-" + os.urandom(4).hex(), command, native4_id, timeout=timeout)
    try:
        result = run_task(task, label, timeout + 15)
        try:
            output = json.loads(result.get("stdout") or "")
        except (ValueError, TypeError):
            output = None
        return {"task_id": task["id"], "execution_id": result["id"], "status": result.get("status"), "output": output}
    finally:
        delete_task(task["id"], label + "-cleanup", native4_id)
