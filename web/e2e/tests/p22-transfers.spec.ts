import { test, expect } from 'playwright/test'
import { loginViaApi, TEST_URLS } from './fixtures'

test.describe('P22 文件传输', () => {
  test('P22-01 传输页面渲染', async ({ page }) => {
    await loginViaApi(page)
    await page.goto(TEST_URLS.transfers)
    await page.waitForTimeout(2000)
    const content = await page.textContent('body')
    expect(content).toBeTruthy()
  })

  /**
   * FAIL-M-001 UI 层回归：FAILED（非终态口径）传输的行菜单必须提供取消入口，
   * 取消后状态刷新为 CANCELED，节点删除不再被该传输阻断。
   *
   * 降级说明：现有 harness 无法轻易构造「源上传 completeUpload 失败」的真实传输
   * （需要真实 Agent 凭证走 Gateway 上传并让 Hub 校验失败），本用例用 route mock
   * 提供 FAILED 传输来覆盖 UI 层 canCancel 行为；Hub 端终态化由 Go 测试
   * TestFileTransferCompleteUploadFailureTerminalizesTargets 覆盖，
   * 「节点删除不再 409」由该 Go 用例的真实 NodeManager.Delete 断言。
   */
  test('P22-02 FAILED 传输行菜单可取消并变为 CANCELED', async ({ page }) => {
    await loginViaApi(page)

    const now = new Date().toISOString()
    const transfer = {
      id: 'e2e-failed-transfer',
      source_node_id: 'e2e-node-source',
      source_path: '/var/lib/nodesteer/e2e.txt',
      size: 0,
      status: 'FAILED',
      error: 'source upload failed: e2e simulated',
      created_at: now,
      updated_at: now,
      targets: [
        {
          transfer_id: 'e2e-failed-transfer',
          node_id: 'e2e-node-target',
          destination_path: '/var/lib/nodesteer/e2e-target.txt',
          status: 'FAILED',
          error: 'source upload failed: e2e simulated',
          updated_at: now,
        },
      ],
    }
    let status = 'FAILED'
    await page.route(/\/api\/transfers$/, (route) =>
      route.fulfill({ json: [{ ...transfer, status }] })
    )
    await page.route(/\/api\/transfers\/[^/]+\/cancel$/, (route) => {
      status = 'CANCELED'
      return route.fulfill({ json: { ...transfer, status: 'CANCELED' } })
    })

    await page.goto(TEST_URLS.transfers)
    const row = page.locator('table tbody tr').first()
    await expect(row).toBeVisible()
    await expect(row.getByText(/^(失败|Failed)$/).first()).toBeVisible()

    // FAILED 行菜单出现取消入口（现有 UI 为菜单项直接触发，无二次确认弹窗）
    await row.getByRole('button', { name: /actions|操作/i }).click()
    const cancelItem = page.getByRole('menuitem', {
      name: /取消传输|Cancel transfer/i,
    })
    await expect(cancelItem).toBeVisible()
    await cancelItem.click()

    // 取消请求发出后列表刷新为 CANCELED（已取消）
    await expect(row.getByText(/^(已取消|Canceled)$/).first()).toBeVisible({
      timeout: 10_000,
    })
  })
})
