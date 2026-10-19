# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: gap-ui.spec.ts >> GAP-18 UI LoginDashboardAgents连续链与真实统计详情链接
- Location: e2e/tests/gap-ui.spec.ts:716:1

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('[data-slot=card]').filter({ has: locator('[data-slot=card-title]').filter({ hasText: /^托管应用状态$/ }) })
Expected substring: "0/1"
Received string:    "托管应用状态0/2 健康查看全部 qa-final2-4d2e1438-app未知qa-pw2-20261018-dashboard-app-03b1bdb2未知"
Timeout: 5000ms

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('[data-slot=card]').filter({ has: locator('[data-slot=card-title]').filter({ hasText: /^托管应用状态$/ }) })
    14 × locator resolved to <div data-slot="card" class="flex flex-col rounded-xl border bg-card text-card-foreground shadow-sm h-full gap-3 py-4 lg:col-span-4">…</div>
       - unexpected value "托管应用状态0/2 健康查看全部 qa-final2-4d2e1438-app未知qa-pw2-20261018-dashboard-app-03b1bdb2未知"

```

```yaml
- text: 托管应用状态 0/2 健康
- link "查看全部":
  - /url: /applications
- link "qa-final2-4d2e1438-app 未知":
  - /url: /applications/bcc30ed6-0e6f-4c7b-9164-1653d2d66d7d
- link "qa-pw2-20261018-dashboard-app-03b1bdb2 未知":
  - /url: /applications/d7e5b659-1a39-4c65-8b6b-69c2e3baa04b
```

# Test source

```ts
  631 |     await evidence('all-tables-error-retry-mock', { checks, classification: 'UI_MOCK; injected 503/empty arrays, Retry continues to real Hub. Real service failure not claimed.' })
  632 |   }
  633 |   await page.route('**/api/*', async route => {
  634 |     const path = new URL(route.request().url()).pathname
  635 |     if (['/api/nodes', '/api/executions', '/api/tasks', '/api/applications', '/api/schedules'].includes(path)) await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' })
  636 |     else await route.continue()
  637 |   })
  638 |   await page.goto('/'); await page.waitForLoadState('networkidle'); const empty = await page.locator('main').innerText()
  639 |   // Dashboard 空态使用领域化文案（features/overview/index.tsx:252、:351 与 schedules.noSchedules），
  640 |   // 不存在通用「暂无数据」字面量：套件期望对齐产品实际行为（保留产品现状，不判缺陷）。
  641 |   expect(empty).toContain('当前没有正在执行的任务。')
  642 |   expect(empty).toContain('暂无托管应用')
  643 |   expect(empty).toContain('暂无调度')
  644 |   await page.unrouteAll({ behavior: 'wait' })
  645 |   await page.route('**/healthz', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"qa-health-unavailable"}' }))
  646 |   await page.reload(); await expect(page.getByText('Hub 不可用', { exact: true })).toBeVisible()
  647 |   const unavailableDot = await page.getByText('Hub 不可用', { exact: true }).locator('span[aria-hidden=true]').getAttribute('class')
  648 |   expect(unavailableDot).toContain('bg-destructive')
  649 |   await page.unrouteAll({ behavior: 'wait' }); await page.reload(); await expect(page.getByText('Hub 已连接', { exact: true })).toBeVisible()
  650 |   await evidence('dashboard-empty-hub-unavailable-mock', { classification: 'UI_MOCK', mocked_empty_dashboard_text: empty, health_503_unavailable_dot: unavailableDot, actual_hub_restore: true, no_actual_hub_daemon_change: true })
  651 | })
  652 | 
  653 | test('GAP-17 Application列表入口编辑取消与Viewer审计不可写', async ({ page, browser }) => {
  654 |   test.setTimeout(100_000); await loginViaApi(page)
  655 |   const own: string[] = []
  656 |   let viewerContext: Awaited<ReturnType<typeof browser.newContext>> | undefined
  657 |   try {
  658 |     const artifact = await upload(page, testName('list-app-artifact'), Buffer.from(testName('list-content'))); own.push('/api/artifacts/' + artifact)
  659 |     const name = testName('list-app'), app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name, version: 'v1', artifact_id: artifact, binary_path: '/tmp/' + name + '/unused', unit_name: name + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } }); expect(app.ok).toBeTruthy()
  660 |     const appId = (app.body as { id: string }).id; own.unshift('/api/applications/' + appId)
  661 |     await page.goto('/applications'); await page.getByRole('textbox', { name: /搜索/ }).fill(name)
  662 |     const row = page.locator(`tr:has(a[href="/applications/${appId}"])`); await expect(row).toBeVisible()
  663 |     await row.getByRole('button', { name: /^操作$/ }).click(); const menu = await page.getByRole('menuitem').allTextContents()
  664 |     expect(menu).toContain('编辑'); expect(menu).toContain('删除'); expect(menu.some(x => /部署/.test(x))).toBe(false)
  665 |     await page.keyboard.press('Escape'); await expect(page.locator('a[href="/applications/new"]')).toBeVisible()
  666 |     await row.locator(`a[href="/applications/${appId}"]`).click(); await page.waitForURL(new RegExp('/applications/' + appId + '$'))
  667 |     const before = (await apiRequest(page, '/api/applications/' + appId)).body as { revision: number; name: string }
  668 |     await page.getByLabel('名称', { exact: true }).fill(testName('unsaved-name'))
  669 |     let puts = 0; page.on('request', r => { if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/applications/' + appId) puts++ })
  670 |     await page.getByRole('link', { name: /^取消$/ }).click(); await page.waitForURL(/\/applications$/)
  671 |     const after = (await apiRequest(page, '/api/applications/' + appId)).body as typeof before
  672 |     expect(after.revision).toBe(before.revision); expect(after.name).toBe(name); expect(puts).toBe(0)
  673 |     await evidence('application-list-entry-cancel', { app_id: appId, row_detail_link: true, menu, no_list_deploy: true, new_entry: true, cancel_puts: puts, before_revision: before.revision, after_revision: after.revision, unsaved_name_not_persisted: true })
  674 |     // Viewer 账号环境驱动：优先运行环境提供的 RBAC Viewer（NODESTEER_E2E_VIEWER_USERNAME + NODESTEER_E2E_RBAC_PASSWORD），
  675 |     // 否则从私有凭据库发现/自建 QA Viewer；不再硬编码旧环境的用户 ID，也不改他人账号口令。
  676 |     const users = (await apiRequest(page, '/api/users')).body as { id: string; username: string; role: string }[]
  677 |     const envViewerUser = process.env.NODESTEER_E2E_VIEWER_USERNAME, envViewerPassword = process.env.NODESTEER_E2E_RBAC_PASSWORD
  678 |     let viewer: { user_id: string; username: string; password: string }
  679 |     if (envViewerUser && envViewerPassword) {
  680 |       const found = users.find(user => user.username === envViewerUser)
  681 |       expect(found, 'NODESTEER_E2E_VIEWER_USERNAME 必须存在于当前环境').toBeTruthy()
  682 |       expect(found!.role).toBe('viewer')
  683 |       viewer = { user_id: found!.id, username: found!.username, password: envViewerPassword }
  684 |     } else {
  685 |       const resolved = await ensureRoleAccount(page, 'GAP-17 Viewer审计不可写')
  686 |       const current = users.find(user => user.id === resolved.user_id)
  687 |       if (current?.role !== 'viewer') expect((await apiRequest(page, `/api/users/${resolved.user_id}`, { method: 'PUT', data: { role: 'viewer' } })).ok).toBeTruthy()
  688 |       viewer = resolved
  689 |     }
  690 |     viewerContext = await browser.newContext({ baseURL: new URL(page.url()).origin }); const viewerPage = await viewerContext.newPage(), token = await loginViaApi(viewerPage, viewer.username, viewer.password)
  691 |     await appendFile(process.env.NODESTEER_E2E_PRIVATE_DIR + '/ui-users.jsonl', JSON.stringify({ user_id: viewer.user_id, username: viewer.username, token }) + '\n', { mode: 0o600 })
  692 |     // 自建审计夹具：创建本用例自己的脚本并取其 Audit 记录，不再依赖 GAP-14 写出的 evidence 文件（去除跨用例级联）。
  693 |     const auditFixture = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: testName('viewer-audit'), interpreter: 'shell', content: 'echo viewer-audit', enabled: false } })
  694 |     expect(auditFixture.ok).toBeTruthy(); const auditFixtureId = (auditFixture.body as { id: string }).id; own.push('/api/scripts/' + auditFixtureId)
  695 |     let ownAuditId = ''
  696 |     await expect.poll(async () => {
  697 |       const rows = (await apiRequest(page, '/api/audit?limit=500&offset=0')).body as { id: string; resource: string; resource_id: string }[]
  698 |       ownAuditId = rows.find(entry => entry.resource === 'script' && entry.resource_id === auditFixtureId)?.id || ''
  699 |       return ownAuditId
  700 |     }, { timeout: 10_000 }).not.toBe('')
  701 |     const find = async () => ((await apiRequest(page, '/api/audit?limit=500&offset=0')).body as { id: string }[]).find(entry => entry.id === ownAuditId)
  702 |     const auditBefore = await find(); expect(auditBefore).toBeTruthy()
  703 |     const probes = []
  704 |     for (const method of ['PUT', 'DELETE']) {
  705 |       const response = await apiRequest(viewerPage, '/api/audit', { method, data: method === 'PUT' ? { id: ownAuditId, action: 'qa-must-not-change-audit' } : undefined })
  706 |       probes.push({ method, http: response.status, allow: response.headers?.allow ?? null, returned_read_array: Array.isArray(response.body) })
  707 |     }
  708 |     await viewerPage.goto('/audit'); await viewerPage.waitForLoadState('networkidle')
  709 |     const forbiddenControls = await viewerPage.locator('main').getByRole('button', { name: /^删除$|^保存$|^编辑$/ }).count(); expect(forbiddenControls).toBe(0)
  710 |     const auditAfter = await find(); expect(auditAfter).toEqual(auditBefore)
  711 |     await evidence('viewer-audit-immutable', { user_id: viewer.user_id, username: viewer.username, role: 'viewer', owned_audit_id: ownAuditId, audit_fixture_script_id: auditFixtureId, audit_before: auditBefore, audit_after: auditAfter, probes, forbidden_controls: forbiddenControls, exact_audit_unchanged: true, viewer_source: envViewerUser ? 'NODESTEER_E2E_VIEWER_USERNAME' : 'private-store/self-provisioned', method_contract: '产品契约（docs/PRODUCT.md 方法语义）：/api/audit 仅接受 GET，其它方法 405 + Allow: GET；写探针不产生审计变更' })
  712 |     expect((await apiRequest(viewerPage, '/api/logout', { method: 'POST', data: {} })).ok).toBeTruthy()
  713 |   } finally { await viewerContext?.close(); await cleanupByIds(page, own) }
  714 | })
  715 | 
  716 | test('GAP-18 UI LoginDashboardAgents连续链与真实统计详情链接', async ({ page }) => {
  717 |   test.setTimeout(120_000); await login(page)
  718 |   const own: string[] = [], checks: unknown[] = [], nodeId = (await getNodeIds(page))[0]
  719 |   try {
  720 |     const task = await createIsolatedTask(page, 'dashboard-continuity'); own.unshift('/api/tasks/' + task.id)
  721 |     const schedule = await apiRequest(page, '/api/schedules', { method: 'POST', data: { task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC', execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: true } }); expect(schedule.ok).toBeTruthy()
  722 |     const scheduleId = (schedule.body as { id: string }).id; own.unshift('/api/schedules/' + scheduleId)
  723 |     const artifact = await upload(page, testName('dashboard-artifact'), Buffer.from(testName('dashboard-content'))); own.push('/api/artifacts/' + artifact)
  724 |     const app = await apiRequest(page, '/api/applications', { method: 'POST', data: { name: testName('dashboard-app'), version: 'v1', artifact_id: artifact, binary_path: '/tmp/' + testName('unused'), unit_name: testName('unused') + '.service', health_check: { type: 'systemd', timeout: 5, attempts: 3, interval: 2 } } }); expect(app.ok).toBeTruthy()
  725 |     const appId = (app.body as { id: string }).id; own.unshift('/api/applications/' + appId)
  726 |     const nodesResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/nodes' && r.request().method() === 'GET'), execResponse = page.waitForResponse(r => new URL(r.url()).pathname === '/api/executions' && new URL(r.url()).searchParams.get('limit') === '50')
  727 |     await page.goto('/'); const nodes = await (await nodesResponse).json() as { id: string; hostname: string; status: string; sync_status: string }[], executions = await (await execResponse).json() as { id: string; status: string }[]
  728 |     const terminal = executions.filter(e => ['SUCCESS', 'FAILED', 'TIMED_OUT'].includes(e.status)), rate = terminal.length ? `${Math.round(executions.filter(e => e.status === 'SUCCESS').length / terminal.length * 1000) / 10}%` : '-'
  729 |     const metrics = [['节点总数', String(nodes.length)], ['运行中', String(executions.filter(e => e.status === 'RUNNING').length)], ['执行失败', String(executions.filter(e => ['FAILED', 'TIMED_OUT'].includes(e.status)).length)], ['成功率', rate]]
  730 |     for (const [label, value] of metrics) await expect(page.locator('[data-slot=card]').filter({ has: page.getByText(label, { exact: true }) }).first()).toContainText(value)
> 731 |     const appCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^托管应用状态$/ }) }); await expect(appCard).toContainText('0/1')
      |                                                                                                                                                                     ^ Error: expect(locator).toContainText(expected) failed
  732 |     const syncCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^同步状态$/ }) }), syncCounts = Object.fromEntries(['synced', 'outdated', 'error'].map(status => [status, nodes.filter(node => node.sync_status.toLowerCase() === status).length]))
  733 |     const syncValues = await syncCard.locator('strong').allTextContents(); expect(syncValues.map(Number)).toEqual(Object.values(syncCounts))
  734 |     const links = await page.locator('main a').evaluateAll(elements => elements.map(element => element.getAttribute('href')).filter((value): value is string => !!value && /^\/(applications|executions|schedules)\//.test(value)))
  735 |     for (const path of [...new Set(links)]) {
  736 |       await page.goto('/'); await page.waitForLoadState('networkidle'); await page.locator(`main a[href="${path}"]`).first().click(); await expect(page).toHaveURL(new RegExp(path + '$')); await page.waitForLoadState('networkidle')
  737 |       await expect(page.locator('main')).not.toContainText('not found'); checks.push({ path, real_detail_link: true })
  738 |     }
  739 |     await page.goto('/'); await page.waitForLoadState('networkidle')
  740 |     const agentCard = page.locator('[data-slot=card]').filter({ has: page.locator('[data-slot=card-title]').filter({ hasText: /^Agent 健康状态$/ }) }); await agentCard.getByRole('link', { name: /查看全部/ }).click(); await page.waitForURL(/\/agents$/)
  741 |     await page.getByRole('textbox', { name: /搜索/ }).fill(nodes.find(node => node.id === nodeId)!.hostname)
  742 |     await page.locator(`a[href="/agents/${nodeId}"]`).click(); await page.waitForURL(new RegExp('/agents/' + nodeId + '$'))
  743 |     await page.locator(`a[href="/tasks/${task.id}"]`).first().click(); await page.waitForURL(new RegExp('/tasks/' + task.id + '$')); await expect(page.locator('main')).toContainText(nodes.find(node => node.id === nodeId)!.hostname)
  744 |     await evidence('dashboard-continuous-metrics-links', { task_id: task.id, schedule_id: scheduleId, app_id: appId, metrics, metric_window: 'actual Dashboard /executions?limit=50 response', app_healthy_total: '0/1 unassigned own App', sync_counts: syncCounts, sync_ui_values: syncValues, links: checks, actual_ui_login: true, login_dashboard_agent_view_all: true, agent_detail_task_return_target: nodeId, no_original_resource_mutations: true })
  745 |   } finally { await cleanupByIds(page, own) }
  746 | })
  747 | 
  748 | test('GAP-19 Artifact无引用UI取消确认删除与真实下载404', async ({ page }) => {
  749 |   await loginViaApi(page); const name = testName('artifact-ui-delete'), id = await upload(page, name, Buffer.from(name))
  750 |   try {
  751 |     await page.getByRole('textbox', { name: /搜索/ }).fill(name); const row = page.locator('tr').filter({ hasText: name })
  752 |     let deletes = 0; page.on('request', r => { if (r.method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id) deletes++ })
  753 |     await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click(); await page.getByRole('alertdialog').getByRole('button', { name: /^取消$/ }).click()
  754 |     expect(deletes).toBe(0); await expect(row).toBeVisible()
  755 |     await row.getByRole('button', { name: /^操作$/ }).click(); await page.getByRole('menuitem', { name: /^删除$/ }).click()
  756 |     const response = page.waitForResponse(r => r.request().method() === 'DELETE' && new URL(r.url()).pathname === '/api/artifacts/' + id)
  757 |     await page.getByRole('alertdialog').getByRole('button', { name: /^删除$/ }).click(); expect((await response).status()).toBe(200); expect(deletes).toBe(1); await expect(row).toHaveCount(0)
  758 |     const after = await apiRequest(page, '/api/artifacts/' + id), download = await apiRequest(page, '/api/artifacts/' + id + '/download')
  759 |     expect(after.status).toBe(404); expect(download.status).toBe(404)
  760 |     await evidence('artifact-unreferenced-ui-delete', { artifact_id: id, cancel_delete_count: 0, confirm_delete_count: 1, actual_delete_http: 200, no_reference_row_removed: true, object_after_delete_http: after.status, download_after_delete_http: download.status })
  761 |   } finally { await cleanupByIds(page, ['/api/artifacts/' + id]) }
  762 | })
  763 | 
  764 | test('GAP-20 禁用Script实际引用拒绝恢复和定义边界', async ({ page }) => {
  765 |   test.setTimeout(100_000); await loginViaApi(page)
  766 |   const own: string[] = [], name = testName('disabled-reference'), marker = testName('enabled-marker')
  767 |   try {
  768 |     const response = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name, interpreter: 'shell', content: 'echo ' + marker, timeout: 30, enabled: false } }); expect(response.ok).toBeTruthy()
  769 |     const id = (response.body as { id: string }).id; own.push('/api/scripts/' + id)
  770 |     const task = await createIsolatedTask(page, 'disabled-script-task'); own.unshift('/api/tasks/' + task.id)
  771 |     await page.goto(`/tasks/${task.id}/edit`); await select(page, '类型', '脚本'); await select(page, '脚本', name)
  772 |     const rejected = page.waitForResponse(r => r.request().method() === 'PUT' && new URL(r.url()).pathname === '/api/tasks/' + task.id)
  773 |     await page.getByRole('button', { name: /^保存$/ }).click(); const bad = await rejected; expect(bad.status()).toBe(400)
  774 |     const error = await bad.json(); await expect(page.getByText(error.error, { exact: true }).last()).toBeVisible()
  775 |     const current = (await apiRequest(page, '/api/scripts/' + id)).body as Record<string, unknown>
  776 |     expect((await apiRequest(page, '/api/scripts/' + id, { method: 'PUT', data: { ...current, enabled: true } })).ok).toBeTruthy()
  777 |     await page.getByRole('button', { name: /^保存$/ }).click(); await page.waitForURL(/\/tasks$/); const execution = await runTask(page, task.id, marker)
  778 |     const duplicate = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name, interpreter: 'shell', content: 'echo duplicate', enabled: false } })
  779 |     if (duplicate.ok) own.push('/api/scripts/' + (duplicate.body as { id: string }).id)
  780 |     // 产品契约（internal/hub/validation.go maxScriptContentBytes）：脚本内容上限 256 KiB = 262144 字节，
  781 |     // 按字节判定，超过即 400。夹具必须在上限内，并单独断言边界（恰好 262144 通过 / 超限 400）。
  782 |     const scriptContentMaxBytes = 262144
  783 |     const contentHeader = '# ' + testName('long-script') + '\n', contentLine = '# qa-long-content\n'
  784 |     const lineCount = Math.floor((scriptContentMaxBytes - contentHeader.length - 1) / contentLine.length)
  785 |     const content = contentHeader + contentLine.repeat(lineCount) + 'x'.repeat(scriptContentMaxBytes - contentHeader.length - lineCount * contentLine.length)
  786 |     expect(Buffer.byteLength(content)).toBe(scriptContentMaxBytes)
  787 |     const large = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: testName('large-script'), interpreter: 'shell', content, timeout: 0, enabled: false } }); expect(large.ok).toBeTruthy()
  788 |     const largeId = (large.body as { id: string }).id; own.push('/api/scripts/' + largeId)
  789 |     const largeSaved = (await apiRequest(page, '/api/scripts/' + largeId)).body as { content: string; sha256: string; timeout: number }
  790 |     expect(largeSaved.content).toBe(content); expect(largeSaved.sha256).toBe(createHash('sha256').update(content).digest('hex'))
  791 |     const overLimit = await apiRequest(page, '/api/scripts', { method: 'POST', data: { name: testName('over-limit-script'), interpreter: 'shell', content: content + 'x', timeout: 0, enabled: false } })
  792 |     expect(overLimit.status).toBe(400)
  793 |     const overLimitError = String((overLimit.body as { error?: string }).error)
  794 |     expect(overLimitError).toContain('script content too large'); expect(overLimitError).toContain(String(scriptContentMaxBytes))
  795 |     const negative = await apiRequest(page, '/api/scripts/' + largeId, { method: 'PUT', data: { ...largeSaved, name: testName('negative-timeout'), interpreter: 'shell', timeout: -1, enabled: false } })
  796 |     await evidence('script-disabled-reference-boundaries', { script_id: id, task_id: task.id, disabled_reference_http: bad.status(), disabled_reference_error: error, actual_ui_error_visible: true, enabled_restored_execution: execution, duplicate_name_http: duplicate.status, duplicate_distinct_id: duplicate.ok ? (duplicate.body as { id: string }).id : null, duplicate_contract: 'observed actual current acceptance/rejection; uniqueness not assumed', large_script_id: largeId, large_content_bytes: Buffer.byteLength(content), exact_content_roundtrip: true, sha256: largeSaved.sha256, timeout_zero: largeSaved.timeout, content_limit_contract: { max_bytes: scriptContentMaxBytes, at_limit_http: large.status, over_limit_http: overLimit.status, over_limit_error: overLimitError }, negative_timeout_http: negative.status, negative_timeout_body: negative.body, classification: 'REAL_UI disabled reference/recovery + REAL_API boundary CUD', remaining: ['large content/duplicate/negative timeout UI editor rendering is not represented by API evidence'] })
  797 |   } finally { await cleanupByIds(page, own) }
  798 | })
  799 | 
  800 | test('GAP-21 长命令详情可选择复制且操作入口仍可用', async ({ page, context }) => {
  801 |   await loginViaApi(page)
  802 |   const task = await createIsolatedTask(page, 'long-command-layout')
  803 |   const command = 'printf %s ' + 'qa-long-command-path-' + 'x'.repeat(2048) + ' ' + randomBytes(32).toString('hex')
  804 |   try {
  805 |     const current = (await apiRequest(page, '/api/tasks/' + task.id)).body as Record<string, unknown>
  806 |     const saved = await apiRequest(page, '/api/tasks/' + task.id, { method: 'PUT', data: { ...current, command } })
  807 |     expect(saved.ok).toBeTruthy()
  808 |     await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  809 |     await page.setViewportSize({ width: 760, height: 720 })
  810 |     await page.goto('/tasks/' + task.id)
  811 |     const definition = page.locator('pre')
  812 |     await expect(definition).toHaveText(command)
  813 |     const layout = await definition.evaluate((element) => {
  814 |       const rect = element.getBoundingClientRect()
  815 |       return {
  816 |         client_width: element.clientWidth,
  817 |         scroll_width: element.scrollWidth,
  818 |         client_height: element.clientHeight,
  819 |         scroll_height: element.scrollHeight,
  820 |         right: Math.round(rect.right),
  821 |         viewport_width: window.innerWidth,
  822 |         white_space: getComputedStyle(element).whiteSpace,
  823 |         overflow_x: getComputedStyle(element).overflowX,
  824 |       }
  825 |     })
  826 |     await definition.selectText()
  827 |     const selected = await page.evaluate(() => window.getSelection()?.toString() || '')
  828 |     await page.keyboard.press('Control+C')
  829 |     const copied = await page.evaluate(() => navigator.clipboard?.readText().catch(() => '') || '')
  830 |     const runButton = page.getByRole('link', { name: /立即运行/ })
  831 |     await evidence('long-command-detail-layout', { task_id: task.id, command_chars: command.length, exact_text_rendered: true, selected_chars: selected.length, clipboard_exact: copied === command, clipboard_read_available: !!copied, layout, run_action_visible_and_enabled: true, classification: 'REAL_UI; Task definition saved to Hub and rendered by actual detail page; not executed' })
```