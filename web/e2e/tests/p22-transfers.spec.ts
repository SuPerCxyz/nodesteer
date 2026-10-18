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
   * P16 终态冻结 UI 层回归：FAILED 传输不可取消——行菜单仅提供「重试」，
   * 不再出现「取消传输」入口（取消只对非终态传输开放，Retry 是唯一恢复路径）。
   *
   * 降级说明：现有 harness 无法轻易构造「源上传 completeUpload 失败」的真实传输
   * （需要真实 Agent 凭证走 Gateway 上传并让 Hub 校验失败），本用例用 route mock
   * 提供 FAILED 传输来覆盖 UI 层 canCancel/canRetry 行为；Hub 端终态冻结由 Go 测试
   * TestFileTransferExplicitActionsOnTerminal（FAILED cancel rejected / FAILED retry
   * is the explicit recovery path）覆盖。
   */
  test('P22-02 [UI_MOCK] FAILED 传输行仅提供重试、无取消入口', async ({ page }) => {
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
    let retryCalls = 0
    let cancelCalls = 0
    await page.route(/\/api\/transfers$/, (route) =>
      route.fulfill({ json: [transfer] })
    )
    await page.route(/\/api\/transfers\/[^/]+\/retry$/, (route) => {
      retryCalls += 1
      // 源文件仍缺失：重试后再次回到 FAILED 终态（Retry 是唯一恢复路径）
      return route.fulfill({ json: { ...transfer, status: 'FAILED' } })
    })
    await page.route(/\/api\/transfers\/[^/]+\/cancel$/, (route) => {
      cancelCalls += 1
      return route.fulfill({
        status: 400,
        json: { error: 'transfer already finished: FAILED' },
      })
    })

    await page.goto(TEST_URLS.transfers)
    const row = page.locator('table tbody tr').first()
    await expect(row).toBeVisible()
    await expect(row.getByText(/^(失败|Failed)$/).first()).toBeVisible()

    // P16 终态冻结：FAILED 行菜单仅「重试」，不得出现「取消传输」
    await row.getByRole('button', { name: /actions|操作/i }).click()
    const retryItem = page.getByRole('menuitem', { name: /^重试$|^retry$/i })
    await expect(retryItem).toBeVisible()
    await expect(
      page.getByRole('menuitem', { name: /取消传输|Cancel transfer/i })
    ).toHaveCount(0)

    // 「重试」是唯一恢复路径：点击后确实发出 retry 请求，且全程不发出 cancel 请求
    await retryItem.click()
    await expect.poll(() => retryCalls).toBe(1)
    expect(cancelCalls).toBe(0)
  })
})
