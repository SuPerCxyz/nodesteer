import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ApplicationEditor, GroupEditor, ScheduleEditor, ScriptEditor } from './index'

const { get, post, put } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get, post, put } }
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
    Link: ({ children }: { children?: ReactElement }) => <a href='#'>{children}</a>,
  }
})

function renderEditor(element: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>{element}</QueryClientProvider>
    </SidebarProvider>
  )
}

/** 点击保存 → 断言按钮在请求在途时禁用，且第二次点击不会发出第二个请求 */
async function expectSingleSubmit(
  element: ReactElement,
  path: string,
  method: 'post' | 'put'
) {
  window.history.replaceState({}, '', path)
  const page = await renderEditor(element)
  const save = page.getByRole('button', { name: i18n.t('common.save') })

  await expect.element(save).toBeEnabled()
  await userEvent.click(save)
  await expect.element(save).toBeDisabled()
  await expect.element(save).toHaveAttribute('aria-busy', 'true')

  // 按钮已禁用，浏览器不会派发第二次 click
  ;(save.element() as HTMLButtonElement).click()
  expect(method === 'post' ? post : put).toHaveBeenCalledTimes(1)
}

describe('编辑器保存 pending 期间禁用按钮', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    // 保存请求一直挂起，模拟后端处理中
    post.mockImplementation(() => new Promise(() => {}))
    put.mockImplementation(() => new Promise(() => {}))
    get.mockResolvedValue([])
  })

  it('调度编辑器：编辑既有调度重复点击只发一次 PUT', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks') return [{ id: 'task-1', name: '部署任务' }]
      if (path === '/schedules/sch-1')
        return {
          id: 'sch-1',
          task_id: 'task-1',
          revision: 1,
          type: 'cron',
          expression: '*/5 * * * *',
          interval_sec: 60,
          run_at: '',
          timezone: 'Asia/Shanghai',
          execution_owner: 'agent',
          offline_policy: 'allow_offline',
          misfire_policy: 'run_once',
          enabled: true,
        }
      return []
    })

    await expectSingleSubmit(<ScheduleEditor />, '/schedules/sch-1', 'put')
    expect(put).toHaveBeenCalledWith(
      '/schedules/sch-1',
      expect.objectContaining({ task_id: 'task-1' })
    )
  })

  it('脚本编辑器：编辑既有脚本重复点击只发一次 PUT', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/scripts/sc-1')
        return {
          id: 'sc-1',
          name: '巡检脚本',
          description: '',
          interpreter: 'shell',
          content: '#!/bin/sh\necho hi',
          parameters: [],
          environment: {},
          working_dir: '',
          timeout: 300,
          enabled: true,
          revision: 1,
        }
      return []
    })

    await expectSingleSubmit(<ScriptEditor />, '/scripts/sc-1', 'put')
    expect(put).toHaveBeenCalledWith(
      '/scripts/sc-1',
      expect.objectContaining({ name: '巡检脚本' })
    )
  })

  it('分组编辑器：编辑既有分组重复点击只发一次 PUT', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/groups/gr-1')
        return {
          id: 'gr-1',
          name: '生产分组',
          description: '',
          type: 'static',
          label_key: '',
          label_value: '',
          members: [],
          created_at: '2026-01-01T00:00:00Z',
        }
      return []
    })

    await expectSingleSubmit(<GroupEditor />, '/groups/gr-1', 'put')
    expect(put).toHaveBeenCalledWith(
      '/groups/gr-1',
      expect.objectContaining({ name: '生产分组' })
    )
  })

  it('应用编辑器：编辑既有应用重复点击只发一次 PUT', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/applications/app-1')
        return {
          id: 'app-1',
          name: '测试应用',
          description: '',
          artifact_id: 'art-1',
          version: '1.0.0',
          binary_path: '/usr/local/bin/app',
          arguments: [],
          environment: {},
          config: '',
          config_path: '',
          unit_name: 'app.service',
          health_check: {
            type: 'systemd',
            target: '',
            timeout: 5,
            attempts: 3,
            interval: 2,
          },
        }
      if (path === '/applications/app-1/nodes') return []
      return []
    })

    await expectSingleSubmit(<ApplicationEditor />, '/applications/app-1', 'put')
    expect(put).toHaveBeenCalledWith(
      '/applications/app-1',
      expect.objectContaining({ name: '测试应用' })
    )
  })
})
