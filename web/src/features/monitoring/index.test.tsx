import type { ReactElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Monitoring } from './index'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: '/monitoring' }),
    Link: ({ children, to }: { children?: ReactElement; to?: string }) => (
      <a href={typeof to === 'string' ? to : '#'}>{children}</a>
    ),
  }
})

vi.mock('./nodes', () => ({
  NodeMonitoring: () => <div>节点监控总览（子组件）</div>,
}))

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SidebarProvider>
        <Monitoring />
      </SidebarProvider>
    </QueryClientProvider>
  )
}

describe('节点监控页', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('只保留节点监控，不渲染规则、触发记录或服务目标', async () => {
    const page = await renderPage()

    await expect
      .element(
        page.getByRole('heading', { name: i18n.t('monitoring.monitoring') })
      )
      .toBeVisible()
    await expect
      .element(page.getByText(i18n.t('monitoring.nodeMonitoringDescription')))
      .toBeVisible()
    await expect.element(page.getByText('节点监控总览（子组件）')).toBeVisible()
    await expect
      .element(page.getByText(i18n.t('rules.listTitle')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByText(i18n.t('monitoring.triggerHistory')))
      .not.toBeInTheDocument()
    await expect
      .element(page.getByText(i18n.t('monitoring.httpServices')))
      .not.toBeInTheDocument()
  })
})
