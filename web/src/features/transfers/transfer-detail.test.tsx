import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import type { FileTransfer, Node } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { FileTransfers, TransferDetail } from './index'

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

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
    Link: ({ children, to }: { children?: ReactNode; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

const NODES: Node[] = [
  { id: 'node-a', hostname: 'agent-a' } as Node,
  { id: 'node-b', hostname: 'agent-b' } as Node,
]

const TRANSFER: FileTransfer = {
  id: 'tr-1',
  source_node_id: 'node-a',
  source_path: '/tmp/source.bin',
  size: 2048,
  sha256: 'deadbeef',
  status: 'DELIVERING',
  created_at: '2026-10-17T00:00:00Z',
  updated_at: '2026-10-17T00:01:00Z',
  targets: [
    {
      transfer_id: 'tr-1',
      node_id: 'node-b',
      destination_path: '/tmp/destination.bin',
      status: 'DELIVERING',
      updated_at: '2026-10-17T00:01:00Z',
    },
  ],
}

function renderPage(element: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>{element}</QueryClientProvider>
    </SidebarProvider>
  )
}

describe('传输详情页与状态本地化（OBS-20261017-CROSSPAGE-LINK-GAPS ② / OBS-20261014-TRANSFER-STATUS-BADGE-NOT-LOCALIZED）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    window.history.replaceState({}, '', '/transfers/tr-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/transfers/tr-1') return TRANSFER
      if (path === '/transfers') return [TRANSFER]
      if (path === '/nodes') return NODES
      return []
    })
  })

  it('详情页展示传输与目标回链，DELIVERING 徽标本地化为「投递中」', async () => {
    const page = await renderPage(<TransferDetail />)

    await expect
      .element(page.getByRole('link', { name: 'agent-a' }))
      .toHaveAttribute('href', '/agents/node-a')
    await expect
      .element(page.getByRole('link', { name: 'agent-b' }))
      .toHaveAttribute('href', '/agents/node-b')
    // 传输状态与目标状态各一个徽标：都必须是中文「投递中」，不能是原始枚举
    await expect.element(page.getByText('投递中').first()).toBeVisible()
    await expect.element(page.getByText('DELIVERING')).not.toBeInTheDocument()
    // 进行中的传输可取消（危险操作在 overflow 菜单）、不可重试
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.actions') }).first()
    )
    await expect
      .element(page.getByRole('menuitem', { name: i18n.t('transfers.cancel') }))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('transfers.retry') }))
      .not.toBeInTheDocument()
  })

  it('列表行提供到详情页的「查看详情」链接', async () => {
    const page = await renderPage(<FileTransfers />)

    await expect
      .element(page.getByRole('link', { name: i18n.t('common.viewDetails') }))
      .toHaveAttribute('href', '/transfers/tr-1')
  })
  it('空列表空态与工具栏位于同一张列表 Card 内', async () => {
    window.history.replaceState({}, '', '/transfers')
    get.mockImplementation(async (path: string) =>
      path === '/nodes' ? NODES : []
    )
    const page = await renderPage(<FileTransfers />)

    await expect
      .element(page.getByText(i18n.t('transfers.empty')))
      .toBeVisible()
    const empty = page.getByText(i18n.t('transfers.empty')).element()
    expect(empty.closest('[data-card="list"]')).not.toBeNull()
    expect(
      page
        .getByRole('textbox', {
          name: i18n.t('transfers.searchPlaceholder'),
        })
        .element()
    ).toBeTruthy()
  })
  it('locks the transfer draft and new action until a delayed create request settles', async () => {
    let release!: (value: FileTransfer) => void
    post.mockImplementationOnce(
      () =>
        new Promise<FileTransfer>((resolve) => {
          release = resolve
        })
    )
    const page = await renderPage(<FileTransfers />)
    const newButton = page.getByRole('button', {
      name: i18n.t('transfers.newTransfer'),
      exact: true,
    })
    await userEvent.click(newButton)
    await userEvent.click(
      page.getByRole('combobox', {
        name: i18n.t('transfers.source'),
        exact: true,
      })
    )
    await userEvent.click(page.getByRole('option', { name: 'agent-a' }))
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('transfers.sourcePath') }),
      '/tmp/a'
    )
    await userEvent.click(
      page.getByRole('combobox', {
        name: i18n.t('transfers.target'),
        exact: true,
      })
    )
    await userEvent.click(page.getByRole('option', { name: 'agent-b' }))
    await userEvent.fill(
      page.getByRole('textbox', { name: i18n.t('transfers.destinationPath') }),
      '/tmp/b'
    )
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('transfers.addTarget'),
        exact: true,
      })
    )
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('transfers.start'), exact: true })
    )
    await expect.poll(() => post.mock.calls.length).toBe(1)
    await expect.element(newButton).toBeDisabled()
    await expect
      .element(
        page.getByRole('textbox', { name: i18n.t('transfers.sourcePath') })
      )
      .toBeDisabled()
    await expect
      .element(
        page
          .getByRole('region')
          .getByRole('button', { name: i18n.t('common.cancel'), exact: true })
      )
      .toBeDisabled()
    release(TRANSFER)
    await expect.element(newButton).toBeEnabled()
    expect(page.getByRole('region').elements()).toHaveLength(0)
  })
})

describe('入站数据面不可用提示', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    window.history.replaceState({}, '', '/transfers')
    get.mockImplementation(async (path: string) => {
      if (path === '/transfers') return []
      if (path === '/nodes') {
        return [
          {
            ...NODES[0],
            connection_mode: 'inbound',
            inbound: {
              configured: true,
              capable: true,
              data_plane: false,
              cert_fingerprint: '',
              cert_not_after: '',
              last_handshake_at: '',
              last_error: '',
            },
          },
          NODES[1],
        ]
      }
      return []
    })
  })

  it('选中入站无数据面节点时显示 banner 并禁用提交', async () => {
    const page = await renderPage(<FileTransfers />)
    await userEvent.click(
      page.getByRole('button', {
        name: i18n.t('transfers.newTransfer'),
        exact: true,
      })
    )
    await userEvent.click(
      page.getByRole('combobox', {
        name: i18n.t('transfers.source'),
        exact: true,
      })
    )
    await userEvent.click(page.getByRole('option', { name: 'agent-a' }))
    await expect
      .element(page.getByTestId('inbound-data-plane-banner'))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('transfers.start') }))
      .toBeDisabled()
  })
})
