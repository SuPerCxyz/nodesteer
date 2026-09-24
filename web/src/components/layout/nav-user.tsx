import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { ChevronsUpDown, LogOut, Settings } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useDialogState from '@/hooks/use-dialog-state'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { SignOutDialog } from '@/components/sign-out-dialog'

type NavUserProps = {
  user: {
    name: string
    email: string
    avatar: string
  }
}

/** 头像缺失或加载失败时回退的静态 logo（维持改动前的视觉行为） */
const STATIC_AVATAR = '/images/nodesteer-logo-square.png'

export function NavUser({ user }: NavUserProps) {
  const { t } = useTranslation()
  const { isMobile } = useSidebar()
  const [open, setOpen] = useDialogState()
  // 记录加载失败的头像 URL；派生 src 使 avatar prop 变化时自动重试新地址
  const [failedAvatar, setFailedAvatar] = useState<string | null>(null)
  const avatarSrc =
    !user.avatar || failedAvatar === user.avatar ? STATIC_AVATAR : user.avatar
  // Radix AvatarImage 用离屏 Image 探测加载状态，onError 传不到渲染的 img，
  // 故用 onLoadingStatusChange 感知失败：真实头像失败/为空 → 静态 logo；
  // logo 自身失败则 src 不再变化，由 Radix Fallback('C') 兜底（与改动前失败态一致）。
  const handleAvatarStatus = (
    status: 'idle' | 'loading' | 'loaded' | 'error'
  ) => {
    if (status === 'error' && avatarSrc === user.avatar) {
      setFailedAvatar(user.avatar)
    }
  }

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size='lg'
                className='data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground'
              >
                <Avatar className='h-8 w-8 rounded-lg'>
                  <AvatarImage
                    src={avatarSrc}
                    alt={user.name}
                    onLoadingStatusChange={handleAvatarStatus}
                  />
                  <AvatarFallback className='rounded-lg'>C</AvatarFallback>
                </Avatar>
                <div className='grid flex-1 text-start text-sm leading-tight'>
                  <span className='truncate font-semibold'>{user.name}</span>
                  <span className='truncate text-xs'>{user.email}</span>
                </div>
                <ChevronsUpDown className='ms-auto size-4' />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className='w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg'
              side={isMobile ? 'bottom' : 'right'}
              align='end'
              sideOffset={4}
            >
              <DropdownMenuLabel className='p-0 font-normal'>
                <div className='flex items-center gap-2 px-1 py-1.5 text-start text-sm'>
                  <Avatar className='h-8 w-8 rounded-lg'>
                    <AvatarImage
                      src={avatarSrc}
                      alt={user.name}
                      onLoadingStatusChange={handleAvatarStatus}
                    />
                    <AvatarFallback className='rounded-lg'>C</AvatarFallback>
                  </Avatar>
                  <div className='grid flex-1 text-start text-sm leading-tight'>
                    <span className='truncate font-semibold'>{user.name}</span>
                    <span className='truncate text-xs'>{user.email}</span>
                  </div>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuGroup>
                <DropdownMenuItem asChild>
                  <Link to='/settings'>
                    <Settings />
                    {t('nav.settings')}
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant='destructive'
                onClick={() => setOpen(true)}
              >
                <LogOut />
                {t('nav.logout')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}
