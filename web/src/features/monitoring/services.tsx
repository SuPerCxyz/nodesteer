import { useTranslation } from 'react-i18next'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { ServiceTargets } from './service-targets'

export function ServiceMonitoring() {
  const { t } = useTranslation()
  return (
    <>
      <NodeSteerHeader
        title={t('monitoring.httpServices')}
        description={t('monitoring.serviceMonitoringDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <ServiceTargets />
      </Main>
    </>
  )
}
