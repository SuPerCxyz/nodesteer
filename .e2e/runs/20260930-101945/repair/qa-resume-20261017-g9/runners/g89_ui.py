"""G8/G9：agent-browser 驱动（仅自有 session：qa-g89-<nonce>-<who>）。

凭据通过 stdin 注入的 JS 填充，不落 argv / 不落 evidence / 不出现于 stdout。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import g89_common as C

ORIGIN = None


def init() -> str:
    global ORIGIN
    cr = C.load_creds()
    ORIGIN = f"http://{C.HOST}:{cr['host_port']}"
    return ORIGIN


def session(who: str) -> str:
    return f"qa-g89-{C.get_nonce()}-{who}"


def run(who: str, *args, stdin=None, timeout=120):
    cmd = ["agent-browser", "--session", session(who)] + list(args)
    cp = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def value(out: str):
    i = out.find("{")
    j = out.find("[")
    if i < 0 or (0 <= j < i):
        i = j
    if i < 0:
        return out.strip()
    try:
        return json.loads(out[i:])
    except Exception:  # noqa: BLE001
        return out[i:]


def js(who: str, code: str):
    out, err, rc = run(who, "eval", "--stdin", stdin=code)
    if rc != 0:
        raise RuntimeError("eval failed: %s%s" % (err[-300:], out[-300:]))
    return value(out)


def open_(who: str, path: str, wait: float = 1.8) -> str:
    run(who, "open", ORIGIN + path)
    time.sleep(wait)
    return url(who)


def url(who: str) -> str:
    return run(who, "get", "url")[0].strip()


def login(who: str, username: str, password: str) -> dict:
    open_(who, "/sign-in", wait=2.4)
    code = r"""
(() => {
  const u = %s, p = %s;
  const setVal = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  };
  const un = document.querySelector('input[name=username], input#username');
  const pw = document.querySelector('input[name=password], input#password');
  if (!un || !pw) return {ok:false, reason:'inputs-not-found'};
  setVal(un, u); setVal(pw, p);
  const btn = [...document.querySelectorAll('button')].find(b => /登录|Sign in|Log in|登陆/.test(b.innerText||''));
  if (!btn) return {ok:false, reason:'submit-not-found'};
  btn.click();
  return {ok:true, password_masked: pw.type === 'password'};
})()
""" % (json.dumps(username), json.dumps(password))
    r = js(who, code)
    for _ in range(40):
        time.sleep(0.4)
        if js(who, "localStorage.getItem('nodesteer_token') ? 'T' : 'N'") == "T":
            break
    return {"submit": r, "token_in_localStorage": bool(js(who, "!!localStorage.getItem('nodesteer_token')")),
            "url": url(who)}


def body_text(who: str) -> str:
    return js(who, "document.body.innerText") or ""


def shot(who: str, batch: str, name: str, full: bool = True) -> str:
    d = C.EVID_DIRS[batch]
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    args = ["screenshot", str(p)]
    if full:
        args.append("--full")
    run(who, *args)
    return f"evidence/{name}" if p.exists() else ""


def install_request_recorder(who: str) -> dict:
    return js(who, r"""
(() => {
  if (window.__g89rec) return {already:true, n: window.__g89rec.length};
  window.__g89rec = [];
  const push = (e) => { try { window.__g89rec.push(e); } catch (x) {} };
  const of = window.fetch;
  window.fetch = async function(input, init) {
    const method = (init && init.method) || (input && input.method) || 'GET';
    const u = (typeof input === 'string') ? input : (input && input.url) || '';
    let body = null;
    try { body = init && init.body ? String(init.body).slice(0, 4000) : null; } catch (e) {}
    const t0 = performance.now();
    try {
      const r = await of.apply(this, arguments);
      push({kind:'fetch', method, url:u, status:r.status, body, ms: Math.round(performance.now()-t0)});
      return r;
    } catch (e) {
      push({kind:'fetch', method, url:u, status:0, body, err:String(e), ms: Math.round(performance.now()-t0)});
      throw e;
    }
  };
  const ox = XMLHttpRequest.prototype.open;
  const os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) { this.__g89 = {method:m, url:u}; return ox.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b) {
    const t0 = performance.now();
    this.addEventListener('loadend', () => push({kind:'xhr', method:(this.__g89||{}).method, url:(this.__g89||{}).url,
      status:this.status, body:b ? String(b).slice(0,4000) : null, ms: Math.round(performance.now()-t0)}));
    return os.apply(this, arguments);
  };
  return {installed:true};
})()
""")


def rec(who: str) -> list:
    return js(who, "window.__g89rec || null") or []


def clear_rec(who: str) -> None:
    js(who, "(() => { if (window.__g89rec) window.__g89rec.length = 0; return true; })()")


def set_input_value(who: str, selector: str, value: str) -> dict:
    return js(who, r"""
(() => {
  const el = document.querySelector(%s);
  if (!el) return {found:false};
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, %s);
  el.dispatchEvent(new Event('input', {bubbles:true}));
  el.dispatchEvent(new Event('change', {bubbles:true}));
  return {found:true, input_type: el.type, tag: el.tagName};
})()
""" % (json.dumps(selector), json.dumps(value)))


def reload(who: str, wait: float = 2.0) -> str:
    run(who, "reload")
    time.sleep(wait)
    return url(who)


def close_session(who: str) -> None:
    run(who, "close", timeout=40)


def cookies_and_storage(who: str) -> dict:
    return js(who, r"""
(() => ({
  cookies: document.cookie ? document.cookie.split('; ').map(c => c.split('=')[0]) : [],
  cookie_raw_present: !!document.cookie,
  ls_keys: Object.keys(localStorage),
  ls_token_len: (localStorage.getItem('nodesteer_token') || '').length,
  ls_token_sha_prefix: null,
  session_keys: Object.keys(sessionStorage)
}))()
""")
