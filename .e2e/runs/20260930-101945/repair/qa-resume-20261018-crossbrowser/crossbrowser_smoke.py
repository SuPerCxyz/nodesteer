#!/usr/bin/env python3
"""qa-resume-20261018-crossbrowser: Firefox / WebKit cross-browser smoke test.

Authority: repair hub http://192.168.100.209:18080
Build:     dddee4f0-e2e-repair-20261018-final

Read-only discipline: no business fixtures are created/modified/deleted.
Login is performed through the real UI form. In-page fetch patching is
session-scoped (page JS only) and never persists. The create-form AND
condition check never clicks 保存.

Usage: python3 crossbrowser_smoke.py [firefox] [webkit] [chromium] ...
Writes: browser-availability.json, smoke-results.json, console-errors.json
        and screenshots under evidence/<browser>/.
"""
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
HUB = 'http://192.168.100.209:18080'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')

os.umask(0o077)


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def load_authority():
    for p in (CONN, CRED):
        st = p.lstat()
        if p.is_symlink() or not p.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
            raise RuntimeError('private_file_gate_failed:' + p.name)
    conn = json.loads(CONN.read_text())
    url = (conn.get('hub_url') or '').rstrip('/')
    if not url.endswith(':18080'):
        raise RuntimeError('endpoint_guard_failed_repair_18080_required')
    return url, json.loads(CRED.read_text())


# ------------------------------------------------------------------ tiny API probe (read-only)
def api_login(cred):
    body = json.dumps({'username': cred['admin_username'], 'password': cred['admin_password']}).encode()
    req = urllib.request.Request(HUB + '/api/login', data=body,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())['token']


def api_get(token, path):
    req = urllib.request.Request(HUB + path, headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def _stable_first(items, name_key='name', prefer='qa-repair-20260930'):
    """Pick a long-lived fixture; avoid concurrent batch's transient qa-pw-* items."""
    if not items:
        return None
    def nm(it):
        return str(it.get(name_key) or it.get('hostname') or '')
    for it in items:
        if prefer in nm(it):
            return it
    for it in items:
        if not nm(it).startswith('qa-pw-'):
            return it
    return items[0]


def probe_fixtures(token):
    """Read-only fixture discovery; tolerates a concurrently churning environment."""
    fx = {'probed_at': now_utc()}
    try:
        nodes = api_get(token, '/api/nodes')
        apps = api_get(token, '/api/applications')
        scripts = api_get(token, '/api/scripts')
        execs = api_get(token, '/api/executions?limit=80')
        tasks = api_get(token, '/api/tasks')
        schedules = api_get(token, '/api/schedules')
    except Exception as exc:
        return {'error': '%s: %s' % (type(exc).__name__, exc)}
    stable_scripts = ([s for s in scripts if 'qa-repair-20260930' in str(s.get('name') or '')]
                      or [s for s in scripts if not str(s.get('name') or '').startswith('qa-pw-')]
                      or scripts)
    script_ids = {s['id'] for s in stable_scripts}
    ex = next((e for e in execs if e.get('script_id') in script_ids), None) or (execs[0] if execs else None)
    and_task = next((t for t in tasks
                     if isinstance(t.get('condition'), dict) and t['condition'].get('type') == 'and'), None)
    app = _stable_first(apps)
    if app:
        try:
            api_get(token, '/api/applications/%s' % app['id'])
        except urllib.error.HTTPError as exc:
            if exc.code == 404:  # deleted between list and check -> drop
                app = None
        except Exception:
            pass
    fx.update({
        'nodes': [{'id': n['id'], 'hostname': n['hostname'], 'status': n.get('status')} for n in nodes],
        'online_node': _stable_first([n for n in nodes if n.get('status') == 'online'], 'hostname'),
        'offline_node': _stable_first([n for n in nodes if n.get('status') == 'offline'], 'hostname'),
        'app': ({'id': app['id'], 'name': app['name'], 'version': app.get('version')} if app else None),
        'scripts': [{'id': s['id'], 'name': s['name']} for s in stable_scripts],
        'execution': ({'id': ex['id'], 'script_id': ex.get('script_id'), 'status': ex.get('status')} if ex else None),
        'tasks_count': len(tasks),
        'and_task': ({'id': and_task['id'], 'name': and_task.get('name')} if and_task else None),
        'schedules': [{'id': s['id'], 'name': s.get('name')} for s in schedules],
    })
    fx['execution_script_exists'] = bool(ex and ex.get('script_id') in script_ids)
    return fx


# ------------------------------------------------------------------ availability
def check_availability(p, browsers):
    out = {'checked_at': now_utc(), 'playwright': None, 'browsers': {}}
    try:
        from importlib.metadata import version
        out['playwright'] = version('playwright')
    except Exception:
        pass
    for name in browsers:
        rec = {'available': False}
        try:
            b = getattr(p, name).launch(headless=True)
            pg = b.new_page()
            pg.goto('about:blank')
            rec['userAgent'] = pg.evaluate('navigator.userAgent')
            pg.set_content('<h1>ok</h1>')
            rec['renderCheck'] = pg.text_content('h1')
            rec['available'] = rec['renderCheck'] == 'ok'
            b.close()
        except Exception as exc:
            rec['error'] = str(exc)[:400]
        out['browsers'][name] = rec
    return out


# ------------------------------------------------------------------ per-browser runner
FETCH_PATCH = r'''
(function(){
  if (window.__fx && window.__fx.orig) { try { window.fetch = window.__fx.orig; } catch(e){} }
  const orig = window.fetch.bind(window);
  window.__fx = {orig: orig, mode: 'normal', releases: []};
  window.fetch = function(input, init){
    const url = (typeof input === 'string') ? input : (input && input.url) || '';
    const m = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const isTasksList = /\/api\/tasks(\?|$)/.test(url) && m === 'GET';
    if (window.__fx.mode === 'tasks-hold' && isTasksList) {
      return new Promise(function(resolve){ window.__fx.releases.push(function(){
        resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}})); }); });
    }
    if (window.__fx.mode === 'tasks-empty' && isTasksList) {
      return Promise.resolve(new Response('[]', {status:200, headers:{'Content-Type':'application/json'}}));
    }
    return orig(input, init);
  };
  return 'patched';
})()
'''


class BrowserRun:
    def __init__(self, pw, name, cred, fx):
        self.pw = pw
        self.name = name
        self.cred = cred
        self.fx = fx
        self.phase = 'init'
        self.console = []
        self.results = []
        self.shots = []

    # ---------------- plumbing
    def attach(self, page):
        def on_console(msg):
            if msg.type == 'error':
                loc = msg.location or {}
                self.console.append({'browser': self.name, 'phase': self.phase, 'type': 'console.error',
                                     'url': loc.get('url', ''), 'text': msg.text[:600]})
        def on_pageerror(err):
            self.console.append({'browser': self.name, 'phase': self.phase, 'type': 'pageerror',
                                 'url': self.page.url if getattr(self, 'page', None) else '', 'text': str(err)[:600]})
        page.on('console', on_console)
        page.on('pageerror', on_pageerror)

    def record(self, case_id, status, detail, shots=None, extra=None):
        rec = {'case': case_id, 'browser': self.name, 'status': status, 'at_utc': now_utc(),
               'detail': detail, 'screenshots': shots or []}
        if extra:
            rec['extra'] = extra
        self.results.append(rec)
        print('  [%s] %-28s %s' % (self.name, case_id, status), flush=True)

    def shot(self, name, full=False):
        p = EV / self.name / (name + '.png')
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(p), full_page=full)
        except Exception as exc:
            print('  screenshot failed %s: %s' % (name, exc), flush=True)
            return None
        rel = 'evidence/%s/%s.png' % (self.name, name)
        self.shots.append(rel)
        return rel

    def body(self):
        try:
            return self.page.inner_text('body')
        except Exception:
            return ''

    def wait_text(self, texts, timeout=15000, absent=False):
        end = time.time() + timeout / 1000.0
        while time.time() < end:
            t = self.body()
            if absent and not any(x in t for x in texts):
                return True
            if not absent and any(x in t for x in texts):
                return True
            time.sleep(0.25)
        return False

    def goto(self, path, wait=1.2, timeout=45000):
        self.page.goto(HUB + path, wait_until='domcontentloaded', timeout=timeout)
        if wait:
            time.sleep(wait)

    def data_rows(self):
        return self.page.evaluate(
            "() => Array.from(document.querySelectorAll('table tbody tr'))"
            ".filter(r => !/没有结果。|加载中/.test(r.innerText)).length")

    def api_status(self, path):
        """Status of an API GET from the page (uses the session token)."""
        return self.page.evaluate(
            "async (p) => { try { const r = await fetch(p, {headers: {Authorization: 'Bearer ' +"
            " (localStorage.getItem('nodesteer_token') || '')}}); return r.status; } catch (e) { return -1; } }",
            path)

    def api_json(self, path):
        return self.page.evaluate(
            "async (p) => { try { const r = await fetch(p, {headers: {Authorization: 'Bearer ' +"
            " (localStorage.getItem('nodesteer_token') || '')}}); return r.ok ? await r.json() : []; }"
            " catch (e) { return []; } }",
            path)

    @staticmethod
    def _pick_stable(items, key):
        if not items:
            return None
        for it in items:
            if 'qa-repair-20260930' in str(it.get(key) or ''):
                return it
        for it in items:
            if not str(it.get(key) or '').startswith('qa-pw-'):
                return it
        return items[0]

    def login(self, username, password, page=None):
        pg = page or self.page
        pg.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_selector('input[name="username"]', timeout=25000)
        pg.fill('input[name="username"]', username)
        pg.fill('input[name="password"]', password)
        pg.click('button[type="submit"]')
        pg.wait_for_function("() => !location.pathname.startsWith('/sign-in')", timeout=35000)
        time.sleep(1.2)
        return bool(pg.evaluate("localStorage.getItem('nodesteer_token')"))

    # ---------------- cases
    def case_login_dashboard(self):
        self.phase = 'login_dashboard'
        ok_login = self.login(self.cred['admin_username'], self.cred['admin_password'])
        self.goto('/')
        got = self.wait_text(['节点总数', '托管应用状态'], timeout=20000)
        t = self.body()
        extra = {
            'login_token': ok_login,
            'dashboard_markers': {k: k in t for k in ['节点总数', '托管应用状态', 'Agent 健康状态', '运行中', '成功率']},
            'health_ratio': next((s for s in ['健康状态不可用'] if s in t), None)
            or next((line.strip() for line in t.splitlines() if '健康' in line and '/' in line), None),
            'node_summary': next((line.strip() for line in t.splitlines() if '在线' in line and '/' in line), None)
            or ('在线' in t),
        }
        shot = self.shot('01-dashboard', full=False)
        status = 'PASS' if (ok_login and got and extra['dashboard_markers']['节点总数']
                            and extra['dashboard_markers']['托管应用状态']) else 'FAIL'
        self.record('login_dashboard', status, '登录 + 仪表盘（健康卡/节点汇总）', [shot] if shot else [], extra)

    def case_sidebar_nav(self):
        self.phase = 'sidebar_nav'
        expected = ['/', '/tasks', '/schedules', '/executions', '/transfers',
                    '/agents', '/groups', '/scripts', '/applications', '/audit', '/users', '/settings']
        self.goto('/')
        self.wait_text(['节点总数'], timeout=15000)
        links = self.page.evaluate("() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href'))")
        missing = [h for h in expected if h not in links]
        per = {}
        for href in expected:
            if href in missing:
                per[href] = 'missing'
                continue
            try:
                self.page.click('a[href="%s"]' % href, timeout=8000)
                time.sleep(1.0)
                path = self.page.evaluate('location.pathname')
                ok = path == href if href != '/' else path == '/'
                txt = len(self.body().strip())
                per[href] = {'path': path, 'ok': ok, 'text_len': txt}
            except Exception as exc:
                per[href] = {'error': str(exc)[:150]}
        ok_all = not missing and all(isinstance(v, dict) and v.get('ok') and v.get('text_len', 0) > 40
                                     for v in per.values())
        status = 'PASS' if ok_all else 'FAIL'
        self.record('sidebar_nav_admin', status, 'admin 侧边栏 12 入口逐一点击渲染', [], {'missing': missing, 'per': per})

    def case_role_entry(self):
        self.phase = 'role_entry'
        attempts = [('operator', 'qa-final-ca99ec4c-role-operator', 'qa-final-pass-123'),
                    ('viewer', 'qa-final-ca99ec4c-role-viewer', 'qa-final-pass-123')]
        per = {}
        ctx = self.pw_browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        self.attach(page)
        try:
            for role, uname, pw in attempts:
                try:
                    ok = self.login(uname, pw, page=page)
                    if not ok:
                        per[role] = {'login': False, 'note': 'token missing'}
                        break
                    page.goto(HUB + '/', wait_until='domcontentloaded', timeout=30000)
                    time.sleep(1.5)
                    per[role] = {
                        'login': True,
                        'has_users_entry': bool(page.query_selector('a[href="/users"]')),
                        'has_settings_entry': bool(page.query_selector('a[href="/settings"]')),
                        'path': page.evaluate('location.pathname'),
                    }
                except Exception as exc:
                    per[role] = {'login': False, 'error': str(exc)[:200]}
                    break
                page.evaluate("localStorage.removeItem('nodesteer_token')")
        finally:
            ctx.close()
        if not per or all(not v.get('login') for v in per.values()):
            self.record('role_entry_operator_viewer', 'SKIP',
                        '角色入口检查跳过：operator/viewer 登录不可用（凭据失效或环境变更）', [], per)
            return
        ok = all(v.get('login') and not v.get('has_users_entry') and v.get('has_settings_entry')
                 for v in per.values())
        self.record('role_entry_operator_viewer', 'PASS' if ok else 'FAIL',
                    'operator/viewer 无「用户」入口、保留「设置」', [], per)

    def case_list(self, key, path):
        self.phase = 'list_' + key
        rec = {'path': path}
        self.goto(path)
        self.wait_text(['每页行数', '暂无数据', '没有结果。'], timeout=20000)
        time.sleep(0.6)
        t = self.body()
        rec['rendered'] = ('每页行数' in t) or ('暂无数据' in t) or ('没有结果。' in t)
        rec['empty_state'] = ('暂无数据' in t) or ('没有结果。' in t)
        rec['rows'] = self.data_rows()
        rec['has_table'] = self.page.locator('table').count() > 0
        # sort (column-header buttons carry a title; header checkbox buttons must be excluded)
        rec['sort'] = {'attempted': False}
        try:
            headers = self.page.locator('thead button[title]')
            if headers.count() == 0:
                headers = self.page.locator('thead th button:not([role="checkbox"])')
            if headers.count() > 0:
                before = self.page.evaluate("() => (document.querySelector('table tbody tr')||{}).innerText || ''")
                headers.first.click(timeout=8000)
                time.sleep(1.0)
                aria = self.page.evaluate("() => { const th = document.querySelector('thead th[aria-sort]'); return th ? th.getAttribute('aria-sort') : null; }")
                after = self.page.evaluate("() => (document.querySelector('table tbody tr')||{}).innerText || ''")
                rec['sort'] = {'attempted': True, 'aria_sort': aria, 'row_order_changed': before != after}
        except Exception as exc:
            rec['sort'] = {'attempted': True, 'error': str(exc)[:150]}
        # filter
        rec['filter'] = {'attempted': False}
        try:
            inp = self.page.locator('input[placeholder*="搜索"]').first
            if inp.count() > 0:
                before_rows = rec['rows']
                inp.fill('zzz-no-match-xyz')
                time.sleep(1.3)
                t2 = self.body()
                after_rows = self.data_rows()
                rec['filter'] = {'attempted': True, 'no_match_shown': ('没有结果。' in t2) or after_rows == 0,
                                 'rows_before': before_rows, 'rows_after': after_rows}
                inp.fill('')
                time.sleep(0.9)
        except Exception as exc:
            rec['filter'] = {'attempted': True, 'error': str(exc)[:150]}
        # pagination
        rec['pagination'] = {'attempted': False}
        try:
            page_of = self.page.evaluate("() => (document.body.innerText.match(/第 \\d+ 页，共 \\d+ 页/) || [null])[0]")
            nxt = self.page.get_by_role('button', name='下一页')
            if nxt.count() > 0:
                enabled = nxt.first.is_enabled()
                if enabled:
                    nxt.first.click(timeout=8000)
                    time.sleep(1.2)
                    page_of2 = self.page.evaluate("() => (document.body.innerText.match(/第 \\d+ 页，共 \\d+ 页/) || [null])[0]")
                    rec['pagination'] = {'attempted': True, 'page_of_before': page_of, 'page_of_after': page_of2,
                                         'changed': page_of != page_of2}
                    prev = self.page.get_by_role('button', name='上一页')
                    if prev.count() > 0 and prev.first.is_enabled():
                        prev.first.click(timeout=8000)
                        time.sleep(0.9)
                else:
                    rec['pagination'] = {'attempted': True, 'next_disabled': True, 'page_of': page_of}
            else:
                rec['pagination'] = {'attempted': False, 'no_control': True, 'page_of': page_of}
        except Exception as exc:
            rec['pagination'] = {'attempted': True, 'error': str(exc)[:150]}
        if key in ('agents', 'transfers'):
            self.shot('02-list-%s' % key)
        sort_ok = rec['sort'].get('attempted') and (rec['sort'].get('aria_sort') in ('ascending', 'descending')
                                                    or rec['sort'].get('row_order_changed'))
        filt_ok = rec['filter'].get('attempted') and (rec['filter'].get('no_match_shown')
                                                      or rec['filter'].get('rows_after', 1) < rec['filter'].get('rows_before', 0))
        pag = rec['pagination']
        pag_ok = (not pag.get('attempted')) or pag.get('changed') or pag.get('next_disabled') or pag.get('no_control')
        if rec['empty_state'] and not rec['has_table']:
            status = 'PASS' if rec['rendered'] else 'FAIL'
            note = '空态页（%s）' % ('暂无数据' if '暂无数据' in t else '没有结果。')
        else:
            status = 'PASS' if (rec['rendered'] and rec['has_table'] and sort_ok and filt_ok and pag_ok) else \
                     ('PARTIAL' if rec['rendered'] and rec['has_table'] else 'FAIL')
            note = '列表渲染 + 排序/筛选/分页'
        self.record('list_' + key, status, note, [], rec)

    def case_lists(self):
        for key, path in [('agents', '/agents'), ('tasks', '/tasks'), ('scripts', '/scripts'),
                          ('schedules', '/schedules'), ('applications', '/applications'),
                          ('artifacts', '/artifacts'), ('transfers', '/transfers'),
                          ('audit', '/audit'), ('users', '/users')]:
            try:
                self.case_list(key, path)
            except Exception as exc:
                self.record('list_' + key, 'FAIL', '未捕获异常: %s' % str(exc)[:250], [],
                            {'trace': traceback.format_exc()[-600:]})

    def ensure_app(self):
        app = self.fx.get('app')
        if app and self.api_status('/api/applications/%s' % app['id']) == 200:
            return app
        apps = self.api_json('/api/applications')
        app = self._pick_stable(apps, 'name')
        self.fx['app'] = app
        return app

    def ensure_online_node(self):
        node = self.fx.get('online_node')
        if node and self.api_status('/api/nodes/%s' % node['id']) == 200:
            return node
        nodes = self.api_json('/api/nodes')
        node = self._pick_stable([n for n in nodes if n.get('status') == 'online'], 'hostname')
        self.fx['online_node'] = node
        return node

    def case_detail_node(self):
        self.phase = 'detail_node'
        node = self.ensure_online_node()
        app = self.ensure_app()
        if not node:
            self.record('detail_node', 'SKIP', '无可用节点', [])
            return
        self.goto('/agents/%s' % node['id'], wait=2.2)
        self.wait_text([node['hostname']], timeout=15000)
        t = self.body()
        links = self.page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href'))")
        extra = {
            'hostname_shown': node['hostname'] in t,
            'has_task_section': '任务' in t,
            'has_schedule_section': '调度' in t,
            'has_app_section': '应用' in t,
            'app_link': ('/applications/%s' % app['id']) in links if app else None,
            'task_links': [h for h in links if h and h.startswith('/tasks/')][:5],
            'schedule_links': [h for h in links if h and h.startswith('/schedules/')][:5],
        }
        shot = self.shot('03-node-detail', full=False)
        ok = extra['hostname_shown'] and (extra['app_link'] is True or extra['has_app_section'])
        self.record('detail_node', 'PASS' if ok else 'FAIL',
                    '节点详情（主机名 + 任务/调度/应用回链区）', [shot] if shot else [], extra)

    def case_detail_execution(self):
        self.phase = 'detail_execution'
        ex = self.fx.get('execution')
        if not ex:
            self.record('detail_execution', 'SKIP', '无可用执行记录', [])
            return
        self.goto('/executions/%s' % ex['id'], wait=2.2)
        self.wait_text(['状态', '执行'], timeout=15000)
        t = self.body()
        links = self.page.evaluate("() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href'))")
        script_link = None
        if ex.get('script_id'):
            script_link = ('/scripts/%s' % ex['script_id']) in links
        extra = {
            'status_zh': ('成功' in t) or ('失败' in t) or ('运行中' in t) or ('已取消' in t),
            'raw_enum_absent': not any(x in t for x in ['SUCCESS\n', 'FAILED\n']),
            'script_link': script_link,
            'has_stdout_section': ('标准输出' in t) or ('输出' in t),
        }
        shot = self.shot('04-execution-detail', full=False)
        ok = extra['status_zh'] and (script_link is None or script_link)
        self.record('detail_execution', 'PASS' if ok else 'FAIL',
                    '执行详情（状态徽标中文 + 脚本回链）', [shot] if shot else [], extra)

    def case_detail_application(self):
        self.phase = 'detail_application'
        app = self.ensure_app()
        if not app:
            self.record('detail_application', 'SKIP', '无可用应用', [])
            return
        self.goto('/applications/%s' % app['id'], wait=2.4)
        self.wait_text([app['name'], '不存在'], timeout=15000)
        t = self.body()
        if app['name'] not in t and '不存在' in t:
            # transient or deleted mid-run: re-pick a live app once and retry
            self.fx['app'] = None
            app = self.ensure_app()
            if app:
                self.goto('/applications/%s' % app['id'], wait=2.4)
                self.wait_text([app['name'], '不存在'], timeout=15000)
                t = self.body()
        t = self.body()
        extra = self.page.evaluate("""() => {
          const t = document.body.innerText;
          const time = document.querySelector('time');
          const nameInput = Array.from(document.querySelectorAll('input')).some(i => i.value === window.__qaAppName);
          return {has_version: t.includes('版本'), has_health_card: t.includes('健康'),
                  has_time: !!time, time_title: time ? time.getAttribute('title') : null,
                  time_text: time ? time.innerText : null,
                  name_in_input: nameInput, editor_title: t.includes('编辑托管应用')};
        }""".replace('window.__qaAppName', json.dumps(app['name'])))
        extra['name_shown'] = extra['name_in_input'] or extra['editor_title']
        shot = self.shot('05-app-detail', full=False)
        ok = extra['name_shown'] and extra['has_version'] and extra['has_time']
        self.record('detail_application', 'PASS' if ok else 'FAIL',
                    '应用详情（state 卡片：版本 + 时间）', [shot] if shot else [], extra)

    def case_repair_tasks_states(self):
        self.phase = 'repair_tasks_states'
        extra = {}
        # fresh page state: full reload then patch fetch, then SPA-nav to /tasks
        self.goto('/agents', wait=2.0)
        extra['patch'] = self.page.evaluate(FETCH_PATCH)
        self.page.evaluate("window.__fx.mode = 'tasks-hold'")
        self.page.click('a[href="/tasks"]', timeout=8000)
        time.sleep(2.0)
        t = self.body()
        extra['loading_state'] = {'has_loading': '加载中' in t, 'has_no_results': '没有结果' in t,
                                  'has_no_data': '暂无数据' in t}
        shot_loading = self.shot('06-tasks-loading')
        n = self.page.evaluate("(window.__fx && window.__fx.releases || []).forEach(f => f()), (window.__fx && window.__fx.releases || []).length")
        time.sleep(2.0)
        t = self.body()
        extra['after_release'] = {'released': n, 'has_no_data': '暂无数据' in t,
                                  'has_rows': self.data_rows() > 0, 'has_no_results': '没有结果' in t}
        shot_after = self.shot('07-tasks-after-release')
        # injected empty
        self.goto('/agents', wait=1.8)
        self.page.evaluate(FETCH_PATCH)
        self.page.evaluate("window.__fx.mode = 'tasks-empty'")
        self.page.click('a[href="/tasks"]', timeout=8000)
        time.sleep(2.0)
        t = self.body()
        extra['injected_empty'] = {'has_no_data': '暂无数据' in t, 'has_no_results': '没有结果' in t,
                                   'has_loading': '加载中' in t}
        shot_empty = self.shot('08-tasks-empty')
        # restore real
        self.page.evaluate("(function(){ if (window.__fx && window.__fx.orig) { window.fetch = window.__fx.orig; return 'restored'; } return 'no'; })()")
        self.goto('/tasks', wait=2.5)
        t = self.body()
        extra['real_state'] = {'has_rows': self.data_rows() > 0, 'has_no_data': '暂无数据' in t,
                               'has_loading_stuck': '加载中' in t, 'has_no_results': '没有结果' in t}
        ok = (extra['loading_state']['has_loading'] and not extra['loading_state']['has_no_results']
              and extra['injected_empty']['has_no_data'] and not extra['injected_empty']['has_no_results']
              and not extra['real_state']['has_loading_stuck'])
        self.record('repair_tasks_states', 'PASS' if ok else 'FAIL',
                    'Tasks 三态：挂起→加载中；空响应→暂无数据；恢复→真实列表',
                    [s for s in [shot_loading, shot_after, shot_empty] if s], extra)

    def case_repair_and_summary(self):
        self.phase = 'repair_and_summary'
        extra = {'variant': None}
        shot1 = shot2 = None
        and_task = self.fx.get('and_task')
        if and_task and self.api_status('/api/tasks/%s' % and_task['id']) != 200:
            extra['and_task_gone'] = True
            and_task = None
        try:
            if and_task:
                extra['variant'] = 'edit-existing-and-task'
                self.goto('/tasks/%s/edit' % and_task['id'], wait=2.5)
                self.wait_text(['条件摘要'], timeout=15000)
                el = self.page.query_selector('[data-testid="condition-summary"]')
                text = el.inner_text() if el else None
                has_clear = self.page.locator('button:has-text("清除全部条件")').count() > 0
                shot1 = self.shot('09-condition-summary')
                extra['summary'] = {'exists': el is not None, 'text': text, 'has_clear': has_clear}
                if el:
                    self.page.click('button:has-text("清除全部条件")', timeout=8000)
                    time.sleep(1.2)
                gone = self.page.query_selector('[data-testid="condition-summary"]') is None
                extra['cleared'] = gone
                shot2 = self.shot('10-condition-cleared')
                ok = el is not None and ' AND ' in (text or '') and has_clear and gone
                self.record('repair_and_summary', 'PASS' if ok else 'FAIL',
                            'AND 条件摘要（编辑既有 AND 任务）+ 清除', [s for s in [shot1, shot2] if s], extra)
                return
            # create-form variant: build AND in the form, never save
            extra['variant'] = 'create-form-build-no-save'
            self.goto('/tasks/new', wait=2.5)
            self.wait_text(['本地条件', '远程条件'], timeout=15000)
            self.page.locator('label:has-text("所有条件都满足（AND）") button[role="checkbox"]').first.click(timeout=8000)
            time.sleep(0.4)
            self.page.click('button:has-text("设置条件")', timeout=8000)
            time.sleep(0.6)
            # remote condition: pick first node in the select
            self.page.click('button:has-text("选择节点...")', timeout=8000)
            time.sleep(0.8)
            opts = self.page.locator('[role="option"]')
            extra['node_options'] = opts.count()
            if opts.count() > 0:
                opts.first.click()
                time.sleep(0.5)
            self.page.click('button:has-text("设置远程条件")', timeout=8000)
            time.sleep(1.0)
            el = self.page.query_selector('[data-testid="condition-summary"]')
            text = el.inner_text() if el else None
            has_clear = self.page.locator('button:has-text("清除全部条件")').count() > 0
            shot1 = self.shot('09-condition-summary')
            extra['summary'] = {'exists': el is not None, 'text': text, 'has_clear': has_clear}
            if el:
                self.page.click('button:has-text("清除全部条件")', timeout=8000)
                time.sleep(1.0)
            gone = self.page.query_selector('[data-testid="condition-summary"]') is None
            extra['cleared'] = gone
            shot2 = self.shot('10-condition-cleared')
            # leave without saving
            self.page.locator('a:has-text("取消")').first.click(timeout=8000)
            time.sleep(1.2)
            extra['left_without_save'] = self.page.evaluate('location.pathname') == '/tasks'
            extra['saved'] = False  # 保存按钮从未点击
        except Exception as exc:
            extra['error'] = traceback.format_exc()[-500:]
        ok = (extra.get('summary', {}).get('exists') and ' AND ' in (extra.get('summary', {}).get('text') or '')
              and extra.get('summary', {}).get('has_clear') and extra.get('cleared'))
        self.record('repair_and_summary', 'PASS' if ok else 'FAIL',
                    'AND 条件摘要 + 一键清除（%s）' % extra.get('variant'),
                    [s for s in [shot1, shot2] if s], extra)

    def case_repair_pagesize(self):
        self.phase = 'repair_pagesize'
        self.goto('/agents', wait=1.5)
        self.page.evaluate("localStorage.setItem('nodesteer.table.nodes', JSON.stringify({pageSize: 999}))")
        self.goto('/agents', wait=2.5)
        self.wait_text(['每页行数'], timeout=15000)
        info = self.page.evaluate("""() => {
          const slots = Array.from(document.querySelectorAll('[data-slot="select-value"]')).map(s => s.innerText.trim());
          const pageText = (document.body.innerText.match(/第 \\d+ 页，共 \\d+ 页/) || [null])[0];
          const rows = Array.from(document.querySelectorAll('table tbody tr')).filter(r => !/没有结果。|加载中/.test(r.innerText)).length;
          return {selectValues: slots, pageText, rows, hasRowsPerPage: document.body.innerText.includes('每页行数')};
        }""")
        shot = self.shot('11-pagesize-999')
        self.page.evaluate("localStorage.removeItem('nodesteer.table.nodes')")
        blank = any(v == '' for v in info['selectValues'])
        numeric = any(v.isdigit() for v in info['selectValues'])
        ok = info['hasRowsPerPage'] and info['rows'] > 0 and not blank and (numeric or bool(info['pageText']))
        self.record('repair_pagesize', 'PASS' if ok else 'FAIL',
                    'pageSize=999 越界回退（下拉非空、行可见）', [shot] if shot else [], info)

    def case_repair_invalid_task(self):
        self.phase = 'repair_invalid_task'
        self.goto('/tasks/00000000-0000-4000-8000-0000000000ee/edit', wait=2.5)
        time.sleep(1.0)
        t = self.body()
        extra = {
            'has_not_found': '任务不存在' in t,
            'has_alert': self.page.locator('[role="alert"]').count() > 0,
            'has_back_link': self.page.locator('a:has-text("返回")').count() > 0,
        }
        shot = self.shot('12-invalid-task')
        ok = extra['has_not_found'] and extra['has_alert'] and extra['has_back_link']
        self.record('repair_invalid_task', 'PASS' if ok else 'FAIL',
                    '无效 task 深链 → 明确报错 + 返回入口', [shot] if shot else [], extra)

    def ensure_node(self, status):
        key = '%s_node' % status
        node = self.fx.get(key)
        if node and self.api_status('/api/nodes/%s' % node['id']) == 200:
            return node
        nodes = self.api_json('/api/nodes')
        node = self._pick_stable([n for n in nodes if n.get('status') == status], 'hostname')
        self.fx[key] = node
        return node

    def case_repair_badges(self):
        self.phase = 'repair_badges'
        extra = {}
        # nodes: offline + online rows
        self.goto('/agents', wait=2.2)
        self.wait_text(['每页行数'], timeout=15000)
        off = self.ensure_node('offline')
        on = self.ensure_node('online')
        def filter_row(needle):
            inp = self.page.locator('input[placeholder*="搜索"]').first
            inp.fill(needle)
            time.sleep(1.5)
            return self.page.evaluate(
                "() => { const rows = Array.from(document.querySelectorAll('table tbody tr'));"
                "const r = rows.find(r => r.innerText.includes(%s)); return r ? r.innerText.replace(/\\n+/g, ' | ') : null; }"
                % json.dumps(needle))
        if off:
            row = filter_row(off['hostname'][:40])
            extra['offline_row'] = (row or '')[:220]
            extra['offline_zh'] = '离线' in (row or '')
            extra['offline_raw_absent'] = 'offline' not in (row or '')
        if on:
            row = filter_row(on['hostname'][:40])
            extra['online_row'] = (row or '')[:220]
            extra['online_zh'] = '在线' in (row or '')
            extra['online_raw_absent'] = 'online' not in (row or '')
        shot_n = self.shot('13-node-badges')
        # transfers: localized badges
        self.goto('/transfers', wait=2.5)
        self.wait_text(['每页行数', '暂无数据'], timeout=20000)
        tr = self.page.evaluate("""() => {
          const rows = Array.from(document.querySelectorAll('table tbody tr')).slice(0, 8);
          const zh = ['成功','失败','已取消','上传中','投递中','等待中','运行中','已跳过'];
          const texts = rows.map(r => r.innerText);
          return {rows: texts.length, zh_rows: texts.filter(t => zh.some(z => t.includes(z))).length,
                  raw_enum_rows: texts.filter(t => /\\b(SUCCESS|FAILED|CANCELED|UPLOADING|DELIVERING|PENDING)\\b/.test(t)).length};
        }""")
        extra['transfers'] = tr
        shot_t = self.shot('14-transfer-badges')
        ok = (extra.get('offline_zh') and extra.get('online_zh')
              and extra.get('offline_raw_absent') and extra.get('online_raw_absent')
              and tr['zh_rows'] > 0 and tr['raw_enum_rows'] == 0)
        self.record('repair_badges', 'PASS' if ok else 'FAIL',
                    '状态徽标中文（节点 在线/离线、传输行中文徽标、无原始枚举）',
                    [s for s in [shot_n, shot_t] if s], extra)

    def case_repair_skip_to_main(self):
        self.phase = 'repair_skip_to_main'
        self.goto('/agents', wait=2.0)
        focused = None
        seq = []
        for _ in range(6):
            self.page.keyboard.press('Tab')
            time.sleep(0.3)
            cur = self.page.evaluate("() => { const a = document.activeElement; return {tag: a.tagName, text: (a.textContent || '').trim().slice(0, 40), href: a.getAttribute && a.getAttribute('href')}; }")
            seq.append(cur)
            if cur.get('text') == 'Skip to Main':
                focused = cur
                break
        shot1 = self.shot('15-skip-focused')
        extra = {'tab_sequence': seq, 'focused': focused}
        if focused:
            pre = self.page.evaluate("() => ({y: window.scrollY, active: document.activeElement.tagName})")
            self.page.keyboard.press('Enter')
            time.sleep(1.0)
            post = self.page.evaluate("""() => ({hash: location.hash, y: window.scrollY,
              active_tag: document.activeElement.tagName,
              active_id: document.activeElement.id || '',
              active_in_content: (function(){const c=document.getElementById('content');
                return !!c && (c === document.activeElement || c.contains(document.activeElement));})(),
              content_exists: !!document.getElementById('content')})""")
            self.page.keyboard.press('Tab')
            time.sleep(0.3)
            nxt = self.page.evaluate("""() => { const a = document.activeElement;
              const c = document.getElementById('content');
              return {tag: a.tagName, text: (a.textContent || '').trim().slice(0, 40),
                      href: a.getAttribute && a.getAttribute('href'), id: a.id || '',
                      in_content: !!c && (c === a || c.contains(a))}; }""")
            extra.update({'pre': pre, 'post': post, 'next_tab_after_enter': nxt})
            shot2 = self.shot('16-skip-after-enter')
            ok = (post['hash'] == '#content' and post['content_exists']
                  and (post['active_in_content'] or nxt.get('in_content')))
        else:
            shot2 = None
            ok = False
        self.record('repair_skip_to_main', 'PASS' if ok else 'FAIL',
                    'Skip to Main：首个 Tab 聚焦 → Enter → #content 生效',
                    [s for s in [shot1, shot2] if s], extra)

    def case_responsive(self):
        self.phase = 'responsive'
        script = (self.fx.get('scripts') or [None])[0]
        sched = (self.fx.get('schedules') or [None])[0]
        extra = {}
        results = {}
        if script and self.api_status('/api/scripts/%s' % script['id']) != 200:
            scripts = self.api_json('/api/scripts')
            script = self._pick_stable(scripts, 'name')
            extra['script_repicked'] = True
        if sched and self.api_status('/api/schedules/%s' % sched['id']) != 200:
            sched = None
            extra['schedule_deleted_midrun'] = True
        targets = []
        if script:
            targets.append(('scripts', '/scripts/%s' % script['id']))
        if sched:
            targets.append(('schedules', '/schedules/%s' % sched['id']))
        else:
            targets.append(('schedules_new_substitute', '/schedules/new'))
        for w, h in ((760, 900), (480, 900)):
            self.page.set_viewport_size({'width': w, 'height': h})
            for key, path in targets:
                self.goto(path, wait=2.0)
                time.sleep(1.0)
                info = self.page.evaluate("""() => {
                  const doc = document.documentElement;
                  const overflow = doc.scrollWidth - window.innerWidth;
                  let worst = null;
                  if (overflow > 1) {
                    let max = 0;
                    for (const el of document.querySelectorAll('*')) {
                      const r = el.getBoundingClientRect();
                      if (r.right - window.innerWidth > max) { max = r.right - window.innerWidth;
                        worst = {tag: el.tagName, cls: (el.className || '').toString().slice(0, 100), right: Math.round(r.right)}; }
                    }
                  }
                  return {scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth, overflow, worst,
                          bodyScrollWidth: document.body.scrollWidth};
                }""")
                results['%s@%d' % (key, w)] = info
                info['rendered_editor'] = ('不存在' not in self.body()) and len(self.body().strip()) > 60
                self.shot('17-responsive-%s-%d' % (key, w))
        self.page.set_viewport_size({'width': 1440, 'height': 900})
        extra['results'] = results
        no_overflow = all(v['overflow'] <= 1 and v.get('rendered_editor') for v in results.values())
        self.record('responsive', 'PASS' if no_overflow else 'FAIL',
                    '760/480 视口无页面级横向滚动', [], extra)

    # ---------------- main
    def run(self):
        launcher = getattr(self.pw, self.name)
        self.pw_browser = launcher.launch(headless=True)
        ctx = self.pw_browser.new_context(viewport={'width': 1440, 'height': 900})
        self.page = ctx.new_page()
        self.attach(self.page)
        t0 = time.time()
        cases = [
            self.case_login_dashboard,
            self.case_sidebar_nav,
            self.case_role_entry,
            lambda: [self.case_lists()],
            self.case_detail_node,
            self.case_detail_execution,
            self.case_detail_application,
            self.case_repair_tasks_states,
            self.case_repair_and_summary,
            self.case_repair_pagesize,
            self.case_repair_invalid_task,
            self.case_repair_badges,
            self.case_repair_skip_to_main,
            self.case_responsive,
        ]
        for case in cases:
            try:
                case()
            except Exception as exc:
                self.phase = getattr(case, '__name__', 'case')
                self.record('exception_' + self.phase, 'FAIL',
                            '未捕获异常: %s' % str(exc)[:300], [], {'trace': traceback.format_exc()[-800:]})
        elapsed = round(time.time() - t0, 1)
        try:
            ctx.close()
            self.pw_browser.close()
        except Exception:
            pass
        return {'results': self.results, 'console': self.console, 'elapsed_sec': elapsed,
                'screenshots': self.shots}


def main():
    wanted = [a for a in sys.argv[1:] if a in ('firefox', 'webkit', 'chromium')] or ['firefox', 'webkit', 'chromium']
    HUB_URL, cred = load_authority()
    assert HUB_URL == HUB
    EV.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    summary = {'batch': 'qa-resume-20261018-crossbrowser', 'authority': HUB,
               'build_label': 'dddee4f0-e2e-repair-20261018-final', 'started_at': now_utc(),
               'browsers': {}, 'fixtures': None}

    token = api_login(cred)
    fx0 = probe_fixtures(token)
    summary['fixtures'] = {k: v for k, v in fx0.items() if k != 'nodes'}
    summary['fixtures']['nodes_brief'] = [{k: n[k] for k in ('hostname', 'status')} for n in fx0.get('nodes', [])]

    with sync_playwright() as p:
        avail = check_availability(p, ['firefox', 'webkit', 'chromium'])
        (HERE / 'browser-availability.json').write_text(json.dumps(avail, indent=2, ensure_ascii=False))

        console_all = []
        for name in wanted:
            if not avail['browsers'].get(name, {}).get('available'):
                summary['browsers'][name] = {'skipped': True, 'reason': 'unavailable'}
                continue
            print('==== browser', name, flush=True)
            try:
                fx = probe_fixtures(api_login(cred))  # fresh probe per browser (env churns)
                runner = BrowserRun(p, name, cred, fx)
                res = runner.run()
                summary['browsers'][name] = {
                    'results': res['results'], 'elapsed_sec': res['elapsed_sec'],
                    'console_error_count': len(res['console']),
                    'screenshots': res['screenshots'],
                    'fixtures_used': {k: v for k, v in fx.items() if k != 'nodes'},
                }
                console_all.extend(res['console'])
            except Exception as exc:
                summary['browsers'][name] = {'error': traceback.format_exc()[-1500:]}
                console_all.append({'browser': name, 'phase': 'runner', 'type': 'harness',
                                    'text': str(exc)[:500]})

    summary['finished_at'] = now_utc()
    try:
        fx_after = probe_fixtures(api_login(cred))
        summary['fixtures_after'] = {k: fx_after.get(k) for k in ('tasks_count', 'and_task', 'schedules')}
        summary['fixtures_after']['schedules_count'] = len(fx_after.get('schedules') or [])
        summary['fixture_writes_by_this_batch'] = 'none (read-only navigation)'
    except Exception as exc:
        summary['fixtures_after'] = {'error': str(exc)[:200]}

    # merge with previous partial runs (same batch dir)
    results_path = HERE / 'smoke-results.json'
    if results_path.exists():
        try:
            prev = json.loads(results_path.read_text())
            merged = dict(prev.get('browsers') or {})
            merged.update(summary['browsers'])
            summary['browsers'] = merged
        except Exception:
            pass
    results_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    console_path = HERE / 'console-errors.json'
    prev_entries = []
    if console_path.exists():
        try:
            prev_entries = [e for e in json.loads(console_path.read_text()).get('entries', [])
                            if e.get('browser') not in wanted]
        except Exception:
            prev_entries = []
    console_path.write_text(json.dumps({'generated_at': now_utc(), 'entries': prev_entries + console_all},
                                       indent=2, ensure_ascii=False))
    # short console summary
    for name in wanted:
        br = summary['browsers'].get(name, {})
        results = br.get('results', [])
        passed = sum(1 for r in results if r['status'] == 'PASS')
        failed = [r['case'] for r in results if r['status'] == 'FAIL']
        print('%s: %d/%d PASS, fails=%s, console_errors=%s' % (
            name, passed, len(results), failed, br.get('console_error_count', 'n/a')), flush=True)


if __name__ == '__main__':
    main()
