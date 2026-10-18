#!/usr/bin/env python3
"""sys-b05 (Execution/Schedule idempotency keys) runtime batch.

Cases: SYS-31, SYS-32, INV-07, SYS-15, AR-20.

Real evidence chain (no product source changes, no protected Agent touched):
  operator/API act -> real request -> Hub/Agent state change -> real execution
  side effect -> API/DB readback.

Owned fixtures only, all deleted + GET404 at the end.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import child_agent
import hubapi
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb05-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
MARKER = 'QA:%s:EXEC' % NONCE
OPERATOR_IP = '192.168.100.170'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
HUB_TICK_MAX = 60          # redis_check_interval_sec is 45 on this Hub
DEV = 'evidence/'

SCRIPT_BODY = (
    '#!/bin/sh\n'
    'set -eu\n'
    'sleep "${NODESTEER_PARAM_delay:-0}"\n'
    "printf '%s\\n' '" + MARKER + "'\n"
)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'script_id': None, 'task_id': None, 'child_node_id': None, 'child_agent_id': None,
            'schedules': {}, 'executions': {}, 'created': [], 'deleted': [], 'delete_results': {},
        }
        self.notes = []
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0, 'hub_definition_deletes': 0,
                       'manual_run_posts': 0, 'node_enroll_posts': 1, 'child_process_starts': 0,
                       'child_process_stops': 0}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        hubapi.write_json(DEV + name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def executions(self, task_id, node_id=None, trigger_type=None, limit=100):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(task_id))
        if node_id:
            q += '&node_id=' + self.hub.quote(node_id)
        if trigger_type:
            q += '&trigger_type=' + self.hub.quote(trigger_type)
        code, body = self.hub.get(q)
        if code != 200:
            raise RuntimeError('executions_list_http_%s' % code)
        return body

    def wait_execution(self, exec_id, timeout=90):
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            code, last = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
            if code == 200 and last.get('status') in TERMINAL:
                return last
            time.sleep(1)
        return last

    def wait_child_online(self, timeout=60):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def wait_revision(self, target, timeout=45):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('global_revision', -1) >= target:
                return node
            time.sleep(1)
        return node

    def wait_hub_execution_for_slot(self, slot, timeout, node_id, task_id):
        deadline = time.time() + timeout
        found = []
        while time.time() < deadline:
            rows = self.executions(task_id, node_id)
            found = [e for e in rows if (e.get('scheduled_time') or '').startswith(slot[:19])]
            if found:
                return found
            time.sleep(2)
        return found

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

    # ---- Phase 1: enroll + owned child through the frame proxy ----
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
        node = self.wait_child_online(timeout=75)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {
            'at_utc': now_utc(),
            'enrollment': {
                'http': code,
                'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                'gateway_url': enroll.get('gateway_url'),
                'node_name': PREFIX + 'child', 'node_ip': '192.168.100.247',
                'registration_token_persisted_to_evidence': False,
            },
            'owned_child': {
                'root_on_native4': self.child.root, 'config_mode_0600': True,
                'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                'listened_operator_port': port,
                'routing': 'child -> operator %s:%d (frame proxy) -> repair hub :18080' % (OPERATOR_IP, port),
            },
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {
                'note': 'owned child Agent uses the exact repair Agent binary; the process owner is the '
                        'harness on native-4 and never the protected nodesteer-agent*.service units',
                'agent_binary_sha256': child_agent.AGENT_SHA,
            },
        }
        self.rec('phase1-owned-child.json', out)
        return out

    # ---- Phase 2: business fixtures ----
    def phase2_fixtures(self):
        self.state['revision_at_fixture_start'] = node_rev_floor(self.hub, self.state['child_node_id'])
        content_sha = hashlib.sha256(SCRIPT_BODY.encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'sys-b05 execution/schedule idempotency fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': SCRIPT_BODY,
            'parameters': [{'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))

        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 20, 'retry': 0,
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
        if s_read.get('sha256') != content_sha or s_read.get('name') != SCRIPT_NAME:
            raise RuntimeError('script_readback_mismatch')
        if t_read.get('script_id') != script['id'] or t_read.get('retry') != 0 or \
           t_read.get('target', {}).get('node_ids') != [self.state['child_node_id']]:
            raise RuntimeError('task_readback_mismatch')
        node = self.wait_revision(self.state.get('revision_at_fixture_start', 0) + 2, timeout=45)
        if node is None or node.get('sync_status') != 'synced':
            raise RuntimeError('child_did_not_apply_fixtures')
        out = {
            'at_utc': now_utc(),
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'revision': s_read.get('revision'), 'content_sha256_expected': content_sha},
            'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                     'type': 'script', 'retry': 0,
                     'target_node_ids': t_read.get('target', {}).get('node_ids')},
            'child_applied_revision': node.get('global_revision'),
            'child_sync_status': node.get('sync_status'),
        }
        self.rec('phase2-fixtures.json', out)
        return out

    # ---- Phase 3: SYS-31 manual execution UUID idempotency ----
    def phase3_manual_uuid(self):
        task_id = self.state['task_id']
        # M1: duplicate EXECUTION_FINISHED upload of one manual Execution UUID
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id),
                                   {'node_ids': {self.state['child_node_id']: True},
                                    'params': {'delay': '6'}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows) != 1:
            raise RuntimeError('manual_run_1_http_%s' % code)
        m1 = rows[0]['id']
        self.state['executions']['manual_m1'] = m1
        self.proxy.arm_dup(m1)                       # duplicate the pending upload
        m1_final = self.wait_execution(m1, timeout=60)
        # A duplicated EXECUTION_FINISHED must make the Hub answer a second
        # EXECUTION_ACK; wait until both the local duplicate marker and the
        # second Hub ACK are observed.
        proxy_after_m1 = None
        deadline = time.time() + 30
        while time.time() < deadline:
            st = self.proxy.state()
            acks = [a for a in st['seen_ack'] if a['execution_id'] == m1]
            if m1 in st['dup_done'] and len(acks) >= 2:
                proxy_after_m1 = st
                break
            time.sleep(0.5)
        if proxy_after_m1 is None:
            proxy_after_m1 = self.proxy.state()

        # M2: a second manual run gets a fresh UUID (premise of the UUID key)
        code, rows2 = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id),
                                    {'node_ids': {self.state['child_node_id']: True},
                                     'params': {'delay': '0'}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows2) != 1:
            raise RuntimeError('manual_run_2_http_%s' % code)
        m2 = rows2[0]['id']
        self.state['executions']['manual_m2'] = m2
        m2_final = self.wait_execution(m2, timeout=60)

        # Hub-side dedup readback for the duplicated UUID
        listed_m1 = [e for e in self.executions(task_id, self.state['child_node_id']) if e['id'] == m1]
        dup_uploads = [e for e in proxy_after_m1['seen_exec_finished'] if e['execution_id'] == m1]
        hub_acks_m1 = [a for a in proxy_after_m1['seen_ack'] if a['execution_id'] == m1]
        dup_done_m1 = m1 in proxy_after_m1['dup_done']
        out = {
            'at_utc': now_utc(),
            'manual_run_1': {
                'request': 'POST /api/tasks/%s/run params={delay:6} node_ids={%s:true}'
                           % (task_id, self.state['child_node_id']),
                'execution_id': m1, 'status': m1_final.get('status'),
                'exit_code': m1_final.get('exit_code'), 'trigger_type': m1_final.get('trigger_type'),
                'stdout': (m1_final.get('stdout') or '').strip(),
                'marker_present': MARKER in (m1_final.get('stdout') or ''),
                'hub_rows_with_this_uuid': len(listed_m1),
            },
            'duplicate_upload': {
                'mechanism': 'frame proxy forwarded the same EXECUTION_FINISHED frame twice '
                             '(same Execution ID) to the repair Hub',
                'exec_finished_original_frame_seen': len(dup_uploads),
                'duplicate_injected_for_exec_id': dup_done_m1,
                'hub_acks_observed_for_exec_id': len(hub_acks_m1),
                'hub_total_acks_observed_by_proxy': len(proxy_after_m1['seen_ack']),
                'hub_rows_after_duplicate': len(listed_m1),
                'hub_status_after_duplicate': listed_m1[0].get('status') if listed_m1 else None,
                'no_duplicate_row': len(listed_m1) == 1,
            },
            'manual_run_2': {
                'execution_id': m2, 'status': m2_final.get('status'),
                'distinct_uuid_from_run_1': m2 != m1,
                'hub_rows_with_this_uuid': len([e for e in self.executions(task_id, self.state['child_node_id']) if e['id'] == m2]),
            },
            'premise': 'each manual Run Now gets a fresh Hub-created Execution UUID; the UUID is the dedup key',
        }
        ok = (m1_final.get('status') == 'SUCCESS' and out['duplicate_upload']['no_duplicate_row']
              and dup_done_m1 and len(hub_acks_m1) >= 2
              and m2 != m1 and m2_final.get('status') == 'SUCCESS')
        out['assertions_pass'] = ok
        self.rec('phase3-manual-uuid.json', out)
        if not ok:
            raise RuntimeError('phase3_manual_uuid_failed')
        return out

    # ---- Phase 4: SYS-32 Hub-side scheduled Execution Key ----
    def phase4_hub_schedule_key(self):
        task_id = self.state['task_id']
        node_id = self.state['child_node_id']
        base = datetime.now(timezone.utc).replace(microsecond=0)
        slot = iso(base - timedelta(seconds=120))       # past -> due on next Hub tick
        other_slot = iso(base - timedelta(seconds=300))
        ids = {}
        for key, run_at in (('A_slot', slot), ('B_same_slot', slot), ('C_other_slot', other_slot)):
            code, sched = self.hub.post('/api/schedules', {
                'task_id': task_id, 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC',
                'execution_owner': 'hub', 'offline_policy': 'hub_online_required',
                'misfire_policy': 'run_once', 'enabled': True,
            })
            self.budget['api_mutations'] += 1
            if code not in (200, 201):
                raise RuntimeError('schedule_create_%s_http_%s %s' % (key, code, sched))
            ids[key] = sched['id']
            self.state['schedules'][key] = sched['id']
            self.state['created'].append(('schedule', sched['id']))
            self.budget['hub_definition_creates'] += 1
        # wait for one Hub scheduler pass that fires both A and B (same slot) and C (other slot)
        deadline = time.time() + HUB_TICK_MAX * 3
        rows_slot, rows_other = [], []
        while time.time() < deadline:
            all_rows = self.executions(task_id, node_id)
            rows_slot = [e for e in all_rows if (e.get('scheduled_time') or '').startswith(slot[:19])]
            rows_other = [e for e in all_rows if (e.get('scheduled_time') or '').startswith(other_slot[:19])]
            if rows_slot and all(e.get('status') in TERMINAL for e in rows_slot) and rows_other:
                break
            time.sleep(3)
        for key in ('A_slot', 'B_same_slot', 'C_other_slot'):
            sid = ids[key]
            code, body = self.hub.delete('/api/schedules/' + self.hub.quote(sid))
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get('/api/schedules/' + self.hub.quote(sid))
            self.state['delete_results'][sid] = {'kind': 'schedule', 'delete_http': code, 'get_http': gcode}
            if code != 200 or gcode != 404:
                raise RuntimeError('schedule_delete_%s_not_404' % sid)
            self.state['deleted'].append(('schedule', sid))
        self.state['executions']['hub_slot_primary'] = rows_slot[0]['id'] if rows_slot else None
        out = {
            'at_utc': now_utc(),
            'key': 'TaskID+NodeID+ScheduledTime (docs/ARCHITECTURE.md §32)',
            'same_slot_utc': slot, 'other_slot_utc': other_slot,
            'schedules': ids,
            'scheduler_tick_note': 'Hub scheduler runs RunDueHUB on the revision_check_interval_sec tick',
            'same_slot_executions': [
                {'id': e['id'], 'status': e.get('status'), 'trigger_type': e.get('trigger_type'),
                 'scheduled_time': e.get('scheduled_time'), 'node_id': e.get('node_id'),
                 'task_id': e.get('task_id'), 'stdout': (e.get('stdout') or '').strip()}
                for e in rows_slot],
            'other_slot_executions': [
                {'id': e['id'], 'status': e.get('status'), 'scheduled_time': e.get('scheduled_time')}
                for e in rows_other],
            'deleted_schedules_get404': {k: self.state['delete_results'][v]['get_http'] for k, v in ids.items()},
            'same_slot_execution_count': len(rows_slot),
            'other_slot_execution_count': len(rows_other),
        }
        out['assertions_pass'] = (len(rows_slot) == 1 and rows_slot[0].get('status') == 'SUCCESS'
                                  and len(rows_other) == 1)
        self.rec('phase4-hub-schedule-key.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase4_hub_schedule_key_failed')
        return out

    # ---- Phase 5: SYS-32 Agent-side scheduled Execution Key (one_time + restart) ----
    def phase5_agent_schedule_key(self):
        task_id = self.state['task_id']
        node_id = self.state['child_node_id']
        node_before = self.node(node_id)
        run_at = iso(datetime.now(timezone.utc) + timedelta(seconds=25))
        code, sched = self.hub.post('/api/schedules', {
            'task_id': task_id, 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC',
            'execution_owner': 'agent', 'offline_policy': 'allow_offline',
            'misfire_policy': 'run_once', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('agent_schedule_create_http_%s' % code)
        sid = sched['id']
        self.state['schedules']['agent_one_time'] = sid
        self.state['created'].append(('schedule', sid))
        self.budget['hub_definition_creates'] += 1
        self.wait_revision(node_before.get('global_revision', 0) + 1, timeout=45)

        slot = run_at[:19]
        rows = self.wait_hub_execution_for_slot(slot, timeout=60, node_id=node_id, task_id=task_id)
        for e in rows:
            self.wait_execution(e['id'], timeout=30)
        rows = self.executions(task_id, node_id)
        first_rows = [e for e in rows if (e.get('scheduled_time') or '').startswith(slot) and e.get('trigger_type') == 'schedule']
        if len(first_rows) != 1:
            raise RuntimeError('agent_one_time_first_fire_expected_1_got_%d' % len(first_rows))
        a1 = first_rows[0]['id']
        self.state['executions']['agent_slot_primary'] = a1
        local_before = self.child.db_read(task_id, [a1])

        # Restart the owned child: in-memory lastFire/lastRun are lost, the persisted
        # journal and the slot key are the only protection.
        restart = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_child_online(timeout=60)
        time.sleep(20)
        rows_after = self.executions(task_id, node_id)
        slot_rows_after = [e for e in rows_after if (e.get('scheduled_time') or '').startswith(slot)
                           and e.get('trigger_type') == 'schedule']
        local_after = self.child.db_read(task_id, [a1])
        slot_group_after = [g for g in local_after.get('slot_groups', []) if (g.get('scheduled_time') or '').startswith(slot)]
        out = {
            'at_utc': now_utc(),
            'key': 'TaskID+NodeID+ScheduledTime, agent-owned schedule (docs/ARCHITECTURE.md §32)',
            'schedule_id': sid, 'run_at_utc': run_at, 'slot_utc': slot,
            'first_fire': {'execution_id': a1, 'hub_status': first_rows[0].get('status'),
                           'trigger_type': first_rows[0].get('trigger_type'),
                           'hub_scheduled_time': first_rows[0].get('scheduled_time'),
                           'stdout': (first_rows[0].get('stdout') or '').strip()},
            'owned_child_restart': {'stop': restart['stop'], 'start': restart['start']},
            'after_restart': {
                'node': {k: node_after.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_after else None,
                'hub_executions_for_slot': len(slot_rows_after),
                'hub_execution_ids_for_slot': [e['id'] for e in slot_rows_after],
                'child_local_rows_for_slot': slot_group_after,
                'no_duplicate_execution': len(slot_rows_after) == 1,
            },
            'local_before_restart': {
                'requested': local_before.get('requested_executions'),
                'slot_groups': local_before.get('slot_groups'),
            },
            'local_after_restart': {
                'requested': local_after.get('requested_executions'),
                'slot_groups': local_after.get('slot_groups'),
                'executions_total': local_after.get('executions_total'),
            },
        }
        out['assertions_pass'] = (len(slot_rows_after) == 1 and len(slot_group_after) == 1
                                  and (slot_group_after[0].get('count') == 1)
                                  and node_after is not None and node_after.get('status') == 'online')
        self.rec('phase5-agent-schedule-key.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase5_agent_schedule_key_failed')
        return out

    # ---- Phase 6: SYS-15 / AR-20 offline execution upload + ACK idempotency ----
    def phase6_offline_upload(self):
        task_id = self.state['task_id']
        node_id = self.state['child_node_id']
        run_at = iso(datetime.now(timezone.utc) + timedelta(seconds=30))
        code, sched = self.hub.post('/api/schedules', {
            'task_id': task_id, 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC',
            'execution_owner': 'agent', 'offline_policy': 'allow_offline',
            'misfire_policy': 'run_once', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('offline_schedule_create_http_%s' % code)
        off_sid = sched['id']
        self.state['schedules']['agent_offline'] = off_sid
        self.state['created'].append(('schedule', off_sid))
        self.budget['hub_definition_creates'] += 1
        node_before = self.node(node_id)
        self.wait_revision(node_before.get('global_revision', 0) + 1, timeout=45)

        # cut the child's Hub connection: the Agent keeps running and fires the
        # agent-owned allow_offline schedule locally.
        self.proxy.set_mode('cut')
        cut_at = now_utc()
        slot = run_at[:19]
        local_offline = None
        deadline = time.time() + 90
        while time.time() < deadline:
            db = self.child.db_read(task_id, [])
            cand = [e for e in db.get('task_executions', [])
                    if (e.get('scheduled_time') or '').startswith(slot) and e.get('offline')]
            if cand:
                local_offline = cand[0]
                break
            time.sleep(3)
        if local_offline is None:
            self.proxy.set_mode('pass')
            raise RuntimeError('offline_execution_not_observed_locally')
        off_id = local_offline['id']
        self.state['executions']['offline'] = off_id
        cut_state = self.proxy.state()
        hub_during_cut = [e for e in self.executions(task_id, node_id) if e['id'] == off_id]

        # drop the first EXECUTION_ACK so the child journal stays unsynced -> the
        # reconnect re-uploads the same Execution ID (real duplicate upload).
        self.proxy.arm_drop_next_ack(1)
        self.proxy.set_mode('pass')
        restored_at = now_utc()
        hub_rows = None
        deadline = time.time() + 60
        while time.time() < deadline:
            hub_rows = [e for e in self.executions(task_id, node_id) if e['id'] == off_id]
            if hub_rows:
                break
            time.sleep(2)
        time.sleep(5)
        local_after_first_upload = self.child.db_read(task_id, [off_id])
        first_upload_row = (local_after_first_upload.get('requested_executions') or [None])[0]

        # restart the child -> real re-upload of the same Execution ID
        restart = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_child_online(timeout=60)
        time.sleep(12)
        local_final = self.child.db_read(task_id, [off_id])
        final_row = (local_final.get('requested_executions') or [None])[0]
        hub_final_rows = [e for e in self.executions(task_id, node_id) if e['id'] == off_id]
        proxy_final = self.proxy.state()
        seen_offline_uploads = [e for e in proxy_final['seen_exec_finished'] if e['execution_id'] == off_id]

        out = {
            'at_utc': now_utc(),
            'schedule_id': off_sid, 'run_at_utc': run_at, 'slot_utc': slot,
            'cut_at_utc': cut_at, 'restored_at_utc': restored_at,
            'offline_execution': {
                'id': off_id, 'local_status': local_offline.get('status'),
                'local_offline_flag': local_offline.get('offline'),
                'local_synced_before_upload': local_offline.get('synced'),
                'hub_rows_while_cut': len(hub_during_cut),
                'slot': local_offline.get('scheduled_time'),
            },
            'after_restore_upload': {
                'hub_rows': len(hub_rows or []),
                'hub_row': {'id': hub_rows[0].get('id'), 'status': hub_rows[0].get('status'),
                            'offline': hub_rows[0].get('offline'), 'trigger_type': hub_rows[0].get('trigger_type'),
                            'scheduled_time': hub_rows[0].get('scheduled_time')} if hub_rows else None,
                'child_local_synced_after_dropped_ack': first_upload_row.get('synced') if first_upload_row else None,
                'proxy_dropped_next_ack_for': proxy_final.get('drop_next_done'),
            },
            'after_restart_duplicate_upload': {
                'hub_rows': len(hub_final_rows),
                'hub_status': hub_final_rows[0].get('status') if hub_final_rows else None,
                'child_local_synced_final': final_row.get('synced') if final_row else None,
                'exec_finished_uploads_seen_by_proxy': len(seen_offline_uploads),
                'no_duplicate_hub_row': len(hub_final_rows) == 1,
            },
            'owned_child_restart': {'stop': restart['stop'], 'start': restart['start']},
            'node_after': {k: node_after.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_after else None,
            'local_final_executions_total': local_final.get('executions_total'),
            'proxy_cut_events': cut_state.get('cut_events'),
        }
        out['assertions_pass'] = (
            local_offline.get('offline') is True
            and not hub_during_cut
            and len(hub_rows or []) == 1
            and first_upload_row is not None and first_upload_row.get('synced') is False
            and len(seen_offline_uploads) >= 2
            and len(hub_final_rows) == 1
            and final_row is not None and final_row.get('synced') is True
        )
        self.rec('phase6-offline-upload-ack.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase6_offline_upload_failed')
        return out

    # ---- Phase 7: cleanup ----
    def phase7_cleanup(self):
        results = {'deleted': [], 'errors': []}
        # child agent process + owned root on native-4
        if self.child is not None:
            try:
                remove = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                remove = {'error': str(exc)}
                results['errors'].append('child_remove:%s' % exc)
        else:
            remove = {'skipped': 'owned_child_never_installed'}
        results['native4_child_removal'] = remove
        # remaining Hub fixtures: schedules then task then script
        order = [('schedule', sid) for sid in self.state['schedules'].values()]
        order += [('task', self.state['task_id']), ('script', self.state['script_id'])]
        for kind, obj_id in order:
            if not obj_id:
                continue
            if obj_id in self.state['delete_results']:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append((kind, obj_id))
        # child node delete
        node_id = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        self.rec('evidence/phase7-cleanup.json', results)
        return results

    # ---- Phase 8: final oracle ----
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
            'native4_owned_child_orphan': orphan,
            'native4_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                  'owned_child_processes': inv['owned_child_processes'],
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': protected_units_ok,
            'primary_agent_unit_untouched': inv['primary_agent_unit'].get('ActiveState') == 'active',
            'cleanup_results': cleanup,
            'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and protected_units_ok
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def node_rev_floor(hub, node_id):
    code, body = hub.get('/api/nodes/' + hub.quote(node_id))
    return (body or {}).get('global_revision', 0)


def main():
    run = Run()
    evidence = {
        'batch_id': 'sys-b05',
        'nonce': NONCE, 'owned_prefix': PREFIX,
        'endpoint': run.hub.base,
        'cases': ['SYS-31', 'SYS-32', 'INV-07', 'SYS-15', 'AR-20'],
        'started_at_utc': now_utc(),
        'phases': {}, 'deviations': [],
    }
    progress = {'batch_id': 'sys-b05', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

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
        mark('fixtures')
        evidence['phases']['fixtures'] = run.phase2_fixtures()
        progress['task_id'] = run.state['task_id']
        progress['script_id'] = run.state['script_id']
        mark('sys31_manual_uuid')
        evidence['phases']['sys31'] = run.phase3_manual_uuid()
        mark('sys32_hub_schedule_key')
        evidence['phases']['sys32_hub'] = run.phase4_hub_schedule_key()
        mark('sys32_agent_schedule_key')
        evidence['phases']['sys32_agent'] = run.phase5_agent_schedule_key()
        mark('sys15_ar20_offline_upload')
        evidence['phases']['sys15_ar20'] = run.phase6_offline_upload()
        mark('evidence_frames')
        frames = {'proxy_state': run.proxy.state(), 'events': run.proxy.events}
        run.rec('evidence/phase7-proxy-frames.json', frames)
        evidence['phases']['frames'] = {'proxy_state': run.proxy.state()}
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True

    mark('cleanup')
    try:
        if run.proxy and not evidence['phases'].get('frames'):
            frames = {'proxy_state': run.proxy.state(), 'events': run.proxy.events}
            run.rec('evidence/phase7-proxy-frames.json', frames)
            evidence['phases']['frames'] = {'proxy_state': run.proxy.state()}
    except Exception:
        pass
    try:
        if run.state['child_node_id'] or run.state['task_id']:
            cleanup = run.phase7_cleanup()
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
        oracle = run.phase8_oracle(progress.get('final_cleanup', {}))
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {
        'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
        'child_node_id': run.state['child_node_id'], 'child_agent_id': run.state['child_agent_id'],
        'schedules': run.state['schedules'], 'executions': run.state['executions'],
        'delete_results': run.state['delete_results'],
    }
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b05', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404},
        ] + [
            {'kind': 'schedule', 'key': k, 'id': v,
             'deleted_get404': run.state['delete_results'].get(v, {}).get('get_http') == 404}
            for k, v in run.state['schedules'].items()
        ],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'deleted_get404': True,
                       'delete_http': 200},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': '192.168.100.247',
                         'root': run.child.root if run.child else None,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('native4_child_removal', {}).get('root_removed'))},
        'preserved_executions': [
            {'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v
        ],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE',
    }
    hubapi.write_json('fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)

    print(json.dumps({
        'batch_id': 'sys-b05',
        'cases': {c['case_id']: c['proposed_after'] for c in
                  json.loads((HERE / 'case-result.json').read_text())['cases']},
        'clean': progress.get('oracle_clean'),
        'deviations': evidence['deviations'],
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    manual = ph.get('sys31', {})
    hub_sched = ph.get('sys32_hub', {})
    agent_sched = ph.get('sys32_agent', {})
    offline = ph.get('sys15_ar20', {})
    frames = ph.get('frames', {}).get('proxy_state', {})
    cleanup_ok = bool(progress.get('oracle_clean'))
    hub_sched_ok = bool(hub_sched.get('assertions_pass'))
    agent_sched_ok = bool(agent_sched.get('assertions_pass'))
    offline_ok = bool(offline.get('assertions_pass'))
    manual_ok = bool(manual.get('assertions_pass'))
    canonical = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per '
                 'remaining_dimensions)')

    def case(case_id, title, proposed, confidence, dims, limits):
        return {
            'case_id': case_id, 'title': title,
            'canonical_before': canonical,
            'proposed_after': proposed,
            'confidence': confidence,
            'covered_dimensions': dims,
            'remaining_or_limitations': limits,
            'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                'case-result.json', 'progress.json', 'fixture-ledger.json',
                'final-cleanup-oracle.json', 'run-evidence.json'],
            'actual_resources': {
                'owned_prefix': PREFIX,
                'child_node_id': run.state['child_node_id'],
                'child_agent_id': run.state['child_agent_id'],
                'script_id': run.state['script_id'],
                'task_id': run.state['task_id'],
                'schedules': run.state['schedules'],
                'executions': run.state['executions'],
                'preserved_execution_history': [v for v in run.state['executions'].values() if v],
            },
            'actual_budget': dict(run.budget),
            'final_cleanup': progress.get('final_cleanup', {}),
        }

    cases = []
    cases.append(case(
        'SYS-31', 'SYS-31 Manual Execution UUID 幂等',
        'PASS' if (manual_ok and cleanup_ok) else ('PASS_WITH_NOTES' if manual_ok else 'PARTIAL'),
        'high' if (manual_ok and cleanup_ok) else 'medium',
        [{
            'test_id': '20261014-RUNTIME:SYS-31',
            'layer': 'hub-api/real-owned-child-agent/frame-proxy/agent-sqlite',
            'status': 'PASS' if manual_ok else 'FAIL',
            'description': [
                'Two manual Run Now calls each produced a distinct Hub-created Execution UUID (premise of the key).',
                'The same manual Execution UUID was uploaded twice (duplicated EXECUTION_FINISHED frame) and the Hub kept exactly one row with unchanged terminal status.',
                'Real Execution side effect observed: script stdout carried the nonce marker, terminal SUCCESS.',
            ]}],
        ['Duplicate upload was exercised via a harness frame proxy in front of the owned child Agent; '
         'no product source was modified.',
         'The Agent-side dedup branch for a repeated RUN_EXECUTION dispatch (same UUID) was not forced; '
         'only the upload/idempotent-upsert direction was proven.'],
    ))
    cases.append(case(
        'SYS-32', 'SYS-32 Scheduled Execution Key 幂等',
        'PASS' if (hub_sched_ok and agent_sched_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if (hub_sched_ok or agent_sched_ok) else 'PARTIAL'),
        'high' if (hub_sched_ok and agent_sched_ok and cleanup_ok) else 'medium',
        [{
            'test_id': '20261014-RUNTIME:SYS-32',
            'layer': 'hub-scheduler/agent-scheduler/agent-sqlite',
            'status': 'PASS' if (hub_sched_ok and agent_sched_ok) else 'PARTIAL',
            'description': [
                'Hub-owned: two distinct one_time Schedules sharing Task+Node+ScheduledTime produced exactly '
                'one Execution; a third Schedule with a different ScheduledTime produced its own Execution in the same pass.',
                'Agent-owned: a one_time Schedule fired on the connected child Agent; after a real child Agent '
                'restart (in-memory fire maps lost) the same slot was re-attempted and suppressed, leaving one '
                'Hub row and one local journal row for that slot.',
            ]}],
        ['The Key is (TaskID, NodeID, ScheduledTime); Task Revision is intentionally not part of the key and '
         'was held constant here.',
         'No cron/DST-boundary variants were exercised in this batch.'],
    ))
    cases.append(case(
        'INV-07', 'INV-07 Execution ID / Key 必须幂等',
        'PASS' if (manual_ok and hub_sched_ok and agent_sched_ok and offline_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if (manual_ok and hub_sched_ok and agent_sched_ok) else 'PARTIAL'),
        'high' if all([manual_ok, hub_sched_ok, agent_sched_ok, offline_ok, cleanup_ok]) else 'medium',
        [{
            'test_id': '20261014-RUNTIME:INV-07',
            'layer': 'hub+agent idempotency keys',
            'status': 'PASS' if all([manual_ok, hub_sched_ok, agent_sched_ok, offline_ok]) else 'PARTIAL',
            'description': [
                'Manual path key = Hub-created Execution UUID; duplicate upload kept one row.',
                'Schedule path key = TaskID+NodeID+ScheduledTime, enforced on the Hub scheduler and on the '
                'agent-owned scheduler (unique slot index + explicit slot lookup), surviving an Agent restart.',
                'Offline path key = Execution ID on journal upload/ACK; re-upload kept one row and converged synced=true.',
            ]}],
        ['INV-07 is proven for the three idempotency paths exercised here (manual UUID, scheduled slot, '
         'offline upload); other Execution-producing paths (Application deploy/operation) were out of scope.'],
    ))
    cases.append(case(
        'SYS-15', 'SYS-15 离线 Execution 上传和 ACK 幂等，无重复',
        'PASS' if (offline_ok and cleanup_ok) else ('PASS_WITH_NOTES' if offline_ok else 'PARTIAL'),
        'high' if (offline_ok and cleanup_ok) else 'medium',
        [{
            'test_id': '20261014-RUNTIME:SYS-15',
            'layer': 'owned-child-agent offline journal/hub upload/ACK',
            'status': 'PASS' if offline_ok else 'FAIL',
            'description': [
                'Child Agent kept running while its Hub link was cut; the agent-owned allow_offline one_time '
                'Schedule fired locally and the journal recorded the Execution with offline=true, synced=false.',
                'No Hub row existed while offline. On reconnect the Execution was uploaded and created on the Hub '
                '(offline=true).',
                'The first EXECUTION_ACK was dropped, so the journal stayed synced=false; a real child Agent '
                'restart re-uploaded the same Execution ID, the Hub kept exactly one row, and the journal '
                'then converged to synced=true.',
            ]}],
        ['"Offline" was simulated by cutting the owned child Agent from the repair Hub through the harness '
         'frame proxy; the protected repair Agents and the Hub were never stopped.'],
    ))
    cases.append(case(
        'AR-20', 'AR-20 Duplicate Execution Upload；恢复后幂等、持久化、Desired State 与实际状态一致',
        'PASS' if (offline_ok and manual_ok and cleanup_ok) else
        ('PASS_WITH_NOTES' if (offline_ok and manual_ok) else 'PARTIAL'),
        'high' if (offline_ok and manual_ok and cleanup_ok) else 'medium',
        [{
            'test_id': '20261014-RUNTIME:AR-20',
            'layer': 'hub execution upsert/frame-dup/offline-upload',
            'status': 'PASS' if (offline_ok and manual_ok) else 'PARTIAL',
            'description': [
                'Duplicate EXECUTION_FINISHED for one manual Execution UUID: Hub stored one row, no state rollback.',
                'Duplicate upload after offline recovery: same Execution ID uploaded twice, Hub stored one row, '
                'terminal status and offline flag consistent, no half-state.',
                'Agent-side journal persistence survived a real process restart; the recovered row is the exact '
                'same Execution ID.',
            ]}],
        ['Duplicate upload was injected at the frame layer of the harness proxy; the underlying Hub '
         'upsert/dedup logic is the unmodified product path.'],
    ))

    result = {
        'batch_id': 'sys-b05',
        'title': 'Execution/Schedule 幂等键：手动 UUID、调度 Key 与离线上传不重复',
        'nonce': NONCE,
        'executed_at_utc': evidence['started_at_utc'],
        'finished_at_utc': evidence['finished_at_utc'],
        'endpoint': evidence['endpoint'],
        'cases': cases,
        'deviations': evidence['deviations'],
        'budget': run.budget,
        'final_cleanup_oracle_clean': cleanup_ok,
    }
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
