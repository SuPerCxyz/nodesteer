"""G1：agent-browser 驱动（仅自有 session：qa-g1-<nonce>-<who>）。

凭据通过 stdin 注入的 JS 填充（local setter + input/change 事件），
不落 argv / 不落 evidence / 不出现在 stdout。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import g1_common as C

ORIGIN = None  # 由 init() 设置


def init(nonce: str) -> str:
    global ORIGIN
    ORIGIN = f"http://{C.HOST}:{C.load_creds(nonce)['host_port']}"
    return ORIGIN


def session(who: str) -> str:
    return f"qa-g1-{C.get_nonce()}-{who}"


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


def open_(who: str, path: str, wait: float = 1.6) -> str:
    urls = []
    run(who, "open", ORIGIN + path)
    time.sleep(wait)
    return url(who)


def url(who: str) -> str:
    return run(who, "get", "url")[0].strip()


def login(who: str, username: str, password: str) -> dict:
    """打开 /sign-in 并提交表单；凭据只存在于 stdin JS 内。"""
    open_(who, "/sign-in", wait=2.2)
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
        tok = js(who, "localStorage.getItem('nodesteer_token') ? 'T' : 'N'")
        if tok == "T":
            break
    return {"submit": r, "token_in_localStorage": bool(js(who, "!!localStorage.getItem('nodesteer_token')")),
            "url": url(who)}


def text_of(who: str, selector: str) -> str:
    return js(who, "(() => { const e = document.querySelector(%s); return e ? (e.innerText||'') : null; })()"
              % json.dumps(selector)) or ""


def body_text(who: str) -> str:
    return js(who, "document.body.innerText") or ""


def buttons(who: str) -> list:
    return js(who, """[...document.querySelectorAll('button,a')].map(e => (e.innerText||'').trim())
      .filter(x => x.length > 0 && x.length < 40)""") or []


def has_text(who: str, needle: str) -> bool:
    return needle in body_text(who)


def shot(who: str, name: str, full: bool = True) -> str:
    C.EVID.mkdir(parents=True, exist_ok=True)
    p = C.EVID / name
    args = ["screenshot", str(p)]
    if full:
        args.append("--full")
    run(who, *args)
    return f"evidence/{name}" if p.exists() else ""


def network(who: str, limit: int = 200) -> list:
    out, _, _ = run(who, "network", "requests")
    return [l for l in out.splitlines() if l.strip()][-limit:]


def install_request_recorder(who: str) -> dict:
    """在页面内挂载 fetch/XHR 记录器（跨导航失效，需每次导航后重装）。"""
    return js(who, r"""
(() => {
  if (window.__g1rec) return {already:true, n: window.__g1rec.length};
  window.__g1rec = [];
  const push = (e) => { try { window.__g1rec.push(e); } catch (x) {} };
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
  XMLHttpRequest.prototype.open = function(m, u) { this.__g1 = {method:m, url:u}; return ox.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(b) {
    const t0 = performance.now();
    this.addEventListener('loadend', () => push({kind:'xhr', method:(this.__g1||{}).method, url:(this.__g1||{}).url,
      status:this.status, body:b ? String(b).slice(0,4000) : null, ms: Math.round(performance.now()-t0)}));
    return os.apply(this, arguments);
  };
  return {installed:true};
})()
""")


def rec(who: str) -> list:
    return js(who, "window.__g1rec || null") or []


def clear_rec(who: str) -> None:
    js(who, "(() => { if (window.__g1rec) window.__g1rec.length = 0; return true; })()")


def click_by_text(who: str, text_pattern: str, tags: str = "button,a") -> dict:
    code = r"""(() => {
  const pat = new RegExp(%s);
  const els = [...document.querySelectorAll(%s)];
  const el = els.find(e => pat.test((e.innerText||'').trim()) || pat.test(e.getAttribute('aria-label')||'') || pat.test(e.title||''));
  if (!el) return {clicked:false, candidates: els.map(e=>(e.innerText||'').trim()).filter(Boolean).slice(0,25)};
  el.scrollIntoView({block:'center'});
  el.click();
  return {clicked:true, text:(el.innerText||'').trim(), tag: el.tagName, disabled: !!el.disabled};
})()""" % (json.dumps(text_pattern), json.dumps(tags))
    r = js(who, code)
    time.sleep(0.8)
    return r


def logout_via_ui(who: str) -> dict:
    """点击退出登录（若存在）。"""
    r = js(who, r"""
(() => {
  const btn = [...document.querySelectorAll('button,a')].find(b => /退出|登出|Logout|Sign out/i.test((b.innerText||'')));
  if (!btn) return {found:false};
  btn.click();
  return {found:true, text:(btn.innerText||'').trim()};
})()""")
    time.sleep(1.2)
    return r


def reload(who: str, wait: float = 1.6) -> str:
    run(who, "reload")
    time.sleep(wait)
    return url(who)


def ui_eval_page_state(who: str) -> dict:
    return js(who, r"""
(() => {
  const t = document.body.innerText || '';
  return {
    url: location.pathname + location.search,
    has_token: !!localStorage.getItem('nodesteer_token'),
    text_len: t.length,
    text_head: t.slice(0, 600),
  };
})()
""")
