import type { ReactNode } from 'react'
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
import { page as browserPage, userEvent } from 'vitest/browser'
import { useAuthStore } from '@/stores/auth-store'
import { setToken } from '@/lib/api'
import { AuthenticatedLayout } from './authenticated-layout'

const { get, navigate } = vi.hoisted(() => ({
  get: vi.fn(),
  navigate: vi.fn(),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    useLocation: (options?: {
      select?: (location: {
        pathname: string
        search: string
        hash: string
        href: string
      }) => unknown
    }) => {
      const location = {
        pathname: '/nodes',
        search: '',
        hash: '',
        href: '/nodes',
      }
      return options?.select ? options.select(location) : location
    },
    Link: ({ children, to }: { children?: ReactNode; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

describe('跳转主内容链接（SkipToMain）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    setToken('layout-test-session')
    // 布局用 store 里的 accessToken 判定会话，直接写 store 而非只写 localStorage
    useAuthStore.getState().auth.setAccessToken('layout-test-session')
    get.mockReset()
    get.mockResolvedValue({
      token: 'layout-test-session',
      user_id: 'admin',
      username: 'admin',
      role: 'administrator',
      expires: '2099-01-01T00:00:00Z',
    })
  })

  it('锚点目标存在且可聚焦，激活后焦点落在主内容容器', async () => {
    const page = await render(
      <AuthenticatedLayout>
        <p>正文内容</p>
      </AuthenticatedLayout>
    )

    const skipLink = page.getByRole('link', { name: 'Skip to Main' })
    await expect.element(skipLink).toBeVisible()

    const target = document.getElementById('content')
    expect(target).not.toBeNull()
    expect(target?.getAttribute('tabindex')).toBe('-1')
    expect(target?.contains(document.querySelector('p'))).toBe(true)

    await userEvent.click(skipLink)
    expect(document.activeElement).toBe(target)
  })
})

describe('侧栏导航地标（P11 a11y：内容需位于 landmark 内）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  // 侧栏在 md 以下断点隐藏（hidden md:block），用桌面视口验证地标
  beforeEach(async () => {
    await browserPage.viewport(1280, 800)
  })

  afterEach(async () => {
    await browserPage.viewport(1280, 800)
  })

  beforeEach(() => {
    setToken('layout-test-session')
    useAuthStore.getState().auth.setAccessToken('layout-test-session')
    get.mockReset()
    get.mockResolvedValue({
      token: 'layout-test-session',
      user_id: 'admin',
      username: 'admin',
      role: 'administrator',
      expires: '2099-01-01T00:00:00Z',
    })
  })

  it('侧栏渲染为具名导航地标，侧栏内链接与用户菜单均在地标内', async () => {
    const page = await render(
      <AuthenticatedLayout>
        <p>正文内容</p>
      </AuthenticatedLayout>
    )

    const nav = page.getByRole('navigation', { name: i18n.t('nav.primary') })
    await expect.element(nav).toBeVisible()
    expect(
      nav
        .element()
        .contains(page.getByRole('link', { name: '仪表盘' }).element())
    ).toBe(true)
    expect(
      nav
        .element()
        .contains(page.getByRole('button', { name: /admin/i }).element())
    ).toBe(true)
  })
})

describe('本地测试免登录模式', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    setToken(null)
    useAuthStore.getState().auth.reset()
    get.mockReset()
    get.mockImplementation(async (path: string) => {
      if (path === '/auth-mode') return { dev_no_auth: true }
      if (path === '/me')
        return {
          token: 'dev-no-auth',
          user_id: 'dev-no-auth',
          username: 'dev',
          role: 'administrator',
          expires: '2099-01-01T00:00:00Z',
        }
      return {}
    })
  })

  it('无 token 时探测 auth-mode，自动进入并显示免登录横幅', async () => {
    const page = await render(
      <AuthenticatedLayout>
        <p>正文内容</p>
      </AuthenticatedLayout>
    )

    await expect.element(page.getByText(i18n.t('auth.devNoAuth'))).toBeVisible()
    await expect.element(page.getByText('正文内容')).toBeVisible()
    expect(useAuthStore.getState().auth.accessToken).toBe('dev-no-auth')
  })
})

describe('未登录回跳', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    setToken(null)
    useAuthStore.getState().auth.reset()
    navigate.mockReset()
    get.mockReset()
  })

  afterEach(() => {
    window.history.replaceState({}, '', '/')
  })

  it('从受保护页面跳转时只带单层回跳目标', async () => {
    window.history.replaceState({}, '', '/nodes')
    get.mockResolvedValue({ dev_no_auth: false })

    await render(
      <AuthenticatedLayout>
        <p>正文内容</p>
      </AuthenticatedLayout>
    )

    await vi.waitFor(() => expect(navigate).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(navigate).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith({
      to: '/sign-in',
      search: { redirect: '/nodes' },
      replace: true,
    })
  })

  it('已在登录页时不再把 sign-in 自身作为 redirect', async () => {
    window.history.replaceState({}, '', '/sign-in?redirect=%2Fnodes')
    get.mockResolvedValue({ dev_no_auth: false })

    await render(
      <AuthenticatedLayout>
        <p>正文内容</p>
      </AuthenticatedLayout>
    )

    await vi.waitFor(() => expect(get).toHaveBeenCalledWith('/auth-mode'))
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(navigate).not.toHaveBeenCalled()
  })
})

// 会话写在同源 localStorage 上，跨测试文件共享；用例结束必须清理，避免污染其它测试
afterEach(() => {
  setToken(null)
  useAuthStore.getState().auth.reset()
})
