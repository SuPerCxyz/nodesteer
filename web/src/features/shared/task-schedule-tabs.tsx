import { useNavigate } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

export type TaskScheduleView = 'tasks' | 'schedules'

/**
 * 任务页视图切换：任务定义 / 调度。
 * 视图写入查询参数（/tasks?view=schedules），保证深链与刷新保持。
 */
export function TaskScheduleTabs({ value }: { value: TaskScheduleView }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <Tabs
      value={value}
      onValueChange={(next) =>
        navigate({
          to: '/tasks',
          search: next === 'schedules' ? { view: 'schedules' as const } : {},
        })
      }
    >
      <TabsList>
        <TabsTrigger value='tasks'>{t('nav.tasks')}</TabsTrigger>
        <TabsTrigger value='schedules'>{t('nav.schedules')}</TabsTrigger>
      </TabsList>
    </Tabs>
  )
}
