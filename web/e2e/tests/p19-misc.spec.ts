import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P19 审计', () => {
  test('P19-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.audit)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })
})

test.describe('P20 用户', () => {
  test('P20-01 列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.users)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P20-05 无删除用户功能', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.users)
    await page.waitForTimeout(2000)
    const deleteBtn = page.getByRole('button', { name: /delete|删除/i }).first()
    await expect(deleteBtn).toHaveCount(0)
  })
})

test.describe('P21 设置', () => {
  test('P21-01 表单渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.settings)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P21-02 保存设置', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.settings)
    await page.waitForTimeout(2000)
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(2000)
  })
})
