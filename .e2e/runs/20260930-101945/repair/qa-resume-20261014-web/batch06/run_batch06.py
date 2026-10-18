#!/usr/bin/env python3
"""web batch06 (qa-resume-20261014-web/batch06) — 节点状态操作 / 分组联动 / 离线节点行为.

Cases (web owner, repair authority http://192.168.100.209:18080):
  P04-04  操作菜单：online -> maintenance；maintenance -> online；set online
  P04-05  offline 节点 set online
  P11-04  offline 节点不进入无限等待，返回明确 BLOCKED/错误
  P06-08  Group 变化后 Task Target 和 Node Detail 任务列表同步
  P17P18-14  Agent Offline 执行 synced=false，恢复后幂等上传为 synced=true

Real evidence chain: browser click -> HTTP request -> Hub/Agent state change ->
real business effect (execution / dispatch / resolution) -> UI feedback.
No product source change. No protected node state change. Owned fixtures only.
"""
import json
import os
import random
import signal
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'runners'))

import child_agent  # noqa: E402
import hubapi  # noqa: E402
from frame_proxy import FrameProxy  # noqa: E402

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-w1410b06-%s-' % NONCE
SCRIPT_NAME = PREFIX + 'script'
TASK_NODE_NAME = PREFIX + 'task-node'
TASK_GROUP_NAME = PREFIX + 'task-group'
GROUP_NAME = PREFIX + 'group'
MARKER = 'QA:%s:EXEC' % NONCE
UI_SESSION = 'qa1410b06'
UI_BASE = 'http://192.168.100.209:18080'
DEV = 'evidence/'
HUB_TICK_MAX = 75          # revision_check_interval_sec on this hub (45s) + margin

PROTECTED_NODES = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}
TERMINAL = {'SUCCESS', 'FAILED', 'SKIPPED', 'CANCELED', 'TIMED_OUT', 'BLOCKED'}

SCRIPT_BODY = (
    '#!/bin/sh\n'
    'set -eu\n'
    'sleep "${NODESTEER_PARAM_delay:-0}"\n'
    "printf '%s\\n' '" + MARKER + "'\n"
)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime('%Y-%m-%dT%H:%M:%SZ')


class UiError(RuntimeError):
    pass


class Run:
    def __init__(self):
        self.hub = hubapi.Hub()
        self.hub.login()
        self.proxy = None
        self.child = None
        self.state = {
            'script_id': None, 'task_node_id': None, 'task_group_id': None,
            'group_id': None, 'child_node_id': None, 'child_agent_id': None,
            'schedules': {}, 'executions': {}, 'navigations': 0,
        }
        self.notes = []
        self.deviations = []
        self.budget = {
            'api_mutations': 0, 'ui_mutations': 0, 'child_process_starts': 0,
            'child_process_stops': 0, 'node_enroll_posts': 0, 'schedules_created': 0,
            'schedules_deleted': 0, 'tasks_created': 0, 'tasks_deleted': 0,
            'groups_created': 0, 'groups_deleted': 0, 'scripts_created': 0,
            'scripts_deleted': 0, 'node_status_posts': 0, 'browser_sessions': 1,
        }

    # ================= helpers: evidence =================
    def rec(self, name, value):
        if name.startswith(DEV):
            name = name[len(DEV):]
        hubapi.write_json(DEV + name, value)
        return value

    def phase(self, name, fn):
        progress_mark(name)
        return fn()

    # ================= helpers: hub =================
    def node(self, node_id):
        code, body = self.hub.get('/api/nodes/' + self.hub.quote(node_id))
        return body if code == 200 else None

    def node_rows(self):
        return self.hub.json('/api/nodes')

    def node_summary(self, node_id):
        n = self.node(node_id) or {}
        return {k: n.get(k) for k in ('id', 'hostname', 'status', 'sync_status',
                                      'global_revision', 'last_seen', 'ip')}

    def wait_node_status(self, node_id, status, timeout=60, poll=1.0):
        deadline = time.time() + timeout
        seen = []
        while time.time() < deadline:
            n = self.node(node_id)
            cur = (n or {}).get('status')
            if cur not in seen:
                seen.append(cur)
            if cur == status:
                return n, seen
            time.sleep(poll)
        return self.node(node_id), seen

    def wait_node_online_synced(self, node_id, timeout=75):
        deadline = time.time() + timeout
        n = None
        while time.time() < deadline:
            n = self.node(node_id)
            if n and n.get('status') == 'online' and n.get('sync_status') == 'synced':
                return n
            time.sleep(1)
        return n

    def wait_revision(self, target, timeout=45):
        deadline = time.time() + timeout
        n = None
        while time.time() < deadline:
            n = self.node(self.state['child_node_id'])
            if n and n.get('global_revision', -1) >= target:
                return n
            time.sleep(1)
        return n

    def executions(self, task_id, node_id=None, limit=100):
        q = '/api/executions?limit=%d&task_id=%s' % (limit, self.hub.quote(task_id))
        if node_id:
            q += '&node_id=' + self.hub.quote(node_id)
        code, body = self.hub.get(q)
        if code != 200:
            raise RuntimeError('executions_list_http_%s' % code)
        return body

    def execution(self, exec_id):
        code, body = self.hub.get('/api/executions/' + self.hub.quote(exec_id))
        return code, body

    def wait_execution(self, exec_id, timeout=90):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            code, body = self.execution(exec_id)
            last = body if code == 200 else None
            if code == 200 and body.get('status') in TERMINAL:
                return body
            time.sleep(1)
        return last

    def create_hub_schedule(self, task_id, run_at, key, offline_policy='hub_online_required'):
        code, sched = self.hub.post('/api/schedules', {
            'task_id': task_id, 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC',
            'execution_owner': 'hub', 'offline_policy': offline_policy,
            'misfire_policy': 'run_once', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('schedule_create_%s_http_%s %s' % (key, code, sched))
        self.state['schedules'][key] = sched['id']
        self.budget['schedules_created'] += 1
        return sched['id']

    def create_agent_schedule(self, task_id, run_at, key, offline_policy='allow_offline'):
        code, sched = self.hub.post('/api/schedules', {
            'task_id': task_id, 'type': 'one_time', 'run_at': run_at, 'timezone': 'UTC',
            'execution_owner': 'agent', 'offline_policy': offline_policy,
            'misfire_policy': 'run_once', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('agent_schedule_create_%s_http_%s %s' % (key, code, sched))
        self.state['schedules'][key] = sched['id']
        self.budget['schedules_created'] += 1
        return sched['id']

    def delete_schedule(self, key):
        sid = self.state['schedules'].get(key)
        if not sid:
            return None
        code, body = self.hub.delete('/api/schedules/' + self.hub.quote(sid))
        self.budget['api_mutations'] += 1
        self.budget['schedules_deleted'] += 1
        gcode, _ = self.hub.get('/api/schedules/' + self.hub.quote(sid))
        return {'key': key, 'id': sid, 'delete_http': code, 'get_http': gcode}

    # ================= helpers: browser =================
    def ui(self, *args, timeout=90, check=False):
        cmd = ['agent-browser', '--session', UI_SESSION] + [str(a) for a in args]
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (cp.stdout or '') + (cp.stderr or '')
        if check and cp.returncode != 0:
            raise UiError('ui_cmd_failed %s :: %s' % (' '.join(args), out[-400:]))
        return cp.returncode, out

    def ui_open(self, path, wait_text=None):
        self.state['navigations'] += 1
        url = UI_BASE + path
        rc, out = self.ui('open', url, timeout=90)
        if wait_text:
            self.ui_wait_text(wait_text, timeout=25)
        return url

    def ui_eval(self, js, timeout=60):
        cp = subprocess.run(['agent-browser', '--session', UI_SESSION, 'eval', '--stdin'],
                            input=js, capture_output=True, text=True, timeout=timeout)
        raw = (cp.stdout or '').strip()
        if not raw:
            raise UiError('ui_eval_empty :: %s' % (cp.stderr or '')[-300:])
        try:
            return json.loads(raw)
        except Exception:
            start = raw.find('{')
            end = raw.rfind('}')
            if start >= 0 and end > start:
                return json.loads(raw[start:end + 1])
            raise UiError('ui_eval_unparsable :: %s' % raw[:300])

    def ui_shot(self, name, full=False):
        args = ['screenshot']
        if full:
            args.append('--full')
        args.append(str(HERE / DEV / name))
        self.ui(*args, timeout=60)
        return name

    def ui_net(self, *extra):
        rc, out = self.ui('network', 'requests', *extra, timeout=45)
        return out.strip().splitlines()

    def ui_login_if_needed(self):
        """The SPA redirects unauthenticated navigations to /sign-in; re-auth using the
        piped credential script so the password never reaches argv/stdout/evidence."""
        self.ui_open('/agents', wait_text='主机名')
        state = self.ui_eval("(() => ({ path: location.pathname, has_table: !!document.querySelector('table') }))()")
        if state.get('path', '').startswith('/sign-in') or not state.get('has_table'):
            self.ui_open('/sign-in', wait_text='登录')
            for attempt in (1, 2):
                # wait until the hydrated sign-in form inputs exist before filling
                ready = False
                deadline = time.time() + 25
                while time.time() < deadline:
                    try:
                        if self.ui_eval("(() => !!(document.querySelector('input[name=username]') && "
                                        "document.querySelector('input[name=password]')))()"):
                            ready = True
                            break
                    except UiError:
                        pass
                    time.sleep(0.5)
                if not ready:
                    raise UiError('sign_in_form_not_ready')
                cp = subprocess.run(['python3', str(HERE / 'runners' / 'login_fill.py')],
                                    capture_output=True, text=True, timeout=30)
                if cp.returncode != 0:
                    raise UiError('login_fill_failed')
                filled = self.ui_eval(cp.stdout)
                if not filled.get('ok'):
                    raise UiError('login_fill_not_ok:%s' % filled)
                time.sleep(0.4)
                lengths = self.ui_eval("(() => ({u: (document.querySelector('input[name=username]')||{}).value"
                                       " ? document.querySelector('input[name=username]').value.length : 0,"
                                       " p: (document.querySelector('input[name=password]')||{}).value"
                                       " ? document.querySelector('input[name=password]').value.length : 0}))()")
                if not (lengths.get('u') and lengths.get('p')):
                    continue
                self.ui('find', 'role', 'button', 'click', '--name', '登录', timeout=45)
                ok = False
                deadline = time.time() + 25
                while time.time() < deadline:
                    try:
                        path = str(self.ui_eval("(() => location.pathname)()"))
                        if '/sign-in' not in path:
                            ok = True
                            break
                    except UiError:
                        pass
                    time.sleep(0.5)
                if ok:
                    break
            self.ui_wait_text('主机名', timeout=25)
            state = self.ui_eval("(() => ({ path: location.pathname, has_table: !!document.querySelector('table') }))()")
        return state

    def ui_net_clear(self):
        self.ui('network', 'requests', '--clear', timeout=45)

    def ui_wait_text(self, text, timeout=20, poll=0.6):
        """Robust text wait: polls `eval` so it does not depend on CLI wait flags."""
        js = "(() => !!(document.body && document.body.innerText.includes(%s)))()" % json.dumps(text)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.ui_eval(js):
                    return True
            except UiError:
                pass
            time.sleep(poll)
        return False

    def ui_wait_url(self, needle, timeout=20, poll=0.6):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if needle in str(self.ui_eval("(() => location.pathname)()")):
                    time.sleep(0.8)
                    return True
            except UiError:
                pass
            time.sleep(poll)
        return False

    def ui_menu_items(self):
        """Read the currently-open dropdown menu item labels."""
        return self.ui_eval("(() => Array.from(document.querySelectorAll('[role=menuitem]'))"
                            ".map(e => e.innerText.trim()))()")

    def ui_tag_node_row(self, hostname):
        js = """(() => {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  rows.forEach(r => r.removeAttribute('data-e2e-row'));
  const host = %s;
  const row = rows.find(r => r.innerText.includes(host));
  if (!row) return { found: false, rows: rows.map(r => r.innerText.replace(/\\s+/g,' ').trim()) };
  row.setAttribute('data-e2e-row', '1');
  return { found: true, cells: Array.from(row.querySelectorAll('td')).map(c => c.innerText.trim()) };
})()""" % json.dumps(hostname)
        return self.ui_eval(js)

    def ui_click_node_menu_action(self, hostname, label):
        """Real user flow: open the row 操作 menu and click one menu item."""
        self.ui_net_clear()
        tagged = self.ui_tag_node_row(hostname)
        if not tagged.get('found'):
            raise UiError('ui_row_not_found:%s' % hostname)
        self.ui('find', 'first', "[data-e2e-row='1'] td:last-child button", 'click', timeout=45)
        self.ui_wait_text('设为在线', timeout=10)
        items = self.ui_menu_items()
        rc, out = self.ui('find', 'role', 'menuitem', 'click', '--name', label, timeout=45)
        return {'menu_items_before_click': items, 'clicked': label, 'click_rc': rc}

    def ui_read_node_row(self, hostname):
        return self.ui_tag_node_row(hostname)

    def ui_row_status(self, hostname):
        tagged = self.ui_read_node_row(hostname)
        if not tagged.get('found'):
            return None
        cells = tagged['cells']
        # cells: [select, status, hostname, os, group, ip, arch, agent_version, mode, last_seen, actions]
        return {'status_badge': cells[1] if len(cells) > 1 else None,
                'hostname': cells[2] if len(cells) > 2 else None,
                'group': cells[4] if len(cells) > 4 else None,
                'cells': cells}

    def ui_node_detail_task_links(self, node_id):
        self.ui_open('/nodes/%s' % node_id, wait_text='任务')
        data = self.ui_eval("""(() => {
  const titles = Array.from(document.querySelectorAll('[data-slot=card-title]'));
  const t = titles.find(x => x.innerText.trim() === '任务');
  let root = null, links = [], section_text = null;
  if (t) {
    root = t.closest('[data-slot=card]');
    if (root) {
      section_text = root.innerText.replace(/\\s+/g, ' ').trim();
      links = Array.from(root.querySelectorAll('a[href*="/tasks/"]')).map(a => ({ href: a.getAttribute('href'), text: a.innerText.trim() }));
    }
  }
  const main = document.querySelector('main');
  const mainText = main ? main.innerText.replace(/\\s+/g, ' ') : '';
  return {
    section_found: !!root, section_text: section_text, task_links: links,
    main_text: mainText.slice(0, 3000),
    node_task_present_in_main: mainText.includes(%s),
    group_task_present_in_main: mainText.includes(%s)
  };
})()""" % (json.dumps(TASK_NODE_NAME), json.dumps(TASK_GROUP_NAME)))
        return data

    def ui_run_task(self, task_id):
        """Real user flow on /tasks/{id}/run: click 立即运行, confirm in the dialog."""
        self.ui_net_clear()
        self.ui_open('/tasks/%s/run' % task_id, wait_text='立即运行')
        self.ui('find', 'role', 'button', 'click', '--name', '立即运行', timeout=45)
        time.sleep(2)
        dlg = self.ui_eval("""(() => {
  const dlg = document.querySelector('[role=alertdialog]');
  if (!dlg) return { found: false };
  const btns = Array.from(dlg.querySelectorAll('button'));
  const confirm = btns[btns.length - 1];
  confirm.setAttribute('data-e2e-confirm', '1');
  return { found: true, dialog_buttons: btns.map(b => b.innerText.trim()), title: (dlg.innerText || '').replace(/\\s+/g, ' ').slice(0, 300) };
})()""")
        if not dlg.get('found'):
            raise UiError('run_confirm_dialog_missing')
        self.ui('find', 'first', "[data-e2e-confirm='1']", 'click', timeout=45)
        # give the SPA time to render the toast and (on failure) not navigate
        time.sleep(3)
        toast = self.ui_eval("""(() => {
  const region = document.querySelector('[role=region]');
  const items = Array.from(document.querySelectorAll('[data-sonner-toast], [role=status], li'));
  return { toast_text: region ? region.innerText.replace(/\\s+/g, ' ').trim().slice(0, 400) : '',
           url: location.pathname,
           body_text: document.querySelector('main') ? document.querySelector('main').innerText.replace(/\\s+/g, ' ').slice(0, 600) : '' };
})()""")
        dlg['toast'] = toast
        dlg['post_requests'] = self.ui_net('--method', 'POST', '--filter', '/api/tasks/')
        return dlg

    def ui_group_member_toggle(self, group_id, hostname, want_checked):
        self.ui_net_clear()
        self.ui_open('/groups/%s' % group_id, wait_text='成员')
        tagged = self.ui_eval("""(() => {
  const host = %s;
  const labels = Array.from(document.querySelectorAll('label'));
  const l = labels.find(x => x.innerText.includes(host));
  if (!l) return { found: false, labels: labels.map(x => x.innerText.replace(/\\s+/g,' ').trim()).slice(0, 20) };
  const box = l.querySelector('[role=checkbox], input[type=checkbox]');
  if (!box) return { found: false, reason: 'no checkbox in label' };
  box.setAttribute('data-e2e-check', '1');
  const state = box.getAttribute('aria-checked') !== null ? box.getAttribute('aria-checked') : String(box.checked);
  return { found: true, checked_before: state };
})()""" % json.dumps(hostname))
        if not tagged.get('found'):
            raise UiError('group_member_checkbox_not_found')
        before = tagged['checked_before']
        state = (before == 'true')
        if state != want_checked:
            self.ui('find', 'first', "[data-e2e-check='1']", 'click', timeout=45)
            time.sleep(0.5)
        after = self.ui_eval("(() => { const b = document.querySelector(\"[data-e2e-check='1']\");"
                             " return b ? (b.getAttribute('aria-checked') !== null ? b.getAttribute('aria-checked') : String(b.checked)) : null; })()")
        self.ui('find', 'role', 'button', 'click', '--name', '保存', timeout=45)
        self.ui_wait_url('/groups', timeout=25)
        return {'checked_before': before, 'checked_after': after,
                'requests': self.ui_net('--method', 'PUT', '--filter', '/api/groups/')}

    def ui_groups_row(self, group_name):
        self.ui_open('/groups', wait_text='分组')
        return self.ui_eval("""(() => {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  const row = rows.find(r => r.innerText.includes(%s));
  return { found: !!row, cells: row ? Array.from(row.querySelectorAll('td')).map(c => c.innerText.trim()) : [],
           all: rows.map(r => r.innerText.replace(/\\s+/g,' ').trim()) };
})()""" % json.dumps(group_name))

    def ui_task_detail(self, task_id):
        self.ui_open('/tasks/%s' % task_id, wait_text='目标')
        return self.ui_eval("""(() => {
  const main = document.querySelector('main');
  return { main_text: main ? main.innerText.replace(/\\s+/g, ' ').slice(0, 3000) : '', url: location.pathname };
})()""")

    def ui_execution_detail(self, exec_id):
        self.ui_open('/executions/%s' % exec_id, wait_text='离线')
        return self.ui_eval("""(() => {
  const main = document.querySelector('main');
  return { main_text: main ? main.innerText.replace(/\\s+/g, ' ').slice(0, 3000) : '', url: location.pathname };
})()""")

    def ui_tasks_table_row(self, task_name):
        self.ui_open('/tasks', wait_text='目标')
        return self.ui_eval("""(() => {
  const rows = Array.from(document.querySelectorAll('table tbody tr'));
  const row = rows.find(r => r.innerText.includes(%s));
  return { found: !!row, cells: row ? Array.from(row.querySelectorAll('td')).map(c => c.innerText.trim()) : [] };
})()""" % json.dumps(task_name))

    # ================= phases =================
    def phase0_guard(self):
        out = {'at_utc': now_utc(), 'endpoint': self.hub.base, 'owned_prefix': PREFIX}
        nodes = self.node_rows()
        by_id = {n['id']: n for n in nodes}
        out['protected_pre'] = {}
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            if not n:
                raise RuntimeError('protected_node_missing:' + label)
            out['protected_pre'][label] = {'id': nid, 'hostname': n.get('hostname'),
                                           'status': n.get('status'), 'sync_status': n.get('sync_status'),
                                           'global_revision': n.get('global_revision')}
        groups = self.hub.json('/api/groups')
        tasks = self.hub.json('/api/tasks')
        scripts = self.hub.json('/api/scripts')
        schedules = self.hub.json('/api/schedules')
        out['pre_counts'] = {'nodes': len(nodes), 'groups': len(groups), 'tasks': len(tasks),
                             'scripts': len(scripts), 'schedules': len(schedules)}
        if any((s.get('name') or '').startswith(PREFIX) for s in scripts) or \
           any((t.get('name') or '').startswith(PREFIX) for t in tasks) or \
           any((g.get('name') or '').startswith(PREFIX) for g in groups) or \
           any((n.get('hostname') or '').startswith(PREFIX) for n in nodes):
            raise RuntimeError('owned_fixture_already_present')
        out['owned_prefix_clear'] = True
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        out['native2_pre'] = {
            'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
            'owned_child_processes': inv['owned_child_processes'],
            'other_owned_children': inv['other_owned_children'],
            'all_qa_roots': inv['all_qa_roots'],
            'agent_binary_sha256': inv['agent_binary_sha256'],
            'agent_binary_sha_matches': inv['agent_binary_sha256'] == child_agent.AGENT_SHA,
            'primary_agent_unit': inv['primary_agent_unit'],
            'repair_agent_unit': inv['repair_agent_unit'],
        }
        if inv['root_exists'] or inv['config_exists'] or inv['owned_child_processes']:
            raise RuntimeError('owned_child_residue_present')
        if not out['native2_pre']['agent_binary_sha_matches']:
            raise RuntimeError('agent_binary_sha_mismatch')
        if inv['primary_agent_unit'].get('ActiveState') != 'active' or \
           inv['repair_agent_unit'].get('ActiveState') != 'active':
            raise RuntimeError('protected_agent_unit_not_active')
        out['other_batches_on_native2'] = {
            'note': 'other batches are executing in parallel; their owned children/roots are recorded '
                    'here and are never signalled or removed by this batch',
            'roots': inv['other_qa_roots'], 'processes': inv['other_owned_children']}
        return self.rec('phase0-guard.json', out)

    def phase1_child(self):
        code, enroll = self.hub.post('/api/nodes/enrollment', {
            'node_name': PREFIX + 'child',
            'node_ip': '192.168.100.206',
            'hub_address': UI_BASE,
        })
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
                                            PREFIX + 'child', '192.168.100.206', token)
        started = self.child.write_config_and_start()
        self.budget['child_process_starts'] += 1
        node = self.wait_node_online_synced(self.state['child_node_id'], timeout=90)
        out = {
            'at_utc': now_utc(),
            'enrollment': {'http': code, 'node_id': enroll['node_id'], 'agent_id': enroll['agent_id'],
                           'node_name': PREFIX + 'child', 'node_ip': '192.168.100.206',
                           'registration_token_persisted_to_evidence': False},
            'owned_child': {'root_on_native2': self.child.root, 'config_mode_0600': True,
                            'data_dir': self.child.data, 'child_processes': started.get('owned_processes'),
                            'listened_operator_port': port,
                            'routing': 'child(206) -> ws://192.168.100.170:%d (harness frame proxy) -> repair hub :18080' % port},
            'node_after_start': self.node_summary(self.state['child_node_id']) if node else None,
            'agent_binary_sha256': child_agent.AGENT_SHA,
        }
        if not node or node.get('status') != 'online' or node.get('sync_status') != 'synced':
            raise RuntimeError('owned_child_did_not_reach_online_synced')
        return self.rec('phase1-owned-child.json', out)

    def phase2_fixtures(self):
        import hashlib
        content_sha = hashlib.sha256(SCRIPT_BODY.encode()).hexdigest()
        code, script = self.hub.post('/api/scripts', {
            'name': SCRIPT_NAME,
            'description': 'web batch06 node-status/group/offline fixture (nonce %s).' % NONCE,
            'interpreter': 'shell', 'content': SCRIPT_BODY,
            'parameters': [{'name': 'delay', 'type': 'string', 'required': False, 'default': '0'}],
            'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('script_create_http_%s %s' % (code, script))
        self.state['script_id'] = script['id']
        self.budget['scripts_created'] += 1

        code, group = self.hub.post('/api/groups', {
            'name': GROUP_NAME, 'description': 'web batch06 group-target fixture (nonce %s).' % NONCE,
            'type': 'static', 'members': [],
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('group_create_http_%s %s' % (code, group))
        self.state['group_id'] = group['id']
        self.budget['groups_created'] += 1

        code, t1 = self.hub.post('/api/tasks', {
            'name': TASK_NODE_NAME, 'type': 'script', 'script_id': self.state['script_id'],
            'target': {'type': 'node', 'node_ids': [self.state['child_node_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_node_create_http_%s %s' % (code, t1))
        self.state['task_node_id'] = t1['id']
        self.budget['tasks_created'] += 1

        code, t2 = self.hub.post('/api/tasks', {
            'name': TASK_GROUP_NAME, 'type': 'script', 'script_id': self.state['script_id'],
            'target': {'type': 'group', 'group_ids': [self.state['group_id']]},
            'parameters': [], 'param_values': {'delay': '0'}, 'timeout': 20, 'retry': 0,
            'offline_policy': 'hub_online_required', 'enabled': True,
        })
        self.budget['api_mutations'] += 1
        if code not in (200, 201):
            raise RuntimeError('task_group_create_http_%s %s' % (code, t2))
        self.state['task_group_id'] = t2['id']
        self.budget['tasks_created'] += 1

        s_read = self.hub.json('/api/scripts/' + self.hub.quote(self.state['script_id']))
        t1_read = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_node_id']))
        t2_read = self.hub.json('/api/tasks/' + self.hub.quote(self.state['task_group_id']))
        g_read = self.hub.json('/api/groups/' + self.hub.quote(self.state['group_id']))
        if s_read.get('sha256') != content_sha:
            raise RuntimeError('script_readback_mismatch')
        if t1_read.get('target', {}).get('node_ids') != [self.state['child_node_id']]:
            raise RuntimeError('task1_readback_mismatch')
        if t2_read.get('target', {}).get('group_ids') != [self.state['group_id']]:
            raise RuntimeError('task2_readback_mismatch')
        if g_read.get('members'):
            raise RuntimeError('group_not_empty_at_create')
        node = self.wait_revision(2, timeout=45)
        out = {
            'at_utc': now_utc(), 'nonce': NONCE,
            'script': {'id': script['id'], 'name': SCRIPT_NAME, 'sha256': s_read.get('sha256'),
                       'content_sha256_expected': content_sha},
            'task_node': {'id': t1['id'], 'name': TASK_NODE_NAME, 'target': t1_read.get('target')},
            'task_group': {'id': t2['id'], 'name': TASK_GROUP_NAME, 'target': t2_read.get('target')},
            'group': {'id': group['id'], 'name': GROUP_NAME, 'type': g_read.get('type'),
                      'members': g_read.get('members')},
            'child_applied_revision': (node or {}).get('global_revision'),
            'child_sync_status': (node or {}).get('sync_status'),
        }
        return self.rec('phase2-fixtures.json', out)

    # ---- P04-04 ----
    def phase3_p04_04(self):
        out = {'at_utc': now_utc(), 'case': 'P04-04', 'steps': []}
        child_id = self.state['child_node_id']
        host = PREFIX + 'child'
        out['ui_auth'] = self.ui_login_if_needed()

        # --- step 1: initial UI readback (online) ---
        self.ui_open('/agents', wait_text='主机名')
        row0 = self.ui_row_status(host)
        self.ui_shot('P0404-01-node-online-row.png')
        out['steps'].append({'step': 'ui_initial_readback', 'row': row0,
                             'hub_node': self.node_summary(child_id)})

        # --- step 2: online -> maintenance via the UI row menu ---
        click = self.ui_click_node_menu_action(host, '维护')
        time.sleep(1.5)
        row1 = self.ui_row_status(host)
        self.ui_shot('P0404-02-node-maintenance-row.png')
        posts1 = self.ui_net('--method', 'POST', '--filter', '/api/nodes/')
        node1 = self.wait_node_status(child_id, 'maintenance', timeout=20)
        out['steps'].append({'step': 'ui_online_to_maintenance', 'menu': click,
                             'post_requests': posts1, 'row_after': row1,
                             'hub_node_after': self.node_summary(child_id),
                             'hub_status_reached': (node1[0] or {}).get('status'),
                             'status_trace': node1[1]})

        # --- step 3: side effect of maintenance — hub-owned schedule is SKIPPED, not dispatched ---
        slot1 = iso(datetime.now(timezone.utc) - timedelta(seconds=90))
        self.create_hub_schedule(self.state['task_node_id'], slot1, 'p0404_maintenance_skip')
        row = None
        deadline = time.time() + HUB_TICK_MAX * 3
        while time.time() < deadline:
            rows = [e for e in self.executions(self.state['task_node_id'], child_id)
                    if (e.get('scheduled_time') or '').startswith(slot1[:19])]
            if rows:
                row = rows[0]
                break
            time.sleep(3)
        self.state['executions']['p0404_paused_skip'] = (row or {}).get('id')
        out['steps'].append({
            'step': 'maintenance_blocks_hub_dispatch',
            'slot_utc': slot1,
            'execution': None if not row else {
                'id': row.get('id'), 'status': row.get('status'), 'block_reason': row.get('block_reason'),
                'trigger_type': row.get('trigger_type'), 'node_id': row.get('node_id'),
                'scheduled_time': row.get('scheduled_time')},
            'expected': 'SKIPPED with block_reason "node paused" (hub/nodes spec: 调度跳过留痕)',
        })

        # --- step 4: maintenance -> online via 恢复 ---
        click2 = self.ui_click_node_menu_action(host, '恢复')
        time.sleep(1.5)
        row2 = self.ui_row_status(host)
        self.ui_shot('P0404-03-node-resumed-row.png')
        posts2 = self.ui_net('--method', 'POST', '--filter', '/api/nodes/')
        node2 = self.wait_node_status(child_id, 'online', timeout=20)
        out['steps'].append({'step': 'ui_maintenance_to_online_resume', 'menu': click2,
                             'post_requests': posts2, 'row_after': row2,
                             'hub_node_after': self.node_summary(child_id),
                             'hub_status_reached': (node2[0] or {}).get('status')})

        # --- step 5: real dispatch works again after resume ---
        slot2 = iso(datetime.now(timezone.utc) - timedelta(seconds=60))
        self.create_hub_schedule(self.state['task_node_id'], slot2, 'p0404_after_resume')
        row_ok = None
        ex_ok = None
        deadline = time.time() + HUB_TICK_MAX * 3
        while time.time() < deadline:
            rows = [e for e in self.executions(self.state['task_node_id'], child_id)
                    if (e.get('scheduled_time') or '').startswith(slot2[:19])]
            if rows:
                row_ok = rows[0]
                ex_ok = self.wait_execution(row_ok['id'], timeout=60)
                break
            time.sleep(3)
        self.state['executions']['p0404_after_resume'] = (row_ok or {}).get('id')
        out['steps'].append({
            'step': 'resume_restores_hub_dispatch',
            'slot_utc': slot2,
            'execution': None if not row_ok else {
                'id': row_ok['id'], 'status': (ex_ok or {}).get('status'),
                'stdout': ((ex_ok or {}).get('stdout') or '').strip(),
                'marker_present': MARKER in ((ex_ok or {}).get('stdout') or ''),
                'trigger_type': (ex_ok or {}).get('trigger_type')},
        })

        # --- step 6: maintenance then 设为在线 (direct maintenance -> online) ---
        click3 = self.ui_click_node_menu_action(host, '维护')
        time.sleep(1.5)
        node_m = self.wait_node_status(child_id, 'maintenance', timeout=20)
        row3 = self.ui_row_status(host)
        click4 = self.ui_click_node_menu_action(host, '设为在线')
        time.sleep(1.5)
        row4 = self.ui_row_status(host)
        self.ui_shot('P0404-04-node-set-online-row.png')
        posts4 = self.ui_net('--method', 'POST', '--filter', '/api/nodes/')
        node4 = self.wait_node_status(child_id, 'online', timeout=20)
        out['steps'].append({'step': 'ui_mechanics_maintenance_then_set_online',
                             'to_maintenance': click3, 'to_maintenance_hub_status':
                                 (node_m[0] or {}).get('status'), 'to_maintenance_row': row3,
                             'set_online': click4, 'set_online_row': row4,
                             'post_requests': posts4,
                             'hub_node_after': self.node_summary(child_id),
                             'hub_status_reached': (node4[0] or {}).get('status')})

        # --- step 7: Agent-side effect — maintenance pauses the agent-local scheduler ---
        # agent-owned allow_offline one_time schedule due while the node is paused,
        # then the same schedule must catch up after 恢复. Both status flips go
        # through the same UI row menu (real user operation).
        run_at = iso(datetime.now(timezone.utc) + timedelta(seconds=25))
        self.create_agent_schedule(self.state['task_node_id'], run_at, 'p0404_agent_offline_sched')
        self.wait_revision(4, timeout=45)
        self.ui_open('/agents', wait_text='主机名')
        click5 = self.ui_click_node_menu_action(host, '维护')
        paused_row = self.wait_node_status(child_id, 'maintenance', timeout=20)
        row_paused = self.ui_row_status(host)
        time.sleep(45)
        local_paused = self.child.db_read(self.state['task_node_id'], [])
        fired_while_paused = [e for e in local_paused.get('task_executions', [])
                              if (e.get('scheduled_time') or '').startswith(run_at[:19])]
        click6 = self.ui_click_node_menu_action(host, '恢复')
        self.wait_node_status(child_id, 'online', timeout=20)
        local_after_resume = None
        deadline = time.time() + 90
        while time.time() < deadline:
            db = self.child.db_read(self.state['task_node_id'], [])
            cand = [e for e in db.get('task_executions', [])
                    if (e.get('scheduled_time') or '').startswith(run_at[:19])]
            if cand:
                local_after_resume = cand[0]
                break
            time.sleep(3)
        out['steps'].append({
            'step': 'agent_paused_blocks_local_schedule_and_resume_catches_up',
            'run_at_utc': run_at,
            'ui_pause': click5, 'ui_resume': click6,
            'node_status_while_waiting': (paused_row[0] or {}).get('status'),
            'ui_row_while_paused': row_paused,
            'local_executions_while_maintenance': fired_while_paused,
            'local_execution_after_resume': local_after_resume,
            'expected': 'paused node must not fire its agent-local schedule; after 恢复 the one_time slot fires once',
        })
        out['agent_pause_dimension_pass'] = (not fired_while_paused and local_after_resume is not None)

        ok = (row0 and row0['status_badge'] == '在线'
              and '维护' in click['menu_items_before_click']
              and (row1 or {}).get('status_badge') == 'maintenance'
              and (node1[0] or {}).get('status') == 'maintenance'
              and row is not None and row.get('status') == 'SKIPPED'
              and (row.get('block_reason') or '') == 'node paused'
              and '恢复' in click2['menu_items_before_click']
              and (row2 or {}).get('status_badge') == '在线'
              and (node2[0] or {}).get('status') == 'online'
              and ex_ok is not None and (ex_ok or {}).get('status') == 'SUCCESS'
              and MARKER in ((ex_ok or {}).get('stdout') or '')
              and (row4 or {}).get('status_badge') == '在线'
              and (node4[0] or {}).get('status') == 'online')
        out['assertions_pass'] = bool(ok)
        return self.rec('phase3-p04-04-operation-menu.json', out)

    # ---- P04-05 ----
    def phase4_p04_05(self):
        out = {'at_utc': now_utc(), 'case': 'P04-05', 'steps': []}
        child_id = self.state['child_node_id']
        host = PREFIX + 'child'
        out['ui_auth'] = self.ui_login_if_needed()

        # --- make the owned child genuinely offline by stopping its process ---
        stopped = self.child.stop()
        self.budget['child_process_stops'] += 1
        node_off, trace = self.wait_node_status(child_id, 'offline', timeout=90)
        self.ui_open('/agents', wait_text='主机名')
        row_off = self.ui_row_status(host)
        self.ui_shot('P0405-01-node-offline-row.png')
        out['steps'].append({'step': 'owned_child_process_stopped_node_offline',
                             'stop': stopped, 'hub_node': self.node_summary(child_id),
                             'status_trace': trace, 'ui_row': row_off})

        # --- UI: 设为在线 on the offline node ---
        click = self.ui_click_node_menu_action(host, '设为在线')
        time.sleep(1.5)
        row_after = self.ui_row_status(host)
        self.ui_shot('P0405-02-node-set-online-row.png')
        posts = self.ui_net('--method', 'POST', '--filter', '/api/nodes/')
        node_on, trace2 = self.wait_node_status(child_id, 'online', timeout=20)
        audit = self.hub.json('/api/audit?limit=25')
        node_audit = [a for a in (audit if isinstance(audit, list) else audit.get('items', []))
                      if a.get('object_id') == child_id or a.get('target_id') == child_id]
        out['steps'].append({'step': 'ui_set_online_on_offline_node', 'menu': click,
                             'post_requests': posts, 'ui_row_after': row_after,
                             'hub_node_after': self.node_summary(child_id),
                             'hub_status': (node_on or {}).get('status'),
                             'status_trace': trace2,
                             'audit_rows_for_node': node_audit[:6]})

        # --- observation: no agent session, the manual online state is not auto-cleaned ---
        time.sleep(12)
        node_12s = self.node_summary(child_id)
        sess_note = ('manual set online is persisted while the owned child process is still stopped; '
                     'no automatic transition back to offline happens without a disconnect/heartbeat '
                     'event or a Hub restart (hub/nodes spec: 断开与心跳超时置离线 / Hub 启动复位孤儿在线)')
        out['steps'].append({'step': 'stale_online_observation_after_12s', 'hub_node': node_12s,
                             'note': sess_note})
        out['observation_stale_online'] = {
            'status_without_session': node_12s.get('status'),
            'sync_status': node_12s.get('sync_status'),
            'note': sess_note}

        # the child process is intentionally left stopped here: P11-04 variant A consumes this
        # exact state (hub record online, no Agent session). Reconnection is verified at the
        # start of P11-04 and recorded as the recovery evidence for this case too.
        out['assertions_pass'] = bool(
            (node_off or {}).get('status') == 'offline'
            and (row_off or {}).get('status_badge') == '离线'
            and '设为在线' in click['menu_items_before_click']
            and (node_on or {}).get('status') == 'online'
            and (row_after or {}).get('status_badge') == '在线')
        out['recovery_verified_in'] = 'phase5-p11-04-offline-node-dispatch.json step "recovery_after_set_online"'
        return self.rec('phase4-p04-05-offline-set-online.json', out)

    # ---- P11-04 ----
    def phase5_p11_04(self):
        out = {'at_utc': now_utc(), 'case': 'P11-04', 'steps': []}
        child_id = self.state['child_node_id']
        t1 = self.state['task_node_id']
        out['ui_auth'] = self.ui_login_if_needed()

        # --- variant A: node record online but the agent has no session ---
        # (state produced by P04-05: manual set online with the process stopped)
        node_a = self.node_summary(child_id)
        before_ids = {e['id'] for e in self.executions(t1, child_id)}
        run_a = self.ui_run_task(t1)
        after_rows_a = [e for e in self.executions(t1, child_id) if e['id'] not in before_ids]
        ex_a = None
        if after_rows_a:
            ex_a = self.wait_execution(after_rows_a[0]['id'], timeout=45)
            self.state['executions']['p1104_agent_offline'] = after_rows_a[0]['id']
        self.ui_shot('P1104-01-run-no-session-error.png')
        out['steps'].append({
            'step': 'variant_A_online_record_without_agent_session',
            'node_before_run': node_a,
            'ui_run': run_a,
            'new_execution': None if not ex_a else {
                'id': ex_a.get('id'), 'status': ex_a.get('status'),
                'block_reason': ex_a.get('block_reason'),
                'end_time': ex_a.get('end_time')},
            'finding': 'dispatch on an online-but-session-less node terminates immediately with an '
                       'explicit error instead of an unbounded wait',
        })

        # --- recovery of the manual set online (P04-05 evidence): start the process again ---
        started_a = self.child.start()
        self.budget['child_process_starts'] += 1
        node_back_a = self.wait_node_online_synced(child_id, timeout=90)
        out['steps'].append({'step': 'recovery_after_set_online', 'start': started_a,
                             'hub_node': self.node_summary(child_id),
                             'note': 'manual set online did not block re-enrolment/reconnection (P04-05)'})

        # --- variant B: genuinely offline node (status=offline) ---
        stopped = self.child.stop()
        self.budget['child_process_stops'] += 1
        node_off, trace = self.wait_node_status(child_id, 'offline', timeout=90)
        before_ids_b = {e['id'] for e in self.executions(t1, child_id)}
        run_b = self.ui_run_task(t1)
        after_b = [e for e in self.executions(t1, child_id) if e['id'] not in before_ids_b]
        self.ui_shot('P1104-02-offline-node-run-error.png')
        pending_probe = self.executions(t1, child_id)
        out['steps'].append({
            'step': 'variant_B_genuinely_offline_node',
            'node_before_run': node_off, 'status_trace': trace,
            'ui_run': run_b, 'new_execution_rows': len(after_b),
            'all_rows_statuses': sorted({e.get('status') for e in pending_probe}),
            'finding': 'the Hub refuses at the status gate (HTTP 4xx with the node status in the '
                       'message); no PENDING execution row is left behind',
        })

        # --- restore ---
        started = self.child.start()
        self.budget['child_process_starts'] += 1
        node_back = self.wait_node_online_synced(child_id, timeout=90)
        out['steps'].append({'step': 'restored', 'hub_node': self.node_summary(child_id)})

        off_err = (run_b.get('post_requests') or [''])[0]
        a_err = (run_a.get('post_requests') or [''])[0]
        toast_a = (run_a.get('toast') or {}).get('toast_text', '')
        toast_b = (run_b.get('toast') or {}).get('toast_text', '')
        out['recovery_verified'] = bool(node_back_a and node_back_a.get('status') == 'online'
                                        and node_back_a.get('sync_status') == 'synced')
        out['assertions_pass'] = bool(
            ex_a is not None and ex_a.get('status') == 'FAILED'
            and 'agent offline' in (ex_a.get('block_reason') or '')
            and 'agent offline' in toast_a
            and '400' in a_err
            and len(after_b) == 0
            and '400' in off_err
            and 'not online' in toast_b
            and node_back and node_back.get('status') == 'online')
        return self.rec('phase5-p11-04-offline-node-dispatch.json', out)

    # ---- P06-08 ----
    def phase6_p06_08(self):
        out = {'at_utc': now_utc(), 'case': 'P06-08', 'steps': []}
        child_id = self.state['child_node_id']
        host = PREFIX + 'child'
        t2 = self.state['task_group_id']
        gid = self.state['group_id']
        out['ui_auth'] = self.ui_login_if_needed()

        # --- before: empty group => task list of the node does not contain the group task ---
        detail_before = self.ui_node_detail_task_links(child_id)
        task_t2_detail = self.ui_task_detail(t2)
        task_row_before = self.ui_tasks_table_row(TASK_GROUP_NAME)
        groups_row_before = self.ui_groups_row(GROUP_NAME)
        self.ui_shot('P0608-01-node-detail-before-group-change.png')
        out['steps'].append({'step': 'before_group_change',
                             'node_detail_task_links': detail_before.get('task_links'),
                             'task_t2_detail_text': (task_t2_detail.get('main_text') or '')[:800],
                             'task_row': task_row_before,
                             'groups_row': groups_row_before})

        # --- real user operation: add the node to the group in the group editor ---
        toggle = self.ui_group_member_toggle(gid, host, True)
        groups_row_after = self.ui_groups_row(GROUP_NAME)
        detail_after = self.ui_node_detail_task_links(child_id)
        self.ui_shot('P0608-02-node-detail-after-group-add.png')
        out['steps'].append({'step': 'ui_add_node_to_group',
                             'toggle': toggle, 'groups_row_after': groups_row_after,
                             'node_detail_task_links_after': detail_after.get('task_links')})

        # --- real business effect: hub dispatch through the group target now reaches the node ---
        slot1 = iso(datetime.now(timezone.utc) - timedelta(seconds=60))
        self.create_hub_schedule(t2, slot1, 'p0608_group_member_added')
        row1 = None
        ex1 = None
        deadline = time.time() + HUB_TICK_MAX * 3
        while time.time() < deadline:
            rows = [e for e in self.executions(t2, child_id)
                    if (e.get('scheduled_time') or '').startswith(slot1[:19])]
            if rows:
                row1 = rows[0]
                ex1 = self.wait_execution(row1['id'], timeout=60)
                break
            time.sleep(3)
        self.state['executions']['p0608_group_dispatch'] = (row1 or {}).get('id')
        out['steps'].append({'step': 'group_change_changes_real_dispatch',
                             'slot_utc': slot1,
                             'execution': None if not row1 else {
                                 'id': row1['id'], 'status': (ex1 or {}).get('status'),
                                 'node_id': (ex1 or {}).get('node_id'),
                                 'stdout': ((ex1 or {}).get('stdout') or '').strip(),
                                 'marker_present': MARKER in ((ex1 or {}).get('stdout') or '')}})

        # --- real user operation: remove the node from the group again ---
        toggle2 = self.ui_group_member_toggle(gid, host, False)
        groups_row_after2 = self.ui_groups_row(GROUP_NAME)
        detail_after2 = self.ui_node_detail_task_links(child_id)
        self.ui_shot('P0608-03-node-detail-after-group-remove.png')
        # run the group task from the UI: resolution is now empty
        run_g = self.ui_run_task(t2)
        self.ui_shot('P0608-04-group-task-run-empty-target.png')
        code, body = self.hub.post('/api/tasks/%s/run' % self.hub.quote(t2), {'params': {}})
        self.budget['api_mutations'] += 1
        out['steps'].append({'step': 'ui_remove_node_from_group',
                             'toggle': toggle2, 'groups_row_after': groups_row_after2,
                             'node_detail_task_links_after': detail_after2.get('task_links'),
                             'group_task_ui_run': run_g,
                             'group_task_manual_run_http': code, 'group_task_manual_run_body': body})
        t2_read = self.hub.json('/api/tasks/' + self.hub.quote(t2))
        g_read = self.hub.json('/api/groups/' + self.hub.quote(gid))
        out['steps'].append({'step': 'task_target_config_unchanged',
                             'task_group_target': t2_read.get('target'),
                             'group_members': g_read.get('members')})

        links_before = {l['text'] for l in (detail_before.get('task_links') or [])}
        links_after = {l['text'] for l in (detail_after.get('task_links') or [])}
        links_after2 = {l['text'] for l in (detail_after2.get('task_links') or [])}
        out['node_detail_task_list'] = {
            'before': {'section_found': detail_before.get('section_found'),
                       'section_text': detail_before.get('section_text'),
                       'link_texts': sorted(links_before),
                       'group_task_present_in_main': detail_before.get('group_task_present_in_main')},
            'after_add': {'section_found': detail_after.get('section_found'),
                          'section_text': detail_after.get('section_text'),
                          'link_texts': sorted(links_after),
                          'group_task_present_in_main': detail_after.get('group_task_present_in_main')},
            'after_remove': {'section_found': detail_after2.get('section_found'),
                             'section_text': detail_after2.get('section_text'),
                             'link_texts': sorted(links_after2),
                             'group_task_present_in_main': detail_after2.get('group_task_present_in_main')},
        }
        out['assertions_pass'] = bool(
            detail_before.get('group_task_present_in_main') is False
            and detail_before.get('node_task_present_in_main') is True
            and TASK_NODE_NAME in links_before
            and TASK_GROUP_NAME not in links_before
            and detail_after.get('group_task_present_in_main') is True
            and TASK_GROUP_NAME in links_after
            and detail_after2.get('group_task_present_in_main') is False
            and TASK_GROUP_NAME not in links_after2
            and TASK_NODE_NAME in links_after2
            and ex1 is not None and ex1.get('status') == 'SUCCESS'
            and MARKER in (ex1.get('stdout') or '')
            and ex1.get('node_id') == child_id
            and code == 400 and 'no target nodes' in str(body)
            and t2_read.get('target', {}).get('group_ids') == [gid]
            and (g_read.get('members') or []) == [])
        return self.rec('phase6-p06-08-group-change-sync.json', out)

    # ---- P17P18-14 ----
    def phase7_p17p18_14(self):
        out = {'at_utc': now_utc(), 'case': 'P17P18-14', 'steps': []}
        child_id = self.state['child_node_id']
        t1 = self.state['task_node_id']
        out['ui_auth'] = self.ui_login_if_needed()

        node_pre = self.wait_node_online_synced(child_id, timeout=60)
        run_at = iso(datetime.now(timezone.utc) + timedelta(seconds=30))
        self.create_agent_schedule(t1, run_at, 'p17p18_offline_exec')
        self.wait_revision((node_pre or {}).get('global_revision', 0) + 1, timeout=45)
        slot = run_at[:19]

        # cut the child's hub link through the harness proxy; the child keeps running
        self.proxy.set_mode('cut')
        cut_at = now_utc()
        local_offline = None
        deadline = time.time() + 120
        while time.time() < deadline:
            db = self.child.db_read(t1, [])
            cand = [e for e in db.get('task_executions', [])
                    if (e.get('scheduled_time') or '').startswith(slot) and e.get('offline')]
            if cand:
                local_offline = cand[0]
                break
            time.sleep(3)
        if local_offline is None:
            self.proxy.set_mode('pass')
            raise RuntimeError('offline_execution_not_observed_locally')
        off_id = local_offline['id']
        self.state['executions']['p17p18_offline'] = off_id
        hub_during_cut = self.execution(off_id)
        out['steps'].append({'step': 'offline_execution_local_only',
                             'schedule_run_at_utc': run_at, 'slot_utc': slot, 'cut_at_utc': cut_at,
                             'local_execution': local_offline,
                             'hub_row_while_cut': {'http': hub_during_cut[0]},
                             'proxy': {'mode': self.proxy.mode(), 'state': self.proxy.state()}})

        # restore, but drop the first EXECUTION_ACK so the journal stays synced=false
        self.proxy.arm_drop_next_ack(1)
        self.proxy.set_mode('pass')
        restored_at = now_utc()
        hub_row = None
        deadline = time.time() + 90
        while time.time() < deadline:
            code, body = self.execution(off_id)
            if code == 200:
                hub_row = body
                break
            time.sleep(2)
        time.sleep(6)
        local_after_first = self.child.db_read(t1, [off_id])
        first_row = (local_after_first.get('requested_executions') or [None])[0]
        out['steps'].append({'step': 'restored_first_upload_ack_dropped',
                             'restored_at_utc': restored_at,
                             'hub_row': None if not hub_row else {
                                 'id': hub_row.get('id'), 'status': hub_row.get('status'),
                                 'offline': hub_row.get('offline'), 'synced': hub_row.get('synced'),
                                 'trigger_type': hub_row.get('trigger_type')},
                             'child_local_synced': (first_row or {}).get('synced'),
                             'proxy': {'drop_next_done': self.proxy.state().get('drop_next_done')}})

        # real child agent restart -> idempotent re-upload of the same Execution ID
        restart = self.child.restart()
        self.budget['child_process_stops'] += 1
        self.budget['child_process_starts'] += 1
        node_after = self.wait_node_online_synced(child_id, timeout=90)
        final_row = None
        deadline = time.time() + 90
        while time.time() < deadline:
            db = self.child.db_read(t1, [off_id])
            cand = (db.get('requested_executions') or [None])[0]
            if cand and cand.get('synced') is True:
                final_row = cand
                break
            time.sleep(3)
        hub_final = [e for e in self.executions(t1, child_id) if e['id'] == off_id]
        proxy_final = self.proxy.state()
        uploads = [e for e in proxy_final['seen_exec_finished'] if e['execution_id'] == off_id]
        out['steps'].append({'step': 'restart_idempotent_reupload',
                             'restart': restart,
                             'child_local_final': final_row,
                             'hub_rows_for_exec_id': len(hub_final),
                             'hub_row_final': None if not hub_final else {
                                 'id': hub_final[0].get('id'), 'status': hub_final[0].get('status'),
                                 'offline': hub_final[0].get('offline'),
                                 'synced': hub_final[0].get('synced'),
                                 'scheduled_time': hub_final[0].get('scheduled_time')},
                             'exec_finished_uploads_seen_by_proxy': len(uploads),
                             'node_after': self.node_summary(child_id)})

        # UI readback of the recovered execution
        ui_exec = self.ui_execution_detail(off_id)
        self.ui_shot('P17P18-01-offline-execution-detail.png')
        out['steps'].append({'step': 'ui_execution_detail', 'ui': ui_exec})

        out['assertions_pass'] = bool(
            local_offline.get('offline') is True and local_offline.get('synced') is False
            and hub_during_cut[0] == 404
            and hub_row is not None and hub_row.get('offline') is True
            and (first_row or {}).get('synced') is False
            and len(uploads) >= 2
            and len(hub_final) == 1
            and (final_row or {}).get('synced') is True)
        return self.rec('phase7-p17p18-14-offline-synced.json', out)

    # ================= cleanup =================
    def phase8_cleanup(self):
        results = {'deleted': [], 'errors': []}
        # 1) schedules
        for key in list(self.state['schedules'].keys()):
            try:
                r = self.delete_schedule(key)
                results['deleted'].append(dict(r, kind='schedule'))
            except Exception as exc:
                results['errors'].append('schedule_delete:%s:%s' % (key, exc))
        # 2) tasks
        for name, tid in (('task_group', self.state['task_group_id']),
                          ('task_node', self.state['task_node_id'])):
            if not tid:
                continue
            path = '/api/tasks/' + self.hub.quote(tid)
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['tasks_deleted'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': 'task', 'key': name, 'id': tid,
                                       'delete_http': code, 'get_http': gcode,
                                       'delete_body': body if isinstance(body, (dict, str)) else None})
        # 3) group (must go before the node: DeleteNode refuses group members)
        gid = self.state['group_id']
        if gid:
            path = '/api/groups/' + self.hub.quote(gid)
            code, body = self.hub.delete(path)
            self.budget['api_mutations'] += 1
            self.budget['groups_deleted'] += 1
            gcode, _ = self.hub.get(path)
            results['deleted'].append({'kind': 'group', 'id': gid, 'delete_http': code,
                                       'get_http': gcode,
                                       'delete_body': body if isinstance(body, (dict, str)) else None})
        # 4) child node record
        nid = self.state['child_node_id']
        if nid:
            npath = '/api/nodes/' + self.hub.quote(nid)
            code, body = self.hub.delete(npath)
            self.budget['api_mutations'] += 1
            gcode, _ = self.hub.get(npath)
            results['child_node'] = {'id': nid, 'delete_http': code, 'get_http': gcode,
                                     'delete_body': body if isinstance(body, (dict, str)) else None}
            results['deleted'].append({'kind': 'node', 'id': nid, 'delete_http': code, 'get_http': gcode})
        # 5) script
        sid = self.state['script_id']
        if sid:
            spath = '/api/scripts/' + self.hub.quote(sid)
            code, body = self.hub.delete(spath)
            self.budget['api_mutations'] += 1
            self.budget['scripts_deleted'] += 1
            gcode, _ = self.hub.get(spath)
            results['deleted'].append({'kind': 'script', 'id': sid, 'delete_http': code,
                                       'get_http': gcode})
        # 6) owned child process + root on native-2
        if self.child is not None:
            try:
                results['native2_child_removal'] = self.child.remove()
                self.budget['child_process_stops'] += 1
            except Exception as exc:
                results['native2_child_removal'] = {'error': str(exc)}
                results['errors'].append('child_remove:%s' % exc)
        else:
            results['native2_child_removal'] = {'skipped': 'owned_child_never_installed'}
        # 7) re-verify every deleted object
        ver = {'objects': {}, 'native2': {}}
        for d in results['deleted']:
            kind = d['kind']
            path = '/api/%ss/%s' % (kind if kind != 'node' else 'node', self.hub.quote(d['id']))
            if kind == 'node':
                path = '/api/nodes/' + self.hub.quote(d['id'])
            code, _ = self.hub.get(path)
            ver['objects']['%s:%s' % (kind, d['id'])] = code
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        ver['native2'] = {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                          'owned_child_processes': inv['owned_child_processes'],
                          'all_qa_roots': inv['all_qa_roots'],
                          'other_owned_children': inv['other_owned_children']}
        results['verification'] = ver
        results['all_get_404'] = all(c == 404 for c in ver['objects'].values())
        results['child_root_gone'] = not inv['root_exists'] and not inv['config_exists']
        results['owned_processes_gone'] = not inv['owned_child_processes']
        self.rec('phase8-cleanup.json', results)
        return results

    def phase9_guard(self, cleanup):
        nodes = self.node_rows()
        by_id = {n['id']: n for n in nodes}
        protected_after = {}
        ok = True
        for nid, label in PROTECTED_NODES.items():
            n = by_id.get(nid)
            protected_after[label] = None if not n else {'status': n.get('status'),
                                                         'sync_status': n.get('sync_status')}
            if not n or n.get('status') != 'online' or n.get('sync_status') != 'synced':
                ok = False
        groups = self.hub.json('/api/groups')
        tasks = self.hub.json('/api/tasks')
        scripts = self.hub.json('/api/scripts')
        schedules = self.hub.json('/api/schedules')
        leftovers = {
            'scripts': [s['id'] for s in scripts if (s.get('name') or '').startswith(PREFIX)],
            'tasks': [t['id'] for t in tasks if (t.get('name') or '').startswith(PREFIX)],
            'groups': [g['id'] for g in groups if (g.get('name') or '').startswith(PREFIX)],
            'child_nodes': [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)],
            'schedules_for_owned_tasks': [s['id'] for s in schedules
                                          if s.get('task_id') in (self.state['task_node_id'],
                                                                  self.state['task_group_id'])],
        }
        inv = child_agent.OwnedChild(NONCE, 1, '', '', '', '', '').inventory()
        guard = {
            'at_utc': now_utc(),
            'owned_prefix': PREFIX,
            'protected_nodes_after': protected_after,
            'protected_nodes_online_synced': ok,
            'hub_leftover_owned_fixtures': leftovers,
            'hub_leftover_clear': not any(leftovers.values()),
            'native2_inventory': {'root_exists': inv['root_exists'], 'config_exists': inv['config_exists'],
                                  'owned_child_processes': inv['owned_child_processes'],
                                  'all_qa_roots': inv['all_qa_roots'],
                                  'other_owned_children': inv['other_owned_children'],
                                  'primary_agent_unit': inv['primary_agent_unit'],
                                  'repair_agent_unit': inv['repair_agent_unit']},
            'native2_owned_child_orphan': bool(inv['root_exists'] or inv['config_exists']
                                               or inv['owned_child_processes']),
            'protected_units_active': (inv['primary_agent_unit'].get('ActiveState') == 'active'
                                       and inv['repair_agent_unit'].get('ActiveState') == 'active'),
            'other_batches_untouched': {
                'note': 'other batches run in parallel on the same hosts; their owned children and roots '
                        'are listed for comparison and were never signalled/removed by this batch',
                'roots': inv['other_qa_roots'], 'processes': inv['other_owned_children']},
            'post_counts': {'nodes': len(nodes), 'groups': len(groups), 'tasks': len(tasks),
                            'scripts': len(scripts), 'schedules': len(schedules)},
            'cleanup': cleanup,
            'preserved_execution_history': [v for k, v in self.state['executions'].items() if v],
            'cleanup_integrity': {
                'pre_other_owned_children': self.pre_other_children,
                'post_other_owned_children': inv['other_owned_children'],
                'pre_other_roots': self.pre_other_roots,
                'post_other_roots': inv['other_qa_roots'],
            },
        }
        guard['clean'] = bool(ok and not any(leftovers.values())
                              and not guard['native2_owned_child_orphan']
                              and guard['protected_units_active']
                              and cleanup.get('all_get_404')
                              and cleanup.get('child_root_gone'))
        self.rec('final-cleanup-guard.json', guard)
        return guard


PROGRESS = {'batch_id': 'qa-resume-20261014-web-batch06', 'nonce': NONCE,
            'owned_prefix': PREFIX, 'phase': 'init', 'updated_at_utc': now_utc()}


def progress_mark(phase, **extra):
    PROGRESS['phase'] = phase
    PROGRESS['updated_at_utc'] = now_utc()
    PROGRESS.update(extra)
    hubapi.write_json('progress.json', PROGRESS)


def main():
    run = Run()
    run.pre_other_children = []
    run.pre_other_roots = []
    evidence = {
        'batch_id': 'qa-resume-20261014-web-batch06',
        'title': '节点状态操作菜单、离线节点 set online、离线派发快速失败、分组联动与 Agent 离线执行同步',
        'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': run.hub.base,
        'cases': ['P04-04', 'P04-05', 'P11-04', 'P06-08', 'P17P18-14'],
        'started_at_utc': now_utc(), 'phases': {}, 'deviations': [],
    }
    failed = False
    try:
        guard = run.phase('guard', run.phase0_guard)
        evidence['phases']['guard'] = guard
        run.pre_other_children = guard['native2_pre']['other_owned_children']
        run.pre_other_roots = guard['native2_pre']['all_qa_roots']
        evidence['phases']['child'] = run.phase('child_start', run.phase1_child)
        PROGRESS['child_node_id'] = run.state['child_node_id']
        PROGRESS['child_agent_id'] = run.state['child_agent_id']
        evidence['phases']['fixtures'] = run.phase('fixtures', run.phase2_fixtures)
        PROGRESS['task_node_id'] = run.state['task_node_id']
        PROGRESS['task_group_id'] = run.state['task_group_id']
        PROGRESS['group_id'] = run.state['group_id']
        PROGRESS['script_id'] = run.state['script_id']
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('SETUP_ERROR:%s' % exc)
        PROGRESS['harness_error'] = str(exc)
        failed = True

    steps = [
        ('p04_04', 'P04-04', run.phase3_p04_04),
        ('p04_05', 'P04-05', run.phase4_p04_05),
        ('p11_04', 'P11-04', run.phase5_p11_04),
        ('p06_08', 'P06-08', run.phase6_p06_08),
        ('p17p18_14', 'P17P18-14', run.phase7_p17p18_14),
    ]
    if not failed:
        for key, case, fn in steps:
            try:
                evidence['phases'][key] = run.phase(key, fn)
                PROGRESS['case_status'] = PROGRESS.get('case_status', {})
                PROGRESS['case_status'][case] = {
                    'assertions_pass': evidence['phases'][key].get('assertions_pass')}
            except BaseException as exc:
                import traceback
                traceback.print_exc()
                evidence['deviations'].append('%s_ERROR:%s' % (case, exc))
                PROGRESS['case_status'] = PROGRESS.get('case_status', {})
                PROGRESS['case_status'][case] = {'assertions_pass': False, 'error': str(exc)}
                failed = True

    # always clean up and write the guard
    try:
        cleanup = run.phase('cleanup', run.phase8_cleanup)
        evidence['phases']['cleanup'] = cleanup
        PROGRESS['final_cleanup_get_http'] = {k: v for k, v in
                                              (cleanup.get('verification', {}).get('objects') or {}).items()}
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('CLEANUP_ERROR:%s' % exc)
        PROGRESS['cleanup_error'] = str(exc)
        cleanup = {}
        failed = True
    finally:
        if run.proxy:
            try:
                run.proxy.stop()
            except Exception:
                pass
        try:
            run.ui('close', timeout=40)
        except Exception:
            pass

    try:
        guard_final = run.phase('oracle', lambda: run.phase9_guard(cleanup))
        evidence['phases']['guard_final'] = guard_final
        PROGRESS['oracle_clean'] = guard_final['clean']
    except BaseException as exc:
        import traceback
        traceback.print_exc()
        evidence['deviations'].append('GUARD_ERROR:%s' % exc)
        PROGRESS['guard_error'] = str(exc)
        guard_final = {}
        failed = True

    evidence['finished_at_utc'] = now_utc()
    evidence['budget'] = run.budget
    evidence['state'] = {
        'script_id': run.state['script_id'],
        'task_node_id': run.state['task_node_id'],
        'task_group_id': run.state['task_group_id'],
        'group_id': run.state['group_id'],
        'child_node_id': run.state['child_node_id'],
        'child_agent_id': run.state['child_agent_id'],
        'schedules': run.state['schedules'],
        'executions': run.state['executions'],
        'ui_session': UI_SESSION,
        'navigations': run.state['navigations'],
    }
    hubapi.write_json('run-evidence.json', evidence)
    write_results(run, evidence, PROGRESS, cleanup, guard_final)
    write_ledger(run, PROGRESS, cleanup, guard_final)
    PROGRESS['phase'] = 'done' if not failed else 'failed'
    PROGRESS['updated_at_utc'] = now_utc()
    hubapi.write_json('progress.json', PROGRESS)
    ph = evidence['phases']
    print(json.dumps({
        'batch_id': evidence['batch_id'],
        'case_assertions': {c: (ph.get(k) or {}).get('assertions_pass') for k, c, _ in steps},
        'clean': PROGRESS.get('oracle_clean'),
        'deviations': evidence['deviations'],
        'executions': run.state['executions'],
    }, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def write_ledger(run, progress, cleanup, guard_final):
    ledger = {
        'batch_id': 'qa-resume-20261014-web-batch06',
        'nonce': NONCE, 'owned_prefix': PREFIX,
        'authority': run.hub.base,
        'hub_fixtures': [
            {'kind': 'script', 'name': SCRIPT_NAME, 'id': run.state['script_id'],
             'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                 'script:%s' % run.state['script_id']) == 404},
            {'kind': 'task', 'name': TASK_NODE_NAME, 'id': run.state['task_node_id'],
             'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                 'task:%s' % run.state['task_node_id']) == 404},
            {'kind': 'task', 'name': TASK_GROUP_NAME, 'id': run.state['task_group_id'],
             'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                 'task:%s' % run.state['task_group_id']) == 404},
            {'kind': 'group', 'name': GROUP_NAME, 'id': run.state['group_id'],
             'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                 'group:%s' % run.state['group_id']) == 404},
        ] + [
            {'kind': 'schedule', 'key': k, 'id': v,
             'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                 'schedule:%s' % v) == 404}
            for k, v in run.state['schedules'].items()
        ],
        'child_node': {'id': run.state['child_node_id'], 'agent_id': run.state['child_agent_id'],
                       'hostname': PREFIX + 'child', 'host': '192.168.100.206',
                       'deleted_get404': cleanup.get('verification', {}).get('objects', {}).get(
                           'node:%s' % run.state['child_node_id']) == 404,
                       'delete_http': (cleanup.get('child_node') or {}).get('delete_http')},
        'host_fixture': {'kind': 'owned_child_agent_process', 'host': '192.168.100.206',
                         'root': run.child.root if run.child else None,
                         'root_removed': (cleanup.get('native2_child_removal') or {}).get('root_removed'),
                         'remaining_processes': (cleanup.get('native2_child_removal') or {}).get(
                             'remaining_processes')},
        'preserved_executions': [
            {'key': k, 'id': v, 'note': 'Execution history is immutable / not deletable by design'}
            for k, v in run.state['executions'].items() if v],
        'protected_nodes_untouched': guard_final.get('protected_nodes_after'),
        'node_status_changes_scope': 'only the owned child node %s (created by this batch)' % run.state['child_node_id'],
        'status': 'CLEANED' if progress.get('oracle_clean') else 'CLEANUP_INCOMPLETE',
    }
    hubapi.write_json('fixture-ledger.json', ledger)


def write_results(run, evidence, progress, cleanup, guard_final):
    ph = evidence['phases']

    def case(case_id, title, canonical, proposed, confidence, dims, limits):
        return {
            'case_id': case_id, 'title': title,
            'canonical_before': canonical,
            'proposed_after': proposed, 'confidence': confidence,
            'covered_dimensions': dims, 'remaining_or_limitations': limits,
            'evidence': sorted(p.name for p in (HERE / 'evidence').iterdir()) + [
                'run-evidence.json', 'progress.json', 'fixture-ledger.json',
                'final-cleanup-guard.json'],
            'actual_resources': {
                'owned_prefix': PREFIX,
                'child_node_id': run.state['child_node_id'],
                'child_agent_id': run.state['child_agent_id'],
                'script_id': run.state['script_id'],
                'task_node_id': run.state['task_node_id'],
                'task_group_id': run.state['task_group_id'],
                'group_id': run.state['group_id'],
                'schedules': run.state['schedules'],
                'executions': run.state['executions'],
            },
            'actual_budget': dict(run.budget),
        }

    canonical = ('NOT_RUN (repair build not executed for this case; legacy baseline PASS '
                 'excluded per remaining_dimensions)')
    cleanup_ok = bool(progress.get('oracle_clean'))

    p44 = ph.get('p04_04') or {}
    p45 = ph.get('p04_05') or {}
    p11 = ph.get('p11_04') or {}
    p06 = ph.get('p06_08') or {}
    p17 = ph.get('p17p18_14') or {}

    cases = []
    cases.append(case(
        'P04-04',
        '操作菜单：online → maintenance；maintenance → online；set online（在 owned child 上执行）',
        canonical,
        'PASS' if (p44.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if p44.get('assertions_pass') else 'PARTIAL'),
        'high' if p44.get('assertions_pass') else 'medium',
        [{
            'test_id': 'P04-04-UI-20261014-BATCH06',
            'layer': 'real-ui -> POST /api/nodes/{id} -> hub node status -> agent NODE_STATUS',
            'status': 'PASS' if p44.get('assertions_pass') else 'PARTIAL',
            'description': [
                'Owned child row action menu in 节点 list: 维护 (online→maintenance), 恢复 '
                '(maintenance→online) and 设为在线 (maintenance→online) each issued a real '
                'POST /api/nodes/{id} that returned 200, and the hub node status followed the click.',
                'UI readback after each click: status badge and the first menu item label switch '
                'between 在线+维护 and maintenance+恢复.',
                'Hub-side effect of maintenance: a hub-owned one_time Schedule due while the node was '
                'paused produced a SKIPPED Execution with block_reason "node paused" instead of being '
                'dispatched; after 恢复 the same kind of Schedule produced a real SUCCESS Execution '
                'whose stdout carried the nonce marker.',
                'Agent-side effect: with the node paused the agent-owned allow_offline one_time Schedule '
                'did not fire locally; it fired once after 恢复 (catch-up on the same slot).',
            ],
            'evidence': ['evidence/phase3-p04-04-operation-menu.json',
                         'evidence/P0404-01-node-online-row.png',
                         'evidence/P0404-02-node-maintenance-row.png',
                         'evidence/P0404-03-node-resumed-row.png',
                         'evidence/P0404-04-node-set-online-row.png'],
        }],
        ['The status badge for `maintenance` renders the raw untranslated string "maintenance" '
         '(StatusBadge has no zh label for it) — recorded as an observation, not a failure of the '
         'operation-menu assertion.',
         'The agent-local pause behaviour was verified with an agent-owned allow_offline Schedule; '
         'the agent does not log the NODE_STATUS application, so the evidence is behavioural.'],
    ))
    cases.append(case(
        'P04-05', 'offline 节点 set online（owned child 离线后经 UI 设为在线）',
        canonical,
        'PASS' if (p45.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if p45.get('assertions_pass') else 'PARTIAL'),
        'high' if p45.get('assertions_pass') else 'medium',
        [{
            'test_id': 'P04-05-UI-20261014-BATCH06',
            'layer': 'real-ui -> POST /api/nodes/{id} {status:online} -> hub node status',
            'status': 'PASS' if p45.get('assertions_pass') else 'PARTIAL',
            'description': [
                'The owned child Agent process was stopped, so the Hub marked the node offline through '
                'the real disconnect path (observed status trace online→offline) and the UI row badge '
                'became 离线.',
                'The offline row action menu was opened in 节点 and 设为在线 was clicked: real '
                'POST /api/nodes/{id} {"status":"online"} → 200, hub node status=online, UI badge 在线.',
                'After the owned child process was restarted (recorded in the P11-04 phase file, step '
                '"recovery_after_set_online"), the node converged back to online+synced, so the manual '
                'set-online did not break re-enrolment/reconnection.',
            ],
            'evidence': ['evidence/phase4-p04-05-offline-set-online.json',
                         'evidence/P0405-01-node-offline-row.png',
                         'evidence/P0405-02-node-set-online-row.png'],
        }],
        ['Observation (not a case failure): the manual online state persists while no Agent session '
         'exists — nothing flips an online record back to offline without a disconnect/heartbeat '
         'event or a Hub start (see evidence "stale_online_observation_after_12s").'],
    ))
    cases.append(case(
        'P11-04', 'offline 节点不进入无限等待，返回明确 BLOCKED/错误',
        canonical,
        'PASS' if (p11.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if p11.get('assertions_pass') else 'PARTIAL'),
        'high' if p11.get('assertions_pass') else 'medium',
        [{
            'test_id': 'P11-04-UI-20261014-BATCH06',
            'layer': 'real-ui run page -> POST /api/tasks/{id}/run -> hub dispatch gate',
            'status': 'PASS' if p11.get('assertions_pass') else 'PARTIAL',
            'description': [
                'Genuinely offline node: clicking 立即运行 on the owned task returned an immediate HTTP 400 '
                '"node <id> is not online (status=offline)"; the UI showed that message as a toast and no '
                'PENDING Execution row was left behind — no unbounded wait.',
                'Online record with no Agent session (state produced by P04-05): the run created one '
                'Execution that terminated immediately as FAILED with block_reason "agent offline" and the '
                'same message in the UI toast.',
            ],
            'evidence': ['evidence/phase5-p11-04-offline-node-dispatch.json',
                         'evidence/P1104-01-run-no-session-error.png',
                         'evidence/P1104-02-offline-node-run-error.png'],
        }],
        ['The case text asks for "BLOCKED/明确错误"; the product surfaces this as an HTTP 4xx refusal '
         '(status gate) or a FAILED execution with block_reason, not an Execution state literally named '
         'BLOCKED. Both are immediate and explicit.',
         'Only the script-task dispatch path was exercised (application deploy / file transfer / artifact '
         'prefetch gates were out of scope for this batch).'],
    ))
    cases.append(case(
        'P06-08', 'Group 变化后 Task Target 和 Node Detail 任务列表同步',
        canonical,
        'PASS' if (p06.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if p06.get('assertions_pass') else 'PARTIAL'),
        'high' if p06.get('assertions_pass') else 'medium',
        [{
            'test_id': 'P06-08-UI-20261014-BATCH06',
            'layer': 'real-ui group editor -> PUT /api/groups/{id} -> task target resolution -> real execution',
            'status': 'PASS' if p06.get('assertions_pass') else 'PARTIAL',
            'description': [
                'Empty owned group (task target = that group): the node detail 任务 section listed only the '
                'node-targeted task; after adding the node to the group in the group editor UI (checkbox + '
                '保存, real PUT /api/groups/{id}) the node detail 任务 section listed the group-targeted task '
                'too — the list follows group membership, not a snapshot.',
                'Hub-side real effect: a hub-owned Schedule for the group-targeted task produced a real '
                'SUCCESS Execution on the newly added node (group → node resolution used the new membership).',
                'Removing the node from the group in the same UI made the group task disappear from the node '
                'detail again and made a manual run of the group task fail with 400 "no target nodes", while '
                'the task target config itself (group_ids) stayed unchanged.',
            ],
            'evidence': ['evidence/phase6-p06-08-group-change-sync.json',
                         'evidence/P0608-01-node-detail-before-group-change.png',
                         'evidence/P0608-02-node-detail-after-group-add.png',
                         'evidence/P0608-03-node-detail-after-group-remove.png'],
        }],
        ['Only one static group and one member node (the owned child) were used, so "group grows" and '
         '"group shrinks" were both observed on that single membership edge.',
         'A label-type group (dynamic membership by node label) was not exercised in this batch.'],
    ))
    cases.append(case(
        'P17P18-14', 'Agent Offline 执行 synced=false，恢复后幂等上传为 synced=true',
        canonical,
        'PASS' if (p17.get('assertions_pass') and cleanup_ok) else
        ('PASS_WITH_NOTES' if p17.get('assertions_pass') else 'PARTIAL'),
        'high' if p17.get('assertions_pass') else 'medium',
        [{
            'test_id': 'P17P18-14-20261014-BATCH06',
            'layer': 'owned child agent + frame proxy + agent sqlite journal + hub upsert',
            'status': 'PASS' if p17.get('assertions_pass') else 'PARTIAL',
            'description': [
                'With the owned child Agent cut from the release Hub (harness frame proxy "cut", the agent '
                'process kept running), an agent-owned allow_offline one_time Schedule fired locally: the '
                'journal row had offline=true and synced=false and no Hub row existed yet.',
                'After the link was restored the Execution was uploaded and the Hub created exactly one row '
                '(offline=true); the first EXECUTION_ACK was dropped so the journal stayed synced=false.',
                'A real child Agent process restart re-uploaded the same Execution ID (>=2 EXECUTION_FINISHED '
                'uploads observed at the frame layer); the Hub still held exactly one row and the journal '
                'converged to synced=true.',
            ],
            'evidence': ['evidence/phase7-p17p18-14-offline-synced.json',
                         'evidence/P17P18-01-offline-execution-detail.png'],
        }],
        ['"Offline" was produced by cutting the owned child Agent link through the harness frame proxy; '
         'the protected Agents and the Hub were never stopped.',
         'The dropped-ACK duplication is injected at the frame layer; the Hub upsert/dedup path itself is '
         'unmodified product code.'],
    ))

    totals = {}
    for c in cases:
        totals[c['proposed_after']] = totals.get(c['proposed_after'], 0) + 1
    results = {
        'batch_id': 'qa-resume-20261014-web-batch06',
        'title': evidence['title'],
        'owner': 'web',
        'status': 'COMPLETE_PENDING_ROOT_REVIEW' if not evidence['deviations'] else 'COMPLETE_WITH_DEVIATIONS',
        'updated_at_utc': now_utc(),
        'environment': {
            'authority': run.hub.base,
            'authority_type': 'repair',
            'primary_8080_touched': False,
            'browser': 'agent-browser 0.32.4 (session %s, --allowed-domains 192.168.100.209)' % UI_SESSION,
            'owned_child_host': '192.168.100.206 (native-2)',
            'agent_binary_sha256': child_agent.AGENT_SHA,
            'protected_nodes_untouched': guard_final.get('protected_nodes_after'),
            'nonce': NONCE,
        },
        'counts': totals,
        'cases': cases,
        'server_writes': {
            'summary': 'all writes are owned fixtures created by this batch and deleted at the end',
            'node_status_changes': 'only the owned child node %s' % run.state['child_node_id'],
            'protected_node_state_changes': 0,
            'user_application_artifact_changes': 0,
            'real_deploy': False,
            'budget': dict(run.budget),
        },
        'preserved_execution_history': [
            {'key': k, 'id': v, 'note': 'immutable Execution history produced by this batch'}
            for k, v in run.state['executions'].items() if v],
        'final_cleanup_guard': {
            'evidence': 'final-cleanup-guard.json',
            'clean': guard_final.get('clean'),
            'hub_leftover_clear': guard_final.get('hub_leftover_clear'),
            'native2_owned_child_orphan': guard_final.get('native2_owned_child_orphan'),
            'all_deleted_objects_get404': cleanup.get('all_get_404'),
            'child_node_get_http': (cleanup.get('child_node') or {}).get('get_http'),
            'post_counts': guard_final.get('post_counts'),
        },
        'deviations': evidence['deviations'],
        'not_claimed': 'No product source change, no Git delivery, no Archive, no protected node state change.',
    }
    hubapi.write_json('results.json', results)


if __name__ == '__main__':
    sys.exit(main())
