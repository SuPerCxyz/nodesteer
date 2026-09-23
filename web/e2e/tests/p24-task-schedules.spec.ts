import { test, expect } from 'playwright/test'
import { apiRequest, loginViaApi, TEST_URLS } from './fixtures'

/**
 * P24 任务页「任务 | 调度」合并视图（2026-09 合并改造）
 * - 调度视图通过 /tasks?view=schedules 深链访问，刷新保持
 * - 旧路由 /schedules 重定向到合并视图
 * - 合并视图内可完成调度的新建 / 启用禁用 / 删除
 */
const TEST_CRON = '0 0 1 1 *' // 每年 1 月 1 日 00:00（后端要求 5 段 cron），测试期间不会真实触发

test.describe('P24 任务与调度合并视图', () => {
  test('P24-01 深链刷新保持与视图切换', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.schedules)
    await page.waitForTimeout(1500)
    await expect(page.getByRole('tab', { name: /调度/ })).toHaveAttribute('data-state', 'active')
    await page.reload()
    await page.waitForTimeout(1500)
    await expect(page.getByRole('tab', { name: /调度/ })).toHaveAttribute('data-state', 'active')
    await expect(page).toHaveURL(/view=schedules/)

    await page.getByRole('tab', { name: /任务/ }).click()
    await page.waitForTimeout(800)
    await expect(page).toHaveURL(/\/tasks(\?.*)?$/)
    await expect(page).not.toHaveURL(/view=schedules/)

    await page.goto('/schedules')
    await page.waitForURL(/\/tasks\?view=schedules/, { timeout: 10_000 })
    await expect(page.getByRole('tab', { name: /调度/ })).toHaveAttribute('data-state', 'active')
  })

  test('P24-02 调度视图内新建、禁用、删除调度', async ({ page }) => {
    await loginViaApi(page)
    const tasks = await apiRequest(page, '/api/tasks')
    const list = (Array.isArray(tasks.body) ? tasks.body : (tasks.body as { items?: unknown[] }).items || []) as {
      id: string
      name: string
    }[]
    test.skip(!list.length, '需要至少一个任务用于创建调度')
    const task = list[0]
    let scheduleId: string | null = null

    try {
      await page.goto(TEST_URLS.schedules)
      await page.waitForTimeout(1500)
      await page.getByRole('link', { name: /新建调度|new schedule/i }).click()
      await page.waitForURL(/\/schedules\/new/, { timeout: 10_000 })

      await page.getByRole('combobox').first().click()
      await page.getByRole('option', { name: task.name }).first().click()
      await page.getByLabel(/表达式|expression/i).fill(TEST_CRON)
      await page.getByRole('button', { name: /保存|save/i }).click()
      await page.waitForURL(/view=schedules/, { timeout: 15_000 })

      const created = page.locator('table tbody tr', { hasText: TEST_CRON }).first()
      await expect(created).toBeVisible()
      const href = await created.locator('a[href^="/schedules/"]').first().getAttribute('href')
      scheduleId = href?.split('/').pop() || null
      expect(scheduleId).toBeTruthy()
      const row = page.locator(`table tbody tr:has(a[href="/schedules/${scheduleId}"])`)

      // 禁用
      await row.getByRole('button').last().click()
      await page.getByRole('menuitem', { name: /禁用|disable/i }).click()
      await page.waitForTimeout(1500)
      await expect(row).toBeVisible()

      // 删除（确认框）
      await row.getByRole('button').last().click()
      await page.getByRole('menuitem', { name: /删除|delete/i }).click()
      const dialog = page.locator('[role=dialog], [role=alertdialog]')
      await expect(dialog).toBeVisible()
      await dialog.getByRole('button', { name: /^(删除|delete|确认|confirm)$/i }).click()
      await page.waitForTimeout(2000)
      await expect(row).toHaveCount(0)
      scheduleId = null
    } finally {
      if (scheduleId) await apiRequest(page, `/api/schedules/${scheduleId}`, { method: 'DELETE' })
    }
  })

  test('P24-03 仪表盘「即将执行的调度」落点', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.waitForTimeout(2000)
    const card = page
      .locator('[data-slot=card]')
      .filter({ hasText: /即将执行的调度|upcoming schedules/i })
      .first()
    const link = card.getByRole('link', { name: /查看全部|view all/i }).first()
    await link.click()
    await page.waitForURL(/view=schedules/, { timeout: 10_000 })
    await expect(page.getByRole('tab', { name: /调度/ })).toHaveAttribute('data-state', 'active')
  })
})
