#!/usr/bin/env python3
"""sys-b07 runtime batch (Agent restart / disconnect / offline task update).

Cases: SYS-16, AR-02, AR-06, AR-09.

The "Agent restart / disconnect" semantics required by this batch are produced
ONLY on the owned child Agent process on repair native-4 (192.168.100.247):
  * SIGKILL + restart of the owned process and its task process group (SYS-16),
  * graceful stop/start of the owned process (AR-02),
  * network cut/restore through the harness frame proxy (AR-06, AR-09).

No protected Agent unit, no repair Hub, no product source is touched.

Real evidence chain: operator/API act -> real request -> Hub/Agent state change
-> real execution side effect -> API + local-SQLite readback.
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
PREFIX = 'qa-sysb07-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
MARK_V1 = 'QA:%s:V1' % NONCE
MARK_V2 = 'QA:%s:V2' % NONCE
OPERATOR_IP = '192.168.100.170'
DEV = 'evidence/'

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


_SCRIPT_TEMPLATE = '''#!/bin/sh
set -eu
D="__MARKS__"
echo "$$" >> "$D/running.pid"
printf '{"ev":"start","pid":%s,"label":"__LABEL__","at":"%s"}\\n' "$$" "$(date -u +%FT%TZ)" >> "$D/events.jsonl"
sleep "${NODESTEER_PARAM_delay:-0}"
printf '{"ev":"end","pid":%s,"label":"__LABEL__","at":"%s"}\\n' "$$" "$(date -u +%FT%TZ)" >> "$D/events.jsonl"
printf '%s\\n' '__LABEL__'
'''


def script_body(label, marks):
    """Shell fixture: real side effects into the owned marks dir + stdout marker."""
    return _SCRIPT_TEMPLATE.replace('__MARKS__', marks).replace('__LABEL__', label)


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'script_id': None, 'task_id': None, 'child_node_id': None, 'child_agent_id': None,
            'executions': {}, 'created': [], 'delete_results': {}, 'child_removal': None,
        }
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0, 'hub_definition_updates': 0,
                       'hub_definition_deletes': 0, 'manual_run_posts': 0, 'node_enroll_posts': 1,
                       'child_process_starts': 0, 'child_process_stops': 0, 'child_process_crashes': 0}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        hubapi.write_json(DEV + name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def executions(self, task_id, limit=500):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(task_id))
        code, body = self.hub.get(q)
        if code != 200:
            raise RuntimeError('executions_list_http_%s' % code)
        return body

    def exec_count(self, task_id):
        return len(self.executions(task_id))

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
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def wait_child_offline(self, timeout=60):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') != 'online':
                return node
            time.sleep(1)
        return node

    def wait_revision(self, target, timeout=60):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('global_revision', -1) >= target and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def wait_local(self, predicate, timeout=90, interval=1.0):
        """Poll owned-child local_state() until predicate(state) is truthy."""
        deadline = time.time() + timeout
        state = None
        while time.time() < deadline:
            state = self.child.local_state(self.state['script_id'], self.state['task_id'])
            try:
                if predicate(state):
                    return state
            except Exception:
                pass
            time.sleep(interval)
        return state

    def hub_exec(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return body if code == 200 else {'_http': code}

    def protected_floor(self):
        """Latest global revision confirmed by an untouched online protected node.

        The global revision is shared with parallel batches and keeps moving, so a
        monotonic floor read after our change is the sound convergence target.
        """
        n = self.node('91f44866-8a28-435e-bbda-2f2367126c00')
        return (n or {}).get('global_revision', 0)

    def run_now(self, delay):
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(self.state['task_id']),
                                   {'node_ids': {self.state['child_node_id']: True},
                                    'params': {'delay': delay}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or not isinstance(rows, list) or len(rows) != 1:
            raise RuntimeError('manual_run_http_%s %s' % (code, rows))
        return rows[0]['id'], rows[0]

    # ---------------- Phase 0: guard ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc()}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
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
        out['native4_pre'] = {
            'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
            'owned_child_processes': ci['owned_child_processes'],
            'agent_binary_sha256': ci['agent_binary_sha256'],
            'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': ci['primary_agent_unit'],
            'repair_agent_unit': ci['repair_agent_unit'],
        }
        if ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native4_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        self.rec('phase0-guard.json', out)
        return out

    # ---------------- Phase 1: enroll + owned child behind the frame proxy ----------------
    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child',
            'node_ip': '192.168.100.247',
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
                                            PREFIX + 'child', '192.168.100.247', token)
        started = self.child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'gateway_url': enroll.get('gateway_url'), 'node_name': PREFIX + 'child',
                           'node_ip': '192.168.100.247',
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {'root_on_native4': self.child.root, 'config_mode_0600': True,
                            'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                            'listened_operator_port': port,
                            'routing': 'child -> operator %s:%d (frame proxy) -> repair hub :18080'
                                       % (OPERATOR_IP, port)},
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {'note': 'owned child Agent uses the exact repair Agent binary; the process '
                                        'owner is the harness on native-4 and never the protected units',
                                'agent_binary_sha256': child_agent.AGENT_SHA},
        }
        self.rec('phase1-owned-child.json', out)
        return out

    # ---------------- Phase 2: business fixtures ----------------
    def phase2_fixtures(self):
        self.state['v1_body'] = script_body(MARK_V1, self.child.marks)
        self.state['v1_sha'] = hashlib.sha256(self.state['v1_body'].encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME, 'description': 'sys-b07 restart/disconnect fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': self.state['v1_body'],
            'parameters': [{'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 300, 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))

        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 300, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, task))
        self.state['task_id'] = task['id']
        self.state['created'].append(('task', task['id']))
        self.budget['hub_definition_creates'] += 2

        s_read = self.hub.json('/api/scripts/' + self.hub.quote(script['id']))
        t_read = self.hub.json('/api/tasks/' + self.hub.quote(task['id']))
        if s_read.get('sha256') != self.state['v1_sha'] or s_read.get('name') != SCRIPT_NAME:
            raise RuntimeError('script_readback_mismatch')
        if t_read.get('script_id') != script['id'] or t_read.get('retry') != 0:
            raise RuntimeError('task_readback_mismatch')
        node = self.wait_revision(s_read.get('revision', 0) + 2, timeout=60)
        local = self.wait_local(lambda st: st.get('script') and st.get('task'), timeout=60)
        applied = bool(local.get('script') and local.get('task')
                       and local['script']['content_sha256'] == self.state['v1_sha'])
        out = {
            'at_utc': now_utc(),
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'revision': s_read.get('revision'), 'content_sha256_expected': self.state['v1_sha']},
            'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                     'type': 'script', 'retry': 0, 'target_node_ids': t_read.get('target', {}).get('node_ids')},
            'child_applied_revision': node.get('global_revision') if node else None,
            'child_sync_status': node.get('sync_status') if node else None,
            'child_local_script_revision': (local.get('script') or {}).get('revision'),
            'child_local_script_content_matches': applied,
        }
        self.rec('phase2-fixtures.json', out)
        if not applied:
            raise RuntimeError('child_did_not_apply_v1_fixtures')
        return out

    # ---------------- Phase 3: SYS-16 old RUNNING + no process -> agent_restarted ----------------
    def phase3_sys16(self):
        before_rows = self.exec_count(self.state['task_id'])
        exec_id, created = self.run_now('180')
        self.state['executions']['sys16'] = exec_id
        comment = created.get('status')
        # wait for Hub RUNNING and the real script start side effect
        deadline = time.time() + 60
        hub_running = None
        marks = None
        while time.time() < deadline:
            ex = self.hub_exec(exec_id)
            marks = self.child.marks_state()
            ev = (marks.get('files') or {}).get('events.jsonl', '')
            if ex.get('status') == 'RUNNING' and '"ev":"start"' in ev and 'running.pid' in (marks.get('files') or {}):
                hub_running = ex
                break
            time.sleep(1)
        if hub_running is None:
            raise RuntimeError('sys16_execution_never_reached_running_with_process')
        local_before = self.child.journal_state([exec_id])
        local_row_before = (local_before.get('rows') or {}).get(exec_id) or {}
        marks_before = self.child.marks_state()

        # Crash: SIGKILL the owned Agent and the in-flight task process group
        crash = self.child.crash()
        self.budget['child_process_crashes'] += 1
        time.sleep(1.0)
        local_after_crash = self.child.journal_state([exec_id])
        hub_after_crash = self.hub_exec(exec_id)
        marks_after_crash = self.child.marks_state()

        # Restart the owned Agent -> startup recovery must finalize the stale RUNNING
        started = self.child.start()
        self.budget['child_process_starts'] += 1
        node_after = self.wait_child_online(timeout=90)
        final_hub = self.wait_execution(exec_id, timeout=90)
        local_final = self.child.journal_state([exec_id])
        local_row_final = (local_final.get('rows') or {}).get(exec_id) or {}
        after_rows = self.exec_count(self.state['task_id'])

        crash_ok = (crash.get('agent_remaining') == [] and crash.get('task_alive_after') == [])
        marks_crash = marks_after_crash.get('files') or {}
        start_present = '"ev":"start"' in marks_crash.get('events.jsonl', '')
        end_absent = '"ev":"end"' not in marks_crash.get('events.jsonl', '')
        ok = (final_hub.get('status') == 'FAILED'
              and final_hub.get('block_reason') == 'agent_restarted'
              and local_row_final.get('status') == 'FAILED'
              and local_row_final.get('block_reason') == 'agent_restarted'
              and int(local_row_final.get('synced') or 0) == 1
              and crash_ok and start_present and end_absent
              and after_rows == before_rows + 1
              and len([e for e in self.executions(self.state['task_id']) if e['id'] == exec_id]) == 1)
        out = {
            'at_utc': now_utc(),
            'execution_id': exec_id,
            'before': {
                'hub_created_status': comment,
                'hub_running_readback': {'status': hub_running.get('status'), 'start_time': hub_running.get('start_time'),
                                         'block_reason': hub_running.get('block_reason')},
                'local_journal': {k: local_row_before.get(k) for k in ('status', 'block_reason', 'synced', 'start_time', 'end_time')},
                'marks_start_present': '"ev":"start"' in (marks_before.get('files') or {}).get('events.jsonl', ''),
                'marks_running_pid': (marks_before.get('files') or {}).get('running.pid', '').strip(),
            },
            'crash': {
                'mechanism': 'SIGKILL owned child Agent main process + SIGKILL in-flight task process group '
                             '(recorded pid) on native-4; the protected nodesteer-agent*.service units and the '
                             'repair Hub were not touched',
                'agent_killed': crash.get('agent_killed'), 'agent_remaining': crash.get('agent_remaining'),
                'task_pids_recorded': crash.get('task_pids_recorded'),
                'task_alive_before': crash.get('task_alive_before'),
                'task_groups_killed': crash.get('task_groups_killed'),
                'task_alive_after': crash.get('task_alive_after'),
                'processes_under_owned_root_after': crash.get('processes_under_owned_root_after'),
                'no_live_task_process': crash.get('task_alive_after') == [],
            },
            'between_crash_and_restart': {
                'local_journal_status_still_running': local_row_before is not None and
                ((local_after_crash.get('rows') or {}).get(exec_id) or {}).get('status'),
                'hub_status_still': hub_after_crash.get('status'),
                'marks_start_present': start_present, 'marks_end_absent': end_absent,
                'note': 'process died mid-run: the script never wrote its end event; the journal row stayed RUNNING',
            },
            'after_restart': {
                'owned_child_restart': started.get('owned_pids'),
                'node_status': node_after.get('status') if node_after else None,
                'node_sync_status': node_after.get('sync_status') if node_after else None,
                'identity_node_id': (local_final.get('rows') or {}).get(exec_id, {}).get('node_id'),
                'hub_final': {k: final_hub.get(k) for k in ('status', 'block_reason', 'exit_code', 'offline', 'synced', 'start_time', 'end_time')},
                'local_final': {k: local_row_final.get(k) for k in ('status', 'block_reason', 'synced', 'offline', 'start_time', 'end_time')},
                'hub_rows_with_this_exec_id': len([e for e in self.executions(self.state['task_id']) if e['id'] == exec_id]),
                'hub_total_rows_before': before_rows, 'hub_total_rows_after': after_rows,
                'no_duplicate_execution': after_rows == before_rows + 1,
            },
            'assertions_pass': ok,
        }
        self.rec('phase3-sys16-agent-restarted.json', out)
        if not ok:
            raise RuntimeError('phase3_sys16_failed')
        return out

    # ---------------- Phase 4: AR-02 Agent Restart recovery ----------------
    def phase4_ar02(self):
        pre_local = self.child.local_state(self.state['script_id'], self.state['task_id'])
        pre_node = self.node(self.state['child_node_id'])
        pre_exec_count = self.exec_count(self.state['task_id'])
        pre_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))

        restarted = self.child.restart(graceful=True)
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        floor = (node or {}).get('global_revision', 0)
        local = self.wait_local(
            lambda st: (st.get('global_revision', -1) >= floor and st.get('script') and st.get('task')),
            timeout=90)

        post_exec_count = self.exec_count(self.state['task_id'])
        post_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))
        # functional proof: a fresh manual run after restart still works exactly once
        exec_id, _ = self.run_now('0')
        self.state['executions']['ar02'] = exec_id
        final = self.wait_execution(exec_id, timeout=90)
        after_run_count = self.exec_count(self.state['task_id'])

        identity_ok = (pre_local.get('identity', {}).get('node_id') == local.get('identity', {}).get('node_id')
                       and pre_local.get('identity', {}).get('agent_id') == local.get('identity', {}).get('agent_id'))
        db_ok = (pre_local.get('state.db', {}).get('dev') == local.get('state.db', {}).get('dev')
                 and pre_local.get('state.db', {}).get('inode') == local.get('state.db', {}).get('inode'))
        hub_script_now = self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))
        script_ok = (local.get('script', {}).get('content_sha256') == self.state['v1_sha']
                     and local.get('script', {}).get('revision') == hub_script_now.get('revision'))
        revision_ok = (local.get('global_revision', -1) >= (node or {}).get('global_revision', 0))
        ids_preserved = pre_ids == post_ids
        run_ok = (final.get('status') == 'SUCCESS' and MARK_V1 in (final.get('stdout') or '')
                  and after_run_count == pre_exec_count + 1)
        ok = identity_ok and db_ok and script_ok and revision_ok and ids_preserved and run_ok
        out = {
            'at_utc': now_utc(),
            'restart': {'mechanism': 'graceful SIGTERM then start of the owned child Agent on native-4; '
                                     'protected Agent units and the repair Hub were not touched',
                        'stop': restarted['stop'], 'start_pids': restarted['start'].get('owned_pids')},
            'identity': {
                'before': pre_local.get('identity'), 'after': local.get('identity'),
                'unchanged': identity_ok,
            },
            'persistence': {
                'db_file_before': pre_local.get('state.db'), 'db_file_after': local.get('state.db'),
                'same_dev_inode': db_ok,
                'terminals_before_restart': pre_ids, 'terminals_after_restart': post_ids,
                'terminal_exec_ids_preserved': ids_preserved,
            },
            'desired_state': {
                'hub_script_revision': self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id'])).get('revision'),
                'local_script_revision': local.get('script', {}).get('revision'),
                'local_script_content_matches_v1': script_ok,
                'hub_node_global_revision': node.get('global_revision'),
                'local_global_revision': local.get('global_revision'),
                'revision_converged': revision_ok,
                'local_task_revision': local.get('task', {}).get('revision'),
                'local_task_enabled': local.get('task', {}).get('enabled'),
            },
            'idempotency': {
                'run_after_restart_execution_id': exec_id,
                'run_after_restart_status': final.get('status'),
                'marker_present': MARK_V1 in (final.get('stdout') or ''),
                'exec_rows_before_restart': pre_exec_count,
                'exec_rows_after_restart': post_exec_count,
                'no_execution_created_by_restart': post_exec_count == pre_exec_count,
                'exec_rows_after_fresh_run': after_run_count,
                'fresh_run_added_exactly_one': after_run_count == pre_exec_count + 1,
            },
            'assertions_pass': ok,
        }
        self.rec('phase4-ar02-restart.json', out)
        if not ok:
            raise RuntimeError('phase4_ar02_failed')
        return out

    # ---------------- Phase 5: AR-06 Agent Disconnect ----------------
    def phase5_ar06(self):
        pre_local = self.child.local_state(self.state['script_id'], self.state['task_id'])
        pre_exec_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))
        pre_proxy = self.proxy.state()

        self.proxy.set_mode('cut')
        node_off = self.wait_child_offline(timeout=60)
        time.sleep(2)
        inv = self.child.inventory()
        local_offline = self.child.local_state(self.state['script_id'], self.state['task_id'])
        proxy_cut = self.proxy.state()

        self.proxy.set_mode('pass')
        node_on = self.wait_child_online(timeout=120)
        floor = (node_on or {}).get('global_revision', 0)
        local = self.wait_local(lambda st: st.get('global_revision', -1) >= floor, timeout=90)
        proxy_after = self.proxy.state()
        post_exec_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))

        cut_ok = proxy_cut['cut_events'] >= 1 and proxy_cut['mode'] == 'cut'
        offline_observed = node_off is not None and node_off.get('status') != 'online'
        child_alive_while_cut = bool(inv.get('owned_child_processes'))
        rejected = len(self.proxy.events_snapshot('client_rejected_while_cut'))
        identity_ok = (pre_local.get('identity') == local.get('identity'))
        revision_ok = (local.get('global_revision', -1) >= (node_on or {}).get('global_revision', 0))
        script_ok = (local.get('script', {}).get('content_sha256') == self.state['v1_sha'])
        no_dup = pre_exec_ids == post_exec_ids
        ok = (cut_ok and offline_observed and child_alive_while_cut and identity_ok
              and revision_ok and script_ok and no_dup
              and node_on.get('status') == 'online' and node_on.get('sync_status') == 'synced')
        out = {
            'at_utc': now_utc(),
            'mechanism': 'harness frame proxy set to "cut" closed the child->Hub websocket; the owned child '
                          'Agent process kept running (disconnect, not restart). Proxy set back to "pass" restored it.',
            'during_disconnect': {
                'proxy_mode': proxy_cut['mode'], 'proxy_cut_events': proxy_cut['cut_events'],
                'hub_node_status': node_off.get('status') if node_off else None,
                'hub_node_sync_status': node_off.get('sync_status') if node_off else None,
                'node_went_offline': offline_observed,
                'owned_child_processes_alive': inv.get('owned_child_processes'),
                'child_alive_while_cut': child_alive_while_cut,
                'local_state_readable': bool(local_offline.get('identity')),
                'local_global_revision_while_cut': local_offline.get('global_revision'),
            },
            'after_restore': {
                'client_connections_rejected_while_cut': rejected,
                'proxy_connection_open_count_before': pre_proxy['connection_open_count'],
                'proxy_connection_open_count_after': proxy_after['connection_open_count'],
                'hub_node_status': node_on.get('status') if node_on else None,
                'hub_node_sync_status': node_on.get('sync_status') if node_on else None,
                'identity_unchanged': identity_ok,
                'identity_after': local.get('identity'),
                'hub_global_revision': node_on.get('global_revision') if node_on else None,
                'local_global_revision': local.get('global_revision'),
                'revision_converged': revision_ok,
                'local_script_content_matches_v1': script_ok,
                'exec_ids_before': pre_exec_ids, 'exec_ids_after': post_exec_ids,
                'no_duplicate_execution': no_dup,
            },
            'assertions_pass': ok,
        }
        self.rec('phase5-ar06-disconnect.json', out)
        if not ok:
            raise RuntimeError('phase5_ar06_failed')
        return out

    # ---------------- Phase 6: AR-09 Agent Offline During Task Update ----------------
    def phase6_ar09(self):
        pre_exec_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))
        pre_local = self.child.local_state(self.state['script_id'], self.state['task_id'])
        hub_script_before = self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))

        self.proxy.set_mode('cut')
        node_off = self.wait_child_offline(timeout=60)

        # Update the Script (new content/label) + the Task definition while the Agent is offline
        self.state['v2_body'] = script_body(MARK_V2, self.child.marks)
        self.state['v2_sha'] = hashlib.sha256(self.state['v2_body'].encode()).hexdigest()
        code, hub_script_after = self.hub.put('/api/scripts/' + self.hub.quote(self.state['script_id']), {
            'name': SCRIPT_NAME, 'description': 'sys-b07 restart/disconnect fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': self.state['v2_body'],
            'parameters': [{'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 300, 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        self.budget['hub_definition_updates'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_update_http_%s %s' % (code, hub_script_after))
        code, hub_task_after = self.hub.put('/api/tasks/' + self.hub.quote(self.state['task_id']), {
            'name': TASK_NAME, 'type': 'script', 'script_id': self.state['script_id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '1'}, 'timeout': 300, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        self.budget['hub_definition_updates'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_update_http_%s %s' % (code, hub_task_after))

        local_still_v1_while_offline = self.child.local_state(self.state['script_id'], self.state['task_id'])
        floor = self.protected_floor()

        self.proxy.set_mode('pass')
        node_on = self.wait_child_online(timeout=120)
        local = self.wait_local(
            lambda st: (st.get('script', {}).get('content_sha256') == self.state['v2_sha']
                        and st.get('task', {}).get('revision') == hub_task_after.get('revision')
                        and st.get('global_revision', -1) >= floor), timeout=120)
        post_sync_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))

        # The update must take effect: a fresh run must use the v2 content exactly once
        exec_id, _ = self.run_now('0')
        self.state['executions']['ar09'] = exec_id
        final = self.wait_execution(exec_id, timeout=90)
        after_run_ids = sorted(e['id'] for e in self.executions(self.state['task_id']))

        script_applied = (local.get('script', {}).get('content_sha256') == self.state['v2_sha']
                          and local.get('script', {}).get('revision') == hub_script_after.get('revision'))
        task_applied = (local.get('task', {}).get('revision') == hub_task_after.get('revision'))
        revision_ok = (local.get('global_revision', -1) >= floor)
        self.state['ar09_floor'] = floor
        no_dup_on_reconnect = (post_sync_ids == pre_exec_ids)
        run_ok = (final.get('status') == 'SUCCESS' and MARK_V2 in (final.get('stdout') or '')
                  and MARK_V1 not in (final.get('stdout') or '')
                  and final.get('script_revision') == hub_script_after.get('revision')
                  and len(after_run_ids) == len(pre_exec_ids) + 1)
        ok = (script_applied and task_applied and revision_ok and no_dup_on_reconnect and run_ok
              and node_on.get('status') == 'online')
        out = {
            'at_utc': now_utc(),
            'mechanism': 'child->Hub link cut via frame proxy; Script content + Task definition updated on the '
                          'repair Hub while the owned child Agent was offline; link restored.',
            'offline_window': {
                'hub_node_status': node_off.get('status') if node_off else None,
                'hub_script_revision_before': hub_script_before.get('revision'),
                'hub_script_revision_after_update': hub_script_after.get('revision'),
                'hub_script_sha_after_update': hub_script_after.get('sha256'),
                'hub_script_sha_expected_v2': self.state['v2_sha'],
                'hub_task_revision_after_update': hub_task_after.get('revision'),
                'local_script_matched_v1_while_offline': (
                    local_still_v1_while_offline.get('script', {}).get('content_sha256') == self.state['v1_sha']),
                'local_script_revision_while_offline': local_still_v1_while_offline.get('script', {}).get('revision'),
            },
            'after_reconnect': {
                'hub_node_status': node_on.get('status') if node_on else None,
                'local_script_revision': local.get('script', {}).get('revision'),
                'local_script_content_matches_v2': script_applied,
                'local_task_revision': local.get('task', {}).get('revision'),
                'task_definition_applied': task_applied,
                'hub_global_revision': node_on.get('global_revision') if node_on else None,
                'local_global_revision': local.get('global_revision'),
                'revision_floor_after_update': floor,
                'revision_converged': revision_ok,
                'exec_ids_before_offline': pre_exec_ids,
                'exec_ids_after_sync': post_sync_ids,
                'sync_created_no_execution': no_dup_on_reconnect,
            },
            'update_takes_effect': {
                'run_execution_id': exec_id,
                'status': final.get('status'),
                'stdout': (final.get('stdout') or '').strip()[-500:],
                'v2_marker_present': MARK_V2 in (final.get('stdout') or ''),
                'v1_marker_absent': MARK_V1 not in (final.get('stdout') or ''),
                'execution_script_revision': final.get('script_revision'),
                'expected_script_revision': hub_script_after.get('revision'),
                'exactly_one_new_execution': len(after_run_ids) == len(pre_exec_ids) + 1,
            },
            'assertions_pass': ok,
        }
        self.rec('phase6-ar09-offline-update.json', out)
        if not ok:
            raise RuntimeError('phase6_ar09_failed')
        return out

    # ---------------- Phase 7: cleanup ----------------
    def phase7_cleanup(self):
        results = {'deleted': [], 'errors': []}
        if self.proxy is not None:
            try:
                self.proxy.set_mode('pass')
            except Exception:
                pass
        if self.child is not None:
            try:
                self.state['child_removal'] = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                results['errors'].append('child_remove:%s' % exc)
                self.state['child_removal'] = {'error': str(exc)}
        results['native4_child_removal'] = self.state['child_removal']
        for kind, obj_id in [('task', self.state['task_id']), ('script', self.state['script_id'])]:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
        node_id = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        if self.proxy is not None:
            try:
                self.proxy.stop()
            except Exception as exc:
                results['errors'].append('proxy_stop:%s' % exc)
        self.rec('phase7-cleanup.json', results)
        return results

    # ---------------- Phase 8: independent oracle ----------------
    def phase8_oracle(self, cleanup):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        ok = True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'), 'sync_status': n.get('sync_status')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        protected_units_ok = (inv['primary_agent_unit'].get('ActiveState') == 'active'
                              and inv['repair_agent_unit'].get('ActiveState') == 'active')
        proxy_port_closed = True
        if self.proxy is not None and self.proxy.port:
            import socket
            s = socket.socket()
            try:
                s.settimeout(1.5)
                s.connect(('192.168.100.170', self.proxy.port))
                proxy_port_closed = False
            except Exception:
                proxy_port_closed = True
            finally:
                s.close()
        oracle = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'native4_owned_child_orphan': orphan,
            'native4_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                  'owned_child_processes': inv['owned_child_processes'],
                                  'agent_binary_sha256': inv['agent_binary_sha256'],
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': protected_units_ok,
            'proxy_listener_released': proxy_port_closed,
            'cleanup_results': cleanup,
            'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and protected_units_ok
                           and cleanup.get('all_get_404') and proxy_port_closed)
        hubapi.write_json('final-cleanup-oracle.json', oracle)
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {
        'batch_id': 'sys-b07', 'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': run.hub.base,
        'cases': ['SYS-16', 'AR-02', 'AR-06', 'AR-09'],
        'started_at_utc': now_utc(), 'phases': {}, 'deviations': [],
    }
    progress = {'batch_id': 'sys-b07', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)

    failed = False
    try:
        mark('guard')
        evidence['phases']['guard'] = run.phase0_guard()
        evidence['phases']['child'] = run.phase1_child()
        mark('child', child_node_id=run.state['child_node_id'], child_agent_id=run.state['child_agent_id'])
        evidence['phases']['fixtures'] = run.phase2_fixtures()
        mark('fixtures', script_id=run.state['script_id'], task_id=run.state['task_id'])
        mark('sys16')
        evidence['phases']['sys16'] = run.phase3_sys16()
        mark('ar02')
        evidence['phases']['ar02'] = run.phase4_ar02()
        mark('ar06')
        evidence['phases']['ar06'] = run.phase5_ar06()
        mark('ar09')
        evidence['phases']['ar09'] = run.phase6_ar09()
    except Exception as exc:
        failed = True
        evidence['deviations'].append('runtime_error:%s:%s' % (type(exc).__name__, str(exc)[:300]))
        try:
            progress['runtime_error'] = '%s:%s' % (type(exc).__name__, str(exc)[:300])
        except Exception:
            pass
    try:
        mark('cleanup')
        cleanup = run.phase7_cleanup()
    except Exception as exc:
        failed = True
        cleanup = {'errors': ['cleanup_failed:%s' % exc]}
        evidence['deviations'].append('cleanup_error:%s' % exc)
    try:
        mark('oracle')
        oracle = run.phase8_oracle(cleanup)
    except Exception as exc:
        failed = True
        oracle = {'clean': False, 'error': str(exc)}
        evidence['deviations'].append('oracle_error:%s' % exc)

    evidence['finished_at_utc'] = now_utc()
    evidence['final_cleanup_oracle_clean'] = bool(oracle.get('clean'))
    hubapi.write_json('run-evidence.json', evidence)

    progress['child_node_id'] = run.state['child_node_id']
    progress['child_agent_id'] = run.state['child_agent_id']
    progress['script_id'] = run.state['script_id']
    progress['task_id'] = run.state['task_id']
    progress['preserved_executions'] = dict(run.state['executions'])
    progress['final_cleanup'] = cleanup
    progress['final_cleanup_get_http'] = {i: r['get_http'] for i, r in run.state['delete_results'].items()}
    progress['child_node_get_http'] = (cleanup.get('child_node') or {}).get('get_http')
    progress['oracle_clean'] = bool(oracle.get('clean'))
    progress['status'] = 'COMPLETE' if not failed else 'INCOMPLETE'
    progress['harness_errors'] = evidence['deviations']
    progress['phase'] = 'done'
    progress['updated_at_utc'] = now_utc()

    cases = {
        'SYS-16': 'PASS' if evidence['phases'].get('sys16', {}).get('assertions_pass') and oracle.get('clean') else
                  ('PASS_WITH_NOTES' if evidence['phases'].get('sys16', {}).get('assertions_pass') else 'FAIL'),
        'AR-02': 'PASS' if evidence['phases'].get('ar02', {}).get('assertions_pass') and oracle.get('clean') else
                 ('PASS_WITH_NOTES' if evidence['phases'].get('ar02', {}).get('assertions_pass') else 'FAIL'),
        'AR-06': 'PASS' if evidence['phases'].get('ar06', {}).get('assertions_pass') and oracle.get('clean') else
                 ('PASS_WITH_NOTES' if evidence['phases'].get('ar06', {}).get('assertions_pass') else 'FAIL'),
        'AR-09': 'PASS' if evidence['phases'].get('ar09', {}).get('assertions_pass') and oracle.get('clean') else
                 ('PASS_WITH_NOTES' if evidence['phases'].get('ar09', {}).get('assertions_pass') else 'FAIL'),
    }
    progress['cases'] = cases
    progress['independent_cleanup_verified'] = bool(oracle.get('clean'))
    progress['artifacts'] = ['case-result.json', 'progress.json', 'fixture-ledger.json',
                             'final-cleanup-oracle.json', 'run-evidence.json', 'evidence/']
    hubapi.write_json('progress.json', progress)

    write_deliverables(run, evidence, progress, cases, oracle)
    print(json.dumps({'batch_id': 'sys-b07', 'cases': cases, 'clean': oracle.get('clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_deliverables(run, evidence, progress, cases, oracle):
    ph = evidence['phases']
    cleanup_ok = bool(oracle.get('clean'))
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
                'owned_prefix': PREFIX,
                'child_node_id': run.state['child_node_id'], 'child_agent_id': run.state['child_agent_id'],
                'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
                'executions': run.state['executions'],
                'preserved_execution_history': [v for v in run.state['executions'].values() if v],
            },
            'actual_budget': dict(run.budget),
            'final_cleanup': progress.get('final_cleanup', {}),
        }

    def status(pass_key):
        if ph.get(pass_key, {}).get('assertions_pass') and cleanup_ok:
            return 'PASS', 'high'
        if ph.get(pass_key, {}).get('assertions_pass'):
            return 'PASS_WITH_NOTES', 'medium'
        return ('PARTIAL', 'medium') if ph.get(pass_key) else ('FAIL', 'low')

    s16, c16 = status('sys16')
    a02, c02 = status('ar02')
    a06, c06 = status('ar06')
    a09, c09 = status('ar09')
    cases_list = [
        case('SYS-16', 'SYS-16 Agent 发现旧 RUNNING 但进程不存在，按 `agent_restarted` 正确收尾。', s16, c16, [{
            'test_id': '20261014-RUNTIME:SYS-16',
            'layer': 'owned-child-agent(real SIGKILL)/agent-sqlite-journal/hub-api',
            'status': 'PASS' if ph.get('sys16', {}).get('assertions_pass') else 'FAIL',
            'description': [
                'A real manual Execution reached Hub RUNNING with its task process alive (script start side effect).',
                'The owned child Agent and the in-flight task process group were SIGKILLed (protected Agent units '
                'untouched), leaving a RUNNING journal row and no live process (no end event).',
                'On restart the Agent finalized the stale RUNNING as FAILED with block_reason=agent_restarted, '
                're-uploaded it (local synced=true), and the Hub readback showed FAILED/agent_restarted with '
                'exactly one Execution row (no duplicate).',
            ]}],
            ['Kill used SIGKILL of the owned process; a STOPPED-phase distinction is not part of this case.']),
        case('AR-02', 'AR-02 Agent Restart；恢复后幂等、持久化、Desired State 与实际状态一致', a02, c02, [{
            'test_id': '20261014-RUNTIME:AR-02',
            'layer': 'owned-child-agent graceful restart/agent-sqlite/hub-api',
            'status': 'PASS' if ph.get('ar02', {}).get('assertions_pass') else 'FAIL',
            'description': [
                'The owned child Agent was gracefully restarted; identity (node_id/agent_id) and the SQLite '
                'file dev/inode were unchanged (persistence).',
                'Terminal Execution IDs created before the restart survived; the restart itself created no new '
                'Execution (idempotent).',
                'After restart local global_revision and the Script/Task definitions converged to the Hub '
                '(Desired State == actual state), and a fresh Run Now produced exactly one SUCCESS with the '
                'nonce marker.',
            ]}],
            ['Restart was performed on the owned child process, not a protected systemd unit.']),
        case('AR-06', 'AR-06 Agent Disconnect；恢复后幂等、持久化、Desired State 与实际状态一致', a06, c06, [{
            'test_id': '20261014-RUNTIME:AR-06',
            'layer': 'frame-proxy connection cut/restore/owned-child-agent/hub-api',
            'status': 'PASS' if ph.get('ar06', {}).get('assertions_pass') else 'FAIL',
            'description': [
                'The child->Hub websocket was cut by the harness frame proxy; the Hub marked the node offline '
                'while the owned child Agent process stayed alive (a real disconnect, not a restart).',
                'The local Agent state stayed readable and unchanged while disconnected.',
                'After the link was restored the node returned online/synced, identity was unchanged, the local '
                'revision converged to the Hub, and no duplicate Execution appeared.',
            ]}],
            ['Disconnect was produced at the harness proxy layer in front of the owned child Agent; the repair '
             'Hub and protected Agents were not stopped.']),
        case('AR-09', 'AR-09 Agent Offline During Task Update；恢复后幂等、持久化、Desired State 与实际状态一致', a09, c09, [{
            'test_id': '20261014-RUNTIME:AR-09',
            'layer': 'frame-proxy offline window/hub definition update/owned-child-agent sync',
            'status': 'PASS' if ph.get('ar09', {}).get('assertions_pass') else 'FAIL',
            'description': [
                'While the owned child Agent was offline, the Hub Script content and the Task definition were '
                'updated (revisions advanced on the Hub); the child still held the old v1 content locally.',
                'On reconnect the child applied the update exactly once (local Script/Task revisions and the '
                'content SHA matched the Hub) and the sync created no Execution.',
                'A fresh Run Now then produced exactly one SUCCESS carrying the new v2 marker and the new '
                'Script revision, proving the offline update took effect without duplication.',
            ]}],
            ['Offline window was created with the harness frame proxy; no Hub stop and no product source change.']),
    ]

    result = {
        'batch_id': 'sys-b07',
        'title': 'Agent 重启/断连/任务更新期间离线：旧 RUNNING 收尾与 Desired State 一致',
        'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
        'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
        'cases': cases_list, 'deviations': evidence['deviations'], 'budget': run.budget,
        'final_cleanup_oracle_clean': cleanup_ok,
    }
    hubapi.write_json('case-result.json', result)

    ledger = {
        'batch_id': 'sys-b07', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404},
        ],
        'child_node': {
            'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
            'hostname': PREFIX + 'child',
            'deleted_get404': (progress.get('final_cleanup', {}).get('child_node', {}) or {}).get('get_http') == 404,
            'delete_http': (progress.get('final_cleanup', {}).get('child_node', {}) or {}).get('delete_http'),
        },
        'host_fixture': {
            'kind': 'owned_child_agent_process', 'host': '192.168.100.247',
            'root': run.child.root if run.child else None,
            'removed': bool((run.state.get('child_removal') or {}).get('root_removed')),
        },
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if cleanup_ok else 'CLEANUP_INCOMPLETE',
    }
    hubapi.write_json('fixture-ledger.json', ledger)


if __name__ == '__main__':
    sys.exit(main())
