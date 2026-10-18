import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import type { Node } from '@/lib/api'
import { AgentDetail } from './index'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => true,
  useCanRun: () => true,
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({ children }: { children?: React.ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

const NODE: Node = {
  id: 'node-1',
  agent_id: 'agent-1',
  hostname: 'node-a',
  ip: '192.0.2.10',
  os: 'linux',
  arch: 'amd64',
  agent_version: '1.0.0',
  deployment_mode: 'native',
  host_integration: false,
  status: 'online',
  labels: {},
  capabilities: {},
  global_revision: 1,
  sync_status: 'synced',
  last_seen: '2026-10-17T00:00:00Z',
  first_seen: '2026-10-01T00:00:00Z',
}

const TASK = {
  id: 'task-1',
  name: 'qa-task',
  type: 'command',
  command: 'true',
  target: { type: 'node', node_ids: ['node-1'] },
  parameters: [],
  param_values: {},
  timeout: 60,
  retry: 0,
  offline_policy: 'hub_online_required',
  enabled: true,
  revision: 1,
  updated_at: '2026-10-17T00:00:00Z',
  created_at: '2026-10-17T00:00:00Z',
}

const SCHEDULE = {
  id: 'sch-1',
  task_id: 'task-1',
  revision: 1,
  type: 'cron',
  expression: '*/5 * * * *',
  interval_sec: 0,
  run_at: '',
  timezone: 'UTC',
  execution_owner: 'agent',
  offline_policy: 'allow_offline',
  misfire_policy: 'run_once',
  enabled: true,
  created_at: '2026-10-17T00:00:00Z',
  updated_at: '2026-10-17T00:00:00Z',
}

const APPLICATION = {
  id: 'app-1',
  name: 'qa-app',
  version: '1.0.0',
  artifact_id: 'art-1',
}

async function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <AgentDetail />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('节点详情任务/调度/应用回链（OBS-20261017-CROSSPAGE-LINK-GAPS ⑤）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/agents/node-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes/node-1') return NODE
      if (path.startsWith('/executions?')) return []
      if (path === '/tasks') return [TASK]
      if (path === '/groups') return []
      if (path === '/schedules') return [SCHEDULE]
      if (path === '/applications') return [APPLICATION]
      if (path === '/applications/app-1/nodes') return ['node-1']
      return []
    })
  })

  it('渲染指向该节点的任务、调度与托管应用回链', async () => {
    const page = await renderDetail()

    // 任务区与调度区各有一个以任务名渲染的链接
    const taskLinks = page.getByRole('link', { name: 'qa-task' })
    await expect
      .element(taskLinks.nth(0))
      .toHaveAttribute('href', '/tasks/task-1')
    await expect
      .element(taskLinks.nth(1))
      .toHaveAttribute('href', '/schedules/sch-1')
    await expect
      .element(page.getByRole('link', { name: 'qa-app' }))
      .toHaveAttribute('href', '/applications/app-1')
  })
})
