#!/usr/bin/env python3
"""Supplementary: real /schedules/{id} responsive check (760/480) on all three
browsers, opportunistically using a live schedule fixture (read-only).

Polls /api/schedules until a schedule appears (bounded), then immediately
verifies the editor page has no page-level horizontal overflow. Skips (does
not fake) if no live schedule appears or the fixture vanishes mid-check.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence' / 'supplementary'
HUB = 'http://192.168.100.209:18080'
CRED = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json')
CONN = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json')
POLL_SECONDS = int(os.environ.get('SCHED_POLL_SECONDS', '480'))

os.umask(0o077)


def load_authority():
    for p in (CONN, CRED):
        st = p.lstat()
        if p.is_symlink() or not p.is_file() or st.st_uid != os.getuid() or (st.st_mode & 0o777) != 0o600:
            raise RuntimeError('private_file_gate_failed:' + p.name)
    return json.loads(CRED.read_text())


def api_login(cred):
    body = json.dumps({'username': cred['admin_username'], 'password': cred['admin_password']}).encode()
    req = urllib.request.Request(HUB + '/api/login', data=body,
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())['token']


def api_get(token, path):
    req = urllib.request.Request(HUB + path, headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def wait_for_schedule(token, deadline):
    while time.time() < deadline:
        try:
            scheds = api_get(token, '/api/schedules')
        except Exception:
            scheds = []
        if scheds:
            return scheds[0]
        time.sleep(8)
    return None


def run_browser(p, name, sched_id, cred):
    rec = {'browser': name, 'schedule_id': sched_id, 'checks': {}}
    b = getattr(p, name).launch(headless=True)
    try:
        ctx = b.new_context(viewport={'width': 1440, 'height': 900})
        pg = ctx.new_page()
        pg.goto(HUB + '/sign-in', wait_until='domcontentloaded', timeout=45000)
        pg.wait_for_selector('input[name="username"]', timeout=25000)
        pg.fill('input[name="username"]', cred['admin_username'])
        pg.fill('input[name="password"]', cred['admin_password'])
        pg.click('button[type="submit"]')
        pg.wait_for_function("() => !location.pathname.startsWith('/sign-in')", timeout=35000)
        time.sleep(1.2)
        for w, h in ((760, 900), (480, 900)):
            pg.set_viewport_size({'width': w, 'height': h})
            pg.goto(HUB + '/schedules/' + sched_id, wait_until='domcontentloaded', timeout=45000)
            time.sleep(2.5)
            body = pg.inner_text('body')
            if '不存在' in body:
                rec['checks']['%d' % w] = {'invalid_fixture': True, 'excerpt': body[:120]}
                continue
            info = pg.evaluate("""() => {
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
                      editor_rendered: (document.body.innerText.includes('编辑调度') ||
                                        document.body.innerText.includes('调度'))};
            }""")
            info['title_has_schedule'] = ('调度' in body)
            rec['checks']['%d' % w] = info
            EV.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(EV / ('%s-schedule-%d.png' % (name, w))))
        ctx.close()
    finally:
        b.close()
    ok = (len(rec['checks']) == 2
          and all(not v.get('invalid_fixture') and v.get('overflow', 9) <= 1 for v in rec['checks'].values()))
    rec['verdict'] = 'PASS' if ok else 'FAIL'
    return rec


def main():
    cred = load_authority()
    token = api_login(cred)
    deadline = time.time() + POLL_SECONDS
    sched = wait_for_schedule(token, deadline)
    out = {'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
           'poll_seconds': POLL_SECONDS, 'schedule': None, 'browsers': {}}
    if not sched:
        out['verdict'] = 'NOT_COVERED'
        out['reason'] = 'no live schedule fixture appeared during polling window'
        (HERE / 'supplementary-schedule-responsive.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print('NOT_COVERED: no schedule appeared')
        return
    out['schedule'] = {'id': sched['id'], 'name': sched.get('name')}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        for name in ('firefox', 'webkit', 'chromium'):
            try:
                # re-check the fixture is still alive right before each browser
                try:
                    api_get(token, '/api/schedules/' + sched['id'])
                except Exception:
                    out['browsers'][name] = {'verdict': 'SKIP', 'reason': 'fixture vanished before run'}
                    continue
                out['browsers'][name] = run_browser(p, name, sched['id'], cred)
            except Exception as exc:
                out['browsers'][name] = {'verdict': 'ERROR', 'error': str(exc)[:300]}
    verdicts = [v.get('verdict') for v in out['browsers'].values()]
    out['verdict'] = 'PASS' if verdicts and all(v == 'PASS' for v in verdicts) else 'PARTIAL'
    (HERE / 'supplementary-schedule-responsive.json').write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print('supplementary verdict:', out['verdict'])


if __name__ == '__main__':
    main()
