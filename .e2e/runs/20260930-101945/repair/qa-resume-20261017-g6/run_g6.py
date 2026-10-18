#!/usr/bin/env python3
"""qa-resume-20261017-g6 runtime batch: G6 long-window / undeletable-history batch.

Cases: X18, X12, X15 (all P0, ledger status PARTIAL).

Real evidence chain (no product source change, no protected Agent/Hub touched, no
Hub restart, no VM restart; node native-1 (192.168.100.204) is used only through
harness-owned children):

  * X18: real FileTransfer (source = owned child a, targets = owned child b with
    a valid destination + owned child a with an ENOTDIR destination) -> aggregate
    FAILED while the succeeded target keeps its delivered file (physical sha);
    retry of the failed target only -> the succeeded target's file is untouched;
    client-side source-change pruning re-checked in the browser; Application
    definition OK + Assign failure (stale node id) + retry + real deploy attempt.
  * X12: label target retargeting across two real executions, empty target,
    overlapping static groups (dedupe), offline/disabled/maintenance node,
    capability mismatch (owned docker-mode child lacks application_deploy), plus
    node-detail / task-detail cross-page reads (immediately vs after cache
    convergence).
  * X15: long-running execution observed live in the browser without refresh
    (PENDING->RUNNING->terminal), websocket cut/recover through the harness frame
    proxy, realtime log shards, terminal stdout/stderr completeness, and
    Follow / search / stream filter / Copy after recovery.

Transfer rows and Execution rows have no DELETE endpoint: every created row is
registered in the retained-history ledger instead of being deleted.
"""
import hashlib
import json
import os
import subprocess
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
BATCH = 'g6'
PREFIX = 'qa-%s-%s-' % (BATCH, NONCE)
DEV = HERE / 'evidence'
HUB_HOST = '192.168.100.209'
ENDPOINT = 'http://%s:18080' % HUB_HOST
NODE_HOST = '192.168.100.204'
# The Hub de-duplicates enrollment by hostname and then by node_ip
# (hub.NodeManager.findEnrollableNode).  Three children on one physical host
# would otherwise collapse into ONE node record, so each owned child
# self-reports a distinct node_ip.  node_ip is metadata only: the Hub never
# dials it (all control/data plane traffic runs over the Agent's own websocket
# plus the Agent's HTTP upload/download to the Hub).
CHILD_IPS = {'a': '192.168.100.204', 'b': '192.168.100.203', 'c': '192.168.100.202'}
HUB_UNIT = 'nodesteer-hub-repair-20260930-101945.service'
# expected Hub binary sha comes from the CR-1 deployment evidence (the checklist
# line for the older CD-0 build is superseded by the CR-1 redeploy on 2026-10-16)
CR1_DEPLOY = (HERE.parent / 'qa-resume-20261016-cr1-verify' / 'deploy-evidence.json')
HUB_SHA = 'fdfa667bf51ce1a7e9a0b1aeeeef7660764b605360613d14fb518b67015e36d1'
try:
    HUB_SHA = json.loads(CR1_DEPLOY.read_text())['artifacts']['hub_bundled']['sha256']
except Exception:
    pass
HUB_BUILD_LABEL = 'dddee4f0-e2e-repair-20261016-cr1'
PREVIOUS_HUB_SHA = 'cd8b7f9d7e40841bbba982c6838b2f74ccfdfac785674030c5bf7d6f1f69120d'
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}
TARGET_TERMINAL = {'SUCCESS', 'FAILED', 'CANCELED'}
AB_SESSION = 'qag6-' + NONCE
AB_ORIGIN = ENDPOINT
DEV.mkdir(exist_ok=True)
os.umask(0o077)


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


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
            t = head + '.' + (digits + '000000')[:6] + rest[i:]
        return datetime.fromisoformat(t)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# browser driver (own session only; never `close --all`)
# --------------------------------------------------------------------------- #
def ab(*args, stdin=None, timeout=180):
    cmd = ['agent-browser', '--session', AB_SESSION] + list(args)
    cp = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    return cp.stdout, cp.stderr, cp.returncode


def ab_value(out):
    i = out.find('{')
    j = out.find('[')
    if i < 0 or (0 <= j < i):
        i = j
    if i < 0:
        return out.strip()
    try:
        return json.loads(out[i:])
    except Exception:
        return out[i:]


def ab_js(code):
    out, err, rc = ab('eval', '--stdin', stdin=code)
    if rc != 0:
        raise RuntimeError('ab_eval_failed: %s' % ((err or out)[-300:]))
    return ab_value(out)


def ab_open(path, wait=1.6):
    ab('open', AB_ORIGIN + path)
    time.sleep(wait)


def ab_shot(name):
    p = DEV / name
    ab('screenshot', str(p))
    time.sleep(0.3)
    return p.exists()


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxies = {}
        self.children = {}
        self.dir_children = {}
        self.state = {
            'batch': BATCH, 'nonce': NONCE, 'prefix': PREFIX, 'endpoint': ENDPOINT,
            'node_host': NODE_HOST, 'started_utc': now_utc(),
            'fixtures': {}, 'nodes': {}, 'created': [], 'deleted': [], 'delete_results': {},
            'executions': {}, 'transfers': {}, 'retained_history': {},
            'ui': {}, 'x12': {}, 'x18': {}, 'x15': {},
        }
        self.budget = {'api_mutations': 0, 'child_process_starts': 0, 'child_process_stops': 0,
                       'frames_total': 0, 'cut_windows': 0}

    # ---------------- evidence plumbing ----------------
    def ev(self, name, value):
        hubapi.write_json(str(DEV / name), value)
        return value

    def save_state(self):
        self.state['updated_utc'] = now_utc()
        hubapi.write_json(str(HERE / 'run-evidence.json'), {'prefix': PREFIX, 'nonce': NONCE,
                                                           'state': self.state, 'budget': self.budget})

    # ---------------- small helpers ----------------
    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def nodes_by_id(self):
        return {n['id']: n for n in self.hub.json('/api/nodes')}

    def set_node_status(self, node_id, status):
        self.budget['api_mutations'] += 1
        return self.hub.post('/api/nodes/' + self.hub.quote(node_id), {'status': status})

    def set_labels(self, node_id, labels):
        self.budget['api_mutations'] += 1
        return self.hub.post('/api/nodes/' + self.hub.quote(node_id), {'labels': labels})

    def execs_by_task(self, task_id, limit=500):
        rows = self.hub.json('/api/executions?task_id=%s&limit=%d' % (self.hub.quote(task_id), limit))
        return sorted(rows, key=lambda r: (r.get('scheduled_time') or '', r.get('start_time') or ''))

    def exec_detail(self, eid):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(eid))
        return code, body

    def exec_logs(self, eid):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(eid) + '/logs')
        return code, body if isinstance(body, list) else []

    def transfer(self, tid):
        return self.hub.json('/api/transfers/' + self.hub.quote(tid))

    def wait_for(self, probe, predicate=None, timeout=90, interval=1.0, label='cond'):
        t0 = time.time()
        samples = []
        last = None
        while time.time() - t0 < timeout:
            try:
                last = probe()
            except Exception as exc:
                last = {'_error': type(exc).__name__}
            ok = bool(predicate(last)) if predicate is not None else bool(last)
            samples.append({'at_utc': now_utc(), 'ok': ok, 'value': last})
            if ok:
                return {'complete': True, 'seconds': round(time.time() - t0, 1), 'label': label,
                        'last': last, 'samples': samples}
            time.sleep(interval)
        return {'complete': False, 'seconds': round(time.time() - t0, 1), 'label': label,
                'last': last, 'samples': samples}

    def wait_exec_terminal(self, eid, timeout=150):
        return self.wait_for(lambda: (lambda cb: {'http': cb[0], 'status': (cb[1] or {}).get('status')})(
            self.exec_detail(eid)), lambda v: v.get('status') in TERMINAL, timeout=timeout,
            interval=1.0, label='exec_terminal')

    def wait_transfer_terminal(self, tid, timeout=240):
        def probe():
            t = self.transfer(tid)
            return {'status': t.get('status'),
                    'targets': [{'node': x['node_id'], 'status': x['status'], 'error': x.get('error')}
                                for x in t.get('targets') or []]}
        return self.wait_for(probe, lambda v: v.get('status') in TARGET_TERMINAL and all(
            x['status'] in TARGET_TERMINAL for x in (v.get('targets') or [])), timeout=timeout,
            interval=2.0, label='transfer_terminal')

    # ======================= phase 0 =======================
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': ENDPOINT, 'hub_sha_expected': HUB_SHA}
        out['healthz'] = self.hub.raw('GET', '/healthz', None, timeout=8)[0]
        out['readyz'] = self.hub.raw('GET', '/readyz', None, timeout=8)[0]
        if out['healthz'] != 200 or out['readyz'] != 200:
            raise RuntimeError('repair_hub_not_healthy')
        probe = (
            "import json,subprocess,hashlib\n"
            "u=subprocess.run(['systemctl','show','--no-pager','--property=Id,ActiveState,MainPID,ExecMainStartTimestamp',%r],capture_output=True,text=True).stdout\n"
            "d=dict(x.split('=',1) for x in u.splitlines() if '=' in x)\n"
            "pid=d.get('MainPID','0')\n"
            "exe=subprocess.run(['readlink','-f','/proc/'+pid+'/exe'],capture_output=True,text=True).stdout.strip()\n"
            "sha=hashlib.sha256(open(exe,'rb').read()).hexdigest() if exe else ''\n"
            "print(json.dumps({'unit':d,'exe':exe,'sha256':sha}))\n"
        ) % HUB_UNIT
        cp = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                             '-o', 'ConnectTimeout=8', '-i', str(Path.home() / '.ssh' / 'id_ed25519'),
                             'root@' + HUB_HOST, 'python3', '-'], input=probe, capture_output=True,
                            text=True, timeout=60)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        if not lines:
            raise RuntimeError('hub_probe_failed:%s' % (cp.stderr[-300:] or 'no output'))
        out['hub_unit'] = json.loads(lines[-1])
        out['hub_binary_sha_matches'] = out['hub_unit']['sha256'] == HUB_SHA
        out['hub_binary_sha_expected_source'] = str(CR1_DEPLOY)
        out['hub_build_label_expected'] = HUB_BUILD_LABEL
        if not out['hub_binary_sha_matches']:
            raise RuntimeError('hub_binary_sha_mismatch')
        import urllib.request
        try:
            with urllib.request.urlopen('http://%s:8080/healthz' % HUB_HOST, timeout=8) as resp:
                out['primary_8080_healthz'] = resp.status
        except Exception as exc:
            out['primary_8080_healthz'] = 'error:%s' % type(exc).__name__
        nodes = self.nodes_by_id()
        out['nodes_before'] = {n['id']: {k: n.get(k) for k in ('hostname', 'status', 'sync_status',
                                                               'global_revision', 'deployment_mode')}
                               for n in nodes.values()}
        protected_prefix = 'qa-repair-20260930-101945-native-'
        out['protected_nodes'] = {}
        ok = True
        for nid, n in nodes.items():
            host = n.get('hostname') or nid
            if host.startswith(PREFIX):
                continue
            protected = host.startswith(protected_prefix)
            healthy = n.get('status') == 'online' and n.get('sync_status') == 'synced'
            out['protected_nodes'][host] = {'id': nid, 'status': n.get('status'),
                                            'sync_status': n.get('sync_status'),
                                            'protected_repair_native': protected, 'healthy': healthy}
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
            'groups': [g['id'] for g in self.hub.json('/api/groups') if (g.get('name') or '').startswith(PREFIX)],
            'applications': [a['id'] for a in self.hub.json('/api/applications') if (a.get('name') or '').startswith(PREFIX)],
            'nodes': [n['id'] for n in nodes.values() if (n.get('hostname') or '').startswith(PREFIX)],
        }
        out['owned_prefix_leftovers'] = leftovers
        if any(leftovers.values()):
            raise RuntimeError('owned_fixture_already_present')
        inv = child_agent.OwnedChild(NONCE, 0, '', '', '', '', '', label='a').inventory()
        out['native1_pre'] = {'root_exists': inv['root_exists'], 'owned_root_glob': inv['owned_root_glob'],
                              'owned_child_processes': inv['owned_child_processes'],
                              'any_g6_processes': inv['any_g6_processes'],
                              'agent_binary_sha256': inv['agent_binary_sha256'],
                              'primary_agent_unit': inv['primary_agent_unit'],
                              'repair_agent_unit': inv['repair_agent_unit']}
        if (inv['root_exists'] or inv['owned_child_processes'] or inv['owned_root_glob']
                or inv['any_g6_processes']):
            raise RuntimeError('owned_child_residue_present')
        if (inv['primary_agent_unit'].get('ActiveState') != 'active'
                or inv['repair_agent_unit'].get('ActiveState') != 'active'):
            raise RuntimeError('protected_agent_units_not_active')
        self.state['agent_binary_sha256'] = inv['agent_binary_sha256']
        db = hubdb.snapshot([])
        out['hub_db'] = {'revision_sequence': db['revision_sequence'], 'quick_check': db['quick_check'],
                         'revision_changes_total': db['revision_changes_total']}
        if db['quick_check'] != 'ok':
            raise RuntimeError('hub_db_quick_check_failed')
        self.state['hub_revision_before'] = db['revision_sequence']
        out['transfers_before_count'] = len(self.hub.json('/api/transfers'))
        out['executions_before_count'] = len(self.hub.json('/api/executions?limit=500'))
        return self.ev('phase0-guard.json', out)

    # ======================= phase 1 =======================
    def _make_child(self, label, deployment_mode, proxy_port):
        node_ip = CHILD_IPS[label]
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + label, 'node_ip': node_ip, 'hub_address': ENDPOINT})
        self.budget['api_mutations'] += 1
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_%s_http_%s %s' % (label, code, enroll))
        for other, rec in self.state['nodes'].items():
            if other != label and rec['id'] == enroll['node_id']:
                raise RuntimeError('enrollment_node_id_collision_%s_with_%s' % (label, other))
        child = child_agent.OwnedChild(NONCE, proxy_port, enroll['node_id'], enroll['agent_id'],
                                       PREFIX + label, node_ip,
                                       self.hub.cred['registration_token'],
                                       deployment_mode=deployment_mode, label=label)
        self.children[label] = child
        started = child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        res = self.wait_for(lambda: (lambda n: {'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                                'deployment_mode': n.get('deployment_mode'),
                                                'capabilities': n.get('capabilities')})(self.node(enroll['node_id']) or {}),
                            lambda v: v.get('status') == 'online' and v.get('sync_status') == 'synced',
                            timeout=120, interval=1.0, label='node_online_' + label)
        if not res['complete']:
            raise RuntimeError('owned_child_%s_did_not_reach_online_synced' % label)
        self.state['nodes'][label] = {'id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                                      'deployment_mode': deployment_mode,
                                      'hub_url': child.hub_url, 'root': child.root,
                                      'launcher': started.get('owned_processes')}
        return {'enroll_http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                'deployment_mode': deployment_mode, 'hub_url': child.hub_url,
                'connect_wait_seconds': res['seconds'], 'node_after_start': res['last'],
                'root_on_native1': child.root}

    def phase1_children(self):
        proxy = FrameProxy('ws://%s:18080' % HUB_HOST, label='a')
        port = proxy.start()
        self.proxies['a'] = proxy
        self.state['proxy_port'] = port
        out = {'at_utc': now_utc(), 'proxy': {'listen_port': port, 'owner': 'workstation 192.168.100.170',
                                              'label': 'a', 'upstream': 'ws://192.168.100.209:18080'},
               'children': {},
               'child_ips': dict(CHILD_IPS),
               'child_ip_note': ('all three children run on the physical host 192.168.100.204; each '
                                 'self-reports a distinct node_ip because Hub enrollment de-duplicates '
                                 'by hostname and then by IP (findEnrollableNode). node_ip is metadata '
                                 'only and is never dialed by the Hub.'),
               'node_id_distinctness_guard': 'enforced in _make_child'}
        out['children']['a'] = self._make_child('a', 'native', port)
        out['children']['b'] = self._make_child('b', 'native', 0)
        out['children']['c'] = self._make_child('c', 'docker', 0)
        out['capability_note'] = ('child c runs deployment_mode=docker -> the Agent advertises '
                                 'application_deploy=false (internal/agent/agent.go capabilities()), '
                                 'which yields a real ONLINE node with a capability mismatch')
        return self.ev('phase1-owned-children.json', out)

    # ======================= phase 2 =======================
    def _create_script(self, name, body, timeout=60):
        payload = {'name': name, 'description': 'g6 long-window fixture (nonce %s)' % NONCE,
                   'interpreter': 'shell', 'content': body, 'parameters': [], 'environment': {},
                   'working_dir': '', 'timeout': timeout, 'enabled': True}
        code, s = self.hub.post('/api/scripts', payload)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, s))
        self.state['created'].append(['script', s['id'], name])
        return s

    def _create_task(self, name, target, script_id=None, app_id=None, ttype='script', timeout=180):
        body = {'name': name, 'type': ttype, 'target': target, 'parameters': [], 'timeout': timeout,
                'retry': 0, 'offline_policy': 'hub_online_required', 'enabled': True}
        if script_id:
            body['script_id'] = script_id
        if app_id:
            body['application_id'] = app_id
        code, t = self.hub.post('/api/tasks', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_create_http_%s %s' % (code, t))
        self.state['created'].append(['task', t['id'], name])
        return t

    def _create_group(self, name, gtype='static', members=None, label_key='', label_value=''):
        body = {'name': name, 'type': gtype, 'members': members or [],
                'label_key': label_key, 'label_value': label_value}
        code, g = self.hub.post('/api/groups', body)
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('group_create_http_%s %s' % (code, g))
        self.state['created'].append(['group', g['id'], name])
        return g

    def phase2_fixtures(self):
        a = self.state['nodes']['a']['id']
        b = self.state['nodes']['b']['id']
        c = self.state['nodes']['c']['id']
        fx = self.state['fixtures']
        kid_a = self.children['a']
        kid_b = self.children['b']
        fx['label_key'] = PREFIX + 'role'
        fx['label_value'] = 'blue'

        long_body = (
            '#!/bin/sh\n'
            'set -u\n'
            'echo "%(p)s long-run start"\n'
            'echo "stdout-line-001 marker-%(p)slong"\n'
            'i=2\n'
            'while [ $i -le 22 ]; do\n'
            '  printf \'%%s stdout-line-%%03d marker-%%slong\\n\' "$(date -u +%%H:%%M:%%S)" "$i" "%(p)s"\n'
            '  if [ $((i %% 4)) -eq 0 ]; then echo "stderr-line-$i marker-%(p)sstderr" 1>&2; fi\n'
            '  sleep 1\n'
            '  i=$((i+1))\n'
            'done\n'
            'echo "stderr-tail marker-%(p)sstderr-tail" 1>&2\n'
            'echo "stdout-tail marker-%(p)sstdout-tail"\n'
            'echo "%(p)s long-run done"\n') % {'p': PREFIX}
        quick_body = (
            '#!/bin/sh\n'
            'set -u\n'
            'echo "%(p)s quick-1"\n'
            'echo "%(p)s quick-2"\n'
            'echo "%(p)s quick-done"\n') % {'p': PREFIX}

        fx['script_long'] = self._create_script(PREFIX + 'script-long', long_body, timeout=120)['id']
        fx['script_quick'] = self._create_script(PREFIX + 'script-quick', quick_body, timeout=60)['id']

        # label target (retargeted by a real label move), empty target, node target, group target
        fx['task_label'] = self._create_task(
            PREFIX + 'task-label-target',
            {'type': 'label', 'label_key': fx['label_key'], 'label_value': fx['label_value'],
             'node_ids': [], 'group_ids': []}, script_id=fx['script_quick'])['id']
        fx['task_label_empty'] = self._create_task(
            PREFIX + 'task-label-empty',
            {'type': 'label', 'label_key': fx['label_key'], 'label_value': 'nobody-has-this',
             'node_ids': [], 'group_ids': []}, script_id=fx['script_quick'])['id']
        fx['task_node_b'] = self._create_task(
            PREFIX + 'task-node-b',
            {'type': 'node', 'node_ids': [b], 'group_ids': [], 'label_key': '', 'label_value': ''},
            script_id=fx['script_quick'])['id']
        fx['task_long'] = self._create_task(
            PREFIX + 'task-long-run',
            {'type': 'node', 'node_ids': [a], 'group_ids': [], 'label_key': '', 'label_value': ''},
            script_id=fx['script_long'], timeout=180)['id']

        g1 = self._create_group(PREFIX + 'group-both', 'static', members=[a, b])
        g2 = self._create_group(PREFIX + 'group-a-only', 'static', members=[a])
        fx['group_both'] = g1['id']
        fx['group_a_only'] = g2['id']
        fx['task_group_overlap'] = self._create_task(
            PREFIX + 'task-group-overlap',
            {'type': 'group', 'group_ids': [g1['id'], g2['id']], 'node_ids': [],
             'label_key': '', 'label_value': ''}, script_id=fx['script_quick'])['id']

        # real physical fixtures on the owned children
        xfer_dir_a = kid_a.fixtures + '/xfer'
        src = xfer_dir_a + '/source-' + NONCE + '.bin'
        blocker = xfer_dir_a + '/blocker-file-' + NONCE
        self.dir_children['transfer_src'] = kid_a.make_file(src, 262144, seed=3)
        self.dir_children['transfer_blocker'] = kid_a.write_small_file(blocker, 'g6 blocker\n')
        fx['xfer_src'] = src
        fx['xfer_blocker'] = blocker
        fx['xfer_dst_b_ok'] = kid_b.fixtures + '/delivered-' + NONCE + '.bin'
        fx['xfer_dst_a_bad'] = blocker + '/cannot-create-' + NONCE + '.bin'
        # self-transfer probe file (owned)
        fx['self_src'] = xfer_dir_a + '/selfcopy-' + NONCE + '.bin'
        self.dir_children['self_src'] = kid_a.write_small_file(fx['self_src'],
                                                              'g6 self-transfer probe %s\n' % NONCE)

        self.state['fixtures'] = fx
        out = {'at_utc': now_utc(), 'fixtures': dict(fx), 'nodes': self.state['nodes'],
               'physical_files': {'transfer_src': self.dir_children['transfer_src'],
                                  'blocker': self.dir_children['transfer_blocker'],
                                  'self_src': self.dir_children['self_src']},
               'node_c_capabilities': (self.node(c) or {}).get('capabilities'),
               'node_a_capabilities': (self.node(a) or {}).get('capabilities')}
        return self.ev('phase2-fixtures.json', out)

    # ======================= phase 3 : X12 =======================
    def _run_now(self, task_id, node_ids=None, params=None):
        body = {}
        if node_ids:
            body['node_ids'] = {nid: True for nid in node_ids}
        if params:
            body['params'] = params
        self.budget['api_mutations'] += 1
        return self.hub.post('/api/tasks/%s/run' % self.hub.quote(task_id), body)

    def phase3_x12(self):
        out = {'at_utc': now_utc(), 'case': 'X12'}
        a = self.state['nodes']['a']['id']
        b = self.state['nodes']['b']['id']
        c = self.state['nodes']['c']['id']
        fx = self.state['fixtures']

        # --- 3.1 label target: initial assignment on node a -> real execution on a
        code, body = self.set_labels(a, {fx['label_key']: fx['label_value']})
        out['label_set_on_a'] = {'http': code, 'body': body,
                                 'node_a_labels': (self.node(a) or {}).get('labels'),
                                 'node_b_labels': (self.node(b) or {}).get('labels')}
        code, rows = self._run_now(fx['task_label'])
        out['label_run1_dispatch'] = {'http': code, 'executions': [
            {'id': r.get('id'), 'node_id': r.get('node_id'), 'status': r.get('status')} for r in (rows or [])]}
        eids1 = [r['id'] for r in (rows or [])]
        self.state['executions']['x12_label_run1'] = eids1
        out['label_run1_terminal'] = [self.wait_exec_terminal(e)['last'] for e in eids1]

        # --- 3.2 empty target: label value nobody has
        before_empty = len(self.execs_by_task(fx['task_label_empty']))
        code, body = self._run_now(fx['task_label_empty'])
        after_empty = len(self.execs_by_task(fx['task_label_empty']))
        out['empty_target'] = {'http': code, 'body': body, 'executions_before': before_empty,
                               'executions_after': after_empty,
                               'no_execution_created': after_empty == before_empty}

        # --- 3.3 overlapping groups: a in both groups, b in one
        code, rows = self._run_now(fx['task_group_overlap'])
        out['overlap_groups_dispatch'] = {'http': code, 'returned': len(rows or []),
                                          'nodes': sorted({r.get('node_id') for r in (rows or [])})}
        eids_ov = [r['id'] for r in (rows or [])]
        self.state['executions']['x12_group_overlap'] = eids_ov
        out['overlap_groups_terminal'] = [self.wait_exec_terminal(e)['last'] for e in eids_ov]

        # --- 3.4 label move a->b, then next real execution must retarget
        self.set_labels(a, {})
        self.set_labels(b, {fx['label_key']: fx['label_value']})
        out['label_moved'] = {'node_a_labels': (self.node(a) or {}).get('labels'),
                              'node_b_labels': (self.node(b) or {}).get('labels')}
        code, rows = self._run_now(fx['task_label'])
        out['label_run2_dispatch'] = {'http': code, 'executions': [
            {'id': r.get('id'), 'node_id': r.get('node_id'), 'status': r.get('status')} for r in (rows or [])]}
        eids2 = [r['id'] for r in (rows or [])]
        self.state['executions']['x12_label_run2'] = eids2
        out['label_run2_terminal'] = [self.wait_exec_terminal(e)['last'] for e in eids2]

        # --- 3.5 offline / disabled / maintenance
        status_probes = {}
        for status in ('offline', 'disabled', 'maintenance'):
            self.set_node_status(b, status)
            observed = (self.node(b) or {}).get('status')
            code, body = self._run_now(fx['task_node_b'])
            status_probes[status] = {'node_status_observed': observed, 'run_http': code,
                                     'run_body': body,
                                     'executions_total_after': len(self.execs_by_task(fx['task_node_b']))}
        out['node_status_gate'] = status_probes
        # maintenance + schedule trigger -> SKIPPED留痕 (documented semantics)
        self.set_node_status(b, 'maintenance')
        sc_body = {'task_id': fx['task_node_b'], 'type': 'cron', 'expression': '* * * * *',
                   'interval_sec': 0, 'timezone': 'UTC', 'execution_owner': 'hub',
                   'offline_policy': 'hub_online_required', 'misfire_policy': 'run_once', 'enabled': True}
        code, sc = self.hub.post('/api/schedules', sc_body)
        self.budget['api_mutations'] += 1
        if code in (200, 201):
            self.state['created'].append(['schedule', sc['id'], PREFIX + 'sc-maintenance-skip'])
        sched_wait = self.wait_for(lambda: [{'id': r['id'], 'status': r['status'],
                                             'block_reason': r.get('block_reason'),
                                             'created_at': r.get('created_at')}
                                            for r in self.execs_by_task(fx['task_node_b'])],
                                   lambda rows: any(r['status'] == 'SKIPPED' for r in rows),
                                   timeout=150, interval=5.0, label='maintenance_skipped')
        out['maintenance_schedule_skipped'] = {'schedule_id': sc.get('id') if isinstance(sc, dict) else None,
                                              'schedule_http': code, 'wait': sched_wait}
        if code in (200, 201):
            self.hub.delete('/api/schedules/' + self.hub.quote(sc['id']))
            self.budget['api_mutations'] += 1
            self.state['deleted'].append(['schedule', sc['id']])
        self.set_node_status(b, 'online')
        self.set_labels(b, {fx['label_key']: fx['label_value']})
        out['restored_node_b'] = {'status': (self.node(b) or {}).get('status'),
                                  'labels': (self.node(b) or {}).get('labels')}

        # --- 3.6 capability mismatch: app_deploy on docker-mode child c
        app = self.state.setdefault('app', {})
        acode, aresp = self.hub.post('/api/applications', {
            'name': PREFIX + 'app-capability', 'unit_name': PREFIX + 'capability.service',
            'description': 'g6 capability-mismatch fixture'})
        self.budget['api_mutations'] += 1
        out['app_definition'] = {'http': acode, 'id': (aresp or {}).get('id') if isinstance(aresp, dict) else None,
                                 'revision': (aresp or {}).get('revision') if isinstance(aresp, dict) else None}
        if acode in (200, 201):
            app['id'] = aresp['id']
            app['revision_created'] = aresp.get('revision')
            self.state['created'].append(['application', aresp['id'], PREFIX + 'app-capability'])
        tcode, tresp = self.hub.post('/api/tasks', {
            'name': PREFIX + 'task-app-on-docker-node', 'type': 'app_deploy',
            'application_id': app['id'], 'target': {'type': 'node', 'node_ids': [c], 'group_ids': [],
                                                     'label_key': '', 'label_value': ''},
            'parameters': [], 'timeout': 60, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True})
        self.budget['api_mutations'] += 1
        out['capability_task_create'] = {'http': tcode, 'id': (tresp or {}).get('id') if isinstance(tresp, dict) else None}
        if tcode in (200, 201):
            fx['task_app_on_c'] = tresp['id']
            self.state['created'].append(['task', tresp['id'], PREFIX + 'task-app-on-docker-node'])
            ccode, cbody = self._run_now(tresp['id'])
            out['capability_mismatch_run'] = {'http': ccode, 'body': cbody,
                                              'node_c_capabilities': (self.node(c) or {}).get('capabilities')}
        dcode, dbody = self.hub.post('/api/applications/%s/deploy' % self.hub.quote(app['id']),
                                     {'node_ids': [c], 'operation': 'deploy'})
        self.budget['api_mutations'] += 1
        out['capability_mismatch_app_deploy'] = {'http': dcode, 'body': dbody}
        self.save_state()
        return self.ev('phase3-x12-targets.json', out)

    # ======================= phase 4 : X18 transfer =======================
    def _xfer_probe(self, tag, src_path, targets, kid_a, wait=110):
        """Create one transfer and wait (bounded) for its terminal state."""
        code, t = self.hub.post('/api/transfers', {
            'source_node_id': targets['source_node_id'], 'source_path': src_path,
            'targets': targets['targets']})
        self.budget['api_mutations'] += 1
        rec = {'tag': tag, 'http': code, 'id': (t or {}).get('id') if isinstance(t, dict) else None,
               'status_initial': (t or {}).get('status') if isinstance(t, dict) else None,
               'targets': targets['targets']}
        if code != 200:
            rec['body'] = t
            return rec
        tid = t['id']
        self.state['transfers'][tag] = tid
        term = self.wait_transfer_terminal(tid, timeout=wait)
        cur = self.transfer(tid)
        rec.update({'waited_s': term['seconds'], 'terminal_complete': term['complete'],
                    'final_status': cur['status'],
                    'final_targets': [{'node_id': x['node_id'], 'status': x['status'],
                                       'error': x.get('error'), 'destination_path': x['destination_path']}
                                      for x in cur['targets']],
                    'sha256': cur.get('sha256'), 'created_at': cur.get('created_at')})
        return rec

    def phase4_x18_transfer(self):
        out = {'at_utc': now_utc(), 'case': 'X18 (transfer part)'}
        a = self.state['nodes']['a']['id']
        b = self.state['nodes']['b']['id']
        fx = self.state['fixtures']
        kid_a, kid_b = self.children['a'], self.children['b']

        before = self.hub.json('/api/transfers')
        out['transfers_before'] = len(before)
        out['source_file'] = kid_a.file_state(fx['xfer_src'])
        out['dst_before'] = kid_b.file_state(fx['xfer_dst_b_ok'])

        # --- warm-up: prove the two-node delivery path is live before the graded run.
        # A flaky, unrecovered remote-target delivery was observed during
        # development (see evidence/repro-xfer-frame-ledger.json); it is recorded
        # here as a bounded, retried pre-flight probe instead of silently
        # weakening the graded assertion.
        warm_src = kid_a.fixtures + '/xfer/warmup-' + NONCE + '.bin'
        out['warmup_file'] = kid_a.write_small_file(warm_src, 'g6 warmup %s\n' % NONCE)
        out['warmup'] = []
        for attempt in (1, 2, 3):
            r = self._xfer_probe('x18_warmup_%d' % attempt, warm_src,
                                 {'source_node_id': a,
                                  'targets': [{'node_id': b,
                                               'destination_path': kid_b.fixtures + '/xfer/warmup-dst-%d.bin' % attempt}]},
                                 kid_a, wait=45)
            out['warmup'].append(r)
            if r.get('final_status') == 'SUCCESS':
                break
        out['warmup_ok'] = (out['warmup'][-1].get('final_status') == 'SUCCESS')

        # --- graded: one target on the remote node (expected SUCCESS), one target on
        # the source node with an ENOTDIR destination (expected FAILED)
        attempts = []
        graded = None
        for attempt in (1, 2):
            r = self._xfer_probe('x18_partial_%d' % attempt, fx['xfer_src'],
                                 {'source_node_id': a,
                                  'targets': [{'node_id': b, 'destination_path': fx['xfer_dst_b_ok']},
                                              {'node_id': a, 'destination_path': fx['xfer_dst_a_bad']}]},
                                 kid_a, wait=110)
            attempts.append(r)
            if r.get('terminal_complete'):
                graded = r
                break
        out['attempts'] = attempts
        out['graded'] = graded
        if graded is None:
            out['graded_note'] = ('no attempt reached a terminal state within the bounded window; '
                                 'the stuck attempt is recorded above and released by cancel in cleanup')
            self.save_state()
            return self.ev('phase4-x18-transfer.json', out)
        tid = graded['id']
        self.state['transfers']['x18_partial'] = tid
        out['final'] = {'id': tid, 'status': graded['final_status'], 'error': None,
                        'sha256': graded['sha256'], 'created_at': graded['created_at'],
                        'targets': graded['final_targets']}
        out['dst_after_success'] = kid_b.file_state(fx['xfer_dst_b_ok'])
        out['bad_dst_after'] = kid_a.file_state(fx['xfer_dst_a_bad'])
        out['success_target_content_matches_source'] = (
            out['dst_after_success'].get('sha256') == out['source_file'].get('sha256'))
        out['failed_target_no_file'] = (out['bad_dst_after'].get('exists') is False)
        out['per_target_isolation'] = {
            'remote_target_status': [x for x in graded['final_targets'] if x['node_id'] == b],
            'source_target_status': [x for x in graded['final_targets'] if x['node_id'] == a],
            'aggregate_status': graded['final_status']}

        # --- retry only the failed target; the succeeded target must stay untouched
        out['retry'] = {'target_id': a}
        rcode, rbody = self.hub.post('/api/transfers/%s/retry?target_id=%s' % (self.hub.quote(tid),
                                                                              self.hub.quote(a)), {})
        self.budget['api_mutations'] += 1
        out['retry']['http'] = rcode
        out['retry']['status_after_retry'] = (rbody or {}).get('status') if isinstance(rbody, dict) else None
        term2 = self.wait_transfer_terminal(tid, timeout=110)
        out['retry']['terminal'] = {'waited_s': term2['seconds'], 'complete': term2['complete'],
                                    'last': term2['last']}
        after2 = self.transfer(tid)
        out['retry']['final'] = {'status': after2['status'],
                                 'targets': [{'node_id': x['node_id'], 'status': x['status'],
                                              'error': x.get('error'),
                                              'destination_path': x['destination_path']}
                                             for x in after2['targets']]}
        out['dst_after_retry'] = kid_b.file_state(fx['xfer_dst_b_ok'])
        out['succeeded_target_untouched_by_retry'] = (
            out['dst_after_retry'].get('sha256') == out['dst_after_success'].get('sha256')
            and out['dst_after_retry'].get('mtime') == out['dst_after_success'].get('mtime'))

        # --- retry with empty target_id: only FAILED targets restart, SUCCESS must survive
        rcode2, rbody2 = self.hub.post('/api/transfers/%s/retry' % self.hub.quote(tid), {})
        self.budget['api_mutations'] += 1
        term3 = self.wait_transfer_terminal(tid, timeout=110)
        after3 = self.transfer(tid)
        out['retry_all'] = {'http': rcode2,
                            'terminal': {'waited_s': term3['seconds'], 'complete': term3['complete'],
                                         'last': term3['last']},
                            'final': {'status': after3['status'],
                                      'targets': [{'node_id': x['node_id'], 'status': x['status'],
                                                   'error': x.get('error')} for x in after3['targets']]}}
        out['dst_after_retry_all'] = kid_b.file_state(fx['xfer_dst_b_ok'])
        out['success_delivery_stable_across_both_retries'] = (
            out['dst_after_retry_all'].get('sha256') == out['dst_after_success'].get('sha256'))

        # --- server-side self-transfer probe (the client guard is the UI-side guard)
        out['self_src_before'] = kid_a.file_state(fx['self_src'])
        scode, st = self.hub.post('/api/transfers', {
            'source_node_id': a, 'source_path': fx['self_src'],
            'targets': [{'node_id': a, 'destination_path': fx['self_src']}]})
        self.budget['api_mutations'] += 1
        out['self_transfer_api'] = {'http': scode, 'accepted': scode == 200,
                                    'id': (st or {}).get('id') if isinstance(st, dict) else None,
                                    'body': st if scode != 200 else None}
        if scode == 200:
            sid = st['id']
            self.state['transfers']['x18_self'] = sid
            self.wait_transfer_terminal(sid, timeout=90)
            sf = self.transfer(sid)
            out['self_transfer_final'] = {'status': sf['status'],
                                          'targets': [{'node_id': x['node_id'], 'status': x['status'],
                                                       'error': x.get('error')} for x in sf['targets']]}
            out['self_src_after'] = self.children['a'].file_state(fx['self_src'])
            out['self_file_unchanged'] = (out['self_src_after'].get('sha256')
                                          == out['self_src_before'].get('sha256'))
        self.save_state()
        return self.ev('phase4-x18-transfer.json', out)

    # ======================= phase 5 : X18 application assign =======================
    def phase5_x18_app(self):
        out = {'at_utc': now_utc(), 'case': 'X18 (application assign part)'}
        a = self.state['nodes']['a']['id']
        app = self.state['app']
        app_id = app['id']
        bogus = '00000000-0000-4000-8000-00000000dead'
        out['definition'] = {'id': app_id, 'revision_after_create': app.get('revision_created')}
        code, body = self.hub.get('/api/applications/' + self.hub.quote(app_id))
        out['definition_get_before_assign'] = {'http': code, 'revision': (body or {}).get('revision')}
        code, body = self.hub.post('/api/applications/%s/assign' % self.hub.quote(app_id),
                                   {'node_ids': [bogus]})
        self.budget['api_mutations'] += 1
        out['assign_failure'] = {'http': code, 'body': body, 'stale_node_id': bogus}
        code, body = self.hub.get('/api/applications/' + self.hub.quote(app_id))
        out['definition_get_after_failed_assign'] = {'http': code, 'revision': (body or {}).get('revision'),
                                                     'name': (body or {}).get('name')}
        ncode, nbody = self.hub.get('/api/applications/' + self.hub.quote(app_id) + '/nodes')
        out['assigned_nodes_after_failure'] = {'http': ncode, 'nodes': nbody}
        lst = [x for x in self.hub.json('/api/applications') if x['id'] == app_id]
        out['definition_locatable_in_list'] = {'found': bool(lst),
                                               'name': lst[0]['name'] if lst else None,
                                               'revision': lst[0]['revision'] if lst else None}
        # real retry: assign to the owned child
        code, body = self.hub.post('/api/applications/%s/assign' % self.hub.quote(app_id),
                                   {'node_ids': [a]})
        self.budget['api_mutations'] += 1
        out['assign_retry'] = {'http': code, 'body': body}
        ncode, nbody = self.hub.get('/api/applications/' + self.hub.quote(app_id) + '/nodes')
        out['assigned_nodes_after_retry'] = {'http': ncode, 'nodes': nbody}
        scode, sbody = self.hub.get('/api/applications/' + self.hub.quote(app_id) + '/state')
        out['app_node_state_after_retry'] = {'http': scode, 'states': sbody}
        dcode, dbody = self.hub.post('/api/applications/%s/deploy' % self.hub.quote(app_id),
                                     {'node_ids': [a], 'operation': 'deploy'})
        self.budget['api_mutations'] += 1
        out['deploy_after_retry'] = {'http': dcode,
                                     'executions': [{'id': e.get('id'), 'node_id': e.get('node_id'),
                                                     'status': e.get('status')} for e in (dbody or [])]
                                     if isinstance(dbody, list) else dbody}
        if isinstance(dbody, list) and dbody:
            eid = dbody[0]['id']
            self.state['executions']['x18_app_deploy'] = eid
            term = self.wait_exec_terminal(eid, timeout=120)
            ec, ed = self.exec_detail(eid)
            out['deploy_execution_final'] = {'waited_s': term['seconds'], 'complete': term['complete'],
                                             'status': (ed or {}).get('status'),
                                             'block_reason': (ed or {}).get('block_reason'),
                                             'exit_code': (ed or {}).get('exit_code'),
                                             'stderr_tail': ((ed or {}).get('stderr') or '')[-400:]}
            acode, abody = self.hub.get('/api/applications/%s/executions' % self.hub.quote(app_id))
            out['app_executions_endpoint'] = {'http': acode,
                                              'count': len(abody) if isinstance(abody, list) else None,
                                              'statuses': sorted({e.get('status') for e in (abody or [])})
                                              if isinstance(abody, list) else None}
            try:
                kdb = self.children['a'].db_query(
                    'SELECT id,application_id,phase,started_at,updated_at FROM deployments ORDER BY started_at')
                out['agent_side_deployment_journal'] = kdb['rows']
            except Exception as exc:
                out['agent_side_deployment_journal'] = {'_error': '%s: %s' % (type(exc).__name__, exc)}
        self.save_state()
        return self.ev('phase5-x18-application.json', out)

    # ======================= phase 6 : X15 logs + disconnect =======================
    def phase6_x15(self):
        out = {'at_utc': now_utc(), 'case': 'X15'}
        fx = self.state['fixtures']
        task = fx['task_long']
        kid = self.children['a']
        proxy = self.proxies['a']

        token = self.hub.token
        ab_open('/agents', wait=1.0)
        ab_js("localStorage.setItem('nodesteer_token', %s); 'token-set'" % json.dumps(token))

        code, rows = self._run_now(task)
        out['dispatch'] = {'http': code, 'executions': [{'id': r['id'], 'node_id': r['node_id'],
                                                        'status': r['status']} for r in (rows or [])]}
        eid = rows[0]['id']
        self.state['executions']['x15_long'] = eid
        prev_logs = self.exec_logs(eid)[1]

        ab_open('/executions/' + eid, wait=2.0)
        ab_js("window.__g6mark = 'm' + Math.random().toString(36).slice(2); document.title = 'g6-exec-' + %s"
              % json.dumps(eid[:8]))
        samples = []
        cut_at = None
        restore_at = None
        t0 = time.time()
        sample_js = r'''(() => {
          const txt = (document.body.innerText || '');
          const head = txt.slice(0, 700);
          const words = ['等待中','运行中','成功','失败','已取消','已跳过','超时','已阻断'];
          return {found: words.filter(w => head.includes(w)),
                  marker: window.__g6mark || null,
                  preLen: (document.querySelector('#execution-logs pre')||{}).innerText ?
                          (document.querySelector('#execution-logs pre').innerText.length) : null,
                  href: location.pathname};
        })()'''
        while time.time() - t0 < 95:
            st = None
            try:
                st = ab_js(sample_js)
            except Exception as exc:
                st = {'_error': type(exc).__name__}
            hub_state = self.exec_detail(eid)
            samples.append({'t': round(time.time() - t0, 1), 'at_utc': now_utc(), 'ui': st,
                            'hub_status': (hub_state[1] or {}).get('status')})
            elapsed = time.time() - t0
            if cut_at is None and elapsed >= 6:
                proxy.set_mode('cut')
                self.budget['cut_windows'] += 1
                cut_at = {'at_utc': now_utc(), 'elapsed': round(elapsed, 1)}
            if cut_at is not None and restore_at is None and elapsed >= 6 + 16:
                proxy.set_mode('pass')
                restore_at = {'at_utc': now_utc(), 'elapsed': round(elapsed, 1)}
            if (hub_state[1] or {}).get('status') in TERMINAL and elapsed > 8:
                break
            time.sleep(2.0)
        out['ui_live_sampling'] = {'cut': cut_at, 'restore': restore_at,
                                   'samples': samples,
                                   'distinct_ui_states': sorted({w for s in samples if isinstance(s.get('ui'), dict)
                                                                 for w in (s['ui'].get('found') or [])}),
                                   'marker_stable': len({s['ui'].get('marker') for s in samples
                                                         if isinstance(s.get('ui'), dict)}) == 1}
        # two extra in-page samples after the hub row turns terminal: the same page
        # instance (marker unchanged) must show the terminal state without a reload
        tail_samples = []
        for _ in range(2):
            time.sleep(3.0)
            try:
                st = ab_js(sample_js)
            except Exception as exc:
                st = {'_error': type(exc).__name__}
            tail_samples.append({'at_utc': now_utc(), 'ui': st,
                                 'hub_status': (self.exec_detail(eid)[1] or {}).get('status')})
        out['ui_live_sampling']['post_terminal_samples'] = tail_samples
        out['ui_live_sampling']['ui_terminal_seen_without_reload'] = any(
            '成功' in (s['ui'].get('found') or []) for s in tail_samples
            if isinstance(s.get('ui'), dict))
        out['ui_live_sampling']['ui_states_including_tail'] = sorted(
            {w for s in samples + tail_samples if isinstance(s.get('ui'), dict)
             for w in (s['ui'].get('found') or [])})
        term = self.wait_exec_terminal(eid, timeout=120)
        ec, ed = self.exec_detail(eid)
        out['final_execution'] = {'waited_s': term['seconds'], 'complete': term['complete'],
                                  'status': (ed or {}).get('status'), 'exit_code': (ed or {}).get('exit_code'),
                                  'stdout_len': len((ed or {}).get('stdout') or ''),
                                  'stderr_len': len((ed or {}).get('stderr') or ''),
                                  'synced': (ed or {}).get('synced')}
        stdout = (ed or {}).get('stdout') or ''
        stderr = (ed or {}).get('stderr') or ''
        out['terminal_output_oracles'] = {
            'final_stdout_has_start': (PREFIX + ' long-run start') in stdout,
            'final_stdout_has_done': (PREFIX + ' long-run done') in stdout,
            'final_stdout_has_stdout_tail': (PREFIX + 'stdout-tail') in stdout,
            'final_stderr_has_tail': (PREFIX + 'stderr-tail') in stderr,
            'stdout_line_markers': sum(1 for line in stdout.splitlines() if PREFIX + 'long' in line),
            'stderr_line_markers': sum(1 for line in stderr.splitlines() if PREFIX + 'stderr' in line)}

        lcode, chunks = self.exec_logs(eid)
        out['realtime_chunks'] = {'http': lcode, 'count': len(chunks),
                                  'seqs': [c.get('seq') for c in chunks],
                                  'streams': sorted({c.get('stream') for c in chunks}),
                                  'total_chars': sum(len(c.get('chunk') or '') for c in chunks),
                                  'has_stdout_marker': any(PREFIX + 'long' in (c.get('chunk') or '') for c in chunks),
                                  'has_stderr_marker': any(PREFIX + 'stderr' in (c.get('chunk') or '') for c in chunks),
                                  'seq_strictly_increasing': [c.get('seq') for c in chunks]
                                  == sorted(c.get('seq') for c in chunks),
                                  'joined_sha256': hashlib.sha256(
                                      ''.join(c.get('chunk') or '' for c in chunks).encode()).hexdigest()}
        node_now = self.node(self.state['nodes']['a']['id']) or {}
        out['node_after_cut'] = {'status': node_now.get('status'), 'sync_status': node_now.get('sync_status'),
                                 'global_revision': node_now.get('global_revision')}
        out['proxy_ledger'] = {'events': [e for e in proxy.events if e['event'] in
                                          ('mode_set', 'connections_closed_for_cut', 'connection_open',
                                           'connection_closed', 'listener_stopped', 'listener_restarted')],
                               'counts_by_type': proxy.state()['counts_by_type'],
                               'frames_total': proxy.state()['frames_total']}
        self.budget['frames_total'] = proxy.state()['frames_total']

        # second run: new execution, own chunks, no old-chunk overwrite
        code2, rows2 = self._run_now(task)
        eid2 = rows2[0]['id']
        self.state['executions']['x15_long_second'] = eid2
        term2 = self.wait_exec_terminal(eid2, timeout=150)
        def shard_summary(x):
            chs = self.exec_logs(x)[1]
            joined = ''.join(c.get('chunk') or '' for c in chs)
            return {'id': x, 'status': (self.exec_detail(x)[1] or {}).get('status'), 'chunks': len(chs),
                    'joined_len': len(joined),
                    'sha_of_joined_chunks': hashlib.sha256(joined.encode()).hexdigest()}

        first_after = shard_summary(eid)
        second = shard_summary(eid2)
        out['shard_isolation'] = {
            'first_execution_chunks_before_second_run': out['realtime_chunks'],
            'first_execution_after_second_run': first_after,
            'second_execution': second,
            'second_run_terminal': {'waited_s': term2['seconds'], 'complete': term2['complete'],
                                    'last': term2['last']},
            'first_execution_chunks_unchanged_by_second_run':
                first_after['sha_of_joined_chunks'] == out['realtime_chunks']['joined_sha256'],
            'second_execution_has_own_seq0_and_distinct_content':
                (second['chunks'] > 0
                 and [c.get('seq') for c in self.exec_logs(eid2)[1]][0] == 0
                 and second['sha_of_joined_chunks'] != first_after['sha_of_joined_chunks'])}
        out['prev_logs_before_run_were_empty'] = len(prev_logs) == 0

        # --- UI log controls on the recovered terminal execution
        ui_logs = {}
        ab_open('/executions/' + eid, wait=2.5)
        ui_logs['terminal_page_text_probe'] = ab_js(sample_js)
        ui_logs['pre_len_terminal'] = ab_js(
            "(() => {const p=document.querySelector('#execution-logs pre');"
            " return {len: p? p.innerText.length: null, hasDone: p? p.innerText.includes(%s): null,"
            " hasStderr: p? p.innerText.includes(%s): null};})()"
            % (json.dumps(PREFIX + ' long-run done'), json.dumps(PREFIX + 'stderr-tail')))
        # search filter
        ui_logs['search'] = ab_js(r'''(async () => {
          const inp = [...document.querySelectorAll('input')].find(i => i.placeholder === %s);
          if (!inp) return {ok:false, reason:'search input not found'};
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(inp, %s);
          inp.dispatchEvent(new Event('input', {bubbles:true}));
          await new Promise(r=>setTimeout(r,500));
          const pre = document.querySelector('#execution-logs pre');
          const lines = pre ? pre.innerText.split('\n') : [];
          return {ok:true, lines: lines.length, all_match: lines.every(l => l.toLowerCase().includes(%s.toLowerCase())),
                  sample: lines.slice(0,3)};
        })()''' % (json.dumps('搜索日志...'), json.dumps('stdout-line-007'), json.dumps('stdout-line-007')))
        ui_logs['search_reset'] = ab_js(r'''(async () => {
          const inp = [...document.querySelectorAll('input')].find(i => i.placeholder === %s);
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(inp, ''); inp.dispatchEvent(new Event('input', {bubbles:true}));
          await new Promise(r=>setTimeout(r,400));
          return {ok:true};
        })()''' % json.dumps('搜索日志...'))
        # stream filter
        ui_logs['stream_filter'] = ab_js(r'''(async () => {
          const sel = document.querySelector('#execution-logs select');
          if (!sel) return {ok:false, reason:'stream select not found'};
          const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;
          const out = {};
          for (const v of ['stderr','stdout','all']) {
            setter.call(sel, v); sel.dispatchEvent(new Event('change', {bubbles:true}));
            await new Promise(r=>setTimeout(r,400));
            const pre = document.querySelector('#execution-logs pre');
            const txt = pre ? pre.innerText : '';
            out[v] = {len: txt.length, has_stdout_marker: txt.includes(%s), has_stderr_marker: txt.includes(%s)};
          }
          return Object.assign({ok:true}, out);
        })()''' % (json.dumps(PREFIX + 'stdout-tail'), json.dumps(PREFIX + 'stderr-tail')))
        # copy + follow
        ui_logs['copy'] = ab_js(r'''(async () => {
          const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '复制日志');
          if (!btn) return {ok:false, reason:'copy button not found'};
          const before = (document.body.innerText||'');
          btn.click();
          const seen = [];
          for (let i=0;i<10;i++) {
            await new Promise(r=>setTimeout(r,200));
            const t = (document.body.innerText||'');
            const tail = t.slice(before.length > 0 ? Math.max(0, t.length - 400) : 0);
            seen.push(tail.includes('已复制') ? 'copied' :
                      (tail.includes('无法自动写入剪切板') ? 'manual' :
                       (tail.includes('复制失败') ? 'failed' : 'none')));
          }
          return {ok:true, toast_seen: seen.filter(x=>x!=='none'),
                  clipboard_api: typeof navigator.clipboard !== 'undefined',
                  is_secure_context: window.isSecureContext};
        })()''')
        ui_logs['follow'] = ab_js(r'''(async () => {
          const btn = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim() === '跟随');
          const pre = document.querySelector('#execution-logs pre');
          if (!btn || !pre) return {ok:false};
          const before = {pressed: btn.getAttribute('aria-pressed'), cls: btn.className,
                          scrollTop: pre.scrollTop, scrollHeight: pre.scrollHeight, clientHeight: pre.clientHeight};
          pre.scrollTop = 0;
          btn.click();
          await new Promise(r=>setTimeout(r,500));
          return {ok:true, before, after: {scrollTop: pre.scrollTop, cls: btn.className,
                  scrolled_to_bottom: Math.abs(pre.scrollTop + pre.clientHeight - pre.scrollHeight) <= 2}};
        })()''')
        out['ui_log_controls'] = ui_logs
        out['ui_screenshots'] = {'terminal': ab_shot('X15-execution-terminal.png'),
                                 'logs': ab_shot('X15-execution-logs.png')}
        self.save_state()
        return self.ev('phase6-x15-logs-recovery.json', out)

    # ======================= phase 7 : cross-page UI =======================
    def phase7_ui_crosspage(self):
        out = {'at_utc': now_utc()}
        a = self.state['nodes']['a']['id']
        b = self.state['nodes']['b']['id']
        fx = self.state['fixtures']
        token = self.hub.token
        ab_open('/agents', wait=2.0)
        ab_js("localStorage.setItem('nodesteer_token', %s); 'ok'" % json.dumps(token))

        # --- X12 cross page: node detail label cache behaviour
        ab_open('/agents/' + a, wait=2.5)
        out['node_a_detail_initial'] = ab_js(r'''(() => {
          const t = document.body.innerText || '';
          const m = t.match(/qa-g6-[a-z0-9]+-role=\S+/g) || [];
          return {labels_seen: [...new Set(m)], has_noLabels: t.includes('暂无标签'), rev: (t.match(/r\d+/)||[null])[0]};
        })()''')
        code, body = self.set_labels(a, {fx['label_key']: 'green'})
        out['api_label_change'] = {'http': code, 'node_a_labels': (self.node(a) or {}).get('labels')}
        time.sleep(3.0)
        out['node_a_detail_immediately_after_api_change'] = ab_js(r'''(() => {
          const t = document.body.innerText || '';
          return {labels_seen: [...new Set(t.match(/qa-g6-[a-z0-9]+-role=\S+/g) || [])]};
        })()''')
        # cache convergence: remount by navigating away and back (query refetch on mount)
        ab_open('/agents', wait=1.5)
        ab_open('/agents/' + a, wait=2.5)
        out['node_a_detail_after_convergence'] = ab_js(r'''(() => {
          const t = document.body.innerText || '';
          return {labels_seen: [...new Set(t.match(/qa-g6-[a-z0-9]+-role=\S+/g) || [])],
                  exec_rows: (t.match(/qa-g6-[a-z0-9]+-task-label-target/g)||[]).length};
        })()''')
        self.set_labels(a, {fx['label_key']: 'blue'})
        out['label_restored'] = {'node_a_labels': (self.node(a) or {}).get('labels')}
        out['node_a_screenshot'] = ab_shot('X12-node-detail-labels.png')

        # --- X12 cross page: task detail executions show the retargeted node
        ab_open('/tasks/' + fx['task_label'], wait=3.0)
        out['task_label_detail'] = ab_js(r'''(() => {
          const t = document.body.innerText || '';
          const rows = [...document.querySelectorAll('tbody tr')].map(r => r.innerText.replace(/\n/g,' | ').slice(0,200));
          return {header: t.slice(0, 420),
                  label_target_seen: t.includes(%s),
                  execution_rows: rows,
                  node_rows_a: (t.match(/%s/g)||[]).length,
                  node_rows_b: (t.match(/%s/g)||[]).length};
        })()''' % (json.dumps(fx['label_key'] + '=' + fx['label_value']),
                   json.dumps(PREFIX + 'a'), json.dumps(PREFIX + 'b')))
        out['task_label_screenshot'] = ab_shot('X12-task-detail-target.png')

        # --- X18 client-side source-change pruning (re-check) + 0 POST
        ab('network', 'requests', '--clear')
        ab_open('/transfers', wait=2.5)
        # radix Select triggers render as role=combobox; [0] = source, [1] = target
        sel_source = r'''(async () => {
          const trig = document.querySelectorAll('[role=combobox]')[%d];
          if (!trig) return {ok:false, reason:'combobox %d missing'};
          const current = (trig.innerText||'').trim();
          trig.click(); await new Promise(r=>setTimeout(r,400));
          const opts = [...document.querySelectorAll('[role=option]')].map(o=>(o.innerText||'').trim());
          const opt = [...document.querySelectorAll('[role=option]')].find(o => (o.innerText||'').includes(%s));
          if (!opt) return {ok:false, current, opts};
          opt.click(); await new Promise(r=>setTimeout(r,450));
          return {ok:true, previous_value: current, chosen: %s};
        })()'''
        sel_target = sel_source
        fill_dst = r'''(() => {
          const inp = [...document.querySelectorAll('input')].find(i => i.placeholder === '/var/lib/app/config.yaml');
          if (!inp) return {ok:false};
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
          setter.call(inp, %s); inp.dispatchEvent(new Event('input',{bubbles:true}));
          return {ok:true, value: inp.value};
        })()'''
        state_js = r'''(() => {
          const start = [...document.querySelectorAll('button')].find(b => (b.innerText||'').trim().startsWith('开始传输'));
          const chips = [...document.querySelectorAll('button,span,div')].filter(b => (b.innerText||'').trim() === %s);
          const combos = [...document.querySelectorAll('[role=combobox]')].map(c => (c.innerText||'').trim());
          return {combobox_values: combos,
                  start_disabled: start ? start.disabled : null,
                  target_chips: chips.length,
                  body_has_prune_msg: (document.body.innerText||'').includes('目标节点不能与来源节点相同')};
        })()'''
        out['x18_ui'] = {}
        out['x18_ui']['select_source_a'] = ab_js(sel_source % (0, 0, json.dumps(PREFIX + 'a'), json.dumps(PREFIX + 'a')))
        out['x18_ui']['select_target_b'] = ab_js(sel_target % (1, 1, json.dumps(PREFIX + 'b'), json.dumps(PREFIX + 'b')))
        out['x18_ui']['fill_dst'] = ab_js(fill_dst % json.dumps(fx['xfer_dst_b_ok'] + '.ui'))
        add = ab_js(r'''(async () => {
          const b = [...document.querySelectorAll('button')].find(x => (x.innerText||'').trim() === '添加目标');
          if (!b) return {ok:false};
          const before = {disabled: b.disabled};
          b.click(); await new Promise(r=>setTimeout(r,500));
          return {ok:true, before};
        })()''')
        out['x18_ui']['add_target'] = add
        out['x18_ui']['ready'] = ab_js(state_js % json.dumps(PREFIX + 'b'))
        out['x18_ui']['shot_ready'] = ab_shot('X18-transfer-ready.png')
        # now change the source to the node that is already a target
        out['x18_ui']['switch_source_to_b'] = ab_js(sel_source % (0, 0, json.dumps(PREFIX + 'b'), json.dumps(PREFIX + 'b')))
        time.sleep(1.0)
        out['x18_ui']['after_switch'] = ab_js(state_js % json.dumps(PREFIX + 'b'))
        out['x18_ui']['shot_after_switch'] = ab_shot('X18-transfer-after-source-switch.png')
        try:
            reqs = ab('network', 'requests')[0]
        except Exception as exc:
            reqs = 'error:%s' % type(exc).__name__
        out['x18_ui']['post_transfers_requests'] = [l for l in str(reqs).splitlines() if '/api/transfers' in l and 'POST' in l.upper()]
        out['x18_ui']['all_transfer_requests'] = [l for l in str(reqs).splitlines() if '/api/transfers' in l]
        self.save_state()
        return self.ev('phase7-ui-crosspage.json', out)

    # ======================= phase 8 : cleanup + retained history =======================
    def phase8_cleanup(self):
        out = {'at_utc': now_utc(), 'retained_history': {}}
        # 1) stop the owned children first (no systemd unit involved)
        for label in ('a', 'b', 'c'):
            self.budget['child_process_stops'] += 1
            try:
                out.setdefault('child_removals', {})[label] = self.children[label].remove()
            except Exception as exc:
                out.setdefault('child_removals', {})[label] = {'_error': '%s: %s' % (type(exc).__name__, exc)}
        # 2) release the undeletable-but-active transfers: a transfer with a target
        #    still in DELIVERING permanently blocks node deletion (409 has active
        #    file transfer).  Transfers have no DELETE endpoint, so the only release
        #    is cancel -> terminal CANCELED (row itself stays as history).
        out['cancelled_transfers'] = {}
        for tag, tid in (self.state.get('transfers') or {}).items():
            cur = self.transfer(tid)
            if cur.get('status') in ('SUCCESS', 'FAILED', 'CANCELED'):
                continue
            code, body = self.hub.post('/api/transfers/%s/cancel' % self.hub.quote(tid), {})
            self.budget['api_mutations'] += 1
            after = self.transfer(tid)
            out['cancelled_transfers'][tag] = {'id': tid, 'http': code, 'status_after': after.get('status'),
                                               'targets_after': [{'node_id': x['node_id'], 'status': x['status']}
                                                                 for x in after['targets']]}
        # 3) delete owned hub objects in dependency order
        #    (tasks -> scripts -> schedules -> groups -> applications -> nodes)
        bykind = {}
        for kind, oid, name in self.state['created']:
            bykind.setdefault(kind, []).append((oid, name))
        # remove the app assignment before deleting the node records
        app = self.state.get('app') or {}
        if app.get('id'):
            code, body = self.hub.post('/api/applications/%s/assign' % self.hub.quote(app['id']),
                                       {'node_ids': [n['id'] for n in self.state['nodes'].values()],
                                        'remove': True})
            self.budget['api_mutations'] += 1
            out.setdefault('app_unassign', {})['http'] = code
        for kind in ('task', 'script', 'schedule', 'group', 'application'):
            for oid, name in bykind.get(kind, []):
                code, body = self.hub.delete('/api/%ss/%s' % (kind, self.hub.quote(oid)))
                self.budget['api_mutations'] += 1
                self.state['delete_results'][oid] = {'kind': kind, 'http': code}
                self.state['deleted'].append([kind, oid])
                out.setdefault('deletes', {})[oid] = {'kind': kind, 'name': name, 'http': code,
                                                      'body': body if code != 200 else None}
        for label in ('a', 'b', 'c'):
            nid = self.state['nodes'][label]['id']
            code, body = self.hub.delete('/api/nodes/' + self.hub.quote(nid))
            self.budget['api_mutations'] += 1
            self.state['delete_results'][nid] = {'kind': 'node', 'http': code}
            self.state['deleted'].append(['node', nid])
            out.setdefault('node_deletes', {})[label] = {'node_id': nid, 'http': code,
                                                         'body': body if code != 200 else None}
        # 4) stop the owned frame proxies (release the ephemeral listener port)
        for label, proxy in self.proxies.items():
            port = proxy.port
            proxy.stop()
            out.setdefault('proxy_released', {})[label] = {'port': port}
        # 5) retained, undeletable history (no DELETE endpoint for transfers/executions)
        transfers = self.hub.json('/api/transfers')
        mine = [t for t in transfers if (t.get('source_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
                or any((x.get('destination_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
                       for x in t.get('targets') or [])]
        out['retained_history']['transfers_total_on_hub'] = len(transfers)
        out['retained_history']['transfers_created_by_batch'] = len(mine)
        out['retained_history']['transfers'] = [
            {'id': t['id'], 'status': t['status'], 'source_path': t.get('source_path'),
             'created_at': t.get('created_at'),
             'targets': [{'node_id': x['node_id'], 'destination_path': x['destination_path'],
                          'status': x['status']} for x in t.get('targets') or []]} for t in mine]
        exp = []
        for key, eid in self.state['executions'].items():
            if isinstance(eid, list):
                for e in eid:
                    exp.append((key, e))
            else:
                exp.append((key, eid))
        rows = []
        for key, eid in exp:
            code, d = self.exec_detail(eid)
            ec, chunks = self.exec_logs(eid)
            rows.append({'purpose': key, 'execution_id': eid, 'http': code,
                         'status': (d or {}).get('status'), 'node_id': (d or {}).get('node_id'),
                         'task_id': (d or {}).get('task_id'),
                         'log_chunks_retained': len(chunks)})
        out['retained_history']['executions'] = rows
        out['retained_history']['execution_rows_created_by_batch'] = len(rows)
        audit = self.hub.json('/api/audit?limit=500')
        out['retained_history']['audit_rows_for_batch'] = len(
            [a for a in audit if PREFIX in (a.get('detail') or '')])
        out['transfers_after_cleanup_count'] = len(self.hub.json('/api/transfers'))
        out['executions_after_cleanup_count'] = len(self.hub.json('/api/executions?limit=500'))
        out['proxy_modes_at_stop'] = {label: p.mode() for label, p in self.proxies.items()}
        self.save_state()
        return self.ev('phase8-cleanup.json', out)
        # retained (undeletable) history
        transfers = self.hub.json('/api/transfers')
        mine = [t for t in transfers if (t.get('source_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
                or any((x.get('destination_path') or '').startswith('/var/lib/nodesteer-qa-g6-')
                       for x in t.get('targets') or [])]
        out['retained_history']['transfers_total_on_hub'] = len(transfers)
        out['retained_history']['transfers_created_by_batch'] = len(mine)
        out['retained_history']['transfers'] = [
            {'id': t['id'], 'status': t['status'], 'source_path': t.get('source_path'),
             'created_at': t.get('created_at'),
             'targets': [{'node_id': x['node_id'], 'destination_path': x['destination_path'],
                          'status': x['status']} for x in t.get('targets') or []]} for t in mine]
        exp = []
        for key, eid in self.state['executions'].items():
            if isinstance(eid, list):
                for e in eid:
                    exp.append((key, e))
            else:
                exp.append((key, eid))
        rows = []
        for key, eid in exp:
            code, d = self.exec_detail(eid)
            ec, chunks = self.exec_logs(eid)
            rows.append({'purpose': key, 'execution_id': eid, 'http': code,
                         'status': (d or {}).get('status'), 'node_id': (d or {}).get('node_id'),
                         'task_id': (d or {}).get('task_id'),
                         'log_chunks_retained': len(chunks)})
        out['retained_history']['executions'] = rows
        out['retained_history']['execution_rows_created_by_batch'] = len(rows)
        out['retained_history']['audit_rows_for_batch'] = len(
            [a for a in self.hub.json('/api/audit?limit=500')
             if (a.get('detail') or '').startswith(PREFIX) or PREFIX in (a.get('detail') or '')])
        out['transfers_after_cleanup_count'] = len(self.hub.json('/api/transfers'))
        out['executions_after_cleanup_count'] = len(self.hub.json('/api/executions?limit=500'))
        self.save_state()
        return self.ev('phase8-cleanup.json', out)


def main():
    run = Run()
    phases = [
        ('phase0_guard', run.phase0_guard),
        ('phase1_children', run.phase1_children),
        ('phase2_fixtures', run.phase2_fixtures),
        ('phase3_x12', run.phase3_x12),
        ('phase4_x18_transfer', run.phase4_x18_transfer),
        ('phase5_x18_app', run.phase5_x18_app),
        ('phase6_x15', run.phase6_x15),
        ('phase7_ui_crosspage', run.phase7_ui_crosspage),
        ('phase8_cleanup', run.phase8_cleanup),
    ]
    results = {}
    ok = True
    for name, fn in phases:
        t0 = time.time()
        print('=== %s start %s' % (name, now_utc()), flush=True)
        try:
            results[name] = {'status': 'ok', 'seconds': round(time.time() - t0, 1), 'result': fn()}
        except Exception as exc:
            ok = False
            import traceback
            results[name] = {'status': 'failed', 'seconds': round(time.time() - t0, 1),
                             'error': '%s: %s' % (type(exc).__name__, exc),
                             'traceback': traceback.format_exc()[-2000:]}
            print('!!! %s failed: %s' % (name, exc), flush=True)
            try:
                run.save_state()
            except Exception:
                pass
            break
        print('=== %s done in %.1fs' % (name, time.time() - t0), flush=True)
    hubapi.write_json(str(HERE / 'phase-results.json'),
                      {k: {kk: vv for kk, vv in v.items() if kk != 'result'} for k, v in results.items()})
    run.save_state()
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
