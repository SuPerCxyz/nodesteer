#!/usr/bin/env python3
"""G5 fixture batch: tasks / scripts / groups / conditions / labels / applications.

Verified build: `dddee4f0-e2e-repair-20261016` (hub cd8b7f9d..., agent b04383bf...),
repair authority http://192.168.100.209:18080, owned host native-1 (192.168.100.204).

Cases: P09P11-07, P09P11-08, P09P11-09, P09P11-11, P09P11-12, P09P11-15,
       P09P11-16, P09P11-17, X09, X11, FL-02, FL-03, FL-04, P14P15-09.

Evidence chain per case: real Web UI action on :18080 -> observed Hub HTTP call ->
Hub/Agent state change (API readback) -> real host effect on the owned child Agent
-> UI feedback.  Owned fixtures only (`qa-g5-<nonce>-*`); protected nodes are never
mutated (read/select only).

Reused writing style from qa-resume-20261016-appui / qa-resume-20261014-web.
"""
import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import browser as browselib
import hostlib_g5 as hostlib
import hubapi

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EVID = BATCH / 'evidence'
EVID.mkdir(parents=True, exist_ok=True)

NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = NONCE + '-'
HUB_HTTP = 'http://192.168.100.209:18080'
HUB_WS = 'ws://192.168.100.209:18080'

VERIFIED_BUILD = hostlib.AGENT_BUILD
VERIFIED_AGENT_SHA = hostlib.AGENT_SHA

NODE_HOST = 'n1'
CHILD_KEYS = ('a', 'b')
CHILD_TAGS = {'a': '', 'b': '-b'}
HOST_KEY = 'n1'

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}

METRICS = ['cpu_usage', 'memory_usage', 'disk_usage', 'file_exists',
           'dir_exists', 'process_exists', 'port_listening', 'command_result']
OPERATORS = ['==', '!=', '>', '<', '>=', '<=']
REMOTE_PROPS = ['online', 'last_execution']
REMOTE_OPS = ['==', '!=']
APP_OPS = ['start', 'stop', 'restart', 'upgrade']
APP_DEPLOY_OPS = ['deploy', 'upgrade']

LABEL_KEY = PREFIX + 'env'
LABEL_V_A = 'g5a'
LABEL_V_B = 'g5b'

SCRIPT_NAME = PREFIX + 'script-a'
GROUP_NAME = PREFIX + 'group-a'
LABEL_GROUP_NAME = PREFIX + 'lgroup-a'
ARTIFACT_NAME = PREFIX + 'payload-app'
APP_NAME = PREFIX + 'app'

UNIT_NAME = 'nodesteer-' + PREFIX + 'app.service'
OPT_ROOT = '/opt/' + PREFIX.rstrip('-')
BINARY = OPT_ROOT + '/app/bin/app'
CONFIG = OPT_ROOT + '/app/config/app.env'

CASE_IDS = ['P09P11-07', 'P09P11-08', 'P09P11-09', 'P09P11-11', 'P09P11-12',
            'P09P11-15', 'P09P11-16', 'P09P11-17', 'X09', 'X11', 'FL-02',
            'FL-03', 'FL-04', 'P14P15-09']


def NOW():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def SHA(b):
    return hashlib.sha256(b).hexdigest()


def browser_session():
    f = HERE / 'browser-session.txt'
    if f.is_file():
        v = f.read_text().strip()
        if v:
            return v
    v = 'g5-%s-%s' % (NONCE.split('-')[-1], secrets.token_hex(3))
    f.write_text(v)
    f.chmod(0o600)
    return v


BROWSER_SESSION = browser_session()


def app_payload(chunk=64):
    lines = ['#!/bin/sh\n', '# nodesteer qa-g5 fixture application\n']
    line = '# g5-padding-%s\n'
    size = 0
    while size < 48 * 1024:
        c = line % ('p' * 60)
        lines.append(c)
        size += len(c)
    lines.append('if [ -n "$APP_VERSION_FILE" ]; then echo "v1" > "$APP_VERSION_FILE"; fi\n')
    lines.append('exec sleep 1000000\n')
    return ''.join(lines).encode()


class Run:
    def __init__(self, browser):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.br = browser
        self.child = {}
        self.cases = {}
        self.state = {'nodes': {}, 'agents': {}, 'tasks': {}, 'scripts': {}, 'groups': {},
                      'apps': {}, 'artifacts': {}, 'deletes': {}, 'executions': {},
                      'fixtures': {}, 'host_effects': {}}
        self.budget = {'api_reads': 0, 'api_mutations': 0, 'ui_actions': 0,
                       'task_creates': 0, 'task_runs': 0, 'node_enroll_posts': 0,
                       'node_deletes': 0, 'child_process_starts': 0,
                       'executions_created_by_this_batch': [],
                       'ui_screenshots': 0}
        self.load_state()

    # ---------------- persistence / evidence ----------------
    def state_path(self):
        return HERE / 'state.json'

    def load_state(self):
        p = self.state_path()
        if p.is_file():
            try:
                saved = json.loads(p.read_text())
                prev = saved.get('state', {})
                for k in ('nodes', 'agents', 'tasks', 'scripts', 'groups', 'apps',
                          'artifacts', 'deletes', 'executions', 'fixtures', 'host_effects'):
                    self.state.setdefault(k, {}).update(prev.get(k, {}))
                if prev.get('baseline_counts'):
                    self.state['baseline_counts'] = prev['baseline_counts']
                self.budget.update(saved.get('budget', {}))
                self.cases = saved.get('cases', {})
            except Exception:
                pass

    def save_state(self):
        self.state_path().write_text(json.dumps(
            {'nonce': NONCE, 'state': self.state, 'budget': self.budget,
             'cases': self.cases, 'updated_at_utc': NOW()},
            ensure_ascii=False, indent=1) + '\n')

    def rec(self, name, value):
        p = EVID / name
        tmp = EVID / (name + '.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
        os.replace(tmp, p)
        return name

    def case(self, cid):
        return self.cases.setdefault(cid, {'dimensions': {}, 'unverified': [],
                                           'harness_errors': [], 'evidence': [],
                                           'ui_paths': []})

    def dim(self, cid, key, value, evidence=()):
        c = self.case(cid)
        c['dimensions'][key] = value
        if evidence:
            c['evidence'].extend(evidence)
        return value

    def unverified(self, cid, text):
        self.case(cid)['unverified'].append(text)

    def harn_err(self, cid, text):
        self.case(cid)['harness_errors'].append(text)

    # ---------------- hub helpers ----------------
    def get(self, path):
        self.budget['api_reads'] += 1
        return self.hub.get(path)

    def jget(self, path):
        code, body = self.get(path)
        if code != 200:
            raise RuntimeError('get_%s_http_%s' % (path, code))
        return body

    def mutate(self, method, path, body=None):
        self.budget['api_mutations'] += 1
        return self.hub.raw(method, path, body)

    def q(self, value):
        return self.hub.quote(value)

    def nodes(self):
        return self.jget('/api/nodes')

    def task(self, tid):
        return self.jget('/api/tasks/' + self.q(tid))

    def node_labels(self, nid):
        return self.jget('/api/nodes/' + self.q(nid)).get('labels') or {}

    def executions_for(self, task_id, node_id=None, limit=200):
        self.budget['api_reads'] += 1
        code, body = self.hub.get('/api/executions?limit=%d' % limit)
        if code != 200 or not isinstance(body, list):
            return []
        out = [e for e in body if e.get('task_id') == task_id]
        if node_id:
            out = [e for e in out if e.get('node_id') == node_id]
        return out

    def run_now(self, task_id, params=None):
        self.budget['api_mutations'] += 1
        self.budget['task_runs'] += 1
        return self.hub.post('/api/tasks/%s/run' % self.q(task_id), {'params': params or {}})

    def wait_execution(self, task_id, node_id, timeout=120, since=None):
        """Wait for a NEW terminal execution of task on node and return it."""
        known = set(since) if since is not None else {
            e['id'] for e in self.executions_for(task_id, node_id)}
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            for e in self.executions_for(task_id, node_id):
                if e['id'] in known:
                    continue
                last = e
                if e.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED',
                                       'CANCELED', 'CANCELLED'):
                    self.budget['executions_created_by_this_batch'].append(e['id'])
                    self.state['executions'][e['id']] = {
                        'task_id': task_id, 'node_id': node_id, 'status': e.get('status')}
                    return e
            time.sleep(3)
        raise RuntimeError('execution_timeout task=%s node=%s last=%s' % (task_id, node_id, last))

    def wait_node(self, node_id, want=('online', 'synced'), timeout=180):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            code, node = self.hub.get('/api/nodes/' + self.q(node_id))
            if code == 200 and node.get('status') == want[0] \
                    and node.get('sync_status') == want[1]:
                return node
            time.sleep(3)
        return node

    # ---------------- browser helpers ----------------
    def ui_open(self, path):
        self.budget['ui_actions'] += 1
        self.br.open(HUB_HTTP + path)
        return self.br.url()

    def ui_pick_option(self, option, combo_js, exact=True):
        res = self.br.evaljs(
            "(function(){const cbs=Array.from(document.querySelectorAll('[role=combobox]'));"
            "const c=(%s);if(!c)return 'no_combo';c.scrollIntoView({block:'center'});"
            "c.click();return 'opened';})()" % combo_js)
        if res != 'opened':
            raise RuntimeError('ui_pick_option_open_failed:%s' % res)
        time.sleep(0.6)
        match = ("e=>e.innerText.trim()===%s" % json.dumps(option)) if exact \
            else ("e=>e.innerText.trim().startsWith(%s)" % json.dumps(option))
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(%s);if(!o)return null;o.click();return o.innerText;})()" % match)
        if clicked is None:
            opts = self.br.evaljs(
                "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText)")
            self.br.press('Escape')
            raise RuntimeError('ui_option_not_found:%s have=%s' % (option, opts))
        time.sleep(0.4)
        return clicked

    def combo_by_label(self, label_prefix):
        """JS expression finding the Radix combobox whose <label> starts with text."""
        return ("(function(){const ls=Array.from(document.querySelectorAll('label'));"
                "const l=ls.find(x=>(x.innerText||'').trim().startsWith(%s));"
                "if(!l)return null;return l.querySelector('[role=combobox]');})()"
                % json.dumps(label_prefix))

    def read_options(self, label_prefix):
        """Open the combobox identified by label and return its option list."""
        res = self.br.evaljs(
            "(function(){const ls=Array.from(document.querySelectorAll('label'));"
            "const l=ls.find(x=>(x.innerText||'').trim().startsWith(%s));"
            "if(!l)return 'no_label';const c=l.querySelector('[role=combobox]');"
            "if(!c)return 'no_combo';c.scrollIntoView({block:'center'});c.click();"
            "return 'opened';})()" % json.dumps(label_prefix))
        if res != 'opened':
            raise RuntimeError('read_options_open_failed:%s:%s' % (label_prefix, res))
        time.sleep(0.6)
        opts = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
        current = self.br.evaljs(
            "(function(){const ls=Array.from(document.querySelectorAll('label'));"
            "const l=ls.find(x=>(x.innerText||'').trim().startsWith(%s));"
            "const c=l.querySelector('[role=combobox]');return c?c.innerText.trim():null;})()"
            % json.dumps(label_prefix))
        self.br.press('Escape')
        time.sleep(0.3)
        return {'current': current, 'options': opts}

    def ui_fill_labeled(self, label_prefix, value, kind='input'):
        js = ("(function(){const ls=Array.from(document.querySelectorAll('label'));"
              "const l=ls.find(x=>(x.innerText||'').trim().startsWith(%s));"
              "if(!l)return 'no_label';const el=l.querySelector('%s');"
              "if(!el)return 'no_input';const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
              % (json.dumps(label_prefix), kind, json.dumps(value)))
        res = self.br.evaljs(js)
        if isinstance(res, str) and res.startswith('no_'):
            raise RuntimeError('ui_fill_labeled_failed:%s:%s' % (label_prefix, res))
        return res

    def ui_fill_by_placeholder(self, placeholder, value, tag='input,textarea'):
        js = ("(function(){const el=Array.from(document.querySelectorAll(%s))"
              ".find(e=>(e.placeholder||'')===%s);if(!el)return 'missing';"
              "const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
              % (json.dumps(tag), json.dumps(placeholder), json.dumps(value)))
        return self.br.evaljs(js)

    def ui_click_button(self, text):
        js = ("(function(){const b=Array.from(document.querySelectorAll('button'))"
              ".find(e=>e.innerText.trim()===%s);if(!b)return 'missing';"
              "b.scrollIntoView({block:'center'});b.click();return 'clicked';})()"
              % json.dumps(text))
        return self.br.evaljs(js)

    def ui_checkbox_states(self):
        return self.br.evaljs(
            "(function(){return Array.from(document.querySelectorAll('[role=checkbox]'))"
            ".map(cb=>{const l=cb.closest('label');"
            "return {label:l?l.innerText.trim().replace(/\\n+/g,'|'):'',"
            "checked:cb.getAttribute('aria-checked')==='true',"
            "state:cb.getAttribute('data-state')};});})()")

    def ui_set_checkbox_by_label(self, text, checked):
        js = ("(function(){const cbs=Array.from(document.querySelectorAll('[role=checkbox]'));"
              "const cb=cbs.find(c=>{const l=c.closest('label');"
              "return l&&l.innerText.includes(%s);});if(!cb)return 'missing';"
              "const on=cb.getAttribute('aria-checked')==='true';"
              "if(on===%s)return 'already';cb.scrollIntoView({block:'center'});cb.click();"
              "return 'clicked';})()" % (json.dumps(text), 'true' if checked else 'false'))
        return self.br.evaljs(js)

    def ui_condition_summary(self):
        return self.br.evaljs(
            "(function(){const ps=Array.from(document.querySelectorAll('p.font-mono'));"
            "return ps.map(p=>p.innerText.trim()).filter(t=>t.length);})()")

    def ui_paths_after(self):
        return {'url': self.br.url()}

    def ui_net(self, path_fragment, method=None):
        reqs = self.br.net_requests()
        data = (reqs or {}).get('data') or {}
        out = []
        for r in data.get('requests') or []:
            if path_fragment in (r.get('url') or '') and (method is None or r.get('method') == method):
                out.append({'method': r.get('method'), 'url': r.get('url'),
                            'status': r.get('status'), 'resourceType': r.get('resourceType')})
        return out

    def shot(self, name):
        self.budget['ui_screenshots'] += 1
        self.br.screenshot(str(EVID / name))
        return name

    # ================= phase 0: guard =================
    def phase0_guard(self):
        out = {'at_utc': NOW(), 'endpoint': self.hub.base, 'nonce': NONCE,
               'owned_prefix': PREFIX, 'verified_build': VERIFIED_BUILD,
               'verified_agent_sha256': VERIFIED_AGENT_SHA,
               'browser_session': BROWSER_SESSION, 'host': hostlib.HOSTS[HOST_KEY]}
        nodes = self.nodes()
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n:
                raise RuntimeError('protected_node_missing:' + label)
            out['protected'][label] = {'id': nid, 'status': n.get('status'),
                                       'sync_status': n.get('sync_status'),
                                       'agent_version': n.get('agent_version'),
                                       'labels': n.get('labels'),
                                       'deployment_mode': n.get('deployment_mode'),
                                       'capabilities': n.get('capabilities')}
            if n.get('status') != 'online':
                self.harn_err('GUARD', 'protected node not online: %s=%s' % (label, n.get('status')))
        counts = {}
        for ep, key in (('/api/nodes', 'nodes'), ('/api/tasks', 'tasks'),
                        ('/api/scripts', 'scripts'), ('/api/groups', 'groups'),
                        ('/api/applications', 'applications'),
                        ('/api/artifacts', 'artifacts'),
                        ('/api/executions?limit=1', 'executions_sample')):
            code, body = self.hub.get(ep)
            counts[key] = {'http': code, 'count': len(body) if isinstance(body, list) else None}
        out['baseline_counts'] = counts
        self.state['baseline_counts'] = counts
        owned = []
        for ep, attr in (('/api/tasks', 'name'), ('/api/scripts', 'name'),
                         ('/api/groups', 'name'), ('/api/applications', 'name'),
                         ('/api/artifacts', 'name'), ('/api/nodes', 'hostname')):
            code, items = self.hub.get(ep)
            for i in (items or []):
                if (i.get(attr) or '').startswith(PREFIX):
                    owned.append({'endpoint': ep, 'id': i['id'], 'name': i.get(attr)})
        out['owned_fixture_residue'] = owned
        if owned:
            raise RuntimeError('owned_fixture_already_present:%s' % owned[:3])
        hb = hostlib.OwnedChild(NONCE, HOST_KEY).host_baseline()
        out['host_baseline'] = hb
        if hb.get('agent_binary_sha256') != VERIFIED_AGENT_SHA:
            raise RuntimeError('agent_binary_sha_mismatch')
        for k in ('root_exists', 'opt_root_exists', 'owned_child_processes', 'owned_units',
                  'etc_systemd_owned', 'wants_owned', 'owned_mounts', 'global_owned_prefix_scan'):
            if hb.get(k):
                raise RuntimeError('host_residue_present:%s:%s' % (k, hb[k]))
        if hb['primary_agent_unit'].get('ActiveState') != 'active' or \
                hb['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_unit_not_active')
        out['guard_ok'] = True
        self.rec('phase00-guard.json', out)
        self.save_state()
        return out

    # ================= phase 1: owned fixtures =================
    def _enroll_child(self, key):
        tag = CHILD_TAGS[key]
        name = PREFIX + 'child-' + key
        ip = PREFIX + 'child-%s.invalid' % key
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        code, enroll = self.hub.post('/api/nodes/enrollment',
                                     {'node_name': name, 'node_ip': ip,
                                      'hub_address': HUB_HTTP})
        if code != 200 or not enroll.get('node_id'):
            raise RuntimeError('enrollment_failed_http_%s_%s' % (code, enroll))
        ch = hostlib.OwnedChild(NONCE, HOST_KEY, tag, enroll['node_id'],
                                enroll['agent_id'], name, ip,
                                self.hub.cred['registration_token'])
        self.child[key] = ch
        started = ch.write_config_and_start(HUB_WS)
        self.budget['child_process_starts'] += 1
        node = self.wait_node(enroll['node_id'])
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:%s' % key)
        self.state['nodes'][key] = enroll['node_id']
        self.state['agents'][key] = enroll['agent_id']
        self.state['fixtures'][enroll['node_id']] = {'kind': 'node', 'key': key, 'name': name}
        return {'key': key, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                'name': name, 'ip': ip, 'host': ch.host['ip'], 'root': ch.root,
                'processes': started.get('owned_processes'),
                'node_after_start': {k: node.get(k) for k in
                                     ('id', 'hostname', 'ip', 'status', 'sync_status',
                                      'agent_version', 'deployment_mode', 'capabilities')}}

    def phase1_fixtures(self):
        out = {'at_utc': NOW(), 'children': {}, 'script': {}, 'groups': {},
               'artifact': {}, 'application': {}}
        for key in CHILD_KEYS:
            out['children'][key] = self._enroll_child(key)
        # --- script fixture
        body = {'name': SCRIPT_NAME, 'description': 'qa-g5 script fixture',
                'interpreter': 'shell', 'content': '#!/bin/sh\necho %s-script-v1\n' % PREFIX,
                'parameters': [], 'environment': {}, 'working_dir': '',
                'timeout': 120, 'enabled': True}
        self.budget['api_mutations'] += 1
        code, sc = self.hub.post('/api/scripts', body)
        if code != 200:
            raise RuntimeError('script_create_http_%s_%s' % (code, sc))
        self.state['scripts']['main'] = sc['id']
        self.state['fixtures'][sc['id']] = {'kind': 'script', 'name': SCRIPT_NAME}
        out['script'] = {'id': sc['id'], 'revision': sc.get('revision'),
                         'sha256': sc.get('sha256'), 'create_http': code}
        # --- static group with child-a
        self.budget['api_mutations'] += 1
        code, g = self.hub.post('/api/groups', {
            'name': GROUP_NAME, 'description': 'qa-g5 static group',
            'type': 'static', 'members': [self.state['nodes']['a']]})
        if code != 200:
            raise RuntimeError('group_create_http_%s_%s' % (code, g))
        self.state['groups']['static'] = g['id']
        self.state['fixtures'][g['id']] = {'kind': 'group', 'name': GROUP_NAME}
        out['groups']['static'] = {'id': g['id'], 'members': g.get('members'),
                                   'type': g.get('type')}
        # --- artifact
        payload = app_payload()
        self.budget['api_mutations'] += 1
        code, art = self.hub.upload_artifact(ARTIFACT_NAME, 'v1', ARTIFACT_NAME + '.bin', payload)
        if code != 200:
            raise RuntimeError('artifact_upload_http_%s_%s' % (code, art))
        if art.get('sha256') != SHA(payload):
            raise RuntimeError('artifact_sha_mismatch')
        self.state['artifacts']['app'] = art['id']
        self.state['fixtures'][art['id']] = {'kind': 'artifact', 'name': ARTIFACT_NAME,
                                             'sha256': art['sha256'], 'size': art['size']}
        out['artifact'] = {'id': art['id'], 'sha256': art['sha256'], 'size': art['size']}
        # --- application + assignment to child-a
        app_body = {'name': APP_NAME, 'description': 'qa-g5 app fixture',
                    'version': 'v1', 'artifact_id': art['id'],
                    'binary_path': BINARY, 'arguments': [], 'environment': {},
                    'config': 'qa_g5=1\n', 'config_path': CONFIG,
                    'unit_name': UNIT_NAME, 'health_check': None}
        self.budget['api_mutations'] += 1
        code, app = self.hub.post('/api/applications', app_body)
        if code != 200:
            raise RuntimeError('app_create_http_%s_%s' % (code, app))
        self.state['apps']['main'] = app['id']
        self.state['fixtures'][app['id']] = {'kind': 'application', 'name': APP_NAME}
        self.budget['api_mutations'] += 1
        acode, abody = self.hub.post('/api/applications/%s/assign' % self.q(app['id']),
                                     {'node_ids': [self.state['nodes']['a']]})
        out['application'] = {'id': app['id'], 'assign_http': acode, 'assign_body': abody,
                              'revision': app.get('revision'), 'created_http': code,
                              'unit_name': UNIT_NAME, 'binary_path': BINARY,
                              'config_path': CONFIG}
        out['label_plan'] = {'label_key': LABEL_KEY, 'values': [LABEL_V_A, LABEL_V_B]}
        self.rec('phase01-fixtures.json', out)
        self.save_state()
        return out

    # ================= phase 2: conditions (E5-1) =================
    def _create_task(self, label, body):
        self.budget['api_mutations'] += 1
        self.budget['task_creates'] += 1
        code, t = self.hub.post('/api/tasks', body)
        if code == 200 and t.get('id'):
            self.state['tasks'][label] = t['id']
            self.state['fixtures'][t['id']] = {'kind': 'task', 'label': label,
                                               'name': body.get('name')}
        return code, t

    def base_task(self, name, nodes, **extra):
        t = {'name': name, 'type': 'command', 'command': '/bin/echo ' + name,
             'target': {'type': 'node', 'node_ids': list(nodes)},
             'parameters': [], 'param_values': {}, 'timeout': 120, 'retry': 0,
             'run_user': '', 'offline_policy': 'hub_online_required', 'enabled': True}
        t.update(extra)
        return t

    def phase2_conditions(self):
        out = {'at_utc': NOW(), 'cases': ['P09P11-15', 'P09P11-16', 'P09P11-17', 'X11']}
        node_a = self.state['nodes']['a']
        # ---- API: local condition: 8 metrics, 6 operators (accept + roundtrip)
        metric_ok, metric_bad = {}, {}
        metric_cases = {
            'cpu_usage': {'value': '10', 'path': ''},
            'memory_usage': {'value': '20', 'path': ''},
            'disk_usage': {'value': '30', 'path': '/'},
            'file_exists': {'value': 'true', 'path': '/etc/hostname'},
            'dir_exists': {'value': 'true', 'path': '/tmp'},
            'process_exists': {'value': 'true', 'path': 'systemd'},
            'port_listening': {'value': 'true', 'path': '22'},
            'command_result': {'value': '0', 'command': 'true'},
        }
        for metric, spec in metric_cases.items():
            cond = {'type': 'local', 'local': {'metric': metric, 'operator': spec.get('op', '=='),
                                               'value': spec['value'],
                                               'path': spec.get('path', ''),
                                               'command': spec.get('command', '')}}
            code, t = self._create_task('cond-%s' % metric,
                                        self.base_task('%scond-%s' % (PREFIX, metric),
                                                       [node_a], condition=cond))
            if code != 200:
                metric_bad[metric] = {'http': code, 'body': t}
                continue
            got = self.task(t['id'])
            metric_ok[metric] = {'id': t['id'], 'http': code,
                                 'stored_local': (got.get('condition') or {}).get('local'),
                                 'roundtrip_ok': ((got.get('condition') or {}).get('local') or {}).get('metric') == metric}
        out['local_metrics_accepted'] = metric_ok
        out['local_metrics_rejected'] = metric_bad
        op_ok, op_neg = {}, {}
        for op in OPERATORS:
            cond = {'type': 'local', 'local': {'metric': 'cpu_usage', 'operator': op,
                                               'value': '5', 'path': '', 'command': ''}}
            code, t = self._create_task('cond-op-%s' % op.replace('=', 'eq').replace('<', 'lt').replace('>', 'gt'),
                                        self.base_task('%scond-op-%s' % (PREFIX, op.replace('=', 'eq')),
                                                       [node_a], condition=cond))
            if code == 200:
                got = self.task(t['id'])
                op_ok[op] = {'id': t['id'], 'stored_operator': ((got.get('condition') or {}).get('local') or {}).get('operator')}
            else:
                op_neg[op] = {'http': code, 'body': t}
        out['local_operators_accepted'] = op_ok
        out['local_operators_rejected'] = op_neg
        neg = {}
        for name, cond in (
                ('unknown_metric', {'type': 'local', 'local': {'metric': 'bogus', 'operator': '==', 'value': '1'}}),
                ('invalid_operator', {'type': 'local', 'local': {'metric': 'cpu_usage', 'operator': '~=', 'value': '1'}}),
                ('non_numeric_cpu', {'type': 'local', 'local': {'metric': 'cpu_usage', 'operator': '>', 'value': 'abc'}}),
                ('missing_path', {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==', 'value': 'true'}}),
                ('bad_port', {'type': 'local', 'local': {'metric': 'port_listening', 'operator': '==', 'value': 'true', 'path': '99999'}}),
                ('missing_command', {'type': 'local', 'local': {'metric': 'command_result', 'operator': '==', 'value': '0'}}),
                ('empty_type', {'type': 'bogus', 'local': None}),
                ('and_empty', {'type': 'and', 'and': []})):
            code, t = self._create_task('neg-' + name, self.base_task('%sneg-%s' % (PREFIX, name), [node_a], condition=cond))
            neg[name] = {'http': code, 'body': t if code != 200 else t.get('id')}
        out['local_negative_validation'] = neg
        # ---- API: remote conditions
        rem = {}
        code, t = self._create_task('remote-online', self.base_task(
            PREFIX + 'cond-remote-online', [node_a],
            condition={'type': 'remote', 'remote': {'node_id': node_a, 'property': 'online',
                                                    'task_id': '', 'operator': '==', 'value': 'online'}}))
        rem['online_eq'] = {'http': code, 'id': t.get('id')}
        code, t2 = self._create_task('remote-lastexec', self.base_task(
            PREFIX + 'cond-remote-lastexec', [node_a],
            condition={'type': 'remote', 'remote': {'node_id': node_a, 'property': 'last_execution',
                                                    'task_id': self.state['tasks'].get('cond-cpu_usage', ''),
                                                    'operator': '==', 'value': 'SUCCESS'}}))
        rem['last_execution'] = {'http': code, 'id': t2.get('id')}
        for name, cond in (
                ('last_execution_missing_task_id', {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'last_execution', 'task_id': '', 'operator': '==', 'value': 'SUCCESS'}}),
                ('last_execution_unknown_task', {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'last_execution', 'task_id': '00000000-0000-0000-0000-000000000000', 'operator': '==', 'value': 'SUCCESS'}}),
                ('unknown_node', {'type': 'remote', 'remote': {'node_id': '00000000-0000-0000-0000-000000000000', 'property': 'online', 'task_id': '', 'operator': '==', 'value': 'online'}}),
                ('missing_node_id', {'type': 'remote', 'remote': {'node_id': '', 'property': 'online', 'task_id': '', 'operator': '==', 'value': 'online'}}),
                ('bad_operator', {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'online', 'task_id': '', 'operator': '>', 'value': 'online'}}),
                ('bad_property', {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'bogus', 'task_id': '', 'operator': '==', 'value': 'x'}}),
                ('bad_online_value', {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'online', 'task_id': '', 'operator': '==', 'value': 'zzz'}})):
            code, resp = self._create_task('neg-' + name, self.base_task(
                '%sneg-%s' % (PREFIX, name), [node_a], condition=cond))
            rem[name] = {'http': code, 'body': resp if code != 200 else resp.get('id')}
        out['remote_conditions'] = rem
        # ---- API: AND structure
        and_children = [
            {'type': 'local', 'local': {'metric': 'file_exists', 'operator': '==', 'value': 'true', 'path': '/etc/hostname', 'command': ''}},
            {'type': 'remote', 'remote': {'node_id': node_a, 'property': 'online', 'task_id': '', 'operator': '==', 'value': 'online'}}]
        code, t = self._create_task('and-two', self.base_task(
            PREFIX + 'cond-and-two', [node_a], condition={'type': 'and', 'and': and_children}))
        stored = self.task(t['id']) if code == 200 else {}
        out['and'] = {'create_http': code, 'id': t.get('id'),
                      'stored': stored.get('condition'),
                      'stored_child_count': len((stored.get('condition') or {}).get('and') or []),
                      'stored_types': [c.get('type') for c in ((stored.get('condition') or {}).get('and') or [])]}
        code, t = self._create_task('and-nested', self.base_task(
            PREFIX + 'cond-and-nested', [node_a],
            condition={'type': 'and', 'and': [
                {'type': 'local', 'local': {'metric': 'cpu_usage', 'operator': '<', 'value': '100', 'path': '', 'command': ''}},
                {'type': 'and', 'and': [
                    {'type': 'local', 'local': {'metric': 'dir_exists', 'operator': '==', 'value': 'true', 'path': '/tmp', 'command': ''}}]}]}))
        out['and']['nested_create_http'] = code
        out['and']['nested_id'] = t.get('id')
        if code == 200:
            got = self.task(t['id'])
            out['and']['nested_stored'] = got.get('condition')
        self.dim('P09P11-17', 'and_via_api', out['and'])
        self.dim('P09P11-15', 'api_enumeration', {
            'metrics_accepted': sorted(metric_ok.keys()), 'metrics_rejected': metric_bad,
            'operators_accepted': sorted(op_ok.keys()), 'operators_rejected': op_neg,
            'negative_validation': neg}, ['evidence/phase02-conditions.json'])
        self.dim('P09P11-16', 'api_remote', rem, ['evidence/phase02-conditions.json'])
        # ---- UI: /tasks/new condition editors
        ui = {}
        self.ui_open('/tasks/new')
        self.br.wait_text('新建任务', timeout=40)
        time.sleep(1.2)
        ui['metric_select'] = self.read_options_by_pair('metric', 'cpu_usage')
        ui['local_operator_select'] = self.read_options_by_pair('operator', '>')
        ui['remote_property_select'] = self.read_options_by_pair('property', 'online')
        ui['remote_operator_select'] = self.read_options_by_pair('remote_operator', '==')
        ui['and_checkbox_labels'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=checkbox]')).map(cb=>{"
            "const l=cb.closest('label');return l?l.innerText.trim():'';}).filter(t=>t)")
        ui_task_name = PREFIX + 'ui-cond-task'
        ui['name_fill'] = self.ui_fill_labeled('名称', ui_task_name)
        ui['node_a_check'] = self.ui_set_checkbox_by_label(
            self.state['fixtures'][node_a]['name'], True)
        ui['command_fill'] = self.fill_command('/bin/echo ' + ui_task_name)
        ui['checkbox_after'] = self.ui_checkbox_states()
        # local condition: file_exists == true on an absent path -> blocking
        ui['set_local_absent'] = self.set_local_condition(
            'file_exists', '==', 'true', path='/var/tmp/' + PREFIX + 'absent')
        ui['summary_after_set'] = self.ui_condition_summary()
        self.shot('phase02-ui-local-condition-set.png')
        ui['save_1'] = self.save_task_form()
        ui['created_task'] = self.find_task_by_name(ui_task_name)
        self.shot('phase02-ui-task-list-after-create.png')
        out['ui'] = ui
        # ---- real execution: condition NOT satisfied -> blocked
        if ui['created_task']:
            tid = ui['created_task']['id']
            self.state['tasks']['ui-cond'] = tid
            self.state['fixtures'][tid] = {'kind': 'task', 'label': 'ui-cond',
                                           'name': ui_task_name}
            before = [e['id'] for e in self.executions_for(tid, node_a)]
            self.run_now(tid)
            out['exec_blocked'] = self.slim_exec(
                self.wait_execution(tid, node_a, timeout=150, since=before))
            # ---- edit -> condition echo (回显)
            back = {'edit_url': self.ui_open('/tasks/%s/edit' % tid)}
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            back['local_metric_shown'] = self.cb_text('metric')
            back['local_operator_shown'] = self.cb_text('operator')
            back['path_shown'] = self.br.evaljs(
                "(function(){const e=Array.from(document.querySelectorAll('input'))"
                ".find(x=>(x.placeholder||'').includes('路径或端口'));return e?e.value:null;})()")
            back['value_shown'] = self.br.evaljs(
                "(function(){const e=Array.from(document.querySelectorAll('input'))"
                ".find(x=>(x.placeholder||'')==='value');return e?e.value:null;})()")
            back['summary_shown'] = self.ui_condition_summary()
            back['and_checkbox_checked'] = self.br.evaljs(
                "(function(){const cb=Array.from(document.querySelectorAll('[role=checkbox]'))"
                ".find(c=>{const l=c.closest('label');return l&&l.innerText.includes('所有条件都满足');});"
                "return cb?cb.getAttribute('aria-checked'):null;})()")
            self.shot('phase02-ui-condition-echo.png')
            # override: different local condition while AND is off -> replaces
            back['override_set'] = self.set_local_condition('cpu_usage', '>', '0')
            back['override_summary'] = self.ui_condition_summary()
            back['override_save'] = self.save_task_form()
            back['stored_after_override'] = self.task(tid).get('condition')
            out['ui_echo_override'] = back
            # clear the last (only) condition -> task executes for real
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            out['clear_local_click'] = self.click_clear_button(0)
            out['summary_after_clear'] = self.ui_condition_summary()
            out['clear_save'] = self.save_task_form()
            out['stored_after_clear'] = self.task(tid).get('condition')
            before = [e['id'] for e in self.executions_for(tid, node_a)]
            self.run_now(tid)
            out['exec_after_clear'] = self.slim_exec(
                self.wait_execution(tid, node_a, timeout=150, since=before))
        # ---- AND semantics: AND + local + remote on the same task
        and_ui = {}
        tid = self.state['tasks'].get('ui-cond')
        if tid:
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            and_ui['and_check'] = self.ui_set_checkbox_by_label('所有条件都满足', True)
            and_ui['set_local'] = self.set_local_condition(
                'file_exists', '==', 'true', path='/etc/hostname')
            and_ui['set_remote'] = self.set_remote_condition(
                self.state['fixtures'][node_a]['name'], 'online', '==', 'online')
            and_ui['summary'] = self.ui_condition_summary()
            self.shot('phase02-ui-and-both-conditions.png')
            # negative: 设置远程条件 without picking a node first
            and_ui['set_remote_no_node'] = self.set_remote_condition(None, 'online', '==', 'online')
            and_ui['summary_after_failed_remote'] = self.ui_condition_summary()
            and_ui['save'] = self.save_task_form()
            and_ui['stored'] = self.task(tid).get('condition')
            before = [e['id'] for e in self.executions_for(tid, node_a)]
            self.run_now(tid)
            and_ui['exec_both_true'] = self.slim_exec(
                self.wait_execution(tid, node_a, timeout=150, since=before))
            # downgrade: clear local -> and[1] collapses to the single remote condition
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            and_ui['downgrade_click'] = self.click_clear_button(0)
            and_ui['downgrade_save'] = self.save_task_form()
            and_ui['stored_after_downgrade'] = self.task(tid).get('condition')
            # clear the remaining remote condition -> no condition at all
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            and_ui['clear_remote_click'] = self.click_clear_button(0)
            and_ui['clear_remote_save'] = self.save_task_form()
            and_ui['stored_after_clear_all'] = self.task(tid).get('condition')
        # ---- AND checked but nothing set: saved payload carries no condition
        self.ui_open('/tasks/new')
        self.br.wait_text('新建任务', timeout=40)
        time.sleep(1.2)
        and_none = {}
        and_none['name_fill'] = self.ui_fill_labeled('名称', PREFIX + 'ui-and-none')
        and_none['node_check'] = self.ui_set_checkbox_by_label(
            self.state['fixtures'][node_a]['name'], True)
        and_none['command_fill'] = self.fill_command('/bin/echo ' + PREFIX + 'ui-and-none')
        and_none['and_check'] = self.ui_set_checkbox_by_label('所有条件都满足', True)
        and_none['summary'] = self.ui_condition_summary()
        and_none['save'] = self.save_task_form()
        t = self.find_task_by_name(PREFIX + 'ui-and-none')
        and_none['created'] = t
        and_none['stored_condition'] = self.task(t['id']).get('condition') if t else None
        if t:
            self.state['tasks']['ui-and-none'] = t['id']
            self.state['fixtures'][t['id']] = {'kind': 'task', 'label': 'ui-and-none',
                                               'name': PREFIX + 'ui-and-none'}
        out['ui_and'] = and_ui
        out['ui_and_none'] = and_none
        self.dim('X11', 'ui_condition_echo_override_clear_block', {
            'created_task': ui.get('created_task'),
            'blocked_execution': out.get('exec_blocked'),
            'echo': out.get('ui_echo_override'),
            'execution_after_clear': out.get('exec_after_clear'),
            'and_structure': and_ui,
            'and_checked_nothing_set': and_none}, ['evidence/phase02-conditions.json'])
        self.dim('P09P11-15', 'ui_enumeration_and_set_clear_effect', {
            'metric_options': ui['metric_select'],
            'operator_options': ui['local_operator_select'],
            'set_clear_effect': {
                'set': ui['set_local_absent'],
                'summary_after_set': ui['summary_after_set'],
                'override': (out.get('ui_echo_override') or {}).get('override_summary'),
                'clear': out.get('summary_after_clear'),
                'blocked_execution': out.get('exec_blocked'),
                'execution_after_clear': out.get('exec_after_clear')}},
            ['evidence/phase02-conditions.json'])
        self.dim('P09P11-16', 'ui_remote_condition', {
            'property_options': ui['remote_property_select'],
            'operator_options': ui['remote_operator_select'],
            'node_pick_required': and_ui.get('set_remote_no_node'),
            'stored_and_remote': and_ui.get('stored'),
            'execution_when_online_true': and_ui.get('exec_both_true'),
            'downgrade_after_local_clear': and_ui.get('stored_after_downgrade')},
            ['evidence/phase02-conditions.json'])
        self.rec('phase02-conditions.json', out)
        self.save_state()
        return out

    # ================= reset helper (re-run safety) =================
    def reset_owned(self, kinds=('tasks',)):
        """Delete owned fixtures of the given kinds (used between re-runs)."""
        out = {}
        if 'tasks' in kinds:
            code, tasks = self.hub.get('/api/tasks')
            removed = []
            for t_ in (tasks or []):
                if (t_.get('name') or '').startswith(PREFIX):
                    dcode, _ = self.hub.delete('/api/tasks/' + self.q(t_['id']))
                    gcode, _ = self.hub.get('/api/tasks/' + self.q(t_['id']))
                    removed.append({'id': t_['id'], 'name': t_['name'],
                                    'delete_http': dcode, 'get_http': gcode})
                    self.state['tasks'].pop(t_['id'], None)
            out['tasks'] = removed
            self.state['tasks'] = {}
        if 'scripts' in kinds:
            code, scripts = self.hub.get('/api/scripts')
            removed = []
            for s in (scripts or []):
                if (s.get('name') or '').startswith(PREFIX) and '(copy)' in (s.get('name') or ''):
                    dcode, _ = self.hub.delete('/api/scripts/' + self.q(s['id']))
                    gcode, _ = self.hub.get('/api/scripts/' + self.q(s['id']))
                    removed.append({'id': s['id'], 'name': s['name'],
                                    'delete_http': dcode, 'get_http': gcode})
                    self.state['scripts'].pop('clone', None)
                    self.state['fixtures'].pop(s['id'], None)
            out['scripts'] = removed
        self.save_state()
        return out

    # ================= browser login + UI probe =================
    def phase_login(self):
        out = {'at_utc': NOW(), 'endpoint': HUB_HTTP, 'browser_session': BROWSER_SESSION}
        self.br.open(HUB_HTTP + '/sign-in')
        out['sign_in_url'] = self.br.url()
        out['sign_in_text'] = self.br.text()[:300]
        cred = self.hub.cred
        last = None
        for _ in range(20):
            last = self.br.evaljs(
                "(function(){const ins=document.querySelectorAll('input');"
                "if(ins.length<2)return 'not_ready:'+ins.length;"
                "function set(el,v){const p=Object.getPrototypeOf(el);"
                "const d=Object.getOwnPropertyDescriptor(p,'value')||"
                "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
                "d.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));}"
                "set(ins[0],%s);set(ins[1],%s);"
                "return {fields:ins.length,filled:ins[0].value!==''&&ins[1].value!==''};})()"
                % (json.dumps(cred['admin_username']), json.dumps(cred['admin_password'])))
            if isinstance(last, dict) and last.get('filled'):
                break
            self.br.wait_ms(1000)
        out['credential_injection'] = last
        out['credential_transport'] = 'stdin only (never argv, never persisted in evidence)'
        self.br.run('find', 'role', 'button', 'click', '--name', '登录')
        self.br.wait_url('**/', timeout=30)
        time.sleep(2)
        out['after_login_url'] = self.br.url()
        out['after_login_text_head'] = self.br.text()[:300]
        out['login_requests'] = self.ui_net('/api/login')
        self.rec('phase01b-ui-login.json', out)
        self.save_state()
        return out

    def probe_form(self, path, anchor_text, shot=None):
        out = {'url': self.ui_open(path)}
        self.br.wait_text(anchor_text, timeout=40)
        time.sleep(1.2)
        out['labels'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('label')).map(l=>"
            "(l.innerText||'').trim().split('\\n')[0])")
        out['comboboxes'] = self.br.evaljs(
            "(function(){return Array.from(document.querySelectorAll('[role=combobox]'))"
            ".map((c,i)=>{const l=c.closest('label');return {i:i,"
            "in_label:l?((l.innerText||'').trim().split('\\n')[0]):null,"
            "text:(c.innerText||'').trim(),"
            "placeholder:(c.querySelector('[data-placeholder]')||{}).innerText||null};});})()")
        out['inputs'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('input,textarea')).map(e=>({"
            "tag:e.tagName,type:e.type||'',placeholder:e.placeholder||'',"
            "label:((e.closest('label')||{}).innerText||'').trim().split('\\n')[0],value:(e.value||'').slice(0,40)}))")
        out['buttons'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('button')).map(b=>b.innerText.trim()).filter(t=>t)")
        out['headings'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('h1,h2,h3')).map(h=>h.innerText.trim())")
        # enumerate options for every combobox (open -> read -> Escape)
        opts = []
        n = len(out['comboboxes'])
        for i in range(n):
            try:
                res = self.br.evaljs(
                    "(function(){const cs=Array.from(document.querySelectorAll('[role=combobox]'));"
                    "const c=cs[%d];if(!c)return 'no_combo';c.scrollIntoView({block:'center'});"
                    "c.click();return 'opened';})()" % i)
                if res != 'opened':
                    opts.append({'i': i, 'error': res})
                    continue
                time.sleep(0.5)
                o = self.br.evaljs(
                    "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
                opts.append({'i': i, 'options': o})
                self.br.press('Escape')
                time.sleep(0.3)
            except Exception as exc:
                opts.append({'i': i, 'error': str(exc)[:200]})
                self.br.press('Escape')
        out['combobox_options'] = opts
        out['checkbox_states'] = self.ui_checkbox_states()
        if shot:
            self.shot(shot)
        return out

    def phase_probe(self):
        out = {'at_utc': NOW()}
        out['tasks_new'] = self.probe_form('/tasks/new', '新建任务', 'probe-tasks-new.png')
        out['scripts_new'] = self.probe_form('/scripts/new', '新建脚本', 'probe-scripts-new.png')
        self.rec('phase01c-ui-probe.json', out)
        self.save_state()
        return out


    # ---------------- combobox / form helpers ----------------
    @staticmethod
    def _cb_finder(kind):
        M = json.dumps(METRICS)
        O = json.dumps(OPERATORS)
        TYPES = json.dumps(['命令', '脚本', '托管应用部署', '托管应用操作'])
        TT = json.dumps(['节点', '分组', '标签'])
        PROPS = json.dumps(['online', '上次执行'])
        ROPS = json.dumps(['==', '!='])
        OPS = json.dumps(['部署', '启动', '停止', '重启', '升级'])
        APP_PH = json.dumps('选择托管应用...')
        base = "const cs=Array.from(document.querySelectorAll('[role=combobox]'));"
        if kind == 'metric':
            return ("(function(){" + base + "for(let i=0;i<cs.length-1;i++){"
                    "const a=(cs[i].innerText||'').trim(),b=(cs[i+1].innerText||'').trim();"
                    "if(%s.includes(a)&&%s.includes(b))return cs[i];}return null;})()" % (M, O))
        if kind == 'operator':
            return ("(function(){" + base + "for(let i=0;i<cs.length-1;i++){"
                    "const a=(cs[i].innerText||'').trim(),b=(cs[i+1].innerText||'').trim();"
                    "if(%s.includes(a)&&%s.includes(b))return cs[i+1];}return null;})()" % (M, O))
        if kind == 'property':
            return ("(function(){" + base + "for(let i=1;i<cs.length-1;i++){"
                    "const a=(cs[i].innerText||'').trim(),b=(cs[i+1].innerText||'').trim();"
                    "if(%s.includes(a)&&%s.includes(b))return cs[i];}return null;})()" % (PROPS, ROPS))
        if kind == 'remote_operator':
            return ("(function(){" + base + "for(let i=1;i<cs.length-1;i++){"
                    "const a=(cs[i].innerText||'').trim(),b=(cs[i+1].innerText||'').trim();"
                    "if(%s.includes(a)&&%s.includes(b))return cs[i+1];}return null;})()" % (PROPS, ROPS))
        if kind == 'remote_node':
            return ("(function(){" + base + "for(let i=1;i<cs.length-1;i++){"
                    "const a=(cs[i].innerText||'').trim(),b=(cs[i+1].innerText||'').trim();"
                    "if(%s.includes(a)&&%s.includes(b))return cs[i-1];}return null;})()" % (PROPS, ROPS))
        if kind == 'type':
            return ("(function(){" + base + "return cs.find(c=>%s.includes((c.innerText||'').trim()))||null;})()" % TYPES)
        if kind == 'target_type':
            return ("(function(){" + base + "return cs.find(c=>%s.includes((c.innerText||'').trim()))||null;})()" % TT)
        if kind == 'operation':
            return ("(function(){" + base + "return cs.find(c=>%s.includes((c.innerText||'').trim()))||null;})()" % OPS)
        if kind == 'app_select':
            return ("(function(){" + base + "return cs.find(c=>{const t=(c.innerText||'').trim();"
                    "return t===%s||t===%s;})||null;})()" % (APP_PH, json.dumps(APP_NAME)))
        return 'null'

    def cb_text(self, kind):
        return self.br.evaljs(
            "(function(){const c=%s;return c?(c.innerText||'').trim():null;})()"
            % self._cb_finder(kind))

    def combobox_dump(self):
        return self.br.evaljs(
            "(function(){return Array.from(document.querySelectorAll('[role=combobox]'))"
            ".map((c,i)=>{const l=c.closest('label');return {i:i,"
            "in_label:l?((l.innerText||'').trim().split('\\n')[0]):null,"
            "text:(c.innerText||'').trim()};});})()")

    def read_cb_options(self, kind):
        js = self._cb_finder(kind)
        res = self.br.evaljs(
            "(function(){const c=%s;if(!c)return 'no_combo';c.scrollIntoView({block:'center'});"
            "c.click();return 'opened';})()" % js)
        if res != 'opened':
            raise RuntimeError('read_cb_options_open_failed:%s:%s' % (kind, res))
        time.sleep(0.6)
        opts = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
        current = self.br.evaljs(
            "(function(){const c=%s;return c?(c.innerText||'').trim():null;})()" % js)
        self.br.press('Escape')
        time.sleep(0.3)
        return {'kind': kind, 'current': current, 'options': opts}

    def read_options_by_pair(self, kind, current_hint=None):
        return self.read_cb_options(kind)

    def pick_cb_option(self, kind, option):
        js = self._cb_finder(kind)
        res = self.br.evaljs(
            "(function(){const c=%s;if(!c)return 'no_combo';c.scrollIntoView({block:'center'});"
            "c.click();return 'opened';})()" % js)
        if res != 'opened':
            raise RuntimeError('pick_cb_open_failed:%s:%s' % (kind, res))
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim()===%s);if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(option))
        if clicked is None:
            opts = self.br.evaljs(
                "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
            self.br.press('Escape')
            raise RuntimeError('pick_cb_option_not_found:%s:%s have=%s' % (kind, option, opts))
        time.sleep(0.5)
        self.budget['ui_actions'] += 1
        return clicked

    def fill_placeholder(self, placeholder, value):
        js = ("(function(){const el=Array.from(document.querySelectorAll('input,textarea'))"
              ".find(e=>(e.placeholder||'')===%s);if(!el)return 'missing';"
              "const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
              % (json.dumps(placeholder), json.dumps(value)))
        return self.br.evaljs(js)

    def fill_first_value_input(self, value):
        js = ("(function(){const el=Array.from(document.querySelectorAll('input'))"
              ".find(e=>(e.placeholder||'')==='value');if(!el)return 'missing';"
              "const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
              % json.dumps(value))
        return self.br.evaljs(js)

    def fill_command(self, value):
        return self.br.evaljs(
            "(function(){const el=Array.from(document.querySelectorAll('textarea'))[0];"
            "if(!el)return 'no_textarea';const p=Object.getPrototypeOf(el);"
            "const d=Object.getOwnPropertyDescriptor(p,'value');d.set.call(el,%s);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()"
            % json.dumps(value))

    def fill_all_value_inputs(self, values):
        js = ("(function(){const els=Array.from(document.querySelectorAll('input'))"
              ".filter(e=>(e.placeholder||'')==='value');const vals=%s;"
              "els.forEach((el,i)=>{const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,vals[i]!==undefined?vals[i]:el.value);"
              "el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));});"
              "return els.map(e=>e.value);})()" % json.dumps(values))
        return self.br.evaljs(js)

    def ui_click_text_button(self, text):
        return self.br.evaljs(
            "(function(){const b=Array.from(document.querySelectorAll('button'))"
            ".find(e=>e.innerText.trim()===%s);if(!b)return 'missing';"
            "b.scrollIntoView({block:'center'});b.click();return 'clicked';})()"
            % json.dumps(text))

    def click_clear_button(self, index=0):
        res = self.br.evaljs(
            "(function(){const bs=Array.from(document.querySelectorAll('button'))"
            ".filter(e=>e.innerText.trim()==='清除');if(bs.length<=%d)return 'missing:'+bs.length;"
            "bs[%d].scrollIntoView({block:'center'});bs[%d].click();return 'clicked';})()"
            % (index, index, index))
        self.budget['ui_actions'] += 1
        time.sleep(0.5)
        return res

    def set_local_condition(self, metric, operator, value, path=None, command=None):
        out = {}
        out['metric_pick'] = self.pick_cb_option('metric', metric)
        out['operator_pick'] = self.pick_cb_option('operator', operator)
        time.sleep(0.3)
        if path is not None:
            out['path_fill'] = self.fill_placeholder('路径或端口', path)
        if command is not None:
            out['command_fill'] = self.fill_placeholder('用于评估的命令', command)
        out['value_fill'] = self.fill_first_value_input(value)
        out['set_click'] = self.ui_click_text_button('设置条件')
        time.sleep(0.5)
        out['summary'] = self.ui_condition_summary()
        return out

    def set_remote_condition(self, node_name, prop, operator, value, task_id=None):
        out = {}
        if node_name:
            out['node_pick'] = self.pick_cb_option('remote_node', node_name)
        else:
            out['node_pick'] = 'skipped(no node chosen)'
        out['property_pick'] = self.pick_cb_option('property',
                                                   '上次执行' if prop == 'last_execution' else prop)
        time.sleep(0.3)
        if task_id is not None:
            out['task_id_fill'] = self.fill_placeholder('task_id', task_id)
        out['operator_pick'] = self.pick_cb_option('remote_operator', operator)
        # the remote value input is the LAST 'value' placeholder input
        out['value_fill'] = self.br.evaljs(
            "(function(){const els=Array.from(document.querySelectorAll('input'))"
            ".filter(e=>(e.placeholder||'')==='value');if(!els.length)return 'missing';"
            "const el=els[els.length-1];const proto=Object.getPrototypeOf(el);"
            "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
            "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
            "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
            "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
            % json.dumps(value))
        out['set_click'] = self.ui_click_text_button('设置远程条件')
        time.sleep(0.5)
        out['summary'] = self.ui_condition_summary()
        return out

    def save_task_form(self):
        self.budget['ui_actions'] += 1
        before = self.br.url()
        click = self.ui_click_text_button('保存')
        deadline = time.time() + 25
        after = before
        while time.time() < deadline:
            time.sleep(0.7)
            after = self.br.url()
            if after.rstrip('/').endswith('/tasks'):
                break
        body = self.br.text()
        toast = [t for t in body.splitlines() if t.strip()][-6:]
        return {'click': click, 'url_before': before, 'url_after': after,
                'saved': after.rstrip('/').endswith('/tasks'),
                'tail_text': toast[-4:],
                'requests': self.ui_net('/api/tasks')[-6:]}

    def find_task_by_name(self, name, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.budget['api_reads'] += 1
            code, tasks = self.hub.get('/api/tasks')
            if code == 200 and isinstance(tasks, list):
                for t in tasks:
                    if t.get('name') == name:
                        return t
            time.sleep(2)
        return None

    @staticmethod
    def slim_exec(e):
        if not e:
            return None
        return {'id': e.get('id'), 'task_id': e.get('task_id'), 'node_id': e.get('node_id'),
                'status': e.get('status'), 'exit_code': e.get('exit_code'),
                'block_reason': e.get('block_reason'), 'script_id': e.get('script_id'),
                'script_revision': e.get('script_revision'), 'offline': e.get('offline'),
                'stdout': (e.get('stdout') or '')[:200], 'stderr': (e.get('stderr') or '')[:200]}

    def slim_task(self, t):
        return {k: t.get(k) for k in ('id', 'name', 'type', 'target', 'command', 'script_id',
                                      'application_id', 'app_operation', 'condition',
                                      'revision', 'enabled', 'offline_policy')}

    def app_node_state(self, app_id, node_id):
        self.budget['api_reads'] += 1
        code, body = self.hub.get('/api/applications/%s/state' % self.q(app_id))
        if code != 200:
            return {'http': code, 'body': body}
        if isinstance(body, list):
            rows = [r for r in body if r.get('node_id') == node_id]
            return {'http': code, 'rows': rows, 'all_rows': len(body)}
        return {'http': code, 'body': body}

    def host_app(self):
        ch = self.child['a']
        return ch.app_state(self.state['apps']['main'], UNIT_NAME, BINARY, CONFIG,
                            shas=[self.state['fixtures'][self.state['artifacts']['app']]['sha256']])

    def ensure_children(self):
        """Re-create the owned child handles after a process restart (state-backed)."""
        for key in CHILD_KEYS:
            if key in self.child:
                continue
            nid = self.state['nodes'].get(key)
            if not nid:
                continue
            name = (self.state['fixtures'].get(nid) or {}).get('name') or (PREFIX + 'child-' + key)
            ip = PREFIX + 'child-%s.invalid' % key
            self.child[key] = hostlib.OwnedChild(
                NONCE, HOST_KEY, CHILD_TAGS[key], nid, self.state['agents'].get(key, ''),
                name, ip, self.hub.cred['registration_token'])
        return self.child

    def delete_owned_task_named(self, name):
        self.budget['api_reads'] += 1
        code, tasks = self.hub.get('/api/tasks')
        out = []
        for t_ in (tasks or []):
            if t_.get('name') == name:
                dcode, _ = self.mutate('DELETE', '/api/tasks/' + self.q(t_['id']))
                gcode, _ = self.hub.get('/api/tasks/' + self.q(t_['id']))
                out.append({'id': t_['id'], 'delete_http': dcode, 'get_http': gcode})
                self.state['deletes'][t_['id']] = {'kind': 'task', 'name': name,
                                                   'delete_http': dcode, 'get_http': gcode}
                for k, v in list(self.state['tasks'].items()):
                    if v == t_['id']:
                        self.state['tasks'].pop(k)
        if out:
            self.save_state()
        return out

    def delete_owned_group_named(self, name):
        code, groups = self.hub.get('/api/groups')
        out = []
        for g in (groups or []):
            if g.get('name') == name:
                dcode, _ = self.mutate('DELETE', '/api/groups/' + self.q(g['id']))
                gcode, _ = self.hub.get('/api/groups/' + self.q(g['id']))
                out.append({'id': g['id'], 'delete_http': dcode, 'get_http': gcode})
                self.state['deletes'][g['id']] = {'kind': 'group', 'name': name,
                                                  'delete_http': dcode, 'get_http': gcode}
                for k, v in list(self.state['groups'].items()):
                    if v == g['id']:
                        self.state['groups'].pop(k)
        if out:
            self.save_state()
        return out

    # ================= phase 3: applications (E5-2) =================
    def phase3_apps(self):
        out = {'at_utc': NOW(), 'cases': ['P09P11-07', 'P09P11-08', 'P14P15-09']}
        self.ensure_children()
        out['rerun_cleanup'] = self.delete_owned_task_named(PREFIX + 'ui-app-deploy')
        node_a = self.state['nodes']['a']
        a_name = self.state['fixtures'][node_a]['name']
        app_id = self.state['apps']['main']
        # ---- P09P11-07 UI: app_deploy type, app select, operation enumeration
        self.ui_open('/tasks/new')
        self.br.wait_text('新建任务', timeout=40)
        time.sleep(1.2)
        ui = {}
        ui['type_options'] = self.read_cb_options('type')
        ui['pick_app_deploy'] = self.pick_cb_option('type', '托管应用部署')
        time.sleep(1.0)
        ui['comboboxes_after_type'] = self.combobox_dump()
        ui['app_options'] = self.read_cb_options('app_select')
        ui['app_pick'] = self.pick_cb_option('app_select', APP_NAME)
        time.sleep(0.6)
        ui['operation_options'] = self.read_cb_options('operation')
        ui['operation_pick'] = self.pick_cb_option('operation', '部署')
        ui['name_fill'] = self.ui_fill_labeled('名称', PREFIX + 'ui-app-deploy')
        ui['node_check'] = self.ui_set_checkbox_by_label(a_name, True)
        self.shot('phase03-ui-app-deploy-form.png')
        ui['save'] = self.save_task_form()
        t = self.find_task_by_name(PREFIX + 'ui-app-deploy')
        ui['created_task'] = t
        if t:
            self.state['tasks']['ui-app-deploy'] = t['id']
            self.state['fixtures'][t['id']] = {'kind': 'task', 'label': 'ui-app-deploy',
                                               'name': t['name']}
            ui['stored'] = self.slim_task(self.task(t['id']))
            before = [e['id'] for e in self.executions_for(t['id'], node_a)]
            ui['run_http'] = self.run_now(t['id'])[0]
            ui['exec_deploy'] = self.slim_exec(
                self.wait_execution(t['id'], node_a, timeout=240, since=before))
            # host + hub effect of the real deploy
            time.sleep(3)
            ui['app_state_after_deploy'] = self.app_node_state(app_id, node_a)
            h = self.host_app()
            ui['host_after_deploy'] = {
                'unit_file': h['unit_file'], 'unit_active': h['unit_state'].get('ActiveState'),
                'binary': h['binary'].get('sha256'), 'config_text': h['config_text'],
                'artifact_cache_entries': h.get('artifact_cache_entries'),
                'local_apps': h.get('local_apps'), 'unit_registry': h.get('unit_registry')}
            ui['artifact_sha'] = self.state['fixtures'][self.state['artifacts']['app']]['sha256']
            ui['host_binary_matches_artifact'] = ui['host_after_deploy']['binary'] == ui['artifact_sha']
            out['ui_deploy'] = ui
            self.dim('P09P11-07', 'ui_app_select_and_real_deploy', {
                'type_options': ui['type_options'],
                'app_options': ui['app_options'],
                'operation_options': ui['operation_options'],
                'created_stored': ui.get('stored'),
                'deploy_execution': ui.get('exec_deploy'),
                'app_state_after_deploy': ui.get('app_state_after_deploy'),
                'host_after_deploy': ui.get('host_after_deploy'),
                'host_binary_matches_artifact': ui.get('host_binary_matches_artifact')},
                ['evidence/phase03-apps.json'])
            # ---- P09P11-08: four operations through the UI + real execution
            ops = {}
            for op_label, op_value in (('启动', 'start'), ('停止', 'stop'),
                                       ('重启', 'restart'), ('升级', 'upgrade')):
                o = {}
                self.ui_open('/tasks/%s/edit' % t['id'])
                self.br.wait_text('编辑任务', timeout=40)
                time.sleep(1.5)
                o['type_pick'] = self.pick_cb_option('type', '托管应用操作')
                time.sleep(0.8)
                o['comboboxes'] = self.combobox_dump()
                if op_value == 'start':
                    o['operation_options'] = self.read_cb_options('operation')
                o['operation_pick'] = self.pick_cb_option('operation', op_label)
                o['save'] = self.save_task_form()
                stored = self.task(t['id'])
                o['stored_type'] = stored.get('type')
                o['stored_app_operation'] = stored.get('app_operation')
                o['stored_application_id'] = stored.get('application_id')
                before = [e['id'] for e in self.executions_for(t['id'], node_a)]
                o['run_http'] = self.run_now(t['id'])[0]
                o['exec'] = self.slim_exec(
                    self.wait_execution(t['id'], node_a, timeout=240, since=before))
                time.sleep(4)
                o['app_state'] = self.app_node_state(app_id, node_a)
                h = self.host_app()
                o['host_unit_active'] = h['unit_state'].get('ActiveState')
                o['host_unit_substate'] = h['unit_state'].get('SubState')
                ops[op_value] = o
                self.shot('phase03-ui-app-op-%s.png' % op_value)
            out['ui_operations'] = ops
            self.dim('P09P11-08', 'ui_four_operations_real_execution', ops,
                     ['evidence/phase03-apps.json'])
        # ---- P14P15-09: node detail -> managed applications -> application detail
        back = {}
        back['node_url'] = self.ui_open('/nodes/%s' % node_a)
        self.br.wait_text(a_name, timeout=40)
        time.sleep(1.5)
        back['managed_section'] = self.br.evaljs(
            "(function(){const hs=Array.from(document.querySelectorAll('h2,h3,h4'));"
            "const h=hs.find(x=>x.innerText.trim()==='托管应用');if(!h)return null;"
            "let root=h;for(let i=0;i<4&&root;i++){root=root.parentElement;"
            "if(root&&root.querySelector('a[href^=\"/applications/\"]'))break;}"
            "return root?root.innerText.trim().slice(0,400):null;})()")
        back['app_links'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('a[href^=\"/applications/\"]'))"
            ".map(a=>({href:a.getAttribute('href'),text:a.innerText.trim()}))")
        click = self.br.evaljs(
            "(function(){const a=Array.from(document.querySelectorAll('a[href^=\"/applications/\"]'))"
            ".find(x=>x.innerText.trim()===%s);if(!a)return 'missing';"
            "a.scrollIntoView({block:'center'});a.click();return 'clicked';})()" % json.dumps(APP_NAME))
        time.sleep(2.5)
        back['link_click'] = click
        back['url_after_click'] = self.br.url()
        back['detail_text_head'] = self.br.text()[:400]
        self.shot('phase03-node-managed-app-link.png')
        out['ui_node_backlink'] = back
        self.dim('P14P15-09', 'node_detail_managed_app_backlink', back,
                 ['evidence/phase03-apps.json'])
        self.rec('phase03-apps.json', out)
        self.save_state()
        return out

    # ================= phase 4: task targets (E5-3) =================
    def phase4_targets(self):
        out = {'at_utc': NOW(), 'cases': ['P09P11-09', 'P09P11-12', 'X09']}
        self.ensure_children()
        out['rerun_cleanup'] = self.delete_owned_task_named(PREFIX + 'ui-target')
        node_a = self.state['nodes']['a']
        node_b = self.state['nodes']['b']
        a_name = self.state['fixtures'][node_a]['name']
        b_name = self.state['fixtures'][node_b]['name']
        name = PREFIX + 'ui-target'
        # ---- P09P11-09: single node / add second node / deselect
        steps = []
        self.ui_open('/tasks/new')
        self.br.wait_text('新建任务', timeout=40)
        time.sleep(1.2)
        s0 = {'name_fill': self.ui_fill_labeled('名称', name),
              'command_fill': self.fill_command('/bin/echo ' + name),
              'check_a': self.ui_set_checkbox_by_label(a_name, True),
              'checkboxes': self.ui_checkbox_states()}
        s0['save'] = self.save_task_form()
        t = self.find_task_by_name(name)
        s0['stored_target'] = (t or {}).get('target')
        steps.append({'step': 'single_node_a', **s0})
        if t:
            tid = t['id']
            self.state['tasks']['ui-target'] = tid
            self.state['fixtures'][tid] = {'kind': 'task', 'label': 'ui-target', 'name': name}
            # add child-b
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            s1 = {'checkboxes_before': self.ui_checkbox_states(),
                  'check_b': self.ui_set_checkbox_by_label(b_name, True),
                  'checkboxes_after': self.ui_checkbox_states()}
            s1['save'] = self.save_task_form()
            s1['stored_target'] = self.task(tid).get('target')
            steps.append({'step': 'multi_node_a_b', **s1})
            # deselect child-b
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            s2 = {'checkboxes_before': self.ui_checkbox_states(),
                  'uncheck_b': self.ui_set_checkbox_by_label(b_name, False),
                  'checkboxes_after': self.ui_checkbox_states()}
            s2['save'] = self.save_task_form()
            s2['stored_target'] = self.task(tid).get('target')
            steps.append({'step': 'deselect_b_back_to_a', **s2})
        out['target_node_steps'] = steps
        self.dim('P09P11-09', 'ui_node_target_single_multi_deselect', steps,
                 ['evidence/phase04-targets.json'])
        # ---- P09P11-12: type/target switching without stale payload fields
        tid = self.state['tasks'].get('ui-target')
        if tid:
            switch = []
            # 1) app_operation + group target
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            w1 = {'type_pick': self.pick_cb_option('type', '托管应用操作')}
            time.sleep(0.8)
            w1['app_pick'] = self.pick_cb_option('app_select', APP_NAME)
            w1['operation_pick'] = self.pick_cb_option('operation', '停止')
            w1['target_type_pick'] = self.pick_cb_option('target_type', '分组')
            time.sleep(0.8)
            w1['group_check'] = self.ui_set_checkbox_by_label(GROUP_NAME, True)
            w1['save'] = self.save_task_form()
            w1['stored'] = self.slim_task(self.task(tid))
            switch.append({'step': 'app_operation+group', **w1})
            # 2) switch to command (stale app fields must be dropped)
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            w2 = {'type_pick': self.pick_cb_option('type', '命令')}
            time.sleep(0.6)
            w2['comboboxes'] = self.combobox_dump()
            w2['command_fill'] = self.fill_command('/bin/echo switched-command')
            w2['save'] = self.save_task_form()
            w2['stored'] = self.slim_task(self.task(tid))
            switch.append({'step': 'switch_to_command', **w2})
            # 3) switch to script (script_id present, app fields still absent)
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            w3 = {'type_pick': self.pick_cb_option('type', '脚本')}
            time.sleep(0.8)
            w3['comboboxes'] = self.combobox_dump()
            w3['script_options'] = self.read_script_options()
            w3['script_pick'] = self.pick_script_option(SCRIPT_NAME)
            w3['target_type_pick'] = self.pick_cb_option('target_type', '标签')
            time.sleep(0.8)
            w3['label_key_fill'] = self.ui_fill_labeled('标签键', LABEL_KEY)
            w3['label_value_fill'] = self.ui_fill_labeled('标签值', LABEL_V_A)
            w3['save'] = self.save_task_form()
            w3['stored'] = self.slim_task(self.task(tid))
            switch.append({'step': 'switch_to_script+label', **w3})
            # 4) back to node target
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            w4 = {'target_type_pick': self.pick_cb_option('target_type', '节点')}
            time.sleep(0.8)
            w4['check_a'] = self.ui_set_checkbox_by_label(a_name, True)
            w4['save'] = self.save_task_form()
            w4['stored'] = self.slim_task(self.task(tid))
            switch.append({'step': 'back_to_node', **w4})
            out['type_target_switching'] = switch
            self.dim('P09P11-12', 'type_target_switch_write_side', switch,
                     ['evidence/phase04-targets.json'])
        # ---- X09: four-type roundtrip + target roundtrip + group roundtrip
        x09 = {'four_type_roundtrip': {}, 'target_roundtrip': {}}
        # command task (ui-target) roundtrip
        pairs = [('command', self.state['tasks'].get('ui-target'))]
        if self.state['tasks'].get('ui-app-deploy'):
            pairs.append(('app_deploy', self.state['tasks']['ui-app-deploy']))
        for label, t_id in pairs:
            if not t_id:
                continue
            before = self.task(t_id)
            self.ui_open('/tasks/%s/edit' % t_id)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            save = self.save_task_form()
            after = self.task(t_id)
            x09['four_type_roundtrip'][label] = {
                'before': self.slim_task(before), 'after': self.slim_task(after),
                'save': save,
                'hidden_fields_unchanged': all(
                    before.get(k) == after.get(k)
                    for k in ('command', 'script_id', 'application_id', 'app_operation'))}
        out['x09'] = x09
        out['manifest'] = {'node_a': node_a, 'node_b': node_b, 'task_ui_target':
                           self.state['tasks'].get('ui-target')}
        self.rec('phase04-targets.json', out)
        self.save_state()
        return out

    def pick_script_option(self, script_name):
        """The script select is the combobox whose placeholder is 选择脚本...."""
        res = self.br.evaljs(
            "(function(){const cs=Array.from(document.querySelectorAll('[role=combobox]'));"
            "const c=cs.find(x=>{const t=(x.innerText||'').trim();"
            "return t==='选择脚本...'||t===%s;});if(!c)return 'no_combo';"
            "c.scrollIntoView({block:'center'});c.click();return 'opened';})()" % json.dumps(SCRIPT_NAME))
        if res != 'opened':
            raise RuntimeError('script_select_open_failed:%s' % res)
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim()===%s);if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(script_name))
        if clicked is None:
            opts = self.br.evaljs(
                "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
            self.br.press('Escape')
            raise RuntimeError('script_option_not_found:%s have=%s' % (script_name, opts))
        time.sleep(0.5)
        return clicked

    def read_script_options(self):
        res = self.br.evaljs(
            "(function(){const cs=Array.from(document.querySelectorAll('[role=combobox]'));"
            "const c=cs.find(x=>{const t=(x.innerText||'').trim();"
            "return t==='选择脚本...'||t===%s;});if(!c)return 'no_combo';c.click();return 'opened';})()"
            % json.dumps(SCRIPT_NAME))
        if res != 'opened':
            return {'error': res}
        time.sleep(0.6)
        opts = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText.trim())")
        self.br.press('Escape')
        time.sleep(0.3)
        return {'options': opts}

    def task_request_body(self, method='PUT'):
        """Full request detail (incl. payload body) of the last /api/tasks/{id} call."""
        reqs = self.br.net_requests()
        data = (reqs or {}).get('data') or {}
        cands = []
        for r in (data.get('requests') or []):
            url = r.get('url') or ''
            if r.get('method') != method or '/api/tasks/' not in url:
                continue
            cands.append(r)
        if not cands:
            return None
        rid = cands[-1].get('id') or cands[-1].get('requestId')
        if not rid:
            return {'_no_request_id': cands[-1]}
        rc, out, err = self.br.run('network', 'request', str(rid), '--json', check=False)
        try:
            return json.loads(out)
        except Exception:
            return {'_raw': out[-1200:]}

    def extract_payload(self, detail):
        """Best-effort payload extraction from an agent-browser request detail body."""
        if not isinstance(detail, dict):
            return None
        if isinstance(detail.get('data'), dict):
            inner = self.extract_payload(detail['data'])
            if inner is not None:
                return inner
        for key in ('requestBody', 'postData', 'body', 'request_body'):
            v = detail.get(key)
            if isinstance(v, dict):
                for k2 in ('text', 'json', 'data'):
                    if isinstance(v.get(k2), (dict, str)):
                        val = v[k2]
                        if isinstance(val, str):
                            try:
                                return json.loads(val)
                            except Exception:
                                return {'_raw': val[:600]}
                        return val
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return {'_raw': v[:600]}
        return None

    def delete_owned_script_clone(self):
        code, scripts = self.hub.get('/api/scripts')
        out = []
        for s in (scripts or []):
            nm = s.get('name') or ''
            if nm.startswith(PREFIX) and nm.endswith('(copy)'):
                dcode, _ = self.mutate('DELETE', '/api/scripts/' + self.q(s['id']))
                gcode, _ = self.hub.get('/api/scripts/' + self.q(s['id']))
                out.append({'id': s['id'], 'name': nm, 'delete_http': dcode, 'get_http': gcode})
                self.state['deletes'][s['id']] = {'kind': 'script', 'name': nm,
                                                  'delete_http': dcode, 'get_http': gcode}
                self.state['scripts'].pop('clone', None)
                self.state['fixtures'].pop(s['id'], None)
        if out:
            self.save_state()
        return out

    # ================= phase 5: labels + cross-page links (E5-4) =================
    def ui_node_detail(self, node_id, anchor):
        url = self.ui_open('/nodes/%s' % node_id)
        self.br.wait_text(anchor, timeout=40)
        time.sleep(1.8)
        d = {'url': url}
        d['head_text'] = self.br.text()[:2500]
        d['labels'] = self.br.evaljs(
            "(function(){const hs=Array.from(document.querySelectorAll('h2,h3,h4'));"
            "const h=hs.find(x=>x.innerText.trim()==='标签');if(!h)return null;"
            "let root=h;for(let i=0;i<4&&root;i++){root=root.parentElement;"
            "if(root&&/标签|已添加/.test(root.innerText))break;}"
            "return root?root.innerText.trim().slice(0,600):null;})()")
        d['groups_text'] = self.br.evaljs(
            "(function(){const t=document.body.innerText;const m=t.match(/分组[\\s\\S]{0,200}/);"
            "return m?m[0]:null;})()")
        d['task_rows'] = self.br.evaljs(
            "(function(){const links=Array.from(document.querySelectorAll('a[href^=\"/tasks/\"]'));"
            "return links.map(a=>a.innerText.trim());})()")
        d['app_links'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('a[href^=\"/applications/\"]'))"
            ".map(a=>a.innerText.trim())")
        d['checkbox_states'] = self.ui_checkbox_states()
        return d

    def phase5_labels(self):
        out = {'at_utc': NOW(), 'cases': ['P09P11-11', 'FL-02', 'FL-03', 'FL-04']}
        self.ensure_children()
        out['rerun_cleanup'] = {
            'task': self.delete_owned_task_named(PREFIX + 'ui-label-task'),
            'group': self.delete_owned_group_named(LABEL_GROUP_NAME),
            'script_clone': self.delete_owned_script_clone()}
        node_a = self.state['nodes']['a']
        node_b = self.state['nodes']['b']
        a_name = self.state['fixtures'][node_a]['name']
        b_name = self.state['fixtures'][node_b]['name']
        # ---- write label on owned children (owned targets only)
        lab = {}
        lab['set_a'] = self.mutate('POST', '/api/nodes/' + self.q(node_a),
                                   {'labels': {LABEL_KEY: LABEL_V_A}})
        lab['node_a_labels'] = self.node_labels(node_a)
        lab['set_b'] = self.mutate('POST', '/api/nodes/' + self.q(node_b),
                                   {'labels': {'g5role': LABEL_V_B}})
        lab['node_b_labels'] = self.node_labels(node_b)
        # ---- label group
        self.budget['api_mutations'] += 1
        code, g = self.hub.post('/api/groups', {'name': LABEL_GROUP_NAME, 'type': 'label',
                                                'label_key': LABEL_KEY,
                                                'label_value': LABEL_V_A,
                                                'description': 'qa-g5 label group'})
        if code != 200:
            raise RuntimeError('label_group_create_http_%s_%s' % (code, g))
        self.state['groups']['label'] = g['id']
        self.state['fixtures'][g['id']] = {'kind': 'group', 'name': LABEL_GROUP_NAME}
        lab['label_group'] = {'id': g['id'], 'create': g}
        code, g2 = self.hub.get('/api/groups/' + self.q(g['id']))
        lab['label_group_get'] = {'http': code, 'body': g2}
        out['labels'] = lab
        # ---- label task via UI
        name = PREFIX + 'ui-label-task'
        self.ui_open('/tasks/new')
        self.br.wait_text('新建任务', timeout=40)
        time.sleep(1.2)
        lt = {'name_fill': self.ui_fill_labeled('名称', name),
              'command_fill': self.fill_command('/bin/echo ' + name),
              'target_type_pick': self.pick_cb_option('target_type', '标签')}
        time.sleep(0.8)
        lt['label_key_fill'] = self.ui_fill_labeled('标签键', LABEL_KEY)
        lt['label_value_fill'] = self.ui_fill_labeled('标签值', LABEL_V_A)
        lt['save'] = self.save_task_form()
        t = self.find_task_by_name(name)
        lt['created_task'] = t
        if t:
            tid = t['id']
            self.state['tasks']['ui-label'] = tid
            self.state['fixtures'][tid] = {'kind': 'task', 'label': 'ui-label', 'name': name}
            lt['stored_target'] = (self.task(tid).get('target'))
            # run #1: only child-a carries LABEL_KEY=LABEL_V_A
            since = [e['id'] for e in self.executions_for(tid, node_a)]
            r1 = {'before': [self.slim_exec(e) for e in self.executions_for(tid)]}
            r1['run_http'] = self.run_now(tid)[0]
            ex = self.wait_execution(tid, node_a, timeout=180, since=since)
            r1['exec_a'] = self.slim_exec(ex)
            time.sleep(3)
            r1['execs_all'] = [self.slim_exec(e) for e in self.executions_for(tid)]
            r1['nodes_hit'] = sorted({e.get('node_id') for e in self.executions_for(tid)})
            lt['run_a_only'] = r1
            # ---- matching node set changes: give child-b the same label
            lt['set_b_same_label'] = self.mutate('POST', '/api/nodes/' + self.q(node_b),
                                                 {'labels': {LABEL_KEY: LABEL_V_A,
                                                             'g5role': LABEL_V_B}})
            lt['labels_after_b'] = {'a': self.node_labels(node_a), 'b': self.node_labels(node_b)}
            since = [e['id'] for e in self.executions_for(tid)]
            lt['run_http_2'] = self.run_now(tid)[0]
            ex_b = self.wait_execution(tid, node_b, timeout=180, since=since)
            time.sleep(3)
            lt['run_a_b'] = {'exec_b': self.slim_exec(ex_b),
                             'nodes_hit': sorted({e.get('node_id') for e in self.executions_for(tid)})}
            # ---- change child-a label value away -> matched set shrinks again
            lt['set_a_new_value'] = self.mutate('POST', '/api/nodes/' + self.q(node_a),
                                                {'labels': {LABEL_KEY: LABEL_V_B}})
            time.sleep(3)
            since = [e['id'] for e in self.executions_for(tid)]
            lt['run_http_3'] = self.run_now(tid)[0]
            ex2 = self.wait_execution(tid, node_b, timeout=180, since=since)
            time.sleep(4)
            all_execs = self.executions_for(tid)
            lt['run_a_changed'] = {
                'exec': self.slim_exec(ex2),
                'execs_after': [self.slim_exec(e) for e in all_execs],
                'a_executions_since_change': [self.slim_exec(e) for e in all_execs
                                              if e.get('node_id') == node_a
                                              and e['id'] not in since],
                'nodes_hit': sorted({e.get('node_id') for e in all_execs})}
            out['label_task'] = lt
            self.dim('P09P11-11', 'label_match_node_change_real_dispatch', {
                'stored_target': lt.get('stored_target'),
                'run_a_only': lt.get('run_a_only'),
                'run_a_b': lt.get('run_a_b'),
                'run_after_a_value_changed': lt.get('run_a_changed'),
                'label_writes': {'set_a': lab['set_a'], 'set_b_same': lt['set_b_same_label'],
                                 'labels_after_b': lt.get('labels_after_b'),
                                 'set_a_new_value': lt.get('set_a_new_value')}},
                ['evidence/phase05-labels.json'])
        # ---- FL-03: Label -> Label Group -> Task Target -> cross page after label change
        fl03 = {}
        fl03['node_b_detail'] = self.ui_node_detail(node_b, b_name)
        fl03['node_a_detail'] = self.ui_node_detail(node_a, a_name)
        fl03['groups_page'] = {}
        self.ui_open('/groups')
        self.br.wait_text('分组', timeout=40)
        time.sleep(1.5)
        fl03['groups_page']['rows'] = self.br.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "return rows.map(r=>r.innerText.replace(/\\n+/g,' | '));})()")
        fl03['group_detail'] = {'url': self.ui_open('/groups/%s' % self.state['groups']['label'])}
        time.sleep(2)
        fl03['group_detail']['text'] = self.br.text()[:1200]
        self.shot('phase05-ui-label-group-detail.png')
        tid = self.state['tasks'].get('ui-label')
        if tid:
            fl03['task_detail'] = {'url': self.ui_open('/tasks/%s' % tid)}
            time.sleep(2)
            fl03['task_detail']['target_text'] = self.br.evaljs(
                "(function(){const t=document.body.innerText;const i=t.indexOf('目标');"
                "return i<0?null:t.slice(i,i+300);})()")
            fl03['task_detail']['stored_target'] = self.task(tid).get('target')
        out['fl03'] = fl03
        self.dim('FL-03', 'label_group_task_cross_page', fl03,
                 ['evidence/phase05-labels.json'])
        # ---- FL-02: Agents list -> Agent Detail -> tasks filtered + backlink + target
        fl02 = {}
        fl02['agents_page'] = {'url': self.ui_open('/agents')}
        self.br.wait_text('Agent', timeout=40)
        time.sleep(1.5)
        fl02['agents_page']['rows'] = self.br.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "return rows.map(r=>r.innerText.replace(/\\n+/g,' | ')).filter(t=>t.includes('qa-g5'));})()")
        click = self.br.evaljs(
            "(function(){const a=Array.from(document.querySelectorAll('a[href^=\"/agents/\"]'))"
            ".find(x=>x.innerText.includes(%s));if(!a)return 'missing';a.click();return 'clicked';})()"
            % json.dumps(a_name))
        time.sleep(2.5)
        fl02['click_agent_link'] = click
        fl02['agent_detail'] = {'url': self.br.url()}
        fl02['agent_detail']['tasks_section'] = self.br.evaljs(
            "(function(){const t=document.body.innerText;const i=t.indexOf('任务');"
            "return i<0?null:t.slice(i,i+400);})()")
        fl02['agent_detail']['task_links'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('a[href^=\"/tasks/\"]')).map(a=>({text:a.innerText.trim(),href:a.getAttribute('href')}))")
        self.shot('phase05-ui-agent-detail-tasks.png')
        task_link = self.br.evaljs(
            "(function(){const a=Array.from(document.querySelectorAll('a[href^=\"/tasks/\"]'))"
            ".find(x=>x.innerText.includes(%s));if(!a)return null;return a.getAttribute('href');})()"
            % json.dumps(PREFIX + 'ui-target'))
        fl02['task_link_href'] = task_link
        if task_link:
            self.ui_open(task_link)
            time.sleep(2)
            fl02['task_detail_url'] = self.br.url()
            fl02['task_detail_target'] = self.br.evaljs(
                "(function(){const t=document.body.innerText;const i=t.indexOf('目标');"
                "return i<0?null:t.slice(i,i+300);})()")
            fl02['task_detail_stored_target'] = self.task(self.state['tasks']['ui-target']).get('target')
        # node detail task list consistency on both children
        fl02['node_a_tasks'] = self.ui_node_detail(node_a, a_name)['task_rows']
        fl02['node_b_tasks'] = self.ui_node_detail(node_b, b_name)['task_rows']
        out['fl02'] = fl02
        self.dim('FL-02', 'agents_detail_task_backlink_target', fl02,
                 ['evidence/phase05-labels.json'])
        # ---- FL-04: script CRUD -> task reference -> clone independence
        fl04 = {}
        sid = self.state['scripts']['main']
        fl04['initial'] = {'get': self.jget('/api/scripts/' + self.q(sid)),
                           'revisions': self.jget('/api/scripts/%s/revisions' % self.q(sid))}
        fl04['list_page'] = {'url': self.ui_open('/scripts')}
        self.br.wait_text('脚本', timeout=40)
        time.sleep(1.5)
        fl04['list_page']['rows'] = self.br.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "return rows.map(r=>r.innerText.replace(/\\n+/g,' | ')).filter(t=>t.includes('qa-g5'));})()")
        # edit the script through the UI -> revision bump + new sha
        url = self.ui_open('/scripts/%s' % sid)
        time.sleep(2.5)
        fl04['edit_page_url'] = url
        fl04['edit_page_text'] = self.br.text()[:600]
        new_content = '#!/bin/sh\necho %s-script-v2\n' % PREFIX
        fl04['content_fill'] = self.br.evaljs(
            "(function(){const el=Array.from(document.querySelectorAll('textarea'))[0];"
            "if(!el)return 'no_textarea';const p=Object.getPrototypeOf(el);"
            "const d=Object.getOwnPropertyDescriptor(p,'value');d.set.call(el,%s);"
            "el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()"
            % json.dumps(new_content))
        fl04['save_click'] = self.ui_click_text_button('保存')
        deadline = time.time() + 25
        while time.time() < deadline:
            time.sleep(0.7)
            if self.br.url().rstrip('/').endswith('/scripts'):
                break
        fl04['save_url_after'] = self.br.url()
        fl04['after_edit'] = {'get': self.jget('/api/scripts/' + self.q(sid)),
                              'revisions': self.jget('/api/scripts/%s/revisions' % self.q(sid))}
        # task referencing the script keeps the same script_id
        tsid = self.state['tasks'].get('script-task')
        if tsid:
            fl04['task_script_reference'] = self.slim_task(self.task(tsid))
        # clone through the scripts row action menu
        self.ui_open('/scripts')
        self.br.wait_text('脚本', timeout=40)
        time.sleep(1.5)
        fl04['filter_click'] = self.br.evaljs(
            "(function(){const i=Array.from(document.querySelectorAll('input')).find(e=>"
            "(e.placeholder||'').includes('搜索'));if(!i)return 'no_search';"
            "const p=Object.getPrototypeOf(i);const d=Object.getOwnPropertyDescriptor(p,'value');"
            "d.set.call(i,%s);i.dispatchEvent(new Event('input',{bubbles:true}));return 'ok';})()"
            % json.dumps(SCRIPT_NAME))
        time.sleep(1.5)
        fl04['row_menu'] = self.br.run('find', 'role', 'button', 'click', '--name', '操作',
                                       check=False)[0]
        time.sleep(1.2)
        fl04['menu_items'] = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=menuitem]')).map(e=>e.innerText.trim())")
        fl04['clone_click'] = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=menuitem]'))"
            ".find(e=>e.innerText.trim()==='克隆');if(!o)return 'missing';o.click();return 'clicked';})()")
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(0.8)
            if '/scripts/' in self.br.url() and '/scripts/new' not in self.br.url():
                break
        fl04['clone_url'] = self.br.url()
        clone_id = fl04['clone_url'].rstrip('/').split('/')[-1]
        if clone_id and clone_id != 'scripts':
            fl04['clone_get'] = self.jget('/api/scripts/' + self.q(clone_id))
            if clone_id not in self.state['fixtures']:
                self.state['fixtures'][clone_id] = {'kind': 'script', 'name': SCRIPT_NAME + ' (copy)'}
            self.state['scripts']['clone'] = clone_id
            # independence: edit the clone, original must not change
            orig_before = self.jget('/api/scripts/' + self.q(sid))
            fl04['clone_content_fill'] = self.br.evaljs(
                "(function(){const el=Array.from(document.querySelectorAll('textarea'))[0];"
                "if(!el)return 'no_textarea';const p=Object.getPrototypeOf(el);"
                "const d=Object.getOwnPropertyDescriptor(p,'value');"
                "d.set.call(el,'#!/bin/sh\\necho clone-edited\\n');"
                "el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()")
            fl04['clone_save_click'] = self.ui_click_text_button('保存')
            deadline = time.time() + 25
            while time.time() < deadline:
                time.sleep(0.7)
                if self.br.url().rstrip('/').endswith('/scripts'):
                    break
            fl04['clone_after_edit'] = self.jget('/api/scripts/' + self.q(clone_id))
            fl04['orig_after_clone_edit'] = self.jget('/api/scripts/' + self.q(sid))
            fl04['independence'] = {
                'orig_content_unchanged': fl04['orig_after_clone_edit'].get('content')
                                          == orig_before.get('content'),
                'orig_sha_unchanged': fl04['orig_after_clone_edit'].get('sha256')
                                      == orig_before.get('sha256'),
                'orig_revision_unchanged': fl04['orig_after_clone_edit'].get('revision')
                                           == orig_before.get('revision'),
                'clone_content_differs': fl04['clone_after_edit'].get('content')
                                         != fl04['orig_after_clone_edit'].get('content')}
            if tsid:
                fl04['task_reference_after_clone_edit'] = self.slim_task(self.task(tsid))
        self.shot('phase05-ui-script-clone.png')
        out['fl04'] = fl04
        self.dim('FL-04', 'script_crud_task_reference_clone', fl04,
                 ['evidence/phase05-labels.json'])
        self.rec('phase05-labels.json', out)
        self.save_state()
        return out


    # ================= phase 5b: X09 four-type roundtrip + stale field execution ==========
    def phase_x09exec(self):
        out = {'at_utc': NOW(), 'cases': ['X09', 'P09P11-12']}
        self.ensure_children()
        node_a = self.state['nodes']['a']
        node_b = self.state['nodes']['b']
        b_name = self.state['fixtures'][node_b]['name']
        app_id = self.state['apps']['main']
        sid = self.state['scripts']['main']
        # ---- four task types created via API, then roundtrip-saved through the real UI
        defs = {
            'command': self.base_task(PREFIX + 'x09-command', [node_a]),
            'script': self.base_task(PREFIX + 'x09-script', [node_a], type='script', script_id=sid),
            'app_deploy': self.base_task(PREFIX + 'x09-app-deploy', [node_a], type='app_deploy',
                                         application_id=app_id, app_operation='deploy'),
            'app_operation': self.base_task(PREFIX + 'x09-app-op', [node_b], type='app_operation',
                                            application_id=app_id, app_operation='start'),
        }
        four = {}
        for typ, body in defs.items():
            self.budget['api_mutations'] += 1
            self.budget['task_creates'] += 1
            code, created = self.hub.post('/api/tasks', body)
            if code != 200:
                four[typ] = {'create_http': code, 'body': created}
                continue
            tid = created['id']
            self.state['tasks']['x09-' + typ] = tid
            self.state['fixtures'][tid] = {'kind': 'task', 'label': 'x09-' + typ,
                                           'name': body['name']}
            rec = {'id': tid, 'create_http': code,
                   'api_get_after_create': self.slim_task(self.task(tid))}
            # real UI roundtrip: open editor, save unchanged
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            rec['ui_echo'] = {
                'type_text': self.cb_text('type'),
                'command_textarea': self.br.evaljs(
                    "(function(){const el=document.querySelector('textarea');return el?el.value:null;})()"),
                'target_type_text': self.cb_text('target_type'),
                'label_key_value': self.br.evaljs(
                    "(function(){const l=Array.from(document.querySelectorAll('label'))"
                    ".find(x=>(x.innerText||'').trim().startsWith('标签键'));if(!l)return null;"
                    "const i=l.querySelector('input');return i?i.value:null;})()"),
                'checkbox_states': self.ui_checkbox_states()}
            rec['save'] = self.save_task_form()
            rec['put_payload'] = self.extract_payload(self.task_request_body('PUT'))
            rec['api_get_after_roundtrip'] = self.slim_task(self.task(tid))
            a, b = rec['api_get_after_create'], rec['api_get_after_roundtrip']
            rec['hidden_fields_unchanged'] = all(
                a.get(k) == b.get(k) for k in ('command', 'script_id', 'application_id',
                                               'app_operation', 'target', 'type'))
            four[typ] = rec
        out['four_type_roundtrip_ui'] = four
        # ---- target roundtrip node->group->label->node is in phase04-type_target_switching;
        #      here: prove stale target fields do not change dispatch behaviour.
        tid = self.state['tasks'].get('ui-target')
        stale = {}
        if tid:
            self.ui_open('/tasks/%s/edit' % tid)
            self.br.wait_text('编辑任务', timeout=40)
            time.sleep(1.5)
            stale['before'] = self.slim_task(self.task(tid))
            stale['target_type_pick'] = self.pick_cb_option('target_type', '节点')
            time.sleep(0.8)
            stale['uncheck_a'] = self.ui_set_checkbox_by_label(
                self.state['fixtures'][node_a]['name'], False)
            stale['check_b'] = self.ui_set_checkbox_by_label(b_name, True)
            stale['save'] = self.save_task_form()
            stale['stored_after'] = self.slim_task(self.task(tid))
            stale['put_payload'] = self.extract_payload(self.task_request_body('PUT'))
            # real execution: only the node target must be dispatched
            before_a = [e['id'] for e in self.executions_for(tid, node_a)]
            before_b = [e['id'] for e in self.executions_for(tid, node_b)]
            self.run_now(tid)
            ex_b = self.wait_execution(tid, node_b, timeout=180, since=before_b)
            time.sleep(5)
            after_a = [e['id'] for e in self.executions_for(tid, node_a)]
            stale['exec'] = self.slim_exec(ex_b)
            stale['executions_on_child_a_added'] = sorted(set(after_a) - set(before_a))
            stale['stale_group_ids_present'] = (stale['stored_after'].get('target') or {}).get('group_ids')
            stale['stale_label_fields_present'] = {
                'label_key': (stale['stored_after'].get('target') or {}).get('label_key'),
                'label_value': (stale['stored_after'].get('target') or {}).get('label_value')}
            stale['static_group_members'] = self.jget('/api/groups/' + self.q(self.state['groups']['static'])).get('members')
            stale['dispatch_honoured_node_target_only'] = not stale['executions_on_child_a_added']
            # no new application deployment from the stale application_id
            h = self.host_app()
            stale['app_state_after'] = self.app_node_state(app_id, node_a)
            stale['deployments_rows'] = [(r['id'], r['phase'], r['to_version'])
                                         for r in (h.get('deployments') or [])][:6]
        out['stale_target_dispatch'] = stale
        self.dim('X09', 'four_type_roundtrip_ui_and_hidden_fields', four,
                 ['evidence/phase05b-x09.json'])
        self.dim('X09', 'stale_hidden_fields_do_not_change_behaviour', {
            'stored_after': (stale or {}).get('stored_after'),
            'execution': (stale or {}).get('exec'),
            'executions_on_child_a_added': (stale or {}).get('executions_on_child_a_added'),
            'fixed_put_payload': (stale or {}).get('put_payload'),
            'static_group_members': (stale or {}).get('static_group_members')},
            ['evidence/phase05b-x09.json'])
        self.dim('P09P11-12', 'stale_field_write_side_payload', {
            'put_payload': (stale or {}).get('put_payload'),
            'stored_after': (stale or {}).get('stored_after')},
            ['evidence/phase05b-x09.json'])
        self.rec('phase05b-x09.json', out)
        self.save_state()
        return out


    # ================= phase 5c: captured write-side payloads =================
    def phase_payload(self):
        out = {'at_utc': NOW(), 'note': 'request payloads captured from the browser '
                                         'network log (postData) of the real UI saves'}
        reqs = self.br.net_requests()
        rs = [r for r in ((reqs or {}).get('data') or {}).get('requests') or []
              if '/api/tasks' in (r.get('url') or '')
              and r.get('method') in ('POST', 'PUT')]
        caps = []
        for r in rs[-12:]:
            rid = r.get('requestId') or r.get('id')
            if not rid:
                continue
            rc, o, e = self.br.run('network', 'request', str(rid), '--json', check=False)
            try:
                detail = json.loads(o)
            except Exception:
                detail = {'_raw': o[-300:]}
            payload = self.extract_payload(detail)
            caps.append({'method': r.get('method'), 'url': r.get('url'),
                         'status': r.get('status'), 'payload': payload})
        out['captured'] = caps
        tid = self.state['tasks'].get('ui-target')
        for c in reversed(caps):
            if c.get('method') == 'PUT' and tid and tid in (c.get('url') or ''):
                out['ui_target_final_put'] = c['payload']
                break
        self.dim('P09P11-12', 'captured_final_put_payload', out['ui_target_final_put'],
                 ['evidence/phase05c-payloads.json'])
        self.dim('X09', 'captured_put_payload', out['ui_target_final_put'],
                 ['evidence/phase05c-payloads.json'])
        self.rec('phase05c-payloads.json', out)
        self.save_state()
        return out

    # ================= phase 6: cleanup + oracle =================
    def phase6_cleanup(self):
        self.ensure_children()
        res = {'at_utc': NOW(), 'steps': {}, 'errors': []}

        def step(name, fn):
            try:
                res['steps'][name] = fn()
            except Exception as exc:
                res['steps'][name] = {'error': str(exc)[:400]}
                res['errors'].append('%s: %s' % (name, exc))
        # 1) product-path host sweep for the owned application unit/binary/config
        def host_sweep():
            return self.child['a'].unit_cleanup(UNIT_NAME, BINARY, CONFIG,
                                                self.child['a'].opt_root)
        step('host_app_residue_sweep', host_sweep)
        # 2) tasks first (release application/script references)
        def delete_tasks():
            out = []
            for label, tid in list(self.state['tasks'].items()):
                code, body = self.mutate('DELETE', '/api/tasks/' + self.q(tid))
                gcode, _ = self.hub.get('/api/tasks/' + self.q(tid))
                out.append({'label': label, 'id': tid, 'delete_http': code,
                            'get_http': gcode, 'body': body if code != 200 else None})
                self.state['deletes'][tid] = {'kind': 'task', 'label': label,
                                              'delete_http': code, 'get_http': gcode}
            return out
        step('task_deletes', delete_tasks)
        # 3) groups
        def delete_groups():
            out = []
            for label, gid in list(self.state['groups'].items()):
                code, body = self.mutate('DELETE', '/api/groups/' + self.q(gid))
                gcode, _ = self.hub.get('/api/groups/' + self.q(gid))
                out.append({'label': label, 'id': gid, 'delete_http': code, 'get_http': gcode})
                self.state['deletes'][gid] = {'kind': 'group', 'label': label,
                                              'delete_http': code, 'get_http': gcode}
            return out
        step('group_deletes', delete_groups)
        # 4) scripts (original + clone)
        def delete_scripts():
            out = []
            for label, sid in list(self.state['scripts'].items()):
                code, body = self.mutate('DELETE', '/api/scripts/' + self.q(sid))
                gcode, _ = self.hub.get('/api/scripts/' + self.q(sid))
                out.append({'label': label, 'id': sid, 'delete_http': code,
                            'get_http': gcode, 'body': body if code != 200 else None})
                self.state['deletes'][sid] = {'kind': 'script', 'label': label,
                                              'delete_http': code, 'get_http': gcode}
            return out
        step('script_deletes', delete_scripts)
        # 5) application (product path -> Agent CleanupApplication)
        def delete_app():
            app_id = self.state['apps']['main']
            code, body = self.mutate('DELETE', '/api/applications/' + self.q(app_id))
            deadline = time.time() + 90
            gcode = 0
            while time.time() < deadline:
                gcode, _ = self.hub.get('/api/applications/' + self.q(app_id))
                if gcode == 404:
                    break
                time.sleep(2)
            self.state['deletes'][app_id] = {'kind': 'application', 'delete_http': code,
                                             'get_http': gcode}
            h = self.host_app()
            return {'delete_http': code, 'get_http_after': gcode,
                    'host_after_product_cleanup': {
                        'unit_file_lexists': h['unit_file'].get('lexists'),
                        'binary_lexists': h['binary'].get('lexists'),
                        'config_lexists': h['config'].get('lexists'),
                        'unit_registry': h.get('unit_registry'),
                        'local_apps': h.get('local_apps'),
                        'unit_active': h['unit_state'].get('ActiveState')}}
        step('application_delete', delete_app)
        # 6) artifact
        def delete_artifact():
            art = self.state['artifacts']['app']
            code, body = self.mutate('DELETE', '/api/artifacts/' + self.q(art))
            gcode, _ = self.hub.get('/api/artifacts/' + self.q(art))
            self.state['deletes'][art] = {'kind': 'artifact', 'delete_http': code,
                                          'get_http': gcode}
            return {'id': art, 'delete_http': code, 'get_http': gcode}
        step('artifact_delete', delete_artifact)
        # 7) owned nodes: delete through the product API, then stop the owned process
        def delete_nodes():
            out = {}
            for key in ('b', 'a'):
                nid = self.state['nodes'].get(key)
                if not nid:
                    continue
                code, body = self.mutate('DELETE', '/api/nodes/' + self.q(nid))
                gcode, _ = self.hub.get('/api/nodes/' + self.q(nid))
                out[key] = {'id': nid, 'delete_http': code, 'get_http': gcode,
                            'body': body if code != 200 else None}
                self.state['deletes'][nid] = {'kind': 'node', 'key': key,
                                              'delete_http': code, 'get_http': gcode}
            return out
        step('node_deletes', delete_nodes)

        def child_removal():
            out = {}
            for key in ('b', 'a'):
                if key in self.child:
                    out[key] = self.child[key].remove()
            return out
        step('owned_children_removal', child_removal)
        res['get_http_by_id'] = {k: v['get_http'] for k, v in self.state['deletes'].items()}
        res['all_owned_deletes_get_404'] = all(v['get_http'] == 404
                                               for v in self.state['deletes'].values())
        res['deletes_not_404'] = {k: v for k, v in self.state['deletes'].items()
                                  if v['get_http'] != 404}
        res['budget'] = dict(self.budget)
        self.rec('phase06-cleanup.json', res)
        self.save_state()
        return res

    def phase7_oracle(self):
        self.ensure_children()
        out = {'at_utc': NOW()}
        counts = {}
        for ep, key in (('/api/nodes', 'nodes'), ('/api/tasks', 'tasks'),
                        ('/api/scripts', 'scripts'), ('/api/groups', 'groups'),
                        ('/api/applications', 'applications'),
                        ('/api/artifacts', 'artifacts')):
            code, body = self.hub.get(ep)
            counts[key] = {'http': code, 'count': len(body) if isinstance(body, list) else None,
                           'owned_left': [i for i in (body or [])
                                          if (i.get('name') or i.get('hostname') or '')
                                          .startswith(PREFIX)]}
        out['final_counts'] = counts
        base = self.state.get('baseline_counts', {})
        out['baseline_counts'] = base
        out['counts_match_baseline'] = all(
            counts[k]['count'] == (base.get(k) or {}).get('count')
            for k in ('nodes', 'tasks', 'scripts', 'groups', 'applications', 'artifacts'))
        out['owned_left_total'] = sum(len(v['owned_left']) for v in counts.values())
        nodes = self.nodes()
        by_id = {n['id']: n for n in nodes}
        out['protected_after'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            out['protected_after'][label] = {'status': n.get('status') if n else None,
                                             'sync_status': n.get('sync_status') if n else None}
        out['protected_all_online_synced'] = all(
            v['status'] == 'online' and v['sync_status'] == 'synced'
            for v in out['protected_after'].values())
        hb = hostlib.OwnedChild(NONCE, HOST_KEY).host_baseline()
        out['host_after'] = {k: hb.get(k) for k in
                             ('root_exists', 'opt_root_exists', 'child_config_exists',
                              'owned_units', 'etc_systemd_owned', 'wants_owned',
                              'owned_mounts', 'owned_child_processes',
                              'global_owned_prefix_scan', 'agent_binary_sha256')}
        out['host_clean'] = not any([hb.get('root_exists'), hb.get('opt_root_exists'),
                                     hb.get('owned_units'), hb.get('etc_systemd_owned'),
                                     hb.get('owned_child_processes'),
                                     hb.get('global_owned_prefix_scan')])
        h = self.host_app()
        out['app_unit_after_cleanup'] = {
            'unit_file_lexists': h['unit_file'].get('lexists'),
            'binary_lexists': h['binary'].get('lexists'),
            'config_lexists': h['config'].get('lexists'),
            'unit_registry': h.get('unit_registry'),
            'local_apps': h.get('local_apps')}
        code, _ = self.hub.get('/healthz')
        out['repair_healthz'] = code
        out['repair_endpoint_untouched'] = code == 200
        out['executions_created_total'] = len(self.budget['executions_created_by_this_batch'])
        out['execution_ids'] = self.budget['executions_created_by_this_batch']
        self.rec('phase07-oracle.json', out)
        self.save_state()
        return out

    # ================= dispatch table =================
    PHASES = {
        'guard': 'phase0_guard',
        'fixtures': 'phase1_fixtures',
        'login': 'phase_login',
        'probe': 'phase_probe',
        'conditions': 'phase2_conditions',
        'apps': 'phase3_apps',
        'targets': 'phase4_targets',
        'labels': 'phase5_labels',
        'x09exec': 'phase_x09exec',
        'payload': 'phase_payload',
        'cleanup': 'phase6_cleanup',
        'oracle': 'phase7_oracle',
        'reset': 'reset_owned',
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('phase')
    ap.add_argument('--headless', action='store_true', default=True)
    args = ap.parse_args()
    br = browselib.Browser(BROWSER_SESSION)
    run = Run(br)
    fn = getattr(run, Run.PHASES[args.phase])
    out = fn()
    run.save_state()
    print(json.dumps({'phase': args.phase, 'ok': True,
                      'keys': list(out.keys())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
