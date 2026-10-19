#!/usr/bin/env python3
"""P5 Task 1: cross-tab logout instant convergence — real multi-tab browser test.

Owned local-loopback instance only: hub http://127.0.0.1:18911 (never remote).
Scenario S1: A logged-in, B on /tasks, C on /schedules -> A logs out via real UI
            -> assert B (and C) clear session and land on /sign-in <=1s, measure.
Scenario S2: re-login A/B/C -> A and B log out near-simultaneously (B dialog
            pre-opened) -> single convergence per tab, no loop; then duplicate
            invalidation signals -> no extra navigation (idempotent).
Scenario S3: fresh unauthenticated context, two tabs on /sign-in, signal written
            by tab Y -> tab X must NOT navigate (unauthenticated guard).

Writes evidence/cross-tab/*.png and prints a JSON result to stdout
(also saved by the caller as cross-tab-results.json).
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

from playwright.sync_api import sync_playwright

RUN = Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-local-smoke')
EV = RUN / 'evidence' / 'cross-tab'
HUB = 'http://127.0.0.1:18911'
CRED = json.loads(Path('/tmp/opencode/p9/credentials.json').read_text())
USER = CRED['admin_username']
PASS = CRED['admin_password']

os.umask(0o077)
EV.mkdir(parents=True, exist_ok=True)

INIT = r'''
(() => {
  window.__p5log = [];
  const t = () => Date.now();
  window.addEventListener('storage', (e) => {
    window.__p5log.push({t: t(), ev: 'storage', key: e.key, newValue: e.newValue});
  });
  for (const name of ['pushState', 'replaceState']) {
    const orig = history[name].bind(history);
    history[name] = function(...a) {
      window.__p5log.push({t: t(), ev: name, url: String(a[2] === undefined ? '' : a[2])});
      return orig(...a);
    };
  }
  window.__p5state = () => ({
    href: location.href,
    path: location.pathname,
    search: location.search,
    token: localStorage.getItem('nodesteer_token'),
    signal: localStorage.getItem('nodesteer_session_invalidated'),
    log: window.__p5log.slice()
  });
})();
'''


def now_ms():
    return time.time() * 1000.0


class ConsoleSink:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def attach(self, page, tag):
        page.on('console', lambda msg: self._on_console(msg, tag))
        page.on('pageerror', lambda err: self.errors.append(
            {'tab': tag, 'type': 'pageerror', 'text': str(err)[:500], 'at': time.strftime('%H:%M:%S')}))

    def _on_console(self, msg, tag):
        try:
            if msg.type == 'error':
                self.errors.append({'tab': tag, 'type': 'console.error', 'text': msg.text[:500],
                                    'at': time.strftime('%H:%M:%S')})
            elif msg.type == 'warning':
                self.warnings.append({'tab': tag, 'type': 'console.warning', 'text': msg.text[:300]})
        except Exception:
            pass


def login_ui(page, timeout=30000):
    page.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=30000)
    page.wait_for_selector('input[name="username"]', timeout=15000)
    page.fill('input[name="username"]', USER)
    page.fill('input[name="password"]', PASS)
    page.click('button[type="submit"]')
    page.wait_for_function("() => !location.pathname.startsWith('/sign-in')", timeout=timeout)
    page.wait_for_timeout(900)
    return page.evaluate("() => localStorage.getItem('nodesteer_token')")


def open_logout_dialog(page):
    page.click('button[aria-label="个人资料"]', timeout=10000)
    page.locator('[role="menuitem"]:has-text("退出登录")').first.click(timeout=10000)
    dlg = page.locator('[role="alertdialog"]')
    dlg.wait_for(state='visible', timeout=10000)
    return dlg


def poll_signin(page, deadline_s=6.0):
    """Node-side polling; returns (ms_epoch_or_None, polls)."""
    polls = 0
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        polls += 1
        try:
            if page.evaluate('location.pathname') == '/sign-in':
                return now_ms(), polls
        except Exception:
            pass
        time.sleep(0.01)
    return None, polls


def state(page):
    return page.evaluate('window.__p5state()')


def nav_entries(log):
    return [e for e in log if e['ev'] in ('pushState', 'replaceState')]


def signin_navs(log):
    return [e for e in nav_entries(log) if '/sign-in' in e.get('url', '')]


def analyze_convergence(st, t_click_ms, baseline_len=0):
    """Derive signal->nav latency from in-page timestamps (entries after baseline only)."""
    log = st['log'][baseline_len:]
    sig_events = [e for e in log if e['ev'] == 'storage' and e['key'] == 'nodesteer_session_invalidated']
    tok_events = [e for e in log if e['ev'] == 'storage' and e['key'] == 'nodesteer_token']
    navs = signin_navs(log)
    out = {
        'signal_events': sig_events,
        'token_events': tok_events,
        'signin_navs': navs,
        'nav_count': len(navs),
    }
    if sig_events and navs:
        out['signal_to_nav_ms'] = round(navs[0]['t'] - sig_events[0]['t'], 2)
    if tok_events and sig_events:
        out['token_event_to_signal_ms'] = round(sig_events[0]['t'] - tok_events[0]['t'], 2)
    if sig_events:
        out['click_to_signal_ms'] = round(sig_events[0]['t'] - t_click_ms, 2)
    if navs:
        out['click_to_nav_ms'] = round(navs[0]['t'] - t_click_ms, 2)
    return out


def main():
    results = {'hub': HUB, 'user': USER, 'scenarios': {}, 'console': {'errors': [], 'warnings': []}}
    sink = ConsoleSink()
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    results['started_at'] = started

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        results['browser_version'] = browser.version

        # ---------------- S1: single logout -> other tabs converge <=1s
        s1 = {'name': 'S1_cross_tab_logout_convergence'}
        try:
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            ctx.add_init_script(INIT)
            tabA = ctx.new_page()
            tabB = ctx.new_page()
            tabC = ctx.new_page()
            sink.attach(tabA, 'S1-A')
            sink.attach(tabB, 'S1-B')
            sink.attach(tabC, 'S1-C')

            s1['login_token'] = bool(login_ui(tabA))
            tabB.goto(HUB + '/tasks', wait_until='domcontentloaded', timeout=30000)
            tabB.wait_for_function(
                "() => document.body.innerText.includes('每页行数') || document.body.innerText.includes('暂无数据')",
                timeout=20000)
            tabC.goto(HUB + '/schedules', wait_until='domcontentloaded', timeout=30000)
            tabC.wait_for_function(
                "() => document.body.innerText.includes('每页行数') || document.body.innerText.includes('暂无数据')",
                timeout=20000)
            tabB.screenshot(path=str(EV / 's1-b-tasks-before-logout.png'))
            tabA.screenshot(path=str(EV / 's1-a-dashboard-before-logout.png'))
            s1['b_before'] = {'path': state(tabB)['path'], 'token_present': bool(state(tabB)['token'])}
            s1['c_before'] = {'path': state(tabC)['path'], 'token_present': bool(state(tabC)['token'])}
            base_b = len(state(tabB)['log'])
            base_c = len(state(tabC)['log'])

            dlg = open_logout_dialog(tabA)
            t_click = now_ms()
            dlg.locator('button:has-text("退出登录")').click(timeout=10000)

            t_b, polls_b = poll_signin(tabB)
            t_c, polls_c = poll_signin(tabC)
            tabB.wait_for_timeout(400)

            stB = state(tabB)
            stC = state(tabC)
            stA = state(tabA)
            s1['a_after'] = {'path': stA['path'], 'token': stA['token']}
            s1['b_after'] = {'path': stB['path'], 'search': stB['search'], 'token': stB['token']}
            s1['c_after'] = {'path': stC['path'], 'search': stC['search'], 'token': stC['token']}
            s1['timing'] = {
                'click_epoch_ms': round(t_click, 1),
                'b_converged_epoch_ms': None if t_b is None else round(t_b, 1),
                'c_converged_epoch_ms': None if t_c is None else round(t_c, 1),
                'b_e2e_ms_node_poll': None if t_b is None else round(t_b - t_click, 1),
                'c_e2e_ms_node_poll': None if t_c is None else round(t_c - t_click, 1),
                'b_polls': polls_b, 'c_polls': polls_c,
                'b_in_page': analyze_convergence(stB, t_click, base_b),
                'c_in_page': analyze_convergence(stC, t_click, base_c),
            }
            s1['b_after']['new_signin_navs'] = s1['timing']['b_in_page']['nav_count']
            tabB.screenshot(path=str(EV / 's1-b-after-convergence.png'))
            tabC.screenshot(path=str(EV / 's1-c-after-convergence.png'))
            tabA.screenshot(path=str(EV / 's1-a-after-logout.png'))

            # post-convergence protected route must bounce to /sign-in (memory session gone)
            tabB.goto(HUB + '/tasks', wait_until='domcontentloaded', timeout=30000)
            tabB.wait_for_timeout(1200)
            s1['b_protected_reentry_path'] = state(tabB)['path']

            checks = {
                'b_converged_le_1s': t_b is not None and (t_b - t_click) <= 1000.0,
                'c_converged_le_1s': t_c is not None and (t_c - t_click) <= 1000.0,
                'b_token_cleared': stB['token'] is None,
                'c_token_cleared': stC['token'] is None,
                'b_redirect_param': 'redirect=' in stB['search'],
                'b_single_nav': s1['b_after']['new_signin_navs'] == 1,
                'a_on_signin': stA['path'] == '/sign-in',
                'b_reentry_bounced': s1['b_protected_reentry_path'] == '/sign-in',
            }
            s1['checks'] = checks
            s1['verdict'] = 'PASS' if all(checks.values()) else 'FAIL'
            s1['elapsed_s'] = round((now_ms() - t_click) / 1000.0, 2)
            ctx.close()
        except Exception as exc:
            s1['verdict'] = 'ERROR'
            s1['error'] = str(exc)[:500]
            s1['trace'] = traceback.format_exc()[-1200:]
        results['scenarios']['S1'] = s1

        # ---------------- S2: near-simultaneous multi-tab logout + duplicate signal idempotency
        s2 = {'name': 'S2_simultaneous_logout_idempotent'}
        try:
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
            ctx.add_init_script(INIT)
            tabA = ctx.new_page()
            tabB = ctx.new_page()
            tabC = ctx.new_page()
            sink.attach(tabA, 'S2-A')
            sink.attach(tabB, 'S2-B')
            sink.attach(tabC, 'S2-C')
            # 真实多标签登录流程：A 走 UI 表单登录；B/C 随后打开受保护页，
            # 从共享 localStorage token 引导出各自的内存会话（同真实用户新开标签）。
            s2['login_a_ui'] = bool(login_ui(tabA))
            tabB.goto(HUB + '/tasks', wait_until='domcontentloaded', timeout=30000)
            tabB.wait_for_function(
                "() => document.body.innerText.includes('每页行数') || document.body.innerText.includes('暂无数据')",
                timeout=20000)
            tabC.goto(HUB + '/schedules', wait_until='domcontentloaded', timeout=30000)
            tabC.wait_for_function(
                "() => document.body.innerText.includes('每页行数') || document.body.innerText.includes('暂无数据')",
                timeout=20000)
            s2['tab_sessions'] = {
                'a': {'path': state(tabA)['path'], 'token': bool(state(tabA)['token'])},
                'b': {'path': state(tabB)['path'], 'token': bool(state(tabB)['token'])},
                'c': {'path': state(tabC)['path'], 'token': bool(state(tabC)['token'])},
            }

            open_logout_dialog(tabA)
            open_logout_dialog(tabB)
            base_a = len(state(tabA)['log'])
            base_b = len(state(tabB)['log'])
            base_c = len(state(tabC)['log'])
            t_click = now_ms()
            tabA.locator('[role="alertdialog"] button:has-text("退出登录")').click(timeout=10000)
            time.sleep(0.06)
            second_click = 'not_attempted'
            try:
                tabB.locator('[role="alertdialog"] button:has-text("退出登录")').click(timeout=2500)
                second_click = 'clicked'
            except Exception as exc:
                second_click = 'element_gone:%s' % type(exc).__name__
            s2['second_tab_logout_click'] = second_click

            t_a, _ = poll_signin(tabA)
            t_b, _ = poll_signin(tabB)
            t_c, _ = poll_signin(tabC)
            tabA.wait_for_timeout(500)
            stA, stB, stC = state(tabA), state(tabB), state(tabC)
            s2['final_paths'] = {'a': stA['path'], 'b': stB['path'], 'c': stC['path']}
            s2['nav_counts_to_signin'] = {
                'a': analyze_convergence(stA, t_click, base_a)['nav_count'],
                'b': analyze_convergence(stB, t_click, base_b)['nav_count'],
                'c': analyze_convergence(stC, t_click, base_c)['nav_count']}
            s2['tokens'] = {'a': stA['token'], 'b': stB['token'], 'c': stC['token']}

            # duplicate signal: write invalidation key 3x from C; already-converged tabs must stay put
            before_counts = {k: len(signin_navs(v['log'])) for k, v in
                             {'a': stA, 'b': stB, 'c': stC}.items()}
            tabC.evaluate("""() => { for (let i = 0; i < 3; i++) {
                localStorage.setItem('nodesteer_session_invalidated', 'dup-' + i + '-' + Date.now()); } }""")
            tabA.wait_for_timeout(1200)
            stA2, stB2, stC2 = state(tabA), state(tabB), state(tabC)
            after_counts = {k: len(signin_navs(v['log'])) for k, v in
                            {'a': stA2, 'b': stB2, 'c': stC2}.items()}
            s2['duplicate_signal'] = {
                'before_nav_counts': before_counts, 'after_nav_counts': after_counts,
                'no_extra_nav': before_counts == after_counts,
                'final_paths': {'a': stA2['path'], 'b': stB2['path'], 'c': stC2['path']},
                'signal_events_a': [e for e in stA2['log'][base_a:] if e['ev'] == 'storage'
                                    and e['key'] == 'nodesteer_session_invalidated'],
            }
            tabA.screenshot(path=str(EV / 's2-final-tab-a.png'))
            tabC.screenshot(path=str(EV / 's2-final-tab-c.png'))
            checks = {
                'all_on_signin': all(p == '/sign-in' for p in s2['final_paths'].values()),
                'single_convergence_per_tab': all(v == 1 for v in s2['nav_counts_to_signin'].values()),
                'tokens_cleared': all(v is None for v in s2['tokens'].values()),
                'duplicate_signal_no_extra_nav': s2['duplicate_signal']['no_extra_nav'],
                'duplicate_signal_stable_paths': all(
                    p == '/sign-in' for p in s2['duplicate_signal']['final_paths'].values()),
            }
            s2['checks'] = checks
            s2['verdict'] = 'PASS' if all(checks.values()) else 'FAIL'
            ctx.close()
        except Exception as exc:
            s2['verdict'] = 'ERROR'
            s2['error'] = str(exc)[:500]
            s2['trace'] = traceback.format_exc()[-1200:]
        results['scenarios']['S2'] = s2

        # ---------------- S3: unauthenticated tabs must not navigate on signal
        s3 = {'name': 'S3_unauthenticated_tab_not_misnavigated'}
        try:
            ctx2 = browser.new_context(viewport={'width': 1280, 'height': 800})
            ctx2.add_init_script(INIT)
            tabX = ctx2.new_page()
            tabY = ctx2.new_page()
            sink.attach(tabX, 'S3-X')
            sink.attach(tabY, 'S3-Y')
            tabX.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=30000)
            tabY.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=30000)
            tabX.wait_for_timeout(800)
            before = state(tabX)
            base_x = len(before['log'])
            tabY.evaluate("() => localStorage.setItem('nodesteer_session_invalidated', 's3-' + Date.now())")
            tabX.wait_for_timeout(1300)
            after = state(tabX)
            new_entries = after['log'][base_x:]
            s3['x_before'] = {'path': before['path'], 'token': before['token']}
            s3['x_after'] = {'path': after['path'], 'token': after['token'],
                             'new_nav_count': len([e for e in new_entries
                                                   if e['ev'] in ('pushState', 'replaceState')]),
                             'signal_events': [e for e in new_entries if e['ev'] == 'storage'
                                               and e['key'] == 'nodesteer_session_invalidated']}
            checks = {
                'x_stayed_on_signin': after['path'] == '/sign-in',
                'x_no_navigation': s3['x_after']['new_nav_count'] == 0,
                'x_received_signal': len(s3['x_after']['signal_events']) >= 1,
            }
            s3['checks'] = checks
            s3['verdict'] = 'PASS' if all(checks.values()) else 'FAIL'
            tabX.screenshot(path=str(EV / 's3-unauthenticated-tab.png'))
            ctx2.close()
        except Exception as exc:
            s3['verdict'] = 'ERROR'
            s3['error'] = str(exc)[:500]
            s3['trace'] = traceback.format_exc()[-1200:]
        results['scenarios']['S3'] = s3

        browser.close()

    results['console']['errors'] = sink.errors
    results['console']['warnings'] = sink.warnings
    results['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    results['overall'] = 'PASS' if all(
        results['scenarios'][k].get('verdict') == 'PASS' for k in ('S1', 'S2', 'S3')) else 'FAIL'
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if results['overall'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
