import { createFileRoute } from '@tanstack/react-router'
import { TransferDetail } from '@/features/transfers'

export const Route = createFileRoute('/_authenticated/transfers/$transferId')({
  component: TransferDetail,
})
