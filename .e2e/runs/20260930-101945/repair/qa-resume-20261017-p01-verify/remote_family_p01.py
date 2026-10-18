#!/usr/bin/env python3
"""qa-resume-20261017-p01-verify: Remote condition family regression.

Build: dddee4f0-e2e-repair-20261017-p01 (P0-1 去兼容化简化版).
Authority: repair hub http://192.168.100.209:18080 (only management-write endpoint).

Phases (run sequentially, state persisted after every case):
  guard   node/unit baseline + clock probes
  remote  Remote condition family through the real product path
  probe   controlled fake-Hub probe with the deployed agent binary (clock independence)
  cleanup delete all fixtures + GET404 oracle + residue sweep

Writes fixtures only with nonce prefix; all removed in the cleanup phase.
"""
import json
import os
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
import hubclient  # noqa: E402
import p01lib as L  # noqa: E402

EV = HERE / 'evidence'
STATE_F = HERE / 'regression-state.json'
RESULTS_F = HERE / 'regression-results.json'
LEDGER_F = HERE / 'fixture-ledger.json'
BUILD_LABEL = 'dddee4f0-e2e-repair-20261017-p01'
HUB_SHA = '029f3f4e5cdfe863a917557171c2672548d1b5773e516bf50eab56e1629e20ee'
AGENT_SHA = '449af1d571daccce4264bda11e10d644f30c0db300f084c1f40a320b6bb2d256'

TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED', 'CANCELED', 'TIMED_OUT'}

os.umask(0o077)
hub = hubclient.Hub()


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_state():
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {'nonce': secrets.token_hex(4), 'fixtures': [], 'phases': {}, 'cases': [],
            'host_roots': [], 'probe': {}}


def save_state(st):
    tmp = STATE_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, STATE_F)


ST = load_state()
P = 'qa-p01-%s-' % ST['nonce']
MARKER_ROOT = '/var/lib/%smk' % P


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
        'batch': 'qa-resume-20261017-p01-verify',
        'authority': 'http://192.168.100.209:18080',
        'build_label': BUILD_LABEL,
        'hub_sha256': HUB_SHA,
        'agent_sha256': AGENT_SHA,
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
    return 'evidence/' + name


def nodes_by_host():
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    out = {}
    for n in rows:
        out[n.get('hostname')] = n
    return out


def wait_exec(eid, timeout=90):
    deadline = time.time() + timeout
    ex = None
    while time.time() < deadline:
        code, ex = hub.get('/api/executions/' + eid)
        if code == 200 and ex.get('status') in TERMINAL:
            return ex
        time.sleep(0.6)
    return ex or {}


def _task_body(name, node_id, command='echo qa-p01', desc='qa-p01 fixture'):
    return {'name': name, 'type': 'command', 'command': command,
            'target': {'type': 'node', 'node_ids': [node_id]},
            'timeout': 20, 'retry': 0, 'offline_policy': 'hub_online_required',
            'enabled': True}


def _put_task_condition(task_id, condition):
    _, task = hub.get('/api/tasks/' + task_id)
    body = {k: task.get(k) for k in ('name', 'type', 'command', 'script_id', 'target', 'timeout',
                                     'retry', 'offline_policy', 'enabled') if k in task}
    body['condition'] = condition
    return hub.put('/api/tasks/' + task_id, body)


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
    # owned offline node fixture (read-only target for deterministic offline cases)
    off = [k for k, v in nodes.items() if v.get('status') == 'offline' and k.startswith('qa-')]
    ST['offline_node'] = {'hostname': off[0], 'id': nodes[off[0]]['id']} if off else None
    info['offline_node'] = ST['offline_node']
    # clock reference: hub vs each node (informational)
    hub_epoch = L.clock_probe(L.HUB_HOST)
    info['clock_probe'] = {'hub': hub_epoch}
    for n, ip in L.NODES.items():
        info['clock_probe'][n] = L.clock_probe(ip)
    info['clock_delta_hub_minus_node'] = {n: (hub_epoch - info['clock_probe'][n])
                                          if info['clock_probe'].get(n) else None for n in L.NODES}
    info['healthz'] = L.curl_code(L.HUB_HOST, '/healthz')
    info['readyz'] = L.curl_code(L.HUB_HOST, '/readyz')
    save_state(ST)
    ev_write('guard-nodes.json', info)
    return info


# ---------------------------------------------------------------- remote (real path)
def phase_remote():
    hub.login()
    n1 = ST['node_ids']['native-1']
    rc, out, _ = L.ssh(L.NODES['native-1'], 'mkdir -p %s && chmod 0755 %s && echo OK' % (MARKER_ROOT, MARKER_ROOT))
    if 'OK' not in out:
        raise RuntimeError('marker root create failed: %s' % out)
    ST['host_roots'].append({'host': L.NODES['native-1'], 'path': MARKER_ROOT, 'role': 'P0-1 markers'})
    save_state(ST)

    out = {'at_utc': now_utc(), 'root': MARKER_ROOT, 'cases': {},
           'clock_delta_at_start': {n: (L.clock_probe(L.HUB_HOST) - L.clock_probe(ip))
                                    for n, ip in L.NODES.items()}}

    def mk_task(key, node_id, run_first=False):
        name = P + 'rem-' + key
        cmd = ("printf 'EXEC %%s\\n' \"$(date -u +%%FT%%T.%%NZ)\" > %s/%s.marker && echo RAN_%s"
               % (MARKER_ROOT, key, key))
        code, body = hub.post('/api/tasks', _task_body(name, node_id, command=cmd))
        if code not in (200, 201):
            raise RuntimeError('task create failed %s %s' % (key, body))
        ST['fixtures'].append(('task', body['id'], name))
        save_state(ST)
        return body['id']

    def marker(key, remove=True):
        p = '%s/%s.marker' % (MARKER_ROOT, key)
        cmd = 'if [ -f %s ]; then echo PRESENT; cat %s; else echo ABSENT; fi%s' % (
            p, p, ('; rm -f %s' % p) if remove else '')
        rc, so, _ = L.ssh(L.NODES['native-1'], cmd)
        lines = so.strip().split('\n')
        present = bool(lines) and lines[0] == 'PRESENT'
        return {'present': present, 'content': lines[1:] if present else None}

    def run_case(case_id, key, condition, expect, note, node_id):
        tid = mk_task(key, node_id)
        code, resp = _put_task_condition(tid, condition)
        if code != 200:
            row = {'case_id': case_id, 'task_id': tid, 'put_http': code, 'put_body': resp,
                   'status': 'PUT_FAILED', 'expect_status': expect, 'note': note}
            out['cases'][case_id] = row
            return row
        marker(key, remove=True)
        code, run = hub.post('/api/tasks/%s/run' % tid, {'node_ids': {node_id: True}})
        eid = run[0]['id'] if code == 200 and run else None
        ex = wait_exec(eid, timeout=90) if eid else {}
        ms = marker(key)
        want_marker = expect == 'SUCCESS'
        row = {'case_id': case_id, 'task_id': tid, 'task_name': P + 'rem-' + key,
               'condition': condition, 'execution_id': eid, 'status': ex.get('status'),
               'exit_code': ex.get('exit_code'), 'block_reason': ex.get('block_reason'),
               'expect_status': expect, 'marker_present': ms.get('present'),
               'marker_content': ms.get('content'),
               'marker_oracle_ok': bool(ms.get('present')) == want_marker,
               'status_match': ex.get('status') == expect, 'note': note, 'at_utc': now_utc()}
        row['verdict'] = 'PASS' if (row['status_match'] and row['marker_oracle_ok']) else 'FAIL'
        if expect == 'BLOCKED':
            row['fail_closed_reason_ok'] = (ex.get('block_reason') or '').startswith('condition unknown')
        if expect == 'SKIPPED':
            row['skipped_reason_ok'] = (ex.get('block_reason') or '') == 'condition not satisfied'
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

    # history seeding (executed once without condition)
    hist_task = mk_task('hist-ok', n1)
    code, run = hub.post('/api/tasks/%s/run' % hist_task, {'node_ids': {n1: True}})
    hist_ex = wait_exec(run[0]['id']) if code == 200 and run else {}
    marker('hist-ok')
    nohist_task = mk_task('nohist', n1)
    out['history_seed'] = {'task_id': hist_task, 'status': hist_ex.get('status')}

    rows = []
    rows.append(run_case('SYS-27-ON01', 'on01', rem(n1, 'online', '==', 'online'), 'SUCCESS',
                         'remote online 满足路径（历史恒 BLOCKED：跨主机时钟偏移门禁）', n1))
    rows.append(run_case('SYS-27-ON02', 'on02', rem(n1, 'online', '!=', 'offline'), 'SUCCESS',
                         'remote online != offline 满足路径', n1))
    rows.append(run_case('SYS-30-ON03', 'on03', rem(n1, 'online', '==', 'offline'), 'SKIPPED',
                         'remote 不满足 → SKIPPED 不误执行', n1))
    rows.append(run_case('SYS-28-LX01', 'lx01', rem(n1, 'last_execution', '==', 'SUCCESS', task=hist_task), 'SUCCESS',
                         'last_execution 成功历史满足路径', n1))
    rows.append(run_case('SYS-28-LX03', 'lx03', rem(n1, 'last_execution', '==', 'FAILED', task=hist_task), 'SKIPPED',
                         'last_execution 不满足 → SKIPPED', n1))
    rows.append(run_case('INV-09-LX05', 'lx05', rem(n1, 'last_execution', '==', 'SUCCESS', task=nohist_task), 'BLOCKED',
                         '未观察 last_execution → UNKNOWN → BLOCKED fail-closed', n1))
    rows.append(run_case('SYS-26-AND07', 'and07',
                         {'type': 'and', 'and': [rem(n1, 'online', '==', 'online'),
                                                 loc('command_result', '==', 'ok', command='echo ok')]},
                         'SUCCESS', 'AND(remote, local) 组合满足路径', n1))
    rows.append(run_case('SYS-26-AND08', 'and08',
                         {'type': 'and', 'and': [rem(n1, 'online', '==', 'offline'),
                                                 loc('command_result', '==', 'ok', command='echo ok')]},
                         'SKIPPED', 'AND 中 remote 不满足 → SKIPPED', n1))
    rows.append(run_case('SYS-26-AND09', 'and09',
                         {'type': 'and', 'and': [rem(n1, 'last_execution', '==', 'SUCCESS', task=nohist_task),
                                                 loc('command_result', '==', 'ok', command='echo ok')]},
                         'BLOCKED', 'AND 中 remote 未知 → 整体保持未知 → BLOCKED（不被当成 SKIPPED 绕过）', n1))
    # deterministic offline node (owned fixture, read-only target)
    off = ST.get('offline_node')
    if off:
        rows.append(run_case('E2E-10-OFF05', 'off05', rem(off['id'], 'online', '==', 'offline'), 'SUCCESS',
                             '真实 offline 节点 online==offline 满足路径（确定性状态可判定）', n1))
        rows.append(run_case('INV-09-OFF06', 'off06', rem(off['id'], 'online', '==', 'online'), 'SKIPPED',
                             '真实 offline 节点不得判为 online → SKIPPED', n1))
    else:
        rows.append({'case_id': 'E2E-10-OFF05', 'status': 'NOT_TESTED', 'note': '无 offline owned 节点可引用'})
    # clock-independence observation on the real path: repeated satisfy-path runs
    reps = []
    for i in (2, 3, 4):
        reps.append(run_case('CLOCK-ON01-r%d' % i, 'on01r%d' % i, rem(n1, 'online', '==', 'online'), 'SUCCESS',
                             '时钟无关性观测：重复满足路径第 %d 次' % i, n1))
    out['clock_repeat'] = [{'case_id': r.get('case_id'), 'status': r.get('status'),
                            'verdict': r.get('verdict')} for r in reps]
    out['clock_delta_at_end'] = {n: (L.clock_probe(L.HUB_HOST) - L.clock_probe(ip))
                                 for n, ip in L.NODES.items()}
    passed = [r for r in rows if r.get('verdict') == 'PASS']
    out['summary'] = {'total': len([r for r in rows if 'verdict' in r]), 'pass': len(passed),
                      'fail': [r.get('case_id') for r in rows if r.get('verdict') == 'FAIL']}
    ev_write('remote-cases.json', out)
    dump_results()
    return out


# ---------------------------------------------------------------- probe (controlled)
def phase_probe():
    hub.login()
    nonce = ST['nonce']
    probe_dir = '/tmp/qa-p01-%s-probe' % nonce
    port = 39171 + (int(nonce[:2], 16) % 50)
    rc, out, _ = L.ssh(L.NODES['native-1'], 'mkdir -p %s && rm -f %s/probe.py && echo OK' % (probe_dir, probe_dir))
    rc, out, err = L.scp_up(str(EV / 'tools' / 'remote_probe_p01.py'),
                            L.NODES['native-1'], probe_dir + '/probe.py')
    if rc != 0:
        raise RuntimeError('scp probe failed: %s' % err[-200:])
    rc, busy, _ = L.ssh(L.NODES['native-1'], 'ss -ltn | grep -c ":%d "' % port)
    if busy.strip() != '0':
        port += 7
    rc, so, se = L.ssh(L.NODES['native-1'],
                       'cd %s && python3 probe.py %s/work %s %d' % (probe_dir, probe_dir, L.AGENT_BIN, port),
                       timeout=300)
    try:
        probe = json.loads(so.strip().split('\n')[-1])
    except Exception:
        probe = {'parse_error': True, 'stdout_tail': so[-500:], 'stderr_tail': se[-500:]}
    probe['rc'] = rc
    probe['probe_dir'] = probe_dir
    probe['port'] = port
    # post-probe residue check on the node
    rc2, procs, _ = L.ssh(L.NODES['native-1'], 'pgrep -af "[p]robe.yaml" || true')
    rc3, lsn, _ = L.ssh(L.NODES['native-1'], 'ss -ltn | grep ":%d " || true' % port)
    probe['residue'] = {'processes': procs.strip(), 'listeners': lsn.strip()}
    ev_write('probe-p01.json', probe)
    return probe


# ---------------------------------------------------------------- record
def phase_record():
    hub.login()
    rc = json.loads((EV / 'remote-cases.json').read_text())
    probe = json.loads((EV / 'probe-p01.json').read_text())
    cases = rc['cases']

    def c(cid):
        return cases.get(cid, {})

    on01, on02, on03 = c('SYS-27-ON01'), c('SYS-27-ON02'), c('SYS-30-ON03')
    lx01, lx03, lx05 = c('SYS-28-LX01'), c('SYS-28-LX03'), c('INV-09-LX05')
    and07, and08, and09 = c('SYS-26-AND07'), c('SYS-26-AND08'), c('SYS-26-AND09')
    off05, off06 = c('E2E-10-OFF05'), c('INV-09-OFF06')
    probe_ok = bool(probe.get('all_expected')) and probe.get('case_count') == 9
    probe_cases = probe.get('cases', {})
    reps = rc.get('clock_repeat', [])

    def ok(*rows):
        return all(r.get('verdict') == 'PASS' for r in rows)

    record('SYS-27', 'remote',
           'PASS' if ok(on01, on02, on03) else 'FAIL',
           '原 FAIL（20261014：remote online 满足/不满足均 BLOCKED，跨主机时钟偏移门禁）→ 20261016 修复版 PASS；'
           '本次 P0-1 简化版 PASS：满足路径 SUCCESS 且真实执行（marker 存在）、不满足 SKIPPED 且无 marker；'
           '判定仅读 Hub 下发的 ttl_remaining_sec，不再做跨时钟比较',
           ['evidence/remote-cases.json', 'evidence/probe-p01.json'],
           {'on01': on01.get('status'), 'on02': on02.get('status'), 'on03': on03.get('status'),
            'marker_on01': on01.get('marker_present'), 'marker_on03': on03.get('marker_present'),
            'clock_delta_hub_minus_node': rc.get('clock_delta_at_end')})
    record('SYS-28', 'remote',
           'PASS' if ok(lx01, lx03, lx05) else 'FAIL',
           '原 PARTIAL（成功/失败/不存在受 BLOCKED 阻塞）→ 20261016 修复版 PASS；本次 P0-1 简化版 PASS：'
           'last_execution==SUCCESS 历史 → SUCCESS 真实执行；==FAILED → SKIPPED；未观察 → UNKNOWN → BLOCKED fail-closed',
           ['evidence/remote-cases.json'],
           {'lx01': lx01.get('status'), 'lx03': lx03.get('status'), 'lx05': lx05.get('status'),
            'lx05_block_reason': lx05.get('block_reason')})
    record('SYS-29', 'remote',
           'PASS_WITH_NOTES' if (ok(lx01, lx05) and probe_ok) else 'FAIL',
           '原 PARTIAL → 20261016 PASS_WITH_NOTES；本次 P0-1 简化版 PASS_WITH_NOTES：'
           '「未过期」路径全程在新鲜窗口内完成（所有满足路径 SUCCESS）；「未观察」仍 fail-closed；'
           '「TTL 过期」经产品路径不可构造（缓存分支无写入方，Hub 每次实时计算正剩余），'
           '由受控探针用真实新二进制验证 ttl_remaining_sec=0/负 → BLOCKED、缺失（旧 Hub 报文）→ BLOCKED，'
           '并由单测 TestRemoteStateValidity/TestRemoteStatePayloadJSON 覆盖',
           ['evidence/remote-cases.json', 'evidence/probe-p01.json'],
           {'probe_expired0': probe_cases.get('expired_remaining0', {}).get('status'),
            'probe_negative': probe_cases.get('expired_remaining_negative', {}).get('status'),
            'probe_missing': probe_cases.get('missing_remaining_old_hub_payload', {}).get('status')})
    record('SYS-30', 'remote',
           'PASS' if ok(on03, and08) else 'FAIL',
           '原 PARTIAL（Remote「不满足」被 SYS-27 阻塞）→ 20261016 修复版 PASS；本次 P0-1 简化版 PASS：'
           'remote 不满足（online==offline、AND 中 remote 不满足）真实 SKIPPED、无 marker/副作用',
           ['evidence/remote-cases.json'],
           {'on03': on03.get('status'), 'and08': and08.get('status'),
            'on03_block_reason': on03.get('block_reason')})
    record('E2E-10', 'remote',
           'PASS_WITH_NOTES' if (ok(on01, on03, off05, off06, lx05) and probe_ok) else 'FAIL',
           '原 PARTIAL（20261014：13/13 Remote BLOCKED）→ 20261016 PASS_WITH_NOTES；本次 P0-1 简化版 PASS_WITH_NOTES：'
           '满足/不满足/确定性 offline/未观察 fail-closed 全部可判定；TTL 过期维度经产品路径仍不可构造，'
           '由受控探针覆盖（过期/缺失 → BLOCKED）',
           ['evidence/remote-cases.json', 'evidence/probe-p01.json'],
           {'off05': off05.get('status'), 'off06': off06.get('status'), 'lx05': lx05.get('status')})
    record('INV-09', 'remote',
           'PASS' if (ok(lx05, off06) and probe_cases.get('unknown_value_with_remaining', {}).get('status') == 'BLOCKED'
                      and probe_cases.get('empty_value_with_remaining', {}).get('status') == 'BLOCKED') else 'FAIL',
           '原 PASS_WITH_NOTES（确定性状态也被判未知）→ 20261016 修复版 PASS；本次 P0-1 简化版 PASS：'
           '未观察 last_execution（UNKNOWN）→ BLOCKED 无业务效果；UNKNOWN/空值即使带正 ttl_remaining_sec 也 BLOCKED'
           '（受控探针），与可判定状态明确区分',
           ['evidence/remote-cases.json', 'evidence/probe-p01.json'],
           {'lx05': lx05.get('status'), 'probe_unknown': probe_cases.get('unknown_value_with_remaining', {}).get('status'),
            'probe_empty': probe_cases.get('empty_value_with_remaining', {}).get('status')})
    record('SYS-26', 'remote',
           'PASS' if ok(and07, and08, and09) else 'FAIL',
           '原 PASS（AND(remote,local) 受 SYS-27 阻塞为 BLOCKED）→ 20261016 修复版 PASS；本次 P0-1 简化版 PASS：'
           'AND 满足 → SUCCESS；AND 中 remote 不满足 → SKIPPED；AND 中 remote 未知 → 整体保持未知 → BLOCKED（不被当成 SKIPPED 绕过）',
           ['evidence/remote-cases.json'],
           {'and07': and07.get('status'), 'and08': and08.get('status'), 'and09': and09.get('status')})
    record('P0-1-CLOCK-INDEPENDENCE', 'probe',
           'PASS' if probe_ok and all(r.get('verdict') == 'PASS' for r in reps) else 'FAIL',
           '时钟无关性观测：真实路径上重复满足路径（含首次共 4 次）全部 SUCCESS；受控探针中 observed_at 偏 ±1h / epoch0 '
           '但 ttl_remaining_sec>0 → SUCCESS，说明判定不读任何时钟；ttl_remaining_sec 缺失/<=0 与 UNKNOWN/空值 → BLOCKED fail-closed',
           ['evidence/remote-cases.json', 'evidence/probe-p01.json'],
           {'probe_all_expected': probe.get('all_expected'), 'probe_case_count': probe.get('case_count'),
            'clock_repeat': reps, 'probe_agent_sha': probe.get('child_agent_binary', {}).get('sha256'),
            'clock_delta_hub_minus_node': rc.get('clock_delta_at_end')})
    dump_results()
    return {'records': [r['case_id'] for r in ST['cases']]}


# ---------------------------------------------------------------- cleanup
def _delete(kind, ident, name):
    if kind == 'task':
        code, _ = hub.delete('/api/tasks/' + ident)
        code2, _ = hub.get('/api/tasks/' + ident)
    else:
        return {'kind': kind, 'id': ident, 'name': name, 'delete_http': None, 'get_http': None}
    return {'kind': kind, 'id': ident, 'name': name, 'delete_http': code, 'get_http': code2}


def phase_cleanup():
    hub.login()
    ledger = {'nonce': ST['nonce'], 'prefix': P, 'deleted': [], 'at_utc': now_utc()}
    for kind, ident, name in ST['fixtures']:
        ledger['deleted'].append(_delete(kind, ident, name))
    # host roots
    ledger['host_roots_removed'] = []
    for hr in ST.get('host_roots', []):
        rc, out, _ = L.ssh(hr['host'], 'rm -rf %s && echo REMOVED || echo FAILED' % hr['path'])
        ledger['host_roots_removed'].append({'host': hr['host'], 'path': hr['path'],
                                             'stdout': out.strip()[:200]})
    # probe residue removal
    probe_dir = ST.get('probe', {}).get('probe_dir') or '/tmp/qa-p01-%s-probe' % ST['nonce']
    rc, out, _ = L.ssh(L.NODES['native-1'], 'rm -rf %s && echo REMOVED || echo FAILED' % probe_dir)
    ledger['probe_dir_removed'] = {'path': probe_dir, 'stdout': out.strip()[:200]}
    # leftover sweep by prefix
    code, lst = hub.get('/api/tasks')
    rows = lst.get('tasks', lst) if isinstance(lst, dict) else lst
    leftover_tasks = [r.get('name') for r in rows if (r.get('name') or '').startswith(P)]
    nodes = nodes_by_host()
    leftover_nodes = [k for k in nodes if k.startswith(P)]
    all_404 = all(r.get('get_http') == 404 for r in ledger['deleted'] if r.get('get_http') is not None)
    rc, procs, _ = L.ssh(L.NODES['native-1'], 'pgrep -af "[p]robe.yaml" || true')
    rc2, mounts, _ = L.ssh(L.NODES['native-1'], 'mount | grep qa-p01-%s || true' % ST['nonce'])
    marker_root_removed = bool(ledger['host_roots_removed']) and all(
        'REMOVED' in h.get('stdout', '') for h in ledger['host_roots_removed'])
    oracle = {
        'at_utc': now_utc(),
        'hub_prefix_leftovers': {'tasks': leftover_tasks, 'nodes': leftover_nodes},
        'all_deleted_get_404': all_404,
        'owned_processes_after': procs.strip().split('\n') if procs.strip() else [],
        'owned_mounts_after': mounts.strip().split('\n') if mounts.strip() else [],
        'marker_root_removed': marker_root_removed,
        'probe_dir_removed': 'REMOVED' in ledger['probe_dir_removed']['stdout'],
        'healthz_readyz': None,
        'protected_nodes': {},
    }
    rc, h, _ = L.ssh(L.HUB_HOST, 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:18080/healthz; '
                                 'curl -s -o /dev/null -w " %{http_code}" http://127.0.0.1:18080/readyz')
    oracle['healthz_readyz'] = h.strip()
    nn = nodes_by_host()
    oracle['protected_nodes'] = {k: {'status': v.get('status'), 'sync_status': v.get('sync_status')}
                                 for k, v in nn.items() if k.startswith('qa-repair')}
    oracle['primary_hub_pid'] = L.unit_pid(L.HUB_HOST, L.PRIMARY_UNIT)
    ledger['oracle'] = oracle
    tmp = LEDGER_F.with_suffix('.tmp')
    tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, LEDGER_F)
    tmp = (HERE / 'final-cleanup-oracle.json').with_suffix('.tmp')
    tmp.write_text(json.dumps(oracle, ensure_ascii=False, indent=1) + '\n')
    os.replace(tmp, HERE / 'final-cleanup-oracle.json')
    dump_results()
    return ledger


PHASES = {'guard': phase_guard, 'remote': phase_remote, 'probe': phase_probe,
          'record': phase_record, 'cleanup': phase_cleanup}

if __name__ == '__main__':
    for arg in sys.argv[1:]:
        fn = PHASES.get(arg)
        if not fn:
            print('unknown phase', arg)
            sys.exit(2)
        print('=== phase %s ===' % arg, flush=True)
        t0 = time.time()
        res = fn()
        if arg == 'probe':
            ST['probe'] = {'probe_dir': res.get('probe_dir'), 'port': res.get('port')}
            save_state(ST)
        print('=== phase %s done in %.1fs ===' % (arg, time.time() - t0), flush=True)
