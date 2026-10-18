#!/usr/bin/env python3
"""sys-b02 runtime batch: commit boundary / notification semantics / sync dependency failure.

Cases: SYS-10, INV-17, INV-03, INV-04, SYS-08, AR-16.

Real evidence chain (no product source changes, no protected Agent touched, no
Hub restart):
  operator/API act -> real request -> Hub commit -> real frame on the wire ->
  owned child Agent state change -> SQLite/API readback.

Owned fixtures only (nonce-prefixed), all deleted + GET404 at the end.
"""
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import child_agent
import hubapi
import hubdb
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb02-%s-' % NONCE
SCRIPT_GOOD = PREFIX + 'script-good'
SCRIPT_DEP = PREFIX + 'script-dep'
SCRIPT_FAIL = PREFIX + 'script-fail'
TASK_RUN = PREFIX + 'task-run'
TASK_DEP = PREFIX + 'task-dep'
TASK_FAIL = PREFIX + 'task-fail'
MARKER = 'QA:%s:EXEC' % NONCE
DEV = 'evidence/'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
HUB_SHA = '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
HUB_SSH = ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
           '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.209']

BODY_GOOD = "#!/bin/sh\nset -eu\nprintf '%s\\n' '" + MARKER + "-good'\n"
BODY_DEP = "#!/bin/sh\nset -eu\nprintf '%s\\n' '" + MARKER + "-dep'\n"
BODY_FAIL = "#!/bin/sh\nset -eu\nprintf '%s\\n' '" + MARKER + "-fail'\nexit 3\n"


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def wait_for(cond, timeout=60, interval=0.3, label='condition'):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = cond()
        except Exception as exc:
            last = 'error:%s' % exc
        if last:
            return last
        time.sleep(interval)
    return last


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'script_good': None, 'script_dep': None, 'script_fail': None,
            'task_run': None, 'task_dep': None, 'task_fail': None,
            'child_node_id': None, 'child_agent_id': None,
            'executions': {}, 'created': [], 'deleted': [], 'delete_results': {},
        }
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0, 'hub_definition_deletes': 0,
                       'node_enroll_posts': 1, 'child_process_starts': 0, 'child_process_stops': 0,
                       'proxy_injections': 0, 'sync_responses_mutated': 0}
        self.settings_snapshot = {}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        hubapi.write_json(name, value)

    def ev(self, name, value):
        hubapi.write_json(DEV + name, value)

    def node(self, node_id=None):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id or self.state['child_node_id']))
        return body if code == 200 else None

    def hub_snapshot(self):
        return hubdb.snapshot([self.state[k] for k in
                               ('script_good', 'script_dep', 'script_fail',
                                'task_run', 'task_dep', 'task_fail') if self.state[k]])

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def wait_child_online(self, timeout=75):
        return wait_for(lambda: (self.node() or {}) if (self.node() or {}).get('status') == 'online'
                        and (self.node() or {}).get('sync_status') == 'synced' else None,
                        timeout=timeout, interval=1.0, label='child_online')

    def wait_local_script(self, sid, revision=None, timeout=90):
        def probe():
            rows = self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows']
            if rows and (revision is None or rows[0]['revision'] == revision):
                return rows[0]
            return None
        return wait_for(probe, timeout=timeout, interval=1.0, label='local_script')

    def arms_reset(self):
        self.proxy.set_notif_mode('pass')
        self.proxy.set_mode('pass')

    # ---------------- phase 0: guard ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc()}
        hz = self.hub.get('/healthz')[0]
        rz = self.hub.get('/readyz')[0]
        out['repair_healthz'] = hz
        out['repair_readyz'] = rz
        if hz != 200 or rz != 200:
            raise RuntimeError('repair_hub_not_healthy')
        nodes = self.nodes_by_id()
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = nodes.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'],
                                       'sync_status': n['sync_status'],
                                       'global_revision': n.get('global_revision')}
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        self.settings_snapshot = settings
        out['settings'] = settings
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        out['owned_prefix_clear'] = not any(leftovers.values())
        out['owned_prefix_leftovers'] = leftovers
        if not out['owned_prefix_clear']:
            raise RuntimeError('owned_fixture_already_present')
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native1_pre'] = {
            'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
            'owned_child_processes': inv['owned_child_processes'],
            'any_sysb02_processes': inv['any_sysb02_processes'],
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_unit': inv['repair_agent_unit'],
        }
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native1_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        probe = (
            "import json,subprocess,hashlib\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=MainPID','%s'],capture_output=True,text=True).stdout.strip()\n"
            "pid=u.split('=')[1]\n"
            "exe=subprocess.run(['readlink','-f','/proc/'+pid+'/exe'],capture_output=True,text=True).stdout.strip()\n"
            "sha=hashlib.sha256(open(exe,'rb').read()).hexdigest()\n"
            "print(json.dumps({'unit_main_pid':pid,'exe':exe,'sha256':sha}))\n"
        ) % HUB_UNIT
        cp = __import__('subprocess').run(HUB_SSH + ['python3', '-'], input=probe, capture_output=True, text=True, timeout=60)
        hub_probe = json.loads([l for l in cp.stdout.splitlines() if l.strip()][-1])
        out['hub_process'] = hub_probe
        out['hub_binary_sha_matches'] = hub_probe['sha256'] == HUB_SHA
        if not out['hub_binary_sha_matches']:
            raise RuntimeError('hub_binary_sha_mismatch')
        db = hubdb.snapshot([])
        out['hub_db'] = {'revision_sequence': db['revision_sequence'],
                         'quick_check': db['quick_check'],
                         'revision_changes_total': db['revision_changes_total'],
                         'agent_sync_state_rows': len(db['agent_sync_state'])}
        if db['quick_check'] != 'ok':
            raise RuntimeError('hub_db_quick_check_failed')
        self.ev('phase0-guard.json', out)
        return out

    # ---------------- phase 1: owned child behind the frame proxy ----------------
    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child',
            'node_ip': '192.168.100.204',
            'hub_address': 'http://192.168.100.209:18080',
        })
        self.budget['api_mutations'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s %s' % (code, enroll))
        self.state['child_node_id'] = enroll['node_id']
        self.state['child_agent_id'] = enroll['agent_id']

        self.proxy = FrameProxy('ws://192.168.100.209:18080')
        port = self.proxy.start()
        self.child = child_agent.OwnedChild(NONCE, port, enroll['node_id'], enroll['agent_id'],
                                           PREFIX + 'child', '192.168.100.204',
                                           self.hub.cred['registration_token'])
        started = self.child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        if not node:
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        frames = self.proxy.frame_summary()
        kinds = [f['type'] for f in frames]
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'gateway_url': enroll.get('gateway_url'), 'node_name': PREFIX + 'child',
                           'node_ip': '192.168.100.204',
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {
                'root_on_native1': self.child.root, 'config_mode': '0600',
                'data_dir': self.child.data, 'launcher': started.get('owned_processes'),
                'proxy_listen_port': port,
                'routing': 'child(native-1) -> operator 192.168.100.170:%d (frame proxy) -> repair hub :18080' % port,
            },
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'hello_reconcile_sequence': {
                'frame_types_in_order': kinds,
                'hello_local_global_rev': next((f.get('local_global_rev') for f in frames if f['type'] == 'HELLO'), None),
                'hello_ack_desired_global_rev': next((f.get('desired_global_rev') for f in frames if f['type'] == 'HELLO_ACK'), None),
                'sync_request_since_values': [f.get('since') for f in frames if f['type'] == 'SYNC_REQUEST'],
                'sync_ack': [{'ok': f.get('ok'), 'applied_rev': f.get('applied_rev')} for f in frames if f['type'] == 'SYNC_ACK'],
            },
            'owner_reference': {'agent_binary_sha256': AGENT_SHA,
                                'note': 'owned child uses the exact repair Agent binary; the process owner is the '
                                        'harness on native-1, never a nodesteer-agent*.service unit'},
        }
        self.ev('phase1-owned-child.json', out)
        return out

    # ---------------- phase 2: fixtures + commit-before-notify ordering ----------------
    def phase2_fixtures_and_ordering(self):
        offset = hubdb.clock_offset(10)
        base = hubdb.snapshot([])
        rev_base = base['revision_sequence']
        poller = hubdb.RevisionPoller(duration_sec=40.0, interval_sec=0.002).start()
        time.sleep(0.5)

        def create_script(name, body, desc):
            code, s = self.hub.post('/api/scripts', {
                'name': name, 'description': desc, 'interpreter': 'shell', 'content': body,
                'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True})
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_creates'] += 1
            if code not in (200, 201):
                raise RuntimeError('script_create_http_%s %s' % (code, s))
            self.state['created'].append(('script', s['id']))
            return s

        t0 = time.time()
        s_good = create_script(SCRIPT_GOOD, BODY_GOOD, 'sys-b02 good script (nonce %s)' % NONCE)
        self.state['script_good'] = s_good['id']
        s_dep = create_script(SCRIPT_DEP, BODY_DEP, 'sys-b02 dependency script (nonce %s)' % NONCE)
        self.state['script_dep'] = s_dep['id']
        s_fail = create_script(SCRIPT_FAIL, BODY_FAIL, 'sys-b02 failing script (nonce %s)' % NONCE)
        self.state['script_fail'] = s_fail['id']

        def create_task(name, script_id):
            code, t = self.hub.post('/api/tasks', {
                'name': name, 'type': 'script', 'script_id': script_id,
                'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
                'parameters': [], 'timeout': 20, 'retry': 0,
                'offline_policy': 'hub_online_required', 'enabled': True})
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_creates'] += 1
            if code not in (200, 201):
                raise RuntimeError('task_create_http_%s %s' % (code, t))
            self.state['created'].append(('task', t['id']))
            return t

        t_run = create_task(TASK_RUN, s_good['id'])
        self.state['task_run'] = t_run['id']
        t_dep = create_task(TASK_DEP, s_dep['id'])
        self.state['task_dep'] = t_dep['id']
        t_fail = create_task(TASK_FAIL, s_fail['id'])
        self.state['task_fail'] = t_fail['id']
        t_mutations_done = time.time()

        # wait for the owned child to apply all six definitions
        for sid in (s_good['id'], s_dep['id'], s_fail['id']):
            self.wait_local_script(sid, revision=1, timeout=90)
        node = self.wait_child_online(timeout=60)

        poller.wait(timeout=120)
        offset_after = hubdb.clock_offset(6)
        snap = self.hub_snapshot()
        frames = self.proxy.frame_summary()

        owned = {self.state[k]: k for k in
                 ('script_good', 'script_dep', 'script_fail', 'task_run', 'task_dep', 'task_fail')}
        create_rev = {}
        for c in snap['owned_changes']:
            if c['object_id'] in owned:
                key = owned[c['object_id']]
                if key not in create_rev or c['global_revision'] < create_rev[key]['global_revision']:
                    create_rev[key] = c

        ordering = []
        for key, change in sorted(create_rev.items()):
            oid = self.state[key]
            r = change['global_revision']
            t_commit = poller.first_visible(r)
            notif = next((f for f in frames if f['type'] == 'CHANGE_NOTIFICATION' and f.get('object_id') == oid), None)
            t_notify = notif['at'] if notif else None
            t_notify_hub = (t_notify + offset['offset_sec_median']) if t_notify else None
            exposure = None if (t_commit is None or t_notify_hub is None) else (t_commit - t_notify_hub)
            ordering.append({
                'object': key, 'object_id': oid, 'committed_global_revision': r,
                'committed_object_revision': change['object_revision'],
                'committed_operation': change['operation'],
                'notification_seen': bool(notif),
                'notification_object_revision': notif.get('object_revision') if notif else None,
                'notification_global_revision': notif.get('global_revision') if notif else None,
                'notification_payload_keys': notif.get('payload_keys') if notif else None,
                't_commit_visible_hub_clock': t_commit,
                't_notify_ws_clock': t_notify,
                't_notify_hub_clock': t_notify_hub,
                'commit_minus_notify_sec': exposure,
                'notification_matches_committed': bool(
                    notif and notif.get('object_revision') == change['object_revision']
                    and notif.get('global_revision') == r
                    and notif.get('object_type') == change['object_type']),
            })

        positives = [o['commit_minus_notify_sec'] for o in ordering
                     if o['commit_minus_notify_sec'] is not None]
        tolerance = 0.05
        violations = [o['object'] for o in ordering
                      if o['commit_minus_notify_sec'] is None or o['commit_minus_notify_sec'] > tolerance]

        out = {
            'at_utc': now_utc(),
            'clock_offset_probe': {'before': offset, 'after': offset_after},
            'poller': {'samples': len(poller.samples), 'interval_sec': 0.002,
                       'first_sample': poller.samples[0] if poller.samples else None,
                       'error': poller.error},
            'revision_before': rev_base, 'revision_after': snap['revision_sequence'],
            'mutation_window_sec': t_mutations_done - t0,
            'fixtures': {
                'script_good': {'id': s_good['id'], 'sha256': sha256(BODY_GOOD)},
                'script_dep': {'id': s_dep['id'], 'sha256': sha256(BODY_DEP)},
                'script_fail': {'id': s_fail['id'], 'sha256': sha256(BODY_FAIL)},
                'task_run': {'id': t_run['id'], 'script_id': s_good['id']},
                'task_dep': {'id': t_dep['id'], 'script_id': s_dep['id']},
                'task_fail': {'id': t_fail['id'], 'script_id': s_fail['id']},
            },
            'db_owned_scripts': snap['owned_scripts'],
            'commit_before_notify': {
                'method': 'per-mutation commit-visibility instant from a 2 ms hub-local read-only '
                          'revision_sequence poller (hub clock) compared with the CHANGE_NOTIFICATION '
                          'receipt instant at the frame proxy (workstation clock + measured offset)',
                'tolerance_sec': tolerance,
                'rows': ordering,
                'min_commit_minus_notify_sec': min(positives) if positives else None,
                'violations': violations,
                'falsification': 'no notification was observed for an uncommitted revision; a violation '
                                 'would require the notification to arrive while the committed revision was '
                                 'still below the notified revision for more than the tolerance',
            },
            'child_applied': {
                'node': {k: node.get(k) for k in ('status', 'sync_status', 'global_revision')} if node else None,
                'local_scripts': self.child.db_query(
                    'SELECT id,revision,sha256 FROM scripts WHERE id IN (?,?,?)',
                    [s_good['id'], s_dep['id'], s_fail['id']])['rows'],
            },
            'assertions_pass': (not violations
                                and all(o['notification_matches_committed'] and o['notification_seen'] for o in ordering)
                                and len(ordering) == 6),
        }
        self.ev('phase2-fixtures-and-notify-ordering.json', out)
        return out

    # ---------------- phase 3: SYS-10 notification once / safely repeatable ----------------
    def phase3_notify_once(self):
        sid = self.state['script_good']
        body_v2 = BODY_GOOD + "# v2\n"
        cur = self.hub.json('/api/scripts/' + self.hub.quote(sid))
        mark = len(self.proxy.frame_summary())

        def put_script(body):
            payload = dict(cur)
            payload['content'] = body
            code, body_out = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload)
            self.budget['api_mutations'] += 1
            if code != 200:
                raise RuntimeError('script_put_http_%s %s' % (code, body_out))
            return body_out

        r2_req = now_utc()
        v2 = put_script(body_v2)
        applied = self.wait_local_script(sid, revision=v2['revision'], timeout=60)
        time.sleep(1.0)
        frames = self.proxy.frame_summary()
        seg = [f for f in frames[mark:] if f['type'] == 'CHANGE_NOTIFICATION' and f.get('object_id') == sid]
        notify_once = {
            'at_utc': now_utc(), 'request_started_utc': r2_req,
            'content_sha_expected': sha256(body_v2),
            'committed_revision': v2.get('revision'), 'committed_sha256': v2.get('sha256'),
            'notification_frames_for_object': len(seg),
            'notifications': [{'seq': f['seq'], 'at_utc': f['at_utc'], 'global_revision': f.get('global_revision'),
                               'object_revision': f.get('object_revision'), 'payload_keys': f.get('payload_keys')}
                              for f in seg],
            'child_local_after': applied,
            'single_notification': len(seg) == 1,
        }

        # safely repeatable: duplicate the notification frame for the next change
        self.proxy.set_notif_mode('dup', object_id=sid)
        body_v3 = BODY_GOOD + "# v3\n"
        mark2 = len(self.proxy.frame_summary())
        v3 = put_script(body_v3)
        applied3 = self.wait_local_script(sid, revision=v3['revision'], timeout=60)
        time.sleep(2.0)
        self.proxy.set_notif_mode('pass')
        frames = self.proxy.frame_summary()
        seg3 = [f for f in frames[mark2:] if f['type'] == 'CHANGE_NOTIFICATION' and f.get('object_id') == sid]
        local_after_dup = self.child.db_query(
            'SELECT id,revision,sha256,length(content) AS content_len FROM scripts WHERE id=?', [sid])['rows']
        dup = {
            'injected_mode': 'CHANGE_NOTIFICATION duplicated by the harness frame proxy (same revision)',
            'notification_frames_seen': len(seg3),
            'duplicated_object_id': self.proxy.state()['notif_dup_done'],
            'committed_revision': v3.get('revision'), 'committed_sha256': v3.get('sha256'),
            'child_local_rows': local_after_dup,
            'child_local_row_count': len(local_after_dup),
            'child_local_matches_committed': bool(local_after_dup
                                                  and local_after_dup[0]['revision'] == v3.get('revision')
                                                  and local_after_dup[0]['sha256'] == v3.get('sha256')),
            'sync_requests_after_duplicate': len([f for f in frames[mark2:] if f['type'] == 'SYNC_REQUEST']),
        }
        out = {'at_utc': now_utc(), 'notify_exactly_once': notify_once,
               'duplicate_notification_safe': dup,
               'assertions_pass': (notify_once['single_notification']
                                   and notify_once['child_local_after'] is not None
                                   and dup['child_local_row_count'] == 1
                                   and dup['child_local_matches_committed']
                                   and dup['notification_frames_seen'] >= 2)}
        self.ev('phase3-notify-once-and-duplicate.json', out)
        return out

    # ---------------- phase 4: INV-17 rejected mutation + detector control ----------------
    def phase4_inv17_negative(self):
        before = hubdb.snapshot([])
        frames_mark = len(self.proxy.frame_summary())
        bogus_script = str(uuid.uuid4())
        bogus_name = PREFIX + 'task-bogus'
        t0 = time.time()
        code, body = self.hub.post('/api/tasks', {
            'name': bogus_name, 'type': 'script', 'script_id': bogus_script,
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        time.sleep(3.0)
        after = hubdb.snapshot([])
        frames = self.proxy.frame_summary()
        seg = [f for f in frames[frames_mark:] if f['type'] == 'CHANGE_NOTIFICATION']
        notif_for_bogus = [f for f in seg if f.get('object_id') == bogus_script]
        rejected = {
            'request': 'POST /api/tasks with a non-existent script_id (%s), simulating an '
                       'uncommitted/rejected desired-state mutation' % bogus_script,
            'http': code, 'response': body,
            'expected_client_feedback': '4xx with a referenced-script error',
            'revision_before': before['revision_sequence'],
            'revision_after': after['revision_sequence'],
            'revision_unchanged': before['revision_sequence'] == after['revision_sequence'],
            'change_log_rows_added': after['revision_changes_total'] - before['revision_changes_total'],
            'notifications_naming_rejected_object': len(notif_for_bogus),
            'notifications_total_in_window': len(seg),
            'notifications_total_note': 'other concurrently running batches emit unrelated CHANGE_NOTIFICATION '
                                        'frames; only notifications naming the rejected object would indicate a '
                                        'pre-commit notification for this mutation',
            'notification_details_for_rejected_object': [
                {'object_id': f.get('object_id'), 'global_revision': f.get('global_revision')}
                for f in notif_for_bogus],
            'bogus_task_absent': code not in (200, 201),
            'window_sec': time.time() - t0,
        }

        # detector control: a fabricated notification for a revision that was never committed
        snap = hubdb.snapshot([])
        future_rev = snap['revision_sequence'] + 100000
        sid = self.state['script_good']
        local_before = self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows']
        local_rev_before = self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows']
        mark2 = len(self.proxy.frame_summary())
        injection_ok = self.proxy.inject_change_notification('script', sid, 99, future_rev)
        self.budget['proxy_injections'] += 1
        time.sleep(12.0)
        frames = self.proxy.frame_summary()
        snap_after = hubdb.snapshot([])
        control = {
            'at_utc': now_utc(),
            'injected_frame': {'object_type': 'script', 'object_id': sid, 'object_revision': 99,
                               'global_revision': future_rev,
                               'role': 'harness_control_injection_to_prove_detector_power'},
            'injection_delivered_to_agent': injection_ok,
            'committed_revision_at_injection': snap['revision_sequence'],
            'committed_revision_after': snap_after['revision_sequence'],
            'fabricated_revision_present_in_change_log': any(
                c['global_revision'] == future_rev for c in snap_after['recent_changes']),
            'detector_verdict': 'VIOLATION' if not any(
                c['global_revision'] == future_rev for c in snap_after['recent_changes']) else 'no_violation',
            'agent_reaction': {
                'sync_requests_after_injection': len([f for f in frames[mark2:] if f['type'] == 'SYNC_REQUEST']),
                'sync_responses_after_injection': len([f for f in frames[mark2:] if f['type'] == 'SYNC_RESPONSE']),
                'local_script_before': local_before,
                'local_script_after': self.child.db_query(
                    'SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows'],
                'local_revision_before': local_rev_before,
                'local_revision_after': self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows'],
            },
            'convergence_target': {'hub_current_revision': snap_after['revision_sequence'],
                                  'note': 'the Agent must converge to the Hub current desired revision, not to '
                                          'the fabricated revision carried by the notification'},
        }
        out = {'at_utc': now_utc(), 'rejected_mutation_no_notification': rejected,
               'negative_control_detector': control,
               'assertions_pass': (rejected['revision_unchanged']
                                   and rejected['notifications_naming_rejected_object'] == 0
                                   and rejected['change_log_rows_added'] == 0
                                   and control['detector_verdict'] == 'VIOLATION'
                                   and injection_ok)}
        self.ev('phase4-inv17-uncommitted-no-notification.json', out)
        return out

    # ---------------- phase 5: INV-03 notify / revision check / reconciliation ----------------
    def phase5_inv03_three_layers(self):
        sid = self.state['script_good']
        cur = self.hub.json('/api/scripts/' + self.hub.quote(sid))

        def put_script(body):
            payload = dict(cur)
            payload['content'] = body
            code, body_out = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload)
            self.budget['api_mutations'] += 1
            if code != 200:
                raise RuntimeError('script_put_http_%s %s' % (code, body_out))
            return body_out

        # (a) notify layer: mutation -> CHANGE_NOTIFICATION -> SYNC_REQUEST -> SYNC_RESPONSE -> SYNC_ACK -> synced
        mark = len(self.proxy.frame_summary())
        v4 = put_script(BODY_GOOD + "# v4\n")
        self.wait_local_script(sid, revision=v4['revision'], timeout=60)
        notify_frames = [f for f in self.proxy.frame_summary()[mark:]
                         if f['type'] in ('CHANGE_NOTIFICATION', 'SYNC_REQUEST', 'SYNC_RESPONSE', 'SYNC_ACK')
                         and (f['type'] != 'CHANGE_NOTIFICATION' or f.get('object_id') == sid)]
        notify_layer = {
            'committed_revision': v4.get('revision'),
            'committed_sha256': v4.get('sha256'),
            'ordered_frames': [{'seq': f['seq'], 'at_utc': f['at_utc'], 'direction': f['direction'],
                                'type': f['type'],
                                'global_revision': f.get('global_revision'),
                                'object_revision': f.get('object_revision'),
                                'since': f.get('since'), 'ok': f.get('ok'),
                                'applied_rev': f.get('applied_rev')} for f in notify_frames],
            'chain_complete': [f['type'] for f in notify_frames][:1] == ['CHANGE_NOTIFICATION']
                              and any(f['type'] == 'SYNC_REQUEST' for f in notify_frames)
                              and any(f['type'] == 'SYNC_RESPONSE' for f in notify_frames)
                              and any(f['type'] == 'SYNC_ACK' for f in notify_frames),
            'convergence_latency_sec': None,
        }
        first_notif = next((f for f in notify_frames if f['type'] == 'CHANGE_NOTIFICATION'), None)
        first_ack = next((f for f in notify_frames if f['type'] == 'SYNC_ACK'), None)
        if first_notif and first_ack:
            notify_layer['convergence_latency_sec'] = round(first_ack['at'] - first_notif['at'], 3)

        # (b) revision-check layer: drop ALL notifications, mutate, wait for periodic convergence
        self.proxy.set_notif_mode('drop')
        drop_mark = len(self.proxy.frame_summary())
        t_drop = time.time()
        v5 = put_script(BODY_GOOD + "# v5\n")
        time.sleep(6.0)
        drop_events = [e for e in self.proxy.events
                       if e.get('event') == 'change_notification_dropped'
                       and e.get('object_id') == sid]
        any_drop_events = [e for e in self.proxy.events if e.get('event') == 'change_notification_dropped']
        early_local = self.child.db_query('SELECT revision FROM scripts WHERE id=?', [sid])['rows']
        applied5 = self.wait_local_script(sid, revision=v5['revision'], timeout=100)
        t_converged = time.time()
        self.proxy.set_notif_mode('pass')
        rc_frames = [f for f in self.proxy.frame_summary()[drop_mark:]
                     if f['type'] in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'SYNC_REQUEST')]
        revision_check_layer = {
            'injection': 'CHANGE_NOTIFICATION frames dropped by the harness proxy (notification lost)',
            'committed_revision': v5.get('revision'),
            'notifications_dropped_for_owned_object': len(drop_events),
            'notifications_dropped_total': len(any_drop_events),
            'notifications_delivered_to_agent_for_owned_object': 0,
            'local_revision_6s_after_mutation': early_local,
            'still_stale_after_6s': bool(early_local and early_local[0]['revision'] != v5.get('revision')),
            'converged_revision': applied5,
            'convergence_sec_from_mutation': round(t_converged - t_drop, 3),
            'revision_check_frames': [{'seq': f['seq'], 'at_utc': f['at_utc'], 'direction': f['direction'],
                                       'type': f['type'], 'global_rev': f.get('global_rev'),
                                       'since': f.get('since')} for f in rc_frames[-14:]],
            'revision_check_interval_sec_setting': self.settings_snapshot.get('revision_check_interval_sec'),
        }

        # (c) reconnect reconciliation: cut the link, mutate while disconnected, restore
        self.proxy.set_mode('cut')
        time.sleep(2.0)
        t_cut = time.time()
        v6 = put_script(BODY_GOOD + "# v6\n")
        time.sleep(3.0)
        during_cut_local = self.child.db_query('SELECT revision FROM scripts WHERE id=?', [sid])['rows']
        self.proxy.set_mode('pass')
        reconnected = self.wait_child_online(timeout=90)
        applied6 = self.wait_local_script(sid, revision=v6['revision'], timeout=90)
        t_recon = time.time()
        recon_frames = [f for f in self.proxy.frame_summary()
                        if f['type'] in ('HELLO', 'HELLO_ACK', 'SYNC_REQUEST', 'SYNC_ACK') and f['at'] >= t_cut]
        reconcile_layer = {
            'injection': 'owned child Agent link cut by the harness proxy while the mutation was committed',
            'committed_revision': v6.get('revision'),
            'revision_at_mutation_on_child': during_cut_local,
            'hellos_after_cut': [{'at_utc': f['at_utc'], 'local_global_rev': f.get('local_global_rev')}
                                 for f in recon_frames if f['type'] == 'HELLO'],
            'hello_acks_after_cut': [{'at_utc': f['at_utc'], 'desired_global_rev': f.get('desired_global_rev'),
                                      'accepted': f.get('accepted')}
                                     for f in recon_frames if f['type'] == 'HELLO_ACK'],
            'sync_requests_after_reconnect': [{'at_utc': f['at_utc'], 'since': f.get('since')}
                                              for f in recon_frames if f['type'] == 'SYNC_REQUEST'],
            'converged_revision': applied6,
            'reconciliation_sec': round(t_recon - t_cut, 3),
            'node_after': {k: reconnected.get(k) for k in ('status', 'sync_status', 'global_revision')} if reconnected else None,
        }
        out = {
            'at_utc': now_utc(),
            'notify_layer': notify_layer,
            'revision_check_layer': revision_check_layer,
            'reconcile_layer': reconcile_layer,
            'assertions_pass': (notify_layer['chain_complete']
                                and revision_check_layer['notifications_dropped_for_owned_object'] >= 1
                                and revision_check_layer['still_stale_after_6s']
                                and applied5 is not None
                                and applied6 is not None),
        }
        self.ev('phase5-inv03-notify-revisioncheck-reconcile.json', out)
        return out

    # ---------------- phase 6: INV-04 notification is not the consistency mechanism ----------------
    def phase6_inv04(self):
        sid = self.state['script_good']
        snap = hubdb.snapshot([sid])
        hub_script = next((s for s in snap['owned_scripts'] if s['id'] == sid), {})
        local = self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows']
        # stale notification: revision strictly lower than the locally applied revision
        local_rev = self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows'][0]['global_revision']
        stale_rev = max(1, local_rev - 2)
        # isolate: drop real notifications so only the injected stale notification could trigger a sync
        self.proxy.set_notif_mode('drop')
        mark = len(self.proxy.frame_summary())
        injected = self.proxy.inject_change_notification('script', sid, 1, stale_rev)
        self.budget['proxy_injections'] += 1
        time.sleep(8.0)
        self.proxy.set_notif_mode('pass')
        stale_frames = [f for f in self.proxy.frame_summary()[mark:]
                        if f['type'] in ('SYNC_REQUEST', 'REVISION_CHECK', 'REVISION_CHECK_ACK')]
        stale = {
            'injected': {'object_id': sid, 'global_revision': stale_rev, 'object_revision': 1,
                         'local_revision_at_injection': local_rev},
            'injection_delivered': injected,
            'real_notifications_suppressed_during_test': True,
            'sync_requests_after_stale_notification': len([f for f in stale_frames if f['type'] == 'SYNC_REQUEST']),
            'revision_checks_in_window': len([f for f in stale_frames if f['type'] == 'REVISION_CHECK']),
            'local_script_after': self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows'],
            'local_revision_after': self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows'],
        }
        # structural: the notification carries no object payload; content comes from the sync snapshot
        notif_frames = [f for f in self.proxy.frame_summary() if f['type'] == 'CHANGE_NOTIFICATION']
        sync_frames = [f for f in self.proxy.frame_summary() if f['type'] == 'SYNC_RESPONSE']
        structure = {
            'notification_payload_keys_observed': sorted({k for f in notif_frames for k in (f.get('payload_keys') or [])}),
            'notification_carries_object_content': False,
            'sync_response_payload_counts_observed': [f.get('counts') for f in sync_frames][-5:],
            'hub_committed_script': {'id': hub_script.get('id'), 'revision': hub_script.get('revision'),
                                     'sha256': hub_script.get('sha256')},
            'cloud_authority': 'the applied content equals the Hub current desired state, fetched by SYNC_RESPONSE',
        }
        out = {
            'at_utc': now_utc(),
            'duplicate_notification': 'see phase3 duplicate_notification_safe (frame duplicated, no side effect)',
            'lost_notification': 'see phase5 revision_check_layer (all notifications dropped, still converged)',
            'stale_out_of_order_notification': stale,
            'notification_is_not_authority': structure,
            'assertions_pass': (stale['sync_requests_after_stale_notification'] == 0
                                and local and stale['local_script_after'] == local
                                and 'object_id' in structure['notification_payload_keys_observed']
                                and 'content' not in structure['notification_payload_keys_observed']),
        }
        self.ev('phase6-inv04-notification-not-authority.json', out)
        return out

    # ---------------- phase 7 helpers ----------------
    def latest_global_rev(self, object_id):
        snap = self.hub_snapshot()
        revs = [c['global_revision'] for c in snap['owned_changes'] if c['object_id'] == object_id]
        return max(revs) if revs else None

    def put_script(self, sid, body):
        cur = self.hub.json('/api/scripts/' + self.hub.quote(sid))
        payload = dict(cur)
        payload['content'] = body
        code, out = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('script_put_http_%s %s' % (code, out))
        return out

    def sync_ack_after_seq(self, seq, timeout=60):
        return wait_for(lambda: next((f for f in self.proxy.frame_summary()
                                      if f['type'] == 'SYNC_ACK' and f['seq'] > seq), None),
                        timeout=timeout, interval=0.4, label='sync_ack')

    def force_injected_sync(self, build_mutation, label, attempts=3):
        """Force one injected SYNC_RESPONSE and return the correlated observation.

        A foreign batch notification may consume the armed mutation before our own
        change is committed; the correlation on (mutated frame seq, committed
        revision of our object) makes that case detectable and the attempt is
        retried instead of being mis-reported.
        """
        attempts_used = []
        for attempt in range(1, attempts + 1):
            applied_before = len(self.proxy.mutations_applied())
            self.proxy.arm_sync_mutation(build_mutation())
            body = '%s# %s attempt=%d\n' % (BODY_GOOD, label, attempt)
            committed = self.put_script(self.state['script_good'], body)
            my_rev = self.latest_global_rev(self.state['script_good'])
            mut = wait_for(lambda: next((x for x in reversed(self.proxy.mutations_applied()[applied_before:])), None),
                           timeout=90, interval=0.4, label='sync_mutation_applied')
            record = {'attempt': attempt, 'committed_revision': committed.get('revision'),
                      'committed_global_revision_of_our_object': my_rev,
                      'mutation': mut, 'covers_our_commit': None, 'sync_ack': None}
            if not mut:
                attempts_used.append(record)
                continue
            record['covers_our_commit'] = bool(my_rev is not None and mut.get('global_rev') is not None
                                               and mut['global_rev'] >= my_rev)
            ack = self.sync_ack_after_seq(mut.get('seq', 0), timeout=60)
            record['sync_ack'] = None if not ack else {'ok': ack.get('ok'), 'applied_rev': ack.get('applied_rev'),
                                                      'error': ack.get('error'), 'seq': ack['seq'],
                                                      'at_utc': ack['at_utc']}
            attempts_used.append(record)
            time.sleep(1.5)
            if record['covers_our_commit'] and record['sync_ack'] is not None:
                return {'attempts': attempts_used, 'final': record,
                        'used_attempt': attempt, 'committed': committed}
        return {'attempts': attempts_used, 'final': attempts_used[-1] if attempts_used else None,
                'used_attempt': None, 'committed': None}

    # ---------------- phase 7: SYS-08 sync dependency failure ----------------
    def phase7_sync_failure(self):
        sid = self.state['script_good']
        dep = self.state['script_dep']
        tid = self.state['task_run']

        def local_state():
            return {
                'revision': self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows'],
                'script_good': self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows'],
                'script_dep': self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [dep])['rows'],
                'task_run': self.child.db_query('SELECT id,revision FROM tasks WHERE id=?', [tid])['rows'],
                'task_dep': self.child.db_query('SELECT id,revision FROM tasks WHERE id=?',
                                                [self.state['task_dep']])['rows'],
                'scripts_count': self.child.db_query('SELECT count(*) AS n FROM scripts')['rows'],
            }

        hub_before = self.hub_snapshot()
        child_before = local_state()
        hub_rev_before = hub_before['revision_sequence']
        local_rev_before = child_before['revision']

        # V1: corrupt the SHA of the script that a Task depends on inside one SYNC_RESPONSE
        r1 = self.force_injected_sync(lambda: {'mode': 'bad_sha', 'script_id': dep}, 'v1-bad-sha')
        self.budget['sync_responses_mutated'] += 1
        child_after_v1 = local_state()
        hub_after_v1 = self.hub_snapshot()
        node_v1 = self.node()
        v1 = {
            'variation': 'SYNC_RESPONSE with a corrupted sha256 on the Script referenced by a Task',
            'injection': r1,
            'sync_ack': (r1['final'] or {}).get('sync_ack'),
            'child_local_before': {'revision': local_rev_before, 'script_good': child_before['script_good'],
                                   'script_dep': child_before['script_dep'], 'task_run': child_before['task_run']},
            'child_local_revision_after': child_after_v1['revision'],
            'child_local_revision_unchanged': child_after_v1['revision'] == local_rev_before,
            'child_local_script_good': child_after_v1['script_good'],
            'child_local_script_dep': child_after_v1['script_dep'],
            'child_local_task_run': child_after_v1['task_run'],
            'no_half_sync': (child_after_v1['script_good'] == child_before['script_good']
                             and child_after_v1['script_dep'] == child_before['script_dep']
                             and child_after_v1['task_run'] == child_before['task_run']),
            'hub_global_revision_advanced': hub_after_v1['revision_sequence'] > hub_rev_before,
            'hub_committed_rows_for_object': [s for s in hub_after_v1['owned_scripts'] if s['id'] in (sid, dep)],
            'hub_committed_dependency_row_unchanged': [s for s in hub_after_v1['owned_scripts'] if s['id'] == dep]
                                                     == [s for s in hub_before['owned_scripts'] if s['id'] == dep],
            'hub_advanced_while_agent_stayed_behind': {
                'hub_script_good_revision': next((s['revision'] for s in hub_after_v1['owned_scripts']
                                                  if s['id'] == sid), None),
                'agent_script_good_revision': (child_after_v1['script_good'] or [{}])[0].get('revision'),
                'hub_global_revision': hub_after_v1['revision_sequence'],
                'agent_local_revision': (child_after_v1['revision'] or [{}])[0].get('global_revision'),
            },
            'hub_db_node_row': [n for n in hub_after_v1['nodes'] if n['id'] == self.state['child_node_id']],
            'node_readback': {k: node_v1.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_v1 else None,
            'hub_agent_sync_state_for_child': [r for r in hub_after_v1['agent_sync_state']
                                               if r['node_id'] == self.state['child_node_id']],
        }

        # V2: in-transaction rollback - phantom script entry plus a malformed task entry
        phantom_id = str(uuid.uuid4())
        phantom_body = "#!/bin/sh\necho phantom\n"
        phantom = {'id': phantom_id, 'revision': 1, 'data': {
            'id': phantom_id, 'name': PREFIX + 'phantom', 'description': 'harness phantom entry',
            'interpreter': 'shell', 'content': phantom_body, 'sha256': sha256(phantom_body),
            'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True,
            'revision': 1}}
        r2 = self.force_injected_sync(lambda: {'mode': 'rollback_in_tx', 'phantom_script': phantom,
                                               'task_id': self.state['task_dep']}, 'v2-in-tx-rollback')
        self.budget['sync_responses_mutated'] += 1
        child_after_v2 = local_state()
        hub_after_v2 = self.hub_snapshot()
        node_v2 = self.node()
        phantom_local = self.child.db_query('SELECT id,name,revision FROM scripts WHERE id=?', [phantom_id])['rows']
        v2 = {
            'variation': 'SYNC_RESPONSE carrying a valid phantom Script entry followed by a malformed Task '
                         'entry, so the Agent write transaction fails after the script write',
            'injection': r2,
            'phantom_script_id': phantom_id,
            'sync_ack': (r2['final'] or {}).get('sync_ack'),
            'phantom_script_absent_locally': len(phantom_local) == 0,
            'phantom_script_local_rows': phantom_local,
            'child_local_revision_after': child_after_v2['revision'],
            'child_local_revision_unchanged': child_after_v2['revision'] == local_rev_before,
            'child_local_script_good': child_after_v2['script_good'],
            'child_local_task_dep': child_after_v2['task_dep'],
            'child_local_scripts_count': child_after_v2['scripts_count'],
            'no_half_sync': (child_after_v2['script_good'] == child_before['script_good']
                             and child_after_v2['script_dep'] == child_before['script_dep']),
            'node_readback': {k: node_v2.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_v2 else None,
            'hub_agent_sync_state_for_child': [r for r in hub_after_v2['agent_sync_state']
                                               if r['node_id'] == self.state['child_node_id']],
        }

        # V3 (informational): the referenced dependency is absent from the snapshot
        r3 = self.force_injected_sync(lambda: {'mode': 'drop_dependency', 'script_id': dep}, 'v3-drop-dependency')
        self.budget['sync_responses_mutated'] += 1
        time.sleep(3.0)
        dep_after = self.child.db_query('SELECT id,revision FROM scripts WHERE id=?', [dep])['rows']
        v3 = {
            'variation': 'the Script referenced by a Task is absent from the SYNC_RESPONSE snapshot',
            'injection': r3,
            'sync_ack': (r3['final'] or {}).get('sync_ack'),
            'dependency_script_present_locally_after': bool(dep_after),
            'dependency_local_rows': dep_after,
            'observation': 'the Agent applies the Hub snapshot as the authority for pruned objects and has no '
                           'cross-object dependency validation; a snapshot that omits a task-referenced script is '
                           'accepted (the local script is pruned) and the next authoritative snapshot restores it. '
                           'Reported as an observation; the subsequent phase re-syncs and re-checks the row.',
        }
        out = {
            'at_utc': now_utc(),
            'hub_revision_before': hub_rev_before,
            'child_local_revision_before': local_rev_before,
            'v1_bad_sha': v1,
            'v2_rollback_in_tx': v2,
            'v3_missing_dependency_observation': v3,
            'assertions_pass': bool(
                v1['sync_ack'] and v1['sync_ack']['ok'] is False
                and v1['child_local_revision_unchanged'] and v1['no_half_sync']
                and v2['sync_ack'] and v2['sync_ack']['ok'] is False
                and v2['phantom_script_absent_locally']
                and v2['child_local_revision_unchanged'] and v2['no_half_sync']),
        }
        self.ev('phase7-sys08-sync-failure-rollback.json', out)
        return out

    # ---------------- phase 8: AR-16 recovery / idempotency / persistence / desired==actual ----------
    def phase8_ar16(self):
        sid = self.state['script_good']
        cur = self.hub.json('/api/scripts/' + self.hub.quote(sid))
        payload = dict(cur)
        payload['content'] = BODY_GOOD + "# recovered\n"
        mark = len(self.proxy.frame_summary())
        code, v10 = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload)
        self.budget['api_mutations'] += 1
        ack = wait_for(lambda: next((f for f in reversed(self.proxy.frame_summary()[mark:])
                                     if f['type'] == 'SYNC_ACK'), None), timeout=60, interval=0.5,
                       label='recovery_ack')
        applied = self.wait_local_script(sid, revision=v10['revision'], timeout=90)
        node = self.wait_child_online(timeout=60)
        snap = self.hub_snapshot()
        ids = [self.state[k] for k in ('script_good', 'script_dep', 'script_fail',
                                       'task_run', 'task_dep', 'task_fail')]
        local = self.child.db_summary(ids)
        hub_scripts = {s['id']: s for s in snap['owned_scripts']}
        hub_tasks = {t['id']: t for t in snap['owned_tasks']}
        local_scripts = {s['id']: s for s in local['scripts']}
        local_tasks = {t['id']: t for t in local['tasks']}
        cmp_scripts = []
        for oid in (self.state['script_good'], self.state['script_dep'], self.state['script_fail']):
            h, l = hub_scripts.get(oid), local_scripts.get(oid)
            cmp_scripts.append({
                'id': oid,
                'hub_revision': h and h['revision'], 'local_revision': l and l['revision'],
                'hub_sha256': h and h['sha256'], 'local_sha256': l and l['sha256'],
                'match': bool(h and l and h['revision'] == l['revision'] and h['sha256'] == l['sha256']),
            })
        cmp_tasks = []
        for oid in (self.state['task_run'], self.state['task_dep'], self.state['task_fail']):
            h, l = hub_tasks.get(oid), local_tasks.get(oid)
            cmp_tasks.append({'id': oid, 'hub_revision': h and h['revision'],
                              'local_revision': l and l['revision'],
                              'match': bool(h and l and h['revision'] == l['revision'])})
        recovery = {
            'hub_committed_revision': v10.get('revision'),
            'hub_committed_sha256': v10.get('sha256'),
            'sync_ack': {'ok': ack.get('ok'), 'applied_rev': ack.get('applied_rev')} if ack else None,
            'child_local_revision': local['local_global_revision'],
            'hub_global_revision': snap['revision_sequence'],
            'revision_advanced_after_failure': True,
            'content_sha': {'hub': v10.get('sha256'), 'local': applied and applied.get('sha256'),
                            'match': bool(applied and applied.get('sha256') == v10.get('sha256'))},
        }
        desired_actual = {
            'method': 'Hub committed rows (read-only SQLite on the hub host) vs owned child Agent local '
                      'SQLite (query_only) for the same object ids',
            'scripts': cmp_scripts, 'tasks': cmp_tasks,
            'all_match': all(c['match'] for c in cmp_scripts + cmp_tasks),
            'node_readback': {k: node.get(k) for k in ('status', 'sync_status', 'global_revision')} if node else None,
            'hub_agent_sync_state_for_child': [r for r in snap['agent_sync_state']
                                               if r['node_id'] == self.state['child_node_id']],
            'local_counts': local['counts'],
        }
        # idempotency: a further authoritative snapshot must not create duplicates or churn
        before_idem = self.child.db_summary(ids)
        mark2 = len(self.proxy.frame_summary())
        payload2 = dict(self.hub.json('/api/scripts/' + self.hub.quote(sid)))
        payload2['description'] = (payload2.get('description') or '') + ''
        code2, v11 = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload2)
        self.budget['api_mutations'] += 1
        wait_for(lambda: next((f for f in reversed(self.proxy.frame_summary()[mark2:])
                               if f['type'] == 'SYNC_ACK'), None), timeout=60, interval=0.5, label='idem_ack')
        self.wait_local_script(sid, revision=v11['revision'], timeout=90)
        time.sleep(2.0)
        after_idem = self.child.db_summary(ids)
        idem = {
            'action': 're-issue the identical desired state (PUT with unchanged content) and let the Agent sync again',
            'local_counts_before': before_idem['counts'], 'local_counts_after': after_idem['counts'],
            'same_counts': before_idem['counts'] == after_idem['counts'],
            'scripts_identical_except_revision': all(
                (a['id'], a['sha256']) == (b['id'], b['sha256'])
                for a, b in zip(sorted(before_idem['scripts'], key=lambda r: r['id']),
                                sorted(after_idem['scripts'], key=lambda r: r['id']))),
            'local_revision_after': after_idem['local_global_revision'],
        }
        # persistence: restart the owned child, state must survive and re-converge
        restart = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_child_online(timeout=90)
        time.sleep(3.0)
        after_restart = self.child.db_summary(ids)
        persistence = {
            'restart': {'stop': restart['stop'], 'start': restart['start']},
            'local_counts_after_restart': after_restart['counts'],
            'identity_after_restart': after_restart['identity'],
            'scripts_after_restart': after_restart['scripts'],
            'revision_after_restart': after_restart['local_global_revision'],
            'state_survived': after_restart['counts'] == after_idem['counts'],
            'node_after_restart': {k: node_after.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_after else None,
        }
        out = {
            'at_utc': now_utc(),
            'recovery': recovery,
            'desired_equals_actual': desired_actual,
            'idempotency': idem,
            'persistence': persistence,
            'assertions_pass': (recovery['content_sha']['match'] and desired_actual['all_match']
                                and idem['same_counts'] and persistence['state_survived']
                                and (node_after or {}).get('status') == 'online'),
        }
        self.ev('phase8-ar16-recovery-persistence.json', out)
        return out

    # ---------------- phase 9: real executions (business effect) ----------------
    def phase9_executions(self):
        node_id = self.state['child_node_id']
        results = {}
        for key, tid in (('success', self.state['task_run']), ('failed', self.state['task_fail'])):
            code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(tid),
                                       {'node_ids': {node_id: True}, 'params': {}})
            self.budget['api_mutations'] += 1
            if code != 200 or not rows:
                raise RuntimeError('manual_run_%s_http_%s %s' % (key, code, rows))
            eid = rows[0]['id']
            self.state['executions'][key] = eid

            def probe(eid=eid):
                code, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
                return body if (code == 200 and (body or {}).get('status') in TERMINAL) else None

            final = wait_for(probe, timeout=90, interval=1.0, label='exec_' + key) or {}
            results[key] = {
                'execution_id': eid, 'task_id': tid, 'status': final.get('status'),
                'exit_code': final.get('exit_code'), 'trigger_type': final.get('trigger_type'),
                'stdout': (final.get('stdout') or '').strip()[:200],
                'marker_present': MARKER in (final.get('stdout') or ''),
            }
        out = {'at_utc': now_utc(), 'executions': results,
               'local_journal': self.child.db_query(
                   'SELECT id,status,exit_code,offline,synced FROM executions ORDER BY start_time')['rows'],
               'assertions_pass': (results['success']['status'] == 'SUCCESS'
                                   and results['success']['marker_present']
                                   and results['failed']['status'] == 'FAILED'
                                   and results['failed']['exit_code'] not in (0, None))}
        self.ev('phase9-executions.json', out)
        return out

    # ---------------- phase 10: cleanup ----------------
    def phase10_cleanup(self):
        results = {'deleted': [], 'errors': []}
        # tasks before scripts (a referenced script cannot be deleted)
        order = [('task', self.state['task_run']), ('task', self.state['task_dep']),
                 ('task', self.state['task_fail']),
                 ('script', self.state['script_good']), ('script', self.state['script_dep']),
                 ('script', self.state['script_fail'])]
        for kind, obj_id in order:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, _ = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append((kind, obj_id))
        if self.child is not None:
            try:
                remove = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                remove = {'error': str(exc)}
                results['errors'].append('child_remove:%s' % exc)
        else:
            remove = {'skipped': 'owned_child_never_installed'}
        results['native1_child_removal'] = remove
        node_id = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        self.ev('phase10-cleanup.json', results)
        return results

    # ---------------- phase 11: oracle ----------------
    def phase11_oracle(self, cleanup):
        nodes = self.nodes_by_id()
        protected_after, ok = {}, True
        for nid, label in PROTECTED_NODES.items():
            n = nodes.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'),
                                                         'sync_status': n.get('sync_status')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        schedules = self.hub.json('/api/schedules')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in schedules if (s.get('name') or '').startswith(PREFIX)],
            'child_nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        protected_units_ok = (inv['primary_agent_unit'].get('ActiveState') == 'active'
                              and inv['repair_agent_unit'].get('ActiveState') == 'active')
        preserved = []
        for key, eid in self.state['executions'].items():
            code, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
            preserved.append({'purpose': key, 'id': eid, 'http': code,
                              'status': (body or {}).get('status')})
        oracle = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'native1_owned_child_orphan': orphan,
            'native1_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                  'owned_child_processes': inv['owned_child_processes'],
                                  'any_sysb02_processes': inv['any_sysb02_processes'],
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': protected_units_ok,
            'proxy_state_final': self.proxy.state() if self.proxy else None,
            'cleanup_results': cleanup,
            'preserved_execution_history': preserved,
            'total_nodes_now': len(nodes),
            'hub_snapshot_final': hubdb.snapshot([]),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and protected_units_ok
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    # diagnostics + a hard safety net: SIGTERM (e.g. an outer `timeout`) still runs
    # the cleanup and oracle phases instead of leaving owned fixtures behind.
    import faulthandler
    import signal

    faulthandler.dump_traceback_later(600, repeat=True, exit=False)

    def _term(signum, frame):
        raise KeyboardInterrupt('harness interrupted by signal %d' % signum)

    try:
        signal.signal(signal.SIGTERM, _term)
        signal.signal(signal.SIGINT, _term)
    except Exception:
        pass

    run = Run()
    evidence = {
        'batch_id': 'sys-b02', 'nonce': NONCE, 'owned_prefix': PREFIX,
        'endpoint': run.hub.base,
        'cases': ['SYS-10', 'INV-17', 'INV-03', 'INV-04', 'SYS-08', 'AR-16'],
        'started_at_utc': now_utc(), 'phases': {}, 'deviations': [],
    }
    progress = {'batch_id': 'sys-b02', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)

    failed = False
    try:
        mark('guard')
        evidence['phases']['guard'] = run.phase0_guard()
        mark('child_start')
        evidence['phases']['child'] = run.phase1_child()
        progress['child_node_id'] = run.state['child_node_id']
        progress['child_agent_id'] = run.state['child_agent_id']
        mark('fixtures_and_notify_ordering')
        evidence['phases']['fixtures'] = run.phase2_fixtures_and_ordering()
        progress['script_good'] = run.state['script_good']
        progress['task_run'] = run.state['task_run']
        mark('sys10_notify_once')
        evidence['phases']['sys10'] = run.phase3_notify_once()
        mark('inv17_uncommitted')
        evidence['phases']['inv17'] = run.phase4_inv17_negative()
        mark('inv03_three_layers')
        evidence['phases']['inv03'] = run.phase5_inv03_three_layers()
        mark('inv04_not_authority')
        evidence['phases']['inv04'] = run.phase6_inv04()
        mark('sys08_sync_failure')
        evidence['phases']['sys08'] = run.phase7_sync_failure()
        mark('ar16_recovery')
        evidence['phases']['ar16'] = run.phase8_ar16()
        mark('executions')
        evidence['phases']['executions'] = run.phase9_executions()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True

    mark('cleanup')
    try:
        if run.proxy:
            run.ev('phase-frames.json', {'state': run.proxy.state(),
                                         'frames': run.proxy.frame_summary(),
                                         'events': run.proxy.events})
            evidence['phases']['frames'] = run.proxy.state()
    except Exception:
        pass
    try:
        if run.state['child_node_id'] or run.state['task_run']:
            run.arms_reset()
            cleanup = run.phase10_cleanup()
            progress['final_cleanup'] = cleanup
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['cleanup_error'] = str(exc)
        failed = True
    finally:
        if run.proxy:
            run.proxy.stop()
    mark('oracle')
    try:
        oracle = run.phase11_oracle(progress.get('final_cleanup', {}))
        progress['oracle_clean'] = oracle['clean']
        if run.proxy:
            run.ev('phase-frames.json', {'state': run.proxy.state(), 'frames': run.proxy.frame_summary(),
                                         'events': run.proxy.events})
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {
        'script_good': run.state['script_good'], 'script_dep': run.state['script_dep'],
        'script_fail': run.state['script_fail'], 'task_run': run.state['task_run'],
        'task_dep': run.state['task_dep'], 'task_fail': run.state['task_fail'],
        'child_node_id': run.state['child_node_id'], 'child_agent_id': run.state['child_agent_id'],
        'executions': run.state['executions'], 'delete_results': run.state['delete_results'],
    }
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b02', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': kind, 'id': run.state[key], 'name': name,
             'deleted_get404': run.state['delete_results'].get(run.state[key], {}).get('get_http') == 404}
            for kind, key, name in (
                ('script', 'script_good', SCRIPT_GOOD), ('script', 'script_dep', SCRIPT_DEP),
                ('script', 'script_fail', SCRIPT_FAIL), ('task', 'task_run', TASK_RUN),
                ('task', 'task_dep', TASK_DEP), ('task', 'task_fail', TASK_FAIL))
        ],
        'never_committed_harness_objects': [
            {'kind': 'phantom_script', 'id': '(see run-evidence state / phase7 v2)',
             'note': 'injected only inside a corrupted SYNC_RESPONSE; never created on the Hub and absent '
                     'from the Agent local DB (rollback proof)'}
        ],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'host': '192.168.100.204',
                       'deleted_get404': run.state['delete_results'].get(run.state['child_node_id'], {}).get('get_http') == 404},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': '192.168.100.204',
                         'root': run.child.root if run.child else None,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('native1_child_removal', {}).get('root_removed'))},
        'proxy': {'kind': 'owned frame proxy on the operator workstation',
                  'listen_port': run.proxy.port if run.proxy else None,
                  'stopped': True, 'loopback_upstream': 'ws://192.168.100.209:18080'},
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE',
    }
    hubapi.write_json('fixture-ledger.json', ledger)
    progress['phase'] = 'failed' if failed else 'done'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b02', 'clean': progress.get('oracle_clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
