import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ExecutionDetail } from './index'

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
    Link: ({ children }: { children?: ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

const EXECUTION = {
  id: 'exec-1',
  task_id: 'task-1',
  task_revision: 4,
  script_id: '',
  script_revision: 0,
  node_id: 'node-1',
  schedule_id: 'sch-1',
  trigger_type: 'schedule',
  scheduled_time: '2026-10-17T02:00:00Z',
  created_at: '2026-10-17T01:59:58Z',
  start_time: '2026-10-17T02:00:00Z',
  end_time: '2026-10-17T02:00:05Z',
  status: 'SUCCESS',
  exit_code: 0,
  stdout: 'ok'.repeat(300),
  stderr: '',
  stdout_truncated: true,
  stderr_truncated: false,
  offline: false,
  synced: true,
  block_reason: '',
}

const SCHEDULE = {
  id: 'sch-1',
  name: 'qa-schedule',
  task_id: 'task-1',
  revision: 1,
  type: 'cron',
  expression: '*/5 * * * *',
  interval_sec: 0,
  run_at: '',
  timezone: 'UTC',
  execution_owner: 'agent',
  target: { type: 'node', node_ids: ['node-1'] },
  timeout: 300,
  retry: 0,
  run_user: '',
  offline_policy: 'allow_offline',
  misfire_policy: 'run_once',
  enabled: true,
  created_at: '2026-10-17T00:00:00Z',
  updated_at: '2026-10-17T00:00:00Z',
}

async function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ExecutionDetail />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('执行记录详情：过程、结果与来源调度', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/executions/exec-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1') return EXECUTION
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'command' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/schedules/sch-1') return SCHEDULE
      if (path === '/executions/exec-1/logs') return []
      return []
    })
  })

  it('展示创建/开始/结束时间线、执行结果与来源调度链接', async () => {
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('executions.process')).first())
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('executions.createdAt')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('executions.startedAt')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('executions.finishedAt')))
      .toBeVisible()

    await expect
      .element(page.getByText(i18n.t('executions.result')).first())
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('executions.truncated')).first())
      .toBeVisible()

    await expect
      .element(page.getByRole('link', { name: 'qa-schedule' }))
      .toHaveAttribute('href', '/schedules/sch-1')
  })

  it('PENDING/RUNNING 显示进行中提示而不是隐藏过程', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1')
        return {
          ...EXECUTION,
          status: 'RUNNING',
          end_time: '',
          stdout: '',
          stderr: '',
        }
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'command' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/schedules/sch-1') return SCHEDULE
      if (path === '/executions/exec-1/logs') return []
      return []
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('executions.inProgress')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('executions.process')).first())
      .toBeVisible()
  })

  it('历史记录无来源调度时显示历史标记，不显示链接', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1')
        return { ...EXECUTION, schedule_id: undefined }
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'command' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/executions/exec-1/logs') return []
      return []
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('executions.historicalSource')))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: 'qa-schedule' }))
      .not.toBeInTheDocument()
  })

  it('来源调度解析失败时显示未知来源且页面不报错', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1')
        return { ...EXECUTION, schedule_id: 'missing' }
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'command' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/schedules/missing') throw new Error('not found')
      if (path === '/executions/exec-1/logs') return []
      return []
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('executions.unknownSource')))
      .toBeVisible()
    await expect.element(page.getByRole('alert')).not.toBeInTheDocument()
  })
})
