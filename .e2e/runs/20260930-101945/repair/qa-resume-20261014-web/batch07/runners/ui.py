#!/usr/bin/env python3
"""agent-browser helpers for batch07 (repair UI at http://192.168.100.209:18080).

Credentials are injected only into the piped JS for the sign-in form; they never
appear in argv, stdout or evidence.
"""
import json
import re
import subprocess
import time

SESSION = 'qaweb1410b07'
CLI = ['agent-browser', '--session', SESSION]
BASE = 'http://192.168.100.209:18080'
CRED = '/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json'
REF_RE = re.compile(r'ref=(e\d+)')


def run(args, timeout=90, stdin=None):
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
    return run(['click', '@' + ref])[0], ''


def eval_js(js, timeout=90):
    rc, out, err = run(['eval', '--stdin'], timeout=timeout, stdin=js)
    txt = (out or '').strip()
    try:
        v = json.loads(txt)
        return v if not isinstance(v, str) else json.loads(v)
    except Exception:
        return {'__eval_raw': txt[:500], '__err': (err or '')[:200]}


def open_url(url):
    return run(['open', url], timeout=90)


def screenshot(path):
    return run(['screenshot', path], timeout=90)


LOGIN_JS = None


def login():
    global LOGIN_JS
    c = json.load(open(CRED))
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
  return { ok: true, username_len: un.value.length, password_len: pw.value.length, password_masked: pw.type === 'password' };
})()
""" % (json.dumps(c['admin_username']), json.dumps(c['admin_password']))
    open_url(BASE + '/sign-in')
    time.sleep(1.5)
    filled = eval_js(LOGIN_JS)
    sn = snap()
    btn, cands = ref_of(sn, 'button "登录"')
    if not btn:
        btn, cands = ref_of(sn, 'button')
    if btn:
        click_ref(btn)
    time.sleep(3)
    return {'fill': filled, 'button_ref': btn, 'url_after': eval_js('location.pathname')}


TABLE_JS = """
(() => {
  const out = { path: location.pathname + location.search, toasts: [], rows: [] };
  out.toasts = [...document.querySelectorAll('[data-sonner-toast], [role=status], li[data-sonner-toast]')]
    .map((e) => e.innerText.trim().replace(/\\s+/g, ' ')).slice(0, 6);
  const table = document.querySelector('table');
  if (table) {
    out.headers = [...table.querySelectorAll('thead th')].map((th) => th.innerText.trim());
    out.rows = [...table.querySelectorAll('tbody tr')].map((tr) =>
      [...tr.cells].map((c) => c.innerText.trim().replace(/\\s+/g, ' ')));
  }
  out.dialogs = [...document.querySelectorAll('[role=dialog]')].map((d) => d.innerText.trim().replace(/\\s+/g, ' ').slice(0, 300));
  return out;
})()
"""


def scan():
    return eval_js(TABLE_JS)
