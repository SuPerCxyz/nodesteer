#!/usr/bin/env python3
"""Clean, repeatable script lifecycle via real UI: P07_P08-04 / P07_P08-11 + G04-02 clean-up."""
import json
import re
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pweb  # noqa: E402
import ui  # noqa: E402

PREFIX = pweb.PREFIX
RES = {}


def snap():
    return ui.run("snapshot", "-i")[0]


def ref_of(text, role="button"):
    for l in snap().splitlines():
        if '%s "%s"' % (role, text) in l and "ref=" in l:
            return re.search(r"ref=(e\d+)", l).group(1)


def toasts():
    return ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast],[data-sonner-toaster] li'));
      return {n:t.length,texts:t.map(x=>x.innerText.trim())}})()''')


def set_label(label, value):
    return ui.js(r'''(() => {
      const lb = Array.from(document.querySelectorAll('main label')).find(l => l.innerText.trim().replace(/\n/g,' ').startsWith(%s));
      if (!lb) return {ok:false, reason:'label'};
      const el = lb.querySelector('input,textarea') || (lb.parentElement && lb.parentElement.querySelector('input,textarea'));
      if (!el) return {ok:false, reason:'control'};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(el, %s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return {ok:true, tag:el.tagName, len:el.value.length};
    })()''' % (json.dumps(label), json.dumps(value)))


def set_content(value):
    return ui.js(r'''(() => {
      const el = document.querySelector('main textarea') || Array.from(document.querySelectorAll('main input')).find(i=>i.placeholder==='内容');
      if (!el) return {ok:false};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto,'value').set.call(el, %s);
      el.dispatchEvent(new Event('input',{bubbles:true}));
      el.dispatchEvent(new Event('change',{bubbles:true}));
      return {ok:true, tag:el.tagName, len:el.value.length};
    })()''' % json.dumps(value))


def save():
    ui.run("click", "button:has-text('保存')") if False else ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.0)


def row_idx(name):
    v = ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));return rows.findIndex(x=>x.innerText.includes(%s))})()" % json.dumps(name))
    try:
        return int(str(v).strip().strip('"'))
    except Exception:
        return -1


def delete_via_ui(name):
    """Real UI delete: row action menu -> 删除 -> confirm 删除. Returns evidence dict."""
    out = {}
    ui.open_("/scripts", 2.2)
    i = row_idx(name)
    out["row_index"] = i
    if i < 0:
        return out
    ui.run("click", "table tbody tr:nth-child(%d) button[aria-label='操作']" % (i + 1))
    time.sleep(1.2)
    out["menu_items"] = ui.js("(()=>Array.from(document.querySelectorAll('[role=menuitem]')).map(m=>m.innerText.trim()))()")
    ui.run("find", "role", "menuitem", "click", "--name", "删除")
    time.sleep(1.3)
    out["dialog"] = ui.js(r'''(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');
      return d?{role:d.getAttribute('role'),text:d.innerText.replace(/\n+/g,' | ').slice(0,300)}:null})()''')
    ui.run("find", "role", "button", "click", "--name", "删除")
    time.sleep(1.8)
    out["toasts"] = toasts()
    return out


def main():
    tok = pweb.tok()
    name = PREFIX + "-script"
    # 1) create through the real UI
    ui.open_("/scripts/new", 2.5)
    create = {"form_fields": ui.js(r'''(() => ({
        labels: Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
        interpreter_combo: Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim()),
        content_control: (()=>{const t=document.querySelector('main textarea');return t?'textarea':null})()}))()'''),
        "set_name": set_label("名称", name),
        "set_desc": set_label("描述", "QA p-web owned script"),
        "set_timeout": set_label("超时", "30"),
        "set_cwd": set_label("工作目录", "/tmp"),
        "set_runuser": set_label("运行用户", "root"),
        "set_content": set_content("echo %s-ok" % PREFIX)}
    ui.shot("P07_P08-04-script-form-filled.png", full=True)
    save()
    create["toasts"] = toasts()
    create["url_after"] = ui.run("get", "url")[0].strip()
    create["write_requests"] = [l for l in ui.requests(150) if "/api/scripts" in l and ("POST" in l or "PUT" in l)]
    st, sc = pweb.j("GET", "/api/scripts")
    mine = [s for s in pweb.lst(sc) if s["name"] == name]
    create["created"] = mine[0] if mine else None
    ui.shot("P07_P08-04-script-list-after-create.png", full=True)
    RES["P07_P08-04_create"] = create
    if not mine:
        ui.write_ev("P07_P08-04-11-script-lifecycle.json", RES)
        print(json.dumps(RES, ensure_ascii=False, indent=1)[:3000])
        return
    s0 = mine[0]

    # 2) edit through the real UI -> revision bump
    ui.open_("/scripts/" + s0["id"], 2.5)
    edit = {"page": ui.js(r'''(()=>({url:location.pathname,h1:(document.querySelector('h1')||{}).innerText||null}))()'''),
            "set_desc": set_label("描述", "QA p-web owned script (edited)"),
            "set_content": set_content("echo %s-edited" % PREFIX)}
    save()
    edit["toasts"] = toasts()
    edit["write_requests"] = [l for l in ui.requests(200) if "/api/scripts" in l and ("POST" in l or "PUT" in l)]
    st, sc = pweb.j("GET", "/api/scripts")
    after = [s for s in pweb.lst(sc) if s["id"] == s0["id"]]
    edit["after"] = {k: after[0].get(k) for k in ("revision", "description", "content", "sha256", "timeout", "working_dir", "run_user", "enabled")} if after else None
    edit["revision_before"] = s0.get("revision")
    ui.shot("P07_P08-04-script-after-edit.png", full=True)
    RES["P07_P08-04_edit"] = edit
    s1 = after[0] if after else s0

    # 3) duplicate name (real UI)
    ui.open_("/scripts/new", 2.2)
    set_label("名称", name)
    set_label("超时", "10")
    set_content("echo dup")
    save()
    RES["P07_P08-11_duplicate_name"] = {"toasts": toasts(), "url": ui.run("get", "url")[0].strip(),
                                        "write_requests": [l for l in ui.requests(220) if "/api/scripts" in l and "POST" in l]}
    ui.shot("P07_P08-11-script-duplicate-name.png", full=True)

    # 4) over-long content (300 KiB) real UI
    ui.open_("/scripts/new", 2.2)
    set_label("名称", PREFIX + "-script-huge")
    huge = set_content("x" * (300 * 1024))
    save()
    RES["P07_P08-11_oversize_content"] = {"set": huge, "toasts": toasts(), "url": ui.run("get", "url")[0].strip(),
                                          "write_requests": [l for l in ui.requests(240) if "/api/scripts" in l and "POST" in l]}
    ui.shot("P07_P08-11-script-oversize.png", full=True)

    # 5) timeout boundary 0 (real UI)
    ui.open_("/scripts/new", 2.2)
    set_label("名称", PREFIX + "-script-timeout0")
    set_label("超时", "0")
    set_content("echo t0")
    save()
    RES["P07_P08-11_timeout_zero"] = {"toasts": toasts(), "url": ui.run("get", "url")[0].strip()}
    ui.shot("P07_P08-11-script-timeout-zero.png", full=True)

    # 6) timeout negative (API; the number input clamps in the UI)
    st, body = pweb.j("POST", "/api/scripts",
                      {"name": PREFIX + "-script-negtimeout", "interpreter": "shell",
                       "content": "echo neg", "timeout": -5, "enabled": True})
    RES["P07_P08-11_timeout_negative_api"] = {"http": st, "body": (body if isinstance(body, str) else json.dumps(body))[:250]}

    # 7) delete every owned script through the real UI confirm dialog
    cleanup = {}
    st, sc = pweb.j("GET", "/api/scripts")
    for s in pweb.lst(sc):
        if s["name"].startswith(PREFIX):
            ev = delete_via_ui(s["name"])
            ev["get_after"] = pweb._call("GET", "/api/scripts/" + s["id"])[0]
            cleanup[s["name"]] = ev
    RES["cleanup_delete"] = cleanup
    RES["owned_scripts_after"] = [s["name"] for s in pweb.mine("/api/scripts")]
    ui.write_ev("P07_P08-04-11-script-lifecycle.json", RES)
    print(json.dumps(RES, ensure_ascii=False, indent=1)[:5000])


main()
