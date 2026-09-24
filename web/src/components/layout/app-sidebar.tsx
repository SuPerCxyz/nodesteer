import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth-store'
import { useLayout } from '@/context/layout-provider'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from '@/components/ui/sidebar'
import { AppTitle } from './app-title'
import { getSidebarData } from './data/sidebar-data'
import { NavGroup } from './nav-group'
import { NavUser } from './nav-user'

export function AppSidebar() {
  const { collapsible, variant } = useLayout()
  const { t } = useTranslation()
  const user = useAuthStore((state) => state.auth.user)
  const sidebarData = getSidebarData(t)
  const rawRole = user?.role?.[0]
  const roleKey =
    rawRole === 'administrator' ||
    rawRole === 'operator' ||
    rawRole === 'viewer'
      ? rawRole
      : 'administrator'
  const displayUser = {
    name: user?.username || user?.email || 'admin',
    email: user ? t(`misc.${roleKey}`) : t('misc.administrator'),
    // 接当前登录用户真实头像；无头像时保持原有静态 logo 视觉
    avatar: user?.avatar || sidebarData.user.avatar,
  }

  return (
    <Sidebar collapsible={collapsible} variant={variant}>
      <SidebarHeader>
        <AppTitle />
      </SidebarHeader>
      <SidebarContent>
        {sidebarData.navGroups.map((props) => (
          <NavGroup key={props.title} {...props} />
        ))}
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={displayUser} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
