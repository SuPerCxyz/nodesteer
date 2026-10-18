"""Scoped repair :18080 API client; credentials remain in the private temp file."""
import hashlib
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
HUB = "http://192.168.100.209:18080"
PRIVATE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
os.umask(0o077)
st = PRIVATE.lstat()
if not PRIVATE.is_file() or PRIVATE.is_symlink() or (st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600):
    raise RuntimeError("private_connection_file_gate_failed")
_connection = json.loads(PRIVATE.read_text())
if _connection.get("hub_url") != HUB or not isinstance(_connection.get("token"), str) or not _connection["token"]:
    raise RuntimeError("private_connection_authority_gate_failed")
TOKEN = _connection["token"]
del _connection


def write_json(name, value):
    path = HERE / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def request(method, path, body=None):
    headers = {"Authorization": "Bearer " + TOKEN}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(HUB + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def get_json(path):
    status, raw = request("GET", path)
    if status != 200:
        raise RuntimeError("api_get_http_" + str(status))
    return json.loads(raw)


def validate_command(command, label):
    if not command.startswith("python3 - <<'PY'\n") or not command.endswith("\nPY"):
        raise RuntimeError("command_heredoc_shape_invalid")
    source = command[len("python3 - <<'PY'\n"):-len("\nPY")]
    compile(source, label, "exec")
    result = subprocess.run(["bash", "-n"], input=command, text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError("command_bash_syntax_failed")
    write_json(label + "-syntax.json", {"python_compile": "PASS", "bash_n": "PASS", "command_sha256": hashlib.sha256(command.encode()).hexdigest()})


def create_task(name, command, node_id):
    validate_command(command, name)
    payload = {"name": name, "type": "command", "target": {"type": "node", "node_ids": [node_id]}, "parameters": [], "param_values": {}, "command": command, "timeout": 55, "retry": 0, "offline_policy": "hub_online_required", "enabled": True}
    write_json("task-definition-" + name + ".json", {"method": "POST", "path": "/api/tasks", "target_node_id": node_id, "payload": {k: v for k, v in payload.items() if k != "command"}, "command_sha256": hashlib.sha256(command.encode()).hexdigest()})
    status, raw = request("POST", "/api/tasks", payload)
    if status != 200:
        raise RuntimeError("task_create_http_" + str(status))
    task = json.loads(raw)
    task_id = task.get("id")
    if not task_id:
        raise RuntimeError("task_id_missing")
    readback = get_json("/api/tasks/" + urllib.parse.quote(task_id, safe=""))
    if readback.get("command") != command or readback.get("target", {}).get("node_ids") != [node_id] or readback.get("retry") != 0 or readback.get("schedule"):
        raise RuntimeError("task_definition_readback_mismatch")
    write_json("task-created-" + name + ".json", {"id": task_id, "name": name, "create_http": status, "target_node_id": node_id, "retry": 0, "schedule_present": False, "command_sha256": hashlib.sha256(command.encode()).hexdigest()})
    return readback


def run_task(task, timeout=90, params=None, secret_values=()):
    task_id = task["id"]
    params = params or {}
    write_json("task-run-definition-" + task_id + ".json", {"task_id": task_id, "provided_parameter_names": sorted(params), "secret_parameter_names": sorted(params) if secret_values else [], "parameter_values_persisted": False})
    status, raw = request("POST", "/api/tasks/" + urllib.parse.quote(task_id, safe="") + "/run", {"params": params})
    if status != 200:
        raise RuntimeError("task_run_http_" + str(status))
    rows = json.loads(raw)
    if len(rows) != 1 or rows[0].get("node_id") != task.get("target", {}).get("node_ids", [None])[0]:
        raise RuntimeError("task_execution_target_or_count_mismatch")
    execution_id = rows[0]["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = get_json("/api/executions/" + urllib.parse.quote(execution_id, safe=""))
        if result.get("status") not in {"PENDING", "RUNNING"}:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("task_execution_timeout")
    output = result.get("stdout", "")
    stderr = result.get("stderr", "")
    sensitive = any(value and (value in output or value in stderr) for value in secret_values)
    write_json("execution-" + task_id + ".json", {"task_id": task_id, "execution_id": execution_id, "node_id": result.get("node_id"), "status": result.get("status"), "exit_code": result.get("exit_code"), "stdout": "" if sensitive else output, "stdout_sha256": hashlib.sha256(output.encode()).hexdigest(), "stderr_chars": len(stderr), "block_reason": result.get("block_reason", ""), "sensitive_output_suppressed": sensitive})
    result["id"] = execution_id
    return result


def cleanup_task(task_id, expected_node):
    path = "/api/tasks/" + urllib.parse.quote(task_id, safe="")
    task = get_json(path)
    owned_prefixes = ("qa-resume-20261009-host-tls-",)
    if task.get("target", {}).get("node_ids") != [expected_node] or not task.get("name", "").startswith(owned_prefixes):
        raise RuntimeError("task_cleanup_identity_guard_failed")
    status, raw = request("DELETE", path)
    after_status, _ = request("GET", path)
    value = {"task_id": task_id, "target_node_id": expected_node, "delete_http": status, "get_after_delete_http": after_status, "response_bytes": len(raw)}
    write_json("task-cleanup-" + task_id + ".json", value)
    if status != 200 or after_status != 404:
        raise RuntimeError("task_cleanup_failed")
    return value
