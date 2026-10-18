import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ScheduleEditor } from './index'

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

async function renderEditor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ScheduleEditor />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('调度新建页无效 task_id 深链（OBS-20261014-INVALID-TASK-DEEPLINK-NO-ERROR）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks') return [{ id: 'task-1', name: '部署任务' }]
      return []
    })
  })

  it('task_id 不存在时显示明确错误与返回列表入口，不渲染保存表单', async () => {
    window.history.replaceState({}, '', '/schedules/new?task_id=missing-task')
    const page = await renderEditor()

    await expect
      .element(page.getByText(i18n.t('schedules.taskNotFound')))
      .toBeVisible()
    // 返回任务列表入口（Link 在测试中被 mock 为 <a>，断言文案存在）
    await expect
      .element(page.getByText(`← ${i18n.t('tasks.title')}`))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .not.toBeInTheDocument()
    // 只读展示：不应发出任何创建请求
    expect(post).not.toHaveBeenCalled()
  })

  it('task_id 存在时保留原有「任务已锁定」预填行为', async () => {
    window.history.replaceState({}, '', '/schedules/new?task_id=task-1')
    const page = await renderEditor()

    await expect
      .element(page.getByText(i18n.t('schedules.taskPrefilled')))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .toBeEnabled()
  })
})
