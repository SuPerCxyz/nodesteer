#!/usr/bin/env python3
"""qa-resume-20261016-g23 runtime batch: merged G2 (protocol frame / disconnect
injection) + G3 (scheduling stop / offline / compensation window), 6 cases.

Cases: P11-08, AR-08, P17P18-03 (G2), P12P13-06, P12P13-07, P12P13-08 (G3).

Real evidence chain (no product source change, no protected Agent/Hub touched,
no Hub restart, no VM restart, node native-2 used only through an owned child):
  operator/API act -> Hub commit/dispatch -> real frame on the wire (owned
  injectable frame proxy) -> owned child Agent state change -> child SQLite /
  Hub API readback -> on-node marker file (independent business-effect counter).

Injected faults (duplicate / lost / reordered / delayed frames, channel cut)
are applied only to the harness-owned child's own websocket channel.
"""
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import child_agent
import hubapi
import hubdb
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
BATCH = 'g23'
PREFIX = 'qa-%s-%s-' % (BATCH, NONCE)
DEV = HERE / 'evidence'
HUB_HOST = '192.168.100.209'
ENDPOINT = 'http://%s:18080' % HUB_HOST
NODE_HOST = '192.168.100.206'
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
HUB_SHA = 'cd8b7f9d7e40841bbba982c6838b2f74ccfdfac785674030c5bf7d6f1f69120d'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
ALL_STATUSES = ['PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED']

DEV.mkdir(exist_ok=True)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    if not s:
        return None
    t = s.replace('Z', '+00:00')
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


def ts_of(s):
    dt = parse_iso(s)
    return dt.timestamp() if dt else None


def wait_for(probe, predicate=None, timeout=60, interval=0.5, label='condition'):
    t0 = time.time()
    samples = []
    last = None
    while time.time() - t0 < timeout:
        last = probe()
        ok = predicate(last) if predicate is not None else bool(last)
        samples.append({'at_utc': now_utc(), 'value': last, 'ok': ok})
        if ok:
            return {'complete': True, 'seconds': round(time.time() - t0, 3), 'label': label,
                    'last': last, 'samples': samples}
        time.sleep(interval)
    return {'complete': False, 'seconds': round(time.time() - t0, 3), 'label': label,
            'last': last, 'samples': samples}


def marker_body(fx_dir, tag):
    return ('#!/bin/sh\n'
            'set -eu\n'
            'd="%s"\n'
            'mkdir -p "$d"\n'
            'ts=$(date -u +%%Y-%%m-%%dT%%H:%%M:%%S.%%NZ)\n'
            'printf \'%s %%s\\n\' "$ts" >> "$d/%s.log"\n'
            'printf \'%s %%s\\n\' "$ts"\n') % (fx_dir, tag, tag, tag)


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxies = {}
        self.children = {}
        self.state = {'started_utc': now_utc(), 'endpoint': ENDPOINT, 'node_host': NODE_HOST,
                      'fixtures': {}, 'schedules': {}, 'nodes': {}, 'created': [], 'deleted': [],
                      'delete_results': {}, 'executions': {}, 'injected_evidence': {}}
        self.budget = {'api_mutations': 0, 'child_process_starts': 0, 'child_process_stops': 0,
                       'frames_dropped': 0, 'frames_duplicated': 0, 'frames_held': 0, 'frames_replayed': 0}

    # ---------------- plumbing ----------------
    def rec(self, name, value):
        hubapi.write_json(name, value)

    def ev(self, name, value):
        hubapi.write_json(str(DEV / name), value)

    def child(self, label='a'):
        return self.children.get(label)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def execs_by_task(self, task_id, limit=500):
        rows = self.hub.json('/api/executions?task_id=%s&limit=%d' % (self.hub.quote(task_id), limit))
        return sorted(rows, key=lambda r: (r.get('scheduled_time') or '', r.get('start_time') or ''))

    def exec_by_status(self, status, task_id=None, limit=50):
        path = '/api/executions?limit=%d&offset=0&status=%s' % (limit, status)
        if task_id:
            path += '&task_id=' + self.hub.quote(task_id)
        code, body = self.hub.get(path)
        return {'query': path, 'http': code, 'rows': body if isinstance(body, list) else body,
                'count': len(body) if isinstance(body, list) else None}

    def hub_exec(self, eid):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
        return code, body

    def local_execs(self, label, task_id):
        return self.child(label).db_query(
            'SELECT id,task_id,script_id,trigger_type,scheduled_time,status,exit_code,offline,'
            'synced,start_time,end_time FROM executions WHERE task_id=? ORDER BY start_time',
            [task_id])['rows']

    def local_sync_state(self, label):
        return self.child(label).db_query('SELECT global_revision,last_sync FROM sync_state WHERE id=1')['rows']

    def local_schedule_rows(self, label, ids):
        ph = ','.join(['?'] * len(ids))
        return self.child(label).db_query(
            'SELECT id,revision,enabled FROM schedules WHERE id IN (%s) ORDER BY id' % ph, ids)['rows']

    def local_def_rows(self, label):
        return {
            'scripts': self.child(label).db_query('SELECT id,revision,sha256,enabled FROM scripts ORDER BY id')['rows'],
            'tasks': self.child(label).db_query('SELECT id,revision,enabled FROM tasks ORDER BY id')['rows'],
            'schedules': self.child(label).db_query('SELECT id,revision,enabled FROM schedules ORDER BY id')['rows'],
        }

    def marker_lines(self, label, tag):
        return self.child(label).read_file(self.child(label).fixtures + '/%s.log' % tag)

    def wait_node(self, node_id, status='online', sync='synced', timeout=120):
        return wait_for(
            lambda: (lambda n: {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                'global_revision': n.get('global_revision')})(self.node(node_id) or {}),
            lambda v: v.get('status') == status and v.get('sync_status') == sync,
            timeout=timeout, interval=1.0, label='node_state')

    def wait_local_schedules(self, label, ids, timeout=90):
        return wait_for(lambda: (lambda rows: rows if len(rows) == len(ids) else None)(
            self.local_schedule_rows(label, ids)), timeout=timeout, interval=1.0, label='local_schedules')

    def wait_exec_terminal(self, eid, timeout=120):
        return wait_for(lambda: (lambda cb: {'http': cb[0], 'status': (cb[1] or {}).get('status')})(
            self.hub_exec(eid)), lambda v: v.get('status') in TERMINAL, timeout=timeout,
            interval=0.7, label='exec_terminal')

    def clock_offset(self, samples=5):
        """Estimate (child_clock - workstation_clock) in seconds over SSH round trips.

        Execution rows are stamped on the child, so every comparison between a
        child timestamp and a harness timestamp must be converted into the
        child's clock frame first."""
        offs, rtts = [], []
        for _ in range(samples):
            t0 = time.time()
            res = child_agent.ssh_json("import json,time\nprint(json.dumps({'t':time.time()}))\n", timeout=30)
            t1 = time.time()
            offs.append(res['t'] - (t0 + t1) / 2.0)
            rtts.append(t1 - t0)
        return {'offset_sec_median': statistics.median(offs), 'samples': [round(o, 4) for o in offs],
                'rtt_sec_median': statistics.median(rtts)}

    def slot_groups(self, rows, key='scheduled_time'):
        groups = {}
        for r in rows:
            groups.setdefault((r.get(key) or '').strip(), []).append(r)
        return groups

    def unique_slots(self, rows, ignore_empty=True, key='scheduled_time'):
        groups = self.slot_groups(rows, key)
        bad = [k for k, v in groups.items() if len(v) > 1 and (k or not ignore_empty)]
        return {'slots': {k: len(v) for k, v in sorted(groups.items())}, 'duplicate_slots': bad,
                'unique': not bad}

    # ---------------- phase 0 ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': ENDPOINT, 'hub_sha_expected': HUB_SHA}
        out['healthz'] = self.hub.raw('GET', '/healthz', None, timeout=8)[0]
        out['readyz'] = self.hub.raw('GET', '/readyz', None, timeout=8)[0]
        if out['healthz'] != 200 or out['readyz'] != 200:
            raise RuntimeError('repair_hub_not_healthy')
        # hub unit + running binary sha (read-only, over ssh)
        probe = (
            "import json,subprocess,hashlib\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID,ExecMainStartTimestamp',%r],capture_output=True,text=True).stdout\n"
            "d=dict(x.split('=',1) for x in u.splitlines() if '=' in x)\n"
            "pid=d.get('MainPID','0')\n"
            "exe=subprocess.run(['readlink','-f','/proc/'+pid+'/exe'],capture_output=True,text=True).stdout.strip()\n"
            "sha=hashlib.sha256(open(exe,'rb').read()).hexdigest() if exe else ''\n"
            "print(json.dumps({'unit':d,'exe':exe,'sha256':sha}))\n"
        ) % HUB_UNIT
        import subprocess as _sp
        cp = _sp.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                      '-i', str(Path.home() / '.ssh' / 'id_ed25519'), 'root@' + HUB_HOST, 'python3', '-'],
                     input=probe, capture_output=True, text=True, timeout=60)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError('hub_probe_failed:%s' % (cp.stderr[-300:] or 'no output'))
        out['hub_unit'] = json.loads(lines[-1])
        out['hub_binary_sha_matches'] = out['hub_unit']['sha256'] == HUB_SHA
        if not out['hub_binary_sha_matches']:
            raise RuntimeError('hub_binary_sha_mismatch')
        # primary hub is read-only observed
        try:
            import urllib.request
            with urllib.request.urlopen('http://%s:8080/healthz' % HUB_HOST, timeout=8) as resp:
                out['primary_8080_healthz'] = resp.status
        except Exception as exc:
            out['primary_8080_healthz'] = 'error:%s' % type(exc).__name__
        nodes = self.nodes_by_id()
        out['nodes_before'] = {n['id']: {k: n.get(k) for k in ('hostname', 'status', 'sync_status', 'global_revision')}
                               for n in nodes.values()}
        # the repair authority hosts exactly 4 protected native repair Agents; every
        # other non-owned node (other batches / historical) is recorded but only the
        # 4 protected ones gate this batch.
        protected_prefix = 'qa-repair-20260930-101945-native-'
        out['protected_nodes'] = {}
        ok = True
        for nid, n in nodes.items():
            host = n.get('hostname') or nid
            if host.startswith(PREFIX):
                continue
            protected = host.startswith(protected_prefix)
            healthy = n.get('status') == 'online' and n.get('sync_status') == 'synced'
            out['protected_nodes'][host] = {
                'id': nid, 'status': n.get('status'), 'sync_status': n.get('sync_status'),
                'global_revision': n.get('global_revision'), 'healthy': healthy, 'protected_repair_native': protected}
            if protected:
                ok = ok and healthy
        out['protected_nodes_online_synced'] = ok
        out['protected_node_count'] = sum(1 for v in out['protected_nodes'].values() if v['protected_repair_native'])
        if not ok or out['protected_node_count'] != 4:
            raise RuntimeError('protected_nodes_not_healthy')
        leftovers = {
            'scripts': [s['id'] for s in self.hub.json('/api/scripts') if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in self.hub.json('/api/tasks') if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in self.hub.json('/api/schedules') if (s.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        out['owned_prefix_leftovers'] = leftovers
        if any(leftovers.values()):
            raise RuntimeError('owned_fixture_already_present')
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '', label='a').inventory()
        out['native2_pre'] = {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                              'owned_child_processes': inv['owned_child_processes'],
                              'any_g23_processes': inv['any_g23_processes'],
                              'owned_root_glob': inv['owned_root_glob'],
                              'agent_binary_sha256': inv['agent_binary_sha256'],
                              'primary_agent_unit': inv['primary_agent_unit'],
                              'repair_agent_unit': inv['repair_agent_unit']}
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'] or inv['owned_root_glob']:
            raise RuntimeError('owned_child_residue_present')
        if inv['primary_agent_unit'].get('ActiveState') != 'active' or inv['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_units_not_active')
        self.state['agent_binary_sha256'] = inv['agent_binary_sha256']
        db = hubdb.snapshot([])
        out['hub_db'] = {'revision_sequence': db['revision_sequence'], 'quick_check': db['quick_check'],
                         'revision_changes_total': db['revision_changes_total']}
        if db['quick_check'] != 'ok':
            raise RuntimeError('hub_db_quick_check_failed')
        self.state['hub_revision_before'] = db['revision_sequence']
        self.ev('phase0-guard.json', out)
        return out

    # ---------------- phase 1 ----------------
    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child-a', 'node_ip': NODE_HOST,
            'hub_address': ENDPOINT})
        self.budget['api_mutations'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s %s' % (code, enroll))
        node_id, agent_id = enroll['node_id'], enroll['agent_id']
        proxy = FrameProxy('ws://%s:18080' % HUB_HOST, label='a')
        port = proxy.start()
        self.proxies['a'] = proxy
        child = child_agent.OwnedChild(NONCE, port, node_id, agent_id, PREFIX + 'child-a',
                                       NODE_HOST, self.hub.cred['registration_token'],
                                       deployment_mode='native', label='a')
        self.children['a'] = child
        started = child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        res = self.wait_node(node_id, 'online', 'synced', timeout=120)
        if not res['complete']:
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        self.state['nodes']['a'] = {'id': node_id, 'agent_id': agent_id}
        n = self.node(node_id) or {}
        out = {'at_utc': now_utc(), 'enrollment': {'http': code, 'node_id': node_id, 'agent_id': agent_id,
                                                  'registration_token_persisted_to_evidence': False},
               'owned_child': {'root_on_native2': child.root, 'config_mode': '0600', 'data_dir': child.data,
                               'launcher': started.get('owned_processes'), 'proxy_listen_port': port,
                               'routing': 'owned child(%s) -> operator %s:%d (own injectable frame proxy) -> repair hub :18080'
                                          % (NODE_HOST, child_agent.OPERATOR_IP, port)},
               'node_after_start': {k: n.get(k) for k in ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                                          'global_revision', 'deployment_mode', 'agent_version')},
               'connect_wait_seconds': res['seconds']}
        self.ev('phase1-owned-child.json', out)
        return out

    # ---------------- phase 2: fixtures ----------------
    def _create_script(self, name, body, desc):
        payload = {'name': name, 'description': desc, 'interpreter': 'shell', 'content': body,
                   'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True}
        code, s = self.hub.post('/api/scripts', payload)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, s))
        self.state['created'].append(('script', s['id'], name))
        return s

    def _create_task(self, name, node_id, script_id):
        body = {'name': name, 'type': 'script',
                'target': {'type': 'node', 'node_ids': [node_id]},
                'parameters': [], 'timeout': 30, 'retry': 0,
                'offline_policy': 'hub_online_required', 'enabled': True, 'script_id': script_id}
        code, t = self.hub.post('/api/tasks', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, t))
        self.state['created'].append(('task', t['id'], name))
        return t

    def _create_schedule(self, name, task_id, **kw):
        body = {'task_id': task_id, 'type': kw['type'], 'expression': kw.get('expression', ''),
                'interval_sec': kw.get('interval_sec', 0), 'timezone': 'UTC',
                'execution_owner': kw['execution_owner'], 'offline_policy': kw['offline_policy'],
                'misfire_policy': kw['misfire_policy'], 'enabled': kw.get('enabled', False)}
        code, sc = self.hub.post('/api/schedules', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('schedule_create_http_%s %s' % (code, sc))
        self.state['created'].append(('schedule', sc['id'], name))
        return sc

    def phase2_fixtures(self):
        node_a = self.state['nodes']['a']['id']
        child = self.child('a')
        fx_dir = child.fixtures
        fx = self.state['fixtures']
        plan_scripts = [
            ('script_marker', 'marker', 'g23 duplicate-delivery marker (nonce %s)' % NONCE),
            ('script_pending', 'pending', 'g23 PENDING/RUNNING window marker (nonce %s)' % NONCE),
            ('script_notif', 'notif', 'g23 notification-fault marker (nonce %s)' % NONCE),
            ('script_owner_agent', 'owner-agent', 'g23 agent-owner marker (nonce %s)' % NONCE),
            ('script_owner_hub', 'owner-hub', 'g23 hub-owner marker (nonce %s)' % NONCE),
            ('script_off_allow', 'off-allow', 'g23 offline-policy allow marker (nonce %s)' % NONCE),
            ('script_off_req', 'off-req', 'g23 offline-policy hub-online marker (nonce %s)' % NONCE),
            ('script_mis_runonce', 'mis-runonce', 'g23 misfire run_once marker (nonce %s)' % NONCE),
            ('script_mis_skip', 'mis-skip', 'g23 misfire skip marker (nonce %s)' % NONCE),
        ]
        for key, tag, desc in plan_scripts:
            s = self._create_script(PREFIX + key.replace('_', '-'), marker_body(fx_dir, tag), desc)
            fx[key] = s['id']
            fx[key + '_tag'] = tag
        plan_tasks = {'task_dup_run': 'script_marker', 'task_pending': 'script_pending',
                      'task_notif': 'script_notif', 'task_owner_agent': 'script_owner_agent',
                      'task_owner_hub': 'script_owner_hub', 'task_off_allow': 'script_off_allow',
                      'task_off_req': 'script_off_req', 'task_mis_runonce': 'script_mis_runonce',
                      'task_mis_skip': 'script_mis_skip'}
        for key, script_key in plan_tasks.items():
            t = self._create_task(PREFIX + key.replace('_', '-'), node_a, fx[script_key])
            fx[key] = t['id']
        plan_schedules = [
            ('sc_notif_agent', 'task_notif', dict(type='interval', interval_sec=30, execution_owner='agent',
                                                  offline_policy='allow_offline', misfire_policy='run_once')),
            ('sc_owner_hub', 'task_owner_hub', dict(type='cron', expression='* * * * *', execution_owner='hub',
                                                    offline_policy='hub_online_required', misfire_policy='run_once')),
            ('sc_owner_agent', 'task_owner_agent', dict(type='cron', expression='* * * * *', execution_owner='agent',
                                                        offline_policy='allow_offline', misfire_policy='run_once')),
            ('sc_off_allow', 'task_off_allow', dict(type='interval', interval_sec=30, execution_owner='agent',
                                                    offline_policy='allow_offline', misfire_policy='run_once')),
            ('sc_off_req', 'task_off_req', dict(type='interval', interval_sec=30, execution_owner='agent',
                                                offline_policy='hub_online_required', misfire_policy='run_once')),
            ('sc_mis_runonce', 'task_mis_runonce', dict(type='interval', interval_sec=30, execution_owner='agent',
                                                        offline_policy='allow_offline', misfire_policy='run_once')),
            ('sc_mis_skip', 'task_mis_skip', dict(type='interval', interval_sec=30, execution_owner='agent',
                                                  offline_policy='allow_offline', misfire_policy='skip')),
        ]
        for key, task_key, kw in plan_schedules:
            sc = self._create_schedule(PREFIX + key.replace('sc_', 'sc-'), fx[task_key], **kw)
            self.state['schedules'][key] = sc['id']
        local = self.wait_local_schedules('a', list(self.state['schedules'].values()), timeout=90)
        out = {'at_utc': now_utc(), 'fixtures': dict(fx), 'schedules': dict(self.state['schedules']),
               'all_schedules_created_disabled': True,
               'child_local_schedules_after_create': local['last'],
               'marker_files_dir_on_child': fx_dir,
               'note': 'each marker script appends one line per real process invocation; that line count is the '
                       'independent business-effect oracle for exactly-once/at-least-once assertions'}
        self.ev('phase2-fixtures.json', out)
        return out

    def set_schedule_enabled(self, key, enabled):
        sc = self.state['schedules'][key]
        code, body = self.hub.put('/api/schedules/' + self.hub.quote(sc), {'enabled': enabled})
        self.budget['api_mutations'] += 1
        return code, body, sc

    # ---------------- phase 3: P11-08 ----------------
    def phase3_p11_08_duplicate_delivery(self):
        fx = self.state['fixtures']
        task = fx['task_dup_run']
        proxy = self.proxies['a']
        proxy.arm_duplicate('RUN_EXECUTION', 'hub_to_agent', 1, task_id=task, label='p11-08-dup-run-delivery')
        proxy.arm_duplicate('EXECUTION_FINISHED', 'agent_to_hub', 1, task_id=task, label='p11-08-dup-result-upload')
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task),
                                   {'node_ids': {self.state['nodes']['a']['id']: True}})
        self.budget['api_mutations'] += 1
        if code != 200 or not rows:
            raise RuntimeError('manual_run_http_%s %s' % (code, rows))
        eid = rows[0]['id']
        self.state['executions']['p11_08_run1'] = eid
        term = self.wait_exec_terminal(eid, timeout=90)
        time.sleep(4)  # let the duplicated upload + ack land in the ledger
        run_frames = proxy.frames_of('RUN_EXECUTION', 'hub_to_agent', task)
        fin_frames = proxy.frames_of('EXECUTION_FINISHED', 'agent_to_hub', task)
        ack_frames = proxy.frames_of('EXECUTION_ACK', 'hub_to_agent')
        dup_events = proxy.state()['duplicate_done']
        dup_run_events = [d for d in dup_events if d['ftype'] == 'RUN_EXECUTION' and d.get('task_id') == task]
        dup_fin_events = [d for d in dup_events if d['ftype'] == 'EXECUTION_FINISHED' and d.get('task_id') == task]
        ack_for_eid = [f for f in ack_frames if f.get('execution_id') == eid]
        hub_rows = self.execs_by_task(task)
        local_rows = self.local_execs('a', task)
        marker = self.marker_lines('a', fx['script_marker_tag'])
        # second, non-injected run: a new UUID really is a new intent
        code2, rows2 = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task),
                                     {'node_ids': {self.state['nodes']['a']['id']: True}})
        self.budget['api_mutations'] += 1
        eid2 = rows2[0]['id']
        self.state['executions']['p11_08_run2'] = eid2
        term2 = self.wait_exec_terminal(eid2, timeout=90)
        time.sleep(2)
        hub_rows2 = self.execs_by_task(task)
        local_rows2 = self.local_execs('a', task)
        marker2 = self.marker_lines('a', fx['script_marker_tag'])
        out = {
            'at_utc': now_utc(),
            'scenario': 'one Manual Run whose hub->agent RUN_EXECUTION frame and agent->hub EXECUTION_FINISHED frame '
                        'are each duplicated on the owned child channel (same Execution ID delivered twice / same '
                        'result uploaded twice), then a second untouched Manual Run',
            'injection': {'run_frames_received': [{'seq': f['seq'], 'execution_id': f.get('execution_id'),
                                                   'task_id': f.get('task_id'), 'trigger_type': f.get('trigger_type')}
                                                  for f in run_frames],
                          'finished_frames_received': [{'seq': f['seq'], 'execution_id': f.get('execution_id'),
                                                        'task_id': f.get('task_id'), 'status': f.get('status')}
                                                       for f in fin_frames],
                          'duplicate_injected_run_execution': dup_run_events,
                          'duplicate_injected_execution_finished': dup_fin_events,
                          'execution_ack_frames_for_this_execution': [{'seq': f['seq'], 'execution_id': f.get('execution_id'),
                                                                       'ok': f.get('ok'), 'error': f.get('error')}
                                                                      for f in ack_for_eid],
                          'note': 'the proxy ledger records frames it receives; a duplicate injection is recorded as an '
                                  'injection event and proven downstream (the duplicated RUN_EXECUTION did not create a second '
                                  'local execution; the duplicated EXECUTION_FINISHED made the Hub ack the same execution twice)'},
            'run1': {'execution_id': eid, 'terminal_wait': {'complete': term['complete'], 'seconds': term['seconds']},
                     'hub_row': (self.hub_exec(eid)[1] or {}),
                     'hub_rows_for_task': len(hub_rows), 'local_rows_for_task': len(local_rows),
                     'local_row': local_rows[0] if local_rows else None,
                     'run_frames_same_exec_id': sorted({f.get('execution_id') for f in run_frames}),
                     'finished_frames_same_exec_id': sorted({f.get('execution_id') for f in fin_frames}),
                     'ack_count_for_execution': len(ack_for_eid),
                     'child_agent_log_tail': self.child('a').read_file(self.child('a').log, tail=12),
                     'marker_lines': marker.get('line_count')},
            'run2': {'execution_id': eid2, 'terminal_wait': {'complete': term2['complete'], 'seconds': term2['seconds']},
                     'hub_rows_for_task': len(hub_rows2), 'local_rows_for_task': len(local_rows2),
                     'marker_lines': marker2.get('line_count')},
            'assertions': {
                'run_execution_duplicate_really_injected': len(dup_run_events) == 1
                and dup_run_events[0].get('execution_id') == eid,
                'execution_finished_duplicate_really_injected': len(dup_fin_events) == 1
                and dup_fin_events[0].get('execution_id') == eid,
                'hub_acked_duplicated_upload_twice': len(ack_for_eid) >= 2,
                'hub_received_one_run_execution_frame': len({f.get('execution_id') for f in run_frames}) == 1,
                'single_hub_execution_row_after_duplicates': len(hub_rows) == 1 and hub_rows[0]['id'] == eid,
                'hub_row_terminal_success': (self.hub_exec(eid)[1] or {}).get('status') == 'SUCCESS',
                'single_child_local_execution_row_after_duplicates': len(local_rows) == 1 and local_rows[0]['id'] == eid,
                'child_row_synced_once': local_rows and local_rows[0]['synced'] == 1,
                'single_business_invocation_for_run1': marker.get('line_count') == 1,
                'second_run_new_uuid_new_intent': eid2 != eid and len(hub_rows2) == 2
                and len({r['id'] for r in hub_rows2}) == 2 and len(local_rows2) == 2,
                'second_run_second_business_invocation': marker2.get('line_count') == 2,
                'no_duplicate_local_rows_after_second_run': len(local_rows2) == 2,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase3-p11-08-duplicate-delivery.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase3_p11_08_failed')
        return out

    # ---------------- phase 4: P17P18-03 ----------------
    def phase4_p17p18_03_pending_window(self):
        fx = self.state['fixtures']
        task = fx['task_pending']
        proxy = self.proxies['a']
        proxy.arm_hold('RUN_EXECUTION', 'hub_to_agent', 8, task_id=task, label='p17p18-pending-window')
        proxy.arm_hold('EXECUTION_FINISHED', 'agent_to_hub', 8, task_id=task, label='p17p18-running-window')
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task),
                                   {'node_ids': {self.state['nodes']['a']['id']: True}})
        self.budget['api_mutations'] += 1
        if code != 200 or not rows:
            raise RuntimeError('manual_run_http_%s %s' % (code, rows))
        eid = rows[0]['id']
        self.state['executions']['p17p18_pending'] = eid
        pending_samples, running_samples = [], []
        t0 = time.time()
        pending_hit = None
        while time.time() - t0 < 12:
            time.sleep(0.8)
            if pending_hit is None:
                q = self.exec_by_status('PENDING', task_id=task)
                hit = [r for r in (q['rows'] or []) if r.get('id') == eid]
                pending_samples.append({'at_utc': now_utc(), 'query': q['query'], 'http': q['http'],
                                        'rows_total': q['count'], 'matching_our_execution': len(hit) > 0,
                                        'row': hit[0] if hit else None})
                if hit:
                    pending_hit = hit[0]
            q2 = self.exec_by_status('RUNNING', task_id=task)
            hit2 = [r for r in (q2['rows'] or []) if r.get('id') == eid]
            running_samples.append({'at_utc': now_utc(), 'query': q2['query'], 'http': q2['http'],
                                    'rows_total': q2['count'], 'matching_our_execution': len(hit2) > 0,
                                    'row': hit2[0] if hit2 else None})
            if pending_hit and any(s['matching_our_execution'] for s in running_samples):
                break
        term = self.wait_exec_terminal(eid, timeout=90)
        time.sleep(2)
        final = self.hub_exec(eid)[1] or {}
        status_probe = {}
        for st in ALL_STATUSES:
            q = self.exec_by_status(st, task_id=task)
            status_probe[st] = {'http': q['http'], 'rows_total': q['count'],
                                'contains_our_execution': any((r or {}).get('id') == eid for r in (q['rows'] or []))}
        marker = self.marker_lines('a', fx['script_pending_tag'])
        out = {
            'at_utc': now_utc(),
            'scenario': 'delivery of the hub->agent RUN_EXECUTION frame is held for 8s by the owned frame proxy, so the '
                        'Hub execution row is genuinely PENDING; the agent->hub EXECUTION_FINISHED frame is held for 8s '
                        'so the row is genuinely RUNNING while the task already completed locally',
            'execution_id': eid, 'cli_attempts_used_for_pending_sampling': 1,
            'ui_equivalent_query': '/api/executions?limit=50&offset=0&status=PENDING&task_id=%s' % task,
            'pending_samples': pending_samples, 'running_samples': running_samples,
            'hold_events': [e for e in proxy.events if e['event'] in ('hold_armed', 'frame_held', 'frame_released')][-6:],
            'final_row': {k: final.get(k) for k in ('id', 'status', 'start_time', 'end_time', 'exit_code',
                                                    'scheduled_time', 'trigger_type', 'node_id')},
            'status_probe_all_eight': status_probe,
            'business_effect_marker_lines': marker.get('line_count'),
            'assertions': {
                'real_pending_row_sampled': pending_hit is not None and pending_hit.get('status') == 'PENDING',
                'real_running_row_sampled': any(s['matching_our_execution'] for s in running_samples),
                'final_terminal_success': final.get('status') == 'SUCCESS',
                'business_effect_once': marker.get('line_count') == 1,
                'all_eight_status_selectors_http_200': all(v['http'] == 200 for v in status_probe.values()),
                'pending_and_running_had_real_rows_during_window': pending_hit is not None
                and any(s['matching_our_execution'] for s in running_samples),
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase4-p17p18-03-pending-running.json', out)
        if not out['assertions_pass']:
            raise RuntimeError('phase4_p17p18_03_failed')
        return out

    # ---------------- phase 5: AR-08 ----------------
    def phase5_ar08_notification_faults(self):
        fx = self.state['fixtures']
        proxy = self.proxies['a']
        task = fx['task_notif']
        script = fx['script_notif']
        schedule = self.state['schedules']['sc_notif_agent']
        # keep one agent-owned schedule live across the fault window (idempotency under faults)
        self.set_schedule_enabled('sc_notif_agent', True)
        self.wait_local_schedules('a', [schedule], timeout=60)
        code, cur = self.hub.get('/api/scripts/' + self.hub.quote(script))
        v1 = cur
        hub_rev_before = hubdb.snapshot([script])['revision_sequence']
        child_pre = {
            'script_rows': self.child('a').db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [script])['rows'],
            'sync_state': self.local_sync_state('a'),
        }
        tag = fx['script_notif_tag']

        def put_content(version, body_extra):
            payload = {'name': v1['name'], 'description': v1['description'], 'interpreter': v1['interpreter'],
                       'content': marker_body(self.child('a').fixtures, tag) + body_extra,
                       'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True}
            c, s = self.hub.put('/api/scripts/' + self.hub.quote(script), payload)
            self.budget['api_mutations'] += 1
            return c, s

        def convergence_check(target_rev, target_sha, timeout=45):
            def probe():
                rows = self.child('a').db_query('SELECT revision,sha256 FROM scripts WHERE id=?', [script])['rows']
                if not rows:
                    return None
                return {'revision': rows[0]['revision'], 'sha256': rows[0]['sha256'],
                        'sync_rev': (self.local_sync_state('a') or [{}])[0].get('global_revision')}
            return wait_for(probe, lambda v: v.get('revision') == target_rev and v.get('sha256') == target_sha,
                            timeout=timeout, interval=1.0, label='child_convergence')

        # ---- A: lost notification
        proxy.arm_drop('CHANGE_NOTIFICATION', 'hub_to_agent', 1, object_id=script, label='ar08-lost-notification')
        codeA, scriptA = put_content('v2', '\nprintf \'v2\\n\'\n')
        if codeA != 200:
            raise RuntimeError('script_put_v2_http_%s' % codeA)
        time.sleep(2)
        dropsA = [d for d in proxy.state()['drop_done'] if d.get('object_id') == script]
        convA = convergence_check(scriptA['revision'], scriptA['sha256'], timeout=45)
        # ---- B: duplicated notification
        proxy.arm_duplicate('CHANGE_NOTIFICATION', 'hub_to_agent', 1, object_id=script, label='ar08-duplicate-notification')
        codeB, scriptB = put_content('v3', '\nprintf \'v3\\n\'\n')
        if codeB != 200:
            raise RuntimeError('script_put_v3_http_%s' % codeB)
        time.sleep(2)
        dupsB = [d for d in proxy.state()['duplicate_done'] if d.get('object_id') == script]
        convB = convergence_check(scriptB['revision'], scriptB['sha256'], timeout=45)
        # raw notification of the *older* revision captured on the wire (for the stale replay)
        raw_notifs = [f for f in proxy.raw_frames_of('CHANGE_NOTIFICATION', 'hub_to_agent')
                      if f.get('global_revision') is not None and f.get('raw')]
        stale = None
        for f in raw_notifs:
            payload = json.loads(f['raw']).get('payload') or {}
            if payload.get('object_id') == script and payload.get('object_revision') == scriptA.get('revision'):
                stale = f
                break
        if stale is None:
            for f in raw_notifs:
                payload = json.loads(f['raw']).get('payload') or {}
                if payload.get('object_id') == script:
                    stale = f
                    break
        # ---- C: reordered / stale notification replayed after the newer one was applied
        sync_before_c = len(proxy.sync_requests_since(0))
        rev_before_c = (self.local_sync_state('a') or [{}])[0].get('global_revision')
        script_before_c = self.child('a').db_query('SELECT revision,sha256 FROM scripts WHERE id=?', [script])['rows']
        replay_rec = proxy.replay(stale['seq']) if stale else None
        self.budget['frames_replayed'] += 1 if replay_rec else 0
        time.sleep(8)
        sync_after_c = len(proxy.sync_requests_since(0))
        rev_after_c = (self.local_sync_state('a') or [{}])[0].get('global_revision')
        script_after_c = self.child('a').db_query('SELECT revision,sha256 FROM scripts WHERE id=?', [script])['rows']
        hub_script_final = self.hub.get('/api/scripts/' + self.hub.quote(script))[1]
        # ---- D: desired state vs actual + schedule idempotency across the fault window
        local_defs = self.local_def_rows('a')
        hub_scripts = {s['id']: s for s in self.hub.json('/api/scripts')}
        hub_tasks = {t['id']: t for t in self.hub.json('/api/tasks')}
        hub_schedules = {s['id']: s for s in self.hub.json('/api/schedules')}
        owned_script_ids = sorted(v for k, v in fx.items()
                                  if k.startswith('script_') and not k.endswith('_tag') and isinstance(v, str))
        owned_task_ids = sorted(v for k, v in fx.items() if k.startswith('task_') and isinstance(v, str))
        owned_sched_ids = sorted(self.state['schedules'].values())
        desired_actual = {'scripts': [], 'tasks': [], 'schedules': []}
        for sid in owned_script_ids:
            h = hub_scripts.get(sid) or {}
            c = [r for r in local_defs['scripts'] if r['id'] == sid]
            desired_actual['scripts'].append({'id': sid, 'hub_revision': h.get('revision'),
                                              'child_revision': c[0]['revision'] if c else None,
                                              'hub_sha256': h.get('sha256'), 'child_sha256': c[0]['sha256'] if c else None})
        for tid in owned_task_ids:
            h = hub_tasks.get(tid) or {}
            c = [r for r in local_defs['tasks'] if r['id'] == tid]
            desired_actual['tasks'].append({'id': tid, 'hub_revision': h.get('revision'),
                                            'child_revision': c[0]['revision'] if c else None})
        for sid in owned_sched_ids:
            h = hub_schedules.get(sid) or {}
            c = [r for r in local_defs['schedules'] if r['id'] == sid]
            desired_actual['schedules'].append({'id': sid, 'hub_revision': h.get('revision'),
                                                'child_revision': c[0]['revision'] if c else None,
                                                'hub_enabled': h.get('enabled'),
                                                'child_enabled': bool(c[0]['enabled']) if c else None})
        desired_actual_ok = (all(x['hub_revision'] == x['child_revision'] and x['hub_sha256'] == x['child_sha256']
                                 for x in desired_actual['scripts'])
                             and all(x['hub_revision'] == x['child_revision'] for x in desired_actual['tasks'])
                             and all(x['hub_revision'] == x['child_revision'] and x['hub_enabled'] == x['child_enabled']
                                     for x in desired_actual['schedules']))
        hub_db_after = hubdb.snapshot([script])
        notif_rows = self.execs_by_task(task)
        notif_local = self.local_execs('a', task)
        hub_slots = self.unique_slots(notif_rows)
        local_slots = self.unique_slots(notif_local)
        # ---- E: restart persistence after the fault window
        sync_before_restart = self.local_sync_state('a')
        exec_ids_before_restart = sorted(r['id'] for r in notif_rows)
        slots_before_restart = sorted({(r.get('scheduled_time') or '') for r in notif_rows})
        stop = self.child('a').stop()
        self.budget['child_process_stops'] += 1
        time.sleep(3)
        start = self.child('a').start()
        self.budget['child_process_starts'] += 1
        back = self.wait_node(self.state['nodes']['a']['id'], 'online', 'synced', timeout=120)
        time.sleep(6)
        sync_after_restart = self.local_sync_state('a')
        script_after_restart = self.child('a').db_query('SELECT revision,sha256 FROM scripts WHERE id=?', [script])['rows']
        notif_rows_after = self.execs_by_task(task)
        exec_ids_after_restart = sorted(r['id'] for r in notif_rows_after)
        new_ids_after_restart = sorted(set(exec_ids_after_restart) - set(exec_ids_before_restart))
        new_rows_after_restart = [r for r in notif_rows_after if r['id'] in new_ids_after_restart]
        new_slots_are_new = all((r.get('scheduled_time') or '') not in slots_before_restart
                                for r in new_rows_after_restart)
        out = {
            'at_utc': now_utc(),
            'scenario': 'hub-side Script definition changes while the owned child channel is fault-injected: a real '
                        'CHANGE_NOTIFICATION is lost, then a real one is duplicated, then a captured stale one is '
                        'replayed (reordered) after the newer revision was already applied',
            'script_id': script, 'hub_revision_before': hub_rev_before,
            'child_before': child_pre,
            'lost_notification': {'put_http': codeA, 'new_revision': scriptA.get('revision'),
                                  'new_sha256': scriptA.get('sha256'),
                                  'frames_dropped': dropsA,
                                  'child_converged': {'complete': convA['complete'], 'seconds': convA['seconds']},
                                  'convergence_samples_tail': convA['samples'][-3:]},
            'duplicate_notification': {'put_http': codeB, 'new_revision': scriptB.get('revision'),
                                       'new_sha256': scriptB.get('sha256'),
                                       'frames_duplicated': dupsB,
                                       'child_converged': {'complete': convB['complete'], 'seconds': convB['seconds']},
                                       'convergence_samples_tail': convB['samples'][-3:]},
            'reordered_stale_notification': {
                'source_frame': {'seq': stale['seq'], 'global_revision': stale['global_revision']} if stale else None,
                'injected_frame': {'seq': replay_rec['seq'], 'global_revision': replay_rec.get('global_revision'),
                                   'injected': replay_rec.get('injected'), 'replayed_seq': replay_rec.get('replayed_seq')}
                if replay_rec else None,
                'sync_requests_before': sync_before_c, 'sync_requests_after': sync_after_c,
                'sync_request_observation_note': 'SYNC_REQUESTs are frequent on this shared authority (heartbeat '
                                                 'revision checks every 5s plus other batches\' object changes); the '
                                                 'count delta inside the window is therefore not a clean discriminator '
                                                 'and is reported as informational only. The applied-state checks below '
                                                 'are the authoritative signal.',
                'local_global_revision_before': rev_before_c, 'local_global_revision_after': rev_after_c,
                'child_script_before': script_before_c, 'child_script_after': script_after_c,
                'hub_script_final': {k: hub_script_final.get(k) for k in ('revision', 'sha256')},
                'stale_revision_older_than_local_at_injection': bool(stale)
                and stale.get('global_revision') is not None and rev_before_c is not None
                and stale['global_revision'] < rev_before_c,
                'no_state_regression': bool(script_after_c) and bool(script_before_c)
                and script_after_c[0]['revision'] == script_before_c[0]['revision']
                and script_after_c[0]['sha256'] == script_before_c[0]['sha256']
                and rev_after_c >= rev_before_c},
            'desired_state_vs_actual': {'comparison': desired_actual, 'consistent': desired_actual_ok,
                                        'child_sync_state': self.local_sync_state('a'),
                                        'hub_revision_sequence': hub_db_after['revision_sequence'],
                                        'hub_agent_sync_state_rows': [r for r in hub_db_after['agent_sync_state']
                                                                      if r['node_id'] == self.state['nodes']['a']['id']]},
            'schedule_idempotency_across_fault_window': {
                'schedule_id': schedule, 'hub_rows': len(notif_rows), 'child_rows': len(notif_local),
                'hub_slot_uniqueness': hub_slots, 'child_slot_uniqueness': local_slots,
                'sample_hub_rows': [{'id': r['id'], 'status': r.get('status'), 'scheduled_time': r.get('scheduled_time'),
                                     'offline': r.get('offline')} for r in notif_rows[-3:]]},
            'restart_persistence': {
                'stop': stop, 'start': start,
                'node_after_restart': back['last'], 'restart_wait_seconds': back['seconds'],
                'sync_before_restart': sync_before_restart, 'sync_after_restart': sync_after_restart,
                'script_row_after_restart': script_after_restart,
                'execution_ids_before_restart': exec_ids_before_restart,
                'execution_ids_after_restart': exec_ids_after_restart,
                'new_execution_ids_after_restart': new_ids_after_restart,
                'new_rows_after_restart': [{'id': r['id'], 'scheduled_time': r.get('scheduled_time'),
                                            'status': r.get('status')} for r in new_rows_after_restart],
                'new_rows_slots_were_not_previously_executed': new_slots_are_new,
                'note': 'a restart may legitimately add executions for slots that were missed while the Agent was '
                        'down (run_once misfire); what must NOT happen is re-uploading or duplicating an already '
                        'synced execution'},
            'assertions': {
                'lost_notification_really_dropped': bool(dropsA) and dropsA[0].get('object_id') == script
                and dropsA[0].get('global_revision') is not None,
                'lost_notification_recovered_by_revision_check': convA['complete'],
                'duplicate_notification_really_duplicated': bool(dupsB) and dupsB[0].get('object_id') == script,
                'duplicate_notification_idempotent_convergence': convB['complete'],
                'stale_notification_replay_ignored': bool(stale) and bool(replay_rec)
                and stale.get('global_revision') is not None and rev_before_c is not None
                and stale['global_revision'] < rev_before_c
                and bool(script_after_c) and bool(script_before_c)
                and script_after_c[0]['revision'] == script_before_c[0]['revision']
                and script_after_c[0]['sha256'] == script_before_c[0]['sha256']
                and rev_after_c >= rev_before_c,
                'desired_state_equals_actual_after_faults': desired_actual_ok,
                'schedule_slots_unique_under_faults': hub_slots['unique'] and local_slots['unique'],
                'schedule_still_produced_real_executions': len(notif_rows) >= 1 and len(notif_local) >= 1,
                'child_restart_resynced': back['complete'],
                'restart_preserved_definition': bool(script_after_restart)
                and script_after_restart[0]['revision'] == scriptB.get('revision'),
                'restart_preserved_all_pre_restart_executions': set(exec_ids_before_restart) <= set(exec_ids_after_restart),
                'restart_no_duplicate_execution_ids': len(exec_ids_after_restart) == len(set(exec_ids_after_restart)),
                'restart_new_rows_are_new_slots_only': new_slots_are_new,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase5-ar08-notification-faults.json', out)
        self.set_schedule_enabled('sc_notif_agent', False)
        if not out['assertions_pass']:
            raise RuntimeError('phase5_ar08_failed')
        return out

    # ---------------- phase 6: P12P13-06 ----------------
    def phase6_p12p13_06_owner_difference(self):
        fx = self.state['fixtures']
        proxy = self.proxies['a']
        t_hub, t_agent = fx['task_owner_hub'], fx['task_owner_agent']
        self.set_schedule_enabled('sc_owner_hub', True)
        self.set_schedule_enabled('sc_owner_agent', True)
        self.wait_local_schedules('a', [self.state['schedules']['sc_owner_hub'],
                                        self.state['schedules']['sc_owner_agent']], timeout=60)

        def enough():
            h = len(self.execs_by_task(t_hub))
            a = len(self.execs_by_task(t_agent))
            return (a, h) if (a >= 2 and h >= 2) else None

        got = wait_for(enough, timeout=240, interval=5.0, label='owner_two_slots_each')
        sync_wait = wait_for(
            lambda: {'hub_hub': len(self.execs_by_task(t_hub)), 'local_hub': len(self.local_execs('a', t_hub)),
                     'hub_agent': len(self.execs_by_task(t_agent)), 'local_agent': len(self.local_execs('a', t_agent))},
            lambda v: v['hub_hub'] == v['local_hub'] and v['hub_agent'] == v['local_agent'], timeout=45,
            interval=2.0, label='hub_child_row_counts_match')
        time.sleep(2)
        h_rows, a_rows = self.execs_by_task(t_hub), self.execs_by_task(t_agent)
        h_local, a_local = self.local_execs('a', t_hub), self.local_execs('a', t_agent)
        h_frames = proxy.frames_of('RUN_EXECUTION', 'hub_to_agent', t_hub)
        a_frames = proxy.frames_of('RUN_EXECUTION', 'hub_to_agent', t_agent)
        h_fin = proxy.frames_of('EXECUTION_FINISHED', 'agent_to_hub', t_hub)
        a_fin = proxy.frames_of('EXECUTION_FINISHED', 'agent_to_hub', t_agent)
        h_ids, a_ids = {r['id'] for r in h_rows}, {r['id'] for r in a_rows}
        h_disp, a_disp = {f.get('execution_id') for f in h_frames}, {f.get('execution_id') for f in a_frames}
        h_reported = {f.get('execution_id') for f in h_fin}
        a_reported = {f.get('execution_id') for f in a_fin}

        def latency(rows):
            vals = []
            for r in rows:
                st, stt = ts_of(r.get('scheduled_time')), ts_of(r.get('start_time'))
                if st and stt:
                    vals.append(round(stt - st, 3))
            return vals

        marker_h = self.marker_lines('a', fx['script_owner_hub_tag'])
        marker_a = self.marker_lines('a', fx['script_owner_agent_tag'])
        out = {
            'at_utc': now_utc(),
            'scenario': 'two cron ("* * * * *") schedules on the same owned child: one execution_owner=hub, one '
                        'agent. The owned frame proxy is the independent source-of-trigger oracle.',
            'hub_owned': {'task_id': t_hub, 'hub_executions': len(h_rows), 'distinct_slots': len(h_ids),
                          'all_ids_dispatched_by_hub': h_ids <= h_disp and bool(h_ids),
                          'run_frames_for_task': [{'seq': f['seq'], 'execution_id': f.get('execution_id'),
                                                   'scheduled_time': f.get('scheduled_time')} for f in h_frames],
                          'agent_reported_finished_ids': sorted(h_reported),
                          'child_local_rows': len(h_local),
                          'child_trigger_types': sorted({r['trigger_type'] for r in h_local}),
                          'start_minus_slot_sec': latency(h_rows),
                          'slot_uniqueness_hub': self.unique_slots(h_rows),
                          'slot_uniqueness_child': self.unique_slots(h_local),
                          'marker_lines': marker_h.get('line_count')},
            'agent_owned': {'task_id': t_agent, 'hub_executions': len(a_rows),
                            'hub_dispatched_run_frames_for_task': len(a_frames),
                            'all_ids_reported_by_agent': a_ids <= a_reported and bool(a_ids),
                            'finished_frames_for_task': [{'seq': f['seq'], 'execution_id': f.get('execution_id'),
                                                          'scheduled_time': f.get('scheduled_time')} for f in a_fin],
                            'child_local_rows': len(a_local),
                            'child_trigger_types': sorted({r['trigger_type'] for r in a_local}),
                            'child_ids_not_dispatched_by_hub': sorted(a_ids - a_disp),
                            'start_minus_slot_sec': latency(a_rows),
                            'slot_uniqueness_hub': self.unique_slots(a_rows),
                            'slot_uniqueness_child': self.unique_slots(a_local),
                            'marker_lines': marker_a.get('line_count')},
            'wait': {'complete': got['complete'], 'seconds': got['seconds'],
                     'row_count_sync': {'complete': sync_wait['complete'], 'seconds': sync_wait['seconds'],
                                        'last': sync_wait['last']}},
            'assertions': {
                'hub_owned_all_slots_dispatched_by_hub': bool(h_ids) and h_ids <= h_disp and len(h_rows) >= 2,
                'agent_owned_zero_hub_dispatch_frames': len(a_frames) == 0,
                'agent_owned_rows_all_reported_by_agent_frames': bool(a_ids) and a_ids <= a_reported,
                'agent_owned_local_journal_has_schedule_rows': len(a_local) == len(a_rows) and bool(a_local),
                'hub_owned_local_journal_has_schedule_rows': len(h_local) == len(h_rows) and bool(h_local),
                'both_owners_single_execution_per_slot': self.unique_slots(h_rows)['unique']
                and self.unique_slots(a_rows)['unique'] and self.unique_slots(h_local)['unique']
                and self.unique_slots(a_local)['unique'],
                'both_owners_real_business_effect': (marker_h.get('line_count') or 0) >= 2
                and (marker_a.get('line_count') or 0) >= 2,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase6-p12p13-06-owner-difference.json', out)
        self.set_schedule_enabled('sc_owner_hub', False)
        self.set_schedule_enabled('sc_owner_agent', False)
        if not out['assertions_pass']:
            raise RuntimeError('phase6_p12p13_06_failed')
        return out

    # ---------------- phase 7: P12P13-07 ----------------
    def phase7_p12p13_07_offline_policy_quadrants(self):
        fx = self.state['fixtures']
        proxy = self.proxies['a']
        node_id = self.state['nodes']['a']['id']
        t_allow, t_req = fx['task_off_allow'], fx['task_off_req']
        self.set_schedule_enabled('sc_off_allow', True)
        self.set_schedule_enabled('sc_off_req', True)
        self.wait_local_schedules('a', [self.state['schedules']['sc_off_allow'],
                                        self.state['schedules']['sc_off_req']], timeout=60)

        def counts():
            return {'allow': len(self.local_execs('a', t_allow)), 'req': len(self.local_execs('a', t_req))}
        clock = self.clock_offset()
        off_sec = clock['offset_sec_median']
        online_wait = wait_for(lambda: (lambda c: c if c['allow'] >= 2 and c['req'] >= 2 else None)(counts()),
                               timeout=180, interval=5.0, label='online_quadrants')
        online_counts = counts()
        allow_online_rows = self.local_execs('a', t_allow)
        req_online_rows = self.local_execs('a', t_req)
        # real disconnect of the owned child's own channel
        t_cut = time.time()
        t_cut_child = t_cut + off_sec
        proxy.set_mode('cut')
        self.budget['channel_cuts'] = self.budget.get('channel_cuts', 0) + 1
        off = wait_for(lambda: (lambda n: n.get('status'))(self.node(node_id) or {}),
                       lambda v: v == 'offline', timeout=95, interval=2.0, label='node_offline')
        cut_marker = now_utc()
        time.sleep(95)
        allow_off_rows = self.local_execs('a', t_allow)
        req_off_rows = self.local_execs('a', t_req)
        allow_new_offline = [r for r in allow_off_rows if (ts_of(r.get('start_time')) or 0) >= t_cut_child]
        req_new_offline = [r for r in req_off_rows if (ts_of(r.get('start_time')) or 0) >= t_cut_child]
        # restore the channel
        t_restore = time.time()
        t_restore_child = t_restore + off_sec
        proxy.set_mode('pass')
        back = self.wait_node(node_id, 'online', 'synced', timeout=120)
        upload_wait = wait_for(
            lambda: (lambda rows: [r for r in rows if (ts_of(r.get('start_time')) or 0) >= t_cut_child])(self.local_execs('a', t_allow)),
            lambda rows: bool(rows) and all(r['synced'] == 1 for r in rows),
            timeout=90, interval=2.0, label='offline_rows_uploaded')
        hub_allow = self.execs_by_task(t_allow)
        hub_offline_rows = [r for r in hub_allow if r.get('offline') is True]
        time.sleep(40)
        req_all = self.local_execs('a', t_req)
        req_post = [r for r in req_all if (ts_of(r.get('start_time')) or 0) >= t_cut_child]
        # compensation signature: exactly one execution whose reported slot lies inside the
        # connectivity-loss span, although that span covers ~3 missed 30s slots
        catchup = [r for r in req_all
                   if t_cut_child <= (ts_of(r.get('scheduled_time')) or -1) <= t_restore_child]
        missed_slots_estimate = int((t_restore_child - t_cut_child) // 30)
        # freeze the schedules before the final count/business-effect comparison
        self.set_schedule_enabled('sc_off_allow', False)
        self.set_schedule_enabled('sc_off_req', False)
        time.sleep(5)
        allow_after = self.local_execs('a', t_allow)
        req_final = self.local_execs('a', t_req)
        marker_allow = self.marker_lines('a', fx['script_off_allow_tag'])
        marker_req = self.marker_lines('a', fx['script_off_req_tag'])
        out = {
            'at_utc': now_utc(),
            'scenario': 'offline_policy x hub-connectivity matrix on agent-owned interval schedules (30s): while the '
                        'owned child channel is cut the agent local scheduler keeps ticking; allow_offline must keep '
                        'creating executions, hub_online_required must not',
            'online_quadrant': {
                'allow_offline_online': {'rows': len(allow_online_rows),
                                         'offline_flags': sorted({r['offline'] for r in allow_online_rows}),
                                         'sample': allow_online_rows[-1] if allow_online_rows else None},
                'hub_online_required_online': {'rows': len(req_online_rows),
                                               'offline_flags': sorted({r['offline'] for r in req_online_rows}),
                                               'sample': req_online_rows[-1] if req_online_rows else None},
                'wait': {'complete': online_wait['complete'], 'seconds': online_wait['seconds']}},
            'offline_window': {
                'cut_at_utc': now_utc(t_cut), 'node_status_offline_observed': off['complete'],
                'node_offline_wait_seconds': off['seconds'], 'window_seconds': 95,
                'node_status_samples': off['samples'][-4:],
                'cut_seam': 'proxy closes both relay legs and stops its listener (agent dials fail at TCP level) so '
                            'the agent cannot establish any handshake during the cut',
                'child_clock_offset': clock,
                'cut_at_child_clock': now_utc(t_cut_child),
                'allow_offline_created_rows': len(allow_new_offline),
                'allow_offline_rows': [{'id': r['id'], 'status': r['status'], 'offline': r['offline'],
                                        'synced': r['synced'], 'scheduled_time': r['scheduled_time']}
                                       for r in allow_new_offline],
                'hub_online_required_created_rows': len(req_new_offline),
                'hub_online_required_rows': req_new_offline},
            'recovery': {
                'restore_at_utc': now_utc(t_restore), 'node_back': back['last'],
                'node_back_wait_seconds': back['seconds'],
                'upload_wait': {'complete': upload_wait['complete'], 'seconds': upload_wait['seconds']},
                'allow_offline_rows_synced_after_restore': [
                    {'id': r['id'], 'offline': r['offline'], 'synced': r['synced']} for r in allow_after
                    if (ts_of(r.get('start_time')) or 0) >= t_cut_child],
                'hub_rows_with_offline_flag': [{'id': r['id'], 'status': r.get('status'), 'offline': r.get('offline'),
                                                'scheduled_time': r.get('scheduled_time')} for r in hub_offline_rows],
                'hub_online_required_catchup_rows': [{'id': r['id'], 'scheduled_time': r['scheduled_time'],
                                                      'start_time': r['start_time']} for r in catchup],
                'missed_slots_estimate_in_offline_span': missed_slots_estimate,
                'hub_online_required_post_restore_rows': len(req_post)},
            'assertions': {
                'quadrant1_online_allow_offline_fires': len(allow_online_rows) >= 2
                and all(r['offline'] == 0 for r in allow_online_rows),
                'quadrant2_online_required_fires': len(req_online_rows) >= 2
                and all(r['offline'] == 0 for r in req_online_rows),
                'quadrant3_offline_allow_offline_fires': len(allow_new_offline) >= 2
                and all(r['offline'] == 1 for r in allow_new_offline),
                'quadrant4_offline_required_does_not_fire': len(req_new_offline) == 0,
                'offline_rows_were_unsynced_while_disconnected': all(r['synced'] == 0 for r in allow_new_offline),
                'offline_rows_uploaded_after_reconnect': upload_wait['complete'],
                'hub_shows_offline_executions_with_offline_flag': len(hub_offline_rows) >= 2,
                'required_resumes_with_single_catchup': len(catchup) == 1,
                'required_catchup_single_for_many_missed_slots': len(catchup) == 1 and missed_slots_estimate >= 2,
                'required_catchup_slot_inside_offline_span': bool(catchup),
                'required_normal_cadence_resumed': len(req_post) >= 2,
                'slots_unique_allow': self.unique_slots(allow_after)['unique'],
                'slots_unique_required': self.unique_slots(req_final)['unique'],
                'business_effect_lines_match_rows': (marker_allow.get('line_count') == len(allow_after)
                                                     and marker_req.get('line_count') == len(req_final)),
            },
            'final_child_row_counts': {'allow': len(allow_after), 'required': len(req_final)},
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase7-p12p13-07-offline-quadrants.json', out)
        self.set_schedule_enabled('sc_off_allow', False)
        self.set_schedule_enabled('sc_off_req', False)
        if not out['assertions_pass']:
            raise RuntimeError('phase7_p12p13_07_failed')
        return out

    # ---------------- phase 8: P12P13-08 ----------------
    def phase8_p12p13_08_misfire_after_stop(self):
        fx = self.state['fixtures']
        node_id = self.state['nodes']['a']['id']
        t_ro, t_sk = fx['task_mis_runonce'], fx['task_mis_skip']
        self.set_schedule_enabled('sc_mis_runonce', True)
        self.set_schedule_enabled('sc_mis_skip', True)
        self.wait_local_schedules('a', [self.state['schedules']['sc_mis_runonce'],
                                        self.state['schedules']['sc_mis_skip']], timeout=60)

        def counts():
            return {'runonce': len(self.local_execs('a', t_ro)), 'skip': len(self.local_execs('a', t_sk))}
        clock = self.clock_offset()
        off_sec = clock['offset_sec_median']
        base = wait_for(lambda: (lambda c: c if c['runonce'] >= 1 and c['skip'] >= 1 else None)(counts()),
                        timeout=180, interval=5.0, label='misfire_baseline')
        base_counts = counts()
        base_rows_ro = self.local_execs('a', t_ro)
        # real stop of the owned child Agent process (>=3 missed 30s slots)
        stop = self.child('a').stop()
        self.budget['child_process_stops'] += 1
        t_stop = time.time()
        t_stop_child = t_stop + off_sec
        time.sleep(95)
        start = self.child('a').start()
        self.budget['child_process_starts'] += 1
        t_start = time.time()
        t_start_child = t_start + off_sec
        back = self.wait_node(node_id, 'online', 'synced', timeout=120)
        time.sleep(55)
        rows_ro = self.local_execs('a', t_ro)
        rows_sk = self.local_execs('a', t_sk)
        new_ro = [r for r in rows_ro if (ts_of(r.get('start_time')) or 0) >= t_stop_child]
        new_sk = [r for r in rows_sk if (ts_of(r.get('start_time')) or 0) >= t_stop_child]
        # a compensation row reports the slot that had already been reached when the Agent was
        # restarted (its slot is at or before the restart instant)
        catchup_ro = [r for r in new_ro if (ts_of(r.get('scheduled_time')) or 9e18) <= t_start_child + 2]
        normal_ro = [r for r in new_ro if r not in catchup_ro]
        skip_catchup = [r for r in new_sk if (ts_of(r.get('scheduled_time')) or 9e18) <= t_start_child + 2]
        skip_normal = [r for r in new_sk if r not in skip_catchup]
        pre_ro = [r for r in rows_ro if (ts_of(r.get('start_time')) or 9e18) < t_stop_child]
        last_pre_ro = max(pre_ro, key=lambda r: ts_of(r.get('start_time')) or 0) if pre_ro else None
        first_post_ro = min(new_ro, key=lambda r: ts_of(r.get('start_time')) or 9e18) if new_ro else None
        missed_boundaries = None
        if last_pre_ro is not None and catchup_ro:
            span = (ts_of(catchup_ro[0].get('scheduled_time')) or 0) - (ts_of(last_pre_ro.get('scheduled_time')) or 0)
            missed_boundaries = int(round(span / 30.0)) - 1
        hub_ro = self.execs_by_task(t_ro)
        hub_sk = self.execs_by_task(t_sk)
        # freeze both schedules before the final count/business-effect comparison
        self.set_schedule_enabled('sc_mis_runonce', False)
        self.set_schedule_enabled('sc_mis_skip', False)
        time.sleep(5)
        rows_ro = self.local_execs('a', t_ro)
        rows_sk = self.local_execs('a', t_sk)
        hub_ro = self.execs_by_task(t_ro)
        hub_sk = self.execs_by_task(t_sk)
        hub_catchup_ro = [r for r in hub_ro if (ts_of(r.get('start_time')) or 0) >= t_stop_child
                          and (ts_of(r.get('scheduled_time')) or 9e18) <= t_start_child + 2]
        hub_catchup_sk = [r for r in hub_sk if (ts_of(r.get('start_time')) or 0) >= t_stop_child
                          and (ts_of(r.get('scheduled_time')) or 9e18) <= t_start_child + 2]
        marker_ro = self.marker_lines('a', fx['script_mis_runonce_tag'])
        marker_sk = self.marker_lines('a', fx['script_mis_skip_tag'])
        out = {
            'at_utc': now_utc(),
            'scenario': 'agent-owned interval schedules (30s), one misfire_policy=run_once and one skip, on the same '
                        'owned child; the Agent process is really stopped across >=3 slots and then restarted',
            'baseline': {'counts': base_counts, 'wait': {'complete': base['complete'], 'seconds': base['seconds']},
                         'runonce_rows': [{'id': r['id'], 'scheduled_time': r['scheduled_time']} for r in base_rows_ro]},
            'stop_window': {'stop_at_utc': now_utc(t_stop), 'stop_result': stop, 'stopped_seconds': 95,
                            'restart_at_utc': now_utc(t_start), 'start_result': start,
                            'child_clock_offset': clock,
                            'stop_at_child_clock': now_utc(t_stop_child),
                            'restart_at_child_clock': now_utc(t_start_child),
                            'node_back': back['last'], 'node_back_wait_seconds': back['seconds']},
            'after_restart': {
                'last_pre_stop_runonce_row': ({'id': last_pre_ro['id'], 'scheduled_time': last_pre_ro['scheduled_time'],
                                               'start_time': last_pre_ro['start_time']} if last_pre_ro else None),
                'first_post_restart_runonce_row': ({'id': first_post_ro['id'],
                                                    'scheduled_time': first_post_ro['scheduled_time'],
                                                    'start_time': first_post_ro['start_time']} if first_post_ro else None),
                'missed_boundaries_between_last_pre_and_catchup': missed_boundaries,
                'runonce_new_rows': [{'id': r['id'], 'status': r['status'], 'scheduled_time': r['scheduled_time'],
                                      'start_time': r['start_time'], 'trigger_type': r['trigger_type']} for r in new_ro],
                'runonce_catchup_rows': [{'id': r['id'], 'scheduled_time': r['scheduled_time'],
                                          'start_time': r['start_time'],
                                          'slot_is_inside_stopped_window': bool(r['scheduled_time'])
                                          and t_stop_child <= (ts_of(r['scheduled_time']) or 0) <= t_start_child + 2,
                                          'start_lag_after_slot_sec': round((ts_of(r.get('start_time')) or 0)
                                                                            - (ts_of(r.get('scheduled_time')) or 0), 3)}
                                         for r in catchup_ro],
                'runonce_normal_cadence_rows': [r['id'] for r in normal_ro],
                'skip_new_rows': [{'id': r['id'], 'status': r['status'], 'scheduled_time': r['scheduled_time'],
                                   'start_time': r['start_time']} for r in new_sk],
                'skip_catchup_rows': [r['id'] for r in skip_catchup],
                'skip_normal_cadence_rows': [r['id'] for r in skip_normal],
                'hub_catchup_rows_runonce': [{'id': r['id'], 'status': r.get('status'),
                                              'scheduled_time': r.get('scheduled_time')} for r in hub_catchup_ro],
                'hub_catchup_rows_skip': [r['id'] for r in hub_catchup_sk],
                'runonce_total_rows_child': len(rows_ro), 'skip_total_rows_child': len(rows_sk),
                'runonce_total_rows_hub': len(hub_ro), 'skip_total_rows_hub': len(hub_sk),
            },
            'assertions': {
                'runonce_catchup_single_execution': len(catchup_ro) == 1,
                'runonce_first_post_restart_row_is_the_compensation': bool(first_post_ro) and bool(catchup_ro)
                and first_post_ro['id'] == catchup_ro[0]['id'],
                'runonce_catchup_slot_is_inside_stopped_window': bool(catchup_ro)
                and t_stop_child <= (ts_of(catchup_ro[0].get('scheduled_time')) or 0) <= t_start_child + 2,
                'runonce_catchup_covers_many_missed_slots': missed_boundaries is not None and missed_boundaries >= 2,
                'runonce_catchup_ran_soon_after_restart': bool(catchup_ro)
                and ((ts_of(catchup_ro[0].get('start_time')) or 9e18) - t_start_child) <= 25,
                'runonce_resumes_normal_cadence': len(normal_ro) >= 1,
                'runonce_catchup_visible_on_hub': len(hub_catchup_ro) >= 1
                and len(hub_catchup_ro) == len(catchup_ro),
                'skip_does_not_catch_up': len(skip_catchup) == 0 and len(hub_catchup_sk) == 0,
                'skip_resumes_normal_cadence': len(skip_normal) >= 1,
                'runonce_no_duplicate_slots': self.unique_slots(rows_ro)['unique']
                and self.unique_slots(hub_ro)['unique'],
                'skip_no_duplicate_slots': self.unique_slots(rows_sk)['unique']
                and self.unique_slots(hub_sk)['unique'],
                'business_effect_lines_match_rows': marker_ro.get('line_count') == len(rows_ro)
                and marker_sk.get('line_count') == len(rows_sk),
                'child_resynced_after_restart': back['complete'],
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase8-p12p13-08-misfire-after-stop.json', out)
        self.set_schedule_enabled('sc_mis_runonce', False)
        self.set_schedule_enabled('sc_mis_skip', False)
        if not out['assertions_pass']:
            raise RuntimeError('phase8_p12p13_08_failed')
        return out

    # ---------------- phase 9: cleanup ----------------
    def phase9_cleanup(self):
        results = {'deleted': [], 'errors': [], 'retained_execution_history': {}}
        order = [('schedule', self.state['schedules'].get(k)) for k in
                 ('sc_notif_agent', 'sc_owner_hub', 'sc_owner_agent', 'sc_off_allow', 'sc_off_req',
                  'sc_mis_runonce', 'sc_mis_skip')]
        order += [('task', self.state['fixtures'].get(k)) for k in
                  ('task_dup_run', 'task_pending', 'task_notif', 'task_owner_agent', 'task_owner_hub',
                   'task_off_allow', 'task_off_req', 'task_mis_runonce', 'task_mis_skip')]
        order += [('script', self.state['fixtures'].get(k)) for k in
                  ('script_marker', 'script_pending', 'script_notif', 'script_owner_agent', 'script_owner_hub',
                   'script_off_allow', 'script_off_req', 'script_mis_runonce', 'script_mis_skip')]
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
        for key, tid in self.state['fixtures'].items():
            if key.startswith('task_'):
                try:
                    rows = self.execs_by_task(tid)
                    results['retained_execution_history'][key] = {
                        'task_id': tid, 'count': len(rows),
                        'statuses': sorted({r.get('status') for r in rows}),
                        'ids': [r['id'] for r in rows]}
                except Exception as exc:
                    results['errors'].append('history_read_%s:%s' % (key, exc))
        ch = self.child('a')
        try:
            rm = ch.remove()
            self.budget['child_process_stops'] += 1
        except Exception as exc:
            rm = {'error': str(exc)}
            results['errors'].append('child_remove:%s' % exc)
        results['child_removal'] = rm
        for label, proxy in self.proxies.items():
            try:
                proxy.stop()
            except Exception as exc:
                results['errors'].append('proxy_stop_%s:%s' % (label, exc))
        nid = self.state['nodes'].get('a', {}).get('id')
        if nid:
            code, body = self.hub.delete('/api/nodes/' + self.hub.quote(nid))
            gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(nid))
            results.setdefault('nodes', []).append({'label': 'a', 'id': nid, 'delete_http': code,
                                                    'get_http': gcode, 'delete_body': body})
            self.state['delete_results'][nid] = {'kind': 'node', 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append(('node', nid))
        results['all_get_404'] = all(v['get_http'] == 404 for v in self.state['delete_results'].values())
        self.ev('phase9-cleanup.json', results)
        self.rec('fixture-ledger.json', {
            'batch_id': BATCH, 'nonce': NONCE, 'prefix': PREFIX, 'endpoint': ENDPOINT,
            'node_used': {'native-2': NODE_HOST},
            'hub_fixtures': [{'kind': k, 'id': i, 'name': None,
                              'delete_http': self.state['delete_results'].get(i, {}).get('delete_http'),
                              'get_http': self.state['delete_results'].get(i, {}).get('get_http'),
                              'deleted_get404': self.state['delete_results'].get(i, {}).get('get_http') == 404}
                             for (k, i) in self.state['deleted'] if k != 'node'],
            'owned_node': [{'id': nid, 'delete_http': self.state['delete_results'].get(nid, {}).get('delete_http'),
                            'get_http': self.state['delete_results'].get(nid, {}).get('get_http')}
                           for (k, nid) in self.state['deleted'] if k == 'node'],
            'child_artifacts': {'launcher': self.child('a').root if self.child('a') else None,
                                'removal': rm},
            'proxies': [{'label': k, 'listen_port': v.state()['listen_port'], 'state': v.state()}
                        for k, v in self.proxies.items()],
            'retained_execution_history': results['retained_execution_history'],
            'notes': 'Execution rows are undeletable product history; counts are registered above. '
                     'All owned objects were deleted by exact id and re-read (expected 404).'})
        return results

    # ---------------- phase 10: final cleanup oracle (in-process summary) ----------------
    def phase10_oracle(self, cleanup):
        nodes = self.nodes_by_id()
        protected_after, ok = {}, True
        protected_prefix = 'qa-repair-20260930-101945-native-'
        for nid, n in nodes.items():
            host = n.get('hostname') or nid
            if host.startswith(PREFIX):
                continue
            protected = host.startswith(protected_prefix)
            protected_after[host] = {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                     'protected_repair_native': protected}
            if protected and (n.get('status') != 'online' or n.get('sync_status') != 'synced'):
                ok = False
        leftovers = {
            'scripts': [s['id'] for s in self.hub.json('/api/scripts') if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in self.hub.json('/api/tasks') if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in self.hub.json('/api/schedules') if (s.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '', label='a').inventory()
        oracle = {
            'at_utc': now_utc(), 'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
            'native2_owned_child_orphan': bool(inv['root_exists'] or inv['config_exists']
                                              or inv['owned_child_processes'] or inv['any_g23_processes']),
            'native2_inventory': {k: inv[k] for k in ('root_exists', 'config_exists', 'owned_child_processes',
                                                      'any_g23_processes', 'owned_root_glob',
                                                      'primary_agent_unit', 'repair_agent_unit')},
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'protected_units_active': (inv['primary_agent_unit'].get('ActiveState') == 'active'
                                       and inv['repair_agent_unit'].get('ActiveState') == 'active'),
            'healthz_repair_18080': self.hub.raw('GET', '/healthz', None, timeout=8)[0],
            'all_get_404': cleanup.get('all_get_404'),
        }
        oracle['ok'] = (oracle['protected_nodes_online_synced'] and oracle['hub_leftover_clear']
                        and not oracle['native2_owned_child_orphan'] and oracle['protected_units_active']
                        and oracle['healthz_repair_18080'] == 200 and oracle['all_get_404'] is True)
        self.ev('phase10-inprocess-oracle.json', oracle)
        return oracle


def main():
    import faulthandler
    faulthandler.enable()
    run = Run()
    evidence = {'batch': BATCH, 'nonce': NONCE, 'prefix': PREFIX, 'endpoint': ENDPOINT,
                'node_used': {'native-2': NODE_HOST}, 'phases': {}, 'deviations': []}
    progress = {'batch_id': BATCH, 'nonce': NONCE, 'prefix': PREFIX, 'phase': 'start',
                'updated_at_utc': now_utc(), 'cases': ['P11-08', 'AR-08', 'P17P18-03',
                                                       'P12P13-06', 'P12P13-07', 'P12P13-08']}
    failed = False

    def mark(phase):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        hubapi.write_json('progress.json', progress)

    phases = [
        ('guard', run.phase0_guard),
        ('child', run.phase1_child),
        ('fixtures', run.phase2_fixtures),
        ('p11-08', run.phase3_p11_08_duplicate_delivery),
        ('p17p18-03', run.phase4_p17p18_03_pending_window),
        ('ar-08', run.phase5_ar08_notification_faults),
        ('p12p13-06', run.phase6_p12p13_06_owner_difference),
        ('p12p13-07', run.phase7_p12p13_07_offline_policy_quadrants),
        ('p12p13-08', run.phase8_p12p13_08_misfire_after_stop),
    ]
    for name, fn in phases:
        if failed:
            break
        mark(name)
        try:
            evidence['phases'][name] = fn()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            evidence['deviations'].append('HARNESS_ERROR_%s:%s' % (name, exc))
            progress['harness_error'] = '%s: %s' % (name, exc)
            failed = True
    mark('frames')
    try:
        evidence['phases']['frames'] = {k: {'state': v.state(), 'frames': v.frames,
                                            'events': v.events} for k, v in run.proxies.items()}
    except Exception as exc:
        evidence['deviations'].append('FRAME_DUMP_ERROR:%s' % exc)
    mark('cleanup')
    try:
        cleanup = run.phase9_cleanup()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        cleanup = {'error': str(exc), 'all_get_404': False}
        evidence['deviations'].append('CLEANUP_ERROR:%s' % exc)
        failed = True
    evidence['phases']['cleanup'] = cleanup
    mark('oracle')
    try:
        evidence['phases']['oracle'] = run.phase10_oracle(cleanup)
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('ORACLE_ERROR:%s' % exc)
        failed = True
    evidence['state'] = run.state
    evidence['budget'] = run.budget
    evidence['failed'] = failed
    run.rec('run-evidence.json', evidence)
    progress['phase'] = 'done'
    progress['updated_at_utc'] = now_utc()
    progress['deviations'] = evidence['deviations']
    progress['failed'] = failed
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'failed': failed, 'deviations': evidence['deviations']}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
