import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P06 分组', () => {
  test('P06-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.groups)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P06-02 新建分组入口', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.groups)
    await page.waitForTimeout(2000)
    const createBtn = page.getByRole('link', { name: /create|新建|add/i }).first()
    await expect(createBtn).toBeVisible()
    await createBtn.click()
    await page.waitForURL(/\/groups\/new/, { timeout: 10_000 })
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
  })

  test('P06-06 取消新建', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.groups)
    await page.waitForTimeout(2000)
    const createBtn = page.getByRole('link', { name: /create|新建|add/i }).first()
    await expect(createBtn).toBeVisible()
    await createBtn.click()
    await page.waitForURL(/\/groups\/new/, { timeout: 10_000 })
    const cancelBtn = page.getByRole('link', { name: /cancel|取消/i }).first()
    await expect(cancelBtn).toBeVisible()
    await cancelBtn.click()
    await page.waitForURL(TEST_URLS.groups, { timeout: 10_000 })
  })
})
