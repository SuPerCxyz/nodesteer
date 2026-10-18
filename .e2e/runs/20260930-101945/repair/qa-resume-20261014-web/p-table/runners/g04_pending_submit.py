"""P-TABLE：G04-01 提交按钮 pending 表现（真实延迟 POST + 重复点击）。

观测：pending 期间按钮是否禁用、重复点击是否产生第二次 POST、最终对象是否重复创建。
"""
from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

CHECK = {
    "groups_editor_save": ("/groups/new", "/api/groups", "/groups"),
    "scripts_editor_save": ("/scripts/new", "/api/scripts", "/scripts"),
    "tasks_schedule_save": ("/schedules/new", "/api/schedules", "/schedules"),
}


def env() -> dict:
    e = dict(os.environ)
    e["PTABLE_SESSION"] = C.SESSION
    e["PTABLE_ORIGIN"] = C.origin()
    return e


def start_holder(path: str, route: str, hold_ms: int):
    proc = subprocess.Popen(
        ["node", str(Path(__file__).resolve().parent / "post-holder.mjs"),
         path, route, str(hold_ms)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, env=env())
    lines, ready = [], None
    deadline = time.time() + 12
    while time.time() < deadline:
        r, _, _ = select.select([proc.stdout], [], [], 0.2)
        if r:
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
            try:
                obj = json.loads(line)
                if obj.get("phase") == "READY":
                    ready = obj
                    break
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is not None:
            break
    if not ready:
        proc.kill()
        out, err = proc.communicate(timeout=5)
        return None, lines, {"harness_error": "ready_timeout", "stderr": err.strip()[:200]}
    return proc, lines, ready


def finish(proc, lines, timeout=40):
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=5)
        lines.append("runner_timeout")
    lines.extend(l.strip() for l in out.splitlines())
    return lines


def btn_state(label: str) -> dict:
    return C.ab_json(r"""
    const btn = [...document.querySelectorAll('main button')].find(b => (b.textContent || '').trim() === __LABEL__);
    if (!btn) return { found: false };
    return { found: true, disabled: !!btn.disabled, ariaDisabled: btn.getAttribute('aria-disabled'),
             text: btn.textContent.trim() };
    """.replace("__LABEL__", json.dumps(label)))


def run_check(name: str, hold_ms: int = 4500) -> dict:
    route, path, _back = CHECK[name]
    rec = {"check": name, "route": route, "post_path": path, "hold_ms": hold_ms,
           "captured_at_utc": C.utc()}
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    time.sleep(0.9)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    refs = {}
    for line in snap.splitlines():
        m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
        if not m:
            continue
        if "textbox" in line and "name" not in refs:
            refs["name"] = m.group(1)
        if "button" in line and "保存" in line:
            refs.setdefault("save", m.group(1))
    rec["refs_found"] = sorted(refs)
    uniq = f"ptable-{json.loads((C.ROOT / 'instance-ledger.json').read_text())['nonce']}-pending-{name[:6]}"
    if "name" in refs:
        C.ab("fill", f"@{refs['name']}", uniq, timeout=25)
        time.sleep(0.3)
    proc, lines, ready = start_holder(path, route, hold_ms)
    if not proc:
        rec["holder_error"] = ready
        return rec
    C.ab("network", "requests", "--clear", check=False)
    # 第一次点击（真实点击保存）
    if "save" in refs:
        C.ab("click", f"@{refs['save']}", check=False)
    else:
        C.ab("find", "role", "button", "click", "--name", "保存", check=False)
    time.sleep(0.9)
    rec["pending_button"] = btn_state("保存")
    # pending 期间再次点击
    if "save" in refs:
        C.ab("click", f"@{refs['save']}", check=False)
    else:
        C.ab("find", "role", "button", "click", "--name", "保存", check=False)
    time.sleep(0.9)
    rec["pending_button_after_second_click"] = btn_state("保存")
    lines = finish(proc, lines, timeout=hold_ms / 1000 + 25)
    parsed = [json.loads(x) for x in lines if x.startswith("{")]
    rec["holder"] = parsed
    time.sleep(1.5)
    reqs = C.ab("network", "requests", check=False).stdout
    posts = [l[-120:] for l in reqs.splitlines() if " POST " in l and path in l]
    rec["post_lines"] = posts
    rec["post_count"] = len(posts)
    rec["unique_name"] = uniq
    return rec


def main() -> None:
    out = {"captured_at_utc": C.utc(), "checks": []}
    for name in ("groups_editor_save", "scripts_editor_save"):
        try:
            out["checks"].append(run_check(name))
        except Exception as exc:  # noqa: BLE001
            out["checks"].append({"check": name, "harness_error": str(exc)[:250]})
        c = out["checks"][-1]
        print(json.dumps({"check": c.get("check"), "post_count": c.get("post_count"),
                          "pending_disabled": (c.get("pending_button") or {}).get("disabled"),
                          "holder_phases": [x.get("phase") for x in (c.get("holder") or [])]},
                         ensure_ascii=False), flush=True)
    rel = C.write_evidence("G04-01-pending-submit.json", out)
    print(json.dumps({"evidence": rel}, ensure_ascii=False))


if __name__ == "__main__":
    main()
