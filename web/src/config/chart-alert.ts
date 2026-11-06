import {
  CHART_STYLE_TOKENS,
  type ChartAlertPaletteId,
  type ChartSeriesStyle,
  type ChartTheme,
} from './chart-palette'

export type ChartAlertTone = 'danger' | 'warning' | 'info'

/** Alert chart colors are intentionally available only through this entry. */
export function getChartAlertStyle(
  tone: ChartAlertTone,
  theme: ChartTheme
): ChartSeriesStyle {
  return {
    paletteId: `alert-${tone}` as ChartAlertPaletteId,
    from: `var(--chart-alert-${tone}-from)`,
    to: `var(--chart-alert-${tone}-to)`,
    lineWidth: CHART_STYLE_TOKENS.lineWidth,
    area: CHART_STYLE_TOKENS.area[theme],
    glow: CHART_STYLE_TOKENS.glow,
  }
}
