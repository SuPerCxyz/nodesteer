#!/usr/bin/env python3
"""agent-browser helpers for batch09-settings (repair UI at http://192.168.100.209:18080).

Credentials are injected only into the piped JS for the sign-in form; they never
appear in argv, stdout or evidence.
"""
import json
import re
import subprocess
import time

SESSION = 'qab09set1015'
CLI = ['agent-browser', '--session', SESSION]
BASE = 'http://192.168.100.209:18080'
CRED = '/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json'
REF_RE = re.compile(r'ref=(e\d+)')


def run(args, timeout=120, stdin=None):
    cp = subprocess.run(CLI + args, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def snap():
    _, out, _ = run(['snapshot', '-i'])
    return out


def ref_of(snapshot, needle, occurrence=0):
    cands = [l for l in snapshot.splitlines() if needle in l]
    if len(cands) <= occurrence:
        return None, cands
    m = REF_RE.search(cands[occurrence])
    return (m.group(1) if m else None), cands


def click_ref(ref):
    return run(['click', '@' + ref])[0]


def eval_js(js, timeout=120):
    rc, out, err = run(['eval', '--stdin'], timeout=timeout, stdin=js)
    txt = (out or '').strip()
    try:
        v = json.loads(txt)
        return v if not isinstance(v, str) else json.loads(v)
    except Exception:
        return {'__eval_raw': txt[:800], '__err': (err or '')[:300]}


def open_url(url):
    return run(['open', url], timeout=120)


def screenshot(path):
    return run(['screenshot', path], timeout=120)


def close():
    return run(['close'], timeout=60)


LOGIN_JS = """
(() => {
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const un = document.querySelector('input[name=username]');
  const pw = document.querySelector('input[name=password]');
  if (!un || !pw) return { ok: false, reason: 'inputs not found' };
  setVal(un, %s); setVal(pw, %s);
  return { ok: true, username_len: un.value.length, password_len: pw.value.length,
           password_masked: pw.type === 'password' };
})()
"""


def login():
    c = json.load(open(CRED))
    open_url(BASE + '/sign-in')
    time.sleep(1.5)
    filled = eval_js(LOGIN_JS % (json.dumps(c['admin_username']), json.dumps(c['admin_password'])))
    sn = snap()
    btn, _ = ref_of(sn, 'button "登录"')
    if not btn:
        btn, _ = ref_of(sn, 'button')
    if btn:
        click_ref(btn)
    time.sleep(3)
    return {'fill': filled, 'button_ref': btn, 'url_after': eval_js('location.pathname')}


READ_SETTINGS_JS = """
(() => {
  const out = { path: location.pathname + location.search, toasts: [], fields: {}, labels: [] };
  out.toasts = [...document.querySelectorAll('[data-sonner-toast], [role=status]')]
    .map((e) => e.innerText.trim().replace(/\\s+/g, ' ')).filter(Boolean).slice(0, 8);
  const labels = [...document.querySelectorAll('label')].filter((l) => l.querySelector('input'));
  for (const l of labels) {
    const inp = l.querySelector('input');
    const key = inp.getAttribute('aria-label') || l.innerText.trim().split('\\n')[0].trim();
    out.fields[key] = inp.value;
    out.labels.push({ key, value: inp.value, disabled: inp.disabled,
                      tag: inp.tagName, type: inp.type });
  }
  const btns = [...document.querySelectorAll('button')].map((b) => b.innerText.trim());
  out.buttons = btns;
  out.save_present = btns.some((b) => b === '保存');
  return out;
})()
"""


def read_settings():
    return eval_js(READ_SETTINGS_JS)


SET_FIELD_JS = """
(() => {
  const key = %s;
  const value = %s;
  const labels = [...document.querySelectorAll('label')].filter((l) => l.querySelector('input'));
  const target = labels.find((l) => {
    const inp = l.querySelector('input');
    return inp.getAttribute('aria-label') === key || l.innerText.trim().split('\\n')[0].trim() === key;
  });
  if (!target) return { ok: false, reason: 'field not found', key };
  const el = target.querySelector('input');
  if (el.disabled) return { ok: false, reason: 'field disabled', key };
  const proto = HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
  return { ok: true, key, now: el.value };
})()
"""


def set_field(key, value):
    return eval_js(SET_FIELD_JS % (json.dumps(key), json.dumps(str(value))))


SET_FIELDS_JS = """
(() => {
  const pairs = %s;
  const labels = [...document.querySelectorAll('label')].filter((l) => l.querySelector('input'));
  const done = {};
  for (const [key, value] of pairs) {
    const target = labels.find((l) => {
      const inp = l.querySelector('input');
      return inp.getAttribute('aria-label') === key || l.innerText.trim().split('\\n')[0].trim() === key;
    });
    if (!target) { done[key] = null; continue; }
    const el = target.querySelector('input');
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(value));
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    done[key] = el.value;
  }
  return { ok: true, after_set: done };
})()
"""


def set_fields(pairs):
    return eval_js(SET_FIELDS_JS % json.dumps(pairs))


CLICK_SAVE_JS = """
(() => {
  const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim() === '保存');
  if (!b) return { ok: false, reason: 'save button not found' };
  const disabled = b.disabled;
  b.click();
  return { ok: true, was_disabled: disabled };
})()
"""


def click_save():
    return eval_js(CLICK_SAVE_JS)


CLICK_SAVE_N_JS = """
(() => {
  const b = [...document.querySelectorAll('button')].find((x) => x.innerText.trim() === '保存');
  if (!b) return { ok: false, reason: 'save button not found' };
  const n = %d;
  for (let i = 0; i < n; i++) b.click();
  return { ok: true, clicks: n };
})()
"""


def click_save_n(n):
    return eval_js(CLICK_SAVE_N_JS % n)


RELOAD_JS = """
(() => { location.reload(); return { reloading: true, at: location.pathname }; })()
"""


def reload_page(wait=4.0):
    out = eval_js(RELOAD_JS)
    time.sleep(wait)
    return out


def toasts():
    return eval_js("(() => [...document.querySelectorAll('[data-sonner-toast]')]"
                   ".map((e) => e.innerText.trim().replace(/\\s+/g, ' ')).filter(Boolean))()")
