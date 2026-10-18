#!/usr/bin/env python3
"""Check removal of an API-seeded persisted secret Task parameter through the UI."""
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
    result = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-220:])
    return result.stdout.strip()


def snapshot():
    return browser(["snapshot", "-i", "-c", "-d", "6"])


def ref_for(text, kind):
    import re
    for line in snapshot().splitlines():
        if text in line and kind in line:
            match = re.search(r"ref=(e\d+)", line)
            if match:
                return "@" + match.group(1)
    raise RuntimeError("UI element missing: " + text)


def evaluate(script):
    return browser(["eval", "--stdin"], script)


def evaluate_json(script):
    value = json.loads(evaluate(script))
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def evidence(data):
    path = QA / "task-secret-parameter-reverse.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    token = CONN["token"]
    with urllib.request.urlopen(urllib.request.Request(BASE + "/api/nodes", headers={"Authorization": "Bearer " + token}), timeout=15) as response:
        nodes = json.loads(response.read())
    secret = secrets.token_hex(24)
    task_name = "qa-resume-20261004-secret-reverse-" + secrets.token_hex(5)
    create_status, created = api("/api/tasks", "POST", {
        "name": task_name, "type": "command", "command": "echo qa-definition-never-run",
        "target": {"type": "node", "node_ids": [nodes[0]["id"]]}, "enabled": False,
        "timeout": 30, "retry": 0, "offline_policy": "hub_online_required",
        "parameters": [{"name": "qa_secret", "type": "secret", "default": secret, "required": False}],
    })
    if create_status not in (200, 201) or not created or not created.get("id"):
        raise SystemExit("QA Task fixture create failed HTTP " + str(create_status))
    task_id = created["id"]
    report = {"case": "persisted secret parameter UI remove/save/reopen", "task_id": task_id, "fixture_source": "real Hub API seeded private-only secret default", "tool": "agent-browser", "task_executed": False, "secrets_included": False}
    try:
        before_status, before = api("/api/tasks/" + task_id)
        params = before.get("parameters") or []
        report["fixture_get_http"] = before_status
        report["fixture_secret_default_matches"] = len(params) == 1 and params[0].get("name") == "qa_secret" and params[0].get("type") == "secret" and params[0].get("default") == secret
        report["before_revision"] = before.get("revision")

        browser(["open", BASE + "/tasks/" + task_id + "/edit"])
        browser(["wait", "--load", "networkidle"])
        # Use a private-only comparison in page context; never return the password itself.
        visible_script = "(()=>{const t=document.querySelector('main')?.innerText||'';const s=" + json.dumps(secret) + ";return JSON.stringify({masked:t.includes('••••••••'),plaintextVisible:t.includes(s),rowVisible:t.includes('qa_secret')})})()"
        visible = evaluate_json(visible_script)
        report["reopened_parameter_row_visible"] = visible.get("rowVisible") is True
        report["reopened_summary_masked"] = visible.get("masked") is True
        report["reopened_plaintext_absent"] = visible.get("plaintextVisible") is False
        # The API-seeded masked row is observed before any UI edit.
        path = "/api/tasks/" + task_id
        browser(["click", ref_for("移除", "button")])
        after_remove = evaluate("document.querySelector('main')?.innerText||''")
        report["parameter_absent_before_save"] = "qa_secret" not in after_remove
        browser(["click", ref_for("保存", "button")])
        browser(["wait", "--url", "**/tasks"])
        browser(["wait", "--load", "networkidle"])
        request_log = browser(["network", "requests"])
        put_statuses = []
        expected_url = BASE + path
        for line in request_log.splitlines():
            if " PUT " + expected_url + " " in line:
                try:
                    put_statuses.append(int(line.rsplit(" ", 1)[-1]))
                except ValueError:
                    pass
        report["ui_put_statuses"] = put_statuses
        saved_status, saved = api(path)
        report["after_save_get_http"] = saved_status
        report["after_revision"] = saved.get("revision")
        report["parameters_empty_after_save"] = not (saved.get("parameters") or [])

        browser(["open", BASE + "/tasks/" + task_id + "/edit"])
        browser(["wait", "--load", "networkidle"])
        reopened = evaluate_json("(()=>{const t=document.querySelector('main')?.innerText||'';return JSON.stringify({rowPresent:t.includes('qa_secret'),maskPresent:t.includes('••••••••')})})()")
        report["reopen_has_no_parameter_row"] = reopened.get("rowPresent") is False
        report["reopen_has_no_secret_summary"] = reopened.get("maskPresent") is False
        report["status"] = "passed" if all(report.get(key) for key in ["fixture_secret_default_matches", "reopened_parameter_row_visible", "reopened_summary_masked", "reopened_plaintext_absent", "parameter_absent_before_save", "parameters_empty_after_save", "reopen_has_no_parameter_row", "reopen_has_no_secret_summary"]) and put_statuses == [200] else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        delete_status, _ = api("/api/tasks/" + task_id, "DELETE")
        get_status, _ = api("/api/tasks/" + task_id)
        report["cleanup_delete_http"] = delete_status
        report["cleanup_get_http"] = get_status
        report["cleanup_verified"] = delete_status in (200, 204, 404) and get_status == 404
        evidence(report)
        print(json.dumps({"status": report["status"], "ui_put_statuses": report.get("ui_put_statuses"), "parameters_empty": report.get("parameters_empty_after_save"), "cleanup": report["cleanup_verified"], "evidence": str((QA / 'task-secret-parameter-reverse.json').relative_to(ROOT))}, ensure_ascii=False))
