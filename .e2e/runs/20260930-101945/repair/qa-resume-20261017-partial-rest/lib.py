"""qa-resume-20261017-partial-rest helpers.

Scope guards:
- Hub endpoint hard-guarded to repair authority port 18080 (never :8080).
- Isolated instance lives on .209 port 18087, owned+disposable (p0407 only).
- Credentials live in the private 0600 temp file; never printed or persisted
  into evidence; login passwords only passed through stdin JS injection.
- Browser session is batch-owned; close only that session.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent          # batch root
EV = HERE / 'evidence'
EV.mkdir(parents=True, exist_ok=True)

NONCE = '621bfad5'
OWNER = 'qa-prst-' + NONCE
PREFIX = OWNER + '-'
BROWSER_SESSION = OWNER + '-ui'

HOST = '192.168.100.209'
REQUIRED_PORT = 18080
UI = 'http://%s:%d' % (HOST, REQUIRED_PORT)
SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
SSH_DEST = 'root@' + HOST

CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')

os.umask(0o022)


def _gate(path: Path):
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
        raise RuntimeError('private_file_gate_failed:' + path.name)


def load_authority():
    _gate(CONN)
    _gate(CRED)
    conn = json.loads(CONN.read_text())
    url = (conn.get('hub_url') or '').rstrip('/')
    if urllib.parse.urlsplit(url).port != REQUIRED_PORT:
        raise RuntimeError('endpoint_guard_failed_repair_18080_required')
    return url, json.loads(CRED.read_text())


def _decode(data):
    if not data:
        return None
    try:
        return json.loads(data.decode('utf-8', 'replace'))
    except Exception:
        return data.decode('utf-8', 'replace')


class Hub:
    def __init__(self, base=None):
        if base is None:
            self.base, self.cred = load_authority()
        else:
            self.base, self.cred = base, None
        self.token = None
        self.calls = 0

    def login(self, username=None, password=None):
        if username is None:
            username, password = self.cred['admin_username'], self.cred['admin_password']
        code, body, _ = self.raw('POST', '/api/login', {'username': username, 'password': password})
        if code != 200 or not isinstance(body, dict) or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, timeout=30, retries=2, headers=None):
        data = None
        hdrs = {'Accept': 'application/json'}
        if headers:
            hdrs.update(headers)
        if self.token:
            hdrs['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            data = json.dumps(body, separators=(',', ':')).encode()
            hdrs.setdefault('Content-Type', 'application/json')
        last = None
        for _ in range(retries + 1):
            req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, _decode(resp.read()), dict(resp.headers)
            except urllib.error.HTTPError as exc:
                return exc.code, _decode(exc.read()), dict(exc.headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(0.8)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def get(self, path, timeout=30):
        return self.raw('GET', path, None, timeout)

    def post(self, path, body=None, timeout=30):
        return self.raw('POST', path, body, timeout)

    def put(self, path, body=None, timeout=30):
        return self.raw('PUT', path, body, timeout)

    def delete(self, path, timeout=30):
        return self.raw('DELETE', path, None, timeout)

    def logout(self):
        if self.token:
            self.raw('POST', '/api/logout', {})


# ------------------------------------------------------------------ evidence
def save(name, obj):
    p = EV / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    return str(p)


def load(name, default=None):
    p = EV / name
    if not p.exists():
        return default
    return json.loads(p.read_text())


def ledger_add(entry):
    """Append one fixture entry to the batch fixture-ledger.json (idempotent by kind+name)."""
    path = HERE / 'fixture-ledger.json'
    data = json.loads(path.read_text()) if path.exists() else {
        'batch': 'qa-resume-20261017-partial-rest', 'nonce': NONCE, 'owner': OWNER,
        'authority': UI + ' (repair)', 'fixtures': [],
    }
    entry = dict(entry)
    entry.setdefault('recorded_at_utc', time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    data['fixtures'] = [f for f in data['fixtures'] if not (f.get('kind') == entry.get('kind') and f.get('name') == entry.get('name'))]
    data['fixtures'].append(entry)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return entry


# ------------------------------------------------------------------ ssh (isolated instance host .209)
def ssh(cmd, timeout=60, check=True):
    cp = subprocess.run(['ssh', '-i', SSH_KEY, '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=no',
                         SSH_DEST, cmd], capture_output=True, text=True, timeout=timeout)
    if check and cp.returncode != 0:
        raise RuntimeError('ssh_failed rc=%s err=%s' % (cp.returncode, (cp.stderr or '')[-400:]))
    return cp


# ------------------------------------------------------------------ browser
def ab(*args, stdin=None, timeout=180):
    cmd = ['agent-browser', '--session', BROWSER_SESSION] + [str(a) for a in args]
    cp = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def ab_value(out):
    i = out.find('{')
    j = out.find('[')
    if i < 0 or (0 <= j < i):
        i = j
    if i < 0:
        return out.strip()
    try:
        return json.loads(out[i:])
    except Exception:
        return out[i:]


def ab_js(code, timeout=120):
    out, err, rc = ab('eval', '--stdin', stdin=code, timeout=timeout)
    if rc != 0:
        raise RuntimeError('ab_eval_failed: %s%s' % ((err or '')[-300:], (out or '')[-300:]))
    return ab_value(out)


def ab_try_js(code, timeout=120):
    try:
        return ab_js(code, timeout=timeout)
    except Exception as exc:
        return {'_error': str(exc)}


def ab_open(path, wait=2.2):
    ab('open', UI + path)
    time.sleep(wait)
    return ab('get', 'url')[0].strip()


def ab_url():
    return ab('get', 'url')[0].strip()


def ab_shot(name, full=True):
    p = EV / name
    args = ['screenshot', str(p)]
    if full:
        args.append('--full')
    ab(*args)
    return str(p)


def ab_net(filter_pattern=None, clear=False):
    if clear:
        ab('network', 'requests', '--clear')
        return []
    args = ['network', 'requests', '--json']
    if filter_pattern:
        args += ['--filter', filter_pattern]
    out, err, rc = ab(*args)
    v = ab_value(out)
    if isinstance(v, dict):
        return (v.get('data') or {}).get('requests') or []
    return v


def ab_login(username=None, password=None):
    """Submit the sign-in form with credentials injected through stdin JS."""
    if username is None:
        _, cred = load_authority()
        username, password = cred['admin_username'], cred['admin_password']
    ab_open('/sign-in', wait=2.4)
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
  return {ok:true};
})()
""" % (json.dumps(username), json.dumps(password))
    r = ab_js(code)
    token = False
    for _ in range(40):
        time.sleep(0.4)
        try:
            token = bool(ab_js("!!localStorage.getItem('nodesteer_token')"))
        except Exception:
            token = False
        if token:
            break
    return {'submit': r, 'token_in_localStorage': token, 'url': ab_url()}


def ab_close():
    out, err, rc = ab('close')
    return {'stdout': out.strip()[-200:], 'stderr': err.strip()[-200:], 'rc': rc}


# ------------------------------------------------------------------ fetch delay injection (client side)
def patch_fetch_delay(url_part, ms=2500, method='POST'):
    code = r"""
(() => {
  const urlPart = %s, delayMs = %d, method = %s;
  if (window.__prstFetchPatched) return {already: true, target: window.__prstFetchTarget};
  const orig = window.fetch.bind(window);
  window.__prstOrigFetch = orig;
  window.__prstFetchPatched = true;
  window.__prstFetchTarget = urlPart;
  window.fetch = async (input, init) => {
    const url = (typeof input === 'string') ? input : ((input && input.url) || '');
    const m = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (m === method && url.indexOf(urlPart) !== -1) {
      await new Promise(r => setTimeout(r, delayMs));
    }
    return orig(input, init);
  };
  return {patched: true, target: urlPart, delay_ms: delayMs, method: method};
})()
""" % (json.dumps(url_part), ms, json.dumps(method))
    return ab_js(code)


def restore_fetch():
    code = r"""
(() => {
  if (window.__prstOrigFetch) {
    window.fetch = window.__prstOrigFetch;
    window.__prstFetchPatched = false;
    window.__prstOrigFetch = null;
    return {restored: true};
  }
  return {restored: false};
})()
"""
    return ab_js(code)


def non_get(requests):
    return [r for r in requests if (r.get('method') or '').upper() != 'GET']


# ------------------------------------------------------------------ fetch observation (client side, count + delay)
def inject_obs(delay_path=None, delay_ms=0, method='POST'):
    """Patch window.fetch: record /api non-GET requests and optionally delay one path.

    Returns patch status. Observation survives until page navigation.
    """
    code = r"""
(() => {
  const delayPath = %s, delayMs = %d, matchMethod = %s;
  window.__prstObs = {requests: [], counts: {}};
  const orig = window.fetch.bind(window);
  window.__prstOrigFetch = orig;
  window.__prstDelay = {path: delayPath, ms: delayMs};
  window.fetch = async (input, init) => {
    const url = (typeof input === 'string') ? input : ((input && input.url) || '');
    const m = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    let path = url;
    try { path = new URL(url, location.origin).pathname; } catch (e) {}
    if (m !== 'GET' && path.startsWith('/api')) {
      const rec = {method: m, path: path, query: url.indexOf('?') >= 0 ? url.slice(url.indexOf('?')) : '', t: Date.now()};
      window.__prstObs.requests.push(rec);
      window.__prstObs.counts[path] = (window.__prstObs.counts[path] || 0) + 1;
      if (delayMs > 0 && m === matchMethod && path === delayPath) {
        await new Promise(r => setTimeout(r, delayMs));
      }
    }
    return orig(input, init);
  };
  return {patched: true, delay_path: delayPath, delay_ms: delayMs};
})()
""" % (json.dumps(delay_path), delay_ms, json.dumps(method))
    return ab_js(code)


def read_obs():
    return ab_js("(() => window.__prstObs || {requests: [], counts: {}})()")


def reset_obs():
    return ab_js("(() => { if (window.__prstObs) { window.__prstObs = {requests: [], counts: {}}; } return true; })()")


def restore_obs():
    code = r"""
(() => {
  if (window.__prstOrigFetch) { window.fetch = window.__prstOrigFetch; window.__prstOrigFetch = null; return {restored: true}; }
  return {restored: false};
})()
"""
    return ab_js(code)


def dbl_click_js(find_js):
    """Dispatch a human-like double click (two clicks 110ms apart) on the element found by find_js."""
    code = r"""
(() => {
  const el = %s;
  if (!el) return {ok: false, reason: 'not-found'};
  const opts = {bubbles: true, cancelable: true, view: window};
  el.dispatchEvent(new MouseEvent('click', opts));
  setTimeout(() => { el.dispatchEvent(new MouseEvent('click', opts)); el.dispatchEvent(new MouseEvent('dblclick', opts)); }, 110);
  return {ok: true, tag: el.tagName, text: (el.innerText || '').trim().slice(0, 60), disabled: !!el.disabled};
})()
""" % find_js
    return ab_js(code)


def brief_reqs(requests):
    return [{k: r.get(k) for k in ('method', 'url', 'status') if r.get(k) is not None} for r in requests]


def wait_until(fn, timeout=30, interval=1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:
            last = exc
        time.sleep(interval)
    return last


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
