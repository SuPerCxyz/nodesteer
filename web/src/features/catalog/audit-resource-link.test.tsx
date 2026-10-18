import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import type { AuditLog } from '@/lib/api'
import { Audit } from './index'

const { getPage } = vi.hoisted(() => ({ getPage: vi.fn() }))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, getPage } }
})

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({ children }: { children?: React.ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

function auditRow(
  id: string,
  resource: string,
  resourceId: string
): AuditLog {
  return {
    id,
    user_id: 'u1',
    username: 'admin',
    action: 'update',
    resource,
    resource_id: resourceId,
    detail: '',
    created_at: '2026-10-17T00:00:00Z',
  }
}

const ROWS: AuditLog[] = [
  auditRow('a1', 'task', 'task-1'),
  auditRow('a2', 'node', 'node-1'),
  auditRow('a3', 'setting', 'heartbeat_interval_sec'),
]

async function renderAudit() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <Audit />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('审计列表资源 ID 跳转（OBS-20261017-CROSSPAGE-LINK-GAPS ①）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    getPage.mockReset()
    getPage.mockResolvedValue({ items: ROWS, total: ROWS.length })
  })

  it('有详情路由的资源 ID 渲染为链接', async () => {
    const page = await renderAudit()

    await expect
      .element(page.getByRole('link', { name: 'task-1' }))
      .toHaveAttribute('href', '/tasks/task-1')
    await expect
      .element(page.getByRole('link', { name: 'node-1' }))
      .toHaveAttribute('href', '/agents/node-1')
  })

  it('无详情路由的资源 ID 保持纯文本，不产生死链', async () => {
    const page = await renderAudit()

    await expect
      .element(page.getByText('heartbeat_interval_sec'))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: 'heartbeat_interval_sec' }))
      .not.toBeInTheDocument()
  })
})
