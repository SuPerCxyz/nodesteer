#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: Go-side verification (items 1-9) on the deployed final2 build.

Build: dddee4f0-e2e-repair-20261018-final2 (hub 3b5eec15…, agent 89f2a3bb…).
Authority: repair hub http://192.168.100.209:18080 only. All fixtures are
nonce-prefixed and owned; protected nodes / primary units are never touched.

Items:
 1  8 concurrent GET /api/transfers (no hang) + GET /api/tasks latency during load
 2  16 concurrent DELETE /api/nodes (all succeed)
 3  16 concurrent DELETE /api/schedules (all 200 or normalized 503, no SQL text, ERROR logs)
 4  unknown subpath -> 404 for 6 families (GET/PUT/DELETE), resources unchanged
 5  missing-resource 404 normalization (5 endpoint groups, no sql: no rows)
 6  artifact download method guard (only GET/HEAD; POST/PUT -> 405 + Allow)
 7  405 + Allow sampling across 10 endpoints
 8  terminal transfer actions (Cancel -> 400 transfer already finished; Retry semantics)
 9  late-receipt terminal freeze (unit-test evidence, runtime construction not feasible)
"""
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import finallib as L  # noqa: E402
import hubclient  # noqa: E402
import hostlib  # noqa: E402

EV = HERE / 'evidence'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LABEL = 'dddee4f0-e2e-repair-20261018-final2'
HUB_SHA = '3b5eec15ea2ce7ad6cd0e0f90b7f0a2382e02db3c577c905ba7f4b6fd9c3902b'
AGENT_SHA = '89f2a3bb4b6bcb8b208230b0c9bffa48d7fb9ddda577b491dd6f4cf317f90008'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}
SQL_RE = re.compile(r'(?i)(sqlite|sql:|database is locked|UNIQUE constraint|no such table|no rows in result set|SELECT )')

os.umask(0o077)
hub = hubclient.Hub()


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {'nonce': secrets.token_hex(4), 'fixtures': [], 'phases': {}, 'cases': [],
            'children': {}, 'ids': {}}


def save_state(st):
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, STATE_F)


ST = load_state()
NONCE = ST['nonce']
P = 'qa-final2-%s-' % NONCE


def record(case, phase, status, conclusion, evidence=None, extra=None):
    row = {'case_id': case, 'phase': phase, 'at_utc': now_utc(), 'status': status,
           'conclusion': conclusion, 'evidence': evidence or [], 'extra': extra or {}}
    ST['cases'] = [c for c in ST['cases'] if c.get('case_id') != case]
    ST['cases'].append(row)
    save_state(ST)
    print('[%s] %s -> %s' % (phase, case, status), flush=True)
    return row


def dump_results():
    payload = {'batch': 'qa-resume-20261018-final2-verify', 'authority': 'http://192.168.100.209:18080',
               'build_label': LABEL, 'hub_sha256': HUB_SHA, 'agent_sha256': AGENT_SHA,
               'updated_at_utc': now_utc(), 'nonce': NONCE, 'cases': ST['cases']}
    tmp = RESULTS_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, RESULTS_F)


def ev_write(name, value):
    path = EV / name
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
    return 'evidence/' + name


def add_fixture(kind, fid, name):
    ST['fixtures'].append([kind, fid, name])
    save_state(ST)


def wait_transfer(tid, timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, t = hub.get('/api/transfers/' + tid)
        if code == 200 and isinstance(t, dict):
            last = t
            if t.get('status') in TERMINAL:
                return t
        time.sleep(1.5)
    return last


def wait_exec(exec_id, timeout=240):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        if code == 200 and isinstance(ex, dict):
            last = ex
            if ex.get('status') in TERMINAL:
                return ex
        time.sleep(1.5)
    return last


def wait_node_online(node_id, timeout=120):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, n = hub.get('/api/nodes/' + node_id)
        if code == 200 and isinstance(n, dict):
            last = n
            if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                return n
        time.sleep(1.5)
    return last


def child_of(key):
    meta = ST['children'][key]
    return hostlib.OwnedChild(NONCE, meta['host_key'], meta['tag'], meta['node_id'],
                              meta['agent_id'], meta['name'], meta['ip'],
                              hub.cred['registration_token'])


def enroll_child(key, tag, host_key, start=True):
    name = P + key
    ip = P + key + '.invalid'
    code, enroll = hub.post('/api/nodes/enrollment',
                            {'node_name': name, 'node_ip': ip,
                             'hub_address': 'http://192.168.100.209:18080'})
    if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
        raise RuntimeError('enrollment_failed_http_%s_%s' % (code, enroll))
    ch = hostlib.OwnedChild(NONCE, host_key, tag, enroll['node_id'], enroll['agent_id'],
                            name, ip, hub.cred['registration_token'])
    started = ch.write_config_and_start('ws://192.168.100.209:18080') if start else None
    ST['children'][key] = {'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'host_key': host_key, 'tag': tag, 'name': name, 'ip': ip,
                           'root': ch.root, 'opt_root': ch.opt_root,
                           'processes': (started or {}).get('owned_processes')}
    save_state(ST)
    add_fixture('node', enroll['node_id'], name)
    return ch


def hub_journal(grep, since='25 min ago', timeout=60):
    rc, out, err = L.ssh(L.HUB_HOST,
                         'journalctl -u %s --since "%s" -o cat --no-pager | { %s ; } || true'
                         % (L.HUB_UNIT, since, grep), timeout=timeout)
    return out


# ================================================================ prepare
def phase_prepare():
    out = {'at_utc': now_utc(), 'children': {}, 'resources': {}}
    for key, tag, host_key in (('a', '-a', 'n1'), ('b', '-b', 'n2')):
        if key in ST['children']:
            ch = child_of(key)
        else:
            ch = enroll_child(key, tag, host_key)
        node = wait_node_online(ST['children'][key]['node_id'])
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:%s:%s' % (key, node))
        if node.get('agent_version') != LABEL:
            raise RuntimeError('owned_child_wrong_build:%s:%s' % (key, node.get('agent_version')))
        out['children'][key] = {'node_id': ST['children'][key]['node_id'], 'host': ch.host['ip'],
                                'root': ch.root,
                                'node': {k: node.get(k) for k in ('hostname', 'status', 'sync_status', 'agent_version')}}
    # owned task (used by groups 1/4/7/8)
    if 'task' not in ST['ids']:
        task = {'name': P + 'task-main', 'type': 'command', 'command': 'echo qa-final2-ok',
                'target': {'type': 'node', 'node_ids': [ST['children']['a']['node_id']]},
                'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
        code, t = hub.post('/api/tasks', task)
        if code not in (200, 201):
            raise RuntimeError('task_create_failed_%s_%s' % (code, t))
        ST['ids']['task'] = t['id']
        add_fixture('task', t['id'], task['name'])
        out['resources']['task'] = {'id': t['id'], 'http': code}
    # owned script
    if 'script' not in ST['ids']:
        sc = {'name': P + 'script', 'description': 'final2 owned script', 'interpreter': 'shell',
              'content': '#!/bin/sh\necho qa-final2-script\n', 'enabled': True,
              'parameters': [], 'environment': {}, 'working_dir': ''}
        code, s = hub.post('/api/scripts', sc)
        if code not in (200, 201):
            raise RuntimeError('script_create_failed_%s_%s' % (code, s))
        ST['ids']['script'] = s['id']
        add_fixture('script', s['id'], sc['name'])
        out['resources']['script'] = {'id': s['id'], 'http': code}
    # owned artifact
    if 'artifact' not in ST['ids']:
        content = ('qa-final2-artifact-%s\n' % NONCE).encode()
        code, a = hub.upload_artifact(P + 'artifact', 'v1', P + 'artifact.bin', content)
        if code != 200:
            raise RuntimeError('artifact_upload_failed_http_%s' % code)
        ST['ids']['artifact'] = a['id']
        ST['ids']['artifact_sha'] = hashlib.sha256(content).hexdigest()
        add_fixture('artifact', a['id'], P + 'artifact')
        out['resources']['artifact'] = {'id': a['id'], 'http': code,
                                        'sha256': ST['ids']['artifact_sha'], 'size': len(content)}
    # owned application
    if 'app' not in ST['ids']:
        unit = 'nodesteer-' + P + 'app.service'
        opt = '/opt/' + P + 'app'
        app = {'name': P + 'app', 'description': 'final2 owned application',
               'artifact_id': ST['ids']['artifact'], 'version': '1.0',
               'binary_path': opt + '/bin/app', 'arguments': [], 'environment': {}, 'config': '',
               'config_path': opt + '/config/app.env', 'unit_name': unit,
               'health_check': {'type': 'systemd', 'target': unit, 'timeout': 5, 'attempts': 2, 'interval': 1}}
        code, a = hub.post('/api/applications', app)
        if code not in (200, 201):
            raise RuntimeError('app_create_failed_%s_%s' % (code, a))
        ST['ids']['app'] = a['id']
        add_fixture('application', a['id'], app['name'])
        out['resources']['app'] = {'id': a['id'], 'http': code}
    # owned execution: run the owned task on owned child a
    if 'execution' not in ST['ids']:
        code, run = hub.post('/api/tasks/%s/run' % ST['ids']['task'], {})
        exec_id = None
        if isinstance(run, list) and run:
            exec_id = run[0].get('id')
        elif isinstance(run, dict):
            exec_id = run.get('id') or (run.get('execution') or {}).get('id')
        if not exec_id:
            raise RuntimeError('task_run_no_execution:%s_%s' % (code, run))
        ex = wait_exec(exec_id, timeout=240)
        if not ex or ex.get('status') != 'SUCCESS':
            raise RuntimeError('owned_execution_not_success:%s' % (ex and ex.get('status')))
        ST['ids']['execution'] = exec_id
        save_state(ST)
        out['resources']['execution'] = {'id': exec_id, 'status': ex.get('status')}
    ev_write('phase00-prepare.json', out)
    return out


# ================================================================ item 1
def phase_g1():
    out = {'at_utc': now_utc(), 'case': ['P6-F1-8CONCURRENT-TRANSFERS']}
    node_a = ST['children']['a']['node_id']
    node_b = ST['children']['b']['node_id']
    ch_a = child_of('a')
    # create 12 small owned transfers so the list has rows with targets (reuse if re-running)
    made = ST['ids'].get('g1_transfers') or []
    root_b = ST['children']['b']['root']
    if not made:
        for i in range(12):
            src = ch_a.root + '/share/g1-%02d-%s.txt' % (i, NONCE)
            ch_a.remote_py("import json,os\np=%(p)r\nos.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)\n"
                           "open(p,'w').write(%(c)r)\nprint(json.dumps({'written':p}))\n"
                           % {'p': src, 'c': 'qa-final2-g1-%02d-%s\n' % (i, NONCE)})
            code, tr = hub.post('/api/transfers', {'source_node_id': node_a, 'source_path': src,
                                                   'targets': [{'node_id': node_b,
                                                                'destination_path': root_b + '/incoming-g1-%02d.txt' % i}]})
            if code != 200:
                raise RuntimeError('g1_transfer_create_failed_%s_%s' % (code, tr))
            made.append(tr['id'])
            add_fixture('transfer', tr['id'], 'g1-%02d' % i)
        ST['ids']['g1_transfers'] = made
        save_state(ST)
    time.sleep(2)
    # 8 concurrent GET /api/transfers (20 sequential calls each, 3 rounds) with barrier
    n = 8
    results = [None] * n
    tasks_latency = []
    stop = threading.Event()

    def lister(i, barrier):
        try:
            barrier.wait(timeout=30)
            t0 = time.time()
            per = []
            rows = None
            for _ in range(20):
                t1 = time.time()
                code, body = hub.get('/api/transfers', timeout=25)
                rows = body if isinstance(body, list) else (body or {}).get('transfers')
                per.append({'http': code, 'elapsed': round(time.time() - t1, 3)})
            targets = sum(len((r or {}).get('targets') or []) for r in (rows or []))
            results[i] = {'i': i, 'calls': len(per), 'all_200': all(p['http'] == 200 for p in per),
                          'max_call_elapsed': max(p['elapsed'] for p in per),
                          'total_elapsed': round(time.time() - t0, 3),
                          'rows': len(rows or []), 'targets': targets}
        except Exception as exc:  # noqa
            results[i] = {'i': i, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}

    def monitor():
        while not stop.is_set():
            try:
                t0 = time.time()
                code, _ = hub.get('/api/tasks', timeout=15)
                tasks_latency.append({'http': code, 'elapsed': round(time.time() - t0, 3)})
            except Exception as exc:  # noqa
                tasks_latency.append({'error': '%s: %s' % (type(exc).__name__, str(exc)[:100])})
            time.sleep(0.05)

    mon = threading.Thread(target=monitor)
    mon.start()
    t0 = time.time()
    for rnd in range(3):
        barrier = threading.Barrier(n)
        threads = [threading.Thread(target=lister, args=(i, barrier)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    wall = round(time.time() - t0, 3)
    stop.set()
    mon.join(timeout=10)
    all_done = all(r is not None for r in results)
    ok_codes = all((r or {}).get('all_200') for r in results)
    max_tasks = max((l.get('elapsed') or 0) for l in tasks_latency) if tasks_latency else None
    ok = (all_done and ok_codes and wall < 20 and max_tasks is not None and max_tasks < 10
          and all((r or {}).get('targets', 0) >= 12 for r in results))
    out['concurrent'] = results
    out['wall_sec'] = wall
    out['rounds'] = 3
    out['tasks_probe'] = {'samples': len(tasks_latency), 'max_elapsed': max_tasks,
                          'errors': [l for l in tasks_latency if 'error' in l][:3]}
    record('P6-F1-8CONCURRENT-TRANSFERS', 'concurrency',
           'FIXED' if ok else 'STILL_BROKEN',
           ('8 并发 GET /api/transfers × 3 轮（每线程 20 次调用）%.3fs 内全部 200（每行含目标）；'
            '并发期间 GET /api/tasks 采样 %d 次、最大延迟 %.3fs'
            % (wall, len(tasks_latency), max_tasks or -1)) if ok
           else '仍复现/异常：%s' % {'wall': wall, 'results': results, 'tasks': out['tasks_probe']},
           ['evidence/phase01-g1-transfers.json'])
    ev_write('phase01-g1-transfers.json', out)
    dump_results()
    return out


# ================================================================ item 2
def phase_g2():
    out = {'at_utc': now_utc(), 'case': ['P6-F1-16CONCURRENT-NODE-DELETE']}
    ids = []
    for i in range(16):
        name = P + 'g2-node-%02d' % i
        code, enroll = hub.post('/api/nodes/enrollment',
                                {'node_name': name, 'node_ip': '%s-g2-%02d.invalid' % (P, i),
                                 'hub_address': 'http://192.168.100.209:18080'})
        if code != 200 or not enroll.get('node_id'):
            raise RuntimeError('g2_enroll_failed_%s_%s' % (code, enroll))
        ids.append(enroll['node_id'])
        add_fixture('node', enroll['node_id'], name)
    ST['ids']['g2_nodes'] = ids
    save_state(ST)
    n = 16
    barrier = threading.Barrier(n)
    results = [None] * n

    def deleter(i):
        try:
            barrier.wait(timeout=30)
            t0 = time.time()
            code, body = hub.delete('/api/nodes/' + ids[i], timeout=30)
            raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            results[i] = {'i': i, 'http': code, 'elapsed': round(time.time() - t0, 3),
                          'body': raw[:120], 'sql_leak': bool(SQL_RE.search(raw))}
        except Exception as exc:  # noqa
            results[i] = {'i': i, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}

    threads = [threading.Thread(target=deleter, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    wall = round(time.time() - t0, 3)
    all_done = all(r is not None for r in results)
    ok_codes = all((r or {}).get('http') == 200 for r in results)
    time.sleep(1)
    gone = []
    for i, nid in enumerate(ids):
        c, _ = hub.get('/api/nodes/' + nid)
        gone.append({'i': i, 'http': c, 'deleted': c == 404})
    all_gone = all(g['deleted'] for g in gone)
    ok = all_done and ok_codes and all_gone
    out['concurrent'] = results
    out['wall_sec'] = wall
    out['verify_deleted'] = gone
    record('P6-F1-16CONCURRENT-NODE-DELETE', 'concurrency',
           'FIXED' if ok else 'STILL_BROKEN',
           '16 并发 DELETE /api/nodes 全部 200（%.3fs），且 16 个节点全部删除（GET 404）' % wall if ok
           else '仍复现：%s' % {'wall': wall, 'codes': [r and r.get('http') for r in results],
                              'gone': gone},
           ['evidence/phase02-g2-node-delete.json'])
    ev_write('phase02-g2-node-delete.json', out)
    dump_results()
    return out


# ================================================================ item 3
def phase_g3():
    out = {'at_utc': now_utc(), 'case': ['P6-F2-16CONCURRENT-SCHEDULE-DELETE']}
    task_id = ST['ids']['task']
    ids = []
    for i in range(16):
        sched = {'name': P + 'g3-sched-%02d' % i, 'task_id': task_id, 'type': 'interval',
                 'interval_sec': 3600, 'timezone': 'UTC', 'enabled': True,
                 'execution_owner': 'hub', 'offline_policy': 'hub_online_required',
                 'misfire_policy': 'skip'}
        code, s = hub.post('/api/schedules', sched)
        if code not in (200, 201):
            raise RuntimeError('g3_sched_create_failed_%s_%s' % (code, s))
        ids.append(s['id'])
        add_fixture('schedule', s['id'], sched['name'])
    ST['ids']['g3_schedules'] = ids
    save_state(ST)
    n = 16
    barrier = threading.Barrier(n)
    results = [None] * n

    def deleter(i):
        try:
            barrier.wait(timeout=30)
            t0 = time.time()
            code, body = hub.delete('/api/schedules/' + ids[i], timeout=30)
            raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            results[i] = {'i': i, 'http': code, 'elapsed': round(time.time() - t0, 3),
                          'body': raw[:140], 'sql_leak': bool(SQL_RE.search(raw))}
        except Exception as exc:  # noqa
            results[i] = {'i': i, 'error': '%s: %s' % (type(exc).__name__, str(exc)[:120])}

    threads = [threading.Thread(target=deleter, args=(i,)) for i in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=40)
    wall = round(time.time() - t0, 3)
    time.sleep(1.5)
    codes = [r and r.get('http') for r in results]
    ok_codes = all(c in (200, 503) for c in codes)
    leaks = [r for r in results if r and r.get('sql_leak')]
    busy_503 = [r for r in results if r and r.get('http') == 503]
    ok_503_text = all('storage busy, please retry' in (r.get('body') or '') for r in busy_503)
    errors = hub_journal("grep -c 'level=ERROR' || true")
    busy_logs = hub_journal("grep -c 'storage busy' || true")
    err_samples = hub_journal("grep 'level=ERROR' | tail -4 || true")
    log_ok = (not busy_503) or (int((busy_logs or '0').strip() or '0') >= len(busy_503))
    gone = []
    for i, sid in enumerate(ids):
        c, _ = hub.get('/api/schedules/' + sid)
        gone.append({'i': i, 'http': c, 'deleted': c == 404})
    ok = (all(r is not None for r in results) and ok_codes and not leaks and ok_503_text
          and log_ok)
    out.update({'concurrent': results, 'wall_sec': wall, 'deleted_verify': gone,
                'journal': {'error_lines': (errors or '').strip(), 'busy_lines': (busy_logs or '').strip(),
                            'error_samples': (err_samples or '').strip()[-600:]}})
    record('P6-F2-16CONCURRENT-SCHEDULE-DELETE', 'concurrency',
           'FIXED' if ok else 'STILL_BROKEN',
           ('16 并发 DELETE /api/schedules 全部 %s（%.3fs）；无 SQLite 原文；日志 ERROR=%s / storage busy=%s'
            % ('200' if not busy_503 else '200/503 归一化', wall,
               (errors or '').strip(), (busy_logs or '').strip())) if ok
           else '仍复现：%s' % {'codes': codes, 'leaks': leaks, 'busy': len(busy_503),
                              'journal': out['journal']},
           ['evidence/phase03-g3-schedule-delete.json'])
    ev_write('phase03-g3-schedule-delete.json', out)
    dump_results()
    return out


# ================================================================ item 4
def phase_g4():
    out = {'at_utc': now_utc(), 'case': ['P14-UNKNOWN-SUBPATH-404']}
    fams = {
        'nodes': ST['children']['a']['node_id'],
        'tasks': ST['ids']['task'],
        'scripts': ST['ids']['script'],
        'applications': ST['ids']['app'],
        'artifacts': ST['ids']['artifact'],
        'executions': ST['ids']['execution'],
    }
    before = {}
    for fam, fid in fams.items():
        c, body = hub.get('/api/%s/%s' % (fam, fid))
        name = None
        if isinstance(body, dict):
            name = body.get('name') or body.get('hostname') or body.get('id')
        before[fam] = {'http': c, 'name': name}
    probes = {}
    for fam, fid in fams.items():
        probes[fam] = {}
        for method in ('GET', 'PUT', 'DELETE'):
            c, h, b = hub.raw_text(method, '/api/%s/%s/bogus' % (fam, fid),
                                   {} if method == 'PUT' else None)
            probes[fam][method] = {'http': c, 'body': b[:100], 'sql_leak': bool(SQL_RE.search(b))}
    after = {}
    unchanged = True
    for fam, fid in fams.items():
        c, body = hub.get('/api/%s/%s' % (fam, fid))
        name = None
        if isinstance(body, dict):
            name = body.get('name') or body.get('hostname') or body.get('id')
        after[fam] = {'http': c, 'name': name}
        if c != 200 or name != before[fam]['name']:
            unchanged = False
    all404 = all(p['http'] == 404 for fam in probes.values() for p in fam.values())
    no_sql = not any(p['sql_leak'] for fam in probes.values() for p in fam.values())
    ok = all404 and unchanged and no_sql
    out.update({'probes': probes, 'before': before, 'after': after,
                'all_404': all404, 'resources_unchanged': unchanged})
    record('P14-UNKNOWN-SUBPATH-404', 'routing',
           'FIXED' if ok else 'STILL_BROKEN',
           '6 资源族 × GET/PUT/DELETE /api/{fam}/{id}/bogus 共 18 探针全部 404；资源本体未被改写/删除' if ok
           else '仍复现：%s' % {'codes': {f: {m: p['http'] for m, p in v.items()} for f, v in probes.items()},
                              'unchanged': unchanged},
           ['evidence/phase04-g4-subpath.json'])
    ev_write('phase04-g4-subpath.json', out)
    dump_results()
    return out


# ================================================================ item 5
def phase_g5():
    out = {'at_utc': now_utc(), 'case': ['P14-MISSING-RESOURCE-404']}
    rand = '00000000-0000-4000-8000-%012x' % secrets.randbelow(0xffffffffffff)
    probes = {}
    c, h, b = hub.raw_text('POST', '/api/nodes', {'id': rand, 'status': 'online'})
    probes['post_node_missing'] = {'http': c, 'body': b[:140], 'expect': 'node not found'}
    c, h, b = hub.raw_text('POST', '/api/nodes/%s/revoke-credential' % rand, {})
    probes['revoke_missing'] = {'http': c, 'body': b[:140], 'expect': 'node not found'}
    c, h, b = hub.raw_text('POST', '/api/transfers/%s/retry' % rand, {})
    probes['transfer_retry_missing'] = {'http': c, 'body': b[:140], 'expect': 'transfer not found'}
    c, h, b = hub.raw_text('POST', '/api/transfers/%s/cancel' % rand, {})
    probes['transfer_cancel_missing'] = {'http': c, 'body': b[:140], 'expect': 'transfer not found'}
    c, h, b = hub.raw_text('POST', '/api/executions/%s/cancel' % rand, {})
    probes['exec_cancel_missing'] = {'http': c, 'body': b[:140], 'expect': 'execution not found'}
    c, h, b = hub.raw_text('PUT', '/api/users/%s' % rand, {'role': 'viewer'})
    probes['user_put_missing'] = {'http': c, 'body': b[:140], 'expect': 'user not found'}
    for k, p in probes.items():
        p['sql_leak'] = bool(SQL_RE.search(p['body']))
    ok = all(p['http'] == 404 and p['expect'] in p['body'] and not p['sql_leak'] for p in probes.values())
    out['probes'] = probes
    record('P14-MISSING-RESOURCE-404', 'routing',
           'FIXED' if ok else 'STILL_BROKEN',
           '6 组不存在资源端点全部 404 归一化文案，且无 sql: no rows / SQLite 原文' if ok
           else '仍复现：%s' % {k: (p['http'], p['body'][:60]) for k, p in probes.items()},
           ['evidence/phase05-g5-missing.json'])
    ev_write('phase05-g5-missing.json', out)
    dump_results()
    return out


# ================================================================ item 6
def phase_g6():
    out = {'at_utc': now_utc(), 'case': ['P14-ARTIFACT-DOWNLOAD-METHODS']}
    aid = ST['ids']['artifact']
    c, h, b = hub.raw_text('GET', '/api/artifacts/%s/download' % aid)
    got_sha = hashlib.sha256(b.encode('utf-8', 'replace')).hexdigest()
    out['get'] = {'http': c, 'sha256': got_sha, 'expected_sha': ST['ids'].get('artifact_sha'),
                  'match': got_sha == ST['ids'].get('artifact_sha')}
    c, h, b = hub.raw_text('HEAD', '/api/artifacts/%s/download' % aid)
    out['head'] = {'http': c}
    for m in ('POST', 'PUT'):
        c, h, b = hub.raw_text(m, '/api/artifacts/%s/download' % aid, {} if m == 'PUT' else None)
        out[m.lower()] = {'http': c, 'allow': h.get('Allow'), 'body': b[:80],
                          'content_leaked': b.strip().startswith('qa-final2-artifact')}
    allow_ok = (out['post'].get('allow') or '').replace(' ', '') == 'GET,HEAD' and \
               (out['put'].get('allow') or '').replace(' ', '') == 'GET,HEAD'
    ok = (out['get']['http'] == 200 and out['get']['match'] and out['head']['http'] == 200
          and out['post']['http'] == 405 and out['put']['http'] == 405 and allow_ok
          and not out['post']['content_leaked'] and not out['put']['content_leaked'])
    record('P14-ARTIFACT-DOWNLOAD-METHODS', 'routing',
           'FIXED' if ok else 'STILL_BROKEN',
           'artifact download：GET/HEAD 200（内容 SHA 一致）；POST/PUT 405 + Allow: GET, HEAD 且不返回文件内容' if ok
           else '仍复现：%s' % out,
           ['evidence/phase06-g6-download.json'])
    ev_write('phase06-g6-download.json', out)
    dump_results()
    return out


# ================================================================ item 7
def phase_g7():
    out = {'at_utc': now_utc(), 'case': ['P6-F6-405-ALLOW-SAMPLE']}
    # one dedicated owned schedule kept for the probe (not part of g3); reuse on re-run
    sched_id = ST['ids'].get('schedule_g7')
    if sched_id:
        c, _ = hub.get('/api/schedules/%s' % sched_id)
        if c == 404:
            sched_id = None
    if not sched_id:
        sched = {'name': P + 'g7-sched', 'task_id': ST['ids']['task'], 'type': 'interval',
                 'interval_sec': 3600, 'timezone': 'UTC', 'enabled': True,
                 'execution_owner': 'hub', 'offline_policy': 'hub_online_required', 'misfire_policy': 'skip'}
        code, s = hub.post('/api/schedules', sched)
        if code not in (200, 201):
            raise RuntimeError('g7_sched_create_failed_%s_%s' % (code, s))
        sched_id = s['id']
        ST['ids']['schedule_g7'] = sched_id
        add_fixture('schedule', sched_id, sched['name'])
        save_state(ST)
    probes = [
        ('GET', '/api/login', None),
        ('PATCH', '/api/tasks', {}),
        ('POST', '/api/tasks/%s' % ST['ids']['task'], {}),
        ('GET', '/api/tasks/%s/run' % ST['ids']['task'], None),
        ('POST', '/api/schedules/%s' % sched_id, {}),
        ('DELETE', '/api/settings', None),
        ('GET', '/api/executions/%s/cancel' % ST['ids']['execution'], None),
        ('POST', '/api/applications/%s/revisions' % ST['ids']['app'], {}),
        ('GET', '/api/nodes/upgrade', None),
        ('PUT', '/api/transfers/%s' % ST['ids']['g1_transfers'][0], {}),
    ]
    rows = []
    for method, path, body in probes:
        c, h, b = hub.raw_text(method, path, body)
        rows.append({'method': method, 'path': path.replace(NONCE, '<nonce>'),
                     'http': c, 'allow': h.get('Allow'), 'body': b[:80]})
    ok = all(r['http'] == 405 and (r['allow'] or '').strip() for r in rows)
    out['probes'] = rows
    record('P6-F6-405-ALLOW-SAMPLE', 'routing',
           'FIXED' if ok else 'STILL_BROKEN',
           '10 个端点抽样 405 全部携带非空 Allow（%s）'
           % ', '.join('%s %s -> %s' % (r['method'], r['path'], r['allow']) for r in rows) if ok
           else '仍复现：%s' % [(r['method'], r['path'], r['http'], r['allow']) for r in rows],
           ['evidence/phase07-g7-allow.json'])
    ev_write('phase07-g7-allow.json', out)
    dump_results()
    return out


# ================================================================ item 8
def phase_g8():
    out = {'at_utc': now_utc(), 'case': ['P16-TERMINAL-TRANSFER-ACTIONS']}
    node_a = ST['children']['a']['node_id']
    node_b = ST['children']['b']['node_id']
    ch_a = child_of('a')
    # SUCCESS transfer
    src = ch_a.root + '/share/term-ok-%s.txt' % NONCE
    ch_a.remote_py("import json,os\np=%(p)r\nos.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)\n"
                   "open(p,'w').write(%(c)r)\nprint(json.dumps({'written':p}))\n"
                   % {'p': src, 'c': 'qa-final2-term-ok-%s\n' % NONCE})
    code, tr = hub.post('/api/transfers', {'source_node_id': node_a, 'source_path': src,
                                           'targets': [{'node_id': node_b,
                                                        'destination_path': ch_a.root + '/term-ok-%s.out' % NONCE}]})
    if code != 200:
        raise RuntimeError('g8_success_transfer_create_failed_%s_%s' % (code, tr))
    tid_ok = tr['id']
    add_fixture('transfer', tid_ok, 'term-success')
    final = wait_transfer(tid_ok, timeout=180)
    out['success_transfer'] = {'id': tid_ok, 'status': (final or {}).get('status')}
    c, h, b = hub.raw_text('POST', '/api/transfers/%s/cancel' % tid_ok, {})
    out['cancel_success'] = {'http': c, 'body': b[:160]}
    c2, t2 = hub.get('/api/transfers/' + tid_ok)
    out['after_cancel'] = {'http': c2, 'status': (t2 or {}).get('status'), 'error': (t2 or {}).get('error')}
    c3, h3, b3 = hub.raw_text('POST', '/api/transfers/%s/retry' % tid_ok, {})
    c4, t4 = hub.get('/api/transfers/' + tid_ok)
    out['retry_success'] = {'http': c3, 'body': b3[:120], 'status_after': (t4 or {}).get('status')}
    ok_success = ((final or {}).get('status') == 'SUCCESS' and out['cancel_success']['http'] == 400
                  and 'transfer already finished' in out['cancel_success']['body']
                  and out['after_cancel']['status'] == 'SUCCESS'
                  and c3 == 200 and out['retry_success']['status_after'] == 'SUCCESS')
    # FAILED transfer (missing source file)
    missing = ch_a.root + '/share/missing-%s.bin' % NONCE
    code, tr = hub.post('/api/transfers', {'source_node_id': node_a, 'source_path': missing,
                                           'targets': [{'node_id': node_b,
                                                        'destination_path': ch_a.root + '/missing-%s.out' % NONCE}]})
    if code != 200:
        raise RuntimeError('g8_failed_transfer_create_failed_%s_%s' % (code, tr))
    tid_bad = tr['id']
    add_fixture('transfer', tid_bad, 'term-failed')
    final_bad = wait_transfer(tid_bad, timeout=180)
    out['failed_transfer'] = {'id': tid_bad, 'status': (final_bad or {}).get('status'),
                              'error': (final_bad or {}).get('error')}
    c5, h5, b5 = hub.raw_text('POST', '/api/transfers/%s/cancel' % tid_bad, {})
    out['cancel_failed'] = {'http': c5, 'body': b5[:160]}
    c6, t6 = hub.get('/api/transfers/' + tid_bad)
    out['after_cancel_failed'] = {'status': (t6 or {}).get('status')}
    c7, h7, b7 = hub.raw_text('POST', '/api/transfers/%s/retry' % tid_bad, {})
    out['retry_failed_http'] = {'http': c7, 'body': b7[:120]}
    seq = []
    deadline = time.time() + 90
    t_start = time.time()
    last = None
    while time.time() < deadline:
        c, t = hub.get('/api/transfers/' + tid_bad)
        st = (t or {}).get('status')
        if not seq or seq[-1]['status'] != st:
            seq.append({'status': st, 'at': now_utc()})
        last = t
        # break once the retried attempt has terminalized again (or after a grace window)
        if st in TERMINAL and (len(seq) >= 2 or time.time() - t_start > 20):
            break
        time.sleep(1.5)
    out['retry_failed_sequence'] = seq
    out['retry_failed_final'] = {'status': (last or {}).get('status'), 'error': (last or {}).get('error')}
    ok_failed = ((final_bad or {}).get('status') == 'FAILED' and out['cancel_failed']['http'] == 400
                 and 'transfer already finished' in out['cancel_failed']['body']
                 and out['after_cancel_failed']['status'] == 'FAILED'
                 and c7 == 200 and (last or {}).get('status') == 'FAILED')
    ok = ok_success and ok_failed
    record('P16-TERMINAL-TRANSFER-ACTIONS', 'terminal',
           'FIXED' if ok else 'STILL_BROKEN',
           ('终态冻结：SUCCESS/FAILED 传输 Cancel 均 400 transfer already finished 且状态不被改写；'
            'SUCCESS Retry 200 不改写；FAILED Retry 200 恢复后再次终态化（唯一恢复路径）') if ok
           else '仍复现：%s' % {'success': ok_success, 'failed': ok_failed, 'out': out},
           ['evidence/phase08-g8-terminal.json'])
    ev_write('phase08-g8-terminal.json', out)
    dump_results()
    return out


# ================================================================ item 9
def phase_g9():
    out = {'at_utc': now_utc(), 'case': ['P16-LATE-RECEIPT-TERMINAL-FREEZE']}
    env = dict(os.environ, TMPDIR='/var/tmp/nodesteer-go-tmp')
    tests = ('TestFileTransferTerminalTargetsFrozen|TestFileTransferTerminalStatesFrozen|'
             'TestFileTransferExplicitActionsOnTerminal|TestFileTransferSupersededUploadAttemptIgnoredAndResumeCompletes|'
             'TestFileTransferCancelRaceWithStaleDispatchDoesNotResurrect|TestFileTransferSupersededDeliveryAttemptIgnored')
    cp = subprocess.run(['go', 'test', './internal/hub/', '-count=1', '-v', '-run', tests],
                        cwd='/home/superc/code/nodesteer', capture_output=True, text=True,
                        timeout=900, env=env)
    lines = cp.stdout.splitlines()
    passed = [l.split('--- PASS: ')[1].split(' ')[0] for l in lines if '--- PASS: ' in l]
    failed = [l.split('--- FAIL: ')[1].split(' ')[0] for l in lines if '--- FAIL: ' in l]
    out['unit_tests'] = {'exit': cp.returncode, 'passed': passed, 'failed': failed,
                         'tail': '\n'.join(lines[-12:])}
    time.sleep(1)
    frozen_logs = hub_journal("grep -c 'terminal state frozen' || true")
    superseded_logs = hub_journal("grep -c 'superseded attempt' || true")
    out['hub_journal'] = {'terminal_state_frozen_lines': (frozen_logs or '').strip(),
                          'superseded_attempt_lines': (superseded_logs or '').strip(),
                          'note': '本次会话窗口内 Hub 日志；终态被拒写会记 WARN "terminal state frozen"'}
    ok = cp.returncode == 0 and len(passed) >= 6 and not failed
    record('P16-LATE-RECEIPT-TERMINAL-FREEZE', 'terminal',
           'FIXED' if ok else 'STILL_BROKEN',
           ('终态冻结单测 6 项全过（%s）；运行时"迟到回执改写终态目标"无法经公开 API 构造，引用单测证据'
            % ', '.join(sorted(set(passed)))) if ok
           else '仍复现：%s' % {'exit': cp.returncode, 'failed': failed},
           ['evidence/phase09-g9-late-receipt.json'])
    ev_write('phase09-g9-late-receipt.json', out)
    dump_results()
    return out


PHASES = {'prepare': phase_prepare, 'g1': phase_g1, 'g2': phase_g2, 'g3': phase_g3,
          'g4': phase_g4, 'g5': phase_g5, 'g6': phase_g6, 'g7': phase_g7,
          'g8': phase_g8, 'g9': phase_g9}


def main():
    hub.login()
    phase = sys.argv[1] if len(sys.argv) > 1 else 'all'
    order = ['prepare', 'g1', 'g2', 'g3', 'g4', 'g5', 'g6', 'g7', 'g8', 'g9']
    if phase == 'all':
        for name in order:
            print('==== phase', name, flush=True)
            PHASES[name]()
    else:
        PHASES[phase]()
    dump_results()


if __name__ == '__main__':
    main()
