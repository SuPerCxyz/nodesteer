import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ApplicationEditor } from './index'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
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
    Link: ({ children }: { children?: ReactNode }) => <a href='#'>{children}</a>,
  }
})

const APP = 'application-state-card'
const STATE_UPDATED_AT = '2026-10-16T07:59:53.146Z'

function healthCard(): Element | undefined {
  return Array.from(document.querySelectorAll('[data-slot=card]')).find(
    (element) =>
      element.querySelector('[data-slot=card-title]')?.textContent === '健康检查'
  )
}

function renderEditor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ApplicationEditor />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('应用详情 state 卡片字段完整性', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', `/applications/${APP}`)
    get.mockImplementation(async (path: string) => {
      if (path === `/applications/${APP}`)
        return {
          id: APP,
          name: '字段应用',
          description: '',
          artifact_id: '',
          version: '2.0',
          binary_path: '/tmp/state-card',
          arguments: [],
          environment: {},
          config: '',
          config_path: '',
          unit_name: '',
          revision: 1,
          updated_at: '2026-10-16T00:00:00Z',
          created_at: '2026-10-16T00:00:00Z',
        }
      if (path === '/nodes')
        return [{ id: 'node-state', hostname: '状态节点', status: 'online' }]
      if (path === `/applications/${APP}/nodes`) return ['node-state']
      if (path === `/applications/${APP}/executions`) return []
      if (path === `/applications/${APP}/state`)
        return [
          {
            application_id: APP,
            node_id: 'node-state',
            version: '1.1',
            operation: 'deploy',
            health: 'healthy',
            updated_at: STATE_UPDATED_AT,
          },
        ]
      return []
    })
  })

  it('渲染节点 state 的 version 与 updated_at', async () => {
    await renderEditor()

    // 健康检查卡片出现该节点行
    await expect
      .poll(() => healthCard()?.textContent || '')
      .toContain('状态节点')
    // 版本字段必须出现在健康检查卡片内
    await expect
      .poll(() => healthCard()?.textContent || '')
      .toContain('版本 1.1')
    // updated_at 必须渲染为时间元素（相对时间 + 完整时间 title）
    const time = healthCard()?.querySelector('time')
    expect(time).toBeTruthy()
    expect(time?.getAttribute('title')).toContain('2026')
    // 既有 operation 反馈保留
    await expect.poll(() => healthCard()?.textContent || '').toContain('deploy')
  })
})
