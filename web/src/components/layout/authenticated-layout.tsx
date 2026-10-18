import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation, useNavigate } from '@tanstack/react-router'
import { sessionToUser, useAuthStore } from '@/stores/auth-store'
import { api, ApiError, type SessionInfo } from '@/lib/api'
import { getCookie } from '@/lib/cookies'
import { currentPath } from '@/lib/navigation'
import { cn } from '@/lib/utils'
import { LayoutProvider } from '@/context/layout-provider'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { SkipToMain } from '@/components/skip-to-main'
import { ErrorState } from '@/features/shared/ui'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const defaultOpen = getCookie('sidebar_state') !== 'false'
  const navigate = useNavigate()
  const location = useLocation()
  const accessToken = useAuthStore((state) => state.auth.accessToken)
  const setUser = useAuthStore((state) => state.auth.setUser)
  const reset = useAuthStore((state) => state.auth.reset)
  const [checking, setChecking] = useState(true)
  const [checkError, setCheckError] = useState<Error | null>(null)
  const [retry, setRetry] = useState(0)
  const redirecting = useRef(false)

  useEffect(() => {
    let mounted = true
    const redirectToSignIn = () => {
      if (redirecting.current) return
      redirecting.current = true
      void navigate({
        to: '/sign-in',
        search: { redirect: currentPath() },
        replace: true,
      })
    }

    if (!accessToken) {
      redirectToSignIn()
      return () => {
        mounted = false
      }
    }

    api
      .get<SessionInfo>('/me')
      .then((session) => {
        if (
          mounted &&
          useAuthStore.getState().auth.accessToken === accessToken
        ) {
          setCheckError(null)
          setUser(sessionToUser(session))
        }
      })
      .catch((error: Error) => {
        if (
          !mounted ||
          useAuthStore.getState().auth.accessToken !== accessToken
        )
          return
        if (error instanceof ApiError && error.status === 401) {
          reset()
          redirectToSignIn()
        } else {
          setCheckError(error)
        }
      })
      .finally(() => {
        if (mounted) setChecking(false)
      })

    return () => {
      mounted = false
    }
  }, [
    accessToken,
    location.pathname,
    location.search,
    location.hash,
    navigate,
    reset,
    setUser,
    retry,
  ])

  if (checking) {
    return (
      <div className='flex min-h-svh items-center justify-center p-6'>
        <Skeleton className='h-8 w-48' />
      </div>
    )
  }

  if (checkError) {
    return (
      <div className='p-6'>
        <ErrorState
          error={checkError}
          onRetry={() => {
            setCheckError(null)
            setChecking(true)
            setRetry((value) => value + 1)
          }}
        />
      </div>
    )
  }

  return (
    <LayoutProvider>
      <SidebarProvider defaultOpen={defaultOpen}>
        <SkipToMain />
        <AppSidebar />
        <SidebarInset
          // 跳转链接 #content 的落点：可聚焦并随之后续 Tab 顺序进入正文
          id='content'
          tabIndex={-1}
          className={cn(
            // Set content container, so we can use container queries
            '@container/content',

            // If layout is fixed, set the height
            // to 100svh to prevent overflow
            'has-data-[layout=fixed]:h-svh',

            // If layout is fixed and sidebar is inset,
            // set the height to 100svh - spacing (total margins) to prevent overflow
            'peer-data-[variant=inset]:has-data-[layout=fixed]:h-[calc(100svh-(var(--spacing)*4))]'
          )}
        >
          {children ?? <Outlet />}
        </SidebarInset>
      </SidebarProvider>
    </LayoutProvider>
  )
}
