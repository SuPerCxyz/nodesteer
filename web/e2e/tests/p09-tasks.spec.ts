import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P09 任务列表', () => {
  test('P09-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.tasks)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P09-02 点击名称进编辑器', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.tasks)
    await page.waitForTimeout(2000)
    const nameLink = page.locator('table tbody tr a').first()
    await expect(nameLink).toBeVisible()
    await nameLink.click()
    await page.waitForURL(/\/tasks\//, { timeout: 10_000 })
  })

  test('P09-04 启用/禁用', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.tasks)
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

test.describe('P10 任务编辑器', () => {
  test('P10-01 新建 command 任务', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/tasks/new')
    await page.waitForTimeout(1000)
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
    await nameInput.fill('test-e2e-task')
    const commandInput = page.getByRole('textbox', { name: /command|命令/i })
    await expect(commandInput).toBeVisible()
    await commandInput.fill('echo hello')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(3000)
  })
})

test.describe('P11 立即运行', () => {
  test('P11-01 立即运行入口', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.tasks)
    await page.waitForTimeout(2000)
    const runLink = page.getByRole('link', { name: /run|运行/i }).first()
    await expect(runLink).toBeVisible()
    await runLink.click()
    await page.waitForURL(/\/tasks\/.*\/run/, { timeout: 10_000 })
  })
})
