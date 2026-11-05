import { z } from 'zod'
import { createFileRoute } from '@tanstack/react-router'
import { Executions } from '@/features/executions'

export const Route = createFileRoute('/_authenticated/executions/')({
  validateSearch: z.object({
    q: z.string().optional(),
    node_id: z.string().optional(),
    schedule_id: z.string().optional(),
    status: z.string().optional(),
    trigger: z.string().optional(),
  }),
  component: Executions,
})
