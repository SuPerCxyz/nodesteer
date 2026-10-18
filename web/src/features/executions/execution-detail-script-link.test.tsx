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
    Link: ({ children }: { children?: React.ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

const EXECUTION = {
  id: 'exec-1',
  task_id: 'task-1',
  task_revision: 4,
  script_id: 'sc-1',
  script_revision: 3,
  node_id: 'node-1',
  trigger_type: 'manual',
  scheduled_time: '',
  start_time: '2026-10-17T02:00:00Z',
  end_time: '2026-10-17T02:00:05Z',
  status: 'SUCCESS',
  exit_code: 0,
  stdout: 'ok',
  stderr: '',
  stdout_truncated: false,
  stderr_truncated: false,
  offline: false,
  synced: true,
  block_reason: '',
}

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ExecutionDetail />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('Execution 详情页 Script 回链（OBS-20261012-EXECUTION-SCRIPT-BACKLINK-GAP）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/executions/exec-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1') return EXECUTION
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'script' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/executions/exec-1/logs') return []
      return []
    })
  })

  it('显示 Script ID 与 revision，且可点击跳转 /scripts/{id}', async () => {
    const page = await renderDetail()

    const link = page.getByRole('link', { name: 'sc-1' })
    await expect.element(link).toBeVisible()
    await expect.element(link).toHaveAttribute('href', '/scripts/sc-1')
    await expect
      .element(page.getByText(i18n.t('executions.script')))
      .toBeVisible()
    await expect.element(page.getByText('r3')).toBeVisible()
  })

  it('无 script_id 的执行不渲染脚本回链', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/executions/exec-1')
        return { ...EXECUTION, script_id: '', script_revision: 0 }
      if (path === '/tasks/task-1')
        return { id: 'task-1', name: 'qa-task', type: 'command' }
      if (path === '/nodes/node-1')
        return { id: 'node-1', hostname: 'node-a', status: 'online' }
      if (path === '/executions/exec-1/logs') return []
      return []
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('executions.script')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByRole('link', { name: 'sc-1' }))
      .not.toBeInTheDocument()
  })
})
