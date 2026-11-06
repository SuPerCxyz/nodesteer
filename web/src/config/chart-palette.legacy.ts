// Legacy snapshot kept only for document/rollback reference. Not used by normal hashing.
export type ChartTheme = 'dark' | 'light'

export type ChartPalette = {
  id: string
  name: string
  dark: { from: string; to: string }
  light: { from: string; to: string }
}

export const CHART_PALETTES = [
  {
    id: 'cyan-blue',
    name: '青蓝',
    dark: { from: '#2DF3D8', to: '#37B8FF' },
    light: { from: '#00A7A5', to: '#147ACD' },
  },
  {
    id: 'blue-violet',
    name: '蓝紫',
    dark: { from: '#509FFF', to: '#A26CFF' },
    light: { from: '#2169D1', to: '#7045C4' },
  },
  {
    id: 'coral',
    name: '珊瑚',
    dark: { from: '#FF688E', to: '#FFA466' },
    light: { from: '#D84B72', to: '#C56B24' },
  },
  {
    id: 'spring-green',
    name: '青绿',
    dark: { from: '#67F0A8', to: '#21D5AC' },
    light: { from: '#179A69', to: '#078D80' },
  },
  {
    id: 'gold-orange',
    name: '金橙',
    dark: { from: '#FFBA53', to: '#FFE071' },
    light: { from: '#B8780E', to: '#A68A08' },
  },
  {
    id: 'violet-pink',
    name: '紫粉',
    dark: { from: '#AE7AFF', to: '#F17BEE' },
    light: { from: '#8348C2', to: '#BA42AF' },
  },
  {
    id: 'lime',
    name: '柠檬绿',
    dark: { from: '#D0F46C', to: '#8FE96D' },
    light: { from: '#759F1D', to: '#3C9B43' },
  },
  {
    id: 'peach-pink',
    name: '桃粉',
    dark: { from: '#FF80C7', to: '#FF9A97' },
    light: { from: '#D54495', to: '#C75A65' },
  },
  {
    id: 'lake-blue',
    name: '湖蓝',
    dark: { from: '#3BE8ED', to: '#3C98FF' },
    light: { from: '#008FA9', to: '#2B6FCD' },
  },
  {
    id: 'indigo-violet',
    name: '靛紫',
    dark: { from: '#8083FF', to: '#C186FF' },
    light: { from: '#5557CD', to: '#9551C2' },
  },
  {
    id: 'magenta',
    name: '玫红',
    dark: { from: '#FF6FA7', to: '#EB5FCE' },
    light: { from: '#C73370', to: '#AC399F' },
  },
  {
    id: 'mint',
    name: '薄荷',
    dark: { from: '#83F5C3', to: '#48D6D1' },
    light: { from: '#149E7F', to: '#23969E' },
  },
  {
    id: 'vermilion',
    name: '朱红',
    dark: { from: '#FF7C66', to: '#FFBE69' },
    light: { from: '#C94B35', to: '#C2862D' },
  },
  {
    id: 'yellow-green',
    name: '黄绿',
    dark: { from: '#B8F566', to: '#65DE8C' },
    light: { from: '#709E22', to: '#1B995C' },
  },
  {
    id: 'ice-blue',
    name: '冰蓝',
    dark: { from: '#8EDDFB', to: '#6CB5FF' },
    light: { from: '#338EBE', to: '#467DCB' },
  },
  {
    id: 'grape-purple',
    name: '葡萄紫',
    dark: { from: '#CB8BFF', to: '#FF85CC' },
    light: { from: '#934DC4', to: '#CD4C99' },
  },
] as const satisfies readonly ChartPalette[]

export type ChartPaletteId = (typeof CHART_PALETTES)[number]['id']

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
  paletteId: ChartPaletteId
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
  seriesId: string,
  theme: ChartTheme,
  options: ChartSeriesStyleOptions = {}
): ChartSeriesStyle {
  const explicitIndex = options.paletteId
    ? CHART_PALETTES.findIndex((palette) => palette.id === options.paletteId)
    : -1
  const palette =
    explicitIndex >= 0
      ? CHART_PALETTES[explicitIndex]
      : CHART_PALETTES[hashChartSeriesId(seriesId) % CHART_PALETTES.length]
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
