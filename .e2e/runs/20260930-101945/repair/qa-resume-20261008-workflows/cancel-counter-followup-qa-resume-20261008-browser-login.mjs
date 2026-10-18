import { spawnSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const session = 'nodesteer-followup-20261008'
const profile = 'nodesteer-resume-followup-20261008'
function run(args, input) {
  const r = spawnSync('agent-browser', ['--session', session, ...args], { input, encoding: 'utf8', maxBuffer: 1024 * 1024 })
  if (r.status !== 0) throw new Error(`agent-browser auth ${args[0]} failed with exit ${r.status}`)
  return r.stdout
}
try {
  run(['auth', 'save', profile, '--url', `${base}/sign-in`, '--username', c.admin_username, '--password-stdin'], `${c.admin_password}\n`)
  run(['auth', 'login', profile])
} finally {
  run(['auth', 'delete', profile])
  delete c.admin_password
  delete c.token
}
process.stdout.write('UI login completed; temporary auth profile deleted.\n')
