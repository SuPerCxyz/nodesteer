import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('E2E 端到端流程', () => {
  test('E2E-01 脚本任务全流程', async ({ page }) => {
    await loginViaApi(page)

    await page.goto('/scripts/new')
    await page.waitForTimeout(1000)
    const scriptName = `e2e-script-${Date.now()}`
    await page.getByLabel(/name|名称/i).fill(scriptName)
    const contentArea = page.locator('textarea, [role="textbox"]').first()
    await expect(contentArea).toBeVisible()
    await contentArea.fill('#!/bin/bash\necho "E2E_TEST_OUTPUT"')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(3000)

    await page.goto('/tasks/new')
    await page.waitForTimeout(1000)
    const taskName = `e2e-task-${Date.now()}`
    await page.getByLabel(/name|名称/i).fill(taskName)
    const commandInput = page.getByRole('textbox', { name: /command|命令/i })
    await expect(commandInput).toBeVisible()
    await commandInput.fill('echo E2E_SUCCESS')
    const targetCheckbox = page.getByRole('checkbox').first()
    await expect(targetCheckbox).toBeVisible()
    await targetCheckbox.click()
    const saveTaskBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveTaskBtn).toBeVisible()
    await saveTaskBtn.click()
    await page.waitForTimeout(3000)

    await page.goto(TEST_URLS.executions)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('E2E-11 用户生命周期', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.users)
    await page.waitForTimeout(2000)

    const createBtn = page.getByRole('button', { name: /create|新建|add/i }).first()
    await expect(createBtn).toBeVisible()
    await createBtn.click()
    await page.waitForTimeout(500)
    const usernameInput = page.getByRole('textbox', {
      name: /username|用户名/i,
    })
    await expect(usernameInput).toBeVisible()
    await usernameInput.fill(`e2e-user-${Date.now()}`)
    const passwordInput = page.getByRole('textbox', {
      name: /password|密码/i,
    })
    await expect(passwordInput).toBeVisible()
    const rbacPassword = process.env.CADENTRA_E2E_RBAC_PASSWORD || ''
    if (!rbacPassword) throw new Error('CADENTRA_E2E_RBAC_PASSWORD is required')
    await passwordInput.fill(rbacPassword)
    const submitBtn = page.getByRole('button', { name: /submit|create|保存|创建/i }).first()
    await expect(submitBtn).toBeVisible()
    await submitBtn.click()
    await page.waitForTimeout(2000)
  })
})
