import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { NavUser } from './nav-user'

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

const STATIC_AVATAR = '/images/nodesteer-logo-square.png'

async function renderNav(avatar: string) {
  return render(
    <SidebarProvider>
      <NavUser user={{ name: 'alice', email: 'viewer', avatar }} />
    </SidebarProvider>
  )
}

describe('NavUser 头像渲染', () => {
  it('头像可加载时渲染真实头像 src', async () => {
    const { getByAltText } = await renderNav('/images/shadcn-admin.png')

    await expect
      .element(getByAltText('alice'))
      .toHaveAttribute('src', '/images/shadcn-admin.png')
  })

  it('头像加载失败时回退到静态 logo（维持改动前视觉）', async () => {
    const { getByAltText } = await renderNav('/images/missing-avatar-404.png')

    await expect
      .element(getByAltText('alice'))
      .toHaveAttribute('src', STATIC_AVATAR)
  })

  it('头像为空时显示静态 logo', async () => {
    const { getByAltText } = await renderNav('')

    await expect
      .element(getByAltText('alice'))
      .toHaveAttribute('src', STATIC_AVATAR)
  })
})
