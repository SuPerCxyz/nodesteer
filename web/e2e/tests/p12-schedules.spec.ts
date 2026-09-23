import { test, expect } from 'playwright/test'
import { apiRequest, deleteSchedulesByExpression, loginViaApi, TEST_URLS } from './fixtures'

test.describe('P12 调度列表', () => {
  test('P12-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.schedules)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P12-04 启用/禁用', async ({ page }) => {
    await loginViaApi(page)
    // 自建调度用于切换（列表可能为空），结束后清理
    const tasks = await apiRequest(page, '/api/tasks')
    const taskList = (Array.isArray(tasks.body)
      ? tasks.body
      : (tasks.body as { items?: unknown[] })?.items || []) as { id: string }[]
    test.skip(!taskList.length, '需要至少一个任务')
    const created = await apiRequest(page, '/api/schedules', {
      method: 'POST',
      data: {
        task_id: taskList[0].id,
        type: 'cron',
        expression: '0 3 * * *',
        timezone: 'UTC',
        execution_owner: 'agent',
        offline_policy: 'allow_offline',
        misfire_policy: 'run_once',
        enabled: true,
      },
    })
    const scheduleId = (created.body as { id?: string })?.id
    try {
      await page.goto(TEST_URLS.schedules)
      await page.waitForTimeout(2000)
      const row = page.locator(`table tbody tr:has(a[href="/schedules/${scheduleId}"])`)
      await expect(row).toBeVisible()
      const actionMenu = row.getByRole('button').last()
      await actionMenu.click()
      const toggleItem = page.getByRole('menuitem', { name: /enable|disable|启用|禁用/i }).first()
      await expect(toggleItem).toBeVisible()
      const textBefore = await toggleItem.textContent()
      await toggleItem.click()
      await page.waitForTimeout(1200)
      await row.getByRole('button').last().click()
      const textAfter = await page
        .getByRole('menuitem', { name: /enable|disable|启用|禁用/i })
        .first()
        .textContent()
      expect(textAfter).not.toEqual(textBefore)
    } finally {
      if (scheduleId) await apiRequest(page, `/api/schedules/${scheduleId}`, { method: 'DELETE' })
    }
  })
})

test.describe('P13 调度编辑器', () => {
  test('P13-01 新建 cron 调度', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/schedules/new')
    await page.waitForTimeout(1000)
    const taskSelect = page.getByRole('combobox', { name: '任务', exact: true })
    await expect(taskSelect).toBeVisible()
    await taskSelect.click()
    await page.getByRole('option').first().click()
    const exprInput = page.getByRole('textbox', { name: /cron 表达式|cron expression/i })
    await expect(exprInput).toBeVisible()
    await exprInput.fill('*/5 * * * *')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(3000)
    // 清理：删除本用例创建的调度，避免持续触发与污染环境
    await deleteSchedulesByExpression(page, '*/5 * * * *')
  })
})
