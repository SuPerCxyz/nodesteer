#!/usr/bin/env python3
"""Emit JS (piped into `agent-browser eval --stdin`) that fills the NodeSteer
sign-in form with local-setter + input events so React state updates.

Credentials come from the 0600 file and appear only inside the piped JS.
They are never written to argv, stdout, evidence, or the reply. The returned
JS value contains booleans/lengths only.
"""
import json

CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
c = json.load(open(CRED))
u = json.dumps(c["admin_username"])
p = json.dumps(c["admin_password"])

print(f"""
(() => {{
  const setVal = (el, v) => {{
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(el, v);
    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }};
  const un = document.querySelector('input[name=username]');
  const pw = document.querySelector('input[name=password]');
  if (!un || !pw) return {{ ok: false, reason: 'inputs not found' }};
  setVal(un, {u});
  setVal(pw, {p});
  return {{ ok: true, username_filled: un.value.length > 0, password_filled: pw.value.length > 0,
            password_masked: pw.type === 'password' }};
}})()
""")
