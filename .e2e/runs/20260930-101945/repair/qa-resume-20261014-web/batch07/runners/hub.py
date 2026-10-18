#!/usr/bin/env python3
"""Batch07 scoped repair :18080 client.

Hard guard: the authority must be the repair endpoint on port 18080.
Credentials are read from the private 0600 file and never persisted,
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
BATCH = HERE.parent
EVID = BATCH / 'evidence'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
REQUIRED_PORT = 18080

os.umask(0o077)


def _gate(path: Path):
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
    return url, json.loads(CRED.read_text())


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def write_json(name, value):
    p = EVID / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + '\n')
    return str(p)


def _decode(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw[:400].decode('utf-8', 'replace')


class Hub:
    def __init__(self):
        self.base, self.cred = load_authority()
        self.token = None
        self.mutations = []
        self.reads = 0

    # ---------- plumbing ----------
    def login(self):
        code, body = self.raw('POST', '/api/login', {
            'username': self.cred['admin_username'],
            'password': self.cred['admin_password']})
        if code != 200 or not isinstance(body, dict) or not body.get('token'):
            raise RuntimeError('login_failed_http_%s' % code)
        self.token = body['token']
        return self.token

    def raw(self, method, path, body=None, raw=None, ctype=None, timeout=60, agent_headers=None, token=None):
        data = raw
        headers = {'Accept': 'application/json'}
        tok = token if token is not None else self.token
        if tok:
            headers['Authorization'] = 'Bearer ' + tok
        if agent_headers:
            headers.update(agent_headers)
        if body is not None:
            data = json.dumps(body, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        if ctype:
            headers['Content-Type'] = ctype
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        started = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, _decode(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, _decode(exc.read())
        except Exception as exc:  # network level
            return -1, {'transport_error': type(exc).__name__, 'detail': str(exc)[:200]}
        finally:
            if method != 'GET':
                self.mutations.append({'method': method, 'path': path,
                                       'at': now_utc(), 'elapsed_s': round(time.time() - started, 3)})

    def get(self, path, **kw):
        self.reads += 1
        return self.raw('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.raw('POST', path, body=body, **kw)

    def delete(self, path, **kw):
        return self.raw('DELETE', path, **kw)

    def upload_artifact(self, name, version, filename, content, arch='amd64'):
        boundary = '----b07' + os.urandom(8).hex()
        parts = []
        for k, v in (('name', name), ('version', version), ('architecture', arch)):
            parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                          % (boundary, k, v)).encode())
        parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                      'Content-Type: application/octet-stream\r\n\r\n' % (boundary, filename)).encode())
        parts.append(content)
        parts.append(('\r\n--%s--\r\n' % boundary).encode())
        payload = b''.join(parts)
        return self.raw('POST', '/api/artifacts', raw=payload,
                        ctype='multipart/form-data; boundary=%s' % boundary, timeout=300)

    # ---------- state ----------
    def nodes(self):
        code, body = self.get('/api/nodes')
        return body if code == 200 else []

    def node_names(self):
        return {n['id']: n.get('hostname') for n in self.nodes()}

    def counts(self):
        out = {}
        for ep, key in (('/api/nodes', 'nodes'), ('/api/applications', 'applications'),
                        ('/api/tasks', 'tasks'), ('/api/schedules', 'schedules'),
                        ('/api/artifacts', 'artifacts'), ('/api/scripts', 'scripts'),
                        ('/api/groups', 'groups'), ('/api/users', 'users'),
                        ('/api/transfers', 'transfers'), ('/api/executions?limit=1', 'executions_head')):
            code, body = self.get(ep)
            if code != 200:
                out[key] = 'http_%s' % code
            elif key == 'executions_head':
                out[key] = len(body or [])
            else:
                out[key] = len(body or [])
        return out
