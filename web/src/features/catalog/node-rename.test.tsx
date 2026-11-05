import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { userEvent } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import type { Node } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AgentDetail } from './index'

const { get, post, permissions } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  permissions: { admin: true, run: true },
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get, post } }
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
  display_name: '生产数据库 01',
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

describe('节点重命名入口', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    permissions.admin = true
    permissions.run = true
    get.mockReset()
    post.mockReset()
    window.history.replaceState({}, '', '/agents/node-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/nodes/node-1') return NODE
      if (path.startsWith('/executions?')) return []
      if (path === '/tasks') return []
      if (path === '/groups') return []
      if (path === '/schedules') return []
      if (path === '/applications') return []
      if (path === '/monitoring/nodes/overview') return { nodes: [] }
      return []
    })
    post.mockResolvedValue({ ok: 'true' })
  })

  it('Operator 可见重命名入口，弹窗预填并可提交裁剪后的值', async () => {
    const page = await renderNodeDetail()
    const renameButton = page.getByRole('button', {
      name: i18n.t('nodes.rename'),
    })
    await expect.element(renameButton).toBeVisible()
    // 详情标题与概览均显示 display_name。
    await expect
      .element(page.getByText('生产数据库 01').first())
      .toBeVisible()
    await expect.element(page.getByText('node-a').first()).toBeVisible()

    await userEvent.click(renameButton)
    const input = page.getByLabelText(i18n.t('nodes.displayName'))
    await expect.element(input).toHaveValue('生产数据库 01')
    await userEvent.fill(input, '  新显示名  ')
    await expect
      .element(page.getByText(i18n.t('nodes.renameHint'), { exact: true }))
      .toBeVisible()
    await userEvent.click(page.getByRole('button', { name: i18n.t('common.save') }))

    expect(post).toHaveBeenCalledWith('/nodes/node-1', {
      display_name: '新显示名',
    })
  })

  it('Viewer 不显示重命名入口', async () => {
    permissions.run = false
    const page = await renderNodeDetail()
    await expect
      .element(page.getByText(i18n.t('nodes.overview')).first())
      .toBeVisible()
    expect(
      page.getByRole('button', { name: i18n.t('nodes.rename') }).elements()
    ).toHaveLength(0)
  })

  it('提交失败展示后端错误且不关闭弹窗', async () => {
    post.mockRejectedValueOnce(new Error('display_name rejected by hub'))
    const page = await renderNodeDetail()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('nodes.rename') })
    )
    const input = page.getByLabelText(i18n.t('nodes.displayName'))
    await userEvent.fill(input, '新名字')
    await userEvent.click(page.getByRole('button', { name: i18n.t('common.save') }))
    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('display_name rejected by hub')
    await expect.element(input).toBeVisible()
  })

  it('超过 64 个字符时禁用提交并提示', async () => {
    const page = await renderNodeDetail()
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('nodes.rename') })
    )
    const input = page.getByLabelText(i18n.t('nodes.displayName'))
    await userEvent.fill(input, 'a'.repeat(65))
    await expect
      .element(page.getByText('65/64'))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.save') }))
      .toBeDisabled()
    expect(post).not.toHaveBeenCalled()
  })

  it('重命名与端点弹窗使用独立 key，不产生重复 key 警告', async () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    try {
      const page = await renderNodeDetail()
      await expect
        .element(page.getByText(i18n.t('nodes.overview')).first())
        .toBeVisible()
      const duplicateKeyWarning = errorSpy.mock.calls
        .flat()
        .some((message) => String(message).includes('same key'))
      expect(duplicateKeyWarning).toBe(false)
    } finally {
      errorSpy.mockRestore()
    }
  })
})
