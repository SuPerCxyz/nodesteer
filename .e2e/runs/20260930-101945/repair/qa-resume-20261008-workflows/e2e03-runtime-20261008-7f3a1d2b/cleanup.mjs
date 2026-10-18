import { readFile, writeFile } from 'node:fs/promises'
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const ledger = JSON.parse(await readFile(`${dir}/ledger.json`, 'utf8'))
const base = 'http://192.168.100.209:18080'
const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
if (!login.ok) throw new Error(`repair login HTTP ${login.status}`)
const loginBody = await login.json(); let token = loginBody.token || loginBody.Token
const resources = [{ kind: 'task', path: '/tasks', id: ledger.task_id }, { kind: 'script', path: '/scripts', id: ledger.script_id }]
const results = []
for (const resource of resources) {
  const del = await fetch(`${base}/api${resource.path}/${resource.id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${token}` } })
  const get = await fetch(`${base}/api${resource.path}/${resource.id}`, { headers: { Authorization: `Bearer ${token}` } })
  results.push({ kind: resource.kind, id: resource.id, delete_http: del.status, verify_get_http: get.status, clean: [200, 204].includes(del.status) && get.status === 404 })
}
const cleanup = { resources: results, all_clean: results.every(x => x.clean) }
await writeFile(`${dir}/result/cleanup.json`, JSON.stringify(cleanup, null, 2))
ledger.cleanup = results; ledger.status = cleanup.all_clean ? 'E2E03_RUNTIME_PASS_CLEAN' : 'CLEANUP_FAILED'
await writeFile(`${dir}/ledger.json`, JSON.stringify(ledger, null, 2))
delete c.admin_password; delete c.token; token = undefined
console.log(JSON.stringify(cleanup))
