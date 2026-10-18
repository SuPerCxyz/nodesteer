import { spawnSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const session = 'nodesteer-e2e03-runtime-20261008'
const base = 'http://192.168.100.209:18080'
const profile = 'nodesteer-e2e03-runtime-20261008'
function run(args, input) {
  const r = spawnSync('agent-browser', ['--session', session, ...args], { input, encoding: 'utf8', maxBuffer: 1024 * 1024 })
  if (r.status !== 0) throw new Error(`agent-browser auth ${args[0]} failed exit ${r.status}`)
}
try {
  run(['auth', 'save', profile, '--url', `${base}/sign-in`, '--username', c.admin_username, '--password-stdin'], `${c.admin_password}\n`)
  run(['auth', 'login', profile])
} finally {
  run(['auth', 'delete', profile])
  delete c.admin_password; delete c.token
}
console.log('UI login completed; temporary auth profile deleted.')
