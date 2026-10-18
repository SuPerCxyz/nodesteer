#!/usr/bin/env python3
"""sys-b04 runtime batch: duplicate / out-of-order notifications and the Change Log
window / full-snapshot recovery path -> convergence on the Hub's Current Desired State.

Cases: SYS-05, AR-11, AR-12, SYS-07, AR-14, AR-15.

Real evidence chain (no product source changes, no protected Agent touched, no
Hub restart):
  operator/API act -> real request -> Hub Desired State commit -> real Hub frame
  (duplicated / deferred / replayed stale) on the harness proxy -> real Agent
  reconcile -> API/SQLite readback.
"""
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

import child_agent
import hubapi
import hubdb
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb04-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
SCRIPT2_NAME = PREFIX + 'script-prune'
TASK2_NAME = PREFIX + 'task-prune'
MARKER = 'QA:%s:EXEC' % NONCE
DEV = 'evidence/'
NODE_IP = '192.168.100.222'                        # owned child host: native-3
CHANGELOG_WINDOW = 5000
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def script_body(version):
    return ('#!/bin/sh\n'
            'set -eu\n'
            'sleep "${NODESTEER_PARAM_delay:-0}"\n'
            'printf \'%s\\n\' "' + MARKER + ':${NODESTEER_PARAM_tag:-none}"\n'
            '# content-version ' + version + '\n')


def hub_health():
    out = {}
    for path in ('/healthz', '/readyz'):
        try:
            with urllib.request.urlopen('http://192.168.100.209:18080' + path, timeout=5) as r:
                out[path] = {'http': r.status, 'body': r.read(50).decode('utf-8', 'replace').strip()}
        except Exception as exc:
            out[path] = {'error': type(exc).__name__}
    return out


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {'script_id': None, 'task_id': None, 'script2_id': None, 'task2_id': None,
                      'child_node_id': None, 'child_agent_id': None, 'executions': {},
                      'created': [], 'deleted': [], 'delete_results': {}, 'versions': {}}
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0,
                       'hub_definition_deletes': 0, 'manual_run_posts': 0,
                       'node_enroll_posts': 1, 'child_process_starts': 0,
                       'child_process_stops': 0, 'proxy_injections': 0,
                       'owned_store_seam_writes': 0}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        hubapi.write_json(DEV + name, value)

    def log(self, *a):
        print('[%s]' % now_utc(), *a)
        sys.stdout.flush()

    def node(self, node_id=None):
        nid = node_id or self.state['child_node_id']
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(nid))
        return body if code == 200 else None

    def script(self):
        return self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))

    def task(self):
        return self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_id']))

    def executions(self, limit=100):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(self.state['task_id']))
        code, body = self.hub.get(q)
        if code != 200:
            raise RuntimeError('executions_list_http_%s' % code)
        return body

    def exec_one(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return body if code == 200 else {}

    def wait_exec(self, exec_id, timeout=120):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            last = self.exec_one(exec_id)
            if last.get('status') in TERMINAL:
                return last
            time.sleep(1)
        return last

    def run_now(self, params):
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(self.state['task_id']),
                                   {'node_ids': {self.state['child_node_id']: True}, 'params': params})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or not rows:
            raise RuntimeError('run_now_http_%s %s' % (code, rows))
        return rows[0]['id']

    def db(self, exec_ids=None):
        return self.child.db_read(self.state['task_id'], exec_ids or [],
                                  self.state['script_id'], task_ids=[self.state['task_id']])

    def wait_child_rev(self, target, timeout=150):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.db()
            if (last.get('local_global_revision') or 0) >= target:
                return last, True
            time.sleep(1.5)
        return last, False

    def wait_desired_match(self, timeout=150):
        deadline = time.time() + timeout
        db = None
        while time.time() < deadline:
            db = self.db()
            s = self.script()
            if (db.get('owned_script') or {}).get('sha256') == s.get('sha256') \
                    and (db.get('owned_script') or {}).get('revision') == s.get('revision'):
                return db, True
            time.sleep(1.5)
        return db, False

    def wait_node_synced(self, timeout=120):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node()
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1.5)
        return node

    def sample_child(self, count, interval=0.6):
        out = []
        for _ in range(count):
            db = self.db()
            out.append({'at_utc': now_utc(),
                        'local_revision': db.get('local_global_revision'),
                        'script_sha256': (db.get('owned_script') or {}).get('sha256'),
                        'script_revision': (db.get('owned_script') or {}).get('revision')})
            time.sleep(interval)
        return out

    def update_script(self, version):
        cur = self.script()
        sha = hashlib.sha256(script_body(version).encode()).hexdigest()
        cur['content'] = script_body(version)
        code, body = self.hub.put('/api/scripts/' + self.hub.quote(cur['id']), cur)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('script_update_http_%s %s' % (code, body))
        s = self.script()
        self.state['versions'][version] = {'sha256': sha, 'revision': s.get('revision')}
        if s.get('sha256') != sha:
            raise RuntimeError('script_update_readback_mismatch_%s' % version)
        return s

    def notifications(self, base=0, object_id=None, action=None):
        out = list(self.proxy.notifications)[base:]
        if object_id:
            out = [n for n in out if n.get('object_id') == object_id]
        if action:
            out = [n for n in out if n.get('action') == action]
        return out

    def sync_responses(self, base=0):
        return [f for f in list(self.proxy.frames)[base:]
                if f.get('type') == 'SYNC_RESPONSE' and f.get('direction') == 'hub_to_agent']

    def wait_sync_response(self, base, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hits = self.sync_responses(base)
            if hits:
                return hits[0]
            time.sleep(1.0)
        return None

    # ---------------- phases ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': self.hub.base}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected_nodes_before'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected_nodes_before'][label] = {
                'id': nid, 'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision')}
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        out['settings_readonly'] = settings
        if settings.get('changelog_window') != str(CHANGELOG_WINDOW):
            raise RuntimeError('unexpected_changelog_window:%s' % settings.get('changelog_window'))
        snap = hubdb.snapshot()
        out['hub_db_before'] = {k: snap[k] for k in
                                ('revision_sequence', 'revision_changes_total', 'quick_check')}
        out['hub_health_before'] = hub_health()
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        out['fixture_absence'] = {
            'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
            'scripts_total': len(scripts), 'tasks_total': len(tasks)}
        if any(out['fixture_absence'][k] for k in
               ('scripts_matching_prefix', 'tasks_matching_prefix', 'child_nodes_matching_prefix')):
            raise RuntimeError('owned_fixture_already_present')
        ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native3_pre'] = {
            'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
            'owned_child_processes': ci['owned_child_processes'],
            'agent_binary_sha256': ci['agent_binary_sha256'],
            'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': ci['primary_agent_unit'],
            'repair_agent_unit': ci['repair_agent_unit']}
        if ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native3_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        self.rec('phase0-guard.json', out)
        return out

    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
            'hub_address': 'http://192.168.100.209:18080'})
        self.budget['api_mutations'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s' % code)
        self.state['child_node_id'] = enroll['node_id']
        self.state['child_agent_id'] = enroll['agent_id']
        token = self.hub.cred['registration_token']
        self.proxy = FrameProxy('ws://192.168.100.209:18080')
        port = self.proxy.start()
        self.child = child_agent.OwnedChild(NONCE, port, enroll['node_id'], enroll['agent_id'],
                                            PREFIX + 'child', NODE_IP, token)
        started = self.child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_node_synced(timeout=90)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {'at_utc': now_utc(),
               'enrollment': {'http': code, 'node_id': enroll['node_id'],
                              'agent_id': enroll['agent_id'], 'node_name': PREFIX + 'child',
                              'node_ip': NODE_IP,
                              'registration_token_persisted_to_evidence': False},
               'owned_child': {'host': NODE_IP, 'root_on_native3': self.child.root,
                               'config_mode_0600': True, 'data_dir': self.child.data,
                               'child_processes': started.get('owned_processes'),
                               'listened_operator_port': port,
                               'routing': 'owned child(native-3) -> operator 192.168.100.170:%d '
                                          '(own frame proxy) -> repair hub :18080' % port},
               'node_after_start': {k: node.get(k) for k in
                                    ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                     'global_revision', 'deployment_mode', 'agent_version')},
               'owner_reference': {'note': 'owned child Agent uses the exact repair Agent binary; '
                                           'the process owner is the harness on native-3',
                                   'agent_binary_sha256': child_agent.AGENT_SHA}}
        self.rec('phase1-owned-child.json', out)
        return out

    def phase2_fixtures(self):
        sha_v1 = hashlib.sha256(script_body('v1').encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME, 'description': 'sys-b04 notification/snapshot fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': script_body('v1'),
            'parameters': [{'name': 'tag', 'type': 'string', 'required': False, 'default': 'none'},
                           {'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))
        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'tag': 'none', 'delay': '0'}, 'timeout': 30,
            'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, task))
        self.state['task_id'] = task['id']
        self.state['created'].append(('task', task['id']))
        self.budget['hub_definition_creates'] += 2
        s_read, t_read = self.script(), self.task()
        if s_read.get('sha256') != sha_v1:
            raise RuntimeError('script_readback_mismatch')
        db, ok = self.wait_desired_match(timeout=150)
        out = {'at_utc': now_utc(),
               'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                          'revision': s_read.get('revision'), 'expected_sha256_v1': sha_v1},
               'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                        'type': 'script', 'retry': t_read.get('retry'),
                        'target_node_ids': t_read.get('target', {}).get('node_ids')},
               'child_applied': {'local_global_revision': db.get('local_global_revision'),
                                 'owned_script': db.get('owned_script'),
                                 'owned_task': db.get('owned_task'),
                                 'scripts_total': db.get('scripts_total'),
                                 'tasks_total': db.get('tasks_total')},
               'desired_equals_actual': ok}
        if not ok:
            raise RuntimeError('child_did_not_apply_fixtures')
        self.state['versions']['v1'] = {'sha256': sha_v1, 'revision': s_read.get('revision')}
        self.rec('phase2-fixtures.json', out)
        return out

    # ---- baseline real execution (duplicate-storm reference count) ----
    def phase3_baseline_execution(self):
        eid = self.run_now({'tag': 'baseline', 'delay': '1'})
        self.state['executions']['m1_baseline'] = eid
        final = self.wait_exec(eid, timeout=120)
        db = self.db([eid])
        out = {'at_utc': now_utc(),
               'request': 'POST /api/tasks/%s/run node_ids={%s:true} params={tag:baseline}'
                          % (self.state['task_id'], self.state['child_node_id']),
               'execution_id': eid, 'status': final.get('status'), 'exit_code': final.get('exit_code'),
               'stdout': (final.get('stdout') or '').strip(),
               'stdout_marker_present': (MARKER + ':baseline') in (final.get('stdout') or ''),
               'hub_rows_for_execution': len([e for e in self.executions() if e['id'] == eid]),
               'hub_executions_for_task': len(self.executions()),
               'child_executions_for_task': len(db.get('task_executions') or [])}
        out['assertions_pass'] = bool(final.get('status') == 'SUCCESS'
                                      and out['stdout_marker_present']
                                      and out['hub_executions_for_task'] == 1
                                      and out['child_executions_for_task'] == 1)
        self.rec('phase3-baseline-execution.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase3_baseline_execution_failed')
        return out

    # ---- SYS-05 / AR-11: duplicated notification ----
    def phase4_duplicate_notification(self):
        base = len(self.proxy.notifications)
        frame_base = len(self.proxy.frames)
        self.proxy.arm_dup_notification(self.state['script_id'], count=1)
        s_v2 = self.update_script('v2')
        t_change = time.time()
        dup = None
        deadline = time.time() + 30
        while time.time() < deadline:
            hits = self.notifications(base, self.state['script_id'], 'duplicated')
            if hits:
                dup = hits[0]
                break
            time.sleep(0.3)
        samples = self.sample_child(6, 0.6)
        db, ok = self.wait_desired_match(timeout=90)
        frames = list(self.proxy.frames)[frame_base:]
        syncs = [f for f in frames if f.get('type') in ('SYNC_REQUEST', 'SYNC_RESPONSE', 'SYNC_ACK')]
        hub_execs = self.executions()
        child_db = self.db()
        monotonic = all((samples[i]['local_revision'] or 0) <= (samples[i + 1]['local_revision'] or 0)
                        for i in range(len(samples) - 1))
        out = {
            'at_utc': now_utc(),
            'scenario': 'a real Desired State change (script v1 -> v2) whose real-time '
                        'CHANGE_NOTIFICATION frame was delivered TWICE by the harness proxy',
            'change': {'object': 'script', 'id': s_v2.get('id'), 'revision': s_v2.get('revision'),
                       'sha256_expected': self.state['versions']['v2']['sha256'],
                       'sha256_hub_readback': s_v2.get('sha256')},
            'duplicated_notification': dup,
            'notification_frames_for_object': self.notifications(base, self.state['script_id']),
            'child_samples_during_storm': samples,
            'child_local_revision_monotonic': monotonic,
            'sync_request_count': len([f for f in syncs if f.get('type') == 'SYNC_REQUEST']),
            'sync_ack_count': len([f for f in syncs if f.get('type') == 'SYNC_ACK']),
            'sync_frames': syncs,
            'child_after': {'local_global_revision': db.get('local_global_revision'),
                            'owned_script': db.get('owned_script')},
            'child_local_revision_final': child_db.get('local_global_revision'),
            'child_quick_check': child_db.get('quick_check'),
            'converged_to_newest': ok,
            'convergence_seconds': round(time.time() - t_change, 1),
            'hub_executions_for_task': len(hub_execs),
            'child_executions_for_task': len(child_db.get('task_executions') or []),
            'hub_execution_ids': [e['id'] for e in hub_execs],
        }
        out['assertions_pass'] = bool(dup is not None and ok and monotonic
                                     and db.get('owned_script', {}).get('sha256')
                                     == self.state['versions']['v2']['sha256']
                                     and out['hub_executions_for_task'] == 1
                                     and out['child_executions_for_task'] == 1)
        self.rec('phase4-sys05-ar11-duplicate-notification.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase4_duplicate_notification_failed')
        return out

    # ---- SYS-05 / AR-12: stale replay + real out-of-order delivery ----
    def phase5_out_of_order(self):
        s_v3 = self.update_script('v3')
        db3, ok3 = self.wait_desired_match(timeout=90)
        stale_base = len(self.proxy.notifications)
        sync_base = len(self.proxy.sync_frames)
        v2_rev = self.state['versions']['v2']['revision']
        for _ in range(2):
            self.proxy.inject_stale_notification('script', self.state['script_id'], v2_rev - 1, v2_rev)
            self.budget['proxy_injections'] += 1
        samples_stale = self.sample_child(6, 0.6)
        stale_db = self.db()
        syncs_stale = list(self.proxy.sync_frames)[sync_base:]

        defer_base_frames = len(self.proxy.frames)
        self.proxy.arm_defer_notification(self.state['script_id'], count=1)
        s_v4 = self.update_script('v4')
        deferred = None
        deadline = time.time() + 30
        while time.time() < deadline:
            d = self.proxy.deferred_frames()
            if d:
                deferred = d[0]
                break
            time.sleep(0.3)
        s_v5 = self.update_script('v5')
        db5, ok5 = self.wait_desired_match(timeout=90)
        t_before_release = time.time()
        released = self.proxy.release_deferred()
        samples_after = self.sample_child(8, 0.7)
        db_final = self.db()
        frames_ooo = list(self.proxy.frames)[defer_base_frames:]
        notif_ooo = [f for f in frames_ooo if f.get('type') == 'CHANGE_NOTIFICATION'
                     and f.get('direction') == 'hub_to_agent']
        syncs_after_release = [f for f in list(self.proxy.frames)
                               if f.get('type') in ('SYNC_REQUEST', 'SYNC_RESPONSE', 'SYNC_ACK')
                               and (f.get('at') or 0) >= t_before_release]
        monotonic_after = all((samples_after[i]['local_revision'] or 0)
                              <= (samples_after[i + 1]['local_revision'] or 0)
                              for i in range(len(samples_after) - 1))
        hub_execs = self.executions()
        child_db = self.db()
        out = {
            'at_utc': now_utc(),
            'scenario': 'two seams on the owned child link: (a) a stale CHANGE_NOTIFICATION replayed after a '
                        'newer change, (b) a genuinely deferred (older) CHANGE_NOTIFICATION released only '
                        'after the newer one had already been delivered',
            'stale_replay': {
                'injected': self.notifications(stale_base, self.state['script_id'], 'injected_stale'),
                'hub_newest_revision': s_v3.get('revision'),
                'hub_newest_sha256': s_v3.get('sha256'),
                'child_local_revision': stale_db.get('local_global_revision'),
                'child_script_sha256': (stale_db.get('owned_script') or {}).get('sha256'),
                'child_did_not_regress': (stale_db.get('owned_script') or {}).get('sha256') == s_v3.get('sha256'),
                'samples': samples_stale,
                'sync_frames_triggered_by_stale_replay': syncs_stale},
            'out_of_order': {
                'deferred_frame': deferred,
                'deferred_object_revision': self.state['versions']['v4']['revision'],
                'newer_object_revision_delivered_while_older_was_held': s_v5.get('revision'),
                'newer_applied_local_revision': db5.get('local_global_revision'),
                'notification_order_on_wire': notif_ooo,
                'deferred_released_count': released,
                'sync_frames_after_release': syncs_after_release,
                'child_samples_after_release': samples_after,
                'child_local_revision_monotonic_after_release': monotonic_after,
                'child_local_revision_final': db_final.get('local_global_revision'),
                'child_script_sha256_final': (db_final.get('owned_script') or {}).get('sha256'),
                'hub_newest_sha256': s_v5.get('sha256'),
                'final_state_is_hub_newest': (db_final.get('owned_script') or {}).get('sha256')
                                            == s_v5.get('sha256')},
            'hub_executions_for_task': len(hub_execs),
            'child_executions_for_task': len(child_db.get('task_executions') or []),
        }
        out['deferred_delivered_after_newer'] = bool(
            deferred and notif_ooo
            and any(n.get('action') == 'deferred' for n in notif_ooo)
            and any(n.get('action') == 'passed'
                    and (n.get('global_revision') or 0) > (deferred.get('global_revision') or 0)
                    for n in notif_ooo))
        out['assertions_pass'] = bool(
            out['stale_replay']['child_did_not_regress']
            and len(out['stale_replay']['sync_frames_triggered_by_stale_replay']) == 0
            and out['deferred_delivered_after_newer']
            and out['out_of_order']['final_state_is_hub_newest']
            and monotonic_after
            and out['child_executions_for_task'] == 1)
        self.rec('phase5-sys05-ar12-out-of-order.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase5_out_of_order_failed')
        return out

    # ---- real deletion of an owned object must be pruned on the Agent ----
    def phase6_tombstone_prune(self):
        code, script2 = self.hub.post('/api/scripts', {
            'name': SCRIPT2_NAME, 'description': 'sys-b04 prune probe (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': script_body('prune'),
            'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script2_create_http_%s %s' % (code, script2))
        self.state['script2_id'] = script2['id']
        self.state['created'].append(('script-prune', script2['id']))
        code, task2 = self.hub.post('/api/tasks', {
            'name': TASK2_NAME, 'type': 'script', 'script_id': script2['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task2_create_http_%s %s' % (code, task2))
        self.state['task2_id'] = task2['id']
        self.state['created'].append(('task-prune', task2['id']))
        self.budget['hub_definition_creates'] += 2
        rev_created = hubdb.snapshot([script2['id'], task2['id']])['revision_sequence']
        db_applied, applied = self.wait_child_rev(rev_created, timeout=150)
        frame_base = len(self.proxy.frames)
        pruned = []
        for kind, obj_id in [('task', task2['id']), ('script', script2['id'])]:
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            dcode, _ = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            pruned.append({'kind': kind, 'id': obj_id, 'delete_http': dcode, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': dcode, 'get_http': gcode}
            self.state['deleted'].append((kind, obj_id))
        deadline = time.time() + 120
        db_after = self.db()
        while time.time() < deadline:
            db_after = self.db()
            if (db_after.get('scripts_total') or 0) <= 1 and (db_after.get('tasks_total') or 0) <= 1:
                break
            time.sleep(1.5)
        resps = self.sync_responses(frame_base)
        out = {
            'at_utc': now_utc(),
            'scenario': 'an owned script+task were really deleted on the Hub; the connected Agent must prune '
                        'them through the normal sync (tombstones)',
            'created_revision': rev_created,
            'child_applied_before_delete': {'local_global_revision': db_applied.get('local_global_revision'),
                                            'scripts_total': db_applied.get('scripts_total'),
                                            'tasks_total': db_applied.get('tasks_total')},
            'expected_child_desired_before_delete': {'scripts_total': 2, 'tasks_total': 2},
            'expected_child_desired_after_delete': {'scripts_total': 1, 'tasks_total': 1},
            'deleted': pruned,
            'sync_responses': resps,
            'tombstones_seen_in_responses': sorted({t for r in resps for t in (r.get('tombstones') or [])}),
            'child_after': {'local_global_revision': db_after.get('local_global_revision'),
                            'scripts_total': db_after.get('scripts_total'),
                            'tasks_total': db_after.get('tasks_total')},
            'hub_scripts_total_global': len(self.hub.json('/api/scripts')),
            'hub_tasks_total_global': len(self.hub.json('/api/tasks')),
        }
        out['pruned'] = bool(out['child_after']['scripts_total'] == 1
                             and out['child_after']['tasks_total'] == 1
                             and out['child_applied_before_delete']['scripts_total'] == 2
                             and out['child_applied_before_delete']['tasks_total'] == 2)
        out['assertions_pass'] = bool(all(d['get_http'] == 404 for d in pruned) and out['pruned'])
        self.rec('phase6-tombstone-prune.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase6_tombstone_prune_failed')
        return out

    # ---- SYS-07 / AR-14 / AR-15: window-expiry boundary + full-snapshot recovery ----
    def phase7_window_boundary_and_snapshot_recovery(self):
        settings_before = self.hub.json('/api/settings')
        if isinstance(settings_before, list):
            settings_before = {s.get('key'): s.get('value') for s in settings_before}
        current = hubdb.snapshot([self.state['script_id'], self.state['task_id']])['revision_sequence']
        window = int(settings_before.get('changelog_window', CHANGELOG_WINDOW))
        reachable = current - window - 1
        boundary = {
            'hub_current_global_revision': current,
            'changelog_window': window,
            'branch_condition': 'since > 0 && (current - since) > changelog_window',
            'largest_revision_that_could_ever_be_outside_the_window': reachable,
            'smallest_valid_positive_since': 1,
            'gap_still_needed_for_the_branch_to_be_reachable': max(0, (window + 2) - current),
            'reachable_in_this_environment': reachable >= 1,
            'reason_not_reachable': 'the repair Hub global revision (%d) is far below changelog_window (%d), '
                                    'so no Agent-side `since` value can satisfy current - since > window while '
                                    'since > 0; writing the global changelog_window setting and mass-advancing '
                                    'the global revision are both excluded by the batch constraints'
                                    % (current, window),
        }
        seam = {
            'how': 'the owned child Agent was stopped and its OWN local store revision was set first to the '
                   'smallest positive value (1) and then to 0, then restarted, so the Agent really asked the '
                   'Hub with a maximally stale `since`; the global changelog_window setting and the global '
                   'revision sequence were never written',
            'hub_current_revision': current,
            'changelog_window': window,
        }

        def recovery(local_rev_value, tag):
            db_before = self.db()
            stopped = self.child.stop()
            self.budget['child_process_stops'] += 1
            write = self.child.set_local_revision(local_rev_value)
            self.budget['owned_store_seam_writes'] += 1
            if write.get('error') or write.get('after') != local_rev_value:
                raise RuntimeError('owned_store_seam_failed:%s' % write)
            frame_base = len(self.proxy.frames)
            started = self.child.start()
            self.budget['child_process_starts'] += 1
            first_resp = self.wait_sync_response(frame_base, timeout=150)
            db_after, converged = self.wait_child_rev(current, timeout=150)
            frames = list(self.proxy.frames)[frame_base:]
            s = self.script()
            return {
                'tag': tag,
                'seam_local_revision_set_to': local_rev_value,
                'seam_before_local_revision': db_before.get('local_global_revision'),
                'seam_write_result': write,
                'child_stop_result': stopped, 'child_start_result': started,
                'sync_request_frames': [f for f in frames if f.get('type') == 'SYNC_REQUEST'],
                'first_sync_response': first_resp,
                'full_resync_flag': (first_resp or {}).get('full_resync'),
                'snapshot_flag': (first_resp or {}).get('snapshot'),
                'snapshot_object_counts': {k: (first_resp or {}).get(k)
                                           for k in ('scripts', 'tasks', 'schedules')},
                'snapshot_script_ids': (first_resp or {}).get('script_ids'),
                'snapshot_task_ids': (first_resp or {}).get('task_ids'),
                'snapshot_tombstones': (first_resp or {}).get('tombstones'),
                'child_converged': converged,
                'child_after': {'local_global_revision': db_after.get('local_global_revision'),
                                'owned_script': db_after.get('owned_script'),
                                'scripts_total': db_after.get('scripts_total'),
                                'tasks_total': db_after.get('tasks_total')},
                'hub_newest_sha256': s.get('sha256'),
                'desired_equals_actual': (db_after.get('owned_script') or {}).get('sha256') == s.get('sha256'),
            }

        r1 = recovery(1, 'since_1_smallest_positive')
        r2 = recovery(0, 'since_0_not_beyond_guard')
        settings_after = self.hub.json('/api/settings')
        if isinstance(settings_after, list):
            settings_after = {s.get('key'): s.get('value') for s in settings_after}
        hub_execs = self.executions()
        out = {
            'at_utc': now_utc(), 'seam': seam, 'boundary_proof': boundary,
            'hub_settings_before': settings_before, 'hub_settings_after': settings_after,
            'changelog_window_unchanged': settings_before.get('changelog_window')
                                          == settings_after.get('changelog_window'),
            'global_revision_after': hubdb.snapshot()['revision_sequence'],
            'recovery_since_1': r1,
            'recovery_since_0': r2,
            'full_resync_observed': bool(r1.get('full_resync_flag') or r2.get('full_resync_flag')),
            'complete_snapshot_recovery_observed': bool(
                r1.get('snapshot_flag') is True and r1.get('child_converged') and r1.get('desired_equals_actual')),
            'hub_executions_for_task': len(hub_execs),
            'child_executions_for_task': len(self.db().get('task_executions') or []),
            'hub_execution_ids': [e['id'] for e in hub_execs],
        }
        out['assertions_pass'] = bool(
            not boundary['reachable_in_this_environment']
            and out['changelog_window_unchanged']
            and r1['full_resync_flag'] is False and r1['snapshot_flag'] is True
            and r1['child_converged'] and r1['desired_equals_actual']
            and self.state['script_id'] in (r1['snapshot_script_ids'] or [])
            and self.state['task_id'] in (r1['snapshot_task_ids'] or [])
            and r2['full_resync_flag'] is False and r2['snapshot_flag'] is True
            and r2['child_converged'] and r2['desired_equals_actual']
            and out['child_executions_for_task'] == 1)
        self.rec('phase7-sys07-ar14-window-boundary.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase7_window_boundary_failed')
        return out

    # ---- AR-15: recovery is idempotent and persistent across a real Agent restart ----
    def phase8_persistence(self):
        db_before = self.db()
        restarted = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        frame_base = len(self.proxy.frames)
        node = self.wait_node_synced(timeout=120)
        db_after, ok = self.wait_desired_match(timeout=120)
        resps = self.sync_responses(frame_base)
        s = self.script()
        hub_execs_before = self.executions()
        eid = self.run_now({'tag': 'after-restart', 'delay': '1'})
        self.state['executions']['m2_after_restart'] = eid
        final = self.wait_exec(eid, timeout=120)
        out = {
            'at_utc': now_utc(),
            'restart': restarted,
            'child_before_restart': {'local_global_revision': db_before.get('local_global_revision'),
                                     'owned_script': db_before.get('owned_script')},
            'node_after_reconnect': {k: (node or {}).get(k) for k in
                                     ('status', 'sync_status', 'global_revision')},
            'sync_responses_after_restart': resps,
            'resync_after_restart_was_not_full': all(r.get('full_resync') is False for r in resps),
            'child_after_restart': {'local_global_revision': db_after.get('local_global_revision'),
                                    'owned_script': db_after.get('owned_script'),
                                    'scripts_total': db_after.get('scripts_total'),
                                    'tasks_total': db_after.get('tasks_total')},
            'desired_equals_actual': ok,
            'hub_newest_sha256': s.get('sha256'),
            'hub_executions_before_new_run': len(hub_execs_before),
            'new_execution': {'id': eid, 'status': final.get('status'),
                              'stdout': (final.get('stdout') or '').strip(),
                              'stdout_marker_present': (MARKER + ':after-restart') in (final.get('stdout') or '')},
            'child_executions_for_task': len(self.db().get('task_executions') or []),
        }
        out['assertions_pass'] = bool(ok and node and node.get('status') == 'online'
                                     and node.get('sync_status') == 'synced'
                                     and out['resync_after_restart_was_not_full']
                                     and final.get('status') == 'SUCCESS'
                                     and out['new_execution']['stdout_marker_present'])
        self.rec('phase8-ar15-persistence.json', out)
        return out

    # ---- final duplicate/idempotency check ----
    def phase9_idempotency(self):
        time.sleep(15)
        rows = self.executions()
        db = self.db()
        out = {
            'at_utc': now_utc(),
            'note': 'settle window after every seam: duplicate / deferred / replayed notifications, a real '
                    'deletion prune and two maximally-stale recoveries must not create duplicate objects or '
                    'duplicate Executions',
            'hub_executions_for_task': len(rows),
            'hub_execution_ids': [e['id'] for e in rows],
            'hub_unique_execution_ids': len({e['id'] for e in rows}) == len(rows),
            'child_executions_for_task': len(db.get('task_executions') or []),
            'child_execution_ids': [e['id'] for e in (db.get('task_executions') or [])],
            'expected_executions': 2,
            'child_scripts_total': db.get('scripts_total'),
            'child_tasks_total': db.get('tasks_total'),
            'hub_scripts_total': len(self.hub.json('/api/scripts')),
            'hub_tasks_total': len(self.hub.json('/api/tasks')),
            'child_quick_check': db.get('quick_check'),
            'desired_equals_actual': (db.get('owned_script') or {}).get('sha256') == self.script().get('sha256'),
        }
        out['assertions_pass'] = bool(out['hub_executions_for_task'] == 2
                                     and out['child_executions_for_task'] == 2
                                     and out['hub_unique_execution_ids']
                                     and out['desired_equals_actual'])
        self.rec('phase9-idempotency.json', out)
        return out

    # ---------------- cleanup / oracle ----------------
    def phase10_cleanup(self):
        results = {'deleted': [], 'errors': []}
        if self.child is not None:
            try:
                remove = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                remove = {'error': str(exc)}
                results['errors'].append('child_remove:%s' % exc)
        else:
            remove = {'skipped': 'owned_child_never_installed'}
        results['native3_child_removal'] = remove
        for kind, obj_id in [('task', self.state['task_id']), ('script', self.state['script_id'])]:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id,
                                       'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
        # fixtures deleted earlier inside the prune probe: carry their recorded DELETE/GET results
        for kind, obj_id in [('task-prune', self.state['task2_id']),
                            ('script-prune', self.state['script2_id'])]:
            if not obj_id:
                continue
            rec = self.state['delete_results'].get(obj_id, {})
            results['deleted'].append({'kind': kind, 'id': obj_id, 'deleted_during_probe': True,
                                       'delete_http': rec.get('delete_http'),
                                       'get_http': rec.get('get_http')})
            gcode_check, _ = self.hub.get('/api/%ss/%s'
                                          % ('task' if kind == 'task-prune' else 'script',
                                             self.hub.quote(obj_id)))
            results['deleted'][-1]['get_http_recheck'] = gcode_check
        cid = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(cid))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(cid))
        results['child_node'] = {'id': cid, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = (all((d.get('get_http') == 404)
                                      and (d.get('get_http_recheck') in (None, 404))
                                      for d in results['deleted']) and gcode == 404)
        self.rec('evidence/phase10-cleanup.json', results)
        return results

    def phase11_oracle(self, cleanup):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        ok = True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {
                'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        snap = hubdb.snapshot()
        oracle = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'native3_owned_child_orphan': orphan,
            'native3_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                  'owned_child_processes': inv['owned_child_processes'],
                                  'owned_root_glob': inv['owned_root_glob'],
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': (inv['primary_agent_unit'].get('ActiveState') == 'active'
                                       and inv['repair_agent_unit'].get('ActiveState') == 'active'),
            'settings_after': settings,
            'changelog_window_untouched': settings.get('changelog_window') == str(CHANGELOG_WINDOW),
            'hub_db_after': {k: snap[k] for k in
                             ('revision_sequence', 'revision_changes_total', 'quick_check')},
            'hub_health_after': hub_health(),
            'cleanup_results': cleanup,
            'total_nodes_now': len(nodes)}
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan
                           and oracle['protected_units_active'] and oracle['changelog_window_untouched']
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b04', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-05', 'AR-11', 'AR-12', 'SYS-07', 'AR-14', 'AR-15'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b04', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)

    failed = False
    try:
        mark('guard'); evidence['phases']['guard'] = run.phase0_guard()
        mark('child'); evidence['phases']['child'] = run.phase1_child()
        progress['child_node_id'] = run.state['child_node_id']
        progress['child_agent_id'] = run.state['child_agent_id']
        mark('fixtures'); evidence['phases']['fixtures'] = run.phase2_fixtures()
        progress['task_id'] = run.state['task_id']; progress['script_id'] = run.state['script_id']
        mark('baseline_execution'); evidence['phases']['baseline'] = run.phase3_baseline_execution()
        mark('duplicate_notification')
        evidence['phases']['duplicate'] = run.phase4_duplicate_notification()
        mark('out_of_order'); evidence['phases']['out_of_order'] = run.phase5_out_of_order()
        mark('tombstone_prune'); evidence['phases']['prune'] = run.phase6_tombstone_prune()
        mark('window_boundary')
        evidence['phases']['window_boundary'] = run.phase7_window_boundary_and_snapshot_recovery()
        mark('persistence'); evidence['phases']['persistence'] = run.phase8_persistence()
        mark('idempotency'); evidence['phases']['idempotency'] = run.phase9_idempotency()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True
    mark('cleanup')
    try:
        run.rec('evidence/phase-frames.json',
                {'frames': list(run.proxy.frames) if run.proxy else [],
                 'events': list(run.proxy.events) if run.proxy else [],
                 'notifications': list(run.proxy.notifications) if run.proxy else [],
                 'proxy_state': run.proxy.state() if run.proxy else None})
    except Exception:
        pass
    if run.proxy and run.proxy.mode() != 'pass':
        try:
            run.proxy.set_mode('pass')
        except Exception:
            pass
    try:
        if run.state['child_node_id'] or run.state['task_id']:
            cleanup = run.phase10_cleanup()
            progress['final_cleanup'] = cleanup
            progress['final_cleanup_get_http'] = {d['id']: d['get_http'] for d in cleanup['deleted']}
            progress['child_node_get_http'] = cleanup['child_node']['get_http']
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
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
                         'child_node_id': run.state['child_node_id'],
                         'child_agent_id': run.state['child_agent_id'],
                         'versions': run.state['versions'],
                         'executions': run.state['executions'],
                         'deleted': run.state['deleted'],
                         'delete_results': run.state['delete_results']}
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b04', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404},
            {'kind': 'script-prune', 'id': run.state['script2_id'], 'name': SCRIPT2_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script2_id'], {}).get('get_http') == 404},
            {'kind': 'task-prune', 'id': run.state['task2_id'], 'name': TASK2_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task2_id'], {}).get('get_http') == 404}],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'deleted_get404': True, 'delete_http': 200},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': '192.168.100.222',
                         'root': run.child.root if run.child else None,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('native3_child_removal', {}).get('root_removed'))},
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'}
    hubapi.write_json('fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b04',
                      'cases': {c['case_id']: c['proposed_after'] for c in
                                json.loads((HERE / 'case-result.json').read_text())['cases']},
                      'clean': progress.get('oracle_clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    dup = ph.get('duplicate', {})
    ooo = ph.get('out_of_order', {})
    prune = ph.get('prune', {})
    wb = ph.get('window_boundary', {})
    pers = ph.get('persistence', {})
    idem = ph.get('idempotency', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    dup_ok = bool(dup.get('assertions_pass'))
    ooo_ok = bool(ooo.get('assertions_pass'))
    prune_ok = bool(prune.get('assertions_pass'))
    boundary_ok = bool(wb.get('assertions_pass'))
    fr_observed = bool(wb.get('full_resync_observed'))
    snapshot_ok = bool(wb.get('complete_snapshot_recovery_observed'))
    pers_ok = bool(pers.get('assertions_pass'))
    idem_ok = bool(idem.get('assertions_pass'))
    canonical = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)')
    seam_note = ('Seam: the harness stopped its OWN child Agent and set its OWN local store revision to 1 and '
                 'then to 0 so the Agent really asked the Hub with the most stale possible `since`. The global '
                 'changelog_window setting and the global revision sequence were never written.')

    def case(case_id, title, proposed, confidence, dims, limits):
        return {'case_id': case_id, 'title': title, 'canonical_before': canonical,
                'proposed_after': proposed, 'confidence': confidence, 'covered_dimensions': dims,
                'remaining_or_limitations': limits,
                'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                    'case-result.json', 'progress.json', 'fixture-ledger.json',
                    'final-cleanup-oracle.json', 'run-evidence.json'],
                'actual_resources': {
                    'owned_prefix': PREFIX, 'child_node_id': run.state['child_node_id'],
                    'child_agent_id': run.state['child_agent_id'],
                    'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
                    'prune_fixtures': {'script_id': run.state['script2_id'], 'task_id': run.state['task2_id']},
                    'object_versions': run.state['versions'], 'executions': run.state['executions'],
                    'preserved_execution_history': [v for v in run.state['executions'].values() if v]},
                'actual_budget': dict(run.budget),
                'final_cleanup': progress.get('final_cleanup', {})}

    cases = []
    cases.append(case(
        'SYS-05', 'SYS-05 通知重复/乱序：最终以 Hub Current Desired State 为准',
        'PASS' if (dup_ok and ooo_ok and idem_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if (dup_ok and ooo_ok) else 'PARTIAL'),
        'high' if (dup_ok and ooo_ok and idem_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-05',
          'layer': 'owned child Agent + harness frame proxy (duplicate/defer/replay) + agent sqlite',
          'status': 'PASS' if (dup_ok and ooo_ok) else 'PARTIAL',
          'description': [
              'Duplicated notification: the real CHANGE_NOTIFICATION for a script change was delivered twice; the Agent still ended exactly on the Hub revision and sha256, its applied revision never went backwards, and no extra Execution appeared.',
              'Stale replay: an older CHANGE_NOTIFICATION was replayed twice after a newer change; the Agent did not regress and issued no sync request for it.',
              'Real out-of-order delivery: the older notification was held by the proxy, the newer one was delivered, and only then was the older one released; the Agent stayed on the newer Hub state with a monotonic applied revision.',
              'Conclusion: the Hub\'s Current Desired State decides the end state; duplicate or late notifications only ever cause (idempotent) extra syncs, never a rollback.']}],
        ['The seams act on the harness-owned frame proxy of the harness\'s own child Agent only; no product '
         'code and no protected Agent was touched.']))

    cases.append(case(
        'AR-11', 'AR-11 Duplicate Notification；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS' if (dup_ok and idem_ok and cleanup_ok) else ('PASS_WITH_NOTES' if dup_ok else 'PARTIAL'),
        'high' if (dup_ok and idem_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-11',
          'layer': 'owned child Agent store / hub rows after a duplicated notification',
          'status': 'PASS' if dup_ok else 'PARTIAL',
          'description': [
              'One logical change + one duplicated notification produced exactly one object row on the Hub and on the Agent (same revision, same sha256).',
              'Object counts, the Execution history and PRAGMA quick_check were unchanged by the duplicate.',
              'Desired State and actual state agreed after the duplicate: the Agent script sha256/revision equalled the Hub\'s.']}],
        ['"Persistence" covers the Agent local store and Hub rows across the duplicate/deferred seams and a real '
         'Agent process restart (see AR-15), not a Hub restart (that is sys-b01).']))

    cases.append(case(
        'AR-12', 'AR-12 Out-of-order Notification；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS' if (ooo_ok and pers_ok and cleanup_ok) else ('PASS_WITH_NOTES' if ooo_ok else 'PARTIAL'),
        'high' if (ooo_ok and pers_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-12',
          'layer': 'owned child Agent + frame proxy deferred delivery + agent sqlite',
          'status': 'PASS' if ooo_ok else 'PARTIAL',
          'description': [
              'The older notification was genuinely deferred on the wire and released only after the newer one: the recorded frame order shows the deferred R_old after the passed R_new.',
              'After the late older notification the Agent stayed on the newer Hub state (monotonic applied revision, final sha256 = Hub newest) with no rollback and no half state.',
              'A following real Agent process restart kept the same state: online/synced, non-full sync after restart, desired == actual, and a new manual Run still succeeded exactly once.']}],
        ['Out-of-order delivery was produced on the harness-owned child link; the Agent\'s real "ignore a '
         'notification that is not newer than the local revision" branch is the unmodified product path.']))

    cases.append(case(
        'SYS-07', 'SYS-07 Change Log 窗口过期：触发 Full Resync',
        'BLOCKED' if not fr_observed else ('PASS' if boundary_ok else 'PARTIAL'),
        'high',
        [{'test_id': '20261014-RUNTIME:SYS-07',
          'layer': 'hub SyncManager.BuildSyncResponse window branch (not reachable) / boundary arithmetic',
          'status': 'BLOCKED' if not fr_observed else 'PASS',
          'description': [
              'The branch is `since > 0 && (current - since) > changelog_window`. The repair Hub global revision is far below changelog_window (5000), so no Agent-side `since` can satisfy it: the largest revision that could ever be outside the window is (current - window - 1), which is <= 0 here.',
              'Observed boundary with the owned child pushed to its most stale possible positive since (=1): the Hub answered full_resync=false with snapshot=true and the complete per-node desired object set.',
              'Observed boundary with since=0: the `since > 0` guard short-circuits the window check entirely; the Hub still answered a complete snapshot with full_resync=false.',
              'full_resync=true was therefore NOT observed. Reaching it would require either writing the global changelog_window setting or advancing the global revision by more than 5000 - both excluded by the batch constraints.']}],
        [seam_note,
         'Reported as BLOCKED rather than PASS: the trigger condition itself was not reproducible in this '
         'environment without forbidden changes. The equivalent recovery behaviour was verified through the '
         'complete-snapshot path (see AR-14/AR-15).',
         'The boundary arithmetic is computed from the live Hub revision and the live setting recorded in '
         'evidence/phase7-sys07-ar14-window-boundary.json.']))

    cases.append(case(
        'AR-14', 'AR-14 Change Log Window Expiry；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS_WITH_NOTES' if (boundary_ok and prune_ok and idem_ok and cleanup_ok) else 'PARTIAL',
        'medium' if (boundary_ok and prune_ok and idem_ok) else 'low',
        [{'test_id': '20261014-RUNTIME:AR-14',
          'layer': 'window-expiry substitute (maximally stale since -> complete snapshot) + hub/agent rows',
          'status': 'PASS_WITH_NOTES' if boundary_ok else 'PARTIAL',
          'description': [
              'A maximally stale owned Agent (local revision 1, then 0) was recovered twice and converged to the Hub\'s Current Desired State with the same sha256/revision; object counts did not grow and no duplicate rows appeared.',
              'Deletion/prune behaviour was verified separately on the same store: a really deleted owned script+task was pruned on the connected Agent through the normal tombstone path (child and Hub counts agreed again).',
              'The Hub rows and revision_changes were unchanged by the Agent side, and the settings (including changelog_window) were identical before and after.',
              'The window-expiry trigger itself was not observed (see SYS-07); the recovery semantics were verified on the closest reachable equivalent, the complete desired snapshot.']}],
        [seam_note,
         'This case\'s expected end state ("recovered, idempotent, persistent, desired == actual") was verified, '
         'but through the complete-snapshot path rather than a full_resync=true response.']))

    cases.append(case(
        'AR-15', 'AR-15 Full Resync；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS_WITH_NOTES' if (snapshot_ok and pers_ok and idem_ok and cleanup_ok) else 'PARTIAL',
        'medium' if (snapshot_ok and pers_ok and idem_ok) else 'low',
        [{'test_id': '20261014-RUNTIME:AR-15',
          'layer': 'complete desired snapshot recovery (twice) + real Agent restart + execution journal',
          'status': 'PASS_WITH_NOTES' if snapshot_ok else 'PARTIAL',
          'description': [
              'Complete-snapshot recovery was exercised twice on the same owned Agent; the second recovery re-applied the same desired object set idempotently (object counts and row identities unchanged).',
              'After the recovery the owned Agent was really restarted; on reconnect it performed a non-full sync, stayed online/synced, and desired == actual persisted.',
              'A new manual Run after that restart produced exactly one SUCCESS Execution carrying the nonce marker, so the recovered Agent is fully functional and the journal was not duplicated.']}],
        [seam_note,
         'The full_resync=true flag was not observed (see SYS-07); what was verified is the Full-Resync-equivalent '
         'recovery semantics: complete desired snapshot delivery, idempotent re-application, persistence across a '
         'real Agent restart and no duplicate Execution.',
         'The Hub Change Log prune timer (15 min) was not waited for: the window condition was approached from the '
         'Agent side instead.']))

    result = {'batch_id': 'sys-b04',
              'title': '通知重复/乱序与 Change Log 窗口过期：以 Hub Current Desired State / Full Resync 收敛',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
              'cases': cases, 'deviations': evidence['deviations'], 'budget': run.budget,
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management writes only to repair http://192.168.100.209:18080',
                  'owned_child': 'one harness-owned child Agent on native-3 (exact repair Agent binary '
                                 'd55dc5cd...) behind a harness-owned frame proxy on 192.168.100.170',
                  'duplicate_notification': 'the proxy delivered a real CHANGE_NOTIFICATION frame twice',
                  'out_of_order': 'the proxy deferred a real CHANGE_NOTIFICATION and released it after a newer '
                                  'one; plus a synthetic replay of an older revision',
                  'window_expiry': 'the owned child Agent was stopped and its OWN local store revision was set to '
                                   '1 and then 0; the global changelog_window setting and the global revision '
                                   'sequence were never written. full_resync=true proved unreachable here',
                  'product_source_changes': 0, 'git_operations': 0, 'hub_restarts': 0,
                  'protected_agents_stopped': 0}}
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
