#!/usr/bin/env python3
"""Inspect only the owned repair native-1 Unit and Agent through a manual Task."""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
PRIVATE = Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONN = json.loads((PRIVATE / "repair-connection.json").read_text())
BASE = CONN["hub_url"].rstrip("/")
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]
HOSTNAME = "qa-repair-20260930-101945-native-1"
UNIT = "nodesteer-agent-repair-20260930-101945-native-1.service"
AGENT_BIN = "/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent"
AGENT_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
COMMAND = f'''set -eu
unit='{UNIT}'
agent='{AGENT_BIN}'
printf 'hostname=%s\\n' "$(hostname)"
printf 'unit=%s\\n' "$unit"
printf 'unit_active=%s\\n' "$(systemctl is-active "$unit")"
unit_pid="$(systemctl show --property=MainPID --value "$unit")"
printf 'unit_main_pid=%s\\n' "$unit_pid"
unit_exe="$(readlink "/proc/$unit_pid/exe")"
unit_sha="$(sha256sum "/proc/$unit_pid/exe" | awk '{{print $1}}')"
printf 'unit_main_exe=%s\\n' "$unit_exe"
printf 'unit_main_sha256=%s\\n' "$unit_sha"
printf 'agent_disk_sha256=%s\\n' "$(sha256sum "$agent" | awk '{{print $1}}')"
parent_exe="$(readlink "/proc/$PPID/exe" || true)"
parent_sha="$(sha256sum "/proc/$PPID/exe" 2>/dev/null | awk '{{print $1}}' || true)"
printf 'direct_parent_exe=%s\\n' "$parent_exe"
printf 'direct_parent_sha256=%s\\n' "$parent_sha"
'''


def api(path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + CONN["token"]}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers, method=method), timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw) if raw else None
        except ValueError:
            return error.code, None


def browser(args, stdin=None):
    result = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=25)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-180:])
    return result.stdout.strip()


def evaluate(script):
    raw = browser(["eval", "--stdin"], script)
    value = json.loads(raw)
    return json.loads(value) if isinstance(value, str) else value


def evaluate_text():
    raw = browser(["eval", "--stdin"], "JSON.stringify(document.body.innerText)")
    try:
        value = json.loads(raw)
        return json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return raw


def snapshot():
    return browser(["snapshot", "-i", "-c", "-d", "7"])


def ref_for(predicate):
    for line in snapshot().splitlines():
        if predicate(line):
            match = re.search(r"ref=(e\d+)", line)
            if match:
                return "@" + match.group(1)
    raise RuntimeError("UI element not found")


def save(report):
    path = QA / "native1-host-inspection-task.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    status, nodes = api("/api/nodes")
    node = next((row for row in nodes if row.get("hostname") == HOSTNAME), None)
    if status != 200 or not node:
        raise SystemExit("repair native-1 API mapping unavailable")
    node_id = node["id"]
    task_name = "qa-resume-20261005-native1-inspect-" + os.urandom(4).hex()
    report = {"case": "native-1 actual Host inspection through one-off manual command Task", "authority": BASE, "node_id": node_id, "node_hostname_expected": HOSTNAME, "expected_unit": UNIT, "expected_agent_sha256": AGENT_SHA, "product_path": "Task UI create -> manual Run -> Execution UI output", "command_scope": "read-only exact repair Agent binary, exact repair native-1 Unit, its Unit MainPID and current direct parent process", "secrets_included": False}
    task_id = None
    execution_id = None
    try:
        browser(["open", BASE + "/tasks/new"])
        browser(["wait", "--load", "networkidle"])
        browser(["fill", ref_for(lambda line: "textbox" in line and '"名称"' in line), task_name])
        browser(["fill", ref_for(lambda line: "textbox" in line and '"命令"' in line), COMMAND])
        browser(["fill", ref_for(lambda line: "spinbutton" in line and "超时" in line), "30"])
        node_ref = ref_for(lambda line: "checkbox" in line and HOSTNAME in line)
        browser(["check", node_ref])
        browser(["click", ref_for(lambda line: "button" in line and "保存" in line)])
        browser(["wait", "--url", "**/tasks"])
        task_status, tasks = api("/api/tasks")
        task = next((row for row in tasks if row.get("name") == task_name), None)
        if task_status != 200 or not task:
            raise RuntimeError("UI-created manual Task missing from API list")
        task_id = task["id"]
        task_status, task = api("/api/tasks/" + task_id)
        if task_status != 200 or not task or task.get("name") != task_name:
            raise RuntimeError("new manual Task detail missing from API")
        report["task_id"] = task_id
        report["task_create_from_ui"] = True
        report["node_status_before_task"] = node.get("status")
        report["agent_version_before_task"] = node.get("agent_version")
        report["task_enabled"] = task.get("enabled")
        report["task_timeout"] = task.get("timeout")
        report["task_target_node_ids"] = task.get("target", {}).get("node_ids", [])
        schedule_status, schedules = api("/api/schedules?task_id=" + task_id)
        report["schedule_count"] = len(schedules) if isinstance(schedules, list) else None
        if report["task_target_node_ids"] != [node_id] or task.get("enabled") is not True or task.get("timeout") != 30 or report["schedule_count"] != 0:
            raise RuntimeError("Task is not enabled for exactly the owned native-1 node")

        browser(["open", BASE + "/tasks/" + task_id + "/run"])
        browser(["wait", "--load", "networkidle"])
        browser(["click", ref_for(lambda line: "button" in line and "立即运行" in line)])
        browser(["click", ref_for(lambda line: "button" in line and "立即运行" in line)])
        browser(["wait", "--url", "**/executions"])
        report["ui_manual_run_url"] = browser(["get", "url"]).splitlines()[-1]
        report["run_request"] = next((line for line in browser(["network", "requests"]).splitlines() if "/api/tasks/" + task_id + "/run" in line), None)

        deadline = time.monotonic() + 45
        execution = None
        while time.monotonic() < deadline:
            code, rows = api("/api/executions?task_id=" + task_id + "&limit=10")
            if code == 200 and rows:
                execution = rows[0]
                if execution.get("status") in ("SUCCESS", "FAILED", "CANCELED", "TIMED_OUT", "BLOCKED"):
                    break
            time.sleep(1)
        if not execution:
            raise RuntimeError("manual Task produced no execution record")
        execution_id = execution.get("id")
        detail_status, detail = api("/api/executions/" + execution_id) if execution_id else (0, None)
        output = (detail or {}).get("stdout") or (detail or {}).get("output") or execution.get("stdout") or execution.get("output") or ""
        report["execution_id"] = execution_id
        report["execution_detail_http"] = detail_status
        report["execution_status"] = execution.get("status")
        report["execution_exit_code"] = execution.get("exit_code", execution.get("exit_code"))
        lines = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in str(output).splitlines() if "=" in line}
        report["host_output"] = {key: lines.get(key) for key in ["hostname", "unit", "unit_active", "unit_main_pid", "unit_main_exe", "unit_main_sha256", "agent_disk_sha256", "direct_parent_exe", "direct_parent_sha256"]}
        browser(["open", BASE + "/executions/" + execution_id])
        browser(["wait", "--load", "networkidle"])
        report["execution_ui_path"] = browser(["get", "url"]).splitlines()[-1]
        report["execution_ui_shows_host_output"] = "hostname=" + HOSTNAME in evaluate_text()
        report["direct_parent_is_agent"] = report["host_output"].get("direct_parent_exe") == AGENT_BIN
        report["unit_main_executable_sha_matches"] = report["host_output"].get("unit_main_sha256") == AGENT_SHA
        report["agent_disk_sha_matches"] = report["host_output"].get("agent_disk_sha256") == AGENT_SHA
        report["host_identity_matches_native1"] = report["host_output"].get("hostname") == HOSTNAME
        report["status"] = "passed" if report["execution_status"] == "SUCCESS" and report["execution_ui_shows_host_output"] and report["unit_main_executable_sha_matches"] and report["agent_disk_sha_matches"] and report["host_identity_matches_native1"] else "partial"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        if task_id:
            delete_status, _ = api("/api/tasks/" + task_id, "DELETE")
            get_status, _ = api("/api/tasks/" + task_id)
            report["task_cleanup_delete_http"] = delete_status
            report["task_cleanup_get_http"] = get_status
            report["task_cleanup_verified"] = delete_status in (200, 204, 404) and get_status == 404
            report["execution_history_retained"] = bool(execution_id)
        save(report)
        print(json.dumps({"status": report["status"], "execution_status": report.get("execution_status"), "host": report.get("host_output", {}).get("hostname"), "agent_sha_matches": report.get("agent_disk_sha_matches"), "cleanup": report.get("task_cleanup_verified"), "evidence": str((QA / "native1-host-inspection-task.json").relative_to(ROOT))}, ensure_ascii=False))
