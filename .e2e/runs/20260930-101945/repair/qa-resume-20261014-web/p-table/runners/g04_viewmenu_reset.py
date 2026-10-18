"""P-TABLE：G03-04 Reset（重置）清空搜索/筛选 + G03-05/X20 视图菜单列隐藏-恢复（补充复核）。"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

TABLES = [
    ("nodes", "/nodes", "/api/nodes", "nodes"),
    ("groups", "/groups", "/api/groups", "groups"),
    ("scripts", "/scripts", "/api/scripts", "scripts"),
    ("tasks", "/tasks", "/api/tasks", "tasks"),
    ("schedules", "/schedules", "/api/schedules", "schedules"),
    ("applications", "/applications", "/api/applications", "applications"),
    ("artifacts", "/artifacts", "/api/artifacts", "artifacts"),
    ("executions", "/executions", "/api/executions", "executions"),
    ("transfers", "/transfers", "/api/transfers", "transfers"),
    ("audit", "/audit", "/api/audit", "audit"),
    ("users", "/users", "/api/users", "users"),
]
VIEW_TABLES = {"nodes", "scripts", "transfers", "audit"}

STATE_JS = r"""
const key = __KEY__;
const m = document.querySelector('main');
const rows = [...(m?.querySelectorAll('tbody tr') || [])];
const txt = m ? m.innerText : '';
const btns = [...(m?.querySelectorAll('button') || [])];
let view = null; try { view = localStorage.getItem('nodesteer.table.' + key); } catch (e) {}
return {
  rows: rows.length,
  noResults: /没有结果|No results/.test(txt),
  pageOf: (txt.match(/第\s*\d+\s*页，共\s*\d+\s*页/) || [])[0] || null,
  reset_visible: btns.some(b => b.textContent.trim().startsWith('重置')),
  view_menu_visible: btns.some(b => b.textContent.trim().startsWith('视图')),
  headers: [...(m?.querySelectorAll('thead th') || [])].map(th => th.innerText.trim()).filter(Boolean),
  localView: view,
};"""


def wait_ui(ms: int = 600) -> None:
    time.sleep(ms / 1000)


def state(key: str) -> dict:
    return C.ab_json(STATE_JS.replace("__KEY__", json.dumps(key)))


def search_ref() -> str | None:
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    for line in snap.splitlines():
        if "textbox" in line and "搜索" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            if m:
                return m.group(1)
    return None


def run_table(name: str, route: str, key: str, token: str) -> dict:
    rec = {"table": name, "route": route, "captured_at_utc": C.utc()}
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(700)
    rec["initial"] = state(key)
    ref = search_ref()
    rec["search_input_found"] = bool(ref)
    if ref:
        C.ab("fill", f"@{ref}", f"zzz-reset-{token}", timeout=25)
        wait_ui(1000)
        rec["after_no_match"] = state(key)
        clicked = C.ab("find", "role", "button", "click", "--name", "重置", check=False).returncode == 0
        wait_ui(1200)
        rec["reset_clicked"] = clicked
        rec["after_reset"] = state(key)
    if name in VIEW_TABLES:
        snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
        vref = None
        for line in snap.splitlines():
            if "button" in line and "视图" in line:
                m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
                if m:
                    vref = m.group(1)
                    break
        rec["view_menu_found"] = bool(vref)
        if vref:
            C.ab("click", f"@{vref}", check=False)
            wait_ui(700)
            items = C.ab_json(r"""
            return [...document.querySelectorAll('[role="menuitemcheckbox"]')]
              .map(e => ({ label: e.textContent.trim(), checked: e.getAttribute('aria-checked') === 'true' }));
            """)
            rec["view_items"] = items
            # 全部取消勾选（隐藏所有可隐藏列）
            for _ in range(len([i for i in items if i["checked"]])):
                done = C.ab_json(r"""
                const items = [...document.querySelectorAll('[role="menuitemcheckbox"]')];
                const target = items.find(e => e.getAttribute('aria-checked') === 'true');
                if (!target) return { clicked: false };
                target.click();
                return { clicked: true, label: target.textContent.trim() };
                """)
                if not done.get("clicked"):
                    break
                wait_ui(320)
            C.ab("press", "Escape", check=False)
            wait_ui(600)
            rec["all_hidden"] = state(key)
            C.ab("reload", timeout=30)
            C.ab("wait", "--load", "networkidle", timeout=30)
            wait_ui(800)
            rec["after_reload_hidden"] = state(key)
            # 恢复
            C.ab("open", C.origin() + route, timeout=45)
            C.ab("wait", "--load", "networkidle", timeout=30)
            wait_ui(700)
            snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
            vref2 = None
            for line in snap.splitlines():
                if "button" in line and "视图" in line:
                    m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
                    if m:
                        vref2 = m.group(1)
                        break
            if vref2:
                C.ab("click", f"@{vref2}", check=False)
                wait_ui(700)
                restored = C.ab_json(r"""
                const items = [...document.querySelectorAll('[role="menuitemcheckbox"]')];
                for (const e of items) {
                  if (e.getAttribute('aria-checked') !== 'true') e.click();
                }
                return items.map(e => e.textContent.trim());
                """)
                rec["restore_clicked"] = restored
                C.ab("press", "Escape", check=False)
                wait_ui(700)
                rec["after_restore"] = state(key)
    return rec


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    token = led["nonce"]
    C.ab("set", "viewport", "1280", "900", check=False)
    wait_ui(400)
    out = {"captured_at_utc": C.utc(), "tables": []}
    for name, route, _api, key in TABLES:
        try:
            rec = run_table(name, route, key, token)
        except Exception as exc:  # noqa: BLE001
            rec = {"table": name, "harness_error": str(exc)[:250]}
        out["tables"].append(rec)
        print(json.dumps({"table": name,
                          "reset_visible_after_search": (rec.get("after_no_match") or {}).get("reset_visible"),
                          "reset_clicked": rec.get("reset_clicked"),
                          "rows_after_reset": (rec.get("after_reset") or {}).get("rows"),
                          "no_results_after_reset": (rec.get("after_reset") or {}).get("noResults"),
                          "view_menu": rec.get("view_menu_found"),
                          "headers_hidden": len((rec.get("all_hidden") or {}).get("headers") or []),
                          "headers_initial": len((rec.get("initial") or {}).get("headers") or []),
                          "after_restore_headers": len((rec.get("after_restore") or {}).get("headers") or [])},
                         ensure_ascii=False), flush=True)
    rel = C.write_evidence("G03-04-05-reset-viewmenu.json", out)
    print(json.dumps({"evidence": rel}, ensure_ascii=False))


if __name__ == "__main__":
    main()
