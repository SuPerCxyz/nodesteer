import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P02 布局与导航', () => {
  test('P02-01 侧栏导航项', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.waitForTimeout(2000)
    const sidebar = page.locator('[data-slot=sidebar]').first()
    const navItems = [
      { name: /^节点$/, url: '/agents' },
      { name: /^脚本$/, url: '/scripts' },
      { name: /^任务$/, url: '/tasks' },
      { name: /^应用$/, url: '/applications' },
      { name: /^执行$/, url: '/executions' },
    ]
    for (const item of navItems) {
      const link = sidebar.getByRole('link', { name: item.name }).first()
      await expect(link).toBeVisible()
      await link.click()
      await page.waitForURL(new RegExp(item.url), { timeout: 10_000 })
      await expect(page).toHaveURL(new RegExp(item.url))
    }
    // 调度已合并到任务页的「调度」视图，侧边栏不再有独立入口
    await expect(sidebar.getByRole('link', { name: /调度/ })).toHaveCount(0)
  })

  test('P02-05 任务页调度视图与旧路由重定向', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.tasks)
    await page.waitForTimeout(1500)
    await page.getByRole('tab', { name: /schedules|调度/i }).click()
    await page.waitForURL(/view=schedules/, { timeout: 10_000 })
    await expect(page.getByRole('heading', { name: /schedules|调度/i }).first()).toBeVisible()
    // 旧路由重定向
    await page.goto('/schedules')
    await page.waitForURL(/\/tasks\?view=schedules/, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/tasks\?view=schedules/)
  })

  test('P02-04 token 过期跳登录', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.evaluate(() => localStorage.setItem('nodesteer_token', 'invalid-token'))
    await page.goto(TEST_URLS.nodes)
    await page.waitForURL(/\/sign-in(?:\?.*)?$/, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/sign-in(?:\?.*)?$/)
  })
})
