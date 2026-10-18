import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { ApplicationEditor } from './index'

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

/** 与 POST /api/applications 的 API 创建路径一致的最小定义（batch07 现场） */
const API_CREATED_APPLICATION = {
  id: 'app-api',
  name: 'qa-api-created-app',
  version: '1.0.0',
  artifact_id: 'art-1',
  binary_path: '/usr/local/bin/b07',
  unit_name: 'b07-app.service',
}

function renderEditor() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <SidebarInset>
        <QueryClientProvider client={client}>
          <ApplicationEditor />
        </QueryClientProvider>
      </SidebarInset>
    </SidebarProvider>
  )
}

describe('应用编辑器保存的提交条件（OBS-20261014-APP-EDITOR-SAVE-NO-REQUEST 复核）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    post.mockReset()
    put.mockReset()
    put.mockImplementation(() => new Promise(() => {}))
    post.mockResolvedValue({})
    get.mockImplementation(async (path: string) => {
      if (path === '/applications/app-api') return API_CREATED_APPLICATION
      if (path === '/applications/app-api/nodes') return []
      return []
    })
    window.history.replaceState({}, '', '/applications/app-api')
  })

  it('未做任何修改直接点保存也会发 PUT（没有脏数据判定）', async () => {
    const page = await renderEditor()
    const save = page.getByRole('button', { name: i18n.t('common.save') })
    await expect.element(save).toBeEnabled()

    await userEvent.click(save)

    await expect.poll(() => put.mock.calls.length).toBe(1)
    expect(put).toHaveBeenCalledWith(
      '/applications/app-api',
      expect.objectContaining({
        name: 'qa-api-created-app',
        version: '1.0.0',
        artifact_id: 'art-1',
        binary_path: '/usr/local/bin/b07',
        unit_name: 'b07-app.service',
      })
    )
    // 分配节点未变化：不额外发 assign
    expect(post).not.toHaveBeenCalled()
  })

  it('唯一提前返回条件是名称为空：不发请求并显示内联错误', async () => {
    const page = await renderEditor()
    const name = page.getByRole('textbox', { name: i18n.t('common.name') })
    await userEvent.clear(name)

    await userEvent.click(
      page.getByRole('button', { name: i18n.t('common.save') })
    )

    await expect.element(page.getByRole('alert')).toHaveTextContent(
      i18n.t('common.name')
    )
    expect(put).not.toHaveBeenCalled()
    expect(post).not.toHaveBeenCalled()
  })
})
