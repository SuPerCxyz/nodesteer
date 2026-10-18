#!/usr/bin/env python3
"""G04-02 delete-menu confirm dialog: cancel = zero write, confirm = single DELETE.

Also cleans the extra owned scripts created during the P07_P08-11 boundary probes.
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pweb  # noqa: E402
import ui  # noqa: E402

PREFIX = pweb.PREFIX
RES = {}


def toasts():
    return ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast],[data-sonner-toaster] li'));
      return {n:t.length,texts:t.map(x=>x.innerText.trim())}})()''')


def row_idx(name):
    v = ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));return rows.findIndex(x=>x.innerText.includes(%s))})()" % json.dumps(name))
    try:
        return int(str(v).strip().strip('"'))
    except Exception:
        return -1


def open_row_menu(name):
    i = row_idx(name)
    if i < 0:
        return False
    ui.run("click", "table tbody tr:nth-child(%d) button[aria-label='操作']" % (i + 1))
    time.sleep(1.2)
    return bool(ui.js("(()=>Array.from(document.querySelectorAll('[role=menuitem]')).length)()"))


def click_menuitem(text):
    ui.run("find", "role", "menuitem", "click", "--name", text)
    time.sleep(1.3)


def dialog_state():
    return ui.js(r'''(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');
      return d?{role:d.getAttribute('role'),text:d.innerText.replace(/\n+/g,' | ').slice(0,400),
        buttons:Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)}:{role:null}})()''')


def click_dialog(which):
    """Real click on the dialog button whose innerText matches the JS predicate."""
    js = r'''(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');if(!d)return -1;
      const bs=Array.from(d.querySelectorAll('button'));
      return bs.findIndex(x=>%s)})()''' % which
    try:
        idx = int(str(ui.js(js)).strip().strip('"'))
    except Exception:
        idx = -1
    if idx < 0:
        return False
    ui.run("click", "[role=alertdialog] button:nth-of-type(%d), [role=dialog] button:nth-of-type(%d)"
           % (idx + 1, idx + 1))
    time.sleep(1.8)
    return True


def main():
    tok = pweb.tok()
    st, sc = pweb.j("GET", "/api/scripts")
    owned = [s for s in pweb.lst(sc) if s["name"].startswith(PREFIX)]
    RES["owned_scripts_before"] = [{"name": s["name"], "id": s["id"], "revision": s.get("revision")} for s in owned]

    main_script = next((s for s in owned if s["name"] == PREFIX + "-script"), None)
    extras = [s for s in owned if s is not main_script]

    # ---- G04-02 with the main script -------------------------------------------------
    g = {"subject": {"id": main_script["id"], "name": main_script["name"]}}
    ui.open_("/scripts", 2.5)
    g["row_menu_opened"] = open_row_menu(main_script["name"])
    g["menu_items"] = ui.js("(()=>Array.from(document.querySelectorAll('[role=menuitem]')).map(m=>m.innerText.trim()))()")
    ui.shot("G04-02-script-row-actions.png")
    click_menuitem("删除")
    g["confirm_dialog"] = dialog_state()
    ui.shot("G04-02-script-delete-confirm.png", full=True)
    before_http = pweb._call("GET", "/api/scripts/" + main_script["id"])[0]
    # cancel
    click_dialog("(x.innerText.trim()==='取消')")
    g["after_cancel"] = {"dialog_present": ui.js("(()=>!!document.querySelector('[role=alertdialog],[role=dialog]'))()"),
                         "get_http": pweb._call("GET", "/api/scripts/" + main_script["id"])[0],
                         "get_before": before_http}
    g["cancel_delete_requests"] = [l for l in ui.requests(300) if "DELETE" in l and "/api/scripts" in l]
    ui.shot("G04-02-script-after-cancel.png", full=True)
    # confirm
    ui.open_("/scripts", 2.0)
    open_row_menu(main_script["name"])
    click_menuitem("删除")
    g["confirm_dialog_2"] = dialog_state()
    click_dialog("(/删除/.test(x.innerText.trim()) && !/取消/.test(x.innerText))")
    time.sleep(1.5)
    g["confirm_toasts"] = toasts()
    g["get_after_confirm"] = pweb._call("GET", "/api/scripts/" + main_script["id"])[0]
    g["confirm_delete_requests"] = [l for l in ui.requests(400) if "DELETE" in l and "/api/scripts" in l]
    ui.shot("G04-02-script-after-delete.png", full=True)
    RES["G04-02"] = g

    # ---- clean the extra owned scripts via UI (same confirm path) --------------------
    cleaned = []
    for s in extras:
        ui.open_("/scripts", 2.0)
        opened = open_row_menu(s["name"])
        click_menuitem("删除")
        click_dialog("(/删除/.test(x.innerText.trim()) && !/取消/.test(x.innerText))")
        time.sleep(1.2)
        cleaned.append({"name": s["name"], "id": s["id"], "row_menu_opened": opened,
                        "get_after": pweb._call("GET", "/api/scripts/" + s["id"])[0]})
    RES["extras_cleaned"] = cleaned
    RES["owned_scripts_after"] = [s["name"] for s in pweb.mine("/api/scripts")]
    ui.write_ev("G04-02-P07_P08-delete-and-cleanup.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:4000])


main()
