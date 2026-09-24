import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { ProfileDropdown } from './profile-dropdown'

// vi.mock 工厂被提升到文件顶部，可变鉴权状态必须放在 vi.hoisted 中
const mockAuth = vi.hoisted(() => ({
  user: null as unknown,
  reset: () => {},
}))

type MockState = {
  auth: {
    user: unknown
    accessToken: string
    reset: () => void
  }
}

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: (selector?: (state: MockState) => unknown) => {
    const state: MockState = {
      auth: {
        user: mockAuth.user,
        accessToken: 'token',
        reset: mockAuth.reset,
      },
    }
    return typeof selector === 'function' ? selector(state) : state
  },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: '/' }),
    Link: ({ children }: { children?: ReactNode }) => (
      <a href='#'>{children}</a>
    ),
  }
})

function makeUser(avatar?: string) {
  return {
    accountNo: 'u-1',
    email: 'alice@example.com',
    role: ['viewer'],
    exp: 1_700_000_000,
    username: 'alice',
    userId: 'u-1',
    ...(avatar ? { avatar } : {}),
  }
}

describe('ProfileDropdown 头像渲染', () => {
  beforeEach(() => {
    mockAuth.user = null
  })

  it('用户有头像时渲染 AvatarImage src', async () => {
    mockAuth.user = makeUser('/images/shadcn-admin.png')

    const { getByRole } = await render(<ProfileDropdown />)

    await expect
      .element(getByRole('img', { name: 'alice' }))
      .toHaveAttribute('src', '/images/shadcn-admin.png')
  })

  it('用户无头像时维持原 Logo Fallback，不渲染头像图片', async () => {
    mockAuth.user = makeUser()

    const { getByRole } = await render(<ProfileDropdown />)

    await expect
      .element(getByRole('img', { name: 'alice' }))
      .not.toBeInTheDocument()
    await expect
      .element(getByRole('img', { name: 'NodeSteer' }))
      .toBeInTheDocument()
  })

  it('鉴权用户未加载时维持原 Logo Fallback', async () => {
    const { getByRole } = await render(<ProfileDropdown />)

    await expect
      .element(getByRole('img', { name: 'NodeSteer' }))
      .toBeInTheDocument()
    await expect
      .element(getByRole('img', { name: 'alice' }))
      .not.toBeInTheDocument()
  })
})
