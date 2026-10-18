import { test, expect } from 'playwright/test'
import { apiRequest, captureCreated, cleanupByIds, getNodeIds, loginViaApi, testName, TEST_URLS } from './fixtures'

test.describe('E2E 端到端流程', () => {
  test('E2E-01 UI脚本任务到真实Agent执行与日志', async ({ page }) => {
    await loginViaApi(page)
    const marker = testName('real-agent')
    const owned: string[] = []
    try {
      await page.goto('/scripts/new')
      const scriptName = testName('flow-script')
      await page.getByLabel(/^name$|^名称$/i).fill(scriptName)
      await page.locator('textarea').first().fill(`#!/bin/sh\nprintf '%s\\n' '${marker}'`)
      const scriptId = await captureCreated(page, '/api/scripts', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      owned.unshift(`/api/scripts/${scriptId}`)
      await page.waitForURL(/\/scripts$/)
      await page.goto('/tasks/new')
      await page.getByLabel(/^name$|^名称$/i).fill(testName('flow-task'))
      await page.getByRole('combobox').first().click()
      await page.getByRole('option', { name: /^脚本$|^script$/i }).click()
      await page.getByRole('combobox').nth(1).click()
      await page.getByRole('option', { name: scriptName, exact: true }).click()
      const nodeIds = await getNodeIds(page)
      expect(nodeIds.length).toBeGreaterThan(0)
      const nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string }[]
      const node = nodes.find((item) => item.id === nodeIds[0])!
      await page.locator('label').filter({ hasText: node.hostname }).getByRole('checkbox').check()
      const taskId = await captureCreated(page, '/api/tasks', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
      owned.unshift(`/api/tasks/${taskId}`)
      await page.waitForURL(/\/tasks$/)
      await page.goto(`/tasks/${taskId}/run`)
      await page.getByRole('button', { name: /^立即运行$|^run now$/i }).click()
      const dialog = page.getByRole('alertdialog')
      const runResponse = page.waitForResponse((r) => new URL(r.url()).pathname === `/api/tasks/${taskId}/run`)
      await dialog.getByRole('button', { name: /^立即运行$|^run now$/i }).click()
      expect((await runResponse).ok()).toBeTruthy()
      await page.waitForURL(/\/executions$/)
      let execution: { id: string; status: string; stdout?: string } | undefined
      await expect.poll(async () => {
        const result = await apiRequest(page, `/api/executions?task_id=${taskId}&limit=10`)
        execution = (result.body as typeof execution[])?.find((e) => e?.status === 'SUCCESS')
        return execution?.stdout || ''
      }, { timeout: 30_000 }).toContain(marker)
      await page.goto(`/executions/${execution!.id}`)
      await expect(page.locator('pre')).toContainText(marker)
      await expect(page.getByRole('link', { name: /返回|back/i }).first()).toBeVisible()
    } finally { await cleanupByIds(page, owned) }
  })

  test('E2E-11 [ROOT_RBAC] 用户生命周期', async ({ page }) => {
    test.skip(process.env.NODESTEER_E2E_ALLOW_USER_CREATE !== '1', '用户无删除API，主控统一创建并记录保留')
    await loginViaApi(page)
    await page.goto(TEST_URLS.users)
    await page.waitForTimeout(2000)

    const createBtn = page.getByRole('button', { name: /create|新建|add/i }).first()
    await expect(createBtn).toBeVisible()
    await createBtn.click()
    await page.waitForTimeout(500)
    const usernameInput = page.getByRole('textbox', {
      name: /^(用户名|username)$/i,
    })
    await expect(usernameInput).toBeVisible()
    await usernameInput.fill(testName('user'))
    const passwordInput = page.getByLabel(/^(密码|password)$/i).first()
    await expect(passwordInput).toBeVisible()
    const rbacPassword = process.env.NODESTEER_E2E_RBAC_PASSWORD || ''
    if (!rbacPassword) throw new Error('NODESTEER_E2E_RBAC_PASSWORD is required')
    await passwordInput.fill(rbacPassword)
    const submitBtn = page.getByRole('button', { name: /submit|create|保存|创建/i }).first()
    await expect(submitBtn).toBeVisible()
    await submitBtn.click()
    await page.waitForTimeout(2000)
  })
})
