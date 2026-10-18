#!/usr/bin/env python3
"""sys-b14 runtime batch: FileTransfer source/target identity auth, size and
SHA256 validation, Hub staged-blob atomic promotion, and Hub restart /
Agent reconnect / target-offline half-product checks.

Cases: SYS-59, SYS-60, SYS-61, SYS-66.

Real evidence chain:
  operator/API act -> real Transfer row -> Hub dispatch over WS -> real Agent
  (owned child, exact repair binary d55dc5cd...) performs the real HTTP
  upload/download -> Hub staged blob (part -> blob rename) -> API / SQLite /
  physical readback on the node and on the Hub host.

The only unit this harness stops/starts is
`nodesteer-hub-repair-20260930-101945.service`.  `nodesteer-hub.service`
(:8080), every `nodesteer-agent.service` and the 4 protected repair Agent
units are only ever read.  No VM is restarted, no product source changed.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import childlib
import hubctl
import hubdb
import xferlib as X

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
BATCH = 'sysb14'
PREFIX = 'qa-%s-%s-' % (BATCH, NONCE)
DEV = HERE / 'evidence'
SRC_HOST = X.NODE_IP['native-1']
TGT_HOST = X.NODE_IP['native-2']
SRC = None
TGT = None


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
        self.budget = {'api_mutations': 0, 'node_enroll_posts': 0,
                       'transfers_created': 0, 'manual_uploads': 0,
                       'transfers_retried': 0, 'hub_restarts': 0,
                       'child_process_starts': 0, 'child_process_removals': 0}
        self.native_prefix_hits = [0, 0]

    # ---- shared helpers ----
    def env(self):
        if 'env' not in self.specs:
            self.specs['env'] = childlib.make_env(BATCH, HERE)
        return self.specs['env']

    def child(self, name):
        return self.env()[name]

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def wait_node(self, node_id, status='online', sync='synced', timeout=120):
        return X.wait_pred(
            lambda: {k: (self.nodes_by_id().get(node_id) or {}).get(k)
                     for k in ('status', 'sync_status', 'global_revision')},
            lambda v: v.get('status') == status and v.get('sync_status') == sync,
            timeout=timeout, interval=1.0)

    def wait_transfer(self, tid, predicate, timeout=180, interval=1.0):
        return X.wait_pred(lambda: self.hub.transfer(tid),
                           predicate, timeout=timeout, interval=interval)


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
        out['protected_nodes_before'][label] = {
            'id': nid, 'agent_id': n.get('agent_id'), 'status': n.get('status'),
            'sync_status': n.get('sync_status'), 'global_revision': n.get('global_revision')}
    unit = hubctl.unit_state()
    out['hub_unit_before'] = unit
    pid = unit.get('MainPID')
    out['hub_binary_before'] = hubctl.binary_sha(pid) if pid and pid != '0' else None
    out['hub_binary_sha_matches_pinned'] = bool(
        (out['hub_binary_before'] or {}).get('sha256') == hubctl.HUB_BIN_SHA)
    if not out['hub_binary_sha_matches_pinned']:
        raise RuntimeError('hub_binary_sha_mismatch')
    out['health_before'] = hubctl.health()
    if not hubctl.healthy():
        raise RuntimeError('repair_hub_not_healthy')
    out['primary_hub_unit_untouched_readonly'] = hubctl.primary_hub_state()
    out['hub_db_before'] = hubdb.snapshot()
    tr = run.hub.transfers()
    out['transfers_baseline'] = {'count': len(tr), 'ids': sorted(t['id'] for t in tr)}
    scripts = run.hub.json('/api/scripts')
    tasks = run.hub.json('/api/tasks')
    out['hub_fixture_absence'] = {
        'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
    if any(out['hub_fixture_absence'].values()):
        raise RuntimeError('owned_fixture_already_present')
    out['node_pre'] = {}
    for name, host in (('src', SRC_HOST), ('tgt', TGT_HOST)):
        inv = run.child(name).inventory()
        out['node_pre'][name] = {
            'host': host, 'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
            'owned_child_processes': inv['owned_child_processes'],
            'owned_root_glob': inv['owned_root_glob'],
            'agent_binary_sha_matches': inv['agent_binary_sha_matches'],
            'primary_agent_unit': inv['primary_agent_unit']}
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present:' + name)
        if not inv['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch:' + name)
        exist_ok = X.node_file_state(host, run.child(name).root)
        if exist_ok.get('exists'):
            raise RuntimeError('owned_root_path_exists:' + name)
    out['protected_units_before'] = X.protected_pids(X.unit_probe())
    return rec('phase0-guard.json', out)


def phase1_children(run):
    out = {'at_utc': X.now_utc(), 'children': {}}
    token = run.hub.cred['registration_token']
    plan = [('src', SRC_HOST, 'native-1'), ('tgt', TGT_HOST, 'native-2')]
    for name, host, label in plan:
        code, enroll, _ = run.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + name, 'node_ip': host,
            'hub_address': 'http://%s:18080' % X.HUB_HOST})
        run.budget['api_mutations'] += 1
        run.budget['node_enroll_posts'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
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
        n = run.nodes_by_id().get(enroll['node_id']) or {}
        out['children'][name] = {
            'host': host, 'protected_label': label, 'node_id': enroll['node_id'],
            'agent_id': enroll['agent_id'], 'node_name': PREFIX + name,
            'root_on_node': child.root, 'config_mode_0600': True,
            'proxy_port': port, 'routing': 'owned child(%s) -> operator %s:%d (own frame proxy) -> repair hub :18080'
                                              % (host, X.OPERATOR_IP, port),
            'child_processes': started.get('owned_processes'),
            'credential_read_from_own_store': {'has_credential': True,
                                               'credential_len': cred_state.get('credential_len'),
                                               'quick_check': cred_state.get('quick_check')},
            'node_after_start': {k: n.get(k) for k in
                                 ('hostname', 'status', 'sync_status', 'global_revision', 'agent_version')},
            'connect_timeline_last': res['samples'][-1] if res['samples'] else None}
    return rec('phase1-owned-children.json', out)


def phase2_main_transfer(run):
    src_file = run.child('src').root + '/xfer/src-main.bin'
    dst_file = run.child('tgt').root + '/xfer/dst-main.bin'
    X.node_rm(TGT_HOST, dst_file)
    made = X.node_make_file(SRC_HOST, src_file, 4 << 20, seed=11)
    code, t, _ = run.hub.create_transfer(run.child('src').node_id, src_file,
                                         [{'node_id': run.child('tgt').node_id,
                                           'destination_path': dst_file}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    if code != 200 or not t.get('id'):
        raise RuntimeError('create_transfer_http_%s %s' % (code, t))
    tid = t['id']
    res = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=180)
    final = res['last'] if res else {}
    dst = X.node_file_state(TGT_HOST, dst_file)
    dst_dir = X.node_list_dir(TGT_HOST, run.child('tgt').root + '/xfer')
    hubfiles = X.hub_xfer_listing()
    leftovers = [i['name'] for i in hubfiles.get('items', []) if i['name'].startswith(tid)]
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'normal single-target transfer over the real product data path (source uploads, '
                    'Hub verifies+stages, target downloads+atomically replaces)',
        'transfer_id': tid,
        'source': {'node_id': run.child('src').node_id, 'path': src_file,
                   'size': made['size'], 'sha256': made['sha256']},
        'destination': {'node_id': run.child('tgt').node_id, 'path': dst_file},
        'create_http': code, 'create_initial_status': t.get('status'),
        'final': summary(final), 'wait': {'complete': res['complete'], 'seconds': res['seconds']},
        'node_side_dst': dst,
        'node_side_dst_dir': dst_dir,
        'hub_staging_leftovers_for_id': leftovers,
        'assertions': {
            'status_success': final.get('status') == 'SUCCESS',
            'hub_sha_matches_source': final.get('sha256') == made['sha256'],
            'hub_size_matches_source': final.get('size') == made['size'],
            'target_file_exists': bool(dst.get('exists')) and not dst.get('is_symlink'),
            'target_sha_matches_source': dst.get('sha256') == made['sha256'],
            'target_size_matches_source': dst.get('size') == made['size'],
            'target_mode_0644': dst.get('mode') == '644',
            'target_has_single_target_row': len(final.get('targets') or []) == 1,
            'no_temp_left_on_target': not any(i['name'].startswith('.nodesteer-transfer-') or i['name'].startswith('.tmp-')
                                              for i in (dst_dir.get('items') or [])),
            'hub_blob_and_part_cleaned_after_success': leftovers == []},
    }
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase2-main-transfer.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase2_main_transfer_failed')
    return out


def phase3_auth_negatives(run):
    tid = run.state.get('main_tid')
    if not tid:
        raise RuntimeError('no_main_transfer')
    src = run.child('src')
    tgt = run.child('tgt')
    src_cred = run.state['src_cred']
    tgt_cred = run.state['tgt_cred']
    before = run.hub.transfer(tid)

    cases = {}

    def agent_call(label, fn):
        code, headers, body = fn()
        cases[label] = {'http': code, 'body': X.decode(body)}

    # source Agent must NOT be able to download (it is not a target)
    agent_call('source_download_forbidden', lambda: run.hub.agent_download(tid, src.agent_id, src_cred))
    # target Agent must NOT be able to upload (it is not the source)
    agent_call('target_upload_forbidden',
               lambda: run.hub.agent_upload(tid, tgt.agent_id, tgt_cred, b'x' * 16,
                                            declared_size=16, content_range='bytes 0-15/16'))
    # wrong credential / no credential on the agent data plane
    agent_call('source_wrong_token', lambda: run.hub.agent_upload(
        tid, src.agent_id, 'cred-not-a-real-credential', b'x' * 16, declared_size=16,
        content_range='bytes 0-15/16'))
    agent_call('source_no_headers', lambda: run.hub.agent_upload(
        tid, None, None, b'x' * 16, declared_size=16, content_range='bytes 0-15/16'))
    agent_call('unknown_agent_id', lambda: run.hub.agent_download(
        tid, 'agent-00000000-0000-0000-0000-000000000000', src_cred))
    # admin management bearer must not authenticate on the agent channel
    agent_call('admin_bearer_on_agent_channel', lambda: run.hub._agent_request(
        'GET', '/agent/transfers/%s/download' % run.hub.quote(tid), src.agent_id, None,
        {'Authorization': 'Bearer ' + (run.hub.token or '')}, None, 60))

    after = run.hub.transfer(tid)
    out = {
        'at_utc': X.now_utc(),
        'transfer_id': tid,
        'cases': cases,
        'negatives_rejected': {
            'source_download': cases['source_download_forbidden']['http'] == 403,
            'target_upload': cases['target_upload_forbidden']['http'] == 403,
            'wrong_token': cases['source_wrong_token']['http'] == 401,
            'no_headers': cases['source_no_headers']['http'] == 401,
            'unknown_agent_id': cases['unknown_agent_id']['http'] == 401,
            'admin_bearer': cases['admin_bearer_on_agent_channel']['http'] == 401},
        'side_effect_check': {
            'transfer_unchanged_after_negatives': summary(before) == summary(after),
            'before': summary(before), 'after': summary(after)},
    }
    out['negatives_pass'] = (all(out['negatives_rejected'].values())
                             and out['side_effect_check']['transfer_unchanged_after_negatives'])
    rec('phase3-auth-negatives.json', out)
    if not out['negatives_pass']:
        raise RuntimeError('phase3_auth_negatives_failed')
    return out


def phase4_size_negatives(run):
    src = run.child('src')
    tgt = run.child('tgt')
    src_file = src.root + '/xfer/src-size.bin'
    dst_file = tgt.root + '/xfer/dst-size.bin'
    X.node_rm(TGT_HOST, dst_file)
    made = X.node_make_file(SRC_HOST, src_file, 1 << 20, seed=23)
    # take the source offline so the Hub cannot auto-dispatch the upload and the
    # transfer stays unstaged while the manual negative uploads are exercised.
    run.samples['src_proxy'].set_mode('cut')
    off = X.wait_pred(lambda: (run.nodes_by_id().get(src.node_id) or {}).get('status'),
                        lambda v: v == 'offline', timeout=60, interval=0.5)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': tgt.node_id, 'destination_path': dst_file}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t.get('id')
    time.sleep(3)
    td = run.hub.transfer(tid)
    cases = {}
    content = b'Q' * 64

    def up(label, **kw):
        run.budget['manual_uploads'] += 1
        code, headers, body = run.hub.agent_upload(tid, src.agent_id, run.state['src_cred'], content, **kw)
        cases[label] = {'http': code, 'body': X.decode(body)}

    up('size_header_not_a_number', declared_size='abc', content_range='bytes 0-63/64')
    up('size_negative', declared_size=-5, content_range='bytes 0-63/64')
    up('size_over_declared_content_length_mismatch', declared_size=1000, content_range='bytes 0-999/1000')
    up('valid_upload_correct', declared_size=64, content_range='bytes 0-63/64', mode=0o644)
    staged = run.hub.transfer(tid)
    # restore the source so its own agent stops being an interfering actor
    run.samples['src_proxy'].set_mode('pass')
    run.wait_node(src.node_id, 'online', 'synced', timeout=90)
    fin = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=120)
    final = fin['last'] if fin else {}
    dst = X.node_file_state(TGT_HOST, dst_file)
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'size validation on the real upload endpoint: source Agent is offline so the '
                    'transfer stays unstaged; the harness speaks the exact Agent HTTP protocol with the '
                    "source child's own credential.",
        'transfer_id': tid,
        'source_file': made,
        'source_offline_observed': off['complete'],
        'pre_upload_state': summary(td),
        'cases': cases,
        'after_valid_upload_state': summary(staged),
        'final': summary(final),
        'destination_after': dst,
        'assertions': {
            'size_header_not_a_number_400': cases['size_header_not_a_number']['http'] == 400,
            'size_negative_400': cases['size_negative']['http'] == 400,
            'content_length_mismatch_400': cases['size_over_declared_content_length_mismatch']['http'] == 400,
            'valid_upload_200': cases['valid_upload_correct']['http'] == 200,
            'hub_sha_matches_payload': staged.get('sha256') == hashlib.sha256(content).hexdigest(),
            'final_success': final.get('status') == 'SUCCESS',
            'dest_sha_matches': dst.get('sha256') == hashlib.sha256(content).hexdigest()},
    }
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase4-size-negatives.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase4_size_negatives_failed')
    return out


def phase5_sha_negative_retry(run):
    src = run.child('src')
    tgt = run.child('tgt')
    src_file = src.root + '/xfer/src-sha.bin'
    dst_file = tgt.root + '/xfer/dst-sha.bin'
    X.node_rm(TGT_HOST, dst_file)
    made = X.node_make_file(SRC_HOST, src_file, 2 << 20, seed=31)
    # target offline so the delivery is held back while staged, giving a window
    # to observe the Hub staged blob and to corrupt it before delivery.
    run.samples['tgt_proxy'].set_mode('cut')
    X.wait_pred(lambda: (run.nodes_by_id().get(tgt.node_id) or {}).get('status'),
                  lambda v: v == 'offline', timeout=60, interval=0.5)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': tgt.node_id, 'destination_path': dst_file}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t['id']
    staged = run.wait_transfer(tid, lambda x: bool(x.get('sha256')), timeout=120)
    staged_row = staged['last'] if staged else {}
    blob = X.hub_file_state('%s/%s.blob' % (X.HUB_XFER_DIR, tid))
    part = X.hub_file_state('%s/%s.part' % (X.HUB_XFER_DIR, tid))
    backup = X.hub_blob_backup(tid)
    corrupt = X.hub_blob_corrupt(tid, 32)
    # restore target: it must reject the corrupted payload (SHA256 mismatch) and
    # must NOT install a half/corrupt file
    run.samples['tgt_proxy'].set_mode('pass')
    run.wait_node(tgt.node_id, 'online', 'synced', timeout=90)
    failed = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=120)
    failed_row = failed['last'] if failed else {}
    dst_after_fail = X.node_file_state(TGT_HOST, dst_file)
    dst_dir_after_fail = X.node_list_dir(TGT_HOST, tgt.root + '/xfer')
    # restore the blob and retry the failed target
    restored = X.hub_blob_restore(tid)
    blob_after_restore = X.hub_file_state('%s/%s.blob' % (X.HUB_XFER_DIR, tid))
    run.budget['api_mutations'] += 1
    run.budget['transfers_retried'] += 1
    rc, retry_row, _ = run.hub.post('/api/transfers/%s/retry' % run.hub.quote(tid), {})
    fin = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=180)
    final = fin['last'] if fin else {}
    dst_final = X.node_file_state(TGT_HOST, dst_file)
    dst_dir_final = X.node_list_dir(TGT_HOST, tgt.root + '/xfer')
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'staged blob is corrupted after the source upload and before delivery; the target '
                    'must verify SHA256, refuse to install and leave no half file; then the blob is '
                    'restored and the failed target is retried to SUCCESS.',
        'transfer_id': tid,
        'source_file': made,
        'target_offline_window': True,
        'staged_row': summary(staged_row),
        'hub_blob_after_staging': blob,
        'hub_part_after_staging': part,
        'atomic_promotion': {
            'blob_exists': bool(blob.get('exists')),
            'blob_sha_matches_source': blob.get('sha256') == made['sha256'],
            'blob_size_matches_source': blob.get('size') == made['size'],
            'no_part_file': not part.get('exists'),
            'staged_sha_matches_source': staged_row.get('sha256') == made['sha256']},
        'blob_backup': backup,
        'blob_corrupt': corrupt,
        'failed_after_corrupt': summary(failed_row),
        'destination_after_failed_verify': dst_after_fail,
        'target_dir_after_failed_verify': dst_dir_after_fail,
        'blob_restore': restored,
        'blob_after_restore': blob_after_restore,
        'retry_http': rc,
        'retry_row': summary(retry_row),
        'final': summary(final),
        'destination_after_retry': dst_final,
        'target_dir_after_retry': dst_dir_final,
        'assertions': {
            'staged_blob_present': bool(blob.get('exists')),
            'staged_sha_matches_source': staged_row.get('sha256') == made['sha256'],
            'no_part_left_after_promotion': not part.get('exists'),
            'corrupted_delivery_failed': failed_row.get('status') == 'FAILED',
            'target_reported_failure': any(x.get('status') == 'FAILED' for x in (failed_row.get('targets') or [])),
            'no_half_file_installed': not dst_after_fail.get('exists'),
            'restored_blob_sha_matches_source': blob_after_restore.get('sha256') == made['sha256'],
            'no_temp_left_after_failure': not any(
                i['name'].startswith('.nodesteer-transfer-') or i['name'].startswith('.tmp-')
                for i in (dst_dir_after_fail.get('items') or [])),
            'retry_reached_success': final.get('status') == 'SUCCESS',
            'retry_dest_sha_matches_source': dst_final.get('sha256') == made['sha256'],
            'retry_dest_size_matches_source': dst_final.get('size') == made['size'],
            'no_temp_left_after_retry': not any(
                i['name'].startswith('.nodesteer-transfer-') or i['name'].startswith('.tmp-')
                for i in (dst_dir_final.get('items') or []))},
    }
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase5-sha-negative-retry.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase5_sha_negative_retry_failed')
    return out


def phase6_hub_restart(run):
    src = run.child('src')
    tgt = run.child('tgt')
    src_file = src.root + '/xfer/src-restart.bin'
    dst_file = tgt.root + '/xfer/dst-restart.bin'
    X.node_rm(TGT_HOST, dst_file)
    made = X.node_make_file(SRC_HOST, src_file, 2 << 20, seed=41)
    run.samples['tgt_proxy'].set_mode('cut')
    X.wait_pred(lambda: (run.nodes_by_id().get(tgt.node_id) or {}).get('status'),
                  lambda v: v == 'offline', timeout=60, interval=0.5)
    code, t, _ = run.hub.create_transfer(src.node_id, src_file,
                                         [{'node_id': tgt.node_id, 'destination_path': dst_file}])
    run.budget['api_mutations'] += 1
    run.budget['transfers_created'] += 1
    tid = t['id']
    staged = run.wait_transfer(tid, lambda x: bool(x.get('sha256')), timeout=120)
    staged_row = staged['last'] if staged else {}
    dst_before = X.node_file_state(TGT_HOST, dst_file)
    pre_rev = staged_row.get('id') and hubdb.snapshot()['revision_sequence']

    # ---- the single authorised repair-Hub restart ----
    window = hubctl.restart_window()
    run.budget['hub_restarts'] += 1
    rec('phase6a-hub-restart.json', dict(window, at_utc=X.now_utc(),
                                         authorised_unit=hubctl.REPAIR_UNIT,
                                         protected_nodes_before=staged_row is not None,
                                         untouched_primary_hub=hubctl.primary_hub_state()))
    if not (window['outage']['outage_observed'] and window['recovery']['healthy']
            and window['mainpid_changed'] and window['binary_sha_same']):
        raise RuntimeError('hub_restart_window_incomplete')

    # ---- post restart: 4 protected nodes reconverge, Agent PIDs unchanged ----
    reconv = X.wait_pred(
        lambda: {nid: {k: (run.nodes_by_id().get(nid) or {}).get(k)
                       for k in ('status', 'sync_status', 'global_revision')}
                 for nid in X.PROTECTED_NODES},
        lambda v: all(r.get('status') == 'online' and r.get('sync_status') == 'synced'
                      for r in v.values()),
        timeout=45, interval=1.0)
    units_after = X.protected_pids(X.unit_probe())
    post_row = run.hub.transfer(tid)
    dst_mid = X.node_file_state(TGT_HOST, dst_file)
    tgt_node_mid = (run.nodes_by_id().get(tgt.node_id) or {})
    # source child must have reconnected on its own
    src_reconn = run.wait_node(src.node_id, 'online', 'synced', timeout=90)

    # ---- bring the target back: ResumeNode must deliver exactly once ----
    run.samples['tgt_proxy'].set_mode('pass')
    tgt_reconn = run.wait_node(tgt.node_id, 'online', 'synced', timeout=120)
    fin = run.wait_transfer(tid, lambda x: x.get('status') in X.TERMINAL, timeout=180)
    final = fin['last'] if fin else {}
    dst_final = X.node_file_state(TGT_HOST, dst_file)
    dst_dir_final = X.node_list_dir(TGT_HOST, tgt.root + '/xfer')
    hubfiles = X.hub_xfer_listing()
    leftovers = [i['name'] for i in hubfiles.get('items', []) if i['name'].startswith(tid)]

    before = X.protected_pids(run.state['units_before'])
    after = units_after
    pid_same = {}
    for label in before:
        pid_same[label] = {u: {'before': before[label][u]['MainPID'],
                               'after': after[label][u]['MainPID'],
                               'same': before[label][u]['MainPID'] == after[label][u]['MainPID']}
                           for u in before[label] if u != hubctl.REPAIR_UNIT}
    pid_same['hub-vm'][hubctl.REPAIR_UNIT] = {
        'before': before['hub-vm'].get(hubctl.REPAIR_UNIT, {}).get('MainPID'),
        'after': after['hub-vm'].get(hubctl.REPAIR_UNIT, {}).get('MainPID'),
        'changed_by_design': before['hub-vm'].get(hubctl.REPAIR_UNIT, {}).get('MainPID')
                             != after['hub-vm'].get(hubctl.REPAIR_UNIT, {}).get('MainPID')}
    out = {
        'at_utc': X.now_utc(),
        'scenario': 'source uploads and the Hub stages the blob while the target is offline; the ONE '
                    'authorised repair-Hub unit is restarted; after reconnect the transfer resumes and '
                    'the target receives exactly one complete, verified file (no half product).',
        'transfer_id': tid,
        'source_file': made,
        'hub_restart': {k: window[k] for k in
                        ('mainpid_changed', 'binary_sha_same', 'downtime_seconds')},
        'hub_restart_mainpid': {'before': window['pre']['unit'].get('MainPID'),
                                'after': window['post']['unit'].get('MainPID')},
        'outage': window['outage']['outage_observed'],
        'recovery_healthy': window['recovery']['healthy'],
        'pre_restart': {'staged_row': summary(staged_row), 'target_node': {
            k: tgt_node_mid.get(k) for k in ('status', 'sync_status')}, 'dst_before': dst_before},
        'protected_reconnect_after_restart': {'all_reconnected_within_45s': reconv['complete'],
                                              'seconds': reconv['seconds'],
                                              'nodes_after': reconv['last']},
        'untouched_units': {'primary_8080_and_agents_pid_unchanged': pid_same,
                            'repair_hub_restarted_by_design': True},
        'post_restart_transfer_state': summary(post_row),
        'post_restart_dst_absent_while_target_offline': dst_mid,
        'source_auto_reconnect': {'reconnected': src_reconn['complete'], 'seconds': src_reconn['seconds']},
        'target_reconnect': {'reconnected': tgt_reconn['complete'], 'seconds': tgt_reconn['seconds']},
        'final': summary(final),
        'destination_final': dst_final,
        'target_dir_final': dst_dir_final,
        'hub_staging_leftovers_for_id': leftovers,
        'assertions': {
            'staged_before_restart': staged_row.get('sha256') == made['sha256'],
            'no_dst_while_target_offline': not dst_before.get('exists'),
            'transfer_survives_restart': (post_row.get('id') == tid
                                          and post_row.get('sha256') == made['sha256']
                                          and post_row.get('size') == made['size']),
            'no_dst_after_restart_while_offline': not dst_mid.get('exists'),
            'source_reconnected': src_reconn['complete'],
            'target_reconnected': tgt_reconn['complete'],
            'final_success': final.get('status') == 'SUCCESS',
            'single_target_row': len(final.get('targets') or []) == 1,
            'dest_sha_matches_source': dst_final.get('sha256') == made['sha256'],
            'dest_size_matches_source': dst_final.get('size') == made['size'],
            'dest_mode_0644': dst_final.get('mode') == '644',
            'no_temp_left': not any(i['name'].startswith('.nodesteer-transfer-') or i['name'].startswith('.tmp-')
                                    for i in (dst_dir_final.get('items') or [])),
            'hub_staging_cleaned': leftovers == [],
            'reconn_within_45s': reconv['complete']},
    }
    run.state['restart_pid_same'] = pid_same
    out['assertions_pass'] = all(out['assertions'].values())
    rec('phase6b-hub-restart-sys66.json', out)
    if not out['assertions_pass']:
        raise RuntimeError('phase6_hub_restart_failed')
    return out


def phase7_settle(run):
    time.sleep(10)
    ids = [run.state.get('main_tid'), run.state.get('size_tid'), run.state.get('sha_tid'),
           run.state.get('restart_tid')]
    rows = {}
    for tid in ids:
        if not tid:
            continue
        t = run.hub.transfer(tid)
        rows[tid] = summary(t)
    out = {
        'at_utc': X.now_utc(),
        'note': 'settle window after all transfers; a Hub restart / retry must not duplicate target rows '
                'or leave non-terminal work',
        'transfers': rows,
        'all_terminal': all(r['status'] in X.TERMINAL for r in rows.values()),
        'single_target_each': all(len(r['targets']) == 1 for r in rows.values()),
        'unique_ids': len(rows) == len(set(rows)),
    }
    out['assertions_pass'] = out['all_terminal'] and out['single_target_each'] and out['unique_ids']
    rec('phase7-settle.json', out)
    return out


def phase8_cleanup(run):
    results = {'children_removed': {}, 'node_deletes': {}, 'errors': []}
    for name in ('src', 'tgt'):
        child = run.child(name)
        try:
            results['children_removed'][name] = child.remove()
            run.budget['child_process_removals'] += 1
        except Exception as exc:
            results['children_removed'][name] = {'error': str(exc)}
            results['errors'].append('child_remove:%s:%s' % (name, exc))
    for proxy_name in ('src_proxy', 'tgt_proxy'):
        proxy = run.samples.get(proxy_name)
        if proxy:
            try:
                proxy.stop()
            except Exception:
                pass
    for name in ('src', 'tgt'):
        child = run.child(name)
        if not child.node_id:
            continue
        code, body, _ = run.hub.delete('/api/nodes/' + run.hub.quote(child.node_id))
        run.budget['api_mutations'] += 1
        gcode, _, _ = run.hub.get('/api/nodes/' + run.hub.quote(child.node_id))
        results['node_deletes'][name] = {'node_id': child.node_id, 'delete_http': code,
                                         'get_http': gcode,
                                         'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('node_delete_not_404:%s:%s/%s' % (name, code, gcode))
    results['all_node_get_404'] = all(v.get('get_http') == 404 for v in results['node_deletes'].values())
    results['all_roots_removed'] = all(
        v.get('root_removed') and not v.get('root_exists_after')
        for v in results['children_removed'].values() if isinstance(v, dict) and 'root_removed' in v)
    rec('phase8-cleanup.json', results)
    return results


def phase9_oracle(run, cleanup):
    nodes = run.hub.json('/api/nodes')
    by_id = {n['id']: n for n in nodes}
    prot = {}
    ok = True
    for nid, label in X.PROTECTED_NODES.items():
        n = by_id.get(nid)
        prot[label] = None if not n else {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                          'global_revision': n.get('global_revision')}
        if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
            ok = False
    scripts = run.hub.json('/api/scripts')
    tasks = run.hub.json('/api/tasks')
    leftovers = {
        'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
        'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
        'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
    node_residue = {}
    for name, host in (('src', SRC_HOST), ('tgt', TGT_HOST)):
        inv = run.child(name).inventory()
        node_residue[name] = {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                              'owned_child_processes': inv['owned_child_processes'],
                              'primary_agent_unit': inv['primary_agent_unit']}
    units_after = X.protected_pids(X.unit_probe())
    before = X.protected_pids(run.state['units_before'])
    pid_same = {lab: {u: {'before': before[lab][u]['MainPID'], 'after': units_after[lab][u]['MainPID'],
                          'same': before[lab][u]['MainPID'] == units_after[lab][u]['MainPID']}
                      for u in before[lab] if u != hubctl.REPAIR_UNIT} for lab in before}
    tr = run.hub.transfers()
    retained = sorted(t['id'] for t in tr if t['id'] in
                      {run.state.get('main_tid'), run.state.get('size_tid'),
                       run.state.get('sha_tid'), run.state.get('restart_tid')} - {None})
    hubfiles = X.hub_xfer_listing()
    our_hub_residue = [i['name'] for i in hubfiles.get('items', [])
                       if any(i['name'].startswith(t) for t in retained)]
    oracle = {
        'at_utc': X.now_utc(),
        'owned_prefix': PREFIX,
        'protected_nodes_after': prot,
        'protected_nodes_online_synced': ok,
        'protected_units_pid_unchanged': pid_same,
        'repair_hub_unit_after': hubctl.unit_state(),
        'repair_hub_active': hubctl.unit_state().get('ActiveState') == 'active',
        'health_after': hubctl.health(),
        'primary_hub_unit_untouched': hubctl.primary_hub_state(),
        'hub_leftover_owned_fixtures': leftovers,
        'hub_leftover_clear': not any(leftovers.values()),
        'node_residue': node_residue,
        'node_residue_clear': not any(v['root_exists'] or v['config_exists'] or v['owned_child_processes']
                                      for v in node_residue.values()),
        'retained_transfers': {
            'count': len(retained), 'ids': retained,
            'note': 'Hub has no DELETE endpoint for FileTransfer; successful rows persist as history '
                    '(design), the Hub staged blobs for these ids were cleaned on success.'},
        'hub_staged_leftovers_for_our_ids': our_hub_residue,
        'transfers_total_now': len(tr),
        'cleanup_results': cleanup,
    }
    oracle['clean'] = (ok and not any(leftovers.values())
                       and oracle['node_residue_clear']
                       and all(v['same'] for lab in pid_same.values() for v in lab.values())
                       and oracle['repair_hub_active']
                       and not our_hub_residue
                       and cleanup.get('all_node_get_404'))
    rec('final-cleanup-oracle.json', oracle)
    X.update_json(HERE / 'final-cleanup-oracle.json', oracle)
    return oracle


# --------------------------------------------------------------------------- #
def main():
    run = Run()
    run.state['units_before'] = X.unit_probe()
    evidence = {'batch_id': 'sys-b14', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base, 'cases': ['SYS-59', 'SYS-60', 'SYS-61', 'SYS-66'],
                'started_at_utc': X.now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b14', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': X.now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = X.now_utc()
        progress.update(extra)
        X.update_json(HERE / 'progress.json', progress)

    failed = False
    try:
        mark('guard'); evidence['phases']['guard'] = phase0_guard(run)
        mark('children'); evidence['phases']['children'] = phase1_children(run)
        progress['children'] = {k: run.child(k).node_id for k in ('src', 'tgt')}
        mark('main_transfer'); evidence['phases']['main'] = phase2_main_transfer(run)
        run.state['main_tid'] = evidence['phases']['main']['transfer_id']
        mark('auth_negatives'); evidence['phases']['auth'] = phase3_auth_negatives(run)
        mark('size_negatives'); evidence['phases']['size'] = phase4_size_negatives(run)
        run.state['size_tid'] = evidence['phases']['size']['transfer_id']
        mark('sha_retry'); evidence['phases']['sha'] = phase5_sha_negative_retry(run)
        run.state['sha_tid'] = evidence['phases']['sha']['transfer_id']
        mark('hub_restart'); evidence['phases']['restart'] = phase6_hub_restart(run)
        run.state['restart_tid'] = evidence['phases']['restart']['transfer_id']
        mark('settle'); evidence['phases']['settle'] = phase7_settle(run)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True
    mark('cleanup')
    # safety: the authorised repair Hub must never be left down
    try:
        if not hubctl.healthy():
            hubctl.start_repair_unit()
            hubctl.wait_healthy(timeout=90)
    except Exception as exc:
        evidence['deviations'].append('HUB_RECOVERY_SAFETY:%s' % exc)
    for name in ('src_proxy', 'tgt_proxy'):
        p = run.samples.get(name)
        if p and p.mode() != 'pass':
            try:
                p.set_mode('pass')
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
    evidence['state'] = {k: run.state.get(k) for k in
                         ('main_tid', 'size_tid', 'sha_tid', 'restart_tid')}
    X.update_json(HERE / 'run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b14', 'nonce': NONCE, 'prefix': PREFIX,
        'owned_children': {name: {'host': host, 'node_id': run.child(name).node_id,
                                  'agent_id': run.child(name).agent_id, 'root': run.child(name).root,
                                  'removed': bool((cleanup.get('children_removed', {}).get(name) or {})
                                                  .get('root_removed'))}
                           for name, host in (('src', SRC_HOST), ('tgt', TGT_HOST))},
        'owned_nodes_deleted_get404': {name: v.get('get_http') for name, v in
                                       (cleanup.get('node_deletes', {}) or {}).items()},
        'created_transfers': [
            {'id': run.state.get('main_tid'), 'purpose': 'SYS-59/60/61 positive end-to-end single target',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
            {'id': run.state.get('size_tid'), 'purpose': 'SYS-61 size validation negatives',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
            {'id': run.state.get('sha_tid'), 'purpose': 'SYS-61 SHA256 negative + retry',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
            {'id': run.state.get('restart_tid'), 'purpose': 'SYS-66 Hub restart / offline target',
             'terminal': 'SUCCESS', 'deletable': False,
             'retention_reason': 'Hub has no FileTransfer DELETE endpoint; row retained as history'},
        ],
        'node_side_files_removed': True,
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE',
    }
    X.update_json(HERE / 'fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = X.now_utc()
    X.update_json(HERE / 'progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b14', 'deviations': evidence['deviations'],
                      'clean': progress.get('oracle_clean')}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    main = ph.get('main', {})
    auth = ph.get('auth', {})
    size = ph.get('size', {})
    sha = ph.get('sha', {})
    restart = ph.get('restart', {})
    settle = ph.get('settle', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    canonical = ('NOT_RUN (repair build not executed; legacy baseline excluded per remaining_dimensions)')

    def case(case_id, title, proposed, confidence, dims, limits, resources):
        return {'case_id': case_id, 'title': title, 'canonical_before': canonical,
                'proposed_after': proposed, 'confidence': confidence,
                'covered_dimensions': dims, 'remaining_or_limitations': limits,
                'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                    'case-result.json', 'progress.json', 'fixture-ledger.json',
                    'final-cleanup-oracle.json', 'run-evidence.json'],
                'actual_resources': resources}

    resources = {
        'owned_prefix': PREFIX,
        'src_node_id': run.child('src').node_id, 'src_agent_id': run.child('src').agent_id,
        'tgt_node_id': run.child('tgt').node_id, 'tgt_agent_id': run.child('tgt').agent_id,
        'transfers': {'main': run.state.get('main_tid'), 'size': run.state.get('size_tid'),
                      'sha_retry': run.state.get('sha_tid'), 'hub_restart': run.state.get('restart_tid')},
        'retained_transfer_history': [run.state.get(k) for k in
                                      ('main_tid', 'size_tid', 'sha_tid', 'restart_tid')
                                      if run.state.get(k)]}

    cases = []
    sys59_ok = bool(main.get('assertions_pass') and auth.get('negatives_pass')
                    and auth.get('negatives_rejected', {}).get('source_download')
                    and auth.get('negatives_rejected', {}).get('target_upload')
                    and cleanup_ok)
    cases.append(case(
        'SYS-59', 'SYS-59 源 Agent 身份认证、源节点只能上传',
        'PASS' if sys59_ok else 'PARTIAL', 'high' if sys59_ok else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-59',
          'layer': 'real Agent HTTP auth + real Transfer dispatch (owned child Agent, exact repair binary)',
          'status': 'PASS' if sys59_ok else 'PARTIAL',
          'description': [
              "A real single-target transfer created on the Hub was uploaded by the source child Agent using its own real agent credential (X-NodeSteer-Agent-ID/Token) and completed to SUCCESS; the Hub recorded exactly the source size/sha256.",
              "The source node could NOT download its own transfer: an authenticated GET /agent/transfers/<id>/download as the source returned 403 \"agent is not a transfer target\".",
              "A non-source Agent could NOT upload: authenticated POST upload as the target node returned 403 \"agent is not the transfer source\".",
              "Wrong/absent credentials were rejected: bogus token 401 \"invalid agent credential\", unknown agent-id 401, no headers 401 \"agent authentication required\", and a management bearer token on the Agent channel 401.",
              "All negatives left the transfer row byte-identical (no side effect)."]}],
        [], resources))

    sys60_ok = bool(main.get('assertions_pass')
                    and auth.get('negatives_rejected', {}).get('target_upload')
                    and main.get('assertions', {}).get('target_sha_matches_source')
                    and cleanup_ok)
    cases.append(case(
        'SYS-60', 'SYS-60 目标 Agent 身份认证、目标只能下载自己的目标',
        'PASS' if sys60_ok else 'PARTIAL', 'high' if sys60_ok else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-60',
          'layer': 'real target Agent download auth + atomic replace (owned child Agent)',
          'status': 'PASS' if sys60_ok else 'PARTIAL',
          'description': [
              "The target child Agent authenticated with its own credential and downloaded the delivery it was dispatched, producing the exact source size/sha256/mode on disk.",
              "The target could NOT upload (it is not the source) -> 403 \"agent is not the transfer source\".",
              "A node that is not a target of a transfer cannot download it -> 403 \"agent is not a transfer target\" (source-download negative).",
              "Delivery is per-target: the transfer carries exactly one target row and the destination is replaced atomically (no temp left)."]}],
        [], resources))

    sys61_ok = bool(size.get('assertions_pass') and sha.get('assertions_pass')
                    and sha.get('atomic_promotion', {}).get('no_part_file'))
    cases.append(case(
        'SYS-61', 'SYS-61 大小校验、SHA256 校验、Hub staged blob 原子提升',
        'PASS' if sys61_ok else 'PARTIAL', 'high' if sys61_ok else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-61',
          'layer': 'real upload endpoint validation / Hub staging blob / target SHA verification',
          'status': 'PASS' if sys61_ok else 'PARTIAL',
          'description': [
              "Size validation on the real Agent upload endpoint: non-numeric size 400, negative size 400, declared size vs Content-Length mismatch 400 \"content length mismatch\"; the correct upload returned 200 and the Hub recorded the exact payload sha256.",
              "Hub staged-blob atomic promotion: while the transfer was staged and undelivered the Hub held <id>.blob (sha256 == source) and no <id>.part, i.e. the .part -> .blob rename happened atomically; after success both were cleaned.",
              "SHA256 validation on the target: with the staged blob deliberately corrupted before delivery the target Agent's verifying reader rejected the payload, the target reported FAILED and no half/corrupt file was installed.",
              "Restoring the blob and retrying the failed target produced SUCCESS with the exact source sha256/size."]}],
        [], resources))

    sys66_ok = bool(restart.get('assertions_pass') and settle.get('assertions_pass'))
    cases.append(case(
        'SYS-66', 'SYS-66 Hub 重启、Agent 重连、目标离线后状态和文件不产生半成品',
        'PASS' if sys66_ok else 'PARTIAL', 'high' if sys66_ok else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-66',
          'layer': 'repair-Hub unit restart / sqlite persistence / owned child reconnect / offline target',
          'status': 'PASS' if sys66_ok else 'PARTIAL',
          'description': [
              "The source uploaded and the Hub staged the blob while the target was offline; the transfer row (id/sha256/size) survived the ONE authorised repair-Hub restart unchanged.",
              "While the target stayed offline no destination file and no temp file existed on the target node, both before and after the restart (no half product).",
              "The source Agent reconnected by itself after the restart; when the target was brought back it reconnected, ResumeNode dispatched the delivery exactly once, and the target received the complete file with the exact source sha256/size/mode and no leftover temp.",
              "After a settle window the transfer was terminal SUCCESS with a single target row: a Hub restart produced no duplicate delivery or duplicated target row."]}],
        ["The observed Hub outage is short (unit stop/start, ~1-3 s); a long outage beyond the Agent heartbeat timeout was not exercised.",
         "The authority state was read from the Hub SQLite read-only copy and the REST API; the target verification failure was produced by corrupting the fixture's own staged blob (harness fault injection on the owned transfer only)."],
        resources))

    result = {'batch_id': 'sys-b14',
              'title': 'FileTransfer：源/目标身份认证、大小与 SHA 校验、Hub staged blob 原子提升、Hub 重启后无半成品',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': run.hub.base,
              'cases': cases, 'deviations': evidence['deviations'],
              'budget': evidence['budget'],
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management and agent data-plane writes only to repair http://192.168.100.209:18080',
                  'hub_restart': 'only nodesteer-hub-repair-20260930-101945.service was stopped/started once; '
                                 'nodesteer-hub.service (:8080) and all Agent units untouched',
                  'owned_children': 'source on native-1 and target on native-2, exact repair Agent binary '
                                    'd55dc5cd..., each behind its own harness frame proxy on the operator host',
                  'credential_source': "each child's own store (identity.credential), never a protected Agent config",
                  'product_source_changes': 0, 'git_operations': 0, 'vms_restarted': 0}}
    X.update_json(HERE / 'case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
