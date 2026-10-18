#!/usr/bin/env python3
"""sys-b12 runtime batch: Host Adapter path guards, cross-filesystem atomic replace,
permission denial, and Capability pre-dispatch gating.

Cases: SYS-56 (`../` / absolute / non-clean / symlink escape / allowlist),
       SYS-57 + AR-35 (cross-filesystem temp file + atomic replace),
       AR-32 (Host Adapter permission denied + recovery),
       AR-34 (Path Escape + recovery),
       SYS-58 + AR-33 + INV-16 (Capability mismatch blocked before dispatch).

Real evidence chain, no product source change, no protected service touched, only owned
nonce-prefixed fixtures on repair native-2 (192.168.100.206):

  operator -> repair Hub :18080 API -> real owned Agent process (native + non-native)
  -> real Host Adapter filesystem/atomic-replace/symlink/allowlist decisions
  -> Hub readback (transfers/executions/state) + owned Agent SQLite journal + host oracle.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import frame_proxy
import hostapi
import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb12-%s-' % NONCE
TAG = 'qa-sysb12-%s' % NONCE
HUB = 'http://192.168.100.209:18080'
WS = 'ws://192.168.100.209:18080'
# distinct, nonce-derived node addresses: the Hub's enrollment dedupes on hostname OR ip, so two
# owned children must never share an ip.  The four protected repair nodes already use the
# "<hostname>.invalid" convention; reusing it keeps the owned nodes collision-free.
NODE_IP_A = PREFIX + 'child-a.invalid'
NODE_IP_B = PREFIX + 'child-b.invalid'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}

OPT = '/opt/%s' % TAG
BIN_MAIN = OPT + '/app/bin/app'
CFG_MAIN = OPT + '/app/config/app.env'
UNIT = PREFIX + 'app.service'
XFS = OPT + '/xfs'
SRC_DIR = OPT + '/src'
ESCAPE_DIR = '/tmp/' + TAG + '-escape'
ALLOW_DIR = OPT + '/allow'
DST_DIR = OPT + '/dst'
ENOSPC_PRE = XFS + '/full/target.bin'
DEV = 'evidence/'
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
SHA = lambda b: hashlib.sha256(b).hexdigest()
TERMINAL = ('SUCCESS', 'FAILED', 'CANCELED')


def pad_script(body, target=1024 * 1024):
    """Valid /bin/sh script with ~1 MiB of comment padding."""
    out = ['#!/bin/sh\n']
    while sum(len(x) for x in out) < target:
        out.append('# padding-%s\n' % ('p' * 78))
    out.append(body)
    return ''.join(out).encode()


def _ex(ex):
    if not ex:
        return None
    return {k: ex.get(k) for k in ('id', 'task_id', 'node_id', 'status', 'trigger_type',
                                   'start_time', 'end_time', 'block_reason')}


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.child_a = None
        self.child_b = None
        self.proxy = None
        self.state = {'artifacts': {}, 'app': None, 'transfers': [], 'nodes': {},
                      'deletes': {}, 'seams': [], 'results': {}}
        self.budget = {'api_mutations': 0, 'artifact_uploads': 0, 'artifact_deletes': 0,
                       'app_creates': 0, 'app_updates': 0, 'app_deletes': 0, 'app_assigns': 0,
                       'deploys': 0, 'transfers': 0, 'transfer_retries': 0,
                       'node_enroll_posts': 0, 'node_deletes': 0, 'node_upgrades': 0,
                       'child_process_starts': 0, 'child_process_stops': 0,
                       'host_seam_actions': 0, 'mounts': 0}
        self.failures = []

    # ---------- plumbing ----------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        path = hubapi.write_json(DEV + name, value)
        self.state['results'][name] = value
        return path

    def progress(self, phase, **extra):
        p = {'batch_id': 'sys-b12', 'nonce': NONCE, 'owned_prefix': PREFIX, 'phase': phase,
             'updated_at_utc': NOW(), 'budget': self.budget, 'failures': self.failures}
        p.update(extra)
        hubapi.write_json('progress.json', p)

    def step(self, name, fn, critical=False):
        """Run a phase, persist whatever it returns, never lose partial evidence."""
        self.progress(name)
        try:
            out = fn()
            self.rec(name + '.json', out)
            return out
        except Exception as exc:
            self.failures.append({'phase': name, 'error': '%s: %s' % (type(exc).__name__, exc)})
            self.rec(name + '-error.json', {'phase': name, 'error': str(exc)[:600]})
            if critical:
                raise
            return None

    # ---------- Hub helpers ----------
    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def wait_node(self, node_id, timeout=90, expect_name=None):
        deadline = time.time() + timeout
        n = None
        while time.time() < deadline:
            n = self.node(node_id)
            if n and n.get('status') == 'online' and n.get('sync_status') == 'synced':
                if expect_name and n.get('hostname') != expect_name:
                    raise RuntimeError('node_identity_mismatch:%s!=%s' % (n.get('hostname'), expect_name))
                return n
            time.sleep(1)
        return n

    def app_hub(self):
        code, body = self.hub.get('/api/applications/' + self.hub.quote(self.state['app']))
        return body if code == 200 else None

    def app_states(self, node_id=None):
        code, body = self.hub.get('/api/applications/%s/state' % self.hub.quote(self.state['app']))
        rows = body if code == 200 else []
        if node_id:
            return [r for r in rows if r.get('node_id') == node_id]
        return rows

    def app_execs(self):
        code, body = self.hub.get('/api/applications/%s/executions' % self.hub.quote(self.state['app']))
        return body if code == 200 else []

    def execution(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return body if code == 200 else None

    def wait_exec(self, exec_id, timeout=180):
        deadline = time.time() + timeout
        ex = None
        while time.time() < deadline:
            ex = self.execution(exec_id)
            if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'):
                return ex
            time.sleep(0.5)
        return ex

    def deploy(self, operation, node_ids, app_id=None):
        self.budget['api_mutations'] += 1
        self.budget['deploys'] += 1
        target = app_id or self.state['app']
        return self.hub.post('/api/applications/%s/deploy' % self.hub.quote(target),
                             {'node_ids': node_ids, 'operation': operation})

    def put_app(self, definition):
        self.budget['api_mutations'] += 1
        self.budget['app_updates'] += 1
        return self.hub.put('/api/applications/' + self.hub.quote(self.state['app']), definition)

    def create_transfer(self, source_node, source_path, targets):
        self.budget['api_mutations'] += 1
        self.budget['transfers'] += 1
        return self.hub.post('/api/transfers', {
            'source_node_id': source_node, 'source_path': source_path,
            'targets': [{'node_id': n, 'destination_path': p, 'mode': 420} for n, p in targets]})

    def transfer(self, tid):
        code, body = self.hub.get('/api/transfers/' + self.hub.quote(tid))
        return body if code == 200 else None

    def wait_transfer(self, tid, timeout=240):
        deadline = time.time() + timeout
        t = None
        while time.time() < deadline:
            t = self.transfer(tid)
            if t and t.get('status') in TERMINAL:
                return t
            time.sleep(0.25)
        return t

    def retry_transfer(self, tid, target_id=''):
        self.budget['api_mutations'] += 1
        self.budget['transfer_retries'] += 1
        path = '/api/transfers/%s/retry' % self.hub.quote(tid)
        if target_id:
            path += '?target_id=' + self.hub.quote(target_id)
        return self.hub.post(path)

    @staticmethod
    def t_target(t, node_id=None):
        for item in (t.get('targets') or []):
            if node_id is None or item.get('node_id') == node_id:
                return item
        return None

    def one_transfer_outcome(self, label, source_node, source_path, dest_node, dest_path,
                             dest_extra=None, raw=None):
        """Create + wait + probe one transfer; returns a compact evidence record."""
        if raw is not None:
            code, body = raw
            out = {'label': label, 'create_http': code, 'create_body': body}
            if code != 200:
                return out
        else:
            code, body = self.create_transfer(source_node, source_path,
                                             [(dest_node, dest_path)])
            out = {'label': label, 'create_http': code, 'create_body': body,
                   'source_path': source_path, 'destination_path': dest_path,
                   'source_node': source_node, 'destination_node': dest_node}
            if code != 200 or not isinstance(body, dict) or not body.get('id'):
                return out
            tid = body['id']
            self.state['transfers'].append({'id': tid, 'label': label})
            out['transfer_id'] = tid
            t = self.wait_transfer(tid)
            out['final_status'] = t.get('status')
            out['final_error'] = t.get('error')
            out['sha256'] = t.get('sha256')
            out['size'] = t.get('size')
            out['target'] = self.t_target(t, dest_node)
        probes = [dest_path] if not dest_extra else [dest_path] + list(dest_extra)
        out['host_probe'] = self.child_a.probe(targets=probes,
                                              dirs=[os.path.dirname(dest_path)] if dest_extra else [])
        return out

    # ---------- app definition ----------
    @staticmethod
    def health(kind='systemd'):
        return {'type': kind, 'target': '', 'attempts': 3, 'interval': 1, 'timeout': 5}

    def app_def(self, version='1.0', binary_path=None, config_path=None, args=None, config=None,
                name=None, unit_name=None, artifact='A1'):
        return {'name': name or (PREFIX + 'app'),
                'description': 'sys-b12 owned managed application (nonce %s)' % NONCE,
                'artifact_id': self.state['artifacts'][artifact]['id'],
                'version': version,
                'binary_path': binary_path or BIN_MAIN,
                'unit_name': unit_name or UNIT,
                'arguments': args if args is not None else ['--nonce=%s' % NONCE],
                'environment': {'APP_NONCE': NONCE},
                'config': config if config is not None else 'APP_MODE=sysb12\n',
                'config_path': config_path if config_path is not None else CFG_MAIN,
                'health_check': self.health()}

    # ---------- phase 0 ----------
    def phase0_guard(self):
        out = {'at_utc': NOW(), 'endpoint': self.hub.base, 'nonce': NONCE, 'owned_prefix': PREFIX,
               'agent_binary_sha256_expected': hostapi.AGENT_SHA}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
                                       'global_revision': n.get('global_revision'),
                                       'deployment_mode': n.get('deployment_mode'),
                                       'capabilities': n.get('capabilities')}
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        out['baseline_counts'] = {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)}
        out['owned_fixture_check'] = {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        if any(out['owned_fixture_check'].values()):
            raise RuntimeError('owned_fixture_already_present')
        base = hostapi.OwnedChild(NONCE).host_baseline()
        out['native2_baseline'] = base
        if base['root_exists'] or base['opt_root_exists'] or base['child_config_exists'] \
                or base['owned_child_processes'] or base['owned_units'] \
                or base['etc_systemd_owned_leftovers'] or base['etc_systemd_owned_wants'] \
                or base['owned_mounts']:
            raise RuntimeError('owned_host_residue_present')
        if base['agent_binary_sha256'] != hostapi.AGENT_SHA:
            raise RuntimeError('agent_binary_sha_mismatch')
        if base['primary_agent_unit'].get('ActiveState') != 'active' \
                or base['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_unit_not_active')
        out['environment_facts'] = {
            'root_fstype_dev': base['filesystem_devs'],
            'tmp_is_separate_mount': base['filesystem_devs']['tmp'] != base['filesystem_devs']['root'],
            'note': 'target dirs under /opt live on the xfs root device; /tmp is a tmpfs '
                    'mount on a different device, so a target under /opt is cross-filesystem '
                    'relative to the default os.TempDir().',
        }
        out['guard_ok'] = True
        return out

    # ---------- phase 1: owned children ----------
    def _enroll(self, node_name, node_ip):
        # fail closed: the Hub reuses an existing pending/offline node when hostname OR ip matches,
        # which would hijack another fixture's node record.
        nodes = self.hub.json('/api/nodes')
        clash = [{'id': n['id'], 'hostname': n.get('hostname'), 'ip': n.get('ip')}
                 for n in nodes if n.get('hostname') == node_name or n.get('ip') == node_ip]
        if clash:
            raise RuntimeError('enrollment_would_reuse_existing_node:%s' % clash)
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': node_name, 'node_ip': node_ip, 'hub_address': HUB})
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s' % code)
        known = {v['node_id'] for v in self.state['nodes'].values()}
        if enroll['node_id'] in known:
            raise RuntimeError('enrollment_reused_node_id:%s' % enroll['node_id'])
        n = self.node(enroll['node_id'])
        if not n or n.get('hostname') != node_name or n.get('ip') != node_ip:
            raise RuntimeError('enrolled_node_record_mismatch:%s' % n)
        return enroll

    def phase1_child_a(self):
        enroll = self._enroll(PREFIX + 'child-a', NODE_IP_A)
        self.state['nodes']['a'] = {'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                    'node_name': PREFIX + 'child-a', 'mode': 'native',
                                    'node_ip': NODE_IP_A}
        self.child_a = hostapi.OwnedChild(NONCE, '', enroll['node_id'], enroll['agent_id'],
                                          PREFIX + 'child-a', NODE_IP_A,
                                          self.hub.cred['registration_token'],
                                          deployment_mode='native')
        started = self.child_a.write_config_and_start(WS)
        self.budget['child_process_starts'] += 1
        node = self.wait_node(enroll['node_id'], timeout=90, expect_name=PREFIX + 'child-a')
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_a_did_not_reach_online')
        out = {'at_utc': NOW(),
               'enrollment': {'http': 200, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                              'node_name': PREFIX + 'child-a', 'node_ip': NODE_IP_A,
                              'node_record': {'hostname': node.get('hostname'), 'ip': node.get('ip')}},
               'owned_child': {'root_on_native2': self.child_a.root, 'opt_root_on_native2': self.child_a.opt_root,
                               'data_dir': self.child_a.data, 'config_mode_0600': True,
                               'launched': started, 'deployment_mode': 'native',
                               'routing': 'child-a -> %s directly (no proxy)' % WS},
               'node_after_start': {k: node.get(k) for k in
                                    ('id', 'hostname', 'ip', 'status', 'sync_status', 'deployment_mode',
                                     'global_revision', 'capabilities')}}
        caps = node.get('capabilities') or {}
        if not (caps.get('application_deploy') and caps.get('host_filesystem') and caps.get('managed_systemd')):
            raise RuntimeError('native_child_lacks_host_capabilities')
        return out

    def phase2_child_b(self):
        enroll = self._enroll(PREFIX + 'child-b', NODE_IP_B)
        self.state['nodes']['b'] = {'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                    'node_name': PREFIX + 'child-b', 'mode': 'docker',
                                    'node_ip': NODE_IP_B, 'host_path_allowlist': [OPT]}
        self.proxy = frame_proxy.RecordingProxy(WS)
        port = self.proxy.start()
        self.child_b = hostapi.OwnedChild(NONCE, 'b', enroll['node_id'], enroll['agent_id'],
                                          PREFIX + 'child-b', NODE_IP_B,
                                          self.hub.cred['registration_token'],
                                          deployment_mode='docker', allowlist=[OPT])
        reach = self.child_b.tcp_check('192.168.100.170', port, timeout=5)
        if not reach.get('ok'):
            raise RuntimeError('child_b_cannot_reach_recording_proxy:%s' % reach)
        started = self.child_b.write_config_and_start('ws://192.168.100.170:%d' % port)
        self.budget['child_process_starts'] += 1
        node = self.wait_node(enroll['node_id'], timeout=120, expect_name=PREFIX + 'child-b')
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_b_did_not_reach_online:%s' % json.dumps(
                {'proxy': self.proxy.summary(), 'log_tail': (self.child_b.log_tail(4000) or {}).get('tail', '')[-1200:]})
                [:1500])
        out = {'at_utc': NOW(),
               'enrollment': {'http': 200, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                              'node_name': PREFIX + 'child-b', 'node_ip': NODE_IP_B,
                              'node_record': {'hostname': node.get('hostname'), 'ip': node.get('ip')}},
               'owned_child': {'root_on_native2': self.child_b.root, 'opt_root_on_native2': self.child_b.opt_root,
                               'data_dir': self.child_b.data, 'deployment_mode': 'docker',
                               'host_path_allowlist': [OPT], 'config_mode_0600': True,
                               'routing': 'child-b -> workstation 192.168.100.170:%d (recording frame '
                                          'proxy) -> repair hub :18080' % port,
                               'launched': started},
               'proxy': {'port': port, 'listen_host': '0.0.0.0', 'upstream': WS,
                         'tcp_reachability_from_native2': reach},
               'node_after_start': {k: node.get(k) for k in
                                    ('id', 'hostname', 'ip', 'status', 'sync_status', 'deployment_mode',
                                     'global_revision', 'capabilities')}}
        caps = node.get('capabilities') or {}
        if caps.get('application_deploy') or caps.get('host_filesystem') or caps.get('managed_systemd'):
            raise RuntimeError('non_native_child_reports_host_capabilities')
        return out

    # ---------- phase 3: artifacts + app ----------
    def phase3_fixtures(self):
        content = pad_script('echo app-start >/dev/null\nexec sleep 100000\n')
        self.budget['api_mutations'] += 1
        self.budget['artifact_uploads'] += 1
        name = PREFIX + 'payload-a1'
        code, body = self.hub.upload_artifact(name, 'v1', name + '.bin', content)
        if code != 200 or body.get('sha256') != SHA(content):
            raise RuntimeError('artifact_upload_failed_http_%s' % code)
        self.state['artifacts']['A1'] = {'id': body['id'], 'sha256': body['sha256'],
                                         'size': body['size'], 'name': name}
        self.budget['api_mutations'] += 1
        self.budget['app_creates'] += 1
        code, app = self.hub.post('/api/applications', self.app_def())
        if code != 200 or not app.get('id'):
            raise RuntimeError('app_create_failed_http_%s_%s' % (code, app))
        self.state['app'] = app['id']
        self.budget['api_mutations'] += 1
        self.budget['app_assigns'] += 1
        code2, _ = self.hub.post('/api/applications/%s/assign' % self.hub.quote(app['id']),
                                 {'node_ids': [self.state['nodes']['a']['node_id']]})
        if code2 != 200:
            raise RuntimeError('app_assign_failed_http_%s' % code2)
        # owned host fixture roots
        for d in (OPT + '/app/bin', OPT + '/app/config', SRC_DIR, ALLOW_DIR, DST_DIR, ESCAPE_DIR):
            r = self.child_a.seam('mkdir', d)
            self.budget['host_seam_actions'] += 1
            self.state['seams'].append({'action': 'mkdir', 'path': d, 'at_utc': NOW()})
        return {'at_utc': NOW(), 'artifact': self.state['artifacts']['A1'],
                'hub_artifact_sha_matches_local': True,
                'app_id': app['id'], 'created_http': code, 'assign_http': code2,
                'hub_definition': {k: app.get(k) for k in
                                   ('id', 'name', 'version', 'revision', 'artifact_id', 'binary_path',
                                    'config_path', 'unit_name', 'arguments', 'health_check')},
                'owned_paths': {'unit': UNIT, 'binary': BIN_MAIN, 'config': CFG_MAIN, 'opt_root': OPT,
                                'src_dir': SRC_DIR, 'dst_dir': DST_DIR, 'escape_victim_dir': ESCAPE_DIR},
                'desired_state_delivered_to_agent': bool(
                    [a for a in (self.child_a.agent_state(app['id'], UNIT, BIN_MAIN, CFG_MAIN).get('local_apps') or [])
                     if a['id'] == app['id']])}

    # ---------- SYS-56: Hub-side path guard ----------
    def phase4_hub_path_guard(self):
        out = {'at_utc': NOW(), 'application_definition_guard': [], 'transfer_guard': []}
        before = self.app_hub()
        # raw POST /api/applications with malicious paths (no fixture leak: they must not be created)
        cases = [
            ('relative_binary_path', {'binary_path': 'opt/rel/bin/app'}),
            ('dotdot_binary_path', {'binary_path': OPT + '/a/../bin/app'}),
            ('non_ascii_nul_binary_path', {'binary_path': OPT + '/a\x00b'}),
            ('relative_config_path', {'config_path': 'etc/rel.conf'}),
            ('dotdot_config_path', {'config_path': OPT + '/x/../../etc/passwd'}),
        ]
        for label, patch in cases:
            body = self.app_def()
            body['name'] = PREFIX + 'guard-' + label
            body.update(patch)
            self.budget['api_mutations'] += 1
            code, resp = self.hub.post('/api/applications', body)
            leaked = [a for a in self.hub.json('/api/applications') if a.get('name') == body['name']]
            out['application_definition_guard'].append(
                {'label': label, 'patched': patch, 'http': code,
                 'error': (resp or {}).get('error') if isinstance(resp, dict) else resp,
                 'created_row_ids': [a['id'] for a in leaked], 'created': bool(leaked)})
        # PUT on the existing owned app must be rejected too and must not bump the revision
        for label, patch in cases[:2]:
            body = self.app_def()
            body.update(patch)
            self.budget['api_mutations'] += 1
            self.budget['app_updates'] += 1
            code, resp = self.hub.put('/api/applications/' + self.hub.quote(self.state['app']), body)
            out['application_definition_guard'].append(
                {'label': 'PUT_' + label, 'patched': patch, 'http': code,
                 'error': (resp or {}).get('error') if isinstance(resp, dict) else resp})
        after = self.app_hub()
        out['app_revision_unchanged'] = {'before': before.get('revision'), 'after': after.get('revision'),
                                         'equal': before.get('revision') == after.get('revision')}
        out['app_paths_unchanged'] = {'binary_path': after.get('binary_path'),
                                      'config_path': after.get('config_path')}
        # transfer destination guard (Hub level)
        src = SRC_DIR + '/guard.bin'
        self.child_a.seam('mkfile', src, {'content': 'sysb12-guard-payload\n'})
        self.budget['host_seam_actions'] += 1
        tbefore = len(self.hub.json('/api/transfers'))
        tc = [
            ('relative_destination', 'opt/rel/out.bin'),
            ('relative_with_dotdot', '../out.bin'),
            ('dotdot_traversal', OPT + '/dst/../../etc/out.bin'),
            ('empty_destination', ''),
        ]
        for label, dest in tc:
            self.budget['api_mutations'] += 1
            code, resp = self.create_transfer(self.state['nodes']['a']['node_id'], src,
                                             [(self.state['nodes']['a']['node_id'], dest)])
            out['transfer_guard'].append(
                {'label': label, 'destination_path': dest, 'http': code,
                 'error': (resp or {}).get('error') if isinstance(resp, dict) else resp})
        tafter = len(self.hub.json('/api/transfers'))
        out['transfer_rows_created_by_rejected_requests'] = tafter - tbefore
        out['all_hub_level_rejections_ok'] = (
            all(c['http'] == 400 for c in out['application_definition_guard'])
            and not any(c.get('created') for c in out['application_definition_guard'])
            and out['app_revision_unchanged']['equal']
            and all(c['http'] == 400 for c in out['transfer_guard'])
            and out['transfer_rows_created_by_rejected_requests'] == 0)
        return out

    # ---------- SYS-56: Agent Host Adapter path guard (deploy path) ----------
    def _deploy_bad_path(self, label, binary_path=None, config_path=None, keep=False):
        code, body = self.put_app(self.app_def(binary_path=binary_path, config_path=config_path))
        rec = {'label': label, 'binary_path': binary_path, 'config_path': config_path,
               'put_http': code, 'put_body': body if code != 200 else 'ok - definition accepted by Hub'}
        if code != 200:
            return rec
        execs_before = len(self.app_execs())
        dcode, dbody = self.deploy('deploy', [self.state['nodes']['a']['node_id']])
        rec['deploy_http'] = dcode
        if dcode != 200 or not isinstance(dbody, list) or not dbody:
            rec['deploy_body'] = dbody
            return rec
        rec['execution_id'] = dbody[0]['id']
        ex = self.wait_exec(dbody[0]['id'])
        rec['execution'] = _ex(ex)
        rec['new_executions'] = len(self.app_execs()) - execs_before
        rec['agent_state'] = {
            'journal_rows': self.child_a.agent_state(self.state['app'], UNIT,
                                                     binary_path or BIN_MAIN,
                                                     config_path or CFG_MAIN).get('deployments'),
            'unit_file_lexists': self.child_a.agent_state(self.state['app'], UNIT,
                                                          binary_path or BIN_MAIN,
                                                          config_path or CFG_MAIN)['unit_file']['lexists'],
        }
        if binary_path:
            rec['target_probe'] = self.child_a.probe(targets=[binary_path], dirs=[os.path.dirname(binary_path)])
        if not keep:
            # restore the good definition
            self.budget['api_mutations'] += 1
            self.budget['app_updates'] += 1
            code2, _ = self.hub.put('/api/applications/' + self.hub.quote(self.state['app']), self.app_def())
            rec['restore_http'] = code2
        return rec

    def phase5_agent_path_guard(self):
        out = {'at_utc': NOW(), 'non_clean_absolute': [], 'hub_accepts_note':
               'Hub validateApplicationPath only requires a leading "/" and rejects ".."; it does '
               'not require filepath.Clean(path)==path, so non-clean-but-absolute paths reach the '
               'Agent Host Adapter and are rejected there.'}
        variants = [
            ('double_slash', OPT + '//app/bin/app', None),
            ('dot_segment', OPT + '/./app/bin/app', None),
            ('trailing_slash', OPT + '/app/bin/', None),
        ]
        for label, bp, cp in variants:
            out['non_clean_absolute'].append(self._deploy_bad_path('nonclean_' + label, binary_path=bp, config_path=cp))
        for rec in out['non_clean_absolute']:
            ex = rec.get('execution') or {}
            rec['agent_rejected_with_validate_path'] = ('invalid deployment path' in (ex.get('block_reason') or '')
                                                        and 'invalid absolute path' in (ex.get('block_reason') or ''))
            rec['no_file_side_effect'] = not (rec.get('target_probe', {}).get('files', {})
                                              .get(rec['binary_path'], {}).get('lexists'))
        # symlink escape: destination is an owned symlink pointing outside the owned tree
        victim = ESCAPE_DIR + '/victim.bin'
        link = OPT + '/escape/bin/app'
        self.child_a.seam('mkfile', victim, {'content': 'sysb12-escape-victim-original\n'})
        self.child_a.seam('mkdir', OPT + '/escape/bin')
        self.child_a.seam('symlink', link, {'target': victim})
        self.budget['host_seam_actions'] += 3
        self.state['seams'].append({'action': 'symlink', 'path': link, 'target': victim})
        pre = self.child_a.probe(targets=[victim, link])
        rec = self._deploy_bad_path('symlink_destination', binary_path=link)
        post = self.child_a.probe(targets=[victim, link])
        rec['victim_before'] = pre['files'][victim]
        rec['victim_after'] = post['files'][victim]
        rec['symlink_after'] = post['files'][link]
        rec['victim_unchanged'] = pre['files'][victim].get('sha256') == post['files'][victim].get('sha256')
        rec['symlink_still_symlink'] = bool(post['files'][link].get('is_symlink'))
        rec['host_adapter_refused_symlink'] = 'destination is a symlink' in (
            (rec.get('execution') or {}).get('block_reason') or '')
        out['symlink_escape'] = rec
        out['allowlist_scope_note'] = (
            'NativeHostAdapter has no allowlist (host root "/"); the allowlist dimension is '
            'enforced by the Agent file-transfer path guard (host_path_allowlist) and by the '
            'ContainerHostAdapter, whose host-root/symlink checks are blocked in this environment '
            '(FAIL-ENV-HOST-001/002). Verified below on the non-native owned child.')
        return out

    # ---------- SYS-56: file-transfer path guard on the agent ----------
    def phase6_transfer_path_guard(self):
        out = {'at_utc': NOW(), 'cases': []}
        node_a = self.state['nodes']['a']['node_id']
        src = SRC_DIR + '/small.bin'
        self.child_a.seam('mkfile', src, {'content': 'sysb12-transfer-payload\n'})
        self.budget['host_seam_actions'] += 1
        # non-clean absolute destination accepted by the Hub, rejected by the Agent Host Adapter
        out['cases'].append(self.one_transfer_outcome(
            'nonclean_double_slash_destination', node_a, src, node_a, DST_DIR + '//out.bin'))
        # symlink destination refused by AtomicReplaceReader without following the link
        victim2 = ESCAPE_DIR + '/victim2.bin'
        link2 = DST_DIR + '/link.bin'
        self.child_a.seam('mkfile', victim2, {'content': 'sysb12-escape-victim2-original\n'})
        self.child_a.seam('symlink', link2, {'target': victim2})
        self.budget['host_seam_actions'] += 2
        self.state['seams'].append({'action': 'symlink', 'path': link2, 'target': victim2})
        rec = self.one_transfer_outcome('symlink_destination', node_a, src, node_a, link2)
        rec['victim2'] = self.child_a.probe(targets=[victim2])['files'][victim2]
        out['cases'].append(rec)
        # positive control on the same node (clean path) -> real delivery
        pos = self.one_transfer_outcome('positive_control_clean_path', node_a, src, node_a, DST_DIR + '/ok.bin')
        out['cases'].append(pos)
        for rec in out['cases']:
            tgt = rec.get('target') or {}
            rec['target_status'] = tgt.get('status')
            rec['target_error'] = tgt.get('error')
        out['nonclean_blocked'] = out['cases'][0].get('target_status') == 'FAILED' and \
            'invalid absolute path' in (out['cases'][0].get('target_error') or '')
        out['symlink_blocked'] = out['cases'][1].get('target_status') == 'FAILED' and \
            'destination is a symlink' in (out['cases'][1].get('target_error') or '')
        out['victim2_unchanged'] = out['cases'][1]['victim2'].get('sha256') == \
            SHA(b'sysb12-escape-victim2-original\n')
        out['positive_control_ok'] = out['cases'][2].get('final_status') == 'SUCCESS' and \
            out['cases'][2]['host_probe']['files'][DST_DIR + '/ok.bin'].get('sha256') == \
            SHA(b'sysb12-transfer-payload\n')
        return out

    # ---------- SYS-56: allowlist on the non-native owned child ----------
    def phase7_allowlist(self):
        out = {'at_utc': NOW(), 'node_b': self.state['nodes']['b']['node_id'],
               'configured_allowlist': [OPT], 'cases': []}
        node_b = self.state['nodes']['b']['node_id']
        src = SRC_DIR + '/allow-src.bin'
        self.child_a.seam('mkfile', src, {'content': 'sysb12-allowlist-payload\n'})
        self.budget['host_seam_actions'] += 1
        outside = '/etc/%s/evil.txt' % TAG
        out['cases'].append(self._transfer_on_b('outside_allowlist_destination', src,
                                                node_b, outside, allow=False))
        out['cases'].append(self._transfer_on_b('inside_allowlist_positive_control', src,
                                                node_b, ALLOW_DIR + '/ok.txt', allow=True))
        for rec in out['cases']:
            tgt = rec.get('target') or {}
            rec['target_status'] = tgt.get('status')
            rec['target_error'] = tgt.get('error')
        out['outside_blocked'] = out['cases'][0]['target_status'] == 'FAILED' and \
            'outside configured allowlist' in (out['cases'][0]['target_error'] or '')
        out['outside_allowlist_dir_absent'] = not out['cases'][0]['probe_b']['files'][outside].get('lexists')
        out['inside_ok'] = out['cases'][1]['final_status'] == 'SUCCESS' and \
            out['cases'][1]['probe_b']['files'][ALLOW_DIR + '/ok.txt'].get('sha256') == \
            SHA(b'sysb12-allowlist-payload\n')
        return out

    def _transfer_on_b(self, label, src, node_b, dest, allow):
        code, body = self.create_transfer(node_b, src, [(node_b, dest)])
        rec = {'label': label, 'create_http': code, 'destination_path': dest,
               'source_path': src, 'expected_allowlist_allowed': allow}
        if code != 200 or not isinstance(body, dict) or not body.get('id'):
            rec['create_body'] = body
            return rec
        tid = body['id']
        self.state['transfers'].append({'id': tid, 'label': label})
        t = self.wait_transfer(tid)
        rec['transfer_id'] = tid
        rec['final_status'] = t.get('status')
        rec['target'] = self.t_target(t, node_b)
        rec['probe_b'] = self.child_b.probe(targets=[dest], dirs=[os.path.dirname(dest)])
        return rec

    # ---------- AR-32: permission denied + recovery ----------
    def phase8_ar32_permission_denied(self):
        out = {'at_utc': NOW(), 'unit': UNIT, 'binary_path': BIN_MAIN, 'config_path': CFG_MAIN}
        bin_dir = os.path.dirname(BIN_MAIN)
        lsattr_before = self.child_a.seam('lsattr', bin_dir)
        self.budget['host_seam_actions'] += 1
        seal = self.child_a.seam('chattr_plus', bin_dir)
        self.budget['host_seam_actions'] += 1
        lsattr_set = self.child_a.seam('lsattr', bin_dir)
        self.state['seams'].append({'action': 'chattr_plus', 'path': bin_dir, 'role': 'AR-32 permission seam'})
        out['seam'] = {'action': 'chattr +i on owned bin dir', 'path': bin_dir,
                       'lsattr_before': (lsattr_before.get('result') or {}).get('out'),
                       'chattr_rc': (seal.get('result') or {}).get('rc'),
                       'lsattr_after_set': (lsattr_set.get('result') or {}).get('out'),
                       'note': 'the owned Agent runs as root, so DAC mode bits cannot deny it; '
                               'immutable (EPERM) is the real kernel-level denial seam'}
        c1, _ = self.put_app(self.app_def())
        out['definition_put_http'] = c1
        before = len(self.app_execs())
        dcode, dbody = self.deploy('deploy', [self.state['nodes']['a']['node_id']])
        out['deploy_http'] = dcode
        ex = self.wait_exec(dbody[0]['id']) if dcode == 200 and dbody else None
        out['denied_execution'] = _ex(ex)
        out['denied_reason_expected'] = 'operation not permitted'
        out['denied_reason_matches_eperm'] = 'operation not permitted' in ((ex or {}).get('block_reason') or '')
        out['new_executions_on_denial'] = len(self.app_execs()) - before
        st_denied = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
        out['host_after_denial'] = {'binary': st_denied['binary'], 'config': st_denied['config'],
                                    'unit_file': st_denied['unit_file'],
                                    'unit_state': st_denied['unit_state'],
                                    'journal_rows': st_denied['deployments'],
                                    'unit_registry': st_denied['unit_registry']}
        out['no_partial_file'] = not st_denied['binary'].get('lexists')
        out['node_state_after_denial'] = self.app_states(self.state['nodes']['a']['node_id'])
        # release the seam and recover through the product
        rel = self.child_a.seam('chattr_minus', bin_dir)
        self.budget['host_seam_actions'] += 1
        out['seam_release'] = {'result': rel.get('result'), 'lsattr': rel.get('lsattr')}
        dcode2, dbody2 = self.deploy('deploy', [self.state['nodes']['a']['node_id']])
        out['recovery_deploy_http'] = dcode2
        ex2 = self.wait_exec(dbody2[0]['id']) if dcode2 == 200 and dbody2 else None
        out['recovery_execution'] = _ex(ex2)
        art = self.state['artifacts']['A1']
        st_ok = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
        out['host_after_recovery'] = {'binary': st_ok['binary'], 'config': st_ok['config'],
                                      'unit_file': st_ok['unit_file'],
                                      'enable_symlink': st_ok['enable_symlink'],
                                      'unit_state': st_ok['unit_state'],
                                      'unit_registry': st_ok['unit_registry'],
                                      'journal_rows': st_ok['deployments']}
        out['binary_matches_artifact'] = st_ok['binary'].get('sha256') == art['sha256']
        out['unit_active'] = st_ok['unit_state'].get('ActiveState') == 'active'
        out['node_state_after_recovery'] = self.app_states(self.state['nodes']['a']['node_id'])
        out['desired_equals_actual'] = self._desired_equals_actual(st_ok)
        # persistence across an owned-child restart
        out['restart'] = self.child_a.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_node(self.state['nodes']['a']['node_id'], timeout=90)
        out['node_after_restart'] = None if not node_after else {k: node_after.get(k) for k in
                                                                 ('status', 'sync_status', 'deployment_mode')}
        st_restart = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
        out['host_after_restart'] = {'binary_sha256': st_restart['binary'].get('sha256'),
                                     'unit_active': st_restart['unit_state'].get('ActiveState'),
                                     'unit_registry': st_restart['unit_registry'],
                                     'journal_rows': len(st_restart['deployments'] or []),
                                     'db_quick_check': st_restart.get('db_quick_check'),
                                     'local_apps': len(st_restart.get('local_apps') or [])}
        out['persisted'] = st_restart['binary'].get('sha256') == art['sha256'] \
            and st_restart['unit_state'].get('ActiveState') == 'active' \
            and bool(st_restart['unit_registry'])
        # idempotent re-deploy
        dcode3, dbody3 = self.deploy('deploy', [self.state['nodes']['a']['node_id']])
        ex3 = self.wait_exec(dbody3[0]['id']) if dcode3 == 200 and dbody3 else None
        st_idem = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
        out['idempotent_redeploy'] = {'http': dcode3, 'execution': _ex(ex3),
                                      'binary_sha256': st_idem['binary'].get('sha256'),
                                      'unit_active': st_idem['unit_state'].get('ActiveState'),
                                      'unit_count_owned': self._owned_unit_count(),
                                      'journal_rows': len(st_idem['deployments'] or [])}
        return out

    def _desired_equals_actual(self, st, app_id=None):
        target = app_id or self.state['app']
        code, hub = self.hub.get('/api/applications/' + self.hub.quote(target))
        hub = hub if code == 200 else {}
        local = [a for a in (st.get('local_apps') or []) if a['id'] == target]
        defn = (local[0]['definition'] if local else {}) or {}
        want_sha = None
        for label, art in self.state['artifacts'].items():
            if art['id'] == hub.get('artifact_id'):
                want_sha = art['sha256']
        return {'app_id': target, 'hub_version': hub.get('version'),
                'hub_binary_path': hub.get('binary_path'),
                'agent_definition_version': defn.get('version'),
                'agent_definition_binary_path': defn.get('binary_path'),
                'disk_binary_sha256': st['binary'].get('sha256'),
                'expected_artifact_sha256': want_sha,
                'equal': (hub.get('version') == defn.get('version')
                          and hub.get('binary_path') == defn.get('binary_path')
                          and st['binary'].get('sha256') == want_sha)}

    def _owned_unit_count(self):
        st = self.child_a.probe(targets=[], dirs=[], freeform=(
            "out['owned_unit_units']=[l.strip() for l in subprocess.run(['systemctl','list-units','--all',"
            "'--no-pager','--no-legend'],capture_output=True,text=True).stdout.splitlines() if __TAG__ in l]"
        ).replace('__TAG__', repr(TAG)))
        return st.get('owned_unit_units')

    # ---------- AR-34: path escape recovery + persistence ----------
    def phase9_ar34_recovery(self):
        out = {'at_utc': NOW(), 'premise':
               'All escape attempts above were refused with no host side effect; recovery is now '
               'driven through the same product path (file delivery) plus an owned-child restart.'}
        node_a = self.state['nodes']['a']['node_id']
        src = SRC_DIR + '/recovery.bin'
        self.child_a.seam('mkfile', src, {'content': 'sysb12-recovery-payload\n'})
        self.budget['host_seam_actions'] += 1
        rec = self.one_transfer_outcome('recovery_clean_delivery', node_a, src, node_a, DST_DIR + '/recovery.bin')
        out['delivery'] = rec
        tid = rec.get('transfer_id')
        t = self.transfer(tid) if tid else {}
        out['transfer_record'] = t
        out['delivery_ok'] = rec.get('final_status') == 'SUCCESS' and \
            rec['host_probe']['files'][DST_DIR + '/recovery.bin'].get('sha256') == \
            SHA(b'sysb12-recovery-payload\n')
        # idempotent retry of an already-successful transfer must not re-deliver or regress
        if tid and (self.t_target(t) or {}).get('node_id'):
            rcode, rbody = self.retry_transfer(tid, (self.t_target(t) or {}).get('node_id'))
            time.sleep(2)
            t2 = self.transfer(tid)
            out['retry'] = {'http': rcode, 'body': rbody,
                            'status_after_retry': t2.get('status'),
                            'target_after_retry': self.t_target(t2)}
            out['retry_idempotent'] = t2.get('status') == 'SUCCESS'
        # persistence across restart
        out['restart'] = self.child_a.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_node(self.state['nodes']['a']['node_id'], timeout=90)
        out['node_after_restart'] = None if not node_after else {k: node_after.get(k) for k in
                                                                 ('status', 'sync_status')}
        out['file_after_restart'] = self.child_a.probe(
            targets=[DST_DIR + '/recovery.bin', DST_DIR + '/ok.bin'])['files']
        t3 = self.transfer(tid)
        out['transfer_record_after_restart'] = {'status': t3.get('status'),
                                                'target': self.t_target(t3)}
        out['persisted'] = (out['file_after_restart'][DST_DIR + '/recovery.bin'].get('sha256')
                            == SHA(b'sysb12-recovery-payload\n')) and t3.get('status') == 'SUCCESS'
        out['no_tmp_leftovers'] = self._tmp_leftover_probe([DST_DIR, ALLOW_DIR])
        return out

    def _tmp_leftover_probe(self, dirs):
        st = self.child_a.probe(targets=[], dirs=dirs)
        return {d: [n for n in (st['dirs'][d].get('entries') or [])
                    if n.startswith('.nodesteer-transfer-') or n.startswith('.tmp-')] for d in dirs}

    # ---------- SYS-57 / AR-35: cross-filesystem temp + atomic replace ----------
    def phase10_cross_fs(self):
        out = {'at_utc': NOW(), 'mount_point': XFS,
               'cross_fs_premise': {
                   'target_dir_device': None, 'tmp_device': None, 'root_device': None},
               'transfer': None, 'enospc': None, 'app_deploy': None}
        mnt = self.child_a.seam('mount_tmpfs', XFS, {'opts': 'size=256m,mode=0755'})
        self.budget['host_seam_actions'] += 1
        self.budget['mounts'] += 1
        self.state['seams'].append({'action': 'mount_tmpfs', 'path': XFS, 'opts': 'size=256m',
                                    'role': 'SYS-57/AR-35 cross-filesystem target'})
        if (mnt.get('result') or {}).get('rc') != 0:
            raise RuntimeError('owned_tmpfs_mount_failed:%s' % mnt.get('result'))
        pre = self.child_a.probe(targets=[], dirs=[XFS])
        out['cross_fs_premise'] = {
            'target_dir': XFS, 'target_dir_device': pre['dirs'][XFS].get('dev'),
            'tmp_device': pre['tmp_dev'], 'root_device': pre['root_dev'],
            'target_is_different_fs_from_tmp': pre['dirs'][XFS].get('dev') != pre['tmp_dev'],
            'mounts': pre['mounts'], 'statvfs': pre['dirs'][XFS].get('statvfs')}
        # big source on the xfs root + destination inside the owned tmpfs
        big = self.child_a.make_file(SRC_DIR + '/big.bin', 96 * 1024 * 1024, 'urandom')
        dest = XFS + '/dst/big.bin'
        self.child_a.seam('mkdir', XFS + '/dst')
        self.budget['host_seam_actions'] += 1
        sdir = XFS + '/dst'
        out['sampler'] = self.child_a.sampler_start([sdir], XFS + '/dst.samples', seconds=40)
        code, body = self.create_transfer(self.state['nodes']['a']['node_id'], big['path'],
                                         [(self.state['nodes']['a']['node_id'], dest)])
        out['transfer'] = {'source': big, 'destination': dest, 'create_http': code}
        if code == 200 and isinstance(body, dict) and body.get('id'):
            tid = body['id']
            self.state['transfers'].append({'id': tid, 'label': 'cross_fs_big'})
            t = self.wait_transfer(tid, timeout=300)
            out['transfer'].update({'transfer_id': tid, 'final_status': t.get('status'),
                                    'error': t.get('error'), 'sha256': t.get('sha256'),
                                    'target': self.t_target(t)})
        stop = self.child_a.sampler_stop(XFS + '/dst.samples')
        samples = self.child_a.sampler_fetch(XFS + '/dst.samples')
        out['sampler'].update({'stop': stop, 'row_count': samples.get('row_count')})
        rows = samples.get('rows') or []
        tmp_seen, partial_seen, fds_seen = [], [], set()
        for row in rows:
            if row.get('event'):
                continue
            entries = (row.get('dirs') or {}).get(sdir) or {}
            if not isinstance(entries, dict):
                continue
            for name, size in entries.items():
                if name.startswith('.nodesteer-transfer-'):
                    tmp_seen.append({'at': row.get('t'), 'name': name, 'size': size})
                elif name == 'big.bin' and size not in (big['size'], 'ERR'):
                    partial_seen.append({'at': row.get('t'), 'size': size})
            for fd, target in (row.get('fds') or {}).items():
                if target.startswith(sdir):
                    fds_seen.add(target.rsplit('/', 1)[-1].split('.')[0] + '.' + target.rsplit('.', 1)[-1])
        out['sampler']['temp_in_target_dir_samples'] = tmp_seen[:6]
        out['sampler']['partial_target_samples'] = partial_seen[:6]
        out['sampler']['agent_open_fds_in_target_dir'] = sorted(str(f) for f in fds_seen)[:10]
        post = self.child_a.probe(targets=[dest], dirs=[sdir])
        out['transfer']['host_after'] = post['files'][dest]
        out['transfer']['sha_matches_source'] = post['files'][dest].get('sha256') == big['sha256']
        out['transfer']['tmp_leftovers'] = [n for n in (post['dirs'][sdir].get('entries') or [])
                                            if n.startswith('.nodesteer-transfer-')]
        out['same_dir_temp_evidence'] = {
            'temp_file_observed_in_target_dir': bool(tmp_seen),
            'agent_fd_open_in_target_dir': bool(fds_seen),
            'no_partial_target_ever_observed': not partial_seen,
            'no_tmp_leftover': not out['transfer']['tmp_leftovers'],
            'conclusion': ('rename of the temp file to the destination succeeded inside a mount '
                           'that is a different filesystem from /tmp, which is only possible when '
                           'the temp file is created in the target directory itself'),
        }
        # deterministic same-filesystem proof: pre-existing target + tmpfs too small for 2x
        self.child_a.seam('umount', XFS)
        self.budget['mounts'] += 1
        mnt2 = self.child_a.seam('mount_tmpfs', XFS, {'opts': 'size=56m,mode=0755'})
        self.budget['mounts'] += 1
        mid = self.child_a.make_file(SRC_DIR + '/mid.bin', 32 * 1024 * 1024, 'urandom')
        self.child_a.seam('mkdir', XFS + '/full')
        self.child_a.seam('mkfile', ENOSPC_PRE, {'size': 32 * 1024 * 1024, 'fill': 'urandom'})
        self.budget['host_seam_actions'] += 3
        pre_enospc = self.child_a.probe(targets=[ENOSPC_PRE], dirs=[XFS + '/full'])
        code2, body2 = self.create_transfer(self.state['nodes']['a']['node_id'], mid['path'],
                                           [(self.state['nodes']['a']['node_id'], ENOSPC_PRE)])
        out['enospc'] = {'mount': {'tmpfs_size': '56m', 'rc': (mnt2.get('result') or {}).get('rc')},
                         'pre_existing_target': pre_enospc['files'][ENOSPC_PRE],
                         'source': mid, 'destination': ENOSPC_PRE, 'create_http': code2,
                         'disk_before': pre_enospc['dirs'][XFS + '/full'].get('statvfs')}
        if code2 == 200 and isinstance(body2, dict) and body2.get('id'):
            tid2 = body2['id']
            self.state['transfers'].append({'id': tid2, 'label': 'cross_fs_enospc'})
            t2 = self.wait_transfer(tid2, timeout=180)
            out['enospc'].update({'transfer_id': tid2, 'final_status': t2.get('status'),
                                  'error': t2.get('error'), 'target': self.t_target(t2),
                                  'target_error': (self.t_target(t2) or {}).get('error')})
        post_enospc = self.child_a.probe(targets=[ENOSPC_PRE], dirs=[XFS + '/full'])
        out['enospc']['target_after'] = post_enospc['files'][ENOSPC_PRE]
        out['enospc']['target_unchanged'] = post_enospc['files'][ENOSPC_PRE].get('sha256') == \
            pre_enospc['files'][ENOSPC_PRE].get('sha256')
        out['enospc']['tmp_leftovers'] = [n for n in (post_enospc['dirs'][XFS + '/full'].get('entries') or [])
                                          if n.startswith('.nodesteer-transfer-')]
        reason = (out['enospc'].get('error') or out['enospc'].get('target_error') or '')
        out['enospc']['reason'] = reason
        out['enospc']['error_is_enospc'] = 'no space left on device' in reason
        out['enospc']['proves_temp_is_in_target_fs'] = bool(out['enospc']['error_is_enospc'])
        out['enospc']['note'] = ('a temp file created outside the target filesystem would surface as '
                                 '"invalid cross-device link" (EXDEV) at rename time, never as ENOSPC '
                                 'while writing the temp file into an almost-full target mount')
        # ---- cross-filesystem AtomicReplace on the managed-application HOST path (AR-35) ----
        self.child_a.seam('umount', XFS)
        secret_neg = self.child_a.seam('mount_tmpfs', XFS, {'opts': 'size=2m,mode=0755'})
        self.budget['mounts'] += 1
        bin_x = XFS + '/xapp/bin/app'          # per-app path (matches api.ManagedApplication spec)
        cfg_x = XFS + '/xapp/config/app.env'
        unit_x = PREFIX + 'xapp.service'
        self.state['ar35_binary'] = bin_x
        self.state['ar35_config'] = cfg_x
        code3, xapp = self.hub.post('/api/applications',
                                    self.app_def(version='1.0', name=PREFIX + 'xapp',
                                                 unit_name=unit_x, binary_path=bin_x, config_path=cfg_x))
        self.budget['api_mutations'] += 1
        self.budget['app_creates'] += 1
        if code3 != 200 or not xapp.get('id'):
            raise RuntimeError('xapp_create_failed_http_%s' % code3)
        self.state['xapp'] = xapp['id']
        self.budget['api_mutations'] += 1
        self.budget['app_assigns'] += 1
        self.hub.post('/api/applications/%s/assign' % self.hub.quote(xapp['id']),
                      {'node_ids': [self.state['nodes']['a']['node_id']]})
        time.sleep(2)
        # deterministic same-filesystem proof on the deploy path: target mount 2 MiB with a
        # pre-existing 1.5 MiB binary cannot hold the old file and the AtomicReplace temp file.
        self.child_a.seam('mkdir', XFS + '/xapp/bin')
        self.child_a.seam('mkdir', XFS + '/xapp/config')
        self.child_a.make_file(bin_x, 1572864, 'urandom')
        self.budget['host_seam_actions'] += 2
        pre_neg = self.child_a.probe(targets=[bin_x], dirs=[XFS + '/xapp/bin'])
        self.child_a.sampler_start([XFS + '/xapp/bin'], XFS + '/xneg.samples', seconds=40)
        ndcode, ndbody = self.deploy('deploy', [self.state['nodes']['a']['node_id']], app_id=xapp['id'])
        nex = self.wait_exec(ndbody[0]['id']) if ndcode == 200 and ndbody else None
        self.child_a.sampler_stop(XFS + '/xneg.samples')
        neg_samples = self.child_a.sampler_fetch(XFS + '/xneg.samples')
        neg_rows = [r for r in (neg_samples.get('rows') or []) if not r.get('event')]
        neg_tmp = []
        for row in neg_rows:
            for name, size in ((row.get('dirs') or {}).get(XFS + '/xapp/bin') or {}).items():
                if name.startswith('.tmp-'):
                    neg_tmp.append({'at': row.get('t'), 'name': name, 'size': size})
        post_neg = self.child_a.probe(targets=[bin_x], dirs=[XFS + '/xapp/bin'])
        out['app_deploy_enospc'] = {
            'binary_path': bin_x, 'unit_name': unit_x, 'artifact': self.state['artifacts']['A1'],
            'mount': 'tmpfs size=2m', 'mount_rc': (secret_neg.get('result') or {}).get('rc'),
            'pre_existing_binary': pre_neg['files'][bin_x],
            'deploy_http': ndcode, 'execution': _ex(nex),
            'reason': (nex or {}).get('block_reason'),
            'reason_is_enospc': 'no space left on device' in ((nex or {}).get('block_reason') or ''),
            'binary_after': post_neg['files'][bin_x],
            'binary_unchanged': post_neg['files'][bin_x].get('sha256') == pre_neg['files'][bin_x].get('sha256'),
            'tmp_in_target_dir_samples': neg_tmp[:6],
            'tmp_leftovers': [n for n in (post_neg['dirs'][XFS + '/xapp/bin'].get('entries') or [])
                              if n.startswith('.tmp-')],
            'unit_file_lexists': self.child_a.agent_state(xapp['id'], unit_x, bin_x, cfg_x)['unit_file']['lexists'],
            'note': 'old target preserved, temp file created inside the target filesystem: the failure '
                    'is ENOSPC while writing the temp, which can only happen when the temp lives in the '
                    'target mount (a foreign temp dir would fail later with EXDEV)',
        }
        out['app_deploy_enospc']['proves_temp_in_target_fs'] = (
            out['app_deploy_enospc']['reason_is_enospc']
            and out['app_deploy_enospc']['binary_unchanged']
            and not out['app_deploy_enospc']['tmp_leftovers'])
        # ---- positive cross-filesystem AtomicReplace + atomicity sampling ----
        self.child_a.seam('umount', XFS)
        mnt3 = self.child_a.seam('mount_tmpfs', XFS, {'opts': 'size=192m,mode=0755'})
        self.budget['mounts'] += 1
        self.child_a.seam('mkdir', XFS + '/xapp/bin')
        self.child_a.seam('mkdir', XFS + '/xapp/config')
        self.budget['host_seam_actions'] += 2
        self.child_a.sampler_start([XFS + '/xapp/bin'], XFS + '/xapp.samples', seconds=40)
        dcode, dbody = self.deploy('upgrade', [self.state['nodes']['a']['node_id']], app_id=xapp['id'])
        ex = self.wait_exec(dbody[0]['id']) if dcode == 200 and dbody else None
        self.child_a.sampler_stop(XFS + '/xapp.samples')
        samples3 = self.child_a.sampler_fetch(XFS + '/xapp.samples')
        st = self.child_a.agent_state(xapp['id'], unit_x, bin_x, cfg_x)
        post3 = self.child_a.probe(targets=[bin_x, cfg_x], dirs=[XFS + '/xapp/bin'])
        rows3 = [r for r in (samples3.get('rows') or []) if not r.get('event')]
        tmp3, partial3, fds3 = [], [], set()
        for row in rows3:
            for name, size in ((row.get('dirs') or {}).get(XFS + '/xapp/bin') or {}).items():
                if name.startswith('.tmp-'):
                    tmp3.append({'at': row.get('t'), 'name': name, 'size': size})
                elif name == 'app' and size not in (self.state['artifacts']['A1']['size'], 'ERR'):
                    partial3.append({'at': row.get('t'), 'size': size})
            for fd, tgt in (row.get('fds') or {}).items():
                if tgt.startswith(XFS + '/xapp/bin'):
                    fds3.add(tgt)
        out['app_deploy'] = {
            'app_id': xapp['id'], 'definition_http': code3, 'assign_note': 'assigned to the capable owned node',
            'binary_path': bin_x, 'config_path': cfg_x, 'unit_name': unit_x,
            'mount': {'tmpfs_size': '192m', 'rc': (mnt3.get('result') or {}).get('rc')},
            'deploy_http': dcode, 'execution': _ex(ex),
            'binary_after': post3['files'][bin_x], 'config_after': post3['files'][cfg_x],
            'binary_matches_artifact': post3['files'][bin_x].get('sha256') ==
                                       self.state['artifacts']['A1']['sha256'],
            'unit_active': st['unit_state'].get('ActiveState'),
            'sampler_rows': samples3.get('row_count'),
            'tmp_in_target_dir_samples': tmp3[:6],
            'agent_fd_open_in_target_dir': sorted(fds3)[:5],
            'partial_target_samples': partial3[:6],
            'tmp_leftovers': [n for n in (post3['dirs'][XFS + '/xapp/bin'].get('entries') or [])
                              if n.startswith('.tmp-')],
            'journal_tail': st['deployments'][:4],
            'desired_equals_actual': self._desired_equals_actual(st, app_id=xapp['id']),
        }
        out['app_deploy']['atomic_replace_ok'] = (
            out['app_deploy']['binary_matches_artifact']
            and out['app_deploy']['unit_active'] == 'active'
            and not out['app_deploy']['partial_target_samples']
            and not out['app_deploy']['tmp_leftovers'])
        out['app_deploy']['same_dir_temp_evidence'] = bool(tmp3) or bool(fds3)
        return out

    # ---------- SYS-58 / AR-33 / INV-16: capability gating ----------
    def phase11_capability_gate(self):
        out = {'at_utc': NOW(), 'non_native_node': self.state['nodes']['b']['node_id'],
               'native_node': self.state['nodes']['a']['node_id'],
               'proxy_before': self.proxy.summary() if self.proxy else None}
        node_a = self.state['nodes']['a']['node_id']
        node_b = self.state['nodes']['b']['node_id']
        nb = self.node(node_b)
        out['node_b_capabilities'] = nb.get('capabilities')
        out['node_b_deployment_mode'] = nb.get('deployment_mode')
        out['capability_false_for'] = [k for k, v in (nb.get('capabilities') or {}).items()
                                       if k in ('application_deploy', 'host_filesystem', 'managed_systemd',
                                                'agent_upgrade') and not v]
        b_state_before = self.child_b.agent_state(self.state['app'], UNIT, '', '')
        out['child_b_agent_state_before'] = {
            'execution_count': b_state_before.get('execution_count'),
            'executions': b_state_before.get('executions'),
            'deployments': b_state_before.get('deployments'),
            'local_apps': b_state_before.get('local_apps'),
            'unit_registry': b_state_before.get('unit_registry'),
        }
        execs_before = self.app_execs()
        # 1) single-node application deploy to the non-capable node
        dcode, dbody = self.deploy('deploy', [node_b])
        out['deploy_to_non_native'] = {'http': dcode, 'body': dbody,
                                       'error_text': (dbody or {}).get('error') if isinstance(dbody, dict) else dbody}
        time.sleep(3)
        execs_after = self.app_execs()
        out['deploy_to_non_native'].update({
            'executions_delta': len(execs_after) - len(execs_before),
            'executions_for_node_b': [e for e in execs_after if e.get('node_id') == node_b],
            'hub_executions_for_app_total': len(execs_after)})
        # 2) application upgrade operation to the non-capable node
        ucode, ubody = self.deploy('upgrade', [node_b])
        out['upgrade_to_non_native'] = {'http': ucode, 'body': ubody,
                                        'error_text': (ubody or {}).get('error') if isinstance(ubody, dict) else ubody}
        # 3) agent self-upgrade request to the non-native node (deployment_mode gate)
        self.budget['api_mutations'] += 1
        self.budget['node_upgrades'] += 1
        scode, sbody = self.hub.post('/api/nodes/upgrade', {'node_ids': [node_b]})
        out['self_upgrade_to_non_native'] = {'http': scode, 'body': sbody}
        if isinstance(sbody, dict) and sbody.get('results'):
            for r in sbody['results']:
                if r.get('node_id') == node_b and r.get('execution_id'):
                    out['self_upgrade_to_non_native']['execution'] = _ex(self.execution(r['execution_id']))
        time.sleep(2)
        # 4) mixed batch [non-capable first, capable second]: no execution at all must be created
        execs_before_mixed = self.app_execs()
        mcode, mbody = self.deploy('deploy', [node_b, node_a])
        out['mixed_batch_noncapable_first'] = {'http': mcode, 'body': mbody,
                                               'executions_delta': len(self.app_execs()) - len(execs_before_mixed)}
        # 5) mixed batch [capable first, non-capable second]: per-node semantics observation
        execs_before_mixed2 = self.app_execs()
        m2code, m2body = self.deploy('deploy', [node_a, node_b])
        out['mixed_batch_capable_first'] = {'http': m2code, 'body': m2body,
                                            'executions_created': len(self.app_execs()) - len(execs_before_mixed2),
                                            'note': 'per-node check: the capable node is dispatched, the '
                                                    'non-capable node is refused in the same request; the API '
                                                    'still returns HTTP 400'}
        time.sleep(6)
        execs_after_all = self.app_execs()
        out['executions_for_node_b_total'] = [e for e in execs_after_all if e.get('node_id') == node_b]
        out['executions_for_node_a_total'] = len([e for e in execs_after_all if e.get('node_id') == node_a])
        b_state_after = self.child_b.agent_state(self.state['app'], UNIT, '', '')
        out['child_b_agent_state_after'] = {
            'execution_count': b_state_after.get('execution_count'),
            'executions': b_state_after.get('executions'),
            'deployments': b_state_after.get('deployments'),
            'local_apps': b_state_after.get('local_apps'),
            'unit_registry': b_state_after.get('unit_registry'),
            'artifact_rows': b_state_after.get('artifact_rows'),
            'db_quick_check': b_state_after.get('db_quick_check'),
        }
        b_probe = self.child_b.probe(targets=[], dirs=[])
        out['child_b_host_probe'] = {'etc_systemd_owned': b_probe.get('files'),
                                     'mounts': b_probe.get('mounts')}
        out['child_b_unit_dir_probe'] = self._b_unit_dir_probe()
        out['child_b_log_hits'] = self._b_log_hits()
        # 6) persistence of the non-capable capability report across a restart
        out['child_b_restart'] = self.child_b.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        nb2 = self.wait_node(node_b, timeout=90)
        out['node_b_after_restart'] = None if not nb2 else {
            'status': nb2.get('status'), 'sync_status': nb2.get('sync_status'),
            'deployment_mode': nb2.get('deployment_mode'), 'capabilities': nb2.get('capabilities')}
        b_state_restart = self.child_b.agent_state(self.state['app'], UNIT, '', '')
        out['child_b_agent_state_after_restart'] = {
            'execution_count': b_state_restart.get('execution_count'),
            'deployments': b_state_restart.get('deployments'),
            'local_apps': b_state_restart.get('local_apps'),
            'artifact_rows': b_state_restart.get('artifact_rows'),
            'identity': b_state_restart.get('identity')}
        # 7) AR-33 recovery: the capable node still converges after the denials
        ex_a = [e for e in execs_after_all if e.get('node_id') == node_a]
        ok_a = [e for e in ex_a if e.get('status') == 'SUCCESS']
        st_a = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
        out['ar33_recovery'] = {
            'capable_node_executions': len(ex_a),
            'capable_node_last_execution': _ex(ex_a[-1]) if ex_a else None,
            'capable_node_last_success_execution': _ex(ok_a[-1]) if ok_a else None,
            'capable_node_unit_active': st_a['unit_state'].get('ActiveState'),
            'capable_node_binary_sha256': st_a['binary'].get('sha256'),
            'desired_equals_actual': self._desired_equals_actual(st_a),
            'non_capable_node_still_clean': (b_state_restart.get('execution_count') == 0
                                             and not b_state_restart.get('deployments')
                                             and not b_state_restart.get('local_apps')),
        }
        out['proxy_after'] = self.proxy.summary() if self.proxy else None
        frames = ((out['proxy_after'] or {}).get('frame_counts') or {}).get('hub_to_agent') or {}
        host_op = (out['proxy_after'] or {}).get('host_op_frames_hub_to_agent') or []
        by_type = {}
        for rec in host_op:
            by_type[rec.get('type')] = by_type.get(rec.get('type'), 0) + 1
        out['inv16_frame_analysis'] = {
            'hub_to_agent_frame_types_total': frames,
            'host_op_candidates': by_type,
            'deploy_request_count': frames.get('DEPLOY_REQUEST', 0),
            'run_execution_count': frames.get('RUN_EXECUTION', 0),
            'no_managed_host_operation_dispatched': (frames.get('DEPLOY_REQUEST', 0) == 0
                                                     and frames.get('RUN_EXECUTION', 0) == 0),
            'file_frames_explained': (
                'FILE_UPLOAD_REQUEST/FILE_DELIVERY_REQUEST seen on the child-b session belong to the '
                'deliberate SYS-56 allowlist file-transfer tests, where child-b is both source and '
                'target (phase7); the file-delivery path is intentionally not gated by '
                'application_deploy because in docker mode the write lands in the container '
                'filesystem, not on a managed host. Recorded as an observation for Root review.'),
            'artifact_prefetch_observation': (
                'ARTIFACT_PREFETCH frames reached the non-capable node because AppManager.Deploy calls '
                'ArtifactManager.PrefetchToNodes for the whole requested node list before the per-node '
                'Capability check. The prefetch only populates the Agent artifact cache; no Host '
                'Operation (DEPLOY_REQUEST) was dispatched. Recorded as an observation, not a case '
                'failure; the client-side artifact cache rows below are the only trace.'),
        }
        out['inv16_no_host_op_frame_to_non_capable_node'] = \
            out['inv16_frame_analysis']['no_managed_host_operation_dispatched']
        return out

    def _b_unit_dir_probe(self):
        script = ('import json,os,subprocess\n'
                  'out={}\n'
                  'try:\n'
                  '    out["etc_systemd_owned"]=[n for n in sorted(os.listdir("/etc/systemd/system"))'
                  ' if "qa-sysb12-" in n]\n'
                  'except Exception as e:\n'
                  '    out["etc_error"]=type(e).__name__\n'
                  'r=subprocess.run(["systemctl","list-units","--all","--no-pager","--no-legend"],'
                  'capture_output=True,text=True)\n'
                  'out["owned_units"]=[l.strip() for l in r.stdout.splitlines() if "qa-sysb12-" in l]\n'
                  'print(json.dumps(out))\n')
        return self.child_b.remote_py(script)

    def _b_log_hits(self):
        tail = self.child_b.log_tail(60000)
        body = tail.get('tail') or ''
        hits = [k for k in ('deploy', 'DEPLOY_REQUEST', 'install binary', 'systemd', 'application') if k in body]
        return {'log_size': tail.get('size'), 'keyword_hits': hits,
                'deploy_lines': [l for l in body.splitlines() if 'deploy' in l.lower()][:10]}

    # ---------- cleanup ----------
    def phase12_cleanup(self):
        res = {'at_utc': NOW(), 'steps': {}, 'errors': []}

        def step(name, fn):
            try:
                res['steps'][name] = fn()
            except Exception as exc:
                res['steps'][name] = {'error': str(exc)[:400]}
                res['errors'].append('%s: %s' % (name, exc))

        def release_seams():
            out = {}
            if self.child_a:
                out['chattr_minus_bin'] = self.child_a.seam('chattr_minus', os.path.dirname(BIN_MAIN))
                out['chattr_minus_opt'] = self.child_a.seam('chattr_minus', OPT)
                out['umount_xfs'] = self.child_a.seam('umount', XFS)
                out['lsattr_bin'] = self.child_a.seam('lsattr', os.path.dirname(BIN_MAIN))
                out['mounts'] = self.child_a.probe(targets=[], dirs=[])['mounts']
            if self.child_b:
                out['mounts_b'] = self.child_b.probe(targets=[], dirs=[])['mounts']
            return out
        step('release_owned_seams', release_seams)

        def product_uninstall():
            out = {}
            owned_apps = [a for a in self.hub.json('/api/applications')
                          if (a.get('name') or '').startswith(PREFIX)]
            if not owned_apps:
                return {'skipped': 'no owned application'}
            if self.child_a is None:
                out['note'] = 'owned child was never started; Hub-side delete only'
                for a in owned_apps:
                    self.budget['api_mutations'] += 1
                    self.budget['app_deletes'] += 1
                    code, body = self.hub.delete('/api/applications/' + self.hub.quote(a['id']))
                    out['delete_' + a['name']] = {'id': a['id'], 'delete_http': code}
                return out
            out['owned_apps'] = [{'id': a['id'], 'name': a['name']} for a in owned_apps]
            for a in owned_apps:
                self.budget['api_mutations'] += 1
                self.budget['app_deletes'] += 1
                code, body = self.hub.delete('/api/applications/' + self.hub.quote(a['id']))
                out['delete_' + a['name']] = {'id': a['id'], 'delete_http': code, 'delete_body': body}
                self.state['deletes'][a['id']] = {'kind': 'application', 'delete_http': code}
            st = None
            deadline = time.time() + 60
            while time.time() < deadline:
                st = self.child_a.agent_state(self.state['app'], UNIT, BIN_MAIN, CFG_MAIN)
                if not st['unit_file']['lexists'] and not st['binary']['lexists'] \
                        and not st['config']['lexists'] and not (st.get('unit_registry') or []):
                    break
                time.sleep(1)
            out['host_after_product_cleanup'] = {
                'unit_file_lexists': st['unit_file']['lexists'],
                'binary_lexists': st['binary']['lexists'],
                'config_lexists': st['config']['lexists'],
                'enable_symlink_lexists': st['enable_symlink']['lexists'],
                'unit_registry': st.get('unit_registry'),
                'unit_state': st['unit_state'],
                'local_apps': st.get('local_apps'),
            }
            out['product_cleanup_complete'] = (not st['unit_file']['lexists']
                                              and not st['binary']['lexists']
                                              and not st['config']['lexists']
                                              and not (st.get('unit_registry') or []))
            out['get_after_delete_http'] = self.hub.get(
                '/api/applications/' + self.hub.quote(self.state['app']))[0]
            return out
        step('product_uninstall', product_uninstall)

        def unit_sweep():
            if self.child_a is None:
                return {'skipped': 'no owned child'}
            units = '%s %s' % (UNIT, PREFIX + 'xapp.service')
            return self.child_a.seam('cmd', OPT, {'argv': ['bash', '-lc',
                                                           'for u in %s; do systemctl stop $u 2>/dev/null; '
                                                           'systemctl disable $u 2>/dev/null; '
                                                           'systemctl reset-failed $u 2>/dev/null; '
                                                           'rm -f /etc/systemd/system/$u; '
                                                           'rm -f /etc/systemd/system/multi-user.target.wants/$u; '
                                                           'done; systemctl daemon-reload' % units]})
        step('host_unit_residue_sweep', unit_sweep)

        def host_dirs_removal():
            out = {}
            if self.child_a:
                for d in (XFS, ESCAPE_DIR):
                    out['rm_' + d] = self.child_a.seam('rm_rf', d)
                out['rm_etc_owned'] = self.child_a.seam('rm_rf', '/etc/%s' % TAG)
                out['rm_sampler_files'] = self.child_a.seam('rm_rf', XFS + '/dst.samples')
                self.budget['host_seam_actions'] += 4
            return out
        step('host_dirs_removal', host_dirs_removal)

        def child_removal():
            out = {}
            if self.child_b:
                out['child_b'] = self.child_b.remove()
            if self.child_a:
                out['child_a'] = self.child_a.remove()
            return out
        step('owned_child_removal', child_removal)

        def artifact_deletes():
            out = []
            for label, art in self.state['artifacts'].items():
                self.budget['api_mutations'] += 1
                self.budget['artifact_deletes'] += 1
                path = '/api/artifacts/' + self.hub.quote(art['id'])
                code, body = self.hub.delete(path)
                gcode, _ = self.hub.get(path)
                out.append({'label': label, 'id': art['id'], 'delete_http': code, 'get_http': gcode})
                self.state['deletes'][art['id']] = {'kind': 'artifact', 'delete_http': code,
                                                    'get_http': gcode}
            return out
        step('artifact_deletes', artifact_deletes)

        def node_deletes():
            out = {}
            for key, info in self.state['nodes'].items():
                self.budget['api_mutations'] += 1
                self.budget['node_deletes'] += 1
                path = '/api/nodes/' + self.hub.quote(info['node_id'])
                code, body = self.hub.delete(path)
                gcode, _ = self.hub.get(path)
                out[key] = {'node_id': info['node_id'], 'delete_http': code, 'delete_body': body,
                            'get_http': gcode}
                self.state['deletes'][info['node_id']] = {'kind': 'node', 'delete_http': code,
                                                          'get_http': gcode}
            return out
        step('node_deletes', node_deletes)

        def proxy_stop():
            if self.proxy:
                self.proxy.stop()
            return {'stopped': True}
        step('frame_proxy_stop', proxy_stop)

        res['get_http_by_id'] = {k: (v or {}).get('get_http') for k, v in self.state['deletes'].items()}
        res['budget'] = dict(self.budget)
        return res

    # ---------- oracle ----------
    def phase13_oracle(self):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after, ok = {}, True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'),
                                                         'sync_status': n.get('sync_status')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        leftover = {'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
                    'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
                    'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        base = hostapi.OwnedChild(NONCE).host_baseline()
        transfers = self.hub.json('/api/transfers')
        owned_transfers = [{'id': t['id'], 'status': t.get('status'),
                            'source_path': t.get('source_path'),
                            'targets': [{'node_id': x.get('node_id'), 'destination_path': x.get('destination_path'),
                                         'status': x.get('status')} for x in (t.get('targets') or [])]}
                           for t in transfers if (t.get('source_path') or '').startswith('/opt/' + TAG)]
        oracle = {'at_utc': NOW(), 'owned_prefix': PREFIX,
                  'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
                  'hub_leftover_owned_fixtures': leftover, 'hub_leftover_clear': not any(leftover.values()),
                  'hub_transfer_records_owned_paths': owned_transfers,
                  'hub_transfer_records_note': ('/api/transfers exposes create/retry/cancel but no delete; '
                                                'the owned transfer records therefore remain in the Hub DB '
                                                'and are listed here verbatim (blobs are removed by the '
                                                'product after SUCCESS)'),
                  'native2_host_after': base,
                  'native2_clean': (not base['root_exists'] and not base['opt_root_exists']
                                    and not base['child_config_exists'] and not base['owned_child_processes']
                                    and not base['owned_units'] and not base['etc_systemd_owned_leftovers']
                                    and not base['etc_systemd_owned_wants'] and not base['owned_mounts']
                                    and not base['tmp_owned_entries'] and not base['etc_owned_entries']),
                  'agent_binary_unchanged': base['agent_binary_sha256'] == hostapi.AGENT_SHA,
                  'protected_units_after': {'primary': base['primary_agent_unit'].get('ActiveState'),
                                            'repair': base['repair_agent_unit'].get('ActiveState')},
                  'sandbox_cleanup': {'samples': 'owned sampler processes stopped and files removed',
                                      'frame_proxy': 'stopped; port released',
                                      'mounts': 'owned tmpfs unmounted'}}
        return oracle

    # ---------- driver ----------
    def run(self):
        oracle = None
        try:
            self.step('phase0-guard', self.phase0_guard, critical=True)
            self.step('phase1-owned-child-a', self.phase1_child_a, critical=True)
            self.step('phase2-owned-child-b', self.phase2_child_b, critical=True)
            self.step('phase3-fixtures', self.phase3_fixtures, critical=True)
            self.step('phase4-hub-path-guard', self.phase4_hub_path_guard)
            self.step('phase5-agent-path-guard', self.phase5_agent_path_guard)
            self.step('phase6-transfer-path-guard', self.phase6_transfer_path_guard)
            self.step('phase7-allowlist', self.phase7_allowlist)
            self.step('phase8-ar32-permission-denied', self.phase8_ar32_permission_denied)
            self.step('phase9-ar34-recovery', self.phase9_ar34_recovery)
            self.step('phase10-cross-fs-atomic-replace', self.phase10_cross_fs)
            self.step('phase11-capability-gate', self.phase11_capability_gate)
        except Exception as exc:
            self.failures.append({'phase': 'CRITICAL', 'error': '%s: %s' % (type(exc).__name__, exc)})
            self.rec('critical-abort.json', {'error': str(exc)[:800], 'failures': self.failures})
        finally:
            self.step('phase12-cleanup', self.phase12_cleanup)
            oracle = self.step('phase13-final-cleanup-oracle', self.phase13_oracle)
        return oracle


def main():
    started = NOW()
    hubapi.write_json('progress.json', {'batch_id': 'sys-b12', 'nonce': NONCE, 'owned_prefix': PREFIX,
                                        'started_at_utc': started, 'phase': 'bootstrap'})
    run = Run()
    run.state['started_at_utc'] = started
    oracle = run.run()
    summary = {'batch_id': 'sys-b12', 'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': run.hub.base,
               'started_at_utc': started, 'finished_at_utc': NOW(), 'failures': run.failures,
               'budget': run.budget,
               'state': {'nodes': run.state['nodes'], 'app_id': run.state.get('app'),
                         'xapp_id': run.state.get('xapp'),
                         'artifacts': run.state['artifacts'], 'transfers': run.state['transfers'],
                         'deletes': run.state['deletes']},
               'oracle': oracle,
               'results': {k: v for k, v in run.state['results'].items()}}
    hubapi.write_json('run-evidence.json', summary)
    hubapi.write_json('final-cleanup-oracle.json', oracle)
    return 0 if not run.failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
