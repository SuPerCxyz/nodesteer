import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SignIn } from './index'

// vi.mock 工厂被 hoist 到文件顶部，被引用的 mock 必须在 hoisted 作用域内创建
const get = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get } }
})

function renderSignIn() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SignIn />
    </QueryClientProvider>
  )
}

describe('SignIn OIDC 状态渲染', () => {
  beforeAll(() => void i18n.changeLanguage('zh'))
  beforeEach(() => {
    get.mockReset()
    window.history.replaceState({}, '', '/sign-in')
  })

  it('OIDC 启用且无兜底：仅 SSO 按钮，无本地表单', async () => {
    get.mockResolvedValue({ enabled: true })
    const { getByRole, getByLabelText } = await renderSignIn()

    await expect
      .element(getByRole('button', { name: '使用 SSO 登录' }))
      .toBeVisible()
    await expect.element(getByLabelText('用户名')).not.toBeInTheDocument()
    await expect.element(getByLabelText('密码')).not.toBeInTheDocument()
  })

  it('OIDC 启用且 local_fallback：SSO 按钮与本地表单并存，含分隔文案', async () => {
    get.mockResolvedValue({ enabled: true, local_fallback: true })
    const { getByRole, getByLabelText, getByText } = await renderSignIn()

    await expect
      .element(getByRole('button', { name: '使用 SSO 登录' }))
      .toBeVisible()
    await expect.element(getByLabelText('用户名')).toBeVisible()
    await expect.element(getByLabelText('密码')).toBeVisible()
    await expect.element(getByText('或使用本地账号登录')).toBeVisible()
  })

  it('OIDC 未启用：仅本地表单，无 SSO 按钮', async () => {
    get.mockResolvedValue({ enabled: false, local_fallback: false })
    const { getByRole, getByLabelText } = await renderSignIn()

    await expect.element(getByLabelText('用户名')).toBeVisible()
    await expect.element(getByLabelText('密码')).toBeVisible()
    await expect
      .element(getByRole('button', { name: '使用 SSO 登录' }))
      .not.toBeInTheDocument()
  })

  it('local_fallback 为旧响应缺省字段时按 false 处理', async () => {
    get.mockResolvedValue({ enabled: true })
    const { getByRole, getByLabelText } = await renderSignIn()

    await expect
      .element(getByRole('button', { name: '使用 SSO 登录' }))
      .toBeVisible()
    await expect.element(getByLabelText('用户名')).not.toBeInTheDocument()
  })
})
