import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, type Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RackList, RackRow } from '@/features/shared/rack-list'
import { EmptyState, ErrorState, LoadingState } from '@/features/shared/ui'
import { historyPointValid } from './history'
import { useHistoryRange } from './history-range'
import { HistoryRangeSelect } from './history-range-select'
import { MultiSeriesChart } from './multi-series-chart'
import { NodeMonitoringTable } from './node-monitoring-table'
import type { History, NodeOverview, Series } from './types'

export function NodeMonitoring() {
  const { t } = useTranslation()
  const [selected] = useState(
    () => new URLSearchParams(window.location.search).get('node') || ''
  )
  const [metricInput, setMetricInput] = useState('cpu.usage')
  const [metric, setMetric] = useState('cpu.usage')
  const [series, setSeries] = useState('')
  const historyRange = useHistoryRange()
  const nodes = useQuery({
    queryKey: ['monitoring-node-list'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const hasNodes = (nodes.data?.length || 0) > 0
  const overview = useQuery({
    queryKey: ['monitoring-nodes-overview'],
    queryFn: () =>
      api.get<{ nodes: NodeOverview[] }>('/monitoring/nodes/overview'),
    enabled: hasNodes,
    refetchInterval: 30000,
  })
  const seriesQuery = useQuery({
    queryKey: ['monitoring-series', selected, metric],
    queryFn: () =>
      api.get<{ series: Series[] }>(
        `/monitoring/nodes/${encodeURIComponent(selected)}/series?${new URLSearchParams({ metric })}`
      ),
    enabled: !!selected && !!metric,
  })
  const historyWindow = historyRange.window
  const historyStart = historyWindow.start?.toISOString() || ''
  const historyEnd = historyWindow.end?.toISOString() || ''
  const history = useQuery({
    queryKey: [
      'monitoring-history',
      selected,
      metric,
      series,
      historyRange.range,
      ...(historyRange.range === 'custom' ? [historyStart, historyEnd] : []),
    ],
    queryFn: () =>
      api.get<History>(
        `/monitoring/nodes/${encodeURIComponent(selected)}/metrics?${new URLSearchParams(
          {
            metric,
            series,
            start: historyStart,
            end: historyEnd,
          }
        )}`
      ),
    enabled: !!selected && !!metric && !!series && historyWindow.valid,
  })
  const overviewByNode = new Map(
    (overview.data?.nodes || []).map((item) => [item.node_id, item])
  )
  const onlineCount = (nodes.data || []).filter(
    (item) => item.status === 'online'
  ).length
  const nodesLoading = nodes.isLoading
  const nodesError = nodes.isError
  const retryOverview = () => {
    void nodes.refetch()
    if (hasNodes) void overview.refetch()
  }
  const stats = [
    {
      key: 'nodes',
      label: t('monitoring.nodeCount'),
      value: nodes.data?.length || 0,
    },
    { key: 'online', label: t('monitoring.onlineCount'), value: onlineCount },
    {
      key: 'alerts',
      label: t('monitoring.activeAlertsTotal'),
      value: t('webFix.alertsPending'),
    },
  ]
  return (
    <div className='space-y-6'>
      <Card>
        <CardHeader>
          <CardTitle>{t('monitoring.nodeOverview')}</CardTitle>
        </CardHeader>
        <CardContent className='space-y-4'>
          {nodesLoading ? (
            <LoadingState />
          ) : nodesError ? (
            <ErrorState error={nodes.error} onRetry={retryOverview} />
          ) : !hasNodes ? (
            <EmptyState message={t('monitoring.noNodes')} />
          ) : (
            <>
              <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-3'>
                {stats.map((item) => (
                  <div
                    key={item.key}
                    data-testid={`overview-stat-${item.key}`}
                    className='rounded-lg border bg-muted/30 p-3'
                  >
                    <div className='text-xs text-muted-foreground'>
                      {item.label}
                    </div>
                    <div className='text-2xl font-semibold'>{item.value}</div>
                  </div>
                ))}
              </div>
              <NodeMonitoringTable
                rowNavigation
                nodes={nodes.data || []}
                overviewByNode={overviewByNode}
                loading={overview.isLoading}
                error={overview.error}
                onRetry={() => overview.refetch()}
              />
            </>
          )}
        </CardContent>
      </Card>
      {!!selected && !nodesLoading && !nodesError && (
        <div>
          <Card>
            <CardHeader>
              <CardTitle>
                {t('monitoring.nodeHistory')}:{' '}
                {nodeDisplayName(
                  nodes.data?.find((node) => node.id === selected) ?? {
                    id: selected,
                  },
                  t('common.unknownNode')
                )}
              </CardTitle>
              <a href='/monitoring' className='text-sm text-primary'>
                {t('monitoring.backToOverview')}
              </a>
            </CardHeader>
            <CardContent className='space-y-4'>
              {!selected ? (
                <p className='text-sm text-muted-foreground'>
                  {t('monitoring.selectNodeForHistory')}
                </p>
              ) : (
                <>
                  <div className='flex flex-wrap items-end gap-2'>
                    <div>
                      <Label htmlFor='history-metric'>
                        {t('monitoring.metricName')}
                      </Label>
                      <Input
                        id='history-metric'
                        value={metricInput}
                        onChange={(event) => setMetricInput(event.target.value)}
                      />
                    </div>
                    <Button
                      variant='outline'
                      onClick={() => {
                        setMetric(metricInput.trim())
                        setSeries('')
                      }}
                    >
                      {t('monitoring.query')}
                    </Button>
                    <div>
                      <Label htmlFor='history-series'>
                        {t('monitoring.series')}
                      </Label>
                      <Select value={series} onValueChange={setSeries}>
                        <SelectTrigger id='history-series' className='min-w-48'>
                          <SelectValue placeholder='—' />
                        </SelectTrigger>
                        <SelectContent>
                          {(seriesQuery.data?.series || []).map((item) => (
                            <SelectItem
                              key={item.seriesKey}
                              value={item.seriesKey}
                            >
                              {Object.entries(item.dimensions)
                                .map(([k, v]) => `${k}=${v}`)
                                .join(', ') || item.seriesKey}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <HistoryRangeSelect
                      id='history-range'
                      value={historyRange.range}
                      onChange={historyRange.setRange}
                      customStart={historyRange.customStart}
                      customEnd={historyRange.customEnd}
                      onCustomStartChange={historyRange.setCustomStart}
                      onCustomEndChange={historyRange.setCustomEnd}
                    />
                  </div>
                  {seriesQuery.isError && (
                    <ErrorState
                      error={seriesQuery.error}
                      onRetry={() => seriesQuery.refetch()}
                    />
                  )}
                  {seriesQuery.isSuccess && !seriesQuery.data.series.length && (
                    <p className='text-sm text-muted-foreground'>
                      {t('monitoring.noSeries')}
                    </p>
                  )}
                  {history.isError && (
                    <ErrorState
                      error={history.error}
                      onRetry={() => history.refetch()}
                    />
                  )}
                  {history.isLoading && series && <LoadingState />}
                  {history.data && (
                    <>
                      <p className='text-sm text-muted-foreground'>
                        {t('monitoring.metricState')}: {history.data.state} ·{' '}
                        {history.data.unit || '—'}
                      </p>
                      {history.data.points.length === 0 ? (
                        <p>{t('monitoring.noPoints')}</p>
                      ) : (
                        <>
                          <MultiSeriesChart
                            series={[
                              {
                                key: `${metric}:${series}`,
                                seriesId: metric,
                                name: metric,
                                points: history.data.points,
                                unit: history.data.unit,
                                gaps: history.data.gaps,
                                stepNs: history.data.stepNs,
                              },
                            ]}
                            height={230}
                            yDomain={
                              metric.startsWith('cpu.') ||
                              metric.startsWith('memory.') ||
                              metric.startsWith('disk.') ||
                              metric.startsWith('filesystem.')
                                ? [0, 100]
                                : 'auto'
                            }
                            unit={history.data.unit}
                            showLegend={false}
                            ariaLabel={t('monitoring.chart')}
                          />
                          <RackList testId='monitoring-history-rack'>
                            {history.data.points.map((point, index) => {
                              const valid = historyPointValid(point)
                              return (
                                <RackRow
                                  key={`${point.timestamp}-${index}`}
                                  testId={`monitoring-history-row-${index}`}
                                  title={new Date(
                                    point.timestamp
                                  ).toLocaleString()}
                                  fields={[
                                    {
                                      label: t('monitoring.value'),
                                      value:
                                        point.value === null
                                          ? '—'
                                          : point.value,
                                      mono: true,
                                    },
                                    {
                                      label: t('monitoring.quality'),
                                      value: valid
                                        ? t('monitoring.validSample')
                                        : t('monitoring.incompleteSample'),
                                      tone: valid ? 'success' : 'warning',
                                    },
                                  ]}
                                />
                              )
                            })}
                          </RackList>
                        </>
                      )}
                    </>
                  )}
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
