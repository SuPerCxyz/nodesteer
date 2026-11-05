import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import {
  assignChartPaletteIds,
  getChartSeriesStyle,
  type ChartPaletteId,
  type ChartSeriesStyle,
} from '@/config/chart-palette'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { useTheme } from '@/context/theme-provider'
import { historyPointValid } from './history'
import type { History } from './types'

export type ChartSeries = {
  key: string
  name: string
  points: History['points']
  unit?: string
  type?: 'area' | 'line'
  gaps?: History['gaps']
  stepNs?: number
  paletteId?: ChartPaletteId
  seriesId?: string
}

export type ChartPoint = { x: number; y: number }

const DEFAULT_WIDTH = 800
const PADDING = { top: 14, right: 12, bottom: 26, left: 44 }

type PreparedSeries = ChartSeries & {
  seriesId: string
  style: ChartSeriesStyle
  type: 'area' | 'line'
  rawSegments: { timestamp: number; value: number }[][]
  rawSortedPoints: { timestamp: number; value: number }[]
  segments: { timestamp: number; value: number }[][]
  sortedPoints: { timestamp: number; value: number }[]
  latest: number | null
}

export function downsampleSegment(
  points: { timestamp: number; value: number }[],
  maxPoints: number
) {
  if (maxPoints < 4 || points.length <= maxPoints) return points
  const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 2))
  const bucketSize = (points.length - 2) / bucketCount
  const selected = new Set<number>([0, points.length - 1])
  for (let bucket = 0; bucket < bucketCount; bucket++) {
    const start = 1 + Math.floor(bucket * bucketSize)
    const end = Math.min(
      points.length - 1,
      1 + Math.floor((bucket + 1) * bucketSize)
    )
    if (start >= end) continue
    let minIndex = start
    let maxIndex = start
    for (let index = start + 1; index < end; index++) {
      if (points[index].value < points[minIndex].value) minIndex = index
      if (points[index].value > points[maxIndex].value) maxIndex = index
    }
    selected.add(minIndex)
    selected.add(maxIndex)
  }
  return [...selected].sort((a, b) => a - b).map((index) => points[index])
}

export function downsampleStackSegments(
  segments: { time: number; values: number[] }[][],
  maxPoints: number
) {
  return segments.map((segment) => {
    if (segment.length <= maxPoints) return segment
    const bucketCount = Math.max(1, Math.floor((maxPoints - 2) / 2))
    const bucketSize = (segment.length - 2) / bucketCount
    const selected = new Set<number>([0, segment.length - 1])
    for (let bucket = 0; bucket < bucketCount; bucket++) {
      const start = 1 + Math.floor(bucket * bucketSize)
      const end = Math.min(
        segment.length - 1,
        1 + Math.floor((bucket + 1) * bucketSize)
      )
      if (start >= end) continue
      let minIndex = start
      let maxIndex = start
      for (let index = start + 1; index < end; index++) {
        const total = segment[index].values.reduce(
          (sum, value) => sum + value,
          0
        )
        const minTotal = segment[minIndex].values.reduce(
          (sum, value) => sum + value,
          0
        )
        const maxTotal = segment[maxIndex].values.reduce(
          (sum, value) => sum + value,
          0
        )
        if (total < minTotal) minIndex = index
        if (total > maxTotal) maxIndex = index
      }
      selected.add(minIndex)
      selected.add(maxIndex)
    }
    return [...selected].sort((a, b) => a - b).map((index) => segment[index])
  })
}

export function maxPointsForWidth(width: number) {
  const safeWidth = Number.isFinite(width) ? width : 320
  return Math.min(120, Math.max(40, Math.round(safeWidth / 10)))
}

function nearestValue(
  points: { timestamp: number; value: number }[],
  time: number,
  tolerance: number
) {
  if (!points.length) return undefined
  let low = 0
  let high = points.length - 1
  while (low <= high) {
    const middle = Math.floor((low + high) / 2)
    if (points[middle].timestamp < time) low = middle + 1
    else high = middle - 1
  }
  let best: { timestamp: number; value: number } | undefined
  for (const candidate of [points[low], points[low - 1]]) {
    if (
      candidate &&
      (!best ||
        Math.abs(candidate.timestamp - time) < Math.abs(best.timestamp - time))
    ) {
      best = candidate
    }
  }
  return best && Math.abs(best.timestamp - time) <= tolerance
    ? best.value
    : undefined
}

function splitSegments(
  points: History['points'],
  gaps: History['gaps'] = [],
  stepNs?: number
) {
  const valid = points
    .filter(historyPointValid)
    .map((point) => ({
      timestamp: Date.parse(point.timestamp),
      value: point.value as number,
    }))
    .filter((point) => Number.isFinite(point.timestamp))
    .sort((a, b) => a.timestamp - b.timestamp)
  const gapRanges = gaps
    .map((gap) => [Date.parse(gap.start), Date.parse(gap.end)])
    .filter(([start, end]) => Number.isFinite(start) && Number.isFinite(end))
  const stepMs = stepNs ? stepNs / 1_000_000 : 0
  const segments: { timestamp: number; value: number }[][] = []
  let current: { timestamp: number; value: number }[] = []
  for (const point of valid) {
    const previous = current[current.length - 1]
    const crossedGap =
      previous &&
      gapRanges.some(
        ([start, end]) => start < point.timestamp && end > previous.timestamp
      )
    const tooFar =
      previous &&
      stepMs > 0 &&
      point.timestamp - previous.timestamp > stepMs * 1.5
    if (previous && (crossedGap || tooFar)) {
      if (current.length) segments.push(current)
      current = []
    }
    current.push(point)
  }
  if (current.length) segments.push(current)
  return segments
}

function formatPoint(point: ChartPoint) {
  return `${point.x.toFixed(2)} ${point.y.toFixed(2)}`
}

function monotoneTangents(points: ChartPoint[]) {
  const count = points.length
  if (count < 2) return new Array<number>(count).fill(0)

  const slopes: number[] = []
  for (let index = 0; index < count - 1; index++) {
    const dx = points[index + 1].x - points[index].x
    slopes.push(dx === 0 ? 0 : (points[index + 1].y - points[index].y) / dx)
  }

  const tangents = new Array<number>(count).fill(0)
  tangents[0] = slopes[0]
  tangents[count - 1] = slopes[count - 2]

  for (let index = 1; index < count - 1; index++) {
    const previous = slopes[index - 1]
    const next = slopes[index]
    if (previous === 0 || next === 0 || previous * next <= 0) {
      tangents[index] = 0
      continue
    }
    const previousSpan = points[index].x - points[index - 1].x
    const nextSpan = points[index + 1].x - points[index].x
    if (previousSpan <= 0 || nextSpan <= 0) {
      tangents[index] = 0
      continue
    }
    const firstWeight = 2 * nextSpan + previousSpan
    const secondWeight = nextSpan + 2 * previousSpan
    tangents[index] =
      (firstWeight + secondWeight) /
      (firstWeight / previous + secondWeight / next)
  }

  for (let index = 0; index < count - 1; index++) {
    const slope = slopes[index]
    if (slope === 0) {
      tangents[index] = 0
      tangents[index + 1] = 0
      continue
    }
    let alpha = tangents[index] / slope
    let beta = tangents[index + 1] / slope
    if (alpha < 0) {
      tangents[index] = 0
      alpha = 0
    }
    if (beta < 0) {
      tangents[index + 1] = 0
      beta = 0
    }
    if (alpha * alpha + beta * beta > 9) {
      const scale = 3 / Math.sqrt(alpha * alpha + beta * beta)
      tangents[index] = scale * alpha * slope
      tangents[index + 1] = scale * beta * slope
    }
  }

  return tangents
}

export function monotoneCubicSegments(points: ChartPoint[]) {
  if (points.length < 2) return []
  const tangents = monotoneTangents(points)
  return points.slice(0, -1).map((point, index) => {
    const next = points[index + 1]
    const dx = next.x - point.x
    return {
      from: point,
      control1: {
        x: point.x + dx / 3,
        y: point.y + (tangents[index] * dx) / 3,
      },
      control2: {
        x: next.x - dx / 3,
        y: next.y - (tangents[index + 1] * dx) / 3,
      },
      to: next,
    }
  })
}

function smoothPath(points: ChartPoint[]) {
  if (!points.length) return ''
  return [
    `M ${formatPoint(points[0])}`,
    ...monotoneCubicSegments(points).map(
      (segment) =>
        `C ${formatPoint(segment.control1)} ${formatPoint(segment.control2)} ${formatPoint(segment.to)}`
    ),
  ].join(' ')
}

function reversedSmoothCommands(points: ChartPoint[]) {
  return monotoneCubicSegments(points)
    .slice()
    .reverse()
    .map(
      (segment) =>
        `C ${formatPoint(segment.control2)} ${formatPoint(segment.control1)} ${formatPoint(segment.from)}`
    )
    .join(' ')
}

function areaPath(upper: ChartPoint[], lower: ChartPoint[]) {
  if (!upper.length) return ''
  const top = smoothPath(upper)
  if (!lower.length) return `${top} Z`
  const bottomStart = lower[lower.length - 1]
  const bottom = reversedSmoothCommands(lower)
  return `${top} L ${formatPoint(bottomStart)}${bottom ? ` ${bottom}` : ''} Z`
}

export function MultiSeriesChart({
  series,
  height = 220,
  yDomain = 'auto',
  unit,
  showLegend = true,
  stacked = false,
  onSeriesToggle,
  onChartClick,
  formatValue,
  className,
  ariaLabel,
}: {
  series: ChartSeries[]
  height?: number
  yDomain?: [number, number] | 'auto'
  unit?: string
  showLegend?: boolean
  stacked?: boolean
  onSeriesToggle?: (key: string, visible: boolean) => void
  onChartClick?: () => void
  formatValue?: (value: number) => string | null
  className?: string
  ariaLabel?: string
}) {
  const { t } = useTranslation()
  const { resolvedTheme } = useTheme()
  const isDark = resolvedTheme === 'dark'
  const theme = isDark ? 'dark' : 'light'
  const rawId = useId().replace(/[:]/g, '')
  const svgRef = useRef<SVGSVGElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [chartWidth, setChartWidth] = useState(() =>
    typeof window === 'undefined'
      ? DEFAULT_WIDTH
      : Math.max(320, window.innerWidth)
  )
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [hover, setHover] = useState<{
    x: number
    y: number
    time: number
  } | null>(null)

  const paletteAssignments = useMemo(() => {
    const explicit: Partial<Record<string, ChartPaletteId>> = {}
    for (const item of series) {
      if (item.paletteId) {
        explicit[item.seriesId || item.key] = item.paletteId
      }
    }
    return assignChartPaletteIds(
      series.map((item) => item.seriesId || item.key),
      explicit
    )
  }, [series])

  const maxPoints = maxPointsForWidth(chartWidth)
  const prepared = useMemo<PreparedSeries[]>(
    () =>
      series.map((item) => {
        const seriesId = item.seriesId || item.key
        const style = getChartSeriesStyle(seriesId, theme, {
          paletteId: paletteAssignments[seriesId],
        })
        const rawSegments = splitSegments(item.points, item.gaps, item.stepNs)
        const rawSortedPoints = rawSegments
          .flat()
          .sort((a, b) => a.timestamp - b.timestamp)
        const latest =
          rawSortedPoints[rawSortedPoints.length - 1]?.value ?? null
        const segments = rawSegments.map((segment) =>
          downsampleSegment(segment, maxPoints)
        )
        const sortedPoints = segments
          .flat()
          .sort((a, b) => a.timestamp - b.timestamp)
        return {
          ...item,
          seriesId,
          style,
          type: item.type || 'area',
          rawSegments,
          rawSortedPoints,
          segments,
          sortedPoints,
          latest,
        }
      }),
    [maxPoints, paletteAssignments, series, theme]
  )

  const visible = prepared.filter((item) => !hidden.has(item.key))
  const visiblePoints = visible.flatMap((item) =>
    item.segments.flatMap((segment) => segment)
  )
  const timestamps = [
    ...new Set(visiblePoints.map((point) => point.timestamp)),
  ].sort((a, b) => a - b)

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const measure = () => {
      const width = element.getBoundingClientRect().width
      if (width > 0) {
        const next = Math.max(320, Math.round(width))
        setChartWidth((previous) =>
          Math.abs(previous - next) < 1 ? previous : next
        )
      }
    }
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [timestamps.length])

  if (!timestamps.length) return null

  const minTime = timestamps[0]
  const maxTime = timestamps[timestamps.length - 1]
  const timeSpan = Math.max(1, maxTime - minTime)
  const plotWidth = chartWidth - PADDING.left - PADDING.right
  const plotHeight = height - PADDING.top - PADDING.bottom

  const xScale = (time: number) =>
    PADDING.left + ((time - minTime) / timeSpan) * plotWidth
  const yScale = (value: number) =>
    PADDING.top +
    plotHeight -
    ((value - domainMin) / (domainMax - domainMin || 1)) * plotHeight

  const areaSeries = visible.filter((item) => item.type === 'area')
  const maxStepMs = Math.max(
    ...areaSeries.map((item) => (item.stepNs ? item.stepNs / 1_000_000 : 0)),
    0
  )
  const tolerance = Math.min(5000, Math.max(1000, maxStepMs / 2))
  const grid = [
    ...new Set(
      areaSeries.flatMap((item) =>
        item.rawSortedPoints.map((point) => point.timestamp)
      )
    ),
  ].sort((a, b) => a - b)
  const stackSegments: { time: number; values: number[] }[][] = []
  if (stacked) {
    let current: { time: number; values: number[] }[] = []
    const breakMs = Math.max(tolerance, maxStepMs * 1.5)
    for (const time of grid) {
      const values = areaSeries.map((item) =>
        nearestValue(item.rawSortedPoints, time, tolerance)
      )
      if (values.some((value) => value === undefined)) {
        if (current.length) stackSegments.push(current)
        current = []
        continue
      }
      if (current.length && time - current[current.length - 1].time > breakMs) {
        stackSegments.push(current)
        current = []
      }
      current.push({ time, values: values as number[] })
    }
    if (current.length) stackSegments.push(current)
  }
  const displayStackSegments = downsampleStackSegments(stackSegments, maxPoints)
  const autoValues = stacked
    ? displayStackSegments.flatMap((segment) =>
        segment.map((point) =>
          point.values.reduce((total, value) => total + value, 0)
        )
      )
    : visiblePoints.map((point) => point.value)
  const autoMax = Math.max(...autoValues, 0)
  const autoMin = Math.min(...autoValues, 0)
  const domainMin = yDomain === 'auto' ? autoMin : yDomain[0]
  const domainMax =
    yDomain === 'auto'
      ? autoMax === autoMin
        ? autoMax + 1
        : autoMax + (autoMax - autoMin) * 0.1
      : yDomain[1]
  const byteIndex =
    unit === 'B' && autoMax > 0
      ? Math.min(5, Math.max(0, Math.floor(Math.log(autoMax) / Math.log(1024))))
      : 0
  const byteUnit = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'][byteIndex]
  const fmtBytes = (value: number) =>
    `${(value / 1024 ** byteIndex).toFixed(byteIndex ? 1 : 0)} ${byteUnit}`
  const fmt = (value: number) => {
    const formatted =
      unit === 'B'
        ? fmtBytes(value)
        : formatValue
          ? formatValue(value)
          : unit
            ? `${value.toFixed(1)}${unit}`
            : value.toFixed(2)
    return formatted ?? '—'
  }
  const axisFmt = (value: number) => {
    if (unit === 'B') return fmtBytes(value)
    if (formatValue) return formatValue(value) ?? value.toFixed(1)
    if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
    if (Number.isInteger(value)) return String(value)
    return value.toFixed(1)
  }

  const timeLabel = (time: number) => {
    const date = new Date(time)
    return timeSpan > 24 * 60 * 60 * 1000
      ? date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })
      : date.toLocaleTimeString(undefined, {
          hour: '2-digit',
          minute: '2-digit',
        })
  }

  const yTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
    const value = domainMin + (domainMax - domainMin) * fraction
    return { value, y: yScale(value) }
  })
  const xTickCount = chartWidth < 520 ? 3 : chartWidth < 900 ? 4 : 5
  const xTicks = Array.from({ length: xTickCount }, (_, index) => {
    const time = minTime + (timeSpan * index) / (xTickCount - 1)
    return { time, x: xScale(time) }
  })

  const clipId = `clip-${rawId}-${isDark ? 'dark' : 'light'}`
  const baseline = yScale(domainMin)
  const hoverTime = hover?.time
  const hoverX = hoverTime === undefined ? 0 : xScale(hoverTime)
  const allStepMs = Math.max(
    ...visible.map((item) => (item.stepNs ? item.stepNs / 1_000_000 : 0)),
    0
  )
  const hoverTolerance = Math.min(5000, Math.max(1000, allStepMs / 2))
  const hoverValues =
    hoverTime === undefined
      ? []
      : visible.map((item) => ({
          ...item,
          value: nearestValue(item.sortedPoints, hoverTime, hoverTolerance),
        }))

  const updateHover = (event: ReactPointerEvent<SVGRectElement>) => {
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    const x = ((event.clientX - rect.left) / rect.width) * chartWidth
    const y = ((event.clientY - rect.top) / rect.height) * height
    const clamped = Math.min(
      PADDING.left + plotWidth,
      Math.max(PADDING.left, x)
    )
    const time = minTime + ((clamped - PADDING.left) / plotWidth) * timeSpan
    let nearest = timestamps[0]
    for (const candidate of timestamps) {
      if (Math.abs(candidate - time) < Math.abs(nearest - time))
        nearest = candidate
    }
    setHover({ x: xScale(nearest), y, time: nearest })
  }

  const toggle = (key: string) => {
    setHidden((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      onSeriesToggle?.(key, !next.has(key))
      return next
    })
  }

  return (
    <div
      ref={containerRef}
      data-testid='multi-series-chart'
      data-theme={isDark ? 'dark' : 'light'}
      className={cn('relative w-full min-w-0', className)}
    >
      {showLegend && (
        <div className='mb-2 flex flex-wrap gap-x-3 gap-y-1 text-xs'>
          {prepared.map((item) => (
            <button
              key={item.key}
              type='button'
              data-series-key={item.key}
              data-series-id={item.seriesId}
              data-palette-id={item.style.paletteId}
              onClick={() => toggle(item.key)}
              className={cn(
                'flex max-w-full min-w-0 items-center gap-1.5 text-start text-muted-foreground transition-opacity hover:text-foreground',
                hidden.has(item.key) && 'line-through opacity-40'
              )}
              aria-pressed={!hidden.has(item.key)}
            >
              <span
                className='h-1.5 w-4 shrink-0 rounded-full'
                style={{
                  background: `linear-gradient(90deg, ${item.style.from}, ${item.style.to})`,
                }}
              />
              <span className='min-w-0 break-all'>{item.name}</span>
              <span className='text-foreground tabular-nums'>
                {item.latest === null ? '—' : fmt(item.latest)}
              </span>
            </button>
          ))}
        </div>
      )}
      <svg
        ref={svgRef}
        role='img'
        aria-label={ariaLabel || t('monitoring.chart')}
        viewBox={`0 0 ${chartWidth} ${height}`}
        width='100%'
        className='w-full'
        style={{ height }}
      >
        <defs>
          <clipPath id={clipId}>
            <rect
              x={PADDING.left}
              y={PADDING.top}
              width={plotWidth}
              height={plotHeight}
            />
          </clipPath>
          {prepared.map((item, index) => {
            const strokeId = `stroke-${rawId}-${isDark ? 'dark' : 'light'}-${index}`
            const fillId = `fill-${rawId}-${isDark ? 'dark' : 'light'}-${index}`
            return (
              <g key={item.key}>
                <linearGradient id={strokeId} x1='0' y1='0' x2='1' y2='0'>
                  <stop offset='0' stopColor={item.style.from} />
                  <stop offset='1' stopColor={item.style.to} />
                </linearGradient>
                <linearGradient id={fillId} x1='0' y1='0' x2='0' y2='1'>
                  <stop
                    offset='0'
                    stopColor={item.style.from}
                    stopOpacity={item.style.area.top}
                  />
                  <stop
                    offset='0.55'
                    stopColor={item.style.to}
                    stopOpacity={item.style.area.middle}
                  />
                  <stop
                    offset='1'
                    stopColor={item.style.to}
                    stopOpacity={item.style.area.bottom}
                  />
                </linearGradient>
              </g>
            )
          })}
        </defs>
        <g className='text-border'>
          {yTicks.map((tick) => (
            <line
              key={tick.value}
              x1={PADDING.left}
              x2={chartWidth - PADDING.right}
              y1={tick.y}
              y2={tick.y}
              stroke='currentColor'
              strokeOpacity='0.55'
            />
          ))}
          {xTicks.map((tick) => (
            <line
              key={tick.time}
              x1={tick.x}
              x2={tick.x}
              y1={PADDING.top}
              y2={height - PADDING.bottom}
              stroke='currentColor'
              strokeOpacity='0.25'
            />
          ))}
        </g>
        <g className='fill-muted-foreground text-[10px]'>
          {yTicks.map((tick) => (
            <text
              key={tick.value}
              x={PADDING.left - 6}
              y={tick.y + 3}
              textAnchor='end'
            >
              {axisFmt(tick.value)}
            </text>
          ))}
          {xTicks.map((tick) => (
            <text key={tick.time} x={tick.x} y={height - 8} textAnchor='middle'>
              {timeLabel(tick.time)}
            </text>
          ))}
        </g>
        <g clipPath={`url(#${clipId})`}>
          {stacked ? (
            <>
              {areaSeries.map((item, layerIndex) => {
                const segmentPaths: {
                  area: string
                  line: string
                  single?: ChartPoint
                }[] = []
                let current: {
                  upper: ChartPoint
                  lower: ChartPoint
                }[] = []
                const flush = () => {
                  if (!current.length) return
                  if (current.length === 1) {
                    segmentPaths.push({
                      area: '',
                      line: '',
                      single: current[0].upper,
                    })
                  } else {
                    segmentPaths.push({
                      area: areaPath(
                        current.map((point) => point.upper),
                        current.map((point) => point.lower)
                      ),
                      line: smoothPath(current.map((point) => point.upper)),
                    })
                  }
                  current = []
                }
                for (const segment of displayStackSegments) {
                  for (const point of segment) {
                    const lower = point.values
                      .slice(0, layerIndex)
                      .reduce((total, value) => total + value, 0)
                    const upper = lower + (point.values[layerIndex] || 0)
                    current.push({
                      upper: { x: xScale(point.time), y: yScale(upper) },
                      lower: { x: xScale(point.time), y: yScale(lower) },
                    })
                  }
                  flush()
                }
                return segmentPaths.map((path, index) => (
                  <g
                    key={`${item.key}-${index}`}
                    data-series-key={item.key}
                    data-series-id={item.seriesId}
                    data-palette-id={item.style.paletteId}
                  >
                    {path.single ? (
                      <circle
                        data-testid='chart-point'
                        cx={path.single.x}
                        cy={path.single.y}
                        r='3'
                        fill={item.style.from}
                        stroke='var(--background)'
                        strokeWidth='1.5'
                      />
                    ) : (
                      <>
                        <path
                          data-testid='chart-area'
                          d={path.area}
                          fill={`url(#fill-${rawId}-${isDark ? 'dark' : 'light'}-${prepared.indexOf(item)})`}
                        />
                        {isDark && (
                          <path
                            data-testid='chart-glow'
                            d={path.line}
                            fill='none'
                            stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${prepared.indexOf(item)})`}
                            strokeWidth={item.style.glow.width}
                            strokeOpacity={item.style.glow.opacity}
                          />
                        )}
                        <path
                          data-testid='chart-line'
                          d={path.line}
                          fill='none'
                          stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${prepared.indexOf(item)})`}
                          strokeWidth={item.style.lineWidth}
                        />
                      </>
                    )}
                  </g>
                ))
              })}
              {visible
                .filter((item) => item.type === 'line')
                .map((item) => {
                  const index = prepared.indexOf(item)
                  return item.segments.map((segment, segmentIndex) => {
                    const points = segment.map((point) => ({
                      x: xScale(point.timestamp),
                      y: yScale(point.value),
                    }))
                    const single = points.length === 1
                    const line = single ? '' : smoothPath(points)
                    return (
                      <g
                        key={`line-${item.key}-${segmentIndex}`}
                        data-series-key={item.key}
                        data-series-id={item.seriesId}
                        data-palette-id={item.style.paletteId}
                      >
                        {single ? (
                          <circle
                            data-testid='chart-point'
                            cx={points[0].x}
                            cy={points[0].y}
                            r='3'
                            fill={item.style.from}
                            stroke='var(--background)'
                            strokeWidth='1.5'
                          />
                        ) : (
                          <>
                            {isDark && (
                              <path
                                data-testid='chart-glow'
                                d={line}
                                fill='none'
                                stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${index})`}
                                strokeWidth={item.style.glow.width}
                                strokeOpacity={item.style.glow.opacity}
                              />
                            )}
                            <path
                              data-testid='chart-line'
                              d={line}
                              fill='none'
                              stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${index})`}
                              strokeWidth={item.style.lineWidth}
                            />
                          </>
                        )}
                      </g>
                    )
                  })
                })}
            </>
          ) : (
            visible.map((item) => {
              const index = prepared.indexOf(item)
              return item.segments.map((segment, segmentIndex) => {
                const points = segment.map((point) => ({
                  x: xScale(point.timestamp),
                  y: yScale(point.value),
                }))
                const single = points.length === 1
                const line = single ? '' : smoothPath(points)
                const area =
                  item.type === 'area' && !single
                    ? areaPath(points, [
                        { x: points[0].x, y: baseline },
                        { x: points[points.length - 1].x, y: baseline },
                      ])
                    : ''
                return (
                  <g
                    key={`${item.key}-${segmentIndex}`}
                    data-series-key={item.key}
                    data-series-id={item.seriesId}
                    data-palette-id={item.style.paletteId}
                  >
                    {single ? (
                      <circle
                        data-testid='chart-point'
                        cx={points[0].x}
                        cy={points[0].y}
                        r='3'
                        fill={item.style.from}
                        stroke='var(--background)'
                        strokeWidth='1.5'
                      />
                    ) : (
                      <>
                        {item.type === 'area' && (
                          <path
                            data-testid='chart-area'
                            d={area}
                            fill={`url(#fill-${rawId}-${isDark ? 'dark' : 'light'}-${index})`}
                          />
                        )}
                        {isDark && (
                          <path
                            data-testid='chart-glow'
                            d={line}
                            fill='none'
                            stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${index})`}
                            strokeWidth={item.style.glow.width}
                            strokeOpacity={item.style.glow.opacity}
                          />
                        )}
                        <path
                          data-testid='chart-line'
                          d={line}
                          fill='none'
                          stroke={`url(#stroke-${rawId}-${isDark ? 'dark' : 'light'}-${index})`}
                          strokeWidth={item.style.lineWidth}
                        />
                      </>
                    )}
                  </g>
                )
              })
            })
          )}
          {hoverTime !== undefined && (
            <line
              x1={hoverX}
              x2={hoverX}
              y1={PADDING.top}
              y2={height - PADDING.bottom}
              stroke='currentColor'
              strokeDasharray='3 3'
              className='text-muted-foreground'
            />
          )}
          {hoverTime !== undefined &&
            hoverValues.map((item) => {
              if (item.value === undefined) return null
              return (
                <circle
                  key={item.key}
                  data-testid='chart-hover-point'
                  data-palette-id={item.style.paletteId}
                  cx={hoverX}
                  cy={yScale(item.value)}
                  r='3'
                  fill={item.style.from}
                  stroke='var(--background)'
                  strokeWidth='1.5'
                />
              )
            })}
        </g>
        <rect
          data-testid='chart-overlay'
          x={PADDING.left}
          y={PADDING.top}
          width={plotWidth}
          height={plotHeight}
          fill='transparent'
          onPointerMove={updateHover}
          onPointerDown={updateHover}
          onPointerLeave={() => setHover(null)}
          onClick={() => onChartClick?.()}
        />
      </svg>
      {hover && (
        <div
          data-testid='chart-tooltip'
          className='pointer-events-none absolute z-10 min-w-44 rounded-md border bg-popover p-2 text-xs text-popover-foreground shadow-md'
          style={{
            left: `${(hover.x / chartWidth) * 100}%`,
            top: `${(hover.y / height) * 100}%`,
            transform: `translate(${hover.x > chartWidth * 0.65 ? '-100%' : '0'}, ${hover.y > height * 0.6 ? '-100%' : '0'})`,
          }}
        >
          <div className='mb-1 text-muted-foreground'>
            {new Date(hover.time).toLocaleString()}
          </div>
          <div className='space-y-1'>
            {hoverValues.map((item) => (
              <div
                key={item.key}
                className='flex items-center justify-between gap-3'
              >
                <span className='flex items-center gap-1.5'>
                  <span
                    className='h-1.5 w-3 rounded-full'
                    data-testid='chart-tooltip-swatch'
                    style={{
                      background: `linear-gradient(90deg, ${item.style.from}, ${item.style.to})`,
                    }}
                  />
                  {item.name}
                </span>
                <span className='tabular-nums'>
                  {item.value === undefined ? '—' : fmt(item.value)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
