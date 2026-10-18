import { test, expect, type Browser, type BrowserContext, type Page } from 'playwright/test'
import { createHash, randomUUID } from 'node:crypto'
import { appendFile, readFile, writeFile } from 'node:fs/promises'
import { apiRequest, captureCreated, cleanupByIds, createIsolatedTask, getNodeIds, loginViaApi, testName } from './fixtures'

const QA_ROLE_USER_ID = '8ac0ef0f-fea5-4be3-8707-2c580363353b'

async function qaRoleAccount() {
  const path = process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl'
  const rows = (await readFile(path, 'utf8')).trim().split('\n').map(line => JSON.parse(line) as { user_id?: string; username?: string; password?: string; role?: string })
  const account = [...rows].reverse().find(row => row.user_id === QA_ROLE_USER_ID && row.password)
  if (!account?.username || !account.password) throw new Error('Exact retained QA user password is missing from private storage')
  return { username: account.username, password: account.password }
}

async function setQaRole(adminPage: Page, role: 'operator' | 'viewer') {
  const response = await apiRequest(adminPage, `/api/users/${QA_ROLE_USER_ID}`, { method: 'PUT', data: { role } })
  if (response.status !== 200) throw new Error(`QA user role restore/set failed: HTTP ${response.status}`)
  const users = await apiRequest(adminPage, '/api/users')
  const current = (users.body as { id: string; role: string }[]).find(user => user.id === QA_ROLE_USER_ID)
  expect(current).toMatchObject({ id: QA_ROLE_USER_ID, role })
}

async function openQaRoleSession(browser: Browser, origin: string, account: { username: string; password: string }, role: 'operator' | 'viewer') {
  const context = await browser.newContext({ baseURL: origin })
  const page = await context.newPage()
  const token = await loginViaApi(page, account.username, account.password)
  await appendFile(process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl', JSON.stringify({ user_id: QA_ROLE_USER_ID, role, token, purpose: 'authorized fresh role E2E session' }) + '\n', { mode: 0o600 })
  const session = { context, page, token }
  try {
    const me = await apiRequest(page, '/api/me')
    expect(me.body).toMatchObject({ Role: role, UserID: QA_ROLE_USER_ID })
    return session
  } catch (error) {
    await closeQaRoleSession(session)
    throw error
  }
}

async function closeQaRoleSession(session: { context: BrowserContext; page: Page; token: string } | undefined) {
  if (!session) return null
  try { return (await session.page.request.post('/api/logout', { headers: { Authorization: `Bearer ${session.token}` } })).status() }
  finally { await session.context.close() }
}

async function verifyReadonlyAudit(page: Page, userId: string, auditId?: string) {
  const path = `/api/audit?user_id=${userId}&limit=100`
  const listed = await apiRequest(page, path)
  expect(listed.status).toBe(200)
  const rows = listed.body as { id: string; user_id: string; username: string; resource: string; resource_id: string; action: string; detail: string; created_at: string }[]
  const target = auditId ? rows.find(row => row.id === auditId) : rows.filter(row => row.resource === 'auth' && row.action === 'login').sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0]
  expect(target, 'exact QA-owned Audit record must exist').toBeTruthy()
  await page.goto('/audit')
  await page.getByRole('combobox').last().click()
  await page.getByRole('option', { name: '50', exact: true }).click()
  const search = page.getByRole('textbox', { name: /搜索/ })
  await search.fill(target!.resource_id || target!.username)
  const matchingRows = page.locator('tbody tr').filter({ hasText: target!.username }).filter({ hasText: target!.action }).filter({ hasText: target!.resource })
  const uiMatchingRows = await matchingRows.count()
  expect(uiMatchingRows).toBeGreaterThan(0)
  const uiRow = matchingRows.first()
  await expect(uiRow).toBeVisible()
  const mutationControls = await uiRow.getByRole('button', { name: /编辑|删除|edit|delete/i }).count()
  expect(mutationControls).toBe(0)
  const probes = [] as { method: string; status: number; target_unchanged_in_response: boolean }[]
  for (const method of ['PUT', 'DELETE']) {
    const response = await apiRequest(page, path, { method, data: { ...target, action: 'qa-forbidden-mutation', detail: 'must remain immutable' } })
    const responseRows = response.body as typeof rows
    const returned = Array.isArray(responseRows) ? responseRows.find(row => row.id === target!.id) : undefined
    probes.push({ method, status: response.status, target_unchanged_in_response: JSON.stringify(returned) === JSON.stringify(target) })
    expect(response.status).toBe(200)
    expect(returned).toEqual(target)
  }
  const after = await apiRequest(page, path)
  const afterTarget = (after.body as typeof rows).find(row => row.id === target!.id)
  expect(afterTarget).toEqual(target)
  return { audit_id: target!.id, audit_before: target, probes, audit_after: afterTarget, ui_matching_rows: uiMatchingRows, ui_mutation_controls: mutationControls }
}

async function runViaUI(page: Page, id: string) {
  await page.goto(`/tasks/${id}/run`)
  await page.getByRole('button', { name: /^立即运行$|^run now$/i }).click()
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/tasks/${id}/run`)
  await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$|^run now$/i }).click()
  expect((await response).ok()).toBeTruthy()
  await page.waitForURL(/\/executions$/)
}

async function successExecution(page: Page, taskId: string, marker: string) {
  let execution: { id: string; node_id: string; status: string; stdout?: string } | undefined
  await expect.poll(async () => {
    const response = await apiRequest(page, `/api/executions?task_id=${taskId}&limit=20`)
    execution = (response.body as typeof execution[])?.find((item) => item?.status === 'SUCCESS')
    return execution?.stdout || ''
  }, { timeout: 30_000 }).toContain(marker)
  return execution!
}

async function deleteViaUI(page: Page, path: string, row: ReturnType<Page['locator']>) {
  await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
  await page.getByRole('menuitem', { name: /^删除$|^delete$/i }).click()
  await page.getByRole('alertdialog').getByRole('button', { name: /^取消$|^cancel$/i }).click()
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
  await page.getByRole('menuitem', { name: /^删除$|^delete$/i }).click()
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === path && r.request().method() === 'DELETE')
  await page.getByRole('alertdialog').getByRole('button', { name: /^删除$|^delete$/i }).click()
  expect((await response).ok()).toBeTruthy()
  await expect(row).toHaveCount(0)
}

test.describe('FULL 当前页面真实业务补测', () => {
  test('FULL-01 Agent4标签→Label Group→任务→真实目标执行', async ({ page }) => {
    await loginViaApi(page)
    const nodeId = process.env.NODESTEER_E2E_LABEL_NODE_ID
    if (!nodeId) throw new Error('NODESTEER_E2E_LABEL_NODE_ID is required')
    const baseline = (await apiRequest(page, `/api/nodes/${nodeId}`)).body as { labels: Record<string,string>; hostname: string }
    const key = `qa_web_${randomUUID().slice(0,8)}`
    const marker = testName('label-marker')
    const owned: string[] = []
    try {
      await page.goto(`/agents/${nodeId}`)
      await page.getByPlaceholder('key', { exact: true }).fill(key)
      await page.getByPlaceholder('value', { exact: true }).fill(marker)
      await page.getByRole('button', { name: /^添加标签$|^add label$/i }).click()
      await expect.poll(async () => ((await apiRequest(page, `/api/nodes/${nodeId}`)).body as { labels: Record<string,string> }).labels[key]).toBe(marker)
      const groupName = testName('label-group')
      await page.goto('/groups/new')
      await page.getByLabel(/^名称$|^name$/i).fill(groupName)
      await page.getByRole('combobox').first().click()
      await page.getByRole('option', { name: /^标签$|^label$/i }).click()
      await page.getByLabel(/^标签键$|^label key$/i).fill(key)
      await page.getByLabel(/^标签值$|^label value$/i).fill(marker)
      const groupId = await captureCreated(page, '/api/groups', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      owned.unshift(`/api/groups/${groupId}`)
      await page.waitForURL(/\/groups$/)
      await page.getByRole('textbox', { name: /搜索分组|search groups/i }).fill(groupName)
      const row = page.locator("tr").filter({ hasText: groupName })
      await expect(row).toContainText('1')
      await page.goto('/tasks/new')
      await page.getByLabel(/^名称$|^name$/i).fill(testName('group-task'))
      await page.getByLabel(/^命令$|^command$/i).fill(`printf '%s\\n' '${marker}'`)
      await page.getByRole('combobox').nth(1).click()
      await page.getByRole('option', { name: /^分组$|^groups?$/i }).click()
      await page.locator('label').filter({ hasText: groupName }).getByRole('checkbox').check()
      const taskId = await captureCreated(page, '/api/tasks', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      owned.unshift(`/api/tasks/${taskId}`)
      await page.waitForURL(/\/tasks$/)
      await runViaUI(page, taskId)
      const exec = await successExecution(page, taskId, marker)
      expect(exec.node_id).toBe(nodeId)
      await page.goto(`/executions/${exec.id}`)
      await expect(page.locator('pre')).toContainText(marker)
      await page.goto(`/agents/${nodeId}`)
      await expect(page.locator('main')).toContainText(groupName)
      await page.getByPlaceholder('key',{exact:true}).fill(key)
      await page.getByPlaceholder('value',{exact:true}).fill(marker+'-unmatched')
      await page.getByRole('button',{name:/^添加标签$|^add label$/i}).click()
      await expect.poll(async()=>((await apiRequest(page,`/api/nodes/${nodeId}`)).body as {labels:Record<string,string>}).labels[key]).toBe(marker+'-unmatched')
      await page.goto('/groups')
      await page.getByRole('textbox',{name:/搜索分组|search groups/i}).fill(groupName)
      await expect(page.locator('tr').filter({hasText:groupName})).toContainText('0')
      await page.goto(`/tasks/${taskId}/run`)
      await page.getByRole('button',{name:/^立即运行$|^run now$/i}).click()
      const emptyResponse=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/tasks/${taskId}/run`)
      await page.getByRole('alertdialog').getByRole('button',{name:/^立即运行$|^run now$/i}).click()
      const status=(await emptyResponse).status()
      expect.soft(status,'empty group should give explicit non-executable feedback').toBeGreaterThanOrEqual(400)
      await expect.poll(async()=>((await apiRequest(page,`/api/executions?task_id=${taskId}&limit=20`)).body as {id:string}[]).length).toBe(1)
      await page.goto(`/agents/${nodeId}`)
      await expect(page.locator('main')).not.toContainText(groupName)
      await page.getByPlaceholder('key',{exact:true}).fill(key)
      await page.getByPlaceholder('value',{exact:true}).fill(marker)
      await page.getByRole('button',{name:/^添加标签$|^add label$/i}).click()
      await expect.poll(async()=>((await apiRequest(page,`/api/nodes/${nodeId}`)).body as {labels:Record<string,string>}).labels[key]).toBe(marker)
      await runViaUI(page,taskId)
      await expect.poll(async()=>((await apiRequest(page,`/api/executions?task_id=${taskId}&limit=20`)).body as {node_id:string;status:string}[]).filter(e=>e.status==='SUCCESS'&&e.node_id===nodeId).length).toBe(2)
    } finally {
      try { await cleanupByIds(page, owned) } finally {
      const current = (await apiRequest(page, `/api/nodes/${nodeId}`)).body as { labels: Record<string,string> }
      const labels = { ...current.labels }
      delete labels[key]
      const restored = await apiRequest(page, `/api/nodes/${nodeId}`, { method: 'POST', data: { labels } })
      expect(restored.ok).toBeTruthy()
      for (const [k,v] of Object.entries(baseline.labels)) expect(labels[k]).toBe(v)
      }
    }
  })

  test('FULL-02 制品UI上传/下载→应用预选/分配/取消分配/版本/删除', async ({ page }) => {
    const token = await loginViaApi(page)
    const owned: string[] = []
    const buffer = Buffer.from('#!/bin/sh\necho qa-artifact\n')
    try {
      await page.goto('/artifacts/new')
      await page.getByLabel(/^名称$|^name$/i).fill(testName('artifact'))
      await page.getByLabel(/^版本$|^version$/i).fill('v1')
      await page.locator('input[type=file]').setInputFiles({ name: testName('payload')+'.sh', mimeType: 'application/octet-stream', buffer })
      const artifactId = await captureCreated(page, '/api/artifacts', () => page.getByRole('button', { name: /^上传$|^upload$/i }).click())
      owned.unshift(`/api/artifacts/${artifactId}`)
      await page.waitForURL(/\/artifacts$/)
      const result = await page.request.get(`/api/artifacts/${artifactId}/download`, { headers: { Authorization: `Bearer ${token}` } })
      expect(result.ok()).toBeTruthy()
      expect(await result.body()).toEqual(buffer)
      await page.goto(`/applications/new?artifact_id=${artifactId}`)
      await expect(page.getByLabel(/^版本$|^version$/i)).toHaveValue('v1')
      const applicationName = testName('application')
      await page.getByLabel(/^名称$|^name$/i).fill(applicationName)
      await page.getByLabel(/^二进制路径$|^binary path$/i).fill('/tmp/'+testName('binary'))
      await page.getByLabel(/^Unit$/).fill('nodesteer-'+testName('unit')+'.service')
      const nodeIds = await getNodeIds(page)
      const nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string }[]
      const node = nodes.find((n) => n.id === nodeIds[0])!
      await page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox').check()
      const applicationId = await captureCreated(page, '/api/applications', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      owned.unshift(`/api/applications/${applicationId}`)
      await page.waitForURL(new RegExp(`/applications/${applicationId}$`))
      await expect.poll(async () => (await apiRequest(page, `/api/applications/${applicationId}/nodes`)).body).toEqual([node.id])
      await page.goto(`/agents/${node.id}`)
      await expect(page.getByRole('link', { name: applicationName, exact: true })).toBeVisible()
      await page.goto(`/applications/${applicationId}`)
      await page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox').uncheck()
      await page.getByLabel(/^版本$|^version$/i).fill('v2')
      await page.getByRole('button', { name: /^保存$|^save$/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/applications/${applicationId}/nodes`)).body || []).toEqual([])
      await expect.poll(async () => (await apiRequest(page, `/api/applications/${applicationId}`)).body).toMatchObject({ version: 'v2', revision: 2 })
      await page.goto('/applications')
      await deleteViaUI(page, `/api/applications/${applicationId}`, page.locator(`tr:has(a[href="/applications/${applicationId}"])`))
    } finally { await cleanupByIds(page, owned) }
  })

  test('FULL-03 Script Secret默认值应遮蔽（仅非敏感标记）', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/scripts/new')
    await page.getByPlaceholder('名称', { exact: true }).fill('qa_secret')
    await page.getByRole('combobox').nth(1).click()
    await page.getByRole('option', { name: 'secret', exact: true }).click()
    const marker = 'QA_NON_SENSITIVE_SECRET_MARKER'
    await page.getByPlaceholder('默认值', { exact: true }).fill(marker)
    await page.getByRole('button', { name: /^添加参数$/ }).click()
    await expect(page.locator('main')).not.toContainText(marker)
  })

  test('FULL-04 实时日志在运行/终态显示全部stdout', async ({ page }) => {
    await loginViaApi(page)
    const task = await createIsolatedTask(page, 'stream')
    const first = testName('first'), second = testName('second')
    try {
      expect((await apiRequest(page, `/api/tasks/${task.id}`, { method: 'PUT', data: { ...task, type: 'command', target: { type: 'node', node_ids: [ (await getNodeIds(page))[0] ] }, command: `echo '${first}'; sleep 6; echo '${second}'`, timeout: 30, enabled: true } })).ok).toBeTruthy()
      await runViaUI(page, task.id)
      let execId: string | undefined
      await expect.poll(async () => {
        const executions = (await apiRequest(page, `/api/executions?task_id=${task.id}&limit=10`)).body as { id: string; status: string }[]
        execId = executions[0]?.id
        return executions[0]?.status
      }).toBe('RUNNING')
      await expect.poll(async () => ((await apiRequest(page, `/api/executions/${execId}/logs`)).body as { chunk: string }[]).map((x) => x.chunk).join('')).toContain(first)
      await page.goto(`/executions/${execId}`)
      await expect(page.locator('pre')).toContainText(first)
      await successExecution(page, task.id, second)
      await expect(page.locator('pre')).toContainText(second, { timeout: 8_000 })
    } finally { await cleanupByIds(page, [`/api/tasks/${task.id}`]) }
  })

  test('FULL-05 Task详情预选调度与on_start字段裁剪', async ({ page }) => {
    await loginViaApi(page)
    const task = await createIsolatedTask(page, 'on-start')
    let id: string | undefined
    try {
      await page.goto(`/tasks/${task.id}`)
      await page.getByRole('link', { name: /新建调度|new schedule/i }).click()
      await expect(page).toHaveURL(new RegExp(`task_id=${task.id}`))
      await expect(page.getByRole('combobox').first()).toBeDisabled()
      await expect(page.getByRole('combobox').first()).toHaveText(task.name)
      await expect(page.getByRole('combobox').nth(1)).toHaveText(/Cron/i)
      await page.getByRole('combobox').nth(1).click()
      await page.getByRole('option', { name: /启动时|on start/i }).click()
      id = await captureCreated(page, '/api/schedules', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      await page.waitForURL(/\/schedules$/)
      const schedule = (await apiRequest(page, `/api/schedules/${id}`)).body as Record<string,unknown>
      expect(schedule.type).toBe('on_start')
      expect(schedule.task_id).toBe(task.id)
      expect(schedule.expression || '').toBe('')
      expect(schedule.interval_sec || 0).toBe(0)
    } finally {
      if (id) await cleanupByIds(page, [`/api/schedules/${id}`])
      await cleanupByIds(page, [`/api/tasks/${task.id}`])
    }
  })
  test('FULL-06 UI文件中继双目标与客户机文件一致、失败重试取消', async ({ page }) => {
    test.setTimeout(120_000)
    await loginViaApi(page)
    const nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string; status: string }[]
    const sourceNode = nodes.find((n) => n.id === (process.env.NODESTEER_E2E_NODE_IDS || '').split(',')[0])!
    const targets = [nodes.find((n) => /agent-2/i.test(n.hostname)), nodes.find((n) => n.id === process.env.NODESTEER_E2E_LABEL_NODE_ID)].filter(Boolean) as typeof nodes
    expect(targets).toHaveLength(2)
    const marker = testName('relay-marker')
    const sourcePath = '/tmp/'+testName('relay-source')
    const destinationPath = '/tmp/'+testName('relay-target')
    const tasks: { id: string; target: { type: string; node_ids: string[] }; command: string }[] = []
    const history: string[] = []
    try {
      const source = await apiRequest(page, '/api/tasks', { method: 'POST', data: {
        name: testName('relay-source-task'), type: 'command', command: `printf '%s\\n' '${marker}' > '${sourcePath}'; cat '${sourcePath}'`, target: { type: 'node', node_ids: [sourceNode.id] }, enabled: true, timeout: 30,
      } })
      expect(source.ok).toBeTruthy()
      const sourceTask = source.body as typeof tasks[number]
      tasks.push(sourceTask)
      await runViaUI(page, sourceTask.id)
      await successExecution(page, sourceTask.id, marker)
      await page.goto('/transfers')
      await page.getByRole('combobox').first().click()
      await page.getByRole('option', { name: sourceNode.hostname, exact: true }).click()
      await page.getByLabel(/^源文件路径$|^source path$/i).fill(sourcePath)
      for (const node of targets) {
        await page.getByRole('combobox').nth(1).click()
        await page.getByRole('option', { name: node.hostname, exact: true }).click()
        await page.getByLabel(/^目标文件路径$|^destination path$/i).fill(destinationPath)
        await page.getByRole('button', { name: /^添加目标$|^add target$/i }).click()
      }
      const transferId = await captureCreated(page, '/api/transfers', () => page.getByRole('button', { name: /^开始传输$|^start transfer$/i }).click())
      history.push(transferId)
      await expect.poll(async () => (await apiRequest(page, `/api/transfers/${transferId}`)).body, { timeout: 30_000 }).toMatchObject({ status: 'SUCCESS' })
      const verify = await apiRequest(page, '/api/tasks', { method: 'POST', data: {
        name: testName('relay-verify-task'), type: 'command', command: `cat '${destinationPath}'`, target: { type: 'node', node_ids: targets.map((n) => n.id) }, enabled: true, timeout: 30,
      } })
      expect(verify.ok).toBeTruthy()
      const verifyTask = verify.body as typeof tasks[number]
      tasks.push(verifyTask)
      await runViaUI(page, verifyTask.id)
      await expect.poll(async () => {
        const executions = (await apiRequest(page, `/api/executions?task_id=${verifyTask.id}&limit=10`)).body as { node_id: string; status: string; stdout: string }[]
        return executions.filter((e) => e.status === 'SUCCESS' && e.stdout.includes(marker)).map((e) => e.node_id).sort()
      }, { timeout: 30_000 }).toEqual(targets.map((n) => n.id).sort())
      await page.goto('/transfers')
      await page.getByRole('combobox').first().click()
      await page.getByRole('option', { name: sourceNode.hostname, exact: true }).click()
      await page.getByLabel(/^源文件路径$|^source path$/i).fill(sourcePath+'-missing')
      await page.getByRole('combobox').nth(1).click()
      await page.getByRole('option', { name: targets[0].hostname, exact: true }).click()
      await page.getByLabel(/^目标文件路径$|^destination path$/i).fill(destinationPath+'-missing')
      await page.getByRole('button', { name: /^添加目标$|^add target$/i }).click()
      const failedId = await captureCreated(page, '/api/transfers', () => page.getByRole('button', { name: /^开始传输$|^start transfer$/i }).click())
      history.push(failedId)
      await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body, { timeout: 20_000 }).toMatchObject({ status: 'FAILED' })
      await page.getByRole('textbox', { name: /搜索|search/i }).fill(failedId)
      const row = page.locator('table tbody tr').first()
      await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
      await page.getByRole('menuitem', { name: /^重试$|^retry$/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body, { timeout: 20_000 }).toMatchObject({ status: 'FAILED' })
      await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
      await page.getByRole('menuitem', { name: /^取消传输$|^cancel( transfer)?$/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body).toMatchObject({ status: 'CANCELED' })
    } finally {
      for (let index=0;index<tasks.length;index++) {
        const task=tasks[index], file=index===0?sourcePath:destinationPath, cleaned=testName('cleaned')
        await apiRequest(page, `/api/tasks/${task.id}`, { method: 'PUT', data: { ...task, command: `rm -f -- '${file}' '${file}-missing'; echo '${cleaned}'` } })
        await apiRequest(page, `/api/tasks/${task.id}/run`, { method: 'POST', data: {} })
        await successExecution(page, task.id, cleaned)
      }
      await cleanupByIds(page, tasks.map((t) => `/api/tasks/${t.id}`).reverse())
      if (process.env.NODESTEER_E2E_RESOURCE_LOG) {
        const { appendFile } = await import('node:fs/promises')
        for (const id of history) await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path:'/api/transfers',id,status:'retained_history',source_path:sourcePath,destination_path:destinationPath })}\n`)
      }
    }
  })

  test('FULL-07 Shell/Bash/Python UI字段、环境/参数、Revision与Clone独立', async ({ page }) => {
    test.setTimeout(120_000)
    await loginViaApi(page)
    const nodeId=(await getNodeIds(page))[0]
    const owned:string[]=[]
    try {
      for(const [interpreter,option] of [['shell','Shell'],['bash','Bash'],['python','Python']]) {
        const marker=testName(interpreter+'-env'), name=testName(interpreter+'-script')
        await page.goto('/scripts/new')
        await page.getByLabel(/^名称$|^name$/i).fill(name)
        await page.getByRole('combobox').first().click()
        await page.getByRole('option',{name:option,exact:true}).click()
        await page.locator('textarea').first().fill(interpreter==='python'?'import os\nprint(os.environ["QA_WEB_VALUE"])':`printf '%s\\n' "$QA_WEB_VALUE"`)
        await page.getByLabel(/^工作目录$|^working directory$/i).fill('/tmp')
        await page.getByLabel(/^运行用户$|^run user$/i).fill('root')
        await page.getByPlaceholder('名称',{exact:true}).fill('qa_text')
        await page.getByPlaceholder('默认值',{exact:true}).fill('qa-default')
        await page.getByRole('button',{name:/^添加参数$/}).click()
        await page.getByPlaceholder('KEY',{exact:true}).fill('QA_WEB_VALUE')
        await page.getByPlaceholder('VALUE',{exact:true}).fill(marker)
        await page.getByRole('button',{name:/^添加$/}).click()
        const scriptId=await captureCreated(page,'/api/scripts',()=>page.getByRole('button',{name:/^保存$|^save$/i}).click())
        owned.push(`/api/scripts/${scriptId}`)
        await page.waitForURL(/\/scripts$/)
        const script=(await apiRequest(page,`/api/scripts/${scriptId}`)).body as Record<string,unknown>
        expect(script).toMatchObject({interpreter,working_dir:'/tmp',run_user:'root',environment:{QA_WEB_VALUE:marker},parameters:[{name:'qa_text',default:'qa-default',type:'string'}]})
        const taskResponse=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName(interpreter+'-task'),type:'script',script_id:scriptId,target:{type:'node',node_ids:[nodeId]},timeout:30,enabled:true}})
        expect(taskResponse.ok).toBeTruthy()
        const taskId=(taskResponse.body as {id:string}).id
        owned.unshift(`/api/tasks/${taskId}`)
        await runViaUI(page,taskId)
        const execution=await successExecution(page,taskId,marker)
        await page.goto(`/executions/${execution.id}`)
        await expect(page.locator('pre')).toContainText(marker)
        await page.goto(`/scripts/${scriptId}`)
        const updated=marker+'-revision2'
        await page.locator('textarea').first().fill(interpreter==='python'?`print("${updated}")`:`echo '${updated}'`)
        await page.getByRole('button',{name:/^保存$|^save$/i}).click()
        await page.waitForURL(/\/scripts$/)
        await page.getByRole('textbox',{name:/搜索脚本|search scripts/i}).fill(name)
        const row=page.locator(`tr:has(a[href="/scripts/${scriptId}"])`)
        await row.getByRole('button',{name:/^操作$|^actions$/i}).click()
        const cloneId=await captureCreated(page,'/api/scripts',()=>page.getByRole('menuitem',{name:/^克隆$|^clone$/i}).click())
        owned.push(`/api/scripts/${cloneId}`)
        const original=(await apiRequest(page,`/api/scripts/${scriptId}`)).body as {revision:number;content:string}
        const clone=(await apiRequest(page,`/api/scripts/${cloneId}`)).body as {revision:number;content:string}
        expect(original.revision).toBe(2)
        expect(clone.revision).toBe(1)
        expect(clone.content).toBe(original.content)
        await page.goto(`/scripts/${scriptId}`)
        await expect(page.locator('main')).toContainText(/版本历史|revision history/i)
        const revisions=(await apiRequest(page,`/api/scripts/${scriptId}/revisions`)).body as {revision:number}[]
        expect(revisions.map(r=>r.revision).sort()).toEqual([1,2])
      }
    } finally {await cleanupByIds(page,owned)}
  })

  for(const viaTask of [false,true]) test(viaTask?'FULL-09 App Task UI创建/运行Deploy与四操作真实Unit/config/SHA/健康':'FULL-08 真实应用UI Deploy/Stop/Start/Restart/Upgrade与Unit/config/SHA', async ({ page }) => {
    test.setTimeout(240_000)
    const { execFileSync } = await import('node:child_process')
    const { readFile, writeFile, appendFile } = await import('node:fs/promises')
    await loginViaApi(page)
    const nodeId=(await getNodeIds(page))[0]
    const nodes=(await apiRequest(page,'/api/nodes')).body as {id:string;hostname:string}[]
    const node=nodes.find(n=>n.id===nodeId)!
    const elf=process.env.NODESTEER_E2E_APP_ELF, inspector=process.env.NODESTEER_E2E_GUEST_INSPECT, base=process.env.NODESTEER_E2E_APP_DIR
    if(!elf||!inspector||!base) throw new Error('Authorized ELF/guest inspector/app directory are required')
    const bytes=await readFile(elf)
    const {createHash}=await import('node:crypto')
    const expectedSHA=createHash('sha256').update(bytes).digest('hex')
    const bytesV2=Buffer.concat([bytes,Buffer.from('\nqa-elf-version-v2\n')])
    const expectedSHAV2=createHash('sha256').update(bytesV2).digest('hex')
    const artifactNameV2=testName('elf-v2')
    const nonce=randomUUID().slice(0,8), unit=`qa-20260930-101945-web-app-${nonce}.service`,directory=`${base}/app-${nonce}`
    const applicationName=testName('service'), owned:string[]=[], observations:unknown[]=[]
    let appId:string|undefined,bodyFailure:unknown,cleanupFailure:unknown
    try {
      await page.goto('/artifacts/new')
      await page.getByLabel(/^名称$|^name$/i).fill(testName('elf'))
      await page.getByLabel(/^版本$|^version$/i).fill('v1')
      await page.locator('input[type=file]').setInputFiles(elf)
      const artifactId=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$|^upload$/i}).click())
      owned.push(`/api/artifacts/${artifactId}`)
      await page.waitForURL(/\/artifacts$/)
      await page.goto('/artifacts/new')
      await page.getByLabel(/^名称$|^name$/i).fill(artifactNameV2)
      await page.getByLabel(/^版本$|^version$/i).fill('v2')
      await page.locator('input[type=file]').setInputFiles({name:artifactNameV2+'.elf',mimeType:'application/octet-stream',buffer:bytesV2})
      const artifactIdV2=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$|^upload$/i}).click())
      owned.push(`/api/artifacts/${artifactIdV2}`)
      await page.waitForURL(/\/artifacts$/)
      await page.goto(`/applications/new?artifact_id=${artifactId}`)
      await page.getByLabel(/^名称$|^name$/i).fill(applicationName)
      await page.getByLabel(/^二进制路径$|^binary path$/i).fill(directory+'/binary')
      await page.getByLabel(/^配置路径$|^config path$/i).fill(directory+'/config.ini')
      await page.getByLabel(/^Unit$/).fill(unit)
      await page.getByLabel(/^(配置内容|config content)/i).fill('qa_version=v1\n')
      for(const argument of ['-c','exec sleep 600']) {
        await page.getByPlaceholder('--port 8080',{exact:true}).fill(argument)
        await page.getByRole('button',{name:/^添加$/}).first().click()
      }
      await page.getByLabel(/^健康检查目标$|^health check target$/i).fill(unit)
      await page.locator('label').filter({hasText:node.hostname}).getByRole('checkbox').check()
      appId=await captureCreated(page,'/api/applications',()=>page.getByRole('button',{name:/^保存$|^save$/i}).click())
      owned.unshift(`/api/applications/${appId}`)
      await page.waitForURL(new RegExp(`/applications/${appId}$`))
      let completed=0
      for(const operation of ['deploy','stop','start','restart','upgrade']) {
        if(operation==='upgrade') {
          await page.getByRole('combobox').first().click()
          await page.getByRole('option',{name:new RegExp(artifactNameV2)}).click()
          await page.getByLabel(/^版本$|^version$/i).fill('v2')
          await page.getByLabel(/^(配置内容|config content)/i).fill('qa_version=v2\n')
          await page.getByRole('button',{name:/^保存$|^save$/i}).click()
          await page.waitForLoadState('networkidle')
        }
        const before=(await apiRequest(page,`/api/applications/${appId}/executions`)).body as {id:string}[]|null
        const existing=new Set((before||[]).map(e=>e.id))
        if(viaTask) {
          await page.goto('/tasks/new')
          await page.waitForLoadState('networkidle')
          await page.getByLabel(/^名称$/).fill(testName('app-task-'+operation))
          await page.getByRole('combobox').first().click()
          await page.getByRole('option',{name:operation==='deploy'?'托管应用部署':'托管应用操作',exact:true}).click()
          await page.locator('label').filter({hasText:/^托管应用/}).getByRole('combobox').click()
          await page.getByRole('option',{name:applicationName,exact:true}).click()
          await page.locator('label').filter({hasText:/^操作/}).getByRole('combobox').click()
          await page.getByRole('option',{name:({deploy:'部署',stop:'停止',start:'启动',restart:'重启',upgrade:'升级'} as Record<string,string>)[operation],exact:true}).click()
          await page.locator('label').filter({hasText:node.hostname}).getByRole('checkbox').check()
          const taskId=await captureCreated(page,'/api/tasks',()=>page.getByRole('button',{name:/^保存$/}).click());owned.unshift('/api/tasks/'+taskId)
          await runViaUI(page,taskId)
          await expect.poll(async()=>((await apiRequest(page,'/api/executions?task_id='+taskId+'&limit=10')).body as {status:string}[])[0]?.status,{timeout:45_000}).toBe('SUCCESS')
          await page.goto('/applications/'+appId);await page.waitForLoadState('networkidle')
        } else {
          await page.getByRole('combobox').last().click()
          await page.getByRole('option',{name:operation,exact:true}).click()
          const response=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/applications/${appId}/deploy`&&r.request().method()==='POST')
          await page.getByRole('button',{name:/^部署$|^deploy$/i}).click()
          expect((await response).ok()).toBeTruthy()
        }
        let execution:{id:string;status:string}|undefined
        await expect.poll(async()=>{
          const executions=((await apiRequest(page,`/api/applications/${appId}/executions`)).body||[]) as typeof execution[]
          execution=executions.find(e=>e&&!existing.has(e.id))
          return execution?.status
        },{timeout:45_000}).toBe('SUCCESS')
        const state=((await apiRequest(page,`/api/applications/${appId}/state`)).body as {node_id:string;operation:string;health:string;version:string}[]).find(s=>s.node_id===nodeId)!
        expect(state.operation).toBe(operation)
        if(operation!=='stop')expect(state.health).toBe('healthy')
        expect(state.version).toBe(operation==='upgrade'?'v2':'v1')
        const guest=JSON.parse(execFileSync('python3',[inspector,unit,directory,'inspect'],{encoding:'utf8',timeout:30_000}))
        expect(guest.properties.ActiveState).toBe(operation==='stop'?'inactive':'active')
        expect(guest.files.binary.sha256).toBe(operation==='upgrade'?expectedSHAV2:expectedSHA)
        expect(guest.files['config.ini'].content).toBe(operation==='upgrade'?'qa_version=v2\n':'qa_version=v1\n')
        expect(guest.unit_content).toContain(directory+'/binary')
        const healthCard=page.locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]').filter({hasText:/^健康检查$/})})
        const liveFeedback=await healthCard.innerText()
        const historyCard=page.locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]').filter({hasText:/^执行历史$/})})
        completed++
        const visibleExecution=await historyCard.getByText('SUCCESS',{exact:true}).count()
        const badges=await healthCard.locator('[data-slot=badge]').allTextContents()
        observations.push({operation,via_task:viaTask,execution_id:execution!.id,state,guest,live_feedback:liveFeedback,visible_execution:visibleExecution,badges})
        if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR+`/managed-app-${nonce}.json`,JSON.stringify({app_id:appId,unit,directory,via_task:viaTask,observations},null,2))
        expect.soft(liveFeedback,operation+' live health feedback').toContain(operation)
        expect.soft(visibleExecution,operation+' live execution feedback').toBeGreaterThanOrEqual(completed)
        if(operation!=='stop')expect.soft(badges,operation+' live health badge').toContain('健康')
        await page.reload()
        await expect(healthCard).toContainText(operation)
        await expect.poll(()=>historyCard.getByText('SUCCESS',{exact:true}).count()).toBeGreaterThanOrEqual(completed)
      }
      await page.getByRole('combobox').last().click()
      await page.getByRole('option',{name:'stop',exact:true}).click()
      await page.getByRole('button',{name:/^部署$|^deploy$/i}).click()
      await expect.poll(()=>JSON.parse(execFileSync('python3',[inspector,unit,directory,'inspect'],{encoding:'utf8',timeout:30_000})).properties.ActiveState).toBe('inactive')
      if(viaTask) {
        const tasks=owned.filter(path=>path.startsWith('/api/tasks/'))
        await cleanupByIds(page,tasks)
        for(const path of tasks)owned.splice(owned.indexOf(path),1)
      }
      await page.goto('/applications')
      await page.getByRole('textbox',{name:/搜索.*应用|search.*application/i}).fill(applicationName)
      await deleteViaUI(page,`/api/applications/${appId}`,page.locator(`tr:has(a[href="/applications/${appId}"])`))
    } catch(error) {
      bodyFailure=error
      if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR+`/managed-app-${nonce}-error.json`,JSON.stringify({error:String(error),url:page.url(),app_id:appId,owned,operations_done:observations.length,token_present:await page.evaluate(()=>!!localStorage.getItem('nodesteer_token'))},null,2))
    } finally {
      try {
        await loginViaApi(page)
        await (async()=>{if(appId) {
          const refs=((await apiRequest(page,'/api/tasks')).body as {id:string;name:string;application_id?:string;app_operation?:string}[]).filter(t=>t.application_id===appId)
          for(const task of refs) {
            if(!task.name.startsWith('qa-20260930-101945-web-'))throw new Error('Unexpected external Task reference to owned Application')
            const path='/api/tasks/'+task.id
            if(owned.includes(path))await cleanupByIds(page,[path])
            else {
              const result=await apiRequest(page,path,{method:'DELETE'});if(![200,204,404].includes(result.status))throw new Error('Referenced Task cleanup HTTP'+result.status)
              if(process.env.NODESTEER_E2E_RESOURCE_LOG)await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG,JSON.stringify({path,id:task.id,name:task.name,status:'deleted',ownership:'exact owned application_id and QA Task name',application_id:appId})+'\n')
            }
            if(owned.includes(path))owned.splice(owned.indexOf(path),1)
          }
        }})()
        await cleanupByIds(page,owned)
      } catch(error) {
        cleanupFailure=error
        if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR+`/managed-app-${nonce}-definition-cleanup-error.json`,JSON.stringify({error:String(error),owned},null,2))
      } finally {
      const cleanup=JSON.parse(execFileSync('python3',[inspector,unit,directory,'cleanup'],{encoding:'utf8',timeout:30_000}))
      expect(cleanup.unit_removed).toBeTruthy()
      expect(cleanup.directory_removed).toBeTruthy()
      if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR+`/managed-app-${nonce}-cleanup.json`,JSON.stringify(cleanup,null,2))
      }
    }
    if(bodyFailure)throw bodyFailure
    if(cleanupFailure)throw cleanupFailure
  })

  test('ROLE-01 Operator与Viewer只能读取自己的Audit记录', async ({ page, browser }) => {
    await loginViaApi(page)
    const account = await qaRoleAccount()
    let operator: Awaited<ReturnType<typeof openQaRoleSession>> | undefined
    let viewer: Awaited<ReturnType<typeof openQaRoleSession>> | undefined
    try {
      const users = (await apiRequest(page, '/api/users')).body as { id: string; role: string }[]
      expect(users.find(user => user.id === QA_ROLE_USER_ID)).toMatchObject({ role: 'viewer' })
      await setQaRole(page, 'operator')
      operator = await openQaRoleSession(browser, new URL(page.url()).origin, account, 'operator')
      const operatorAudit = await verifyReadonlyAudit(operator.page, QA_ROLE_USER_ID)
      await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + '/role-audit-operator.json', JSON.stringify(operatorAudit, null, 2))
      const auditId = operatorAudit.audit_id
      const logoutOperator = await closeQaRoleSession(operator)
      operator = undefined
      expect([200, 204]).toContain(logoutOperator)
      await setQaRole(page, 'viewer')
      viewer = await openQaRoleSession(browser, new URL(page.url()).origin, account, 'viewer')
      const viewerAudit = await verifyReadonlyAudit(viewer.page, QA_ROLE_USER_ID, auditId)
      await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + '/role-audit-operator-viewer.json', JSON.stringify({ qa_user_id: QA_ROLE_USER_ID, audit_id: auditId, operator: operatorAudit, viewer: viewerAudit }, null, 2))
      const logoutViewer = await closeQaRoleSession(viewer)
      viewer = undefined
      expect([200, 204]).toContain(logoutViewer)
    } finally {
      try {
        if (operator) await closeQaRoleSession(operator)
        if (viewer) await closeQaRoleSession(viewer)
      } finally {
        await loginViaApi(page)
        await setQaRole(page, 'viewer')
      }
    }
  })

  test('ROLE-02 Operator真实部署自有应用但定义只读；Viewer部署与定义写入被拒', async ({ page, browser }) => {
    test.setTimeout(180_000)
    await loginViaApi(page)
    const account = await qaRoleAccount()
    const elf = process.env.NODESTEER_E2E_APP_ELF
    const inspector = process.env.NODESTEER_E2E_GUEST_INSPECT
    const base = process.env.NODESTEER_E2E_APP_DIR
    if (!elf || !inspector || !base) throw new Error('Authorized Node1 ELF and guest inspector are required')
    const bytes = await readFile(elf)
    const expectedSHA = createHash('sha256').update(bytes).digest('hex')
    const nonce = randomUUID().slice(0, 8)
    const directory = `${base}/role-${nonce}`
    const unit = `qa-20260930-101945-web-role-${nonce}.service`
    const appName = testName('role-app')
    const owned: string[] = []
    let artifactId = ''
    let appId = ''
    let nodeId = ''
    let operator: Awaited<ReturnType<typeof openQaRoleSession>> | undefined
    let viewer: Awaited<ReturnType<typeof openQaRoleSession>> | undefined
    try {
      const users = (await apiRequest(page, '/api/users')).body as { id: string; role: string }[]
      expect(users.find(user => user.id === QA_ROLE_USER_ID)).toMatchObject({ role: 'viewer' })
      nodeId = (await getNodeIds(page))[0]
      const nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string }[]
      const node = nodes.find(item => item.id === nodeId)!
      await page.goto('/artifacts/new')
      await page.getByLabel(/^名称$/).fill(testName('role-artifact'))
      await page.getByLabel(/^版本$/).fill('v1')
      await page.locator('input[type=file]').setInputFiles(elf)
      artifactId = await captureCreated(page, '/api/artifacts', () => page.getByRole('button', { name: /^上传$/ }).click())
      owned.push('/api/artifacts/' + artifactId)
      await page.waitForURL(/\/artifacts$/)
      await page.goto(`/applications/new?artifact_id=${artifactId}`)
      await page.getByLabel(/^名称$/).fill(appName)
      await page.getByLabel(/^二进制路径$/).fill(directory + '/binary')
      await page.getByLabel(/^配置路径$/).fill(directory + '/config.ini')
      await page.getByLabel(/^Unit$/).fill(unit)
      await page.getByLabel(/^配置内容$/).fill('qa_role=operator\n')
      await page.getByLabel(/^健康检查目标$/).fill(unit)
      for (const argument of ['-c', 'exec sleep 600']) {
        await page.getByPlaceholder('--port 8080', { exact: true }).fill(argument)
        await page.getByRole('button', { name: /^添加$/ }).first().click()
      }
      await page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox').check()
      appId = await captureCreated(page, '/api/applications', () => page.getByRole('button', { name: /^保存$/ }).click())
      owned.unshift('/api/applications/' + appId)
      await page.waitForURL(new RegExp(`/applications/${appId}$`))
      const applicationBefore = (await apiRequest(page, '/api/applications/' + appId)).body as Record<string, unknown>
      const artifactBefore = (await apiRequest(page, '/api/artifacts/' + artifactId)).body
      const executionsBefore = (await apiRequest(page, `/api/applications/${appId}/executions`)).body as { id: string }[]

      await setQaRole(page, 'operator')
      operator = await openQaRoleSession(browser, new URL(page.url()).origin, account, 'operator')
      await operator.page.goto('/applications')
      const operatorRow = operator.page.locator(`tr:has(a[href="/applications/${appId}"])`)
      await expect(operatorRow).toBeVisible()
      await expect(operator.page.locator('a[href="/applications/new"]')).toHaveCount(0)
      await expect(operatorRow.getByRole('button', { name: /^操作$/ })).toHaveCount(0)
      await operator.page.goto('/applications/' + appId)
      await expect(operator.page.getByRole('button', { name: /^保存$/ })).toHaveCount(0)
      const operatorNodeCheck = operator.page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox')
      await expect(operatorNodeCheck).toBeChecked()
      await expect(operatorNodeCheck).toBeDisabled()
      const deployButton = operator.page.getByRole('button', { name: /^部署$/ })
      await expect(deployButton).toBeEnabled()

      const operatorPost = await apiRequest(operator.page, '/api/applications', { method: 'POST', data: { name: testName('operator-denied-app') } })
      const operatorPut = await apiRequest(operator.page, '/api/applications/' + appId, { method: 'PUT', data: { ...applicationBefore, description: 'operator must not change definition' } })
      const operatorDelete = await apiRequest(operator.page, '/api/applications/' + appId, { method: 'DELETE' })
      expect([operatorPost.status, operatorPut.status, operatorDelete.status]).toEqual([403, 403, 403])
      expect((await apiRequest(operator.page, '/api/applications/' + appId)).body).toEqual(applicationBefore)
      expect((await apiRequest(operator.page, '/api/artifacts/' + artifactId)).body).toEqual(artifactBefore)

      const deployResponsePromise = operator.page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/applications/${appId}/deploy`)
      await deployButton.click()
      const deployResponse = await deployResponsePromise
      const deployBody = await deployResponse.json() as { id: string }[]
      expect(deployResponse.status()).toBe(200)
      let deployment: { id: string; node_id: string; status: string } | undefined
      await expect.poll(async () => {
        const current = (await apiRequest(operator!.page, `/api/applications/${appId}/executions`)).body as typeof deployment[]
        deployment = current.find(item => item && !executionsBefore.some(old => old.id === item.id) && item.status === 'SUCCESS')
        return deployment?.status
      }, { timeout: 60_000 }).toBe('SUCCESS')
      const state = (await apiRequest(operator.page, `/api/applications/${appId}/state`)).body as { node_id: string; operation: string; health: string; version: string }[]
      const nodeState = state.find(item => item.node_id === nodeId)!
      expect(nodeState).toMatchObject({ operation: 'deploy', health: 'healthy', version: 'v1' })
      const { execFileSync } = await import('node:child_process')
      const guest = JSON.parse(execFileSync('python3', [inspector, unit, directory, 'inspect'], { encoding: 'utf8', timeout: 30_000 }))
      expect(guest.properties.ActiveState).toBe('active')
      expect(guest.files.binary.sha256).toBe(expectedSHA)
      expect(guest.files['config.ini'].content).toBe('qa_role=operator\n')
      expect(guest.unit_content).toContain(directory + '/binary')
      const healthCard = operator.page.locator('[data-slot=card]').filter({ has: operator.page.locator('[data-slot=card-title]').filter({ hasText: /^健康检查$/ }) })
      const historyCard = operator.page.locator('[data-slot=card]').filter({ has: operator.page.locator('[data-slot=card-title]').filter({ hasText: /^执行历史$/ }) })
      const operatorAppFeedback = { health: await healthCard.innerText(), successRows: await historyCard.getByText('SUCCESS', { exact: true }).count() }
      expect.soft(operatorAppFeedback.health).toContain('deploy')
      expect.soft(operatorAppFeedback.successRows).toBeGreaterThan(0)

      const auditRows = (await apiRequest(operator.page, `/api/audit?user_id=${QA_ROLE_USER_ID}&limit=100`)).body as { id: string; user_id: string; username: string; resource: string; resource_id: string; action: string }[]
      const deployAudit = auditRows.find(item => item.resource === 'application' && item.resource_id === appId && item.action === 'deploy' && item.user_id === QA_ROLE_USER_ID)
      expect(deployAudit).toBeTruthy()
      const operatorAudit = await verifyReadonlyAudit(operator.page, QA_ROLE_USER_ID, deployAudit!.id)
      const operatorExecutions = (await apiRequest(operator.page, `/api/applications/${appId}/executions`)).body

      const logoutOperator = await closeQaRoleSession(operator)
      operator = undefined
      expect([200, 204]).toContain(logoutOperator)
      await setQaRole(page, 'viewer')
      viewer = await openQaRoleSession(browser, new URL(page.url()).origin, account, 'viewer')
      await viewer.page.goto('/applications')
      const viewerRow = viewer.page.locator(`tr:has(a[href="/applications/${appId}"])`)
      await expect(viewerRow).toBeVisible()
      await expect(viewer.page.locator('a[href="/applications/new"]')).toHaveCount(0)
      await expect(viewerRow.getByRole('button', { name: /^操作$/ })).toHaveCount(0)
      await viewer.page.goto('/applications/' + appId)
      await expect(viewer.page.getByRole('button', { name: /^部署$/ })).toHaveCount(0)
      await expect(viewer.page.getByRole('button', { name: /^保存$/ })).toHaveCount(0)
      await expect(viewer.page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox')).toBeDisabled()

      const viewerPost = await apiRequest(viewer.page, '/api/applications', { method: 'POST', data: { name: testName('viewer-denied-app') } })
      const viewerPut = await apiRequest(viewer.page, '/api/applications/' + appId, { method: 'PUT', data: { ...applicationBefore, description: 'viewer must not change definition' } })
      const viewerDelete = await apiRequest(viewer.page, '/api/applications/' + appId, { method: 'DELETE' })
      const viewerDeploy = await apiRequest(viewer.page, `/api/applications/${appId}/deploy`, { method: 'POST', data: { node_ids: [nodeId], operation: 'restart' } })
      expect([viewerPost.status, viewerPut.status, viewerDelete.status, viewerDeploy.status]).toEqual([403, 403, 403, 403])
      expect((await apiRequest(viewer.page, '/api/applications/' + appId)).body).toEqual(applicationBefore)
      expect((await apiRequest(viewer.page, `/api/applications/${appId}/state`)).body).toEqual(state)
      const viewerExecutions = (await apiRequest(viewer.page, `/api/applications/${appId}/executions`)).body
      expect(viewerExecutions).toEqual(operatorExecutions)
      const viewerAudit = await verifyReadonlyAudit(viewer.page, QA_ROLE_USER_ID, deployAudit!.id)
      await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + '/role-application-permissions.json', JSON.stringify({ qa_user_id: QA_ROLE_USER_ID, app_id: appId, artifact_id: artifactId, node_id: nodeId, operator: { list_new_hidden: true, list_actions_hidden: true, save_hidden: true, assigned_node_checkbox_disabled: true, app_create_http: operatorPost.status, app_put_http: operatorPut.status, app_delete_http: operatorDelete.status, app_deploy_http: deployResponse.status(), app_deploy_response_ids: deployBody.map(item => item.id), execution: deployment, state: nodeState, guest, ui_feedback: operatorAppFeedback, audit: operatorAudit }, viewer: { list_new_hidden: true, list_actions_hidden: true, deploy_button_hidden: true, save_hidden: true, app_create_http: viewerPost.status, app_put_http: viewerPut.status, app_delete_http: viewerDelete.status, app_deploy_http: viewerDeploy.status, audit: viewerAudit }, definition_unchanged: true, viewer_deploy_created_no_execution: true }, null, 2))
      const logoutViewer = await closeQaRoleSession(viewer)
      viewer = undefined
      expect([200, 204]).toContain(logoutViewer)
    } finally {
      try {
        if (operator) await closeQaRoleSession(operator)
        if (viewer) await closeQaRoleSession(viewer)
      } finally {
        try {
          await loginViaApi(page)
          await setQaRole(page, 'viewer')
        } finally {
          const { execFileSync } = await import('node:child_process')
          try {
            const cleanup = JSON.parse(execFileSync('python3', [inspector, unit, directory, 'cleanup'], { encoding: 'utf8', timeout: 30_000 }))
            expect(cleanup.unit_removed).toBeTruthy()
            expect(cleanup.directory_removed).toBeTruthy()
          await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + `/role-application-unit-cleanup-${nonce}.json`, JSON.stringify({ app_id: appId || null, artifact_id: artifactId || null, unit, directory, cleanup }, null, 2))
          } finally {
            if (owned.length) await cleanupByIds(page, owned)
          }
        }
      }
    }
  })

})
