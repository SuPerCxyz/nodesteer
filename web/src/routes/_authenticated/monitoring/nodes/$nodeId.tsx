import { createFileRoute } from '@tanstack/react-router'
import { NodeMonitoringDetail } from '@/features/monitoring/node-detail'
import { AdministratorPage } from '@/features/shared/administrator-page'

export const Route = createFileRoute(
  '/_authenticated/monitoring/nodes/$nodeId'
)({
  component: NodeMonitoringDetailRoute,
})

function NodeMonitoringDetailRoute() {
  const { nodeId } = Route.useParams()
  return (
    <AdministratorPage titleKey='monitoring.nodeDetail'>
      <NodeMonitoringDetail nodeId={nodeId} />
    </AdministratorPage>
  )
}
