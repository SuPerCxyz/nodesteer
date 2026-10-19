# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: p22-transfers.spec.ts >> P22 文件传输 >> P22-02 [UI_MOCK] FAILED 传输行菜单可取消并变为 CANCELED
- Location: e2e/tests/p22-transfers.spec.ts:23:3

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('menuitem', { name: /取消传输|Cancel transfer/i })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for getByRole('menuitem', { name: /取消传输|Cancel transfer/i })

```

```yaml
- region "Notifications alt+T"
- menu "操作":
  - menuitem "重试"
```

# Test source

```ts
  1  | import { test, expect } from 'playwright/test'
  2  | import { loginViaApi, TEST_URLS } from './fixtures'
  3  | 
  4  | test.describe('P22 文件传输', () => {
  5  |   test('P22-01 传输页面渲染', async ({ page }) => {
  6  |     await loginViaApi(page)
  7  |     await page.goto(TEST_URLS.transfers)
  8  |     await page.waitForTimeout(2000)
  9  |     const content = await page.textContent('body')
  10 |     expect(content).toBeTruthy()
  11 |   })
  12 | 
  13 |   /**
  14 |    * FAIL-M-001 UI 层回归：FAILED（非终态口径）传输的行菜单必须提供取消入口，
  15 |    * 取消后状态刷新为 CANCELED，节点删除不再被该传输阻断。
  16 |    *
  17 |    * 降级说明：现有 harness 无法轻易构造「源上传 completeUpload 失败」的真实传输
  18 |    * （需要真实 Agent 凭证走 Gateway 上传并让 Hub 校验失败），本用例用 route mock
  19 |    * 提供 FAILED 传输来覆盖 UI 层 canCancel 行为；Hub 端终态化由 Go 测试
  20 |    * TestFileTransferCompleteUploadFailureTerminalizesTargets 覆盖，
  21 |    * 「节点删除不再 409」由该 Go 用例的真实 NodeManager.Delete 断言。
  22 |    */
  23 |   test('P22-02 [UI_MOCK] FAILED 传输行菜单可取消并变为 CANCELED', async ({ page }) => {
  24 |     await loginViaApi(page)
  25 | 
  26 |     const now = new Date().toISOString()
  27 |     const transfer = {
  28 |       id: 'e2e-failed-transfer',
  29 |       source_node_id: 'e2e-node-source',
  30 |       source_path: '/var/lib/nodesteer/e2e.txt',
  31 |       size: 0,
  32 |       status: 'FAILED',
  33 |       error: 'source upload failed: e2e simulated',
  34 |       created_at: now,
  35 |       updated_at: now,
  36 |       targets: [
  37 |         {
  38 |           transfer_id: 'e2e-failed-transfer',
  39 |           node_id: 'e2e-node-target',
  40 |           destination_path: '/var/lib/nodesteer/e2e-target.txt',
  41 |           status: 'FAILED',
  42 |           error: 'source upload failed: e2e simulated',
  43 |           updated_at: now,
  44 |         },
  45 |       ],
  46 |     }
  47 |     let status = 'FAILED'
  48 |     await page.route(/\/api\/transfers$/, (route) =>
  49 |       route.fulfill({ json: [{ ...transfer, status }] })
  50 |     )
  51 |     await page.route(/\/api\/transfers\/[^/]+\/cancel$/, (route) => {
  52 |       status = 'CANCELED'
  53 |       return route.fulfill({ json: { ...transfer, status: 'CANCELED' } })
  54 |     })
  55 | 
  56 |     await page.goto(TEST_URLS.transfers)
  57 |     const row = page.locator('table tbody tr').first()
  58 |     await expect(row).toBeVisible()
  59 |     await expect(row.getByText(/^(失败|Failed)$/).first()).toBeVisible()
  60 | 
  61 |     // FAILED 行菜单出现取消入口（现有 UI 为菜单项直接触发，无二次确认弹窗）
  62 |     await row.getByRole('button', { name: /actions|操作/i }).click()
  63 |     const cancelItem = page.getByRole('menuitem', {
  64 |       name: /取消传输|Cancel transfer/i,
  65 |     })
> 66 |     await expect(cancelItem).toBeVisible()
     |                              ^ Error: expect(locator).toBeVisible() failed
  67 |     await cancelItem.click()
  68 | 
  69 |     // 取消请求发出后列表刷新为 CANCELED（已取消）
  70 |     await expect(row.getByText(/^(已取消|Canceled)$/).first()).toBeVisible({
  71 |       timeout: 10_000,
  72 |     })
  73 |   })
  74 | })
  75 | 
```