import { randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const nodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const run = `qa-resume-20261008-cancel-${randomUUID().slice(0, 8)}`
const marker = `${run}-definition-marker`
const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
if (!login.ok) throw new Error(`repair auth HTTP ${login.status}`)
const loginBody = await login.json(); let token = loginBody.token || loginBody.Token
const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
const definition = {
  name: `${run}-task`,
  type: 'command',
  command: `printf '%s\\n' '${marker}'`,
  target: { type: 'node', node_ids: [nodeId] },
  parameters: [],
  param_values: {},
  timeout: 20,
  retry: 0,
  offline_policy: 'hub_online_required',
  enabled: true,
}
const create = await fetch(`${base}/api/tasks`, { method: 'POST', headers, body: JSON.stringify(definition) })
if (!create.ok) throw new Error(`Task fixture create HTTP ${create.status}`)
const created = await create.json(); const id = created.id
if (typeof id !== 'string') throw new Error('Task fixture create response had no ID')
const read = await fetch(`${base}/api/tasks/${id}`, { headers: { Authorization: `Bearer ${token}` } })
const saved = read.ok ? await read.json() : null
const exact = read.status === 200 && saved.target?.type === 'node' && Array.isArray(saved.target.node_ids) && saved.target.node_ids.length === 1 && saved.target.node_ids[0] === nodeId && (saved.target.group_ids || []).length === 0 && saved.retry === 0 && !saved.schedule
const evidence = {
  run,
  resource: 'task',
  task_id: id,
  task_name: saved?.name,
  create_method: 'POST /api/tasks',
  create_http: create.status,
  readback_http: read.status,
  definition: { type: saved?.type, target: saved?.target, retry: saved?.retry, schedule: saved?.schedule ?? null, enabled: saved?.enabled, timeout: saved?.timeout, command_marker_present: saved?.command?.includes(marker) || false },
  target_exact_singleton_native3: exact,
  permitted_node_id: nodeId,
  browser_followup_pending: true,
}
await writeFile(`${dir}/cancel-counter-followup-${run}-definition.json`, JSON.stringify(evidence, null, 2))
delete c.admin_password; delete c.token; token = undefined
if (!exact) throw new Error('Task fixture target or defaults did not match exact safe definition')
console.log(JSON.stringify({ run, task_id: id, create_http: create.status, readback_http: read.status, target_exact_singleton_native3: exact }))
