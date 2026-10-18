import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Dashboard } from './index'

const { get } = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: window.location.pathname }),
    Link: ({ children }: { children?: ReactNode }) => <a href='#'>{children}</a>,
  }
})

const APP_OK = {
  id: 'app-ok',
  name: '正常应用',
  description: '',
  artifact_id: '',
  version: '1',
  binary_path: '/tmp/ok',
  arguments: [],
  environment: {},
  config: '',
  config_path: '',
  unit_name: '',
  revision: 1,
  updated_at: '2026-10-17T00:00:00Z',
  created_at: '2026-10-17T00:00:00Z',
}
const APP_FAULT = { ...APP_OK, id: 'app-fault', name: '故障应用' }

function stateOf(nodeId: string, health: string) {
  return {
    application_id: '',
    node_id: nodeId,
    version: '1',
    operation: 'deploy',
    health,
    updated_at: '2026-10-17T00:00:00Z',
  }
}

let faultStateCalls = 0

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <Dashboard />
      </QueryClientProvider>
    </SidebarProvider>
  )
}

describe('Dashboard 应用状态失败反馈与健康比真实性', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    faultStateCalls = 0
  })

  it('应用状态失败显示错误与重试，健康比显示不可用而不是 0/N', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/applications') return [APP_OK, APP_FAULT]
      if (path === `/applications/${APP_OK.id}/state`)
        return [stateOf('node-ok', 'healthy')]
      if (path === `/applications/${APP_FAULT.id}/state`) {
        faultStateCalls++
        if (faultStateCalls === 1) throw new Error('app state unavailable')
        return [stateOf('node-fault', 'healthy')]
      }
      return []
    })
    const page = await renderDashboard()

    // 失败必须可见：错误文案 + 重试入口
    await expect
      .element(page.getByRole('alert'))
      .toHaveTextContent('app state unavailable')
    const retry = page.getByRole('button', { name: '重试' })
    await expect.element(retry).toBeVisible()

    // 健康比不得因失败下降为 0/2，也不得显示假值
    const mainText = document.querySelector('main')?.textContent || ''
    expect(mainText).not.toContain('0/2')
    expect(mainText).toContain('健康状态不可用')
    // 状态失败的应用显示「未知」而不是「不健康」
    expect(mainText).toContain('未知')
    expect(mainText).not.toContain('不健康')

    // 重试后恢复真实健康比
    await userEvent.click(retry)
    await expect.element(page.getByRole('alert')).not.toBeInTheDocument()
    await expect.element(page.getByText('2/2 健康')).toBeVisible()
    expect(faultStateCalls).toBeGreaterThan(1)
  })

  it('全部状态成功时仍显示真实健康比，不误报不可用', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/applications') return [APP_OK, APP_FAULT]
      if (path === `/applications/${APP_OK.id}/state`)
        return [stateOf('node-ok', 'healthy')]
      if (path === `/applications/${APP_FAULT.id}/state`)
        return [stateOf('node-fault', 'unhealthy')]
      return []
    })
    const page = await renderDashboard()

    await expect.element(page.getByText('1/2 健康')).toBeVisible()
    await expect.element(page.getByRole('alert')).not.toBeInTheDocument()
    await expect
      .element(page.getByRole('button', { name: '重试' }))
      .not.toBeInTheDocument()
  })
})
