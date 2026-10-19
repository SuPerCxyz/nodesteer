const { chromium } = require('/home/superc/code/nodesteer/web/node_modules/playwright')
const fs = require('fs')
const cred = JSON.parse(fs.readFileSync('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json', 'utf8'))
const BASE = 'http://192.168.100.209:18080'

;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  const r = await page.request.post(BASE + '/api/login', { data: { username: cred.admin_username, password: cred.admin_password } })
  const token = (await r.json()).token
  const H = { Authorization: 'Bearer ' + token }

  const mk = (name, bytes) => ({ name, interpreter: 'shell', content: '# ' + name + '\n' + '# qa-long-content\n'.repeat(bytes), timeout: 0, enabled: false })
  const over = mk('qa-pw-20261018-limit-over-' + Date.now(), 16384)
  const overLen = over.content.length
  const atLimit = mk('qa-pw-20261018-limit-at-' + Date.now(), 16383)
  // exact-at-limit: pad to exactly 262144
  const exact = mk('qa-pw-20261018-limit-exact-' + Date.now(), 1)
  exact.content = 'x'.repeat(262144)

  const rOver = await page.request.post(BASE + '/api/scripts', { headers: H, data: over })
  console.log('GAP-20 fixture size bytes =', overLen, '| HTTP', rOver.status(), '| body:', (await rOver.text()).slice(0, 200))
  const rExact = await page.request.post(BASE + '/api/scripts', { headers: H, data: exact })
  console.log('exact 262144 bytes -> HTTP', rExact.status(), '| body:', (await rExact.text()).slice(0, 160))
  const rAt = await page.request.post(BASE + '/api/scripts', { headers: H, data: atLimit })
  console.log('16383*17 bytes -> HTTP', rAt.status())
  for (const resp of [rExact, rAt]) {
    if (resp.status() === 200) {
      const b = await resp.json()
      const d = await page.request.delete(BASE + '/api/scripts/' + b.id, { headers: H })
      console.log('cleanup script', b.id, 'DELETE', d.status())
    }
  }
  // confirm gone
  await browser.close()
})()
