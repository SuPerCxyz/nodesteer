#!/usr/bin/env python3
"""Verify light/dark/system theme changes, media updates, persistence, and meta color."""
import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = Path(__file__).resolve().parent
BASE = "http://192.168.100.209:18080"
AB = ["agent-browser", "--session", "node-qa-resume-20261004", "--allowed-domains", "192.168.100.209"]
META = {"dark": "#020817", "light": "#fff"}
LABELS = {"light": "浅色", "dark": "深色", "system": "系统"}


def browser(args, stdin=None):
    result = subprocess.run(AB + args, input=stdin, text=True, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError("agent-browser " + args[0] + " failed: " + (result.stdout + result.stderr)[-200:])
    return result.stdout.strip()


def evaluate(script):
    raw = browser(["eval", "--stdin"], script)
    value = json.loads(raw)
    return json.loads(value) if isinstance(value, str) else value


def state():
    return evaluate("JSON.stringify({dark:document.documentElement.classList.contains('dark'),light:document.documentElement.classList.contains('light'),mediaDark:matchMedia('(prefers-color-scheme: dark)').matches,meta:document.querySelector('meta[name=\\\"theme-color\\\"]')?.content||null,cookie:(document.cookie.split(';').map(x=>x.trim()).find(x=>x.startsWith('vite-ui-theme='))||'').split('=').slice(1).join('=')})")


def choose(theme):
    snapshot = browser(["snapshot", "-i", "-c", "-d", "4"])
    match = re.search(r'button "切换主题"[^\n]*ref=(e\d+)', snapshot)
    if not match:
        raise RuntimeError("theme menu button missing")
    browser(["click", "@" + match.group(1)])
    snapshot = browser(["snapshot", "-i", "-c", "-d", "4"])
    match = re.search(r'menuitem "' + re.escape(LABELS[theme]) + r'" \[ref=(e\d+)\]', snapshot)
    if not match:
        raise RuntimeError("theme menu item missing: " + theme)
    browser(["click", "@" + match.group(1)])
    browser(["wait", "200"])


def set_media(value):
    browser(["set", "media", value])
    browser(["wait", "200"])


def reload_home():
    browser(["open", BASE + "/"])
    browser(["wait", "--load", "networkidle"])


def assert_state(name, expected_theme, expected_media, after_reload=False):
    current = state()
    resolved = ("dark" if current["mediaDark"] else "light") if expected_theme == "system" else expected_theme
    expected = {"dark": resolved == "dark", "light": resolved == "light", "mediaDark": expected_media == "dark", "meta": META[resolved], "cookie": expected_theme}
    ok = all(current.get(key) == value for key, value in expected.items())
    return {"step": name, "expected_theme": expected_theme, "expected_media": expected_media, "after_reload": after_reload, "observed": current, "expected": expected, "passed": ok}


def save_report(report):
    path = QA / "theme-media-roundtrip.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    reload_home()
    initial = state()
    original_cookie = initial["cookie"]
    original_media = "dark" if initial["mediaDark"] else "light"
    report = {"case": "Theme light/dark/system, media changes, reload persistence and theme-color", "tool": "agent-browser", "base_url": BASE, "initial_theme_cookie": original_cookie or "<absent>", "steps": [], "secrets_included": False}
    try:
        set_media("light")
        choose("system")
        report["steps"].append(assert_state("system-light-media", "system", "light"))
        set_media("dark")
        report["steps"].append(assert_state("system-dark-media-live-update", "system", "dark"))
        reload_home()
        report["steps"].append(assert_state("system-dark-persisted-reload", "system", "dark", True))

        choose("dark")
        report["steps"].append(assert_state("manual-dark", "dark", "dark"))
        set_media("light")
        report["steps"].append(assert_state("manual-dark-ignores-light-media", "dark", "light"))
        reload_home()
        report["steps"].append(assert_state("manual-dark-persisted-reload", "dark", "light", True))

        choose("light")
        report["steps"].append(assert_state("manual-light", "light", "light"))
        set_media("dark")
        report["steps"].append(assert_state("manual-light-ignores-dark-media", "light", "dark"))
        reload_home()
        report["steps"].append(assert_state("manual-light-persisted-reload", "light", "dark", True))
        report["status"] = "passed" if all(step["passed"] for step in report["steps"]) else "failed"
    except Exception as error:
        report["status"] = "blocked"
        report["error"] = str(error)
    finally:
        try:
            if original_cookie in LABELS:
                choose(original_cookie)
            else:
                choose("system")
                evaluate("document.cookie='vite-ui-theme=; Max-Age=0; path=/'")
            set_media(original_media)
            reload_home()
            report["restore_verified"] = state()["cookie"] == original_cookie
        except Exception:
            report["restore_verified"] = False
        save_report(report)
        print(json.dumps({"status": report["status"], "steps_passed": sum(x.get("passed") is True for x in report["steps"]), "steps_total": len(report["steps"]), "restored": report["restore_verified"], "evidence": str((QA / "theme-media-roundtrip.json").relative_to(ROOT))}, ensure_ascii=False))
