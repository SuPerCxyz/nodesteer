import { test, expect } from 'playwright/test'
import { apiRequest, loginViaApi, TEST_URLS, TEST_USER } from './fixtures'

const OPERATOR_USER = process.env.CADENTRA_E2E_OPERATOR_USERNAME || 'test-operator'
const VIEWER_USER = process.env.CADENTRA_E2E_VIEWER_USERNAME || 'test-viewer'
const RBAC_PASSWORD = process.env.CADENTRA_E2E_RBAC_PASSWORD || ''

test.describe('RBAC 权限矩阵', () => {
  test('RBAC-07 未认证请求 401', async ({ page }) => {
    const resp = await page.request.get('/api/nodes')
    expect(resp.status()).toBe(401)
  })

  test('RBAC-06 Admin 全功能', async ({ page }) => {
    await loginViaApi(page, TEST_USER.username, TEST_USER.password)
    const nodesResp = await apiRequest(page, '/api/nodes')
    expect(nodesResp.ok).toBeTruthy()
    const scriptsResp = await apiRequest(page, '/api/scripts')
    expect(scriptsResp.ok).toBeTruthy()
    const tasksResp = await apiRequest(page, '/api/tasks')
    expect(tasksResp.ok).toBeTruthy()
    const settingsResp = await apiRequest(page, '/api/settings')
    expect(settingsResp.ok).toBeTruthy()
    const usersResp = await apiRequest(page, '/api/users')
    expect(usersResp.ok).toBeTruthy()
  })

  test('RBAC-02/03 Operator/Viewer 读取正常', async ({ page }) => {
    for (const user of [OPERATOR_USER, VIEWER_USER]) {
      await loginViaApi(page, user, RBAC_PASSWORD)
      const nodesResp = await apiRequest(page, '/api/nodes')
      expect(nodesResp.ok).toBeTruthy()
      const scriptsResp = await apiRequest(page, '/api/scripts')
      expect(scriptsResp.ok).toBeTruthy()
    }
  })

  test('RBAC-02 Viewer 写操作被拒', async ({ page }) => {
    await loginViaApi(page, VIEWER_USER, RBAC_PASSWORD)
    const createScriptResp = await apiRequest(page, '/api/scripts', {
      method: 'POST',
      data: { name: 'rbac-test', interpreter: 'bash', content: 'echo test' },
    })
    expect(createScriptResp.status).toBe(403)
  })

  test('RBAC-03 Operator 写操作被拒', async ({ page }) => {
    await loginViaApi(page, OPERATOR_USER, RBAC_PASSWORD)
    const createScriptResp = await apiRequest(page, '/api/scripts', {
      method: 'POST',
      data: { name: 'rbac-test', interpreter: 'bash', content: 'echo test' },
    })
    expect(createScriptResp.status).toBe(403)
  })

  test('RBAC-04 Operator 节点状态修改被拒', async ({ page }) => {
    await loginViaApi(page, OPERATOR_USER, RBAC_PASSWORD)
    const nodesResp = await apiRequest(page, '/api/nodes')
    if (nodesResp.ok) {
      const nodes = Array.isArray(nodesResp.body) ? nodesResp.body : []
      if (nodes.length > 0) {
        const statusResp = await apiRequest(page, `/api/nodes/${nodes[0].id}`, {
          method: 'POST',
          data: { status: 'maintenance' },
        })
        expect(statusResp.status).toBe(403)
      }
    }
  })

  test('RBAC-05 Operator 用户/设置被拒', async ({ page }) => {
    await loginViaApi(page, OPERATOR_USER, RBAC_PASSWORD)
    const settingsResp = await apiRequest(page, '/api/settings', {
      method: 'PUT',
      data: { heartbeat_interval_sec: '30' },
    })
    expect(settingsResp.status).toBe(403)
    const createUserResp = await apiRequest(page, '/api/users', {
      method: 'POST',
      data: { username: 'should-not-exist', password: 'test', role: 'viewer' },
    })
    expect(createUserResp.status).toBe(403)
  })

  test('RBAC-08 页面只展示角色允许的操作', async ({ page }) => {
    for (const [username, canRun] of [
      [OPERATOR_USER, true],
      [VIEWER_USER, false],
    ] as const) {
      await loginViaApi(page, username, RBAC_PASSWORD)

      await page.goto(TEST_URLS.nodes)
      await expect(
        page.getByRole('button', { name: /add node|添加节点/i })
      ).toHaveCount(0)

      await page.goto(TEST_URLS.tasks)
      await expect(
        page.getByRole('link', { name: /new task|新建任务/i })
      ).toHaveCount(0)
      const runCount = await page
        .getByRole('link', { name: /run now|立即运行/i })
        .count()
      if (canRun) expect(runCount).toBeGreaterThan(0)
      else expect(runCount).toBe(0)

      for (const [path, name] of [
        [TEST_URLS.scripts, /new script|新建脚本/i],
        [TEST_URLS.groups, /new group|新建分组/i],
        [TEST_URLS.schedules, /new schedule|新建调度/i],
        [TEST_URLS.applications, /new application|新建托管应用/i],
        [TEST_URLS.artifacts, /upload|上传/i],
      ] as const) {
        await page.goto(path)
        await expect(page.getByRole('link', { name })).toHaveCount(0)
      }

      await page.goto(TEST_URLS.transfers)
      await expect(
        page.getByText(/new (file )?transfer|新建文件传输/i)
      ).toHaveCount(0)
      await expect(
        page.getByRole('button', { name: /start|开始传输|add target|添加目标/i })
      ).toHaveCount(0)

      await page.goto(TEST_URLS.settings)
      await expect(
        page.getByRole('button', { name: /save|保存/i })
      ).toHaveCount(0)
    }
  })
})
