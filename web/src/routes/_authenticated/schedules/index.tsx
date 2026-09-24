import { createFileRoute } from '@tanstack/react-router'
import { Schedules } from '@/features/catalog'

// 调度为独立列表页（原重定向到 /tasks?view=schedules 已移除）
export const Route = createFileRoute('/_authenticated/schedules/')({
  component: Schedules,
})
