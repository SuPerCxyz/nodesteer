#!/usr/bin/env python3
"""qa-resume-20261018-scale: multi-node (8-agent) scale test on a fully owned
local-loopback NodeSteer environment.

Hub 127.0.0.1:18921 (single-port web+gateway), 8 owned agents with independent
data dirs. Scenarios:
  S1 bulk enrollment (8 agents online + revision convergence), timing
  S2 heartbeat stability >=180s, no flapping, hub CPU/mem peaks
  S3 fan-out execution to all 8 nodes (stdout+stderr logs, exit codes)
  S4 group scheduling (group of 8, interval schedule, 2 fires, concurrency/order)
  S5 node jitter (kill 2 agents -> offline; restart -> online + revision converge)
  S6 concurrent pressure (8 nodes x 3 parallel runs = 24 executions)
  S7 API latency at scale vs 0-node/1-node baselines

Only owned objects; nothing here ever contacts production or the repair fleet.
Report-only: no product code is modified.
"""
import argparse
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scalelib as S  # noqa: E402

RUN = HERE.parent
EV = RUN / 'evidence'
RAW = EV / 'raw'
STATE_F = RUN / 'scale-state.json'

AGENT_COUNT = 8
S2_DURATION = 185.0
WATCHDOG_S = 1500.0

os.umask(0o077)
HUB_LOG = S.WORK / 'hub' / 'hub.log'
HUB_DATA = S.WORK / 'hub' / 'data'
SHARED = S.WORK / 'shared'


def now():
    return time.time()


def write_json(path, obj):
    path = Path(path)
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ secrets
def load_secrets():
    cred = read_json(S.WORK / 'credentials.json', {}) or {}
    return [v for v in (cred.get('admin_password'), cred.get('registration_token')) if v]


def redact_text(text):
    for sec in load_secrets():
        if sec:
            text = text.replace(sec, '[REDACTED-CREDENTIAL]')
    return text


def write_evidence_text(name, text):
    p = RAW / name
    p.write_text(redact_text(text))
    return 'evidence/raw/' + name


class State:
    def __init__(self):
        self.data = None
        if os.environ.get('QA_SCALE_RESUME') == '1':
            self.data = read_json(STATE_F, None)
        if not self.data:
            self.data = {
                'batch': 'qa-resume-20261018-scale',
                'nonce': secrets.token_hex(4),
                'scenarios': {},
                'instances': {},
                'fixtures': [],
                'started_at_utc': S.now_utc(),
            }
        self.save()

    def save(self):
        self.data['updated_at_utc'] = S.now_utc()
        write_json(STATE_F, self.data)

    @property
    def nonce(self):
        return self.data['nonce']

    def record(self, key, verdict, summary, details=None, evidence=None):
        self.data['scenarios'][key] = {
            'verdict': verdict, 'summary': summary,
            'details': details or {}, 'evidence': evidence or [],
            'at_utc': S.now_utc(),
        }
        self.save()
        print('[SCENARIO] %s -> %s :: %s' % (key, verdict, summary), flush=True)

    def fixture(self, kind, fid, name):
        self.data['fixtures'].append([kind, fid, name])
        self.save()


ST = State()
NONCE = ST.nonce
P = 'qa-scale-%s-' % NONCE

AGENTS = {}
HUB = None
SAMPLER = None
LOAD_SAMPLES = []
LOAD_STOP = threading.Event()
RECORDED_PIDS = []  # every pid we ever launched (hub + all agent starts)


def note_pid(pid):
    if pid:
        RECORDED_PIDS.append(int(pid))


# ------------------------------------------------------------------- hub proc
class HubProc:
    def __init__(self):
        self.config = S.WORK / 'hub' / 'hub.yaml'
        self.log = HUB_LOG
        self.pid = None
        self._proc = None
        self.starts = []

    def start(self, timeout=90):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log, 'ab', buffering=0)
        proc = subprocess.Popen([S.HUB_BIN, '--config', str(self.config)],
                                stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        self.pid = proc.pid
        self._proc = proc
        note_pid(self.pid)
        self.starts.append({'pid': self.pid, 'at': S.now_utc()})
        ok_h = S.wait_http_ok(S.HUB_PORT, '/healthz', timeout)
        ok_r = S.wait_http_ok(S.HUB_PORT, '/readyz', timeout)
        return {'pid': self.pid, 'healthz': ok_h, 'readyz': ok_r}

    def alive(self):
        if not self.pid:
            return False
        proc = self._proc
        if proc is not None:
            return proc.poll() is None
        try:
            os.kill(self.pid, 0)
            return True
        except OSError:
            return False

    def stop(self, timeout=15):
        if not self.pid:
            return {'stopped': True, 'note': 'never_started'}
        proc = self._proc
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
        graceful = not left
        if left:
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
        rec = {'pid': self.pid, 'graceful': graceful, 'stopped': not left}
        self.pid = None
        self._proc = None
        return rec


def load_sampler():
    while not LOAD_STOP.is_set():
        try:
            with open('/proc/loadavg') as f:
                parts = f.read().split()
            with open('/proc/meminfo') as f:
                mem = {}
                for line in f:
                    k, _, v = line.partition(':')
                    mem[k.strip()] = v.strip()
            LOAD_SAMPLES.append({
                't': round(time.time(), 3),
                'load1': float(parts[0]),
                'load5': float(parts[1]),
                'load15': float(parts[2]),
                'mem_available_kb': int(mem.get('MemAvailable', '0 kB').split()[0]),
            })
        except Exception:
            pass
        LOAD_STOP.wait(2.0)


def pids_snapshot():
    out = {}
    if HUB and HUB.pid:
        out['hub'] = HUB.pid
    for k, ag in AGENTS.items():
        if ag.pid:
            out['agent-%s' % k] = ag.pid
    return out


# ------------------------------------------------------------------ helpers
def node_info(hub, node_id):
    try:
        code, n = hub.get('/api/nodes/' + hub.quote(node_id))
        if code == 200 and isinstance(n, dict):
            return n
    except Exception:
        pass
    return None


def wait_node_online(hub, node_id, timeout=150):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        n = node_info(hub, node_id)
        if n:
            last = n
            if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                return n
        time.sleep(1.0)
    return last


def wait_node_status(hub, node_id, want, timeout=150):
    deadline = time.time() + timeout
    last = None
    t0 = now()
    while time.time() < deadline:
        n = node_info(hub, node_id)
        if n:
            last = n
            if n.get('status') == want:
                return n, round(now() - t0, 2)
        time.sleep(1.0)
    return last, None


def wait_exec(hub, exec_id, timeout=300):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        except Exception:
            time.sleep(1.5)
            continue
        if code == 200 and isinstance(ex, dict):
            last = ex
            if ex.get('status') in S.TERMINAL_EXEC:
                return ex
        time.sleep(1.0)
    return last


def run_task(hub, task_id, params=None):
    t0 = now()
    code, run = hub.post('/api/tasks/%s/run' % task_id, {'params': params or {}})
    dt = round((now() - t0) * 1000.0, 1)
    execs = run if isinstance(run, list) else []
    return {'http': code, 'execs': execs, 'ms': dt}


def bump_script_revision(hub, script_id, marker):
    code, sc = hub.get('/api/scripts/' + hub.quote(script_id))
    if code != 200:
        raise RuntimeError('script_get_failed_%s' % code)
    body = dict(sc)
    body['description'] = marker
    code, out = hub.put('/api/scripts/' + hub.quote(script_id), body)
    return code, out


def hub_rev():
    return S.read_hub_revision(HUB_DATA)


def wait_revision(hub, node_ids, want_rev, timeout=90):
    res = {}
    for nid in node_ids:
        t0 = now()
        ok = False
        last = None
        deadline = time.time() + timeout
        while time.time() < deadline:
            n = node_info(hub, nid)
            if n:
                last = n
                if n.get('global_revision') == want_rev:
                    ok = True
                    break
            time.sleep(1.0)
        res[nid] = {'ok': ok, 'ms': round((now() - t0) * 1000.0, 1),
                    'global_revision': (last or {}).get('global_revision'),
                    'sync_status': (last or {}).get('sync_status')}
    return res


def http_get_ms(port, path, timeout=5):
    t0 = time.time()
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d%s' % (port, path), timeout=timeout) as resp:
            resp.read()
            code = resp.status
    except Exception as exc:
        code = type(exc).__name__
    return code, round((time.time() - t0) * 1000.0, 2)


def parse_ts(ts):
    import datetime as _dt
    return _dt.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()


def stats_of(values):
    if not values:
        return {'n': 0}
    vs = sorted(values)
    n = len(vs)

    def pct(p):
        idx = min(n - 1, int(round((p / 100.0) * (n - 1))))
        return vs[idx]
    return {'n': n, 'min_ms': round(vs[0], 2), 'p50_ms': round(pct(50), 2),
            'p95_ms': round(pct(95), 2), 'max_ms': round(vs[-1], 2),
            'mean_ms': round(sum(vs) / n, 2)}


def sample_endpoint(hub, name, fn, n=30):
    samples = []
    codes = {}
    for _ in range(n):
        t0 = time.time()
        code = None
        try:
            code, _body = fn(hub)
        except Exception as exc:
            code = 'ERR:' + type(exc).__name__
        ms = round((time.time() - t0) * 1000.0, 2)
        codes[str(code)] = codes.get(str(code), 0) + 1
        samples.append(ms)
    out = stats_of(samples)
    out['codes'] = codes
    out['endpoint'] = name
    return out


def burst_sample(hub, path, concurrency=10):
    results = []
    lock = threading.Lock()

    def one():
        t0 = time.time()
        try:
            code, _ = hub.get(path, retries=0)
        except Exception as exc:
            code = 'ERR:' + type(exc).__name__
        ms = round((time.time() - t0) * 1000.0, 2)
        with lock:
            results.append({'http': code, 'ms': ms})

    t0 = time.time()
    threads = [threading.Thread(target=one) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    wall = round((time.time() - t0) * 1000.0, 2)
    return {'path': path, 'concurrency': concurrency, 'wall_ms': wall,
            'results': results,
            'stats': stats_of([r['ms'] for r in results]),
            'errors': [r for r in results if not isinstance(r['http'], int) or r['http'] >= 400]}


# --------------------------------------------------------------------- setup
def setup_environment():
    print('== setup: isolated 8-agent local environment ==', flush=True)
    if not S.wait_port_free(S.HUB_PORT, 5):
        raise RuntimeError('port_busy_%d' % S.HUB_PORT)
    if S.WORK.exists():
        subprocess.run(['rm', '-rf', str(S.WORK)], check=False)
    for d in ('hub', 'hub/data', 'shared'):
        (S.WORK / d).mkdir(parents=True, exist_ok=True)

    admin_pw = 'qa-scale-' + secrets.token_urlsafe(12)
    reg_token = 'qa-scale-reg-' + secrets.token_urlsafe(12)
    cred_path = S.WORK / 'credentials.json'
    fd = os.open(cred_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'admin_username': 'admin', 'admin_password': admin_pw,
                   'registration_token': reg_token}, f)

    hub_cfg = (
        'web_addr: "127.0.0.1:%d"\n'
        'gateway_addr: "127.0.0.1:%d"\n'
        'gateway_base_url: "http://127.0.0.1:%d"\n'
        'registration_token: "%s"\n'
        'data_dir: "%s"\n'
        'artifact_dir: "%s"\n'
        'base_url: "http://127.0.0.1:%d"\n'
        'admin_username: "admin"\n'
        'admin_password: "%s"\n'
        'session_ttl: 24h\n'
        'revision_check_sec: 5\n'
        'heartbeat_timeout: 20s\n'
    ) % (S.HUB_PORT, S.HUB_PORT, S.HUB_PORT, reg_token,
         HUB_DATA, HUB_DATA / 'artifacts', S.HUB_PORT, admin_pw)
    fd = os.open(HUB.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(hub_cfg)

    started = HUB.start()
    if not (started['healthz'] and started['readyz']):
        raise RuntimeError('hub_not_ready:%s' % started)
    ST.data['instances']['hub'] = {'pid': HUB.pid, 'port': S.HUB_PORT,
                                   'data_dir': str(HUB_DATA),
                                   'binary_sha256': S.sha256_file(S.HUB_BIN),
                                   'config_redacted': redact_text(hub_cfg)}
    ST.save()

    hub = S.Hub()
    hub.login()
    # 运行期参数：更快心跳与 revision 检查（5s 是 hub 调度 tick 的下限）
    code, out = hub.put('/api/settings', {'heartbeat_interval_sec': '2',
                                          'revision_check_interval_sec': '5'})
    if code != 200:
        raise RuntimeError('settings_update_failed_%s_%s' % (code, out))
    print('setup: hub ready pid=%s, settings applied' % HUB.pid, flush=True)
    return hub


def enroll_agents(hub, count=AGENT_COUNT):
    """S1: enroll+launch all agents, measure per-agent timing."""
    print('== S1: bulk enrollment of %d agents ==' % count, flush=True)
    t_all0 = now()
    per = {}
    for i in range(1, count + 1):
        key = str(i)
        name = '%sagent-%d' % (P, i)
        ip = '10.99.0.%d' % i
        t0 = now()
        code, enroll = hub.post('/api/nodes/enrollment',
                                {'node_name': name, 'node_ip': ip,
                                 'hub_address': 'http://127.0.0.1:%d' % S.HUB_PORT},
                                tag='enroll')
        t_enroll = now()
        if code != 200 or not enroll.get('node_id'):
            raise RuntimeError('enrollment_failed_%s_%s' % (key, str(enroll)[:200]))
        ag = S.AgentProc(key, name, ip, data_root=S.WORK)
        ag.node_id = enroll['node_id']
        ag.agent_id = enroll['agent_id']
        ag.write_config(hub.cred['registration_token'])
        pid = ag.start(wait_s=0.2)
        note_pid(pid)
        AGENTS[key] = ag
        per[key] = {
            'node_id': ag.node_id, 'agent_id': ag.agent_id, 'node_name': name, 'node_ip': ip,
            'enroll_api_ms': round((t_enroll - t0) * 1000.0, 1),
            'launch_ms': round((now() - t_enroll) * 1000.0, 1),
            'pid': pid, '_t_launch_mono': now(),
        }
        ST.data['instances']['agent-' + key] = {
            'pid': pid, 'node_id': ag.node_id, 'agent_id': ag.agent_id,
            'node_name': name, 'node_ip': ip, 'data_dir': str(ag.data),
            'binary_sha256': S.sha256_file(S.AGENT_BIN)}
        ST.fixture('node', ag.node_id, name)
        ST.save()
        print('  enrolled+launched %s (%s) pid=%s' % (name, ag.node_id[:8], pid), flush=True)

    for key, ag in AGENTS.items():
        n = wait_node_online(hub, ag.node_id, timeout=150)
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            raise RuntimeError('agent_%s_not_online_synced:%s' % (key, n))
        per[key]['online_synced'] = True
        per[key]['start_to_synced_ms'] = round((now() - per[key]['_t_launch_mono']) * 1000.0, 1)
        per[key]['last_seen'] = n.get('last_seen')
        per[key]['global_revision_at_first_sync'] = n.get('global_revision')
    t_all1 = now()

    rev0 = hub_rev()
    rev_report = {}
    for key, ag in AGENTS.items():
        n = node_info(hub, ag.node_id) or {}
        rev_report[key] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                           'global_revision': n.get('global_revision')}
    all_match0 = all(v['global_revision'] == rev0 and v['sync_status'] == 'synced'
                     for v in rev_report.values())

    # revision bump -> all 8 converge
    code, script = hub.post('/api/scripts', {
        'name': P + 'scale-script', 'description': 'scale script v1',
        'interpreter': 'shell', 'enabled': True, 'environment': {}, 'working_dir': '',
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'content': (
            'SID="$NODESTEER_PARAM_run_id"\n'
            'SIDE="$NODESTEER_PARAM_side_file"\n'
            'echo "OUT-$SID-start $(date +%s.%N) pid=$$"\n'
            'echo "ERR-$SID-line $(date +%s.%N)" >&2\n'
            'sleep 3\n'
            'echo "OUT-$SID-done $(date +%s.%N)"\n'
            'printf \'%s %s\\n\' "$SID" "$$" >> "$SIDE"\n')})
    if code not in (200, 201):
        raise RuntimeError('script_create_failed_%s_%s' % (code, script))
    ST.data['ids'] = {'script': script['id']}
    ST.fixture('script', script['id'], script['name'])
    ST.save()

    rev_before = hub_rev()
    code, _ = bump_script_revision(hub, script['id'], 'rev-bump-s1-' + NONCE)
    rev_after = hub_rev()
    converge = wait_revision(hub, [ag.node_id for ag in AGENTS.values()], rev_after, timeout=90)
    conv_ok = all(v['ok'] for v in converge.values())

    t_all2 = now()
    for v in per.values():
        v.pop('_t_launch_mono', None)
    details = {
        'per_agent': per,
        'total_wall_s': round(t_all2 - t_all0, 2),
        'revision': {'hub_rev_initial': rev0, 'rev_before_bump': rev_before,
                     'rev_after_bump': rev_after,
                     'all_synced_at_initial_rev': all_match0,
                     'initial_state': rev_report,
                     'convergence': converge, 'convergence_ok': conv_ok},
        'settings': {'heartbeat_interval_sec': 2, 'revision_check_interval_sec': 5,
                     'heartbeat_timeout': '20s'},
    }
    verdict = 'PASS' if (all_match0 and conv_ok) else 'FAIL'
    ST.record('S1_bulk_enrollment', verdict,
              '%d agents online+synced, revision %s->%s converged=%s, wall=%ss' % (
                  count, rev_before, rev_after, conv_ok, details['total_wall_s']),
              details=details)
    write_json(RAW / 's1-enrollment.json', details)
    return details


def scenario_2_heartbeat(hub, duration=S2_DURATION):
    print('== S2: heartbeat stability %.0fs ==' % duration, flush=True)
    node_ids = {k: ag.node_id for k, ag in AGENTS.items()}
    samples = []
    stop = threading.Event()
    errors = []

    def sampler():
        while not stop.is_set():
            t0 = now()
            try:
                code, nodes = hub.get('/api/nodes', retries=0)
            except Exception as exc:
                errors.append({'t': round(now(), 3), 'error': type(exc).__name__})
                stop.wait(1.0)
                continue
            ms = round((now() - t0) * 1000.0, 2)
            if code != 200 or not isinstance(nodes, list):
                errors.append({'t': round(now(), 3), 'http': code})
                stop.wait(1.0)
                continue
            by_id = {n['id']: n for n in nodes}
            rec = {'t': round(now(), 3), 'ms': ms, 'online': 0, 'statuses': {}}
            for k, nid in node_ids.items():
                n = by_id.get(nid) or {}
                rec['statuses'][k] = n.get('status')
                if n.get('status') == 'online':
                    rec['online'] += 1
            samples.append(rec)
            stop.wait(1.0)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    t0 = now()
    while now() - t0 < duration:
        time.sleep(5)
    stop.set()
    th.join(timeout=10)

    transitions = {k: [] for k in node_ids}
    prev = {}
    for s in samples:
        for k, st in s['statuses'].items():
            if k in prev and prev[k] != st:
                transitions[k].append({'t': s['t'], 'from': prev[k], 'to': st})
            prev[k] = st
    off_samples = sum(1 for s in samples if s['online'] < len(node_ids))
    reconnects = {k: ag.connected_count() for k, ag in AGENTS.items()}
    hub_err = len(hub.errors)
    hub_log_hits = S.grep_log(HUB_LOG, [r'node marked offline', r'level=ERROR', r'panic:'])
    lat = stats_of([s['ms'] for s in samples])
    stable = off_samples == 0 and not any(transitions.values()) and not errors
    details = {
        'duration_s': round(now() - t0, 1),
        'samples': len(samples),
        'offline_samples': off_samples,
        'transitions': {k: v for k, v in transitions.items() if v},
        'sampler_errors': errors,
        'list_latency_ms': lat,
        'agent_connected_counts': reconnects,
        'hub_http_5xx_total': hub_err,
        'hub_log_hits': hub_log_hits,
    }
    write_json(RAW / 's2-samples.json', {'samples': samples})
    ST.record('S2_heartbeat_stability', 'PASS' if stable else 'FAIL',
              '%d samples, 0 flapping=%s, reconnects=%s' % (
                  len(samples), off_samples == 0, reconnects),
              details=details, evidence=['evidence/raw/s2-samples.json'])
    return details


def scenario_3_fanout(hub):
    print('== S3: fan-out execution to all 8 nodes ==', flush=True)
    script_id = ST.data['ids']['script']
    node_ids = [ag.node_id for ag in AGENTS.values()]
    code, task = hub.post('/api/tasks', {
        'name': P + 'task-fanout', 'type': 'script', 'script_id': script_id,
        'target': {'type': 'node', 'node_ids': node_ids},
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'param_values': {}, 'timeout': 120, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True})
    if code not in (200, 201):
        raise RuntimeError('task_create_failed_%s_%s' % (code, task))
    ST.data['ids']['task_fanout'] = task['id']
    ST.fixture('task', task['id'], task['name'])
    ST.save()

    side = SHARED / ('s3-%s.log' % NONCE)
    run = run_task(hub, task['id'], {'run_id': 's3-' + NONCE, 'side_file': str(side)})
    execs = run['execs']
    if run['http'] != 200 or len(execs) != 8:
        ST.record('S3_fanout', 'FAIL', 'run returned http=%s execs=%d' % (run['http'], len(execs)))
        return {'run': run}
    by_node = {}
    results = {}
    for e in execs:
        by_node.setdefault(e['node_id'], []).append(e['id'])
    t0 = now()
    for e in execs:
        ex = wait_exec(hub, e['id'], timeout=180)
        logs = None
        try:
            lc, chunks = hub.get('/api/executions/%s/logs' % hub.quote(e['id']))
            if lc == 200 and isinstance(chunks, list):
                streams = {}
                for c in chunks:
                    streams.setdefault(c.get('stream'), []).append(c.get('chunk', ''))
                logs = {'chunks': len(chunks), 'streams': sorted(streams.keys()),
                        'stdout_has_marker': any('OUT-s3-' + NONCE in x for x in streams.get('stdout', [])),
                        'stderr_has_marker': any('ERR-s3-' + NONCE in x for x in streams.get('stderr', []))}
        except Exception as exc:
            logs = {'error': type(exc).__name__}
        results[e['id']] = {
            'node_id': e['node_id'], 'status': (ex or {}).get('status'),
            'exit_code': (ex or {}).get('exit_code'),
            'stdout_has_start': 'OUT-s3-%s-start' % NONCE in (ex or {}).get('stdout', ''),
            'stdout_has_done': 'OUT-s3-%s-done' % NONCE in (ex or {}).get('stdout', ''),
            'stderr_has_line': 'ERR-s3-%s-line' % NONCE in (ex or {}).get('stderr', ''),
            'stdout_len': len((ex or {}).get('stdout', '')),
            'stderr_len': len((ex or {}).get('stderr', '')),
            'logs': logs,
        }
    elapsed = round(now() - t0, 2)
    try:
        side_lines = side.read_text().strip().splitlines()
    except OSError:
        side_lines = []
    per_node_ok = all(len(v) == 1 for v in by_node.values()) and len(by_node) == 8
    all_ok = all(r['status'] == 'SUCCESS' and r['exit_code'] == 0 and r['stdout_has_start']
                 and r['stdout_has_done'] and r['stderr_has_line'] for r in results.values())
    chunks_ok = all(r['logs'] and r['logs'].get('stdout_has_marker') and r['logs'].get('stderr_has_marker')
                    for r in results.values())
    details = {
        'run_http': run['http'], 'run_api_ms': run['ms'], 'exec_count': len(execs),
        'one_per_node': per_node_ok, 'nodes_targeted': len(by_node),
        'wall_to_terminal_s': elapsed,
        'results': results,
        'side_file_lines': len(side_lines),
        'side_file_ok': len(side_lines) == 8,
        'all_terminal_success': all_ok,
        'realtime_chunks_complete': chunks_ok,
    }
    verdict = 'PASS' if (all_ok and per_node_ok and len(side_lines) == 8) else 'FAIL'
    ST.record('S3_fanout_execution', verdict,
              '8 nodes: terminal_success=%s, one_per_node=%s, wall=%ss' % (
                  all_ok, per_node_ok, elapsed), details=details)
    write_json(RAW / 's3-fanout.json', details)
    return details


def scenario_4_group_schedule(hub):
    print('== S4: group scheduling (8 nodes) ==', flush=True)
    node_ids = [ag.node_id for ag in AGENTS.values()]
    code, group = hub.post('/api/groups', {
        'name': P + 'group-8', 'description': 'scale group with 8 nodes',
        'type': 'static', 'members': node_ids})
    if code not in (200, 201):
        raise RuntimeError('group_create_failed_%s_%s' % (code, group))
    ST.fixture('group', group['id'], group['name'])
    code, g = hub.get('/api/groups/' + hub.quote(group['id']))
    group_members = (g or {}).get('members') or []

    code, task = hub.post('/api/tasks', {
        'name': P + 'task-group', 'type': 'script', 'script_id': ST.data['ids']['script'],
        'target': {'type': 'group', 'group_ids': [group['id']]},
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': False, 'default': 's4-sched'},
                       {'name': 'side_file', 'type': 'string', 'required': False, 'default': str(SHARED / ('s4-%s.log' % NONCE))}],
        'param_values': {'run_id': 's4-sched', 'side_file': str(SHARED / ('s4-%s.log' % NONCE))},
        'timeout': 120, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True})
    if code not in (200, 201):
        raise RuntimeError('task_create_failed_%s_%s' % (code, task))
    ST.data['ids']['task_group'] = task['id']
    ST.fixture('task', task['id'], task['name'])

    code, sched = hub.post('/api/schedules', {
        'task_id': task['id'], 'type': 'interval', 'interval_sec': 20,
        'timezone': 'UTC', 'execution_owner': 'hub',
        'offline_policy': 'hub_online_required', 'misfire_policy': 'run_once',
        'enabled': True})
    if code not in (200, 201):
        raise RuntimeError('schedule_create_failed_%s_%s' % (code, sched))
    ST.data['ids']['schedule_group'] = sched['id']
    ST.fixture('schedule', sched['id'], 'interval-20s')
    ST.save()
    t_created = now()

    # wait for 2 fires (dedupe executions by id across polls)
    fires = {}
    first_seen = {}
    deadline = time.time() + 200
    while time.time() < deadline:
        code, lst = hub.get('/api/executions?task_id=%s&limit=200' % hub.quote(task['id']))
        if code == 200 and isinstance(lst, list):
            for e in lst:
                st = e.get('scheduled_time')
                if st:
                    fires.setdefault(st, {})[e['id']] = e
                    first_seen.setdefault(st, round(now() - t_created, 1))
        if len(fires) >= 2:
            break
        time.sleep(2.0)
    t_second_fire = now()

    fire_report = {}
    for st, edict in sorted(fires.items()):
        elist = list(edict.values())
        per_node = {}
        for e in elist:
            per_node.setdefault(e['node_id'], []).append(e)
        starts = [e.get('start_time') for e in elist if e.get('start_time')]
        fire_report[st] = {
            'exec_count': len(elist),
            'first_seen_s': first_seen.get(st),
            'per_node_counts': {k: len(v) for k, v in per_node.items()},
            'one_per_node': len(per_node) == 8 and all(len(v) == 1 for v in per_node.values()),
            'start_time_spread_ms': None,
            'statuses': {},
        }
        if starts:
            try:
                vals = sorted(parse_ts(x) for x in starts)
                fire_report[st]['start_time_spread_ms'] = round((vals[-1] - vals[0]) * 1000.0, 1)
            except Exception:
                pass
    # terminal statuses for all executions of this task (superseded by the
    # wait-for-terminal block below; kept here only to seed ordering analysis)
    code, lst = hub.get('/api/executions?task_id=%s&limit=200' % hub.quote(task['id']))
    all_execs = lst if (code == 200 and isinstance(lst, list)) else []

    # per-node ordering semantics
    per_node_order = {}
    for e in all_execs:
        per_node_order.setdefault(e['node_id'], []).append(
            {'st': e.get('scheduled_time'), 'start': e.get('start_time'), 'status': e.get('status')})
    order_ok = True
    for nid, items in per_node_order.items():
        items.sort(key=lambda x: x['st'] or '')
        starts = [x['start'] for x in items if x['start']]
        if starts != sorted(starts):
            order_ok = False

    # fire N+1 starts only after fire N ended (sequential semantics on each node)
    seq_ok = True
    seq_detail = {}
    try:
        ordered = sorted(fires.keys())
        if len(ordered) >= 2:
            def bounds(st):
                el = list(fires[st].values())
                ss = [parse_ts(e['start_time']) for e in el if e.get('start_time')]
                ee = [parse_ts(e['end_time']) for e in el if e.get('end_time')]
                return (min(ss), max(ee)) if ss and ee else None
            b1, b2 = bounds(ordered[0]), bounds(ordered[1])
            seq_ok = bool(b1 and b2 and b2[0] > b1[1])
            seq_detail = {'fire1_end': b1[1] if b1 else None,
                          'fire2_start': b2[0] if b2 else None}
    except Exception as exc:
        seq_ok = False
        seq_detail = {'error': type(exc).__name__}

    # stop the schedule (owned fixture) so it cannot interfere with later scenarios
    code_del, del_out = hub.delete('/api/schedules/' + hub.quote(sched['id']))

    # wait until every execution of the observed fires reaches a terminal state
    t_term0 = now()
    terminal_deadline = time.time() + 120
    all_execs = []
    while time.time() < terminal_deadline:
        code, lst = hub.get('/api/executions?task_id=%s&limit=200' % hub.quote(task['id']))
        if code == 200 and isinstance(lst, list):
            all_execs = lst
            if all(e.get('status') in S.TERMINAL_EXEC for e in all_execs):
                break
        time.sleep(2.0)
    wall_to_terminal_s = round(now() - t_term0, 1)
    status_counts = {}
    for e in all_execs:
        status_counts[e.get('status')] = status_counts.get(e.get('status'), 0) + 1
    non_terminal = [e['id'] for e in all_execs if e.get('status') not in S.TERMINAL_EXEC]
    for e in all_execs:
        if e.get('scheduled_time') in fire_report and e.get('status'):
            fire_report[e['scheduled_time']]['statuses'][e['status']] = \
                fire_report[e['scheduled_time']]['statuses'].get(e['status'], 0) + 1

    fires_ok = all(f['one_per_node'] for f in fire_report.values())
    fires_with_8 = sum(1 for f in fire_report.values() if f['exec_count'] == 8)
    details = {
        'group_members': len(group_members),
        'fires_observed': len(fire_report),
        'fires_with_8_execs': fires_with_8,
        'fire_report': fire_report,
        'fire_first_seen_s': first_seen,
        'status_counts_all': status_counts,
        'non_terminal': non_terminal,
        'per_node_order_monotonic': order_ok,
        'fires_sequential': seq_ok,
        'fires_sequential_detail': seq_detail,
        'schedule_delete_http': code_del,
        'total_execs': len(all_execs),
    }
    verdict = 'PASS' if (len(group_members) == 8 and fires_ok and len(fire_report) >= 2
                         and not non_terminal and order_ok and seq_ok) else 'FAIL'
    ST.record('S4_group_scheduling', verdict,
              'group=%d members, fires=%d (each 8 execs=%s), order_ok=%s' % (
                  len(group_members), len(fire_report), fires_ok, order_ok), details=details)
    write_json(RAW / 's4-group-schedule.json', details)
    return details


def scenario_5_jitter(hub):
    print('== S5: node jitter (kill 2 agents) ==', flush=True)
    victims = ['3', '6']
    others = [k for k in AGENTS if k not in victims]
    node_ids = {k: ag.node_id for k, ag in AGENTS.items()}
    kill_rec = {}
    t_kill = now()
    for k in victims:
        kill_rec[k] = AGENTS[k].kill()
    # wait offline
    offline = {}
    for k in victims:
        n, lat = wait_node_status(hub, node_ids[k], 'offline', timeout=150)
        offline[k] = {'status': (n or {}).get('status'), 'latency_s': lat}
    others_transitions = []
    for k in others:
        n = node_info(hub, node_ids[k]) or {}
        others_transitions.append({'agent': k, 'status': n.get('status'),
                                   'sync_status': n.get('sync_status')})
    others_ok = all(o['status'] == 'online' for o in others_transitions)

    # restart victims
    restart = {}
    for k in victims:
        ag = AGENTS[k]
        t_r0 = now()
        pid = ag.start(wait_s=0.2)
        note_pid(pid)
        n = wait_node_online(hub, ag.node_id, timeout=150)
        restart[k] = {'pid': pid, 'status': (n or {}).get('status'),
                      'sync_status': (n or {}).get('sync_status'),
                      'recovery_s': round(now() - t_r0, 2)}
    t_restart_done = now()

    # supplementary: heartbeat-timeout path. SIGSTOP one agent (process alive,
    # TCP open, no traffic) -> hub must mark offline via heartbeat timeout,
    # then SIGCONT -> agent reconnects and converges.
    timeout_probe = {}
    probe_key = '4'
    ag_p = AGENTS[probe_key]
    if ag_p.pid:
        os.kill(ag_p.pid, signal.SIGSTOP)
        t_s0 = now()
        n, lat = wait_node_status(hub, ag_p.node_id, 'offline', timeout=120)
        timeout_probe['stopped_offline'] = {'status': (n or {}).get('status'), 'latency_s': lat}
        os.kill(ag_p.pid, signal.SIGCONT)
        n2 = wait_node_online(hub, ag_p.node_id, timeout=120)
        timeout_probe['resumed'] = {'status': (n2 or {}).get('status'),
                                    'sync_status': (n2 or {}).get('sync_status'),
                                    'recovery_s': round(now() - t_s0, 2)}
        timeout_probe['probe_agent'] = probe_key
    timeout_ok = (timeout_probe.get('stopped_offline', {}).get('status') == 'offline'
                  and timeout_probe.get('resumed', {}).get('status') == 'online'
                  and timeout_probe.get('resumed', {}).get('sync_status') == 'synced')

    rev_before = hub_rev()
    code, _ = bump_script_revision(hub, ST.data['ids']['script'], 'rev-bump-s5-' + NONCE)
    rev_after = hub_rev()
    converge = wait_revision(hub, list(node_ids.values()), rev_after, timeout=120)
    conv_ok = all(v['ok'] for v in converge.values())
    reconnects = {k: AGENTS[k].connected_count() for k in victims}
    hub_log_hits = S.grep_log(HUB_LOG, [r'node marked offline', r'level=ERROR', r'panic:'])
    details = {
        'killed': {k: kill_rec[k] for k in victims},
        'offline_detection': offline,
        'others_state': others_transitions,
        'others_still_online': others_ok,
        'restarted': restart,
        'restart_wall_s': round(t_restart_done - t_kill, 1),
        'heartbeat_timeout_probe': timeout_probe,
        'heartbeat_timeout_probe_ok': timeout_ok,
        'revision': {'before': rev_before, 'after': rev_after,
                     'convergence': converge, 'ok': conv_ok},
        'agent_reconnect_counts': reconnects,
        'hub_log_hits': hub_log_hits,
    }
    det_ok = all(v['status'] == 'offline' and v['latency_s'] is not None for v in offline.values())
    rec_ok = all(v['status'] == 'online' and v['sync_status'] == 'synced' for v in restart.values())
    verdict = 'PASS' if (det_ok and rec_ok and others_ok and conv_ok and timeout_ok) else 'FAIL'
    ST.record('S5_node_jitter', verdict,
              'offline_detect=%s recovery=%s others_online=%s hb_timeout=%s rev_converge=%s' % (
                  det_ok, rec_ok, others_ok, timeout_ok, conv_ok), details=details)
    write_json(RAW / 's5-jitter.json', details)
    return details


def scenario_6_pressure(hub):
    print('== S6: concurrent pressure 8 nodes x 3 runs = 24 ==', flush=True)
    node_ids = [ag.node_id for ag in AGENTS.values()]
    code, task = hub.post('/api/tasks', {
        'name': P + 'task-pressure', 'type': 'script', 'script_id': ST.data['ids']['script'],
        'target': {'type': 'node', 'node_ids': node_ids},
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'param_values': {}, 'timeout': 180, 'retry': 0,
        'offline_policy': 'allow_offline', 'enabled': True})
    if code not in (200, 201):
        raise RuntimeError('task_create_failed_%s_%s' % (code, task))
    ST.data['ids']['task_pressure'] = task['id']
    ST.fixture('task', task['id'], task['name'])
    ST.save()

    side = SHARED / ('s6-%s.log' % NONCE)
    err0 = len(hub.errors)
    health = []
    stop = threading.Event()

    def health_sampler():
        while not stop.is_set():
            code_h, ms = http_get_ms(S.HUB_PORT, '/healthz')
            health.append({'t': round(now(), 3), 'code': code_h, 'ms': ms})
            stop.wait(0.5)

    ht = threading.Thread(target=health_sampler, daemon=True)
    ht.start()

    results = {}
    lock = threading.Lock()

    def one_run(idx):
        try:
            h2 = S.Hub()
            h2.login()
            r = run_task(h2, task['id'], {'run_id': 'p%d' % idx, 'side_file': str(side)})
            with lock:
                results[idx] = {'http': r['http'], 'ms': r['ms'],
                                'exec_ids': [e['id'] for e in r['execs']],
                                'count': len(r['execs'])}
        except Exception as exc:
            with lock:
                results[idx] = {'error': type(exc).__name__ + ':' + str(exc)[:160]}

    threads = [threading.Thread(target=one_run, args=(i,)) for i in (1, 2, 3)]
    t0 = now()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    dispatch_wall = round(now() - t0, 2)

    all_ids = []
    for idx in sorted(results):
        all_ids.extend(results[idx].get('exec_ids', []))
    dup_ids = len(all_ids) - len(set(all_ids))

    t1 = now()
    terminal = {}
    for eid in all_ids:
        ex = wait_exec(hub, eid, timeout=240)
        terminal[eid] = ex
    wall_terminal = round(now() - t1, 2)
    stop.set()
    ht.join(timeout=5)

    per_node = {}
    per_run = {}
    for eid, ex in terminal.items():
        if not ex:
            continue
        per_node.setdefault(ex.get('node_id'), []).append(eid)
    for idx, r in results.items():
        per_run[idx] = r.get('count')
    status_counts = {}
    bad = []
    for eid, ex in terminal.items():
        st = (ex or {}).get('status')
        status_counts[st] = status_counts.get(st, 0) + 1
        if st != 'SUCCESS' or (ex or {}).get('exit_code') != 0:
            bad.append({'exec_id': eid, 'status': st,
                        'exit_code': (ex or {}).get('exit_code')})
        out = (ex or {}).get('stdout', '')
        err = (ex or {}).get('stderr', '')
        run_id = None
        m = re.search(r'OUT-(p\d)-start', out)
        if m:
            run_id = m.group(1)
        if not (run_id and 'OUT-%s-done' % run_id in out and 'ERR-%s-line' % run_id in err):
            bad.append({'exec_id': eid, 'reason': 'stream_marker_mismatch',
                        'run_id': run_id})

    # side-file double-run detection
    try:
        lines = [l for l in side.read_text().splitlines() if l.strip()]
    except OSError:
        lines = []
    per_run_lines = {}
    for l in lines:
        rid = l.split()[0]
        per_run_lines[rid] = per_run_lines.get(rid, 0) + 1

    # agent-local execution counts
    agent_counts = {}
    for k, ag in AGENTS.items():
        rows = ag.executions(task_id=task['id'])
        ids = [r['id'] for r in rows if 'id' in r]
        agent_counts[k] = {'count': len(rows), 'distinct_ids': len(set(ids)),
                           'statuses': sorted({r.get('status') for r in rows if 'status' in r})}

    err_delta = len(hub.errors) - err0
    hub_log_hits = S.grep_log(HUB_LOG, [r'level=ERROR', r'panic:'])
    health_stats = stats_of([h['ms'] for h in health])
    health_errors = [h for h in health if h['code'] != 200]
    expected_24 = len(all_ids) == 24
    per_node_3 = len(per_node) == 8 and all(len(v) == 3 for v in per_node.values())
    details = {
        'dispatch': results, 'dispatch_wall_s': dispatch_wall,
        'total_exec_ids': len(all_ids), 'unique_exec_ids': len(set(all_ids)),
        'duplicate_exec_ids': dup_ids,
        'expected_24': expected_24,
        'per_node_counts': {k: len(v) for k, v in per_node.items()},
        'per_node_exactly_3': per_node_3,
        'per_run_dispatch_counts': per_run,
        'status_counts': status_counts,
        'bad_executions': bad,
        'wall_to_all_terminal_s': wall_terminal,
        'side_file_lines': len(lines), 'side_file_per_run': per_run_lines,
        'side_file_ok': len(lines) == 24 and all(v == 8 for v in per_run_lines.values()),
        'agent_local_counts': agent_counts,
        'hub_5xx_delta': err_delta,
        'hub_log_hits': hub_log_hits,
        'healthz_stats': health_stats,
        'healthz_non_200': health_errors,
    }
    verdict = 'PASS' if (expected_24 and not dup_ids and per_node_3 and not bad
                         and details['side_file_ok'] and err_delta == 0
                         and not health_errors
                         and all(v['count'] == 3 and v['distinct_ids'] == 3
                                 for v in agent_counts.values())) else 'FAIL'
    ST.record('S6_concurrent_pressure', verdict,
              '24 execs=%s, unique=%s, per_node_3=%s, sidefile=%s, 5xx=%s' % (
                  expected_24, not dup_ids, per_node_3, details['side_file_ok'], err_delta),
              details=details)
    write_json(RAW / 's6-pressure.json', details)
    return details


def scenario_7_api_latency(hub, baselines):
    print('== S7: API latency at scale (8 nodes) ==', flush=True)
    node_ids = [ag.node_id for ag in AGENTS.values()]
    code, lst = hub.get('/api/executions?limit=50')
    latest_exec = None
    if code == 200 and isinstance(lst, list) and lst:
        latest_exec = lst[0]['id']
    endpoints = {}
    endpoints['nodes_list'] = sample_endpoint(hub, 'nodes_list',
                                              lambda h: h.get('/api/nodes', retries=0), 30)
    endpoints['node_detail'] = sample_endpoint(hub, 'node_detail',
                                               lambda h: h.get('/api/nodes/' + h.quote(node_ids[0]), retries=0), 30)
    endpoints['executions_list'] = sample_endpoint(hub, 'executions_list',
                                                   lambda h: h.get('/api/executions?limit=50', retries=0), 30)
    if latest_exec:
        endpoints['execution_detail'] = sample_endpoint(
            hub, 'execution_detail',
            lambda h: h.get('/api/executions/' + h.quote(latest_exec), retries=0), 30)
    endpoints['healthz'] = sample_endpoint(
        hub, 'healthz', lambda h: (http_get_ms(S.HUB_PORT, '/healthz')[0], None), 30)
    endpoints['readyz'] = sample_endpoint(
        hub, 'readyz', lambda h: (http_get_ms(S.HUB_PORT, '/readyz')[0], None), 30)

    burst = burst_sample(hub, '/api/nodes', concurrency=10)
    code, lst2 = hub.get('/api/nodes')
    list_ok = code == 200 and isinstance(lst2, list) and len(lst2) == 8 \
        and all(n.get('status') == 'online' for n in lst2)

    comparisons = {}
    for name, cur in endpoints.items():
        base = (baselines.get('one_node') or {}).get(name) or (baselines.get('zero_node') or {}).get(name)
        if base and base.get('n'):
            comparisons[name] = {
                'baseline_p50_ms': base.get('p50_ms'), 'scale_p50_ms': cur.get('p50_ms'),
                'baseline_p95_ms': base.get('p95_ms'), 'scale_p95_ms': cur.get('p95_ms'),
                'p95_ratio': round(cur['p95_ms'] / base['p95_ms'], 2) if base.get('p95_ms') else None,
            }
    details = {
        'endpoints': endpoints, 'burst': burst, 'list_correct_8_online': list_ok,
        'comparisons': comparisons, 'baselines': baselines,
    }
    verdict = 'PASS' if (list_ok and not burst['errors']
                         and all(v.get('codes', {}).get('200') == v.get('n') for v in endpoints.values())) else 'FAIL'
    ST.record('S7_api_latency_at_scale', verdict,
              'list_correct=%s, burst_errors=%d' % (list_ok, len(burst['errors'])), details=details)
    write_json(RAW / 's7-api-latency.json', details)
    return details


def capture_baseline(hub, label, with_node_detail=None):
    base = {}
    base['nodes_list'] = sample_endpoint(hub, 'nodes_list', lambda h: h.get('/api/nodes', retries=0), 20)
    base['executions_list'] = sample_endpoint(hub, 'executions_list',
                                              lambda h: h.get('/api/executions?limit=50', retries=0), 20)
    if with_node_detail:
        base['node_detail'] = sample_endpoint(
            hub, 'node_detail', lambda h: h.get('/api/nodes/' + h.quote(with_node_detail), retries=0), 20)
    base['healthz'] = sample_endpoint(hub, 'healthz',
                                      lambda h: (http_get_ms(S.HUB_PORT, '/healthz')[0], None), 20)
    base['readyz'] = sample_endpoint(hub, 'readyz',
                                     lambda h: (http_get_ms(S.HUB_PORT, '/readyz')[0], None), 20)
    print('baseline %s captured' % label, flush=True)
    return base


# ------------------------------------------------------------------ cleanup
def cleanup_environment():
    print('== cleanup ==', flush=True)
    rec = {'agents': {}, 'hub': None}
    for k, ag in AGENTS.items():
        try:
            rec['agents'][k] = ag.stop(timeout=8) if ag.pid else {'stopped': True, 'note': 'not_running'}
        except Exception as exc:
            rec['agents'][k] = {'error': type(exc).__name__}
    if HUB and HUB.pid:
        rec['hub'] = HUB.stop(timeout=15)
    # safety: SIGKILL any recorded pid still alive
    forced = []
    for pid in RECORDED_PIDS:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            forced.append(pid)
        except OSError:
            pass
    rec['forced_kills'] = forced
    return rec


def leftover_processes():
    hits = []
    for entry in os.listdir('/proc'):
        if not entry.isdigit():
            continue
        try:
            cmdline = Path('/proc/%s/cmdline' % entry).read_bytes().decode('utf-8', 'replace')
        except OSError:
            continue
        if 'nodesteer-qa-20261018-scale' in cmdline:
            hits.append({'pid': int(entry), 'cmdline': cmdline.replace('\x00', ' ').strip()[:200]})
    return hits


def build_cleanup_oracle(cleanup_rec, started_utc):
    checks = []

    def add(name, ok, detail=None):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    # 1. all recorded pids dead
    alive = []
    for pid in RECORDED_PIDS:
        try:
            os.kill(pid, 0)
            alive.append(pid)
        except OSError:
            pass
    add('all_launched_pids_dead', not alive, {'alive_pids': alive,
                                              'recorded_pids': len(RECORDED_PIDS)})
    # 2. port free + not connectable
    free = S.wait_port_free(S.HUB_PORT, 5)
    add('hub_port_%d_free' % S.HUB_PORT, free)
    add('hub_port_%d_not_connectable' % S.HUB_PORT, not S.port_connectable(S.HUB_PORT))
    # 3. no leftover processes
    left = leftover_processes()
    add('no_leftover_scale_processes', not left, {'leftover': left})
    # 4. workdir removed
    subprocess.run(['rm', '-rf', str(S.WORK)], check=False)
    add('workdir_removed', not S.WORK.exists(), {'workdir': str(S.WORK)})
    # 5. build dir removed (binaries hashed into evidence first)
    subprocess.run(['rm', '-rf', str(S.BUILD)], check=False)
    add('build_dir_removed', not S.BUILD.exists(), {'build_dir': str(S.BUILD)})
    # 6. go tmp dir removed
    go_tmp = Path('/var/tmp/nodesteer-go-tmp')
    subprocess.run(['rm', '-rf', str(go_tmp)], check=False)
    add('go_tmp_removed', not go_tmp.exists())
    # 7. /tmp/opencode/p13 empty dir removed (could not be used: tmpfs user quota)
    p13 = Path('/tmp/opencode/p13')
    try:
        if p13.exists():
            subprocess.run(['rmdir', str(p13)], check=False)
    except OSError:
        pass
    add('tmp_p13_dir_gone', not p13.exists(), {'note': 'tmpfs quota prevented use'})
    # 8. listener scan
    try:
        ss = subprocess.run(['ss', '-ltn'], capture_output=True, text=True, timeout=10).stdout
        listeners = [l for l in ss.splitlines() if ':%d' % S.HUB_PORT in l]
    except Exception:
        listeners = []
    add('no_listener_on_hub_port', not listeners, {'listeners': listeners})
    # 9. repo write scope (informational): files in the repo modified since the
    #    run started, outside this run's own directory and .git. Concurrent
    #    sessions may also write, so this is recorded, not asserted.
    try:
        repo = RUN.parents[4]
        t0 = time.mktime(time.strptime(started_utc, '%Y-%m-%dT%H:%M:%SZ'))
        cmd = ['find', str(repo), '-newermt', '@%d' % int(t0 - 60), '-type', 'f',
               '-not', '-path', '*/.git/*',
               '-not', '-path', '*qa-resume-20261018-scale*',
               '-not', '-path', '*/node_modules/*',
               '-not', '-path', '*/.ruff_cache/*',
               '-not', '-path', '*/playwright-report/*',
               '-not', '-path', '*/.tanstack/*']
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        outside = [l for l in out.splitlines() if l.strip()]
    except Exception as exc:
        outside = ['scan_error:%s' % type(exc).__name__]
    add('repo_write_scope_info', True,
        {'modified_outside_run_dir_since_start': outside[:50], 'count': len(outside),
         'note': 'info-level; concurrent sessions may also write'})
    # 10. run artifacts present
    for art in ('scale-results.json', 'resource-usage.json'):
        add('artifact_%s_present' % art.replace('.', '_'), (RUN / art).is_file())
    oracle = {
        'batch': 'qa-resume-20261018-scale',
        'started_at_utc': started_utc,
        'finished_at_utc': S.now_utc(),
        'cleanup_actions': cleanup_rec,
        'checks': checks,
        'verdict': 'PASS' if all(c['ok'] for c in checks) else 'FAIL',
        'failed_checks': [c['name'] for c in checks if not c['ok']],
    }
    return oracle


# --------------------------------------------------------------------- main
def write_scale_results():
    sc = ST.data['scenarios']
    findings = read_json(RUN / 'findings.json', {'findings': []}) or {'findings': []}
    summary = {'total': len(sc), 'pass': 0, 'fail': 0, 'findings': len(findings.get('findings', []))}
    for v in sc.values():
        if v['verdict'] == 'PASS':
            summary['pass'] += 1
        else:
            summary['fail'] += 1
    out = {
        'batch': 'qa-resume-20261018-scale',
        'generated_at_utc': S.now_utc(),
        'environment': {
            'hub': ST.data['instances'].get('hub'),
            'agents': {k: v for k, v in ST.data['instances'].items() if k.startswith('agent-')},
            'agent_count': AGENT_COUNT,
            'loopback_only': True,
            'host': {'cpu_cores': os.cpu_count()},
        },
        'scenarios': sc,
        'summary': summary,
    }
    write_json(RUN / 'scale-results.json', out)
    return out


def main():
    global HUB, SAMPLER
    parser = argparse.ArgumentParser()
    parser.add_argument('--s2-duration', type=float, default=S2_DURATION)
    args = parser.parse_args()

    started = S.now_utc()
    t_start = now()
    watchdog_fired = {'v': False}

    def watchdog():
        time.sleep(WATCHDOG_S)
        watchdog_fired['v'] = True
        print('WATCHDOG: time limit reached, emergency cleanup', flush=True)
        try:
            rec = cleanup_environment()
            oracle = build_cleanup_oracle(rec, started)
            write_json(RUN / 'final-cleanup-oracle.json', oracle)
        except Exception as exc:
            print('watchdog cleanup error: %r' % exc, flush=True)
        os._exit(9)

    threading.Thread(target=watchdog, daemon=True).start()

    hub = None
    baselines = {}
    try:
        HUB = HubProc()
        hub = setup_environment()
        baselines['zero_node'] = capture_baseline(hub, 'zero_node')

        SAMPLER = S.ProcSampler(pids_snapshot, interval=0.5)
        SAMPLER.start()
        threading.Thread(target=load_sampler, daemon=True).start()

        s1 = enroll_agents(hub, AGENT_COUNT)
        first_node_id = AGENTS['1'].node_id
        baselines['one_node'] = capture_baseline(hub, 'one_node', with_node_detail=first_node_id)

        s2 = scenario_2_heartbeat(hub, duration=args.s2_duration)
        s3 = scenario_3_fanout(hub)
        s4 = scenario_4_group_schedule(hub)
        s5 = scenario_5_jitter(hub)
        s6 = scenario_6_pressure(hub)
        s7 = scenario_7_api_latency(hub, baselines)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        print('FATAL: %r\n%s' % (exc, tb), flush=True)
        ST.record('fatal_error', 'FAIL', '%s: %s' % (type(exc).__name__, str(exc)[:300]),
                  details={'traceback': tb})
    finally:
        LOAD_STOP.set()
        proc_usage = SAMPLER.stop() if SAMPLER else {}
        try:
            hub_metrics = {}
            if HUB_DATA.exists():
                import sqlite3
                conn = sqlite3.connect('file:%s?mode=ro' % (HUB_DATA / 'hub.db'), uri=True, timeout=5)
                try:
                    hub_metrics['revision'] = conn.execute(
                        'SELECT value FROM revision_sequence').fetchone()[0]
                    hub_metrics['nodes'] = conn.execute('SELECT COUNT(*) FROM nodes').fetchone()[0]
                    hub_metrics['executions'] = conn.execute(
                        'SELECT COUNT(*) FROM executions').fetchone()[0]
                    hub_metrics['exec_status'] = dict(conn.execute(
                        'SELECT status, COUNT(*) FROM executions GROUP BY status').fetchall())
                finally:
                    conn.close()
            usage = {
                'batch': 'qa-resume-20261018-scale',
                'generated_at_utc': S.now_utc(),
                'sampling_interval_s': 0.5,
                'processes': proc_usage,
                'host_load': {
                    'samples': len(LOAD_SAMPLES),
                    'peak_load1': max((s['load1'] for s in LOAD_SAMPLES), default=None),
                    'min_mem_available_kb': min((s['mem_available_kb'] for s in LOAD_SAMPLES), default=None),
                },
                'hub_db': hub_metrics,
                'raw_samples_file': 'evidence/raw/resource-samples.json',
            }
            write_json(RUN / 'resource-usage.json', usage)
            write_json(RAW / 'resource-samples.json', {
                'process_samples': SAMPLER.samples if SAMPLER else [],
                'load_samples': LOAD_SAMPLES})
        except Exception as exc:
            print('usage write error: %r' % exc, flush=True)

        # evidence: hub + agent logs (redacted)
        try:
            if HUB_LOG.exists():
                data = HUB_LOG.read_bytes()
                write_evidence_text('hub.log', data[-4 << 20:].decode('utf-8', 'replace'))
            for k, ag in AGENTS.items():
                tail = ag.log_tail(200000)
                if tail:
                    write_evidence_text('agent-%s.log' % k, tail)
        except Exception as exc:
            print('log evidence error: %r' % exc, flush=True)

        rec = cleanup_environment()
        results = write_scale_results()
        oracle = build_cleanup_oracle(rec, started)
        write_json(RUN / 'final-cleanup-oracle.json', oracle)
        print('cleanup oracle: %s failed=%s' % (oracle['verdict'], oracle['failed_checks']), flush=True)
        print('scale-results: %s' % json.dumps(results['summary']), flush=True)
        print('total wall: %.1fs' % (now() - t_start), flush=True)


if __name__ == '__main__':
    main()
