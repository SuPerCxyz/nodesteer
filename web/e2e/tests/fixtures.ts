import { type Page } from 'playwright/test'

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
  schedules: '/tasks?view=schedules',
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
  const body = await resp.json()
  const token = body.token ?? body.Token
  await page.evaluate((t) => localStorage.setItem('nodesteer_token', t), token)
  return token
}

export async function apiRequest(
  page: Page,
  path: string,
  options: { method?: string; data?: unknown } = {}
) {
  return page.evaluate(
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
      return { status: response.status, ok: response.ok, body }
    },
    { path, method: options.method || 'GET', data: options.data }
  )
}

export async function getNodeIds(page: Page): Promise<string[]> {
  const resp = await page.request.get('/api/nodes')
  if (!resp.ok()) return []
  const nodes = await resp.json()
  return nodes.map((n: { id: string }) => n.id)
}

/** 删除名称匹配前缀的测试对象（脚本/任务及其调度），用于用例清理。 */
export async function cleanupByName(
  page: Page,
  prefixes: { scripts?: string[]; tasks?: string[] }
) {
  const list = (b: unknown) => (Array.isArray(b) ? b : ((b as { items?: unknown[] })?.items || []))
  const match = (name: string | undefined, prefix: string) =>
    typeof name === 'string' && name.startsWith(prefix)

  const schedules = await apiRequest(page, '/api/schedules')
  const tasks = await apiRequest(page, '/api/tasks')
  const taskItems = list(tasks.body) as { id: string; name: string }[]
  const doomedTasks = new Set(
    taskItems
      .filter((t) => (prefixes.tasks || []).some((p) => match(t.name, p)))
      .map((t) => t.id)
  )
  for (const sch of list(schedules.body) as { id: string; task_id: string }[]) {
    if (doomedTasks.has(sch.task_id)) {
      await apiRequest(page, `/api/schedules/${sch.id}`, { method: 'DELETE' })
    }
  }
  for (const t of taskItems) {
    if (doomedTasks.has(t.id)) {
      await apiRequest(page, `/api/tasks/${t.id}`, { method: 'DELETE' })
    }
  }
  const scripts = await apiRequest(page, '/api/scripts')
  for (const sc of list(scripts.body) as { id: string; name: string }[]) {
    if ((prefixes.scripts || []).some((p) => match(sc.name, p))) {
      await apiRequest(page, `/api/scripts/${sc.id}`, { method: 'DELETE' })
    }
  }
}

/** 删除指定表达式的调度（用于用例自建调度的清理）。 */
export async function deleteSchedulesByExpression(page: Page, expression: string) {
  const schedules = await apiRequest(page, '/api/schedules')
  const list = (b: unknown) => (Array.isArray(b) ? b : ((b as { items?: unknown[] })?.items || []))
  for (const sch of list(schedules.body) as { id: string; expression: string }[]) {
    if (sch.expression === expression) {
      await apiRequest(page, `/api/schedules/${sch.id}`, { method: 'DELETE' })
    }
  }
}
