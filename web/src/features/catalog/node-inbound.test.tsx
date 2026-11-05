import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import type { Node, NodeConnectionTestResult } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AgentDetail } from './index'

const { get, post, authenticatedFetch, permissions } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  authenticatedFetch: vi.fn(),
  permissions: { admin: true, run: true },
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, get, post },
    authenticatedFetch,
  }
})

vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => permissions.admin,
  useCanRun: () => permissions.run,
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
  display_name: '入站节点',
  ip: '192.0.2.10',
  os: 'linux',
  arch: 'amd64',
  agent_version: '1.0.0',
  deployment_mode: 'native',
  host_integration: false,
  status: 'offline',
  labels: {},
  capabilities: {},
  global_revision: 1,
  sync_status: 'synced',
  last_seen: '2026-10-17T00:00:00Z',
  first_seen: '2026-10-01T00:00:00Z',
  endpoint: 'agent.example.com:18444',
  connection_mode: 'offline',
  inbound_bundle_stale: false,
  inbound: {
    configured: true,
    capable: true,
    data_plane: true,
    cert_fingerprint: 'sha256:abcdef',
    cert_not_after: '2027-10-17T00:00:00Z',
    last_handshake_at: '2026-10-17T00:00:00Z',
    last_error: '',
  },
}

const TEST_RESULT: NodeConnectionTestResult = {
  node_id: 'node-1',
  endpoint: 'agent.example.com:18444',
  connected: false,
  test_mode: true,
  stages: [
    { name: 'dns', ok: true, duration_ms: 1 },
    {
      name: 'tcp',
      ok: false,
      code: 'tcp_failed',
      error: 'connection refused',
      duration_ms: 5,
    },
  ],
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

describe('节点入站连接详情', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    permissions.admin = true
    permissions.run = true
    get.mockReset()
    post.mockReset()
    authenticatedFetch.mockReset()
    authenticatedFetch.mockResolvedValue(
      new Response(new Blob(['bundle']), {
        status: 200,
        headers: {
          'Content-Disposition': 'attachment; filename="node.tar.gz"',
        },
      })
    )
    window.history.replaceState({}, '', '/agents/node-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes/node-1') return NODE
      if (path.startsWith('/executions?')) return []
      if (path === '/tasks') return []
      if (path === '/groups') return []
      if (path === '/schedules') return []
      if (path === '/applications') return []
      return []
    })
    post.mockResolvedValue({ ok: 'true' })
  })

  it('管理员可见入站诊断与下载/轮换/测试/编辑入口', async () => {
    const page = await renderNodeDetail()
    await expect.element(page.getByTestId('node-inbound-section')).toBeVisible()
    await expect
      .element(page.getByText('agent.example.com:18444').first())
      .toBeVisible()
    await expect.element(page.getByTestId('node-edit-endpoint')).toBeVisible()
    await expect.element(page.getByTestId('node-rotate-bundle')).toBeVisible()
    await expect.element(page.getByTestId('node-test-connection')).toBeVisible()
  })

  it('Operator 不显示管理操作但保留诊断信息', async () => {
    permissions.admin = false
    const page = await renderNodeDetail()
    await expect.element(page.getByTestId('node-inbound-section')).toBeVisible()
    expect(page.getByTestId('node-rotate-bundle').elements()).toHaveLength(0)
    expect(page.getByTestId('node-edit-endpoint').elements()).toHaveLength(0)
    expect(page.getByTestId('node-test-connection').elements()).toHaveLength(0)
  })

  it('测试连接展示分阶段结果', async () => {
    post.mockResolvedValueOnce(TEST_RESULT)
    const page = await renderNodeDetail()
    await userEvent.click(page.getByTestId('node-test-connection'))
    await expect
      .element(page.getByTestId('node-connection-test-result'))
      .toHaveTextContent('tcp')
    await expect
      .element(page.getByTestId('node-connection-test-result'))
      .toHaveTextContent('tcp_failed')
    expect(post).toHaveBeenCalledWith('/nodes/node-1/connection-test', {})
  })

  it('编辑 endpoint 提交规范化字段并提示重新签发', async () => {
    const page = await renderNodeDetail()
    await userEvent.click(page.getByTestId('node-edit-endpoint'))
    const input = page.getByTestId('node-endpoint-input')
    await userEvent.fill(input, 'agent2.example.com')
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.save') })
    )
    expect(post).toHaveBeenCalledWith('/nodes/node-1', {
      endpoint: 'agent2.example.com',
    })
  })

  it('轮换 bundle 前要求确认并调用 rotate 接口', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const page = await renderNodeDetail()
    await userEvent.click(page.getByTestId('node-rotate-bundle'))
    expect(confirm).toHaveBeenCalled()
    expect(authenticatedFetch).toHaveBeenCalledWith(
      '/api/nodes/node-1/inbound-bundle/rotate',
      { method: 'POST' }
    )
    confirm.mockRestore()
  })

  it('bundle 过期时显示下载入口并隐藏轮换', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes/node-1') {
        return {
          ...NODE,
          inbound_bundle_stale: true,
          inbound: { ...NODE.inbound, configured: false },
        }
      }
      if (path.startsWith('/executions?')) return []
      return []
    })
    const page = await renderNodeDetail()
    await expect.element(page.getByTestId('node-download-bundle')).toBeVisible()
    expect(page.getByTestId('node-rotate-bundle').elements()).toHaveLength(0)
  })
})
