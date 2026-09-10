import { type Page } from 'playwright/test'

export const TEST_USER = {
  username: process.env.CADENTRA_E2E_USERNAME || 'admin',
  password: process.env.CADENTRA_E2E_PASSWORD || '',
}

export const TEST_URLS = {
  login: '/sign-in',
  dashboard: '/',
  nodes: '/nodes',
  groups: '/groups',
  scripts: '/scripts',
  tasks: '/tasks',
  schedules: '/schedules',
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
  await page.evaluate((t) => localStorage.setItem('cadentra_token', t), token)
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
          Authorization: `Bearer ${localStorage.getItem('cadentra_token') || ''}`,
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
