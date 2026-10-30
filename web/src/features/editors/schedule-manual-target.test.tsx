import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ScheduleEditor } from './index'

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

describe('调度计划 manual 类型与目标校验', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/schedules/new')
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks') return [{ id: 'task-1', name: '部署任务' }]
      if (path === '/nodes')
        return [{ id: 'node-1', hostname: 'node-a', status: 'online' }]
      if (path === '/groups') return []
      return []
    })
  })

  it('默认未选择目标时显示校验错误并禁用保存', async () => {
    const page = await renderEditor()

    await expect
      .element(page.getByText(i18n.t('schedules.targetRequired')))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .toBeDisabled()
  })

  it('manual 隐藏全部触发时间字段并显示仅手动说明，选择目标后允许保存', async () => {
    const page = await renderEditor()

    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('schedules.triggerType') })
    )
    await userEvent.click(page.getByRole('option', { name: '仅手动' }))

    await expect
      .element(page.getByText(i18n.t('schedules.manualHint')))
      .toBeVisible()
    await expect
      .element(page.getByLabelText(i18n.t('schedules.cronExpression')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByLabelText(i18n.t('schedules.intervalSec')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByLabelText(i18n.t('schedules.runAt')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByLabelText(i18n.t('schedules.timezone')))
      .not.toBeInTheDocument()

    await userEvent.click(page.getByRole('checkbox', { name: /node-a/ }))
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .toBeEnabled()
  })
})
