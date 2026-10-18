import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { useAuthStore } from '@/stores/auth-store'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import type { Artifact, User } from '@/lib/api'
import { ArtifactDetail, Artifacts, UserDetail, Users } from './index'

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
    Link: ({ children, to }: { children?: React.ReactNode; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

const USERS: User[] = [
  {
    id: 'u-1',
    username: 'alice',
    role: 'operator',
    created_at: '2026-10-01T00:00:00Z',
  },
]

const ARTIFACT: Artifact = {
  id: 'art-1',
  name: 'qa-artifact',
  version: '1.0.0',
  architecture: 'amd64',
  filename: 'qa-artifact.tar.gz',
  size: 4096,
  sha256: 'cafebabe',
  uploaded_at: '2026-10-01T00:00:00Z',
}

function renderPage(element: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <SidebarProvider>
      <QueryClientProvider client={client}>{element}</QueryClientProvider>
    </SidebarProvider>
  )
}

describe('用户详情路由（OBS-20261017-CROSSPAGE-LINK-GAPS ③）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    useAuthStore.setState({
      auth: {
        ...useAuthStore.getState().auth,
        user: {
          accountNo: 'admin-1',
          email: 'admin',
          role: ['administrator'],
          exp: 0,
          username: 'admin',
        },
      },
    })
    get.mockImplementation(async (path: string) => {
      if (path === '/users') return USERS
      return []
    })
  })

  it('展示用户资料与角色，缺失用户时给出明确错误', async () => {
    window.history.replaceState({}, '', '/users/u-1')
    const page = await renderPage(<UserDetail />)

    await expect.element(page.getByRole('heading', { name: 'alice' })).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('misc.operator')).first())
      .toBeVisible()

    window.history.replaceState({}, '', '/users/missing-user')
    const missing = await renderPage(<UserDetail />)
    await expect
      .element(
        missing.getByText(
          i18n.t('errors.notFoundResource', {
            resource: i18n.t('errors.types.user'),
          })
        )
      )
      .toBeVisible()
  })
})

describe('发布包详情路由（OBS-20261017-CROSSPAGE-LINK-GAPS ④）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/artifacts/art-1')
    get.mockImplementation(async (path: string) => {
      if (path === '/artifacts/art-1') return ARTIFACT
      if (path === '/artifacts') return [ARTIFACT]
      if (path === '/applications')
        return [
          {
            id: 'app-1',
            name: 'qa-app',
            artifact_id: 'art-1',
          },
        ]
      return []
    })
  })

  it('展示发布包元数据、使用情况回链与下载入口', async () => {
    const page = await renderPage(<ArtifactDetail />)

    await expect.element(page.getByText('qa-artifact.tar.gz')).toBeVisible()
    await expect.element(page.getByText('cafebabe')).toBeVisible()
    await expect
      .element(page.getByRole('link', { name: 'qa-app' }))
      .toHaveAttribute('href', '/applications/app-1')
    await expect
      .element(page.getByRole('button', { name: i18n.t('artifacts.download') }))
      .toBeVisible()
    await expect
      .element(page.getByRole('button', { name: i18n.t('common.delete') }))
      .toBeVisible()
  })

  it('发布包列表名称可跳转到详情页', async () => {
    window.history.replaceState({}, '', '/artifacts')
    const page = await renderPage(<Artifacts />)

    await expect
      .element(page.getByRole('link', { name: 'qa-artifact' }))
      .toHaveAttribute('href', '/artifacts/art-1')
  })
})

describe('用户列表跳转详情（OBS-20261017-CROSSPAGE-LINK-GAPS ③）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('用户名链接到用户详情页', async () => {
    useAuthStore.setState({
      auth: {
        ...useAuthStore.getState().auth,
        user: {
          accountNo: 'admin-1',
          email: 'admin',
          role: ['administrator'],
          exp: 0,
          username: 'admin',
        },
      },
    })
    get.mockReset()
    window.history.replaceState({}, '', '/users')
    get.mockImplementation(async (path: string) => {
      if (path === '/users') return USERS
      return []
    })
    const page = await renderPage(<Users />)

    await expect
      .element(page.getByRole('link', { name: 'alice' }))
      .toHaveAttribute('href', '/users/u-1')
  })
})
