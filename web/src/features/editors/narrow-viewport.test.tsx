import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { page } from 'vitest/browser'
import { render } from 'vitest-browser-react'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { ScheduleEditor, ScriptEditor } from './index'

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
    Link: ({ children }: { children?: ReactElement }) => <a href='#'>{children}</a>,
  }
})

const LONG_TASK_NAME =
  'qa-长任务名称-用于长值布局验证-中文超长标题-0123456789-abcdefghijklmnopqrstuvwxyz-结束'
const LONG_LINE = 'echo ' + 'x'.repeat(320)
const LONG_PARAM = 'param_' + 'y'.repeat(120)
const LONG_DEFAULT = 'z'.repeat(200)
const LONG_ENV_KEY = 'LONG_ENV_KEY_' + 'k'.repeat(80)

function renderEditor(element: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <SidebarInset>
        <QueryClientProvider client={client}>{element}</QueryClientProvider>
      </SidebarInset>
    </SidebarProvider>
  )
}

/** 页面级横向溢出：文档滚动宽度不得超过可视宽度 */
function expectNoPageOverflow() {
  const root = document.documentElement
  expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth)
}

describe('窄屏（480px）长值不撑破页面', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(async () => {
    await page.viewport(480, 800)
    get.mockReset()
    get.mockResolvedValue([])
  })

  afterEach(async () => {
    await page.viewport(1280, 800)
  })

  it('调度编辑器：长任务名的选择框在容器内截断', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/tasks')
        return [{ id: 'task-1', name: LONG_TASK_NAME }]
      if (path === '/schedules/sch-1')
        return {
          id: 'sch-1',
          task_id: 'task-1',
          revision: 1,
          type: 'on_start',
          expression: '',
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

    window.history.replaceState({}, '', '/schedules/sch-1')
    const rendered = await renderEditor(<ScheduleEditor />)
    await expect
      .element(rendered.getByRole('combobox', { name: '任务' }))
      .toBeVisible()

    const trigger = rendered
      .getByRole('combobox', { name: '任务' })
      .element() as HTMLElement
    const main = document.querySelector('main') as HTMLElement
    expect(trigger.getBoundingClientRect().width).toBeLessThanOrEqual(
      main.getBoundingClientRect().width
    )
    expectNoPageOverflow()
  })

  it('脚本编辑器：超长内容/参数/环境变量在容器内换行或截断', async () => {
    get.mockImplementation(async (path: string) => {
      if (path === '/scripts/sc-1')
        return {
          id: 'sc-1',
          name: '长值脚本',
          description: '',
          interpreter: 'shell',
          content: `#!/bin/sh\n${LONG_LINE}\n`,
          parameters: [
            { name: LONG_PARAM, type: 'string', required: false, default: LONG_DEFAULT },
          ],
          environment: { [LONG_ENV_KEY]: LONG_LINE },
          working_dir: '/tmp',
          timeout: 300,
          enabled: true,
          revision: 1,
        }
      if (path === '/scripts/sc-1/revisions')
        return [{ revision: 1, content: LONG_LINE, sha256: 'a'.repeat(64), changed_at: '2026-01-01T00:00:00Z' }]
      return []
    })

    window.history.replaceState({}, '', '/scripts/sc-1')
    const rendered = await renderEditor(<ScriptEditor />)
    await expect
      .element(rendered.getByRole('button', { name: '保存' }))
      .toBeVisible()

    expectNoPageOverflow()
    // 长参数名/默认值/环境变量行都必须收在容器内，而不是撑宽页面
    const mainWidth = (
      document.querySelector('main') as HTMLElement
    ).getBoundingClientRect().width
    for (const needle of [LONG_PARAM, LONG_DEFAULT, LONG_ENV_KEY]) {
      const element = Array.from(
        document.querySelectorAll('main span')
      ).find((span) => span.textContent?.includes(needle))
      expect(element).toBeTruthy()
      expect(
        element!.getBoundingClientRect().width
      ).toBeLessThanOrEqual(mainWidth)
    }
  })
})
