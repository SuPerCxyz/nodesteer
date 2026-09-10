import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P07 脚本列表', () => {
  test('P07-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.scripts)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P07-02 新建脚本入口', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.scripts)
    await page.waitForTimeout(2000)
    const createBtn = page.getByRole('link', { name: /create|新建|add/i }).first()
    await expect(createBtn).toBeVisible()
    await createBtn.click()
    await page.waitForURL(/\/scripts\/new/, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/scripts\/new/)
  })

  test('P07-04 启用/禁用', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.scripts)
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

test.describe('P08 脚本编辑器', () => {
  test('P08-01 新建脚本保存', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/scripts/new')
    await page.waitForTimeout(1000)
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
    await nameInput.fill('test-e2e-script')
    const contentArea = page.locator('textarea, [role="textbox"]').first()
    await expect(contentArea).toBeVisible()
    await contentArea.fill('#!/bin/bash\necho hello')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(3000)
  })

  test('P08-08 取消编辑', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.scripts)
    await page.waitForTimeout(2000)
    const nameLink = page.locator('table tbody tr a').first()
    await expect(nameLink).toBeVisible()
    await nameLink.click()
    await page.waitForTimeout(1500)
    const cancelBtn = page.getByRole('link', { name: /cancel|取消/i }).first()
    await expect(cancelBtn).toBeVisible()
    await cancelBtn.click()
    await page.waitForURL(TEST_URLS.scripts, { timeout: 10_000 })
  })
})
