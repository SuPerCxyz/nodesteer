#!/usr/bin/env python3
"""Keyboard-verify Execution page size 40, real API pagination, persistence, and restoration."""
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
PRIVATE = Path(os.environ["NODESTEER_RESUME_PRIVATE_DIR"])
CONN = json.loads((PRIVATE / "repair-connection.json").read_text())
BASE = CONN["hub_url"].rstrip("/")
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]
OPTIONS = ["10", "20", "30", "40", "50"]


def run(args, stdin=None):
    result = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-180:])
    return result.stdout.strip()


def evaluate(script):
    value = json.loads(run(["eval", "--stdin"], script))
    return json.loads(value) if isinstance(value, str) else value


def size_control():
    snapshot = run(["snapshot", "-i", "-c", "-d", "6"])
    rows = [line for line in snapshot.splitlines() if "combobox" in line]
    if not rows:
        raise RuntimeError("Execution filter/page-size combobox missing")
    line = rows[-1]
    match = re.search(r"ref=(e\d+)", line)
    return "@" + match.group(1), line.rsplit(":", 1)[-1].strip()


def active_option():
    values = evaluate("JSON.stringify(Array.from(document.querySelectorAll('[role=option]')).map(e=>({value:e.textContent.trim(),focused:e===document.activeElement,highlighted:e.hasAttribute('data-highlighted')})))")
    return next((row["value"] for row in values if row["focused"] and row["highlighted"]), None)


def choose_size(target):
    ref, current = size_control()
    run(["focus", ref]); run(["press", "Enter"]); run(["wait", "100"])
    steps = []
    for _ in range(10):
        active = active_option()
        if active == target:
            break
        if active not in OPTIONS:
            run(["press", "Home"]); run(["wait", "100"]); continue
        direction = "ArrowDown" if OPTIONS.index(target) > OPTIONS.index(active) else "ArrowUp"
        run(["press", direction]); run(["wait", "100"]); steps.append(direction)
    if active_option() != target:
        raise RuntimeError("keyboard failed to focus option " + target)
    run(["press", "Enter"]); run(["wait", "--load", "networkidle"])
    selected = size_control()[1]
    if selected != target:
        raise RuntimeError("UI page size did not change to " + target)
    return {"before": current, "selected": selected, "keyboard_steps": steps}


def request_lines(previous):
    return [line for line in run(["network", "requests"]).splitlines() if line not in previous and "/api/executions" in line]


def read_api_page(url):
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + CONN["token"]})
    with urllib.request.urlopen(req, timeout=15) as response:
        body = json.loads(response.read())
        rows = body if isinstance(body, list) else next((body.get(k) for k in ("items", "data", "executions", "rows") if isinstance(body.get(k), list)), [])
        total = next((value for key, value in response.headers.items() if key.lower() in ("x-total-count", "x-total", "x-total-rows")), None)
        return {"http": response.status, "row_count": len(rows), "total_header": total, "body_keys": list(body) if isinstance(body, dict) else None}


if __name__ == "__main__":
    report = {"case": "SAFE-01 Execution page-size 40 keyboard selection/API/persistence", "tool": "agent-browser", "method": "Keyboard focus/arrow navigation/Enter; no force click", "secrets_included": False}
    original = "10"
    try:
        run(["open", BASE + "/executions"]); run(["wait", "--load", "networkidle"])
        original = size_control()[1]
        before = run(["network", "requests"]).splitlines()
        report["selection"] = choose_size("40")
        selected_requests = request_lines(before)
        api_line = next((line for line in selected_requests if "GET " in line and "/api/executions?" in line and "limit=40" in line and line.rstrip().endswith(" 200")), None)
        report["actual_ui_api_request"] = api_line
        rows = evaluate("JSON.stringify({rows:document.querySelectorAll('table tbody tr').length,pageText:document.querySelector('main')?.innerText||''})")
        report["ui_row_count"] = rows["rows"]
        if api_line:
            url = re.search(r"GET (https?://[^ ]+) ", api_line).group(1)
            report["same_query_api_page"] = read_api_page(url)
        run(["open", BASE + "/executions"]); run(["wait", "--load", "networkidle"])
        report["hard_reload_page_size"] = size_control()[1]
        reload_lines = request_lines(selected_requests)
        report["hard_reload_limit40_request"] = next((line for line in reload_lines if "limit=40" in line), None)
        report["status"] = "passed" if api_line and report["ui_row_count"] == 40 and report.get("same_query_api_page", {}).get("http") == 200 and report["hard_reload_page_size"] == "40" and report["hard_reload_limit40_request"] else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        try:
            run(["open", BASE + "/executions"]); run(["wait", "--load", "networkidle"])
            if size_control()[1] != original:
                choose_size(original)
            report["restored_original_page_size"] = size_control()[1] == original
        except Exception as error:
            report["restored_original_page_size"] = False
            report["restore_error"] = str(error)[:180]
        path = QA / "safe01-executions-page-size-keyboard.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2); stream.write("\n")
        print(json.dumps({"status": report["status"], "selected_api": bool(report.get("actual_ui_api_request")), "ui_rows": report.get("ui_row_count"), "hard_reload": report.get("hard_reload_page_size"), "restored": report.get("restored_original_page_size"), "evidence": str(path.relative_to(ROOT))}, ensure_ascii=False))
