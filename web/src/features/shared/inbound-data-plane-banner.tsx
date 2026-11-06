import { AlertTriangle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { blockedInboundNodes } from './inbound-data-plane'

/** 入口级提示：仅当当前选中/目标节点为入站会话且未声明数据面时展示。 */
export function InboundDataPlaneBanner({
  nodes,
}: {
  nodes: Array<Node | undefined>
}) {
  const { t } = useTranslation()
  const blocked = blockedInboundNodes(nodes)
  if (!blocked.length) return null
  return (
    <div
      role='alert'
      data-testid='inbound-data-plane-banner'
      className='rounded-md border border-warning/50 bg-warning/10 p-3 text-sm'
    >
      <p className='flex items-start gap-2 font-medium'>
        <AlertTriangle className='mt-0.5 size-4 shrink-0' />
        {t('nodes.inboundDataPlaneBlocked', {
          nodes: blocked
            .map((node) => nodeDisplayName(node, t('common.unknownNode')))
            .join(', '),
        })}
      </p>
      <p className='mt-1 text-xs text-muted-foreground'>
        {t('nodes.inboundDataPlaneBlockedHint')}
      </p>
    </div>
  )
}
