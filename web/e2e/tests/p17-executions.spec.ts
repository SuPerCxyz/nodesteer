import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P17 执行列表', () => {
  test('P17-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.executions)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
    await expect(page.getByRole('heading', { name: /执行记录|executions/i }).first()).toBeVisible()
    await expect(page.getByRole('columnheader', { name: /来源调度|source schedule/i })).toBeVisible()
  })

  test('P17-03 点击 ID 进详情', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.executions)
    await page.waitForTimeout(2000)
    const idLink = page.locator('table tbody tr a[href^="/executions/"]').first()
    await expect(idLink).toBeVisible()
    await idLink.click()
    await page.waitForURL(/\/executions\//, { timeout: 10_000 })
  })
})

test.describe('P18 执行详情', () => {
  test('P18-01 详情渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.executions)
    await page.waitForTimeout(2000)
    const idLink = page.locator('table tbody tr a[href^="/executions/"]').first()
    await expect(idLink).toBeVisible()
    await idLink.click()
    await page.waitForURL(/\/executions\//, { timeout: 10_000 })
    await page.waitForTimeout(1500)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
    await expect(page.getByText(/执行过程|execution process/i).first()).toBeVisible()
    await expect(page.getByText(/执行结果|execution result/i).first()).toBeVisible()
  })
})
