#!/usr/bin/env python3
"""p-app runtime batch: Managed Application deploy / upgrade / rollback / backup /
Unit registry / health checks / crash recovery / NativeHostAdapter / file transfer.

Cases: SYS-44, SYS-45, SYS-47, SYS-48, SYS-49, SYS-51, SYS-53, SYS-62, SYS-63,
       SYS-64, SYS-65, E2E-12, E2E-13, AR-04, AR-26, AR-30, AR-32, INV-11,
       INV-12, INV-18.

Evidence chain (no product source change, no protected service touched):
  Hub API act -> real Hub request -> real owned Agent process on native-4
  -> real systemd unit / binary / config / backup / artifact cache / transfer dest
  -> Agent SQLite deployment journal + host probes -> API/UI readback.

Owned fixtures only (nonce prefix `qa-papp-<nonce>-*`); every host side effect is
created under an owned path and removed again at the end.
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import hostlib
import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-papp-%s-' % NONCE
HUB = 'http://192.168.100.209:18080'
WS = 'ws://192.168.100.209:18080'
NODE_IP = '192.168.100.247'          # real host IP (diagnostics only)
# Ownership/identity placeholder IPs: the enrollment API reuses an existing node when the
# name OR the IP matches (hub.NodeManager.findEnrollableNode), so every owned child must have
# a distinct, non-routable identity.  `<...>.invalid` is the established repair-environment
# convention (the protected repair nodes use exactly this form).
def child_ip(key):
    return '%schild-%s.invalid' % (PREFIX, key)


def child_name(key):
    return '%schild-%s' % (PREFIX, key)
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
UNIT = 'nodesteer-' + PREFIX + 'app.service'
OPT_ROOT = '/opt/' + PREFIX.rstrip('-')
BINARY = OPT_ROOT + '/app/bin/app'
CONFIG = OPT_ROOT + '/app/config/app.env'
UNIT_UNREG = 'nodesteer-' + PREFIX + 'unreg.service'
UNIT_FOREIGN = 'nodesteer-' + PREFIX + 'foreign.service'
DEV = 'evidence/'
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
SHA = lambda b: hashlib.sha256(b).hexdigest()
KNOWN_DEFECT = 'E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE'


def pad_script(body, target=4 * 1024 * 1024):
    """A valid /bin/sh script padded with comments.

    A multi-megabyte payload keeps the product's copy/fsync steps (artifact download,
    previous-version backup, atomic replace) long enough to observe the PREPARING and
    STOPPED journal windows on a real host instead of sub-millisecond transitions.
    """
    line = '# p-app-padding-%s\n'
    out = ['#!/bin/sh\n']
    size = 0
    while size < target:
        chunk = line % ('p' * 78)
        out.append(chunk)
        size += len(chunk)
    out.append(body)
    return ''.join(out).encode()


def app_script(version, mode='ok', pad=True):
    if mode == 'bad':
        return pad_script(" echo bad >/dev/null\nexit 1\n") if pad else \
            b"#!/bin/sh\n# nodesteer p-app fixture: always unhealthy\n echo bad >/dev/null\nexit 1\n"
    body = ("#!/bin/sh\n"
            "# nodesteer p-app fixture version %s\n" % version +
            'if [ -n "$APP_VERSION_FILE" ]; then echo "%s" > "$APP_VERSION_FILE"; fi\n' % version +
            'if [ -n "$APP_READY_FLAG" ]; then echo ready > "$APP_READY_FLAG"; fi\n'
            'if [ -n "$APP_LISTEN_PORT" ]; then\n'
            '  exec python3 -c "import socket,sys,time; s=socket.socket(); '
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',int(sys.argv[1]))); "
            's.listen(16); time.sleep(1000000)" "$APP_LISTEN_PORT"\n'
            'fi\n'
            'if [ -n "$APP_HTTP_PORT" ]; then\n'
            '  exec python3 -m http.server "$APP_HTTP_PORT" --bind 127.0.0.1\n'
            'fi\n'
            'exec sleep 1000000\n')
    return pad_script(body) if pad else body.encode()


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.child = {}
        self.state = {'artifacts': {}, 'apps': {}, 'execs': {}, 'deletes': {}, 'seams': [],
                      'transfers': {}, 'crash': {}, 'baseline': {}, 'verdicts': {}}
        self.budget = {'api_mutations': 0, 'artifact_uploads': 0, 'artifact_deletes': 0,
                       'app_creates': 0, 'app_updates': 0, 'app_deletes': 0, 'app_assigns': 0,
                       'deploys': 0, 'transfers': 0, 'transfer_retries': 0, 'transfer_cancels': 0,
                       'node_enroll_posts': 0, 'node_deletes': 0,
                       'child_process_starts': 0, 'child_process_stops': 0, 'child_process_kills': 0,
                       'host_seam_actions': 0, 'systemd_unit_writes': 0}
        self.results = {}

    # ---------------- plumbing ----------------
    def rec(self, name, value):
        return hubapi.write_json(DEV + name, value)

    def progress(self, phase, **extra):
        p = {'batch_id': 'p-app', 'nonce': NONCE, 'owned_prefix': PREFIX, 'phase': phase,
             'updated_at_utc': NOW(), 'budget': self.budget}
        p.update(extra)
        hubapi.write_json('progress.json', p)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def wait_child_online(self, key, timeout=120):
        deadline = time.time() + timeout
        node = None
        nid = self.state['nodes'][key]
        while time.time() < deadline:
            node = self.node(nid)
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

    def art_state(self, key='a', shas=()):
        ch = self.child[key]
        return ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG, shas=shas)

    def deploy(self, operation='upgrade', app='main', node_keys=('a',)):
        self.budget['api_mutations'] += 1
        self.budget['deploys'] += 1
        node_ids = [self.state['nodes'][k] for k in node_keys]
        code, body = self.hub.post('/api/applications/%s/deploy' % self.hub.quote(self.state['apps'][app]),
                                   {'node_ids': node_ids, 'operation': operation})
        if code != 200 or not isinstance(body, list) or not body:
            raise RuntimeError('deploy_failed_http_%s_%s' % (code, body))
        return body

    def wait_exec(self, exec_id, timeout=240):
        deadline = time.time() + timeout
        ex = None
        while time.time() < deadline:
            ex = self.exec_get(exec_id)
            if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED',
                                           'TIMED_OUT', 'BLOCKED'):
                return ex
            time.sleep(1)
        return ex

    @staticmethod
    def _ex(ex):
        if not ex:
            return None
        return {k: ex.get(k) for k in ('id', 'task_id', 'node_id', 'status', 'trigger_type',
                                       'start_time', 'end_time', 'block_reason', 'exit_code')}

    def watch_deploy(self, operation, child_key='a', shas=(), timeout=240, app='main',
                     unit=UNIT, binary=BINARY, config=CONFIG):
        """Trigger a deploy, follow the journal until the execution is terminal."""
        execs = self.deploy(operation, app=app, node_keys=(child_key,))
        exec_id = execs[0]['id']
        ch = self.child[child_key]
        samples = []
        deadline = time.time() + timeout
        last_key = None
        while time.time() < deadline:
            st = ch.app_state(self.state['apps'][app], unit, binary, config, shas=shas)
            rows = st.get('deployments') or []
            row = rows[0] if rows else {}
            phase = row.get('phase')
            key = (phase, st['binary'].get('sha256'), st['unit_state'].get('ActiveState'))
            if key != last_key:
                samples.append({'at_utc': NOW(), 'phase': phase, 'journal_id': row.get('id'),
                                'binary_sha256': st['binary'].get('sha256'),
                                'unit_active': st['unit_state'].get('ActiveState')})
                last_key = key
            ex = self.exec_get(exec_id)
            if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED',
                                           'TIMED_OUT', 'BLOCKED'):
                return {'exec_id': exec_id, 'exec': ex, 'execs': execs, 'samples': samples,
                        'final_state': self._settle(app, unit, binary, config, shas,
                                                    expect_ok=ex.get('status') == 'SUCCESS')}
            time.sleep(0.4)
        return {'exec_id': exec_id, 'exec': self.exec_get(exec_id), 'execs': execs,
                'samples': samples,
                'final_state': ch.app_state(self.state['apps'][app], unit, binary, config, shas=shas)}

    def _settle(self, app, unit, binary, config, shas, expect_ok=True, timeout=25):
        """The Agent writes the journal/registry right before reporting the result; wait until the
        read-back is complete so a terminal execution is not followed by a half-visible state."""
        ch = self.child['a']
        deadline = time.time() + timeout
        st = None
        while time.time() < deadline:
            st = ch.app_state(self.state['apps'][app], unit, binary, config, shas=shas)
            rows = st.get('deployments') or []
            done = bool(rows)
            if expect_ok:
                done = done and bool(st.get('unit_registry'))
            if done and st.get('db_quick_check') == 'ok':
                return st
            time.sleep(0.5)
        return st

    def deploy_node_state(self, app='main', child_key='a'):
        return [s for s in self.app_states(self.state['apps'][app])
                if s['node_id'] == self.state['nodes'][child_key]]

    # ---------------- app definitions ----------------
    @staticmethod
    def health(kind='systemd', target='', attempts=3, interval=2, timeout=5):
        return {'type': kind, 'target': target, 'attempts': attempts, 'interval': interval,
                'timeout': timeout}

    def app_def(self, app_key, version, artifact, args, env, config, health, binary=BINARY,
                config_path=CONFIG, unit=UNIT, name=None):
        return {'name': name or (PREFIX + app_key),
                'description': 'p-app owned managed application (nonce %s, %s)' % (NONCE, app_key),
                'artifact_id': self.state['artifacts'][artifact]['id'],
                'version': version,
                'binary_path': binary,
                'unit_name': unit,
                'arguments': args,
                'environment': env,
                'config': config,
                'config_path': config_path,
                'health_check': health}

    def put_app(self, app_key, definition):
        self.budget['api_mutations'] += 1
        self.budget['app_updates'] += 1
        code, body = self.hub.put('/api/applications/' + self.hub.quote(self.state['apps'][app_key]),
                                  definition)
        if code != 200:
            raise RuntimeError('app_update_failed_http_%s_%s' % (code, body))
        return body

    def put_app_raw(self, app_key, definition):
        self.budget['api_mutations'] += 1
        self.budget['app_updates'] += 1
        return self.hub.put('/api/applications/' + self.hub.quote(self.state['apps'][app_key]),
                            definition)

    def wait_agent_def(self, app_key, match, child_key='a', timeout=45):
        """Wait until the owned child has the desired definition (match is a dict subset)."""
        app_id = self.state['apps'][app_key]
        ch = self.child[child_key]
        deadline = time.time() + timeout
        st = None
        while time.time() < deadline:
            st = ch.app_state(app_id, UNIT, BINARY, CONFIG)
            for a in st.get('local_apps') or []:
                if a['id'] != app_id:
                    continue
                d = a.get('definition') or {}
                if all(d.get(k) == v for k, v in match.items()):
                    return {'state': st, 'definition': d, 'revision': a.get('revision')}
            time.sleep(1)
        return {'state': st, 'definition': None, 'revision': None}

    # ---------------- phase 0: guard ----------------
    def phase0_guard(self):
        out = {'at_utc': NOW(), 'endpoint': self.hub.base, 'nonce': NONCE, 'owned_prefix': PREFIX,
               'agent_binary_sha256_expected': hostlib.AGENT_SHA,
               'native_host': hostlib.HOST}
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
        for label, items, attrs in (('artifacts', arts, ('id', 'name')),
                                     ('applications', apps, ('id', 'name')),
                                     ('nodes', nodes, ('id', 'hostname'))):
            out['owned_fixture_check_' + label] = [i[attrs[0]] for i in items
                                                   if (i.get(attrs[1]) or '').startswith(PREFIX)]
            if out['owned_fixture_check_' + label]:
                raise RuntimeError('owned_fixture_already_present:' + label)
        base = hostlib.OwnedChild(NONCE).host_baseline()
        out['native4_baseline'] = base
        if (base['root_exists'] or base['opt_root_exists'] or base['child_config_exists']
                or base['owned_child_processes'] or base['owned_units'] or base['etc_systemd_owned']
                or base['wants_owned'] or base['owned_mounts'] or base['global_owned_prefix_scan']):
            raise RuntimeError('owned_host_residue_present')
        if base['agent_binary_sha256'] != hostlib.AGENT_SHA:
            raise RuntimeError('agent_binary_sha_mismatch')
        if base['primary_agent_unit'].get('ActiveState') != 'active' \
                or base['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_unit_not_active')
        out['guard_ok'] = True
        self.rec('phase00-guard.json', out)
        return out

    # ---------------- phase 1: owned children ----------------
    def _enroll_child(self, key, tag, port_seed):
        name = child_name(key)
        ip = child_ip(key)
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        code, enroll = self.hub.post('/api/nodes/enrollment',
                                     {'node_name': name, 'node_ip': ip, 'hub_address': HUB})
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s' % code)
        self.state['nodes'][key] = enroll['node_id']
        self.state['agents'][key] = enroll['agent_id']
        ch = hostlib.OwnedChild(NONCE, tag, enroll['node_id'], enroll['agent_id'], name, ip,
                                self.hub.cred['registration_token'])
        self.child[key] = ch
        started = ch.write_config_and_start(WS)
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(key, timeout=120)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:' + key)
        if not (node.get('capabilities') or {}).get('application_deploy'):
            raise RuntimeError('owned_child_lacks_application_deploy_capability:' + key)
        if node.get('id') != enroll['node_id'] or node.get('agent_id') != enroll['agent_id']:
            raise RuntimeError('owned_child_identity_mismatch:' + key)
        return {'key': key, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                'node_name': name, 'node_ip': ip, 'root': ch.root, 'opt_root': ch.opt_root,
                'data_dir': ch.data, 'processes': started.get('owned_processes'),
                'node_after_start': {k: node.get(k) for k in
                                     ('id', 'agent_id', 'hostname', 'ip', 'status', 'sync_status',
                                      'global_revision', 'deployment_mode', 'agent_version',
                                      'capabilities')}}

    def phase1_children(self):
        self.state['nodes'] = {}
        self.state['agents'] = {}
        out = {'at_utc': NOW(), 'children': {}, 'registration_token_persisted': False,
               'identity_isolation_note':
                   'the enrollment API reuses a node when its name or IP matches an existing node, so '
                   'each owned child is enrolled with a distinct non-routable <name>.invalid identity'}
        for key, tag in (('a', ''), ('b', '-b'), ('c', '-c')):
            out['children'][key] = self._enroll_child(key, tag, 0)
        ids = [c['node_id'] for c in out['children'].values()]
        agents = [c['agent_id'] for c in out['children'].values()]
        out['distinct_node_identities'] = len(set(ids)) == len(ids) and len(set(agents)) == len(agents)
        self.rec('phase01-owned-children.json', out)
        if not out['distinct_node_identities']:
            raise RuntimeError('owned_child_identities_not_isolated')
        return out

    # ---------------- phase 2: artifacts ----------------
    def phase2_artifacts(self):
        contents = {'A1': app_script('v1'), 'A2': app_script('v2'), 'A3': app_script('v3', 'bad'),
                    'A4': app_script('v1.5')}
        out = {'at_utc': NOW(), 'artifacts': {}}
        roles = {'A1': 'good v1 (baseline install + rollback target)',
                 'A2': 'good v2 (upgrade target)',
                 'A3': 'always-exit-1 (systemd health failure / failed upgrade)',
                 'A4': 'good v1.5 (uncached artifact for the prefetch-failure branch)'}
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
                                       'name': name, 'local_sha256_matches_hub': True, 'role': roles[label]}
        self.rec('phase02-artifacts.json', out)
        return out

    # ---------------- phase 3: applications + assign ----------------
    def app_baseline(self, key, version, artifact, profile, config_extra='', env=None,
                     health=None, binary=BINARY, config_path=CONFIG, unit=UNIT):
        return self.app_def(key, version, artifact, ['--profile=' + profile, '--nonce=' + NONCE],
                            env if env is not None else {'APP_PROFILE': profile, 'APP_NONCE': NONCE},
                            config_extra, health or self.health(),
                            binary=binary, config_path=config_path, unit=unit)

    def phase3_app_create(self):
        out = {'at_utc': NOW(), 'apps': {}}
        # main lifecycle application
        defn = self.app_baseline('main', '1.0', 'A1', 'v1', 'APP_MODE=v1\n')
        self.budget['api_mutations'] += 1
        self.budget['app_creates'] += 1
        code, body = self.hub.post('/api/applications', defn)
        if code != 200 or not body.get('id'):
            raise RuntimeError('app_create_failed_http_%s_%s' % (code, body))
        self.state['apps']['main'] = body['id']
        out['apps']['main'] = {'id': body['id'], 'hub_definition': {k: body.get(k) for k in
                               ('id', 'name', 'version', 'revision', 'artifact_id', 'binary_path',
                                'config_path', 'unit_name', 'arguments', 'health_check')}}
        # never-deployed application for the Unit Registry negative test
        defn2 = self.app_baseline('unreg', '0.1', 'A1', 'unreg', 'APP_MODE=unreg\n', unit=UNIT_UNREG)
        self.budget['api_mutations'] += 1
        self.budget['app_creates'] += 1
        code2, body2 = self.hub.post('/api/applications', defn2)
        if code2 != 200 or not body2.get('id'):
            raise RuntimeError('app_unreg_create_failed_http_%s_%s' % (code2, body2))
        self.state['apps']['unreg'] = body2['id']
        out['apps']['unreg'] = {'id': body2['id'], 'unit_name': body2.get('unit_name')}
        # assign both to child A
        for key in ('main', 'unreg'):
            self.budget['api_mutations'] += 1
            self.budget['app_assigns'] += 1
            c2, _ = self.hub.post('/api/applications/%s/assign' % self.hub.quote(self.state['apps'][key]),
                                  {'node_ids': [self.state['nodes']['a']]})
            out['apps'][key]['assign_http'] = c2
            if c2 != 200:
                raise RuntimeError('app_assign_failed_%s_http_%s' % (key, c2))
        # this sampler window covers the assign-triggered prefetch (4 MiB artifact) plus the deploy
        self.budget['host_seam_actions'] += 1
        self.child['a'].sample_net('prefetch', 60, 0.05)
        got = self.wait_agent_def('main', {'version': '1.0', 'artifact_id': self.state['artifacts']['A1']['id']})
        out['apps']['main']['agent_local_definition'] = got['definition']
        out['apps']['main']['desired_state_delivered'] = got['definition'] is not None
        out['apps']['main']['artifact_url_from_hub'] = (got['definition'] or {}).get('artifact_url')
        if got['definition'] is None:
            raise RuntimeError('desired_state_not_delivered_to_agent')
        self.rec('phase03-app-create.json', out)
        return out

    # ---------------- phase 4: first install (SYS-45 / SYS-53 modes / SYS-47 registry) ----------------
    def phase4_install_v1(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        ch = self.child['a']
        before = ch.deployments()
        alive = ch.process_alive()
        self.state['agent_pid'] = (alive.get('pids') or [None])[0]
        out_pid = {'owned_agent_pids': alive.get('pids')}
        expect = ['PREPARING', 'STOPPED', 'REPLACED', 'STARTED', 'VERIFYING', 'DONE']
        # artifact payload evidence window (covers prefetch/download + install)
        self.budget['host_seam_actions'] += 1
        ch.sample_net('prefetch', 60, 0.01)
        # Force a cold cache so the initial deploy really performs Resolve -> Download -> Verify
        # inside PREPARING.  The cache file is an owned fixture file inside the owned data dir;
        # the artifact store row still exists, so ArtifactCache.Has() re-hashes and fails, which
        # makes Prefetch download the 4 MiB payload again.
        cache_file = ch.data + '/artifacts/' + sha1
        self.budget['host_seam_actions'] += 1
        cold = ch.seam(cache_file, 'rm')
        # Pre-existing owned config fixture: it makes the PREPARING backup step (which the product runs
        # before it writes anything) long enough to observe on a real host.  The binary and unit paths
        # stay untouched, which the backup 'absent:' markers in the journal prove afterwards.
        pre_cfg = self._make_blob('a', CONFIG, 4 * 1024 * 1024, 0o644)
        out_pid['pre_existing_config_fixture'] = pre_cfg
        attempts = []
        for attempt in (1, 2):
            rows_before = ch.deployments()
            self.budget['host_seam_actions'] += 1
            ch.start_watcher('install%d' % attempt, UNIT, BINARY,
                             after_id=(rows_before[0]['id'] if rows_before else ''),
                             mode='observe', timeout=120)
            res = self.watch_deploy('deploy', shas=[sha1])
            watch = ch.read_watcher('install%d' % attempt, wait_exit=True, timeout=140)
            attempts.append({'attempt': attempt, 'execution': self._ex(res['exec']),
                             'block_reason': (res['exec'] or {}).get('block_reason'),
                             'phases': watch['phase_sequence'], 'rows': watch['rows']})
            if (res['exec'] or {}).get('status') == 'SUCCESS':
                break
            out_pid['retry_due_to'] = (res['exec'] or {}).get('block_reason')
        st = res['final_state']
        node_state = self.deploy_node_state()
        ok_attempt = next((a for a in attempts if (a['execution'] or {}).get('status') == 'SUCCESS'), None)
        seq_ok = (ok_attempt or {}).get('phases') or []
        union = []
        for a in attempts:
            for p in a['phases']:
                if p not in union:
                    union.append(p)
        union.sort(key=lambda p: expect.index(p) if p in expect else 99)
        out = {'at_utc': NOW(), 'operation': 'deploy', 'execution': self._ex(res['exec']),
               'owned_agent_identity': out_pid,
               'cold_cache_seam': cold,
               'install_attempts': attempts,
               'journal_phase_sequence': res['samples'],
               'artifact_v1': self.state['artifacts']['A1'],
               'expected_stage_map': {
                   'resolve': 'Hub resolves artifact -> artifact_url/api/artifacts/<id>/download + sha256',
                   'download_verify': 'agent ArtifactCache.Prefetch: GET -> <dir>/<sha>.tmp -> sha256 -> atomic rename',
                   'install': 'AtomicReplace(binary_path, 0755) then journal phase REPLACED',
                   'unit': 'InstallUnit(/etc/systemd/system/<unit>) + daemon-reload + enable',
                   'start': 'journal STARTED then StartService',
                   'health': 'journal VERIFYING then systemd health check until active',
                   'done': 'RegisterUnit + journal DONE'},
               'node_state_hub': node_state,
               'local_app_definition': self.wait_agent_def('main', {'version': '1.0'})['definition'],
               'stage_order_observed': union,
               'stage_order_by_attempt': {str(a['attempt']): a['phases'] for a in attempts},
               'stage_missing_phases': [p for p in expect if p not in union],
               'pre_existing_owned_config': {'path': CONFIG, 'sha256': pre_cfg['sha256'],
                                             'size': pre_cfg['size'],
                                             'note': 'owned fixture file; it exists before the first '
                                                     'install so the PREPARING backup step is observable'}}
        a = st
        out['actual_state'] = self._actual(a, sha1, 'v1')
        out['stage_order_ok'] = (all(p in union for p in expect)
                                 and seq_ok == [p for p in expect if p in seq_ok])
        row0 = (a.get('deployments') or [{}])[0]
        out['install_journal_row'] = row0
        out['backup_markers'] = {
            'binary_backup_absent': row0.get('backup_path') == 'absent:',
            'config_backup_recorded': bool(row0.get('backup_config_path')),
            'unit_backup_absent': row0.get('backup_unit_path') == 'absent:',
            'config_backup_matches_pre_existing': bool(row0.get('backup_config_path'))
            and row0.get('backup_config_path') != 'absent:'
            and list(ch.file_shas([row0['backup_config_path']]).values())[0].get('sha256') == pre_cfg['sha256']}
        out['install_ok'] = ((res['exec'] or {}).get('status') == 'SUCCESS'
                             and out['actual_state']['binary_sha_matches_artifact']
                             and out['actual_state']['unit_active'] == 'active'
                             and out['actual_state']['enable_symlink']
                             and bool(a.get('unit_registry'))
                             and 'PREPARING' in union
                             and out['backup_markers']['binary_backup_absent']
                             and out['backup_markers']['config_backup_matches_pre_existing'])
        out['unit_registry_after_install'] = a.get('unit_registry')
        out['prefetch_cache'] = {
            'cache_file_for_sha': (a.get('watched_sha_files') or {}).get(sha1),
            'artifact_rows': a.get('artifact_rows'),
            'cache_dir_entries': a.get('artifact_cache_entries'),
            'cache_dir': a.get('artifact_cache_dir')}
        out['native_adapter_modes'] = {
            'binary_mode': a['binary'].get('mode'),
            'binary_uid_gid': [a['binary'].get('uid'), a['binary'].get('gid')],
            'config_mode': a['config'].get('mode'),
            'config_uid_gid': [a['config'].get('uid'), a['config'].get('gid')],
            'unit_file_mode': a['unit_file'].get('mode'),
            'binary_dir_mode': a['binary_dir'].get('mode'),
            'binary_dir_entries': a['binary_dir_entries'],
            'agent_uid': 0,
            'note': 'the owned child Agent runs as root, so the product writes root:root files'}
        out['atomic_replace_evidence'] = {
            'binary_dir_entries_after': a['binary_dir_entries'],
            'tmp_leftovers': [n for n in (a['binary_dir_entries'] or []) if n.startswith('.tmp-')],
            'no_tmp_leftover': not [n for n in (a['binary_dir_entries'] or []) if n.startswith('.tmp-')]}
        out['net_artifact_payload_window'] = self._net_summary('prefetch',
                                                              self.state['artifacts']['A1']['size'],
                                                              expected_pid=self.state.get('agent_pid'))
        log = ch.log_tail()['tail']
        out['agent_log_artifact_lines'] = [l.strip()[:200] for l in log.splitlines()
                                           if 'artifact' in l.lower()][-8:]
        self.rec('phase04-install-v1.json', out)
        if not out['install_ok']:
            raise RuntimeError('phase4_install_v1_failed')
        return out

    def refresh_agent_pid(self):
        alive = self.child['a'].process_alive()
        self.state['agent_pid'] = (alive.get('pids') or [None])[0]
        return self.state['agent_pid']

    def _net_summary(self, name, payload_size, expected_pid=None):
        """INV-18 evidence: the payload must travel on its own HTTP socket, not the control WS.

        Only sockets owned by the owned child Agent process are considered when `expected_pid`
        is given, so sockets of other Agents on the same host cannot be mistaken for ours.
        """
        rows = self.child['a'].read_net(name)
        per_socket = {}
        for r in rows:
            for s in r.get('sockets', []):
                if expected_pid and str(s.get('pid')) != str(expected_pid):
                    continue
                key = (s.get('local'), s.get('pid'))
                cur = per_socket.setdefault(key, {'first_recv': s.get('bytes_received'),
                                                  'first_sent': s.get('bytes_sent'),
                                                  'last_recv': s.get('bytes_received'),
                                                  'last_sent': s.get('bytes_sent'), 'seen': 0})
                cur['last_recv'] = s.get('bytes_received')
                cur['last_sent'] = s.get('bytes_sent')
                cur['seen'] += 1
        sockets = []
        for key, v in per_socket.items():
            recv_delta = (None if v['first_recv'] is None or v['last_recv'] is None
                          else v['last_recv'] - v['first_recv'])
            sent_delta = (None if v['first_sent'] is None or v['last_sent'] is None
                          else v['last_sent'] - v['first_sent'])
            # a short-lived socket may be sampled only once; its cumulative counter is then the best
            # (and usually sufficient) upper bound for the bytes it carried.
            evidence = max([x for x in (recv_delta, sent_delta, v['last_recv'], v['last_sent']) if x is not None]
                           or [0])
            sockets.append({'local': key[0], 'pid': key[1], 'samples': v['seen'],
                            'recv_delta': recv_delta, 'sent_delta': sent_delta,
                            'last_recv': v['last_recv'], 'last_sent': v['last_sent'],
                            'payload_evidence_bytes': evidence})
        sockets.sort(key=lambda s: -s['payload_evidence_bytes'])
        bulky = [s for s in sockets if s['payload_evidence_bytes'] >= payload_size * 0.9]
        data = bulky[0] if bulky else None
        non_data = [s for s in sockets if not data or s['local'] != data['local']]
        control = max(non_data, key=lambda s: s['samples']) if non_data else None
        return {'sample_rows': len(rows), 'payload_size': payload_size,
                'filtered_to_agent_pid': expected_pid, 'sockets': sockets[:8],
                'distinct_agent_sockets': len(sockets),
                'control_ws_socket': ({'local': control['local'], 'samples': control['samples'],
                                       'recv_delta': control['recv_delta'],
                                       'sent_delta': control['sent_delta'],
                                       'payload_evidence_bytes': control['payload_evidence_bytes']}
                                      if control else None),
                'payload_data_socket': ({'local': data['local'], 'samples': data['samples'],
                                         'recv_delta': data['recv_delta'],
                                         'sent_delta': data['sent_delta'],
                                         'payload_evidence_bytes': data['payload_evidence_bytes']}
                                        if data else None),
                'payload_on_separate_socket_from_control': bool(
                    control and data and data['local'] != control['local']),
                'sockets_with_payload_bytes': bulky,
                'http_data_socket_detected': bool(bulky),
                'note': ('the control WebSocket is a single long-lived socket; a separate socket whose '
                         'transferred bytes cover the payload proves the payload itself does not travel '
                         'over the control long connection')}

    def _net_summary_b(self, name, payload_size, expected_pid=None):
        orig = self.child['a']
        try:
            self.child['a'] = self.child['b']
            return self._net_summary(name, payload_size, expected_pid=expected_pid)
        finally:
            self.child['a'] = orig

    def _actual(self, st, artifact_sha, label):
        return {'label': label,
                'binary_path': BINARY, 'binary_exists': st['binary'].get('exists'),
                'binary_sha256': st['binary'].get('sha256'),
                'binary_sha_matches_artifact': st['binary'].get('sha256') == artifact_sha,
                'binary_mode': st['binary'].get('mode'),
                'config_sha256': st['config'].get('sha256'),
                'config_mode': st['config'].get('mode'),
                'unit_file_lexists': st['unit_file'].get('lexists'),
                'unit_file_sha256': st['unit_file'].get('sha256'),
                'unit_file_content_flags': st.get('unit_file_content'),
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

    # ---------------- phase 5: repeat prefetch cache hit + install idempotency (SYS-44 / AR-26) ----------------
    def phase5_idempotent(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        before = self.art_state('a', shas=[sha1])
        cache_before = (before.get('watched_sha_files') or {}).get(sha1, {})
        self.budget['host_seam_actions'] += 1
        self.child['a'].sample_net('repeat', 10, 0.05)
        res = self.watch_deploy('deploy', shas=[sha1])
        after = res['final_state']
        cache_after = (after.get('watched_sha_files') or {}).get(sha1, {})
        execs_before = len(self.hub.json('/api/applications/%s/executions'
                                         % self.hub.quote(self.state['apps']['main'])))
        out = {'at_utc': NOW(), 'execution': self._ex(res['exec']),
               'binary_sha_before': before['binary'].get('sha256'),
               'binary_sha_after': after['binary'].get('sha256'),
               'unit_file_sha_before': before['unit_file'].get('sha256'),
               'unit_file_sha_after': after['unit_file'].get('sha256'),
               'unit_active': after['unit_state'].get('ActiveState'),
               'journal_rows_before': len(before.get('deployments') or []),
               'journal_rows_after': len(after.get('deployments') or []),
               'executions_before': execs_before,
               'owned_units_on_host': self.child['a'].inventory(UNIT)['owned_systemd_units'],
               'prefetch_cache_before': {'exists': cache_before.get('exists'),
                                         'sha256': cache_before.get('sha256'),
                                         'mtime': cache_before.get('mtime'),
                                         'mode': cache_before.get('mode')},
               'prefetch_cache_after': {'exists': cache_after.get('exists'),
                                        'sha256': cache_after.get('sha256'),
                                        'mtime': cache_after.get('mtime'),
                                        'mode': cache_after.get('mode')},
               'journal_phase_sequence': res['samples'],
               'net_samples_summary': self._net_summary('repeat', self.state['artifacts']['A1']['size'],
                                                        expected_pid=self.state.get('agent_pid')),
               'node_state_hub': self.deploy_node_state()}
        out['cache_hit_no_rewrite'] = (cache_before.get('mtime') == cache_after.get('mtime')
                                       and cache_before.get('sha256') == sha1)
        tb = (before.get('unit_file_text') or '').splitlines()
        ta = (after.get('unit_file_text') or '').splitlines()
        out['unit_file_text_before'] = tb
        out['unit_file_text_after'] = ta
        out['unit_file_content_stable'] = tb == ta
        out['unit_file_diff'] = {'removed': [l for l in tb if l not in ta],
                                 'added': [l for l in ta if l not in tb]}
        out['registry_after'] = after.get('unit_registry')
        out['no_duplicate_unit'] = len(out['owned_units_on_host']) == 1
        out['idempotent_install'] = (out['binary_sha_before'] == out['binary_sha_after'] == sha1
                                     and out['unit_active'] == 'active'
                                     and (res['exec'] or {}).get('status') == 'SUCCESS'
                                     and out['journal_rows_after'] > out['journal_rows_before']
                                     and out['no_duplicate_unit']
                                     and len(after.get('unit_registry') or []) == 1
                                     and bool(after['unit_file_content'])
                                     and after['unit_file_content'].get('has_v1'))
        out['observations'] = []
        if not out['unit_file_content_stable']:
            out['observations'].append(
                'generated unit file content is not byte-stable across identical deploys: the '
                'Environment= line order follows Go map iteration, so /etc/systemd/system/<unit>.service '
                'is rewritten with a different sha256 although the definition is unchanged '
                '(semantics identical: same ExecStart/Environment set/EnvironmentFile)')
        self.rec('phase05-prefetch-repeat-idempotent.json', out)
        if not out['idempotent_install'] or not out['cache_hit_no_rewrite']:
            raise RuntimeError('phase5_idempotency_or_cache_failed')
        return out

    # ---------------- phase 6: persistence across an owned child restart (AR-26) ----------------
    def phase6_persistence(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        before = self.art_state('a', shas=[sha1])
        restarted = self.child['a'].restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online('a', timeout=120)
        time.sleep(6)
        after = self.art_state('a', shas=[sha1])
        out = {'at_utc': NOW(), 'restart': restarted,
               'node_after_restart': {k: (node or {}).get(k) for k in
                                      ('status', 'sync_status', 'global_revision')},
               'before': self._actual(before, sha1, 'v1'),
               'after': self._actual(after, sha1, 'v1'),
               'db_quick_check_after': after.get('db_quick_check'),
               'unit_registry_after': after.get('unit_registry'),
               'journal_rows_after': len(after.get('deployments') or []),
               'node_state_hub': self.deploy_node_state()}
        out['persistent_and_idempotent'] = (out['after']['binary_sha_matches_artifact']
                                           and out['after']['unit_active'] == 'active'
                                           and out['after']['enable_symlink']
                                           and after.get('db_quick_check') == 'ok'
                                           and len(after.get('unit_registry') or []) == 1
                                           and len(after.get('deployments') or [])
                                           == len(before.get('deployments') or []))
        out['desired_state_reconciled'] = (
            (self.app_hub(self.state['apps']['main']) or {}).get('version') == '1.0'
            and bool(out['node_state_hub'])
            and out['node_state_hub'][0].get('health') == 'healthy'
            and out['node_state_hub'][0].get('version') == '1.0')
        self.rec('phase06-persistence.json', out)
        if not out['persistent_and_idempotent']:
            raise RuntimeError('phase6_persistence_failed')
        return out

    # ---------------- phase 7: prefetch failure + recovery (SYS-44) ----------------
    def phase7_prefetch_failure(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        sha4 = self.state['artifacts']['A4']['sha256']
        artdir = self.child['a'].data + '/artifacts'
        baseline = self.art_state('a', shas=[sha1, sha4])
        self.budget['host_seam_actions'] += 1
        seam = self.child['a'].seam(artdir, 'chattr_plus_i')
        self.state['seams'].append({'path': artdir, 'action': 'chattr_plus_i', 'release': 'chattr_minus_i'})
        defn = self.app_baseline('main', '1.5', 'A4', 'v1.5', 'APP_MODE=v1.5\n')
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '1.5'})
        res = self.watch_deploy('upgrade', shas=[sha4, sha1])
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        out = {'at_utc': NOW(), 'seam': seam, 'artifact_uncached_sha256': sha4,
               'execution': self._ex(res['exec']),
               'block_reason_raw': (res['exec'] or {}).get('block_reason'),
               'journal_row_after': row,
               'journal_phase_sequence': res['samples'],
               'binary_sha_unchanged': st['binary'].get('sha256') == sha1,
               'actual_state_after': self._actual(st, sha1, 'v1'),
               'cache_dir_probe': st.get('artifact_cache_dir'),
               'node_state_hub': self.deploy_node_state(),
               'no_host_mutation': (st['binary'].get('sha256') == baseline['binary'].get('sha256')
                                    and st['unit_file'].get('sha256') == baseline['unit_file'].get('sha256')
                                    and st['unit_state'].get('ActiveState') == 'active'
                                    and st.get('unit_registry') == baseline.get('unit_registry'))}
        out['prefetch_failure_explicit'] = ((res['exec'] or {}).get('status') == 'FAILED'
                                            and 'artifact download failed' in
                                            ((res['exec'] or {}).get('block_reason') or '')
                                            and row.get('phase') == 'DONE'
                                            and out['no_host_mutation'])
        # release the seam and prove the product recovers (cache hit back to v1)
        rel = self.child['a'].seam(artdir, 'chattr_minus_i')
        self.budget['host_seam_actions'] += 1
        out['seam_release'] = rel
        self.put_app('main', self.app_baseline('main', '1.0', 'A1', 'v1', 'APP_MODE=v1\n'))
        self.wait_agent_def('main', {'version': '1.0'})
        res2 = self.watch_deploy('upgrade', shas=[sha1])
        st2 = res2['final_state']
        out['recovery_execution'] = self._ex(res2['exec'])
        out['recovery_state'] = self._actual(st2, sha1, 'v1')
        out['recovery_ok'] = ((res2['exec'] or {}).get('status') == 'SUCCESS'
                              and st2['binary'].get('sha256') == sha1
                              and st2['unit_state'].get('ActiveState') == 'active')
        self.rec('phase07-prefetch-failure.json', out)
        if not out['prefetch_failure_explicit'] or not out['recovery_ok']:
            raise RuntimeError('phase7_prefetch_failure_failed')
        return out

    # ---------------- phase 8: Unit Registry / unowned unit (SYS-47 / INV-12) ----------------
    def _registry_diag(self, ex, diagnostics=()):
        """Collect the rejection text from the execution and (fallback) the node state."""
        text = ((ex or {}).get('block_reason') or '')
        for d in diagnostics:
            text += ' | ' + ((d or {}).get('error') or '')
        return text

    def phase8_unit_registry(self):
        out = {'at_utc': NOW(), 'scenarios': {}}
        # (a) never-deployed application: operation must be rejected, unit untouched
        inv_before = self.child['a'].inventory(UNIT_UNREG)
        ns0 = self.deploy_node_state()
        execs = self.deploy('start', app='unreg')
        ex = self.wait_exec(execs[0]['id'], timeout=90)
        inv_after = self.child['a'].inventory(UNIT_UNREG)
        diag = self._registry_diag(ex, self.deploy_node_state())
        out['scenarios']['unregistered_application'] = {
            'app_id': self.state['apps']['unreg'], 'unit_name': UNIT_UNREG,
            'execution': self._ex(ex),
            'diagnostic_text': diag,
            'expected_message': 'unit not registered',
            'message_matched': 'unit not registered' in diag,
            'status_failed': (ex or {}).get('status') == 'FAILED',
            'unit_file_before': inv_before['owned_unit_file_exists'],
            'unit_file_after': inv_after['owned_unit_file_exists'],
            'unit_state_after': inv_after['owned_unit_state'],
            'node_state_before': ns0,
            'host_unit_untouched': (not inv_after['owned_unit_file_exists']
                                    and inv_after['owned_unit_state'].get('ActiveState') in ('inactive', ''))}
        # (b) unowned NodeSteer-namespaced service that this product did not register
        self.budget['host_seam_actions'] += 1
        self.budget['systemd_unit_writes'] += 1
        unit_body = ('[Unit]\nDescription=p-app owned foreign probe unit\n\n'
                     '[Service]\nExecStart=/bin/sleep 100000\n\n[Install]\nWantedBy=multi-user.target\n')
        w = self.child['a'].remote_py(
            "import json,os,subprocess\n"
            "p=%r; body=%r; u=%r\n"
            "open(p,'w').write(body)\n"
            "subprocess.run(['systemctl','daemon-reload'])\n"
            "r=subprocess.run(['systemctl','start',u],capture_output=True,text=True)\n"
            "s=subprocess.run(['systemctl','show','--no-pager','--property=ActiveState,MainPID',u],capture_output=True,text=True)\n"
            "print(json.dumps({'written':os.path.isfile(p),'start':{'rc':r.returncode,'err':r.stderr.strip()[:200]},"
            "'state':dict(x.split('=',1) for x in s.stdout.splitlines() if '=' in x)}))\n"
            % ('/etc/systemd/system/' + UNIT_FOREIGN, unit_body, UNIT_FOREIGN))
        # point the unregistered app at that live unit
        defn = self.app_baseline('unreg', '0.1', 'A1', 'unreg', 'APP_MODE=unreg\n', unit=UNIT_FOREIGN)
        self.put_app('unreg', defn)
        time.sleep(4)
        execs2 = self.deploy('start', app='unreg')
        ex2 = self.wait_exec(execs2[0]['id'], timeout=90)
        fstate = self.child['a'].remote_py(
            "import json,subprocess\n"
            "u=%r\n"
            "s=subprocess.run(['systemctl','show','--no-pager','--property=ActiveState,MainPID,Id',u],capture_output=True,text=True)\n"
            "print(json.dumps(dict(x.split('=',1) for x in s.stdout.splitlines() if '=' in x)))\n" % UNIT_FOREIGN)
        diag2 = self._registry_diag(ex2, self.deploy_node_state())
        out['scenarios']['unowned_live_unit'] = {
            'foreign_unit': UNIT_FOREIGN, 'setup': w,
            'execution': self._ex(ex2),
            'diagnostic_text': diag2,
            'message_matched': 'unit not registered' in diag2,
            'status_failed': (ex2 or {}).get('status') == 'FAILED',
            'foreign_unit_after': fstate,
            'foreign_unit_still_active': fstate.get('ActiveState') == 'active',
            'foreign_main_pid_unchanged': bool(w['state'].get('MainPID'))
            and fstate.get('MainPID') == w['state'].get('MainPID')}
        # (c) positive: the registered managed unit is operable
        execs3 = self.deploy('stop', app='main')
        ex3 = self.wait_exec(execs3[0]['id'], timeout=90)
        inv_stop = self.child['a'].inventory(UNIT)
        ns_stop = self.deploy_node_state()
        execs4 = self.deploy('start', app='main')
        ex4 = self.wait_exec(execs4[0]['id'], timeout=90)
        inv_start = self.child['a'].inventory(UNIT)
        ns_start = self.deploy_node_state()
        out['scenarios']['registered_managed_unit'] = {
            'stop_execution': self._ex(ex3), 'start_execution': self._ex(ex4),
            'unit_state_after_stop': inv_stop['owned_unit_state'],
            'unit_state_after_start': inv_start['owned_unit_state'],
            'node_state_after_stop': ns_stop, 'node_state_after_start': ns_start,
            'stop_ok': (inv_stop['owned_unit_state'].get('ActiveState') in ('inactive', 'failed')),
            'start_ok': inv_start['owned_unit_state'].get('ActiveState') == 'active'}
        out['sys47_ok'] = (out['scenarios']['unregistered_application']['message_matched']
                           and out['scenarios']['unregistered_application']['status_failed']
                           and out['scenarios']['unregistered_application']['host_unit_untouched']
                           and out['scenarios']['unowned_live_unit']['message_matched']
                           and out['scenarios']['unowned_live_unit']['status_failed']
                           and out['scenarios']['unowned_live_unit']['foreign_unit_still_active']
                           and out['scenarios']['unowned_live_unit']['foreign_main_pid_unchanged']
                           and out['scenarios']['registered_managed_unit']['stop_ok']
                           and out['scenarios']['registered_managed_unit']['start_ok'])
        self.rec('phase08-unit-registry.json', out)
        if not out['sys47_ok']:
            raise RuntimeError('phase8_unit_registry_failed')
        return out

    # ---------------- phase 9: upgrade v1 -> v2 (SYS-49) ----------------
    def phase9_upgrade_v2(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        sha2 = self.state['artifacts']['A2']['sha256']
        before = self.art_state('a', shas=[sha1, sha2])
        prev_binary_sha = before['binary'].get('sha256')
        defn = self.app_baseline('main', '2.0', 'A2', 'v2', 'APP_MODE=v2\n')
        put = self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '2.0', 'artifact_id': self.state['artifacts']['A2']['id']})
        self.budget['host_seam_actions'] += 1
        self.child['a'].start_watcher('upg', UNIT, BINARY, after_id=(before.get('deployments') or [{}])[0].get('id') or '',
                                      mode='observe', timeout=120)
        res = self.watch_deploy('upgrade', shas=[sha2])
        watch = self.child['a'].read_watcher('upg', wait_exit=True, timeout=140)
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        backup_sha = self.child['a'].file_shas([row.get('backup_path')]) if row.get('backup_path') else {}
        out = {'at_utc': NOW(), 'operation': 'upgrade',
               'app_revision_after': put.get('revision'),
               'execution': self._ex(res['exec']),
               'journal_phase_sequence': res['samples'],
               'stage_watcher_phase_sequence': watch['phase_sequence'],
               'stage_watcher_rows': watch['rows'],
               'journal_row': row,
               'previous_binary_sha256_before_upgrade': prev_binary_sha,
               'artifact_v1': self.state['artifacts']['A1'], 'artifact_v2': self.state['artifacts']['A2'],
               'previous_version_backup': {
                   'backup_path': row.get('backup_path'),
                   'backup_binary_sha256': (list(backup_sha.values())[0].get('sha256') if backup_sha else None),
                   'equals_previous_binary': bool(backup_sha)
                   and list(backup_sha.values())[0].get('sha256') == prev_binary_sha},
               'actual_state': self._actual(st, sha2, 'v2'),
               'node_state_hub': self.deploy_node_state()}
        out['stage_order'] = [r.get('phase') for r in watch['rows'] if r.get('phase')]
        out['unit_inactive_at_replace'] = [r.get('unit_active') for r in watch['rows']
                                           if r.get('phase') == 'REPLACED']
        out['stop_before_replace'] = (out['unit_inactive_at_replace'] == ['inactive'])
        out['binary_never_partial'] = all(r.get('binary_sha256') in (prev_binary_sha, sha2, None)
                                          for r in watch['rows'])
        out['no_tmp_leftover'] = all(not (r.get('tmp_entries') or []) for r in watch['rows'])
        out['upgrade_ok'] = ((res['exec'] or {}).get('status') == 'SUCCESS'
                             and out['actual_state']['binary_sha_matches_artifact']
                             and out['actual_state']['unit_active'] == 'active'
                             and out['actual_state']['unit_file_content_flags'] is not None
                             and out['actual_state']['unit_file_content_flags'].get('has_v2')
                             and out['previous_version_backup']['equals_previous_binary']
                             and row.get('phase') == 'DONE'
                             and out['binary_never_partial'] and out['stop_before_replace'])
        self.rec('phase09-upgrade-v2.json', out)
        if not out['upgrade_ok']:
            raise RuntimeError('phase9_upgrade_failed')
        return out

    # ---------------- phase 10: failed upgrade restores old version (E2E-12) ----------------
    def phase10_failed_upgrade(self):
        sha2 = self.state['artifacts']['A2']['sha256']
        sha3 = self.state['artifacts']['A3']['sha256']
        before = self.art_state('a', shas=[sha2, sha3])
        defn = self.app_baseline('main', '3.0', 'A3', 'v3-bad', 'APP_MODE=v3\n',
                                 health=self.health('systemd', attempts=2, interval=1, timeout=4))
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '3.0'})
        self.budget['host_seam_actions'] += 1
        self.child['a'].start_watcher('failupg', UNIT, BINARY,
                                      after_id=(before.get('deployments') or [{}])[0].get('id') or '',
                                      mode='observe', timeout=120)
        res = self.watch_deploy('upgrade', shas=[sha3, sha2])
        watch = self.child['a'].read_watcher('failupg', wait_exit=True, timeout=140)
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        out = {'at_utc': NOW(), 'operation': 'upgrade', 'target_version': '3.0',
               'execution': self._ex(res['exec']),
               'block_reason_raw': (res['exec'] or {}).get('block_reason'),
               'journal_phase_sequence': res['samples'],
               'stage_watcher_phase_sequence': watch['phase_sequence'],
               'journal_row': row,
               'actual_state_after_rollback': self._actual(st, sha2, 'v2'),
               'v3_artifact_sha256': sha3, 'v2_artifact_sha256': sha2,
               'node_state_hub': self.deploy_node_state()}
        a = st
        out['failure_reported'] = ((res['exec'] or {}).get('status') == 'FAILED'
                                   and 'health check failed' in ((res['exec'] or {}).get('block_reason') or ''))
        out['rollback_checks'] = {
            'journal_saw_rolling_back': 'ROLLING_BACK' in (watch['phase_sequence'] or []),
            'journal_ends_done': row.get('phase') == 'DONE',
            'restored_binary_to_v2': a['binary'].get('sha256') == sha2,
            'v3_binary_not_installed': a['binary'].get('sha256') != sha3,
            'restored_config': a['config'].get('sha256') == SHA(b'APP_MODE=v2\n'),
            'restored_unit': bool(a.get('unit_file_content')) and a['unit_file_content'].get('has_v2'),
            'unit_active_after': a['unit_state'].get('ActiveState') == 'active',
            'registry_intact': a.get('unit_registry')}
        out['explicit_rollback_result'] = {
            'execution_status': (res['exec'] or {}).get('status'),
            'node_state_health': (self.deploy_node_state() or [{}])[0].get('health'),
            'node_state_version': (self.deploy_node_state() or [{}])[0].get('version'),
            'node_state_error': (self.deploy_node_state() or [{}])[0].get('error')}
        out['no_false_success'] = not ((res['exec'] or {}).get('status') == 'SUCCESS')
        out['e2e12_ok'] = (out['failure_reported'] and out['no_false_success']
                           and all(v for k, v in out['rollback_checks'].items() if k != 'registry_intact')
                           and bool(out['rollback_checks']['registry_intact']))
        self.rec('phase10-failed-upgrade-rollback.json', out)
        if not out['e2e12_ok']:
            raise RuntimeError('phase10_failed_upgrade_failed')
        return out

    # ---------------- phase 11: health check types (SYS-48) ----------------
    def _health_scenario(self, name, version, profile, health, env=None, artifact='A1',
                         config=None, expect_success=True, extra=None):
        before = self.art_state('a', shas=[self.state['artifacts'][artifact]['sha256']])
        prev_sha = before['binary'].get('sha256')
        defn = self.app_baseline('main', version, artifact, profile,
                                 config if config is not None else ('APP_MODE=%s\n' % profile),
                                 env=env, health=health)
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': version})
        res = self.watch_deploy('upgrade')
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        sha = self.state['artifacts'][artifact]['sha256']
        sc = {'at_utc': NOW(), 'name': name, 'health_check': health, 'environment': env,
              'target_version': version, 'previous_binary_sha256': prev_sha,
              'execution': self._ex(res['exec']),
              'block_reason_raw': (res['exec'] or {}).get('block_reason'),
              'journal_phase_sequence': res['samples'],
              'journal_row_phase': row.get('phase'),
              'binary_sha256_after': st['binary'].get('sha256'),
              'unit_active_after': st['unit_state'].get('ActiveState'),
              'node_state_hub': self.deploy_node_state()}
        if expect_success:
            sc['ok'] = ((res['exec'] or {}).get('status') == 'SUCCESS'
                        and st['binary'].get('sha256') == sha
                        and st['unit_state'].get('ActiveState') == 'active')
        else:
            reason = ((res['exec'] or {}).get('block_reason') or '')
            sc['restored_previous_binary'] = st['binary'].get('sha256') == prev_sha
            sc['unit_active_after'] = st['unit_state'].get('ActiveState')
            sc['rollback_failure_reported'] = 'rollback failed' in reason
            sc['rollback_journal_finalised'] = row.get('phase') == 'DONE'
            sc['poison_row_left'] = row.get('phase') != 'DONE'
            sc['poison_rows_after'] = [r['id'] for r in (st.get('deployments') or [])
                                       if r.get('phase') != 'DONE']
            sc['known_defect_id'] = KNOWN_DEFECT if sc['poison_row_left'] else None
            sc['ok'] = ((res['exec'] or {}).get('status') == 'FAILED'
                        and 'health check failed' in reason
                        and sc['restored_previous_binary'])
        if extra:
            sc.update(extra(st))
        return sc

    def phase11_health_types(self):
        fp_tcp = self._free_port()
        fp_http = self._free_port()
        fp_closed_tcp = self._free_port()
        fp_closed_http = self._free_port()
        flag = self.child['a'].opt_root + '/ready.flag'
        env_ok = {'APP_PROFILE': 'health', 'APP_NONCE': NONCE}
        out = {'at_utc': NOW(), 'ports': {'tcp': fp_tcp, 'http': fp_http,
                                          'closed_tcp': fp_closed_tcp, 'closed_http': fp_closed_http},
               'scenarios': {}}
        v = '2.1'
        out['scenarios']['systemd_positive'] = self._health_scenario(
            'systemd_positive', v, 'health',
            self.health('systemd', attempts=3, interval=2, timeout=5), env=env_ok, artifact='A1',
            extra=lambda st: {'unit_active': st['unit_state'].get('ActiveState'),
                              'main_pid': st['unit_state'].get('MainPID')})
        out['scenarios']['tcp_positive'] = self._health_scenario(
            'tcp_positive', '2.2', 'health',
            self.health('tcp', '127.0.0.1:%d' % fp_tcp, attempts=4, interval=2, timeout=4),
            env=dict(env_ok, APP_LISTEN_PORT=str(fp_tcp)), artifact='A2',
            extra=lambda st: {'listener_open_probe': self._tcp_open(fp_tcp),
                              'main_pid': st['unit_state'].get('MainPID')})
        out['scenarios']['tcp_negative'] = self._health_scenario(
            'tcp_negative', '2.3', 'health',
            self.health('tcp', '127.0.0.1:%d' % fp_closed_tcp, attempts=2, interval=1, timeout=3),
            env=env_ok, artifact='A1', expect_success=False,
            extra=lambda st: {'nothing_listening': not self._tcp_open(fp_closed_tcp)})
        out['scenarios']['http_positive'] = self._health_scenario(
            'http_positive', '2.4', 'health',
            self.health('http', 'http://127.0.0.1:%d/' % fp_http, attempts=4, interval=2, timeout=4),
            env=dict(env_ok, APP_HTTP_PORT=str(fp_http)), artifact='A1',
            extra=lambda st: {'http_probe_from_host': self._http_code(fp_http)})
        out['scenarios']['http_negative'] = self._health_scenario(
            'http_negative', '2.5', 'health',
            self.health('http', 'http://127.0.0.1:%d/' % fp_closed_http, attempts=2, interval=1, timeout=3),
            env=env_ok, artifact='A2', expect_success=False,
            extra=lambda st: {'nothing_listening': not self._tcp_open(fp_closed_http)})
        out['scenarios']['command_positive'] = self._health_scenario(
            'command_positive', '2.6', 'health',
            self.health('command', 'test -f %s' % flag, attempts=3, interval=2, timeout=4),
            env=dict(env_ok, APP_READY_FLAG=flag), artifact='A2',
            extra=lambda st: {'flag_exists': self._path_exists(flag)})
        out['scenarios']['command_negative'] = self._health_scenario(
            'command_negative', '2.7', 'health',
            self.health('command', 'false', attempts=2, interval=1, timeout=3),
            env=env_ok, artifact='A1', expect_success=False)
        out['scenarios']['systemd_negative'] = self._health_scenario(
            'systemd_negative', '2.8', 'v3-bad',
            self.health('systemd', attempts=2, interval=1, timeout=4),
            env=env_ok, artifact='A3', expect_success=False,
            extra=lambda st: {'service_exit_note': 'binary exits 1 immediately -> unit inactive-failed',
                              'unit_active': st['unit_state'].get('ActiveState')})
        negatives = [k for k in ('tcp_negative', 'http_negative', 'command_negative',
                                 'systemd_negative') if k in out['scenarios']]
        out['sys48_ok'] = all(sc.get('ok') for sc in out['scenarios'].values())
        out['health_rollback_finalised_all'] = all(
            out['scenarios'][k].get('rollback_journal_finalised') for k in negatives)
        out['health_rollback_failed_scenarios'] = [k for k in negatives
                                                  if not out['scenarios'][k].get('rollback_journal_finalised')]
        out['poison_rows_total'] = sum(len(out['scenarios'][k].get('poison_rows_after') or [])
                                      for k in negatives)
        out['known_defect_ids'] = sorted({out['scenarios'][k]['known_defect_id'] for k in negatives
                                         if out['scenarios'][k].get('known_defect_id')})
        out['defect_statement'] = (
            'product defect E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE: after a failed deploy the '
            'rollback re-runs the health check with the FAILED version health definition, so a non-systemd '
            'or permanently-unhealthy check makes rollback report "rollback failed" and leaves the '
            'deployment journal in ROLLING_BACK instead of closing it to DONE')
        self.rec("phase11-health-types.json", out)
        if not out['sys48_ok']:
            raise RuntimeError('phase11_health_types_failed')
        return out

    def _free_port(self):
        return self.child['a'].remote_py(
            "import json,socket\n"
            "s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close()\n"
            "print(json.dumps({'port':p}))\n")['port']

    def _tcp_open(self, port):
        r = self.child['a'].remote_py(
            "import json,socket\n"
            "s=socket.socket(); s.settimeout(1.5)\n"
            "try:\n"
            "    s.connect(('127.0.0.1',%d)); ok=True\n"
            "except Exception: ok=False\n"
            "print(json.dumps({'open':ok}))\n" % port)
        return bool(r['open'])

    def _http_code(self, port):
        rc, out, err = self.child['a'].sh(
            "curl -s -o /dev/null -w '%%{http_code}' --max-time 3 http://127.0.0.1:%d/ || echo ERR" % port,
            timeout=20)
        return (out.strip() or 'ERR')

    def _path_exists(self, path):
        r = self.child['a'].remote_py(
            "import json,os\nprint(json.dumps({'exists':os.path.exists(%r)}))\n" % path)
        return bool(r['exists'])

    # ---------------- phase 12: crash at each upgrade stage (SYS-51 / E2E-13 / AR-30 / INV-11) ----------------
    def phase12_crash_stages(self):
        out = {'at_utc': NOW(), 'cycles': {}}
        # stage -> (target_version, artifact, profile, health); the target artifact always differs
        # from the currently installed baseline so REPLACED/VERIFYING windows are observable.
        cycles = [
            ('STOPPED', '4.1', 'A4', 'crash-stop', self.health('systemd', attempts=3, interval=2, timeout=4)),
            ('REPLACED', '4.2', 'A1', 'crash-replace', self.health('systemd', attempts=3, interval=2, timeout=4)),
            ('STARTED', '4.3', 'A4', 'crash-start', self.health('systemd', attempts=3, interval=2, timeout=4)),
            ('VERIFYING', '4.4', 'A1', 'crash-verify',
             self.health('systemd', attempts=10, interval=3, timeout=5)),
        ]
        for stage, version, artifact, profile, health in cycles:
            key = 'crash_' + stage.lower()
            sha = self.state['artifacts'][artifact]['sha256']
            before = self.art_state('a')
            prev_sha = before['binary'].get('sha256')
            prev_row = (before.get('deployments') or [{}])[0].get('id') or ''
            defn = self.app_baseline('main', version, artifact, profile, 'APP_MODE=%s\n' % profile,
                                     health=health)
            self.put_app('main', defn)
            self.wait_agent_def('main', {'version': version})
            self.budget['host_seam_actions'] += 1
            self.child['a'].start_watcher(key, UNIT, BINARY, after_id=prev_row, mode='kill',
                                          target=stage, timeout=180)
            execs = self.deploy('upgrade')
            exec_id = execs[0]['id']
            watch = self.child['a'].read_watcher(key, wait_exit=True, timeout=200)
            kill_ev = (watch['killed'] or [{}])[0]
            alive = self.child['a'].process_alive()
            st_kill = self.art_state('a')
            cyc = {'stage_target': stage, 'target_version': version, 'artifact': artifact,
                   'previous_binary_sha256': prev_sha,
                   'watcher_phase_sequence_before_kill': watch['phase_sequence'],
                   'kill_event': kill_ev,
                   'phase_at_kill': kill_ev.get('phase_at_kill'),
                   'child_alive_after_kill': alive,
                   'journal_row_at_kill': (st_kill.get('deployments') or [{}])[0],
                   'binary_sha_at_kill': st_kill['binary'].get('sha256'),
                   'new_version_on_disk_at_kill': st_kill['binary'].get('sha256') == sha,
                   'hub_execution_at_kill': self._ex(self.exec_get(exec_id))}
            # restart the owned child -> RecoverDeployments must resolve the interrupted deployment
            killed_row = kill_ev.get('row') or cyc['journal_row_at_kill'].get('id')
            self.budget['host_seam_actions'] += 1
            self.child['a'].start_watcher(key + '_rec', UNIT, BINARY, row_id=killed_row or '',
                                          mode='observe', timeout=200)
            started = self.child['a'].start()
            self.budget['child_process_starts'] += 1
            node = self.wait_child_online('a', timeout=120)
            rec_watch = self.child['a'].read_watcher(key + '_rec', wait_exit=True, timeout=220)
            time.sleep(3)
            st = self.art_state('a')
            row = (st.get('deployments') or [{}])[0]
            node_state = self.deploy_node_state()
            cyc.update({
                'restart': started,
                'node_after_restart': {k: (node or {}).get(k) for k in ('status', 'sync_status')},
                'recovery_phase_sequence': rec_watch['phase_sequence'],
                'recovery_rows': rec_watch['rows'],
                'journal_row_after_recovery': row,
                'binary_sha_after_recovery': st['binary'].get('sha256'),
                'unit_active_after_recovery': st['unit_state'].get('ActiveState'),
                'node_state_hub': node_state,
                'hub_execution_after_recovery': self._ex(self.exec_get(exec_id)),
                'agent_log_recovery_lines': [l.strip()[:220] for l in
                                             self.child['a'].log_tail()['tail'].splitlines()
                                             if 'recover' in l.lower() or 'rollback' in l.lower()][-8:]})
            cyc['recovered_to_defined_state'] = (row.get('phase') == 'DONE'
                                                 and st['unit_state'].get('ActiveState') == 'active')
            cyc['previous_version_restored'] = (st['binary'].get('sha256') == prev_sha
                                                and st['unit_state'].get('ActiveState') == 'active')
            cyc['no_poison_row'] = all(r.get('phase') == 'DONE'
                                       for r in (st.get('deployments') or []))
            cyc['phase_at_kill_matches_target'] = kill_ev.get('phase_at_kill') == stage
            # after the crash recovery the product must accept a normal deploy again and end in a
            # consistent Desired == Actual state (AR-26 / AR-30 "recovery then idempotency").
            good = self.app_baseline('main', '2.0', 'A2', 'v2', 'APP_MODE=v2\n')
            self.put_app('main', good)
            self.wait_agent_def('main', {'version': '2.0'})
            restore = self.watch_deploy('upgrade', shas=[self.state['artifacts']['A2']['sha256']])
            st_r = restore['final_state']
            cyc['post_recovery_restore'] = {
                'execution': self._ex(restore['exec']),
                'binary_sha256': st_r['binary'].get('sha256'),
                'unit_active': st_r['unit_state'].get('ActiveState'),
                'node_state_hub': self.deploy_node_state(),
                'ok': ((restore['exec'] or {}).get('status') == 'SUCCESS'
                       and st_r['binary'].get('sha256') == self.state['artifacts']['A2']['sha256']
                       and st_r['unit_state'].get('ActiveState') == 'active')}
            out['cycles'][stage] = cyc
            self.rec('phase12-crash-%s.json' % stage.lower(), cyc)
        out['sys51_ok'] = all(c['recovered_to_defined_state'] and c['previous_version_restored']
                             and (c.get('post_recovery_restore') or {}).get('ok')
                             for c in out['cycles'].values())
        out['all_stages_killed'] = all(c['phase_at_kill_matches_target'] for c in out['cycles'].values())
        out['recovery_then_idempotency_ok'] = out['sys51_ok']
        self.rec('phase12-crash-recovery.json', out)
        if not out['sys51_ok']:
            raise RuntimeError('phase12_crash_recovery_failed')
        return out

    # ---------------- phase 13: host adapter permission denied (AR-32 + known defect) ----------------
    def phase13_permission_denied(self):
        target_sha = self.state['artifacts']['A1']['sha256']   # version 5.0 payload
        bindir = os.path.dirname(BINARY)
        before = self.art_state('a')
        prev_sha = before['binary'].get('sha256')              # currently installed baseline (A2/v2.0)
        out = {'at_utc': NOW(), 'seam_disclaimer':
               'the owned child Agent runs as root, so DAC mode bits cannot deny the write; the real '
               'permission seam is chattr +i on the owned binary directory (EPERM), same as sys-b11 SYS-52',
               'binary_dir': bindir, 'previous_binary_sha256': prev_sha,
               'target_binary_sha256': target_sha,
               'baseline_version': (self.app_hub(self.state['apps']['main']) or {}).get('version')}
        self.budget['host_seam_actions'] += 1
        seam = self.child['a'].seam(bindir, 'chattr_plus_i')
        self.state['seams'].append({'path': bindir, 'action': 'chattr_plus_i', 'release': 'chattr_minus_i'})
        out['seam'] = seam
        defn = self.app_baseline('main', '5.0', 'A1', 'denied', 'APP_MODE=denied\n')
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '5.0'})
        res = self.watch_deploy('upgrade', shas=[target_sha, prev_sha])
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        reason = ((res['exec'] or {}).get('block_reason') or '')
        out['denied_execution'] = self._ex(res['exec'])
        out['denied_block_reason'] = reason
        out['denied_journal_row'] = row
        out['denied_journal_phase'] = row.get('phase')
        out['denied_journal_phase_sequence'] = res['samples']
        out['binary_after_denied'] = st['binary'].get('sha256')
        out['denied_explicit'] = ((res['exec'] or {}).get('status') == 'FAILED'
                                  and ('permission denied' in reason.lower()
                                       or 'not permitted' in reason.lower()
                                       or 'install binary failed' in reason))
        out['rollback_failure_reported'] = 'rollback failed' in reason
        out['not_silently_successful'] = (res['exec'] or {}).get('status') != 'SUCCESS'
        out['host_unchanged_by_denied_deploy'] = st['binary'].get('sha256') == prev_sha
        out['rollback_failed_poison_row'] = row.get('phase') == 'ROLLING_BACK'
        out['known_defect_id'] = KNOWN_DEFECT if out['rollback_failed_poison_row'] else None
        # release the seam, then let the product succeed with the new version
        rel = self.child['a'].seam(bindir, 'chattr_minus_i')
        self.budget['host_seam_actions'] += 1
        out['seam_release'] = rel
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '5.0'})
        res2 = self.watch_deploy('upgrade', shas=[target_sha])
        st2 = res2['final_state']
        out['success_after_release'] = {'execution': self._ex(res2['exec']),
                                       'binary_sha256': st2['binary'].get('sha256'),
                                       'unit_active': st2['unit_state'].get('ActiveState')}
        out['new_version_installed'] = (st2['binary'].get('sha256') == target_sha
                                        and (res2['exec'] or {}).get('status') == 'SUCCESS')
        # restart the owned child: a poisoned ROLLING_BACK row is re-run on startup
        self.budget['host_seam_actions'] += 1
        self.child['a'].start_watcher('deniedrec', UNIT, BINARY, mode='observe', timeout=200)
        restarted = self.child['a'].restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online('a', timeout=120)
        rec_watch = self.child['a'].read_watcher('deniedrec', wait_exit=True, timeout=220)
        time.sleep(4)
        st3 = self.art_state('a')
        hub_app = self.app_hub(self.state['apps']['main']) or {}
        out['post_restart'] = {
            'restart': restarted,
            'node_after_restart': {k: (node or {}).get(k) for k in ('status', 'sync_status')},
            'recovery_phase_sequence': rec_watch['phase_sequence'],
            'recovery_rows': rec_watch['rows'],
            'binary_sha256_after_restart': st3['binary'].get('sha256'),
            'unit_active_after_restart': st3['unit_state'].get('ActiveState'),
            'deployments_after_restart': st3.get('deployments'),
            'node_state_hub': self.deploy_node_state(),
            'hub_app_version': hub_app.get('version'),
            'hub_app_artifact_id': hub_app.get('artifact_id'),
            'desired_artifact_sha256': target_sha}
        out['stale_backup_written_to_current_path'] = (
            st3['binary'].get('sha256') != target_sha and st3['binary'].get('sha256') == prev_sha)
        out['desired_state_vs_actual_consistent'] = (st3['binary'].get('sha256') == target_sha)
        out['defect_reproduced'] = bool(out['rollback_failed_poison_row']
                                        and out['stale_backup_written_to_current_path'])
        out['poison_rows_remaining'] = [r['id'] for r in (st3.get('deployments') or [])
                                       if r.get('phase') != 'DONE']
        out['ar32_ok'] = (out['denied_explicit'] and out['not_silently_successful']
                          and out['host_unchanged_by_denied_deploy']
                          and out['new_version_installed']
                          and out['desired_state_vs_actual_consistent']
                          and not out['defect_reproduced']
                          and not out['poison_rows_remaining'])
        # restore Desired == Actual through the product path so later phases start consistent
        restore = self.watch_deploy('upgrade', shas=[target_sha])
        out['consistency_restore'] = {'execution': self._ex(restore['exec']),
                                      'binary_sha256': restore['final_state']['binary'].get('sha256'),
                                      'unit_active': restore['final_state']['unit_state'].get('ActiveState')}
        self.rec('phase13-permission-denied.json', out)
        return out

    # ---------------- phase 14: NativeHostAdapter path guards (SYS-53) ----------------
    def phase14_path_guards(self):
        sha1 = self.state['artifacts']['A1']['sha256']
        before = self.art_state('a')
        out = {'at_utc': NOW(), 'scenarios': {},
               'host_adapter_notes': {
                   'host_root': '/', 'map_path': 'identity',
                   'chown_reachable_via_api': False,
                   'note': ('NativeHostAdapter.Chown exists in the interface but no deployment-path caller '
                            'invokes it, so user/group mapping is not reachable through the Hub API; '
                            'the observed ownership is the Agent process uid/gid (root)')}}
        # hub-layer rejections (HTTP 400, definition stays valid)
        bad = {
            'binary_path_traversal': self.app_baseline('main', '1.0', 'A1', 'v1', binary='/opt/../etc/app'),
            'binary_path_relative': self.app_baseline('main', '1.0', 'A1', 'v1', binary='opt/app/bin/app'),
            'config_path_traversal': self.app_baseline('main', '1.0', 'A1', 'v1', config_path='/etc/../etc/app.conf'),
            'unit_name_invalid': self.app_baseline('main', '1.0', 'A1', 'v1', unit='bad/unit/service'),
        }
        for name, defn in bad.items():
            self.budget['api_mutations'] += 1
            code, body = self.hub.put('/api/applications/' + self.hub.quote(self.state['apps']['main']), defn)
            out['scenarios'][name] = {'layer': 'hub', 'http': code, 'body': body}
            out['scenarios'][name]['rejected'] = code == 400
        # agent-layer rejection: path is absolute and free of '..' but not filepath.Clean()
        defn = self.app_baseline('main', '6.0', 'A1', 'clean', 'APP_MODE=clean\n',
                                 binary=OPT_ROOT + '/app/bin//app')
        units_before = sorted(self.child['a'].inventory(UNIT)['etc_systemd_owned_leftovers'])
        self.put_app('main', defn)
        self.wait_agent_def('main', {'version': '6.0'})
        res = self.watch_deploy('upgrade')
        st = res['final_state']
        row = (st.get('deployments') or [{}])[0]
        out['scenarios']['binary_path_not_clean'] = {
            'layer': 'agent NativeHostAdapter.ValidatePath', 'definition_binary_path': defn['binary_path'],
            'execution': self._ex(res['exec']),
            'block_reason_raw': (res['exec'] or {}).get('block_reason'),
            'journal_row_phase': row.get('phase'),
            'journal_phase_sequence': res['samples'],
            'binary_sha_unchanged': st['binary'].get('sha256') == before['binary'].get('sha256'),
            'unit_active': st['unit_state'].get('ActiveState'),
            'rejected': ((res['exec'] or {}).get('status') == 'FAILED'
                         and 'invalid deployment path' in ((res['exec'] or {}).get('block_reason') or '')
                         and row.get('phase') == 'DONE'
                         and st['binary'].get('sha256') == before['binary'].get('sha256'))}
        # no-new-unit probe for the rejected path
        inv = self.child['a'].inventory(UNIT)
        out['scenarios']['binary_path_not_clean']['unit_files_before'] = units_before
        out['scenarios']['binary_path_not_clean']['unit_files'] = inv['etc_systemd_owned_leftovers']
        out['scenarios']['binary_path_not_clean']['no_extra_unit_file'] = \
            sorted(inv['etc_systemd_owned_leftovers']) == units_before
        # restore the good definition
        self.put_app('main', self.app_baseline('main', '1.0', 'A1', 'v1', 'APP_MODE=v1\n'))
        self.wait_agent_def('main', {'version': '1.0'})
        res2 = self.watch_deploy('upgrade', shas=[sha1])
        out['restore'] = {'execution': self._ex(res2['exec']),
                          'binary_sha256': res2['final_state']['binary'].get('sha256')}
        out['sys53_ok'] = (all(s['rejected'] for k, s in out['scenarios'].items()
                               if s.get('layer') == 'hub')
                           and out['scenarios']['binary_path_not_clean']['rejected']
                           and out['scenarios']['binary_path_not_clean']['no_extra_unit_file']
                           and (res2['exec'] or {}).get('status') == 'SUCCESS')
        self.rec('phase14-native-adapter-path-guards.json', out)
        if not out['sys53_ok']:
            raise RuntimeError('phase14_path_guards_failed')
        return out

    # ---------------- phase 15: file transfer (SYS-62/63/64/65 + INV-18) ----------------
    def _make_blob(self, child_key, path, size, mode=0o640):
        return self.child[child_key].remote_py(
            "import hashlib,json,os,secrets\n"
            "p=%r; size=%d; mode=%d\n"
            "os.makedirs(os.path.dirname(p),mode=0o755,exist_ok=True)\n"
            "h=hashlib.sha256()\n"
            "with open(p,'wb') as f:\n"
            "    left=size\n"
            "    while left>0:\n"
            "        chunk=secrets.token_bytes(min(65536,left)); f.write(chunk); h.update(chunk); left-=len(chunk)\n"
            "os.chmod(p,mode)\n"
            "print(json.dumps({'path':p,'size':os.path.getsize(p),'sha256':h.hexdigest(),'mode':oct(os.stat(p).st_mode & 0o7777)}))\n"
            % (path, size, mode))

    def _transfer_get(self, tid):
        code, body = self.hub.get('/api/transfers/' + self.hub.quote(tid))
        return body if code == 200 else None

    def _wait_transfer(self, tid, timeout=180, predicate=None):
        deadline = time.time() + timeout
        t = None
        while time.time() < deadline:
            t = self._transfer_get(tid)
            if t and predicate and predicate(t):
                return t
            if t and not predicate and t.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
                return t
            time.sleep(1)
        return t

    def _dest_dir(self, child_key, name):
        return self.child[child_key].root + '/dst-' + name

    def phase16_file_transfer(self):
        out = {'at_utc': NOW(), 'scenarios': {}}
        big = 16 * 1024 * 1024
        src = self.child['a'].root + '/src/payload.bin'
        blob = self._make_blob('a', src, big, 0o640)
        out['source_blob'] = blob
        dest_b = self._dest_dir('b', 'multi')
        dest_c = self._dest_dir('c', 'multi')
        for k, d in (('b', dest_b), ('c', dest_c)):
            self.child[k].seam(d, 'mkdir', {'mode': 0o755})
        # ---- SYS-63: two targets, both succeed -> overall SUCCESS
        pid_a = self.refresh_agent_pid()
        pid_b = (self.child['b'].process_alive().get('pids') or [None])[0]
        self.budget['host_seam_actions'] += 1
        self.child['b'].sample_dir('dest', dest_b, 90, 0.01)
        self.child['a'].sample_net('xfer', 30, 0.05)
        self.child['b'].sample_net('xfer_target', 30, 0.05)
        self.budget['api_mutations'] += 1
        self.budget['transfers'] += 1
        code, t = self.hub.post('/api/transfers', {
            'source_node_id': self.state['nodes']['a'],
            'source_path': src,
            'targets': [{'node_id': self.state['nodes']['b'], 'destination_path': dest_b + '/out.bin', 'mode': 0o640},
                        {'node_id': self.state['nodes']['c'], 'destination_path': dest_c + '/out.bin', 'mode': 0o640}]})
        if code != 200 or not t.get('id'):
            raise RuntimeError('transfer_create_failed_http_%s_%s' % (code, t))
        tid = t['id']
        self.state['transfers']['multi'] = tid
        t_final = self._wait_transfer(tid, timeout=180)
        files = {}
        target_modes = {}
        for k, d in (('b', dest_b), ('c', dest_c)):
            files[k] = self.child[k].file_shas([d + '/out.bin'])
            info = list(files[k].values())[0]
            target_modes[k] = self.child[k].remote_py(
                "import json,os\nprint(json.dumps({'m':oct(os.stat(%r).st_mode & 0o7777) if os.path.exists(%r) else None}))\n"
                % (d + '/out.bin', d + '/out.bin'))['m']
        dirw = self.child['b'].read_dir('dest')
        temps = sorted({e['name'] for row in dirw for e in row.get('entries', [])
                        if e.get('name', '').startswith('.nodesteer-transfer-')})
        out['scenarios']['sys63_multi_target_success'] = {
            'transfer_id': tid, 'create_http': code, 'source': blob,
            'final': t_final,
            'target_files': files,
            'target_sha_matches_source': all(v[list(v)[0]].get('sha256') == blob['sha256'] for v in files.values()),
            'target_modes': target_modes,
            'target_mode_0640': all(m == '0o640' for m in target_modes.values()),
            'temp_file_observed_in_destination_dir': temps,
            'temp_cleanup_after_success': not temps,
            'dest_dir_samples': (dirw[:2] + dirw[-2:]) if len(dirw) > 4 else dirw,
            'net_proof_inv18_source_upload': self._net_summary('xfer', big, expected_pid=pid_a),
            'net_proof_inv18_target_download': self._net_summary_b('xfer_target', big, pid_b)}
        sc63 = out['scenarios']['sys63_multi_target_success']
        sc63['ok'] = (t_final.get('status') == 'SUCCESS'
                      and sc63['target_sha_matches_source']
                      and sc63['target_mode_0640']
                      and all(list(v.values())[0].get('sha256') == blob['sha256']
                              for v in files.values()))
        self.rec('phase16-file-transfer.json', out)
        # ---- SYS-62: destination .tmp + verify + atomic replace (temp observed, no partial final file)
        seq = []
        for row in dirw:
            for e in row.get('entries', []):
                seq.append({'t': row['t'], 'name': e.get('name'), 'size': e.get('size')})
        out['scenarios']['sys62_destination_attr'] = {
            'dir_samples': len(dirw), 'entry_sequence': seq[:40],
            'final_files': [e['name'] for e in (dirw[-1].get('entries') if dirw else [])],
            'temp_left_at_end': [e['name'] for e in (dirw[-1].get('entries') if dirw else [])
                                 if e.get('name', '').startswith('.nodesteer-transfer-')],
            'no_temp_left_at_end': not [e['name'] for e in (dirw[-1].get('entries') if dirw else [])
                                        if e.get('name', '').startswith('.nodesteer-transfer-')],
            'temp_naming': '.nodesteer-transfer-* (NativeHostAdapter.AtomicReplaceReader)',
            'sha_verified_before_rename': 'target sha equals source sha, mismatched sha is rejected',
            'temp_observed': bool(temps),
            'temp_names_observed': temps,
            'partial_final_never_observed': all(
                not (e.get('name') == 'out.bin' and e.get('size') not in (0, big))
                for row in dirw for e in row.get('entries', []))}
        self.rec('phase16-file-transfer.json', out)
        out['scenarios']['sys62_destination_attr']['ok'] = bool(
            out['scenarios']['sys62_destination_attr']['no_temp_left_at_end']
            and out['scenarios']['sys62_destination_attr']['partial_final_never_observed']
            and list(files['b'].values())[0].get('sha256') == blob['sha256']
            and bool(temps))
        # ---- SYS-64: one target succeeds, one fails; states must not overwrite each other
        d_b2 = self._dest_dir('b', 'partial')
        d_c2 = self._dest_dir('c', 'blocked')
        self.child['b'].seam(d_b2, 'mkdir', {'mode': 0o755})
        self.child['c'].seam(d_c2, 'mkdir', {'mode': 0o755})
        self.budget['host_seam_actions'] += 1
        seam_c = self.child['c'].seam(d_c2, 'chattr_plus_i')
        self.state['seams'].append({'path': d_c2, 'action': 'chattr_plus_i', 'release': 'chattr_minus_i'})
        self.budget['api_mutations'] += 1
        self.budget['transfers'] += 1
        code, t = self.hub.post('/api/transfers', {
            'source_node_id': self.state['nodes']['a'],
            'source_path': src,
            'targets': [{'node_id': self.state['nodes']['b'], 'destination_path': d_b2 + '/out.bin', 'mode': 0o644},
                        {'node_id': self.state['nodes']['c'], 'destination_path': d_c2 + '/out.bin', 'mode': 0o644}]})
        if code != 200 or not t.get('id'):
            raise RuntimeError('transfer_create_2_failed_http_%s_%s' % (code, t))
        tid2 = t['id']
        self.state['transfers']['partial'] = tid2
        t2 = self._wait_transfer(tid2, timeout=180)
        b_file = self.child['b'].file_shas([d_b2 + '/out.bin'])
        c_file = self.child['c'].file_shas([d_c2 + '/out.bin'])
        per_target = {x['node_id']: {'status': x['status'], 'error': x.get('error'),
                                     'destination_path': x['destination_path']}
                      for x in (t2.get('targets') or [])}
        out['scenarios']['sys64_partial_failure'] = {
            'transfer_id': tid2, 'seam': seam_c, 'final': t2,
            'per_target_final': per_target,
            'b_outcome': per_target.get(self.state['nodes']['b']),
            'c_outcome': per_target.get(self.state['nodes']['c']),
            'b_file': b_file, 'c_file': c_file,
            'independent_states': (per_target.get(self.state['nodes']['b'], {}).get('status') == 'SUCCESS'
                                   and per_target.get(self.state['nodes']['c'], {}).get('status') == 'FAILED'),
            'success_target_not_overwritten': b_file[list(b_file)[0]].get('sha256') == blob['sha256'],
            'failed_target_no_file': not c_file[list(c_file)[0]].get('exists')}
        self.rec('phase16-file-transfer.json', out)
        out['scenarios']['sys64_partial_failure']['ok'] = bool(
            t2.get('status') == 'FAILED'
            and out['scenarios']['sys64_partial_failure']['independent_states']
            and out['scenarios']['sys64_partial_failure']['success_target_not_overwritten']
            and out['scenarios']['sys64_partial_failure']['failed_target_no_file'])
        # ---- SYS-65: retry the failed target, then cancel a separate in-flight transfer
        self.budget['host_seam_actions'] += 1
        self.child['c'].seam(d_c2, 'chattr_minus_i')
        self.budget['api_mutations'] += 1
        self.budget['transfer_retries'] += 1
        code_r, t_r = self.hub.post('/api/transfers/%s/retry?target_id=%s'
                                    % (self.hub.quote(tid2), self.hub.quote(self.state['nodes']['c'])))
        t_r2 = self._wait_transfer(tid2, timeout=180, predicate=lambda x: x.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'))
        c_file2 = self.child['c'].file_shas([d_c2 + '/out.bin'])
        retried_ok = (code_r == 200 and t_r2.get('status') == 'SUCCESS'
                      and c_file2[list(c_file2)[0]].get('sha256') == blob['sha256'])
        # cancel while the source upload of a fresh, larger transfer is in flight
        big2 = 128 * 1024 * 1024
        src2 = self.child['a'].root + '/src/payload-large.bin'
        blob2 = self._make_blob('a', src2, big2, 0o640)
        d_c3 = self._dest_dir('c', 'cancel')
        self.child['c'].seam(d_c3, 'mkdir', {'mode': 0o755})
        self.budget['api_mutations'] += 1
        self.budget['transfers'] += 1
        code3, t3 = self.hub.post('/api/transfers', {
            'source_node_id': self.state['nodes']['a'],
            'source_path': src2,
            'targets': [{'node_id': self.state['nodes']['c'], 'destination_path': d_c3 + '/out.bin', 'mode': 0o644}]})
        if code3 != 200 or not t3.get('id'):
            raise RuntimeError('transfer_create_3_failed_http_%s_%s' % (code3, t3))
        tid3 = t3['id']
        self.state['transfers']['cancel'] = tid3
        time.sleep(0.4)
        inflight = self._transfer_get(tid3)
        self.budget['api_mutations'] += 1
        self.budget['transfer_cancels'] += 1
        code4, t4 = self.hub.post('/api/transfers/%s/cancel' % self.hub.quote(tid3))
        time.sleep(3)
        t4b = self._transfer_get(tid3)
        cancel_file = self.child['c'].file_shas([d_c3 + '/out.bin'])
        out['scenarios']['sys65_retry_and_cancel'] = {
            'retry_http': code_r, 'retry_transfer': t_r2, 'retried_target_file': c_file2,
            'retry_ok': retried_ok,
            'cancel_large_source': blob2,
            'state_before_cancel': {'status': (inflight or {}).get('status'),
                                    'targets': [(x['node_id'], x['status'])
                                                for x in ((inflight or {}).get('targets') or [])]},
            'cancel_http': code4, 'cancel_response_status': (t4 or {}).get('status'),
            'state_after_cancel': {'status': (t4b or {}).get('status'),
                                   'error': (t4b or {}).get('error'),
                                   'targets': [(x['node_id'], x['status'])
                                               for x in ((t4b or {}).get('targets') or [])]},
            'canceled_destination_file': cancel_file,
            'no_partial_destination_after_cancel': not cancel_file[list(cancel_file)[0]].get('exists'),
            'in_flight_before_cancel': (inflight or {}).get('status') in ('PENDING', 'UPLOADING', 'DELIVERING'),
            'cancel_target_statuses': [{'node_id': x['node_id'], 'status': x['status'],
                                        'error': x.get('error')}
                                       for x in ((t4b or {}).get('targets') or [])],
            'cancel_ok': (code4 == 200 and (t4b or {}).get('status') == 'CANCELED'
                          and all(x['status'] in ('CANCELED', 'SUCCESS')
                                  for x in ((t4b or {}).get('targets') or [])))}
        sc65 = out['scenarios']['sys65_retry_and_cancel']
        sc65['cancel_after_completion'] = not sc65['in_flight_before_cancel']
        sc65['ok'] = bool(retried_ok and sc65['cancel_ok']
                          and sc65['no_partial_destination_after_cancel'])
        out['file_transfer_ok'] = all(v.get('ok') for k, v in out['scenarios'].items())
        self.rec('phase16-file-transfer.json', out)
        if not out['file_transfer_ok']:
            raise RuntimeError('phase16_file_transfer_failed')
        return out

    # ---------------- phase 15: poison-row restart consequence (known P0 defect) ----------------
    def phase15_poison_restart(self):
        """Restart the owned child while ROLLING_BACK rows exist and record what RecoverDeployments does."""
        before = self.art_state('a')
        poison = [r for r in (before.get('deployments') or []) if r.get('phase') != 'DONE']
        desired = self.app_hub(self.state['apps']['main']) or {}
        local = [a for a in (before.get('local_apps') or []) if a['id'] == self.state['apps']['main']]
        desired_sha = ((local[0]['definition'] or {}).get('artifact_sha256') if local else None)
        out = {'at_utc': NOW(),
               'known_defect_id': KNOWN_DEFECT,
               'poison_rows_before_restart': poison,
               'binary_sha_before_restart': before['binary'].get('sha256'),
               'unit_active_before_restart': before['unit_state'].get('ActiveState'),
               'desired_version': desired.get('version'),
               'desired_artifact_sha256': desired_sha,
               'poison_row_backup_shas': self.child['a'].file_shas(
                   [r['backup_path'] for r in poison if r.get('backup_path')]) if poison else {},
               'hypothesis': ('GetActiveDeployments() selects phase NOT IN (DONE); on startup '
                              'RecoverDeployments re-runs rollback for every poison row using that row '
                              'stale backup, overwriting the currently installed version')}
        self.budget['host_seam_actions'] += 1
        self.child['a'].start_watcher('poisonrec', UNIT, BINARY, mode='observe', timeout=200)
        restarted = self.child['a'].restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online('a', timeout=120)
        rec = self.child['a'].read_watcher('poisonrec', wait_exit=True, timeout=220)
        time.sleep(4)
        st = self.art_state('a')
        out.update({
            'restart': restarted,
            'node_after_restart': {k: (node or {}).get(k) for k in ('status', 'sync_status')},
            'recovery_phase_sequence': rec['phase_sequence'],
            'recovery_rows': rec['rows'],
            'binary_sha_after_restart': st['binary'].get('sha256'),
            'unit_active_after_restart': st['unit_state'].get('ActiveState'),
            'deployments_after_restart': st.get('deployments'),
            'node_state_hub': self.deploy_node_state(),
            'agent_log_recovery_lines': [l.strip()[:200] for l in
                                         self.child['a'].log_tail()['tail'].splitlines()
                                         if 'recover' in l.lower() or 'rollback' in l.lower()][-10:]})
        out['poison_rows_after_restart'] = [r['id'] for r in (st.get('deployments') or [])
                                           if r.get('phase') != 'DONE']
        before_sha = before['binary'].get('sha256')
        after_sha = st['binary'].get('sha256')
        backup_shas = {v.get('sha256') for v in (out['poison_row_backup_shas'] or {}).values()}
        out['binary_mtime_before_restart'] = before['binary'].get('mtime')
        out['binary_mtime_after_restart'] = st['binary'].get('mtime')
        out['binary_rewritten_by_recovery'] = (before['binary'].get('mtime') != st['binary'].get('mtime'))
        out['binary_changed_by_recovery'] = after_sha != before_sha
        out['after_sha_matches_a_poison_backup'] = after_sha in backup_shas
        out['stale_backup_overwrote_current_binary'] = (after_sha != before_sha
                                                       and after_sha in backup_shas)
        out['desired_vs_actual_consistent_after_restart'] = after_sha == desired_sha
        out['desired_vs_actual_note'] = (
            'before the restart the desired definition already pointed at the failed version while the '
            'installed binary was the previous version (a second consequence of the failed rollback)')
        out['defect_reproduced'] = bool(poison) and out['stale_backup_overwrote_current_binary']
        # restore a consistent Desired == Actual state through the product path
        good = self.app_baseline('main', '2.0', 'A2', 'v2', 'APP_MODE=v2\n')
        self.put_app('main', good)
        self.wait_agent_def('main', {'version': '2.0'})
        sha_a2 = self.state['artifacts']['A2']['sha256']
        restore = self.watch_deploy('upgrade', shas=[sha_a2])
        out['consistency_restore'] = {'execution': self._ex(restore['exec']),
                                     'binary_sha256': restore['final_state']['binary'].get('sha256'),
                                     'unit_active': restore['final_state']['unit_state'].get('ActiveState'),
                                     'consistent': restore['final_state']['binary'].get('sha256') == sha_a2
                                     and (restore['exec'] or {}).get('status') == 'SUCCESS'}
        self.rec('phase15-poison-restart.json', out)
        return out

    # ---------------- cleanup ----------------
    def phase17_cleanup(self):
        res = {'at_utc': NOW(), 'steps': {}, 'errors': []}

        def step(name, fn):
            try:
                res['steps'][name] = fn()
            except Exception as exc:
                res['steps'][name] = {'error': str(exc)[:400]}
                res['errors'].append('%s: %s' % (name, exc))

        def release_seams():
            out = {}
            for key in ('a', 'b', 'c'):
                if key not in self.child:
                    continue
                ch = self.child[key]
                out[key] = {'chattr_minus_i_data': ch.seam(ch.data + '/applications', 'chattr_minus_i'),
                            'chattr_minus_i_artifacts': ch.seam(ch.data + '/artifacts', 'chattr_minus_i'),
                            'chattr_minus_i_opt_bin': ch.seam(ch.opt_root + '/app/bin', 'chattr_minus_i'),
                            'umount_dst_b': ch.seam(self._dest_dir(key, 'multi'), 'umount'),
                            'umount_dst_c': ch.seam(self._dest_dir(key, 'blocked'), 'umount')}
            return out
        step('release_host_seams', release_seams)

        def foreign_unit_removal():
            up = '/etc/systemd/system/' + 'nodesteer-' + PREFIX + 'foreign.service'
            self.budget['host_seam_actions'] += 1
            return self.child['a'].remote_py(
                "import json,os,subprocess\n"
                "u=%r; p=%r\n"
                "out={}\n"
                "def run(c):\n"
                "    r=subprocess.run(c,capture_output=True,text=True)\n"
                "    return {'rc':r.returncode,'out':r.stdout.strip()[:150],'err':r.stderr.strip()[:150]}\n"
                "out['stop']=run(['systemctl','stop',u])\n"
                "out['disable']=run(['systemctl','disable',u])\n"
                "if os.path.lexists(p): os.remove(p)\n"
                "out['daemon_reload']=run(['systemctl','daemon-reload'])\n"
                "out['reset_failed']=run(['systemctl','reset-failed',u])\n"
                "out['unit_file_remaining']=os.path.lexists(p)\n"
                "print(json.dumps(out))\n" % ('nodesteer-' + PREFIX + 'foreign.service', up))
        step('foreign_unit_removal', foreign_unit_removal)

        def product_uninstall():
            out = {}
            st0 = self.art_state('a')
            self.state['registry_before_cleanup'] = {
                'unit_registry': st0.get('unit_registry'),
                'unit_file_lexists': st0['unit_file'].get('lexists'),
                'binary_lexists': st0['binary'].get('lexists'),
                'config_lexists': st0['config'].get('lexists'),
                'unit_state': st0['unit_state']}
            out['host_before_product_cleanup'] = self.state['registry_before_cleanup']
            for key in ('main', 'unreg'):
                if key not in self.state['apps']:
                    continue
                self.budget['api_mutations'] += 1
                self.budget['app_deletes'] += 1
                code, body = self.hub.delete('/api/applications/' + self.hub.quote(self.state['apps'][key]))
                out[key] = {'delete_http': code, 'delete_body': body,
                            'agent_registry_before_cleanup': None}
            out['main']['agent_registry_before_cleanup'] = self.state.get('registry_before_cleanup')
            # wait until the product cleanup removed the managed host resources
            deadline = time.time() + 60
            st = None
            while time.time() < deadline:
                st = self.art_state('a')
                if (not st['unit_file'].get('lexists') and not st['binary'].get('lexists')
                        and not st['config'].get('lexists') and not (st.get('unit_registry') or [])):
                    break
                time.sleep(1)
            out['host_after_product_cleanup'] = {
                'unit_file_lexists': st['unit_file'].get('lexists'),
                'enable_symlink_lexists': st['enable_symlink'].get('lexists'),
                'binary_lexists': st['binary'].get('lexists'),
                'config_lexists': st['config'].get('lexists'),
                'unit_registry': st.get('unit_registry'),
                'unit_state': st['unit_state'],
                'binary_dir_entries': st.get('binary_dir_entries')}
            out['product_cleanup_complete'] = (not st['unit_file'].get('lexists')
                                               and not st['enable_symlink'].get('lexists')
                                               and not st['binary'].get('lexists')
                                               and not st['config'].get('lexists')
                                               and not (st.get('unit_registry') or []))
            out['get_after_delete_http'] = {
                'main': self.hub.get('/api/applications/' + self.hub.quote(self.state['apps']['main']))[0],
                'unreg': self.hub.get('/api/applications/' + self.hub.quote(self.state['apps']['unreg']))[0]}
            return out
        step('product_uninstall', product_uninstall)

        def host_residue_sweep():
            out = {}
            for key in ('a',):
                if key in self.child:
                    out[key] = self.child[key].unit_cleanup(UNIT, BINARY, CONFIG, self.child[key].opt_root)
            return out
        step('host_residue_sweep', host_residue_sweep)

        def child_removal():
            out = {}
            for key in ('a', 'b', 'c'):
                if key in self.child:
                    ch = self.child[key]
                    extra = [self._dest_dir(key, n) + '/out.bin'
                             for n in ('multi', 'partial', 'blocked', 'cancel')]
                    out[key] = {'unit_cleanup': ch.unit_cleanup('', '', '', '', extra_files=extra),
                                'remove': ch.remove()}
            return out
        step('owned_children_removal', child_removal)

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

        def delete_nodes():
            out = {}
            for attempt in range(4):
                pending = [(k, self.state['nodes'][k]) for k in ('a', 'b', 'c')
                           if k in self.state.get('nodes', {})]
                pending = [(k, nid) for k, nid in pending
                           if self.hub.get('/api/nodes/' + self.hub.quote(nid))[0] == 200]
                if not pending:
                    break
                for key, nid in pending:
                    self.budget['api_mutations'] += 1
                    self.budget['node_deletes'] += 1
                    path = '/api/nodes/' + self.hub.quote(nid)
                    code, body = self.hub.delete(path)
                    gcode, _ = self.hub.get(path)
                    out[key] = {'id': nid, 'attempt': attempt, 'delete_http': code,
                                'delete_body': body, 'get_http': gcode}
                    self.state['deletes'][nid] = {'kind': 'node', 'delete_http': code, 'get_http': gcode}
                time.sleep(2)
            return out
        step('node_deletes', delete_nodes)

        def transfer_leftovers():
            return self.hub.json('/api/transfers')[:20] if True else None
        step('transfer_list_after', transfer_leftovers)
        res['get_http_by_id'] = {k: v['get_http'] for k, v in self.state['deletes'].items()}
        res['all_owned_deletes_get_404'] = all(v['get_http'] == 404
                                              for v in self.state['deletes'].values())
        res['budget'] = dict(self.budget)
        self.rec('phase17-cleanup.json', res)
        return res

    # ---------------- oracle ----------------
    def phase18_oracle(self):
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
        leftovers = {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        base = hostlib.OwnedChild(NONCE).host_baseline()
        guard = self.results.get('guard') or {}
        baseline_counts = guard.get('baseline_counts', {})
        transfers = self.hub.json('/api/transfers')
        owned_transfers = [t for t in transfers if t['id'] in self.state['transfers'].values()]
        oracle = {'at_utc': NOW(), 'owned_prefix': PREFIX,
                  'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
                  'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
                  'counts_after': {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)},
                  'counts_baseline': baseline_counts,
                  'counts_returned_to_baseline': (len(arts) == baseline_counts.get('artifacts')
                                                  and len(apps) == baseline_counts.get('applications')
                                                  and len(nodes) == baseline_counts.get('nodes')),
                  'owned_transfers_terminal': [{'id': t['id'], 'status': t['status']} for t in owned_transfers],
                  'native4_host_after': base,
                  'native4_clean': (not base['owned_units'] and not base['etc_systemd_owned']
                                    and not base['wants_owned'] and not base['owned_mounts']
                                    and not base['root_exists'] and not base['opt_root_exists']
                                    and not base['child_config_exists']
                                    and not base['owned_child_processes']
                                    and not base['global_owned_prefix_scan']),
                  'agent_binary_sha_unchanged': base['agent_binary_sha256'] == hostlib.AGENT_SHA,
                  'protected_units_active': (base['primary_agent_unit'].get('ActiveState') == 'active'
                                             and base['repair_agent_unit'].get('ActiveState') == 'active'),
                  'etc_systemd_count_baseline': (guard.get('native4_baseline') or {}).get('etc_systemd_total'),
                  'etc_systemd_count_after': base['etc_systemd_total'],
                  'etc_systemd_count_unchanged': ((guard.get('native4_baseline') or {}).get('etc_systemd_total')
                                                  == base['etc_systemd_total']),
                  'cleanup_results': self.results.get('cleanup', {}),
                  'node_delete_get_http': {k: v.get('get_http') for k, v in self.state['deletes'].items()
                                           if v['kind'] == 'node'}}
        oracle['clean'] = (ok and oracle['hub_leftover_clear'] and oracle['counts_returned_to_baseline']
                           and oracle['native4_clean'] and oracle['agent_binary_sha_unchanged']
                           and oracle['protected_units_active'] and oracle['etc_systemd_count_unchanged']
                           and all(v == 404 for v in oracle['node_delete_get_http'].values())
                           and all(v.get('get_http') == 404 for v in self.state['deletes'].values()
                                   if v['kind'] == 'artifact'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle

    # ---------------- driver ----------------
    def run(self):
        phases = [
            ('guard', self.phase0_guard, []),
            ('children', self.phase1_children, ['guard']),
            ('artifacts', self.phase2_artifacts, ['children']),
            ('app_create', self.phase3_app_create, ['artifacts']),
            ('install_v1', self.phase4_install_v1, ['app_create']),
            ('idempotent', self.phase5_idempotent, ['install_v1']),
            ('persistence', self.phase6_persistence, ['idempotent']),
            ('prefetch_failure', self.phase7_prefetch_failure, ['persistence']),
            ('unit_registry', self.phase8_unit_registry, ['prefetch_failure']),
            ('upgrade_v2', self.phase9_upgrade_v2, ['unit_registry']),
            ('failed_upgrade', self.phase10_failed_upgrade, ['upgrade_v2']),
            ('crash_stages', self.phase12_crash_stages, ['failed_upgrade']),
            ('permission_denied', self.phase13_permission_denied, ['crash_stages']),
            ('path_guards', self.phase14_path_guards, ['permission_denied']),
            ('health_types', self.phase11_health_types, ['path_guards']),
            ('poison_restart', self.phase15_poison_restart, ['health_types']),
            ('file_transfer', self.phase16_file_transfer, ['poison_restart']),
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
            self.results['cleanup'] = self.phase17_cleanup()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            self.results['cleanup'] = {'harness_error': str(exc)}
            failed.append('cleanup')
        self.progress('oracle')
        try:
            self.results['oracle'] = self.phase18_oracle()
        except BaseException as exc:
            import traceback
            traceback.print_exc()
            self.results['oracle'] = {'harness_error': str(exc)}
            failed.append('oracle')
        return failed


def main():
    started = NOW()
    run = Run()
    hubapi.write_json('progress.json', {'batch_id': 'p-app', 'nonce': NONCE, 'owned_prefix': PREFIX,
                                        'endpoint': run.hub.base, 'started_at_utc': started})
    failed = run.run()
    summary = {'batch_id': 'p-app', 'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': run.hub.base,
               'started_at_utc': started, 'finished_at_utc': NOW(), 'failed_phases': failed,
               'budget': run.budget,
               'state': {'nodes': run.state.get('nodes'), 'agents': run.state.get('agents'),
                         'apps': run.state.get('apps'), 'artifacts': run.state.get('artifacts'),
                         'transfers': run.state.get('transfers'), 'deletes': run.state['deletes']},
               'results': run.results}
    hubapi.write_json('run-evidence.json', summary)
    print(json.dumps({'failed_phases': failed,
                      'oracle_clean': (run.results.get('oracle') or {}).get('clean'),
                      'install_ok': (run.results.get('install_v1') or {}).get('install_ok'),
                      'upgrade_ok': (run.results.get('upgrade_v2') or {}).get('upgrade_ok'),
                      'sys47_ok': (run.results.get('unit_registry') or {}).get('sys47_ok'),
                      'sys48_ok': (run.results.get('health_types') or {}).get('sys48_ok'),
                      'sys51_ok': (run.results.get('crash_stages') or {}).get('sys51_ok'),
                      'sys53_ok': (run.results.get('path_guards') or {}).get('sys53_ok'),
                      'e2e12_ok': (run.results.get('failed_upgrade') or {}).get('e2e12_ok'),
                      'ar32_defect_reproduced': (run.results.get('permission_denied') or {}).get('defect_reproduced'),
                      'file_transfer_ok': (run.results.get('file_transfer') or {}).get('file_transfer_ok'),
                      'health_rollback_finalised_all': (run.results.get('health_types') or {}).get('health_rollback_finalised_all'),
                      'poison_restart_defect': (run.results.get('poison_restart') or {}).get('defect_reproduced')},
                     ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
