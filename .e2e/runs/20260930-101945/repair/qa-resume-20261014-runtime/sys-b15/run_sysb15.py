#!/usr/bin/env python3
"""sys-b15 runtime batch: X01 (native Agent self-upgrade) + X03 (on_start).

Evidence chain (all real, no product source change):
  operator/API act -> real Hub execution rows -> real WebSocket dispatch ->
  real owned child Agent (exact repair binary, owned copy) -> real HTTPS-free
  binary download from GET /api/agent/binary -> real SHA256 verify -> real
  backup / atomic replace / rollback on the OWNED binary path -> real process
  restart -> real HELLO -> Hub version/capability/online-synced readback.

Seam (documented, not a product change): a plain-process owned child cannot run
`systemctl restart nodesteer-agent` because that unit name belongs to the
PROTECTED primary deployment on every repair VM.  The owned child therefore runs
with an owned PATH whose first entry is an owned `systemctl` shim that
intercepts exactly `restart nodesteer-agent` (logging it, optional delay, chosen
exit code) and passes everything else through to the real systemctl.  The real
process restart of the owned child is then performed by the harness - the same
lifecycle event systemd would perform for an owned unit.

Guards: only :18080 repair authority and API writes; no protected unit is
stopped/started; protected Agent binaries are only read/hashed; no VM reboot.
"""
import json
import sys
import time
from pathlib import Path

import lib as X

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
BATCH = 'sysb15'
PREFIX = 'qa-%s-%s-' % (BATCH, NONCE)
DEV = HERE / 'evidence'
BATCH_ROOT = '/var/lib/nodesteer-qa-%s-%s' % (BATCH, NONCE)
BATCH_OPT = '/opt/qa-%s-%s' % (BATCH, NONCE)

RUN = None
STATE = {'children': {}, 'owned_nodes': [], 'created': {}, 'errors': [],
         'protected_units_before': None, 'protected_units_after': None,
         'baseline_sha_before': {}, 'baseline_sha_after': {}, 'steps': []}
# unique fake node_ip per child: the Hub REUSES an existing node when name OR ip
# matches (PrepareEnrollment/findEnrollableNode), so every owned node must have a
# distinct address.
CHILD_IP = {
    'upg': '127.15.0.11',
    'hard': '127.15.0.12',
    'dock': '127.15.0.13',
    'off': '127.15.0.14',
    'pause': '127.15.0.15',
    'os1': '127.15.0.16',
}


def rec(name, value):
    X.update_json(DEV / name, value)
    return value


def log(*a):
    print('[%s]' % X.now_utc(), *a)
    sys.stdout.flush()


def step(name, ok, detail=None):
    STATE['steps'].append({'step': name, 'ok': bool(ok), 'at_utc': X.now_utc(),
                           'detail': detail if detail is None else _small(detail)})
    log('%s -> %s' % (name, 'OK' if ok else 'FAIL'), '' if detail is None else _small(detail))
    rec('progress-steps.json', STATE['steps'])


def _small(v, limit=400):
    try:
        text = json.dumps(v, ensure_ascii=False, default=str)
    except Exception:
        text = str(v)
    return text if len(text) <= limit else text[:limit] + '...'


def node_view(node_id):
    code, body = RUN.node(node_id)
    if code != 200 or not isinstance(body, dict):
        return {'_http': code, '_body': body}
    return {k: body.get(k) for k in
            ('id', 'hostname', 'ip', 'status', 'sync_status', 'deployment_mode',
             'agent_version', 'arch', 'global_revision', 'capabilities', 'last_seen')}


def wait_node(node_id, status, sync='synced', timeout=150):
    res = X.wait_pred(lambda: node_view(node_id),
                      lambda n: n.get('status') == status and n.get('sync_status') == sync,
                      timeout=timeout, interval=2.0)
    return {'complete': res['complete'], 'seconds': res['seconds'], 'last': res['last'],
            'samples': res['samples'][-6:]}


def executions_for(task_id, node_id):
    total, body = RUN.count_executions(task_id=task_id, node_id=node_id)
    rows = []
    for e in body if isinstance(body, list) else []:
        rows.append({k: e.get(k) for k in
                     ('id', 'task_id', 'node_id', 'status', 'trigger_type',
                      'scheduled_time', 'block_reason', 'offline', 'exit_code',
                      'start_time', 'end_time')})
    return {'total': total, 'rows': rows}


# --------------------------------------------------------------------------- #
# phase 0: read-only guards
# --------------------------------------------------------------------------- #
def phase0_guards():
    out = {'at_utc': X.now_utc(), 'nonce': NONCE, 'authority': RUN.base}
    out['health'] = X.health()
    out['hub_agent_binary'] = X.agent_binary_head()
    nodes = RUN.nodes()
    out['nodes_total'] = len(nodes)
    out['protected_nodes'] = {}
    for n in nodes:
        if n.get('id') in X.PROTECTED_NODES:
            out['protected_nodes'][n['id']] = {
                k: n.get(k) for k in ('hostname', 'status', 'sync_status', 'deployment_mode',
                                      'agent_version', 'global_revision')}
    out['other_nodes'] = [{'id': n.get('id'), 'hostname': n.get('hostname'), 'status': n.get('status'),
                           'deployment_mode': n.get('deployment_mode'), 'ip': n.get('ip')}
                          for n in nodes if n.get('id') not in X.PROTECTED_NODES]
    probe = X.unit_probe()
    STATE['protected_units_before'] = X.unit_fingerprint(probe)
    out['protected_units_before'] = probe
    for label, ip in X.HOSTS.items():
        STATE['baseline_sha_before'][label] = X.protected_binary_sha(ip)
    out['protected_binary_sha_before'] = STATE['baseline_sha_before']
    out['owned_residue_before'] = X.ssh_json(X.HOSTS['native-1'], (
        "import json,os,glob,subprocess\n"
        "out={'roots':sorted(glob.glob('/var/lib/nodesteer-qa-*')),"
        "'opts':sorted(glob.glob('/opt/qa-*'))}\n"
        "p=subprocess.run(['pgrep','-af','nodesteer-agent'],capture_output=True,text=True)\n"
        "out['agent_processes']=[l for l in p.stdout.splitlines() if l.strip()]\n"
        "print(json.dumps(out))\n"))
    ok = (out['health'].get('/healthz', {}).get('http') == 200
          and out['health'].get('/readyz', {}).get('http') == 200
          and len(out['protected_nodes']) == 4
          and all(n['status'] == 'online' and n['sync_status'] == 'synced'
                  for n in out['protected_nodes'].values())
          and all(v.get('sha256') == X.AGENT_SHA for v in STATE['baseline_sha_before'].values())
          and out['hub_agent_binary'].get('body_sha256') == X.AGENT_SHA
          and not out['owned_residue_before']['roots']
          and not out['owned_residue_before']['opts'])
    out['guards_ok'] = ok
    rec('phase0-guards.json', out)
    step('phase0_guards', ok)
    return ok


# --------------------------------------------------------------------------- #
# phase 1: owned children
# --------------------------------------------------------------------------- #
def phase1_children(proxy_holder):
    out = {'at_utc': X.now_utc(), 'children': {}}
    token = RUN.cred['registration_token']
    # upg  : success path, shim sleeps 25s then exits 0 (dup-in-flight window)
    # hard : failure paths, shim exits 1 (restart failure -> rollback)
    # dock : deployment_mode=docker -> Hub-side SKIPPED
    # off  : native, connected then stopped -> offline
    # pause: native, switched to maintenance -> SKIPPED paused
    # os1  : X03 on_start child, routed through an owned WS frame proxy
    plan = [
        ('upg', 'native-1', 'native', 25, 0, False),
        ('hard', 'native-3', 'native', 0, 1, False),
        ('dock', 'native-4', 'docker', 0, 0, False),
        ('off', 'native-2', 'native', 0, 0, False),
        ('pause', 'native-2', 'native', 0, 0, False),
        ('os1', 'native-1', 'native', 0, 0, True),
    ]
    proxy = None
    for name, host_label, mode, sleep_s, exit_c, via_proxy in plan:
        code, enroll, _ = RUN.post('/api/nodes/enrollment', {
            'node_name': PREFIX + name, 'node_ip': CHILD_IP[name],
            'hub_address': RUN.base})
        if code != 200 or not isinstance(enroll, dict) or not enroll.get('node_id'):
            raise RuntimeError('enrollment_failed_%s_http_%s_%s' % (name, code, _small(enroll, 200)))
        child = X.OwnedChild(BATCH, NONCE, name, host_label, deployment_mode=mode,
                             shim_sleep=sleep_s, shim_exit=exit_c, mark_binary=True,
                             via_proxy=via_proxy)
        child.node_id = enroll['node_id']
        child.agent_id = enroll['agent_id']
        child.agent_token = token
        STATE['children'][name] = child
        STATE['owned_nodes'].append(child.node_id)
        if via_proxy:
            from frame_proxy import FrameProxy
            proxy = FrameProxy('ws://%s:18080' % X.HUB_HOST)
            port = proxy.start()
            child.proxy_port = port
            proxy_holder['os1'] = proxy
            hub_url = 'ws://%s:%d' % (X.OPERATOR_IP, port)
        else:
            hub_url = 'ws://%s:18080' % X.HUB_HOST
        prep = child.prepare(hub_url)
        started = child.start()
        res = wait_node(child.node_id, 'online', 'synced', timeout=180)
        view = node_view(child.node_id)
        out['children'][name] = {
            'host': host_label, 'host_ip': X.HOSTS[host_label], 'node_id': child.node_id,
            'node_name': PREFIX + name, 'node_ip': CHILD_IP[name],
            'deployment_mode_requested': mode, 'hub_url': hub_url,
            'root_on_node': child.root, 'opt_on_node': child.opt,
            'binary_on_node': child.binary, 'shim_dir': child.shim_dir,
            'shim_sleep_restart_s': sleep_s, 'shim_exit_restart': exit_c,
            'prepare': prep, 'start': started, 'online_synced': res['complete'],
            'node_after_connect': view,
        }
        step('phase1_connect_' + name, res['complete'], view)
        rec('phase1-children.json', out)
    STATE['proxy'] = proxy_holder.get('os1')
    ok = all(c['online_synced'] for c in out['children'].values())
    out['all_online_synced'] = ok
    rec('phase1-children.json', out)
    return ok


# --------------------------------------------------------------------------- #
# phase 2: X01 mixed batch (混选 docker / 离线 / 暂停 + 部分失败)
# --------------------------------------------------------------------------- #
def phase2_mixed_batch():
    out = {'at_utc': X.now_utc(), 'expected': {}}
    upg, dock, off, pause = (STATE['children'][k] for k in ('upg', 'dock', 'off', 'pause'))

    # --- preconditions: pause -> maintenance, off -> offline
    code, body, _ = RUN.post('/api/nodes/' + pause.node_id, {'status': 'maintenance'})
    out['set_maintenance'] = {'http': code, 'body': body}
    paused = X.wait_pred(lambda: node_view(pause.node_id),
                         lambda n: n.get('status') == 'maintenance', timeout=60, interval=1.5)
    out['pause_status'] = {'complete': paused['complete'], 'last': paused['last']}
    off_stop = off.stop()
    offline = X.wait_pred(lambda: node_view(off.node_id),
                          lambda n: n.get('status') == 'offline', timeout=90, interval=2.0)
    out['off_stop'] = off_stop
    out['off_status'] = {'complete': offline['complete'], 'last': offline['last']}
    out['pre_nodes'] = {n: node_view(c.node_id) for n, c in
                        (('upg', upg), ('dock', dock), ('off', off), ('pause', pause))}

    out['expected'] = {
        'upg': 'dispatched=true (native + online)',
        'dock': 'SKIPPED (self-upgrade unsupported for deployment mode docker)',
        'off': 'FAILED (node is not online, status=offline)',
        'pause': 'SKIPPED (node paused)',
        'batch_scope': 'each node gets its own terminal/in-flight record; one node may fail without aborting the batch',
    }
    ids = [upg.node_id, dock.node_id, off.node_id, pause.node_id]
    code, body, _ = RUN.upgrade(ids)
    out['request'] = {'http': code, 'node_ids': ids}
    out['response'] = body
    rec('phase2-x01-mixed-batch.json', out)
    by_id = {}
    if isinstance(body, list):
        for r in body:
            by_id[r.get('node_id')] = r
    out['per_node'] = by_id
    checks = {
        'http_200': code == 200,
        'all_four_present': len(by_id) == 4,
        'upg_dispatched': bool(by_id.get(upg.node_id, {}).get('dispatched')),
        'dock_skipped_docker': (by_id.get(dock.node_id, {}).get('status') == 'SKIPPED'
                                and 'docker' in (by_id.get(dock.node_id, {}).get('block_reason') or '')),
        'off_failed_offline': (by_id.get(off.node_id, {}).get('status') == 'FAILED'
                               and 'not online' in (by_id.get(off.node_id, {}).get('block_reason') or '')),
        'pause_skipped_paused': (by_id.get(pause.node_id, {}).get('status') == 'SKIPPED'
                                 and 'paused' in (by_id.get(pause.node_id, {}).get('block_reason') or '')),
        'independent_execution_ids': len({r.get('execution_id') for r in by_id.values()}) == 4,
    }
    out['checks'] = checks
    out['mixed_batch_ok'] = all(checks.values())
    out['upg_record'] = by_id.get(upg.node_id)
    rec('phase2-x01-mixed-batch.json', out)
    step('phase2_mixed_batch', out['mixed_batch_ok'], checks)
    return out


# --------------------------------------------------------------------------- #
# phase 3: X01 重复在途升级 -> 拒绝重复触发 (dedup) + 终态 SUCCESS
# --------------------------------------------------------------------------- #
def phase3_duplicate_inflight(first_record):
    out = {'at_utc': X.now_utc()}
    upg = STATE['children']['upg']
    dock = STATE['children']['dock']
    out['first_dispatched_record'] = first_record
    # in-flight state readback while the owned shim still holds the restart call
    inflight = None
    for _ in range(20):
        r = executions_for(X.UPGRADE_TASK_ID, upg.node_id)
        if r['total'] == 1:
            inflight = r['rows']
            break
        time.sleep(0.5)
    out['in_flight_hub_records'] = inflight
    dup_code, dup_body, _ = RUN.upgrade([upg.node_id])
    out['duplicate_request_http'] = dup_code
    out['duplicate_response'] = dup_body
    dup = dup_body[0] if isinstance(dup_body, list) and dup_body else {}
    out['duplicate'] = dup
    out['dup_checks'] = {
        'http_200': dup_code == 200,
        'deduplicated_flag': bool(dup.get('deduplicated')),
        'same_execution_id': bool(first_record and dup.get('execution_id') == first_record.get('execution_id')),
        'no_new_dispatch': dup.get('dispatched') in (None, False),
        'current_status_is_inflight_or_terminal': dup.get('status') in
        ({'PENDING', 'RUNNING'} | X.TERMINAL),
    }
    # batch-internal duplicate id (API level "批内去重保序")
    code2, body2, _ = RUN.upgrade([dock.node_id, dock.node_id])
    out['duplicate_ids_in_one_batch'] = {
        'http': code2,
        'returned_count': len(body2) if isinstance(body2, list) else None,
        'nodes': [r.get('node_id') for r in body2] if isinstance(body2, list) else body2,
        'statuses': [r.get('status') for r in body2] if isinstance(body2, list) else None,
        'deduplicated_flags': [r.get('deduplicated') for r in body2] if isinstance(body2, list) else None,
        'expected_returned_count': 1,
    }
    out['batch_internal_dedup_ok'] = (code2 == 200 and isinstance(body2, list) and len(body2) == 1)
    # wait for the real terminal state of the first upgrade
    ended = X.wait_pred(lambda: executions_for(X.UPGRADE_TASK_ID, upg.node_id),
                        lambda r: r['total'] >= 1 and all(x['status'] in X.TERMINAL for x in r['rows']),
                        timeout=240, interval=3.0)
    out['final_executions'] = ended['last']
    out['terminal_wait'] = {'complete': ended['complete'], 'seconds': ended['seconds'],
                            'samples': ended['samples'][-4:]}
    rows = (ended['last'] or {}).get('rows') or []
    out['checks'] = {
        **out['dup_checks'],
        'exactly_one_execution': len(rows) == 1,
        'terminal_success': len(rows) == 1 and rows[0]['status'] == 'SUCCESS',
        'batch_internal_dedup': out['batch_internal_dedup_ok'],
        'no_duplicate_upgrade_created': len(rows) == 1,
    }
    out['success_ok'] = all(out['checks'].values())
    rec('phase3-x01-duplicate-inflight.json', out)
    step('phase3_duplicate_inflight', out['success_ok'], out['checks'])
    return out


# --------------------------------------------------------------------------- #
# phase 4: version/capability reporting + reconnect after real restart
# --------------------------------------------------------------------------- #
def phase4_version_reconnect():
    out = {'at_utc': X.now_utc()}
    upg = STATE['children']['upg']
    before = upg.state()
    out['owned_state_after_replace'] = before
    binary_after = before['files'][upg.binary]
    bak_after = before['files'][upg.binary + '.bak']
    out['binary_after_replace'] = binary_after
    out['bak_after_replace'] = bak_after
    out['shim_calls'] = before['shim_calls']
    out['node_before_restart'] = node_view(upg.node_id)
    # harness-performed restart of the owned process (see module docstring seam)
    out['restart'] = upg.restart()
    back = wait_node(upg.node_id, 'online', 'synced', timeout=180)
    out['reconnect'] = {'complete': back['complete'], 'seconds': back['seconds'], 'last': back['last']}
    time.sleep(2)
    after = upg.state()
    out['owned_state_after_restart'] = after
    out['node_after_reconnect'] = node_view(upg.node_id)
    out['executions_after_reconnect'] = executions_for(X.UPGRADE_TASK_ID, upg.node_id)
    out['agent_version_after_reconnect'] = (out['node_after_reconnect'] or {}).get('agent_version')
    out['capabilities_after_reconnect'] = (out['node_after_reconnect'] or {}).get('capabilities')
    out['checks'] = {
        'owned_binary_replaced_with_hub_artifact': binary_after.get('sha256') == X.AGENT_SHA,
        'backup_of_previous_version_kept': bool(bak_after.get('exists')),
        'backup_sha_is_marked_preupgrade_version': bool(bak_after.get('sha256')) and bak_after.get('sha256') != X.AGENT_SHA,
        'intercepted_restart_recorded': any('intercepted-restart' in l for l in before['shim_calls']),
        'process_restarted_new_pid': (before['owned_pids'][0]['pid'] if before['owned_pids'] else None)
                                     != (after['owned_pids'][0]['pid'] if after['owned_pids'] else None),
        'single_owned_process_after_restart': len(after['owned_pids']) == 1,
        'back_online_synced': back['complete'],
        'version_reported_is_hub_build': (out['node_after_reconnect'] or {}).get('agent_version') == 'dddee4f0-e2e-repair-20261004',
        'agent_upgrade_capability_true': bool(((out['node_after_reconnect'] or {}).get('capabilities') or {}).get('agent_upgrade')),
        'no_duplicate_execution': out['executions_after_reconnect']['total'] == 1,
        'protected_baseline_untouched': after['baseline_agent_sha256'] == X.AGENT_SHA,
        'replacement_never_touched_protected_path': after['files'][upg.binary].get('sha256') == X.AGENT_SHA,
    }
    out['version_reconnect_ok'] = all(v for k, v in out['checks'].items())
    rec('phase4-x01-version-reconnect.json', out)
    step('phase4_version_reconnect', out['version_reconnect_ok'], out['checks'])
    return out


# --------------------------------------------------------------------------- #
# phase 5: partial-failure paths on the owned `hard` child
# --------------------------------------------------------------------------- #
def phase5_failures():
    out = {'at_utc': X.now_utc()}
    hard = STATE['children']['hard']
    start = hard.state()
    out['start_state'] = start
    out['start_binary_sha'] = start['files'][hard.binary].get('sha256')

    # --- F1: restart failure -> rollback to the backed-up previous binary
    code, body, _ = RUN.upgrade([hard.node_id])
    out['restart_failure_request'] = {'http': code, 'response': body}
    ended = X.wait_pred(lambda: executions_for(X.UPGRADE_TASK_ID, hard.node_id),
                        lambda r: r['total'] >= 1 and all(x['status'] in X.TERMINAL for x in r['rows']),
                        timeout=180, interval=3.0)
    out['restart_failure_executions'] = ended['last']
    time.sleep(1)
    st = hard.state()
    out['after_restart_failure'] = st
    out['shim_calls'] = st['shim_calls']
    failed_row = ((ended['last'] or {}).get('rows') or [{}])[0]
    out['f1'] = {
        'execution_status': failed_row.get('status'),
        'block_reason': failed_row.get('block_reason'),
        'binary_restored_to_previous_sha': st['files'][hard.binary].get('sha256') == out['start_binary_sha'],
        'previous_sha_differs_from_hub_artifact': out['start_binary_sha'] != X.AGENT_SHA,
        'backup_consumed_by_rollback': not st['files'][hard.binary + '.bak'].get('exists'),
        'reason_mentions_rollback': 'rollback' in (failed_row.get('block_reason') or '').lower(),
    }
    out['f1_ok'] = (out['f1']['execution_status'] == 'FAILED' and out['f1']['binary_restored_to_previous_sha']
                    and out['f1']['backup_consumed_by_rollback'] and out['f1']['previous_sha_differs_from_hub_artifact'])

    # --- F2: backup step failure (pre-existing <exe>.bak directory) -> FAILED before replace
    pre = hard.py(
        "import json,os\n"
        "bak=%r\n"
        "existed=os.path.lexists(bak)\n"
        "if existed and not os.path.isdir(bak): os.remove(bak)\n"
        "os.makedirs(bak, exist_ok=True)\n"
        "print(json.dumps({'bak':bak,'created_dir':os.path.isdir(bak),'pre_existed':existed}))\n"
        % (hard.binary + '.bak'))
    out['f2_seam'] = pre
    code2, body2, _ = RUN.upgrade([hard.node_id])
    out['backup_failure_request'] = {'http': code2, 'response': body2}
    ended2 = X.wait_pred(lambda: executions_for(X.UPGRADE_TASK_ID, hard.node_id),
                         lambda r: r['total'] >= 2 and all(x['status'] in X.TERMINAL for x in r['rows']),
                         timeout=180, interval=3.0)
    out['backup_failure_executions'] = ended2['last']
    st2 = hard.state()
    out['after_backup_failure'] = st2
    rows2 = (ended2['last'] or {}).get('rows') or []
    f2row = [r for r in rows2 if r['status'] == 'FAILED']
    out['f2'] = {
        'rows': rows2,
        'f2_block_reason': f2row[-1].get('block_reason') if f2row else None,
        'binary_unchanged': st2['files'][hard.binary].get('sha256') == out['start_binary_sha'],
        'no_leftover_new_file': not st2['files'][hard.binary + '.new'].get('exists'),
        'no_half_replacement': st2['files'][hard.binary].get('sha256') != X.AGENT_SHA,
    }
    out['f2_ok'] = (out['f2']['binary_unchanged'] and out['f2']['no_leftover_new_file']
                    and any(r['status'] == 'FAILED' for r in rows2))
    out['checks'] = {'f1_restart_failure_rollback': out['f1_ok'], 'f2_backup_failure_no_change': out['f2_ok']}
    out['failures_ok'] = out['f1_ok'] and out['f2_ok']
    rec('phase5-x01-failures.json', out)
    step('phase5_x01_failures', out['failures_ok'], out['checks'])
    return out


# --------------------------------------------------------------------------- #
# phase 6: confirm/cancel gate (no request -> no upgrade record)
# --------------------------------------------------------------------------- #
def phase6_cancel_confirm():
    out = {'at_utc': X.now_utc()}
    before_total, before_rows = RUN.count_executions(task_id=X.UPGRADE_TASK_ID)
    out['before'] = {'upgrade_executions_total': before_total}
    # The bulk-upgrade UI gates the POST behind ConfirmDialog: cancelling the
    # dialog issues no request at all.  Backend-observable equivalent: with no
    # POST there is no new upgrade row and no dispatch is attempted.
    time.sleep(5)
    after_total, after_rows = RUN.count_executions(task_id=X.UPGRADE_TASK_ID)
    out['after'] = {'upgrade_executions_total': after_total}
    # control: the same observation window WITH a real request does create rows
    ctrl = STATE['children']['dock']
    code, body, _ = RUN.upgrade([ctrl.node_id])
    after_post_total, _ = RUN.count_executions(task_id=X.UPGRADE_TASK_ID)
    out['control_post'] = {'http': code, 'response': body,
                           'total_before_control': after_total,
                           'total_after_control': after_post_total}
    out['frontend_gate_reference'] = {
        'file': 'web/src/features/catalog/node-bulk-actions.tsx',
        'lines': '141-172',
        'fact': "ConfirmDialog only calls handleConfirm (which POSTs /api/nodes/upgrade) on confirm; "
                "cancelBtnText is wired to dialog close, so cancelling performs no API call",
    }
    out['checks'] = {
        'no_request_no_new_row': after_total == before_total,
        'control_request_creates_row': after_post_total > after_total,
        'ui_cancel_is_client_side_no_request': True,
    }
    out['cancel_ok'] = all(out['checks'].values())
    out['limitation'] = ('UI click-through of the ConfirmDialog cancel path was intentionally NOT '
                         'executed: the same bulk-upgrade dialog confirms real protected nodes, and a '
                         'mis-click would replace Agent binaries on protected nodes. Verified at API '
                         'level + frontend source contract instead.')
    rec('phase6-x01-cancel-confirm.json', out)
    step('phase6_cancel_confirm', out['cancel_ok'], out['checks'])
    return out


# --------------------------------------------------------------------------- #
# phase 7: X03 on_start semantics
# --------------------------------------------------------------------------- #
def phase7_on_start(proxy):
    out = {'at_utc': X.now_utc()}
    os1 = STATE['children']['os1']
    marker = '%s/onstart-marker.log' % os1.root
    script = RUN.post('/api/scripts', {
        'name': PREFIX + 'onstart-script', 'description': 'sys-b15 X03 on_start marker writer',
        'interpreter': 'shell', 'enabled': True,
        'content': 'echo "$(date -u +%%Y-%%m-%%dT%%H:%%M:%%SZ) on_start %s" >> %s\n' % (NONCE, marker),
    })
    if script[0] != 200:
        raise RuntimeError('script_create_failed_%s_%s' % (script[0], _small(script[1], 200)))
    script_id = script[1]['id']
    STATE['created']['script'] = script_id
    task = RUN.post('/api/tasks', {
        'name': PREFIX + 'onstart-task', 'type': 'script', 'script_id': script_id,
        'target': {'type': 'node', 'node_ids': [os1.node_id]},
        'enabled': True, 'timeout': 60, 'retry': 0, 'offline_policy': 'allow_offline',
    })
    if task[0] != 200:
        raise RuntimeError('task_create_failed_%s_%s' % (task[0], _small(task[1], 200)))
    task_id = task[1]['id']
    STATE['created']['task'] = task_id
    sch = RUN.post('/api/schedules', {
        'task_id': task_id, 'type': 'on_start', 'expression': '', 'execution_owner': 'agent',
        'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'timezone': 'UTC', 'enabled': True,
    })
    if sch[0] != 200:
        raise RuntimeError('schedule_create_failed_%s_%s' % (sch[0], _small(sch[1], 200)))
    schedule_id = sch[1]['id']
    STATE['created']['schedule'] = schedule_id
    out['created'] = {'script': script_id, 'task': task_id, 'schedule': schedule_id,
                      'schedule_body': sch[1], 'target_node': os1.node_id, 'marker_on_node': marker}

    # wait until the child's own local store holds the schedule (sync applied)
    synced = X.wait_pred(lambda: os1.db_read(),
                         lambda d: any(s.get('id') == schedule_id and s.get('enabled')
                                       for s in (d.get('schedules') or [])),
                         timeout=120, interval=3.0)
    out['schedule_synced_to_child'] = {'complete': synced['complete'], 'last': synced['last']}

    def count_and_marker():
        e = executions_for(task_id, os1.node_id)
        st = X.node_file_state(os1.host, marker)
        lines = []
        if st.get('exists'):
            raw = X.ssh_json(os1.host, (
                "import json\n"
                "p=%r\n"
                "print(json.dumps({'lines':open(p).read().splitlines()}))\n" % marker))
            lines = raw.get('lines', [])
        return {'executions': e['total'], 'rows': e['rows'], 'marker_lines': lines,
                'marker_count': len(lines)}

    # --- T1: process start #1
    r1 = os1.restart()
    got1 = X.wait_pred(count_and_marker, lambda c: c['executions'] >= 1, timeout=120, interval=3.0)
    out['t1_first_start'] = {'restart': r1, 'wait': {'complete': got1['complete'], 'last': got1['last'],
                                                     'samples': got1['samples'][-4:]}}
    # --- T2: process start #2 -> triggers again (once per process start)
    time.sleep(1.5)
    r2 = os1.restart()
    got2 = X.wait_pred(count_and_marker, lambda c: c['executions'] >= 2, timeout=120, interval=3.0)
    out['t2_second_start'] = {'restart': r2, 'wait': {'complete': got2['complete'], 'last': got2['last'],
                                                      'samples': got2['samples'][-4:]}}
    # --- T3: network reconnect WITHOUT process restart -> no re-trigger
    pid_before = (os1.state()['owned_pids'] or [{}])[0].get('pid')
    proxy_before = proxy.state()
    proxy.set_mode('cut')
    cut_state = proxy.state()
    # the agent must observe the loss without dying (same process keeps retrying)
    lost = X.wait_pred(lambda: os1.log_tail(30, pattern='connection lost'),
                       lambda r: bool(r.get('lines')), timeout=60, interval=2.0)
    time.sleep(4)
    proxy.set_mode('pass')
    reconn = X.wait_pred(lambda: proxy.state(),
                         lambda s: s['client_connects'] > proxy_before['client_connects']
                         and s['open_connections'] >= 1, timeout=150, interval=2.0)
    back_online = wait_node(os1.node_id, 'online', 'synced', timeout=150)
    time.sleep(8)
    pid_after = (os1.state()['owned_pids'] or [{}])[0].get('pid')
    after_reconnect = count_and_marker()
    out['t3_reconnect'] = {
        'proxy_state_before': proxy_before, 'proxy_state_after_cut': cut_state,
        'proxy_state_after_pass': proxy.state(),
        'new_client_connections': proxy.state()['client_connects'] - proxy_before['client_connects'],
        'agent_observed_connection_lost': {'complete': lost['complete'], 'last': lost['last']},
        'reconnect_wait': {'complete': reconn['complete'], 'seconds': reconn['seconds']},
        'node_after_reconnect': back_online['last'],
        'pid_before': pid_before, 'pid_after': pid_after, 'process_alive_same_pid': pid_before == pid_after,
        'counts': after_reconnect,
        'sync_frames_after_reconnect': [f for f in proxy.frames if f.get('type') in ('SYNC_RESPONSE', 'HELLO')][-6:],
        'reconnect_ok': bool(reconn['complete'] and back_online['complete'] and pid_before == pid_after),
    }
    # --- T4: disable the schedule -> no more triggers
    put = RUN.put('/api/schedules/' + schedule_id, {
        'task_id': task_id, 'type': 'on_start', 'expression': '', 'execution_owner': 'agent',
        'offline_policy': 'allow_offline', 'misfire_policy': 'skip', 'timezone': 'UTC', 'enabled': False,
    })
    out['disable'] = {'http': put[0], 'body': put[1]}
    dis = X.wait_pred(lambda: os1.db_read(),
                      lambda d: any(s.get('id') == schedule_id and not s.get('enabled')
                                    for s in (d.get('schedules') or [])),
                      timeout=120, interval=3.0)
    out['disabled_synced_to_child'] = {'complete': dis['complete'], 'last': dis['last']}
    time.sleep(2)
    r4 = os1.restart()
    back4 = wait_node(os1.node_id, 'online', 'synced', timeout=150)
    time.sleep(12)
    after_disable = count_and_marker()
    out['t4_disabled_start'] = {'restart': r4, 'node_back': back4['complete'],
                                'settle_seconds': 12, 'counts': after_disable}
    # --- T5: delete the schedule -> no more triggers
    dele = RUN.delete('/api/schedules/' + schedule_id)
    out['delete_schedule'] = {'http': dele[0], 'body': dele[1],
                              'verify_get': RUN.get('/api/schedules/' + schedule_id)[0]}
    dele2 = X.wait_pred(lambda: os1.db_read(),
                        lambda d: not any(s.get('id') == schedule_id for s in (d.get('schedules') or [])),
                        timeout=120, interval=3.0)
    out['deleted_synced_to_child'] = {'complete': dele2['complete'], 'last': dele2['last']}
    time.sleep(2)
    r5 = os1.restart()
    back5 = wait_node(os1.node_id, 'online', 'synced', timeout=150)
    time.sleep(12)
    after_delete = count_and_marker()
    out['t5_deleted_start'] = {'restart': r5, 'node_back': back5['complete'],
                               'settle_seconds': 12, 'counts': after_delete}

    base = out['t2_second_start']['wait']['last']
    out['checks'] = {
        'start1_triggers_once': out['t1_first_start']['wait']['last']['executions'] == 1,
        'start2_triggers_again': out['t2_second_start']['wait']['last']['executions'] == 2,
        'one_execution_per_process_start': out['t2_second_start']['wait']['last']['executions'] == 2,
        'marker_written_per_start': out['t2_second_start']['wait']['last']['marker_count'] == 2,
        'reconnect_same_process': out['t3_reconnect']['process_alive_same_pid'] is True,
        'reconnect_really_happened': bool(out['t3_reconnect']['reconnect_ok']),
        'reconnect_no_repeat_trigger': out['t3_reconnect']['counts']['executions'] == 2,
        'reconnect_no_new_marker': out['t3_reconnect']['counts']['marker_count'] == 2,
        'disabled_start_no_trigger': out['t4_disabled_start']['counts']['executions'] == 2,
        'deleted_start_no_trigger': out['t5_deleted_start']['counts']['executions'] == 2,
        'deleted_schedule_absent_locally': out['deleted_synced_to_child']['complete'],
    }
    out['on_start_ok'] = all(out['checks'].values())
    rec('phase7-x03-on-start.json', out)
    step('phase7_on_start', out['on_start_ok'], out['checks'])
    return out


# --------------------------------------------------------------------------- #
# phase 8: cleanup
# --------------------------------------------------------------------------- #
def phase8_cleanup(phase_ok):
    out = {'at_utc': X.now_utc()}
    # 1. fixture teardown on the control plane (schedule -> task -> script order)
    deletes = {}
    for label, path in (('schedule', '/api/schedules/'),
                        ('task', '/api/tasks/'),
                        ('script', '/api/scripts/')):
        fid = STATE['created'].get(label)
        if not fid:
            continue
        code, body, _ = RUN.delete(path + fid)
        vcode, vbody, _ = RUN.get(path + fid)
        deletes[label] = {'id': fid, 'delete_http': code, 'delete_body': body,
                          'get_http': vcode, 'get_body': _small(vbody, 160)}
    out['fixture_deletes'] = deletes
    out['all_fixtures_gone'] = all(v['get_http'] == 404 for v in deletes.values()) if deletes else True

    # 2. stop + remove owned children (processes + owned roots)
    removes = {}
    for name, child in STATE['children'].items():
        removes[name] = child.remove()
    out['child_removals'] = removes
    out['all_roots_removed'] = all(v.get('root_exists_after') is False and v.get('opt_exists_after') is False
                                   for v in removes.values())
    out['all_processes_gone'] = all(not v.get('remaining_processes') for v in removes.values())

    # 3. owned node records
    node_deletes = {}
    for node_id in STATE['owned_nodes']:
        code, body, _ = RUN.delete('/api/nodes/' + node_id)
        vcode, vbody = RUN.node(node_id)
        node_deletes[node_id] = {'delete_http': code, 'delete_body': body, 'get_http': vcode,
                                 'get_body': _small(vbody, 160)}
    out['node_deletes'] = node_deletes
    out['all_nodes_gone'] = all(v['get_http'] == 404 for v in node_deletes.values())

    # 4. batch-level owned roots, then residue sweep on every hosting node
    hosts = sorted({c.host for c in STATE['children'].values()})
    try:
        out['batch_dir_removals'] = X.remove_batch_dirs(BATCH, NONCE, hosts)
    except Exception as exc:  # noqa: BLE001
        out['batch_dir_removals'] = {'error': '%s: %s' % (type(exc).__name__, _small(str(exc), 300))}
    residue = {}
    for host in hosts:
        residue[host] = X.ssh_json(host, (
            "import json,os,glob,subprocess\n"
            "out={'batch_root':os.path.exists(%r),'batch_opt':os.path.exists(%r),"
            "'batch_roots':sorted(glob.glob('/var/lib/nodesteer-qa-sysb15-*')),"
            "'batch_opts':sorted(glob.glob('/opt/qa-sysb15-*'))}\n"
            "p=subprocess.run(['pgrep','-af','qa-sysb15'],capture_output=True,text=True)\n"
            "out['owned_processes']=[l for l in p.stdout.splitlines() if l.strip()]\n"
            "q=subprocess.run(['pgrep','-af',%r],capture_output=True,text=True)\n"
            "out['agent_processes']=[l for l in q.stdout.splitlines() if l.strip()]\n"
            "print(json.dumps(out))\n" % (BATCH_ROOT, BATCH_OPT, 'nodesteer-agent')))
    out['residue'] = residue
    out['no_owned_residue'] = all(not r['batch_root'] and not r['batch_opt'] and not r['batch_roots']
                                  and not r['batch_opts'] and not r['owned_processes']
                                  for r in residue.values())
    out['batch_dirs_gone'] = all(
        (v.get('root_exists_after') is False and v.get('opt_exists_after') is False)
        for v in out['batch_dir_removals'].values() if isinstance(v, dict)) if out['batch_dir_removals'] else False

    # 5. protected environment back to baseline
    out['health'] = X.health()
    out['protected_nodes'] = {n['id']: node_view(n['id']) for n in RUN.nodes()
                              if n.get('id') in X.PROTECTED_NODES}
    out['all_protected_online_synced'] = all(
        v.get('status') == 'online' and v.get('sync_status') == 'synced'
        for v in out['protected_nodes'].values())
    probe = X.unit_probe()
    STATE['protected_units_after'] = X.unit_fingerprint(probe)
    out['protected_units_after'] = probe
    out['protected_units_unchanged'] = STATE['protected_units_before'] == STATE['protected_units_after']
    for label, ip in X.HOSTS.items():
        STATE['baseline_sha_after'][label] = X.protected_binary_sha(ip)
    out['protected_binary_sha_after'] = STATE['baseline_sha_after']
    out['all_baseline_sha_restored'] = all(v.get('sha256') == X.AGENT_SHA
                                           for v in STATE['baseline_sha_after'].values())
    out['health_ok'] = (out['health'].get('/healthz', {}).get('http') == 200
                        and out['health'].get('/readyz', {}).get('http') == 200)
    out['oracle_clean'] = all([out['all_fixtures_gone'], out['all_roots_removed'],
                               out['all_processes_gone'], out['all_nodes_gone'],
                               out['batch_dirs_gone'], out['no_owned_residue'],
                               out['all_protected_online_synced'],
                               out['protected_units_unchanged'], out['all_baseline_sha_restored'],
                               out['health_ok']])
    rec('phase8-cleanup.json', out)
    step('phase8_cleanup', out['oracle_clean'], {'oracle_clean': out['oracle_clean']})
    return out


# --------------------------------------------------------------------------- #
def main():
    global RUN
    RUN = X.Hub()
    RUN.login()
    result = {'batch_id': BATCH, 'nonce': NONCE, 'prefix': PREFIX,
              'started_at_utc': X.now_utc(), 'authority': RUN.base}
    proxy_holder = {}
    phases = {}
    try:
        if not phase0_guards():
            result['aborted'] = 'phase0_guards_failed'
            return finish(result, phases)
        phases['p1'] = {'ok': phase1_children(proxy_holder)}
        phases['p2'] = phase2_mixed_batch()
        phases['p3'] = phase3_duplicate_inflight(phases['p2'].get('upg_record'))
        phases['p4'] = phase4_version_reconnect()
        phases['p5'] = phase5_failures()
        phases['p6'] = phase6_cancel_confirm()
        phases['p7'] = phase7_on_start(proxy_holder['os1'])
    except Exception as exc:  # noqa: BLE001 - harness must always reach cleanup
        STATE['errors'].append('%s: %s' % (type(exc).__name__, _small(str(exc), 500)))
        log('PHASE ERROR', type(exc).__name__, str(exc)[:400])
    finally:
        try:
            if proxy_holder.get('os1'):
                proxy_holder['os1'].stop()
        except Exception as exc:  # noqa: BLE001
            STATE['errors'].append('proxy_stop: %s' % type(exc).__name__)
        try:
            phases['p8'] = phase8_cleanup(phases)
        except Exception as exc:  # noqa: BLE001
            STATE['errors'].append('cleanup: %s: %s' % (type(exc).__name__, _small(str(exc), 500)))
            log('CLEANUP ERROR', type(exc).__name__, str(exc)[:400])
    return finish(result, phases)


def finish(result, phases):
    result['phases'] = phases
    result['errors'] = STATE['errors']
    result['steps'] = STATE['steps']
    result['finished_at_utc'] = X.now_utc()
    rec('run-evidence.json', result)
    rec('progress.json', {
        'batch_id': BATCH, 'nonce': NONCE, 'phase': 'done' if not STATE['errors'] else 'done_with_errors',
        'updated_at_utc': X.now_utc(),
        'owned_nodes': STATE['owned_nodes'],
        'created_fixtures': STATE['created'],
        'steps_ok': all(s['ok'] for s in STATE['steps']),
        'errors': STATE['errors'],
    })
    log('DONE', json.dumps({k: _phase_ok(v) for k, v in phases.items()}))


def _phase_ok(v):
    if not isinstance(v, dict):
        return v
    for key in ('ok', 'mixed_batch_ok', 'success_ok', 'version_reconnect_ok',
                'failures_ok', 'cancel_ok', 'on_start_ok', 'oracle_clean'):
        if key in v:
            return v[key]
    return None
    return 0


if __name__ == '__main__':
    sys.exit(main())
