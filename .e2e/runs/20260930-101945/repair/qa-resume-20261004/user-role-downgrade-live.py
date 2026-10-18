#!/usr/bin/env python3
"""Prove role downgrade changes an existing QA session's authorization immediately."""
import json
import os
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
PRIVATE = Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONN = json.loads((PRIVATE / "repair-connection.json").read_text())
BASE = CONN["hub_url"].rstrip("/")
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]
USERS_PRIVATE = PRIVATE / "playwright" / "resume-20261004-safe01_21" / "ui-users.jsonl"
RESOURCES_PRIVATE = PRIVATE / "playwright" / "resume-20261004-safe01_21" / "resources.jsonl"


def api(path, method="GET", body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    active_token = CONN["token"] if token is None else token
    headers = {"Authorization": "Bearer " + active_token} if active_token else {}
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


def eval_js(script):
    return browser(["eval", "--stdin"], script)


def save_report(report):
    path = QA / "user-role-downgrade-live.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def field(row, key):
    if not isinstance(row, dict):
        return None
    return next((value for name, value in row.items() if name.lower() == key.lower()), None)


if __name__ == "__main__":
    credential_rows = [json.loads(line) for line in USERS_PRIVATE.read_text().splitlines() if line.strip()]
    final_credentials = next(row for row in reversed(credential_rows) if row.get("final") is True and row.get("username") and row.get("password"))
    username, password = final_credentials["username"], final_credentials["password"]
    retained = [json.loads(line) for line in RESOURCES_PRIVATE.read_text().splitlines() if line.strip()]
    fixture = next(row for row in reversed(retained) if row.get("path") == "/api/users" and row.get("status") == "retained_account")
    user_id = fixture["id"]
    report = {"case": "operator session immediately loses permission after downgrade to viewer", "user_id": user_id, "fixture_source": "retained unique QA account from SAFE-21", "main_admin_changed": False, "tool": "agent-browser plus authenticated live API probes", "secrets_included": False}
    user_token = None
    try:
        users_status, users = api("/api/users")
        user = next(row for row in users if row.get("id") == user_id)
        report["admin_users_before_http"] = users_status
        report["fixture_initial_role"] = user.get("role")
        promoted_status, _ = api("/api/users/" + user_id, "PUT", {"role": "operator"})
        report["qa_fixture_promote_http"] = promoted_status
        login_status, login = api("/api/login", "POST", {"username": username, "password": password}, token="")
        report["operator_login_http"] = login_status
        user_token = login.get("token")
        if login_status != 200 or not user_token:
            raise RuntimeError("QA Operator login failed HTTP " + str(login_status))

        me_status, me = api("/api/me", token=user_token)
        probe_id = str(uuid.uuid4())
        before_probe, _ = api("/api/executions/" + probe_id + "/cancel", "POST", token=user_token)
        report["operator_me_http"] = me_status
        report["operator_me_role"] = field(me, "role")
        report["operator_cancel_permission_probe_http"] = before_probe

        browser(["eval", "--stdin"], "localStorage.setItem('nodesteer_token'," + json.dumps(user_token) + ");")
        browser(["open", BASE + "/"])
        browser(["wait", "--load", "networkidle"])
        report["operator_ui_role_label_visible"] = "操作员" in eval_js("document.body.innerText")

        demote_status, _ = api("/api/users/" + user_id, "PUT", {"role": "viewer"})
        after_probe, _ = api("/api/executions/" + probe_id + "/cancel", "POST", token=user_token)
        viewer_me_status, viewer_me = api("/api/me", token=user_token)
        report["admin_downgrade_http"] = demote_status
        report["existing_session_cancel_permission_probe_http"] = after_probe
        report["existing_session_me_http"] = viewer_me_status
        report["existing_session_me_role_after_downgrade"] = field(viewer_me, "role")

        browser(["open", BASE + "/"])
        browser(["wait", "--load", "networkidle"])
        report["downgraded_ui_role_label_visible"] = "查看者" in eval_js("document.body.innerText")

        logout_status, _ = api("/api/logout", "POST", token=user_token)
        report["qa_session_logout_http"] = logout_status
        report["admin_users_after_http"] = api("/api/users")[0]
        report["admin_me_after_role_change"] = field(api("/api/me")[1], "role")
        checks = [
            report["admin_users_before_http"] == 200,
            report["fixture_initial_role"] == "viewer",
            promoted_status == 200,
            report["operator_me_role"] == "operator",
            before_probe == 400,
            report["operator_ui_role_label_visible"],
            demote_status == 200,
            after_probe == 403,
            viewer_me_status == 200 and field(viewer_me, "role") == "viewer",
            report["downgraded_ui_role_label_visible"],
            logout_status == 200,
            report["admin_users_after_http"] == 200,
            report["admin_me_after_role_change"] == "administrator",
        ]
        report["status"] = "passed" if all(checks) else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        # Keep the owned account as a valid Viewer and restore this isolated browser to the repair admin.
        restore_status, _ = api("/api/users/" + user_id, "PUT", {"role": "viewer"})
        if user_token:
            api("/api/logout", "POST", token=user_token)
        browser(["eval", "--stdin"], "localStorage.setItem('nodesteer_token'," + json.dumps(CONN["token"]) + ");")
        browser(["open", BASE + "/"])
        browser(["wait", "--load", "networkidle"])
        report["fixture_final_role_http"], final_users = api("/api/users")
        final_fixture = next((row for row in final_users if row.get("id") == user_id), None)
        report["browser_admin_restored"] = "管理员" in eval_js("document.body.innerText")
        report["fixture_final_role"] = field(final_fixture, "role")
        report["fixture_retained_as_viewer"] = report["fixture_final_role"] == "viewer"
        report["restore_role_http"] = restore_status
        save_report(report)
        print(json.dumps({"status": report["status"], "operator_probe": report.get("operator_cancel_permission_probe_http"), "viewer_probe": report.get("existing_session_cancel_permission_probe_http"), "admin_restored": report["browser_admin_restored"], "evidence": str((QA / "user-role-downgrade-live.json").relative_to(ROOT))}, ensure_ascii=False))
