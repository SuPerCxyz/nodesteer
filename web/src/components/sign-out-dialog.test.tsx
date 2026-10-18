import i18n from '@/i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { SignOutDialog } from './sign-out-dialog'

const navigate = vi.fn()
const reset = vi.fn()
const post = vi.hoisted(() => vi.fn())
const toastError = vi.hoisted(() => vi.fn())
const toastSuccess = vi.hoisted(() => vi.fn())
const calls: string[] = []

const MOCK_HREF = 'https://app.test/dashboard?tab=1'

vi.mock('@/stores/auth-store', () => ({
  useAuthStore: () => ({
    auth: { reset },
  }),
}))

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, post } }
})

vi.mock('sonner', () => ({
  toast: { error: toastError, success: toastSuccess },
}))

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => navigate,
    useLocation: () => ({ href: MOCK_HREF }),
  }
})

describe('SignOutDialog', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('zh')
    vi.clearAllMocks()
    calls.length = 0
    post.mockImplementation(async () => {
      calls.push('post')
      return { ok: 'true' }
    })
    reset.mockImplementation(() => {
      calls.push('reset')
    })
    navigate.mockImplementation(() => {
      calls.push('navigate')
    })
  })

  it('先 POST /api/logout 吊销服务端会话，再清本地状态并跳转', async () => {
    const { getByRole } = await render(
      <SignOutDialog open onOpenChange={vi.fn()} />
    )

    await userEvent.click(getByRole('button', { name: i18n.t('nav.logout') }))

    await expect.poll(() => navigate.mock.calls.length).toBe(1)
    expect(post).toHaveBeenCalledWith('/logout')
    expect(reset).toHaveBeenCalledOnce()
    expect(navigate).toHaveBeenCalledWith({
      to: '/sign-in',
      search: { redirect: MOCK_HREF },
      replace: true,
    })
    expect(calls).toEqual(['post', 'reset', 'navigate'])
    expect(toastError).not.toHaveBeenCalled()
  })

  it('吊销失败仍清本地状态并跳转，同时给出可理解反馈', async () => {
    post.mockImplementation(async () => {
      calls.push('post')
      throw new Error('HTTP 500')
    })
    const { getByRole } = await render(
      <SignOutDialog open onOpenChange={vi.fn()} />
    )

    await userEvent.click(getByRole('button', { name: i18n.t('nav.logout') }))

    await expect.poll(() => navigate.mock.calls.length).toBe(1)
    expect(reset).toHaveBeenCalledOnce()
    expect(calls).toEqual(['post', 'reset', 'navigate'])
    expect(toastError).toHaveBeenCalledOnce()
    expect(toastError.mock.calls[0][0]).toBe(
      i18n.t('login.signOutRevokeFailed')
    )
  })

  it('吊销请求在途时按钮禁用，重复点击不会发第二次请求', async () => {
    post.mockImplementation(() => new Promise(() => {}))
    const { getByRole } = await render(
      <SignOutDialog open onOpenChange={vi.fn()} />
    )
    const confirm = getByRole('button', { name: i18n.t('nav.logout') })

    await userEvent.click(confirm)
    await expect.element(confirm).toBeDisabled()
    // 第二次点击：按钮已禁用，浏览器不会派发 click 事件
    ;(confirm.element() as HTMLButtonElement).click()
    expect(post).toHaveBeenCalledOnce()
    expect(reset).not.toHaveBeenCalled()
  })

  it('does not call reset or navigate when Cancel is clicked', async () => {
    const { getByRole } = await render(
      <SignOutDialog open onOpenChange={vi.fn()} />
    )

    await userEvent.click(
      getByRole('button', { name: i18n.t('common.cancel') })
    )

    expect(post).not.toHaveBeenCalled()
    expect(reset).not.toHaveBeenCalled()
    expect(navigate).not.toHaveBeenCalled()
  })
})
