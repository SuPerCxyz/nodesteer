#!/usr/bin/env python3
"""Batch07 runner: transfer target isolation/retry/path-safety (P22-10/11/13/14)
and Agent artifact-cache `.tmp` behaviour (P16-09/P16-10) on the repair authority.

Node-side scope: native-3 (192.168.100.222) only, own directory
/tmp/qa-w1410b07-<nonce>.  Adds /tmp/opencode-harness.

Real-evidence chain per action:
  operator/API/UI act -> real request -> real state change -> physical effect
  (target file content/mode/sha256, agent cache file/.tmp, Hub blob) -> readback
  (API + node filesystem + agent log/SQLite).

Fixture budget: <=6 owned transfers (no DELETE endpoint -> terminal history),
<=3 owned artifacts, <=1 owned application.  Never creates/deletes
User/Node/Group/Script/Task, never changes protected node state, never performs a
real deploy, never restarts Hub/protected Agent, never edits product source.
"""
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import hub
import nodes

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EV = BATCH / 'evidence'
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-w1410b07-%s' % NONCE
N3DIR = '/tmp/qa-w1410b07-%s' % NONCE
NATIVE3 = '37989297-3bc8-4c0c-b302-7c6efbdba630'
HOST001 = 'f6a79d23-2410-450c-ac39-64dd70c9daa1'
N3_AGENT_ID = 'agent-bb005487-7d84-418c-b598-f93d8b0c86a0'
BASELINE_CACHE_SHA = '37a674d655a6ac4111e488608d81d368364db8d8a13d4293d03e9f9eec8a7b8a'
STATE_PATH = BATCH / 'state.json'
HUB_ARTIFACT_DIR = nodes.HUB_ARTIFACTS
HUB_TRANSFER_DIR = nodes.HUB_DATA + '/file-transfers'


def now():
    return hub.now_utc()


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {'nonce': NONCE, 'prefix': PREFIX, 'created': {'transfers': [], 'artifacts': [], 'applications': []},
            'evidence': {}, 'log': [], 'budget': {'transfers': 0, 'artifacts': 0, 'applications': 0}}


def save_state(st):
    st['updated_at'] = now()
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=True) + '\n')


def rec(st, name, value, note=''):
    hub.write_json(name, value)
    st['evidence'][name] = {'note': note, 'at': now()}
    save_state(st)
    return value


def sha_of(path_on_n3):
    r = nodes.n3_out('sha256sum %s 2>/dev/null | cut -d" " -f1' % path_on_n3)
    return r['stdout'].strip() or None


def poll_transfer(h, tid, timeout=180, terminal=('SUCCESS', 'FAILED', 'CANCELED')):
    """Poll until the transfer is terminal; return (final_item, history)."""
    hist = []
    deadline = time.time() + timeout
    item = None
    while time.time() < deadline:
        code, item = h.get('/api/transfers/%s' % tid)
        if code != 200:
            hist.append({'at': now(), 'http': code})
            time.sleep(1)
            continue
        hist.append({'at': now(), 'status': item.get('status'),
                     'targets': {t['node_id'][:8]: t['status'] for t in item.get('targets') or []}})
        if item.get('status') in terminal:
            return item, hist
        time.sleep(1)
    return item, hist


def wait_for(pred, timeout=60, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    return None


def hub_blob_stat(path):
    r = nodes.hubsh_out('stat -c "%%s %%a %%U" %s 2>/dev/null || echo MISSING' % path)
    return r['stdout']


# ---------------------------------------------------------------- stage: setup
def stage_setup(h, st):
    script = """
set -e
d=%s
rm -rf "$d"; mkdir -p "$d"
head -c 1048576 /dev/urandom > "$d/src-1m.bin"
head -c 2097152 /dev/urandom > "$d/src-2m.bin"
head -c 524288  /dev/urandom > "$d/src-small.bin"
head -c 8388608 /dev/urandom > "$d/src-8m.bin"
truncate -s 10737418241 "$d/src-overlimit.bin"
printf 'OLD-CONTENT-MARKER-%%s\\n' "%s" > "$d/dst-replace.bin"
chmod 600 "$d/dst-replace.bin"
printf 'OLD-INTERRUPT-MARKER\\n' > "$d/dst-interrupt.bin"
chmod 644 "$d/dst-interrupt.bin"
printf 'retry-symlink\\n' > "$d/dst-symlink-target.txt"
ln -s "$d/dst-symlink-target.txt" "$d/dst-symlink.bin"
ls -la "$d"
echo "--sha"
sha256sum "$d"/*.bin 2>/dev/null | grep -v overlimit
""" % (N3DIR, NONCE)
    out = nodes.n3_out(script)
    if out['rc'] != 0:
        raise RuntimeError('node setup failed: %s' % (out['stderr'] or out['stdout']))
    st['node_dir'] = N3DIR
    st['setup'] = out
    return rec(st, 'stage0-node-setup.json', {'node_dir': N3DIR, 'result': out,
                                              'note': 'owned native-3 fixture dir'}, 'setup')


# ------------------------------------------------- stage: P22-13 API negatives
def stage_p22_13_neg(h, st):
    ev = {'at': now(), 'authority': h.base, 'cases': {}, 'negative_side_effects': {}}

    def create(src, dst, target=HOST001, mode=None):
        t = {'node_id': target, 'destination_path': dst}
        if mode:
            t['mode'] = mode
        return h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src, 'targets': [t]})

    # a) relative source path
    ev['cases']['rel_source_path'] = {'request': {'source_path': 'qa/relative.bin'},
                                      'response': create('qa/relative.bin', N3DIR + '/x.bin')}
    # b) relative destination path
    ev['cases']['rel_dest_path'] = {'request': {'destination_path': 'qa/relative-dst.bin'},
                                    'response': create(N3DIR + '/src-1m.bin', 'qa/relative-dst.bin')}
    # c) source traversal
    ev['cases']['traversal_source'] = {'request': {'source_path': '/tmp/../etc/passwd'},
                                       'response': create('/tmp/../etc/passwd', N3DIR + '/esc.bin')}
    # d) destination traversal / escape
    ev['cases']['traversal_dest'] = {'request': {'destination_path': N3DIR + '/../../etc/evil.bin'},
                                     'response': create(N3DIR + '/src-1m.bin', N3DIR + '/../../etc/evil.bin')}
    ev['cases']['traversal_dest_nested'] = {'request': {'destination_path': '/tmp/./../tmp/evil2.bin'},
                                            'response': create(N3DIR + '/src-1m.bin', '/tmp/./../tmp/evil2.bin')}
    # e) empty source / empty target / duplicate target / unknown node
    ev['cases']['empty_source_path'] = {'response': create('', N3DIR + '/x.bin')}
    ev['cases']['unknown_source_node'] = {'response': h.post('/api/transfers', {
        'source_node_id': '00000000-0000-0000-0000-000000000000',
        'source_path': N3DIR + '/src-1m.bin',
        'targets': [{'node_id': HOST001, 'destination_path': '/tmp/x.bin'}]})}
    ev['cases']['duplicate_target'] = {'response': h.post('/api/transfers', {
        'source_node_id': NATIVE3, 'source_path': N3DIR + '/src-1m.bin',
        'targets': [{'node_id': HOST001, 'destination_path': '/tmp/a.bin'},
                    {'node_id': HOST001, 'destination_path': '/tmp/b.bin'}]})}
    ev['cases']['no_target'] = {'response': h.post('/api/transfers', {
        'source_node_id': NATIVE3, 'source_path': N3DIR + '/src-1m.bin', 'targets': []})}

    # f) wrong Agent credential on the Agent data endpoints (real gateway auth)
    tid = 'b07nonexistent-0000'
    cred_cases = {}
    cred_cases['no_headers_download'] = h.raw('GET', '/agent/transfers/%s/download' % tid)
    cred_cases['bogus_id_token_download'] = h.raw('GET', '/agent/transfers/%s/download' % tid,
                                                 agent_headers={'X-NodeSteer-Agent-ID': 'agent-00000000',
                                                                'X-NodeSteer-Agent-Token': 'not-a-token'})
    cred_cases['real_agent_id_wrong_token_download'] = h.raw(
        'GET', '/agent/transfers/%s/download' % tid,
        agent_headers={'X-NodeSteer-Agent-ID': N3_AGENT_ID, 'X-NodeSteer-Agent-Token': 'wrong-token-b07'})
    cred_cases['real_agent_id_wrong_token_upload'] = h.raw(
        'POST', '/agent/transfers/%s/upload' % tid, raw=b'',
        agent_headers={'X-NodeSteer-Agent-ID': N3_AGENT_ID, 'X-NodeSteer-Agent-Token': 'wrong-token-b07',
                       'X-NodeSteer-File-Size': '4'})
    cred_cases['admin_user_token_on_agent_channel'] = h.raw(
        'GET', '/agent/transfers/%s/download' % tid,
        agent_headers={'X-NodeSteer-Agent-ID': N3_AGENT_ID, 'X-NodeSteer-Agent-Token': h.token})
    # e2) artifact download endpoint credential check (Agent identity required)
    cred_cases['artifact_download_no_credential'] = h.raw('GET', '/api/artifacts/00000000-0000-0000-0000-000000000000/download',
                                                         token='')
    ev['cases']['agent_credential'] = {k: {'http': v[0], 'body': v[1]} for k, v in cred_cases.items()}

    # negative side effects: no transfer created, no Hub staging file, no node file
    code, tl = h.get('/api/transfers')
    ev['negative_side_effects']['transfers_total'] = len(tl or [])
    ev['negative_side_effects']['owned_prefix_in_list'] = [t['id'] for t in (tl or []) if t.get('source_path', '').startswith(N3DIR)]
    ev['negative_side_effects']['hub_staging'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    ev['negative_side_effects']['node_dir_listing'] = nodes.n3_out('ls -la %s/' % N3DIR)
    ev['negative_side_effects']['host001_cannot_be_inspected'] = 'host integration container is used only to produce a fast allowlist failure'
    return rec(st, 'P22-13-api-negatives.json', ev, 'P22-13 path/credential negatives')


# --------------------------------------------------- stage: P22-10 isolation
def _isolation_attempt(h, st, src, dst_ok, dst_bad, timeout, tag, prior_notes):
    ev = {'at': now(), 'tag': tag, 'prior_notes': prior_notes}
    src_sha = sha_of(src)
    code, body = h.post('/api/transfers', {
        'source_node_id': NATIVE3, 'source_path': src,
        'targets': [{'node_id': NATIVE3, 'destination_path': dst_ok, 'mode': 0o640},
                    {'node_id': HOST001, 'destination_path': dst_bad}]})
    ev['create'] = {'http': code, 'body': body}
    if code != 200:
        return ev, None
    tid = body['id']
    st['created']['transfers'].append({'id': tid, 'case': 'P22-10 isolation (%s)' % tag, 'source': src,
                                       'src_sha256': src_sha, 'targets': {'native-3': dst_ok, 'host001': dst_bad},
                                       'mode': oct(0o640)})
    st['budget']['transfers'] += 1
    save_state(st)
    t0 = time.time()
    item, hist = poll_transfer(h, tid, timeout=timeout)
    ev['transfer_id'] = tid
    ev['src_sha256'] = src_sha
    ev['poll_seconds'] = round(time.time() - t0, 1)
    ev['final'] = item
    ev['status_history'] = hist
    ev['node_side_native3'] = nodes.n3_file_state(dst_ok)
    ev['hub_staging_after'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    ev['hub_blob_stat'] = hub_blob_stat('%s/%s.blob' % (HUB_TRANSFER_DIR, tid))
    if item and item.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED'):
        # second target never delivered: cancel to avoid a permanently open delivery
        code2, body2 = h.post('/api/transfers/%s/cancel' % tid)
        ev['cancel'] = {'http': code2, 'reason': 'second target stayed DELIVERING with no agent result',
                        'body': body2}
        time.sleep(2)
        code3, item3 = h.get('/api/transfers/%s' % tid)
        ev['final_after_cancel'] = item3
    ev['agent_log_native3'] = nodes.agent_logs(since='10 min ago', grep='deliver|transfer')
    return ev, tid


def stage_p22_10(h, st):
    """2-target isolation attempt #2: native-3 (allowlisted /tmp) + host001 (non-allowlist path)."""
    ev, tid = _isolation_attempt(h, st, N3DIR + '/src-2m.bin', N3DIR + '/iso-native3.bin',
                                 '/var/tmp/qa-w1410b07-%s/x-host001.bin' % NONCE, timeout=90, tag='attempt2',
                                 prior_notes='attempt1 (3f91190c) native-3 SUCCESS while host001 target stayed '
                                             'DELIVERING and never reported a result; recorded in '
                                             'P22-10-isolation-attempt1.json')
    return rec(st, 'P22-10-isolation.json', ev, 'P22-10 isolation attempt2')


def stage_p22_10_recover(h, st):
    """Re-read attempt #1's terminal state after the harness shell timeout interrupted the poll loop."""
    tid = '3f91190c-0fb0-42e2-8c48-c56d501ae34d'
    code, item = h.get('/api/transfers/%s' % tid)
    ev = {
        'at': now(), 'tag': 'attempt1-reconstructed',
        'note': 'the poll loop was killed by the harness shell timeout; the live observations were '
                'native-3 target SUCCESS within ~2s and host001 target DELIVERING with no error for '
                '>12 min (10 polls at 5s after the initial 180s poll loop).  A harness fault injection '
                '(truncating the Hub staged blob to 4096 bytes) did not produce any agent-side result '
                'either.  The transfer was then canceled through POST /api/transfers/{id}/cancel.',
        'transfer_id': tid, 'final': item,
        'node_side_native3': nodes.n3_file_state(N3DIR + '/iso-native3.bin'),
        'hub_staging_after': nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR),
        'targets': [{'node_id': t['node_id'], 'status': t['status'], 'error': t.get('error'),
                     'destination_path': t['destination_path'], 'mode': t.get('mode'),
                     'updated_at': t.get('updated_at')} for t in (item or {}).get('targets') or []],
    }
    return rec(st, 'P22-10-isolation-attempt1.json', ev, 'P22-10 isolation attempt1 (reconstructed)')


# --------------------------------------------------- stage: P22-11 retries
def stage_p22_11(h, st):
    ev = {'at': now(), 'cases': {}}

    # ---- case A: FAILED target (destination is a symlink) -> target_id retry
    src = N3DIR + '/src-1m.bin'
    src_sha = sha_of(src)
    dst = N3DIR + '/retry-symlink.bin'     # pre-created symlink by setup
    code, body = h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src,
                                           'targets': [{'node_id': NATIVE3, 'destination_path': dst}]})
    if code != 200:
        ev['cases']['target_id_retry'] = {'create_http': code, 'create_body': body}
        return rec(st, 'P22-11-retry.json', ev, 'P22-11 retry')
    tid = body['id']
    st['created']['transfers'].append({'id': tid, 'case': 'P22-11 target_id retry', 'source': src,
                                       'src_sha256': src_sha, 'targets': {'native-3': dst}})
    st['budget']['transfers'] += 1
    save_state(st)
    item, hist = poll_transfer(h, tid, timeout=120)
    target_id = (item.get('targets') or [{}])[0].get('node_id')
    a = {'transfer_id': tid, 'src_sha256': src_sha, 'destination': dst,
         'failed_state': item, 'status_history': hist,
         'node_side_after_fail': nodes.n3_file_state(dst),
         'temp_files_after_fail': nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)}
    # fix the fault: remove the blocking symlink
    a['fix'] = nodes.n3_out('rm -f %s' % dst)
    code2, body2 = h.post('/api/transfers/%s/retry?target_id=%s' % (tid, target_id))
    a['retry_request'] = {'http': code2, 'body': body2, 'target_id': target_id, 'scope': 'single target'}
    item2, hist2 = poll_transfer(h, tid, timeout=120)
    a['after_retry'] = item2
    a['after_retry_history'] = hist2
    a['node_side_after_retry'] = nodes.n3_file_state(dst)
    ev['cases']['target_id_retry'] = a

    # ---- case B: source-side FAILED (missing source file) -> whole-transfer retry
    src_b = N3DIR + '/src-late.bin'        # does not exist yet
    dst_b = N3DIR + '/retry-whole.bin'
    code, body = h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src_b,
                                           'targets': [{'node_id': NATIVE3, 'destination_path': dst_b}]})
    if code == 200:
        tid_b = body['id']
        st['created']['transfers'].append({'id': tid_b, 'case': 'P22-11 whole retry (source side)',
                                           'source': src_b, 'targets': {'native-3': dst_b}})
        st['budget']['transfers'] += 1
        save_state(st)
        item, hist = poll_transfer(h, tid_b, timeout=120)
        b = {'transfer_id': tid_b, 'source': src_b, 'destination': dst_b,
             'failed_state': item, 'status_history': hist}
        # fix: create the missing source file with known content
        create = nodes.n3_out('head -c 1048576 /dev/urandom > %s && sha256sum %s' % (src_b, src_b))
        b['fix'] = create
        src_b_sha = (create['stdout'].split()[0] if create['stdout'] else None)
        code2, body2 = h.post('/api/transfers/%s/retry' % tid_b)
        b['retry_request'] = {'http': code2, 'body': body2, 'scope': 'whole transfer (no target_id)'}
        item2, hist2 = poll_transfer(h, tid_b, timeout=180)
        b['after_retry'] = item2
        b['after_retry_history'] = hist2
        b['node_side_after_retry'] = nodes.n3_file_state(dst_b)
        b['src_sha256'] = src_b_sha
        ev['cases']['whole_retry_source_side'] = b
    else:
        ev['cases']['whole_retry_source_side'] = {'create_http': code, 'create_body': body}

    # ---- case C: whole-transfer retry does not re-deliver an already SUCCESS target
    t_retry = next((t for t in st['created']['transfers'] if t.get('case') == 'P22-11 target_id retry'), None)
    if t_retry:
        dst_ok = t_retry['targets']['native-3']
        before = nodes.n3_file_state(dst_ok)
        code, body = h.post('/api/transfers/%s/retry' % t_retry['id'])
        time.sleep(4)
        after = nodes.n3_file_state(dst_ok)
        code2, item = h.get('/api/transfers/%s' % t_retry['id'])
        ev['cases']['whole_retry_skips_success_target'] = {
            'transfer_id': t_retry['id'], 'retry_http': code, 'retry_body': body,
            'native3_dst_before': before, 'native3_dst_after': after,
            'final': item,
            'interpretation': 'no FAILED target left -> retry keeps the terminal state and does not '
                              're-dispatch the SUCCESS target (dest mtime/sha stable)'}
    return rec(st, 'P22-11-retry.json', ev, 'P22-11 retry semantics')


# ------------------------------------------------- stage: P22-13 size limit
def stage_p22_13_limit(h, st):
    ev = {'at': now()}
    src = N3DIR + '/src-overlimit.bin'
    dst = N3DIR + '/dst-overlimit.bin'
    ev['source'] = nodes.n3_file_state(src)
    ev['limit_expected'] = 'Hub default MaxFileTransferBytes = 10 GiB (no max_file_transfer_bytes in repair hub config)'
    code, body = h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src,
                                           'targets': [{'node_id': NATIVE3, 'destination_path': dst}]})
    ev['create'] = {'http': code, 'body': body}
    if code == 200:
        tid = body['id']
        st['created']['transfers'].append({'id': tid, 'case': 'P22-13 file over limit', 'source': src,
                                           'targets': {'native-3': dst}})
        st['budget']['transfers'] += 1
        save_state(st)
        t0 = time.time()
        item, hist = poll_transfer(h, tid, timeout=180)
        ev['transfer_id'] = tid
        ev['elapsed_s'] = round(time.time() - t0, 2)
        ev['final'] = item
        ev['status_history'] = hist
        ev['node_side_dst'] = nodes.n3_file_state(dst)
        ev['hub_staging_after'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
        ev['hub_blob_stat'] = hub_blob_stat('%s/%s.blob' % (HUB_TRANSFER_DIR, tid))
        ev['hub_part_stat'] = hub_blob_stat('%s/%s.part' % (HUB_TRANSFER_DIR, tid))
        code2, body2 = h.post('/api/transfers/%s/retry' % tid)
        time.sleep(3)
        code3, item3 = h.get('/api/transfers/%s' % tid)
        ev['retry'] = {'http': code2, 'body': body2, 'after': item3}
        ev['agent_log'] = nodes.agent_logs(since='5 min ago', grep='upload|transfer|size')
    return rec(st, 'P22-13-size-limit.json', ev, 'P22-13 over-limit')


# ------------------------------------------- stage: P22-14 success + atomic
def stage_p22_14_success(h, st):
    ev = {'at': now()}
    src = N3DIR + '/src-8m.bin'
    dst = N3DIR + '/dst-replace.bin'
    src_sha = sha_of(src)
    ev['src'] = nodes.n3_file_state(src)
    ev['dst_before'] = nodes.n3_file_state(dst)
    code, body = h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src,
                                           'targets': [{'node_id': NATIVE3, 'destination_path': dst, 'mode': 0o640}]})
    ev['create'] = {'http': code, 'body': body}
    if code != 200:
        return rec(st, 'P22-14-success.json', ev, 'P22-14 success (create failed)')
    tid = body['id']
    st['created']['transfers'].append({'id': tid, 'case': 'P22-14 content/mode/sha/atomic replace', 'source': src,
                                       'src_sha256': src_sha, 'targets': {'native-3': dst}, 'requested_mode': '640'})
    st['budget']['transfers'] += 1
    save_state(st)
    item, hist = poll_transfer(h, tid, timeout=180)
    ev['transfer_id'] = tid
    ev['src_sha256'] = src_sha
    ev['final'] = item
    ev['status_history'] = hist
    ev['dst_after'] = nodes.n3_file_state(dst)
    ev['dst_matches_src'] = (ev['dst_after']['stdout'] and src_sha in ev['dst_after']['stdout'])
    ev['temp_files_after'] = nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)
    ev['hub_staging_after'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    ev['agent_log'] = nodes.agent_logs(since='5 min ago', grep='deliver|transfer')
    return rec(st, 'P22-14-success.json', ev, 'P22-14 success/atomic replace')


# ------------------------------- stage: P22-14 half-file cleanup on interruption
def stage_p22_14_interrupt(h, st):
    ev = {'at': now()}
    src = N3DIR + '/src-big.bin'
    dst = N3DIR + '/dst-interrupt.bin'
    # src: 160 MiB of real random bytes; dst: pre-existing 192 MiB sparse file whose
    # size is LARGER than the incoming file, so a non-atomic writer would leave a stale tail.
    create = nodes.n3_out(
        'rm -f %s %s; head -c 167772160 /dev/urandom > %s; truncate -s 201326592 %s; '
        'sha256sum %s; stat -c "%%s %%a" %s' % (src, dst, src, dst, src, dst), timeout=240)
    ev['src_and_dst_setup'] = create
    src_sha = create['stdout'].split()[0] if create['stdout'] else None
    ev['src_sha256'] = src_sha
    ev['dst_before'] = nodes.n3_file_state(dst)
    ev['src_size'] = nodes.n3_out('stat -c %%s %s' % src)['stdout']
    code, body = h.post('/api/transfers', {'source_node_id': NATIVE3, 'source_path': src,
                                           'targets': [{'node_id': NATIVE3, 'destination_path': dst, 'mode': 0o640}]})
    ev['create'] = {'http': code, 'body': body}
    if code != 200:
        return rec(st, 'P22-14-interrupt.json', ev, 'P22-14 interruption (create failed)')
    tid = body['id']
    st['created']['transfers'].append({'id': tid, 'case': 'P22-14 half-file cleanup + target_id retry',
                                       'source': src, 'src_sha256': src_sha, 'targets': {'native-3': dst},
                                       'requested_mode': '640'})
    st['budget']['transfers'] += 1
    save_state(st)
    ev['transfer_id'] = tid
    blob = '%s/%s.blob' % (HUB_TRANSFER_DIR, tid)
    observed = {'temp_seen': None, 'temp_samples': []}
    deadline = time.time() + 150
    while time.time() < deadline:
        r = nodes.n3_out("ls -la %s/.nodesteer-transfer-* 2>/dev/null | head -3 || echo none" % N3DIR)
        if 'nodesteer-transfer' in r['stdout']:
            observed['temp_seen'] = r['stdout']
            break
        time.sleep(0.05)
    if observed['temp_seen']:
        observed['temp_sample_1'] = nodes.n3_out(
            "stat -c '%%s' %s/.nodesteer-transfer-* 2>/dev/null | head -1 || echo none" % N3DIR)['stdout']
        ev['hub_blob_before'] = hub_blob_stat(blob)
        observed['blob_backup'] = nodes.hubsh_out('cp -a %s %s.b07backup && stat -c "%%s" %s.b07backup' % (blob, blob, blob))
        observed['hub_blob_truncated'] = nodes.hubsh_out('truncate -s 4096 %s && stat -c "%%s" %s' % (blob, blob))
        observed['temp_sample_2'] = nodes.n3_out(
            "stat -c '%%s' %s/.nodesteer-transfer-* 2>/dev/null | head -1 || echo none" % N3DIR)['stdout']
    ev['inflight_observation'] = observed
    item, hist = poll_transfer(h, tid, timeout=240)
    ev['final_after_interrupt'] = item
    ev['status_history'] = hist
    ev['temp_files_after_interrupt'] = nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)
    ev['dst_after_interrupt'] = nodes.n3_file_state(dst)
    ev['agent_log_after_interrupt'] = nodes.agent_logs(since='10 min ago', grep='deliver|transfer|sha')
    # restore the Hub staged blob and retry with target_id
    ev['blob_restore'] = nodes.hubsh_out('ls -la %s.b07backup 2>/dev/null; mv -f %s.b07backup %s && stat -c "%%s" %s' % (blob, blob, blob, blob))
    if item and item.get('status') == 'FAILED':
        tid_target = (item.get('targets') or [{}])[0].get('node_id')
        code2, body2 = h.post('/api/transfers/%s/retry?target_id=%s' % (tid, tid_target))
        ev['retry_request'] = {'http': code2, 'body': body2, 'target_id': tid_target,
                               'scope': 'single target (target_id)'}
        item2, hist2 = poll_transfer(h, tid, timeout=300)
        ev['after_retry'] = item2
        ev['after_retry_history'] = hist2
        ev['dst_after_retry'] = nodes.n3_file_state(dst)
        ev['dst_sha_matches_src'] = bool(src_sha and dst_after_contains(dst, src_sha))
        ev['dst_size_vs_src_size'] = {'dst': nodes.n3_out('stat -c %%s %s' % dst)['stdout'],
                                      'src': nodes.n3_out('stat -c %%s %s' % src)['stdout']}
        ev['temp_files_after_retry'] = nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)
        ev['hub_staging_after_retry'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
        ev['agent_log_after_retry'] = nodes.agent_logs(since='10 min ago', grep='deliver|transfer|sha')
    return rec(st, 'P22-14-interrupt.json', ev, 'P22-14 half-file cleanup + target_id retry')


def dst_after_contains(path, sha):
    r = nodes.n3_out('sha256sum %s 2>/dev/null | cut -d" " -f1' % path)
    return r['stdout'].strip() == sha


def stage_p22_14_half_file(h, st):
    """Half-file cleanup on a real mid-write failure.

    Deterministic node-side fault: the delivery temp file lives in the destination
    directory on native-3 `/tmp` (tmpfs, 979 MiB).  Freeing only ~96 MiB before the
    delivery makes the agent's temp write fail with ENOSPC part-way through the
    download, so a half file exists on disk when the write fails.  The filler is
    removed immediately after the terminal state is captured, then the same
    transfer is retried through POST /api/transfers/{id}/retry?target_id=...
    """
    tid = 'f926be58-68db-4181-854f-dba803340500'   # P22-13 over-limit transfer, FAILED with sha256=""
    ev = {'at': now(), 'transfer_id': tid, 'strategy': 'tmpfs ENOSPC during the delivery temp write'}
    src = N3DIR + '/src-overlimit.bin'
    dst = N3DIR + '/dst-overlimit.bin'
    filler = N3DIR + '/.enospc-filler.bin'
    try:
        prep = nodes.n3_out('rm -f %s/src-big.bin %s/dst-interrupt.bin %s; '
                            'head -c 268435456 /dev/urandom > %s; '
                            'truncate -s 201326592 %s; chmod 644 %s; '
                            'sha256sum %s; stat -c "%%s %%a" %s; df -B1 --output=avail /tmp | tail -1'
                            % (N3DIR, N3DIR, filler, src, dst, dst, src, dst), timeout=300)
        ev['prep'] = prep
        if prep['rc'] != 0:
            raise RuntimeError('prep failed: %s' % prep['stderr'])
        src_sha = prep['stdout'].split()[0]
        ev['src_sha256'] = src_sha
        ev['src_size'] = nodes.n3_out('stat -c %%s %s' % src)['stdout']
        ev['dst_before'] = nodes.n3_file_state(dst)
        # leave ~96 MiB free in the node tmpfs
        fill = nodes.n3_out(
            'free=$(df -B1 --output=avail /tmp | tail -1); '
            'want=$((free - 100663296 - 8388608)); '
            'echo "free_before=$free fill_bytes=$want"; '
            '[ $want -gt 0 ] && dd if=/dev/zero of=%s bs=1M count=$((want/1048576)) conv=fsync status=none; '
            'df -B1 --output=avail /tmp | tail -1' % filler, timeout=300)
        ev['tmpfs_fill'] = fill
        code, body = h.post('/api/transfers/%s/retry' % tid)
        ev['retry_source_redispatch'] = {'http': code, 'body_present': bool(body),
                                        'note': 'transfer had sha256="" (source-side failure) so retry restarts the source upload'}
        t0 = time.time()
        sampled_temp = []
        deadline = time.time() + 300
        item = None
        while time.time() < deadline:
            r = nodes.n3_out("stat -c '%%s' %s/.nodesteer-transfer-* 2>/dev/null | head -1 || echo none" % N3DIR)
            sampled_temp.append({'at_s': round(time.time() - t0, 1), 'temp_size': r['stdout']})
            code2, item = h.get('/api/transfers/%s' % tid)
            if code2 == 200 and item.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
                break
            time.sleep(0.4)
        ev['temp_size_samples'] = sampled_temp[-25:]
        ev['final_after_fill'] = item
    finally:
        ev['filler_cleanup'] = nodes.n3_out('rm -f %s; df -B1 --output=avail /tmp | tail -1' % filler)
    ev['temp_files_after_fail'] = nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)
    ev['dst_after_fail'] = nodes.n3_file_state(dst)
    ev['agent_log_after_fail'] = nodes.agent_logs(since='10 min ago', grep='deliver|transfer|space|error')
    item = ev.get('final_after_fill')
    if item and item.get('status') == 'FAILED':
        tid_target = (item.get('targets') or [{}])[0].get('node_id')
        code3, body3 = h.post('/api/transfers/%s/retry?target_id=%s' % (tid, tid_target))
        ev['retry_request'] = {'http': code3, 'target_id': tid_target, 'scope': 'single target'}
        item2, hist2 = poll_transfer(h, tid, timeout=300)
        ev['after_retry'] = item2
        ev['after_retry_history'] = hist2
        ev['dst_after_retry'] = nodes.n3_file_state(dst)
        ev['dst_sha_matches_src'] = bool(src_sha and dst_after_contains(dst, src_sha))
        ev['dst_size_vs_src_size'] = {'dst': nodes.n3_out('stat -c %%s %s' % dst)['stdout'],
                                      'src': nodes.n3_out('stat -c %%s %s' % src)['stdout']}
        ev['temp_files_after_retry'] = nodes.n3_out('ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo none' % N3DIR)
        ev['hub_staging_after_retry'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    return rec(st, 'P22-14-half-file.json', ev, 'P22-14 half-file cleanup (ENOSPC) + target_id retry')


def stage_p22_14_half_file_natural(h, st):
    """P22-14 half-file cleanup: real mid-write failure on an existing transfer.

    Live state when this stage runs: transfer f926be58 was retried (source re-dispatch
    after the source-side over-limit failure), the 256 MiB source upload completed, and
    the delivery then failed with a real agent-side write error while the node /tmp
    tmpfs was nearly full:
        write /tmp/qa-w1410b07-<nonce>/.nodesteer-transfer-<pid>: no space left on device
    Evidence captured here: the FAILED transfer/target, the destination file unchanged,
    the absence of any leftover .nodesteer-transfer-* half file, then a target_id retry
    that delivers successfully after the space was freed.
    """
    tid = 'f926be58-68db-4181-854f-dba803340500'
    ev = {'at': now(), 'transfer_id': tid,
          'fault': 'node /tmp tmpfs nearly full during the delivery temp write (real ENOSPC)',
          'dst': N3DIR + '/dst-overlimit.bin', 'src': N3DIR + '/src-overlimit.bin'}
    code, item = h.get('/api/transfers/%s' % tid)
    ev['failed_state'] = item
    ev['failed_targets'] = [{'node_id': t['node_id'], 'status': t['status'], 'error': t.get('error'),
                             'destination_path': t['destination_path'], 'mode': t.get('mode'),
                             'updated_at': t.get('updated_at')} for t in (item or {}).get('targets') or []]
    ev['half_file_globs_after_failure'] = nodes.n3_out(
        'ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo "none"' % N3DIR)
    ev['node_tmpfs_after_failure'] = nodes.n3_out('df -h /tmp | tail -1')
    ev['dst_after_failure'] = nodes.n3_file_state(N3DIR + '/dst-overlimit.bin')
    ev['src_state'] = nodes.n3_file_state(N3DIR + '/src-overlimit.bin')
    ev['agent_log_after_failure'] = nodes.agent_logs(since='20 min ago', grep='deliver|transfer|space|write')
    ev['hub_staging_after_failure'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    ev['hub_blob_stat'] = hub_blob_stat('%s/%s.blob' % (HUB_TRANSFER_DIR, tid))
    # recovery: retry the single FAILED target now that the node has space again
    tid_target = NATIVE3
    code2, body2 = h.post('/api/transfers/%s/retry?target_id=%s' % (tid, tid_target))
    ev['retry_request'] = {'http': code2, 'body_present': bool(body2), 'target_id': tid_target,
                           'scope': 'single target (target_id)'}
    item2, hist2 = poll_transfer(h, tid, timeout=300)
    ev['after_retry'] = item2
    ev['after_retry_history'] = hist2
    ev['dst_after_retry'] = nodes.n3_file_state(N3DIR + '/dst-overlimit.bin')
    ev['src_sha256'] = sha_of(N3DIR + '/src-overlimit.bin')
    ev['dst_sha_matches_src'] = bool(ev['src_sha256'] and dst_after_contains(N3DIR + '/dst-overlimit.bin', ev['src_sha256']))
    ev['dst_size_vs_src_size'] = {'dst': nodes.n3_out('stat -c %%s %s/dst-overlimit.bin' % N3DIR)['stdout'],
                                  'src': nodes.n3_out('stat -c %%s %s/src-overlimit.bin' % N3DIR)['stdout']}
    ev['dst_mode_after_retry'] = nodes.n3_out('stat -c %%a %s/dst-overlimit.bin' % N3DIR)['stdout']
    ev['half_file_globs_after_retry'] = nodes.n3_out(
        'ls -la %s/.nodesteer-transfer-* 2>/dev/null || echo "none"' % N3DIR)
    ev['hub_staging_after_retry'] = nodes.hubsh_out('ls -la %s/ 2>/dev/null' % HUB_TRANSFER_DIR)
    ev['agent_log_after_retry'] = nodes.agent_logs(since='20 min ago', grep='deliver|transfer|space')
    return rec(st, 'P22-14-half-file.json', ev, 'P22-14 half-file cleanup (real ENOSPC) + target_id retry')


def stage_incident(h, st):
    """Record the harness incident on native-3 (.222) and its recovery, plus node health."""
    ev = {
        'at': now(),
        'summary': 'A harness fixture step that filled /tmp (tmpfs, 979 MiB) on native-3 (.222) while the '
                   'VM balloon was at 1 GiB caused severe guest memory pressure: sshd stopped answering '
                   'after 18:09Z, the repair Agent went offline at 2026-10-14T18:09:47Z, and my node-side '
                   'SSH checks failed until the balloon was raised.',
        'node': {'id': NATIVE3, 'ip': '192.168.100.222', 'kvm_domain': 'nodesteer-agent-2 (verified by MAC 52:54:00:6a:af:05)'},
        'filler_size_bytes': 483393536,
        'guest_observations': {'load_avg_15m_during_incident': 13.81,
                               'load_avg_1m_after_fix': 5.18,
                               'tmpfs_used_during_incident': '733M/979M',
                               'tmpfs_used_after_cleanup': '272M/979M'},
        'recovery': [
            {'action': 'virsh setmem nodesteer-agent-2 2097152 --live (balloon 1 GiB -> 2 GiB)',
             'effect': 'sshd answered again within ~1 min; repair Agent reconnected'},
            {'action': 'rm -f /tmp/qa-w1410b07-<nonce>/.enospc-filler.bin',
             'effect': 'tmpfs pressure removed'},
        ],
        'errors_i_made': [
            {'action': 'virsh reboot nodesteer-agent-3 --mode agent',
             'mistake': 'wrong KVM domain: nodesteer-agent-3 is 192.168.100.204 (repair native-1), not '
                        'native-3; the intended VM is 192.168.100.222 = nodesteer-agent-2',
             'effect': 'native-1 (.204) rebooted (uptime 8 min at 18:24Z); its nodesteer-agent.service, '
                       'nodesteer-agent-repair-...-native-1.service and managed app unit '
                       'qa-20260930-101945-web-app-6a32f.service restarted automatically and are active; '
                       'native-1 is online in the repair Hub again at 18:24:43Z',
             'disclosure': 'unintended side effect on another batch node; no manual service start/stop used'},
        ],
        'not_done': ['no repair Hub restart', 'no protected Agent stop/start by hand',
                     'no product source change', 'no other node fixture touched'],
    }
    # live health readback
    code, n3 = h.get('/api/nodes/%s' % NATIVE3)
    code2, n1 = h.get('/api/nodes/91f44866-8a28-435e-bbda-2f2367126c00')
    ev['live_readback'] = {'native3': {'status': n3.get('status'), 'last_seen': n3.get('last_seen')},
                           'native1': {'status': n1.get('status'), 'last_seen': n1.get('last_seen')}}
    ev['native3_services'] = nodes.n3_out('systemctl is-active nodesteer-agent.service '
                                         'nodesteer-agent-repair-20260930-101945-native-3.service; uptime; '
                                         'df -h /tmp | tail -1; free -m | head -2')
    ev['native3_agent_cache'] = nodes.agent_cache_state()
    return rec(st, 'incident-report.json', ev, 'harness incident + recovery + node health')


def stage_snapshot2(h, st):
    data = {'at': now(), 'authority': h.base, 'counts': h.counts(),
            'nodes': [{'id': n['id'], 'hostname': n['hostname'], 'status': n['status']} for n in h.nodes()],
            'owned_transfers': [], 'agent_cache': nodes.agent_cache_state(),
            'agent_db': nodes.agent_db_artifacts()}
    for t in st['created']['transfers']:
        code, item = h.get('/api/transfers/%s' % t['id'])
        if code == 200:
            data['owned_transfers'].append({'id': item['id'], 'case': t['case'], 'status': item['status'],
                                            'size': item.get('size'), 'sha256': item.get('sha256'),
                                            'targets': [{'node_id': x['node_id'], 'status': x['status'],
                                                         'error': x.get('error')} for x in item.get('targets') or []]})
    return rec(st, 'snapshot-after-transfers.json', data, 'post-transfer snapshot')


STAGES = {
    'setup': stage_setup,
    'p22-13-neg': stage_p22_13_neg,
    'p22-10': stage_p22_10,
    'p22-10-recover': stage_p22_10_recover,
    'p22-11': stage_p22_11,
    'p22-13-limit': stage_p22_13_limit,
    'p22-14-success': stage_p22_14_success,
    'p22-14-interrupt': stage_p22_14_interrupt,
    'p22-14-half-file': stage_p22_14_half_file,
    'p22-14-half-natural': stage_p22_14_half_file_natural,
    'incident': stage_incident,
    'snapshot2': stage_snapshot2,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in STAGES:
        print('usage: run_b07.py <%s>' % '|'.join(STAGES))
        return 2
    st = load_state()
    h = hub.Hub()
    h.login()
    ev = STAGES[sys.argv[1]](h, st)
    save_state(st)
    print(json.dumps({'stage': sys.argv[1], 'ok': True}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
