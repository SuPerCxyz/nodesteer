"""Scoped repair :18080 API client for the 20261016 regression.

Credentials are read from the private 0600 temp files and never persisted,
printed, or written into evidence. Endpoint is hard-guarded to :18080.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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
        code, body = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'],
            'password': self.cred['admin_password']})
        if code != 200 or not isinstance(body, dict) or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, timeout=25, retries=2, headers=None):
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
                    return resp.status, _decode(resp.read())
            except urllib.error.HTTPError as exc:
                return exc.code, _decode(exc.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last = exc
                time.sleep(0.8)
        raise RuntimeError('api_transport_failure:%s' % type(last).__name__)

    def get(self, path, timeout=25):
        return self.raw('GET', path, None, timeout)

    def post(self, path, body=None, timeout=25):
        return self.raw('POST', path, body, timeout)

    def put(self, path, body=None, timeout=25):
        return self.raw('PUT', path, body, timeout)

    def delete(self, path, timeout=25):
        return self.raw('DELETE', path, None, timeout)

    def upload_artifact(self, name, version, filename, content, arch='amd64', timeout=60):
        """POST /api/artifacts as multipart/form-data with a real file part."""
        boundary = '----r1610' + os.urandom(12).hex()
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

    def raw_text(self, method, path, body=None, timeout=60, headers=None):
        """Return (status, headers, body_text) for precise body assertions."""
        data = None
        hdrs = {'Accept': 'application/json'}
        if headers:
            hdrs.update(headers)
        if self.token and 'Authorization' not in hdrs:
            hdrs['Authorization'] = 'Bearer ' + self.token
        if body is not None:
            data = json.dumps(body, separators=(',', ':')).encode()
            hdrs['Content-Type'] = 'application/json'
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method)
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode('utf-8', 'replace')

    def quote(self, value):
        return urllib.parse.quote(str(value), safe='')


if __name__ == '__main__':
    hub = Hub()
    hub.login()
    code, body = hub.get('/api/nodes')
    nodes = body.get('nodes', body) if isinstance(body, dict) else body
    print(json.dumps(nodes, ensure_ascii=False, indent=1))
