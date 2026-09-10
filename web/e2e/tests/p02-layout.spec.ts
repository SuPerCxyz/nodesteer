import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P02 布局与导航', () => {
  test('P02-01 侧栏导航项', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.waitForTimeout(2000)
    const navItems = [
      { name: /nodes|节点/i, url: '/agents' },
      { name: /scripts|脚本/i, url: '/scripts' },
      { name: /tasks|任务/i, url: '/tasks' },
      { name: /schedules|调度/i, url: '/schedules' },
      { name: /applications|托管应用/i, url: '/applications' },
      { name: /executions|执行/i, url: '/executions' },
    ]
    for (const item of navItems) {
      const link = page.getByRole('link', { name: item.name }).first()
      await expect(link).toBeVisible()
      await link.click()
      await page.waitForURL(new RegExp(item.url), { timeout: 10_000 })
      await expect(page).toHaveURL(new RegExp(item.url))
    }
  })

  test('P02-04 token 过期跳登录', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.evaluate(() => localStorage.setItem('cadentra_token', 'invalid-token'))
    await page.goto(TEST_URLS.nodes)
    await page.waitForURL(/\/sign-in(?:\?.*)?$/, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/sign-in(?:\?.*)?$/)
  })
})
