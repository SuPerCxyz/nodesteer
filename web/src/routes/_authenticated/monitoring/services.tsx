import { createFileRoute } from '@tanstack/react-router'
import { ServiceMonitoring } from '@/features/monitoring/services'
import { AdministratorPage } from '@/features/shared/administrator-page'

export const Route = createFileRoute('/_authenticated/monitoring/services')({
  component: () => (
    <AdministratorPage titleKey='nav.serviceMonitoring'>
      <ServiceMonitoring />
    </AdministratorPage>
  ),
})
