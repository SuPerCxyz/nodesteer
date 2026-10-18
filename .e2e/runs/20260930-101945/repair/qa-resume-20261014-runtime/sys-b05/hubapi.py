"""Scoped repair :18080 API client for sys-b05.

Credentials are read from the private 0600 temp file and never persisted,
printed, or written into evidence.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
REQUIRED_PORT = 18080

os.umask(0o077)


def _gate(path):
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
        raise RuntimeError('private_file_gate_failed:' + path.name)


def load_authority():
    _gate(CONN)
    _gate(CRED)
    conn = json.loads(CONN.read_text())
    url = (conn.get('hub_url') or '').rstrip('/')
    split = urllib.parse.urlsplit(url)
    if split.port != REQUIRED_PORT:
        raise RuntimeError('endpoint_guard_failed_repair_18080_required')
    cred = json.loads(CRED.read_text())
    return url, cred


class Hub:
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.calls = 0

    def login(self):
        code, body = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'],
            'password': self.cred['admin_password']})
        if code != 200 or not isinstance(body, dict) or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, timeout=25, retries=3):
        data = None
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            data = json.dumps(body, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        last = None
        for _ in range(retries):
            req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.status, _decode(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, _decode(exc.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def get(self, path, timeout=25):
        return self.raw('GET', path, None, timeout)

    def json(self, path, timeout=25):
        code, body = self.get(path, timeout)
        if code != 200:
            raise RuntimeError('get_%s_http_%s' % (path, code))
        return body

    def post(self, path, body=None):
        return self.raw('POST', path, body if body is not None else {})

    def put(self, path, body):
        return self.raw('PUT', path, body)

    def delete(self, path):
        return self.raw('DELETE', path)

    def quote(self, value):
        return urllib.parse.quote(str(value), safe='')


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return {'_non_json_body': raw[:300].decode('utf-8', 'replace')}


def write_json(name, value, mode=0o644):
    path = HERE / name
    tmp = HERE / (name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    return path
