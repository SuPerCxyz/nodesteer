import { useEffect } from 'react'
import i18n from '@/i18n'
import { beforeAll, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { SidebarProvider } from '@/components/ui/sidebar'
import { AdministratorPage } from './administrator-page'

vi.mock('@tanstack/react-router', async (original) => {
  const actual = await original<typeof import('@tanstack/react-router')>()
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useLocation: () => ({ href: '/monitoring' }),
  }
})
beforeAll(async () => {
  await i18n.changeLanguage('en')
})
it('does not mount protected queries for viewer/operator and unmounts on role loss', async () => {
  let mounts = 0
  let cleanups = 0
  function ProtectedQueries() {
    useEffect(() => {
      mounts++
      return () => {
        cleanups++
      }
    }, [])
    return <p>Protected data</p>
  }
  const setRole = (role: string) =>
    useAuthStore.setState({
      auth: {
        ...useAuthStore.getState().auth,
        user: {
          accountNo: 'test',
          email: 'test',
          role: [role],
          exp: 0,
          username: 'test',
        },
      },
    })
  setRole('viewer')
  const page = await render(
    <SidebarProvider>
      <AdministratorPage titleKey='nav.monitoring'>
        <ProtectedQueries />
      </AdministratorPage>
    </SidebarProvider>
  )
  await expect.element(page.getByRole('alert')).toBeVisible()
  expect(mounts).toBe(0)
  setRole('operator')
  await expect.element(page.getByRole('alert')).toBeVisible()
  expect(mounts).toBe(0)
  setRole('administrator')
  await expect.element(page.getByText('Protected data')).toBeVisible()
  expect(mounts).toBe(1)
  setRole('viewer')
  await expect.element(page.getByRole('alert')).toBeVisible()
  expect(cleanups).toBe(1)
})
