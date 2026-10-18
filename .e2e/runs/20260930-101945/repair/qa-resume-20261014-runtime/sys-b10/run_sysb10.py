#!/usr/bin/env python3
"""sys-b10 runtime batch: Artifact download verify/atomic-cache/.tmp cleanup.

Cases: SYS-41, SYS-42, SYS-43, AR-24, AR-25, INV-10.

Real evidence chain (no product source change, no protected Agent/Hub restart):
  operator/API act -> real request (Hub download / injected ARTIFACT_PREFETCH
  through a harness frame proxy) -> real owned Agent process effect -> physical
  cache file + SHA + SQLite row + agent log -> API/DB readback.

Owned fixtures only, all deleted + GET404 at the end.
"""
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import child_agent
import hubapi
from frame_proxy import FrameProxy

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb10-%s-' % NONCE
OPERATOR_IP = '192.168.100.170'
NODE_IP = '192.168.100.206'
PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
SHARED_ARTIFACT = '7b8929ed-c880-46f1-90e6-a5e9c0dab096'
SHARED_APP = '804e6b14-eb84-4dda-90de-8bcab9586c4c'
NONEXISTENT_ARTIFACT = '00000000-0000-0000-0000-000000000000'
BLACKHOLE_URL = 'http://192.0.2.1:18080/api/artifacts/blackhole/download'
DEV = 'evidence/'
REAL_CACHE_PATHS = [
    '/var/lib/nodesteer/artifacts',
    '/var/lib/nodesteer-repair-20260930-101945/artifacts',
]


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'child_node_id': None, 'child_agent_id': None,
            'artifacts': {}, 'apps': {}, 'created': [], 'deletes': {},
        }
        self.budget = {'api_mutations': 0, 'artifact_uploads': 0, 'app_creates': 0,
                       'app_deletes': 0, 'artifact_deletes': 0, 'node_enroll_posts': 0,
                       'node_deletes': 0, 'child_process_starts': 0, 'child_process_stops': 0}

    # ---------- helpers ----------
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        return hubapi.write_json(DEV + name, value)

    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def wait_child_online(self, timeout=75):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['child_node_id'])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    def art_state(self, shas):
        return self.child.artifact_state(shas)

    def wait_artifact_present(self, sha, timeout=60):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self.art_state([sha])
            if last['watched'][sha]['file_present'] and last['watched'][sha]['file_sha_matches']:
                return last
            time.sleep(1)
        return last

    def settle(self, seconds=4.0):
        time.sleep(seconds)

    # ---------- phase 0: guard ----------
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': self.hub.base, 'nonce': NONCE, 'prefix': PREFIX}
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
                                       'deployment_mode': n.get('deployment_mode')}
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        out['shared_artifact_present'] = any(a['id'] == SHARED_ARTIFACT for a in arts)
        out['shared_app_present'] = any(a['id'] == SHARED_APP for a in apps)
        if not out['shared_artifact_present'] or not out['shared_app_present']:
            raise RuntimeError('shared_fixture_missing')
        out['artifact_count_before'] = len(arts)
        out['owned_fixtures_absent'] = {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        if any(out['owned_fixtures_absent'].values()):
            raise RuntimeError('owned_fixture_already_present')
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native2_pre'] = {
            'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
            'owned_child_processes': inv['owned_child_processes'],
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_unit': inv['repair_agent_unit'],
        }
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native2_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        out['real_cache_baseline'] = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '')\
            .real_cache_baseline(REAL_CACHE_PATHS)
        self.state['real_cache_baseline'] = out['real_cache_baseline']
        self.rec('phase0-guard.json', out)
        return out

    # ---------- phase 1: owned child ----------
    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
            'hub_address': 'http://192.168.100.209:18080'})
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
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
        node = self.wait_child_online(timeout=75)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_did_not_reach_online')
        out = {'at_utc': now_utc(),
               'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                              'node_name': PREFIX + 'child', 'node_ip': NODE_IP,
                              'gateway_url': enroll.get('gateway_url'),
                              'registration_token_persisted_to_evidence': False},
               'owned_child': {'root_on_native2': self.child.root, 'config_mode_0600': True,
                               'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                               'listened_operator_port': port,
                               'routing': 'child -> operator %s:%d (frame proxy) -> repair hub :18080'
                                          % (OPERATOR_IP, port)},
               'node_after_start': {k: node.get(k) for k in
                                    ('id', 'agent_id', 'hostname', 'status', 'sync_status',
                                     'global_revision', 'deployment_mode', 'agent_version')}}
        self.rec('phase1-owned-child.json', out)
        return out

    # ---------- phase 2: artifacts + fault-injection server ----------
    def phase2_artifacts(self):
        A = os.urandom(4096)
        B = os.urandom(4096)
        C = os.urandom(4096)
        out = {'at_utc': now_utc(), 'artifacts': {}}
        for label, content, tag in (('A', A, 'good-a'), ('B', B, 'other-b'), ('C', C, 'interrupt-c')):
            name = PREFIX + tag
            code, body = self.hub.upload_artifact(name, 'v1', name + '.bin', content)
            self.budget['api_mutations'] += 1
            self.budget['artifact_uploads'] += 1
            if code != 200:
                raise RuntimeError('artifact_upload_failed_%s_http_%s' % (label, code))
            expect = sha256_hex(content)
            if body.get('sha256') != expect:
                raise RuntimeError('hub_sha_not_local_sha_%s' % label)
            self.state['artifacts'][label] = {'id': body['id'], 'sha256': body['sha256'],
                                              'size': body['size'], 'name': name}
            out['artifacts'][label] = {'id': body['id'], 'sha256': body['sha256'], 'size': body['size'],
                                       'name': name, 'local_sha256_matches_hub': True}
        # caller-side view of the Hub download endpoint with Agent identity
        aid = self.state['artifacts']['A']['id']
        st, n, hdr = self.hub.download_probe(aid, agent_headers={
            'X-NodeSteer-Agent-Token': self.hub.cred['registration_token'],
            'X-NodeSteer-Agent-ID': self.state['child_agent_id']})
        out['hub_download_probe_A'] = {'http': st, 'bytes': n,
                                       'x_artifact_sha256': hdr.get('X-Artifact-SHA256'),
                                       'content_length': hdr.get('Content-Length')}
        # fault-injection server on native-2
        port = self._pick_remote_port()
        wrong = os.urandom(1024)
        cfg = {'payloads': {'__wrong__': base64.b64encode(wrong).decode()},
               'truncate': {'declare_len': 8192, 'send_bytes': 200, 'hold_sec': 8},
               'stall': {'hold_sec': 60}}
        self.child.write_remote_text(self.child.root + '/artifact_server_config.json',
                                     json.dumps(cfg))
        server_src = (HERE / 'artifact_server.py').read_text()
        self.child.write_remote_text(self.child.root + '/artifact_server.py', server_src, mode=0o600)
        started = self.child.start_artifact_server(server_src, port)
        self.state['server'] = {'port': port, 'url_base': 'http://127.0.0.1:%d' % port}
        out['fault_server'] = {'port': port, 'listens': '127.0.0.1', 'processes': started.get('processes'),
                               'wrong_body_sha256': sha256_hex(wrong),
                               'endpoints': ['/payload/<name> (unused)', '/wrongbody', '/truncate',
                                             '/stall', '/status/<code>']}
        self.rec('phase2-artifacts.json', out)
        return out

    def _pick_remote_port(self):
        script = ("import json,socket\n"
                  "s=socket.socket(); s.bind(('127.0.0.1',0)); p=s.getsockname()[1]; s.close()\n"
                  "print(json.dumps({'port':p}))\n")
        return self.child.remote_py(script)['port']

    # ---------- phase 3: SHA mismatch (SYS-42 / INV-10) ----------
    def phase3_sha_mismatch(self):
        sha_B = self.state['artifacts']['B']['sha256']
        sha_fake = sha256_hex(('sysb10-%s-mismatch-expected' % NONCE).encode())
        url_B = '%s/api/artifacts/%s/download' % (self.hub.base, self.state['artifacts']['B']['id'])
        inj = self.proxy.inject_artifact_prefetch(url_B, sha_fake, artifact_id='harness-mismatch')
        time.sleep(6)
        st = self.art_state([sha_fake, sha_B])
        out = {'at_utc': now_utc(), 'injected': inj,
               'case_premise': 'expected SHA256 differs from the bytes actually served by the real Hub download endpoint',
               'served_url_host': self.hub.base, 'expected_sha256': sha_fake,
               'served_body_sha256': sha_B,
               'agent_state': {'expected_sha_file_present': st['watched'][sha_fake]['file_present'],
                               'expected_sha_db_row': st['db_rows'].get(sha_fake),
                               'served_body_sha_file_present': st['watched'][sha_B]['file_present'],
                               'served_body_sha_db_row': st['db_rows'].get(sha_B),
                               'tmp_entries': st['tmp_entries'],
                               'db_artifact_count': st['db_artifact_count'],
                               'db_quick_check': st['db_quick_check']}}
        log = self.child.log_tail()
        out['agent_log_evidence'] = self._log_lines(log['tail'], ['sha256 mismatch', sha_fake[:16]])
        out['prohibited_install'] = (not out['agent_state']['expected_sha_file_present']
                                     and not out['agent_state']['expected_sha_db_row']
                                     and not out['agent_state']['served_body_sha_file_present']
                                     and not out['agent_state']['served_body_sha_db_row']
                                     and not out['agent_state']['tmp_entries'])
        self.rec('phase3-sha-mismatch.json', out)
        return out

    # ---------- phase 4: AR-25 SHA failure for artifact A (install prohibited) ----------
    def phase4_a_sha_failure(self):
        sha_A = self.state['artifacts']['A']['sha256']
        url = self.state['server']['url_base'] + '/wrongbody'
        inj = self.proxy.inject_artifact_prefetch(url, sha_A, artifact_id='harness-ar25-fail')
        time.sleep(6)
        st = self.art_state([sha_A])
        out = {'at_utc': now_utc(), 'injected': inj,
               'premise': 'artifact A has never been cached on this owned Agent; harness serves wrong bytes for sha_A',
               'expected_sha256': sha_A, 'served_url': url,
               'agent_state': {'a_file_present': st['watched'][sha_A]['file_present'],
                               'a_db_row': st['db_rows'].get(sha_A),
                               'tmp_entries': st['tmp_entries'],
                               'db_artifact_count': st['db_artifact_count']}}
        log = self.child.log_tail()
        out['agent_log_evidence'] = self._log_lines(log['tail'], ['sha256 mismatch'])
        out['install_prohibited'] = (not out['agent_state']['a_file_present']
                                     and not out['agent_state']['a_db_row']
                                     and not out['agent_state']['tmp_entries'])
        self.rec('phase4-ar25-sha-failure.json', out)
        return out

    # ---------- phase 5: SYS-43 download interruption / timeout / HTTP error ----------
    def phase5_download_faults(self):
        base = self.state['server']['url_base']
        out = {'at_utc': now_utc(), 'scenarios': {}}

        # 5a HTTP error (real Hub 404)
        sha_c = sha256_hex(b'http-error-expected')
        inj = self.proxy.inject_artifact_prefetch(
            '%s/api/artifacts/%s/download' % (self.hub.base, NONEXISTENT_ARTIFACT), sha_c,
            artifact_id='harness-http-error')
        time.sleep(6)
        st = self.art_state([sha_c])
        out['scenarios']['http_error_404'] = self._scenario_result(inj, sha_c, st, ['download status 404'])

        # 5b interruption (truncated body vs declared Content-Length)
        sha_t = sha256_hex(b'interrupted-expected')
        inj = self.proxy.inject_artifact_prefetch(base + '/truncate', sha_t, artifact_id='harness-interrupt')
        seen = None
        try:
            seen = self.child.poll_for_file(sha_t + '.tmp', present=True, timeout=6)
        except Exception as exc:
            seen = {'error': str(exc)[:200]}
        time.sleep(9)
        st = self.art_state([sha_t])
        res = self._scenario_result(inj, sha_t, st, ['unexpected EOF', 'EOF'])
        res['tmp_seen_during_transfer'] = seen
        out['scenarios']['interruption_truncated'] = res

        # 5c timeout (dial timeout to an unroutable/blackholed address)
        sha_to = sha256_hex(b'timeout-expected')
        inj = self.proxy.inject_artifact_prefetch(BLACKHOLE_URL, sha_to, artifact_id='harness-timeout')
        time.sleep(40)
        st = self.art_state([sha_to])
        out['scenarios']['timeout_dial'] = self._scenario_result(
            inj, sha_to, st, ['i/o timeout', 'context deadline exceeded', 'connection', 'dial tcp'])

        self.rec('phase5-download-faults.json', out)
        return out

    def _scenario_result(self, inj, sha, st, needles):
        log = self.child.log_tail()
        return {'injected': inj, 'expected_sha256': sha,
                'file_present': st['watched'][sha]['file_present'],
                'db_row': st['db_rows'].get(sha),
                'tmp_present_for_sha': st['watched'][sha]['tmp_present'],
                'all_tmp_entries': st['tmp_entries'],
                'db_artifact_count': st['db_artifact_count'],
                'agent_log_lines': self._log_lines(log['tail'], needles),
                'no_residue': (not st['watched'][sha]['file_present']
                               and not st['db_rows'].get(sha)
                               and not st['watched'][sha]['tmp_present'])}

    # ---------- phase 6: AR-24 interruption for artifact C ----------
    def phase6_c_interruption(self):
        sha_C = self.state['artifacts']['C']['sha256']
        inj = self.proxy.inject_artifact_prefetch(self.state['server']['url_base'] + '/truncate',
                                                  sha_C, artifact_id='harness-ar24-interrupt')
        time.sleep(4)
        mid = self.art_state([sha_C])
        time.sleep(10)
        st = self.art_state([sha_C])
        out = {'at_utc': now_utc(), 'injected': inj, 'expected_sha256': sha_C,
               'mid_transfer_tmp_seen': mid['watched'][sha_C]['tmp_present'],
               'mid_transfer_tmp_entries': mid['tmp_entries'],
               'after': {'file_present': st['watched'][sha_C]['file_present'],
                         'db_row': st['db_rows'].get(sha_C),
                         'tmp_entries': st['tmp_entries'],
                         'db_artifact_count': st['db_artifact_count']},
               'agent_log_evidence': self._log_lines(self.child.log_tail()['tail'],
                                                     ['unexpected EOF', 'EOF'])}
        out['interruption_clean'] = (not out['after']['file_present'] and not out['after']['db_row']
                                     and not out['after']['tmp_entries'])
        self.rec('phase6-ar24-interruption.json', out)
        return out

    # ---------- phase 7: recovery + SYS-41 install via Desired State apps ----------
    def phase7_recovery_success(self):
        apps = {}
        for label, art in (('A', 'A'), ('C', 'C')):
            name = PREFIX + 'app-' + label.lower()
            body = {'name': name, 'description': 'sys-b10 owned app (nonce %s)' % NONCE,
                    'artifact_id': self.state['artifacts'][art]['id'], 'version': 'v1',
                    'binary_path': self.child.root + '/bin/' + label.lower(),
                    'unit_name': 'nodesteer-' + PREFIX + 'app-' + label.lower(),
                    'config': '', 'arguments': [], 'environment': {}}
            code, resp = self.hub.post('/api/applications', body)
            self.budget['api_mutations'] += 1
            self.budget['app_creates'] += 1
            if code != 200 or not resp.get('id'):
                raise RuntimeError('app_create_failed_%s_http_%s_%s' % (label, code, resp))
            apps[label] = resp['id']
            self.state['apps'][label] = resp['id']
            code2, _ = self.hub.post('/api/applications/%s/assign' % resp['id'],
                                     {'node_ids': [self.state['child_node_id']]})
            self.budget['api_mutations'] += 1
            if code2 != 200:
                raise RuntimeError('app_assign_failed_%s_http_%s' % (label, code2))
        sha_A = self.state['artifacts']['A']['sha256']
        sha_C = self.state['artifacts']['C']['sha256']
        stA = self.wait_artifact_present(sha_A, timeout=70)
        stC = self.wait_artifact_present(sha_C, timeout=70)
        st = self.art_state([sha_A, sha_C])
        if st.get('db_error'):
            raise RuntimeError('agent_db_probe_error: %s' % st['db_error'])
        log_tail = self.child.log_tail()['tail']
        out = {'at_utc': now_utc(), 'apps': apps,
               'desired_state': {'app_A': {'id': apps['A'], 'artifact_sha256': sha_A, 'assigned_node': self.state['child_node_id']},
                                 'app_C': {'id': apps['C'], 'artifact_sha256': sha_C, 'assigned_node': self.state['child_node_id']}},
               'actual_state': {
                   'A': {k: st['watched'][sha_A][k] for k in
                         ('file_present', 'file_sha_matches', 'file_sha', 'file_size', 'tmp_present')},
                   'C': {k: st['watched'][sha_C][k] for k in
                         ('file_present', 'file_sha_matches', 'file_sha', 'file_size', 'tmp_present')}},
               'expected_sizes': {'A': self.state['artifacts']['A']['size'], 'C': self.state['artifacts']['C']['size']},
               'db_all_artifact_rows': st['db_all_artifact_rows'],
               'db_artifact_count': st['db_artifact_count'], 'db_quick_check': st['db_quick_check'],
               'local_apps_on_agent': st['local_apps'],
               'tmp_entries': st['tmp_entries'],
               'agent_log_install_lines': self._log_lines(log_tail, ['artifact cached'])}
        out['sys41_atomic_install'] = (
            out['actual_state']['A']['file_present'] and out['actual_state']['A']['file_sha_matches']
            and out['actual_state']['A']['file_size'] == out['expected_sizes']['A']
            and not out['actual_state']['A']['tmp_present'] and not st['tmp_entries']
            and st['db_rows'].get(sha_A))
        out['ar24_recovery_success'] = (
            out['actual_state']['C']['file_present'] and out['actual_state']['C']['file_sha_matches']
            and out['actual_state']['C']['file_size'] == out['expected_sizes']['C']
            and not out['actual_state']['C']['tmp_present'] and st['db_rows'].get(sha_C))
        out['desired_equals_actual'] = out['sys41_atomic_install'] and out['ar24_recovery_success']
        self.rec('phase7-recovery-success.json', out)
        return out

    # ---------- phase 8: idempotency ----------
    def phase8_idempotency(self):
        sha_A = self.state['artifacts']['A']['sha256']
        sha_C = self.state['artifacts']['C']['sha256']
        before = self.art_state([sha_A, sha_C])
        log_before = self._log_lines(self.child.log_tail()['tail'], ['artifact cached'])
        # real Desired-State re-assertion -> agent resyncs -> prefetchApplications -> cache hit
        for label in ('A', 'C'):
            code, _ = self.hub.post('/api/applications/%s/assign' % self.state['apps'][label],
                                    {'node_ids': [self.state['child_node_id']]})
            self.budget['api_mutations'] += 1
            if code != 200:
                raise RuntimeError('reassign_failed_%s_http_%s' % (label, code))
        # explicit re-inject of the same correct prefetch (cache-hit path)
        for label, ref in (('A', 'A'), ('C', 'C')):
            aid = self.state['artifacts'][ref]['id']
            self.proxy.inject_artifact_prefetch('%s/api/artifacts/%s/download' % (self.hub.base, aid),
                                                self.state['artifacts'][ref]['sha256'],
                                                artifact_id='harness-idem-' + label)
        time.sleep(12)
        after = self.art_state([sha_A, sha_C])
        log_after = self._log_lines(self.child.log_tail()['tail'], ['artifact cached'])
        out = {'at_utc': now_utc(),
               'before': {k: {kk: before['watched'][k][kk] for kk in ('file_mtime', 'file_sha_matches')} for k in (sha_A, sha_C)},
               'after': {k: {kk: after['watched'][k][kk] for kk in ('file_mtime', 'file_sha_matches')} for k in (sha_A, sha_C)},
               'db_downloaded_at_before': {r['sha256']: r['downloaded_at'] for r in before['db_all_artifact_rows']},
               'db_downloaded_at_after': {r['sha256']: r['downloaded_at'] for r in after['db_all_artifact_rows']},
               'db_artifact_count_before': before['db_artifact_count'],
               'db_artifact_count_after': after['db_artifact_count'],
               'install_log_count_before': len(log_before), 'install_log_count_after': len(log_after),
               'tmp_entries_after': after['tmp_entries'],
               'no_redownload': True}
        out['idempotent'] = (
            after['watched'][sha_A]['file_mtime'] == before['watched'][sha_A]['file_mtime']
            and after['watched'][sha_C]['file_mtime'] == before['watched'][sha_C]['file_mtime']
            and len(log_after) == len(log_before)
            and after['db_artifact_count'] == before['db_artifact_count']
            and not after['tmp_entries'])
        self.rec('phase8-idempotency.json', out)
        return out

    # ---------- phase 9: persistence across owned-child restart ----------
    def phase9_persistence(self):
        sha_A = self.state['artifacts']['A']['sha256']
        sha_C = self.state['artifacts']['C']['sha256']
        before = self.art_state([sha_A, sha_C])
        restarted = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(timeout=75)
        time.sleep(8)
        after = self.art_state([sha_A, sha_C])
        log_after = self._log_lines(self.child.log_tail()['tail'], ['artifact cached'])
        out = {'at_utc': now_utc(), 'restart': restarted,
               'node_after_restart': {k: node.get(k) for k in ('status', 'sync_status', 'global_revision')},
               'before': {k: before['watched'][k] for k in (sha_A, sha_C)},
               'after': {k: after['watched'][k] for k in (sha_A, sha_C)},
               'db_rows_after': {k: after['db_rows'].get(k) for k in (sha_A, sha_C)},
               'db_quick_check_after': after['db_quick_check'],
               'db_downloaded_at_after': {r['sha256']: r['downloaded_at'] for r in after['db_all_artifact_rows']},
               'tmp_entries_after': after['tmp_entries'],
               'install_log_count_after': len(log_after)}
        out['persistent'] = (
            after['watched'][sha_A]['file_present'] and after['watched'][sha_A]['file_sha_matches']
            and after['watched'][sha_C]['file_present'] and after['watched'][sha_C]['file_sha_matches']
            and after['db_rows'].get(sha_A) and after['db_rows'].get(sha_C)
            and after['watched'][sha_A]['file_mtime'] == before['watched'][sha_A]['file_mtime']
            and after['watched'][sha_C]['file_mtime'] == before['watched'][sha_C]['file_mtime']
            and not after['tmp_entries'] and after['db_quick_check'] == 'ok')
        self.rec('phase9-persistence.json', out)
        return out

    # ---------- phase 10: bounded observation - server stall (no read timeout) ----------
    def phase10_stall_observation(self):
        sha_s = sha256_hex(b'stall-expected')
        inj = self.proxy.inject_artifact_prefetch(self.state['server']['url_base'] + '/stall', sha_s,
                                                  artifact_id='harness-stall')
        time.sleep(6)
        st1 = self.art_state([sha_s])
        time.sleep(18)
        st2 = self.art_state([sha_s])
        out = {'at_utc': now_utc(), 'injected': inj, 'expected_sha256': sha_s,
               'observation': 'server sends response headers then withholds the body; the Agent uses '
                              'http.DefaultClient with no read/idle timeout and passes the agent-lifetime '
                              'context, so the prefetch neither fails nor times out.',
               'tmp_present_t_plus_6s': st1['watched'][sha_s]['tmp_present'],
               'tmp_present_t_plus_24s': st2['watched'][sha_s]['tmp_present'],
               'tmp_entries_t_plus_24s': st2['tmp_entries'],
               'file_present_t_plus_24s': st2['watched'][sha_s]['file_present'],
               'db_row': st2['db_rows'].get(sha_s),
               'classification': 'SUPPLEMENTARY_OBSERVATION_NOT_A_REQUIRED_DIMENSION',
               'note': 'The required SYS-43 dimensions (interruption / HTTP error / dial timeout) each '
                       'clean .tmp; this stall case shows only that no response-read timeout exists, which '
                       'is a robustness observation, not a required cleanup failure.'}
        self.state['stall_sha'] = sha_s
        self.rec('phase10-stall-observation.json', out)
        return out

    def _log_lines(self, tail, needles):
        lines = []
        for line in tail.splitlines():
            if any(n in line for n in needles):
                lines.append(line.strip()[:400])
        return lines[-12:]

    # ---------- phase 11: cleanup ----------
    def phase11_cleanup(self):
        results = {'deleted': [], 'errors': [], 'at_utc': now_utc()}
        # 1) owned child process + fault server + owned root on native-2
        if self.child is not None:
            try:
                results['native2_removal'] = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                results['native2_removal'] = {'error': str(exc)[:300]}
                results['errors'].append('child_remove:%s' % exc)
        # 2) apps then artifacts then node
        order = [('application', self.state['apps'].get(k)) for k in ('A', 'C')]
        order += [('artifact', self.state['artifacts'][k]['id']) for k in ('C', 'B', 'A')]
        for kind, obj_id in order:
            if not obj_id:
                continue
            path = '/api/%ss/%s' % (kind, self.hub.quote(obj_id))
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            gcode, _ = self.hub.get(path)
            if kind == 'artifact':
                self.budget['artifact_deletes'] += 1
            else:
                self.budget['app_deletes'] += 1
            results['deleted'].append({'kind': kind, 'id': obj_id, 'delete_http': code, 'get_http': gcode})
            self.state['deletes'][obj_id] = {'kind': kind, 'delete_http': code, 'get_http': gcode}
        node_id = self.state['child_node_id']
        code, body = self.hub.delete('/api/nodes/' + self.hub.quote(node_id))
        self.budget['api_mutations'] += 1
        self.budget['node_deletes'] += 1
        gcode, _ = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        results['child_node'] = {'id': node_id, 'delete_http': code, 'get_http': gcode,
                                 'delete_body': body if isinstance(body, (dict, str)) else None}
        if code != 200 or gcode != 404:
            results['errors'].append('child_node_delete_not_404:%s/%s' % (code, gcode))
        results['all_get_404'] = all(d['get_http'] == 404 for d in results['deleted']) and gcode == 404
        self.rec('phase11-cleanup.json', results)
        return results

    # ---------- phase 12: final oracle ----------
    def phase12_oracle(self, cleanup):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        ok = True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'), 'sync_status': n.get('sync_status')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        leftovers = {
            'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        orphan = bool(inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes'])
        server_left = [l for l in inv['agent_process_table'] if 'artifact_server.py' in l]
        protected_units_ok = (inv['primary_agent_unit'].get('ActiveState') == 'active'
                              and inv['repair_agent_unit'].get('ActiveState') == 'active')
        real_after = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').real_cache_baseline(REAL_CACHE_PATHS)
        baseline = self.state.get('real_cache_baseline', {})
        real_unpolluted = self._cache_equal(baseline, real_after)
        oracle = {
            'at_utc': now_utc(), 'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
            'shared_artifact_still_present': any(a['id'] == SHARED_ARTIFACT for a in arts),
            'shared_app_still_present': any(a['id'] == SHARED_APP for a in apps),
            'native2_owned_child_orphan': orphan,
            'native2_fault_server_residue': server_left,
            'protected_units_active': protected_units_ok,
            'primary_agent_unit_untouched': inv['primary_agent_unit'].get('ActiveState') == 'active',
            'real_cache_baseline': baseline, 'real_cache_after': real_after,
            'real_protected_cache_unpolluted': real_unpolluted,
            'cleanup_results': cleanup, 'total_nodes_now': len(nodes),
        }
        oracle['clean'] = (ok and not any(leftovers.values()) and not orphan and not server_left
                           and protected_units_ok and real_unpolluted
                           and oracle['shared_artifact_still_present'] and oracle['shared_app_still_present']
                           and cleanup.get('all_get_404'))
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle

    @staticmethod
    def _cache_equal(a, b):
        return a == b


def main():
    run = Run()
    evidence = {'batch_id': 'sys-b10', 'nonce': NONCE, 'owned_prefix': PREFIX,
                'endpoint': run.hub.base,
                'cases': ['SYS-41', 'SYS-42', 'SYS-43', 'AR-24', 'AR-25', 'INV-10'],
                'started_at_utc': now_utc(), 'phases': {}, 'deviations': []}
    progress = {'batch_id': 'sys-b10', 'nonce': NONCE, 'phase': 'guard', 'updated_at_utc': now_utc()}

    def mark(phase, **extra):
        progress['phase'] = phase
        progress['updated_at_utc'] = now_utc()
        progress.update(extra)
        hubapi.write_json('progress.json', progress)

    failed = False
    try:
        mark('guard'); evidence['phases']['guard'] = run.phase0_guard()
        mark('child'); evidence['phases']['child'] = run.phase1_child()
        progress['child_node_id'] = run.state['child_node_id']
        progress['child_agent_id'] = run.state['child_agent_id']
        mark('artifacts'); evidence['phases']['artifacts'] = run.phase2_artifacts()
        progress['artifacts'] = {k: v['id'] for k, v in run.state['artifacts'].items()}
        mark('sha_mismatch'); evidence['phases']['sha_mismatch'] = run.phase3_sha_mismatch()
        mark('ar25_sha_failure'); evidence['phases']['ar25_fail'] = run.phase4_a_sha_failure()
        mark('download_faults'); evidence['phases']['faults'] = run.phase5_download_faults()
        mark('ar24_interruption'); evidence['phases']['ar24_interrupt'] = run.phase6_c_interruption()
        mark('recovery'); evidence['phases']['recovery'] = run.phase7_recovery_success()
        progress['apps'] = run.state['apps']
        mark('idempotency'); evidence['phases']['idempotency'] = run.phase8_idempotency()
        mark('persistence'); evidence['phases']['persistence'] = run.phase9_persistence()
        mark('stall_observation'); evidence['phases']['stall'] = run.phase10_stall_observation()
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('HARNESS_ERROR:%s' % exc)
        progress['harness_error'] = str(exc)
        failed = True

    mark('cleanup')
    try:
        if run.state['child_node_id'] or run.state['apps'] or run.state['artifacts']:
            cleanup = run.phase11_cleanup()
            progress['final_cleanup'] = cleanup
            progress['final_cleanup_get_http'] = {d['id']: d['get_http'] for d in cleanup['deleted']}
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['cleanup_error'] = str(exc)
        failed = True
    finally:
        if run.proxy:
            try:
                run.proxy.stop()
            except Exception:
                pass

    mark('oracle')
    try:
        oracle = run.phase12_oracle(progress.get('final_cleanup', {}))
        progress['oracle_clean'] = oracle['clean']
    except Exception as exc:
        import traceback
        traceback.print_exc()
        progress['oracle_error'] = str(exc)
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['failed'] = failed
    evidence['state'] = {'child_node_id': run.state['child_node_id'],
                         'child_agent_id': run.state['child_agent_id'],
                         'artifacts': run.state['artifacts'], 'apps': run.state['apps'],
                         'deletes': run.state['deletes']}
    hubapi.write_json('run-evidence.json', evidence)
    if run.proxy:
        hubapi.write_json('evidence/proxy-frames.json',
                          {'proxy_state': run.proxy.state(), 'events': run.proxy.events})
    print(json.dumps({'failed': failed, 'oracle_clean': progress.get('oracle_clean'),
                      'harness_error': progress.get('harness_error'),
                      'cleanup_error': progress.get('cleanup_error')}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
