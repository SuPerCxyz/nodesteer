#!/usr/bin/env python3
"""qa-resume-20261018-local-smoke: P5 local full-stack smoke (owned loopback).

Hub http://127.0.0.1:18911 (owned). Verifies:
  - login / layout / dashboard
  - page regression: nodes, tasks (loading->data->empty), scripts, schedules,
    applications, transfers, audit, users, groups
  - P5-relevant fix points: AND condition summary, pageSize=999 fallback,
    invalid task deep link, Chinese status badges, Skip to Main focus
  - node offline badge after graceful agent stop (evidence last step)

Read-only on business data; only localStorage/viewport manipulations and
session-scoped fetch patching. Writes evidence/smoke/*.png + stdout JSON.
"""
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

RUN = Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-local-smoke')
EV = RUN / 'evidence' / 'smoke'
HUB = 'http://127.0.0.1:18911'
CRED = json.loads(Path('/tmp/opencode/p9/credentials.json').read_text())
USER = CRED['admin_username']
PASS = CRED['admin_password']
AGENT_PID_FILE = Path('/tmp/opencode/p9/agent.pid')

os.umask(0o077)
EV.mkdir(parents=True, exist_ok=True)

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


class Smoke:
    def __init__(self, page):
        self.page = page
        self.results = {}
        self.console = []
        self.errors = []
        page.on('console', self._console)
        page.on('pageerror', lambda err: self.errors.append(
            {'type': 'pageerror', 'text': str(err)[:500], 'at': time.strftime('%H:%M:%S')}))

    def _console(self, msg):
        try:
            if msg.type in ('error', 'warning'):
                self.console.append({'type': msg.type, 'text': msg.text[:400],
                                     'at': time.strftime('%H:%M:%S')})
        except Exception:
            pass

    def shot(self, name):
        try:
            p = EV / (name + '.png')
            self.page.screenshot(path=str(p))
            return 'evidence/smoke/%s.png' % name
        except Exception:
            return None

    def body(self):
        try:
            return self.page.evaluate('document.body.innerText') or ''
        except Exception:
            return ''

    def goto(self, path, wait=1.0):
        self.page.goto(HUB + path, wait_until='domcontentloaded', timeout=30000)
        time.sleep(wait)

    def wait_text(self, texts, timeout=15000, absent=False):
        deadline = time.time() + timeout / 1000.0
        while time.time() < deadline:
            t = self.body()
            hit = any(x in t for x in texts)
            if hit != absent:
                return True
            time.sleep(0.3)
        return False

    def data_rows(self):
        return self.page.evaluate(
            """() => Array.from(document.querySelectorAll('table tbody tr'))
                 .filter(r => !/没有结果。|加载中|暂无数据/.test(r.innerText)).length""")

    def record(self, case_id, status, note, shots=None, extra=None):
        self.results[case_id] = {'status': status, 'note': note,
                                 'screenshots': shots or [], 'extra': extra or {},
                                 'at': time.strftime('%H:%M:%S')}

    def login(self):
        self.page.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=30000)
        self.page.wait_for_selector('input[name="username"]', timeout=20000)
        self.page.fill('input[name="username"]', USER)
        self.page.fill('input[name="password"]', PASS)
        self.page.click('button[type="submit"]')
        self.page.wait_for_function("() => !location.pathname.startsWith('/sign-in')", timeout=30000)
        time.sleep(1.0)
        return bool(self.page.evaluate("() => localStorage.getItem('nodesteer_token')"))

    # ------------------------------------------------------------- cases
    def case_login_dashboard(self):
        ok_login = self.login()
        self.goto('/', 1.5)
        got = self.wait_text(['节点总数', '托管应用状态'], timeout=15000)
        t = self.body()
        extra = {'login_token': ok_login,
                 'markers': {k: k in t for k in ['节点总数', '托管应用状态', 'Agent 健康状态']}}
        shot = self.shot('01-dashboard')
        ok = ok_login and got and all(extra['markers'].values())
        self.record('login_dashboard', 'PASS' if ok else 'FAIL',
                    '登录 + 仪表盘渲染', [shot], extra)

    def case_sidebar_nav(self):
        expected = ['/', '/tasks', '/schedules', '/executions', '/transfers',
                    '/agents', '/groups', '/scripts', '/applications', '/audit', '/users', '/settings']
        self.goto('/')
        self.wait_text(['节点总数'], timeout=15000)
        per = {}
        for href in expected:
            try:
                link = self.page.locator('a[href="%s"]' % href).first
                if link.count() == 0:
                    per[href] = 'missing'
                    continue
                link.click(timeout=8000)
                time.sleep(1.0)
                path = self.page.evaluate('location.pathname')
                t = self.body()
                per[href] = {'path': path, 'ok': path == href, 'text_len': len(t.strip())}
            except Exception as exc:
                per[href] = {'error': str(exc)[:150]}
        ok = all(isinstance(v, dict) and v.get('ok') and v.get('text_len', 0) > 40 for v in per.values())
        self.record('sidebar_nav', 'PASS' if ok else 'FAIL',
                    '侧边栏 12 入口逐一点击渲染', [self.shot('02-sidebar-last')], {'per': per})

    def case_list(self, key, path, expect_rows_min=0):
        rec = {}
        self.goto(path, 1.5)
        self.wait_text(['每页行数', '暂无数据', '没有结果。'], timeout=20000)
        time.sleep(0.5)
        t = self.body()
        rec['rendered'] = ('每页行数' in t) or ('暂无数据' in t) or ('没有结果。' in t)
        rec['empty_state'] = ('暂无数据' in t) or ('没有结果。' in t)
        rec['rows'] = self.data_rows()
        rec['has_table'] = self.page.locator('table').count() > 0
        ok = rec['rendered'] and rec['rows'] >= expect_rows_min
        self.record('list_' + key, 'PASS' if ok else 'FAIL',
                    '列表渲染（rows=%s）' % rec['rows'], [self.shot('03-list-' + key)], rec)

    def case_node_detail_and_badges(self):
        extra = {}
        self.goto('/agents', 1.8)
        self.wait_text(['每页行数', '暂无数据'], timeout=20000)
        row = self.page.evaluate(
            """() => { const r = document.querySelector('table tbody tr'); return r ? r.innerText.replace(/\\n+/g,' | ') : null; }""")
        extra['row'] = (row or '')[:300]
        extra['online_zh'] = '在线' in (row or '')
        extra['raw_online_absent'] = 'online' not in (row or '').lower().replace('nodesteer', '')
        shot1 = self.shot('04-node-badges-online')
        # node detail
        detail_ok = False
        try:
            self.page.locator('table tbody tr a').first.click(timeout=8000)
            time.sleep(1.5)
            path = self.page.evaluate('location.pathname')
            t = self.body()
            extra['detail_path'] = path
            extra['detail_markers'] = {k: (k in t) for k in ['节点', '状态']}
            detail_ok = path.startswith('/agents/') and len(t) > 100
            self.shot('05-node-detail')
        except Exception as exc:
            extra['detail_error'] = str(exc)[:200]
        ok = extra['online_zh'] and extra['raw_online_absent'] and detail_ok
        self.record('node_badges_detail', 'PASS' if ok else 'FAIL',
                    '节点在线徽标中文 + 详情页', [shot1], extra)

    def case_tasks_three_states(self):
        extra = {}
        self.goto('/agents', 1.5)
        extra['patch'] = self.page.evaluate(FETCH_PATCH)
        self.page.evaluate("window.__fx.mode = 'tasks-hold'")
        self.page.click('a[href="/tasks"]', timeout=8000)
        time.sleep(1.5)
        t = self.body()
        extra['loading'] = {'has_loading': '加载中' in t, 'has_no_results': '没有结果' in t}
        shot1 = self.shot('06-tasks-loading')
        n = self.page.evaluate("(window.__fx.releases||[]).forEach(f=>f()), (window.__fx.releases||[]).length")
        time.sleep(1.5)
        t = self.body()
        extra['after_release'] = {'released': n, 'has_no_data': '暂无数据' in t,
                                  'has_rows': self.data_rows() > 0, 'stuck_loading': '加载中' in t}
        shot2 = self.shot('07-tasks-after-release')
        self.goto('/agents', 1.2)
        self.page.evaluate(FETCH_PATCH)
        self.page.evaluate("window.__fx.mode = 'tasks-empty'")
        self.page.click('a[href="/tasks"]', timeout=8000)
        time.sleep(1.5)
        t = self.body()
        extra['injected_empty'] = {'has_no_data': '暂无数据' in t, 'has_no_results': '没有结果' in t,
                                   'stuck_loading': '加载中' in t}
        shot3 = self.shot('08-tasks-empty')
        self.page.evaluate("(function(){ if (window.__fx && window.__fx.orig) { window.fetch = window.__fx.orig; return 'restored'; } return 'no'; })()")
        self.goto('/tasks', 2.0)
        t = self.body()
        extra['real_state'] = {'has_no_data': '暂无数据' in t, 'stuck_loading': '加载中' in t,
                               'has_no_results': '没有结果' in t}
        ok = (extra['loading']['has_loading'] and not extra['loading']['has_no_results']
              and extra['after_release']['has_no_data'] and not extra['after_release']['stuck_loading']
              and extra['injected_empty']['has_no_data'] and not extra['injected_empty']['has_no_results']
              and not extra['real_state']['stuck_loading'])
        self.record('tasks_three_states', 'PASS' if ok else 'FAIL',
                    'Tasks 三态：挂起→加载中；空响应→暂无数据；恢复→真实态',
                    [s for s in [shot1, shot2, shot3] if s], extra)

    def case_and_summary(self):
        extra = {'variant': 'create-form-build-no-save'}
        shot1 = shot2 = None
        try:
            self.goto('/tasks/new', 2.0)
            self.wait_text(['本地条件', '远程条件'], timeout=15000)
            self.page.locator('label:has-text("所有条件都满足（AND）") button[role="checkbox"]').first.click(timeout=8000)
            time.sleep(0.4)
            self.page.click('button:has-text("设置条件")', timeout=8000)
            time.sleep(0.6)
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
            self.page.locator('a:has-text("取消")').first.click(timeout=8000)
            time.sleep(1.2)
            extra['left_without_save'] = self.page.evaluate('location.pathname') == '/tasks'
            extra['saved'] = False
        except Exception as exc:
            extra['error'] = traceback.format_exc()[-500:]
        ok = (extra.get('summary', {}).get('exists') and ' AND ' in (extra.get('summary', {}).get('text') or '')
              and extra.get('summary', {}).get('has_clear') and extra.get('cleared'))
        self.record('and_condition_summary', 'PASS' if ok else 'FAIL',
                    'AND 条件摘要 + 一键清除（创建表单，不保存）',
                    [s for s in [shot1, shot2] if s], extra)

    def case_pagesize_999(self):
        self.goto('/agents', 1.5)
        self.page.evaluate("localStorage.setItem('nodesteer.table.nodes', JSON.stringify({pageSize: 999}))")
        self.goto('/agents', 2.0)
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
        self.record('pagesize_999_fallback', 'PASS' if ok else 'FAIL',
                    'pageSize=999 越界回退（下拉非空、行可见）', [shot], info)

    def case_invalid_task(self):
        self.goto('/tasks/00000000-0000-4000-8000-0000000000ee/edit', 2.0)
        time.sleep(0.8)
        t = self.body()
        extra = {
            'has_not_found': '任务不存在' in t,
            'has_alert': self.page.locator('[role="alert"]').count() > 0,
            'has_back_link': self.page.locator('a:has-text("返回")').count() > 0,
        }
        shot = self.shot('12-invalid-task')
        ok = extra['has_not_found'] and extra['has_alert'] and extra['has_back_link']
        self.record('invalid_task_deeplink', 'PASS' if ok else 'FAIL',
                    '无效 task 深链 → 明确报错 + 返回入口', [shot], extra)

    def case_transfers_empty_badges(self):
        self.goto('/transfers', 2.0)
        self.wait_text(['每页行数', '暂无数据'], timeout=20000)
        info = self.page.evaluate("""() => {
          const rows = Array.from(document.querySelectorAll('table tbody tr')).slice(0, 8);
          const zh = ['成功','失败','已取消','上传中','投递中','等待中','运行中','已跳过'];
          const texts = rows.map(r => r.innerText);
          return {rows: texts.length, zh_rows: texts.filter(t => zh.some(z => t.includes(z))).length,
                  raw_enum_rows: texts.filter(t => /\\b(SUCCESS|FAILED|CANCELED|UPLOADING|DELIVERING|PENDING)\\b/.test(t)).length,
                  has_empty: document.body.innerText.includes('暂无数据')};
        }""")
        shot = self.shot('13-transfers')
        ok = info['raw_enum_rows'] == 0 and (info['rows'] == 0 and info['has_empty'] or info['zh_rows'] > 0)
        note = '传输页空态本地化（无行）' if info['rows'] == 0 else '传输行中文徽标'
        self.record('transfers_badges', 'PASS' if ok else 'FAIL', note, [shot], info)

    def case_skip_to_main(self):
        self.goto('/agents', 1.5)
        focused = None
        seq = []
        for _ in range(6):
            self.page.keyboard.press('Tab')
            time.sleep(0.25)
            cur = self.page.evaluate(
                "() => { const a = document.activeElement; return {tag: a.tagName, text: (a.textContent||'').trim().slice(0,40)}; }")
            seq.append(cur)
            if cur.get('text') == 'Skip to Main':
                focused = cur
                break
        shot1 = self.shot('14-skip-focused')
        extra = {'tab_sequence': seq, 'focused': focused}
        ok = False
        if focused:
            self.page.keyboard.press('Enter')
            time.sleep(0.8)
            post = self.page.evaluate("""() => ({hash: location.hash,
              active_in_content: (function(){const c=document.getElementById('content');
                return !!c && (c === document.activeElement || c.contains(document.activeElement));})(),
              content_exists: !!document.getElementById('content')})""")
            extra['post'] = post
            self.shot('15-skip-after-enter')
            ok = post['hash'] == '#content' and post['content_exists'] and post['active_in_content']
        self.record('skip_to_main', 'PASS' if ok else 'FAIL',
                    'Skip to Main：Tab 聚焦 → Enter → #content 焦点', [shot1], extra)

    def case_offline_badge(self):
        """Graceful agent stop -> node offline -> Chinese badge."""
        extra = {'agent_stopped': False}
        shot = None
        try:
            pid = int(AGENT_PID_FILE.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            extra['agent_stopped'] = True
            # graceful close should mark offline quickly
            time.sleep(4)
            self.goto('/agents', 2.0)
            self.wait_text(['每页行数', '暂无数据'], timeout=20000)
            deadline = time.time() + 40
            row = ''
            while time.time() < deadline:
                row = self.page.evaluate(
                    """() => { const r = document.querySelector('table tbody tr'); return r ? r.innerText.replace(/\\n+/g,' | ') : ''; }""") or ''
                if '离线' in row:
                    break
                time.sleep(2.5)
                self.page.reload(wait_until='domcontentloaded')
                time.sleep(1.2)
            extra['row'] = row[:300]
            extra['offline_zh'] = '离线' in row
            extra['raw_offline_absent'] = 'offline' not in row.lower().replace('nodesteer', '')
            shot = self.shot('16-node-offline-badge')
        except Exception as exc:
            extra['error'] = str(exc)[:300]
        ok = extra.get('offline_zh') and extra.get('raw_offline_absent')
        self.record('offline_badge', 'PASS' if ok else 'FAIL',
                    '优雅停止 Agent → 节点离线徽标中文', [shot], extra)

    def run(self):
        cases = [
            self.case_login_dashboard,
            self.case_sidebar_nav,
            lambda: self.case_list('nodes', '/agents', expect_rows_min=1),
            self.case_node_detail_and_badges,
            self.case_tasks_three_states,
            lambda: self.case_list('tasks', '/tasks'),
            lambda: self.case_list('scripts', '/scripts'),
            lambda: self.case_list('schedules', '/schedules'),
            lambda: self.case_list('applications', '/applications'),
            lambda: self.case_list('executions', '/executions'),
            lambda: self.case_list('audit', '/audit'),
            lambda: self.case_list('users', '/users', expect_rows_min=1),
            lambda: self.case_list('groups', '/groups'),
            self.case_and_summary,
            self.case_pagesize_999,
            self.case_invalid_task,
            self.case_transfers_empty_badges,
            self.case_skip_to_main,
            self.case_offline_badge,
        ]
        for case in cases:
            name = getattr(case, '__name__', 'case')
            try:
                case()
            except Exception as exc:
                self.record('exception_' + name, 'FAIL',
                            '未捕获异常: %s' % str(exc)[:250], [],
                            {'trace': traceback.format_exc()[-800:]})


def main():
    out = {'hub': HUB, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        sm = Smoke(page)
        sm.run()
        out['results'] = sm.results
        out['console'] = sm.console
        out['page_errors'] = sm.errors
        ctx.close()
        browser.close()
    passed = sum(1 for v in out['results'].values() if v['status'] == 'PASS')
    total = len(out['results'])
    out['summary'] = {'pass': passed, 'total': total,
                      'rate': round(passed / total, 3) if total else 0,
                      'failed_cases': [k for k, v in out['results'].items() if v['status'] != 'PASS']}
    out['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
