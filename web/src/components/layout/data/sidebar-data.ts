import type { TFunction } from 'i18next'
import {
  Activity,
  ArrowLeftRight,
  AppWindow,
  CalendarClock,
  FileCode2,
  History,
  LayoutDashboard,
  ListTodo,
  Server,
  Settings,
  Users,
  Layers3,
} from 'lucide-react'
import type { SidebarData } from '../types'

export function getSidebarData(t: TFunction): SidebarData {
  return {
    user: {
      name: 'NodeSteer User',
      email: 'admin',
      avatar: '/images/nodesteer-logo-square.png',
    },
    teams: [],
    navGroups: [
      {
        title: t('nav.sections.overview'),
        items: [{ title: t('nav.dashboard'), url: '/', icon: LayoutDashboard }],
      },
      {
        title: t('nav.sections.operations'),
        items: [
          { title: t('nav.tasks'), url: '/tasks', icon: ListTodo },
          { title: t('nav.schedules'), url: '/schedules', icon: CalendarClock },
          { title: t('nav.executions'), url: '/executions', icon: Activity },
          {
            title: t('nav.transfers'),
            url: '/transfers',
            icon: ArrowLeftRight,
          },
        ],
      },
      {
        title: t('nav.sections.nodes'),
        items: [
          { title: t('nav.nodes'), url: '/agents', icon: Server },
          { title: t('nav.groups'), url: '/groups', icon: Layers3 },
        ],
      },
      {
        title: t('nav.sections.resources'),
        items: [
          { title: t('nav.scripts'), url: '/scripts', icon: FileCode2 },
          {
            title: t('nav.applications'),
            url: '/applications',
            icon: AppWindow,
          },
        ],
      },
      {
        title: t('nav.sections.system'),
        items: [
          { title: t('nav.audit'), url: '/audit', icon: History },
          { title: t('nav.users'), url: '/users', icon: Users },
          { title: t('nav.settings'), url: '/settings', icon: Settings },
        ],
      },
    ],
  }
}
