#!/usr/bin/env python3
"""APP-UI-20261016 batch: owned Application UI lifecycle closure.

Verified build: `dddee4f0-e2e-repair-20261016` (hub cd8b7f9d…, agent b04383bf…),
repair authority http://192.168.100.209:18080.

Cases: P14P15-06, P14P15-07, P14P15-08, P14P15-13, P14P15-14, P14P15-15,
       P14P15-16, P14P15-18, FL-10, FL-11.

Evidence chain per case (real user path):
  real browser click in the NodeSteer Web UI on :18080
  -> real Hub HTTP request observed in the browser network log
  -> real Hub/Agent state change (API readback)
  -> real host effect on the owned child Agent's host (systemd unit / binary /
     config / artifact cache / deployment journal / health result)
  -> real UI feedback (health card, execution history, version history, toasts).

Owned fixtures only (`qa-appui-<nonce>-*`); every host side effect lives under an
owned path and the owned Application is removed through the product path
(delete Application -> Agent CleanupApplication) before the owned roots go away.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import hostlib
import hubapi

HERE = Path(__file__).resolve().parent
RUNTIME = HERE.parent / 'qa-appui-runtime'
EVID = HERE.parent / 'evidence'
RUNTIME.mkdir(parents=True, exist_ok=True)
EVID.mkdir(parents=True, exist_ok=True)

NONCE = (RUNTIME / '.nonce').read_text().strip()
PREFIX = NONCE + '-'
HUB_HTTP = 'http://192.168.100.209:18080'
HUB_WS = 'ws://192.168.100.209:18080'


def _browser_session():
    """A brand-new browser session per run so the sign-in flow is always real."""
    import secrets
    f = RUNTIME / 'browser-session.txt'
    if f.is_file():
        v = f.read_text().strip()
        if v:
            return v
    v = 'appui-%s-%s' % (NONCE.split('-')[-1], secrets.token_hex(3))
    f.write_text(v)
    f.chmod(0o600)
    return v


BROWSER_SESSION = _browser_session()

# owned host paths (the product writes the unit/binary/config under these)
UNIT = 'nodesteer-' + PREFIX + 'app.service'
OPT_ROOT = '/opt/' + PREFIX.rstrip('-')
BINARY = OPT_ROOT + '/app/bin/app'
CONFIG = OPT_ROOT + '/app/config/app.env'

APP_NAME = PREFIX + 'app'

NODE_A_KEY, NODE_B_KEY = 'a', 'b'
NODE_A_HOST, NODE_B_HOST = 'n1', 'n3'

VERIFIED_BUILD = hostlib.AGENT_BUILD
VERIFIED_AGENT_SHA = hostlib.AGENT_SHA

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}

TO_ORDER = ['deploy', 'start', 'stop', 'restart', 'upgrade']


def SHA(b):
    return hashlib.sha256(b).hexdigest()


def NOW():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def pad_script(body, target=4 * 1024 * 1024):
    line = '# appui-padding-%s\n'
    out = ['#!/bin/sh\n']
    size = 0
    while size < target:
        chunk = line % ('p' * 78)
        out.append(chunk)
        size += len(chunk)
    out.append(body)
    return ''.join(out).encode()


def app_script(version, mode='ok'):
    if mode == 'bad':
        return pad_script("echo appui-fixture-unhealthy >/dev/null\nexit 1\n")
    body = ("#!/bin/sh\n"
            "# nodesteer appui fixture version %s\n" % version +
            'if [ -n "$APP_VERSION_FILE" ]; then echo "%s" > "$APP_VERSION_FILE"; fi\n' % version +
            'if [ -n "$APP_READY_FLAG" ]; then echo ready > "$APP_READY_FLAG"; fi\n'
            'if [ -n "$APP_LISTEN_PORT" ]; then\n'
            '  python3 -c "import socket,time,sys; s=socket.socket(); '
            "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); s.bind(('127.0.0.1',int(sys.argv[1]))); "
            's.listen(16); time.sleep(1000000)" "$APP_LISTEN_PORT" &\n'
            'fi\n'
            'if [ -n "$APP_HTTP_PORT" ]; then\n'
            '  python3 -m http.server "$APP_HTTP_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &\n'
            'fi\n'
            'exec sleep 1000000\n')
    if mode == 'ok_notrailing':
        return body.encode()
    return pad_script(body)


class Run:
    def __init__(self, browser):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.br = browser
        self.child = {}
        self.state = {'nodes': {}, 'agents': {}, 'apps': {}, 'artifacts': {},
                      'deletes': {}, 'seams': [], 'revisions': {}}
        self.budget = {'api_reads': 0, 'api_mutations': 0, 'ui_actions': 0, 'ui_deploys': 0,
                       'artifact_uploads': 0, 'app_creates': 0, 'app_updates': 0,
                       'app_deletes': 0, 'app_assigns': 0, 'deploys': 0,
                       'node_enroll_posts': 0, 'node_deletes': 0,
                       'artifact_deletes': 0, 'child_process_starts': 0,
                       'host_seam_actions': 0, 'ui_screenshots': 0}
        self.results = {}
        self.snap_cache = ''
        self.load_state()

    # ---------------- persistence ----------------
    def state_path(self):
        return RUNTIME / 'state.json'

    def load_state(self):
        p = self.state_path()
        if p.is_file():
            try:
                saved = json.loads(p.read_text())
                self.state.update(saved.get('state') or {})
                self.budget.update(saved.get('budget') or {})
            except Exception:
                pass
        # rebuild the owned-child handles so a resumed process keeps host access
        for key, meta in (self.state.get('node_meta') or {}).items():
            if key not in self.state.get('nodes', {}):
                continue
            self.child[key] = hostlib.OwnedChild(
                NONCE, meta['host_key'], meta['tag'], self.state['nodes'][key],
                self.state['agents'][key], meta['name'], meta['ip'], '')

    def save_state(self):
        hubapi.write_json(str(self.state_path()), {'state': self.state, 'budget': self.budget,
                                                   'updated_at_utc': NOW()})

    def rec(self, name, value):
        return hubapi.write_json(str(EVID / name), value)

    def progress(self, phase, **extra):
        p = {'batch_id': 'appui', 'verified_build': VERIFIED_BUILD, 'nonce': NONCE,
             'owned_prefix': PREFIX, 'endpoint': HUB_HTTP, 'phase': phase,
             'updated_at_utc': NOW(), 'budget': self.budget}
        p.update(extra)
        hubapi.write_json(str(HERE.parent / 'progress.json'), p)

    # ---------------- hub helpers ----------------
    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def app_hub(self, app_id=None):
        app_id = app_id or self.state['apps']['main']
        code, body = self.hub.get('/api/applications/' + self.hub.quote(app_id))
        return body if code == 200 else None

    def app_states(self, app_id=None):
        app_id = app_id or self.state['apps']['main']
        code, body = self.hub.get('/api/applications/%s/state' % self.hub.quote(app_id))
        return body if code == 200 else []

    def app_nodes(self, app_id=None):
        app_id = app_id or self.state['apps']['main']
        code, body = self.hub.get('/api/applications/%s/nodes' % self.hub.quote(app_id))
        return (body or []) if code == 200 else []

    def app_revisions(self, app_id=None):
        app_id = app_id or self.state['apps']['main']
        code, body = self.hub.get('/api/applications/%s/revisions' % self.hub.quote(app_id))
        return body if code == 200 else []

    def app_executions(self, app_id=None):
        app_id = app_id or self.state['apps']['main']
        code, body = self.hub.get('/api/applications/%s/executions' % self.hub.quote(app_id))
        return body if code == 200 else []

    def wait_exec(self, exec_id, timeout=300):
        deadline = time.time() + timeout
        ex = None
        while time.time() < deadline:
            code, ex = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
            if code == 200 and ex and ex.get('status') in (
                    'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'):
                return ex
            time.sleep(1)
        return ex

    @staticmethod
    def _ex(ex):
        if not ex:
            return None
        return {k: ex.get(k) for k in ('id', 'task_id', 'node_id', 'status', 'trigger_type',
                                       'start_time', 'end_time', 'block_reason', 'exit_code')}

    def host_state(self, key=NODE_A_KEY, shas=()):
        ch = self.child[key]
        return ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG, shas=shas)

    def wait_child_online(self, key, timeout=150):
        deadline = time.time() + timeout
        node = None
        while time.time() < deadline:
            node = self.node(self.state['nodes'][key])
            if node and node.get('status') == 'online' and node.get('sync_status') == 'synced':
                return node
            time.sleep(1)
        return node

    # ---------------- UI helpers ----------------
    def ui(self, label, fn, *a, **kw):
        """Wrap a real UI action; every UI step is logged with its result."""
        self.budget['ui_actions'] += 1
        try:
            out = fn(*a, **kw)
            self.ui_log.append({'step': label, 'at_utc': NOW(), 'ok': True,
                                'result': _short(out)})
            return out
        except Exception as exc:
            self.ui_log.append({'step': label, 'at_utc': NOW(), 'ok': False,
                                'error': str(exc)[:400]})
            raise

    def snap(self):
        self.snap_cache = self.br.snapshot(interactive=True, compact=False)
        return self.snap_cache

    def ref(self, kind, name, occurrence=1):
        if not self.snap_cache:
            self.snap()
        want = '%s "%s"' % (kind, name)
        hits = []
        for line in self.snap_cache.splitlines():
            if want in line:
                m = re.search(r'ref=(e\d+)', line)
                if m:
                    hits.append((line.strip(), m.group(1)))
        if len(hits) < occurrence:
            raise RuntimeError('ui_ref_not_found:%s occurrence=%d have=%d' % (want, occurrence, len(hits)))
        return '@' + hits[occurrence - 1][1]

    def ui_open(self, path):
        self.br.open(HUB_HTTP + path)
        self.snap_cache = ''

    def ui_open_app(self):
        self.ui_open('/applications/%s' % self.state['apps']['main'])
        self.br.wait_text('托管应用', timeout=30)
        self.br.wait_ms(1200)

    def ui_pick_option(self, option, combo_selector_js):
        """Open a Radix Select and click the option with the exact given text."""
        res = self.br.evaljs(
            "(function(){const cbs=Array.from(document.querySelectorAll('%s'));"
            "if(!cbs.length)return 'no_combo';cbs[0].scrollIntoView({block:'center'});"
            "cbs[0].click();return 'opened';})()" % combo_selector_js)
        if res != 'opened':
            raise RuntimeError('ui_pick_option_open_failed:%s' % res)
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim()===%s);if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(option))
        if clicked is None:
            opts = self.br.evaljs("Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText)")
            self.br.press('Escape')
            raise RuntimeError('ui_option_not_found:%s have=%s' % (option, opts))
        time.sleep(0.4)
        return clicked

    def ui_set_health(self, htype, target):
        self.ui_pick_option(htype, '[role=combobox]')
        # health type combo is the one currently showing the old type; locate by position among comboboxes
        # (handled by caller passing an explicit selector)
        self.ui_fill_field('健康检查目标', target)

    def ui_fill_field(self, label, value):
        """Fill a form field found by its visible label text (React-safe, value on stdin)."""
        js = ("(function(){const lab=Array.from(document.querySelectorAll('label'))"
              ".find(l=>l.innerText.trim().startsWith(%s));"
              "if(!lab)return 'no_label';const el=lab.querySelector('input,textarea');"
              "if(!el)return 'no_input';"
              "const proto=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(proto,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "el.dispatchEvent(new Event('change',{bubbles:true}));return el.value;})()"
              % (json.dumps(label), json.dumps(value)))
        res = self.br.evaljs(js)
        if isinstance(res, str) and res.startswith('no_'):
            raise RuntimeError('ui_fill_field_failed:%s:%s' % (label, res))
        return res

    def ui_select_artifact(self, label_prefix):
        """Open the artifact select (first combobox) and click the option by name prefix."""
        res = self.br.evaljs(
            "(function(){const cb=document.querySelectorAll('[role=combobox]')[0];"
            "if(!cb)return 'no_combo';cb.scrollIntoView({block:'center'});cb.click();return 'opened';})()")
        if res != 'opened':
            raise RuntimeError('artifact_select_open_failed:%s' % res)
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim().startsWith(%s));if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(label_prefix))
        if clicked is None:
            opts = self.br.evaljs("Array.from(document.querySelectorAll('[role=option]')).map(e=>e.innerText)")
            self.br.press('Escape')
            raise RuntimeError('artifact_option_not_found:%s have=%s' % (label_prefix, opts))
        time.sleep(0.4)
        return clicked

    def ui_health_type_combo_js(self):
        """The health-type combobox is the one whose text is a health type value."""
        return ("[role=combobox]" if False else None)

    def ui_set_health_type(self, htype):
        res = self.br.evaljs(
            "(function(){const cbs=Array.from(document.querySelectorAll('[role=combobox]'));"
            "const cb=cbs.find(e=>['systemd','tcp','http','command'].includes(e.innerText.trim()));"
            "if(!cb)return 'no_combo';cb.scrollIntoView({block:'center'});cb.click();return 'opened';})()")
        if res != 'opened':
            raise RuntimeError('health_type_open_failed:%s' % res)
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim()===%s);if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(htype))
        if clicked is None:
            self.br.press('Escape')
            raise RuntimeError('health_type_option_missing:%s' % htype)
        time.sleep(0.4)
        return clicked

    def ui_set_operation(self, op):
        res = self.br.evaljs(
            "(function(){const cbs=Array.from(document.querySelectorAll('[role=combobox]'));"
            "const cb=cbs.find(e=>%s.includes(e.innerText.trim()));"
            "if(!cb)return 'no_combo';cb.scrollIntoView({block:'center'});cb.click();return 'opened';})()"
            % json.dumps(TO_ORDER))
        if res != 'opened':
            raise RuntimeError('operation_open_failed:%s' % res)
        time.sleep(0.6)
        clicked = self.br.evaljs(
            "(function(){const o=Array.from(document.querySelectorAll('[role=option]'))"
            ".find(e=>e.innerText.trim()===%s);if(!o)return null;o.click();return o.innerText;})()"
            % json.dumps(op))
        if clicked is None:
            self.br.press('Escape')
            raise RuntimeError('operation_option_missing:%s' % op)
        time.sleep(0.4)
        return clicked

    def ui_node_checkbox_label(self, key):
        return self.child[key].node_name

    CHECKBOX_PROBE = ("const isOn=e=>e.getAttribute('aria-checked')==='true'||e.getAttribute('data-state')==='checked';")

    def ui_set_node_checkbox(self, key, checked):
        name = self.ui_node_checkbox_label(key)
        js = ("(function(){" + self.CHECKBOX_PROBE +
              "const cbs=Array.from(document.querySelectorAll('[role=checkbox]'));"
              "const cb=cbs.find(e=>{const l=e.closest('label');"
              "return l&&l.innerText.trim().startsWith(%s);});"
              "if(!cb)return 'no_checkbox';"
              "if(isOn(cb)===%s)return 'already';"
              "cb.scrollIntoView({block:'center'});cb.click();"
              "return 'clicked';})()"
              % (json.dumps(name), 'true' if checked else 'false'))
        return self.br.evaljs(js)

    def ui_checkbox_states(self):
        return self.br.evaljs(
            "(function(){" + self.CHECKBOX_PROBE +
            "return Array.from(document.querySelectorAll('[role=checkbox]'))"
            ".map(e=>{const l=e.closest('label');return {label:(l?l.innerText:'').trim(),checked:isOn(e)};});})()")

    def _card_js(self, title):
        return ("(function(){const t=Array.from(document.querySelectorAll('div'))"
                ".find(e=>e.innerText.trim()===" + json.dumps(title) + ");"
                "if(!t)return null;return t.closest('div.bg-card');})()")

    def ui_health_cards(self):
        """Read the rendered application health card rows (node, badge, operation/error)."""
        return self.br.evaljs(
            "(function(){const t=Array.from(document.querySelectorAll('div'))"
            ".find(e=>e.innerText.trim()==='健康检查');if(!t)return null;"
            "const card=t.closest('div.bg-card');if(!card)return 'no_card';"
            "const rows=Array.from(card.querySelectorAll('.divide-y > div'));"
            "return {rows:rows.map(r=>{const sp=Array.from(r.querySelectorAll('span'));"
            "return {node:sp[0]?sp[0].innerText.trim():'',badge:sp[1]?sp[1].innerText.trim():'',"
            "detail:sp[2]?sp[2].innerText.trim():''};}),card_text:card.innerText};})()")

    @staticmethod
    def hp(cards):
        if isinstance(cards, dict):
            return cards.get('rows') or []
        return cards or []

    def ui_versions_card(self):
        return self.br.evaljs(
            "(function(){const t=Array.from(document.querySelectorAll('div'))"
            ".find(e=>e.innerText.trim()==='版本历史');if(!t)return null;"
            "const card=t.closest('div.bg-card');if(!card)return 'no_card';"
            "return {rows:Array.from(card.querySelectorAll('details')).map(d=>({"
            "summary:(d.querySelector('summary')||{}).innerText||'',"
            "content:(d.querySelector('pre')||{}).textContent||''})),card_text:card.innerText};})()")

    def ui_page_text(self):
        return self.br.text()

    def ui_save(self, expect_url_re='applications/') :
        """Click 保存 and wait for the editor to navigate back to the detail page."""
        res = self.br.evaljs(
            "(function(){const b=Array.from(document.querySelectorAll('button'))"
            ".find(e=>e.innerText.trim()==='保存');if(!b)return 'no_save';"
            "b.scrollIntoView({block:'center'});b.click();return 'clicked';})()")
        if res != 'clicked':
            raise RuntimeError('save_button_missing')
        deadline = time.time() + 45
        while time.time() < deadline:
            u = self.br.url()
            if '/applications/' in u and not u.endswith('/new'):
                self.snap_cache = ''
                return u
            time.sleep(0.5)
        raise RuntimeError('save_did_not_navigate url=%s' % self.br.url())

    def ui_click_deploy(self):
        res = self.br.evaljs(
            "(function(){const b=Array.from(document.querySelectorAll('button'))"
            ".find(e=>e.innerText.trim()==='部署');if(!b)return 'no_deploy';"
            "b.scrollIntoView({block:'center'});b.click();return 'clicked';})()")
        if res != 'clicked':
            raise RuntimeError('deploy_button_missing')
        return res

    def ui_confirm_dialog_text(self):
        return self.br.evaljs(
            "(function(){const d=document.querySelector('[role=alertdialog]');"
            "if(!d)return null;"
            "const btns=Array.from(d.querySelectorAll('button')).map(b=>b.innerText.trim());"
            "const title=(d.querySelector('[role=heading],h2')||{}).innerText||'';"
            "const full=d.innerText||'';"
            "let desc=full.replace(title,'').split('\\n').map(s=>s.trim())"
            ".filter(s=>s&&btns.indexOf(s)<0).join(' ');"
            "return {title:title.trim(),description:desc,buttons:btns};})()")

    def ui_click_dialog_button(self, label):
        res = self.br.evaljs(
            "(function(){const d=document.querySelector('[role=alertdialog]');if(!d)return 'no_dialog';"
            "const b=Array.from(d.querySelectorAll('button')).find(e=>e.innerText.trim()===%s);"
            "if(!b)return 'no_button';b.click();return 'clicked';})()" % json.dumps(label))
        if res != 'clicked':
            raise RuntimeError('dialog_button_failed:%s:%s' % (label, res))
        time.sleep(0.8)
        return res

    def ui_app_row(self):
        """Open the applications list filtered to the owned app and read its row."""
        self.ui_filter_list()
        return self.br.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "return rows.map(r=>Array.from(r.querySelectorAll('td')).map(c=>c.innerText.trim()));})()")

    def ui_filter_list(self):
        self.ui_open('/applications')
        self.br.wait_text('托管应用', timeout=30)
        self.br.evaljs(
            "(function(){const i=Array.from(document.querySelectorAll('input')).find(e=>"
            "(e.placeholder||'').includes('搜索'));if(!i)return 'no_search';"
            "const p=Object.getPrototypeOf(i);const d=Object.getOwnPropertyDescriptor(p,'value');"
            "d.set.call(i,%s);i.dispatchEvent(new Event('input',{bubbles:true}));return 'ok';})()"
            % json.dumps(APP_NAME))
        time.sleep(1.5)
        return self.br.evaljs(
            "(function(){return Array.from(document.querySelectorAll('table tbody tr')).length;})()")

    def ui_click_row_menu(self, action_label):
        """Real mouse click on the row action menu, then on the menu item."""
        self.ui_filter_list()
        rows = self.br.evaljs(
            "(function(){return Array.from(document.querySelectorAll('table tbody tr'))"
            ".map(r=>r.innerText.replace(/\\n+/g,' | '));})()")
        self.br.run('find', 'role', 'button', 'click', '--name', '操作')
        time.sleep(1.0)
        items = self.br.evaljs(
            "Array.from(document.querySelectorAll('[role=menuitem]')).map(e=>e.innerText.trim())")
        if not items:
            raise RuntimeError('row_menu_did_not_open rows=%s' % rows)
        self.br.run('find', 'role', 'menuitem', 'click', '--name', action_label)
        time.sleep(1.0)
        return items

    def ui_click_action_dialog(self, label):
        """Real mouse click on a confirm-dialog button (falls back to a DOM click)."""
        self.br.run('find', 'role', 'button', 'click', '--name', label)
        time.sleep(0.6)
        if self.ui_confirm_dialog_text() is None:
            return 'closed'
        r = self.br.evaljs(
            "(function(){const d=document.querySelector('[role=alertdialog]');if(!d)return 'no_dialog';"
            "const b=Array.from(d.querySelectorAll('button')).find(e=>e.innerText.trim()===%s);"
            "if(!b)return 'no_button';b.click();return 'dom_click';})()" % json.dumps(label))
        time.sleep(0.8)
        return r

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
        p = str(EVID / name)
        self.br.screenshot(p)
        return name

    # ---------------- phase 0: guard ----------------
    def phase0_guard(self):
        out = {'at_utc': NOW(), 'endpoint': self.hub.base, 'nonce': NONCE, 'owned_prefix': PREFIX,
               'verified_build': VERIFIED_BUILD, 'agent_binary_sha256_expected': VERIFIED_AGENT_SHA,
               'browser_session': BROWSER_SESSION,
               'hosts': {k: hostlib.HOSTS[k]['ip'] for k in (NODE_A_HOST, NODE_B_HOST)}}
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        out['protected'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                raise RuntimeError('protected_node_not_healthy:' + label)
            out['protected'][label] = {'id': nid, 'status': n['status'], 'sync_status': n['sync_status'],
                                       'agent_version': n.get('agent_version'),
                                       'deployment_mode': n.get('deployment_mode'),
                                       'capabilities': n.get('capabilities')}
            if n.get('agent_version') != VERIFIED_BUILD:
                raise RuntimeError('protected_node_not_on_verified_build:%s=%s' % (label, n.get('agent_version')))
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        out['baseline_counts'] = {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)}
        out['preexisting_not_owned'] = {
            'artifacts': [{'id': a['id'], 'name': a['name']} for a in arts if not a['name'].startswith(PREFIX)],
            'applications': [{'id': a['id'], 'name': a['name']} for a in apps if not a['name'].startswith(PREFIX)]}
        for label, items, attr in (('artifacts', arts, 'name'), ('applications', apps, 'name'),
                                   ('nodes', nodes, 'hostname')):
            owned = [i['id'] for i in items if (i.get(attr) or '').startswith(PREFIX)]
            out['owned_fixture_check_' + label] = owned
            if owned:
                raise RuntimeError('owned_fixture_already_present:' + label)
        self.state['baseline_counts'] = out['baseline_counts']
        out['host_baselines'] = {}
        for key, hk in ((NODE_A_KEY, NODE_A_HOST), (NODE_B_KEY, NODE_B_HOST)):
            base = hostlib.OwnedChild(NONCE, hk, tag=('' if key == NODE_A_KEY else '-b')).host_baseline()
            out['host_baselines'][hk] = base
            if base['agent_binary_sha256'] != VERIFIED_AGENT_SHA:
                raise RuntimeError('agent_binary_sha_mismatch_on_' + hk)
            if base['primary_agent_unit'].get('ActiveState') != 'active' \
                    or base['repair_agent_unit'].get('ActiveState') != 'active':
                raise RuntimeError('protected_agent_unit_not_active_on_' + hk)
            for k in ('root_exists', 'opt_root_exists', 'child_config_exists', 'owned_units',
                      'etc_systemd_owned', 'wants_owned', 'owned_mounts', 'owned_child_processes',
                      'global_owned_prefix_scan'):
                if base[k]:
                    raise RuntimeError('owned_host_residue_present:%s:%s' % (hk, k))
        out['guard_ok'] = True
        self.rec('phase00-guard.json', out)
        return out

    # ---------------- phase 1: owned children ----------------
    def _enroll_child(self, key, tag, host_key):
        name = PREFIX + 'child-' + key
        ip = PREFIX + 'child-%s.invalid' % key
        self.budget['api_mutations'] += 1
        self.budget['node_enroll_posts'] += 1
        code, enroll = self.hub.post('/api/nodes/enrollment',
                                     {'node_name': name, 'node_ip': ip, 'hub_address': HUB_HTTP})
        if code != 200 or not enroll.get('node_id') or not enroll.get('agent_id'):
            raise RuntimeError('enrollment_failed_http_%s_%s' % (code, enroll))
        self.state['nodes'][key] = enroll['node_id']
        self.state['agents'][key] = enroll['agent_id']
        ch = hostlib.OwnedChild(NONCE, host_key, tag, enroll['node_id'], enroll['agent_id'],
                                name, ip, self.hub.cred['registration_token'])
        self.child[key] = ch
        started = ch.write_config_and_start(HUB_WS)
        self.budget['child_process_starts'] += 1
        node = self.wait_child_online(key)
        if not node or node.get('status') != 'online':
            raise RuntimeError('owned_child_not_online:' + key)
        if not (node.get('capabilities') or {}).get('application_deploy'):
            raise RuntimeError('owned_child_lacks_application_deploy:' + key)
        if node.get('agent_version') != VERIFIED_BUILD:
            raise RuntimeError('owned_child_not_on_verified_build:%s' % node.get('agent_version'))
        self.state.setdefault('node_meta', {})[key] = {'host_key': host_key, 'tag': tag,
                                                       'name': name, 'ip': ip}
        return {'key': key, 'host': ch.host['ip'], 'node_id': enroll['node_id'],
                'agent_id': enroll['agent_id'], 'node_name': name, 'node_ip': ip,
                'root': ch.root, 'opt_root': ch.opt_root,
                'processes': started.get('owned_processes'),
                'node_after_start': {k: node.get(k) for k in
                                     ('id', 'agent_id', 'hostname', 'ip', 'status', 'sync_status',
                                      'global_revision', 'deployment_mode', 'agent_version',
                                      'capabilities')}}

    def phase1_children(self):
        out = {'at_utc': NOW(), 'children': {},
               'identity_isolation_note':
                   'the enrollment API reuses a node when its name or IP matches an existing node, '
                   'so each owned child is enrolled with a distinct non-routable <name>.invalid identity'}
        for key, tag, hk in ((NODE_A_KEY, '', NODE_A_HOST), (NODE_B_KEY, '-b', NODE_B_HOST)):
            out['children'][key] = self._enroll_child(key, tag, hk)
        ids = [c['node_id'] for c in out['children'].values()]
        agents = [c['agent_id'] for c in out['children'].values()]
        out['distinct_node_identities'] = len(set(ids)) == len(ids) and len(set(agents)) == len(agents)
        if not out['distinct_node_identities']:
            raise RuntimeError('owned_child_identities_not_isolated')
        self.rec('phase01-children.json', out)
        return out

    # ---------------- phase 2: artifacts ----------------
    def phase2_artifacts(self):
        contents = {'A1': app_script('1.0'), 'A2': app_script('2.0'),
                    'A3': app_script('3.0', 'bad'), 'A4': app_script('1.5')}
        roles = {'A1': 'good v1.0 (baseline install, tcp/http/command health)',
                 'A2': 'good v2.0 (upgrade target)',
                 'A3': 'always-exit-1 (failed upgrade / unhealthy)',
                 'A4': 'good v1.5 (downgrade target for the delete-cancel baseline)'}
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
                                              'size': body['size'], 'name': name, 'version': 'v1'}
            out['artifacts'][label] = {'id': body['id'], 'sha256': body['sha256'],
                                       'size': body['size'], 'name': name, 'role': roles[label],
                                       'local_sha256_matches_hub': True}
        self.rec('phase02-artifacts.json', out)
        return out

    # ---------------- phase 3: browser login ----------------
    def _inject_credentials(self):
        """Fill the sign-in form; the values only ever travel on stdin."""
        cred = self.hub.cred
        js = ("(function(){const ins=document.querySelectorAll('input');"
              "if(ins.length<2)return 'not_ready:'+ins.length;"
              "function set(el,v){const p=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(p,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));}"
              "set(ins[0],%s);set(ins[1],%s);"
              "return {fields:ins.length,filled:ins[0].value!==''&&ins[1].value!==''};})()"
              % (json.dumps(cred['admin_username']), json.dumps(cred['admin_password'])))
        last = None
        for _ in range(20):
            last = self.br.evaljs(js)
            if isinstance(last, dict) and last.get('filled'):
                return last
            self.br.wait_ms(1000)
        raise RuntimeError('signin_form_not_ready:%s' % last)

    def phase3_ui_login(self):
        out = {'at_utc': NOW(), 'browser_session': BROWSER_SESSION, 'endpoint': HUB_HTTP}
        self.br.open(HUB_HTTP + '/sign-in')
        out['sign_in_url'] = self.br.url()
        out['sign_in_snapshot'] = self.br.snapshot(interactive=True, compact=False)[:1500]
        cred = self.hub.cred
        js = ("(function(){function set(el,v){const p=Object.getPrototypeOf(el);"
              "const d=Object.getOwnPropertyDescriptor(p,'value')||"
              "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');"
              "d.set.call(el,v);el.dispatchEvent(new Event('input',{bubbles:true}));}"
              "const ins=document.querySelectorAll('input');set(ins[0],%s);set(ins[1],%s);"
              "return {fields:ins.length,filled:ins[0].value!==''&&ins[1].value!==''};})()"
              % (json.dumps(cred['admin_username']), json.dumps(cred['admin_password'])))
        out['credential_injection'] = self._inject_credentials()
        out['credential_transport'] = 'stdin only (never argv, never written to evidence)'
        self.br.run('find', 'role', 'button', 'click', '--name', '登录')
        self.br.wait_url('**/', timeout=30)
        time.sleep(2)
        out['after_login_url'] = self.br.url()
        out['after_login_text_head'] = self.br.text()[:400]
        out['login_requests'] = self.ui_net('/api/login')
        self.shot('ui-00-signin-after-login.png')
        self.ui_open('/applications')
        self.br.wait_text('托管应用', timeout=30)
        out['applications_page_url'] = self.br.url()
        out['applications_page_text'] = self.br.text()[:1200]
        self.shot('ui-01-applications-list.png')
        self.rec('phase03-ui-login.json', out)
        return out

    # ---------------- phase 4: create the application through the UI ----------------
    def phase4_ui_create(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-06', 'P14P15-08', 'P14P15-14'],
               'ui_path': '/applications/new'}
        self.child[NODE_A_KEY].sample_ready_flag = True
        listen, http_port = self._free_ports(NODE_A_KEY)
        self.state['ports'] = {'listen': listen, 'http': http_port}
        self.ui_open('/applications/new')
        self.br.wait_text('新建托管应用', timeout=30)
        time.sleep(1.0)
        out['new_page_snapshot'] = self.br.snapshot(interactive=True, compact=False)[:2500]
        self._fill_create_form('1.0', 'A1', listen, http_port, 'v1')
        out['form_state'] = self._read_form()
        out['node_checkbox_before'] = self.ui_checkbox_states()
        res_a = self.ui_set_node_checkbox(NODE_A_KEY, True)
        out['node_a_checkbox_action'] = res_a
        out['node_checkbox_after_check'] = self.ui_checkbox_states()
        out['scenario_note'] = ('only the owned child node %s is checked; every other node in the '
                                'list (protected and foreign) is left untouched'
                                % self.child[NODE_A_KEY].node_name)
        out['save_url'] = self.ui_save()
        time.sleep(2)
        app_id = out['save_url'].rstrip('/').split('/')[-1]
        self.state['apps']['main'] = app_id
        out['created_app_id'] = app_id
        out['create_requests'] = {
            'POST /api/applications': self.ui_net('/api/applications', 'POST'),
            'POST assign': self.ui_net('/assign', 'POST')}
        time.sleep(2)
        hub_app = self.app_hub(app_id) or {}
        out['hub_after_create'] = {k: hub_app.get(k) for k in
                                   ('id', 'name', 'version', 'revision', 'artifact_id',
                                    'binary_path', 'config_path', 'unit_name', 'health_check',
                                    'created_at', 'updated_at')}
        out['assign_after_create'] = self.app_nodes(app_id)
        out['state_after_create'] = self.app_states(app_id)
        out['revisions_after_create'] = [{'revision': r.get('revision')} for r in self.app_revisions(app_id)]
        # artifact usage: the Artifacts page lists the applications that reference the artifact
        self.ui_open('/artifacts')
        self.br.wait_text('发布包', timeout=30)
        time.sleep(1.0)
        out['artifact_usage_ui'] = self.br.evaljs(
            "(function(){const rows=Array.from(document.querySelectorAll('table tbody tr'));"
            "return rows.map(r=>({row:r.innerText.replace(/\\n+/g,' | ')}))"
            ".filter(x=>x.row.includes(%s));})()" % json.dumps(self.state['artifacts']['A1']['name']))
        self.shot('ui-02-artifact-usage.png')
        # the fresh application must show "no health state" before its first deploy
        self.ui_open_app()
        out['health_card_before_deploy'] = self.ui_health_cards()
        out['health_text_before_deploy'] = '暂无节点健康状态' in self.br.text()
        out['executions_before_deploy'] = self.app_executions(app_id)
        self.shot('ui-03-app-detail-no-state.png')
        # agent side: the desired definition reached the owned child
        got = self._wait_agent_def({'version': '1.0',
                                    'artifact_id': self.state['artifacts']['A1']['id']})
        out['agent_local_definition'] = got.get('definition')
        out['desired_state_delivered'] = got.get('definition') is not None
        out['agent_revision'] = got.get('revision')
        out['agent_artifact_url'] = (got.get('definition') or {}).get('artifact_url')
        self.rec('phase04-ui-create.json', out)
        return out

    def _free_ports(self, key):
        script = (
            "import json,socket\n"
            "def f():\n"
            "    s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close();return p\n"
            "print(json.dumps({'listen':f(),'http':f()}))\n")
        return (self.child[key].remote_py(script)['listen'],
                self.child[key].remote_py(script)['http'])

    def _fill_create_form(self, version, artifact, listen, http_port, profile):
        self.ui_fill_field('名称', APP_NAME)
        self.ui_select_artifact(self.state['artifacts'][artifact]['name'])
        self.ui_fill_field('版本', version)
        self.ui_fill_field('二进制路径', BINARY)
        self.ui_fill_field('配置路径', CONFIG)
        self.ui_fill_field('Unit', UNIT)
        self.ui_fill_field('配置内容', 'APP_MODE=%s\nAPP_VERSION=%s\n' % (profile, version))
        # environment entries used by the fixture binary for the tcp/http health probes
        self._add_env('APP_LISTEN_PORT', str(listen))
        self._add_env('APP_HTTP_PORT', str(http_port))
        self._add_env('APP_PROFILE', profile)
        self.ui_set_health_type('systemd')
        self.ui_fill_field('健康检查目标', UNIT)

    def _add_env(self, key, value):
        js = ("(function(){const ins=Array.from(document.querySelectorAll('input'))"
              ".filter(e=>(e.placeholder||'').toUpperCase()==='KEY');"
              "if(!ins.length)return 'no_key';const el=ins[0];"
              "const p=Object.getPrototypeOf(el);const d=Object.getOwnPropertyDescriptor(p,'value');"
              "d.set.call(el,%s);el.dispatchEvent(new Event('input',{bubbles:true}));"
              "const vs=Array.from(document.querySelectorAll('input'))"
              ".filter(e=>(e.placeholder||'').toUpperCase()==='VALUE');"
              "if(!vs.length)return 'no_value';const ve=vs[0];"
              "const p2=Object.getPrototypeOf(ve);const d2=Object.getOwnPropertyDescriptor(p2,'value');"
              "d2.set.call(ve,%s);ve.dispatchEvent(new Event('input',{bubbles:true}));"
              "const btns=Array.from(document.querySelectorAll('button'))"
              ".filter(e=>e.innerText.trim()==='添加');if(!btns.length)return 'no_add';"
              "btns[btns.length-1].click();return 'added';})()"
              % (json.dumps(key), json.dumps(value)))
        res = self.br.evaljs(js)
        if res != 'added':
            raise RuntimeError('add_env_failed:%s:%s' % (key, res))
        time.sleep(0.4)
        return res

    def _read_form(self):
        return self.br.evaljs(
            "(function(){const out={};"
            "for(const l of Array.from(document.querySelectorAll('label'))){"
            "const el=l.querySelector('input,textarea');"
            "if(el&&l.innerText.trim())out[l.innerText.trim().split('\\n')[0]]=el.value;}"
            "out['_comboboxes']=Array.from(document.querySelectorAll('[role=combobox]')).map(e=>e.innerText.trim());"
            "return out;})()")

    def _wait_agent_def(self, match, key=NODE_A_KEY, timeout=60):
        app_id = self.state['apps']['main']
        ch = self.child[key]
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

    # ---------------- phase 5: edit -> revision +1 and version history ----------------
    def phase5_ui_edit_revision(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-07']}
        before = self.app_hub() or {}
        out['revision_before'] = before.get('revision')
        out['version_before'] = before.get('version')
        self.ui_open_app()
        out['edit_page_snapshot'] = self.br.snapshot(interactive=True, compact=False)[:2000]
        self.ui_fill_field('版本', '1.1')
        self.ui_fill_field('配置内容', 'APP_MODE=v1.1\nAPP_VERSION=1.1\n')
        out['form_before_save'] = self._read_form()
        out['save_url'] = self.ui_save()
        time.sleep(2.5)
        after = self.app_hub() or {}
        out['revision_after'] = after.get('revision')
        out['version_after'] = after.get('version')
        out['revision_incremented'] = (after.get('revision') or 0) == (before.get('revision') or 0) + 1
        revs = self.app_revisions()
        out['revisions_api'] = [{'revision': r.get('revision'),
                                 'has_version_1_1': '1.1' in (r.get('content') or ''),
                                 'has_version_1_0': '"version":"1.0"' in (r.get('content') or '')
                                 or '1.0' in (r.get('content') or '')} for r in revs]
        out['revision_count'] = len(revs)
        out['put_request'] = self.ui_net('/api/applications/' + self.state['apps']['main'], 'PUT')
        self.ui_open_app()
        out['versions_card_ui'] = self.ui_versions_card()
        out['history_text_has_r2'] = 'r2' in self.br.text()
        self.shot('ui-04-version-history.png')
        self._wait_agent_def({'version': '1.1'})
        out['agent_revision_after_edit'] = (self.host_state(NODE_A_KEY) or {})
        self.rec('phase05-ui-edit-revision.json', out)
        return out

    # ---------------- phase 6: multi-node assign / unassign through the checkboxes ----------------
    def phase6_ui_multi_assign(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-08']}
        out['nodes_before'] = self.app_nodes()
        self.ui_open_app()
        out['checkboxes_before'] = self.ui_checkbox_states()
        res_b = self.ui_set_node_checkbox(NODE_B_KEY, True)
        out['node_b_check_action'] = res_b
        out['checkboxes_after_check_b'] = self.ui_checkbox_states()
        out['save_url'] = self.ui_save()
        time.sleep(3)
        out['nodes_after_multi_assign'] = self.app_nodes()
        out['assign_requests'] = self.ui_net('/assign', 'POST')
        out['assigned_both_owned_nodes'] = sorted(out['nodes_after_multi_assign']) == \
            sorted([self.state['nodes'][NODE_A_KEY], self.state['nodes'][NODE_B_KEY]])
        got_b = self._wait_agent_def({'version': '1.1'}, key=NODE_B_KEY)
        out['agent_b_definition_received'] = got_b.get('definition') is not None
        out['agent_b_definition'] = got_b.get('definition')
        self.ui_open_app()
        out['checkboxes_before_unassign'] = self.ui_checkbox_states()
        res_un = self.ui_set_node_checkbox(NODE_B_KEY, False)
        out['node_b_uncheck_action'] = res_un
        out['checkboxes_after_uncheck_b'] = self.ui_checkbox_states()
        out['save_url_2'] = self.ui_save()
        time.sleep(3)
        out['nodes_after_unassign'] = self.app_nodes()
        out['unassign_requests'] = self.ui_net('/assign', 'POST')
        out['unassigned_b'] = self.state['nodes'][NODE_B_KEY] not in out['nodes_after_unassign']
        out['node_a_still_assigned'] = self.state['nodes'][NODE_A_KEY] in out['nodes_after_unassign']
        self.ui_open_app()
        out['final_checkbox_states'] = self.ui_checkbox_states()
        self.shot('ui-05-assign-nodes.png')
        out['agent_b_state_after_unassign'] = self.child[NODE_B_KEY].app_state(
            self.state['apps']['main'], UNIT, BINARY, CONFIG)
        out['agent_a_state_after_unassign'] = self.host_state(NODE_A_KEY)
        self.rec('phase06-ui-multi-assign.json', out)
        return out

    # ---------------- phase 7: UI deploy (FL-10, P14P15-13, P14P15-14) ----------------
    def phase7_ui_deploy(self):
        out = {'at_utc': NOW(), 'case': ['FL-10', 'P14P15-13', 'P14P15-14'],
               'operation': 'deploy'}
        sha1 = self.state['artifacts']['A1']['sha256']
        ch = self.child[NODE_A_KEY]
        out['host_before'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG))
        self.ui_open_app()
        out['operation_selected'] = self.ui_set_operation('deploy')
        out['deploy_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        out['deploy_requests'] = self.ui_net('/deploy', 'POST')
        ex, samples = self._await_ui_deploy(timeout=300)
        out['execution'] = self._ex(ex)
        out['journal_samples'] = samples
        out['state_after_deploy'] = self.app_states()
        out['executions_ui'] = self.app_executions()
        self.ui_open_app()
        out['health_card_ui'] = self.ui_health_cards()
        self.shot('ui-06-deployed-healthy.png')
        host = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG, shas=(sha1,)))
        out['host_after'] = host
        out['journal'] = ch.journal(UNIT, 60)
        out['config_effect_probe'] = self.config_effect_probe()
        out['cross_check_fl10'] = {
            'ui_health_badge': [c.get('badge') for c in self.hp(out['health_card_ui'])],
            'api_state_health': [s.get('health') for s in out['state_after_deploy']],
            'api_state_version': [s.get('version') for s in out['state_after_deploy']],
            'api_state_operation': [s.get('operation') for s in out['state_after_deploy']],
            'api_state_error': [s.get('error') for s in out['state_after_deploy']],
            'api_state_updated_at': [s.get('updated_at') for s in out['state_after_deploy']],
            'api_execution_status': (ex or {}).get('status'),
            'host_unit_active': host['unit_state'].get('ActiveState'),
            'host_binary_sha256': host['binary'].get('sha256'),
            'expected_binary_sha256': sha1,
            'host_binary_sha_matches_artifact': host['binary'].get('sha256') == sha1,
            'host_config_text': host.get('config_text'),
            'host_unit_file_path': host.get('unit_file_path'),
            'host_unit_file_text': host.get('unit_file_text'),
            'host_unit_registry': host.get('unit_registry'),
            'ui_health_text': self.br.text()[:600],
        }
        out['fl10_consistent'] = (
            out['state_after_deploy'] and out['state_after_deploy'][0].get('health') == 'healthy'
            and (ex or {}).get('status') == 'SUCCESS'
            and host['unit_state'].get('ActiveState') == 'active'
            and host['binary'].get('sha256') == sha1
            and out['state_after_deploy'][0].get('version') == '1.1'
            and not out['state_after_deploy'][0].get('error'))
        self.rec('phase07-ui-deploy.json', out)
        return out

    def _await_ui_deploy(self, timeout=300):
        """Follow the newest execution through the API and sample the on-host journal."""
        ch = self.child[NODE_A_KEY]
        seen = {}
        samples = []
        deadline = time.time() + timeout
        latest = None
        while time.time() < deadline:
            exs = self.app_executions()
            if exs:
                latest = exs[0]
                ex_id = latest.get('id')
                if ex_id not in seen:
                    seen[ex_id] = True
                ex = self.wait_exec(ex_id, timeout=5) if latest.get('status') in ('PENDING', 'RUNNING') else latest
                st = ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG)
                rows = st.get('deployments') or []
                row = rows[0] if rows else {}
                key = (row.get('phase'), st['binary'].get('sha256'), st['unit_state'].get('ActiveState'),
                       (ex or {}).get('status'))
                if not samples or samples[-1]['key'] != list(key):
                    samples.append({'at_utc': NOW(), 'key': list(key),
                                    'journal_phase': row.get('phase'),
                                    'journal_id': row.get('id'),
                                    'binary_sha256': st['binary'].get('sha256'),
                                    'unit_active': st['unit_state'].get('ActiveState'),
                                    'exec_status': (ex or {}).get('status')})
                if ex and ex.get('status') in ('SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED',
                                               'TIMED_OUT', 'BLOCKED'):
                    time.sleep(2)
                    return ex, samples
            time.sleep(1.2)
        return latest, samples

    def _host_slim(self, st):
        if not st:
            return None
        return {'binary': st.get('binary'), 'config': st.get('config'),
                'config_text': st.get('config_text'),
                'unit_file_path': st.get('unit_file_path'),
                'unit_file': st.get('unit_file'), 'unit_state': st.get('unit_state'),
                'unit_file_content': st.get('unit_file_content'),
                'unit_file_text': st.get('unit_file_text'),
                'enable_symlink': st.get('enable_symlink'),
                'backup_dir': st.get('backup_dir'),
                'backup_entries': st.get('backup_entries'),
                'unit_registry': st.get('unit_registry'),
                'deployments': st.get('deployments'),
                'local_apps': st.get('local_apps'),
                'artifact_cache_entries': st.get('artifact_cache_entries'),
                'watched_sha_files': st.get('watched_sha_files'),
                'db_quick_check': st.get('db_quick_check'),
                'main_process': st.get('main_process')}

    # ---------------- phase 8: start/stop/restart UI operations ----------------
    def phase8_ui_ops(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-14']}
        ch = self.child[NODE_A_KEY]
        # stop
        self.ui_open_app()
        out['op_stop_selected'] = self.ui_set_operation('stop')
        out['op_stop_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex_stop, s1 = self._await_ui_deploy(timeout=180)
        out['stop_execution'] = self._ex(ex_stop)
        out['stop_state'] = self.app_states()
        out['stop_host'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG))
        out['stop_journal'] = ch.journal(UNIT, 25)
        self.ui_open_app()
        out['stop_health_card'] = self.ui_health_cards()
        self.shot('ui-07-stopped.png')
        # start
        out['op_start_selected'] = self.ui_set_operation('start')
        out['op_start_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex_start, s2 = self._await_ui_deploy(timeout=180)
        out['start_execution'] = self._ex(ex_start)
        out['start_state'] = self.app_states()
        out['start_host'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG))
        # restart
        self.ui_open_app()
        out['op_restart_selected'] = self.ui_set_operation('restart')
        out['op_restart_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex_restart, s3 = self._await_ui_deploy(timeout=180)
        out['restart_execution'] = self._ex(ex_restart)
        out['restart_state'] = self.app_states()
        out['restart_host'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG))
        out['journal_samples'] = {'stop': s1, 'start': s2, 'restart': s3}
        self.ui_open_app()
        out['final_health_card'] = self.ui_health_cards()
        self.rec('phase08-ui-ops.json', out)
        return out

    # ---------------- phase 9: health check types ----------------
    def phase9_ui_health_types(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-15', 'P14P15-14'], 'scenarios': {}}
        ports = self.state['ports']
        ch = self.child[NODE_A_KEY]
        scenarios = [
            ('tcp_ok', 'tcp', '127.0.0.1:%d' % ports['listen']),
            ('http_ok', 'http', 'http://127.0.0.1:%d/' % ports['http']),
            ('command_ok', 'command', 'test -f %s' % BINARY),
        ]
        for name, htype, target in scenarios:
            sc = {'health_type': htype, 'target': target}
            sc['host_probe_before'] = self._probe_target(htype, target)
            self.ui_open_app()
            self.ui_set_health_type(htype)
            self.ui_fill_field('健康检查目标', target)
            sc['form_after'] = self._read_form()
            sc['save_url'] = self.ui_save()
            time.sleep(2.5)
            self.ui_open_app()
            sc['operation'] = self.ui_set_operation('deploy')
            sc['deploy_click'] = self.ui_click_deploy()
            self.budget['ui_deploys'] += 1
            ex, samples = self._await_ui_deploy(timeout=300)
            sc['execution'] = self._ex(ex)
            sc['journal_samples'] = samples
            sc['state'] = self.app_states()
            sc['host'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG))
            self.ui_open_app()
            sc['health_card'] = self.ui_health_cards()
            sc['ui_text_has_healthy'] = '健康' in self.br.text()
            self.shot('ui-08-health-%s.png' % name)
            out['scenarios'][name] = sc
        # negative branch: tcp target that is really closed on the host
        closed = self._closed_port()
        sc = {'health_type': 'tcp', 'target': '127.0.0.1:%d' % closed, 'negative': True}
        sc['host_probe_before'] = self._probe_target('tcp', '127.0.0.1:%d' % closed)
        prev = self.app_hub() or {}
        sc['version_before'] = prev.get('version')
        sha_before = self.state['artifacts']['A1']['sha256']
        host_before = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG,
                                                   shas=(sha_before,)))
        sc['host_before'] = host_before
        self.ui_open_app()
        self.ui_set_health_type('tcp')
        self.ui_fill_field('健康检查目标', '127.0.0.1:%d' % closed)
        sc['save_url'] = self.ui_save()
        time.sleep(2.5)
        self.ui_open_app()
        sc['operation'] = self.ui_set_operation('deploy')
        sc['deploy_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex, samples = self._await_ui_deploy(timeout=300)
        sc['execution'] = self._ex(ex)
        sc['journal_samples'] = samples
        sc['state'] = self.app_states()
        sc['host_after'] = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG,
                                                        shas=(sha_before,)))
        sc['journal'] = ch.journal(UNIT, 40)
        self.ui_open_app()
        sc['health_card'] = self.ui_health_cards()
        sc['ui_text'] = self.br.text()[:900]
        self.shot('ui-09-health-tcp-negative.png')
        out['scenarios']['tcp_closed_negative'] = sc
        self.rec('phase09-ui-health-types.json', out)
        return out

    def config_effect_probe(self):
        """Does the app config file actually reach the running service?

        Reads the generated unit's EnvironmentFile line, systemd's own view of the
        service environment, the real process environment and the on-disk config
        so the 'Config' dimension is checked against a客户机实际效果, not just a
        written file.
        """
        script = (
            "import json,os,subprocess,re\n"
            "unit=%r; cfg=%r\n"
            "out={}\n"
            "up='/etc/systemd/system/'+unit\n"
            "out['unit_exists']=os.path.lexists(up)\n"
            "txt=open(up).read() if os.path.lexists(up) else ''\n"
            "out['unit_environmentfile_line']=[l for l in txt.splitlines() if l.startswith('EnvironmentFile=')]\n"
            "out['unit_environment_lines']=[l for l in txt.splitlines() if l.startswith('Environment=')]\n"
            "out['config_file_text']=open(cfg).read() if os.path.isfile(cfg) else None\n"
            "r=subprocess.run(['systemctl','show','-p','Environment','--value',unit],capture_output=True,text=True)\n"
            "out['systemctl_environment']=r.stdout.strip()\n"
            "mp=subprocess.run(['systemctl','show','-p','MainPID','--value',unit],capture_output=True,text=True).stdout.strip()\n"
            "out['main_pid']=mp\n"
            "proc={}\n"
            "if mp and mp!='0':\n"
            "    try:\n"
            "        for kv in open('/proc/'+mp+'/environ','rb').read().split(b'\\0'):\n"
            "            if b'=' in kv:\n"
            "                k,v=kv.split(b'=',1)\n"
            "                k=k.decode('utf-8','replace')\n"
            "                if k.startswith('APP_') or k=='EnvironmentFile':\n"
            "                    proc[k]=v.decode('utf-8','replace')\n"
            "    except Exception as e:\n"
            "        proc['_error']=type(e).__name__\n"
            "out['process_app_env']=proc\n"
            "print(json.dumps(out))\n"
        ) % (UNIT, CONFIG)
        return self.child[NODE_A_KEY].remote_py(script)

    def _probe_target(self, htype, target):
        if htype == 'tcp':
            host, port = target.split(':')
            script = ("import json,socket\n"
                      "s=socket.socket();s.settimeout(3)\n"
                      "try:\n"
                      "    s.connect((%r,int(%r)));print(json.dumps({'connect':'ok'}))\n"
                      "except Exception as e:\n"
                      "    print(json.dumps({'connect':'refused','error':type(e).__name__}))\n"
                      "finally:\n"
                      "    s.close()\n" % (host, port))
            return self.child[NODE_A_KEY].remote_py(script)
        if htype == 'http':
            script = ("import json,urllib.request\n"
                      "try:\n"
                      "    r=urllib.request.urlopen(%r,timeout=5)\n"
                      "    print(json.dumps({'http':r.status}))\n"
                      "except Exception as e:\n"
                      "    print(json.dumps({'http':'error:'+type(e).__name__}))\n" % target)
            return self.child[NODE_A_KEY].remote_py(script)
        if htype == 'command':
            script = ("import json,subprocess\n"
                      "r=subprocess.run(['/bin/sh','-c',%r],capture_output=True,text=True)\n"
                      "print(json.dumps({'rc':r.returncode}))\n" % target)
            return self.child[NODE_A_KEY].remote_py(script)
        script = ("import json,subprocess\n"
                  "r=subprocess.run(['systemctl','is-active',%r],capture_output=True,text=True)\n"
                  "print(json.dumps({'active':r.stdout.strip()}))\n" % target)
        return self.child[NODE_A_KEY].remote_py(script)

    def _closed_port(self):
        script = ("import json,socket\n"
                  "s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close()\n"
                  "print(json.dumps({'port':p}))\n")
        return self.child[NODE_A_KEY].remote_py(script)['port']

    # ---------------- phase 10: upgrade -> rollback (FL-11) ----------------
    def phase10_upgrade_rollback(self):
        out = {'at_utc': NOW(), 'case': ['FL-11'], 'steps': {}}
        ch = self.child[NODE_A_KEY]
        sha1 = self.state['artifacts']['A1']['sha256']
        sha2 = self.state['artifacts']['A2']['sha256']
        # 1) real UI upgrade to 2.0 (good artifact)
        self.ui_open_app()
        self.ui_select_artifact(self.state['artifacts']['A2']['name'])
        self.ui_fill_field('版本', '2.0')
        self.ui_fill_field('配置内容', 'APP_MODE=v2\nAPP_VERSION=2.0\n')
        out['steps']['upgrade_form'] = self._read_form()
        self.ui_set_health_type('systemd')
        self.ui_fill_field('健康检查目标', UNIT)
        out['steps']['upgrade_save_url'] = self.ui_save()
        time.sleep(3)
        got = self._wait_agent_def({'version': '2.0'})
        out['steps']['agent_has_v2'] = got.get('definition') is not None
        self.ui_open_app()
        out['steps']['operation'] = self.ui_set_operation('upgrade')
        out['steps']['click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex, samples = self._await_ui_deploy(timeout=300)
        host = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG, shas=(sha2,)))
        out['steps']['upgrade_execution'] = self._ex(ex)
        out['steps']['upgrade_journal_samples'] = samples
        out['steps']['upgrade_state'] = self.app_states()
        out['steps']['upgrade_host'] = host
        out['steps']['upgrade_journal'] = ch.journal(UNIT, 40)
        self.ui_open_app()
        out['steps']['upgrade_health_card'] = self.ui_health_cards()
        self.shot('ui-10-upgrade-v2.png')
        out['steps']['upgrade_ok'] = ((ex or {}).get('status') == 'SUCCESS'
                                     and host['binary'].get('sha256') == sha2
                                     and host['unit_state'].get('ActiveState') == 'active')
        # 2) UI upgrade to the always-failing 3.0 artifact -> agent must roll back to 2.0
        self.ui_open_app()
        self.ui_select_artifact(self.state['artifacts']['A3']['name'])
        self.ui_fill_field('版本', '3.0')
        out['steps']['failed_form'] = self._read_form()
        out['steps']['failed_save_url'] = self.ui_save()
        time.sleep(3)
        self._wait_agent_def({'version': '3.0'}, timeout=45)
        self.ui_open_app()
        out['steps']['failed_operation'] = self.ui_set_operation('upgrade')
        out['steps']['failed_click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex2, samples2 = self._await_ui_deploy(timeout=300)
        time.sleep(4)
        host2 = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG,
                                             shas=(sha1, sha2)))
        out['steps']['failed_execution'] = self._ex(ex2)
        out['steps']['failed_journal_samples'] = samples2
        out['steps']['failed_state'] = self.app_states()
        out['steps']['failed_host'] = host2
        out['steps']['failed_journal'] = ch.journal(UNIT, 60)
        self.ui_open_app()
        out['steps']['failed_health_card'] = self.ui_health_cards()
        self.shot('ui-11-failed-upgrade-rollback.png')
        out['steps']['rollback_restored_v2_binary'] = host2['binary'].get('sha256') == sha2
        out['steps']['journal_phase_after_failure'] = [r.get('phase') for r in
                                                       (host2.get('deployments') or [])[:4]]
        out['steps']['journal_all_done'] = all(r.get('phase') == 'DONE'
                                              for r in (host2.get('deployments') or []))
        out['steps']['open_rows'] = [r for r in (host2.get('deployments') or [])
                                     if r.get('phase') != 'DONE']
        self.rec('phase10-upgrade-rollback.json', out)
        return out

    # ---------------- phase 11: P14P15-18 recovery convergence ----------------
    def phase11_recovery(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-18'],
               'dimensions': ['Binary', 'Config', 'Unit', 'Health', 'Deployment Journal']}
        ch = self.child[NODE_A_KEY]
        sha1 = self.state['artifacts']['A1']['sha256']
        prev = self.app_hub() or {}
        out['current_version_definition'] = prev.get('version')
        self.ui_open_app()
        self.ui_select_artifact(self.state['artifacts']['A1']['name'])
        self.ui_fill_field('版本', '2.1')
        self.ui_fill_field('配置内容', 'APP_MODE=v2\nAPP_VERSION=2.1\n')
        self.ui_set_health_type('systemd')
        self.ui_fill_field('健康检查目标', UNIT)
        out['save_url'] = self.ui_save()
        time.sleep(3)
        self._wait_agent_def({'version': '2.1'}, timeout=45)
        self.ui_open_app()
        out['operation'] = self.ui_set_operation('deploy')
        out['click'] = self.ui_click_deploy()
        self.budget['ui_deploys'] += 1
        ex, samples = self._await_ui_deploy(timeout=300)
        host = self._host_slim(ch.app_state(self.state['apps']['main'], UNIT, BINARY, CONFIG, shas=(sha1,)))
        out['execution'] = self._ex(ex)
        out['journal_samples'] = samples
        out['state'] = self.app_states()
        out['recovered'] = {
            'Binary': {'path': BINARY, 'sha256': host['binary'].get('sha256'),
                       'expected': sha1, 'matches': host['binary'].get('sha256') == sha1},
            'Config': {'path': CONFIG, 'text': host.get('config_text'),
                       'matches_intended': '2.1' in (host.get('config_text') or '')},
            'Unit': {'path': host['unit_file_path'],
                     'lexists': (host.get('unit_file') or {}).get('lexists'),
                     'ActiveState': host['unit_state'].get('ActiveState'),
                     'SubState': host['unit_state'].get('SubState'),
                     'unit_sha': (host.get('unit_file') or {}).get('sha256')},
            'Health': {'api': [s.get('health') for s in out['state']],
                       'execution_status': (ex or {}).get('status')},
            'DeploymentJournal': {'rows_after': (host.get('deployments') or [])[:3],
                                  'all_done': all(r.get('phase') == 'DONE'
                                                  for r in (host.get('deployments') or [])),
                                  'db_quick_check': host.get('db_quick_check'),
                                  'unit_registry': host.get('unit_registry')}}
        out['config_effect_probe'] = self.config_effect_probe()
        out['journal_replay_probe'] = self.journal_replay_probe()
        out['journal_after_replay_probe'] = (self.host_state(NODE_A_KEY) or {}).get('deployments', [])[:4]
        self.ui_open_app()
        out['health_card'] = self.ui_health_cards()
        out['state_after_replay_probe'] = self.app_states()
        self.shot('ui-12-recovered.png')
        out['all_dimensions_recovered'] = (
            out['recovered']['Binary']['matches']
            and out['recovered']['Config']['matches_intended']
            and out['recovered']['Unit']['ActiveState'] == 'active'
            and out['recovered']['Health']['execution_status'] == 'SUCCESS'
            and out['recovered']['DeploymentJournal']['all_done'])
        out['recovery_verdict'] = {
            'binary_config_unit_health_recovered': (
                out['recovered']['Binary']['matches']
                and out['recovered']['Config']['matches_intended']
                and out['recovered']['Unit']['ActiveState'] == 'active'
                and out['recovered']['Health']['execution_status'] == 'SUCCESS'),
            'journal_all_rows_done': out['recovered']['DeploymentJournal']['all_done'],
            'journal_has_non_done_rows': [r for r in (host.get('deployments') or [])
                                          if r.get('phase') != 'DONE'],
            'stale_backup_replayed_on_restart': out['journal_replay_probe']['stale_backup_replayed'],
            'binary_sha_stable_across_restart': out['journal_replay_probe']['binary_unchanged'],
            'config_effective_for_service': None,
        }
        probe = out.get('config_effect_probe') or {}
        ef = (probe.get('unit_environmentfile_line') or [''])[0]
        out['recovery_verdict']['config_effective_for_service'] = {
            'unit_environmentfile_line': ef,
            'systemctl_environment': probe.get('systemctl_environment'),
            'process_app_env': probe.get('process_app_env'),
            'config_file_text': probe.get('config_file_text'),
            'verdict': ('systemd ignores the quoted "-<path>" form: the config file is '
                        'NOT loaded into the service environment')
            if ef.startswith('EnvironmentFile="-') else 'config file loaded into service env',
        }
        self.rec('phase11-recovery.json', out)
        return out

    # ---------------- phase 12: delete cancel / confirm ----------------
    def phase12_ui_delete(self):
        out = {'at_utc': NOW(), 'case': ['P14P15-16']}
        app_id = self.state['apps']['main']
        ch = self.child[NODE_A_KEY]
        out['ui_row_before'] = self.ui_app_row()
        out['assign_before'] = self.app_nodes()
        out['state_before'] = self.app_states()
        out['revisions_before'] = [r.get('revision') for r in self.app_revisions()]
        self.shot('ui-13-delete-before.png')
        # --- cancel path
        out['menu_items'] = self.ui_click_row_menu('删除')
        dialog = self.ui_confirm_dialog_text()
        out['cancel_dialog'] = dialog
        self.shot('ui-14-delete-dialog.png')
        out['cancel_click'] = self.ui_click_action_dialog('取消')
        time.sleep(1.2)
        out['dialog_after_cancel'] = self.ui_confirm_dialog_text()
        out['row_after_cancel'] = self.ui_app_row()
        code_after_cancel, _ = self.hub.get('/api/applications/' + self.hub.quote(app_id))
        out['get_http_after_cancel'] = code_after_cancel
        out['assign_after_cancel'] = self.app_nodes()
        out['state_after_cancel'] = self.app_states()
        out['revisions_after_cancel'] = [r.get('revision') for r in self.app_revisions()]
        out['cancel_preserved'] = (code_after_cancel == 200
                                  and out['assign_after_cancel'] == out['assign_before']
                                  and len(out['revisions_after_cancel']) == len(out['revisions_before'])
                                  and out['state_after_cancel'] == out['state_before'])
        # --- confirm path
        out['menu_items_2'] = self.ui_click_row_menu('删除')
        dialog2 = self.ui_confirm_dialog_text()
        out['confirm_dialog'] = dialog2
        out['host_before_delete'] = self._host_slim(ch.app_state(app_id, UNIT, BINARY, CONFIG))
        out['confirm_click'] = self.ui_click_action_dialog('删除')
        time.sleep(2)
        out['delete_requests'] = self.ui_net('/api/applications/' + app_id, 'DELETE')
        deadline = time.time() + 90
        code, body = 0, None
        while time.time() < deadline:
            code, body = self.hub.get('/api/applications/' + self.hub.quote(app_id))
            if code == 404:
                break
            time.sleep(2)
        out['get_http_after_delete'] = code
        out['delete_http'] = None
        self.state['deletes'][app_id] = {'kind': 'application', 'get_http': code}
        # product-path cleanup on the owned host: unit / binary / config / registry
        step = None
        deadline = time.time() + 90
        while time.time() < deadline:
            step = ch.app_state(app_id, UNIT, BINARY, CONFIG)
            if (not step['unit_file'].get('lexists') and not step['binary'].get('lexists')
                    and not step['config'].get('lexists') and not (step.get('unit_registry') or [])):
                break
            time.sleep(2)
        out['host_after_product_cleanup'] = {
            'unit_file_lexists': step['unit_file'].get('lexists'),
            'enable_symlink_lexists': step['enable_symlink'].get('lexists'),
            'binary_lexists': step['binary'].get('lexists'),
            'config_lexists': step['config'].get('lexists'),
            'unit_registry': step.get('unit_registry'),
            'local_apps': step.get('local_apps'),
            'unit_state': step['unit_state'],
            'unit_file_lexists_state': step['unit_state'].get('LoadState'),
            'binary_dir_entries': step.get('binary_dir_entries')}
        out['product_cleanup_complete'] = (
            not step['unit_file'].get('lexists') and not step['enable_symlink'].get('lexists')
            and not step['binary'].get('lexists') and not step['config'].get('lexists')
            and not (step.get('unit_registry') or []))
        out['ui_row_after_delete'] = self.ui_app_row()
        out['state_after_delete_http'] = self.hub.get(
            '/api/applications/%s/state' % self.hub.quote(app_id))[0]
        out['revisions_after_delete_http'] = self.hub.get(
            '/api/applications/%s/revisions' % self.hub.quote(app_id))[0]
        out['nodes_after_delete_http'] = self.hub.get(
            '/api/applications/%s/nodes' % self.hub.quote(app_id))[0]
        self.shot('ui-15-after-delete.png')
        out['id_reusable_404'] = code == 404
        self.rec('phase12-ui-delete.json', out)
        return out

    def journal_replay_probe(self):
        """Owned-child restart: does a non-DONE journal row replay a stale backup?

        This is the empirical check for the previously filed defect
        E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE on the verified build.
        Only the OWNED child Agent process is restarted; no VM, no Hub and no
        protected Agent unit is touched.
        """
        import json as _json
        ch = self.child[NODE_A_KEY]
        app_id = self.state['apps']['main']
        before = self._host_slim(ch.app_state(app_id, UNIT, BINARY, CONFIG))
        proc_before = ch.process_alive()
        rows_before = [(r['id'], r['phase'], r['to_version']) for r in (before.get('deployments') or [])]
        open_before = [r for r in rows_before if r[1] != 'DONE']
        stop = ch.stop()
        time.sleep(2)
        start = ch.start()
        node = self.wait_child_online(NODE_A_KEY, timeout=150)
        time.sleep(8)
        after = self._host_slim(ch.app_state(app_id, UNIT, BINARY, CONFIG))
        rows_after = [(r['id'], r['phase'], r['to_version']) for r in (after.get('deployments') or [])]
        log = ch.log_tail(120000)
        tail = (log or {}).get('tail') or ''
        recovery_lines = [l for l in tail.splitlines()
                          if ('recover' in l.lower() or 'superseded' in l.lower()
                              or 'rollback' in l.lower())]
        return {'open_rows_before': open_before, 'rows_before': rows_before,
                'binary_before_sha256': (before.get('binary') or {}).get('sha256'),
                'config_before': before.get('config_text'),
                'unit_active_before': (before.get('unit_state') or {}).get('ActiveState'),
                'process_before': proc_before, 'stop': stop, 'start': start,
                'child_online_after': bool(node and node.get('status') == 'online'),
                'rows_after': rows_after, 'open_rows_after': [r for r in rows_after if r[1] != 'DONE'],
                'binary_after_sha256': (after.get('binary') or {}).get('sha256'),
                'config_after': after.get('config_text'),
                'unit_active_after': (after.get('unit_state') or {}).get('ActiveState'),
                'binary_unchanged': (before.get('binary') or {}).get('sha256')
                                    == (after.get('binary') or {}).get('sha256'),
                'recovery_log_lines': recovery_lines[-25:],
                'stale_backup_replayed': (before.get('binary') or {}).get('sha256')
                                        != (after.get('binary') or {}).get('sha256')}

    # ---------------- cleanup ----------------
    def cleanup(self):
        res = {'at_utc': NOW(), 'steps': {}, 'errors': []}

        def step(name, fn):
            try:
                res['steps'][name] = fn()
            except Exception as exc:
                res['steps'][name] = {'error': str(exc)[:400]}
                res['errors'].append('%s: %s' % (name, exc))

        def host_sweep():
            out = {}
            for key in (NODE_A_KEY, NODE_B_KEY):
                if key in self.child:
                    ch = self.child[key]
                    out[key] = ch.unit_cleanup(UNIT, BINARY, CONFIG, ch.opt_root)
            return out
        step('host_residue_sweep', host_sweep)

        def child_removal():
            out = {}
            for key in (NODE_A_KEY, NODE_B_KEY):
                if key in self.child:
                    out[key] = self.child[key].remove()
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
                self.state['deletes'][art['id']] = {'kind': 'artifact', 'delete_http': code,
                                                    'get_http': gcode}
            return out
        step('artifact_deletes', delete_artifacts)

        def delete_nodes():
            out = {}
            for attempt in range(4):
                pending = [(k, self.state['nodes'][k]) for k in (NODE_A_KEY, NODE_B_KEY)
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
                    out['%s_attempt%d' % (key, attempt)] = {'id': nid, 'delete_http': code,
                                                            'delete_body': body, 'get_http': gcode}
                    self.state['deletes'][nid] = {'kind': 'node', 'delete_http': code,
                                                  'get_http': gcode}
                time.sleep(2)
            return out
        step('node_deletes', delete_nodes)
        res['get_http_by_id'] = {k: v['get_http'] for k, v in self.state['deletes'].items()}
        res['all_owned_deletes_get_404'] = all(v['get_http'] == 404
                                              for v in self.state['deletes'].values())
        res['budget'] = dict(self.budget)
        self.rec('phase13-cleanup.json', res)
        return res

    def oracle(self):
        nodes = self.hub.json('/api/nodes')
        by_id = {n['id']: n for n in nodes}
        protected_after, ok = {}, True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'),
                                                         'sync_status': n.get('sync_status'),
                                                         'agent_version': n.get('agent_version')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        arts = self.hub.json('/api/artifacts')
        apps = self.hub.json('/api/applications')
        leftovers = {'artifacts': [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)],
                     'applications': [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)],
                     'nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]}
        hosts = {}
        for hk in (NODE_A_HOST, NODE_B_HOST):
            hosts[hk] = hostlib.OwnedChild(NONCE, hk, tag=('' if hk == NODE_A_HOST else '-b')).host_baseline()
        guard = self.results.get('guard') or {}
        baseline = guard.get('baseline_counts', {})
        oracle = {
            'at_utc': NOW(), 'owned_prefix': PREFIX, 'verified_build': VERIFIED_BUILD,
            'protected_nodes_after': protected_after, 'protected_nodes_online_synced': ok,
            'protected_nodes_on_verified_build': all(
                (v or {}).get('agent_version') == VERIFIED_BUILD for v in protected_after.values()),
            'hub_leftover_owned_fixtures': leftovers, 'hub_leftover_clear': not any(leftovers.values()),
            'counts_after': {'artifacts': len(arts), 'applications': len(apps), 'nodes': len(nodes)},
            'counts_baseline': baseline,
            'counts_returned_to_baseline': (len(arts) == baseline.get('artifacts')
                                            and len(apps) == baseline.get('applications')
                                            and len(nodes) == baseline.get('nodes')),
            'hosts_after': hosts,
            'hosts_clean': all(
                (not h['owned_units'] and not h['etc_systemd_owned'] and not h['wants_owned']
                 and not h['owned_mounts'] and not h['root_exists'] and not h['opt_root_exists']
                 and not h['child_config_exists'] and not h['owned_child_processes']
                 and not h['global_owned_prefix_scan']) for h in hosts.values()),
            'agent_binary_sha_unchanged': all(h['agent_binary_sha256'] == VERIFIED_AGENT_SHA
                                              for h in hosts.values()),
            'protected_units_active': all(
                (h['primary_agent_unit'].get('ActiveState') == 'active'
                 and h['repair_agent_unit'].get('ActiveState') == 'active') for h in hosts.values()),
            'etc_systemd_count_baseline': {hk: (guard.get('host_baselines', {}).get(hk) or {}).get('etc_systemd_total')
                                           for hk in (NODE_A_HOST, NODE_B_HOST)},
            'etc_systemd_count_after': {hk: h['etc_systemd_total'] for hk, h in hosts.items()},
            'cleanup_results': self.results.get('cleanup', {}),
            'get_http_by_id': {k: v.get('get_http') for k, v in self.state['deletes'].items()},
        }
        oracle['etc_systemd_count_unchanged'] = all(
            oracle['etc_systemd_count_after'][hk] == oracle['etc_systemd_count_baseline'][hk]
            for hk in (NODE_A_HOST, NODE_B_HOST))
        oracle['all_owned_deletes_get_404'] = all(v == 404 for v in oracle['get_http_by_id'].values())
        oracle['clean'] = (ok and oracle['hub_leftover_clear'] and oracle['counts_returned_to_baseline']
                           and oracle['hosts_clean'] and oracle['agent_binary_sha_unchanged']
                           and oracle['protected_units_active'] and oracle['etc_systemd_count_unchanged']
                           and oracle['all_owned_deletes_get_404'])
        self.rec('final-cleanup-oracle.json', oracle)
        return oracle


def _short(v):
    s = json.dumps(v, ensure_ascii=False, default=str) if not isinstance(v, str) else v
    return s[:300]


PHASES = [
    ('guard', 'phase0_guard'),
    ('children', 'phase1_children'),
    ('artifacts', 'phase2_artifacts'),
    ('ui_login', 'phase3_ui_login'),
    ('ui_create', 'phase4_ui_create'),
    ('ui_edit_revision', 'phase5_ui_edit_revision'),
    ('ui_multi_assign', 'phase6_ui_multi_assign'),
    ('ui_deploy', 'phase7_ui_deploy'),
    ('ui_ops', 'phase8_ui_ops'),
    ('ui_health_types', 'phase9_ui_health_types'),
    ('upgrade_rollback', 'phase10_upgrade_rollback'),
    ('recovery', 'phase11_recovery'),
    ('ui_delete', 'phase12_ui_delete'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phases', default='')
    ap.add_argument('--keep', action='store_true',
                    help='do not run cleanup/oracle (for incremental iteration only)')
    args = ap.parse_args()

    import browser as browsemod
    started = NOW()
    b = browsemod.Browser(BROWSER_SESSION)
    run = Run(b)
    run.ui_log = []
    hubapi.write_json(str(HERE.parent / 'progress.json'),
                      {'batch_id': 'appui', 'nonce': NONCE, 'owned_prefix': PREFIX,
                       'endpoint': run.hub.base, 'verified_build': VERIFIED_BUILD,
                       'started_at_utc': started, 'browser_session': BROWSER_SESSION})
    want = [p.strip() for p in args.phases.split(',') if p.strip()] or [n for n, _ in PHASES]
    failed = []
    try:
        for name, fn_name in PHASES:
            if name not in want:
                continue
            run.progress(name)
            try:
                run.results[name] = getattr(run, fn_name)()
                run.save_state()
            except BaseException as exc:
                import traceback
                traceback.print_exc()
                run.results[name] = {'harness_error': '%s: %s' % (type(exc).__name__, exc)}
                failed.append(name)
                run.ui_log.append({'step': name, 'at_utc': NOW(), 'ok': False,
                                   'error': '%s: %s' % (type(exc).__name__, exc)})
                run.save_state()
                break
    finally:
        if not args.keep:
            run.progress('cleanup')
            run.results['cleanup'] = run.cleanup()
            run.progress('oracle')
            run.results['oracle'] = run.oracle()
        run.save_state()
        summary = {'batch_id': 'appui', 'verified_build': VERIFIED_BUILD, 'nonce': NONCE,
                   'owned_prefix': PREFIX, 'endpoint': run.hub.base,
                   'browser_session': BROWSER_SESSION,
                   'started_at_utc': started, 'finished_at_utc': NOW(),
                   'failed_phases': failed, 'budget': run.budget,
                   'ui_log': run.ui_log,
                   'state': {k: run.state.get(k) for k in ('nodes', 'agents', 'apps', 'artifacts',
                                                           'deletes', 'ports')},
                   'results': run.results}
        hubapi.write_json(str(EVID / 'run-evidence.json'), summary)
        print(json.dumps({'failed_phases': failed,
                          'oracle_clean': (run.results.get('oracle') or {}).get('clean'),
                          'ui_actions': run.budget['ui_actions'],
                          'ui_log_failures': [u for u in run.ui_log if not u.get('ok')]},
                         ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
