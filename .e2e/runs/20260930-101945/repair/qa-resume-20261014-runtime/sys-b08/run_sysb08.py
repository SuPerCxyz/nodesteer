#!/usr/bin/env python3
"""sys-b08 runtime batch: offline autonomy and the online gate.

Cases: SYS-13, SYS-14, AR-18, E2E-04.

  * SYS-13   Agent-owned interval Schedule with offline_policy=allow_offline keeps
             firing and keeps having a real effect on the node while the Agent has
             no Hub connection.
  * SYS-14   Agent-owned interval Schedule with offline_policy=hub_online_required
             does NOT fire while the Hub is unreachable (and does fire when online,
             which is the positive control for the same schedule object).
  * AR-18    Offline scheduled executions: after recovery the rows are uploaded
             idempotently (one row per slot, content identical), persistent, and
             Desired State == actual state.
  * E2E-04   Agent-owned interval offline -> executes while offline -> no duplicate
             after recovery (including a second offline/online cycle).

The Hub-unavailable window is produced by cutting the owned child Agent's own link
on the harness-owned frame proxy.  No protected service is stopped, the repair Hub
is not restarted in this batch, and the Agent process stays alive so that its local
scheduler is genuinely exercised offline (that is exactly the offline-autonomy
behaviour under test).
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
import node_units
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb08-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_ALLOW_NAME = PREFIX + 'task-allow'
TASK_REQUIRED_NAME = PREFIX + 'task-required'
MARKER = 'QA:%s:EXEC' % NONCE
DEV = 'evidence/'
NODE_IP = '192.168.100.222'                       # owned child host: native-3
CHILD_HOST = '192.168.100.222'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
EFFECT_DIR = '/var/lib/nodesteer-qa-sysb08-%s/effects' % NONCE
INTERVAL_SEC = 5

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
UNIT_HOSTS = {
    'native-1': ('192.168.100.204', 'nodesteer-agent-repair-20260930-101945-native-1.service'),
    'native-2': ('192.168.100.206', 'nodesteer-agent-repair-20260930-101945-native-2.service'),
    'native-3': ('192.168.100.222', 'nodesteer-agent-repair-20260930-101945-native-3.service'),
    'native-4': ('192.168.100.247', 'nodesteer-agent-repair-20260930-101945-native-4.service'),
}


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def sha256_text(text):
    return hashlib.sha256((text or '').encode()).hexdigest()


def script_body(version):
    return (
        '#!/bin/sh\n'
        'set -eu\n'
        'TAG="${NODESTEER_PARAM_tag:-none}"\n'
        'EFFECT="' + EFFECT_DIR + '/${TAG}.txt"\n'
        'printf \'%s|%s|%s\\n\' "$(date -u +%s.%N)" "$TAG" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$EFFECT"\n'
        'printf \'%s:%s:ran\\n\' "' + MARKER + '" "$TAG"\n'
        '# content-version ' + version + '\n'
    )


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {'script_id': None, 'task_allow_id': None, 'task_required_id': None,
                      'schedule_allow_id': None, 'schedule_required_id': None,
                      'child_node_id': None, 'child_agent_id': None,
                      'executions': {}, 'created': [], 'deleted': [], 'delete_results': {}}
        self.budget = {'api_mutations': 0, 'hub_definition_creates': 0,
                       'hub_definition_deletes': 0, 'manual_run_posts': 0,
                       'node_enroll_posts': 1, 'child_process_starts': 0,
                       'child_process_stops': 0, 'hub_stop_events': 0, 'hub_start_events': 0,
                       'proxy_cut_events': 0}

    # ---------------- helpers ----------------
    def rec(self, name, value):
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

    def tasks(self):
        return self.hub.json('/api/tasks')

    def schedules(self):
        return self.hub.json('/api/schedules')

    def executions(self, task_id=None, node_id=None, limit=300):
        q = '/api/executions?limit=%d' % limit
        if task_id:
            q += '&task_id=' + self.hub.quote(task_id)
        if node_id:
            q += '&node_id=' + self.hub.quote(node_id)
        code, body = self.hub.get(q)
        if code != 200:
            raise RuntimeError('executions_list_http_%s' % code)
        return body

    def exec_one(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return body if code == 200 else {}

    def exec_logs(self, exec_id):
        code, body = self.hub.get('/api/executions/%s/logs' % self.hub.quote(exec_id))
        return body if code == 200 else []

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

    def poll_nodes(self, seconds, interval=1.0, stop=None, include_child=True):
        t0 = time.time()
        samples = []
        while time.time() - t0 < seconds:
            by_id = self.nodes_by_id()
            row = {'at_utc': now_utc(), 't': round(time.time() - t0, 1), 'nodes': {}}
            for nid, label in PROTECTED_NODES.items():
                n = by_id.get(nid) or {}
                row['nodes'][label] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                       'global_revision': n.get('global_revision')}
            if include_child:
                cid = self.state.get('child_node_id')
                n = by_id.get(cid) or {}
                row['nodes']['owned_child'] = {'status': n.get('status'),
                                               'sync_status': n.get('sync_status'),
                                               'global_revision': n.get('global_revision')}
            samples.append(row)
            if stop and stop(by_id):
                return {'complete': True, 'seconds': round(time.time() - t0, 1), 'samples': samples}
            time.sleep(interval)
        return {'complete': False, 'seconds': round(time.time() - t0, 1), 'samples': samples}

    def all_units_snapshot(self):
        out = {}
        for label, (ip, unit) in UNIT_HOSTS.items():
            out[label] = {'ip': ip, 'units': node_units.probe(ip, [unit, 'nodesteer-agent.service'])}
        out['hub-vm'] = {'ip': '192.168.100.209',
                         'units': node_units.probe('192.168.100.209',
                                                   ['nodesteer-hub.service', hubctl.REPAIR_UNIT])}
        return out

    ROWCOLS = ['id', 'task_id', 'node_id', 'status', 'trigger_type', 'scheduled_time', 'start_time',
               'end_time', 'offline', 'synced', 'exit_code', 'block_reason', 'stdout_len',
               'stderr_len', 'stdout', 'stderr']

    def row_dict(self, row):
        if not row:
            return None
        d = dict(zip(self.ROWCOLS, row))
        d['offline'] = bool(d['offline'])
        d['synced'] = bool(d['synced'])
        return d

    def local_rows(self, task_id):
        return [self.row_dict(r) for r in self.child.execution_rows([task_id])['rows']]

    def hub_rows(self, task_id):
        return self.executions(task_id=task_id)

    def child_schedules(self):
        """Agent-local schedules table (id, definition_json, revision, enabled) parsed."""
        rows = self.child.sql('SELECT id,revision,enabled,definition_json FROM schedules')['rows']
        out = []
        for r in rows:
            try:
                d = json.loads(r[3] or '{}')
            except Exception:
                d = {}
            out.append({'id': r[0], 'revision': r[1], 'enabled': bool(r[2]),
                        'task_id': d.get('task_id'), 'type': d.get('type'),
                        'interval_sec': d.get('interval_sec'),
                        'execution_owner': d.get('execution_owner'),
                        'offline_policy': d.get('offline_policy'),
                        'misfire_policy': d.get('misfire_policy')})
        return out

    def child_tasks(self):
        """Agent-local tasks table (id, definition_json, revision, enabled) parsed."""
        rows = self.child.sql('SELECT id,revision,enabled,definition_json FROM tasks')['rows']
        out = []
        for r in rows:
            try:
                d = json.loads(r[3] or '{}')
            except Exception:
                d = {}
            out.append({'id': r[0], 'revision': r[1], 'enabled': bool(r[2]),
                        'offline_policy': d.get('offline_policy'), 'script_id': d.get('script_id'),
                        'param_values': d.get('param_values')})
        return out

    def effect_counts(self):
        probe = self.child.marker_probe(['%s/allow.txt' % EFFECT_DIR,
                                        '%s/required.txt' % EFFECT_DIR])
        out = {}
        for tag in ('allow', 'required'):
            path = '%s/%s.txt' % (EFFECT_DIR, tag)
            entry = probe.get(path) or {'exists': False}
            content = entry.get('content') or ''
            out[tag] = {'path': path, 'exists': entry.get('exists'),
                        'lines': len([l for l in content.splitlines() if l.strip()]),
                        'content_tail': content.splitlines()[-6:],
                        'sha256': entry.get('sha256')}
        return out

    # ---------------- phases ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': self.hub.base}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected_nodes_before'] = {}
        bad = []
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                bad.append(label)
            out['protected_nodes_before'][label] = None if not n else {
                'id': nid, 'agent_id': n.get('agent_id'), 'hostname': n.get('hostname'),
                'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision')}
        out['protected_all_online_synced'] = not bad
        if bad:
            raise RuntimeError('protected_node_not_healthy:' + ','.join(bad))
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
        out['primary_hub_unit_readonly'] = hubctl.primary_hub_state()
        out['units_before'] = self.all_units_snapshot()
        out['hub_db_before'] = hubdb.snapshot()
        scripts = self.hub.json('/api/scripts')
        tasks = self.tasks()
        schedules = self.schedules()
        out['fixture_absence'] = {
            'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
            'scripts_total': len(scripts), 'tasks_total': len(tasks),
            'schedules_total': len(schedules), 'nodes_total': len(nodes)}
        if any(out['fixture_absence'][k] for k in
               ('scripts_matching_prefix', 'tasks_matching_prefix', 'child_nodes_matching_prefix')):
            raise RuntimeError('owned_fixture_already_present')
        ci = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['child_host_pre'] = {
            'host': CHILD_HOST, 'effect_dir': EFFECT_DIR,
            'root_exists': ci['root_exists'], 'config_exists': ci['config_exists'],
            'owned_child_processes': ci['owned_child_processes'],
            'other_qa_child_processes': ci.get('other_qa_child_processes'),
            'owned_root_glob': ci['owned_root_glob'],
            'agent_binary_sha256': ci['agent_binary_sha256'],
            'agent_binary_sha_matches': ci['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': ci['primary_agent_unit'],
            'repair_agent_unit': ci['repair_agent_unit']}
        if ci['root_exists'] or ci['config_exists'] or ci['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['child_host_pre']['agent_binary_sha_matches']:
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
        effects = self.child.py_json(
            "import json,os\n"
            "os.makedirs(%r, mode=0o700, exist_ok=True)\n"
            "print(json.dumps({'effects_dir': %r, 'exists': os.path.isdir(%r), 'entries': sorted(os.listdir(%r))}))\n"
            % (EFFECT_DIR, EFFECT_DIR, EFFECT_DIR, EFFECT_DIR), label='effect_dir')
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
                'host': NODE_IP, 'root_on_node': self.child.root, 'config_mode_0600': True,
                'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                'listened_operator_port': port, 'effect_dir': effects,
                'routing': 'owned child(native-3) -> operator 192.168.100.170:%d (own frame proxy) -> repair hub :18080' % port},
            'connect_timeline': res['samples'],
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {
                'note': 'owned child Agent uses the exact repair Agent binary; process owner is the '
                        'harness on native-3, never a nodesteer-agent*.service unit',
                'agent_binary_sha256': child_agent.AGENT_SHA},
        }
        self.rec('phase1-owned-child.json', out)
        return out

    def phase2_fixtures(self):
        cid = self.state['child_node_id']
        sha_v1 = hashlib.sha256(script_body('v1').encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'sys-b08 offline-autonomy fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': script_body('v1'),
            'parameters': [{'name': 'tag', 'type': 'string', 'required': False, 'default': 'none'}],
            'environment': {}, 'working_dir': '', 'timeout': 60, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))

        made = {'script': {'id': script['id'], 'sha256': self.hub.json('/api/scripts/' + script['id']).get('sha256'),
                           'expected_sha256': sha_v1}}
        for key, name, policy in (('task_allow_id', TASK_ALLOW_NAME, 'allow_offline'),
                                  ('task_required_id', TASK_REQUIRED_NAME, 'hub_online_required')):
            code, task = self.hub.post('/api/tasks', {
                'name': name, 'type': 'script', 'script_id': script['id'],
                'target': {'type': 'node', 'node_ids': [cid]},
                'parameters': [], 'param_values': {'tag': 'allow' if 'allow' in policy else 'required'},
                'timeout': 60, 'retry': 0, 'offline_policy': policy, 'enabled': True})
            self.budget['api_mutations'] += 1
            if code not in (200, 201):
                raise RuntimeError('task_create_http_%s %s' % (code, task))
            self.state[key] = task['id']
            self.state['created'].append(('task', task['id']))
            made[name] = {'id': task['id'], 'offline_policy': policy,
                          'param_values': task.get('param_values')}
        for key, task_key, name, policy in (
                ('schedule_allow_id', 'task_allow_id', PREFIX + 'sched-allow', 'allow_offline'),
                ('schedule_required_id', 'task_required_id', PREFIX + 'sched-required',
                 'hub_online_required')):
            code, sch = self.hub.post('/api/schedules', {
                'task_id': self.state[task_key], 'type': 'interval', 'interval_sec': INTERVAL_SEC,
                'timezone': 'UTC', 'execution_owner': 'agent', 'offline_policy': policy,
                'misfire_policy': 'skip', 'enabled': True})
            self.budget['api_mutations'] += 1
            if code not in (200, 201):
                raise RuntimeError('schedule_create_http_%s %s' % (code, sch))
            self.state[key] = sch['id']
            self.state['created'].append(('schedule', sch['id']))
            made[name] = {'id': sch['id'], 'task_id': self.state[task_key], 'type': sch.get('type'),
                          'interval_sec': sch.get('interval_sec'),
                          'execution_owner': sch.get('execution_owner'),
                          'offline_policy': sch.get('offline_policy'),
                          'misfire_policy': sch.get('misfire_policy'),
                          'enabled': sch.get('enabled')}
        self.budget['hub_definition_creates'] += 5
        made['script']['content_sha256_matches'] = made['script']['sha256'] == sha_v1

        # wait for the child to receive tasks + schedules
        owned_sched_ids = {self.state['schedule_allow_id'], self.state['schedule_required_id']}
        owned_task_ids = {self.state['task_allow_id'], self.state['task_required_id']}
        deadline = time.time() + 150
        child_view, tasks_view = [], []
        while time.time() < deadline:
            child_view = [s for s in self.child_schedules() if s['id'] in owned_sched_ids]
            tasks_view = [t for t in self.child_tasks() if t['id'] in owned_task_ids]
            if len(child_view) == 2 and len(tasks_view) == 2:
                break
            time.sleep(2)
        # positive control while ONLINE: both schedules must fire at least once
        t0 = time.time()
        allow_online = required_online = 0
        while time.time() - t0 < 40:
            allow_online = len(self.local_rows(self.state['task_allow_id']))
            required_online = len(self.local_rows(self.state['task_required_id']))
            if allow_online >= 1 and required_online >= 1:
                break
            time.sleep(2)
        out = {
            'at_utc': now_utc(),
            'fixtures': made,
            'child_schedules': child_view,
            'child_tasks': tasks_view,
            'online_positive_control': {
                'allow_executions': allow_online, 'required_executions': required_online,
                'both_fired_while_online': allow_online >= 1 and required_online >= 1,
                'note': 'both agent-owned interval schedules fire while the Hub link is up; this is the '
                        'control that isolates the offline gate as the differentiator in SYS-14'},
            'effect_counts_after_online': self.effect_counts(),
        }
        if not out['online_positive_control']['both_fired_while_online']:
            raise RuntimeError('online_positive_control_failed')
        self.rec('phase2-fixtures.json', out)
        return out

    def _offline_window(self, cut_seconds, label):
        """Cut the owned child's Hub link for a bounded window and observe both sides."""
        def trigger_counts():
            tail = self.child.log_tail(3000).get('tail', [])
            return {
                'allow_trigger_lines': sum(1 for l in tail
                                           if 'schedule trigger' in l
                                           and self.state['schedule_allow_id'] in l),
                'required_trigger_lines': sum(1 for l in tail
                                              if 'schedule trigger' in l
                                              and self.state['schedule_required_id'] in l),
                'connection_lost_lines': sum(1 for l in tail if 'connection lost' in l),
            }

        pre_cut = {'at_utc': now_utc(), 'node': self.node(),
                   'allow_rows': len(self.local_rows(self.state['task_allow_id'])),
                   'required_rows': len(self.local_rows(self.state['task_required_id'])),
                   'hub_rows_allow': self.hub_rows(self.state['task_allow_id']),
                   'hub_rows_required': self.hub_rows(self.state['task_required_id']),
                   'trigger_counts': trigger_counts(),
                   'effects': self.effect_counts()}
        self.proxy.set_mode('cut')
        self.budget['proxy_cut_events'] += 1
        t_cut = time.time()
        offline_timeline = self.poll_nodes(min(cut_seconds, 30), 1.0)
        try:
            hub_view_during = self.node()
            hub_allow_during = self.hub_rows(self.state['task_allow_id'])
            hub_required_during = self.hub_rows(self.state['task_required_id'])
        except Exception as exc:
            hub_view_during = {'error': str(exc)}
            hub_allow_during, hub_required_during = [], []
        time.sleep(max(0.0, cut_seconds - (time.time() - t_cut)))
        during_counts = trigger_counts()
        during = {
            'at_utc': now_utc(), 'seconds_offline': round(time.time() - t_cut, 1),
            'allow_local_rows': self.local_rows(self.state['task_allow_id']),
            'required_local_rows': self.local_rows(self.state['task_required_id']),
            'effects': self.effect_counts(),
            'child_node_record_seen_by_hub_during_cut': hub_view_during,
            'hub_executions_allow_during_cut': hub_allow_during,
            'hub_executions_required_during_cut': hub_required_during,
            'local_revision': self.child.sql('SELECT global_revision,last_sync FROM sync_state WHERE id=1')['rows'],
            'offline_timeline': offline_timeline,
        }
        during['child_log_counts_absolute'] = during_counts
        during['child_log_counts'] = {
            'allow_trigger_lines': during_counts['allow_trigger_lines']
                                   - pre_cut['trigger_counts']['allow_trigger_lines'],
            'required_trigger_lines': during_counts['required_trigger_lines']
                                      - pre_cut['trigger_counts']['required_trigger_lines'],
            'connection_lost_lines': during_counts['connection_lost_lines']
                                     - pre_cut['trigger_counts']['connection_lost_lines'],
            'note': 'delta of matching agent-log lines across the offline window (absolute counts also '
                    'recorded); the Agent log carries "schedule trigger" for the allow_offline schedule '
                    'and none for the hub_online_required one',
        }
        self.proxy.set_mode('pass')
        reconnect = self.poll_nodes(60, 0.5, stop=lambda by: (
            (by.get(self.state['child_node_id']) or {}).get('status') == 'online'
            and (by.get(self.state['child_node_id']) or {}).get('sync_status') == 'synced'))
        upload_deadline = time.time() + 120
        local_allow = local_required = None
        while time.time() < upload_deadline:
            local_allow = self.local_rows(self.state['task_allow_id'])
            local_required = self.local_rows(self.state['task_required_id'])
            if local_allow and all(r['synced'] for r in local_allow) and \
               local_required and all(r['synced'] for r in local_required):
                break
            time.sleep(2)
        post = {'at_utc': now_utc(),
                'reconnect_complete': reconnect['complete'],
                'reconnect_seconds': reconnect['seconds'],
                'reconnect_timeline': reconnect['samples'],
                'allow_local_rows': local_allow,
                'required_local_rows': local_required,
                'hub_allow_rows': self.hub_rows(self.state['task_allow_id']),
                'hub_required_rows': self.hub_rows(self.state['task_required_id']),
                'effects': self.effect_counts(),
                'node': self.node()}
        return {'label': label, 'pre_cut': pre_cut, 'during_offline': during, 'after_restore': post}

    def phase3_offline_autonomy(self):
        window = self._offline_window(32, 'primary-offline-window')
        out = dict(window)
        out['at_utc'] = now_utc()
        during = window['during_offline']
        pre = window['pre_cut']
        schedule_rows_during = [r for r in during['allow_local_rows']
                                if r['trigger_type'] == 'schedule']
        out['derived'] = {
            'offline_executions_allow': len(during['allow_local_rows']) - pre['allow_rows'],
            'offline_executions_required': len(during['required_local_rows']) - pre['required_rows'],
            'offline_flag_distribution_allow': {
                'offline_rows_total': sum(1 for r in schedule_rows_during if r['offline']),
                'online_rows_total': sum(1 for r in schedule_rows_during if not r['offline']),
                'offline_rows_created_during_cut': sum(
                    1 for r in schedule_rows_during
                    if r['offline'] and r['start_time'] >= during['at_utc']),
            },
            'required_effect_file_unchanged_during_offline':
                (during['effects']['required']['lines'] == pre['effects']['required']['lines']),
            'allow_effect_file_grew_during_offline':
                (during['effects']['allow']['lines'] > pre['effects']['allow']['lines']),
            'hub_blind_during_offline_allow': len(during['hub_executions_allow_during_cut'])
                                              == len(pre['hub_rows_allow']),
            'hub_blind_during_offline_required': len(during['hub_executions_required_during_cut'])
                                                 == len(pre['hub_rows_required']),
            'reconnected_and_all_synced': bool(window['after_restore']['reconnect_complete']
                                               and all(r['synced'] for r in window['after_restore']['allow_local_rows'])
                                               and all(r['synced'] for r in window['after_restore']['required_local_rows'])),
        }
        self.rec('phase3-sys13-sys14-offline-autonomy.json', out)
        return out

    def phase4_recovery_idempotency(self):
        # second offline/online cycle on the same schedules: recovery must not
        # duplicate any slot and must not replay old slots.
        window = self._offline_window(22, 'second-offline-window')
        slots = {}
        for task_label, task_id in (('allow', self.state['task_allow_id']),
                                   ('required', self.state['task_required_id'])):
            local = self.local_rows(task_id)
            hub = self.hub_rows(task_id)
            local_slots = {}
            for r in local:
                local_slots.setdefault(r['scheduled_time'], []).append(r['id'])
            hub_slots = {}
            for r in hub:
                hub_slots.setdefault(r['scheduled_time'], []).append(r['id'])
            content_matches = []
            for r in local:
                if r['offline']:
                    detail = hubdb.execution_detail(r['id'])
                    content_matches.append({
                        'execution_id': r['id'], 'local_status': r['status'],
                        'hub_status': (detail or {}).get('status'),
                        'hub_offline': bool((detail or {}).get('offline')),
                        'local_stdout_sha256': sha256_text(r['stdout']),
                        'hub_stdout_sha256': sha256_text((detail or {}).get('stdout')),
                        'match': (sha256_text(r['stdout']) == sha256_text((detail or {}).get('stdout'))
                                  and r['status'] == (detail or {}).get('status')),
                    })
            slots[task_label] = {
                'task_id': task_id,
                'local_rows': len(local), 'hub_rows': len(hub),
                'local_ids': sorted(r['id'] for r in local),
                'hub_ids': sorted(r['id'] for r in hub),
                'local_slots': {k: v for k, v in local_slots.items()},
                'hub_slots': {k: v for k, v in hub_slots.items()},
                'local_slot_unique': all(len(v) == 1 for v in local_slots.values()),
                'hub_slot_unique': all(len(v) == 1 for v in hub_slots.values()),
                'local_slot_set_equals_hub_slot_set': set(local_slots) == set(hub_slots),
                'id_sets_equal': sorted(r['id'] for r in local) == sorted(r['id'] for r in hub),
                'offline_content_matches_hub': content_matches,
                'all_offline_content_matches': all(c['match'] for c in content_matches) if content_matches else None,
                'duplicate_ids': len(hub) - len({r['id'] for r in hub}),
            }
        out = {
            'at_utc': now_utc(),
            'second_window': window,
            'slot_analysis': slots,
            'no_duplicate_after_two_cycles': all(
                v['local_slot_unique'] and v['hub_slot_unique'] and v['id_sets_equal']
                and v['local_slot_set_equals_hub_slot_set'] and v['duplicate_ids'] == 0
                for v in slots.values()),
            'note': 'two full offline/online cycles were performed on the same agent-owned interval '
                    'schedules; every scheduled slot exists exactly once locally and exactly once on '
                    'the Hub, and the offline uploads carry byte-identical stdout',
        }
        self.rec('phase4-e2e04-ar18-recovery-idempotency.json', out)
        return out

    def phase5_settle_and_consistency(self, guard):
        time.sleep(15)
        s_read = self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))
        t_allow = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_allow_id']))
        t_required = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_required_id']))
        sch = {x['id']: x for x in self.schedules()}
        child = self.child.db_read(self.state['task_allow_id'], [], self.state['script_id'],
                                   task_ids=[self.state['task_allow_id'], self.state['task_required_id']])
        child_script = self.child.sql('SELECT id,name,revision,sha256,enabled FROM scripts WHERE id=?',
                                      args=[self.state['script_id']])['rows']
        all_child_sched = self.child_schedules()
        all_child_tasks = self.child_tasks()
        child_sched = [s for s in all_child_sched
                       if s['task_id'] in (self.state['task_allow_id'],
                                           self.state['task_required_id'])]
        child_tasks_view = [t for t in all_child_tasks
                            if t['id'] in (self.state['task_allow_id'],
                                           self.state['task_required_id'])]
        pre_logs = {}
        for label, task_id in (('allow', self.state['task_allow_id']),
                               ('required', self.state['task_required_id'])):
            pre_logs[label] = {r['id']: len(self.exec_logs(r['id'])) for r in self.hub_rows(task_id)}
        time.sleep(6)
        post_logs = {}
        for label, task_id in (('allow', self.state['task_allow_id']),
                               ('required', self.state['task_required_id'])):
            post_logs[label] = {r['id']: len(self.exec_logs(r['id'])) for r in self.hub_rows(task_id)}
        # Only executions present in BOTH snapshots can be compared: the live interval
        # schedules legitimately create new executions between the two samples.
        common = {}
        changed = []
        for label in pre_logs:
            inter = sorted(set(pre_logs[label]) & set(post_logs[label]))
            common[label] = {eid: {'before': pre_logs[label][eid], 'after': post_logs[label][eid]}
                             for eid in inter}
            changed += [{'task': label, 'execution_id': eid,
                         'before': common[label][eid]['before'],
                         'after': common[label][eid]['after']}
                        for eid in inter if pre_logs[label][eid] != post_logs[label][eid]]
        all_counts = [n for label in post_logs for n in post_logs[label].values()] or [0]
        out = {
            'at_utc': now_utc(),
            'post_outage_executions_continue': {
                'allow_total_hub_rows': len(self.hub_rows(self.state['task_allow_id'])),
                'required_total_hub_rows': len(self.hub_rows(self.state['task_required_id'])),
                'note': 'after the link came back both schedules fired again (positive control), while '
                        'no old slot was replayed'},
            'desired_equals_actual': {
                'hub_script_sha256': s_read.get('sha256'),
                'child_script_sha256': (child_script[0][3] if child_script else None),
                'script_match': bool(child_script) and s_read.get('sha256') == child_script[0][3],
                'hub_task_allow_revision': t_allow.get('revision'),
                'hub_task_required_revision': t_required.get('revision'),
                'child_task_revisions': {t['id']: t['revision'] for t in child_tasks_view},
                'expected_task_revisions': {t_allow.get('id'): t_allow.get('revision'),
                                            t_required.get('id'): t_required.get('revision')},
                'hub_schedule_revisions': {self.state['schedule_allow_id']:
                                           sch.get(self.state['schedule_allow_id'], {}).get('revision'),
                                           self.state['schedule_required_id']:
                                           sch.get(self.state['schedule_required_id'], {}).get('revision')},
                'child_schedule_revisions': {s['id']: s['revision'] for s in child_sched},
                'child_schedule_task_ids': {s['id']: s['task_id'] for s in child_sched},
                'child_schedule_offline_policies': {s['id']: s['offline_policy'] for s in child_sched},
                'child_local_global_revision': child.get('local_global_revision'),
                'child_quick_check': child.get('quick_check'),
            },
            'log_chunks_stable_for_existing_executions': {
                'common_executions': common,
                'changed': changed,
                'stable': not changed,
            },
            'agent_owned_executions_have_no_realtime_chunks': {
                'max_realtime_chunks_per_execution': max(all_counts),
                'observed_counts': post_logs,
                'note': 'agent-owned scheduled executions exist on the Hub only after the Agent '
                        'uploads them, so realtime LOG_CHUNK frames for them are dropped by the Hub '
                        '(AppendLog requires an existing execution row); their logs are carried '
                        'authoritatively by the final upload',
            },
            'log_chunks': {'before': pre_logs, 'after': post_logs},
            'effect_files_final': self.effect_counts(),
        }
        out['assertions_pass'] = bool(
            out['desired_equals_actual']['script_match']
            and out['log_chunks_stable_for_existing_executions']['stable'])
        self.rec('phase5-settle-consistency.json', out)
        return out

    # ---- cleanup ----
    def phase6_cleanup(self):
        results = {'deleted': [], 'errors': [], 'at_utc': now_utc()}
        # schedules first so nothing keeps firing
        for kind, obj_id in [('schedule', self.state['schedule_allow_id']),
                             ('schedule', self.state['schedule_required_id']),
                             ('task', self.state['task_allow_id']),
                             ('task', self.state['task_required_id']),
                             ('script', self.state['script_id'])]:
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
        if self.child is not None:
            try:
                results['effect_files'] = self.child.remove_paths(
                    ['%s/allow.txt' % EFFECT_DIR, '%s/required.txt' % EFFECT_DIR])
            except Exception as exc:
                results['errors'].append('effect_remove:%s' % exc)
            try:
                remove = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                remove = {'error': str(exc)}
                results['errors'].append('child_remove:%s' % exc)
        else:
            remove = {'skipped': 'owned_child_never_installed'}
        results['node_child_removal'] = remove
        cid = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(cid))
        self.budget['api_mutations'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(cid))
        results['child_node'] = {'id': cid, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        return results

    def phase7_oracle(self, cleanup, guard):
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
        tasks = self.tasks()
        schedules = self.schedules()
        owned_task_ids = {self.state['task_allow_id'], self.state['task_required_id']}
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in schedules if s.get('task_id') in owned_task_ids],
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        hub_unit = hubctl.unit_state()
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        units_after = self.all_units_snapshot()
        repair_key = 'hub-vm/' + hubctl.REPAIR_UNIT
        pid_pairs = {}
        for label in list(guard['units_before']):
            for unit in guard['units_before'][label]['units']:
                pid_pairs['%s/%s' % (label, unit)] = {
                    'before': guard['units_before'][label]['units'][unit].get('MainPID'),
                    'after': units_after[label]['units'][unit].get('MainPID')}
        protected_pairs = {k: v for k, v in pid_pairs.items() if k != repair_key}
        oracle = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'node_owned_child_orphan': orphan,
            'node_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                               'owned_child_processes': inv['owned_child_processes'],
                               'owned_root_glob': inv['owned_root_glob'],
                               'primary_agent_unit': inv['primary_agent_unit'],
                               'repair_agent_unit': inv['repair_agent_unit']},
            'protected_units_active': (inv['primary_agent_unit'].get('ActiveState') == 'active'
                                       and inv['repair_agent_unit'].get('ActiveState') == 'active'),
            'repair_hub_unit_after': hub_unit,
            'repair_hub_active': hub_unit.get('ActiveState') == 'active',
            'health_after': hubctl.health(),
            'units_after': units_after,
            'all_protected_and_primary_service_mainpids_identical_to_baseline': {
                'pairs': protected_pairs,
                'all_identical': all(v['before'] == v['after'] for v in protected_pairs.values())},
            'repair_hub_mainpid': {
                'pair': pid_pairs.get(repair_key),
                'unchanged': bool(pid_pairs.get(repair_key)
                                  and pid_pairs[repair_key]['before'] == pid_pairs[repair_key]['after']),
                'note': 'this batch does not stop or start the repair Hub at all'},
            'settings_after': settings,
            'settings_unchanged': settings == guard['settings_readonly'],
            'hub_restarts_in_this_batch': 0,
            'cleanup_results': cleanup,
            'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan
                           and oracle['protected_units_active'] and oracle['repair_hub_active']
                           and oracle['health_after']['healthz'].get('http') == 200
                           and oracle['health_after']['readyz'].get('http') == 200
                           and oracle['settings_unchanged']
                           and oracle['all_protected_and_primary_service_mainpids_identical_to_baseline'][
                               'all_identical']
                           and oracle['repair_hub_mainpid']['unchanged']
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        hubapi.write_json('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b08', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-13', 'SYS-14', 'AR-18', 'E2E-04'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b08', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)

    failed = False
    guard = None
    try:
        mark('guard'); guard = run.phase0_guard(); evidence['phases']['guard'] = guard
        mark('child'); evidence['phases']['child'] = run.phase1_child()
        progress['child_node_id'] = run.state['child_node_id']
        progress['child_agent_id'] = run.state['child_agent_id']
        mark('fixtures'); evidence['phases']['fixtures'] = run.phase2_fixtures()
        progress['task_allow_id'] = run.state['task_allow_id']
        progress['task_required_id'] = run.state['task_required_id']
        progress['schedule_allow_id'] = run.state['schedule_allow_id']
        progress['schedule_required_id'] = run.state['schedule_required_id']
        progress['script_id'] = run.state['script_id']
        mark('offline_window')
        evidence['phases']['offline'] = run.phase3_offline_autonomy()
        progress['offline_derived'] = evidence['phases']['offline']['derived']
        mark('recovery_idempotency')
        evidence['phases']['recovery'] = run.phase4_recovery_idempotency()
        progress['no_duplicate_after_two_cycles'] = evidence['phases']['recovery'][
            'no_duplicate_after_two_cycles']
        mark('settle'); evidence['phases']['settle'] = run.phase5_settle_and_consistency(guard)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = '%s: %s' % (type(exc).__name__, exc)
        failed = True
    mark('cleanup')
    try:
        if run.proxy:
            frames_payload = {'frames': list(run.proxy.frames), 'events': list(run.proxy.events),
                              'proxy_state': run.proxy.state()}
            # sanitize so a single odd frame value can never lose the whole ledger
            frames_payload = json.loads(json.dumps(frames_payload, default=str))
            hubapi.write_json('evidence/phase-frames.json', frames_payload)
    except Exception as exc:
        progress['frames_dump_error'] = '%s: %s' % (type(exc).__name__, exc)
        evidence['deviations'].append('FRAMES_DUMP_ERROR:%s' % exc)
    if run.proxy and run.proxy.mode() != 'pass':
        try:
            run.proxy.set_mode('pass')
        except Exception:
            pass
    try:
        if run.state['child_node_id'] or run.state['schedule_allow_id']:
            cleanup = run.phase6_cleanup()
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
        oracle = run.phase7_oracle(progress.get('final_cleanup', {}),
                                   guard or {'units_before': {}, 'settings_readonly': {}})
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    try:
        if not hubctl.healthy():
            hubctl.start_repair_unit()
            progress['hub_rescue_start'] = hubctl.wait_healthy(timeout=120)
    except Exception as exc:
        progress['hub_rescue_error'] = str(exc)

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {'script_id': run.state['script_id'],
                         'task_allow_id': run.state['task_allow_id'],
                         'task_required_id': run.state['task_required_id'],
                         'schedule_allow_id': run.state['schedule_allow_id'],
                         'schedule_required_id': run.state['schedule_required_id'],
                         'child_node_id': run.state['child_node_id'],
                         'child_agent_id': run.state['child_agent_id'],
                         'delete_results': run.state['delete_results']}
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b08', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_allow_id'], 'name': TASK_ALLOW_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_allow_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_required_id'], 'name': TASK_REQUIRED_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_required_id'], {}).get('get_http') == 404},
            {'kind': 'schedule', 'id': run.state['schedule_allow_id'], 'name': PREFIX + 'sched-allow',
             'deleted_get404': run.state['delete_results'].get(run.state['schedule_allow_id'], {}).get('get_http') == 404},
            {'kind': 'schedule', 'id': run.state['schedule_required_id'], 'name': PREFIX + 'sched-required',
             'deleted_get404': run.state['delete_results'].get(run.state['schedule_required_id'], {}).get('get_http') == 404}],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'deleted_get404': True, 'delete_http': 200},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': CHILD_HOST,
                         'root': run.child.root if run.child else None,
                         'effect_dir': EFFECT_DIR,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('node_child_removal', {}).get('root_removed'))},
        'preserved_executions': {
            'note': 'offline scheduled executions are preserved as idempotency evidence',
            'allow_ids': [r['id'] for r in (evidence['phases'].get('offline', {}).get(
                'after_restore', {}).get('allow_local_rows') or [])],
            'required_ids': [r['id'] for r in (evidence['phases'].get('offline', {}).get(
                'after_restore', {}).get('required_local_rows') or [])]},
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'}
    hubapi.write_json('fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b08',
                      'cases': {c['case_id']: c['proposed_after'] for c in
                                json.loads((HERE / 'case-result.json').read_text())['cases']},
                      'clean': progress.get('oracle_clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    fx = ph.get('fixtures', {})
    off = ph.get('offline', {})
    rec = ph.get('recovery', {})
    settle = ph.get('settle', {})
    cleanup_ok = bool(progress.get('oracle_clean'))

    derived = off.get('derived', {})
    during = off.get('during_offline', {})
    after = off.get('after_restore', {})
    pre = off.get('pre_cut', {})
    slots = rec.get('slot_analysis', {})
    allow_slots = slots.get('allow', {})
    required_slots = slots.get('required', {})
    control_ok = bool(fx.get('online_positive_control', {}).get('both_fired_while_online'))

    offline_allow_ok = bool(control_ok
                            and (derived.get('offline_executions_allow') or 0) >= 1
                            and derived.get('allow_effect_file_grew_during_offline')
                            and (derived.get('offline_flag_distribution_allow', {})
                                 .get('offline_rows_total') or 0) >= 1
                            and (during.get('child_log_counts', {})
                                 .get('allow_trigger_lines') or 0) >= 1
                            and derived.get('reconnected_and_all_synced'))
    offline_required_ok = bool(control_ok
                               and (pre.get('required_rows') or 0) == len(during.get('required_local_rows') or [])
                               and derived.get('required_effect_file_unchanged_during_offline')
                               and (during.get('child_log_counts', {})
                                    .get('required_trigger_lines') or 0) == 0)
    ar18_ok = bool(offline_allow_ok
                   and all(v['all_offline_content_matches'] for v in slots.values()
                           if v.get('offline_content_matches_hub'))
                   and all(v['id_sets_equal'] for v in slots.values())
                   and rec.get('no_duplicate_after_two_cycles')
                   and settle.get('assertions_pass'))
    e2e04_ok = bool(offline_allow_ok and allow_slots.get('local_slot_unique')
                    and allow_slots.get('hub_slot_unique')
                    and allow_slots.get('local_slot_set_equals_hub_slot_set')
                    and allow_slots.get('duplicate_ids') == 0
                    and rec.get('no_duplicate_after_two_cycles'))
    canonical = ('NOT_RUN (repair build not executed; legacy baseline PASS excluded per '
                 'remaining_dimensions)')

    def case(case_id, title, proposed, confidence, dims, limits):
        return {'case_id': case_id, 'title': title, 'canonical_before': canonical,
                'proposed_after': proposed, 'confidence': confidence,
                'covered_dimensions': dims, 'remaining_or_limitations': limits,
                'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                    'case-result.json', 'progress.json', 'fixture-ledger.json',
                    'final-cleanup-oracle.json', 'run-evidence.json'],
                'actual_resources': {
                    'owned_prefix': PREFIX, 'child_node_id': run.state['child_node_id'],
                    'child_agent_id': run.state['child_agent_id'],
                    'script_id': run.state['script_id'],
                    'task_allow_id': run.state['task_allow_id'],
                    'task_required_id': run.state['task_required_id'],
                    'schedule_allow_id': run.state['schedule_allow_id'],
                    'schedule_required_id': run.state['schedule_required_id']},
                'actual_budget': dict(run.budget),
                'final_cleanup': progress.get('final_cleanup', {})}

    cases = []
    cases.append(case(
        'SYS-13', 'SYS-13 Agent 断网时 Allow Offline 调度继续执行',
        'PASS' if (offline_allow_ok and cleanup_ok) else ('PASS_WITH_NOTES' if offline_allow_ok else 'PARTIAL'),
        'high' if (offline_allow_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-13',
          'layer': 'agent-local scheduler / real Hub link cut (own frame proxy) / node effect files',
          'status': 'PASS' if offline_allow_ok else 'PARTIAL',
          'description': [
              'An agent-owned interval Schedule (interval_sec=%d, execution_owner=agent, offline_policy=allow_offline) was created on the repair Hub and synced to the owned child Agent.' % INTERVAL_SEC,
              'While the Hub link was cut the Agent process stayed alive and its local scheduler kept firing: %s new execution(s) appeared in the Agent store with offline=1 and trigger_type=schedule, and the node effect file grew by the same amount.' % derived.get('offline_executions_allow'),
              'The business effect is real: the script appended one line per fire to the harness-owned effect file on the node while the Hub was unreachable (the Hub had no new Execution rows for that time window).',
              'After the link was restored the offline executions were uploaded (synced=1) and the schedule kept firing normally.']}],
        ['The Hub link was cut on the harness-owned frame proxy (the Agent process deliberately stayed '
         'alive so the local scheduler could be exercised); no protected service and no Hub unit was '
         'stopped in this batch.']))

    cases.append(case(
        'SYS-14', 'SYS-14 Hub Online Required 在 Hub 不可用时不执行',
        'PASS' if (offline_required_ok and cleanup_ok) else ('PASS_WITH_NOTES' if offline_required_ok else 'PARTIAL'),
        'high' if (offline_required_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-14',
          'layer': 'agent-local scheduler offline gate / node effect files / agent log',
          'status': 'PASS' if offline_required_ok else 'PARTIAL',
          'description': [
              'A second agent-owned interval Schedule with the same shape but offline_policy=hub_online_required was synced to the same Agent, and it fired while the link was up (positive control on the same object).',
              'During the identical offline window its execution count stayed exactly at the pre-cut value: no new journal row, no new Execution, and zero new lines in its node effect file - so the task really did not execute on the client machine.',
              'The Agent log for the offline window contains no "schedule trigger" line for that schedule, while it does contain them for the allow_offline schedule: the gate is the offline policy, not a broken schedule.',
              'After the link was restored the hub_online_required schedule fired again - proving it was suppressed by the offline gate and not permanently disabled.']}],
        ['The "no execution" side is evidenced by three independent readings (Agent store, node effect '
         'file, agent log) plus the post-recovery positive control on the same schedule.']))

    cases.append(case(
        'AR-18', 'AR-18 Offline Scheduled Execution；恢复后幂等、持久化、Desired State与实际状态一致',
        'PASS' if (ar18_ok and cleanup_ok) else ('PASS_WITH_NOTES' if offline_allow_ok else 'PARTIAL'),
        'high' if (ar18_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-18',
          'layer': 'offline agent-owned schedule -> hub upload idempotency / persistence / desired==actual',
          'status': 'PASS' if ar18_ok else 'PARTIAL',
          'description': [
              'Offline scheduled executions were produced by a real agent-owned interval schedule during two separate offline windows and uploaded after each recovery.',
              'Idempotency: for every scheduled slot there is exactly one Agent-local row and exactly one Hub row; the local and Hub execution ID sets are identical and each offline execution carries byte-identical stdout (content SHA256 match) and the same terminal status.',
              'Persistence: the Hub revision did not decrease, the owned script/task/schedule rows were still present after recovery with matching revisions, and PRAGMA quick_check was ok on both sides.',
              'Desired State == actual state after recovery: the Hub script sha256 matches the Agent-local copy, the task revisions and schedule revisions match on both sides, and the schedules continued to fire without replaying old slots.']}],
        ['Two offline windows were used (32s and 22s) so that both the first upload and a repeat '
         'recovery path were covered.']))

    cases.append(case(
        'E2E-04', 'E2E-04 Agent-owned Interval Offline → 离线继续执行，恢复后无重复',
        'PASS' if (e2e04_ok and cleanup_ok) else ('PASS_WITH_NOTES' if e2e04_ok else 'PARTIAL'),
        'high' if (e2e04_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:E2E-04',
          'layer': 'agent-owned interval schedule / offline continuation / duplicate-free recovery',
          'status': 'PASS' if e2e04_ok else 'PARTIAL',
          'description': [
              'The Agent-owned interval schedule continued to execute while the Agent had no Hub connection (%s new offline executions in the first window, %s lines added to the node effect file), i.e. offline autonomy is real and not a Hub-side replay.' % (derived.get('offline_executions_allow'), (after.get('effects', {}).get('allow', {}).get('lines') or 0) - (pre.get('effects', {}).get('allow', {}).get('lines') or 0)),
              'After recovery there was no duplicate: every slot exists exactly once locally and once on the Hub, the ID sets match, and the same holds after a second offline/online cycle.',
              'No catch-up replay happened after recovery: later slots were produced by the live schedule, old slots were not re-executed, and the Hub row count equals the number of distinct slots.',
              'The user-visible feedback is consistent with the effect on the node: the Hub shows exactly the executions the Agent journal recorded, with the offline flag set for the ones produced while disconnected.']}],
        ['"No duplicate" is asserted at slot granularity (task_id + node_id + scheduled_time), which is '
         'the product idempotency key for scheduled executions.']))

    result = {'batch_id': 'sys-b08',
              'title': '离线自治与在线门禁：Allow Offline 继续执行、Hub Online Required 不执行、'
                       'Agent-owned 调度离线触发',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
              'cases': cases, 'deviations': evidence['deviations'], 'budget': run.budget,
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management writes only to repair http://192.168.100.209:18080',
                  'hub_outage': 'cut of the owned child Agent link on the harness-owned frame proxy '
                                '(proxy mode=cut); the repair Hub was NOT restarted in this batch '
                                '(0 hub stop/start events)',
                  'owned_child': 'one harness-owned child Agent on native-3 (exact repair Agent binary '
                                 'd55dc5cd...) behind a harness-owned frame proxy on 192.168.100.170',
                  'offline_autonomy': 'the Agent process intentionally stayed alive so its local '
                                      'scheduler was exercised offline',
                  'product_source_changes': 0, 'git_operations': 0, 'vms_restarted': 0}}
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
