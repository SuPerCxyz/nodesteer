import { describe, expect, it } from 'vitest'
import {
  CHART_PALETTES,
  CHART_STYLE_TOKENS,
  assignChartPaletteIds,
  getChartSeriesStyle,
  hashChartSeriesId,
} from './chart-palette'

const EXPECTED_PALETTES = [
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
]

describe('chart palette', () => {
  it('包含 16 组精确的双色渐变', () => {
    expect(CHART_PALETTES).toEqual(EXPECTED_PALETTES)
  })

  it('深色和浅色主题使用同一个 paletteId', () => {
    for (const palette of CHART_PALETTES) {
      const dark = getChartSeriesStyle('series', 'dark', {
        paletteId: palette.id,
      })
      const light = getChartSeriesStyle('series', 'light', {
        paletteId: palette.id,
      })
      expect(dark.paletteId).toBe(palette.id)
      expect(light.paletteId).toBe(palette.id)
      expect(dark.from).toBe(palette.dark.from)
      expect(dark.to).toBe(palette.dark.to)
      expect(light.from).toBe(palette.light.from)
      expect(light.to).toBe(palette.light.to)
    }
  })

  it('未指定 paletteId 时按 FNV-1a 稳定取色', () => {
    const first = getChartSeriesStyle('cpu.usage', 'dark')
    const second = getChartSeriesStyle('cpu.usage', 'dark')
    expect(second).toEqual(first)
    expect(first.paletteId).toBe(
      CHART_PALETTES[hashChartSeriesId('cpu.usage') % CHART_PALETTES.length].id
    )
  })

  it('显式 paletteId 优先', () => {
    const style = getChartSeriesStyle('cpu.usage', 'light', {
      paletteId: 'grape-purple',
    })
    expect(style.paletteId).toBe('grape-purple')
    expect(style.from).toBe('#934DC4')
    expect(style.to).toBe('#CD4C99')
  })

  it('线宽、面积透明度和光晕使用统一 token', () => {
    expect(CHART_STYLE_TOKENS.lineWidth).toBe(1.8)
    expect(CHART_STYLE_TOKENS.glow).toEqual({ width: 4, opacity: 0.06 })
    expect(CHART_STYLE_TOKENS.area.dark).toEqual({
      top: 0.34,
      middle: 0.16,
      bottom: 0,
    })
    expect(CHART_STYLE_TOKENS.area.light).toEqual({
      top: 0.26,
      middle: 0.11,
      bottom: 0,
    })
    expect(
      getChartSeriesStyle('series', 'dark', { paletteId: 'cyan-blue' })
    ).toMatchObject({
      lineWidth: 1.8,
      area: { top: 0.34, middle: 0.16, bottom: 0 },
      glow: { width: 4, opacity: 0.06 },
    })
    expect(
      getChartSeriesStyle('series', 'light', { paletteId: 'cyan-blue' })
    ).toMatchObject({
      lineWidth: 1.8,
      area: { top: 0.26, middle: 0.11, bottom: 0 },
      glow: { width: 4, opacity: 0.06 },
    })
  })

  it('同一图表的分配不随顺序变化', () => {
    const ids = ['cpu.user', 'cpu.system', 'cpu.idle', 'cpu.iowait']
    const first = assignChartPaletteIds(ids)
    const reversed = assignChartPaletteIds([...ids].reverse())
    expect(reversed).toEqual(first)
    expect(new Set(Object.values(first)).size).toBe(ids.length)
  })

  it('显式 paletteId 优先于自动分配', () => {
    const coralIndex = CHART_PALETTES.findIndex(
      (palette) => palette.id === 'coral'
    )
    let conflicting = ''
    for (let index = 0; index < 10000; index++) {
      const candidate = `auto-${index}`
      if (hashChartSeriesId(candidate) % CHART_PALETTES.length === coralIndex) {
        conflicting = candidate
        break
      }
    }
    expect(conflicting).not.toBe('')
    const result = assignChartPaletteIds([conflicting, 'explicit'], {
      explicit: 'coral',
    })
    expect(result.explicit).toBe('coral')
    expect(result[conflicting]).not.toBe('coral')
    expect(new Set(Object.values(result)).size).toBe(2)
  })

  it('显式冲突时按排序后的 seriesId 确定性探测', () => {
    const first = assignChartPaletteIds(['a', 'b'], {
      a: 'coral',
      b: 'coral',
    })
    const second = assignChartPaletteIds(['b', 'a'], {
      a: 'coral',
      b: 'coral',
    })
    expect(second).toEqual(first)
    expect(first.a).toBe('coral')
    expect(first.b).not.toBe('coral')
    expect(new Set(Object.values(first)).size).toBe(2)
  })

  it.each([6, 12, 16])('%i 条曲线分配不同调色板', (count) => {
    const ids = Array.from({ length: count }, (_, index) => `series-${index}`)
    const result = assignChartPaletteIds(ids)
    expect(Object.keys(result)).toHaveLength(count)
    expect(new Set(Object.values(result)).size).toBe(count)
  })

  it('超过 16 条曲线时确定性复用色板', () => {
    const ids = Array.from({ length: 20 }, (_, index) => `series-${index}`)
    const result = assignChartPaletteIds(ids)
    expect(Object.keys(result)).toHaveLength(20)
    expect(new Set(Object.values(result)).size).toBe(16)
  })
})
