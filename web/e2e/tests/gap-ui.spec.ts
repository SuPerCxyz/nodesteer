import { test, expect, type Page } from 'playwright/test'
import { writeFile, appendFile, readFile, rm } from 'node:fs/promises'
import { createHash, randomBytes } from 'node:crypto'
import { apiRequest, captureCreated, cleanupByIds, createIsolatedTask, getNodeIds, login, loginViaApi, testName } from './fixtures'

async function evidence(name: string, data: unknown) {
  await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + '/' + name + '.json', JSON.stringify(data, null, 2))
}

async function select(page: Page, label: string, option: string) {
  await page.locator('label').filter({ hasText: new RegExp('^' + label) }).getByRole('combobox').click()
  await page.getByRole('option', { name: option, exact: true }).click()
}

async function upload(page: Page, name: string, buffer: Buffer) {
  await page.goto('/artifacts/new')
  await page.getByLabel(/^名称$/).fill(name)
  await page.getByLabel(/^版本$/).fill('v1')
  await page.locator('input[type=file]').setInputFiles({ name: name + '.txt', mimeType: 'application/octet-stream', buffer })
  const id = await captureCreated(page, '/api/artifacts', () => page.getByRole('button', { name: /^上传$/ }).click())
  await page.waitForURL(/\/artifacts$/)
  return id
}

async function runTask(page: Page, id: string, marker: string) {
  const before = (await apiRequest(page, `/api/executions?task_id=${id}&limit=100`)).body as { id: string }[]
  await page.goto(`/tasks/${id}/run`)
  await page.getByRole('button', { name: /^立即运行$/ }).click()
  const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${id}/run`)
  await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
  expect((await response).ok()).toBeTruthy()
  await page.waitForURL(/\/executions$/)
  let execution: { id: string; stdout: string; status: string } | undefined
  await expect.poll(async () => {
    const executions = (await apiRequest(page, `/api/executions?task_id=${id}&limit=20`)).body as typeof execution[]
    execution = executions.find(item => item?.status === 'SUCCESS' && item.stdout.includes(marker) && !before.some(old => old.id === item.id))
    return execution?.stdout || ''
  }, { timeout: 30_000 }).toContain(marker)
  return { execution_id: execution!.id, stdout: execution!.stdout, status: execution!.status }
}

test('GAP-01 Task四类型真实保存及隐藏字段往返', async ({ page }) => {
  test.setTimeout(150_000)
  await loginViaApi(page)
  const owned: string[] = [], observations: unknown[] = []
  try {
    const scriptName = testName('roundtrip-script')
    const script = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: scriptName, interpreter: 'shell', content: 'echo roundtrip-script', enabled: true } })
    expect(script.ok).toBeTruthy()
    const scriptId = (script.body as { id: string }).id
    owned.push('/api/scripts/' + scriptId)
    const artifactId = await upload(page, testName('roundtrip-artifact'), Buffer.from('definition only'))
    owned.push('/api/artifacts/' + artifactId)
    const appName = testName('roundtrip-app')
    const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: appName, version: 'v1', artifact_id: artifactId, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } })
    expect(app.ok).toBeTruthy()
    const appId = (app.body as { id: string }).id
    owned.unshift('/api/applications/' + appId)
    const task = await createIsolatedTask(page, 'type-roundtrip')
    owned.unshift('/api/tasks/' + task.id)
    for (const [type, title] of [['script', '脚本'], ['app_deploy', '托管应用部署'], ['app_operation', '托管应用操作'], ['command', '命令']]) {
      await page.goto(`/tasks/${task.id}/edit`)
      await select(page, '类型', title)
      if (type === 'script') await select(page, '脚本', scriptName)
      if (type.startsWith('app_')) await select(page, '托管应用', appName)
      if (type === 'app_deploy') await select(page, '操作', '部署')
      if (type === 'app_operation') await select(page, '操作', '停止')
      if (type === 'command') await page.getByLabel('命令', { exact: true }).fill('echo roundtrip-final')
      const response = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname === `/api/tasks/${task.id}`)
      await page.getByRole('button', { name: /^保存$/ }).click()
      const actualResponse = await response, payload = actualResponse.request().postDataJSON()
      expect(actualResponse.ok()).toBeTruthy()
      await page.waitForURL(/\/tasks$/)
      const saved = (await apiRequest(page, `/api/tasks/${task.id}`)).body as Record<string, unknown>
      expect(saved.type).toBe(type)
      if (type === 'script') expect(payload.script_id).toBe(scriptId)
      if (type.startsWith('app_')) expect(payload.application_id).toBe(appId)
      expect(payload.command || '').toBe(type === 'command' ? 'echo roundtrip-final' : '')
      expect(payload.app_operation || '').toBe(type === 'app_deploy' ? 'deploy' : type === 'app_operation' ? 'stop' : '')
      await page.goto(`/tasks/${task.id}/edit`)
      await expect(page.locator('label').filter({ hasText: /^类型/ }).getByRole('combobox')).toContainText(title)
      const execution = ['script', 'command'].includes(type) ? await runTask(page, task.id, type === 'script' ? 'roundtrip-script' : 'roundtrip-final') : null
      observations.push({ type, payload, saved, execution })
      await evidence('task-four-types-roundtrip-progress', { task_id: task.id, observations, app_assignment: 'none; definition only' })
    }
    await evidence('task-four-types-roundtrip', { task_id: task.id, script_id: scriptId, app_id: appId, observations, app_actual_runs: 'existing FULL09 evidence covers real five app operations; this suite covers same Task field roundtrip only' })
  } finally { await cleanupByIds(page, owned) }
})

test('GAP-02 Task真实PUT pending重复提交与secret摘要', async ({ page }) => {
  await loginViaApi(page)
  const task = await createIsolatedTask(page, 'pending-secret'), secret = randomBytes(24).toString('hex')
  await appendFile(process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl', JSON.stringify({ token: secret, purpose: 'synthetic Task secret redaction' }) + '\n', { mode: 0o600 })
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let writes = 0
  try {
    await page.goto(`/tasks/${task.id}/edit`)
    await page.getByPlaceholder('参数名称', { exact: true }).fill('qa_secret')
    await page.getByRole('combobox').filter({ hasText: /^string$/ }).click()
    await page.getByRole('option', { name: 'secret', exact: true }).click()
    await page.getByPlaceholder('默认值', { exact: true }).fill(secret)
    await page.getByRole('button', { name: /^添加参数$/ }).click()
    const exposed = (await page.locator('main').innerText()).includes(secret)
    const row = page.locator('div.flex.items-center').filter({ hasText: 'qa_secret' }).filter({ has: page.getByRole('button', { name: /^移除$/ }) })
    await row.getByRole('button', { name: /^移除$/ }).click()
    await expect(page.locator('main')).not.toContainText('qa_secret')
    await page.route(`**/api/tasks/${task.id}`, async route => {
      if (route.request().method() === 'PUT') { writes++; await gate }
      await route.continue()
    })
    const save = page.getByRole('button', { name: /^保存$/ })
    await save.click()
    await expect.poll(() => writes).toBe(1)
    const disabled = await save.isDisabled()
    if (!disabled) { await save.click(); await expect.poll(() => writes).toBe(2) }
    release()
    await page.waitForURL(/\/tasks$/)
    await page.waitForLoadState('networkidle')
    const saved = (await apiRequest(page, `/api/tasks/${task.id}`)).body as { revision: number; parameters?: unknown[] }
    expect(saved.parameters || []).toEqual([])
    await evidence('task-pending-secret', { task_id: task.id, synthetic_secret_publicly_saved: false, secret_summary_exposed: exposed, parameter_remove_persisted: true, delay_kind: 'REAL_UI_DELAY; actual PUT requests continued to real Hub unchanged', pending_save_disabled: disabled, actual_put_requests: writes, final_revision: saved.revision })
    expect.soft(exposed, 'Task secret default must be masked in summary').toBe(false)
    expect.soft(disabled, 'Task Save must be disabled while real request is pending').toBe(true)
    expect.soft(writes, 'Repeated pending clicks must produce only one PUT').toBe(1)
  } finally { release?.(); await page.unrouteAll({ behavior: 'wait' }); await cleanupByIds(page, ['/api/tasks/' + task.id]) }
})

test('GAP-03 Artifact真实下载headers重复空文件与引用删除', async ({ page }) => {
  test.setTimeout(130_000)
  const token = await loginViaApi(page), owned: string[] = [], results: unknown[] = []
  try {
    const name = testName('artifact-boundary'), buffer = Buffer.from(name + '\n')
    const id = await upload(page, name, buffer)
    owned.push('/api/artifacts/' + id)
    const saved = (await apiRequest(page, '/api/artifacts/' + id)).body as { size: number; sha256: string }
    const download = await page.request.get(`/api/artifacts/${id}/download`, { headers: { Authorization: `Bearer ${token}` } })
    expect(download.ok()).toBeTruthy(); expect(await download.body()).toEqual(buffer)
    expect(saved.size).toBe(buffer.length); expect(saved.sha256).toBe(createHash('sha256').update(buffer).digest('hex'))
    expect(download.headers()['content-length']).toBe(String(buffer.length))
    expect(download.headers()['x-artifact-sha256']).toBe(saved.sha256)
    results.push({ download_status: download.status(), size: saved.size, sha256: saved.sha256, headers: { content_length: download.headers()['content-length'], x_artifact_sha256: download.headers()['x-artifact-sha256'] } })
    // 同名同版本的实际行为由当前契约决定，明确记录，不凭唯一性假设判错。
    const duplicateId = await upload(page, name, buffer)
    owned.push('/api/artifacts/' + duplicateId)
    results.push({ duplicate_name_version: 'accepted distinct object', duplicate_id: duplicateId })
    const emptyId = await upload(page, testName('empty-artifact'), Buffer.alloc(0))
    owned.push('/api/artifacts/' + emptyId)
    const empty = (await apiRequest(page, '/api/artifacts/' + emptyId)).body as { size: number; sha256: string }
    expect(empty.size).toBe(0); expect(empty.sha256).toBe(createHash('sha256').update(Buffer.alloc(0)).digest('hex'))
    results.push({ empty_file: 'accepted', size: empty.size, sha256: empty.sha256 })
    const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('referenced-app'), version: 'v1', artifact_id: id, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } })
    expect(app.ok).toBeTruthy(); const appId = (app.body as { id: string }).id; owned.unshift('/api/applications/' + appId)
    await page.goto('/artifacts'); await page.getByRole('textbox', { name: /搜索/ }).fill(name)
    const row = page.locator('tr').filter({ has: page.locator(`a[href="/applications/${appId}"]`) })
    await expect(row).toBeVisible()
    let deletes = 0; page.on('request', r => { if (r.method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id) deletes++ })
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
    await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
    expect(deletes).toBe(0); expect((await apiRequest(page, '/api/artifacts/' + id)).ok).toBeTruthy()
    // 菜单取消锁页已有独立缺陷；硬刷新继续本项引用删除，不重复覆盖其根因。
    await page.reload(); await page.getByRole('textbox', { name: /搜索/ }).fill(name)
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
    const response = page.waitForResponse(r => r.request().method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id)
    await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click()
    const actual = await response, body = await actual.json()
    await evidence('artifact-boundaries-reference-delete', { artifact_id: id, app_id: appId, results, cancel_delete_requests: 0, confirm_delete_requests: deletes, referenced_delete_http: actual.status(), referenced_delete_body: body, retained_artifact_http: (await apiRequest(page, '/api/artifacts/' + id)).status })
    expect(actual.status()).toBe(409); expect(deletes).toBe(1); await expect(row).toBeVisible()
    expect((await apiRequest(page, '/api/applications/' + appId)).ok).toBeTruthy()
  } finally { await cleanupByIds(page, owned) }
})

test('GAP-04 重叠多Group去选与直接Label动态实际执行', async ({ page }) => {
  test.setTimeout(180_000)
  await loginViaApi(page)
  const own: string[] = [], nodeId = (await getNodeIds(page))[0], labelNode = process.env.NODESTEER_E2E_LABEL_NODE_ID!, key = 'qa_web_' + randomBytes(4).toString('hex'), value = testName('direct-label')
  const checks: unknown[] = []
  let labelCreated = false
  try {
    const groups: { id: string; name: string }[] = []
    for (let i = 0; i < 2; i++) {
      const name = testName('overlap-group'), response = await apiRequest(page, '/api/groups', { method: 'POST', data: { name, type: 'static', members: [nodeId] } })
      expect(response.ok).toBeTruthy(); const id = (response.body as { id: string }).id
      groups.push({ id, name }); own.push('/api/groups/' + id)
    }
    const task = await createIsolatedTask(page, 'multi-target'); own.unshift('/api/tasks/' + task.id)
    await page.goto(`/tasks/${task.id}/edit`); await select(page, '目标类型', '分组')
    for (const group of groups) await page.locator('label').filter({ hasText: group.name }).getByRole('checkbox').check()
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/)
    const saved = (await apiRequest(page, `/api/tasks/${task.id}`)).body as { target: { group_ids: string[] } }
    expect(saved.target.group_ids.sort()).toEqual(groups.map(g => g.id).sort())
    const execution = await runTask(page, task.id, 'isolated-e2e')
    const first = (await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as { id: string; node_id: string }[]
    expect(first).toHaveLength(1); expect(first[0].node_id).toBe(nodeId)
    await page.goto(`/agents/${nodeId}`); const nodeTaskLink = page.locator(`a[href="/tasks/${task.id}"]`); await expect(nodeTaskLink.first()).toBeVisible()
    checks.push({ overlapping_group_count: 2, target: saved.target, executions: first, execution, node_detail_task_link: true })
    await page.goto(`/tasks/${task.id}/edit`)
    await page.locator('label').filter({ hasText: groups[1].name }).getByRole('checkbox').uncheck()
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/)
    expect(((await apiRequest(page, `/api/tasks/${task.id}`)).body as { target: { group_ids: string[] } }).target.group_ids).toEqual([groups[0].id])
    checks.push({ deselect_group_persisted: true })
    await page.goto(`/agents/${labelNode}`); await page.getByPlaceholder('key', { exact: true }).fill(key); await page.getByPlaceholder('value', { exact: true }).fill(value)
    await page.getByRole('button', { name: /^添加标签$/ }).click(); labelCreated = true
    await expect.poll(async () => ((await apiRequest(page, `/api/nodes/${labelNode}`)).body as { labels: Record<string, string> }).labels[key]).toBe(value)
    await page.goto(`/tasks/${task.id}/edit`); await select(page, '目标类型', '标签')
    await page.getByLabel('标签键', { exact: true }).fill(key); await page.getByLabel('标签值', { exact: true }).fill(value)
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/)
    const direct = await runTask(page, task.id, 'isolated-e2e')
    const second = (await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as { id: string; node_id: string }[]
    expect(second).toHaveLength(2); expect(second.find(e => e.id !== first[0].id)?.node_id).toBe(labelNode)
    await page.goto(`/agents/${labelNode}`); await expect(page.locator(`a[href="/tasks/${task.id}"]`).first()).toBeVisible()
    await page.getByPlaceholder('key', { exact: true }).fill(key); await page.getByPlaceholder('value', { exact: true }).fill(value + '-unmatched'); await page.getByRole('button', { name: /^添加标签$/ }).click()
    await expect.poll(async () => ((await apiRequest(page, `/api/nodes/${labelNode}`)).body as { labels: Record<string, string> }).labels[key]).toBe(value + '-unmatched')
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click()
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`)
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
    const empty = await response; expect(empty.status()).toBeGreaterThanOrEqual(400)
    expect(((await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as unknown[])).toHaveLength(2)
    checks.push({ direct_label_execution: direct, actual_target_node: labelNode, dynamic_unmatched_run_http: empty.status(), no_extra_execution: true })
    await evidence('multi-group-direct-label', { task_id: task.id, group_ids: groups.map(g => g.id), checks })
  } finally {
    await cleanupByIds(page, own)
    if (labelCreated) {
      const labels = { ...((await apiRequest(page, `/api/nodes/${labelNode}`)).body as { labels: Record<string, string> }).labels }; delete labels[key]
      expect((await apiRequest(page, `/api/nodes/${labelNode}`, { method: 'POST', data: { labels } })).ok).toBeTruthy()
      expect(((await apiRequest(page, `/api/nodes/${labelNode}`)).body as { labels: Record<string, string> }).labels[key]).toBeUndefined()
    }
  }
})

test('GAP-05 Task所有缺字段真实错误且不创建', async ({ page }) => {
  await loginViaApi(page)
  const nodeId = (await getNodeIds(page))[0], nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string }[], hostname = nodes.find(n => n.id === nodeId)!.hostname
  const checks: unknown[] = []; let posts = 0
  page.on('request', r => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/tasks') posts++ })
  await page.goto('/tasks/new'); const save = page.getByRole('button', { name: /^保存$/ })
  await save.click(); expect(posts).toBe(0); checks.push({ empty_name: 'client rejected', posts: 0 })
  await page.getByLabel('名称', { exact: true }).fill(testName('missing-fields'))
  await page.locator('label').filter({ hasText: hostname }).getByRole('checkbox').check()
  for (const [option, missing] of [['命令', 'command'], ['脚本', 'script'], ['托管应用部署', 'application'], ['托管应用操作', 'application operation']]) {
    await select(page, '类型', option)
    const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/tasks')
    await save.click(); const actual = await response; const body = await actual.json()
    expect(actual.status()).toBe(400); checks.push({ missing, http: actual.status(), error: body })
  }
  await select(page, '类型', '命令'); await page.getByLabel('命令', { exact: true }).fill('echo missing-target')
  await page.locator('label').filter({ hasText: hostname }).getByRole('checkbox').uncheck()
  const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/tasks')
  await save.click(); const actual = await response; expect(actual.status()).toBe(400)
  checks.push({ missing: 'node target', http: actual.status(), error: await actual.json() })
  await evidence('task-missing-fields', { checks, classification: 'REAL_UI; missing-id injection not performed because selectors permit actual IDs only', remaining: ['invalid ID API contract separately owned by Root'] })
})

test('GAP-06 Enrollment真实Retry原参数与pending禁用', async ({ page }) => {
  await loginViaApi(page)
  const own: string[] = [], requests: unknown[] = []; let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  try {
    await page.goto('/agents'); await page.getByRole('button', { name: /^添加节点$/ }).click()
    const dialog = page.getByRole('dialog'), name = dialog.getByLabel(/^节点名称$/), address = dialog.getByLabel(/^节点地址/), hub = dialog.getByLabel(/^Hub 地址$/), generate = dialog.getByRole('button', { name: /^生成安装命令$/ })
    const bad = { node_name: 'bad name!', node_ip: '192.0.2.64', hub_address: new URL(page.url()).origin }
    await name.fill(bad.node_name); await address.fill(bad.node_ip); await hub.fill(bad.hub_address)
    for (const retry of [false, true]) {
      const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/nodes/enrollment' && r.request().method() === 'POST')
      await (retry ? dialog.getByRole('button', { name: /^重试$/ }) : generate).click()
      const actual = await response; expect(actual.status()).toBe(400); expect(actual.request().postDataJSON()).toEqual(bad)
      requests.push({ retry, payload: actual.request().postDataJSON(), http: actual.status(), error: await actual.json() })
    }
    const prefix = testName('enroll-boundary'), goodName = prefix + 'q'.repeat(128 - prefix.length)
    await name.fill(goodName); await hub.fill('https://qa-hub.example.test:8443')
    let postCount = 0
    await page.route('**/api/nodes/enrollment', async route => { postCount++; await gate; await route.continue() })
    const create = captureCreated(page, '/api/nodes/enrollment', () => generate.click())
    await expect.poll(() => postCount).toBe(1); await expect(generate).toBeDisabled()
    const loading = await dialog.locator('[data-slot=skeleton]').count()
    release(); const id = await create; own.push('/api/nodes/' + id)
    const node = (await apiRequest(page, '/api/nodes/' + id)).body as { status: string; hostname: string }
    expect(node).toMatchObject({ status: 'pending', hostname: goodName })
    await expect(generate).toBeEnabled(); expect(postCount).toBe(1)
    const gatewayText = await dialog.innerText()
    await evidence('enrollment-retry-pending', { node_id: id, name_128_accepted: true, https_hub_accepted: true, real_error_retry: requests, pending_disabled: true, skeleton_count: loading, successful_post_count: postCount, classification: 'REAL_UI_DELAY; network request continued unchanged', gateway_display_present: gatewayText.includes('qa-hub.example.test'), commands_omitted_for_credential_safety: true })
  } finally { release?.(); await page.unrouteAll({ behavior: 'wait' }); await page.keyboard.press('Escape'); await cleanupByIds(page, own) }
})

test('GAP-07 Audit真实服务端分页与跨页搜索', async ({ page }) => {
  await loginViaApi(page); await page.goto('/audit')
  const pageSize = page.getByRole('combobox').last()
  await pageSize.click(); await page.getByRole('option', { name: '10', exact: true }).click()
  const next = page.getByRole('button', { name: /^下一页$/ }), previous = page.getByRole('button', { name: /^上一页$/ }), first = page.getByRole('button', { name: /^跳转到第一页$/ })
  await expect(previous).toBeDisabled(); await expect(next).toBeEnabled()
  const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/audit' && new URL(r.url()).searchParams.get('offset') === '10')
  await next.click(); const actual = await response, rows = await actual.json() as { id: string; resource_id: string; detail: string; action: string; resource: string }[]
  await expect(previous).toBeEnabled(); await expect(page.locator('table')).toBeVisible()
  const firstPage = (await apiRequest(page, '/api/audit?limit=10&offset=0')).body as typeof rows
  const marker = rows.find(r => r.resource_id && !firstPage.some(p => p.resource_id === r.resource_id))
  if (!marker) throw new Error('No unique cross-page audit resource ID available; fixture precondition not met')
  const search = page.getByRole('textbox', { name: /搜索/ })
  const globalResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/audit' && new URL(r.url()).searchParams.get('q') === marker.resource_id && new URL(r.url()).searchParams.get('offset') === '0')
  await search.fill(marker.resource_id); await expect(page.locator('tbody')).toContainText(marker.resource_id)
  const searched = await globalResponse
  expect(searched.status()).toBe(200)
  const globalRows = await searched.json() as typeof rows
  expect(globalRows.some(row => row.resource_id === marker.resource_id)).toBe(true)
  await search.fill(''); await expect(first).toBeDisabled(); await expect(previous).toBeDisabled()
  await search.fill(marker.resource_id); await page.waitForLoadState('networkidle')
  const globalHit = (await page.locator('tbody').innerText()).includes(marker.resource_id)
  const reset = page.getByRole('button', { name: /^重置$/ }); await reset.click(); await expect(search).toHaveValue('')
  await next.click(); await expect(previous).toBeEnabled(); await previous.click(); await expect(previous).toBeDisabled()
  await evidence('audit-cross-page-search', { marker, second_page_request: actual.url(), second_page_hit: true, first_page_global_hit: globalHit, next_previous_first_disabled_boundaries: true, reset: true, classification: 'REAL_UI', backend_query: searched.url(), global_response_status: searched.status(), global_response_resource_ids: globalRows.map(row => row.resource_id), global_response_total: searched.headers()['x-total-count'] })
  expect.soft(globalHit, 'Audit search must find known row beyond first server page').toBe(true)
})

test('GAP-08 所有11表隐藏全部可隐藏列并刷新恢复', async ({ page }) => {
  test.setTimeout(180_000); await loginViaApi(page)
  const results: unknown[] = [], own: string[] = []
  try {
  for (const [kind, data] of [['groups', { type: 'static', members: [] }], ['scripts', { interpreter: 'shell', content: 'echo columns', enabled: true }]] as const) {
    const created = await apiRequest(page, '/api/' + kind, { method: 'POST', data: { name: testName('columns-' + kind), ...data } })
    expect(created.ok).toBeTruthy(); own.push('/api/' + kind + '/' + (created.body as { id: string }).id)
  }
  const task = await createIsolatedTask(page, 'columns-task'); own.unshift('/api/tasks/' + task.id)
  const schedule = await apiRequest(page, '/api/schedules', { method: 'POST', data: { task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC', execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: false } })
  expect(schedule.ok).toBeTruthy(); own.unshift('/api/schedules/' + (schedule.body as { id: string }).id)
  const artifact = await upload(page, testName('columns-artifact'), Buffer.from(testName('columns-content'))); own.push('/api/artifacts/' + artifact)
  const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('columns-app'), version: 'v1', artifact_id: artifact, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } })
  expect(app.ok).toBeTruthy(); own.unshift('/api/applications/' + (app.body as { id: string }).id)
  for (const module of ['agents', 'groups', 'scripts', 'tasks', 'schedules', 'applications', 'artifacts', 'executions', 'transfers', 'audit', 'users']) {
    await page.goto('/' + module); await page.waitForLoadState('networkidle')
    const table = page.locator('table').first(), view = page.getByRole('button', { name: /^视图$/ })
    if (!(await table.count())) { results.push({ module, not_executable: 'real empty dataset has no table/column controls' }); continue }
    await expect(view).toBeVisible(); await view.click()
    const options = page.getByRole('menuitemcheckbox'), labels = await options.allTextContents(), baseline = await options.evaluateAll(items => items.map(item => item.getAttribute('aria-checked') === 'true'))
    await page.keyboard.press('Escape'); await expect(options).toHaveCount(0)
    for (let i = 0; i < labels.length; i++) {
      await view.click()
      if (await options.nth(i).getAttribute('aria-checked') === 'true') await options.nth(i).click()
      await page.keyboard.press('Escape'); await expect(options).toHaveCount(0)
    }
    await page.keyboard.press('Escape')
    const hiddenHeaders = await table.locator('thead th').count()
    await page.reload(); await page.waitForLoadState('networkidle'); await view.click()
    const allHiddenPersisted = (await options.evaluateAll(items => items.every(item => item.getAttribute('aria-checked') === 'false')))
    expect(allHiddenPersisted).toBe(true)
    await page.keyboard.press('Escape'); await expect(options).toHaveCount(0)
    for (let i = 0; i < labels.length; i++) {
      await view.click()
      if ((await options.nth(i).getAttribute('aria-checked') === 'true') !== baseline[i]) await options.nth(i).click()
      await page.keyboard.press('Escape'); await expect(options).toHaveCount(0)
    }
    await page.keyboard.press('Escape'); await page.reload(); await page.waitForLoadState('networkidle'); await view.click()
    const restored = await options.evaluateAll(items => items.map(item => item.getAttribute('aria-checked') === 'true'))
    expect(restored).toEqual(baseline); await page.keyboard.press('Escape')
    results.push({ module, columns: labels, baseline, hidden_headers_including_nonhideable: hiddenHeaders, all_hidden_persisted: allHiddenPersisted, restored })
    await evidence('all-tables-all-columns', { results, classification: 'REAL_UI; no data mutations; browser local column preferences restored' })
  }
  } finally { await cleanupByIds(page, own) }
})

test('GAP-09 Artifact真实8MiB上传pending与精确大小SHA', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = [], name = testName('large-artifact'), buffer = Buffer.alloc(8 * 1024 * 1024, name)
  let release!: () => void, requests = 0
  const gate = new Promise<void>(resolve => { release = resolve })
  try {
    await page.goto('/artifacts/new'); await page.getByLabel(/^名称$/).fill(name); await page.getByLabel(/^版本$/).fill('v1')
    await page.locator('input[type=file]').setInputFiles({ name: name + '.bin', mimeType: 'application/octet-stream', buffer })
    await page.route('**/api/artifacts', async route => {
      if (route.request().method() === 'POST') { requests++; await gate }
      await route.continue()
    })
    const button = page.getByRole('button', { name: /^上传$|^上传中/ })
    const create = captureCreated(page, '/api/artifacts', () => button.click())
    await expect.poll(() => requests).toBe(1); await expect(button).toBeDisabled()
    release(); const id = await create; own.push('/api/artifacts/' + id); await page.waitForURL(/\/artifacts$/)
    const artifact = (await apiRequest(page, '/api/artifacts/' + id)).body as { size: number; sha256: string }
    expect(artifact.size).toBe(buffer.length); expect(artifact.sha256).toBe(createHash('sha256').update(buffer).digest('hex')); expect(requests).toBe(1)
    await page.getByRole('textbox', { name: /搜索/ }).fill(name); await expect(page.locator('tbody')).toContainText('8192 KB')
    await evidence('artifact-large-pending', { artifact_id: id, size: artifact.size, sha256: artifact.sha256, pending_disabled: true, upload_requests: requests, classification: 'REAL_UI_DELAY; actual unchanged multipart request', size_limit_contract: 'No configured maximum in current API/ArtifactManager; 8MiB actual upload tested, unbounded enormous files not generated' })
  } finally { release?.(); await page.unrouteAll({ behavior: 'wait' }); await cleanupByIds(page, own) }
})

test('GAP-10 Application内嵌Artifact上传取消错误与成功预选', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = [], name = testName('inline-app'), checks: unknown[] = []
  try {
    const oldId = await upload(page, testName('inline-original'), Buffer.from(testName('old-content'))); own.push('/api/artifacts/' + oldId)
    await page.goto('/applications/new?artifact_id=' + oldId); await page.getByLabel('名称', { exact: true }).fill(name); await page.getByLabel('二进制路径', { exact: true }).fill('/tmp/' + name + '/unused')
    await page.getByRole('button', { name: /^上传新发布包$/ }).click()
    const form = page.locator('form').filter({ has: page.locator('input[name=name]') })
    let uploads = 0; page.on('request', r => { if (r.method() === 'POST' && new URL(r.url()).pathname === '/api/artifacts') uploads++ })
    await form.getByRole('button', { name: /^取消$/ }).click(); expect(uploads).toBe(0)
    checks.push({ cancel_upload_zero_post: true, existing_preset_unchanged: true })
    await page.getByRole('button', { name: /^上传新发布包$/ }).click(); await form.locator('input[name=name]').fill(''); await form.locator('input[name=version]').fill('v2')
    await form.locator('input[type=file]').setInputFiles({ name: 'qa-inline.txt', mimeType: 'application/octet-stream', buffer: Buffer.from(testName('new-content')) })
    await form.getByRole('button', { name: /^上传$/ }).click(); expect(uploads).toBe(0)
    // 浏览器必填失败后直接取消，原Artifact/version依然保持。
    await form.getByRole('button', { name: /^取消$/ }).click(); await expect(page.getByLabel('版本', { exact: true })).toHaveValue('v1')
    await page.getByRole('button', { name: /^上传新发布包$/ }).click(); const newName = testName('inline-new')
    await form.locator('input[name=name]').fill(newName); await form.locator('input[name=version]').fill('v2'); await form.locator('input[type=file]').setInputFiles({ name: newName + '.txt', mimeType: 'application/octet-stream', buffer: Buffer.from(newName) })
    const id = await captureCreated(page, '/api/artifacts', () => form.getByRole('button', { name: /^上传$/ }).click()); own.push('/api/artifacts/' + id)
    await expect(form).toHaveCount(0); await expect(page.getByLabel('版本', { exact: true })).toHaveValue('v2')
    const appId = await captureCreated(page, '/api/applications', () => page.getByRole('button', { name: /^保存$/ }).click()); own.unshift('/api/applications/' + appId)
    await page.waitForURL(new RegExp('/applications/' + appId + '$'))
    const saved = (await apiRequest(page, '/api/applications/' + appId)).body as { artifact_id: string; version: string }
    expect(saved).toMatchObject({ artifact_id: id, version: 'v2' })
    checks.push({ native_required_zero_post: true, auto_selected_artifact_id: id, auto_selected_version: 'v2', saved_app_id: appId })
    await evidence('inline-artifact-upload', { checks, original_artifact_id: oldId, classification: 'REAL_UI', remaining: ['server-side upload failure rendering tested separately using UI_MOCK; not represented by native required validation'] })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-11 Task RunUser启用离线策略及每次ManualRun新UUID', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const network: unknown[] = []
  page.on('requestfailed', request => network.push({ path: new URL(request.url()).pathname, error: request.failure()?.errorText }))
  page.on('response', response => { if (response.status() === 401) network.push({ path: new URL(response.url()).pathname, status: 401 }) })
  const task = await createIsolatedTask(page, 'runuser-offline')
  try {
    await page.waitForLoadState('networkidle'); await page.goto(`/tasks/${task.id}/edit`); await page.waitForLoadState('networkidle'); await page.getByLabel(/^命令/).fill("printf 'qa-run-user='; id -un")
    await page.getByLabel('运行用户', { exact: true }).fill('root'); await select(page, '离线策略', '允许离线')
    const enabled = page.locator('label').filter({ hasText: /^已启用$/ }).getByRole('checkbox'); await enabled.uncheck()
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/)
    expect((await apiRequest(page, `/api/tasks/${task.id}`)).body).toMatchObject({ run_user: 'root', enabled: false, offline_policy: 'allow_offline' })
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click()
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`)
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click(); const disabled = await response
    expect(disabled.status()).toBeGreaterThanOrEqual(400); expect((await apiRequest(page, `/api/executions?task_id=${task.id}&limit=10`)).body).toEqual([])
    await page.goto(`/tasks/${task.id}/edit`); await enabled.check(); await select(page, '离线策略', '需 Hub 在线')
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/)
    const first = await runTask(page, task.id, 'qa-run-user=root'), second = await runTask(page, task.id, 'qa-run-user=root')
    expect(first.execution_id).not.toBe(second.execution_id)
    await evidence('task-runuser-policy-uuid', { task_id: task.id, disabled_run_http: disabled.status(), disabled_run_executions: 0, saved_enabled_offline_variants: true, actual_run_user: 'root', executions: [first, second], new_manual_intent_uuid: true, remaining: ['same UUID delivery/upload idempotence belongs to backend/system evidence', 'offline scheduling not simulated by this online manual run'] })
  } catch (error) { await evidence('task-policy-harness-observation', { error: String(error), url: page.url(), token_present: await page.evaluate(() => !!localStorage.getItem('nodesteer_token')), network }); throw error }
  finally { await loginViaApi(page); await cleanupByIds(page, ['/api/tasks/' + task.id]) }
})

test('GAP-12 自有offline节点明确错误和Node1条件BLOCKED反馈', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = []
  try {
    const enrolled = await apiRequest(page, '/api/nodes/enrollment', { method: 'POST', data: { node_name: testName('offline-fixture'), node_ip: '192.0.2.74', hub_address: new URL(page.url()).origin } })
    expect(enrolled.ok).toBeTruthy(); const nodeId = (enrolled.body as { node_id: string }).node_id; own.push('/api/nodes/' + nodeId)
    expect((await apiRequest(page, '/api/nodes/' + nodeId, { method: 'POST', data: { status: 'offline' } })).ok).toBeTruthy()
    const task = await createIsolatedTask(page, 'offline-blocked'); own.unshift('/api/tasks/' + task.id)
    const current = (await apiRequest(page, '/api/tasks/' + task.id)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, target: { type: 'node', node_ids: [nodeId] } } })).ok).toBeTruthy()
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click()
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`)
    const started = Date.now(); await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
    const offline = await response, offlineBody = await offline.json(); expect(offline.status()).toBe(400)
    const elapsed = Date.now() - started; expect(elapsed).toBeLessThan(10_000)
    await expect(page.getByRole('button', { name: /^立即运行$/ })).toBeEnabled()
    const none = (await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as unknown[]; expect(none).toEqual([])
    const node1 = (await getNodeIds(page))[0], marker = testName('must-not-execute')
    expect((await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, command: 'echo ' + marker, target: { type: 'node', node_ids: [node1] }, condition: { type: 'local', local: { metric: 'file_exists', operator: '==', value: 'true', path: '/tmp/' + marker + '/absent' } } } })).ok).toBeTruthy()
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click()
    const run = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`)
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click(); expect((await run).ok()).toBeTruthy(); await page.waitForURL(/\/executions$/)
    let execution: { id: string; status: string; block_reason: string; stdout: string } | undefined
    await expect.poll(async () => {
      execution = ((await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as typeof execution[])[0]
      return execution?.status
    }, { timeout: 30_000 }).toBe('SKIPPED')
    expect(execution!.block_reason).toBeTruthy(); expect(execution!.stdout || '').not.toContain(marker)
    await page.goto('/executions/' + execution!.id); await page.waitForLoadState('networkidle')
    const skippedReasonVisible = (await page.locator('main').innerText()).includes(execution!.block_reason)
    const skipped = execution
    const neverRun = await createIsolatedTask(page, 'remote-never-run'); own.unshift('/api/tasks/' + neverRun.id)
    expect((await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, command: 'echo ' + marker, condition: { type: 'remote', remote: { node_id: node1, property: 'last_execution', task_id: neverRun.id, operator: '==', value: 'SUCCESS' } } } })).ok).toBeTruthy()
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click()
    const unknownRun = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`)
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click(); expect((await unknownRun).ok()).toBeTruthy(); await page.waitForURL(/\/executions$/)
    await expect.poll(async () => {
      execution = ((await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as typeof execution[]).find(item => item?.status === 'BLOCKED')
      return execution?.status
    }, { timeout: 30_000 }).toBe('BLOCKED')
    expect(execution!.block_reason).toContain('condition unknown'); expect(execution!.stdout || '').not.toContain(marker)
    await page.goto('/executions/' + execution!.id); await page.waitForLoadState('networkidle'); await expect(page.locator('main')).toContainText('已阻断')
    const blockedText = await page.locator('main').innerText(), blockedReasonVisible = blockedText.includes(execution!.block_reason)
    await evidence('offline-node-condition-blocked', { task_id: task.id, synthetic_offline_node_id: nodeId, offline_status: offline.status(), offline_body: offlineBody, response_elapsed_ms: elapsed, offline_execution_count: 0, local_false_skipped_execution: skipped, local_false_skipped_reason_ui_visible: skippedReasonVisible, remote_unknown_blocked_execution: execution, real_node_id: node1, condition_path_absent: true, no_command_marker: true, ui_block_reason_visible: blockedReasonVisible, blocked_page_text: blockedText, daemon_changes: 'none' })
    expect(blockedReasonVisible, 'Real BLOCKED execution must display actual block_reason').toBe(true)
  } finally { await cleanupByIds(page, own) }
})

test('GAP-13 TaskSchedule双页创建取消删除完整一致', async ({ page }) => {
  test.setTimeout(80_000); await loginViaApi(page)
  const task = await createIsolatedTask(page, 'schedule-continuity'), own = ['/api/tasks/' + task.id]
  try {
    await page.goto('/schedules/new?task_id=' + task.id)
    await page.getByRole('textbox', { name: /cron 表达式/i }).fill('0 0 1 1 *')
    const id = await captureCreated(page, '/api/schedules', () => page.getByRole('button', { name: /^保存$/ }).click()); own.unshift('/api/schedules/' + id); await page.waitForURL(/\/schedules$/)
    await page.getByRole('textbox', { name: /搜索/ }).fill(task.name)
    let row = page.locator(`tr:has(a[href="/schedules/${id}"])`); await expect(row).toContainText('0 0 1 1 *')
    await page.goto('/tasks/' + task.id); await expect(page.locator(`a[href="/schedules/${id}"]`)).toBeVisible()
    await page.locator(`a[href="/schedules/${id}"]`).click(); await page.waitForURL(new RegExp('/schedules/' + id + '$')); await expect(page.locator('main')).toContainText(task.name)
    await page.goto('/schedules'); await page.getByRole('textbox', { name: /搜索/ }).fill(task.name); row = page.locator(`tr:has(a[href="/schedules/${id}"])`)
    let deletes = 0; page.on('request', r => { if (r.method() === 'DELETE' && new URL(r.url()).pathname === '/api/schedules/' + id) deletes++ })
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click(); await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
    expect(deletes).toBe(0); expect((await apiRequest(page, '/api/schedules/' + id)).ok).toBeTruthy()
    await page.reload(); await page.getByRole('textbox', { name: /搜索/ }).fill(task.name)
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
    const response = page.waitForResponse(r => r.request().method() === 'DELETE' && new URL(r.url()).pathname === '/api/schedules/' + id)
    await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click(); expect((await response).ok()).toBeTruthy(); expect(deletes).toBe(1); await expect(row).toHaveCount(0)
    await page.goto('/tasks/' + task.id); await expect(page.locator(`a[href="/schedules/${id}"]`)).toHaveCount(0)
    await evidence('task-schedule-continuity', { task_id: task.id, schedule_id: id, task_detail_and_list_consistent: true, schedule_detail_task_name: true, cancel_delete_count: 0, confirm_delete_count: 1, both_pages_removed: true, schedule_after_delete_http: (await apiRequest(page, '/api/schedules/' + id)).status })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-14 Login与五类对象真实CRUD Audit逐事件', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = [], objects: { id: string; resource: string; actions: string[] }[] = []
  try {
    const script = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: testName('audit-script'), interpreter: 'shell', content: 'echo audit', enabled: true } }); expect(script.ok).toBeTruthy()
    const scriptId = (script.body as { id: string }).id; own.push('/api/scripts/' + scriptId)
    const scriptCurrent = (await apiRequest(page, '/api/scripts/' + scriptId)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/scripts/' + scriptId, { method: 'PUT', data: { ...scriptCurrent, content: 'echo audit-updated' } })).ok).toBeTruthy(); objects.push({ id: scriptId, resource: 'script', actions: ['create', 'update', 'delete'] })
    const task = await createIsolatedTask(page, 'audit-task'); own.unshift('/api/tasks/' + task.id)
    const taskCurrent = (await apiRequest(page, '/api/tasks/' + task.id)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...taskCurrent, command: 'echo audit-updated' } })).ok).toBeTruthy(); objects.push({ id: task.id, resource: 'task', actions: ['create', 'update', 'delete'] })
    const schedule = await apiRequest(page, '/api/schedules', { method: 'POST', data: { task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC', execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: false } }); expect(schedule.ok).toBeTruthy()
    const scheduleId = (schedule.body as { id: string }).id; own.unshift('/api/schedules/' + scheduleId)
    const scheduleCurrent = (await apiRequest(page, '/api/schedules/' + scheduleId)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/schedules/' + scheduleId, { method: 'PUT', data: { ...scheduleCurrent, expression: '1 0 1 1 *' } })).ok).toBeTruthy(); objects.push({ id: scheduleId, resource: 'schedule', actions: ['create', 'update', 'delete'] })
    const artifactId = await upload(page, testName('audit-artifact'), Buffer.from(testName('audit-content'))); own.push('/api/artifacts/' + artifactId); objects.push({ id: artifactId, resource: 'artifact', actions: ['upload', 'delete'] })
    const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('audit-app'), version: 'v1', artifact_id: artifactId, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } }); expect(app.ok).toBeTruthy()
    const appId = (app.body as { id: string }).id; own.unshift('/api/applications/' + appId)
    const appCurrent = (await apiRequest(page, '/api/applications/' + appId)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/applications/' + appId, { method: 'PUT', data: { ...appCurrent, version: 'v2' } })).ok).toBeTruthy(); objects.push({ id: appId, resource: 'application', actions: ['create', 'update', 'delete'] })
    await cleanupByIds(page, [...own]); own.length = 0
    const audit = (await apiRequest(page, '/api/audit?limit=100&offset=0')).body as { id: string; action: string; resource: string; resource_id: string; username: string; detail: string }[]
    const login = audit.find(item => item.resource === 'auth' && item.action === 'login' && item.username === process.env.NODESTEER_E2E_USERNAME); expect(login).toBeTruthy()
    const recorded = objects.map(object => ({ ...object, entries: audit.filter(entry => entry.resource_id === object.id) }))
    for (const object of recorded) expect(object.entries.map(entry => entry.action).sort()).toEqual(object.actions.sort())
    await page.goto('/audit'); const size = page.getByRole('combobox').last(); await size.click(); await page.getByRole('option', { name: '50', exact: true }).click()
    const search = page.getByRole('textbox', { name: /搜索/ })
    for (const object of recorded) { await search.fill(object.id); await expect(page.locator('tbody')).toContainText(object.id); expect(await page.locator('tbody tr').count()).toBe(object.actions.length) }
    await search.fill(''); await size.click(); await page.getByRole('option', { name: '10', exact: true }).click()
    await evidence('crud-login-audit', { login, objects: recorded, classification: 'REAL_API_CUD plus REAL_UI audit display for each exact resource ID', artifact_update_not_applicable: 'no metadata update API or UI', credentials_and_tokens_saved: false })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-15 Root控制Node2物理offline窗口的真实UI Run', async ({ page }) => {
  test.setTimeout(140_000); await loginViaApi(page)
  const nodeId = 'd77e2440-919f-4431-a488-733817dcd809', task = await createIsolatedTask(page, 'physical-offline'), privateDir = process.env.NODESTEER_E2E_PRIVATE_DIR!
  const flag = privateDir + '/web-offline-ui-flag.json', completed = privateDir + '/web-offline-ui-completed.json'
  await rm(flag, { force: true }); await rm(completed, { force: true })
  try {
    const current = (await apiRequest(page, '/api/tasks/' + task.id)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, target: { type: 'node', node_ids: [nodeId] } } })).ok).toBeTruthy()
    await page.goto(`/tasks/${task.id}/run`); await page.getByRole('button', { name: /^立即运行$/ }).click(); await expect(page.getByRole('alertdialog')).toBeVisible()
    await writeFile(privateDir + '/web-offline-ui-request.json', JSON.stringify({ task_id: task.id, node_id: nodeId, phase: 'dialog-ready', flag_path: flag, complete_path: completed, root_action: 'Root controls own Node2 daemon stop/status offline, writes offline-ready; Web clicks real dialog, then writes completed; Root restores daemon in finally' }), { mode: 0o600 })
    await expect.poll(async () => {
      try { const data = JSON.parse(await readFile(flag, 'utf8')); return data.node_id === nodeId && data.phase === 'offline-ready' }
      catch { return false }
    }, { timeout: 110_000, intervals: [250, 500] }).toBe(true)
    const before = (await apiRequest(page, '/api/nodes/' + nodeId)).body as { status: string }; expect(before.status).toBe('offline')
    const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/tasks/${task.id}/run`), started = Date.now()
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
    const actual = await response, body = await actual.json(), elapsed = Date.now() - started
    expect(actual.status()).toBe(400); expect(elapsed).toBeLessThan(10_000); await expect(page.getByRole('button', { name: /^立即运行$/ })).toBeEnabled()
    const executions = (await apiRequest(page, `/api/executions?task_id=${task.id}&limit=20`)).body as unknown[]; expect(executions).toEqual([])
    await evidence('physical-offline-task-run', { task_id: task.id, node_id: nodeId, actual_node_status: before.status, daemon_window_owner: 'Root', actual_ui_run_status: actual.status(), actual_error: body, response_elapsed_ms: elapsed, no_executions: true, run_button_reenabled: true, classification: 'REAL_UI_PHYSICAL_OFFLINE' })
  } finally {
    await cleanupByIds(page, ['/api/tasks/' + task.id])
    await writeFile(completed, JSON.stringify({ task_id: task.id, node_id: nodeId, phase: 'completed', timestamp: new Date().toISOString() }), { mode: 0o600 })
  }
})

test('GAP-16 [UI_MOCK] 11表ErrorRetry/Empty与Dashboard空态Hub指示', async ({ page }) => {
  test.setTimeout(400_000); await loginViaApi(page)
  const checks: unknown[] = []
  for (const module of ['agents', 'groups', 'scripts', 'tasks', 'schedules', 'applications', 'artifacts', 'executions', 'transfers', 'audit', 'users']) {
    const endpoint = '/api/' + (module === 'agents' ? 'nodes' : module)
    let mode = 'error', requests = 0
    await page.route('**' + endpoint + '*', async route => {
      if (new URL(route.request().url()).pathname !== endpoint || route.request().method() !== 'GET') { await route.continue(); return }
      requests++
      if (mode === 'error') await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'qa-specific-' + module + '-unavailable' }) })
      else if (mode === 'empty') await route.fulfill({ status: 200, contentType: 'application/json', headers: { 'X-Total-Count': '0' }, body: '[]' })
      else await route.continue()
    })
    await page.goto('/' + module)
    const retry = page.getByRole('button', { name: /^重试$/ })
    try { await expect(retry).toBeVisible({ timeout: 30_000 }) }
    catch (error) { await evidence('mock-error-harness-' + module, { requests, url: page.url(), main: (await page.locator('main').innerText()).slice(0, 1500), token_present: await page.evaluate(() => !!localStorage.getItem('nodesteer_token')) }); throw error }
    await expect(page.locator('main')).toContainText('qa-specific-' + module + '-unavailable')
    const before = requests; mode = 'real'; await retry.click(); await expect(retry).toHaveCount(0); expect(requests).toBeGreaterThan(before)
    mode = 'empty'; await page.reload(); await page.waitForLoadState('networkidle')
    const emptyText = await page.locator('main').innerText(), distinctEmpty = emptyText.includes('暂无数据') && !emptyText.includes('没有结果。')
    checks.push({ module, error_503_specific: true, retry_request_count: requests - before, retry_actual_real_server: true, mocked_dataset_empty_distinct_from_no_results: distinctEmpty, mocked_empty_actual_text: emptyText.slice(0, 450) })
    expect.soft(distinctEmpty, module + ' empty dataset should differ from filtered No Results').toBe(true)
    await page.unroute('**' + endpoint + '*')
    await evidence('all-tables-error-retry-mock', { checks, classification: 'UI_MOCK; injected 503/empty arrays, Retry continues to real Hub. Real service failure not claimed.' })
  }
  await page.route('**/api/*', async route => {
    const path = new URL(route.request().url()).pathname
    if (['/api/nodes', '/api/executions', '/api/tasks', '/api/applications', '/api/schedules'].includes(path)) await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
    else await route.continue()
  })
  await page.goto('/'); await page.waitForLoadState('networkidle'); const empty = await page.locator('main').innerText()
  expect(empty).toContain('暂无数据')
  await page.unrouteAll({ behavior: 'wait' })
  await page.route('**/healthz', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"qa-health-unavailable"}' }))
  await page.reload(); await expect(page.getByText('Hub 不可用', { exact: true })).toBeVisible()
  const unavailableDot = await page.getByText('Hub 不可用', { exact: true }).locator('span[aria-hidden=true]').getAttribute('class')
  expect(unavailableDot).toContain('bg-destructive')
  await page.unrouteAll({ behavior: 'wait' }); await page.reload(); await expect(page.getByText('Hub 已连接', { exact: true })).toBeVisible()
  await evidence('dashboard-empty-hub-unavailable-mock', { classification: 'UI_MOCK', mocked_empty_dashboard_text: empty, health_503_unavailable_dot: unavailableDot, actual_hub_restore: true, no_actual_hub_daemon_change: true })
})

test('GAP-17 Application列表入口编辑取消与Viewer审计不可写', async ({ page, browser }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = [], viewerId = '8ac0ef0f-fea5-4be3-8707-2c580363353b'
  let viewerContext: Awaited<ReturnType<typeof browser.newContext>> | undefined
  try {
    const artifact = await upload(page, testName('list-app-artifact'), Buffer.from(testName('list-content'))); own.push('/api/artifacts/' + artifact)
    const name = testName('list-app'), app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name, version: 'v1', artifact_id: artifact, binary_path: '/tmp/' + name + '/unused', unit_name: name + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } }); expect(app.ok).toBeTruthy()
    const appId = (app.body as { id: string }).id; own.unshift('/api/applications/' + appId)
    await page.goto('/applications'); await page.getByRole('textbox', { name: /搜索/ }).fill(name)
    const row = page.locator(`tr:has(a[href="/applications/${appId}"])`); await expect(row).toBeVisible()
    await row.getByRole('button', { name: /^操作$/ }).click(); const menu = await page.getByRole('menuitem').allTextContents()
    expect(menu).toContain('编辑'); expect(menu).toContain('删除'); expect(menu.some(x => /部署/.test(x))).toBe(false)
    await page.keyboard.press('Escape'); await expect(page.locator('a[href="/applications/new"]')).toBeVisible()
    await row.locator(`a[href="/applications/${appId}"]`).click(); await page.waitForURL(new RegExp('/applications/' + appId + '$'))
    const before = (await apiRequest(page, '/api/applications/' + appId)).body as { revision: number; name: string }
    await page.getByLabel('名称', { exact: true }).fill(testName('unsaved-name'))
    let puts = 0; page.on('request', r => { if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/applications/' + appId) puts++ })
    await page.getByRole('link', { name: /^取消$/ }).click(); await page.waitForURL(/\/applications$/)
    const after = (await apiRequest(page, '/api/applications/' + appId)).body as typeof before
    expect(after.revision).toBe(before.revision); expect(after.name).toBe(name); expect(puts).toBe(0)
    await evidence('application-list-entry-cancel', { app_id: appId, row_detail_link: true, menu, no_list_deploy: true, new_entry: true, cancel_puts: puts, before_revision: before.revision, after_revision: after.revision, unsaved_name_not_persisted: true })
    // 仅恢复已授权且旧密码丢失的exact保留QA Viewer账号；不创建/变更其他User。
    const users = (await apiRequest(page, '/api/users')).body as { id: string; username: string; role: string }[], viewer = users.find(user => user.id === viewerId)!
    expect(viewer.role).toBe('viewer'); const password = 'Qa!' + randomBytes(20).toString('hex') + '9z'
    expect((await apiRequest(page, '/api/users/' + viewerId, { method: 'PUT', data: { password, role: 'viewer' } })).ok).toBeTruthy()
    await appendFile(process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl', JSON.stringify({ user_id: viewerId, username: viewer.username, password, role: 'viewer', purpose: 'authorized exact retained QA password recovery' }) + '\n', { mode: 0o600 })
    viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin }); const viewerPage = await viewerContext.newPage(), token = await loginViaApi(viewerPage, viewer.username, password)
    await appendFile(process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl', JSON.stringify({ user_id: viewerId, token }) + '\n', { mode: 0o600 })
    const source = JSON.parse(await readFile(process.env.NODESTEER_E2E_EVIDENCE_DIR + '/crud-login-audit.json', 'utf8')), ownAuditId = source.objects[0].entries[0].id
    const find = async () => ((await apiRequest(page, '/api/audit?limit=500&offset=0')).body as { id: string }[]).find(entry => entry.id === ownAuditId)
    const auditBefore = await find(); expect(auditBefore).toBeTruthy()
    const probes = []
    for (const method of ['PUT', 'DELETE']) {
      const response = await apiRequest(viewerPage, '/api/audit', { method, data: method === 'PUT' ? { id: ownAuditId, action: 'qa-must-not-change-audit' } : undefined })
      probes.push({ method, http: response.status, returned_read_array: Array.isArray(response.body) })
    }
    await viewerPage.goto('/audit'); await viewerPage.waitForLoadState('networkidle')
    const forbiddenControls = await viewerPage.locator('main').getByRole('button', { name: /^删除$|^保存$|^编辑$/ }).count(); expect(forbiddenControls).toBe(0)
    const auditAfter = await find(); expect(auditAfter).toEqual(auditBefore)
    await evidence('viewer-audit-immutable', { user_id: viewerId, role: 'viewer', owned_audit_id: ownAuditId, audit_before: auditBefore, audit_after: auditAfter, probes, forbidden_controls: forbiddenControls, exact_audit_unchanged: true, password_recovered_private_only: true, method_contract: 'current /api/audit handler reads for every method; HTTP200/read-array is not an audit mutation' })
    expect((await apiRequest(viewerPage, '/api/logout', { method: 'POST', data: {} })).ok).toBeTruthy()
  } finally { await viewerContext?.close(); await cleanupByIds(page, own) }
})

test('GAP-18 UI LoginDashboardAgents连续链与真实统计详情链接', async ({ page }) => {
  test.setTimeout(120_000); await login(page)
  const own: string[] = [], checks: unknown[] = [], nodeId = (await getNodeIds(page))[0]
  try {
    const task = await createIsolatedTask(page, 'dashboard-continuity'); own.unshift('/api/tasks/' + task.id)
    const schedule = await apiRequest(page, '/api/schedules', { method: 'POST', data: { task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC', execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: true } }); expect(schedule.ok).toBeTruthy()
    const scheduleId = (schedule.body as { id: string }).id; own.unshift('/api/schedules/' + scheduleId)
    const artifact = await upload(page, testName('dashboard-artifact'), Buffer.from(testName('dashboard-content'))); own.push('/api/artifacts/' + artifact)
    const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('dashboard-app'), version: 'v1', artifact_id: artifact, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } }); expect(app.ok).toBeTruthy()
    const appId = (app.body as { id: string }).id; own.unshift('/api/applications/' + appId)
    const nodesResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/nodes' && r.request().method() === 'GET'), execResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/executions' && new URL(r.url()).searchParams.get('limit') === '50')
    await page.goto('/'); const nodes = await (await nodesResponse).json() as { id: string; hostname: string; status: string; sync_status: string }[], executions = await (await execResponse).json() as { id: string; status: string }[]
    const terminal = executions.filter(e => ['SUCCESS', 'FAILED', 'TIMED_OUT'].includes(e.status)), rate = terminal.length ? `${Math.round(executions.filter(e => e.status === 'SUCCESS').length / terminal.length * 1000) / 10}%` : '-'
    const metrics = [['节点总数', String(nodes.length)], ['运行中', String(executions.filter(e => e.status === 'RUNNING').length)], ['执行失败', String(executions.filter(e => ['FAILED', 'TIMED_OUT'].includes(e.status)).length)], ['成功率', rate]]
    for (const [label, value] of metrics) await expect(page.locator('[data-slot=card]').filter({ has: page.getByText(label, { exact: true }) }).first()).toContainText(value)
    const appCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^托管应用状态$/ }) }); await expect(appCard).toContainText('0/1')
    const syncCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^同步状态$/ }) }), syncCounts = Object.fromEntries(['synced', 'outdated', 'error'].map(status => [status, nodes.filter(node => node.sync_status.toLowerCase() === status).length]))
    const syncValues = await syncCard.locator('strong').allTextContents(); expect(syncValues.map(Number)).toEqual(Object.values(syncCounts))
    const links = await page.locator('main a').evaluateAll(elements => elements.map(element => element.getAttribute('href')).filter((value): value is string => !!value && /^\/(applications|executions|schedules)\//.test(value)))
    for (const path of [...new Set(links)]) {
      await page.goto('/'); await page.waitForLoadState('networkidle'); await page.locator(`main a[href="${path}"]`).first().click(); await expect(page).toHaveURL(new RegExp(path + '$')); await page.waitForLoadState('networkidle')
      await expect(page.locator('main')).not.toContainText('not found'); checks.push({ path, real_detail_link: true })
    }
    await page.goto('/'); await page.waitForLoadState('networkidle')
    const agentCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^Agent 健康状态$/ }) }); await agentCard.getByRole('link', { name: /查看全部/ }).click(); await page.waitForURL(/\/agents$/)
    await page.getByRole('textbox', { name: /搜索/ }).fill(nodes.find(node => node.id === nodeId)!.hostname)
    await page.locator(`a[href="/agents/${nodeId}"]`).click(); await page.waitForURL(new RegExp('/agents/' + nodeId + '$'))
    await page.locator(`a[href="/tasks/${task.id}"]`).first().click(); await page.waitForURL(new RegExp('/tasks/' + task.id + '$')); await expect(page.locator('main')).toContainText(nodes.find(node => node.id === nodeId)!.hostname)
    await evidence('dashboard-continuous-metrics-links', { task_id: task.id, schedule_id: scheduleId, app_id: appId, metrics, metric_window: 'actual Dashboard /executions?limit=50 response', app_healthy_total: '0/1 unassigned own App', sync_counts: syncCounts, sync_ui_values: syncValues, links: checks, actual_ui_login: true, login_dashboard_agent_view_all: true, agent_detail_task_return_target: nodeId, no_original_resource_mutations: true })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-19 Artifact无引用UI取消确认删除与真实下载404', async ({ page }) => {
  await loginViaApi(page); const name = testName('artifact-ui-delete'), id = await upload(page, name, Buffer.from(name))
  try {
    await page.getByRole('textbox', { name: /搜索/ }).fill(name); const row = page.locator('tr').filter({ hasText: name })
    let deletes = 0; page.on('request', r => { if (r.method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id) deletes++ })
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click(); await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
    expect(deletes).toBe(0); await expect(row).toBeVisible()
    await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
    const response = page.waitForResponse(r => r.request().method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id)
    await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click(); expect((await response).status()).toBe(200); expect(deletes).toBe(1); await expect(row).toHaveCount(0)
    const after = await apiRequest(page, '/api/artifacts/' + id), download = await apiRequest(page, '/api/artifacts/' + id + '/download')
    expect(after.status).toBe(404); expect(download.status).toBe(404)
    await evidence('artifact-unreferenced-ui-delete', { artifact_id: id, cancel_delete_count: 0, confirm_delete_count: 1, actual_delete_http: 200, no_reference_row_removed: true, object_after_delete_http: after.status, download_after_delete_http: download.status })
  } finally { await cleanupByIds(page, ['/api/artifacts/' + id]) }
})

test('GAP-20 禁用Script实际引用拒绝恢复和定义边界', async ({ page }) => {
  test.setTimeout(100_000); await loginViaApi(page)
  const own: string[] = [], name = testName('disabled-reference'), marker = testName('enabled-marker')
  try {
    const response = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name, interpreter: 'shell', content: 'echo ' + marker, timeout: 30, enabled: false } }); expect(response.ok).toBeTruthy()
    const id = (response.body as { id: string }).id; own.push('/api/scripts/' + id)
    const task = await createIsolatedTask(page, 'disabled-script-task'); own.unshift('/api/tasks/' + task.id)
    await page.goto(`/tasks/${task.id}/edit`); await select(page, '类型', '脚本'); await select(page, '脚本', name)
    const rejected = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname === '/api/tasks/' + task.id)
    await page.getByRole('button', { name: /^保存$/ }).click(); const bad = await rejected; expect(bad.status()).toBe(400)
    const error = await bad.json(); await expect(page.getByText(error.error, { exact: true }).last()).toBeVisible()
    const current = (await apiRequest(page, '/api/scripts/' + id)).body as Record<string, unknown>
    expect((await apiRequest(page, '/api/scripts/' + id, { method: 'PUT', data: { ...current, enabled: true } })).ok).toBeTruthy()
    await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/); const execution = await runTask(page, task.id, marker)
    const duplicate = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name, interpreter: 'shell', content: 'echo duplicate', enabled: false } })
    if (duplicate.ok) own.push('/api/scripts/' + (duplicate.body as { id: string }).id)
    const content = '# ' + testName('long-script') + '\n' + '# qa-long-content\n'.repeat(16384)
    const large = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: testName('large-script'), interpreter: 'shell', content, timeout: 0, enabled: false } }); expect(large.ok).toBeTruthy()
    const largeId = (large.body as { id: string }).id; own.push('/api/scripts/' + largeId)
    const largeSaved = (await apiRequest(page, '/api/scripts/' + largeId)).body as { content: string; sha256: string; timeout: number }
    expect(largeSaved.content).toBe(content); expect(largeSaved.sha256).toBe(createHash('sha256').update(content).digest('hex'))
    const negative = await apiRequest(page, '/api/scripts/' + largeId, { method: 'PUT', data: { ...largeSaved, name: testName('negative-timeout'), interpreter: 'shell', timeout: -1, enabled: false } })
    await evidence('script-disabled-reference-boundaries', { script_id: id, task_id: task.id, disabled_reference_http: bad.status(), disabled_reference_error: error, actual_ui_error_visible: true, enabled_restored_execution: execution, duplicate_name_http: duplicate.status, duplicate_distinct_id: duplicate.ok ? (duplicate.body as { id: string }).id : null, duplicate_contract: 'observed actual current acceptance/rejection; uniqueness not assumed', large_script_id: largeId, large_content_bytes: Buffer.byteLength(content), exact_content_roundtrip: true, sha256: largeSaved.sha256, timeout_zero: largeSaved.timeout, negative_timeout_http: negative.status, negative_timeout_body: negative.body, classification: 'REAL_UI disabled reference/recovery + REAL_API boundary CUD', remaining: ['large content/duplicate/negative timeout UI editor rendering is not represented by API evidence'] })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-21 长命令详情可选择复制且操作入口仍可用', async ({ page, context }) => {
  await loginViaApi(page)
  const task = await createIsolatedTask(page, 'long-command-layout')
  const command = 'printf %s ' + 'qa-long-command-path-' + 'x'.repeat(2048) + ' ' + randomBytes(32).toString('hex')
  try {
    const current = (await apiRequest(page, '/api/tasks/' + task.id)).body as Record<string, unknown>
    const saved = await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, command } })
    expect(saved.ok).toBeTruthy()
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.setViewportSize({ width: 760, height: 720 })
    await page.goto('/tasks/' + task.id)
    const definition = page.locator('pre')
    await expect(definition).toHaveText(command)
    const layout = await definition.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      return {
        client_width: element.clientWidth,
        scroll_width: element.scrollWidth,
        client_height: element.clientHeight,
        scroll_height: element.scrollHeight,
        right: Math.round(rect.right),
        viewport_width: window.innerWidth,
        white_space: getComputedStyle(element).whiteSpace,
        overflow_x: getComputedStyle(element).overflowX,
      }
    })
    await definition.selectText()
    const selected = await page.evaluate(() => window.getSelection()?.toString() || '')
    await page.keyboard.press('Control+C')
    const copied = await page.evaluate(() => navigator.clipboard?.readText().catch(() => '') || '')
    const runButton = page.getByRole('link', { name: /立即运行/ })
    await evidence('long-command-detail-layout', { task_id: task.id, command_chars: command.length, exact_text_rendered: true, selected_chars: selected.length, clipboard_exact: copied === command, clipboard_read_available: !!copied, layout, run_action_visible_and_enabled: true, classification: 'REAL_UI; Task definition saved to Hub and rendered by actual detail page; not executed' })
    expect(selected).toBe(command)
    if (copied) expect(copied).toBe(command)
    expect(layout.right).toBeLessThanOrEqual(layout.viewport_width)
    await expect(runButton).toBeVisible()
    await expect(runButton).toBeEnabled()
  } finally { await cleanupByIds(page, ['/api/tasks/' + task.id]) }
})

test('GAP-22 App/Group/Schedule详情与动态菜单按钮名称', async ({ page }) => {
  await loginViaApi(page)
  const own: string[] = []
  const checks: { module: string; path: string | null; unnamed_buttons: string[]; menu_items: string[]; status: string }[] = []
  try {
    const artifactId = await upload(page, testName('ax-artifact'), Buffer.from('definition only'))
    own.push('/api/artifacts/' + artifactId)
    const task = await createIsolatedTask(page, 'ax-schedule-task')
    own.unshift('/api/tasks/' + task.id)
    const group = await apiRequest(page, '/api/groups', { method: 'POST', data: { name: testName('ax-group'), type: 'static', members: [] } })
    expect(group.ok).toBeTruthy()
    const groupId = (group.body as { id: string }).id
    own.unshift('/api/groups/' + groupId)
    const schedule = await apiRequest(page, '/api/schedules', { method: 'POST', data: { task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC', execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: false } })
    expect(schedule.ok).toBeTruthy()
    const scheduleId = (schedule.body as { id: string }).id
    own.unshift('/api/schedules/' + scheduleId)
    const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('ax-app'), version: 'v1', artifact_id: artifactId, binary_path: '/tmp/qa-definition-only', unit_name: testName('ax-unit') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } })
    expect(app.ok).toBeTruthy()
    const appId = (app.body as { id: string }).id
    own.unshift('/api/applications/' + appId)
    for (const [module, id] of [['applications', appId], ['groups', groupId], ['schedules', scheduleId]]) {
      const path = '/' + module + '/' + id
      await page.goto(path)
      await page.waitForLoadState('networkidle')
      const unnamed = await page.locator('button').evaluateAll((buttons) => buttons.filter((button) => {
        const element = button as HTMLElement
        const role = button.getAttribute('role')
        if (role && role !== 'button') return false
        if (!element.getClientRects().length) return false
        return !(button.getAttribute('aria-label') || button.getAttribute('title') || button.textContent?.trim() || button.querySelector('svg title')?.textContent?.trim())
      }).map((button) => ({ class_name: (button as HTMLElement).className })))
      await page.goto('/' + module)
      await page.waitForLoadState('networkidle')
      const row = page.locator('tbody tr').filter({ hasText: module === 'groups' ? 'ax-group' : module === 'schedules' ? task.name : 'ax-app' }).first()
      const actions = row.getByRole('button', { name: /^操作$/ })
      await actions.click()
      const items = (await page.getByRole('menuitem').allTextContents()).map(x => x.trim()).filter(Boolean)
      expect(items.length).toBeGreaterThan(0)
      expect(unnamed, `${path} has unnamed detail buttons`).toEqual([])
      await page.keyboard.press('Escape')
      checks.push({ module, path, unnamed_buttons: [], menu_items: items, status: 'CHECKED' })
    }
    await evidence('dynamic-detail-menu-button-names', { app_id: appId, group_id: groupId, schedule_id: scheduleId, task_id: task.id, checks, classification: 'REAL_UI_READONLY; exact own definitions, no App deployment or Task execution' })
  } finally { await cleanupByIds(page, own) }
})

test('GAP-23 自有QA节点维护阻断Run并联动Dashboard统计', async ({ page }) => {
  test.setTimeout(90_000)
  await loginViaApi(page)
  const name = testName('maintenance-dashboard'), own: string[] = []
  let nodeId = '', taskId = ''
  const setNodeStatus = async (action: string) => {
    await page.goto('/agents')
    await page.getByRole('textbox', { name: /搜索/ }).fill(name)
    const row = page.locator(`tr:has(a[href="/agents/${nodeId}"])`)
    await row.getByRole('button', { name: /^操作$/ }).click()
    await page.getByRole('menuitem', { name: action, exact: true }).click()
    const expected = action === '维护' ? 'maintenance' : 'online'
    await expect.poll(async () => ((await apiRequest(page, '/api/nodes/' + nodeId)).body as { status: string }).status).toBe(expected)
  }
  const dashboardCounts = async () => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    const nodes = (await apiRequest(page, '/api/nodes')).body as { status: string }[]
    const expected = {
      total: nodes.length,
      online: nodes.filter(node => node.status === 'online').length,
      offline: nodes.filter(node => node.status === 'offline').length,
      degraded: nodes.filter(node => !['online', 'offline'].includes(node.status)).length,
    }
    const totalCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^节点总数$/ }) }).first()
    await expect(totalCard.locator('.text-2xl')).toHaveText(String(expected.total))
    const health = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^Agent 健康状态$/ }) })
    const values = (await health.locator('strong').allTextContents()).map(Number)
    expect(values).toEqual([expected.online, expected.offline, expected.degraded])
    const executions = (await apiRequest(page, '/api/executions?limit=50')).body as { status: string }[]
    const terminal = executions.filter(execution => ['SUCCESS', 'FAILED', 'TIMED_OUT'].includes(execution.status)).length
    const runStats = {
      running: executions.filter(execution => execution.status === 'RUNNING').length,
      failed: executions.filter(execution => ['FAILED', 'TIMED_OUT'].includes(execution.status)).length,
      success_rate: terminal ? `${Math.round(executions.filter(execution => execution.status === 'SUCCESS').length / terminal * 1000) / 10}%` : '-',
    }
    for (const [label, value] of [['运行中', String(runStats.running)], ['执行失败', String(runStats.failed)], ['成功率', runStats.success_rate]]) {
      const card = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: new RegExp('^' + label + '$') }) }).first()
      await expect(card.locator('.text-2xl')).toHaveText(value)
    }
    return { ...expected, run_stats: runStats }
  }
  try {
    const created = await apiRequest(page, '/api/nodes/enrollment', { method: 'POST', data: { node_name: name, node_ip: '192.0.2.89', hub_address: new URL(page.url()).origin } })
    expect(created.status).toBe(200)
    nodeId = (created.body as { node_id: string }).node_id
    own.unshift('/api/nodes/' + nodeId)
    await apiRequest(page, '/api/nodes/' + nodeId, { method: 'POST', data: { status: 'online' } })
    const onlineCounts = await dashboardCounts()
    await setNodeStatus('维护')
    const maintenanceCounts = await dashboardCounts()
    expect(maintenanceCounts.total).toBe(onlineCounts.total)
    expect(maintenanceCounts.online).toBe(onlineCounts.online - 1)
    expect(maintenanceCounts.degraded).toBe(onlineCounts.degraded + 1)
    const task = await apiRequest(page, '/api/tasks', { method: 'POST', data: { name: testName('maintenance-run'), type: 'command', command: 'echo must-not-run', target: { type: 'node', node_ids: [nodeId] }, timeout: 30, retry: 0, offline_policy: 'hub_online_required', enabled: true } })
    expect(task.ok).toBeTruthy()
    taskId = (task.body as { id: string }).id
    own.unshift('/api/tasks/' + taskId)
    await page.goto('/tasks/' + taskId + '/run')
    await page.getByRole('button', { name: /^立即运行$/ }).click()
    const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/tasks/' + taskId + '/run')
    await page.getByRole('alertdialog').getByRole('button', { name: /^立即运行$/ }).click()
    const actual = await response, body = await actual.json()
    const runUiText = await page.locator('body').innerText()
    const specificMaintenanceErrorVisible = runUiText.includes('status=maintenance')
    const executions = (await apiRequest(page, '/api/executions?task_id=' + taskId + '&limit=20')).body as unknown[]
    const dashboardAfterRun = await dashboardCounts()
    await setNodeStatus('恢复')
    const restoredCounts = await dashboardCounts()
    await evidence('maintenance-dashboard-run-guard', { node_id: nodeId, task_id: taskId, online_counts: onlineCounts, maintenance_counts: maintenanceCounts, run_http: actual.status(), run_body: body, specific_maintenance_error_visible: specificMaintenanceErrorVisible, run_ui_text: runUiText.slice(0, 1200), no_executions: executions.length === 0, dashboard_after_run: dashboardAfterRun, restored_counts: restoredCounts, task_command_executed: false, scope: 'own pending enrollment record only; no Agent daemon/service or existing node changed' })
    expect(actual.status()).toBe(400)
    expect(executions).toEqual([])
    expect(specificMaintenanceErrorVisible).toBe(true)
    expect(dashboardAfterRun.run_stats).toEqual(maintenanceCounts.run_stats)
    expect(restoredCounts.total).toBe(onlineCounts.total)
    expect(restoredCounts.online).toBe(onlineCounts.online)
    expect(restoredCounts.degraded).toBe(onlineCounts.degraded)
  } finally {
    if (taskId) {
      const executions = (await apiRequest(page, '/api/executions?task_id=' + taskId + '&limit=20')).body as { id: string; status: string }[]
      for (const execution of executions) if (['PENDING', 'RUNNING'].includes(execution.status)) await apiRequest(page, '/api/executions/' + execution.id + '/cancel', { method: 'POST' })
    }
    if (nodeId) await apiRequest(page, '/api/nodes/' + nodeId, { method: 'POST', data: { status: 'online' } })
    if (own.length) await cleanupByIds(page, own)
  }
})

test('GAP-24 Dashboard真实运行/应用/调度空态与服务端一致', async ({ page }) => {
  await loginViaApi(page)
  const executions = (await apiRequest(page, '/api/executions?limit=50')).body as { id: string; status: string }[]
  const applications = (await apiRequest(page, '/api/applications')).body as { id: string; name: string }[]
  const schedules = (await apiRequest(page, '/api/schedules')).body as { id: string; enabled: boolean; task_id: string }[]
  const running = executions.filter(execution => execution.status === 'RUNNING')
  const upcoming = schedules.filter(schedule => schedule.enabled).slice(0, 5)
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  const checks = {
    actual_running_execution_ids: running.map(execution => execution.id),
    actual_application_ids: applications.map(application => application.id),
    actual_enabled_schedule_ids: upcoming.map(schedule => schedule.id),
    empty_running_message_visible: false,
    empty_application_message_visible: false,
    empty_schedule_message_visible: false,
    recent_failed_count: executions.filter(execution => ['FAILED', 'TIMED_OUT'].includes(execution.status)).length,
    recent_execution_count: executions.length,
  }
  if (running.length === 0) {
    await expect(page.getByText('当前没有正在执行的任务。', { exact: true })).toBeVisible()
    checks.empty_running_message_visible = true
  }
  const appCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^托管应用状态$/ }) })
  if (applications.length === 0) {
    await expect(appCard).toContainText('暂无托管应用')
    checks.empty_application_message_visible = true
  } else {
    for (const application of applications) await expect(appCard.locator(`a[href="/applications/${application.id}"]`)).toBeVisible()
  }
  const scheduleCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^即将执行的调度$/ }) })
  if (upcoming.length === 0) {
    await expect(scheduleCard).toContainText('暂无调度')
    checks.empty_schedule_message_visible = true
  } else {
    for (const schedule of upcoming) await expect(scheduleCard.locator(`a[href="/schedules/${schedule.id}"]`)).toBeVisible()
  }
  await evidence('dashboard-real-empty-state-applicability', { checks, classification: 'REAL_API_DATA_AND_REAL_UI; existing failed history is preserved and never cleared or mocked', remaining: checks.recent_failed_count ? ['no-failed-executions empty state is not applicable while real recent failure rows exist'] : [] })
})
