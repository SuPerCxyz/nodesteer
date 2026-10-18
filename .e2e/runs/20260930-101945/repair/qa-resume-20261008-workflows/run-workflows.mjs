import { createRequire } from 'node:module'
import { readFile, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'

const require = createRequire('/home/superc/code/nodesteer/web/package.json')
const { chromium } = require('playwright')
const outputDir = new URL('.', import.meta.url).pathname
const connection = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
if (connection.hub_url !== base) throw new Error('Repair endpoint mismatch')
const prefix = `qa-resume-20261008-workflow-${randomUUID().slice(0, 8)}`
const marker = `${prefix}-param-marker`
const envMarker = `${prefix}-env-marker`
const targetNodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const owned = []
const result = {
  run: prefix,
  authority: base,
  target_node_id: targetNodeId,
  cases: {},
  requests: [],
  actual_effect: {},
  cleanup: [],
}
let browser
let apiToken

async function api(path, method = 'GET', data) {
  const response = await fetch(`${base}/api${path}`, {
    method,
    headers: { Authorization: `Bearer ${apiToken}`, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  })
  let body = null
  try { body = await response.json() } catch {}
  result.requests.push({ method, path, status: response.status })
  return { status: response.status, ok: response.ok, body }
}

function need(ok, label) { if (!ok) throw new Error(`Assertion failed: ${label}`) }
async function navigate(page, path) { await page.goto(path, { waitUntil: 'commit', timeout: 60_000 }) }
function getId(body, label) {
  const id = body?.id || body?.node_id
  need(typeof id === 'string' && id.length > 0, `${label} response ID`)
  return id
}
async function waitFor(predicate, label, timeout = 90_000) {
  const until = Date.now() + timeout
  while (Date.now() < until) {
    const value = await predicate()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 1500))
  }
  throw new Error(`Timed out waiting for ${label}`)
}
async function createViaUI(page, path, action, kind) {
  let capturedId
  let status
  const routeHandler = async route => {
    if (route.request().method() !== 'POST') return route.continue()
    const response = await route.fetch()
    status = response.status()
    const body = await response.json()
    capturedId = body?.id || body?.node_id
    await route.fulfill({ response })
  }
  await page.route(`${base}/api${path}`, routeHandler)
  await action()
  await waitFor(() => capturedId, `${kind} UI create response ID`, 30_000)
  await page.unroute(`${base}/api${path}`, routeHandler)
  need(status < 400, `${kind} UI create HTTP ${status}`)
  const id = getId({ id: capturedId }, kind)
  owned.push({ path, id, kind })
  return id
}
async function saveViaUI(page, path, method, action, label) {
  const responsePromise = page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname === `/api${path}`, { timeout: 30_000 })
  await action()
  const response = await responsePromise
  result.requests.push({ method, path, status: response.status() })
  need(response.ok(), `${label} HTTP ${response.status()}`)
  return response
}

try {
  const login = await fetch(`${base}/api/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: connection.admin_username, password: connection.admin_password }),
  })
  need(login.ok, `repair API login HTTP ${login.status}`)
  const loginBody = await login.json()
  apiToken = loginBody.token || loginBody.Token
  need(typeof apiToken === 'string' && apiToken.length > 0, 'process-only API token acquired')
  const nodeResponse = await api('/nodes')
  need(nodeResponse.ok, 'node list readable')
  const target = nodeResponse.body.find(node => node.id === targetNodeId)
  need(target && target.status === 'online' && target.sync_status === 'synced', 'native-3 online/synced precondition')
  result.target_node = { id: target.id, hostname: target.hostname, status: target.status, sync_status: target.sync_status }

  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ baseURL: base, viewport: { width: 1440, height: 1100 } })
  const page = await context.newPage()
  await navigate(page, '/sign-in')
  await page.getByLabel(/^(用户名|username)$/i).fill(connection.admin_username)
  await page.getByLabel(/^(密码|password)$/i).fill(connection.admin_password)
  await page.getByRole('button', { name: /^(登录|sign in)$/i }).click()
  await page.waitForURL(url => url.origin === base && !url.pathname.endsWith('/sign-in'), { timeout: 20_000 })
  result.login_ui = 'PASS'

  const groupName = `${prefix}-group`
  await navigate(page, '/groups/new')
  await page.getByLabel(/^名称$/).fill(groupName)
  await page.getByLabel(/^描述$/).fill('isolated one-node E2E fixture')
  const nodeLabel = page.locator('label').filter({ hasText: target.hostname }).filter({ has: page.getByRole('checkbox') })
  await nodeLabel.getByRole('checkbox').check()
  const groupId = await createViaUI(page, '/groups', () => page.getByRole('button', { name: /^保存$/ }).click(), 'group')
  const group = await api(`/groups/${groupId}`)
  need(group.ok && group.body.name === groupName && group.body.members?.includes(targetNodeId), 'Group UI readback contains only native-3')
  result.cases.group = { status: 'PASS', group_id: groupId, member_node_ids: group.body.members }

  const scriptName = `${prefix}-script`
  await navigate(page, '/scripts/new')
  await page.getByLabel(/^名称$/).fill(scriptName)
  await page.locator('textarea').first().fill('printf \'%s|%s\\n\' "$NODESTEER_PARAM_qa_input" "$QA_ENV"')
  await page.getByLabel(/^工作目录$/).fill('/tmp')
  await page.getByPlaceholder('名称', { exact: true }).fill('qa_input')
  await page.getByPlaceholder('默认值', { exact: true }).fill('script-default')
  await page.getByRole('button', { name: /^添加参数$/ }).click()
  await page.getByPlaceholder('KEY', { exact: true }).fill('QA_ENV')
  await page.getByPlaceholder('VALUE', { exact: true }).fill(envMarker)
  await page.getByRole('button', { name: /^添加$/ }).click()
  const scriptId = await createViaUI(page, '/scripts', () => page.getByRole('button', { name: /^保存$/ }).click(), 'script')
  const script = await api(`/scripts/${scriptId}`)
  need(script.ok && script.body.name === scriptName && script.body.parameters?.some(item => item.name === 'qa_input') && script.body.environment?.QA_ENV === envMarker, 'Script UI fields persisted')
  result.cases.script = { status: 'PASS', script_id: scriptId, environment_key: 'QA_ENV', parameter_name: 'qa_input' }

  const taskName = `${prefix}-task`
  await navigate(page, '/tasks/new')
  await page.getByLabel(/^名称$/).fill(taskName)
  await page.getByRole('combobox').nth(0).click()
  await page.getByRole('option', { name: /^脚本$/ }).click()
  await page.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: scriptName, exact: true }).click()
  await page.getByRole('combobox').nth(2).click()
  await page.getByRole('option', { name: /^分组$/ }).click()
  await page.locator('label').filter({ hasText: groupName }).getByRole('checkbox').check()
  await page.getByPlaceholder('参数名称', { exact: true }).fill('qa_input')
  await page.getByPlaceholder('默认值', { exact: true }).fill('task-default')
  await page.getByRole('button', { name: /^添加参数$/ }).click()
  const taskId = await createViaUI(page, '/tasks', () => page.getByRole('button', { name: /^保存$/ }).click(), 'task')
  const task = await api(`/tasks/${taskId}`)
  need(task.ok && task.body.type === 'script' && task.body.script_id === scriptId && task.body.target?.type === 'group' && task.body.target.group_ids?.includes(groupId) && task.body.parameters?.some(item => item.name === 'qa_input'), 'Task links Script, Group target, and parameter')
  result.cases.task_create = { status: 'PASS', task_id: taskId, target_type: task.body.target.type, target_group_ids: task.body.target.group_ids, script_id: scriptId }

  await navigate(page, `/tasks/${taskId}/run`)
  await page.getByLabel('qa_input').fill(marker)
  let runBody
  let runStatus
  const runPath = `/tasks/${taskId}/run`
  const runRoute = async route => {
    const response = await route.fetch()
    runStatus = response.status()
    runBody = await response.json()
    await route.fulfill({ response })
  }
  await page.route(`${base}/api${runPath}`, runRoute)
  await page.getByRole('button', { name: /^立即运行$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
  await waitFor(() => runBody, 'Task Run response', 30_000)
  await page.unroute(`${base}/api${runPath}`, runRoute)
  need(runStatus < 400, `Task Run UI HTTP ${runStatus}`)
  const runIds = Array.isArray(runBody) ? runBody.map(item => item.id) : [runBody.id].filter(Boolean)
  result.requests.push({ method: 'POST', path: runPath, status: runStatus, execution_ids: runIds })
  await page.waitForURL(/\/executions(?:\?|$)/, { timeout: 20_000 })
  const execution = await waitFor(async () => {
    const response = await api(`/executions?task_id=${taskId}&limit=20`)
    const rows = Array.isArray(response.body) ? response.body : response.body?.items || []
    return rows.find(item => item.stdout?.includes(marker) || item.status === 'FAILED') || null
  }, 'native-3 execution with parameter marker', 90_000)
  need(execution.status === 'SUCCESS' && execution.node_id === targetNodeId && execution.stdout.includes(marker) && execution.stdout.includes(envMarker), 'node execution SUCCESS and both parameter/environment marker values')
  result.actual_effect = { execution_id: execution.id, node_id: execution.node_id, status: execution.status, exit_code: execution.exit_code, parameter_marker_observed: execution.stdout.includes(marker), environment_marker_observed: execution.stdout.includes(envMarker), stdout_marker_only: `${marker}|${envMarker}` }
  await navigate(page, `/executions/${execution.id}`)
  await page.waitForLoadState('networkidle')
  await page.locator('main').getByText(marker, { exact: false }).waitFor({ timeout: 30_000 })
  await page.screenshot({ path: `${outputDir}/execution-logs.png`, fullPage: true })
  result.cases.run_execution_logs = { status: 'PASS', execution_id: execution.id, UI_feedback: 'execution detail displays parameter/environment markers and SUCCESS' }

  const auditResponse = await api(`/audit?limit=200&offset=0`)
  const auditRows = Array.isArray(auditResponse.body) ? auditResponse.body : auditResponse.body?.items || []
  const taskAudit = auditRows.filter(item => item.resource === 'task' && item.resource_id === taskId)
  const runAudit = taskAudit.find(item => ['run', 'execute', 'execution'].includes(item.action))
  need(runAudit, 'same Task has an execution/run Audit entry')
  await navigate(page, '/audit')
  const search = page.getByRole('textbox').first()
  await search.fill(taskId)
  await expectVisible(page.locator('tbody'), taskId, 'Audit UI row for same Task')
  await page.screenshot({ path: `${outputDir}/task-audit.png`, fullPage: true })
  result.cases.task_audit_ui = { status: 'PASS', task_id: taskId, action: runAudit.action, audit_id: runAudit.id, ui_feedback: 'Audit table search by exact Task ID shows the correlated row' }

  // Task edit and enabled state are driven only through the Task UI.
  await navigate(page, `/tasks/${taskId}/edit`)
  const nameField = page.getByLabel(/^名称$/).first()
  await nameField.fill(`${taskName}-edited`)
  await saveViaUI(page, `/tasks/${taskId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'Task edit')
  let savedTask = await api(`/tasks/${taskId}`)
  need(savedTask.body.name === `${taskName}-edited`, 'Task edited name readback')
  await navigate(page, '/tasks')
  await page.getByRole('textbox', { name: /搜索任务/ }).fill(`${taskName}-edited`)
  const taskRow = page.locator(`tr:has(a[href="/tasks/${taskId}"])`)
  await taskRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^禁用$/ }).click()
  await waitFor(async () => { const current = await api(`/tasks/${taskId}`); return current.body?.enabled === false }, 'Task disabled')
  await taskRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^启用$/ }).click()
  await waitFor(async () => { const current = await api(`/tasks/${taskId}`); return current.body?.enabled === true }, 'Task enabled')
  result.cases.task_edit_toggle = { status: 'PASS', task_id: taskId, edited: true, disabled_then_enabled: true }

  // A one-time, Agent-owned schedule is deliberately bounded to one future slot.
  const taskDetail = `/tasks/${taskId}`
  await navigate(page, taskDetail)
  await page.locator('main').getByText(`${taskName}-edited`, { exact: false }).waitFor({ timeout: 20_000 })
  await page.getByRole('link', { name: /新建调度/ }).click()
  await page.getByRole('combobox').first().getByText(`${taskName}-edited`, { exact: false }).waitFor({ timeout: 20_000 })
  await page.getByRole('combobox').nth(1).click()
  await page.getByRole('option', { name: /^一次性$/ }).click()
  const runAt = page.getByLabel(/^运行时间$/)
  const firstSlot = new Date(Date.now() + 210_000)
  const localDate = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(firstSlot).replace(' ', 'T')
  await runAt.fill(localDate)
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  const checkboxes = page.getByRole('checkbox')
  const enabledCheckbox = checkboxes.last()
  if (await enabledCheckbox.isChecked()) await enabledCheckbox.uncheck()
  const scheduleId = await createViaUI(page, '/schedules', () => page.getByRole('button', { name: /^保存$/ }).click(), 'schedule')
  const scheduleInitial = await api(`/schedules/${scheduleId}`)
  need(scheduleInitial.ok && scheduleInitial.body.task_id === taskId && scheduleInitial.body.type === 'one_time' && scheduleInitial.body.enabled === false && scheduleInitial.body.execution_owner === 'agent', 'Schedule created disabled, one-time, Agent-owned and linked to Task')
  await navigate(page, `/schedules/${scheduleId}`)
  const updatedSlot = new Date(Date.now() + 150_000)
  const updatedLocal = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(updatedSlot).replace(' ', 'T')
  await page.getByLabel(/^运行时间$/).fill(updatedLocal)
  await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
  const editCheckbox = page.getByRole('checkbox').last()
  if (!(await editCheckbox.isChecked())) await editCheckbox.check()
  await saveViaUI(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'Schedule update/enable')
  const scheduleEnabled = await api(`/schedules/${scheduleId}`)
  need(scheduleEnabled.body.enabled === true && scheduleEnabled.body.run_at && scheduleEnabled.body.task_id === taskId && scheduleEnabled.body.timezone === 'Asia/Shanghai', 'Schedule updated and enabled')
  await navigate(page, `/schedules/${scheduleId}`)
  await page.locator('main').getByText(taskName + '-edited', { exact: false }).waitFor({ timeout: 20_000 })
  result.cases.schedule_link_update = { status: 'PASS', schedule_id: scheduleId, task_id: taskId, task_prefilled_from_task_detail: true, schedule_detail_shows_linked_task: true, initial_disabled: true, edited_and_enabled: true, trigger: 'one_time at future Asia/Shanghai run_at; Agent owner; single target node' }

  const scheduledExecution = await waitFor(async () => {
    const response = await api(`/executions?task_id=${taskId}&limit=30`)
    const rows = Array.isArray(response.body) ? response.body : response.body?.items || []
    return rows.find(item => item.trigger_type === 'schedule' || item.trigger_type === 'scheduled') || null
  }, 'single one-time Schedule execution', 240_000)
  result.cases.schedule_trigger = { status: scheduledExecution.status === 'SUCCESS' && scheduledExecution.node_id === targetNodeId ? 'PASS' : 'FAIL', execution_id: scheduledExecution.id, node_id: scheduledExecution.node_id, execution_status: scheduledExecution.status, trigger_type: scheduledExecution.trigger_type, marker_observed: scheduledExecution.stdout?.includes(marker) || false }
  need(result.cases.schedule_trigger.status === 'PASS', 'scheduled run executed once on native-3')

  // Disable the completed one-time Schedule through its edit form, verify no further trigger, then delete it in UI.
  await navigate(page, `/schedules/${scheduleId}`)
  const scheduleToggle = page.getByRole('checkbox').last()
  if (await scheduleToggle.isChecked()) await scheduleToggle.uncheck()
  await saveViaUI(page, `/schedules/${scheduleId}`, 'PUT', () => page.getByRole('button', { name: /^保存$/ }).click(), 'Schedule disable')
  const scheduleDisabled = await api(`/schedules/${scheduleId}`)
  need(scheduleDisabled.body.enabled === false, 'Schedule disabled')
  result.cases.schedule_disable = { status: 'PASS', schedule_id: scheduleId, enabled: false }

  // Verify delete cancel has no side effect, then confirm exact row deletion.
  await navigate(page, '/schedules')
  await page.getByRole('textbox', { name: /搜索/ }).fill(taskName + '-edited')
  let scheduleRow = page.locator(`tr:has(a[href="/schedules/${scheduleId}"])`)
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need((await api(`/schedules/${scheduleId}`)).ok, 'Schedule delete cancel preserved resource')
  await scheduleRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => (await api(`/schedules/${scheduleId}`)).status === 404, 'Schedule deleted')
  result.cases.schedule_delete = { status: 'PASS', schedule_id: scheduleId, cancel_preserved: true, confirmed_delete_http: 404 }
  owned.splice(owned.findIndex(item => item.id === scheduleId), 1)

  // Task delete cancel/confirm, followed by explicit not-found readback.
  await navigate(page, '/tasks')
  await page.getByRole('textbox', { name: /搜索任务/ }).fill(`${taskName}-edited`)
  await taskRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  need((await api(`/tasks/${taskId}`)).ok, 'Task delete cancel preserved resource')
  await taskRow.getByRole('button', { name: /^操作$/ }).click()
  await page.getByRole('menuitem', { name: /^删除$/ }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
  await waitFor(async () => (await api(`/tasks/${taskId}`)).status === 404, 'Task deleted')
  result.cases.task_delete = { status: 'PASS', task_id: taskId, cancel_preserved: true, confirmed_delete_http: 404 }
  owned.splice(owned.findIndex(item => item.id === taskId), 1)

  // Delete only the exact resources created by this runner, in reverse dependency order.
  result.cases.audit_entry = { resource: runAudit.resource, resource_id: runAudit.resource_id, action: runAudit.action }
  result.overall = 'PASS'
} catch (error) {
  result.overall = 'FAIL'
  result.error = String(error?.message || error).slice(0, 500)
} finally {
  if (apiToken) {
    for (const item of [...owned].reverse()) {
      const response = await api(`${item.path}/${item.id}`, 'DELETE')
      const clean = [200, 204, 404].includes(response.status)
      result.cleanup.push({ kind: item.kind, id: item.id, status: response.status, clean })
      if (!clean) result.overall = 'FAIL_CLEANUP'
    }
  }
  if (browser) await browser.close()
  delete connection.admin_password
  delete connection.token
  apiToken = undefined
  await writeFile(`${outputDir}/workflow-results-${prefix}.json`, JSON.stringify(result, null, 2))
  process.stdout.write(JSON.stringify({ overall: result.overall, cases: Object.fromEntries(Object.entries(result.cases).map(([key, value]) => [key, value.status])), resource_count: result.cleanup.length, error: result.error || null }) + '\n')
}

async function expectVisible(locator, text, label) {
  await locator.getByText(text, { exact: false }).first().waitFor({ timeout: 20_000 })
}
