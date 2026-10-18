import { test, expect } from 'playwright/test'
import { apiRequest, captureCreated, cleanupByIds, createIsolatedTask, loginViaApi, TEST_URLS } from './fixtures'

const TEST_CRON = '0 0 1 1 *' // 远未来触发；只使用本轮任务和精确创建ID。

test.describe('P24 独立调度与任务联动', () => {
  test('P24-01 独立深链刷新与旧入口重定向', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.schedules)
    await expect(page.getByRole('heading', { name: /schedules|调度/i }).first()).toBeVisible()
    await page.reload()
    await expect(page).toHaveURL(/\/schedules$/)
    await page.goto('/tasks?view=schedules')
    await expect(page).toHaveURL(/\/schedules$/)
    await expect(page.getByRole('heading', { name: /schedules|调度/i }).first()).toBeVisible()
  })

  test('P24-02 调度新建、禁用、删除取消与确认', async ({ page }) => {
    await loginViaApi(page)
    const task = await createIsolatedTask(page, 'p2402')
    let scheduleId: string | undefined
    try {
      await page.goto(TEST_URLS.schedules)
      await page.getByRole('link', { name: /新建调度|new schedule/i }).click()
      await page.getByRole('combobox', { name: /^任务$|^task$/i }).click()
      await page.getByRole('option', { name: task.name, exact: true }).click()
      await page.getByRole('textbox', { name: /cron 表达式|cron expression/i }).fill(TEST_CRON)
      scheduleId = await captureCreated(page, '/api/schedules', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      await page.waitForURL(/\/schedules$/)
      const row = page.locator(`tr:has(a[href="/schedules/${scheduleId}"])`)
      await expect(row).toBeVisible()
      await row.getByRole('button', { name: /操作|actions/i }).click()
      await page.getByRole('menuitem', { name: /禁用|disable/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/schedules/${scheduleId}`)).body).toMatchObject({ enabled: false })
      await row.getByRole('button', { name: /操作|actions/i }).click()
      await page.getByRole('menuitem', { name: /删除|delete/i }).click()
      let dialog = page.getByRole('alertdialog')
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: /^取消$|^cancel$/i }).click()
      await expect(row).toBeVisible()
      await row.getByRole('button', { name: /操作|actions/i }).click()
      await page.getByRole('menuitem', { name: /删除|delete/i }).click()
      dialog = page.getByRole('alertdialog')
      const response = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/schedules/${scheduleId}` && r.request().method() === 'DELETE')
      await dialog.getByRole('button', { name: /^删除$|^delete$/i }).click()
      expect((await response).ok()).toBeTruthy()
      await expect(row).toHaveCount(0)
    } finally {
      if (scheduleId) await cleanupByIds(page, [`/api/schedules/${scheduleId}`])
      await cleanupByIds(page, [`/api/tasks/${task.id}`])
    }
  })

  test('P24-03 仪表盘即将执行调度查看全部落点', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    const card = page.locator('[data-slot=card]').filter({ hasText: /即将执行的调度|upcoming schedules/i }).first()
    await card.getByRole('link', { name: /查看全部|view all/i }).click()
    await expect(page).toHaveURL(/\/schedules$/)
    await expect(page.getByRole('heading', { name: /调度|schedules/i }).first()).toBeVisible()
  })
})
