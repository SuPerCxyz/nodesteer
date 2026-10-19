#!/usr/bin/env python3
"""qa-resume-20261018-soak shared helpers.

Owned local-loopback environment ONLY:
  hub 127.0.0.1:18091 (owned data dir), agents through owned TCP proxies.
Never contacts any production/repair endpoint (guard below enforces the port).
"""
import json
import os
import re
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HUB_PORT = 18091
PROXY_A_PORT = 18092          # ws proxy for agent A -> hub
PROXY_HTTP_PORT = 18093       # gateway http proxy -> hub
PROXY_B_PORT = 18094          # ws proxy for agent B -> hub
CTRL_A_PORT = 18192
CTRL_HTTP_PORT = 18193
CTRL_B_PORT = 18194
HUB_BIN = '/tmp/opencode/nodesteer-build-20261018-final/nodesteer-hub-static-final'
AGENT_BIN = '/tmp/opencode/nodesteer-build-20261018-final/nodesteer-agent-static'
HUB_SHA = '00e1f375bf67a59ad04dfc6283741cb276ac6be70ce2f21e02d25094845e7a71'
AGENT_SHA = 'c6ccfe104fec2dfdd25d2e244d60809e6db84066ee296f4a9b3167944d238d01'
WORK = Path('/var/tmp/nodesteer-qa-20261018-soak')
TERMINAL_EXEC = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}
TERMINAL_XFER = {'SUCCESS', 'FAILED', 'CANCELED'}

os.umask(0o077)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _decode(data):
    if not data:
        return None
    try:
        return json.loads(data.decode('utf-8', 'replace'))
    except Exception:
        return data.decode('utf-8', 'replace')


class Hub:
    """API client hard-guarded to the owned 127.0.0.1:18091 endpoint."""

    def __init__(self, cred_path=None):
        cred_path = cred_path or (WORK / 'credentials.json')
        st = cred_path.lstat()
        if cred_path.is_symlink() or not cred_path.is_file() or (st.st_mode & 0o777) != 0o600:
            raise RuntimeError('private_file_gate_failed')
        cred = json.loads(cred_path.read_text())
        self.base = 'http://127.0.0.1:%d' % HUB_PORT
        self.cred = cred
        self.token = None
        self.calls = 0
        self.errors = []

    def login(self):
        code, body = self.raw('POST', '/api/login', {
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
                    if resp.status >= 500:
                        self.errors.append({'path': path, 'http': resp.status})
                    return resp.status, _decode(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code >= 500:
                    self.errors.append({'path': path, 'http': exc.code})
                return exc.code, _decode(exc.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last = exc
                time.sleep(0.5)
        raise RuntimeError('api_transport_failure:%s:%s' % (path, type(last).__name__))

    def get(self, path, timeout=30):
        return self.raw('GET', path, None, timeout)

    def post(self, path, body=None, timeout=60):
        return self.raw('POST', path, body, timeout)

    def put(self, path, body=None, timeout=30):
        return self.raw('PUT', path, body, timeout)

    def delete(self, path, timeout=30):
        return self.raw('DELETE', path, None, timeout)

    def quote(self, value):
        return urllib.parse.quote(str(value), safe='')


class ProxyCtl:
    """Control client for one fault-injection proxy."""

    def __init__(self, control_port):
        self.port = control_port
        self.calls = []

    def cmd(self, line, timeout=10):
        s = socket.create_connection(('127.0.0.1', self.port), timeout=timeout)
        try:
            s.sendall((line + '\n').encode())
            buf = b''
            s.settimeout(timeout)
            while b'\n' not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            resp = json.loads(buf.decode().strip() or '{}')
        finally:
            s.close()
        self.calls.append({'cmd': line, 'resp': resp, 'at': now_utc()})
        return resp

    def kill(self):
        return self.cmd('kill')

    def drop(self, on):
        return self.cmd('drop %s' % ('on' if on else 'off'))

    def throttle(self, bps):
        return self.cmd('throttle %d' % bps)

    def stats(self):
        return self.cmd('stats')

    def stop(self):
        try:
            return self.cmd('stop')
        except OSError:
            return {'ok': False, 'error': 'control_unreachable'}


class AgentProc:
    """One owned local agent process with an owned data dir."""

    def __init__(self, key, ws_url, http_base, node_name, node_ip, deployment_mode='native'):
        self.key = key
        self.root = WORK / ('agent-' + key)
        self.data = self.root / 'data'
        self.share = self.root / 'share'
        self.log_path = self.root / 'agent.log'
        self.config = self.root / 'agent.yaml'
        self.ws_url = ws_url
        self.http_base = http_base
        self.node_name = node_name
        self.node_ip = node_ip
        self.deployment_mode = deployment_mode
        self.node_id = None
        self.agent_id = None
        self.pid = None
        self.restarts = 0
        self.starts = []
        self.stops = []

    def write_config(self, token):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.share.mkdir(mode=0o700, parents=True, exist_ok=True)
        body = (
            'hub_url: "%s"\n'
            'registration_token: "%s"\n'
            'agent_id: "%s"\n'
            'node_name: "%s"\n'
            'node_ip: "%s"\n'
            'deployment_mode: "%s"\n'
            'data_dir: "%s"\n'
            'heartbeat_sec: 2\n'
            'revision_check_sec: 2\n'
            'max_log_bytes: 1048576\n'
        ) % (self.ws_url, token, self.agent_id, self.node_name, self.node_ip,
             self.deployment_mode, self.data)
        fd = os.open(self.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            f.write(body)

    def start(self, wait_s=1.0):
        assert self.config.exists(), 'config not written'
        env = dict(os.environ)
        for k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy'):
            env.pop(k, None)
        env['NO_PROXY'] = '*'
        env['no_proxy'] = '*'
        logf = open(self.log_path, 'ab', buffering=0)
        proc = subprocess.Popen([AGENT_BIN, '--config', str(self.config)],
                                stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        self.pid = proc.pid
        self._proc = proc
        time.sleep(wait_s)
        self.starts.append({'pid': self.pid, 'at': now_utc()})
        return self.pid

    def alive(self):
        if not self.pid:
            return False
        proc = getattr(self, '_proc', None)
        if proc is not None:
            return proc.poll() is None
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def stop(self, timeout=10):
        if not self.pid:
            return {'stopped': True, 'note': 'never_started'}
        proc = getattr(self, '_proc', None)
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        left = True
        if proc is not None:
            try:
                proc.wait(timeout=timeout)
                left = False
            except subprocess.TimeoutExpired:
                left = True
        else:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not self.alive():
                    left = False
                    break
                time.sleep(0.2)
        killed_after_timeout = False
        if left:
            killed_after_timeout = True
            try:
                os.kill(self.pid, signal.SIGKILL)
            except OSError:
                pass
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                    left = False
                except subprocess.TimeoutExpired:
                    left = True
            else:
                time.sleep(0.5)
                left = self.alive()
        rec = {'pid': self.pid, 'stopped': not left, 'killed_after_timeout': killed_after_timeout}
        self.stops.append(rec)
        self.pid = None
        return rec

    def kill(self, sig=signal.SIGKILL):
        if not self.pid:
            return {'killed': False}
        proc = getattr(self, '_proc', None)
        try:
            os.kill(self.pid, sig)
        except OSError:
            pass
        alive = True
        if proc is not None:
            try:
                proc.wait(timeout=5)
                alive = False
            except subprocess.TimeoutExpired:
                alive = True
        else:
            deadline = time.time() + 5
            while time.time() < deadline and self.alive():
                time.sleep(0.1)
            alive = self.alive()
        rec = {'pid': self.pid, 'alive_after': alive, 'signal': int(sig)}
        self.pid = None
        return rec

    def restart(self, wait_s=1.0):
        self.kill()
        self.restarts += 1
        return self.start(wait_s=wait_s)

    def log_tail(self, max_bytes=120000):
        try:
            data = self.log_path.read_bytes()
        except OSError:
            return ''
        return data[-max_bytes:].decode('utf-8', 'replace')

    # ------------------------------------------------------------ local db
    def db(self):
        return self.data / 'state.db'

    def db_query(self, sql, args=()):
        uri = 'file:%s?mode=ro' % self.db()
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute('PRAGMA query_only=ON')
        try:
            rows = conn.execute(sql, args).fetchall()
            cols = [d[0] for d in conn.execute(sql, args).description]
        finally:
            conn.close()
        return [dict(zip(cols, r)) for r in rows]

    def executions(self):
        try:
            return self.db_query(
                'SELECT id, task_id, status, exit_code, synced, start_time, end_time, '
                'length(stdout) AS stdout_len, length(stderr) AS stderr_len '
                'FROM executions ORDER BY start_time')
        except Exception as exc:
            return [{'error': type(exc).__name__ + ':' + str(exc)[:120]}]

    def global_revision(self):
        try:
            row = self.db_query('SELECT global_revision FROM sync_state WHERE id=1')
            return row[0]['global_revision'] if row else None
        except Exception:
            return None


class ProcSampler:
    """Samples VmRSS and CPU jiffies for PIDs; records peaks."""

    def __init__(self, pids_fn, interval=0.5):
        self.pids_fn = pids_fn
        self.interval = interval
        self.samples = []
        self.peak = {}
        self._stop = threading.Event()
        self._thread = None

    def _read(self, pid):
        rss_kb = None
        cpu_jiffies = None
        try:
            with open('/proc/%d/status' % pid) as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        rss_kb = int(line.split()[1])
                        break
        except OSError:
            return None
        try:
            with open('/proc/%d/stat' % pid) as f:
                parts = f.read().split()
            cpu_jiffies = int(parts[13]) + int(parts[14])
        except (OSError, IndexError, ValueError):
            pass
        return {'rss_kb': rss_kb, 'cpu_jiffies': cpu_jiffies}

    def _loop(self):
        while not self._stop.is_set():
            for name, pid in self.pids_fn().items():
                if not pid:
                    continue
                got = self._read(pid)
                if got is None:
                    continue
                rec = {'t': time.time(), 'name': name, 'pid': pid}
                rec.update(got)
                self.samples.append(rec)
                pk = self.peak.setdefault(name, {'rss_kb': 0, 'cpu_jiffies_first': got['cpu_jiffies'],
                                                 'cpu_jiffies_last': got['cpu_jiffies']})
                pk['rss_kb'] = max(pk['rss_kb'], got['rss_kb'] or 0)
                if got['cpu_jiffies'] is not None:
                    if pk['cpu_jiffies_first'] is None:
                        pk['cpu_jiffies_first'] = got['cpu_jiffies']
                    pk['cpu_jiffies_last'] = got['cpu_jiffies']
            self._stop.wait(self.interval)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        out = {}
        for name, pk in self.peak.items():
            cpu_pct = None
            if pk['cpu_jiffies_first'] is not None and pk['cpu_jiffies_last'] is not None:
                delta = pk['cpu_jiffies_last'] - pk['cpu_jiffies_first']
                wall = self.interval * max(1, len([s for s in self.samples if s['name'] == name]) - 1)
                if wall > 0:
                    cpu_pct = round(100.0 * delta / 100.0 / wall, 2)  # jiffies@100Hz
            out[name] = {'peak_rss_kb': pk['rss_kb'], 'avg_cpu_pct': cpu_pct}
        return out


# ------------------------------------------------------------------ helpers
def wait_port_free(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('127.0.0.1', port))
            return True
        except OSError:
            time.sleep(0.3)
        finally:
            s.close()
    return False


def wait_http_ok(port, path='/healthz', timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, path), timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def grep_log(path, patterns):
    hits = {}
    try:
        text = Path(path).read_text(errors='replace')
    except OSError:
        return {'error': 'log_unreadable'}
    for pat in patterns:
        matches = re.findall(pat, text)
        hits[pat] = len(matches)
        if matches:
            hits[pat + '_sample'] = [m if isinstance(m, str) else str(m) for m in matches[:3]]
    return hits


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
