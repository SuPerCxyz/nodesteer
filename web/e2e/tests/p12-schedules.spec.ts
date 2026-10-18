import { test, expect } from 'playwright/test'
import { apiRequest, captureCreated, cleanupByIds, createIsolatedTask, loginViaApi, TEST_URLS } from './fixtures'

test.describe('P12 调度列表', () => {
  test('P12-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.schedules)
    await expect(page.getByRole('heading', { name: /调度|schedules/i }).first()).toBeVisible()
  })

  test('P12-04 本轮调度启用/禁用', async ({ page }) => {
    await loginViaApi(page)
    const task = await createIsolatedTask(page, 'p1204')
    let id: string | undefined
    try {
      const created = await apiRequest(page, '/api/schedules', { method: 'POST', data: {
        task_id: task.id, type: 'cron', expression: '0 0 1 1 *', timezone: 'UTC',
        execution_owner: 'agent', offline_policy: 'allow_offline', misfire_policy: 'skip', enabled: false,
      } })
      expect(created.ok).toBeTruthy()
      id = (created.body as { id: string }).id
      await page.goto(TEST_URLS.schedules)
      const row = page.locator(`tr:has(a[href="/schedules/${id}"])`)
      await expect(row).toBeVisible()
      await row.getByRole('button', { name: /操作|actions/i }).click()
      await page.getByRole('menuitem', { name: /^启用$|^enable$/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/schedules/${id}`)).body).toMatchObject({ enabled: true })
      await row.getByRole('button', { name: /操作|actions/i }).click()
      await page.getByRole('menuitem', { name: /^禁用$|^disable$/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/schedules/${id}`)).body).toMatchObject({ enabled: false })
    } finally {
      if (id) await cleanupByIds(page, [`/api/schedules/${id}`])
      await cleanupByIds(page, [`/api/tasks/${task.id}`])
    }
  })
})

test.describe('P13 调度编辑器', () => {
  test('P13-01 UI新建cron调度', async ({ page }) => {
    await loginViaApi(page)
    const task = await createIsolatedTask(page, 'p1301')
    let id: string | undefined
    try {
      await page.goto('/schedules/new')
      await page.getByRole('combobox', { name: /^任务$|^task$/i }).click()
      await page.getByRole('option', { name: task.name, exact: true }).click()
      await page.getByRole('textbox', { name: /cron 表达式|cron expression/i }).fill('0 0 1 1 *')
      id = await captureCreated(page, '/api/schedules', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      await page.waitForURL(/\/schedules$/)
      await expect(page.locator(`a[href="/schedules/${id}"]`)).toBeVisible()
    } finally {
      if (id) await cleanupByIds(page, [`/api/schedules/${id}`])
      await cleanupByIds(page, [`/api/tasks/${task.id}`])
    }
  })
})
