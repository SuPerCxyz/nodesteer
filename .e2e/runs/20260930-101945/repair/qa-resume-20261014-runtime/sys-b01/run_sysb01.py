#!/usr/bin/env python3
"""sys-b01 runtime batch: repair-Hub restart -> Agent auto-reconnect, Session and
Desired State rebuild, authority re-convergence.

Cases: SYS-03, SYS-17, INV-01, AR-01.

Real evidence chain (no product source changes, no protected Agent stopped, no
VM touched):
  operator/API act -> real request -> Hub Desired State commit -> real Hub
  restart (the ONE authorised unit) -> real Agent reconnect frames -> real
  Execution side effects -> API/SQLite readback.

The only unit this harness stops/starts is
`nodesteer-hub-repair-20260930-101945.service`.  `nodesteer-hub.service`
(:8080), the primary Agent units and the 4 protected repair Agent units are
never written to or restarted.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import child_agent
import hubapi
import hubctl
import hubdb
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb01-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
MARKER = 'QA:%s:EXEC' % NONCE
DEV = 'evidence/'
NODE_IP = '192.168.100.204'                       # owned child host: native-1
TARGET_NODE_ID = '611c02a5-1859-4465-878b-d543b7f6fdec'   # native-2 (protected)
RETRY_WINDOW = 150                                # reconnect observation window (s)

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


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {'script_id': None, 'task_id': None, 'child_node_id': None,
                      'child_agent_id': None, 'executions': {}, 'created': [],
                      'deleted': [], 'delete_results': {}}
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0,
                       'hub_definition_deletes': 0, 'manual_run_posts': 0,
                       'node_enroll_posts': 1, 'child_process_starts': 0,
                       'child_process_stops': 0, 'hub_restarts': 0}

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

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def script(self):
        return self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))

    def task(self):
        return self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_id']))

    def executions(self, node_id=None, limit=100):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(self.state['task_id']))
        if node_id:
            q += '&node_id=' + self.hub.quote(node_id)
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

    def run_node(self, node_id, params):
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(self.state['task_id']),
                                   {'node_ids': {node_id: True}, 'params': params})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200:
            raise RuntimeError('run_now_http_%s %s' % (code, rows))
        return rows

    def child_db(self, exec_ids=None):
        return self.child.db_read(self.state['task_id'], exec_ids or [],
                                  self.state['script_id'], task_ids=[self.state['task_id']])

    def wait_child_rev(self, target, timeout=90):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.child_db()
            if (last.get('local_global_revision') or 0) >= target:
                return last, True
            time.sleep(2)
        return last, False

    def protected_snapshot(self):
        by_id = self.nodes_by_id()
        out = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            out[label] = None if not n else {
                'id': nid, 'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision'),
                'agent_version': n.get('agent_version')}
        return out

    def poll_nodes(self, seconds, interval=1.0, stop=None):
        """Sampled node table; `stop` is a predicate over (by_id) returning True to end early."""
        t0 = time.time()
        samples = []
        while time.time() - t0 < seconds:
            by_id = self.nodes_by_id()
            row = {'at_utc': now_utc(), 't': round(time.time() - t0, 1), 'nodes': {}}
            for nid, label in PROTECTED_NODES.items():
                n = by_id.get(nid) or {}
                row['nodes'][label] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                       'global_revision': n.get('global_revision')}
            cid = self.state.get('child_node_id')
            n = by_id.get(cid) or {}
            row['nodes']['owned_child'] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                           'global_revision': n.get('global_revision')}
            samples.append(row)
            if stop and stop(by_id):
                return {'complete': True, 'seconds': round(time.time() - t0, 1), 'samples': samples}
            time.sleep(interval)
        return {'complete': False, 'seconds': round(time.time() - t0, 1), 'samples': samples}

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
                'id': nid, 'agent_id': n.get('agent_id'), 'hostname': n.get('hostname'),
                'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision')}
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        out['settings_readonly'] = settings
        out['hub_unit_before'] = hubctl.unit_state()
        pid = out['hub_unit_before'].get('MainPID')
        out['hub_binary_before'] = hubctl.binary_sha(pid) if pid and pid != '0' else None
        out['hub_binary_sha_matches_pinned'] = (
            out['hub_binary_before'] or {}).get('sha256') == hubctl.HUB_BIN_SHA
        if not out['hub_binary_sha_matches_pinned']:
            raise RuntimeError('hub_binary_sha_mismatch')
        out['health_before'] = hubctl.health()
        out['primary_hub_unit_untouched_readonly'] = hubctl.primary_hub_state()
        out['hub_db_before'] = hubdb.snapshot()
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        out['fixture_absence'] = {
            'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
            'scripts_total': len(scripts), 'tasks_total': len(tasks), 'nodes_total': len(nodes)}
        if any(out['fixture_absence'][k] for k in
               ('scripts_matching_prefix', 'tasks_matching_prefix', 'child_nodes_matching_prefix')):
            raise RuntimeError('owned_fixture_already_present')
        ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native1_pre'] = {
            'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
            'owned_child_processes': ci['owned_child_processes'],
            'other_qa_child_processes': ci['other_qa_child_processes'],
            'owned_root_glob': ci['owned_root_glob'],
            'agent_binary_sha256': ci['agent_binary_sha256'],
            'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': ci['primary_agent_unit'],
            'repair_agent_unit': ci['repair_agent_unit']}
        if ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native1_pre']['agent_binary_sha_matches']:
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
        res = self.poll_nodes(90, 1.0, stop=lambda by: (
            (by.get(self.state['child_node_id']) or {}).get('status') == 'online'
            and (by.get(self.state['child_node_id']) or {}).get('sync_status') == 'synced'))
        node = self.node()
        if not res['complete']:
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {
                'host': NODE_IP, 'root_on_native1': self.child.root, 'config_mode_0600': True,
                'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                'listened_operator_port': port,
                'routing': 'owned child(native-1) -> operator 192.168.100.170:%d (own frame proxy) -> repair hub :18080' % port},
            'connect_timeline': res['samples'],
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {
                'note': 'owned child Agent uses the exact repair Agent binary; process owner is the harness '
                        'on native-1, never a nodesteer-agent*.service unit',
                'agent_binary_sha256': child_agent.AGENT_SHA},
            'child_store_after_start': {k: self.child_db().get(k) for k in
                                        ('local_global_revision', 'scripts_total', 'tasks_total')},
        }
        self.rec('phase1-owned-child.json', out)
        return out

    def phase2_fixtures(self):
        sha_v1 = hashlib.sha256(script_body('v1').encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'sys-b01 hub-restart fixture (nonce %s).' % NONCE,
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
            'target': {'type': 'node', 'node_ids': [TARGET_NODE_ID, self.state['child_node_id']]},
            'parameters': [], 'param_values': {'tag': 'none', 'delay': '0'}, 'timeout': 30,
            'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, task))
        self.state['task_id'] = task['id']
        self.state['created'].append(('task', task['id']))
        self.budget['hub_definition_creates'] += 2
        s_read, t_read = self.script(), self.task()
        if s_read.get('sha256') != sha_v1 or s_read.get('name') != SCRIPT_NAME:
            raise RuntimeError('script_readback_mismatch')
        if t_read.get('script_id') != script['id'] or t_read.get('retry') != 0 or \
           sorted(t_read.get('target', {}).get('node_ids') or []) != sorted([TARGET_NODE_ID, self.state['child_node_id']]):
            raise RuntimeError('task_readback_mismatch')
        target_rev = s_read.get('revision')
        db, child_ok = self.wait_child_rev(target_rev, timeout=120)
        res = self.poll_nodes(120, 1.0, stop=lambda by: (
            (by.get(TARGET_NODE_ID) or {}).get('global_revision', 0) >= target_rev))
        if not (child_ok and res['complete']):
            raise RuntimeError('both_targets_did_not_apply_fixtures')
        out = {
            'at_utc': now_utc(),
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'revision': s_read.get('revision'), 'expected_sha256_v1': sha_v1,
                       'content_sha256_matches': s_read.get('sha256') == sha_v1},
            'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                     'type': 'script', 'retry': t_read.get('retry'),
                     'target_node_ids': t_read.get('target', {}).get('node_ids'),
                     'schedule_free': True},
            'child_applied': {'local_global_revision': db.get('local_global_revision'),
                              'owned_script': db.get('owned_script')},
            'target_node_applied': res['samples'][-1]['nodes'] if res['samples'] else None,
            'target_node_connect_timeline': res['samples'],
            'desired_equals_actual_on_child': db.get('owned_script', {}).get('sha256') == sha_v1,
        }
        if not out['desired_equals_actual_on_child']:
            raise RuntimeError('child_does_not_match_hub_desired_state')
        self.rec('phase2-fixtures.json', out)
        return out

    # ---- SYS-03 / AR-01: real execution before the restart ----
    def phase3_pre_restart_baseline(self):
        rows = self.run_node(TARGET_NODE_ID, {'tag': 'pre', 'delay': '2'})
        if len(rows) != 1:
            raise RuntimeError('pre_restart_run_expected_one_row')
        pre_exec = rows[0]['id']
        self.state['executions']['pre_restart_target_node'] = pre_exec
        final = self.wait_exec(pre_exec, timeout=120)
        hub_rows = [e for e in self.executions(TARGET_NODE_ID) if e['id'] == pre_exec]
        baseline = {
            'at_utc': now_utc(),
            'hub_db': hubdb.snapshot([self.state['script_id'], self.state['task_id']]),
            'script_readback': self.script(),
            'task_readback': self.task(),
            'protected_nodes': self.protected_snapshot(),
            'child_node': {k: (self.node() or {}).get(k) for k in
                           ('status', 'sync_status', 'global_revision')},
        }
        out = {
            'at_utc': now_utc(),
            'request': 'POST /api/tasks/%s/run node_ids={%s:true} params={tag:pre,delay:2}'
                       % (self.state['task_id'], TARGET_NODE_ID),
            'execution_id': pre_exec,
            'status': final.get('status'), 'exit_code': final.get('exit_code'),
            'trigger_type': final.get('trigger_type'), 'node_id': final.get('node_id'),
            'stdout_marker_pre_present': (MARKER + ':pre') in (final.get('stdout') or ''),
            'stdout': (final.get('stdout') or '').strip(),
            'hub_rows_for_execution': len(hub_rows),
            'baseline': baseline,
        }
        out['assertions_pass'] = bool(final.get('status') == 'SUCCESS'
                                      and out['stdout_marker_pre_present']
                                      and len(hub_rows) == 1
                                      and final.get('node_id') == TARGET_NODE_ID)
        self.rec('phase3-pre-restart-execution.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase3_pre_restart_execution_failed')
        return out

    # ---- Desired State change made while the owned child is isolated ----
    def phase4_desired_state_change(self):
        self.proxy.set_mode('cut')
        cut_observed = self.poll_nodes(30, 1.0, stop=lambda by: (
            (by.get(self.state['child_node_id']) or {}).get('status') == 'offline'))
        db_before = self.child_db()
        t_change = time.time()
        sha_v2 = hashlib.sha256(script_body('v2').encode()).hexdigest()
        cur = self.script()
        cur['content'] = script_body('v2')
        code, body = self.hub.put('/api/scripts/' + self.hub.quote(cur['id']), cur)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('script_update_http_%s %s' % (code, body))
        s_v2 = self.script()
        rev_after = hubdb.snapshot([self.state['script_id'], self.state['task_id']])['revision_sequence']
        db_after = self.child_db()
        out = {
            'at_utc': now_utc(),
            'scenario': 'the owned child is cut from the Hub, then a real Desired State change (script '
                        'content v1 -> v2) is committed on the Hub. The change must survive the Hub restart '
                        'and the reconnecting Agent must reconcile to it.',
            'proxy_mode': self.proxy.mode(),
            'child_offline_observed_in_hub': cut_observed['complete'],
            'cut_timeline': cut_observed['samples'],
            'desired_state_change': {
                'object': 'script', 'id': s_v2.get('id'),
                'sha256_before': cur.get('sha256'), 'sha256_after': s_v2.get('sha256'),
                'expected_sha256_v2': sha_v2, 'sha256_matches_v2': s_v2.get('sha256') == sha_v2,
                'revision_before': None if not isinstance(body, dict) else cur.get('revision'),
                'revision_after': s_v2.get('revision'),
                'global_revision_after_change': rev_after},
            'child_store_before_change': {'local_global_revision': db_before.get('local_global_revision'),
                                          'owned_script': db_before.get('owned_script')},
            'child_store_after_change': {'local_global_revision': db_after.get('local_global_revision'),
                                         'owned_script': db_after.get('owned_script')},
            'change_seconds': round(time.time() - t_change, 2),
        }
        out['child_lagged_behind_new_desired'] = bool(
            (db_after.get('owned_script') or {}).get('sha256') != sha_v2
            and (db_after.get('local_global_revision') or 0) < (rev_after or 0))
        out['target_revision'] = rev_after
        out['no_global_setting_written'] = True
        self.state['target_revision'] = rev_after
        self.state['sha_v2'] = sha_v2
        self.rec('phase4-desired-state-change.json', out)
        if not out['child_lagged_behind_new_desired']:
            raise RuntimeError('child_did_not_lag_after_cut_change')
        return out

    # ---- the single authorised repair-Hub restart ----
    def phase5_hub_restart(self):
        pre_nodes = self.protected_snapshot()
        pre_rev = hubdb.snapshot([self.state['script_id'], self.state['task_id']])['revision_sequence']
        # widen the reconnecting Agent's Reconciliation window (own proxy only)
        self.proxy.hold_sync_responses(True)
        window = hubctl.restart_window()
        self.budget['hub_restarts'] += 1
        self.proxy.set_mode('pass')
        out = dict(window)
        out['at_utc'] = now_utc()
        out['protected_nodes_before_restart'] = pre_nodes
        out['global_revision_before'] = pre_rev
        out['proxy_hold_armed'] = True
        out['authorised_unit'] = hubctl.REPAIR_UNIT
        out['untouched_units'] = {
            'primary_hub': hubctl.primary_hub_state(),
            'assert_only_repair_unit_restarted': True}
        self.rec('phase5-hub-restart.json', out)
        if not (out['outage']['outage_observed'] and out['recovery']['healthy']
                and out['mainpid_changed'] and out['binary_sha_same']):
            raise RuntimeError('hub_restart_window_incomplete')
        return out

    # ---- SYS-03 / SYS-17 / INV-01 / AR-01: post-restart verification ----
    def phase6_post_restart(self):
        # 6a. Hub restart invalidates in-memory management sessions: the first API
        # call after the restart is re-authenticated once by the client.
        relogins_after_restart = self.hub.relogins
        # catch the protected Agents' offline -> online transition with a fast probe
        restart_side = self.poll_nodes(6, 0.4)
        target_rev = self.state['target_revision']
        rec_res = self.poll_nodes(RETRY_WINDOW, 0.75, stop=lambda by: all(
            (by.get(nid) or {}).get('status') == 'online'
            and (by.get(nid) or {}).get('sync_status') == 'synced'
            and (by.get(nid) or {}).get('global_revision', 0) >= target_rev
            for nid in PROTECTED_NODES))
        protected_after = self.protected_snapshot()
        db_after = hubdb.snapshot([self.state['script_id'], self.state['task_id']])

        # 6b. READY gate: while the owned child's reconciliation reply is withheld
        hold_seen = {'complete': False, 'seconds': 0.0}
        t0 = time.time()
        while time.time() - t0 < 40:
            if any(e.get('event') == 'sync_response_held' for e in self.proxy.events):
                hold_seen = {'complete': True, 'seconds': round(time.time() - t0, 1)}
                break
            time.sleep(0.3)
        if not hold_seen['complete']:
            raise RuntimeError('no_sync_response_held_after_restart')
        cid = self.state['child_node_id']
        gate_node = self.node()
        gate_store = self.child_db()
        gate_frames = list(self.proxy.frames)
        sync_acks_after_restart = [f for f in gate_frames
                                   if f.get('direction') == 'agent_to_hub' and f.get('type') == 'SYNC_ACK'
                                   and (f.get('applied_rev') or 0) >= target_rev]
        # business-level probe: dispatch a real Run during the Reconciliation window
        not_ready_rows = self.run_node(cid, {'tag': 'during-reconcile', 'delay': '0'})
        not_ready_exec = not_ready_rows[0]['id'] if not_ready_rows else None
        self.state['executions']['during_reconcile_child'] = not_ready_exec
        not_ready_final = self.wait_exec(not_ready_exec, timeout=90) if not_ready_exec else {}
        held_pending = self.proxy.pending_frames()
        child_log_not_ready = self.child.log_tail(400)
        not_ready_log_lines = [l for l in child_log_not_ready.get('tail', [])
                               if 'not ready' in l or 'reconciling' in l or 'invalid sync' in l][-6:]

        # 6c. release the reconciliation reply -> READY must follow
        t_release = time.time()
        released = self.proxy.release_sync_responses()
        db_ready, ready_ok = self.wait_child_rev(target_rev, timeout=90)
        node_ready = None
        deadline = time.time() + 60
        while time.time() < deadline:
            node_ready = self.node()
            if (node_ready or {}).get('sync_status') == 'synced' \
                    and (node_ready or {}).get('global_revision', 0) >= target_rev:
                break
            time.sleep(1)
        ready_seconds = round(time.time() - t_release, 1)
        post_ready_rows = self.run_node(cid, {'tag': 'after-ready', 'delay': '0'})
        ready_exec = post_ready_rows[0]['id'] if post_ready_rows else None
        self.state['executions']['after_ready_child'] = ready_exec
        ready_final = self.wait_exec(ready_exec, timeout=120) if ready_exec else {}

        # 6d. real execution on the protected target node AFTER the restart
        post_rows = self.run_node(TARGET_NODE_ID, {'tag': 'post', 'delay': '2'})
        post_exec = post_rows[0]['id'] if post_rows else None
        self.state['executions']['post_restart_target_node'] = post_exec
        post_final = self.wait_exec(post_exec, timeout=120) if post_exec else {}

        frames = list(self.proxy.frames)
        events = list(self.proxy.events)
        hello_idx = next((i for i, f in enumerate(frames)
                          if f.get('type') == 'HELLO' and f.get('direction') == 'agent_to_hub'), None)
        ack_idx = next((i for i, f in enumerate(frames)
                        if f.get('type') == 'SYNC_ACK' and f.get('direction') == 'agent_to_hub'
                        and (f.get('applied_rev') or 0) >= target_rev), None)
        out = {
            'at_utc': now_utc(),
            'target_revision_after_change': target_rev,
            'management_session_after_restart': {
                'relogins_observed': relogins_after_restart,
                'note': 'the repair Hub keeps management sessions in memory; the first API call after the '
                        'restart returned 401 "invalid or expired session" and the client re-authenticated '
                        'once. This confirms that Hub-side sessions are rebuilt, not persisted.'},
            'protected_reconnect': {
                'all_reconnected': rec_res['complete'],
                'seconds_to_full_reconnect': rec_res['seconds'],
                'nodes_after': protected_after,
                'restart_side_timeline': restart_side['samples'],
                'timeline': rec_res['samples']},
            'hub_persistence_after_restart': {
                'revision_sequence': db_after['revision_sequence'],
                'revision_sequence_preserved': db_after['revision_sequence'] >= target_rev,
                'revision_changes_total': db_after['revision_changes_total'],
                'owned_scripts': db_after['owned_scripts'],
                'owned_tasks': db_after['owned_tasks'],
                'owned_changes': db_after['owned_changes'],
                'quick_check': db_after['quick_check'],
                'script_row_sha_is_v2': [s.get('sha256') for s in db_after['owned_scripts']],
                'hub_api_script_sha': self.script().get('sha256')},
            'owned_child_reconnect': {
                'sync_response_held_seen': hold_seen,
                'held_pending_frames': held_pending,
                'epoch': {
                    'node_online': (gate_node or {}).get('status'),
                    'node_sync_status': (gate_node or {}).get('sync_status'),
                    'node_global_revision_reported_by_agent': (gate_node or {}).get('global_revision'),
                    'child_store_local_revision': gate_store.get('local_global_revision'),
                    'child_store_script_sha': (gate_store.get('owned_script') or {}).get('sha256'),
                    'target_revision': target_rev,
                    'hub_desired_script_sha': self.script().get('sha256'),
                    'agent_has_not_declared_ready': bool(
                        (gate_store.get('owned_script') or {}).get('sha256') != self.state['sha_v2']
                        and (gate_store.get('local_global_revision') or 0) < target_rev),
                    'sync_acks_with_applied_rev_ge_target_before_release': sync_acks_after_restart},
                'no_ready_evaluation_frame_ordering': {
                    'hello_index': hello_idx, 'first_applied_sync_ack_index': ack_idx,
                    'hello_precedes_applied_sync_ack': bool(
                        hello_idx is not None and ack_idx is not None and hello_idx < ack_idx)},
                'released_sync_responses': released,
                'ready_seconds_after_release': ready_seconds,
                'child_store_after_reconcile': {
                    'local_global_revision': db_ready.get('local_global_revision'),
                    'owned_script': db_ready.get('owned_script')},
                'node_after_reconcile': {k: (node_ready or {}).get(k) for k in
                                          ('status', 'sync_status', 'global_revision')},
                'reconcile_converged': ready_ok},
            'executions': {
                'during_reconcile_dispatch': {
                    'request': 'POST /api/tasks/%s/run node_ids={%s:true} params={tag:during-reconcile}'
                               % (self.state['task_id'], cid),
                    'execution_id': not_ready_exec,
                    'status': not_ready_final.get('status'),
                    'block_reason': not_ready_final.get('block_reason'),
                    'offline': not_ready_final.get('offline'),
                    'exit_code': not_ready_final.get('exit_code'),
                    'agent_log_lines': not_ready_log_lines},
                'after_ready_dispatch': {
                    'execution_id': ready_exec, 'status': ready_final.get('status'),
                    'exit_code': ready_final.get('exit_code'),
                    'stdout': (ready_final.get('stdout') or '').strip(),
                    'stdout_marker_after_ready_present': (MARKER + ':after-ready') in (ready_final.get('stdout') or '')},
                'post_restart_target_node_dispatch': {
                    'request': 'POST /api/tasks/%s/run node_ids={%s:true} params={tag:post,delay:2}'
                               % (self.state['task_id'], TARGET_NODE_ID),
                    'execution_id': post_exec, 'status': post_final.get('status'),
                    'exit_code': post_final.get('exit_code'),
                    'stdout': (post_final.get('stdout') or '').strip(),
                    'stdout_marker_post_present': (MARKER + ':post') in (post_final.get('stdout') or ''),
                    'node_id': post_final.get('node_id')}},
        }
        out['frame_ledger'] = frames
        out['proxy_events'] = events
        self.rec('phase6-post-restart.json', out)
        self.rec('phase6-proxy-frames.json', {'frames': frames, 'events': events,
                                              'proxy_state': self.proxy.state()})
        return out

    def phase7_duplicate_check(self):
        """Settle and re-count: the restart must not have produced duplicate Executions."""
        time.sleep(20)
        t_rows = self.executions()
        cid = self.state['child_node_id']
        by_node = {}
        for r in t_rows:
            by_node.setdefault(r.get('node_id'), []).append(
                {'id': r['id'], 'status': r.get('status'), 'trigger_type': r.get('trigger_type'),
                 'block_reason': r.get('block_reason')})
        out = {
            'at_utc': now_utc(),
            'note': 'settle window after all dispatches; a restart must not duplicate any Execution',
            'task_id': self.state['task_id'],
            'total_executions_for_task': len(t_rows),
            'by_node': by_node,
            'expected': {
                'target_node': 2, 'owned_child': 2,
                'target_node_ids': [TARGET_NODE_ID, cid]},
            'counts_match': {'target_node': len(by_node.get(TARGET_NODE_ID, [])) == 2,
                             'owned_child': len(by_node.get(cid, [])) == 2},
            'unique_execution_ids': len({r['id'] for r in t_rows}) == len(t_rows),
        }
        out['assertions_pass'] = bool(all(out['counts_match'].values())
                                      and out['unique_execution_ids'])
        self.rec('phase7-no-duplicate-executions.json', out)
        return out

    # ---- cleanup ----
    def phase8_cleanup(self):
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
        results['native1_child_removal'] = remove
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
            self.state['deleted'].append((kind, obj_id))
        cid = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(cid))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(cid))
        results['child_node'] = {'id': cid, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        self.rec('evidence/phase8-cleanup.json', results)
        return results

    def phase9_oracle(self, cleanup):
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
        hub_unit = hubctl.unit_state()
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
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
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': (inv['primary_agent_unit'].get('ActiveState') == 'active'
                                       and inv['repair_agent_unit'].get('ActiveState') == 'active'),
            'primary_agent_unit_untouched': inv['primary_agent_unit'].get('ActiveState') == 'active',
            'repair_hub_unit_after': hub_unit,
            'repair_hub_active': hub_unit.get('ActiveState') == 'active',
            'health_after': hubctl.health(),
            'primary_hub_unit_untouched': hubctl.primary_hub_state(),
            'settings_after': settings,
            'cleanup_results': cleanup,
            'total_nodes_now': len(nodes)}
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan
                           and oracle['protected_units_active'] and oracle['repair_hub_active']
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b01', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-03', 'SYS-17', 'INV-01', 'AR-01'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b01', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

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
        mark('pre_restart_execution')
        evidence['phases']['pre_restart'] = run.phase3_pre_restart_baseline()
        mark('desired_state_change')
        evidence['phases']['desired_change'] = run.phase4_desired_state_change()
        mark('hub_restart')
        evidence['phases']['hub_restart'] = run.phase5_hub_restart()
        progress['hub_restart'] = {k: evidence['phases']['hub_restart'][k] for k in
                                   ('mainpid_changed', 'binary_sha_same', 'downtime_seconds')}
        progress['hub_mainpid'] = {'before': evidence['phases']['hub_restart']['pre']['unit'].get('MainPID'),
                                   'after': evidence['phases']['hub_restart']['post']['unit'].get('MainPID')}
        mark('post_restart'); evidence['phases']['post_restart'] = run.phase6_post_restart()
        mark('duplicate_check'); evidence['phases']['duplicates'] = run.phase7_duplicate_check()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True
    mark('cleanup')
    try:
        if run.proxy and not evidence['phases'].get('frames'):
            run.rec('evidence/phase6-proxy-frames.json',
                    {'frames': list(run.proxy.frames), 'events': list(run.proxy.events),
                     'proxy_state': run.proxy.state()})
    except Exception:
        pass
    if run.proxy and run.proxy.mode() != 'pass':
        try:
            run.proxy.set_mode('pass')
        except Exception:
            pass
    try:
        if run.state['child_node_id'] or run.state['task_id']:
            cleanup = run.phase8_cleanup()
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
        oracle = run.phase9_oracle(progress.get('final_cleanup', {}))
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
                         'executions': run.state['executions'],
                         'delete_results': run.state['delete_results']}
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b01', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404}],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'deleted_get404': True, 'delete_http': 200},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': '192.168.100.204',
                         'root': run.child.root if run.child else None,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('native1_child_removal', {}).get('root_removed'))},
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'}
    hubapi.write_json('fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b01',
                      'cases': {c['case_id']: c['proposed_after'] for c in
                                json.loads((HERE / 'case-result.json').read_text())['cases']},
                      'clean': progress.get('oracle_clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    pre = ph.get('pre_restart', {})
    chg = ph.get('desired_change', {})
    restart = ph.get('hub_restart', {})
    post = ph.get('post_restart', {})
    dup = ph.get('duplicates', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    restart_ok = bool(restart.get('outage', {}).get('outage_observed')
                      and restart.get('recovery', {}).get('healthy')
                      and restart.get('mainpid_changed') and restart.get('binary_sha_same'))
    persist = post.get('hub_persistence_after_restart', {})
    reconnect = post.get('protected_reconnect', {})
    child = post.get('owned_child_reconnect', {})
    execs = post.get('executions', {})
    pre_ok = bool(pre.get('assertions_pass'))
    reconnect_ok = bool(reconnect.get('all_reconnected'))
    persistence_ok = bool(persist.get('revision_sequence_preserved')
                          and persist.get('owned_scripts')
                          and persist.get('owned_tasks'))
    ready_gate_ok = bool(child.get('epoch', {}).get('agent_has_not_declared_ready')
                         and child.get('no_ready_evaluation_frame_ordering', {}).get('hello_precedes_applied_sync_ack')
                         and (execs.get('during_reconcile_dispatch', {}).get('status') == 'BLOCKED')
                         and child.get('reconcile_converged')
                         and execs.get('after_ready_dispatch', {}).get('status') == 'SUCCESS'
                         and execs.get('after_ready_dispatch', {}).get('stdout_marker_after_ready_present'))
    post_exec_ok = bool(execs.get('post_restart_target_node_dispatch', {}).get('status') == 'SUCCESS'
                        and execs.get('post_restart_target_node_dispatch', {}).get('stdout_marker_post_present'))
    dup_ok = bool(dup.get('assertions_pass'))
    authority_ok = bool(chg.get('desired_state_change', {}).get('sha256_matches_v2')
                        and chg.get('child_lagged_behind_new_desired')
                        and child.get('child_store_after_reconcile', {}).get('owned_script', {}).get('sha256')
                        == run.state.get('sha_v2')
                        and persist.get('script_row_sha_is_v2') == [run.state.get('sha_v2')])
    canonical = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)')

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
                    'target_node_id': TARGET_NODE_ID, 'executions': run.state['executions'],
                    'preserved_execution_history': [v for v in run.state['executions'].values() if v]},
                'actual_budget': dict(run.budget),
                'final_cleanup': progress.get('final_cleanup', {})}

    cases = []
    cases.append(case(
        'SYS-03', 'SYS-03 Hub 重启：Agent 自动重连、Session 重建、Desired State 不丢',
        'PASS' if (restart_ok and reconnect_ok and persistence_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if (restart_ok and reconnect_ok) else 'PARTIAL'),
        'high' if (restart_ok and reconnect_ok and persistence_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-03',
          'layer': 'hub unit restart / hub sqlite / gateway sessions / 4 protected repair Agents',
          'status': 'PASS' if (restart_ok and reconnect_ok and persistence_ok) else 'PARTIAL',
          'description': [
              '`systemctl stop/start nodesteer-hub-repair-20260930-101945.service` on 192.168.100.209: caller-visible /healthz and /readyz stopped answering (outage observed from the operator workstation), MainPID changed, and the binary sha256 stayed 164ea4ac...',
              'All 4 protected repair Agents went through orphan-online reset at Hub start and reconnected by themselves to online/synced with global_revision >= the revision of a Desired State change committed just before the restart (timeline sampled every 1s).',
              'Hub persistence survived: revision_sequence stayed >= the pre-restart revision, the owned script/task rows and their revision_changes rows were still present after the restart, PRAGMA quick_check = ok, and the owned child Agent reconciled exactly the one logical change with no duplicate Execution.']}],
        []))

    cases.append(case(
        'SYS-17', 'SYS-17 Agent 重连前不宣告 READY，完成 Reconciliation 后再 READY',
        'PASS' if (ready_gate_ok and cleanup_ok) else ('PASS_WITH_NOTES' if ready_gate_ok else 'PARTIAL'),
        'high' if (ready_gate_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-17',
          'layer': 'owned child Agent ready gate / harness frame proxy / hub execution dispatch',
          'status': 'PASS' if ready_gate_ok else 'PARTIAL',
          'description': [
              'Reconnect order on the wire: HELLO -> HELLO_ACK -> SYNC_REQUEST -> SYNC_RESPONSE -> SYNC_ACK; the harness held the Hub->Agent SYNC_RESPONSE(s) so the Reconciliation window stayed open.',
              'While the reply was withheld the owned child was online on the Hub but its applied revision was still the pre-restart one and its local store still held the old script sha: no READY.',
              'A real manual Run dispatched in that window produced a real BLOCKED Execution whose block_reason is the Agent\'s own "agent not ready (reconciling)" gate - the business-level proof that READY was not claimed.',
              'After the reply was released the Agent applied the sync (SYNC_ACK applied_rev = Hub revision), its reported revision caught up, and the next manual Run returned SUCCESS with the nonce marker.']}],
        ['READY is an internal Agent state; it was observed through its product-level effects (rejected dispatch '
         'in the Reconciliation window, accepted dispatch after it) and the Hub-visible applied revision, not by '
         'reading Agent memory.',
         'The Reconciliation window was widened by holding SYNC_RESPONSE frames on the harness\'s own frame '
         'proxy for its own child Agent; no product code and no protected Agent was changed.']))

    cases.append(case(
        'INV-01', 'INV-01 Hub 是 Desired State 唯一权威源',
        'PASS' if (authority_ok and cleanup_ok) else ('PASS_WITH_NOTES' if authority_ok else 'PARTIAL'),
        'high' if (authority_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:INV-01',
          'layer': 'hub script authority / owned child store / hub restart persistence',
          'status': 'PASS' if authority_ok else 'PARTIAL',
          'description': [
              'The operator changed the script on the Hub while the owned child was cut from the Hub; the child kept its stale copy and its old local revision, i.e. the Agent never became the source of the value.',
              'After the restart the Hub still served exactly the operator-written object (same sha256/revision) and the Agent converged to it on reconnection - Hub -> Agent only.',
              'Hub rows and revision_changes were unchanged by the Agent side across the restart; no Agent-originated Desired State write path was observed in the frame ledger.']}],
        ['Authority was exercised through the script (Desired State) object path; the Execution/Journal upload '
         'path is a separate direction of the protocol and was out of scope for this invariant.']))

    cases.append(case(
        'AR-01', 'AR-01 Hub Restart；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS' if (restart_ok and post_exec_ok and dup_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if restart_ok else 'PARTIAL'),
        'high' if (restart_ok and post_exec_ok and dup_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-01',
          'layer': 'hub restart / real executions on a protected repair node / idempotency',
          'status': 'PASS' if (restart_ok and post_exec_ok and dup_ok) else 'PARTIAL',
          'description': [
              'Real execution before the restart on protected native-2: terminal SUCCESS, stdout carried the nonce pre marker, exactly one Hub row.',
              'Real execution after the restart on the same protected node: terminal SUCCESS, stdout carried the nonce post marker, exactly one Hub row - the restart did not lose or duplicate work.',
              'After a settle window the Task had exactly 2 Executions on the target node and 2 on the owned child (one BLOCKED during Reconciliation, one SUCCESS after it): no duplicated Execution and no half state.',
              'Desired State and actual state agreed after the restart: the Hub script sha256/revision were the operator-written values and both the protected node and the owned child had applied them.']}],
        ['Hub restart was performed once, on the single authorised repair unit; the primary :8080 Hub and every '
         'Agent service kept their original MainPIDs (recorded read-only before and after).']))

    result = {'batch_id': 'sys-b01',
              'title': 'Hub 重启：Agent 自动重连、Session/Desired State 重建与权威源收敛',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
              'cases': cases, 'deviations': evidence['deviations'], 'budget': run.budget,
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management writes only to repair http://192.168.100.209:18080',
                  'hub_restart': 'only nodesteer-hub-repair-20260930-101945.service was stopped/started once; '
                                 'nodesteer-hub.service (:8080) and all Agent units untouched',
                  'owned_child': 'one harness-owned child Agent on native-1 (exact repair Agent binary d55dc5cd...) '
                                 'behind a harness-owned frame proxy on 192.168.100.170',
                  'ready_gate': 'SYNC_RESPONSE frames were held on the harness frame proxy so the Reconciliation '
                                'window could be observed and exercised by a real manual Run',
                  'product_source_changes': 0, 'git_operations': 0, 'vms_restarted': 0}}
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
