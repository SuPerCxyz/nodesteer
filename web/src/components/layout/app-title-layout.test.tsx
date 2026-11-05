import type { ReactNode } from 'react'
import i18n from '@/i18n'
import '@/styles/index.css'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage, userEvent } from 'vitest/browser'
import { useAuthStore } from '@/stores/auth-store'
import { LayoutProvider } from '@/context/layout-provider'
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import { AppSidebar } from './app-sidebar'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: (options?: {
      select?: (location: {
        href: string
        pathname: string
        search: Record<string, unknown>
        hash: string
      }) => unknown
    }) => {
      const location = {
        href: '/',
        pathname: '/',
        search: {},
        hash: '',
      }
      return options?.select ? options.select(location) : location
    },
    Link: ({
      children,
      ...props
    }: { children?: ReactNode } & Record<string, unknown>) => (
      <a href='#' {...props}>
        {children}
      </a>
    ),
  }
})

async function renderSidebar() {
  const page = await render(
    <LayoutProvider>
      <SidebarProvider defaultOpen>
        <AppSidebar />
        <SidebarInset>
          <SidebarTrigger aria-label='Open navigation' />
          <p>Main content</p>
        </SidebarInset>
      </SidebarProvider>
    </LayoutProvider>
  )
  await document.fonts.ready
  return page
}
beforeAll(async () => {
  document.documentElement.classList.add('font-maple')
  await i18n.changeLanguage('zh')
})
beforeEach(() => {
  document.cookie = 'layout_variant=inset; path=/'
  document.cookie = 'layout_collapsible=icon; path=/'
  document.cookie = 'sidebar_state=true; path=/'
  useAuthStore.setState({
    auth: {
      ...useAuthStore.getState().auth,
      user: {
        accountNo: 'test',
        username: 'alice',
        email: 'alice',
        role: ['administrator'],
        exp: 0,
      },
    },
  })
})
afterEach(async () => {
  await browserPage.viewport(1280, 720)
  await i18n.changeLanguage('zh')
})

it('uses a 240px sidebar and a full-width subtitle below the 32px toggle without clipping either language', async () => {
  await browserPage.viewport(1366, 768)
  const page = await renderSidebar()
  for (const language of ['zh', 'en']) {
    await i18n.changeLanguage(language)
    for (const width of [1366, 1920]) {
      await browserPage.viewport(width, 768)
      const nav = page.getByRole('navigation', { name: i18n.t('nav.primary') })
      await expect.element(nav).toBeVisible()
      const subtitle = nav
        .element()
        .querySelector('[data-slot="brand-subtitle"]') as HTMLElement
      await expect
        .poll(() => subtitle.textContent)
        .toBe(i18n.t('brand.subtitle'))
      const toggle = nav
        .getByRole('button', {
          name: i18n.t('common.toggleSidebar'),
          exact: true,
        })
        .first()
        .element()
      const subRect = subtitle.getBoundingClientRect()
      const buttonRect = toggle.getBoundingClientRect()
      expect(nav.element().getBoundingClientRect().width).toBe(240)
      expect(
        Math.abs(
          subRect.width - subtitle.parentElement!.getBoundingClientRect().width
        )
      ).toBeLessThan(1.5)
      expect(subRect.top).toBeGreaterThanOrEqual(buttonRect.bottom - 1)
      expect(buttonRect.width).toBe(32)
      expect(buttonRect.height).toBe(32)
      expect(subtitle.scrollWidth).toBeLessThanOrEqual(subtitle.clientWidth + 1)
      expect(subtitle.scrollHeight).toBeLessThanOrEqual(
        subtitle.clientHeight + 1
      )
      expect(getComputedStyle(subtitle).fontSize).toBe('12px')
      const content = nav
        .element()
        .querySelector('[data-sidebar="content"]') as HTMLElement
      expect(content.scrollHeight).toBeLessThanOrEqual(content.clientHeight + 1)
      expect(
        nav.element().querySelectorAll('[data-sidebar="group-label"]')
      ).toHaveLength(6)
      expect(
        nav.element().querySelectorAll('a[data-sidebar="menu-button"]')
      ).toHaveLength(17)
      const footer = nav
        .element()
        .querySelector('[data-sidebar="footer"]') as HTMLElement
      expect(footer.getBoundingClientRect().bottom).toBeLessThanOrEqual(
        nav.element().getBoundingClientRect().bottom + 1
      )
    }
  }
  expect(i18n.getResource('zh', 'translation', 'login.subtitle')).toBe(
    'Linux 节点自动化控制台'
  )
})

it('keeps icon width, keyboard toggling and the persisted collapsed preference', async () => {
  await browserPage.viewport(1366, 768)
  const page = await renderSidebar()
  const nav = page.getByRole('navigation', { name: i18n.t('nav.primary') })
  const toggle = nav.getByRole('button', {
    name: i18n.t('common.toggleSidebar'),
    exact: true,
  })
  await userEvent.click(toggle)
  await expect
    .poll(() =>
      document
        .querySelector('[data-slot="sidebar"]')
        ?.getAttribute('data-state')
    )
    .toBe('collapsed')
  expect(
    document
      .querySelector<HTMLElement>('[data-slot="sidebar-wrapper"]')!
      .style.getPropertyValue('--sidebar-width-icon')
  ).toBe('3rem')
  expect(document.cookie).toContain('sidebar_state=false')
  await expect.element(toggle).toBeVisible()
  await userEvent.keyboard('{Control>}b{/Control}')
  await expect
    .poll(() =>
      document
        .querySelector('[data-slot="sidebar"]')
        ?.getAttribute('data-state')
    )
    .toBe('expanded')
  expect(document.cookie).toContain('sidebar_state=true')
  expect(document.activeElement).toBe(toggle.element())
})

it('keeps the 390px mobile drawer at 288px and closes it from the brand link', async () => {
  await browserPage.viewport(390, 844)
  const page = await renderSidebar()
  await userEvent.click(
    page.getByRole('button', { name: 'Open navigation', exact: true })
  )
  const nav = page.getByRole('navigation', { name: i18n.t('nav.primary') })
  await expect.element(nav).toBeVisible()
  expect(
    nav.element().closest('[data-mobile="true"]')!.getBoundingClientRect().width
  ).toBe(288)
  await expect
    .element(nav.getByText('Linux 节点自动化平台', { exact: true }))
    .toBeVisible()
  await userEvent.click(nav.getByRole('link', { name: /NodeSteer/ }))
  await expect.element(page.getByRole('dialog')).not.toBeInTheDocument()
  expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
    window.innerWidth + 1
  )
})
