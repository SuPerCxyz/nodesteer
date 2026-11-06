import { useMemo, useState, type ReactNode } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import type { ChartPaletteId } from '@/config/chart-palette'
import {
  Activity,
  Clock3,
  Cpu,
  Maximize2,
  MemoryStick,
  Package,
  Server,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, type Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import {
  ErrorState,
  LoadingState,
  StatusBadge,
  TimeValue,
} from '@/features/shared/ui'
import {
  containerCompleteness,
  type LatestMetric,
} from './container-completeness'
import { historyPointValid } from './history'
import { scheduleHistory } from './history-queue'
import {
  HISTORY_REFRESH_MS,
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

const refreshIntervalWhenVisible = (ms: number) => () =>
  typeof document !== 'undefined' && document.hidden ? false : ms

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

const CPU_BREAKDOWN = [
  {
    metric: 'cpu.user',
    short: 'us',
    labelKey: 'monitoring.cpuUser',
    paletteId: 'series-1',
  },
  {
    metric: 'cpu.system',
    short: 'sys',
    labelKey: 'monitoring.cpuSystem',
    paletteId: 'series-2',
  },
  {
    metric: 'cpu.iowait',
    short: 'wa',
    labelKey: 'monitoring.cpuIowait',
    paletteId: 'series-11',
  },
  {
    metric: 'cpu.softirq',
    short: 'si',
    labelKey: 'monitoring.cpuSoftirq',
    paletteId: 'series-9',
  },
  {
    metric: 'cpu.steal',
    short: 'st',
    labelKey: 'monitoring.cpuSteal',
    paletteId: 'series-10',
  },
] as const satisfies ReadonlyArray<{
  metric: string
  short: string
  labelKey: string
  paletteId: ChartPaletteId
}>

function HeaderFact({
  icon,
  label,
  value,
  testId,
  showLabel = false,
}: {
  icon: ReactNode
  label: string
  value: ReactNode
  testId?: string
  showLabel?: boolean
}) {
  return (
    <span
      data-testid={testId}
      className='inline-flex max-w-full min-w-0 items-center gap-1.5 text-sm'
    >
      <span
        role='img'
        aria-label={label}
        title={label}
        className='inline-flex shrink-0 items-center text-muted-foreground'
      >
        {icon}
      </span>
      {showLabel && (
        <span className='shrink-0 text-muted-foreground'>{label}:</span>
      )}
      <span
        className={cn(
          'min-w-0 font-medium text-foreground',
          showLabel ? 'truncate' : '[overflow-wrap:anywhere] break-words'
        )}
      >
        {value}
      </span>
    </span>
  )
}

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
    staleTime: HISTORY_REFRESH_MS,
    refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
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
  hideLatest = false,
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
  hideLatest?: boolean
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
        {!hideLatest && (
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
        )}
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
  alert?: 'danger' | 'warning' | 'info'
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
      staleTime: HISTORY_REFRESH_MS,
      refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
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
        alert: item.alert,
        name: item.name,
        points: history.points,
        unit: history.unit,
        gaps: history.gaps,
        stepNs: history.stepNs,
        type: item.type || 'area',
      }
    })
    .filter((item): item is ChartSeries => item !== null)
  const loading =
    seriesQueries.some((query) => query.isLoading) ||
    historyQueries.some((query) => query.isLoading)
  return (
    <Card data-testid={testId} className={cn('flex flex-col', className)}>
      {!chartSeries.length && (
        <CardHeader>
          <CardTitle className='text-sm'>{title}</CardTitle>
        </CardHeader>
      )}
      <CardContent className='flex flex-1 flex-col gap-2'>
        {chartSeries.length > 0 && (
          <MultiSeriesChart
            series={chartSeries}
            title={title}
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

function isPartitionDeviceName(name: string) {
  return (
    /^(sd[a-z]+|vd[a-z]+|xvd[a-z]+)\d+$/.test(name) ||
    /^nvme\d+n\d+p\d+$/.test(name) ||
    /^mmcblk\d+p\d+$/.test(name) ||
    /^(md|dm-|loop|bcache|nbd)\d+p\d+$/.test(name)
  )
}

/** 优先使用 inventory 分类；分类缺失时只用明确的设备名规则兜底。 */
function isWholeDisk(name: string, kinds?: Map<string, string>) {
  const kind = kinds?.get(name)
  if (kind) return kind === 'disk'
  return !isPartitionDeviceName(name)
}

/**
 * 按完整维度组合分组，避免同一挂载点下多个设备混用 used/total。
 * 每个指标在每个分组内最多取一条 series，缺失时保留空位而不是借用其他设备。
 */
function buildSplitGroups(
  seriesByMetric: Series[][],
  dimensionKey: string,
  groupDimensions: string[],
  wholeDiskOnly = false,
  deviceKinds?: Map<string, string>
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
      if (wholeDiskOnly && !isWholeDisk(label, deviceKinds)) continue
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
  wholeDiskOnly,
  deviceKinds,
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
  wholeDiskOnly?: boolean
  deviceKinds?: Map<string, string>
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
    wholeDiskOnly,
    deviceKinds
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
        staleTime: HISTORY_REFRESH_MS,
        refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
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
              alert: item.alert,
              name: item.name,
              points: history.points,
              unit: history.unit,
              gaps: history.gaps,
              stepNs: history.stepNs,
              type: item.type || 'area',
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
const CONTAINER_TITLES = ['cpu', 'memory', 'networkRx', 'networkTx'] as const
const MAX_CONTAINER_SERIES = 32

function containerIdentity(item: Series) {
  return item.dimensions.container_id
    ? `id:${item.dimensions.container_id}`
    : `legacy:${item.dimensions.container || item.seriesKey}`
}

function containerName(item: Series) {
  return (
    item.dimensions.container || item.dimensions.container_id || item.seriesKey
  )
}

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
  const [detailIdentity, setDetailIdentity] = useState<string | null>(null)
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
    refetchInterval: refreshIntervalWhenVisible(30000),
  })
  const seriesByMetric = queries.map((query) => query.data?.series || [])
  const names = new Map<string, string>()
  for (const series of seriesByMetric.flat()) {
    const identity = containerIdentity(series)
    if (!names.has(identity)) names.set(identity, containerName(series))
  }
  const identities = [...names.keys()].slice(0, MAX_CONTAINER_SERIES)
  const allowedIdentities = new Set(identities)
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
        <div className='flex flex-wrap items-center gap-2'>
          <Select value={selected} onValueChange={setSelected}>
            <SelectTrigger
              className='w-56'
              aria-label={t('monitoring.containerMore')}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='all'>
                {t('monitoring.allContainers')}
              </SelectItem>
              {identities.map((id) => (
                <SelectItem key={id} value={id}>
                  {names.get(id)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            type='button'
            variant='outline'
            size='sm'
            data-testid='container-expand-all'
            disabled={!identities.length}
            onClick={() => setDetailIdentity(identities[0] || '')}
          >
            <Maximize2 className='me-1 size-4' />
            {t('monitoring.expandAllContainers')}
          </Button>
        </div>
      </div>
      {CONTAINER_METRICS.map((metric, index) => (
        <ContainerMetricCard
          key={metric}
          nodeId={nodeId}
          metric={metric}
          title={t(
            `monitoring.${CONTAINER_TITLES[index]}` as
              | 'monitoring.cpu'
              | 'monitoring.memory'
              | 'monitoring.networkRx'
              | 'monitoring.networkTx'
          )}
          range={range}
          window={window}
          query={queries[index]}
          seriesOptions={seriesByMetric[index]}
          allowedIdentities={allowedIdentities}
          selected={selected}
          onOpenContainer={setDetailIdentity}
        />
      ))}
      <ContainerDetailDialog
        open={detailIdentity !== null}
        onOpenChange={(open) => {
          if (!open) setDetailIdentity(null)
        }}
        nodeId={nodeId}
        range={range}
        window={window}
        seriesByMetric={seriesByMetric}
        identities={identities}
        names={names}
        selected={detailIdentity || identities[0] || ''}
        onSelect={setDetailIdentity}
      />
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
  seriesOptions,
  allowedIdentities,
  selected,
  onOpenContainer,
}: {
  nodeId: string
  metric: string
  title: string
  range: HistoryRange
  window: HistoryRangeWindow
  query: ReturnType<typeof useMetricSeries>
  seriesOptions: Series[]
  allowedIdentities: Set<string>
  selected: string
  onOpenContainer: (identity: string) => void
}) {
  const { t } = useTranslation()
  const series = seriesOptions.filter(
    (item) =>
      allowedIdentities.has(containerIdentity(item)) &&
      (selected === 'all' || containerIdentity(item) === selected)
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
      staleTime: HISTORY_REFRESH_MS,
      refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
    })),
  })
  const chart = series.flatMap((item, index) => {
    const history = histories[index].data
    const name = containerName(item)
    return history
      ? [
          {
            key: item.seriesKey,
            name,
            hideLegendValue: true,
            points: history.points,
            gaps: history.gaps,
            stepNs: history.stepNs,
            unit: history.unit,
          },
        ]
      : []
  })
  const identityBySeriesKey = new Map(
    series.map((item) => [item.seriesKey, containerIdentity(item)])
  )
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
            onSeriesClick={(key) => {
              const identity = identityBySeriesKey.get(key)
              if (identity) onOpenContainer(identity)
            }}
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

function ContainerDetailDialog({
  open,
  onOpenChange,
  nodeId,
  range,
  window,
  seriesByMetric,
  identities,
  names,
  selected,
  onSelect,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  nodeId: string
  range: HistoryRange
  window: HistoryRangeWindow
  seriesByMetric: Series[][]
  identities: string[]
  names: Map<string, string>
  selected: string
  onSelect: (identity: string) => void
}) {
  const { t } = useTranslation()
  const selectedSeries = seriesByMetric.map((items) =>
    items.find((item) => containerIdentity(item) === selected)
  )
  const histories = useQueries({
    queries: CONTAINER_METRICS.map((metric, index) => ({
      queryKey: metricHistoryQueryKey(
        nodeId,
        metric,
        selectedSeries[index]?.seriesKey || '',
        range,
        window
      ),
      queryFn: ({ signal }) =>
        fetchMetricHistory(
          nodeId,
          metric,
          selectedSeries[index]!.seriesKey,
          window,
          signal
        ),
      enabled: open && Boolean(selectedSeries[index]) && window.valid,
      staleTime: HISTORY_REFRESH_MS,
      refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
    })),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid='container-detail-dialog'
        className='flex h-dvh max-h-none w-full max-w-none flex-col gap-0 rounded-none p-0 sm:h-[80vh] sm:max-w-5xl sm:rounded-lg'
      >
        <DialogHeader className='border-b px-4 py-3 text-start'>
          <DialogTitle>{t('monitoring.containerDetail')}</DialogTitle>
          <DialogDescription className='sr-only'>
            {t('monitoring.containerDetailDescription')}
          </DialogDescription>
        </DialogHeader>
        <div className='grid min-h-0 flex-1 grid-rows-[auto_1fr] sm:grid-cols-[220px_1fr] sm:grid-rows-1'>
          <div className='max-h-36 overflow-auto border-b p-2 sm:max-h-none sm:border-e sm:border-b-0'>
            {identities.map((identity) => (
              <button
                key={identity}
                type='button'
                onClick={() => onSelect(identity)}
                className={cn(
                  'flex w-full min-w-0 items-center justify-between gap-2 rounded-md px-2 py-1.5 text-start text-sm hover:bg-accent',
                  identity === selected && 'bg-accent font-medium'
                )}
                aria-pressed={identity === selected}
              >
                <span className='truncate'>{names.get(identity)}</span>
              </button>
            ))}
          </div>
          <div className='grid min-h-0 gap-4 overflow-auto p-3 sm:grid-cols-2'>
            {CONTAINER_METRICS.map((metric, index) => {
              const history = histories[index].data
              const query = histories[index]
              const name = selectedSeries[index]
                ? containerIdentity(selectedSeries[index])
                : ''
              const title = t(
                `monitoring.${CONTAINER_TITLES[index]}` as
                  | 'monitoring.cpu'
                  | 'monitoring.memory'
                  | 'monitoring.networkRx'
                  | 'monitoring.networkTx'
              )
              const formatter = metric.includes('network')
                ? formatBps
                : formatPercent
              return (
                <Card key={metric} data-testid={`container-detail-${index}`}>
                  <CardHeader>
                    <CardTitle className='text-sm'>{title}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {query.error ? (
                      <ErrorState
                        error={query.error}
                        onRetry={() => {
                          void query.refetch()
                        }}
                      />
                    ) : query.isLoading ? (
                      <LoadingState />
                    ) : history && history.points.some(historyPointValid) ? (
                      <MultiSeriesChart
                        series={[
                          {
                            key: `${metric}:${name}`,
                            name: title,
                            points: history.points,
                            gaps: history.gaps,
                            stepNs: history.stepNs,
                            unit: history.unit,
                          },
                        ]}
                        height={170}
                        yDomain='auto'
                        showLegend={false}
                        formatValue={formatter}
                        ariaLabel={title}
                      />
                    ) : (
                      <p className='text-sm text-muted-foreground'>
                        {t('monitoring.noPoints')}
                      </p>
                    )}
                  </CardContent>
                </Card>
              )
            })}
          </div>
        </div>
      </DialogContent>
    </Dialog>
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
  const breakdownQueries = useQueries({
    queries: CPU_BREAKDOWN.map((item) => ({
      queryKey: ['monitoring-series', nodeId, item.metric],
      queryFn: () =>
        api.get<{ series: Series[] }>(
          `/monitoring/nodes/${encodeURIComponent(nodeId)}/series?${new URLSearchParams({ metric: item.metric })}`
        ),
      enabled: !!nodeId,
    })),
  })
  const breakdownSeries = breakdownQueries.map((query) =>
    (query.data?.series || []).find((item) => item.dimensions.cpu === 'all')
  )
  const breakdownHistories = useQueries({
    queries: breakdownSeries.map((series, index) => ({
      queryKey: metricHistoryQueryKey(
        nodeId,
        CPU_BREAKDOWN[index].metric,
        series?.seriesKey || '',
        range,
        window
      ),
      queryFn: ({ signal }) =>
        fetchMetricHistory(
          nodeId,
          CPU_BREAKDOWN[index].metric,
          series!.seriesKey,
          window,
          signal
        ),
      staleTime: HISTORY_REFRESH_MS,
      refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
      enabled: !!series && window.valid,
    })),
  })
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
      staleTime: HISTORY_REFRESH_MS,
      refetchInterval: refreshIntervalWhenVisible(HISTORY_REFRESH_MS),
      enabled: expanded && window.valid,
    })),
  })
  const chartSeries: ChartSeries[] = []
  if (history.data) {
    CPU_BREAKDOWN.forEach((item, index) => {
      const series = breakdownSeries[index]
      const data = breakdownHistories[index].data
      if (!series || !data) return
      chartSeries.push({
        key: `${item.metric}:${series.seriesKey}`,
        seriesId: item.metric,
        paletteId: item.paletteId,
        name: item.short,
        tooltipName: t(item.labelKey),
        points: data.points,
        gaps: data.gaps,
        stepNs: data.stepNs,
        unit: '%',
        type: 'area',
      })
    })
    chartSeries.push({
      key: 'cpu.usage:total',
      seriesId: 'cpu.usage',
      name: t('monitoring.totalUsage'),
      tooltipName: t('monitoring.totalUsage'),
      points: history.data.points,
      gaps: history.data.gaps,
      stepNs: history.data.stepNs,
      unit: '%',
      type: 'area',
    })
  }
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
            type: 'area' as const,
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
        { metric: 'load.1', name: 'load.1', paletteId: 'series-1' },
        { metric: 'load.5', name: 'load.5', paletteId: 'series-2' },
        { metric: 'load.15', name: 'load.15', paletteId: 'series-4' },
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
          (() => {
            const chart: ChartSeries[] = []
            if (rxHistory.data) {
              chart.push({
                key: 'network.rx_bps',
                seriesId: 'network.rx_bps',
                paletteId: 'series-2',
                name: t('monitoring.networkRx'),
                points: rxHistory.data.points,
                unit: rxHistory.data.unit,
                gaps: rxHistory.data.gaps,
                stepNs: rxHistory.data.stepNs,
              })
            }
            if (txHistory.data) {
              chart.push({
                key: 'network.tx_bps',
                seriesId: 'network.tx_bps',
                paletteId: 'series-5',
                name: t('monitoring.networkTx'),
                points: txHistory.data.points,
                unit: txHistory.data.unit,
                gaps: txHistory.data.gaps,
                stepNs: txHistory.data.stepNs,
              })
            }
            return chart.some((item) => item.points.some(historyPointValid)) ? (
              <MultiSeriesChart
                series={chart}
                height={200}
                yDomain='auto'
                unit={chart[0]?.unit}
                showLegend
                formatValue={formatBps}
                ariaLabel={t('monitoring.network')}
              />
            ) : (
              <p className='text-sm text-muted-foreground'>
                {t('monitoring.noPoints')}
              </p>
            )
          })()
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
    refetchInterval: refreshIntervalWhenVisible(30000),
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
  const cpuInfo = node?.inventory?.cpu?.[0]
  const cpuValue =
    cpuInfo?.model || cpuInfo?.cores
      ? [
          cpuInfo.model,
          cpuInfo.cores != null
            ? `${cpuInfo.cores} ${t('nodes.cores')}`
            : undefined,
        ]
          .filter(Boolean)
          .join(' · ')
      : '—'
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
              <div className='flex flex-wrap gap-x-4 gap-y-1.5'>
                <HeaderFact
                  testId='node-header-hostname'
                  icon={
                    <Server
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label={t('monitoring.hostname')}
                  value={node?.inventory?.hostname || '—'}
                />
                <HeaderFact
                  testId='node-header-uptime'
                  icon={
                    <Clock3
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label={t('monitoring.uptime')}
                  value={formatUptime(row?.uptime_seconds, t)}
                />
                <HeaderFact
                  testId='node-header-distribution'
                  icon={
                    <Package
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label={t('monitoring.distribution')}
                  value={node?.inventory?.os || '—'}
                />
                <HeaderFact
                  testId='node-header-cpu'
                  icon={
                    <Cpu
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label='CPU'
                  value={cpuValue}
                />
                <HeaderFact
                  testId='node-header-memory'
                  icon={
                    <MemoryStick
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label={t('monitoring.memory')}
                  value={
                    node?.inventory?.memory?.total_kb
                      ? formatBytes(node.inventory.memory.total_kb * 1024) ||
                        '—'
                      : '—'
                  }
                />
                <HeaderFact
                  showLabel
                  testId='node-header-last-sample'
                  icon={
                    <Activity
                      className='size-3.5'
                      strokeWidth={1.8}
                      aria-hidden='true'
                    />
                  }
                  label={t('monitoring.lastSample')}
                  value={
                    row?.observed_at ? (
                      <TimeValue value={row.observed_at} />
                    ) : (
                      '—'
                    )
                  }
                />
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
              {
                metric: 'memory.cached_bytes',
                name: 'Cached',
                paletteId: 'series-3',
              },
              {
                metric: 'memory.used_bytes',
                name: 'Used',
                paletteId: 'series-1',
              },
              {
                metric: 'memory.buffers_bytes',
                name: 'Buffers',
                paletteId: 'series-6',
              },
              {
                metric: 'memory.free_bytes',
                name: 'Free',
                paletteId: 'series-11',
              },
              {
                metric: 'memory.available_bytes',
                name: 'Available',
                paletteId: 'series-5',
              },
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
            paletteId='series-11'
            hideLatest
          />
          <LoadCard
            nodeId={nodeId}
            range={historyRange.range}
            window={historyRange.window}
          />
          <SplitMultiMetricCards
            nodeId={nodeId}
            metrics={[
              {
                metric: 'filesystem.used_bytes',
                name: t('monitoring.used'),
                paletteId: 'series-1',
              },
              {
                metric: 'filesystem.total_bytes',
                name: t('monitoring.total'),
                paletteId: 'series-13',
              },
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
                paletteId: 'series-1',
              },
              {
                metric: 'disk.write_bps',
                name: 'Write',
                paletteId: 'series-12',
              },
            ]}
            title={t('monitoring.diskIo')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={formatBps}
            testId='metric-card-disk-io'
            dimensionKey='device'
            wholeDiskOnly
            deviceKinds={classified}
          />
          <SplitMultiMetricCards
            nodeId={nodeId}
            metrics={[
              {
                metric: 'disk.read_iops',
                name: 'Read',
                paletteId: 'series-1',
              },
              {
                metric: 'disk.write_iops',
                name: 'Write',
                paletteId: 'series-12',
              },
            ]}
            title={t('monitoring.diskIops')}
            range={historyRange.range}
            window={historyRange.window}
            formatter={(value) => value.toFixed(2)}
            testId='metric-card-disk-iops'
            dimensionKey='device'
            wholeDiskOnly
            deviceKinds={classified}
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
                alert: 'danger',
              },
              {
                metric: 'network.tx_errors',
                name: 'TX errors',
                alert: 'danger',
              },
              {
                metric: 'network.rx_dropped',
                name: 'RX dropped',
                alert: 'warning',
              },
              {
                metric: 'network.tx_dropped',
                name: 'TX dropped',
                alert: 'warning',
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
            paletteId='series-15'
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
            paletteId='series-16'
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
