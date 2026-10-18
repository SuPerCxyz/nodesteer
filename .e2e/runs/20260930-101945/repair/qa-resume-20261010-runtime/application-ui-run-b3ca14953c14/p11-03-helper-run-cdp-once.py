#!/usr/bin/env python3
"""Pause the one owned Task Run POST briefly and sample real loading state."""
import hashlib
import json
import pathlib
import subprocess
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from websockets.sync.client import connect

SESSION = "qa-p11-05-b3ca14953c14"
PROFILE = "/tmp/nodesteer-p11-05-b3ca14953c14-ieyj9m6m/browser-profile"
TASK_ID = "a2ca0d53-aa91-471c-b349-e883f70951ec"
ORIGIN = "http://127.0.0.1:38181"
EXPECTED_PAGE = ORIGIN + "/tasks/" + TASK_ID + "/run"
RUN_URL = ORIGIN + "/api/tasks/" + TASK_ID + "/run"
OUT = pathlib.Path(__file__).resolve().parent / "helper-deployed-run-control-result.json"
MAX_HOLD_NS = 500_000_000
QUEUED_FETCH_EVENTS = []


def utc():
    return datetime.now(timezone.utc).isoformat()


def cli(*args, timeout=15):
    p = subprocess.run(
        ["agent-browser", "--session", SESSION, "--profile", PROFILE, *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    if p.returncode != 0:
        raise RuntimeError("agent_browser_command_failed")
    return p.stdout.strip()


def send_cdp(ws, command_id, method, params=None, session_id=None, timeout=3):
    msg = {"id": command_id, "method": method, "params": params or {}}
    if session_id:
        msg["sessionId"] = session_id
    ws.send(json.dumps(msg))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic())))
        if item.get("id") == command_id:
            if "error" in item:
                raise RuntimeError("cdp_command_failed_" + method.replace(".", "_"))
            return item.get("result", {})
        if item.get("method") == "Fetch.requestPaused":
            QUEUED_FETCH_EVENTS.append(item)
    raise RuntimeError("cdp_command_timeout_" + method.replace(".", "_"))


def ui_state(ws, next_id, page_session, timeout=0.08):
    expr = r'''JSON.stringify((()=>{
      const d=document.querySelector('[role="alertdialog"]');
      const c=d?[...d.querySelectorAll('button')].find(x=>x.innerText.trim()==="立即运行"):null;
      const m=[...document.querySelectorAll('main button')].find(x=>["立即运行","运行中"].includes(x.innerText.trim()));
      return {
        url:location.href,
        dialog_present:!!d,
        confirm_found:!!c,
        confirm_disabled:c?c.disabled:null,
        confirm_label:c?c.innerText.trim():null,
        main_run_found:!!m,
        main_run_disabled:m?m.disabled:null,
        main_run_label:m?m.innerText.trim():null,
        navigation_count:performance.getEntriesByType('navigation').length,
        time_origin:performance.timeOrigin
      };
    })())'''
    result = send_cdp(ws, next_id, "Runtime.evaluate", {
        "expression": expr, "returnByValue": True, "awaitPromise": True,
    }, session_id=page_session, timeout=timeout)
    value = ((result.get("result") or {}).get("value"))
    if not isinstance(value, str):
        raise RuntimeError("ui_state_result_missing")
    return json.loads(value)


def main():
    page_url = cli("get", "url")
    if page_url != EXPECTED_PAGE:
        raise RuntimeError("task_run_page_guard_failed")
    snap = cli("snapshot", "-i")
    if "确定立即运行“qa-p11-05-physical-probe-b3ca14953c14”吗？" not in snap:
        raise RuntimeError("exact_helper_task_confirm_dialog_missing")
    confirm_lines = [line.strip() for line in snap.splitlines()
                     if line.strip().startswith('- button "立即运行" [ref=')]
    confirm_refs = [line.split("[ref=", 1)[1].split("]", 1)[0]
                    for line in confirm_lines]
    if len(confirm_refs) != 1:
        raise RuntimeError("confirm_button_ref_not_unique")
    confirm_ref = confirm_refs[0]
    cdp_url = cli("get", "cdp-url")
    u = urlparse(cdp_url)
    if u.scheme != "ws" or u.hostname != "127.0.0.1" or not u.port or u.port == 40949:
        raise RuntimeError("owned_browser_cdp_url_guard_failed")
    result = {
        "status": "RUN_CONTROL_STARTED",
        "captured_at_utc": utc(),
        "task_id": TASK_ID,
        "purpose": "P11-03 helper Task UI loading-state sample; not a product app deploy",
        "page_url_before_click": page_url,
        "confirm_ref_from_fresh_snapshot": confirm_ref,
        "snapshot_sha256": hashlib.sha256(snap.encode()).hexdigest(),
        "cdp_host": u.hostname,
        "cdp_port": u.port,
        "web_cdp40949_used": False,
        "fetch_pattern": RUN_URL,
        "actual_click_count": 0,
        "matching_paused_post_count": 0,
        "duplicate_post_blocked_count": 0,
        "continued_once": False,
        "hold_duration_ms": None,
        "ui_state_during_pause": None,
        "click_returncode": None,
        "click_timed_out": False,
        "request_body_recorded": False,
        "request_headers_recorded": False,
        "token_recorded": False,
    }
    click_proc = None
    ws = None
    page_session = None
    next_id = 1
    paused_request_id = None
    continued = False
    try:
        ws = connect(cdp_url, open_timeout=5, close_timeout=1, proxy=None)
        targets = send_cdp(ws, next_id, "Target.getTargets")
        next_id += 1
        pages = [x for x in targets.get("targetInfos", []) if x.get("type") == "page"]
        matches = [x for x in pages if x.get("url") == EXPECTED_PAGE]
        if len(matches) != 1:
            raise RuntimeError("owned_task_page_target_not_unique")
        attached = send_cdp(ws, next_id, "Target.attachToTarget", {
            "targetId": matches[0]["targetId"], "flatten": True,
        })
        next_id += 1
        page_session = attached.get("sessionId")
        if not page_session:
            raise RuntimeError("owned_task_page_attach_failed")
        initial = ui_state(ws, next_id, page_session)
        next_id += 1
        if (initial.get("url") != EXPECTED_PAGE or not initial.get("dialog_present")
                or not initial.get("confirm_found") or initial.get("confirm_disabled") is not False
                or not initial.get("main_run_found") or initial.get("main_run_disabled") is not False):
            raise RuntimeError("confirm_ui_preflight_state_mismatch")
        result["ui_state_before_click"] = initial
        send_cdp(ws, next_id, "Fetch.enable", {
            "patterns": [{"urlPattern": RUN_URL, "requestStage": "Request"}],
        }, session_id=page_session)
        next_id += 1
        click_proc = subprocess.Popen(
            ["agent-browser", "--session", SESSION, "--profile", PROFILE, "click", confirm_ref],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        result["actual_click_count"] = 1
        result["confirmation_click_started_at_utc"] = utc()
        deadline = time.monotonic() + 12
        paused = None
        while time.monotonic() < deadline:
            try:
                item = json.loads(ws.recv(timeout=min(0.1, max(0.01, deadline - time.monotonic()))))
            except TimeoutError:
                if click_proc.poll() is not None:
                    break
                continue
            if item.get("method") != "Fetch.requestPaused":
                continue
            request = item.get("params", {}).get("request", {})
            url = request.get("url", "")
            method = request.get("method", "")
            if url == RUN_URL and method == "POST" and item.get("sessionId") == page_session:
                paused = item
                paused_request_id = item.get("params", {}).get("requestId")
                break
            req_id = item.get("params", {}).get("requestId")
            if req_id:
                # Fail closed if the exact page unexpectedly emits another intercepted request.
                send_cdp(ws, next_id, "Fetch.continueRequest", {"requestId": req_id},
                         session_id=item.get("sessionId"), timeout=0.1)
                next_id += 1
                raise RuntimeError("unexpected_intercepted_request")
        if paused is None or not paused_request_id:
            result["status"] = "RUN_CLICK_SENT_NO_MATCHING_FETCH_PAUSE"
            result["click_process_returncode_before_reconcile"] = click_proc.poll()
            return
        result["matching_paused_post_count"] = 1
        req = paused["params"]["request"]
        result["paused_request"] = {
            "method": req.get("method"),
            "path": "/api/tasks/" + TASK_ID + "/run",
            "resource_type": paused["params"].get("resourceType"),
            "network_id_present": bool(paused["params"].get("networkId")),
        }
        paused_ns = time.monotonic_ns()
        deadline_ns = paused_ns + 430_000_000
        observed = None
        while time.monotonic_ns() < deadline_ns:
            try:
                observed = ui_state(ws, next_id, page_session, timeout=min(0.08, max(0.01, (deadline_ns-time.monotonic_ns())/1e9)))
                next_id += 1
            except Exception:
                observed = None
            if (observed and observed.get("dialog_present") and observed.get("confirm_found")
                    and observed.get("confirm_disabled") is True and observed.get("main_run_disabled") is True
                    and observed.get("main_run_label") == "运行中"):
                break
            time.sleep(0.01)
        result["ui_state_during_pause"] = observed
        # Continue the one product POST regardless of UI sampling outcome; never replay it.
        ws.send(json.dumps({"id": next_id, "method": "Fetch.continueRequest",
                            "params": {"requestId": paused_request_id}, "sessionId": page_session}))
        continue_ns = time.monotonic_ns()
        continued = True
        result["continued_once"] = True
        result["hold_duration_ms"] = round((continue_ns - paused_ns) / 1_000_000, 3)
        if result["hold_duration_ms"] > 500:
            result["status"] = "POST_CONTINUED_HOLD_LIMIT_EXCEEDED"
        else:
            result["status"] = "ONE_RUN_POST_CONTINUED"
        next_id += 1
        # Acknowledge continue when available; bounded to avoid extending the hold.
        try:
            ack_deadline = time.monotonic() + 0.1
            while time.monotonic() < ack_deadline:
                item = json.loads(ws.recv(timeout=max(0.01, ack_deadline-time.monotonic())))
                if item.get("id") == next_id - 1:
                    result["continue_acknowledged"] = "error" not in item
                    break
        except Exception:
            result["continue_acknowledged"] = False
        try:
            click_proc.communicate(timeout=12)
            result["click_returncode"] = click_proc.returncode
        except subprocess.TimeoutExpired:
            result["click_timed_out"] = True
            click_proc.terminate()
            try:
                click_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                click_proc.kill()
                click_proc.wait(timeout=2)
        # Count any duplicate matching POSTs while the page is still on the Task Run route;
        # abort duplicates so the granted single business execution is never multiplied.
        dup_deadline = time.monotonic() + 0.4
        while time.monotonic() < dup_deadline:
            try:
                if QUEUED_FETCH_EVENTS:
                    item = QUEUED_FETCH_EVENTS.pop(0)
                else:
                    item = json.loads(ws.recv(timeout=min(0.05, max(0.01, dup_deadline-time.monotonic()))))
            except TimeoutError:
                continue
            if item.get("method") != "Fetch.requestPaused":
                continue
            request = item.get("params", {}).get("request", {})
            if request.get("url") == RUN_URL and request.get("method") == "POST":
                result["duplicate_post_blocked_count"] += 1
                send_cdp(ws, next_id, "Fetch.failRequest", {
                    "requestId": item["params"]["requestId"], "errorReason": "Aborted",
                }, session_id=item.get("sessionId"), timeout=0.1)
                next_id += 1
        return
    finally:
        if paused_request_id and not continued and ws and page_session:
            # Emergency release of only the exact request paused by this helper.
            try:
                ws.send(json.dumps({"id": next_id, "method": "Fetch.continueRequest",
                                    "params": {"requestId": paused_request_id}, "sessionId": page_session}))
                result["continued_once"] = True
                result["emergency_continue"] = True
            except Exception:
                result["emergency_continue"] = False
        if ws and page_session:
            try:
                send_cdp(ws, next_id + 1, "Fetch.disable", {}, session_id=page_session, timeout=0.1)
            except Exception:
                result["fetch_disable_acknowledged"] = False
        if click_proc and click_proc.poll() is None:
            try:
                click_proc.communicate(timeout=3)
                result["click_returncode"] = click_proc.returncode
            except subprocess.TimeoutExpired:
                result["click_timed_out"] = True
                click_proc.terminate()
                try:
                    click_proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    click_proc.kill()
                    click_proc.wait(timeout=1)
        result["finished_at_utc"] = utc()
        OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        if ws:
            try:
                ws.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
