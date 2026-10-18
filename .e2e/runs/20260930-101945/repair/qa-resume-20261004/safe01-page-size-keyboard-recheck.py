#!/usr/bin/env python3
"""Verify 40-row size by accessible keyboard and server response, then restore preferences."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
BROWSER = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]
OPTIONS = ["10", "20", "30", "40", "50"]


def run(args):
    result = subprocess.run(BROWSER + args, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-160:])
    return result.stdout.strip()


def evaluate(script):
    value = json.loads(run(["eval", script]))
    return json.loads(value) if isinstance(value, str) else value


def page_size():
    snapshot = run(["snapshot", "-i", "-c", "-d", "4"])
    line = next((line for line in snapshot.splitlines() if "combobox" in line), None)
    if not line:
        raise RuntimeError("page-size combobox missing")
    match = re.search(r"ref=(e\d+)", line)
    return "@" + match.group(1), line.rsplit(":", 1)[-1].strip()


def focused_option():
    rows = evaluate("JSON.stringify(Array.from(document.querySelectorAll('[role=option]')).map(e=>({text:e.textContent.trim(),focused:e===document.activeElement,highlighted:e.hasAttribute('data-highlighted')})))")
    return next((row["text"] for row in rows if row["focused"] and row["highlighted"]), None)


def choose_size(target):
    ref, initial = page_size()
    run(["focus", ref]); run(["press", "Enter"]); run(["wait", "100"])
    steps = []
    for _ in range(10):
        active = focused_option()
        if active == target:
            break
        if active not in OPTIONS:
            run(["press", "Home"]); run(["wait", "100"]); continue
        direction = "ArrowDown" if OPTIONS.index(target) > OPTIONS.index(active) else "ArrowUp"
        run(["press", direction]); run(["wait", "100"]); steps.append(direction)
    if focused_option() != target:
        raise RuntimeError("keyboard focus did not reach " + target)
    run(["press", "Enter"]); run(["wait", "--load", "networkidle"])
    selected = page_size()[1]
    if selected != target:
        raise RuntimeError("selected page-size value mismatch")
    return {"initial": initial, "selected": selected, "keyboard_steps": steps}


def api_events(path, previous):
    lines = run(["network", "requests"]).splitlines()
    return [line for line in lines if line not in previous and "/api/" + path in line]


def table_case(path, endpoint):
    run(["open", "http://192.168.100.209:18080/" + path]); run(["wait", "--load", "networkidle"])
    initial = page_size()[1]
    before = run(["network", "requests"]).splitlines()
    selection = choose_size("40")
    requests = api_events(endpoint, before)
    table_state = evaluate("JSON.stringify({rows:document.querySelectorAll('table tbody tr').length})")
    run(["open", "http://192.168.100.209:18080/" + path]); run(["wait", "--load", "networkidle"])
    persisted = page_size()[1] == "40"
    reload_requests = [line for line in run(["network", "requests"]).splitlines() if line not in before and "/api/" + endpoint in line]
    return {"route": path, "initial_size": initial, "selection": selection, "ui_row_count": table_state["rows"], "requests_after_selection": requests[-6:], "persisted_after_hard_reload": persisted, "requests_after_reload": reload_requests[-6:]}


if __name__ == "__main__":
    report = {"case": "SAFE-01 page-size keyboard selection/persistence", "tool": "agent-browser", "method": "Accessible focus and arrow keys, confirm focused option before Enter; no forced click", "secret_included": False}
    try:
        report["agents"] = table_case("agents", "nodes")
        report["audit"] = table_case("audit", "audit")
        audit_requests = report["audit"]["requests_after_selection"] + report["audit"]["requests_after_reload"]
        report["audit_limit_40_http_200"] = any("limit=40" in line and line.endswith(" 200") for line in audit_requests)
        report["status"] = "passed" if report["agents"]["persisted_after_hard_reload"] and report["audit"]["persisted_after_hard_reload"] and report["audit_limit_40_http_200"] else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        for path in ["agents", "audit"]:
            try:
                run(["open", "http://192.168.100.209:18080/" + path]); run(["wait", "--load", "networkidle"])
                original = report.get(path, {}).get("initial_size", "10")
                if page_size()[1] != original:
                    choose_size(original)
            except Exception:
                report.setdefault("restore_errors", []).append(path)
        out = QA / "safe01-page-size-keyboard-recheck.json"
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2); stream.write("\n")
        print(json.dumps({"status": report["status"], "agents_persisted": report.get("agents", {}).get("persisted_after_hard_reload"), "audit_persisted": report.get("audit", {}).get("persisted_after_hard_reload"), "audit_limit40_200": report.get("audit_limit_40_http_200"), "evidence": str(out.relative_to(ROOT))}, ensure_ascii=False))
