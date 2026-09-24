import { z } from 'zod'
import { createFileRoute, redirect } from '@tanstack/react-router'
import { Tasks } from '@/features/tasks'

export const Route = createFileRoute('/_authenticated/tasks/')({
  validateSearch: z.object({
    view: z.enum(['tasks', 'schedules']).optional(),
  }),
  // 旧链接 /tasks?view=schedules 按 301 语义重定向到独立调度页，避免断链
  beforeLoad: ({ search }) => {
    if (search.view === 'schedules') throw redirect({ to: '/schedules' })
  },
  component: Tasks,
})
