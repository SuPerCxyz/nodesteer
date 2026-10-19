#!/usr/bin/env python3
"""S7 verification: controlled API-latency comparison.

Same execution history, quiet state:
  A) 1 node online  + 48 executions history
  B) 8 nodes online + same 48 executions history
  C) 8 nodes online, sampled immediately after a 24-execution burst (tail effect)

Only owned local-loopback objects; destroyed at the end.
"""
import json
import os
import secrets
import shutil
import signal
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
RAW = RUN / 'evidence' / 'raw'
VERIFY = Path('/var/tmp/nodesteer-qa-20261018-scale/verify-s7')
os.umask(0o077)


def wait_online(hub, nid, timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        c, n = hub.get('/api/nodes/' + nid)
        last = n
        if n.get('status') == 'online' and n.get('sync_status') == 'synced':
            return n
        time.sleep(0.5)
    return last


def sample(hub, fn, n=30):
    ms = []
    codes = {}
    for _ in range(n):
        t0 = time.time()
        try:
            code, _ = fn()
        except Exception as exc:
            code = 'ERR:' + type(exc).__name__
        ms.append(round((time.time() - t0) * 1000.0, 2))
        codes[str(code)] = codes.get(str(code), 0) + 1
    vs = sorted(ms)

    def pct(p):
        return vs[min(len(vs) - 1, int(round(p / 100.0 * (len(vs) - 1))))]
    return {'n': n, 'min': vs[0], 'p50': pct(50), 'p95': pct(95), 'max': vs[-1],
            'mean': round(sum(vs) / len(vs), 2), 'codes': codes, 'raw_ms': ms}


def full_panel(hub, node_id, exec_id):
    def hz(path):
        def probe():
            t0 = time.time()
            try:
                with urllib.request.urlopen('http://127.0.0.1:%d%s' % (S.HUB_PORT, path), timeout=3) as r:
                    r.read()
                    return r.status, round((time.time() - t0) * 1000.0, 2)
            except Exception as exc:
                return type(exc).__name__, None
        return probe

    out = {}
    out['nodes_list'] = sample(hub, lambda: hub.get('/api/nodes', retries=0))
    out['node_detail'] = sample(hub, lambda: hub.get('/api/nodes/' + node_id, retries=0))
    out['executions_list'] = sample(hub, lambda: hub.get('/api/executions?limit=50', retries=0))
    out['execution_detail'] = sample(hub, lambda: hub.get('/api/executions/' + exec_id, retries=0))
    out['healthz'] = sample(hub, hz('/healthz'))
    out['readyz'] = sample(hub, hz('/readyz'))
    return out


def main():
    t0 = time.time()
    assert S.wait_port_free(S.HUB_PORT, 5), 'port busy'
    if VERIFY.exists():
        shutil.rmtree(VERIFY)
    (VERIFY / 'hub' / 'data').mkdir(parents=True)
    (VERIFY / 'shared').mkdir()
    pw = 'verify-' + secrets.token_urlsafe(8)
    reg = 'verify-reg-' + secrets.token_urlsafe(8)
    cred = VERIFY / 'credentials.json'
    fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'admin_username': 'admin', 'admin_password': pw,
                   'registration_token': reg}, f)
    S.WORK = VERIFY
    cfg = (
        'web_addr: "127.0.0.1:%d"\ngateway_addr: "127.0.0.1:%d"\n'
        'gateway_base_url: "http://127.0.0.1:%d"\nregistration_token: "%s"\n'
        'data_dir: "%s"\nartifact_dir: "%s"\nbase_url: "http://127.0.0.1:%d"\n'
        'admin_username: "admin"\nadmin_password: "%s"\nsession_ttl: 24h\n'
        'revision_check_sec: 5\nheartbeat_timeout: 20s\n'
    ) % (S.HUB_PORT, S.HUB_PORT, S.HUB_PORT, reg, VERIFY / 'hub' / 'data',
         VERIFY / 'hub' / 'data' / 'artifacts', S.HUB_PORT, pw)
    hubcfg = VERIFY / 'hub' / 'hub.yaml'
    fd = os.open(hubcfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(cfg)
    logf = open(VERIFY / 'hub' / 'hub.log', 'ab', buffering=0)
    hp = subprocess.Popen([S.HUB_BIN, '--config', str(hubcfg)], stdout=logf,
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          start_new_session=True)
    assert S.wait_http_ok(S.HUB_PORT, '/readyz', 60)
    hub = S.Hub()
    hub.login()
    hub.put('/api/settings', {'heartbeat_interval_sec': '2', 'revision_check_interval_sec': '5'})

    agents = {}
    node_ids = []
    # agent 1 first, so the baseline is measured with exactly 1 node
    for i in range(1, 9):
        name = 'verify-s7-agent-%d' % i
        ip = '10.97.0.%d' % i
        c, en = hub.post('/api/nodes/enrollment',
                         {'node_name': name, 'node_ip': ip,
                          'hub_address': 'http://127.0.0.1:%d' % S.HUB_PORT})
        assert c == 200, (c, en)
        ag = S.AgentProc(str(i), name, ip, data_root=VERIFY)
        ag.node_id = en['node_id']
        ag.agent_id = en['agent_id']
        ag.write_config(reg)
        if i == 1:
            ag.start()
            n = wait_online(hub, ag.node_id)
            assert n.get('status') == 'online', n
        agents[i] = ag
        node_ids.append(ag.node_id)
    n1 = node_ids[0]

    # script + task on the single node; generate 48 executions history
    c, script = hub.post('/api/scripts', {
        'name': 'verify-s7-script', 'description': 'v1', 'interpreter': 'shell',
        'enabled': True, 'environment': {}, 'working_dir': '',
        'parameters': [{'name': 'rid', 'type': 'string', 'required': True}],
        'content': 'echo "OUT-$NODESTEER_PARAM_rid"; echo "ERR-$NODESTEER_PARAM_rid" >&2\n'})
    assert c in (200, 201), (c, script)
    c, task = hub.post('/api/tasks', {
        'name': 'verify-s7-task', 'type': 'script', 'script_id': script['id'],
        'target': {'type': 'node', 'node_ids': [n1]},
        'parameters': [{'name': 'rid', 'type': 'string', 'required': True}],
        'param_values': {}, 'timeout': 60, 'retry': 0,
        'offline_policy': 'allow_offline', 'enabled': True})
    assert c in (200, 201), (c, task)

    def fire(n_runs, params_prefix):
        ids = []
        for i in range(n_runs):
            c, run = hub.post('/api/tasks/%s/run' % task['id'],
                              {'params': {'rid': '%s-%d' % (params_prefix, i)}})
            assert c == 200, (c, run)
            ids.extend(e['id'] for e in run)
        deadline = time.time() + 120
        while time.time() < deadline:
            c, lst = hub.get('/api/executions?task_id=%s&limit=200' % task['id'])
            rows = {e['id']: e for e in lst}
            if all(rows.get(x, {}).get('status') in S.TERMINAL_EXEC for x in ids):
                break
            time.sleep(0.5)
        return ids

    hist = fire(48, 'hist')
    time.sleep(2)
    c, lst = hub.get('/api/executions?limit=50')
    exec_id = lst[0]['id']
    print('history executions:', len(hist))

    panel_1node = full_panel(hub, n1, exec_id)
    load1 = open('/proc/loadavg').read().split()[0]
    print('panel 1node done (load1=%s)' % load1)

    # start the remaining 7 agents
    for i in range(2, 9):
        agents[i].start()
    for i in range(2, 9):
        n = wait_online(hub, agents[i].node_id, timeout=90)
        assert n.get('status') == 'online' and n.get('sync_status') == 'synced', (i, n)
    time.sleep(3)  # let post-enrollment sync settle before sampling
    load8 = open('/proc/loadavg').read().split()[0]
    panel_8node = full_panel(hub, n1, exec_id)
    print('panel 8node done (load1=%s)' % load8)

    # tail effect: burst 24 executions, then sample immediately
    c, task8 = hub.post('/api/tasks', {
        'name': 'verify-s7-task8', 'type': 'script', 'script_id': script['id'],
        'target': {'type': 'node', 'node_ids': node_ids},
        'parameters': [{'name': 'rid', 'type': 'string', 'required': True}],
        'param_values': {}, 'timeout': 60, 'retry': 0,
        'offline_policy': 'allow_offline', 'enabled': True})
    assert c in (200, 201), (c, task8)
    threads = []

    def one(idx):
        hub.post('/api/tasks/%s/run' % task8['id'], {'params': {'rid': 'burst-%d' % idx}})

    for i in range(3):
        th = threading.Thread(target=one, args=(i,))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    t_burst = time.time()
    deadline = time.time() + 120
    while time.time() < deadline:
        c, lst = hub.get('/api/executions?task_id=%s&limit=200' % task8['id'])
        if lst and all(e.get('status') in S.TERMINAL_EXEC for e in lst):
            break
        time.sleep(0.5)
    panel_tail = full_panel(hub, n1, exec_id)
    print('panel tail done (burst wall %.1fs)' % (time.time() - t_burst))

    # cleanup
    for ag in agents.values():
        ag.stop(timeout=8)
    hp.send_signal(signal.SIGTERM)
    try:
        hp.wait(timeout=15)
    except subprocess.TimeoutExpired:
        hp.kill()
    time.sleep(1)
    assert S.wait_port_free(S.HUB_PORT, 10), 'port busy after stop'
    shutil.rmtree(VERIFY)
    assert not VERIFY.exists()

    out = {
        'batch': 'qa-resume-20261018-scale',
        'kind': 's7-verification',
        'generated_at_utc': S.now_utc(),
        'design': 'same 48-execution history; A=1 node, B=8 nodes quiet, C=8 nodes right after 24-exec burst',
        'history_executions': len(hist),
        'load1_at_1node': load1, 'load1_at_8node': load8,
        'panels': {'one_node': panel_1node, 'eight_nodes': panel_8node, 'eight_nodes_post_burst': panel_tail},
        'wall_s': round(time.time() - t0, 1),
    }
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / 's7-verification.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({k: {e: {kk: v[kk] for kk in ('p50', 'p95', 'max')} for e in ('nodes_list', 'node_detail', 'executions_list', 'execution_detail', 'healthz', 'readyz')}
                      for k, v in out['panels'].items()}, ensure_ascii=False, indent=1))
    print('VERIFY PASS in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
