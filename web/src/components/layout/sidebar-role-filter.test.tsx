import type { ReactNode } from 'react'
import i18n from '@/i18n'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage } from 'vitest/browser'
import { useAuthStore } from '@/stores/auth-store'
import { LayoutProvider } from '@/context/layout-provider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AppSidebar } from './app-sidebar'
import { getSidebarData } from './data/sidebar-data'

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

function navUrls(canAdmin: boolean) {
  return getSidebarData(i18n.t, { canAdmin }).navGroups.flatMap((group) =>
    group.items.map((item) => item.url)
  )
}

function renderSidebar(role: string) {
  useAuthStore.setState({
    auth: {
      ...useAuthStore.getState().auth,
      user: {
        accountNo: 'u1',
        email: 'alice',
        role: [role],
        exp: 0,
        username: 'alice',
      },
    },
  })
  return render(
    <LayoutProvider>
      <SidebarProvider>
        <AppSidebar />
      </SidebarProvider>
    </LayoutProvider>
  )
}

describe('侧边栏导航按角色过滤（OBS-20261014-NAV-ROLE-UNFILTERED）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('导航数据：仅 administrator 含用户入口，operator/viewer 不含', () => {
    expect(navUrls(true)).toContain('/users')
    expect(navUrls(false)).not.toContain('/users')
    // 其他入口不因角色过滤而丢失
    expect(navUrls(false)).toEqual(
      expect.arrayContaining([
        '/audit',
        '/settings',
        '/agents',
        '/groups',
        '/tasks',
      ])
    )
  })

  it('导航分组：监控组仅管理员可见，节点组对所有角色可见', () => {
    const adminGroups = getSidebarData(i18n.t, {
      canAdmin: true,
    }).navGroups
    const automation = adminGroups.find(
      (group) => group.title === i18n.t('nav.sections.automation')
    )
    const monitoring = adminGroups.find(
      (group) => group.title === i18n.t('nav.sections.monitoring')
    )
    const nodes = adminGroups.find(
      (group) => group.title === i18n.t('nav.sections.nodes')
    )
    expect(automation?.items.map((item) => item.url)).toEqual([
      '/tasks',
      '/schedules',
      '/executions',
      '/rules',
      '/transfers',
    ])
    expect(monitoring?.items.map((item) => item.url)).toEqual([
      '/monitoring',
      '/monitoring/services',
      '/monitoring/alerts',
    ])
    expect(nodes?.items.map((item) => item.url)).toEqual(['/agents', '/groups'])

    const viewerGroups = getSidebarData(i18n.t, {
      canAdmin: false,
    }).navGroups
    expect(
      viewerGroups.find(
        (group) => group.title === i18n.t('nav.sections.monitoring')
      )
    ).toBeUndefined()
    expect(
      viewerGroups
        .find((group) => group.title === i18n.t('nav.sections.nodes'))
        ?.items.map((item) => item.url)
    ).toEqual(['/agents', '/groups'])
    expect(navUrls(false)).not.toContain('/monitoring')
    expect(navUrls(false)).not.toContain('/monitoring/services')
    expect(navUrls(false)).not.toContain('/monitoring/alerts')
    expect(navUrls(true)).toContain('/rules')
    expect(navUrls(false)).not.toContain('/rules')
    expect(navUrls(false)).toContain('/tasks')
  })

  it('operator 渲染侧边栏时不出现「用户」入口', async () => {
    await browserPage.viewport(1280, 800)
    const page = await renderSidebar('operator')

    await expect
      .element(page.getByRole('link', { name: '用户' }))
      .not.toBeInTheDocument()
    await expect.element(page.getByRole('link', { name: '审计' })).toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '节点监控' }))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByRole('link', { name: '服务监控' }))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByRole('link', { name: '告警' }))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByRole('link', { name: '联动规则' }))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByText('监控', { exact: true }))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByText('自动化控制', { exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '节点', exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '分组', exact: true }))
      .toBeVisible()
  })

  it('viewer 渲染侧边栏时不出现「用户」入口', async () => {
    await browserPage.viewport(1280, 800)
    const page = await renderSidebar('viewer')

    await expect
      .element(page.getByRole('link', { name: '用户' }))
      .not.toBeInTheDocument()
  })

  it('administrator 渲染侧边栏时保留「用户」入口', async () => {
    await browserPage.viewport(1280, 800)
    const page = await renderSidebar('administrator')

    await expect.element(page.getByRole('link', { name: '用户' })).toBeVisible()
    await expect
      .element(page.getByText('自动化控制', { exact: true }))
      .toBeVisible()
    await expect.element(page.getByText('监控', { exact: true })).toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '节点监控' }))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '服务监控' }))
      .toBeVisible()
    await expect.element(page.getByRole('link', { name: '告警' })).toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '联动规则' }))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '节点', exact: true }))
      .toBeVisible()
    await expect
      .element(page.getByRole('link', { name: '分组', exact: true }))
      .toBeVisible()
  })
})
