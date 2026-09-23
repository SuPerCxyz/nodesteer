import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { TaskSchedules } from '@/features/tasks'

export const Route = createFileRoute('/_authenticated/tasks/')({
  validateSearch: z.object({
    view: z.enum(['tasks', 'schedules']).optional(),
  }),
  component: TaskSchedules,
})
