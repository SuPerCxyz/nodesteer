import { useState } from 'react'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
  useParams,
} from '@tanstack/react-router'
import { Route } from '@/routes/_authenticated/applications/$applicationId'
import { expect, it } from 'vitest'
import { render } from 'vitest-browser-react'

it('Application ID变更重新挂载Editor，旧tracking不进入新应用', async () => {
  let instances = 0
  function EditorState() {
    const [instance] = useState(() => ++instances)
    const { applicationId } = useParams({ strict: false })
    return (
      <div>
        {applicationId}:{instance}
      </div>
    )
  }
  const root = createRootRoute()
  const application = createRoute({
    getParentRoute: () => root,
    path: '/applications/$applicationId',
    component: EditorState,
    remountDeps: Route.options.remountDeps,
  })
  const router = createRouter({
    routeTree: root.addChildren([application]),
    history: createMemoryHistory({ initialEntries: ['/applications/app-a'] }),
  })
  const page = await render(<RouterProvider router={router} />)
  await expect.element(page.getByText('app-a:1', { exact: true })).toBeVisible()
  await router.navigate({
    to: '/applications/$applicationId',
    params: { applicationId: 'app-b' },
  })
  await expect.element(page.getByText('app-b:2', { exact: true })).toBeVisible()
})
