#!/usr/bin/env python3
"""qa-resume-20261018-soak: long-stability / robustness soak on a fully owned
local-loopback NodeSteer environment (hub 127.0.0.1:18091 + 2 owned agents
behind owned fault-injection TCP proxies).

Scenarios:
  S1 disconnect storm (20x ws kill)
  S2 disconnect during execution (SYS-35 semantics)
  S3 hub restart during execution
  S4 transfer interruption / resume (source ws, target ws, cancel, agent restart)
  S5 concurrent pressure (5 executions + 2 transfers)
  S6 agent restart recovery (execution + transfer, SIGKILL)

Only owned objects; nothing here ever contacts production or the repair fleet.
"""
import argparse
import json
import os
import random
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import soaklib as S  # noqa: E402

RUN = HERE
EV = RUN / 'evidence'
RAW = EV / 'raw'
WORK = S.WORK
STATE_F = RUN / 'soak-state.json'
TOOLS = EV / 'tools'

os.umask(0o077)
HUB_LOG = WORK / 'hub' / 'hub.log'
PROXIES = {
    'a': {'listen': S.PROXY_A_PORT, 'ctrl': S.CTRL_A_PORT, 'target': S.HUB_PORT},
    'http': {'listen': S.PROXY_HTTP_PORT, 'ctrl': S.CTRL_HTTP_PORT, 'target': S.HUB_PORT},
    'b': {'listen': S.PROXY_B_PORT, 'ctrl': S.CTRL_B_PORT, 'target': S.HUB_PORT},
}


def now():
    return time.time()


def now_utc():
    return S.now_utc()


def write_json(path, obj):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


class State:
    def __init__(self):
        self.data = read_json(STATE_F, None) or {
            'batch': 'qa-resume-20261018-soak',
            'nonce': secrets.token_hex(4),
            'scenarios': {},
            'fixtures': [],
            'instances': {},
            'started_at_utc': now_utc(),
        }

    def save(self):
        self.data['updated_at_utc'] = now_utc()
        write_json(STATE_F, self.data)

    @property
    def nonce(self):
        return self.data['nonce']

    def record(self, key, verdict, summary, details=None, evidence=None):
        self.data['scenarios'][key] = {
            'verdict': verdict, 'summary': summary,
            'details': details or {}, 'evidence': evidence or [],
            'at_utc': now_utc(),
        }
        self.save()
        print('[SCENARIO] %s -> %s :: %s' % (key, verdict, summary), flush=True)

    def fixture(self, kind, fid, name):
        self.data['fixtures'].append([kind, fid, name])
        self.save()


ST = State()
NONCE = ST.nonce
P = 'qa-soak-%s-' % NONCE


# --------------------------------------------------------------------- redact
def load_secrets():
    cred = read_json(WORK / 'credentials.json', {}) or {}
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


# ------------------------------------------------------------------- hub proc
class HubProc:
    def __init__(self):
        self.config = WORK / 'hub' / 'hub.yaml'
        self.log = HUB_LOG
        self.pid = None
        self.starts = []
        self.stops = []

    def start(self, wait=True, timeout=90):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        logf = open(self.log, 'ab', buffering=0)
        proc = subprocess.Popen([S.HUB_BIN, '--config', str(self.config)],
                                stdout=logf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        self.pid = proc.pid
        self._proc = proc
        self.starts.append({'pid': self.pid, 'at': now_utc()})
        if wait:
            ok_h = S.wait_http_ok(S.HUB_PORT, '/healthz', timeout)
            ok_r = S.wait_http_ok(S.HUB_PORT, '/readyz', timeout)
            return {'pid': self.pid, 'healthz': ok_h, 'readyz': ok_r}
        return {'pid': self.pid}

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

    def stop(self, timeout=20):
        rec = {'pid': self.pid}
        if not self.pid:
            return {'stopped': True, 'note': 'never_started'}
        proc = getattr(self, '_proc', None)
        t0 = now()
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
        rec['graceful'] = not left
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
            else:
                time.sleep(0.5)
                left = self.alive()
        rec['stopped'] = not left
        rec['stop_seconds'] = round(now() - t0, 2)
        self.stops.append(rec)
        self.pid = None
        return rec

    def restart(self):
        stop = self.stop()
        time.sleep(1.0)
        start = self.start()
        return {'stop': stop, 'start': start}

    def log_tail(self, n=200000):
        try:
            return redact_text(self.log.read_text(errors='replace')[-n:])
        except OSError:
            return ''


HUB = HubProc()
AGENTS = {}
CTLS = {}
PROXY_PROCS = {}


# ------------------------------------------------------------------- helpers
def hub_rev():
    """Current hub global revision, read from the owned hub.db."""
    import sqlite3
    db = WORK / 'hub' / 'data' / 'hub.db'
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % db, uri=True, timeout=5)
        try:
            row = conn.execute('SELECT value FROM revision_sequence').fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def node_info(hub, node_id):
    try:
        code, n = hub.get('/api/nodes/' + node_id)
        if code == 200 and isinstance(n, dict):
            return n
    except Exception:
        pass
    return None


def wait_node_online(hub, node_id, timeout=150, want_synced=True):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        n = node_info(hub, node_id)
        if n:
            last = n
            if n.get('status') == 'online' and (not want_synced or n.get('sync_status') == 'synced'):
                return n
        time.sleep(1.0)
    return last


def wait_exec(hub, exec_id, timeout=300):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        except Exception:
            time.sleep(2)
            continue
        if code == 200 and isinstance(ex, dict):
            last = ex
            if ex.get('status') in S.TERMINAL_EXEC:
                return ex
        time.sleep(1.5)
    return last


def wait_transfer(hub, tid, timeout=300):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            code, t = hub.get('/api/transfers/' + tid)
        except Exception:
            time.sleep(2)
            continue
        if code == 200 and isinstance(t, dict):
            last = t
            if t.get('status') in S.TERMINAL_XFER:
                return t
        time.sleep(1.5)
    return last


def exec_stdout(hub, exec_id):
    code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
    out = ex.get('stdout') or '' if isinstance(ex, dict) else ''
    return ex, out


def run_task(hub, task_id, params=None):
    code, run = hub.post('/api/tasks/%s/run' % task_id, {'params': params or {}})
    exec_id = None
    if isinstance(run, list) and run:
        exec_id = run[0].get('id')
    elif isinstance(run, dict):
        exec_id = run.get('id') or (run.get('execution') or {}).get('id')
    if not exec_id:
        raise RuntimeError('run_failed_http_%s_%s' % (code, str(run)[:200]))
    return exec_id, run


def non_terminal_execs(hub, node_id=None):
    path = '/api/executions'
    if node_id:
        path += '?node_id=' + hub.quote(node_id)
    code, lst = hub.get(path)
    rows = lst.get('executions', lst) if isinstance(lst, dict) else lst
    if not isinstance(rows, list):
        return None
    return [r for r in rows if r.get('status') not in S.TERMINAL_EXEC]


def bump_revision(hub, script, marker):
    code, sc = hub.put('/api/scripts/%s' % script['id'], dict(script, description=marker))
    return code, sc


# --------------------------------------------------------------------- setup
def setup_environment():
    print('== setup: isolated local environment ==', flush=True)
    for port in (S.HUB_PORT, S.PROXY_A_PORT, S.PROXY_HTTP_PORT, S.PROXY_B_PORT,
                 S.CTRL_A_PORT, S.CTRL_HTTP_PORT, S.CTRL_B_PORT):
        if not S.wait_port_free(port, 3):
            raise RuntimeError('port_busy_%d' % port)
    if WORK.exists():
        subprocess.run(['rm', '-rf', str(WORK)], check=False)
    for d in ('hub', 'hub/data', 'proxies', 'agent-A', 'agent-B'):
        (WORK / d).mkdir(parents=True, exist_ok=True)

    admin_pw = 'qa-soak-' + secrets.token_urlsafe(12)
    reg_token = 'qa-soak-reg-' + secrets.token_urlsafe(12)
    cred_path = WORK / 'credentials.json'
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
        'revision_check_sec: 2\n'
        'heartbeat_timeout: 30s\n'
    ) % (S.HUB_PORT, S.HUB_PORT, S.PROXY_HTTP_PORT, reg_token,
         WORK / 'hub' / 'data', WORK / 'hub' / 'data' / 'artifacts', S.HUB_PORT, admin_pw)
    fd = os.open(HUB.config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(hub_cfg)

    started = HUB.start()
    if not (started['healthz'] and started['readyz']):
        raise RuntimeError('hub_not_ready:%s' % started)
    ST.data['instances']['hub'] = {'pid': HUB.pid, 'port': S.HUB_PORT,
                                   'data_dir': str(WORK / 'hub' / 'data'),
                                   'binary_sha256': S.HUB_SHA}

    # proxies
    for key, cfg in PROXIES.items():
        log = WORK / 'proxies' / ('%s.log' % key)
        proc = subprocess.Popen(
            [sys.executable, str(TOOLS / 'proxy.py'), '--listen', str(cfg['listen']),
             '--target-port', str(cfg['target']), '--control', str(cfg['ctrl']),
             '--log', str(log)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True)
        PROXY_PROCS[key] = proc
        CTLS[key] = S.ProxyCtl(cfg['ctrl'])
    for key, ctl in CTLS.items():
        deadline = time.time() + 10
        ok = False
        while time.time() < deadline:
            try:
                ctl.stats()
                ok = True
                break
            except OSError:
                time.sleep(0.2)
        if not ok:
            raise RuntimeError('proxy_not_up_%s' % key)
    ST.data['instances']['proxies'] = {
        k: {'listen': v['listen'], 'control': v['ctrl'], 'pid': PROXY_PROCS[k].pid}
        for k, v in PROXIES.items()}
    ST.save()

    hub = S.Hub()
    hub.login()

    for key, ws_url, http_base, name, ip in (
            ('A', 'ws://127.0.0.1:%d' % S.PROXY_A_PORT, 'http://127.0.0.1:%d' % S.PROXY_HTTP_PORT,
             P + 'agent-a', '10.77.0.1'),
            ('B', 'ws://127.0.0.1:%d' % S.PROXY_B_PORT, 'http://127.0.0.1:%d' % S.PROXY_HTTP_PORT,
             P + 'agent-b', '10.77.0.2')):
        ag = S.AgentProc(key, ws_url, http_base, name, ip)
        code, enroll = hub.post('/api/nodes/enrollment',
                                {'node_name': name, 'node_ip': ip,
                                 'hub_address': 'http://127.0.0.1:%d' % S.HUB_PORT})
        if code != 200 or not enroll.get('node_id'):
            raise RuntimeError('enrollment_failed_%s_%s' % (code, str(enroll)[:200]))
        ag.node_id = enroll['node_id']
        ag.agent_id = enroll['agent_id']
        ag.write_config(reg_token)
        ag.start()
        AGENTS[key] = ag
        ST.data['instances']['agent-' + key] = {
            'pid': ag.pid, 'node_id': ag.node_id, 'agent_id': ag.agent_id,
            'node_name': name, 'ws_url': ws_url, 'data_dir': str(ag.data),
            'binary_sha256': S.AGENT_SHA}
        ST.fixture('node', ag.node_id, name)
        ST.save()

    nodes = {}
    for key, ag in AGENTS.items():
        n = wait_node_online(hub, ag.node_id, timeout=90)
        nodes[key] = n
        if not n or n.get('status') != 'online':
            raise RuntimeError('agent_%s_not_online:%s' % (key, n))
    print('setup complete: hub pid %s, agents %s/%s' % (
        HUB.pid, AGENTS['A'].pid, AGENTS['B'].pid), flush=True)

    # owned fixtures: scripts + tasks
    long_script = {
        'name': P + 'long-script', 'description': 'soak long script v1',
        'interpreter': 'shell', 'enabled': True, 'environment': {}, 'working_dir': '',
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'content': (
            'SID="$NODESTEER_PARAM_run_id"\n'
            'SIDE="$NODESTEER_PARAM_side_file"\n'
            'echo "PID $$" >> "$SIDE"\n'
            'echo "RUN-START $SID $(date +%s.%N)"\n'
            'i=0\n'
            'while [ $i -lt 15 ]; do\n'
            '  i=$((i+1))\n'
            '  ts=$(date +%s.%N)\n'
            '  echo "tick $i $SID $ts"\n'
            '  echo "tick $i $SID $ts" >> "$SIDE"\n'
            '  sleep 2\n'
            'done\n'
            'echo "SOAK-DONE $SID"\n')}
    code, script = hub.post('/api/scripts', long_script)
    if code not in (200, 201):
        raise RuntimeError('script_create_failed_%s' % code)
    ST.data['ids'] = {'script_long': script['id']}
    ST.fixture('script', script['id'], long_script['name'])

    short_script = dict(long_script, name=P + 'short-script', description='soak short script',
                        content=(
                            'SID="$NODESTEER_PARAM_run_id"\n'
                            'SIDE="$NODESTEER_PARAM_side_file"\n'
                            'echo "PID $$" >> "$SIDE"\n'
                            'echo "RUN-START $SID $(date +%s.%N)"\n'
                            'i=0\n'
                            'while [ $i -lt 4 ]; do\n'
                            '  i=$((i+1))\n'
                            '  ts=$(date +%s.%N)\n'
                            '  echo "tick $i $SID $ts"\n'
                            '  echo "tick $i $SID $ts" >> "$SIDE"\n'
                            '  sleep 2\n'
                            'done\n'
                            'echo "SOAK-DONE $SID"\n'))
    code, script_short = hub.post('/api/scripts', short_script)
    if code not in (200, 201):
        raise RuntimeError('script_short_create_failed_%s' % code)
    ST.data['ids']['script_short'] = script_short['id']
    ST.fixture('script', script_short['id'], short_script['name'])

    for key, sid, sname, targets in (
            ('task_long', script['id'], 'long', [AGENTS['A'].node_id]),
            ('task_long_b', script['id'], 'long-b', [AGENTS['B'].node_id]),
            ('task_short', script_short['id'], 'short', [AGENTS['A'].node_id])):
        task = {'name': P + 'task-' + sname, 'type': 'script', 'script_id': sid,
                'target': {'type': 'node', 'node_ids': targets},
                'parameters': long_script['parameters'], 'param_values': {},
                'timeout': 300, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
        code, t = hub.post('/api/tasks', task)
        if code not in (200, 201):
            raise RuntimeError('task_create_failed_%s_%s' % (key, code))
        ST.data['ids'][key] = t['id']
        ST.fixture('task', t['id'], task['name'])
    ST.save()
    return hub


# ----------------------------------------------------------------- scenario 1
def scenario_1(hub):
    print('== S1: disconnect storm ==', flush=True)
    node_a = AGENTS['A'].node_id
    samples = []
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            n = node_info(hub, node_a)
            if n:
                samples.append({'t': round(now(), 3), 'status': n.get('status'),
                                'sync': n.get('sync_status')})
            stop.wait(0.5)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    kills = []
    t0 = now()
    for i in range(20):
        st = CTLS['a'].stats()
        resp = CTLS['a'].kill()
        kills.append({'i': i, 'active_before': st.get('active'), 'at': round(now() - t0, 2)})
        time.sleep(random.uniform(0.5, 2.0))
    stop.set()
    th.join(timeout=5)
    storm_seconds = round(now() - t0, 1)

    # settle
    settle_t0 = now()
    node = wait_node_online(hub, node_a, timeout=180)
    settle_seconds = round(now() - settle_t0, 1)
    final_status = (node or {}).get('status')
    final_sync = (node or {}).get('sync_status')

    # revision bump + convergence
    rev_before = hub_rev()
    code2, sc = hub.get('/api/scripts/' + ST.data['ids']['script_long'])
    code, _ = bump_revision(hub, sc, 'rev-bump-s1-' + NONCE)
    rev_after = hub_rev()
    converge = {}
    for key, ag in AGENTS.items():
        deadline = time.time() + 90
        while time.time() < deadline:
            n = node_info(hub, ag.node_id)
            if n and n.get('sync_status') == 'synced' and n.get('global_revision') == rev_after:
                converge[key] = {'ok': True, 'global_revision': n.get('global_revision')}
                break
            time.sleep(1)
        else:
            n = node_info(hub, ag.node_id) or {}
            converge[key] = {'ok': False, 'global_revision': n.get('global_revision'),
                             'sync_status': n.get('sync_status')}

    # stuck-state / error checks
    stuck = non_terminal_execs(hub, node_a)
    log_scan = S.grep_log(HUB_LOG, [r'panic:', r'goroutine \d+ \[', r'level=ERROR'])
    transitions = 0
    prev = None
    for s in samples:
        if prev is not None and s['status'] != prev:
            transitions += 1
        prev = s['status']
    off_seen = sum(1 for s in samples if s['status'] == 'offline')
    on_seen = sum(1 for s in samples if s['status'] == 'online')

    detail = {
        'storm_seconds': storm_seconds, 'kills': kills,
        'kills_with_active_conn': sum(1 for k in kills if (k.get('active_before') or 0) > 0),
        'node_status_samples': len(samples), 'status_transitions': transitions,
        'samples_offline': off_seen, 'samples_online': on_seen,
        'settle_seconds': settle_seconds, 'final_status': final_status, 'final_sync': final_sync,
        'revision': {'before': rev_before, 'after': rev_after, 'convergence': converge},
        'non_terminal_executions': stuck, 'hub_log_scan': log_scan,
        'hub_client_5xx': hub.errors[-10:], 'hub_alive': HUB.alive(),
        'sample_series': samples[::4][:120],
    }
    write_json(EV / 's1-disconnect-storm.json', detail)
    write_evidence_text('s1-hub-log-tail.txt', HUB.log_tail(40000))
    ok = (final_status == 'online' and final_sync == 'synced'
          and transitions >= 3 and off_seen >= 2
          and all(v.get('ok') for v in converge.values())
          and not stuck
          and log_scan.get('panic:') == 0
          and log_scan.get('goroutine \\d+ \\[') == 0)
    ST.record('S1-disconnect-storm', 'PASS' if ok else 'FAIL',
              '20 次 WS kill（%.1fs）：状态收敛 %s/%s，翻转 %d 次，revision %s→%s 双方收敛，无卡死执行，无 panic'
              % (storm_seconds, final_status, final_sync, transitions, rev_before, rev_after)
              if ok else '异常：final=%s/%s transitions=%d converge=%s stuck=%s panic=%s'
              % (final_status, final_sync, transitions, converge, stuck, log_scan.get('panic:')),
              detail, ['evidence/s1-disconnect-storm.json', 'evidence/raw/s1-hub-log-tail.txt'])
    return ok


# ----------------------------------------------------------------- scenario 2
def scenario_2(hub):
    print('== S2: execution survives disconnect (SYS-35) ==', flush=True)
    node_a = AGENTS['A'].node_id
    ag = AGENTS['A']
    run_id = 's2-' + NONCE
    side = ag.share / 's2-side.log'
    if side.exists():
        side.unlink()
    exec_id, _ = run_task(hub, ST.data['ids']['task_long'],
                          {'run_id': run_id, 'side_file': str(side)})
    ST.data['ids']['s2_exec'] = exec_id
    ST.save()
    # wait until running + first ticks
    deadline = time.time() + 30
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        if isinstance(ex, dict) and (ex.get('status') == 'RUNNING' or (ex.get('start_time') or '')):
            break
        time.sleep(0.5)
    time.sleep(3.0)
    # inject: kill ws + refuse reconnect
    CTLS['a'].kill()
    CTLS['a'].drop(True)
    outage_start = now()
    probes = []
    for _ in range(6):
        time.sleep(2.0)
        probes.append({'t': round(now() - outage_start, 1),
                       'side_bytes': side.stat().st_size if side.exists() else None,
                       'agent_exec': (ag.executions() or [{}])[-1]})
    CTLS['a'].drop(False)
    ex_final = wait_exec(hub, exec_id, timeout=180)
    ex_row, stdout = exec_stdout(hub, exec_id)
    # wait node back online + journal synced
    node = wait_node_online(hub, node_a, timeout=90)
    deadline = time.time() + 60
    journal = None
    while time.time() < deadline:
        rows = [r for r in ag.executions() if r.get('id') == exec_id]
        if rows and rows[0].get('synced') == 1 and rows[0].get('status') in S.TERMINAL_EXEC:
            journal = rows[0]
            break
        time.sleep(1)
    side_text = side.read_text() if side.exists() else ''
    side_ticks = [l for l in side_text.splitlines() if l.startswith('tick ')]
    side_ts = []
    for l in side_ticks:
        try:
            side_ts.append(float(l.split()[-1]))
        except ValueError:
            pass
    ticks_during_outage = [t for t in side_ts if outage_start <= t <= outage_start + 12.0]
    stdout_lines = stdout.splitlines()
    detail = {
        'exec_id': exec_id, 'run_id': run_id,
        'final_status': (ex_final or {}).get('status'),
        'exit_code': (ex_final or {}).get('exit_code'),
        'stdout_len': len(stdout),
        'stdout_tick_lines': len([l for l in stdout_lines if l.startswith('tick ')]),
        'stdout_run_starts': len([l for l in stdout_lines if l.startswith('RUN-START ')]),
        'stdout_done': ('SOAK-DONE %s' % run_id) in stdout,
        'stdout_head': stdout[:160], 'stdout_tail': stdout[-160:],
        'stderr': (ex_row or {}).get('stderr', '')[:200],
        'outage_probes': probes,
        'side_tick_count': len(side_ticks),
        'side_ticks_during_outage': len(ticks_during_outage),
        'journal_row': journal,
        'node_after': {'status': (node or {}).get('status'), 'sync': (node or {}).get('sync_status')},
        'log_chunk_count': None,
    }
    try:
        code, chunks = hub.get('/api/executions/%s/logs' % hub.quote(exec_id))
        detail['log_chunk_count'] = len(chunks) if isinstance(chunks, list) else chunks
    except Exception as exc:
        detail['log_chunk_count'] = 'err:' + type(exc).__name__
    write_json(EV / 's2-exec-disconnect.json', detail)
    write_evidence_text('s2-agent-log-tail.txt', ag.log_tail(40000))
    ok = (detail['final_status'] == 'SUCCESS' and detail['exit_code'] == 0
          and detail['stdout_tick_lines'] == 15 and detail['stdout_run_starts'] == 1
          and detail['stdout_done']
          and detail['side_tick_count'] == 15 and detail['side_ticks_during_outage'] >= 3
          and journal is not None
          and detail['node_after']['status'] == 'online')
    ST.record('S2-exec-disconnect', 'PASS' if ok else 'FAIL',
              '执行期间 WS 断开 12s：Agent 侧继续跑完（断连窗口内 %d 个 tick 落盘），重连后上报 SUCCESS，'
              'Hub 双流日志完整（15 tick + SOAK-DONE），journal synced' % detail['side_ticks_during_outage']
              if ok else '异常：%s' % {k: detail[k] for k in
                                      ('final_status', 'exit_code', 'stdout_tick_lines', 'stdout_done',
                                       'side_tick_count', 'side_ticks_during_outage', 'journal_row')},
              detail, ['evidence/s2-exec-disconnect.json', 'evidence/raw/s2-agent-log-tail.txt'])
    return ok


# ----------------------------------------------------------------- scenario 3
def scenario_3(hub):
    print('== S3: hub restart during execution ==', flush=True)
    node_a = AGENTS['A'].node_id
    ag = AGENTS['A']
    run_id = 's3-' + NONCE
    side = ag.share / 's3-side.log'
    if side.exists():
        side.unlink()
    task_id = ST.data['ids']['task_long']

    def task_exec_ids():
        code, lst = hub.get('/api/executions?task_id=' + task_id)
        rows = lst.get('executions', lst) if isinstance(lst, dict) else lst
        return {r.get('id') for r in (rows or []) if isinstance(r, dict)}

    before_ids = task_exec_ids()
    exec_id, _ = run_task(hub, task_id, {'run_id': run_id, 'side_file': str(side)})
    ST.data['ids']['s3_exec'] = exec_id
    ST.save()
    time.sleep(6.0)   # running, ~3 ticks done
    hub_stop_t = now()
    stop = HUB.stop()
    time.sleep(2.0)
    start = HUB.start()
    hub_up_t = now()
    hub_down_for = round(hub_up_t - hub_stop_t, 1)
    ST.data['instances']['hub'] = {'pid': HUB.pid, 'port': S.HUB_PORT, 'restarted': True}
    ST.save()
    # sessions live in hub memory: re-login after restart (new token)
    relogin = hub.login()
    ex_final = wait_exec(hub, exec_id, timeout=240)
    ex_row, stdout = exec_stdout(hub, exec_id)
    node = wait_node_online(hub, node_a, timeout=120)
    after_ids = task_exec_ids()
    new_ids = after_ids - before_ids
    agent_rows = [r for r in ag.executions() if r.get('id') == exec_id]
    side_text = side.read_text() if side.exists() else ''
    stdout_lines = stdout.splitlines()
    ticks = [l for l in side_text.splitlines() if l.startswith('tick ')]
    rev = hub_rev()
    log_scan = S.grep_log(HUB_LOG, [r'panic:', r'goroutine \d+ \['])
    detail = {
        'exec_id': exec_id, 'hub_down_seconds': hub_down_for,
        'hub_stop': stop, 'hub_start': start, 'relogin_ok': bool(relogin),
        'final_status': (ex_final or {}).get('status'), 'exit_code': (ex_final or {}).get('exit_code'),
        'stdout_tick_lines': len([l for l in stdout_lines if l.startswith('tick ')]),
        'stdout_run_starts': len([l for l in stdout_lines if l.startswith('RUN-START ')]),
        'stdout_done': ('SOAK-DONE %s' % run_id) in stdout,
        'side_ticks': len(ticks),
        'new_exec_ids_for_task': sorted(new_ids),
        'exec_rows_new_count': len(new_ids), 'agent_journal_rows': len(agent_rows),
        'node_after': {'status': (node or {}).get('status'), 'sync': (node or {}).get('sync_status'),
                       'global_revision': (node or {}).get('global_revision')},
        'hub_revision_after_restart': rev,
        'hub_log_scan': log_scan, 'hub_client_5xx': hub.errors[-10:],
    }
    write_json(EV / 's3-hub-restart.json', detail)
    write_evidence_text('s3-hub-log-tail.txt', HUB.log_tail(40000))
    ok = (detail['final_status'] == 'SUCCESS' and detail['exit_code'] == 0
          and detail['stdout_tick_lines'] == 15 and detail['stdout_run_starts'] == 1
          and detail['stdout_done']
          and detail['side_ticks'] == 15
          and new_ids == {exec_id} and len(agent_rows) == 1
          and detail['node_after']['status'] == 'online'
          and detail['node_after']['sync'] == 'synced'
          and log_scan.get('panic:') == 0)
    ST.record('S3-hub-restart', 'PASS' if ok else 'FAIL',
              '执行中重启 Hub（停机 %ss）：执行在 Agent 侧跑完，重连后 SUCCESS 全量日志；无重复执行（新执行 id 恰 1 条）；'
              '节点 online/synced，revision=%s' % (hub_down_for, rev)
              if ok else '异常：%s' % {k: detail[k] for k in
                                      ('final_status', 'stdout_tick_lines', 'stdout_done',
                                       'side_ticks', 'exec_rows_new_count', 'agent_journal_rows', 'node_after')},
              detail, ['evidence/s3-hub-restart.json', 'evidence/raw/s3-hub-log-tail.txt'])
    return ok


# ----------------------------------------------------------- transfer helpers
def make_file(ag, name, size_mb):
    p = ag.share / name
    p.parent.mkdir(parents=True, exist_ok=True)
    block = os.urandom(1 << 20)
    with open(p, 'wb') as f:
        for _ in range(size_mb):
            f.write(block)
    return p, S.sha256_file(p)


def create_transfer(hub, src_node, src_path, dst_node, dst_path):
    code, tr = hub.post('/api/transfers', {
        'source_node_id': src_node, 'source_path': str(src_path),
        'targets': [{'node_id': dst_node, 'destination_path': str(dst_path)}]})
    if code != 200 or not isinstance(tr, dict) or not tr.get('id'):
        raise RuntimeError('transfer_create_failed_%s_%s' % (code, str(tr)[:200]))
    return tr


def part_path(tid):
    return WORK / 'hub' / 'data' / 'file-transfers' / (tid + '.part')


def blob_path(tid):
    return WORK / 'hub' / 'data' / 'file-transfers' / (tid + '.blob')


def transfer_target_status(t):
    targets = t.get('targets') or []
    return [x.get('status') for x in targets]


# ----------------------------------------------------------------- scenario 4
def scenario_4(hub):
    print('== S4: transfer interruption / resume ==', flush=True)
    node_a, node_b = AGENTS['A'].node_id, AGENTS['B'].node_id
    ag_a, ag_b = AGENTS['A'], AGENTS['B']
    detail = {}
    checks = {}

    # ---------- 4a: source ws interrupted mid-upload, drop http to exhaust retries
    src, src_sha = make_file(ag_a, 's4a-src.bin', 50)
    dst = ag_b.share / 'incoming' / 's4a-dst.bin'
    if dst.exists():
        dst.unlink()
    CTLS['http'].throttle(6_000_000)
    tr = create_transfer(hub, node_a, src, node_b, dst)
    tid = tr['id']
    ST.fixture('transfer', tid, 's4a source-resume')
    time.sleep(2.5)
    part_before = part_path(tid).stat().st_size if part_path(tid).exists() else 0
    status_before = (hub.get('/api/transfers/' + tid)[1] or {}).get('status')
    CTLS['a'].kill()
    CTLS['a'].drop(True)
    CTLS['http'].kill()
    CTLS['http'].drop(True)
    time.sleep(12.0)
    part_mid = part_path(tid).stat().st_size if part_path(tid).exists() else 0
    status_mid = (hub.get('/api/transfers/' + tid)[1] or {}).get('status')
    CTLS['http'].drop(False)
    time.sleep(1.0)
    CTLS['a'].drop(False)
    t_resume = now()
    final = wait_transfer(hub, tid, timeout=240)
    dst_sha = S.sha256_file(dst) if dst.exists() else None
    resume_seconds = round(now() - t_resume, 1)
    detail['4a_source_ws_resume'] = {
        'transfer_id': tid, 'src_size': src.stat().st_size, 'src_sha256': src_sha,
        'part_before_kill': part_before, 'part_during_outage': part_mid,
        'status_before_kill': status_before, 'status_during_outage': status_mid,
        'final_status': (final or {}).get('status'), 'final_error': (final or {}).get('error'),
        'final_sha256': (final or {}).get('sha256'), 'dst_sha256': dst_sha,
        'resume_seconds_after_restore': resume_seconds,
        'target_status': transfer_target_status(final or {}),
    }
    checks['4a'] = (detail['4a_source_ws_resume']['final_status'] == 'SUCCESS'
                    and dst_sha == src_sha
                    and part_mid > 0 and part_mid < src.stat().st_size)
    write_json(EV / 's4a-source-resume.json', detail['4a_source_ws_resume'])

    # ---------- 4b: target ws interrupted mid-delivery
    src2, src2_sha = make_file(ag_a, 's4b-src.bin', 50)
    dst2 = ag_b.share / 'incoming' / 's4b-dst.bin'
    if dst2.exists():
        dst2.unlink()
    tr2 = create_transfer(hub, node_a, src2, node_b, dst2)
    tid2 = tr2['id']
    ST.fixture('transfer', tid2, 's4b target-resume')
    deadline = time.time() + 120
    status2 = None
    while time.time() < deadline:
        status2 = (hub.get('/api/transfers/' + tid2)[1] or {}).get('status')
        if status2 in ('DELIVERING', 'SUCCESS', 'FAILED'):
            break
        time.sleep(0.5)
    time.sleep(2.0)  # delivery in flight
    CTLS['b'].kill()
    CTLS['b'].drop(True)
    CTLS['http'].kill()   # kill B's in-flight download; ws down so result is not reported
    time.sleep(8.0)
    mid2 = hub.get('/api/transfers/' + tid2)[1] or {}
    CTLS['b'].drop(False)
    final2 = wait_transfer(hub, tid2, timeout=240)
    dst2_sha = S.sha256_file(dst2) if dst2.exists() else None
    detail['4b_target_ws_resume'] = {
        'transfer_id': tid2, 'src_sha256': src2_sha, 'status_at_kill': status2,
        'status_during_outage': mid2.get('status'), 'target_status_during_outage': transfer_target_status(mid2),
        'final_status': (final2 or {}).get('status'), 'final_error': (final2 or {}).get('error'),
        'dst_sha256': dst2_sha,
        'node_b_status_after': (wait_node_online(hub, node_b, timeout=90) or {}).get('status'),
    }
    checks['4b'] = (detail['4b_target_ws_resume']['final_status'] == 'SUCCESS'
                    and dst2_sha == src2_sha
                    and detail['4b_target_ws_resume']['node_b_status_after'] == 'online')
    write_json(EV / 's4b-target-resume.json', detail['4b_target_ws_resume'])

    # ---------- 4c: cancel mid-flight -> CANCELED terminal, files cleaned
    src3, _ = make_file(ag_a, 's4c-src.bin', 50)
    dst3 = ag_b.share / 'incoming' / 's4c-dst.bin'
    if dst3.exists():
        dst3.unlink()
    tr3 = create_transfer(hub, node_a, src3, node_b, dst3)
    tid3 = tr3['id']
    ST.fixture('transfer', tid3, 's4c cancel')
    time.sleep(3.0)
    ctl_stats_before = CTLS['http'].stats()
    code, cancel = hub.post('/api/transfers/%s/cancel' % tid3, {})
    t_cancel = now()
    final3 = wait_transfer(hub, tid3, timeout=60)
    deadline = time.time() + 20
    part_gone = blob_gone = False
    while time.time() < deadline:
        part_gone = not part_path(tid3).exists()
        blob_gone = not blob_path(tid3).exists()
        if part_gone and blob_gone:
            break
        time.sleep(0.5)
    time.sleep(5.0)
    still3 = hub.get('/api/transfers/' + tid3)[1] or {}
    # agent-side cancellation: active upload conn should be closed shortly after cancel
    conns_after = CTLS['http'].stats()
    detail['4c_cancel'] = {
        'transfer_id': tid3, 'cancel_http': code, 'cancel_latency_s': round(now() - t_cancel, 2),
        'final_status': (final3 or {}).get('status'), 'target_status': transfer_target_status(final3 or {}),
        'status_5s_later': still3.get('status'),
        'part_removed': part_gone, 'blob_removed': blob_gone,
        'dest_exists': dst3.exists(),
        'http_active_before_cancel': ctl_stats_before.get('active'),
        'http_active_after_cancel': conns_after.get('active'),
        'http_killed_total': conns_after.get('stats', {}).get('killed'),
    }
    checks['4c'] = (detail['4c_cancel']['final_status'] == 'CANCELED'
                    and all(s == 'CANCELED' for s in detail['4c_cancel']['target_status'])
                    and detail['4c_cancel']['status_5s_later'] == 'CANCELED'
                    and part_gone and blob_gone and not dst3.exists())
    write_json(EV / 's4c-cancel.json', detail['4c_cancel'])

    # ---------- 4d: agent process restart (SIGTERM) mid-upload
    src4, src4_sha = make_file(ag_a, 's4d-src.bin', 50)
    dst4 = ag_b.share / 'incoming' / 's4d-dst.bin'
    if dst4.exists():
        dst4.unlink()
    tr4 = create_transfer(hub, node_a, src4, node_b, dst4)
    tid4 = tr4['id']
    ST.fixture('transfer', tid4, 's4d agent-restart')
    time.sleep(2.5)
    part4_before = part_path(tid4).stat().st_size if part_path(tid4).exists() else 0
    stop_rec = ag_a.stop()
    time.sleep(0.5)
    ag_a.start()
    ST.data['instances']['agent-A']['pid'] = ag_a.pid
    ST.save()
    final4 = wait_transfer(hub, tid4, timeout=240)
    dst4_sha = S.sha256_file(dst4) if dst4.exists() else None
    detail['4d_agent_restart'] = {
        'transfer_id': tid4, 'src_sha256': src4_sha, 'part_before_restart': part4_before,
        'agent_stop': stop_rec, 'agent_new_pid': ag_a.pid,
        'final_status': (final4 or {}).get('status'), 'final_error': (final4 or {}).get('error'),
        'dst_sha256': dst4_sha,
        'node_after': (wait_node_online(hub, node_a, timeout=120) or {}).get('status'),
    }
    checks['4d'] = (detail['4d_agent_restart']['final_status'] == 'SUCCESS'
                    and dst4_sha == src4_sha)
    write_json(EV / 's4d-agent-restart.json', detail['4d_agent_restart'])
    CTLS['http'].throttle(0)

    # ---------- 4e: source ws killed while upload stays in flight (no http drop)
    src5, src5_sha = make_file(ag_a, 's4e-src.bin', 50)
    dst5 = ag_b.share / 'incoming' / 's4e-dst.bin'
    if dst5.exists():
        dst5.unlink()
    tr5 = create_transfer(hub, node_a, src5, node_b, dst5)
    tid5 = tr5['id']
    ST.fixture('transfer', tid5, 's4e ws-drop-in-flight')
    time.sleep(2.0)
    part5_before = part_path(tid5).stat().st_size if part_path(tid5).exists() else 0
    CTLS['a'].kill()
    # no drop: agent reconnects in 2-4s while the http upload is still in flight
    time.sleep(14.0)
    mid5 = hub.get('/api/transfers/' + tid5)[1] or {}
    final5 = wait_transfer(hub, tid5, timeout=240)
    dst5_sha = S.sha256_file(dst5) if dst5.exists() else None
    detail['4e_ws_drop_in_flight'] = {
        'transfer_id': tid5, 'src_sha256': src5_sha, 'part_before_kill': part5_before,
        'status_after_reconnect': mid5.get('status'),
        'target_status_after_reconnect': transfer_target_status(mid5),
        'final_status': (final5 or {}).get('status'), 'final_error': (final5 or {}).get('error'),
        'final_sha256': (final5 or {}).get('sha256'), 'dst_sha256': dst5_sha,
        'target_status_final': transfer_target_status(final5 or {}),
    }
    checks['4e'] = (detail['4e_ws_drop_in_flight']['final_status'] == 'SUCCESS'
                    and dst5_sha == src5_sha)
    write_json(EV / 's4e-ws-drop-in-flight.json', detail['4e_ws_drop_in_flight'])

    ok = all(checks.values())
    ST.record('S4-transfer-interrupt', 'PASS' if ok else 'FAIL',
              '4a 源端 WS 中断+HTTP 拒绝 → ResumeNode 断点续传 SUCCESS（part %s→%s）；4b 目标端 WS 中断 → 重投 SUCCESS；'
              '4c 取消 → CANCELED 终态且 part/blob 清理；4d 传输中重启 Agent → SUCCESS；4e WS 中断但上传仍在飞 → 恢复。SHA 全部一致'
              % (detail['4a_source_ws_resume']['part_before_kill'], detail['4a_source_ws_resume']['part_during_outage'])
              if ok else '异常：%s' % checks,
              detail, ['evidence/s4a-source-resume.json', 'evidence/s4b-target-resume.json',
                       'evidence/s4c-cancel.json', 'evidence/s4d-agent-restart.json',
                       'evidence/s4e-ws-drop-in-flight.json'])
    return ok


# ----------------------------------------------------------------- scenario 5
def scenario_5(hub):
    print('== S5: concurrent pressure (5 exec + 2 transfer) ==', flush=True)
    node_a, node_b = AGENTS['A'].node_id, AGENTS['B'].node_id
    ag_a, ag_b = AGENTS['A'], AGENTS['B']
    sampler = S.ProcSampler(lambda: {'hub': HUB.pid, 'agent-A': ag_a.pid, 'agent-B': ag_b.pid})
    sampler.start()
    CTLS['http'].throttle(4_000_000)
    execs = []
    run_ids = []
    # 3 long on A, 2 long on B, plus 2 transfers
    for i in range(5):
        node = node_a if i < 3 else node_b
        task_id = ST.data['ids']['task_long'] if node == node_a else ST.data['ids']['task_long_b']
        run_id = 's5-%d-%s' % (i, NONCE)
        side = (ag_a if node == node_a else ag_b).share / ('s5-%d-side.log' % i)
        if side.exists():
            side.unlink()
        exec_id, _ = run_task(hub, task_id,
                              {'run_id': run_id, 'side_file': str(side)})
        execs.append({'exec_id': exec_id, 'node': node, 'run_id': run_id, 'side': side})
        run_ids.append(run_id)
        ST.data['ids']['s5_exec_%d' % i] = exec_id
    transfers = []
    for i, size in ((0, 40), (1, 30)):
        src, src_sha = make_file(ag_a, 's5-%d-src.bin' % i, size)
        dst = ag_b.share / 'incoming' / ('s5-%d-dst.bin' % i)
        if dst.exists():
            dst.unlink()
        tr = create_transfer(hub, node_a, src, node_b, dst)
        transfers.append({'tid': tr['id'], 'src': src, 'dst': dst, 'sha': src_sha})
        ST.fixture('transfer', tr['id'], 's5 transfer %d' % i)
    ST.save()
    t0 = now()
    results = {}
    deadline = time.time() + 300
    while time.time() < deadline:
        done = True
        for e in execs:
            if e['exec_id'] not in results:
                code, ex = hub.get('/api/executions/' + hub.quote(e['exec_id']))
                if isinstance(ex, dict) and ex.get('status') in S.TERMINAL_EXEC:
                    results[e['exec_id']] = ex
            if e['exec_id'] not in results:
                done = False
        for t in transfers:
            key = 'tr-' + t['tid']
            if key not in results:
                code, tr = hub.get('/api/transfers/' + t['tid'])
                if isinstance(tr, dict) and tr.get('status') in S.TERMINAL_XFER:
                    results[key] = tr
            if key not in results:
                done = False
        if done:
            break
        time.sleep(2.0)
    elapsed = round(now() - t0, 1)
    peaks = sampler.stop()
    CTLS['http'].throttle(0)
    exec_summary = []
    for e in execs:
        ex = results.get(e['exec_id']) or {}
        exec_summary.append({'exec_id': e['exec_id'], 'node': e['node'], 'run_id': e['run_id'],
                             'status': ex.get('status'), 'exit_code': ex.get('exit_code')})
    tr_summary = []
    for t in transfers:
        tr = results.get('tr-' + t['tid']) or {}
        dst_sha = S.sha256_file(t['dst']) if t['dst'].exists() else None
        tr_summary.append({'transfer_id': t['tid'], 'status': tr.get('status'),
                           'sha_match': dst_sha == t['sha'], 'dst_sha256': dst_sha,
                           'error': tr.get('error')})
    stuck = non_terminal_execs(hub)
    detail = {
        'elapsed_seconds': elapsed, 'executions': exec_summary, 'transfers': tr_summary,
        'peaks': peaks, 'non_terminal_executions_after': stuck,
        'hub_client_5xx': hub.errors[-10:], 'hub_alive': HUB.alive(),
        'log_scan': S.grep_log(HUB_LOG, [r'panic:', r'goroutine \d+ \[']),
    }
    write_json(EV / 's5-concurrency.json', detail)
    ok = (all(e['status'] == 'SUCCESS' for e in exec_summary)
          and all(t['status'] == 'SUCCESS' and t['sha_match'] for t in tr_summary)
          and not stuck
          and not [e for e in hub.errors if e.get('http', 0) >= 500]
          and detail['log_scan'].get('panic:') == 0)
    ST.record('S5-concurrency', 'PASS' if ok else 'FAIL',
              '5 执行 + 2 传输并发：%.1fs 全部终态 SUCCESS、SHA 一致、无 5xx/panic/卡死；'
              '峰值 RSS hub=%sKB agent-A=%sKB agent-B=%sKB' % (
                  elapsed, peaks.get('hub', {}).get('peak_rss_kb'),
                  peaks.get('agent-A', {}).get('peak_rss_kb'),
                  peaks.get('agent-B', {}).get('peak_rss_kb'))
              if ok else '异常：%s' % {'exec': exec_summary, 'tr': tr_summary, 'stuck': stuck,
                                      '5xx': [e for e in hub.errors if e.get('http', 0) >= 500]},
              detail, ['evidence/s5-concurrency.json'])
    return ok


# ----------------------------------------------------------------- scenario 6
def scenario_6(hub):
    print('== S6: agent restart recovery ==', flush=True)
    node_a, node_b = AGENTS['A'].node_id, AGENTS['B'].node_id
    ag_a, ag_b = AGENTS['A'], AGENTS['B']
    detail = {}
    checks = {}

    # ---------- 6a: SIGKILL agent mid-execution -> journal marks FAILED agent_restarted
    run_id = 's6a-' + NONCE
    side = ag_a.share / 's6a-side.log'
    if side.exists():
        side.unlink()
    exec_id, _ = run_task(hub, ST.data['ids']['task_long'],
                          {'run_id': run_id, 'side_file': str(side)})
    ST.data['ids']['s6a_exec'] = exec_id
    ST.save()
    time.sleep(6.0)
    kill_rec = ag_a.kill(signal.SIGKILL)
    time.sleep(0.5)
    ag_a.start()
    ST.data['instances']['agent-A']['pid'] = ag_a.pid
    ST.save()
    ex_final = wait_exec(hub, exec_id, timeout=180)
    ex_row, stdout = exec_stdout(hub, exec_id)
    side_text = side.read_text() if side.exists() else ''
    starts = [l for l in side_text.splitlines() if l.startswith('RUN-START')]
    ticks = [l for l in side_text.splitlines() if l.startswith('tick ')]
    journal = [r for r in ag_a.executions() if r.get('id') == exec_id]
    deadline = time.time() + 60
    while time.time() < deadline:
        journal = [r for r in ag_a.executions() if r.get('id') == exec_id]
        if journal and journal[0].get('synced') == 1:
            break
        time.sleep(1)
    # orphan observation: after SIGKILL the execution process group is not reaped;
    # wait until the orphan finishes (side file reaches 15 ticks) and re-verify the
    # hub-side terminal stays FAILED (no resurrection / duplicate execution).
    orphan_deadline = time.time() + 45
    ticks_later = len(ticks)
    while time.time() < orphan_deadline:
        ticks_later = len([l for l in (side.read_text() if side.exists() else '').splitlines()
                           if l.startswith('tick ')])
        if ticks_later >= 15:
            break
        time.sleep(2)
    time.sleep(2)
    ex_later = wait_exec(hub, exec_id, timeout=20)
    detail['6a_exec_restart'] = {
        'exec_id': exec_id, 'kill': kill_rec, 'agent_new_pid': ag_a.pid,
        'final_status': (ex_final or {}).get('status'),
        'block_reason': (ex_final or {}).get('block_reason'),
        'status_after_orphan_window': (ex_later or {}).get('status'),
        'stdout_tail': stdout[-120:], 'side_run_starts': len(starts), 'side_ticks': len(ticks),
        'side_ticks_after_orphan_window': ticks_later,
        'orphan_continued_after_agent_death': ticks_later > len(ticks),
        'journal_row': journal[0] if journal else None,
        'node_after': (wait_node_online(hub, node_a, timeout=120) or {}).get('status'),
    }
    checks['6a'] = ((ex_final or {}).get('status') == 'FAILED'
                    and 'agent_restarted' in ((ex_final or {}).get('block_reason') or '')
                    and (ex_later or {}).get('status') == 'FAILED'
                    and journal and journal[0].get('status') == 'FAILED'
                    and journal[0].get('synced') == 1)
    write_json(EV / 's6a-exec-restart.json', detail['6a_exec_restart'])

    # ---------- 6b: SIGKILL source agent mid-upload -> resume
    src, src_sha = make_file(ag_a, 's6b-src.bin', 50)
    dst = ag_b.share / 'incoming' / 's6b-dst.bin'
    if dst.exists():
        dst.unlink()
    CTLS['http'].throttle(6_000_000)
    tr = create_transfer(hub, node_a, src, node_b, dst)
    tid = tr['id']
    ST.fixture('transfer', tid, 's6b source sigkill')
    time.sleep(2.5)
    part_before = part_path(tid).stat().st_size if part_path(tid).exists() else 0
    kill_rec2 = ag_a.kill(signal.SIGKILL)
    time.sleep(0.5)
    ag_a.start()
    ST.data['instances']['agent-A']['pid'] = ag_a.pid
    ST.save()
    final = wait_transfer(hub, tid, timeout=240)
    dst_sha = S.sha256_file(dst) if dst.exists() else None
    detail['6b_transfer_sigkill'] = {
        'transfer_id': tid, 'src_sha256': src_sha, 'part_before_kill': part_before,
        'kill': kill_rec2, 'final_status': (final or {}).get('status'),
        'final_error': (final or {}).get('error'), 'dst_sha256': dst_sha,
        'node_after': (wait_node_online(hub, node_a, timeout=120) or {}).get('status'),
    }
    checks['6b'] = (detail['6b_transfer_sigkill']['final_status'] == 'SUCCESS'
                    and dst_sha == src_sha)
    write_json(EV / 's6b-transfer-sigkill.json', detail['6b_transfer_sigkill'])

    # ---------- 6c: SIGKILL target agent mid-delivery -> re-delivery
    src3, src3_sha = make_file(ag_a, 's6c-src.bin', 50)
    dst3 = ag_b.share / 'incoming' / 's6c-dst.bin'
    if dst3.exists():
        dst3.unlink()
    tr3 = create_transfer(hub, node_a, src3, node_b, dst3)
    tid3 = tr3['id']
    ST.fixture('transfer', tid3, 's6c target sigkill')
    deadline = time.time() + 120
    while time.time() < deadline:
        st = (hub.get('/api/transfers/' + tid3)[1] or {}).get('status')
        if st in ('DELIVERING', 'SUCCESS', 'FAILED'):
            break
        time.sleep(0.5)
    time.sleep(2.0)
    kill_rec3 = ag_b.kill(signal.SIGKILL)
    time.sleep(0.5)
    ag_b.start()
    ST.data['instances']['agent-B']['pid'] = ag_b.pid
    ST.save()
    final3 = wait_transfer(hub, tid3, timeout=240)
    dst3_sha = S.sha256_file(dst3) if dst3.exists() else None
    detail['6c_target_sigkill'] = {
        'transfer_id': tid3, 'src_sha256': src3_sha, 'kill': kill_rec3,
        'final_status': (final3 or {}).get('status'), 'final_error': (final3 or {}).get('error'),
        'dst_sha256': dst3_sha,
        'node_after': (wait_node_online(hub, node_b, timeout=120) or {}).get('status'),
    }
    checks['6c'] = (detail['6c_target_sigkill']['final_status'] == 'SUCCESS'
                    and dst3_sha == src3_sha)
    write_json(EV / 's6c-target-sigkill.json', detail['6c_target_sigkill'])
    CTLS['http'].throttle(0)

    ok = all(checks.values())
    ST.record('S6-agent-restart', 'PASS' if ok else 'FAIL',
              '6a SIGKILL 执行中 → FAILED(agent_restarted) 且 journal 同步、无重复副作用；'
              '6b SIGKILL 源端传输中 → 断点续传 SUCCESS；6c SIGKILL 目标端交付中 → 重投 SUCCESS，SHA 一致'
              if ok else '异常：%s' % checks,
              detail, ['evidence/s6a-exec-restart.json', 'evidence/s6b-transfer-sigkill.json',
                       'evidence/s6c-target-sigkill.json'])
    return ok


def kill_orphan_exec_groups():
    """Kill any orphaned execution process groups (SIGKILLed agent scenario).

    The runner uses Setpgid; script PIDs are recorded in the side files as
    'PID <n>' lines. Kill the recorded groups (TERM then KILL).
    """
    killed = []
    for key, ag in AGENTS.items():
        for side in sorted(ag.share.glob('*-side.log')):
            pids = []
            try:
                for line in side.read_text(errors='replace').splitlines():
                    if line.startswith('PID '):
                        try:
                            pids.append(int(line.split()[1]))
                        except (IndexError, ValueError):
                            pass
            except OSError:
                continue
            for pid in pids:
                try:
                    comm = open('/proc/%d/comm' % pid).read().strip()
                except OSError:
                    continue
                if comm not in ('sh', 'bash', 'dash'):
                    killed.append({'side': str(side), 'pgid': pid, 'skip': 'comm=%s' % comm})
                    continue
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(pid, sig)
                    except OSError:
                        break
                    time.sleep(0.2)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                killed.append({'side': str(side), 'pgid': pid})
    return killed


# ------------------------------------------------------------------- cleanup
def cleanup():
    print('== cleanup ==', flush=True)
    oracle = {'batch': 'qa-resume-20261018-soak', 'at_utc': now_utc(), 'steps': {}}
    for key, ag in AGENTS.items():
        if ag.pid:
            oracle['steps']['stop_agent_' + key] = ag.stop()
    oracle['steps']['orphan_exec_groups'] = kill_orphan_exec_groups()
    for key, proc in PROXY_PROCS.items():
        try:
            CTLS[key].stop()
        except Exception as exc:
            oracle['steps']['proxy_%s_stop_cmd' % key] = str(exc)
    time.sleep(1.0)
    for key, proc in PROXY_PROCS.items():
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        oracle['steps']['proxy_%s_exit' % key] = proc.returncode
    if HUB.pid:
        oracle['steps']['stop_hub'] = HUB.stop()
    time.sleep(1.0)

    # preserve logs (redacted) before removing the work dir
    preserved = {}
    for name, path in (('hub.log', WORK / 'hub' / 'hub.log'),
                       ('agent-A.log', WORK / 'agent-A' / 'agent.log'),
                       ('agent-B.log', WORK / 'agent-B' / 'agent.log'),
                       ('proxy-a.log', WORK / 'proxies' / 'a.log'),
                       ('proxy-http.log', WORK / 'proxies' / 'http.log'),
                       ('proxy-b.log', WORK / 'proxies' / 'b.log')):
        try:
            preserved[name] = write_evidence_text('raw-' + name, path.read_text(errors='replace')[-400000:])
        except OSError:
            preserved[name] = None
    oracle['preserved_logs'] = preserved

    # remove owned dir
    rm = subprocess.run(['rm', '-rf', str(WORK)], capture_output=True, text=True)
    oracle['steps']['rm_work'] = {'rc': rm.returncode, 'stderr': rm.stderr[-200:]}

    # verify ports free
    ports = {}
    for port in (S.HUB_PORT, S.PROXY_A_PORT, S.PROXY_HTTP_PORT, S.PROXY_B_PORT,
                 S.CTRL_A_PORT, S.CTRL_HTTP_PORT, S.CTRL_B_PORT):
        ports[str(port)] = S.wait_port_free(port, 2)
    oracle['ports_free'] = ports

    # verify no owned processes remain
    pg = subprocess.run(['pgrep', '-af',
                         'nodesteer-hub-static-final|nodesteer-agent-static|evidence/tools/proxy.py'],
                        capture_output=True, text=True)
    procs = [l for l in pg.stdout.splitlines() if l.strip()]
    procs = [l for l in procs if 'pgrep' not in l and 'run_soak.py' not in l]
    oracle['leftover_processes'] = procs
    oracle['work_dir_removed'] = not WORK.exists()
    oracle['pass'] = (all(ports.values()) and not procs and not WORK.exists()
                      and all(v.get('stopped', True) for k, v in oracle['steps'].items()
                              if k.startswith('stop_agent') or k == 'stop_hub'))
    write_json(RUN / 'final-cleanup-oracle.json', oracle)
    print('cleanup oracle: pass=%s' % oracle['pass'], flush=True)
    return oracle


# ---------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('phase', nargs='?', default='all')
    ap.add_argument('start', nargs='?', default='s1')
    args = ap.parse_args()
    phases = ['s1', 's2', 's3', 's4', 's5', 's6']
    if args.phase == 'cleanup':
        cleanup()
        return
    if args.phase == 'smoke':
        hub = None
        try:
            hub = setup_environment()
            run_id = 'smoke-' + NONCE
            side = AGENTS['A'].share / 'smoke-side.log'
            if side.exists():
                side.unlink()
            exec_id, _ = run_task(hub, ST.data['ids']['task_short'],
                                  {'run_id': run_id, 'side_file': str(side)})
            ex = wait_exec(hub, exec_id, timeout=120)
            ex_row, stdout = exec_stdout(hub, exec_id)
            print(json.dumps({'smoke_exec': exec_id, 'status': (ex or {}).get('status'),
                              'exit_code': (ex or {}).get('exit_code'),
                              'ticks': len([l for l in stdout.splitlines() if l.startswith('tick ')]),
                              'done': ('SOAK-DONE %s' % run_id) in stdout,
                              'node_a': (node_info(hub, AGENTS['A'].node_id) or {}).get('status'),
                              'node_b': (node_info(hub, AGENTS['B'].node_id) or {}).get('status')},
                             ensure_ascii=False), flush=True)
        finally:
            cleanup()
        return
    if args.phase == 'watch':
        hub = None
        try:
            hub = setup_environment()
            print('watching for intrinsic connection flapping (120s)...', flush=True)
            t0 = now()
            series = []
            flips = {'A': 0, 'B': 0}
            prev = {}
            while now() - t0 < 120:
                for key, ag in AGENTS.items():
                    n = node_info(hub, ag.node_id) or {}
                    st = n.get('status')
                    if prev.get(key) is not None and st != prev.get(key):
                        flips[key] += 1
                    prev[key] = st
                series.append({'t': round(now() - t0, 1),
                               'A': prev.get('A'), 'B': prev.get('B'),
                               'pa': CTLS['a'].stats().get('active'),
                               'pb': CTLS['b'].stats().get('active')})
                time.sleep(2)
            detail = {'flips': flips, 'series': series,
                      'proxy_a': CTLS['a'].stats(), 'proxy_b': CTLS['b'].stats(),
                      'hub_log_scan': S.grep_log(HUB_LOG, [r'panic:'])}
            write_json(EV / 'watch-flapping.json', detail)
            print(json.dumps({'flips': flips, 'a_stats': detail['proxy_a']['stats'],
                              'b_stats': detail['proxy_b']['stats']}, ensure_ascii=False), flush=True)
            write_evidence_text('watch-hub-log.txt', HUB.log_tail(60000))
            write_evidence_text('watch-agent-a-log.txt', AGENTS['A'].log_tail(60000))
            write_evidence_text('watch-agent-b-log.txt', AGENTS['B'].log_tail(60000))
        finally:
            # preserve logs before removing the work dir
            for name, path in (('hub.log', WORK / 'hub' / 'hub.log'),
                               ('agent-A.log', WORK / 'agent-A' / 'agent.log'),
                               ('agent-B.log', WORK / 'agent-B' / 'agent.log'),
                               ('proxy-a.log', WORK / 'proxies' / 'a.log'),
                               ('proxy-http.log', WORK / 'proxies' / 'http.log'),
                               ('proxy-b.log', WORK / 'proxies' / 'b.log')):
                try:
                    write_evidence_text('raw-' + name, path.read_text(errors='replace')[-200000:])
                except OSError:
                    pass
            cleanup()
        return
    if args.phase == 'repro':
        hub = None
        try:
            hub = setup_environment()
            node_a, node_b = AGENTS['A'].node_id, AGENTS['B'].node_id
            ag_a, ag_b = AGENTS['A'], AGENTS['B']
            src, src_sha = make_file(ag_a, 'repro-src.bin', 50)
            dst = ag_b.share / 'incoming' / 'repro-dst.bin'
            if dst.exists():
                dst.unlink()
            CTLS['http'].throttle(2_000_000)
            tr = create_transfer(hub, node_a, src, node_b, dst)
            tid = tr['id']
            # wait until upload is genuinely in flight
            deadline = time.time() + 40
            part0 = 0
            while time.time() < deadline:
                part0 = part_path(tid).stat().st_size if part_path(tid).exists() else 0
                if part0 > 5_000_000:
                    break
                time.sleep(0.3)
            kill_at = now()
            CTLS['a'].kill()          # ws drop only; http upload keeps flying
            timeline = []
            deadline = time.time() + 180
            while time.time() < deadline:
                t = hub.get('/api/transfers/' + tid)[1] or {}
                timeline.append({
                    'dt': round(now() - kill_at, 2), 'status': t.get('status'),
                    'error': (t.get('error') or '')[:120],
                    'targets': transfer_target_status(t),
                    'part': part_path(tid).stat().st_size if part_path(tid).exists() else 0,
                    'sha': (t.get('sha256') or '')[:12]})
                if t.get('status') in S.TERMINAL_XFER and now() - kill_at > 75:
                    break
                time.sleep(0.4)
            final = hub.get('/api/transfers/' + tid)[1] or {}
            dst_sha = S.sha256_file(dst) if dst.exists() else None
            statuses = [x['status'] for x in timeline]
            transitions = []
            prev = None
            for x in timeline:
                if x['status'] != prev:
                    transitions.append({'dt': x['dt'], 'status': x['status'], 'error': x['error'],
                                        'targets': x['targets'], 'part': x['part']})
                    prev = x['status']
            failed_idx = [i for i, s in enumerate(statuses) if s == 'FAILED']
            terminal_violation = any(any(s != 'FAILED' for s in statuses[i + 1:]) for i in failed_idx)
            part_samples = [{'dt': x['dt'], 'part': x['part'], 'status': x['status']}
                            for x in timeline[::4]]
            detail = {
                'transfer_id': tid, 'part_before_kill': part0, 'src_sha256': src_sha,
                'final_status': final.get('status'), 'final_error': final.get('error'),
                'dst_sha256': dst_sha, 'sha_match': dst_sha == src_sha,
                'status_transitions': transitions,
                'failed_then_nonfailed': terminal_violation,
                'timeline_len': len(timeline), 'part_samples': part_samples,
            }
            write_json(EV / 'repro-superseded-upload.json', detail)
            print(json.dumps(detail, ensure_ascii=False, indent=1), flush=True)
        finally:
            for name, path in (('hub.log', WORK / 'hub' / 'hub.log'),
                               ('agent-A.log', WORK / 'agent-A' / 'agent.log'),
                               ('agent-B.log', WORK / 'agent-B' / 'agent.log'),
                               ('proxy-a.log', WORK / 'proxies' / 'a.log'),
                               ('proxy-http.log', WORK / 'proxies' / 'http.log'),
                               ('proxy-b.log', WORK / 'proxies' / 'b.log')):
                try:
                    write_evidence_text('raw-repro-' + name, path.read_text(errors='replace')[-200000:])
                except OSError:
                    pass
            cleanup()
        return
    if args.phase == 'all':
        hub = None
        try:
            hub = setup_environment()
            start_idx = phases.index(args.start) if args.start in phases else 0
            for name in phases[start_idx:]:
                fn = {'s1': scenario_1, 's2': scenario_2, 's3': scenario_3,
                      's4': scenario_4, 's5': scenario_5, 's6': scenario_6}[name]
                try:
                    fn(hub)
                except Exception as exc:
                    import traceback
                    tb = traceback.format_exc()
                    ST.record('S%s' % name[1:], 'ERROR', 'scenario crashed: %s' % exc,
                              {'traceback': redact_text(tb)[-4000:]})
                ST.save()
        finally:
            cleanup()
    else:
        # targeted rerun of one scenario against an already-running env is not supported
        raise SystemExit('use "all" or "cleanup"')


if __name__ == '__main__':
    main()
