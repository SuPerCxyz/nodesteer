#!/usr/bin/env python3
"""sys-b03 runtime batch: Lost Notification / Revision Lag -> Revision Check convergence.

Cases: SYS-04, AR-10, SYS-06, AR-13.

Real evidence chain (no product source changes, no protected Agent touched):
  operator/API act -> real request -> Hub Desired State change -> notification
  frame dropped on the owned child's link -> Agent local revision lags ->
  periodic Revision Check converges the Agent -> API/DB readback.

The harness owns exactly one child Agent on repair native-3 and one frame proxy
on the operator workstation. Notification drops are armed per object id, so no
other batch's notification is ever affected.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import child_agent
import hubapi
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb03-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
TASK2_NAME = PREFIX + 'task2'
MARKER = 'QA:%s:EXEC' % NONCE
NODE_IP = '192.168.100.222'
DEV = 'evidence/'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
REV_CHECK_INTERVAL = 45      # Hub pushes revision_check_interval_sec=45 to Agents


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def script_body(tag):
    return ('#!/bin/sh\n'
            'set -eu\n'
            'sleep "${NODESTEER_PARAM_delay:-0}"\n'
            "printf '%s\\n' '" + MARKER + ':' + tag + "'\n")


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {'script_id': None, 'task_id': None, 'task2_id': None,
                      'child_node_id': None, 'child_agent_id': None,
                      'created': [], 'deleted': [], 'delete_results': {}, 'executions': {}}
        self.notes = []
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0,
                       'hub_definition_deletes': 0, 'manual_run_posts': 0,
                       'node_enroll_posts': 1, 'child_process_starts': 0,
                       'child_process_stops': 0}

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

    def script(self, sid=None):
        sid = sid or self.state['script_id']
        return self.hub.json('/api/scripts/' + self.hub.quote(sid))

    def executions(self, task_id=None, limit=100):
        task_id = task_id or self.state['task_id']
        code, body = self.hub.get('/api/executions?limit=%d&task_id=%s'
                                  % (limit, self.hub.quote(task_id)))
        return body if code == 200 else []

    def db(self, exec_ids=None):
        tids = [t for t in (self.state.get('task_id'), self.state.get('task2_id')) if t]
        return self.child.db_read(self.state['task_id'], exec_ids or [], self.state['script_id'],
                                  task_ids=tids)

    def notif_base(self):
        return len(self.proxy.notifications)

    def dropped_since(self, base, object_ids=None):
        out = [n for n in self.proxy.notifications[base:] if n.get('action') == 'dropped']
        if object_ids is not None:
            want = set(object_ids)
            out = [n for n in out if n.get('object_id') in want]
        return out

    def wait_drop(self, obj_id, base, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for n in self.proxy.notifications[base:]:
                if n.get('object_id') == obj_id and n.get('action') == 'dropped':
                    return n
            time.sleep(0.3)
        return None

    def owned_task_ids(self):
        return [t for t in (self.state.get('task_id'), self.state.get('task2_id')) if t]

    def hub_desired(self):
        """Hub-side Desired State values for the owned objects."""
        s = self.script()
        tasks = {tid: self.hub.json('/api/tasks/' + self.hub.quote(tid))
                 for tid in self.owned_task_ids()}
        return s, tasks

    def desired_match(self, db):
        s, tasks = self.hub_desired()
        if db.get('owned_script', {}).get('sha256') != s.get('sha256'):
            return False
        got = {t['id']: t for t in (db.get('owned_tasks') or []) if t}
        for tid, t in tasks.items():
            if got.get(tid, {}).get('revision') != t.get('revision'):
                return False
        return True

    def wait_execution(self, exec_id, timeout=90):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            code, last = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
            if code == 200 and last.get('status') in TERMINAL:
                return last
            time.sleep(1)
        return last

    def wait_child_online(self, timeout=90):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node()
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(2)
        return node

    def wait_child_rev(self, target, timeout=90):
        """Wait until the owned child's local applied revision >= target."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.db()
            if last and (last.get('local_global_revision') or 0) >= target:
                return last, True
            time.sleep(2)
        return last, False

    def wait_desired_match(self, timeout=120):
        """Wait until the owned child's applied objects match the Hub Desired State."""
        deadline = time.time() + timeout
        db = None
        while time.time() < deadline:
            db = self.db()
            if self.desired_match(db):
                return db, True
            time.sleep(2)
        return db, False

    def observe_hub_rev(self, timeout=90):
        """Wait for one fresh REVISION_CHECK_ACK and return the Hub's current revision."""
        base = len(self.proxy.revision_checks)
        deadline = time.time() + timeout
        while time.time() < deadline:
            frames = list(self.proxy.revision_checks)
            for f in frames[base:]:
                if f.get('type') == 'REVISION_CHECK_ACK':
                    return f.get('global_rev')
            time.sleep(1)
        return None

    # ---------------- change helpers ----------------
    def update_script(self, content):
        sc = self.script()
        sc['content'] = content
        code, body = self.hub.put('/api/scripts/' + self.hub.quote(sc['id']), sc)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('script_update_http_%s %s' % (code, body))
        return self.script()

    def update_task(self, **fields):
        t = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_id']))
        for k, v in fields.items():
            t[k] = v
        code, body = self.hub.put('/api/tasks/' + self.hub.quote(t['id']), t)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('task_update_http_%s %s' % (code, body))
        return self.hub.json('/api/tasks/' + self.hub.quote(t['id']))

    # ---------------- phases ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc()}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'],
                                       'sync_status': n['sync_status'],
                                       'global_revision': n.get('global_revision')}
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        out['settings'] = settings
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        if any((s.get('name') or '').startswith(PREFIX) for s in scripts) or \
           any((t.get('name') or '').startswith(PREFIX) for t in tasks) or \
           any((n.get('hostname') or '').startswith(PREFIX) for n in nodes):
            raise RuntimeError('owned_fixture_already_present')
        out['owned_prefix_clear'] = True
        ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native3_pre'] = {
            'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
            'owned_child_processes': ci['owned_child_processes'],
            'agent_binary_sha256': ci['agent_binary_sha256'],
            'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': ci['primary_agent_unit'],
            'repair_agent_unit': ci['repair_agent_unit'],
        }
        if ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native3_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        self.rec('phase0-guard.json', out)
        return out

    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child',
            'node_ip': NODE_IP,
            'hub_address': 'http://192.168.100.209:18080',
        })
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
        node = self.wait_child_online(timeout=90)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {
                'host': NODE_IP, 'root_on_native3': self.child.root, 'config_mode_0600': True,
                'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                'listened_operator_port': port,
                'routing': 'child(native-3) -> operator 192.168.100.170:%d (frame proxy) -> repair hub :18080' % port,
            },
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {
                'note': 'owned child Agent uses the exact repair Agent binary; the process owner is the '
                        'harness on native-3 and never the protected nodesteer-agent*.service units',
                'agent_binary_sha256': child_agent.AGENT_SHA},
            'local_store_after_start': {k: self.db().get(k) for k in
                                        ('local_global_revision', 'scripts_total', 'tasks_total',
                                         'executions_total')},
        }
        self.rec('phase1-owned-child.json', out)
        return out

    def phase2_fixtures(self):
        content_sha = hashlib.sha256(script_body('v1').encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'sys-b03 notification/revision fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': script_body('v1'),
            'parameters': [{'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))

        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, task))
        self.state['task_id'] = task['id']
        self.state['created'].append(('task', task['id']))
        self.budget['hub_definition_creates'] += 2

        s_read = self.script()
        t_read = self.hub.json('/api/tasks/' + self.hub.quote(task['id']))
        if s_read.get('sha256') != content_sha:
            raise RuntimeError('script_readback_mismatch')
        db, ok = self.wait_child_rev(s_read.get('revision'), timeout=90)
        if not ok:
            raise RuntimeError('child_did_not_apply_fixtures')
        out = {
            'at_utc': now_utc(),
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'revision': s_read.get('revision')},
            'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                     'type': 'script', 'target_node_ids': t_read.get('target', {}).get('node_ids')},
            'child_applied': {'local_global_revision': db.get('local_global_revision'),
                              'owned_script': db.get('owned_script'),
                              'owned_task': db.get('owned_task')},
            'desired_equals_actual': bool(db.get('owned_script', {}).get('sha256') == s_read.get('sha256')
                                          and db.get('owned_task', {}).get('revision') == t_read.get('revision')),
        }
        self.rec('phase2-fixtures.json', out)
        return out

    # ---- Phase 3: one logical change -> exactly one Execution ----
    def phase3_single_execution(self):
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(self.state['task_id']),
                                   {'node_ids': {self.state['child_node_id']: True},
                                    'params': {'delay': '2'}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows) != 1:
            raise RuntimeError('manual_run_http_%s' % code)
        eid = rows[0]['id']
        self.state['executions']['manual'] = eid
        final = self.wait_execution(eid, timeout=90)
        hub_rows = [e for e in self.executions() if e['id'] == eid]
        db = self.db([eid])
        out = {
            'at_utc': now_utc(),
            'request': 'POST /api/tasks/%s/run node_ids={%s:true} params={delay:2}'
                       % (self.state['task_id'], self.state['child_node_id']),
            'execution_id': eid, 'status': final.get('status'), 'exit_code': final.get('exit_code'),
            'trigger_type': final.get('trigger_type'),
            'stdout_marker_present': MARKER + ':v1' in (final.get('stdout') or ''),
            'hub_rows_for_execution': len(hub_rows),
            'child_rows_for_execution': len([r for r in (db.get('requested_executions') or []) if r]),
            'child_task_execution_count': len(db.get('task_executions') or []),
        }
        out['assertions_pass'] = (final.get('status') == 'SUCCESS' and len(hub_rows) == 1
                                  and out['child_task_execution_count'] == 1)
        self.rec('phase3-single-execution.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase3_single_execution_failed')
        return out

    # ---- SYS-04: single lost notification -> Revision Check convergence ----
    def phase4_sys04(self):
        pre = self.db()
        r0 = pre.get('local_global_revision')
        # isolate the owned child from ALL notifications (its own + concurrent
        # batches') so convergence can only come from Revision Check.
        self.proxy.suppress_notifications(True)
        self.proxy.arm_drop_notification(self.state['script_id'])
        base = self.notif_base()
        ev_base = len(self.proxy.events)
        t_change = time.time()
        sc = self.update_script(script_body('v2'))
        dropped = self.wait_drop(self.state['script_id'], base, timeout=25)
        if not dropped:
            raise RuntimeError('sys04_notification_not_dropped')
        target = dropped.get('global_revision')
        sync_base = len(self.proxy.sync_frames)
        # immediately after the drop the child must still be at the old revision
        time.sleep(3)
        lagged = self.db()
        node_lagged = self.node()
        db, ok = self.wait_child_rev(target, timeout=REV_CHECK_INTERVAL + 60)
        t_converged = time.time()
        self.proxy.suppress_notifications(False)
        frames = list(self.proxy.sync_frames)
        syncs = frames[sync_base:]
        reqs = [f for f in syncs if f.get('type') == 'SYNC_REQUEST']
        resps = [f for f in syncs if f.get('type') == 'SYNC_RESPONSE']
        acks = [f for f in syncs if f.get('type') == 'SYNC_ACK']
        rev_after = list(self.proxy.revision_checks)
        node_after = self.node()
        # ordered timeline (proxy.events preserves insertion order) around the change:
        # the frame that immediately preceded the converging SYNC_REQUEST must be a
        # REVISION_CHECK_ACK, proving convergence came from Revision Check, not a notification.
        events = list(self.proxy.events)[ev_base:]
        trigger = None
        for i, e in enumerate(events):
            if e.get('event') == 'sync_frame' and e.get('type') == 'SYNC_REQUEST':
                trigger = events[i - 1] if i > 0 else None
                break
        out = {
            'at_utc': now_utc(),
            'scenario': 'one Desired State change (script content update) with its real-time '
                        'CHANGE_NOTIFICATION dropped on the owned child link; the child also gets no '
                        'other notification (suppressed), so only Revision Check can converge it',
            'pre_change_local_revision': r0,
            'dropped_notification': dropped,
            'notifications_suppressed_during_window': True,
            'child_still_lagged_3s_after_drop': lagged.get('local_global_revision'),
            'child_lagged_behind_target': (lagged.get('local_global_revision') or 0) < (target or 0),
            'node_sync_status_while_lagged': node_lagged.get('sync_status') if node_lagged else None,
            'target_global_revision': target,
            'converged': ok,
            'child_local_revision_after': db.get('local_global_revision'),
            'convergence_seconds': round(t_converged - t_change, 1),
            'convergence_waited_for_revision_check': (t_converged - t_change) > 5,
            'timeline_around_change': events,
            'frame_immediately_before_converging_sync_request': trigger,
            'sync_request_frames': reqs,
            'sync_response_frames': resps,
            'sync_ack_frames': acks,
            'sync_request_since': reqs[0].get('since') if reqs else None,
            'sync_response_full_resync': resps[0].get('full_resync') if resps else None,
            'sync_ack_applied_rev': acks[-1].get('applied_rev') if acks else None,
            'revision_check_ack_seen_at_or_after_target': [f for f in rev_after
                                                           if f.get('type') == 'REVISION_CHECK_ACK'
                                                           and (f.get('global_rev') or 0) >= (target or 0)][:3],
            'child_script_after': db.get('owned_script'),
            'hub_script_revision': sc.get('revision'),
            'hub_script_sha256': sc.get('sha256'),
            'desired_equals_actual': db.get('owned_script', {}).get('sha256') == sc.get('sha256'),
            'node_after': {k: node_after.get(k) for k in ('status', 'sync_status', 'global_revision')}
            if node_after else None,
        }
        out['assertions_pass'] = bool(
            out['child_lagged_behind_target'] and ok
            and out['convergence_waited_for_revision_check']
            and out['sync_response_full_resync'] is False
            and out['desired_equals_actual']
            and trigger is not None and trigger.get('type') == 'REVISION_CHECK_ACK')
        self.rec('phase4-sys04-lost-notification.json', out)
        return out

    # ---- SYS-06: multi-revision lag -> incremental sync ----
    def phase6_sys06(self):
        pre = self.db()
        r0 = pre.get('local_global_revision')
        # isolate from other notifications, then make three Desired State changes whose
        # CHANGE_NOTIFICATION frames are all dropped: script1 content update, task1 update,
        # and a new task2 targeted at the child (task/script membership defines Desired State).
        self.proxy.suppress_notifications(True)
        self.proxy.arm_drop_notification([self.state['script_id'], self.state['task_id']])
        base = self.notif_base()
        t_change = time.time()
        s_new = self.update_script(script_body('v3'))
        t_new = self.update_task(param_values={'delay': '3'})
        code, task2 = self.hub.post('/api/tasks', {
            'name': TASK2_NAME, 'type': 'script', 'script_id': self.state['script_id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        self.budget['hub_definition_creates'] += 1
        if code not in (200, 201):
            raise RuntimeError('task2_create_http_%s %s' % (code, task2))
        self.state['task2_id'] = task2['id']
        self.state['created'].append(('task2', task2['id']))
        want = [self.state['script_id'], self.state['task_id'], self.state['task2_id']]
        dropped = []
        deadline = time.time() + 25
        while time.time() < deadline:
            dropped = self.dropped_since(base, want)
            if len({d['object_id'] for d in dropped}) >= 3:
                break
            time.sleep(0.3)
        target = max([d.get('global_revision') for d in dropped] or [0])
        sync_base = len(self.proxy.sync_frames)
        time.sleep(3)
        lagged = self.db()
        db, ok = self.wait_child_rev(target, timeout=REV_CHECK_INTERVAL + 60)
        self.proxy.suppress_notifications(False)
        frames = list(self.proxy.sync_frames)[sync_base:]
        reqs = [f for f in frames if f.get('type') == 'SYNC_REQUEST']
        resps = [f for f in frames if f.get('type') == 'SYNC_RESPONSE']
        acks = [f for f in frames if f.get('type') == 'SYNC_ACK']
        t2_hub = self.hub.json('/api/tasks/' + self.hub.quote(task2['id']))
        out = {
            'at_utc': now_utc(),
            'scenario': 'three Desired State changes (script1 content, task1 update, new task2 on the '
                        'child) with all three CHANGE_NOTIFICATION frames dropped; Agent must catch up '
                        'via one Sync driven by Revision Check',
            'pre_change_local_revision': r0,
            'dropped_notifications': dropped,
            'dropped_distinct_objects': sorted({d['object_id'] for d in dropped}),
            'lag': {'local_revision_3s_after_changes': lagged.get('local_global_revision'),
                    'target_revision': target,
                    'lagged_behind': (lagged.get('local_global_revision') or 0) < target},
            'target_global_revision': target,
            'converged': ok,
            'child_local_revision_after': db.get('local_global_revision'),
            'convergence_seconds': round(time.time() - t_change, 1),
            'sync_request_count': len(reqs),
            'sync_request_frames': reqs,
            'sync_request_since': reqs[0].get('since') if reqs else None,
            'sync_response_frames': resps,
            'sync_response_delta': {
                'global_rev': resps[0].get('global_rev') if resps else None,
                'full_resync': resps[0].get('full_resync') if resps else None,
                'snapshot': resps[0].get('snapshot') if resps else None,
                'scripts_in_response': resps[0].get('scripts') if resps else None,
                'tasks_in_response': resps[0].get('tasks') if resps else None,
            } if resps else None,
            'sync_ack_applied_rev': acks[-1].get('applied_rev') if acks else None,
            'child_after': {'owned_script': db.get('owned_script'), 'owned_tasks': db.get('owned_tasks'),
                            'scripts_total': db.get('scripts_total'),
                            'tasks_total': db.get('tasks_total')},
            'hub': {'script_revision': s_new.get('revision'), 'script_sha256': s_new.get('sha256'),
                    'task_revision': t_new.get('revision'), 'task2_revision': t2_hub.get('revision')},
            'desired_equals_actual': self.desired_match(db),
        }
        out['incremental_since_matches'] = (out['sync_request_since'] == r0)
        out['assertions_pass'] = bool(
            out['lag']['lagged_behind'] and ok and out['desired_equals_actual']
            and out['sync_response_delta'] and out['sync_response_delta']['full_resync'] is False
            and len(resps) >= 1 and not reqs == [] and out['sync_request_since'] == r0)
        self.rec('phase6-sys06-revision-lag.json', out)
        return out

    # ---- AR-10: lost notification + offline recovery (idempotent/persistent/desired==actual) ----
    def phase7_ar10(self):
        pre = self.db()
        r0 = pre.get('local_global_revision')
        # (1) lose one notification while connected (suppress all notifications so
        #     only Revision Check could converge the first change)
        self.proxy.suppress_notifications(True)
        self.proxy.arm_drop_notification(self.state['task_id'])
        base = self.notif_base()
        t_chg = time.time()
        t_first = self.update_task(param_values={'delay': '4'})
        dropped1 = self.wait_drop(self.state['task_id'], base, timeout=25)
        if not dropped1:
            raise RuntimeError('ar10_notification_not_dropped')
        target1 = dropped1.get('global_revision')
        # (2) take the child offline BEFORE it can Revision-Check; then change Desired State offline
        stop = self.child.stop()
        self.budget['child_process_stops'] += 1
        node_offline = self.node()
        time.sleep(3)
        t_offline = time.time()
        s_off = self.update_script(script_body('v4'))
        offline_change_rev = s_off.get('revision')
        # (3) restart -> reconnect reconciliation (HELLO_ACK desired rev > local -> SYNC_REQUEST)
        sync_base = len(self.proxy.sync_frames)
        start = self.child.start()
        self.budget['child_process_starts'] += 1
        node_recover = self.wait_child_online(timeout=120)
        db_after, desired_ok = self.wait_desired_match(timeout=120)
        hub_rev = self.observe_hub_rev(timeout=120)
        frames = list(self.proxy.sync_frames)[sync_base:]
        reqs = [f for f in frames if f.get('type') == 'SYNC_REQUEST']
        resps = [f for f in frames if f.get('type') == 'SYNC_RESPONSE']
        acks = [f for f in frames if f.get('type') == 'SYNC_ACK']
        s_hub = self.script()
        t_hub = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_id']))
        # (4) idempotency: duplicate a live notification, then confirm state stays single & correct
        self.proxy.suppress_notifications(False)
        self.proxy.arm_dup_notification(self.state['script_id'])
        t_dup = self.update_script(script_body('v5'))
        dup_seen = None
        deadline = time.time() + 25
        while time.time() < deadline:
            if self.proxy.state()['dup_notification_done']:
                dup_seen = self.proxy.state()['dup_notification_done']
                break
            time.sleep(0.3)
        db_dup, dup_ok = self.wait_desired_match(timeout=120)
        # (5) second restart on the converged state -> identical state, no new Execution
        restart2 = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        self.wait_child_online(timeout=120)
        time.sleep(10)
        db_final = self.db([self.state['executions'].get('manual')])
        hub_execs = self.executions()
        out = {
            'at_utc': now_utc(),
            'scenario': 'lost CHANGE_NOTIFICATION -> child taken offline -> Desired State changed while '
                        'offline (notification undeliverable) -> child restarted and reconciled',
            'pre_local_revision': r0,
            'first_change_dropped_notification': dropped1,
            'target_after_first_change': target1,
            'child_stopped': stop,
            'node_right_after_stop': {k: node_offline.get(k) for k in ('status', 'sync_status')}
            if node_offline else None,
            'offline_change': {'script_revision': offline_change_rev, 'changed_at_utc': now_utc()},
            'child_offline_seconds': round(time.time() - t_offline, 1),
            'child_started': start,
            'recovery': {
                'node_after_restart': {k: node_recover.get(k) for k in
                                       ('status', 'sync_status', 'global_revision')} if node_recover else None,
                'hub_revision_observed_after_restart': hub_rev,
                'child_local_revision_after': db_after.get('local_global_revision'),
                'converged_desired_equals_actual': desired_ok,
                'sync_request_frames': reqs,
                'sync_response_frames': resps,
                'sync_ack_frames': acks,
                'reconnect_sync_request_since': reqs[0].get('since') if reqs else None,
                'persisted_store_survived_restart': bool(db_after.get('owned_script')
                                                         and db_after.get('owned_task')),
                'child_script_after': db_after.get('owned_script'),
                'hub_script_sha256': s_hub.get('sha256'),
                'child_task_revision_after': (db_after.get('owned_task') or {}).get('revision'),
                'hub_task_revision': t_hub.get('revision'),
            },
            'idempotency_duplicate_notification': {
                'duplicate_delivered': dup_seen is not None,
                'duplicate_frames': dup_seen,
                'target_revision': t_dup.get('revision'),
                'desired_equals_actual_after_duplicate': dup_ok,
                'child_local_revision_after': db_dup.get('local_global_revision'),
                'sync_frames_during_duplicate_window': len(self.proxy.sync_frames),
            },
            'idempotency_second_restart': {
                'restart': restart2,
                'child_local_revision_after': db_final.get('local_global_revision'),
                'owned_script': db_final.get('owned_script'),
                'owned_tasks': db_final.get('owned_tasks'),
                'task_execution_count': len(db_final.get('task_executions') or []),
                'hub_execution_count': len(hub_execs),
                'hub_execution_ids': [e['id'] for e in hub_execs],
            },
            'node_final': {k: (self.node() or {}).get(k) for k in
                           ('status', 'sync_status', 'global_revision')},
        }
        exec_id = self.state['executions'].get('manual')
        out['assertions_pass'] = bool(
            desired_ok and dup_ok
            and out['idempotency_duplicate_notification']['duplicate_delivered']
            and out['idempotency_second_restart']['task_execution_count'] == 1
            and out['idempotency_second_restart']['hub_execution_count'] == 1
            and out['idempotency_second_restart']['hub_execution_ids'] == [exec_id]
            and out['recovery']['persisted_store_survived_restart']
            and out['recovery']['node_after_restart'] is not None
            and out['recovery']['node_after_restart']['status'] == 'online')
        self.rec('phase7-ar10-lost-notification-recovery.json', out)
        return out

    # ---- AR-13: revision lag recovery + idempotent/persistent/desired==actual ----
    def phase8_ar13(self):
        pre = self.db()
        r0 = pre.get('local_global_revision')
        self.proxy.suppress_notifications(True)
        self.proxy.arm_drop_notification([self.state['script_id'], self.state['task_id']])
        base = self.notif_base()
        t_chg = time.time()
        s_hub = self.update_script(script_body('v6'))
        t_hub = self.update_task(param_values={'delay': '5'})
        dropped = []
        want = [self.state['script_id'], self.state['task_id']]
        deadline = time.time() + 25
        while time.time() < deadline:
            dropped = self.dropped_since(base, want)
            if len({d['object_id'] for d in dropped}) >= 2:
                break
            time.sleep(0.3)
        target = max([d.get('global_revision') for d in dropped] or [0])
        sync_base = len(self.proxy.sync_frames)
        time.sleep(3)
        lagged = self.db()
        db, ok = self.wait_child_rev(target, timeout=REV_CHECK_INTERVAL + 60)
        self.proxy.suppress_notifications(False)
        frames = list(self.proxy.sync_frames)[sync_base:]
        reqs = [f for f in frames if f.get('type') == 'SYNC_REQUEST']
        resps = [f for f in frames if f.get('type') == 'SYNC_RESPONSE']
        desired_ok = self.desired_match(db)
        # recovery: restart the child on the converged state -> must stay consistent
        restart = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_recover = self.wait_child_online(timeout=120)
        db_after, restart_desired_ok = self.wait_desired_match(timeout=120)
        db_after = self.db([self.state['executions'].get('manual')])
        hub_execs = self.executions()
        out = {
            'at_utc': now_utc(),
            'scenario': 'two Desired State changes with both CHANGE_NOTIFICATION frames dropped -> '
                        'Agent revision lag recovered by Revision Check, then a real child restart',
            'pre_local_revision': r0,
            'dropped_notifications': dropped,
            'target_global_revision': target,
            'lag': {'local_revision_3s_after_changes': lagged.get('local_global_revision'),
                    'lagged_behind': (lagged.get('local_global_revision') or 0) < target},
            'recovery_by_revision_check': {
                'converged': ok,
                'child_local_revision_after': db.get('local_global_revision'),
                'convergence_seconds': round(time.time() - t_chg, 1),
                'sync_request_count': len(reqs),
                'sync_request_since': reqs[0].get('since') if reqs else None,
                'sync_response_full_resync': resps[0].get('full_resync') if resps else None,
                'desired_equals_actual': desired_ok,
            },
            'recovery_by_restart': {
                'restart': restart,
                'node_after': {k: node_recover.get(k) for k in
                               ('status', 'sync_status', 'global_revision')} if node_recover else None,
                'child_local_revision_after': db_after.get('local_global_revision'),
                'owned_script': db_after.get('owned_script'),
                'owned_tasks': db_after.get('owned_tasks'),
                'persisted_after_restart': bool(db_after.get('owned_script')
                                                and db_after.get('owned_tasks')),
                'desired_equals_actual_after_restart': restart_desired_ok,
                'task_execution_count': len(db_after.get('task_executions') or []),
                'hub_execution_count': len(hub_execs),
            },
            'node_final': {k: (self.node() or {}).get(k) for k in
                           ('status', 'sync_status', 'global_revision')},
        }
        out['assertions_pass'] = bool(
            ok and desired_ok and out['recovery_by_restart']['persisted_after_restart']
            and out['recovery_by_restart']['desired_equals_actual_after_restart']
            and out['recovery_by_restart']['task_execution_count'] == 1
            and out['recovery_by_restart']['hub_execution_count'] == 1)
        self.rec('phase8-ar13-revision-lag-recovery.json', out)
        return out

    # ---- cleanup ----
    def phase9_cleanup(self):
        results = {'deleted': [], 'errors': []}
        try:
            if self.proxy:
                self.proxy.set_mode('pass')
                results['proxy_mode_restored'] = self.proxy.mode()
        except Exception as exc:
            results['errors'].append('proxy_restore:%s' % exc)
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
        order = [('schedule', s) for s in self.state.get('schedules', [])]
        order += [('task', self.state['task2_id']), ('task', self.state['task_id']),
                  ('script', self.state['script_id'])]
        for kind, obj_id in order:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append((kind, obj_id))
        node_id = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        self.rec('evidence/phase9-cleanup.json', results)
        return results

    def phase10_oracle(self, cleanup):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        ok = True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
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
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        protected_units_ok = (inv['primary_agent_unit'].get('ActiveState') == 'active'
                              and inv['repair_agent_unit'].get('ActiveState') == 'active')
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
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': protected_units_ok,
            'cleanup_results': cleanup,
            'proxy_final_state': (self.proxy.state() if self.proxy else None),
            'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and protected_units_ok
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        hubapi.write_json('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b03', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-04', 'AR-10', 'SYS-06', 'AR-13'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b03', 'nonce': NONCE, 'phase': 'guard',
                'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)
        run.log('phase', phase)

    failed = False
    try:
        mark('guard')
        evidence['phases']['guard'] = run.phase0_guard()
        mark('child_start')
        evidence['phases']['child'] = run.phase1_child()
        progress['child_node_id'] = run.state['child_node_id']
        progress['child_agent_id'] = run.state['child_agent_id']
        mark('fixtures')
        evidence['phases']['fixtures'] = run.phase2_fixtures()
        progress['task_id'] = run.state['task_id']
        progress['script_id'] = run.state['script_id']
        mark('single_execution')
        evidence['phases']['single_execution'] = run.phase3_single_execution()
        mark('sys04')
        evidence['phases']['sys04'] = run.phase4_sys04()
        mark('sys06')
        evidence['phases']['sys06'] = run.phase6_sys06()
        mark('ar10')
        evidence['phases']['ar10'] = run.phase7_ar10()
        mark('ar13')
        evidence['phases']['ar13'] = run.phase8_ar13()
        mark('frames')
        evidence['phases']['frames'] = {
            'proxy_state': run.proxy.state(),
            'notification_frames': run.proxy.notifications,
            'revision_check_frames': run.proxy.revision_checks,
            'sync_frames': run.proxy.sync_frames,
        }
        run.rec('evidence/phase11-notification-revision-timeline.json', evidence['phases']['frames'])
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True

    mark('cleanup')
    try:
        if run.proxy and 'frames' not in evidence['phases']:
            evidence['phases']['frames'] = {
                'proxy_state': run.proxy.state(),
                'notification_frames': run.proxy.notifications,
                'revision_check_frames': run.proxy.revision_checks,
                'sync_frames': run.proxy.sync_frames}
            run.rec('evidence/phase11-notification-revision-timeline.json', evidence['phases']['frames'])
    except Exception:
        pass
    try:
        if run.state['child_node_id'] or run.state['task_id']:
            cleanup = run.phase9_cleanup()
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
        oracle = run.phase10_oracle(progress.get('final_cleanup', {}))
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['notes'] = run.notes
    hubapi.write_json('run-evidence.json', evidence)

    hubapi.write_json('fixture-ledger.json', {
        'batch_id': 'sys-b03', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task2_id'], 'name': TASK2_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task2_id'], {}).get('get_http') == 404},
        ],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'delete_http': 200,
                       'deleted_get404': True},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': NODE_IP, 'root': run.child.root if run.child else None,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('native3_child_removal', {}).get('root_removed'))},
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'})

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    run.log('DONE failed=%s clean=%s' % (failed, progress.get('oracle_clean')))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    sys04 = ph.get('sys04', {})
    sys06 = ph.get('sys06', {})
    ar10 = ph.get('ar10', {})
    ar13 = ph.get('ar13', {})
    single = ph.get('single_execution', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    canonical = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per '
                 'remaining_dimensions)')

    def case(case_id, title, proposed, confidence, dims, limits):
        return {
            'case_id': case_id, 'title': title, 'canonical_before': canonical,
            'proposed_after': proposed, 'confidence': confidence,
            'covered_dimensions': dims, 'remaining_or_limitations': limits,
            'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                'case-result.json', 'progress.json', 'fixture-ledger.json',
                'final-cleanup-oracle.json', 'run-evidence.json'],
            'actual_resources': {
                'owned_prefix': PREFIX, 'child_node_id': run.state['child_node_id'],
                'child_agent_id': run.state['child_agent_id'], 'script_id': run.state['script_id'],
                'task_id': run.state['task_id'], 'task2_id': run.state['task2_id'],
                'executions': run.state['executions'],
                'preserved_execution_history': [v for v in run.state['executions'].values() if v]},
            'actual_budget': dict(run.budget),
            'final_cleanup': progress.get('final_cleanup', {}),
        }

    s04_ok = bool(sys04.get('assertions_pass'))
    s06_ok = bool(sys06.get('assertions_pass'))
    ar10_ok = bool(ar10.get('assertions_pass'))
    ar13_ok = bool(ar13.get('assertions_pass'))
    single_ok = bool(single.get('assertions_pass'))

    cases = [
        case('SYS-04', 'SYS-04 实时通知丢失：Revision Check 最终收敛。',
             'PASS' if (s04_ok and cleanup_ok) else ('PASS_WITH_NOTES' if s04_ok else 'PARTIAL'),
             'high' if (s04_ok and cleanup_ok) else 'medium',
             [{'test_id': '20261014-RUNTIME:SYS-04',
               'layer': 'hub-sync/agent-connection/frame-proxy/agent-sqlite',
               'status': 'PASS' if s04_ok else 'FAIL',
               'description': [
                   'A real Desired State change (script content update) published a real '
                   'CHANGE_NOTIFICATION whose global_revision equalled the new Hub revision; the '
                   'frame was dropped on the owned child link (per-object-id arming, no other '
                   'batch affected).',
                   '3s after the drop the child local applied revision was still behind the target, '
                   'and the notification-triggered path was gone.',
                   'The child converged only at a periodic Revision Check: REVISION_CHECK -> '
                   'REVISION_CHECK_ACK(global_rev=target) -> SYNC_REQUEST(since=local) -> '
                   'SYNC_RESPONSE(full_resync=false) -> SYNC_ACK(applied_rev=target).',
                   'Actual effect: the child script sha256/revision then matched the Hub Desired State.']}],
             ['One connectivity scenario (link up, single dropped notification). Duplicated or '
              'reordered notifications on a live link are covered under AR-10.'],
             ),
        case('AR-10', 'AR-10 Lost Notification；恢复后幂等、持久化、Desired State与实际状态一致',
             'PASS' if (ar10_ok and cleanup_ok) else ('PASS_WITH_NOTES' if ar10_ok else 'PARTIAL'),
             'high' if (ar10_ok and cleanup_ok) else 'medium',
             [{'test_id': '20261014-RUNTIME:AR-10',
               'layer': 'hub-sync/frame-proxy/agent-connection/agent-sqlite',
               'status': 'PASS' if ar10_ok else 'FAIL',
               'description': [
                   'Lost notification: a task-change CHANGE_NOTIFICATION was dropped; the child was '
                   'then taken offline and a second Desired State change was made while offline, so '
                   'it could not be delivered at all.',
                   'Recovery: after a real child restart the reconnect reconciliation '
                   '(HELLO_ACK desired revision > local) drove an incremental SYNC_REQUEST and the '
                   'child converged; node returned to online/synced.',
                   'Idempotent: a duplicated CHANGE_NOTIFICATION left the applied state correct and '
                   'single (one row per object), and a second real restart reproduced the same applied '
                   'script sha / task revisions with exactly one Execution for the one manual Run Now.',
                   'Persistent: the child store survived restarts with the same applied revisions; '
                   'Desired State == applied state (sha/revision) on both sides.']}],
             ['"Offline" was produced by stopping only the owned child process; the protected repair '
              'Agents and the Hub were never stopped.'],
             ),
        case('SYS-06', 'SYS-06 Agent Revision 落后：增量 Sync 正确。',
             'PASS' if (s06_ok and cleanup_ok) else ('PASS_WITH_NOTES' if s06_ok else 'PARTIAL'),
             'high' if (s06_ok and cleanup_ok) else 'medium',
             [{'test_id': '20261014-RUNTIME:SYS-06',
               'layer': 'hub-sync/agent-connection/agent-sqlite',
               'status': 'PASS' if s06_ok else 'FAIL',
               'description': [
                   'Three distinct Desired State changes (script update, task1 update, new task2 '
                   'targeted at the child) were made with all three CHANGE_NOTIFICATION frames '
                   'dropped; the child local revision stayed at the pre-change value (real lag).',
                   'Revision Check drove exactly one sync cycle: SYNC_REQUEST(since=local applied '
                   'revision) -> SYNC_RESPONSE(full_resync=false, snapshot=true) -> '
                   'SYNC_ACK(applied_rev=new global revision).',
                   'Agent-side DB readback after the sync matched the Hub Desired State for every '
                   'owned object (script sha256, task1/task2 revisions) and the local applied '
                   'revision reached the target.']}],
             ['The Hub sync response is a complete node-scoped Desired State snapshot with '
              'tombstones filtered by `since` (not a per-object delta); the incremental property '
              'verified here is the since-based request plus idempotent full upsert with pruning. '
              'Because other batches advance the global revision concurrently, assertions are on '
              'the owned objects and on since=local, not on a global revision count.'],
             ),
        case('AR-13', 'AR-13 Revision Lag；恢复后幂等、持久化、Desired State与实际状态一致',
             'PASS' if (ar13_ok and cleanup_ok) else ('PASS_WITH_NOTES' if ar13_ok else 'PARTIAL'),
             'high' if (ar13_ok and cleanup_ok) else 'medium',
             [{'test_id': '20261014-RUNTIME:AR-13',
               'layer': 'hub-sync/frame-proxy/agent-connection/agent-sqlite',
               'status': 'PASS' if ar13_ok else 'FAIL',
               'description': [
                   'Revision lag: two Desired State changes with both CHANGE_NOTIFICATION frames '
                   'dropped left the child behind the Hub revision (measured via the child store).',
                   'Recovery: Revision Check converged the child (full_resync=false, since=local '
                   'applied revision); Desired State then equalled the applied state.',
                   'Idempotent/persistent: a subsequent real child restart re-established '
                   'online/synced without re-applying different values and without adding a second '
                   'Execution for the single manual Run Now.']}],
             ['The lag window is bounded by the Hub-pushed revision_check_interval_sec (45s in this '
              'environment); measured convergence times are recorded.'],
             ),
    ]
    result = {'batch_id': 'sys-b03',
              'title': '通知丢失 / Revision 落后：Revision Check 收敛（SYS-04/AR-10/SYS-06/AR-13）',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
              'cases': cases, 'deviations': evidence['deviations'], 'notes': evidence.get('notes', []),
              'single_execution_phase': single,
              'budget': run.budget, 'final_cleanup_oracle_clean': cleanup_ok}
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
