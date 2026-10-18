#!/usr/bin/env python3
"""Real-UI fixture flows: script create/edit/duplicate/delete-confirm (G04-02, P07_P08-04/11)."""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api  # noqa: E402
import ui  # noqa: E402

PREFIX = "qa-pweb-" + (Path(__file__).resolve().parent.parent / ".nonce").read_text().strip()
RES = {}


def snap():
    return ui.run("snapshot", "-i")[0]


def ref_of(text, role="button"):
    s = snap()
    for l in s.splitlines():
        if '%s "%s"' % (role, text) in l and "ref=" in l:
            return re.search(r"ref=(e\d+)", l).group(1)


def set_field(label_text, value):
    """Set the input/textarea inside the <label> whose text starts with label_text."""
    js = r'''(() => {
      const lb = Array.from(document.querySelectorAll('main label')).find(l => l.innerText.trim().replace(/\n/g,' ').startsWith(%s));
      if (!lb) return {ok:false, reason:'label not found'};
      const el = lb.querySelector('input,textarea') || (lb.parentElement && lb.parentElement.querySelector('input,textarea'));
      if (!el) return {ok:false, reason:'control not found'};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, %s);
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      return {ok:true, tag:el.tagName, len:el.value.length};
    })()''' % (json.dumps(label_text), json.dumps(value))
    return ui.js(js)


def set_placeholder(ph, value):
    js = r'''(() => {
      const el = Array.from(document.querySelectorAll('main input,main textarea')).find(i => (i.placeholder||'') === %s);
      if (!el) return {ok:false};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const s = Object.getOwnPropertyDescriptor(proto, 'value').set;
      s.call(el, %s);
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      return {ok:true, len:el.value.length};
    })()''' % (json.dumps(ph), json.dumps(value))
    return ui.js(js)


def toasts():
    return ui.js(r'''(()=>{const t=Array.from(document.querySelectorAll('[data-sonner-toast],[data-sonner-toaster] li'));
      return {n:t.length,texts:t.map(x=>x.innerText.trim())}})()''')


def create_script():
    print("[P07_P08-04] script create via real UI")
    out = {}
    ui.open_("/scripts/new", 2.5)
    out["form_fields"] = ui.js(r'''(() => ({
      labels: Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()),
      content_control: (()=>{const t=document.querySelector('main textarea');return t?{tag:'textarea', ph:t.placeholder}:{tag:null}})(),
      interpreter_combo: Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim())
    }))()''')
    out["set_name"] = set_field("名称", PREFIX + "-script")
    out["set_desc"] = set_field("描述", "QA p-web batch owned script")
    out["set_timeout"] = set_field("超时", "30")
    out["set_cwd"] = set_field("工作目录", "/tmp")
    out["set_runuser"] = set_field("运行用户", "root")
    out["set_content"] = ui.js(r'''(() => {
      const el = document.querySelector('main textarea') || Array.from(document.querySelectorAll('main input')).find(i=>i.placeholder==='内容');
      if (!el) return {ok:false, reason:'no content control'};
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const s = Object.getOwnPropertyDescriptor(proto, 'value').set;
      s.call(el, "echo %s-ok");
      el.dispatchEvent(new Event('input', {bubbles:true}));
      el.dispatchEvent(new Event('change', {bubbles:true}));
      return {ok:true, tag:el.tagName, len:el.value.length};
    })()''' % PREFIX)
    ui.shot("P07_P08-04-script-form-filled.png", full=True)
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.0)
    out["toasts"] = toasts()
    out["url_after_save"] = ui.run("get", "url")[0].strip()
    out["writes"] = [l for l in ui.requests(120) if "/api/scripts" in l]
    tok = api.login()
    st, sc = api.j("GET", "/api/scripts", tok)
    my = [s for s in api.as_list(sc) if s["name"] == PREFIX + "-script"]
    out["api_script"] = my[0] if my else None
    out["created_via_ui"] = bool(my)
    ui.shot("P07_P08-04-script-list-after-create.png", full=True)
    if my:
        s = my[0]
        RES["script"] = {"id": s["id"], "name": s["name"], "revision": s.get("revision"),
                         "interpreter": s.get("interpreter"), "timeout": s.get("timeout"),
                         "working_dir": s.get("working_dir"), "run_user": s.get("run_user"),
                         "enabled": s.get("enabled"), "sha256": s.get("sha256"),
                         "content": s.get("content")}
    RES["P07_P08-04"] = out
    return my[0] if my else None


def duplicate_and_limits(script):
    print("[P07_P08-11] duplicate name / over-long content / timeout boundary")
    out = {}
    # duplicate name
    ui.open_("/scripts/new", 2.2)
    set_field("名称", script["name"])
    set_field("超时", "10")
    ui.js(r'''(() => {const el=document.querySelector('main textarea');const p=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      if(el){p.call(el,'echo dup');el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}return true})()''')
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.0)
    out["duplicate_name"] = {"toasts": toasts(), "url": ui.run("get", "url")[0].strip()}
    ui.shot("P07_P08-11-script-duplicate-name.png", full=True)
    # over-long content (256 KiB+1)
    ui.open_("/scripts/new", 2.2)
    set_field("名称", PREFIX + "-script-huge")
    big = "x" * (300 * 1024)
    r = ui.js("(() => {const el=document.querySelector('main textarea');if(!el)return{ok:false};const p=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;p.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return{ok:true,len:el.value.length}})()" % json.dumps(big))
    out["oversize_set"] = r
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.5)
    out["oversize_content"] = {"toasts": toasts(), "url": ui.run("get", "url")[0].strip()}
    ui.shot("P07_P08-11-script-oversize.png", full=True)
    # timeout boundary: 0
    ui.open_("/scripts/new", 2.2)
    set_field("名称", PREFIX + "-script-timeout0")
    set_field("超时", "0")
    ui.js(r'''(() => {const el=document.querySelector('main textarea');const p=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      if(el){p.call(el,'echo t0');el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}return true})()''')
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.0)
    out["timeout_zero"] = {"toasts": toasts(), "url": ui.run("get", "url")[0].strip()}
    ui.shot("P07_P08-11-script-timeout-zero.png", full=True)
    # timeout negative via API (UI number input may clamp)
    st, body = api.j("POST", "/api/scripts", api.login(),
                     {"name": PREFIX + "-script-negtimeout", "interpreter": "shell",
                      "content": "echo neg", "timeout": -5, "enabled": True})
    out["timeout_negative_api"] = {"http": st, "body": body if isinstance(body, str) else json.dumps(body)[:200]}
    tok = api.login()
    st, sc = api.j("GET", "/api/scripts", tok)
    out["owned_scripts_after"] = [s["name"] for s in api.as_list(sc) if s["name"].startswith(PREFIX)]
    RES["P07_P08-11"] = out


def edit_script(script):
    print("[P07_P08-04] script edit")
    out = {}
    ui.open_("/scripts/" + script["id"], 2.5)
    out["edit_page"] = ui.js(r'''(()=>({url:location.pathname,h1:(document.querySelector('h1')||{}).innerText||null,
      text:(document.querySelector('main')||document.body).innerText.slice(0,400)}))()''')
    out["before_revision"] = script.get("revision")
    r = set_field("描述", "QA p-web batch edited description")
    out["set_desc"] = r
    ui.js(r'''(() => {const el=document.querySelector('main textarea');if(!el)return{ok:false};const p=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      p.call(el,'echo %s-edited');el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return{ok:true}})()''' % PREFIX)
    before = ui.requests(60)
    ui.run("click", "@" + ref_of("保存"))
    time.sleep(2.0)
    out["toasts"] = toasts()
    out["writes"] = [l for l in ui.requests(150) if "/api/scripts" in l and ("PUT" in l or "POST" in l)]
    tok = api.login()
    st, sc = api.j("GET", "/api/scripts", tok)
    my = [s for s in api.as_list(sc) if s["id"] == script["id"]]
    out["after"] = {"revision": my[0].get("revision"), "description": my[0].get("description"),
                    "content": my[0].get("content"), "sha256": my[0].get("sha256")} if my else None
    ui.shot("P07_P08-04-script-after-edit.png", full=True)
    RES["P07_P08-04_edit"] = out


def delete_script(script):
    print("[G04-02] script delete confirm cancel/confirm")
    out = {}
    ui.open_("/scripts", 2.5)
    # open row actions menu for our script
    row = ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));const r=rows.find(x=>x.innerText.includes(%s));if(!r)return null;const b=r.querySelector('button[aria-label],button');return b?b.innerText.trim():null})()" % json.dumps(script["name"]))
    out["row_found"] = row is not None
    idx = ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));return rows.findIndex(x=>x.innerText.includes(%s))})()" % json.dumps(script["name"]))
    out["row_index"] = idx
    # click the row action button (last button in the row)
    ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));const r=rows[%d];const bs=Array.from(r.querySelectorAll('button'));const b=bs[bs.length-1];b.click();return true})()" % idx)
    time.sleep(1.0)
    out["menu_items"] = ui.js("(()=>Array.from(document.querySelectorAll('[role=menuitem]')).map(m=>m.innerText.trim()))()")
    ui.shot("G04-02-script-row-actions.png")
    # click 删除
    ui.js("(() => {const m=Array.from(document.querySelectorAll('[role=menuitem]')).find(x=>x.innerText.trim()==='删除');if(m){m.click();return true}return false})()")
    time.sleep(1.2)
    out["confirm_dialog"] = ui.js(r'''(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');
      return d?{role:d.getAttribute('role'),text:d.innerText.slice(0,400),
        buttons:Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean)}:{role:null}})()''')
    ui.shot("G04-02-script-delete-confirm.png")
    # count deletes before cancel
    ui.run("network", "requests")
    # cancel
    ui.js("(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');const b=Array.from(d.querySelectorAll('button')).find(x=>x.innerText.trim()==='取消');if(b){b.click();return true}return false})()")
    time.sleep(1.2)
    out["after_cancel"] = {"dialog": ui.js("(()=>!!document.querySelector('[role=alertdialog],[role=dialog]'))()")}
    tok = api.login()
    st, _ = api.call("GET", "/api/scripts/" + script["id"], tok)
    out["after_cancel_get"] = st
    out["cancel_writes"] = [l for l in ui.requests(200) if "DELETE" in l and "/api/scripts" in l]
    ui.shot("G04-02-script-after-cancel.png", full=True)
    # confirm delete
    ui.js("(() => {const rows=Array.from(document.querySelectorAll('table tbody tr'));const ix=rows.findIndex(x=>x.innerText.includes(%s));if(ix<0)return false;const bs=Array.from(rows[ix].querySelectorAll('button'));bs[bs.length-1].click();return true})()" % json.dumps(script["name"]))
    time.sleep(1.0)
    ui.js("(() => {const m=Array.from(document.querySelectorAll('[role=menuitem]')).find(x=>x.innerText.trim()==='删除');if(m){m.click();return true}return false})()")
    time.sleep(1.2)
    ui.js("(() => {const d=document.querySelector('[role=alertdialog],[role=dialog]');const b=Array.from(d.querySelectorAll('button')).find(x=>/删除/.test(x.innerText.trim())&&!/取消/.test(x.innerText));if(b){b.click();return true}return false})()")
    time.sleep(2.0)
    out["confirm_toasts"] = toasts()
    st, _ = api.call("GET", "/api/scripts/" + script["id"], tok)
    out["after_confirm_get"] = st
    out["confirm_writes"] = [l for l in ui.requests(250) if "DELETE" in l and "/api/scripts" in l]
    ui.shot("G04-02-script-after-delete.png", full=True)
    RES["G04-02_script"] = out


def main():
    script = create_script()
    if script:
        edit_script(script)
        duplicate_and_limits(script)
        delete_script(script)
    ui.write_ev("P07_P08-G04-02-script.json", RES)
    print(json.dumps({k: RES[k] for k in RES}, ensure_ascii=False, indent=1)[:4000])


main()
