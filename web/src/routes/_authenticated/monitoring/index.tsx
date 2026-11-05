import { createFileRoute } from '@tanstack/react-router'
import { Monitoring } from '@/features/monitoring'
import { AdministratorPage } from '@/features/shared/administrator-page'

export const Route = createFileRoute('/_authenticated/monitoring/')({
  component: () => (
    <AdministratorPage titleKey='monitoring.monitoring'>
      <Monitoring />
    </AdministratorPage>
  ),
})
