import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import type { Task } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Tasks } from './index'

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

const TASK: Task = {
  id: 'task-qa-1',
  name: 'qa-task',
  type: 'command',
  command: 'echo hi',
  parameters: [],
  param_values: {},
  enabled: true,
  revision: 1,
  updated_at: '2026-10-17T00:00:00Z',
  created_at: '2026-10-17T00:00:00Z',
}

async function renderTasks() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  const page = await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <Tasks />
      </QueryClientProvider>
    </SidebarProvider>
  )
  const workspace = page.getByTestId('tasks-workspace').element()
  expect(workspace.getAttribute('data-slot')).toBe('card')
  expect(workspace.querySelectorAll('[data-slot="card"]')).toHaveLength(0)
  expect(getComputedStyle(workspace).borderTopWidth).toBe('1px')
  return page
}

describe('Tasks 列表 Loading/Empty/Error 三态', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    localStorage.clear()
  })

  it('初始请求未完成时显示 Loading，不显示「没有结果」；空结果只显示空态', async () => {
    let resolveTasks: ((value: Task[]) => void) | undefined
    const pending = new Promise<Task[]>((resolve) => {
      resolveTasks = resolve
    })
    get.mockImplementation((path: string) =>
      path === '/tasks' ? pending : Promise.resolve([])
    )
    const page = await renderTasks()

    // 请求挂起：必须是加载态，不能把空数组当「没有结果」
    await expect.element(page.getByText('加载中...')).toBeVisible()
    await expect.element(page.getByText('没有结果。')).not.toBeInTheDocument()

    resolveTasks!([])
    // 请求完成且真空：显示空态，仍不能显示「没有结果」
    await expect.element(page.getByText(i18n.t('tasks.noTasks'))).toBeVisible()
    await expect.element(page.getByText('没有结果。')).not.toBeInTheDocument()
  })

  it('请求完成后为空列表显示空态，而不是「没有结果」', async () => {
    get.mockResolvedValue([])
    const page = await renderTasks()

    await expect.element(page.getByText(i18n.t('tasks.noTasks'))).toBeVisible()
    await expect.element(page.getByText('没有结果。')).not.toBeInTheDocument()
  })

  it('请求失败显示错误与重试入口，重试成功后恢复列表', async () => {
    let calls = 0
    get.mockImplementation(async (path: string) => {
      if (path !== '/tasks') return []
      calls++
      if (calls === 1) throw new Error('tasks unavailable')
      return [TASK]
    })
    const page = await renderTasks()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('tasks unavailable')
    await expect.element(page.getByText('没有结果。')).not.toBeInTheDocument()

    await userEvent.click(page.getByRole('button', { name: '重试' }))
    await expect.element(page.getByRole('alert')).not.toBeInTheDocument()
    await expect.element(page.getByText('qa-task')).toBeVisible()
    expect(calls).toBeGreaterThan(1)
  })

  it('列表不再显示目标、超时与离线策略列，改为关联调度数量', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks') return [TASK]
      if (path === '/schedules')
        return [{ id: 'sch-1', task_id: TASK.id, name: 'qa-schedule' }]
      return []
    })
    const page = await renderTasks()

    await expect.element(page.getByText('qa-task')).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('tasks.relatedSchedules')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('targets.type')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByText(i18n.t('strategy.timeoutSec')))
      .not.toBeInTheDocument()
  })
})
