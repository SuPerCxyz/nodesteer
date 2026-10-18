#!/usr/bin/env python3
"""Task form probes (read-only, no save): P06-05 / P09P11-08/09/10/11/12/13/15/16/17/22."""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

RES = {}


def snap():
    return ui.run("snapshot", "-i")[0]


def combo_options(label, close=True):
    s = snap()
    m = re.search(r'combobox "%s" \[expanded=false, ref=(e\d+)\]' % re.escape(label), s)
    if not m:
        return None
    ui.run("click", "@" + m.group(1))
    time.sleep(0.9)
    opts = [l.strip() for l in snap().splitlines() if "- option" in l]
    if close:
        ui.run("press", "Escape")
        time.sleep(0.4)
    return opts


def select_combo(label, option_text):
    s = snap()
    m = re.search(r'combobox "%s" \[expanded=false, ref=(e\d+)\]' % re.escape(label), s)
    if not m:
        return {"ok": False, "reason": "combo not found"}
    ui.run("click", "@" + m.group(1))
    time.sleep(0.8)
    s2 = snap()
    target = [l for l in s2.splitlines() if "- option" in l and option_text in l]
    if not target:
        ui.run("press", "Escape")
        return {"ok": False, "reason": "option not found", "options": [l.strip() for l in s2.splitlines() if "- option" in l]}
    ui.run("click", "@" + re.search(r"ref=(e\d+)", target[0]).group(1))
    time.sleep(1.0)
    return {"ok": True}


def form_state():
    return ui.js(r'''(() => {
      const main = document.querySelector('main') || document.body;
      return {
        has_command_field: !!Array.from(document.querySelectorAll('main label')).find(l=>l.innerText.trim().startsWith('命令')),
        has_script_picker: !!Array.from(document.querySelectorAll('main label')).find(l=>l.innerText.includes('脚本')),
        has_app_picker: !!Array.from(document.querySelectorAll('main label')).find(l=>/应用/.test(l.innerText.split('\n')[0])),
        labels: Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(0,25),
        combos: Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim().slice(0,40)),
        error_texts: (main.innerText.match(/(必填|不能为空|请输入|请选择|required|无效|invalid)[^\n]{0,30}/g)||[]).slice(0,8),
        inputs: Array.from(main.querySelectorAll('input,textarea')).map(e=>({ph:e.placeholder,type:e.type,val:(e.value||'').slice(0,40)}))
      };
    })()''')


def save_and_capture(tag):
    ui.run("click", "button:has-text('保存')") if False else None
    out, err, rc = ui.run("find", "button", "click", "--name", "保存")
    time.sleep(1.4)
    st = ui.js(r'''(() => {
      const main = document.querySelector('main') || document.body;
      const inv = Array.from(document.querySelectorAll('[aria-invalid=true]')).length;
      const btns = Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(Boolean);
      return {aria_invalid: inv, url: location.pathname,
        messages: (main.innerText.match(/(必填|不能为空|请输入|请选择|至少|不能|无效|失败|错误)[^\n]{0,40}/g)||[]).slice(0,10),
        toast: (document.body.innerText.match(/(保存失败|创建失败|请[^\n]{0,20})/g)||[]).slice(0,5)};
    })()''')
    ui.shot("P09P11-22-%s.png" % tag, full=True)
    return st


def main():
    ui.open_("/tasks/new", 2.5)
    RES["P09P11-13_controls"] = ui.js(r'''(() => {
      const labels = Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim());
      return {labels, has_timeout: labels.some(l=>l.startsWith('超时')), has_retry: labels.some(l=>l.startsWith('重试')),
        has_runuser: labels.some(l=>l.includes('运行用户')), has_enabled: labels.some(l=>l.includes('已启用')),
        offline_policy_combo: Array.from(document.querySelectorAll('main [role=combobox]')).map(c=>c.innerText.trim())};
    })()''')
    RES["P09P11-15_local_condition"] = {
        "metric_options": combo_options("cpu_usage") or combo_options("cpu_usage"),
    }
    # metric select button text is the current metric; open via text
    RES["P09P11-15_operator_options"] = None
    # type switching
    switches = []
    for name, label in [("脚本", "script"), ("托管应用部署", "app_deploy"),
                        ("托管应用操作", "app_operation"), ("命令", "command")]:
        r = select_combo("类型", name)
        st = form_state()
        switches.append({"selected_type": name, "select_result": r,
                         "has_command_field": st["has_command_field"],
                         "has_script_picker": st["has_script_picker"],
                         "has_app_picker": st["has_app_picker"],
                         "combos": st["combos"], "error_texts": st["error_texts"]})
        ui.shot("P06-05-type-%s.png" % label, full=True)
    RES["P06-05_type_switch"] = switches
    # target type switching
    tsw = []
    for name in ["分组", "标签", "节点"]:
        r = select_combo("目标类型", name)
        st = ui.js(r'''(() => {
          const main=document.querySelector('main')||document.body;
          return {labels:Array.from(document.querySelectorAll('main label')).map(l=>l.innerText.replace(/\n/g,' ').trim()).slice(0,25),
                  placeholders:Array.from(main.querySelectorAll('input')).map(i=>i.placeholder),
                  text:main.innerText.slice(0,400)};
        })()''')
        tsw.append({"target_type": name, "select_result": r, "placeholders": st["placeholders"],
                    "labels": [l for l in st["labels"] if l], "text_head": st["text"][:200]})
        ui.shot("P09P11-09-12-target-%s.png" % {"分组": "group", "标签": "label", "节点": "node"}[name], full=True)
    RES["P09P11-09_10_11_12_target_switch"] = tsw
    # empty save validation
    ui.open_("/tasks/new", 2.0)
    RES["P09P11-22_empty_save"] = save_and_capture("empty")
    # name only (空命令)
    ui.js(r'''(() => {const el=Array.from(document.querySelectorAll('main input')).find(i=>!(i.placeholder||'').length && i.type==='text');
      const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;s.call(el,'qa-pweb-d6dbf7af-noCommand');el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));return true})()''')
    time.sleep(0.5)
    RES["P09P11-22_name_only"] = save_and_capture("name-only-no-command-no-target")
    # script type without script
    select_combo("类型", "脚本")
    RES["P09P11-22_script_without_script"] = save_and_capture("script-no-script")
    # app_deploy without app
    select_combo("类型", "托管应用部署")
    RES["P09P11-22_appdeploy_without_app"] = save_and_capture("appdeploy-no-app")
    ui.write_ev("P06-05-P09P11-task-form.json", RES)
    print(json.dumps({k: RES[k] for k in RES}, ensure_ascii=False, indent=1)[:5000])


main()
