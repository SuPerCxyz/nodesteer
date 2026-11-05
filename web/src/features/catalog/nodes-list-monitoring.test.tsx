import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import type { Node } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Agents } from './index'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => false,
  useCanRun: () => false,
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
}

function renderAgents() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <Agents />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('节点列表页', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return [NODE]
      if (path === '/groups') return []
      return []
    })
  })

  it('不再挂载节点当前监控卡片，也不请求监控概览', async () => {
    const page = await renderAgents()

    await expect.element(page.getByText('node-a')).toBeVisible()
    await expect.element(page.getByText('节点当前监控')).not.toBeInTheDocument()
    expect(
      get.mock.calls.some(([path]) => path === '/monitoring/nodes/overview')
    ).toBe(false)
  })
})
