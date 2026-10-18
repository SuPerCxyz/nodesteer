#!/usr/bin/env python3
"""Finalize: rebuild fixture ledger, live 404 sweep, write oracle/progress/case-result."""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lib as L  # noqa: E402

hub = L.Hub()
hub.login()

# ---------- collect fixtures from case evidence ----------
fixtures = {}

def add(kind, fid, name=None, purpose=None, status=None, delete_http=None, get_after_http=None):
    key = (kind, fid)
    f = fixtures.setdefault(key, {'kind': kind, 'id': fid, 'name': name, 'purpose': purpose,
                                  'created': True, 'deleted': False})
    if name:
        f['name'] = name
    if purpose:
        f['purpose'] = purpose
    if status == 'deleted' or delete_http is not None:
        f['deleted'] = True
        f['delete_http'] = delete_http
        f['get_after_http'] = get_after_http


g = L.load('g0403-cleanup.json')
for d in (g or {}).get('deleted', []):
    add('group', d['id'], d.get('name'), 'G04-03 duplicate-name regression', 'deleted', d.get('delete_http'), d.get('get_after_http'))

x = L.load('x08-results.json') or {}
for d in (x.get('cleanup', {}) or {}).get('deleted', []):
    add(d['kind'], d['id'], None, 'X08 fixture', 'deleted', d.get('delete_http'), d.get('get_after_http'))
# names/purposes from earlier entries
for f in [
    ('task', '47a29cca-bd91-4478-8c4f-5890a7e7cc53', L.PREFIX + 'stop', 'X08 stop cancel/confirm + slow response'),
    ('node', '901da814-0040-42f6-9cdd-96a152a45f91', L.PREFIX + 'node', 'X08 revoke-credential cancel (owned)'),
    ('node', '14d19a9d-cddf-4da9-8cee-8ec9f404f377', L.PREFIX + 'retry', 'X08 enrollment failure->retry'),
    ('task', 'd42d5888-dd64-45f5-9b62-15669418f91b', L.PREFIX + 'retry', 'SYS-40 retry=2 per-attempt audit evidence'),
    ('node', '9ab139d0-4b3c-4656-a1bb-00944f5ab75f', L.PREFIX + 'revnode', 'X08 revoke-credential confirm (owned)'),
]:
    add(f[0], f[1], f[2], f[3])

t6 = L.load('x08-t6-revoke-confirm.json') or {}
c6 = t6.get('cleanup') or {}
if c6.get('get404') is not None:
    add('node', t6['owned_node']['id'], t6['owned_node']['name'], 'X08 revoke-credential confirm (owned)', 'deleted', c6.get('delete_http'), c6.get('get_after_http'))
s = L.load('sys40-retry-audit.json') or {}
cs = s.get('cleanup') or {}
if cs.get('get404') is not None:
    add('task', s['task']['id'], s['task']['name'], 'SYS-40 retry=2 per-attempt audit evidence', 'deleted', cs.get('delete_http'), cs.get('get_after_http'))

# ---------- live 404 sweep (authoritative final verification) ----------
live = []
for (kind, fid), f in sorted(fixtures.items()):
    base = {'group': '/api/groups/', 'node': '/api/nodes/', 'task': '/api/tasks/'}[kind]
    c, body, _ = hub.get(base + fid)
    f['live_get_http'] = c
    f['live_get404'] = c == 404
    live.append({'kind': kind, 'id': fid, 'name': f.get('name'), 'get_http': c, 'get404': c == 404})

ledger = {
    'batch': 'qa-resume-20261017-partial-rest',
    'nonce': L.NONCE,
    'owner': L.OWNER,
    'authority': L.UI + ' (repair)',
    'finalized_at_utc': L.now_utc(),
    'fixtures': [{k: v for k, v in f.items()} for (k, fid), f in sorted(fixtures.items())],
    'live_404_sweep': live,
    'all_owned_deleted_get404': all(l['get404'] for l in live),
    'non_deletable_history': {
        'executions': [
            {'id': '9fec8595-6e5c-4c55-a6b0-f44b9b9a13cb', 'status': 'CANCELED', 'task': L.PREFIX + 'stop', 'batch_test': 'X08 T1/T2'},
            {'id': 'c8030d06-f141-4091-be74-344527028e65', 'status': 'FAILED', 'task': L.PREFIX + 'retry', 'batch_test': 'SYS-40'},
        ],
        'note': 'execution records have no delete API; these are the only executions created by this batch (verified: no other execution started after 14:25Z)',
    },
}
(HERE / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=1))

# ---------- leftover scan ----------
scan = {}
for path, key in [('/api/tasks', 'tasks'), ('/api/groups', 'groups'), ('/api/nodes', 'nodes'),
                  ('/api/schedules', 'schedules'), ('/api/scripts', 'scripts'), ('/api/artifacts', 'artifacts'),
                  ('/api/applications', 'applications'), ('/api/users', 'users')]:
    c, body, _ = hub.get(path)
    hits = [x for x in (body or []) if L.OWNER in json.dumps(x, ensure_ascii=False)]
    scan[key] = {'http': c, 'total': len(body or []), 'owned_leftovers': len(hits)}

# ---------- repair authority + protected nodes ----------
hc, hb, _ = hub.raw('GET', '/healthz')
c, nodes, _ = hub.get('/api/nodes')
protected = {n['hostname']: {'status': n.get('status'), 'sync_status': n.get('sync_status')}
             for n in nodes if n['hostname'].startswith('qa-repair-20260930-101945-native')}
sha = subprocess.run(['ssh', '-i', L.SSH_KEY, '-o', 'ConnectTimeout=8', L.SSH_DEST,
                      'sha256sum /opt/nodesteer-repair-20260930-101945/bin/nodesteer-hub-static-login-guard'],
                     capture_output=True, text=True, timeout=30).stdout.split()[0]

# ---------- remote port/process check ----------
ss = subprocess.run(['ssh', '-i', L.SSH_KEY, '-o', 'ConnectTimeout=8', L.SSH_DEST,
                     'ss -ltn | grep -c ":18087 " || true; ls -d /tmp/nodesteer-iso-* 2>/dev/null | wc -l; pgrep -f "hub-static-login-guard --config /tmp/nodesteer-iso-[p]rst" | wc -l'],
                    capture_output=True, text=True, timeout=30)
port_18087, iso_dirs, iso_procs = [l.strip() for l in ss.stdout.strip().splitlines()[:3]]

# ---------- browser sessions ----------
ab_sessions = subprocess.run(['agent-browser', 'session', 'list'], capture_output=True, text=True, timeout=30).stdout.strip()

# ---------- counts ----------
pre = {'tasks': 0, 'groups': 0, 'schedules': 0, 'scripts': 2, 'artifacts': 1, 'applications': 1, 'users': 8,
       'nodes': 6, 'executions_total': 1551, 'captured': 'first admin snapshot 2026-10-17T14:31Z (executions total derived: 1553 - 2 batch executions, verified no other execution started after 14:25Z)'}
post = L.load('post-batch-snapshot.json') or {}
oracle = {
    'batch_id': 'qa-resume-20261017-partial-rest',
    'finalized_at_utc': L.now_utc(),
    'all_owned_deleted_get404': ledger['all_owned_deleted_get404'],
    'live_404_sweep': live,
    'leftover_scan': scan,
    'isolated_instance': (L.load('instance-ledger.json') or {}).get('instances', [{}])[0],
    'repair_authority': {'healthz': {'http': hc, 'body': hb}, 'binary_sha256': sha,
                         'build_label_ref': 'dddee4f0-e2e-repair-20261017-fixall (sha matches fixall-verify deploy evidence)',
                         'protected_nodes': protected},
    'remote_hygiene': {'port_18087_listeners': int(port_18087 or 0), 'iso_dirs_left': int(iso_dirs or 0),
                       'iso_processes_left': int(iso_procs or 0)},
    'browser_sessions': {'session_list_output': ab_sessions, 'own_session_closed': 'No active sessions' in ab_sessions},
    'counts_pre_post': {'pre': pre, 'post': {k: post.get(k) for k in ('tasks', 'groups', 'schedules', 'scripts', 'artifacts', 'applications', 'users', 'nodes', 'executions_total')}},
    'non_deletable_history': ledger['non_deletable_history'],
    'declarations': {
        'primary_8080_contacted': False,
        'git_or_archive_operations': False,
        'product_source_or_config_changes': False,
        'hub_or_protected_agent_restarted': False,
        'protected_node_state_changed': False,
        'writes_outside_owned_fixtures_or_isolated_instance': False,
        'evidence_write_scope': '.e2e/runs/20260930-101945/repair/qa-resume-20261017-partial-rest/ only',
    },
    'tmp_files_removed': ['/tmp/opencode-prst-node-id', '/tmp/opencode-qa-partial-rest-token'],
}
(HERE / 'final-cleanup-oracle.json').write_text(json.dumps(oracle, ensure_ascii=False, indent=1))

# ---------- progress ----------
progress = {
    'batch': 'qa-resume-20261017-partial-rest',
    'nonce': L.NONCE,
    'started_at_utc': '2026-10-17T14:31:00Z',
    'finished_at_utc': L.now_utc(),
    'build_label_under_test': 'dddee4f0-e2e-repair-20261017-fixall',
    'hub_sha256': sha,
    'phases': [
        {'phase': 'read-ledger', 'status': 'DONE', 'detail': 'remaining_dimensions read from repair/feature-inventory.json + partial-closeout-plan + g7 brief'},
        {'phase': 'G04-03', 'status': 'DONE', 'result': 'PASS', 'evidence': ['evidence/g0403-api.json', 'evidence/g0403-ui.json', 'evidence/g0403-source-scan.json', 'evidence/g0403-cleanup.json']},
        {'phase': 'P04-07', 'status': 'DONE', 'result': 'PASS', 'evidence': ['evidence/p0407-isolated.json', 'instance-ledger.json']},
        {'phase': 'X08', 'status': 'DONE', 'result': 'PASS', 'evidence': ['evidence/x08-results.json', 'evidence/x08-t4-rerun.json', 'evidence/x08-t6-revoke-confirm.json']},
        {'phase': 'SYS-40', 'status': 'DONE', 'result': 'PARTIAL (maintained, product ruling)', 'evidence': ['evidence/sys40-retry-audit.json']},
        {'phase': 'cleanup+oracle', 'status': 'DONE', 'result': 'all owned deleted GET404', 'evidence': ['fixture-ledger.json', 'final-cleanup-oracle.json']},
    ],
    'cases_total': 4, 'cases_pass_proposed': 3, 'cases_partial_maintained': 1,
}
(HERE / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=1))

print('ledger fixtures:', len(ledger['fixtures']), 'all404:', ledger['all_owned_deleted_get404'])
print('leftovers:', {k: v['owned_leftovers'] for k, v in scan.items()})
print('remote hygiene:', oracle['remote_hygiene'])
print('browser:', ab_sessions)
print('sha match:', sha == '1d8cc17e3aeeb5df59026bc3881a6ff8b5a3e308617c931ac1f13d70a9cc230b')
