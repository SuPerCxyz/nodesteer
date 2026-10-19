// qa-resume-20261018-final2-verify: axe scan (injected, no repo dependency) on 3 pages
// + sidebar navigation landmark check + StatusBadge contrast measurement.
const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright');
const fs = require('fs');

const BASE = 'http://192.168.100.209:18080';
const CRED = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'));
const AXE = fs.readFileSync('/tmp/opencode/final2-axe/package/axe.min.js', 'utf8');
const RUN = '/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-final2-verify';
const STATE = JSON.parse(fs.readFileSync(RUN + '/regression-state.json', 'utf8'));
const TASK_ID = STATE.ids.task;
const OUT = process.argv[2] || '/tmp/opencode/final2-axe.json';
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];

(async () => {
  const out = { at_utc: new Date().toISOString(), case: 'P11-P17-AXE-SAMPLE', base: BASE,
                axe_version: null, pages: {} };
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(BASE + '/sign-in', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('input[name="username"]', { timeout: 20000 });
    await page.fill('input[name="username"]', CRED.admin_username);
    await page.fill('input[name="password"]', CRED.admin_password);
    await page.click('button:has-text("登录")');
    await page.waitForFunction(() => !!localStorage.getItem('nodesteer_token') && location.pathname !== '/sign-in', null, { timeout: 30000 });
    out.login_ok = true;

    const pages = [
      { name: 'nodes-list', url: '/agents' },
      { name: 'transfers-list', url: '/transfers' },
      { name: 'task-editor', url: '/tasks/' + TASK_ID + '/edit' },
    ];
    for (const spec of pages) {
      await page.goto(BASE + spec.url, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3000);
      await page.addScriptTag({ content: AXE });
      const res = await page.evaluate(async (tags) => {
        const r = await window.axe.run(document, {
          runOnly: { type: 'tag', values: tags },
          resultTypes: ['violations'],
        });
        return {
          version: window.axe.version,
          violations: r.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.length,
            targets: v.nodes.slice(0, 3).map(n => n.target.join(' ')) })),
        };
      }, TAGS);
      out.axe_version = res.version;
      const counts = { critical: 0, serious: 0, moderate: 0, minor: 0, total: 0 };
      for (const v of res.violations) { counts[v.impact] = (counts[v.impact] || 0) + v.nodes; counts.total += v.nodes; }
      out.pages[spec.name] = { url: spec.url, counts, violations: res.violations };
    }

    // ---- sidebar navigation landmark (nodes page)
    await page.goto(BASE + '/agents', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    out.sidebar_landmark = await page.evaluate(() => {
      const nav = document.querySelector('nav[aria-label]') || document.querySelector('[role="navigation"]');
      if (!nav) return { exists: false };
      return { exists: true, tag: nav.tagName.toLowerCase(), label: nav.getAttribute('aria-label'),
               link_count: nav.querySelectorAll('a').length };
    });

    // ---- StatusBadge contrast: all visible [data-slot="badge"] elements (canvas sRGB conversion)
    out.badge_contrast = await page.evaluate(() => {
      const toRGB = (cssColor) => {
        const cv = document.createElement('canvas');
        cv.width = cv.height = 1;
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        ctx.clearRect(0, 0, 1, 1);
        ctx.fillStyle = cssColor;
        ctx.fillRect(0, 0, 1, 1);
        const d = ctx.getImageData(0, 0, 1, 1).data;
        return { r: d[0], g: d[1], b: d[2], a: d[3] / 255 };
      };
      const lum = (c) => {
        const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
        return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
      };
      const ratio = (a, b) => {
        const la = lum(a), lb = lum(b);
        const hi = Math.max(la, lb), lo = Math.min(la, lb);
        return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
      };
      const badges = Array.from(document.querySelectorAll('[data-slot="badge"]')).filter(e => e.offsetWidth > 0);
      const seen = {};
      const rows = [];
      for (const el of badges) {
        const cs = getComputedStyle(el);
        const text = (el.textContent || '').trim();
        const key = text + '|' + cs.color + '|' + cs.backgroundColor;
        if (seen[key]) continue;
        seen[key] = true;
        const fg = toRGB(cs.color), bg = toRGB(cs.backgroundColor);
        rows.push({ text, color: cs.color, background: cs.backgroundColor,
                    fg_rgb: [fg.r, fg.g, fg.b], bg_rgb: [bg.r, bg.g, bg.b],
                    ratio: ratio(fg, bg), aa_normal: ratio(fg, bg) >= 4.5 });
      }
      const min = rows.length ? Math.min(...rows.map(r => r.ratio)) : null;
      return { found: rows.length > 0, count: rows.length, rows,
               min_ratio: min, all_aa_normal: rows.length > 0 && rows.every(r => r.aa_normal) };
    });

    const bad = Object.entries(out.pages).filter(([, p]) => p.counts.critical > 0 || p.counts.serious > 0);
    out.verdict = {
      three_pages_scanned: Object.keys(out.pages).length === 3,
      zero_critical_serious: bad.length === 0,
      sidebar_landmark: !!(out.sidebar_landmark && out.sidebar_landmark.exists),
      badge_contrast_ok: !!(out.badge_contrast && out.badge_contrast.found && out.badge_contrast.all_aa_normal),
    };
    out.pass = Object.values(out.verdict).every(Boolean);
  } catch (err) {
    out.error = String(err && err.stack || err).slice(0, 1200);
    out.pass = false;
  } finally {
    await browser.close();
  }
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  console.log(JSON.stringify({ pass: out.pass, verdict: out.verdict,
    pages: Object.fromEntries(Object.entries(out.pages).map(([k, v]) => [k, v.counts])),
    sidebar: out.sidebar_landmark, badge: out.badge_contrast, error: out.error || null }, null, 1));
  process.exit(out.pass ? 0 : 1);
})();
