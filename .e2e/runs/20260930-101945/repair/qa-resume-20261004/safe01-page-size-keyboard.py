#!/usr/bin/env python3
"""Check table page-size 40 with keyboard navigation and persistence."""
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]


def run(args, stdin=None):
    result = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-200:])
    return result.stdout.strip()


def evaluate(script):
    raw = run(["eval", "--stdin"], script)
    value = json.loads(raw)
    return json.loads(value) if isinstance(value, str) else value


def snapshot():
    return run(["snapshot", "-i", "-c", "-d", "5"])


def page_size_ref():
    for line in snapshot().splitlines():
        if "combobox" in line:
            match = re.search(r"ref=(e\d+)", line)
            if match:
                return "@" + match.group(1), line.rsplit(":", 1)[-1].strip()
    raise RuntimeError("page-size combobox missing")


def active_option():
    rows=evaluate("JSON.stringify(Array.from(document.querySelectorAll('[role=option]')).map(e=>({text:e.textContent.trim(),focused:e===document.activeElement,highlighted:e.hasAttribute('data-highlighted')})))")
    return next((row["text"] for row in rows if row["focused"] and row["highlighted"]),None)


def choose_size(target):
    ref,initial=page_size_ref();run(["focus",ref]);run(["press","Enter"])
    options=["10","20","30","40","50"];steps=[]
    for _ in range(8):
        active=active_option()
        if active==target:break
        if active not in options:
            run(["press","Home"]);continue
        direction="ArrowDown" if options.index(target)>options.index(active) else "ArrowUp"
        run(["press",direction]);steps.append(direction)
    if active_option()!=target:raise RuntimeError("keyboard could not focus option "+target)
    run(["press","Enter"]);run(["wait","--load","networkidle"])
    selected=page_size_ref()[1]
    if selected!=target:raise RuntimeError("page size did not change to "+target)
    return {"initial":initial,"focused_option":target,"selected":selected,"keyboard_steps":steps}

def verify_path(path, name):
    run(["open", "http://192.168.100.209:18080/" + path])
    run(["wait", "--load", "networkidle"])
    requests_before = run(["network", "requests"]).splitlines()
    selection = choose_size("40")
    request_lines = run(["network", "requests"]).splitlines()
    relevant = [line for line in request_lines if line not in requests_before and "/api/" + ("audit" if path == "audit" else "nodes") in line]
    state = evaluate("JSON.stringify({rows:document.querySelectorAll('table tbody tr').length,comboboxes:Array.from(document.querySelectorAll('[role=combobox]')).map(e=>e.innerText.trim())})")
    run(["open", "http://192.168.100.209:18080/" + path])
    run(["wait", "--load", "networkidle"])
    persisted = page_size_ref()[1] == "40"
    requests_after_reload = [line for line in run(["network", "requests"]).splitlines() if line not in request_lines and "/api/" + ("audit" if path == "audit" else "nodes") in line]
    return {"table": name, "original_page_size": selection["initial"], "selection": selection, "ui_rows": state["rows"], "api_requests_after_selection": relevant[-8:], "persisted_after_hard_reload": persisted, "api_requests_after_reload": requests_after_reload[-8:]}


def restore_size(value):
    if page_size_ref()[1]==value:return
    choose_size(value)


if __name__ == "__main__":
    report = {"case": "SAFE-01 page size 40 keyboard selection and persistence", "tool": "agent-browser", "method": "focus/Enter/ArrowDown-or-ArrowUp until the target option is focused/Enter; no force click", "secrets_included": False}
    try:
        report["agents"] = verify_path("agents", "agents")
        report["audit"] = verify_path("audit", "audit")
        report["status"] = "passed" if report["agents"]["persisted_after_hard_reload"] and report["audit"]["persisted_after_hard_reload"] else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        # Restore each isolated browser table preference to its captured original value.
        for path in ["agents", "audit"]:
            try:
                run(["open", "http://192.168.100.209:18080/" + path]); run(["wait", "--load", "networkidle"])
                original = report.get(path, {}).get("original_page_size", "10")
                restore_size(original)
            except Exception:
                report.setdefault("restore_errors", []).append(path)
        path = QA / "safe01-page-size-keyboard.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2); stream.write("\n")
        print(json.dumps({"status": report["status"], "agents_persisted": report.get("agents", {}).get("persisted_after_hard_reload"), "audit_persisted": report.get("audit", {}).get("persisted_after_hard_reload"), "evidence": str(path.relative_to(ROOT))}, ensure_ascii=False))
