import { createRequire } from 'node:module'
import { randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
const require = createRequire('/home/superc/code/nodesteer/web/package.json')
const { chromium } = require('playwright')
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const nodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const run = `qa-resume-20261008-task-schedule-${randomUUID().slice(0, 8)}`
const marker = `${run}-scheduled-marker`
const resources = []
const result = { run, authority: base, node_id: nodeId, phases: [], requests: [], cleanup: [], status: 'RUNNING' }
let token
let browser
const ledgerPath = `${dir}/ledger-${run}.json`
async function persistLedger() { await writeFile(ledgerPath, JSON.stringify({ run, resources, phases: result.phases, updated_at: new Date().toISOString() }, null, 2)) }
function need(ok, label) { if (!ok) throw new Error(`Assertion failed: ${label}`) }
async function api(path, method = 'GET', data) {
  const response = await fetch(`${base}/api${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) })
  let body = null; try { body = await response.json() } catch {}
  result.requests.push({ method, path, status: response.status })
  return { status: response.status, ok: response.ok, body }
}
async function routeWrite(page, path, method, action, kind) {
  let status
  let id
  const handler = async route => {
    if (route.request().method() !== method) return route.continue()
    const response = await route.fetch()
    status = response.status()
    if (method === 'POST') { const body = await response.json(); id = body.id || body.node_id }
    await route.fulfill({ response })
  }
  await page.route(`${base}/api${path}`, handler)
  await action()
  await waitFor(() => method === 'POST' ? id : status !== undefined, `${kind} ${method}`, 30_000)
  await page.unroute(`${base}/api${path}`, handler)
  need(status < 400, `${kind} ${method} HTTP ${status}`)
  if (method === 'POST') {
    need(typeof id === 'string', `${kind} response ID`)
    resources.push({ kind, path, id, state: 'created' })
    await persistLedger()
  }
  result.requests.push({ method, path, status, ...(id ? { id } : {}) })
  return id
}
async function waitFor(predicate, label, timeout = 90_000) {
  const until = Date.now() + timeout
  while (Date.now() < until) { const value = await predicate(); if (value) return value; await new Promise(resolve => setTimeout(resolve, 1500)) }
  throw new Error(`Timed out waiting for ${label}`)
}
async function navigate(page, path) {
  await page.goto(path, { waitUntil: 'commit', timeout: 15_000 })
}
async function tasksList(page) {
  if (new URL(page.url()).pathname !== '/tasks') {
    await page.getByRole('link', { name: /^(任务|tasks)$/i }).first().click()
    await page.waitForURL(url => url.pathname === '/tasks', { timeout: 15_000 })
  }
  await page.getByRole('heading', { name: /^(任务|tasks)$/i }).waitFor({ timeout: 15_000 })
}
async function findRow(page, name, id) {
  const search = page.getByRole('textbox', { name: /搜索任务|search tasks/i })
  await search.fill(name)
  const row = page.locator(`tr:has(a[href="/tasks/${id}"])`)
  await row.waitFor({ state: 'visible', timeout: 15_000 })
  return row
}
async function exactRead(path, expected, kind, id) {
  const response = await api(path)
  need(response.status === expected, `${kind} ${id} GET ${expected}, got ${response.status}`)
  return response.body
}

try {
  const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
  need(login.ok, `repair auth HTTP ${login.status}`)
  const loginBody = await login.json(); token = loginBody.token || loginBody.Token
  need(typeof token === 'string', 'process-only API token')
  const nodes = await api('/nodes')
  const node = nodes.body.find(x => x.id === nodeId)
  need(node?.status === 'online' && node?.sync_status === 'synced', 'native-3 online/synced')
  result.node = { id: node.id, hostname: node.hostname, status: node.status, sync_status: node.sync_status }

  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  await navigate(page, '/sign-in')
  await page.getByLabel(/^(用户名|username)$/i).fill(c.admin_username)
  await page.getByLabel(/^(密码|password)$/i).fill(c.admin_password)
  await page.getByRole('button', { name: /^(登录|sign in)$/i }).click()
  await page.waitForURL(url => url.origin === base && url.pathname !== '/sign-in', { timeout: 20_000 })
  await tasksList(page)

  const taskName = `${run}-task`
  const editedTaskName = `${taskName}-edited`
  await page.getByRole('link', { name: /新建任务|new task/i }).click()
  await page.getByLabel(/^名称$/).fill(taskName)
  await page.getByRole('textbox', { name: /命令|command/i }).fill(`printf '%s\\n' '${marker}'`)
  const nodeLabel = page.locator('label').filter({ hasText: node.hostname }).filter({ has: page.getByRole('checkbox') })
  await nodeLabel.getByRole('checkbox').check()
  const taskId = await routeWrite(page, '/tasks', 'POST', () => page.getByRole('button', { name: /^保存$/ }).click(), 'task')
  result.phases.push({ phase: 'task_created', task_id: taskId, ui: 'PASS' }); await persistLedger()
  await tasksList(page)
  let row = await findRow(page, taskName, taskId)
  const taskBefore = await exactRead(`/tasks/${taskId}`, 200, 'task', taskId)
  need(taskBefore.target?.type === 'node' && taskBefore.target.node_ids?.includes(nodeId), 'Task targets native-3 only')
  await row.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^编辑$/ }).click()
  await page.getByLabel(/^名称$/).first().fill(editedTaskName)
  await page.getByRole('textbox', { name: /命令|command/i }).fill(`printf '%s\\n' '${marker}-edited'`)
  await routeWrite(page, `/tasks/${taskId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'task edit')
  await tasksList(page)
  row = await findRow(page, editedTaskName, taskId)
  const edited = await exactRead(`/tasks/${taskId}`, 200, 'task', taskId)
  need(edited.name === editedTaskName && edited.command.includes(`${marker}-edited`), 'Task edit persisted')
  for (const [label, enabled] of [['禁用', false], ['启用', true]]) {
    await row.getByRole('button', { name: /^操作$/ }).click()
    await page.getByRole('menuitem', { name: new RegExp(`^${label}$`) }).click()
    await waitFor(async () => (await api(`/tasks/${taskId}`)).body?.enabled === enabled, `Task ${label}`)
    row = await findRow(page, editedTaskName, taskId)
  }
  result.phases.push({ phase: 'task_edit_disable_enable', task_id: taskId, edited: true, disabled_then_enabled: true, status: 'PASS' }); await persistLedger()

  // Follow the actual task detail → schedule form entry. Use a bounded one-time slot only.
  await row.locator(`a[href="/tasks/${taskId}"]`).click()
  await page.waitForURL(new RegExp(`/tasks/${taskId}$`), { timeout: 20_000 })
  await page.getByRole('link', { name: /新建调度|new schedule/i }).click()
  await page.getByRole('combobox').first().getByText(editedTaskName, { exact: false }).waitFor({ timeout: 15_000 })
  await page.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: /^一次性$/ }).click()
  const runAt = page.getByLabel(/^运行时间$/)
  const toLocal = date => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date).replace(' ', 'T')
  await runAt.fill(toLocal(new Date(Date.now() + 210_000)))
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  const initialEnabled = page.getByRole('checkbox').last()
  if (await initialEnabled.isChecked()) await initialEnabled.uncheck()
  const scheduleId = await routeWrite(page, '/schedules', 'POST', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule')
  const initial = await exactRead(`/schedules/${scheduleId}`, 200, 'schedule', scheduleId)
  need(initial.task_id === taskId && initial.type === 'one_time' && initial.enabled === false && initial.execution_owner === 'agent', 'initial Schedule linked, disabled and Agent-owned')
  result.phases.push({ phase: 'schedule_created_disabled', schedule_id: scheduleId, task_id: taskId, type: initial.type, owner: initial.execution_owner, enabled: initial.enabled, run_at: initial.run_at }); await persistLedger()

  await navigate(page, `/schedules/${scheduleId}`)
  await page.getByLabel(/^运行时间$/).fill(toLocal(new Date(Date.now() + 150_000)))
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  const enabled = page.getByRole('checkbox').last()
  if (!(await enabled.isChecked())) await enabled.check()
  await routeWrite(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule edit/enable')
  const active = await exactRead(`/schedules/${scheduleId}`, 200, 'schedule', scheduleId)
  need(active.enabled === true && active.run_at && active.task_id === taskId && active.timezone === 'Asia/Shanghai', 'Schedule update enabled and persisted')
  result.phases.push({ phase: 'schedule_updated_enabled', schedule_id: scheduleId, enabled: true, run_at: active.run_at, timezone: active.timezone, status: 'PASS' }); await persistLedger()

  const scheduled = await waitFor(async () => {
    const response = await api(`/executions?task_id=${taskId}&limit=20`)
    const rows = Array.isArray(response.body) ? response.body : response.body?.items || []
    return rows.find(x => x.trigger_type === 'schedule') || null
  }, 'single scheduled execution', 240_000)
  need(scheduled.node_id === nodeId && scheduled.status === 'SUCCESS' && (scheduled.stdout || '').includes(marker), 'Agent Schedule execution SUCCESS on native-3 with marker')
  result.phases.push({ phase: 'schedule_trigger', schedule_id: scheduleId, execution_id: scheduled.id, trigger_type: scheduled.trigger_type, node_id: scheduled.node_id, status: scheduled.status, marker_observed: scheduled.stdout.includes(marker) }); await persistLedger()
  await navigate(page, `/executions/${scheduled.id}`)
  await page.locator('main').getByText(marker, { exact: false }).waitFor({ timeout: 20_000 })
  await page.screenshot({ path: `${dir}/scheduled-execution-${run}.png`, fullPage: true })

  await navigate(page, `/schedules/${scheduleId}`)
  const toggle = page.getByRole('checkbox').last()
  if (await toggle.isChecked()) await toggle.uncheck()
  await routeWrite(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule disable')
  need((await exactRead(`/schedules/${scheduleId}`, 200, 'schedule', scheduleId)).enabled === false, 'Schedule disabled after one-time run')
  result.phases.push({ phase: 'schedule_disabled', schedule_id: scheduleId, enabled: false, status: 'PASS' }); await persistLedger()

  await page.getByRole('link', { name: /^(调度|schedules)$/i }).first().click()
  await page.waitForURL(url => url.pathname === '/schedules', { timeout: 15_000 })
  await page.getByRole('textbox', { name: /搜索|search/i }).fill(editedTaskName)
  let scheduleRow = page.locator(`tr:has(a[href="/schedules/${scheduleId}"])`)
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need((await exactRead(`/schedules/${scheduleId}`, 200, 'schedule', scheduleId)).id === scheduleId, 'Schedule delete cancel no side effect')
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => (await api(`/schedules/${scheduleId}`)).status === 404, 'Schedule delete GET404')
  resources.find(x => x.id === scheduleId).state = 'deleted_and_get404'; await persistLedger()
  result.phases.push({ phase: 'schedule_delete', schedule_id: scheduleId, cancel_preserved: true, confirmed_delete_get: 404, status: 'PASS' }); await persistLedger()

  await tasksList(page)
  row = await findRow(page, editedTaskName, taskId)
  await row.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need((await exactRead(`/tasks/${taskId}`, 200, 'task', taskId)).id === taskId, 'Task delete cancel no side effect')
  await row.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => (await api(`/tasks/${taskId}`)).status === 404, 'Task delete GET404')
  resources.find(x => x.id === taskId).state = 'deleted_and_get404'; await persistLedger()
  result.phases.push({ phase: 'task_delete', task_id: taskId, cancel_preserved: true, confirmed_delete_get: 404, status: 'PASS' }); await persistLedger()
  result.status = 'PASS'
} catch (error) {
  result.status = 'FAIL_OR_INCOMPLETE'
  result.error = String(error?.message || error).split('\n')[0].slice(0, 300)
} finally {
  for (const resource of [...resources].reverse()) {
    if (resource.state === 'deleted_and_get404') continue
    const del = await api(`${resource.path}/${resource.id}`, 'DELETE')
    const get = await api(`${resource.path}/${resource.id}`)
    const clean = [200, 204, 404].includes(del.status) && get.status === 404
    resource.state = clean ? 'cleanup_get404' : 'cleanup_failed'
    result.cleanup.push({ kind: resource.kind, id: resource.id, delete_http: del.status, verify_get_http: get.status, clean })
    if (!clean) result.status = 'FAIL_CLEANUP'
    await persistLedger()
  }
  if (browser) await browser.close()
  delete c.admin_password; delete c.token; token = undefined
  await writeFile(`${dir}/task-schedule-results-${run}.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ run, status: result.status, phases: result.phases.map(x => ({ phase: x.phase, status: x.status || null })), cleanup: result.cleanup.map(x => ({ kind: x.kind, delete_http: x.delete_http, verify_get_http: x.verify_get_http, clean: x.clean })), error: result.error || null }))
}
