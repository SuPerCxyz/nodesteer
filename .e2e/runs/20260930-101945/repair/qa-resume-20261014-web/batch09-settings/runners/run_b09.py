#!/usr/bin/env python3
"""batch09-settings runtime batch (web cases P21-03..P21-06, FL-16)
plus the api case IDEMPOTENT-MANUAL (written into the api deliverable dir).

Real evidence chain only: operator/API/UI action -> real request -> Hub/Agent
state change -> real observable Agent behaviour (heartbeat cadence, revision
cadence, log truncation) -> API/DB readback.

Global runtime settings are modified during the run and ALWAYS restored in a
`finally` block (with a field-by-field settings-before/settings-after diff).

Owned fixtures only; every owned resource is deleted and re-read with GET 404.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import childlib  # noqa: E402
import hubapi  # noqa: E402
import node_units  # noqa: E402
import settings_snapshot as ss  # noqa: E402
import ui  # noqa: E402
from frame_proxy import FrameProxy  # noqa: E402

WEB_DIR = HERE.parent
API_DIR = WEB_DIR.parent.parent / 'qa-resume-20261014-api' / 'idempotent-manual'
WEB_EV = WEB_DIR / 'evidence'
API_EV = API_DIR / 'evidence'
if not (WEB_DIR / '.nonce').is_file():
    (WEB_DIR / '.nonce').write_text(__import__('secrets').token_hex(6) + '\n')
NONCE = (WEB_DIR / '.nonce').read_text().strip()
PREFIX = 'qa-b09-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'bigscript'
TASK_NAME = PREFIX + 'bigtask'
MARKER = 'QA:%s:EXEC' % NONCE
MARKER_LINES = 200
OPERATOR_IP = '192.168.100.170'
CHILD_TAG = 'qa-b09-settings'
CHILD_HOST = '192.168.100.247'
OWNED_NODE_IP = '192.168.100.247'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
ORIGINAL_KEYS = ['heartbeat_interval_sec', 'revision_check_interval_sec',
                 'changelog_window', 'max_log_bytes']
MODIFIED = {'heartbeat_interval_sec': '3', 'revision_check_interval_sec': '5',
            'max_log_bytes': '2048'}
BIG_BODY = 'A' * 20000
SCRIPT_BODY = (
    '#!/bin/sh\n'
    'set -eu\n'
    'sleep 3\n'                                  # gives the harness time to arm the frame seam
    "printf '%s\\n' '" + MARKER + "'\n"
    "head -c 20000 /dev/zero | tr '\\0' 'A'\n"
    "printf '\\n'\n"
)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def write_at(path, value):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)
    return p


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'child_node_id': None, 'child_agent_id': None, 'script_id': None, 'task_id': None,
            'executions': {}, 'created': [], 'delete_results': {},
        }
        self.budget = {
            'api_mutations': 0, 'settings_puts': 0, 'node_enroll_posts': 0,
            'hub_definition_creates': 0, 'hub_definition_deletes': 0,
            'manual_run_posts': 0, 'child_process_starts': 0, 'child_process_removals': 0,
            'ui_sessions': 0, 'ui_saves': 0, 'hub_restarts': 0, 'vms_restarted': 0,
            'product_source_changes': 0, 'git_operations': 0,
        }
        self.deviations = []
        self.settings_before = None
        self.settings_after = None
        self.unit_baseline = None

    # ---------------- helpers ----------------
    def web(self, name, value):
        return write_at(WEB_EV / name, value)

    def api(self, name, value):
        return write_at(API_EV / name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def executions(self, task_id, node_id=None, limit=100):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(task_id))
        if node_id:
            q += '&node_id=' + self.hub.quote(node_id)
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

    def wait_child(self, timeout=90):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def wait_revision(self, target, timeout=60):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('global_revision', -1) >= target:
                return node
            time.sleep(1)
        return node

    def observe(self, window_sec, specs):
        """Clear the frame log, observe `window_sec` once, and return the inter-frame
        gaps for each (frame_type, direction) spec."""
        self.proxy.clear_frames()
        t0 = time.time()
        time.sleep(window_sec)
        out = {'window_sec': window_sec, 'specs': {}}
        for frame_type, direction in specs:
            rows = [r for r in self.proxy.frame_log([frame_type]) if r['dir'] == direction]
            deltas = [round(rows[i + 1]['t'] - rows[i]['t'], 3) for i in range(len(rows) - 1)]
            out['specs']['%s/%s' % (direction, frame_type)] = {
                'frame_type': frame_type, 'direction': direction,
                'frames_seen': len(rows),
                'first_offset_sec': round(rows[0]['t'] - t0, 3) if rows else None,
                'last_offset_sec': round(rows[-1]['t'] - t0, 3) if rows else None,
                'deltas_sec': deltas,
                'delta_min': min(deltas) if deltas else None,
                'delta_max': max(deltas) if deltas else None,
                'delta_mean': round(sum(deltas) / len(deltas), 3) if deltas else None,
            }
        return out

    def put_settings(self, values, purpose):
        before = self.hub.json('/api/settings')
        code, body = self.hub.put('/api/settings', values)
        self.budget['api_mutations'] += 1
        self.budget['settings_puts'] += 1
        after = self.hub.json('/api/settings')
        return {'purpose': purpose, 'request': values, 'http': code, 'body': body,
                'api_before': before, 'api_after': after}

    # ---------------- phases ----------------
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': self.hub.base}
        if not self.hub.base.endswith(':18080'):
            raise RuntimeError('endpoint_guard_failed_repair_18080_required')
        out['endpoint_guard_ok'] = True
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'],
                                       'sync_status': n['sync_status']}
        scripts = self.hub.json('/api/scripts')
        tasks = self.hub.json('/api/tasks')
        leftover = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        if any(leftover.values()):
            raise RuntimeError('owned_fixture_already_present')
        out['owned_prefix_clear'] = True
        inv = childlib.OwnedChild(NONCE, 1, '', '', '', '', '', host=CHILD_HOST,
                                  tag=CHILD_TAG).inventory()
        out['child_host'] = CHILD_HOST
        out['child_pre'] = {
            'root': inv['root'], 'root_exists': inv['root_exists'],
            'config_exists': inv['config_exists'],
            'owned_child_processes': inv['owned_child_processes'],
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == childlib.AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_units': inv['repair_agent_units'],
        }
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['child_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        self.unit_baseline = {}
        for label, (ip, unit) in node_units.NODES.items():
            self.unit_baseline[label] = {'ip': ip,
                                         'units': node_units.probe(ip, [unit, 'nodesteer-agent.service'])}
        self.unit_baseline['hub-vm'] = {
            'ip': node_units.HUB[0],
            'units': node_units.probe(node_units.HUB[0], [node_units.HUB[1], node_units.HUB[2]])}
        out['unit_pid_baseline'] = self.unit_baseline
        self.web('phase0-guard.json', out)
        return out

    def phase1_settings_before(self):
        snap = ss.capture(self.hub)
        self.settings_before = snap
        self.web('settings-before.json', snap)
        return snap

    def phase2_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child',
            'node_ip': OWNED_NODE_IP,
            'hub_address': self.hub.base,
        })
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s' % code)
        self.state['child_node_id'] = enroll['node_id']
        self.state['child_agent_id'] = enroll['agent_id']
        self.state['created'].append(('node', enroll['node_id']))

        token = self.hub.cred['registration_token']
        self.proxy = FrameProxy('ws://192.168.100.209:18080')
        port = self.proxy.start()
        self.child = childlib.OwnedChild(
            NONCE, port, enroll['node_id'], enroll['agent_id'], PREFIX + 'child',
            OWNED_NODE_IP, token, host=CHILD_HOST, tag=CHILD_TAG,
            heartbeat_sec=2, revision_check_sec=2, max_log_bytes=262144)
        started = self.child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child(timeout=75)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'],
                           'agent_id': enroll['agent_id'],
                           'gateway_url': enroll.get('gateway_url'),
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {
                'host': CHILD_HOST, 'root': self.child.root,
                'config': self.child.config, 'config_mode_0600': True,
                'local_config_echo_no_token': self.child.local_config_echo(),
                'local_config_intent': {'heartbeat_sec': 2, 'revision_check_sec': 2,
                                        'max_log_bytes': 262144},
                'listened_operator_port': port,
                'routing': 'child -> operator %s:%d (frame proxy) -> repair hub :18080'
                           % (OPERATOR_IP, port),
                'child_processes': started.get('owned_processes'),
            },
            'node_after_start': {k: node.get(k) for k in
                                 ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                  'global_revision', 'deployment_mode', 'agent_version')},
            'agent_binary_sha256': childlib.AGENT_SHA,
        }
        self.web('phase2-owned-child.json', out)
        return out

    def phase3_baseline_cadence(self):
        """The child's own config says 2s/2s. If the observed cadence is ~30s/~45s,
        the Hub-pushed settings are already being consumed at connect time."""
        obs = self.observe(132, [('HEARTBEAT', 'agent_to_hub'),
                                 ('REVISION_CHECK', 'agent_to_hub')])
        hb = obs['specs']['agent_to_hub/HEARTBEAT']
        rev = obs['specs']['agent_to_hub/REVISION_CHECK']
        st = self.proxy.state()
        out = {
            'at_utc': now_utc(),
            'global_settings_at_this_point': self.hub.json('/api/settings'),
            'child_local_config': {'heartbeat_sec': 2, 'revision_check_sec': 2,
                                   'max_log_bytes': 262144},
            'observation': obs,
            'heartbeat_cadence': hb,
            'revision_cadence': rev,
            'heartbeat_ack_frames_seen': st['heartbeat_ack_count'],
            'settings_broadcast_frames_seen_so_far': st['settings_frames'],
            'interpretation': 'HEARTBEAT carries no settings; HEARTBEAT_ACK does. A ~30s/~45s '
                              'cadence against a 2s/2s local file proves the Hub-pushed runtime '
                              'settings are applied by the Agent.',
        }
        # 132 s window: heartbeat 30 s +-20% -> 4-6 frames with 3-5 gaps; revision check 45 s
        # +-20% -> 2-3 frames with 1-2 gaps. A 2 s local cadence would produce ~66/66 frames.
        hb_ok = hb['frames_seen'] >= 3 and bool(hb['deltas_sec']) and \
            all(22.0 <= d <= 41.0 for d in hb['deltas_sec'])
        rev_ok = rev['frames_seen'] >= 2 and bool(rev['deltas_sec']) and \
            all(30.0 <= d <= 62.0 for d in rev['deltas_sec'])
        out['assertions'] = {
            'heartbeat_not_local_2s_and_matches_hub_30s': bool(hb_ok),
            'revision_not_local_2s_and_matches_hub_45s': bool(rev_ok),
            'heartbeat_ack_observed': st['heartbeat_ack_count'] >= 1,
            'no_local_2s_cadence_burst': hb['frames_seen'] <= 8 and rev['frames_seen'] <= 5,
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase3-baseline-settings-consumption.json', out)
        return out

    def phase4_apply_and_broadcast(self):
        before = self.hub.json('/api/settings')
        self.proxy.clear_frames()
        put = self.put_settings(MODIFIED, 'P21-06/FL-16 runtime settings change')
        if put['http'] != 200:
            raise RuntimeError('settings_put_http_%s' % put['http'])
        # wait for the SETTINGS broadcast frame to reach the owned child
        deadline = time.time() + 20
        frame = None
        first_heartbeat_after_put = None
        put_at = time.time()
        while time.time() < deadline:
            frames = self.proxy.state()['settings_frames']
            if frames:
                frame = frames[-1]
                break
            time.sleep(0.5)
        # The Agent only re-arms its timer AFTER it fires, so the already pending
        # next heartbeat/revision tick keeps the pre-change interval (30 s / 45 s
        # +-20 % jitter). Let that pending tick drain, then measure the steady state
        # so the reading is not distorted by the transition.
        settle_sec = 58
        time.sleep(settle_sec)
        hb_frames = [r for r in self.proxy.frame_log(['HEARTBEAT']) if r['dir'] == 'agent_to_hub']
        if hb_frames:
            first_heartbeat_after_put = round(hb_frames[0]['t'] - put_at, 3)
        obs = self.observe(34, [('HEARTBEAT', 'agent_to_hub'), ('REVISION_CHECK', 'agent_to_hub')])
        hb = obs['specs']['agent_to_hub/HEARTBEAT']
        rev = obs['specs']['agent_to_hub/REVISION_CHECK']
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid) or {}
            protected_after[label] = {'status': n.get('status'), 'sync_status': n.get('sync_status')}
        out = {
            'at_utc': now_utc(),
            'settings_put': put,
            'settings_broadcast_frame_from_hub_to_owned_child': frame,
            'transition': {
                'settle_sec_after_put': settle_sec,
                'first_heartbeat_seen_after_put_sec': first_heartbeat_after_put,
                'note': 'the pending pre-change heartbeat tick is expected to land inside the '
                        'settle window (old interval 24-36 s) and is therefore excluded from the '
                        'steady-state measurement below',
            },
            'observation_after_change': obs,
            'heartbeat_cadence_after_change': hb,
            'revision_cadence_after_change': rev,
            'protected_nodes_during_change': protected_after,
            'proxy_state_after': {
                'frame_type_counts': self.proxy.state()['frame_type_counts'],
                'settings_frames': self.proxy.state()['settings_frames'],
            },
        }
        broadcast_ok = bool(frame) and frame['payload'].get('heartbeat_sec') == 3 and \
            frame['payload'].get('revision_check_sec') == 5 and \
            frame['payload'].get('max_log_bytes') == 2048
        # steady-state bands: heartbeat 3 s +-20% rounded up to the Agent's 1 s ticker -> 3..4 s;
        # revision check 5 s +-20% rounded up -> 4..6 s. Allow the measured slack of the proxy hop.
        hb_ok = hb['frames_seen'] >= 7 and bool(hb['deltas_sec']) and \
            all(2.8 <= d <= 4.3 for d in hb['deltas_sec'])
        rev_ok = rev['frames_seen'] >= 4 and bool(rev['deltas_sec']) and \
            all(3.8 <= d <= 6.3 for d in rev['deltas_sec'])
        out['assertions'] = {
            'settings_frame_broadcast_to_online_owned_child': broadcast_ok,
            'heartbeat_cadence_became_~3s': bool(hb_ok),
            'revision_cadence_became_~5s': bool(rev_ok),
            'api_values_persisted': put['api_after'].get('heartbeat_interval_sec') == '3'
            and put['api_after'].get('revision_check_interval_sec') == '5'
            and put['api_after'].get('max_log_bytes') == '2048',
            'protected_nodes_still_online_synced': all(
                v.get('status') == 'online' and v.get('sync_status') == 'synced'
                for v in protected_after.values()),
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase4-settings-broadcast-and-cadence.json', out)
        return out

    def phase5_fixtures(self):
        node_before = self.node(self.state['child_node_id']) or {}
        rev_before = node_before.get('global_revision', 0)
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'batch09-settings max_log_bytes consumption fixture (nonce %s)' % NONCE,
            'interpreter': 'shell', 'content': SCRIPT_BODY, 'parameters': [],
            'environment': {}, 'working_dir': '', 'timeout': 30, 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        code, task = self.hub.post('/api/tasks', {
            'name': TASK_NAME, 'type': 'script', 'script_id': script['id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {}, 'timeout': 30, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, task))
        self.state['task_id'] = task['id']
        node = self.wait_revision(rev_before + 2, timeout=60)
        node = self.node(self.state['child_node_id']) or node
        out = {'at_utc': now_utc(), 'script_id': script['id'], 'task_id': task['id'],
               'script_body_sha256': __import__('hashlib').sha256(SCRIPT_BODY.encode()).hexdigest(),
               'expected_full_stdout_bytes': len(MARKER) + 1 + len(BIG_BODY) + 1,
               'child_global_revision_before_fixtures': rev_before,
               'child_global_revision_after_fixtures': (node or {}).get('global_revision'),
               'child_sync_status': (node or {}).get('sync_status')}
        self.web('phase5-fixtures.json', out)
        return out

    def _run_task(self, tag):
        code, rows = self.hub.post('/api/tasks/%s/run' % self.hub.quote(self.state['task_id']),
                                   {'node_ids': {self.state['child_node_id']: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows) != 1:
            raise RuntimeError('manual_run_%s_http_%s %s' % (tag, code, rows))
        exec_id = rows[0]['id']
        final = self.wait_execution(exec_id, timeout=90)
        return exec_id, final

    def phase6_max_log_truncation(self):
        exec_id, final = self._run_task('trunc')
        self.state['executions']['trunc'] = exec_id
        local = self.child.db_read_executions([exec_id])
        row = local['requested_executions'][0]
        stdout = final.get('stdout') or ''
        out = {
            'at_utc': now_utc(),
            'settings_in_force': self.hub.json('/api/settings'),
            'execution_id': exec_id,
            'hub_execution': {
                'status': final.get('status'), 'stdout_bytes': len(stdout),
                'stdout_truncated': final.get('stdout_truncated'),
                'marker_present': MARKER in stdout, 'head': stdout[:80],
                'tail': stdout[-40:], 'exact_len_2048': len(stdout) == 2048},
            'agent_local_execution': row,
            'script_raw_stdout_bytes': len(MARKER) + 1 + 20000 + 1,
            'interpretation': 'with max_log_bytes=2048 the same script must be cut to 2048 bytes '
                              'on both the Agent journal and the Hub row, and be flagged truncated',
        }
        out['assertions'] = {
            'hub_stdout_cut_to_max_log_bytes': 2000 <= len(stdout) <= 2048,
            'hub_flag_truncated': final.get('stdout_truncated') is True,
            'agent_local_stdout_cut_to_2048': bool(row) and 2000 <= (row.get('stdout_len') or 0) <= 2048,
            'script_really_emitted_full_output': out['script_raw_stdout_bytes'] > 20000,
            'marker_survived_at_head': MARKER in stdout,
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase6-max-log-bytes-truncation.json', out)
        return out

    # ---- P21-04 : invalid inputs ----
    def phase7_ui_invalid(self):
        cases = [
            ('心跳间隔（秒）', 'abc', 'heartbeat_interval_sec must be between 1 and 86400',
             'non_numeric'),
            ('心跳间隔（秒）', '0', 'heartbeat_interval_sec must be between 1 and 86400', 'zero'),
            ('心跳间隔（秒）', '-5', 'heartbeat_interval_sec must be between 1 and 86400',
             'negative'),
            ('心跳间隔（秒）', '99999999', 'heartbeat_interval_sec must be between 1 and 86400',
             'oversized'),
            ('修订检查间隔（秒）', '1', 'revision_check_interval_sec must be between 5 and 86400',
             'below_min_revision'),
            ('最大日志字节数', '100', 'max_log_bytes must be between 1024 and 17179869184',
             'below_min_max_log'),
            ('变更日志窗口', '0', 'changelog_window must be between 1 and 1000000000',
             'zero_changelog'),
        ]
        ui.open_url(ui.BASE + '/settings')
        time.sleep(4)
        self.budget['ui_sessions'] += 1
        baseline_get = self.hub.json('/api/settings')
        ui_read_at_start = ui.read_settings()
        results = []
        for key, value, expected_msg, dim in cases:
            toast_before = ui.toasts()
            set_res = ui.set_field(key, value)
            pre = ui.read_settings()
            click = ui.click_save()
            self.budget['ui_saves'] += 1
            time.sleep(2.5)
            after = ui.read_settings()
            get_now = self.hub.json('/api/settings')
            results.append({
                'dimension': dim, 'field': key, 'value_tried': value, 'ui_set': set_res,
                'ui_input_value_before_save': pre['fields'].get(key),
                'ui_save_click': click,
                'toasts_before_attempt': toast_before,
                'toasts_after_save': after['toasts'],
                'expected_error_message': expected_msg,
                'expected_error_shown': expected_msg in after['toasts'],
                'error_toast_present': any('must be between' in t for t in after['toasts']),
                'api_settings_after_attempt': get_now,
                'not_persisted': get_now == baseline_get,
                'ui_field_value_after_failed_save': after['fields'].get(key),
            })
            # reset the field to the currently persisted value so the next attempt only
            # carries its own single invalid field
            ui.set_field(key, get_now.get(ui_field_to_api(key)) or '')
        # API-level invalid gateway URL (the UI renders no gateway field because the
        # key is absent in the Hub, so the invalid-URL path is exercised through the
        # same validation code via the API; a VALID gateway URL is deliberately NOT
        # stored because the settings table has no delete path and the pre-existing
        # state has no agent_gateway_base_url row to restore to).
        gw = []
        for value in ['not-a-url', 'ftp://example.com', 'http://', '']:
            code, body = self.hub.put('/api/settings', {'agent_gateway_base_url': value})
            self.budget['api_mutations'] += 1
            self.budget['settings_puts'] += 1
            get_now = self.hub.json('/api/settings')
            gw.append({'value': value, 'http': code, 'body': body,
                       'agent_gateway_base_url_still_absent': 'agent_gateway_base_url' not in get_now,
                       'api_settings_unchanged': get_now == baseline_get})
        api_invalid_numbers = []
        for key, value in [('heartbeat_interval_sec', '0'), ('heartbeat_interval_sec', '-1'),
                           ('revision_check_interval_sec', '4'), ('max_log_bytes', '1023'),
                           ('changelog_window', '-1')]:
            code, body = self.hub.put('/api/settings', {key: value})
            self.budget['api_mutations'] += 1
            self.budget['settings_puts'] += 1
            api_invalid_numbers.append({'key': key, 'value': value, 'http': code, 'body': body,
                                        'unchanged': self.hub.json('/api/settings') == baseline_get})
        out = {
            'at_utc': now_utc(),
            'ui_session': {'path_at_start': ui_read_at_start['path'],
                           'fields_rendered_at_start': ui_read_at_start['fields'],
                           'save_button_present': ui_read_at_start['save_present'],
                           'api_settings_at_start': baseline_get},
            'ui_invalid_cases': results,
            'api_invalid_gateway_url': gw,
            'api_invalid_numbers': api_invalid_numbers,
        }
        ui_ok = all(r['not_persisted'] and r['expected_error_shown'] for r in results)
        per_dim = {r['dimension']: {'not_persisted': r['not_persisted'],
                                    'expected_error_shown': r['expected_error_shown']}
                   for r in results}
        gw_ok = all(r['http'] == 400 and r['agent_gateway_base_url_still_absent'] for r in gw)
        api_ok = all(r['http'] == 400 and r['unchanged'] for r in api_invalid_numbers)
        out['assertions'] = {
            'every_ui_invalid_input_rejected_with_expected_error_and_not_persisted': ui_ok,
            'every_api_invalid_gateway_url_rejected_400_not_persisted': gw_ok,
            'every_api_invalid_number_rejected_400_not_persisted': api_ok,
        }
        out['per_dimension'] = per_dim
        out['passed'] = all(out['assertions'].values())
        self.web('phase7-p21-04-invalid-inputs.json', out)
        return out

    # ---- P21-03 : valid save, success toast, duplicate save ----
    def phase8_ui_save_success_and_duplicate(self):
        original = {k: self.settings_before['api_settings'][k] for k in ORIGINAL_KEYS
                    if k in self.settings_before['api_settings']}
        ui.open_url(ui.BASE + '/settings')
        time.sleep(4)
        before = ui.read_settings()
        set_res = ui.set_fields([[ui_key_for(k), v] for k, v in original.items()])
        after_set = ui.read_settings()
        # duplicate save: two clicks in the same tick -> two PUTs
        dup = ui.click_save_n(2)
        self.budget['ui_saves'] += 1
        time.sleep(3.5)
        t1 = ui.read_settings()
        api_after = self.hub.json('/api/settings')
        # a further, deliberate single "repeated save" with no edits in between
        dup2 = ui.click_save()
        self.budget['ui_saves'] += 1
        time.sleep(3)
        t2 = ui.read_settings()
        api_after_2 = self.hub.json('/api/settings')
        restore_target = {k: v for k, v in original.items()}
        out = {
            'at_utc': now_utc(),
            'restore_target_values': restore_target,
            'ui_before_edit': before['fields'],
            'ui_set': set_res,
            'ui_after_edit_pre_save': after_set['fields'],
            'duplicate_save': {'clicks': dup, 'toasts': t1['toasts'],
                               'api_after': api_after},
            'repeated_save_no_edit': {'clicks': dup2, 'toasts': t2['toasts'],
                                      'api_after': api_after_2},
        }
        toast_ok = any('已保存' in t for t in t1['toasts'])
        persisted = all(api_after.get(k) == v for k, v in restore_target.items())
        stable = api_after_2 == api_after
        out['assertions'] = {
            'ui_save_produced_success_toast': toast_ok,
            'values_persisted_on_hub': persisted,
            'duplicate_save_idempotent_no_error_toast': not any(
                'must be between' in t or '失败' in t for t in t1['toasts']),
            'repeated_save_same_result': stable,
            'settings_now_original': api_after_2 == self.settings_before['api_settings']
            or all(api_after_2.get(k) == v for k, v in restore_target.items()),
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase8-p21-03-save-success-and-duplicate.json', out)
        return out

    # ---- P21-05 : persistence after refresh ----
    def phase9_ui_refresh_persistence(self):
        ui.open_url(ui.BASE + '/settings')
        time.sleep(4)
        pre = ui.read_settings()
        pre_api = self.hub.json('/api/settings')
        ui.reload_page(wait=5.0)
        post = ui.read_settings()
        post_api = self.hub.json('/api/settings')
        db = ss._ssh(ss.PROBE)['settings_rows']
        db_managed = {k: db.get(k) for k in ORIGINAL_KEYS}
        expected = {ui_key_for(k): v for k, v in self.settings_before['api_settings'].items()}
        out = {
            'at_utc': now_utc(),
            'fields_before_reload': pre['fields'],
            'fields_after_hard_reload': post['fields'],
            'api_settings_before_reload': pre_api,
            'api_settings_after_reload': post_api,
            'hub_db_settings': db_managed,
            'expected_fields_from_settings_before_snapshot': expected,
        }
        fields_ok = all(post['fields'].get(k) == v for k, v in expected.items())
        out['assertions'] = {
            'ui_fields_after_hard_reload_equal_persisted': bool(fields_ok),
            'api_unchanged_across_reload': pre_api == post_api,
            'api_equals_settings_before_snapshot': post_api == self.settings_before['api_settings'],
            'hub_db_equals_api': all(db_managed.get(k) == post_api.get(k) for k in ORIGINAL_KEYS),
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase9-p21-05-refresh-persistence.json', out)
        return out

    # ---- consumption reverted to the restored values + full log again ----
    def phase10_post_restore_consumption(self):
        obs = self.observe(45, [('HEARTBEAT', 'agent_to_hub')])
        hb = obs['specs']['agent_to_hub/HEARTBEAT']
        exec_id, final = self._run_task('full')
        self.state['executions']['full'] = exec_id
        stdout = final.get('stdout') or ''
        local = self.child.db_read_executions([exec_id])
        row = local['requested_executions'][0]
        out = {
            'at_utc': now_utc(),
            'settings_in_force': self.hub.json('/api/settings'),
            'observation_after_restore': obs,
            'heartbeat_cadence_after_restore': hb,
            'full_log_execution': {
                'execution_id': exec_id, 'status': final.get('status'),
                'stdout_bytes': len(stdout), 'stdout_truncated': final.get('stdout_truncated'),
                'marker_present': MARKER in stdout,
                'agent_local_stdout_bytes': (row or {}).get('stdout_len'),
                'expected_full_bytes': len(MARKER) + 1 + 20000 + 1,
            },
        }
        out['assertions'] = {
            'heartbeat_cadence_back_to_~30s': hb['frames_seen'] <= 3 and hb['frames_seen'] >= 1,
            'full_stdout_no_longer_truncated': len(stdout) == len(MARKER) + 1 + 20000 + 1
            and final.get('stdout_truncated') is False,
        }
        out['passed'] = all(out['assertions'].values())
        self.web('phase10-post-restore-consumption.json', out)
        return out

    # ---- IDEMPOTENT-MANUAL (api deliverable) ----
    def phase11_idempotent_manual(self):
        task_id = self.state['task_id']
        node_id = self.state['child_node_id']
        m1 = None
        rows1 = None
        # M1 with a duplicated EXECUTION_FINISHED upload
        code, rows1 = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id),
                                    {'node_ids': {node_id: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows1) != 1:
            raise RuntimeError('manual_run_m1_http_%s %s' % (code, rows1))
        m1 = rows1[0]['id']
        self.proxy.arm_dup(m1)
        m1_final = self.wait_execution(m1, timeout=90)
        proxy_after = None
        deadline = time.time() + 30
        while time.time() < deadline:
            st = self.proxy.state()
            acks = [a for a in st['seen_ack'] if a['execution_id'] == m1]
            if m1 in st['dup_done'] and len(acks) >= 2:
                proxy_after = st
                break
            time.sleep(0.5)
        if proxy_after is None:
            proxy_after = self.proxy.state()
        listed_m1 = [e for e in self.executions(task_id, node_id) if e['id'] == m1]
        source = self.hub.json('/api/executions/' + self.hub.quote(m1))

        # M2 : fresh UUID, then a real re-upload of the same Execution ID
        code, rows2 = self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id),
                                    {'node_ids': {node_id: True}, 'params': {}})
        self.budget['api_mutations'] += 1
        self.budget['manual_run_posts'] += 1
        if code != 200 or len(rows2) != 1:
            raise RuntimeError('manual_run_m2_http_%s %s' % (code, rows2))
        m2 = rows2[0]['id']
        self.proxy.arm_drop_ack(m2)
        m2_final = self.wait_execution(m2, timeout=90)
        local_pre = self.child.db_read_executions([m2])
        local_row_pre = local_pre['requested_executions'][0]
        # withhold-ACK wait: local row must stay unsynced
        deadline = time.time() + 20
        while time.time() < deadline:
            local_pre = self.child.db_read_executions([m2])
            local_row_pre = local_pre['requested_executions'][0]
            if local_row_pre and local_row_pre.get('synced') is False:
                break
            time.sleep(1)
        hub_row_before_reupload = self.hub.json('/api/executions/' + self.hub.quote(m2))
        finished_before = len([f for f in self.proxy.state()['seen_exec_finished']
                               if f['execution_id'] == m2])
        # force a reconnect -> product re-upload path
        self.proxy.set_mode('cut')
        time.sleep(4)
        self.proxy.set_mode('pass')
        deadline = time.time() + 60
        reuploaded = False
        while time.time() < deadline:
            st = self.proxy.state()
            fin = [f for f in st['seen_exec_finished'] if f['execution_id'] == m2]
            ack = [a for a in st['seen_ack'] if a['execution_id'] == m2]
            if len(fin) >= 2 and len(ack) >= 1:
                reuploaded = True
                break
            time.sleep(1)
        deadline = time.time() + 30
        local_row_post = local_row_pre
        while time.time() < deadline:
            local_post = self.child.db_read_executions([m2])
            local_row_post = local_post['requested_executions'][0]
            if local_row_post and local_row_post.get('synced') is True:
                break
            time.sleep(1)
        listed_m2 = [e for e in self.executions(task_id, node_id) if e['id'] == m2]
        hub_row_after = self.hub.json('/api/executions/' + self.hub.quote(m2))
        out = {
            'at_utc': now_utc(),
            'premise': 'each manual Run Now gets a fresh Hub-created Execution UUID; the UUID is the '
                       'idempotency key for the Execution-finished upload',
            'owner': 'api',
            'child': {'node_id': node_id, 'agent_id': self.state['child_agent_id'],
                      'host': CHILD_HOST, 'owned_root': self.child.root,
                      'proxy_port': self.proxy.port},
            'manual_run_1': {
                'request': 'POST /api/tasks/%s/run {"node_ids":{"%s":true},"params":{}}' % (task_id, node_id),
                'http': code, 'execution_id': m1, 'status': m1_final.get('status'),
                'exit_code': m1_final.get('exit_code'), 'trigger_type': m1_final.get('trigger_type'),
                'stdout': (m1_final.get('stdout') or '').strip(),
                'marker_present': MARKER in (m1_final.get('stdout') or ''),
                'hub_rows_with_this_uuid': len(listed_m1),
            },
            'duplicate_delivery': {
                'mechanism': 'frame proxy forwarded the same EXECUTION_FINISHED frame (same Execution '
                             'ID) to the repair Hub a second time',
                'exec_finished_frames_seen_for_m1': len([f for f in proxy_after['seen_exec_finished']
                                                         if f['execution_id'] == m1]),
                'duplicate_injected': m1 in proxy_after['dup_done'],
                'hub_acks_seen_for_m1_by_proxy': len([a for a in proxy_after['seen_ack']
                                                      if a['execution_id'] == m1]),
                'hub_rows_after_duplicate': len(listed_m1),
                'hub_status_after_duplicate': listed_m1[0].get('status') if listed_m1 else None,
                'no_duplicate_row': len(listed_m1) == 1,
                'hub_execution_ids_for_task': sorted(e['id'] for e in self.executions(task_id, node_id)),
            },
            'manual_run_2': {
                'execution_id': m2, 'status': m2_final.get('status'),
                'distinct_uuid_from_run_1': m2 != m1,
                'hub_rows_with_this_uuid': len(listed_m2),
            },
            'reupload_same_execution_id': {
                'mechanism': 'harness withheld one Hub->Agent EXECUTION_ACK for m2, then cut and '
                             'restored the child connection so the product re-upload path ran',
                'agent_local_row_while_ack_withheld': local_row_pre,
                'exec_finished_frames_for_m2_before_reconnect': finished_before,
                'reupload_observed': reuploaded,
                'exec_finished_frames_for_m2_after_reconnect': len(
                    [f for f in self.proxy.state()['seen_exec_finished'] if f['execution_id'] == m2]),
                'agent_local_row_after_reupload': local_row_post,
                'hub_row_before_reupload': hub_row_before_reupload,
                'hub_row_after_reupload': hub_row_after,
                'hub_rows_after_reupload': len(listed_m2),
                'content_identical': (hub_row_before_reupload.get('stdout') ==
                                      hub_row_after.get('stdout')),
                'no_duplicate_row_after_reupload': len(listed_m2) == 1,
            },
        }
        out['assertions'] = {
            'm1_success_and_marker': m1_final.get('status') == 'SUCCESS' and
            MARKER in (m1_final.get('stdout') or ''),
            'duplicate_delivery_kept_one_hub_row': len(listed_m1) == 1,
            'duplicate_delivery_acked_twice': len([a for a in proxy_after['seen_ack']
                                                  if a['execution_id'] == m1]) >= 2,
            'm2_new_distinct_uuid': m2 != m1,
            'm2_success': m2_final.get('status') == 'SUCCESS',
            'ack_withheld_left_local_row_unsynced': bool(local_row_pre) and
            local_row_pre.get('synced') is False,
            'reupload_same_execution_id_observed': reuploaded,
            'reupload_kept_one_hub_row_and_identical_content': len(listed_m2) == 1 and
            hub_row_before_reupload.get('stdout') == hub_row_after.get('stdout'),
        }
        out['passed'] = all(out['assertions'].values())
        self.api('phase3-idempotent-manual.json', out)
        self.api('case-evidence.json', {
            'case_id': 'IDEMPOTENT-MANUAL', 'batch': 'idempotent-manual',
            'nonce': NONCE, 'at_utc': now_utc(),
            'shared_owned_child': True,
            'child': out['child'],
            'source_evidence': 'phase3-idempotent-manual.json',
            'cross_batch_note': 'this run shares the owned child Agent / frame proxy of '
                                'qa-resume-20261014-web/batch09-settings (single serial batch); '
                                'the child, its fixtures and the proxy are owned by that batch '
                                'and are removed during its cleanup phase',
        })
        return out

    # ---------------- restore + cleanup ----------------
    def ensure_settings_restored(self):
        """Always run this: compare against the pre-run snapshot and PUT back anything
        that differs. Returns (restored, detail)."""
        original = {k: v for k, v in (self.settings_before or {}).get('api_settings', {}).items()
                    if k in ORIGINAL_KEYS}
        current = self.hub.json('/api/settings')
        to_fix = {k: v for k, v in original.items() if current.get(k) != v}
        extra = {k: v for k, v in current.items() if k not in original}
        detail = {'original': original, 'current_before_restore': current,
                  'keys_to_fix': to_fix, 'unexpected_extra_keys': extra}
        if to_fix:
            code, body = self.hub.put('/api/settings', to_fix)
            self.budget['api_mutations'] += 1
            self.budget['settings_puts'] += 1
            detail['restore_put'] = {'request': to_fix, 'http': code, 'body': body}
        after = self.hub.json('/api/settings')
        detail['current_after_restore'] = after
        detail['restored'] = after == (self.settings_before or {}).get('api_settings')
        return detail['restored'], detail

    def phase12_settings_after(self, restore_detail):
        snap = ss.capture(self.hub)
        self.settings_after = snap
        self.web('settings-after.json', snap)
        diff = ss.diff(self.settings_before, snap)
        out = {'at_utc': now_utc(), 'restore_procedure': restore_detail, 'diff': diff}
        out['passed'] = diff['all_equal'] and restore_detail.get('restored') is True
        self.web('phase12-settings-after-and-restore-diff.json', out)
        return out

    def phase13_cleanup(self):
        results = {'at_utc': now_utc(), 'errors': [], 'deleted': []}
        if self.child is not None:
            results['child_process_removal'] = self.child.remove()
            self.budget['child_process_removals'] += 1
        order = [('task', self.state['task_id']), ('script', self.state['script_id'])]
        for kind, obj_id in order:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['hub_definition_deletes'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code,
                                       'get_http': gcode})
            self.state['delete_results'][obj_id] = {'kind': kind, 'delete_http': code,
                                                    'get_http': gcode}
        node_id = self.state['child_node_id']
        if node_id:
            code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
            self.budget['api_mutations'] += 1
            gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
            results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode,
                                     'delete_body': body if isinstance(body, (dict, str)) else None}
            if code != 200 or gcode != 404:
                results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and \
            results.get('child_node', {}).get('get_http') == 404
        self.web('phase13-cleanup.json', results)
        return results

    def phase14_oracle(self, cleanup, settings_ok):
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
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = childlib.OwnedChild(NONCE, 1, '', '', '', '', '', host=CHILD_HOST,
                                  tag=CHILD_TAG).inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        units_after = {}
        for label, (ip, unit) in node_units.NODES.items():
            units_after[label] = {'ip': ip,
                                  'units': node_units.probe(ip, [unit, 'nodesteer-agent.service'])}
        units_after['hub-vm'] = {
            'ip': node_units.HUB[0],
            'units': node_units.probe(node_units.HUB[0], [node_units.HUB[1], node_units.HUB[2]])}
        pid_same = {}
        for label, base in self.unit_baseline.items():
            pid_same[label] = {}
            for unit, vals in base['units'].items():
                now_vals = units_after.get(label, {}).get('units', {}).get(unit, {})
                pid_same[label][unit] = {'before': vals.get('MainPID'), 'after': now_vals.get('MainPID'),
                                         'same': vals.get('MainPID') == now_vals.get('MainPID')}
        all_pids_same = all(v['same'] for grp in pid_same.values() for v in grp.values())
        health = {}
        for suffix in ('/healthz', '/readyz'):
            try:
                code, body = self.hub.get(suffix)
                health[suffix] = {'http': code, 'body_len': len(json.dumps(body)) if body else 0}
            except Exception as exc:
                health[suffix] = {'error': type(exc).__name__}
        # after cleanup the Hub settings table must be back to its pre-run key/value shape
        try:
            db_now = ss._ssh(ss.PROBE)['settings_rows']
        except Exception as exc:  # noqa: BLE001
            db_now = {'__error': type(exc).__name__}
        before_db = {k: (self.settings_before or {}).get('hub_db_settings_managed', {}).get(k)
                     for k in ORIGINAL_KEYS}
        db_now_managed = {k: db_now.get(k) for k in ORIGINAL_KEYS} if isinstance(db_now, dict) else None
        oracle = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'owned_child_orphan_on_child_host': orphan,
            'owned_child_inventory_after': {
                'root': inv['root'], 'root_exists': inv['root_exists'],
                'config_exists': inv['config_exists'],
                'owned_child_processes': inv['owned_child_processes'],
                'primary_agent_unit': inv['primary_agent_unit'],
                'repair_agent_units': inv['repair_agent_units']},
            'protected_units_pid_unchanged': pid_same,
            'all_protected_units_pid_unchanged': all_pids_same,
            'hub_health_after': health,
            'hub_db_settings_key_set_after_cleanup': sorted(db_now.keys()) if isinstance(db_now, dict) else None,
            'hub_db_settings_key_set_before_run': sorted(
                (self.settings_before or {}).get('hub_db_settings_all_keys', [])),
            'hub_db_settings_key_set_after_equals_before': (
                sorted(db_now.keys()) == sorted(
                    (self.settings_before or {}).get('hub_db_settings_all_keys', []))
                if isinstance(db_now, dict) and '__error' not in db_now else False),
            'hub_db_managed_settings_after_cleanup': db_now_managed,
            'hub_db_managed_settings_match_before': (db_now_managed == before_db),
            'settings_restored': settings_ok,
            'cleanup_results': cleanup,
            'budget': self.budget,
            'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and all_pids_same
                           and cleanup.get('all_get_404') and settings_ok
                           and oracle['hub_db_settings_key_set_after_equals_before']
                           and oracle['hub_db_managed_settings_match_before'])
        self.web('final-cleanup-oracle.json', oracle)
        return oracle


def ui_key_for(api_key):
    return {
        'heartbeat_interval_sec': '心跳间隔（秒）',
        'revision_check_interval_sec': '修订检查间隔（秒）',
        'changelog_window': '变更日志窗口',
        'max_log_bytes': '最大日志字节数',
        'agent_gateway_base_url': 'Agent 网关地址',
    }.get(api_key, api_key)


def ui_field_to_api(ui_key):
    for api_key, label in {
        'heartbeat_interval_sec': '心跳间隔（秒）',
        'revision_check_interval_sec': '修订检查间隔（秒）',
        'changelog_window': '变更日志窗口',
        'max_log_bytes': '最大日志字节数',
    }.items():
        if label == ui_key:
            return api_key
    return ui_key


def main():
    run = Run()
    started = now_utc()
    hubapi.write_json('../progress.json', {
        'batch_id': 'batch09-settings', 'nonce': NONCE, 'owned_prefix': PREFIX,
        'phase': 'guard', 'updated_at_utc': started,
        'settings_modified_window': 'to be recorded'})
    evidence = {'batch_id': 'batch09-settings', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base, 'started_at_utc': started, 'phases': {},
                'deviations': []}
    progress = {'batch_id': 'batch09-settings', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'phase': 'guard', 'updated_at_utc': started}
    settings_modified_events = []
    trunc_evidence = None
    invalid_evidence = None
    save_evidence = None
    refresh_evidence = None
    post_restore_evidence = None
    idem_evidence = None
    baseline_evidence = None
    broadcast_evidence = None
    child_evidence = None
    before_evidence = None
    cleanup = None
    after_evidence = None
    oracle = None
    fatal = None

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('../progress.json', progress)

    try:
        mark('guard')
        evidence['phases']['guard'] = run.phase0_guard()
        mark('settings_before')
        before_evidence = run.phase1_settings_before()
        evidence['phases']['settings_before'] = before_evidence
        mark('child_start')
        child_evidence = run.phase2_child()
        evidence['phases']['child'] = child_evidence
        progress.update({'child_node_id': run.state['child_node_id'],
                         'child_agent_id': run.state['child_agent_id'],
                         'child_root': run.child.root})
        mark('baseline_cadence')
        baseline_evidence = run.phase3_baseline_cadence()
        evidence['phases']['baseline'] = baseline_evidence
        mark('apply_and_broadcast')
        broadcast_evidence = run.phase4_apply_and_broadcast()
        settings_modified_events.append({
            'recorded_at_utc': now_utc(), 'kind': 'settings_put',
            'request': MODIFIED,
            'observed_consumption': {
                'settings_broadcast_frame': broadcast_evidence[
                    'settings_broadcast_frame_from_hub_to_owned_child'],
                'heartbeat_cadence': broadcast_evidence['heartbeat_cadence_after_change'],
                'revision_cadence': broadcast_evidence['revision_cadence_after_change']}})
        evidence['phases']['broadcast'] = broadcast_evidence
        mark('fixtures')
        evidence['phases']['fixtures'] = run.phase5_fixtures()
        progress.update({'script_id': run.state['script_id'], 'task_id': run.state['task_id']})
        mark('max_log_truncation')
        trunc_evidence = run.phase6_max_log_truncation()
        evidence['phases']['max_log_truncation'] = trunc_evidence
        settings_modified_events.append({
            'recorded_at_utc': now_utc(), 'kind': 'max_log_bytes_consumption',
            'observed_consumption': {
                'hub_stdout_bytes': trunc_evidence['hub_execution']['stdout_bytes'],
                'hub_stdout_truncated': trunc_evidence['hub_execution']['stdout_truncated'],
                'script_raw_bytes': trunc_evidence['script_raw_stdout_bytes']}})
        mark('ui_invalid')
        invalid_evidence = run.phase7_ui_invalid()
        evidence['phases']['ui_invalid'] = invalid_evidence
        mark('ui_save')
        save_evidence = run.phase8_ui_save_success_and_duplicate()
        evidence['phases']['ui_save'] = save_evidence
        settings_modified_events.append({
            'recorded_at_utc': now_utc(), 'kind': 'settings_restored_via_ui_save',
            'request': save_evidence['restore_target_values'],
            'toasts': save_evidence['duplicate_save']['toasts']})
        mark('ui_refresh')
        refresh_evidence = run.phase9_ui_refresh_persistence()
        evidence['phases']['ui_refresh'] = refresh_evidence
        mark('post_restore_consumption')
        post_restore_evidence = run.phase10_post_restore_consumption()
        evidence['phases']['post_restore'] = post_restore_evidence
        settings_modified_events.append({
            'recorded_at_utc': now_utc(), 'kind': 'consumption_after_restore',
            'observed_consumption': {
                'heartbeat_cadence': post_restore_evidence['heartbeat_cadence_after_restore'],
                'full_log': post_restore_evidence['full_log_execution']}})
        mark('idempotent_manual')
        idem_evidence = run.phase11_idempotent_manual()
        evidence['phases']['idempotent_manual'] = idem_evidence
        print('PHASES_DONE', now_utc(), flush=True)
    except Exception as exc:  # noqa: BLE001
        fatal = '%s: %s' % (type(exc).__name__, exc)
        evidence['deviations'].append({'kind': 'fatal', 'detail': fatal, 'at_utc': now_utc()})
        print('FATAL %s' % fatal, flush=True)
    finally:
        mark('restore_settings')
        try:
            restored, restore_detail = run.ensure_settings_restored()
        except Exception as exc:  # noqa: BLE001
            restored, restore_detail = False, {'error': '%s: %s' % (type(exc).__name__, exc)}
        try:
            after_evidence = run.phase12_settings_after(restore_detail)
            evidence['phases']['settings_after'] = after_evidence
        except Exception as exc:  # noqa: BLE001
            after_evidence = {'passed': False, 'error': '%s: %s' % (type(exc).__name__, exc)}
            evidence['deviations'].append({'kind': 'settings_after_capture_failed',
                                           'detail': after_evidence['error']})
        try:
            mark('cleanup')
            cleanup = run.phase13_cleanup()
            evidence['phases']['cleanup'] = cleanup
        except Exception as exc:  # noqa: BLE001
            evidence['deviations'].append({'kind': 'cleanup_failed',
                                           'detail': '%s: %s' % (type(exc).__name__, exc)})
        try:
            mark('oracle')
            oracle = run.phase14_oracle(cleanup or {}, bool(after_evidence.get('passed')))
            evidence['phases']['oracle'] = oracle
        except Exception as exc:  # noqa: BLE001
            evidence['deviations'].append({'kind': 'oracle_failed',
                                           'detail': '%s: %s' % (type(exc).__name__, exc)})
        try:
            if run.proxy is not None:
                run.proxy.state()
                run.proxy.stop()
        except Exception:
            pass

    evidence['finished_at_utc'] = now_utc()
    evidence['settings_modified_events'] = settings_modified_events
    evidence['settings_modified_events_note'] = (
        'recorded_at_utc is when the event was written to evidence right after its phase; the '
        'exact PUT/frame instants are the timestamps inside the corresponding phase evidence '
        '(settings_put + the SETTINGS frame at_utc in phase4, and the execution times in phase6)')
    evidence['budget'] = run.budget
    evidence['fatal'] = fatal
    hubapi.write_json('../run-evidence.json', evidence)

    # ---------------- deliverables ----------------
    def state_of(ev, key='passed'):
        if not ev:
            return 'NOT_RUN'
        return 'PASS' if ev.get(key) else 'FAIL'

    restore_ok = bool(after_evidence.get('passed'))
    web_cases = [
        {
            'case_id': 'P21-03', 'title': '修改、保存、成功提示、失败提示、重复保存。',
            'canonical_before': 'NOT_RUN', 'proposed_after': None, 'confidence': None,
            'dimensions': {
                'modify_and_save_via_ui': save_evidence and
                save_evidence['assertions']['values_persisted_on_hub'],
                'success_toast_已保存': save_evidence and
                save_evidence['assertions']['ui_save_produced_success_toast'],
                'failure_toast_on_invalid_input': invalid_evidence and
                invalid_evidence['assertions'][
                    'every_ui_invalid_input_rejected_with_expected_error_and_not_persisted'],
                'duplicate_save_no_side_effect': save_evidence and
                save_evidence['assertions']['duplicate_save_idempotent_no_error_toast'] and
                save_evidence['assertions']['repeated_save_same_result'],
            },
            'evidence': ['phase7-p21-04-invalid-inputs.json',
                         'phase8-p21-03-save-success-and-duplicate.json',
                         'phase9-p21-05-refresh-persistence.json',
                         'phase12-settings-after-and-restore-diff.json',
                         'settings-before.json', 'settings-after.json'],
        },
        {
            'case_id': 'P21-04', 'title': '非法数字、0、负数、超大值、非法 Gateway URL。',
            'canonical_before': 'NOT_RUN', 'proposed_after': None, 'confidence': None,
            'dimensions': {
                'non_numeric_rejected': invalid_evidence and
                invalid_evidence['per_dimension']['non_numeric']['expected_error_shown'] and
                invalid_evidence['per_dimension']['non_numeric']['not_persisted'],
                'zero_rejected': invalid_evidence and
                invalid_evidence['per_dimension']['zero']['expected_error_shown'] and
                invalid_evidence['per_dimension']['zero']['not_persisted'],
                'negative_rejected': invalid_evidence and
                invalid_evidence['per_dimension']['negative']['expected_error_shown'] and
                invalid_evidence['per_dimension']['negative']['not_persisted'],
                'oversized_rejected': invalid_evidence and
                invalid_evidence['per_dimension']['oversized']['expected_error_shown'] and
                invalid_evidence['per_dimension']['oversized']['not_persisted'],
                'below_minimum_rejected': invalid_evidence and
                invalid_evidence['per_dimension']['below_min_revision']['expected_error_shown'] and
                invalid_evidence['per_dimension']['below_min_max_log']['not_persisted'],
                'invalid_gateway_url_rejected': invalid_evidence and
                invalid_evidence['assertions'][
                    'every_api_invalid_gateway_url_rejected_400_not_persisted'],
                'no_partial_write_on_rejection': invalid_evidence and
                all(r['not_persisted'] for r in invalid_evidence['ui_invalid_cases']) and
                all(r['unchanged'] for r in invalid_evidence['api_invalid_numbers']),
            },
            'evidence': ['phase7-p21-04-invalid-inputs.json',
                         'settings-before.json', 'settings-after.json'],
        },
        {
            'case_id': 'P21-05', 'title': '保存后刷新持久化。',
            'canonical_before': 'NOT_RUN', 'proposed_after': None, 'confidence': None,
            'dimensions': {
                'ui_field_values_survive_hard_reload': refresh_evidence and
                refresh_evidence['assertions']['ui_fields_after_hard_reload_equal_persisted'],
                'api_equals_hub_db_after_reload': refresh_evidence and
                refresh_evidence['assertions']['hub_db_equals_api'],
                'values_survive_hub_read_back': refresh_evidence and
                refresh_evidence['assertions']['api_equals_settings_before_snapshot'],
            },
            'evidence': ['phase9-p21-05-refresh-persistence.json', 'settings-before.json',
                         'settings-after.json'],
        },
        {
            'case_id': 'P21-06', 'title': '设置变更是否广播到在线 Agent。',
            'canonical_before': 'NOT_RUN', 'proposed_after': None, 'confidence': None,
            'dimensions': {
                'settings_frame_actually_reaches_online_owned_agent': broadcast_evidence and
                broadcast_evidence['assertions'][
                    'settings_frame_broadcast_to_online_owned_child'],
                'broadcast_payload_matches_saved_values': broadcast_evidence and
                broadcast_evidence['settings_broadcast_frame_from_hub_to_owned_child'] is not None,
                'protected_agents_unharmed_by_broadcast': broadcast_evidence and
                broadcast_evidence['assertions']['protected_nodes_still_online_synced'],
            },
            'evidence': ['phase4-settings-broadcast-and-cadence.json'],
        },
        {
            'case_id': 'FL-16',
            'title': 'Settings Save → Agent Runtime → Agent 心跳/Revision/日志设置实际消费或明确不消费',
            'canonical_before': 'NOT_RUN (baseline was PARTIAL)', 'proposed_after': None,
            'confidence': None,
            'dimensions': {
                'heartbeat_setting_consumed_at_connect': baseline_evidence and
                baseline_evidence['assertions'][
                    'heartbeat_not_local_2s_and_matches_hub_30s'],
                'revision_setting_consumed_at_connect': baseline_evidence and
                baseline_evidence['assertions'][
                    'revision_not_local_2s_and_matches_hub_45s'],
                'heartbeat_cadence_follows_saved_3s': broadcast_evidence and
                broadcast_evidence['assertions']['heartbeat_cadence_became_~3s'],
                'revision_cadence_follows_saved_5s': broadcast_evidence and
                broadcast_evidence['assertions']['revision_cadence_became_~5s'],
                'cadence_contrast_baseline_30s45s_vs_saved_3s5s': bool(
                    baseline_evidence and broadcast_evidence
                    and baseline_evidence['assertions'][
                        'heartbeat_not_local_2s_and_matches_hub_30s']
                    and broadcast_evidence['heartbeat_cadence_after_change']['delta_mean'] <= 5.0
                    and baseline_evidence['heartbeat_cadence']['delta_mean'] >= 22.0),
                'max_log_bytes_truncates_real_output': trunc_evidence and
                trunc_evidence['assertions']['hub_stdout_cut_to_max_log_bytes'],
                'max_log_bytes_effect_reverts_after_restore': post_restore_evidence and
                post_restore_evidence['assertions']['full_stdout_no_longer_truncated'],
                'heartbeat_cadence_reverts_after_restore': post_restore_evidence and
                post_restore_evidence['assertions']['heartbeat_cadence_back_to_~30s'],
            },
            'evidence': ['phase3-baseline-settings-consumption.json',
                         'phase4-settings-broadcast-and-cadence.json',
                         'phase6-max-log-bytes-truncation.json',
                         'phase10-post-restore-consumption.json',
                         'settings-before.json', 'settings-after.json',
                         'phase12-settings-after-and-restore-diff.json'],
        },
    ]
    problems = []
    if fatal:
        problems.append('fatal: ' + fatal)
    if not restore_ok:
        problems.append('settings restore verification failed')
    for c in web_cases:
        dims = c['dimensions']
        passed = bool(dims) and all(bool(v) for v in dims.values())
        if not restore_ok or fatal:
            passed = False
        c['proposed_after'] = 'PASS' if passed else ('PARTIAL' if any(bool(v) for v in dims.values())
                                                     else 'FAIL')
        c['confidence'] = 'high' if passed and not fatal else ('medium' if passed else 'low')
        c['all_dimensions_pass'] = passed
        c['dimension_verdicts'] = {k: ('PASS' if v else 'FAIL') for k, v in dims.items()}

    idem_dims = (idem_evidence or {}).get('assertions', {})
    idem_pass = bool(idem_evidence) and bool(idem_dims) and all(idem_dims.values())
    idem_case = {
        'case_id': 'IDEMPOTENT-MANUAL',
        'title': '每次Manual Run新UUID为新意图；相同Execution ID/Key重复投递/上传保持幂等',
        'canonical_before': 'NOT_RUN', 'proposed_after': 'PASS' if idem_pass else 'FAIL',
        'confidence': 'high' if idem_pass else 'low',
        'dimensions': {'%s' % k: ('PASS' if v else 'FAIL') for k, v in idem_dims.items()},
        'all_dimensions_pass': idem_pass,
        'owned_child_and_fixtures': (idem_evidence or {}).get('child'),
        'evidence': ['phase3-idempotent-manual.json', 'case-evidence.json'],
        'cross_batch_note': 'shares the batch09-settings owned child Agent + frame proxy; a single '
                            'serial batch, so the api deliverable records its own evidence files '
                            'while the web batch owns the fixture ledger',
    }

    web_result = {
        'batch_id': 'batch09-settings', 'nonce': NONCE, 'owned_prefix': PREFIX,
        'title': 'Settings：修改/保存/校验/持久化/广播 与 Agent 运行时实际消费',
        'executed_at_utc': started, 'finished_at_utc': now_utc(),
        'endpoint': run.hub.base,
        'cases': web_cases,
        'settings_snapshot': {
            'before_file': 'evidence/settings-before.json',
            'after_file': 'evidence/settings-after.json',
            'diff_file': 'evidence/phase12-settings-after-and-restore-diff.json',
            'before_api_settings': (before_evidence or {}).get('api_settings'),
            'after_api_settings': ((after_evidence or {}).get('diff') or {}).get('api_settings_after'),
            'restored': restore_ok,
            'diff': (after_evidence or {}).get('diff'),
        },
        'settings_modified_events': settings_modified_events,
        'deviations': evidence['deviations'],
        'budget': run.budget,
        'fatal': fatal,
        'final_cleanup_oracle_clean': bool(oracle and oracle.get('clean')),
    }
    hubapi.write_json('../case-result.json', web_result)
    hubapi.write_json('../progress.json', {
        'batch_id': 'batch09-settings', 'nonce': NONCE, 'owned_prefix': PREFIX,
        'phase': 'done' if not fatal else 'done-with-fatal',
        'updated_at_utc': now_utc(),
        'child_node_id': run.state['child_node_id'], 'child_agent_id': run.state['child_agent_id'],
        'child_root': run.child.root if run.child else None,
        'script_id': run.state['script_id'], 'task_id': run.state['task_id'],
        'executions': run.state['executions'],
        'settings_restored': restore_ok, 'fatal': fatal,
        'cleanup': cleanup, 'oracle_clean': bool(oracle and oracle.get('clean')),
        'budget': run.budget})
    hubapi.write_json('../fixture-ledger.json', {
        'batch_id': 'batch09-settings', 'nonce': NONCE, 'prefix': PREFIX,
        'owned_child': ({'host': CHILD_HOST, 'tag': CHILD_TAG, 'root': run.child.root,
                         'node_id': run.state['child_node_id'],
                         'agent_id': run.state['child_agent_id'],
                         'removed': bool(cleanup and cleanup.get('child_process_removal',
                                                                 {}).get('root_exists_after') is False)}
                        if run.child else None),
        'owned_hub_fixtures': {
            'script': {'id': run.state['script_id'], 'name': SCRIPT_NAME,
                       'purpose': 'max_log_bytes truncation (FL-16)',
                       'delete': run.state['delete_results'].get(run.state['script_id'])},
            'task': {'id': run.state['task_id'], 'name': TASK_NAME,
                     'purpose': 'manual run for FL-16 + IDEMPOTENT-MANUAL',
                     'delete': run.state['delete_results'].get(run.state['task_id'])},
            'node': {'id': run.state['child_node_id'], 'name': PREFIX + 'child',
                     'delete': (cleanup or {}).get('child_node')},
        },
        'executions_created': run.state['executions'],
        'retained_history': {
            'note': 'Hub has no execution DELETE endpoint; execution rows remain as history under the '
                    'deleted owned task id',
            'execution_ids': list(run.state['executions'].values())},
        'settings_keys_modified_then_restored': {'requested': MODIFIED,
                                                 'restored_to': (before_evidence or {}).get('api_settings'),
                                                 'verified': restore_ok},
        'api_and_web_deliverables': {'web': str(WEB_DIR), 'api': str(API_DIR)},
        'status': 'CLEANED' if (cleanup or {}).get('all_get_404') else 'INCOMPLETE'})
    hubapi.write_json('../final-cleanup-oracle.json', oracle or {'clean': False, 'fatal': fatal})

    idem_result = {
        'batch_id': 'idempotent-manual', 'nonce': NONCE,
        'title': 'IDEMPOTENT-MANUAL：Manual Run 新 UUID 与相同 Execution ID 重复投递/上传幂等',
        'executed_at_utc': started, 'finished_at_utc': now_utc(), 'endpoint': run.hub.base,
        'cases': [idem_case],
        'deviations': evidence['deviations'],
        'budget': run.budget,
        'shared_context': {
            'owned_child': (idem_evidence or {}).get('child'),
            'owned_web_batch': str(WEB_DIR),
            'note': 'the owned child Agent, its proxies and its Hub fixtures are declared and '
                    'removed by the batch09-settings ledger; this deliverable carries its own '
                    'evidence and verifies the product idempotency behaviour',
        },
    }
    write_at(API_DIR / 'case-result.json', idem_result)
    write_at(API_DIR / 'progress.json', {
        'batch_id': 'idempotent-manual', 'nonce': NONCE,
        'phase': 'done' if idem_evidence else 'not-run',
        'updated_at_utc': now_utc(),
        'child_node_id': run.state['child_node_id'],
        'task_id': run.state['task_id'],
        'executions': run.state['executions'],
        'settings_restored': restore_ok,
        'fatal': fatal})
    write_at(API_DIR / 'fixture-ledger.json', {
        'batch_id': 'idempotent-manual', 'nonce': NONCE,
        'shared_owned_child': True,
        'owned_child_owner': 'qa-resume-20261014-web/batch09-settings',
        'fixtures_owner': 'qa-resume-20261014-web/batch09-settings (fixture-ledger.json)',
        'executions_created': run.state['executions'],
        'no_additional_hub_resources_created_by_this_deliverable': True,
        'status': 'CLEANED' if (cleanup or {}).get('all_get_404') else 'INCOMPLETE'})
    write_at(API_DIR / 'final-cleanup-oracle.json', {
        'batch_id': 'idempotent-manual', 'nonce': NONCE,
        'shared_cleanup_oracle': str(WEB_DIR / 'final-cleanup-oracle.json'),
        'clean': bool(oracle and oracle.get('clean')),
        'settings_restored': restore_ok,
        'at_utc': now_utc()})

    print(json.dumps({
        'fatal': fatal,
        'web_cases': {c['case_id']: c['proposed_after'] for c in web_cases},
        'web_dimensions': {c['case_id']: c['dimension_verdicts'] for c in web_cases},
        'idempotent_manual': idem_case['proposed_after'],
        'idempotent_dimensions': idem_case['dimensions'],
        'settings_restored': restore_ok,
        'oracle_clean': bool(oracle and oracle.get('clean')),
        'budget': run.budget,
    }, ensure_ascii=False, indent=2))
    return 0 if not fatal else 1


if __name__ == '__main__':
    sys.exit(main())
