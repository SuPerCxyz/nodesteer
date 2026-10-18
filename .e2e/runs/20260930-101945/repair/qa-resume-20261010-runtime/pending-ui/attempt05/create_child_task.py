#!/usr/bin/env python3
"""Create one exact, unscheduled command Task for the approved PENDING UI probe."""
import hashlib
import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
CONNECTION = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
PRIVATE_CREDENTIALS = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json")
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


def api(base: str, token: str, method: str, path: str, body=None, allowed=(200,)):
    headers = {"Authorization": "Bearer " + token} if token else {}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    try:
        with opener.open(Request(base + path, data=data, headers=headers, method=method), timeout=7) as response:
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
    if PRIVATE_CREDENTIALS.is_symlink() or stat.S_IMODE(PRIVATE_CREDENTIALS.stat().st_mode) != 0o600:
        raise RuntimeError("private_credentials_file_guard_failed")
    credentials = json.loads(PRIVATE_CREDENTIALS.read_text())
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


def write_reserved(path: Path, value: dict, exclusive=False):
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def main():
    connection = json.loads(CONNECTION.read_text())
    info = CONNECTION.stat()
    if CONNECTION.is_symlink() or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("private_connection_mode_or_path_guard_failed")
    base = str(connection.get("hub_url", "")).rstrip("/")
    token = connection.get("token")
    if base != BASE_EXPECTED or not isinstance(token, str) or not token:
        raise RuntimeError("private_repair_endpoint_guard_failed")

    nonce = secrets.token_hex(6)
    marker = "QA_PENDING_UI_" + nonce
    task_name = "qa-resume-20261010-pending-ui-" + nonce
    task_id = None
    token_owned_by_task_creator = False
    ledger = HERE / "child-task-ledger.json"
    if ledger.exists() or ledger.is_symlink():
        raise RuntimeError("child_task_ledger_collision")
    command = "sleep 4; printf '%s\\n' '" + marker + "'"
    intent = {
        "phase": "CREATE_INTENT",
        "created_at_utc": utc(),
        "node_id": NODE,
        "agent_id": AGENT,
        "task_name": task_name,
        "marker": marker,
        "task_type": "command",
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "retry": 0,
        "timeout_seconds": 15,
        "schedule_expected": False,
        "credential_values_recorded": False,
    }
    write_reserved(ledger, intent, exclusive=True)
    try:
        token, token_owned_by_task_creator = refresh_private_session(base, token)
        _, me = api(base, token, "GET", "/api/me")
        role = me.get("Role", me.get("role")) if isinstance(me, dict) else None
        if role != "administrator":
            raise RuntimeError("private_connection_role_not_administrator")
        nodes = {}
        for node_id, agent_id in {**PROTECTED, NODE: AGENT}.items():
            _, node = api(base, token, "GET", "/api/nodes/" + node_id)
            if node.get("id") != node_id or node.get("agent_id") != agent_id or node.get("status") != "online" or node.get("sync_status") != "synced":
                raise RuntimeError("native_node_health_or_identity_guard_failed")
            nodes[node_id] = {"agent_id": agent_id, "status": node["status"], "sync_status": node["sync_status"], "global_revision": node.get("global_revision")}
        tasks = api(base, token, "GET", "/api/tasks")[1]
        if not isinstance(tasks, list) or any(row.get("name") == task_name for row in tasks):
            raise RuntimeError("task_name_collision_or_task_list_shape")
        if any(NODE in ((row.get("target") or {}).get("node_ids") or (row.get("target") or {}).get("nodeIds") or []) for row in tasks):
            raise RuntimeError("child_already_has_targeted_task")
        schedules = api(base, token, "GET", "/api/schedules")[1]
        if not isinstance(schedules, list) or any(NODE in ((row.get("target") or {}).get("node_ids") or []) for row in schedules):
            raise RuntimeError("child_has_schedule_or_schedule_list_shape")
        for status in ("PENDING", "RUNNING"):
            rows = api(base, token, "GET", "/api/executions?node_id=" + NODE + "&status=" + status + "&limit=100&offset=0")[1]
            if not isinstance(rows, list) or rows:
                raise RuntimeError("child_inflight_execution_guard_failed")
        body = {
            "name": task_name,
            "type": "command",
            "command": command,
            "target": {"type": "node", "node_ids": [NODE]},
            "parameters": [],
            "timeout": 15,
            "retry": 0,
            "offline_policy": "hub_online_required",
            "enabled": True,
        }
        _, created = api(base, token, "POST", "/api/tasks", body, (200, 201))
        task_id = created.get("id")
        if not isinstance(task_id, str) or len(task_id) != 36:
            raise RuntimeError("created_task_id_invalid")
        intent.update({"phase": "CREATED_NEEDS_READBACK", "task_id": task_id, "created_at_utc": utc()})
        write_reserved(ledger, intent)
        _, task = api(base, token, "GET", "/api/tasks/" + quote(task_id, safe=""))
        target = task.get("target") or {}
        node_ids = target.get("node_ids") or target.get("nodeIds") or []
        if task.get("id") != task_id or task.get("name") != task_name or task.get("type") != "command" or task.get("command") != command or task.get("retry") != 0 or task.get("timeout") != 15 or task.get("enabled") is not True or node_ids != [NODE] or task.get("parameters") not in ([], None):
            raise RuntimeError("created_task_exact_readback_mismatch")
        tasks = api(base, token, "GET", "/api/tasks")[1]
        child_targeted = [row.get("id") for row in tasks if NODE in (((row.get("target") or {}).get("node_ids") or (row.get("target") or {}).get("nodeIds") or []))]
        schedules = api(base, token, "GET", "/api/schedules")[1]
        if child_targeted != [task_id] or any(row.get("task_id") == task_id for row in schedules):
            raise RuntimeError("created_task_count_or_schedule_guard_failed")
        intent.update({"phase": "TASK_CREATED_AND_VERIFIED", "verified_at_utc": utc(), "child_targeted_task_ids": child_targeted, "schedule_count": 0, "preflight_nodes": nodes})
        write_reserved(ledger, intent)
        print(json.dumps({"status": intent["phase"], "task_id": task_id, "task_name": task_name, "marker": marker, "node_id": NODE, "retry": 0, "timeout_seconds": 15, "schedule_count": 0, "task_command_sha256": intent["command_sha256"], "evidence": ledger.name}, ensure_ascii=False))
        return 0
    except Exception as error:
        if task_id is None:
            try:
                candidates = api(base, token, "GET", "/api/tasks")[1]
                matches = [row for row in candidates if row.get("name") == task_name]
                if len(matches) == 1 and matches[0].get("type") == "command" and matches[0].get("command") == command and (matches[0].get("target") or {}).get("node_ids") == [NODE]:
                    task_id = matches[0].get("id")
            except Exception:
                pass
        intent.update({"phase": "CREATE_OR_VERIFY_FAILED", "safe_error": str(error)[:100], "failed_at_utc": utc(), "task_id": task_id})
        write_reserved(ledger, intent)
        print(json.dumps({"status": intent["phase"], "task_id": task_id, "task_name": task_name, "safe_error": intent["safe_error"], "evidence": ledger.name}, ensure_ascii=False))
        return 2
    finally:
        if token_owned_by_task_creator:
            try:
                intent["private_api_session_cleanup"] = logout_private_session(base, token)
            except Exception:
                intent["private_api_session_cleanup"] = {"verified": False, "safe_error": "private_session_logout_failed"}
            write_reserved(ledger, intent)


if __name__ == "__main__":
    raise SystemExit(main())
