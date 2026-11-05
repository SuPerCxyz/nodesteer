import { Link } from '@tanstack/react-router'
import { Menu, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Logo } from '@/assets/logo'
import { cn } from '@/lib/utils'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { Button } from '../ui/button'

export function AppTitle() {
  const { setOpenMobile } = useSidebar()
  const { t } = useTranslation()
  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <SidebarMenuButton
          size='lg'
          className='relative block h-auto min-h-12 gap-0 py-0 group-data-[collapsible=icon]:min-h-8 hover:bg-transparent active:bg-transparent'
          asChild
        >
          <div>
            <Link
              to='/'
              onClick={() => setOpenMobile(false)}
              className='grid w-full min-w-0 text-start text-sm leading-tight group-data-[collapsible=icon]:hidden'
            >
              <span className='flex h-8 min-w-0 items-center gap-2 pe-8 font-bold'>
                <Logo className='size-5 text-primary' />
                {t('brand.name')}
              </span>
              <span
                data-slot='brand-subtitle'
                className='min-w-0 text-xs leading-4 [overflow-wrap:anywhere] whitespace-normal'
              >
                {t('brand.subtitle')}
              </span>
            </Link>
            <ToggleSidebar className='absolute inset-e-0 top-0' />
          </div>
        </SidebarMenuButton>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

function ToggleSidebar({
  className,
  onClick,
  ...props
}: React.ComponentProps<typeof Button>) {
  const { t } = useTranslation()
  const { toggleSidebar } = useSidebar()

  return (
    <Button
      data-sidebar='trigger'
      data-slot='sidebar-trigger'
      variant='ghost'
      size='icon'
      className={cn('aspect-square size-8 max-md:scale-125', className)}
      onClick={(event) => {
        onClick?.(event)
        toggleSidebar()
      }}
      {...props}
    >
      <X className='md:hidden' />
      <Menu className='max-md:hidden' />
      <span className='sr-only'>{t('common.toggleSidebar')}</span>
    </Button>
  )
}
