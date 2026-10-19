const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright')
const fs = require('fs')
const cred = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'))
const BASE = 'http://192.168.100.209:18080'

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.goto(BASE + '/sign-in')
  const r = await page.request.post(BASE + '/api/login', { data: { username: cred.admin_username, password: cred.admin_password } })
  const body = await r.json()
  const token = body.token || body.Token
  await page.evaluate((t) => localStorage.setItem('nodesteer_token', t), token)
  const tasks = await (await page.request.get(BASE + '/api/tasks', { headers: { Authorization: 'Bearer ' + token } })).json()
  const list = Array.isArray(tasks) ? tasks : tasks.items
  console.log('task_count', list.length)
  const first = list[0]
  console.log('first_task', JSON.stringify({ id: first.id, name: first.name, type: first.type, enabled: first.enabled, revision: first.revision, script_id: first.script_id, application_id: first.application_id }))
  await page.goto(BASE + '/tasks/' + first.id)
  await page.waitForTimeout(2500)
  const state = await page.evaluate(() => ({
    url: location.pathname,
    mainPresent: !!document.querySelector('main'),
    mainText: (document.querySelector('main')?.innerText || '').slice(0, 600),
    hasOverview: /概览|overview/i.test(document.querySelector('main')?.innerText || ''),
    sectionTitles: [...document.querySelectorAll('main h2,main h3')].map(e => e.innerText.trim()).slice(0, 20),
  }))
  console.log('detail_state', JSON.stringify(state, null, 2))
  await page.screenshot({ path: '/tmp/opencode/nodesteer-pw-20261018/repro-p23-02-first-task.png', fullPage: true })
  // check a task that the suite itself created (own prefix) if any
  const own = list.filter(t => String(t.name).startsWith('qa-'))
  if (own.length) {
    await page.goto(BASE + '/tasks/' + own[0].id)
    await page.waitForTimeout(2500)
    const st2 = await page.evaluate(() => ({ url: location.pathname, hasOverview: /概览|overview/i.test(document.querySelector('main')?.innerText || ''), sectionTitles: [...document.querySelectorAll('main h2,main h3')].map(e => e.innerText.trim()).slice(0, 20) }))
    console.log('own_task_state', JSON.stringify(st2))
  }
  await browser.close()
})()
