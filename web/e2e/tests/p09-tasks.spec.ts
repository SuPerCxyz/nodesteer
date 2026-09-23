import { test, expect } from 'playwright/test'
import { loginViaApi } from './fixtures'

test.describe('P10 任务编辑器', () => {
  test('P10-01 新建 command 任务', async ({ page }) => {
    await loginViaApi(page)
    await page.goto('/tasks/new')
    await page.waitForTimeout(1000)
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
    await nameInput.fill('test-e2e-task')
    const commandInput = page.getByRole('textbox', { name: /command|命令/i })
    await expect(commandInput).toBeVisible()
    await commandInput.fill('echo hello')
    const saveBtn = page.getByRole('button', { name: /save|保存/i }).first()
    await expect(saveBtn).toBeVisible()
    await saveBtn.click()
    await page.waitForTimeout(3000)
  })

  /**
   * FAIL-G-001 回归：app_deploy 类型必须出现 deploy/upgrade 操作选择，
   * 且保存 payload 的 app_operation 归一化为 deploy（后端仅接受 deploy|upgrade）。
   * 测试环境无托管应用数据时无法通过真实后端校验（application_id 400），
   * 故 route mock 拦截 POST /api/tasks 直接断言 payload。
   */
  test('P10-02 托管应用部署任务默认 deploy 并保存', async ({ page }) => {
    await loginViaApi(page)

    let posted: Record<string, unknown> | null = null
    await page.route(/\/api\/tasks$/, (route) => {
      if (route.request().method() === 'POST') {
        posted = route.request().postDataJSON() as Record<string, unknown>
        return route.fulfill({
          status: 201,
          json: { id: 'e2e-mocked-app-deploy-task' },
        })
      }
      return route.fallback()
    })

    await page.goto('/tasks/new')
    const nameInput = page.getByLabel(/name|名称/i)
    await expect(nameInput).toBeVisible()
    await nameInput.fill('e2e-app-deploy-task')

    // 类型切换为托管应用部署
    await page.getByRole('combobox').first().click()
    await page
      .getByRole('option', { name: /托管应用部署|Application Deploy/i })
      .click()

    // 出现操作选择（组合框顺序：类型[0]、托管应用[1]、操作[2]），默认 deploy
    const operationSelect = page.getByRole('combobox').nth(2)
    await expect(operationSelect).toHaveText(/部署|Deploy/)
    await operationSelect.click()
    await expect(page.getByRole('option', { name: /部署|Deploy/ })).toBeVisible()
    await expect(page.getByRole('option', { name: /升级|Upgrade/ })).toBeVisible()
    // app_deploy 不得出现 Operation 枚举（start/stop/restart）
    await expect(
      page.getByRole('option', { name: /启动|停止|重启|Start|Stop|Restart/ })
    ).toHaveCount(0)
    await page.getByRole('option', { name: /部署|Deploy/ }).click()

    await page.getByRole('button', { name: /save|保存/i }).first().click()
    await page.waitForURL(/\/tasks$/, { timeout: 15_000 })

    expect(posted).toBeTruthy()
    expect(posted?.type).toBe('app_deploy')
    expect(posted?.app_operation).toBe('deploy')
  })
})
