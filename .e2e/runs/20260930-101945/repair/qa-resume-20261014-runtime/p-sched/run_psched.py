#!/usr/bin/env python3
"""p-sched runtime batch: Cron/owner trigger source / misfire / cancel+process-group.

Cases: SYS-19, SYS-20, SYS-21, INV-06, E2E-05, E2E-06, E2E-07, E2E-08,
       SYS-38, SYS-39, SYS-40, AR-21, AR-22, AR-23.

Real evidence chain (no product source changes, no protected Agent/Hub touched,
no Hub restart, node native-2 only):
  operator/API act -> real request -> Hub commit/dispatch -> real frame on the
  wire (owned frame proxy) -> owned child Agent state change -> SQLite/API
  readback -> on-node process ground truth.

Owned fixtures only (nonce-prefixed), all deleted + GET404 at the end.
"""
import hashlib
import json
import shlex
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import child_agent
import hubapi
import hubdb
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-psched-%s-' % NONCE
MARKER_A = 'qa-psched-marker-a'
MARKER_B = 'qa-psched-marker-b'
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

BODY_MARKER = (
    "#!/bin/sh\n"
    "set -eu\n"
    "d=\"%s\"\n"
    "mkdir -p \"$d\"\n"
    "ts=$(date -u +%%Y-%%m-%%dT%%H:%%M:%%S.%%NZ)\n"
    "printf '%%s %%s\\n' '" + MARKER_A + "' \"$ts\" >> \"$d/invocations.log\"\n"
    "printf '%%s %%s\\n' '" + MARKER_A + "' \"$ts\"\n"
)
BODY_BADUSER = (
    "#!/bin/sh\n"
    "set -eu\n"
    "printf 'should-not-run\\n'\n"
)
BODY_FAIL = (
    "#!/bin/sh\n"
    "set -u\n"
    "d=\"%s\"\n"
    "mkdir -p \"$d\"\n"
    "ts=$(date -u +%%Y-%%m-%%dT%%H:%%M:%%S.%%NZ)\n"
    "printf '" + MARKER_A + " attempt %%s\\n' \"$ts\" >> \"$d/retry-attempts.log\"\n"
    "printf '" + MARKER_A + " attempt %%s\\n' \"$ts\"\n"
    "exit 3\n"
)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    if not s:
        return None
    t = s.replace('Z', '+00:00')
    # normalise nanoseconds to microseconds for datetime.fromisoformat
    try:
        if '.' in t:
            head, rest = t.split('.', 1)
            digits = ''
            i = 0
            while i < len(rest) and rest[i].isdigit():
                digits += rest[i]
                i += 1
            frac = (digits + '000000')[:6]
            t = head + '.' + frac + rest[i:]
        return datetime.fromisoformat(t)
    except Exception:
        return None


def wait_for(cond, timeout=60, interval=0.5, label='condition'):
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


def minute_of(scheduled_time):
    dt = parse_iso(scheduled_time)
    return None if dt is None else dt.strftime('%Y-%m-%dT%H:%M')


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxies = {}
        self.children = {}
        self.state = {
            'started_utc': now_utc(),
            'fixtures': {},
            'nodes': {},
            'schedules': {},
            'executions': {},
            'created': [],
            'deleted': [],
            'delete_results': {},
        }
        self.budget = {'api_mutations': 0, 'child_process_starts': 0, 'child_process_stops': 0}
        self.settings_snapshot = {}

    # ---------------- helpers ----------------
    def rec(self, name, value):
        hubapi.write_json(name, value)

    def ev(self, name, value):
        hubapi.write_json(DEV + name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def child(self, label='a'):
        return self.children.get(label)

    def hub_execs(self, task_id, limit=300):
        rows = self.hub.json('/api/executions?task_id=%s&limit=%d' % (self.hub.quote(task_id), limit))
        return sorted(rows, key=lambda r: (r.get('scheduled_time') or '', r.get('start_time') or ''))

    def local_execs(self, label, task_id):
        rows = self.child(label).db_query(
            'SELECT id,task_id,script_id,trigger_type,scheduled_time,status,exit_code,offline,'
            'synced,start_time,end_time FROM executions WHERE task_id=? ORDER BY scheduled_time',
            [task_id])['rows']
        return rows

    def wait_child_online(self, node_id, timeout=90):
        def probe():
            n = self.node(node_id) or {}
            return n if n.get('status') == 'online' and n.get('sync_status') == 'synced' else None
        return wait_for(probe, timeout=timeout, interval=1.0, label='child_online')

    def wait_local_schedules(self, label, ids, timeout=90):
        def probe():
            ph = ','.join(['?'] * len(ids))
            rows = self.child(label).db_query(
                'SELECT id,enabled,revision FROM schedules WHERE id IN (%s)' % ph, ids)['rows']
            return rows if len(rows) == len(ids) else None
        return wait_for(probe, timeout=timeout, interval=1.0, label='local_schedules')

    # ---------------- phase 0: guard ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc()}
        out['repair_healthz'] = self.hub.get('/healthz')[0]
        out['repair_readyz'] = self.hub.get('/readyz')[0]
        if out['repair_healthz'] != 200 or out['repair_readyz'] != 200:
            raise RuntimeError('repair_hub_not_healthy')
        nodes = self.nodes_by_id()
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = nodes.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
                                       'global_revision': n.get('global_revision')}
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        self.settings_snapshot = settings
        out['settings'] = settings
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        schedules = self.hub.json('/api/schedules')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in schedules if (s.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        out['owned_prefix_leftovers'] = leftovers
        out['owned_prefix_clear'] = not any(leftovers.values())
        if not out['owned_prefix_clear']:
            raise RuntimeError('owned_fixture_already_present')

        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '', label='a').inventory()
        out['native2_pre'] = {
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_unit': inv['repair_agent_unit'],
            'any_psched_processes': inv['any_psched_processes'],
            'owned_child_processes': inv['owned_child_processes'],
        }
        if inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native2_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        if inv['primary_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('primary_agent_unit_not_active')
        if inv['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('repair_agent_unit_not_active')

        probe = (
            "import json,subprocess,hashlib\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=MainPID','%s'],capture_output=True,text=True).stdout.strip()\n"
            "pid=u.split('=')[1]\n"
            "exe=subprocess.run(['readlink','-f','/proc/'+pid+'/exe'],capture_output=True,text=True).stdout.strip()\n"
            "sha=hashlib.sha256(open(exe,'rb').read()).hexdigest()\n"
            "print(json.dumps({'unit_main_pid':pid,'exe':exe,'sha256':sha}))\n"
        ) % HUB_UNIT
        cp = __import__('subprocess').run(_hub_ssh() + ['python3', '-'], input=probe,
                                          capture_output=True, text=True, timeout=60)
        hub_probe = json.loads([l for l in cp.stdout.splitlines() if l.strip()][-1])
        out['hub_process'] = hub_probe
        out['hub_binary_sha_matches'] = hub_probe['sha256'] == HUB_SHA
        if not out['hub_binary_sha_matches']:
            raise RuntimeError('hub_binary_sha_mismatch')
        db = hubdb.snapshot([])
        out['hub_db'] = {'revision_sequence': db['revision_sequence'], 'quick_check': db['quick_check'],
                         'revision_changes_total': db['revision_changes_total']}
        if db['quick_check'] != 'ok':
            raise RuntimeError('hub_db_quick_check_failed')
        self.ev('phase0-guard.json', out)
        return out

    # ---------------- phase 1: owned child behind the frame proxy ----------------
    def phase1_child(self, label, deployment_mode):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child-' + label,
            'node_ip': '192.168.100.206',
            'hub_address': 'http://192.168.100.209:18080',
        })
        self.budget['api_mutations'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s %s' % (code, enroll))
        node_id, agent_id = enroll['node_id'], enroll['agent_id']

        proxy = FrameProxy('ws://192.168.100.209:18080', label=label)
        port = proxy.start()
        self.proxies[label] = proxy
        child = child_agent.OwnedChild(NONCE, port, node_id, agent_id, PREFIX + 'child-' + label,
                                      '192.168.100.206', self.hub.cred['registration_token'],
                                      deployment_mode=deployment_mode, label=label)
        self.children[label] = child
        started = child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(node_id, timeout=90)
        if not node:
            raise RuntimeError('owned_child_did_not_reach_online_synced:' + label)
        self.state['nodes'][label] = {'id': node_id, 'agent_id': agent_id}
        frames = proxy.frame_summary() if hasattr(proxy, 'frame_summary') else proxy.frames
        out = {
            'at_utc': now_utc(), 'label': label, 'deployment_mode': deployment_mode,
            'enrollment': {'http': code, 'node_id': node_id, 'agent_id': agent_id,
                           'gateway_url': enroll.get('gateway_url'),
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {'root_on_native2': child.root, 'config_mode': '0600', 'data_dir': child.data,
                            'launcher': started.get('owned_processes'), 'proxy_listen_port': port,
                            'routing': 'child(native-2) -> operator 192.168.100.170:%d (frame proxy) -> repair hub :18080' % port},
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'frame_types_in_order': [f['type'] for f in frames],
        }
        self.ev('phase1-owned-child-%s.json' % label, out)
        return out

    # ---------------- phase 2: fixtures ----------------
    def _create_script(self, name, body, desc, run_user=''):
        body_json = {
            'name': name, 'description': desc, 'interpreter': 'shell', 'content': body,
            'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True}
        if run_user:
            body_json['run_user'] = run_user
        code, s = self.hub.post('/api/scripts', body_json)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, s))
        self.state['created'].append(('script', s['id'], name))
        return s

    def _create_task(self, name, node_id, script_id=None, command=None, timeout=30, retry=0):
        body = {'name': name, 'type': 'script' if script_id else 'command',
                'target': {'type': 'node', 'node_ids': [node_id]},
                'parameters': [], 'timeout': timeout, 'retry': retry,
                'offline_policy': 'hub_online_required', 'enabled': True}
        if script_id:
            body['script_id'] = script_id
        else:
            body['command'] = command
        code, t = self.hub.post('/api/tasks', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, t))
        self.state['created'].append(('task', t['id'], name))
        return t

    def _create_schedule(self, name, task_id, **kw):
        body = {'task_id': task_id, 'type': kw['type'], 'expression': kw.get('expression', ''),
                'interval_sec': kw.get('interval_sec', 0), 'timezone': kw.get('timezone', 'UTC'),
                'execution_owner': kw['execution_owner'], 'offline_policy': kw.get('offline_policy', 'hub_online_required'),
                'misfire_policy': kw.get('misfire_policy', 'run_once'), 'enabled': kw.get('enabled', True)}
        if kw.get('run_at'):
            body['run_at'] = kw['run_at']
        code, sc = self.hub.post('/api/schedules', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('schedule_create_http_%s %s' % (code, sc))
        self.state['created'].append(('schedule', sc['id'], name))
        return sc

    def phase2_fixtures(self):
        node_a = self.state['nodes']['a']['id']
        fx = self.state['fixtures']
        fixtures_a = self.children['a'].fixtures
        s_marker = self._create_script(PREFIX + 'script-marker', BODY_MARKER % fixtures_a,
                                       'p-sched marker script (nonce %s)' % NONCE)
        s_fail = self._create_script(PREFIX + 'script-fail', BODY_FAIL % fixtures_a,
                                     'p-sched failing script (nonce %s)' % NONCE)
        s_bad = self._create_script(PREFIX + 'script-baduser', BODY_BADUSER,
                                    'p-sched pre-exec error script (nonce %s)' % NONCE,
                                    run_user='qa_psched_no_such_user_%s' % NONCE)
        fx['script_marker'] = s_marker['id']
        fx['script_fail'] = s_fail['id']
        fx['script_baduser'] = s_bad['id']

        t_agent_cron = self._create_task(PREFIX + 'task-agent-cron', node_a, script_id=s_marker['id'])
        t_hub_cron = self._create_task(PREFIX + 'task-hub-cron', node_a, script_id=s_marker['id'])
        t_ot_future = self._create_task(PREFIX + 'task-onetime-future', node_a, script_id=s_marker['id'])
        t_ot_skip = self._create_task(PREFIX + 'task-onetime-skip', node_a, script_id=s_marker['id'])
        t_ot_runonce = self._create_task(PREFIX + 'task-onetime-runonce', node_a, script_id=s_marker['id'])
        t_retry = self._create_task(PREFIX + 'task-retry', node_a, script_id=s_fail['id'], timeout=30, retry=2)
        t_baduser = self._create_task(PREFIX + 'task-baduser', node_a, script_id=s_bad['id'], timeout=30, retry=2)
        fx.update({'task_agent_cron': t_agent_cron['id'], 'task_hub_cron': t_hub_cron['id'],
                   'task_ot_future': t_ot_future['id'], 'task_ot_skip': t_ot_skip['id'],
                   'task_ot_runonce': t_ot_runonce['id'], 'task_retry': t_retry['id'],
                   'task_baduser': t_baduser['id']})

        d_pg_to = fixtures_a + '/' + MARKER_A + '-pg-timeout'
        d_pg_ca = fixtures_a + '/' + MARKER_A + '-pg-cancel'
        t_pg_to = self._create_task(PREFIX + 'task-pg-timeout', node_a,
                                    command=_pg_command(d_pg_to, 300), timeout=6)
        t_pg_ca = self._create_task(PREFIX + 'task-pg-cancel', node_a,
                                    command=_pg_command(d_pg_ca, 300), timeout=120)
        fx.update({'task_pg_timeout': t_pg_to['id'], 'task_pg_cancel': t_pg_ca['id'],
                   'pg_timeout_dir': d_pg_to, 'pg_cancel_dir': d_pg_ca})

        # schedules
        now = datetime.now(timezone.utc)
        run_at_future = now + timedelta(seconds=95)
        run_at_past = now - timedelta(seconds=600)
        sc_agent = self._create_schedule(PREFIX + 'sc-agent-cron', fx['task_agent_cron'],
                                         type='cron', expression='* * * * *', timezone='UTC',
                                         execution_owner='agent', misfire_policy='run_once')
        sc_hub = self._create_schedule(PREFIX + 'sc-hub-cron', fx['task_hub_cron'],
                                       type='cron', expression='* * * * *', timezone='UTC',
                                       execution_owner='hub', misfire_policy='run_once')
        sc_ot_future = self._create_schedule(PREFIX + 'sc-onetime-future', fx['task_ot_future'],
                                             type='one_time', run_at=iso(run_at_future), timezone='Asia/Shanghai',
                                             execution_owner='agent', misfire_policy='run_once')
        sc_ot_skip = self._create_schedule(PREFIX + 'sc-onetime-skip', fx['task_ot_skip'],
                                           type='one_time', run_at=iso(run_at_past), timezone='UTC',
                                           execution_owner='agent', misfire_policy='skip')
        sc_ot_runonce = self._create_schedule(PREFIX + 'sc-onetime-runonce', fx['task_ot_runonce'],
                                              type='one_time', run_at=iso(run_at_past), timezone='UTC',
                                              execution_owner='agent', misfire_policy='run_once')
        self.state['schedules'] = {
            'agent_cron': sc_agent['id'], 'hub_cron': sc_hub['id'],
            'ot_future': sc_ot_future['id'], 'ot_skip': sc_ot_skip['id'], 'ot_runonce': sc_ot_runonce['id']}
        self.state['fixtures']['ot_future_run_at_utc'] = iso(run_at_future)
        self.state['fixtures']['ot_past_run_at_utc'] = iso(run_at_past)

        local = self.wait_local_schedules('a', [sc_agent['id'], sc_hub['id'], sc_ot_future['id'],
                                                sc_ot_skip['id'], sc_ot_runonce['id']], timeout=90)
        out = {'at_utc': now_utc(), 'fixtures': dict(fx), 'schedules': dict(self.state['schedules']),
               'run_at_future_utc': iso(run_at_future), 'created_utc': iso(now),
               'child_local_schedules': local,
               'note': 'marker script appends one line per real process invocation to '
                       '<child_root>/fixtures/invocations.log (independent business-effect counter)'}
        self.ev('phase2-fixtures.json', out)
        return out

    # ---------------- phase 3: owner source + multi-slot ----------------
    def phase3_owner_multislot(self):
        f = self.state['fixtures']
        proxy = self.proxies['a']

        def enough():
            a = len(self.hub_execs(f['task_agent_cron']))
            h = len(self.hub_execs(f['task_hub_cron']))
            return (a, h) if (a >= 3 and h >= 2) else None

        got = wait_for(enough, timeout=300, interval=5.0, label='cron_slots')
        time.sleep(3)  # let any just-dispatched RUN_EXECUTION frame land in the proxy ledger
        a_execs = self.hub_execs(f['task_agent_cron'])
        h_execs = self.hub_execs(f['task_hub_cron'])
        run_frames = proxy.frames_of('RUN_EXECUTION')
        fin_frames = proxy.frames_of('EXECUTION_FINISHED')

        def slot_report(execs):
            groups = {}
            for e in execs:
                m = minute_of(e.get('scheduled_time'))
                groups.setdefault(m, []).append(e)
            rows = []
            for m in sorted(k for k in groups if k):
                g = groups[m]
                start = min((e.get('start_time') for e in g if e.get('start_time')), default=None)
                scheduled = g[0].get('scheduled_time')
                lat = None
                if start and scheduled:
                    lat = (parse_iso(start) - parse_iso(scheduled)).total_seconds()
                rows.append({'minute': m, 'count': len(g),
                             'execution_ids': [e['id'] for e in g],
                             'statuses': [e.get('status') for e in g],
                             'scheduled_time': scheduled, 'start_time': start,
                             'start_minus_slot_sec': lat})
            return rows

        a_rows = slot_report(a_execs)
        h_rows = slot_report(h_execs)
        a_run_frames = [fr for fr in run_frames if fr.get('task_id') == f['task_agent_cron']]
        h_run_frames = [fr for fr in run_frames if fr.get('task_id') == f['task_hub_cron']]
        a_local = self.local_execs('a', f['task_agent_cron'])
        h_local = self.local_execs('a', f['task_hub_cron'])
        h_exec_ids = {e['id'] for e in h_execs}
        h_frame_exec_ids = {fr.get('execution_id') for fr in h_run_frames}
        a_exec_ids = {e['id'] for e in a_execs}
        a_frame_exec_ids = {fr.get('execution_id') for fr in a_run_frames}

        out = {
            'at_utc': now_utc(),
            'wait_result': got,
            'agent_owned_cron': {
                'hub_executions': len(a_execs), 'slot_rows': a_rows,
                'distinct_minutes': len(a_rows),
                'no_duplicate_slots': all(r['count'] == 1 for r in a_rows),
                'all_success': all(e.get('status') == 'SUCCESS' for e in a_execs),
                'max_start_latency_sec': max((r['start_minus_slot_sec'] for r in a_rows
                                              if r['start_minus_slot_sec'] is not None), default=None),
                'agent_local_executions': len(a_local),
                'agent_local_trigger_types': sorted({r['trigger_type'] for r in a_local}),
                'hub_dispatched_run_frames_for_task': len(a_run_frames),
                'hub_dispatched_run_frame_exec_ids': sorted(a_frame_exec_ids),
                'hub_exec_ids_minus_frame_exec_ids': sorted(a_exec_ids - a_frame_exec_ids),
            },
            'hub_owned_cron': {
                'hub_executions': len(h_execs), 'slot_rows': h_rows,
                'distinct_minutes': len(h_rows),
                'no_duplicate_slots': all(r['count'] == 1 for r in h_rows),
                'all_success': all(e.get('status') == 'SUCCESS' for e in h_execs),
                'hub_dispatched_run_frames_for_task': len(h_run_frames),
                'run_frame_exec_ids': sorted(h_frame_exec_ids),
                'hub_exec_ids_not_dispatched_by_hub': sorted(h_exec_ids - h_frame_exec_ids),
                'agent_local_executions': len(h_local),
            },
            'wire_frames': {'run_execution_total': len(run_frames),
                            'execution_finished_total': len(fin_frames),
                            'run_frames': run_frames},
            'assertions': {
                'agent_cron_no_hub_dispatch': len(a_run_frames) == 0,
                'hub_cron_all_dispatched_by_hub': len(h_execs) > 0 and (h_exec_ids <= h_frame_exec_ids),
                'agent_cron_slots_unique': all(r['count'] == 1 for r in a_rows),
                'hub_cron_slots_unique': all(r['count'] == 1 for r in h_rows),
                'agent_cron_multislot': len(a_rows) >= 3,
                'hub_cron_multislot': len(h_rows) >= 2,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase3-owner-multislot.json', out)
        return out

    # ---------------- phase 4: disable suppression ----------------
    def phase4_disable(self):
        f = self.state['fixtures']
        sc = self.state['schedules']['agent_cron']
        before = len(self.hub_execs(f['task_agent_cron']))
        code, body = self.hub.put('/api/schedules/' + self.hub.quote(sc), {'enabled': False})
        self.budget['api_mutations'] += 1
        readback = self.hub.get('/api/schedules/' + self.hub.quote(sc))
        local = self.child('a').db_query('SELECT id,enabled FROM schedules WHERE id=?', [sc])['rows']
        disable_instant = time.time()
        # wait across a full minute boundary plus margin
        time.sleep(80)
        after_execs = self.hub_execs(f['task_agent_cron'])
        # executions whose scheduled slot is after the disable instant
        post = [e for e in after_execs
                if parse_iso(e.get('scheduled_time')) and parse_iso(e['scheduled_time']).timestamp() > disable_instant]
        run_frames = [fr for fr in self.proxies['a'].frames_of('RUN_EXECUTION')
                      if fr.get('task_id') == f['task_agent_cron']]
        out = {
            'at_utc': now_utc(),
            'schedule_id': sc,
            'put_http': code, 'put_body': body,
            'readback': {'http': readback[0], 'enabled': (readback[1] or {}).get('enabled')},
            'child_local_schedule': local,
            'executions_before_disable': before,
            'executions_after_wait': len(after_execs),
            'executions_with_slot_after_disable_instant': len(post),
            'post_disable_slots': [e.get('scheduled_time') for e in post],
            'hub_dispatch_frames_for_disabled': len(run_frames),
            'waited_sec': time.time() - disable_instant,
            'assertions': {
                'api_readback_disabled': (readback[1] or {}).get('enabled') is False,
                'child_local_disabled': len(local) == 1 and local[0]['enabled'] == 0,
                'no_new_execution_after_disable': len(post) == 0,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase4-disable-suppression.json', out)
        return out

    # ---------------- phase 5: one_time + timezone + misfire ----------------
    def phase5_onetime(self):
        f = self.state['fixtures']
        fut = self.hub_execs(f['task_ot_future'])
        skip = self.hub_execs(f['task_ot_skip'])
        ro = self.hub_execs(f['task_ot_runonce'])
        run_at = parse_iso(f['ot_future_run_at_utc'])
        fired = None
        lat = None
        if fut:
            fired = fut[0].get('start_time')
            if fired:
                lat = (parse_iso(fired) - run_at).total_seconds()
        out = {
            'at_utc': now_utc(),
            'timezone_note': 'sc-onetime-future uses timezone Asia/Shanghai with a UTC run_at; '
                             'a timezone bug would shift the fire instant by 8h',
            'onetime_future': {
                'task_id': f['task_ot_future'], 'run_at_utc': f['ot_future_run_at_utc'],
                'executions': len(fut),
                'detail': [{'id': e['id'], 'status': e.get('status'), 'start_time': e.get('start_time'),
                            'scheduled_time': e.get('scheduled_time'), 'exit_code': e.get('exit_code')} for e in fut],
                'start_minus_run_at_sec': lat,
            },
            'onetime_past_skip': {
                'task_id': f['task_ot_skip'], 'run_at_utc': f['ot_past_run_at_utc'],
                'executions': len(skip),
                'detail': [{'id': e['id'], 'status': e.get('status')} for e in skip],
            },
            'onetime_past_runonce': {
                'task_id': f['task_ot_runonce'], 'run_at_utc': f['ot_past_run_at_utc'],
                'executions': len(ro),
                'detail': [{'id': e['id'], 'status': e.get('status'), 'scheduled_time': e.get('scheduled_time'),
                            'start_time': e.get('start_time')} for e in ro],
            },
            'child_local': {
                'future': self.local_execs('a', f['task_ot_future']),
                'skip': self.local_execs('a', f['task_ot_skip']),
                'runonce': self.local_execs('a', f['task_ot_runonce']),
            },
            'assertions': {
                'future_fired_once': len(fut) == 1 and fut[0].get('status') == 'SUCCESS',
                'future_punctual_within_5s': lat is not None and abs(lat) <= 5.0,
                'past_skip_no_execution': len(skip) == 0,
                'past_runonce_single_catchup': len(ro) == 1 and ro[0].get('status') == 'SUCCESS',
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase5-onetime-misfire.json', out)
        return out

    # ---------------- phase 6: native process-group cleanup ----------------
    def _pg_watch(self, label, d, dur):
        """Start an on-node liveness watcher for parent/child pid files; returns a handle."""
        script = (
            "import json,os,time\n"
            "d=%r; dur=%r\n" % (d, dur)
        ) + (
            "deadline=time.time()+dur\n"
            "state={}; deaths={}; pids={}; t0=time.time()\n"
            "while time.time()<deadline:\n"
            "    for nm in ('parent','child'):\n"
            "        pf=os.path.join(d,nm)\n"
            "        try: pid=int(open(pf).read().strip())\n"
            "        except Exception: pid=None\n"
            "        if pid: pids[nm]=pid\n"
            "        p=pids.get(nm)\n"
            "        if not p: continue\n"
            "        try: os.kill(p,0); alive=True\n"
            "        except ProcessLookupError: alive=False\n"
            "        except PermissionError: alive=True\n"
            "        prev=state.get(nm); state[nm]=alive\n"
            "        if prev and not alive and nm not in deaths:\n"
            "            deaths[nm]={'pid':p,'death_ts':time.time(),'since_start':time.time()-t0}\n"
            "    time.sleep(0.05)\n"
            "print(json.dumps({'t0':t0,'pids':pids,'deaths':deaths,'final_state':state}))\n"
        )
        holder = {}

        def worker():
            rc, out, err = self.child(label)._ssh(script, timeout=dur + 40)
            try:
                holder['result'] = json.loads([l for l in out.splitlines() if l.strip()][-1])
            except Exception:
                holder['result'] = {'error': err[-300:] or out[-300:], 'rc': rc}
        th = threading.Thread(target=worker, daemon=True, name='pg-watch-' + label)
        th.start()
        return {'thread': th, 'holder': holder}

    def _pg_case(self, label, name, task_id, mode, d, timeout_expected):
        # clean any stale dir
        self.child(label).kill_marker_processes(MARKER_A if label == 'a' else MARKER_B)
        self.child(label)._ssh('import shutil,os\nshutil.rmtree(%r, ignore_errors=True)\nprint("CLEAN")\n' % d)
        dur = 40 if mode == 'timeout' else 30
        watch = self._pg_watch(label, d, dur)
        time.sleep(1.0)
        t_run = time.time()
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id),
                                   {'node_ids': {self.state['nodes'][label]['id']: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        if code != 200 or not rows:
            raise RuntimeError('pg_run_%s_http_%s %s' % (name, code, rows))
        eid = rows[0]['id']
        self.state['executions'][name] = eid
        # wait for the parent/child pid files to appear
        def pids_ready():
            probe = self.child(label).process_probe({'parent': d + '/parent', 'child': d + '/child'},
                                                    MARKER_A if label == 'a' else MARKER_B)
            p = probe['pids']
            return probe if (p['parent']['pid'] and p['child']['pid'] and
                             p['parent']['alive'] and p['child']['alive']) else None
        ready = wait_for(pids_ready, timeout=20, interval=0.5, label='pg_pids')
        if mode == 'cancel':
            time.sleep(3.0)
            ccode, cbody = self.hub.post('/api/executions/%s/cancel' % self.hub.quote(eid), {})
            self.budget['api_mutations'] += 1
        else:
            ccode, cbody = None, None

        def terminal():
            code2, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
            return body if (code2 == 200 and (body or {}).get('status') in TERMINAL) else None
        final = wait_for(terminal, timeout=90, interval=1.0, label='pg_terminal') or {}
        watch['thread'].join(timeout=dur + 30)
        w = watch['holder'].get('result', {})
        deaths = w.get('deaths', {})
        grace = None
        if deaths.get('parent') and deaths.get('child'):
            grace = deaths['child']['death_ts'] - deaths['parent']['death_ts']
        post = self.child(label).process_probe({'parent': d + '/parent', 'child': d + '/child'},
                                               MARKER_A if label == 'a' else MARKER_B)
        out = {
            'at_utc': now_utc(), 'mode': mode, 'name': name, 'deployment_mode': self.children[label].deployment_mode,
            'task_id': task_id, 'execution_id': eid, 'run_http': code, 'cancel_http': ccode,
            'terminal_status': final.get('status'), 'exit_code': final.get('exit_code'),
            'pids_seen_while_running': (ready or {}).get('pids') if isinstance(ready, dict) else None,
            'watcher': w,
            'parent_then_child_death_gap_sec': grace,
            'post_probe': post,
            'expected_status': timeout_expected,
            'assertions': {
                'terminal_status_expected': final.get('status') == timeout_expected,
                'parent_dead_after': deaths.get('parent') is not None and not post['pids']['parent']['alive'],
                'child_dead_after': deaths.get('child') is not None and not post['pids']['child']['alive'],
                'no_marker_leftovers': len(post['marker_processes']) == 0,
            },
        }
        if mode == 'timeout':
            out['assertions']['grace_gap_between_0_and_6s'] = grace is not None and 0 <= grace <= 6.0
        out['assertions_pass'] = all(out['assertions'].values())
        return out

    def phase6_native_pg(self):
        f = self.state['fixtures']
        to = self._pg_case('a', 'pg_timeout_native', f['task_pg_timeout'], 'timeout', f['pg_timeout_dir'], 'TIMED_OUT')
        ca = self._pg_case('a', 'pg_cancel_native', f['task_pg_cancel'], 'cancel', f['pg_cancel_dir'], 'CANCELED')
        out = {'at_utc': now_utc(), 'timeout_case': to, 'cancel_case': ca,
               'assertions_pass': to['assertions_pass'] and ca['assertions_pass']}
        self.ev('phase6-native-process-group.json', out)
        return out

    # ---------------- phase 7: retry per-attempt + interval observation ----------------
    def phase7_retry(self):
        f = self.state['fixtures']
        tid = f['task_retry']
        # fresh count baseline
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(tid),
                                   {'node_ids': {self.state['nodes']['a']['id']: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        if code != 200 or not rows:
            raise RuntimeError('retry_run_http_%s %s' % (code, rows))
        eid = rows[0]['id']
        self.state['executions']['retry'] = eid

        def terminal():
            c, b = self.hub.get('/api/executions/' + self.hub.quote(eid))
            return b if (c == 200 and (b or {}).get('status') in TERMINAL) else None
        final = wait_for(terminal, timeout=90, interval=0.5, label='retry_terminal') or {}
        attempts = self.child('a').read_file(self.children['a'].fixtures + '/retry-attempts.log')
        # execution count for this task/slot (must be exactly one logical run)
        execs = self.hub_execs(tid)
        audit = self.hub.json('/api/audit?limit=300')
        exec_audit = [a for a in audit if a.get('resource_id') == tid and a.get('action') == 'execute']
        stdout_lines = [l for l in (final.get('stdout') or '').splitlines() if ' attempt ' in l]
        out = {
            'at_utc': now_utc(), 'task_id': tid, 'execution_id': eid, 'run_http': code,
            'retry_configured': 2,
            'terminal_status': final.get('status'), 'exit_code': final.get('exit_code'),
            'logical_executions_for_task': len(execs),
            'execution_ids_for_task': [e['id'] for e in execs],
            'attempt_markers_in_stdout': len(stdout_lines),
            'attempt_file': attempts,
            'attempt_file_lines': attempts.get('line_count'),
            'attempt_timestamps': [l.split(' attempt ', 1)[1] for l in (attempts.get('lines') or []) if ' attempt ' in l],
            'per_attempt_audit_rows': len(exec_audit),
            'audit_execute_rows': [{'action': a.get('action'), 'resource': a.get('resource'),
                                    'resource_id': a.get('resource_id'), 'detail': a.get('detail'),
                                    'created_at': a.get('created_at')} for a in exec_audit[:5]],
            'note': 'retry loop has no configured backoff (agent/agent.go retries immediately); '
                    'observed attempt intervals are recorded as observations only - there is no normative threshold',
        }
        out['attempts_observed'] = len(attempts.get('lines') or [])
        out['assertions'] = {
            'final_failed': final.get('status') == 'FAILED' and final.get('exit_code') not in (0, None),
            'three_attempts': out['attempts_observed'] == 3,
            'single_logical_execution': len(execs) == 1,
            'no_per_attempt_audit': len(exec_audit) <= 1,
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase7-retry-attempts.json', out)
        return out

    # ---------------- phase 7b: retry error boundary (pre-exec failure) ----------------
    def phase7b_error_boundary(self):
        f = self.state['fixtures']
        tid = f['task_baduser']
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(tid),
                                   {'node_ids': {self.state['nodes']['a']['id']: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        if code != 200 or not rows:
            raise RuntimeError('baduser_run_http_%s %s' % (code, rows))
        eid = rows[0]['id']
        self.state['executions']['baduser'] = eid

        def terminal():
            c, b = self.hub.get('/api/executions/' + self.hub.quote(eid))
            return b if (c == 200 and (b or {}).get('status') in TERMINAL) else None
        final = wait_for(terminal, timeout=60, interval=0.5, label='baduser_terminal') or {}
        execs = self.hub_execs(tid)
        local = self.local_execs('a', tid)
        out = {
            'at_utc': now_utc(), 'task_id': tid, 'execution_id': eid, 'run_http': code,
            'retry_configured': 2,
            'terminal_status': final.get('status'), 'exit_code': final.get('exit_code'),
            'block_reason': final.get('block_reason'),
            'logical_executions_for_task': len(execs),
            'note': 'pre-exec failure (RunUser lookup fails in buildCmd) must NOT be retried; the retry loop '
                    'breaks on a runner error (lastErr != nil) rather than an exit-code failure',
        }
        out['assertions'] = {
            'final_failed': final.get('status') == 'FAILED',
            'error_reason_present': 'run user' in (final.get('block_reason') or '').lower(),
            'no_retry_on_pre_exec_error': len(execs) == 1,
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase7b-error-boundary.json', out)
        return out

    # ---------------- phase 8: docker/compose form assessment (no fixtures) ----------------
    def phase8_docker_form_assessment(self):
        """The product has no docker/compose *task execution* form.

        Docker/Compose appear only as the enrolled Agent's own deployment mode
        (snippets returned by the enrollment endpoint) and the task runner
        (internal/agent/execution/runner.go) is deployment-mode independent, so
        there is no distinct docker process-group cleanup to exercise. We record
        the environment fact and the source evidence and mark the dimension
        PARTIAL rather than fabricate a container run.
        """
        probe = (
            "import json,subprocess\n"
            "out={}\n"
            "out['docker_binary']=subprocess.run(['which','docker'],capture_output=True,text=True).stdout.strip()\n"
            "out['docker_active']=subprocess.run(['systemctl','is-active','docker'],capture_output=True,text=True).stdout.strip()\n"
            "out['docker_version']=subprocess.run(['docker','--version'],capture_output=True,text=True).stdout.strip()\n"
            "print(json.dumps(out))\n"
        )
        rc, out, err = self.children['a']._ssh(probe, timeout=40)
        env = json.loads([l for l in out.splitlines() if l.strip()][-1]) if rc == 0 and out.strip() else {'error': err[-300:]}
        out = {
            'at_utc': now_utc(),
            'native2_docker_environment': env,
            'source_findings': {
                'deployment_modes': ['native', 'docker', 'docker_host_integration'],
                'task_runner': 'internal/agent/execution/runner.go builds every command/script with '
                               'SysProcAttr{Setpgid:true} and kills -pgid (SIGTERM, 2s, SIGKILL) regardless of '
                               'deployment mode; there is no docker/compose-specific execution branch.',
                'enrollment_snippet_only': 'internal/hub/api/api.go returns docker_run/docker_compose snippets for '
                                           'enrolling the Agent itself (agent deployment), not for executing tasks.',
                'app_deploy': 'internal/agent/application.go deploys via systemd units through the host adapter, '
                              'not docker compose.',
            },
            'conclusion': 'No docker/compose *execution* form exists in the product; the process-group cleanup '
                          'code path is shared and was exercised on the native owned child (phase6). The '
                          'Docker/Compose sub-dimension is therefore PARTIAL.',
            'assertions_pass': False,
            'dimension_status': 'PARTIAL',
        }
        self.ev('phase8-docker-compose-assessment.json', out)
        return out

    # ---------------- phase 9: recovery / idempotency / persisted desired state ----------------
    def phase9_recovery(self):
        f = self.state['fixtures']
        node_a = self.state['nodes']['a']['id']
        before = {
            'ot_future': len(self.hub_execs(f['task_ot_future'])),
            'ot_runonce': len(self.hub_execs(f['task_ot_runonce'])),
            'ot_skip': len(self.hub_execs(f['task_ot_skip'])),
            'pg_timeout_status': (self.hub.get('/api/executions/' + self.hub.quote(self.state['executions']['pg_timeout_native']))[1] or {}).get('status'),
            'pg_cancel_status': (self.hub.get('/api/executions/' + self.hub.quote(self.state['executions']['pg_cancel_native']))[1] or {}).get('status'),
        }
        stopped = self.child('a').stop()
        self.budget['child_process_stops'] += 1
        time.sleep(2.0)
        started = self.child('a').start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(node_a, timeout=90)
        # allow the local scheduler to re-evaluate every one-time schedule at least 2 ticks
        time.sleep(8)
        after = {
            'ot_future': len(self.hub_execs(f['task_ot_future'])),
            'ot_runonce': len(self.hub_execs(f['task_ot_runonce'])),
            'ot_skip': len(self.hub_execs(f['task_ot_skip'])),
        }
        # local persisted executions survive the restart
        local_pg = self.child('a').db_query(
            'SELECT id,task_id,status,scheduled_time FROM executions WHERE task_id IN (?,?) ORDER BY scheduled_time',
            [f['task_pg_timeout'], f['task_pg_cancel']])['rows']
        local_ot = self.child('a').db_query(
            'SELECT id,task_id,status,scheduled_time FROM executions WHERE task_id IN (?,?,?) ORDER BY scheduled_time',
            [f['task_ot_future'], f['task_ot_skip'], f['task_ot_runonce']])['rows']
        hub_pg_final = {
            'pg_timeout': (self.hub.get('/api/executions/' + self.hub.quote(self.state['executions']['pg_timeout_native']))[1] or {}).get('status'),
            'pg_cancel': (self.hub.get('/api/executions/' + self.hub.quote(self.state['executions']['pg_cancel_native']))[1] or {}).get('status'),
        }
        out = {
            'at_utc': now_utc(),
            'restart': {'stop': stopped, 'start': started,
                        'node_after': {k: (node or {}).get(k) for k in ('status', 'sync_status', 'global_revision')}},
            'before_restart_counts': before, 'after_restart_counts': after,
            'hub_pg_status_after_restart': hub_pg_final,
            'local_pg_executions': local_pg, 'local_onetime_executions': local_ot,
            'assertions': {
                'no_duplicate_onetime_after_restart': (before['ot_future'] == after['ot_future']
                                                       and before['ot_runonce'] == after['ot_runonce']
                                                       and before['ot_skip'] == after['ot_skip']),
                'timeout_status_persisted': hub_pg_final['pg_timeout'] == before['pg_timeout_status'] == 'TIMED_OUT',
                'cancel_status_persisted': hub_pg_final['pg_cancel'] == before['pg_cancel_status'] == 'CANCELED',
                'local_pg_persisted': len(local_pg) == 2,
                'child_resynced': (node or {}).get('status') == 'online' and (node or {}).get('sync_status') == 'synced',
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase9-recovery-idempotency.json', out)
        return out

    # ---------------- phase 10: cleanup ----------------
    def phase10_cleanup(self):
        results = {'deleted': [], 'errors': []}
        order = [('schedule', self.state['schedules'].get(k)) for k in
                 ('agent_cron', 'hub_cron', 'ot_future', 'ot_skip', 'ot_runonce')]
        order += [('task', self.state['fixtures'].get(k)) for k in
                  ('task_agent_cron', 'task_hub_cron', 'task_ot_future', 'task_ot_skip', 'task_ot_runonce',
                   'task_retry', 'task_baduser', 'task_pg_timeout', 'task_pg_cancel')]
        order += [('script', self.state['fixtures'].get(k)) for k in ('script_marker', 'script_fail', 'script_baduser')]
        for kind, obj_id in order:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, _ = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append((kind, obj_id))
        # stop the owned child processes + remove their roots BEFORE deleting the
        # hub node rows, so a live child cannot re-register the node afterwards
        for label in ('a', 'b'):
            ch = self.child(label)
            if ch is None:
                continue
            try:
                ch.kill_marker_processes(MARKER_A if label == 'a' else MARKER_B)
            except Exception as exc:
                results['errors'].append('kill_marker_%s:%s' % (label, exc))
            try:
                rm = ch.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                rm = {'error': str(exc)}
                results['errors'].append('child_remove_%s:%s' % (label, exc))
            results['child_removal_%s' % label] = rm
        for label, proxy in self.proxies.items():
            try:
                proxy.stop()
            except Exception as exc:
                results['errors'].append('proxy_stop_%s:%s' % (label, exc))
        # now delete both owned nodes (agent + node rows)
        for label in ('a', 'b'):
            nid = self.state['nodes'].get(label, {}).get('id')
            if not nid:
                continue
            code, body = self.hub.delete('/api/nodes/' + self.hub.quote(nid))
            gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(nid))
            results.setdefault('nodes', []).append({'label': label, 'id': nid, 'delete_http': code,
                                                    'get_http': gcode, 'delete_body': body})
            self.state['delete_results'][nid] = {'kind': 'node', 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append(('node', nid))
        results['all_get_404'] = all(v['get_http'] == 404 for v in self.state['delete_results'].values())
        self.ev('phase10-cleanup.json', results)
        return results

    # ---------------- phase 11: oracle ----------------
    def phase11_oracle(self, cleanup):
        nodes = self.nodes_by_id()
        protected_after, ok = {}, True
        for nid, label in PROTECTED_NODES.items():
            n = nodes.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'), 'sync_status': n.get('sync_status')}
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
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '', label='a').inventory()
        inv_b = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '', label='b').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']
                      or inv_b['root_exists'] or inv_b['config_exists'] or inv_b['owned_child_processes']
                      or inv['any_psched_processes'] or inv_b['any_psched_processes'])
        protected_units_ok = (inv['primary_agent_unit'].get('ActiveState') == 'active'
                              and inv['repair_agent_unit'].get('ActiveState') == 'active')
        preserved = []
        for key, eid in self.state['executions'].items():
            code, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
            preserved.append({'purpose': key, 'id': eid, 'http': code, 'status': (body or {}).get('status')})
        oracle = {
            'at_utc': now_utc(), 'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
            'native2_owned_child_orphan': orphan,
            'native2_inventory_a': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                    'owned_child_processes': inv['owned_child_processes'],
                                    'any_psched_processes': inv['any_psched_processes']},
            'native2_inventory_b': {'root_exists': inv_b['root_exists'], 'config_exists': inv_b['config_exists'],
                                    'owned_child_processes': inv_b['owned_child_processes'],
                                    'any_psched_processes': inv_b['any_psched_processes']},
            'protected_units_active': protected_units_ok,
            'proxy_states_final': {k: v.state() for k, v in self.proxies.items()},
            'cleanup_results': cleanup,
            'preserved_execution_history': preserved,
            'total_nodes_now': len(nodes),
            'hub_snapshot_final': hubdb.snapshot([]),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and protected_units_ok
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def _pg_command(d, sleep_sec):
    """Group leader (the outer sh) keeps the default TERM disposition and dies on
    SIGTERM; a background descendant ignores TERM and loops forever, so it can
    only be reaped by the SIGKILL that follows the grace period. The gap between
    the two deaths therefore measures the grace period."""
    inner = "trap '' TERM; while :; do sleep 1; done"
    return ("mkdir -p %s; rm -f %s/parent %s/child; echo $$ > %s/parent; "
            "sh -c %s & echo $! > %s/child; wait") % (d, d, d, d, shlex.quote(inner), d)


def _pg_command_b(d, sleep_sec):
    inner = "trap '' TERM; sleep %d & echo $! > %s/child; echo $$ > %s/parent; wait" % (sleep_sec, d, d)
    return "mkdir -p %s; rm -f %s/parent %s/child; sh -c %s" % (d, d, d, shlex.quote(inner.replace(MARKER_A, MARKER_B)))


def _hub_ssh():
    import subprocess
    from pathlib import Path as _P
    return ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
            '-i', str(_P.home() / '.ssh' / 'id_ed25519'), 'root@192.168.100.209']


def main():
    import faulthandler
    faulthandler.enable()
    run = Run()
    evidence = {'batch': 'p-sched', 'nonce': NONCE, 'prefix': PREFIX,
                'endpoint': 'http://192.168.100.209:18080', 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'p-sched', 'nonce': NONCE, 'phase': 'start', 'updated_at_utc': now_utc()}
    failed = False

    def mark(phase):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        hubapi.write_json('progress.json', progress)

    try:
        mark('guard'); evidence['phases']['guard'] = run.phase0_guard()
        mark('child-a'); evidence['phases']['child_a'] = run.phase1_child('a', 'native')
        mark('fixtures'); evidence['phases']['fixtures'] = run.phase2_fixtures()
        mark('owner-multislot'); evidence['phases']['owner_multislot'] = run.phase3_owner_multislot()
        mark('disable'); evidence['phases']['disable'] = run.phase4_disable()
        mark('onetime'); evidence['phases']['onetime'] = run.phase5_onetime()
        mark('native-pg'); evidence['phases']['native_pg'] = run.phase6_native_pg()
        mark('retry'); evidence['phases']['retry'] = run.phase7_retry()
        mark('error-boundary'); evidence['phases']['error_boundary'] = run.phase7b_error_boundary()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True

    # docker/compose form assessment (no fixtures, no second child)
    if not failed:
        mark('docker-assessment')
        try:
            evidence['phases']['docker'] = run.phase8_docker_form_assessment()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            evidence['deviations'].append('DOCKER_ASSESSMENT_ERROR:%s' % exc)
            evidence['phases']['docker'] = {'error': str(exc), 'dimension_status': 'PARTIAL'}

    if not failed:
        mark('recovery')
        try:
            evidence['phases']['recovery'] = run.phase9_recovery()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            evidence['deviations'].append('RECOVERY_PHASE_ERROR:%s' % exc)

    mark('frames')
    try:
        evidence['phases']['frames'] = {k: {'state': v.state(), 'frames': v.frames}
                                        for k, v in run.proxies.items()}
    except Exception:
        pass

    mark('cleanup')
    try:
        cleanup = run.phase10_cleanup()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        cleanup = {'error': str(exc), 'all_get_404': False}
        evidence['deviations'].append('CLEANUP_ERROR:%s' % exc)
        failed = True
    evidence['phases']['cleanup'] = cleanup

    mark('oracle')
    try:
        evidence['phases']['oracle'] = run.phase11_oracle(cleanup)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('ORACLE_ERROR:%s' % exc)
        failed = True

    evidence['state'] = run.state
    evidence['budget'] = run.budget
    evidence['settings'] = run.settings_snapshot
    evidence['failed'] = failed
    run.rec('run-evidence.json', evidence)
    progress['phase'] = 'done'
    progress['updated_at_utc'] = now_utc()
    progress['deviations'] = evidence['deviations']
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'failed': failed, 'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
