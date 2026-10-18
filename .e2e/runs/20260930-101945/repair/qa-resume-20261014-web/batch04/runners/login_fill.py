#!/usr/bin/env python3
"""Emit JS (to stdout, piped into `agent-browser eval --stdin`) that fills the
NodeSteer sign-in form with local-setter + input events so React state updates.

Credentials are read from the 0600 file and embedded only in the piped JS.
They never enter argv, stdout, evidence, or the reply.
The returned JS value contains booleans/lengths only, never the secret values.
"""
import json

CRED = "/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json"
c = json.load(open(CRED))
u = json.dumps(c["admin_username"])
p = json.dumps(c["admin_password"])

js = f"""
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
  return {{
    ok: true,
    username_filled: un.value.length > 0,
    username_len: un.value.length,
    password_filled: pw.value.length > 0,
    password_len: pw.value.length,
    password_masked: pw.type === 'password'
  }};
}})()
"""
print(js)
