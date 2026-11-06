import '@/styles/index.css'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  PALETTE_MODE,
  applyPaletteMode,
  type PaletteMode,
} from './palette-mode'

const root = document.documentElement
const originalClass = root.className
const originalPalette = root.dataset.palette

/** Resolve a CSS custom property through the cascade to a concrete color. */
function resolveToken(token: string) {
  const probe = document.createElement('span')
  probe.style.color = `var(${token})`
  document.body.append(probe)
  const color = getComputedStyle(probe).color
  probe.remove()
  return color
}

function resolveRgb(token: string) {
  const match = resolveToken(token).match(/\d+/g)
  if (!match || match.length < 3) {
    throw new Error(`Cannot resolve ${token}: ${resolveToken(token)}`)
  }
  return match.slice(0, 3).map(Number)
}

function luminance([red, green, blue]: number[]) {
  const channels = [red, green, blue]
    .map((value) => value / 255)
    .map((value) =>
      value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4
    )
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrast(foreground: string, background: string) {
  const first = luminance(resolveRgb(foreground))
  const second = luminance(resolveRgb(background))
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05)
}

function setDark(dark: boolean) {
  root.classList.toggle('dark', dark)
  root.classList.toggle('light', !dark)
}

function selectPalette(mode: PaletteMode) {
  applyPaletteMode(root, mode)
}

beforeEach(() => {
  setDark(false)
  selectPalette('azure')
})

afterEach(() => {
  root.className = originalClass
  if (originalPalette === undefined) {
    delete root.dataset.palette
  } else {
    root.dataset.palette = originalPalette
  }
})

describe('调色板模式', () => {
  it('默认启用 azure，并加载 A 浅色主色与界面底色', () => {
    expect(PALETTE_MODE).toBe('azure')
    applyPaletteMode(root)
    expect(root.dataset.palette).toBe('azure')
    expect(resolveToken('--primary')).toBe('rgb(37, 99, 235)')
    expect(resolveToken('--background')).toBe('rgb(244, 248, 255)')
    expect(resolveToken('--card')).toBe('rgb(255, 255, 255)')
    expect(resolveToken('--foreground')).toBe('rgb(15, 27, 43)')
    expect(resolveToken('--muted-foreground')).toBe('rgb(91, 107, 128)')
    expect(resolveToken('--text-faint')).toBe('rgb(95, 112, 132)')
  })

  it('azure / spectrum / new / legacy 分别恢复对应浅色主色', () => {
    const expected: [PaletteMode, string][] = [
      ['azure', 'rgb(37, 99, 235)'],
      ['spectrum', 'rgb(59, 111, 245)'],
      ['new', 'rgb(0, 95, 251)'],
      ['legacy', 'rgb(30, 41, 59)'],
    ]
    for (const [mode, primary] of expected) {
      selectPalette(mode)
      expect(resolveToken('--primary')).toBe(primary)
    }
    selectPalette('azure')
    expect(resolveToken('--background')).toBe('rgb(244, 248, 255)')
    selectPalette('spectrum')
    expect(resolveToken('--background')).toBe('rgb(241, 245, 251)')
    selectPalette('new')
    expect(resolveToken('--background')).toBe('rgb(255, 255, 255)')
    selectPalette('legacy')
    expect(resolveToken('--background')).toBe('rgb(255, 255, 255)')
  })

  it('深色模式下四种调色板主色分别生效', () => {
    setDark(true)
    const expected: [PaletteMode, string][] = [
      ['azure', 'rgb(96, 165, 250)'],
      ['spectrum', 'rgb(122, 162, 255)'],
      ['new', 'rgb(91, 155, 255)'],
      ['legacy', 'rgb(226, 232, 240)'],
    ]
    for (const [mode, primary] of expected) {
      selectPalette(mode)
      expect(resolveToken('--primary')).toBe(primary)
    }
    selectPalette('azure')
    expect(resolveToken('--background')).toBe('rgb(8, 17, 31)')
    selectPalette('spectrum')
    expect(resolveToken('--background')).toBe('rgb(10, 17, 28)')
    selectPalette('legacy')
    expect(resolveToken('--background')).toBe('rgb(15, 23, 42)')
  })

  it('azure 普通系列与 alert 完全分离，Swap(s7) 不是灰也不是告警色', () => {
    for (const dark of [false, true]) {
      setDark(dark)
      selectPalette('azure')
      const alerts = new Set(
        [
          '--chart-alert-danger-from',
          '--chart-alert-danger-to',
          '--chart-alert-warning-from',
          '--chart-alert-warning-to',
          '--chart-alert-info-from',
          '--chart-alert-info-to',
        ].map(resolveToken)
      )

      for (let index = 1; index <= 16; index++) {
        expect(alerts.has(resolveToken(`--chart-series-${index}-from`))).toBe(
          false
        )
        expect(alerts.has(resolveToken(`--chart-series-${index}-to`))).toBe(
          false
        )
      }

      const swap = [
        resolveToken('--chart-series-7-from'),
        resolveToken('--chart-series-7-to'),
      ]
      const neutral = [
        resolveToken('--chart-series-13-from'),
        resolveToken('--chart-series-13-to'),
      ]
      expect(swap.some((color) => neutral.includes(color))).toBe(false)
      expect(swap.some((color) => alerts.has(color))).toBe(false)
    }
  })

  it('保留 spectrum 快照：alert-info 与 series-1 同色', () => {
    selectPalette('spectrum')
    expect(resolveToken('--chart-alert-info-from')).toBe(
      resolveToken('--chart-series-1-from')
    )
  })

  it('usage-warning 在明暗主题均为 A 淡黄，强档为深一级', () => {
    for (const dark of [false, true]) {
      setDark(dark)
      selectPalette('azure')
      expect(resolveToken('--usage-warning')).toBe('rgb(253, 230, 138)')
      expect(resolveToken('--usage-warning-strong')).toBe('rgb(252, 211, 77)')
    }
  })

  it('azure 明暗主题关键文字与 tooltip 满足 AA 4.5:1', () => {
    const pairs: [string, string][] = [
      ['--foreground', '--background'],
      ['--muted-foreground', '--background'],
      ['--text-faint', '--background'],
      ['--primary-foreground', '--primary'],
      ['--secondary-foreground', '--secondary'],
      ['--accent-foreground', '--accent'],
      ['--success-foreground', '--success'],
      ['--warning-foreground', '--warning'],
      ['--danger-foreground', '--danger'],
      ['--info-foreground', '--info'],
      ['--tooltip-foreground', '--tooltip'],
      ['--tooltip-muted', '--tooltip'],
    ]
    for (const dark of [false, true]) {
      setDark(dark)
      selectPalette('azure')
      for (const [foreground, background] of pairs) {
        expect(
          contrast(foreground, background),
          `${dark ? 'dark' : 'light'} ${foreground} on ${background}`
        ).toBeGreaterThanOrEqual(4.5)
      }
    }
  })
})
