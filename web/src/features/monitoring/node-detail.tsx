import { useMemo, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import type { ChartPaletteId } from '@/config/chart-palette'
import { useTranslation } from 'react-i18next'
import { api, type Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { ErrorState, LoadingState, StatusBadge } from '@/features/shared/ui'
import {
  containerCompleteness,
  type LatestMetric,
} from './container-completeness'
import { historyPointValid } from './history'
import { scheduleHistory } from './history-queue'
import {
  useHistoryRange,
  type HistoryRange,
  type HistoryRangeWindow,
} from './history-range'
import { HistoryRangeSelect } from './history-range-select'
import {
  formatBps,
  formatBytes,
  formatLoad,
  formatPercent,
  formatRate,
  latestPointValue,
} from './metric-format'
import { MultiSeriesChart, type ChartSeries } from './multi-series-chart'
import { formatTemperature, formatUptime } from './node-overview-metrics'
import type { History, NodeOverview, Series } from './types'

const INTERFACE_KEYS = ['interface', 'device', 'iface', 'nic']
const VIRTUAL_INTERFACE_PREFIXES = [
  'lo',
  'docker',
  'veth',
  'br-',
  'virbr',
  'tun',
  'tap',
  'wg',
]

function isPercentMetric(metric: string) {
  return (
    metric.startsWith('cpu.') ||
    metric.startsWith('memory.') ||
    metric.startsWith('disk.') ||
    metric.startsWith('filesystem.') ||
    metric.startsWith('container.cpu.') ||
    metric.startsWith('container.memory.')
  )
}

function useMetricSeries(nodeId: string, metric: string) {
  return useQuery({
    queryKey: ['monitoring-series', nodeId, metric],
    queryFn: () =>
      api.get<{ series: Series[] }>(
        `/monitoring/nodes/${encodeURIComponent(nodeId)}/series?${new URLSearchParams({ metric })}`
      ),
    enabled: !!nodeId && !!metric,
  })
}

function useMetricHistory(
  nodeId: string,
  metric: string,
  series: string,
  range: HistoryRange,
  window: HistoryRangeWindow
) {
  return useQuery({
    queryKey: metricHistoryQueryKey(nodeId, metric, series, range, window),
    queryFn: ({ signal }) =>
      fetchMetricHistory(nodeId, metric, series, window, signal),
    staleTime: 30000,
    enabled: !!nodeId && !!metric && !!series && window.valid,
  })
}

function metricHistoryQueryKey(
  nodeId: string,
  metric: string,
  series: string,
  range: HistoryRange,
  window: HistoryRangeWindow
) {
  const start = window.start?.toISOString() || ''
  const end = window.end?.toISOString() || ''
  return [
    'monitoring-history',
    nodeId,
    metric,
    series,
    range,
    start,
    end,
    '', // Default server step is part of the cache identity.
  ] as const
}

function fetchMetricHistory(
  nodeId: string,
  metric: string,
  series: string,
  window: HistoryRangeWindow,
  signal: AbortSignal
) {
  return scheduleHistory(signal, () =>
    api.get<History>(
      `/monitoring/nodes/${encodeURIComponent(nodeId)}/metrics?${new URLSearchParams(
        {
          metric,
          series,
          start: window.start?.toISOString() || '',
          end: window.end?.toISOString() || '',
        }
      )}`,
      { signal }
    )
  )
}

function useSelectedSeries(
  series: Series[],
  dimensionKey?: string,
  preferredValue?: string
) {
  const [selected, setSelected] = useState('')
  const preferred =
    dimensionKey && preferredValue
      ? series.find((item) => item.dimensions[dimensionKey] === preferredValue)
      : undefined
  const fallback = (preferred || series[0])?.seriesKey || ''
  const active =
    selected && series.some((item) => item.seriesKey === selected)
      ? selected
      : fallback
  return [active, setSelected] as const
}

function seriesLabel(series: Series) {
  return (
    Object.entries(series.dimensions)
      .map(([key, value]) => `${key}=${value}`)
      .join(', ') || series.seriesKey
  )
}

function dimensionValue(dimensions: Record<string, string>, keys: string[]) {
  for (const key of keys) {
    if (dimensions[key]) return dimensions[key]
  }
  return ''
}

function pickDefaultInterface(values: string[]) {
  if (values.includes('enp1s0')) return 'enp1s0'
  return (
    values.find(
      (value) =>
        !VIRTUAL_INTERFACE_PREFIXES.some(
          (prefix) => value === prefix || value.startsWith(prefix)
        )
    ) ||
    values[0] ||
    ''
  )
}

function MetricChartCard({
  nodeId,
  metric,
  title,
  range,
  window,
  formatter,
  chartLabel,
  testId,
  seriesDimensionKey,
  preferredSeriesValue,
  selectorLabel,
  emptyMessage,
  paletteId,
  seriesId,
}: {
  nodeId: string
  metric: string
  title: string
  range: HistoryRange
  window: HistoryRangeWindow
  formatter: (value: number) => string | null
  chartLabel: string
  testId: string
  seriesDimensionKey?: string
  preferredSeriesValue?: string
  selectorLabel?: string
  emptyMessage?: string
  paletteId?: ChartPaletteId
  seriesId?: string
}) {
  const { t } = useTranslation()
  const seriesQuery = useMetricSeries(nodeId, metric)
  const seriesOptions = seriesQuery.data?.series || []
  const [series, setSeries] = useSelectedSeries(
    seriesOptions,
    seriesDimensionKey,
    preferredSeriesValue
  )
  const history = useMetricHistory(nodeId, metric, series, range, window)
  const latest = latestPointValue(history.data?.points || [])
  const hasValidPoints = (history.data?.points || []).some(historyPointValid)
  const error = seriesQuery.error || history.error
  const retry = () => {
    void seriesQuery.refetch()
    void history.refetch()
  }
  return (
    <Card data-testid={testId} className='flex flex-col'>
      <CardHeader className='flex flex-row flex-wrap items-center justify-between gap-2 space-y-0'>
        <CardTitle className='text-sm'>{title}</CardTitle>
        {selectorLabel && seriesOptions.length > 1 && (
          <div className='flex items-center gap-2'>
            <Label
              htmlFor={`${testId}-series`}
              className='text-xs text-muted-foreground'
            >
              {selectorLabel}
            </Label>
            <Select value={series} onValueChange={setSeries}>
              <SelectTrigger id={`${testId}-series`} className='w-44'>
                <SelectValue placeholder='—' />
              </SelectTrigger>
              <SelectContent>
                {seriesOptions.map((item) => (
                  <SelectItem key={item.seriesKey} value={item.seriesKey}>
                    {seriesDimensionKey && item.dimensions[seriesDimensionKey]
                      ? `${seriesDimensionKey}=${item.dimensions[seriesDimensionKey]}`
                      : seriesLabel(item)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </CardHeader>
      <CardContent className='flex flex-1 flex-col gap-3'>
        <p className='text-sm'>
          <span className='text-muted-foreground'>
            {t('monitoring.latestValue')}:{' '}
          </span>
          <span className='font-medium'>
            {latest === null
              ? t('monitoring.waitingForData')
              : formatter(latest) || t('monitoring.waitingForData')}
          </span>
        </p>
        {error ? (
          <ErrorState error={error} onRetry={retry} />
        ) : seriesQuery.isLoading || (series && history.isLoading) ? (
          <LoadingState />
        ) : !seriesOptions.length ? (
          <p className='text-sm text-muted-foreground'>
            {emptyMessage || t('monitoring.noSeries')}
          </p>
        ) : hasValidPoints && history.data ? (
          // 底部对齐：同一行卡片高度可能不同（标题/选择器/最新值占位差异），
          // 图表统一贴底后绘图区与 x 轴基线保持一致。
          <div className='mt-auto'>
            <MultiSeriesChart
              series={[
                {
                  key: `${metric}:${series}`,
                  seriesId: seriesId || metric,
                  paletteId,
                  name: title,
                  points: history.data.points,
                  unit: history.data.unit,
                  gaps: history.data.gaps,
                  stepNs: history.data.stepNs,
                },
              ]}
              height={180}
              yDomain={isPercentMetric(metric) ? [0, 100] : 'auto'}
              unit={history.data.unit}
              showLegend={false}
              formatValue={formatter}
              ariaLabel={chartLabel}
            />
          </div>
        ) : (
          <p className='text-sm text-muted-foreground'>
            {emptyMessage || t('monitoring.noPoints')}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

type MetricConfig = {
  metric: string
  name: string
  type?: 'area' | 'line'
  paletteId?: ChartPaletteId
}

function MultiMetricCard({
  nodeId,
  metrics,
  title,
  range,
  window,
  formatter,
  testId,
  dimensionKey,
  preferredDimensionValue,
  yDomain = 'auto',
  height = 180,
  className,
}: {
  nodeId: string
  metrics: MetricConfig[]
  title: string
  range: HistoryRange
  window: HistoryRangeWindow
  formatter: (value: number) => string | null
  testId: string
  dimensionKey?: string
  preferredDimensionValue?: string
  yDomain?: [number, number] | 'auto'
  height?: number
  className?: string
}) {
  const { t } = useTranslation()
  const seriesQueries = useQueries({
    queries: metrics.map((item) => ({
      queryKey: ['monitoring-series', nodeId, item.metric],
      queryFn: () =>
        api.get<{ series: Series[] }>(
          `/monitoring/nodes/${encodeURIComponent(nodeId)}/series?${new URLSearchParams({ metric: item.metric })}`
        ),
      enabled: !!nodeId,
    })),
  })
  const selections = metrics.map((item, index) => {
    const options = seriesQueries[index].data?.series || []
    const series =
      (dimensionKey &&
        preferredDimensionValue &&
        options.find(
          (option) =>
            option.dimensions[dimensionKey] === preferredDimensionValue
        )) ||
      options[0]
    return { ...item, series }
  })
  const historyQueries = useQueries({
    queries: selections.map((item) => ({
      queryKey: metricHistoryQueryKey(
        nodeId,
        item.metric,
        item.series?.seriesKey || '',
        range,
        window
      ),
      queryFn: ({ signal }) =>
        fetchMetricHistory(
          nodeId,
          item.metric,
          item.series!.seriesKey,
          window,
          signal
        ),
      staleTime: 30000,
      enabled: !!item.series && window.valid,
    })),
  })
  const chartSeries = selections
    .map((item, index): ChartSeries | null => {
      const history = historyQueries[index].data
      if (!history) return null
      return {
        key: item.metric,
        seriesId: item.metric,
        paletteId: item.paletteId,
        name: item.name,
        points: history.points,
        unit: history.unit,
        gaps: history.gaps,
        stepNs: history.stepNs,
        type: item.type || 'line',
      }
    })
    .filter((item): item is ChartSeries => item !== null)
  const loading =
    seriesQueries.some((query) => query.isLoading) ||
    historyQueries.some((query) => query.isLoading)
  return (
    <Card data-testid={testId} className={cn('flex flex-col', className)}>
      <CardHeader>
        <CardTitle className='text-sm'>{title}</CardTitle>
      </CardHeader>
      <CardContent className='flex flex-1 flex-col gap-2'>
        {chartSeries.length > 0 && (
          <MultiSeriesChart
            series={chartSeries}
            height={height}
            yDomain={yDomain}
            unit={chartSeries[0]?.unit}
            showLegend
            formatValue={formatter}
            ariaLabel={title}
          />
        )}
        {seriesQueries.map((query, index) =>
          query.error ? (
            <div key={metrics[index].metric}>
              <span>{metrics[index].name}</span>
              <ErrorState
                error={query.error}
                onRetry={() => {
                  void query.refetch()
                }}
              />
            </div>
          ) : null
        )}
        {historyQueries.map((query, index) =>
          query.error ? (
            <div key={metrics[index].metric}>
              <span>{metrics[index].name}</span>
              <ErrorState
                error={query.error}
                onRetry={() => {
                  void query.refetch()
                }}
              />
            </div>
          ) : null
        )}
        {loading && <LoadingState />}
        {!loading &&
          !chartSeries.length &&
          !seriesQueries.some((query) => query.error) &&
          !historyQueries.some((query) => query.error) && (
            <p className='text-sm text-muted-foreground'>
              {t('monitoring.noSeries')}
            </p>
          )}
      </CardContent>
    </Card>
  )
}

function splitCardSlug(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function splitCardTestId(base: string, value: string) {
  return `${base}-${splitCardSlug(value) || 'root'}`
}

type SplitMetricGroup = {
  key: string
  label: string
  extraLabels: string[]
  series: (Series | undefined)[]
}

/**
 * 按完整维度组合分组，避免同一挂载点下多个设备混用 used/total。
 * 每个指标在每个分组内最多取一条 series，缺失时保留空位而不是借用其他设备。
 */
function buildSplitGroups(
  seriesByMetric: Series[][],
  dimensionKey: string,
  groupDimensions: string[],
  allowedDevices?: Set<string>
) {
  const groups = new Map<string, SplitMetricGroup>()
  const identity = (dimensions: Record<string, string>) =>
    groupDimensions
      .map((key) => `${key}=${dimensions[key] ?? ''}`)
      .join('\u001f')
  for (let index = 0; index < seriesByMetric.length; index++) {
    for (const series of seriesByMetric[index]) {
      const label = series.dimensions[dimensionKey]
      if (!label) continue
      if (allowedDevices && !allowedDevices.has(label)) continue
      const key = identity(series.dimensions)
      let group = groups.get(key)
      if (!group) {
        group = {
          key,
          label,
          extraLabels: groupDimensions
            .filter((dimension) => dimension !== dimensionKey)
            .map((dimension) => series.dimensions[dimension])
            .filter((value): value is string => Boolean(value)),
          series: new Array(seriesByMetric.length).fill(undefined),
        }
        groups.set(key, group)
      }
      if (!group.series[index]) group.series[index] = series
    }
  }
  return [...groups.values()]
    .filter((group) => group.series.some(Boolean))
    .sort(
      (a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key)
    )
}

function SplitMultiMetricCards({
  nodeId,
  metrics,
  title,
  range,
  window,
  formatter,
  testId,
  dimensionKey,
  groupDimensions,
  allowedDevices,
}: {
  nodeId: string
  metrics: MetricConfig[]
  title: string
  range: HistoryRange
  window: HistoryRangeWindow
  formatter: (value: number) => string | null
  testId: string
  dimensionKey: string
  groupDimensions?: string[]
  allowedDevices?: Set<string>
}) {
  const { t } = useTranslation()
  const seriesQueries = useQueries({
    queries: metrics.map((item) => ({
      queryKey: ['monitoring-series', nodeId, item.metric],
      queryFn: () =>
        api.get<{ series: Series[] }>(
          `/monitoring/nodes/${encodeURIComponent(nodeId)}/series?${new URLSearchParams({ metric: item.metric })}`
        ),
      enabled: !!nodeId,
    })),
  })
  const resolvedGroupDimensions = groupDimensions ?? [dimensionKey]
  const groups = buildSplitGroups(
    seriesQueries.map((query) => query.data?.series || []),
    dimensionKey,
    resolvedGroupDimensions,
    allowedDevices
  )
  const labelCounts = new Map<string, number>()
  for (const group of groups) {
    labelCounts.set(group.label, (labelCounts.get(group.label) || 0) + 1)
  }
  let historyIndex = 0
  const groupedItems = groups.map((group) => {
    const duplicated = (labelCounts.get(group.label) || 0) > 1
    const extra =
      duplicated && group.extraLabels.length
        ? ` (${group.extraLabels.join(', ')})`
        : ''
    const suffix =
      duplicated && group.extraLabels.length
        ? `-${splitCardSlug(group.extraLabels.join('-'))}`
        : ''
    return {
      key: group.key,
      value: group.label,
      label: `${group.label}${extra}`,
      testId: `${splitCardTestId(testId, group.label)}${suffix}`,
      items: group.series.flatMap((series, index) =>
        series
          ? [{ ...metrics[index], series, historyIndex: historyIndex++ }]
          : []
      ),
    }
  })
  const histories = useQueries({
    queries: groupedItems.flatMap((group) =>
      group.items.map((item) => ({
        queryKey: metricHistoryQueryKey(
          nodeId,
          item.metric,
          item.series.seriesKey,
          range,
          window
        ),
        queryFn: ({ signal }) =>
          fetchMetricHistory(
            nodeId,
            item.metric,
            item.series.seriesKey,
            window,
            signal
          ),
        staleTime: 30000,
        enabled: window.valid,
      }))
    ),
  })
  const loading =
    seriesQueries.some((query) => query.isLoading) ||
    histories.some((query) => query.isLoading)
  const errors =
    seriesQueries.some((query) => query.error) ||
    histories.some((query) => query.error)
  if (!groupedItems.length) {
    return (
      <Card data-testid={testId}>
        <CardHeader>
          <CardTitle className='text-sm'>{title}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <LoadingState />
          ) : (
            <p className='text-sm text-muted-foreground'>
              {t('monitoring.noSeries')}
            </p>
          )}
          {seriesQueries.map((query, index) =>
            query.error ? (
              <ErrorState
                key={metrics[index].metric}
                error={query.error}
                onRetry={() => {
                  void query.refetch()
                }}
              />
            ) : null
          )}
        </CardContent>
      </Card>
    )
  }
  return (
    <>
      {errors &&
        seriesQueries.map((query, index) =>
          query.error ? (
            <ErrorState
              key={metrics[index].metric}
              error={query.error}
              onRetry={() => {
                void query.refetch()
              }}
            />
          ) : null
        )}
      {groupedItems.map((group) => {
        const chartSeries = group.items
          .map((item): ChartSeries | null => {
            const history = histories[item.historyIndex].data
            if (!history) return null
            return {
              key: `${item.metric}:${item.series.seriesKey}`,
              seriesId: item.metric,
              paletteId: item.paletteId,
              name: item.name,
              points: history.points,
              unit: history.unit,
              gaps: history.gaps,
              stepNs: history.stepNs,
              type: item.type || 'line',
            }
          })
          .filter((item): item is ChartSeries => item !== null)
        const hasValidPoints = chartSeries.some((item) =>
          item.points.some(historyPointValid)
        )
        return (
          <Card
            key={group.key}
            data-testid={group.testId}
            className='flex flex-col'
          >
            <CardHeader>
              <CardTitle className='text-sm'>
                {title} · {group.label}
              </CardTitle>
            </CardHeader>
            <CardContent className='flex flex-1 flex-col gap-3'>
              {group.items.map((item) =>
                histories[item.historyIndex].error ? (
                  <ErrorState
                    key={item.metric}
                    error={histories[item.historyIndex].error}
                    onRetry={() => {
                      void histories[item.historyIndex].refetch()
                    }}
                  />
                ) : null
              )}
              {group.items.some(
                (item) => histories[item.historyIndex].isLoading
              ) && <LoadingState />}
              {hasValidPoints ? (
                <div className='mt-auto'>
                  <MultiSeriesChart
                    series={chartSeries}
                    height={180}
                    yDomain='auto'
                    unit={chartSeries[0]?.unit}
                    showLegend
                    formatValue={formatter}
                    ariaLabel={`${title} ${group.label}`}
                  />
                </div>
              ) : (
                <p className='text-sm text-muted-foreground'>
                  {t('monitoring.noPoints')}
                </p>
              )}
            </CardContent>
          </Card>
        )
      })}
    </>
  )
}

const CONTAINER_METRICS = [
  'container.cpu.usage',
  'container.memory.usage',
  'container.network.rx_bps',
  'container.network.tx_bps',
]

function ContainerSection({
  nodeId,
  range,
  window,
  available,
}: {
  nodeId: string
  range: HistoryRange
  window: HistoryRangeWindow
  available?: boolean
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState('all')
  const queries = useQueries({
    queries: CONTAINER_METRICS.map((metric) => ({
      queryKey: ['monitoring-series', nodeId, metric],
      queryFn: () =>
        api.get<{ series: Series[] }>(
          `/monitoring/nodes/${encodeURIComponent(nodeId)}/series?${new URLSearchParams({ metric })}`
        ),
      enabled: available === true && !!nodeId,
    })),
  })
  const latest = useQuery({
    queryKey: ['monitoring-container-latest', nodeId],
    queryFn: () =>
      api.get<{ metrics: LatestMetric[] }>(
        `/monitoring/nodes/${encodeURIComponent(nodeId)}/latest`
      ),
    enabled: available === true && !!nodeId,
    refetchInterval: 30000,
  })
  const all = queries.flatMap((query) => query.data?.series || [])
  const identity = (item: Series) =>
    item.dimensions.container_id
      ? `id:${item.dimensions.container_id}`
      : `legacy:${item.dimensions.container || item.seriesKey}`
  const names = new Map(
    all.map((item) => [
      identity(item),
      item.dimensions.container ||
        item.dimensions.container_id ||
        item.seriesKey,
    ])
  )
  const completeness = latest.isError
    ? null
    : containerCompleteness(latest.data?.metrics || [])
  if (available !== true)
    return (
      <Card data-testid='metric-card-containers'>
        <CardContent>
          <p className='text-sm text-muted-foreground'>
            {t('monitoring.containerUnknown')}
          </p>
        </CardContent>
      </Card>
    )
  return (
    <>
      <div className='flex flex-wrap items-center justify-between gap-2 lg:col-span-2'>
        <p className='text-sm text-muted-foreground'>
          {!completeness
            ? t('monitoring.collectionUnknown')
            : completeness.partial
              ? t('monitoring.collectionPartial', completeness)
              : t('monitoring.collectionComplete', completeness)}
        </p>
        <Select value={selected} onValueChange={setSelected}>
          <SelectTrigger
            className='w-56'
            aria-label={t('monitoring.containerMore')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>{t('monitoring.allContainers')}</SelectItem>
            {[...names].map(([id, name]) => (
              <SelectItem key={id} value={id}>
                {name} ·{' '}
                {id.startsWith('legacy:')
                  ? t('monitoring.legacy')
                  : id.slice(3, 15)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {CONTAINER_METRICS.map((metric, index) => (
        <ContainerMetricCard
          key={metric}
          nodeId={nodeId}
          metric={metric}
          title={`${t('monitoring.container')} ${[t('monitoring.cpu'), t('monitoring.memory'), t('monitoring.networkRx'), t('monitoring.networkTx')][index]}`}
          range={range}
          window={window}
          query={queries[index]}
          selected={selected}
        />
      ))}
    </>
  )
}

function ContainerMetricCard({
  nodeId,
  metric,
  title,
  range,
  window,
  query,
  selected,
}: {
  nodeId: string
  metric: string
  title: string
  range: HistoryRange
  window: HistoryRangeWindow
  query: ReturnType<typeof useMetricSeries>
  selected: string
}) {
  const { t } = useTranslation()
  const series = (query.data?.series || []).filter(
    (item) =>
      selected === 'all' ||
      (item.dimensions.container_id
        ? `id:${item.dimensions.container_id}`
        : `legacy:${item.dimensions.container || item.seriesKey}`) === selected
  )
  const histories = useQueries({
    queries: series.map((item) => ({
      queryKey: metricHistoryQueryKey(
        nodeId,
        metric,
        item.seriesKey,
        range,
        window
      ),
      queryFn: ({ signal }) =>
        fetchMetricHistory(nodeId, metric, item.seriesKey, window, signal),
      enabled: window.valid,
      staleTime: 30000,
    })),
  })
  const chart = series.flatMap((item, index) => {
    const history = histories[index].data
    const name =
      item.dimensions.container ||
      item.dimensions.container_id ||
      item.seriesKey
    return history
      ? [
          {
            key: item.seriesKey,
            name: `${name} · ${item.dimensions.container_id ? item.dimensions.container_id.slice(0, 12) : t('monitoring.legacy')}`,
            points: history.points,
            gaps: history.gaps,
            stepNs: history.stepNs,
            unit: history.unit,
          },
        ]
      : []
  })
  const formatter = metric.includes('network') ? formatBps : formatPercent
  const cardId: Record<string, string> = {
    'container.cpu.usage': 'cpu',
    'container.memory.usage': 'memory',
    'container.network.rx_bps': 'rx',
    'container.network.tx_bps': 'tx',
  }
  return (
    <Card data-testid={`metric-card-container-${cardId[metric]}`}>
      <CardHeader>
        <CardTitle className='text-sm'>{title}</CardTitle>
      </CardHeader>
      <CardContent className='space-y-3'>
        {query.error && (
          <ErrorState
            error={query.error}
            onRetry={() => {
              void query.refetch()
            }}
          />
        )}
        {chart.length > 0 && (
          <MultiSeriesChart
            series={chart}
            height={180}
            yDomain='auto'
            unit={chart[0]?.unit}
            showLegend
            formatValue={formatter}
            ariaLabel={title}
          />
        )}
        {histories.map((history, index) =>
          history.error ? (
            <div key={series[index].seriesKey}>
              <span>
                {series[index].dimensions.container || series[index].seriesKey}
              </span>
              <ErrorState
                error={history.error}
                onRetry={() => {
                  void history.refetch()
                }}
              />
            </div>
          ) : null
        )}
        {(query.isLoading ||
          histories.some((history) => history.isLoading)) && <LoadingState />}
        {!query.isLoading && !series.length && (
          <p className='text-sm text-muted-foreground'>
            {t('monitoring.containerNoData')}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function CpuChartCard({
  nodeId,
  range,
  window,
}: {
  nodeId: string
  range: HistoryRange
  window: HistoryRangeWindow
}) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)
  const seriesQuery = useMetricSeries(nodeId, 'cpu.usage')
  const options = seriesQuery.data?.series || []
  const total = options.find((item) => item.dimensions.cpu === 'all')
  const history = useMetricHistory(
    nodeId,
    'cpu.usage',
    total?.seriesKey || '',
    range,
    window
  )
  const cores = options.filter(
    (item) => item.dimensions.cpu && item.dimensions.cpu !== 'all'
  )
  const histories = useQueries({
    queries: cores.map((item) => ({
      queryKey: metricHistoryQueryKey(
        nodeId,
        'cpu.usage',
        item.seriesKey,
        range,
        window
      ),
      queryFn: ({ signal }) =>
        fetchMetricHistory(nodeId, 'cpu.usage', item.seriesKey, window, signal),
      staleTime: 30000,
      enabled: expanded && window.valid,
    })),
  })
  const chartSeries = history.data
    ? [
        {
          key: 'total',
          name: t('monitoring.totalUsage'),
          points: history.data.points,
          gaps: history.data.gaps,
          stepNs: history.data.stepNs,
          unit: '%',
        },
      ]
    : []
  const coreSeries = cores.flatMap((item, index) => {
    const data = histories[index].data
    return data
      ? [
          {
            key: item.seriesKey,
            name: `CPU ${item.dimensions.cpu}`,
            points: data.points,
            gaps: data.gaps,
            stepNs: data.stepNs,
            unit: '%',
          },
        ]
      : []
  })
  const error = seriesQuery.error || history.error
  return (
    <Card data-testid='metric-card-cpu' className='flex flex-col lg:col-span-2'>
      <CardHeader className='flex flex-row flex-wrap items-center justify-between gap-2 space-y-0'>
        <CardTitle className='text-sm'>{t('monitoring.cpu')}</CardTitle>
        {cores.length > 0 && (
          <Button
            type='button'
            variant='outline'
            size='sm'
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? t('common.collapse') : t('monitoring.perCore')}
          </Button>
        )}
      </CardHeader>
      <CardContent className='flex flex-1 flex-col gap-3'>
        <p className='text-sm'>
          {t('monitoring.latestValue')}:{' '}
          {formatPercent(latestPointValue(history.data?.points || [])) ||
            t('monitoring.waitingForData')}
        </p>
        {error ? (
          <ErrorState
            error={error}
            onRetry={() => {
              void seriesQuery.refetch()
              void history.refetch()
            }}
          />
        ) : seriesQuery.isLoading || (total && history.isLoading) ? (
          <LoadingState />
        ) : chartSeries.length ? (
          <MultiSeriesChart
            series={chartSeries}
            height={230}
            yDomain={[0, 100]}
            unit='%'
            formatValue={formatPercent}
            ariaLabel={t('monitoring.cpu')}
          />
        ) : (
          <p className='text-sm text-muted-foreground'>
            {t('monitoring.noSeries')}
          </p>
        )}
        {expanded && (
          <div className='space-y-2 border-t pt-3'>
            <h4 className='text-xs font-medium'>{t('monitoring.perCore')}</h4>
            {coreSeries.length > 0 && (
              <MultiSeriesChart
                series={coreSeries}
                height={230}
                yDomain={[0, 100]}
                unit='%'
                showLegend
                formatValue={formatPercent}
                ariaLabel={`${t('monitoring.cpu')} ${t('monitoring.perCore')}`}
              />
            )}
            {histories.map((query, index) =>
              query.error ? (
                <ErrorState
                  key={cores[index].seriesKey}
                  error={query.error}
                  onRetry={() => {
                    void query.refetch()
                  }}
                />
              ) : null
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function LoadCard({
  nodeId,
  range,
  window,
}: {
  nodeId: string
  range: HistoryRange
  window: HistoryRangeWindow
}) {
  const { t } = useTranslation()
  return (
    <MultiMetricCard
      nodeId={nodeId}
      metrics={[
        { metric: 'load.1', name: 'load.1', paletteId: 'blue-violet' },
        { metric: 'load.5', name: 'load.5', paletteId: 'violet-pink' },
        { metric: 'load.15', name: 'load.15', paletteId: 'spring-green' },
      ]}
      title={t('monitoring.load')}
      range={range}
      window={window}
      formatter={formatLoad}
      testId='metric-card-load'
      height={180}
    />
  )
}

function NetworkMetricRow({
  label,
  history,
  chartLabel,
  paletteId,
  seriesId,
}: {
  label: string
  history?: History
  chartLabel: string
  paletteId?: ChartPaletteId
  seriesId: string
}) {
  const { t } = useTranslation()
  const latest = latestPointValue(history?.points || [])
  const hasValidPoints = (history?.points || []).some(historyPointValid)
  return (
    <div className='space-y-2'>
      <div className='flex items-center justify-between gap-2 text-sm'>
        <span className='text-muted-foreground'>{label}</span>
        <span className='font-medium'>
          {latest === null
            ? t('monitoring.waitingForData')
            : formatBps(latest) || t('monitoring.waitingForData')}
        </span>
      </div>
      {hasValidPoints && history ? (
        <MultiSeriesChart
          series={[
            {
              key: chartLabel,
              seriesId,
              paletteId,
              name: label,
              points: history.points,
              unit: history.unit,
              gaps: history.gaps,
              stepNs: history.stepNs,
            },
          ]}
          height={110}
          yDomain='auto'
          unit={history.unit}
          showLegend={false}
          formatValue={formatBps}
          ariaLabel={chartLabel}
        />
      ) : (
        <p className='text-sm text-muted-foreground'>
          {t('monitoring.noPoints')}
        </p>
      )}
    </div>
  )
}

function NetworkCard({
  nodeId,
  range,
  window,
}: {
  nodeId: string
  range: HistoryRange
  window: HistoryRangeWindow
}) {
  const { t } = useTranslation()
  const rxSeriesQuery = useMetricSeries(nodeId, 'network.rx_bps')
  const txSeriesQuery = useMetricSeries(nodeId, 'network.tx_bps')
  const rxOptions = rxSeriesQuery.data?.series || []
  const txOptions = txSeriesQuery.data?.series || []
  const interfaces = useMemo(() => {
    const values = new Set<string>()
    for (const item of [
      ...(rxSeriesQuery.data?.series || []),
      ...(txSeriesQuery.data?.series || []),
    ]) {
      const value = dimensionValue(item.dimensions, INTERFACE_KEYS)
      if (value) values.add(value)
    }
    return [...values]
  }, [rxSeriesQuery.data, txSeriesQuery.data])
  const [selectedInterface, setSelectedInterface] = useState('')
  const activeInterface =
    selectedInterface && interfaces.includes(selectedInterface)
      ? selectedInterface
      : pickDefaultInterface(interfaces)
  const rxSeries =
    rxOptions.find(
      (item) =>
        dimensionValue(item.dimensions, INTERFACE_KEYS) === activeInterface
    )?.seriesKey ||
    rxOptions[0]?.seriesKey ||
    ''
  const txSeries =
    txOptions.find(
      (item) =>
        dimensionValue(item.dimensions, INTERFACE_KEYS) === activeInterface
    )?.seriesKey ||
    txOptions[0]?.seriesKey ||
    ''
  const rxHistory = useMetricHistory(
    nodeId,
    'network.rx_bps',
    rxSeries,
    range,
    window
  )
  const txHistory = useMetricHistory(
    nodeId,
    'network.tx_bps',
    txSeries,
    range,
    window
  )
  const error =
    rxSeriesQuery.error ||
    txSeriesQuery.error ||
    rxHistory.error ||
    txHistory.error
  const retry = () => {
    void rxSeriesQuery.refetch()
    void txSeriesQuery.refetch()
    void rxHistory.refetch()
    void txHistory.refetch()
  }
  const loading =
    rxSeriesQuery.isLoading ||
    txSeriesQuery.isLoading ||
    (rxSeries && rxHistory.isLoading) ||
    (txSeries && txHistory.isLoading)
  return (
    <Card data-testid='metric-card-network' className='lg:col-span-2'>
      <CardHeader className='flex flex-row flex-wrap items-center justify-between gap-2 space-y-0'>
        <CardTitle className='text-sm'>{t('monitoring.network')}</CardTitle>
        {interfaces.length > 1 && (
          <div className='flex items-center gap-2'>
            <Label
              htmlFor='network-interface'
              className='text-xs text-muted-foreground'
            >
              {t('monitoring.interface')}
            </Label>
            <Select
              value={activeInterface}
              onValueChange={setSelectedInterface}
            >
              <SelectTrigger id='network-interface' className='w-40'>
                <SelectValue placeholder='—' />
              </SelectTrigger>
              <SelectContent>
                {interfaces.map((item) => (
                  <SelectItem key={item} value={item}>
                    {item}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </CardHeader>
      <CardContent className='space-y-4'>
        {error ? (
          <ErrorState error={error} onRetry={retry} />
        ) : loading ? (
          <LoadingState />
        ) : !interfaces.length ? (
          <p className='text-sm text-muted-foreground'>
            {t('monitoring.waitingForData')}
          </p>
        ) : (
          <>
            <NetworkMetricRow
              label={t('monitoring.networkRx')}
              history={rxHistory.data}
              chartLabel={`${t('monitoring.network')} ${t('monitoring.networkRx')}`}
              paletteId='cyan-blue'
              seriesId='network.rx_bps'
            />
            <NetworkMetricRow
              label={t('monitoring.networkTx')}
              history={txHistory.data}
              chartLabel={`${t('monitoring.network')} ${t('monitoring.networkTx')}`}
              paletteId='spring-green'
              seriesId='network.tx_bps'
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

export function NodeMonitoringDetail({ nodeId }: { nodeId: string }) {
  const { t } = useTranslation()
  const historyRange = useHistoryRange()
  const overview = useQuery({
    queryKey: ['monitoring-nodes-overview'],
    queryFn: () =>
      api.get<{ nodes: NodeOverview[] }>('/monitoring/nodes/overview'),
    refetchInterval: 30000,
    staleTime: 30000,
  })
  const row = overview.data?.nodes?.find((item) => item.node_id === nodeId)
  const nodes = useQuery({
    queryKey: ['monitoring-node-list'],
    queryFn: () => api.get<Node[]>('/nodes'),
    enabled: !!nodeId,
  })
  const node = nodes.data?.find((item) => item.id === nodeId)
  const readIopsSeries = useMetricSeries(nodeId, 'disk.read_iops')
  const writeIopsSeries = useMetricSeries(nodeId, 'disk.write_iops')
  const devices = node?.inventory?.block_devices
  const classified = new Map(
    (devices || []).map((device) => [device.name, device.kind])
  )
  const deviceClassificationUnknown =
    !devices ||
    [
      ...(readIopsSeries.data?.series || []),
      ...(writeIopsSeries.data?.series || []),
    ].some((item) => {
      const kind = classified.get(item.dimensions.device)
      return kind !== 'disk' && kind !== 'partition'
    }) ||
    devices.some((device) => device.kind === 'unknown')
  const containerAvailable =
    row?.container_metrics_available ?? node?.capabilities?.container_metrics
  if (overview.isLoading) {
    return (
      <>
        <NodeSteerHeader
          title={t('monitoring.nodeMonitoringDetail')}
          backTo='/monitoring'
          backLabel={t('monitoring.backToOverview')}
        />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  }
  if (overview.isError) {
    return (
      <>
        <NodeSteerHeader
          title={t('monitoring.nodeMonitoringDetail')}
          backTo='/monitoring'
          backLabel={t('monitoring.backToOverview')}
        />
        <Main>
          <ErrorState
            error={overview.error}
            onRetry={() => overview.refetch()}
          />
        </Main>
      </>
    )
  }
  return (
    <>
      <NodeSteerHeader
        title={t('monitoring.nodeMonitoringDetail')}
        backTo='/monitoring'
        backLabel={t('monitoring.backToOverview')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card>
          <CardContent className='flex flex-wrap items-start justify-between gap-4'>
            <div className='space-y-2'>
              <div className='flex flex-wrap items-center gap-2'>
                <CardTitle className='text-xl'>
                  {nodeDisplayName(
                    {
                      display_name: row?.display_name,
                      hostname: row?.hostname,
                      id: row?.node_id || nodeId,
                    },
                    t('common.unknownNode')
                  )}
                </CardTitle>
                {row && <StatusBadge status={row.status} />}
              </div>
              <div className='flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground'>
                <span>
                  {t('monitoring.hostname')}:{' '}
                  {node?.inventory?.hostname || t('monitoring.unknown')}
                </span>
                <span>
                  {t('monitoring.uptime')}:{' '}
                  {row?.uptime_seconds == null
                    ? t('monitoring.unknown')
                    : formatUptime(row.uptime_seconds, t)}
                </span>
                <span>
                  {t('monitoring.distribution')}:{' '}
                  {node?.inventory?.os || t('monitoring.unknown')}
                </span>
                <span>
                  {t('monitoring.kernel')}:{' '}
                  {node?.inventory?.kernel || t('monitoring.unknown')}
                </span>
                <span>
                  CPU:{' '}
                  {node?.inventory?.cpu?.[0]?.model || t('monitoring.unknown')}
                </span>
                <span>
                  {t('monitoring.memory')}:{' '}
                  {node?.inventory?.memory?.total_kb
                    ? formatBytes(node.inventory.memory.total_kb * 1024)
                    : t('monitoring.unknown')}
                </span>
              </div>
              {row?.state && row.state !== 'ready' && (
                <p className='text-sm text-muted-foreground'>
                  {t('monitoring.dataUnavailable')}
                </p>
              )}
              {nodes.error && (
                <ErrorState
                  error={nodes.error}
                  onRetry={() => {
                    void nodes.refetch()
                  }}
                />
              )}
            </div>
            <div className='flex flex-wrap items-end gap-2'>
              <HistoryRangeSelect
                id='detail-range'
                value={historyRange.range}
                onChange={historyRange.setRange}
                customStart={historyRange.customStart}
                customEnd={historyRange.customEnd}
                onCustomStartChange={historyRange.setCustomStart}
                onCustomEndChange={historyRange.setCustomEnd}
              />
              <Button asChild variant='outline'>
                <a href={`/agents/${nodeId}`}>{t('monitoring.nodeActions')}</a>
              </Button>
            </div>
          </CardContent>
        </Card>
        <div className='grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2 [&>*]:min-w-0'>
          <CpuChartCard
            nodeId={nodeId}
            range={historyRange.range}
            window={historyRange.window}
          />
          <MultiMetricCard
            nodeId={nodeId}
            metrics={[
              { metric: 'memory.cached_bytes', name: 'Cached' },
              { metric: 'memory.used_bytes', name: 'Used' },
              { metric: 'memory.buffers_bytes', name: 'Buffers' },
              { metric: 'memory.free_bytes', name: 'Free' },
              { metric: 'memory.available_bytes', name: 'Available' },
            ]}
            title={t('monitoring.memory')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatBytes}
            testId='metric-card-memory'
          />
          <MetricChartCard
            nodeId={nodeId}
            metric='memory.swap_usage'
            title={t('monitoring.swap')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatPercent}
            chartLabel={t('monitoring.swap')}
            testId='metric-card-swap'
            paletteId='gold-orange'
          />
          <LoadCard
            nodeId={nodeId}
            range={historyRange.range}
            window={historyRange.window}
          />
          <SplitMultiMetricCards
            nodeId={nodeId}
            metrics={[
              { metric: 'filesystem.used_bytes', name: t('monitoring.used') },
              { metric: 'filesystem.total_bytes', name: t('monitoring.total') },
            ]}
            title={t('monitoring.filesystem')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatBytes}
            testId='metric-card-disk'
            dimensionKey='mount'
            groupDimensions={['mount', 'device']}
          />
          <SplitMultiMetricCards
            nodeId={nodeId}
            metrics={[
              {
                metric: 'disk.read_bps',
                name: 'Read',
                paletteId: 'blue-violet',
              },
              { metric: 'disk.write_bps', name: 'Write', paletteId: 'coral' },
            ]}
            title={t('monitoring.diskIo')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatBps}
            testId='metric-card-disk-io'
            dimensionKey='device'
          />
          <SplitMultiMetricCards
            nodeId={nodeId}
            metrics={[
              {
                metric: 'disk.read_iops',
                name: 'Read',
                paletteId: 'blue-violet',
              },
              { metric: 'disk.write_iops', name: 'Write', paletteId: 'coral' },
            ]}
            title={t('monitoring.diskIops')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={(value) => value.toFixed(2)}
            testId='metric-card-disk-iops'
            dimensionKey='device'
            allowedDevices={
              new Set(
                (node?.inventory?.block_devices || [])
                  .filter((device) => device.kind === 'disk')
                  .map((device) => device.name)
              )
            }
          />
          {deviceClassificationUnknown && (
            <p className='text-sm text-muted-foreground'>
              {t('monitoring.blockDevicesUnknown')}
            </p>
          )}
          <NetworkCard
            nodeId={nodeId}
            range={historyRange.range}
            window={historyRange.window}
          />
          <MultiMetricCard
            nodeId={nodeId}
            metrics={[
              {
                metric: 'network.rx_errors',
                name: 'RX errors',
                paletteId: 'coral',
              },
              {
                metric: 'network.tx_errors',
                name: 'TX errors',
                paletteId: 'vermilion',
              },
              {
                metric: 'network.rx_dropped',
                name: 'RX dropped',
                paletteId: 'gold-orange',
              },
              {
                metric: 'network.tx_dropped',
                name: 'TX dropped',
                paletteId: 'peach-pink',
              },
            ]}
            title={t('monitoring.networkErrors')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatRate}
            testId='metric-card-network-errors'
            dimensionKey='interface'
            preferredDimensionValue='enp1s0'
            className='lg:col-span-2'
          />
          <MetricChartCard
            nodeId={nodeId}
            metric='system.temperature_c'
            title={t('dashboard.temperature')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatTemperature}
            chartLabel={t('dashboard.temperature')}
            testId='metric-card-temperature'
            emptyMessage={t('monitoring.sensorUnavailable')}
            paletteId='gold-orange'
          />
          <MetricChartCard
            nodeId={nodeId}
            metric='system.fan_rpm'
            title={t('monitoring.fan')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={(value) => `${value.toFixed(0)} RPM`}
            chartLabel={t('monitoring.fan')}
            testId='metric-card-fan'
            seriesDimensionKey='fan'
            selectorLabel={t('monitoring.fan')}
            emptyMessage={t('monitoring.sensorUnavailable')}
            paletteId='ice-blue'
          />
          <ContainerSection
            nodeId={nodeId}
            range={historyRange.range}
            window={historyRange.window}
            available={containerAvailable}
          />
        </div>
      </Main>
    </>
  )
}
