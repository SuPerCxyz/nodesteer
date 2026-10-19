// qa-resume-20261018-final2-verify: real dual-tab logout convergence (P5 X13).
// Two real tabs in one browser context (shared localStorage -> storage events).
const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright');
const fs = require('fs');

const BASE = 'http://192.168.100.209:18080';
const CRED = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'));
const OUT = process.argv[2] || '/tmp/opencode/final2-dual-tab.json';

(async () => {
  const out = { at_utc: new Date().toISOString(), case: 'P5-X13-DUAL-TAB-LOGOUT', base: BASE };
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const p1 = await context.newPage();
  const p2 = await context.newPage();
  try {
    // ---- login on tab 1
    await p1.goto(BASE + '/sign-in', { waitUntil: 'domcontentloaded' });
    await p1.waitForSelector('input[name="username"]', { timeout: 20000 });
    await p1.fill('input[name="username"]', CRED.admin_username);
    await p1.fill('input[name="password"]', CRED.admin_password);
    await p1.click('button:has-text("登录")');
    await p1.waitForFunction(() => !!localStorage.getItem('nodesteer_token') && location.pathname !== '/sign-in', null, { timeout: 30000 });
    out.login_ok = true;

    // ---- tab 2 loads with the shared session
    await p2.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await p2.waitForFunction(() => !!localStorage.getItem('nodesteer_token'), null, { timeout: 20000 });
    out.page2_token_present = await p2.evaluate(() => !!localStorage.getItem('nodesteer_token'));
    out.page2_url = p2.url();
    const oldToken = await p1.evaluate(() => localStorage.getItem('nodesteer_token'));
    out.token_captured = !!oldToken;

    // ---- instrumentation: signal-write time on tab1, convergence time on tab2
    await p1.evaluate(() => {
      window.__sigAt = null;
      const orig = localStorage.setItem.bind(localStorage);
      localStorage.setItem = (k, v) => { if (k === 'nodesteer_session_invalidated') window.__sigAt = Date.now(); return orig(k, v); };
    });
    await p2.evaluate(() => {
      window.__convergedAt = null;
      const check = () => {
        if (!window.__convergedAt && !localStorage.getItem('nodesteer_token') && location.pathname === '/sign-in') {
          window.__convergedAt = Date.now();
        }
      };
      setInterval(check, 10); check();
    });

    // ---- real logout through the UI on tab 1
    const t0 = Date.now();
    await p1.click('button:has-text("qa-repair")');
    await p1.click('[role="menuitem"]:has-text("退出登录")');
    await p1.waitForSelector('[role="alertdialog"]', { timeout: 10000 });
    out.dialog_text = (await p1.locator('[role="alertdialog"]').innerText()).replace(/\n+/g, ' | ').slice(0, 160);
    await p1.locator('[role="alertdialog"] button').last().click();

    // ---- poll tab 2 for convergence
    let converged = false;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      converged = await p2.evaluate(() => !localStorage.getItem('nodesteer_token') && location.pathname === '/sign-in');
      if (converged) break;
      await new Promise(r => setTimeout(r, 25));
    }
    out.converged = converged;
    out.click_to_converge_ms = converged ? (Date.now() - t0) : null;
    const sigAt = await p1.evaluate(() => window.__sigAt);
    const convAt = await p2.evaluate(() => window.__convergedAt);
    out.signal_at_ms = sigAt;
    out.converged_at_ms = convAt;
    out.signal_to_converge_ms = (sigAt && convAt) ? (convAt - sigAt) : null;
    out.page2_url_after = p2.url();
    out.page2_has_redirect_param = /redirect=/.test(p2.url());
    out.page2_token_after = await p2.evaluate(() => localStorage.getItem('nodesteer_token'));
    out.page1_url_after = p1.url();
    // old token must be revoked server-side
    out.old_token_me_status = await p2.evaluate(async (tok) => {
      const r = await fetch('/api/me', { headers: { Authorization: 'Bearer ' + tok } });
      return r.status;
    }, oldToken);
    out.verdict = {
      login_ok: out.login_ok,
      page2_was_logged_in: out.page2_token_present,
      converged: converged,
      converged_within_1s: out.signal_to_converge_ms !== null && out.signal_to_converge_ms <= 1000,
      token_cleared: out.page2_token_after === null,
      redirected_to_signin: out.page2_url_after.includes('/sign-in'),
      old_token_revoked: out.old_token_me_status === 401,
    };
    out.pass = Object.values(out.verdict).every(Boolean);
  } catch (err) {
    out.error = String(err && err.stack || err).slice(0, 1200);
    out.pass = false;
  } finally {
    await browser.close();
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log(JSON.stringify({ pass: out.pass, verdict: out.verdict, click_to_converge_ms: out.click_to_converge_ms, signal_to_converge_ms: out.signal_to_converge_ms, error: out.error || null }, null, 1));
  process.exit(out.pass ? 0 : 1);
})();
