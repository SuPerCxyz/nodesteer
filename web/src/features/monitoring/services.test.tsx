import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ServiceMonitoring } from './services'

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

vi.mock('@/stores/auth-store', () => {
  const state = {
    auth: {
      user: { role: 'administrator' },
      reset: vi.fn(),
    },
  }
  return {
    useAuthStore: (selector?: (state: unknown) => unknown) =>
      selector ? selector(state) : state,
  }
})

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({ children, to }: { children?: ReactNode; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

const OBSERVED_AT = new Date().toISOString()

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

const NODE = {
  id: 'node-1',
  agent_id: 'agent-1',
  hostname: 'node-a',
  ip: '192.0.2.10',
  os: 'linux',
  arch: 'amd64',
  agent_version: '1.0.0',
  deployment_mode: 'native',
  host_integration: false,
  status: 'online',
  labels: {},
  capabilities: {},
  global_revision: 1,
  sync_status: 'synced',
  last_seen: OBSERVED_AT,
  first_seen: OBSERVED_AT,
}

const LATEST = {
  items: [
    {
      id: 1,
      target_id: TARGET.id,
      observed_at: OBSERVED_AT,
      status_code: 200,
      value: { status: 'ok' },
      duration_ns: 1_500_000,
    },
  ],
  next: '',
}

const OBSERVATIONS = [
  {
    id: 11,
    observed_at: OBSERVED_AT,
    status_code: 200,
    value: { status: 'ok' },
    duration_ns: 1_500_000,
  },
  {
    id: 12,
    observed_at: OBSERVED_AT,
    status_code: 200,
    value: { status: 'ok' },
  },
]

function defaultGet(path: string) {
  if (path === '/monitoring/http-targets') return [TARGET]
  if (path.startsWith('/monitoring/latest?')) return LATEST
  if (path === '/nodes') return [NODE]
  if (path === `/monitoring/http-targets/${TARGET.id}/observations`)
    return OBSERVATIONS
  return []
}

function renderServices() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ServiceMonitoring />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

async function waitForPage() {
  const page = await renderServices()
  await expect
    .element(
      page.getByRole('button', { name: i18n.t('monitoring.createTarget') })
    )
    .toBeVisible()
  return page
}

describe('服务监控页', () => {
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
    post.mockResolvedValue({ ...TARGET, id: 'target-2', name: '新目标' })
    put.mockResolvedValue(TARGET)
    del.mockResolvedValue(undefined)
  })

  it('展示目标列表、最近观察和耗时', async () => {
    const page = await waitForPage()

    await expect
      .element(page.getByText(i18n.t('monitoring.httpServices')))
      .toBeVisible()
    await expect.element(page.getByText(TARGET.name)).toBeVisible()
    await expect.element(page.getByText(TARGET.url)).toBeVisible()
    await expect.element(page.getByText('HTTP 200')).toBeVisible()

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    await expect
      .element(page.getByText(i18n.t('monitoring.recentObservations')))
      .toBeVisible()
    await expect.element(page.getByText('1.5 ms')).toBeVisible()
  })

  it('创建目标提交表单并反馈成功', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('monitoring.createTarget'),
        exact: true,
      })
    )
    const name = page.getByRole('textbox', { name: i18n.t('monitoring.name') })
    await userEvent.fill(name.first(), '新目标')
    await userEvent.fill(
      page.getByRole('textbox', { name: 'URL' }),
      'https://example.com/new-health'
    )

    await userEvent.click(
      page
        .getByRole('region')
        .getByRole('button', {
          name: i18n.t('monitoring.createTarget'),
          exact: true,
        })
    )

    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      '/monitoring/http-targets',
      expect.objectContaining({
        name: '新目标',
        url: 'https://example.com/new-health',
        interval_sec: 60,
        timeout_sec: 5,
        enabled: true,
      })
    )
    expect(toastSuccess).toHaveBeenCalledWith(i18n.t('monitoring.saved'))
  })

  it('编辑目标提交 PUT，失败时显示错误反馈', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    const name = page.getByRole('textbox', { name: i18n.t('monitoring.name') })
    await userEvent.fill(name.first(), '改名目标')
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.saveTarget') })
    )

    await expect.poll(() => put.mock.calls.length).toBe(1)
    expect(put).toHaveBeenCalledWith(
      `/monitoring/http-targets/${TARGET.id}`,
      expect.objectContaining({ name: '改名目标' })
    )

    put.mockRejectedValueOnce(new Error('保存失败'))
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.saveTarget') })
    )
    await expect.poll(() => toastError.mock.calls.length).toBe(1)
    expect(toastError).toHaveBeenCalledWith('保存失败')
  })

  it('删除目标必须经过确认，确认后调用删除接口', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.delete') })
    )

    await expect
      .element(
        page.getByText(i18n.t('monitoring.deleteTargetAndItsHistory'), {
          exact: true,
        })
      )
      .toBeVisible()

    await userEvent.click(page.getByRole('button', { name: /^确认$/ }))
    await expect.poll(() => del.mock.calls.length).toBe(1)
    expect(del).toHaveBeenCalledWith(`/monitoring/http-targets/${TARGET.id}`)
  })

  it('加载中显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderServices()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('查询失败显示错误与重试', async () => {
    get.mockRejectedValue(new Error('服务监控加载失败'))
    const page = await renderServices()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('服务监控加载失败')
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.retry') }))
      .toBeVisible()
  })

  it('空列表仍显示创建表单而不是错误或加载态', async () => {
    get.mockImplementation(async (path: string) => {
      if (path.startsWith('/monitoring/latest?')) return { items: [], next: '' }
      return []
    })
    const page = await renderServices()

    await expect
      .element(
        page.getByRole('button', { name: i18n.t('monitoring.createTarget') })
      )
      .toBeVisible()
    expect(
      document.querySelectorAll('[data-testid^="monitoring-target-row-"]')
    ).toHaveLength(0)
  })
  it('deleting a viewed history object preserves another object draft', async () => {
    const second = { ...TARGET, id: 'target-b', name: 'Object B' }
    get.mockImplementation(async (path: string) =>
      path === '/monitoring/http-targets' ? [TARGET, second] : defaultGet(path)
    )
    const page = await waitForPage()
    const firstRow = page.getByTestId('monitoring-target-row-' + TARGET.id)
    const secondRow = page.getByTestId('monitoring-target-row-' + second.id)
    await userEvent.click(
      secondRow.getByRole('button', { name: i18n.t('monitoring.viewEdit') })
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('monitoring.name') }),
      'Unsaved B'
    )
    await userEvent.click(
      firstRow.getByRole('button', { name: i18n.t('webFix.history') })
    )
    await userEvent.click(
      firstRow.getByRole('button', {
        name: i18n.t('monitoring.delete'),
        exact: true,
      })
    )
    await userEvent.click(
      page
        .getByRole('alertdialog')
        .getByRole('button', {
          name: i18n.t('monitoring.confirmAction'),
          exact: true,
        })
    )
    await expect.poll(() => del.mock.calls.length).toBe(1)
    await expect
      .element(page.getByRole('textbox', { name: i18n.t('monitoring.name') }))
      .toHaveValue('Unsaved B')
  })
})
