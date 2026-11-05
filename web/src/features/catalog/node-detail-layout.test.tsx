import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import type { Node } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AgentDetail } from './index'

const { get, permissions } = vi.hoisted(() => ({
  get: vi.fn(),
  permissions: { admin: true },
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => permissions.admin,
  useCanRun: () => true,
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({
      children,
      to: _to,
      ...props
    }: {
      children?: React.ReactNode
      to?: string
      'aria-label'?: string
      title?: string
    }) => (
      <a href='#' {...props}>
        {children}
      </a>
    ),
  }
})

const NODE: Node = {
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
  inventory: {
    os: 'Ubuntu',
    os_version: '26.04',
    kernel: '6.8.0-generic',
    arch: 'amd64',
    cpu: [{ model: 'Xeon', cores: 8, mhz: 2400 }],
    memory: { total_kb: 16 * 1024 * 1024, available_kb: 8 * 1024 * 1024 },
    filesystem: Array.from({ length: 12 }, (_, index) => ({
      mount: `/mnt/long-mount-point-${index}`,
      device: `/dev/sd${index}`,
      fs_type: 'ext4',
      total_kb: 2048,
      free_kb: 1024,
    })),
    network: [
      { interface: 'enp1s0', addresses: ['192.0.2.10/24'], mac: '' },
      { interface: 'enp2s0', addresses: ['192.0.2.11/24'], mac: '' },
    ],
  },
}

async function renderNodeDetail() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <AgentDetail />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('节点详情布局', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    permissions.admin = true
    get.mockReset()
    window.history.replaceState({}, '', '/agents/node-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes/node-1') return NODE
      if (path.startsWith('/executions?')) return []
      if (path === '/tasks') return []
      if (path === '/groups') return []
      if (path === '/schedules') return []
      if (path === '/applications') return []
      if (path === '/monitoring/nodes/overview')
        return {
          nodes: [
            {
              node_id: 'node-1',
              hostname: 'node-a',
              status: 'online',
              state: 'ready',
              cpu: 42.5,
              memory: 61,
              disk: 70,
              load1: 0.85,
              load5: 0.65,
              load15: 0.55,
              network_rx_bps: 1024,
              network_tx_bps: 2048,
              temperature_c: 45.5,
              uptime_seconds: 90061,
              observed_at: '2026-10-17T02:05:00Z',
            },
          ],
        }
      return []
    })
  })

  it('返回入口在标题行，概览与清单各占整行并上下排列', async () => {
    const page = await renderNodeDetail()
    await expect
      .element(page.getByText(i18n.t('nodes.overview')).first())
      .toBeVisible()

    const back = page.getByRole('link', { name: i18n.t('common.back') })
    await expect.element(back).toBeVisible()
    const header = document.querySelector('header') as HTMLElement
    // 返回按钮统一放在标题行，不再单独占一行。
    expect(header.contains(back.element() as globalThis.Node)).toBe(true)

    const main = document.querySelector('main') as HTMLElement
    const grid = main.querySelector(':scope > div.grid') as HTMLElement
    expect(grid).not.toBeNull()
    expect(main.querySelector(':scope > a')).toBeNull()
    expect(grid.firstElementChild?.textContent).toContain(
      i18n.t('nodes.overview')
    )

    const fullRows = [...grid.children].filter((element) =>
      element.className.includes('2xl:col-span-2')
    )
    expect(fullRows[0]?.textContent).toContain(i18n.t('nodes.overview'))
    expect(fullRows[1]?.textContent).toContain(i18n.t('nodes.inventory'))
  })

  it('顶部显示 Beszel 风格节点监控概览行', async () => {
    const page = await renderNodeDetail()
    const row = page.getByTestId('node-monitoring-overview-row')
    await expect.element(row).toBeVisible()
    await expect.element(row).toHaveTextContent('42.5%')
    await expect.element(row).toHaveTextContent('61.0%')
    await expect.element(row).toHaveTextContent('70.0%')
    await expect.element(row).toHaveTextContent('0.85 / 0.65 / 0.55')
    await expect.element(row).toHaveTextContent('3.0 KB/s')
    await expect.element(row).toHaveTextContent('45.5 °C')
    await expect.element(row).toHaveTextContent('1 天 1 小时')
    await expect
      .element(row.getByRole('link', { name: i18n.t('dashboard.viewDetails') }))
      .toHaveAttribute('href', '/monitoring/nodes/node-1')
  })

  it('文件系统与网络默认折叠，仅显示摘要，展开后显示完整内容', async () => {
    const page = await renderNodeDetail()

    await expect
      .element(page.getByText('/mnt/long-mount-point-0 等 12 项'))
      .toBeVisible()
    await expect
      .element(page.getByText('enp1s0, enp2s0，共 2 个接口'))
      .toBeVisible()
    await expect
      .element(page.getByText('/mnt/long-mount-point-5'))
      .not.toBeInTheDocument()

    await page.getByRole('button', { name: '展开全部 文件系统' }).click()
    await expect
      .element(page.getByText('/mnt/long-mount-point-5'))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: '收起 文件系统' }))
      .toBeVisible()

    await page.getByRole('button', { name: '展开全部 网络' }).click()
    await expect.element(page.getByText('192.0.2.10/24')).toBeVisible()
  })

  for (const role of ['operator', 'viewer']) {
    it(`${role} can read the node without requesting administrator monitoring`, async () => {
      permissions.admin = false
      const page = await renderNodeDetail()
      await expect
        .element(page.getByText(i18n.t('nodes.inventory')).first())
        .toBeVisible()
      expect(
        page.getByTestId('node-monitoring-overview-row').elements()
      ).toHaveLength(0)
      expect(
        get.mock.calls.some(([path]) => path === '/monitoring/nodes/overview')
      ).toBe(false)
    })
  }
})
