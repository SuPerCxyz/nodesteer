import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AutomationRules } from './rules'

const { get, post, put, del, toastSuccess, toastError } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get, post, put, del } }
})

vi.mock('sonner', () => ({
  toast: { success: toastSuccess, error: toastError },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: '/monitoring' }),
    Link: ({ children, to }: { children?: ReactElement; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

// 规则页只加载目标、规则与调度，不挂载节点监控组件。
const TARGET = {
  id: 'target-1',
  name: 'API 健康检查',
  kind: 'http' as const,
  url: 'https://example.com/health',
  json_path: ['status'],
  interval_sec: 60,
  timeout_sec: 5,
  enabled: true,
}

const RULE = {
  id: 'rule-1',
  name: 'API 故障规则',
  target_id: TARGET.id,
  schedule_id: 'schedule-1',
  operator: 'status_eq',
  expected: '503',
  duration_sec: 0,
  cooldown_sec: 300,
  enabled: true,
}

const SCHEDULE = {
  id: 'schedule-1',
  name: '五分钟巡检',
  task_id: 'task-1',
}

const EVENTS = [
  {
    id: 'event-failed',
    status: 'failed',
    observed_at: '2026-10-17T02:00:00Z',
    execution_ids: ['execution-1'],
    error: 'node operation failed',
  },
  {
    id: 'event-unknown',
    status: 'unknown',
    observed_at: '2026-10-17T01:59:00Z',
    execution_ids: ['execution-2'],
  },
  {
    id: 'event-resolved',
    status: 'resolved',
    observed_at: '2026-10-17T01:58:00Z',
    execution_ids: [],
  },
]

function defaultGet(path: string) {
  if (path === '/monitoring/http-targets') return [TARGET]
  if (path === '/monitoring/rules') return [RULE]
  if (path === '/schedules') return [SCHEDULE]
  if (path === `/monitoring/rules/${RULE.id}/events`) return EVENTS
  return []
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SidebarProvider>
        <AutomationRules />
      </SidebarProvider>
    </QueryClientProvider>
  )
}

async function waitForPage() {
  const page = await renderPage()
  await expect
    .element(
      page.getByRole('button', {
        name: i18n.t('monitoring.createRule'),
        exact: true,
      })
    )
    .toBeVisible()
  return page
}

async function openRuleEvents() {
  const page = await waitForPage()
  await userEvent.click(
    page.getByRole('button', { name: i18n.t('monitoring.viewEdit') }).last()
  )
  await expect
    .element(
      page.getByText(i18n.t('monitoring.triggerHistory'), { exact: true })
    )
    .toBeVisible()
  return page
}

describe('联动规则页：列表与表单', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    del.mockReset()
    toastSuccess.mockReset()
    toastError.mockReset()
    get.mockImplementation(async (path: string) => defaultGet(path))
    post.mockResolvedValue({ ...RULE, id: 'rule-2', name: '新规则' })
    put.mockResolvedValue(RULE)
    del.mockResolvedValue(undefined)
  })

  it('展示规则列表与启用状态', async () => {
    const page = await waitForPage()

    await expect.element(page.getByText(i18n.t('rules.title'))).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('rules.listTitle')))
      .toBeVisible()
    await expect.element(page.getByText(RULE.name)).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('monitoring.enabled')))
      .toBeVisible()
  })

  it('创建规则提交目标、调度与默认条件', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('monitoring.createRule'),
        exact: true,
      })
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('monitoring.name') }),
      '新规则'
    )
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.target') })
    )
    await userEvent.click(page.getByRole('option', { name: TARGET.name }))
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.schedule') })
    )
    await userEvent.click(page.getByRole('option', { name: SCHEDULE.name }))

    await userEvent.click(
      page.getByRole('region').getByRole('button', {
        name: i18n.t('monitoring.createRule'),
        exact: true,
      })
    )

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      '/monitoring/rules',
      expect.objectContaining({
        name: '新规则',
        target_id: TARGET.id,
        schedule_id: SCHEDULE.id,
        operator: 'status_eq',
        expected: '503',
        enabled: false,
      })
    )
  })

  it('编辑规则并停用后提交 PUT', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('monitoring.name') }),
      '改名规则'
    )
    await userEvent.click(
      page.getByRole('checkbox', {
        name: i18n.t('monitoring.enableAutomaticExecution'),
      })
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.saveRule') })
    )

    await expect.poll(() => put.mock.calls.length).toBe(1)
    expect(put).toHaveBeenCalledWith(
      `/monitoring/rules/${RULE.id}`,
      expect.objectContaining({ name: '改名规则', enabled: false })
    )
  })
  it('deleting a viewed history object preserves another object draft', async () => {
    const second = { ...RULE, id: 'rule-b', name: 'Object B' }
    get.mockImplementation(async (path: string) =>
      path === '/monitoring/rules' ? [RULE, second] : defaultGet(path)
    )
    const page = await waitForPage()
    const firstRow = page.getByTestId('monitoring-rule-row-' + RULE.id)
    const secondRow = page.getByTestId('monitoring-rule-row-' + second.id)
    await userEvent.click(
      secondRow.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('monitoring.name') }),
      'Unsaved B'
    )
    await userEvent.click(
      firstRow.getByRole('button', { name: i18n.t('common.actions') })
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('webFix.history') })
    )
    await userEvent.click(
      firstRow.getByRole('button', { name: i18n.t('common.actions') })
    )
    await userEvent.click(
      page.getByRole('menuitem', {
        name: i18n.t('monitoring.delete'),
        exact: true,
      })
    )
    await userEvent.click(
      page.getByRole('alertdialog').getByRole('button', {
        name: i18n.t('monitoring.confirmAction'),
        exact: true,
      })
    )
    await expect.poll(() => del.mock.calls.length).toBe(1)
    await expect
      .element(page.getByRole('textbox', { name: i18n.t('monitoring.name') }))
      .toHaveValue('Unsaved B')
  })

  it('freezes editing and object changes while a delayed save is pending', async () => {
    let release!: (value: typeof RULE) => void
    put.mockImplementationOnce(
      () =>
        new Promise<typeof RULE>((resolve) => {
          release = resolve
        })
    )
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    const name = page.getByRole('textbox', { name: i18n.t('monitoring.name') })
    await userEvent.fill(name, 'Pending name')
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('monitoring.saveRule'),
        exact: true,
      })
    )
    await expect.poll(() => put.mock.calls.length).toBe(1)
    await expect.element(name).toBeDisabled()
    await expect
      .element(
        page.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
      )
      .toBeDisabled()
    await expect
      .element(
        page.getByRole('button', {
          name: i18n.t('monitoring.createRule'),
          exact: true,
        })
      )
      .toBeDisabled()
    release({ ...RULE, name: 'Pending name' })
    await expect.element(name).toBeEnabled()
    await expect.element(name).toHaveValue('Pending name')
  })
})

describe('联动规则页：触发记录确认', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    del.mockReset()
    toastSuccess.mockReset()
    toastError.mockReset()
    window.history.replaceState({}, '', '/monitoring')
    get.mockImplementation(async (path: string) => defaultGet(path))
    post.mockResolvedValue({})
    put.mockResolvedValue({})
    del.mockResolvedValue(undefined)
  })

  it('unknown 显示放弃按钮；取消不调用，确认调用 abandon-unknown；failed 不显示放弃', async () => {
    const page = await openRuleEvents()

    const abandon = page.getByRole('button', {
      name: i18n.t('monitoring.abandonUnknown'),
    })
    await expect.element(abandon).toBeVisible()
    expect(abandon.elements()).toHaveLength(1)

    const failedRow = document.querySelector(
      '[data-testid="monitoring-rule-event-event-failed"]'
    )
    const unknownRow = document.querySelector(
      '[data-testid="monitoring-rule-event-event-unknown"]'
    )
    expect(failedRow).toBeTruthy()
    expect(unknownRow).toBeTruthy()

    const buttonTexts = (row: Element) =>
      Array.from(row.querySelectorAll('button')).map((button) =>
        button.textContent?.trim()
      )
    expect(buttonTexts(failedRow!)).toContain(i18n.t('monitoring.acknowledge'))
    expect(buttonTexts(failedRow!)).not.toContain(
      i18n.t('monitoring.abandonUnknown')
    )
    expect(buttonTexts(unknownRow!)).toContain(
      i18n.t('monitoring.abandonUnknown')
    )

    await userEvent.click(abandon)
    await expect
      .element(
        page.getByText(i18n.t('monitoring.confirmAbandonUnknown'), {
          exact: true,
        })
      )
      .toBeVisible()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.cancel') })
    )
    await expect.element(page.getByRole('alertdialog')).not.toBeInTheDocument()
    expect(post).not.toHaveBeenCalled()

    await userEvent.click(abandon)
    await userEvent.click(page.getByRole('button', { name: /^确认$/ }))

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/rules/${RULE.id}/abandon-unknown`
    )
    expect(toastSuccess).toHaveBeenCalledWith(i18n.t('monitoring.abandoned'))
  })

  it('failed/unknown 显示确认按钮，failed 使用专门文案并调用 resolve-unknown', async () => {
    const page = await openRuleEvents()

    const acknowledge = page.getByRole('button', {
      name: i18n.t('monitoring.acknowledge'),
    })
    await expect.element(acknowledge.first()).toBeVisible()
    expect(acknowledge.elements()).toHaveLength(2)

    await userEvent.click(acknowledge.first())
    await expect
      .element(
        page.getByText(i18n.t('monitoring.confirmFailureHandled'), {
          exact: true,
        })
      )
      .toBeVisible()

    await userEvent.click(page.getByRole('button', { name: /^确认$/ }))

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/rules/${RULE.id}/resolve-unknown`
    )
    expect(toastSuccess).toHaveBeenCalledWith(i18n.t('monitoring.saved'))

    await expect.element(acknowledge.first()).toBeVisible()
    await userEvent.click(acknowledge.nth(1))
    await expect
      .element(
        page.getByText(
          i18n.t('monitoring.confirmThatTheUncertainExecutionHasBeenInspected'),
          { exact: true }
        )
      )
      .toBeVisible()

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.cancel') })
    )
    await expect.element(page.getByRole('alertdialog')).not.toBeInTheDocument()
    expect(post).toHaveBeenCalledTimes(1)
  })
})

describe('联动规则页：规则手动执行', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    del.mockReset()
    toastSuccess.mockReset()
    toastError.mockReset()
    window.history.replaceState({}, '', '/monitoring')
    get.mockImplementation(async (path: string) => defaultGet(path))
    post.mockResolvedValue({})
    put.mockResolvedValue({})
    del.mockResolvedValue(undefined)
  })

  it('取消不提交，确认后提交关联任务并反馈', async () => {
    const page = await waitForPage()
    const runNow = page.getByRole('button', {
      name: i18n.t('monitoring.runNow'),
    })

    await userEvent.click(runNow)
    await expect
      .element(
        page.getByText(i18n.t('monitoring.runTheAssociatedNodeTaskNow'), {
          exact: true,
        })
      )
      .toBeVisible()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.cancel') })
    )
    expect(post).not.toHaveBeenCalled()

    await userEvent.click(runNow)
    await userEvent.click(page.getByRole('button', { name: /^确认$/ }))

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith('/tasks/task-1/run', {
      schedule_id: SCHEDULE.id,
    })
    expect(toastSuccess).toHaveBeenCalledWith(
      i18n.t('monitoring.taskSubmitted')
    )
  })
})

describe('联动规则页：加载、错误与空态', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    del.mockReset()
    toastSuccess.mockReset()
    toastError.mockReset()
    window.history.replaceState({}, '', '/monitoring')
  })

  it('查询未完成时显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderPage()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('查询失败时显示错误与重试，重试会重新发起请求', async () => {
    get.mockRejectedValue(new Error('监控数据加载失败'))
    const page = await renderPage()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('监控数据加载失败')
    const before = get.mock.calls.length

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.retry') })
    )
    await expect.poll(() => get.mock.calls.length).toBeGreaterThan(before)
  })

  it('规则页空列表显示规则列表而不是错误或加载态', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/http-targets') return []
      if (path === '/monitoring/rules') return []
      if (path === '/schedules') return []
      return []
    })
    const page = await renderPage()

    await expect
      .element(page.getByText(i18n.t('rules.listTitle')))
      .toBeVisible()
    await expect.element(page.getByText(i18n.t('rules.noRules'))).toBeVisible()
    expect(
      page
        .getByText(i18n.t('rules.noRules'))
        .element()
        .closest('[data-card="list"]')
    ).not.toBeNull()
    await expect
      .element(page.getByText(i18n.t('monitoring.httpServices')))
      .not.toBeInTheDocument()
    expect(
      document.querySelectorAll('[data-testid^="monitoring-rule-row-"]')
    ).toHaveLength(0)
  })

  it('无权限时显示错误且不渲染规则表单', async () => {
    get.mockRejectedValue(new Error('permission denied'))
    const page = await renderPage()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('permission denied')
    await expect
      .element(
        page.getByRole('button', { name: i18n.t('monitoring.createRule') })
      )
      .not.toBeInTheDocument()
  })
})
