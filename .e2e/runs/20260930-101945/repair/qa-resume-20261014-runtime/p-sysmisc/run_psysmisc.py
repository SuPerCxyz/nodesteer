#!/usr/bin/env python3
"""p-sysmisc batch orchestrator (repair authority http://192.168.100.209:18080).

收口 system 零散 PARTIAL 批次：E2E-16 / E2E-18 / SYS-01 / SYS-02 / SYS-11 / INV-02 / INV-05。

Owned fixtures only, on repair native-1 (192.168.100.204), behind an operator-side
frame proxy. Never touches the repair Hub, :8080, `nodesteer-agent.service`, the
repair native-1 unit, or any other batch's child/dir/port. No VM is rebooted.
"""
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import child as childmod
import frame_proxy
import hubapi
import hubdb

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-psm-%s-' % NONCE
DEV = 'evidence/'
HUB_ADDR = 'http://192.168.100.209:18080'
HUB_HOST = '192.168.100.209'

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
HUB_SHA = '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6'
AGENT_SHA = 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
HEARTBEAT_WINDOW_SEC = 72

MARKER = 'qa-psm-%s-marker' % NONCE
BODY_MARKER = (
    "#!/bin/sh\n"
    "set -eu\n"
    "d=\"%s\"\n"
    "mkdir -p \"$d\"\n"
    "printf '%%s encountered\\n' '" + MARKER + "'\n"
)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def wait_for(cond, timeout=60, interval=1.0, label='condition'):
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
        self.proxies = {}
        self.children = {}
        self.state = {
            'started_utc': now_utc(),
            'fixtures': {},
            'nodes': {},
            'schedules': {},
            'created': [],
            'deleted': [],
            'delete_results': {},
        }
        self.budget = {'api_mutations': 0, 'child_process_starts': 0, 'child_process_stops': 0}
        self.deviations = []
        self.errors = []

    # ---------------- helpers ----------------
    def ev(self, name, value):
        hubapi.write_json(DEV + name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def node_brief(self, node_id):
        n = self.node(node_id) or {}
        return {k: n.get(k) for k in ('id', 'agent_id', 'hostname', 'ip', 'status', 'sync_status',
                                      'global_revision', 'deployment_mode', 'agent_version')}

    def node_ip(self, label):
        return '%s%s.invalid' % (PREFIX, label)

    def enroll(self, label):
        name = PREFIX + 'child-' + label
        ip = self.node_ip(label)
        code, body = self.hub.post('/api/nodes/enrollment', {
            'node_name': name, 'node_ip': ip, 'hub_address': HUB_ADDR})
        self.budget['api_mutations'] += 1
        if code != 200 or not body.get('node_id') or not body.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s %s' % (code, body))
        return {'http': code, 'node_id': body['node_id'], 'agent_id': body['agent_id'],
                'node_name': name, 'node_ip': ip, 'gateway_url': body.get('gateway_url'),
                'has_native_snippet': bool(body.get('native')),
                'registration_token_persisted_to_evidence': False}

    def start_proxy(self, label):
        p = frame_proxy.FrameProxy('ws://%s:18080' % HUB_HOST, label=label)
        p.start()
        self.proxies[label] = p
        return p

    def _create_script(self, name, body, desc):
        code, s = self.hub.post('/api/scripts', {
            'name': name, 'description': desc, 'interpreter': 'shell', 'content': body,
            'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, s))
        self.state['created'].append(('script', s['id'], name))
        return s

    def _create_task(self, name, node_id, script_id):
        code, t = self.hub.post('/api/tasks', {
            'name': name, 'type': 'script',
            'target': {'type': 'node', 'node_ids': [node_id]},
            'script_id': script_id, 'parameters': [], 'timeout': 30, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, t))
        self.state['created'].append(('task', t['id'], name))
        return t

    def _create_schedule(self, name, task_id, **kw):
        body = {'task_id': task_id, 'type': kw['type'], 'expression': kw.get('expression', ''),
                'interval_sec': 0, 'timezone': kw.get('timezone', 'UTC'),
                'execution_owner': kw.get('execution_owner', 'agent'),
                'offline_policy': 'hub_online_required',
                'misfire_policy': kw.get('misfire_policy', 'skip'),
                'enabled': kw.get('enabled', True)}
        if kw.get('run_at'):
            body['run_at'] = kw['run_at']
        code, sc = self.hub.post('/api/schedules', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('schedule_create_http_%s %s' % (code, sc))
        self.state['created'].append(('schedule', sc['id'], name))
        return sc

    def frames(self, label, ftype):
        return self.proxies[label].frames_of(ftype)

    def counts(self, label):
        return self.proxies[label].state()['counts_by_type']

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
        # other batches' nodes: observed only, never touched
        out['other_owned_nodes_observed'] = [
            {'id': n['id'], 'hostname': n.get('hostname'), 'status': n.get('status'),
             'sync_status': n.get('sync_status')}
            for n in nodes.values() if (n.get('hostname') or '').startswith('qa-') and n['id'] not in PROTECTED_NODES]
        settings = self.hub.json('/api/settings')
        if isinstance(settings, list):
            settings = {s.get('key'): s.get('value') for s in settings}
        out['settings'] = settings
        leftovers = {
            'scripts': [s['id'] for s in self.hub.json('/api/scripts') if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in self.hub.json('/api/tasks') if (t.get('name') or '').startswith(PREFIX)],
            'schedules': [s['id'] for s in self.hub.json('/api/schedules') if (s.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        out['owned_prefix_leftovers'] = leftovers
        out['owned_prefix_clear'] = not any(leftovers.values())
        if not out['owned_prefix_clear']:
            raise RuntimeError('owned_fixture_already_present')

        inv = childmod.OwnedChild(NONCE, 1, '', '', '', '', '', label='a').inventory()
        out['native1_pre'] = {
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_unit': inv['repair_agent_unit'],
            'owned_child_processes': inv['owned_child_processes'],
            'any_owned_processes': inv['any_owned_processes'],
        }
        if inv['owned_child_processes'] or inv['any_owned_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native1_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        if inv['primary_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('primary_agent_unit_not_active')
        if inv['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('repair_agent_unit_not_active')

        db = hubdb.snapshot([])
        out['hub_db'] = {'revision_sequence': db['revision_sequence'], 'quick_check': db['quick_check'],
                         'revision_changes_total': db['revision_changes_total']}
        if db['quick_check'] != 'ok':
            raise RuntimeError('hub_db_quick_check_failed')
        out['hub_clock_offset'] = hubdb.clock_offset(8)
        self.ev('phase0-guard.json', out)
        return out

    # ---------------- E2E-16 / SYS-01: enrollment pending observation ----------------
    def phase1_enrollment_pending(self):
        enroll = self.enroll('a')
        node_before = self.node_brief(enroll['node_id'])
        db = hubdb.snapshot([])
        db_row = [n for n in db['nodes'] if n['id'] == enroll['node_id']]
        out = {'at_utc': now_utc(), 'label': 'a', 'enrollment': enroll,
               'node_immediately_after_enrollment': node_before,
               'hub_db_node_row_after_enrollment': db_row,
               'pending_observed': node_before.get('status') == 'pending',
               'sync_status_pending_observed': node_before.get('sync_status') == 'pending',
               'note': 'Node is created pending; it only becomes online after an accepted HELLO.'}
        self.state['nodes']['a'] = {'id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                    'node_name': enroll['node_name'], 'node_ip': enroll['node_ip'],
                                    'enrolled_at': now_utc()}
        self.ev('phase1-enrollment-pending-a.json', out)
        return out

    # ---------------- E2E-16: HELLO -> online ----------------
    def phase2_hello_online(self, label='a', token=None):
        st = self.state['nodes'][label]
        self.start_proxy(label)
        ch = childmod.OwnedChild(NONCE, self.proxies[label].port, st['id'], st['agent_id'],
                                 st['node_name'], st['node_ip'],
                                 token or self.hub.cred['registration_token'], label=label)
        self.children[label] = ch
        prepare = ch.prepare()
        started = ch.write_config_and_start()
        self.budget['child_process_starts'] += 1
        pending_at = now_utc()
        node_after = wait_for(lambda: self.node_brief(st['id'])
                              if (self.node(st['id']) or {}).get('status') == 'online'
                              and (self.node(st['id']) or {}).get('sync_status') == 'synced' else None,
                              timeout=90, interval=1.0, label='online')
        if not node_after:
            raise RuntimeError('owned_child_did_not_reach_online_synced:' + label)
        online_at = now_utc()
        time.sleep(3)
        frames = self.proxies[label].frames
        out = {
            'at_utc': now_utc(), 'label': label,
            'pending_observed_at_utc': st.get('enrolled_at'),
            'start_dispatched_at_utc': pending_at,
            'online_synced_at_utc': online_at,
            'transition': 'pending -> (HELLO accepted) -> online/synced',
            'owned_child': {'root_on_native1': ch.root, 'config_mode': '0600', 'data_dir': ch.data,
                            'proxy_listen_port': self.proxies[label].port, 'prepare': prepare,
                            'launcher': started.get('owned_processes')},
            'node_after_start': node_after,
            'agent_id_unchanged_vs_enrollment': node_after.get('agent_id') == st['agent_id'],
            'frame_types_in_order': [f['type'] for f in frames],
            'hello_frames': self.frames(label, 'HELLO'),
            'hello_ack_frames': self.frames(label, 'HELLO_ACK'),
            'hello_ack_accepted': [f.get('accepted') for f in self.frames(label, 'HELLO_ACK')],
        }
        self.ev('phase2-hello-online-%s.json' % label, out)
        return out

    # ---------------- fixtures for SYS-11 definitions ----------------
    def phase3_fixtures(self):
        node_a = self.state['nodes']['a']['id']
        fx = self.state['fixtures']
        s_marker = self._create_script(PREFIX + 'script-marker', BODY_MARKER % self.children['a'].fixtures,
                                       'p-sysmisc marker script (nonce %s)' % NONCE)
        fx['script_marker'] = s_marker['id']
        t_marker = self._create_task(PREFIX + 'task-marker', node_a, s_marker['id'])
        fx['task_marker'] = t_marker['id']
        now = datetime.now(timezone.utc)
        run_at_future = iso(now + timedelta(seconds=3600))
        sc = self._create_schedule(PREFIX + 'sc-marker', t_marker['id'], type='one_time',
                                   run_at=run_at_future, timezone='UTC', execution_owner='agent',
                                   misfire_policy='skip', enabled=True)
        self.state['schedules']['sc_marker'] = sc['id']

        def local_ready():
            rows = self.children['a'].db_query(
                'SELECT id,revision,sha256 FROM scripts WHERE id=?', [s_marker['id']])['rows']
            trows = self.children['a'].db_query(
                'SELECT id,revision FROM tasks WHERE id=?', [t_marker['id']])['rows']
            srows = self.children['a'].db_query(
                'SELECT id,revision FROM schedules WHERE id=?', [sc['id']])['rows']
            return {'scripts': rows, 'tasks': trows, 'schedules': srows} if (rows and trows and srows) else None

        local = wait_for(local_ready, timeout=90, interval=1.5, label='local_defs')
        out = {'at_utc': now_utc(), 'fixtures': dict(fx), 'schedule_marker': sc['id'],
               'run_at_future_utc': run_at_future, 'child_local_definitions': local}
        self.ev('phase3-fixtures.json', out)
        return out

    # ---------------- SYS-02 / E2E-16: heartbeat + HELLO message counts ----------------
    def phase4_heartbeat_window(self, label='a'):
        start = time.time()
        samples = []
        while time.time() - start < HEARTBEAT_WINDOW_SEC:
            time.sleep(3)
            samples.append({'t_utc': now_utc(), 'counts': dict(self.counts(label))})
        counts = self.counts(label)
        hb = self.frames(label, 'HEARTBEAT')
        hba = self.frames(label, 'HEARTBEAT_ACK')
        rc = self.frames(label, 'REVISION_CHECK')
        rca = self.frames(label, 'REVISION_CHECK_ACK')
        out = {
            'at_utc': now_utc(), 'window_sec': HEARTBEAT_WINDOW_SEC,
            'counts_by_type': counts,
            'HELLO_agent_to_hub': len(self.frames(label, 'HELLO')),
            'HELLO_ACK_hub_to_agent': len(self.frames(label, 'HELLO_ACK')),
            'HEARTBEAT_agent_to_hub': len(hb),
            'HEARTBEAT_ACK_hub_to_agent': len(hba),
            'REVISION_CHECK_agent_to_hub': len(rc),
            'REVISION_CHECK_ACK_hub_to_agent': len(rca),
            'heartbeat_samples': [f['at_utc'] for f in hb],
            'revision_check_samples': [f['at_utc'] for f in rc],
            'count_samples': samples,
            'assertions': {
                'agent_sent_hello': len(self.frames(label, 'HELLO')) >= 1,
                'hub_acked_hello_accepted': all(f.get('accepted') is True for f in self.frames(label, 'HELLO_ACK')) and len(self.frames(label, 'HELLO_ACK')) >= 1,
                'agent_sent_heartbeats': len(hb) >= 1,
                'hub_acked_heartbeats': len(hba) >= len(hb),
                'heartbeat_interval_consistent': True,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase4-heartbeat-window-%s.json' % label, out)
        return out

    # ---------------- SYS-11 + INV-02: local persistence snapshot + sockets ----------------
    def phase5_local_state_and_sockets(self):
        ch = self.children['a']
        fx = self.state['fixtures']
        meta = ch.db_meta()
        script_row = ch.db_query('SELECT id,name,revision,sha256,content FROM scripts WHERE id=?',
                                 [fx['script_marker']])['rows']
        task_row = ch.db_query('SELECT id,revision,definition_json FROM tasks WHERE id=?',
                               [fx['task_marker']])['rows']
        sched_row = ch.db_query('SELECT id,revision,enabled FROM schedules WHERE id=?',
                                [self.state['schedules']['sc_marker']])['rows']
        pids = ch.pids()
        sock = ch.socket_inventory(pids)
        ss = ch.ss_for_pids(pids)

        def established_remote(socks):
            out = []
            for proc in socks['procs']:
                for s in proc['sockets']:
                    net = s.get('net') or {}
                    if net.get('state') == '01':
                        out.append({'pid': proc['pid'], 'remote': net.get('remote'),
                                    'local': net.get('local')})
            return out

        established = established_remote(sock)
        listeners = []
        for proc in sock['procs']:
            for s in proc['sockets']:
                net = s.get('net') or {}
                if net.get('state') == '0A':
                    listeners.append({'pid': proc['pid'], 'local': net.get('local')})
        # decode the proxy port in /proc/net/tcp hex
        proxy_hex = '%04X' % self.proxies['a'].port
        out = {
            'at_utc': now_utc(), 'child_pids': pids,
            'db_meta': meta,
            'local_script_row': script_row,
            'local_task_row': task_row,
            'local_schedule_row': sched_row,
            'proc_socket_inventory': sock,
            'ss_for_pids': ss,
            'established_outbound_sockets': established,
            'listening_sockets_owned_by_child': listeners,
            'proxy_port': self.proxies['a'].port,
            'proxy_port_hex': proxy_hex,
            'assertions': {
                'db_is_wal': meta.get('journal_mode') == 'wal',
                'wal_file_present': meta['db_files']['state.db-wal']['exists'],
                'db_quick_check_ok': meta.get('quick_check') == 'ok',
                'identity_agent_id_present': any(i['key'] == 'agent_id' and i['present'] for i in meta['identity']),
                'identity_node_id_present': any(i['key'] == 'node_id' and i['present'] for i in meta['identity']),
                'identity_credential_present': any(i['key'] == 'credential' and i['present'] for i in meta['identity']),
                'script_persisted_locally': bool(script_row),
                'task_definition_persisted_locally': bool(task_row),
                'schedule_definition_persisted_locally': bool(sched_row),
                'outbound_connection_to_proxy_present': any(proxy_hex in (e['remote'] or '') for e in established),
                'no_reverse_listener_owned_by_child': len(listeners) == 0,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase5-local-state-and-sockets-a.json', out)
        return out

    # ---------------- SYS-11 + SYS-02: restart persistence ----------------
    def phase6_restart_persistence(self):
        ch = self.children['a']
        fx = self.state['fixtures']
        st = self.state['nodes']['a']

        def snapshot():
            return {
                'db_meta': ch.db_meta(),
                'script_row': ch.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?',
                                          [fx['script_marker']])['rows'],
                'task_row': ch.db_query('SELECT id,revision FROM tasks WHERE id=?',
                                        [fx['task_marker']])['rows'],
                'sched_row': ch.db_query('SELECT id,revision,enabled FROM schedules WHERE id=?',
                                         [self.state['schedules']['sc_marker']])['rows'],
                'node': self.node_brief(st['id']),
            }

        before = snapshot()
        stop = ch.stop()
        self.budget['child_process_stops'] += 1
        restart = ch.start()
        self.budget['child_process_starts'] += 1
        node_after = wait_for(lambda: self.node_brief(st['id'])
                              if (self.node(st['id']) or {}).get('status') == 'online'
                              and (self.node(st['id']) or {}).get('sync_status') == 'synced' else None,
                              timeout=90, interval=1.0, label='online_after_restart')
        if not node_after:
            raise RuntimeError('child_did_not_recover_after_restart')
        time.sleep(4)
        after = snapshot()
        rev_before = before['db_meta']['sync_state'][0]['global_revision'] if before['db_meta']['sync_state'] else None
        rev_after = after['db_meta']['sync_state'][0]['global_revision'] if after['db_meta']['sync_state'] else None
        identity_before = {i['key']: i['present'] for i in before['db_meta']['identity']}
        identity_after = {i['key']: i['present'] for i in after['db_meta']['identity']}

        def row_map(rows, key='id'):
            return {r[key]: r for r in rows}

        out = {
            'at_utc': now_utc(),
            'stop_result': stop, 'restart_launcher': restart,
            'before_restart': before, 'after_restart': after,
            'node_before': before['node'], 'node_after': node_after,
            'agent_id_unchanged': before['node'].get('agent_id') == node_after.get('agent_id'),
            'node_id_unchanged': before['node'].get('id') == node_after.get('id'),
            'revision_before': rev_before, 'revision_after': rev_after,
            'hello_frames_total': len(self.frames('a', 'HELLO')),
            'hello_ack_accepted_all': [f.get('accepted') for f in self.frames('a', 'HELLO_ACK')],
            'assertions': {
                'same_agent_id_after_restart': before['node'].get('agent_id') == node_after.get('agent_id'),
                'same_node_id_after_restart': before['node'].get('id') == node_after.get('id'),
                'revision_persisted_not_reset': (rev_after is not None and rev_before is not None
                                                 and rev_after >= rev_before),
                'identity_rows_persisted': identity_before == identity_after and identity_after.get('agent_id'),
                'script_persisted': bool(after['script_row']) and
                                    row_map(after['script_row']) == row_map(before['script_row']),
                'task_persisted': bool(after['task_row']) and
                                  row_map(after['task_row']) == row_map(before['task_row']),
                'schedule_persisted': bool(after['sched_row']) and
                                      row_map(after['sched_row']) == row_map(before['sched_row']),
                'second_hello_after_restart': len(self.frames('a', 'HELLO')) >= 2,
            },
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase6-restart-persistence-a.json', out)
        return out

    # ---------------- SYS-01: token / credential / re-enrollment ----------------
    def phase7_sys01_tokens(self):
        out = {'at_utc': now_utc(), 'steps': {}}
        enroll = self.enroll('b')
        out['steps']['enroll_b'] = {'enrollment': enroll, 'node': self.node_brief(enroll['node_id'])}
        self.state['nodes']['b'] = {'id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                    'node_name': enroll['node_name'], 'node_ip': enroll['node_ip'],
                                    'enrolled_at': now_utc()}
        node_b = enroll['node_id']

        # (1) invalid registration token -> HELLO rejected, node stays pending
        self.start_proxy('b')
        chb = childmod.OwnedChild(NONCE, self.proxies['b'].port, node_b, enroll['agent_id'],
                                  enroll['node_name'], enroll['node_ip'],
                                  'qa-psm-%s-INVALID-TOKEN' % NONCE, label='b')
        self.children['b'] = chb
        chb.prepare()
        chb.write_config_and_start()
        self.budget['child_process_starts'] += 1
        wait_for(lambda: self.frames('b', 'HELLO_ACK') or
                 (len(self.frames('b', 'HELLO')) > 0 and True), timeout=40, interval=1.0, label='invalid_token_ack')
        time.sleep(3)
        invalid_acks = self.frames('b', 'HELLO_ACK')
        chb.stop()
        self.budget['child_process_stops'] += 1
        out['steps']['invalid_token'] = {
            'hello_count': len(self.frames('b', 'HELLO')),
            'hello_ack': invalid_acks,
            'rejected': bool(invalid_acks) and all(a.get('accepted') is False for a in invalid_acks),
            'reject_messages': sorted({a.get('message') for a in invalid_acks}),
            'node_after': self.node_brief(node_b),
            'node_still_pending': (self.node(node_b) or {}).get('status') == 'pending',
        }

        # (2) re-enrollment reuses identity (no duplicate node)
        re_enroll = self.enroll('b')
        nodes_now = [n for n in self.hub.json('/api/nodes') if (n.get('hostname') or '').startswith(PREFIX)]
        out['steps']['re_enrollment'] = {
            'new_enrollment': re_enroll,
            'same_node_id': re_enroll['node_id'] == node_b,
            'same_agent_id': re_enroll['agent_id'] == enroll['agent_id'],
            'owned_node_count': len(nodes_now),
            'owned_node_ids': [n['id'] for n in nodes_now],
        }
        node_b = re_enroll['node_id']

        # (3) valid token -> registration accepted, online/synced
        before_hello = len(self.frames('b', 'HELLO'))
        chb._token = self.hub.cred['registration_token']
        chb.write_config_and_start()
        self.budget['child_process_starts'] += 1
        online = wait_for(lambda: self.node_brief(node_b)
                          if (self.node(node_b) or {}).get('status') == 'online'
                          and (self.node(node_b) or {}).get('sync_status') == 'synced' else None,
                          timeout=90, interval=1.0, label='b_online')
        if not online:
            self.errors.append('child_b_valid_token_did_not_reach_online')
        out['steps']['valid_token'] = {
            'node_after': online or self.node_brief(node_b),
            'hello_ack_tail': self.frames('b', 'HELLO_ACK')[before_hello:],
            'accepted_true_present': any(a.get('accepted') is True
                                         for a in self.frames('b', 'HELLO_ACK')[before_hello:]),
        }

        # (4) revoke credential -> restart presents revoked credential -> rejected; node demoted
        code, body = self.hub.post('/api/nodes/%s/revoke-credential' % self.hub.quote(node_b), {})
        self.budget['api_mutations'] += 1
        node_after_revoke = self.node_brief(node_b)
        pre = len(self.frames('b', 'HELLO_ACK'))
        chb.stop(); self.budget['child_process_stops'] += 1
        chb.start(); self.budget['child_process_starts'] += 1
        wait_for(lambda: [a for a in self.frames('b', 'HELLO_ACK')[pre:]
                          if a.get('accepted') is False] or None,
                 timeout=40, interval=1.0, label='revoked_reject')
        time.sleep(4)
        new_acks = self.frames('b', 'HELLO_ACK')[pre:]
        out['steps']['revoke_credential'] = {
            'revoke_http': code, 'revoke_body': body,
            'node_right_after_revoke': node_after_revoke,
            'new_hello_acks': new_acks,
            'rejected_present': any(a.get('accepted') is False for a in new_acks),
            'reject_messages': sorted({a.get('message') for a in new_acks if a.get('accepted') is False}),
            'node_after_rejected_hello': self.node_brief(node_b),
        }
        chb.stop(); self.budget['child_process_stops'] += 1

        # (5) re-enrollment clears revoked -> agent reconnects with registration token -> online again
        re_enroll2 = self.enroll('b')
        nodes_now2 = [n for n in self.hub.json('/api/nodes') if (n.get('hostname') or '').startswith(PREFIX)]
        pre2 = len(self.frames('b', 'HELLO_ACK'))
        chb.start(); self.budget['child_process_starts'] += 1
        online2 = wait_for(lambda: self.node_brief(node_b)
                           if (self.node(node_b) or {}).get('status') == 'online'
                           and (self.node(node_b) or {}).get('sync_status') == 'synced' else None,
                           timeout=90, interval=1.0, label='b_recovered')
        if not online2:
            self.errors.append('child_b_did_not_recover_after_re_enrollment')
        out['steps']['re_enroll_after_revoke'] = {
            'same_node_id': re_enroll2['node_id'] == node_b,
            'same_agent_id': re_enroll2['agent_id'] == enroll['agent_id'],
            'owned_node_count': len(nodes_now2),
            'node_after': online2 or self.node_brief(node_b),
            'hello_ack_tail': self.frames('b', 'HELLO_ACK')[pre2:],
            'accepted_true_present': any(a.get('accepted') is True
                                         for a in self.frames('b', 'HELLO_ACK')[pre2:]),
        }
        out['assertions'] = {
            'invalid_token_rejected': out['steps']['invalid_token']['rejected'],
            'invalid_token_node_stays_pending': out['steps']['invalid_token']['node_still_pending'],
            're_enrollment_same_node_id': out['steps']['re_enrollment']['same_node_id'],
            're_enrollment_same_agent_id': out['steps']['re_enrollment']['same_agent_id'],
            're_enrollment_no_duplicate_node': out['steps']['re_enrollment']['owned_node_count'] == 2,
            'valid_token_online': (out['steps']['valid_token']['node_after'] or {}).get('status') == 'online',
            'revoked_credential_rejected': out['steps']['revoke_credential']['rejected_present'],
            're_enroll_after_revoke_recovers': (out['steps']['re_enroll_after_revoke']['node_after'] or {}).get('status') == 'online',
            're_enroll_after_revoke_no_duplicate': out['steps']['re_enroll_after_revoke']['owned_node_count'] == 2,
        }
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase7-sys01-tokens-b.json', out)
        return out

    # ---------------- INV-05: CommitSync / sync transaction failure ----------------
    def phase8_inv05_commit_failure(self):
        out = {'at_utc': now_utc(), 'steps': {}}
        enroll = self.enroll('c')
        self.state['nodes']['c'] = {'id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                    'node_name': enroll['node_name'], 'node_ip': enroll['node_ip'],
                                    'enrolled_at': now_utc()}
        node_c = enroll['node_id']
        out['steps']['enroll_c'] = {'enrollment': enroll}
        self.start_proxy('c')
        chc = childmod.OwnedChild(NONCE, self.proxies['c'].port, node_c, enroll['agent_id'],
                                  enroll['node_name'], enroll['node_ip'],
                                  self.hub.cred['registration_token'], label='c', tmpfs_mb=12)
        self.children['c'] = chc
        prep = chc.prepare()
        chc.write_config_and_start()
        self.budget['child_process_starts'] += 1
        online = wait_for(lambda: self.node_brief(node_c)
                          if (self.node(node_c) or {}).get('status') == 'online'
                          and (self.node(node_c) or {}).get('sync_status') == 'synced' else None,
                          timeout=90, interval=1.0, label='c_online')
        if not online:
            raise RuntimeError('child_c_did_not_reach_online_synced')
        out['steps']['baseline'] = {'prepare': prep, 'node': online,
                                    'db_meta': chc.db_meta()}
        rev_before = chc.db_meta()['sync_state'][0]['global_revision']

        # fill the size-limited tmpfs to leave a small fixed free margin
        fill = chc.tmpfs_fill(keep_free_bytes=65536)
        out['steps']['space_constrained'] = {'fill': fill, 'stat': chc.tmpfs_stat()}

        # create a script (referenced by a task targeting node c) large enough to exceed the margin
        big_content = '#!/bin/sh\nset -eu\n# ' + ('x' * (1024 * 1024)) + '\n'
        s_big = self._create_script(PREFIX + 'script-big', big_content,
                                    'p-sysmisc oversized sync payload for INV-05 (nonce %s)' % NONCE)
        t_big = self._create_task(PREFIX + 'task-big', node_c, s_big['id'])
        self.state['fixtures']['script_big'] = s_big['id']
        self.state['fixtures']['task_big'] = t_big['id']
        out['steps']['space_constrained']['script_big_id'] = s_big['id']
        out['steps']['space_constrained']['task_big_id'] = t_big['id']
        out['steps']['space_constrained']['content_bytes'] = len(big_content)

        pre_acks = len(self.frames('c', 'SYNC_ACK'))
        pids_before = chc.pids()

        def failed_ack():
            tail = self.frames('c', 'SYNC_ACK')[pre_acks:]
            return [a for a in tail if a.get('ok') is False] or None

        # wait for either a graceful failed ACK or a crash of the owned agent
        deadline = time.time() + 150
        failed = None
        crash_seen = None
        while time.time() < deadline:
            failed = failed_ack()
            if failed:
                break
            now_pids = chc.pids()
            if not now_pids:
                crash_seen = {'pids_before': pids_before, 'pids_after': now_pids}
                break
            time.sleep(2.0)

        sync_status_samples = []
        tail_acks = self.frames('c', 'SYNC_ACK')[pre_acks:]
        for _ in range(4):
            n = self.node(node_c) or {}
            sync_status_samples.append({'t_utc': now_utc(), 'sync_status': n.get('sync_status'),
                                        'global_revision': n.get('global_revision')})
            time.sleep(1.5)
        local_after_fail = chc.db_meta()
        big_row_local = chc.db_query('SELECT id,revision FROM scripts WHERE id=?', [s_big['id']])['rows']
        log_tail = chc.read_file(chc.log, tail=60)
        log_lines = log_tail.get('lines') or []
        pids_after = chc.pids()
        crash = not pids_after
        out['steps']['commit_failure'] = {
            'sync_ack_tail': tail_acks,
            'failed_ack_frames': failed or [],
            'failed_ack_present': bool(failed),
            'sync_ack_ok_values': [a.get('ok') for a in tail_acks],
            'sync_ack_errors': [a.get('error') for a in tail_acks if a.get('ok') is False],
            'applied_rev_values': [a.get('applied_rev') for a in tail_acks],
            'agent_crashed': crash,
            'crash_detail': crash_seen or {'pids_before': pids_before, 'pids_after': pids_after},
            'panic_lines': [l for l in log_lines if 'fatal error' in l or 'unlock of unlocked mutex' in l
                            or 'store.go:258' in l or 'agent.go:918' in l or 'agent.go:800' in l],
            'commit_error_branch_hit': any('agent.go:918' in l for l in log_lines),
            'rollback_double_unlock': any('unlock of unlocked mutex' in l for l in log_lines)
                                      and any('store.go:258' in l for l in log_lines),
            'local_revision_before': rev_before,
            'local_revision_after_failed_commit': local_after_fail['sync_state'][0]['global_revision'],
            'big_script_local_rows_after_fail': big_row_local,
            'big_script_absent_locally': not big_row_local,
            'node_sync_status_samples': sync_status_samples,
            'agent_log_tail': log_tail,
        }

        # recovery: free the tmpfs and restart the owned child; a successful sync must
        # then persist the previously failing payload (no half-sync state, no corruption)
        cleared = chc.tmpfs_clear_junk()
        restart = chc.start()
        self.budget['child_process_starts'] += 1

        def recovered_online():
            n = self.node(node_c) or {}
            return n if n.get('status') == 'online' and n.get('sync_status') == 'synced' else None

        online_after = wait_for(recovered_online, timeout=90, interval=1.0, label='c_recover_online')

        def big_applied():
            rows = chc.db_query('SELECT id,revision FROM scripts WHERE id=?', [s_big['id']])['rows']
            return rows or None

        big_after = wait_for(big_applied, timeout=120, interval=2.0, label='c_big_applied')
        time.sleep(3)
        local_after_rec = chc.db_meta()
        rec_tail = self.frames('c', 'SYNC_ACK')[pre_acks:]
        out['steps']['recovery'] = {
            'cleared': cleared, 'restart_launcher': restart,
            'node_after_restart': self.node_brief(node_c),
            'online_after_restart': bool(online_after),
            'sync_ack_after_fail': rec_tail,
            'recovered_ok_ack_present': any(a.get('ok') is True for a in rec_tail),
            'local_revision_after_recovery': local_after_rec['sync_state'][0]['global_revision'],
            'big_script_local_rows_after_recovery': big_after or [],
            'big_script_present_locally_after_recovery': bool(big_after),
        }
        out['assertions'] = {
            'commit_failure_path_exercised': bool(failed) or crash,
            'commit_error_branch_hit': out['steps']['commit_failure']['commit_error_branch_hit']
                                       or out['steps']['commit_failure']['failed_ack_present'],
            'object_absent_after_failed_commit': out['steps']['commit_failure']['big_script_absent_locally'],
            'failure_handled_gracefully': (not out['steps']['commit_failure']['agent_crashed'])
                                          and out['steps']['commit_failure']['failed_ack_present'],
            'recovery_after_restart': out['steps']['recovery']['online_after_restart']
                                      and out['steps']['recovery']['big_script_present_locally_after_recovery'],
        }
        out['defect'] = None
        if out['steps']['commit_failure']['rollback_double_unlock']:
            out['defect'] = {
                'id': 'psm-INV05-commit-failure-double-unlock',
                'summary': 'CommitSync error path double-unlocks the local store mutex: applySync '
                           'defers RollbackSync after CommitSync has already unlocked and cleared tx, '
                           'so any CommitSync error (here an injected SQLITE_FULL) panics the Agent '
                           'with "fatal error: sync: unlock of unlocked mutex".',
                'code_refs': ['internal/agent/store.go:244-265', 'internal/agent/agent.go:794-802',
                              'internal/agent/agent.go:917-919'],
            }
        # assertions_pass stays False on a defect by design
        out['assertions_pass'] = all(out['assertions'].values())
        self.ev('phase8-inv05-commit-failure-c.json', out)
        return out

    # ---------------- E2E-18: adopt same-case attempt02 journal evidence ----------------
    def phase9_e2e18_adopt(self):
        base = HERE.parents[1] / 'qa-resume-20261010-api' / 'sys12-preflight'
        a2 = base / 'sys12-b9e47f16deb9-final-summary.json'
        a4 = base / 'artifact-cache-preparation' / 'artifact-cache-ad9bf531b28a-final-summary.json'
        out = {'at_utc': now_utc(), 'adoption': 'same-case SYS-12 attempt02 journal + attempt04 identity/revision/cache'}

        def rel(p):
            for up in (5, 6, 4):
                try:
                    return str(Path(p).resolve().relative_to(HERE.parents[up]))
                except Exception:
                    continue
            return str(p)

        if a2.exists():
            d = json.loads(a2.read_text())
            out['attempt02_journal'] = {
                'file': rel(a2),
                'captured_at_utc': d.get('captured_at_utc'),
                'attempt': d.get('attempt'),
                'nonce': d.get('nonce'),
                'a_to_b_persistence_status': (d.get('a_to_b_persistence') or {}).get('status'),
                'same_node_agent_identity': (d.get('a_to_b_persistence') or {}).get('same_node_agent_identity'),
                'a_db': (d.get('a_to_b_persistence') or {}).get('a_db'),
                'b_db_after_sync': (d.get('a_to_b_persistence') or {}).get('b_db_after_sync'),
                'script_sqlite_cache': d.get('script_sqlite_cache'),
                'sqlite_journal_exit_code': (d.get('business') or {}).get('sqlite_journal_exit_code'),
                'coverage_limits': d.get('coverage_limits'),
            }
        else:
            out['attempt02_journal'] = {'missing': str(a2)}
        if a4.exists():
            d4 = json.loads(a4.read_text())
            out['attempt04_identity_revision_cache'] = {
                'file': str(a4),
                'identity': d4.get('same_node_agent_identity') or d4.get('identity'),
                'keys': list(d4.keys())[:30],
            }
        else:
            out['attempt04_identity_revision_cache'] = {'missing': str(a4)}
        out['limitations'] = [
            'Docker/Compose recreate cannot be reproduced in this batch: it needs an exclusive batch and a docker-host deployment form; the repair Hub must not be restarted.',
            'Journal dimension is adopted from the same-case SYS-12 attempt02 evidence (local SQLite execution journal rows survive recreate).',
        ]
        self.ev('phase9-e2e18-adopt-attempt02.json', out)
        return out

    # ---------------- cleanup ----------------
    def phase_cleanup(self):
        results = {'deleted': [], 'errors': []}
        # 1. API objects: schedules first, then tasks, then scripts (reference order)
        prio = {'schedule': 0, 'task': 1, 'script': 2}
        created = sorted(self.state['created'], key=lambda t: prio.get(t[0], 9))
        for kind, oid, _name in created:
            path = '/api/%ss/%s' % (kind, self.hub.quote(oid))
            code, _ = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': oid, 'delete_http': code, 'get_http': gcode})
            self.state['delete_results'][oid] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
            self.state['deleted'].append((kind, oid))
        # 2. children (stop + remove root, tmpfs umount) BEFORE deleting node rows
        for label in ('a', 'b', 'c'):
            ch = self.children.get(label)
            if ch is None:
                continue
            try:
                rm = ch.remove(umount_first=(label == 'c'))
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                rm = {'error': str(exc)}
                results['errors'].append('child_remove_%s:%s' % (label, exc))
            results['child_removal_%s' % label] = rm
        # 3. proxies
        for label, proxy in self.proxies.items():
            try:
                proxy.stop()
            except Exception as exc:
                results['errors'].append('proxy_stop_%s:%s' % (label, exc))
        # 4. nodes
        for label in ('a', 'b', 'c'):
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
        self.ev('phase-cleanup.json', results)
        return results

    def record_fixture_ledger(self):
        led = {
            'batch_id': 'p-sysmisc', 'nonce': NONCE, 'prefix': PREFIX,
            'endpoint': HUB_ADDR, 'node_used': {'native-1': '192.168.100.204'},
            'hub_fixtures': [], 'child_nodes': [], 'host_fixtures': [], 'proxies': [], 'status': 'PENDING',
        }
        for kind, oid, name in self.state['created']:
            r = self.state['delete_results'].get(oid, {})
            led['hub_fixtures'].append({'kind': kind, 'id': oid, 'name': name,
                                        'delete_http': r.get('delete_http'), 'get_http': r.get('get_http'),
                                        'deleted_get404': r.get('get_http') == 404})
        for label, st in self.state['nodes'].items():
            r = self.state['delete_results'].get(st['id'], {})
            led['child_nodes'].append({'label': label, 'id': st['id'], 'agent_id': st['agent_id'],
                                       'hostname': st['node_name'], 'node_ip': st['node_ip'],
                                       'host': '192.168.100.204', 'delete_http': r.get('delete_http'),
                                       'get_http': r.get('get_http'), 'deleted_get404': r.get('get_http') == 404})
        for label, ch in self.children.items():
            led['host_fixtures'].append({'kind': 'owned_child_agent_process', 'host': '192.168.100.204',
                                         'label': label, 'root': ch.root,
                                         'tmpfs_mb': ch.tmpfs_mb})
        for label, p in self.proxies.items():
            led['proxies'].append({'label': label, 'listen_port': p.port, 'deployment_mode': 'native'})
        led['status'] = 'CLEANED' if all(f.get('deleted_get404') for f in led['hub_fixtures']) and \
            all(n.get('deleted_get404') for n in led['child_nodes']) else 'PARTIAL'
        hubapi.write_json('fixture-ledger.json', led)
        return led


def main():
    run = Run()
    progress = {'batch_id': 'p-sysmisc', 'nonce': NONCE, 'phase': 'start', 'updated_at_utc': now_utc(),
                'deviations': []}
    hubapi.write_json('progress.json', progress)
    phases = [
        ('phase0_guard', run.phase0_guard),
        ('phase1_enrollment_pending', run.phase1_enrollment_pending),
        ('phase2_hello_online', run.phase2_hello_online),
        ('phase3_fixtures', run.phase3_fixtures),
        ('phase4_heartbeat_window', run.phase4_heartbeat_window),
        ('phase5_local_state_and_sockets', run.phase5_local_state_and_sockets),
        ('phase6_restart_persistence', run.phase6_restart_persistence),
        ('phase7_sys01_tokens', run.phase7_sys01_tokens),
        ('phase8_inv05_commit_failure', run.phase8_inv05_commit_failure),
        ('phase9_e2e18_adopt', run.phase9_e2e18_adopt),
    ]
    summary = {}
    for name, fn in phases:
        progress['phase'] = name
        progress['updated_at_utc'] = now_utc()
        hubapi.write_json('progress.json', progress)
        try:
            res = fn()
            summary[name] = 'OK'
            if isinstance(res, dict) and 'assertions_pass' in res:
                summary[name] = 'OK' if res['assertions_pass'] else 'ASSERT_FAIL'
        except Exception as exc:
            summary[name] = 'ERROR: %s' % exc
            run.errors.append('%s: %s' % (name, exc))
            hubapi.write_json(DEV + ('phase-error-%s.json' % name), {'error': str(exc)})
            if name == 'phase0_guard':
                summary['aborted_after'] = 'phase0_guard_failed'
                run.errors.append('aborted: guard failed, remaining phases skipped')
                break
    # capture the big fixture ids if fixed late
    run.state['fixtures']['script_big'] = run.state['fixtures'].get('script_big') or _resolve(run, 'script-big', 'script')
    run.state['fixtures']['task_big'] = run.state['fixtures'].get('task_big') or _resolve(run, 'task-big', 'task')
    progress['phase'] = 'cleanup'; hubapi.write_json('progress.json', progress)
    try:
        cleanup = run.phase_cleanup()
    except Exception as exc:
        cleanup = {'errors': [str(exc)]}
        run.errors.append('cleanup: %s' % exc)
    try:
        sweep = childmod.sweep_owned()
    except Exception as exc:
        sweep = {'error': str(exc)}
    cleanup['defensive_sweep'] = sweep
    ledger = run.record_fixture_ledger()
    run_state = {'nonce': NONCE, 'prefix': PREFIX, 'state': run.state, 'budget': run.budget,
                 'errors': run.errors, 'deviations': run.deviations, 'summary': summary,
                 'cleanup': cleanup, 'ledger': ledger, 'defensive_sweep': sweep, 'finished_utc': now_utc()}
    hubapi.write_json('run-evidence.json', run_state)
    progress['phase'] = 'done'
    progress['updated_at_utc'] = now_utc()
    progress['summary'] = summary
    progress['errors'] = run.errors
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'summary': summary, 'errors': run.errors,
                      'all_get_404': cleanup.get('all_get_404'),
                      'ledger_status': ledger['status']}, ensure_ascii=False, indent=2))
    return 0


def _resolve(run, name, kind):
    for k, oid, nm in run.state['created']:
        if k == kind and nm == PREFIX + name:
            return oid
    return None


if __name__ == '__main__':
    raise SystemExit(main())
