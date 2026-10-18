"""qa-resume-20261017-control-gap helpers.

Scope guards:
- Hub endpoint hard-guarded to repair authority port 18080 (never :8080).
- Credentials live in the private 0600 temp file; never printed, persisted
  into evidence, or passed through argv (stdin JS injection only).
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

HERE = Path(__file__).resolve().parent.parent  # evidence/
ROOT = HERE.parent                              # batch root
EV = HERE
TOOLS = HERE / 'tools'

NONCE = '1cdad4fe'
OWNER = 'qa-cgap-' + NONCE
PREFIX = OWNER + '-'
BROWSER_SESSION = OWNER + '-ui'

HOST = '192.168.100.209'
REQUIRED_PORT = 18080
UI = 'http://%s:%d' % (HOST, REQUIRED_PORT)

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
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.calls = 0

    def login(self):
        code, body, _ = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'],
            'password': self.cred['admin_password']})
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

    def upload_artifact(self, name, version, filename, content, arch='amd64', timeout=60):
        boundary = '----cgap' + os.urandom(12).hex()
        parts = []
        for field, value in (('name', name), ('version', version), ('architecture', arch)):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, field, value)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode())
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode())
        data = b''.join(parts)
        hdrs = {
            'Accept': 'application/json',
            'Content-Type': 'multipart/form-data; boundary=' + boundary,
            'Content-Length': str(len(data)),
        }
        if self.token:
            hdrs['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(self.base + '/api/artifacts', data=data, headers=hdrs, method='POST')
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())


# ------------------------------------------------------------------ evidence
def save(name, obj):
    EV.mkdir(parents=True, exist_ok=True)
    p = EV / name
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    return str(p)


def load(name, default=None):
    p = EV / name
    if not p.exists():
        return default
    return json.loads(p.read_text())


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


def ab_login():
    """Submit the sign-in form with credentials injected through stdin JS."""
    _, cred = load_authority()
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
""" % (json.dumps(cred['admin_username']), json.dumps(cred['admin_password']))
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
