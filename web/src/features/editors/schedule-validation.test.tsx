import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
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

describe('ScheduleEditor 保存前实时校验（OBS-20261014-SCHEDULE-INTERVAL-ZERO-SAVE-ENABLED / PAST-ONETIME-SAVE-ENABLED）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    window.history.replaceState({}, '', '/schedules/new')
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks') return [{ id: 'task-1', name: '部署任务' }]
      return []
    })
  })

  async function selectType(page: Awaited<ReturnType<typeof renderEditor>>, label: string) {
    await userEvent.click(page.getByRole('combobox', { name: '类型' }))
    await userEvent.click(page.getByRole('option', { name: label }))
  }

  it('间隔为 0 时显示内联错误并禁用保存，不发出请求', async () => {
    const page = await renderEditor()
    await selectType(page, '间隔')

    const interval = page.getByRole('spinbutton', {
      name: i18n.t('schedules.intervalSec'),
    })
    await userEvent.fill(interval, '0')

    await expect
      .element(page.getByText(i18n.t('schedules.intervalInvalid')))
      .toBeVisible()
    const save = page.getByRole('button', { name: i18n.t('common.save') })
    await expect.element(save).toBeDisabled()

    ;(save.element() as HTMLButtonElement).click()
    expect(post).not.toHaveBeenCalled()
  })

  it('间隔恢复合法值时错误消失、保存可用', async () => {
    const page = await renderEditor()
    await selectType(page, '间隔')

    const interval = page.getByRole('spinbutton', {
      name: i18n.t('schedules.intervalSec'),
    })
    await userEvent.fill(interval, '0')
    await expect
      .element(page.getByText(i18n.t('schedules.intervalInvalid')))
      .toBeVisible()

    await userEvent.fill(interval, '60')
    await expect
      .element(page.getByText(i18n.t('schedules.intervalInvalid')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .toBeEnabled()
  })

  it('一次性运行时间早于当前时间时显示内联错误并禁用保存', async () => {
    const page = await renderEditor()
    await selectType(page, '一次性')

    const runAt = page.getByLabelText(i18n.t('schedules.runAt'))
    await userEvent.fill(runAt, '2020-01-01T00:00')

    await expect
      .element(page.getByText(i18n.t('schedules.runAtPast')))
      .toBeVisible()
    const save = page.getByRole('button', { name: i18n.t('common.save') })
    await expect.element(save).toBeDisabled()

    ;(save.element() as HTMLButtonElement).click()
    expect(post).not.toHaveBeenCalled()
  })
})
