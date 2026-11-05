import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { userEvent } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import type { NodeEnrollment } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Agents } from './index'

const { get, post } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}))

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

const ENROLLMENT: NodeEnrollment = {
  node_id: 'node-new',
  agent_id: 'agent-new',
  endpoint: 'agent.example.com:18444',
  inbound_bundle_available: true,
  gateway_url: 'ws://hub.example:8080',
  gateway_base_url: 'http://hub.example:8080',
  agent_image: 'nodesteer/agent:latest',
  native: 'curl -fsSL http://hub.example/install.sh | sh',
  docker_run: 'docker run nodesteer/agent:latest',
  docker_compose: 'services: {}',
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

describe('添加节点 endpoint', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    window.history.replaceState({}, '', '/agents')
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes') return []
      if (path === '/groups') return []
      return []
    })
    post.mockResolvedValue(ENROLLMENT)
  })

  it('endpoint 为必填并随纳管请求提交，bundle 可用时提供下载入口', async () => {
    const page = await renderAgents()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('nodes.addNode') })
    )
    const endpoint = page.getByTestId('enrollment-endpoint')
    await expect.element(endpoint).toHaveAttribute('required')

    await userEvent.fill(
      page.getByLabelText(i18n.t('nodes.nodeName')),
      '新节点'
    )
    await userEvent.fill(
      page.getByLabelText(i18n.t('nodes.nodeAddress')),
      '192.0.2.20'
    )
    await userEvent.fill(endpoint, 'agent.example.com')
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('nodes.generateCommand') })
    )

    expect(post).toHaveBeenCalledWith('/nodes/enrollment', {
      node_name: '新节点',
      node_ip: '192.0.2.20',
      endpoint: 'agent.example.com',
      hub_address: window.location.origin,
    })
    await expect
      .element(page.getByTestId('enrollment-download-bundle'))
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('nodes.inboundBundleHint')))
      .toBeVisible()
  })
})
