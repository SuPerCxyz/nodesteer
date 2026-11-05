import { createFileRoute } from '@tanstack/react-router'
import { AutomationRules } from '@/features/monitoring/rules'
import { AdministratorPage } from '@/features/shared/administrator-page'

export const Route = createFileRoute('/_authenticated/rules')({
  component: () => (
    <AdministratorPage titleKey='rules.title'>
      <AutomationRules />
    </AdministratorPage>
  ),
})
