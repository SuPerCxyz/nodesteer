import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage, userEvent } from 'vitest/browser'
import { ThemeProvider } from '@/context/theme-provider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { NodeMonitoringDetail } from './node-detail'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

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
    Link: ({
      children,
      to,
      ...props
    }: {
      children?: ReactNode
      to?: string
    } & Record<string, unknown>) => (
      <a href={typeof to === 'string' ? to : '#'} {...props}>
        {children}
      </a>
    ),
  }
})

const OVERVIEW = {
  node_id: 'node-1',
  hostname: 'node-1',
  status: 'online',
  state: 'ready',
  cpu: 42.5,
  memory: 61,
  load1: 0.85,
  disk: 70,
  network_rx_bps: 1024,
  network_tx_bps: 2 * 1024 * 1024,
  alerts: 0,
  observed_at: '2026-10-17T02:05:00Z',
}

const SERIES = {
  'cpu.usage': {
    series: [
      { seriesKey: 'cpu-all', dimensions: { cpu: 'all' } },
      { seriesKey: 'cpu-0', dimensions: { cpu: '0' } },
    ],
  },
  'cpu.user': {
    series: [{ seriesKey: 'cpu-user-all', dimensions: { cpu: 'all' } }],
  },
  'cpu.system': {
    series: [{ seriesKey: 'cpu-system-all', dimensions: { cpu: 'all' } }],
  },
  'cpu.iowait': {
    series: [{ seriesKey: 'cpu-iowait-all', dimensions: { cpu: 'all' } }],
  },
  'cpu.softirq': {
    series: [{ seriesKey: 'cpu-softirq-all', dimensions: { cpu: 'all' } }],
  },
  'cpu.steal': {
    series: [{ seriesKey: 'cpu-steal-all', dimensions: { cpu: 'all' } }],
  },
  'memory.usage': {
    series: [{ seriesKey: 'mem-1', dimensions: { memory: 'used' } }],
  },
  'memory.swap_usage': {
    series: [{ seriesKey: 'swap-1', dimensions: {} }],
  },
  'load.1': { series: [{ seriesKey: 'load-1', dimensions: {} }] },
  'load.5': { series: [{ seriesKey: 'load-5', dimensions: {} }] },
  'load.15': { series: [{ seriesKey: 'load-15', dimensions: {} }] },
  'disk.usage': {
    series: [
      { seriesKey: 'disk-root', dimensions: { mount: '/' } },
      { seriesKey: 'disk-data', dimensions: { mount: '/data' } },
    ],
  },
  'disk.read_bps': {
    series: [
      { seriesKey: 'read-sda', dimensions: { device: 'sda' } },
      { seriesKey: 'read-nvme0n1', dimensions: { device: 'nvme0n1' } },
    ],
  },
  'disk.write_bps': {
    series: [
      { seriesKey: 'write-sda', dimensions: { device: 'sda' } },
      { seriesKey: 'write-nvme0n1', dimensions: { device: 'nvme0n1' } },
    ],
  },
  'disk.read_iops': {
    series: [
      { seriesKey: 'read-iops-sda', dimensions: { device: 'sda' } },
      { seriesKey: 'read-iops-nvme0n1', dimensions: { device: 'nvme0n1' } },
    ],
  },
  'disk.write_iops': {
    series: [
      { seriesKey: 'write-iops-sda', dimensions: { device: 'sda' } },
      { seriesKey: 'write-iops-nvme0n1', dimensions: { device: 'nvme0n1' } },
    ],
  },
  'network.rx_bps': {
    series: [
      { seriesKey: 'rx-enp', dimensions: { interface: 'enp1s0' } },
      { seriesKey: 'rx-lo', dimensions: { interface: 'lo' } },
    ],
  },
  'network.tx_bps': {
    series: [
      { seriesKey: 'tx-enp', dimensions: { interface: 'enp1s0' } },
      { seriesKey: 'tx-lo', dimensions: { interface: 'lo' } },
    ],
  },
}

for (const metric of [
  'memory.cached_bytes',
  'memory.used_bytes',
  'memory.buffers_bytes',
  'memory.free_bytes',
  'memory.available_bytes',
]) {
  Object.assign(SERIES, {
    [metric]: { series: [{ seriesKey: metric, dimensions: {} }] },
  })
}
for (const metric of ['filesystem.used_bytes', 'filesystem.total_bytes']) {
  Object.assign(SERIES, {
    [metric]: {
      series: [
        {
          seriesKey: `${metric}-root`,
          dimensions: { mount: '/', device: '/dev/sda1' },
        },
        {
          seriesKey: `${metric}-data`,
          dimensions: { mount: '/data', device: '/dev/sdb1' },
        },
      ],
    },
  })
}

const VALUES: Record<string, number> = {
  'cpu.usage': 42.5,
  'cpu.user': 18.2,
  'cpu.system': 7.4,
  'cpu.iowait': 2.1,
  'cpu.softirq': 1.3,
  'cpu.steal': 0.4,
  'memory.cached_bytes': 1024,
  'memory.used_bytes': 2048,
  'memory.buffers_bytes': 512,
  'memory.free_bytes': 256,
  'memory.available_bytes': 1280,
  'filesystem.used_bytes': 2048,
  'filesystem.total_bytes': 4096,
  'memory.usage': 61,
  'memory.swap_usage': 82.3,
  'load.1': 0.85,
  'load.5': 0.65,
  'load.15': 0.55,
  'disk.usage': 70,
  'disk.read_bps': 2048,
  'disk.write_bps': 1024,
  'disk.read_iops': 12,
  'disk.write_iops': 6,
  'network.rx_bps': 1024,
  'network.tx_bps': 2 * 1024 * 1024,
}

function historyFor(metric: string, series: string) {
  const value =
    metric === 'cpu.usage' && series === 'cpu-0'
      ? 12.5
      : metric === 'disk.usage' && series === 'disk-data'
        ? 80
        : metric === 'network.rx_bps' && series === 'rx-lo'
          ? 2048
          : metric === 'network.tx_bps' && series === 'tx-lo'
            ? 4096
            : VALUES[metric] || 0
  return {
    state: 'ready',
    unit: metric.endsWith('_bytes')
      ? 'B'
      : metric.includes('_bps')
        ? 'bps'
        : metric.includes('_iops')
          ? 'ops/s'
          : '%',
    stepNs: 60_000_000_000,
    points: [
      {
        timestamp: '2026-10-17T02:00:00Z',
        value,
        quality: 1,
      },
      {
        timestamp: '2026-10-17T02:01:00Z',
        value,
        quality: 1,
      },
    ],
    gaps: [],
  }
}

function defaultGet(path: string) {
  if (path === '/monitoring/nodes/overview') return { nodes: [OVERVIEW] }
  if (path === '/nodes')
    return [
      {
        id: 'node-1',
        hostname: 'node-1',
        inventory: {
          block_devices: [
            { name: 'sda', kind: 'disk' },
            { name: 'nvme0n1', kind: 'disk' },
          ],
        },
      },
    ]
  if (path.startsWith('/monitoring/nodes/node-1/series?')) {
    const metric =
      new URL(path, 'http://localhost').searchParams.get('metric') || ''
    return SERIES[metric as keyof typeof SERIES] || { series: [] }
  }
  if (path.startsWith('/monitoring/nodes/node-1/metrics?')) {
    const url = new URL(path, 'http://localhost')
    return historyFor(
      url.searchParams.get('metric') || '',
      url.searchParams.get('series') || ''
    )
  }
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

function renderDetail() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <NodeMonitoringDetail nodeId='node-1' />
        </ThemeProvider>
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('节点监控详情', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    get.mockImplementation(async (path: string) => defaultGet(path))
  })

  it('header 展示 inventory 真实主机名与主机事实，不以配置节点名冒充', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return { nodes: [{ ...OVERVIEW, uptime_seconds: 90061 }] }
      if (path === '/nodes')
        return [
          {
            id: 'node-1',
            hostname: 'configured-node-name',
            display_name: '',
            inventory: {
              hostname: 'real-host-01',
              os: 'Ubuntu 24.04 LTS',
              kernel: '6.8.0-test',
              cpu: [{ model: 'Intel Test CPU', cores: 8, mhz: 3000 }],
              memory: { total_kb: 2048, available_kb: 1024 },
              block_devices: [
                { name: 'sda', kind: 'disk' },
                { name: 'nvme0n1', kind: 'disk' },
              ],
            },
          },
        ]
      return defaultGet(path)
    })
    const page = await renderDetail()

    const hostname = page.getByTestId('node-header-hostname')
    await expect.element(hostname).toHaveTextContent('real-host-01')
    await expect.element(hostname).not.toHaveTextContent('configured-node-name')
    await expect
      .element(page.getByTestId('node-header-distribution'))
      .toHaveTextContent('Ubuntu 24.04 LTS')
    await expect
      .element(page.getByTestId('node-header-cpu'))
      .toHaveTextContent('Intel Test CPU')
    await expect
      .element(page.getByTestId('node-header-cpu'))
      .toHaveTextContent('8 核')
    await expect
      .element(page.getByTestId('node-header-memory'))
      .toHaveTextContent('2.0 MiB')
    await expect
      .element(page.getByTestId('node-header-last-sample'))
      .toHaveTextContent('2026')
    expect(page.getByText('6.8.0-test').elements()).toHaveLength(0)
    for (const testId of [
      'node-header-hostname',
      'node-header-uptime',
      'node-header-distribution',
      'node-header-cpu',
      'node-header-memory',
      'node-header-last-sample',
    ]) {
      const icon = page.getByTestId(testId).element().querySelector('svg')
      expect(icon?.getAttribute('class')).toContain('size-3.5')
      expect(icon?.getAttribute('stroke-width')).toBe('1.8')
    }
    for (const [testId, label] of [
      ['node-header-hostname', i18n.t('monitoring.hostname')],
      ['node-header-uptime', i18n.t('monitoring.uptime')],
      ['node-header-distribution', i18n.t('monitoring.distribution')],
      ['node-header-cpu', 'CPU'],
      ['node-header-memory', i18n.t('monitoring.memory')],
    ] as const) {
      const fact = page.getByTestId(testId)
      await expect.element(fact).not.toHaveTextContent(`${label}:`)
      await expect.element(fact.getByRole('img', { name: label })).toBeVisible()
      await expect
        .element(fact.getByRole('img', { name: label }))
        .toHaveAttribute('title', label)
    }
    expect(
      page
        .getByText(i18n.t('monitoring.hostname') + ':', { exact: true })
        .elements()
    ).toHaveLength(0)
    expect(
      page
        .getByText(i18n.t('monitoring.uptime') + ':', { exact: true })
        .elements()
    ).toHaveLength(0)
    expect(
      page
        .getByText(i18n.t('monitoring.distribution') + ':', { exact: true })
        .elements()
    ).toHaveLength(0)
    expect(page.getByText('CPU:', { exact: true }).elements()).toHaveLength(0)
    expect(
      page
        .getByText(i18n.t('monitoring.memory') + ':', { exact: true })
        .elements()
    ).toHaveLength(0)
  })

  it('展示节点 header、返回链接和节点操作，默认查询 1h', async () => {
    const page = await renderDetail()

    await expect
      .element(page.getByText('node-1', { exact: true }))
      .toBeVisible()
    await expect.element(page.getByText('在线')).toBeVisible()
    expect(page.getByText('ready', { exact: true }).elements()).toHaveLength(0)
    await expect
      .element(
        page.getByRole('link', { name: i18n.t('monitoring.backToOverview') })
      )
      .toHaveAttribute('href', '/monitoring')
    await expect
      .element(
        page.getByRole('link', { name: i18n.t('monitoring.nodeActions') })
      )
      .toHaveAttribute('href', '/agents/node-1')
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
      )
      .toHaveTextContent(i18n.t('monitoring.lastHour'))

    await expect.poll(() => metricsPaths().length).toBeGreaterThan(0)
    expect(
      metricsPaths().some((path) => rangeMs(path) === 60 * 60 * 1000)
    ).toBe(true)
  })

  it('渲染 CPU/内存/负载/磁盘/网络图表和最新值', async () => {
    const page = await renderDetail()

    const cpu = page.getByTestId('metric-card-cpu')
    const memory = page.getByTestId('metric-card-memory')
    const load = page.getByTestId('metric-card-load')
    const diskRoot = page.getByTestId('metric-card-disk-root')
    const diskData = page.getByTestId('metric-card-disk-data')
    const network = page.getByTestId('metric-card-network')

    await expect.element(cpu).toHaveTextContent('42.5%')
    await expect.element(memory).toHaveTextContent('Cached')
    await expect.element(memory).toHaveTextContent('Available')
    await expect.element(load).toHaveTextContent('0.85')
    await expect.element(load).toHaveTextContent('0.65')
    await expect.element(load).toHaveTextContent('0.55')
    await expect.element(diskRoot).toHaveTextContent(i18n.t('monitoring.used'))
    await expect.element(diskData).toHaveTextContent(i18n.t('monitoring.total'))
    await expect.element(network).toHaveTextContent('1.0 KB/s')
    await expect.element(network).toHaveTextContent('2.0 MB/s')

    await expect
      .element(cpu.getByRole('img', { name: i18n.t('monitoring.cpu') }))
      .toBeVisible()
    await expect
      .element(memory.getByRole('img', { name: i18n.t('monitoring.memory') }))
      .toBeVisible()
    const loadChart = load.getByTestId('multi-series-chart')
    await expect.element(loadChart).toBeVisible()
    expect(load.getByTestId('multi-series-chart').elements()).toHaveLength(1)
    await expect
      .element(
        load.getByRole('img', {
          name: i18n.t('monitoring.load'),
          exact: true,
        })
      )
      .toBeVisible()
    const loadContainer = loadChart.element()
    const loadSeries = [...loadContainer.querySelectorAll('g[data-series-id]')]
      .map((node) => ({
        id: node.getAttribute('data-series-id'),
        palette: node.getAttribute('data-palette-id'),
      }))
      .sort((a, b) => String(a.id).localeCompare(String(b.id)))
    expect(loadSeries).toEqual([
      { id: 'load.1', palette: 'series-1' },
      { id: 'load.15', palette: 'series-4' },
      { id: 'load.5', palette: 'series-2' },
    ])
    await expect
      .element(load.getByText('load.1', { exact: true }))
      .toBeVisible()
    await expect
      .element(load.getByText('load.5', { exact: true }))
      .toBeVisible()
    await expect
      .element(load.getByText('load.15', { exact: true }))
      .toBeVisible()
    await expect
      .element(
        diskRoot.getByRole('img', {
          name: `${i18n.t('monitoring.filesystem')} /`,
          exact: true,
        })
      )
      .toBeVisible()
    await expect
      .element(
        diskData.getByRole('img', {
          name: `${i18n.t('monitoring.filesystem')} /data`,
          exact: true,
        })
      )
      .toBeVisible()
    const networkChart = network.getByTestId('multi-series-chart')
    await expect.element(networkChart).toBeVisible()
    expect(network.getByTestId('multi-series-chart').elements()).toHaveLength(1)
    const networkSeries = [
      ...networkChart.element().querySelectorAll('g[data-series-id]'),
    ].map((node) => node.getAttribute('data-series-id'))
    expect(new Set(networkSeries)).toEqual(
      new Set(['network.rx_bps', 'network.tx_bps'])
    )
    await expect
      .element(
        network.getByText(i18n.t('monitoring.networkRx'), { exact: true })
      )
      .toBeVisible()
    await expect
      .element(
        network.getByText(i18n.t('monitoring.networkTx'), { exact: true })
      )
      .toBeVisible()

    expect(
      cpu
        .getByRole('combobox', { name: i18n.t('monitoring.series') })
        .elements()
    ).toHaveLength(0)
    await expect
      .element(
        network.getByRole('combobox', { name: i18n.t('monitoring.interface') })
      )
      .toHaveTextContent('enp1s0')
  })

  it('所有节点详情时间序列都使用渐变描边和面积渐隐', async () => {
    const page = await renderDetail()
    for (const testId of [
      'metric-card-cpu',
      'metric-card-memory',
      'metric-card-swap',
      'metric-card-load',
      'metric-card-disk-root',
      'metric-card-disk-data',
      'metric-card-disk-io-sda',
      'metric-card-disk-iops-sda',
      'metric-card-network',
    ]) {
      const card = page.getByTestId(testId)
      await expect.element(card).toBeVisible()
      await vi.waitFor(
        () => {
          const count = card.getByTestId('chart-area').elements().length
          if (count === 0) throw new Error(`${testId} has no gradient area`)
          return count
        },
        { timeout: 4000 }
      )
      const groups = card.element().querySelectorAll('g[data-series-id]')
      expect(groups.length).toBeGreaterThan(0)
      for (const group of groups) {
        const line = group.querySelector('[data-testid="chart-line"]')
        const area = group.querySelector('[data-testid="chart-area"]')
        expect(line?.getAttribute('stroke')).toMatch(/^url\(#stroke-/)
        expect(area?.getAttribute('fill')).toMatch(/^url\(#fill-/)
      }
    }
  })

  it('按分区顺序渲染指标卡，并在无容器数据时显示不可用', async () => {
    const page = await renderDetail()

    for (const testId of [
      'metric-card-cpu',
      'metric-card-memory',
      'metric-card-swap',
      'metric-card-load',
      'metric-card-disk-root',
      'metric-card-disk-data',
      'metric-card-disk-io-nvme0n1',
      'metric-card-disk-io-sda',
      'metric-card-disk-iops-nvme0n1',
      'metric-card-disk-iops-sda',
      'metric-card-network',
      'metric-card-network-errors',
      'metric-card-temperature',
      'metric-card-fan',
      'metric-card-containers',
    ]) {
      await expect.element(page.getByTestId(testId)).toBeVisible()
    }

    const ids = [
      ...document.querySelectorAll('[data-testid^="metric-card-"]'),
    ].map((element) => element.getAttribute('data-testid'))
    expect(ids.indexOf('metric-card-cpu')).toBeLessThan(
      ids.indexOf('metric-card-memory')
    )
    expect(ids.indexOf('metric-card-memory')).toBeLessThan(
      ids.indexOf('metric-card-swap')
    )
    expect(ids.indexOf('metric-card-swap')).toBeLessThan(
      ids.indexOf('metric-card-load')
    )
    expect(ids.indexOf('metric-card-network')).toBeLessThan(
      ids.indexOf('metric-card-temperature')
    )
    expect(ids.indexOf('metric-card-temperature')).toBeLessThan(
      ids.indexOf('metric-card-containers')
    )
    await expect
      .element(page.getByText(i18n.t('monitoring.containerUnknown')))
      .toBeVisible()
    await expect
      .element(page.getByTestId('metric-card-temperature'))
      .toHaveTextContent(i18n.t('monitoring.sensorUnavailable'))
  })

  it('容器能力 false 时不显示容器图表', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return {
          nodes: [{ ...OVERVIEW, container_metrics_available: false }],
        }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('monitoring.containerUnknown')))
      .toBeVisible()
    expect(
      page.getByTestId('metric-card-container-cpu').elements()
    ).toHaveLength(0)
    expect(
      get.mock.calls.some(
        ([path]) =>
          String(path).includes('/series?') &&
          String(path).includes('container.')
      )
    ).toBe(false)
  })

  it('容器能力 true 但无 series 时显示暂无数据', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return {
          nodes: [{ ...OVERVIEW, container_metrics_available: true }],
        }
      if (path.includes('/series?') && path.includes('container.'))
        return { series: [] }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('monitoring.containerNoData')).first())
      .toBeVisible()
  })

  it('容器能力 true 且有数据时显示容器图表', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return {
          nodes: [{ ...OVERVIEW, container_metrics_available: true }],
        }
      if (path.includes('/series?') && path.includes('container.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            {
              seriesKey: `${metric}|container=beszel`,
              dimensions: { container: 'beszel' },
            },
          ],
        }
      }
      if (path.includes('/metrics?') && path.includes('container.')) {
        return {
          state: 'ready',
          unit: '%',
          stepNs: 60_000_000_000,
          points: [
            {
              timestamp: '2026-10-17T02:00:00Z',
              value: 5,
              quality: 1,
            },
            {
              timestamp: '2026-10-17T02:01:00Z',
              value: 6,
              quality: 1,
            },
          ],
          gaps: [],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByTestId('metric-card-container-cpu'))
      .toBeVisible()
    await expect
      .poll(
        () =>
          page
            .getByTestId('metric-card-container-cpu')
            .getByTestId('chart-area')
            .elements().length
      )
      .toBeGreaterThan(0)
    const containerChart = page
      .getByTestId('metric-card-container-cpu')
      .getByTestId('multi-series-chart')
    await userEvent.hover(containerChart.getByTestId('chart-overlay'))
    const tooltip = page.getByTestId('chart-tooltip')
    await expect.element(tooltip).toHaveTextContent('beszel')
    await expect.element(tooltip).toHaveTextContent('%')
    expect(tooltip.element().textContent).not.toMatch(/[0-9a-f]{12}/i)
  })

  it('容器网络图 Y 轴、图例与 tooltip 使用一致的速率单位', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return { nodes: [{ ...OVERVIEW, container_metrics_available: true }] }
      if (path.includes('/series?') && path.includes('container.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            {
              seriesKey: `${metric}|container=web`,
              dimensions: { container: 'web', container_id: 'web-id' },
            },
          ],
        }
      }
      if (path.includes('/metrics?') && path.includes('container.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          state: 'ready',
          unit: metric.includes('network') ? 'bps' : '%',
          stepNs: 60_000_000_000,
          points: [
            {
              timestamp: '2026-10-17T02:00:00Z',
              value: metric.includes('network') ? 2 * 1024 * 1024 : 5,
              quality: 1,
            },
          ],
          gaps: [],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()
    const card = page.getByTestId('metric-card-container-rx')
    const chart = card.getByTestId('multi-series-chart')
    await expect.element(chart).toBeVisible()

    const axisText = [...chart.element().querySelectorAll('svg text')].map(
      (node) => node.textContent || ''
    )
    expect(axisText.some((text) => text.includes('MB/s'))).toBe(true)

    await userEvent.hover(chart.getByTestId('chart-overlay'))
    await expect
      .element(page.getByTestId('chart-tooltip'))
      .toHaveTextContent('MB/s')
  })

  it('同名容器按 ID 分线，单选复用已加载历史', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return { nodes: [{ ...OVERVIEW, container_metrics_available: true }] }
      if (path.includes('/series?') && path.includes('container.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            {
              seriesKey: `${metric}-first`,
              dimensions: { container: 'web', container_id: 'first-id' },
            },
            {
              seriesKey: `${metric}-second`,
              dimensions: { container: 'web', container_id: 'second-id' },
            },
            { seriesKey: `${metric}-legacy`, dimensions: { container: 'web' } },
          ],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()
    const cpu = page.getByTestId('metric-card-container-cpu')
    await expect.element(cpu).toHaveTextContent('web')
    expect(cpu.getByText(/first-id|second-id/).elements()).toHaveLength(0)
    const before = metricsPaths().length
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.containerMore') })
    )
    const options = page.getByRole('option', { name: 'web', exact: true })
    expect(options.elements()).toHaveLength(3)
    for (const option of options.elements()) {
      expect(option.textContent).toBe('web')
    }
    await userEvent.click(options.first())
    expect(cpu.getByText(/first-id|second-id/).elements()).toHaveLength(0)
    expect(
      cpu.getByText('web', { exact: true }).elements().length
    ).toBeGreaterThan(0)
    expect(metricsPaths()).toHaveLength(before)
  })

  it('容器主图不再显示容器标题，点击图例/展开全部打开单容器监控', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        return { nodes: [{ ...OVERVIEW, container_metrics_available: true }] }
      if (path.includes('/series?') && path.includes('container.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            {
              seriesKey: `${metric}-first`,
              dimensions: { container: 'web', container_id: 'first-id' },
            },
            {
              seriesKey: `${metric}-second`,
              dimensions: { container: 'api', container_id: 'second-id' },
            },
          ],
        }
      }
      if (path.includes('/metrics?') && path.includes('container.')) {
        return {
          state: 'ready',
          unit: path.includes('network') ? 'bps' : '%',
          stepNs: 60_000_000_000,
          points: [
            { timestamp: '2026-10-17T02:00:00Z', value: 5, quality: 1 },
            { timestamp: '2026-10-17T02:01:00Z', value: 6, quality: 1 },
          ],
          gaps: [],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()
    const cpu = page.getByTestId('metric-card-container-cpu')
    await expect.element(cpu).toHaveTextContent('CPU')
    expect(cpu.getByText(/^容器 /).elements()).toHaveLength(0)

    await userEvent.click(cpu.getByRole('button').first())
    const dialog = page.getByTestId('container-detail-dialog')
    await expect.element(dialog).toBeVisible()
    await expect
      .element(dialog.getByText(i18n.t('monitoring.containerDetail')))
      .toBeVisible()
    expect(dialog.getByTestId(/container-detail-/).elements().length).toBe(4)
    expect(dialog.getByText(/first-id|second-id/).elements()).toHaveLength(0)
    await userEvent.keyboard('{Escape}')
    await expect.element(dialog).not.toBeInTheDocument()

    await userEvent.click(page.getByTestId('container-expand-all'))
    await expect
      .element(page.getByTestId('container-detail-dialog'))
      .toBeVisible()
  })

  it('overview 缺少容器字段时回退到节点能力', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview') return { nodes: [OVERVIEW] }
      if (path === '/nodes')
        return [
          {
            id: 'node-1',
            hostname: 'node-1',
            capabilities: { container_metrics: true },
          },
        ]
      if (path.includes('/series?') && path.includes('container.'))
        return { series: [] }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(
        page
          .getByTestId('metric-card-container-cpu')
          .getByText(i18n.t('monitoring.containerNoData'))
      )
      .toBeVisible()
  })

  it('内存图例横向位于标题同一行，Swap 不显示最新值', async () => {
    await browserPage.viewport(1920, 1080)
    const page = await renderDetail()
    const memory = page.getByTestId('metric-card-memory')
    const title = memory.getByText(i18n.t('monitoring.memory'), { exact: true })
    const cached = memory.getByRole('button', { name: /Cached/ })
    const available = memory.getByRole('button', { name: /Available/ })
    await expect.element(title).toBeVisible()
    await expect.element(cached).toBeVisible()
    await expect.element(available).toBeVisible()
    expect(cached.element().parentElement).toBe(
      available.element().parentElement
    )
    expect(cached.element().parentElement?.className).toContain('flex-wrap')
    expect(cached.element().className).toContain('whitespace-nowrap')

    const swap = page.getByTestId('metric-card-swap')
    await expect.element(swap).toBeVisible()
    expect(
      swap.getByText(i18n.t('monitoring.latestValue')).elements()
    ).toHaveLength(0)
    await browserPage.viewport(1280, 720)
  })

  it('CPU 默认总量，展开后加载真实分核', async () => {
    const page = await renderDetail()
    const cpu = page.getByTestId('metric-card-cpu')
    await expect.element(cpu).toHaveTextContent('42.5%')
    expect(metricsPaths().some((path) => path.includes('series=cpu-0'))).toBe(
      false
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.perCore') })
    )
    await expect
      .poll(() => metricsPaths().some((path) => path.includes('series=cpu-0')))
      .toBe(true)
    await expect.element(cpu).toHaveTextContent('12.5%')
  })

  it('CPU 使用紧凑 us/sys/wa/si/st 分类且不显示 idle', async () => {
    const page = await renderDetail()
    const cpu = page.getByTestId('metric-card-cpu')
    await expect.element(cpu).toBeVisible()

    for (const short of ['us', 'sys', 'wa', 'si', 'st']) {
      await expect
        .element(cpu.getByText(short, { exact: true }).first())
        .toBeVisible()
    }
    await expect
      .element(cpu.getByText(i18n.t('monitoring.totalUsage')))
      .toBeVisible()
    expect(cpu.getByText(/idle|闲置/i).elements()).toHaveLength(0)
    expect(metricsPaths().some((path) => path.includes('cpu.idle'))).toBe(false)
    expect(page.getByTestId('cpu-breakdown').elements()).toHaveLength(0)
    expect(
      cpu.getByText(i18n.t('monitoring.latestValue')).elements()
    ).toHaveLength(0)

    await userEvent.hover(cpu.getByTestId('chart-overlay'))
    const tooltip = cpu.getByTestId('chart-tooltip')
    await expect
      .element(tooltip)
      .toHaveTextContent(i18n.t('monitoring.cpuUser'))
    await expect
      .element(tooltip)
      .toHaveTextContent(i18n.t('monitoring.cpuIowait'))
    await expect
      .element(tooltip)
      .toHaveTextContent(i18n.t('monitoring.cpuSteal'))
  })

  it('Swap、CPU iowait 与磁盘写使用普通系列色而非告警色', async () => {
    const page = await renderDetail()
    const paletteOf = (testId: string, seriesId: string) => {
      const node = page
        .getByTestId(testId)
        .element()
        .querySelector(`g[data-series-id="${seriesId}"]`)
      return node?.getAttribute('data-palette-id')
    }
    await expect
      .poll(() => paletteOf('metric-card-swap', 'memory.swap_usage'))
      .toBe('series-11')
    await expect
      .poll(() => paletteOf('metric-card-cpu', 'cpu.iowait'))
      .toBe('series-11')
    await expect
      .poll(() => paletteOf('metric-card-disk-io-sda', 'disk.write_bps'))
      .toBe('series-12')
    for (const palette of ['series-11', 'series-12']) {
      expect(palette.startsWith('alert-')).toBe(false)
    }
  })

  it('切换时间范围后重新请求 24h 数据', async () => {
    const page = await renderDetail()
    await expect.element(page.getByTestId('metric-card-cpu')).toBeVisible()

    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
    )
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
  })

  it('3h 和 12h 预设按选定范围查询', async () => {
    const page = await renderDetail()
    for (const [label, hours] of [
      [i18n.t('monitoring.last3Hours'), 3],
      [i18n.t('monitoring.last12Hours'), 12],
    ] as const) {
      await userEvent.click(
        page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
      )
      await userEvent.click(page.getByRole('option', { name: label }))
      await expect
        .poll(() =>
          metricsPaths().some(
            (path) => rangeMs(path) === hours * 60 * 60 * 1000
          )
        )
        .toBe(true)
    }
  })

  it('无总 CPU 系列时不会使用第一颗核心冒充', async () => {
    get.mockImplementation(async (path: string) =>
      path.includes('/series?') && path.includes('cpu.usage')
        ? { series: [{ seriesKey: 'cpu-0', dimensions: { cpu: '0' } }] }
        : defaultGet(path)
    )
    const page = await renderDetail()
    await expect
      .element(page.getByTestId('metric-card-cpu'))
      .toHaveTextContent(i18n.t('monitoring.noSeries'))
    expect(metricsPaths().some((path) => path.includes('series=cpu-0'))).toBe(
      false
    )
  })

  it('磁盘容量按挂载点拆分，每个文件系统独立图表且不再显示 Inode', async () => {
    const page = await renderDetail()
    const root = page.getByTestId('metric-card-disk-root')
    const data = page.getByTestId('metric-card-disk-data')

    await expect.element(root).toHaveTextContent('/')
    await expect.element(data).toHaveTextContent('/data')
    await expect.element(root.getByTestId('multi-series-chart')).toBeVisible()
    await expect.element(data.getByTestId('multi-series-chart')).toBeVisible()
    expect(
      page
        .getByRole('combobox', { name: i18n.t('monitoring.mountPoint') })
        .elements()
    ).toHaveLength(0)
    expect(page.getByTestId('metric-card-inode').elements()).toHaveLength(0)

    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=filesystem.used_bytes') &&
            path.includes('series=filesystem.used_bytes-root')
        )
      )
      .toBe(true)
    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=filesystem.total_bytes') &&
            path.includes('series=filesystem.total_bytes-data')
        )
      )
      .toBe(true)
  })

  it('同一挂载点的不同设备按 mount+device 独立配对容量', async () => {
    const values: Record<string, number> = {
      'used-a': 1024,
      'total-a': 4096,
      'used-b': 8192,
      'total-b': 16384,
    }
    get.mockImplementation(async (path: string) => {
      if (path.includes('/series?') && path.includes('filesystem.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        const prefix = metric === 'filesystem.used_bytes' ? 'used' : 'total'
        return {
          series: [
            {
              seriesKey: `${prefix}-a`,
              dimensions: { mount: '/', device: '/dev/sda1' },
            },
            {
              seriesKey: `${prefix}-b`,
              dimensions: { mount: '/', device: '/dev/sdb1' },
            },
          ],
        }
      }
      if (path.includes('/metrics?') && path.includes('filesystem.')) {
        const seriesKey =
          new URL(path, 'http://localhost').searchParams.get('series') || ''
        return {
          state: 'ready',
          unit: 'B',
          stepNs: 60_000_000_000,
          points: [
            {
              timestamp: '2026-10-17T02:00:00Z',
              value: values[seriesKey] || 0,
              quality: 1,
            },
          ],
          gaps: [],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()
    const cardA = page.getByTestId('metric-card-disk-root-dev-sda1')
    const cardB = page.getByTestId('metric-card-disk-root-dev-sdb1')

    await expect.element(cardA).toHaveTextContent('/dev/sda1')
    await expect.element(cardB).toHaveTextContent('/dev/sdb1')
    await expect.element(cardA.getByTestId('multi-series-chart')).toBeVisible()
    await expect.element(cardB.getByTestId('multi-series-chart')).toBeVisible()

    const chartSeriesKeys = (card: typeof cardA) =>
      [
        ...new Set(
          [...card.element().querySelectorAll('g[data-series-key]')].map(
            (node) => node.getAttribute('data-series-key')
          )
        ),
      ].sort()
    expect(chartSeriesKeys(cardA)).toEqual([
      'filesystem.total_bytes:total-a',
      'filesystem.used_bytes:used-a',
    ])
    expect(chartSeriesKeys(cardB)).toEqual([
      'filesystem.total_bytes:total-b',
      'filesystem.used_bytes:used-b',
    ])
  })

  it('单个历史请求失败不影响其他图表，并可独立重试', async () => {
    let rootUsedFailed = false
    get.mockImplementation(async (path: string) => {
      if (
        !rootUsedFailed &&
        path.includes('/metrics?') &&
        path.includes('series=filesystem.used_bytes-root')
      ) {
        rootUsedFailed = true
        throw new Error('root history failed')
      }
      return defaultGet(path)
    })
    const page = await renderDetail()
    const root = page.getByTestId('metric-card-disk-root')
    const data = page.getByTestId('metric-card-disk-data')

    await expect
      .element(root.getByRole('alert'))
      .toHaveTextContent('root history failed')
    await expect.element(data.getByTestId('multi-series-chart')).toBeVisible()

    await userEvent.click(
      root.getByRole('button', { name: i18n.t('common.retry') })
    )
    await expect
      .poll(() =>
        [...root.element().querySelectorAll('g[data-series-key]')].some(
          (node) =>
            node.getAttribute('data-series-key') ===
            'filesystem.used_bytes:filesystem.used_bytes-root'
        )
      )
      .toBe(true)
    await expect.element(root.getByRole('alert')).not.toBeInTheDocument()
  })

  it('磁盘 I/O 与 IOPS 按设备拆分，每张图保留读写两条线', async () => {
    const page = await renderDetail()

    for (const device of ['nvme0n1', 'sda']) {
      for (const [base, seriesIds] of [
        ['metric-card-disk-io', ['disk.read_bps', 'disk.write_bps']],
        ['metric-card-disk-iops', ['disk.read_iops', 'disk.write_iops']],
      ] as const) {
        const card = page.getByTestId(`${base}-${device}`)
        await expect.element(card).toBeVisible()
        const chart = card.getByTestId('multi-series-chart')
        await expect.element(chart).toBeVisible()
        const rendered = [
          ...chart.element().querySelectorAll('g[data-series-id]'),
        ]
          .map((node) => ({
            id: node.getAttribute('data-series-id'),
            palette: node.getAttribute('data-palette-id'),
          }))
          .sort((a, b) => String(a.id).localeCompare(String(b.id)))
        expect(rendered).toEqual([
          { id: seriesIds[0], palette: 'series-1' },
          { id: seriesIds[1], palette: 'series-12' },
        ])
      }
    }

    expect(page.getByTestId('metric-card-disk-io').elements()).toHaveLength(0)
    expect(page.getByTestId('metric-card-disk-iops').elements()).toHaveLength(0)
    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=disk.read_bps') &&
            path.includes('series=read-sda')
        )
      )
      .toBe(true)
    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=disk.write_iops') &&
            path.includes('series=write-iops-nvme0n1')
        )
      )
      .toBe(true)
  })

  it('磁盘 I/O 与 IOPS 只显示整盘，分区 series 不生成卡片', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes')
        return [
          {
            id: 'node-1',
            hostname: 'node-1',
            inventory: {
              block_devices: [
                { name: 'sda', kind: 'disk' },
                { name: 'sda1', kind: 'partition' },
              ],
            },
          },
        ]
      if (path.includes('/series?') && path.includes('disk.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            { seriesKey: `${metric}-sda`, dimensions: { device: 'sda' } },
            { seriesKey: `${metric}-sda1`, dimensions: { device: 'sda1' } },
          ],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByTestId('metric-card-disk-io-sda'))
      .toBeVisible()
    await expect
      .element(page.getByTestId('metric-card-disk-iops-sda'))
      .toBeVisible()
    expect(
      page.getByTestId('metric-card-disk-io-sda1').elements()
    ).toHaveLength(0)
    expect(
      page.getByTestId('metric-card-disk-iops-sda1').elements()
    ).toHaveLength(0)
    expect(
      page
        .getByTestId('metric-card-disk-io')
        .getByText('sda1', { exact: false })
        .elements()
    ).toHaveLength(0)
  })

  it('整盘 series 缺失时不用分区数据回退', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes')
        return [
          {
            id: 'node-1',
            hostname: 'node-1',
            inventory: {
              block_devices: [
                { name: 'sda', kind: 'disk' },
                { name: 'sda1', kind: 'partition' },
              ],
            },
          },
        ]
      if (path.includes('/series?') && path.includes('disk.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            { seriesKey: `${metric}-sda1`, dimensions: { device: 'sda1' } },
          ],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()

    const ioCard = page.getByTestId('metric-card-disk-io')
    const iopsCard = page.getByTestId('metric-card-disk-iops')
    await expect.element(ioCard).toBeVisible()
    await expect.element(iopsCard).toBeVisible()
    expect(
      ioCard.element().querySelectorAll('[data-testid^="metric-card-disk-io-"]')
    ).toHaveLength(0)
    expect(
      iopsCard
        .element()
        .querySelectorAll('[data-testid^="metric-card-disk-iops-"]')
    ).toHaveLength(0)
    expect(ioCard.getByText('sda1', { exact: false }).elements()).toHaveLength(
      0
    )
  })

  it('缺少设备分类时按设备名规则排除分区', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return [{ id: 'node-1', hostname: 'node-1' }]
      if (path.includes('/series?') && path.includes('disk.')) {
        const metric =
          new URL(path, 'http://localhost').searchParams.get('metric') || ''
        return {
          series: [
            { seriesKey: `${metric}-sda`, dimensions: { device: 'sda' } },
            { seriesKey: `${metric}-sda1`, dimensions: { device: 'sda1' } },
            {
              seriesKey: `${metric}-nvme`,
              dimensions: { device: 'nvme0n1' },
            },
            {
              seriesKey: `${metric}-nvme-p1`,
              dimensions: { device: 'nvme0n1p1' },
            },
          ],
        }
      }
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByTestId('metric-card-disk-io-sda'))
      .toBeVisible()
    await expect
      .element(page.getByTestId('metric-card-disk-io-nvme0n1'))
      .toBeVisible()
    for (const testId of [
      'metric-card-disk-io-sda1',
      'metric-card-disk-io-nvme0n1p1',
      'metric-card-disk-iops-sda1',
      'metric-card-disk-iops-nvme0n1p1',
    ]) {
      expect(page.getByTestId(testId).elements()).toHaveLength(0)
    }
  })

  it('网卡切换后 RX/TX 按所选 interface 重新请求', async () => {
    const page = await renderDetail()
    const network = page.getByTestId('metric-card-network')
    await expect
      .element(
        network.getByRole('combobox', { name: i18n.t('monitoring.interface') })
      )
      .toBeVisible()

    await userEvent.click(
      network.getByRole('combobox', { name: i18n.t('monitoring.interface') })
    )
    await userEvent.click(page.getByRole('option', { name: 'lo' }))

    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=network.rx_bps') &&
            path.includes('series=rx-lo')
        )
      )
      .toBe(true)
    await expect
      .poll(() =>
        metricsPaths().some(
          (path) =>
            path.includes('metric=network.tx_bps') &&
            path.includes('series=tx-lo')
        )
      )
      .toBe(true)
  })

  it('自定义时间范围通过弹窗选择、按 start/end 查询并校验非法范围', async () => {
    const page = await renderDetail()
    await expect.element(page.getByTestId('metric-card-cpu')).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('monitoring.timeRange')))
      .not.toBeInTheDocument()

    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
    )
    await userEvent.click(
      page.getByRole('option', { name: i18n.t('monitoring.customRange') })
    )

    const start = page.getByLabelText(i18n.t('monitoring.startTime'))
    const end = page.getByLabelText(i18n.t('monitoring.endTime'))
    await expect.element(start).toBeVisible()
    await expect.element(end).toBeVisible()

    await userEvent.fill(start, '2026-10-17T00:00')
    await userEvent.fill(end, '2026-10-17T01:00')
    const expectedStart = new Date('2026-10-17T00:00').toISOString()
    const expectedEnd = new Date('2026-10-17T01:00').toISOString()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.apply') })
    )

    await expect
      .poll(() =>
        metricsPaths().some((path) => {
          const url = new URL(path, 'http://localhost')
          return (
            url.searchParams.get('start') === expectedStart &&
            url.searchParams.get('end') === expectedEnd
          )
        })
      )
      .toBe(true)

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('monitoring.editCustomRange') })
    )
    await userEvent.fill(
      page.getByLabelText(i18n.t('monitoring.endTime')),
      '2026-10-16T23:00'
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.apply') })
    )
    await expect
      .element(page.getByText(i18n.t('monitoring.invalidTimeRange')))
      .toBeVisible()
    expect(
      metricsPaths().some((path) => {
        const url = new URL(path, 'http://localhost')
        return (
          url.searchParams.get('start') === expectedStart &&
          url.searchParams.get('end') ===
            new Date('2026-10-16T23:00').toISOString()
        )
      })
    ).toBe(false)

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.cancel') })
    )
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
    )
    await userEvent.click(
      page.getByRole('option', { name: i18n.t('monitoring.lastHour') })
    )
    await expect
      .element(page.getByLabelText(i18n.t('monitoring.startTime')))
      .not.toBeInTheDocument()
  })

  it('自定义时间范围超过 30 天时提示错误', async () => {
    const page = await renderDetail()
    await expect.element(page.getByTestId('metric-card-cpu')).toBeVisible()

    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('monitoring.timeRange') })
    )
    await userEvent.click(
      page.getByRole('option', { name: i18n.t('monitoring.customRange') })
    )
    await userEvent.fill(
      page.getByLabelText(i18n.t('monitoring.startTime')),
      '2026-10-01T00:00'
    )
    await userEvent.fill(
      page.getByLabelText(i18n.t('monitoring.endTime')),
      '2026-11-15T00:00'
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.apply') })
    )

    await expect
      .element(page.getByText(i18n.t('monitoring.maxRange30Days')))
      .toBeVisible()
  })

  it('加载中显示加载态', async () => {
    get.mockImplementation(() => new Promise(() => {}))
    const page = await renderDetail()

    await expect.element(page.getByText(i18n.t('common.loading'))).toBeVisible()
  })

  it('overview 失败显示错误与重试', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview')
        throw new Error('节点详情加载失败')
      return defaultGet(path)
    })
    const page = await renderDetail()

    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('节点详情加载失败')
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.retry') }))
      .toBeVisible()
  })

  it('没有 series 时显示无数据而不是伪造 0', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/monitoring/nodes/overview') return { nodes: [OVERVIEW] }
      if (path.includes('/series?')) return { series: [] }
      return []
    })
    const page = await renderDetail()

    await expect
      .element(page.getByText(i18n.t('monitoring.noSeries')).first())
      .toBeVisible()
    await expect
      .element(page.getByTestId('metric-card-cpu'))
      .toHaveTextContent(i18n.t('monitoring.noSeries'))
  })
})
