import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { RunTask } from './index'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get, post } }
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

const TASK = {
  id: 'task-1',
  name: 'qa-run-task',
  type: 'command',
  command: 'true',
  parameters: [],
  param_values: {},
  enabled: true,
  revision: 1,
  updated_at: '2026-10-17T00:00:00Z',
  created_at: '2026-10-17T00:00:00Z',
}

const NODES = [
  { id: 'node-1', hostname: 'node-a', status: 'online' },
  { id: 'node-2', hostname: 'node-b', status: 'online' },
]

async function renderRun() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <RunTask />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('任务试运行请求级目标与策略', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    // 保持请求在途，避免测试中跳转到执行记录页
    post.mockImplementation(() => new Promise(() => {}))
    window.history.replaceState({}, '', '/tasks/task-1/run')
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks/task-1') return TASK
      if (path === '/nodes') return NODES
      if (path === '/groups') return []
      return []
    })
  })

  it('未选择目标时禁止提交', async () => {
    const page = await renderRun()

    const run = page.getByRole('button', { name: i18n.t('tasks.runNow') })
    await expect.element(run).toBeDisabled()
    expect(run.element().closest('[data-slot="card-footer"]')).not.toBeNull()
    await expect
      .element(page.getByText(i18n.t('tasks.targetRequired')))
      .toBeVisible()
    expect(post).not.toHaveBeenCalled()
  })

  it('提交 target 与 timeout/retry/run_user/offline_policy', async () => {
    const page = await renderRun()

    await userEvent.click(page.getByRole('checkbox', { name: /node-a/ }))
    await userEvent.fill(
      page.getByRole('spinbutton', { name: i18n.t('strategy.timeoutSec') }),
      '120'
    )
    await userEvent.fill(
      page.getByRole('spinbutton', { name: i18n.t('strategy.retry') }),
      '2'
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('strategy.runUser') }),
      'deploy'
    )
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('strategy.offlinePolicy') })
    )
    await userEvent.click(page.getByRole('option', { name: '允许离线' }))

    const run = page.getByRole('button', { name: i18n.t('tasks.runNow') })
    await expect.element(run).toBeEnabled()
    await userEvent.click(run)
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('tasks.runNow') }).last()
    )

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith('/tasks/task-1/run', {
      target: { type: 'node', node_ids: ['node-1'] },
      params: {},
      timeout: 120,
      retry: 2,
      run_user: 'deploy',
      offline_policy: 'allow_offline',
    })
  })
})
