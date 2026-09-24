import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { ScheduleEditor } from '@/features/editors'

export const Route = createFileRoute('/_authenticated/schedules/new')({
  // 任务详情「新建调度」入口用 task_id 预选任务
  validateSearch: z.object({
    task_id: z.string().optional(),
  }),
  component: ScheduleEditor,
})
