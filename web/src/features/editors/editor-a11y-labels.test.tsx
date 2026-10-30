import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import '@/styles/index.css'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarProvider } from '@/components/ui/sidebar'
import { TaskEditor } from '@/features/tasks'
import { ScriptEditor, ApplicationEditor, ScheduleEditor } from './index'

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
    Link: ({ children }: { children?: ReactElement }) => (
      <a href='#'>{children}</a>
    ),
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

describe('编辑器表单控件可访问名（P11 a11y：button-name / label）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue([])
    window.history.replaceState({}, '', '/scripts/new')
  })

  it('脚本编辑器：内容文本域由可见标签关联，参数类型选择器有可访问名', async () => {
    const page = await renderEditor(<ScriptEditor />)

    await expect
      .element(page.getByRole('textbox', { name: i18n.t('scripts.content') }))
      .toBeVisible()
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('scripts.paramType') })
      )
      .toBeVisible()
  })

  it('任务编辑器：参数类型与条件选择器均有可访问名', async () => {
    window.history.replaceState({}, '', '/tasks/new')
    const page = await renderEditor(<TaskEditor />)

    await expect
      .element(page.getByRole('combobox', { name: i18n.t('tasks.paramType') }))
      .toBeVisible()
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('tasks.conditionMetric') })
      )
      .toBeVisible()
    expect(
      page
        .getByRole('combobox', { name: i18n.t('tasks.conditionOperator') })
        .all()
    ).toHaveLength(1)
  })

  it('任务编辑器：不再渲染目标与执行策略字段', async () => {
    window.history.replaceState({}, '', '/tasks/new')
    const page = await renderEditor(<TaskEditor />)

    await expect
      .element(page.getByRole('combobox', { name: i18n.t('targets.type') }))
      .not.toBeInTheDocument()
    await expect
      .element(
        page.getByRole('spinbutton', { name: i18n.t('strategy.timeoutSec') })
      )
      .not.toBeInTheDocument()
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('strategy.offlinePolicy') })
      )
      .not.toBeInTheDocument()
  })

  it('调度计划编辑器：远程条件选择器均有可访问名', async () => {
    window.history.replaceState({}, '', '/schedules/new')
    const page = await renderEditor(<ScheduleEditor />)

    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('tasks.conditionNode') })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('tasks.conditionProperty') })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('combobox', { name: i18n.t('tasks.conditionOperator') })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('button', {
          name: i18n.t('schedules.addRemoteCondition'),
        })
      )
      .toBeVisible()
  })

  it('应用编辑器：发布包与操作选择器有可访问名', async () => {
    window.history.replaceState({}, '', '/applications/app-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/applications/app-1')
        return {
          id: 'app-1',
          name: 'qa-app',
          artifact_id: 'art-1',
          version: 'v1',
          target_node_ids: [],
        }
      if (path === '/artifacts')
        return [
          {
            id: 'art-1',
            name: 'qa-artifact',
            version: 'v1',
            architecture: 'amd64',
            filename: 'qa-app.tar.gz',
            size: 1024,
            sha256: 'cafebabe',
            uploaded_at: '2026-10-01T00:00:00Z',
          },
        ]
      return []
    })
    const page = await renderEditor(<ApplicationEditor />)

    await expect
      .element(page.getByRole('combobox', { name: i18n.t('apps.artifact') }))
      .toBeVisible()
    await expect
      .element(page.getByRole('combobox', { name: i18n.t('apps.operation') }))
      .toBeVisible()
  })
})

describe('编辑器输入框可访问名（P17 a11y：仅 placeholder 命名的输入补 aria-label）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    get.mockResolvedValue([])
    window.history.replaceState({}, '', '/scripts/new')
  })

  it('脚本编辑器：参数与环境变量输入均有可访问名', async () => {
    const page = await renderEditor(<ScriptEditor />)

    for (const name of [
      `${i18n.t('scripts.parameters')}: ${i18n.t('scripts.paramName')}`,
      `${i18n.t('scripts.parameters')}: ${i18n.t('scripts.paramDefault')}`,
      `${i18n.t('scripts.environment')}: ${i18n.t('common.key')}`,
      `${i18n.t('scripts.environment')}: ${i18n.t('common.value')}`,
    ]) {
      await expect.element(page.getByRole('textbox', { name })).toBeVisible()
    }
  })

  it('任务编辑器：参数与条件输入均有可访问名', async () => {
    window.history.replaceState({}, '', '/tasks/new')
    const page = await renderEditor(<TaskEditor />)

    await expect
      .element(page.getByRole('textbox', { name: i18n.t('tasks.paramName') }))
      .toBeVisible()
    await expect
      .element(
        page.getByRole('textbox', { name: i18n.t('tasks.paramDefault') })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('textbox', { name: i18n.t('tasks.localConditionValue') })
      )
      .toBeVisible()
    // 条件输入按指标/属性动态渲染：切换到会展示输入框的取值后再断言
    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('tasks.conditionMetric') })
    )
    await userEvent.click(page.getByRole('option', { name: 'disk_usage' }))
    await expect
      .element(
        page.getByRole('textbox', { name: i18n.t('tasks.conditionPath') })
      )
      .toBeVisible()

    await userEvent.click(
      page.getByRole('combobox', { name: i18n.t('tasks.conditionMetric') })
    )
    await userEvent.click(page.getByRole('option', { name: 'command_result' }))
    await expect
      .element(
        page.getByRole('textbox', { name: i18n.t('tasks.conditionCommand') })
      )
      .toBeVisible()
  })

  it('应用编辑器：发布包上传、参数与环境变量输入均有可访问名', async () => {
    window.history.replaceState({}, '', '/applications/app-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/applications/app-1')
        return {
          id: 'app-1',
          name: 'qa-app',
          artifact_id: 'art-1',
          version: 'v1',
          target_node_ids: [],
        }
      if (path === '/artifacts')
        return [
          {
            id: 'art-1',
            name: 'qa-artifact',
            version: 'v1',
            architecture: 'amd64',
            filename: 'qa-app.tar.gz',
            size: 1024,
            sha256: 'cafebabe',
            uploaded_at: '2026-10-01T00:00:00Z',
          },
        ]
      return []
    })
    const page = await renderEditor(<ApplicationEditor />)

    await expect
      .element(page.getByRole('textbox', { name: i18n.t('apps.arguments') }))
      .toBeVisible()
    await expect
      .element(
        page.getByRole('textbox', {
          name: `${i18n.t('apps.env')}: ${i18n.t('common.key')}`,
        })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('textbox', {
          name: `${i18n.t('apps.env')}: ${i18n.t('common.value')}`,
        })
      )
      .toBeVisible()

    // 上传表单默认隐藏，点击「上传新发布包」后断言其输入名
    await userEvent.click(
      page.getByRole('button', { name: i18n.t('apps.uploadArtifact') })
    )
    await expect
      .element(
        page.getByRole('textbox', {
          name: `${i18n.t('apps.artifact')}: ${i18n.t('common.name')}`,
        })
      )
      .toBeVisible()
    await expect
      .element(
        page.getByRole('textbox', {
          name: `${i18n.t('apps.artifact')}: ${i18n.t('common.version')}`,
        })
      )
      .toBeVisible()
  })
})
