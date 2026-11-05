import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { useTranslation } from 'react-i18next'
import { api } from '@/lib/api'
import { statusLabel } from '@/lib/status-labels'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ErrorState, LoadingState } from '@/features/shared/ui'
import { formatBps, formatLoad, percentTone } from './metric-format'
import type { NodeOverview } from './types'

export function NodeStatusDot({ status }: { status?: string | null }) {
  const key = (status || '').toLowerCase().replace(/[-\s]/g, '_')
  const color =
    key === 'online'
      ? 'bg-emerald-500'
      : key === 'offline'
        ? 'bg-red-500'
        : 'bg-amber-500'
  const label = statusLabel(status)
  return (
    <span
      role='img'
      aria-label={label}
      title={label}
      className={`inline-block size-2.5 shrink-0 rounded-full ${color}`}
    />
  )
}

export function NodeMetricBar({
  value,
  testId,
}: {
  value?: number | null
  testId?: string
}) {
  const hasValue = typeof value === 'number' && Number.isFinite(value)
  const percent = hasValue ? Math.min(100, Math.max(0, value)) : 0
  const tone = percentTone(value)
  const color =
    tone === 'danger'
      ? 'bg-red-600'
      : tone === 'warning'
        ? 'bg-amber-500'
        : 'bg-emerald-600'
  return (
    <div
      data-testid={testId}
      data-tone={tone}
      className='flex min-w-24 items-center gap-2'
    >
      <span className='w-12 text-right text-xs tabular-nums'>
        {hasValue ? `${value.toFixed(1)}%` : '—'}
      </span>
      {hasValue && (
        <div className='h-2 w-16 overflow-hidden rounded-full bg-muted-foreground/20 ring-1 ring-border/80 ring-inset dark:bg-muted-foreground/30 dark:ring-border'>
          <div
            data-testid={testId ? `${testId}-bar` : undefined}
            className={`h-full rounded-full ${color}`}
            style={{ width: `${percent}%` }}
          />
        </div>
      )}
    </div>
  )
}

export function formatTemperature(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(1)} °C`
    : '—'
}

export function formatUptime(seconds: number | null | undefined, t: TFunction) {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) {
    return '—'
  }
  const totalMinutes = Math.floor(seconds / 60)
  if (totalMinutes < 60) {
    return t('dashboard.uptimeMinutes', { minutes: totalMinutes })
  }
  const totalHours = Math.floor(totalMinutes / 60)
  if (totalHours < 24) {
    return t('dashboard.uptimeHours', { hours: totalHours })
  }
  return t('dashboard.uptimeDaysHours', {
    days: Math.floor(totalHours / 24),
    hours: totalHours % 24,
  })
}

export function networkTotalBps(row?: NodeOverview) {
  if (typeof row?.network_total_bps === 'number') {
    return row.network_total_bps
  }
  if (
    typeof row?.network_rx_bps === 'number' &&
    typeof row?.network_tx_bps === 'number'
  ) {
    return row.network_rx_bps + row.network_tx_bps
  }
  return null
}

function Metric({
  label,
  children,
  first,
}: {
  label: string
  children: ReactNode
  first?: boolean
}) {
  return (
    <div
      className={
        first
          ? 'min-w-24 space-y-1'
          : 'min-w-24 space-y-1 sm:border-l sm:border-border sm:pl-4'
      }
    >
      <div className='text-xs text-muted-foreground'>{label}</div>
      {children}
    </div>
  )
}

export function NodeMonitoringOverviewRow({
  nodeId,
  nodeName,
  status,
}: {
  nodeId: string
  nodeName: string
  status?: string
}) {
  const { t } = useTranslation()
  const overview = useQuery({
    queryKey: ['monitoring-nodes-overview'],
    queryFn: () =>
      api.get<{ nodes: NodeOverview[] }>('/monitoring/nodes/overview'),
    refetchInterval: 30000,
    staleTime: 30000,
  })
  const row = overview.data?.nodes?.find((item) => item.node_id === nodeId)
  if (overview.isLoading) {
    return (
      <Card data-testid='node-monitoring-overview-row'>
        <CardContent>
          <LoadingState />
        </CardContent>
      </Card>
    )
  }
  if (overview.isError) {
    return (
      <Card data-testid='node-monitoring-overview-row'>
        <CardContent>
          <ErrorState
            error={overview.error}
            onRetry={() => overview.refetch()}
          />
        </CardContent>
      </Card>
    )
  }
  return (
    <Card data-testid='node-monitoring-overview-row' className='gap-0 py-0'>
      <CardContent className='flex flex-wrap items-center gap-x-6 gap-y-3 py-4'>
        <div className='flex min-w-40 items-center gap-2'>
          <NodeStatusDot status={status} />
          <div className='min-w-0'>
            <div className='truncate font-medium'>{nodeName}</div>
            {!row && (
              <div className='text-xs text-muted-foreground'>
                {t('monitoring.waitingForData')}
              </div>
            )}
          </div>
        </div>
        <Metric label={t('dashboard.cpu')} first>
          <NodeMetricBar value={row?.cpu} testId='overview-cpu' />
        </Metric>
        <Metric label={t('monitoring.memory')}>
          <NodeMetricBar value={row?.memory} testId='overview-memory' />
        </Metric>
        <Metric label={t('monitoring.disk')}>
          <NodeMetricBar value={row?.disk} testId='overview-disk' />
        </Metric>
        <Metric label={t('dashboard.load')}>
          <span className='text-xs tabular-nums'>
            {formatLoad(row?.load1) ?? '—'} / {formatLoad(row?.load5) ?? '—'} /{' '}
            {formatLoad(row?.load15) ?? '—'}
          </span>
        </Metric>
        <Metric label={t('dashboard.network')}>
          <span className='text-xs tabular-nums'>
            {formatBps(networkTotalBps(row)) ?? '—'}
          </span>
        </Metric>
        <Metric label={t('dashboard.temperature')}>
          <span className='text-xs tabular-nums'>
            {formatTemperature(row?.temperature_c)}
          </span>
        </Metric>
        <Metric label={t('dashboard.uptime')}>
          <span className='text-xs tabular-nums'>
            {formatUptime(row?.uptime_seconds, t)}
          </span>
        </Metric>
        <Button asChild variant='outline' size='sm' className='ms-auto'>
          <a href={`/monitoring/nodes/${nodeId}`}>
            {t('dashboard.viewDetails')}
          </a>
        </Button>
      </CardContent>
    </Card>
  )
}
