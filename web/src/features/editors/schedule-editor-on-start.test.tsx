import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ScheduleEditor } from './index'

const get = vi.hoisted(() => vi.fn())

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

const TASKS = [{ id: 'task-1', name: '部署任务' }]

const ON_START_SCHEDULE = {
  id: 'sch-1',
  task_id: 'task-1',
  revision: 1,
  type: 'on_start',
  expression: '',
  interval_sec: 0,
  run_at: '',
  timezone: 'Asia/Shanghai',
  execution_owner: 'agent',
  offline_policy: 'allow_offline',
  misfire_policy: 'run_once',
  enabled: true,
  created_at: '2026-09-24T00:00:00Z',
  updated_at: '2026-09-24T00:00:00Z',
}

function mockApi(schedule: unknown = []) {
  get.mockImplementation((path: string) => {
    if (path === '/tasks') return Promise.resolve(TASKS)
    if (path.startsWith('/schedules/')) return Promise.resolve(schedule)
    return Promise.resolve([])
  })
}

async function renderEditor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ScheduleEditor />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('ScheduleEditor on_start 类型', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/schedules/new')
  })

  it('选中 on_start 隐藏全部触发时间与时区字段，并显示说明文案', async () => {
    mockApi()
    const { getByRole, getByLabelText, getByText } = await renderEditor()

    // 默认 cron：表达式与时区可见
    await expect.element(getByLabelText('Cron 表达式')).toBeVisible()
    await expect.element(getByLabelText('时区')).toBeVisible()

    await userEvent.click(getByRole('combobox', { name: '类型' }))
    await userEvent.click(getByRole('option', { name: '节点启动时' }))

    await expect.element(getByLabelText('Cron 表达式')).not.toBeInTheDocument()
    await expect.element(getByLabelText('时区')).not.toBeInTheDocument()
    await expect.element(getByLabelText('间隔（秒）')).not.toBeInTheDocument()
    await expect.element(getByLabelText('运行时间')).not.toBeInTheDocument()
    await expect
      .element(getByText('节点每次启动后触发一次，无固定触发时刻。'))
      .toBeVisible()
    // 预览不展示时刻与时区标注
    await expect.element(getByText(/时区：/)).not.toBeInTheDocument()
  })

  it('编辑既有 on_start 调度：选项回显且时间字段不渲染', async () => {
    mockApi(ON_START_SCHEDULE)
    window.history.replaceState({}, '', '/schedules/sch-1')
    const { getByRole, getByLabelText } = await renderEditor()

    await expect
      .element(getByRole('combobox', { name: '类型' }))
      .toHaveTextContent('节点启动时')
    await expect.element(getByLabelText('Cron 表达式')).not.toBeInTheDocument()
    await expect.element(getByLabelText('时区')).not.toBeInTheDocument()
    await expect.element(getByLabelText('运行时间')).not.toBeInTheDocument()
    await expect.element(getByLabelText('间隔（秒）')).not.toBeInTheDocument()
  })
})
