#!/usr/bin/env python3
"""qa-resume-20261018-final-verify: Go-side regression on the deployed final build.

Build: dddee4f0-e2e-repair-20261018-final (hub 00e1f375…, agent c6ccfe10…).
Authority: repair hub http://192.168.100.209:18080 only.

Covers task items 1-10:
 1 X16 secret split redaction (owned child + secret param task)
 2 enrollment 12x concurrency (no 500, no SQLite text)
 3 APP-NODES empty array
 4 past one_time schedule -> 400
 5 /api/me snake_case keys
 6 method/not-found semantics (transfers 405, DELETE 404, node oracle 403)
 7 self transfer 400 + disabled dispatch 400
 8 duplicate username 400 normalized
 9 deployment journal from_version = installed version
10 G23 reconnect handshake window (mock WS holds HELLO_ACK)

Every fixture is nonce-prefixed and owned; nothing protected is touched.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import hubclient  # noqa: E402
import hostlib  # noqa: E402

EV = HERE / 'evidence'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LABEL = 'dddee4f0-e2e-repair-20261018-final'
HUB_SHA = '00e1f375bf67a59ad04dfc6283741cb276ac6be70ce2f21e02d25094845e7a71'
AGENT_SHA = 'c6ccfe104fec2dfdd25d2e244d60809e6db84066ee296f4a9b3167944d238d01'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}
SQL_RE = re.compile(r'(?i)(sqlite|sql:|database is locked|UNIQUE constraint|no such table|SELECT )')

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
P = 'qa-final-%s-' % NONCE


def record(case, phase, status, conclusion, evidence=None, extra=None):
    row = {'case_id': case, 'phase': phase, 'at_utc': now_utc(), 'status': status,
           'conclusion': conclusion, 'evidence': evidence or [], 'extra': extra or {}}
    ST['cases'] = [c for c in ST['cases'] if c.get('case_id') != case]
    ST['cases'].append(row)
    save_state(ST)
    print('[%s] %s -> %s' % (phase, case, status), flush=True)
    return row


def dump_results():
    payload = {'batch': 'qa-resume-20261018-final-verify', 'authority': 'http://192.168.100.209:18080',
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


def wait_exec(exec_id, timeout=300):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + hub.quote(exec_id))
        if code == 200 and isinstance(ex, dict):
            last = ex
            if ex.get('status') in TERMINAL:
                return ex
        time.sleep(2)
    return last


def wait_node_online(node_id, timeout=150):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, n = hub.get('/api/nodes/' + node_id)
        if code == 200 and isinstance(n, dict):
            last = n
            if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                return n
        time.sleep(2)
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


# ---------------------------------------------------------------- phase: children
def phase_children():
    out = {'at_utc': now_utc(), 'children': {}}
    plan = [('a', '-a', 'n2'), ('b', '-b', 'n2'), ('d', '-d', 'n3')]
    for key, tag, host_key in plan:
        if key in ST['children']:
            ch = child_of(key)
        else:
            ch = enroll_child(key, tag, host_key)
        node = wait_node_online(ST['children'][key]['node_id'])
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:%s:%s' % (key, node))
        if node.get('agent_version') != LABEL:
            raise RuntimeError('owned_child_wrong_build:%s:%s' % (key, node.get('agent_version')))
        out['children'][key] = {'node_id': ST['children'][key]['node_id'],
                                'host': ch.host['ip'], 'root': ch.root,
                                'node': {k: node.get(k) for k in
                                         ('hostname', 'status', 'sync_status', 'agent_version')}}
    ev_write('phase01-children.json', out)
    return out


# ---------------------------------------------------------------- phase: api contract
def phase_api_contract():
    out = {'at_utc': now_utc(), 'cases': {}}
    # ---- item 5: /api/me keys
    code, me = hub.get('/api/me')
    keys = sorted((me or {}).keys()) if isinstance(me, dict) else []
    pascal = [k for k in keys if k[:1].isupper()]
    ok_me = (code == 200 and {'token', 'user_id', 'username', 'role', 'expires'} <= set(keys)
             and not pascal)
    out['cases']['ME_KEYS'] = {'http': code, 'keys': keys, 'pascal_keys': pascal,
                               'user_id': (me or {}).get('user_id'),
                               'role': (me or {}).get('role')}
    record('OBS-20261015-API-CONTRACT-ME-KEYS', 'api', 'FIXED' if ok_me else 'STILL_BROKEN',
           '/api/me 返回 token/user_id/username/role/expires 且无 PascalCase 键' if ok_me
           else '仍复现：%s' % out['cases']['ME_KEYS'],
           ['evidence/phase02-api-contract.json'])
    # ---- item 6a: transfers non-POST -> 405 with Allow (nonexistent id also 405; method guard first)
    tid = ST['ids'].get('transfer_happy')
    probes = {}
    if tid:
        c, h, b = hub.raw_text('PUT', '/api/transfers/%s' % tid, {})
        probes['put'] = {'http': c, 'allow': h.get('Allow'), 'body': b[:120]}
        c, h, b = hub.raw_text('DELETE', '/api/transfers/%s' % tid, None)
        probes['delete'] = {'http': c, 'allow': h.get('Allow'), 'body': b[:120]}
        c, h, b = hub.raw_text('GET', '/api/transfers/%s/cancel' % tid, None)
        probes['get_action'] = {'http': c, 'allow': h.get('Allow'), 'body': b[:120]}
    missing_tid = '00000000-0000-4000-8000-0000000000aa'
    c, h, b = hub.raw_text('PUT', '/api/transfers/%s' % missing_tid, {})
    probes['put_missing_id'] = {'http': c, 'allow': h.get('Allow'), 'body': b[:120]}
    ok405 = (probes.get('put', {}).get('http') == 405 and 'GET' in (probes.get('put', {}).get('allow') or '')
             and probes.get('delete', {}).get('http') == 405
             and probes.get('get_action', {}).get('http') == 405
             and (probes.get('get_action', {}).get('allow') or '') == 'POST'
             and probes.get('put_missing_id', {}).get('http') == 405)
    out['cases']['TRANSFERS_405'] = probes
    record('OBS-20261015-API-CONTRACT-TRANSFERS-405', 'api', 'FIXED' if ok405 else 'STILL_BROKEN',
           'transfers 非 POST -> 405 + Allow（方法守卫先于存在性）' if ok405
           else '仍复现：%s' % probes, ['evidence/phase02-api-contract.json'])
    # ---- item 6b: DELETE missing id -> 404 normalized
    del_out = {}
    rand = '00000000-0000-4000-8000-00000000%04x' % secrets.randbelow(0xffff)
    for name, path in (('scripts', '/api/scripts/%s'), ('tasks', '/api/tasks/%s'),
                       ('schedules', '/api/schedules/%s'), ('groups', '/api/groups/%s'),
                       ('artifacts', '/api/artifacts/%s'), ('applications', '/api/applications/%s')):
        c, h, b = hub.raw_text('DELETE', path % rand, None)
        del_out[name] = {'http': c, 'body': b[:140], 'sql_leak': bool(SQL_RE.search(b))}
    okdel = all(v['http'] == 404 and not v['sql_leak'] for v in del_out.values())
    out['cases']['DELETE_MISSING_404'] = del_out
    record('OBS-20261015-API-CONTRACT-DELETE-404', 'api', 'FIXED' if okdel else 'STILL_BROKEN',
           '删除不存在 id 统一 404 且无 SQL 原文（6 端点）' if okdel
           else '仍复现：%s' % {k: (v['http'], v['sql_leak']) for k, v in del_out.items()},
           ['evidence/phase02-api-contract.json'])
    # ---- item 6c: node existence oracle (operator/viewer -> 403 both existing & missing)
    oracle = {'users': {}}
    for role in ('operator', 'viewer'):
        uname = P + 'role-' + role
        code, u = hub.post('/api/users', {'username': uname, 'password': 'qa-final-pass-123', 'role': role})
        if code not in (200, 201):
            raise RuntimeError('user_create_failed_%s_%s' % (code, u))
        ST['ids']['user_' + role] = u['id']
        add_fixture('user', u['id'], uname)
        tok = None
        fresh = hubclient.Hub()
        c2, login = fresh.raw('POST', '/api/login', {'username': uname, 'password': 'qa-final-pass-123'})
        if c2 == 200 and isinstance(login, dict):
            tok = login.get('token')
        rec = {'user_id': u['id'], 'login_http': c2, 'token_ok': bool(tok)}
        existing = ST['children']['b']['node_id']
        if tok:
            c3, h3, b3 = fresh.raw_text('DELETE', '/api/nodes/%s' % existing, None,
                                        headers={'Authorization': 'Bearer ' + tok})
            c4, h4, b4 = fresh.raw_text('DELETE', '/api/nodes/%s' % rand, None,
                                        headers={'Authorization': 'Bearer ' + tok})
            c5, h5, b5 = fresh.raw_text('GET', '/api/users', None,
                                        headers={'Authorization': 'Bearer ' + tok})
            rec.update({'delete_existing': c3, 'delete_missing': c4, 'list_users': c5,
                        'body_missing': b4[:120], 'sql_leak': bool(SQL_RE.search(b3 + b4))})
        oracle['users'][role] = rec
    okoracle = all(r.get('delete_existing') == 403 and r.get('delete_missing') == 403
                   and not r.get('sql_leak') for r in oracle['users'].values())
    # admin PUT missing node -> 404 node not found (no SQL)
    c6, h6, b6 = hub.raw_text('PUT', '/api/nodes/%s' % rand, {'labels': {'x': 'y'}})
    oracle['admin_put_missing'] = {'http': c6, 'body': b6[:140], 'sql_leak': bool(SQL_RE.search(b6))}
    okoracle = okoracle and c6 == 404 and not oracle['admin_put_missing']['sql_leak']
    out['cases']['NODE_ORACLE'] = oracle
    record('OBS-20261015-API-CONTRACT-NODE-ORACLE', 'api', 'FIXED' if okoracle else 'STILL_BROKEN',
           'operator/viewer 对存在/不存在节点 DELETE 均 403；admin PUT 不存在 404 node not found 无 SQL' if okoracle
           else '仍复现：%s' % oracle, ['evidence/phase02-api-contract.json'])
    # ---- item 8: duplicate username -> 400 username already exists (no SQL)
    uname = P + 'dup'
    c1, u = hub.post('/api/users', {'username': uname, 'password': 'qa-final-pass-123', 'role': 'viewer'})
    c2, h2, b2 = hub.raw_text('POST', '/api/users', {'username': uname, 'password': 'qa-final-pass-123', 'role': 'viewer'})
    dup = {'first_http': c1, 'second_http': c2, 'second_body': b2[:200], 'sql_leak': bool(SQL_RE.search(b2))}
    okdup = c1 in (200, 201) and c2 == 400 and 'username already exists' in b2 and not dup['sql_leak']
    if c1 in (200, 201):
        ST['ids']['user_dup'] = u['id']
        add_fixture('user', u['id'], uname)
    out['cases']['DUP_USER'] = dup
    record('OBS-20261016-G1-USER-DUP-SQL-MESSAGE', 'api', 'FIXED' if okdup else 'STILL_BROKEN',
           '重复用户名 -> 400 username already exists 且无 SQLite 原文' if okdup
           else '仍复现：%s' % dup, ['evidence/phase02-api-contract.json'])
    # ---- item 2: enrollment concurrency (12 parallel, all non-500, no SQLite text)
    def enroll(i):
        name = P + 'conc-%02d' % i
        c, body = hub.post('/api/nodes/enrollment',
                           {'node_name': name, 'node_ip': '%s%02d.invalid' % (P, i),
                            'hub_address': 'http://192.168.100.209:18080'})
        raw = json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body
        return {'i': i, 'http': c, 'sql_leak': bool(SQL_RE.search(raw)), 'raw': raw[:120],
                'node_id': body.get('node_id') if isinstance(body, dict) else None}
    conc = []
    threads = [threading.Thread(target=lambda i=i: conc.append(enroll(i))) for i in range(12)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    elapsed = time.time() - t0
    codes = [r['http'] for r in conc]
    seq_name = P + 'seq'
    cs, seq = hub.post('/api/nodes/enrollment',
                       {'node_name': seq_name, 'node_ip': P + 'seq.invalid',
                        'hub_address': 'http://192.168.100.209:18080'})
    cbad, hbad, bbad = hub.raw_text('POST', '/api/nodes/enrollment', {'node_ip': 'x'})
    for r in conc:
        if r.get('node_id'):
            add_fixture('node', r['node_id'], P + 'conc-%02d' % r['i'])
    if cs == 200 and isinstance(seq, dict) and seq.get('node_id'):
        add_fixture('node', seq['node_id'], seq_name)
    okconc = (len(conc) == 12 and all(c == 200 for c in codes)
              and not any(r['sql_leak'] for r in conc)
              and cs == 200 and cbad == 400 and not SQL_RE.search(bbad))
    out['cases']['ENROLL_CONC'] = {'elapsed_s': round(elapsed, 2), 'codes': codes,
                                   'leaks': [r['raw'] for r in conc if r['sql_leak']],
                                   'sequential': {'http': cs, 'node_id': seq.get('node_id') if isinstance(seq, dict) else None},
                                   'bad_request': {'http': cbad, 'body': bbad[:160]}}
    record('OBS-20261016-G1-ENROLL-TRANSIENT-500', 'api', 'FIXED' if okconc else 'STILL_BROKEN',
           '12 并发 enrollment 全部 200（无 500/503、无 SQLite 原文）；顺序正常；坏请求 400 归一化' if okconc
           else '仍复现：%s' % {'codes': codes, 'seq': cs, 'bad': cbad},
           ['evidence/phase02-api-contract.json'])
    ev_write('phase02-api-contract.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: task + schedule
def phase_task_schedule():
    out = {'at_utc': now_utc(), 'cases': {}}
    node_b = ST['children']['b']['node_id']
    task = {'name': P + 'task-main', 'type': 'command', 'command': 'echo qa-final-ok',
            'target': {'type': 'node', 'node_ids': [node_b]},
            'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, t = hub.post('/api/tasks', task)
    if code not in (200, 201):
        raise RuntimeError('task_create_failed_%s_%s' % (code, t))
    ST['ids']['task'] = t['id']
    add_fixture('task', t['id'], task['name'])
    out['task'] = {'id': t['id'], 'http': code}
    # item 4: one_time schedule past run_at -> 400; future -> 200; PUT changed past -> 400
    past = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3600))
    future = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 7200))
    base = {'name': P + 'sched', 'task_id': t['id'], 'type': 'one_time',
            'timezone': 'UTC', 'enabled': True, 'execution_owner': 'hub',
            'offline_policy': 'hub_online_required', 'misfire_policy': 'skip'}
    c1, h1, b1 = hub.raw_text('POST', '/api/schedules', dict(base, run_at=past))
    c2, s2 = hub.post('/api/schedules', dict(base, run_at=future, name=P + 'sched-future'))
    ok_future = c2 in (200, 201)
    if ok_future:
        ST['ids']['schedule'] = s2['id']
        add_fixture('schedule', s2['id'], base['name'] + '-future')
    c3, h3, b3 = hub.raw_text('PUT', '/api/schedules/%s' % ST['ids'].get('schedule', 'x'),
                              dict(base, run_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 60))))
    ok_sched = (c1 == 400 and 'run_at must be in the future' in b1 and ok_future
                and c3 == 400 and 'run_at must be in the future' in b3)
    out['cases']['PAST_ONETIME'] = {'post_past': {'http': c1, 'body': b1[:160]},
                                    'post_future': {'http': c2, 'id': s2.get('id') if isinstance(s2, dict) else None},
                                    'put_changed_past': {'http': c3, 'body': b3[:160]}}
    record('OBS-20261014-PAST-ONETIME-SAVE-ENABLED', 'api', 'FIXED' if ok_sched else 'STILL_BROKEN',
           '过去 run_at 创建 -> 400 run_at must be in the future；未来正常；PUT 改为过去值同样 400' if ok_sched
           else '仍复现：%s' % out['cases']['PAST_ONETIME'],
           ['evidence/phase03-task-schedule.json'])
    ev_write('phase03-task-schedule.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: app + from_version
def phase_app():
    out = {'at_utc': now_utc(), 'cases': {}}
    # artifacts A1/A2 (tiny scripts used as app payloads)
    for label, content in (('A1', b'#!/bin/sh\necho qa-final-app-v1\nexec sleep 1000000\n'),
                           ('A2', b'#!/bin/sh\necho qa-final-app-v2\nexec sleep 1000000\n')):
        name = P + 'payload-' + label.lower()
        code, body = hub.upload_artifact(name, 'v1', name + '.bin', content)
        if code != 200:
            raise RuntimeError('artifact_upload_failed_%s_http_%s' % (label, code))
        ST['ids']['artifact_' + label] = body['id']
        add_fixture('artifact', body['id'], name)
    node_a = ST['children']['a']['node_id']
    ch_a = child_of('a')
    unit = 'nodesteer-' + P + 'app.service'
    opt = '/opt/' + P + 'app'
    binary = opt + '/bin/app'
    config = opt + '/config/app.env'
    app = {'name': P + 'app', 'description': 'final owned application',
           'artifact_id': ST['ids']['artifact_A1'], 'version': '1.0',
           'binary_path': binary, 'arguments': [], 'environment': {}, 'config': '',
           'config_path': config, 'unit_name': unit,
           'health_check': {'type': 'systemd', 'target': unit, 'timeout': 5, 'attempts': 2, 'interval': 1}}
    code, a = hub.post('/api/applications', app)
    if code not in (200, 201):
        raise RuntimeError('app_create_failed_%s_%s' % (code, a))
    ST['ids']['app'] = a['id']
    add_fixture('application', a['id'], app['name'])
    out['app'] = {'id': a['id'], 'http': code}
    # item 3: no bound nodes -> [] (raw bytes)
    c1, h1, b1 = hub.raw_text('GET', '/api/applications/%s/nodes' % a['id'])
    out['cases']['APP_NODES_EMPTY'] = {'http': c1, 'raw': b1.strip()[:40]}
    ok3 = c1 == 200 and b1.strip() == '[]'
    record('OBS-20261012-APP-NODES-NULL-SHAPE', 'api', 'FIXED' if ok3 else 'STILL_BROKEN',
           '无绑定节点时 GET /applications/{id}/nodes 原始响应为 []（非 null）' if ok3
           else '仍复现：raw=%r' % b1[:60], ['evidence/phase04-app.json'])
    # assign + deploy v1 (first install: from_version empty)
    code, r = hub.post('/api/applications/%s/assign' % a['id'], {'node_ids': [node_a]})
    out['assign'] = {'http': code}
    code, execs = hub.post('/api/applications/%s/deploy' % a['id'], {'node_ids': [node_a], 'operation': 'deploy'})
    if code != 200 or not execs:
        raise RuntimeError('deploy_v1_failed_%s_%s' % (code, execs))
    ex1 = wait_exec(execs[0]['id'], timeout=300)
    out['deploy_v1'] = {'status': (ex1 or {}).get('status'), 'error': (ex1 or {}).get('error')}
    time.sleep(2)
    probe1 = ch_a.app_state(a['id'], unit, binary, config, shas=())
    deps1 = probe1.get('deployments') or []
    # deploy v2: from_version must equal actually-installed 1.0
    app2 = dict(app, artifact_id=ST['ids']['artifact_A2'], version='2.0')
    code, up = hub.put('/api/applications/%s' % a['id'], app2)
    out['update_v2'] = {'http': code, 'version': (up or {}).get('version')}
    code, execs = hub.post('/api/applications/%s/deploy' % a['id'], {'node_ids': [node_a], 'operation': 'deploy'})
    if code != 200 or not execs:
        raise RuntimeError('deploy_v2_failed_%s_%s' % (code, execs))
    ex2 = wait_exec(execs[0]['id'], timeout=300)
    out['deploy_v2'] = {'status': (ex2 or {}).get('status'), 'error': (ex2 or {}).get('error')}
    time.sleep(2)
    probe2 = ch_a.app_state(a['id'], unit, binary, config, shas=())
    deps2 = [d for d in (probe2.get('deployments') or []) if d.get('application_id') == a['id']]
    rows = sorted(deps2, key=lambda d: d.get('started_at') or '')
    first = next((d for d in rows if d.get('to_version') == '1.0'), None)
    second = next((d for d in rows if d.get('to_version') == '2.0'), None)
    out['journal'] = {'v1_row': first, 'v2_row': second, 'all': rows[:6]}
    ok9 = (first is not None and (first.get('from_version') or '') == ''
           and second is not None and second.get('from_version') == '1.0'
           and second.get('to_version') == '2.0')
    record('OBS-20261016-JOURNAL-FROM-VERSION', 'api', 'FIXED' if ok9 else 'STILL_BROKEN',
           '部署 journal：首次 from_version 空、二次 from_version=1.0（实际安装版本）' if ok9
           else '仍复现：%s' % out['journal'],
           ['evidence/phase04-app.json'])
    ev_write('phase04-app.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: X16
X16_SCRIPT = r'''#!/bin/sh
S="${NODESTEER_PARAM_marker_secret}"
case "${NODESTEER_PARAM_probe}" in
  full) printf "%s\n" "$S" ;;
  split) printf "%s" "${S%????????}"; sleep 1.5; printf "%s\n" "${S#????????}" ;;
  cross) printf "%s" "${S#????????}"; printf "%s\n" "${S%????????}" >&2 ;;
  boundary) printf "%s" "${S%????????}"; head -c 200000 /dev/zero | tr '\0' 'x'; printf "\n"; printf "%s\n" "${S#????????}" ;;
  *) echo "probe=${NODESTEER_PARAM_probe}" ;;
esac
echo qa-final-x16-done
'''


def analyze_secret(marker, stdout, stderr):
    combined = stdout + stderr
    frags = sorted({marker[i:i + 8] for i in range(len(marker) - 7)})
    leaked = [f for f in frags if f in combined]
    return {'full_in_stdout': marker in stdout, 'full_in_stderr': marker in stderr,
            'full_in_combined': marker in combined,
            'redacted_count': stdout.count('[REDACTED]') + stderr.count('[REDACTED]'),
            'leaked_8byte_fragments': leaked[:6], 'leak_count': len(leaked),
            'stdout_len': len(stdout), 'stderr_len': len(stderr),
            'stdout_head': stdout[:60], 'stderr_head': stderr[:60]}


def phase_x16():
    out = {'at_utc': now_utc(), 'cases': {}}
    node_a = ST['children']['a']['node_id']
    sc = {'name': P + 'x16-script', 'description': 'final x16 owned script', 'interpreter': 'shell',
          'content': X16_SCRIPT, 'enabled': True,
          'parameters': [{'name': 'marker_secret', 'type': 'secret', 'required': True},
                         {'name': 'probe', 'type': 'string', 'required': True}],
          'environment': {}, 'working_dir': ''}
    sid = ST['ids'].get('script_x16')
    if sid:
        code, script = hub.put('/api/scripts/%s' % sid, sc)
        if code != 200:
            raise RuntimeError('x16_script_update_failed_%s_%s' % (code, script))
    else:
        code, script = hub.post('/api/scripts', sc)
        if code not in (200, 201):
            raise RuntimeError('x16_script_create_failed_%s_%s' % (code, script))
        ST['ids']['script_x16'] = script['id']
        add_fixture('script', script['id'], sc['name'])
    markers = {'split': 'QAFINAL-HALVES-%s-0123456789abcdef' % NONCE,
               'cross': 'QAFINAL-CROSS-%s-abcdef0123456789' % NONCE,
               'boundary': 'QAFINAL-BOUNDARY-%s-fedcba9876543210' % NONCE,
               'full': 'QAFINAL-FULL-%s-0011223344556677' % NONCE}
    task = {'name': P + 'x16-task', 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [node_a]},
            'parameters': sc['parameters'], 'param_values': {'marker_secret': markers['split']},
            'timeout': 60, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, t = hub.post('/api/tasks', task)
    if code not in (200, 201):
        raise RuntimeError('x16_task_create_failed_%s_%s' % (code, t))
    ST['ids']['task_x16'] = t['id']
    add_fixture('task', t['id'], task['name'])
    out['script_id'] = script['id']
    out['task_id'] = t['id']
    verdict = {}
    for probe in ('split', 'cross', 'boundary', 'full'):
        marker = markers[probe]
        # per-probe run: secret value is param_values; override for each probe value
        code, upd = hub.put('/api/tasks/%s' % t['id'], dict(task, param_values={'marker_secret': marker}))
        if code != 200:
            raise RuntimeError('x16_task_update_failed_%s' % code)
        code, run = hub.post('/api/tasks/%s/run' % t['id'], {'params': {'probe': probe}})
        exec_id = None
        if isinstance(run, list) and run:
            exec_id = run[0].get('id')
        elif isinstance(run, dict):
            exec_id = run.get('id') or (run.get('execution') or {}).get('id')
        if not exec_id:
            code, lst = hub.get('/api/executions?task_id=' + t['id'])
            if code == 200 and isinstance(lst, list) and lst:
                exec_id = lst[0].get('id')
        ex = wait_exec(exec_id, timeout=180) if exec_id else None
        code, chunks = hub.get('/api/executions/%s/logs' % hub.quote(exec_id))
        stdout = ''.join(c.get('chunk', '') for c in sorted(chunks or [], key=lambda c: (c.get('stream'), c.get('seq', 0)))
                         if c.get('stream') == 'stdout')
        stderr = ''.join(c.get('chunk', '') for c in sorted(chunks or [], key=lambda c: (c.get('stream'), c.get('seq', 0)))
                         if c.get('stream') == 'stderr')
        analysis = analyze_secret(marker, stdout, stderr)
        detail = json.dumps(ex or {}, ensure_ascii=False)
        analysis['full_in_execution_detail'] = marker in detail
        analysis['marker_len'] = len(marker)
        analysis['chunk_count'] = len(chunks or [])
        analysis['exec_status'] = (ex or {}).get('status')
        verdict[probe] = analysis
    out['cases']['X16'] = verdict
    ok = all(
        v['exec_status'] == 'SUCCESS'
        and not v['full_in_stdout'] and not v['full_in_stderr'] and not v['full_in_combined']
        and not v['full_in_execution_detail'] and v['leak_count'] == 0 and v['redacted_count'] >= 1
        for v in verdict.values())
    record('E2E-20261017-SECRET-SPLIT-VALUE-RECONSTRUCT', 'x16', 'FIXED' if ok else 'STILL_BROKEN',
           '四种拆片形态（同流前后段/跨流/大输出头尾/整值）日志均出现 [REDACTED] 且无任何 ≥8 字节片段可重组' if ok
           else '仍复现：%s' % {k: {kk: vv for kk, vv in v.items() if kk in
                                    ('full_in_combined', 'leak_count', 'leaked_8byte_fragments', 'redacted_count')}
                                for k, v in verdict.items()},
           ['evidence/phase05-x16.json'])
    ev_write('phase05-x16.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: transfers
def phase_transfers():
    out = {'at_utc': now_utc(), 'cases': {}}
    ch_a, ch_b = child_of('a'), child_of('b')
    node_a, node_b = ST['children']['a']['node_id'], ST['children']['b']['node_id']
    src = ch_a.root + '/share/xfer-src.txt'
    content = 'qa-final-xfer-%s\n' % NONCE
    ch_a.remote_py("import json,os\np=%(p)r\nos.makedirs(os.path.dirname(p),mode=0o700,exist_ok=True)\n"
                   "open(p,'w').write(%(c)r)\nprint(json.dumps({'written':p}))\n" % {'p': src, 'c': content})
    dst = ch_b.root + '/incoming/xfer-dst.txt'
    code, tr = hub.post('/api/transfers', {'source_node_id': node_a, 'source_path': src,
                                           'targets': [{'node_id': node_b, 'destination_path': dst}]})
    out['create_http'] = code
    if code != 200:
        raise RuntimeError('transfer_create_failed_%s_%s' % (code, tr))
    ST['ids']['transfer_happy'] = tr['id']
    add_fixture('transfer', tr['id'], 'happy a->b')
    deadline = time.time() + 180
    final = None
    while time.time() < deadline:
        c, final = hub.get('/api/transfers/' + tr['id'])
        if final.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
            break
        time.sleep(2)
    got = ch_b.remote_py("import json,os\np=%(p)r\nprint(json.dumps({'exists':os.path.isfile(p),'text':open(p).read() if os.path.isfile(p) else None}))\n" % {'p': dst})
    out['happy'] = {'final': {k: final.get(k) for k in ('id', 'status', 'error', 'sha256')},
                    'dest': got}
    # item 7: self transfer -> 400
    c7, h7, b7 = hub.raw_text('POST', '/api/transfers',
                              {'source_node_id': node_a, 'source_path': src,
                               'targets': [{'node_id': node_a, 'destination_path': src + '.self'}]})
    out['cases']['SELF_TRANSFER'] = {'http': c7, 'body': b7[:200]}
    ok7 = c7 == 400 and 'self transfer' in b7
    record('OBS-20261017-XFER-API-NO-SELF-TRANSFER-CHECK', 'api', 'FIXED' if ok7 else 'STILL_BROKEN',
           '自传（目标=源节点）-> 400 self transfer is not allowed' if ok7
           else '仍复现：%s' % out['cases']['SELF_TRANSFER'],
           ['evidence/phase06-transfers.json'])
    ev_write('phase06-transfers.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: disabled dispatch
def phase_disabled():
    out = {'at_utc': now_utc(), 'cases': {}}
    node_d = ST['children']['d']['node_id']
    code, r = hub.post('/api/nodes/%s/revoke-credential' % node_d, {})
    out['revoke'] = {'http': code, 'body': r}
    time.sleep(1)
    c2, nd = hub.get('/api/nodes/' + node_d)
    out['status_after'] = (nd or {}).get('status')
    # dedicated owned task targeting the disabled node (product path: UI Run Now uses the task target)
    task = {'name': P + 'task-disabled', 'type': 'command', 'command': 'echo never',
            'target': {'type': 'node', 'node_ids': [node_d]},
            'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
    code, t = hub.post('/api/tasks', task)
    if code not in (200, 201):
        raise RuntimeError('disabled_task_create_failed_%s_%s' % (code, t))
    ST['ids']['task_disabled'] = t['id']
    add_fixture('task', t['id'], task['name'])
    out['task'] = {'id': t['id'], 'http': code}
    code, run = hub.post('/api/tasks/%s/run' % t['id'], {})
    body = json.dumps(run, ensure_ascii=False) if not isinstance(run, str) else run
    out['dispatch'] = {'http': code, 'body': body[:220]}
    ok = (out['status_after'] == 'disabled' and code == 400
          and 'is disabled' in body and 'not online' not in body)
    record('OBS-20261017-EXEC-DISABLED-BRANCH-UNREACHABLE', 'api', 'FIXED' if ok else 'STILL_BROKEN',
           '禁用节点派发 -> 400 node X is disabled（不是 not online 文案）' if ok
           else '仍复现：%s' % out['dispatch'], ['evidence/phase07-disabled.json'])
    ev_write('phase07-disabled.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- phase: G23 handshake
MOCK_WS = r'''
import base64, hashlib, json, socket, struct, sys, time
PORT = int(sys.argv[1]); ACK_DELAY = float(sys.argv[2]); HOLD = float(sys.argv[3]); LOG = sys.argv[4]
GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

def log(msg):
    with open(LOG, 'a') as f:
        f.write('%.3f %s\n' % (time.time(), msg))

def read_exact(conn, n):
    buf = b''
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise EOFError
        buf += chunk
    return buf

def read_frame(conn):
    b0, b1 = read_exact(conn, 2)
    opcode = b0 & 0x0f
    masked = b1 & 0x80
    ln = b1 & 0x7f
    if ln == 126:
        ln = struct.unpack('>H', read_exact(conn, 2))[0]
    elif ln == 127:
        ln = struct.unpack('>Q', read_exact(conn, 8))[0]
    mask = read_exact(conn, 4) if masked else None
    data = read_exact(conn, ln) if ln else b''
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data

def send_frame(conn, data, opcode=1):
    header = bytes([0x80 | opcode])
    ln = len(data)
    if ln < 126:
        header += bytes([ln])
    elif ln < 65536:
        header += bytes([126]) + struct.pack('>H', ln)
    else:
        header += bytes([127]) + struct.pack('>Q', ln)
    conn.sendall(header + data)

srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(('127.0.0.1', PORT))
srv.listen(1)
log('listening %d' % PORT)
conn, addr = srv.accept()
log('accepted %s' % (addr,))
req = b''
while b'\r\n\r\n' not in req:
    chunk = conn.recv(4096)
    if not chunk:
        log('closed during upgrade'); sys.exit(0)
    req += chunk
headers = {}
for line in req.decode('latin1').split('\r\n')[1:]:
    if ':' in line:
        k, v = line.split(':', 1)
        headers[k.strip().lower()] = v.strip()
key = headers.get('sec-websocket-key', '')
accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
conn.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
              'Connection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n' % accept).encode())
log('upgraded')
ack_at = time.time() + ACK_DELAY
hello_seen = False
while time.time() < ack_at:
    conn.settimeout(max(0.2, ack_at - time.time()))
    try:
        opcode, data = read_frame(conn)
    except socket.timeout:
        continue
    except EOFError:
        log('client closed before ack'); sys.exit(0)
    if opcode == 9:
        send_frame(conn, data, opcode=10)
    elif opcode == 1:
        try:
            env = json.loads(data.decode('utf-8', 'replace'))
            log('msg %s' % env.get('type'))
            if env.get('type') == 'HELLO':
                hello_seen = True
        except Exception:
            log('non-json frame %d' % len(data))
ack = {'type': 'HELLO_ACK', 'id': 'mock-ack', 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
       'payload': {'accepted': True}}
send_frame(conn, json.dumps(ack).encode())
log('HELLO_ACK accepted sent hello_seen=%s' % hello_seen)
conn.settimeout(5)
end = time.time() + HOLD
while time.time() < end:
    try:
        opcode, data = read_frame(conn)
    except socket.timeout:
        continue
    except Exception:
        log('closed after ack')
        break
    if opcode == 9:
        send_frame(conn, data, opcode=10)
    elif opcode == 1:
        try:
            log('msg %s' % json.loads(data.decode('utf-8', 'replace')).get('type'))
        except Exception:
            pass
log('done')
'''


def phase_g23():
    out = {'at_utc': now_utc(), 'case': ['OBS-20261016-G23-RECONNECT-WINDOW']}
    if 'g' not in ST['children']:
        # first start against the real hub: creates the owned store schema and a synced node record
        ch = enroll_child('g', '-g', 'n1', start=True)
        wait_node_online(ST['children']['g']['node_id'])
    else:
        ch = child_of('g')
    meta = ST['children']['g']
    node_g = meta['node_id']
    task_id = P + 'g23-task'
    sch_id = P + 'g23-sched'
    # stop any previous run, then inject owned task+schedule into the owned local store
    try:
        out['pre_stop'] = ch.stop()
    except Exception as exc:  # noqa
        out['pre_stop'] = {'error': str(exc)}
    time.sleep(1)
    past = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 600))
    inject = r'''
import json, os, sqlite3
data = %(data)r
task_id = %(task)r
sch_id = %(sch)r
node_id = %(node)r
past = %(past)r
db = os.path.join(data, 'state.db')
conn = sqlite3.connect(db)
task = {'id': task_id, 'name': task_id, 'type': 'command', 'command': 'echo qa-final-g23-ran',
        'target': {'type': 'node', 'node_ids': [node_id]}, 'parameters': [], 'param_values': {},
        'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True, 'revision': 1}
sch = {'id': sch_id, 'name': sch_id, 'task_id': task_id, 'type': 'interval', 'interval_sec': 1,
       'timezone': 'UTC', 'enabled': True, 'execution_owner': 'agent',
       'offline_policy': 'hub_online_required', 'misfire_policy': 'skip', 'created_at': past, 'revision': 1}
conn.execute('INSERT OR REPLACE INTO tasks (id, definition_json, revision, enabled) VALUES (?,?,?,1)',
             (task_id, json.dumps(task), 1))
conn.execute('INSERT OR REPLACE INTO schedules (id, definition_json, revision, enabled) VALUES (?,?,?,1)',
             (sch_id, json.dumps(sch), 1))
conn.commit()
n = conn.execute("SELECT COUNT(*) FROM executions WHERE task_id=?", (task_id,)).fetchone()[0]
print(json.dumps({'tasks': 1, 'schedules': 1, 'executions_before': n}))
''' % {'data': ch.data, 'task': task_id, 'sch': sch_id, 'node': node_g, 'past': past}
    out['inject'] = ch.remote_py(inject)
    # mock WS server on the child host: hold HELLO_ACK for ACK_DELAY seconds
    port = 19100 + (int(NONCE[:4], 16) % 400)
    ack_delay, hold = 25.0, 120.0
    mock_log = ch.root + '/mock-ws.log'
    mock_py = ch.root + '/mock_ws.py'
    b64 = base64.b64encode(MOCK_WS.encode()).decode()
    setup = (
        "import base64,json,os\n"
        "root=%(root)r; mock=%(mock)r; log=%(log)r\n"
        "open(mock,'w').write(base64.b64decode(%(b64)r).decode())\n"
        "open(log,'w').close()\n"
        "print(json.dumps({'ok':True}))\n" % {'root': ch.root, 'mock': mock_py, 'log': mock_log, 'b64': b64})
    out['mock_setup'] = ch.remote_py(setup)
    start_mock = (
        "import json,os,subprocess,time\n"
        "p=subprocess.Popen(['setsid','nohup','python3',%(mock)r,str(%(port)d),'%(delay)s','%(hold)s',%(log)r],\n"
        "  stdout=open(%(log)r,'a'),stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)\n"
        "time.sleep(1.2)\n"
        "q=subprocess.run(['pgrep','-f',%(mock)r],capture_output=True,text=True)\n"
        "print(json.dumps({'launcher':p.pid,'procs':q.stdout.split()}))\n"
        % {'mock': mock_py, 'port': port, 'delay': ack_delay, 'hold': hold, 'log': mock_log})
    out['mock_start'] = ch.remote_py(start_mock)
    # start the owned child against the mock
    ch.write_config_and_start('ws://127.0.0.1:%d' % port)
    t_start = time.time()
    out['t_start'] = t_start
    # window observation at ~12s: no HELLO_ACK yet -> hub_online_required schedule must NOT trigger
    time.sleep(12)
    obs_window = ch.remote_py(
        "import json,sqlite3,os\n"
        "db=os.path.join(%(data)r,'state.db')\n"
        "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')\n"
        "rows=[{'id':r[0],'status':r[1],'offline':r[2],'synced':r[3],'scheduled_time':r[4],'start_time':r[5]} "
        "for r in c.execute('SELECT id,status,offline,synced,scheduled_time,start_time FROM executions WHERE task_id=?',(%(task)r,))]\n"
        "print(json.dumps({'executions':rows}))\n" % {'data': ch.data, 'task': task_id})
    out['window_probe'] = {'at_offset_s': round(time.time() - t_start, 1), 'executions': obs_window.get('executions')}
    # wait for post-ACK trigger (ACK at ~25s)
    deadline = time.time() + 60
    after = None
    while time.time() < deadline:
        time.sleep(4)
        after = ch.remote_py(
            "import json,sqlite3,os\n"
            "db=os.path.join(%(data)r,'state.db')\n"
            "c=sqlite3.connect('file:'+db+'?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')\n"
            "rows=[{'id':r[0],'status':r[1],'offline':r[2],'synced':r[3],'scheduled_time':r[4],'start_time':r[5]} "
            "for r in c.execute('SELECT id,status,offline,synced,scheduled_time,start_time FROM executions WHERE task_id=?',(%(task)r,))]\n"
            "print(json.dumps({'executions':rows}))\n" % {'data': ch.data, 'task': task_id})
        if after.get('executions'):
            break
    out['after_ack_probe'] = {'at_offset_s': round(time.time() - t_start, 1), 'executions': after.get('executions')}
    # agent log evidence
    log_tail = ch.log_tail(200000)
    triggers = [l for l in (log_tail.get('tail') or '').splitlines() if 'schedule trigger' in l]
    out['agent_trigger_lines'] = triggers[-5:]
    mock_log_tail = ch.remote_py("import json\nprint(json.dumps({'tail':open(%(log)r).read()[-2000:]}))\n" % {'log': mock_log})
    out['mock_log'] = mock_log_tail.get('tail')
    win_execs = out['window_probe']['executions'] or []
    after_execs = out['after_ack_probe']['executions'] or []
    out['verdict'] = {
        'window_closed_no_trigger': len(win_execs) == 0,
        'triggered_after_ack': len(after_execs) >= 1,
        'post_ack_offline_false': all(e.get('offline') == 0 for e in after_execs),
        'mock_hello_seen': 'hello_seen=True' in (out['mock_log'] or ''),
    }
    ok = all(out['verdict'].values())
    record('OBS-20261016-G23-RECONNECT-WINDOW', 'g23', 'FIXED' if ok else 'STILL_BROKEN',
           'HELLO_ACK 前（窗口内 12s 探测）hub_online_required 调度零触发；ACK accepted 后正常触发且 offline=0' if ok
           else '仍复现：%s' % out['verdict'],
           ['evidence/phase08-g23.json'], out)
    # stop child + mock, then clean owned dirs at cleanup stage (keep DB for evidence)
    try:
        out['stop_child'] = ch.stop()
    except Exception as exc:  # noqa
        out['stop_child'] = {'error': str(exc)}
    kill_mock = ch.remote_py(
        "import json,os,signal,subprocess,time\n"
        "mock=%(mock)r\n"
        "p=subprocess.run(['pgrep','-f',mock],capture_output=True,text=True)\n"
        "pids=[int(x) for x in p.stdout.split() if x.isdigit()]\n"
        "for pid in pids:\n"
        "    try: os.kill(pid, signal.SIGTERM)\n"
        "    except Exception: pass\n"
        "time.sleep(0.5)\n"
        "q=subprocess.run(['pgrep','-f',mock],capture_output=True,text=True)\n"
        "print(json.dumps({'killed':pids,'remaining':q.stdout.split()}))\n" % {'mock': mock_py})
    out['kill_mock'] = kill_mock
    ev_write('phase08-g23.json', out)
    dump_results()
    return out


def main():
    hub.login()
    phase = sys.argv[1] if len(sys.argv) > 1 else 'all'
    phases = {'children': phase_children, 'api': phase_api_contract,
              'task': phase_task_schedule, 'app': phase_app, 'x16': phase_x16,
              'transfers': phase_transfers, 'disabled': phase_disabled, 'g23': phase_g23}
    order = ['children', 'task', 'transfers', 'api', 'disabled', 'app', 'x16', 'g23']
    if phase == 'all':
        for name in order:
            print('==== phase', name, flush=True)
            phases[name]()
    else:
        phases[phase]()
    dump_results()


if __name__ == '__main__':
    main()
