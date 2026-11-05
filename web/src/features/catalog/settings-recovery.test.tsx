import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { ThemeProvider } from '@/context/theme-provider'
import { SidebarProvider } from '@/components/ui/sidebar'
import { Settings } from './index'

const { get, put } = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }))
vi.mock('@/lib/api', async (original) => {
  const actual = await original<typeof import('@/lib/api')>()
  return { ...actual, api: { ...actual.api, get, put } }
})
vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => true,
  useCanRun: () => true,
}))
vi.mock('@tanstack/react-router', async (original) => {
  const actual = await original<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: '/settings' }),
  }
})
beforeAll(async () => {
  await i18n.changeLanguage('zh')
})
it('shows read failure, prevents empty saves, retries and retains draft on failed save', async () => {
  get
    .mockRejectedValueOnce(new Error('read unavailable'))
    .mockResolvedValue({ heartbeat_interval: '30' })
  put.mockRejectedValue(new Error('save unavailable'))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const page = await render(
    <SidebarProvider>
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <Settings />
        </ThemeProvider>
      </QueryClientProvider>
    </SidebarProvider>
  )
  await expect
    .element(page.getByRole('alert'))
    .toHaveTextContent('read unavailable')
  await expect
    .element(
      page.getByRole('button', { name: i18n.t('common.save'), exact: true })
    )
    .toBeDisabled()
  expect(put).not.toHaveBeenCalled()
  await userEvent.click(
    page.getByRole('button', { name: i18n.t('common.retry'), exact: true })
  )
  const input = page.getByRole('textbox')
  await expect.element(input).toHaveValue('30')
  await userEvent.fill(input, '45')
  await userEvent.click(
    page.getByRole('button', { name: i18n.t('common.save'), exact: true })
  )
  await expect.poll(() => put.mock.calls.length).toBe(1)
  await expect.element(input).toHaveValue('45')
  expect(put).toHaveBeenCalledWith('/settings', { heartbeat_interval: '45' })
})
