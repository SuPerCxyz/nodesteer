import { createRequire } from 'node:module'
import { randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
const require = createRequire('/home/superc/code/nodesteer/web/package.json')
const { chromium } = require('playwright')
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const nodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const run = `qa-resume-20261008-crosspage-${randomUUID().slice(0, 8)}`
const resources = []
const result = { run, authority: base, node_id: nodeId, cases: {}, requests: [], cleanup: [], status: 'RUNNING' }
let token, browser
const ledgerPath = `${dir}/ledger-${run}.json`
async function persist() { await writeFile(ledgerPath, JSON.stringify({ run, resources, cases: result.cases, updated_at: new Date().toISOString() }, null, 2)) }
function need(ok, msg) { if (!ok) throw new Error(`Assertion failed: ${msg}`) }
async function api(path, method = 'GET') {
  const r = await fetch(`${base}/api${path}`, { method, headers: { Authorization: `Bearer ${token}` } })
  let body = null; try { body = await r.json() } catch {}
  result.requests.push({ method, path, status: r.status })
  return { status: r.status, body }
}
async function writeUI(page, path, method, action, kind) {
  let id, status
  const handler = async route => {
    if (route.request().method() !== method) return route.continue()
    const response = await route.fetch(); status = response.status()
    if (method === 'POST') { const body = await response.json(); id = body.id || body.node_id }
    await route.fulfill({ response })
  }
  await page.route(`${base}/api${path}`, handler); await action()
  await waitFor(() => method === 'POST' ? id : status !== undefined, `${kind} ${method}`)
  await page.unroute(`${base}/api${path}`, handler)
  need(status < 400, `${kind} ${method} HTTP ${status}`)
  if (method === 'POST') { resources.push({ kind, path, id, state: 'created' }); await persist() }
  result.requests.push({ method, path, status, ...(id ? { id } : {}) }); return id
}
async function waitFor(fn, label, timeout = 20_000) {
  const until = Date.now() + timeout
  while (Date.now() < until) { const v = await fn(); if (v) return v; await new Promise(r => setTimeout(r, 500)) }
  throw new Error(`Timed out waiting for ${label}`)
}
async function pageTo(page, path) {
  await page.goto(path, { waitUntil: 'commit', timeout: 15_000 })
  if (path !== '/sign-in') await page.locator('main').waitFor({ state: 'visible', timeout: 15_000 })
}
async function taskList(page) {
  if (new URL(page.url()).pathname !== '/tasks') {
    await page.getByRole('link', { name: /^(任务|tasks)$/i }).first().click()
    await page.waitForURL(u => u.pathname === '/tasks', { waitUntil: 'commit', timeout: 15_000 })
  }
  await page.getByRole('heading', { name: /^(任务|tasks)$/i }).waitFor({ timeout: 15_000 })
}
async function findTask(page, name, id) {
  await page.getByRole('textbox', { name: /搜索任务|search tasks/i }).fill(name)
  const row = page.locator(`tr:has(a[href="/tasks/${id}"])`)
  await row.waitFor({ state: 'visible', timeout: 15_000 }); return row
}

try {
  const l = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
  need(l.ok, `repair login ${l.status}`); const lb = await l.json(); token = lb.token || lb.Token
  browser = await chromium.launch({ headless: true }); const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1000 } }); const page = await context.newPage()
  await pageTo(page, '/sign-in')
  await page.getByLabel(/^(用户名|username)$/i).fill(c.admin_username); await page.getByLabel(/^(密码|password)$/i).fill(c.admin_password)
  await page.getByRole('button', { name: /^(登录|sign in)$/i }).click()
  await page.waitForURL(u => u.origin === base && u.pathname !== '/sign-in', { waitUntil: 'commit', timeout: 20_000 })
  await taskList(page)
  const taskName = `${run}-task`
  await page.getByRole('link', { name: /新建任务|new task/i }).click()
  await page.getByLabel(/^名称$/).fill(taskName)
  await page.getByRole('textbox', { name: /命令|command/i }).fill(`printf '%s\\n' '${run}-fixture'`)
  const node = (await api('/nodes')).body.find(x => x.id === nodeId)
  need(node?.status === 'online' && node?.sync_status === 'synced', 'native-3 online/synced')
  await page.locator('label').filter({ hasText: node.hostname }).filter({ has: page.getByRole('checkbox') }).getByRole('checkbox').check()
  const taskId = await writeUI(page, '/tasks', 'POST', () => page.getByRole('button', { name: /^保存$/ }).click(), 'task')
  const taskDefinition = await api(`/tasks/${taskId}`)
  need(taskDefinition.status === 200 && taskDefinition.body.target?.node_ids?.length === 1 && taskDefinition.body.target.node_ids[0] === nodeId, 'Task target is exactly native-3 only')
  await taskList(page); let taskRow = await findTask(page, taskName, taskId)
  await taskRow.locator(`a[href="/tasks/${taskId}"]`).click()
  await page.waitForURL(u => u.pathname === `/tasks/${taskId}`, { waitUntil: 'commit', timeout: 15_000 })
  const newSchedule = page.getByRole('link', { name: /新建调度|new schedule/i })
  await newSchedule.click()
  await page.getByRole('combobox').first().getByText(taskName, { exact: false }).waitFor({ timeout: 15_000 })
  await page.getByRole('combobox').nth(1).click(); await page.getByRole('option', { name: /^一次性$/ }).click()
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  const future = new Date('2050-01-02T00:00:00.000Z')
  const local = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(future).replace(' ', 'T')
  await page.getByLabel(/^运行时间$/).fill(local)
  const enabled = page.locator('main').getByRole('checkbox'); need(await enabled.count() === 1, 'Schedule form has one enabled checkbox')
  if (await enabled.isChecked()) await enabled.uncheck()
  const scheduleId = await writeUI(page, '/schedules', 'POST', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule')
  let schedule = await api(`/schedules/${scheduleId}`)
  need(schedule.status === 200 && schedule.body.task_id === taskId && schedule.body.enabled === false, 'Schedule linked, future and disabled')

  // Read the actual Task schedule section and follow its exact schedule href.
  await pageTo(page, `/tasks/${taskId}`)
  const linkedSchedule = page.locator(`a[href="/schedules/${scheduleId}"]`)
  await linkedSchedule.waitFor({ state: 'visible', timeout: 15_000 })
  result.cases.task_schedule_link = { status: 'PASS', task_id: taskId, schedule_id: scheduleId, exact_link_visible: true }
  await persist()
  await linkedSchedule.click(); await page.waitForURL(u => u.pathname === `/schedules/${scheduleId}`, { waitUntil: 'commit', timeout: 15_000 })
  await page.locator('main').getByText(taskName, { exact: false }).waitFor({ timeout: 15_000 })
  result.cases.schedule_detail_linked_task = { status: 'PASS', task_id: taskId, schedule_id: scheduleId, task_name_visible: true }

  // Update the future one-time slot and exercise enable/disable without triggering it.
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  await page.getByLabel(/^运行时间$/).fill(new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date('2050-01-03T00:00:00Z')).replace(' ', 'T'))
  await writeUI(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule update')
  const futureCheck = await api(`/schedules/${scheduleId}`); need(futureCheck.body.timezone === 'Asia/Shanghai' && futureCheck.body.enabled === false, 'Schedule update persisted safely disabled')
  await pageTo(page, `/schedules/${scheduleId}`)
  const toggle = page.locator('main').getByRole('checkbox'); need(await toggle.count() === 1, 'Schedule edit form has one enabled checkbox'); await toggle.check()
  await writeUI(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule enable')
  need((await api(`/schedules/${scheduleId}`)).body.enabled === true, 'Schedule enabled')
  await pageTo(page, `/schedules/${scheduleId}`)
  const toggleOff = page.locator('main').getByRole('checkbox'); need(await toggleOff.count() === 1, 'Schedule edit form has one enabled checkbox'); await toggleOff.uncheck()
  await writeUI(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule disable')
  need((await api(`/schedules/${scheduleId}`)).body.enabled === false, 'Schedule disabled')
  result.cases.schedule_update_toggle = { status: 'PASS', schedule_id: scheduleId, edited_future_slot: true, enabled_then_disabled: true, trigger_suppressed_future: true }
  await persist()

  await pageTo(page, '/schedules')
  await page.getByRole('textbox', { name: /搜索|search/i }).fill(taskName)
  let scheduleRow = page.locator(`tr:has(a[href="/schedules/${scheduleId}"])`)
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
  let scheduleDeletes = 0
  const countScheduleDelete = req => { if (req.method() === 'DELETE' && new URL(req.url()).pathname === `/api/schedules/${scheduleId}`) scheduleDeletes++ }
  page.on('request', countScheduleDelete)
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need(scheduleDeletes === 0 && (await api(`/schedules/${scheduleId}`)).status === 200, 'Schedule cancel sent zero DELETE and resource remains')
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => scheduleDeletes === 1 && (await api(`/schedules/${scheduleId}`)).status === 404, 'Schedule exact DELETE and GET404')
  page.off('request', countScheduleDelete); resources.find(x => x.id === scheduleId).state = 'deleted_and_get404'; await persist()
  const schedulesReload = page.waitForResponse(r => new URL(r.url()).pathname === '/api/schedules' && r.request().method() === 'GET', { timeout: 20_000 })
  await pageTo(page, `/tasks/${taskId}`)
  await schedulesReload
  await page.locator('main').getByText(taskName, { exact: false }).waitFor({ timeout: 15_000 })
  need(await page.locator(`a[href="/schedules/${scheduleId}"]`).count() === 0, 'Task detail no longer links deleted Schedule')
  result.cases.schedule_delete_crosspage = { status: 'PASS', schedule_id: scheduleId, cancel_delete_count: 0, confirm_delete_count: scheduleDeletes, get_after_delete: 404, task_detail_link_removed: true }
  await persist()

  await taskList(page); taskRow = await findTask(page, taskName, taskId)
  await taskRow.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
  let taskDeletes = 0
  const countTaskDelete = req => { if (req.method() === 'DELETE' && new URL(req.url()).pathname === `/api/tasks/${taskId}`) taskDeletes++ }
  page.on('request', countTaskDelete)
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need(taskDeletes === 0 && (await api(`/tasks/${taskId}`)).status === 200, 'Task cancel sent zero DELETE and resource remains')
  await taskRow.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => taskDeletes === 1 && (await api(`/tasks/${taskId}`)).status === 404, 'Task exact DELETE and GET404')
  page.off('request', countTaskDelete); resources.find(x => x.id === taskId).state = 'deleted_and_get404'; await persist()
  result.cases.task_delete = { status: 'PASS', task_id: taskId, cancel_delete_count: 0, confirm_delete_count: taskDeletes, get_after_delete: 404 }
  result.status = 'PASS'
} catch (error) {
  result.status = 'FAIL_OR_INCOMPLETE'; result.error = String(error?.message || error).split('\n')[0].slice(0, 300)
} finally {
  for (const resource of [...resources].reverse()) {
    if (resource.state === 'deleted_and_get404') continue
    const del = await api(`${resource.path}/${resource.id}`, 'DELETE'); const get = await api(`${resource.path}/${resource.id}`)
    const clean = [200, 204, 404].includes(del.status) && get.status === 404
    resource.state = clean ? 'cleanup_get404' : 'cleanup_failed'
    result.cleanup.push({ kind: resource.kind, id: resource.id, delete_http: del.status, verify_get_http: get.status, clean }); await persist()
    if (!clean) result.status = 'FAIL_CLEANUP'
  }
  if (browser) await browser.close(); delete c.admin_password; delete c.token; token = undefined
  await writeFile(`${dir}/crosspage-delete-results-${run}.json`, JSON.stringify(result, null, 2))
  console.log(JSON.stringify({ run, status: result.status, cases: result.cases, cleanup: result.cleanup, error: result.error || null }))
}
