import { useTranslation } from 'react-i18next'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { NodeMonitoring } from './nodes'

export function Monitoring() {
  const { t } = useTranslation()
  return (
    <>
      <NodeSteerHeader
        title={t('monitoring.monitoring')}
        description={t('monitoring.nodeMonitoringDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <NodeMonitoring />
      </Main>
    </>
  )
}
