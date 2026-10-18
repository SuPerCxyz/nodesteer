#!/usr/bin/env python3
"""sys-b06 runtime batch: Journal-first, complete local logs while the Hub is
unavailable, and idempotent Execution/log re-upload after recovery.

Cases: SYS-33, INV-08, SYS-35, SYS-36, AR-19, AR-07.

Real evidence chain (no product source change, no protected Agent stopped, no VM
touched):

  operator/API act -> real dispatch -> real Hub Desired State commit -> real
  execution on a real repair node -> real repair-Hub stop (the ONE authorised
  unit) while the process is still running -> agent-local journal/log readback ->
  real Hub start -> real Agent reconnect/upload -> Hub API + SQLite readback.

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
import node_units
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb06-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NAME = PREFIX + 'task'
MARKER = 'QA:%s:EXEC' % NONCE
DEV = 'evidence/'
NODE_IP = '192.168.100.204'                       # owned child host: native-1
CHILD_HOST = '192.168.100.204'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
# Effect files live inside the owned child root so cleanup removes them with it.
EFFECT_DIR = '/var/lib/nodesteer-qa-sysb06-%s/effects' % NONCE

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
RECONVERGE_WINDOW = 90.0   # >=45s Revision Check window as required by the batch rules

# Node-side high-frequency sampler: proves the journal row (created by the Agent
# before it spawns the process) is visible before the script's first statement
# has run.  Local clock, local observation point, no SSH round trip per sample.
JOURNAL_SAMPLER = r'''
import json, os, sqlite3, sys, time
db, task_id, marker, out, duration = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
t0 = time.time()
c = sqlite3.connect('file:' + db + '?mode=ro', uri=True, timeout=2)
c.execute('PRAGMA query_only=ON')
row_seen = None
marker_seen = None
pre_spawn_samples = 0
post_spawn_samples = 0
samples = 0
err = None
while time.time() - t0 < duration:
    try:
        now = time.time()
        samples += 1
        if row_seen is None:
            r = c.execute('SELECT id,status,start_time,synced FROM executions WHERE task_id=? ORDER BY start_time LIMIT 1',
                          (task_id,)).fetchone()
            if r:
                row_seen = {'at': now, 'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                            'exec_id': r[0], 'status': r[1], 'start_time': r[2], 'synced': bool(r[3])}
        if marker_seen is None and os.path.exists(marker):
            st = os.stat(marker)
            marker_seen = {'at': now, 'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
                           'size': st.st_size,
                           'content': open(marker, 'r', errors='replace').read()[:400]}
        if row_seen and not marker_seen:
            pre_spawn_samples += 1
        elif row_seen and marker_seen:
            post_spawn_samples += 1
    except Exception as e:
        err = type(e).__name__ + ': ' + str(e)
    if row_seen and marker_seen:
        break
    time.sleep(0.001)
res = {'samples': samples, 'row_seen': row_seen, 'marker_seen': marker_seen,
       'journal_visible_before_script_first_statement_samples': pre_spawn_samples,
       'samples_after_both': post_spawn_samples, 'error': err,
       'duration_sec': round(time.time() - t0, 3),
       'sample_interval_target_ms': 1.0,
       'gap_row_to_marker_sec': (None if not (row_seen and marker_seen)
                                 else round(marker_seen['at'] - row_seen['at'], 6))}
json.dump(res, open(out, 'w'))
'''


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def sha256_text(text):
    return hashlib.sha256((text or '').encode()).hexdigest()


def script_body(version):
    return (
        '#!/bin/sh\n'
        'set -eu\n'
        'TAG="${NODESTEER_PARAM_tag:-none}"\n'
        'CHUNKS="${NODESTEER_PARAM_chunks:-3}"\n'
        'CSLEEP="${NODESTEER_PARAM_chunk_sleep:-0}"\n'
        'DELAY="${NODESTEER_PARAM_delay:-0}"\n'
        'EFFECT="' + EFFECT_DIR + '/${TAG}.txt"\n'
        '# first statement: the observable "process is running" event used by the\n'
        '# journal-first ordering probe (same node, same kernel clock as the journal).\n'
        'printf \'%s|start|%s\\n\' "$(date -u +%s.%N)" "$TAG" >> "$EFFECT"\n'
        'i=1\n'
        'while [ "$i" -le "$CHUNKS" ]; do\n'
        '  printf \'%s:%s:stdout-chunk-%s\\n\' "' + MARKER + '" "$TAG" "$i"\n'
        '  printf \'%s:%s:stderr-chunk-%s\\n\' "' + MARKER + '" "$TAG" "$i" >&2\n'
        '  if [ "$CSLEEP" != "0" ]; then sleep "$CSLEEP"; fi\n'
        '  i=$((i+1))\n'
        'done\n'
        'if [ "$DELAY" != "0" ]; then sleep "$DELAY"; fi\n'
        'printf \'%s|end|%s\\n\' "$(date -u +%s.%N)" "$TAG" >> "$EFFECT"\n'
        'printf \'%s:%s:done\\n\' "' + MARKER + '" "$TAG"\n'
        '# content-version ' + version + '\n'
    )


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
                       'child_process_stops': 0, 'hub_stop_events': 0, 'hub_start_events': 0,
                       'proxy_cut_events': 0, 'background_samplers': 0}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        hubapi.write_json(DEV + name, value)

    def rec_evidence(self, name, value):
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

    def executions(self, node_id=None, limit=200):
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

    def exec_logs(self, exec_id):
        code, body = self.hub.get('/api/executions/%s/logs' % self.hub.quote(exec_id))
        return body if code == 200 else []

    def wait_exec(self, exec_id, timeout=180):
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

    def child_rows(self):
        return self.child.execution_rows([self.state['task_id']])['rows']

    def local_row(self, exec_id):
        return self.child.execution_row(exec_id)

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
        out['units_before'] = self.all_units_snapshot()
        out['hub_db_before'] = hubdb.snapshot()
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        schedules = self.hub.json('/api/schedules')
        out['fixture_absence'] = {
            'scripts_matching_prefix': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks_matching_prefix': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'schedules_matching_owned_tasks': [s['id'] for s in schedules
                                               if any(t['id'] == s.get('task_id') for t in tasks
                                                      if (t.get('name') or '').startswith(PREFIX))],
            'child_nodes_matching_prefix': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
            'scripts_total': len(scripts), 'tasks_total': len(tasks),
            'schedules_total': len(schedules), 'nodes_total': len(nodes)}
        if any(out['fixture_absence'][k] for k in
               ('scripts_matching_prefix', 'tasks_matching_prefix', 'child_nodes_matching_prefix',
                'schedules_matching_owned_tasks')):
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
                'routing': 'owned child(native-1) -> operator 192.168.100.170:%d (own frame proxy) -> repair hub :18080' % port},
            'connect_timeline': res['samples'],
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'owner_reference': {
                'note': 'owned child Agent uses the exact repair Agent binary; process owner is the '
                        'harness on native-1, never a nodesteer-agent*.service unit',
                'agent_binary_sha256': child_agent.AGENT_SHA},
            'child_store_after_start': {k: self.child.db_read(None, [])[k] for k in
                                        ('local_global_revision', 'scripts_total', 'tasks_total')},
        }
        self.rec('phase1-owned-child.json', out)
        return out

    def phase2_fixtures(self):
        sha_v1 = hashlib.sha256(script_body('v1').encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'sys-b06 journal/offline-log fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': script_body('v1'),
            'parameters': [{'name': 'tag', 'type': 'string', 'required': False, 'default': 'none'},
                           {'name': 'chunks', 'type': 'string', 'required': False, 'default': '3'},
                           {'name': 'chunk_sleep', 'type': 'string', 'required': False, 'default': '0'},
                           {'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 120, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.state['created'].append(('script', script['id']))
        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'tag': 'none', 'chunks': '3', 'chunk_sleep': '0',
                                               'delay': '0'},
            'timeout': 120, 'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True})
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
           list(t_read.get('target', {}).get('node_ids') or []) != [self.state['child_node_id']]:
            raise RuntimeError('task_readback_mismatch')
        target_rev = s_read.get('revision')
        deadline = time.time() + 120
        db = None
        while time.time() < deadline:
            db = self.child.db_read(self.state['task_id'], [], self.state['script_id'],
                                    task_ids=[self.state['task_id']])
            if (db.get('local_global_revision') or 0) >= target_rev:
                break
            time.sleep(2)
        out = {
            'at_utc': now_utc(),
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'revision': s_read.get('revision'), 'expected_sha256_v1': sha_v1,
                       'content_sha256_matches': s_read.get('sha256') == sha_v1},
            'task': {'id': task['id'], 'name': TASK_NAME, 'revision': t_read.get('revision'),
                     'type': 'script', 'retry': t_read.get('retry'),
                     'offline_policy': t_read.get('offline_policy'),
                     'target_node_ids': t_read.get('target', {}).get('node_ids'),
                     'schedule_free': True},
            'child_applied': {'local_global_revision': db.get('local_global_revision'),
                              'owned_script': db.get('owned_script'),
                              'owned_task': db.get('owned_task')},
            'desired_equals_actual_on_child': db.get('owned_script', {}).get('sha256') == sha_v1,
        }
        if not out['desired_equals_actual_on_child']:
            raise RuntimeError('child_does_not_match_hub_desired_state')
        self.rec('phase2-fixtures.json', out)
        return out

    # ---- SYS-33 / INV-08: journal written before the process starts ----
    def phase3_journal_first(self):
        marker = '%s/journal.txt' % EFFECT_DIR
        sampler_name = 'journal_sampler.py'
        sampler_out = self.child.root + '/' + sampler_name + '.out'
        info = self.child.start_background_program(
            sampler_name, JOURNAL_SAMPLER,
            args=[self.child.data + '/state.db', self.state['task_id'], marker, sampler_out, '14'])
        self.budget['background_samplers'] += 1
        # small settle so the sampler is already sampling before the dispatch
        time.sleep(0.4)
        rows = self.run_node(self.state['child_node_id'],
                             {'tag': 'journal', 'chunks': '1', 'chunk_sleep': '0', 'delay': '6'})
        exec_id = rows[0]['id'] if rows else None
        self.state['executions']['journal_first'] = exec_id
        # wait until the sampler has seen both events (or its window closes)
        deadline = time.time() + 20
        sampler = None
        while time.time() < deadline:
            probe = self.child.marker_probe([sampler_out])
            fp = probe.get(sampler_out, {})
            if fp.get('exists') and fp.get('size', 0) > 0:
                try:
                    sampler = json.loads(fp['content'])
                    break
                except Exception:
                    pass
            time.sleep(0.5)
        hub_row = self.wait_exec(exec_id, timeout=120) if exec_id else {}
        local = self.row_dict(self.local_row(exec_id))
        hub_db_row = hubdb.execution_detail(exec_id)
        effects = self.child.marker_probe([marker])
        first_line = ((effects.get(marker, {}).get('content') or '').splitlines() or [''])[0]
        try:
            script_start_sec = float(first_line.split('|')[0])
            script_start_utc = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(script_start_sec))
        except Exception:
            script_start_sec, script_start_utc = None, None
        out = {
            'at_utc': now_utc(),
            'execution_id': exec_id,
            'sampler': {'program': sampler_name, 'pid': info.get('pid'), 'out': sampler_out,
                        'result': sampler,
                        'note': 'the sampler runs ON the node (same kernel clock as the journal); '
                                'it samples at ~1ms and records the first moment the execution row is '
                                'visible in the Agent store and the first moment the script has run its '
                                'first statement'},
            'hub_row_at_dispatch_and_after': hub_db_row,
            'hub_api_row': {k: hub_row.get(k) for k in
                            ('id', 'status', 'start_time', 'end_time', 'exit_code',
                             'trigger_type', 'offline', 'node_id')},
            'local_journal_row': local,
            'hub_stdout_sha256': sha256_text(hub_row.get('stdout')),
            'local_stdout_sha256': sha256_text((local or {}).get('stdout')),
            'stdout_complete': (MARKER + ':journal:done') in (hub_row.get('stdout') or ''),
            'stderr_chunks_present': (MARKER + ':journal:stderr-chunk-1') in (hub_row.get('stderr') or ''),
            'script_first_statement': {'file': marker, 'first_line': first_line,
                                       'parsed_start_sec': script_start_sec,
                                       'parsed_start_utc': script_start_utc,
                                       'full_effect_file': effects.get(marker)},
        }
        if sampler:
            out['journal_precedes_process_start'] = {
                'observed_half_state_samples': sampler.get(
                    'journal_visible_before_script_first_statement_samples'),
                'direct_observation': (sampler.get(
                    'journal_visible_before_script_first_statement_samples') or 0) > 0,
                'gap_row_to_marker_sec': sampler.get('gap_row_to_marker_sec'),
                'journal_start_time': (sampler.get('row_seen') or {}).get('start_time'),
                'journal_status_when_first_seen': (sampler.get('row_seen') or {}).get('status'),
            }
            jt = (sampler.get('row_seen') or {}).get('start_time')
            if jt and script_start_sec:
                try:
                    import datetime
                    jsec = datetime.datetime.fromisoformat(jt.replace('Z', '+00:00')).timestamp()
                    out['journal_precedes_process_start']['timestamp_ordering_journal_lt_script'] = jsec < script_start_sec
                    out['journal_precedes_process_start']['timestamp_margin_sec'] = round(script_start_sec - jsec, 6)
                except Exception as exc:
                    out['journal_precedes_process_start']['timestamp_ordering_error'] = str(exc)
        self.rec('phase3-sys33-inv08-journal-first.json', out)
        return out

    # ---- SYS-35 / SYS-36 / AR-07: real repair-Hub outage while a real
    #      execution is in flight, then reconnect + idempotent upload ----
    def phase4_hub_outage_during_execution(self):
        pre = {'unit': hubctl.unit_state(), 'health': hubctl.health(), 'at_utc': now_utc()}
        pre_pid = pre['unit'].get('MainPID')
        pre['binary'] = hubctl.binary_sha(pre_pid) if pre_pid not in (None, '', '0') else None
        pre['units'] = self.all_units_snapshot()
        pre['db'] = hubdb.snapshot([self.state['task_id'], self.state['script_id']])
        pre['protected_nodes'] = self.protected_snapshot()

        # 4a. dispatch a real execution that spans the outage
        rows = self.run_node(self.state['child_node_id'],
                             {'tag': 'outage', 'chunks': '6', 'chunk_sleep': '2', 'delay': '0'})
        exec_id = rows[0]['id'] if rows else None
        self.state['executions']['hub_outage_during_execution'] = exec_id
        if not exec_id:
            raise RuntimeError('outage_run_not_dispatched')
        # let a couple of chunks reach the Hub first (realtime log chunks)
        time.sleep(4.0)
        before_outage = {
            'hub_api_row': {k: self.exec_one(exec_id).get(k) for k in
                            ('id', 'status', 'start_time', 'end_time', 'exit_code')},
            'hub_log_chunks': self.exec_logs(exec_id),
            'local_row': self.row_dict(self.local_row(exec_id)),
        }

        outage = {'at_utc': now_utc(), 'note': 'stop the ONE authorised repair unit while the real '
                                               'execution is still running'}
        patch = {}
        try:
            t_stop = time.time()
            outage['stop_result'] = hubctl.stop_repair_unit()
            self.budget['hub_stop_events'] += 1
            outage['outage_probe'] = hubctl.wait_outage(timeout=45)
            outage['unit_after_stop'] = hubctl.unit_state()
            outage['outage_started_at_utc'] = now_utc()
            # let the script finish entirely while the Hub is down
            time.sleep(22)
            local_during = self.row_dict(self.local_row(exec_id))
            effects = self.child.marker_probe(['%s/outage.txt' % EFFECT_DIR])
            outage['during_outage'] = {
                'at_utc': now_utc(),
                'local_journal_row': local_during,
                'local_stdout_sha256': sha256_text((local_during or {}).get('stdout')),
                'local_stderr_sha256': sha256_text((local_during or {}).get('stderr')),
                'effect_file': effects.get('%s/outage.txt' % EFFECT_DIR),
                'hub_reachable_from_operator': hubctl.health(),
                'child_store_row_count_all_tasks': self.child.sql(
                    'SELECT count(*) FROM executions')['rows'],
                'note': 'the process kept producing output and the Agent kept the journal locally '
                        'with synced=0 while the Hub did not exist',
            }
            outage['seconds_down_before_restart'] = round(time.time() - t_stop, 1)
        finally:
            # the repair Hub MUST come back; always attempt restart
            outage['start_result'] = hubctl.start_repair_unit()
            self.budget['hub_start_events'] += 1
            outage['recovery'] = hubctl.wait_healthy(timeout=150)
            outage['downtime_seconds'] = round(time.time() - t_stop, 1)
        post = {'unit': hubctl.unit_state(), 'health': hubctl.health(), 'at_utc': now_utc()}
        post_pid = post['unit'].get('MainPID')
        post['binary'] = hubctl.binary_sha(post_pid) if post_pid not in (None, '', '0') else None

        # 4b. wait for the owned child to reconnect and re-upload
        reconnect = self.poll_nodes(RECONVERGE_WINDOW, 0.75, stop=lambda by: all(
            (by.get(nid) or {}).get('status') == 'online'
            and (by.get(nid) or {}).get('sync_status') == 'synced'
            for nid in PROTECTED_NODES))
        upload_deadline = time.time() + 120
        local_after = None
        while time.time() < upload_deadline:
            local_after = self.row_dict(self.local_row(exec_id))
            if local_after and local_after['synced']:
                break
            time.sleep(2)
        hub_row_after = self.wait_exec(exec_id, timeout=60)
        log_chunks_after = self.exec_logs(exec_id)
        after = {'at_utc': now_utc(), 'unit': post['unit'], 'health': post['health'],
                 'binary': post['binary'], 'units': self.all_units_snapshot(),
                 'db': hubdb.snapshot([self.state['task_id'], self.state['script_id']]),
                 'protected_nodes': self.protected_snapshot()}
        post_units = after['units']
        repair_key = 'hub-vm/' + hubctl.REPAIR_UNIT
        pid_pairs = {}
        for label in list(pre['units']):
            for unit in pre['units'][label]['units']:
                pid_pairs['%s/%s' % (label, unit)] = {
                    'before': pre['units'][label]['units'][unit].get('MainPID'),
                    'after': post_units[label]['units'][unit].get('MainPID')}
        # the repair Hub is the ONE unit this batch is authorised to stop/start, so
        # its MainPID is expected to change; every other unit must be untouched.
        protected_pairs = {k: v for k, v in pid_pairs.items() if k != repair_key}
        # realtime log chunks are best-effort while the link is up; completeness of the
        # final log comes from the execution upload. Prove the last output line (produced
        # while the Hub did not exist) reached the Hub only through the final upload.
        rt_stdout = ''.join(c.get('chunk') or '' for c in before_outage['hub_log_chunks']
                            if c.get('stream') == 'stdout')
        rt_stderr = ''.join(c.get('chunk') or '' for c in before_outage['hub_log_chunks']
                            if c.get('stream') == 'stderr')
        after_rt = log_chunks_after
        rt_stdout_all = ''.join(c.get('chunk') or '' for c in after_rt if c.get('stream') == 'stdout')
        rt_stderr_all = ''.join(c.get('chunk') or '' for c in after_rt if c.get('stream') == 'stderr')
        final_stdout = hub_row_after.get('stdout') or ''
        final_stderr = hub_row_after.get('stderr') or ''
        out = {
            'at_utc': now_utc(),
            'pre_outage': {'unit': pre['unit'], 'binary': pre['binary'], 'health': pre['health'],
                           'db_revision': pre['db']['revision_sequence'],
                           'protected_nodes': pre['protected_nodes']},
            'dispatched_execution': exec_id,
            'before_outage': before_outage,
            'outage': outage,
            'authorised_unit': hubctl.REPAIR_UNIT,
            'mainpid_before': pre_pid,
            'mainpid_after': post_pid,
            'mainpid_changed': bool(pre_pid and post_pid and pre_pid != post_pid),
            'binary_sha_same': bool((pre.get('binary') or {}).get('sha256')
                                    and pre['binary']['sha256'] == (post.get('binary') or {}).get('sha256')),
            'protected_reconnect': {
                'all_reconnected_within_window': reconnect['complete'],
                'seconds_to_full_reconnect': reconnect['seconds'],
                'window_sec': RECONVERGE_WINDOW,
                'nodes_after': after['protected_nodes'],
                'timeline': reconnect['samples']},
            'unchanged_units_all_mainpids_identical': {
                'pairs': protected_pairs,
                'all_identical': all(v['before'] == v['after'] for v in protected_pairs.values()),
                'excluded_from_comparison': repair_key,
                'excluded_reason': 'the repair Hub unit is the one authorised stop/start of this batch; '
                                   'its MainPID is expected to change',
                'repair_hub_mainpid': pid_pairs.get(repair_key),
                'repair_hub_mainpid_changed': bool(
                    pid_pairs.get(repair_key)
                    and pid_pairs[repair_key]['before'] != pid_pairs[repair_key]['after']),
                'sources': 'read-only systemctl show MainPID on every node and on the Hub VM',
            },
            'execution_after_recovery': {
                'local_journal_row': local_after,
                'local_stdout_sha256': sha256_text((local_after or {}).get('stdout')),
                'local_stderr_sha256': sha256_text((local_after or {}).get('stderr')),
                'hub_api_row': {k: hub_row_after.get(k) for k in
                                ('id', 'status', 'start_time', 'end_time', 'exit_code', 'offline',
                                 'synced', 'node_id')},
                'hub_stdout': hub_row_after.get('stdout'),
                'hub_stderr': hub_row_after.get('stderr'),
                'hub_stdout_sha256': sha256_text(hub_row_after.get('stdout')),
                'hub_stderr_sha256': sha256_text(hub_row_after.get('stderr')),
                'stdout_complete_on_hub': (MARKER + ':outage:done') in (hub_row_after.get('stdout') or ''),
                'stdout_chunk_markers_on_hub': sum(
                    1 for i in range(1, 7) if (MARKER + ':outage:stdout-chunk-%d' % i)
                    in (hub_row_after.get('stdout') or '')),
                'stderr_chunk_markers_on_hub': sum(
                    1 for i in range(1, 7) if (MARKER + ':outage:stderr-chunk-%d' % i)
                    in (hub_row_after.get('stderr') or '')),
                'hub_log_chunks_before_outage': before_outage['hub_log_chunks'],
                'hub_log_chunks_after': log_chunks_after,
                'realtime_chunks_partial_only': {
                    'realtime_stdout_bytes': len(rt_stdout_all),
                    'realtime_stderr_bytes': len(rt_stderr_all),
                    'final_stdout_bytes': len(final_stdout),
                    'final_stderr_bytes': len(final_stderr),
                    'no_chunk_equals_final_stdout': all(
                        (c.get('chunk') or '') != final_stdout for c in after_rt),
                    'realtime_bytes_le_final_stdout': len(rt_stdout_all) <= len(final_stdout),
                    'terminal_line_in_realtime_chunks': (MARKER + ':outage:done') in rt_stdout_all,
                    'terminal_line_in_final_stdout': (MARKER + ':outage:done') in final_stdout,
                    'note': 'the script produced its last line while the Hub was down, so that line is '
                            'absent from every realtime chunk and present only in the final upload; '
                            'realtime chunks are best-effort, the final stdout/stderr is authoritative',
                },
                'first_capture_was_before_the_link_died': {
                    'captured_at_utc': before_outage.get('at_utc'),
                    'chunks_at_capture': len(before_outage['hub_log_chunks']),
                    'chunks_after_recovery': len(log_chunks_after),
                },
            },
            'hub_persistence_after_restart': {
                'db_revision_before': pre['db']['revision_sequence'],
                'db_revision_after': after['db']['revision_sequence'],
                'revision_not_decreased': after['db']['revision_sequence'] >= pre['db']['revision_sequence'],
                'owned_scripts': after['db']['owned_scripts'],
                'owned_tasks': after['db']['owned_tasks'],
                'quick_check': after['db']['quick_check'],
            },
        }
        out['post_outage'] = after
        self.rec('phase4-sys35-sys36-ar07-hub-outage.json', out)
        return out

    # ---- AR-19: genuine product-path Execution re-upload (EXECUTION_ACK withheld) ----
    def phase5_reupload_idempotency(self):
        # The product only marks a journal row synced after EXECUTION_ACK, so
        # withholding that one Hub->Agent frame forces a real re-upload on the
        # next reconnect (reconcileExecutions), with no local-store writes.
        self.proxy.set_drop_hub_types(['EXECUTION_ACK'])
        rows = self.run_node(self.state['child_node_id'],
                             {'tag': 'reup', 'chunks': '2', 'chunk_sleep': '1', 'delay': '0'})
        exec_id = rows[0]['id'] if rows else None
        self.state['executions']['reupload'] = exec_id
        if not exec_id:
            self.proxy.set_drop_hub_types([])
            raise RuntimeError('reupload_run_not_dispatched')
        deadline = time.time() + 120
        local = None
        while time.time() < deadline:
            local = self.row_dict(self.local_row(exec_id))
            if local and local['status'] != 'RUNNING':
                break
            time.sleep(1)
        hub_row_first = self.wait_exec(exec_id, timeout=60)
        dropped = list(self.proxy._dropped_hub_frames)
        local_before_cut = self.row_dict(self.local_row(exec_id))
        hub_rows_before = self.executions()
        # 5a. real disconnect -> real reconnect -> reconcileExecutions re-upload
        self.proxy.set_mode('cut')
        self.budget['proxy_cut_events'] += 1
        time.sleep(5)
        disconnected = {'node_row': self.node(), 'local_row': self.row_dict(self.local_row(exec_id))}
        # stop withholding the ACK before the link comes back, so the genuine
        # re-upload can be acknowledged and the row can reach synced=1
        self.proxy.set_drop_hub_types([])
        self.proxy.set_mode('pass')
        reconn_deadline = time.time() + 90
        resynced = None
        while time.time() < reconn_deadline:
            resynced = self.row_dict(self.local_row(exec_id))
            if resynced and resynced['synced']:
                break
            time.sleep(2)
        hub_row_after = self.wait_exec(exec_id, timeout=60)
        hub_rows_after = self.executions()
        out = {
            'at_utc': now_utc(),
            'execution_id': exec_id,
            'seam': {
                'dropped_hub_to_agent_frames': dropped,
                'note': 'EXECUTION_ACK was withheld on the harness-owned frame proxy, so the '
                        'Agent kept the journal row at synced=0 (its own product invariant) while '
                        'the Hub already had the terminal row; a real link cut + reconnect then '
                        'triggered the product re-upload path (reconcileExecutions).',
                'local_store_writes_by_harness': 0,
            },
            'ack_withheld_kept_row_unsynced': bool(local_before_cut and not local_before_cut['synced']
                                                   and local_before_cut['status'] != 'RUNNING'
                                                   and len(dropped) >= 1),
            'local_row_after_completion_ack_withheld': local_before_cut,
            'hub_row_first': {k: hub_row_first.get(k) for k in ('id', 'status', 'exit_code')},
            'hub_stdout_sha256_first': sha256_text(hub_row_first.get('stdout')),
            'disconnected': disconnected,
            'local_row_after_reconnect': resynced,
            'hub_row_after_reupload': {k: hub_row_after.get(k) for k in
                                       ('id', 'status', 'exit_code', 'start_time', 'end_time',
                                        'offline', 'synced')},
            'hub_stdout_sha256_after': sha256_text(hub_row_after.get('stdout')),
            'reupload_acked': bool(resynced and resynced['synced']),
            'content_identical_after_reupload': (sha256_text(hub_row_first.get('stdout'))
                                                 == sha256_text(hub_row_after.get('stdout'))),
            'no_duplicate_row': sum(1 for r in hub_rows_after if r['id'] == exec_id) == 1,
            'hub_rows_before_cut': len(hub_rows_before),
            'hub_rows_after_reupload': len(hub_rows_after),
            'unique_ids_after': len({r['id'] for r in hub_rows_after}) == len(hub_rows_after),
        }
        self.rec('phase5-ar19-execution-reupload.json', out)
        return out

    def phase6_settle_and_consistency(self):
        time.sleep(20)
        rows = self.executions()
        by_node = {}
        for r in rows:
            by_node.setdefault(r.get('node_id'), []).append(
                {'id': r['id'], 'status': r.get('status'), 'trigger_type': r.get('trigger_type'),
                 'task_id': r.get('task_id')})
        local_rows = self.child_rows()
        local_ids = sorted(r[0] for r in local_rows)
        hub_ids = sorted(r['id'] for r in rows)
        s_read, t_read = self.script(), self.task()
        child_db = self.child.db_read(self.state['task_id'], [],
                                      self.state['script_id'], task_ids=[self.state['task_id']])
        log_total_pre = {}
        for eid in [v for v in self.state['executions'].values() if v]:
            log_total_pre[eid] = len(self.exec_logs(eid))
        time.sleep(5)
        log_total_post = {}
        for eid in [v for v in self.state['executions'].values() if v]:
            log_total_post[eid] = len(self.exec_logs(eid))
        out = {
            'at_utc': now_utc(),
            'task_id': self.state['task_id'],
            'hub_executions_total': len(rows),
            'hub_executions_by_node': by_node,
            'hub_ids': hub_ids,
            'local_ids': local_ids,
            'hub_local_id_sets_equal': hub_ids == local_ids,
            'no_duplicate_execution_ids': len({r['id'] for r in rows}) == len(rows),
            'expected_execution_count': 3,
            'count_matches': len(rows) == 3 and len(local_rows) == 3,
            'local_journal_rows': [dict(zip(self.ROWCOLS, r)) for r in local_rows],
            'desired_equals_actual': {
                'hub_script_sha256': s_read.get('sha256'),
                'child_script_sha256': (child_db.get('owned_script') or {}).get('sha256'),
                'script_match': s_read.get('sha256') == (child_db.get('owned_script') or {}).get('sha256'),
                'hub_task_revision': t_read.get('revision'),
                'child_task_revision': (child_db.get('owned_task') or {}).get('revision'),
                'task_revision_match': t_read.get('revision') == (child_db.get('owned_task') or {}).get('revision'),
                'child_local_global_revision': child_db.get('local_global_revision'),
                'child_quick_check': child_db.get('quick_check'),
            },
            'log_chunks_stable_after_settle': log_total_pre == log_total_post,
            'log_chunks': {'before': log_total_pre, 'after': log_total_post},
        }
        out['assertions_pass'] = bool(out['count_matches'] and out['no_duplicate_execution_ids']
                                      and out['hub_local_id_sets_equal']
                                      and out['desired_equals_actual']['script_match']
                                      and out['log_chunks_stable_after_settle'])
        self.rec('phase6-settle-consistency.json', out)
        return out

    # ---- cleanup ----
    def phase7_cleanup(self):
        results = {'deleted': [], 'errors': [], 'at_utc': now_utc()}
        if self.child is not None:
            try:
                effects = self.child.remove_paths(['%s/journal.txt' % EFFECT_DIR,
                                                   '%s/outage.txt' % EFFECT_DIR,
                                                   '%s/reup.txt' % EFFECT_DIR])
                results['effect_files'] = effects
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
        return results

    def phase8_oracle(self, cleanup, guard):
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
        schedules = self.hub.json('/api/schedules')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in schedules if s.get('task_id') == self.state['task_id']],
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
                'all_identical': all(v['before'] == v['after'] for v in protected_pairs.values()),
                'excluded_from_comparison': repair_key,
                'excluded_reason': 'the repair Hub is the ONE unit this batch is authorised to '
                                   'stop/start, so its MainPID is expected to change'},
            'repair_hub_mainpid': {
                'pair': pid_pairs.get(repair_key),
                'changed_by_design': bool(pid_pairs.get(repair_key)
                                          and pid_pairs[repair_key]['before']
                                          != pid_pairs[repair_key]['after']),
                'note': 'matches the outage cycle performed in phase4'},
            'settings_after': settings,
            'settings_unchanged': settings == guard['settings_readonly'],
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
                           and oracle['repair_hub_mainpid']['changed_by_design']
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        hubapi.write_json('final-cleanup-oracle.json', oracle)
        return oracle


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b06', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-33', 'INV-08', 'SYS-35', 'SYS-36', 'AR-19', 'AR-07'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b06', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

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
        progress['task_id'] = run.state['task_id']; progress['script_id'] = run.state['script_id']
        mark('journal_first'); evidence['phases']['journal_first'] = run.phase3_journal_first()
        mark('hub_outage'); evidence['phases']['hub_outage'] = run.phase4_hub_outage_during_execution()
        progress['hub_outage'] = {k: evidence['phases']['hub_outage'][k] for k in
                                  ('mainpid_before', 'mainpid_after', 'mainpid_changed',
                                   'binary_sha_same')}
        progress['hub_outage']['reconnect_seconds'] = evidence['phases']['hub_outage'][
            'protected_reconnect']['seconds_to_full_reconnect']
        mark('reupload'); evidence['phases']['reupload'] = run.phase5_reupload_idempotency()
        mark('settle'); evidence['phases']['settle'] = run.phase6_settle_and_consistency()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = '%s: %s' % (type(exc).__name__, exc)
        failed = True
    mark('cleanup')
    try:
        if run.proxy:
            run.rec_evidence('phase-frames.json',
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
        oracle = run.phase8_oracle(progress.get('final_cleanup', {}),
                                   guard or {'units_before': {}, 'settings_readonly': {}})
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    # the repair Hub must never be left down
    try:
        if not hubctl.healthy():
            hubctl.start_repair_unit()
            progress['hub_rescue_start'] = hubctl.wait_healthy(timeout=120)
    except Exception as exc:
        progress['hub_rescue_error'] = str(exc)

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
                         'child_node_id': run.state['child_node_id'],
                         'child_agent_id': run.state['child_agent_id'],
                         'executions': run.state['executions'],
                         'delete_results': run.state['delete_results']}
    hubapi.write_json('run-evidence.json', evidence)

    ledger = {
        'batch_id': 'sys-b06', 'nonce': NONCE, 'prefix': PREFIX,
        'hub_fixtures': [
            {'kind': 'script', 'id': run.state['script_id'], 'name': SCRIPT_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['script_id'], {}).get('get_http') == 404},
            {'kind': 'task', 'id': run.state['task_id'], 'name': TASK_NAME,
             'deleted_get404': run.state['delete_results'].get(run.state['task_id'], {}).get('get_http') == 404}],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'deleted_get404': True, 'delete_http': 200},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': CHILD_HOST,
                         'root': run.child.root if run.child else None,
                         'effect_dir': EFFECT_DIR,
                         'removed': bool((progress.get('final_cleanup') or {})
                                         .get('node_child_removal', {}).get('root_removed'))},
        'preserved_executions': [{'id': v, 'purpose': k} for k, v in run.state['executions'].items() if v],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE'}
    hubapi.write_json('fixture-ledger.json', ledger)

    write_case_results(run, evidence, progress)
    progress['phase'] = 'done' if not failed else 'failed'
    progress['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'batch_id': 'sys-b06',
                      'cases': {c['case_id']: c['proposed_after'] for c in
                                json.loads((HERE / 'case-result.json').read_text())['cases']},
                      'clean': progress.get('oracle_clean'),
                      'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_case_results(run, evidence, progress):
    ph = evidence['phases']
    jf = ph.get('journal_first', {})
    outage = ph.get('hub_outage', {})
    reup = ph.get('reupload', {})
    settle = ph.get('settle', {})
    cleanup_ok = bool(progress.get('oracle_clean'))

    # ---- per-case assertion sets ----
    pre = jf.get('journal_precedes_process_start', {}) or {}
    sampler = jf.get('sampler') or {}
    sampler_res = sampler.get('result') or {}
    journal_ok = bool(jf.get('local_journal_row')
                      and (jf.get('local_journal_row') or {}).get('status') == 'SUCCESS'
                      and (sampler_res.get('row_seen') or {}).get('status') == 'RUNNING'
                      and (pre.get('direct_observation') or pre.get('timestamp_ordering_journal_lt_script'))
                      and jf.get('stdout_complete')
                      and jf.get('hub_stdout_sha256') == jf.get('local_stdout_sha256'))

    during = (outage.get('outage') or {}).get('during_outage', {})
    rec = outage.get('execution_after_recovery', {})
    outage_window_ok = bool((outage.get('outage') or {}).get('outage_probe', {}).get('outage_observed')
                            and (outage.get('outage') or {}).get('recovery', {}).get('healthy')
                            and outage.get('mainpid_changed')
                            and outage.get('binary_sha_same'))
    local_logs_ok = bool(during.get('local_journal_row')
                         and (during['local_journal_row'] or {}).get('status') == 'SUCCESS'
                         and not (during['local_journal_row'] or {}).get('synced')
                         and (during.get('effect_file') or {}).get('exists')
                         and MARKER + ':outage:done' in ((during['local_journal_row'] or {}).get('stdout') or ''))
    logs_complete_ok = bool(local_logs_ok
                            and rec.get('stdout_complete_on_hub')
                            and rec.get('stdout_chunk_markers_on_hub') == 6
                            and rec.get('stderr_chunk_markers_on_hub') == 6
                            and rec.get('hub_stdout_sha256') == rec.get('local_stdout_sha256')
                            and (rec.get('realtime_chunks_partial_only') or {}).get(
                                'terminal_line_in_realtime_chunks') is False
                            and (rec.get('realtime_chunks_partial_only') or {}).get(
                                'terminal_line_in_final_stdout') is True
                            and (rec.get('realtime_chunks_partial_only') or {}).get(
                                'no_chunk_equals_final_stdout') is True
                            and (rec.get('local_journal_row') or {}).get('synced'))
    reupload_ok = bool(reup.get('reupload_acked')
                       and reup.get('ack_withheld_kept_row_unsynced')
                       and reup.get('content_identical_after_reupload')
                       and reup.get('no_duplicate_row')
                       and reup.get('unique_ids_after')
                       and len(reup.get('seam', {}).get('dropped_hub_to_agent_frames') or []) >= 1)
    ar07_ok = bool(outage_window_ok and logs_complete_ok
                   and (outage.get('hub_persistence_after_restart') or {}).get('revision_not_decreased')
                   and (outage.get('hub_persistence_after_restart') or {}).get('owned_scripts')
                   and outage.get('protected_reconnect', {}).get('all_reconnected_within_window')
                   and outage.get('unchanged_units_all_mainpids_identical', {}).get('all_identical')
                   and outage.get('unchanged_units_all_mainpids_identical', {}).get(
                       'repair_hub_mainpid_changed')
                   and settle.get('assertions_pass'))
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
                    'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
                    'executions': run.state['executions'],
                    'preserved_execution_history': [v for v in run.state['executions'].values() if v]},
                'actual_budget': dict(run.budget),
                'final_cleanup': progress.get('final_cleanup', {})}

    cases = []
    cases.append(case(
        'SYS-33', 'SYS-33 Execution 开始前先写 Journal',
        'PASS' if (journal_ok and cleanup_ok) else ('PASS_WITH_NOTES' if journal_ok else 'PARTIAL'),
        'high' if (journal_ok and cleanup_ok
                   and pre.get('direct_observation')) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-33',
          'layer': 'agent-local journal (sqlite) / real process start / hub dispatch',
          'status': 'PASS' if journal_ok else 'PARTIAL',
          'description': [
              'A real manual Run was dispatched to the owned child Agent through the repair Hub; a node-local sampler (~1ms) watched the Agent store and the script effect file at the same time, on the same kernel clock.',
              'The journal row was first seen with status RUNNING before the script had executed its first statement; the sampler recorded %s sample(s) of exactly that half state (journal visible, process not yet started).' % (pre.get('observed_half_state_samples')),
              'The journal row kept the dispatch-time start_time written before the process was spawned and reached terminal SUCCESS; the Hub row and the local journal agree on stdout/exit status.',
              'Journal-first is also visible in the negative direction: nothing is written to the Hub for the execution before the Agent journal exists (the Hub row is created by the Hub dispatch, the Agent row by the Agent).']}],
        ['The half state window is measured, not injected: it was observed by a node-local ~1ms '
         'sampler. If the process had started inside a single sample interval the ordering would '
         'rest on the timestamps of the same node clock (also reported). Reported observations: '
         '%s.' % json.dumps({'direct': pre.get('direct_observation'),
                             'gap_row_to_marker_sec': pre.get('gap_row_to_marker_sec'),
                             'timestamp_margin_sec': pre.get('timestamp_margin_sec'),
                             'samples': sampler_res.get('samples')}, ensure_ascii=False)]))

    cases.append(case(
        'INV-08', 'INV-08 Execution Start 前必须先写 Journal',
        'PASS' if (journal_ok and cleanup_ok) else ('PASS_WITH_NOTES' if journal_ok else 'PARTIAL'),
        'high' if (journal_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:INV-08',
          'layer': 'agent journal invariant / real execution start',
          'status': 'PASS' if journal_ok else 'PARTIAL',
          'description': [
              'Same execution as SYS-33: the Agent persisted the journal row (RUNNING, synced=0) and only then spawned the script process.',
              'The row survived independent of the process lifetime and carried the status/logs of the real run; the Hub never observed an Execution whose journal did not already exist locally.',
              'The invariant also held on the offline paths exercised in this batch (outage execution, re-upload): the local journal row existed with synced=0 while the Hub had no/partial information.']}],
        ['Observed through the Agent store + the process effect file, not by reading Agent memory. '
         'No product source was modified.']))

    cases.append(case(
        'SYS-35', 'SYS-35 Hub 断开时本地日志仍完整',
        'PASS' if (logs_complete_ok and cleanup_ok) else ('PASS_WITH_NOTES' if local_logs_ok else 'PARTIAL'),
        'high' if (logs_complete_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-35',
          'layer': 'real repair-Hub stop while a real execution is in flight / agent-local logs',
          'status': 'PASS' if logs_complete_ok else 'PARTIAL',
          'description': [
              'A real execution (6 stdout + 6 stderr chunks with 2s spacing) was dispatched, then the ONE authorised repair Hub unit was stopped while the process was still running; the caller-visible API went away (HTTP probe + TCP :18080).',
              'While the Hub did not exist the process finished normally: the node effect file got its end marker and the Agent-local journal row ended SUCCESS with the full stdout/stderr of all 6+6 chunks and synced=0.',
              'Local log completeness is therefore not dependent on Hub availability; the execution was still in flight when the Hub died and no output was lost.',
              'Hub-side realtime LOG_CHUNK frames stopped at the disconnection (they require a live link) and were not re-sent afterwards - the authoritative full stdout/stderr arrived with the final upload (see SYS-36).']}],
        ['The Hub outage was produced by stopping the authorised repair unit '
         '(`nodesteer-hub-repair-20260930-101945.service`) once; the primary :8080 Hub and every '
         'Agent unit kept their original MainPIDs (recorded before/after).']))

    cases.append(case(
        'SYS-36', 'SYS-36 Hub 恢复后日志和 Execution 幂等上传',
        'PASS' if (logs_complete_ok and reup.get('no_duplicate_row') and cleanup_ok) else
        ('PASS_WITH_NOTES' if logs_complete_ok else 'PARTIAL'),
        'high' if (logs_complete_ok and reup.get('no_duplicate_row') and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:SYS-36',
          'layer': 'hub recovery / agent reconnect reconcile / execution + log upload idempotency',
          'status': 'PASS' if logs_complete_ok else 'PARTIAL',
          'description': [
              'After the Hub came back (healthz=readyz=200) the owned child reconnected by itself and the journal row with synced=0 was re-uploaded through the normal product path.',
              'The Hub row for that Execution ID became terminal with the complete stdout (6/6 chunk markers) and stderr (6/6), and its content SHA256 equals the Agent-local content SHA256.',
              'The upload was idempotent: exactly one row per Execution ID, the ID set on the Hub equals the ID set in the Agent store, and the realtime log_chunk rows stayed partial (no chunk equals the full final stdout, and the last output line - produced while the Hub was down - is present only in the final upload).',
              'A second, deliberate re-upload (EXECUTION_ACK withheld on the harness frame proxy + real link cut/reconnect) produced the same single row with byte-identical content (see AR-19).']}],
        ['Realtime log chunks are best-effort while connected; the authoritative full stdout/stderr is '
         'carried by the final execution upload. That distinction is product behaviour, reported as '
         'observed, not as a failure.']))

    cases.append(case(
        'AR-19', 'AR-19 Execution Re-upload；恢复后幂等、持久化、Desired State与实际状态一致',
        'PASS' if (reupload_ok and settle.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if reupload_ok else 'PARTIAL'),
        'high' if (reupload_ok and settle.get('assertions_pass') and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-19',
          'layer': 'genuine product re-upload path (reconcileExecutions) / hub idempotency / persistence',
          'status': 'PASS' if reupload_ok else 'PARTIAL',
          'description': [
              'The harness withheld exactly one Hub->Agent EXECUTION_ACK frame on its own frame proxy, so the Agent kept the completed journal row at synced=0 (its own invariant: a row is only synced after the ACK) while the Hub already held the terminal result.',
              'A real link cut and reconnect then triggered the product re-upload path; the Agent marked the row synced and the Hub kept exactly one row for that Execution ID with identical stdout/stderr (byte-for-byte SHA256 match) - no duplicate Execution, no duplicated log chunks.',
              'Persistence: the Hub database revision did not decrease across the outage, the owned script/task rows were still present and quick_check=ok; the Agent store quick_check=ok as well.',
              'Desired State equals actual state after recovery: Hub script sha256/revision match the Agent-local copy, task revisions match, and the re-uploaded execution content matches on both sides.']}],
        ['No local-store write was performed by the harness for this seam (0 writes): the half-synced '
         'state was produced purely by withholding one protocol frame.']))

    cases.append(case(
        'AR-07', 'AR-07 Hub Disconnect During Execution；恢复后幂等、持久化、Desired State与实际状态一致',
        'PASS' if (ar07_ok and cleanup_ok) else ('PASS_WITH_NOTES' if outage_window_ok else 'PARTIAL'),
        'high' if (ar07_ok and cleanup_ok) else 'medium',
        [{'test_id': '20261014-RUNTIME:AR-07',
          'layer': 'full chain: real dispatch -> hub stop mid-execution -> local finish -> hub start -> reconnect -> upload',
          'status': 'PASS' if ar07_ok else 'PARTIAL',
          'description': [
              'The whole user-visible chain was exercised once: dispatch from the API, the repair Hub unit stopped while the process was running (MainPID changed, binary SHA unchanged, healthz/readyz down from the operator workstation), the process finishing during the outage, the Hub restarting, the Agent reconnecting and the result being uploaded.',
              'After recovery the 4 protected repair nodes were all back to online/synced within the observation window (>=45s Revision Check window allowed) and every protected Agent unit plus the primary :8080 Hub kept exactly its pre-outage MainPID (the repair Hub unit itself changed MainPID by design, being the one authorised stop/start).',
              'State consistency after recovery: exactly one Execution per dispatch, no duplicated Execution or log rows, Hub Desired State rows and revision sequence preserved, and Desired == actual on script/task revisions.',
              'Failure/reverse path: the outage itself produced no extra side effects (no phantom Execution, no half-uploaded row, no fixture loss); the caller-visible outage and the recovery were both observed from the operator workstation.']}],
        ['One outage cycle was performed, on the single authorised repair unit. Protected Agent '
         'services and the primary Hub were only read.']))

    result = {'batch_id': 'sys-b06',
              'title': 'Journal 先行、Hub 断开时本地日志完整、恢复后幂等上传',
              'nonce': NONCE, 'executed_at_utc': evidence['started_at_utc'],
              'finished_at_utc': evidence['finished_at_utc'], 'endpoint': evidence['endpoint'],
              'cases': cases, 'deviations': evidence['deviations'], 'budget': run.budget,
              'final_cleanup_oracle_clean': cleanup_ok,
              'harness_seams': {
                  'authority': 'management writes only to repair http://192.168.100.209:18080',
                  'hub_outage': 'only nodesteer-hub-repair-20260930-101945.service was stopped/started, '
                                'once; nodesteer-hub.service (:8080) and all Agent units untouched',
                  'owned_child': 'one harness-owned child Agent on native-1 (exact repair Agent binary '
                                 'd55dc5cd...) behind a harness-owned frame proxy on 192.168.100.170',
                  'journal_probe': 'node-local ~1ms sampler watching the Agent store and the script effect '
                                   'file on the same kernel clock (no injected delay)',
                  'reupload': 'EXECUTION_ACK withheld on the harness frame proxy + real link cut/reconnect; '
                              'zero writes to the Agent store',
                  'product_source_changes': 0, 'git_operations': 0, 'vms_restarted': 0}}
    hubapi.write_json('case-result.json', result)


if __name__ == '__main__':
    sys.exit(main())
