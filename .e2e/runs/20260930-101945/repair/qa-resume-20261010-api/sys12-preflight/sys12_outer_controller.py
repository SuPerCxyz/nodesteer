#!/usr/bin/env python3
"""Reviewed SYS-12 orchestration draft. No remote action without --execute and a Root grant ID."""
import argparse
import hashlib
import json
import os
import re
import shlex
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
LIFECYCLE = HERE / "lifecycle_source_proposal.py"
TARGET_NATIVE2 = "611c02a5-1859-4465-878b-d543b7f6fdec"
REPAIR_HOST = "192.168.100.209"
REPAIR_PORT = 18080
SHELL_SHA = "a6f559e00b69a4aa4d8cb607be18d9386c5aee55c509e2c075549dcf00e00fc7"
NORMAL_HELPER_BUDGET = 6
RECOVERY_HELPER_BUDGET = 4
TOTAL_HELPER_BUDGET = NORMAL_HELPER_BUDGET + RECOVERY_HELPER_BUDGET
MAX_BODY = 2 * 1024 * 1024
TERMINAL_EXECUTION_STATES = {"SUCCESS", "FAILED", "SKIPPED", "BLOCKED", "TIMED_OUT", "CANCELED"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GateError(RuntimeError):
    pass


class ActiveHelperBlocked(GateError):
    pass


def block_active_helper(ledger, rec):
    rec["execution_reconcile"] = "OWNED_ACTIVE_HELPER_BLOCKED"
    ledger["status"] = "OWNED_ACTIVE_HELPER_BLOCKED"
    ledger["phase"] = "OWNED_ACTIVE_HELPER_BLOCKED"
    ledger["last_safe_error"] = "OWNED_ACTIVE_HELPER_BLOCKED"
    try:
        save(ledger["ledger_path"], ledger)
        result_path = Path(ledger["result_path"])
        if result_path.exists():
            blocked_result = read_owned_evidence(result_path)
            blocked_result["status"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            blocked_result["safe_error"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            save(result_path, blocked_result)
        save(ledger["progress_path"], {"updated_at_utc": utc(),
                                       "status": "OWNED_ACTIVE_HELPER_BLOCKED",
                                       "next_action": "stop all remote mutations; Root must reconcile exact helper Execution",
                                       "nonce": ledger["nonce"]})
    except Exception as exc:
        rec["block_evidence_write_error"] = type(exc).__name__
    raise ActiveHelperBlocked("OWNED_ACTIVE_HELPER_BLOCKED")


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as f:
            f.write(data)
            f.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temp, path)


def set_phase(ledger, status, next_action):
    ledger["phase"] = status
    ledger["updated_at_utc"] = utc()
    save(ledger["ledger_path"], ledger)
    save(ledger["progress_path"], {"updated_at_utc": utc(), "status": status,
                                   "next_action": next_action, "nonce": ledger["nonce"]})


def read_private(path):
    p = Path(path)
    expected_uid = os.geteuid()
    st = p.lstat()
    if (not stat.S_ISREG(st.st_mode) or p.is_symlink() or st.st_uid != expected_uid
            or stat.S_IMODE(st.st_mode) != 0o600):
        raise GateError("private_connection_owner_mode_guard_failed")
    parent = p.parent
    ps = parent.lstat()
    if (not stat.S_ISDIR(ps.st_mode) or parent.is_symlink() or parent.resolve() != parent
            or p.resolve() != p or ps.st_uid != expected_uid or stat.S_IMODE(ps.st_mode) != 0o700):
        raise GateError("private_connection_directory_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fst = os.fstat(fd)
        if (fst.st_dev, fst.st_ino, fst.st_uid, stat.S_IMODE(fst.st_mode)) != (
                st.st_dev, st.st_ino, expected_uid, 0o600):
            raise GateError("private_connection_changed_during_open")
        raw = os.read(fd, 65537)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise GateError("private_connection_size_limit")
    obj = json.loads(raw)
    base = obj.get("hub_url", "").rstrip("/")
    token = obj.get("token")
    u = urlsplit(base)
    if (u.scheme != "http" or u.hostname != REPAIR_HOST or u.port != REPAIR_PORT
            or u.username or u.password or u.query or u.fragment or u.path not in {"", "/"}
            or not isinstance(token, str) or not token):
        raise GateError("repair_connection_authority_guard_failed")
    return base, token


class API:
    def __init__(self, base, token):
        self.base = base
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        self.on_response = None
        self.progress_path = None
        self.nonce = None

    def progress(self, status, next_action):
        if self.progress_path:
            save(self.progress_path, {"updated_at_utc": utc(), "status": status,
                                      "next_action": next_action, "nonce": self.nonce})

    def request(self, method, path, body=None, timeout=12, limit=MAX_BODY):
        headers = {"Authorization": "Bearer " + self.token}
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        req = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                code, raw = resp.status, resp.read(limit + 1)
        except HTTPError as exc:
            code, raw = exc.code, exc.read(limit + 1)
        except (URLError, TimeoutError, OSError):
            raise GateError("api_request_transport_failed")
        if len(raw) > limit:
            raise GateError("api_response_size_limit")
        try:
            obj = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            obj = None
        meta = {"status": code, "bytes": len(raw), "json_type": type(obj).__name__ if obj is not None else "empty_or_invalid",
                "body_sha256": sha(raw)}
        if self.on_response:
            self.on_response({"method": method, "path": path, **meta})
        return code, obj, meta


def require(code, expected, label):
    if code != expected:
        raise GateError(label + "_unexpected_http_status")


def list_rows(obj, keys=()):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in keys:
            if isinstance(obj.get(key), list):
                return obj[key]
    return None


def get_node(api, node_id):
    code, obj, _ = api.request("GET", "/api/nodes/" + node_id)
    require(code, 200, "node_get")
    if not isinstance(obj, dict) or obj.get("id") != node_id:
        raise GateError("node_get_identity_mismatch")
    return obj


def require_node_healthy(node, expected_id, expected_name=None):
    if node.get("id") != expected_id or node.get("status") != "online" or node.get("sync_status") != "synced":
        raise GateError("node_not_online_synced")
    if expected_name and node.get("hostname") != expected_name:
        raise GateError("node_hostname_mismatch")
    if node.get("deployment_mode") != "docker" or node.get("host_integration") is not False:
        raise GateError("new_node_mode_or_host_integration_mismatch")
    if not isinstance(node.get("agent_id"), str) or not node.get("agent_id"):
        raise GateError("node_agent_id_missing")
    if not isinstance(node.get("global_revision"), int):
        raise GateError("node_global_revision_missing")


def wait_node(api, node_id, node_name, timeout=75):
    deadline = time.monotonic() + timeout
    next_progress = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        last = get_node(api, node_id)
        if last.get("status") == "online" and last.get("sync_status") == "synced":
            require_node_healthy(last, node_id, node_name)
            return last
        if time.monotonic() >= next_progress:
            api.progress("WAITING_FOR_OWNED_NODE_SYNC", "poll only the owned Node until online/synced")
            next_progress = time.monotonic() + 15
        time.sleep(0.5)
    raise GateError("owned_node_sync_timeout")


def synced_node_snapshot(api, node_id, node_name):
    node = wait_node(api, node_id, node_name)
    return {"id": node["id"], "agent_id": node["agent_id"],
            "status": node["status"], "sync_status": node["sync_status"],
            "global_revision": node["global_revision"]}


def wait_node_by_name(api, node_name, timeout=45):
    deadline = time.monotonic() + timeout
    next_progress = time.monotonic() + 15
    while time.monotonic() < deadline:
        code, obj, _ = api.request("GET", "/api/nodes")
        require(code, 200, "nodes_list")
        rows = list_rows(obj, ("nodes", "items"))
        if rows is None:
            raise GateError("nodes_list_shape_invalid")
        matches = [x for x in rows if isinstance(x, dict) and x.get("hostname") == node_name]
        if len(matches) > 1:
            raise GateError("owned_node_name_not_unique")
        if matches:
            node = matches[0]
            if not isinstance(node.get("id"), str) or not node.get("id"):
                raise GateError("owned_node_id_missing")
            return wait_node(api, node["id"], node_name, timeout=max(1, deadline - time.monotonic()))
        if time.monotonic() >= next_progress:
            api.progress("WAITING_FOR_FIRST_HELLO", "poll exact fresh NodeName only")
            next_progress = time.monotonic() + 15
        time.sleep(0.5)
    raise GateError("owned_node_hello_timeout")


def ensure_node_name_unused(api, node_name, before_ids):
    code, obj, _ = api.request("GET", "/api/nodes")
    require(code, 200, "pre_prepare_nodes_list")
    rows = list_rows(obj, ("nodes", "items"))
    if rows is None:
        raise GateError("pre_prepare_nodes_list_shape_invalid")
    if any(isinstance(x, dict) and x.get("hostname") == node_name for x in rows):
        raise GateError("fresh_node_name_collision")
    after_ids = {x.get("id") for x in rows if isinstance(x, dict)}
    if after_ids != before_ids:
        raise GateError("node_set_changed_during_pre_prepare_guard")
    return {"node_name_unused": True, "baseline_node_ids_unchanged": True}


def helper_command(source, phase, nonce, params):
    argv = ["python3", "-c", source, "--phase", phase, "--nonce", nonce,
            "--shell-sha256", SHELL_SHA, "--execute"]
    for key, value in params.items():
        if value is None or value == "":
            continue
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return " ".join(shlex.quote(x) for x in argv)


def ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=105):
    if not rec.get("run_requested"):
        rec["terminal_proven"] = True
        return None
    execution_id = rec.get("execution_id")
    if not execution_id:
        query = urlencode({"task_id": task_id, "limit": 10})
        try:
            code, obj, _ = api.request("GET", "/api/executions?" + query)
        except GateError as exc:
            try:
                block_active_helper(ledger, rec)
            except ActiveHelperBlocked as blocked:
                raise blocked from exc
        rows = list_rows(obj, ("executions", "items"))
        if code != 200 or rows is None:
            block_active_helper(ledger, rec)
        exact = [x for x in rows if isinstance(x, dict) and x.get("task_id") == task_id]
        if len(exact) != 1 or not isinstance(exact[0].get("id"), str) or not exact[0]["id"]:
            rec["execution_reconcile"] = "no_unique_execution_for_exact_task"
            block_active_helper(ledger, rec)
        execution_id = rec["execution_id"] = exact[0]["id"]
        save(ledger["ledger_path"], ledger)

    def read_bound():
        try:
            code, current, _ = api.request("GET", "/api/executions/" + execution_id)
        except GateError as exc:
            try:
                block_active_helper(ledger, rec)
            except ActiveHelperBlocked as blocked:
                raise blocked from exc
        if (code != 200 or not isinstance(current, dict) or current.get("id") != execution_id
                or current.get("task_id") != task_id):
            block_active_helper(ledger, rec)
        return current

    current = read_bound()
    if current.get("status") in TERMINAL_EXECUTION_STATES:
        rec["execution_status"] = current["status"]
        rec["terminal_proven"] = True
        save(ledger["ledger_path"], ledger)
        return current
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        current = read_bound()
        if current.get("status") in TERMINAL_EXECUTION_STATES:
            rec["execution_status"] = current["status"]
            rec["terminal_proven"] = True
            save(ledger["ledger_path"], ledger)
            return current
        time.sleep(0.5)
    rec["cancel_requested"] = True
    try:
        cancel_code, cancel_obj, _ = api.request("POST", "/api/executions/" + execution_id + "/cancel")
        rec["cancel_http"] = cancel_code
        rec["cancel_acknowledged"] = (cancel_code == 200 and isinstance(cancel_obj, dict)
                                      and cancel_obj.get("ok") == "true")
    except GateError:
        rec["cancel_transport_failed"] = True
    save(ledger["ledger_path"], ledger)
    cancel_deadline = time.monotonic() + 30
    while time.monotonic() < cancel_deadline:
        current = read_bound()
        if current.get("status") in TERMINAL_EXECUTION_STATES:
            rec["execution_status"] = current["status"]
            rec["terminal_proven"] = True
            save(ledger["ledger_path"], ledger)
            return current
        time.sleep(0.5)
    block_active_helper(ledger, rec)


def validate_helper_budget_ledger(ledger):
    calls = ledger.get("helper_calls")
    if not isinstance(calls, list):
        raise GateError("helper_budget_ledger_shape_invalid")
    actual = {
        "helper_call_count_total": len(calls),
        "helper_call_count_normal": sum(not bool(x.get("recovery")) for x in calls if isinstance(x, dict)),
        "helper_call_count_recovery": sum(bool(x.get("recovery")) for x in calls if isinstance(x, dict)),
    }
    if any(not isinstance(x, dict) for x in calls):
        raise GateError("helper_budget_ledger_shape_invalid")
    if any(ledger.get(k) != v for k, v in actual.items()):
        raise GateError("helper_budget_ledger_counter_mismatch")
    if (actual["helper_call_count_normal"] > NORMAL_HELPER_BUDGET
            or actual["helper_call_count_recovery"] > RECOVERY_HELPER_BUDGET
            or actual["helper_call_count_total"] > TOTAL_HELPER_BUDGET):
        raise GateError("helper_budget_ledger_exceeded")
    return actual


def run_helper(api, source, source_sha, ledger, result, phase, params, pre_create=None,
               pre_run=None, recovery=False):
    counts = validate_helper_budget_ledger(ledger)
    total = counts["helper_call_count_total"]
    normal = counts["helper_call_count_normal"]
    recovery_count = counts["helper_call_count_recovery"]
    if total >= TOTAL_HELPER_BUDGET or (recovery and recovery_count >= RECOVERY_HELPER_BUDGET) or (
            not recovery and normal >= NORMAL_HELPER_BUDGET):
        raise GateError("native2_helper_budget_exhausted")
    helper_name = "qa-sys12-helper-" + ledger["nonce"] + "-" + str(total + 1)
    command = helper_command(source, phase, ledger["nonce"], params)
    command_sha = sha(command.encode())
    rec = {"phase": phase, "task_name": helper_name, "command_sha256": command_sha,
           "source_sha256": source_sha, "task_id": None, "execution_id": None,
           "params": params, "recovery": recovery, "ledger_path": ledger["ledger_path"],
           "task_deleted_and_404": False, "terminal_proven": False, "started_at_utc": utc()}
    ledger["helper_calls"].append(rec)
    ledger["helper_call_count_total"] = total + 1
    ledger["helper_call_count_normal"] = normal + (0 if recovery else 1)
    ledger["helper_call_count_recovery"] = recovery_count + (1 if recovery else 0)
    ledger["phase"] = "helper_" + phase
    save(ledger["ledger_path"], ledger)
    save(ledger["progress_path"], {"updated_at_utc": utc(), "status": ledger["phase"],
                                   "next_action": "create and run only this Native2 helper Task"})
    result["status"] = "RUNNING_" + phase.upper()
    save(ledger["result_path"], result)
    code, node, _ = api.request("GET", "/api/nodes/" + TARGET_NATIVE2)
    require(code, 200, "native2_get")
    if not isinstance(node, dict) or node.get("id") != TARGET_NATIVE2 or node.get("status") != "online" or node.get("sync_status") != "synced":
        raise GateError("native2_not_online_synced")
    code, task_list, _ = api.request("GET", "/api/tasks")
    task_rows = list_rows(task_list, ("tasks", "items"))
    if code != 200 or task_rows is None or any(isinstance(x, dict) and x.get("name") == helper_name for x in task_rows):
        raise GateError("helper_task_name_preflight_failed")
    if pre_create:
        pre_create()
    task_id = None
    try:
        rec["task_create_requested"] = True
        save(ledger["ledger_path"], ledger)
        code, created, _ = api.request("POST", "/api/tasks", {
            "name": helper_name, "type": "command", "command": command,
            "target": {"type": "node", "node_ids": [TARGET_NATIVE2]}, "parameters": [],
            "timeout": 90, "retry": 0, "offline_policy": "hub_online_required", "enabled": True,
        })
        if code not in (200, 201) or not isinstance(created, dict) or not isinstance(created.get("id"), str):
            raise GateError("helper_task_create_failed")
        task_id = created["id"]
        rec["task_id"] = task_id
        save(ledger["ledger_path"], ledger)
        code, readback, _ = api.request("GET", "/api/tasks/" + task_id)
        target = readback.get("target", {}) if isinstance(readback, dict) else {}
        ok = (code == 200 and isinstance(readback, dict) and readback.get("id") == task_id
              and readback.get("name") == helper_name and readback.get("type") == "command"
              and sha(str(readback.get("command", "")).encode()) == command_sha
              and isinstance(target, dict) and target.get("node_ids") == [TARGET_NATIVE2]
              and readback.get("retry") == 0 and readback.get("timeout") == 90)
        if not ok:
            raise GateError("helper_task_readback_mismatch")
        if pre_run:
            rec["pre_run_node"] = pre_run()
            save(ledger["ledger_path"], ledger)
        rec["run_requested"] = True
        save(ledger["ledger_path"], ledger)
        code, started, _ = api.request("POST", "/api/tasks/" + task_id + "/run", {"params": {}})
        executions = list_rows(started, ("executions",))
        if executions is None and isinstance(started, dict) and isinstance(started.get("id"), str):
            executions = [started]
        if (code not in (200, 201) or not executions or len(executions) != 1
                or not isinstance(executions[0], dict)):
            raise GateError("helper_execution_start_shape_invalid")
        execution_id = executions[0].get("id")
        if not isinstance(execution_id, str) or not execution_id:
            raise GateError("helper_execution_id_missing")
        rec["execution_id"] = execution_id
        save(ledger["ledger_path"], ledger)
        deadline = time.monotonic() + 105
        next_progress = time.monotonic() + 15
        terminal = None
        while time.monotonic() < deadline:
            code, current, _ = api.request("GET", "/api/executions/" + execution_id)
            if code != 200 or not isinstance(current, dict) or current.get("id") != execution_id:
                raise GateError("helper_execution_readback_failed")
            if current.get("task_id") != task_id:
                block_active_helper(ledger, rec)
            if current.get("status") in TERMINAL_EXECUTION_STATES:
                terminal = current
                break
            if time.monotonic() >= next_progress:
                api.progress("WAITING_FOR_HELPER_TERMINAL", "poll only the recorded helper Execution")
                next_progress = time.monotonic() + 15
            time.sleep(0.5)
        if terminal is None:
            terminal = ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=0)
        rec["terminal_proven"] = True
        rec["execution_status"] = terminal.get("status")
        code, log_obj, _ = api.request("GET", "/api/executions/" + execution_id + "/logs", limit=1024 * 1024)
        chunks = list_rows(log_obj, ("logs",))
        if code != 200 or chunks is None:
            raise GateError("helper_logs_readback_failed")
        stdout = "".join(x.get("chunk", "") for x in sorted(
            (x for x in chunks if isinstance(x, dict) and x.get("stream") == "stdout"),
            key=lambda x: (x.get("seq", 0), x.get("created_at", ""))) if isinstance(x.get("chunk"), str))
        try:
            remote = json.loads(stdout) if stdout else None
        except ValueError:
            remote = None
        if isinstance(remote, dict):
            rec["safe_result"] = remote
            result["helper_results"].append({"phase": phase, "task_id": task_id,
                                              "execution_id": execution_id,
                                              "status": terminal.get("status"), "remote": remote})
            save(ledger["ledger_path"], ledger)
            save(ledger["result_path"], result)
        if terminal.get("status") != "SUCCESS" or not isinstance(remote, dict):
            raise GateError("helper_terminal_or_json_output_failed")
        if remote.get("credentials_emitted") is not False:
            raise GateError("helper_credentials_output_guard_failed")
        return remote
    finally:
        if task_id:
            if rec.get("run_requested") and not rec.get("terminal_proven"):
                ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=2)
            dcode, _, _ = api.request("DELETE", "/api/tasks/" + task_id)
            gcode, _, _ = api.request("GET", "/api/tasks/" + task_id)
            rec["task_delete_http"] = dcode
            rec["task_get_after_delete_http"] = gcode
            rec["task_deleted_and_404"] = dcode in (200, 404) and gcode == 404
            save(ledger["ledger_path"], ledger)
            if not rec["task_deleted_and_404"]:
                raise GateError("exact_helper_task_cleanup_failed")


def exact_get(api, path, expected_id):
    code, obj, _ = api.request("GET", path)
    require(code, 200, "owned_object_get")
    if not isinstance(obj, dict) or obj.get("id") != expected_id:
        raise GateError("owned_object_identity_mismatch")
    return obj


def delete_exact(api, path, expected_id, guard, allow_missing=False):
    code, obj, _ = api.request("GET", path)
    if code == 404 and allow_missing:
        return {"id": expected_id, "delete_http": 404, "get_after_delete_http": 404,
                "already_absent": True}
    require(code, 200, "owned_object_get")
    if not isinstance(obj, dict) or obj.get("id") != expected_id:
        raise GateError("owned_object_identity_mismatch")
    if not guard(obj):
        raise GateError("owned_object_cleanup_guard_failed")
    code, _, _ = api.request("DELETE", path)
    if code not in (200, 404):
        raise GateError("owned_object_delete_failed")
    gcode, _, _ = api.request("GET", path)
    if gcode != 404:
        raise GateError("owned_object_get_after_delete_not_404")
    return {"id": expected_id, "delete_http": code, "get_after_delete_http": gcode}


def run(args):
    base, token = read_private(args.connection_file)
    api = API(base, token)
    raw = LIFECYCLE.read_text()
    compile(raw, str(LIFECYCLE), "exec")
    source_sha = sha(raw.encode())
    controller_sha = sha(Path(__file__).read_bytes())
    if source_sha != args.approved_lifecycle_sha256 or controller_sha != args.approved_controller_sha256:
        raise GateError("approved_source_hash_mismatch")
    nonce = uuid.uuid4().hex[:12]
    node_name = "qa-sys12-docker-" + nonce
    owner = "qa-resume-20261012-sys12-" + nonce
    marker = "qa-sys12-" + nonce
    script_name = "qa-sys12-script-" + nonce
    task_name = "qa-sys12-script-task-" + nonce
    outdir = HERE.resolve()
    progress_path = outdir / ("sys12-" + nonce + "-progress.json")
    ledger_path = outdir / ("sys12-" + nonce + "-ledger.json")
    result_path = outdir / ("sys12-" + nonce + "-result.json")
    if any(p.exists() for p in (progress_path, ledger_path, result_path)):
        raise GateError("local_evidence_path_collision")
    ledger = {"created_at_utc": utc(), "nonce": nonce, "owner": owner, "node_name": node_name,
              "script_name": script_name, "task_name": task_name,
              "helper_calls": [], "helper_call_count_total": 0,
              "helper_call_count_normal": 0, "helper_call_count_recovery": 0,
              "script_id": None, "task_id": None, "schedule_id": None,
              "node_id": None, "agent_id": None, "execution_id": None,
              "authority_port": 18080, "credentials_persisted": False,
              "lifecycle_source_sha256": source_sha,
              "controller_source_sha256": controller_sha,
              "ledger_path": str(ledger_path), "result_path": str(result_path),
              "progress_path": str(progress_path),
              "resource_grant_id": args.grant_id}
    result = {"status": "STARTED", "captured_at_utc": utc(), "nonce": nonce,
              "authority_port": 18080, "source_sha256": source_sha,
              "controller_source_sha256": controller_sha,
              "credentials_persisted": False, "helper_results": [], "api_evidence": []}
    api.on_response = lambda item: (result["api_evidence"].append(item), save(result_path, result))
    api.progress_path, api.nonce = progress_path, nonce
    save(ledger_path, ledger)
    save(result_path, result)
    save(progress_path, {"updated_at_utc": utc(), "status": "PREFLIGHT",
                         "next_action": "verify protected nodes and Native2", "nonce": nonce})

    # Read-only health guard for the explicitly supplied original Node set.
    protected = list(dict.fromkeys(args.protected_node_ids.split(",")))
    if len(protected) != 4 or TARGET_NATIVE2 not in protected:
        raise GateError("exactly_four_original_node_ids_required")
    protected_before = {}
    for nid in protected:
        n = get_node(api, nid)
        if n.get("status") != "online" or n.get("sync_status") != "synced":
            raise GateError("protected_original_node_not_healthy")
        protected_before[nid] = {"status": n.get("status"), "sync_status": n.get("sync_status"),
                                  "deployment_mode": n.get("deployment_mode"),
                                  "host_integration": n.get("host_integration")}
    native2 = get_node(api, TARGET_NATIVE2)
    if native2.get("status") != "online" or native2.get("sync_status") != "synced":
        raise GateError("native2_not_online_synced")
    code, nodes_obj, _ = api.request("GET", "/api/nodes")
    require(code, 200, "baseline_nodes_list")
    node_rows = list_rows(nodes_obj, ("nodes", "items"))
    if node_rows is None:
        raise GateError("baseline_nodes_shape_invalid")
    before_ids = {x.get("id") for x in node_rows if isinstance(x, dict)}
    result["protected_nodes_before"] = protected_before
    ledger["baseline_node_ids"] = sorted(x for x in before_ids if isinstance(x, str))
    save(ledger_path, ledger)

    script_content = "printf '%s' " + shlex.quote(marker)
    script_sha = sha(script_content.encode())
    ledger["script_sha256"] = script_sha
    ledger["script_content_bytes"] = len(script_content.encode())
    save(ledger_path, ledger)
    hub_objects_deleted = False
    try:
        def adjacent_node_name_check():
            ensure_node_name_unused(api, node_name, before_ids)

        # This callback runs after the exact helper Task readback and immediately before its Run;
        # prepare creates A during that single Run.
        prepared = run_helper(api, raw, source_sha, ledger, result, "prepare", {},
                              pre_run=adjacent_node_name_check)
        if prepared.get("status") != "CONTAINER_A_STARTED":
            raise GateError("container_a_prepare_result_mismatch")
        node = wait_node_by_name(api, node_name)
        ledger["node_id"], ledger["agent_id"] = node["id"], node["agent_id"]
        if node["id"] in before_ids:
            raise GateError("new_node_id_was_preexisting")
        ledger["new_node_hello_verified"] = True
        save(ledger_path, ledger)

        # Script name preflight and one owned definition.
        set_phase(ledger, "SCRIPT_CREATE", "create one owned Script through repair API")
        set_phase(ledger, "SCRIPT_CREATE", "create one owned Script via repair API")
        code, scripts_obj, _ = api.request("GET", "/api/scripts")
        require(code, 200, "scripts_list")
        scripts = list_rows(scripts_obj, ("scripts", "items"))
        if scripts is None or any(isinstance(x, dict) and x.get("name") == script_name for x in scripts):
            raise GateError("script_name_preflight_failed")
        script_body = {"name": script_name, "description": "owned SYS-12 persistence fixture",
                       "interpreter": "shell", "content": script_content, "parameters": [],
                       "environment": {}, "working_dir": "", "timeout": 30, "enabled": True}
        ledger["script_create_requested"] = True
        save(ledger_path, ledger)
        code, script, _ = api.request("POST", "/api/scripts", script_body)
        if code not in (200, 201) or not isinstance(script, dict) or not isinstance(script.get("id"), str):
            raise GateError("owned_script_create_failed")
        ledger["script_id"] = script["id"]
        ledger["script_revision"] = script.get("revision")
        ledger["script_sha256"] = script.get("sha256")
        if script.get("name") != script_name or script.get("sha256") != script_sha or script.get("revision") != 1:
            raise GateError("owned_script_response_mismatch")
        script_readback = exact_get(api, "/api/scripts/" + script["id"], script["id"])
        if (script_readback.get("name") != script_name or script_readback.get("sha256") != script_sha
                or script_readback.get("revision") != 1 or script_readback.get("content") != script_content):
            raise GateError("owned_script_get_readback_mismatch")
        save(ledger_path, ledger)

        code, tasks_obj, _ = api.request("GET", "/api/tasks")
        tasks = list_rows(tasks_obj, ("tasks", "items"))
        if code != 200 or tasks is None or any(isinstance(x, dict) and x.get("name") == task_name for x in tasks):
            raise GateError("script_task_name_preflight_failed")
        set_phase(ledger, "SCRIPT_TASK_CREATE_RUN", "create one Script Task and one execution")
        task_body = {"name": task_name, "type": "script", "target": {"type": "node", "node_ids": [node["id"]]},
                     "parameters": [], "param_values": {}, "script_id": script["id"], "timeout": 30,
                     "retry": 0, "offline_policy": "hub_online_required", "enabled": True}
        ledger["task_create_requested"] = True
        save(ledger_path, ledger)
        code, task, _ = api.request("POST", "/api/tasks", task_body)
        if code not in (200, 201) or not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise GateError("script_task_create_failed")
        ledger["task_id"] = task["id"]
        if task.get("type") != "script" or task.get("script_id") != script["id"]:
            raise GateError("script_task_response_mismatch")
        task_readback = exact_get(api, "/api/tasks/" + task["id"], task["id"])
        if (task_readback.get("name") != task_name or task_readback.get("type") != "script"
                or task_readback.get("script_id") != script["id"]
                or task_readback.get("target", {}).get("node_ids") != [node["id"]]):
            raise GateError("script_task_get_readback_mismatch")
        save(ledger_path, ledger)
        code, run_obj, _ = api.request("POST", "/api/tasks/" + task["id"] + "/run", {"params": {}})
        execs = list_rows(run_obj, ("executions",))
        if execs is None and isinstance(run_obj, dict) and isinstance(run_obj.get("id"), str):
            execs = [run_obj]
        if (code != 200 or not execs or len(execs) != 1 or not isinstance(execs[0], dict)
                or not isinstance(execs[0].get("id"), str)):
            raise GateError("single_script_execution_start_failed")
        execution_id = execs[0]["id"]
        ledger["execution_id"] = execution_id
        save(ledger_path, ledger)
        deadline = time.monotonic() + 75
        next_progress = time.monotonic() + 15
        terminal = None
        while time.monotonic() < deadline:
            code, current, _ = api.request("GET", "/api/executions/" + execution_id)
            if code != 200 or not isinstance(current, dict) or current.get("id") != execution_id:
                raise GateError("business_execution_readback_failed")
            if current.get("status") in TERMINAL_EXECUTION_STATES:
                terminal = current
                break
            if time.monotonic() >= next_progress:
                api.progress("WAITING_FOR_SCRIPT_EXECUTION", "poll only the owned Script Task Execution")
                next_progress = time.monotonic() + 15
            time.sleep(0.5)
        if terminal is None or terminal.get("status") != "SUCCESS":
            raise GateError("script_execution_not_success")
        api_exit_code_omitted = "exit_code" not in terminal
        if not api_exit_code_omitted:
            api_exit_code = terminal.get("exit_code")
            if type(api_exit_code) is not int or api_exit_code != 0:
                raise GateError("script_execution_api_exit_code_invalid")
        result["api_success_exit_code_omitted_known_defect"] = api_exit_code_omitted
        ledger["api_success_exit_code_omitted_known_defect"] = api_exit_code_omitted
        save(result_path, result)
        save(ledger_path, ledger)
        if (terminal.get("node_id") != node["id"] or terminal.get("task_id") != task["id"]
                or terminal.get("script_id") != script["id"] or terminal.get("script_revision") != 1):
            raise GateError("script_execution_identity_mismatch")
        code, log_obj, _ = api.request("GET", "/api/executions/" + execution_id + "/logs", limit=1024 * 1024)
        chunks = list_rows(log_obj, ("logs",))
        if code != 200 or chunks is None:
            raise GateError("business_execution_logs_readback_failed")
        stdout = "".join(x.get("chunk", "") for x in sorted(
            (x for x in chunks if isinstance(x, dict) and x.get("stream") == "stdout"),
            key=lambda x: (x.get("seq", 0), x.get("created_at", ""))) if isinstance(x.get("chunk"), str))
        if stdout != marker:
            raise GateError("script_execution_marker_mismatch")

        code, schedules_obj, _ = api.request("GET", "/api/schedules")
        schedules = list_rows(schedules_obj, ("schedules", "items"))
        schedule_name = "qa-sys12-disabled-schedule-" + nonce
        if code != 200 or schedules is None or any(isinstance(x, dict) and x.get("name") == schedule_name for x in schedules):
            raise GateError("schedule_collection_or_collision_guard_failed")
        set_phase(ledger, "DISABLED_SCHEDULE_CREATE", "create one disabled Schedule")
        set_phase(ledger, "DISABLED_SCHEDULE_CREATE", "create and verify one disabled Schedule")
        schedule_body = {"task_id": task["id"], "type": "cron", "expression": "0 0 1 1 *",
                         "timezone": "UTC", "execution_owner": "agent",
                         "offline_policy": "hub_online_required", "misfire_policy": "skip", "enabled": False}
        ledger["schedule_create_requested"] = True
        save(ledger_path, ledger)
        code, schedule, _ = api.request("POST", "/api/schedules", schedule_body)
        if code not in (200, 201) or not isinstance(schedule, dict) or not isinstance(schedule.get("id"), str):
            raise GateError("disabled_schedule_create_failed")
        ledger["schedule_id"] = schedule["id"]
        if schedule.get("enabled") is not False or schedule.get("task_id") != task["id"]:
            raise GateError("disabled_schedule_response_mismatch")
        schedule_readback = exact_get(api, "/api/schedules/" + schedule["id"], schedule["id"])
        if (schedule_readback.get("enabled") is not False or schedule_readback.get("task_id") != task["id"]
                or schedule_readback.get("type") != "cron" or schedule_readback.get("expression") != "0 0 1 1 *"
                or schedule_readback.get("execution_owner") != "agent"
                or schedule_readback.get("offline_policy") != "hub_online_required"
                or schedule_readback.get("misfire_policy") != "skip"):
            raise GateError("disabled_schedule_get_readback_mismatch")
        save(ledger_path, ledger)

        node = wait_node(api, node["id"], node_name)
        desired_rev = node["global_revision"]
        set_phase(ledger, "INSPECT_A", "inspect A SQLite Script cache and database-open proof")
        set_phase(ledger, "INSPECT_A", "verify Script DB cache and process-open proof on A")
        inspect_a = run_helper(api, raw, source_sha, ledger, result, "inspect", {
            "schedule_id": schedule["id"], "execution_id": execution_id, "script_id": script["id"],
            "task_id": task["id"], "script_sha256": script_sha, "script_revision": 1,
            "expected_node_id": node["id"], "expected_agent_id": node["agent_id"],
            "hub_desired_revision": desired_rev,
        }, pre_run=lambda: synced_node_snapshot(api, node["id"], node_name))
        if inspect_a.get("status") != "INSPECTION_COMPLETE":
            raise GateError("inspect_a_result_mismatch")
        db_a = inspect_a.get("local_database", {})
        verify_db_summary(db_a, node, script["id"], script_name, script_sha, len(script_content.encode()),
                          task["id"], execution_id, schedule["id"], schedule.get("revision"))
        if db_a.get("global_revision", -1) < ledger["helper_calls"][-1].get("pre_run_node", {}).get("global_revision", -1):
            raise GateError("inspect_a_local_revision_below_synced_node_revision")
        verify_process_proof(inspect_a.get("agent_db_open_proof_before_read"),
                             (inspect_a.get("containers") or [{}])[0].get("container_id"), db_a)
        verify_process_proof(inspect_a.get("agent_db_open_proof_after_read"),
                             (inspect_a.get("containers") or [{}])[0].get("container_id"), db_a)
        ledger["container_a_id"] = (inspect_a.get("containers") or [{}])[0].get("container_id")
        save(ledger_path, ledger)

        set_phase(ledger, "RECREATE_B", "persist A snapshot, replace A with B and prove B opened same database")
        set_phase(ledger, "RECREATE_B", "persist A-stop snapshot and replace A with B")
        recreated = run_helper(api, raw, source_sha, ledger, result, "recreate", {
            "schedule_id": schedule["id"], "execution_id": execution_id, "script_id": script["id"],
            "task_id": task["id"], "script_sha256": script_sha, "script_revision": 1,
            "expected_node_id": node["id"], "expected_agent_id": node["agent_id"],
        })
        if recreated.get("status") != "CONTAINER_B_STARTED":
            raise GateError("container_b_recreate_result_mismatch")
        db_b = recreated.get("b_start_local_persistence", {})
        verify_db_summary(db_b, node, script["id"], script_name, script_sha, len(script_content.encode()),
                          task["id"], execution_id, schedule["id"], schedule.get("revision"))
        verify_process_proof(recreated.get("b_process_db_open_proof"),
                             (recreated.get("container_b") or {}).get("container_id"), db_b)
        verify_process_proof(recreated.get("b_process_db_open_proof_after_read"),
                             (recreated.get("container_b") or {}).get("container_id"), db_b)
        if recreated.get("container_a_removed_id") != ledger.get("container_a_id"):
            raise GateError("container_a_immutable_id_mismatch")
        ledger["container_b_id"] = (recreated.get("container_b") or {}).get("container_id")
        save(ledger_path, ledger)
        if (db_a.get("db_dev"), db_a.get("db_inode")) != (db_b.get("db_dev"), db_b.get("db_inode")):
            raise GateError("state_db_identity_changed_after_recreate")
        if db_b.get("global_revision", -1) < db_a.get("global_revision", -1):
            raise GateError("local_global_revision_regressed_after_recreate")

        node = wait_node(api, node["id"], node_name)
        desired_rev = node["global_revision"]
        set_phase(ledger, "INSPECT_B", "verify synced B persistence and stop B")
        set_phase(ledger, "INSPECT_B", "verify synced B cache and stop B")
        inspect_b = run_helper(api, raw, source_sha, ledger, result, "inspect", {
            "schedule_id": schedule["id"], "execution_id": execution_id, "script_id": script["id"],
            "task_id": task["id"], "script_sha256": script_sha, "script_revision": 1,
            "expected_node_id": node["id"], "expected_agent_id": node["agent_id"],
            "hub_desired_revision": desired_rev, "stop_after_inspection": True,
        }, pre_run=lambda: synced_node_snapshot(api, node["id"], node_name))
        if inspect_b.get("status") != "INSPECTION_COMPLETE" or not inspect_b.get("container_b_stopped"):
            raise GateError("inspect_b_or_stop_result_mismatch")
        db_after = inspect_b.get("local_database", {})
        verify_db_summary(db_after, node, script["id"], script_name, script_sha, len(script_content.encode()),
                          task["id"], execution_id, schedule["id"], schedule.get("revision"))
        if db_after.get("global_revision", -1) < ledger["helper_calls"][-1].get("pre_run_node", {}).get("global_revision", -1):
            raise GateError("inspect_b_local_revision_below_synced_node_revision")
        verify_process_proof(inspect_b.get("agent_db_open_proof_before_read"),
                             (inspect_b.get("containers") or [{}])[-1].get("container_id"), db_after)
        verify_process_proof(inspect_b.get("agent_db_open_proof_after_read"),
                             (inspect_b.get("containers") or [{}])[-1].get("container_id"), db_after)
        if (inspect_b.get("container_b_stopped") or {}).get("running") is not False:
            raise GateError("container_b_not_stopped_after_final_inspection")
        if (db_after.get("db_dev"), db_after.get("db_inode")) != (db_a.get("db_dev"), db_a.get("db_inode")):
            raise GateError("state_db_identity_changed_post_reconnect")
        if db_after.get("global_revision", -1) < desired_rev:
            raise GateError("local_revision_below_current_synced_hub_revision")

        set_phase(ledger, "API_CLEANUP", "delete exact Schedule, Task, Script and Node; verify each 404")
        set_phase(ledger, "API_CLEANUP", "delete owned Schedule, Task, Script, Node and verify 404")
        cleanup_api_objects(api, ledger, result)
        if not all_api_objects_404(api, ledger):
            raise GateError("api_cleanup_closure_guard_failed")
        hub_objects_deleted = True
        set_phase(ledger, "RUNTIME_CLEANUP", "remove exact owned stopped container and bridge")
        set_phase(ledger, "RUNTIME_CLEANUP", "remove only exact owned container IDs and bridge")
        runtime = run_helper(api, raw, source_sha, ledger, result, "cleanup-runtime", {})
        if runtime.get("status") != "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED":
            raise GateError("runtime_cleanup_result_mismatch")
        set_phase(ledger, "ROOT_CLEANUP", "remove exact owned private/data root after API 404s")
        set_phase(ledger, "ROOT_CLEANUP", "remove owned root only after exact API 404 proof")
        root_cleanup = run_helper(api, raw, source_sha, ledger, result, "cleanup-root", {"hub_objects_deleted": True})
        if root_cleanup.get("status") != "EXACT_OWNED_ROOT_REMOVED":
            raise GateError("root_cleanup_result_mismatch")
        protected_after = {}
        for nid in protected:
            n = get_node(api, nid)
            if n.get("status") != "online" or n.get("sync_status") != "synced":
                raise GateError("protected_original_node_unhealthy_after_test")
            protected_after[nid] = {"status": n.get("status"), "sync_status": n.get("sync_status"),
                                    "deployment_mode": n.get("deployment_mode"),
                                    "host_integration": n.get("host_integration")}
        result["protected_nodes_after"] = protected_after
        result["status"] = "SYS12_SCRIPT_DB_CACHE_PASS_ARTIFACT_CACHE_NOT_TESTED"
        result["cleanup"] = "exact API objects 404; runtime and owned root removed; Execution/Audit retained"
        result["coverage"] = {"script_db_cache": "PASS", "artifact_cache": "NOT_TESTED"}
        save(result_path, result)
        ledger["status"] = result["status"]
        ledger["phase"] = "COMPLETE"
        ledger["phase"] = "COMPLETE"
        save(ledger_path, ledger)
    except Exception as exc:
        result["status"] = "FAILED_OR_PARTIAL"
        result["safe_error"] = str(exc) if isinstance(exc, GateError) else "unexpected_" + type(exc).__name__
        result["captured_at_utc"] = utc()
        ledger["status"] = result["status"]
        ledger["last_safe_error"] = result["safe_error"]
        save(result_path, result)
        save(ledger_path, ledger)
        save(progress_path, {"updated_at_utc": utc(), "status": "FAILED_OR_PARTIAL",
                             "next_action": "Root review; if approved, invoke --execute --grant-id <grant> --recover-ledger " + str(ledger_path),
                             "nonce": nonce, "safe_error": result["safe_error"]})
        if isinstance(exc, ActiveHelperBlocked):
            result["status"] = ledger["status"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            result["safe_error"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            ledger["phase"] = "OWNED_ACTIVE_HELPER_BLOCKED"
            save(result_path, result)
            save(ledger_path, ledger)
            save(progress_path, {"updated_at_utc": utc(), "status": "OWNED_ACTIVE_HELPER_BLOCKED",
                                 "next_action": "stop all remote mutations; Root must reconcile exact helper Execution",
                                 "nonce": nonce})
            raise
        # Stop/remove only exact owned runtime resources before deleting the Node on failure.
        # If an owner guard fails, leave every resource for Root review.
        try:
            runtime_exists = has_owned_runtime(ledger)
            runtime_done = successful_helper(ledger, "cleanup-runtime", "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED")
            if runtime_exists and not runtime_done:
                if any(call.get("phase") == "cleanup-runtime" for call in ledger["helper_calls"]):
                    raise GateError("runtime_cleanup_attempt_failed_preserved_for_root_review")
                if ledger.get("helper_call_count_total", len(ledger["helper_calls"])) >= TOTAL_HELPER_BUDGET:
                    raise GateError("helper_budget_exhausted_before_failure_runtime_cleanup")
                runtime = run_helper(api, raw, source_sha, ledger, result, "cleanup-runtime", {}, recovery=True)
                if runtime.get("status") != "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED":
                    raise GateError("failure_runtime_cleanup_result_mismatch")
                runtime_done = True
            cleanup_api_objects(api, ledger, result, allow_missing=True)
            hub_objects_deleted = all_api_objects_404(api, ledger)
            if (hub_objects_deleted and runtime_exists and runtime_done
                    and not successful_helper(ledger, "cleanup-root", "EXACT_OWNED_ROOT_REMOVED")
                    and ledger.get("helper_call_count_total", len(ledger["helper_calls"])) < TOTAL_HELPER_BUDGET):
                run_helper(api, raw, source_sha, ledger, result, "cleanup-root",
                           {"hub_objects_deleted": True}, recovery=True)
        except Exception as cleanup_exc:
            result["cleanup_safe_error"] = str(cleanup_exc) if isinstance(cleanup_exc, GateError) else "unexpected_" + type(cleanup_exc).__name__
            save(result_path, result)
        raise
    finally:
        result["captured_at_utc"] = utc()
        save(result_path, result)


def verify_db_summary(db, node, script_id, script_name, script_sha, script_bytes,
                      task_id, execution_id, schedule_id, schedule_revision):
    script = db.get("script", {}) if isinstance(db, dict) else {}
    execution = db.get("execution", {}) if isinstance(db, dict) else {}
    schedule = db.get("schedule", {}) if isinstance(db, dict) else {}
    if (db.get("status") != "ok" or db.get("quick_check") != "ok"
            or db.get("identity", {}).get("node_id") != node.get("id")
            or db.get("identity", {}).get("agent_id") != node.get("agent_id")
            or not isinstance(db.get("db_dev"), int) or not isinstance(db.get("db_inode"), int)
            or script.get("id") != script_id or script.get("name") != script_name or script.get("revision") != 1
            or script.get("stored_sha256") != script_sha or script.get("content_sha256") != script_sha
            or script.get("content_bytes") != script_bytes or script.get("enabled") is not True
            or execution.get("id") != execution_id or execution.get("task_id") != task_id
            or execution.get("status") != "SUCCESS" or execution.get("exit_code") != 0
            or execution.get("script_id") != script_id or execution.get("script_revision") != 1
            or schedule.get("id") != schedule_id or schedule.get("revision") != schedule_revision
            or schedule.get("enabled") is not False or schedule.get("task_id") != task_id
            or schedule.get("execution_owner") != "agent"):
        raise GateError("local_sqlite_business_oracle_mismatch")


def verify_process_proof(proof, container_id, db):
    if (not isinstance(proof, dict) or not isinstance(container_id, str)
            or proof.get("container_id") != container_id or proof.get("uid") != 65534
            or proof.get("exe_sha256") != "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
            or proof.get("cgroup_matches_container") is not True
            or not isinstance(proof.get("pid"), int) or not isinstance(proof.get("start_ticks"), str)
            or (proof.get("container_db_dev"), proof.get("container_db_inode"))
            != (db.get("db_dev"), db.get("db_inode"))
            or not isinstance(proof.get("matching_db_fd_count"), int)
            or proof.get("matching_db_fd_count", 0) < 1):
        raise GateError("agent_process_database_fd_proof_mismatch")


def cleanup_api_objects(api, ledger, result, allow_missing=False):
    deleted = ledger.setdefault("api_cleanup", [])
    cleanup_helper_tasks(api, ledger)
    sid, tid, script_id, nid = (ledger.get("schedule_id"), ledger.get("task_id"),
                                 ledger.get("script_id"), ledger.get("node_id"))
    node_name, script_name, task_name = ledger.get("node_name"), ledger.get("script_name"), ledger.get("task_name")
    if not sid and tid and ledger.get("schedule_create_requested"):
        code, obj, _ = api.request("GET", "/api/schedules")
        require(code, 200, "cleanup_schedule_list")
        rows = list_rows(obj, ("schedules", "items"))
        if rows is None:
            raise GateError("cleanup_schedule_list_shape_invalid")
        matches = [x for x in (rows or []) if isinstance(x, dict) and x.get("task_id") == tid
                   and x.get("type") == "cron" and x.get("expression") == "0 0 1 1 *"
                   and x.get("enabled") is False]
        if len(matches) > 1:
            raise GateError("owned_schedule_recovery_ambiguous")
        if matches:
            sid = ledger["schedule_id"] = matches[0].get("id")
    if not tid and script_id and nid and ledger.get("task_create_requested"):
        code, obj, _ = api.request("GET", "/api/tasks")
        require(code, 200, "cleanup_task_list")
        rows = list_rows(obj, ("tasks", "items"))
        if rows is None:
            raise GateError("cleanup_task_list_shape_invalid")
        matches = [x for x in (rows or []) if isinstance(x, dict) and x.get("name") == task_name
                   and x.get("script_id") == script_id
                   and x.get("target", {}).get("node_ids") == [nid]]
        if len(matches) > 1:
            raise GateError("owned_task_recovery_ambiguous")
        if matches:
            tid = ledger["task_id"] = matches[0].get("id")
    if not script_id and ledger.get("script_create_requested"):
        code, obj, _ = api.request("GET", "/api/scripts")
        require(code, 200, "cleanup_script_list")
        rows = list_rows(obj, ("scripts", "items"))
        if rows is None:
            raise GateError("cleanup_script_list_shape_invalid")
        matches = [x for x in (rows or []) if isinstance(x, dict) and x.get("name") == script_name
                   and x.get("sha256") == ledger.get("script_sha256")]
        if len(matches) > 1:
            raise GateError("owned_script_recovery_ambiguous")
        if matches:
            script_id = ledger["script_id"] = matches[0].get("id")
    if not nid and any(call.get("phase") == "prepare" and call.get("run_requested")
                       for call in ledger.get("helper_calls", [])):
        code, obj, _ = api.request("GET", "/api/nodes")
        require(code, 200, "cleanup_nodes_list")
        rows = list_rows(obj, ("nodes", "items"))
        if rows is None:
            raise GateError("cleanup_nodes_list_shape_invalid")
        matches = [x for x in (rows or []) if isinstance(x, dict) and x.get("hostname") == node_name]
        if len(matches) > 1 or any(x.get("id") in ledger.get("baseline_node_ids", []) for x in matches):
            raise GateError("cleanup_node_name_resolution_ambiguous")
        if matches:
            if (matches[0].get("deployment_mode") != "docker"
                    or matches[0].get("host_integration") is not False):
                raise GateError("cleanup_node_mode_guard_failed")
            nid = ledger["node_id"] = matches[0].get("id")
            ledger["agent_id"] = matches[0].get("agent_id")
    save(ledger["ledger_path"], ledger)
    if sid:
        deleted.append(delete_exact(api, "/api/schedules/" + sid, sid,
                                    lambda x: x.get("task_id") == tid and x.get("enabled") is False
                                    and x.get("type") == "cron" and x.get("expression") == "0 0 1 1 *"
                                    and x.get("timezone") == "UTC" and x.get("execution_owner") == "agent"
                                    and x.get("offline_policy") == "hub_online_required"
                                    and x.get("misfire_policy") == "skip",
                                    allow_missing=allow_missing))
        save(ledger["ledger_path"], ledger)
    elif not allow_missing:
        raise GateError("owned_schedule_id_missing_before_cleanup")
    if tid:
        deleted.append(delete_exact(api, "/api/tasks/" + tid, tid,
                                    lambda x: x.get("name") == task_name and x.get("script_id") == script_id
                                    and x.get("type") == "script" and x.get("target", {}).get("node_ids") == [nid]
                                    and x.get("retry") == 0 and x.get("timeout") == 30
                                    and x.get("offline_policy") == "hub_online_required",
                                    allow_missing=allow_missing))
        save(ledger["ledger_path"], ledger)
    elif not allow_missing:
        raise GateError("owned_task_id_missing_before_cleanup")
    if script_id:
        deleted.append(delete_exact(api, "/api/scripts/" + script_id, script_id,
                                    lambda x: x.get("name") == script_name and x.get("sha256") == ledger.get("script_sha256"),
                                    allow_missing=allow_missing))
        save(ledger["ledger_path"], ledger)
    elif not allow_missing:
        raise GateError("owned_script_id_missing_before_cleanup")
    if nid:
        deleted.append(delete_exact(api, "/api/nodes/" + nid, nid,
                                    lambda x: x.get("hostname") == node_name and x.get("agent_id") == ledger.get("agent_id")
                                    and x.get("deployment_mode") == "docker" and x.get("host_integration") is False,
                                    allow_missing=allow_missing))
        save(ledger["ledger_path"], ledger)
    save(ledger["ledger_path"], ledger)


def cleanup_helper_tasks(api, ledger):
    for rec in ledger.get("helper_calls", []):
        if rec.get("task_deleted_and_404") and rec.get("terminal_proven"):
            continue
        task_id = rec.get("task_id")
        if not task_id:
            if not rec.get("task_create_requested"):
                rec["task_deleted_and_404"] = True
                save(ledger["ledger_path"], ledger)
                continue
            code, obj, _ = api.request("GET", "/api/tasks")
            require(code, 200, "helper_recovery_task_list")
            rows = list_rows(obj, ("tasks", "items"))
            if rows is None:
                raise GateError("helper_recovery_task_list_shape_invalid")
            matches = [x for x in (rows or []) if isinstance(x, dict) and x.get("name") == rec.get("task_name")]
            if len(matches) > 1:
                raise GateError("helper_task_recovery_ambiguous")
            if matches:
                candidate = matches[0]
                if (candidate.get("type") != "command"
                        or sha(str(candidate.get("command", "")).encode()) != rec.get("command_sha256")
                        or candidate.get("target", {}).get("node_ids") != [TARGET_NATIVE2]
                        or candidate.get("timeout") != 90 or candidate.get("retry") != 0):
                    raise GateError("helper_task_recovery_identity_mismatch")
                task_id = rec["task_id"] = candidate.get("id")
        if not task_id:
            rec["task_deleted_and_404"] = True
            save(ledger["ledger_path"], ledger)
            continue
        code, task, _ = api.request("GET", "/api/tasks/" + task_id)
        if code == 404:
            if rec.get("run_requested") and not rec.get("terminal_proven"):
                if not rec.get("task_id"):
                    block_active_helper(ledger, rec)
                ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=5)
            rec["task_deleted_and_404"] = True
            save(ledger["ledger_path"], ledger)
            continue
        require(code, 200, "helper_recovery_task_get")
        if (not isinstance(task, dict) or task.get("name") != rec.get("task_name")
                or sha(str(task.get("command", "")).encode()) != rec.get("command_sha256")
                or task.get("target", {}).get("node_ids") != [TARGET_NATIVE2]
                or task.get("timeout") != 90 or task.get("retry") != 0):
            raise GateError("helper_task_recovery_identity_mismatch")
        if rec.get("run_requested"):
            ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=105)
        else:
            rec["terminal_proven"] = True
            save(ledger["ledger_path"], ledger)
        dcode, _, _ = api.request("DELETE", "/api/tasks/" + task_id)
        gcode, _, _ = api.request("GET", "/api/tasks/" + task_id)
        if dcode not in (200, 404) or gcode != 404:
            raise GateError("helper_task_recovery_cleanup_failed")
        rec["task_deleted_and_404"] = True
        ledger["phase"] = "helper_task_recovery_cleanup"
        save(ledger["ledger_path"], ledger)


def prove_helper_records_inactive(api, ledger):
    for rec in ledger.get("helper_calls", []):
        if not rec.get("run_requested"):
            rec["terminal_proven"] = True
            save(ledger["ledger_path"], ledger)
            continue
        task_id = rec.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            block_active_helper(ledger, rec)
        ensure_helper_terminal(api, ledger, rec, task_id, wait_seconds=10)


def all_api_objects_404(api, ledger):
    checks = [("schedule_id", "/api/schedules/"), ("task_id", "/api/tasks/"),
              ("script_id", "/api/scripts/"), ("node_id", "/api/nodes/")]
    for key, prefix in checks:
        value = ledger.get(key)
        if value:
            code, _, _ = api.request("GET", prefix + value)
            if code != 404:
                return False
    if not all(x.get("task_deleted_and_404") for x in ledger.get("helper_calls", [])):
        return False
    for path, key, name in (("/api/nodes", "nodes", ledger.get("node_name")),
                            ("/api/scripts", "scripts", ledger.get("script_name")),
                            ("/api/tasks", "tasks", ledger.get("task_name"))):
        code, obj, _ = api.request("GET", path)
        if code != 200:
            return False
        rows = list_rows(obj, (key, "items"))
        if rows is None or any(isinstance(x, dict) and x.get("hostname" if key == "nodes" else "name") == name
                               for x in rows):
            return False
    if ledger.get("task_id"):
        code, obj, _ = api.request("GET", "/api/schedules")
        if code != 200:
            return False
        rows = list_rows(obj, ("schedules", "items"))
        if rows is None or any(isinstance(x, dict) and x.get("task_id") == ledger["task_id"] for x in rows):
            return False
    return True


def read_owned_evidence(path):
    p = Path(path)
    st = p.lstat()
    if (not stat.S_ISREG(st.st_mode) or p.is_symlink() or st.st_uid != os.geteuid()
            or stat.S_IMODE(st.st_mode) != 0o600 or p.resolve().parent != HERE.resolve()):
        raise GateError("recovery_evidence_path_owner_mode_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino, fs.st_uid, stat.S_IMODE(fs.st_mode)) != (
                st.st_dev, st.st_ino, os.geteuid(), 0o600):
            raise GateError("recovery_evidence_changed_during_open")
        raw = os.read(fd, 4 * 1024 * 1024 + 1)
    finally:
        os.close(fd)
    if len(raw) > 4 * 1024 * 1024:
        raise GateError("recovery_evidence_size_limit")
    return json.loads(raw)


def has_owned_runtime(ledger):
    for call in ledger.get("helper_calls", []):
        if call.get("phase") == "prepare":
            remote = call.get("safe_result", {})
            if not isinstance(remote, dict):
                if call.get("run_requested"):
                    return True
                continue
            partial = remote.get("partial_state", {})
            if (remote.get("status") == "CONTAINER_A_STARTED"
                    or (isinstance(partial, dict) and partial.get("root"))
                    or remote.get("owner_json")):
                return True
    return bool(ledger.get("container_a_id") or ledger.get("container_b_id"))


def successful_helper(ledger, phase, status):
    for call in ledger.get("helper_calls", []):
        remote = call.get("safe_result")
        if (call.get("phase") == phase and call.get("execution_status") == "SUCCESS"
                and isinstance(remote, dict) and remote.get("status") == status):
            return True
    return False


def recover_cleanup(args):
    ledger_path = Path(args.recover_ledger)
    ledger = read_owned_evidence(ledger_path)
    if ledger.get("ledger_path") != str(ledger_path.resolve()):
        raise GateError("recovery_ledger_self_path_mismatch")
    if not re.fullmatch(r"[0-9a-f]{12}", ledger.get("nonce", "")):
        raise GateError("recovery_nonce_shape_invalid")
    result_path = Path(ledger.get("result_path", ""))
    progress_path = Path(ledger.get("progress_path", ""))
    if result_path.resolve().parent != HERE.resolve() or progress_path.resolve().parent != HERE.resolve():
        raise GateError("recovery_result_path_outside_owned_evidence_dir")
    result = read_owned_evidence(result_path)
    base, token = read_private(args.connection_file)
    api = API(base, token)
    api.on_response = lambda item: (result.setdefault("api_evidence", []).append(item), save(result_path, result))
    api.progress_path, api.nonce = progress_path, ledger["nonce"]
    raw = LIFECYCLE.read_text()
    compile(raw, str(LIFECYCLE), "exec")
    source_sha = sha(raw.encode())
    controller_sha = sha(Path(__file__).read_bytes())
    if (source_sha != args.approved_lifecycle_sha256
            or controller_sha != args.approved_controller_sha256
            or ledger.get("lifecycle_source_sha256") != source_sha
            or ledger.get("controller_source_sha256") != controller_sha):
        raise GateError("recovery_lifecycle_source_sha_mismatch")
    validate_helper_budget_ledger(ledger)
    result["recovery_started_at_utc"] = utc()
    result["status"] = "RECOVERY_RUNNING"
    save(result_path, result)
    # Reconcile every previously requested Run before any cleanup mutation.
    prove_helper_records_inactive(api, ledger)
    set_phase(ledger, "RECOVERY_PREFLIGHT", "reconcile only owner-ledger resources and exact names")
    runtime_exists = has_owned_runtime(ledger)
    runtime_done = successful_helper(ledger, "cleanup-runtime", "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED")
    root_done = successful_helper(ledger, "cleanup-root", "EXACT_OWNED_ROOT_REMOVED")
    if runtime_exists and not runtime_done:
        if ledger.get("helper_call_count_total", len(ledger.get("helper_calls", []))) >= TOTAL_HELPER_BUDGET:
            raise GateError("helper_budget_exhausted_before_runtime_recovery")
        set_phase(ledger, "RECOVERY_RUNTIME_CLEANUP", "recover exact nonce-named container/network and remove them")
        remote = run_helper(api, raw, source_sha, ledger, result, "cleanup-runtime", {}, recovery=True)
        if remote.get("status") != "DOCKER_RESOURCES_REMOVED_ROOT_RETAINED":
            raise GateError("recovery_runtime_cleanup_result_mismatch")
        runtime_done = True
    cleanup_api_objects(api, ledger, result, allow_missing=True)
    if not all_api_objects_404(api, ledger):
        raise GateError("recovery_api_objects_not_all_404")
    if runtime_exists and runtime_done and not root_done:
        if ledger.get("helper_call_count_total", len(ledger.get("helper_calls", []))) >= TOTAL_HELPER_BUDGET:
            raise GateError("helper_budget_exhausted_before_root_recovery")
        set_phase(ledger, "RECOVERY_ROOT_CLEANUP", "remove exact owner root after API 404 proof")
        remote = run_helper(api, raw, source_sha, ledger, result, "cleanup-root",
                            {"hub_objects_deleted": True}, recovery=True)
        if remote.get("status") != "EXACT_OWNED_ROOT_REMOVED":
            raise GateError("recovery_root_cleanup_result_mismatch")
    result["status"] = "FAILED_PHASE_CLEANUP_COMPLETE"
    result["cleanup"] = "exact owned API objects 404; known owned runtime/root recovery completed; history retained"
    ledger["status"] = result["status"]
    ledger["phase"] = "RECOVERY_COMPLETE"
    save(result_path, result)
    save(ledger_path, ledger)
    save(progress_path, {"updated_at_utc": utc(), "status": "RECOVERY_COMPLETE",
                         "next_action": "Root review; do not rerun the failed phase", "nonce": ledger["nonce"]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection-file", required=True)
    parser.add_argument("--protected-node-ids", default="", help="comma-separated exact original four Node IDs")
    parser.add_argument("--grant-id", default="", help="Root resource grant reference; required with --execute")
    parser.add_argument("--approved-lifecycle-sha256", default="")
    parser.add_argument("--approved-controller-sha256", default="")
    parser.add_argument("--recover-ledger", default="", help="recover one failed phase from its private owner ledger")
    parser.add_argument("--execute", action="store_true", help="perform the reviewed fixture lifecycle")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "DRAFT_NO_ACTION", "remote_actions": False,
                          "requires_execute": True, "requires_root_grant_id": True}))
        return
    if not args.grant_id or not re.fullmatch(r"[0-9a-f]{64}", args.approved_lifecycle_sha256) \
            or not re.fullmatch(r"[0-9a-f]{64}", args.approved_controller_sha256):
        raise SystemExit("Root grant ID and reviewed source SHA256 values required")
    if args.recover_ledger:
        recover_cleanup(args)
    else:
        if not args.protected_node_ids:
            raise SystemExit("protected original Node IDs required")
        run(args)


if __name__ == "__main__":
    main()
