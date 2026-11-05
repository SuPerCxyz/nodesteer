import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { blockedNavigations } from '@/test-utils/setup'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage, userEvent } from 'vitest/browser'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Dashboard } from './index'

const { get, session } = vi.hoisted(() => ({
  get: vi.fn(),
  session: { role: 'administrator' },
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

vi.mock('@/stores/auth-store', () => {
  const state = {
    auth: {
      user: session,
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

const NODES = [
  {
    id: 'node-1',
    hostname: 'node-1',
    status: 'online',
    sync_status: 'synced',
  },
  {
    id: 'node-2',
    hostname: 'node-2',
    status: 'online',
    sync_status: 'synced',
  },
  {
    id: 'node-3',
    hostname: 'node-3',
    status: 'offline',
    sync_status: 'outdated',
  },
]

const EXECUTIONS = [
  {
    id: 'exec-running',
    task_id: 'task-1',
    node_id: 'node-1',
    status: 'RUNNING',
    start_time: '2026-10-17T02:00:00Z',
    end_time: '',
    trigger_type: 'manual',
  },
  {
    id: 'exec-failed',
    task_id: 'task-1',
    node_id: 'node-2',
    status: 'FAILED',
    start_time: '2026-10-17T01:00:00Z',
    end_time: '2026-10-17T01:01:00Z',
    trigger_type: 'schedule',
  },
  {
    id: 'exec-success',
    task_id: 'task-2',
    node_id: 'node-1',
    status: 'SUCCESS',
    start_time: '2026-10-17T00:00:00Z',
    end_time: '2026-10-17T00:01:00Z',
    trigger_type: 'manual',
  },
]

const TASKS = [
  { id: 'task-1', name: '任务一' },
  { id: 'task-2', name: '任务二' },
]

const OVERVIEW = [
  {
    node_id: 'node-1',
    hostname: 'node-1',
    status: 'online',
    state: 'ready',
    cpu: 42.5,
    memory: 61,
    disk: 95,
    load1: 1.2,
    load5: 0.8,
    load15: 0.6,
    network_rx_bps: 1024,
    network_tx_bps: 2048,
    network_total_bps: 3072,
    temperature_c: 45.5,
    uptime_seconds: 90061,
    alerts: 2,
    observed_at: '2026-10-17T02:05:00Z',
  },
  {
    node_id: 'node-2',
    hostname: 'node-2',
    status: 'online',
    state: 'ready',
    cpu: 10,
    memory: 85,
    disk: 70,
    load1: 0.5,
    load5: 0.4,
    load15: 0.3,
    network_rx_bps: 524288,
    network_tx_bps: 524288,
    temperature_c: null,
    uptime_seconds: 3660,
    alerts: 1,
    observed_at: '2026-10-17T02:05:00Z',
  },
]

let overviewCalls = 0

function defaultGet(path: string) {
  if (path === '/nodes') return NODES
  if (path === '/executions?limit=50') return EXECUTIONS
  if (path === '/applications') return []
  if (path === '/schedules') return []
  if (path === '/tasks') return TASKS
  if (path === '/monitoring/nodes/overview') return { nodes: OVERVIEW }
  return []
}

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <SidebarInset>
          <Dashboard />
        </SidebarInset>
      </QueryClientProvider>
    </SidebarProvider>
  )
}

afterEach(async () => {
  await browserPage.viewport(1280, 720)
})

describe('Dashboard 自动化与节点监控全宽分区总览', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    session.role = 'administrator'
    get.mockReset()
    overviewCalls = 0
    get.mockImplementation(async (path: string) => defaultGet(path))
  })

  it('展示自动化与监控指标、运行任务和节点表', async () => {
    const page = await renderDashboard()

    const automation = page.getByTestId('dashboard-automation-card')
    const monitoring = page.getByTestId('dashboard-monitoring-card')
    const overviewCard = page.getByTestId('dashboard-monitoring-overview')
    await expect.element(automation).toBeVisible()
    await expect.element(monitoring).toBeVisible()
    await expect.element(overviewCard).toBeVisible()
    await expect
      .element(automation.getByText(i18n.t('dashboard.automationTitle')))
      .toBeVisible()
    await expect
      .element(overviewCard.getByText(i18n.t('dashboard.monitoringOverview')))
      .toBeVisible()
    await expect
      .element(automation.getByRole('link', { name: i18n.t('common.viewAll') }))
      .toHaveAttribute('href', '/executions')
    await expect
      .element(
        overviewCard.getByRole('link', {
          name: i18n.t('dashboard.viewMonitoring'),
        })
      )
      .toHaveAttribute('href', '/monitoring')

    await expect
      .element(page.getByTestId('automation-running'))
      .toHaveTextContent('1')
    await expect
      .element(page.getByTestId('automation-failed'))
      .toHaveTextContent('1')
    await expect
      .element(page.getByTestId('automation-success-rate'))
      .toHaveTextContent('50%')
    await expect
      .element(page.getByTestId('monitoring-online'))
      .toHaveTextContent('2')
    await expect
      .element(page.getByTestId('monitoring-monitored'))
      .toHaveTextContent('2')
    await expect
      .element(page.getByTestId('monitoring-alerts'))
      .toHaveTextContent(i18n.t('webFix.alertsPending'))

    await expect.element(automation.getByText('任务一')).toBeVisible()
    await expect
      .element(automation.getByRole('link', { name: /任务一/ }))
      .toHaveAttribute('href', '/executions/exec-running')

    const nodeRow = page.getByTestId('node-monitoring-row-node-1')
    await expect.element(nodeRow).toHaveTextContent('42.5%')
    await expect.element(nodeRow).toHaveTextContent('61.0%')
    await expect.element(nodeRow).toHaveTextContent('95.0%')
    await expect.element(nodeRow).toHaveTextContent('1.20 / 0.80 / 0.60')
    await expect.element(nodeRow).toHaveTextContent('1.0 KB/s')
    await expect.element(nodeRow).toHaveTextContent('2.0 KB/s')
    await expect.element(nodeRow).toHaveTextContent('45.5 °C')
    await expect.element(nodeRow).toHaveTextContent('1 天 1 小时')
    await expect
      .element(page.getByTestId('dashboard-cpu-node-1'))
      .toHaveAttribute('data-tone', 'success')
    await expect
      .element(page.getByTestId('dashboard-memory-node-2'))
      .toHaveAttribute('data-tone', 'warning')
    await expect
      .element(page.getByTestId('dashboard-disk-node-1'))
      .toHaveAttribute('data-tone', 'danger')
    await expect
      .element(
        page
          .getByTestId('dashboard-monitoring-overview')
          .getByRole('link', { name: 'node-1' })
      )
      .toHaveAttribute('href', '/monitoring/nodes/node-1')
    await expect
      .element(
        page
          .getByTestId('dashboard-monitoring-overview')
          .getByRole('link', { name: i18n.t('dashboard.viewDetails') })
          .first()
      )
      .toHaveAttribute('href', '/monitoring/nodes/node-1')

    const node2Row = page.getByTestId('node-monitoring-row-node-2')
    await expect.element(node2Row).toHaveTextContent('512.0 KB/s')
    await expect.element(node2Row).toHaveTextContent('1 小时')
    await expect.element(node2Row).toHaveTextContent('—')

    expect(
      get.mock.calls.filter(([path]) => path === '/monitoring/nodes/overview')
    ).toHaveLength(1)
  })

  it('Dashboard 节点行支持点击与回车进入详情', async () => {
    const page = await renderDashboard()
    const row = page.getByRole('group', { name: 'node-1' })
    await expect.element(row).toBeVisible()

    blockedNavigations.length = 0
    await userEvent.click(page.getByTestId('dashboard-cpu-node-1'))
    await expect
      .poll(() =>
        blockedNavigations.some((url) =>
          url.endsWith('/monitoring/nodes/node-1')
        )
      )
      .toBe(true)

    blockedNavigations.length = 0
    row.element().focus()
    await userEvent.keyboard('{Enter}')
    await expect
      .poll(() =>
        blockedNavigations.some((url) =>
          url.endsWith('/monitoring/nodes/node-1')
        )
      )
      .toBe(true)
  })

  it('没有运行任务和节点数据时显示空态与真实空指标', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview') return { nodes: [] }
      return []
    })
    const page = await renderDashboard()

    await expect
      .element(page.getByText(i18n.t('dashboard.noRunning')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('dashboard.noMonitoringData')))
      .toBeVisible()
    await expect
      .element(page.getByTestId('monitoring-monitored'))
      .toHaveTextContent('0')
    await expect
      .element(page.getByTestId('monitoring-alerts'))
      .toHaveTextContent(i18n.t('webFix.alertsPending'))
    await expect
      .element(page.getByTestId('automation-success-rate'))
      .toHaveTextContent('-')
  })

  it('加载中显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderDashboard()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('监控数据失败显示错误与重试，重试后恢复节点表', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview') {
        overviewCalls++
        if (overviewCalls === 1) throw new Error('监控总览加载失败')
        return { nodes: OVERVIEW }
      }
      return defaultGet(path)
    })
    const page = await renderDashboard()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('监控总览加载失败')
    await expect
      .element(page.getByTestId('monitoring-monitored'))
      .toHaveTextContent('—')

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.retry') })
    )
    await expect.element(page.getByRole('alert')).not.toBeInTheDocument()
    await expect
      .element(
        page
          .getByTestId('dashboard-monitoring-overview')
          .getByRole('link', { name: 'node-1' })
      )
      .toBeVisible()
  })

  for (const role of ['operator', 'viewer']) {
    it(`${role} sees automation without administrator monitoring links or queries`, async () => {
      session.role = role
      const page = await renderDashboard()
      await expect
        .element(page.getByTestId('dashboard-automation-card'))
        .toBeVisible()
      expect(
        page.getByTestId('dashboard-monitoring-overview').elements()
      ).toHaveLength(0)
      expect(
        get.mock.calls.some(([path]) => path === '/monitoring/nodes/overview')
      ).toBe(false)
    })
  }
  it('keeps monitoring and automation in full-width vertical sections at desktop and mobile widths', async () => {
    const page = await renderDashboard()
    await expect
      .element(page.getByTestId('dashboard-monitoring-overview'))
      .toBeVisible()
    for (const width of [1366, 1920, 390]) {
      await browserPage.viewport(width, 800)
      const main = document.querySelector('main[data-layout]') as HTMLElement
      const monitoring = page
        .getByTestId('dashboard-monitoring-overview')
        .element()
        .getBoundingClientRect()
      const automation = page
        .getByTestId('dashboard-automation-card')
        .element()
        .getBoundingClientRect()
      const bounds = main.getBoundingClientRect()
      const style = getComputedStyle(main)
      const left = bounds.left + parseFloat(style.paddingLeft)
      const right = bounds.right - parseFloat(style.paddingRight)
      expect(Math.abs(monitoring.left - left)).toBeLessThan(1.5)
      expect(Math.abs(monitoring.right - right)).toBeLessThan(1.5)
      expect(Math.abs(automation.width - monitoring.width)).toBeLessThan(1.5)
      expect(monitoring.top).toBeGreaterThanOrEqual(automation.bottom)
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
        window.innerWidth + 1
      )
    }
  })
})
