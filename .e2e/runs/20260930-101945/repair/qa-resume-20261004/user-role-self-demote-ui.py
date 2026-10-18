#!/usr/bin/env python3
"""Verify an administrator demoting their own QA account is immediately reflected in the same UI page."""
import json
import os
import re
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
USERS_PRIVATE = PRIVATE / "playwright" / "resume-20261004-safe01_21" / "ui-users.jsonl"
RESOURCES_PRIVATE = PRIVATE / "playwright" / "resume-20261004-safe01_21" / "resources.jsonl"


def api(path, method="GET", body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    active = CONN["token"] if token is None else token
    headers = {"Authorization": "Bearer " + active} if active else {}
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
    process = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=25)
    if process.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (process.stdout + process.stderr)[-180:])
    return process.stdout.strip()


def evaluate(script):
    raw = browser(["eval", "--stdin"], script)
    value = json.loads(raw)
    return json.loads(value) if isinstance(value, str) else value


def snapshot():
    return browser(["snapshot", "-i", "-c", "-d", "6"])


def ref_for_line(predicate):
    for line in snapshot().splitlines():
        if predicate(line):
            match = re.search(r"ref=(e\d+)", line)
            if match:
                return "@" + match.group(1)
    raise RuntimeError("UI reference not found")


def save(report):
    path = QA / "user-role-self-demote-ui.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    credential_rows = [json.loads(line) for line in USERS_PRIVATE.read_text().splitlines() if line.strip()]
    final_credentials = next(row for row in reversed(credential_rows) if row.get("final") is True and row.get("username") and row.get("password"))
    username, password = final_credentials["username"], final_credentials["password"]
    resources = [json.loads(line) for line in RESOURCES_PRIVATE.read_text().splitlines() if line.strip()]
    fixture = next(row for row in reversed(resources) if row.get("path") == "/api/users" and row.get("status") == "retained_account")
    user_id = fixture["id"]
    report = {"case": "User can self-demote through Users UI and immediately loses Admin controls", "user_id": user_id, "fixture_source": "owned retained SAFE-21 QA account", "same_page_no_reload": True, "secrets_included": False, "tool": "agent-browser"}
    token = None
    try:
        promote_status, _ = api("/api/users/" + user_id, "PUT", {"role": "administrator"})
        login_status, login = api("/api/login", "POST", {"username": username, "password": password}, token="")
        token = login.get("token") if login else None
        if promote_status != 200 or login_status != 200 or not token:
            raise RuntimeError("owned QA administrator setup failed")
        report["promote_http"] = promote_status
        report["login_http"] = login_status
        browser(["eval", "--stdin"], "localStorage.setItem('nodesteer_token'," + json.dumps(token) + ");")
        browser(["open", BASE + "/users"]); browser(["wait", "--load", "networkidle"])
        initial = evaluate("JSON.stringify({url:location.pathname,body:document.body.innerText,create:[...document.querySelectorAll('button')].some(b=>/新建用户/.test(b.innerText)&&!b.disabled)})")
        report["initial_path"] = initial["url"]
        report["initial_admin_label_visible"] = "管理员" in initial["body"]
        report["initial_new_user_button_enabled"] = initial["create"] is True

        search_ref = ref_for_line(lambda line: "textbox" in line and "搜索" in line)
        browser(["fill", search_ref, username]); browser(["wait", "300"])
        role_ref = ref_for_line(lambda line: "combobox" in line and ("管理员" in line or "administrator" in line.lower()))
        before_requests = browser(["network", "requests"]).splitlines()
        browser(["click", role_ref])
        viewer_ref = ref_for_line(lambda line: "option" in line and "查看者" in line)
        browser(["click", viewer_ref]); browser(["wait", "--load", "networkidle"])
        after_requests = browser(["network", "requests"]).splitlines()
        report["role_put_request"] = next((line for line in after_requests if line not in before_requests and "PUT " + BASE + "/api/users/" + user_id in line), None)
        report["url_after_self_demote"] = browser(["get", "url"]).splitlines()[-1]
        live_me = evaluate("fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}}).then(async r=>JSON.stringify({status:r.status,role:(await r.json()).Role}))")
        report["same_session_me_after_self_demote"] = live_me
        same_page = evaluate("JSON.stringify({url:location.pathname,body:document.body.innerText,create:[...document.querySelectorAll('button')].some(b=>/新建用户/.test(b.innerText)&&!b.disabled),form:!!document.querySelector('input[placeholder*=\"用户名\"]')})")
        report["same_page_path_after_self_demote"] = same_page["url"]
        report["row_viewer_label_visible"] = "查看者" in same_page["body"]
        report["new_user_button_enabled_after_self_demote"] = same_page["create"] is True
        report["admin_form_open_after_self_demote"] = same_page["form"] is True
        if same_page["create"] is True:
            # Opening the form is a UI-only probe; no user is submitted or created.
            create_ref = ref_for_line(lambda line: "button" in line and "新建用户" in line)
            browser(["click", create_ref]); browser(["wait", "200"])
            form = evaluate("JSON.stringify({form:!!document.querySelector('input[placeholder*=\"用户名\"]'),url:location.pathname})")
            report["admin_form_open_after_click"] = form["form"]
            report["url_after_create_button_probe"] = form["url"]
            cancel = next((line for line in snapshot().splitlines() if "button" in line and "取消" in line), None)
            if cancel:
                match = re.search(r"ref=(e\d+)", cancel)
                if match: browser(["click", "@" + match.group(1)])

        status, users = api("/api/users")
        current = next(row for row in users if row.get("id") == user_id)
        report["admin_users_api_http"] = status
        report["current_user_role_from_admin_view"] = current.get("role")
        report["status"] = "passed" if report["initial_admin_label_visible"] and report["initial_new_user_button_enabled"] and report["same_session_me_after_self_demote"]["role"] == "viewer" and report["same_page_path_after_self_demote"] == "/users" and report["row_viewer_label_visible"] and not report["new_user_button_enabled_after_self_demote"] and not report["admin_form_open_after_self_demote"] and status == 200 else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        # Preserve the account as a valid Viewer, log out only this QA session, and restore the private repair admin browser.
        restore_status, _ = api("/api/users/" + user_id, "PUT", {"role": "viewer"})
        if token:
            api("/api/logout", "POST", token=token)
        browser(["eval", "--stdin"], "localStorage.setItem('nodesteer_token'," + json.dumps(CONN["token"]) + ");")
        browser(["open", BASE + "/"]); browser(["wait", "--load", "networkidle"])
        report["restore_role_http"] = restore_status
        report["browser_admin_restored"] = "管理员" in evaluate("JSON.stringify(document.body.innerText)")
        final_users_status, final_users = api("/api/users")
        report["admin_api_still_available"] = final_users_status == 200
        report["fixture_final_role"] = next((row.get("role") for row in final_users if row.get("id") == user_id), None)
        save(report)
        print(json.dumps({"status": report["status"], "same_page_role": report.get("same_session_me_after_self_demote"), "new_user_button_enabled_after": report.get("new_user_button_enabled_after_self_demote"), "admin_restored": report["browser_admin_restored"], "evidence": str((QA / "user-role-self-demote-ui.json").relative_to(ROOT))}, ensure_ascii=False))
