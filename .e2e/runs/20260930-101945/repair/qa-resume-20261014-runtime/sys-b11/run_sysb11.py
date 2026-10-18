#!/usr/bin/env python3
"""sys-b11 runtime batch: Managed Application lifecycle.

Cases: AR-27 (upgrade), AR-28 (health failure), AR-29 (rollback),
       SYS-50 (health-fail rollback sequence), SYS-52 (backup failure reporting).

Real evidence chain, no product source change, no protected service touched:
  operator/API act -> real Hub request (deploy/upgrade via the session)
  -> real owned Agent process -> real systemd unit / binary / config / journal
  -> Agent SQLite deployments journal -> API/UI readback.

Owned fixtures only (nonce prefix); every host side effect is removed again.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import child_agent
import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb11-%s-' % NONCE
HUB = 'http://192.168.100.209:18080'
WS = 'ws://192.168.100.209:18080'
NODE_IP = '192.168.100.206'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
SHARED_ARTIFACT = '7b8929ed-c880-46f1-90e6-a5e9c0dab096'
SHARED_APP = '804e6b14-eb84-4dda-90de-8bcab9586c4c'
UNIT = 'nodesteer-' + PREFIX + 'app.service'
OPT_ROOT = '/opt/qa-sysb11-%s' % NONCE
BINARY = OPT_ROOT + '/app/bin/app'
CONFIG = OPT_ROOT + '/app/config/app.env'
TMPFS_OPTS = 'size=128k,mode=0755'
DEV = 'evidence/'
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
SHA = lambda b: hashlib.sha256(b).hexdigest()


def pad_script(body, target=1024 * 1024):
    """A valid /bin/sh script with ~1 MiB of comment padding (needed for the ENOSPC seam)."""
    line = '# padding-%s\n'
    out = ['#!/bin/sh\n']
    n = 0
    while sum(len(x) for x in out) < target:
        out.append(line % ('p' * 78))
        n += 1
    out.append(body)
    return ''.join(out).encode()


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.child = None
        self.state = {'artifacts': {}, 'app': None, 'execs': {}, 'deletes': {},
                      'seams': [], 'journal_rows': [], 'crash': {}}
        self.budget = {'api_mutations': 0, 'artifact_uploads': 0, 'artifact_deletes': 0,
                       'app_creates': 0, 'app_updates': 0, 'app_deletes': 0, 'app_assigns': 0,
                       'deploys': 0, 'node_enroll_posts': 0, 'node_deletes': 0,
                       'child_process_starts': 0, 'child_process_stops': 0, 'child_process_kills': 0,
                       'host_seam_actions': 0, 'systemd_unit_writes': 0}
        self.results = {}

    # ---------- plumbing ----------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        return hubapi.write_json(DEV + name, value)

    def progress(self, phase, **extra):
        p = {'batch_id': 'sys-b11', 'nonce': NONCE, 'owned_prefix': PREFIX, 'phase': phase,
             'updated_at_utc': NOW(), 'budget': self.budget}
        p.update(extra)
        self.p = p
        hubapi.write_json('progress.json', p)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def wait_child_online(self, timeout=90):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def exec_get(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return body if code == 200 else None

    def app_states(self, app_id):
        code, body = self.hub.get('/api/applications/%s/state' % self.hub.quote(app_id))
        return body if code == 200 else []

    def app_hub(self, app_id):
        code, body = self.hub.get('/api/applications/' + self.hub.quote(app_id))
        return body if code == 200 else None

    def art_state(self, shas=()):
        return self.child.app_state(self.state['app'], UNIT, BINARY, CONFIG, shas=shas)

    def deploy(self, operation='upgrade'):
        self.budget['api_mutations'] += 1
        self.budget['deploys'] += 1
        code, body = self.hub.post('/api/applications/%s/deploy' % self.hub.quote(self.state['app']),
                                   {'node_ids': [self.state['child_node_id']], 'operation': operation})
        if code != 200 or not isinstance(body, list) or not body or not body[0].get('id'):
            raise RuntimeError('deploy_failed_http_%s_%s' % (code, body))
        return body[0]['id']

    def wait_exec(self, exec_id, timeout=180):
        deadline = time.time() + timeout
        ex = None
        while time.time() < deadline:
            ex = self.exec_get(exec_id)
            if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'):
                return ex
            time.sleep(1)
        return ex

    def watch_deploy(self, operation, unit=UNIT, binary=BINARY, config=CONFIG, shas=(),
                     stop_on_phase=None, timeout=240, sample_interval=0.5, kill_on_phase=None):
        """Trigger a deploy and sample the journal/binary/unit state while it runs.

        Returns {exec_id, exec, samples:[{at, phase, binary_sha256, unit_active, journal_id}], killed}
        """
        exec_id = self.deploy(operation)
        samples = []
        deadline = time.time() + timeout
        killed = False
        last_key = None
        while time.time() < deadline:
            st = self.child.app_state(self.state['app'], unit, binary, config, shas=shas)
            rows = st.get('deployments') or []
            row = rows[0] if rows else {}
            phase = row.get('phase')
            key = (phase, st['binary'].get('sha256'), st['unit_state'].get('ActiveState'))
            if key != last_key:
                samples.append({'at_utc': NOW(), 'phase': phase, 'journal_id': row.get('id'),
                                'binary_sha256': st['binary'].get('sha256'),
                                'binary_exists': st['binary'].get('exists'),
                                'unit_active': st['unit_state'].get('ActiveState'),
                                'unit_file_sha256': st['unit_file'].get('sha256'),
                                'config_sha256': st['config'].get('sha256')})
                last_key = key
            if kill_on_phase and phase == kill_on_phase:
                k = self.child.kill_child()
                self.budget['child_process_kills'] += 1
                killed = True
                samples.append({'at_utc': NOW(), 'event': 'child_sigkill_at_phase_%s' % phase,
                                'kill': k, 'hub_exec_status_at_kill': (self.exec_get(exec_id) or {}).get('status')})
                break
            if stop_on_phase and phase == stop_on_phase:
                break
            ex = self.exec_get(exec_id)
            if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'):
                st2 = self.child.app_state(self.state['app'], unit, binary, config, shas=shas)
                rows2 = st2.get('deployments') or []
                samples.append({'at_utc': NOW(), 'event': 'exec_terminal',
                                'phase': (rows2[0] if rows2 else {}).get('phase'),
                                'binary_sha256': st2['binary'].get('sha256'),
                                'unit_active': st2['unit_state'].get('ActiveState')})
                return {'exec_id': exec_id, 'exec': ex, 'samples': samples, 'killed': killed,
                        'final_state': st2}
            time.sleep(sample_interval)
        return {'exec_id': exec_id, 'exec': self.exec_get(exec_id), 'samples': samples,
                'killed': killed, 'final_state': self.child.app_state(self.state['app'], unit, binary, config, shas=shas)}

    # ---------- app definition fixture ----------
    @staticmethod
    def health(kind='systemd', attempts=3, interval=2, timeout=5, target=''):
        return {'type': kind, 'target': target, 'attempts': attempts, 'interval': interval,
                'timeout': timeout}

    def app_def(self, version, artifact_label, args, env, config, health):
        return {'name': PREFIX + 'app',
                'description': 'sys-b11 owned managed application (nonce %s)' % NONCE,
                'artifact_id': self.state['artifacts'][artifact_label]['id'],
                'version': version,
                'binary_path': BINARY,
                'unit_name': UNIT,
                'arguments': args,
                'environment': env,
                'config': config,
                'config_path': CONFIG,
                'health_check': health}

    def put_app(self, definition):
        self.budget['api_mutations'] += 1
        self.budget['app_updates'] += 1
        code, body = self.hub.put('/api/applications/' + self.hub.quote(self.state['app']), definition)
        if code != 200:
            raise RuntimeError('app_update_failed_http_%s_%s' % (code, body))
        return body

    def wait_agent_definition(self, version, timeout=30):
        deadline = time.time() + timeout
        st = None
        while time.time() < deadline:
            st = self.child.app_state(self.state['app'], UNIT, BINARY, CONFIG)
            for a in st.get('local_apps') or []:
                if a['id'] == self.state['app'] and (a.get('definition') or {}).get('version') == version:
                    return st
            time.sleep(1)
        return st

    def wait_host(self, predicate, timeout=45, interval=1.0, unit=UNIT, binary=BINARY, config=CONFIG, shas=()):
        deadline = time.time() + timeout
        st = None
        while time.time() < deadline:
            st = self.child.app_state(self.state['app'], unit, binary, config, shas=shas)
            try:
                if predicate(st):
                    return st
            except Exception:
                pass
            time.sleep(interval)
        return st

    # ---------- phase 0 ----------
    def phase0_guard(self):
        out = {'at_utc': NOW(), 'endpoint': self.hub.base, 'nonce': NONCE, 'owned_prefix': PREFIX,
               'hub_binary_sha256_expected': '164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6',
               'agent_binary_sha256_expected': child_agent.AGENT_SHA}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
                                       'global_revision': n.get('global_revision'),
                                       'agent_version': n.get('agent_version'),
                                       'deployment_mode': n.get('deployment_mode'),
                                       'capabilities': n.get('capabilities')}
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        out['baseline_counts'] = {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)}
        out['shared_artifact_present'] = any(a['id'] == SHARED_ARTIFACT for a in arts)
        out['owned_fixture_check'] = {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        if any(out['owned_fixture_check'].values()):
            raise RuntimeError('owned_fixture_already_present')
        base = child_agent.OwnedChild(NONCE).host_baseline()
        out['native2_baseline'] = base
        if base['root_exists'] or base['opt_root_exists'] or base['child_config_exists'] \
                or base['owned_child_processes'] or base['owned_units'] or base['etc_systemd_owned'] \
                or base['wants_owned'] or base['owned_mounts']:
            raise RuntimeError('owned_host_residue_present')
        if base['agent_binary_sha256'] != child_agent.AGENT_SHA:
            raise RuntimeError('agent_binary_sha_mismatch')
        if base['primary_agent_unit'].get('ActiveState') != 'active' \
                or base['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_unit_not_active')
        out['guard_ok'] = True
        self.rec('phase0-guard.json', out)
        return out

    # ---------- phase 1: owned child ----------
    def phase1_child(self):
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child', 'node_ip': NODE_IP, 'hub_address': HUB})
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s' % code)
        self.state['child_node_id'] = enroll['node_id']
        self.state['child_agent_id'] = enroll['agent_id']
        self.child = child_agent.OwnedChild(NONCE, enroll['node_id'], enroll['agent_id'],
                                           PREFIX + 'child', NODE_IP,
                                           self.hub.cred['registration_token'])
        started = self.child.write_config_and_start(WS)
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online')
        out = {'at_utc': NOW(),
               'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                              'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
                              'gateway_url': enroll.get('gateway_url'),
                              'registration_token_persisted_to_evidence': False},
               'owned_child': {'root_on_native2': self.child.root, 'opt_root_on_native2': self.child.opt_root,
                               'config_mode_0600': True, 'data_dir': self.child.data,
                               'child_processes': started.get('owned_processes'),
                               'routing': 'child -> %s directly (no proxy)' % WS},
               'node_after_start': {k: node.get(k) for k in
                                    ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                     'global_revision', 'deployment_mode', 'agent_version', 'capabilities')}}
        if not (node.get('capabilities') or {}).get('application_deploy'):
            raise RuntimeError('owned_child_lacks_application_deploy_capability')
        self.rec('phase1-owned-child.json', out)
        return out

    # ---------- phase 2: artifacts ----------
    def phase2_artifacts(self):
        contents = {
            'A1': pad_script('echo v1 >/dev/null\nexec sleep 100000\n'),
            'A2': pad_script('echo v2 >/dev/null\nexec sleep 100000\n'),
            'A3': pad_script('echo v3-health-units-exits\nexit 0\n'),
            'A4': pad_script('echo v4-crash-window\nexit 0\n'),
        }
        out = {'at_utc': NOW(), 'artifacts': {}}
        for label, content in contents.items():
            name = PREFIX + 'payload-' + label.lower()
            self.budget['api_mutations'] += 1
            self.budget['artifact_uploads'] += 1
            code, body = self.hub.upload_artifact(name, 'v1', name + '.bin', content)
            if code != 200:
                raise RuntimeError('artifact_upload_failed_%s_http_%s' % (label, code))
            if body.get('sha256') != SHA(content):
                raise RuntimeError('hub_sha_not_local_sha_%s' % label)
            self.state['artifacts'][label] = {'id': body['id'], 'sha256': body['sha256'],
                                              'size': body['size'], 'name': name}
            out['artifacts'][label] = {'id': body['id'], 'sha256': body['sha256'], 'size': body['size'],
                                       'name': name, 'local_sha256_matches_hub': True,
                                       'role': {'A1': 'v1 good (install+upgrade source)',
                                                'A2': 'v2 good (upgrade target / rollback restore source)',
                                                'A3': 'v3 unhealthy (health-fail rollback)',
                                                'A4': 'v4 unhealthy (crash-recovery rollback)'}[label]}
        out['enospc_premise'] = {'tmpfs_size_kib': 128,
                                 'A2_binary_bytes': self.state['artifacts']['A2']['size'],
                                 'A2_exceeds_tmpfs': self.state['artifacts']['A2']['size'] > 128 * 1024}
        self.rec('phase2-artifacts.json', out)
        return out

    # ---------- phase 3: app create + assign ----------
    def phase3_app_create(self):
        defn = self.app_def('1.0', 'A1', ['--profile=v1', '--nonce=%s' % NONCE],
                            {'APP_PROFILE': 'v1'}, 'APP_MODE=v1\n', self.health())
        self.budget['api_mutations'] += 1
        self.budget['app_creates'] += 1
        code, body = self.hub.post('/api/applications', defn)
        if code != 200 or not body.get('id'):
            raise RuntimeError('app_create_failed_http_%s_%s' % (code, body))
        self.state['app'] = body['id']
        self.budget['api_mutations'] += 1
        self.budget['app_assigns'] += 1
        code2, _ = self.hub.post('/api/applications/%s/assign' % self.hub.quote(body['id']),
                                 {'node_ids': [self.state['child_node_id']]})
        if code2 != 200:
            raise RuntimeError('app_assign_failed_http_%s' % code2)
        st = self.wait_agent_definition('1.0', timeout=40)
        local = [a for a in (st.get('local_apps') or []) if a['id'] == body['id']]
        out = {'at_utc': NOW(), 'app_id': body['id'], 'created_http': code, 'assign_http_http': code2,
               'hub_definition': {k: body.get(k) for k in
                                  ('id', 'name', 'version', 'revision', 'artifact_id', 'binary_path',
                                   'config_path', 'unit_name', 'arguments', 'health_check')},
               'owned_unit_name': UNIT, 'owned_binary_path': BINARY, 'owned_config_path': CONFIG,
               'agent_local_definition': local[0] if local else None,
               'desired_state_delivered_to_agent': bool(local),
               'agent_local_artifact_url_sha_filled': bool(local and
                                                           (local[0]['definition'] or {}).get('artifact_sha256')),
               'node_online_synced': (self.node(self.state['child_node_id']) or {}).get('status')}
        if not local:
            raise RuntimeError('desired_state_not_delivered_to_agent')
        self.rec('phase3-app-create.json', out)
        return out

    # ---------- phase 4: install v1 ----------
    def phase4_install_v1(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        res = self.watch_deploy('deploy', shas=[sha1])
        st = res['final_state']
        node_state = [s for s in self.app_states(self.state['app'])
                      if s['node_id'] == self.state['child_node_id']]
        out = {'at_utc': NOW(), 'operation': 'deploy', 'execution': _ex(res['exec']),
               'journal_phase_sequence': res['samples'],
               'artifact_v1': self.state['artifacts']['A1'],
               'actual_state': _app_actual(st, sha1, 'v1')}
        out['install_ok'] = (res['exec'] or {}).get('status') == 'SUCCESS' \
            and out['actual_state']['binary_sha_matches_artifact'] \
            and out['actual_state']['unit_active'] == 'active' \
            and out['actual_state']['enable_symlink']
        self.state['binary_sha_after_install'] = out['actual_state']['binary_sha256']
        out['node_state_hub'] = node_state
        out['hub_app_after'] = {k: self.app_hub(self.state['app']).get(k)
                                for k in ('id', 'revision', 'version', 'artifact_id')}
        self.rec('phase4-install-v1.json', out)
        if not out['install_ok']:
            raise RuntimeError('phase4_install_v1_failed')
        return out

    # ---------- phase 5: install idempotency ----------
    def phase5_install_idempotent(self):
        before = self.art_state([self.state['artifacts']['A1']['sha256']])
        res = self.watch_deploy('deploy', shas=[self.state['artifacts']['A1']['sha256']])
        after = res['final_state']
        execs_before = len(self.hub.json('/api/applications/%s/executions' % self.hub.quote(self.state['app'])))
        # desired-state re-assertion must not re-install on its own
        self.budget['api_mutations'] += 1
        self.budget['app_assigns'] += 1
        self.hub.post('/api/applications/%s/assign' % self.hub.quote(self.state['app']),
                      {'node_ids': [self.state['child_node_id']]})
        self.wait_agent_definition('1.0', timeout=20)
        time.sleep(8)
        st_reassert = self.art_state()
        journal_after_reassert = len(st_reassert.get('deployments') or [])
        out = {'at_utc': NOW(),
               'repeat_deploy_execution': _ex(res['exec']),
               'binary_sha_before': before['binary'].get('sha256'),
               'binary_sha_after': after['binary'].get('sha256'),
               'unit_file_sha_before': before['unit_file'].get('sha256'),
               'unit_file_sha_after': after['unit_file'].get('sha256'),
               'enable_symlink_count': 1 if after['enable_symlink'].get('lexists') else 0,
               'single_unit_file': after['unit_file'].get('lexists'),
               'unit_active': after['unit_state'].get('ActiveState'),
               'owned_units_on_host': self.child.inventory(UNIT)['owned_systemd_units'],
               'journal_rows_before_reassert': len(after.get('deployments') or []),
               'journal_rows_after_reassert': journal_after_reassert,
               'executions_before_reassert': execs_before,
               'executions_after_reassert': len(self.hub.json(
                   '/api/applications/%s/executions' % self.hub.quote(self.state['app']))),
               'node_state_hub': [s for s in self.app_states(self.state['app'])
                                  if s['node_id'] == self.state['child_node_id']]}
        out['idempotent_install'] = (
            out['binary_sha_before'] == out['binary_sha_after']
            and out['unit_file_sha_before'] == out['unit_file_sha_after']
            and out['unit_active'] == 'active'
            and (res['exec'] or {}).get('status') == 'SUCCESS'
            and out['journal_rows_after_reassert'] == out['journal_rows_before_reassert']
            and out['executions_after_reassert'] == out['executions_before_reassert']
            and len(out['owned_units_on_host']) == 1)
        self.rec('phase5-install-idempotent.json', out)
        if not out['idempotent_install']:
            raise RuntimeError('phase5_idempotency_failed')
        return out

    # ---------- phase 6: upgrade v1 -> v2 ----------
    def phase6_upgrade_v2(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        sha2 = self.state['artifacts']['A2']['sha256']
        before_app = self.app_hub(self.state['app'])
        defn = self.app_def('2.0', 'A2', ['--profile=v2', '--nonce=%s' % NONCE],
                            {'APP_PROFILE': 'v2'}, 'APP_MODE=v2\n', self.health())
        put = self.put_app(defn)
        self.wait_agent_definition('2.0', timeout=30)
        res = self.watch_deploy('upgrade', shas=[sha2])
        st = res['final_state']
        rows = st.get('deployments') or []
        row = rows[0] if rows else {}
        backup_sha = self.child.file_shas([row.get('backup_path')]) if row.get('backup_path') else {}
        out = {'at_utc': NOW(), 'operation': 'upgrade',
               'app_revision_before': before_app.get('revision'), 'app_revision_after': put.get('revision'),
               'binary_sha_before_deploy': self.state.get('binary_sha_after_install'),
               'artifact_v1': self.state['artifacts']['A1'], 'artifact_v2': self.state['artifacts']['A2'],
               'execution': _ex(res['exec']), 'journal_phase_sequence': res['samples'],
               'journal_row': row,
               'journal_from_version_observation': {
                   'from_version': row.get('from_version'), 'installed_before_deploy': '1.0 (A1)',
                   'note': 'the product writes from_version from the synced Application definition at deploy '
                           'time, not from the previously installed version; the true previous version is '
                           'proven by the pre-deploy binary SHA and the backup content SHA'},
               'previous_version_backup': {
                   'backup_path': row.get('backup_path'),
                   'backup_binary_sha256': list(backup_sha.values())[0].get('sha256') if backup_sha else None,
                   'equals_v1_artifact_sha256': bool(backup_sha) and
                   list(backup_sha.values())[0].get('sha256') == sha1,
                   'note': 'PRODUCT.md §39/§41: Backup Current must retain at least the previous version'},
               'actual_state': _app_actual(st, sha2, 'v2'),
               'node_state_hub': [s for s in self.app_states(self.state['app'])
                                  if s['node_id'] == self.state['child_node_id']]}
        out['upgrade_ok'] = (res['exec'] or {}).get('status') == 'SUCCESS' \
            and out['actual_state']['binary_sha_matches_artifact'] \
            and out['actual_state']['unit_active'] == 'active' \
            and out['previous_version_backup']['equals_v1_artifact_sha256'] \
            and row.get('phase') == 'DONE'
        self.rec('phase6-upgrade-v2.json', out)
        if not out['upgrade_ok']:
            raise RuntimeError('phase6_upgrade_v2_failed')
        return out

    # ---------- phase 7: upgrade idempotency ----------
    def phase7_upgrade_idempotent(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        before = self.art_state([sha2])
        res = self.watch_deploy('upgrade', shas=[sha2])
        after = res['final_state']
        out = {'at_utc': NOW(), 'execution': _ex(res['exec']),
               'binary_sha_before': before['binary'].get('sha256'),
               'binary_sha_after': after['binary'].get('sha256'),
               'unit_file_sha_before': before['unit_file'].get('sha256'),
               'unit_file_sha_after': after['unit_file'].get('sha256'),
               'unit_active': after['unit_state'].get('ActiveState'),
               'owned_units_on_host': self.child.inventory(UNIT)['owned_systemd_units'],
               'journal_row': (after.get('deployments') or [{}])[0],
               'node_state_hub': [s for s in self.app_states(self.state['app'])
                                  if s['node_id'] == self.state['child_node_id']]}
        out['idempotent_upgrade'] = ((res['exec'] or {}).get('status') == 'SUCCESS'
                                     and out['binary_sha_before'] == out['binary_sha_after'] == sha2
                                     and out['unit_file_sha_before'] == out['unit_file_sha_after']
                                     and out['unit_active'] == 'active'
                                     and len(out['owned_units_on_host']) == 1)
        self.rec('phase7-upgrade-idempotent.json', out)
        if not out['idempotent_upgrade']:
            raise RuntimeError('phase7_idempotency_failed')
        return out

    # ---------- phase 8: persistence across agent restart ----------
    def phase8_persistence(self, tag='phase8-persistence.json'):
        sha2 = self.state['artifacts']['A2']['sha256']
        before = self.art_state([sha2])
        restarted = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        time.sleep(5)
        after = self.art_state([sha2])
        out = {'at_utc': NOW(), 'restart': restarted, 'tag': tag,
               'node_after_restart': {k: node.get(k) for k in ('status', 'sync_status', 'global_revision')},
               'before': _app_actual(before, sha2, 'v2'), 'after': _app_actual(after, sha2, 'v2'),
               'db_quick_check_after': after.get('db_quick_check'),
               'unit_registry_after': after.get('unit_registry'),
               'journal_rows_after': len(after.get('deployments') or []),
               'node_state_hub': [s for s in self.app_states(self.state['app'])
                                  if s['node_id'] == self.state['child_node_id']]}
        out['persistent'] = (out['after']['binary_sha_matches_artifact']
                            and out['after']['unit_active'] == 'active'
                            and out['after']['enable_symlink']
                            and after.get('db_quick_check') == 'ok'
                            and len(after.get('unit_registry') or []) == 1
                            and len(after.get('deployments') or []) == len(before.get('deployments') or []))
        self.rec(tag, out)
        if not out['persistent']:
            raise RuntimeError('persistence_failed:' + tag)
        return out

    # ---------- phase 9: SYS-50 / AR-28 live health-failure rollback ----------
    def phase9_health_rollback(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        sha3 = self.state['artifacts']['A3']['sha256']
        defn = self.app_def('3.0', 'A3', ['--profile=v3-health'],
                            {'APP_PROFILE': 'v3'}, 'APP_MODE=v3\n',
                            self.health('systemd', attempts=3, interval=2, timeout=5))
        put = self.put_app(defn)
        self.wait_agent_definition('3.0', timeout=30)
        res = self.watch_deploy('upgrade', shas=[sha3, sha2])
        st = res['final_state']
        rows = st.get('deployments') or []
        row = rows[0] if rows else {}
        phases = [s.get('phase') for s in res['samples'] if s.get('phase')]
        node_state = [s for s in self.app_states(self.state['app'])
                      if s['node_id'] == self.state['child_node_id']]
        cache_shas = {e['name'] for e in (st.get('backup_entries') or [])}
        out = {'at_utc': NOW(), 'operation': 'upgrade', 'target_version': '3.0',
               'app_revision_after_put': put.get('revision'),
               'execution': _ex(res['exec']),
               'journal_phase_sequence': res['samples'],
               'journal_phases_observed': phases,
               'journal_row': row,
               'journal_from_version_observation': {
                   'from_version': row.get('from_version'),
                   'note': 'journal from_version reflects the synced definition version at deploy time; the '
                           'previously installed version is proven by the pre-deploy binary SHA and by the '
                           'backup content SHA'},
               'binary_sha_before_deploy': self.state.get('binary_sha_after_install'),
               'actual_state_after_rollback': _app_actual(st, sha2, 'v2'),
               'node_state_hub': node_state,
               'v3_artifact_sha256': sha3, 'v2_artifact_sha256': sha2,
               'rollback_order_checks': {
                   'stopped_new_version': 'ROLLING_BACK' in phases,
                   'restored_binary_to_v2': st['binary'].get('sha256') == sha2,
                   'restored_config': (st['config'].get('sha256') == SHA(b'APP_MODE=v2\n')),
                   'restored_unit': self._unit_has('--profile=v2'),
                   'unit_active_after': st['unit_state'].get('ActiveState') == 'active',
                   'artifact_cache_has_v3': sha3 in cache_shas,
                   'v3_binary_not_installed': st['binary'].get('sha256') != sha3}}
        out['no_false_success'] = not (row.get('phase') == 'DONE' and node_state
                                       and node_state[0].get('health') == 'healthy'
                                       and node_state[0].get('version') == '3.0')
        out['health_failure_reported'] = (
            (res['exec'] or {}).get('status') == 'FAILED'
            and 'health check failed' in ((res['exec'] or {}).get('block_reason') or '')
            and bool(node_state) and node_state[0].get('health') == 'unhealthy'
            and node_state[0].get('version') == '3.0'
            and 'health check failed' in (node_state[0].get('error') or ''))
        out['sys50_ok'] = (out['health_failure_reported']
                           and out['rollback_order_checks']['restored_binary_to_v2']
                           and out['rollback_order_checks']['restored_config']
                           and out['rollback_order_checks']['restored_unit']
                           and out['rollback_order_checks']['unit_active_after']
                           and out['rollback_order_checks']['v3_binary_not_installed']
                           and row.get('phase') == 'DONE')
        self.rec('phase9-sys50-health-rollback.json', out)
        if not out['sys50_ok']:
            raise RuntimeError('phase9_health_rollback_failed')
        return out

    def _unit_has(self, needle):
        script = ("import json,os\n"
                  "p=%r; n=%r\n"
                  "t=open(p).read() if os.path.isfile(p) else ''\n"
                  "print(json.dumps({'path':p,'has_needle':n in t,'len':len(t)}))\n") % \
                 ('/etc/systemd/system/' + UNIT, needle)
        return self.child.remote_py(script)['has_needle']

    # ---------- phase 10: recover to v2 (AR-28 recovery) ----------
    def phase10_recover_v2(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        defn = self.app_def('2.0', 'A2', ['--profile=v2', '--nonce=%s' % NONCE],
                            {'APP_PROFILE': 'v2'}, 'APP_MODE=v2\n', self.health())
        self.put_app(defn)
        self.wait_agent_definition('2.0', timeout=30)
        res = self.watch_deploy('upgrade', shas=[sha2])
        st = res['final_state']
        node_state = [s for s in self.app_states(self.state['app'])
                      if s['node_id'] == self.state['child_node_id']]
        hub_app = self.app_hub(self.state['app'])
        local = [a for a in (st.get('local_apps') or []) if a['id'] == self.state['app']]
        out = {'at_utc': NOW(), 'execution': _ex(res['exec']),
               'journal_phase_sequence': res['samples'],
               'journal_row': (st.get('deployments') or [{}])[0],
               'actual_state': _app_actual(st, sha2, 'v2'),
               'node_state_hub': node_state,
               'desired_state': {'hub_version': hub_app.get('version'),
                                 'hub_revision': hub_app.get('revision'),
                                 'hub_artifact_id': hub_app.get('artifact_id'),
                                 'agent_local_version': (local[0]['definition'] or {}).get('version') if local else None,
                                 'agent_local_artifact_id': (local[0]['definition'] or {}).get('artifact_id') if local else None},
               'assigned_nodes': self.hub.json('/api/applications/%s/nodes' % self.hub.quote(self.state['app']))}
        out['desired_equals_actual'] = (hub_app.get('version') == '2.0'
                                        and out['actual_state']['binary_sha_matches_artifact']
                                        and out['actual_state']['unit_active'] == 'active'
                                        and bool(node_state) and node_state[0].get('health') == 'healthy'
                                        and node_state[0].get('version') == '2.0')
        out['recovery_ok'] = (res['exec'] or {}).get('status') == 'SUCCESS' and out['desired_equals_actual']
        self.rec('phase10-ar28-recovery-v2.json', out)
        if not out['recovery_ok']:
            raise RuntimeError('phase10_recovery_failed')
        return out

    # ---------- phase 11: AR-28 persistence + idempotency after the rollback ----------
    def phase11_ar28_persistence(self):
        pers = self.phase8_persistence('phase11-ar28-persistence.json')
        sha2 = self.state['artifacts']['A2']['sha256']
        before = self.art_state([sha2])
        res = self.watch_deploy('upgrade', shas=[sha2])
        after = res['final_state']
        out = {'at_utc': NOW(), 'persistence': pers, 'repeat_deploy_execution': _ex(res['exec']),
               'binary_sha_before': before['binary'].get('sha256'),
               'binary_sha_after': after['binary'].get('sha256'),
               'unit_active': after['unit_state'].get('ActiveState'),
               'journal_rows_after': len(after.get('deployments') or [])}
        out['idempotent_after_rollback'] = (
            (res['exec'] or {}).get('status') == 'SUCCESS'
            and out['binary_sha_before'] == out['binary_sha_after'] == sha2
            and out['unit_active'] == 'active')
        self.rec('phase11-ar28-persistence-idempotent.json', out)
        if not out['idempotent_after_rollback']:
            raise RuntimeError('phase11_idempotency_failed')
        return out

    # ---------- phase 12: AR-29 crash-recovery rollback ----------
    def phase12_crash_rollback(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        sha4 = self.state['artifacts']['A4']['sha256']
        defn = self.app_def('4.0', 'A4', ['--profile=v4-crash'],
                            {'APP_PROFILE': 'v4'}, 'APP_MODE=v4\n',
                            self.health('systemd', attempts=12, interval=6, timeout=5))
        put = self.put_app(defn)
        self.wait_agent_definition('4.0', timeout=30)
        res = self.watch_deploy('upgrade', shas=[sha4, sha2], kill_on_phase='VERIFYING', timeout=200)
        self.state['execs']['crash_deploy'] = res['exec_id']
        hub_exec_at_kill = (res['exec'] or {})
        st_kill = res['final_state']
        row_kill = (st_kill.get('deployments') or [{}])[0]
        killed = res['killed']
        # restart the owned agent -> RecoverDeployments must roll back the interrupted deployment
        started = self.child.start()
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=90)
        rec = self.watch_host_recovery(timeout=180)
        time.sleep(3)
        st = self.art_state([sha2])
        row = (st.get('deployments') or [{}])[0]
        node_state = [s for s in self.app_states(self.state['app'])
                      if s['node_id'] == self.state['child_node_id']]
        log = self.child.log_tail()['tail']
        out = {'at_utc': NOW(), 'target_version': '4.0', 'app_revision_after_put': put.get('revision'),
               'kill': {'killed': killed, 'samples_before_kill': res['samples'],
                        'journal_row_at_kill': row_kill,
                        'hub_execution_at_kill': _ex(hub_exec_at_kill),
                        'v4_binary_was_on_disk': st_kill['binary'].get('sha256') == sha4},
               'restart': started, 'node_after_restart': {k: node.get(k) for k in
                                                          ('status', 'sync_status', 'global_revision')},
               'recovery_sampling': rec,
               'journal_row_after_recovery': row,
               'actual_state_after_recovery': _app_actual(st, sha2, 'v2'),
               'node_state_hub': node_state,
               'agent_log_recovery_lines': [l.strip()[:300] for l in log.splitlines()
                                            if 'recover' in l.lower() or 'rollback' in l.lower()][-10:],
               'hub_execution_after_recovery': _ex(self.exec_get(res['exec_id'])),
               'v4_artifact_sha256': sha4, 'v2_artifact_sha256': sha2}
        out['journal_recovered_to_done'] = row.get('phase') == 'DONE' and row.get('to_version') == '4.0'
        out['restored_previous_version'] = (st['binary'].get('sha256') == sha2
                                            and st['unit_state'].get('ActiveState') == 'active'
                                            and self._unit_has('--profile=v2'))
        out['ar29_ok'] = out['journal_recovered_to_done'] and out['restored_previous_version']
        self.rec('phase12-ar29-crash-rollback.json', out)
        if not out['ar29_ok']:
            raise RuntimeError('phase12_crash_rollback_failed')
        return out

    def watch_host_recovery(self, timeout=180, interval=0.6):
        """Sample the journal until the recovered deployment reaches DONE."""
        deadline = time.time() + timeout
        samples = []
        last = None
        while time.time() < deadline:
            st = self.child.app_state(self.state['app'], UNIT, BINARY, CONFIG)
            rows = st.get('deployments') or []
            row = rows[0] if rows else {}
            if row.get('phase') != last:
                samples.append({'at_utc': NOW(), 'phase': row.get('phase'),
                                'binary_sha256': st['binary'].get('sha256'),
                                'unit_active': st['unit_state'].get('ActiveState'),
                                'journal_id': row.get('id')})
                last = row.get('phase')
            if row.get('phase') == 'DONE':
                return {'reached_done': True, 'samples': samples}
            time.sleep(interval)
        return {'reached_done': False, 'samples': samples}

    # ---------- phase 13: AR-29 recovery to v2 ----------
    def phase13_recover_after_crash(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        defn = self.app_def('2.0', 'A2', ['--profile=v2', '--nonce=%s' % NONCE],
                            {'APP_PROFILE': 'v2'}, 'APP_MODE=v2\n', self.health())
        self.put_app(defn)
        self.wait_agent_definition('2.0', timeout=30)
        res = self.watch_deploy('upgrade', shas=[sha2])
        st = res['final_state']
        before = st
        res2 = self.watch_deploy('upgrade', shas=[sha2])
        after = res2['final_state']
        node_state = [s for s in self.app_states(self.state['app'])
                      if s['node_id'] == self.state['child_node_id']]
        hub_app = self.app_hub(self.state['app'])
        out = {'at_utc': NOW(), 'recovery_execution': _ex(res['exec']),
               'idempotent_repeat_execution': _ex(res2['exec']),
               'binary_sha_between': [before['binary'].get('sha256'), after['binary'].get('sha256')],
               'unit_active': after['unit_state'].get('ActiveState'),
               'journal_row': (after.get('deployments') or [{}])[0],
               'node_state_hub': node_state,
               'hub_version': hub_app.get('version'),
               'stale_execution_note': ('an interrupted DEPLOY_REQUEST leaves a local Agent execution row; on '
                                        'restart the Agent finalizes it as FAILED/agent_restarted and uploads '
                                        'it, so the Hub does not keep a stale RUNNING execution '
                                        '(observed below)'),
               'crashed_execution_state_observed': _ex(self.exec_get(self.state['execs']['crash_deploy']))
               if self.state['execs'].get('crash_deploy') else None}
        out['ar29_recovery_ok'] = ((res['exec'] or {}).get('status') == 'SUCCESS'
                                   and (res2['exec'] or {}).get('status') == 'SUCCESS'
                                   and before['binary'].get('sha256') == sha2
                                   and after['binary'].get('sha256') == sha2
                                   and after['unit_state'].get('ActiveState') == 'active'
                                   and hub_app.get('version') == '2.0'
                                   and bool(node_state) and node_state[0].get('health') == 'healthy')
        self.rec('phase13-ar29-recovery.json', out)
        if not out['ar29_recovery_ok']:
            raise RuntimeError('phase13_recovery_failed')
        return out

    # ---------- phase 14: SYS-52 backup failures ----------
    def phase14_backup_failures(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        baseline = self.art_state([sha2])
        out = {'at_utc': NOW(), 'baseline': {'binary_sha256': baseline['binary'].get('sha256'),
                                            'unit_file_sha256': baseline['unit_file'].get('sha256'),
                                            'unit_active': baseline['unit_state'].get('ActiveState'),
                                            'unit_registry': baseline.get('unit_registry'),
                                            'journal_rows': len(baseline.get('deployments') or [])},
               'scenarios': {}}

        def one(name, seam, expect_any, release):
            self.budget['host_seam_actions'] += 1
            applied = self.child.backup_seam(seam)
            res = self.watch_deploy('upgrade', shas=[sha2])
            st = res['final_state']
            row = (st.get('deployments') or [{}])[0]
            node_state = [s for s in self.app_states(self.state['app'])
                          if s['node_id'] == self.state['child_node_id']]
            block = (res['exec'] or {}).get('block_reason') or ''
            matched = any(all(s in block for s in group) for group in expect_any)
            inv = self.child.inventory(UNIT)
            host_units = {'systemctl_list_units_matching': inv['owned_systemd_units'],
                          'etc_systemd_owned': inv['etc_systemd_owned_leftovers'],
                          'etc_systemd_owned_wants': inv['etc_systemd_owned_wants']}
            no_extra_unit = (inv['etc_systemd_owned_leftovers'] == [UNIT]
                             and sorted(inv['etc_systemd_owned_wants']) == [UNIT])
            scenario = {'seam_action': seam, 'seam_applied': applied,
                        'execution': _ex(res['exec']),
                        'block_reason_raw': block,
                        'expected_message_matched': matched,
                        'expected_message_any_of': expect_any,
                        'journal_row_after': row,
                        'journal_phase_sequence': res['samples'],
                        'node_state_hub': node_state,
                        'host_units': host_units,
                        'no_host_mutation': {
                            'binary_sha256': st['binary'].get('sha256'),
                            'binary_unchanged': st['binary'].get('sha256') == sha2,
                            'unit_file_sha256': st['unit_file'].get('sha256'),
                            'unit_file_unchanged': st['unit_file'].get('sha256') == baseline['unit_file'].get('sha256'),
                            'unit_active': st['unit_state'].get('ActiveState'),
                            'unit_registry_unchanged': st.get('unit_registry') == baseline.get('unit_registry'),
                            'single_owned_unit_file': bool(no_extra_unit)},
                        'explicit_failure': ((res['exec'] or {}).get('status') == 'FAILED' and bool(block)
                                             and matched
                                             and bool(node_state) and node_state[0].get('health') == 'unhealthy'),
                        'not_silently_successful': (res['exec'] or {}).get('status') != 'SUCCESS'}
            rel = self.child.backup_seam(release) if release else None
            scenario['seam_release'] = {'action': release, 'result': rel}
            self.state['seams'].append({'action': seam, 'release': release})
            return scenario

        # (a) backup destination missing/unusable: <data>/applications is a dangling symlink
        out['scenarios']['missing_backup_destination'] = one(
            'missing_backup_destination', 'mk_dangling_symlink',
            [['create backup directory failed']], 'mkdir')
        # (b) backup destination permission denied (immutable dir blocks even root)
        self.child.backup_seam('mkdir')
        out['scenarios']['backup_permission_denied'] = one(
            'backup_permission_denied', 'chattr_plus_i',
            [['backup binary failed'], ['permission denied'], ['operation not permitted']], 'chattr_minus_i')
        self.child.backup_seam('mkdir')
        # (c) backup destination out of space (owned 128 KiB tmpfs at the backup dir)
        out['scenarios']['backup_disk_full'] = one(
            'backup_disk_full', 'mount_tmpfs',
            [['backup binary failed'], ['no space left on device']], 'umount')
        self.child.backup_seam('rm_dir')
        # (d) a simply-missing backup directory is created by the product (not an error)
        out['scenarios']['missing_dir_is_created'] = one(
            'missing_dir_is_created', 'rm_dir', [['deploy succeeded']], 'mkdir')
        out['scenarios']['missing_dir_is_created']['expectation'] = 'not a failure: the missing backup directory is created and the deploy succeeds'
        out['scenarios']['missing_dir_is_created']['positive'] = \
            (out['scenarios']['missing_dir_is_created']['execution'] or {}).get('status') == 'SUCCESS'
        out['sys52_failure_modes_explicit'] = all(
            out['scenarios'][k]['explicit_failure'] and out['scenarios'][k]['not_silently_successful']
            and all(out['scenarios'][k]['no_host_mutation'].values())
            for k in ('missing_backup_destination', 'backup_permission_denied', 'backup_disk_full'))
        out['sys52_ok'] = out['sys52_failure_modes_explicit'] and \
            out['scenarios']['missing_dir_is_created']['positive']
        self.rec('phase14-sys52-backup-failures.json', out)
        if not out['sys52_ok']:
            raise RuntimeError('phase14_backup_failures_failed')
        return out

    # ---------- phase 15: cleanup ----------
    def phase15_cleanup(self):
        res = {'at_utc': NOW(), 'steps': {}, 'errors': []}

        def step(name, fn):
            try:
                res['steps'][name] = fn()
            except Exception as exc:
                res['steps'][name] = {'error': str(exc)[:300]}
                res['errors'].append('%s: %s' % (name, exc))

        # 1) release every host seam (defensive, idempotent)
        def release_seams():
            if self.child is None:
                return {'skipped': 'no owned child/host fixtures in this attempt'}
            out = {}
            out['umount'] = self.child.backup_seam('umount')
            out['chattr_minus_i'] = self.child.backup_seam('chattr_minus_i')
            out['mkdir'] = self.child.backup_seam('mkdir')
            out['status'] = self.child.backup_seam('status')
            return out
        step('release_host_seams', release_seams)

        # 2) product-driven uninstall: delete the Hub Application -> tombstone -> CleanupApplication
        def product_uninstall():
            out = {}
            if not self.state.get('app'):
                return {'skipped': 'no owned application was created in this attempt'}
            self.budget['api_mutations'] += 1
            self.budget['app_deletes'] += 1
            code, body = self.hub.delete('/api/applications/' + self.hub.quote(self.state['app']))
            out['delete_http'] = code
            out['delete_body'] = body
            removed = self.wait_host(lambda st: (not st['unit_file'].get('lexists')
                                                 and not st['binary'].get('lexists')
                                                 and not (st.get('unit_registry') or [])),
                                     timeout=45, interval=1.0)
            out['host_after_product_cleanup'] = {
                'unit_file_lexists': removed['unit_file'].get('lexists'),
                'enable_symlink_lexists': removed['enable_symlink'].get('lexists'),
                'binary_lexists': removed['binary'].get('lexists'),
                'config_lexists': removed['config'].get('lexists'),
                'unit_registry': removed.get('unit_registry'),
                'unit_state': removed['unit_state'],
                'backup_dir_still_present': removed['backup_dir'].get('is_dir')}
            out['product_cleanup_complete'] = (not removed['unit_file'].get('lexists')
                                               and not removed['enable_symlink'].get('lexists')
                                               and not removed['binary'].get('lexists')
                                               and not removed['config'].get('lexists')
                                               and not (removed.get('unit_registry') or []))
            out['get_after_delete_http'] = self.hub.get('/api/applications/' + self.hub.quote(self.state['app']))[0]
            return out
        step('product_uninstall', product_uninstall)

        # 3) residue sweep on native-2 (owned host fixtures)
        def residue_sweep():
            if self.child is None:
                return {'skipped': 'no owned child/host fixtures in this attempt'}
            return self.child.unit_cleanup(UNIT, BINARY, CONFIG, self.child.opt_root)
        step('host_residue_sweep', residue_sweep)

        # 4) stop + delete the owned child roots
        def child_removal():
            if self.child is None:
                return {'skipped': 'no owned child was started in this attempt'}
            return self.child.remove()
        step('owned_child_removal', child_removal)

        # 5) delete owned artifacts
        def delete_artifacts():
            out = []
            for label, art in self.state['artifacts'].items():
                self.budget['api_mutations'] += 1
                self.budget['artifact_deletes'] += 1
                path = '/api/artifacts/' + self.hub.quote(art['id'])
                code, body = self.hub.delete(path)
                gcode, _ = self.hub.get(path)
                out.append({'label': label, 'id': art['id'], 'delete_http': code, 'get_http': gcode})
                self.state['deletes'][art['id']] = {'kind': 'artifact', 'delete_http': code, 'get_http': gcode}
            return out
        step('artifact_deletes', delete_artifacts)

        # 6) delete the owned node
        def delete_node():
            if not self.state.get('child_node_id'):
                return {'skipped': 'no owned node was enrolled in this attempt'}
            self.budget['api_mutations'] += 1
            self.budget['node_deletes'] += 1
            path = '/api/nodes/' + self.hub.quote(self.state['child_node_id'])
            code, body = self.hub.delete(path)
            gcode, _ = self.hub.get(path)
            self.state['deletes'][self.state['child_node_id']] = {'kind': 'node', 'delete_http': code,
                                                                 'get_http': gcode}
            return {'id': self.state['child_node_id'], 'delete_http': code, 'delete_body': body,
                    'get_http': gcode}
        step('node_delete', delete_node)

        res['get_http_by_id'] = {k: v['get_http'] for k, v in self.state['deletes'].items()}
        res['budget'] = dict(self.budget)
        self.rec('phase15-cleanup.json', res)
        return res

    # ---------- phase 16: oracle ----------
    def phase16_oracle(self):
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
        leftovers = {'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
                     'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
                     'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        base = child_agent.OwnedChild(NONCE).host_baseline()
        guard = self.results.get('guard') or {}
        baseline_counts = guard.get('baseline_counts', {})
        oracle = {'at_utc': NOW(), 'owned_prefix': PREFIX,
                  'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
                  'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
                  'shared_artifact_still_present': any(a['id'] == SHARED_ARTIFACT for a in arts),
                  'shared_app_still_present': any(a['id'] == SHARED_APP for a in apps),
                  'counts_after': {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)},
                  'counts_baseline': baseline_counts,
                  'counts_returned_to_baseline': (len(arts) == baseline_counts.get('artifacts')
                                                  and len(apps) == baseline_counts.get('applications')
                                                  and len(nodes) == baseline_counts.get('nodes')),
                  'native2_host_after': base,
                  'native2_clean': (not base['owned_units'] and not base['etc_systemd_owned']
                                    and not base['wants_owned'] and not base['owned_mounts']
                                    and not base['root_exists'] and not base['opt_root_exists']
                                    and not base['child_config_exists']
                                    and not base['owned_child_processes']),
                  'agent_binary_sha_unchanged': base['agent_binary_sha256'] == child_agent.AGENT_SHA,
                  'protected_units_active': (base['primary_agent_unit'].get('ActiveState') == 'active'
                                             and base['repair_agent_unit'].get('ActiveState') == 'active'),
                  'etc_systemd_count_baseline': (guard.get('native2_baseline') or {}).get('etc_systemd_total'),
                  'etc_systemd_count_after': base['etc_systemd_total'],
                  'etc_systemd_count_unchanged': ((guard.get('native2_baseline') or {}).get('etc_systemd_total')
                                                  == base['etc_systemd_total']),
                  'cleanup_results': self.results.get('cleanup', {}),
                  'node_delete_get_http': (self.state['deletes'].get(self.state['child_node_id']) or {}).get('get_http')}
        oracle['clean'] = (ok and oracle['hub_leftover_clear'] and oracle['shared_artifact_still_present']
                           and oracle['shared_app_still_present'] and oracle['counts_returned_to_baseline']
                           and oracle['native2_clean'] and oracle['agent_binary_sha_unchanged']
                           and oracle['protected_units_active'] and oracle['etc_systemd_count_unchanged']
                           and oracle['node_delete_get_http'] == 404)
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle

    # ---------- driver ----------
    def run(self):
        phases = [
            ('guard', self.phase0_guard, []),
            ('child', self.phase1_child, ['guard']),
            ('artifacts', self.phase2_artifacts, ['child']),
            ('app_create', self.phase3_app_create, ['artifacts']),
            ('install_v1', self.phase4_install_v1, ['app_create']),
            ('install_idempotent', self.phase5_install_idempotent, ['install_v1']),
            ('upgrade_v2', self.phase6_upgrade_v2, ['install_idempotent']),
            ('upgrade_idempotent', self.phase7_upgrade_idempotent, ['upgrade_v2']),
            ('persistence_v2', self.phase8_persistence, ['upgrade_idempotent']),
            ('health_rollback', self.phase9_health_rollback, ['persistence_v2']),
            ('recover_v2', self.phase10_recover_v2, ['health_rollback']),
            ('ar28_persistence', self.phase11_ar28_persistence, ['recover_v2']),
            ('crash_rollback', self.phase12_crash_rollback, ['ar28_persistence']),
            ('recover_after_crash', self.phase13_recover_after_crash, ['crash_rollback']),
            ('backup_failures', self.phase14_backup_failures, ['recover_after_crash']),
        ]
        failed = []
        blocked = set()
        self.results = {}
        for name, fn, deps in phases:
            if any(d in blocked for d in deps):
                self.results[name] = {'skipped': 'dependency_failed:%s' % [d for d in deps if d in blocked]}
                blocked.add(name)
                self.progress(name, skipped=self.results[name])
                continue
            self.progress(name)
            try:
                self.results[name] = fn()
            except BaseException as exc:
                import traceback
                traceback.print_exc()
                self.results[name] = {'harness_error': '%s: %s' % (type(exc).__name__, exc)}
                failed.append(name)
                blocked.add(name)
        self.progress('cleanup')
        try:
            self.results['cleanup'] = self.phase15_cleanup()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            self.results['cleanup'] = {'harness_error': str(exc)}
            failed.append('cleanup')
        self.progress('oracle')
        try:
            self.results['oracle'] = self.phase16_oracle()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            self.results['oracle'] = {'harness_error': str(exc)}
            failed.append('oracle')
        return failed


def _ex(ex):
    if not ex:
        return None
    return {k: ex.get(k) for k in ('id', 'task_id', 'node_id', 'status', 'trigger_type',
                                   'start_time', 'end_time', 'block_reason', 'exit_code')}


def _app_actual(st, artifact_sha, label):
    return {'label': label,
            'binary_path': BINARY, 'binary_exists': st['binary'].get('exists'),
            'binary_sha256': st['binary'].get('sha256'),
            'binary_mode': st['binary'].get('mode'),
            'binary_sha_matches_artifact': st['binary'].get('sha256') == artifact_sha,
            'config_sha256': st['config'].get('sha256'),
            'unit_file_exists': st['unit_file'].get('lexists'),
            'unit_file_sha256': st['unit_file'].get('sha256'),
            'enable_symlink': st['enable_symlink'].get('lexists'),
            'unit_active': st['unit_state'].get('ActiveState'),
            'unit_sub_state': st['unit_state'].get('SubState'),
            'unit_main_pid': st['unit_state'].get('MainPID'),
            'unit_state': st['unit_state'],
            'unit_registry': st.get('unit_registry'),
            'main_process': st.get('main_process'),
            'backup_entries': [e['name'] for e in (st.get('backup_entries') or [])],
            'db_quick_check': st.get('db_quick_check'),
            'journal_rows': len(st.get('deployments') or [])}


def main():
    started = NOW()
    run = Run()
    progress = {'batch_id': 'sys-b11', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base, 'started_at_utc': started}
    run.p = progress
    hubapi.write_json('progress.json', progress)
    failed = run.run()
    summary = {'batch_id': 'sys-b11', 'nonce': NONCE, 'owned_prefix': PREFIX,
               'endpoint': run.hub.base, 'started_at_utc': started, 'finished_at_utc': NOW(),
               'failed_phases': failed, 'budget': run.budget,
               'state': {'child_node_id': run.state.get('child_node_id'),
                         'child_agent_id': run.state.get('child_agent_id'),
                         'app_id': run.state.get('app'),
                         'artifacts': run.state.get('artifacts'), 'deletes': run.state['deletes']},
               'results': run.results}
    hubapi.write_json('run-evidence.json', summary)
    print(json.dumps({'failed_phases': failed,
                      'oracle_clean': (run.results.get('oracle') or {}).get('clean'),
                      'sys50_ok': (run.results.get('health_rollback') or {}).get('sys50_ok'),
                      'sys52_ok': (run.results.get('backup_failures') or {}).get('sys52_ok'),
                      'ar29_ok': (run.results.get('crash_rollback') or {}).get('ar29_ok')},
                     ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
