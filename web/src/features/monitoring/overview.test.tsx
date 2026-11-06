import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import type { Node } from '@/lib/api'
import { ThemeProvider } from '@/context/theme-provider'
import { NodeMonitoringTable } from './node-monitoring-table'
import { NodeMonitoring } from './nodes'
import type { NodeOverview } from './types'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

const NODE_BASE = {
  agent_id: 'agent-1',
  ip: '192.0.2.10',
  os: 'linux',
  arch: 'amd64',
  agent_version: '1.0.0',
  deployment_mode: 'native',
  host_integration: false,
  labels: {},
  capabilities: {},
  global_revision: 1,
  sync_status: 'synced',
  last_seen: '2026-10-17T00:00:00Z',
  first_seen: '2026-10-01T00:00:00Z',
}

function node(
  id: string,
  hostname: string,
  status: 'online' | 'offline' = 'online'
) {
  return { ...NODE_BASE, id, hostname, status }
}

const NODES = [
  node('node-a', 'node-a'),
  node('node-b', 'node-b'),
  node('node-c', 'node-c', 'offline'),
]

const OVERVIEW = [
  {
    node_id: 'node-a',
    hostname: 'node-a',
    status: 'online',
    cpu: 42.5,
    memory: 61,
    load1: 0.85,
    disk: 70,
    network_rx_bps: 1024,
    network_tx_bps: 2 * 1024 * 1024,
    alerts: 2,
    observed_at: '2026-10-17T02:05:00Z',
    state: 'ready',
  },
  {
    node_id: 'node-b',
    hostname: 'node-b',
    status: 'online',
    cpu: null,
    memory: null,
    load1: null,
    disk: null,
    network_rx_bps: null,
    network_tx_bps: null,
    state: 'no_data',
    observed_at: '2026-10-17T02:00:00Z',
  },
]

const SERIES = {
  series: [{ seriesKey: 'series-1', dimensions: { cpu: '0' } }],
}

const HISTORY = {
  state: 'ready',
  unit: '%',
  stepNs: 60_000_000_000,
  points: [
    { timestamp: '2026-10-17T02:00:00Z', value: 42, quality: 1 },
    { timestamp: '2026-10-17T02:01:00Z', value: null, quality: 2 },
  ],
  gaps: [],
}

function defaultGet(path: string) {
  if (path === '/nodes') return NODES
  if (path === '/monitoring/nodes/overview') return { nodes: OVERVIEW }
  if (path.includes('/series?')) return SERIES
  if (path.includes('/metrics?')) return HISTORY
  return []
}

function metricsPaths() {
  return get.mock.calls
    .map(([path]) => String(path))
    .filter((path) => path.includes('/metrics?'))
}

function rangeMs(path: string) {
  const url = new URL(path, 'http://localhost')
  const start = Date.parse(url.searchParams.get('start') || '')
  const end = Date.parse(url.searchParams.get('end') || '')
  return end - start
}

function renderOverview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <NodeMonitoring />
      </ThemeProvider>
    </QueryClientProvider>
  )
}

describe('节点监控总览', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/monitoring')
    get.mockImplementation(async (path: string) => defaultGet(path))
  })

  it('渲染所有节点行、统计和原生监控指标', async () => {
    const page = await renderOverview()

    await expect
      .element(page.getByTestId('overview-stat-nodes'))
      .toHaveTextContent('3')
    await expect
      .element(page.getByTestId('overview-stat-online'))
      .toHaveTextContent('2')
    await expect
      .element(page.getByTestId('overview-stat-alerts'))
      .toHaveTextContent(i18n.t('webFix.alertsPending'))

    const rack = page.getByTestId('node-monitoring-rack')
    const readyRow = page.getByTestId('node-monitoring-row-node-a')
    await expect.element(readyRow).toHaveTextContent('42.5%')
    await expect.element(readyRow).toHaveTextContent('61.0%')
    await expect.element(readyRow).toHaveTextContent('0.85 /')
    await expect.element(readyRow).toHaveTextContent('70.0%')
    await expect.element(readyRow).toHaveTextContent('1.0 KB/s')
    await expect.element(readyRow).toHaveTextContent('2.0 MB/s')

    const noDataRow = page.getByTestId('node-monitoring-row-node-b')
    await expect.element(noDataRow).toHaveTextContent('—')

    const missingRow = page.getByTestId('node-monitoring-row-node-c')
    await expect.element(missingRow).toHaveTextContent('—')
    await expect
      .element(rack.getByRole('img', { name: '离线' }))
      .toHaveAttribute('aria-label', '离线')
  })

  it('查看详情链接到节点监控详情页', async () => {
    const page = await renderOverview()
    const rack = page.getByTestId('node-monitoring-rack')

    await expect
      .element(rack.getByRole('link', { name: 'node-a' }))
      .toHaveAttribute('href', '/monitoring/nodes/node-a')
    await expect
      .element(
        rack
          .getByRole('link', {
            name: i18n.t('monitoring.viewDetails'),
          })
          .first()
      )
      .toHaveAttribute('href', '/monitoring/nodes/node-a')
  })

  it('默认 1h 查询，切换 24h 后重新请求并保留图表和质量表', async () => {
    window.history.replaceState({}, '', '/monitoring?node=node-a')
    const page = await renderOverview()

    const seriesSelect = page.getByRole('combobox', {
      name: i18n.t('monitoring.series'),
    })
    await expect.element(seriesSelect).toBeVisible()
    await userEvent.click(seriesSelect)
    await userEvent.click(page.getByRole('option', { name: 'cpu=0' }))

    await expect.poll(() => metricsPaths().length).toBeGreaterThan(0)
    const firstPaths = metricsPaths()
    expect(rangeMs(firstPaths[firstPaths.length - 1])).toBe(60 * 60 * 1000)

    const rangeSelect = page.getByRole('combobox', {
      name: i18n.t('monitoring.timeRange'),
    })
    await userEvent.click(rangeSelect)
    await userEvent.click(
      page.getByRole('option', { name: i18n.t('monitoring.last24Hours') })
    )

    await expect
      .poll(() =>
        metricsPaths().some(
          (path) => Math.abs(rangeMs(path) - 24 * 60 * 60 * 1000) < 1000
        )
      )
      .toBe(true)
    await expect
      .element(page.getByRole('img', { name: i18n.t('monitoring.chart') }))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('monitoring.validSample')))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('monitoring.incompleteSample')))
      .toBeVisible()
  })

  it('没有 NodeSteer 节点时显示空态，且不请求 overview', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return []
      return []
    })
    const page = await renderOverview()

    await expect
      .element(page.getByText(i18n.t('monitoring.noNodes')))
      .toBeVisible()
    expect(
      get.mock.calls.some(([path]) => path === '/monitoring/nodes/overview')
    ).toBe(false)
  })

  it('加载中显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderOverview()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('overview 查询失败显示错误与重试', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return NODES
      if (path === '/monitoring/nodes/overview')
        throw new Error('监控总览加载失败')
      return []
    })
    const page = await renderOverview()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('监控总览加载失败')
    const before = get.mock.calls.length

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.retry') })
    )
    await expect.poll(() => get.mock.calls.length).toBeGreaterThan(before)
  })

  it('保留 ?node= 深链，自动选中并查询历史序列', async () => {
    window.history.replaceState({}, '', '/monitoring?node=node-b')
    const page = await renderOverview()

    await expect
      .element(page.getByText(i18n.t('monitoring.metricName')))
      .toBeVisible()
    await expect
      .poll(() =>
        get.mock.calls.some(
          ([path]) =>
            String(path).startsWith('/monitoring/nodes/node-b/series?') &&
            String(path).includes('metric=cpu.usage')
        )
      )
      .toBe(true)
  })
})

describe('NodeMonitoringTable 进度与标签列', () => {
  const tableNodes = [
    { id: 'tone-1', hostname: 'tone-1', status: 'online' },
    { id: 'tone-2', hostname: 'tone-2', status: 'online' },
  ] as Node[]
  const tableOverview = new Map<string, NodeOverview>([
    [
      'tone-1',
      {
        node_id: 'tone-1',
        hostname: 'tone-1',
        status: 'online',
        cpu: 40,
        memory: 75,
        disk: 90,
        load1: 0.01,
        load5: 0.02,
        load15: 0.09,
        network_rx_bps: 9830,
        network_tx_bps: 519000,
        temperature_c: null,
      },
    ],
    [
      'tone-2',
      {
        node_id: 'tone-2',
        hostname: 'tone-2',
        status: 'online',
        cpu: 74.9,
        memory: 90.1,
        disk: 95,
        load1: 0.12,
        load5: 0.06,
        load15: 0.02,
        network_rx_bps: 1024,
        network_tx_bps: 1024 * 1024,
        temperature_c: 45.5,
      },
    ],
  ])

  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  function renderTable() {
    return render(
      <NodeMonitoringTable
        nodes={tableNodes}
        overviewByNode={tableOverview}
        loading={false}
        error={null}
        onRetry={() => {}}
      />
    )
  }

  it('按 75% / 90% 边界映射 warning / danger', async () => {
    const page = await renderTable()

    await expect
      .element(page.getByTestId('dashboard-cpu-tone-1'))
      .toHaveAttribute('data-tone', 'success')
    await expect
      .element(page.getByTestId('dashboard-memory-tone-1'))
      .toHaveAttribute('data-tone', 'warning')
    await expect
      .element(page.getByTestId('dashboard-disk-tone-1'))
      .toHaveAttribute('data-tone', 'warning')
    await expect
      .element(page.getByTestId('dashboard-cpu-tone-2'))
      .toHaveAttribute('data-tone', 'success')
    await expect
      .element(page.getByTestId('dashboard-memory-tone-2'))
      .toHaveAttribute('data-tone', 'danger')
    await expect
      .element(page.getByTestId('dashboard-disk-tone-2'))
      .toHaveAttribute('data-tone', 'danger')

    expect(
      page.getByTestId('dashboard-memory-tone-1-bar').element().className
    ).toContain('bg-warning')
    expect(
      page.getByTestId('dashboard-memory-tone-2-bar').element().className
    ).toContain('bg-danger')
    expect(
      page.getByTestId('dashboard-cpu-tone-1-bar').element().className
    ).toContain('bg-success')
  })

  it('进度条为 8px 且轨道有明显内描边', async () => {
    const page = await renderTable()
    const bar = page.getByTestId('dashboard-cpu-tone-1-bar').element()
    const track = bar.parentElement
    if (!track) throw new Error('bar track missing')
    expect(track.className).toContain('h-2')
    expect(track.className).toContain('ring-1')
    expect(track.className).toContain('bg-muted-foreground/20')
  })

  it('所有节点行共用同一组固定标签子列', async () => {
    const page = await renderTable()
    const rack = page.getByTestId('node-monitoring-rack').element()

    await expect
      .poll(() => rack.style.getPropertyValue('--rack-tags-width'))
      .toMatch(/^\d+px$/)
    const columns = rack.style.getPropertyValue('--rack-tag-columns')
    expect(columns.split(' ')).toHaveLength(7)
    expect(columns).not.toMatch(/auto|fr/)

    for (const id of ['tone-1', 'tone-2']) {
      const grid = page.getByTestId(`node-tags-${id}`).element()
      expect(grid.style.gridTemplateColumns).toBe(
        'var(--rack-tag-columns, 43px 127px 43px 92px 92px 43px 57px)'
      )
      expect(
        [...grid.querySelectorAll('[data-tag-column]')].map((cell) =>
          cell.getAttribute('data-tag-column')
        )
      ).toEqual([
        'load-label',
        'load-value',
        'network-label',
        'network-rx',
        'network-tx',
        'temperature-label',
        'temperature-value',
      ])
    }
  })
})
