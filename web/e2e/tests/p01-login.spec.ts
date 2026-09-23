import { test, expect } from 'playwright/test'
import { TEST_URLS, TEST_USER } from './fixtures'

test.describe('P01 登录页', () => {
  test('P01-01 正常登录', async ({ page }) => {
    await page.goto(TEST_URLS.login)
    await page.getByLabel(/username|用户名/i).fill(TEST_USER.username)
    await page.getByLabel(/password|密码/i).fill(TEST_USER.password)
    await page.getByRole('button', { name: /sign in|登录/i }).click()
    await page.waitForURL(TEST_URLS.dashboard, { timeout: 15_000 })
    await expect(page).toHaveURL(TEST_URLS.dashboard)
  })

  test('P01-02 密码错误', async ({ page }) => {
    await page.goto(TEST_URLS.login)
    await page.getByLabel(/username|用户名/i).fill(TEST_USER.username)
    await page.getByLabel(/password|密码/i).fill('wrongpassword')
    await page.getByRole('button', { name: /sign in|登录/i }).click()
    await page.waitForTimeout(2000)
    await expect(page).toHaveURL(TEST_URLS.login)
  })

  test('P01-03 空提交', async ({ page }) => {
    await page.goto(TEST_URLS.login)
    await page.getByRole('button', { name: /sign in|登录/i }).click()
    await page.waitForTimeout(1000)
    await expect(page).toHaveURL(TEST_URLS.login)
  })

  test('P01-04 中文/EN 切换', async ({ page }) => {
    await page.goto(TEST_URLS.login)
    const langBtn = page.getByRole('button', { name: /语言|language/i })
    await expect(langBtn).toBeVisible()
    await langBtn.click()
    await page.getByRole('menuitem', { name: /English/i }).click()
    await page.waitForTimeout(500)
    const stored = await page.evaluate(() => localStorage.getItem('nodesteer_lang'))
    expect(stored).toBe('en')
    // 语言与 <html lang> 保持一致（FAIL-B-110）
    const lang = await page.evaluate(() => document.documentElement.lang)
    expect(lang).toBe('en')
    // 切回中文，避免影响后续用例
    await langBtn.click()
    await page.getByRole('menuitem', { name: /中文/ }).click()
    await page.waitForTimeout(300)
    expect(await page.evaluate(() => document.documentElement.lang)).toBe('zh-CN')
  })

  test('P01-06 直接访问未登录页重定向', async ({ page }) => {
    const routes = ['/', '/nodes', '/scripts', '/tasks', '/executions']
    for (const route of routes) {
      await page.goto(route)
      await expect(page).toHaveURL(new RegExp(TEST_URLS.login))
    }
  })

  test('P01-07 SSO-only 模式（enabled:true, local_fallback:false）仅显示 SSO 入口', async ({
    page,
  }) => {
    await page.route('**/api/oidc/state', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ enabled: true, local_fallback: false }),
      })
    })
    await page.goto(TEST_URLS.login)
    await expect(page.getByRole('button', { name: /sign in with sso|使用 sso/i })).toBeVisible()
    await expect(page.getByLabel(/username|用户名/i)).toHaveCount(0)
    await expect(page.getByLabel(/password|密码/i)).toHaveCount(0)
  })

  test('P01-08 本地模式（enabled:false, local_fallback:true）仅显示本地表单', async ({
    page,
  }) => {
    await page.route('**/api/oidc/state', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ enabled: false, local_fallback: true }),
      })
    })
    await page.goto(TEST_URLS.login)
    await expect(page.getByLabel(/username|用户名/i)).toBeVisible()
    await expect(page.getByLabel(/password|密码/i)).toBeVisible()
    // 本地模式下不出现 SSO 按钮与分隔文案（互斥，永不并存）
    await expect(page.getByRole('button', { name: /sign in with sso|使用 sso/i })).toHaveCount(0)
    await expect(
      page.getByText(/or sign in with a local account|或使用本地账号登录/i)
    ).toHaveCount(0)
  })

  test('P01-09 本地登录被后端拒绝（403）时显示错误提示', async ({ page }) => {
    await page.route('**/api/oidc/state', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ enabled: false, local_fallback: true }),
      })
    })
    await page.route('**/api/login', async (route) => {
      await route.fulfill({
        status: 403,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'local login disabled' }),
      })
    })
    await page.goto(TEST_URLS.login)
    await page.getByLabel(/username|用户名/i).fill('any-user')
    await page.getByLabel(/password|密码/i).fill('any-password')
    await page.getByRole('button', { name: /sign in|登录/i }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('local login disabled')
  })
})

test.describe('P01 登录后回跳', () => {
  test('P01-05 登录后回跳到目标页', async ({ page }) => {
    await page.goto(TEST_URLS.nodes)
    await expect(page).toHaveURL(new RegExp(TEST_URLS.login))
    await page.getByLabel(/username|用户名/i).fill(TEST_USER.username)
    await page.getByLabel(/password|密码/i).fill(TEST_USER.password)
    await page.getByRole('button', { name: /sign in|登录/i }).click()
    await page.waitForURL(TEST_URLS.nodes, { timeout: 15_000 })
    await expect(page).toHaveURL(TEST_URLS.nodes)
  })
})
