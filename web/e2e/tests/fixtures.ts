import { type Page } from 'playwright/test'
import { randomBytes, randomUUID } from 'node:crypto'
import { appendFile, readFile } from 'node:fs/promises'

export const TEST_PREFIX = process.env.NODESTEER_E2E_PREFIX || `qa-${Date.now()}-web-`
const ownedResources = new Set<string>()
const retainedExecutions = new Set<string>()

export const testName = (label: string) => `${TEST_PREFIX}${label}-${randomUUID().slice(0, 8)}`

async function recordCreated(path: string, id: string, name?: string) {
  ownedResources.add(`${path}/${id}`)
  if (process.env.NODESTEER_E2E_RESOURCE_LOG)
    await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path, id, name, status: 'created' })}\n`)
}

/** 捕获UI创建响应，只保存本轮对象ID，不保存凭据或响应全文。 */
export async function captureCreated(page: Page, path: string, action: () => Promise<unknown>) {
  const key = `qa-created:${path}`
  await page.evaluate(({ path, key }) => {
    sessionStorage.removeItem(key)
    const original = window.fetch
    ;(window as Window & { qaOriginalFetch?: typeof fetch }).qaOriginalFetch = original
    window.fetch = async (...args) => {
      const response = await original(...args)
      const url = typeof args[0] === 'string' ? args[0] : args[0] instanceof URL ? args[0].href : args[0].url
      if (new URL(url, location.origin).pathname === path && args[1]?.method === 'POST') {
        // 只旁观真实浏览器响应，ID存入sessionStorage以跨越页面硬跳转；凭据不落盘。
        const body = await response.clone().json() as { id?: string; node_id?: string; name?: string }
        sessionStorage.setItem(key, JSON.stringify({ id: body.id || body.node_id, name: body.name, status: response.status }))
      }
      return response
    }
  }, { path, key })
  try {
    await action()
    await page.waitForFunction((key) => !!sessionStorage.getItem(key), key)
    await page.waitForLoadState('networkidle')
    let body: { id?: string; name?: string; status: number } | undefined
    // 硬导航可发生在networkidle与evaluate之间；旁观ID已存sessionStorage，等待新页面再读取。
    for (let attempt = 0; attempt < 3; attempt++) {
      try { body = await page.evaluate((key) => JSON.parse(sessionStorage.getItem(key)!), key); break }
      catch (error) {
        if (!String(error).includes('Execution context was destroyed') || attempt === 2) throw error
        await page.waitForLoadState('networkidle')
      }
    }
    if (!body) throw new Error(`Create ${path}: observer ID missing`)
    if (body.status >= 400 || !body.id) throw new Error(`Create ${path}: HTTP ${body.status}; object ID ${body.id ? 'present' : 'missing'}`)
    await recordCreated(path === '/api/nodes/enrollment' ? '/api/nodes' : path, body.id, body.name)
    return body.id
  } finally {
    await page.evaluate((key) => {
      const target = window as Window & { qaOriginalFetch?: typeof fetch }
      if (target.qaOriginalFetch) window.fetch = target.qaOriginalFetch
      delete target.qaOriginalFetch
      sessionStorage.removeItem(key)
    }, key).catch(() => {})
  }
}

/** 仅清理已捕获的本轮精确ID，禁止按名称前缀或cron表达式删除。 */
export async function cleanupByIds(page: Page, paths: string[]) {
  for (const path of paths) {
    if (!ownedResources.has(path)) throw new Error(`Refusing unowned cleanup: ${path}`)
    const response = await apiRequest(page, path, { method: 'DELETE' })
    if (![200, 204, 404].includes(response.status)) throw new Error(`Cleanup ${path}: HTTP ${response.status}`)
    ownedResources.delete(path)
    if (process.env.NODESTEER_E2E_RESOURCE_LOG)
      await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path, status: 'deleted' })}\n`)
  }
}

export const TEST_USER = {
  username: process.env.NODESTEER_E2E_USERNAME || 'admin',
  password: process.env.NODESTEER_E2E_PASSWORD || '',
}

export const TEST_URLS = {
  login: '/sign-in',
  dashboard: '/',
  nodes: '/nodes',
  groups: '/groups',
  scripts: '/scripts',
  tasks: '/tasks',
  schedules: '/schedules',
  schedulesNew: '/schedules/new',
  applications: '/applications',
  artifacts: '/artifacts',
  executions: '/executions',
  audit: '/audit',
  users: '/users',
  settings: '/settings',
  transfers: '/transfers',
}

export async function login(page: Page, username = TEST_USER.username, password = TEST_USER.password) {
  // 先退出当前测试身份；有效会话访问登录页会自动返回业务页。
  await page.goto(TEST_URLS.dashboard)
  const token = await page.evaluate(() => localStorage.getItem('nodesteer_token'))
  if (token) {
    const response = await page.request.post('/api/logout', { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok() && response.status() !== 401) throw new Error(`Logout before login failed: HTTP ${response.status()}`)
  }
  await page.evaluate(() => localStorage.removeItem('nodesteer_token'))
  await page.goto(TEST_URLS.login)
  await page.getByLabel(/username|用户名/i).fill(username)
  await page.getByLabel(/password|密码/i).fill(password)
  await page.getByRole('button', { name: /sign in|登录/i }).click()
  await page.waitForURL(TEST_URLS.dashboard, { timeout: 15_000 })
}

export async function loginViaApi(page: Page, username = TEST_USER.username, password = TEST_USER.password) {
  await page.goto(TEST_URLS.login)
  const resp = await page.request.post('/api/login', {
    data: { username, password },
  })
  if (!resp.ok()) throw new Error(`Login failed: HTTP ${resp.status()}`)
  const body = await resp.json()
  const token = body.token ?? body.Token
  if (typeof token !== 'string' || !token) throw new Error('Login response has no session token')
  await page.evaluate((t) => localStorage.setItem('nodesteer_token', t), token)
  return token
}

export async function apiRequest(
  page: Page,
  path: string,
  options: { method?: string; data?: unknown } = {}
) {
  const method = options.method || 'GET'
  const evaluate = () =>
    page.evaluate(
      async ({ path, method, data }) => {
        const response = await fetch(path, {
          method,
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${localStorage.getItem('nodesteer_token') || ''}`,
          },
          body: data === undefined ? undefined : JSON.stringify(data),
        })
        let body: unknown = null
        try {
          body = await response.json()
        } catch {
          // Empty responses are valid for some API operations.
        }
        return { status: response.status, ok: response.ok, body, headers: Object.fromEntries(response.headers.entries()) as Record<string, string> }
      },
      { path, method, data: options.data }
    )
  let result: Awaited<ReturnType<typeof evaluate>>
  for (let attempt = 0; ; attempt++) {
    try {
      result = await evaluate()
      break
    } catch (error) {
      // 页面硬导航会销毁执行上下文：仅对幂等的 GET 做有界重试（与 captureCreated 同策略），避免整轮假失败。
      if (attempt >= 2 || method !== 'GET' || !String(error).includes('Execution context was destroyed')) throw error
      await page.waitForLoadState('domcontentloaded').catch(() => {})
    }
  }
  if (options.method === 'POST' && result.ok) {
    const data = options.data as { name?: string; node_name?: string; task_id?: string } | undefined
    const body = result.body as { id?: string; node_id?: string } | null
    const name = data?.name || data?.node_name
    const ownSchedule = path === '/api/schedules' && ownedResources.has(`/api/tasks/${data?.task_id}`)
    if ((name?.startsWith(TEST_PREFIX) || ownSchedule) && (body?.id || body?.node_id))
      await recordCreated(path === '/api/nodes/enrollment' ? '/api/nodes' : path, (body?.id || body?.node_id)!, name)
  }
  const taskId = new URL(path, 'http://e2e.local').searchParams.get('task_id')
  if (path.startsWith('/api/executions?') && taskId && ownedResources.has(`/api/tasks/${taskId}`) && Array.isArray(result.body)) {
    for (const execution of result.body as { id: string; node_id: string; status: string }[]) {
      if (retainedExecutions.has(execution.id)) continue
      retainedExecutions.add(execution.id)
      if (process.env.NODESTEER_E2E_RESOURCE_LOG)
        await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path: '/api/executions', id: execution.id, task_id: taskId, node_id: execution.node_id, status: 'retained_history' })}\n`)
    }
  }
  return result
}

export async function getNodeIds(page: Page): Promise<string[]> {
  const resp = await apiRequest(page, '/api/nodes')
  if (!resp.ok) throw new Error(`Nodes: HTTP ${resp.status}`)
  const allowed = (process.env.NODESTEER_E2E_NODE_IDS || '').split(',').filter(Boolean)
  if (!allowed.length) throw new Error('NODESTEER_E2E_NODE_IDS must identify authorized test Agents')
  const nodes = resp.body as { id: string }[]
  return nodes.filter((n) => allowed.includes(n.id)).map((n) => n.id)
}

export async function createIsolatedTask(page: Page, label: string) {
  const nodeIds = await getNodeIds(page)
  if (!nodeIds.length) throw new Error('No authorized Agent is present')
  const result = await apiRequest(page, '/api/tasks', { method: 'POST', data: {
    name: testName(label), type: 'command', command: 'echo isolated-e2e',
    target: { type: 'node', node_ids: [nodeIds[0]] },
    enabled: true, timeout: 30, retry: 0, offline_policy: 'hub_online_required',
  } })
  if (!result.ok) throw new Error(`Create isolated task: HTTP ${result.status}`)
  return result.body as { id: string; name: string }
}

/** 本轮授权的测试节点集合（NODESTEER_E2E_NODE_IDS + NODESTEER_E2E_LABEL_NODE_ID，去重）。 */
export function authorizedNodeIds(): string[] {
  const ids = (process.env.NODESTEER_E2E_NODE_IDS || '').split(',').filter(Boolean)
  const label = process.env.NODESTEER_E2E_LABEL_NODE_ID
  const all = [...ids]
  if (label) all.push(label)
  return [...new Set(all)]
}

export interface QaRoleAccount { user_id: string; username: string; password: string }

/**
 * 解析 QA 角色账号（角色收敛/权限类用例），使套件可跨环境复用，按优先级：
 * 1) 环境提供：NODESTEER_E2E_ROLE_USERNAME + NODESTEER_E2E_ROLE_PASSWORD（可用 NODESTEER_E2E_ROLE_USER_ID 指定 ID）；
 * 2) 运行时发现：私有凭据库 ui-users.jsonl 中最近一条含用户名+口令、且账号仍存在于当前环境的记录；
 * 3) 自建夹具：自有前缀 + 一次性随机口令，以 viewer 角色保留并记录到私有凭据库（用户无删除 API）。
 */
export async function ensureRoleAccount(page: Page, purpose: string): Promise<QaRoleAccount> {
  const usersResponse = await apiRequest(page, '/api/users')
  if (!usersResponse.ok) throw new Error(`Resolve role account: /api/users HTTP ${usersResponse.status}`)
  const users = usersResponse.body as { id: string; username: string; role: string }[]

  const envUsername = process.env.NODESTEER_E2E_ROLE_USERNAME
  const envPassword = process.env.NODESTEER_E2E_ROLE_PASSWORD
  if (envUsername && envPassword) {
    const user = users.find((item) => item.username === envUsername || item.id === process.env.NODESTEER_E2E_ROLE_USER_ID)
    if (!user) throw new Error(`NODESTEER_E2E_ROLE_USERNAME "${envUsername}" is not present in this environment`)
    return { user_id: user.id, username: user.username, password: envPassword }
  }

  const privateDir = process.env.NODESTEER_E2E_PRIVATE_DIR
  if (!privateDir) throw new Error('NODESTEER_E2E_PRIVATE_DIR is required to discover or persist the QA role account')
  const storePath = privateDir + '/ui-users.jsonl'
  // 私有库缺失/损坏时按「无记录」处理（随后进入自建分支）
  const stored = await readFile(storePath, 'utf8').then(
    (text) => text.trim().split('\n').filter(Boolean).map((line) => JSON.parse(line) as { user_id?: string; username?: string; password?: string; role_account?: boolean }),
    () => [] as { user_id?: string; username?: string; password?: string; role_account?: boolean }[],
  )
  const candidates = [...stored].reverse().filter((row) => row.username && row.password)
  for (const row of [...candidates.filter((item) => item.role_account), ...candidates]) {
    const user = users.find((item) => item.username === row.username && (!row.user_id || item.id === row.user_id))
    if (user) return { user_id: user.id, username: user.username, password: String(row.password) }
  }

  const username = testName('role-user')
  const password = randomBytes(24).toString('base64url')
  const created = await apiRequest(page, '/api/users', { method: 'POST', data: { username, password, role: 'viewer' } })
  if (!created.ok) throw new Error(`Create QA role account: HTTP ${created.status}`)
  const userId = (created.body as { id: string }).id
  await appendFile(storePath, JSON.stringify({ user_id: userId, username, password, role: 'viewer', role_account: true, purpose }) + '\n', { mode: 0o600 })
  if (process.env.NODESTEER_E2E_RESOURCE_LOG)
    await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path: '/api/users', id: userId, name: username, status: 'retained_account', role: 'viewer' })}\n`)
  return { user_id: userId, username, password }
}
