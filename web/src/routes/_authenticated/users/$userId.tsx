import { createFileRoute } from '@tanstack/react-router'
import { UserDetail } from '@/features/catalog'

export const Route = createFileRoute('/_authenticated/users/$userId')({
  component: UserDetail,
})
