import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Executions } from './index'

const { getPage, get } = vi.hoisted(() => ({ getPage: vi.fn(), get: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, getPage, get } }
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
    getRouteApi: () => ({
      useSearch: () => ({}),
      useNavigate: () => vi.fn(),
    }),
  }
})

const EXECUTION = {
  id: 'exec-1',
  task_id: 'task-1',
  task_revision: 1,
  script_id: '',
  script_revision: 0,
  node_id: 'node-1',
  trigger_type: 'manual',
  scheduled_time: '',
  start_time: '2026-10-17T00:00:00Z',
  end_time: '2026-10-17T00:00:01Z',
  status: 'SUCCESS',
  exit_code: 0,
  stdout: '',
  stderr: '',
  stdout_truncated: false,
  stderr_truncated: false,
  offline: false,
  synced: true,
  block_reason: '',
}

async function renderExecutions() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <Executions />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('执行列表触发方式筛选枚举（OBS-20261016-P17P18-04-SYSTEM-TRIGGER-NO-SOURCE）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    getPage.mockReset()
    get.mockReset()
    getPage.mockResolvedValue({ items: [EXECUTION], total: 1 })
    get.mockResolvedValue([])
  })

  it('不暴露系统无法产出的 system 触发枚举（一期无系统触发）', async () => {
    const page = await renderExecutions()

    const select = page.getByRole('combobox', {
      name: i18n.t('executions.trigger'),
    })
    await expect.element(select).toBeVisible()
    await userEvent.click(select)
    const options = await page.getByRole('option').all()
    const texts = options.map((option) => option.element().textContent)
    expect(texts).toContain(i18n.t('executions.allTriggers'))
    expect(texts).toContain('手动')
    expect(texts).toContain('调度')
    expect(texts).not.toContain('系统')
  })

  it('展示来源调度链接；历史记录显示历史标记', async () => {
    getPage.mockResolvedValue({
      items: [
        { ...EXECUTION, schedule_id: 'sch-1' },
        { ...EXECUTION, id: 'exec-2', schedule_id: undefined },
      ],
      total: 2,
    })
    get.mockImplementation(async (path: string) => {
      if (path === '/schedules')
        return [
          {
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
          },
        ]
      return []
    })

    const page = await renderExecutions()

    await expect
      .element(page.getByRole('link', { name: 'qa-schedule' }))
      .toHaveAttribute('href', '/schedules/sch-1')
    await expect
      .element(page.getByText(i18n.t('executions.historicalSource')))
      .toBeVisible()
  })
})
