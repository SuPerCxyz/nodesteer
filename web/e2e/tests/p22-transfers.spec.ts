import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P22 文件传输', () => {
  test('P22-01 传输页面渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.transfers)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })
})
