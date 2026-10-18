#!/usr/bin/env python3
"""Compact agent-browser driver for the webgap batch (own session only).

Usage:
    python3 ui.py open /agents
    python3 ui.py js 'document.querySelectorAll("table").length'
    python3 ui.py shot X04-example.png
    python3 ui.py req            # last network requests (compact)
"""
import json
import subprocess
import sys
import time
from pathlib import Path

SESSION = "qawebgap-6456ada2"
ORIGIN = "http://192.168.100.209:18080"
BATCH = Path(__file__).resolve().parent.parent
EV = BATCH / "evidence"


def run(*args, stdin=None, timeout=120):
    cmd = ["agent-browser", "--session", SESSION] + list(args)
    cp = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def value(out):
    i = out.find("{")
    j = out.find("[")
    if i < 0 or (0 <= j < i):
        i = j
    if i < 0:
        return out.strip()
    try:
        return json.loads(out[i:])
    except Exception:
        return out[i:]


def js(code):
    out, err, rc = run("eval", "--stdin", stdin=code)
    if rc != 0:
        raise RuntimeError("eval failed: %s" % (err[-400:] or out[-400:]))
    return value(out)


def open_(path, wait=1.4):
    run("open", ORIGIN + path)
    time.sleep(wait)


def click(selector):
    out, err, rc = run("click", selector)
    time.sleep(0.9)
    return rc == 0


def click_js(selector):
    return js(r'''(() => {
      const el = document.querySelector(%s);
      if (!el) return {clicked:false};
      el.scrollIntoView({block:'center'});
      el.click();
      return {clicked:true, text:(el.innerText||'').trim()};
    })()''' % json.dumps(selector))


def find_click(role, name):
    out, err, rc = run("find", role, "click", "--name", name)
    time.sleep(0.9)
    return rc == 0


def fill(selector, text):
    out, err, rc = run("fill", selector, text)
    time.sleep(0.4)
    return rc == 0


def shot(name, full=False):
    EV.mkdir(parents=True, exist_ok=True)
    args = ["screenshot", str(EV / name)]
    if full:
        args.append("--full")
    out, err, rc = run(*args)
    return (EV / name).exists()


def requests(limit=120):
    out, err, rc = run("network", "requests")
    lines = [l for l in out.splitlines() if l.strip()]
    return lines[-limit:]


def url():
    return run("get", "url")[0].strip()


def write_ev(name, obj):
    EV.mkdir(parents=True, exist_ok=True)
    p = EV / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    return str(p)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "open":
        open_(sys.argv[2])
        print(url())
    elif cmd == "js":
        print(json.dumps(js(sys.argv[2]), ensure_ascii=False, indent=1)[:8000])
    elif cmd == "jsf":
        print(json.dumps(js(Path(sys.argv[2]).read_text()), ensure_ascii=False, indent=1)[:12000])
    elif cmd == "shot":
        print(shot(sys.argv[2], full=("--full" in sys.argv)))
    elif cmd == "click":
        print(click(sys.argv[2]))
    elif cmd == "clickjs":
        print(json.dumps(click_js(sys.argv[2]), ensure_ascii=False))
    elif cmd == "find":
        print(find_click(sys.argv[2], sys.argv[3]))
    elif cmd == "req":
        print("\n".join(requests(int(sys.argv[2]) if len(sys.argv) > 2 else 120)))
    elif cmd == "url":
        print(url())
    else:
        raise SystemExit("unknown command " + cmd)
