import { createFileRoute } from '@tanstack/react-router'
import { MonitoringAlerts } from '@/features/monitoring/alerts'
import { AdministratorPage } from '@/features/shared/administrator-page'

export const Route = createFileRoute('/_authenticated/monitoring/alerts')({
  component: () => (
    <AdministratorPage titleKey='alerts.title'>
      <MonitoringAlerts />
    </AdministratorPage>
  ),
})
