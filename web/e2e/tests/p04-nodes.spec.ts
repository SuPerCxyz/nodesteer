import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P04 节点列表', () => {
  test('P04-01 节点列表渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(2000)
    const table = page.locator('table').first()
    await expect(table).toBeVisible()
    const rows = table.locator('tbody tr')
    const count = await rows.count()
    expect(count).toBeGreaterThan(0)
  })

  test('P04-02 点击 hostname 进入详情', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(2000)
    const hostnameLink = page.locator('table tbody tr a').first()
    await expect(hostnameLink).toBeVisible()
    await hostnameLink.click()
    await page.waitForURL(/\/agents\//, { timeout: 10_000 })
    await expect(page).toHaveURL(/\/agents\//)
  })

  test('P04-03 维护/恢复按钮', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(2000)
    const actionMenu = page.getByRole('button', { name: /actions|操作/i }).first()
    await expect(actionMenu).toBeVisible()
    await actionMenu.click()
    await page.getByRole('menuitem', { name: /maintain|维护/i }).click()
    await page.waitForTimeout(1000)
    await actionMenu.click()
    await page.getByRole('menuitem', { name: /restore|恢复/i }).click()
    await page.waitForTimeout(1000)
  })

  test('P04-04 删除菜单关闭并显示确认弹窗', async ({ page }) => {
    await loginViaApi(page)
    await page.route('**/api/nodes/*', async (route) => {
      if (route.request().method() === 'DELETE') {
        await route.fulfill({ status: 204, body: '' })
        return
      }
      await route.continue()
    })
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(1000)

    const actionMenu = page.getByRole('button', { name: /actions|操作/i }).first()
    await expect(actionMenu).toBeVisible()
    await actionMenu.click()
    const deleteItem = page.getByRole('menuitem', { name: /delete|删除/i })
    await expect(deleteItem).toBeVisible()
    await deleteItem.click()
    await expect(deleteItem).toBeHidden()

    const confirm = page.locator('[role="alertdialog"]')
    await expect(confirm).toBeVisible()
    await confirm.getByRole('button', { name: /delete|删除/i }).click()
    await expect(confirm).toBeHidden()
  })
})

test.describe('P05 节点详情', () => {
  test('P05-01 节点详情渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(2000)
    const hostnameLink = page.locator('table tbody tr a').first()
    await expect(hostnameLink).toBeVisible()
    await hostnameLink.click()
    await page.waitForURL(/\/agents\//, { timeout: 10_000 })
    await page.waitForTimeout(1500)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  test('P05-08 添加节点命令', async ({ page }) => {
    await loginViaApi(page)
    await page.context().grantPermissions(
      ['clipboard-read', 'clipboard-write'],
      { origin: process.env.BASE_URL || 'http://192.168.100.209:8080' }
    )
    await page.route('**/api/nodes/enrollment', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          gateway_url: 'ws://hub.example.test:8443',
          native: 'sudo install ./nodesteer-agent',
          docker_run: 'docker run nodesteer-agent',
          docker_compose: 'services:\n  nodesteer-agent:',
        }),
      })
    })
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(2000)
    const addNodeBtn = page.getByRole('button', { name: /add node|添加节点/i }).first()
    await expect(addNodeBtn).toBeVisible()
    await addNodeBtn.click()
    await page.waitForTimeout(1000)
    const dialog = page.locator('[role="dialog"]')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel(/节点名称|node name/i).fill(`e2e-node-${Date.now()}`)
    await dialog.getByLabel(/节点地址|node address/i).fill('192.168.100.212')
    await dialog.getByLabel(/Hub 地址|hub address/i).fill(process.env.BASE_URL || 'http://192.168.100.209:8080')
    await dialog.getByRole('button', { name: /生成安装命令|generate/i }).click()
    await page.waitForTimeout(1000)
    const content = await dialog.textContent()
    expect(content).toContain('nodesteer-agent')
    await dialog.getByRole('button', { name: /copy|复制/i }).click()
    const secureClipboard = await page.evaluate(
      () => window.isSecureContext && !!navigator.clipboard?.readText
    )
    if (secureClipboard) {
      const copied = await page.evaluate(() => navigator.clipboard?.readText())
      expect(copied).toBe('sudo install ./nodesteer-agent')
    } else {
      await expect(
        page.getByText(/当前页面无法自动写入剪切板|cannot write to the clipboard automatically/i)
      ).toBeVisible()
    }
  })

  test('P05-09 关闭后重新打开纳管弹窗应重置状态', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.nodes)
    const addNodeBtn = page.getByRole('button', {
      name: /add node|添加节点/i,
    })
    await expect(addNodeBtn).toBeVisible()
    await addNodeBtn.click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByLabel(/节点名称|node name/i).fill('invalid-enrollment')
    await dialog.getByLabel(/节点地址|node address/i).fill('not_an_address')
    await dialog.getByRole('button', {
      name: /生成安装命令|generate/i,
    }).click()
    await expect(dialog).toContainText(/valid IP|有效的 IP|hostname|主机名/i)

    await dialog.getByRole('button', { name: /close|关闭/i }).click()
    await expect(dialog).toBeHidden()
    await addNodeBtn.click()
    await expect(dialog).toBeVisible()
    await expect(dialog.getByLabel(/节点名称|node name/i)).toHaveValue('')
    await expect(dialog.getByLabel(/节点地址|node address/i)).toHaveValue('')
    await expect(dialog).not.toContainText(/valid IP|有效的 IP|hostname|主机名/i)
  })
})
