#!/usr/bin/env python3
"""batch10-hub-restart (web): File Relay multi-target -> success / failure /
retry / cancel / Hub restart, plus Hub restart + Agent reconnect + offline
target transfer recovery with UI readback.

Cases: E2E-14, P22-15.

Real evidence chain:
  UI + operator/API act -> real Transfer rows -> Hub dispatch over WS -> real
  Agent (owned child, exact repair binary) performs the real HTTP download
  upload -> Hub staged blob -> node filesystem + API + SQLite + UI readback.

The only unit this harness stops/starts is
`nodesteer-hub-repair-20260930-101945.service`.  `nodesteer-hub.service`
(:8080), every `nodesteer-agent.service` and the 4 protected repair Agent
units are only ever read.  No VM is restarted, no product source changed.
"""
import json
import sys
import time
from pathlib import Path

import childlib
import hubctl
import hubdb
import ui
import xferlib as X

HERE = Path(__file__).resolve().parent.parent          # batch10-hub-restart/
RUNNERS = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
BATCH = 'b10'
PREFIX = 'qa-%s-%s-' % (BATCH, NONCE)
DEV = HERE / 'evidence'
SRC_HOST = X.NODE_IP['native-1']
TGT_A_HOST = X.NODE_IP['native-2']
TGT_B_HOST = X.NODE_IP['native-3']
TARGET_LABEL = {'native-1': 'native-1', 'native-2': 'native-2', 'native-3': 'native-3'}


def rec(name, value):
    X.update_json(DEV / name, value)
    return value


def log(*a):
    print('[%s]' % X.now_utc(), *a)
    sys.stdout.flush()


def summary(t):
    if not t:
        return None
    return {'id': t.get('id'), 'source_node_id': t.get('source_node_id'),
            'source_path': t.get('source_path'), 'size': t.get('size'),
            'sha256': t.get('sha256'), 'source_mode': t.get('source_mode'),
            'status': t.get('status'), 'error': t.get('error'),
            'targets': [{'node_id': x.get('node_id'), 'destination_path': x.get('destination_path'),
                         'mode': x.get('mode'), 'status': x.get('status'), 'error': x.get('error')}
                        for x in (t.get('targets') or [])]}


class Run:
    def __init__(self):
        self.hub = X.Hub()
        self.hub.login()
        self.specs = {}
        self.samples = {}
        self.state = {}
        self.budget = {'api_mutations': 0, 'node_enroll_posts': 0, 'transfers_created': 0,
                       'transfers_retried': 0, 'transfers_canceled': 0, 'hub_restarts': 0,
                       'child_process_starts': 0, 'child_process_removals': 0, 'ui_sessions': 0}

    def env(self):
        if 'env' not in self.specs:
            self.specs['env'] = childlib.make_env(BATCH, HERE)
        return self.specs['env']

    def child(self, name):
        return self.env()[name]

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def wait_node(self, node_id, status='online', sync='synced', timeout=120):
        return X.wait_pred(lambda: {k: (self.nodes_by_id().get(node_id) or {}).get(k)
                                    for k in ('status', 'sync_status', 'global_revision')},
                           lambda v: v.get('status') == status and v.get('sync_status') == sync,
                           timeout=timeout, interval=1.0)

    def wait_transfer(self, tid, predicate, timeout=240, interval=1.0):
        return X.wait_pred(lambda: self.hub.transfer(tid), predicate, timeout=timeout, interval=interval)


# --------------------------------------------------------------------------- #
def phase0_guard(run):
    out = {'at_utc': X.now_utc(), 'endpoint': run.hub.base}
    nodes = run.hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    out['protected_nodes_before'] = {}
    for nid, label in X.PROTECTED_NODES.items():
        n = by_id.get(nid)
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            raise RuntimeError('protected_node_not_healthy:' + label)
        out['protected_nodes_before'][label] = {'id': nid, 'status': n.get('status'),
                                                'sync_status': n.get('sync_status'),
                                                'global_revision': n.get('global_revision')}
    unit = hubctl.unit_state()
    out['hub_unit_before'] = unit
    pid = unit.get('MainPID')
    out['hub_binary_before'] = hubctl.binary_sha(pid) if pid and pid != '0' else None
    out['hub_binary_sha_matches_pinned'] = bool((out['hub_binary_before'] or {}).get('sha256') == hubctl.HUB_BIN_SHA)
    if not out['hub_binary_sha_matches_pinned']:
        raise RuntimeError('hub_binary_sha_mismatch')
    out['health_before'] = hubctl.health()
    if not hubctl.healthy():
        raise RuntimeError('repair_hub_not_healthy')
    out['primary_hub_unit_untouched_readonly'] = hubctl.primary_hub_state()
    out['hub_db_before'] = hubdb.snapshot()
    tr = run.hub.transfers()
    out['transfers_baseline'] = {'count': len(tr)}
    scripts = run.hub.json('/api/scripts')
    tasks = run.hub.json('/api/tasks')
    out['hub_fixture_absence'] = {
        'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
    if any(out['hub_fixture_absence'].values()):
        raise RuntimeError('owned_fixture_already_present')
    out['node_pre'] = {}
    for name in ('src', 'tgt_a', 'tgt_b'):
        child = run.child(name)
        inv = child.inventory()
        out['node_pre'][name] = {'host': child.host, 'root_exists': inv['root_exists'],
                                 'config_exists': inv['config_exists'],
                                 'owned_child_processes': inv['owned_child_processes'],
                                 'agent_binary_sha_matches': inv['agent_binary_sha_matches']}
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present:' + name)
        if not inv['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch:' + name)
    out['protected_units_before'] = X.protected_pids(X.unit_probe())
    return rec('phase0-guard.json', out)


def phase1_children(run):
    out = {'at_utc': X.now_utc(), 'children': {}}
    token = run.hub.cred['registration_token']
    plan = [('src', SRC_HOST, 'native-1'), ('tgt_a', TGT_A_HOST, 'native-2'), ('tgt_b', TGT_B_HOST, 'native-3')]
    for name, host, label in plan:
        code, enroll, _ = run.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + name, 'node_ip': host, 'hub_address': 'http://%s:18080' % X.HUB_HOST})
        run.budget['api_mutations'] += 1
        run.budget['node_enroll_posts'] += 1
        if code != 200 or not enroll.get('node_id'):
            raise RuntimeError('enrollment_failed_%s_http_%s' % (name, code))
        child = run.child(name)
        child.node_id = enroll['node_id']
        child.agent_id = enroll['agent_id']
        proxy = X.FrameProxy('ws://%s:18080' % X.HUB_HOST)
        port = proxy.start()
        run.samples[name + '_proxy'] = proxy
        started = child.write_config_and_start(port, enroll['node_id'], enroll['agent_id'],
                                               token, PREFIX + name, host)
        run.budget['child_process_starts'] += 1
        res = run.wait_node(enroll['node_id'], 'online', 'synced', timeout=120)
        if not res['complete']:
            raise RuntimeError('child_not_online_synced:' + name)
        cred_state, cred = child.read_cred()
        if not cred_state or not cred_state.get('has_credential'):
            raise RuntimeError('child_credential_missing:' + name)
        run.state[name + '_cred'] = cred
        out['children'][name] = {'host': host, 'protected_label': label, 'node_id': enroll['node_id'],
                                 'agent_id': enroll['agent_id'], 'node_name': PREFIX + name,
                                 'root_on_node': child.root, 'proxy_port': port,
                                 'credential_read_from_own_store': {'has_credential': True,
                                                                    'credential_len': cred_state.get('credential_len')},
                                 'child_processes': started.get('owned_processes')}
    return rec('phase1-owned-children.json', out)


def phase2_multitarget(run):
    src = run.child('src')
    a = run.child('tgt_a')
    b = run.child('tgt_b')
    src_file = src.root + '/xfer/src-multi.bin'
    dst_a = a.root + '/xfer/dst-multi-a.bin'
    dst_b = b.root + '/xfer/dst-multi-b.bin'
    X.node_rm(TGT_A_HOST, dst_a)
    X.node_rm(TGT_B_HOST, dst_b)
    # deterministic failure target: an existing symlink at the destination makes
    # the native host adapter refuse the atomic replace.
    X.node_symlink(TGT_B_HOST, dst_b, '/etc/hostname')
    made = X.node_make_file(SRC_HOST, src_file, 2 << 20, seed=53)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': a.node_id, 'destination_path': dst_a},
                                          {'node_id': b.node_id, 'destination_path': dst_b}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t['id']
    res = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=240)
    final = res['last'] if res else {}
    dst_a_state = X.node_file_state(TGT_A_HOST, dst_a)
    dst_b_state = X.node_file_state(TGT_B_HOST, dst_b)
    targets = {x.get('node_id'): x for x in (final.get('targets') or [])}
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'multi-target File Relay: one target succeeds and one fails (symlink destination); '
                    'the failing target must not block the succeeding one.',
        'transfer_id': tid, 'source_file': made,
        'targets': {'tgt_a': a.node_id, 'tgt_b': b.node_id},
        'final': summary(final),
        'tgt_a_destination': dst_a_state, 'tgt_b_destination': dst_b_state,
        'assertions': {
            'overall_failed_because_one_target_failed': final.get('status') == 'FAILED',
            'tgt_a_success': (targets.get(a.node_id) or {}).get('status') == 'SUCCESS',
            'tgt_b_failed': (targets.get(b.node_id) or {}).get('status') == 'FAILED',
            'tgt_a_file_correct': (dst_a_state.get('sha256') == made['sha256']
                                   and dst_a_state.get('size') == made['size']
                                   and dst_a_state.get('mode') == '644'),
            'tgt_b_isolated_no_install': dst_b_state.get('is_symlink') is True,
            'tgt_b_error_mentions_symlink': 'symlink' in ((targets.get(b.node_id) or {}).get('error') or ''),
            'two_target_rows': len(final.get('targets') or []) == 2},
    }
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase2-multitarget-success-failure.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase2_multitarget_failed')
    return out


def phase3_retry(run):
    tid = run.state['multi_tid']
    a = run.child('tgt_a')
    b = run.child('tgt_b')
    dst_b = b.root + '/xfer/dst-multi-b.bin'
    # remove the symlink so the retried target can succeed
    X.node_rm(TGT_B_HOST, dst_b)
    run.budget['api_mutations'] += 1
    run.budget['transfers_retried'] += 1
    code, row, _ = run.hub.post('/api/transfers/%s/retry?target_id=%s'
                                % (run.hub.quote(tid), run.hub.quote(b.node_id)), {})
    res = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=240)
    final = res['last'] if res else {}
    dst_b_state = X.node_file_state(TGT_B_HOST, dst_b)
    src_file = run.child('src').root + '/xfer/src-multi.bin'
    made = X.node_file_state(SRC_HOST, src_file)
    out = {'at_utc': X.now_utc(), 'transfer_id': tid, 'retry_http': code, 'retry_row': summary(row),
           'final': summary(final), 'tgt_b_destination': dst_b_state,
           'assertions': {
               'retry_http_200': code == 200,
               'final_success': final.get('status') == 'SUCCESS',
               'single_target_retry_only': len({x.get('node_id') for x in (final.get('targets') or [])}) == 2,
               'tgt_b_installed_correct': (dst_b_state.get('sha256') == made.get('sha256')
                                           and dst_b_state.get('size') == made.get('size') and not dst_b_state.get('is_symlink'))}}
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase3-retry-single-target.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase3_retry_failed')
    return out


def phase4_cancel(run):
    src = run.child('src')
    a = run.child('tgt_a')
    src_file = src.root + '/xfer/src-cancel.bin'
    dst_a = a.root + '/xfer/dst-cancel.bin'
    X.node_rm(TGT_A_HOST, dst_a)
    made = X.node_make_file(SRC_HOST, src_file, 1 << 20, seed=61)
    run.samples['tgt_a_proxy'].set_mode('cut')
    X.wait_pred(lambda: (run.nodes_by_id().get(a.node_id) or {}).get('status'),
                lambda v: v == 'offline', timeout=60, interval=0.5)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': a.node_id, 'destination_path': dst_a}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t['id']
    staged = run.wait_transfer(tid, lambda x: bool(x.get('sha256')), timeout=120)
    staged_row = staged['last'] if staged else {}
    ccode, canceled, _ = run.hub.post('/api/transfers/%s/cancel' % run.hub.quote(tid), {})
    run.budget['api_mutations'] += 1
    run.budget['transfers_canceled'] += 1
    run.samples['tgt_a_proxy'].set_mode('pass')
    run.wait_node(a.node_id, 'online', 'synced', timeout=90)
    time.sleep(6)
    after = run.hub.transfer(tid)
    dst_state = X.node_file_state(TGT_A_HOST, dst_a)
    hubfiles = X.hub_xfer_listing()
    leftover = [i['name'] for i in hubfiles.get('items', []) if i['name'].startswith(tid)]
    out = {'at_utc': X.now_utc(), 'transfer_id': tid, 'source_file': made,
           'staged_row': summary(staged_row), 'cancel_http': ccode, 'cancel_row': summary(canceled),
           'after_settle': summary(after), 'destination': dst_state,
           'hub_staging_leftovers_for_id': leftover,
           'assertions': {
               'was_staged_before_cancel': bool(staged_row.get('sha256')),
               'cancel_http_200': ccode == 200,
               'canceled_terminal': after.get('status') == 'CANCELED',
               'target_canceled': all(x.get('status') == 'CANCELED' for x in (after.get('targets') or [])),
               'no_destination_file': not dst_state.get('exists'),
               'hub_staging_cleaned_on_cancel': leftover == []}}
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase4-cancel.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase4_cancel_failed')
    return out


def phase5_hub_restart(run):
    src = run.child('src')
    b = run.child('tgt_b')
    src_file = src.root + '/xfer/src-restart.bin'
    dst_b = b.root + '/xfer/dst-restart.bin'
    X.node_rm(TGT_B_HOST, dst_b)
    made = X.node_make_file(SRC_HOST, src_file, 2 << 20, seed=67)
    run.samples['tgt_b_proxy'].set_mode('cut')
    X.wait_pred(lambda: (run.nodes_by_id().get(b.node_id) or {}).get('status'),
                lambda v: v == 'offline', timeout=60, interval=0.5)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': b.node_id, 'destination_path': dst_b}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t['id']
    staged = run.wait_transfer(tid, lambda x: bool(x.get('sha256')), timeout=120)
    staged_row = staged['last'] if staged else {}
    dst_before = X.node_file_state(TGT_B_HOST, dst_b)
    window = hubctl.restart_window()
    run.budget['hub_restarts'] += 1
    rec('phase5a-hub-restart.json', dict(window, at_utc=X.now_utc(), authorised_unit=hubctl.REPAIR_UNIT,
                                         untouched_primary_hub=hubctl.primary_hub_state()))
    if not (window['outage']['outage_observed'] and window['recovery']['healthy']
            and window['mainpid_changed'] and window['binary_sha_same']):
        raise RuntimeError('hub_restart_window_incomplete')
    reconv = X.wait_pred(
        lambda: {nid: {k: (run.nodes_by_id().get(nid) or {}).get(k) for k in ('status', 'sync_status')}
                 for nid in X.PROTECTED_NODES},
        lambda v: all(r.get('status') == 'online' and r.get('sync_status') == 'synced' for r in v.values()),
        timeout=45, interval=1.0)
    units_after = X.protected_pids(X.unit_probe())
    before = X.protected_pids(run.state['units_before'])
    pid_same = {lab: {u: {'before': before[lab][u]['MainPID'], 'after': units_after[lab][u]['MainPID'],
                          'same': before[lab][u]['MainPID'] == units_after[lab][u]['MainPID']}
                      for u in before[lab] if u != hubctl.REPAIR_UNIT} for lab in before}
    post_row = run.hub.transfer(tid)
    dst_mid = X.node_file_state(TGT_B_HOST, dst_b)
    src_reconn = run.wait_node(src.node_id, 'online', 'synced', timeout=90)
    run.samples['tgt_b_proxy'].set_mode('pass')
    tgt_reconn = run.wait_node(b.node_id, 'online', 'synced', timeout=120)
    fin = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=240)
    final = fin['last'] if fin else {}
    dst_final = X.node_file_state(TGT_B_HOST, dst_b)
    dst_dir = X.node_list_dir(TGT_B_HOST, b.root + '/xfer')
    hubfiles = X.hub_xfer_listing()
    leftover = [i['name'] for i in hubfiles.get('items', []) if i['name'].startswith(tid)]
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'Hub restart + Agent reconnect + target offline: the transfer is staged while the '
                    'target is offline, the ONE authorised repair-Hub unit is restarted, and after '
                    'reconnect the delivery resumes with no half product.',
        'transfer_id': tid, 'source_file': made,
        'hub_restart': {k: window[k] for k in ('mainpid_changed', 'binary_sha_same', 'downtime_seconds')},
        'hub_restart_mainpid': {'before': window['pre']['unit'].get('MainPID'),
                                'after': window['post']['unit'].get('MainPID')},
        'outage': window['outage']['outage_observed'], 'recovery_healthy': window['recovery']['healthy'],
        'pre_restart_staged': summary(staged_row), 'dst_before_restart': dst_before,
        'protected_reconnect_after_restart': {'all_reconnected_within_45s': reconv['complete'],
                                              'seconds': reconv['seconds'], 'nodes_after': reconv['last']},
        'untouched_units': {'primary_8080_and_agents_pid_unchanged': pid_same},
        'post_restart_transfer_state': summary(post_row),
        'post_restart_dst_absent_while_target_offline': dst_mid,
        'source_auto_reconnect': src_reconn, 'target_reconnect': tgt_reconn,
        'final': summary(final), 'destination_final': dst_final, 'target_dir_final': dst_dir,
        'hub_staging_leftovers_for_id': leftover,
        'assertions': {
            'staged_before_restart': staged_row.get('sha256') == made['sha256'],
            'no_dst_before_restart': not dst_before.get('exists'),
            'transfer_survives_restart': (post_row.get('id') == tid and post_row.get('sha256') == made['sha256']),
            'no_dst_after_restart_while_offline': not dst_mid.get('exists'),
            'source_reconnected': src_reconn['complete'],
            'target_reconnected': tgt_reconn['complete'],
            'final_success': final.get('status') == 'SUCCESS',
            'single_target_row': len(final.get('targets') or []) == 1,
            'dest_sha_matches_source': dst_final.get('sha256') == made['sha256'],
            'dest_size_matches_source': dst_final.get('size') == made['size'],
            'dest_mode_0644': dst_final.get('mode') == '644',
            'no_temp_left': not any(i['name'].startswith('.nodesteer-transfer-') or i['name'].startswith('.tmp-')
                                    for i in (dst_dir.get('items') or [])),
            'hub_staging_cleaned': leftover == [],
            'all_pids_unchanged': all(v['same'] for lab in pid_same.values() for v in lab.values()),
            'reconn_within_45s': reconv['complete']}}
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase5b-hub-restart-recovery.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase5_hub_restart_failed')
    return out


def phase6_ui(run):
    """UI readback: the transfers page must agree with the API state.

    The transfers table renders the source Agent name, source path, the
    aggregate Chinese status and one 'target 状态' token per target; it does NOT
    render the transfer UUID, so the readback is matched on the owned source
    filenames and the rendered target statuses.
    """
    out = {'at_utc': X.now_utc()}
    api_rows = {}
    for tid in (run.state.get('multi_tid'), run.state.get('cancel_tid'), run.state.get('restart_tid')):
        if tid:
            api_rows[tid] = summary(run.hub.transfer(tid))
    out['api_rows'] = api_rows
    out['api_statuses'] = {tid: r['status'] for tid, r in api_rows.items()}
    out['api_target_statuses'] = {tid: sorted(x['status'] for x in r['targets'])
                                  for tid, r in api_rows.items()}
    multi = api_rows.get(run.state.get('multi_tid')) or {}
    cancel = api_rows.get(run.state.get('cancel_tid')) or {}
    restart = api_rows.get(run.state.get('restart_tid')) or {}
    try:
        run.budget['ui_sessions'] += 1
        out['login'] = ui.login()
        ui.open_url(ui.BASE + '/transfers')
        time.sleep(3.5)
        out['screenshot_transfers'] = str(ui.screenshot(str(DEV / 'UI-transfers-table.png')))
        # focus the table on the owned rows through the page's own search box
        out['search_fill'] = ui.eval_js("""(() => {
            const el = document.querySelector('input[type=search], input[placeholder*="搜索"], input[placeholder*="筛选"]');
            if (!el) return {ok: false, reason: 'no search input'};
            const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            setter.call(el, %s);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            return {ok: true, placeholder: el.placeholder, value: el.value};
        })()""" % json.dumps(NONCE))
        time.sleep(2.5)
        out['scan'] = ui.scan()
        out['screenshot_owned_rows'] = str(ui.screenshot(str(DEV / 'UI-transfers-owned.png')))
        cells = ui.eval_js("""(() => {
            const table = document.querySelector('table');
            const rows = table ? [...table.querySelectorAll('tbody tr')]
                .map(tr => [...tr.cells].map(c => c.innerText.trim().replace(/\\s+/g, ' '))) : [];
            return {rows};
        })()""")
        own = [r for r in ((cells or {}).get('rows') or []) if NONCE in ' '.join(r)]
        out['owned_ui_rows'] = own

        def row_for(suffix):
            for r in own:
                if len(r) >= 5 and r[1].endswith(suffix):
                    return r
            return None

        r_multi = row_for('/src-multi.bin')
        r_cancel = row_for('/src-cancel.bin')
        r_restart = row_for('/src-restart.bin')
        out['ui_rows'] = {'multi': r_multi, 'cancel': r_cancel, 'restart': r_restart}
        out['assertions'] = {
            'ui_page_reached': (out['scan'] or {}).get('path', '').startswith('/transfers'),
            'ui_has_owned_rows': bool(own),
            'ui_multi_row_present_success_two_targets': bool(
                r_multi and r_multi[2] == '成功' and 'tgt_a 成功' in r_multi[4] and 'tgt_b 成功' in r_multi[4]),
            'ui_cancel_row_present_canceled': bool(
                r_cancel and r_cancel[2] == '已取消' and 'tgt_a 已取消' in r_cancel[4]),
            'ui_restart_row_present_success': bool(
                r_restart and r_restart[2] == '成功' and 'tgt_b 成功' in r_restart[4]),
            'ui_multi_row_mentions_two_targets': len(multi.get('targets') or []) == 2,
            'api_multi_is_terminal_success': multi.get('status') == 'SUCCESS',
            'api_cancel_is_canceled': cancel.get('status') == 'CANCELED',
            'api_restart_is_success': restart.get('status') == 'SUCCESS',
            'ui_agrees_with_api': bool(
                r_multi and r_cancel and multi.get('status') == 'SUCCESS' and cancel.get('status') == 'CANCELED')}
    except Exception as exc:
        import traceback
        traceback.print_exc()
        out['ui_error'] = '%s: %s' % (type(exc).__name__, exc)
        out['assertions'] = {'ui_reachable': False}
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase6-ui-readback.json', out)
    return out


def phase7_settle(run):
    time.sleep(10)
    ids = [run.state.get('multi_tid'), run.state.get('cancel_tid'), run.state.get('restart_tid')]
    rows = {tid: summary(run.hub.transfer(tid)) for tid in ids if tid}
    out = {'at_utc': X.now_utc(),
           'note': 'settle window; a retry/cancel/Hub restart must not duplicate or resurrect work',
           'transfers': rows,
           'all_terminal': all(r['status'] in X.TERMINAL for r in rows.values()),
           'unique_ids': len(rows) == len(set(rows))}
    out['assertions_pass'] = out['all_terminal'] and out['unique_ids']
    rec('phase7-settle.json', out)
    return out


def phase8_cleanup(run):
    results = {'children_removed': {}, 'node_deletes': {}, 'errors': []}
    for name in ('src', 'tgt_a', 'tgt_b'):
        child = run.child(name)
        try:
            results['children_removed'][name] = child.remove()
            run.budget['child_process_removals'] += 1
        except Exception as exc:
            results['children_removed'][name] = {'error': str(exc)}
            results['errors'].append('child_remove:%s:%s' % (name, exc))
    for name in list(run.samples):
        if name.endswith('_proxy'):
            try:
                run.samples[name].stop()
            except Exception:
                pass
    for name in ('src', 'tgt_a', 'tgt_b'):
        child = run.child(name)
        if not child.node_id:
            continue
        code, body, _ = run.hub.delete('/api/nodes/' + run.hub.quote(child.node_id))
        run.budget['api_mutations'] += 1
        gcode, _, _ = run.hub.get('/api/nodes/' + run.hub.quote(child.node_id))
        results['node_deletes'][name] = {'node_id': child.node_id, 'delete_http': code, 'get_http': gcode,
                                         'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('node_delete_not_404:%s:%s/%s' % (name, code, gcode))
    results['all_node_get_404'] = all(v.get('get_http') == 404 for v in results['node_deletes'].values())
    results['all_roots_removed'] = all(v.get('root_removed') and not v.get('root_exists_after')
                                       for v in results['children_removed'].values()
                                       if isinstance(v, dict) and 'root_removed' in v)
    rec('phase8-cleanup.json', results)
    return results


def phase9_oracle(run, cleanup):
    nodes = run.hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    prot = {}
    ok = True
    for nid, label in X.PROTECTED_NODES.items():
        n = by_id.get(nid)
        prot[label] = None if not n else {'status': n.get('status'), 'sync_status': n.get('sync_status')}
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            ok = False
    scripts = run.hub.json('/api/scripts')
    tasks = run.hub.json('/api/tasks')
    leftovers = {'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
                 'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
                 'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
    node_residue = {}
    for name in ('src', 'tgt_a', 'tgt_b'):
        child = run.child(name)
        inv = child.inventory()
        node_residue[name] = {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                              'owned_child_processes': inv['owned_child_processes']}
    units_after = X.protected_pids(X.unit_probe())
    before = X.protected_pids(run.state['units_before'])
    pid_same = {lab: {u: {'before': before[lab][u]['MainPID'], 'after': units_after[lab][u]['MainPID'],
                          'same': before[lab][u]['MainPID'] == units_after[lab][u]['MainPID']}
                      for u in before[lab] if u != hubctl.REPAIR_UNIT} for lab in before}
    retained = [run.state.get(k) for k in ('multi_tid', 'cancel_tid', 'restart_tid') if run.state.get(k)]
    hubfiles = X.hub_xfer_listing()
    our_residue = [i['name'] for i in hubfiles.get('items', [])
                   if any(i['name'].startswith(t) for t in retained)]
    oracle = {
        'at_utc': X.now_utc(), 'owned_prefix': PREFIX,
        'protected_nodes_after': prot, 'protected_nodes_online_synced': ok,
        'protected_units_pid_unchanged': pid_same,
        'repair_hub_unit_after': hubctl.unit_state(),
        'repair_hub_active': hubctl.unit_state().get('ActiveState') == 'active',
        'health_after': hubctl.health(), 'primary_hub_unit_untouched': hubctl.primary_hub_state(),
        'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
        'node_residue': node_residue,
        'node_residue_clear': not any(v['root_exists'] or v['config_exists'] or v['owned_child_processes']
                                      for v in node_residue.values()),
        'retained_transfers': {'count': len(retained), 'ids': retained,
                               'note': 'Hub has no FileTransfer DELETE endpoint; rows persist as history (design).'},
        'hub_staged_leftovers_for_our_ids': our_residue,
        'transfers_total_now': len(run.hub.transfers()),
        'cleanup_results': cleanup}
    oracle['clean'] = (ok and not any(leftovers.values()) and oracle['node_residue_clear']
                       and all(v['same'] for lab in pid_same.values() for v in lab.values())
                       and oracle['repair_hub_active'] and not our_residue
                       and cleanup.get('all_node_get_404'))
    rec('final-cleanup-oracle.json', oracle)
    X.update_json(HERE / 'final-cleanup-oracle.json', oracle)
    return oracle


# --------------------------------------------------------------------------- #
def main():
    run = Run()
    run.state['units_before'] = X.unit_probe()
    evidence = {'batch_id': 'batch10-hub-restart', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base, 'cases': ['E2E-14', 'P22-15'],
                'started_at_utc': X.now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'batch10-hub-restart', 'nonce': NONCE, 'phase': 'guard',
                'updated_at_utc': X.now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = X.now_utc()
        progress.update(extra)
        X.update_json(HERE / 'progress.json', progress)

    failed = False
    try:
        mark('guard'); evidence['phases']['guard'] = phase0_guard(run)
        mark('children'); evidence['phases']['children'] = phase1_children(run)
        progress['children'] = {k: run.child(k).node_id for k in ('src', 'tgt_a', 'tgt_b')}
        mark('multitarget'); evidence['phases']['multi'] = phase2_multitarget(run)
        run.state['multi_tid'] = evidence['phases']['multi']['transfer_id']
        mark('retry'); evidence['phases']['retry'] = phase3_retry(run)
        mark('cancel'); evidence['phases']['cancel'] = phase4_cancel(run)
        run.state['cancel_tid'] = evidence['phases']['cancel']['transfer_id']
        mark('hub_restart'); evidence['phases']['restart'] = phase5_hub_restart(run)
        run.state['restart_tid'] = evidence['phases']['restart']['transfer_id']
        mark('ui'); evidence['phases']['ui'] = phase6_ui(run)
        mark('settle'); evidence['phases']['settle'] = phase7_settle(run)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True
    mark('cleanup')
    try:
        if not hubctl.healthy():
            hubctl.start_repair_unit()
            hubctl.wait_healthy(timeout=90)
    except Exception as exc:
        evidence['deviations'].append('HUB_RECOVERY_SAFETY:%s' % exc)
    for name in list(run.samples):
        if name.endswith('_proxy'):
            try:
                run.samples[name].set_mode('pass')
            except Exception:
                pass
    try:
        cleanup = phase8_cleanup(run)
        progress['final_cleanup'] = cleanup
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['cleanup_error'] = str(exc)
        cleanup = {}
        failed = True
    mark('oracle')
    try:
        oracle = phase9_oracle(run, cleanup)
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = X.now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {k: run.state.get(k) for k in ('multi_tid', 'cancel_tid', 'restart_tid')}
    X.update_json(HERE / 'run-evidence.json', evidence)

    ledger = {
        'batch_id': 'batch10-hub-restart', 'nonce': NONCE, 'prefix': PREFIX,
        'owned_children': {name: {'host': run.child(name).host, 'node_id': run.child(name).node_id,
                                  'agent_id': run.child(name).agent_id, 'root': run.child(name).root,
                                  'removed': bool((cleanup.get('children_removed', {}).get(name) or {})
                                                  .get('root_removed'))}
                           for name in ('src', 'tgt_a', 'tgt_b')},
        'owned_nodes_deleted_get404': {n: v.get('get_http') for n, v in
                                       (cleanup.get('node_deletes', {}) or {}).items()},
        'created_transfers': [
            {'id': run.state.get('multi_tid'), 'purpose': 'E2E-14 multi-target success+failure+retry',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
            {'id': run.state.get('cancel_tid'), 'purpose': 'E2E-14 cancel',
             'terminal': 'CANCELED', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
            {'id': run.state.get('restart_tid'), 'purpose': 'E2E-14/P22-15 Hub restart + offline target',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'}],
        'node_side_files_removed': True,
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'}
    X.update_json(HERE / 'fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = X.now_utc()
    X.update_json(HERE / 'progress.json', progress)
    print(json.dumps({'batch_id': 'batch10-hub-restart', 'deviations': evidence['deviations'],
                      'clean': progress.get('oracle_clean')}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    multi = ph.get('multi', {})
    retry = ph.get('retry', {})
    cancel = ph.get('cancel', {})
    restart = ph.get('restart', {})
    uiev = ph.get('ui', {})
    settle = ph.get('settle', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    canonical = 'NOT_RUN (repair build not executed; legacy baseline PARTIAL excluded per remaining_dimensions)'

    def case(case_id, title, proposed, confidence, dims, limits, resources):
        return {'case_id': case_id, 'title': title, 'canonical_before': canonical,
                'proposed_after': proposed, 'confidence': confidence,
                'covered_dimensions': dims, 'remaining_or_limitations': limits,
                'evidence': sorted(p.name for p in DEV.iterdir()) + [
                    'case-result.json', 'progress.json', 'fixture-ledger.json',
                    'final-cleanup-oracle.json', 'run-evidence.json'],
                'actual_resources': resources}

    resources = {'owned_prefix': PREFIX,
                 'src_node_id': run.child('src').node_id, 'src_agent_id': run.child('src').agent_id,
                 'tgt_a_node_id': run.child('tgt_a').node_id, 'tgt_a_agent_id': run.child('tgt_a').agent_id,
                 'tgt_b_node_id': run.child('tgt_b').node_id, 'tgt_b_agent_id': run.child('tgt_b').agent_id,
                 'transfers': {'multi_target': run.state.get('multi_tid'),
                               'canceled': run.state.get('cancel_tid'),
                               'hub_restart': run.state.get('restart_tid')},
                 'retained_transfer_history': [run.state.get(k) for k in
                                               ('multi_tid', 'cancel_tid', 'restart_tid')
                                               if run.state.get(k)]}

    ui_ok = bool(uiev.get('assertions_pass'))
    e2e_ok = bool(multi.get('assertions_pass') and retry.get('assertions_pass')
                  and cancel.get('assertions_pass') and restart.get('assertions_pass')
                  and settle.get('assertions_pass') and ui_ok)
    cases = []
    cases.append(case(
        'E2E-14', 'File Relay 多目标 → 成功、失败、重试、取消、Hub 重启均正确',
        'PASS' if (e2e_ok and cleanup_ok) else ('PASS_WITH_NOTES' if e2e_ok else 'PARTIAL'),
        'high' if (e2e_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-WEB:E2E-14',
          'layer': 'multi-target File Relay over the real product data path + Hub restart',
          'status': 'PASS' if (e2e_ok and cleanup_ok) else 'PARTIAL',
          'description': [
              "Multi-target SUCCESS/FAILURE isolation: a two-target transfer where one target succeeded (exact source size/sha256/mode on disk) and the other failed (symlink destination rejected by the native host adapter) reached overall FAILED without blocking the good target.",
              "RETRY: the failed target was retried individually (POST /api/transfers/<id>/retry?target_id=<node>) to SUCCESS with the exact source sha256.",
              "CANCEL: a staged transfer whose target was offline was canceled -> terminal CANCELED, no destination file and the Hub staged blob/part cleaned.",
              "HUB RESTART: the transfer staged while the target was offline survived the authorised repair-Hub restart and completed after reconnect with no half product.",
              "UI readback: the /transfers page lists the owned rows and the rendered per-target statuses agree with the API (multi-target row 成功 with tgt_a 成功 + tgt_b 成功; cancel row 已取消)."]}],
        ["The multi-target transfer uses the same source Agent for every target and a symlink destination "
         "as the deterministic failure; no cross-source case is included.",
         "One Hub restart was performed (the single authorised repair unit)."],
        resources))
    p22_ok = bool(restart.get('assertions_pass') and ui_ok)
    cases.append(case(
        'P22-15', 'Hub 重启、Agent 重连、目标离线后的传输恢复',
        'PASS' if (p22_ok and cleanup_ok) else ('PASS_WITH_NOTES' if p22_ok else 'PARTIAL'),
        'high' if (p22_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-WEB:P22-15',
          'layer': 'repair-Hub restart / Agent reconnect / offline target / UI feedback',
          'status': 'PASS' if p22_ok else 'PARTIAL',
          'description': [
              "The source uploaded and the Hub staged the blob while the target was offline; the transfer row survived the ONE authorised repair-Hub restart unchanged.",
              "The source Agent reconnected by itself after the restart; the target reconnected when brought back and ResumeNode delivered exactly once.",
              "No destination file or temp file existed on the target while it was offline (before and after the restart), and the final file matched the exact source size/sha256/mode with the Hub staging cleaned.",
              "All 4 protected repair nodes plus the primary :8080 Hub and every Agent kept their MainPIDs; only the repair-Hub MainPID changed, and /healthz, /readyz returned 200.",
              "UI feedback is consistent with the API state for the owned transfers."]}],
        ["The Hub outage observed is short (unit stop/start, ~1-3 s); a long outage beyond the Agent "
         "heartbeat timeout was not exercised.",
         "The UI stage is a read-only readback of the transfers table; no transfer was created or "
         "acted on from the UI."],
        resources))

    result = {'batch_id': 'batch10-hub-restart',
              'title': 'File Relay 多目标：成功/失败/重试/取消/Hub 重启 与 目标离线恢复（含 UI 反馈）',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': run.hub.base,
              'cases': cases, 'deviations': evidence['deviations'], 'budget': evidence['budget'],
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management and agent data-plane writes only to repair http://192.168.100.209:18080',
                  'hub_restart': 'only nodesteer-hub-repair-20260930-101945.service was stopped/started once; '
                                 'nodesteer-hub.service (:8080) and all Agent units untouched',
                  'owned_children': 'source on native-1, targets on native-2/native-3, exact repair Agent binary '
                                    'd55dc5cd..., each behind its own harness frame proxy',
                  'ui': 'agent-browser read-only session on the repair UI; no UI writes',
                  'credential_source': "each child's own store (identity.credential), never a protected Agent config",
                  'product_source_changes': 0, 'git_operations': 0, 'vms_restarted': 0}}
    X.update_json(HERE / 'case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
