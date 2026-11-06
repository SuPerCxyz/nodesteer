import { describe, expect, it } from 'vitest'
import { getChartAlertStyle } from './chart-alert'
import {
  CHART_PALETTES,
  CHART_STYLE_TOKENS,
  assignChartPaletteIds,
  getChartSeriesStyle,
} from './chart-palette'

describe('chart palette', () => {
  it('exposes series-1..16 as CSS-variable gradients without hex', () => {
    expect(CHART_PALETTES.map((palette) => palette.id)).toEqual(
      Array.from({ length: 16 }, (_, index) => `series-${index + 1}`)
    )
    for (const palette of CHART_PALETTES) {
      for (const theme of ['light', 'dark'] as const) {
        expect(palette[theme].from).toMatch(/^var\(--chart-series-\d+-from\)$/)
        expect(palette[theme].to).toMatch(/^var\(--chart-series-\d+-to\)$/)
      }
    }
    expect(JSON.stringify(CHART_PALETTES)).not.toMatch(/#[0-9a-f]{3,8}/i)
  })

  it('does not mix alert colors into the normal series palette', () => {
    const all = JSON.stringify(CHART_PALETTES)
    expect(all).not.toContain('alert-')
    expect(all).not.toContain('danger')
    expect(all).not.toContain('warning')
  })

  it('uses alert tokens only through the dedicated alert entry', () => {
    const danger = getChartAlertStyle('danger', 'dark')
    expect(danger.paletteId).toBe('alert-danger')
    expect(danger.from).toBe('var(--chart-alert-danger-from)')
    expect(danger.to).toBe('var(--chart-alert-danger-to)')
    expect(
      CHART_PALETTES.some((palette) => palette.id === danger.paletteId)
    ).toBe(false)
  })

  it('unknown series falls back to neutral series-13', () => {
    const style = getChartSeriesStyle('unknown.metric', 'dark')
    expect(style.paletteId).toBe('series-13')
    expect(style.from).toBe('var(--chart-series-13-from)')
  })

  it('explicit paletteId is deterministic across themes', () => {
    const dark = getChartSeriesStyle('x', 'dark', { paletteId: 'series-4' })
    const light = getChartSeriesStyle('x', 'light', { paletteId: 'series-4' })
    expect(dark.paletteId).toBe('series-4')
    expect(light.paletteId).toBe('series-4')
    expect(dark.from).toBe('var(--chart-series-4-from)')
    expect(light.to).toBe('var(--chart-series-4-to)')
  })

  it('keeps style tokens and deterministic allocation', () => {
    expect(CHART_STYLE_TOKENS.lineWidth).toBe(1.8)
    expect(CHART_STYLE_TOKENS.area.dark).toEqual({
      top: 0.34,
      middle: 0.16,
      bottom: 0,
    })
    const ids = ['cpu.user', 'cpu.system', 'cpu.iowait']
    expect(assignChartPaletteIds([...ids].reverse())).toEqual(
      assignChartPaletteIds(ids)
    )
    const explicit = assignChartPaletteIds(['a', 'b'], {
      a: 'series-1',
      b: 'series-1',
    })
    expect(explicit.a).toBe('series-1')
    expect(explicit.b).not.toBe('series-1')
    expect(new Set(Object.values(explicit)).size).toBe(2)
  })
})
