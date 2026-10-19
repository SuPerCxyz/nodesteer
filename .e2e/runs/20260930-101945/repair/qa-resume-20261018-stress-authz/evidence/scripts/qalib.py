"""Shared HTTP client for the isolated qa-stress-authz instance (:18089).

Scope guard: every request goes to the isolated instance only. The repair
production hub (:18080) and primary hub (:8080) are never contacted.
Credentials live in a private 0600 file and are never written to evidence.
"""
import http.client
import json
import os
import re
import time
import urllib.parse
import uuid

WORK = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(WORK, 'creds.json')

SQL_MARKERS = re.compile(
    r'sql:|SQLITE|sqlite|no rows in result set|constraint failed|'
    r'database is locked|syntax error|UNIQUE constraint', re.IGNORECASE)


def load_creds():
    st = os.stat(CREDS_PATH)
    if st.st_mode & 0o777 != 0o600:
        raise RuntimeError('creds file must be 0600')
    with open(CREDS_PATH) as f:
        return json.load(f)


class Resp:
    __slots__ = ('status', 'body', 'headers', 'ms', 'method', 'path')

    def __init__(self, status, body, headers, ms, method, path):
        self.status = status
        self.body = body
        self.headers = headers
        self.ms = ms
        self.method = method
        self.path = path

    @property
    def allow(self):
        for k, v in self.headers.items():
            if k.lower() == 'allow':
                return v
        return None

    @property
    def sql_hit(self):
        m = SQL_MARKERS.search(self.body)
        return m.group(0) if m else None

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None

    def snippet(self, n=400):
        return self.body[:n]


class Client:
    def __init__(self, host, port, token=None, timeout=30):
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout

    def request(self, method, path, body=None, token=None, headers=None,
                raw_body=None, content_type=None, read_cap=524288, use_token=True):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            if raw_body is not None:
                data = raw_body
            elif body is not None:
                data = json.dumps(body).encode()
            else:
                data = None
            hdrs = dict(headers or {})
            if data is not None and content_type:
                hdrs['Content-Type'] = content_type
            elif data is not None and 'Content-Type' not in hdrs:
                hdrs['Content-Type'] = 'application/json'
            if use_token and token is None:
                token = self.token
            if token:
                hdrs['Authorization'] = 'Bearer ' + token
            t0 = time.time()
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            raw = resp.read(read_cap)
            ms = (time.time() - t0) * 1000.0
            return Resp(resp.status, raw.decode('utf-8', 'replace'),
                        dict(resp.getheaders()), ms, method, path)
        finally:
            conn.close()

    def login(self, username, password):
        r = self.request('POST', '/api/login',
                         {'username': username, 'password': password}, use_token=False)
        if r.status != 200:
            raise RuntimeError('login failed for %s: %s %s' % (username, r.status, r.snippet(200)))
        self.token = r.json()['token']
        return self.token


def new_uid(prefix='qa-stress'):
    return '%s-%s' % (prefix, uuid.uuid4().hex[:10])


def multipart(fields, filename, content):
    boundary = '----qastress' + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                  'Content-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode())
    parts.append(content)
    parts.append(('\r\n--%s--\r\n' % boundary).encode())
    return b''.join(parts), 'multipart/form-data; boundary=' + boundary


def fire(n, fn):
    """Run fn(i) for i in range(n) as simultaneously as the pool allows."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    barrier = threading.Barrier(n)

    def one(i):
        try:
            barrier.wait(timeout=60)
        except threading.BrokenBarrierError:
            pass
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(one, range(n)))
