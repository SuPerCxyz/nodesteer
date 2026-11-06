import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { MonitoringAlerts } from './alerts'

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
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({ children, to }: { children?: ReactNode; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

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
  last_seen: '2026-10-17T00:00:00Z',
  first_seen: '2026-10-01T00:00:00Z',
}

const CHANNEL = {
  id: 'channel-1',
  name: 'Ops webhook',
  type: 'webhook',
  config: { url_env: 'OPS_WEBHOOK_URL', timeout_sec: 10 },
  enabled: true,
}

const RULE = {
  id: 'rule-1',
  name: 'High CPU',
  description: '',
  node_id: 'node-1',
  metric: 'cpu.usage',
  series: 'cpu.usage|cpu=all',
  operator: 'gt',
  threshold: 90,
  duration_sec: 60,
  recovery_sec: 30,
  cooldown_sec: 300,
  severity: 'critical',
  channels: ['channel-1'],
  enabled: true,
}

const EVENT = {
  id: 'event-1',
  rule_id: 'rule-1',
  rule_name: 'High CPU',
  node_id: 'node-1',
  node_hostname: 'node-a',
  metric: 'cpu.usage',
  series: 'cpu.usage|cpu=all',
  operator: 'gt',
  status: 'firing',
  severity: 'critical',
  value: 95,
  threshold: 90,
  started_at: '2026-10-17T02:00:00Z',
  fired_at: '2026-10-17T02:01:00Z',
  resolved_at: '0001-01-01T00:00:00Z',
  acknowledged_at: '0001-01-01T00:00:00Z',
  silenced_until: '0001-01-01T00:00:00Z',
  message: '',
  error: '',
}

const ACKNOWLEDGED_EVENT = {
  ...EVENT,
  id: 'event-2',
  rule_name: 'Acknowledged CPU',
  status: 'resolved',
  resolved_at: '2026-10-17T02:03:00Z',
  acknowledged_at: '2026-10-17T02:02:00Z',
}

const EVENT_LIST = {
  events: [EVENT, ACKNOWLEDGED_EVENT],
  total: 2,
  limit: 20,
  offset: 0,
}

function defaultGet(path: string) {
  if (path === '/monitoring/alerts/rules') return [RULE]
  if (path === '/monitoring/notification-channels') return [CHANNEL]
  if (path === '/nodes') return [NODE]
  if (path.startsWith('/monitoring/alerts/events?')) return EVENT_LIST
  return []
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <MonitoringAlerts />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

async function waitForPage() {
  const page = await renderPage()
  await expect.element(page.getByText(RULE.name)).toBeVisible()
  return page
}

describe('告警页', () => {
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
    post.mockResolvedValue(RULE)
    put.mockResolvedValue(RULE)
    del.mockResolvedValue(undefined)
  })

  it('规则列表、创建、启停、静默与删除', async () => {
    const page = await waitForPage()

    await expect.element(page.getByText(RULE.name)).toBeVisible()
    await expect.element(page.getByText('node-a')).toBeVisible()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.newRule'), exact: true })
    )

    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('alerts.name') }),
      'New alert'
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.createRule') })
    )
    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      '/monitoring/alerts/rules',
      expect.objectContaining({ name: 'New alert', metric: 'cpu.usage' })
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('alerts.disable') })
    )
    await expect.poll(() => post.mock.calls.length).toBe(2)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/alerts/rules/${RULE.id}/enable`,
      { enabled: false }
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('alerts.silence') }).first()
    )
    const dialog = page.getByRole('dialog')
    await expect.element(dialog).toBeVisible()
    await userEvent.click(
      dialog.getByRole('button', {
        name: i18n.t('alerts.silence'),
        exact: true,
      })
    )
    await expect.poll(() => post.mock.calls.length).toBe(3)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/alerts/rules/${RULE.id}/silence`,
      { duration_sec: 3600 }
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('alerts.delete') }).first()
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.confirm') })
    )
    await expect.poll(() => del.mock.calls.length).toBe(1)
    expect(del).toHaveBeenCalledWith(`/monitoring/alerts/rules/${RULE.id}`)
  })

  it('通知渠道创建、测试发送与删除', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('tab', { name: i18n.t('alerts.channelsTab') })
    )
    await expect.element(page.getByText(CHANNEL.name)).toBeVisible()
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('alerts.newChannel'),
        exact: true,
      })
    )

    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('alerts.name') }),
      'New channel'
    )
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('alerts.urlEnv') }),
      'NEW_WEBHOOK_URL'
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.createChannel') })
    )
    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      '/monitoring/notification-channels',
      expect.objectContaining({
        name: 'New channel',
        type: 'webhook',
        config: expect.objectContaining({ url_env: 'NEW_WEBHOOK_URL' }),
      })
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('alerts.test') })
    )
    await expect.poll(() => post.mock.calls.length).toBe(2)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/notification-channels/${CHANNEL.id}/test`
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await userEvent.click(
      page.getByRole('menuitem', { name: i18n.t('alerts.delete') })
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.confirm') })
    )
    await expect.poll(() => del.mock.calls.length).toBe(1)
    expect(del).toHaveBeenCalledWith(
      `/monitoring/notification-channels/${CHANNEL.id}`
    )
  })

  it('事件活动/历史过滤与确认', async () => {
    const page = await waitForPage()
    await userEvent.click(
      page.getByRole('tab', { name: i18n.t('alerts.eventsTab') })
    )
    await expect.element(page.getByText(RULE.name)).toBeVisible()
    expect(
      page.getByRole('button', { name: i18n.t('alerts.ack') }).elements()
    ).toHaveLength(1)
    await expect.element(page.getByText(/0001/)).not.toBeInTheDocument()
    const acknowledgedRow = page.getByTestId('alert-event-row-event-2')
    await expect
      .element(acknowledgedRow)
      .toHaveTextContent(i18n.t('alerts.acknowledged'))
    expect(
      acknowledgedRow
        .getByRole('button', { name: i18n.t('alerts.ack') })
        .elements()
    ).toHaveLength(0)

    const activeCall = get.mock.calls
      .map(([path]) => String(path))
      .find((path) => path.startsWith('/monitoring/alerts/events?'))
    expect(activeCall).toContain('state=active')

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.ack') })
    )
    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith(
      `/monitoring/alerts/events/${EVENT.id}/ack`
    )

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('alerts.historyEvents') })
    )
    await expect
      .poll(() =>
        get.mock.calls.some(
          ([path]) =>
            String(path).startsWith('/monitoring/alerts/events?') &&
            String(path).includes('state=history')
        )
      )
      .toBe(true)
  })

  it('加载中显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderPage()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('查询失败显示错误与重试，空列表显示空态', async () => {
    get.mockRejectedValue(new Error('permission denied'))
    const page = await renderPage()
    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('permission denied')

    get.mockImplementation(async (path: string) => {
      if (path.startsWith('/monitoring/alerts/events?'))
        return { events: [], total: 0, limit: 20, offset: 0 }
      return []
    })
    const emptyPage = await renderPage()
    await expect
      .element(emptyPage.getByText(i18n.t('alerts.noRules')))
      .toBeVisible()
  })
})
