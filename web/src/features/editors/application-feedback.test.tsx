import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { setToken } from '@/lib/api'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ApplicationEditor } from './index'

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
    Link: ({ children }: { children?: ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

let historyReads = 0
let healthReads = 0
let terminalHistoryRead = false
let healthAfterTerminalHistory: boolean[] = []
const APP = 'application-qa'

async function renderEditor() {
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

describe('Application失败反馈与会话边界', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })
  beforeEach(() => {
    historyReads = 0
    healthReads = 0
    terminalHistoryRead = false
    healthAfterTerminalHistory = []
    post.mockReset()
    get.mockReset()
    setToken('owned-ui-session')
    window.history.replaceState({}, '', `/applications/${APP}`)
    get.mockImplementation(async (path: string) => {
      if (path === `/applications/${APP}`)
        return {
          id: APP,
          name: '测试应用',
          version: '1',
          binary_path: '/tmp/qa',
          arguments: [],
          environment: {},
          enabled: true,
        }
      if (path === '/nodes')
        return [{ id: 'node-qa', hostname: '测试节点', status: 'online' }]
      if (path === `/applications/${APP}/nodes`) return ['node-qa']
      if (path === `/applications/${APP}/executions`) {
        historyReads++
        if (post.mock.calls.length) {
          await Promise.resolve()
          terminalHistoryRead = true
        }
        return post.mock.calls.length
          ? [{ id: 'partial-execution', status: 'SUCCESS' }]
          : []
      }
      if (path === `/applications/${APP}/state`) {
        healthReads++
        if (post.mock.calls.length)
          healthAfterTerminalHistory.push(terminalHistoryRead)
        return post.mock.calls.length
          ? [{ node_id: 'node-qa', health: 'healthy', operation: 'deploy' }]
          : []
      }
      return []
    })
  })
  afterEach(() => {
    setToken(null)
  })

  it('POST失败仍重新读取历史/健康，不掩盖已经执行的部分节点', async () => {
    post.mockRejectedValue(new Error('partial dispatch rejected'))
    const page = await renderEditor()
    const deploy = page.getByRole('button', { name: '部署', exact: true })
    await expect.element(deploy).toBeEnabled()
    const initialHistory = historyReads
    const initialHealth = healthReads
    await userEvent.click(deploy)
    await expect.poll(() => historyReads).toBeGreaterThan(initialHistory)
    await expect.poll(() => healthReads).toBeGreaterThan(initialHealth)
    expect(healthAfterTerminalHistory.length).toBeGreaterThan(0)
    expect(healthAfterTerminalHistory.every(Boolean)).toBe(true)
    await expect
      .element(page.getByText('partial dispatch rejected', { exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByText('SUCCESS', { exact: true }))
      .toBeVisible()
    await expect.element(deploy).toBeEnabled()
  })

  it('请求期间换会话后不再刷新旧页面或显示旧失败', async () => {
    post.mockImplementation(async () => {
      setToken('replacement-ui-session')
      throw new Error('old request failed')
    })
    const page = await renderEditor()
    const deploy = page.getByRole('button', { name: '部署', exact: true })
    await expect.element(deploy).toBeEnabled()
    const initialHistory = historyReads
    const initialHealth = healthReads
    await userEvent.click(deploy)
    await expect.poll(() => post.mock.calls.length).toBe(1)
    expect(historyReads).toBe(initialHistory)
    expect(healthReads).toBe(initialHealth)
    await expect
      .element(page.getByText('old request failed', { exact: true }))
      .not.toBeInTheDocument()
  })

  it('真实array响应先PENDING，页面无需reload自动收敛到终态与健康', async () => {
    let finished = false
    const initialGet = get.getMockImplementation()!
    get.mockImplementation(async (path: string) => {
      if (path === `/applications/${APP}/executions`) {
        historyReads++
        return post.mock.calls.length
          ? [
              {
                id: 'pending-execution',
                task_id: APP,
                node_id: 'node-qa',
                status: finished ? 'SUCCESS' : 'PENDING',
              },
            ]
          : []
      }
      if (path === `/applications/${APP}/state`) {
        healthReads++
        return finished
          ? [{ node_id: 'node-qa', health: 'healthy', operation: 'deploy' }]
          : []
      }
      return initialGet(path)
    })
    post.mockResolvedValue([
      {
        id: 'pending-execution',
        task_id: APP,
        node_id: 'node-qa',
        status: 'PENDING',
      },
    ])
    const page = await renderEditor()
    const deploy = page.getByRole('button', { name: '部署', exact: true })
    await expect.element(deploy).toBeEnabled()
    await userEvent.click(deploy)
    await expect
      .element(page.getByText('PENDING', { exact: true }))
      .toBeVisible()
    await expect.element(deploy).toBeDisabled()
    // 模拟Agent在首轮查询后完成；保持当前页面，不通过reload驱动取数。
    finished = true
    await expect
      .poll(
        () => document.querySelector('main')?.textContent?.includes('SUCCESS'),
        { timeout: 8000 }
      )
      .toBe(true)
    await expect
      .poll(
        () => {
          const card = Array.from(
            document.querySelectorAll('[data-slot=card]')
          ).find(
            (element) =>
              element.querySelector('[data-slot=card-title]')?.textContent ===
              '健康检查'
          )
          return card?.querySelector('[data-slot=badge]')?.textContent
        },
        { timeout: 8000 }
      )
      .toBe('健康')
    await expect.element(deploy).toBeEnabled()
    expect(historyReads).toBeGreaterThan(2)
    expect(healthReads).toBeGreaterThan(2)
  })
})
