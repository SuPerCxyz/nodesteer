import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

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
    await page.goto(TEST_URLS.schedules)
    await page.waitForTimeout(2000)
    const actionMenu = page.getByRole('button', { name: /actions|操作/i }).first()
    await expect(actionMenu).toBeVisible()
    await actionMenu.click()
    const toggleItem = page.getByRole('menuitem', { name: /enable|disable|启用|禁用/i }).first()
    await expect(toggleItem).toBeVisible()
    const textBefore = await toggleItem.textContent()
    await toggleItem.click()
    await page.waitForTimeout(1000)
    await actionMenu.click()
    const textAfter = await page.getByRole('menuitem', { name: /enable|disable|启用|禁用/i }).first().textContent()
    expect(textAfter).not.toEqual(textBefore)
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
  })
})
