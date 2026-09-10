import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P03 仪表盘', () => {
  test('P03-01 仪表盘渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P03-02 节点表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    await page.waitForTimeout(2000)
    const table = page.locator('table').first()
    await expect(table).toBeVisible()
    const rows = table.locator('tbody tr')
    expect(await rows.count()).toBeGreaterThan(0)
  })

  test('P03-03 仪表盘无崩溃', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.dashboard)
    const errors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text())
    })
    await page.waitForTimeout(3000)
    expect(errors.filter((e) => e.includes('Uncaught')).length).toBe(0)
  })
})
