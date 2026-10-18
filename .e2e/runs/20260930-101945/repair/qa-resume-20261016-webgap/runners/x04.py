#!/usr/bin/env python3
"""X04: Cron 五字段 <-> 原始表达式双向同步 + 非法表达式反馈 + 下次执行预览。

只读 draft 表单：不提交、不写入任何资源。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui  # noqa: E402

STEPS = []
SHOTS = []


def snap(label):
    st = ui.js(r'''
    (() => {
      const F = ['分','时','日','月','周'];
      const fi = F.map(l => document.querySelector('input[aria-label="' + l + '"]'));
      const raw = document.querySelector('input[aria-label="Cron 表达式"]');
      const prevTitle = [...document.querySelectorAll('*')].find(e => e.children.length===0 && (e.textContent||'').includes('接下来') || (e.textContent||'').includes('无法预览'));
      let preview = null;
      const cand = [...document.querySelectorAll('div,p,section')].find(e => (e.innerText||'').includes('接下来 5 次触发'));
      if (cand) preview = cand.innerText.trim().slice(0, 400);
      else {
        const p2 = [...document.querySelectorAll('p')].find(e => (e.innerText||'').includes('无法预览'));
        if (p2) preview = p2.innerText.trim();
      }
      const alerts = [...document.querySelectorAll('[role=alert]')].map(e => e.innerText.trim());
      return {
        fields: fi.map(i => i ? {v: i.value, dis: i.disabled, inv: i.getAttribute('aria-invalid')} : null),
        raw: raw ? {v: raw.value, inv: raw.getAttribute('aria-invalid')} : null,
        alerts,
        preview,
        tz: (document.querySelector('input[value="Asia/Shanghai"]')||{}).value || null,
      };
    })()
    ''')
    STEPS.append({"step": label, "state": st})
    print("---", label)
    print(json.dumps(st, ensure_ascii=False))
    return st


def set_raw(v):
    return ui.js(r'''(async () => {
      const el = document.querySelector('input[aria-label="Cron 表达式"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(el, %s);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(r => setTimeout(r, 250));
      return {set: %s, now: el.value};
    })()''' % (json.dumps(v), json.dumps(v)))


def set_field(label, v):
    return ui.js(r'''(async () => {
      const el = document.querySelector('input[aria-label="%s"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(el, %s);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      await new Promise(r => setTimeout(r, 250));
      return {field: "%s", now: el.value};
    })()''' % (label, json.dumps(v), label))


def main():
    ui.run("network", "requests", "--clear")
    ui.open_("/schedules/new", wait=2.0)
    s0 = snap("initial-empty")
    ui.shot("X04-01-initial-empty.png")

    set_raw("30 4 1-5 * 2")
    time.sleep(0.5)
    s1 = snap("raw->fields: '30 4 1-5 * 2'")
    ui.shot("X04-02-raw-valid.png")

    set_field("分", "7")
    time.sleep(0.4)
    s2 = snap("field minute=7 -> raw")
    set_field("时", "*/3")
    time.sleep(0.4)
    s3 = snap("field hour=*/3 -> raw")
    ui.shot("X04-03-fields-to-raw.png")

    set_raw("99 99 * * *")
    time.sleep(0.5)
    s4 = snap("raw invalid '99 99 * * *'")
    ui.shot("X04-04-invalid.png")

    set_field("月", "13")
    time.sleep(0.5)
    s5 = snap("field month=13 (field-level invalid)")
    ui.shot("X04-05-field-invalid.png")

    set_raw("@daily")
    time.sleep(0.5)
    s6 = snap("raw non-graphical '@daily'")
    ui.shot("X04-06-nongraphical.png")

    set_raw("0 12 * * 1-5")
    time.sleep(0.5)
    s7 = snap("raw valid again '0 12 * * 1-5'")
    ui.shot("X04-07-preview.png")

    set_raw("*/15 * * * *")
    time.sleep(0.6)
    s8 = snap("raw '*/15 * * * *' preview")
    ui.shot("X04-08-step-preview.png")

    # preset select: switch to 每天 12:00
    pres = ui.js(r'''(async () => {
      const trig = document.querySelector('button[aria-label="常用预设"]');
      if (!trig) return {ok:false, reason:'preset trigger missing'};
      trig.click();
      await new Promise(r => setTimeout(r, 400));
      const items = [...document.querySelectorAll('[role=option]')].map(e => e.innerText.trim());
      const noon = [...document.querySelectorAll('[role=option]')].find(e => e.innerText.trim() === '每天 12:00');
      if (!noon) return {ok:false, items};
      noon.click();
      await new Promise(r => setTimeout(r, 400));
      return {ok:true, items};
    })()''')
    STEPS.append({"step": "preset-select-open", "value": pres})
    print("--- preset", json.dumps(pres, ensure_ascii=False))
    time.sleep(0.5)
    s9 = snap("preset 'daily noon' applied")
    ui.shot("X04-09-preset.png")

    reqs = ui.requests(200)
    posts = [r for r in reqs if "POST" in r.upper() and "/api/schedules" in r]
    out = {
        "case_id": "X04",
        "url": "/schedules/new",
        "writes_performed": False,
        "post_schedules_requests": posts,
        "tz_input_default": (s0.get("tz") if isinstance(s0, dict) else None),
        "steps": STEPS,
        "session": ui.SESSION,
        "origin": ui.ORIGIN,
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    p = Path(ui.BATCH) / "evidence" / "X04-cron-sync.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print("WROTE", p)
    print("POST/schedules:", posts)


main()
