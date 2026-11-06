export type ChartTheme = 'dark' | 'light'

export type ChartPaletteId =
  `series-${1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 | 15 | 16}`
export type ChartAlertPaletteId =
  | 'alert-danger'
  | 'alert-warning'
  | 'alert-info'

export type ChartPalette = {
  id: ChartPaletteId
  name: string
  dark: { from: string; to: string }
  light: { from: string; to: string }
}

const SERIES_NAMES = [
  '冷蓝',
  '天青',
  '青绿',
  '靛蓝',
  '湖蓝',
  '蓝灰',
  '石板灰',
  '钢蓝',
  '青灰',
  '长春花',
  '雾蓝',
  '石墨灰',
  '浅板岩',
  '冰蓝',
  '低饱和陶土',
  '低饱和天青',
]

export const CHART_PALETTES: readonly ChartPalette[] = SERIES_NAMES.map(
  (name, index) => {
    const id = `series-${index + 1}` as ChartPaletteId
    const from = `var(--chart-series-${index + 1}-from)`
    const to = `var(--chart-series-${index + 1}-to)`
    return {
      id,
      name,
      dark: { from, to },
      light: { from, to },
    }
  }
)

export type ChartAreaOpacity = {
  top: number
  middle: number
  bottom: number
}

export type ChartStyleTokens = {
  lineWidth: number
  area: Record<ChartTheme, ChartAreaOpacity>
  glow: { width: number; opacity: number }
  legend: { color: 'series' }
  tooltip: { color: 'series' }
  hover: { color: 'series' }
}

export const CHART_STYLE_TOKENS: ChartStyleTokens = {
  lineWidth: 1.8,
  area: {
    dark: { top: 0.34, middle: 0.16, bottom: 0 },
    light: { top: 0.26, middle: 0.11, bottom: 0 },
  },
  glow: { width: 4, opacity: 0.06 },
  legend: { color: 'series' },
  tooltip: { color: 'series' },
  hover: { color: 'series' },
}

export type ChartSeriesStyle = {
  paletteId: ChartPaletteId | ChartAlertPaletteId
  from: string
  to: string
  lineWidth: number
  area: ChartAreaOpacity
  glow: { width: number; opacity: number }
}

export type ChartSeriesStyleOptions = {
  paletteId?: ChartPaletteId
}

export function hashChartSeriesId(seriesId: string) {
  let hash = 0x811c9dc5
  for (let index = 0; index < seriesId.length; index++) {
    hash ^= seriesId.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return hash >>> 0
}

export function getChartSeriesStyle(
  _seriesId: string,
  theme: ChartTheme,
  options: ChartSeriesStyleOptions = {}
): ChartSeriesStyle {
  const explicit = options.paletteId
    ? CHART_PALETTES.find((palette) => palette.id === options.paletteId)
    : undefined
  // Unknown/no explicit identity stays neutral; never fall into alert colors.
  // Unknown/no explicit identity stays neutral gray; never fall into alert colors.
  const palette = explicit ?? CHART_PALETTES[12]
  const colors = palette[theme]
  return {
    paletteId: palette.id,
    from: colors.from,
    to: colors.to,
    lineWidth: CHART_STYLE_TOKENS.lineWidth,
    area: CHART_STYLE_TOKENS.area[theme],
    glow: CHART_STYLE_TOKENS.glow,
  }
}

export function assignChartPaletteIds(
  seriesIds: readonly string[],
  explicit: Partial<Record<string, ChartPaletteId>> = {}
): Record<string, ChartPaletteId> {
  const ids = [...new Set(seriesIds)].sort()
  const used = new Set<ChartPaletteId>()
  const assignments: Record<string, ChartPaletteId> = {}
  const paletteCount = CHART_PALETTES.length

  const findFree = (start: number) => {
    for (let offset = 0; offset < paletteCount; offset++) {
      const candidate = CHART_PALETTES[(start + offset) % paletteCount].id
      if (!used.has(candidate)) return candidate
    }
    return undefined
  }

  const pendingExplicit: string[] = []
  for (const id of ids) {
    const desired = explicit[id]
    if (!desired) continue
    if (!used.has(desired)) {
      assignments[id] = desired
      used.add(desired)
    } else {
      pendingExplicit.push(id)
    }
  }

  for (const id of pendingExplicit) {
    const desired = explicit[id]!
    const start = Math.max(
      0,
      CHART_PALETTES.findIndex((palette) => palette.id === desired)
    )
    const candidate = findFree(start)
    assignments[id] = candidate ?? desired
    used.add(assignments[id])
  }

  for (const id of ids) {
    if (assignments[id]) continue
    const start = hashChartSeriesId(id) % paletteCount
    const candidate = findFree(start)
    assignments[id] = candidate ?? CHART_PALETTES[start].id
    used.add(assignments[id])
  }

  return assignments
}
