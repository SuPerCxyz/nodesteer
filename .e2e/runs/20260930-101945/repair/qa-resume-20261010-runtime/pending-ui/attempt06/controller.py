#!/usr/bin/env python3
"""Local authenticated coordinator for one preapproved owned-child PENDING UI run."""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
import re
import shlex
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[6]
CONNECTION_FILE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
PRIVATE_CREDENTIALS_FILE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json")
TEST_DATA_FILE = PROJECT_ROOT / ".e2e/test-data.json"
BASE_EXPECTED = "http://192.168.100.209:18080"
NODE = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
AGENT = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
PARENT = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
PROTECTED = {
    "91f44866-8a28-435e-bbda-2f2367126c00": "agent-d21d5e7d-0ff0-4355-b8de-4889dc865343",
    "611c02a5-1859-4465-878b-d543b7f6fdec": "agent-ef905092-34f1-4850-b0c9-445e6e87ff97",
    "37989297-3bc8-4c0c-b302-7c6efbdba630": "agent-bb005487-7d84-418c-b598-f93d8b0c86a0",
    PARENT: "agent-bbd350f8-69f9-4406-923c-4f9c335e6962",
}
opener = build_opener(ProxyHandler({}))


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_private_connection() -> tuple[str, str]:
    if CONNECTION_FILE.is_symlink():
        raise RuntimeError("private_connection_symlink_rejected")
    info = CONNECTION_FILE.stat()
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("private_connection_mode_not_0600")
    private = json.loads(CONNECTION_FILE.read_text())
    base = str(private.get("hub_url", "")).rstrip("/")
    token = private.get("token")
    if base != BASE_EXPECTED or not isinstance(token, str) or not token:
        raise RuntimeError("repair_endpoint_or_session_guard_failed")
    return base, token


def latest_owner_manifest_sha256() -> tuple[str, str]:
    """Read the root-maintained latest owner pointer and verify its linked evidence."""
    if TEST_DATA_FILE.is_symlink():
        raise RuntimeError("latest_owner_pointer_symlink_rejected")
    data = json.loads(TEST_DATA_FILE.read_text())
    child = data["repair_environment_state"]["backend_child_20261008"]
    guard = child["latest_owner_guard"]
    expected_sha = guard.get("owner_manifest_sha256")
    evidence_rel = child.get("latest_owner_evidence")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or not isinstance(evidence_rel, str):
        raise RuntimeError("latest_owner_pointer_shape_invalid")
    evidence_candidate = PROJECT_ROOT / evidence_rel
    if evidence_candidate.is_symlink() or not evidence_candidate.is_file():
        raise RuntimeError("latest_owner_evidence_file_guard_failed")
    evidence_path = evidence_candidate.resolve(strict=True)
    if not evidence_path.is_relative_to(PROJECT_ROOT / ".e2e/runs/20260930-101945/repair/qa-resume-20261010-runtime/ui-native3/restart"):
        raise RuntimeError("latest_owner_evidence_path_outside_restart_scope")
    detail = json.loads(evidence_path.read_text())
    result = detail.get("result") or {}
    if result.get("node_id") != NODE or result.get("agent_id") != AGENT or result.get("owner_manifest_sha256_after") != expected_sha:
        raise RuntimeError("latest_owner_pointer_evidence_mismatch")
    return expected_sha, evidence_rel


def api(base: str, token: str, method: str, path: str, body=None, allowed=(200,)):
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with opener.open(Request(base + path, data=data, headers=headers, method=method), timeout=6) as response:
            status, raw = response.status, response.read()
    except HTTPError as error:
        status, raw = error.code, error.read()
    except URLError as error:
        raise RuntimeError("repair_api_unavailable") from error
    if status not in allowed:
        raise RuntimeError("repair_api_" + method.lower() + "_http_" + str(status))
    return status, json.loads(raw) if raw else None


def refresh_private_session(base: str, token: str) -> tuple[str, bool]:
    status, me = api(base, token, "GET", "/api/me", allowed=(200, 401))
    if status == 200:
        role = me.get("Role", me.get("role")) if isinstance(me, dict) else None
        if role != "administrator":
            raise RuntimeError("private_connection_role_not_administrator")
        return token, False
    if PRIVATE_CREDENTIALS_FILE.is_symlink() or stat.S_IMODE(PRIVATE_CREDENTIALS_FILE.stat().st_mode) != 0o600:
        raise RuntimeError("private_credentials_file_guard_failed")
    credentials = json.loads(PRIVATE_CREDENTIALS_FILE.read_text())
    username, password = credentials.get("admin_username"), credentials.get("admin_password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        raise RuntimeError("private_login_fields_missing")
    _, login = api(base, "", "POST", "/api/login", {"username": username, "password": password})
    fresh = login.get("token") or login.get("Token")
    if not isinstance(fresh, str) or not fresh:
        raise RuntimeError("private_login_token_missing")
    try:
        _, fresh_me = api(base, fresh, "GET", "/api/me")
        role = fresh_me.get("Role", fresh_me.get("role")) if isinstance(fresh_me, dict) else None
        if role != "administrator":
            raise RuntimeError("fresh_private_session_role_not_administrator")
        return fresh, True
    except Exception:
        try:
            api(base, fresh, "POST", "/api/logout", {}, allowed=(200, 204, 401))
            api(base, fresh, "GET", "/api/me", allowed=(401,))
        except Exception:
            pass
        raise


def logout_private_session(base: str, token: str) -> dict:
    logout_status = api(base, token, "POST", "/api/logout", {}, allowed=(200, 204, 401))[0]
    me_status = api(base, token, "GET", "/api/me", allowed=(401, 200))[0]
    return {"logout_status": logout_status, "same_token_me_status": me_status, "verified": logout_status in (200, 204) and me_status == 401}


def child_task_guard(base: str, token: str, args) -> dict:
    _, task = api(base, token, "GET", "/api/tasks/" + quote(args.task_id, safe=""))
    target = task.get("target") or {}
    node_ids = target.get("node_ids") or target.get("nodeIds") or []
    command = "sleep 4; printf '%s\\n' '" + args.marker + "'"
    if task.get("id") != args.task_id or task.get("name") != args.task_name or task.get("type") != "command" or task.get("command") != command or task.get("retry") != 0 or task.get("timeout") != 15 or task.get("enabled") is not True or node_ids != [NODE] or task.get("parameters") not in ([], None):
        raise RuntimeError("child_task_exact_definition_guard_failed")
    _, all_tasks = api(base, token, "GET", "/api/tasks")
    if not isinstance(all_tasks, list):
        raise RuntimeError("task_list_shape_unrecognized")
    child_targeted = []
    for row in all_tasks:
        target_row = row.get("target") or {}
        if NODE in (target_row.get("node_ids") or target_row.get("nodeIds") or []):
            child_targeted.append(row.get("id"))
    if child_targeted != [args.task_id]:
        raise RuntimeError("child_targeted_task_cardinality_mismatch")
    _, schedules = api(base, token, "GET", "/api/schedules")
    if not isinstance(schedules, list) or any(row.get("task_id") in child_targeted for row in schedules if isinstance(row, dict)):
        raise RuntimeError("child_task_schedule_guard_failed")
    return {"task_id": args.task_id, "task_name": args.task_name, "node_id": NODE, "retry": 0, "timeout": 15, "child_targeted_task_count": 1, "child_schedule_count": 0, "command_sha256": digest(command.encode())}


def fresh_api_guard(base: str, token: str, args) -> dict:
    nodes = {}
    for node_id, expected_agent in {**PROTECTED, NODE: AGENT}.items():
        _, node = api(base, token, "GET", "/api/nodes/" + node_id)
        if node.get("id") != node_id or node.get("agent_id") != expected_agent or node.get("status") != "online" or node.get("sync_status") != "synced":
            raise RuntimeError("protected_node_identity_or_health_guard_failed")
        nodes[node_id] = {"agent_id": expected_agent, "status": node["status"], "sync_status": node["sync_status"], "global_revision": node.get("global_revision")}
    child_task = child_task_guard(base, token, args)
    pending = api(base, token, "GET", "/api/executions?" + urlencode({"node_id": NODE, "status": "PENDING", "limit": 100, "offset": 0}))[1]
    running = api(base, token, "GET", "/api/executions?" + urlencode({"node_id": NODE, "status": "RUNNING", "limit": 100, "offset": 0}))[1]
    if not isinstance(pending, list) or not isinstance(running, list) or len(pending) >= 100 or len(running) >= 100 or pending or running:
        raise RuntimeError("child_inflight_execution_guard_failed")
    return {"checked_at_utc": utc(), "endpoint_port": 18080, "nodes": nodes, "child_task": child_task, "child_pending_count": 0, "child_running_count": 0, "credentials_emitted": False}


def helper_command(args) -> tuple[str, str, str]:
    arm_bytes = (HERE / "arm_control.py").read_bytes()
    watchdog_bytes = (HERE / "watchdog.py").read_bytes()
    arm_sha, watchdog_sha = digest(arm_bytes), digest(watchdog_bytes)
    code = "exec(compile(" + repr(arm_bytes.decode()) + ", '<pending-ui-arm-helper>', 'exec'))"
    command = "python3 -c " + shlex.quote(code)
    values = {
        "--execute-reviewed-arm": None,
        "--hold-nonce": args.hold_nonce,
        "--task-id": args.task_id,
        "--task-name": args.task_name,
        "--marker": args.marker,
        "--root": args.root,
        "--root-dev": str(args.root_dev),
        "--root-inode": str(args.root_inode),
        "--root-uid": str(args.root_uid),
        "--root-gid": str(args.root_gid),
        "--manifest-sha256": args.manifest_sha256,
        "--config-path": args.config_path,
        "--config-sha256": args.config_sha256,
        "--db-dev": str(args.db_dev),
        "--db-inode": str(args.db_inode),
        "--child-pid": str(args.child_pid),
        "--child-startticks": str(args.child_startticks),
        "--child-uid": str(args.child_uid),
        "--child-cgroup": args.child_cgroup,
        "--proxy-pid": str(args.proxy_pid),
        "--proxy-startticks": str(args.proxy_startticks),
        "--proxy-uid": str(args.proxy_uid),
        "--proxy-cgroup": args.proxy_cgroup,
        "--watchdog-source-b64": base64.b64encode(watchdog_bytes).decode("ascii"),
        "--watchdog-source-sha256": watchdog_sha,
        "--evidence": args.root + "/arm-evidence-" + args.hold_nonce + ".json",
    }
    for name, value in values.items():
        command += " " + name
        if value is not None:
            command += " " + shlex.quote(value)
    return command, arm_sha, watchdog_sha


def create_and_run_arm_helper(base: str, token: str, args, command: str, on_task_created, on_execution_created) -> tuple[str, dict]:
    task_name = "qa-resume-20261010-pending-ui-arm-" + args.hold_nonce
    definitions = api(base, token, "GET", "/api/tasks")[1]
    if any(row.get("name") == task_name for row in definitions if isinstance(row, dict)):
        raise RuntimeError("owned_arm_helper_task_name_collision")
    created = api(base, token, "POST", "/api/tasks", {
        "name": task_name,
        "type": "command",
        "command": command,
        "target": {"type": "node", "node_ids": [PARENT]},
        "parameters": [],
        "timeout": 30,
        "retry": 0,
        "offline_policy": "hub_online_required",
        "enabled": True,
    }, (200, 201))[1]
    helper_id = created.get("id")
    if not isinstance(helper_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", helper_id):
        raise RuntimeError("arm_helper_task_id_invalid")
    on_task_created(helper_id, task_name)
    readback = api(base, token, "GET", "/api/tasks/" + helper_id)[1]
    if readback.get("id") != helper_id or readback.get("name") != task_name or readback.get("command") != command or readback.get("type") != "command" or readback.get("retry") != 0 or readback.get("timeout") != 30 or (readback.get("target") or {}).get("node_ids") != [PARENT]:
        raise RuntimeError("arm_helper_task_readback_mismatch")
    _, schedules = api(base, token, "GET", "/api/schedules")
    if any(row.get("task_id") == helper_id for row in schedules if isinstance(row, dict)):
        raise RuntimeError("arm_helper_task_unexpected_schedule")

    # Creating the parent helper advances the Hub revision. Wait for the child and original
    # Native Nodes to converge, then perform the last live API guard immediately before run.
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            fresh_api_guard(base, token, args)
            break
        except RuntimeError as error:
            if "protected_node_identity_or_health_guard_failed" not in str(error):
                raise
            time.sleep(1)
    else:
        raise RuntimeError("native_nodes_did_not_return_online_synced_after_helper_task_create")
    guard = fresh_api_guard(base, token, args)
    run_result = api(base, token, "POST", "/api/tasks/" + helper_id + "/run", {"params": {}}, (200, 201))[1]
    rows = run_result if isinstance(run_result, list) else [run_result] if isinstance(run_result, dict) and run_result.get("id") else []
    if len(rows) != 1 or not isinstance(rows[0].get("id"), str):
        raise RuntimeError("arm_helper_run_cardinality_mismatch")
    execution_id = rows[0]["id"]
    on_execution_created(execution_id)
    poll_deadline = time.monotonic() + 12
    execution = None
    while time.monotonic() < poll_deadline:
        execution = api(base, token, "GET", "/api/executions/" + execution_id)[1]
        if execution.get("status") in {"SUCCESS", "FAILED", "BLOCKED", "TIMED_OUT", "CANCELED"}:
            break
        time.sleep(0.15)
    if not execution or execution.get("status") != "SUCCESS" or execution.get("node_id") != PARENT:
        raise RuntimeError("arm_helper_execution_not_success")
    stdout = execution.get("stdout") or ""
    if not stdout:
        chunks = api(base, token, "GET", "/api/executions/" + execution_id + "/logs")[1]
        if isinstance(chunks, list):
            stdout = "".join(row.get("chunk", "") for row in sorted(chunks, key=lambda row: row.get("seq", 0)) if row.get("stream") == "stdout")
    try:
        result = json.loads(stdout)
    except (TypeError, ValueError) as error:
        raise RuntimeError("arm_helper_stdout_not_structured") from error
    if result.get("status") != "HOLD_ARMED" or result.get("task_id") != args.task_id or result.get("hold_nonce") != args.hold_nonce:
        raise RuntimeError("arm_helper_did_not_confirm_exact_hold")
    return execution_id, {"helper_task_id": helper_id, "helper_task_name": task_name, "helper_execution_id": execution_id, "helper_execution_status": execution.get("status"), "helper_execution_node_id": execution.get("node_id"), "fresh_api_guard_before_run": guard, "hold_proof": result, "stdout_sha256": digest(stdout.encode()), "private_token_emitted": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute-reviewed-sequence", action="store_true")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-name", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--hold-nonce", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--root-dev", type=int, required=True)
    parser.add_argument("--root-inode", type=int, required=True)
    parser.add_argument("--root-uid", type=int, required=True)
    parser.add_argument("--root-gid", type=int, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--db-dev", type=int, required=True)
    parser.add_argument("--db-inode", type=int, required=True)
    parser.add_argument("--child-pid", type=int, required=True)
    parser.add_argument("--child-startticks", type=int, required=True)
    parser.add_argument("--child-uid", type=int, required=True)
    parser.add_argument("--child-cgroup", required=True)
    parser.add_argument("--proxy-pid", type=int, required=True)
    parser.add_argument("--proxy-startticks", type=int, required=True)
    parser.add_argument("--proxy-uid", type=int, required=True)
    parser.add_argument("--proxy-cgroup", required=True)
    parser.add_argument("--prepared-proof", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, default=HERE / "controller-evidence.json")
    args = parser.parse_args()
    if not args.execute_reviewed_sequence:
        raise SystemExit("refusing_without_explicit_execute_reviewed_sequence")
    if not re.fullmatch(r"[0-9a-f]{12}", args.hold_nonce) or not re.fullmatch(r"QA_PENDING_UI_[0-9a-f]{12}", args.marker) or not args.task_name.endswith(args.marker[-12:]):
        raise SystemExit("task_marker_or_control_nonce_guard_failed")
    if args.prepared_proof.resolve().parent != HERE or args.evidence.resolve().parent != HERE:
        raise SystemExit("controller_evidence_outside_exclusive_directory")
    reserved_paths = [args.evidence, HERE / ("hold-proof-" + args.hold_nonce + ".json"), HERE / ("ui-evidence-" + args.hold_nonce + ".json"), HERE / ("pending-execution-" + args.hold_nonce + ".png"), HERE / ("success-execution-" + args.hold_nonce + ".png")]
    if any(path.exists() or path.is_symlink() for path in reserved_paths):
        raise SystemExit("controller_evidence_path_collision")
    def cleanup_prepared_browser():
        try:
            prepared_proof = json.loads(args.prepared_proof.read_text())
            spec = importlib.util.spec_from_file_location("pending_ui_cleanup", HERE / "ui_driver.py")
            cleanup_ui = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cleanup_ui)
            return cleanup_ui.cleanup_prepared_browser_session(prepared_proof)
        except Exception:
            return {"status": "CLEANUP_PARTIAL", "safe_error": "prepared_browser_cleanup_failed"}
    base = token = None
    token_owned_by_controller = False
    try:
        latest_manifest_sha, latest_owner_evidence = latest_owner_manifest_sha256()
        if args.manifest_sha256 != latest_manifest_sha:
            raise RuntimeError("stale_manifest_sha_input")
        command, arm_sha, watchdog_sha = helper_command(args)
        base, token = load_private_connection()
        token, token_owned_by_controller = refresh_private_session(base, token)
    except Exception as error:
        out = {
            "status": "REFUSED_STALE_MANIFEST_SHA" if str(error) == "stale_manifest_sha_input" else "PREFLIGHT_FAILED",
            "captured_at_utc": utc(),
            "safe_error": str(error)[:120],
            "browser_cleanup": cleanup_prepared_browser(),
            "helper_task_created": False,
            "hold_armed": False,
            "run_clicked": False,
        }
        if token_owned_by_controller and base and token:
            try:
                out["controller_api_session_cleanup"] = logout_private_session(base, token)
            except Exception:
                out["controller_api_session_cleanup"] = {"verified": False, "safe_error": "private_session_logout_failed"}
        args.evidence.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
        os.chmod(args.evidence, 0o600)
        print(json.dumps(out, ensure_ascii=False))
        return 2
    out = {"status": "STARTED", "captured_at_utc": utc(), "task_id": args.task_id, "node_id": NODE, "agent_id": AGENT, "arm_source_sha256": arm_sha, "watchdog_source_sha256": watchdog_sha, "latest_owner_evidence": latest_owner_evidence, "manifest_sha256_source": "test-data.latest_owner_guard verified against linked restart result", "manifest_sha256_matches_latest": True, "command_contains_credentials": False}
    helper_id = None
    helper_execution_id = None
    prepared = None
    ui = None
    ui_run_started = False
    child_ui_run_clicked = False
    def record_helper_task(task_id, task_name):
        nonlocal helper_id
        helper_id = task_id
        out["helper_task_id"] = task_id
        out["helper_task_name"] = task_name
    def record_helper_execution(execution_id):
        nonlocal helper_execution_id
        helper_execution_id = execution_id
        out["helper_execution_id"] = execution_id
    try:
        prepared = json.loads(args.prepared_proof.read_text())
        spec = importlib.util.spec_from_file_location("pending_ui_driver", HERE / "ui_driver.py")
        ui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ui)
        if prepared.get("status") != "UI_PREPARED_NO_RUN" or prepared.get("task_id") != args.task_id or prepared.get("task_name") != args.task_name or prepared.get("marker") != args.marker or prepared.get("node_id") != NODE or prepared.get("agent_id") != AGENT or prepared.get("browser_session") != "nodesteer-pending-ui-20261010" or not isinstance(prepared.get("cdp_port"), int) or prepared.get("browser_cdp_guard", {}).get("loopback") is not True or prepared.get("browser_cdp_guard", {}).get("port") != prepared.get("cdp_port") or prepared.get("authenticated_role") != "administrator" or prepared.get("task_exact_scope_verified") is not True or prepared.get("run_button", {}).get("has_run_button") is not True or prepared.get("browser_session_left_open") is not True or prepared.get("run_clicked") is not False or prepared.get("run_page_path") != "/tasks/" + args.task_id + "/run":
            raise RuntimeError("prepared_browser_proof_mismatch")
        ui.check_prepared_proof(args.prepared_proof, args)
        execution_id, arm_result = create_and_run_arm_helper(base, token, args, command, record_helper_task, record_helper_execution)
        hold_proof = dict(arm_result["hold_proof"])
        hold_proof.update({"node_id": NODE, "agent_id": AGENT})
        proof_path = HERE / ("hold-proof-" + args.hold_nonce + ".json")
        if proof_path.exists() or proof_path.is_symlink():
            raise RuntimeError("hold_proof_path_collision")
        proof_path.write_text(json.dumps(hold_proof, ensure_ascii=False, indent=2) + "\n")
        os.chmod(proof_path, 0o600)
        out.update({"status": "ARMED", "helper_task_id": helper_id, "helper_execution_id": execution_id, "fresh_api_guard_before_run": arm_result.get("fresh_api_guard_before_run"), "hold_proof": {k: hold_proof.get(k) for k in ("status", "task_id", "hold_nonce", "armed_at_utc", "control_keys", "controls_sha256", "watchdog_wait_seconds")}, "hold_proof_path": proof_path.name})

        old_argv = sys.argv
        ui_evidence = HERE / ("ui-evidence-" + args.hold_nonce + ".json")
        sys.argv = [
            str(HERE / "ui_driver.py"), "--task-id", args.task_id, "--task-name", args.task_name, "--marker", args.marker,
            "--execute-reviewed-run", "--hold-proof", str(proof_path), "--hold-nonce", args.hold_nonce,
            "--prepared-proof", str(args.prepared_proof), "--evidence", str(ui_evidence),
        ]
        ui_run_started = True
        try:
            ui_rc = ui.main()
        finally:
            sys.argv = old_argv
        if ui_evidence.exists():
            ui_result = json.loads(ui_evidence.read_text())
            out["ui_status"] = ui_result.get("status")
            out["ui_run_clicked"] = ui_result.get("run_clicked")
            child_ui_run_clicked = ui_result.get("run_clicked") is True
            out["ui_execution_id"] = ui_result.get("execution_id")
            out["ui_final_status_visible"] = ui_result.get("final_status_visible")
            out["ui_cleanup"] = {k: ui_result.get(k) for k in ("api_logout_completed", "browser_session_closed", "browser_me_with_revoked_token_status")}
        out["status"] = "COMPLETE" if ui_rc == 0 else "UI_PARTIAL_OR_FAILED"
    except Exception as error:
        out["status"] = "CONTROLLER_FAILED"
        out["safe_error"] = str(error)[:120]
    finally:
        if prepared and ui is not None and not ui_run_started:
            try:
                out["pre_ui_browser_cleanup"] = ui.cleanup_prepared_browser_session(prepared)
            except Exception:
                out["pre_ui_browser_cleanup"] = {"status": "CLEANUP_PARTIAL", "safe_error": "prepared_browser_cleanup_failed"}
        # Preserve executions/audit; remove only the unique helper definition and its own
        # child Task after it is terminal and no exact child execution remains in flight.
        if helper_id:
            try:
                _, helper_executions = api(base, token, "GET", "/api/executions?" + urlencode({"node_id": PARENT, "task_id": helper_id, "limit": 100, "offset": 0}))
                helper_active = [row.get("status") for row in helper_executions if row.get("status") in {"PENDING", "RUNNING"}]
                if helper_active:
                    out["helper_task_get404"] = False
                    out["helper_task_cleanup_reason"] = "active_helper_execution_preserved"
                else:
                    api(base, token, "DELETE", "/api/tasks/" + helper_id, allowed=(200, 204))
                    api(base, token, "GET", "/api/tasks/" + helper_id, allowed=(404,))
                    out["helper_task_get404"] = True
            except Exception:
                out["helper_task_get404"] = False
        if not child_ui_run_clicked:
            out["child_task_preserved_for_diagnosis"] = True
        else:
            try:
                _, child_execs = api(base, token, "GET", "/api/executions?" + urlencode({"node_id": NODE, "task_id": args.task_id, "limit": 100, "offset": 0}))
                active = [x for x in child_execs if x.get("status") in {"PENDING", "RUNNING"}]
                if not active:
                    task = api(base, token, "GET", "/api/tasks/" + args.task_id)[1]
                    expected_command = "sleep 4; printf '%s\\n' '" + args.marker + "'"
                    if task.get("name") == args.task_name and task.get("type") == "command" and task.get("command") == expected_command and task.get("retry") == 0 and task.get("target", {}).get("node_ids") == [NODE]:
                        api(base, token, "DELETE", "/api/tasks/" + args.task_id, allowed=(200, 204))
                        api(base, token, "GET", "/api/tasks/" + args.task_id, allowed=(404,))
                        out["child_task_get404"] = True
                    else:
                        out["child_task_get404"] = False
                else:
                    out["child_task_get404"] = False
                    out["child_task_cleanup_reason"] = "active_child_execution_preserved"
            except Exception:
                out["child_task_get404"] = False
        if token_owned_by_controller:
            try:
                out["controller_api_session_cleanup"] = logout_private_session(base, token)
            except Exception:
                out["controller_api_session_cleanup"] = {"verified": False, "safe_error": "private_session_logout_failed"}
    out["completed_at_utc"] = utc()
    args.evidence.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    os.chmod(args.evidence, 0o600)
    print(json.dumps({k: v for k, v in out.items() if k not in {"hold_proof"}}, ensure_ascii=False))
    return 0 if out.get("status") == "COMPLETE" and out.get("helper_task_get404") and out.get("child_task_get404") else 2


if __name__ == "__main__":
    sys.exit(main())
