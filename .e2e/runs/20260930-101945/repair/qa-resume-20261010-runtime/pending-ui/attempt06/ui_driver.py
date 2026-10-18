#!/usr/bin/env python3
"""Prepare and, only with an explicit reviewed gate, verify the PENDING UI path."""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


HERE = Path(__file__).resolve().parent
CREDS = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json")
BASE = "http://192.168.100.209:18080"
NODE = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
AGENT = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
SESSION = "nodesteer-pending-ui-20261010"
PROFILE_DIR = None
MAX_PENDING_SECONDS = 15.0
opener = build_opener(ProxyHandler({}))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def api(path: str, method: str = "GET", body=None, token=None, allowed=(200,)):
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with opener.open(Request(BASE + path, data=data, headers=headers, method=method), timeout=8) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    except URLError as error:
        raise RuntimeError("repair_api_unavailable") from error
    if status not in allowed:
        raise RuntimeError(f"repair_api_http_{status}")
    return status, json.loads(raw) if raw else None


def browser(args, stdin=None, timeout=8):
    # The session owns its auto-launched browser; never attach to a shared CDP port.
    if PROFILE_DIR is None:
        raise RuntimeError("owned_browser_profile_not_initialized")
    command_args = list(args)
    if command_args and command_args[0] == "open":
        command_args[1:1] = ["--init-script", str(HERE / "ui-network-observer.js")]
    return subprocess.run(
        ["agent-browser", "--session", SESSION, "--profile", str(PROFILE_DIR), *command_args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def parse_browser_json(output: str):
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("browser_json_output_missing")
    value = json.loads(lines[-1])
    return json.loads(value) if isinstance(value, str) else value


def create_owned_browser_profile() -> dict:
    global PROFILE_DIR
    path = Path(tempfile.mkdtemp(prefix="nodesteer-pending-ui-", dir="/tmp"))
    os.chmod(path, 0o700)
    PROFILE_DIR = path
    st = path.stat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("owned_browser_profile_owner_guard_failed")
    return {"path": str(path), "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid}


def activate_owned_browser_profile(expected: dict) -> None:
    global PROFILE_DIR
    path = Path(expected.get("path", ""))
    if path.parent != Path("/tmp") or not path.name.startswith("nodesteer-pending-ui-") or path.is_symlink():
        raise RuntimeError("prepared_browser_profile_path_guard_failed")
    st = path.stat()
    actual = {"path": str(path), "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid}
    if actual != expected or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        raise RuntimeError("prepared_browser_profile_owner_guard_failed")
    PROFILE_DIR = path


def remove_owned_browser_profile(expected: dict) -> bool:
    try:
        st = PROFILE_DIR.lstat()
        if PROFILE_DIR.parent != Path("/tmp") or not PROFILE_DIR.name.startswith("nodesteer-pending-ui-") or PROFILE_DIR.is_symlink() or not stat.S_ISDIR(st.st_mode):
            return False
        actual = {"path": str(PROFILE_DIR), "dev": st.st_dev, "inode": st.st_ino, "uid": st.st_uid}
        if actual != expected or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
            return False
        shutil.rmtree(PROFILE_DIR)
        return not PROFILE_DIR.exists() and not PROFILE_DIR.is_symlink()
    except FileNotFoundError:
        return False


def browser_cdp_guard(expected=None) -> dict:
    info_result = browser(["session", "info", "--json"], timeout=1.5)
    if info_result.returncode != 0:
        raise RuntimeError("dedicated_browser_session_info_unavailable")
    info = parse_browser_json(info_result.stdout).get("data", {})
    runtime = info.get("runtime", {})
    lifecycle = runtime.get("lifecycle", {})
    if info.get("session") != SESSION or runtime.get("browserLaunched") is not True or lifecycle.get("reused") is True:
        raise RuntimeError("dedicated_browser_not_owned_session_launch")
    result = browser(["get", "cdp-url"], timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("dedicated_browser_cdp_unavailable")
    value = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    try:
        parsed = urlsplit(value)
    except ValueError as error:
        raise RuntimeError("dedicated_browser_cdp_url_invalid") from error
    if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise RuntimeError("dedicated_browser_cdp_not_loopback")
    with socket.create_connection(("127.0.0.1", parsed.port), timeout=1.0):
        pass
    port_hex = f"{parsed.port:04X}"
    inodes = set()
    for proc_net in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            for line in proc_net.read_text().splitlines()[1:]:
                cols = line.split()
                if cols[1].rsplit(":", 1)[1].upper() == port_hex and cols[3] == "0A":
                    inodes.add(cols[9])
        except OSError:
            continue
    owners = []
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            for fd in os.scandir(f"/proc/{pid}/fd"):
                target = os.readlink(fd.path)
                if not target.startswith("socket:[") or target[8:-1] not in inodes:
                    continue
                stat_fields = Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()
                raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                profile_match = re.search(rb"--user-data-dir(?:=|\s+)([^\x00\s]+)", raw_cmdline)
                if not profile_match:
                    raise RuntimeError("browser_profile_path_not_provable")
                profile = Path(os.fsdecode(profile_match.group(1)))
                if profile != PROFILE_DIR:
                    raise RuntimeError("browser_profile_not_exact_owned_path")
                profile_stat = profile.stat()
                proc_uid = os.stat(f"/proc/{pid}").st_uid
                exe_path = os.readlink(f"/proc/{pid}/exe")
                exe_sha = hashlib.sha256(Path(f"/proc/{pid}/exe").read_bytes()).hexdigest()
                owner = {
                    "pid": pid,
                    "start_ticks": int(stat_fields[19]),
                    "uid": proc_uid,
                    "exe_basename": Path(exe_path).name,
                    "exe_sha256": exe_sha,
                    "profile_path": str(profile),
                    "profile_dev": profile_stat.st_dev,
                    "profile_inode": profile_stat.st_ino,
                    "profile_uid": profile_stat.st_uid,
                    "profile_mode": stat.S_IMODE(profile_stat.st_mode),
                }
                owners.append(owner)
        except (OSError, PermissionError, ProcessLookupError):
            continue
    if len(owners) != 1:
        raise RuntimeError("browser_cdp_listener_owner_not_unique")
    owner = owners[0]
    if owner["uid"] != os.getuid() or owner["profile_uid"] != os.getuid() or owner["profile_mode"] != 0o700:
        raise RuntimeError("browser_cdp_owner_or_profile_guard_failed")
    result = {
        "loopback": True,
        "port": parsed.port,
        "session": SESSION,
        "session_background_pid": runtime.get("backgroundPid"),
        "browser_launched_by_session": True,
        "browser_owner": owner,
    }
    if expected is not None and result != expected:
        raise RuntimeError("dedicated_browser_process_fingerprint_changed")
    return result


def login_and_install_browser_token():
    st = CREDS.stat()
    if stat.S_IMODE(st.st_mode) != 0o600:
        raise RuntimeError("private_credentials_mode_not_0600")
    credentials = json.loads(CREDS.read_text())
    username = credentials.get("admin_username")
    password = credentials.get("admin_password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RuntimeError("private_login_fields_missing")
    token = None
    installed = False
    try:
        _, login = api("/api/login", "POST", {"username": username, "password": password})
        token = login.get("token") or login.get("Token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("login_token_missing")
        _, me = api("/api/me", token=token)
        role = me.get("Role", me.get("role")) if isinstance(me, dict) else None
        if role != "administrator":
            raise RuntimeError("authenticated_role_not_administrator")
        opened = browser(["open", BASE + "/sign-in"], timeout=4)
        if opened.returncode != 0:
            raise RuntimeError("dedicated_browser_open_failed")
        js = "localStorage.setItem('nodesteer_token'," + json.dumps(token) + "); 'session_token_installed';"
        result = browser(["eval", "--stdin"], js)
        if result.returncode != 0:
            raise RuntimeError("browser_token_install_failed")
        installed = True
        opened = browser(["open", BASE + "/"], timeout=4)
        if opened.returncode != 0:
            raise RuntimeError("authenticated_home_open_failed")
        browser(["wait", "--load", "networkidle"], timeout=5)
        browser_me = browser_session_me()
        if browser_me.get("http_status") != 200 or browser_me.get("role") != "administrator" or browser_me.get("role_is_administrator") is not True:
            raise RuntimeError("browser_session_not_authenticated_administrator")
        return token
    except Exception:
        if token:
            try:
                if installed:
                    clear = browser(["eval", "--stdin"], "localStorage.removeItem('nodesteer_token'); sessionStorage.removeItem('qa_pending_ui_task_id'); true", timeout=1.5)
                api("/api/logout", "POST", {}, token=token, allowed=(200, 204, 401))
                api("/api/me", token=token, allowed=(401,))
            except Exception:
                pass
        raise


def exact_task(task_id: str, expected_name: str, marker: str, token: str) -> dict:
    _, task = api("/api/tasks/" + quote(task_id, safe=""), token=token)
    target = task.get("target") or {}
    node_ids = target.get("node_ids") or target.get("nodeIds") or []
    if (
        task.get("id") != task_id
        or task.get("name") != expected_name
        or task.get("type") != "command"
        or task.get("retry") != 0
        or task.get("timeout") != 15
        or task.get("enabled") is not True
        or node_ids != [NODE]
        or task.get("parameters") not in ([], None)
        or task.get("command") != "sleep 4; printf '%s\\n' '" + marker + "'"
    ):
        raise RuntimeError("task_identity_or_scope_guard_failed")
    _, schedules_data = api("/api/schedules", token=token)
    schedules = schedules_data if isinstance(schedules_data, list) else schedules_data.get("schedules", schedules_data.get("items", [])) if isinstance(schedules_data, dict) else []
    if any(row.get("task_id") == task_id for row in schedules if isinstance(row, dict)):
        raise RuntimeError("unexpected_task_schedule")
    return task


def visible_text() -> str:
    result = browser(["read"], timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("browser_read_failed")
    return result.stdout


def snapshot_meta() -> dict:
    result = browser(["snapshot", "-i"], timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("browser_snapshot_failed")
    return {"sha256": hashlib.sha256(result.stdout.encode()).hexdigest(), "bytes": len(result.stdout.encode()), "has_run_button": any(x in result.stdout for x in ("Run Now", "立即运行"))}


def snapshot_button_ref(label: str, last: bool = False) -> str:
    result = browser(["snapshot", "-i"], timeout=1.5)
    if result.returncode != 0:
        return ""
    refs = []
    for line in result.stdout.splitlines():
        if "button" not in line or '"' + label + '"' not in line:
            continue
        match = re.search(r"@e(\d+)|\[ref=e(\d+)\]", line)
        if match:
            refs.append("@e" + (match.group(1) or match.group(2)))
    if not refs:
        return ""
    return refs[-1] if last else refs[0]


def check_hold_proof(path: Path, task_id: str, nonce: str) -> dict:
    proof = json.loads(path.read_text())
    if proof.get("status") != "HOLD_ARMED" or proof.get("task_id") != task_id or proof.get("hold_nonce") != nonce:
        raise RuntimeError("hold_proof_exact_identity_failed")
    created = datetime.fromisoformat(proof["armed_at_utc"].replace("Z", "+00:00"))
    age = (utc_now() - created).total_seconds()
    if age < 0 or age > MAX_PENDING_SECONDS:
        raise RuntimeError("hold_proof_expired_or_clock_invalid")
    if proof.get("node_id") != NODE or proof.get("agent_id") != AGENT or proof.get("control_keys") != ["hold_types", "qa_hold_nonce", "qa_hold_task_id"]:
        raise RuntimeError("hold_proof_scope_guard_failed")
    return proof


def check_prepared_proof(path: Path, args) -> dict:
    proof = json.loads(path.read_text())
    expected_path = "/tasks/" + args.task_id + "/run"
    if proof.get("status") != "UI_PREPARED_NO_RUN" or proof.get("task_id") != args.task_id or proof.get("task_name") != args.task_name or proof.get("marker") != args.marker or proof.get("node_id") != NODE or proof.get("agent_id") != AGENT or proof.get("browser_session") != SESSION or proof.get("browser_cdp_guard", {}).get("loopback") is not True or not isinstance(proof.get("browser_cdp_guard", {}).get("port"), int) or proof.get("authenticated_role") != "administrator" or proof.get("task_exact_scope_verified") is not True or proof.get("run_button", {}).get("has_run_button") is not True or not re.fullmatch(r"@e\d+", proof.get("run_button", {}).get("ref", "")) or proof.get("run_page_path") != expected_path or proof.get("browser_session_left_open") is not True or proof.get("run_clicked") is not False:
        raise RuntimeError("prepared_ui_proof_identity_mismatch")
    expected_profile = proof.get("browser_profile") or {}
    activate_owned_browser_profile(expected_profile)
    browser_cdp_guard(proof["browser_cdp_guard"])
    current = browser(["get", "url"], timeout=1.5)
    expected_url = BASE + expected_path
    if current.returncode != 0 or current.stdout.strip() != expected_url:
        raise RuntimeError("prepared_browser_url_changed")
    browser_session_me()
    browser_task_readback(args.task_id, args.task_name, args.marker)
    if not snapshot_meta().get("has_run_button"):
        raise RuntimeError("prepared_browser_run_button_missing")
    live_run_ref = ""
    for label in ("Run Now", "立即运行"):
        live_run_ref = snapshot_button_ref(label)
        if live_run_ref:
            break
    if not re.fullmatch(r"@e\d+", live_run_ref):
        raise RuntimeError("prepared_browser_run_button_ref_missing")
    return proof


def click_run_once() -> None:
    # Use the real RunTask page and its confirm dialog; no direct /tasks/{id}/run API call.
    labels = ("Run Now", "立即运行")
    first = next((ref for label in labels if (ref := snapshot_button_ref(label))), "")
    if not first:
        raise RuntimeError("run_now_button_not_found")
    if browser(["click", first], timeout=1.5).returncode != 0:
        raise RuntimeError("run_now_ui_click_failed")
    confirmation = next((ref for label in labels if (ref := snapshot_button_ref(label, last=True))), "")
    if not confirmation:
        raise RuntimeError("confirmation_run_button_not_found")
    if browser(["click", confirmation], timeout=1.5).returncode != 0:
        raise RuntimeError("confirmation_run_ui_click_failed")


def browser_session_me() -> dict:
    result = browser(["eval", "--stdin"], "JSON.stringify(window.__nodesteerPendingUIObserver?.events||[])", timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("browser_me_check_failed")
    events = parse_browser_json(result.stdout)
    me = next((event for event in reversed(events) if event.get("path") == "/api/me"), {})
    if me.get("http_status") != 200 or me.get("role") != "administrator" or me.get("role_is_administrator") is not True:
        raise RuntimeError("browser_session_not_authenticated_administrator")
    return me


def cleanup_prepared_browser_session(proof: dict) -> dict:
    """Revoke the browser's exact stored session, prove same-token 401, then close its profile."""
    result = {"status": "CLEANUP_STARTED", "browser_session": SESSION}
    try:
        activate_owned_browser_profile(proof.get("browser_profile") or {})
        result["browser_owner_guard"] = bool(browser_cdp_guard(proof.get("browser_cdp_guard") or {}))
        current = browser(["get", "url"], timeout=1.5)
        parsed = urlsplit(current.stdout.strip()) if current.returncode == 0 else None
        expected_origin = urlsplit(BASE)
        if not parsed or (parsed.scheme, parsed.netloc) != (expected_origin.scheme, expected_origin.netloc):
            result["same_origin_page"] = False
        else:
            result["same_origin_page"] = True
            cleanup_js = "(async()=>{const old=localStorage.getItem('nodesteer_token');let logoutStatus=null,oldMeStatus=null,unauthenticatedMeStatus=null;if(old){const h={Authorization:'Bearer '+old,'Content-Type':'application/json'};try{logoutStatus=(await fetch('/api/logout',{method:'POST',headers:h,body:'{}'})).status}catch{};try{oldMeStatus=(await fetch('/api/me',{headers:{Authorization:'Bearer '+old}})).status}catch{}}else{try{unauthenticatedMeStatus=(await fetch('/api/me')).status}catch{}}localStorage.removeItem('nodesteer_token');sessionStorage.removeItem('qa_pending_ui_task_id');return JSON.stringify({hadToken:!!old,logoutStatus,oldMeStatus,unauthenticatedMeStatus,tokenPresent:!!localStorage.getItem('nodesteer_token')})})()"
            response = browser(["eval", "--stdin"], cleanup_js, timeout=2)
            # parse_browser_json already handles both a raw JSON object and a JSON-encoded string.
            metadata = parse_browser_json(response.stdout) if response.returncode == 0 else {}
            if not isinstance(metadata, dict):
                metadata = {}
            result["logout_status"] = metadata.get("logoutStatus")
            result["same_token_me_status"] = metadata.get("oldMeStatus")
            result["unauthenticated_me_status"] = metadata.get("unauthenticatedMeStatus")
            result["stored_token_was_present"] = metadata.get("hadToken") is True
            result["token_storage_cleared"] = metadata.get("tokenPresent") is False
    except Exception as error:
        result["safe_error"] = str(error)[:100]
    try:
        closed = browser(["close"], timeout=3).returncode == 0
        result["browser_session_closed"] = closed
        profile = proof.get("browser_profile") or {}
        result["browser_profile_removed"] = remove_owned_browser_profile(profile) if closed else False
    except Exception:
        result["browser_session_closed"] = False
        result["browser_profile_removed"] = False
    result["same_token_logout_verified"] = result.get("logout_status") in (200, 204) and result.get("same_token_me_status") == 401
    result["status"] = "CLEANED" if result.get("same_origin_page") and result.get("same_token_logout_verified") and result.get("token_storage_cleared") and result.get("browser_session_closed") and result.get("browser_profile_removed") else "CLEANUP_PARTIAL"
    return result


def browser_task_readback(task_id: str, task_name: str, marker: str) -> dict:
    result = browser(["eval", "--stdin"], "JSON.stringify(window.__nodesteerPendingUIObserver?.events||[])", timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("browser_task_readback_failed")
    events = parse_browser_json(result.stdout)
    row = next((event for event in reversed(events) if event.get("path") == "/api/tasks/" + task_id and event.get("http_status") == 200), {})
    expected_command = "sleep 4; printf '%s\\n' '" + marker + "'"
    if row.get("task_id") != task_id or row.get("name") != task_name or row.get("type") != "command" or row.get("retry") != 0 or row.get("timeout") != 15 or row.get("enabled") is not True or row.get("node_ids") != [NODE] or row.get("parameters") not in ([], None) or row.get("command") != expected_command:
        raise RuntimeError("browser_task_or_schedule_scope_guard_failed")
    return row


def run_page_readiness(task_id: str, task_name: str, timeout: float = 5.0) -> dict:
    """Wait for the async task query and title; retain only safe readiness metadata."""
    deadline = time.monotonic() + timeout
    last = {"path": None, "task_get_status": None, "task_name_visible": False, "state": "loading"}
    while time.monotonic() < deadline:
        page = browser(["read"], timeout=1.5)
        js = "(()=>{const id=" + json.dumps(task_id) + ";const events=window.__nodesteerPendingUIObserver?.events||[];const task=[...events].reverse().find(x=>x.path==='/api/tasks/'+id);const text=document.body?.innerText||'';return JSON.stringify({path:location.pathname,task_get_status:task?.http_status??null,task_name_visible:text.includes(" + json.dumps(task_name) + "),loading:/loading|加载中/i.test(text),error:!!document.querySelector('[role=alert]')})})()"
        observed = browser(["eval", "--stdin"], js, timeout=1.5)
        if page.returncode == 0 and observed.returncode == 0:
            state = parse_browser_json(observed.stdout)
            last = state
            if state.get("path") != "/tasks/" + task_id + "/run":
                last["state"] = "wrong_path"
                return last
            if state.get("task_get_status") not in (None, 200):
                last["state"] = "task_api_error"
                return last
            if state.get("error"):
                last["state"] = "page_error"
                return last
            if state.get("task_get_status") == 200 and state.get("task_name_visible"):
                last["state"] = "ready"
                return last
            last["state"] = "loading" if state.get("loading") or state.get("task_get_status") is None else "content_mismatch"
        time.sleep(0.1)
    last["state"] = "timeout_" + str(last.get("state", "loading"))
    return last


def execution_link_for_task(task_name: str) -> str:
    js = "(()=>{const name=" + json.dumps(task_name) + ";const rows=[...document.querySelectorAll('tr')];const row=rows.find(x=>x.innerText.includes(name));if(!row)return '';const link=[...row.querySelectorAll('a[href^=\"/executions/\"]')].find(x=>/\\/executions\\/[0-9a-f-]{36}/i.test(x.getAttribute('href')||''));return link?link.getAttribute('href'):''})()"
    result = browser(["eval", "--stdin"], js, timeout=2)
    if result.returncode != 0:
        raise RuntimeError("execution_link_lookup_failed")
    raw = result.stdout.strip()
    if not raw:
        return ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw
    return value if isinstance(value, str) and re.fullmatch(r"/executions/[0-9a-f-]{36}", value) else ""


def execution_ui_state() -> dict:
    js = "(()=>{const grids=[...document.querySelectorAll('main .grid')];const g=grids.find(x=>x.children.length===5);const card=g&&g.children[0];return JSON.stringify({url:location.pathname,navigationEntries:performance.getEntriesByType('navigation').length,timeOrigin:performance.timeOrigin,historyLength:history.length,statusCard:card?card.innerText:'',pageText:document.querySelector('main')?.innerText||''})})()"
    result = browser(["eval", "--stdin"], js, timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("execution_ui_state_read_failed")
    return parse_browser_json(result.stdout)


def canonical_badge(value: str) -> str | None:
    if re.search(r"\bPending\b|等待中", value, re.I):
        return "PENDING"
    if re.search(r"\bRunning\b|运行中", value, re.I):
        return "RUNNING"
    if re.search(r"\bSuccess\b|成功", value, re.I):
        return "SUCCESS"
    if re.search(r"\bFailed\b|失败", value, re.I):
        return "FAILED"
    if re.search(r"Timed out|超时", value, re.I):
        return "TIMED_OUT"
    if re.search(r"Canceled|已取消", value, re.I):
        return "CANCELED"
    if re.search(r"Blocked|已阻断", value, re.I):
        return "BLOCKED"
    return None


def browser_execution_oracle(execution_id: str, task_id: str) -> dict:
    result = browser(["eval", "--stdin"], "JSON.stringify(window.__nodesteerPendingUIObserver?.events||[])", timeout=1.5)
    if result.returncode != 0:
        raise RuntimeError("browser_execution_oracle_failed")
    events = parse_browser_json(result.stdout)
    row = next((event for event in reversed(events) if event.get("path") == "/api/executions/" + execution_id and event.get("http_status") == 200), {})
    if row.get("id") != execution_id or row.get("task_id") != task_id or row.get("node_id") != NODE:
        raise RuntimeError("api_execution_identity_oracle_mismatch")
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--execute-reviewed-run", action="store_true")
    parser.add_argument("--hold-proof", type=Path)
    parser.add_argument("--hold-nonce")
    parser.add_argument("--prepared-proof", type=Path)
    parser.add_argument("--evidence", type=Path, default=HERE / "ui-evidence.json")
    args = parser.parse_args()
    if args.prepare_only == args.execute_reviewed_run:
        raise SystemExit("choose_exactly_one_of_prepare_only_or_execute_reviewed_run")
    if args.execute_reviewed_run and (not args.hold_proof or not args.hold_nonce):
        raise SystemExit("reviewed_run_requires_exact_hold_proof_and_nonce")
    if args.execute_reviewed_run and not args.prepared_proof:
        raise SystemExit("reviewed_run_requires_prepared_ui_proof")
    if not re.fullmatch(r"QA_PENDING_UI_[0-9a-f]{12}", args.marker):
        raise SystemExit("unique_marker_format_invalid")
    if not args.task_name.endswith(args.marker[-12:]):
        raise SystemExit("task_name_must_bind_to_unique_marker")
    if args.hold_nonce and not re.fullmatch(r"[0-9a-f]{12,32}", args.hold_nonce):
        raise SystemExit("control_nonce_format_invalid")
    if args.evidence.resolve().parent != HERE.resolve() or (args.prepared_proof and args.prepared_proof.resolve().parent != HERE.resolve()) or (args.hold_proof and args.hold_proof.resolve().parent != HERE.resolve()):
        raise SystemExit("ui_evidence_must_stay_in_exclusive_pending_ui_directory")
    reserved = [args.evidence]
    if args.execute_reviewed_run:
        reserved.extend((args.evidence.with_name("pending-execution-" + args.hold_nonce + ".png"), args.evidence.with_name("success-execution-" + args.hold_nonce + ".png")))
    if any(path.exists() or path.is_symlink() for path in reserved):
        raise SystemExit("ui_evidence_path_collision")
    evidence = {"status": "STARTED", "task_id": args.task_id, "node_id": NODE, "agent_id": AGENT}
    token = None
    try:
        if args.prepare_only:
            evidence["browser_profile"] = create_owned_browser_profile()
            if browser(["open", "about:blank"], timeout=4).returncode != 0:
                raise RuntimeError("owned_browser_session_launch_failed")
            evidence["browser_cdp_guard"] = browser_cdp_guard()
            token = login_and_install_browser_token()
            exact_task(args.task_id, args.task_name, args.marker, token)
            evidence["task_exact_scope_verified"] = True
            set_task = browser(["eval", "--stdin"], "sessionStorage.setItem('qa_pending_ui_task_id'," + json.dumps(args.task_id) + "); 'task_scope_installed';", timeout=1.5)
            if set_task.returncode != 0:
                raise RuntimeError("browser_task_scope_install_failed")
            opened = browser(["open", BASE + "/tasks/" + quote(args.task_id, safe="") + "/run"], timeout=5)
            if opened.returncode != 0:
                raise RuntimeError("run_task_page_open_failed")
            browser(["wait", "--load", "networkidle"], timeout=5)
            readiness = run_page_readiness(args.task_id, args.task_name)
            evidence["run_page_readiness"] = readiness
            evidence["run_page_path"] = readiness.get("path")
            evidence["task_get_status"] = readiness.get("task_get_status")
            if readiness.get("state") != "ready":
                raise RuntimeError("run_page_" + str(readiness.get("state", "readiness_failed")))
            page = visible_text()
            if args.task_name not in page:
                raise RuntimeError("run_page_task_name_not_visible")
            evidence["run_page_visible_text_sha256"] = hashlib.sha256(page.encode()).hexdigest()
            evidence["run_page_bytes"] = len(page.encode())
            evidence["run_button"] = snapshot_meta()
            evidence["run_button"]["ref"] = ""
            for label in ("Run Now", "立即运行"):
                evidence["run_button"]["ref"] = snapshot_button_ref(label)
                if evidence["run_button"]["ref"]:
                    break
            if not evidence["run_button"]["has_run_button"]:
                raise RuntimeError("run_button_missing_from_ui")
            if not re.fullmatch(r"@e\d+", evidence["run_button"]["ref"]):
                raise RuntimeError("run_button_ref_missing_from_ui")
            evidence["status"] = "UI_PREPARED_NO_RUN"
            evidence["task_id"] = args.task_id
            evidence["task_name"] = args.task_name
            evidence["marker"] = args.marker
            evidence["browser_session"] = SESSION
            evidence["cdp_port"] = evidence["browser_cdp_guard"]["port"]
            evidence["authenticated_role"] = "administrator"
            evidence["run_page_path"] = "/tasks/" + args.task_id + "/run"
            evidence["browser_session_left_open"] = True
            evidence["run_clicked"] = False
        else:
            prepared_proof = check_prepared_proof(args.prepared_proof, args)
            evidence["prepared_ui_proof"] = {k: prepared_proof.get(k) for k in ("status", "task_id", "task_name", "marker", "browser_session", "cdp_port")}
            evidence["browser_profile"] = prepared_proof.get("browser_profile")
            evidence["hold_proof"] = check_hold_proof(args.hold_proof, args.task_id, args.hold_nonce)
            current_url = browser(["get", "url"], timeout=1.5)
            expected_url = BASE + "/tasks/" + quote(args.task_id, safe="") + "/run"
            if current_url.returncode != 0 or current_url.stdout.strip() != expected_url:
                raise RuntimeError("preopened_run_page_url_mismatch")
            evidence["prearmed_browser_url"] = expected_url
            evidence["run_clicked"] = False
            click_run_once()
            evidence["run_clicked"] = True
            exec_link = ""
            exec_deadline = time.monotonic() + 1.5
            while time.monotonic() < exec_deadline and not exec_link:
                exec_link = execution_link_for_task(args.task_name)
                if not exec_link:
                    time.sleep(0.1)
            if not re.fullmatch(r"/executions/[0-9a-f-]{36}", exec_link):
                raise RuntimeError("exact_task_execution_link_missing_from_ui")
            execution_id = exec_link.rsplit("/", 1)[1]
            evidence["execution_id"] = execution_id
            detail_open = browser(["open", BASE + exec_link], timeout=3)
            if detail_open.returncode != 0:
                raise RuntimeError("execution_detail_open_failed")
            detail_started = time.monotonic()
            pending_text = ""
            pending_state = {}
            while time.monotonic() - detail_started < 1.5:
                pending_text = visible_text()
                pending_state = execution_ui_state()
                if canonical_badge(pending_state.get("statusCard", "")):
                    break
                time.sleep(0.1)
            api_initial = browser_execution_oracle(execution_id, args.task_id)
            evidence["api_execution_identity"] = {k: api_initial.get(k) for k in ("id", "task_id", "node_id")}
            evidence["api_initial_status"] = api_initial.get("status")
            if api_initial.get("id") != execution_id or api_initial.get("task_id") != args.task_id or api_initial.get("node_id") != NODE:
                raise RuntimeError("api_execution_identity_oracle_mismatch")
            evidence["pending_ui_text_sha256"] = hashlib.sha256(pending_text.encode()).hexdigest()
            evidence["pending_ui_bytes"] = len(pending_text.encode())
            evidence["execution_id_visible"] = execution_id in pending_text
            evidence["pending_status_badge"] = pending_state.get("statusCard", "")
            evidence["pending_status"] = canonical_badge(pending_state.get("statusCard", ""))
            evidence["pending_status_visible"] = evidence["pending_status"] == "PENDING"
            evidence["detail_navigation_entry_count"] = pending_state.get("navigationEntries")
            evidence["detail_history_length"] = pending_state.get("historyLength")
            evidence["detail_time_origin"] = pending_state.get("timeOrigin")
            evidence["initial_execution_detail_path"] = pending_state.get("url")
            evidence["observed_status_badges"] = [{"status": evidence["pending_status"], "badge": evidence["pending_status_badge"], "at_utc": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "elapsed_monotonic_seconds": round(time.monotonic() - detail_started, 4)}]
            shot = args.evidence.with_name("pending-execution-" + args.hold_nonce + ".png")
            screenshot = browser(["screenshot", "body", str(shot)], timeout=3)
            evidence["pending_screenshot"] = {"written": screenshot.returncode == 0 and shot.exists(), "sha256": hashlib.sha256(shot.read_bytes()).hexdigest() if shot.exists() else None}
            evidence["status"] = "PENDING_UI_CAPTURED" if evidence["execution_id_visible"] and evidence["pending_status_visible"] else "PENDING_UI_NOT_OBSERVED"
            # Wait for the watchdog-owned release, then let the real Execution page auto-refresh.
            final_status = None
            final_text = pending_text
            observed_status_badges = evidence["observed_status_badges"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                current_state = execution_ui_state()
                current_badge = current_state.get("statusCard", "")
                final_text = current_state.get("pageText", "")
                current_status = canonical_badge(current_badge)
                if current_status and current_status != observed_status_badges[-1].get("status"):
                    observed_status_badges.append({"status": current_status, "badge": current_badge, "at_utc": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"), "elapsed_monotonic_seconds": round(time.monotonic() - detail_started, 4)})
                if current_status == "SUCCESS" and args.marker in final_text:
                    final_status = "SUCCESS"
                    break
                if current_status in {"FAILED", "TIMED_OUT", "CANCELED", "BLOCKED"}:
                    final_status = current_status
                    break
                time.sleep(0.25)
            final_state = execution_ui_state()
            final_text = final_state.get("pageText", "")
            api_final = browser_execution_oracle(execution_id, args.task_id)
            evidence["final_status_visible"] = final_status
            evidence["observed_status_badges"] = observed_status_badges
            evidence["api_final_execution"] = {k: api_final.get(k) for k in ("id", "task_id", "node_id", "status")}
            evidence["final_status_badge"] = final_state.get("statusCard", "")
            evidence["final_detail_navigation_entry_count"] = final_state.get("navigationEntries")
            evidence["final_detail_history_length"] = final_state.get("historyLength")
            evidence["final_detail_time_origin"] = final_state.get("timeOrigin")
            evidence["final_execution_detail_path"] = final_state.get("url")
            evidence["same_execution_detail_no_reload"] = final_state.get("url") == pending_state.get("url") and final_state.get("timeOrigin") == pending_state.get("timeOrigin")
            evidence["running_status_badge_observed"] = any(entry.get("status") == "RUNNING" for entry in observed_status_badges)
            evidence["final_ui_text_sha256"] = hashlib.sha256(final_text.encode()).hexdigest()
            evidence["success_ui_visible"] = canonical_badge(final_state.get("statusCard", "")) == "SUCCESS"
            evidence["marker_ui_visible"] = args.marker in final_text
            evidence["execution_id_still_visible"] = execution_id in final_text
            final_shot = args.evidence.with_name("success-execution-" + args.hold_nonce + ".png")
            final_capture = browser(["screenshot", "body", str(final_shot)], timeout=3)
            evidence["success_screenshot"] = {"written": final_capture.returncode == 0 and final_shot.exists(), "sha256": hashlib.sha256(final_shot.read_bytes()).hexdigest() if final_shot.exists() else None}
            if evidence["status"] == "PENDING_UI_CAPTURED" and final_status == "SUCCESS" and api_final.get("id") == execution_id and api_final.get("task_id") == args.task_id and api_final.get("node_id") == NODE and api_final.get("status") == "SUCCESS" and evidence["running_status_badge_observed"] and evidence["success_ui_visible"] and evidence["marker_ui_visible"] and evidence["execution_id_still_visible"] and evidence["same_execution_detail_no_reload"]:
                evidence["status"] = "PENDING_TO_SUCCESS_UI_VERIFIED"
            elif evidence["status"] == "PENDING_UI_CAPTURED":
                evidence["status"] = "PENDING_UI_CAPTURED_BUT_FULL_TRANSITION_NOT_VERIFIED"
        evidence["captured_at_utc"] = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception as error:
        evidence["status"] = "DRIVER_GUARD_FAILED"
        evidence["safe_error"] = str(error)[:120]
        evidence["captured_at_utc"] = utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    finally:
        if args.execute_reviewed_run or evidence.get("status") != "UI_PREPARED_NO_RUN":
            # The caller's finalizer clears only the exact owned RUN_EXECUTION control.
            # This UI process never edits proxy controls.
            try:
                cleanup_js = "(async()=>{const old=localStorage.getItem('nodesteer_token');let logoutStatus=0,oldMeStatus=0;if(old){const h={Authorization:'Bearer '+old,'Content-Type':'application/json'};try{logoutStatus=(await fetch('/api/logout',{method:'POST',headers:h,body:'{}'})).status}catch{};try{oldMeStatus=(await fetch('/api/me',{headers:{Authorization:'Bearer '+old}})).status}catch{}}localStorage.removeItem('nodesteer_token');sessionStorage.removeItem('qa_pending_ui_task_id');return JSON.stringify({logoutStatus,oldMeStatus,tokenPresent:!!localStorage.getItem('nodesteer_token')})})()"
                cleanup = browser(["eval", "--stdin"], cleanup_js, timeout=2)
                cleanup_result = parse_browser_json(cleanup.stdout) if cleanup.returncode == 0 else {}
                evidence["browser_token_clear_requested"] = cleanup_result.get("tokenPresent") is False
                evidence["browser_me_with_revoked_token_status"] = cleanup_result.get("oldMeStatus")
                evidence["browser_logout_status"] = cleanup_result.get("logoutStatus")
            except Exception:
                evidence["browser_token_clear_requested"] = False
            evidence["api_logout_completed"] = evidence.get("browser_logout_status") in (200, 204) and evidence.get("browser_me_with_revoked_token_status") == 401
            if not evidence["api_logout_completed"] and token:
                try:
                    evidence["fallback_api_logout_status"] = api("/api/logout", "POST", {}, token=token, allowed=(200, 204, 401))[0]
                    evidence["fallback_revoked_token_me_status"] = api("/api/me", token=token, allowed=(401,))[0]
                    evidence["api_logout_completed"] = evidence["fallback_revoked_token_me_status"] == 401
                except Exception:
                    evidence["api_logout_completed"] = False
            try:
                browser(["close"], timeout=3)
                evidence["browser_session_closed"] = True
                if evidence.get("browser_profile"):
                    evidence["browser_profile_removed"] = remove_owned_browser_profile(evidence["browser_profile"])
            except Exception:
                evidence["browser_session_closed"] = False
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    os.chmod(args.evidence, 0o600)
    print(json.dumps({k: v for k, v in evidence.items() if k not in {"hold_proof"}}, ensure_ascii=False))
    return 0 if evidence["status"] in {"UI_PREPARED_NO_RUN", "PENDING_TO_SUCCESS_UI_VERIFIED"} else 2


if __name__ == "__main__":
    sys.exit(main())
