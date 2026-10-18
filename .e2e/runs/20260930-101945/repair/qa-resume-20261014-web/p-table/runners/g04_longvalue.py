"""P-TABLE：G04-07 / X20 长值边界与窄屏（真实页面测量与截图）。

覆盖：长命令（2144 字符）精确渲染/可选/横向容纳、长中文名称行、760 与 480 窄屏下的
表格/表单/行菜单/确认框是否溢出或遮挡。
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

LONG_CMD = "echo " + ("A" * 2139)


def wait_ui(ms: int = 500) -> None:
    time.sleep(ms / 1000)


def shot(name: str) -> str:
    p = C.EVID / name
    C.ab("screenshot", str(p), check=False)
    return f"evidence/{name}"


def set_viewport(w: int, h: int) -> None:
    C.ab("set", "viewport", str(w), str(h), check=False)
    wait_ui(600)


def overflow(route: str) -> dict:
    return C.ab_json(r"""
    const m = document.querySelector('main');
    const de = document.documentElement;
    return { route: location.pathname, viewport: window.innerWidth,
             doc_scrollWidth: de.scrollWidth, doc_clientWidth: de.clientWidth,
             main_scrollWidth: m ? m.scrollWidth : null, main_clientWidth: m ? m.clientWidth : null,
             page_horizontal_overflow: de.scrollWidth > de.clientWidth + 1 };
    """)


def long_command_check(task_id: str) -> dict:
    C.ab("open", C.origin() + f"/tasks/{task_id}", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(900)
    return C.ab_json(r"""
    const expected = __CMD__;
    const pre = document.querySelector('main pre');
    const runLink = [...document.querySelectorAll('main a')].find(a => (a.getAttribute('href') || '').endsWith('/run'));
    let selLen = null;
    if (pre) {
      const r = document.createRange(); r.selectNodeContents(pre);
      const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
      selLen = s.toString().length;
      s.removeAllRanges();
    }
    const cs = pre ? getComputedStyle(pre) : null;
    const m = document.querySelector('main');
    return {
      route: location.pathname,
      command_present: !!pre,
      command_exact: !!pre && pre.textContent === expected,
      command_length: pre ? pre.textContent.length : null,
      expected_length: expected.length,
      selectable: !!pre && cs.userSelect !== 'none' && cs.webkitUserSelect !== 'none',
      selection_length: selLen,
      overflow_style: cs ? cs.overflowX : null,
      pre_scrollWidth: pre ? pre.scrollWidth : null,
      pre_clientWidth: pre ? pre.clientWidth : null,
      horizontally_scrollable: !!pre && pre.scrollWidth > pre.clientWidth,
      page_overflow: m ? m.scrollWidth > m.clientWidth + 1 : null,
      run_link_visible: !!runLink && runLink.offsetParent !== null,
      run_link_href: runLink ? runLink.getAttribute('href') : null,
      viewport: window.innerWidth,
    };
    """.replace("__CMD__", json.dumps(LONG_CMD)))


def long_name_row(token: str) -> dict:
    C.ab("open", C.origin() + "/groups", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(700)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    sref = None
    for line in snap.splitlines():
        if "textbox" in line and "搜索" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            sref = m.group(1) if m else None
            break
    if sref:
        C.ab("fill", f"@{sref}", "长中文名称", timeout=25)
        wait_ui(1000)
    return C.ab_json(r"""
    const m = document.querySelector('main');
    const cells = [...(m?.querySelectorAll('tbody tr td') || [])];
    const long = cells.filter(c => (c.innerText || '').trim().length >= 20);
    const first = long[0];
    return {
      route: location.pathname,
      rows: m?.querySelectorAll('tbody tr').length,
      long_cells: long.length,
      long_cell_len: first ? first.innerText.trim().length : null,
      has_title_attr: first ? first.getAttribute('title') : null,
      cell_scrollWidth: first ? first.scrollWidth : null,
      cell_clientWidth: first ? first.clientWidth : null,
      table_overflow_x: (() => { const t = m?.querySelector('div.overflow-x-auto, div.overflow-auto'); return t ? getComputedStyle(t).overflowX : null; })(),
      page_overflow: m ? m.scrollWidth > m.clientWidth + 1 : null,
    };
    """)


def narrow_dialog(route: str, token: str) -> dict:
    C.ab("open", C.origin() + route, timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(800)
    snap = C.ab("snapshot", "-i", "-c", timeout=25).stdout
    sref = None
    for line in snap.splitlines():
        if "textbox" in line and "搜索" in line:
            m = re.search(r"ref=([A-Za-z0-9_-]+)", line)
            sref = m.group(1) if m else None
            break
    if sref:
        C.ab("fill", f"@{sref}", token, timeout=25)
        wait_ui(1000)
    before = overflow(route)
    C.ab("click", 'tbody tr:nth-child(1) button[aria-label="操作"]', check=False)
    wait_ui(600)
    menu_geom = C.ab_json(r"""
    const menu = document.querySelector('[role="menu"]');
    if (!menu) return { menu_present: false };
    const r = menu.getBoundingClientRect();
    return { menu_present: true, left: Math.round(r.left), right: Math.round(r.right),
             top: Math.round(r.top), bottom: Math.round(r.bottom),
             within_viewport: r.left >= 0 && r.right <= window.innerWidth + 1,
             viewport: window.innerWidth };
    """)
    mshot = shot(f"X20-narrow-{route.strip('/').replace('/', '-')}-menu-480.png")
    C.ab("find", "role", "menuitem", "click", "--name", "删除", check=False)
    wait_ui(800)
    dlg_geom = C.ab_json(r"""
    const d = document.querySelector('[role="alertdialog"]');
    if (!d) return { dialog_present: false };
    const r = d.getBoundingClientRect();
    const btns = [...d.querySelectorAll('button')].map(b => {
      const br = b.getBoundingClientRect();
      return { text: b.textContent.trim(), visible: b.offsetParent !== null,
               within_viewport: br.left >= -1 && br.right <= window.innerWidth + 1 };
    });
    return { dialog_present: true, left: Math.round(r.left), right: Math.round(r.right),
             within_viewport: r.left >= -1 && r.right <= window.innerWidth + 1,
             buttons: btns, viewport: window.innerWidth };
    """)
    dshot = shot(f"X20-narrow-{route.strip('/').replace('/', '-')}-dialog-480.png")
    C.ab("press", "Escape", check=False)
    wait_ui(500)
    return {"route": route, "before": before, "menu": menu_geom, "menu_shot": mshot,
            "dialog": dlg_geom, "dialog_shot": dshot}


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    fl = json.loads((C.ROOT / "fixture-ledger.json").read_text())
    token = led["nonce"]
    task_id = fl["long_value_fixtures"]["long_task_id"]
    out = {"captured_at_utc": C.utc(), "long_command_expected_length": len(LONG_CMD)}

    # 760px：长命令
    set_viewport(760, 900)
    out["wide760_command"] = long_command_check(task_id)
    out["wide760_command_shot"] = shot("X20-long-command-760.png")
    out["wide760_groups"] = long_name_row(token)

    # 480px：详情 / 表单 / 表格 / 菜单 / 确认框
    set_viewport(480, 900)
    out["narrow480_command"] = long_command_check(task_id)
    out["narrow480_command_shot"] = shot("X20-long-command-480.png")
    out["narrow480_groups_table"] = long_name_row(token)
    out["narrow480_groups_table_shot"] = shot("X20-narrow-groups-table-480.png")
    C.ab("open", C.origin() + "/tasks/new", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    wait_ui(800)
    out["narrow480_task_form"] = overflow("/tasks/new")
    out["narrow480_task_form_shot"] = shot("X20-narrow-task-form-480.png")
    out["narrow480_delete_dialog_groups"] = narrow_dialog("/groups", token)
    out["narrow480_delete_dialog_scripts"] = narrow_dialog("/scripts", token)

    set_viewport(1280, 900)
    rel = C.write_evidence("G04-07-X20-longvalue-narrow.json", out)
    print(json.dumps({"evidence": rel,
                      "cmd_exact_760": out["wide760_command"].get("command_exact"),
                      "cmd_len_760": out["wide760_command"].get("command_length"),
                      "selectable": out["wide760_command"].get("selectable"),
                      "sel_len": out["wide760_command"].get("selection_length"),
                      "pre_hscroll": out["wide760_command"].get("horizontally_scrollable"),
                      "page_overflow_760": out["wide760_command"].get("page_overflow"),
                      "run_visible": out["wide760_command"].get("run_link_visible"),
                      "long_name_cells": out["wide760_groups"].get("long_cells"),
                      "narrow_form_overflow": out["narrow480_task_form"].get("page_horizontal_overflow"),
                      "dialog_within": (out["narrow480_delete_dialog_groups"].get("dialog") or {}).get("within_viewport")},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
