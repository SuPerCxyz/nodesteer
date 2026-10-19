const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright')
const fs = require('fs')
const crypto = require('crypto')
const cred = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'))
const BASE = 'http://192.168.100.209:18080'
const WORK = '/tmp/opencode/nodesteer-pw-20261018'

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const r = await page.request.post(BASE + '/api/login', { data: { username: cred.admin_username, password: cred.admin_password } })
  const token = (await r.json()).token
  const H = { Authorization: 'Bearer ' + token }
  const password = 'Qa!' + crypto.randomBytes(18).toString('hex') + '9z'
  const out = {}
  for (const role of ['operator', 'viewer']) {
    const username = `qa-pw-20261018-${role}-` + crypto.randomBytes(4).toString('hex')
    const res = await page.request.post(BASE + '/api/users', { headers: H, data: { username, password, role } })
    const body = await res.json().catch(() => ({}))
    console.log(role, 'HTTP', res.status(), 'id', body.id, 'username', username)
    out[role] = { id: body.id, username, role, http: res.status() }
  }
  fs.writeFileSync(WORK + '/rbac-users.json', JSON.stringify({ password, users: out }, null, 2), { mode: 0o600 })
  // verify logins work
  for (const role of ['operator', 'viewer']) {
    const lr = await page.request.post(BASE + '/api/login', { data: { username: out[role].username, password } })
    console.log('login', role, 'HTTP', lr.status())
  }
  await browser.close()
})()
