"""P-TABLE：G04-05 可访问名扫描 + G04-04/X19 键盘（Tab 顺序 / Enter 提交 / Escape 关闭与焦点返回）
+ G04-03 具体错误文案（重复名称真实 400）。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

ROUTES = ["/nodes", "/groups", "/scripts", "/tasks", "/schedules", "/applications",
          "/artifacts", "/executions", "/transfers", "/audit", "/users"]

ARIA_JS = r"""
const m = document.querySelector('main');
const ctl = [...(m?.querySelectorAll('button, a, [role="button"]') || [])];
const name = e => ((e.textContent || '').trim()
  || e.getAttribute('aria-label') || e.getAttribute('title')
  || (e.querySelector('.sr-only')?.textContent || '').trim());
const unnamed = ctl.filter(e => !name(e));
const titled = ctl.filter(e => !name(e) || e.getAttribute('aria-label') || e.getAttribute('title'));
return {
  route: location.pathname,
  controls: ctl.length,
  unnamed_count: unnamed.length,
  unnamed_samples: unnamed.slice(0, 5).map(e => e.outerHTML.slice(0, 100)),
  with_aria_or_title: titled.length,
};"""

FOCUS_JS = r"""
const a = document.activeElement;
return { tag: a?.tagName || null, aria: a?.getAttribute('aria-label') || null,
         ph: a?.getAttribute('placeholder') || null, type: a?.getAttribute('type') || null,
         text: (a?.textContent || '').trim().slice(0, 24) };
"""


def wait_ui(ms: int = 500) -> None:
    time.sleep(ms / 1000)


def aria_scan() -> dict:
    out = {"captured_at_utc": C.utc(), "routes": []}
    for r in ROUTES:
        C.ab("open", C.origin() + r, timeout=45)
        C.ab("wait", "--load", "networkidle", timeout=30)
        wait_ui(600)
        try:
            out["routes"].append(C.ab_json(ARIA_JS))
        except Exception as exc:  # noqa: BLE001
            out["routes"].append({"route": r, "harness_error": str(exc)[:200]})
    return out


def tab_order(route: str, steps: int = 10) -> dict:
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(700)
    seq = []
    for _ in range(steps):
        C.ab("press", "Tab", check=False)
        wait_ui(160)
        try:
            seq.append(C.ab_json(FOCUS_JS))
        except Exception:  # noqa: BLE001
            seq.append({"harness_error": "focus_read_failed"})
    return {"route": route, "sequence": seq}


def delete_branch(route: str, token: str, confirm: bool) -> dict:
    """真实键盘打开行操作菜单 → 删除确认框：Escape 取消或确认删除。"""
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(800)
    # 用搜索定位 owned fixture，避免误删
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    sref = None
    for line in snap.splitlines():
        if "textbox" in line and "搜索" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m:
                sref = m.group(1)
                break
    if sref:
        C.ab("fill", f"@{sref}", token, timeout=25)
        wait_ui(900)
    C.ab("network", "requests", "--clear", check=False)
    row_sel = 'tbody tr:nth-child(1) button[aria-label="操作"]'
    C.ab("focus", row_sel, check=False)
    C.ab("press", "Enter", check=False)   # 打开菜单
    wait_ui(600)
    menu = C.ab_json(r"""
    return { items: [...document.querySelectorAll('[role="menuitem"]')].map(x => x.textContent.trim()) };
    """)
    C.ab("find", "role", "menuitem", "click", "--name", "删除", check=False)
    wait_ui(800)
    dialog = C.ab_json(r"""
    const d = document.querySelector('[role="alertdialog"]');
    return { present: !!d, text: d ? d.innerText.trim().slice(0, 140) : null };
    """)
    if confirm:
        C.ab("find", "role", "button", "click", "--name", "删除", check=False)
        wait_ui(1500)
    else:
        C.ab("press", "Escape", check=False)
        wait_ui(900)
    after = C.ab_json(r"""
    const d = document.querySelector('[role="alertdialog"]');
    const a = document.activeElement;
    const t = localStorage.getItem('nodesteer_token');
    return { dialog_present: !!d, focused_tag: a?.tagName || null,
             focused_aria: a?.getAttribute('aria-label') || null,
             focused_text: (a?.textContent || '').trim().slice(0, 20) };
    """)
    reqs = C.ab("network", "requests", check=False).stdout
    delete_count = sum(1 for l in reqs.splitlines() if " DELETE " in l)
    return {"route": route, "confirm_mode": confirm, "menu_items": menu.get("items"),
            "dialog": dialog, "after": after, "delete_requests": delete_count}


def duplicate_name_error(token: str) -> dict:
    """真实 400 文案：用已存在的分组名再次创建。"""
    C.ab("open", C.origin() + "/groups/new", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(800)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    refs = {}
    for line in snap.splitlines():
        m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
        if not m:
            continue
        if "textbox" in line and "name" not in refs:
            refs["name"] = m.group(1)
        if "button" in line and ("保存" in line or "创建" in line or "提交" in line):
            refs.setdefault("submit", m.group(1))
    if "name" not in refs:
        return {"harness_error": "no_name_input"}
    C.ab("fill", f"@{refs['name']}", f"ptable-{token}-g01", timeout=25)
    wait_ui(300)
    C.ab("find", "role", "button", "click", "--name", "保存", check=False)
    wait_ui(1600)
    texts = C.ab_json(r"""
    const m = document.querySelector('main');
    const alerts = [...(m?.querySelectorAll('[role="alert"]') || [])].map(e => e.innerText.trim());
    const toast = [...document.querySelectorAll('[data-sonner-toast], [role="status"]')].map(e => e.innerText.trim());
    return { url: location.pathname, alerts, toast, mainHasError: /失败|错误|已存在|重复|error/i.test(m?.innerText || '') };
    """)
    return {"submitted_existing_name": f"ptable-{token}-g01", "observed": texts}


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    token = led["nonce"]
    result = {"captured_at_utc": C.utc()}
    which = sys.argv[1:] or ["aria", "tab", "delete", "dup"]
    if "aria" in which:
        result["G04-05_aria"] = aria_scan()
        print(json.dumps({"aria": [(r.get("route"), r.get("unnamed_count")) for r in result["G04-05_aria"]["routes"]]},
                         ensure_ascii=False), flush=True)
    if "tab" in which:
        result["G04-04_tab_new_group"] = tab_order("/groups/new", steps=9)
        print(json.dumps({"tab": [s.get("text") or s.get("ph") or s.get("tag")
                                  for s in result["G04-04_tab_new_group"]["sequence"]]},
                         ensure_ascii=False), flush=True)
        # Enter 提交
        C.ab("open", C.origin() + "/groups/new", timeout=45)
        C.ab("wait", "--load", "networkidle", timeout=30)
        wait_ui(700)
        snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
        nref = None
        for line in snap.splitlines():
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m and "textbox" in line:
                nref = m.group(1)
                break
        C.ab("network", "requests", "--clear", check=False)
        newname = f"ptable-{token}-enter-submit"
        if nref:
            C.ab("fill", f"@{nref}", newname, timeout=25)
            C.ab("focus", f"@{nref}", check=False)
            C.ab("press", "Enter", check=False)
            wait_ui(2200)
        reqs = C.ab("network", "requests", check=False).stdout
        posts = [l for l in reqs.splitlines() if " POST " in l and "/api/groups" in l]
        after = C.ab_json(r"""
        const t = localStorage.getItem('nodesteer_token');
        const r = await fetch('/api/groups', { headers: { Authorization: 'Bearer ' + t } });
        const b = await r.json();
        return { url: location.pathname, groups: Array.isArray(b) ? b.length : null,
                 created: Array.isArray(b) ? b.some(g => g.name === __NAME__) : false };
        """.replace("__NAME__", json.dumps(newname)))
        result["G04-04_enter_submit"] = {"posts": len(posts), "after": after,
                                        "post_lines": [l[-90:] for l in posts]}
        print(json.dumps(result["G04-04_enter_submit"], ensure_ascii=False), flush=True)
    if "dup" in which:
        result["G04-03_duplicate_name"] = duplicate_name_error(token)
        print(json.dumps(result["G04-03_duplicate_name"], ensure_ascii=False)[:600], flush=True)
    if "delete" in which:
        result["G04-02_escape_cancel"] = delete_branch("/groups", token, confirm=False)
        result["G04-02_confirm_delete"] = delete_branch("/scripts", token, confirm=True)
        result["G04-02_confirm_delete_application"] = delete_branch("/applications", token, confirm=True)
        for k in ("G04-02_escape_cancel", "G04-02_confirm_delete", "G04-02_confirm_delete_application"):
            v = result[k]
            print(json.dumps({k: {"menu": v.get("menu_items"), "dialog": bool((v.get("dialog") or {}).get("present")),
                                  "delete": v.get("delete_requests"),
                                  "focus_return": (v.get("after") or {}).get("focused_aria")}}, ensure_ascii=False), flush=True)
    rel = C.write_evidence("G04-keyboard-a11y.json", result)
    print(json.dumps({"evidence": rel}, ensure_ascii=False))


if __name__ == "__main__":
    main()
