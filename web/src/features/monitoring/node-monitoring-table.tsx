import {
  Fragment,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
  type MouseEvent,
} from 'react'
import {
  ArrowDown,
  ArrowUp,
  ChevronRight,
  Clock3,
  Gauge,
  Network,
  Thermometer,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { statusLabel } from '@/lib/status-labels'
import { RackList, RackRow, RackTags } from '@/features/shared/rack-list'
import { EmptyState, ErrorState, LoadingState } from '@/features/shared/ui'
import { formatBps, formatLoad, percentTone } from './metric-format'
import {
  NodeStatusDot,
  formatTemperature,
  formatUptime,
} from './node-overview-metrics'
import type { NodeOverview } from './types'

function navigateRow(event: MouseEvent<HTMLDivElement>, href: string) {
  const target = event.target as HTMLElement
  if (
    target.closest(
      'a,button,input,select,textarea,[role="button"],[contenteditable="true"]'
    ) ||
    window.getSelection()?.toString()
  )
    return
  if (event.button === 1 || event.ctrlKey || event.metaKey) {
    window.open(href, '_blank', 'noopener,noreferrer')
  } else if (event.button === 0 && !event.shiftKey && !event.altKey) {
    window.location.assign(href)
  }
}

const TAG_GAP = 6
const TAG_SAFETY = 2
const DEFAULT_TAG_COLUMNS = '43px 127px 43px 92px 92px 43px 57px'
const DEFAULT_TAG_WIDTH =
  43 + 127 + 43 + 92 + 92 + 43 + 57 + TAG_GAP * 6 + TAG_SAFETY

type NodeTagRow = {
  id: string
  load: string
  rx: string
  tx: string
  temperature: string
}

type TagLabels = {
  load: string
  network: string
  temperature: string
  networkRx: string
  networkTx: string
}

type TagLayout = {
  columns: string
  width: number
}

function percentValue(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(1)}%`
    : '—'
}

function measureTagLayout(root: HTMLElement): TagLayout {
  const keys = [
    'load-label',
    'load-value',
    'network-label',
    'rx',
    'tx',
    'temperature-label',
    'temperature-value',
  ] as const
  const widths = keys.map((key) => {
    const nodes = root.querySelectorAll(`[data-measure="${key}"]`)
    let max = 0
    for (const node of nodes) {
      max = Math.max(max, node.getBoundingClientRect().width)
    }
    return Math.ceil(max) + 1
  })
  return {
    columns: widths.map((width) => `${width}px`).join(' '),
    width:
      widths.reduce((total, width) => total + width, 0) +
      TAG_GAP * (keys.length - 1) +
      TAG_SAFETY,
  }
}

function TagMeasureLayer({
  measureRef,
  rows,
  labels,
}: {
  measureRef: RefObject<HTMLDivElement | null>
  rows: NodeTagRow[]
  labels: TagLabels
}) {
  return (
    <div
      ref={measureRef}
      aria-hidden='true'
      className='pointer-events-none absolute opacity-0'
      style={{ left: -10000, top: 0, whiteSpace: 'nowrap' }}
    >
      <span
        data-measure='load-label'
        className='inline-flex items-center gap-1 text-[11px] text-muted-foreground'
      >
        <Gauge className='size-3' aria-hidden='true' />
        {labels.load}
      </span>
      <span
        data-measure='network-label'
        className='inline-flex items-center gap-1 text-[11px] text-muted-foreground'
      >
        <Network className='size-3' aria-hidden='true' />
        {labels.network}
      </span>
      <span
        data-measure='temperature-label'
        className='inline-flex items-center gap-1 text-[11px] text-muted-foreground'
      >
        <Thermometer className='size-3' aria-hidden='true' />
        {labels.temperature}
      </span>
      {/* 预留常见最大格式宽度：避免 30s 刷新后数值变长/温度从 — 变成读数时，
          标签列宽变化把左侧字段和进度条整体推走。 */}
      <span
        data-measure='load-value'
        className='text-[11px] font-medium tabular-nums'
      >
        9.99 / 9.99 / 9.99
      </span>
      <span
        data-measure='rx'
        className='inline-flex items-center gap-0.5 text-[11px] font-medium tabular-nums'
      >
        <ArrowDown className='size-3' aria-hidden='true' />
        1023.9 KB/s
      </span>
      <span
        data-measure='tx'
        className='inline-flex items-center gap-0.5 text-[11px] font-medium tabular-nums'
      >
        <ArrowUp className='size-3' aria-hidden='true' />
        1023.9 KB/s
      </span>
      <span
        data-measure='temperature-value'
        className='text-[11px] font-medium tabular-nums'
      >
        100.0 °C
      </span>
      {rows.map((row) => (
        <Fragment key={row.id}>
          <span
            data-measure='load-value'
            className='text-[11px] font-medium tabular-nums'
          >
            {row.load}
          </span>
          <span
            data-measure='rx'
            className='inline-flex items-center gap-0.5 text-[11px] font-medium tabular-nums'
          >
            <ArrowDown className='size-3' aria-hidden='true' />
            {row.rx}
          </span>
          <span
            data-measure='tx'
            className='inline-flex items-center gap-0.5 text-[11px] font-medium tabular-nums'
          >
            <ArrowUp className='size-3' aria-hidden='true' />
            {row.tx}
          </span>
          <span
            data-measure='temperature-value'
            className='text-[11px] font-medium tabular-nums'
          >
            {row.temperature}
          </span>
        </Fragment>
      ))}
    </div>
  )
}

function NodeOverviewTags({
  row,
  labels,
}: {
  row: NodeTagRow
  labels: TagLabels
}) {
  const fallbackTags = [
    {
      label: labels.load,
      value: row.load,
      icon: <Gauge className='size-3' aria-hidden='true' />,
    },
    {
      label: labels.network,
      value: (
        <span className='inline-flex items-center gap-2'>
          <span
            className='inline-flex items-center gap-0.5'
            title={labels.networkRx}
          >
            <ArrowDown className='size-3' aria-hidden='true' />
            {row.rx}
          </span>
          <span
            className='inline-flex items-center gap-0.5'
            title={labels.networkTx}
          >
            <ArrowUp className='size-3' aria-hidden='true' />
            {row.tx}
          </span>
        </span>
      ),
      icon: <Network className='size-3' aria-hidden='true' />,
    },
    {
      label: labels.temperature,
      value: row.temperature,
      icon: <Thermometer className='size-3' aria-hidden='true' />,
    },
  ]
  return (
    <>
      <div
        data-testid={`node-tags-${row.id}`}
        data-tag-grid='true'
        className='hidden items-center gap-x-1.5 lg:grid'
        style={{
          gridTemplateColumns: `var(--rack-tag-columns, ${DEFAULT_TAG_COLUMNS})`,
        }}
      >
        <span
          data-tag-column='load-label'
          className='inline-flex min-w-0 items-center gap-1 overflow-hidden text-[11px] whitespace-nowrap text-muted-foreground'
        >
          <Gauge className='size-3 shrink-0' aria-hidden='true' />
          <span className='truncate'>{labels.load}</span>
        </span>
        <span
          data-tag-column='load-value'
          className='min-w-0 truncate text-[11px] font-medium text-foreground tabular-nums'
          title={row.load}
        >
          {row.load}
        </span>
        <span
          data-tag-column='network-label'
          className='inline-flex min-w-0 items-center gap-1 overflow-hidden text-[11px] whitespace-nowrap text-muted-foreground'
        >
          <Network className='size-3 shrink-0' aria-hidden='true' />
          <span className='truncate'>{labels.network}</span>
        </span>
        <span
          data-tag-column='network-rx'
          className='inline-flex min-w-0 items-center gap-0.5 overflow-hidden text-[11px] font-medium text-foreground tabular-nums'
          title={`${labels.networkRx}: ${row.rx}`}
        >
          <ArrowDown className='size-3 shrink-0' aria-hidden='true' />
          <span className='truncate'>{row.rx}</span>
        </span>
        <span
          data-tag-column='network-tx'
          className='inline-flex min-w-0 items-center gap-0.5 overflow-hidden text-[11px] font-medium text-foreground tabular-nums'
          title={`${labels.networkTx}: ${row.tx}`}
        >
          <ArrowUp className='size-3 shrink-0' aria-hidden='true' />
          <span className='truncate'>{row.tx}</span>
        </span>
        <span
          data-tag-column='temperature-label'
          className='inline-flex min-w-0 items-center gap-1 overflow-hidden text-[11px] whitespace-nowrap text-muted-foreground'
        >
          <Thermometer className='size-3 shrink-0' aria-hidden='true' />
          <span className='truncate'>{labels.temperature}</span>
        </span>
        <span
          data-tag-column='temperature-value'
          className='min-w-0 truncate text-[11px] font-medium text-foreground tabular-nums'
        >
          {row.temperature}
        </span>
      </div>
      {/* 窄屏保留流式标签，避免 7 个固定子列在手机宽度下溢出。 */}
      <div className='lg:hidden'>
        <RackTags items={fallbackTags} />
      </div>
    </>
  )
}

export function NodeMonitoringTable({
  nodes,
  overviewByNode,
  loading,
  error,
  onRetry,
  rowNavigation = false,
}: {
  nodes: Node[]
  overviewByNode: Map<string, NodeOverview>
  loading: boolean
  error: unknown
  onRetry: () => void
  rowNavigation?: boolean
}) {
  const { t } = useTranslation()
  const measureRef = useRef<HTMLDivElement>(null)
  const [tagLayout, setTagLayout] = useState<TagLayout>(() => ({
    columns: DEFAULT_TAG_COLUMNS,
    width: DEFAULT_TAG_WIDTH,
  }))
  const labels: TagLabels = {
    load: t('dashboard.load'),
    network: t('dashboard.network'),
    temperature: t('dashboard.temperature'),
    networkRx: t('monitoring.networkRx'),
    networkTx: t('monitoring.networkTx'),
  }
  const tagRows = nodes.map((node) => {
    const row = overviewByNode.get(node.id)
    return {
      id: node.id,
      load: `${formatLoad(row?.load1) ?? '—'} / ${
        formatLoad(row?.load5) ?? '—'
      } / ${formatLoad(row?.load15) ?? '—'}`,
      rx: formatBps(row?.network_rx_bps) ?? '—',
      tx: formatBps(row?.network_tx_bps) ?? '—',
      temperature: formatTemperature(row?.temperature_c),
    }
  })

  useLayoutEffect(() => {
    const root = measureRef.current
    if (!root) return
    const update = () => {
      const next = measureTagLayout(root)
      setTagLayout((previous) =>
        previous.columns === next.columns && previous.width === next.width
          ? previous
          : next
      )
    }
    update()
    if (typeof document !== 'undefined' && document.fonts?.ready) {
      void document.fonts.ready.then(update)
    }
  }, [nodes, overviewByNode, t])

  if (!nodes.length) {
    return <EmptyState message={t('dashboard.noMonitoringData')} />
  }
  if (loading) return <LoadingState />
  if (error) return <ErrorState error={error} onRetry={onRetry} />

  const tagStyle = {
    '--rack-tags-width': `${tagLayout.width}px`,
    '--rack-tag-columns': tagLayout.columns,
  } as CSSProperties

  return (
    <>
      <TagMeasureLayer measureRef={measureRef} rows={tagRows} labels={labels} />
      <RackList testId='node-monitoring-rack' style={tagStyle}>
        {nodes.map((node, index) => {
          const row = overviewByNode.get(node.id)
          const cpu = row?.cpu
          const memory = row?.memory
          const disk = row?.disk
          return (
            <div
              key={node.id}
              role='group'
              tabIndex={rowNavigation ? 0 : undefined}
              aria-label={nodeDisplayName(node, t('common.unknownNode'))}
              className='rounded-md focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring'
              onClick={(event) =>
                rowNavigation &&
                navigateRow(
                  event,
                  `/monitoring/nodes/${encodeURIComponent(node.id)}`
                )
              }
              onAuxClick={(event) => {
                if (rowNavigation && event.button === 1)
                  navigateRow(
                    event,
                    `/monitoring/nodes/${encodeURIComponent(node.id)}`
                  )
              }}
              onKeyDown={(event) => {
                if (
                  rowNavigation &&
                  event.target === event.currentTarget &&
                  event.key === 'Enter'
                )
                  window.location.assign(
                    `/monitoring/nodes/${encodeURIComponent(node.id)}`
                  )
              }}
            >
              <RackRow
                testId={`node-monitoring-row-${node.id}`}
                layout='node'
                leading={<NodeStatusDot status={node.status} />}
                title={
                  <a
                    href={`/monitoring/nodes/${node.id}`}
                    className='hover:underline'
                  >
                    {nodeDisplayName(node, t('common.unknownNode'))}
                  </a>
                }
                subtitle={
                  <>
                    <span>{statusLabel(node.status)}</span>
                    <span aria-hidden='true'>·</span>
                    <Clock3 className='size-3 shrink-0' aria-hidden='true' />
                    <span>{formatUptime(row?.uptime_seconds, t)}</span>
                  </>
                }
                fields={[
                  {
                    label: 'CPU',
                    value: percentValue(cpu),
                    tone: percentTone(cpu),
                    testId: `dashboard-cpu-${node.id}`,
                    bar: {
                      value: cpu,
                      tone: percentTone(cpu),
                      testId: `dashboard-cpu-${node.id}-bar`,
                    },
                  },
                  {
                    label: t('monitoring.memory'),
                    value: percentValue(memory),
                    tone: percentTone(memory),
                    testId: `dashboard-memory-${node.id}`,
                    bar: {
                      value: memory,
                      tone: percentTone(memory),
                      testId: `dashboard-memory-${node.id}-bar`,
                    },
                  },
                  {
                    label: t('monitoring.disk'),
                    value: percentValue(disk),
                    tone: percentTone(disk),
                    testId: `dashboard-disk-${node.id}`,
                    bar: {
                      value: disk,
                      tone: percentTone(disk),
                      testId: `dashboard-disk-${node.id}-bar`,
                    },
                  },
                ]}
                tagsContent={
                  <NodeOverviewTags row={tagRows[index]} labels={labels} />
                }
                actions={
                  <a
                    href={`/monitoring/nodes/${node.id}`}
                    className='inline-flex h-6 items-center gap-0.5 rounded-md px-1.5 text-[11px] text-muted-foreground hover:border hover:bg-card hover:text-foreground'
                  >
                    {t('dashboard.viewDetails')}
                    <ChevronRight className='size-3.5' aria-hidden='true' />
                  </a>
                }
              />
            </div>
          )
        })}
      </RackList>
    </>
  )
}
