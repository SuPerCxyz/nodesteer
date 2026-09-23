import { createFileRoute, redirect } from '@tanstack/react-router'

// 调度列表已合并到任务页的「调度」视图，旧路由保留重定向
export const Route = createFileRoute('/_authenticated/schedules/')({
  beforeLoad: () => {
    throw redirect({ to: '/tasks', search: { view: 'schedules' } })
  },
})
