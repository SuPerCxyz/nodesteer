import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P14 托管应用列表', () => {
  test('P14-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.applications)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })
})

test.describe('P15 托管应用编辑器', () => {
  test('P15-01 新建托管应用入口', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/applications/new')
    await page.waitForTimeout(1000)
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
  })
})
