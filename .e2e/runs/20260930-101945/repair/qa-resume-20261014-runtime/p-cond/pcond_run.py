#!/usr/bin/env python3
"""P-COND batch: condition engine / metrics / operators / Remote+Local dimensions.

Authority: repair hub http://192.168.100.209:18080 (only management-write endpoint).
Owned child processes: none started. All fixtures use nonce prefix qa-pcond-<nonce>-*.
Owned host root on native-1: /var/lib/nodesteer-qa-pcond-<nonce>/ (markers + probe files).

Phases (rerunnable; state persisted in run-state.json, case rows in evidence/cases.jsonl):
  0 guard+clock     1 hostroot     2 fixtures     3 local matrix    4 and combos
  5 remote matrix   6 ui            7 cleanup      8 residue oracle
"""
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
STATE_F = HERE / 'run-state.json'
CASES_F = EV / 'cases.jsonl'

BROWSER = build_opener(ProxyHandler({}))
TOKEN = [None]
BASE = [None]

SSH_KEY = str(Path.home() / '.ssh' / 'id_ed25519')
N1 = '192.168.100.204'      # native-1  (only node we schedule onto)
HUB_HOST = '192.168.100.209'  # repair hub host (read-only `date` for clock measurement)

NODE_N1 = '91f44866-8a28-435e-bbda-2f2367126c00'
NODE_N1_HOST = 'qa-repair-20260930-101945-native-1'
NODE_OFFLINE = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'   # pre-existing node, status=offline
NODE_OFFLINE_HOST = ('qa-resume-20261008-native4-child-c5d48ea9-da08-4123-94fb-'
                     '69b11abd59a7')
NODE_HINTEG = 'f6a79d23-2410-450c-ac39-64dd70c9daa1'    # online, non-native
NODE_HINTEG_HOST = 'qa-resume-20261010-host-f174890c3846-host001'

TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'

TASK_KEYS = ['cmd', 'cpu', 'mem', 'disk', 'file', 'dir', 'proc', 'port', 'and',
             'remote', 'hist_ok', 'hist_fail', 'nohist']


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def ssh(host, cmd, timeout=40):
    cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                         '-o', 'ConnectTimeout=8', '-i', SSH_KEY, 'root@' + host, cmd],
                        capture_output=True, text=True, timeout=timeout)
    return cp.returncode, cp.stdout, cp.stderr


def api(method, path, body=None, allowed=(200,), retries=3):
    headers = {'Accept': 'application/json'}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if TOKEN[0]:
        headers['Authorization'] = 'Bearer ' + TOKEN[0]
    last = None
    for _ in range(retries):
        try:
            req = Request(BASE[0] + path, data=data, headers=headers, method=method)
            with BROWSER.open(req, timeout=25) as resp:
                raw = resp.read()
                try:
                    return resp.status, (json.loads(raw) if raw else None)
                except Exception:
                    return resp.status, {'_non_json': raw[:200].decode('utf-8', 'replace')}
        except HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {'_raw': raw[:300].decode('utf-8', 'replace')}
        except (URLError, TimeoutError) as exc:
            last = exc
            time.sleep(1.2)
    raise RuntimeError('api transport failure: %s' % type(last).__name__)


def dump(name, payload):
    (EV / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {}


def save_state(st):
    STATE_F.write_text(json.dumps(st, ensure_ascii=False, indent=2) + '\n')


def append_case(row):
    with CASES_F.open('a') as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + '\n')


def read_cases():
    if not CASES_F.exists():
        return []
    out = []
    for line in CASES_F.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------- phases
def phase_guard(st):
    cred, conn = bootstrap()
    code, body = api('POST', '/api/login',
                     {'username': cred['admin_username'], 'password': cred['admin_password']})
    if code != 200 or not (body or {}).get('token'):
        raise SystemExit('login failed HTTP %s' % code)
    TOKEN[0] = body['token']

    code, nodes = api('GET', '/api/nodes')
    by_id = {n['id']: n for n in (nodes or [])}
    for need, label in ((NODE_N1, 'native-1'), (NODE_OFFLINE, 'offline-node'),
                        (NODE_HINTEG, 'host-integration')):
        if need not in by_id:
            raise SystemExit('required node missing: %s' % label)
    n1 = by_id[NODE_N1]
    if n1.get('status') != 'online' or n1.get('sync_status') != 'synced':
        raise SystemExit('native-1 not usable: %s' % n1.get('status'))

    # read-only settings snapshot (must be unchanged at the end)
    _, settings = api('GET', '/api/settings')

    # clock skew: hub host vs native-1, interleaved, RTT-midpoint corrected
    skew = []
    for i in range(12):
        if i % 2 == 0:
            a0 = time.time()
            rc1, so_h, _ = ssh(HUB_HOST, 'date -u +%s.%N')
            a1 = time.time()
            rc2, so_n, _ = ssh(N1, 'date -u +%s.%N')
            a2 = time.time()
        else:
            a0 = time.time()
            rc2, so_n, _ = ssh(N1, 'date -u +%s.%N')
            a1 = time.time()
            rc1, so_h, _ = ssh(HUB_HOST, 'date -u +%s.%N')
            a2 = time.time()
        if rc1 or rc2:
            continue
        # hub read spans [a0,a1] or [a1,a2]; express both at the same wall instant a2
        if i % 2 == 0:
            hub_ep = float(so_h.strip()) + (a2 - (a0 + a1) / 2)
            nat_ep = float(so_n.strip()) + (a2 - (a1 + a2) / 2)
        else:
            nat_ep = float(so_n.strip()) + (a2 - (a0 + a1) / 2)
            hub_ep = float(so_h.strip()) + (a2 - (a1 + a2) / 2)
        skew.append(round(hub_ep - nat_ep, 4))
    skew.sort()
    med = skew[len(skew) // 2] if skew else None
    guard = {
        'endpoint': BASE[0],
        'guard_18080': True,
        'login_http': code,
        'executed_at_utc': now_utc(),
        'nodes': [{'id': n['id'], 'hostname': n.get('hostname'), 'status': n.get('status'),
                   'sync_status': n.get('sync_status'), 'agent_id': n.get('agent_id'),
                   'global_revision': n.get('global_revision')} for n in (nodes or [])],
        'settings_before': settings,
        'clock_skew_hub_minus_native1_s': {
            'samples': skew, 'median': med,
            'method': 'ssh date -u +%s.%N on hub host and native-1, interleaved, '
                      'corrected to a common instant',
            'interpretation': 'positive => hub clock ahead of agent clock => '
                              'observed_at(hub now) > agent now => agent validRemoteState'
                              ' rejects => remote condition UNKNOWN => BLOCKED'},
        'protected_agent_unit_native1': 'nodesteer-agent-repair-20260930-101945-native-1.service',
        'notes': ['management writes only to repair :18080',
                  'no child agent / no proxy / no server started by this batch',
                  'native-1 is the only scheduling target used for owned fixtures'],
    }
    st['guard'] = guard
    dump('phase0-guard.json', guard)
    save_state(st)
    return guard


def host_root(st):
    nonce = st['nonce']
    root = '/var/lib/nodesteer-qa-pcond-%s' % nonce
    cmd = ('set -e; mkdir -p %s/markers; chmod 0755 %s %s/markers; '
           'printf "probe-%%s\\n" "$(date -u +%%FT%%T.%%NZ)" > %s/probe_file.txt; '
           'rm -f %s/missing_file.txt; rmdir %s/missing_dir 2>/dev/null || true; '
           'echo DIR_OK; ls -la %s' % (root, root, root, root, root, root, root))
    rc, so, se = ssh(N1, cmd)
    if rc != 0:
        raise SystemExit('host root setup failed: %s %s' % (so, se))
    info = {'root': root, 'setup_rc': rc, 'stdout': so, 'stderr': se}
    st['host_root'] = root
    dump('phase1-host-root.json', info)
    save_state(st)
    return root


def base_task(key, name, command, marker_key):
    """command: shell run by the agent on the node; marker oracle path derived from key."""
    return {'name': name, 'type': 'command', 'command': command,
            'target': {'type': 'node', 'node_ids': [NODE_N1]},
            'timeout': 20, 'retry': 0, 'offline_policy': 'hub_online_required',
            'enabled': True}


def phase_fixtures(st):
    nonce = st['nonce']
    root = st['host_root']
    mk = root + '/markers'
    tasks = {}
    created = []

    def cmd_for(key):
        return ("mkdir -p {mk} && printf 'EXEC %s key={k}\\n' \"$(date -u +%FT%T.%NZ)\" "
                "> {mk}/{k}.marker && echo RAN_{k}".format(mk=mk, k=key))

    bodies = {}
    for key in ['cmd', 'cpu', 'mem', 'disk', 'file', 'dir', 'proc', 'port', 'and', 'remote']:
        bodies[key] = base_task(key, 'qa-pcond-%s-%s' % (nonce, key), cmd_for(key), key)
    # history helpers: run once WITHOUT condition to seed last_execution
    bodies['hist_ok'] = base_task('hist_ok', 'qa-pcond-%s-hist-ok' % nonce, 'echo HIST_OK',
                                  'hist_ok')
    bodies['hist_fail'] = base_task('hist_fail', 'qa-pcond-%s-hist-fail' % nonce, 'exit 7',
                                    'hist_fail')
    bodies['nohist'] = base_task('nohist', 'qa-pcond-%s-nohist' % nonce,
                                 "printf 'NEVER %s\\n' \"$(date -u +%FT%T.%NZ)\" "
                                 "> {mk}/nohist.marker".format(mk=mk), 'nohist')

    for key in TASK_KEYS:
        code, created_body = api('POST', '/api/tasks', bodies[key], (200, 201))
        if code not in (200, 201):
            raise SystemExit('create task %s failed %s %s' % (key, code, created_body))
        tasks[key] = {'id': created_body['id'], 'name': bodies[key]['name'], 'body': bodies[key]}
        created.append({'kind': 'task', 'key': key, 'id': created_body['id'],
                        'name': bodies[key]['name']})
        # exact readback
        gc, gb = api('GET', '/api/tasks/' + created_body['id'])
        if gc != 200 or gb.get('name') != bodies[key]['name'] or gb.get('type') != 'command':
            raise SystemExit('task readback mismatch for %s' % key)
        tasks[key]['revision'] = gb.get('revision')

    # seed last_execution history on native-1
    hist = {}
    for key in ('hist_ok', 'hist_fail'):
        code, run = api('POST', '/api/tasks/%s/run' % tasks[key]['id'], {'node_ids': {NODE_N1: True}})
        eid = run[0]['id']
        ex = wait_exec(eid, timeout=40)
        hist[key] = {'task_id': tasks[key]['id'], 'execution_id': eid,
                     'status': ex.get('status'), 'exit_code': ex.get('exit_code')}
    if hist['hist_ok']['status'] != 'SUCCESS' or hist['hist_fail']['status'] != 'FAILED':
        raise SystemExit('history seed mismatch: %s' % hist)

    st['tasks'] = tasks
    st['created'] = st.get('created', []) + created
    fixture = {'host_root': root,
               'tasks': {k: {'id': v['id'], 'name': v['name'], 'revision': v.get('revision'),
                             'command': v['body']['command']} for k, v in tasks.items()},
               'history_seed': hist,
               'created_order': [c['key'] for c in created]}
    dump('phase2-fixtures.json', fixture)
    save_state(st)
    return tasks, hist


def wait_exec(eid, timeout=45):
    deadline = time.time() + timeout
    ex = {}
    while time.time() < deadline:
        c, ex = api('GET', '/api/executions/' + eid)
        if c == 200 and ex.get('status') in TERMINAL:
            return ex
        time.sleep(0.6)
    ex = ex or {}
    ex['_timeout'] = True
    return ex


def marker_state(root, key):
    path = '%s/markers/%s.marker' % (root, key)
    rc, so, se = ssh(N1, 'if [ -f %s ]; then echo PRESENT; cat %s; else echo ABSENT; fi; '
                         'rm -f %s' % (path, path, path))
    if rc != 0:
        return {'error': se, 'rc': rc}
    lines = so.strip('\n').split('\n')
    present = lines and lines[0] == 'PRESENT'
    return {'present': bool(present), 'content': '\n'.join(lines[1:]) if present else None}


def put_condition(st, key, condition):
    t = st['tasks'][key]
    body = dict(t['body'])
    if condition is not None:
        body['condition'] = condition
    code, resp = api('PUT', '/api/tasks/' + t['id'], body)
    if code != 200:
        raise SystemExit('PUT condition failed key=%s %s %s' % (key, code, resp))
    t['revision'] = resp.get('revision')
    save_state(st)
    return resp


def run_one(st, key, case_id, condition, expect, oracle, note, dimension):
    root = st['host_root']
    pre_rev = st['tasks'][key].get('revision')
    put_condition(st, key, condition)
    code, run = api('POST', '/api/tasks/%s/run' % st['tasks'][key]['id'],
                    {'node_ids': {NODE_N1: True}})
    if code != 200 or not run:
        raise SystemExit('run failed key=%s %s %s' % (key, code, run))
    eid = run[0]['id']
    ex = wait_exec(eid)
    ms = marker_state(root, key)
    row = {
        'case_id': case_id, 'dimension': dimension, 'task_key': key,
        'task_id': st['tasks'][key]['id'], 'task_revision_before': pre_rev,
        'task_revision_used': ex.get('task_revision'),
        'condition': condition,
        'execution_id': eid, 'status': ex.get('status'), 'exit_code': ex.get('exit_code'),
        'block_reason': ex.get('block_reason'), 'stdout': (ex.get('stdout') or '').strip(),
        'start_time': ex.get('start_time'), 'end_time': ex.get('end_time'),
        'expect_status': expect, 'oracle': oracle,
        'marker_present': ms.get('present'), 'marker_content': ms.get('content'),
        'marker_probe_error': ms.get('error'),
        'timeout': bool(ex.get('_timeout')), 'note': note, 'at_utc': now_utc(),
    }
    row['status_match'] = (row['status'] == expect)
    want_marker = (expect == 'SUCCESS')
    row['oracle_match'] = (bool(row['marker_present']) == want_marker)
    row['executed'] = bool(row['marker_present'])
    row['verdict'] = 'PASS' if (row['status_match'] and row['oracle_match']
                               and not row['timeout']) else 'FAIL'
    append_case(row)
    return row


# ---------------- local case tables ----------------
def local_cases(root):
    m = root + '/markers'
    pf = root + '/probe_file.txt'
    mf = root + '/missing_file.txt'
    md = root + '/missing_dir'
    noproc = 'qa-pcond-nosuchproc-zz'
    cases = []

    def add(cid, key, cond, expect, note):
        cases.append((cid, key, cond, expect, 'SYS-25/E2E-09', note))

    def loc(metric, op, value, path=None, command=None):
        c = {'type': 'local', 'local': {'metric': metric, 'operator': op, 'value': value}}
        if path is not None:
            c['local']['path'] = path
        if command is not None:
            c['local']['command'] = command
        return c

    # command_result: string / numeric / float / error
    add('PC-CMD-RET-01', 'cmd', loc('command_result', '==', 'abc', command='echo abc'),
        'SUCCESS', 'command_result == match')
    add('PC-CMD-RET-02', 'cmd', loc('command_result', '==', 'xyz', command='echo abc'),
        'SKIPPED', 'command_result == mismatch')
    add('PC-CMD-RET-03', 'cmd', loc('command_result', '!=', 'xyz', command='echo abc'),
        'SUCCESS', 'command_result != match')
    add('PC-CMD-RET-04', 'cmd', loc('command_result', '!=', 'abc', command='echo abc'),
        'SKIPPED', 'command_result != mismatch')
    add('PC-CMD-NUM-01', 'cmd', loc('command_result', '>', '9', command='echo 10'),
        'SUCCESS', 'numeric >')
    add('PC-CMD-NUM-02', 'cmd', loc('command_result', '<', '9', command='echo 10'),
        'SKIPPED', 'numeric < false')
    add('PC-CMD-NUM-03', 'cmd', loc('command_result', '>=', '10', command='echo 10'),
        'SUCCESS', 'numeric >= equal')
    add('PC-CMD-NUM-04', 'cmd', loc('command_result', '<=', '10', command='echo 10'),
        'SUCCESS', 'numeric <= equal')
    add('PC-CMD-NUM-05', 'cmd', loc('command_result', '>=', '11', command='echo 10'),
        'SKIPPED', 'numeric >= false')
    add('PC-CMD-NUM-06', 'cmd', loc('command_result', '<=', '9', command='echo 10'),
        'SKIPPED', 'numeric <= false')
    add('PC-CMD-FLT-01', 'cmd', loc('command_result', '>', '1.4', command='echo 1.5'),
        'SUCCESS', 'float >')
    add('PC-CMD-STR-01', 'cmd', loc('command_result', '>', 'false', command='echo true'),
        'SUCCESS', 'non-numeric falls back to string compare (true>false)')
    add('PC-CMD-STR-02', 'cmd', loc('command_result', '>', 'abd', command='echo abc'),
        'SKIPPED', 'non-numeric string compare abc>abd false')
    add('PC-CMD-ERR-01', 'cmd', loc('command_result', '==', '', command='exit 3'),
        'BLOCKED', 'command_result non-zero exit -> unevaluable -> fail closed')
    add('PC-CMD-ERR-02', 'cmd', loc('command_result', '==', '', command='nosuch-pcond-cmd-zz'),
        'BLOCKED', 'command_result 127 (missing binary) -> unevaluable -> fail closed')
    add('PC-CMD-TRM-01', 'cmd', loc('command_result', '==', 'abc', command="printf 'abc\\n'"),
        'SUCCESS', 'command_result trims output')

    # cpu_usage (numeric, all operators)
    add('PC-CPU-01', 'cpu', loc('cpu_usage', '>=', '0'), 'SUCCESS', 'cpu >= 0')
    add('PC-CPU-02', 'cpu', loc('cpu_usage', '<=', '100'), 'SUCCESS', 'cpu <= 100')
    add('PC-CPU-03', 'cpu', loc('cpu_usage', '<', '101'), 'SUCCESS', 'cpu < 101')
    add('PC-CPU-04', 'cpu', loc('cpu_usage', '>', '100'), 'SKIPPED', 'cpu > 100 false')
    add('PC-CPU-05', 'cpu', loc('cpu_usage', '==', '12345.67'), 'SKIPPED', 'cpu == bogus false')
    add('PC-CPU-06', 'cpu', loc('cpu_usage', '!=', '12345.67'), 'SUCCESS', 'cpu != bogus true')

    # memory_usage
    add('PC-MEM-01', 'mem', loc('memory_usage', '>', '0'), 'SUCCESS', 'mem > 0')
    add('PC-MEM-02', 'mem', loc('memory_usage', '<', '100'), 'SUCCESS', 'mem < 100')
    add('PC-MEM-03', 'mem', loc('memory_usage', '>=', '0'), 'SUCCESS', 'mem >= 0')
    add('PC-MEM-04', 'mem', loc('memory_usage', '>', '100'), 'SKIPPED', 'mem > 100 false')
    add('PC-MEM-05', 'mem', loc('memory_usage', '==', '12345.67'), 'SKIPPED', 'mem == bogus false')
    add('PC-MEM-06', 'mem', loc('memory_usage', '!=', '12345.67'), 'SUCCESS', 'mem != bogus true')

    # disk_usage
    add('PC-DSK-01', 'disk', loc('disk_usage', '>', '0', path='/'), 'SUCCESS', 'disk / > 0')
    add('PC-DSK-02', 'disk', loc('disk_usage', '<=', '100', path='/'), 'SUCCESS', 'disk / <= 100')
    add('PC-DSK-03', 'disk', loc('disk_usage', '>=', '0', path='/'), 'SUCCESS', 'disk / >= 0')
    add('PC-DSK-04', 'disk', loc('disk_usage', '>', '100', path='/'), 'SKIPPED', 'disk / > 100 false')
    add('PC-DSK-05', 'disk', loc('disk_usage', '!=', '12345.67', path='/'), 'SUCCESS',
        'disk / != bogus true')
    add('PC-DSK-06', 'disk', loc('disk_usage', '==', '12345.67', path='/'), 'SKIPPED',
        'disk / == bogus false')
    add('PC-DSK-ERR-01', 'disk', loc('disk_usage', '==', '0', path=md + '-nodir'), 'BLOCKED',
        'statfs on missing path -> unevaluable -> fail closed')

    # file_exists (present + absent)
    add('PC-FILE-01', 'file', loc('file_exists', '==', 'true', path=pf), 'SUCCESS', 'file present ==true')
    add('PC-FILE-02', 'file', loc('file_exists', '==', 'false', path=pf), 'SKIPPED', 'file present ==false')
    add('PC-FILE-03', 'file', loc('file_exists', '!=', 'false', path=pf), 'SUCCESS', 'file present !=false')
    add('PC-FILE-04', 'file', loc('file_exists', '!=', 'true', path=pf), 'SKIPPED', 'file present !=true')
    add('PC-FILE-05', 'file', loc('file_exists', '>', 'false', path=pf), 'SUCCESS', 'file >false (string)')
    add('PC-FILE-06', 'file', loc('file_exists', '>=', 'false', path=pf), 'SUCCESS', 'file >=false (string)')
    add('PC-FILE-07', 'file', loc('file_exists', '<', 'true', path=pf), 'SKIPPED', 'file <true false')
    add('PC-FILE-08', 'file', loc('file_exists', '<=', 'true', path=pf), 'SUCCESS', 'file <=true')
    add('PC-FILE-09', 'file', loc('file_exists', '==', 'false', path=mf), 'SUCCESS', 'file absent ==false')
    add('PC-FILE-10', 'file', loc('file_exists', '==', 'true', path=mf), 'SKIPPED', 'file absent ==true')

    # dir_exists
    add('PC-DIR-01', 'dir', loc('dir_exists', '==', 'true', path=m), 'SUCCESS', 'dir present ==true')
    add('PC-DIR-02', 'dir', loc('dir_exists', '==', 'false', path=m), 'SKIPPED', 'dir present ==false')
    add('PC-DIR-03', 'dir', loc('dir_exists', '!=', 'false', path=m), 'SUCCESS', 'dir present !=false')
    add('PC-DIR-04', 'dir', loc('dir_exists', '>=', 'false', path=m), 'SUCCESS', 'dir >=false (string)')
    add('PC-DIR-05', 'dir', loc('dir_exists', '<', 'true', path=m), 'SKIPPED', 'dir <true false')
    add('PC-DIR-06', 'dir', loc('dir_exists', '==', 'false', path=md), 'SUCCESS', 'dir absent ==false')
    add('PC-DIR-07', 'dir', loc('dir_exists', '==', 'true', path=md), 'SKIPPED', 'dir absent ==true')

    # process_exists
    add('PC-PROC-01', 'proc', loc('process_exists', '==', 'true', path='sshd'), 'SUCCESS', 'sshd exists')
    add('PC-PROC-02', 'proc', loc('process_exists', '==', 'false', path='sshd'), 'SKIPPED', 'sshd ==false')
    add('PC-PROC-03', 'proc', loc('process_exists', '!=', 'false', path='sshd'), 'SUCCESS', 'sshd !=false')
    add('PC-PROC-04', 'proc', loc('process_exists', '>=', 'false', path='sshd'), 'SUCCESS', 'sshd >=false (string)')
    add('PC-PROC-05', 'proc', loc('process_exists', '<', 'true', path='sshd'), 'SKIPPED', 'sshd <true false')
    add('PC-PROC-06', 'proc', loc('process_exists', '==', 'false', path=noproc), 'SUCCESS', 'absent proc ==false')
    add('PC-PROC-07', 'proc', loc('process_exists', '==', 'true', path=noproc), 'SKIPPED', 'absent proc ==true')

    # port_listening
    add('PC-PORT-01', 'port', loc('port_listening', '==', 'true', path='22'), 'SUCCESS', 'port 22 listening')
    add('PC-PORT-02', 'port', loc('port_listening', '==', 'false', path='22'), 'SKIPPED', 'port 22 ==false')
    add('PC-PORT-03', 'port', loc('port_listening', '!=', 'false', path='22'), 'SUCCESS', 'port 22 !=false')
    add('PC-PORT-04', 'port', loc('port_listening', '>=', 'false', path='22'), 'SUCCESS', 'port 22 >=false (string)')
    add('PC-PORT-05', 'port', loc('port_listening', '<', 'true', path='22'), 'SKIPPED', 'port 22 <true false')
    add('PC-PORT-06', 'port', loc('port_listening', '==', 'false', path='59987'), 'SUCCESS', 'port 59987 closed ==false')
    add('PC-PORT-07', 'port', loc('port_listening', '==', 'true', path='59987'), 'SKIPPED', 'port 59987 closed ==true')
    return cases


def and_cases(root):
    pf = root + '/probe_file.txt'
    m = root + '/markers'

    def loc(metric, op, value, path=None, command=None):
        c = {'type': 'local', 'local': {'metric': metric, 'operator': op, 'value': value}}
        if path is not None:
            c['local']['path'] = path
        if command is not None:
            c['local']['command'] = command
        return c

    t = loc('command_result', '==', 'abc', command='echo abc')
    f = loc('file_exists', '==', 'true', path=root + '/no-such-file-zz')
    u = loc('command_result', '==', '', command='exit 3')
    r = {'type': 'remote', 'remote': {'node_id': NODE_N1, 'property': 'online',
                                      'operator': '==', 'value': 'online'}}
    return [
        ('PC-AND-01', 'and', {'type': 'and', 'and': [t, loc('file_exists', '==', 'true', path=pf)]},
         'SUCCESS', 'SYS-25/E2E-09', 'and[true,true] -> satisfied'),
        ('PC-AND-02', 'and', {'type': 'and', 'and': [t, f]},
         'SKIPPED', 'SYS-25/SYS-30', 'and[true,false] -> not satisfied, must not execute'),
        ('PC-AND-03', 'and', {'type': 'and', 'and': [loc('dir_exists', '==', 'true', path=m), u]},
         'BLOCKED', 'SYS-25/E2E-09', 'and[true,unknown] -> stays UNKNOWN -> fail closed'),
        ('PC-AND-04', 'and', {'type': 'and', 'and': [
            {'type': 'and', 'and': [t, loc('file_exists', '==', 'true', path=pf)]}, f]},
         'SKIPPED', 'SYS-25/E2E-09', 'nested 2-level and[and[t,t],false] -> SKIPPED'),
        ('PC-AND-05', 'and', {'type': 'and', 'and': [
            {'type': 'and', 'and': [t, loc('file_exists', '==', 'true', path=pf)]}, u]},
         'BLOCKED', 'SYS-25/E2E-09', 'nested 2-level and[and[t,t],unknown] -> BLOCKED'),
        ('PC-AND-06', 'and', {'type': 'and', 'and': [f, u]},
         'SKIPPED', 'SYS-30', 'and[false,unknown] short-circuits to false -> SKIPPED'),
        ('PC-AND-07', 'and', {'type': 'and', 'and': [r, t]},
         'BLOCKED', 'E2E-10/SYS-27', 'and[remote(online),true] -> remote unknown -> BLOCKED'
                                     ' (SYS-27 defect)'),
    ]


def remote_cases(st):
    ok = st['tasks']['hist_ok']['id']
    bad = st['tasks']['hist_fail']['id']
    nohist = st['tasks']['nohist']['id']

    def rem(node, prop, op, value, task=None):
        c = {'type': 'remote', 'remote': {'node_id': node, 'property': prop,
                                          'operator': op, 'value': value}}
        if task:
            c['remote']['task_id'] = task
        return c

    # (case_id, condition, spec_expectation, dimension, note, state_determinable)
    return [
        ('PC-REM-ON-01', rem(NODE_N1, 'online', '==', 'online'), 'SUCCESS', 'E2E-10/SYS-27',
         'remote online satisfied path on online node', True),
        ('PC-REM-ON-02', rem(NODE_N1, 'online', '!=', 'offline'), 'SUCCESS', 'E2E-10/SYS-27',
         'remote online != offline', True),
        ('PC-REM-ON-03', rem(NODE_N1, 'online', '==', 'offline'), 'SKIPPED', 'SYS-30/E2E-10',
         'remote online mismatch -> not satisfied', True),
        ('PC-REM-ON-04', rem(NODE_N1, 'online', '==', 'unknown'), 'SKIPPED', 'SYS-30/E2E-10',
         'remote online == unknown (explicit unknown value)', True),
        ('PC-REM-ON-05', rem(NODE_OFFLINE, 'online', '==', 'offline'), 'SUCCESS', 'E2E-10/INV-09',
         'real disconnected node (registered, status=offline) satisfied path', True),
        ('PC-REM-ON-06', rem(NODE_OFFLINE, 'online', '==', 'online'), 'SKIPPED',
         'E2E-10/INV-09', 'real disconnected node must not look online', True),
        ('PC-REM-ON-07', rem(NODE_HINTEG, 'online', '==', 'online'), 'SUCCESS', 'E2E-10/SYS-27',
         'second online node satisfied path', True),
        ('PC-REM-LX-01', rem(NODE_N1, 'last_execution', '==', 'SUCCESS', task=ok), 'SUCCESS',
         'SYS-28', 'last_execution == SUCCESS with real SUCCESS history', True),
        ('PC-REM-LX-02', rem(NODE_N1, 'last_execution', '==', 'FAILED', task=bad), 'SUCCESS',
         'SYS-28', 'last_execution == FAILED with real FAILED history', True),
        ('PC-REM-LX-03', rem(NODE_N1, 'last_execution', '==', 'FAILED', task=ok), 'SKIPPED',
         'SYS-28', 'last_execution == FAILED but history SUCCESS -> not satisfied', True),
        ('PC-REM-LX-04', rem(NODE_N1, 'last_execution', '!=', 'SUCCESS', task=ok), 'SKIPPED',
         'SYS-28', 'last_execution != SUCCESS but history SUCCESS -> not satisfied', True),
        ('PC-REM-LX-05', rem(NODE_N1, 'last_execution', '==', 'SUCCESS', task=nohist),
         'BLOCKED', 'SYS-29/INV-09', 'last_execution never observed -> UNKNOWN -> BLOCKED '
                                    '(fail closed; spec-consistent)', False),
        ('PC-REM-LX-06', rem(NODE_OFFLINE, 'last_execution', '==', 'SUCCESS', task=ok),
         'BLOCKED', 'SYS-29/INV-09', 'last_execution unobserved on disconnected node -> UNKNOWN '
                                    '-> BLOCKED (fail closed; spec-consistent)', False),
    ]


def phase_local_and_remote(st):
    keys = ('PC-')
    done = {r['case_id'] for r in read_cases()}
    rows = []
    for (cid, key, cond, expect, dim, note) in local_cases(st['host_root']):
        if cid in done:
            continue
        rows.append(run_one(st, key, cid, cond, expect, 'marker', note, dim))
    dump('phase3-local-matrix.json', {
        'total': len(rows),
        'pass': sum(1 for r in rows if r['verdict'] == 'PASS'),
        'fail': sum(1 for r in rows if r['verdict'] == 'FAIL'),
        'rows': rows})
    for (cid, key, cond, expect, dim, note) in and_cases(st['host_root']):
        if cid in done:
            continue
        rows.append(run_one(st, key, cid, cond, expect, 'marker', note, dim))
    dump('phase4-and-combos.json', {
        'rows': [r for r in rows if r['case_id'].startswith('PC-AND')]})

    # remote matrix
    rrows = []
    for (cid, cond, spec, dim, note, determinable) in remote_cases(st):
        if cid in done:
            continue
        r = run_one(st, 'remote', cid, cond, spec, 'marker', note, dim)
        r['spec_expect_status'] = spec
        r['state_determinable'] = determinable
        r['blocked_by_sys27'] = (r['status'] == 'BLOCKED' and spec != 'BLOCKED'
                                 and determinable)
        r['fail_closed_consistent'] = (r['status'] == 'BLOCKED' and spec == 'BLOCKED')
        r['business_effect_ok'] = (not r['executed'])
        rrows.append(r)
    dump('phase5-remote-matrix.json', {
        'total': len(rrows),
        'observed_blocked': sum(1 for r in rrows if r['status'] == 'BLOCKED'),
        'blocked_by_sys27': sum(1 for r in rrows if r['blocked_by_sys27']),
        'fail_closed_consistent': sum(1 for r in rrows if r['fail_closed_consistent']),
        'rows': rrows})
    return rows + rrows


def phase_repeat(st):
    """Determinism control: re-run representative cases and require identical terminal state."""
    pf = st['host_root'] + '/probe_file.txt'

    def loc(metric, op, value, path=None, command=None):
        c = {'type': 'local', 'local': {'metric': metric, 'operator': op, 'value': value}}
        if path is not None:
            c['local']['path'] = path
        if command is not None:
            c['local']['command'] = command
        return c

    table = [
        ('PC-REP-01', 'cmd', loc('command_result', '==', 'abc', command='echo abc'),
         'SUCCESS', 'SYS-25/E2E-09', 'repeat: satisfied local condition (executes)'),
        ('PC-REP-02', 'cmd', loc('command_result', '==', 'xyz', command='echo abc'),
         'SKIPPED', 'SYS-30/E2E-09', 'repeat: unsatisfied local condition (SKIPPED)'),
        ('PC-REP-03', 'cmd', loc('command_result', '==', '', command='exit 3'),
         'BLOCKED', 'E2E-09', 'repeat: unevaluable local condition (fail closed)'),
        ('PC-REP-04', 'remote', {'type': 'remote', 'remote': {
            'node_id': NODE_N1, 'property': 'online', 'operator': '==', 'value': 'online'}},
         'BLOCKED', 'E2E-10/SYS-27', 'repeat: remote online satisfied path (SYS-27 defect)'),
        ('PC-REP-05', 'file', loc('file_exists', '==', 'true', path=pf),
         'SUCCESS', 'SYS-25', 'repeat: boolean metric with >/< family'),
    ]
    out = []
    for (cid, key, cond, expect, dim, note) in table:
        got = []
        for i in range(6):
            r = run_one(st, key, '%s-R%d' % (cid, i + 1), cond, expect, 'marker',
                        '%s [repeat %d/6]' % (note, i + 1), dim)
            got.append({'status': r['status'], 'marker_present': r['marker_present'],
                        'block_reason': r['block_reason'], 'verdict': r['verdict']})
            r['case_id'] = cid
        statuses = sorted({g['status'] for g in got})
        out.append({'case_id': cid, 'task_key': key, 'expect': expect, 'dimension': dim,
                    'runs': len(got), 'distinct_statuses': statuses,
                    'deterministic': len(statuses) == 1 and statuses[0] == expect,
                    'detail': got})
    dump('phase4b-repeatability.json', {
        'rows': out, 'all_deterministic': all(r['deterministic'] for r in out)})
    return out


def phase_ui(st):
    """Browser capture of Execution detail UI for SUCCESS / SKIPPED / BLOCKED(local/remote)."""
    from playwright.sync_api import sync_playwright  # local import: optional dependency
    cases = read_cases()
    label = {'SUCCESS': '成功', 'SKIPPED': '已跳过', 'BLOCKED': '已阻断', 'FAILED': '失败'}
    pick = {}
    order = [('success', 'SUCCESS'), ('skipped', 'SKIPPED'), ('blocked_local', 'BLOCKED')]
    for tag, want in order:
        for r in cases:
            if r['status'] == want and r['dimension'].startswith(('SYS-25', 'SYS-30', 'E2E-09')):
                pick[tag] = r
                break
    for r in cases:                      # remote BLOCKED (SYS-27 defect UI feedback)
        if r['case_id'] == 'PC-REM-ON-01':
            pick['blocked_remote'] = r
            break

    out = {'captured_at_utc': now_utc(), 'base': BASE[0],
           'status_labels': label, 'pages': {}}
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=True)
        ctx = br.new_context(viewport={'width': 1440, 'height': 1200})
        ctx.add_init_script("localStorage.setItem('nodesteer_token', %s)" % json.dumps(TOKEN[0]))
        page = ctx.new_page()

        def snap(tag, url):
            page.goto(url, wait_until='networkidle', timeout=45000)
            page.wait_for_timeout(1500)
            return page.inner_text('body')

        for tag, r in pick.items():
            url = '%s/executions/%s' % (BASE[0], r['execution_id'])
            text = snap(tag, url)
            shot = EV / ('ui-execution-%s-%s.png' % (tag, r['case_id']))
            page.screenshot(path=str(shot), full_page=True)
            want = r['status']
            out['pages'][tag] = {
                'case_id': r['case_id'], 'execution_id': r['execution_id'], 'url': url,
                'expected_status': want, 'expected_status_label': label[want],
                'ui_contains_status_label': label[want] in text,
                'ui_contains_block_reason': (r.get('block_reason') or '') in text,
                'ui_contains_execution_id': r['execution_id'] in text,
                'ui_contains_node_hostname': NODE_N1_HOST in text,
                'ui_shows_empty_output': '（空）' in text,
                'ui_contains_stdout_marker': 'RAN_' in text,
                'body_text': text, 'screenshot': str(shot.relative_to(HERE))}

        # node detail page: definitive status of the disconnected node (INV-09 context)
        url = '%s/nodes/%s' % (BASE[0], NODE_OFFLINE)
        text = snap('node_offline', url)
        shot = EV / 'ui-node-offline.png'
        page.screenshot(path=str(shot), full_page=True)
        out['pages']['node_offline'] = {
            'node_id': NODE_OFFLINE, 'url': url,
            'ui_contains_hostname': NODE_OFFLINE_HOST in text,
            'ui_contains_offline_label': ('离线' in text),
            'body_text': text, 'screenshot': str(shot.relative_to(HERE))}

        # task detail page for the remote-condition task
        tid = st['tasks']['remote']['id']
        url = '%s/tasks/%s' % (BASE[0], tid)
        text = snap('task_remote', url)
        shot = EV / 'ui-task-remote-condition.png'
        page.screenshot(path=str(shot), full_page=True)
        out['pages']['task_remote_condition'] = {
            'task_id': tid, 'url': url,
            'ui_mentions_node_id_hostname': NODE_N1_HOST in text,
            'ui_shows_blocked_executions': '已阻断' in text,
            'body_text': text, 'screenshot': str(shot.relative_to(HERE))}
        br.close()
    dump('phase6-ui.json', out)
    return out


def phase_cleanup(st):
    results = []
    for key, t in st['tasks'].items():
        dc, db = api('DELETE', '/api/tasks/' + t['id'])
        gc, gb = api('GET', '/api/tasks/' + t['id'], allowed=(200, 404))
        results.append({'kind': 'task', 'key': key, 'id': t['id'], 'name': t['name'],
                        'delete_http': dc, 'get_http_after_delete': gc,
                        'deleted': dc in (200, 204) and gc == 404})
    root = st['host_root']
    rc, so, se = ssh(N1, 'rm -rf %s && if [ -e %s ]; then echo STILL_PRESENT; else echo GONE; fi'
                     % (root, root))
    host = {'root': root, 'rm_rc': rc, 'stdout': so.strip(), 'stderr': se,
            'removed': 'GONE' in so}
    out = {'at_utc': now_utc(), 'fixtures': results,
           'all_deleted_get404': all(r['deleted'] for r in results), 'host_root': host}
    dump('phase7-cleanup.json', out)
    return out


def phase_residue(st):
    _, nodes = api('GET', '/api/nodes')
    _, settings = api('GET', '/api/settings')
    _, tasks = api('GET', '/api/tasks')
    _, scheds = api('GET', '/api/schedules')
    _, scripts = api('GET', '/api/scripts')
    owned_prefix = 'qa-pcond-' + st['nonce'] + '-'
    _, exs = api('GET', '/api/executions?limit=200')
    owned_execs = [e for e in (exs or [])
                   if e.get('task_id') in {t['id'] for t in st['tasks'].values()}]
    rc, so, se = ssh(N1, 'ls -d /var/lib/nodesteer-qa-pcond-* 2>/dev/null; echo "---procs"; '
                         'pgrep -a -f "qa-pcond" | grep -v pgrep | head; echo "---agentunits"; '
                         'systemctl is-active nodesteer-agent-repair-20260930-101945-native-1.service; '
                         'systemctl is-active nodesteer-agent.service')
    remote_written = [k for k, t in st['tasks'].items()]
    out = {
        'at_utc': now_utc(),
        'owned_task_prefix_residue': [t.get('name') for t in (tasks or [])
                                      if owned_prefix in (t.get('name') or '')],
        'owned_schedule_prefix_residue': [s.get('name') for s in (scheds or [])
                                          if owned_prefix in (s.get('name') or '')],
        'owned_script_prefix_residue': [s.get('name') for s in (scripts or [])
                                        if owned_prefix in (s.get('name') or '')],
        'owned_execution_history_retained': {
            'count': len(owned_execs),
            'note': 'Execution history has no DELETE API; retained like every prior batch. '
                    'All rows belong to owned task ids created by this batch.',
            'task_ids': sorted({e.get('task_id') for e in owned_execs}),
        },
        'settings_after': settings,
        'settings_unchanged': settings == st['guard'].get('settings_before'),
        'nodes_status': [{'id': n['id'], 'status': n.get('status'),
                          'sync_status': n.get('sync_status')} for n in (nodes or [])],
        'native1_host': {'stdout': so, 'stderr': se, 'rc': rc},
        'no_owned_child_processes': 'qa-pcond' not in so,
        'host_root_present': bool([l for l in so.splitlines()
                                   if l.startswith('/var/lib/nodesteer-qa-pcond-')]),
        'touched_task_keys': remote_written,
    }
    dump('phase8-residue-oracle.json', out)
    return out


def bootstrap():
    """Idempotent auth bootstrap so phases can also run in separate invocations."""
    cred = json.loads(CRED.read_text())
    conn = json.loads(CONN.read_text())
    BASE[0] = conn['hub_url'].rstrip('/')
    sp = urlsplit(BASE[0])
    if sp.port != 18080:
        raise SystemExit('endpoint guard failed: repair :18080 required, got %s' % sp.port)
    code, body = api('POST', '/api/login',
                     {'username': cred['admin_username'], 'password': cred['admin_password']})
    if code != 200 or not (body or {}).get('token'):
        raise SystemExit('login failed HTTP %s' % code)
    TOKEN[0] = body['token']
    return cred, conn


def main():
    if not EV.exists():
        EV.mkdir(parents=True)
    bootstrap()
    st = load_state()
    nonce = st.get('nonce') or secrets.token_hex(4)
    st['nonce'] = nonce
    save_state(st)
    phases = sys.argv[1:] or ['guard', 'root', 'fixtures', 'matrix', 'repeat', 'ui', 'cleanup',
                              'residue']
    for ph in phases:
        t0 = time.time()
        if ph == 'guard':
            g = phase_guard(st)
            print('[guard] 18080=%s clock_skew_median=%s' %
                  (g['guard_18080'], g['clock_skew_hub_minus_native1_s']['median']))
        elif ph == 'root':
            print('[root] %s' % host_root(st))
        elif ph == 'fixtures':
            tasks, hist = phase_fixtures(st)
            print('[fixtures] %d tasks, history=%s' % (len(tasks), hist))
        elif ph == 'matrix':
            rows = phase_local_and_remote(st)
            print('[matrix] %d rows in this pass, total recorded=%d'
                  % (len(rows), len(read_cases())))
        elif ph == 'repeat':
            reps = phase_repeat(st)
            print('[repeat] all_deterministic=%s' % all(r['deterministic'] for r in reps))
        elif ph == 'ui':
            u = phase_ui(st)
            print('[ui] %s' % {k: {kk: vv for kk, vv in v.items() if kk.startswith('ui_')} for k, v in u['pages'].items()})
        elif ph == 'cleanup':
            c = phase_cleanup(st)
            print('[cleanup] all_deleted_get404=%s host_root=%s'
                  % (c['all_deleted_get404'], c['host_root']['removed']))
        elif ph == 'residue':
            r = phase_residue(st)
            print('[residue] task=%s schedule=%s script=%s settings_unchanged=%s'
                  % (r['owned_task_prefix_residue'], r['owned_schedule_prefix_residue'],
                     r['owned_script_prefix_residue'], r['settings_unchanged']))
        print('  (%s took %.1fs)' % (ph, time.time() - t0))


if __name__ == '__main__':
    main()
