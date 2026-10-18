import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { TaskEditor } from './index'

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

/** 已落库的 AND 任务：本地 + 远程两个条件 */
const AND_TASK = {
  id: 'task-1',
  name: 'qa-and-task',
  type: 'command',
  command: 'true',
  target: { type: 'node', node_ids: ['node-1'] },
  parameters: [],
  param_values: {},
  condition: {
    type: 'and',
    and: [
      {
        type: 'local',
        local: {
          metric: 'cpu_usage',
          operator: '>',
          value: '80',
        },
      },
      {
        type: 'remote',
        remote: {
          node_id: 'node-1',
          property: 'online',
          operator: '==',
          value: 'online',
        },
      },
    ],
  },
  timeout: 300,
  retry: 0,
  offline_policy: 'hub_online_required',
  enabled: true,
  revision: 3,
  updated_at: '2026-10-17T00:00:00Z',
  created_at: '2026-10-17T00:00:00Z',
}

async function renderEditor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <TaskEditor />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('任务 AND 条件摘要与清除（OBS-20261016-G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return [{ id: 'node-1', hostname: 'node-a' }]
      if (path === '/tasks/task-1') return AND_TASK
      return []
    })
  })

  it('已落库的 AND 条件组显示可读摘要（含节点名与 AND 连接），并可一键清除', async () => {
    window.history.replaceState({}, '', '/tasks/task-1/edit')
    const page = await renderEditor()

    const summary = page.getByTestId('condition-summary')
    await expect.element(summary).toBeVisible()
    await expect.element(summary).toHaveTextContent('cpu_usage > 80')
    await expect.element(summary).toHaveTextContent('AND')
    await expect.element(summary).toHaveTextContent('node-a.online == online')

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('tasks.clearConditions') })
    )
    await expect.element(summary).not.toBeInTheDocument()
  })

  it('新建任务时设置单条件立即出现摘要', async () => {
    window.history.replaceState({}, '', '/tasks/new')
    const page = await renderEditor()

    await expect
      .element(page.getByTestId('condition-summary'))
      .not.toBeInTheDocument()

    await userEvent.fill(page.getByPlaceholder('value').first(), '80')
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('tasks.setCondition') })
    )

    const summary = page.getByTestId('condition-summary')
    await expect.element(summary).toBeVisible()
    await expect.element(summary).toHaveTextContent('cpu_usage > 80')
  })
})
