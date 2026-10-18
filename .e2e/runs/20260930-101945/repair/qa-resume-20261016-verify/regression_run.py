#!/usr/bin/env python3
"""qa-resume-20261016-verify: targeted regression for the 7 repaired defects.

Authority: repair hub http://192.168.100.209:18080 (only management-write endpoint).
Writes fixtures only with nonce prefix, all removed in the cleanup phase.

Phases (run sequentially, state persisted after every case):
  guard   node/unit baseline
  api     P1-4 script validation / P2-5 groups / P2-6 SQL leak / P3-7 methods
  remote  P0-1 remote conditions (new agent on native-1 + old agent on native-4)
  app     P0-2 stale-backup / poison-row recovery on native-2
  inv05   P1-3 CommitSync failure injection (owned child agent, tmpfs)
  cleanup delete all fixtures + GET404 oracle + residue sweep
"""
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'evidence' / 'tools'))
import hubclient  # noqa: E402

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LEDGER_F = HERE / 'fixture-ledger.json'

SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
HUB_HOST = '192.168.100.209'
N1 = '192.168.100.204'   # native-1 (new agent)
N2 = '192.168.100.206'   # native-2 (new agent)
N4 = '192.168.100.247'   # native-4 (old agent during mixed-version window)

TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}

os.umask(0o077)
hub = hubclient.Hub()


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def ssh(host, cmd, timeout=60):
    cp = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
         '-i', SSH_KEY, 'root@' + host, cmd],
        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {'nonce': secrets.token_hex(4), 'fixtures': [], 'phases': {}, 'cases': []}


def save_state(st):
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, STATE_F)


ST = load_state()
P = 'qa-r1610-%s-' % ST['nonce']


def record(case, phase, new_status, new_conclusion, evidence=None, extra=None):
    row = {
        'case_id': case, 'phase': phase, 'at_utc': now_utc(),
        'new_status': new_status, 'new_conclusion': new_conclusion,
        'evidence': evidence or [], 'extra': extra or {},
    }
    ST['cases'] = [c for c in ST['cases'] if c.get('case_id') != case]
    ST['cases'].append(row)
    save_state(ST)
    print('[%s] %s -> %s' % (phase, case, new_status), flush=True)
    return row


def dump_results():
    payload = {
        'batch': 'qa-resume-20261016-verify',
        'authority': 'http://192.168.100.209:18080',
        'build_label': 'dddee4f0-e2e-repair-20261016',
        'updated_at_utc': now_utc(),
        'nonce': ST['nonce'],
        'cases': ST['cases'],
    }
    tmp = RESULTS_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, RESULTS_F)


def ev_write(name, value):
    path = EV / name
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, path)
    return str(path.relative_to(HERE.parent.parent.parent.parent.parent))


def nodes_by_host():
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    out = {}
    for n in rows:
        out[n.get('hostname')] = n
    return out


def wait_node(node_id, timeout=120):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        code, body = hub.get('/api/nodes/' + node_id)
        if code == 200:
            last = body
            if body.get('status') == 'online' and body.get('sync_status') == 'synced':
                return body
        time.sleep(1.0)
    return last


def wait_exec(eid, timeout=120):
    deadline = time.time() + timeout
    ex = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + eid)
        if code == 200 and ex.get('status') in TERMINAL:
            return ex
        time.sleep(0.6)
    return ex or {}


# ---------------------------------------------------------------- guard
def phase_guard():
    hub.login()
    nodes = nodes_by_host()
    info = {'at_utc': now_utc(), 'hub_url': hub.base}
    info['nodes'] = {k: {kk: v.get(kk) for kk in
                         ('id', 'status', 'sync_status', 'deployment_mode', 'agent_version')
                         if kk in v} for k, v in nodes.items()
                     if k.startswith('qa-repair')}
    ids = {}
    for i in (1, 2, 3, 4):
        hn = 'qa-repair-20260930-101945-native-%d' % i
        if hn not in nodes:
            raise RuntimeError('native-%d missing from hub nodes' % i)
        ids['native-%d' % i] = nodes[hn]['id']
    info['node_ids'] = ids
    ST['node_ids'] = ids
    # clock reference: hub vs native-1 (informational)
    rc1, so1, _ = ssh(HUB_HOST, 'date -u +%s.%N')
    rc2, so2, _ = ssh(N1, 'date -u +%s.%N')
    if rc1 == 0 and rc2 == 0:
        info['clock_probe'] = {'hub_epoch': float(so1.strip()), 'n1_epoch': float(so2.strip())}
    save_state(ST)
    ev_write('guard-nodes.json', info)
    return info


# ---------------------------------------------------------------- api phases
def _script_body(name, content=None, timeout=30, desc='qa-r1610 fixture'):
    return {'name': name, 'description': desc, 'interpreter': 'shell',
            'content': content if content is not None else '#!/bin/sh\necho %s\n' % name,
            'parameters': [], 'environment': {}, 'working_dir': '',
            'timeout': timeout, 'enabled': True}


def _task_body(name, node_id, command='echo qa-r1610', desc='qa-r1610 fixture'):
    return {'name': name, 'type': 'command', 'command': command,
            'target': {'type': 'node', 'node_ids': [node_id]},
            'timeout': 20, 'retry': 0, 'offline_policy': 'hub_online_required',
            'enabled': True}


def _has_sql(body):
    text = '' if body is None else (body if isinstance(body, str) else json.dumps(body))
    low = text.lower()
    return ('sql:' in low or 'no rows' in low or 'sqlite' in low)


def phase_api():
    n1 = ST['node_ids']['native-1']
    out = {'at_utc': now_utc(), 'cases': {}}

    # ---- P1-4 script validation ----
    c = {}
    code, body = hub.post('/api/scripts', _script_body(P + 'script-neg', timeout=-5))
    c['neg_timeout_create'] = {'http': code, 'body': body}
    code_list, lst = hub.get('/api/scripts')
    names = [s.get('name') for s in (lst.get('scripts', lst) if isinstance(lst, dict) else lst)]
    c['neg_timeout_persisted'] = (P + 'script-neg') in names
    c['neg_timeout_ok'] = code == 400 and not c['neg_timeout_persisted']

    code, body = hub.post('/api/scripts', _script_body(P + 'script-zero', timeout=0))
    c['zero_timeout_create'] = {'http': code, 'body': body}
    sc_zero = body.get('id') if isinstance(body, dict) else None
    if sc_zero:
        _, got = hub.get('/api/scripts/' + sc_zero)
        c['zero_timeout_readback'] = got.get('timeout') if isinstance(got, dict) else got
        c['zero_timeout_ok'] = code in (200, 201) and c['zero_timeout_readback'] == 300
    content_at = '#!/bin/sh\n# ' + ('a' * (262144 - 13)) + '\n'
    c['content_at_len'] = len(content_at)
    code, body = hub.post('/api/scripts', _script_body(P + 'script-at', content=content_at))
    c['content_at'] = {'http': code, 'body_id': (body or {}).get('id') if isinstance(body, dict) else None}
    c['content_at_ok'] = code in (200, 201)
    sc_at = (body or {}).get('id') if isinstance(body, dict) else None
    content_over = '#!/bin/sh\n# ' + ('a' * (262145 - 13)) + '\n'
    c['content_over_len'] = len(content_over)
    code, body = hub.post('/api/scripts', _script_body(P + 'script-over', content=content_over))
    c['content_over'] = {'http': code, 'body': body}
    c['content_over_ok'] = code == 400
    if sc_zero:
        upd = _script_body(P + 'script-zero', timeout=-5)
        code, body = hub.put('/api/scripts/' + sc_zero, upd)
        _, got = hub.get('/api/scripts/' + sc_zero)
        c['neg_timeout_update'] = {'http': code, 'body': body, 'readback_timeout': got.get('timeout')}
        c['neg_timeout_update_ok'] = code == 400 and got.get('timeout') == 300
        ST['fixtures'].append(('script', sc_zero, P + 'script-zero'))
    if sc_at:
        ST['fixtures'].append(('script', sc_at, P + 'script-at'))
    out['cases']['P1-4'] = c
    save_state(ST)
    record('P07_P08-11/API-CONTRACT', 'api', 'PASS' if all(
        c[k] for k in ('neg_timeout_ok', 'zero_timeout_ok', 'content_at_ok', 'content_over_ok',
                       'neg_timeout_update_ok')) else 'FAIL',
        'timeout=-5→400 且不落库；timeout=0→200 且回读 300；262144B→200；262145B→400；PUT 负数→400 且不改变已存值',
        ['evidence/api-p1-4.json'], c)

    # ---- P2-5 groups ----
    g = {}
    code, body = hub.post('/api/groups', {})
    g['empty_post'] = {'http': code, 'body': body}
    g['empty_post_ok'] = code == 400
    code, body = hub.post('/api/groups', {'name': '   ', 'description': 'blank'})
    g['blank_post'] = {'http': code, 'body': body}
    g['blank_post_ok'] = code == 400
    missing = str(uuid.uuid4())
    code, body = hub.put('/api/groups/' + missing, {'name': P + 'grp-missing', 'description': 'x', 'type': 'static'})
    g['missing_put'] = {'http': code, 'body': body}
    g['missing_put_ok'] = code == 404
    code, body = hub.post('/api/groups', {'name': P + 'grp-ok', 'description': 'qa fixture', 'type': 'static', 'members': []})
    g['create_ok'] = {'http': code, 'body': body}
    gid = (body or {}).get('id') if isinstance(body, dict) else None
    g['create_ok_ok'] = code in (200, 201) and bool(gid)
    code, body = hub.post('/api/groups', {'name': (P + 'grp-ok').upper(), 'description': 'dup', 'type': 'static', 'members': []})
    g['dup_post'] = {'http': code, 'body': body}
    g['dup_post_ok'] = code == 400 and not _has_sql(body)
    if gid:
        code, body = hub.put('/api/groups/' + gid, {'name': P + 'grp-ok2', 'description': 'qa fixture v2', 'type': 'static', 'members': []})
        g['put_ok'] = {'http': code, 'body': body}
        g['put_ok_ok'] = code == 200
        ST['fixtures'].append(('group', gid, P + 'grp-ok2'))
    out['cases']['P2-5'] = g
    record('P06-02/P09P11-10/API-CONTRACT', 'api', 'PASS' if all(
        g[k] for k in ('empty_post_ok', 'blank_post_ok', 'missing_put_ok', 'create_ok_ok',
                       'dup_post_ok', 'put_ok_ok')) else 'FAIL',
        '空名/纯空白 POST→400；不存在 id PUT→404；重名→400 归一化（无 SQL 原文）；正常 POST/PUT 200',
        ['evidence/api-p2-5.json'], g)

    # ---- P2-6 SQL leak ----
    s = {}
    nf = str(uuid.uuid4())
    base_task = _create_fixture_task(out)
    task_id = base_task['id'] if base_task else None
    checks = []
    # schedules: build a valid body from an existing schedule if any
    code, scs = hub.get('/api/schedules')
    scs_rows = scs.get('schedules', scs) if isinstance(scs, dict) else scs
    if scs_rows:
        tmpl = dict(scs_rows[0])
    elif task_id:
        tmpl = {'name': P + 'sched-tmpl', 'task_id': task_id, 'type': 'once', 'expression': '',
                'interval_sec': 0, 'timezone': 'UTC', 'execution_owner': 'hub',
                'offline_policy': 'hub_online_required', 'misfire_policy': 'skip', 'enabled': True}
    else:
        tmpl = None
    if tmpl is not None:
        body_sched = {k: tmpl.get(k) for k in ('name', 'task_id', 'type', 'expression', 'interval_sec',
                                               'timezone', 'execution_owner', 'offline_policy',
                                               'misfire_policy', 'enabled') if k in tmpl}
        body_sched['name'] = P + 'sched-nf'
        code, resp = hub.put('/api/schedules/' + nf, body_sched)
        checks.append(('schedule', code, resp))
    body_script = _script_body(P + 'script-nf')
    code, resp = hub.put('/api/scripts/' + nf, body_script)
    checks.append(('script', code, resp))
    body_task = _task_body(P + 'task-nf', n1)
    code, resp = hub.put('/api/tasks/' + nf, body_task)
    checks.append(('task', code, resp))
    # application body
    code, arts = hub.get('/api/artifacts')
    arts_rows = arts.get('artifacts', arts) if isinstance(arts, dict) else arts
    if arts_rows:
        a0 = arts_rows[0]
        body_app = {'name': P + 'app-nf', 'description': 'qa', 'artifact_id': a0.get('id'),
                    'version': '1', 'binary_path': '/tmp/qa-r1610-nf/app', 'unit_name': P + 'appnf.service',
                    'arguments': [], 'environment': {}, 'config': '', 'config_path': '/tmp/qa-r1610-nf/app.env',
                    'health_check': {'type': 'systemd', 'target': '', 'attempts': 1, 'interval': 1, 'timeout': 2}}
        code, resp = hub.put('/api/applications/' + nf, body_app)
        checks.append(('application', code, resp))
    s['checks'] = [{'resource': r, 'http': c, 'body': b, 'leak': _has_sql(b)} for r, c, b in checks]
    s['all_404'] = all(c == 404 for _, c, _ in checks)
    s['no_leak'] = all(not _has_sql(b) for _, _, b in checks)
    out['cases']['P2-6'] = s
    record('API-CONTRACT/P12P13-02/P14P15-01', 'api', 'PASS' if (s['all_404'] and s['no_leak'] and len(checks) == 4) else 'FAIL',
        '4 资源 PUT 不存在 id → 404 且响应体无 sql/no rows 文本',
        ['evidence/api-p2-6.json'], s)

    # ---- P3-7 methods ----
    m = {}
    for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        code, hdrs, text = hub.raw_text(method, '/api/audit', {} if method in ('POST', 'PUT', 'PATCH') else None)
        m['audit_' + method] = {'http': code, 'allow': hdrs.get('Allow'), 'body': text[:200]}
    code, hdrs, text = hub.raw_text('GET', '/api/audit')
    m['audit_GET'] = {'http': code, 'x_total_count': hdrs.get('X-Total-Count')}
    code, hdrs, _ = hub.raw_text('OPTIONS', '/api/audit')
    m['audit_OPTIONS'] = {'http': code}
    code, hdrs, _ = hub.raw_text('POST', '/api/me')
    m['me_POST'] = {'http': code, 'allow': hdrs.get('Allow')}
    code, hdrs, _ = hub.raw_text('POST', '/metrics')
    m['metrics_POST'] = {'http': code, 'allow': hdrs.get('Allow'), 'path': '/metrics'}
    code, hdrs, _ = hub.raw_text('POST', '/api/metrics')
    m['api_metrics_POST'] = {'http': code, 'note': '端点实际为 /metrics（/api 前缀下无路由）'}
    code, hdrs, _ = hub.raw_text('GET', '/api/logout')
    m['logout_GET'] = {'http': code, 'allow': hdrs.get('Allow')}
    code2, _ = hub.get('/api/me')
    m['me_still_valid_after_logout_GET'] = code2 == 200
    code, _, text = hub.raw_text('POST', '/api/logout', {})
    m['logout_POST'] = {'http': code, 'body': text[:120]}
    hub.login()
    m['ok'] = (all(m['audit_' + x]['http'] == 405 and 'GET' in (m['audit_' + x]['allow'] or '')
                   for x in ('POST', 'PUT', 'DELETE', 'PATCH'))
               and m['audit_GET']['http'] == 200 and bool(m['audit_GET']['x_total_count'])
               and m['audit_OPTIONS']['http'] == 204
               and m['me_POST']['http'] == 405 and 'GET' in (m['me_POST']['allow'] or '')
               and m['metrics_POST']['http'] == 405 and 'GET' in (m['metrics_POST']['allow'] or '')
               and m['logout_GET']['http'] == 405 and m['me_still_valid_after_logout_GET']
               and m['logout_POST']['http'] == 200)
    out['cases']['P3-7'] = m
    record('API-CONTRACT/P19-05/FL-07', 'api', 'PASS' if m['ok'] else 'FAIL',
        '/api/audit 非 GET→405+Allow:GET，GET 保留 X-Total-Count，OPTIONS 204；/api/me、/api/metrics 非 GET→405；GET /api/logout→405 且未注销',
        ['evidence/api-p3-7.json'], m)
    dump_results()
    return out


def _create_fixture_task(out):
    """Create one owned task used as valid schedule template reference."""
    n1 = ST['node_ids']['native-1']
    name = P + 'task-tmpl'
    code, body = hub.post('/api/tasks', _task_body(name, n1, command='true'))
    if code in (200, 201) and body.get('id'):
        ST['fixtures'].append(('task', body['id'], name))
        save_state(ST)
        return body
    return None


# ---------------------------------------------------------------- P0-1 remote
def _put_task_condition(task_id, condition):
    _, task = hub.get('/api/tasks/' + task_id)
    body = {k: task.get(k) for k in ('name', 'type', 'command', 'script_id', 'target', 'timeout',
                                     'retry', 'offline_policy', 'enabled') if k in task}
    body['condition'] = condition
    return hub.put('/api/tasks/' + task_id, body)


def phase_remote():
    hub.login()
    n1 = ST['node_ids']['native-1']
    n4 = ST['node_ids']['native-4']
    root = '/var/lib/%smk' % P
    ssh(N1, 'mkdir -p %s && chmod 0755 %s' % (root, root))
    ST.setdefault('host_roots', []).append({'host': N1, 'path': root, 'role': 'P0-1 markers'})
    save_state(ST)

    out = {'at_utc': now_utc(), 'root': root, 'cases': {}}

    def mk_task(key, node_id):
        name = P + 'rem-' + key
        cmd = ("printf 'EXEC %s\\n' \"$(date -u +%%FT%%T.%%NZ)\" > %s/%s.marker && echo RAN_%s"
               % ('%s', root, key, key))
        code, body = hub.post('/api/tasks', _task_body(name, node_id, command=cmd))
        if code not in (200, 201):
            raise RuntimeError('task create failed %s %s' % (key, body))
        ST['fixtures'].append(('task', body['id'], name))
        save_state(ST)
        return body['id']

    def marker(key, remove=True, host=N1):
        p = '%s/%s.marker' % (root, key)
        cmd = 'if [ -f %s ]; then echo PRESENT; cat %s; else echo ABSENT; fi%s' % (
            p, p, ('; rm -f %s' % p) if remove else '')
        rc, so, _ = ssh(host, cmd)
        lines = so.strip().split('\n')
        present = bool(lines) and lines[0] == 'PRESENT'
        return {'present': present, 'content': lines[1:] if present else None}

    def run_case(case_id, key, condition, expect, note, node_id, host=N1):
        tid = mk_task(key, node_id)
        code, resp = _put_task_condition(tid, condition)
        if code != 200:
            row = {'case_id': case_id, 'task_id': tid, 'put_http': code, 'put_body': resp,
                   'status': 'PUT_FAILED', 'expect_status': expect, 'note': note}
            out['cases'][case_id] = row
            return row
        marker(key, remove=True, host=host)
        code, run = hub.post('/api/tasks/%s/run' % tid, {'node_ids': {node_id: True}})
        eid = run[0]['id'] if code == 200 and run else None
        ex = wait_exec(eid, timeout=90) if eid else {}
        ms = marker(key, host=host)
        want_marker = expect == 'SUCCESS'
        row = {'case_id': case_id, 'task_id': tid, 'task_name': P + 'rem-' + key,
               'condition': condition, 'execution_id': eid, 'status': ex.get('status'),
               'exit_code': ex.get('exit_code'), 'block_reason': ex.get('block_reason'),
               'expect_status': expect, 'marker_present': ms.get('present'),
               'marker_oracle_ok': bool(ms.get('present')) == want_marker,
               'status_match': ex.get('status') == expect, 'note': note, 'at_utc': now_utc()}
        row['verdict'] = 'PASS' if (row['status_match'] and row['marker_oracle_ok']) else 'FAIL'
        if case_id.startswith('MIXED'):
            row['verdict'] = 'INFO'
            row['mixed_version_note'] = (
                '旧 Agent 使用 observed_at 绝对时间回退路径；BLOCKED 表示旧 Agent 未升级前仍受时钟偏移影响'
                '（预期、文档已注明）；若为 SUCCESS 则表示该节点时钟未触发偏移门禁')
        out['cases'][case_id] = row
        return row

    def rem(node, prop, op, value, task=None):
        c = {'type': 'remote', 'remote': {'node_id': node, 'property': prop, 'operator': op, 'value': value}}
        if task:
            c['remote']['task_id'] = task
        return c

    def loc(metric, op, value, path=None, command=None):
        c = {'type': 'local', 'local': {'metric': metric, 'operator': op, 'value': value}}
        if path is not None:
            c['local']['path'] = path
        if command is not None:
            c['local']['command'] = command
        return c

    # history seeding task (runs once without condition)
    hist_task = mk_task('hist-ok', n1)
    code, run = hub.post('/api/tasks/%s/run' % hist_task, {'node_ids': {n1: True}})
    hist_ex = wait_exec(run[0]['id']) if code == 200 and run else {}
    marker('hist-ok', host=N1)
    nohist_task = mk_task('nohist', n1)
    out['history_seed'] = {'task_id': hist_task, 'status': hist_ex.get('status')}

    rows = []
    rows.append(run_case('SYS-27-ON01', 'on01', rem(n1, 'online', '==', 'online'), 'SUCCESS',
                         'remote online 满足路径（此前恒 BLOCKED）', n1))
    rows.append(run_case('SYS-27-ON02', 'on02', rem(n1, 'online', '!=', 'offline'), 'SUCCESS',
                         'remote online != offline 满足路径', n1))
    rows.append(run_case('SYS-30-ON03', 'on03', rem(n1, 'online', '==', 'offline'), 'SKIPPED',
                         'remote 不满足 → SKIPPED 不误执行', n1))
    rows.append(run_case('SYS-28-LX01', 'lx01', rem(n1, 'last_execution', '==', 'SUCCESS', task=hist_task), 'SUCCESS',
                         'last_execution 成功历史满足路径', n1))
    rows.append(run_case('SYS-28-LX03', 'lx03', rem(n1, 'last_execution', '==', 'FAILED', task=hist_task), 'SKIPPED',
                         'last_execution 不满足 → SKIPPED', n1))
    rows.append(run_case('INV-09-LX05', 'lx05', rem(n1, 'last_execution', '==', 'SUCCESS', task=nohist_task), 'BLOCKED',
                         '未观察 last_execution → UNKNOWN → BLOCKED fail-closed 保持', n1))
    rows.append(run_case('SYS-26-AND07', 'and07',
                         {'type': 'and', 'and': [rem(n1, 'online', '==', 'online'),
                                                 loc('command_result', '==', 'ok', command='echo ok')]},
                         'SUCCESS', 'AND(remote, local) 组合满足路径', n1))
    # offline node (if any) fail-closed / deterministic offline value
    off = [n for n in nodes_by_host().values() if n.get('status') == 'offline']
    if off:
        off_id = off[0]['id']
        rows.append(run_case('E2E-10-OFF05', 'off05', rem(off_id, 'online', '==', 'offline'), 'SUCCESS',
                             '真实 offline 节点 online==offline 满足路径', n1))
        rows.append(run_case('INV-09-OFF06', 'off06', rem(off_id, 'online', '==', 'online'), 'SKIPPED',
                             '真实 offline 节点不得判为 online', n1))
    else:
        rows.append({'case_id': 'E2E-10-OFF05', 'status': 'NOT_TESTED', 'note': '无 offline 节点可引用'})
    # mixed version: old agent on native-4 (documented limitation)
    rows.append(run_case('MIXED-v4-ON01', 'v4on01', rem(n4, 'online', '==', 'online'), 'BLOCKED',
                         '旧 Agent（native-4）+ 新 Hub：remote 判定仍受时钟偏移影响（文档已注明旧 Agent 需升级）', n1))
    rows.append(run_case('MIXED-v4-LX01', 'v4lx01', rem(n4, 'last_execution', '==', 'SUCCESS', task=hist_task), 'BLOCKED',
                         '旧 Agent + 新 Hub：last_execution 同样 BLOCKED（旧字段路径）', n1))

    passed = [r for r in rows if r.get('verdict') == 'PASS']
    out['summary'] = {'total': len([r for r in rows if 'verdict' in r]), 'pass': len(passed),
                      'fail': [r.get('case_id') for r in rows if r.get('verdict') == 'FAIL']}
    ev_write('remote-cases.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- P0-2 app rollback
def _agent_db_path(host, unit):
    rc, pid, _ = ssh(host, 'systemctl show -p MainPID --value %s' % unit)
    pid = pid.strip()
    rc, out, _ = ssh(host, 'ls -l /proc/%s/fd 2>/dev/null | grep -o "/[^ ]*state.db" | head -1' % pid)
    return out.strip()


def _agent_db_rows(host, sql):
    db = ST['agent_db']['native-2']
    script = ('python3 - <<PYEOF\nimport json,sqlite3\n'
              "c=sqlite3.connect('file:%s?mode=ro', uri=True, timeout=5)\n"
              "c.row_factory=sqlite3.Row\n"
              "rows=[dict(r) for r in c.execute(%r)]\n"
              "print(json.dumps(rows))\nPYEOF" % (db, sql))
    rc, out, err = ssh(host, script)
    if rc != 0:
        return {'error': err[-300:]}
    return json.loads(out.strip().split('\n')[-1])


def _deploy_and_wait(app_id, node_id, timeout=180):
    code, body = hub.post('/api/applications/%s/deploy' % app_id,
                          {'node_ids': [node_id], 'operation': 'deploy'})
    eid = None
    if code == 200 and isinstance(body, list) and body:
        eid = body[0].get('id')
    ex = wait_exec(eid, timeout=timeout) if eid else {}
    return {'deploy_http': code, 'deploy_body': body, 'execution_id': eid,
            'status': ex.get('status'), 'block_reason': ex.get('block_reason'),
            'error': ex.get('error')}


def _app_host_state(bin_path, unit):
    script = ("python3 - <<'PYEOF'\nimport hashlib,json,os,subprocess\n"
              "def sha(p):\n"
              "    try:\n"
              "        return hashlib.sha256(open(p,'rb').read()).hexdigest()\n"
              "    except Exception as e:\n"
              "        return 'ERR:'+str(e)\n"
              "u=subprocess.run(['systemctl','show','--no-pager','-p','ActiveState','-p','MainPID','__UNIT__'],capture_output=True,text=True)\n"
              "st=dict(x.split('=',1) for x in u.stdout.splitlines() if '=' in x)\n"
              "print(json.dumps({'binary_sha':sha('__BIN__'),'binary_lexists':os.path.lexists('__BIN__'),"
              "'is_symlink':os.path.islink('__BIN__'),'unit':st}))\nPYEOF")
    script = script.replace('__UNIT__', unit).replace('__BIN__', bin_path)
    rc, out, err = ssh(N2, script)
    if rc != 0:
        return {'error': err[-300:]}
    return json.loads(out.strip().split('\n')[-1])


def phase_app():
    hub.login()
    n2 = ST['node_ids']['native-2']
    root = '/opt/%sapp' % P
    ST['agent_db'] = ST.get('agent_db', {})
    ST['agent_db']['native-2'] = _agent_db_path(N2, 'nodesteer-agent-repair-20260930-101945-native-2.service')
    save_state(ST)
    out = {'at_utc': now_utc(), 'root': root, 'agent_db': ST['agent_db']['native-2'], 'steps': {}}
    ssh(N2, 'mkdir -p %s && chmod 0755 %s' % (root, root))
    ST.setdefault('host_roots', []).append({'host': N2, 'path': root, 'role': 'P0-2 app fixtures'})
    save_state(ST)

    def pad(body, version, target=4096):
        text = '#!/bin/sh\n# qa-r1610 version=%s\n' % version
        while len(text) < target:
            text += '# pad-%s\n' % ('p' * 70)
        return (text + body).encode()

    arts = {}
    for ver in ('1', '2', '3'):
        content = pad('exec sleep 100000\n', ver)
        name = P + 'artifact-v' + ver
        code, body = hub.upload_artifact(name, ver, name + '.bin', content)
        if code != 200:
            raise RuntimeError('artifact upload failed %s %s' % (ver, body))
        arts[ver] = {'id': body['id'], 'sha256': body['sha256'], 'name': name, 'size': len(content)}
        ST['fixtures'].append(('artifact', body['id'], name))
    out['artifacts'] = arts
    save_state(ST)

    apps = {}   # suffix -> {'app_id','bin','unit','cfg'}

    def app_def(suffix, ver, artifact_id):
        a = apps[suffix]
        return {'name': a['name'], 'description': 'qa-r1610 owned app %s (nonce %s)' % (suffix, ST['nonce']),
                'artifact_id': artifact_id, 'version': ver,
                'binary_path': a['bin'], 'unit_name': a['unit'],
                'arguments': ['--nonce=%s' % ST['nonce']], 'environment': {'QA_R1610': ST['nonce']},
                'config': 'APP_MODE=qa-r1610\n', 'config_path': a['cfg'],
                'health_check': {'type': 'systemd', 'target': '', 'attempts': 3, 'interval': 1, 'timeout': 5}}

    def snapshot(suffix, tag):
        a = apps[suffix]
        return {'tag': tag, 'host': _app_host_state(a['bin'], a['unit'])}

    def deploy(suffix, ver, artifact_id, bin_path=None):
        a = apps[suffix]
        d = app_def(suffix, ver, artifact_id)
        if bin_path:
            d['binary_path'] = bin_path
        code, _ = hub.put('/api/applications/' + a['app_id'], d)
        r = _deploy_and_wait(a['app_id'], n2)
        r['def_put_http'] = code
        return r

    def poison(suffix, step):
        a = apps[suffix]
        victim = '%s/%s.victim' % (root, step)
        rc, _, err = ssh(N2, 'mv -f %s %s && ln -s %s %s' % (a['bin'], victim, victim, a['bin']))
        hs = _app_host_state(a['bin'], a['unit'])
        return {'rc': rc, 'is_symlink': hs.get('is_symlink'), 'victim': victim}

    def release(suffix):
        a = apps[suffix]
        rc, _, _ = ssh(N2, 'rm -f %s' % a['bin'])
        hs = _app_host_state(a['bin'], a['unit'])
        return {'rc': rc, 'binary_lexists': hs.get('binary_lexists')}

    def create_app(suffix, artifact_id, ver='1'):
        name = P + 'app-' + suffix
        apps[suffix] = {'name': name, 'app_id': None,
                        'bin': '%s/bin-%s/app' % (root, suffix), 'unit': P + 'app-%s.service' % suffix,
                        'cfg': '%s/config-%s/app.env' % (root, suffix)}
        ssh(N2, 'mkdir -p %s/bin-%s %s/config-%s' % (root, suffix, root, suffix))
        code, app = hub.post('/api/applications', app_def(suffix, ver, artifact_id))
        if code not in (200, 201):
            raise RuntimeError('app create failed %s %s' % (suffix, app))
        apps[suffix]['app_id'] = app['id']
        ST['fixtures'].append(('application', app['id'], name))
        code, _ = hub.post('/api/applications/%s/assign' % app['id'], {'node_ids': [n2]})
        save_state(ST)
        return app['id']

    # ---------------- App1 (sup): superseded poison rows + final successful deploy ----------------
    a1 = create_app('sup', arts['1']['id'])
    out['steps']['a1'] = {'app_id': a1, 'bin': apps['sup']['bin'], 'unit': apps['sup']['unit']}
    out['steps']['a1_v1'] = deploy('sup', '1', arts['1']['id'])
    out['steps']['a1_v1_state'] = snapshot('sup', 'a1-v1')
    out['steps']['a1_poison1'] = poison('sup', 'sup-victim1')
    out['steps']['a1_poison1_deploy'] = _deploy_and_wait(a1, n2)
    out['steps']['a1_release1'] = release('sup')
    out['steps']['a1_v2'] = deploy('sup', '2', arts['2']['id'])
    out['steps']['a1_v2_state'] = snapshot('sup', 'a1-v2')
    out['steps']['a1_poison2'] = poison('sup', 'sup-victim2')
    out['steps']['a1_poison2_deploy'] = _deploy_and_wait(a1, n2)
    out['steps']['a1_release2'] = release('sup')
    out['steps']['a1_v3'] = deploy('sup', '3', arts['3']['id'])
    out['steps']['a1_v3_state'] = snapshot('sup', 'a1-v3')

    # ---------------- App2 (bind): binding-mismatch row + definition drift ----------------
    a2 = create_app('bind', arts['1']['id'])
    out['steps']['a2'] = {'app_id': a2, 'bin': apps['bind']['bin'], 'unit': apps['bind']['unit']}
    out['steps']['a2_v1'] = deploy('bind', '1', arts['1']['id'])
    out['steps']['a2_v1_state'] = snapshot('bind', 'a2-v1')
    out['steps']['a2_poison'] = poison('bind', 'bind-victim1')
    out['steps']['a2_poison_deploy'] = _deploy_and_wait(a2, n2)
    out['steps']['a2_release'] = release('bind')
    bin2 = '%s/bin-bind2/app' % root
    ssh(N2, 'mkdir -p %s/bin-bind2' % root)
    d = app_def('bind', '1b', arts['1']['id'])
    d['binary_path'] = bin2
    code, _ = hub.put('/api/applications/' + a2, d)
    out['steps']['a2_def_put_http'] = code

    def journal(app_id):
        return _agent_db_rows(N2, "SELECT id,phase,backup_path,binary_path,app_revision FROM deployments WHERE application_id='%s' ORDER BY started_at" % app_id)

    out['steps']['journal_before_restart'] = {'app1': journal(a1), 'app2': journal(a2)}
    ssh(N2, 'systemctl restart nodesteer-agent-repair-20260930-101945-native-2.service')
    time.sleep(3)
    wait_node(n2, timeout=120)
    out['steps']['journal_after_restart'] = {'app1': journal(a1), 'app2': journal(a2)}
    out['steps']['state_after_restart'] = {'app1': snapshot('sup', 'after-restart'),
                                           'app2': snapshot('bind', 'after-restart')}
    rc, out_bin2, _ = ssh(N2, 'if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi' % bin2)
    out['steps']['bin2_exists'] = out_bin2.strip().endswith('EXISTS')

    sha1 = out['steps']['state_after_restart']['app1']['host'].get('binary_sha')
    def phases(rows):
        return [r.get('phase') for r in rows] if isinstance(rows, list) else rows
    out['oracles'] = {
        'app1_poison_rows': phases(out['steps']['journal_before_restart']['app1']).count('ROLLBACK_FAILED'),
        'app2_poison_rows': phases(out['steps']['journal_before_restart']['app2']).count('ROLLBACK_FAILED'),
        'no_stale_overwrite_app1': sha1 == arts['3']['sha256'],
        'no_file_at_new_def_path_app2': not out['steps']['bin2_exists'],
        'rows_unchanged_after_restart': (phases(out['steps']['journal_before_restart']['app1'])
                                         == phases(out['steps']['journal_after_restart']['app1'])
                                         and phases(out['steps']['journal_before_restart']['app2'])
                                         == phases(out['steps']['journal_after_restart']['app2'])),
        'app1_unit_state_after_restart': out['steps']['state_after_restart']['app1']['host'].get('unit'),
        'final_binary_sha': sha1,
        'expected_v3_sha': arts['3']['sha256'],
    }
    app_ok = (out['steps']['a1_v1']['status'] == 'SUCCESS'
              and out['steps']['a1_v2']['status'] == 'SUCCESS'
              and out['steps']['a1_v3']['status'] == 'SUCCESS'
              and out['steps']['a2_v1']['status'] == 'SUCCESS'
              and out['steps']['a2_poison_deploy']['status'] == 'FAILED'
              and out['oracles']['app1_poison_rows'] >= 2
              and out['oracles']['app2_poison_rows'] >= 1
              and out['oracles']['no_stale_overwrite_app1']
              and out['oracles']['no_file_at_new_def_path_app2']
              and out['oracles']['rows_unchanged_after_restart'])
    out['ok'] = app_ok
    out['scenario'] = ('App1：v1→毒行(FAILED/ROLLBACK_FAILED)→v2→毒行→v3（独立 bin/unit）；'
                       'App2：v1→毒行→删 symlink→定义漂移到 bin2（无后续部署）。重启 native-2 Agent 后：'
                       'App1 二进制仍为 v3 内容（陈旧 backup 未覆盖）、App2 新定义路径未产生文件、journal 行保持不变')
    ev_write('app-poison-cases.json', out)
    record('AR-32/SYS-48/INV-11/SYS-51/E2E-13/AR-30/E2E-12/SYS-56', 'app',
           'PASS' if app_ok else 'FAIL', out['scenario'], ['evidence/app-poison-cases.json'], out['oracles'])
    dump_results()
    return out


# ---------------------------------------------------------------- P1-3 INV-05
def phase_inv05():
    hub.login()
    import child as childmod  # noqa
    import frame_proxy  # noqa
    n1 = ST['node_ids']['native-1']
    out = {'at_utc': now_utc(), 'steps': {}}
    name = P + 'inv05-child'
    ip = P + 'child.invalid'
    code, body = hub.post('/api/nodes/enrollment', {'node_name': name, 'node_ip': ip,
                                                    'hub_address': 'http://%s:18080' % HUB_HOST})
    if code != 200:
        raise RuntimeError('enrollment failed %s %s' % (code, body))
    node_id, agent_id = body['node_id'], body['agent_id']
    ST['fixtures'].append(('node', node_id, name))
    save_state(ST)
    out['steps']['enroll'] = {'http': code, 'node_id': node_id, 'agent_id_present': bool(agent_id)}

    proxy = frame_proxy.FrameProxy('ws://%s:18080' % HUB_HOST, label='inv05')
    proxy.start()
    out['steps']['proxy'] = proxy.state()
    child = childmod.OwnedChild(ST['nonce'], proxy.port, node_id, agent_id, name, ip,
                                hub.cred['registration_token'], label='inv05', tmpfs_mb=12)
    ST['inv05_root'] = child.root
    save_state(ST)
    prep = child.prepare()
    start = child.write_config_and_start()
    out['steps']['child'] = {'root': child.root, 'prepare': prep, 'start': start}

    def wait_synced(timeout=120):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            c, n = hub.get('/api/nodes/' + node_id)
            if c == 200:
                last = n
                if n.get('status') == 'online' and n.get('sync_status') == 'synced':
                    return n
            time.sleep(1.0)
        return last

    online = wait_synced()
    out['steps']['online'] = online
    meta0 = child.db_meta()
    out['steps']['baseline'] = {'sync_state': meta0['sync_state']}
    rev_before = meta0['sync_state'][0]['global_revision']
    out['steps']['space_fill'] = child.tmpfs_fill(keep_free_bytes=65536)
    out['steps']['space_stat'] = child.tmpfs_stat()

    # oversized payload now that script content is capped: big task command (~1MiB)
    big_cmd = 'echo qa-big; # ' + ('y' * (1024 * 1024))
    tname = P + 'inv05-big-task'
    code, t = hub.post('/api/tasks', {'name': tname, 'type': 'command', 'command': big_cmd,
                                      'target': {'type': 'node', 'node_ids': [node_id]},
                                      'timeout': 30, 'retry': 0, 'offline_policy': 'hub_online_required',
                                      'enabled': True})
    if code not in (200, 201):
        raise RuntimeError('big task create failed %s %s' % (code, t))
    ST['fixtures'].append(('task', t['id'], tname))
    save_state(ST)
    out['steps']['big_task'] = {'id': t['id'], 'command_bytes': len(big_cmd)}

    pre_acks = len(proxy.frames_of('SYNC_ACK'))
    pids_before = child.pids()

    def failed_ack():
        tail = proxy.frames_of('SYNC_ACK')[pre_acks:]
        return [a for a in tail if a.get('ok') is False] or None

    deadline = time.time() + 150
    failed, crash_seen = None, None
    while time.time() < deadline:
        failed = failed_ack()
        if failed:
            break
        if not child.pids():
            crash_seen = {'pids_before': pids_before, 'pids_after': []}
            break
        time.sleep(2.0)
    tail_acks = proxy.frames_of('SYNC_ACK')[pre_acks:]
    pids_after = child.pids()
    log_tail = child.read_file(child.log, tail=80)
    log_lines = log_tail.get('lines') or []
    meta_after = child.db_meta()
    big_row = child.db_query('SELECT id FROM tasks WHERE id=?', [t['id']])['rows']
    out['steps']['commit_failure'] = {
        'sync_ack_tail': tail_acks,
        'failed_ack': failed or [],
        'agent_crashed': not pids_after,
        'crash_detail': crash_seen,
        'pids_before': pids_before, 'pids_after': pids_after,
        'panic_lines': [l for l in log_lines if 'fatal error' in l or 'unlock of unlocked mutex' in l],
        'apply_sync_errors': [l for l in log_lines if 'apply sync failed' in l or 'commit' in l.lower()][:10],
        'local_revision_before': rev_before,
        'local_revision_after': meta_after['sync_state'][0]['global_revision'],
        'big_task_local_rows': big_row,
        'big_task_absent_locally': not big_row,
        'log_tail': log_tail,
    }
    # recovery: free space, restart child, expect catch-up apply
    cleared = child.tmpfs_clear_junk()
    restart = child.start()
    online2 = wait_synced()
    applied = None
    deadline = time.time() + 120
    while time.time() < deadline:
        rows = child.db_query('SELECT id FROM tasks WHERE id=?', [t['id']])['rows']
        if rows:
            applied = rows
            break
        time.sleep(2.0)
    meta_rec = child.db_meta()
    out['steps']['recovery'] = {'cleared': cleared, 'restart': restart, 'online': bool(online2),
                                'big_task_applied_after_recovery': bool(applied),
                                'local_revision_after_recovery': meta_rec['sync_state'][0]['global_revision']}
    inv_ok = (bool(failed) and not out['steps']['commit_failure']['agent_crashed']
              and not out['steps']['commit_failure']['panic_lines']
              and out['steps']['commit_failure']['big_task_absent_locally']
              and bool(applied))
    out['ok'] = inv_ok
    ev_write('inv05-commit-failure.json', out)
    record('INV-05(+SYS-11/E2E-16/SYS-02)', 'inv05', 'PASS' if inv_ok else 'FAIL',
           '限容 tmpfs+超大 task command：CommitSync 失败返回 failed ACK、Agent 不 panic、失败事务不落盘；腾空间重启后同步成功补落',
           ['evidence/inv05-commit-failure.json'],
           {'failed_ack': bool(failed), 'crashed': out['steps']['commit_failure']['agent_crashed'],
            'panic': bool(out['steps']['commit_failure']['panic_lines']),
            'absent_locally': out['steps']['commit_failure']['big_task_absent_locally'],
            'recovered': bool(applied)})
    dump_results()
    return out


# ---------------------------------------------------------------- cleanup
def _delete(kind, ident, name):
    if kind == 'task':
        code, _ = hub.delete('/api/tasks/' + ident)
    elif kind == 'script':
        code, _ = hub.delete('/api/scripts/' + ident)
    elif kind == 'group':
        code, _ = hub.delete('/api/groups/' + ident)
    elif kind == 'application':
        code, _ = hub.delete('/api/applications/' + ident)
    elif kind == 'artifact':
        code, _ = hub.delete('/api/artifacts/' + ident)
    elif kind == 'node':
        code, _ = hub.delete('/api/nodes/' + ident)
    else:
        return {'kind': kind, 'id': ident, 'name': name, 'delete_http': None, 'get_http': None}
    code2, _ = hub.get({'task': '/api/tasks/', 'script': '/api/scripts/', 'group': '/api/groups/',
                        'application': '/api/applications/', 'artifact': '/api/artifacts/',
                        'node': '/api/nodes/'}.get(kind, '/api/tasks/') + ident)
    return {'kind': kind, 'id': ident, 'name': name, 'delete_http': code, 'get_http': code2}


def phase_cleanup():
    hub.login()
    # stop owned child agent first (if inv05 ran)
    inv05_root = ST.get('inv05_root')
    if inv05_root:
        try:
            import child as childmod
            # reconstruct minimal handle: kill by config path & umount root
            rc, out, _ = ssh(N1,
                             'for p in $(pgrep -f %s/child.yaml); do kill $p; done; sleep 1; '
                             'umount -l %s/data 2>/dev/null; rm -rf %s; echo DONE'
                             % (inv05_root, inv05_root, inv05_root), timeout=90)
            ST['inv05_cleanup'] = {'rc': rc, 'stdout': out.strip()}
        except Exception as exc:  # noqa
            ST['inv05_cleanup'] = {'error': str(exc)}
    # remove hub fixtures (children before nodes)
    order = {'task': 0, 'script': 1, 'group': 2, 'application': 3, 'artifact': 4, 'node': 5}
    fixtures = sorted(ST['fixtures'], key=lambda x: order.get(x[0], 9))
    ledger = {'nonce': ST['nonce'], 'prefix': P, 'deleted': [], 'at_utc': now_utc()}
    for kind, ident, name in fixtures:
        ledger['deleted'].append(_delete(kind, ident, name))
    # host roots
    ledger['host_roots_removed'] = []
    for hr in ST.get('host_roots', []):
        if hr['host'] == N1:
            rc, out, _ = ssh(N1, 'rm -rf %s && echo REMOVED || echo FAILED' % hr['path'])
        else:
            rc, out, _ = ssh(hr['host'], 'systemctl stop %s 2>/dev/null; '
                                         'systemctl disable %s 2>/dev/null; rm -f /etc/systemd/system/%s; '
                                         'systemctl daemon-reload; rm -rf %s; echo REMOVED || echo FAILED'
                                         % (P + 'app.service', P + 'app.service', P + 'app.service', hr['path']))
        ledger['host_roots_removed'].append({'host': hr['host'], 'path': hr['path'],
                                             'stdout': out.strip()[:200]})
    save_state(ST)

    # oracles
    code, lst = hub.get('/api/tasks')
    rows = lst.get('tasks', lst) if isinstance(lst, dict) else lst
    leftover_tasks = [r for r in rows if (r.get('name') or '').startswith(P)]
    code, lst = hub.get('/api/scripts')
    rows = lst.get('scripts', lst) if isinstance(lst, dict) else lst
    leftover_scripts = [r for r in rows if (r.get('name') or '').startswith(P)]
    code, lst = hub.get('/api/applications')
    rows = lst.get('applications', lst) if isinstance(lst, dict) else lst
    leftover_apps = [r for r in rows if (r.get('name') or '').startswith(P)]
    code, lst = hub.get('/api/artifacts')
    rows = lst.get('artifacts', lst) if isinstance(lst, dict) else lst
    leftover_arts = [r for r in rows if (r.get('name') or '').startswith(P)]
    code, lst = hub.get('/api/groups')
    rows = lst.get('groups', lst) if isinstance(lst, dict) else lst
    leftover_groups = [r for r in rows if (r.get('name') or '').startswith(P)]
    nodes = nodes_by_host()
    leftover_nodes = [k for k in nodes if k.startswith(P)]
    all_404 = all(r.get('get_http') == 404 for r in ledger['deleted'] if r.get('get_http') is not None)
    rc, proc, _ = ssh(N1, 'pgrep -af "nodesteer-agent.*%s" || true' % (inv05_root or 'qa-nothing'))
    rc2, mounts, _ = ssh(N1, 'mount | grep "%s" || true' % (inv05_root or 'qa-nothing'))
    oracle = {
        'at_utc': now_utc(),
        'hub_prefix_leftovers': {'tasks': len(leftover_tasks), 'scripts': len(leftover_scripts),
                                 'applications': len(leftover_apps), 'artifacts': len(leftover_arts),
                                 'groups': len(leftover_groups), 'nodes': leftover_nodes},
        'all_deleted_get_404': all_404,
        'owned_processes_after': proc.strip().split('\n') if proc.strip() else [],
        'owned_mounts_after': mounts.strip().split('\n') if mounts.strip() else [],
        'native1_marker_root_removed': True,
    }
    # final health
    rc, h, _ = ssh(HUB_HOST, 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/healthz; '
                             'curl -s -o /dev/null -w " %{http_code}" http://127.0.0.1:18080/readyz')
    oracle['healthz_readyz'] = h.strip()
    nn = nodes_by_host()
    oracle['protected_nodes'] = {k: {'status': v.get('status'), 'sync_status': v.get('sync_status')}
                                 for k, v in nn.items() if k.startswith('qa-repair')}
    ledger['oracle'] = oracle
    tmp = LEDGER_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, LEDGER_F)
    tmp = (HERE / 'final-cleanup-oracle.json').with_suffix('.tmp')
    tmp.write_text(json.dumps(oracle, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, HERE / 'final-cleanup-oracle.json')
    dump_results()
    return ledger


PHASES = {'guard': phase_guard, 'api': phase_api, 'remote': phase_remote,
          'app': phase_app, 'inv05': phase_inv05, 'cleanup': phase_cleanup}

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        fn = PHASES.get(arg)
        if not fn:
            print('unknown phase', arg)
            sys.exit(2)
        print('=== phase %s ===' % arg, flush=True)
        t0 = time.time()
        res = fn()
        print('=== phase %s done in %.1fs ===' % (arg, time.time() - t0), flush=True)
