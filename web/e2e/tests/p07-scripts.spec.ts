import { test, expect } from 'playwright/test'
import { apiRequest, captureCreated, cleanupByIds, loginViaApi, testName, TEST_URLS } from './fixtures'

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

  test('P07-04 启用/禁用本轮脚本', async ({ page }) => {
    await loginViaApi(page)
    const created = await apiRequest(page, '/api/scripts', { method: 'POST', data: {
      name: testName('p0704'), interpreter: 'shell', content: 'echo qa', enabled: true,
    } })
    expect(created.ok).toBeTruthy()
    const id = (created.body as { id: string }).id
    try {
      await page.goto(TEST_URLS.scripts)
      const row = page.locator(`tr:has(a[href="/scripts/${id}"])`)
      await expect(row).toBeVisible()
      await row.getByRole('button', { name: /actions|操作/i }).click()
      await page.getByRole('menuitem', { name: /disable|禁用/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/scripts/${id}`)).body)
        .toMatchObject({ enabled: false })
      await row.getByRole('button', { name: /actions|操作/i }).click()
      await page.getByRole('menuitem', { name: /enable|启用/i }).click()
      await expect.poll(async () => (await apiRequest(page, `/api/scripts/${id}`)).body)
        .toMatchObject({ enabled: true })
    } finally { await cleanupByIds(page, [`/api/scripts/${id}`]) }
  })
})

test.describe('P08 脚本编辑器', () => {
  test('P08-01 新建脚本保存', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/scripts/new')
    await page.waitForTimeout(1000)
    const nameInput = page.getByLabel(/^name$|^名称$/i)
    await expect(nameInput).toBeVisible()
    await nameInput.fill(testName('p0801'))
    const contentArea = page.locator('textarea, [role="textbox"]').first()
    await expect(contentArea).toBeVisible()
    await contentArea.fill('#!/bin/bash\necho hello')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    const id = await captureCreated(page, '/api/scripts', () => saveBtn.click())
    try { await page.waitForURL(TEST_URLS.scripts); await expect(page.locator(`a[href="/scripts/${id}"]`)).toBeVisible() }
    finally { await cleanupByIds(page, [`/api/scripts/${id}`]) }
  })

  test('P08-08 取消编辑无写请求', async ({ page }) => {
    await loginViaApi(page)
    const created = await apiRequest(page, '/api/scripts', { method: 'POST', data: {
      name: testName('p0808'), interpreter: 'shell', content: 'echo cancel', enabled: true,
    } })
    expect(created.ok).toBeTruthy()
    const id = (created.body as { id: string }).id
    try {
      await page.goto(`/scripts/${id}`)
      await page.getByLabel(/^名称$|^name$/i).fill(testName('unsaved'))
      let writes = 0
      page.on('request', (r) => { if (new URL(r.url()).pathname === `/api/scripts/${id}` && r.method() === 'PUT') writes++ })
      await page.getByRole('link', { name: /^取消$|^cancel$/i }).click()
      await page.waitForURL(TEST_URLS.scripts)
      expect(writes).toBe(0)
      expect((await apiRequest(page, `/api/scripts/${id}`)).body).toMatchObject({ revision: 1 })
    } finally { await cleanupByIds(page, [`/api/scripts/${id}`]) }
  })
})
