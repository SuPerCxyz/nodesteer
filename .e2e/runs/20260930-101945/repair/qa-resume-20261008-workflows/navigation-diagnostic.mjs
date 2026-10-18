import { createRequire } from 'node:module'
import { readFile, writeFile } from 'node:fs/promises'
const require = createRequire('/home/superc/code/nodesteer/web/package.json')
const { chromium } = require('playwright')
const out = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const evidence = { authority: base, fixture_mutations: 0, events: [], attempts: [] }
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  page.on('response', response => {
    const request = response.request()
    let path
    try { path = new URL(response.url()).pathname } catch { path = '<invalid-url>' }
    if (request.isNavigationRequest() || path.startsWith('/api/')) evidence.events.push({ type: 'response', method: request.method(), path, status: response.status(), navigation: request.isNavigationRequest() })
  })
  page.on('requestfailed', request => {
    let path
    try { path = new URL(request.url()).pathname } catch { path = '<invalid-url>' }
    evidence.events.push({ type: 'requestfailed', method: request.method(), path, error: request.failure()?.errorText?.slice(0, 120) || 'unknown' })
  })
  page.on('console', msg => { if (msg.type() === 'error') evidence.events.push({ type: 'console-error', message: 'redacted-console-error' }) })
  await page.goto('/sign-in', { waitUntil: 'commit', timeout: 15_000 })
  await page.getByLabel(/^(用户名|username)$/i).fill(c.admin_username)
  await page.getByLabel(/^(密码|password)$/i).fill(c.admin_password)
  await page.getByRole('button', { name: /^(登录|sign in)$/i }).click()
  await page.waitForURL(url => url.origin === base && !url.pathname.endsWith('/sign-in'), { timeout: 20_000 })
  evidence.login_ui = 'PASS'
  for (const [label, path] of [['spa-click', '/tasks'], ['direct-commit', '/tasks']]) {
    const attempt = { label, path }
    try {
      if (label === 'spa-click') {
        const navLink = page.getByRole('link', { name: /任务|tasks/i }).first()
        await navLink.click({ timeout: 10_000 })
        await page.waitForURL(url => url.pathname === '/tasks', { timeout: 10_000 })
      } else {
        await page.goto(path, { waitUntil: 'commit', timeout: 10_000 })
      }
      attempt.navigation = 'COMMITTED'
    } catch (error) {
      attempt.navigation = 'TIMEOUT_OR_ERROR'
      attempt.error = String(error?.message || error).split('\n')[0].slice(0, 180)
    }
    try { await page.locator('h1').waitFor({ timeout: 10_000 }); attempt.heading_wait = 'PASS' }
    catch { attempt.heading_wait = 'TIMEOUT' }
    attempt.page = await page.evaluate(() => ({
      url: location.pathname,
      ready_state: document.readyState,
      title: document.title,
      h1_count: document.querySelectorAll('h1').length,
      h1: document.querySelector('h1')?.textContent?.trim().slice(0, 100) || null,
      main_count: document.querySelectorAll('main').length,
      table_count: document.querySelectorAll('main table').length,
      body_text_length: document.body?.innerText?.length || 0,
    }))
    attempt.snapshot_ready = await page.locator('main').count().catch(() => 0)
    evidence.attempts.push(attempt)
    if (attempt.page.ready_state !== 'loading') {
      await page.screenshot({ path: `${out}/tasks-navigation-${label}.png`, fullPage: true }).catch(() => {})
    }
  }
} catch (error) {
  evidence.diagnostic_error = String(error?.message || error).split('\n')[0].slice(0, 180)
} finally {
  await browser.close()
  delete c.admin_password
  delete c.token
  await writeFile(`${out}/navigation-diagnostic-2.json`, JSON.stringify(evidence, null, 2))
  console.log(JSON.stringify({ login_ui: evidence.login_ui, attempts: evidence.attempts, event_count: evidence.events.length, fixture_mutations: evidence.fixture_mutations }))
}
