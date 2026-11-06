import { useEffect, useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { LanguageSwitch } from '@/components/language-switch'
import { ProfileDropdown } from '../profile-dropdown'
import { ThemeSwitch } from '../theme-switch'
import { Header } from './header'

export function NodeSteerHeader({
  title,
  description,
  action,
  backTo,
  backLabel,
}: {
  title: string
  description?: string
  action?: React.ReactNode
  /** 详情/编辑页统一返回入口：标题左侧图标按钮，不单独占一行 */
  backTo?: string
  /** 返回按钮的可访问名称与 tooltip，默认“返回” */
  backLabel?: string
}) {
  const { t } = useTranslation()
  const backText = backLabel || t('common.back')
  const [hubOnline, setHubOnline] = useState<boolean | null>(null)

  useEffect(() => {
    const check = () =>
      fetch('/healthz', { cache: 'no-store' })
        .then((response) => setHubOnline(response.ok))
        .catch(() => setHubOnline(false))
    check()
    const timer = window.setInterval(check, 15000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <Header fixed>
      <div className='flex min-w-0 flex-1 flex-wrap items-center justify-between gap-3'>
        <div className='flex min-w-0 flex-1 items-center gap-2'>
          {backTo ? (
            <Button
              asChild
              variant='ghost'
              size='icon'
              className='size-8 shrink-0'
            >
              <Link to={backTo} aria-label={backText} title={backText}>
                <ArrowLeft className='size-4' />
              </Link>
            </Button>
          ) : null}
          <div className='min-w-0 flex-1 space-y-0.5'>
            <h1
              className='truncate text-2xl font-semibold tracking-tight'
              title={title}
            >
              {title}
            </h1>
            {description && (
              <p className='truncate text-sm text-muted-foreground'>
                {description}
              </p>
            )}
          </div>
        </div>
        <div className='ms-auto flex shrink-0 flex-wrap items-center justify-end gap-1.5'>
          {action}
          {action && (
            <Separator
              orientation='vertical'
              className='mx-1 hidden h-6 sm:block'
            />
          )}
          <span className='hidden items-center gap-1.5 text-xs text-muted-foreground sm:inline-flex'>
            <span
              className={`size-1.5 rounded-full ${hubOnline === false ? 'bg-danger' : 'bg-success'}`}
              aria-hidden='true'
            />
            {hubOnline === null
              ? t('header.checking')
              : hubOnline
                ? t('header.connected')
                : t('header.unavailable')}
          </span>
          <LanguageSwitch />
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </div>
    </Header>
  )
}
