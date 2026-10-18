import type { ReactNode } from 'react'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { setToken } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { AuthenticatedLayout } from './authenticated-layout'

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
    useLocation: () => ({ pathname: '/nodes', search: '', hash: '' }),
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
