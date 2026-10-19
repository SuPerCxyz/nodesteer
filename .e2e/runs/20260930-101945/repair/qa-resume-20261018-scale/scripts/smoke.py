#!/usr/bin/env python3
"""Smoke: validate harness mechanics (hub boot, enroll, exec, offline detect,
cleanup) with 2 agents before the full 8-node scale run."""
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scalelib as S  # noqa: E402

SMOKE = Path('/var/tmp/nodesteer-qa-20261018-scale/smoke')
os.umask(0o077)
S.WORK = SMOKE  # redirect scalelib work dir


def main():
    t0 = time.time()
    assert S.wait_port_free(S.HUB_PORT, 5), 'port busy'
    if SMOKE.exists():
        shutil.rmtree(SMOKE)
    (SMOKE / 'hub' / 'data').mkdir(parents=True)
    (SMOKE / 'shared').mkdir()
    pw = 'smoke-' + secrets.token_urlsafe(8)
    reg = 'smoke-reg-' + secrets.token_urlsafe(8)
    cred = SMOKE / 'credentials.json'
    fd = os.open(cred, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump({'admin_username': 'admin', 'admin_password': pw,
                   'registration_token': reg}, f)
    cfg = (
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
    ) % (S.HUB_PORT, S.HUB_PORT, S.HUB_PORT, reg, SMOKE / 'hub' / 'data',
         SMOKE / 'hub' / 'data' / 'artifacts', S.HUB_PORT, pw)
    hubcfg = SMOKE / 'hub' / 'hub.yaml'
    fd = os.open(hubcfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(cfg)

    logf = open(SMOKE / 'hub' / 'hub.log', 'ab', buffering=0)
    hp = subprocess.Popen([S.HUB_BIN, '--config', str(hubcfg)], stdout=logf,
                          stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                          start_new_session=True)
    assert S.wait_http_ok(S.HUB_PORT, '/readyz', 60), 'hub not ready'
    hub = S.Hub()
    hub.login()
    code, out = hub.put('/api/settings', {'heartbeat_interval_sec': '2',
                                          'revision_check_interval_sec': '5'})
    assert code == 200, ('settings', code, out)
    print('hub up pid=%d' % hp.pid)

    agents = {}
    for i in (1, 2):
        name = 'smoke-agent-%d' % i
        ip = '10.98.0.%d' % i
        code, en = hub.post('/api/nodes/enrollment',
                            {'node_name': name, 'node_ip': ip,
                             'hub_address': 'http://127.0.0.1:%d' % S.HUB_PORT})
        assert code == 200 and en.get('node_id'), (code, en)
        ag = S.AgentProc(str(i), name, ip, data_root=SMOKE)
        ag.node_id = en['node_id']
        ag.agent_id = en['agent_id']
        ag.write_config(reg)
        ag.start()
        agents[i] = ag
    for i, ag in agents.items():
        deadline = time.time() + 60
        n = None
        while time.time() < deadline:
            n = None
            c, n = hub.get('/api/nodes/' + ag.node_id)
            if c == 200 and n.get('status') == 'online' and n.get('sync_status') == 'synced':
                break
            time.sleep(1)
        assert n and n.get('status') == 'online' and n.get('sync_status') == 'synced', (i, n)
        print('agent %d online+synced, rev=%s' % (i, n.get('global_revision')))

    # script/task/exec
    side = SMOKE / 'shared' / 'side.log'
    code, script = hub.post('/api/scripts', {
        'name': 'smoke-script', 'description': 'v1', 'interpreter': 'shell', 'enabled': True,
        'environment': {}, 'working_dir': '',
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'content': ('SID="$NODESTEER_PARAM_run_id"\nSIDE="$NODESTEER_PARAM_side_file"\n'
                    'echo "OUT-$SID-start"\necho "ERR-$SID-line" >&2\nsleep 2\n'
                    'echo "OUT-$SID-done"\nprintf \'%s %s\\n\' "$SID" "$$" >> "$SIDE"\n')})
    assert code in (200, 201), (code, script)
    code, task = hub.post('/api/tasks', {
        'name': 'smoke-task', 'type': 'script', 'script_id': script['id'],
        'target': {'type': 'node', 'node_ids': [ag.node_id for ag in agents.values()]},
        'parameters': [{'name': 'run_id', 'type': 'string', 'required': True},
                       {'name': 'side_file', 'type': 'string', 'required': True}],
        'param_values': {}, 'timeout': 60, 'retry': 0,
        'offline_policy': 'hub_online_required', 'enabled': True})
    assert code in (200, 201), (code, task)
    code, run = hub.post('/api/tasks/%s/run' % task['id'],
                         {'params': {'run_id': 'smoke', 'side_file': str(side)}})
    assert code == 200 and len(run) == 2, (code, run)
    ok = 0
    for e in run:
        deadline = time.time() + 60
        ex = None
        while time.time() < deadline:
            c, ex = hub.get('/api/executions/' + e['id'])
            if ex.get('status') in S.TERMINAL_EXEC:
                break
            time.sleep(1)
        print('exec %s status=%s exit=%s out_ok=%s err_ok=%s' % (
            e['id'][:8], ex.get('status'), ex.get('exit_code'),
            'OUT-smoke-start' in ex.get('stdout', ''), 'ERR-smoke-line' in ex.get('stderr', '')))
        if ex.get('status') == 'SUCCESS' and ex.get('exit_code') == 0:
            ok += 1
        c, chunks = hub.get('/api/executions/%s/logs' % e['id'])
        print('  chunks=%d streams=%s' % (len(chunks) if isinstance(chunks, list) else -1,
                                          sorted({ch.get('stream') for ch in chunks}) if isinstance(chunks, list) else None))
    assert ok == 2, 'exec not all success'
    print('side lines:', len(side.read_text().splitlines()))

    # revision bump + converge
    import sqlite3
    def rev():
        conn = sqlite3.connect('file:%s?mode=ro' % (SMOKE / 'hub' / 'data' / 'hub.db'), uri=True)
        try:
            return conn.execute('SELECT value FROM revision_sequence').fetchone()[0]
        finally:
            conn.close()
    c, sc = hub.get('/api/scripts/' + script['id'])
    c2, _ = hub.put('/api/scripts/' + script['id'], dict(sc, description='v2'))
    want = rev()
    print('rev after bump:', want, 'put http:', c2)
    for i, ag in agents.items():
        deadline = time.time() + 60
        got = None
        while time.time() < deadline:
            c, n = hub.get('/api/nodes/' + ag.node_id)
            got = n.get('global_revision')
            if got == want:
                break
            time.sleep(1)
        print('agent %d converged to %s: %s' % (i, want, got == want))
        assert got == want

    # offline detect (kill agent 2) + recovery
    t = time.time()
    agents[2].kill()
    deadline = time.time() + 120
    st = None
    while time.time() < deadline:
        c, n = hub.get('/api/nodes/' + agents[2].node_id)
        st = n.get('status')
        if st == 'offline':
            break
        time.sleep(2)
    print('offline detect after %.1fs: %s' % (time.time() - t, st))
    assert st == 'offline', st
    agents[2].start()
    deadline = time.time() + 90
    n = None
    while time.time() < deadline:
        c, n = hub.get('/api/nodes/' + agents[2].node_id)
        if n.get('status') == 'online' and n.get('sync_status') == 'synced':
            break
        time.sleep(1)
    print('recovered after %.1fs: %s/%s' % (time.time() - t, n.get('status'), n.get('sync_status')))
    assert n.get('status') == 'online' and n.get('sync_status') == 'synced'

    # cleanup
    for ag in agents.values():
        ag.stop(timeout=8)
    hp.send_signal(signal.SIGTERM)
    try:
        hp.wait(timeout=15)
    except subprocess.TimeoutExpired:
        hp.kill()
    time.sleep(1)
    assert S.wait_port_free(S.HUB_PORT, 10), 'port still busy after hub stop'
    shutil.rmtree(SMOKE)
    assert not SMOKE.exists()
    print('SMOKE PASS in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
