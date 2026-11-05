export type HistoryPoint = {
  timestamp: string
  value: number | null
  quality?: number
  count?: number
}
export type HistoryGap = { start: string; end: string }

// Raw samples are valid at quality 1. Stored rollup buckets omit quality and
// carry count > 0, while empty buckets have a null value and count 0.
export function historyPointValid(
  point: HistoryPoint
): point is HistoryPoint & { value: number } {
  if (point.value === null || !Number.isFinite(point.value)) return false
  if (!Number.isFinite(Date.parse(point.timestamp))) return false
  return (
    point.quality === 1 ||
    ((point.quality === 0 || point.quality === undefined) &&
      (point.count ?? 0) > 0)
  )
}

// Missing values, reported gaps and absent sampling intervals break lines.
export function historySegments(
  points: HistoryPoint[],
  gaps: HistoryGap[],
  stepNs: number
): string[][] {
  const valid = points.filter(historyPointValid)
  if (!valid.length) return []
  const values = valid.map((point) => point.value)
  const times = valid.map((point) => Date.parse(point.timestamp))
  const min = values.reduce((a, b) => Math.min(a, b), Infinity)
  const max = values.reduce((a, b) => Math.max(a, b), -Infinity)
  const start = times.reduce((a, b) => Math.min(a, b), Infinity)
  const end = times.reduce((a, b) => Math.max(a, b), -Infinity)
  const segments: string[][] = [[]]
  let previous = Number.NaN
  for (const point of points) {
    if (!historyPointValid(point)) {
      if (segments[segments.length - 1].length) segments.push([])
      previous = Number.NaN
      continue
    }
    const time = Date.parse(point.timestamp)
    if (
      Number.isFinite(previous) &&
      ((stepNs > 0 && time - previous > (stepNs / 1_000_000) * 1.5) ||
        (gaps || []).some(
          (gap) =>
            previous < Date.parse(gap.end) && time > Date.parse(gap.start)
        ))
    ) {
      if (segments[segments.length - 1].length) segments.push([])
    }
    const x = 8 + ((time - start) / (end - start || 1)) * 584
    const y = 192 - ((point.value - min) / (max - min || 1)) * 184
    segments[segments.length - 1].push(`${x},${y}`)
    previous = time
  }
  return segments.filter((segment) => segment.length)
}
