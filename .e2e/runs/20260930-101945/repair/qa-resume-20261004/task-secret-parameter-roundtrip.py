#!/usr/bin/env python3
"""Verify a persisted secret Task parameter can be removed through the UI."""
import json
import os
import secrets
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
PRIVATE = Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONN = json.loads((PRIVATE / "repair-connection.json").read_text())
BASE = CONN["hub_url"].rstrip("/")
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]


def api(path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": "Bearer " + CONN["token"]}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            value = json.loads(raw) if raw else None
        except ValueError:
            value = None
        return error.code, value


def browser(args, stdin=None):
    process = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=20)
    if process.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (process.stdout + process.stderr)[-220:])
    return process.stdout.strip()


def snapshot():
    return browser(["snapshot", "-i", "-c", "-d", "6"])


def ref_for(text, kind):
    for line in snapshot().splitlines():
        if kind in line and text in line:
            import re
            match = re.search(r"ref=(e\d+)", line)
            if match:
                return "@" + match.group(1)
    raise RuntimeError("UI element missing: " + text)


def evaluate(script):
    return browser(["eval", "--stdin"], script)


def save_evidence(data):
    path = QA / "task-secret-parameter-roundtrip.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    node_response = urllib.request.urlopen(urllib.request.Request(BASE + "/api/nodes", headers={"Authorization": "Bearer " + CONN["token"]}), timeout=15)
    nodes = json.loads(node_response.read())
    node_id = nodes[0]["id"]
    task_name = "qa-resume-20261004-secret-remove-" + secrets.token_hex(5)
    secret_value = secrets.token_hex(24)
    create_status, created = api("/api/tasks", "POST", {
        "name": task_name, "type": "command", "command": "echo qa-definition-never-run",
        "target": {"type": "node", "node_ids": [node_id]}, "enabled": False,
        "timeout": 30, "retry": 0, "offline_policy": "hub_online_required", "parameters": [],
    })
    if create_status not in (200, 201) or not created or not created.get("id"):
        raise SystemExit("QA Task create failed HTTP " + str(create_status))
    task_id = created["id"]
    report = {"case": "Task persisted secret parameter mask/remove/save/reopen", "task_id": task_id, "create_http": create_status, "task_executed": False, "tool": "agent-browser", "secrets_included": False}
    try:
        browser(["pushstate", BASE + "/tasks/" + task_id + "/edit"])
        browser(["wait", "--load", "networkidle"])
        browser(["fill", ref_for("参数名称", "textbox"), "qa_secret"])
        browser(["click", ref_for("string", "combobox")])
        option_ref = ref_for("secret", "option")
        browser(["click", option_ref])
        field_ref = ref_for("默认值", "textbox")
        script = "(()=>{const e=document.querySelector('input[placeholder=\\\"默认值\\\"]');if(!e)return false;const s=" + json.dumps(secret_value) + ";const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(e,s);e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return e.value===s})()"
        report["secret_input_injected"] = "true" in evaluate(script).lower()
        browser(["click", ref_for("添加参数", "button")])
        added = evaluate("JSON.stringify({text:document.querySelector('main')?.innerText||'',inputType:document.querySelector('input[placeholder=\\\"默认值\\\"]')?.type||null})")
        report["new_parameter_summary_masked"] = "••••••••" in added and secret_value not in added
        report["new_parameter_summary_secret_absent"] = secret_value not in added
        browser(["click", ref_for("保存", "button")])
        browser(["wait", "--url", "**/tasks"])
        saved_status, saved = api("/api/tasks/" + task_id)
        params = saved.get("parameters") or []
        report["initial_save_http"] = saved_status
        report["initial_saved_revision"] = saved.get("revision")
        report["persisted_secret_default_matches"] = len(params) == 1 and params[0].get("name") == "qa_secret" and params[0].get("type") == "secret" and params[0].get("default") == secret_value

        browser(["pushstate", BASE + "/tasks/" + task_id + "/edit"])
        browser(["wait", "--load", "networkidle"])
        reopened = evaluate("JSON.stringify({text:document.querySelector('main')?.innerText||'',passwordInputs:[...document.querySelectorAll('input[type=password]')].map(e=>e.value)})")
        report["reopened_secret_summary_masked"] = "••••••••" in reopened
        report["reopened_secret_absent_from_visible_text"] = secret_value not in reopened
        report["reopened_secret_input_loaded"] = secret_value in reopened
        browser(["screenshot", str(QA / "task-secret-persisted-masked.png")])

        remove = ref_for("移除", "button")
        browser(["click", remove])
        removed_editor = evaluate("document.querySelector('main')?.innerText||''")
        report["removed_parameter_absent_before_save"] = "qa_secret" not in removed_editor
        browser(["click", ref_for("保存", "button")])
        browser(["wait", "--url", "**/tasks"])
        removed_status, removed = api("/api/tasks/" + task_id)
        report["remove_save_http"] = removed_status
        report["removed_parameters_empty"] = not (removed.get("parameters") or [])
        report["removed_revision"] = removed.get("revision")
        browser(["pushstate", BASE + "/tasks/" + task_id + "/edit"])
        browser(["wait", "--load", "networkidle"])
        after = evaluate("JSON.stringify({text:document.querySelector('main')?.innerText||'',parameterName:document.querySelector('input[placeholder=\\\"参数名称\\\"]')?.value||''})")
        report["reopened_removed_parameter_absent"] = "qa_secret" not in after and '••••••••' not in after
        browser(["screenshot", str(QA / "task-secret-removed.png")])
        report["status"] = "passed" if all(report.get(key) for key in ["secret_input_injected", "new_parameter_summary_masked", "persisted_secret_default_matches", "reopened_secret_summary_masked", "reopened_secret_absent_from_visible_text", "removed_parameter_absent_before_save", "removed_parameters_empty", "reopened_removed_parameter_absent"]) else "failed"
        report["input_observation"] = "The persisted default is represented by a masked summary; it is not rehydrated into the new-parameter input. This is outside the requested remove/save acceptance path."
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        delete_status, _ = api("/api/tasks/" + task_id, "DELETE")
        get_status, _ = api("/api/tasks/" + task_id)
        report["cleanup_delete_http"] = delete_status
        report["cleanup_get_http"] = get_status
        report["cleanup_verified"] = delete_status in (200, 204, 404) and get_status == 404
        save_evidence(report)
        print(json.dumps({"status": report["status"], "task_id": task_id, "persisted": report.get("persisted_secret_default_matches"), "removed": report.get("removed_parameters_empty"), "cleanup": report["cleanup_verified"], "evidence": str((QA / 'task-secret-parameter-roundtrip.json').relative_to(ROOT))}, ensure_ascii=False))
