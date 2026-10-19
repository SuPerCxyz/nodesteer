const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright')
const fs = require('fs')
const cred = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'))
const BASE = 'http://192.168.100.209:18080'

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.goto(BASE + '/sign-in')
  const r = await page.request.post(BASE + '/api/login', { data: { username: cred.admin_username, password: cred.admin_password } })
  const token = (await r.json()).token
  await page.evaluate((t) => localStorage.setItem('nodesteer_token', t), token)

  // --- GAP-14 repro: audit UI search row count ---
  const task = await (await page.request.post(BASE + '/api/tasks', {
    headers: { Authorization: 'Bearer ' + token },
    data: { name: 'qa-pw-20261018-repro-audit-' + Date.now(), type: 'command', command: 'echo repro',
            target: { type: 'node', node_ids: ['91f44866-8a28-435e-bbda-2f2367126c00'] }, enabled: true, timeout: 30, retry: 0,
            offline_policy: 'hub_online_required' },
  })).json()
  console.log('created task', task.id)
  await page.waitForTimeout(1500)
  await page.goto(BASE + '/audit')
  await page.waitForTimeout(1500)
  const size = page.getByRole('combobox').last()
  await size.click()
  await page.getByRole('option', { name: '50', exact: true }).click()
  await page.waitForTimeout(1200)
  const before = await page.locator('tbody tr').count()
  console.log('unfiltered tbody rows (after size=50):', before)
  const search = page.getByRole('textbox', { name: /搜索/ })
  await search.fill(task.id)
  await page.waitForTimeout(2500)
  const after = await page.locator('tbody tr').count()
  const txt = await page.locator('tbody').innerText()
  console.log('filtered tbody rows:', after, '| contains id:', txt.includes(task.id))
  const reqs = []
  page.on('request', (rq) => { if (rq.url().includes('/api/audit')) reqs.push(rq.url()) })
  await search.fill('')
  await page.waitForTimeout(800)
  await search.fill(task.id)
  await page.waitForTimeout(2500)
  console.log('requests seen:', reqs.slice(-4))
  console.log('rows after re-search:', await page.locator('tbody tr').count())
  await page.screenshot({ path: '/tmp/opencode/nodesteer-pw-20261018/repro-gap14-audit-search.png', fullPage: true })
  await page.request.delete(BASE + '/api/tasks/' + task.id, { headers: { Authorization: 'Bearer ' + token } })
  console.log('cleanup task delete done')
  await browser.close()
})()
