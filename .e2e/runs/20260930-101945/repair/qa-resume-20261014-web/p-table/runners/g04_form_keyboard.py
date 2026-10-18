"""P-TABLE：G04-04/X19 表单内 Tab 顺序与 Enter 提交的真实复核（含失败留证）。"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

FOCUS_JS = r"""
const a = document.activeElement;
return { tag: a?.tagName || null, aria: a?.getAttribute('aria-label') || null,
         ph: a?.getAttribute('placeholder') || null, type: a?.getAttribute('type') || null,
         text: (a?.textContent || '').trim().slice(0, 20) };
"""


def wait_ui(ms: int = 500) -> None:
    time.sleep(ms / 1000)


def form_keyboard(token: str) -> dict:
    out = {"route": "/groups/new", "captured_at_utc": C.utc()}
    C.ab("open", C.origin() + "/groups/new", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(800)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    refs = []
    for line in snap.splitlines():
        m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
        if m and ("textbox" in line or "button" in line):
            refs.append((line.strip()[:80], m.group(1)))
    out["form_controls"] = [r[0] for r in refs]
    name_ref = next((r[1] for r in refs if "textbox" in r[0]), None)
    if not name_ref:
        out["harness_error"] = "no_name_field"
        return out
    newname = f"ptable-{token}-tab-enter"
    C.ab("fill", f"@{name_ref}", newname, timeout=25)
    C.ab("focus", f"@{name_ref}", check=False)
    wait_ui(300)
    seq = []
    for _ in range(4):
        C.ab("press", "Tab", check=False)
        wait_ui(220)
        try:
            seq.append(C.ab_json(FOCUS_JS))
        except Exception:  # noqa: BLE001
            seq.append({"harness_error": "focus_read_failed"})
    out["tab_sequence_from_name_field"] = seq

    # Enter 提交（在名称字段内）
    C.ab("focus", f"@{name_ref}", check=False)
    C.ab("network", "requests", "--clear", check=False)
    C.ab("press", "Enter", check=False)
    wait_ui(2500)
    reqs = C.ab("network", "requests", check=False).stdout
    posts = [l[-110:] for l in reqs.splitlines() if " POST " in l and "/api/groups" in l]
    after = C.ab_json(r"""
    const d = document.querySelector('[role="alertdialog"], [role="alert"]');
    const m = document.querySelector('main');
    return { url: location.pathname, alert: d ? d.innerText.trim().slice(0, 120) : null,
             main_has_name: !!m && m.innerText.includes(__NAME__) };
    """.replace("__NAME__", json.dumps(newname)))
    out["enter_submit"] = {"posts": len(posts), "post_lines": posts, "after": after}

    # 对照组：显式点击“保存”
    if len(posts) == 0:
        C.ab("network", "requests", "--clear", check=False)
        C.ab("find", "role", "button", "click", "--name", "保存", check=False)
        wait_ui(2200)
        reqs2 = C.ab("network", "requests", check=False).stdout
        posts2 = [l[-110:] for l in reqs2.splitlines() if " POST " in l and "/api/groups" in l]
        out["save_button_click"] = {"posts": len(posts2),
                                    "url_after": C.ab("get", "url", check=False).stdout.strip()}
    return out


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    out = form_keyboard(led["nonce"])
    rel = C.write_evidence("G04-04-form-keyboard.json", out)
    print(json.dumps({"evidence": rel,
                      "tab": [f"{s.get('tag')}:{s.get('ph') or s.get('text') or s.get('aria')}" for s in
                              out.get("tab_sequence_from_name_field", [])],
                      "enter_posts": (out.get("enter_submit") or {}).get("posts"),
                      "enter_after": (out.get("enter_submit") or {}).get("after"),
                      "save_click": out.get("save_button_click")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
