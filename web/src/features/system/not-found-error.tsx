import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'

export function NotFoundError() {
  const { t } = useTranslation()
  return (
    <div className='container flex min-h-svh flex-col items-center justify-center gap-4 text-center'>
      <h1 className='text-3xl font-semibold'>{t('errors.pageNotFound')}</h1>
      <p className='text-muted-foreground'>{t('errors.pageNotFoundDesc')}</p>
      <Button asChild>
        <Link to='/'>{t('errors.backToOverview')}</Link>
      </Button>
    </div>
  )
}
